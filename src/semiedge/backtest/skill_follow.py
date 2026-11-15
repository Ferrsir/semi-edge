"""Screen of ``skill_follow_v1`` (spec ``docs/specifications/skill_follow_v1.md``, #173).

Nineteen pre-registered candidates on two universes: the chart rules documented by the
``day-trader-skill`` pack (re-implemented here; none of its code or data is used) and
sector-follow rules. Each candidate is scored by a signal study in planned R.

* A and B (5-minute, same day) build :class:`~semiedge.backtest.intraday_dip.DaySession`
  objects and reuse that module's signal study, so exits match ``intraday_dip_v1``.
* D and S (daily bars) and N (overnight on 5-minute bars) are scored here.

A run reads only bars inside the requested period, plus earlier daily bars for context.
Nothing here places or simulates broker orders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from semiedge.backtest import intraday_dip as idip
from semiedge.backtest.intraday_dip import DaySession, Signal
from semiedge.backtest.intraday_signals import SessionBars
from semiedge.data.calendar import TradingCalendar
from semiedge.data.intraday import session_bar_starts
from semiedge.indicators.core import atr

# ------------------------------------------------------------------ fixed by the spec

U80_ADDED: tuple[str, ...] = (
    "QCOM",
    "TXN",
    "ADI",
    "NXPI",
    "MCHP",
    "ON",
    "MPWR",
    "TER",
    "ENTG",
    "ARM",
    "GFS",
    "SWKS",
    "QRVO",
    "LSCC",
    "AMKR",
    "COHR",
    "ONTO",
    "CRDO",
    "ALAB",
    "MTSI",
    "RMBS",
    "STM",
    "UMC",
    "NVMI",
    "AAPL",
    "DELL",
    "HPE",
    "NTAP",
    "PSTG",
    "WDC",
    "STX",
    "CIEN",
    "LITE",
    "CLS",
    "FN",
    "CSCO",
    "IBM",
    "GLW",
    "APH",
    "JBL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "META",
    "TSLA",
    "NFLX",
    "ORCL",
    "CRM",
    "ADBE",
    "NOW",
    "PLTR",
    "SNOW",
    "PANW",
    "CRWD",
    "NET",
    "DDOG",
    "MDB",
    "SHOP",
    "UBER",
    "INTU",
    "WDAY",
    "ZS",
    "TEAM",
    "APP",
)
INDEX = {"U16": "SMH", "U80": "QQQ"}

DAILY_START = date(2016, 1, 4)
HISTORY = (date(2016, 4, 1), date(2024, 12, 13))
DEVELOPMENT = (date(2024, 12, 16), date(2026, 6, 30))
HOLDOUT = (date(2026, 7, 1), date(2026, 10, 23))
PERIODS = {"history": HISTORY, "development": DEVELOPMENT, "holdout": HOLDOUT}

SLIPPAGES = (5.0, 10.0, 20.0)
DECISION_SLIPPAGE = 10.0
SELECTION_MIN_TRADES = 300
HOLDOUT_MIN_TRADES = 60
MAX_SELECTED = 2
BOOTSTRAP_RESAMPLES = 2000

FULL_SESSION_BARS = 78
OR_BARS = 3
A_LAST_TRIGGER = 71  # closes at 15:30
A_TARGET_R = 2.0
B_DECISION = 5  # closes at 10:00
B_INDEX_MIN = 0.005
B_STOP_ATR = 0.5
D_INDEX_MIN = 0.015
D_STOP_ATR = 1.0
S_STOP_ATR = 2.0
S_SESSIONS = 3
N_DECISION = 75  # closes at 15:50
N_INDEX_MIN = 0.010
MIN_RISK_FRACTION = 0.0005
MIN_DAILY_BARS = 60
MIN_SETUP_CLOSES = 30
PLACEHOLDER_MAX_VOLUME = 1000
NO_TARGET = math.inf

SETUPS = ("breakout_20d", "bull_flag", "reclaim_ma20", "oversold", "oversold_bounce")
A_SETUP = dict(zip(("A1", "A2", "A3", "A4", "A5"), SETUPS, strict=True))
D_SETUP = dict(zip(("D1", "D2", "D3", "D4", "D5"), SETUPS, strict=True))
INTRADAY_CANDIDATES = ("A0", *A_SETUP, "B1", "B2")
DAILY_CANDIDATES = (*D_SETUP, "D6", "D7")
SWING_CANDIDATES = ("S6", "S7")
OVERNIGHT_CANDIDATES = ("N1", "N2")
CANDIDATES = (
    *INTRADAY_CANDIDATES,
    *DAILY_CANDIDATES,
    *SWING_CANDIDATES,
    *OVERNIGHT_CANDIDATES,
)
BASELINES = ("B0", "D0", "S0", "N0")


def baseline_of(candidate: str) -> str:
    if candidate in A_SETUP:
        return "A0"
    return {"A": "B0", "B": "B0", "D": "D0", "S": "S0", "N": "N0"}[candidate[0]]


def needs_history(candidate: str) -> bool:
    return candidate[0] in "DS"


# ------------------------------------------------------------------ daily panel


def wilder_rsi(close: np.ndarray, window: int = 14) -> np.ndarray:
    """Wilder RSI on a gap-free close array; NaN until ``window`` changes exist."""
    out = np.full(close.shape[0], np.nan)
    if close.shape[0] < window + 1:
        return out
    diff = np.diff(close)
    gain, loss = np.maximum(diff, 0.0), np.maximum(-diff, 0.0)
    avg_gain, avg_loss = float(gain[:window].mean()), float(loss[:window].mean())
    for i in range(window, diff.shape[0] + 1):
        if i > window:
            avg_gain = (avg_gain * (window - 1) + gain[i - 1]) / window
            avg_loss = (avg_loss * (window - 1) + loss[i - 1]) / window
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def daily_setups(close: pd.Series[float]) -> pd.DataFrame:
    """The skill's five daily setups per session, on closes only (spec, "Candidates", A1..A5).

    A missing close inside a rule's window makes that rule false.
    """
    c = close.astype(float)
    ma20 = c.rolling(20).mean()
    own = c.dropna()
    rsi = pd.Series(wilder_rsi(own.to_numpy()), index=own.index).reindex(c.index)
    enough = c.notna().cumsum() >= MIN_SETUP_CLOSES
    out = pd.DataFrame(
        {
            "breakout_20d": c > c.shift(1).rolling(20).max(),
            "bull_flag": (c.shift(5) / c.shift(15) - 1 > 0.10)
            & ((c.rolling(5).max() - c.rolling(5).min()) / c < 0.05),
            "reclaim_ma20": (c.shift(1) < ma20.shift(1)) & (c > ma20),
            "oversold": rsi < 30,
            "oversold_bounce": (rsi.shift(1) < 30) & (c > c.shift(1)),
        }
    )
    return out & enough.to_numpy()[:, None]


@dataclass
class DailyPanel:
    """Daily values on the full session calendar; a missing bar is NaN."""

    sessions: list[date]
    o: dict[str, np.ndarray]
    h: dict[str, np.ndarray]
    lo: dict[str, np.ndarray]
    c: dict[str, np.ndarray]
    atr: dict[str, np.ndarray]
    eligible: dict[str, np.ndarray]  # 60 bars through t and ATR > 0, for a trade after t
    setups: dict[str, dict[str, np.ndarray]]

    def index_of(self, session: date) -> int | None:
        try:
            return self._pos[session]
        except KeyError:
            return None

    def __post_init__(self) -> None:
        self._pos = {s: i for i, s in enumerate(self.sessions)}

    def ret(self, symbol: str) -> np.ndarray:
        c = self.c[symbol]
        out = np.full(c.shape[0], np.nan)
        out[1:] = c[1:] / c[:-1] - 1
        return out


def prepare_daily(
    daily: dict[str, pd.DataFrame],
    symbols: tuple[str, ...],
    *,
    end: date,
    calendar: TradingCalendar,
) -> DailyPanel:
    """Align every symbol's daily bars to the calendar up to ``end`` (later bars are never read)."""
    sessions = calendar.sessions_in_range(DAILY_START, end)
    index = pd.DatetimeIndex([pd.Timestamp(s) for s in sessions])
    panel = DailyPanel(sessions, {}, {}, {}, {}, {}, {}, {})
    for sym in symbols:
        frame = daily.get(sym)
        if frame is None or frame.empty:
            continue
        frame = frame.copy()
        frame.index = pd.DatetimeIndex(pd.DatetimeIndex(frame.index).date)
        frame = frame.reindex(index)
        if "volume" in frame.columns:
            placeholder = (frame["high"] == frame["low"]) & (
                frame["volume"] < PLACEHOLDER_MAX_VOLUME
            )
            frame.loc[placeholder, ["open", "high", "low", "close"]] = np.nan
        bad = frame[["open", "high", "low", "close"]].isna().any(axis=1)
        frame.loc[bad, ["open", "high", "low", "close"]] = np.nan
        o, h, lo, c = (frame[k].astype(float) for k in ("open", "high", "low", "close"))
        a = atr(h, lo, c).to_numpy(dtype="float64")
        have = c.notna().cumsum().to_numpy()  # bars through t, known at the close of t
        panel.o[sym], panel.h[sym] = o.to_numpy(), h.to_numpy()
        panel.lo[sym], panel.c[sym] = lo.to_numpy(), c.to_numpy()
        panel.atr[sym] = a
        panel.eligible[sym] = (have >= MIN_DAILY_BARS) & np.isfinite(a) & (a > 0)
        setups = daily_setups(c)
        panel.setups[sym] = {k: setups[k].to_numpy(dtype=bool) for k in SETUPS}
    return panel


# ------------------------------------------------------------------ study records and stats


@dataclass(frozen=True)
class Trade:
    symbol: str
    session: str  # entry session
    r_multiple: float
    reason: str


def stats(r: np.ndarray, *, seed: int = 0) -> dict[str, Any]:
    """Trades, mean R, win rate, mean without the five best, and a 95% bootstrap interval."""
    if r.size == 0:
        return {"trades": 0}
    rng = np.random.default_rng(seed)
    means = np.empty(BOOTSTRAP_RESAMPLES)
    for i in range(BOOTSTRAP_RESAMPLES):
        means[i] = r[rng.integers(0, r.size, r.size)].mean()
    trimmed = np.sort(r)[:-5] if r.size > 5 else np.array([])
    return {
        "trades": int(r.size),
        "mean_r": round(float(r.mean()), 4),
        "median_r": round(float(np.median(r)), 4),
        "mean_r_without_top5": round(float(trimmed.mean()), 4) if trimmed.size else None,
        "win_rate": round(float((r > 0).mean()), 4),
        "mean_r_95ci": [round(float(np.quantile(means, q)), 4) for q in (0.025, 0.975)],
    }


def r_multiples(trades: list[Trade]) -> np.ndarray:
    return np.array([t.r_multiple for t in trades], dtype="float64")


# ------------------------------------------------------------------ D and S: daily bars


def _range(panel: DailyPanel, start: date, end: date) -> tuple[int, int]:
    idx = [i for i, s in enumerate(panel.sessions) if start <= s <= end]
    if not idx:
        raise ValueError("no sessions in the requested period")
    return idx[0], idx[-1]


def _daily_signal_mask(panel: DailyPanel, sym: str, index: str, candidate: str) -> np.ndarray:
    """Signal at the close of each session ``t`` for one symbol."""
    ok = panel.eligible[sym]
    if candidate in ("D0", "S0"):
        return ok
    if candidate in D_SETUP:
        return np.asarray(ok & panel.setups[sym][D_SETUP[candidate]], dtype=bool)
    r, r_idx = panel.ret(sym), panel.ret(index)
    with np.errstate(invalid="ignore"):
        strong = r_idx >= D_INDEX_MIN
        side = r < r_idx if candidate in ("D6", "S6") else r > r_idx
    return ok & strong & side


def daily_study(
    panel: DailyPanel,
    symbols: tuple[str, ...],
    index: str,
    candidate: str,
    *,
    start: date,
    end: date,
    slippage_bps: float,
) -> list[Trade]:
    """D candidates: enter at the next open, exit at a 1-ATR stop or that session's close."""
    slip = slippage_bps / 10_000.0
    first, last = _range(panel, start, end)
    trades: list[Trade] = []
    for sym in symbols:
        if sym not in panel.c or index not in panel.c:
            continue
        mask = _daily_signal_mask(panel, sym, index, candidate)
        o, lo, c, a = panel.o[sym], panel.lo[sym], panel.c[sym], panel.atr[sym]
        for d in range(max(first, 1), last + 1):  # d is the entry session, t = d - 1
            t = d - 1
            if not mask[t] or math.isnan(o[d]):
                continue
            stop = o[d] - D_STOP_ATR * a[t]
            stopped = lo[d] <= stop
            exit_price = stop if stopped else c[d]
            r = (exit_price * (1 - slip) - o[d] * (1 + slip)) / (D_STOP_ATR * a[t])
            trades.append(
                Trade(sym, panel.sessions[d].isoformat(), float(r), "stop" if stopped else "close")
            )
    return trades


def swing_study(
    panel: DailyPanel,
    symbols: tuple[str, ...],
    index: str,
    candidate: str,
    *,
    start: date,
    end: date,
    slippage_bps: float,
) -> list[Trade]:
    """S candidates: enter at the next open, 2-ATR stop, exit at the close of session 3."""
    slip = slippage_bps / 10_000.0
    first, last = _range(panel, start, end)
    trades: list[Trade] = []
    for sym in symbols:
        if sym not in panel.c or index not in panel.c:
            continue
        mask = _daily_signal_mask(panel, sym, index, candidate)
        o, lo, c, a = panel.o[sym], panel.lo[sym], panel.c[sym], panel.atr[sym]
        for d in range(max(first, 1), last - S_SESSIONS + 2):  # the exit stays inside the period
            t = d - 1
            held = range(d, d + S_SESSIONS)
            if not mask[t] or any(math.isnan(o[j]) for j in held):
                continue
            risk = S_STOP_ATR * a[t]
            stop = o[d] - risk
            exit_price, reason = c[d + S_SESSIONS - 1], "time"
            for j in held:
                if j > d and o[j] <= stop:
                    exit_price, reason = o[j], "stop_gap"
                    break
                if lo[j] <= stop:
                    exit_price, reason = stop, "stop"
                    break
            r = (exit_price * (1 - slip) - o[d] * (1 + slip)) / risk
            trades.append(Trade(sym, panel.sessions[d].isoformat(), float(r), reason))
    return trades


# ------------------------------------------------------------------ 5-minute sessions


@dataclass
class IntradayPanel:
    """Full sessions only: per symbol, arrays of shape (sessions, 78); a missing bar is NaN."""

    sessions: list[date]
    o: dict[str, np.ndarray]
    h: dict[str, np.ndarray]
    lo: dict[str, np.ndarray]
    c: dict[str, np.ndarray]
    v: dict[str, np.ndarray]

    def bars(self, symbol: str, i: int) -> SessionBars:
        return SessionBars(
            self.o[symbol][i], self.h[symbol][i], self.lo[symbol][i], self.c[symbol][i],
            self.v[symbol][i],
        )  # fmt: skip


def prepare_intraday(
    intraday: dict[str, pd.DataFrame],
    symbols: tuple[str, ...],
    *,
    start: date,
    end: date,
    calendar: TradingCalendar,
) -> IntradayPanel:
    """Grid every symbol's bars for the full sessions in ``start..end`` (no bar outside is read)."""
    if start > end:
        raise ValueError("start must not exceed end")
    sessions, starts = [], []
    for session in calendar.sessions_in_range(start, end):
        grid = session_bar_starts(calendar, session)
        if len(grid) == FULL_SESSION_BARS:
            sessions.append(session)
            starts.append(grid)
    panel = IntradayPanel(sessions, {}, {}, {}, {}, {})
    if not sessions:
        return panel
    every = starts[0].append(starts[1:]) if len(starts) > 1 else starts[0]
    shape = (len(sessions), FULL_SESSION_BARS)
    for sym in symbols:
        frame = intraday.get(sym)
        if frame is None or frame.empty:
            continue
        day = frame.reindex(every)
        v = day["volume"].to_numpy(dtype="float64").reshape(shape)
        empty = ~(v > 0)  # zero volume or absent: missing
        for name, store in (
            ("open", panel.o), ("high", panel.h), ("low", panel.lo), ("close", panel.c),
        ):  # fmt: skip
            arr = day[name].to_numpy(dtype="float64").reshape(shape).copy()
            arr[empty] = np.nan
            store[sym] = arr
        panel.v[sym] = np.where(empty, np.nan, v)
    return panel


def _floor_cent(x: float) -> float:
    return math.floor(x * 100 + 1e-9) / 100


def orb_signal(b: SessionBars) -> Signal | None:
    """The skill's opening-range breakout: first close above the 15-minute high (candidate A)."""
    if b.n != FULL_SESSION_BARS or not bool(b.present[:OR_BARS].all()):
        return None
    orh, orl = float(b.h[:OR_BARS].max()), float(b.lo[:OR_BARS].min())
    for k in range(OR_BARS, A_LAST_TRIGGER + 1):
        c = float(b.c[k])
        if math.isnan(c):
            return None  # a gap before the trigger: the session is not usable
        if c > orh:
            stop = _floor_cent((orh + orl) / 2)
            r0 = c - stop
            if not (stop > 0 and r0 >= MIN_RISK_FRACTION * c):
                return None
            return Signal("", k, stop, c + A_TARGET_R * r0, r0, c, -(c / orh))
    return None


def follow_signals(
    b: SessionBars, idx: SessionBars, *, pc: float, pc_idx: float, atr_d1: float
) -> dict[str, Signal]:
    """Sector follow at 10:00 (candidates B0, B1, B2)."""
    k = B_DECISION
    if b.n != FULL_SESSION_BARS or not bool(b.present[: k + 1].all()):
        return {}
    c = float(b.c[k])
    r0 = B_STOP_ATR * atr_d1
    stop = c - r0
    if not (stop > 0 and r0 >= MIN_RISK_FRACTION * c):
        return {}
    out = {"B0": Signal("", k, stop, NO_TARGET, r0, c, 0.0)}
    c_idx = float(idx.c[k])
    if math.isnan(c_idx):
        return out
    r, r_idx = c / pc - 1, c_idx / pc_idx - 1
    if r_idx >= B_INDEX_MIN:
        if r < r_idx:
            out["B1"] = Signal("", k, stop, NO_TARGET, r0, c, r - r_idx)
        elif r > r_idx:
            out["B2"] = Signal("", k, stop, NO_TARGET, r0, c, -(r - r_idx))
    return out


def _add(sigs: dict[str, dict[int, list[Signal]]], variant: str, sym: str, s: Signal) -> None:
    sigs[variant].setdefault(s.k, []).append(
        Signal(sym, s.k, s.stop, s.target, s.r0, s.close, s.score)
    )


def prepare_sessions(
    bars: IntradayPanel, panel: DailyPanel, symbols: tuple[str, ...], index: str
) -> list[DaySession]:
    """Signals of A0..A5 and B0..B2 for every full session, for the shared same-day study."""
    variants = ("A0", *A_SETUP, "B0", "B1", "B2")
    out: list[DaySession] = []
    for i, session in enumerate(bars.sessions):
        d = panel.index_of(session)
        sigs: dict[str, dict[int, list[Signal]]] = {v: {} for v in variants}
        day: dict[str, SessionBars] = {}
        idx = bars.bars(index, i) if index in bars.c else None
        for sym in symbols:
            if sym not in bars.c or sym not in panel.c or d is None or d < 1:
                continue
            b = bars.bars(sym, i)
            day[sym] = b
            t = d - 1
            if not panel.eligible[sym][t] or math.isnan(panel.c[sym][t]):
                continue

            orb = orb_signal(b)
            if orb is not None:
                _add(sigs, "A0", sym, orb)
                for variant, setup in A_SETUP.items():
                    if panel.setups[sym][setup][t]:
                        _add(sigs, variant, sym, orb)
            if idx is not None and index in panel.c and not math.isnan(panel.c[index][t]):
                found = follow_signals(
                    b,
                    idx,
                    pc=float(panel.c[sym][t]),
                    pc_idx=float(panel.c[index][t]),
                    atr_d1=float(panel.atr[sym][t]),
                )
                for variant, s in found.items():
                    _add(sigs, variant, sym, s)
        for by_k in sigs.values():
            for found_at in by_k.values():
                found_at.sort(key=lambda s: (s.score, s.symbol))
        out.append(DaySession(session, FULL_SESSION_BARS, day, sigs))
    return out


def intraday_study(
    sessions: list[DaySession], candidate: str, *, slippage_bps: float
) -> list[Trade]:
    return [
        Trade(t.symbol, t.session, t.r_multiple, t.reason)
        for t in idip.signal_study(sessions, candidate, slippage_bps=slippage_bps)
    ]


def overnight_study(
    bars: IntradayPanel,
    panel: DailyPanel,
    symbols: tuple[str, ...],
    index: str,
    candidate: str,
    *,
    calendar: TradingCalendar,
    slippage_bps: float,
) -> list[Trade]:
    """N candidates: buy at 15:50, sell at the next session's open; both sessions full."""
    slip = slippage_bps / 10_000.0
    k = N_DECISION
    trades: list[Trade] = []
    if index not in bars.c or index not in panel.c:
        return trades
    for i, session in enumerate(bars.sessions[:-1]):
        if calendar.next_session(session) != bars.sessions[i + 1]:
            continue  # the next session is a half day or outside the period
        d = panel.index_of(session)
        if d is None or d < 1:
            continue
        t = d - 1
        c_idx, pc_idx = float(bars.c[index][i, k]), float(panel.c[index][t])
        r_idx = c_idx / pc_idx - 1 if not (math.isnan(c_idx) or math.isnan(pc_idx)) else math.nan
        for sym in symbols:
            if sym not in bars.c or sym not in panel.c or not panel.eligible[sym][t]:
                continue
            pc = float(panel.c[sym][t])
            row = bars.c[sym][i]
            entry, exit_price = float(bars.o[sym][i, k + 1]), float(bars.o[sym][i + 1, 0])
            if (
                math.isnan(pc)
                or bool(np.isnan(row[: k + 1]).any())
                or math.isnan(entry)
                or math.isnan(exit_price)
            ):
                continue
            if candidate != "N0":
                r = float(row[k]) / pc - 1
                if not (r_idx >= N_INDEX_MIN):
                    continue
                if not (r > r_idx if candidate == "N1" else r < r_idx):
                    continue
            r_mult = (exit_price * (1 - slip) - entry * (1 + slip)) / float(panel.atr[sym][t])
            trades.append(Trade(sym, session.isoformat(), float(r_mult), "next_open"))
    return trades


# ------------------------------------------------------------------ the screen


@dataclass
class Inputs:
    panel: DailyPanel
    symbols: tuple[str, ...]
    index: str
    calendar: TradingCalendar
    bars: IntradayPanel | None = None
    sessions: list[DaySession] | None = None


def study(
    inp: Inputs, candidate: str, *, start: date, end: date, slippage_bps: float
) -> list[Trade]:
    """One candidate's (or baseline's) trades on one universe and period."""
    family = candidate[0]
    if family == "D":
        return daily_study(
            inp.panel, inp.symbols, inp.index, candidate,
            start=start, end=end, slippage_bps=slippage_bps,
        )  # fmt: skip
    if family == "S":
        return swing_study(
            inp.panel, inp.symbols, inp.index, candidate,
            start=start, end=end, slippage_bps=slippage_bps,
        )  # fmt: skip
    if inp.bars is None or inp.sessions is None:
        raise ValueError("5-minute candidates need the intraday panel")
    if family == "N":
        return overnight_study(
            inp.bars, inp.panel, inp.symbols, inp.index, candidate,
            calendar=inp.calendar, slippage_bps=slippage_bps,
        )  # fmt: skip
    return intraday_study(inp.sessions, candidate, slippage_bps=slippage_bps)


def gate_failures(
    s: dict[str, Any], base: dict[str, Any], *, min_trades: int, need_ci: bool
) -> list[str]:
    m = s.get("mean_r", -math.inf)
    trimmed = s.get("mean_r_without_top5")
    low = (s.get("mean_r_95ci") or [-math.inf])[0]
    return [
        *([f"fewer than {min_trades} trades"] if s.get("trades", 0) < min_trades else []),
        *(["mean R not above 0"] if not m > 0 else []),
        *(["mean R not above the baseline"] if not m > base.get("mean_r", -math.inf) else []),
        *(["mean R without the five best trades not above 0"] if not (trimmed or 0) > 0 else []),
        *(["lower bound of the 95% interval not above 0"] if need_ci and not low > 0 else []),
    ]


def select(
    results: dict[str, dict[str, dict[str, Any]]],
    history: dict[str, dict[str, dict[str, Any]]],
) -> tuple[list[str], dict[str, list[str]]]:
    """The spec's selection on development results at the decision cost.

    ``results[universe][candidate]`` and ``history[universe][candidate]`` hold :func:`stats`.
    Returns the selected ``candidate:universe`` pairs (at most two) and every pair's failures.
    """
    notes: dict[str, list[str]] = {}
    qualified: list[tuple[float, str]] = []
    for universe, by_candidate in results.items():
        for cand in CANDIDATES:
            s = by_candidate[cand]
            fails = gate_failures(
                s, by_candidate[baseline_of(cand)], min_trades=SELECTION_MIN_TRADES, need_ci=True
            )
            if needs_history(cand) and not history[universe][cand].get("mean_r", -math.inf) > 0:
                fails.append("history-check mean R not above 0")
            pair = f"{cand}:{universe}"
            notes[pair] = fails
            if not fails:
                qualified.append((-s["mean_r_95ci"][0], pair))
    return [pair for _, pair in sorted(qualified)[:MAX_SELECTED]], notes


def holdout_failures(s: dict[str, Any], base: dict[str, Any]) -> list[str]:
    return gate_failures(s, base, min_trades=HOLDOUT_MIN_TRADES, need_ci=False)
