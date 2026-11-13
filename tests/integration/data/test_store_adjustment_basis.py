"""Regression tests: adjusted series must never mix adjustment bases (split/dividend re-basing)."""

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from semiedge.data.schema import Adjustment, DataFeed, DataSource
from semiedge.data.store import ParquetBarStore
from semiedge.data.synthetic import SyntheticMarketDataSource

pytestmark = pytest.mark.integration

FIRST = datetime(2024, 6, 3, tzinfo=UTC)
SECOND = datetime(2024, 6, 12, tzinfo=UTC)


@pytest.fixture
def bars():
    return SyntheticMarketDataSource().get_daily_bars(["A"], date(2024, 5, 20), date(2024, 6, 14))[
        "A"
    ]


def _rebased(frame: pd.DataFrame, ratio: float) -> pd.DataFrame:
    """Simulate a vendor re-adjusting history after a 1:ratio split."""
    out = frame.copy()
    for column in ("open", "high", "low", "close"):
        out[column] = out[column] / ratio
    out["volume"] = out["volume"] * ratio
    return out


def _write(store, frame, fetched_at, adjustment=Adjustment.SPLIT):
    return store.write(
        "A",
        frame,
        feed=DataFeed.SIP,
        adjustment=adjustment,
        source=DataSource.ALPACA,
        fetched_at=fetched_at,
    )


def _read(store, adjustment=Adjustment.SPLIT):
    return store.read("A", feed=DataFeed.SIP, adjustment=adjustment)


def test_non_overlapping_adjusted_append_is_rejected(tmp_path, bars):
    """An append with no overlap cannot prove the stored rows share the vendor's current basis."""
    store = ParquetBarStore(tmp_path)
    _write(store, bars.loc[:"2024-05-31"], FIRST)
    # After a 10:1 split the vendor returns post-split prices; the stored rows are pre-split.
    with pytest.raises(ValueError, match="overlap"):
        _write(store, _rebased(bars.loc["2024-06-03":], 10.0), SECOND)
    # The stored series is unchanged by the rejected write.
    pd.testing.assert_frame_equal(_read(store).frame, bars.loc[:"2024-05-31"])


def test_rebased_partial_refetch_is_rejected(tmp_path, bars):
    store = ParquetBarStore(tmp_path)
    _write(store, bars.loc[:"2024-06-07"], FIRST)
    with pytest.raises(ValueError, match="adjustment basis"):
        _write(store, _rebased(bars.loc["2024-06-05":], 10.0), SECOND)
    pd.testing.assert_frame_equal(_read(store).frame, bars.loc[:"2024-06-07"])


def test_rebased_full_refetch_replaces_history(tmp_path, bars):
    store = ParquetBarStore(tmp_path)
    _write(store, bars.loc[:"2024-06-07"], FIRST)
    rebased = _rebased(bars, 10.0)
    _write(store, rebased, SECOND)
    pd.testing.assert_frame_equal(_read(store).frame, rebased)


def test_adjusted_partial_append_requires_complete_history(tmp_path, bars, caplog):
    store = ParquetBarStore(tmp_path)
    _write(store, bars.loc[:"2024-06-07"], FIRST)
    tail = bars.loc["2024-06-07":].copy()
    tail.loc[pd.Timestamp("2024-06-14"), "volume"] += 1.0  # ordinary correction, newest session
    with pytest.raises(ValueError, match="unverified for retained sessions"):
        _write(store, tail, SECOND)
    expected = pd.concat([bars.loc[:"2024-06-06"], tail])
    _write(store, expected, SECOND)
    pd.testing.assert_frame_equal(_read(store).frame, expected)
    assert "OHLCV correction" not in caplog.text  # shared session 2024-06-07 was identical


def test_backfill_before_stored_range_checks_latest_shared_session(tmp_path, bars):
    store = ParquetBarStore(tmp_path)
    _write(store, _rebased(bars.loc["2024-06-03":], 10.0), SECOND)
    with pytest.raises(ValueError, match="adjustment basis"):
        _write(store, bars.loc[:"2024-06-04"], FIRST)


def test_raw_series_keeps_disjoint_appends(tmp_path, bars):
    """Unadjusted prices have no basis to drift, so disjoint appends stay allowed."""
    store = ParquetBarStore(tmp_path)
    _write(store, bars.loc[:"2024-05-31"], FIRST, Adjustment.RAW)
    _write(store, bars.loc["2024-06-03":], SECOND, Adjustment.RAW)
    pd.testing.assert_frame_equal(_read(store, Adjustment.RAW).frame, bars)


def test_rebased_refetch_with_interior_gap_is_rejected(tmp_path, bars):
    """F-012: a stored session missing from a restated refetch must not keep the old basis."""
    store = ParquetBarStore(tmp_path)
    _write(store, bars, FIRST)
    rebased = _rebased(bars, 10.0).drop(pd.Timestamp("2024-06-05"))
    with pytest.raises(ValueError, match="adjustment basis changed at 2024-06-04"):
        _write(store, rebased, SECOND)
    pd.testing.assert_frame_equal(_read(store).frame, bars)


def test_same_basis_refetch_with_interior_gap_is_unverified(tmp_path, bars):
    store = ParquetBarStore(tmp_path)
    _write(store, bars, FIRST)
    with pytest.raises(ValueError, match="unverified for retained sessions"):
        _write(store, bars.drop(pd.Timestamp("2024-06-05")), SECOND)
    pd.testing.assert_frame_equal(_read(store).frame, bars)


def test_opposite_splits_cannot_hide_changed_basis_in_retained_gap(tmp_path):
    """F-036: equal anchors cannot prove the basis between opposite corporate actions."""
    index = pd.DatetimeIndex(
        ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
        dtype="datetime64[ns]",
        name="session",
    )
    original = pd.DataFrame(
        {c: [100.0] * 4 for c in ("open", "high", "low", "close", "volume")}, index=index
    )
    restated = original.copy()
    # Reverse 1:2 Jan3, forward 2:1 Jan4: net factor one before both events.
    restated.loc[index[1], ["open", "high", "low", "close"]] /= 2
    restated.loc[index[1], "volume"] *= 2
    store = ParquetBarStore(tmp_path)
    _write(store, original, FIRST)
    path = next(tmp_path.rglob("A.parquet"))
    saved = path.read_bytes()
    with pytest.raises(ValueError, match="unverified for retained sessions"):
        _write(store, restated.drop(index[1]), SECOND)
    assert path.read_bytes() == saved
    pd.testing.assert_frame_equal(_read(store).frame, original)
    # A complete fresh history can resolve the unknown basis without retaining the old gap.
    _write(store, restated, SECOND)
    pd.testing.assert_frame_equal(_read(store).frame, restated)
