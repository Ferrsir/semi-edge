"""Edge cases for the session calendar, quality gates and synthetic source contract."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import Adjustment, DataFeed, DataSource
from semiedge.data.source import MarketDataSource
from semiedge.data.synthetic import SyntheticMarketDataSource
from semiedge.data.validation import assess_quality, filter_completed_bars

AS_OF = date(2024, 11, 29)


@pytest.fixture(scope="module")
def calendar() -> TradingCalendar:
    return TradingCalendar()


@pytest.fixture
def bars() -> pd.DataFrame:
    return SyntheticMarketDataSource().get_daily_bars(["A"], date(2024, 11, 18), date(2024, 12, 6))[
        "A"
    ]


def _assess(frame: pd.DataFrame, calendar: TradingCalendar, **kwargs):
    params = {
        "symbol": "A",
        "as_of": AS_OF,
        "calendar": calendar,
        "lookback_sessions": 5,
        "feed": DataFeed.SYNTHETIC,
    }
    params.update(kwargs)
    return assess_quality(frame, **params)


def test_sessions_in_range_inclusive_and_skips_holidays(calendar):
    assert calendar.sessions_in_range(date(2024, 11, 27), date(2024, 12, 2)) == [
        date(2024, 11, 27),
        date(2024, 11, 29),
        date(2024, 12, 2),
    ]
    assert calendar.sessions_in_range(date(2024, 11, 28), date(2024, 11, 28)) == []
    with pytest.raises(ValueError, match="start must not exceed end"):
        calendar.sessions_in_range(date(2024, 12, 2), date(2024, 11, 27))


@pytest.mark.parametrize("count", [0, -1, True, 2.0])
def test_sessions_window_rejects_invalid_count(calendar, count):
    with pytest.raises(ValueError, match="positive integer"):
        calendar.sessions_window(AS_OF, count)


def test_previous_and_next_session_cross_weekends_and_holidays(calendar):
    assert calendar.previous_session(date(2024, 12, 2)) == date(2024, 11, 29)
    assert calendar.previous_session(date(2024, 11, 29)) == date(2024, 11, 27)
    assert calendar.next_session(date(2024, 11, 30)) == date(2024, 12, 2)


def test_christmas_eve_early_close(calendar):
    assert calendar.session_close(date(2024, 12, 24)) == datetime(2024, 12, 24, 18, tzinfo=UTC)
    assert calendar.last_completed_session(datetime(2024, 12, 24, 18, 29, tzinfo=UTC), 30) == (
        date(2024, 12, 23)
    )
    assert calendar.last_completed_session(datetime(2024, 12, 24, 18, 30, tzinfo=UTC), 30) == (
        date(2024, 12, 24)
    )


@pytest.mark.parametrize("settle", [True, 1.5, "30"])
def test_last_completed_session_rejects_non_integer_settle(calendar, settle):
    with pytest.raises(ValueError, match="nonnegative integer"):
        calendar.last_completed_session(datetime(2024, 11, 29, 22, tzinfo=UTC), settle)


def test_future_bars_never_affect_report(bars, calendar):
    """Rows after as_of must not make a stale series look fresh or hide gaps (look-ahead)."""
    report = _assess(bars, calendar)
    assert report.last_session == AS_OF
    assert report.is_actionable
    stale = bars.drop(pd.Timestamp(AS_OF))
    stale_report = _assess(stale, calendar)
    assert stale_report.last_session == date(2024, 11, 27)
    assert not stale_report.is_fresh
    assert not stale_report.is_actionable
    assert stale_report.missing_sessions == (AS_OF,)


def test_bad_values_after_as_of_are_not_reported(bars, calendar):
    bars.loc[pd.Timestamp("2024-12-03"), "close"] = -1.0
    assert _assess(bars, calendar).is_actionable


def test_schema_violation_becomes_blocking_issue(bars, calendar):
    report = _assess(bars.iloc[::-1], calendar)
    assert not report.is_actionable
    assert report.last_session is None
    assert report.missing_sessions == tuple(calendar.sessions_window(AS_OF, 5))
    assert any("sorted ascending" in issue for issue in report.issues)


@pytest.mark.parametrize("allowed", [-1, True, 1.0])
def test_max_missing_sessions_must_be_nonnegative_int(bars, calendar, allowed):
    with pytest.raises(ValueError, match="max_missing_sessions"):
        _assess(bars, calendar, max_missing_sessions=allowed)


def test_non_session_as_of_fails_closed(bars, calendar):
    with pytest.raises(ValueError, match="not a session"):
        _assess(bars, calendar, as_of=date(2024, 11, 28))


def test_feed_column_without_declared_feed_is_rejected(bars, calendar):
    report = _assess(bars.assign(feed="sip"), calendar, feed=None)
    assert "data feed is unknown" in report.issues
    assert "mixed or mismatched data feeds" in report.issues


def test_feed_column_matching_declared_feed_is_accepted(bars, calendar):
    assert _assess(bars.assign(feed="synthetic"), calendar).is_actionable


def test_feed_column_with_missing_value_is_rejected(bars, calendar):
    feeds = pd.Series(["synthetic"] * len(bars), index=bars.index, dtype="object")
    feeds.iloc[0] = None
    assert "mixed or mismatched data feeds" in _assess(bars.assign(feed=feeds), calendar).issues


def test_bar_on_non_exchange_session_is_rejected(bars, calendar):
    thanksgiving = bars.iloc[[0]].copy()
    thanksgiving.index = pd.DatetimeIndex(
        [pd.Timestamp("2024-11-28")], name="session", dtype="datetime64[ns]"
    )
    frame = pd.concat([bars, thanksgiving]).sort_index()
    assert "bars contain non-exchange sessions" in _assess(frame, calendar).issues


def test_filter_completed_bars_validates_input(bars):
    with pytest.raises(ValueError, match="sorted ascending"):
        filter_completed_bars(bars.iloc[::-1], AS_OF)


def test_synthetic_source_contract(calendar):
    source: MarketDataSource = SyntheticMarketDataSource(seed=11, calendar=calendar)
    assert (source.source, source.feed, source.adjustment) == (
        DataSource.SYNTHETIC,
        DataFeed.SYNTHETIC,
        Adjustment.SPLIT,
    )
    empty = source.get_daily_bars(["A"], date(2024, 11, 28), date(2024, 11, 28))["A"]
    assert empty.empty
    other_seed = SyntheticMarketDataSource(seed=12, calendar=calendar)
    window = (["A"], date(2024, 11, 18), date(2024, 11, 29))
    assert not source.get_daily_bars(*window)["A"].equals(other_seed.get_daily_bars(*window)["A"])
