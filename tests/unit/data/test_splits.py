"""Hand-written fixtures only: no vendor observations or credentials."""

from datetime import UTC, date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import Adjustment, DataSchemaError, empty_bar_frame
from semiedge.data.splits import SplitDataError, SplitEvent, normalize_raw_splits

DAYS = [date(2024, 1, d) for d in (2, 3, 4, 5)]
CUTOFF = datetime(2024, 1, 5, 22, tzinfo=UTC)
KNOWN = datetime(2024, 1, 2, 12, tzinfo=UTC)


def bars():
    frame = pd.DataFrame(
        {
            "open": [100.0] * 4,
            "high": [110.0] * 4,
            "low": [90.0] * 4,
            "close": [100.0] * 4,
            "volume": [1000.0] * 4,
            "vwap": [101.0] * 4,
            "trade_count": [10.0] * 4,
        },
        index=pd.DatetimeIndex(DAYS, dtype="datetime64[ns]", name="session"),
    )
    frame.attrs["adjustment"] = "raw"
    return frame


def event(day=DAYS[1], new=2, old=1, known=KNOWN):
    return SplitEvent("AAA", day, new, old, known)


def normalize(frame=None, events=(), **kwargs):
    return normalize_raw_splits(
        bars() if frame is None else frame,
        events,
        symbol="AAA",
        adjustment=kwargs.pop("adjustment", Adjustment.RAW),
        as_of=kwargs.pop("as_of", DAYS[-1]),
        known_at=kwargs.pop("known_at", CUTOFF),
        calendar=TradingCalendar(),
        **kwargs,
    )


@pytest.mark.parametrize(("new", "old"), [(2, 1), (1, 5), (3, 2)])
def test_forward_reverse_fractional_conserve_notional(new, old):
    original = bars()
    untouched = original.copy(deep=True)
    split = event(new=new, old=old)
    result = normalize(original, [split])
    ratio = new / old
    assert result.applied == (split,)
    assert result.frame.iloc[0].close == pytest.approx(100 / ratio)
    assert result.frame.iloc[0].volume == pytest.approx(1000 * ratio)
    assert result.frame.iloc[0].vwap == pytest.approx(101 / ratio)
    assert result.frame.iloc[0].trade_count == 10
    np.testing.assert_allclose(
        result.frame.close * result.frame.volume, original.close * original.volume
    )
    assert_frame_equal(result.frame.iloc[1:], original.iloc[1:])
    assert_frame_equal(original, untouched)
    assert original.attrs == {"adjustment": "raw"}
    assert result.frame.attrs["adjustment"] == "split"


def test_multiple_events_order_and_effective_boundary():
    a, b = event(), event(DAYS[2], 3, 1)
    result = normalize(events=[b, a])
    assert result.applied == (a, b)
    np.testing.assert_allclose(result.frame.close, [100 / 6, 100 / 3, 100, 100])
    assert_frame_equal(result.frame, normalize(events=[a, b]).frame)


def test_inverse_chained_splits_have_exact_unit_factor():
    original = bars()
    result = normalize(original, [event(new=3, old=2), event(DAYS[2], new=2, old=3)])
    # Both events affect Jan2; exact cancellation must leave every float bit unchanged there.
    pd.testing.assert_series_equal(result.frame.iloc[0], original.iloc[0], check_exact=True)
    assert result.frame.iloc[1].close == 150.0  # only the second event affects Jan3
    assert_frame_equal(result.frame.iloc[2:], original.iloc[2:])


@pytest.mark.parametrize(("new", "old"), [(2**53, 1), (1, 2**53)])
def test_unrepresentable_cumulative_ratio_fails_closed(new, old):
    dates = TradingCalendar().sessions_window(DAYS[-1], 24)
    frame = bars().iloc[:1].copy()
    frame.index = pd.DatetimeIndex([dates[0]], dtype="datetime64[ns]", name="session")
    events = [event(day, new, old) for day in dates[1:]]
    with pytest.raises(SplitDataError, match="overflowed or underflowed"):
        normalize(frame, events)


def test_missing_event_iterable_has_explicit_data_error():
    with pytest.raises(SplitDataError, match="iterable"):
        normalize(events=None)


@pytest.mark.safety
def test_cutoffs_prevent_future_knowledge_and_effective_leakage():
    future_known = event(DAYS[1], known=CUTOFF + timedelta(seconds=1))
    future_effective = event(date(2024, 1, 8))
    baseline = normalize()
    mixed = bars()
    mixed.loc[mixed.index[1:], ["open", "high", "low", "close", "vwap"]] /= 2
    with pytest.raises(SplitDataError, match="not known"):
        normalize(mixed, events=[future_known, future_effective])
    result = normalize(events=[future_effective])
    assert result.applied == ()
    assert_frame_equal(result.frame, baseline.frame)
    known_boundary = event(known=CUTOFF)
    assert normalize(events=[known_boundary]).applied == (known_boundary,)


@pytest.mark.safety
def test_prefix_causality_at_historical_decision_time():
    split = event(DAYS[2], known=datetime(2024, 1, 4, 12, tzinfo=UTC))
    args = {"as_of": DAYS[1], "known_at": datetime(2024, 1, 3, 21, tzinfo=UTC)}
    full = normalize(events=[split], **args)
    prefix = normalize(bars().iloc[:2], **args)
    assert_frame_equal(full.frame, prefix.frame)
    assert len(full.frame) == 2
    assert full.applied == ()
    # Knowledge alone does not make a future-effective event apply.
    assert normalize(events=[event(DAYS[2])], **args).applied == ()


def test_already_adjusted_and_output_rejected():
    for adjustment in (Adjustment.SPLIT, Adjustment.ALL, Adjustment.DIVIDEND, "raw"):
        with pytest.raises(SplitDataError, match="RAW"):
            normalize(adjustment=adjustment)
    with pytest.raises(SplitDataError, match="RAW"):
        normalize(normalize(events=[event()]).frame)
    frame = bars()
    frame.attrs["adjustment"] = "all"
    with pytest.raises(SplitDataError, match="RAW"):
        normalize(frame)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, float("inf"), 2**53 + 1])
def test_invalid_counts(value):
    for name in ("new", "old"):
        with pytest.raises(SplitDataError, match="share counts"):
            event(**{name: value})


def test_invalid_event_metadata():
    with pytest.raises(SplitDataError, match="differ"):
        event(new=1)
    with pytest.raises(SplitDataError, match="date"):
        event(datetime(2024, 1, 3, tzinfo=UTC))
    with pytest.raises(SplitDataError, match="aware"):
        event(known=datetime(2024, 1, 1, tzinfo=UTC).replace(tzinfo=None))
    with pytest.raises(SplitDataError, match="ticker"):
        SplitEvent("../AAA", DAYS[1], 2, 1, KNOWN)
    with pytest.raises(SplitDataError, match="requested symbol"):
        normalize(events=[SplitEvent("BBB", DAYS[1], 2, 1, KNOWN)])
    with pytest.raises(SplitDataError, match="requested symbol"):
        normalize(events=[{}])


@pytest.mark.parametrize("events", [[event(), event()], [event(), event(new=3)]])
def test_duplicate_effective_sessions_reject_even_when_unknown(events):
    with pytest.raises(SplitDataError, match="duplicate"):
        normalize(events=events)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"as_of": date(2024, 1, 6)},
        {"as_of": date(2080, 1, 2)},
        {"as_of": "2024-01-05"},
        {"known_at": datetime(2024, 1, 5, tzinfo=UTC).replace(tzinfo=None)},
        {"known_at": datetime(2024, 1, 5, 20, 59, tzinfo=UTC)},
    ],
)
def test_invalid_cutoffs(kwargs):
    with pytest.raises(SplitDataError):
        normalize(**kwargs)


def test_sessions_and_timezone_boundaries():
    with pytest.raises(SplitDataError, match="session"):
        normalize(events=[event(date(2024, 1, 6))])
    frame = bars()
    frame.index = pd.DatetimeIndex([*DAYS[:3], date(2024, 1, 6)], name="session").as_unit("ns")
    assert len(normalize(frame).frame) == 3  # future non-session is outside the prefix
    with pytest.raises(SplitDataError, match="session"):
        normalize(frame, as_of=date(2024, 1, 8), known_at=datetime(2024, 1, 8, 22, tzinfo=UTC))
    offset = timezone(timedelta(hours=-5))
    result = normalize(known_at=datetime(2024, 1, 5, 16, tzinfo=offset))
    assert result.known_at == datetime(2024, 1, 5, 21, tzinfo=UTC)
    assert event(known=KNOWN.astimezone(offset)).known_at == KNOWN


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("close", np.nan),
        ("volume", np.inf),
        ("open", 0),
        ("volume", -1),
        ("high", 95),
        ("low", 105),
        ("vwap", -1),
        ("trade_count", 1.5),
        ("trade_count", -1),
    ],
)
def test_invalid_bars(column, value):
    frame = bars()
    frame.loc[frame.index[0], column] = value
    with pytest.raises(SplitDataError):
        normalize(frame)


def test_schema_and_unknown_columns():
    with pytest.raises(DataSchemaError):
        normalize(bars().drop(columns="close"))
    frame = bars()
    frame["unpriced_metric"] = 1.0
    with pytest.raises(SplitDataError, match="unknown"):
        normalize(frame)
    empty = empty_bar_frame()
    empty.attrs["adjustment"] = "raw"
    assert normalize(empty, [event()]).frame.empty


@pytest.mark.parametrize(
    ("column", "value", "new", "old"),
    [
        ("volume", 1e308, 2, 1),
        ("low", 1e-310, 2**53, 1),
        ("high", 1e308, 1, 2**53),
    ],
)
def test_numerical_failures_are_explicit_without_input_mutation(column, value, new, old):
    frame = bars()
    frame.loc[frame.index[0], column] = value
    original = frame.copy(deep=True)
    with pytest.raises(SplitDataError, match="overflowed or underflowed"):
        normalize(frame, [event(new=new, old=old)])
    assert_frame_equal(frame, original)


def test_one_shot_events_are_applied_once():
    split = event()
    result = normalize(events=(e for e in [split]))
    assert result.applied == (split,)
    assert result.frame.iloc[0].close == 50


def test_missing_provenance_is_rejected():
    frame = bars()
    frame.attrs.clear()
    with pytest.raises(SplitDataError, match="RAW"):
        normalize(frame)


@pytest.mark.safety
def test_applied_prefix_ignores_future_values_and_preserves_attrs():
    frame = bars()
    frame.attrs["fixture"] = {"label": "hand-written"}
    frame.loc[frame.index[-1], "close"] = np.nan
    original = frame.copy(deep=True)
    args = {"as_of": DAYS[1], "known_at": datetime(2024, 1, 3, 21, tzinfo=UTC)}
    split = event()
    full = normalize(frame, [split], **args)
    prefix = normalize(frame.iloc[:2], [split], **args)
    assert full.applied == (split,)
    np.testing.assert_allclose(full.frame.close, [50, 100])
    assert_frame_equal(full.frame, prefix.frame)
    assert_frame_equal(frame, original)
    assert frame.attrs == original.attrs
    full.frame.attrs["fixture"]["label"] = "changed copy"
    assert frame.attrs["fixture"]["label"] == "hand-written"


def test_early_close_and_maximum_share_count_boundaries():
    day = date(2024, 11, 29)
    with pytest.raises(SplitDataError, match="not closed"):
        normalize(as_of=day, known_at=datetime(2024, 11, 29, 17, 59, tzinfo=UTC))
    result = normalize(as_of=day, known_at=datetime(2024, 11, 29, 18, tzinfo=UTC))
    assert result.as_of == day
    split = event(new=2**53)
    result = normalize(events=[split])
    assert result.applied == (split,)
    assert result.frame.iloc[0].close == pytest.approx(100 / 2**53)
    with pytest.raises(SplitDataError, match="session"):
        normalize(events=[event(date(1987, 1, 2))])
