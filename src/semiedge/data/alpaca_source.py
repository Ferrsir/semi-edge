"""Read-only Alpaca historical market data adapter; no trading client is created."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from alpaca.data.enums import Adjustment as AlpacaAdjustment
from alpaca.data.enums import DataFeed as AlpacaFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from semiedge.data.calendar import TradingCalendar
from semiedge.data.intraday import (
    BAR_MINUTES,
    INTRADAY_INDEX_NAME,
    allowed_bar_starts,
    validate_intraday_frame,
)
from semiedge.data.schema import (
    OPTIONAL_BAR_COLUMNS,
    REQUIRED_BAR_COLUMNS,
    Adjustment,
    DataFeed,
    DataSource,
    empty_bar_frame,
    validate_bar_frame,
)

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


class MarketDataError(RuntimeError):
    """The vendor request or response could not be trusted in full."""


class AlpacaMarketDataSource:
    source = DataSource.ALPACA

    def __init__(
        self,
        client: StockHistoricalDataClient,
        feed: DataFeed,
        adjustment: Adjustment,
        *,
        calendar: TradingCalendar | None = None,
    ) -> None:
        self.client = client
        self.feed = DataFeed(feed)
        self.adjustment = Adjustment(adjustment)
        self._feed = AlpacaFeed(feed.value)
        self._adjustment = AlpacaAdjustment(adjustment.value)
        # Sessions that have not closed yet are dropped: their daily bar is partial.
        self.calendar = calendar if calendar is not None else TradingCalendar()

    @classmethod
    def from_credentials(
        cls, api_key: str, secret_key: str, feed: DataFeed, adjustment: Adjustment
    ) -> AlpacaMarketDataSource:
        return cls(StockHistoricalDataClient(api_key, secret_key), feed, adjustment)

    def get_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]:
        if start > end:
            raise ValueError("start must not exceed end")
        result = {symbol: empty_bar_frame() for symbol in symbols}
        if not symbols:
            return result
        now = datetime.now(UTC)
        request_start = datetime.combine(start, time.min, ET)
        request_end = datetime.combine(end + timedelta(days=1), time.min, ET)
        if self.feed == DataFeed.SIP:
            cutoff = now - timedelta(minutes=16)
            if request_end > cutoff:
                logger.info("SIP end clamped from %s to %s", request_end, cutoff)
                request_end = cutoff
        if request_end < request_start:
            return result
        try:
            response = self.client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=list(symbols),
                    start=request_start,
                    end=request_end,
                    timeframe=TimeFrame.Day,
                    feed=self._feed,
                    adjustment=self._adjustment,
                )
            )
            if isinstance(response, dict):
                raise ValueError("raw vendor responses are unsupported")
            bars: pd.DataFrame = response.df
            if bars.empty:
                return result
            last_closed = self.calendar.last_completed_session(now, 0)
            present = set(bars.index.get_level_values("symbol"))
            for symbol in symbols:
                if symbol in present:
                    result[symbol] = _session_frame(bars, symbol, start, end, last_closed)
        except Exception:
            # Vendor exceptions can embed URLs/credentials; do not propagate their text.
            raise MarketDataError("Daily bar request or response failed") from None
        return result

    def get_intraday_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]:
        """Regular-session 5-minute bars for sessions ``start..end`` (#118), one request per symbol.

        Pre- and post-market bars are dropped; a bar that has not finished is never returned.
        """
        if start > end:
            raise ValueError("start must not exceed end")
        sessions = self.calendar.sessions_in_range(start, end)
        if not sessions or not symbols:
            return {symbol: _empty_intraday() for symbol in symbols}
        now = datetime.now(UTC)
        request_start = self.calendar.session_open(sessions[0])
        request_end = self.calendar.session_close(sessions[-1])
        if self.feed == DataFeed.SIP:
            request_end = min(request_end, now - timedelta(minutes=16))
        else:
            request_end = min(request_end, now)
        allowed = allowed_bar_starts(self.calendar, sessions)
        # A bar is complete once its end has passed the request's (clamped) end.
        allowed = allowed[allowed + timedelta(minutes=BAR_MINUTES) <= pd.Timestamp(request_end)]
        result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            if request_end <= request_start:
                result[symbol] = _empty_intraday()
                continue
            try:
                response = self.client.get_stock_bars(
                    StockBarsRequest(
                        symbol_or_symbols=[symbol],
                        start=request_start,
                        end=request_end,
                        timeframe=TimeFrame(BAR_MINUTES, TimeFrameUnit.Minute),
                        feed=self._feed,
                        adjustment=self._adjustment,
                    )
                )
                if isinstance(response, dict):
                    raise ValueError("raw vendor responses are unsupported")
                bars: pd.DataFrame = response.df
                result[symbol] = _intraday_frame(bars, symbol, allowed, self.calendar)
            except Exception:
                # Vendor exceptions can embed URLs/credentials; do not propagate their text.
                raise MarketDataError("Intraday bar request or response failed") from None
        return result


def _empty_intraday() -> pd.DataFrame:
    return pd.DataFrame(
        columns=list(REQUIRED_BAR_COLUMNS),
        index=pd.DatetimeIndex([], tz=UTC, name=INTRADAY_INDEX_NAME),
        dtype="float64",
    )


def _intraday_frame(
    bars: pd.DataFrame, symbol: str, allowed: pd.DatetimeIndex, calendar: TradingCalendar
) -> pd.DataFrame:
    """One symbol's vendor rows as a validated frame of complete regular-session bars."""
    if bars.empty or symbol not in set(bars.index.get_level_values("symbol")):
        return _empty_intraday()
    frame = bars.xs(symbol, level="symbol").copy()
    timestamps = pd.DatetimeIndex(frame.index)
    if timestamps.tz is None:
        raise ValueError("vendor timestamps must be timezone-aware")
    frame.index = pd.DatetimeIndex(timestamps.tz_convert(UTC), name=INTRADAY_INDEX_NAME)
    kept = frame.loc[frame.index.isin(allowed)]  # pre/post-market and unfinished bars out
    columns = [c for c in (*REQUIRED_BAR_COLUMNS, *OPTIONAL_BAR_COLUMNS) if c in kept]
    out = pd.DataFrame(kept.loc[:, columns]).astype("float64").sort_index()
    validate_intraday_frame(out, symbol=symbol, calendar=calendar)
    return out


def _session_frame(
    bars: pd.DataFrame, symbol: str, start: date, end: date, last_closed: date
) -> pd.DataFrame:
    """Convert one symbol's vendor rows to a validated bar frame of closed sessions in range."""
    frame = bars.xs(symbol, level="symbol").copy()
    timestamps = pd.DatetimeIndex(frame.index)
    if timestamps.tz is None:
        raise ValueError("vendor timestamps must be timezone-aware")
    frame.index = pd.DatetimeIndex(
        timestamps.tz_convert(ET).tz_localize(None).normalize(),
        name="session",
        dtype="datetime64[ns]",
    )
    columns = [c for c in (*REQUIRED_BAR_COLUMNS, *OPTIONAL_BAR_COLUMNS) if c in frame]
    # A daily bar for a session that has not closed is partial; never return it as a bar.
    in_progress = frame.index.date > last_closed
    if in_progress.any():
        logger.info("Dropped %d unclosed session bar(s) for %s", in_progress.sum(), symbol)
    keep = (frame.index.date >= start) & (frame.index.date <= end) & ~in_progress
    frame = frame.loc[keep, columns].astype("float64").sort_index()
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("vendor bars must be a DataFrame")
    validate_bar_frame(frame, symbol=symbol)
    return frame
