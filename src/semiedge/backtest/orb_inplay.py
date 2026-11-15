"""Backtest of ``orb_inplay_v1`` (spec ``docs/specifications/orb_inplay_v1.md``, #145).

The 5-minute opening-range breakout in stocks in play (Zarattini, Barbon and Aziz, 2024),
long-only: a green first candle on unusually heavy first-bar volume is bought at the 09:35 open
and held to the stop or the 15:55 flatten. The study, replay, accounts and gates come from
:mod:`semiedge.backtest.intraday_dip`; this module only produces the signals.
"""

from __future__ import annotations

import math
from collections import deque
from datetime import date
from typing import Any

import pandas as pd

from semiedge.backtest import intraday_dip as idip
from semiedge.backtest.gap_reversal import _daily_prior, _prior
from semiedge.backtest.intraday import _grid
from semiedge.backtest.intraday_dip import DaySession, Signal, _without_empty_bars
from semiedge.backtest.intraday_signals import SessionBars
from semiedge.data.calendar import TradingCalendar
from semiedge.data.intraday import session_bar_starts

VARIANTS = ("O1", "O2")
DEVELOPMENT = (date(2026, 1, 2), date(2026, 6, 30))
HOLDOUT = (date(2026, 7, 1), date(2026, 10, 23))
SEEN_WINDOW = (date(2026, 10, 26), date(2026, 11, 13))
RVOL_MIN = 1.0
RVOL_SESSIONS = 14
RVOL_MIN_HISTORY = 10
STOP_ATR = 0.10
SELECTION_MIN_TRADES = 60
HOLDOUT_MIN_TRADES = 40
NO_TARGET = math.inf  # held to the stop or the flatten

ACCOUNT_MODELS = idip.ACCOUNT_MODELS
SLIPPAGES = (5.0, 10.0, 20.0)
SELECTION_SLIPPAGE = 10.0
signal_study = idip.signal_study
study_stats = idip.study_stats
replay = idip.replay
smh_change_pct = idip.smh_change_pct


def rvol(v0: float, history: list[float]) -> float | None:
    """First-bar volume over its mean in the prior sessions that have bar 0 (10 of 14 needed)."""
    window = [h for h in history[-RVOL_SESSIONS:] if math.isfinite(h)]
    if len(window) < RVOL_MIN_HISTORY or not math.isfinite(v0):
        return None
    mean = sum(window) / len(window)
    return v0 / mean if mean > 0 else None


def session_signals(
    b: SessionBars, history: list[float], atr_d1: float | None
) -> dict[str, dict[int, list[Signal]]]:
    out: dict[str, dict[int, list[Signal]]] = {"O1": {}, "O2": {}, "baseline": {}}
    if b.n == 0 or atr_d1 is None or not math.isfinite(float(b.c[0])):
        return out
    o0, c0, l0, v0 = float(b.o[0]), float(b.c[0]), float(b.lo[0]), float(b.v[0])
    if not c0 > o0:
        return out
    r = rvol(v0, history)
    if r is None:
        return out

    def make(stop: float, score: float) -> list[Signal]:
        r0 = c0 - stop
        if r0 < idip.MIN_RISK_FRACTION * c0:
            return []
        return [Signal("", 0, stop, NO_TARGET, r0, c0, score)]

    paper_stop = c0 - STOP_ATR * atr_d1
    if base := make(paper_stop, -r):
        out["baseline"][0] = base
    if r >= RVOL_MIN:
        if o1 := make(paper_stop, -r):
            out["O1"][0] = o1
        if o2 := make(l0, -r):
            out["O2"][0] = o2
    return out


def prepare_sessions(
    intraday: dict[str, pd.DataFrame],
    daily: dict[str, pd.DataFrame],
    *,
    tradable: tuple[str, ...],
    benchmark: str,  # same call shape as intraday_dip.prepare_sessions; unused here
    start: date,
    end: date,
    calendar: TradingCalendar,
) -> list[DaySession]:
    if start > end:
        raise ValueError("start must not exceed end")
    window = calendar.sessions_in_range(start, end)
    if not window:
        return []
    warmup = calendar.sessions_window(calendar.previous_session(window[0]), RVOL_SESSIONS + 6)
    ctx = {s: _daily_prior(daily.get(s)) for s in tradable}
    history: dict[str, deque[float]] = {s: deque(maxlen=RVOL_SESSIONS) for s in tradable}
    test = set(window)
    out: list[DaySession] = []
    for session in [*warmup, *window]:
        starts = session_bar_starts(calendar, session)
        bars: dict[str, SessionBars] = {}
        sigs: dict[str, dict[int, list[Signal]]] = {"O1": {}, "O2": {}, "baseline": {}}
        for sym in tradable:
            b = _without_empty_bars(_grid(intraday.get(sym), starts))
            bars[sym] = b
            if session in test:
                _, a = _prior(ctx[sym], session)
                for variant, by_k in session_signals(b, list(history[sym]), a).items():
                    for k, found in by_k.items():
                        sigs[variant].setdefault(k, []).extend(
                            Signal(sym, s.k, s.stop, s.target, s.r0, s.close, s.score)
                            for s in found
                        )
            history[sym].append(float(b.v[0]) if b.n else math.nan)
        if session in test:
            for by_k in sigs.values():
                for found in by_k.values():
                    found.sort(key=lambda s: (s.score, s.symbol))
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
