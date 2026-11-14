"""Setup triggers for ``intraday_v1`` (spec ``docs/specifications/intraday_v1.md``), per session.

Pure functions: one session's 5-minute bars (plus the prior sessions they need) in, raw setup
conditions per bar out. Nothing here knows about positions, money or account rules; the replay in
:mod:`semiedge.backtest.intraday` applies those. Every value at bar index ``k`` uses only bars
``0..k`` of the session and earlier sessions (spec, no-look-ahead rules), which
``tests/safety/test_intraday_backtest.py`` checks by truncation.

Bar indices are 0-based (``k = 0`` is the 09:30 bar ``b_1``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

SETUPS = ("A", "B", "C")
OR_BARS = 3
RVOL_MIN = 1.5
ORW_MIN_ATR, ORW_MAX_ATR = 0.25, 1.0
VWAP_TOUCH = 1.002
HISTORY_SESSIONS = 20
MIN_HISTORY = 15  # of the prior 20 sessions (spec clarification, #130)
DAILY_HISTORY = 15


@dataclass
class SessionBars:
    """One symbol's bars for one session on the full grid; missing bars are NaN."""

    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    v: np.ndarray

    @property
    def n(self) -> int:
        return len(self.c)

    @property
    def present(self) -> np.ndarray:
        return ~np.isnan(self.c)

    @property
    def complete(self) -> bool:
        return bool(self.present.all())


@dataclass
class SessionContext:
    """Inputs from earlier sessions; ``None`` when the spec's history requirement is not met."""

    prior_orv: list[float] = field(default_factory=list)  # ORV per prior session; NaN if incomplete
    prior_slots: list[np.ndarray] = field(default_factory=list)  # V by bar per prior session
    pdh: float | None = None
    atr_d1: float | None = None


@dataclass
class Trigger:
    setup: str
    k: int
    stop: float
    rank_volume: float  # V(b_k) / SV_k, for same-bar ordering


def vwap(bars: SessionBars) -> np.ndarray:
    """``VWAP_k`` with typical price; NaN from the first missing bar on (it cannot be repaired)."""
    tp = (bars.h + bars.lo + bars.c) / 3.0
    pv = np.cumsum(np.nan_to_num(tp * bars.v, nan=0.0))
    vol = np.cumsum(np.nan_to_num(bars.v, nan=0.0))
    out: np.ndarray = np.asarray(
        np.divide(pv, vol, out=np.full(bars.n, np.nan), where=vol > 0), dtype="float64"
    )
    gap = _first_gap(bars)
    if gap is not None:
        out[gap:] = np.nan
    return out


def _first_gap(bars: SessionBars) -> int | None:
    missing = np.flatnonzero(~bars.present)
    return int(missing[0]) if missing.size else None


def valid_until(bars: SessionBars) -> int:
    """Last possible decision bar index: every bar up to it must exist (spec ``intraday_gap``)."""
    gap = _first_gap(bars)
    return bars.n - 1 if gap is None else gap - 1


def last_decision_index(n_bars: int, own_last: int) -> int:
    """Windows end at the bar ending 30 minutes before the close (``b_72`` on a full day)."""
    return min(own_last, n_bars - 7)


def slot_volume(ctx: SessionContext, k: int) -> float | None:
    """``SV_k``: mean volume of bar ``k`` over those of the prior 20 sessions that have it.

    An early close or a vendor gap removes a session from this bar's average; at least
    ``MIN_HISTORY`` of the 20 must remain, otherwise ``SV_k`` is undefined (spec clarification,
    #130).
    """
    window = ctx.prior_slots[-HISTORY_SESSIONS:]
    if len(window) < HISTORY_SESSIONS:
        return None
    values = [float(s[k]) for s in window if k < len(s) and not math.isnan(float(s[k]))]
    if len(values) < MIN_HISTORY:
        return None
    return float(np.mean(values))


def orv_average(ctx: SessionContext) -> float | None:
    """``ORV_avg`` over those of the prior 20 sessions whose opening range is complete."""
    window = ctx.prior_orv[-HISTORY_SESSIONS:]
    if len(window) < HISTORY_SESSIONS:
        return None
    values = [v for v in window if not math.isnan(v)]
    return float(np.mean(values)) if len(values) >= MIN_HISTORY else None


def history_ok(ctx: SessionContext) -> bool:
    """Shared condition 3: 20 prior sessions exist and at least 15 of them are complete."""
    window = ctx.prior_slots[-HISTORY_SESSIONS:]
    complete = sum(1 for s in window if not np.isnan(s).any())
    return len(window) >= HISTORY_SESSIONS and complete >= MIN_HISTORY


def market_ok(smh: SessionBars, smh_vwap: np.ndarray, k: int) -> bool:
    """Shared condition 1: SMH closes above its own VWAP on bar ``k``."""
    return bool(smh.present[: k + 1].all()) and float(smh.c[k]) > float(smh_vwap[k])


def setup_a_day_eligible(bars: SessionBars, ctx: SessionContext) -> bool:
    if not bars.present[:OR_BARS].all() or ctx.atr_d1 is None:
        return False
    orv_avg = orv_average(ctx)
    if orv_avg is None or orv_avg <= 0:
        return False
    orv = float(np.sum(bars.v[:OR_BARS]))
    orw = float(np.max(bars.h[:OR_BARS]) - np.min(bars.lo[:OR_BARS]))
    return orv / orv_avg >= RVOL_MIN and ORW_MIN_ATR * ctx.atr_d1 <= orw <= ORW_MAX_ATR * ctx.atr_d1


def triggers_at(
    bars: SessionBars,
    vw: np.ndarray,
    ctx: SessionContext,
    k: int,
    *,
    a_eligible: bool,
) -> list[Trigger]:
    """Raw setup conditions that hold at bar ``k`` (no position or account state applied)."""
    if k > valid_until(bars) or not history_ok(ctx):
        return []
    if ctx.pdh is None or ctx.atr_d1 is None:
        return []
    c_k = float(bars.c[k])
    if not c_k > float(vw[k]):  # every setup needs C(b_k) > VWAP_k; NaN fails
        return []
    sv = slot_volume(ctx, k)
    rank = float(bars.v[k]) / sv if sv else 0.0
    out: list[Trigger] = []
    n = bars.n
    # Setup A: opening-range breakout, b_3..b_24.
    if a_eligible and OR_BARS - 1 <= k <= last_decision_index(n, 23):
        orh = float(np.max(bars.h[:OR_BARS]))
        if c_k > orh:
            out.append(Trigger("A", k, float(np.min(bars.lo[:OR_BARS])), rank))
    # Setup B: prior-day-high breakout, b_3..b_72.
    if 2 <= k <= last_decision_index(n, 71) and sv is not None:
        pdh = ctx.pdh
        if (
            float(bars.o[0]) <= pdh
            and c_k > pdh
            and float(bars.c[k - 1]) <= pdh
            and float(bars.v[k]) >= RVOL_MIN * sv
        ):
            out.append(Trigger("B", k, float(np.min(bars.lo[k - 2 : k + 1])), rank))
    # Setup C: VWAP pullback in a trend, b_12..b_66.
    if 11 <= k <= last_decision_index(n, 65):
        held = all(float(bars.c[j]) > float(vw[j]) for j in range(k - 6, k))
        highs = bars.h[:k]
        hod = float(np.max(highs))
        latest_high = int(np.flatnonzero(highs == hod)[-1])
        if (
            held
            and latest_high >= max(0, k - 12)
            and float(bars.lo[k]) <= float(vw[k]) * VWAP_TOUCH
            and c_k > float(bars.o[k])
        ):
            out.append(Trigger("C", k, float(bars.lo[k]), rank))
    return [t for t in out if math.isfinite(t.stop)]
