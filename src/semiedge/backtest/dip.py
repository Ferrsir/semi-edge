"""Backtest of ``dip_v1`` on cached daily bars (spec ``docs/specifications/dip_v1.md``, #133).

Two kinds of run, as the spec defines them:

* the **signal study** takes every signal as its own one-share trade (no limits, sizing, halts or
  account rules) and scores it in R, for both pre-registered variants and the baseline;
* the **portfolio replay** runs a $5,000 account through the account models with the spec's
  sizing, limits and halts.

Every value used at session ``t`` comes from bars up to ``t``; entries fill at the open of
``t + 1``. :func:`prepare_panel` drops every bar after the run's end before computing anything, so
a development run cannot see holdout prices. Nothing here places or simulates broker orders.
"""

from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Literal

import numpy as np
import pandas as pd

from semiedge.config.models import RiskLimits
from semiedge.indicators.core import atr, sma

AccountModel = Literal["unrestricted", "margin_pdt", "cash"]
ACCOUNT_MODELS: tuple[AccountModel, ...] = ("unrestricted", "margin_pdt", "cash")
Variant = Literal["S1", "S2", "baseline"]
VARIANTS: tuple[Variant, ...] = ("S1", "S2")
SLIPPAGES = (10.0, 20.0, 30.0)

# Fixed by the spec before any result (ground rule 3).
DEVELOPMENT = (date(2016, 4, 1), date(2024, 12, 31))
HOLDOUT = (date(2025, 1, 2), date(2026, 11, 13))
TREND_WINDOW = 50
BOUNCE_WINDOW = 5
ATR_WINDOW = 14
RSI_MAX = 10.0
REL_DROP_MAX = -1.5
STOP_ATR = 2.0
MIN_HISTORY = 60
MAX_HELD = 3
MIN_RISK_FRACTION = 0.0005
PLACEHOLDER_MAX_VOLUME = 1_000.0  # revision 1.1: H == L on less volume is not a trading session
PDT_EQUITY = 25_000.0
PDT_MAX_DAY_TRADES = 3
PDT_WINDOW = 5
SELECTION_SLIPPAGE = 20.0
SELECTION_MIN_TRADES = 200
HOLDOUT_MIN_TRADES = 60


# ------------------------------------------------------------------ indicators and panel


def rsi2(close: pd.Series[float]) -> pd.Series[float]:
    """Wilder RSI over two sessions, as defined in the spec; a missing close restarts seeding."""
    c = close.to_numpy(dtype="float64")
    out = np.full(c.shape[0], np.nan)
    avg_g = avg_l = math.nan
    seed: list[tuple[float, float]] = []
    for i in range(1, c.shape[0]):
        if math.isnan(c[i]) or math.isnan(c[i - 1]):
            avg_g = avg_l = math.nan
            seed = []
            continue
        d = float(c[i] - c[i - 1])
        g, lo = max(d, 0.0), max(-d, 0.0)
        if math.isnan(avg_g):
            seed.append((g, lo))
            if len(seed) < 2:
                continue
            avg_g = (seed[0][0] + seed[1][0]) / 2
            avg_l = (seed[0][1] + seed[1][1]) / 2
        else:
            avg_g, avg_l = (avg_g + g) / 2, (avg_l + lo) / 2
        out[i] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return pd.Series(out, index=close.index)


@dataclass
class Panel:
    """Every symbol on the benchmark's session grid, with the spec's values at each close."""

    sessions: list[date]
    symbols: tuple[str, ...]
    o: dict[str, np.ndarray]
    h: dict[str, np.ndarray]
    lo: dict[str, np.ndarray]
    c: dict[str, np.ndarray]
    sma5: dict[str, np.ndarray]
    atr14: dict[str, np.ndarray]
    rsi: dict[str, np.ndarray]
    rel_drop: dict[str, np.ndarray]
    shared: dict[str, np.ndarray]  # trend and eligibility conditions hold at the close
    smh_o: np.ndarray
    smh_c: np.ndarray

    def index_range(self, start: date, end: date) -> tuple[int, int] | None:
        idx = [i for i, s in enumerate(self.sessions) if start <= s <= end]
        return (idx[0], idx[-1]) if idx else None


def _session_dates(index: pd.Index) -> list[date]:
    return [pd.Timestamp(x).date() for x in index]


def _drop_placeholders(frame: pd.DataFrame) -> pd.DataFrame:
    """Revision 1.1: a bar with no range on almost no volume did not trade; it becomes missing."""
    if "volume" not in frame.columns:
        return frame
    placeholder = (frame["high"] == frame["low"]) & (frame["volume"] < PLACEHOLDER_MAX_VOLUME)
    out = frame.copy()
    out.loc[placeholder, ["open", "high", "low", "close"]] = np.nan
    return out


def prepare_panel(
    daily: dict[str, pd.DataFrame],
    *,
    tradable: tuple[str, ...],
    benchmark: str,
    end: date,
) -> Panel:
    """Grid and indicators from bars up to ``end`` only (later bars are dropped first)."""
    if benchmark not in daily:
        raise ValueError("benchmark bars are required")

    def cut(frame: pd.DataFrame) -> pd.DataFrame:
        keep = [d <= end for d in _session_dates(frame.index)]
        return frame.loc[keep]

    bench = cut(daily[benchmark])
    grid = pd.Index(_session_dates(bench.index))
    smh = bench.set_axis(grid)
    smh_c = smh["close"].astype(float)
    smh_trend = (smh_c > sma(smh_c, TREND_WINDOW)).to_numpy()
    smh_r3 = (smh_c / smh_c.shift(3) - 1).to_numpy()
    out = Panel(
        list(grid), tradable, {}, {}, {}, {}, {}, {}, {}, {}, {},
        smh["open"].to_numpy(dtype="float64"), smh_c.to_numpy(dtype="float64"),
    )  # fmt: skip
    for sym in tradable:
        raw = daily.get(sym)
        if raw is None or raw.empty:
            frame = pd.DataFrame(np.nan, index=grid, columns=["open", "high", "low", "close"])
        else:
            frame = cut(raw)
            frame = frame.set_axis(pd.Index(_session_dates(frame.index))).reindex(grid)
            frame = _drop_placeholders(frame)
        c = frame["close"].astype(float)
        h, lo = frame["high"].astype(float), frame["low"].astype(float)
        a = atr(h, lo, c, ATR_WINDOW)
        present = c.notna()
        full_window = present.astype(int).rolling(TREND_WINDOW + 1).sum() == TREND_WINDOW + 1
        eligible = present.astype(int).cumsum() >= MIN_HISTORY
        trend = c > sma(c, TREND_WINDOW)
        r3 = c / c.shift(3) - 1
        out.o[sym] = frame["open"].to_numpy(dtype="float64")
        out.h[sym] = h.to_numpy()
        out.lo[sym] = lo.to_numpy()
        out.c[sym] = c.to_numpy()
        out.sma5[sym] = sma(c, BOUNCE_WINDOW).to_numpy()
        out.atr14[sym] = a.to_numpy()
        out.rsi[sym] = rsi2(c).to_numpy()
        out.rel_drop[sym] = ((r3.to_numpy() - smh_r3) / (a / c).to_numpy()).astype("float64")
        out.shared[sym] = (
            present.to_numpy()
            & full_window.to_numpy()
            & eligible.to_numpy()
            & trend.to_numpy()
            & smh_trend
            & np.isfinite(a.to_numpy())
        )
    return out


@dataclass(frozen=True)
class Signal:
    symbol: str
    score: float  # lower ranks first
    stop: float
    close: float


def signals_at(p: Panel, variant: Variant, i: int) -> list[Signal]:
    """Signals at the close of session ``i``, ranked (spec: lowest score, then symbol)."""
    out: list[Signal] = []
    for sym in p.symbols:
        if not p.shared[sym][i]:
            continue
        if variant == "S1":
            score = float(p.rsi[sym][i])
            hit = score < RSI_MAX
        elif variant == "S2":
            score = float(p.rel_drop[sym][i])
            hit = score <= REL_DROP_MAX
        else:
            score, hit = 0.0, True
        if hit and math.isfinite(score):
            close = float(p.c[sym][i])
            out.append(Signal(sym, score, close - STOP_ATR * float(p.atr14[sym][i]), close))
    return sorted(out, key=lambda s: (s.score, s.symbol))


# ------------------------------------------------------------------ one position


@dataclass
class _Open:
    symbol: str
    signal_i: int
    entry_i: int
    fill: float
    stop: float
    shares: int
    risk: float  # revision 1.1: risk planned at the signal, C(t) - stop = 2 * ATR14(t)
    held: int = 0
    exit_pending: str | None = None


def _try_fill(p: Panel, sym: str, i: int, stop: float, slip: float) -> float | str:
    o = float(p.o[sym][i])
    if math.isnan(o) or math.isnan(float(p.c[sym][i])):
        return "missing_fill_bar"
    if o <= stop:
        return "gap_through_stop"
    fill = o * (1 + slip)
    if fill - stop < MIN_RISK_FRACTION * fill:
        return "stop_too_close"
    return fill


def _open_exit(p: Panel, pos: _Open, i: int) -> tuple[str, float] | None:
    """Exits at the open of ``i``: one decided at the previous close, or a gap through the stop."""
    o = float(p.o[pos.symbol][i])
    if math.isnan(o):
        return None  # no bar: a pending exit waits for the next open
    if pos.exit_pending is not None:
        return pos.exit_pending, o
    if i > pos.entry_i and o <= pos.stop:
        return "stop_gap", o
    return None


def _day(p: Panel, pos: _Open, i: int, *, stop_blocked: bool = False) -> tuple[str, float] | None:
    """The rest of held session ``i``: the stop, then the close-based bounce and time exits."""
    sym = pos.symbol
    c = float(p.c[sym][i])
    if math.isnan(c):
        return None
    if float(p.lo[sym][i]) <= pos.stop:
        if not stop_blocked:
            return "stop", pos.stop
        pos.exit_pending = "stop_pdt_deferred"  # a 4th day trade is refused; sell next open
        return None
    pos.held += 1
    if c > float(p.sma5[sym][i]):
        pos.exit_pending = "bounce"
    elif pos.held >= MAX_HELD:
        pos.exit_pending = "time"
    return None


def _last_close(p: Panel, sym: str, i: int, fallback: float) -> float:
    for j in range(i, -1, -1):
        v = float(p.c[sym][j])
        if not math.isnan(v):
            return v
    return fallback


# ------------------------------------------------------------------ signal study


@dataclass(frozen=True)
class StudyTrade:
    symbol: str
    signal_session: str
    entry_session: str
    exit_session: str
    reason: str
    r_multiple: float


def signal_study(
    p: Panel, variant: Variant, *, start: date, end: date, slippage_bps: float
) -> list[StudyTrade]:
    """Every signal in the window as an independent one-share trade, scored in R."""
    rng = p.index_range(start, end)
    if rng is None:
        return []
    i0, i1 = rng
    slip = slippage_bps / 10_000.0
    trades: list[StudyTrade] = []
    for i in range(i0, i1):  # the last session has no next open inside the window
        for sig in signals_at(p, variant, i):
            fill = _try_fill(p, sig.symbol, i + 1, sig.stop, slip)
            if isinstance(fill, str):
                continue
            pos = _Open(sig.symbol, i, i + 1, fill, sig.stop, 1, sig.close - sig.stop)
            done: tuple[str, float, int] | None = None
            for j in range(i + 1, i1 + 1):
                res = _open_exit(p, pos, j) if j > pos.entry_i else None
                res = res or _day(p, pos, j)
                if res is not None:
                    done = (res[0], res[1], j)
                    break
            if done is None:
                done = ("end_of_window", _last_close(p, sig.symbol, i1, fill), i1)
            reason, price, j = done
            r = (price * (1 - slip) - fill) / (sig.close - sig.stop)
            trades.append(
                StudyTrade(
                    sig.symbol,
                    p.sessions[i].isoformat(),
                    p.sessions[i + 1].isoformat(),
                    p.sessions[j].isoformat(),
                    reason,
                    round(r, 4),
                )
            )
    return trades


def study_stats(trades: list[StudyTrade], *, seed: int = 0) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    r = np.array([t.r_multiple for t in trades])
    boot = np.random.default_rng(seed).choice(r, size=(2000, r.size), replace=True).mean(axis=1)
    return {
        "trades": int(r.size),
        "mean_r": round(float(r.mean()), 4),
        "median_r": round(float(np.median(r)), 4),
        "win_rate": round(float((r > 0).mean()), 4),
        "mean_r_95ci": [round(float(np.quantile(boot, q)), 4) for q in (0.025, 0.975)],
        "exits": dict(Counter(t.reason for t in trades)),
    }


def select_variant(study: dict[str, dict[str, Any]]) -> tuple[str | None, list[str]]:
    """The spec's selection rule on development results at 20 bps; returns (variant, notes)."""
    base = study["baseline"].get("mean_r", -math.inf)
    notes: list[str] = []
    qualified: list[tuple[float, str]] = []
    for v in VARIANTS:
        s = study[v]
        n, m = s.get("trades", 0), s.get("mean_r", -math.inf)
        fails = [
            *(["fewer than 200 trades"] if n < SELECTION_MIN_TRADES else []),
            *(["mean R not above 0"] if not m > 0 else []),
            *(["mean R not above the baseline"] if not m > base else []),
        ]
        notes.append(f"{v}: {'qualifies' if not fails else '; '.join(fails)}")
        if not fails:
            qualified.append((m, v))
    if not qualified:
        return None, notes
    return max(qualified)[1], notes


def holdout_pass(study: dict[str, dict[str, Any]], variant: str, portfolio_net: float) -> list[str]:
    """Failed holdout conditions (an empty list means the spec's pass test is met)."""
    s, base = study[variant], study["baseline"].get("mean_r", -math.inf)
    m = s.get("mean_r", -math.inf)
    return [
        *(["fewer than 60 trades"] if s.get("trades", 0) < HOLDOUT_MIN_TRADES else []),
        *(["mean R not above 0"] if not m > 0 else []),
        *(["mean R not above the baseline"] if not m > base else []),
        *(["$5,000 unrestricted run not positive"] if not portfolio_net > 0 else []),
    ]


# ------------------------------------------------------------------ portfolio replay


@dataclass(frozen=True)
class Trade:
    symbol: str
    signal_session: str
    entry_session: str
    exit_session: str
    held_sessions: int
    shares: int
    entry: float
    stop: float
    exit: float
    reason: str
    pnl: float
    r_multiple: float


@dataclass
class _Pending:
    shares: int
    stop: float
    signal_i: int
    equity: float
    estimate: float


@dataclass
class RunResult:
    variant: str
    account: AccountModel
    slippage_bps: float
    start_equity: float
    trades: list[Trade] = field(default_factory=list)
    session_equity: list[tuple[str, float]] = field(default_factory=list)
    refusals: Counter[str] = field(default_factory=Counter)
    drawdown_halt_on: str | None = None

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"trades": len(self.trades)}
        if self.trades:
            pnl = np.array([t.pnl for t in self.trades])
            r = np.array([t.r_multiple for t in self.trades])
            out.update(
                win_rate=round(float((pnl > 0).mean()), 4),
                mean_r=round(float(r.mean()), 4),
                exits=dict(Counter(t.reason for t in self.trades)),
            )
        curve = [e for _, e in self.session_equity]
        end = curve[-1] if curve else self.start_equity
        out["end_equity"] = round(end, 2)
        out["net_pnl"] = round(end - self.start_equity, 2)
        out["net_return_pct"] = round((end / self.start_equity - 1) * 100, 2)
        peak, worst = -math.inf, 0.0
        for v in curve:
            peak = max(peak, v)
            worst = min(worst, v / peak - 1)
        out["max_drawdown_pct"] = round(worst * 100, 2)
        by_session: dict[str, float] = {}
        for t in self.trades:
            by_session[t.exit_session] = by_session.get(t.exit_session, 0.0) + t.pnl
        top = sorted(by_session.values(), reverse=True)[:5]
        out["net_without_best_5_sessions"] = round(sum(by_session.values()) - sum(top), 2)
        out["refusals"] = dict(self.refusals)
        out["drawdown_halt_on"] = self.drawdown_halt_on
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "account": self.account,
            "slippage_bps": self.slippage_bps,
            "start_equity": self.start_equity,
            "summary": self.summary(),
            "trades": [asdict(t) for t in self.trades],
            "session_equity": self.session_equity,
        }


def replay(  # noqa: PLR0912, PLR0915 - one pass over the sessions keeps the event order explicit
    p: Panel,
    *,
    variant: Variant,
    start: date,
    end: date,
    start_equity: float,
    limits: RiskLimits,
    account: AccountModel,
    slippage_bps: float,
) -> RunResult:
    if not (start_equity > 0 and math.isfinite(start_equity)):
        raise ValueError("start_equity must be positive")
    result = RunResult(variant, account, slippage_bps, start_equity)
    rng = p.index_range(start, end)
    if rng is None:
        return result
    i0, i1 = rng
    slip = slippage_bps / 10_000.0
    cash, unsettled = start_equity, 0.0
    positions: dict[str, _Open] = {}
    pending: dict[str, _Pending] = {}
    peak = prev_close = start_equity
    day_trades: deque[int] = deque(maxlen=PDT_WINDOW)
    dd_halted = False

    def book(pos: _Open, i: int, reason: str, price: float) -> None:
        nonlocal cash, unsettled
        positions.pop(pos.symbol)
        sale = price * (1 - slip)
        if account == "cash":
            unsettled += pos.shares * sale
        else:
            cash += pos.shares * sale
        pnl = (sale - pos.fill) * pos.shares
        result.trades.append(
            Trade(
                pos.symbol,
                p.sessions[pos.signal_i].isoformat(),
                p.sessions[pos.entry_i].isoformat(),
                p.sessions[i].isoformat(),
                pos.held,
                pos.shares,
                round(pos.fill, 4),
                round(pos.stop, 4),
                round(sale, 4),
                reason,
                round(pnl, 2),
                round((sale - pos.fill) / pos.risk, 4),
            )
        )

    for i in range(i0, i1 + 1):
        cash += unsettled  # T+1: the previous session's sales settle before this open
        unsettled = 0.0
        today_dt = 0
        recent_dt = sum(list(day_trades)[-(PDT_WINDOW - 1) :])
        # 1. the open: exits decided at the last close and gaps through stops, then entries
        for pos in list(positions.values()):
            res = _open_exit(p, pos, i)
            if res is not None:
                book(pos, i, *res)
        for sym, pend in sorted(pending.items()):
            fill = _try_fill(p, sym, i, pend.stop, slip)
            if isinstance(fill, str):
                result.refusals[fill] += 1
                continue
            shares = min(
                pend.shares,
                math.floor(pend.equity * limits.max_position_pct / fill),
                math.floor(cash / fill),
            )
            if shares < 1:
                result.refusals["fill_over_cap"] += 1
                continue
            cash -= shares * fill
            positions[sym] = _Open(
                sym, pend.signal_i, i, fill, pend.stop, shares, pend.estimate - pend.stop
            )
        pending = {}
        # 2. the session: stops (live from the entry session), then the close-based exits
        for pos in list(positions.values()):
            blocked = (
                account == "margin_pdt"
                and prev_close < PDT_EQUITY
                and i == pos.entry_i
                and recent_dt + today_dt >= PDT_MAX_DAY_TRADES
            )
            res = _day(p, pos, i, stop_blocked=blocked)
            if res is not None:
                book(pos, i, *res)
                if i == pos.entry_i:
                    today_dt += 1
        day_trades.append(today_dt)
        # 3. the close: equity, halts, then decisions for the next open
        equity = (
            cash
            + unsettled
            + sum(q.shares * _last_close(p, s, i, q.fill) for s, q in positions.items())
        )
        result.session_equity.append((p.sessions[i].isoformat(), round(equity, 2)))
        daily_halt = equity <= prev_close * (1 - limits.daily_loss_halt_pct)
        peak = max(peak, equity)
        if not dd_halted and equity <= peak * (1 - limits.drawdown_halt_pct):
            dd_halted = True
            result.drawdown_halt_on = p.sessions[i].isoformat()
        prev_close = equity
        if i == i1:
            break
        sigs = [s for s in signals_at(p, variant, i) if s.symbol not in positions]
        if dd_halted or daily_halt:
            result.refusals["drawdown_halt" if dd_halted else "daily_loss_halt"] += len(sigs)
            continue
        for sig in sigs:
            reason = _admit(
                sig,
                i,
                positions=positions,
                pending=pending,
                limits=limits,
                equity=equity,
                cash=cash,
                account=account,
                unsettled=unsettled,
            )
            if isinstance(reason, str):
                result.refusals[reason] += 1
            else:
                pending[sig.symbol] = reason
    for pos in list(positions.values()):  # the window ends: close at its last close
        book(pos, i1, "end_of_window", _last_close(p, pos.symbol, i1, pos.fill))
    if result.session_equity:
        result.session_equity[-1] = (result.session_equity[-1][0], round(cash + unsettled, 2))
    return result


def _admit(  # noqa: PLR0911 - one return per refusal reason
    sig: Signal,
    i: int,
    *,
    positions: dict[str, _Open],
    pending: dict[str, _Pending],
    limits: RiskLimits,
    equity: float,
    cash: float,
    account: AccountModel,
    unsettled: float,
) -> _Pending | str:
    """A pending entry sized at the close, or why it is refused.

    Positions with an exit decided for the next open still count against the limits and their
    sale is not counted as cash yet (conservative: both orders would go in at the same open).
    """
    if len(positions) + len(pending) >= limits.max_positions:
        return "max_positions"
    est = sig.close
    risk = est - sig.stop
    if risk <= 0 or risk < MIN_RISK_FRACTION * est:
        return "stop_too_close"
    by_risk = math.floor(equity * limits.max_risk_per_trade_pct / risk)
    by_position = math.floor(equity * limits.max_position_pct / est)
    if by_position < 1:
        return "position_cap"
    exposure = sum(q.shares * q.fill for q in positions.values()) + sum(
        q.shares * q.estimate for q in pending.values()
    )
    by_gross = math.floor((equity * limits.max_gross_exposure_pct - exposure) / est)
    reserved = sum(q.shares * q.estimate for q in pending.values())
    by_cash = math.floor((cash - reserved) / est)
    shares = min(by_risk, by_position, by_gross, by_cash)
    if shares >= 1:
        return _Pending(shares, sig.stop, i, equity, est)
    if by_cash < 1:
        return "unsettled_cash" if account == "cash" and unsettled > 0 else "insufficient_cash"
    if by_gross < 1:
        return "gross_exposure"
    return "risk_budget"


def smh_buy_and_hold(p: Panel, start: date, end: date) -> float | None:
    rng = p.index_range(start, end)
    if rng is None:
        return None
    i0, i1 = rng
    return round((float(p.smh_c[i1]) / float(p.smh_o[i0]) - 1) * 100, 2)
