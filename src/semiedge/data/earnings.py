"""Cross-checked earnings dates: pure types, strict parsers, overrides and resolution (#20).

Spec: ``docs/specifications/earnings-calendar.md``. This module performs no I/O except reading the
override file, never imports a network module and never sees an API key. Fetching and caching
live in :mod:`semiedge.data.earnings_sources`; the risk-layer adapter is
:mod:`semiedge.risk.earnings`.

Fail closed: any doubt about a source makes the resolution ``verified=False``, which R16 turns
into ``earnings_data_unavailable``.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml

from semiedge.config.loader import read_strict_yaml

__all__ = [
    "ALPHAVANTAGE",
    "CLOCK_SKEW_TOLERANCE",
    "FINNHUB",
    "AlphaVantageParseError",
    "EarningsDataError",
    "EarningsEvent",
    "EarningsResolution",
    "FinnhubParseError",
    "ManualOverride",
    "ResolutionPolicy",
    "SourceName",
    "SourceSnapshot",
    "load_overrides",
    "parse_alphavantage_csv",
    "parse_finnhub_json",
    "resolve_earnings",
]

SourceName = Literal["finnhub", "alphavantage"]
FINNHUB: SourceName = "finnhub"
ALPHAVANTAGE: SourceName = "alphavantage"
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


class EarningsDataError(ValueError):
    """Earnings input that cannot be trusted (invalid override file, invalid snapshot)."""


class FinnhubParseError(EarningsDataError):
    """A Finnhub response that does not match the expected shape."""


class AlphaVantageParseError(EarningsDataError):
    """An Alpha Vantage response that is not the expected CSV."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class EarningsEvent:
    symbol: str
    report_date: date
    hour: str | None  # vendor timing hint ("bmo"/"amc"/...); informational only


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """One source's view at ``fetched_at``. ``symbols=None`` means every symbol is covered."""

    source: SourceName
    fetched_at: datetime
    coverage_start: date
    coverage_end: date
    symbols: frozenset[str] | None
    events: tuple[EarningsEvent, ...]
    status: Literal["ok", "error"]
    error: str | None

    def __post_init__(self) -> None:
        if self.source not in (FINNHUB, ALPHAVANTAGE):
            raise EarningsDataError("unknown earnings source")
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise EarningsDataError("fetched_at must be timezone-aware")
        if self.coverage_end < self.coverage_start:
            raise EarningsDataError("coverage_end before coverage_start")
        if self.status not in ("ok", "error"):
            raise EarningsDataError("status must be ok or error")
        if (self.status == "error") != (self.error is not None):
            raise EarningsDataError("error code must be set exactly when status is error")
        if self.error is not None and not _ERROR_CODE.match(self.error):
            raise EarningsDataError("error code must be a short snake_case code")
        for event in self.events:
            if self.symbols is not None and event.symbol not in self.symbols:
                raise EarningsDataError("event for a symbol outside the snapshot's coverage")


@dataclass(frozen=True, slots=True)
class ManualOverride:
    symbol: str
    next_earnings_date: date
    verified_on: date
    source_url: str
    note: str | None


@dataclass(frozen=True, slots=True)
class EarningsResolution:
    symbol: str
    next_date: date | None
    verified: bool
    source: str
    reason: str  # stable snake_case code(s), "+"-joined; "verified" when verified
    detail: str


@dataclass(frozen=True, slots=True)
class ResolutionPolicy:
    max_age: timedelta
    min_forward_coverage: timedelta
    override_max_age: timedelta


# ------------------------------------------------------------------------- parsers


def _iso(value: object, what: str, error: type[EarningsDataError]) -> date:
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        raise error(f"{what} is not an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise error(f"{what} is not a valid date") from None


def parse_finnhub_json(body: bytes, *, symbol: str, start: date, end: date) -> list[EarningsEvent]:
    """Strictly parse one per-symbol Finnhub ``/calendar/earnings`` response."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FinnhubParseError("not JSON") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("earningsCalendar"), list):
        raise FinnhubParseError("earningsCalendar is not a list")
    events: list[EarningsEvent] = []
    for row in payload["earningsCalendar"]:
        if not isinstance(row, dict):
            raise FinnhubParseError("row is not an object")
        if row.get("symbol") != symbol:
            raise FinnhubParseError("row for a different symbol")
        when = _iso(row.get("date"), "date", FinnhubParseError)
        if not start <= when <= end:
            raise FinnhubParseError("date outside the requested window")
        hour = row.get("hour")
        events.append(EarningsEvent(symbol, when, hour if isinstance(hour, str) and hour else None))
    return events


def parse_alphavantage_csv(body: bytes) -> list[EarningsEvent]:
    """Strictly parse Alpha Vantage ``EARNINGS_CALENDAR`` CSV (raises with a fixed code)."""
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise AlphaVantageParseError("parse_error") from None
    stripped = text.lstrip()
    if not stripped:
        raise AlphaVantageParseError("parse_error")
    if stripped.startswith("{"):  # JSON notice: rate limit, invalid key, premium endpoint
        raise AlphaVantageParseError("api_message")
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    if "symbol" not in fields or "reportDate" not in fields:
        raise AlphaVantageParseError("parse_error")
    events: list[EarningsEvent] = []
    for row in reader:
        symbol = (row.get("symbol") or "").strip()
        if not _SYMBOL.match(symbol):
            continue  # vendor lists non-US / odd tickers; they can never match the universe
        try:
            when = _iso((row.get("reportDate") or "").strip(), "reportDate", EarningsDataError)
        except EarningsDataError:
            raise AlphaVantageParseError("parse_error") from None
        hour = (row.get("timeOfTheDay") or "").strip() or None
        events.append(EarningsEvent(symbol, when, hour))
    return events


# ------------------------------------------------------------------------- overrides


_OVERRIDE_FIELDS = {"symbol", "next_earnings_date", "verified_on", "source_url", "note"}
_OVERRIDE_REQUIRED = _OVERRIDE_FIELDS - {"note"}


def _yaml_date(value: object, what: str) -> date:
    if isinstance(value, datetime):
        raise EarningsDataError(f"override {what} must be a date, not a timestamp")
    if isinstance(value, date):
        return value
    return _iso(value, f"override {what}", EarningsDataError)


def _parse_override(entry: object, *, allowed: frozenset[str], today: date) -> ManualOverride:
    if not isinstance(entry, dict) or not all(isinstance(k, str) for k in entry):
        raise EarningsDataError("each override must be a mapping")
    keys = set(entry)
    if keys - _OVERRIDE_FIELDS or _OVERRIDE_REQUIRED - keys:
        raise EarningsDataError("override has unknown or missing fields")
    symbol = entry["symbol"]
    if not isinstance(symbol, str) or symbol not in allowed:
        raise EarningsDataError("override symbol is not a tradable universe symbol")
    verified_on = _yaml_date(entry["verified_on"], "verified_on")
    if verified_on > today:
        raise EarningsDataError(f"override verified_on for {symbol} is in the future")
    url = entry["source_url"]
    if not isinstance(url, str) or urlsplit(url).scheme != "https" or not urlsplit(url).netloc:
        raise EarningsDataError(f"override source_url for {symbol} must be an https URL")
    note = entry.get("note")
    if note is not None and not isinstance(note, str):
        raise EarningsDataError("override note must be text")
    return ManualOverride(
        symbol,
        _yaml_date(entry["next_earnings_date"], "next_earnings_date"),
        verified_on,
        url,
        note,
    )


def load_overrides(
    path: Path, *, universe: Iterable[str], today: date
) -> dict[str, ManualOverride]:
    """Load and strictly validate the manual override file. A missing file means no overrides."""
    if not path.exists():
        return {}
    try:
        raw = read_strict_yaml(path)
    except (ValueError, OSError, UnicodeDecodeError, yaml.YAMLError, TypeError):
        raise EarningsDataError("override file is unreadable") from None
    if not isinstance(raw, dict) or set(raw) != {"overrides"}:
        raise EarningsDataError("override file must have exactly one key: overrides")
    entries = raw["overrides"]
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise EarningsDataError("overrides must be a list")
    allowed = frozenset(universe)
    result: dict[str, ManualOverride] = {}
    for entry in entries:
        override = _parse_override(entry, allowed=allowed, today=today)
        if override.symbol in result:
            raise EarningsDataError(f"duplicate override for {override.symbol}")
        result[override.symbol] = override
    return result


# ------------------------------------------------------------------------- resolution


def _health(  # noqa: PLR0911 - one return per spec §4 rule 2 reason code
    snap: SourceSnapshot | None,
    *,
    name: SourceName,
    symbol: str,
    on_or_after: date,
    now: datetime,
    policy: ResolutionPolicy,
) -> str | None:
    """``None`` if the source is usable for this query, else a reason code."""
    if snap is None:
        return f"{name}_missing"
    if snap.source != name:
        return f"{name}_missing"
    if snap.status != "ok":
        return f"{name}_error_{snap.error}"
    if snap.fetched_at > now + CLOCK_SKEW_TOLERANCE:
        return f"{name}_clock_skew"
    if now - snap.fetched_at > policy.max_age:
        return f"{name}_stale"
    if on_or_after < snap.coverage_start or snap.coverage_end < (
        on_or_after + policy.min_forward_coverage
    ):
        return f"{name}_coverage"
    if snap.symbols is not None and symbol not in snap.symbols:
        return f"{name}_symbol_not_covered"
    return None


def _next(snap: SourceSnapshot, symbol: str, on_or_after: date) -> date | None:
    dates = [
        e.report_date for e in snap.events if e.symbol == symbol and e.report_date >= on_or_after
    ]
    return min(dates) if dates else None


def resolve_earnings(
    symbol: str,
    on_or_after: date,
    *,
    finnhub: SourceSnapshot | None,
    alphavantage: SourceSnapshot | None,
    overrides: Mapping[str, ManualOverride],
    now: datetime,
    policy: ResolutionPolicy,
) -> EarningsResolution:
    """Apply spec §4: fresh override, else both sources healthy and in agreement."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    today = now.date()
    notes: list[str] = []
    override = overrides.get(symbol)
    if override is not None:
        fresh = (
            override.verified_on <= today
            and today - override.verified_on <= policy.override_max_age
            and override.next_earnings_date >= on_or_after
        )
        if fresh:
            return EarningsResolution(
                symbol,
                override.next_earnings_date,
                True,
                f"manual_override@{override.verified_on.isoformat()}",
                "verified",
                f"hand-checked on {override.verified_on.isoformat()}: {override.source_url}",
            )
        notes.append("override_expired")

    problems = [
        p
        for p in (
            _health(
                finnhub,
                name=FINNHUB,
                symbol=symbol,
                on_or_after=on_or_after,
                now=now,
                policy=policy,
            ),
            _health(
                alphavantage,
                name=ALPHAVANTAGE,
                symbol=symbol,
                on_or_after=on_or_after,
                now=now,
                policy=policy,
            ),
        )
        if p is not None
    ]
    if problems or finnhub is None or alphavantage is None:
        reason = "+".join([*problems, *notes])
        return EarningsResolution(symbol, None, False, "none", reason, "earnings source unusable")

    primary = _next(finnhub, symbol, on_or_after)
    check = _next(alphavantage, symbol, on_or_after)
    if primary != check:
        detail = (
            f"finnhub={primary.isoformat() if primary else 'none'} "
            f"alphavantage={check.isoformat() if check else 'none'}"
        )
        reason = "+".join(["sources_disagree", *notes])
        return EarningsResolution(symbol, None, False, "none", reason, detail)
    detail = (
        f"both sources: {primary.isoformat() if primary else 'no report'} "
        f"(fetched {finnhub.fetched_at.isoformat()} / {alphavantage.fetched_at.isoformat()})"
    )
    return EarningsResolution(symbol, primary, True, "finnhub+alphavantage", "verified", detail)
