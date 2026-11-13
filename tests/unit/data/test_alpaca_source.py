"""Offline unit tests for the Alpaca daily-bar adapter (fake client; no network, no credentials)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pandas as pd
import pytest
from alpaca.data.enums import Adjustment as AlpacaAdjustment
from alpaca.data.enums import DataFeed as AlpacaFeed
from alpaca.data.timeframe import TimeFrame

import semiedge.data.alpaca_source as alpaca_module
from semiedge.data.alpaca_source import AlpacaMarketDataSource, MarketDataError
from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import Adjustment, DataFeed, DataSource, validate_bar_frame
from semiedge.data.source import MarketDataSource

COLUMNS = ["open", "high", "low", "close", "volume", "trade_count", "vwap"]


def _vendor_frame(rows: list[tuple[str, str]], **extra: list[Any]) -> pd.DataFrame:
    """Shape of ``BarSet.df``: MultiIndex (symbol, tz-aware UTC timestamp)."""
    index = pd.MultiIndex.from_tuples(
        [(symbol, pd.Timestamp(ts)) for symbol, ts in rows], names=["symbol", "timestamp"]
    )
    n = len(rows)
    data: dict[str, list[Any]] = {
        "open": [10.0 + i for i in range(n)],
        "high": [12.0 + i for i in range(n)],
        "low": [9.0 + i for i in range(n)],
        "close": [11.0 + i for i in range(n)],
        "volume": [1000 + i for i in range(n)],
        "trade_count": [10 + i for i in range(n)],
        "vwap": [10.5 + i for i in range(n)],
    }
    data.update(extra)
    return pd.DataFrame(data, index=index)


def _client(frame: pd.DataFrame) -> Mock:
    client = Mock(spec=["get_stock_bars"])
    client.get_stock_bars.return_value = Mock(df=frame)
    return client


class _Clock:
    """Stand-in for the adapter module's ``datetime`` name with a fixed ``now``."""

    def __init__(self, now: datetime) -> None:
        self._now = now
        self.combine = datetime.combine

    def now(self, tz: Any = None) -> datetime:
        assert tz is UTC
        return self._now


def _fetch(
    source: AlpacaMarketDataSource,
    symbols: list[str],
    start: date,
    end: date,
    now: datetime | None = None,
) -> dict[str, pd.DataFrame]:
    if now is None:
        return source.get_daily_bars(symbols, start, end)
    with patch("semiedge.data.alpaca_source.datetime", _Clock(now)):
        return source.get_daily_bars(symbols, start, end)


def test_satisfies_market_data_source_protocol() -> None:
    source: MarketDataSource = AlpacaMarketDataSource(
        _client(pd.DataFrame()), DataFeed.SIP, Adjustment.SPLIT
    )
    assert source.source is DataSource.ALPACA
    assert source.feed is DataFeed.SIP
    assert source.adjustment is Adjustment.SPLIT


def test_request_shape_and_symbol_split() -> None:
    frame = _vendor_frame(
        [
            ("AMD", "2024-03-08T05:00:00Z"),  # EST midnight
            ("AMD", "2024-03-11T04:00:00Z"),  # EDT midnight (after the DST change)
            ("NVDA", "2024-03-11T04:00:00Z"),
        ]
    )
    client = _client(frame)
    source = AlpacaMarketDataSource(client, DataFeed.IEX, Adjustment.ALL)
    result = _fetch(source, ["NVDA", "AMD", "NONE"], date(2024, 3, 8), date(2024, 3, 11))
    assert set(result) == {"NVDA", "AMD", "NONE"}
    assert list(result["AMD"].index.date) == [date(2024, 3, 8), date(2024, 3, 11)]
    assert list(result["NVDA"].index.date) == [date(2024, 3, 11)]
    assert result["NONE"].empty
    for symbol, bars in result.items():
        validate_bar_frame(bars, symbol=symbol)
    assert list(result["AMD"].columns) == COLUMNS
    request = client.get_stock_bars.call_args.args[0]
    assert request.symbol_or_symbols == ["NVDA", "AMD", "NONE"]
    assert request.timeframe.value == TimeFrame.Day.value
    assert request.feed == AlpacaFeed.IEX
    assert request.adjustment == AlpacaAdjustment.ALL
    # Request bounds are New York midnights, serialized by alpaca-py as naive UTC.
    assert request.start == datetime(2024, 3, 8, 5)  # noqa: DTZ001
    assert request.end == datetime(2024, 3, 12, 4)  # noqa: DTZ001


def test_bars_outside_requested_dates_are_dropped() -> None:
    frame = _vendor_frame(
        [
            ("A", "2024-03-07T05:00:00Z"),
            ("A", "2024-03-08T05:00:00Z"),
            ("A", "2024-03-12T04:00:00Z"),
        ]
    )
    result = _fetch(
        AlpacaMarketDataSource(_client(frame), DataFeed.IEX, Adjustment.RAW),
        ["A"],
        date(2024, 3, 8),
        date(2024, 3, 11),
    )
    assert list(result["A"].index.date) == [date(2024, 3, 8)]


def test_unsorted_vendor_rows_are_sorted() -> None:
    frame = _vendor_frame([("A", "2024-03-11T04:00:00Z"), ("A", "2024-03-08T05:00:00Z")])
    result = _fetch(
        AlpacaMarketDataSource(_client(frame), DataFeed.IEX, Adjustment.RAW),
        ["A"],
        date(2024, 3, 8),
        date(2024, 3, 11),
    )
    assert list(result["A"].index.date) == [date(2024, 3, 8), date(2024, 3, 11)]


def test_empty_symbol_list_makes_no_request() -> None:
    client = _client(pd.DataFrame())
    source = AlpacaMarketDataSource(client, DataFeed.IEX, Adjustment.RAW)
    assert source.get_daily_bars([], date(2024, 3, 8), date(2024, 3, 11)) == {}
    client.get_stock_bars.assert_not_called()


def test_empty_vendor_response_maps_every_symbol_to_empty_frame() -> None:
    result = _fetch(
        AlpacaMarketDataSource(_client(pd.DataFrame()), DataFeed.IEX, Adjustment.RAW),
        ["A", "B"],
        date(2024, 3, 8),
        date(2024, 3, 11),
    )
    assert set(result) == {"A", "B"}
    for bars in result.values():
        assert bars.empty
        validate_bar_frame(bars)


def test_start_after_end_rejected_before_any_request() -> None:
    client = _client(pd.DataFrame())
    source = AlpacaMarketDataSource(client, DataFeed.IEX, Adjustment.RAW)
    with pytest.raises(ValueError, match="start"):
        source.get_daily_bars(["A"], date(2024, 3, 12), date(2024, 3, 11))
    client.get_stock_bars.assert_not_called()


def test_synthetic_feed_cannot_be_requested_from_vendor() -> None:
    with pytest.raises(ValueError, match="synthetic"):
        AlpacaMarketDataSource(_client(pd.DataFrame()), DataFeed.SYNTHETIC, Adjustment.RAW)


@pytest.mark.parametrize(
    "fault",
    ["exception", "raw_dict", "naive_timestamps", "non_numeric", "duplicate_session", "no_close"],
)
def test_untrusted_responses_fail_closed_without_vendor_text(fault: str) -> None:
    secret = "fixture-secret-in-vendor-message"
    frame = _vendor_frame([("A", "2024-03-08T05:00:00Z"), ("A", "2024-03-11T04:00:00Z")])
    client = _client(frame)
    if fault == "exception":
        client.get_stock_bars.side_effect = RuntimeError(secret)
    elif fault == "raw_dict":
        client.get_stock_bars.return_value = {"bars": {"A": []}}
    elif fault == "naive_timestamps":
        client.get_stock_bars.return_value = Mock(df=_vendor_frame([("A", "2024-03-08T05:00")]))
    elif fault == "non_numeric":
        client.get_stock_bars.return_value = Mock(
            df=_vendor_frame([("A", "2024-03-08T05:00:00Z")], close=[secret])
        )
    elif fault == "duplicate_session":
        client.get_stock_bars.return_value = Mock(
            df=_vendor_frame([("A", "2024-03-08T05:00:00Z"), ("A", "2024-03-08T14:30:00Z")])
        )
    else:
        client.get_stock_bars.return_value = Mock(df=frame.drop(columns="close"))
    source = AlpacaMarketDataSource(client, DataFeed.IEX, Adjustment.RAW)
    with pytest.raises(MarketDataError) as caught:
        _fetch(source, ["A"], date(2024, 3, 8), date(2024, 3, 11))
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_sip_end_is_clamped_to_sixteen_minutes_ago(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="semiedge.data.alpaca_source")
    now = datetime(2024, 3, 12, 14, tzinfo=UTC)
    client = _client(pd.DataFrame())
    _fetch(
        AlpacaMarketDataSource(client, DataFeed.SIP, Adjustment.SPLIT),
        ["A"],
        date(2024, 3, 8),
        date(2024, 3, 12),
        now,
    )
    expected = (now - timedelta(minutes=16)).replace(tzinfo=None)
    assert client.get_stock_bars.call_args.args[0].end == expected
    assert "SIP end clamped" in caplog.text


@pytest.mark.parametrize("feed", [DataFeed.IEX, DataFeed.DELAYED_SIP])
def test_non_sip_feeds_are_not_clamped(feed: DataFeed) -> None:
    client = _client(pd.DataFrame())
    _fetch(
        AlpacaMarketDataSource(client, feed, Adjustment.SPLIT),
        ["A"],
        date(2024, 3, 8),
        date(2024, 3, 12),
        datetime(2024, 3, 12, 14, tzinfo=UTC),
    )
    assert client.get_stock_bars.call_args.args[0].end == datetime(2024, 3, 13, 4)  # noqa: DTZ001


def test_sip_window_entirely_inside_delay_returns_empty_without_request() -> None:
    # 00:10 EDT on 2024-03-12: now - 16 minutes precedes the requested start (midnight ET).
    client = _client(pd.DataFrame())
    result = _fetch(
        AlpacaMarketDataSource(client, DataFeed.SIP, Adjustment.SPLIT),
        ["A"],
        date(2024, 3, 12),
        date(2024, 3, 12),
        datetime(2024, 3, 12, 4, 10, tzinfo=UTC),
    )
    assert result["A"].empty
    client.get_stock_bars.assert_not_called()


@pytest.mark.parametrize("feed", [DataFeed.IEX, DataFeed.SIP, DataFeed.DELAYED_SIP])
def test_in_progress_session_bar_is_not_returned(feed: DataFeed) -> None:
    """A daily bar for a session that has not closed is partial and must never be returned.

    Otherwise it is persisted as if complete and survives any later fetch that does not
    re-cover that session.
    """
    # 2024-03-12 15:00 UTC = 11:00 EDT: the 2024-03-12 session is in progress.
    frame = _vendor_frame([("A", "2024-03-11T04:00:00Z"), ("A", "2024-03-12T04:00:00Z")])
    result = _fetch(
        AlpacaMarketDataSource(_client(frame), feed, Adjustment.SPLIT),
        ["A"],
        date(2024, 3, 11),
        date(2024, 3, 12),
        datetime(2024, 3, 12, 15, tzinfo=UTC),
    )
    assert list(result["A"].index.date) == [date(2024, 3, 11)]


def test_early_close_session_is_returned_once_closed() -> None:
    # 2024-11-29 (day after Thanksgiving) closes at 13:00 ET = 18:00 UTC.
    frame = _vendor_frame([("A", "2024-11-29T05:00:00Z")])
    source = AlpacaMarketDataSource(
        _client(frame), DataFeed.IEX, Adjustment.SPLIT, calendar=TradingCalendar()
    )
    day = date(2024, 11, 29)
    before = _fetch(source, ["A"], day, day, datetime(2024, 11, 29, 17, 59, tzinfo=UTC))
    after = _fetch(source, ["A"], day, day, datetime(2024, 11, 29, 18, tzinfo=UTC))
    assert before["A"].empty
    assert list(after["A"].index.date) == [day]


def test_credential_factory_builds_only_a_market_data_client() -> None:
    with (
        patch("semiedge.data.alpaca_source.StockHistoricalDataClient") as data_client,
        patch("alpaca.trading.client.TradingClient") as trading_client,
    ):
        source = AlpacaMarketDataSource.from_credentials(
            "fixture-key", "fixture-secret", DataFeed.SIP, Adjustment.SPLIT
        )
    data_client.assert_called_once_with("fixture-key", "fixture-secret")
    trading_client.assert_not_called()
    assert source.client is data_client.return_value
    assert (source.feed, source.adjustment) == (DataFeed.SIP, Adjustment.SPLIT)


def test_adapter_module_never_references_the_trading_api() -> None:
    text = Path(alpaca_module.__file__).read_text(encoding="utf-8")
    assert "alpaca.trading" not in text
    assert "TradingClient" not in text
