"""SimGateway (intraday_execution.md §4, #162): bar fills, protection rules, no shorting,
cash settlement, account snapshot, and no Alpaca import. Hand-built bars; no network."""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from semiedge.broker.gateway import BrokerOrderStatus, OrderRejected, OrderRequest
from semiedge.broker.sim import SimBar, SimGateway
from semiedge.clock import FrozenClock
from semiedge.data.calendar import TradingCalendar
from semiedge.execution.client_ids import make_client_order_id, namespace, parse_client_order_id
from semiedge.risk.types import OrderSide

pytestmark = pytest.mark.safety
NS = namespace("sim-store")
DAY = date(2026, 3, 2)
T0 = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)  # 09:30 New York
CAL = TradingCalendar()


def _bar(o, h=None, lo=None, c=None):
    return SimBar(
        o, h if h is not None else o, lo if lo is not None else o, c if c is not None else o, 1000.0
    )


def _k(k):
    return T0 + timedelta(minutes=5 * k)


def _gw(model="unrestricted", slip=0.0, cash=5000.0):
    clock = FrozenClock(_k(1))
    gw = SimGateway(cash, model, slip, parse_client_order_id, CAL, clock)
    return gw, clock


def _cid(kind, symbol="NVDA", attempt=1):
    return make_client_order_id(NS, kind, DAY, symbol, attempt)


def _buy(qty=10, symbol="NVDA"):
    return OrderRequest(_cid("entry", symbol), symbol, OrderSide.BUY, qty, "market", "day")


def _oco(qty=10, target=102.0, stop=99.0, symbol="NVDA"):
    return OrderRequest(
        _cid("protection", symbol), symbol, OrderSide.SELL, qty, "limit", "day", "oco", target, stop
    )


def _entered(slip=0.0, model="unrestricted"):
    """Buy decided after bar 0, filled at bar 1's open of 100."""
    gw, clock = _gw(model, slip)
    gw.on_bar(_k(0), {"NVDA": _bar(100.0)})
    gw.send_order(_buy())
    gw.on_bar(_k(1), {"NVDA": _bar(100.0, 100.5, 99.5, 100.2)})
    return gw, clock


def test_market_buy_fills_at_the_next_bar_open_plus_slippage():
    gw, _ = _entered(slip=10.0)
    (fill,) = gw.list_fills(after=T0 - timedelta(days=1))
    assert fill.price == pytest.approx(100.1)
    (pos,) = gw.get_positions()
    assert (pos.symbol, pos.qty) == ("NVDA", 10.0)
    assert gw.cash == pytest.approx(5000 - 1001.0)


def test_protection_is_live_on_the_entry_bar_without_a_gap_test():
    gw, _ = _entered()
    gw.send_order(_oco(stop=99.6))  # the entry bar's low of 99.5 touches the stop
    stop_fill = gw.list_fills(after=T0 - timedelta(days=1))[-1]
    assert stop_fill.side is OrderSide.SELL
    assert stop_fill.price == pytest.approx(99.6)
    assert gw.get_positions() == ()


def test_protection_untouched_on_the_entry_bar_waits_for_the_next_bar():
    gw, _ = _entered()
    gw.send_order(_oco())
    assert len(gw.list_fills(after=T0 - timedelta(days=1))) == 1
    gw.on_bar(_k(2), {"NVDA": _bar(100.0, 102.5, 99.8, 102.0)})
    assert gw.list_fills(after=T0 - timedelta(days=1))[-1].price == pytest.approx(102.0)


def test_gap_through_the_stop_fills_at_the_open():
    gw, _ = _entered()
    gw.send_order(_oco())
    gw.on_bar(_k(2), {"NVDA": _bar(98.0, 98.5, 97.0, 98.2)})
    assert gw.list_fills(after=T0 - timedelta(days=1))[-1].price == pytest.approx(98.0)


def test_a_bar_touching_both_levels_counts_as_the_stop():
    gw, _ = _entered(slip=10.0)
    gw.send_order(_oco())
    gw.on_bar(_k(2), {"NVDA": _bar(100.0, 102.5, 98.5, 100.0)})
    assert gw.list_fills(after=T0 - timedelta(days=1))[-1].price == pytest.approx(99.0 * 0.999)


def test_rejections_duplicate_foreign_buying_power_and_shorting():
    gw, _ = _entered()
    with pytest.raises(OrderRejected) as dup:
        gw.send_order(_buy())
    assert dup.value.duplicate_client_order_id is True
    with pytest.raises(OrderRejected):
        gw.send_order(_buy(qty=1000, symbol="AMD"))  # no price for AMD yet
    gw.on_bar(_k(2), {"NVDA": _bar(100.0), "AMD": _bar(100.0)})
    with pytest.raises(OrderRejected, match="buying power"):
        gw.send_order(_buy(qty=100, symbol="AMD"))
    gw.send_order(_oco())
    flatten = OrderRequest(_cid("flatten"), "NVDA", OrderSide.SELL, 1, "market", "day")
    with pytest.raises(OrderRejected, match="no shorting"):
        gw.send_order(flatten)  # the OCO already covers all 10 shares


def test_cancel_then_flatten_sells_at_the_next_open():
    gw, _ = _entered(slip=10.0)
    oco = gw.send_order(_oco())
    gw.request_cancel(oco.broker_order_id)
    assert gw.lookup_order(oco.client_order_id).status is BrokerOrderStatus.CANCELED
    gw.send_order(OrderRequest(_cid("flatten"), "NVDA", OrderSide.SELL, 10, "market", "day"))
    gw.on_bar(_k(2), {"NVDA": _bar(101.0, 103.0, 98.0, 101.0)})
    assert gw.list_fills(after=T0 - timedelta(days=1))[-1].price == pytest.approx(101.0 * 0.999)
    assert gw.get_positions() == ()


def test_cash_account_settles_sales_next_session():
    gw, _ = _entered(model="cash")
    gw.send_order(OrderRequest(_cid("flatten"), "NVDA", OrderSide.SELL, 10, "market", "day"))
    gw.on_bar(_k(2), {"NVDA": _bar(100.0)})
    assert gw.cash == pytest.approx(4000.0)
    assert gw.unsettled == pytest.approx(1000.0)
    assert gw.get_account_snapshot().buying_power == pytest.approx(4000.0)
    gw.start_session(date(2026, 3, 3))
    assert gw.cash == pytest.approx(5000.0)
    assert gw.unsettled == 0.0


def test_account_snapshot_is_valid_during_and_after_the_session():
    gw, clock = _entered()
    snap = gw.get_account_snapshot()
    assert snap.market_is_open is True
    assert snap.equity == pytest.approx(5000.0 + 10 * 0.2)  # marked at the 100.2 close
    clock.advance(timedelta(hours=8))
    after = gw.get_account_snapshot()
    assert after.market_is_open is False
    assert after.next_open > after.as_of


def test_bars_must_arrive_in_order():
    gw, _ = _entered()
    with pytest.raises(ValueError, match="time order"):
        gw.on_bar(_k(1), {"NVDA": _bar(100.0)})


def test_sim_module_never_imports_alpaca():
    source = Path(__import__("semiedge.broker.sim", fromlist=["x"]).__file__).read_text()
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    assert not any(n == "alpaca" or n.startswith("alpaca.") for n in names)
    assert "semiedge.safety.broker" not in names
