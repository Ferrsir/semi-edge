"""Hermetic unit tests for ParquetBarStore merge, provenance and guard logic (tmp_path only)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from semiedge.data.schema import Adjustment, DataFeed, DataSchemaError, DataSource
from semiedge.data.store import SUMMARY_COLUMNS, ParquetBarStore, StoredSeries
from semiedge.data.synthetic import SyntheticMarketDataSource

T0 = datetime(2024, 12, 2, 1, tzinfo=UTC)
T1 = T0 + timedelta(days=1)


@pytest.fixture
def bars() -> pd.DataFrame:
    return SyntheticMarketDataSource().get_daily_bars(
        ["A"], date(2024, 11, 18), date(2024, 11, 29)
    )["A"]


@pytest.fixture
def store(tmp_path: Path) -> ParquetBarStore:
    return ParquetBarStore(tmp_path / "cache")


def _write(
    store: ParquetBarStore,
    frame: pd.DataFrame,
    fetched_at: datetime = T0,
    *,
    symbol: str = "A",
    feed: DataFeed = DataFeed.SIP,
    adjustment: Adjustment = Adjustment.SPLIT,
    source: DataSource = DataSource.ALPACA,
) -> Path:
    return store.write(
        symbol, frame, feed=feed, adjustment=adjustment, source=source, fetched_at=fetched_at
    )


def _read(store: ParquetBarStore, adjustment: Adjustment = Adjustment.SPLIT) -> StoredSeries | None:
    return store.read("A", feed=DataFeed.SIP, adjustment=adjustment)


def test_roundtrip_preserves_contract_and_provenance(store, bars):
    path = _write(store, bars, datetime(2024, 12, 1, 21, tzinfo=timezone(timedelta(hours=-5))))
    assert path.parent.name == "adjustment=split"
    assert path.parent.parent.name == "feed=sip"
    stored = _read(store)
    assert stored is not None
    pd.testing.assert_frame_equal(stored.frame, bars)
    assert (stored.symbol, stored.feed, stored.adjustment, stored.source) == (
        "A",
        DataFeed.SIP,
        Adjustment.SPLIT,
        DataSource.ALPACA,
    )
    # fetched_at is normalised to UTC.
    assert stored.fetched_at == datetime(2024, 12, 2, 2, tzinfo=UTC)
    assert stored.fetched_at.utcoffset() == timedelta(0)
    raw = pd.read_parquet(path)
    assert set(raw["feed"]) == {"sip"}
    assert set(raw["source"]) == {"alpaca"}


def test_no_temporary_files_left_after_write(store, bars):
    path = _write(store, bars)
    _write(store, bars, T1)
    assert sorted(p.name for p in path.parent.iterdir()) == ["A.parquet"]


def test_newer_fetch_wins_and_warns(store, bars, caplog):
    _write(store, bars)
    corrected = bars.copy()
    corrected.loc[corrected.index[-1], "high"] += 0.5
    with caplog.at_level(logging.WARNING, logger="semiedge.data.store"):
        _write(store, corrected, T1)
    assert "OHLCV correction for A" in caplog.text
    stored = _read(store)
    assert stored is not None
    assert stored.frame["high"].iloc[-1] == corrected["high"].iloc[-1]
    assert stored.fetched_at == T1


def test_identical_refetch_does_not_warn(store, bars, caplog):
    _write(store, bars)
    with caplog.at_level(logging.WARNING, logger="semiedge.data.store"):
        _write(store, bars, T1)
    assert "OHLCV correction" not in caplog.text


def test_input_frame_is_not_mutated(store, bars):
    before = bars.copy()
    _write(store, bars)
    pd.testing.assert_frame_equal(bars, before)


@pytest.mark.parametrize("adjustment", [Adjustment.SPLIT, Adjustment.DIVIDEND, Adjustment.ALL])
def test_disjoint_adjusted_append_rejected(store, bars, adjustment):
    _write(store, bars.iloc[:5], adjustment=adjustment)
    with pytest.raises(ValueError, match="overlap"):
        _write(store, bars.iloc[5:], T1, adjustment=adjustment)


def test_dividend_rebase_detected_on_anchor_close(store, bars):
    _write(store, bars.iloc[:6], adjustment=Adjustment.ALL)
    rebased = bars.iloc[4:].copy()
    rebased["close"] = rebased["close"] * 0.995  # cash dividend restates prior closes
    with pytest.raises(ValueError, match="adjustment basis changed at 2024-11-22"):
        _write(store, rebased, T1, adjustment=Adjustment.ALL)


def test_disjoint_raw_append_allowed(store, bars):
    _write(store, bars.iloc[:5], adjustment=Adjustment.RAW)
    _write(store, bars.iloc[5:], T1, adjustment=Adjustment.RAW)
    stored = _read(store, Adjustment.RAW)
    assert stored is not None
    pd.testing.assert_frame_equal(stored.frame, bars)


def test_series_are_partitioned_by_feed_and_adjustment(store, bars):
    _write(store, bars, feed=DataFeed.IEX)
    _write(store, bars * 2, feed=DataFeed.SIP)
    _write(store, bars * 3, feed=DataFeed.SIP, adjustment=Adjustment.RAW)
    iex = store.read("A", feed=DataFeed.IEX, adjustment=Adjustment.SPLIT)
    sip = store.read("A", feed=DataFeed.SIP, adjustment=Adjustment.SPLIT)
    assert iex is not None
    assert sip is not None
    pd.testing.assert_frame_equal(iex.frame, bars)
    pd.testing.assert_frame_equal(sip.frame, bars * 2)
    assert store.read("A", feed=DataFeed.DELAYED_SIP, adjustment=Adjustment.SPLIT) is None


def test_merge_rejects_different_source(store, bars):
    _write(store, bars, source=DataSource.FIXTURE)
    with pytest.raises(ValueError, match="different data sources"):
        _write(store, bars, T1, source=DataSource.ALPACA)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("provenance_column", "provenance columns"),
        ("empty", "empty series"),
        ("naive_timestamp", "timezone-aware"),
        ("bad_schema", "sorted ascending"),
    ],
)
def test_write_rejects_invalid_input(store, bars, change, message):
    fetched_at = T0
    if change == "provenance_column":
        bars = bars.assign(feed="sip")
    elif change == "empty":
        bars = bars.iloc[:0]
    elif change == "naive_timestamp":
        fetched_at = T0.replace(tzinfo=None)
    else:
        bars = bars.iloc[::-1]
    with pytest.raises((ValueError, DataSchemaError), match=message):
        _write(store, bars, fetched_at)
    assert not list((store.root).rglob("*.parquet"))


@pytest.mark.parametrize("symbol", ["", "a", "../A", "A/B", "TOOLONGSYMBOL", "A B", "1A"])
def test_symbol_must_be_a_safe_ticker(store, bars, symbol):
    with pytest.raises(ValueError, match="invalid symbol"):
        _write(store, bars, symbol=symbol)


def test_dotted_ticker_accepted(store, bars):
    assert _write(store, bars, symbol="BRK.B").name == "BRK.B.parquet"


def test_synthetic_requires_synthetic_root(tmp_path, bars):
    kwargs = {
        "feed": DataFeed.SYNTHETIC,
        "adjustment": Adjustment.SPLIT,
        "source": DataSource.SYNTHETIC,
        "fetched_at": T0,
    }
    with pytest.raises(ValueError, match="synthetic root"):
        ParquetBarStore(tmp_path / "real").write("A", bars, **kwargs)
    ParquetBarStore(tmp_path / "Synthetic" / "x").write("A", bars, **kwargs)
    with pytest.raises(ValueError, match="must agree"):
        ParquetBarStore(tmp_path / "synthetic").write(
            "A", bars, feed=DataFeed.SIP, adjustment=Adjustment.SPLIT,
            source=DataSource.SYNTHETIC, fetched_at=T0,
        )  # fmt: skip


def _corrupt(path: Path, mutate) -> None:
    raw = pd.read_parquet(path)
    mutate(raw).to_parquet(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw.drop(columns="source"), "missing provenance"),
        (lambda raw: raw.iloc[:0], "no rows"),
        (lambda raw: raw.assign(adjustment="raw"), "mismatched provenance"),
        (
            lambda raw: raw.assign(source=["alpaca", *["fixture"] * (len(raw) - 1)]),
            "mixed or mismatched",
        ),
        (lambda raw: raw.assign(fetched_at=raw["fetched_at"].dt.tz_localize(None)), "fetched_at"),
        (lambda raw: raw.iloc[::-1], "sorted ascending"),
    ],
)
def test_read_rejects_tampered_files(store, bars, mutate, message):
    path = _write(store, bars)
    _corrupt(path, mutate)
    with pytest.raises(DataSchemaError, match=message):
        _read(store)
    with pytest.raises(DataSchemaError, match=message):
        store.summary()
    # A tampered file also blocks merges instead of being silently rewritten.
    with pytest.raises(DataSchemaError, match=message):
        _write(store, bars, T1)


def test_real_root_rejects_synthetic_rows_on_read(store, bars):
    path = _write(store, bars)
    _corrupt(path, lambda raw: raw.assign(source="synthetic"))
    with pytest.raises(ValueError, match="synthetic"):
        _read(store)


def test_symbols_and_summary(store, bars):
    assert store.symbols(feed=DataFeed.SIP, adjustment=Adjustment.SPLIT) == []
    empty = store.summary()
    assert empty.empty
    assert list(empty.columns) == SUMMARY_COLUMNS
    _write(store, bars, symbol="NVDA")
    _write(store, bars.iloc[:4], symbol="AMD")
    _write(store, bars, symbol="AMD", feed=DataFeed.IEX, adjustment=Adjustment.RAW)
    assert store.symbols(feed=DataFeed.SIP, adjustment=Adjustment.SPLIT) == ["AMD", "NVDA"]
    assert store.symbols(feed=DataFeed.IEX, adjustment=Adjustment.RAW) == ["AMD"]
    summary = store.summary()
    assert list(summary.columns) == SUMMARY_COLUMNS
    rows = {(r.symbol, r.feed, r.adjustment): r for r in summary.itertuples()}
    assert set(rows) == {("AMD", "iex", "raw"), ("AMD", "sip", "split"), ("NVDA", "sip", "split")}
    amd = rows[("AMD", "sip", "split")]
    assert amd.rows == 4
    assert amd.source == "alpaca"
    assert pd.Timestamp(amd.first_session) == bars.index[0]
    assert pd.Timestamp(amd.last_session) == bars.index[3]
    assert pd.Timestamp(amd.last_fetched_at) == pd.Timestamp(T0)


def test_backfill_checks_latest_shared_session(store, bars):
    _write(store, bars.iloc[4:], T1)
    rebased = bars.iloc[:6].copy()
    rebased["close"] = rebased["close"] * 10.0  # pre-split basis
    # The stored 2024-11-22 row is newer than the backfill, so it is retained and anchors itself.
    with pytest.raises(ValueError, match="adjustment basis changed at 2024-11-22"):
        _write(store, rebased, T0)
    with pytest.raises(ValueError, match="unverified for retained sessions"):
        _write(store, bars.iloc[:6], T0)  # absent retained sessions are unverified
    _write(store, bars, T0)  # complete incoming coverage resolves the uncertainty
    stored = _read(store)
    assert stored is not None
    pd.testing.assert_frame_equal(stored.frame, bars)


def test_interior_gap_in_rebased_refetch_rejected(
    store: ParquetBarStore, bars: pd.DataFrame
) -> None:
    """F-012: a retained session inside the refetch range is anchored by its shared neighbours."""
    _write(store, bars)
    rebased = bars.drop(pd.Timestamp("2024-11-21")).copy()
    rebased["close"] = rebased["close"] / 10.0  # vendor restated history after a split
    with pytest.raises(ValueError, match="adjustment basis changed at 2024-11-20"):
        _write(store, rebased, T1)
    with pytest.raises(ValueError, match="unverified for retained sessions"):
        _write(store, bars.drop(pd.Timestamp("2024-11-21")), T1)
    stored = _read(store)
    assert stored is not None
    pd.testing.assert_frame_equal(stored.frame, bars)


def test_interior_gap_before_ex_date_rejected(store: ParquetBarStore, bars: pd.DataFrame) -> None:
    """A gap on the session before an ex-date is caught by the preceding shared session.

    The split restates only sessions before 2024-11-22, so the following anchor matches while the
    retained 2024-11-21 row is still on the old basis.
    """
    _write(store, bars)
    rebased = bars.drop(pd.Timestamp("2024-11-21")).copy()
    before_ex_date = rebased.index < pd.Timestamp("2024-11-22")
    rebased.loc[before_ex_date, "close"] = rebased.loc[before_ex_date, "close"] / 10.0
    with pytest.raises(ValueError, match="adjustment basis changed at 2024-11-20"):
        _write(store, rebased, T1)


def _two_sessions() -> pd.DataFrame:
    index = pd.DatetimeIndex(["2024-01-02", "2024-01-03"], name="session", dtype="datetime64[ns]")
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1_000.0},
        index=index,
        dtype="float64",
    )


def _rebased(frame: pd.DataFrame, factor: float) -> pd.DataFrame:
    out = frame.copy()
    out[["open", "high", "low", "close"]] = out[["open", "high", "low", "close"]] / factor
    out["volume"] = out["volume"] * factor
    return out


def _at(day: int) -> datetime:
    return datetime(2024, 1, day, tzinfo=UTC)


def test_newer_stored_row_cannot_splice_bases_with_older_refetch(store: ParquetBarStore) -> None:
    """F-024: a stored row kept by a newer fetched_at is anchored like any retained row."""
    bars = _two_sessions()
    _write(store, bars, _at(4))
    _corrupt(
        store._path("A", DataFeed.SIP, Adjustment.SPLIT),
        lambda raw: raw.assign(fetched_at=[_at(4), _at(6)]),
    )  # seed legacy per-row fetch metadata without accepting a partial adjusted write
    with pytest.raises(ValueError, match="adjustment basis changed at 2024-01-03"):
        _write(store, _rebased(bars, 10.0), _at(5))  # older than Jan 3's stored fetch
    stored = _read(store)
    assert stored is not None
    assert stored.frame["close"].tolist() == [100.0, 100.0]


def test_same_basis_out_of_order_refetch_is_accepted(store: ParquetBarStore) -> None:
    bars = _two_sessions()
    _write(store, bars, _at(4))
    _corrupt(
        store._path("A", DataFeed.SIP, Adjustment.SPLIT),
        lambda raw: raw.assign(fetched_at=[_at(4), _at(6)]),
    )
    _write(store, bars, _at(5))  # older than one stored row, same basis: harmless
    stored = _read(store)
    assert stored is not None
    pd.testing.assert_frame_equal(stored.frame, bars)


def test_fully_newer_store_ignores_older_rebased_refetch_rows(store: ParquetBarStore) -> None:
    """Every stored row is newer: they are all retained, so each must match the refetch."""
    bars = _two_sessions()
    _write(store, bars, _at(6))
    with pytest.raises(ValueError, match="adjustment basis changed"):
        _write(store, _rebased(bars, 10.0), _at(5))


def test_newer_rebased_refetch_replacing_everything_is_accepted(store: ParquetBarStore) -> None:
    bars = _two_sessions()
    _write(store, bars, _at(4))
    _write(store, _rebased(bars, 10.0), _at(5))  # full overlap, newer: nothing old retained
    stored = _read(store)
    assert stored is not None
    assert stored.frame["close"].tolist() == [10.0, 10.0]
