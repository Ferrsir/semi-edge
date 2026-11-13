"""Shared pure types for the risk manager, backtest and paper engine (issue #19).

Spec-verbatim: docs/specifications/phase2-4-interfaces.md §1. All dataclasses are frozen and their
constructors do **not** validate: the risk manager does, so invalid input becomes a recorded
rejection rather than an exception in the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from semiedge.safety.modes import TradingMode

__all__ = [
    "EarningsInfo",
    "EntryProposal",
    "Halt",
    "HaltKind",
    "OrderSide",
    "PendingOrderView",
    "PortfolioView",
    "PositionView",
    "RiskCheck",
    "RiskDecision",
]


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class HaltKind(StrEnum):
    MANUAL = "manual"  # owner via dashboard/CLI
    DAILY_LOSS = "daily_loss"  # auto; clears at the next decision cycle (§8)
    DRAWDOWN = "drawdown"  # auto; latched until a human clears it
    RECONCILIATION = "reconciliation"  # irreconcilable broker/local state; human clears
    BROKER = "broker"  # repeated broker failures / account blocked; human clears
    DATA = "data"  # regime benchmark stale/invalid; clears when data is fresh
    PROTECTION = "protection"  # a filled position lacked a protective stop past deadline


@dataclass(frozen=True, slots=True)
class Halt:
    kind: HaltKind
    reason: str
    since: datetime


@dataclass(frozen=True, slots=True)
class EntryProposal:
    """A ranked, eligible momentum_v1 candidate turned into a proposed long entry."""

    symbol: str
    decision_session: date  # t; bars <= t were used
    data_as_of: date  # last completed session in the inputs; must equal decision_session
    strategy: str  # "momentum_v1"
    strategy_version: str
    rank: int  # 1-based among eligible (SignalEvaluation.rank)
    score: float
    entry_reference: float  # C_t
    atr: float  # ATR_t
    stop: float  # C_t - stop_atr_multiple * ATR_t (from SignalEvaluation)
    target: float  # C_t + target_atr_multiple * ATR_t
    proposed_shares: int  # semiedge.risk.sizing.propose_position_size(...).shares
    side: OrderSide = OrderSide.BUY  # anything else is rejected (long-only)


@dataclass(frozen=True, slots=True)
class PositionView:
    symbol: str
    qty: int  # > 0 (long only); negative/zero is an invalid input
    avg_entry_price: float
    mark_price: float  # latest known price used for exposure (close t in backtest)


@dataclass(frozen=True, slots=True)
class PendingOrderView:
    symbol: str
    side: OrderSide
    remaining_qty: int  # qty - filled_qty, > 0
    valuation_price: float  # limit price if any, else the intent's entry_reference
    is_entry: bool


@dataclass(frozen=True, slots=True)
class PortfolioView:
    mode: TradingMode  # BACKTEST or PAPER; SCANNER is rejected by the risk manager
    as_of: datetime  # decision timestamp (UTC), supplied by caller; never read inside
    session: date  # decision session t
    equity: float  # equity at close t (backtest) / account equity (paper)
    start_of_day_equity: float  # equity at close t-1 (backtest) / account.last_equity (paper)
    peak_equity: float  # max equity observed up to and including as_of
    cash: float
    positions: tuple[PositionView, ...]
    pending_orders: tuple[PendingOrderView, ...]
    halts: tuple[Halt, ...]  # active halts (any kind)


@dataclass(frozen=True, slots=True)
class EarningsInfo:
    symbol: str
    next_earnings_session: date | None  # first session on/after the query date with a report
    verified: bool  # False => unknown; treated per RiskLimits.require_verified_earnings_data
    source: str  # e.g. "none", "<vendor>", "manual_yaml@2026-10-30"


@dataclass(frozen=True, slots=True)
class RiskCheck:
    check_id: str  # stable id from §2.2, e.g. "R12_gross_exposure"
    passed: bool
    observed: float | None
    limit: float | None
    detail: str


@dataclass(frozen=True, slots=True)
class RiskDecision:
    proposal: EntryProposal
    approved: bool
    approved_shares: int  # 0 if rejected; 1 <= approved_shares <= proposed_shares if approved
    reasons: tuple[str, ...]  # reason codes; empty iff approved with approved_shares == proposed
    checks: tuple[RiskCheck, ...]  # every check evaluated, in §2.2 order
    limits_fingerprint: str  # semiedge.risk.manager.limits_fingerprint(limits)
    decided_at: datetime  # == portfolio.as_of
