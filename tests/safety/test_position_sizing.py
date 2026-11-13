"""Safety tests for ``semiedge.risk.sizing.propose_position_size`` (issue #12)."""

from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from semiedge.config.loader import load_app_config
from semiedge.risk import SizingResult, propose_position_size
from semiedge.safety.limits import RISK_LIMIT_CEILINGS

pytestmark = pytest.mark.safety

RISK = RISK_LIMIT_CEILINGS.max_risk_per_trade_pct  # 0.0025
ALLOC = RISK_LIMIT_CEILINGS.max_position_pct  # 0.05


def _size(
    equity: float, entry: float, stop: float, risk: float = RISK, alloc: float = ALLOC
) -> SizingResult:
    return propose_position_size(
        equity, entry, stop, max_risk_per_trade_pct=risk, max_position_pct=alloc
    )


# ------------------------------------------------------------------ hand-computed


def test_risk_budget_binds() -> None:
    # risk: 100000*0.0025 = 250 / (100-90 = 10) = 25; alloc: 100000*0.05 = 5000 / 100 = 50
    r = _size(100_000, 100.0, 90.0)
    assert (r.shares, r.shares_by_risk, r.shares_by_allocation) == (25, 25, 50)
    assert r.binding_constraint == "risk"
    assert r.dollar_risk == 250.0
    assert r.notional == 2500.0
    assert r.reason is None


def test_allocation_budget_binds() -> None:
    # risk: 250 / (100-98 = 2) = 125; alloc: 5000 / 100 = 50
    r = _size(100_000, 100.0, 98.0)
    assert (r.shares, r.shares_by_risk, r.shares_by_allocation) == (50, 125, 50)
    assert r.binding_constraint == "allocation"
    assert r.dollar_risk == 100.0
    assert r.notional == 5000.0


def test_tie_reports_risk() -> None:
    # risk: 250 / 5 = 50; alloc: 5000 / 100 = 50
    r = _size(100_000, 100.0, 95.0)
    assert (r.shares, r.binding_constraint) == (50, "risk")


def test_floor_never_rounds_up() -> None:
    # risk: 250 / (30-27 = 3) = 83.33 -> 83; alloc: 5000 / 30 = 166.67 -> 166
    r = _size(100_000, 30.0, 27.0)
    assert (r.shares, r.shares_by_risk, r.shares_by_allocation) == (83, 83, 166)
    assert r.dollar_risk == pytest.approx(249.0)
    assert r.dollar_risk <= 100_000 * RISK


def test_momentum_fixture_trade() -> None:
    # entry 12, stop 9.5 (12 - 2*1.25): risk 250/2.5 = 100; alloc 5000/12 = 416.67 -> 416
    r = _size(100_000, 12.0, 9.5)
    assert (r.shares, r.binding_constraint) == (100, "risk")


def test_tightened_limits_from_config_are_used() -> None:
    r = _size(100_000, 100.0, 90.0, risk=0.001, alloc=0.01)
    # risk 100/10 = 10; alloc 1000/100 = 10
    assert (r.shares_by_risk, r.shares_by_allocation) == (10, 10)


def test_repository_risk_config_is_within_ceilings_and_usable(config_dir: Path) -> None:
    limits = load_app_config(config_dir).risk
    r = propose_position_size(
        100_000,
        100.0,
        90.0,
        max_risk_per_trade_pct=limits.max_risk_per_trade_pct,
        max_position_pct=limits.max_position_pct,
    )
    assert r.shares > 0
    assert r.dollar_risk <= 100_000 * RISK_LIMIT_CEILINGS.max_risk_per_trade_pct
    assert r.notional <= 100_000 * RISK_LIMIT_CEILINGS.max_position_pct


# ------------------------------------------------------------------ zero shares (valid input)


def test_too_expensive_gives_zero_with_reason() -> None:
    # alloc: 1000*0.05 = 50 / 100 -> 0; risk 2.5 / 1 -> 2
    r = _size(1_000, 100.0, 99.0)
    assert r.shares == 0
    assert r.binding_constraint == "allocation"
    assert r.reason is not None
    assert "allocation budget allows 0 shares" in r.reason
    assert (r.dollar_risk, r.notional) == (0.0, 0.0)


def test_wide_stop_gives_zero_risk_shares() -> None:
    # risk: 1000*0.0025 = 2.5 / 50 -> 0
    r = _size(1_000, 100.0, 50.0)
    assert (r.shares, r.binding_constraint) == (0, "risk")
    assert r.reason is not None


# ------------------------------------------------------------------ invalid inputs (fail closed)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"equity": 0.0},
        {"equity": -1.0},
        {"equity": math.nan},
        {"equity": math.inf},
        {"entry": 0.0},
        {"entry": -5.0},
        {"entry": math.nan},
        {"stop": 0.0},
        {"stop": -1.0},
        {"stop": math.inf},
        {"stop": 100.0},  # stop == entry
        {"stop": 101.0},  # stop above entry
        {"risk": 0.0},
        {"risk": -0.001},
        {"risk": math.nan},
        {"alloc": 0.0},
        {"alloc": math.inf},
        {"risk": math.nextafter(RISK, 1.0)},  # one ulp above the ceiling
        {"alloc": math.nextafter(ALLOC, 1.0)},
        {"risk": 0.01},
        {"alloc": 0.5},
        {"equity": True},
        {"entry": "100"},
        {"stop": None},
    ],
)
def test_invalid_inputs_give_zero_shares(kwargs: dict[str, object]) -> None:
    args: dict[str, object] = {
        "equity": 100_000.0,
        "entry": 100.0,
        "stop": 90.0,
        "risk": RISK,
        "alloc": ALLOC,
    } | kwargs
    r = propose_position_size(
        args["equity"],  # type: ignore[arg-type]
        args["entry"],  # type: ignore[arg-type]
        args["stop"],  # type: ignore[arg-type]
        max_risk_per_trade_pct=args["risk"],  # type: ignore[arg-type]
        max_position_pct=args["alloc"],  # type: ignore[arg-type]
    )
    assert r == SizingResult(0, 0, 0, "invalid", 0.0, 0.0, r.reason)
    assert r.reason


def test_ceiling_values_themselves_are_accepted() -> None:
    assert _size(100_000, 100.0, 90.0, risk=RISK, alloc=ALLOC).binding_constraint != "invalid"


def test_numpy_scalars_are_accepted() -> None:
    r = propose_position_size(
        np.float64(100_000),
        np.float64(100.0),
        np.float64(90.0),
        max_risk_per_trade_pct=RISK,
        max_position_pct=ALLOC,
    )
    assert r.shares == 25


# ------------------------------------------------------------------ invariants


def test_budgets_never_exceeded_randomised() -> None:
    rng = np.random.default_rng(42)
    for _ in range(3_000):
        equity = float(rng.uniform(1_000, 10_000_000))
        entry = float(rng.uniform(0.5, 2_000))
        stop = entry - float(rng.uniform(1e-4, entry * 0.999))
        risk = float(rng.uniform(1e-5, RISK))
        alloc = float(rng.uniform(1e-4, ALLOC))
        r = _size(equity, entry, stop, risk, alloc)
        assert r.binding_constraint != "invalid"
        assert r.shares == min(r.shares_by_risk, r.shares_by_allocation) >= 0
        # Exact rational check: never more risk or notional than the budgets allow.
        f_entry, f_rps = Fraction(entry), Fraction(entry) - Fraction(stop)
        assert r.shares * f_rps <= Fraction(equity) * Fraction(risk)
        assert r.shares * f_entry <= Fraction(equity) * Fraction(alloc)
        # ... and one more share would exceed the binding budget (floor, not under-sizing).
        if r.binding_constraint == "risk":
            assert (r.shares + 1) * f_rps > Fraction(equity) * Fraction(risk)
        else:
            assert (r.shares + 1) * f_entry > Fraction(equity) * Fraction(alloc)
        assert (r.reason is None) == (r.shares > 0)


def test_float_rounding_cannot_add_a_share() -> None:
    # 0.1 + 0.2 style traps: 0.3 / 0.1 is 2.9999999999999996 in floats; exact floor is 2 or 3
    # depending on the binary values, never more than the exact quotient.
    r = _size(1_000_000, 0.3, 0.2, risk=RISK, alloc=ALLOC)
    rps = Fraction(0.3) - Fraction(0.2)
    assert r.shares_by_risk == math.floor(Fraction(1_000_000) * Fraction(RISK) / rps)


@pytest.mark.parametrize("name", ["equity", "entry_price", "stop_price"])
def test_integer_too_large_for_float_is_invalid_not_an_exception(name: str) -> None:
    """F-027: an int beyond the float range returns the documented invalid result."""
    kwargs = {"equity": 100_000, "entry_price": 100, "stop_price": 95}
    kwargs[name] = 10**1000
    result = propose_position_size(
        kwargs["equity"],
        kwargs["entry_price"],
        kwargs["stop_price"],
        max_risk_per_trade_pct=RISK,
        max_position_pct=ALLOC,
    )
    assert (result.shares, result.binding_constraint) == (0, "invalid")
    assert result.reason is not None
    assert name in result.reason
