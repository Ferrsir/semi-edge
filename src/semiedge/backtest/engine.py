"""Deterministic, calendar-driven next-open simulation; exits are never risk gated."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol, cast

import pandas as pd

from semiedge import __version__
from semiedge.backtest.costs import checked, fill_cost
from semiedge.backtest.data import (
    BacktestData,
    BacktestInputError,
    load_backtest_data,
    validate_inputs,
)
from semiedge.backtest.fills import market_price, protective_fill
from semiedge.backtest.results import BacktestResult, CancelledEntry, EquityPoint, ExitReason, Trade
from semiedge.config.models import (
    BacktestSettings,
    DataConfig,
    MomentumV1Params,
    RiskLimits,
    UniverseConfig,
)
from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import DataSource, empty_bar_frame
from semiedge.data.validation import assess_quality
from semiedge.reporting import SYNTHETIC_BANNER, new_run_id
from semiedge.risk.manager import RiskManager, UnavailableEarningsCalendar, limits_fingerprint
from semiedge.risk.proposals import build_entry_proposals
from semiedge.risk.types import (
    EntryProposal,
    Halt,
    HaltKind,
    PortfolioView,
    PositionView,
    RiskDecision,
)
from semiedge.safety.modes import TradingMode
from semiedge.strategy.exits import held_sessions, time_exit_due
from semiedge.strategy.momentum_v1 import evaluate_universe

__all__ = [
    "BacktestData",
    "BacktestInputError",
    "BacktestRequest",
    "EntryGate",
    "load_backtest_data",
    "run_backtest",
]


class EntryGate(Protocol):
    @property
    def limits(self) -> RiskLimits: ...

    def evaluate_entries(
        self, proposals: Sequence[EntryProposal], portfolio: PortfolioView
    ) -> tuple[RiskDecision, ...]: ...


@dataclass(frozen=True)
class BacktestRequest:
    start: date
    end: date
    universe: UniverseConfig
    params: MomentumV1Params
    risk: RiskLimits
    settings: BacktestSettings
    data_settings: DataConfig
    command: str


@dataclass
class _Position:
    proposal: EntryProposal
    entry_session: date
    price: float
    qty: int
    entry_cost: float
    mark: float

    @property
    def trade_id(self) -> str:
        return f"{self.proposal.symbol}-{self.entry_session:%Y%m%d}"


class _Simulation:
    def __init__(
        self,
        request: BacktestRequest,
        data: BacktestData,
        gate: EntryGate,
        calendar: TradingCalendar,
    ) -> None:
        self.request, self.data, self.gate, self.calendar = request, data, gate, calendar
        self.cash = request.settings.initial_equity
        self.previous_equity = self.cash
        self.peak = self.cash
        self.positions: dict[str, _Position] = {}
        self.entries: tuple[RiskDecision, ...] = ()
        self.exits: set[str] = set()
        self.trades: list[Trade] = []
        self.cancelled: list[CancelledEntry] = []
        self.curve: list[EquityPoint] = []
        self.decisions: list[RiskDecision] = []
        self.halts: list[tuple[date, HaltKind, str]] = []
        self.latched: tuple[Halt, ...] = ()
        self.gaps: list[tuple[date, str]] = []
        self.overshoots: list[tuple[date, str, str]] = []
        # Reuse the manager's halt semantics even with a test gate; no duplicated thresholds.
        self.halt_manager = RiskManager(
            request.risk,
            calendar=calendar,
            earnings=UnavailableEarningsCalendar(),
            tradable_symbols=frozenset(request.universe.tradable_symbols),
            earnings_waiver="backtest_unavailable",
        )

    def row(self, symbol: str, day: date) -> pd.Series[float] | None:
        frame = self.data.bars.get(symbol)
        if frame is None or pd.Timestamp(day) not in frame.index:
            return None
        row = cast("pd.Series[float]", frame.loc[pd.Timestamp(day)])
        for column in ("open", "high", "low", "close"):
            checked(float(row[column]), f"{symbol} {day}", column, positive=True)
        if row["high"] < max(row["open"], row["close"], row["low"]) or row["low"] > min(
            row["open"], row["close"]
        ):
            raise BacktestInputError(f"{symbol} {day}: invalid_ohlc")
        return row

    def market(self, value: float, symbol: str, day: date, *, sell: bool) -> float:
        return market_price(
            value, self.request.settings.costs.slippage_bps, sell=sell, context=f"{symbol} {day}"
        )

    def close(self, symbol: str, day: date, price: float, reason: ExitReason) -> None:
        pos = self.positions.pop(symbol)
        self.exits.discard(symbol)
        p = pos.proposal
        notional, cost = fill_cost(
            price, pos.qty, self.request.settings.costs, sell=True, context=pos.trade_id
        )
        self.cash = checked(self.cash + notional - cost, pos.trade_id, "cash")
        costs = checked(cost + pos.entry_cost, pos.trade_id, "costs")
        gross = checked((price - pos.price) * pos.qty, pos.trade_id, "gross_pnl")
        net = checked(gross - costs, pos.trade_id, "net_pnl")
        risk = checked(pos.qty * (p.entry_reference - p.stop), pos.trade_id, "risk", positive=True)
        multiple = checked(net / risk, pos.trade_id, "r_multiple")
        self.trades.append(
            Trade(
                pos.trade_id,
                symbol,
                p.decision_session,
                pos.entry_session,
                p.entry_reference,
                pos.price,
                pos.qty,
                p.stop,
                p.target,
                day,
                price,
                reason,
                held_sessions(pos.entry_session, day, self.calendar),
                costs,
                gross,
                net,
                multiple,
                p.score,
                p.rank,
            )
        )

    def protect(self, symbol: str, day: date, *, gap: bool) -> None:
        row = self.row(symbol, day)
        if row is None:
            return
        pos = self.positions[symbol]
        fill = protective_fill(
            opened=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            stop=pos.proposal.stop,
            target=pos.proposal.target,
            gap=gap,
            slippage_bps=self.request.settings.costs.slippage_bps,
            context=pos.trade_id,
        )
        if fill is not None:
            self.close(symbol, day, *fill)

    def open_session(self, day: date) -> None:
        for symbol in sorted(self.exits):
            row = self.row(symbol, day)
            if row is not None:
                self.close(
                    symbol, day, self.market(float(row["open"]), symbol, day, sell=True), "time"
                )
        # Missing time-exit bars keep the exit pending until an executable open exists.
        self.exits.intersection_update(self.positions)
        for symbol in sorted(self.positions):
            self.protect(symbol, day, gap=True)
        for decision in sorted(self.entries, key=lambda d: d.proposal.symbol):
            p, qty = decision.proposal, decision.approved_shares
            symbol = p.symbol
            row = self.row(symbol, day)
            reason = None
            if row is None:
                reason = "entry_no_bar"
            else:
                price = self.market(float(row["open"]), symbol, day, sell=False)
                notional, cost = fill_cost(
                    price,
                    qty,
                    self.request.settings.costs,
                    sell=False,
                    context=f"{symbol}-{day:%Y%m%d}",
                )
                remaining = checked(self.cash - notional - cost, f"{symbol} {day}", "cash")
                if remaining < 0:
                    reason = "entry_insufficient_cash"
                else:
                    self.cash = remaining
                    self.positions[symbol] = _Position(p, day, price, qty, cost, price)
                    opened = float(row["open"])
                    # F-096: an open at or below the stop invalidates the entry even when the
                    # slipped fill lands above it (ADR-008, phase2-4-interfaces.md §3.2 step 3).
                    if price <= p.stop or opened <= p.stop or price >= p.target:
                        self.close(
                            symbol,
                            day,
                            self.market(float(row["open"]), symbol, day, sell=True),
                            "entry_gap_flatten",
                        )
            if reason:
                self.cancelled.append(CancelledEntry(symbol, p.decision_session, day, reason))
        self.entries = ()
        self.record_overshoots(day)
        for symbol in sorted(self.positions):
            self.protect(symbol, day, gap=False)

    def record_overshoots(self, day: date) -> None:
        notionals = {}
        for symbol, pos in self.positions.items():
            row = self.row(symbol, day)
            notionals[symbol] = checked(
                pos.qty * (float(row["open"]) if row is not None else pos.mark),
                f"{symbol} {day}",
                "open_notional",
            )
        gross = checked(sum(notionals.values()), str(day), "open_gross")
        equity = checked(self.cash + gross, str(day), "open_equity", positive=True)
        for symbol, notional in sorted(notionals.items()):
            if notional / equity > self.request.risk.max_position_pct:
                self.overshoots.append((day, symbol, "R11_position_cap"))
            if gross / equity > self.request.risk.max_gross_exposure_pct:
                self.overshoots.append((day, symbol, "R12_gross_exposure"))

    def mark(self, day: date) -> float:
        for symbol, pos in sorted(self.positions.items()):
            row = self.row(symbol, day)
            if row is None:
                self.gaps.append((day, symbol))
            else:
                pos.mark = float(row["close"])
        gross = checked(
            sum(
                checked(p.qty * p.mark, f"{s} {day}", "mark_notional")
                for s, p in self.positions.items()
            ),
            str(day),
            "gross_exposure",
        )
        equity = checked(self.cash + gross, str(day), "equity")
        self.peak = max(self.peak, equity)
        self.curve.append(EquityPoint(day, equity, self.cash, gross, len(self.positions)))
        return equity

    def decide(self, day: date, equity: float) -> None:
        for symbol, pos in self.positions.items():
            if time_exit_due(
                pos.entry_session, day, self.request.params.max_holding_sessions, self.calendar
            ):
                self.exits.add(symbol)
        now = self.calendar.session_close(day) + timedelta(
            minutes=self.request.data_settings.bar_settle_minutes
        )
        portfolio = PortfolioView(
            TradingMode.BACKTEST,
            now,
            day,
            equity,
            self.previous_equity,
            self.peak,
            self.cash,
            tuple(
                PositionView(s, p.qty, p.price, p.mark) for s, p in sorted(self.positions.items())
            ),
            (),
            self.latched,
        )
        for halt in self.halt_manager.entry_halts(portfolio):
            if not any(active.kind == halt.kind for active in self.latched):
                self.halts.append((day, halt.kind, halt.reason))
                if halt.kind == HaltKind.DRAWDOWN:
                    self.latched = (*self.latched, halt)
        symbols = {*self.request.universe.tradable_symbols, self.request.universe.regime_benchmark}
        bars = {
            s: self.data.bars.get(s, empty_bar_frame()).loc[: pd.Timestamp(day)].copy()
            for s in sorted(symbols)
        }
        quality = {
            s: assess_quality(
                frame,
                symbol=s,
                as_of=day,
                calendar=self.calendar,
                lookback_sessions=self.request.params.required_history(),
                feed=self.data.feed,
                max_missing_sessions=self.request.data_settings.max_missing_sessions_in_lookback,
            )
            for s, frame in bars.items()
        }
        first = min(frame.index[0].date() for frame in bars.values() if not frame.empty)
        evaluations = evaluate_universe(
            bars,
            self.request.universe.regime_benchmark,
            self.request.params,
            day,
            tradable=self.request.universe.tradable_symbols,
            quality=quality,
            expected_sessions=self.calendar.sessions_in_range(first, day),
        )
        proposals = build_entry_proposals(
            evaluations,
            equity=equity,
            limits=self.request.risk,
            params=self.request.params,
            decision_session=day,
            data_as_of=day,
        )
        decisions = self.gate.evaluate_entries(proposals, portfolio)
        self.decisions.extend(decisions)
        self.entries = tuple(d for d in decisions if d.approved)
        self.previous_equity = equity


def run_backtest(  # noqa: PLR0912 - validation and mandatory labels follow the contract
    request: BacktestRequest, data: BacktestData, *, gate: EntryGate, calendar: TradingCalendar
) -> BacktestResult:
    if limits_fingerprint(gate.limits) != limits_fingerprint(request.risk):
        raise ValueError("gate/request risk limits differ")
    if request.start > request.end or not all(
        calendar.is_session(d) for d in (request.start, request.end)
    ):
        raise BacktestInputError("invalid_window")
    if not request.command.strip():
        raise BacktestInputError("reproducing_command_required")
    if request.settings.earnings_policy == "require_data":
        raise BacktestInputError("verified_point_in_time_earnings_unavailable")
    validate_inputs(data, request.universe)
    if data.adjustment != request.data_settings.adjustment:
        raise BacktestInputError("configured_adjustment_mismatch")
    if data.source != DataSource.SYNTHETIC and data.feed != request.data_settings.daily_feed:
        raise BacktestInputError("configured_feed_mismatch")
    previous = calendar.previous_session(request.start)
    regime = data.bars[request.universe.regime_benchmark].loc[: pd.Timestamp(previous)]
    quality = assess_quality(
        regime,
        symbol=request.universe.regime_benchmark,
        as_of=previous,
        calendar=calendar,
        lookback_sessions=request.params.required_history(),
        feed=data.feed,
        max_missing_sessions=0,
    )
    if not quality.is_actionable:
        raise BacktestInputError(f"regime_warmup_unavailable: {quality.issues}")
    sim = _Simulation(request, data, gate, calendar)
    sim.decide(previous, sim.cash)
    for day in calendar.sessions_in_range(request.start, request.end):
        sim.open_session(day)
        equity = sim.mark(day)
        if day == request.end:
            for symbol in sorted(sim.positions):
                row = sim.row(symbol, day)
                if row is None:
                    raise BacktestInputError(f"{symbol} {day}: end_of_window_no_bar")
                sim.close(
                    symbol,
                    day,
                    sim.market(float(row["close"]), symbol, day, sell=True),
                    "end_of_window",
                )
            sim.curve[-1] = EquityPoint(day, sim.cash, sim.cash, 0.0, 0)
        else:
            sim.decide(day, equity)
    labels = [
        "SURVIVORSHIP-BIASED UNIVERSE: current hand-picked list, not point-in-time",
        "PRICE RETURN: dividends excluded",
        "EARNINGS BLACKOUT NOT APPLIED",
        "CORPORATE ACTIONS NOT CHECKED",
    ]
    if data.source == DataSource.SYNTHETIC:
        labels.insert(0, SYNTHETIC_BANNER)
    if data.source == DataSource.FIXTURE:
        labels.insert(0, f"FIXTURE DATA — OFFLINE IMPORT, PROVENANCE: {data.fixture_manifest}")
    if sim.overshoots:
        labels.append(
            f"GAP OVERSHOOT OF EXPOSURE CAPS: {len({d for d, _, _ in sim.overshoots})} sessions"
        )
    if any(t.exit_reason == "end_of_window" for t in sim.trades):
        labels.append("OPEN POSITIONS CLOSED AT WINDOW END")
    config = {
        "universe": request.universe.model_dump(mode="json"),
        "strategy": request.params.model_dump(mode="json"),
        "risk": request.risk.model_dump(mode="json"),
        "data": request.data_settings.model_dump(mode="json"),
        "BacktestSettings": request.settings.model_dump(mode="json"),
    }
    fingerprint = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    result = BacktestResult(
        1,
        new_run_id(calendar.session_close(request.end), secrets.token_hex(4)),
        request.command,
        None,
        __version__,
        fingerprint,
        data.fingerprint(),
        request.start,
        request.end,
        tuple(labels),
        tuple(sim.trades),
        tuple(sim.cancelled),
        tuple(sim.curve),
        tuple(sim.decisions),
        tuple(sim.halts),
        tuple(sim.gaps),
        tuple(sim.overshoots),
        initial_equity=request.settings.initial_equity,
        metrics_context={
            **config,
            "source": data.source.value,
            "feed": data.feed.value,
            "adjustment": data.adjustment.value,
        },
    )
    result.to_dict()  # No non-finite quantity may escape even when save() is not called.
    return result
