"""Finnhub + Alpha Vantage earnings fetchers, JSON cache and refresh policy (#20).

Spec: ``docs/specifications/earnings-calendar.md`` §2–§3. Keys are passed in by the caller (from
``AppSettings``) and never stored, logged, raised or returned. The Alpha Vantage request URL
contains its key, so it is built here, used once and never kept. Every failure is reduced to a
fixed snake_case code; exception text and response bodies are discarded.

No test performs a real request: :class:`EarningsProvider` takes an injectable ``http_get``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

from semiedge.clock import Clock
from semiedge.data.earnings import (
    _SYMBOL,
    ALPHAVANTAGE,
    FINNHUB,
    AlphaVantageParseError,
    EarningsDataError,
    EarningsEvent,
    EarningsResolution,
    FinnhubParseError,
    ManualOverride,
    ResolutionPolicy,
    SourceName,
    SourceSnapshot,
    load_overrides,
    parse_alphavantage_csv,
    parse_finnhub_json,
    resolve_earnings,
)

__all__ = [
    "ALPHAVANTAGE_COVERAGE_DAYS",
    "CACHE_SCHEMA_VERSION",
    "EarningsProvider",
    "EarningsProviderSettings",
    "HttpGet",
    "RefreshOutcome",
    "load_snapshot",
    "urllib_get",
]

log = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = 1
ALPHAVANTAGE_COVERAGE_DAYS = 89  # horizon=3month always spans at least 89 days
_FINNHUB_URL = "https://finnhub.io/api/v1/calendar/earnings"
_ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"
_MAX_BODY = 8 * 1024 * 1024

HttpGet = Callable[[str, Mapping[str, str], float], tuple[int, bytes]]
"""``(url, headers, timeout_seconds) -> (status, body)``; may raise on transport errors."""


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect (F-166).

    urllib copies request headers onto the redirected request, so following a 3xx from a vendor
    would send ``X-Finnhub-Token`` (or a query-string key) to whatever host the ``Location``
    names, including a plain-http one. Returning ``None`` makes urllib raise ``HTTPError`` with
    the 3xx status. :func:`urllib_get` turns that into ``(status, b"")`` and the provider into
    the error code ``http_3xx``.
    """

    def redirect_request(  # noqa: PLR0917 - urllib calls this with six positional arguments
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _build_opener(*handlers: urllib.request.BaseHandler) -> urllib.request.OpenerDirector:
    """The default transport's opener; tests add a fake HTTPS handler through this same path."""
    return urllib.request.build_opener(_RefuseRedirects, *handlers)


_OPENER = _build_opener()


def urllib_get(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    """Default HTTPS GET to the two fixed vendor URLs only. Redirects are refused (F-166)."""
    if not (url.startswith(_FINNHUB_URL + "?") or url.startswith(_ALPHAVANTAGE_URL + "?")):
        raise ValueError("refusing an unexpected URL")
    request = urllib.request.Request(url, headers=dict(headers), method="GET")  # noqa: S310
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            if (
                response.geturl() != url
            ):  # defence in depth: never accept a response from another URL
                raise ValueError("refusing a redirected response")
            return int(response.status), response.read(_MAX_BODY + 1)
    except urllib.error.HTTPError as exc:  # non-2xx, including a refused 3xx: keep only the code
        return int(exc.code), b""


@dataclass(frozen=True, slots=True)
class EarningsProviderSettings:
    lookahead_days: int
    min_forward_coverage_days: int
    max_age_hours: float
    max_attempts_per_day: int
    retry_interval_minutes: int
    request_timeout_seconds: float
    finnhub_min_interval_seconds: float
    override_max_age_days: int

    @property
    def policy(self) -> ResolutionPolicy:
        return ResolutionPolicy(
            max_age=timedelta(hours=self.max_age_hours),
            min_forward_coverage=timedelta(days=self.min_forward_coverage_days),
            override_max_age=timedelta(days=self.override_max_age_days),
        )


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    source: SourceName
    action: Literal[
        "fetched",
        "skipped_fresh",
        "skipped_attempt_limit",
        "skipped_retry_wait",
        "missing_api_key",
        "failed",
    ]
    status: Literal["ok", "error", "missing"]
    error: str | None


# ------------------------------------------------------------------------- cache


def _snapshot_to_json(snap: SourceSnapshot, attempts: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "source": snap.source,
        "fetched_at": snap.fetched_at.astimezone(UTC).isoformat(),
        "coverage_start": snap.coverage_start.isoformat(),
        "coverage_end": snap.coverage_end.isoformat(),
        "symbols": None if snap.symbols is None else sorted(snap.symbols),
        "events": [
            {"symbol": e.symbol, "report_date": e.report_date.isoformat(), "hour": e.hour}
            for e in snap.events
        ],
        "status": snap.status,
        "error": snap.error,
        "attempts": dict(attempts),
    }


_ATTEMPT_KEYS = frozenset({"date", "count", "last_attempt_at"})
_OPTIONAL_ATTEMPT_KEYS = frozenset({"requested"})  # symbols the last fetch asked for (#114)


def _req(raw: Mapping[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    """Field ``key`` with exactly type ``kind``, never coerced (F-167). A bool is not an int."""
    value = raw[key]
    if isinstance(value, bool) and bool not in (kind if isinstance(kind, tuple) else (kind,)):
        raise EarningsDataError(f"cache field {key} has the wrong type")
    if not isinstance(value, kind):
        raise EarningsDataError(f"cache field {key} has the wrong type")
    return value


def _symbol(value: Any) -> str:
    if not isinstance(value, str) or not _SYMBOL.match(value):
        raise EarningsDataError("cache holds an invalid symbol")
    return value


def _event_from_json(raw: Any) -> EarningsEvent:
    if not isinstance(raw, dict) or set(raw) != {"symbol", "report_date", "hour"}:
        raise EarningsDataError("cache event malformed")
    hour = _req(raw, "hour", (str, type(None)))
    return EarningsEvent(
        _symbol(raw["symbol"]), date.fromisoformat(_req(raw, "report_date", str)), hour
    )


def _attempts_from_json(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EarningsDataError("cache attempts missing")
    if not raw:
        return {}
    if not _ATTEMPT_KEYS <= set(raw) <= _ATTEMPT_KEYS | _OPTIONAL_ATTEMPT_KEYS:
        raise EarningsDataError("cache attempts malformed")
    date.fromisoformat(_req(raw, "date", str))
    if _req(raw, "count", int) < 0:
        raise EarningsDataError("cache attempt count negative")
    last = datetime.fromisoformat(_req(raw, "last_attempt_at", str))
    if last.tzinfo is None:
        raise EarningsDataError("cache last_attempt_at must be timezone-aware")
    if "requested" in raw:
        requested = _req(raw, "requested", list)
        if [_symbol(r) for r in requested] != sorted(set(requested)):
            raise EarningsDataError("cache attempts requested list malformed")
    return dict(raw)


def _snapshot_from_json(raw: Any, source: SourceName) -> tuple[SourceSnapshot, dict[str, Any]]:
    """Rebuild a snapshot from its cache file, rejecting every container or type mismatch (F-167).

    A malformed field must make the cache count as missing, never as "verified, no report".
    """
    if not isinstance(raw, dict) or raw.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise EarningsDataError("cache schema mismatch")
    if raw.get("source") != source:
        raise EarningsDataError("cache source mismatch")
    symbols = _req(raw, "symbols", (list, type(None)))
    events = _req(raw, "events", list)
    snap = SourceSnapshot(
        source=source,
        fetched_at=datetime.fromisoformat(_req(raw, "fetched_at", str)),
        coverage_start=date.fromisoformat(_req(raw, "coverage_start", str)),
        coverage_end=date.fromisoformat(_req(raw, "coverage_end", str)),
        symbols=None if symbols is None else frozenset(_symbol(s) for s in symbols),
        events=tuple(_event_from_json(e) for e in events),
        status=_req(raw, "status", str),
        error=_req(raw, "error", (str, type(None))),
    )
    return snap, _attempts_from_json(raw.get("attempts"))


def load_snapshot(path: Path, source: SourceName) -> tuple[SourceSnapshot | None, dict[str, Any]]:
    """Read one cache file; anything unreadable or invalid counts as missing (fail closed)."""
    if not path.exists():
        return None, {}
    try:
        return _snapshot_from_json(json.loads(path.read_text(encoding="utf-8")), source)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        log.warning("earnings cache unreadable; treated as missing", extra={"source": source})
        return None, {}


def _save(path: Path, snap: SourceSnapshot, attempts: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_snapshot_to_json(snap, attempts), indent=2, allow_nan=False)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------------- provider


class _FetchError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EarningsProvider:
    """Owns the two caches, the refresh policy and query-time resolution."""

    def __init__(
        self,
        settings: EarningsProviderSettings,
        *,
        cache_dir: Path,
        override_path: Path,
        universe: Iterable[str],
        finnhub_api_key: str | None,
        alphavantage_api_key: str | None,
        clock: Clock,
        http_get: HttpGet = urllib_get,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._cache_dir = cache_dir
        self._override_path = override_path
        self._universe = tuple(sorted(set(universe)))
        if not self._universe:
            raise ValueError("earnings universe is empty")
        self._keys: dict[SourceName, str | None] = {
            FINNHUB: finnhub_api_key or None,
            ALPHAVANTAGE: alphavantage_api_key or None,
        }
        self._clock = clock
        self._http_get = http_get
        self._sleep = sleep

    def __repr__(self) -> str:  # never show keys
        return f"EarningsProvider(universe={len(self._universe)} symbols)"

    @property
    def universe(self) -> tuple[str, ...]:
        return self._universe

    def cache_path(self, source: SourceName) -> Path:
        return self._cache_dir / f"{source}.json"

    def snapshots(self) -> dict[SourceName, SourceSnapshot | None]:
        return {s: load_snapshot(self.cache_path(s), s)[0] for s in (FINNHUB, ALPHAVANTAGE)}

    def overrides(self) -> dict[str, ManualOverride]:
        return load_overrides(
            self._override_path, universe=self._universe, today=self._now().date()
        )

    def _now(self) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock returned a naive datetime")
        return now.astimezone(UTC)

    # ---------------------------------------------------------------- refresh

    def refresh(self, *, force: bool = False) -> tuple[RefreshOutcome, ...]:
        return tuple(self._refresh_one(source, force) for source in (FINNHUB, ALPHAVANTAGE))

    def _refresh_one(self, source: SourceName, force: bool) -> RefreshOutcome:
        path = self.cache_path(source)
        snap, attempts = load_snapshot(path, source)
        now = self._now()
        today = now.date().isoformat()
        key = self._keys[source]
        if key is None:
            log.warning("earnings source has no API key", extra={"source": source})
            return RefreshOutcome(source, "missing_api_key", self._status(snap), "missing_api_key")
        wanted = None if source == ALPHAVANTAGE else frozenset(self._universe)
        if not force:
            if (
                snap is not None
                and snap.status == "ok"
                and snap.fetched_at.astimezone(UTC).date() == now.date()
                and self._covers_request(snap, attempts, wanted)
            ):
                return RefreshOutcome(source, "skipped_fresh", "ok", None)
            count = int(attempts.get("count", 0)) if attempts.get("date") == today else 0
            if count >= self._settings.max_attempts_per_day:
                return RefreshOutcome(
                    source,
                    "skipped_attempt_limit",
                    self._status(snap),
                    snap.error if snap else None,
                )
            last = attempts.get("last_attempt_at")
            last_failed = snap is None or snap.status == "error"
            if last_failed and isinstance(last, str) and attempts.get("date") == today:
                waited = now - datetime.fromisoformat(last)
                if waited < timedelta(minutes=self._settings.retry_interval_minutes):
                    return RefreshOutcome(
                        source,
                        "skipped_retry_wait",
                        self._status(snap),
                        snap.error if snap else None,
                    )
        prior = int(attempts.get("count", 0)) if attempts.get("date") == today else 0
        new_attempts = {"date": today, "count": prior + 1, "last_attempt_at": now.isoformat()}
        if wanted is not None:
            new_attempts["requested"] = sorted(wanted)
        try:
            fresh = self._fetch_finnhub(key, now) if source == FINNHUB else self._fetch_av(key, now)
        except _FetchError as exc:
            failed = self._failed_snapshot(source, snap, now, exc.code)
            _save(path, failed, new_attempts)
            log.warning("earnings refresh failed", extra={"source": source, "error_code": exc.code})
            return RefreshOutcome(source, "failed", "error", exc.code)
        _save(path, fresh, new_attempts)
        log.info("earnings refresh ok", extra={"source": source, "events": len(fresh.events)})
        return RefreshOutcome(source, "fetched", "ok", None)

    @staticmethod
    def _covers_request(
        snap: SourceSnapshot, attempts: Mapping[str, Any], wanted: frozenset[str] | None
    ) -> bool:
        """Fresh for this universe (#114).

        True when the snapshot covers every wanted symbol, or when the last fetch asked for exactly
        this universe and only unparseable symbols are missing. A changed universe refetches.
        """
        if snap.symbols == wanted:
            return True
        if wanted is None or snap.symbols is None or not snap.symbols <= wanted:
            return False
        return attempts.get("requested") == sorted(wanted)

    @staticmethod
    def _status(snap: SourceSnapshot | None) -> Literal["ok", "error", "missing"]:
        return "missing" if snap is None else snap.status

    def _failed_snapshot(
        self, source: SourceName, prior: SourceSnapshot | None, now: datetime, code: str
    ) -> SourceSnapshot:
        if prior is not None:
            return SourceSnapshot(
                source,
                prior.fetched_at,
                prior.coverage_start,
                prior.coverage_end,
                prior.symbols,
                prior.events,
                "error",
                code,
            )
        return SourceSnapshot(source, now, now.date(), now.date(), frozenset(), (), "error", code)

    def _get(self, url: str, headers: Mapping[str, str]) -> bytes:
        try:
            status, body = self._http_get(url, headers, self._settings.request_timeout_seconds)
        except Exception as exc:  # transport error: keep only the class name, never the text
            raise _FetchError(f"transport_{type(exc).__name__.lower()}"[:40]) from None
        if status == 429:
            raise _FetchError("rate_limited")
        if status != 200:
            raise _FetchError(f"http_{int(status)}")
        if len(body) > _MAX_BODY:
            raise _FetchError("response_too_large")
        return body

    def _fetch_finnhub(self, key: str, now: datetime) -> SourceSnapshot:
        start = now.date()
        end = start + timedelta(days=self._settings.lookahead_days)
        events: list[EarningsEvent] = []
        covered: list[str] = []
        unparsed: list[str] = []
        for index, symbol in enumerate(self._universe):
            if index:
                self._sleep(self._settings.finnhub_min_interval_seconds)
            query = urlencode({"from": start.isoformat(), "to": end.isoformat(), "symbol": symbol})
            # A transport or HTTP failure is an outage, not one symbol: it fails the whole snapshot.
            body = self._get(f"{_FINNHUB_URL}?{query}", {"X-Finnhub-Token": key})
            try:
                events.extend(parse_finnhub_json(body, symbol=symbol, start=start, end=end))
            except FinnhubParseError:
                # Owner decision 2026-11-14 (#114): one symbol's unparseable answer (Finnhub returns
                # 2330.TW for TSM) leaves that symbol uncovered, so R16 blocks it alone.
                unparsed.append(symbol)
                continue
            covered.append(symbol)
        if not covered:
            raise _FetchError("parse_error")
        if unparsed:
            log.warning(
                "finnhub answers failed to parse; symbols left uncovered",
                extra={"source": FINNHUB, "symbols": ",".join(unparsed)},
            )
        return SourceSnapshot(
            FINNHUB, now, start, end, frozenset(covered), tuple(events), "ok", None
        )

    def _fetch_av(self, key: str, now: datetime) -> SourceSnapshot:
        start = now.date()
        query = urlencode({"function": "EARNINGS_CALENDAR", "horizon": "3month", "apikey": key})
        body = self._get(f"{_ALPHAVANTAGE_URL}?{query}", {})
        del query  # contains the key
        try:
            events = parse_alphavantage_csv(body)
        except AlphaVantageParseError as exc:
            raise _FetchError(exc.code) from None
        end = start + timedelta(days=ALPHAVANTAGE_COVERAGE_DAYS)
        universe = set(self._universe)
        kept = tuple(e for e in events if e.symbol in universe and start <= e.report_date)
        return SourceSnapshot(ALPHAVANTAGE, now, start, end, None, kept, "ok", None)

    # ---------------------------------------------------------------- resolution

    def resolver(self) -> Callable[[str, date], EarningsResolution]:
        """Bind the current caches and overrides; resolution uses the clock at query time.

        Raises :class:`EarningsDataError` if the override file is invalid (fail closed).
        """
        snaps = self.snapshots()
        overrides = self.overrides()
        policy = self._settings.policy

        def resolve(symbol: str, on_or_after: date) -> EarningsResolution:
            return resolve_earnings(
                symbol,
                on_or_after,
                finnhub=snaps[FINNHUB],
                alphavantage=snaps[ALPHAVANTAGE],
                overrides=overrides,
                now=self._now(),
                policy=policy,
            )

        return resolve
