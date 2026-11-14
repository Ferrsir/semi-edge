"""Broker gateway contract (spec ``phase2-4-interfaces.md`` §6.1, §6.4; #21).

Types and the protocol shared by every gateway: ``AlpacaPaperGateway`` (paper orders, the only
module that will call Alpaca order endpoints) and ``SimGateway`` (#162, no network). This module
defines no client and makes no network call. Method names deliberately equal no Alpaca
``TradingClient`` method name (§0.5 scan (c)).
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from semiedge.broker.account import AccountSnapshot, BrokerDataError, BrokerError, PositionSnapshot
from semiedge.risk.types import OrderSide
from semiedge.state.models import OrderStatus

__all__ = [
    "BrokerFill",
    "BrokerGateway",
    "BrokerOrder",
    "BrokerOrderStatus",
    "OrderRejected",
    "OrderRequest",
    "SubmitOutcomeUnknown",
    "UnmappableBrokerStatus",
    "local_status",
    "open_sell_qty",
]


class BrokerOrderStatus(StrEnum):
    """Normalised from Alpaca's order status; unknown values become ``OTHER``."""

    NEW = "new"
    ACCEPTED = "accepted"
    PENDING_NEW = "pending_new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    DONE_FOR_DAY = "done_for_day"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    PENDING_CANCEL = "pending_cancel"
    REPLACED = "replaced"
    HELD = "held"
    OTHER = "other"

    @classmethod
    def parse(cls, value: object) -> BrokerOrderStatus:
        if type(value) is not str:
            raise BrokerDataError("order status must be a string")
        try:
            return cls(value)
        except ValueError:
            return cls.OTHER


def _positive_int(value: object, label: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive int")


def _price(value: object, label: str) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be a positive finite float")
    return value


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: OrderSide
    qty: int
    order_type: Literal["market", "limit"]
    time_in_force: Literal["opg", "day", "gtc"]
    order_class: Literal["simple", "oco"] = "simple"
    limit_price: float | None = None  # oco: the take-profit limit (= target)
    stop_price: float | None = None  # oco: the stop-loss stop (= stop); simple: must be None

    def __post_init__(self) -> None:
        # Client-id parsing lives in semiedge.execution, which semiedge.broker may not import
        # (§0.2). Each gateway is built with the parser and checks ids in its write guards.
        if type(self.client_order_id) is not str or not self.client_order_id:
            raise ValueError("client_order_id must be a non-empty string")
        if type(self.symbol) is not str or not self.client_order_id.count(f"-{self.symbol}-"):
            raise ValueError("client_order_id symbol differs from the request symbol")
        if self.side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError("side must be BUY or SELL")
        _positive_int(self.qty, "qty")
        if self.order_type not in ("market", "limit"):
            raise ValueError("order_type must be market or limit")
        if self.time_in_force not in ("opg", "day", "gtc"):
            raise ValueError("time_in_force must be opg, day or gtc")
        if self.order_class == "simple":
            self._check_simple()
        elif self.order_class == "oco":
            self._check_oco()
        else:
            raise ValueError("order_class must be simple or oco")

    def _check_simple(self) -> None:
        if self.stop_price is not None:
            raise ValueError("a simple order carries no stop_price")
        if self.order_type == "limit":
            _price(self.limit_price, "limit_price")
        elif self.limit_price is not None:
            raise ValueError("a market order carries no limit_price")

    def _check_oco(self) -> None:
        if self.side is not OrderSide.SELL or self.order_type != "limit":
            raise ValueError("an oco is a limit SELL (take-profit and stop-loss)")
        limit = _price(self.limit_price, "limit_price")
        stop = _price(self.stop_price, "stop_price")
        if not stop < limit:
            raise ValueError("an oco stop must be below its take-profit limit")


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    qty: int
    filled_qty: int
    filled_avg_price: float | None
    status: BrokerOrderStatus
    order_type: str
    time_in_force: str
    order_class: str
    legs: tuple[BrokerOrder, ...]
    submitted_at: datetime | None
    updated_at: datetime

    @property
    def remaining_qty(self) -> int:
        return self.qty - self.filled_qty


@dataclass(frozen=True, slots=True)
class BrokerFill:
    """One account FILL activity, the key for idempotent fill ingestion (§6.4)."""

    activity_id: str
    broker_order_id: str
    symbol: str
    side: OrderSide
    qty: int
    price: float
    ts: datetime


class SubmitOutcomeUnknown(BrokerError):  # noqa: N818 - spec-verbatim name (§6.1)
    """A timeout, connection reset or 5xx after the request was sent: look up before retrying."""


class OrderRejected(BrokerError):  # noqa: N818 - spec-verbatim name (§6.1)
    """An explicit 4xx rejection. The message is untrusted broker text (§13)."""

    def __init__(self, reason: str, *, duplicate_client_order_id: bool) -> None:
        super().__init__(reason)
        self.duplicate_client_order_id = duplicate_client_order_id


class UnmappableBrokerStatus(BrokerError):  # noqa: N818 - spec-verbatim name (§6.1)
    """``replaced``/``other``: SEMI-EDGE never replaces orders, so this is a RECONCILIATION halt."""


class BrokerGateway(Protocol):
    def send_order(self, request: OrderRequest) -> BrokerOrder: ...
    def lookup_order(self, client_order_id: str) -> BrokerOrder | None: ...
    def list_orders(
        self, *, status: Literal["open", "all"], after: datetime | None
    ) -> tuple[BrokerOrder, ...]: ...
    def list_fills(self, *, after: datetime) -> tuple[BrokerFill, ...]: ...
    def request_cancel(self, broker_order_id: str) -> None: ...
    def get_positions(self) -> tuple[PositionSnapshot, ...]: ...
    def get_account_snapshot(self) -> AccountSnapshot: ...


_ACCEPTED: frozenset[BrokerOrderStatus] = frozenset(
    {
        BrokerOrderStatus.NEW,
        BrokerOrderStatus.PENDING_NEW,
        BrokerOrderStatus.ACCEPTED,
        BrokerOrderStatus.HELD,
    }
)
_DIRECT: dict[BrokerOrderStatus, OrderStatus] = {
    BrokerOrderStatus.PARTIALLY_FILLED: "partially_filled",
    BrokerOrderStatus.FILLED: "filled",
    BrokerOrderStatus.CANCELED: "canceled",
    BrokerOrderStatus.EXPIRED: "expired",
    BrokerOrderStatus.REJECTED: "rejected",
    BrokerOrderStatus.PENDING_CANCEL: "cancel_pending",
}
_TERMINAL_BROKER: frozenset[BrokerOrderStatus] = frozenset(
    {
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.CANCELED,
        BrokerOrderStatus.EXPIRED,
        BrokerOrderStatus.REJECTED,
        BrokerOrderStatus.REPLACED,
    }
)


def local_status(order: BrokerOrder) -> OrderStatus:
    """§6.4 broker -> local mapping.

    ``replaced`` and ``other`` raise :class:`UnmappableBrokerStatus` (RECONCILIATION halt).
    """
    status = order.status
    if status in _ACCEPTED:
        return "accepted"
    if status in _DIRECT:
        return _DIRECT[status]
    if status is BrokerOrderStatus.DONE_FOR_DAY:
        if order.remaining_qty > 0:
            raise UnmappableBrokerStatus("done_for_day with open quantity")
        return "expired"
    raise UnmappableBrokerStatus(f"broker status {status.value!r} is never expected")


def open_sell_qty(orders: Collection[BrokerOrder], symbol: str) -> int:
    """Remaining qty of open SELL orders for ``symbol``; an OCO counts once (§9.3)."""
    return sum(
        o.remaining_qty
        for o in orders
        if o.symbol == symbol and o.side is OrderSide.SELL and o.status not in _TERMINAL_BROKER
    )
