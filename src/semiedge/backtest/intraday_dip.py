"""Backtest of ``intraday_dip_v1`` on cached 5-minute bars (spec
``docs/specifications/intraday_dip_v1.md``, #137).

Same-day only: a stock stretched below today's VWAP prints a reversal bar; buy the next open,
sell at the VWAP it was stretched from, at the day's low, or at the open of the last bar.

* The **signal study** takes every signal as its own one-share trade, scored in planned R.
* The **portfolio replay** runs a $5,000 account through the three account models with the
  ``5k`` profile's sizing, limits and halts.

Signals at bar ``k`` use only bars ``0..k`` of the session and daily bars of earlier sessions.
Each session is prepared from its own bars, so a development run never reads a holdout bar.
Nothing here places or simulates broker orders.
"""

from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Literal

import numpy as np
import pandas as pd

from semiedge.backtest.intraday import _daily_context, _grid, _prior_daily
from semiedge.backtest.intraday_signals import SessionBars, valid_until, vwap
from semiedge.config.models import RiskLimits
from semiedge.data.calendar import TradingCalendar
from semiedge.data.intraday import session_bar_starts

AccountModel = Literal["unrestricted", "margin_pdt", "cash"]
ACCOUNT_MODELS: tuple[AccountModel, ...] = ("unrestricted", "margin_pdt", "cash")
Variant = Literal["V1", "V2", "baseline"]
VARIANTS: tuple[Variant, ...] = ("V1", "V2")
SLIPPAGES = (10.0, 20.0, 30.0)

# Fixed by the spec before any result (ground rule 3).
DEVELOPMENT = (date(2024, 12, 16), date(2025, 12, 31))
HOLDOUT = (date(2026, 1, 2), date(2026, 11, 13))
FIRST_DECISION = 3  # 09:45 bar
LAST_DECISION = 71  # 15:30 bar on a full day
STRETCH_MIN = 0.5
REL_MAX = -0.5
MIN_RISK_FRACTION = 0.0005
PDT_EQUITY = 25_000.0
PDT_MAX_DAY_TRADES = 3
PDT_WINDOW = 5
SELECTION_SLIPPAGE = 20.0
SELECTION_MIN_TRADES = 300
HOLDOUT_MIN_TRADES = 100


# ------------------------------------------------------------------ signals


@dataclass(frozen=True)
class Signal:
    symbol: str
    k: int
    stop: float  # LOD_k
    target: float  # VWAP_k
    r0: float  # C_k - LOD_k
    close: float
    score: float  # lower ranks first


@dataclass
class DaySession:
    session: date
    n: int
    bars: dict[str, SessionBars]
    signals: dict[str, dict[int, list[Signal]]]  # variant -> k -> signals (state-free)

    def at(self, variant: str, k: int) -> list[Signal]:
        return self.signals.get(variant, {}).get(k, [])


def _without_empty_bars(b: SessionBars) -> SessionBars:
    """A bar with zero volume counts as missing (spec, "Data")."""
    empty = b.v == 0
    if not empty.any():
        return b
    out = SessionBars(b.o.copy(), b.h.copy(), b.lo.copy(), b.c.copy(), b.v.copy())
    for arr in (out.o, out.h, out.lo, out.c, out.v):
        arr[empty] = np.nan
    return out


def session_signals(
    b: SessionBars, smh: SessionBars, atr_d1: float | None
) -> dict[str, dict[int, list[Signal]]]:
    """Every bar's signals for one symbol, by variant (positions are applied by the replay)."""
    out: dict[str, dict[int, list[Signal]]] = {"V1": {}, "V2": {}, "baseline": {}}
    if atr_d1 is None or not atr_d1 > 0 or b.n == 0 or math.isnan(float(b.o[0])):
        return out
    vw = vwap(b)
    last = min(valid_until(b), LAST_DECISION, b.n - 7)
    lod = np.minimum.accumulate(np.where(np.isnan(b.lo), np.inf, b.lo))
    o0, smh_o0 = float(b.o[0]), float(smh.o[0])
    unit = atr_d1 / o0
    for k in range(FIRST_DECISION, last + 1):
        c = float(b.c[k])
        if not (c > float(b.o[k]) and c > float(b.h[k - 1]) and c < float(vw[k])):
            continue
        stop, target = float(lod[k]), float(vw[k])
        r0 = c - stop
        if target - c < r0 or r0 < MIN_RISK_FRACTION * c:
            continue
        stretch = (float(vw[k - 1]) - float(b.c[k - 1])) / atr_d1
        smh_prev = float(smh.c[k - 1])
        rel = (
            ((float(b.c[k - 1]) / o0 - 1) - (smh_prev / smh_o0 - 1)) / unit
            if math.isfinite(smh_prev) and math.isfinite(smh_o0)
            else math.nan
        )
        base = Signal("", k, stop, target, r0, c, -stretch)
        out["baseline"].setdefault(k, []).append(base)
        if stretch >= STRETCH_MIN:
            out["V1"].setdefault(k, []).append(base)
        if rel <= REL_MAX:
            v2 = Signal("", k, stop, target, r0, c, rel)
            out["V2"].setdefault(k, []).append(v2)
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
    ctx = {s: _daily_context(daily.get(s)) for s in tradable}
    out: list[DaySession] = []
    for session in calendar.sessions_in_range(start, end):
        starts = session_bar_starts(calendar, session)
        smh = _without_empty_bars(_grid(intraday.get(benchmark), starts))
        bars: dict[str, SessionBars] = {}
        sigs: dict[str, dict[int, list[Signal]]] = {"V1": {}, "V2": {}, "baseline": {}}
        for sym in tradable:
            b = _without_empty_bars(_grid(intraday.get(sym), starts))
            bars[sym] = b
            _, atr_d1 = _prior_daily(ctx[sym], session)
            for variant, by_k in session_signals(b, smh, atr_d1).items():
                for k, found in by_k.items():
                    sigs[variant].setdefault(k, []).extend(
                        Signal(sym, s.k, s.stop, s.target, s.r0, s.close, s.score) for s in found
                    )
        for by_k in sigs.values():
            for found in by_k.values():
                found.sort(key=lambda s: (s.score, s.symbol))
        out.append(DaySession(session, len(starts), bars, sigs))
    return out


# ------------------------------------------------------------------ one position


@dataclass
class _Open:
    symbol: str
    entry_k: int
    fill: float
    stop: float
    target: float
    r0: float
    shares: int


def _try_fill(b: SessionBars, k: int, sig: Signal, slip: float) -> float | str:
    o = float(b.o[k])
    if math.isnan(o):
        return "missing_fill_bar"
    if o <= sig.stop:
        return "gap_through_stop"
    if o >= sig.target:
        return "opened_at_target"
    return o * (1 + slip)


def _bar_exit(b: SessionBars, pos: _Open, k: int, n: int) -> tuple[str, float] | None:  # noqa: PLR0911
    """The exit on bar ``k``, if any: flatten, gaps, stop (wins a tie), then target."""
    o = float(b.o[k])
    if math.isnan(o):
        return None
    if k == n - 1:
        return "flat_1555", o
    if k > pos.entry_k:
        if o <= pos.stop:
            return "stop_gap", o
        if o >= pos.target:
            return "target_gap", o
    if float(b.lo[k]) <= pos.stop:
        return "stop", pos.stop
    if float(b.h[k]) >= pos.target:
        return "target", pos.target
    return None


def _last_close(b: SessionBars, k: int, fallback: float) -> float:
    for j in range(k, -1, -1):
        v = float(b.c[j])
        if not math.isnan(v):
            return v
    return fallback


# ------------------------------------------------------------------ signal study


@dataclass(frozen=True)
class StudyTrade:
    symbol: str
    session: str
    signal_bar: int
    exit_bar: int
    reason: str
    r_multiple: float


def signal_study(
    sessions: list[DaySession], variant: Variant, *, slippage_bps: float
) -> list[StudyTrade]:
    slip = slippage_bps / 10_000.0
    trades: list[StudyTrade] = []
    for ds in sessions:
        for k, found in sorted(ds.signals.get(variant, {}).items()):
            for sig in found:
                b = ds.bars[sig.symbol]
                fill = _try_fill(b, k + 1, sig, slip)
                if isinstance(fill, str):
                    continue
                pos = _Open(sig.symbol, k + 1, fill, sig.stop, sig.target, sig.r0, 1)
                done: tuple[str, float, int] | None = None
                for j in range(k + 1, ds.n):
                    res = _bar_exit(b, pos, j, ds.n)
                    if res is not None:
                        done = (res[0], res[1], j)
                        break
                if done is None:  # the last bar is missing: its last close stands in
                    done = ("flat_missing_bar", _last_close(b, ds.n - 1, fill), ds.n - 1)
                reason, price, j = done
                r = (price * (1 - slip) - fill) / sig.r0
                trades.append(
                    StudyTrade(sig.symbol, ds.session.isoformat(), k, j, reason, round(r, 4))
                )
    return trades


def study_stats(trades: list[StudyTrade], *, seed: int = 0) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    r = np.array([t.r_multiple for t in trades])
    boot = np.random.default_rng(seed).choice(r, size=(2000, r.size), replace=True).mean(axis=1)
    trimmed = np.sort(r)[:-5] if r.size > 5 else np.array([])
    return {
        "trades": int(r.size),
        "mean_r": round(float(r.mean()), 4),
        "median_r": round(float(np.median(r)), 4),
        "mean_r_without_top5": round(float(trimmed.mean()), 4) if trimmed.size else None,
        "win_rate": round(float((r > 0).mean()), 4),
        "mean_r_95ci": [round(float(np.quantile(boot, q)), 4) for q in (0.025, 0.975)],
        "exits": dict(Counter(t.reason for t in trades)),
    }


def _gate_failures(s: dict[str, Any], base: float, min_trades: int) -> list[str]:
    m = s.get("mean_r", -math.inf)
    trimmed = s.get("mean_r_without_top5")
    return [
        *([f"fewer than {min_trades} trades"] if s.get("trades", 0) < min_trades else []),
        *(["mean R not above 0"] if not m > 0 else []),
        *(["mean R not above the baseline"] if not m > base else []),
        *(["mean R without the top five trades not above 0"] if not (trimmed or 0) > 0 else []),
    ]


def select_variant(study: dict[str, dict[str, Any]]) -> tuple[str | None, list[str]]:
    """The spec's selection rule on development results at 20 bps."""
    base = study["baseline"].get("mean_r", -math.inf)
    notes, qualified = [], []
    for v in VARIANTS:
        fails = _gate_failures(study[v], base, SELECTION_MIN_TRADES)
        notes.append(f"{v}: {'qualifies' if not fails else '; '.join(fails)}")
        if not fails:
            qualified.append((study[v]["mean_r"], v))
    return (max(qualified)[1] if qualified else None), notes


def holdout_pass(study: dict[str, dict[str, Any]], variant: str, portfolio_net: float) -> list[str]:
    base = study["baseline"].get("mean_r", -math.inf)
    fails = _gate_failures(study[variant], base, HOLDOUT_MIN_TRADES)
    return [*fails, *(["$5,000 unrestricted run not positive"] if not portfolio_net > 0 else [])]


# ------------------------------------------------------------------ portfolio replay


@dataclass(frozen=True)
class Trade:
    symbol: str
    session: str
    entry_bar: int
    exit_bar: int
    shares: int
    entry: float
    stop: float
    target: float
    exit: float
    reason: str
    pnl: float
    r_multiple: float


@dataclass
class _Pending:
    sig: Signal
    shares: int
    equity: float


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
                trades_per_session=round(len(self.trades) / max(len(self.session_equity), 1), 2),
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
            by_session[t.session] = by_session.get(t.session, 0.0) + t.pnl
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


def replay(  # noqa: PLR0912, PLR0915 - one pass over the bars keeps the event order explicit
    sessions: list[DaySession],
    *,
    variant: Variant,
    start_equity: float,
    limits: RiskLimits,
    account: AccountModel,
    slippage_bps: float,
) -> RunResult:
    if not (start_equity > 0 and math.isfinite(start_equity)):
        raise ValueError("start_equity must be positive")
    slip = slippage_bps / 10_000.0
    result = RunResult(variant, account, slippage_bps, start_equity)
    cash, unsettled = start_equity, 0.0
    peak = start_equity
    day_trades: deque[int] = deque(maxlen=PDT_WINDOW)
    dd_halted = False

    for ds in sessions:
        cash += unsettled  # T+1: the previous session's sales settle before this open
        unsettled = 0.0
        positions: dict[str, _Open] = {}
        pending: dict[str, _Pending] = {}
        last_close: dict[str, float] = {}
        sod_equity = cash
        today_trades = 0
        daily_halted = False
        recent = sum(list(day_trades)[-(PDT_WINDOW - 1) :])
        n = ds.n

        def book(
            pos: _Open,
            k: int,
            reason: str,
            price: float,
            *,
            positions: dict[str, _Open] = positions,
            ds: DaySession = ds,
        ) -> None:
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
                    ds.session.isoformat(),
                    pos.entry_k,
                    k,
                    pos.shares,
                    round(pos.fill, 4),
                    round(pos.stop, 4),
                    round(pos.target, 4),
                    round(sale, 4),
                    reason,
                    round(pnl, 2),
                    round((sale - pos.fill) / pos.r0, 4),
                )
            )

        for k in range(n):
            # 1. entries decided at the last close fill at this open
            for sym, pend in sorted(pending.items()):
                b = ds.bars[sym]
                fill = _try_fill(b, k, pend.sig, slip)
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
                s = pend.sig
                positions[sym] = _Open(sym, k, fill, s.stop, s.target, s.r0, shares)
                today_trades += 1
            pending = {}
            # 2. exits on this bar
            for pos in list(positions.values()):
                b = ds.bars[pos.symbol]
                res = _bar_exit(b, pos, k, n)
                if res is None and k == n - 1:
                    res = ("flat_missing_bar", last_close.get(pos.symbol, pos.fill))
                if res is not None:
                    book(pos, k, *res)
            for sym, b in ds.bars.items():
                if not math.isnan(float(b.c[k])):
                    last_close[sym] = float(b.c[k])
            equity = (
                cash
                + unsettled
                + sum(p.shares * last_close.get(s, p.fill) for s, p in positions.items())
            )
            # 3. decisions at the close of bar k
            found = ds.at(variant, k)
            if not found or dd_halted:
                continue
            if equity <= sod_equity * (1 - limits.daily_loss_halt_pct):
                daily_halted = True
            sigs = [s for s in found if s.symbol not in positions]
            if daily_halted:
                result.refusals["daily_loss_halt"] += len(sigs)
                continue
            for sig in sigs:
                reason = _admit(
                    sig,
                    positions=positions,
                    pending=pending,
                    limits=limits,
                    account=account,
                    equity=equity,
                    cash=cash,
                    unsettled=unsettled,
                    pdt_used=recent + today_trades + len(pending),
                    pdt_applies=account == "margin_pdt" and sod_equity < PDT_EQUITY,
                )
                if isinstance(reason, str):
                    result.refusals[reason] += 1
                else:
                    pending[sig.symbol] = reason
        day_trades.append(today_trades)
        close_equity = cash + unsettled
        result.session_equity.append((ds.session.isoformat(), round(close_equity, 2)))
        peak = max(peak, close_equity)
        if not dd_halted and close_equity <= peak * (1 - limits.drawdown_halt_pct):
            dd_halted = True
            result.drawdown_halt_on = ds.session.isoformat()
    return result


def _admit(  # noqa: PLR0911 - one return per refusal reason
    sig: Signal,
    *,
    positions: dict[str, _Open],
    pending: dict[str, _Pending],
    limits: RiskLimits,
    account: AccountModel,
    equity: float,
    cash: float,
    unsettled: float,
    pdt_used: int,
    pdt_applies: bool,
) -> _Pending | str:
    if len(positions) + len(pending) >= limits.max_positions:
        return "max_positions"
    if pdt_applies and pdt_used >= PDT_MAX_DAY_TRADES:
        return "pdt_limit"
    est = sig.close
    by_risk = math.floor(equity * limits.max_risk_per_trade_pct / sig.r0)
    by_position = math.floor(equity * limits.max_position_pct / est)
    if by_position < 1:
        return "position_cap"
    exposure = sum(p.shares * p.fill for p in positions.values()) + sum(
        p.shares * p.sig.close for p in pending.values()
    )
    by_gross = math.floor((equity * limits.max_gross_exposure_pct - exposure) / est)
    reserved = sum(p.shares * p.sig.close for p in pending.values())
    by_cash = math.floor((cash - reserved) / est)
    shares = min(by_risk, by_position, by_gross, by_cash)
    if shares >= 1:
        return _Pending(sig, shares, equity)
    if by_cash < 1:
        return "unsettled_cash" if account == "cash" and unsettled > 0 else "insufficient_cash"
    if by_gross < 1:
        return "gross_exposure"
    return "risk_budget"


def smh_change_pct(
    intraday: dict[str, pd.DataFrame], benchmark: str, start: date, end: date
) -> float | None:
    frame = intraday.get(benchmark)
    if frame is None or frame.empty:
        return None
    ts = pd.DatetimeIndex(frame.index).tz_convert("America/New_York")
    days = np.array([t.date() for t in ts])
    window = frame.loc[(days >= start) & (days <= end)]
    if window.empty:
        return None
    return round((float(window["close"].iloc[-1]) / float(window["open"].iloc[0]) - 1) * 100, 2)
