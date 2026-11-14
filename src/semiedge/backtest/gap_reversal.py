"""Backtest of ``gap_reversal_v1`` (spec ``docs/specifications/gap_reversal_v1.md``, #141).

Same-day: a semi that opens at least one daily ATR below the previous close is bought after it
reclaims VWAP (G1) or the 15-minute high (G2), targeting half the gap, with the day's low as
the stop. The signal study, the replay, the account models and the gates come from
:mod:`semiedge.backtest.intraday_dip`; only the signals differ.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from semiedge.backtest import intraday_dip as idip
from semiedge.backtest.intraday import _grid
from semiedge.backtest.intraday_dip import DaySession, Signal, _without_empty_bars
from semiedge.backtest.intraday_signals import SessionBars, valid_until, vwap
from semiedge.data.calendar import TradingCalendar
from semiedge.data.intraday import session_bar_starts
from semiedge.indicators.core import atr

VARIANTS = ("G1", "G2")
DEVELOPMENT = (date(2024, 12, 16), date(2025, 12, 31))
HOLDOUT = (date(2026, 1, 2), date(2026, 10, 23))
SEEN_WINDOW = (date(2026, 10, 26), date(2026, 11, 13))  # inspected for the idea; never a test
GAP_ATR = 1.0
G1_LAST = 11
G2_FIRST, G2_LAST = 3, 23
OR_BARS = 3
SELECTION_MIN_TRADES = 40
HOLDOUT_MIN_TRADES = 25

# shared with intraday_dip_v1
ACCOUNT_MODELS = idip.ACCOUNT_MODELS
SLIPPAGES = idip.SLIPPAGES
SELECTION_SLIPPAGE = idip.SELECTION_SLIPPAGE
signal_study = idip.signal_study
study_stats = idip.study_stats
replay = idip.replay
smh_change_pct = idip.smh_change_pct


def _daily_prior(daily: pd.DataFrame | None) -> pd.DataFrame | None:
    """Close and Wilder ATR(14) per daily session, looked up by the next session."""
    if daily is None or daily.empty:
        return None
    return pd.DataFrame(
        {
            "close": daily["close"].astype(float),
            "atr": atr(
                daily["high"].astype(float),
                daily["low"].astype(float),
                daily["close"].astype(float),
            ),
        },
        index=pd.DatetimeIndex(daily.index),
    )


def _prior(ctx: pd.DataFrame | None, session: date) -> tuple[float | None, float | None]:
    if ctx is None:
        return None, None
    earlier = ctx.loc[ctx.index < pd.Timestamp(session)]
    if earlier.empty:
        return None, None
    pc, a = float(earlier["close"].iloc[-1]), float(earlier["atr"].iloc[-1])
    return (pc if math.isfinite(pc) else None), (a if math.isfinite(a) and a > 0 else None)


def session_signals(
    b: SessionBars, pc: float | None, atr_d1: float | None
) -> dict[str, dict[int, list[Signal]]]:
    out: dict[str, dict[int, list[Signal]]] = {"G1": {}, "G2": {}, "baseline": {}}
    if pc is None or atr_d1 is None or b.n == 0 or math.isnan(float(b.o[0])):
        return out
    o0 = float(b.o[0])
    if not o0 <= pc - GAP_ATR * atr_d1:
        return out
    score = -(pc - o0) / atr_d1  # the largest gap ranks first
    target = o0 + 0.5 * (pc - o0)
    vw = vwap(b)
    last = min(valid_until(b), b.n - 7)
    lod = np.minimum.accumulate(np.where(np.isnan(b.lo), np.inf, b.lo))

    def make(k: int, stop: float) -> Signal | None:
        c = float(b.c[k])
        r0 = c - stop
        if c >= target or r0 < idip.MIN_RISK_FRACTION * c:
            return None
        return Signal("", k, stop, target, r0, c, score)

    if last >= 0:
        s = make(0, float(b.lo[0]))
        if s is not None:
            out["baseline"][0] = [s]
    for k in range(1, min(G1_LAST, last) + 1):
        if float(b.c[k]) > float(vw[k]):
            s = make(k, float(lod[k]))
            if s is not None:
                out["G1"][k] = [s]
            break  # the first qualifying bar only
    if last >= OR_BARS - 1:
        orh = float(np.max(b.h[:OR_BARS]))
        for k in range(G2_FIRST, min(G2_LAST, last) + 1):
            if float(b.c[k]) > orh:
                s = make(k, float(lod[k]))
                if s is not None:
                    out["G2"][k] = [s]
                break
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
    ctx = {s: _daily_prior(daily.get(s)) for s in tradable}
    out: list[DaySession] = []
    for session in calendar.sessions_in_range(start, end):
        starts = session_bar_starts(calendar, session)
        bars: dict[str, SessionBars] = {}
        sigs: dict[str, dict[int, list[Signal]]] = {"G1": {}, "G2": {}, "baseline": {}}
        for sym in tradable:
            b = _without_empty_bars(_grid(intraday.get(sym), starts))
            bars[sym] = b
            pc, a = _prior(ctx[sym], session)
            for variant, by_k in session_signals(b, pc, a).items():
                for k, found in by_k.items():
                    sigs[variant].setdefault(k, []).extend(
                        Signal(sym, s.k, s.stop, s.target, s.r0, s.close, s.score) for s in found
                    )
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
