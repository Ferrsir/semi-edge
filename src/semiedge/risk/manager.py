"""Deterministic, independent risk manager for new long entries (issue #19, T8).

Contract: docs/specifications/phase2-4-interfaces.md §2. The manager gates **entries only**;
exits are never gated (§2.4). It is pure: no clock reads (``decided_at = portfolio.as_of``), no
randomness, no I/O. Every proposal gets one :class:`RiskDecision` recording every check R01–R17
in order. Downsizing checks (R10–R13) can only lower the size.

Fail closed: invalid input is a recorded rejection (``invalid_input``), never a default; an
exception inside a check becomes ``internal_error``; limits above the pre-approved ceilings,
any ``allow_*`` flag, or ``require_verified_earnings_data=False`` make construction raise
:class:`SafetyViolation`. Until owner issue #20 provides a verified earnings source, every new
PAPER entry is rejected by R16 (``earnings_data_unavailable``); nothing here may relax that.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from fractions import Fraction
from typing import Final, Literal, Protocol

from semiedge.config.models import RiskLimits
from semiedge.data.calendar import TradingCalendar
from semiedge.risk.sizing import propose_position_size
from semiedge.risk.types import (
    EarningsInfo,
    EntryProposal,
    Halt,
    HaltKind,
    OrderSide,
    PendingOrderView,
    PortfolioView,
    RiskCheck,
    RiskDecision,
)
from semiedge.safety.limits import RISK_LIMIT_CEILINGS
from semiedge.safety.modes import SafetyViolation, TradingMode

__all__ = [
    "CHECK_IDS",
    "EarningsCalendar",
    "RiskManager",
    "UnavailableEarningsCalendar",
    "limits_fingerprint",
]

EarningsWaiver = Literal["none", "backtest_unavailable"]
CHECK_IDS: Final = (
    "R01_mode",
    "R02_inputs",
    "R03_halts",
    "R04_long_only",
    "R05_tradable",
    "R06_fresh",
    "R07_levels",
    "R08_duplicate",
    "R09_max_positions",
    "R10_risk_per_trade",
    "R11_position_cap",
    "R12_gross_exposure",
    "R13_cash",
    "R14_daily_loss",
    "R15_drawdown",
    "R16_earnings",
    "R17_min_shares",
)
_SIZING_CHECKS: Final = ("R10_risk_per_trade", "R11_position_cap", "R12_gross_exposure", "R13_cash")
_CEILING_FIELDS: Final = (
    "max_risk_per_trade_pct",
    "max_position_pct",
    "max_gross_exposure_pct",
    "max_positions",
    "daily_loss_halt_pct",
    "drawdown_halt_pct",
)
_ALLOW_FLAGS: Final = (
    "allow_leverage",
    "allow_short_selling",
    "allow_options",
    "allow_extended_hours",
)


class EarningsCalendar(Protocol):
    def next_earnings(self, symbol: str, on_or_after: date) -> EarningsInfo: ...


class UnavailableEarningsCalendar:
    """Returns ``verified=False`` for every symbol. The only implementation until #20 (H1)."""

    def next_earnings(self, symbol: str, on_or_after: date) -> EarningsInfo:
        return EarningsInfo(symbol, None, verified=False, source="none")


def limits_fingerprint(limits: RiskLimits) -> str:
    """The ONE definition (§2.1): sha256 of the sorted, compact JSON dump of the limits."""
    payload = json.dumps(limits.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ helpers


def _finite(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _whole(value: object, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _floor_div(numerator: float, denominator: float) -> int:
    """``floor(numerator / denominator)`` in exact arithmetic, never below 0."""
    if denominator <= 0:
        return 0
    return max(0, math.floor(Fraction(numerator) / Fraction(denominator)))


def _check(check_id: str, passed: bool, detail: str, observed: float | None = None,
           limit: float | None = None) -> RiskCheck:  # fmt: skip
    return RiskCheck(check_id, passed, observed, limit, detail)


def _not_evaluated(check_id: str, why: str) -> RiskCheck:
    return RiskCheck(check_id, False, None, None, f"not evaluated: {why}")


def _input_problems(proposal: EntryProposal, portfolio: PortfolioView) -> list[str]:
    problems: list[str] = []
    floats = {
        "score": proposal.score,
        "entry_reference": proposal.entry_reference,
        "atr": proposal.atr,
        "stop": proposal.stop,
        "target": proposal.target,
        "equity": portfolio.equity,
        "start_of_day_equity": portfolio.start_of_day_equity,
        "peak_equity": portfolio.peak_equity,
        "cash": portfolio.cash,
    }
    problems += [f"{name} is not finite" for name, value in floats.items() if not _finite(value)]
    if not _whole(proposal.proposed_shares, 0):
        problems.append("proposed_shares must be an int >= 0")
    if not _whole(proposal.rank, 1):
        problems.append("rank must be an int >= 1")
    if not problems:
        for name in ("equity", "start_of_day_equity", "peak_equity"):
            if not floats[name] > 0:
                problems.append(f"{name} must be positive")
        if portfolio.peak_equity < portfolio.equity:
            problems.append("peak_equity must be >= equity")
        if portfolio.cash < 0:
            problems.append("cash must be >= 0")
    for position in portfolio.positions:
        prices = (position.mark_price, position.avg_entry_price)  # F-061: both must be valid
        if not _whole(position.qty, 1) or not all(_finite(v) and v > 0 for v in prices):
            problems.append(
                f"position {position.symbol}: qty must be > 0 and mark/entry prices finite > 0"
            )
    for order in portfolio.pending_orders:
        if not _whole(order.remaining_qty, 1) or not (
            _finite(order.valuation_price) and order.valuation_price > 0
        ):
            problems.append(f"pending {order.symbol}: qty must be > 0 and price finite > 0")
    return problems


@dataclass
class _Batch:
    """The working copy of the portfolio for one ``evaluate_entries`` call."""

    portfolio: PortfolioView
    pending: list[PendingOrderView] = field(default_factory=list)
    seen_symbols: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.pending = list(self.portfolio.pending_orders)

    def buy_notional(self) -> float:
        return sum(
            float(o.remaining_qty) * o.valuation_price
            for o in self.pending
            if o.side == OrderSide.BUY
        )

    def exposure(self) -> float:
        held = sum(float(p.qty) * p.mark_price for p in self.portfolio.positions)
        return held + self.buy_notional()


# ------------------------------------------------------------------ manager


class RiskManager:
    def __init__(
        self,
        limits: RiskLimits,
        *,
        calendar: TradingCalendar,
        earnings: EarningsCalendar,
        tradable_symbols: frozenset[str],
        earnings_waiver: EarningsWaiver = "none",
    ) -> None:
        if not isinstance(limits, RiskLimits):
            raise SafetyViolation("RiskManager requires a validated RiskLimits")
        if not _whole(limits.max_positions, 1):  # F-062: model_construct can bypass the int type
            raise SafetyViolation("risk limit max_positions must be a positive int")
        for name in _CEILING_FIELDS:
            value, ceiling = getattr(limits, name), getattr(RISK_LIMIT_CEILINGS, name)
            if not _finite(value) or not value > 0 or value > ceiling:
                raise SafetyViolation(f"risk limit {name} is outside (0, {ceiling}]")
        for name in _ALLOW_FLAGS:
            if getattr(limits, name) is not False:
                raise SafetyViolation(f"{name} must be False")
        if limits.require_verified_earnings_data is not True:
            raise SafetyViolation("require_verified_earnings_data must be True (R16, fail closed)")
        if not _whole(limits.earnings_blackout_sessions, 0):
            raise SafetyViolation("earnings_blackout_sessions must be an int >= 0")
        if earnings_waiver not in ("none", "backtest_unavailable"):
            raise SafetyViolation("earnings_waiver must be 'none' or 'backtest_unavailable'")
        if not isinstance(tradable_symbols, frozenset):
            raise SafetyViolation("tradable_symbols must be a frozenset")
        self._limits = limits
        self._calendar = calendar
        self._earnings = earnings
        self._tradable = tradable_symbols
        self._waiver: EarningsWaiver = earnings_waiver
        self._fingerprint = limits_fingerprint(limits)

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    # -------------------------------------------------------------- public API

    def evaluate_entries(
        self, proposals: Sequence[EntryProposal], portfolio: PortfolioView
    ) -> tuple[RiskDecision, ...]:
        """Deterministic. One decision per proposal, in (rank, symbol) processing order."""
        batch = _Batch(portfolio)
        ordered = sorted(proposals, key=lambda p: (_sort_rank(p.rank), str(p.symbol)))
        decisions: list[RiskDecision] = []
        for proposal in ordered:
            try:
                decision = self._decide(proposal, batch)
            except Exception as exc:  # fail closed per proposal
                decision = RiskDecision(
                    proposal=proposal,
                    approved=False,
                    approved_shares=0,
                    reasons=("internal_error",),
                    checks=(_check("internal_error", False, type(exc).__name__),),
                    limits_fingerprint=self._fingerprint,
                    decided_at=portfolio.as_of,
                )
            batch.seen_symbols.add(proposal.symbol)
            if decision.approved:
                batch.pending.append(
                    PendingOrderView(
                        proposal.symbol,
                        OrderSide.BUY,
                        decision.approved_shares,
                        proposal.entry_reference,
                        is_entry=True,
                    )
                )
            decisions.append(decision)
        return tuple(decisions)

    def entry_halts(self, portfolio: PortfolioView) -> tuple[Halt, ...]:
        """Halts implied by the portfolio itself (DAILY_LOSS, DRAWDOWN) plus ``portfolio.halts``."""
        halts = list(portfolio.halts)
        equity_ok = _finite(portfolio.equity)
        sod, peak = portfolio.start_of_day_equity, portfolio.peak_equity
        if equity_ok and _finite(sod) and sod > 0 and not self._daily_ok(portfolio):
            halts.append(Halt(HaltKind.DAILY_LOSS, "daily_loss_halt", portfolio.as_of))
        if equity_ok and _finite(peak) and peak > 0 and not self._drawdown_ok(portfolio):
            halts.append(Halt(HaltKind.DRAWDOWN, "drawdown_halt", portfolio.as_of))
        return tuple(halts)

    # -------------------------------------------------------------- one proposal

    def _decide(self, proposal: EntryProposal, batch: _Batch) -> RiskDecision:
        portfolio = batch.portfolio
        checks: list[RiskCheck] = []
        rejects: list[str] = []

        def record(check: RiskCheck, reason: str | None = None) -> None:
            checks.append(check)
            if not check.passed and reason is not None:
                rejects.append(reason)

        record(*self._r01_mode(portfolio))
        problems = _input_problems(proposal, portfolio)
        record(
            _check("R02_inputs", not problems, "; ".join(problems) or "all inputs valid"),
            "invalid_input",
        )
        if problems:
            checks += [_not_evaluated(cid, "invalid_input") for cid in CHECK_IDS[2:]]
            return self._result(proposal, portfolio, checks, rejects, approved_shares=0, sizes=[])

        for step in (self._r03_halts, self._r04_long_only, self._r05_tradable, self._r06_fresh,
                     self._r07_levels):  # fmt: skip
            record(*step(proposal, portfolio))
        record(*self._r08_duplicate(proposal, batch))
        record(*self._r09_max_positions(batch))
        levels_ok = checks[6].passed
        sizes = self._sizing_checks(proposal, batch, levels_ok)
        checks += [check for check, _ in sizes]
        record(*self._r14_daily_loss(portfolio))
        record(*self._r15_drawdown(portfolio))
        record(*self._r16_earnings(proposal, portfolio))
        allowed = [allow for _, allow in sizes]
        approved_shares = min([proposal.proposed_shares, *allowed]) if levels_ok else 0
        record(
            _check(
                "R17_min_shares",
                approved_shares >= 1,
                f"min(proposed {proposal.proposed_shares}, R10..R13 {allowed}) = {approved_shares}",
                float(approved_shares),
                1.0,
            ),
            "size_below_one_share",
        )
        return self._result(
            proposal, portfolio, checks, rejects, approved_shares=approved_shares, sizes=sizes
        )

    def _result(
        self,
        proposal: EntryProposal,
        portfolio: PortfolioView,
        checks: list[RiskCheck],
        rejects: list[str],
        *,
        approved_shares: int,
        sizes: list[tuple[RiskCheck, int]],
    ) -> RiskDecision:
        approved = not rejects
        reasons = list(dict.fromkeys(rejects))
        if approved and approved_shares < proposal.proposed_shares:
            reasons += [
                f"downsized:{check.check_id}" for check, allow in sizes if allow == approved_shares
            ]
        return RiskDecision(
            proposal=proposal,
            approved=approved,
            approved_shares=approved_shares if approved else 0,
            reasons=tuple(reasons),
            checks=tuple(checks),
            limits_fingerprint=self._fingerprint,
            decided_at=portfolio.as_of,
        )

    # -------------------------------------------------------------- reject checks

    def _r01_mode(self, portfolio: PortfolioView) -> tuple[RiskCheck, str]:
        mode = portfolio.mode
        ok = mode in (TradingMode.BACKTEST, TradingMode.PAPER)
        if self._waiver != "none" and mode != TradingMode.BACKTEST:
            ok = False
        return _check("R01_mode", ok, f"mode {mode!s}, earnings_waiver {self._waiver}"), (
            "invalid_mode"
        )

    def _r03_halts(
        self, proposal: EntryProposal, portfolio: PortfolioView
    ) -> tuple[RiskCheck, str]:
        kinds = sorted({str(h.kind) for h in portfolio.halts})
        reason = f"halt_active:{kinds[0]}" if kinds else "halt_active"
        detail = f"active halts: {', '.join(kinds)}" if kinds else "no active halts"
        return _check("R03_halts", not kinds, detail, float(len(kinds)), 0.0), reason

    def _r04_long_only(
        self, proposal: EntryProposal, portfolio: PortfolioView
    ) -> tuple[RiskCheck, str]:
        ok = proposal.side == OrderSide.BUY
        return _check("R04_long_only", ok, f"side {proposal.side!s}"), "not_long"

    def _r05_tradable(
        self, proposal: EntryProposal, portfolio: PortfolioView
    ) -> tuple[RiskCheck, str]:
        ok = proposal.symbol in self._tradable
        return _check("R05_tradable", ok, f"{proposal.symbol} tradable: {ok}"), "not_tradable"

    def _r06_fresh(
        self, proposal: EntryProposal, portfolio: PortfolioView
    ) -> tuple[RiskCheck, str]:
        ok = proposal.data_as_of == proposal.decision_session == portfolio.session
        detail = (
            f"data_as_of {proposal.data_as_of}, decision {proposal.decision_session}, "
            f"portfolio session {portfolio.session}"
        )
        return _check("R06_fresh", ok, detail), "stale_data"

    def _r07_levels(
        self, proposal: EntryProposal, portfolio: PortfolioView
    ) -> tuple[RiskCheck, str]:
        p = proposal
        ok = 0 < p.stop < p.entry_reference < p.target and p.atr > 0
        detail = f"stop {p.stop}, entry {p.entry_reference}, target {p.target}, atr {p.atr}"
        return _check("R07_levels", ok, detail), "invalid_levels"

    def _r08_duplicate(self, proposal: EntryProposal, batch: _Batch) -> tuple[RiskCheck, str]:
        symbol = proposal.symbol
        held = any(p.symbol == symbol for p in batch.portfolio.positions)
        pending = any(o.symbol == symbol for o in batch.pending)
        earlier = symbol in batch.seen_symbols
        ok = not (held or pending or earlier)
        detail = f"position {held}, pending order {pending}, earlier in batch {earlier}"
        return _check("R08_duplicate", ok, detail), "duplicate_or_conflict"

    def _r09_max_positions(self, batch: _Batch) -> tuple[RiskCheck, str]:
        count = len(batch.portfolio.positions) + sum(1 for o in batch.pending if o.is_entry)
        limit = self._limits.max_positions
        ok = count < limit
        detail = f"{count} positions + pending entries, limit {limit}"
        return _check("R09_max_positions", ok, detail, float(count), float(limit)), (
            "max_positions"
        )

    def _daily_ok(self, portfolio: PortfolioView) -> bool:
        change = portfolio.equity / portfolio.start_of_day_equity - 1
        return change > -self._limits.daily_loss_halt_pct

    def _drawdown_ok(self, portfolio: PortfolioView) -> bool:
        change = portfolio.equity / portfolio.peak_equity - 1
        return change > -self._limits.drawdown_halt_pct

    def _r14_daily_loss(self, portfolio: PortfolioView) -> tuple[RiskCheck, str]:
        change = portfolio.equity / portfolio.start_of_day_equity - 1
        limit = -self._limits.daily_loss_halt_pct
        ok = self._daily_ok(portfolio)
        return _check("R14_daily_loss", ok, f"day change {change:.6f} > {limit}", change, limit), (
            "daily_loss_halt"
        )

    def _r15_drawdown(self, portfolio: PortfolioView) -> tuple[RiskCheck, str]:
        change = portfolio.equity / portfolio.peak_equity - 1
        limit = -self._limits.drawdown_halt_pct
        ok = self._drawdown_ok(portfolio)
        return _check("R15_drawdown", ok, f"drawdown {change:.6f} > {limit}", change, limit), (
            "drawdown_halt"
        )

    def _r16_earnings(
        self, proposal: EntryProposal, portfolio: PortfolioView
    ) -> tuple[RiskCheck, str]:
        info = self._earnings.next_earnings(proposal.symbol, proposal.decision_session)
        blackout = self._limits.earnings_blackout_sessions
        if not (isinstance(info, EarningsInfo) and info.symbol == proposal.symbol):
            return _check("R16_earnings", False, "earnings source returned no record"), (
                "earnings_data_unavailable"
            )
        if info.verified is not True:
            if self._waiver == "backtest_unavailable" and portfolio.mode == TradingMode.BACKTEST:
                return _check("R16_earnings", True, "waived: no point-in-time earnings data"), (
                    "earnings_data_unavailable"
                )
            detail = f"earnings data unverified (source {info.source})"
            return _check("R16_earnings", False, detail), "earnings_data_unavailable"
        upcoming = info.next_earnings_session
        if upcoming is None:
            return _check("R16_earnings", True, "no upcoming earnings"), "earnings_blackout"
        if upcoming <= proposal.decision_session:
            k = 0
        else:
            k = len(
                self._calendar.sessions_in_range(
                    proposal.decision_session + timedelta(days=1), upcoming
                )
            )
        ok = k > blackout
        detail = f"{k} sessions to earnings on {upcoming}, blackout {blackout}"
        return _check("R16_earnings", ok, detail, float(k), float(blackout)), "earnings_blackout"

    # -------------------------------------------------------------- downsizing checks

    def _sizing_checks(
        self, proposal: EntryProposal, batch: _Batch, levels_ok: bool
    ) -> list[tuple[RiskCheck, int]]:
        if not levels_ok:
            return [(_not_evaluated(cid, "invalid_levels"), 0) for cid in _SIZING_CHECKS]
        portfolio, limits = batch.portfolio, self._limits
        sized = propose_position_size(
            portfolio.equity,
            proposal.entry_reference,
            proposal.stop,
            max_risk_per_trade_pct=limits.max_risk_per_trade_pct,
            max_position_pct=limits.max_position_pct,
        )
        by_risk, by_alloc = sized.shares_by_risk, sized.shares_by_allocation
        note = f"; {sized.reason}" if sized.shares == 0 and sized.reason else ""
        exposure = batch.exposure()
        gross_cap = portfolio.equity * limits.max_gross_exposure_pct
        by_gross = _floor_div(gross_cap - exposure, proposal.entry_reference)
        free_cash = portfolio.cash - batch.buy_notional()
        by_cash = _floor_div(free_cash, proposal.entry_reference)
        wanted = proposal.proposed_shares
        rows: list[tuple[str, int, str]] = [
            ("R10_risk_per_trade", by_risk, f"risk budget allows {by_risk} shares{note}"),
            ("R11_position_cap", by_alloc, f"allocation budget allows {by_alloc} shares{note}"),
            ("R12_gross_exposure", by_gross,
             f"exposure {exposure:.2f} of cap {gross_cap:.2f} allows {by_gross} shares"),
            ("R13_cash", by_cash, f"free cash {free_cash:.2f} allows {by_cash} shares"),
        ]  # fmt: skip
        return [
            (_check(cid, allow >= wanted, detail, float(allow), float(wanted)), allow)
            for cid, allow, detail in rows
        ]


def _sort_rank(rank: object) -> int:
    return rank if type(rank) is int else 2**63
