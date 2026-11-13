"""Hand-computed fixtures and contract tests for ``semiedge.indicators`` (issue #11)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from semiedge.indicators import (
    atr,
    prior_rolling_max,
    prior_rolling_mean,
    rolling_fraction_true,
    sma,
    trailing_return,
    true_range,
)

NAN = math.nan


def _sessions(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2024-01-02", periods=n, name="session").as_unit("ns")


def _series(values: list[float], name: str | None = "x") -> pd.Series:
    return pd.Series(values, index=_sessions(len(values)), dtype="float64", name=name)


def _assert_values(actual: pd.Series, expected: list[float], *, rel: float = 0.0) -> None:
    """Element-wise comparison where NaN must match NaN exactly in position."""
    assert actual.dtype == np.float64
    assert len(actual) == len(expected)
    for i, (got, want) in enumerate(zip(actual.to_list(), expected, strict=True)):
        if math.isnan(want):
            assert math.isnan(got), f"position {i}: expected NaN, got {got}"
        elif rel == 0.0:
            assert got == want, f"position {i}: expected {want!r}, got {got!r}"
        else:
            assert got == pytest.approx(want, rel=rel, abs=0.0), f"position {i}"


# ------------------------------------------------------------------ sma


def test_sma_hand_computed() -> None:
    # windows: (1,2,3)=2, (2,3,4)=3, (3,4,5)=4
    result = sma(_series([1, 2, 3, 4, 5]), 3)
    _assert_values(result, [NAN, NAN, 2.0, 3.0, 4.0])


def test_sma_includes_current_bar() -> None:
    # The last window (30, 1000) includes the current bar's 1000.
    _assert_values(sma(_series([10, 20, 30, 1000]), 2), [NAN, 15.0, 25.0, 515.0])


def test_sma_nan_poisons_every_window_containing_it() -> None:
    # windows of 2: (10,20)=15, (20,nan), (nan,40), (40,50)=45, (50,60)=55
    result = sma(_series([10, 20, NAN, 40, 50, 60]), 2)
    _assert_values(result, [NAN, 15.0, NAN, NAN, 45.0, 55.0])


def test_sma_treats_infinite_values_as_undefined() -> None:
    result = sma(_series([1.0, math.inf, 3.0, 4.0]), 2)
    _assert_values(result, [NAN, NAN, NAN, 3.5])


def test_sma_window_longer_than_series_is_all_nan() -> None:
    _assert_values(sma(_series([1, 2, 3]), 4), [NAN, NAN, NAN])


def test_sma_window_one_is_identity() -> None:
    _assert_values(sma(_series([1.5, 2.5, NAN]), 1), [1.5, 2.5, NAN])


def test_sma_preserves_index_and_name_and_casts_ints() -> None:
    s = pd.Series([1, 2, 3], index=_sessions(3), name="close")  # int64 input
    result = sma(s, 2)
    assert result.index.equals(s.index)
    assert result.name == "close"
    assert result.dtype == np.float64


def test_sma_matches_pandas_rolling_mean_on_random_data() -> None:
    rng = np.random.default_rng(11)
    s = pd.Series(rng.normal(100, 5, 400), index=_sessions(400))
    expected = s.rolling(window=50, min_periods=50).mean()
    pd.testing.assert_series_equal(sma(s, 50), expected, check_exact=False, rtol=1e-12)


def test_sma_does_not_mutate_input() -> None:
    s = _series([1.0, math.inf, 3.0])
    before = s.copy()
    sma(s, 2)
    pd.testing.assert_series_equal(s, before)


# ------------------------------------------------------------------ true range / ATR

# Hand fixture (5 bars). TR by hand:
#   t0: H-L = 10-8 = 2                                       (first bar: H-L only)
#   t1: max(11-9.5=1.5, |11-9|=2,      |9.5-9|=0.5)   = 2
#   t2: max(10-7=3,     |10-10.5|=0.5, |7-10.5|=3.5)  = 3.5
#   t3: max(9-8.5=0.5,  |9-7.5|=1.5,   |8.5-7.5|=1)   = 1.5
#   t4: max(9.25-8.25=1,|9.25-8.75|=.5,|8.25-8.75|=.5)= 1
HIGH = [10.0, 11.0, 10.0, 9.0, 9.25]
LOW = [8.0, 9.5, 7.0, 8.5, 8.25]
CLOSE = [9.0, 10.5, 7.5, 8.75, 9.0]
TR_EXPECTED = [2.0, 2.0, 3.5, 1.5, 1.0]


def test_true_range_hand_computed() -> None:
    result = true_range(_series(HIGH), _series(LOW), _series(CLOSE))
    _assert_values(result, TR_EXPECTED)


def test_true_range_first_bar_ignores_close() -> None:
    # A huge first close must not matter: there is no previous close for bar 1.
    result = true_range(_series([10.0]), _series([8.0]), _series([500.0]))
    _assert_values(result, [2.0])


def test_true_range_undefined_previous_close_makes_tr_undefined() -> None:
    result = true_range(
        _series([10.0, 11.0, 12.0]), _series([9.0, 10.0, 11.0]), _series([9.5, NAN, 11.5])
    )
    # t1 uses C0 (defined) -> max(1, 1.5, 0.5) = 1.5; t2 needs C1 (NaN) -> undefined.
    _assert_values(result, [1.0, 1.5, NAN])


def test_atr_wilder_seeding_and_recursion_hand_computed() -> None:
    # n = 3. Seed at the 3rd bar = mean(2, 2, 3.5) = 2.5
    # t3 = (2.5 * 2 + 1.5) / 3 = 13/6
    # t4 = (13/6 * 2 + 1) / 3 = 16/9
    result = atr(_series(HIGH), _series(LOW), _series(CLOSE), window=3)
    _assert_values(result, [NAN, NAN, 2.5, 13 / 6, 16 / 9], rel=1e-15)


def test_atr_window_one_equals_true_range() -> None:
    result = atr(_series(HIGH), _series(LOW), _series(CLOSE), window=1)
    _assert_values(result, TR_EXPECTED)


def test_atr_is_not_a_simple_moving_average_of_tr() -> None:
    # Wilder smoothing differs from SMA(TR) after the seed: SMA3 at t4 = (3.5+1.5+1)/3 = 2.
    result = atr(_series(HIGH), _series(LOW), _series(CLOSE), window=3)
    assert result.iloc[4] != pytest.approx(2.0)


def test_atr_undefined_bar_resets_and_reseeds() -> None:
    # n = 2. TR by hand:
    #   t0: 10-9 = 1
    #   t1: max(1, |11-9.5|=1.5, |10-9.5|=0.5)        = 1.5
    #   t2: high undefined                            -> NaN (ATR resets)
    #   t3: max(0.5, |10.75-10.25|=0.5, |10.25-10.25|=0) = 0.5
    #   t4: max(1, |11.5-10.5|=1, |10.5-10.5|=0)       = 1
    #   t5: max(0.5, |11.25-11|=0.25, |10.75-11|=0.25) = 0.5
    # ATR: t1 seed (1+1.5)/2 = 1.25; t2 NaN; t3 NaN (one TR since reset);
    #      t4 re-seed (0.5+1)/2 = 0.75; t5 (0.75*1 + 0.5)/2 = 0.625
    high = _series([10.0, 11.0, NAN, 10.75, 11.5, 11.25])
    low = _series([9.0, 10.0, 10.0, 10.25, 10.5, 10.75])
    close = _series([9.5, 10.5, 10.25, 10.5, 11.0, 11.0])
    _assert_values(true_range(high, low, close), [1.0, 1.5, NAN, 0.5, 1.0, 0.5])
    _assert_values(atr(high, low, close, window=2), [NAN, 1.25, NAN, NAN, 0.75, 0.625])


def test_atr_constant_range_is_exact() -> None:
    n = 30
    close = _series([100.0] * n)
    result = atr(close + 1.0, close - 1.0, close, window=14)
    assert result.iloc[:13].isna().all()
    assert (result.iloc[13:] == 2.0).all()


def test_atr_matches_naive_reference_on_random_data() -> None:
    rng = np.random.default_rng(5)
    n = 300
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    high = close * (1 + rng.uniform(0, 0.03, n))
    low = close * (1 - rng.uniform(0, 0.03, n))
    # Naive reference written straight from the spec.
    tr = [high[0] - low[0]] + [
        max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        for i in range(1, n)
    ]
    expected = [NAN] * 13 + [sum(tr[:14]) / 14]
    for i in range(14, n):
        expected.append((expected[-1] * 13 + tr[i]) / 14)
    result = atr(_series(list(high)), _series(list(low)), _series(list(close)), window=14)
    _assert_values(result, expected, rel=1e-12)


def test_atr_rejects_misaligned_inputs() -> None:
    high = _series(HIGH)
    low = pd.Series(LOW, index=_sessions(6)[1:], name="x")
    with pytest.raises(ValueError, match="identical index"):
        atr(high, low, _series(CLOSE), window=3)


# ------------------------------------------------------------------ prior rolling max / mean


def test_prior_rolling_max_excludes_current_bar_hand_computed() -> None:
    x = _series([5, 1, 4, 2, 8, 3])
    # window 2: t2=max(5,1)=5, t3=max(1,4)=4, t4=max(4,2)=4 (current 8 excluded), t5=max(2,8)=8
    _assert_values(prior_rolling_max(x, 2), [NAN, NAN, 5.0, 4.0, 4.0, 8.0])
    # window 3: t3=max(5,1,4)=5, t4=max(1,4,2)=4, t5=max(4,2,8)=8
    _assert_values(prior_rolling_max(x, 3), [NAN, NAN, NAN, 5.0, 4.0, 8.0])


def test_prior_rolling_max_nan_in_window_is_undefined() -> None:
    x = _series([5, NAN, 4, 2, 1])
    # window 2: t2=(5,nan) NaN, t3=(nan,4) NaN, t4=(4,2)=4
    _assert_values(prior_rolling_max(x, 2), [NAN, NAN, NAN, NAN, 4.0])


def test_prior_rolling_mean_excludes_current_bar_hand_computed() -> None:
    x = _series([2, 4, 6, 8, 100])
    # window 2: t2=(2+4)/2=3, t3=(4+6)/2=5, t4=(6+8)/2=7 (current 100 excluded)
    _assert_values(prior_rolling_mean(x, 2), [NAN, NAN, 3.0, 5.0, 7.0])
    # window 4: t4=(2+4+6+8)/4=5
    _assert_values(prior_rolling_mean(x, 4), [NAN, NAN, NAN, NAN, 5.0])


def test_prior_rolling_functions_match_shift_then_roll() -> None:
    rng = np.random.default_rng(3)
    s = pd.Series(rng.lognormal(13, 0.5, 250), index=_sessions(250))
    pd.testing.assert_series_equal(
        prior_rolling_max(s, 20), s.shift(1).rolling(20, min_periods=20).max()
    )
    pd.testing.assert_series_equal(
        prior_rolling_mean(s, 20),
        s.shift(1).rolling(20, min_periods=20).mean(),
        check_exact=False,
        rtol=1e-12,
    )


# ------------------------------------------------------------------ trailing return


def test_trailing_return_hand_computed() -> None:
    x = _series([100.0, 110.0, 121.0, 133.1])
    _assert_values(trailing_return(x, 1), [NAN, 0.1, 0.1, 0.1], rel=1e-12)
    _assert_values(trailing_return(x, 2), [NAN, NAN, 0.21, 0.21], rel=1e-12)
    _assert_values(trailing_return(x, 3), [NAN, NAN, NAN, 0.331], rel=1e-12)


def test_trailing_return_exact_value() -> None:
    # 125/100 - 1 is exactly representable.
    _assert_values(trailing_return(_series([100.0, 125.0]), 1), [NAN, 0.25])


def test_trailing_return_non_positive_or_missing_prices_are_undefined() -> None:
    x = _series([100.0, 0.0, 50.0, -5.0, NAN, 60.0])
    # t1: cur 0 -> NaN; t2: base 0 -> NaN; t3: cur -5 -> NaN; t4: cur NaN; t5: base NaN
    _assert_values(trailing_return(x, 1), [NAN, NAN, NAN, NAN, NAN, NAN])
    # window 2: t2 = 50/100 - 1 = -0.5; t3 base 0; t4 cur NaN; t5 base -5
    _assert_values(trailing_return(x, 2), [NAN, NAN, -0.5, NAN, NAN, NAN])


# ------------------------------------------------------------------ rolling fraction true


def test_rolling_fraction_true_hand_computed_float_mask() -> None:
    mask = _series([1, 1, 1, 0, 0, 1, NAN, 1, 1, 1])
    # window 3: t2=3/3, t3=2/3, t4=1/3, t5=1/3, t6..t8 contain NaN, t9=3/3
    expected = [NAN, NAN, 1.0, 2 / 3, 1 / 3, 1 / 3, NAN, NAN, NAN, 1.0]
    _assert_values(rolling_fraction_true(mask, 3), expected)


def test_rolling_fraction_true_bool_and_nullable_masks() -> None:
    plain = pd.Series([True, False, True, True], index=_sessions(4))
    _assert_values(rolling_fraction_true(plain, 2), [NAN, 0.5, 0.5, 1.0])
    nullable = pd.Series([True, pd.NA, True, True], index=_sessions(4), dtype="boolean")
    _assert_values(rolling_fraction_true(nullable, 2), [NAN, NAN, NAN, 1.0])


def test_rolling_fraction_true_rejects_non_binary_values() -> None:
    with pytest.raises(ValueError, match="True/False"):
        rolling_fraction_true(_series([1.0, 0.5]), 2)
    with pytest.raises(ValueError, match="True/False"):
        rolling_fraction_true(_series([1.0, math.inf]), 2)
    with pytest.raises(TypeError):
        rolling_fraction_true(pd.Series(["a", "b"], index=_sessions(2)), 2)


# ------------------------------------------------------------------ input validation


@pytest.mark.parametrize("window", [0, -3])
def test_non_positive_window_rejected(window: int) -> None:
    with pytest.raises(ValueError, match="window"):
        sma(_series([1, 2, 3]), window)


@pytest.mark.parametrize("window", [True, 2.0, "3"])
def test_non_int_window_rejected(window: object) -> None:
    with pytest.raises(TypeError, match="window"):
        prior_rolling_max(_series([1, 2, 3]), window)  # type: ignore[arg-type]


def test_non_series_rejected() -> None:
    with pytest.raises(TypeError, match="Series"):
        sma([1.0, 2.0, 3.0], 2)  # type: ignore[arg-type]


def test_unsorted_or_duplicate_index_rejected() -> None:
    idx = _sessions(3)
    with pytest.raises(ValueError, match="sorted"):
        sma(pd.Series([1.0, 2.0, 3.0], index=idx[::-1]), 2)
    with pytest.raises(ValueError, match="unique"):
        sma(pd.Series([1.0, 2.0, 3.0], index=idx[[0, 1, 1]]), 2)


def test_boolean_or_text_price_series_rejected() -> None:
    with pytest.raises(TypeError, match="numeric"):
        sma(pd.Series([True, False, True], index=_sessions(3)), 2)
    with pytest.raises(TypeError, match="numeric"):
        trailing_return(pd.Series(["1", "2"], index=_sessions(2)), 1)


def test_empty_series_returns_empty_float_series() -> None:
    empty = pd.Series([], index=_sessions(0), dtype="float64")
    for result in (
        sma(empty, 3),
        prior_rolling_max(empty, 3),
        prior_rolling_mean(empty, 3),
        trailing_return(empty, 3),
        rolling_fraction_true(empty, 3),
        true_range(empty, empty, empty),
        atr(empty, empty, empty, 3),
    ):
        assert result.empty
        assert result.dtype == np.float64
