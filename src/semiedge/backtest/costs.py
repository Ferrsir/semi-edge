"""Finite arithmetic at the simulation's fill boundary."""

from __future__ import annotations

import math

from semiedge.config.models import CostSettings


def checked(value: float, context: str, quantity: str, *, positive: bool = False) -> float:
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{context}: invalid {quantity}: {value}")
    return value


def fill_cost(
    price: float, qty: int, settings: CostSettings, *, sell: bool, context: str
) -> tuple[float, float]:
    checked(price, context, "fill_price", positive=True)
    if type(qty) is not int or qty <= 0:
        raise ValueError(f"{context}: invalid shares")
    try:
        notional = checked(price * qty, context, "notional")
        cost = checked(
            settings.commission_per_order
            + settings.commission_per_share * qty
            + (settings.sell_fee_rate * notional if sell else 0),
            context,
            "cost",
        )
    except OverflowError as exc:
        raise ValueError(f"{context}: invalid notional/cost arithmetic overflow") from exc
    return notional, cost
