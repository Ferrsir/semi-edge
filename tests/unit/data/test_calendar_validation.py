from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import DataFeed, validate_bar_frame
from semiedge.data.synthetic import SyntheticMarketDataSource
from semiedge.data.validation import assess_quality, filter_completed_bars


@pytest.fixture
def calendar():
    return TradingCalendar()


@pytest.mark.parametrize(
    ("session", "hour"),
    [
        (date(2024, 11, 29), 18),
        (date(2024, 3, 8), 21),
        (date(2024, 3, 11), 20),
        (date(2024, 11, 1), 20),
        (date(2024, 11, 4), 21),
    ],
)
def test_close_boundary(calendar, session, hour):
    close = datetime.combine(session, datetime.min.time(), UTC).replace(hour=hour)
    assert calendar.session_close(session) == close
    boundary = close + timedelta(minutes=30)
    assert calendar.last_completed_session(boundary, 30) == session
    assert calendar.last_completed_session(
        boundary - timedelta(microseconds=1), 30
    ) == calendar.previous_session(session)
    assert (
        calendar.last_completed_session(boundary.astimezone(ZoneInfo("America/New_York")), 30)
        == session
    )


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2024, 11, 30, 12, tzinfo=UTC), date(2024, 11, 29)),
        (datetime(2024, 11, 28, 23, tzinfo=UTC), date(2024, 11, 27)),
        (datetime(2024, 3, 10, 12, tzinfo=UTC), date(2024, 3, 8)),
    ],
)
def test_closed_days(calendar, now, expected):
    assert calendar.last_completed_session(now, 30) == expected


def test_calendar_contract(calendar):
    assert calendar._calendar is TradingCalendar()._calendar
    assert not calendar.is_session(date(2024, 11, 28))
    assert calendar.next_session(date(2024, 11, 28)) == date(2024, 11, 29)
    assert calendar.next_session(date(2024, 11, 29)) == date(2024, 12, 2)
    assert calendar.sessions_window(date(2024, 11, 29), 2) == [
        date(2024, 11, 27),
        date(2024, 11, 29),
    ]
    with pytest.raises(ValueError, match="timezone-aware"):
        calendar.last_completed_session(datetime(2024, 11, 29, tzinfo=UTC).replace(tzinfo=None), 30)
    with pytest.raises(ValueError, match="nonnegative integer"):
        calendar.last_completed_session(datetime.now(UTC), -1)


def test_synthetic_deterministic(calendar):
    args = (["NVDA", "SMH", "AMD"], date(2020, 1, 1), date(2024, 12, 31))
    first = SyntheticMarketDataSource(calendar=calendar).get_daily_bars(*args)
    second = SyntheticMarketDataSource(calendar=calendar).get_daily_bars(*args)
    breakouts = 0
    for symbol, frame in first.items():
        pd.testing.assert_frame_equal(frame, second[symbol])
        validate_bar_frame(frame)
        assert (frame.high >= frame[["open", "close"]].max(axis=1)).all()
        assert (frame.low <= frame[["open", "close"]].min(axis=1)).all()
        assert (frame > 0).all().all()
        assert list(frame.index.date) == calendar.sessions_in_range(args[1], args[2])
        breakouts += (
            (frame.close > frame.high.shift(1).rolling(20).max())
            & (frame.volume > 1.2 * frame.volume.shift(1).rolling(20).mean())
        ).sum()
    assert breakouts > 0
    reversed_result = SyntheticMarketDataSource().get_daily_bars(
        list(reversed(args[0])), args[1], args[2]
    )
    pd.testing.assert_frame_equal(first["NVDA"], reversed_result["NVDA"])


@pytest.fixture
def bars():
    return SyntheticMarketDataSource().get_daily_bars(["A"], date(2024, 11, 25), date(2024, 12, 2))[
        "A"
    ]


def report(bars, calendar, **kwargs):
    return assess_quality(
        bars,
        symbol="A",
        as_of=date(2024, 11, 29),
        calendar=calendar,
        lookback_sessions=3,
        feed=DataFeed.SYNTHETIC,
        **kwargs,
    )


def test_quality_gaps_and_freshness(bars, calendar):
    assert report(bars, calendar).is_actionable
    gap = bars.drop(pd.Timestamp("2024-11-27"))
    assert report(gap, calendar).missing_sessions == (date(2024, 11, 27),)
    assert not report(gap, calendar).is_actionable
    assert report(gap, calendar, max_missing_sessions=1).is_actionable
    assert not report(bars.loc[:"2024-11-27"], calendar).is_fresh
    assert not report(bars.iloc[:0], calendar).is_actionable


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("high", 0.1),
        ("low", 1e6),
        ("close", -1),
        ("volume", -1),
        ("open", float("nan")),
        ("close", float("inf")),
    ],
)
def test_bad_ohlcv(bars, calendar, column, value):
    bars.loc[pd.Timestamp("2024-11-29"), column] = value
    assert not report(bars, calendar).is_actionable


@pytest.mark.safety
def test_mixed_feed_rejected(bars, calendar):
    bars["feed"] = "synthetic"
    bars.loc[bars.index[0], "feed"] = "iex"
    assert "mixed or mismatched data feeds" in report(bars, calendar).issues


def test_unknown_feed(bars, calendar):
    result = assess_quality(
        bars,
        symbol="A",
        as_of=date(2024, 11, 29),
        calendar=calendar,
        lookback_sessions=3,
        feed=None,
    )
    assert not result.is_actionable


def test_filter_returns_independent_copy(bars):
    filtered = filter_completed_bars(bars, date(2024, 11, 29))
    assert filtered.index[-1] == pd.Timestamp("2024-11-29")
    filtered.iloc[0, 0] = 1
    assert bars.iloc[0, 0] != 1
