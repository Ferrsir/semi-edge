"""Strict offline CSV fixture ingestion; no vendor provenance or order capability."""

from __future__ import annotations

import csv
import math
import re
from datetime import date
from io import StringIO
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from semiedge.data.schema import REQUIRED_BAR_COLUMNS, SESSION_INDEX_NAME, validate_bar_frame

_HEADER = (SESSION_INDEX_NAME, *REQUIRED_BAR_COLUMNS)
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


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


def load_csv_bars(path: Path, *, symbol: str, as_of: date) -> pd.DataFrame:
    """Read one UTF-8 fixture snapshot and validate its daily bars."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise CsvImportError("Cannot read CSV fixture") from None
    return parse_csv_bars(text, symbol=symbol, as_of=as_of)


def parse_csv_bars(text: str, *, symbol: str, as_of: date) -> pd.DataFrame:
    """Validate all CSV rows, then retain only sessions through ``as_of``.

    ``as_of`` is an explicit caller-supplied *completed-session cutoff*, not a claim
    of availability at that historical instant. No current clock or network is consulted.
    Missing intervening sessions are not filled; downstream quality checks remain required.
    All imported values are user-provided fixtures, never authenticated market data.
    """
    if type(symbol) is not str or not _SYMBOL.fullmatch(symbol):
        raise CsvImportError("Invalid symbol")
    if type(as_of) is not date:
        raise CsvImportError("as_of must be a date, not a datetime or string")
    sessions, values = _read_rows(text)
    try:
        calendar = xcals.get_calendar(
            "XNYS", start=date(sessions[0].year, 1, 1), end=date(sessions[-1].year, 12, 31)
        )
        valid = calendar.sessions
        index = pd.DatetimeIndex(sessions, dtype="datetime64[ns]", name=SESSION_INDEX_NAME)
        if not index.isin(valid).all():
            raise CsvImportError("CSV contains a non-XNYS session")
    except (ValueError, OverflowError, TypeError) as exc:
        if isinstance(exc, CsvImportError):
            raise
        raise CsvImportError("CSV session range is unsupported by the exchange calendar") from None
    frame = pd.DataFrame(values, index=index, columns=list(REQUIRED_BAR_COLUMNS), dtype="float64")
    frame = frame.loc[[session <= as_of for session in sessions]].copy()
    if frame.empty:
        raise CsvImportError("No bars at or before the completed-session cutoff")
    validate_bar_frame(frame, symbol=symbol)
    return frame
