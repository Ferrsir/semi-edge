"""Proposed position size: the smaller of the risk budget and the allocation budget (issue #12).

Formulas (docs/specifications/momentum_v1.md, "Proposed trade parameters")::

    risk_per_share = entry - stop
    shares_risk    = floor(equity * max_risk_per_trade_pct / risk_per_share)
    shares_alloc   = floor(equity * max_position_pct / entry)
    shares         = min(shares_risk, shares_alloc)

Fail closed: any non-finite, non-numeric or non-positive input, ``stop >= entry``, or a
percentage above the pre-approved ceiling in :mod:`semiedge.safety.limits` yields
``shares == 0`` with ``binding_constraint == "invalid"``. Ceilings are never raised or
clamped to; a limit above its ceiling is rejected outright.

The floors are evaluated in exact rational arithmetic on the binary float inputs
(:class:`fractions.Fraction`), so rounding can never produce one share more than the budget
allows. This is a *proposed* size; portfolio limits are the Phase 3 risk manager's job.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from numbers import Real
from typing import Literal

from semiedge.safety.limits import RISK_LIMIT_CEILINGS

__all__ = ["SizingResult", "propose_position_size"]


@dataclass(frozen=True)
class SizingResult:
    shares: int
    shares_by_risk: int
    shares_by_allocation: int
    binding_constraint: Literal["risk", "allocation", "invalid"]
    dollar_risk: float  # shares * (entry - stop)
    notional: float  # shares * entry
    reason: str | None  # set when shares == 0


def _invalid(reason: str) -> SizingResult:
    return SizingResult(0, 0, 0, "invalid", 0.0, 0.0, reason)


def _positive_finite(name: str, value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return f"{name} must be a real number, got {type(value).__name__}"
    try:
        number = float(value)
    except (OverflowError, ValueError):  # e.g. an int beyond the float range (F-027)
        return f"{name} is not representable as a finite float"
    if not math.isfinite(number):
        return f"{name} must be finite, got {number}"
    if number <= 0:
        return f"{name} must be positive, got {number}"
    return None


def propose_position_size(
    equity: float,
    entry_price: float,
    stop_price: float,
    *,
    max_risk_per_trade_pct: float,
    max_position_pct: float,
) -> SizingResult:
    """Shares = min(risk-budget shares, allocation-budget shares); 0 with a reason if invalid.

    On a tie both budgets bind; ``binding_constraint`` then reports ``"risk"``.
    """
    inputs = {
        "equity": equity,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "max_risk_per_trade_pct": max_risk_per_trade_pct,
        "max_position_pct": max_position_pct,
    }
    for name, value in inputs.items():
        problem = _positive_finite(name, value)
        if problem is not None:
            return _invalid(problem)
    if stop_price >= entry_price:
        return _invalid(f"stop_price {stop_price} must be below entry_price {entry_price}")
    ceilings = (
        (
            "max_risk_per_trade_pct",
            max_risk_per_trade_pct,
            RISK_LIMIT_CEILINGS.max_risk_per_trade_pct,
        ),
        ("max_position_pct", max_position_pct, RISK_LIMIT_CEILINGS.max_position_pct),
    )
    for name, value, ceiling in ceilings:
        if value > ceiling:
            return _invalid(f"{name}={value} exceeds the pre-approved ceiling {ceiling}")

    eq = Fraction(float(equity))
    entry = Fraction(float(entry_price))
    risk_per_share = entry - Fraction(float(stop_price))
    risk_budget = eq * Fraction(float(max_risk_per_trade_pct))
    alloc_budget = eq * Fraction(float(max_position_pct))
    by_risk = math.floor(risk_budget / risk_per_share)
    by_alloc = math.floor(alloc_budget / entry)
    shares = min(by_risk, by_alloc)
    binding: Literal["risk", "allocation"] = "risk" if by_risk <= by_alloc else "allocation"
    reason = None
    if shares == 0:
        reason = (
            f"{binding} budget allows 0 shares (risk budget {float(risk_budget):.2f} / "
            f"{float(risk_per_share):.4f} per share; allocation budget "
            f"{float(alloc_budget):.2f} / entry {float(entry):.4f})"
        )
    return SizingResult(
        shares=shares,
        shares_by_risk=by_risk,
        shares_by_allocation=by_alloc,
        binding_constraint=binding,
        dollar_risk=float(shares * risk_per_share),
        notional=float(shares * entry),
        reason=reason,
    )
