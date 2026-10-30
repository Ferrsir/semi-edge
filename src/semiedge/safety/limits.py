"""Pre-approved ceilings for configurable risk limits.

``config/risk.yaml`` may TIGHTEN any limit but can never exceed these ceilings.
Raising a ceiling is a change to this restricted file and therefore requires
explicit human approval (RISK_POLICY.md, docs/governance/ENGINEERING_POLICY.md).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RiskLimitCeilings:
    """Maximum permissible values for risk configuration (fractions of equity)."""

    max_risk_per_trade_pct: float = 0.0025
    max_position_pct: float = 0.05
    max_gross_exposure_pct: float = 0.20
    max_positions: int = 4
    daily_loss_halt_pct: float = 0.01
    drawdown_halt_pct: float = 0.05


RISK_LIMIT_CEILINGS = RiskLimitCeilings()
