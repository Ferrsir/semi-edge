"""Strict offline CSV fixture ingestion; no vendor provenance or order capability."""

from __future__ import annotations

import csv
import math
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from semiedge.data.schema import REQUIRED_BAR_COLUMNS, SESSION_INDEX_NAME, validate_bar_frame

_HEADER = (SESSION_INDEX_NAME, *REQUIRED_BAR_COLUMNS)
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
MIN_FIXTURE_SESSION = date(1990, 1, 1)


class CsvImportError(ValueError):
    """Input cannot be safely interpreted as a daily-bar fixture."""


def _parse_row(row: list[str], header: list[str], line: int) -> tuple[date, list[float]]:
    if len(row) != len(header):
        raise CsvImportError(f"CSV row {line}: wrong field count")
    cells = dict(zip(header, row, strict=True))
    raw_session = cells[SESSION_INDEX_NAME]
    if not _DATE.fullmatch(raw_session):
        raise CsvImportError(f"CSV row {line}: session must be YYYY-MM-DD")
    try:
        session = date.fromisoformat(raw_session)
    except ValueError:
        raise CsvImportError(f"CSV row {line}: invalid session date") from None
    values = []
    for column in REQUIRED_BAR_COLUMNS:
        raw = cells[column]
        if not _NUMBER.fullmatch(raw):
            raise CsvImportError(f"CSV row {line}: invalid {column}")
        number = float(raw)
        if not math.isfinite(number) or number < 0 or (column != "volume" and number == 0):
            raise CsvImportError(f"CSV row {line}: invalid {column}")
        if column == "volume":
            precise = Decimal(raw)
            if precise != precise.to_integral_value() or precise.is_signed() or precise > 2**53:
                raise CsvImportError(f"CSV row {line}: volume must be an exact nonnegative integer")
        values.append(number)
    opening, high, low, close, _volume = values
    if high < max(opening, close, low) or low > min(opening, close, high):
        raise CsvImportError(f"CSV row {line}: inconsistent OHLC prices")
    return session, values


def _read_rows(text: str) -> tuple[list[date], list[list[float]]]:
    sessions: list[date] = []
    values: list[list[float]] = []
    try:
        with StringIO(text.removeprefix("\ufeff"), newline="") as stream:
            reader = csv.reader(stream, strict=True)
            header = next(reader, [])
            if len(header) != len(_HEADER) or set(header) != set(_HEADER):
                raise CsvImportError("CSV must have exactly session,open,high,low,close,volume")
            for row in reader:
                session, numbers = _parse_row(row, header, reader.line_num)
                if sessions and session <= sessions[-1]:
                    raise CsvImportError("CSV sessions must be unique and sorted ascending")
                sessions.append(session)
                values.append(numbers)
    except (OSError, UnicodeError, csv.Error):
        raise CsvImportError("Cannot read CSV fixture") from None
    if not sessions:
        raise CsvImportError("CSV fixture has no bars")
    return sessions, values


def load_csv_bars(
    path: Path, *, symbol: str, as_of: date, now: datetime, settle_minutes: int = 30
) -> pd.DataFrame:
    """Read one UTF-8 fixture snapshot and validate its daily bars."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise CsvImportError("Cannot read CSV fixture") from None
    return parse_csv_bars(text, symbol=symbol, as_of=as_of, now=now, settle_minutes=settle_minutes)


def _utc_clock(as_of: date, now: datetime, settle_minutes: int) -> datetime:
    if type(as_of) is not date:
        raise CsvImportError("as_of must be a date, not a datetime or string")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise CsvImportError("now must be a timezone-aware datetime")
    if type(settle_minutes) is not int or not 0 <= settle_minutes <= 240:
        raise CsvImportError("settle_minutes must be an integer from 0 to 240")
    try:
        return now.astimezone(UTC)
    except (ValueError, OverflowError):
        raise CsvImportError("now is outside the supported UTC datetime range") from None


def parse_csv_bars(
    text: str, *, symbol: str, as_of: date, now: datetime, settle_minutes: int = 30
) -> pd.DataFrame:
    """Validate all rows before selecting completed sessions through ``as_of``.

    ``as_of`` and all rows must be XNYS sessions closed and settled at the injected
    aware ``now``. No hidden clock or network is consulted. Gaps are recorded, not
    filled; all values remain user-provided fixtures, never verified vendor data.
    """
    if type(symbol) is not str or not _SYMBOL.fullmatch(symbol):
        raise CsvImportError("Invalid symbol")
    now = _utc_clock(as_of, now, settle_minutes)
    sessions, values = _read_rows(text)
    if min(sessions[0], as_of) < MIN_FIXTURE_SESSION:
        raise CsvImportError("CSV fixtures support sessions from 1990-01-01 onward")
    if max(sessions[-1], as_of) > now.date():
        raise CsvImportError("CSV session or cutoff is in the future")
    try:
        calendar = xcals.get_calendar(
            "XNYS",
            start=date(min(sessions[0], as_of).year, 1, 1),
            end=date(max(sessions[-1], as_of).year, 12, 31),
        )
        valid = calendar.sessions
        if pd.Timestamp(as_of) not in valid:
            raise CsvImportError("as_of must be an XNYS session")
        index = pd.DatetimeIndex(sessions, dtype="datetime64[ns]", name=SESSION_INDEX_NAME)
        if not index.isin(valid).all():
            raise CsvImportError("CSV contains a non-XNYS session")
        for session in {as_of, sessions[-1]}:
            completed_at = calendar.session_close(pd.Timestamp(session)) + pd.Timedelta(
                minutes=settle_minutes
            )
            if completed_at > pd.Timestamp(now):
                raise CsvImportError("CSV session or cutoff has not closed and settled")
    except (ValueError, OverflowError, TypeError) as exc:
        if isinstance(exc, CsvImportError):
            raise
        raise CsvImportError("CSV session range is unsupported by the exchange calendar") from None
    frame = pd.DataFrame(values, index=index, columns=list(REQUIRED_BAR_COLUMNS), dtype="float64")
    frame = frame.loc[[session <= as_of for session in sessions]].copy()
    if frame.empty:
        raise CsvImportError("No bars at or before the completed-session cutoff")
    validate_bar_frame(frame, symbol=symbol)
    expected = valid[(valid >= frame.index[0]) & (valid <= pd.Timestamp(as_of))]
    frame.attrs["missing_sessions"] = [
        d.date().isoformat() for d in expected.difference(frame.index)
    ]
    return frame
