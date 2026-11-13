"""Pure adverse market fills and explicit fill charges; no broker schedules or I/O."""

from __future__ import annotations

import math
from typing import Literal

from semiedge.config.models import CostSettings


def _positive(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("price must be a positive finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError("price must be a positive finite number") from None
    if not math.isfinite(result) or result <= 0:
        raise ValueError("price must be a positive finite number")
    return result


def _validate(side: Literal["buy", "sell"], costs: CostSettings) -> CostSettings:
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    if not isinstance(costs, CostSettings):
        raise ValueError("costs must be CostSettings")
    # Revalidate even model_construct/model_copy values at the arithmetic boundary.
    return CostSettings.model_validate(costs.model_dump())


def market_price(reference: float, side: Literal["buy", "sell"], costs: CostSettings) -> float:
    """Adverse slippage on a market fill; limit targets do not call this helper."""
    checked = _validate(side, costs)
    price = _positive(reference)
    slip = checked.slippage_bps / 10_000
    result = price * (1 + slip if side == "buy" else 1 - slip)
    return _positive(result)


def fill_cost(
    price: float, shares: int, side: Literal["buy", "sell"], costs: CostSettings
) -> float:
    """Commission per fill plus a sell-only notional fee; slippage is already in price."""
    checked = _validate(side, costs)
    price = _positive(price)
    if type(shares) is not int or shares <= 0:
        raise ValueError("shares must be a positive integer")
    try:
        charge = checked.commission_per_order + checked.commission_per_share * shares
        if side == "sell":
            notional = price * shares
            if not math.isfinite(notional):
                raise ValueError("fill notional must be finite")
            charge += checked.sell_fee_rate * notional
    except OverflowError:
        raise ValueError("fill cost overflowed") from None
    if not math.isfinite(charge) or charge < 0:
        raise ValueError("fill cost must be finite and nonnegative")
    return charge
