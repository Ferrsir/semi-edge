"""``SimGateway``: the SIM-mode broker (spec ``intraday_execution.md`` §4, #162).

Implements :class:`semiedge.broker.gateway.BrokerGateway` with no network and no credentials.
The engine feeds it each completed 5-minute bar (:meth:`SimGateway.on_bar`) and it fills orders
by the backtests' rules, so a SIM run can reproduce a backtest's trade list:

* a market order sent after bar ``k`` closes fills at bar ``k+1``'s open, plus slippage on a buy
  and minus slippage on a sell;
* a protective sell (OCO or stop) placed right after its entry fill is live for the rest of that
  entry bar: it is checked against the bar's low (stop) and high (target), with no open-gap test;
* from the next bar on, an open at or through a level fills at the open, the stop wins a tie,
  and a target fills at its price; slippage applies to every exit.

Positions and cash change only through these fills. Sale proceeds in the ``cash`` account model
settle at the next session (:meth:`SimGateway.start_session`).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

from semiedge.broker.account import AccountSnapshot, BrokerDataError, PositionSnapshot
from semiedge.broker.gateway import (
    BrokerFill,
    BrokerOrder,
    BrokerOrderStatus,
    OrderRejected,
    OrderRequest,
    open_sell_qty,
)
from semiedge.clock import Clock
from semiedge.data.calendar import TradingCalendar
from semiedge.risk.types import OrderSide

SimAccountModel = Literal["unrestricted", "margin_pdt", "cash"]
_OPEN = (BrokerOrderStatus.ACCEPTED,)


@dataclass(frozen=True, slots=True)
class SimBar:
    o: float
    h: float
    lo: float
    c: float
    v: float


@dataclass
class _Order:
    ask: OrderRequest  # the order as sent
    broker_id: str
    submitted_at: datetime
    status: BrokerOrderStatus = BrokerOrderStatus.ACCEPTED
    filled_qty: int = 0
    avg_price: float | None = None
    updated_at: datetime | None = None
    live_from_bar: datetime | None = None  # protective sells: the first bar they can fill on


@dataclass
class _Position:
    qty: int
    avg: float
    opened_bar: datetime


@dataclass
class SimGateway:
    """In-memory paper broker for SIM mode. Never imports or calls Alpaca."""

    starting_cash: float
    account_model: SimAccountModel
    slippage_bps: float
    parse_client_order_id: Callable[[str], object | None]
    calendar: TradingCalendar
    clock: Clock
    cash: float = field(init=False)
    unsettled: float = field(init=False, default=0.0)
    last_equity: float = field(init=False)
    _orders: dict[str, _Order] = field(init=False, default_factory=dict)
    _fills: list[BrokerFill] = field(init=False, default_factory=list)
    _positions: dict[str, _Position] = field(init=False, default_factory=dict)
    _last_close: dict[str, float] = field(init=False, default_factory=dict)
    _bar: datetime | None = field(init=False, default=None)
    _bars: Mapping[str, SimBar] = field(init=False, default_factory=dict)
    _seq: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if not (math.isfinite(self.starting_cash) and self.starting_cash > 0):
            raise ValueError("starting_cash must be positive")
        if self.account_model not in ("unrestricted", "margin_pdt", "cash"):
            raise ValueError("unknown account model")
        if not (math.isfinite(self.slippage_bps) and self.slippage_bps >= 0):
            raise ValueError("slippage_bps must be non-negative")
        self.cash = self.starting_cash
        self.last_equity = self.starting_cash

    # ------------------------------------------------------------------ feed

    def start_session(self, session: date) -> None:
        """T+1: the previous session's sale proceeds settle before ``session``'s open."""
        del session  # recorded by the caller; settlement does not depend on which session
        self.cash += self.unsettled
        self.unsettled = 0.0
        self.last_equity = self._equity()

    def on_bar(self, bar_start: datetime, bars: Mapping[str, SimBar]) -> None:
        """Process one completed 5-minute bar for every symbol that has it."""
        if self._bar is not None and bar_start <= self._bar:
            raise ValueError("bars must arrive in time order")
        self._bar, self._bars = bar_start, bars
        slip = self.slippage_bps / 10_000.0
        for order in self._live(lambda o: o.ask.order_class == "simple"):
            bar = bars.get(order.ask.symbol)
            if bar is None:
                continue  # no trade for that symbol in this bar: the market order waits
            if order.ask.side is OrderSide.BUY:
                self._fill(order, bar.o * (1 + slip), bar_start)
            else:
                self._fill(order, bar.o * (1 - slip), bar_start)
        for order in self._live(lambda o: o.ask.order_class == "oco"):
            if order.live_from_bar is not None and bar_start >= order.live_from_bar:
                self._check_protection(order, bars.get(order.ask.symbol), gaps=True)
        for sym, bar in bars.items():
            self._last_close[sym] = bar.c

    # ------------------------------------------------------------------ BrokerGateway

    def send_order(self, req: OrderRequest) -> BrokerOrder:
        if self.parse_client_order_id(req.client_order_id) is None:
            raise OrderRejected("client_order_id is not ours", duplicate_client_order_id=False)
        if req.client_order_id in self._orders:
            raise OrderRejected("client_order_id must be unique", duplicate_client_order_id=True)
        if req.side is OrderSide.BUY:
            estimate = self._last_close.get(req.symbol)
            if estimate is None:
                raise OrderRejected("no price for symbol", duplicate_client_order_id=False)
            if req.qty * estimate > self._buying_power():
                raise OrderRejected("insufficient buying power", duplicate_client_order_id=False)
        else:
            held = self._positions.get(req.symbol)
            available = (held.qty if held else 0) - open_sell_qty(
                [self._view(o) for o in self._orders.values()], req.symbol
            )
            if req.qty > available:
                raise OrderRejected(
                    "sell exceeds position (no shorting)", duplicate_client_order_id=False
                )
        now = self.clock.now()
        self._seq += 1
        order = _Order(req, f"sim-{self._seq}", now, updated_at=now)
        self._orders[req.client_order_id] = order
        if req.order_class == "oco":
            held = self._positions[req.symbol]
            if self._bar is not None and held.opened_bar == self._bar:
                self._check_protection(order, self._bars.get(req.symbol), gaps=False)
            # live from the next bar (bar starts are whole minutes, so +1 microsecond is "after")
            order.live_from_bar = (
                datetime.min.replace(tzinfo=UTC)
                if self._bar is None
                else self._bar.replace(microsecond=1)
            )
        return self._view(order)

    def lookup_order(self, client_order_id: str) -> BrokerOrder | None:
        order = self._orders.get(client_order_id)
        return None if order is None else self._view(order)

    def list_orders(
        self, *, status: Literal["open", "all"], after: datetime | None
    ) -> tuple[BrokerOrder, ...]:
        rows = [
            o
            for o in self._orders.values()
            if (after is None or o.submitted_at > after) and (status == "all" or o.status in _OPEN)
        ]
        return tuple(self._view(o) for o in rows)

    def list_fills(self, *, after: datetime) -> tuple[BrokerFill, ...]:
        return tuple(f for f in self._fills if f.ts > after)

    def request_cancel(self, broker_order_id: str) -> None:
        for order in self._orders.values():
            if order.broker_id == broker_order_id:
                if order.status in _OPEN:
                    order.status = BrokerOrderStatus.CANCELED
                    order.updated_at = self.clock.now()
                return
        raise BrokerDataError("unknown broker order id")

    def get_positions(self) -> tuple[PositionSnapshot, ...]:
        out = []
        for sym in sorted(self._positions):
            p = self._positions[sym]
            px = self._last_close.get(sym, p.avg)
            out.append(
                PositionSnapshot(
                    symbol=sym,
                    qty=float(p.qty),
                    side="long",
                    avg_entry_price=p.avg,
                    market_value=p.qty * px,
                    cost_basis=p.qty * p.avg,
                    unrealized_pl=p.qty * (px - p.avg),
                    unrealized_plpc=px / p.avg - 1,
                    current_price=px,
                    asset_class="us_equity",
                )
            )
        return tuple(out)

    def get_account_snapshot(self) -> AccountSnapshot:
        now = self.clock.now()
        today = now.date()
        session = today if self.calendar.is_session(today) else self.calendar.next_session(today)
        open_, close = self.calendar.session_open(session), self.calendar.session_close(session)
        is_open = open_ <= now < close
        if now >= close:
            nxt = self.calendar.next_session(session)
            open_, close = self.calendar.session_open(nxt), self.calendar.session_close(nxt)
        elif is_open:
            open_ = self.calendar.session_open(self.calendar.next_session(session))
        positions = self.get_positions()
        long_value = sum(p.market_value for p in positions)
        return AccountSnapshot(
            account_number_masked="****SIM0",
            status="ACTIVE",
            currency="USD",
            equity=self.cash + self.unsettled + long_value,
            last_equity=self.last_equity,
            cash=self.cash + self.unsettled,
            buying_power=self._buying_power(),
            long_market_value=long_value,
            pattern_day_trader=None,
            trading_blocked=False,
            account_blocked=False,
            trade_suspended_by_user=False,
            market_is_open=is_open,
            next_open=open_,
            next_close=close,
            as_of=now,
            positions=positions,
        )

    # ------------------------------------------------------------------ internals

    def _live(self, keep: Callable[[_Order], bool]) -> list[_Order]:
        return [o for o in self._orders.values() if o.status in _OPEN and keep(o)]

    def _buying_power(self) -> float:
        return max(self.cash, 0.0)  # settled cash only; margin is not used (gross <= 75%)

    def _equity(self) -> float:
        return (
            self.cash
            + self.unsettled
            + sum(p.qty * self._last_close.get(s, p.avg) for s, p in self._positions.items())
        )

    def _check_protection(self, order: _Order, bar: SimBar | None, *, gaps: bool) -> None:
        if bar is None:
            return
        slip = self.slippage_bps / 10_000.0
        stop, limit = order.ask.stop_price, order.ask.limit_price
        if stop is None or limit is None:  # pragma: no cover - OrderRequest guarantees both
            return
        if gaps and (bar.o <= stop or bar.o >= limit):
            price = bar.o  # gapped through a level: filled at the open
        elif bar.lo <= stop:
            price = stop
        elif bar.h >= limit:
            price = limit
        else:
            return
        assert self._bar is not None  # noqa: S101 - a bar is always set when checking
        self._fill(order, price * (1 - slip), self._bar)

    def _fill(self, order: _Order, price: float, bar_start: datetime) -> None:
        req = order.ask
        qty = req.qty
        if req.side is OrderSide.BUY:
            self.cash -= qty * price
            held = self._positions.get(req.symbol)
            if held is None:
                self._positions[req.symbol] = _Position(qty, price, bar_start)
            else:
                total = held.qty + qty
                held.avg = (held.avg * held.qty + price * qty) / total
                held.qty = total
        else:
            held = self._positions[req.symbol]
            held.qty -= qty
            if held.qty == 0:
                del self._positions[req.symbol]
            if self.account_model == "cash":
                self.unsettled += qty * price
            else:
                self.cash += qty * price
        order.status = BrokerOrderStatus.FILLED
        order.filled_qty = qty
        order.avg_price = price
        order.updated_at = self.clock.now()
        self._seq += 1
        self._fills.append(
            BrokerFill(
                f"simfill-{self._seq}",
                order.broker_id,
                req.symbol,
                req.side,
                qty,
                price,
                order.updated_at,
            )
        )

    def _view(self, order: _Order) -> BrokerOrder:
        req = order.ask
        return BrokerOrder(
            broker_order_id=order.broker_id,
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            side=req.side,
            qty=req.qty,
            filled_qty=order.filled_qty,
            filled_avg_price=order.avg_price,
            status=order.status,
            order_type=req.order_type,
            time_in_force=req.time_in_force,
            order_class=req.order_class,
            legs=(),
            submitted_at=order.submitted_at,
            updated_at=order.updated_at or order.submitted_at,
        )


__all__ = ["SimAccountModel", "SimBar", "SimGateway"]
