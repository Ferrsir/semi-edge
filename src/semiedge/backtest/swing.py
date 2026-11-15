"""Backtest of ``swing_v1`` (spec ``docs/specifications/swing_v1.md``, #180).

Five long-only multi-day candidates on daily bars: three dip rules (R1..R3) and two trend rules
(T1, T2), each with a baseline drawn from the same universe. One set of held-session rules
serves the signal study and the $5,000 portfolio run. Three Monte Carlo checks are fixed by the
spec: a trade bootstrap, a bootstrap of the edge over the baseline, and a block bootstrap of the
account's daily returns.

Signals at the close of ``t`` read bars up to ``t`` only. The panel is cut at the end of the
requested period before any indicator is computed. Nothing here places or simulates broker
orders.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import numpy as np
import pandas as pd

from semiedge.backtest.dip import rsi2
from semiedge.backtest.skill_follow import U80_ADDED, DailyPanel, prepare_daily
from semiedge.config.models import RiskLimits
from semiedge.data.calendar import TradingCalendar

# ------------------------------------------------------------------ fixed by the spec

X60: tuple[str, ...] = (
    "JPM", "BAC", "GS", "MS", "WFC", "C", "V", "MA", "AXP", "BLK", "SCHW", "UNH", "JNJ", "LLY",
    "PFE", "MRK", "ABBV", "TMO", "ABT", "AMGN", "XOM", "CVX", "COP", "SLB", "CAT", "DE", "BA",
    "GE", "HON", "LMT", "RTX", "UPS", "FDX", "WMT", "COST", "HD", "LOW", "TGT", "MCD", "SBUX",
    "NKE", "DIS", "CMCSA", "T", "VZ", "KO", "PEP", "PG", "PM", "MO", "LIN", "NEE", "DUK", "SO",
    "AMT", "PLD", "SPG", "BKNG", "GM", "F",
)  # fmt: skip
U80_EXTRA = U80_ADDED

Sample = Literal["development", "holdout", "unseen"]
SAMPLES: dict[str, tuple[str, str, date, date]] = {  # universe, index, start, end
    "development": ("U80", "QQQ", date(2016, 4, 1), date(2024, 12, 31)),
    "holdout": ("U80", "QQQ", date(2025, 1, 2), date(2026, 10, 23)),
    "unseen": ("X60", "SPY", date(2016, 4, 1), date(2026, 10, 23)),
}

SLIPPAGES = (5.0, 10.0, 20.0)
DECISION_SLIPPAGE = 10.0
MIN_BARS = 260
MIN_RISK_FRACTION = 0.0005
MOM_SKIP, MOM_SPAN = 21, 126
BREAKOUT_WINDOW = 100
TRAIL_ATR = 3.0
ROTATION_TOP = 3
RESET_SESSIONS = 21
BOOTSTRAP_RESAMPLES = 2000
MC_PATHS = 5000
MC_BLOCK = 21
MC_MIN_POSITIVE = 0.90
SELECTION_MIN_TRADES = 300
HOLDOUT_MIN_TRADES = 40
MAX_SELECTED = 2
SESSIONS_PER_YEAR = 252


@dataclass(frozen=True)
class Rule:
    name: str
    family: Literal["dip", "trend"]
    baseline: str
    stop_atr: float
    max_hold: int
    overlap: bool  # the study takes overlapping signals of one symbol


RULES: dict[str, Rule] = {
    "R1": Rule("R1", "dip", "R1b", 2.0, 3, True),
    "R2": Rule("R2", "dip", "R2b", 3.0, 7, True),
    "R3": Rule("R3", "dip", "R3b", 3.0, 7, True),
    "T1": Rule("T1", "trend", "Tb", 3.0, 120, False),
    "T2": Rule("T2", "trend", "Tb", 3.0, 120, False),
    "R1b": Rule("R1b", "dip", "R1b", 2.0, 3, True),
    "R2b": Rule("R2b", "dip", "R2b", 3.0, 7, True),
    "R3b": Rule("R3b", "dip", "R3b", 3.0, 7, True),
    "Tb": Rule("Tb", "trend", "Tb", 3.0, 120, False),
}
CANDIDATES = ("R1", "R2", "R3", "T1", "T2")
BASELINES = ("R1b", "R2b", "R3b", "Tb")


# ------------------------------------------------------------------ signals


@dataclass
class Book:
    """One candidate's signals on a panel: everything the held-session rules read."""

    rule: Rule
    entry: dict[str, np.ndarray]  # signal at the close of t
    score: dict[str, np.ndarray]  # lower ranks first
    sma5: dict[str, np.ndarray]


def _sma(c: pd.Series[float], n: int) -> np.ndarray:
    return c.rolling(n).mean().to_numpy()  # NaN when any close in the window is missing


def week_ends(sessions: list[date]) -> np.ndarray:
    """True on the last session of each ISO week (the final session of the panel included)."""
    weeks = [s.isocalendar()[:2] for s in sessions]
    return np.array([i + 1 == len(weeks) or weeks[i + 1] != weeks[i] for i in range(len(weeks))])


def build(  # noqa: PLR0912, PLR0915 - one branch per candidate keeps the rules side by side
    panel: DailyPanel, symbols: tuple[str, ...], index: str, name: str
) -> Book:
    """Signals of one candidate or baseline for every symbol (spec, "Candidates")."""
    rule = RULES[name]
    idx_c = pd.Series(panel.c[index])
    with np.errstate(invalid="ignore"):
        regime50 = idx_c.to_numpy() > _sma(idx_c, 50)
        regime200 = idx_c.to_numpy() > _sma(idx_c, 200)
    weekly = week_ends(panel.sessions)
    book = Book(rule, {}, {}, {})
    mom: dict[str, np.ndarray] = {}
    base: dict[str, np.ndarray] = {}
    for sym in symbols:
        if sym not in panel.c:
            continue
        c = pd.Series(panel.c[sym])
        a = panel.atr[sym]
        cv = c.to_numpy()
        sma5, sma50, sma200 = _sma(c, 5), _sma(c, 50), _sma(c, 200)
        m = (c.shift(MOM_SKIP) / c.shift(MOM_SKIP + MOM_SPAN) - 1).to_numpy()
        have = c.notna().cumsum().to_numpy() >= MIN_BARS
        ok = have & np.isfinite(cv) & np.isfinite(a) & (a > 0)
        with np.errstate(invalid="ignore"):
            up50, up200 = cv > sma50, cv > sma200
            rsi = rsi2(c).to_numpy()
            if name in ("R1", "R1b"):
                entry = ok & up50 & regime50
                score = rsi
                if name == "R1":
                    entry = entry & (rsi < 10)
            elif name in ("R2", "R2b"):
                entry = ok & up200 & regime200
                score = rsi
                if name == "R2":
                    entry = entry & (rsi < 5)
            elif name in ("R3", "R3b"):
                entry = ok & up200 & regime200
                score = cv / sma5
                if name == "R3":
                    down3 = (
                        (cv < c.shift(1).to_numpy())
                        & (c.shift(1).to_numpy() < c.shift(2).to_numpy())
                        & (c.shift(2).to_numpy() < c.shift(3).to_numpy())
                    )
                    entry = entry & down3 & (cv < sma5)
            else:  # trend rules
                entry = ok & up50 & regime200 & np.isfinite(m)
                score = -m
                if name == "T2":
                    entry = entry & (cv > c.shift(1).rolling(BREAKOUT_WINDOW).max().to_numpy())
                else:  # T1 and Tb act on the last session of each week
                    entry = entry & weekly
        entry = entry & np.isfinite(score)
        if rule.family == "dip" and name in CANDIDATES:
            entry = entry & np.isfinite(sma5)
        book.entry[sym] = np.asarray(entry, dtype=bool)
        book.score[sym] = np.asarray(score, dtype="float64")
        book.sma5[sym] = sma5
        mom[sym], base[sym] = m, book.entry[sym]
    if name == "T1":  # keep only the three highest MOM among the eligible, per week-end
        syms = list(book.entry)
        if syms:
            eligible = np.array([base[s] for s in syms])
            ranks = np.where(eligible, np.array([mom[s] for s in syms]), -np.inf)
            order = np.argsort(-ranks, axis=0, kind="stable")  # ties keep symbol order
            top = np.zeros_like(eligible)
            for row in range(min(ROTATION_TOP, len(syms))):
                top[order[row], np.arange(eligible.shape[1])] = True
            for k, s in enumerate(syms):
                book.entry[s] = eligible[k] & top[k]
    return book


# ------------------------------------------------------------------ one position


@dataclass
class _Pos:
    symbol: str
    entry_i: int
    fill: float
    stop: float
    risk: float  # planned at the signal: C(t) - stop
    shares: int
    held: int = 0
    high_close: float = -math.inf
    pending: str | None = None


def _try_fill(panel: DailyPanel, sym: str, i: int, stop: float, slip: float) -> float | str:
    o = float(panel.o[sym][i])
    if math.isnan(o):
        return "missing_fill_bar"
    if o <= stop:
        return "gap_through_stop"
    fill = o * (1 + slip)
    if fill - stop < MIN_RISK_FRACTION * fill:
        return "stop_too_close"
    return fill


def _at_open(panel: DailyPanel, pos: _Pos, i: int) -> tuple[str, float] | None:
    """Steps 1 and 2: an exit decided at the last close, or a gap through the stop."""
    o = float(panel.o[pos.symbol][i])
    if math.isnan(o):
        return None
    if pos.pending is not None:
        return pos.pending, o
    if i > pos.entry_i and o <= pos.stop:
        return "stop_gap", o
    return None


def _in_day(panel: DailyPanel, book: Book, pos: _Pos, i: int) -> tuple[str, float] | None:
    """Steps 3 and 4: the stop, then the close-based exits for the next open."""
    sym, rule = pos.symbol, book.rule
    c = float(panel.c[sym][i])
    if math.isnan(c):
        return None
    if float(panel.lo[sym][i]) <= pos.stop:
        return "stop", pos.stop
    pos.held += 1
    if rule.family == "dip":
        if c > float(book.sma5[sym][i]):
            pos.pending = "bounce"
        elif pos.held >= rule.max_hold:
            pos.pending = "time"
        return None
    pos.high_close = max(pos.high_close, c)
    a = float(panel.atr[sym][i])
    if not math.isnan(a) and c < pos.high_close - TRAIL_ATR * a:
        pos.pending = "trail"
    elif pos.held >= rule.max_hold:
        pos.pending = "time"
    return None


def _last_close(panel: DailyPanel, sym: str, i: int, fallback: float) -> float:
    for j in range(i, -1, -1):
        v = float(panel.c[sym][j])
        if not math.isnan(v):
            return v
    return fallback


def _window(panel: DailyPanel, start: date, end: date) -> tuple[int, int] | None:
    idx = [i for i, s in enumerate(panel.sessions) if start <= s <= end]
    return (idx[0], idx[-1]) if idx else None


# ------------------------------------------------------------------ signal study


@dataclass(frozen=True)
class StudyTrade:
    symbol: str
    entry_session: str
    exit_session: str
    reason: str
    r_multiple: float


def signal_study(
    panel: DailyPanel, book: Book, *, start: date, end: date, slippage_bps: float
) -> list[StudyTrade]:
    """Every signal in the window as a one-share trade scored in planned R."""
    rng = _window(panel, start, end)
    if rng is None:
        return []
    i0, i1 = rng
    slip = slippage_bps / 10_000.0
    rule = book.rule
    trades: list[StudyTrade] = []
    for sym, entry in book.entry.items():
        free_from = i0
        for i in (int(x) + i0 for x in np.flatnonzero(entry[i0:i1])):  # no open after i1
            if not rule.overlap and i < free_from:
                continue
            close = float(panel.c[sym][i])
            risk = rule.stop_atr * float(panel.atr[sym][i])
            stop = close - risk
            fill = _try_fill(panel, sym, i + 1, stop, slip)
            if isinstance(fill, str):
                continue
            pos = _Pos(sym, i + 1, fill, stop, risk, 1)
            done: tuple[str, float, int] | None = None
            for j in range(i + 1, i1 + 1):
                res = _at_open(panel, pos, j) if j > pos.entry_i else None
                res = res or _in_day(panel, book, pos, j)
                if res is not None:
                    done = (res[0], res[1], j)
                    break
            if done is None:
                done = ("end_of_window", _last_close(panel, sym, i1, fill), i1)
            reason, price, j = done
            free_from = j
            trades.append(
                StudyTrade(
                    sym,
                    panel.sessions[i + 1].isoformat(),
                    panel.sessions[j].isoformat(),
                    reason,
                    round((price * (1 - slip) - fill) / risk, 4),
                )
            )
    return trades


def r_multiples(trades: list[StudyTrade]) -> np.ndarray:
    return np.array([t.r_multiple for t in trades], dtype="float64")


def _boot_means(r: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = np.empty(BOOTSTRAP_RESAMPLES)
    for i in range(BOOTSTRAP_RESAMPLES):
        out[i] = r[rng.integers(0, r.size, r.size)].mean()
    return out


def study_stats(trades: list[StudyTrade], *, seed: int = 0) -> dict[str, Any]:
    """MC1: trades, mean R, win rate, mean without the five best, 95% bootstrap interval."""
    r = r_multiples(trades)
    if r.size == 0:
        return {"trades": 0}
    means = _boot_means(r, np.random.default_rng(seed))
    trimmed = np.sort(r)[:-5] if r.size > 5 else np.array([])
    return {
        "trades": int(r.size),
        "mean_r": round(float(r.mean()), 4),
        "median_r": round(float(np.median(r)), 4),
        "mean_r_without_top5": round(float(trimmed.mean()), 4) if trimmed.size else None,
        "win_rate": round(float((r > 0).mean()), 4),
        "mean_r_95ci": [round(float(np.quantile(means, q)), 4) for q in (0.025, 0.975)],
        "exits": dict(Counter(t.reason for t in trades)),
    }


def edge_over_baseline(
    trades: list[StudyTrade], baseline: list[StudyTrade], *, seed: int = 0
) -> dict[str, Any]:
    """MC2: bootstrap interval of (candidate mean R - baseline mean R), sides independent."""
    r, b = r_multiples(trades), r_multiples(baseline)
    if r.size == 0 or b.size == 0:
        return {"difference": None, "ci95": [None, None]}
    rng = np.random.default_rng(seed)
    diff = _boot_means(r, rng) - _boot_means(b, rng)
    return {
        "difference": round(float(r.mean() - b.mean()), 4),
        "ci95": [round(float(np.quantile(diff, q)), 4) for q in (0.025, 0.975)],
    }


# ------------------------------------------------------------------ $5,000 portfolio run


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_session: str
    exit_session: str
    shares: int
    fill: float
    exit_price: float
    reason: str
    pnl: float
    r_multiple: float


@dataclass
class _Pending:
    symbol: str
    stop: float
    risk: float
    shares: int
    signal_equity: float


@dataclass
class RunResult:
    start_equity: float
    slippage_bps: float
    halt_model: str
    sessions: list[str]
    equity: list[float]
    trades: list[Trade] = field(default_factory=list)
    exposure: list[float] = field(default_factory=list)
    drawdown_halts: list[str] = field(default_factory=list)
    refused: Counter[str] = field(default_factory=Counter)

    def daily_returns(self) -> np.ndarray:
        e = np.array([self.start_equity, *self.equity])
        return np.asarray(e[1:] / e[:-1] - 1, dtype="float64")

    def summary(self) -> dict[str, Any]:
        e = np.array(self.equity) if self.equity else np.array([self.start_equity])
        final = float(e[-1])
        peak = np.maximum.accumulate(np.concatenate([[self.start_equity], e]))[1:]
        years = max(len(e) / SESSIONS_PER_YEAR, 1e-9)
        wins = [t for t in self.trades if t.pnl > 0]
        return {
            "slippage_bps": self.slippage_bps,
            "halt_model": self.halt_model,
            "trades": len(self.trades),
            "win_rate": round(len(wins) / len(self.trades), 4) if self.trades else None,
            "mean_r": (
                round(float(np.mean([t.r_multiple for t in self.trades])), 4)
                if self.trades
                else None
            ),
            "net_pnl": round(final - self.start_equity, 2),
            "net_pct": round((final / self.start_equity - 1) * 100, 2),
            "cagr_pct": round(((final / self.start_equity) ** (1 / years) - 1) * 100, 2),
            "max_drawdown_pct": round(float((e / peak - 1).min()) * 100, 2),
            "avg_gross_exposure_pct": (
                round(float(np.mean(self.exposure)) * 100, 1) if self.exposure else 0.0
            ),
            "drawdown_halts": self.drawdown_halts,
            "refused": dict(self.refused),
        }


def replay(  # noqa: PLR0912, PLR0915 - one pass over the sessions keeps the event order explicit
    panel: DailyPanel,
    book: Book,
    *,
    start: date,
    end: date,
    start_equity: float,
    limits: RiskLimits,
    slippage_bps: float,
    halt_model: Literal["reset", "strict"] = "reset",
) -> RunResult:
    """The spec's portfolio run under the given risk profile (unrestricted account)."""
    rng = _window(panel, start, end)
    out = RunResult(start_equity, slippage_bps, halt_model, [], [])
    if rng is None:
        return out
    i0, i1 = rng
    slip = slippage_bps / 10_000.0
    rule = book.rule
    cash = start_equity
    held: dict[str, _Pos] = {}
    pending: list[_Pending] = []
    prev_equity = peak = start_equity
    halted_at: int | None = None

    def close_out(pos: _Pos, i: int, reason: str, price: float) -> None:
        nonlocal cash
        net = price * (1 - slip)
        cash += pos.shares * net
        out.trades.append(
            Trade(
                pos.symbol,
                panel.sessions[pos.entry_i].isoformat(),
                panel.sessions[i].isoformat(),
                pos.shares,
                round(pos.fill, 4),
                round(price, 4),
                reason,
                round(pos.shares * (net - pos.fill), 2),
                round((net - pos.fill) / pos.risk, 4),
            )
        )
        del held[pos.symbol]

    for i in range(i0, i1 + 1):
        for pos in list(held.values()):  # steps 1 and 2
            res = _at_open(panel, pos, i)
            if res is not None:
                close_out(pos, i, *res)
        for p in pending:  # fills at this open, in rank order
            fill = _try_fill(panel, p.symbol, i, p.stop, slip)
            if isinstance(fill, str):
                out.refused[fill] += 1
                continue
            gross = sum(
                q.shares * _last_close(panel, q.symbol, i - 1, q.fill) for q in held.values()
            )
            room = limits.max_gross_exposure_pct * p.signal_equity - gross
            shares = min(
                p.shares,
                math.floor(p.signal_equity * limits.max_risk_per_trade_pct / (fill - p.stop)),
                math.floor(p.signal_equity * limits.max_position_pct / fill),
                math.floor(max(room, 0.0) / fill),
                math.floor(cash / fill),
            )
            if shares < 1:
                out.refused["no_share_fits"] += 1
                continue
            cash -= shares * fill
            held[p.symbol] = _Pos(p.symbol, i, fill, p.stop, p.risk, shares)
        pending = []
        for pos in list(held.values()):  # steps 3 and 4
            res = _in_day(panel, book, pos, i)
            if res is not None:
                close_out(pos, i, *res)
        value = sum(q.shares * _last_close(panel, q.symbol, i, q.fill) for q in held.values())
        equity = cash + value
        out.sessions.append(panel.sessions[i].isoformat())
        out.equity.append(round(equity, 4))
        out.exposure.append(value / equity if equity > 0 else 0.0)
        daily_block = equity <= prev_equity * (1 - limits.daily_loss_halt_pct)
        prev_equity = equity
        peak = max(peak, equity)
        if halted_at is None and equity <= peak * (1 - limits.drawdown_halt_pct):
            halted_at = i
            out.drawdown_halts.append(panel.sessions[i].isoformat())
        elif halted_at is not None and halt_model == "reset" and i - halted_at >= RESET_SESSIONS:
            halted_at = None
            peak = equity  # the drawdown is measured from equity at the reset
        if i == i1 or daily_block or halted_at is not None:
            continue
        free = limits.max_positions - len(held)
        if free <= 0:
            continue
        found = sorted(
            (float(book.score[sym][i]), sym)
            for sym, entry in book.entry.items()
            if entry[i] and sym not in held
        )
        reserved = 0.0
        for _, sym in found[:free]:
            close = float(panel.c[sym][i])
            risk = rule.stop_atr * float(panel.atr[sym][i])
            room = limits.max_gross_exposure_pct * equity - value - reserved
            shares = min(
                math.floor(equity * limits.max_risk_per_trade_pct / risk),
                math.floor(equity * limits.max_position_pct / close),
                math.floor(max(room, 0.0) / close),
            )
            if shares < 1:
                out.refused["no_share_fits_at_signal"] += 1
                continue
            reserved += shares * close
            pending.append(_Pending(sym, close - risk, risk, shares, equity))
    return out


def account_paths(returns: np.ndarray, *, seed: int = 0) -> dict[str, Any]:
    """MC3: circular block bootstrap of the account's daily returns (spec, "Monte Carlo")."""
    n = returns.size
    if n == 0:
        return {"paths": 0}
    rng = np.random.default_rng(seed)
    blocks = math.ceil(n / MC_BLOCK)
    finals = np.empty(MC_PATHS)
    drawdowns = np.empty(MC_PATHS)
    offsets = np.arange(MC_BLOCK)
    for start in range(0, MC_PATHS, 500):
        size = min(500, MC_PATHS - start)
        starts = rng.integers(0, n, (size, blocks))
        idx = ((starts[:, :, None] + offsets) % n).reshape(size, -1)[:, :n]
        curve = np.cumprod(1 + returns[idx], axis=1)
        peak = np.maximum(np.maximum.accumulate(curve, axis=1), 1.0)
        finals[start : start + size] = curve[:, -1]
        drawdowns[start : start + size] = (curve / peak - 1).min(axis=1)
    return {
        "paths": MC_PATHS,
        "share_positive": round(float((finals > 1).mean()), 4),
        "final_return_pct": {
            f"p{q}": round(float((np.quantile(finals, q / 100) - 1) * 100), 2) for q in (5, 50, 95)
        },
        "max_drawdown_pct": {
            "median": round(float(np.median(drawdowns)) * 100, 2),
            "p95_worst": round(float(np.quantile(drawdowns, 0.05)) * 100, 2),
        },
    }


def index_buy_and_hold(panel: DailyPanel, index: str, start: date, end: date) -> float | None:
    rng = _window(panel, start, end)
    if rng is None:
        return None
    a, b = float(panel.c[index][rng[0]]), float(panel.c[index][rng[1]])
    return None if math.isnan(a) or math.isnan(b) else round((b / a - 1) * 100, 2)


# ------------------------------------------------------------------ gates


def selection_failures(
    study: dict[str, Any], edge: dict[str, Any], run: dict[str, Any], paths: dict[str, Any]
) -> list[str]:
    low = (study.get("mean_r_95ci") or [-math.inf])[0]
    edge_low = edge["ci95"][0]
    return [
        *(
            [f"fewer than {SELECTION_MIN_TRADES} trades"]
            if study.get("trades", 0) < SELECTION_MIN_TRADES
            else []
        ),
        *(["mean R not above 0"] if not study.get("mean_r", -math.inf) > 0 else []),
        *(
            ["mean R without the five best trades not above 0"]
            if not (study.get("mean_r_without_top5") or 0) > 0
            else []
        ),
        *(["MC1 lower bound not above 0"] if not low > 0 else []),
        *(["MC2 lower bound not above 0"] if not (edge_low is not None and edge_low > 0) else []),
        *(["portfolio net not positive"] if not run.get("net_pnl", 0) > 0 else []),
        *(
            [f"MC3 below {MC_MIN_POSITIVE:.0%} positive paths"]
            if not paths.get("share_positive", 0) >= MC_MIN_POSITIVE
            else []
        ),
    ]


def holdout_failures(
    study: dict[str, Any], baseline: dict[str, Any], run: dict[str, Any]
) -> list[str]:
    m = study.get("mean_r", -math.inf)
    return [
        *(
            [f"fewer than {HOLDOUT_MIN_TRADES} trades"]
            if study.get("trades", 0) < HOLDOUT_MIN_TRADES
            else []
        ),
        *(["mean R not above 0"] if not m > 0 else []),
        *(["mean R not above the baseline"] if not m > baseline.get("mean_r", -math.inf) else []),
        *(
            ["mean R without the five best trades not above 0"]
            if not (study.get("mean_r_without_top5") or 0) > 0
            else []
        ),
        *(["portfolio net not positive"] if not run.get("net_pnl", 0) > 0 else []),
    ]


def select(results: dict[str, dict[str, Any]]) -> tuple[list[str], dict[str, list[str]]]:
    """At most two qualifiers, by portfolio net result (ties to the first name)."""
    notes = {c: results[c]["failures"] for c in CANDIDATES}
    ranked = sorted((-results[c]["portfolio"]["net_pnl"], c) for c in CANDIDATES if not notes[c])
    return [c for _, c in ranked[:MAX_SELECTED]], notes


def panel_for(
    daily: dict[str, pd.DataFrame],
    symbols: tuple[str, ...],
    index: str,
    *,
    end: date,
    calendar: TradingCalendar,
) -> DailyPanel:
    return prepare_daily(daily, (*symbols, index), end=end, calendar=calendar)
