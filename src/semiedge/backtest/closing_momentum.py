"""Backtest of ``closing_momentum_v2`` (spec ``docs/specifications/closing_momentum_v2.md``, #169).

Same-day, long-only: when a stock and SMH are both up from the previous close to 10:00, buy the
stock at 15:30 and hold to the 15:55 flatten, with a stop 0.25 daily ATR below the 15:30 decision
close. The study, replay, accounts and gates come from :mod:`semiedge.backtest.intraday_dip`.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd

from semiedge.backtest import intraday_dip as idip
from semiedge.backtest.gap_reversal import _daily_prior, _prior
from semiedge.backtest.intraday import _grid
from semiedge.backtest.intraday_dip import DaySession, Signal, _without_empty_bars
from semiedge.backtest.intraday_signals import SessionBars, valid_until
from semiedge.data.calendar import TradingCalendar
from semiedge.data.intraday import session_bar_starts

VARIANTS = ("M1", "M2")
DEVELOPMENT = (date(2026, 1, 2), date(2026, 6, 30))
HOLDOUT = (date(2026, 7, 1), date(2026, 10, 23))
SEEN_WINDOW = (date(2026, 10, 26), date(2026, 11, 13))
FULL_SESSION_BARS = 78
FIRST_HALF_HOUR_BAR = 5  # 09:55-10:00 close
DECISION_BAR = 71  # 15:25-15:30 close; entry at the 15:30 open
STOP_ATR = 0.25
SELECTION_MIN_TRADES = 150
HOLDOUT_MIN_TRADES = 80
NO_TARGET = math.inf

ACCOUNT_MODELS = idip.ACCOUNT_MODELS
SLIPPAGES = (5.0, 10.0, 20.0)
SELECTION_SLIPPAGE = 10.0
signal_study = idip.signal_study
study_stats = idip.study_stats
replay = idip.replay
smh_change_pct = idip.smh_change_pct


def session_signals(
    b: SessionBars,
    smh: SessionBars,
    *,
    pc: float | None,
    pc_smh: float | None,
    atr_d1: float | None,
) -> dict[str, dict[int, list[Signal]]]:
    out: dict[str, dict[int, list[Signal]]] = {"M1": {}, "M2": {}, "baseline": {}}
    if b.n != FULL_SESSION_BARS or pc is None or pc_smh is None or atr_d1 is None:
        return out
    if valid_until(b) < DECISION_BAR or not bool(smh.present[: FIRST_HALF_HOUR_BAR + 1].all()):
        return out
    c71 = float(b.c[DECISION_BAR])
    stop = c71 - STOP_ATR * atr_d1
    r0 = c71 - stop
    if not (math.isfinite(stop) and stop > 0 and r0 >= idip.MIN_RISK_FRACTION * c71):
        return out
    r = float(b.c[FIRST_HALF_HOUR_BAR]) / pc - 1
    r_smh = float(smh.c[FIRST_HALF_HOUR_BAR]) / pc_smh - 1

    def sig(score: float) -> list[Signal]:
        return [Signal("", DECISION_BAR, stop, NO_TARGET, r0, c71, score)]

    out["baseline"][DECISION_BAR] = sig(-r)
    if r > 0 and r_smh > 0:
        out["M1"][DECISION_BAR] = sig(-r)
        if r > r_smh:
            out["M2"][DECISION_BAR] = sig(-(r - r_smh))
    return out


def prepare_sessions(
    intraday: dict[str, pd.DataFrame],
    daily: dict[str, pd.DataFrame],
    *,
    tradable: tuple[str, ...],
    benchmark: str,
    start: date,
    end: date,
    calendar: TradingCalendar,
) -> list[DaySession]:
    if start > end:
        raise ValueError("start must not exceed end")
    ctx = {s: _daily_prior(daily.get(s)) for s in (*tradable, benchmark)}
    out: list[DaySession] = []
    for session in calendar.sessions_in_range(start, end):
        starts = session_bar_starts(calendar, session)
        smh = _without_empty_bars(_grid(intraday.get(benchmark), starts))
        pc_smh, _ = _prior(ctx[benchmark], session)
        bars: dict[str, SessionBars] = {}
        sigs: dict[str, dict[int, list[Signal]]] = {"M1": {}, "M2": {}, "baseline": {}}
        for sym in tradable:
            b = _without_empty_bars(_grid(intraday.get(sym), starts))
            bars[sym] = b
            pc, a = _prior(ctx[sym], session)
            found = session_signals(b, smh, pc=pc, pc_smh=pc_smh, atr_d1=a)
            for variant, by_k in found.items():
                for k, items in by_k.items():
                    sigs[variant].setdefault(k, []).extend(
                        Signal(sym, s.k, s.stop, s.target, s.r0, s.close, s.score) for s in items
                    )
        for by_k in sigs.values():
            for items in by_k.values():
                items.sort(key=lambda s: (s.score, s.symbol))
        out.append(DaySession(session, len(starts), bars, sigs))
    return out


def select_variant(study: dict[str, dict[str, Any]]) -> tuple[str | None, list[str]]:
    base = study["baseline"].get("mean_r", -math.inf)
    notes, qualified = [], []
    for v in VARIANTS:
        fails = idip._gate_failures(study[v], base, SELECTION_MIN_TRADES)
        notes.append(f"{v}: {'qualifies' if not fails else '; '.join(fails)}")
        if not fails:
            qualified.append((study[v]["mean_r"], v))
    return (max(qualified)[1] if qualified else None), notes


def holdout_pass(study: dict[str, dict[str, Any]], variant: str, portfolio_net: float) -> list[str]:
    base = study["baseline"].get("mean_r", -math.inf)
    fails = idip._gate_failures(study[variant], base, HOLDOUT_MIN_TRADES)
    return [*fails, *(["$5,000 unrestricted run not positive"] if not portfolio_net > 0 else [])]
