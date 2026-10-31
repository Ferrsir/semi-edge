"""Hand-computed tests for ``semiedge.strategy.momentum_v1.compute_features`` (issue #12)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from momentum_fixtures import (
    REGIME_CLOSE,
    SMALL_PARAMS,
    STOCK,
    bar_frame,
    regime_frame,
    sessions,
)

from semiedge.config.models import MomentumV1Params
from semiedge.data.schema import DataSchemaError
from semiedge.strategy.momentum_v1 import FEATURE_COLUMNS, compute_features

NAN = math.nan


def _features(
    stock: dict[str, list[float]] | None = None,
    regime: list[float] | None = None,
    params: MomentumV1Params = SMALL_PARAMS,
) -> pd.DataFrame:
    return compute_features(
        bar_frame(STOCK if stock is None else stock),
        regime_frame(REGIME_CLOSE if regime is None else regime),
        params,
    )


def _with(**changes: dict[int, float]) -> dict[str, list[float]]:
    """Copy of the STOCK fixture with selected positions of columns replaced."""
    out = {k: list(v) for k, v in STOCK.items()}
    for column, edits in changes.items():
        for position, value in edits.items():
            out[column][position] = value
    return out


def _col(frame: pd.DataFrame, column: str) -> list[float]:
    return [float(x) for x in frame[column].to_list()]


def _assert_col(frame: pd.DataFrame, column: str, expected: list[float]) -> None:
    got = _col(frame, column)
    assert len(got) == len(expected), column
    for i, (g, e) in enumerate(zip(got, expected, strict=True)):
        if math.isnan(e):
            assert math.isnan(g), f"{column}[{i}]: expected NaN, got {g}"
        else:
            assert g == pytest.approx(e, rel=1e-12, abs=1e-12), f"{column}[{i}]"


# ------------------------------------------------------------------ hand-computed fixture


def test_feature_frame_columns_and_index() -> None:
    f = _features()
    assert tuple(f.columns) == FEATURE_COLUMNS
    assert f.index.equals(sessions(5))
    assert f.index.name == "session"


def test_hand_computed_features() -> None:
    f = _features()
    # SMA2(C): t1 (10+10)/2, t2 10, t3 (10+11)/2, t4 (11+12)/2
    _assert_col(f, "sma_fast", [NAN, 10.0, 10.0, 10.5, 11.5])
    # SMA3(C): t2 10, t3 (10+10+11)/3, t4 (10+11+12)/3
    _assert_col(f, "sma_slow", [NAN, NAN, 10.0, 31 / 3, 11.0])
    # PH = max(H_{t-2}, H_{t-1}): t2 max(10.5,10.5), t3 max(10.5,10.5), t4 max(10.5,11)
    _assert_col(f, "prior_high", [NAN, NAN, 10.5, 10.5, 11.0])
    # TR: 1, 1, 1, max(1,|11-10|,|10-10|)=1, max(1.5,|12.5-11|,|11-11|)=1.5
    # ATR(2): seed t1 (1+1)/2=1; t2 (1+1)/2=1; t3 1; t4 (1*1+1.5)/2=1.25
    _assert_col(f, "atr", [NAN, 1.0, 1.0, 1.0, 1.25])
    # R = C_t/C_{t-2}-1: t2 0, t3 11/10-1, t4 12/10-1
    _assert_col(f, "ret", [NAN, NAN, 0.0, 0.1, 0.2])
    # SMA3(M): t2 100, t3 305/3, t4 (100+105+110)/3=105; RM: t3 105/100-1, t4 110/100-1
    _assert_col(f, "regime_sma", [NAN, NAN, 100.0, 305 / 3, 105.0])
    _assert_col(f, "regime_ret", [NAN, NAN, 0.0, 0.05, 0.1])
    _assert_col(f, "excess_ret", [NAN, NAN, 0.0, 0.05, 0.1])
    # PV = mean(V_{t-2}, V_{t-1}) = 100; RV = V_t / PV
    _assert_col(f, "prior_volume", [NAN, NAN, 100.0, 100.0, 100.0])
    _assert_col(f, "rel_volume", [NAN, NAN, 1.0, 1.0, 3.0])
    # C_u > SMA3_u: t2 10>10 no (0), t3 11>10.33 yes, t4 12>11 yes; window 2 -> t3 0.5, t4 1.0
    _assert_col(f, "trend_persistence", [NAN, NAN, NAN, 0.5, 1.0])


def test_hand_computed_score_components() -> None:
    f = _features()
    # s_RS = clip((XR-0.02)/(0.15-0.02)): t2 clip(-0.02/0.13)=0, t3 0.03/0.13, t4 0.08/0.13
    _assert_col(f, "s_rs", [NAN, NAN, 0.0, 0.03 / 0.13, 0.08 / 0.13])
    # s_BO = clip(((C-PH)/ATR)/1.0): t2 (10-10.5)/1 -> 0, t3 0.5/1, t4 1/1.25 = 0.8
    _assert_col(f, "s_bo", [NAN, NAN, 0.0, 0.5, 0.8])
    # s_RV = clip((RV-1.2)/(3.0-1.2)): t2/t3 clip(-0.2/1.8)=0, t4 1.8/1.8 = 1
    _assert_col(f, "s_rv", [NAN, NAN, 0.0, 0.0, 1.0])
    _assert_col(f, "s_tp", [NAN, NAN, NAN, 0.5, 1.0])
    # score = 100*(0.4 s_RS + 0.3 s_BO + 0.2 s_RV + 0.1 s_TP)
    t3 = 100 * (0.4 * 0.03 / 0.13 + 0.3 * 0.5 + 0.0 + 0.1 * 0.5)
    t4 = 100 * (0.4 * 0.08 / 0.13 + 0.3 * 0.8 + 0.2 * 1.0 + 0.1 * 1.0)
    _assert_col(f, "score", [NAN, NAN, NAN, t3, t4])
    assert f["score"].iloc[4] == pytest.approx(78.61538461538461, rel=1e-12)


def test_hand_computed_conditions() -> None:
    f = _features()
    # t3: only relative volume fails (RV 1.0 < 1.2); t4: everything passes.
    expected_t3 = {
        "cond_sector_regime": True,  # 105 > 101.67
        "cond_above_sma_fast": True,  # 11 > 10.5
        "cond_above_sma_slow": True,  # 11 > 10.33
        "cond_breakout": True,  # 11 > 10.5
        "cond_relative_strength": True,  # 0.05 >= 0.02
        "cond_relative_volume": False,  # 1.0 < 1.2
        "all_conditions": False,
    }
    for column, value in expected_t3.items():
        assert bool(f[column].iloc[3]) is value, column
    assert all(bool(f[c].iloc[4]) for c in expected_t3)
    # Undefined inputs never pass.
    assert not f.iloc[:2][[c for c in f.columns if c.startswith("cond_")]].to_numpy().any()
    assert f["all_conditions"].dtype == np.bool_


# ------------------------------------------------------------------ per-condition boundaries


def test_sector_regime_fails_below_and_at_sma() -> None:
    # SMA3(M) at t4 = (100+105+101)/3 = 102 > 101
    f = _features(regime=[100.0, 100.0, 100.0, 105.0, 101.0])
    assert not f["cond_sector_regime"].iloc[4]
    # Equality is not a pass (strict >): flat regime M_t == SMA
    f = _features(regime=[100.0] * 5)
    assert f["regime_close"].iloc[4] == f["regime_sma"].iloc[4]
    assert not f["cond_sector_regime"].iloc[4]


def test_above_sma_fast_and_slow_strict() -> None:
    # t4 close equals t3 close -> SMA2 == close; SMA3 = (10+11+11)/3 = 10.67 < 11
    f = _features(_with(open={4: 11.0}, high={4: 11.2}, low={4: 10.8}, close={4: 11.0}))
    assert f["sma_fast"].iloc[4] == 11.0
    assert not f["cond_above_sma_fast"].iloc[4]
    assert f["cond_above_sma_slow"].iloc[4]
    # close 10.2 < SMA3 (10+11+10.2)/3 = 10.4 and < SMA2 10.6
    f = _features(_with(open={4: 10.5}, high={4: 10.6}, low={4: 10.1}, close={4: 10.2}))
    assert not f["cond_above_sma_fast"].iloc[4]
    assert not f["cond_above_sma_slow"].iloc[4]


def test_breakout_is_strict_and_excludes_current_bar() -> None:
    # close == PH (11 = max(H2=10.5, H3=11)) -> not a breakout
    f = _features(_with(open={4: 11.0}, high={4: 11.5}, low={4: 10.8}, close={4: 11.0}))
    assert f["prior_high"].iloc[4] == 11.0
    assert not f["cond_breakout"].iloc[4]
    # The current bar's own high (12.5 in the base fixture) is never part of PH.
    assert _features()["prior_high"].iloc[4] == 11.0


def test_relative_strength_threshold_inclusive() -> None:
    xr = float(_features()["excess_ret"].iloc[4])
    at = SMALL_PARAMS.model_copy(update={"min_excess_return": xr})
    assert _features(params=at)["cond_relative_strength"].iloc[4]
    above = SMALL_PARAMS.model_copy(update={"min_excess_return": math.nextafter(xr, 1.0)})
    assert not _features(params=above)["cond_relative_strength"].iloc[4]


def test_relative_volume_threshold_inclusive() -> None:
    f = _features(_with(volume={4: 120.0}))  # 120 / 100 == 1.2
    assert f["rel_volume"].iloc[4] == 1.2
    assert f["cond_relative_volume"].iloc[4]
    assert f["s_rv"].iloc[4] == 0.0
    f = _features(_with(volume={4: 119.99}))
    assert not f["cond_relative_volume"].iloc[4]


def test_components_are_clipped_to_unit_interval() -> None:
    # Huge volume and breakout saturate; tiny excess return floors at 0.
    f = _features(_with(volume={4: 10_000.0}, high={4: 30.0}, close={4: 29.0}))
    assert f["s_rv"].iloc[4] == 1.0
    assert f["s_bo"].iloc[4] == 1.0
    assert f["s_rs"].iloc[4] == 1.0
    assert 0.0 <= f["score"].iloc[4] <= 100.0
    f = _features(regime=[100.0, 100.0, 100.0, 105.0, 125.0])  # XR = 0.2 - 0.25 < 0
    assert f["s_rs"].iloc[4] == 0.0


# ------------------------------------------------------------------ edge cases (fail closed)


def test_zero_prior_volume_makes_relative_volume_undefined() -> None:
    f = _features(_with(volume={0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}))
    assert f["prior_volume"].iloc[4] == 0.0
    assert math.isnan(f["rel_volume"].iloc[4])
    assert not f["cond_relative_volume"].iloc[4]
    assert math.isnan(f["s_rv"].iloc[4])
    assert math.isnan(f["score"].iloc[4])


def test_zero_current_volume_fails_volume_condition() -> None:
    f = _features(_with(volume={4: 0.0}))
    assert f["rel_volume"].iloc[4] == 0.0
    assert not f["cond_relative_volume"].iloc[4]
    assert f["s_rv"].iloc[4] == 0.0


def test_flat_prices_give_zero_atr_and_undefined_breakout_component() -> None:
    flat = {k: [10.0] * 6 for k in ("open", "high", "low", "close")}
    flat["volume"] = [100.0] * 6
    f = _features(flat, regime=[100.0] * 6)
    assert f["atr"].iloc[5] == 0.0
    assert not f["cond_breakout"].iloc[5]  # 10 > 10 is false
    assert math.isnan(f["s_bo"].iloc[5])  # (C - PH) / 0 is undefined, not inf
    assert math.isnan(f["score"].iloc[5])
    assert f["ret"].iloc[5] == 0.0


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("close", NAN),
        ("high", math.inf),
        ("volume", -1.0),
        ("low", 0.0),
        ("high", 9.9),  # high < close 10 -> inconsistent OHLC
    ],
)
def test_invalid_bar_is_a_missing_session(column: str, value: float) -> None:
    f = _features(_with(**{column: {2: value}}))
    row = f.iloc[2]
    assert math.isnan(row["close"])
    assert math.isnan(row["regime_close"])  # the whole aligned row is undefined
    # Every window covering t2 is undefined: SMA3 at t4 covers t2..t4.
    assert math.isnan(f["sma_slow"].iloc[4])
    assert math.isnan(f["prior_high"].iloc[4])  # max(H2, H3)
    assert math.isnan(f["ret"].iloc[4])  # C4 / C2
    assert math.isnan(f["atr"].iloc[4])  # Wilder restarts after an undefined TR
    assert not f["all_conditions"].iloc[4]
    assert math.isnan(f["score"].iloc[4])
    # SMA2 at t4 covers only t3..t4 and stays defined.
    assert f["sma_fast"].iloc[4] == 11.5


def test_session_missing_from_regime_makes_row_undefined() -> None:
    idx = sessions(5)
    regime = regime_frame([100.0, 100.0, 105.0, 110.0], idx.delete(2))
    f = compute_features(bar_frame(STOCK), regime, SMALL_PARAMS)
    assert f.index.equals(idx)
    assert math.isnan(f["close"].iloc[2])  # stock bar exists but its regime row is missing
    assert math.isnan(f["regime_sma"].iloc[4])
    assert math.isnan(f["sma_slow"].iloc[4])
    assert math.isnan(f["score"].iloc[4])


def test_session_missing_from_stock_makes_row_undefined() -> None:
    idx = sessions(5)
    stock = bar_frame(STOCK).drop(index=idx[2])
    f = compute_features(stock, regime_frame(REGIME_CLOSE), SMALL_PARAMS)
    assert f.index.equals(idx)  # the gap is visible, not silently closed
    assert math.isnan(f["regime_close"].iloc[2])
    assert math.isnan(f["sma_slow"].iloc[4])
    assert math.isnan(f["ret"].iloc[4])


def test_rows_before_both_series_exist_are_dropped() -> None:
    regime = regime_frame(REGIME_CLOSE[1:], sessions(5)[1:])
    f = compute_features(bar_frame(STOCK), regime, SMALL_PARAMS)
    assert f.index.equals(sessions(5)[1:])


def test_short_history_is_undefined_everywhere() -> None:
    short = {k: v[:3] for k, v in STOCK.items()}
    f = _features(short, regime=REGIME_CLOSE[:3])
    assert SMALL_PARAMS.required_history() == 4
    assert f["score"].isna().all()
    assert not f["all_conditions"].any()


def test_empty_inputs_return_empty_frame() -> None:
    empty = bar_frame({k: [] for k in STOCK})
    f = compute_features(empty, regime_frame(REGIME_CLOSE), SMALL_PARAMS)
    assert f.empty
    assert tuple(f.columns) == FEATURE_COLUMNS


def test_schema_violation_raises() -> None:
    bad = bar_frame(STOCK).rename_axis("date")
    with pytest.raises(DataSchemaError):
        compute_features(bad, regime_frame(REGIME_CLOSE), SMALL_PARAMS)
    with pytest.raises(DataSchemaError):
        compute_features(bar_frame(STOCK), regime_frame(REGIME_CLOSE).iloc[::-1], SMALL_PARAMS)


def test_inputs_are_not_mutated() -> None:
    stock, regime = bar_frame(_with(close={2: NAN})), regime_frame(REGIME_CLOSE)
    before = (stock.copy(), regime.copy())
    compute_features(stock, regime, SMALL_PARAMS)
    pd.testing.assert_frame_equal(stock, before[0])
    pd.testing.assert_frame_equal(regime, before[1])


# ------------------------------------------------------------------ default parameters


def _random_bars(n: int, seed: int, drift: float = 0.001) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.02, n)))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.02, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.02, n))
    volume = rng.lognormal(14, 0.4, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=sessions(n, "2023-01-02"),
    )


def test_default_params_match_naive_reference() -> None:
    """Independent loop implementation of docs/specifications/momentum_v1.md, default params."""
    params = MomentumV1Params()
    stock, regime = _random_bars(160, 1), _random_bars(160, 2)
    f = compute_features(stock, regime, params)
    c, h, lo, v = (stock[k].to_list() for k in ("close", "high", "low", "volume"))
    m = regime["close"].to_list()

    def mean(xs: list[float]) -> float:
        return sum(xs) / len(xs)

    tr = [h[0] - lo[0]] + [
        max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, 160)
    ]
    atr_ref = [NAN] * 160
    atr_ref[13] = mean(tr[:14])
    for i in range(14, 160):
        atr_ref[i] = (atr_ref[i - 1] * 13 + tr[i]) / 14

    for t in (67, 68, 100, 159):  # 68 = required_history() - 1 is the first defined score
        sma50 = mean(c[t - 49 : t + 1])
        ph = max(h[t - 20 : t])
        pv = mean(v[t - 20 : t])
        xr = (c[t] / c[t - 20] - 1) - (m[t] / m[t - 20] - 1)
        rv = v[t] / pv
        row = f.iloc[t]
        assert row["sma_fast"] == pytest.approx(mean(c[t - 19 : t + 1]), rel=1e-12)
        assert row["sma_slow"] == pytest.approx(sma50, rel=1e-12)
        assert row["regime_sma"] == pytest.approx(mean(m[t - 49 : t + 1]), rel=1e-12)
        assert row["prior_high"] == ph
        assert row["prior_volume"] == pytest.approx(pv, rel=1e-12)
        assert row["excess_ret"] == pytest.approx(xr, rel=1e-9, abs=1e-12)
        assert row["rel_volume"] == pytest.approx(rv, rel=1e-12)
        assert row["atr"] == pytest.approx(atr_ref[t], rel=1e-12)
        conds = [
            m[t] > mean(m[t - 49 : t + 1]),
            c[t] > mean(c[t - 19 : t + 1]),
            c[t] > sma50,
            c[t] > ph,
            xr >= 0.02,
            rv >= 1.2,
        ]
        assert bool(row["all_conditions"]) is all(conds)
        if t < 68:
            assert math.isnan(row["s_tp"])  # needs SMA50 at t-19
            assert math.isnan(row["score"])
            continue
        above = sum(c[u] > mean(c[u - 49 : u + 1]) for u in range(t - 19, t + 1))

        def clip(x: float) -> float:
            return min(1.0, max(0.0, x))

        expected = 100 * (
            0.40 * clip((xr - 0.02) / (0.15 - 0.02))
            + 0.30 * clip(((c[t] - ph) / atr_ref[t]) / 1.0)
            + 0.20 * clip((rv - 1.2) / (3.0 - 1.2))
            + 0.10 * above / 20
        )
        assert row["s_tp"] == above / 20
        assert row["score"] == pytest.approx(expected, rel=1e-9, abs=1e-9)


def test_score_and_components_bounded_on_random_data() -> None:
    f = compute_features(_random_bars(400, 3, 0.004), _random_bars(400, 4), MomentumV1Params())
    defined = f.dropna(subset=["score"])
    assert len(defined) > 300
    assert defined["score"].between(0.0, 100.0).all()
    for column in ("s_rs", "s_bo", "s_rv", "s_tp"):
        assert defined[column].between(0.0, 1.0).all()


def test_split_adjustment_invariance() -> None:
    """Conditions and score are ratios within one series: a constant rescale changes nothing."""
    params = MomentumV1Params()
    stock, regime = _random_bars(200, 5, 0.003), _random_bars(200, 6)
    scaled = stock.copy()
    for column in ("open", "high", "low", "close"):
        scaled[column] = stock[column] / 4.0
    scaled["volume"] = stock["volume"] * 4.0
    a, b = compute_features(stock, regime, params), compute_features(scaled, regime, params)
    cond_cols = [c for c in a.columns if c.startswith("cond_")] + ["all_conditions"]
    pd.testing.assert_frame_equal(a[cond_cols], b[cond_cols])
    pd.testing.assert_series_equal(a["score"], b["score"], rtol=1e-9)


# ------------------------------------------------------------------ expected session calendar


def _gapped() -> tuple[pd.DataFrame, pd.DataFrame, pd.DatetimeIndex]:
    """STOCK and regime bars with the same session (calendar position 3) missing from BOTH."""
    calendar = sessions(6)
    index = calendar.delete(3)
    return bar_frame(STOCK, index), regime_frame(REGIME_CLOSE, index), calendar


def test_session_missing_from_both_series_is_undefined_with_calendar() -> None:
    stock, regime, calendar = _gapped()
    features = compute_features(stock, regime, SMALL_PARAMS, expected_sessions=calendar)
    assert list(features.index) == list(calendar)
    # Prior-window baselines on the gap end at t-1 and stay defined; nothing else on it is.
    excluded = [c for c in FEATURE_COLUMNS if c.startswith(("cond_", "all_"))]
    on_gap = features.loc[calendar[3]].drop([*excluded, "prior_high", "prior_volume"])
    assert on_gap.isna().all()
    assert math.isnan(features.loc[calendar[4], "prior_high"])
    last = features.iloc[-1]
    # Every window ending at the last session spans the gap: nothing is defined, nothing passes.
    for column in ("sma_slow", "prior_high", "atr", "ret", "regime_sma", "score"):
        assert math.isnan(last[column]), column
    assert not last["all_conditions"]


def test_session_missing_from_both_series_is_invisible_without_calendar() -> None:
    # Documented limitation: the join alone cannot see a gap present in both series.
    stock, regime, _ = _gapped()
    features = compute_features(stock, regime, SMALL_PARAMS)
    assert len(features) == 5
    assert bool(features.iloc[-1]["all_conditions"])


def test_calendar_without_gaps_changes_nothing() -> None:
    plain = _features()
    with_calendar = compute_features(
        bar_frame(STOCK), regime_frame(REGIME_CLOSE), SMALL_PARAMS, expected_sessions=sessions(9)
    )
    pd.testing.assert_frame_equal(plain, with_calendar)


def test_bar_on_unexpected_session_is_rejected() -> None:
    calendar = sessions(5).delete(2)
    with pytest.raises(DataSchemaError, match="not expected trading sessions"):
        compute_features(
            bar_frame(STOCK), regime_frame(REGIME_CLOSE), SMALL_PARAMS, expected_sessions=calendar
        )


@pytest.mark.parametrize(
    ("calendar", "error", "match"),
    [
        ("2024-01-02", TypeError, "not a string"),
        ([pd.Timestamp("2024-01-02 10:00")], ValueError, "midnight"),
        ([pd.Timestamp("2024-01-02", tz="UTC")], ValueError, "tz-naive"),
        ([pd.Timestamp("2024-01-02"), pd.NaT], ValueError, "missing"),
        ([pd.Timestamp("2024-01-02")] * 2, ValueError, "unique"),
    ],
)
def test_malformed_calendar_is_rejected(
    calendar: object, error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        compute_features(
            bar_frame(STOCK),
            regime_frame(REGIME_CLOSE),
            SMALL_PARAMS,
            expected_sessions=calendar,  # type: ignore[arg-type]
        )


def test_threshold_is_exact_ieee_comparison_fail_closed_at_rounded_boundary() -> None:
    # Decimal excess return is exactly 0.13 - 0.11 = 0.02, but in binary it computes to
    # 0.0199999999999998 < 0.02: the inclusive >= test fails (no tolerance, conservative).
    closes = [100.0, 100.0, 100.0, 105.0, 113.0]
    stock = {
        "open": closes,
        "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes],
        "close": closes,
        "volume": [100.0] * 5,
    }
    last = _features(stock, [100.0, 100.0, 100.0, 105.0, 111.0]).iloc[-1]
    assert last["excess_ret"] == pytest.approx(0.02, abs=1e-15)
    assert last["excess_ret"] < 0.02
    assert not last["cond_relative_strength"]
