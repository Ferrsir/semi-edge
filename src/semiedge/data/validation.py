"""Completed-bar and per-series quality gates.

Universe consumers must also compare reports' feeds before combining series; a single
frame cannot establish the provenance of another symbol or its benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import REQUIRED_BAR_COLUMNS, DataFeed, DataSchemaError, validate_bar_frame


def filter_completed_bars(frame: pd.DataFrame, as_of: date) -> pd.DataFrame:
    validate_bar_frame(frame)
    return frame.loc[frame.index <= pd.Timestamp(as_of)].copy()


@dataclass(frozen=True)
class DataQualityReport:
    symbol: str
    as_of: date
    last_session: date | None
    is_fresh: bool
    missing_sessions: tuple[date, ...]
    feed: DataFeed | None
    issues: tuple[str, ...]

    @property
    def is_actionable(self) -> bool:
        # assess_quality records a blocking issue when missing sessions exceed the allowance.
        return self.is_fresh and self.feed is not None and not self.issues


def assess_quality(
    frame: pd.DataFrame,
    *,
    symbol: str,
    as_of: date,
    calendar: TradingCalendar,
    lookback_sessions: int,
    feed: DataFeed | None,
    max_missing_sessions: int = 0,
) -> DataQualityReport:
    if type(max_missing_sessions) is not int or max_missing_sessions < 0:
        raise ValueError("max_missing_sessions must be a nonnegative integer")
    expected = calendar.sessions_window(as_of, lookback_sessions)
    issues: list[str] = []
    try:
        validate_bar_frame(frame, symbol=symbol)
    except DataSchemaError as exc:
        return DataQualityReport(symbol, as_of, None, False, tuple(expected), feed, (str(exc),))
    completed = filter_completed_bars(frame, as_of)
    last = completed.index[-1].date() if len(completed) else None
    missing = tuple(s for s in expected if pd.Timestamp(s) not in completed.index)
    if last != as_of:
        issues.append(f"stale data: last session {last}, expected {as_of}")
    if len(missing) > max_missing_sessions:
        issues.append(f"missing {len(missing)} sessions (allowed {max_missing_sessions})")
    if feed is None:
        issues.append("data feed is unknown")
    if "feed" in frame:
        feeds = frame["feed"]
        if feeds.isna().any() or feed is None or not feeds.eq(feed.value).all():
            issues.append("mixed or mismatched data feeds")
    if any(not calendar.is_session(s.date()) for s in completed.index):
        issues.append("bars contain non-exchange sessions")
    issues.extend(_ohlcv_issues(completed))
    return DataQualityReport(symbol, as_of, last, last == as_of, missing, feed, tuple(issues))


def _ohlcv_issues(completed: pd.DataFrame) -> list[str]:
    issues: list[str] = []
    values = completed.loc[:, list(REQUIRED_BAR_COLUMNS)]
    if not np.isfinite(values.to_numpy()).all():
        issues.append("non-finite OHLCV values")
    prices = completed.loc[:, ["open", "high", "low", "close"]]
    if (prices <= 0).any().any():
        issues.append("prices must be positive")
    if (completed["volume"] < 0).any():
        issues.append("volume must be nonnegative")
    if (completed["high"] < completed[["open", "close"]].max(axis=1)).any():
        issues.append("high below open or close")
    if (completed["low"] > completed[["open", "close"]].min(axis=1)).any():
        issues.append("low above open or close")
    return issues
