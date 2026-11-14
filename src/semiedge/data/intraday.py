"""5-minute bar contract and cache for ``intraday_v1`` (#118; spec ``intraday_v1.md``, Data).

A frame is indexed by each bar's **start** time in UTC (``ts``), on the 5-minute grid, inside the
regular session: from the session open to ``close − 5 min``. Pre- and post-market bars are not part
of the contract; the source drops them before validation.

The store keeps one Parquet file per symbol, feed and adjustment, and every write **replaces** the
file. A split-adjusted vendor series is restated after a split, so merging two fetches could splice
two price bases together; replacing the whole file keeps one basis per file.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import (
    OPTIONAL_BAR_COLUMNS,
    REQUIRED_BAR_COLUMNS,
    Adjustment,
    DataFeed,
    DataSchemaError,
    DataSource,
)

INTRADAY_INDEX_NAME = "ts"
BAR_MINUTES = 5
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")
_META_COLUMNS = ("feed", "adjustment", "source", "fetched_at")


def session_bar_starts(calendar: TradingCalendar, session: date) -> pd.DatetimeIndex:
    """Every regular-session 5-minute bar start for ``session`` (UTC), honouring early closes."""
    start = pd.Timestamp(calendar.session_open(session)).tz_convert(UTC)
    last = pd.Timestamp(calendar.session_close(session)).tz_convert(UTC) - timedelta(
        minutes=BAR_MINUTES
    )
    return pd.date_range(start, last, freq=f"{BAR_MINUTES}min", name=INTRADAY_INDEX_NAME)


def allowed_bar_starts(calendar: TradingCalendar, sessions: list[date]) -> pd.DatetimeIndex:
    """All regular-session bar starts for ``sessions`` (UTC); raises on a non-session day."""
    starts: list[pd.Timestamp] = []
    for session in sessions:
        if not calendar.is_session(session):
            raise DataSchemaError(f"{session} is not a session")
        starts.extend(session_bar_starts(calendar, session))
    return pd.DatetimeIndex(starts, name=INTRADAY_INDEX_NAME, dtype="datetime64[ns, UTC]")


def _check_index(index: pd.Index, symbol: str) -> pd.DatetimeIndex:
    if index.name != INTRADAY_INDEX_NAME:
        raise DataSchemaError(f"{symbol}: index must be named {INTRADAY_INDEX_NAME!r}")
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise DataSchemaError(f"{symbol}: index must be a timezone-aware DatetimeIndex")
    if str(index.tz) != "UTC" or index.hasnans:
        raise DataSchemaError(f"{symbol}: index must be UTC without NaT")
    if not index.is_monotonic_increasing or index.has_duplicates:
        raise DataSchemaError(f"{symbol}: bar starts must be strictly increasing")
    if len(index) and not (index == index.floor(f"{BAR_MINUTES}min")).all():
        raise DataSchemaError(f"{symbol}: bar starts must sit on the {BAR_MINUTES}-minute grid")
    return index


def _check_values(frame: pd.DataFrame, symbol: str) -> None:
    missing = [c for c in REQUIRED_BAR_COLUMNS if c not in frame.columns]
    if missing:
        raise DataSchemaError(f"{symbol}: missing columns {missing}")
    extra = [c for c in frame.columns if c not in (*REQUIRED_BAR_COLUMNS, *OPTIONAL_BAR_COLUMNS)]
    if extra:
        raise DataSchemaError(f"{symbol}: unexpected columns {extra}")
    values = frame[list(REQUIRED_BAR_COLUMNS)].to_numpy(dtype="float64")
    if not np.isfinite(values).all():
        raise DataSchemaError(f"{symbol}: OHLCV values must be finite")
    o, h, lo, c, v = values.T
    if (o <= 0).any() or (h <= 0).any() or (lo <= 0).any() or (c <= 0).any():
        raise DataSchemaError(f"{symbol}: prices must be positive")
    if (h < np.maximum(o, c)).any() or (lo > np.minimum(o, c)).any() or (h < lo).any():
        raise DataSchemaError(f"{symbol}: high/low do not bound open and close")
    if (v < 0).any():
        raise DataSchemaError(f"{symbol}: volume must not be negative")


def validate_intraday_frame(
    frame: pd.DataFrame, *, symbol: str = "?", calendar: TradingCalendar | None = None
) -> None:
    """Raise :class:`DataSchemaError` unless ``frame`` satisfies the 5-minute bar contract."""
    calendar = calendar if calendar is not None else TradingCalendar()
    index = _check_index(frame.index, symbol)
    _check_values(frame, symbol)
    sessions = sorted({ts.tz_convert("America/New_York").date() for ts in index})
    try:
        allowed = allowed_bar_starts(calendar, sessions)
    except DataSchemaError as exc:
        raise DataSchemaError(f"{symbol}: bars on a day that is not a session ({exc})") from None
    outside = index.difference(allowed)
    if len(outside):
        raise DataSchemaError(
            f"{symbol}: {len(outside)} bar(s) outside regular hours, first {outside[0]}"
        )


@dataclass(frozen=True)
class IntradaySeries:
    symbol: str
    frame: pd.DataFrame
    feed: DataFeed
    adjustment: Adjustment
    source: DataSource
    fetched_at: datetime


class IntradayBarStore:
    """One replace-only Parquet file per symbol/feed/adjustment under ``raw/bars/5min``."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, symbol: str, feed: DataFeed, adjustment: Adjustment) -> Path:
        if _SYMBOL.fullmatch(symbol) is None:
            raise ValueError("invalid symbol")
        return (
            self.root
            / "raw"
            / "bars"
            / f"{BAR_MINUTES}min"
            / f"feed={DataFeed(feed).value}"
            / f"adjustment={Adjustment(adjustment).value}"
            / f"{symbol}.parquet"
        )

    def replace(
        self,
        symbol: str,
        frame: pd.DataFrame,
        *,
        feed: DataFeed,
        adjustment: Adjustment,
        source: DataSource,
        fetched_at: datetime,
        calendar: TradingCalendar | None = None,
    ) -> Path:
        """Validate, then atomically replace the symbol's file. A refusal leaves it untouched."""
        feed, adjustment, source = DataFeed(feed), Adjustment(adjustment), DataSource(source)
        if (source == DataSource.SYNTHETIC) != (feed == DataFeed.SYNTHETIC):
            raise ValueError("synthetic source and feed must agree")
        if frame.empty:
            raise ValueError("cannot persist an empty series")
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        validate_intraday_frame(frame, symbol=symbol, calendar=calendar)
        out = frame.astype("float64").copy()
        out["feed"] = feed.value
        out["adjustment"] = adjustment.value
        out["source"] = source.value
        out["fetched_at"] = pd.Timestamp(fetched_at.astimezone(UTC))
        path = self._path(symbol, feed, adjustment)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".bars-", suffix=".tmp", dir=path.parent)
        os.close(descriptor)
        try:
            out.to_parquet(temporary, index=True)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return path

    def read(self, symbol: str, *, feed: DataFeed, adjustment: Adjustment) -> IntradaySeries | None:
        path = self._path(symbol, feed, adjustment)
        if not path.exists():
            return None
        raw = pd.read_parquet(path)
        if raw.empty or not set(_META_COLUMNS) <= set(raw.columns):
            raise DataSchemaError(f"{symbol}: intraday cache file is malformed")
        if raw["feed"].nunique() != 1 or raw["feed"].iloc[0] != DataFeed(feed).value:
            raise DataSchemaError(f"{symbol}: intraday cache feed mismatch")
        if (
            raw["adjustment"].nunique() != 1
            or raw["adjustment"].iloc[0] != Adjustment(adjustment).value
        ):
            raise DataSchemaError(f"{symbol}: intraday cache adjustment mismatch")
        if raw["source"].nunique() != 1 or raw["fetched_at"].nunique() != 1:
            raise DataSchemaError(f"{symbol}: intraday cache mixes fetches or sources")
        frame = raw.drop(columns=list(_META_COLUMNS))
        validate_intraday_frame(frame, symbol=symbol)
        return IntradaySeries(
            symbol,
            frame,
            DataFeed(feed),
            Adjustment(adjustment),
            DataSource(raw["source"].iloc[0]),
            pd.Timestamp(raw["fetched_at"].iloc[0]).to_pydatetime(),
        )

    def symbols(self, *, feed: DataFeed, adjustment: Adjustment) -> list[str]:
        folder = self._path("A", feed, adjustment).parent
        if not folder.is_dir():
            return []
        return sorted(p.stem for p in folder.glob("*.parquet") if _SYMBOL.fullmatch(p.stem))

    def summary(self, *, feed: DataFeed, adjustment: Adjustment) -> dict[str, dict[str, object]]:
        """Per symbol: first and last bar start (UTC), rows and fetch time."""
        out: dict[str, dict[str, object]] = {}
        for symbol in self.symbols(feed=feed, adjustment=adjustment):
            series = self.read(symbol, feed=feed, adjustment=adjustment)
            if series is None:
                continue
            out[symbol] = {
                "first_bar": series.frame.index[0].isoformat(),
                "last_bar": series.frame.index[-1].isoformat(),
                "rows": len(series.frame),
                "fetched_at": series.fetched_at.isoformat(),
            }
        return out
