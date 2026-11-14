"""Gateway contract types (phase2-4 §6.1, §6.4, §9.3; #21). Pure; no client, no network."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from semiedge.broker.gateway import (
    BrokerOrder,
    BrokerOrderStatus,
    OrderRejected,
    OrderRequest,
    UnmappableBrokerStatus,
    local_status,
    open_sell_qty,
)
from semiedge.execution.client_ids import make_client_order_id, namespace
from semiedge.risk.types import OrderSide

pytestmark = pytest.mark.safety
NS = namespace("store")
D = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 15, tzinfo=UTC)


def _cid(kind="entry", symbol="NVDA"):
    return make_client_order_id(NS, kind, D, symbol, 1)


def test_valid_requests():
    OrderRequest(_cid(), "NVDA", OrderSide.BUY, 10, "market", "opg")
    OrderRequest(_cid(), "NVDA", OrderSide.BUY, 10, "market", "day")
    OrderRequest(_cid("protection"), "NVDA", OrderSide.SELL, 10, "limit", "day", "oco", 105.0, 98.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"client_order_id": ""},
        {"symbol": "AMD"},  # differs from the id's symbol
        {"qty": 0},
        {"qty": 1.0},
        {"qty": True},
        {"order_type": "stop"},
        {"time_in_force": "ioc"},
        {"order_class": "bracket"},
        {"stop_price": 98.0},  # a simple market order with a stop
        {"limit_price": 101.0},  # a market order with a limit
    ],
)
def test_invalid_requests_are_refused(kwargs):
    base = {
        "client_order_id": _cid(),
        "symbol": "NVDA",
        "side": OrderSide.BUY,
        "qty": 10,
        "order_type": "market",
        "time_in_force": "day",
    }
    with pytest.raises(ValueError, match=r"."):
        OrderRequest(**{**base, **kwargs})


@pytest.mark.parametrize(
    ("side", "limit", "stop"),
    [
        (OrderSide.BUY, 105.0, 98.0),  # an oco is a sell
        (OrderSide.SELL, 98.0, 105.0),  # stop above the limit
        (OrderSide.SELL, 105.0, None),
        (OrderSide.SELL, float("nan"), 98.0),
    ],
)
def test_invalid_oco_is_refused(side, limit, stop):
    with pytest.raises(ValueError, match=r"."):
        OrderRequest(_cid("protection"), "NVDA", side, 10, "limit", "day", "oco", limit, stop)


def _order(status, *, qty=10, filled=0, side=OrderSide.SELL, symbol="NVDA"):
    return BrokerOrder(
        "b1", _cid("protection", symbol), symbol, side, qty, filled, None, status,
        "limit", "day", "oco", (), NOW, NOW,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("status", "local"),
    [
        (BrokerOrderStatus.NEW, "accepted"),
        (BrokerOrderStatus.PENDING_NEW, "accepted"),
        (BrokerOrderStatus.ACCEPTED, "accepted"),
        (BrokerOrderStatus.HELD, "accepted"),
        (BrokerOrderStatus.PARTIALLY_FILLED, "partially_filled"),
        (BrokerOrderStatus.FILLED, "filled"),
        (BrokerOrderStatus.CANCELED, "canceled"),
        (BrokerOrderStatus.EXPIRED, "expired"),
        (BrokerOrderStatus.REJECTED, "rejected"),
        (BrokerOrderStatus.PENDING_CANCEL, "cancel_pending"),
    ],
)
def test_broker_to_local_mapping(status, local):
    assert local_status(_order(status)) == local


def test_done_for_day_maps_to_expired_only_when_nothing_is_open():
    assert local_status(_order(BrokerOrderStatus.DONE_FOR_DAY, filled=10)) == "expired"
    with pytest.raises(UnmappableBrokerStatus):
        local_status(_order(BrokerOrderStatus.DONE_FOR_DAY, filled=3))


@pytest.mark.parametrize("status", [BrokerOrderStatus.REPLACED, BrokerOrderStatus.OTHER])
def test_replaced_and_other_are_reconciliation_halts(status):
    with pytest.raises(UnmappableBrokerStatus):
        local_status(_order(status))


def test_unknown_broker_status_parses_as_other():
    assert BrokerOrderStatus.parse("calculated") is BrokerOrderStatus.OTHER
    assert BrokerOrderStatus.parse("filled") is BrokerOrderStatus.FILLED


def test_open_sell_qty_counts_remaining_open_sells_only():
    orders = [
        _order(BrokerOrderStatus.ACCEPTED, qty=10, filled=4),
        _order(BrokerOrderStatus.FILLED, qty=5, filled=5),
        _order(BrokerOrderStatus.ACCEPTED, side=OrderSide.BUY),
        _order(BrokerOrderStatus.ACCEPTED, symbol="AMD"),
    ]
    assert open_sell_qty(orders, "NVDA") == 6


def test_order_rejected_carries_the_duplicate_flag():
    err = OrderRejected("client_order_id must be unique", duplicate_client_order_id=True)
    assert err.duplicate_client_order_id is True
