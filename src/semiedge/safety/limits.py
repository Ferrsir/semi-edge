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

    # Raised 2026-11-14 by the owner (#128) to allow the $5k day-trading profile
    # (config/risk_5k.yaml). Default limits stay at DEFAULT_RISK_LIMITS below.
    max_risk_per_trade_pct: float = 0.01
    max_position_pct: float = 0.25
    max_gross_exposure_pct: float = 0.75
    max_positions: int = 4
    daily_loss_halt_pct: float = 0.01
    drawdown_halt_pct: float = 0.05


RISK_LIMIT_CEILINGS = RiskLimitCeilings()

# Defaults for any limit a config file leaves out. They are deliberately NOT the ceilings, so
# raising a ceiling never loosens a run that relies on defaults (#128).
DEFAULT_RISK_LIMITS = RiskLimitCeilings(
    max_risk_per_trade_pct=0.0025,
    max_position_pct=0.05,
    max_gross_exposure_pct=0.20,
    max_positions=4,
    daily_loss_halt_pct=0.01,
    drawdown_halt_pct=0.05,
)
