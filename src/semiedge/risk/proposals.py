"""The shared scanner-to-entry boundary for backtest and paper execution."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date

from semiedge.config.models import MomentumV1Params, RiskLimits
from semiedge.risk.sizing import propose_position_size
from semiedge.risk.types import EntryProposal
from semiedge.strategy.base import SignalEvaluation
from semiedge.strategy.exits import exit_levels


def build_entry_proposals(
    evaluations: Sequence[SignalEvaluation],
    *,
    equity: float,
    limits: RiskLimits,
    params: MomentumV1Params,
    decision_session: date,
    data_as_of: date,
) -> tuple[EntryProposal, ...]:
    proposals = []
    for ev in evaluations:
        if not ev.eligible:
            continue
        if (
            ev.session != decision_session
            or ev.score is None
            or ev.close is None
            or ev.atr is None
            or ev.proposed_stop is None
            or ev.proposed_target is None
            or ev.rank is None
        ):
            raise ValueError(f"{ev.symbol}: incomplete eligible evaluation")
        levels = exit_levels(ev.close, ev.atr, params)
        if not (
            math.isclose(levels.stop, ev.proposed_stop, rel_tol=1e-9, abs_tol=0)
            and math.isclose(levels.target, ev.proposed_target, rel_tol=1e-9, abs_tol=0)
        ):
            raise ValueError(f"{ev.symbol}: scanner/exit level divergence")
        size = propose_position_size(
            equity,
            ev.close,
            levels.stop,
            max_risk_per_trade_pct=limits.max_risk_per_trade_pct,
            max_position_pct=limits.max_position_pct,
        )
        proposals.append(
            EntryProposal(
                ev.symbol,
                decision_session,
                data_as_of,
                ev.strategy,
                ev.strategy_version,
                ev.rank,
                ev.score,
                ev.close,
                ev.atr,
                levels.stop,
                levels.target,
                size.shares,
            )
        )
    return tuple(sorted(proposals, key=lambda p: (p.rank, p.symbol)))
