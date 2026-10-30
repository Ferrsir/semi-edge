"""Trading-mode and brokerage safety guards.

RESTRICTED PATH: changes here require explicit human approval (see
docs/governance/ENGINEERING_POLICY.md). This package is the single place that
decides whether order submission is permitted and how broker clients are built.
"""

from semiedge.safety.limits import RISK_LIMIT_CEILINGS, RiskLimitCeilings
from semiedge.safety.modes import (
    SUPPORTED_MODES,
    SafetyViolation,
    TradingMode,
    assert_order_submission_allowed,
    order_submission_permitted,
    validate_safety_flags,
)

__all__ = [
    "RISK_LIMIT_CEILINGS",
    "SUPPORTED_MODES",
    "RiskLimitCeilings",
    "SafetyViolation",
    "TradingMode",
    "assert_order_submission_allowed",
    "order_submission_permitted",
    "validate_safety_flags",
]
