"""Typed corporate-action ledger and consistency checks; no order or portfolio writes."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
import pandas as pd
from alpaca.common.enums import BaseURL
from alpaca.data.historical.corporate_actions import CorporateActionsClient

from semiedge.data.alpaca_source import MarketDataError
from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import Adjustment, DataFeed, DataSource, validate_bar_frame
from semiedge.data.splits import SplitEvent
from semiedge.data.store import StoredSeries


class CorporateActionKind(StrEnum):
    SPLIT = "split"
    REVERSE_SPLIT = "reverse_split"
    CASH_DIVIDEND = "cash_dividend"
    STOCK_DIVIDEND = "stock_dividend"
    SPINOFF = "spinoff"
    MERGER = "merger"
    SYMBOL_CHANGE = "symbol_change"
    OTHER = "other"


_SPLITS = frozenset({CorporateActionKind.SPLIT, CorporateActionKind.REVERSE_SPLIT})
_FIELDS = frozenset(
    {
        "event_id",
        "symbol",
        "kind",
        "ex_date",
        "new_shares",
        "old_shares",
        "cash_amount",
        "source",
        "fetched_at",
        "known_at",
    }
)


def _symbol(symbol: str) -> None:
    if type(symbol) is not str or re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol) is None:
        raise ValueError("invalid corporate-action symbol")


def _aware(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("corporate-action timestamps must be aware datetimes")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class CorporateAction:
    event_id: str
    symbol: str
    kind: CorporateActionKind
    ex_date: date
    new_shares: int | None
    old_shares: int | None
    cash_amount: float | None
    source: DataSource
    fetched_at: datetime
    known_at: datetime

    def __post_init__(self) -> None:
        _symbol(self.symbol)
        if (
            type(self.event_id) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", self.event_id) is None
        ):
            raise ValueError("invalid corporate-action event id")
        if not isinstance(self.kind, CorporateActionKind) or not isinstance(
            self.source, DataSource
        ):
            raise ValueError("corporate-action kind/source must be explicit enums")
        if type(self.ex_date) is not date:
            raise ValueError("corporate-action ex_date must be a date")
        object.__setattr__(self, "fetched_at", _aware(self.fetched_at))
        object.__setattr__(self, "known_at", _aware(self.known_at))
        if self.known_at > self.fetched_at:
            raise ValueError("event knowledge cannot be later than its retrieval")
        if (
            self.source != DataSource.ALPACA
            and self.event_id != f"{self.kind}:{self.symbol}:{self.ex_date:%Y%m%d}"
        ):
            raise ValueError("fixture/synthetic event ids must use the canonical event key")
        self._check_terms()

    def _check_terms(self) -> None:
        if self.kind in _SPLITS:
            try:
                supported = TradingCalendar().is_session(self.ex_date)
            except ValueError:
                supported = False
            if not supported:
                raise ValueError("split ex_date must be a supported exchange session")
            # Reuse the shared exact-count validation; no independent split arithmetic.
            event = self.to_split_event()
            if self.cash_amount is not None:
                raise ValueError("split cash_amount must be None")
            if (event.new_shares > event.old_shares) != (self.kind == CorporateActionKind.SPLIT):
                raise ValueError("split direction disagrees with its kind")
        elif self.new_shares is not None or self.old_shares is not None:
            raise ValueError("non-split share counts must be None")
        if self.kind == CorporateActionKind.CASH_DIVIDEND:
            amount = self.cash_amount
            if not isinstance(amount, float | int) or isinstance(amount, bool):
                raise ValueError("cash dividends require a finite positive cash_amount")
            try:
                valid = math.isfinite(amount) and amount > 0
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError("cash dividends require a finite positive cash_amount")
        elif self.cash_amount is not None:
            raise ValueError("cash_amount must be None for non-cash actions")

    @property
    def ratio(self) -> Fraction | None:
        if self.kind not in _SPLITS:
            return None
        event = self.to_split_event()
        return Fraction(event.new_shares, event.old_shares)

    def to_split_event(self) -> SplitEvent:
        if (
            self.kind not in _SPLITS
            or type(self.new_shares) is not int
            or type(self.old_shares) is not int
        ):
            raise ValueError("an exact split event requires integer share counts")
        return SplitEvent(
            self.symbol, self.ex_date, self.new_shares, self.old_shares, self.known_at
        )


class CorporateActionSource(Protocol):
    def get_actions(
        self, symbols: Sequence[str], start: date, end: date
    ) -> tuple[CorporateAction, ...]: ...


def _payload(action: CorporateAction) -> dict[str, object]:
    return {
        "event_id": action.event_id,
        "symbol": action.symbol,
        "kind": action.kind.value,
        "ex_date": action.ex_date.isoformat(),
        "new_shares": action.new_shares,
        "old_shares": action.old_shares,
        "cash_amount": action.cash_amount,
        "source": action.source.value,
        "fetched_at": action.fetched_at.isoformat(),
        "known_at": action.known_at.isoformat(),
    }


def _datetime(text: object) -> datetime:
    if (
        type(text) is not str
        or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:[0-5]\d)", text
        )
        is None
    ):
        raise ValueError("invalid corporate-action timestamp")
    return _aware(datetime.fromisoformat(text))


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate corporate-action JSON key")
        result[key] = value
    return result


def _decode(text: object) -> CorporateAction:
    if type(text) is not str:
        raise ValueError("corporate-action payload must be JSON text")
    value = json.loads(text, object_pairs_hook=_unique)
    if type(value) is not dict or set(value) != _FIELDS:
        raise ValueError("invalid corporate-action payload fields")
    if type(value["ex_date"]) is not str:
        raise ValueError("invalid corporate-action ex_date")
    day = date.fromisoformat(value["ex_date"])
    if day.isoformat() != value["ex_date"]:
        raise ValueError("non-canonical corporate-action ex_date")
    return CorporateAction(
        value["event_id"],
        value["symbol"],
        CorporateActionKind(value["kind"]),
        day,
        value["new_shares"],
        value["old_shares"],
        value["cash_amount"],
        DataSource(value["source"]),
        _datetime(value["fetched_at"]),
        _datetime(value["known_at"]),
    )


class CorporateActionStore:
    """Immutable event terms, per-symbol atomic Parquet publication; no absence guarantee.

    JSON payloads preserve integer share counts exactly instead of nullable float coercion.
    Missing ledgers raise; an empty fetch/write never certifies that no actions exist.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, symbol: str) -> Path:
        _symbol(symbol)
        path = self.root / "raw" / "corporate_actions" / f"{symbol}.parquet"
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError("corporate-action path escapes its cache root or is a symlink")
        return path

    def read(self, symbol: str) -> tuple[CorporateAction, ...]:
        path = self._path(symbol)
        if not path.exists():
            raise FileNotFoundError("corporate-action ledger unavailable for requested symbol")
        frame = pd.read_parquet(path)
        if set(frame.columns) != {"schema_version", "payload_json"} or frame.empty:
            raise ValueError("invalid corporate-action ledger schema")
        if frame["schema_version"].dtype != "int64" or not frame["schema_version"].eq(1).all():
            raise ValueError("unsupported corporate-action ledger version")
        try:
            actions = tuple(_decode(text) for text in frame["payload_json"])
        except (ValueError, TypeError, OverflowError):
            raise ValueError("corporate-action ledger payload failed validation") from None
        if any(a.symbol != symbol for a in actions) or len({a.event_id for a in actions}) != len(
            actions
        ):
            raise ValueError("corporate-action ledger contains mismatched or duplicate events")
        if len({a.source for a in actions}) != 1:
            raise ValueError("corporate-action ledger mixes data sources")
        return tuple(sorted(actions, key=lambda a: (a.ex_date, a.event_id)))

    def write(self, actions: Sequence[CorporateAction]) -> None:
        grouped: dict[str, list[CorporateAction]] = {}
        for action in actions:
            if not isinstance(action, CorporateAction):
                raise ValueError("ledger writes require validated CorporateAction values")
            grouped.setdefault(action.symbol, []).append(action)
        prepared: dict[Path, pd.DataFrame] = {}
        for symbol, additions in grouped.items():
            path = self._path(symbol)
            current = self.read(symbol) if path.exists() else ()
            merged = {a.event_id: a for a in current}
            for action in additions:
                old = merged.get(action.event_id)
                if old is not None:
                    prior, fresh = _payload(old), _payload(action)
                    prior.pop("fetched_at")
                    fresh.pop("fetched_at")
                    if prior != fresh:
                        raise ValueError("conflicting corporate-action event terms require review")
                    if old.fetched_at >= action.fetched_at:
                        continue
                merged[action.event_id] = action
            if len({a.source for a in merged.values()}) != 1:
                raise ValueError("corporate-action ledger cannot mix data sources")
            rows = sorted(merged.values(), key=lambda a: (a.ex_date, a.event_id))
            prepared[path] = pd.DataFrame(
                {
                    "schema_version": pd.Series([1] * len(rows), dtype="int64"),
                    "payload_json": [
                        json.dumps(_payload(a), sort_keys=True, allow_nan=False) for a in rows
                    ],
                }
            )
        for path, frame in prepared.items():
            self._publish(path, frame)

    @staticmethod
    def _publish(path: Path, frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        os.close(fd)
        try:
            frame.to_parquet(temporary, index=False)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class AdjustmentCheck:
    symbol: str
    is_consistent: bool
    issues: tuple[str, ...]


def check_adjustment_consistency(
    stored: StoredSeries, actions: Sequence[CorporateAction], *, jump_threshold: float = 0.35
) -> AdjustmentCheck:
    """Check a vendor-adjusted snapshot, not historical event coverage or strategy performance.

    Splits after retrieval require refetch before the stored-prefix cutoff is applied.
    An explained raw split discontinuity in already
    adjusted bars is still inconsistent (proposed §14 correction on #18); event metadata is
    never used here to repair prices silently. Freshness/required windows remain separate gates.
    """
    if isinstance(jump_threshold, bool) or not isinstance(jump_threshold, float | int):
        raise ValueError("jump_threshold must be a finite fraction between zero and one")
    try:
        valid_threshold = math.isfinite(jump_threshold) and 0 < jump_threshold < 1
    except OverflowError:
        valid_threshold = False
    if not valid_threshold:
        raise ValueError("jump_threshold must be a finite fraction between zero and one")
    _symbol(stored.symbol)
    fetched_at = _aware(stored.fetched_at)
    if (
        not isinstance(stored.source, DataSource)
        or not isinstance(stored.feed, DataFeed)
        or not isinstance(stored.adjustment, Adjustment)
        or (stored.source == DataSource.SYNTHETIC) != (stored.feed == DataFeed.SYNTHETIC)
    ):
        return AdjustmentCheck(stored.symbol, False, ("stored provenance is invalid",))
    frame = stored.frame
    try:
        validate_bar_frame(frame, symbol=stored.symbol)
    except ValueError:
        return AdjustmentCheck(stored.symbol, False, ("invalid stored bar schema",))
    issues = _bar_issues(frame)
    if issues:
        return AdjustmentCheck(stored.symbol, False, tuple(issues))
    if stored.adjustment not in (Adjustment.SPLIT, Adjustment.ALL):
        issues.append("corporate-action consistency requires vendor split-adjusted history")
    last = frame.index[-1].date()
    try:
        if fetched_at < TradingCalendar().session_close(last):
            issues.append("last stored session was not complete at retrieval")
    except ValueError:
        issues.append("last stored session is not a supported exchange session")
    effective, event_issues = _effective_actions(stored, actions, last, fetched_at)
    issues.extend(event_issues)
    issues.extend(_jump_issues(frame, effective, stored.adjustment, float(jump_threshold)))
    return AdjustmentCheck(stored.symbol, not issues, tuple(dict.fromkeys(issues)))


def _effective_actions(
    stored: StoredSeries, actions: Sequence[CorporateAction], last: date, fetched_at: datetime
) -> tuple[dict[date, list[CorporateAction]], list[str]]:
    issues: list[str] = []
    effective: dict[date, list[CorporateAction]] = {}
    seen: set[str] = set()
    for action in actions:
        if not isinstance(action, CorporateAction) or action.symbol != stored.symbol:
            raise ValueError("actions must match the stored symbol")
        if action.event_id in seen:
            raise ValueError("duplicate corporate-action event identities")
        seen.add(action.event_id)
        if action.source != stored.source:
            issues.append("corporate-action source differs from stored history")
        if action.kind in _SPLITS and action.ex_date > fetched_at.date():
            issues.append(f"split {action.ex_date} after latest fetched_at: full refetch required")
        if action.ex_date > last:
            continue
        effective.setdefault(action.ex_date, []).append(action)
        if action.known_at > fetched_at:
            issues.append("effective corporate action was not known at the retrieval cutoff")
    return effective, issues


def _bar_issues(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return ["stored bar history is empty"]
    values = frame[["open", "high", "low", "close", "volume"]]
    if not np.isfinite(values.to_numpy()).all():
        return ["stored OHLCV values must be finite"]
    if (frame[["open", "high", "low", "close"]] <= 0).any().any() or (frame.volume < 0).any():
        return ["stored prices must be positive and volume nonnegative"]
    if (frame.high < frame[["open", "low", "close"]].max(axis=1)).any() or (
        frame.low > frame[["open", "high", "close"]].min(axis=1)
    ).any():
        return ["stored OHLC envelope is invalid"]
    return []


def _jump_issues(
    frame: pd.DataFrame,
    events: dict[date, list[CorporateAction]],
    adjustment: Adjustment,
    threshold: float,
) -> list[str]:
    issues: list[str] = []
    for index in range(1, len(frame)):
        day = frame.index[index].date()
        previous, current = float(frame.close.iloc[index - 1]), float(frame.close.iloc[index])
        price_ratio = current / previous
        if not math.isfinite(price_ratio):
            issues.append(f"nonfinite close return at {day}")
            continue
        matching = events.get(day, [])
        splits = [a for a in matching if a.kind in _SPLITS]
        if len(splits) > 1:
            issues.append(f"multiple split events at {day} require review")
        split = splits[0].ratio if len(splits) == 1 else None
        log_return = math.log(current) - math.log(previous)
        if split is not None and abs(log_return + math.log(float(split))) <= abs(log_return):
            issues.append(f"unadjusted split discontinuity at {day}: full refetch required")
        elif abs(price_ratio - 1) > threshold:
            cash = [a for a in matching if a.kind == CorporateActionKind.CASH_DIVIDEND]
            try:
                total_cash = math.fsum(a.cash_amount for a in cash if a.cash_amount is not None)
            except OverflowError:
                total_cash = math.inf
            explained_cash = (
                adjustment == Adjustment.SPLIT
                and bool(cash)
                and math.isfinite(total_cash)
                and math.isclose(current, previous - total_cash, rel_tol=1e-6)
            )
            if not explained_cash:
                issues.append(f"unexplained 1-day move {price_ratio - 1:+.1%} on {day}")
    return issues


class CorporateActionReadClient(Protocol):
    @property
    def _base_url(self) -> object: ...

    def get(self, path: Literal["/corporate-actions"], data: dict[str, object]) -> object: ...


_CATEGORIES: dict[str, tuple[CorporateActionKind, str, str]] = {
    "forward_splits": (CorporateActionKind.SPLIT, "symbol", "ex_date"),
    "reverse_splits": (CorporateActionKind.REVERSE_SPLIT, "symbol", "ex_date"),
    "cash_dividends": (CorporateActionKind.CASH_DIVIDEND, "symbol", "ex_date"),
    "stock_dividends": (CorporateActionKind.STOCK_DIVIDEND, "symbol", "ex_date"),
    "spin_offs": (CorporateActionKind.SPINOFF, "source_symbol", "ex_date"),
    "cash_mergers": (CorporateActionKind.MERGER, "acquiree_symbol", "effective_date"),
    "stock_mergers": (CorporateActionKind.MERGER, "acquiree_symbol", "effective_date"),
    "stock_and_cash_mergers": (CorporateActionKind.MERGER, "acquiree_symbol", "effective_date"),
    "name_changes": (CorporateActionKind.SYMBOL_CHANGE, "old_symbol", "effective_date"),
}


def _data_endpoint(client: CorporateActionReadClient) -> None:
    raw = client._base_url
    if raw is BaseURL.DATA or (type(raw) is str and raw.rstrip("/") == BaseURL.DATA.value):
        return
    raise MarketDataError("corporate actions require the Alpaca market-data endpoint")


def _share_count(value: object) -> int:
    if type(value) is int and 0 < value <= 2**53:
        return value
    if type(value) is float and math.isfinite(value) and value.is_integer() and 0 < value <= 2**53:
        return int(value)  # exact integer-valued API rate, never a float ratio reconstruction
    raise ValueError("corporate-action rates must be exact positive integer share counts")


def _record(
    category: str, raw: object, fetched_at: datetime, knowledge: Callable[[str], datetime]
) -> CorporateAction:
    if type(raw) is not dict or category not in _CATEGORIES:
        raise ValueError("unsupported corporate-action category or record")
    kind, symbol_key, date_key = _CATEGORIES[category]
    identity = raw.get("id")
    if (
        type(identity) is not str
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", identity) is None
    ):
        raise ValueError("invalid corporate-action event id")
    day = raw[date_key]
    if type(day) is not str:
        raise ValueError("corporate-action effective date is missing")
    ex_date = date.fromisoformat(day)
    if ex_date.isoformat() != day:
        raise ValueError("corporate-action effective date is not canonical")
    new = _share_count(raw.get("new_rate")) if kind in _SPLITS else None
    old = _share_count(raw.get("old_rate")) if kind in _SPLITS else None
    return CorporateAction(
        identity,
        raw[symbol_key],
        kind,
        ex_date,
        new,
        old,
        raw.get("rate") if kind == CorporateActionKind.CASH_DIVIDEND else None,
        DataSource.ALPACA,
        fetched_at,
        knowledge(identity),
    )


class AlpacaCorporateActionSource:
    """Read-only, bounded raw JSON adapter with required verified event knowledge times.

    Provider start/end filter process_date, not ex_date. Missing knowledge/effective dates and
    unsupported action categories fail explicitly; retrieval time never substitutes for known_at.
    Empty valid responses do not establish complete historical corporate-action coverage.
    """

    def __init__(
        self,
        client: CorporateActionReadClient,
        *,
        knowledge: Callable[[str], datetime],
        now: Callable[[], datetime],
    ) -> None:
        _data_endpoint(client)
        if not callable(knowledge) or not callable(now):
            raise MarketDataError(
                "corporate actions require an explicit knowledge resolver and clock"
            )
        self._client = client
        self._knowledge = knowledge
        self._now = now

    @classmethod
    def from_credentials(
        cls,
        api_key: str,
        secret_key: str,
        *,
        knowledge: Callable[[str], datetime],
        now: Callable[[], datetime],
    ) -> AlpacaCorporateActionSource:
        if type(api_key) is not str or not api_key or type(secret_key) is not str or not secret_key:
            raise MarketDataError("corporate-action market-data credentials are missing")
        try:
            client = CorporateActionsClient(api_key=api_key, secret_key=secret_key, raw_data=True)
            return cls(client, knowledge=knowledge, now=now)
        except Exception:
            raise MarketDataError("cannot construct corporate-action market-data client") from None

    def _pages(self, symbols: tuple[str, ...], start: date, end: date) -> list[tuple[str, object]]:
        rows: list[tuple[str, object]] = []
        seen: set[str] = set()
        query: dict[str, object] = {
            "symbols": ",".join(symbols),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": 1000,
            "sort": "asc",
            "region": "us",
            "data_quality": "all",
        }
        for _ in range(100):
            _data_endpoint(self._client)
            page = self._client.get(path="/corporate-actions", data=dict(query))
            if type(page) is not dict or type(page.get("corporate_actions")) is not dict:
                raise ValueError("invalid corporate-action response envelope")
            for category, values in page["corporate_actions"].items():
                if category not in _CATEGORIES or type(values) is not list:
                    raise ValueError("unsupported corporate-action response category")
                rows.extend((category, value) for value in values)
            if len(rows) > 100_000:
                raise ValueError("corporate-action response exceeds event limit")
            token = page.get("next_page_token")
            if token is None:
                return rows
            if type(token) is not str or not token or not token.isprintable() or token in seen:
                raise ValueError("invalid or repeated corporate-action pagination token")
            seen.add(token)
            query["page_token"] = token
        raise ValueError("corporate-action response exceeds page limit")

    def get_actions(
        self, symbols: Sequence[str], start: date, end: date
    ) -> tuple[CorporateAction, ...]:
        try:
            requested = tuple(sorted(set(symbols)))
            if not requested or type(start) is not date or type(end) is not date or start > end:
                raise ValueError("invalid corporate-action request")
            for symbol in requested:
                _symbol(symbol)
            rows = self._pages(requested, start, end)
            fetched_at = _aware(self._now())
            actions = tuple(
                _record(category, raw, fetched_at, self._knowledge) for category, raw in rows
            )
            if any(a.symbol not in requested for a in actions):
                raise ValueError("corporate-action response contains unrequested symbols")
            identities = [(a.symbol, a.event_id) for a in actions]
            if len(identities) != len(set(identities)):
                raise ValueError("corporate-action response contains duplicate events")
            return tuple(sorted(actions, key=lambda a: (a.symbol, a.ex_date, a.event_id)))
        except Exception:
            raise MarketDataError(
                "corporate-action data or verified knowledge unavailable"
            ) from None
