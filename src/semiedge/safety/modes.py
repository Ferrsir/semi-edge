"""Trading modes and the rules that decide whether orders may be submitted.

Invariants (enforced here, tested in tests/safety/):

* ``live`` mode exists only so the architecture can name it; selecting it is
  always a :class:`SafetyViolation` in this MVP.
* ``ALLOW_LIVE_TRADING=true`` is always a :class:`SafetyViolation`.
* Order submission requires ``paper`` mode AND ``ENABLE_ORDER_SUBMISSION=true``.
* ``sim`` mode (#162) simulates fills on live bars and never submits an order:
  ``ENABLE_ORDER_SUBMISSION=true`` with ``sim`` is a :class:`SafetyViolation`.
* Every check fails closed: unknown or inconsistent flags raise.
"""

from __future__ import annotations

from enum import StrEnum


class TradingMode(StrEnum):
    """Operating mode of the platform."""

    SCANNER = "scanner"
    BACKTEST = "backtest"
    PAPER = "paper"
    SIM = "sim"  # simulated fills on live bars; never sends an order (intraday_execution.md)
    LIVE = "live"  # Named for architecture only. Unsupported; can never be activated in the MVP.


SUPPORTED_MODES: frozenset[TradingMode] = frozenset(
    {TradingMode.SCANNER, TradingMode.BACKTEST, TradingMode.PAPER, TradingMode.SIM}
)


class SafetyViolation(RuntimeError):  # noqa: N818 - domain name, deliberately not "...Error"
    """Raised when a configuration or action would breach a trading-safety invariant."""


def validate_safety_flags(
    mode: TradingMode,
    *,
    enable_order_submission: bool,
    allow_live_trading: bool,
) -> None:
    """Validate the combination of safety flags; raise :class:`SafetyViolation` if unsafe."""
    if type(enable_order_submission) is not bool or type(allow_live_trading) is not bool:
        raise SafetyViolation("Safety flags must be explicit booleans, not truthy values.")
    if not isinstance(mode, TradingMode):
        raise SafetyViolation(f"Unknown trading mode {mode!r}")
    if mode is TradingMode.LIVE or mode not in SUPPORTED_MODES:
        raise SafetyViolation(
            "TRADING_MODE=live is not supported. SEMI-EDGE is paper-only; real-money trading "
            "requires a separate, human-approved implementation and deployment process."
        )
    if allow_live_trading:
        raise SafetyViolation(
            "ALLOW_LIVE_TRADING=true is not supported in the MVP and must remain false."
        )
    if enable_order_submission and mode is not TradingMode.PAPER:
        raise SafetyViolation(
            "ENABLE_ORDER_SUBMISSION=true is only valid with TRADING_MODE=paper "
            f"(got {mode.value})."
        )


def order_submission_permitted(
    mode: TradingMode,
    *,
    enable_order_submission: bool,
    allow_live_trading: bool,
) -> bool:
    """Return True only when PAPER order submission has been explicitly enabled.

    Raises :class:`SafetyViolation` for unsafe flag combinations rather than returning False,
    so a misconfiguration is surfaced instead of silently ignored.
    """
    validate_safety_flags(
        mode, enable_order_submission=enable_order_submission, allow_live_trading=allow_live_trading
    )
    return mode is TradingMode.PAPER and enable_order_submission


def assert_order_submission_allowed(
    mode: TradingMode,
    *,
    enable_order_submission: bool,
    allow_live_trading: bool,
) -> None:
    """Raise unless PAPER order submission is explicitly enabled. Call before any order call."""
    if not order_submission_permitted(
        mode, enable_order_submission=enable_order_submission, allow_live_trading=allow_live_trading
    ):
        raise SafetyViolation(
            "Order submission is disabled. It requires TRADING_MODE=paper and "
            "ENABLE_ORDER_SUBMISSION=true."
        )
