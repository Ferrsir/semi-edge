from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest
from alpaca.data.enums import Adjustment as AlpacaAdjustment
from alpaca.data.enums import DataFeed as AlpacaFeed

from semiedge.data.alpaca_source import AlpacaMarketDataSource, MarketDataError
from semiedge.data.schema import Adjustment, DataFeed, DataSource, validate_bar_frame
from semiedge.data.store import ParquetBarStore
from semiedge.data.synthetic import SyntheticMarketDataSource

pytestmark = pytest.mark.integration


@pytest.fixture
def bars():
    return SyntheticMarketDataSource().get_daily_bars(
        ["A"], date(2024, 11, 25), date(2024, 11, 29)
    )["A"]


def write(store, bars, timestamp=None, **kwargs):
    return store.write(
        "A",
        bars,
        feed=DataFeed.IEX,
        adjustment=Adjustment.SPLIT,
        source=DataSource.FIXTURE,
        fetched_at=timestamp or datetime(2024, 12, 1, tzinfo=UTC),
        **kwargs,
    )


def read(store):
    return store.read("A", feed=DataFeed.IEX, adjustment=Adjustment.SPLIT)


def test_roundtrip_merge_summary(tmp_path, bars, caplog):
    store = ParquetBarStore(tmp_path)
    path = write(store, bars)
    assert path == tmp_path / "raw/bars/daily/feed=iex/adjustment=split/A.parquet"
    result = read(store)
    pd.testing.assert_frame_equal(result.frame, bars)
    assert result.source == DataSource.FIXTURE
    changed = bars.iloc[-1:].copy()
    changed.loc[:, "close"] += 1
    write(store, changed, datetime(2024, 12, 2, tzinfo=UTC))
    assert "OHLCV correction" in caplog.text
    assert read(store).frame.close.iloc[-1] == changed.close.iloc[-1]
    write(store, bars)  # older fetch cannot undo correction
    assert read(store).frame.close.iloc[-1] == changed.close.iloc[-1]
    assert read(store).fetched_at == datetime(2024, 12, 2, tzinfo=UTC)
    assert store.symbols(feed=DataFeed.IEX, adjustment=Adjustment.SPLIT) == ["A"]
    summary = store.summary()
    assert summary.iloc[0]["symbol"] == "A"
    assert summary.iloc[0]["rows"] == len(bars)
    assert summary.iloc[0]["feed"] == "iex"
    raw = pd.read_parquet(path)
    assert {"feed", "adjustment", "source", "fetched_at"}.issubset(raw.columns)


@pytest.mark.parametrize("operation", ["to_parquet", "replace"])
def test_atomic_failure(tmp_path, bars, operation):
    store = ParquetBarStore(tmp_path)
    path = write(store, bars)
    original = path.read_bytes()
    target = (
        "pandas.DataFrame.to_parquet"
        if operation == "to_parquet"
        else "semiedge.data.store.os.replace"
    )

    def fail(*args, **kwargs):
        if operation == "to_parquet":
            Path(args[0]).write_bytes(b"incomplete parquet bytes")
        raise OSError("simulated failure")

    with (
        patch(target, side_effect=fail),
        pytest.raises(OSError, match="simulated failure"),
    ):
        write(store, bars * 2, datetime(2024, 12, 2, tzinfo=UTC))
    assert path.read_bytes() == original
    assert list(path.parent.glob("*.tmp")) == []


def test_synthetic_guard(tmp_path, bars):
    kwargs = {
        "feed": DataFeed.SYNTHETIC,
        "adjustment": Adjustment.SPLIT,
        "source": DataSource.SYNTHETIC,
        "fetched_at": datetime.now(UTC),
    }
    with pytest.raises(ValueError, match="synthetic root"):
        ParquetBarStore(tmp_path).write("A", bars, **kwargs)
    store = ParquetBarStore(tmp_path / "synthetic")
    store.write("A", bars, **kwargs)
    assert len(store.summary()) == 1
    kwargs["feed"] = DataFeed.IEX
    with pytest.raises(ValueError, match="must agree"):
        store.write("A", bars, **kwargs)


@pytest.mark.parametrize("bad_feed", ["sip", None])
def test_store_rejects_mixed_provenance(tmp_path, bars, bad_feed):
    store = ParquetBarStore(tmp_path)
    path = write(store, bars)
    raw = pd.read_parquet(path)
    raw.loc[raw.index[0], "feed"] = bad_feed
    raw.to_parquet(path)
    with pytest.raises(ValueError, match="provenance"):
        read(store)
    with pytest.raises(ValueError, match="provenance"):
        store.summary()


def test_store_merges_disjoint_sessions_and_equal_timestamps(tmp_path, bars):
    store = ParquetBarStore(tmp_path)
    write(store, bars.iloc[:2])
    write(store, bars.iloc[2:])
    pd.testing.assert_frame_equal(read(store).frame, bars)
    write(store, bars * 2)
    # Equal fetch timestamps do not supersede the existing observations.
    pd.testing.assert_frame_equal(read(store).frame, bars)


def test_store_invalid_inputs(tmp_path, bars):
    store = ParquetBarStore(tmp_path)
    assert read(store) is None
    assert store.summary().empty
    with pytest.raises(ValueError, match="timezone-aware"):
        write(store, bars, datetime(2024, 1, 1, tzinfo=UTC).replace(tzinfo=None))
    with pytest.raises(ValueError, match="empty"):
        write(store, bars.iloc[:0])
    with pytest.raises(ValueError, match="symbol"):
        store.read("../A", feed=DataFeed.IEX, adjustment=Adjustment.SPLIT)


@pytest.mark.parametrize("feed", [DataFeed.IEX, DataFeed.SIP, DataFeed.DELAYED_SIP])
@pytest.mark.parametrize("adjustment", list(Adjustment))
def test_alpaca_conversion(feed, adjustment):
    index = pd.MultiIndex.from_tuples(
        [
            ("A", pd.Timestamp("2024-03-08T05:00:00Z")),
            ("A", pd.Timestamp("2024-03-11T04:00:00Z")),
        ],
        names=["symbol", "timestamp"],
    )
    raw = pd.DataFrame(
        {
            "open": [10, 11],
            "high": [12, 13],
            "low": [9, 10],
            "close": [11, 12],
            "volume": [100, 200],
            "vwap": [10, 11],
        },
        index=index,
    )
    client = Mock()
    client.get_stock_bars.return_value.df = raw
    source = AlpacaMarketDataSource(client, feed, adjustment)
    result = source.get_daily_bars(["A", "MISSING"], date(2024, 3, 8), date(2024, 3, 11))
    validate_bar_frame(result["A"])
    assert result["A"].index.tolist() == [pd.Timestamp("2024-03-08"), pd.Timestamp("2024-03-11")]
    assert result["A"].close.tolist() == [11.0, 12.0]
    assert result["MISSING"].empty
    request = client.get_stock_bars.call_args.args[0]
    assert request.feed == AlpacaFeed(feed.value)
    assert request.adjustment == AlpacaAdjustment(adjustment.value)


def test_alpaca_errors():
    client = Mock()
    client.get_stock_bars.side_effect = RuntimeError("private vendor detail")
    source = AlpacaMarketDataSource(client, DataFeed.IEX, Adjustment.SPLIT)
    with pytest.raises(MarketDataError, match="request or response failed") as caught:
        source.get_daily_bars(["A"], date(2024, 1, 1), date(2024, 2, 1))
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("bad_response", ["naive", "duplicate", "missing_column", "raw"])
def test_alpaca_rejects_invalid_response(bad_response):
    timestamps = [pd.Timestamp("2024-03-11T04:00:00Z")]
    if bad_response == "naive":
        timestamps = [timestamps[0].tz_localize(None)]
    if bad_response == "duplicate":
        timestamps *= 2
    raw = pd.DataFrame(
        {c: [1] * len(timestamps) for c in ["open", "high", "low", "close", "volume"]},
        index=pd.MultiIndex.from_product([["A"], timestamps], names=["symbol", "timestamp"]),
    )
    if bad_response == "missing_column":
        raw = raw.drop(columns="close")
    client = Mock()
    client.get_stock_bars.return_value.df = raw
    if bad_response == "raw":
        client.get_stock_bars.return_value = {"bars": []}
    with pytest.raises(MarketDataError, match="request or response failed"):
        AlpacaMarketDataSource(client, DataFeed.IEX, Adjustment.SPLIT).get_daily_bars(
            ["A"], date(2024, 3, 11), date(2024, 3, 11)
        )


def test_alpaca_uses_new_york_date():
    client = Mock()
    client.get_stock_bars.return_value.df = pd.DataFrame(
        {c: [1] for c in ["open", "high", "low", "close", "volume"]},
        index=pd.MultiIndex.from_tuples(
            [("A", pd.Timestamp("2024-03-12T01:00:00Z"))], names=["symbol", "timestamp"]
        ),
    )
    frame = AlpacaMarketDataSource(client, DataFeed.IEX, Adjustment.RAW).get_daily_bars(
        ["A"], date(2024, 3, 11), date(2024, 3, 11)
    )["A"]
    assert frame.index.tolist() == [pd.Timestamp("2024-03-11")]


def test_alpaca_credential_factory_is_market_data_only():
    with patch("semiedge.data.alpaca_source.StockHistoricalDataClient") as client:
        source = AlpacaMarketDataSource.from_credentials(
            "fixture-key", "fixture-secret", DataFeed.IEX, Adjustment.SPLIT
        )
        client.assert_called_once_with("fixture-key", "fixture-secret")
        assert source.client is client.return_value


def test_sip_clamp(caplog):
    caplog.set_level("INFO")
    client = Mock()
    client.get_stock_bars.return_value.df = pd.DataFrame()
    source = AlpacaMarketDataSource(client, DataFeed.SIP, Adjustment.SPLIT)
    now = datetime(2024, 11, 29, 18, tzinfo=UTC)
    with patch("semiedge.data.alpaca_source.datetime") as clock:
        clock.now.return_value = now
        clock.combine = datetime.combine
        source.get_daily_bars(["A"], date(2024, 11, 25), date(2024, 12, 1))
    # alpaca-py normalizes request datetimes to naive UTC for serialization.
    assert client.get_stock_bars.call_args.args[0].end == (now - timedelta(minutes=16)).replace(
        tzinfo=None
    )
    assert "SIP end clamped" in caplog.text
