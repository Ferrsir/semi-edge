"""Tests for ``semiedge.strategy.momentum_v1.evaluate_universe`` (issue #12)."""

from __future__ import annotations

import math
from datetime import date, datetime

import pandas as pd
import pytest
from momentum_fixtures import (
    REGIME_CLOSE,
    SMALL_PARAMS,
    STOCK,
    FakeQuality,
    bar_frame,
    quality_for,
    regime_frame,
    sessions,
)

from semiedge.config.models import MomentumV1Params
from semiedge.data.schema import DataFeed
from semiedge.strategy import SignalEvaluation, evaluate_universe
from semiedge.strategy.momentum_v1 import CONDITION_NAMES

AS_OF = sessions(5)[-1].date()  # 2024-01-08
T3 = sessions(5)[3].date()
CALENDAR = sessions(80)  # expected exchange sessions covering every fixture
EXPECTED_SCORE = 100 * (0.4 * 0.08 / 0.13 + 0.3 * 0.8 + 0.2 * 1.0 + 0.1 * 1.0)


def _stock(**changes: dict[int, float]) -> pd.DataFrame:
    cols = {k: list(v) for k, v in STOCK.items()}
    for column, edits in changes.items():
        for position, value in edits.items():
            cols[column][position] = value
    return bar_frame(cols)


def _evaluate(
    bars: dict[str, pd.DataFrame],
    tradable: list[str],
    *,
    as_of: date = AS_OF,
    quality: dict[str, FakeQuality] | str | None = "auto",
    calendar: pd.DatetimeIndex | None = CALENDAR,
) -> list[SignalEvaluation]:
    if quality == "auto":
        quality = quality_for(as_of, "SMH", *tradable)
    assert not isinstance(quality, str)
    return evaluate_universe(
        bars,
        "SMH",
        SMALL_PARAMS,
        as_of,
        tradable=tradable,
        quality=quality,
        expected_sessions=calendar,
    )


def _by_symbol(evals: list[SignalEvaluation]) -> dict[str, SignalEvaluation]:
    return {e.symbol: e for e in evals}


def _base() -> dict[str, pd.DataFrame]:
    return {"SMH": regime_frame(REGIME_CLOSE), "AAA": _stock()}


# ------------------------------------------------------------------ eligible candidate


def test_eligible_candidate_reports_everything() -> None:
    [e] = _evaluate(_base(), ["AAA"])
    assert e.eligible
    assert e.rejection_reasons == ()
    assert e.rank == 1
    assert e.session == AS_OF
    assert (e.strategy, e.strategy_version) == ("momentum_v1", "1.0.0")
    assert e.score == pytest.approx(EXPECTED_SCORE, rel=1e-12)
    assert dict(e.score_components) == pytest.approx(
        {"s_rs": 0.08 / 0.13, "s_bo": 0.8, "s_rv": 1.0, "s_tp": 1.0}, rel=1e-12
    )
    assert e.close == 12.0
    assert e.atr == 1.25
    assert e.proposed_stop == 12.0 - 2 * 1.25  # 9.5
    assert e.proposed_target == 12.0 + 4 * 1.25  # 17.0
    # Every condition is reported with value, threshold and pass/fail.
    assert tuple(c.name for c in e.conditions) == CONDITION_NAMES
    cond = {c.name: c for c in e.conditions}
    assert (cond["sector_regime"].value, cond["sector_regime"].threshold) == (110.0, 105.0)
    assert (cond["above_sma_fast"].value, cond["above_sma_fast"].threshold) == (12.0, 11.5)
    assert (cond["above_sma_slow"].value, cond["above_sma_slow"].threshold) == (12.0, 11.0)
    assert (cond["breakout"].value, cond["breakout"].threshold) == (12.0, 11.0)
    assert cond["relative_strength"].value == pytest.approx(0.1)
    assert cond["relative_strength"].threshold == 0.02
    assert (cond["relative_volume"].value, cond["relative_volume"].threshold) == (3.0, 1.2)
    assert all(c.passed for c in e.conditions)
    assert cond["sector_regime"].description == "SMH close 110 > SMA3 105"


def test_score_components_mapping_is_read_only() -> None:
    [e] = _evaluate(_base(), ["AAA"])
    with pytest.raises(TypeError):
        e.score_components["s_rs"] = 0.0  # type: ignore[index]


def test_failed_condition_reported_with_reason_and_score_still_shown() -> None:
    # Evaluated at t3: everything but relative volume passes (RV 1.0 < 1.2).
    bars = {"SMH": regime_frame(REGIME_CLOSE), "AAA": _stock()}
    [e] = _evaluate(bars, ["AAA"], as_of=T3)
    assert not e.eligible
    assert e.rank is None
    assert e.rejection_reasons == (
        "relative_volume: failed (volume / prior 2-session mean 1 >= 1.2)",
    )
    assert e.score == pytest.approx(100 * (0.4 * 0.03 / 0.13 + 0.3 * 0.5 + 0.1 * 0.5))
    assert e.proposed_stop is None
    assert e.proposed_target is None


def test_bars_after_as_of_are_ignored() -> None:
    # Evaluating at t3 with t4 present must equal evaluating t3 on truncated data.
    full = _evaluate(_base(), ["AAA"], as_of=T3)
    cut = {k: v.iloc[:4] for k, v in _base().items()}
    assert _evaluate(cut, ["AAA"], as_of=T3) == full


# ------------------------------------------------------------------ ranking


def test_ranking_by_score_then_symbol_and_ineligible_last() -> None:
    weaker = _stock(volume={4: 200.0})  # RV 2.0 -> s_RV = 0.8/1.8
    failing = _stock(volume={4: 100.0})
    bars = {"SMH": regime_frame(REGIME_CLOSE), "BBB": _stock(), "AAA": _stock()}
    bars |= {"CCC": weaker, "DDD": failing}
    evals = _evaluate(bars, ["DDD", "CCC", "BBB", "AAA"])
    assert [(e.symbol, e.rank, e.eligible) for e in evals] == [
        ("AAA", 1, True),  # tie with BBB -> symbol ascending
        ("BBB", 2, True),
        ("CCC", 3, True),
        ("DDD", None, False),
    ]
    assert evals[0].score == evals[1].score
    assert evals[2].score is not None
    assert evals[2].score < evals[1].score  # type: ignore[operator]


def test_no_candidates_means_no_ranked_symbols() -> None:
    bars = {"SMH": regime_frame(REGIME_CLOSE), "AAA": _stock(volume={4: 100.0})}
    evals = _evaluate(bars, ["AAA"])
    assert [e.eligible for e in evals] == [False]
    assert all(e.rank is None and e.proposed_stop is None for e in evals)


def test_empty_tradable_list_returns_nothing() -> None:
    assert _evaluate(_base(), []) == []


# ------------------------------------------------------------------ data gates (fail closed)


def test_missing_quality_reports_make_everything_ineligible() -> None:
    [e] = _evaluate(_base(), ["AAA"], quality=None)
    assert not e.eligible
    assert any("no data quality reports supplied" in r for r in e.rejection_reasons)
    assert e.score == pytest.approx(EXPECTED_SCORE)  # still computed for display


def test_quality_gates() -> None:
    cases: dict[str, tuple[dict[str, FakeQuality], str]] = {
        "missing symbol report": (quality_for(AS_OF, "SMH"), "AAA: no data quality report"),
        "missing regime report": (
            quality_for(AS_OF, "AAA"),
            "regime benchmark SMH: no data quality report",
        ),
        "not actionable": (
            quality_for(AS_OF, "SMH")
            | {"AAA": FakeQuality("AAA", AS_OF, issues=("gap",), actionable=False)},
            "AAA: data quality not actionable (gap)",
        ),
        "regime not actionable": (
            quality_for(AS_OF, "AAA") | {"SMH": FakeQuality("SMH", AS_OF, actionable=False)},
            "regime benchmark SMH: data quality not actionable (not actionable)",
        ),
        "feed mix": (
            quality_for(AS_OF, "SMH") | quality_for(AS_OF, "AAA", feed=DataFeed.IEX),
            "AAA: feed iex differs from regime benchmark feed",
        ),
        "unknown feed": (
            quality_for(AS_OF, "SMH") | quality_for(AS_OF, "AAA", feed=None),
            "AAA: data feed unknown",
        ),
        "stale report": (
            quality_for(AS_OF, "SMH") | quality_for(T3, "AAA"),
            "AAA: data quality report is as of 2024-01-05, not 2024-01-08",
        ),
        "wrong symbol": (
            quality_for(AS_OF, "SMH") | {"AAA": FakeQuality("ZZZ", AS_OF)},
            "AAA: data quality report is for 'ZZZ'",
        ),
    }
    for label, (quality, expected) in cases.items():
        [e] = _evaluate(_base(), ["AAA"], quality=quality)
        assert not e.eligible, label
        assert any(r.startswith(expected) for r in e.rejection_reasons), (
            label,
            e.rejection_reasons,
        )
        assert e.proposed_stop is None, label


def test_stale_symbol_is_ineligible() -> None:
    bars = {"SMH": regime_frame(REGIME_CLOSE), "AAA": _stock().iloc[:4]}
    [e] = _evaluate(bars, ["AAA"])
    assert not e.eligible
    assert "AAA: stale data (last session 2024-01-05, as_of 2024-01-08)" in e.rejection_reasons
    assert e.session == T3
    assert all(c.value is None for c in e.conditions if c.name != "sector_regime")


def test_stale_regime_makes_every_symbol_ineligible() -> None:
    bars = {"SMH": regime_frame(REGIME_CLOSE).iloc[:4], "AAA": _stock()}
    [e] = _evaluate(bars, ["AAA"])
    assert not e.eligible
    assert (
        "regime benchmark SMH: stale data (last session 2024-01-05, as_of 2024-01-08)"
        in e.rejection_reasons
    )
    assert any(r.startswith("sector_regime: undefined input") for r in e.rejection_reasons)


def test_missing_regime_or_symbol_bars() -> None:
    [e] = _evaluate({"AAA": _stock()}, ["AAA"])
    assert "regime benchmark SMH: no bars supplied" in e.rejection_reasons
    assert e.score is None
    [e] = _evaluate({"SMH": regime_frame(REGIME_CLOSE)}, ["AAA"])
    assert "AAA: no bars supplied" in e.rejection_reasons
    assert e.session is None
    assert e.score is None
    assert e.score_components == {}
    assert any(
        r.startswith("score: undefined (missing components: s_rs") for r in e.rejection_reasons
    )


def test_no_bars_on_or_before_as_of() -> None:
    later = bar_frame(STOCK, sessions(5, "2024-02-01"))
    [e] = _evaluate({"SMH": regime_frame(REGIME_CLOSE), "AAA": later}, ["AAA"])
    assert "AAA: no completed bars on or before 2024-01-08" in e.rejection_reasons


def test_invalid_frame_is_reported_not_raised() -> None:
    bad = _stock().rename_axis("date")
    [e] = _evaluate({"SMH": regime_frame(REGIME_CLOSE), "AAA": bad}, ["AAA"])
    assert not e.eligible
    assert any(r.startswith("AAA: invalid bar frame") for r in e.rejection_reasons)


def test_regime_benchmark_is_never_a_candidate() -> None:
    bars = _base() | {"SMH": regime_frame(REGIME_CLOSE)}
    evals = _by_symbol(_evaluate(bars, ["AAA", "SMH"]))
    assert evals["AAA"].eligible
    assert not evals["SMH"].eligible
    assert "SMH: regime benchmark is never a candidate" in evals["SMH"].rejection_reasons


def test_undefined_inputs_are_explicit_reasons() -> None:
    short = {"SMH": regime_frame(REGIME_CLOSE).iloc[:3], "AAA": _stock().iloc[:3]}
    as_of = sessions(5)[2].date()
    [e] = _evaluate(short, ["AAA"], as_of=as_of)
    assert not e.eligible
    assert "score: undefined (missing components: s_tp)" in e.rejection_reasons
    assert e.score is None
    assert "s_tp" not in e.score_components


def test_zero_prior_volume_reason() -> None:
    bars = {
        "SMH": regime_frame(REGIME_CLOSE),
        "AAA": _stock(volume={0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}),
    }
    [e] = _evaluate(bars, ["AAA"])
    assert not e.eligible
    rv = {c.name: c for c in e.conditions}["relative_volume"]
    assert rv.value is None
    assert not rv.passed
    assert any(r.startswith("relative_volume: undefined input") for r in e.rejection_reasons)


def test_nan_close_at_as_of_is_ineligible() -> None:
    bars = {"SMH": regime_frame(REGIME_CLOSE), "AAA": _stock(close={4: math.nan})}
    [e] = _evaluate(bars, ["AAA"])
    assert not e.eligible
    assert e.close is None
    assert e.score is None


# ------------------------------------------------------------------ argument validation


def test_argument_validation() -> None:
    with pytest.raises(TypeError, match="date"):
        evaluate_universe(_base(), "SMH", SMALL_PARAMS, datetime(2024, 1, 8), tradable=["AAA"])  # noqa: DTZ001
    with pytest.raises(TypeError, match="sequence"):
        evaluate_universe(_base(), "SMH", SMALL_PARAMS, AS_OF, tradable="AAA")
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_universe(_base(), "SMH", SMALL_PARAMS, AS_OF, tradable=["AAA", "AAA"])


def test_deterministic() -> None:
    bars = _base() | {"BBB": _stock(volume={4: 200.0})}
    assert _evaluate(bars, ["BBB", "AAA"]) == _evaluate(bars, ["AAA", "BBB"])


# ------------------------------------------------------------------ calendar and stop gates


def test_missing_calendar_makes_every_symbol_ineligible() -> None:
    [e] = _evaluate(_base(), ["AAA"], calendar=None)
    assert not e.eligible
    assert any("no expected session calendar" in r for r in e.rejection_reasons)
    assert e.score == pytest.approx(EXPECTED_SCORE, rel=1e-12)  # still shown


def test_as_of_not_an_expected_session_is_ineligible() -> None:
    [e] = _evaluate(_base(), ["AAA"], calendar=CALENDAR.delete(4))
    assert not e.eligible
    assert any(f"as_of {AS_OF} is not an expected" in r for r in e.rejection_reasons)


def test_session_missing_from_both_series_is_ineligible() -> None:
    index = sessions(6).delete(3)
    bars = {"SMH": regime_frame(REGIME_CLOSE, index), "AAA": bar_frame(STOCK, index)}
    as_of = index[-1].date()
    [e] = _evaluate(bars, ["AAA"], as_of=as_of)
    assert not e.eligible
    assert e.score is None
    assert any(r.startswith("breakout: undefined input") for r in e.rejection_reasons)
    # The same bars without the calendar would wrongly pass: the gate is what catches it.
    [unchecked] = evaluate_universe(
        bars,
        "SMH",
        SMALL_PARAMS,
        as_of,
        tradable=["AAA"],
        quality=quality_for(as_of, "SMH", "AAA"),
    )
    assert [r for r in unchecked.rejection_reasons if "calendar" not in r] == []


def test_bar_on_unexpected_session_is_ineligible() -> None:
    [e] = _evaluate(_base(), ["AAA"], calendar=CALENDAR.delete(2))
    assert not e.eligible
    assert any("not expected trading sessions" in r for r in e.rejection_reasons)


def test_non_positive_proposed_stop_is_ineligible() -> None:
    # A wide final bar: TR4 = 30 - 11 = 19, ATR4 = (1 + 19) / 2 = 10, stop = 12 - 2 * 10 = -8.
    bars = {"SMH": regime_frame(REGIME_CLOSE), "AAA": _stock(high={4: 30.0})}
    [e] = _evaluate(bars, ["AAA"])
    assert all(c.passed for c in e.conditions)
    assert e.atr == 10.0
    assert not e.eligible
    assert e.rejection_reasons == ("proposed stop -8 = close 12 - 2 x ATR 10 is not positive",)
    assert e.proposed_stop is None
    assert e.proposed_target is None
    assert e.rank is None


def test_non_finite_proposed_target_makes_symbol_ineligible() -> None:
    """F-026: a validated finite multiple whose target overflows to inf is rejected, not emitted."""
    params = MomentumV1Params.model_validate(
        {**SMALL_PARAMS.model_dump(), "target_atr_multiple": 1.7e308}
    )
    [e] = evaluate_universe(
        _base(),
        "SMH",
        params,
        AS_OF,
        tradable=["AAA"],
        quality=quality_for(AS_OF, "SMH", "AAA"),
        expected_sessions=CALENDAR,
    )
    assert not e.eligible
    assert (e.proposed_stop, e.proposed_target, e.rank) == (None, None, None)
    assert any("proposed target" in r and "not finite" in r for r in e.rejection_reasons)
