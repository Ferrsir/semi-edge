"""Pure, explicitly cut off split normalization; no vendor or portfolio integration (#46)."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from fractions import Fraction

import numpy as np
import pandas as pd

from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import (
    OPTIONAL_BAR_COLUMNS,
    REQUIRED_BAR_COLUMNS,
    Adjustment,
    validate_bar_frame,
)


class SplitDataError(ValueError):
    """Invalid events, provenance, bars or numerical adjustment result."""


def _symbol(value: str) -> None:
    if type(value) is not str or re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", value) is None:
        raise SplitDataError("symbol must be an uppercase ticker")


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SplitDataError("known_at must be an aware datetime")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class SplitEvent:
    symbol: str
    effective_session: date
    new_shares: int
    old_shares: int
    known_at: datetime

    def __post_init__(self) -> None:
        _symbol(self.symbol)
        if type(self.effective_session) is not date:
            raise SplitDataError("effective_session must be a date")
        for count in (self.new_shares, self.old_shares):
            if type(count) is not int or not 0 < count <= 2**53:
                raise SplitDataError("share counts must be integers from 1 through 2**53")
        if self.new_shares == self.old_shares:
            raise SplitDataError("split ratio must differ from one")
        object.__setattr__(self, "known_at", _aware(self.known_at))

    @property
    def ratio(self) -> float:
        return self.new_shares / self.old_shares


@dataclass(frozen=True)
class SplitNormalization:
    """Frame is a fresh copy; applied events do not certify ledger completeness."""

    frame: pd.DataFrame
    applied: tuple[SplitEvent, ...]
    as_of: date
    known_at: datetime


def _session(day: date, calendar: TradingCalendar) -> None:
    if type(day) is not date:
        raise SplitDataError("session must be a date")
    try:
        valid = calendar.is_session(day)
    except ValueError:
        valid = False
    if not valid:
        raise SplitDataError(f"not a supported exchange session: {day}")


def _values(frame: pd.DataFrame) -> None:
    values = frame.to_numpy(dtype=np.float64)
    if not np.isfinite(values).all():
        raise SplitDataError("bar values must be finite")
    prices = ["open", "high", "low", "close", *(["vwap"] if "vwap" in frame else [])]
    if (frame[prices] <= 0).any().any() or (frame["volume"] < 0).any():
        raise SplitDataError("prices must be positive and volume nonnegative")
    if (frame["high"] < frame[["open", "low", "close"]].max(axis=1)).any() or (
        frame["low"] > frame[["open", "high", "close"]].min(axis=1)
    ).any():
        raise SplitDataError("invalid OHLC envelope")
    if "trade_count" in frame and (
        (frame["trade_count"] < 0).any()
        or (frame["trade_count"] != np.floor(frame["trade_count"])).any()
    ):
        raise SplitDataError("trade_count must be a nonnegative integer")


def _apply_ratios(result: pd.DataFrame, applied: tuple[SplitEvent, ...]) -> None:
    prices = ["open", "high", "low", "close", *(["vwap"] if "vwap" in result else [])]
    # Compose later events exactly before rounding once at the float64 bar boundary. Inverse
    # events then have factor exactly one instead of accumulating one rounding error per event.
    groups: dict[Fraction, list[pd.Timestamp]] = {}
    factor = Fraction(1)
    cursor = len(applied) - 1
    for stamp in reversed(result.index):
        while cursor >= 0 and stamp < pd.Timestamp(applied[cursor].effective_session):
            event = applied[cursor]
            factor *= Fraction(event.new_shares, event.old_shares)
            cursor -= 1
        groups.setdefault(factor, []).append(stamp)
    try:
        with np.errstate(over="raise", under="raise", invalid="raise", divide="raise"):
            for factor, sessions in groups.items():
                if factor == 1:
                    continue
                ratio = float(factor)
                if not math.isfinite(ratio) or ratio <= 0:
                    raise FloatingPointError("cumulative split ratio is not representable")
                mask = result.index.isin(sessions)
                # NumPy operations honour errstate; Pandas arithmetic may suppress it.
                result.loc[mask, prices] = result.loc[mask, prices].to_numpy() / ratio
                result.loc[mask, "volume"] = result.loc[mask, "volume"].to_numpy() * ratio
    except (FloatingPointError, OverflowError):
        raise SplitDataError("split adjustment overflowed or underflowed") from None


def normalize_raw_splits(
    frame: pd.DataFrame,
    events: Iterable[SplitEvent],
    *,
    symbol: str,
    adjustment: Adjustment,
    as_of: date,
    known_at: datetime,
    calendar: TradingCalendar,
) -> SplitNormalization:
    """Normalize raw bars only with effective events available by the explicit cutoff."""
    _symbol(symbol)
    cutoff = _aware(known_at)
    _session(as_of, calendar)
    if calendar.session_close(as_of) > cutoff:
        raise SplitDataError("as_of session has not closed at known_at")
    if adjustment is not Adjustment.RAW or frame.attrs.get("adjustment") != "raw":
        raise SplitDataError("only explicitly RAW, unadjusted frames may be normalized")
    validate_bar_frame(frame, symbol=symbol)
    if set(frame.columns) - {*REQUIRED_BAR_COLUMNS, *OPTIONAL_BAR_COLUMNS}:
        raise SplitDataError("unknown bar columns cannot be safely adjusted")
    result = frame.loc[frame.index <= pd.Timestamp(as_of)].copy(deep=True)
    _values(result)
    for stamp in result.index:
        _session(stamp.date(), calendar)
    try:
        events = tuple(events)
    except TypeError:
        raise SplitDataError("events must be an iterable of SplitEvent values") from None
    seen: set[date] = set()
    for event in events:
        if not isinstance(event, SplitEvent) or event.symbol != symbol:
            raise SplitDataError("every event must be a SplitEvent for the requested symbol")
        _session(event.effective_session, calendar)
        if event.effective_session in seen:
            raise SplitDataError("duplicate or conflicting effective sessions")
        seen.add(event.effective_session)
        if event.effective_session <= as_of and event.known_at > cutoff:
            raise SplitDataError("effective split was not known at the information cutoff")
    applied = tuple(
        sorted(
            (e for e in events if e.known_at <= cutoff and e.effective_session <= as_of),
            key=lambda e: e.effective_session,
        )
    )
    _apply_ratios(result, applied)
    _values(result)
    result.attrs["adjustment"] = Adjustment.SPLIT.value
    result.attrs["split_as_of"] = as_of.isoformat()
    result.attrs["split_known_at"] = cutoff.isoformat()
    return SplitNormalization(result, applied, as_of, cutoff)
