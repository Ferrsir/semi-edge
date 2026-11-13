"""Deterministic daily-bar stop-first fill rules."""

from __future__ import annotations

from semiedge.backtest.costs import checked
from semiedge.backtest.results import ExitReason


def market_price(price: float, slippage_bps: float, *, sell: bool, context: str) -> float:
    return checked(
        price * (1 + (-1 if sell else 1) * slippage_bps / 10_000),
        context,
        "fill_price",
        positive=True,
    )


def protective_fill(
    *,
    opened: float,
    high: float,
    low: float,
    stop: float,
    target: float,
    gap: bool,
    slippage_bps: float,
    context: str,
) -> tuple[float, ExitReason] | None:
    if (opened if gap else low) <= stop:
        return (
            market_price(opened if gap else stop, slippage_bps, sell=True, context=context),
            "stop_gap" if gap else "stop",
        )
    if (opened if gap else high) >= target:
        return checked(
            target, context, "fill_price", positive=True
        ), "target_gap" if gap else "target"
    return None
