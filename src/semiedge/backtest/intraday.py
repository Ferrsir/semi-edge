"""Bar-by-bar replay of ``intraday_v1`` on cached 5-minute bars (#130).

Signals come from :mod:`semiedge.backtest.intraday_signals` and are computed once per session.
This module applies everything that depends on state: one position per symbol, one trigger per
setup per symbol per day, fills at the next bar's open with slippage, stop/target brackets (a bar
that touches both counts as a stop), the 15:55 flatten, the risk limits and halts, and the account
models (``unrestricted``, ``margin_pdt``, ``cash``) from the spec's "Accounts, profiles and run
modes" section. Nothing here places or simulates orders against a broker.
"""

from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Literal

import numpy as np
import pandas as pd

from semiedge.backtest.intraday_signals import (
    SETUPS,
    SessionBars,
    SessionContext,
    Trigger,
    market_ok,
    setup_a_day_eligible,
    triggers_at,
    vwap,
)
from semiedge.config.models import RiskLimits
from semiedge.data.calendar import TradingCalendar
from semiedge.data.intraday import session_bar_starts
from semiedge.indicators.core import atr

AccountModel = Literal["unrestricted", "margin_pdt", "cash"]
ACCOUNT_MODELS: tuple[AccountModel, ...] = ("unrestricted", "margin_pdt", "cash")
PDT_EQUITY = 25_000.0
PDT_MAX_DAY_TRADES = 3  # a 4th within five sessions is refused
PDT_WINDOW = 5
MIN_RISK_FRACTION = 0.0005
TARGET_R = 2.0
HISTORY_LOOKBACK = 40  # sessions loaded before ``start`` for the 20-session averages
NY = "America/New_York"


# ------------------------------------------------------------------ prepared data


@dataclass
class PreparedSession:
    session: date
    n_bars: int
    smh: SessionBars
    smh_vwap: np.ndarray
    triggers: dict[int, list[tuple[str, Trigger]]]  # k -> (symbol, trigger), state-free
    bars: dict[str, SessionBars]
    gap_symbols: tuple[str, ...]


def _grid(frame: pd.DataFrame | None, starts: pd.DatetimeIndex) -> SessionBars:
    if frame is None:
        nan = np.full(len(starts), np.nan)
        return SessionBars(nan, nan.copy(), nan.copy(), nan.copy(), nan.copy())
    day = frame.reindex(starts)
    return SessionBars(
        day["open"].to_numpy(dtype="float64"),
        day["high"].to_numpy(dtype="float64"),
        day["low"].to_numpy(dtype="float64"),
        day["close"].to_numpy(dtype="float64"),
        day["volume"].to_numpy(dtype="float64"),
    )


def _daily_context(daily: pd.DataFrame | None) -> pd.DataFrame | None:
    """Per daily session: that day's high and Wilder ATR(14), for lookup by the next session."""
    if daily is None or daily.empty:
        return None
    out = pd.DataFrame(
        {
            "high": daily["high"].astype(float),
            "atr": atr(
                daily["high"].astype(float),
                daily["low"].astype(float),
                daily["close"].astype(float),
            ),
            "count": np.arange(1, len(daily) + 1),
        },
        index=pd.DatetimeIndex(daily.index),
    )
    return out


def _prior_daily(ctx: pd.DataFrame | None, session: date) -> tuple[float | None, float | None]:
    """``PDH`` and ``ATR_d1`` from daily bars strictly before ``session`` (15 bars required)."""
    if ctx is None:
        return None, None
    earlier = ctx.loc[ctx.index < pd.Timestamp(session)]
    if earlier.empty or int(earlier["count"].iloc[-1]) < 15:
        return None, None
    high, value = float(earlier["high"].iloc[-1]), float(earlier["atr"].iloc[-1])
    return (high if math.isfinite(high) else None), (value if math.isfinite(value) else None)


def prepare_sessions(
    intraday: dict[str, pd.DataFrame],
    daily: dict[str, pd.DataFrame],
    *,
    tradable: tuple[str, ...],
    benchmark: str,
    start: date,
    end: date,
    calendar: TradingCalendar,
) -> list[PreparedSession]:
    """Grid every session, compute the state-free triggers once, and keep rolling history."""
    if start > end:
        raise ValueError("start must not exceed end")
    test = calendar.sessions_in_range(start, end)
    if not test:
        return []
    history = calendar.sessions_window(calendar.previous_session(test[0]), HISTORY_LOOKBACK)
    sessions = [*history, *test]
    daily_ctx = {s: _daily_context(daily.get(s)) for s in tradable}
    rolling: dict[str, SessionContext] = {s: SessionContext() for s in tradable}
    prepared: list[PreparedSession] = []
    test_set = set(test)
    for session in sessions:
        starts = session_bar_starts(calendar, session)
        bars = {s: _grid(intraday.get(s), starts) for s in tradable}
        smh = _grid(intraday.get(benchmark), starts)
        if session in test_set:
            smh_vw = vwap(smh)
            by_k: dict[int, list[tuple[str, Trigger]]] = {}
            for symbol in tradable:
                ctx = rolling[symbol]
                ctx.pdh, ctx.atr_d1 = _prior_daily(daily_ctx[symbol], session)
                b = bars[symbol]
                vw = vwap(b)
                a_ok = setup_a_day_eligible(b, ctx)
                for k in range(2, len(starts) - 6):
                    for trig in triggers_at(b, vw, ctx, k, a_eligible=a_ok):
                        by_k.setdefault(k, []).append((symbol, trig))
            prepared.append(
                PreparedSession(
                    session,
                    len(starts),
                    smh,
                    smh_vw,
                    by_k,
                    bars,
                    tuple(s for s in tradable if not bars[s].complete),
                )
            )
        for symbol in tradable:  # this session becomes history for the next one
            b = bars[symbol]
            ctx = rolling[symbol]
            ctx.prior_slots.append(b.v.copy())
            ctx.prior_orv.append(float(np.sum(b.v[:3])) if b.present[:3].all() else math.nan)
            del ctx.prior_slots[:-40], ctx.prior_orv[:-40]
    return prepared


# ------------------------------------------------------------------ replay


@dataclass
class _Pending:
    setup: str
    shares: int
    stop: float
    k_fill: int
    estimate: float
    equity: float


@dataclass
class _Position:
    setup: str
    shares: int
    entry: float
    stop: float
    target: float
    risk: float
    entry_k: int
    session: date


@dataclass(frozen=True)
class Trade:
    symbol: str
    setup: str
    session: str
    entry_bar: int
    exit_bar: int
    shares: int
    entry: float
    exit: float
    stop: float
    target: float
    reason: str
    pnl: float
    r_multiple: float


@dataclass
class RunResult:
    account: AccountModel
    slippage_bps: float
    start_equity: float
    trades: list[Trade] = field(default_factory=list)
    session_equity: list[tuple[str, float]] = field(default_factory=list)
    refusals: Counter[str] = field(default_factory=Counter)
    drawdown_halt_on: str | None = None

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, group in (
            ("all", self.trades),
            *((s, [t for t in self.trades if t.setup == s]) for s in SETUPS),
        ):
            out[name] = _stats(group, self.start_equity)
        curve = [e for _, e in self.session_equity]
        out["max_drawdown_pct"] = _max_drawdown(curve)
        out["end_equity"] = curve[-1] if curve else self.start_equity
        daily = _session_pnl(self.trades)
        top = sorted(daily.values(), reverse=True)[:5]
        out["net_without_best_5_sessions"] = round(sum(daily.values()) - sum(top), 2)
        out["refusals"] = dict(self.refusals)
        out["drawdown_halt_on"] = self.drawdown_halt_on
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "slippage_bps": self.slippage_bps,
            "start_equity": self.start_equity,
            "summary": self.summary(),
            "trades": [asdict(t) for t in self.trades],
            "session_equity": self.session_equity,
        }


def _stats(trades: list[Trade], start_equity: float) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    pnl = np.array([t.pnl for t in trades])
    r = np.array([t.r_multiple for t in trades])
    gains, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    return {
        "trades": len(trades),
        "win_rate": round(float((pnl > 0).mean()), 4),
        "expectancy_r": round(float(r.mean()), 4),
        "avg_pnl": round(float(pnl.mean()), 2),
        "net_pnl": round(float(pnl.sum()), 2),
        "net_return_pct": round(float(pnl.sum()) / start_equity * 100, 2),
        "profit_factor": round(float(gains / losses), 3) if losses > 0 else None,
        "exits": dict(Counter(t.reason for t in trades)),
    }


def _max_drawdown(curve: list[float]) -> float:
    peak, worst = -math.inf, 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return round(worst * 100, 2)


def _session_pnl(trades: list[Trade]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in trades:
        out[t.session] = out.get(t.session, 0.0) + t.pnl
    return out


def replay(  # noqa: PLR0912, PLR0915 - one pass over the bar loop keeps the fill order explicit
    prepared: list[PreparedSession],
    *,
    start_equity: float,
    limits: RiskLimits,
    account: AccountModel,
    slippage_bps: float,
) -> RunResult:
    if start_equity <= 0 or not math.isfinite(start_equity):
        raise ValueError("start_equity must be positive")
    slip = slippage_bps / 10_000.0
    result = RunResult(account, slippage_bps, start_equity)
    cash = start_equity  # settled cash in the cash model; all cash otherwise
    unsettled = 0.0
    peak_close = start_equity
    day_trades: deque[int] = deque(maxlen=PDT_WINDOW)
    dd_halted = False
    order = {s: i for i, s in enumerate(SETUPS)}

    for ps in prepared:
        cash += unsettled  # T+1: yesterday's sale proceeds settle at the start of the session
        unsettled = 0.0
        positions: dict[str, _Position] = {}
        pending: dict[str, _Pending] = {}
        fired: set[tuple[str, str]] = set()
        last_close: dict[str, float] = {}
        sod_equity = cash
        today_trades = 0
        daily_halted = False
        n = ps.n_bars

        def _book_exit(  # defaults bind this session's state (ruff B023)
            symbol: str,
            k: int,
            price: float,
            reason: str,
            *,
            positions: dict[str, _Position] = positions,
            ps: PreparedSession = ps,
        ) -> None:
            nonlocal cash, unsettled
            pos = positions.pop(symbol)
            proceeds = pos.shares * price
            if account == "cash":
                unsettled += proceeds
            else:
                cash += proceeds
            pnl = (price - pos.entry) * pos.shares
            result.trades.append(
                Trade(
                    symbol,
                    pos.setup,
                    ps.session.isoformat(),
                    pos.entry_k,
                    k,
                    pos.shares,
                    round(pos.entry, 4),
                    round(price, 4),
                    round(pos.stop, 4),
                    round(pos.target, 4),
                    reason,
                    round(pnl, 2),
                    round(pnl / (pos.risk * pos.shares), 4),
                )
            )

        for k in range(n):
            # 1. entries scheduled for this bar fill at its open, plus slippage
            for symbol in [s for s, p in pending.items() if p.k_fill == k]:
                p = pending.pop(symbol)
                bar = ps.bars[symbol]
                if not bar.present[k]:
                    result.refusals["missing_fill_bar"] += 1
                    continue
                fill = float(bar.o[k]) * (1 + slip)
                risk = fill - p.stop
                if risk <= 0 or risk < MIN_RISK_FRACTION * fill:
                    result.refusals["gap_through_stop" if risk <= 0 else "stop_too_close"] += 1
                    continue
                shares = min(
                    p.shares,
                    # Recheck the reserved quantity against the actual slipped fill:
                    # a next-open gap can widen entry-to-stop risk since admission.
                    math.floor(p.equity * limits.max_risk_per_trade_pct / risk),
                    math.floor(p.equity * limits.max_position_pct / fill),
                    math.floor(cash / fill),
                )
                if shares < 1:
                    result.refusals["fill_over_cap"] += 1
                    continue
                cash -= shares * fill
                positions[symbol] = _Position(
                    p.setup, shares, fill, p.stop, fill + TARGET_R * risk, risk, k, ps.session
                )
                today_trades += 1
            # 2. exits: flatten on the last bar, otherwise stop first, then target
            for symbol in list(positions):
                pos = positions[symbol]
                bar = ps.bars[symbol]
                if k == n - 1:
                    price = float(bar.o[k]) if bar.present[k] else last_close.get(symbol, pos.entry)
                    _book_exit(
                        symbol,
                        k,
                        price * (1 - slip),
                        "flat_1555" if bar.present[k] else "flat_missing_bar",
                    )
                    continue
                if not bar.present[k]:
                    continue
                o, lo, h = float(bar.o[k]), float(bar.lo[k]), float(bar.h[k])
                if o <= pos.stop:
                    _book_exit(symbol, k, o * (1 - slip), "stop_gap")
                elif lo <= pos.stop:
                    _book_exit(symbol, k, pos.stop * (1 - slip), "stop")
                elif h >= pos.target:
                    _book_exit(symbol, k, pos.target * (1 - slip), "target")
            for symbol, bar in ps.bars.items():
                if bar.present[k]:
                    last_close[symbol] = float(bar.c[k])
            equity = (
                cash
                + unsettled
                + sum(p.shares * last_close.get(s, p.entry) for s, p in positions.items())
            )
            # 3. decisions at the close of bar k; entries fill at the open of k + 1
            if k >= n - 6 or dd_halted:
                continue
            if equity <= sod_equity * (1 - limits.daily_loss_halt_pct):
                daily_halted = True
            if equity <= peak_close * (1 - limits.drawdown_halt_pct):
                dd_halted = True
                result.drawdown_halt_on = ps.session.isoformat()
                continue
            candidates = [
                (sym, trig)
                for sym, trig in ps.triggers.get(k, [])
                if sym not in positions and sym not in pending and (sym, trig.setup) not in fired
            ]
            if not candidates or not market_ok(ps.smh, ps.smh_vwap, k):
                continue  # shared condition 1 fails: no setup fires, so nothing is used up
            for sym, trig in candidates:
                fired.add((sym, trig.setup))  # the trigger happened, whether or not it is taken
            if daily_halted:
                result.refusals["daily_loss_halt"] += len(candidates)
                continue
            taken: set[str] = set()
            for sym, trig in sorted(
                candidates, key=lambda c: (order[c[1].setup], -c[1].rank_volume, c[0])
            ):
                if sym in taken:
                    continue  # one position per symbol: the higher-ranked setup won this bar
                taken.add(sym)
                reason = _admit(
                    sym,
                    trig,
                    ps=ps,
                    k=k,
                    positions=positions,
                    pending=pending,
                    limits=limits,
                    account=account,
                    equity=equity,
                    cash=cash,
                    unsettled=unsettled,
                    sod_equity=sod_equity,
                    day_trades=day_trades,
                    today_trades=today_trades,
                )
                if isinstance(reason, str):
                    result.refusals[reason] += 1
                else:
                    pending[sym] = reason
        result.refusals["unfilled_at_close"] += len(pending)
        day_trades.append(today_trades)
        close_equity = cash + unsettled
        peak_close = max(peak_close, close_equity)
        result.session_equity.append((ps.session.isoformat(), round(close_equity, 2)))
    return result


def _admit(  # noqa: PLR0911 - one return per refusal reason
    sym: str,
    trig: Trigger,
    *,
    ps: PreparedSession,
    k: int,
    positions: dict[str, _Position],
    pending: dict[str, _Pending],
    limits: RiskLimits,
    account: AccountModel,
    equity: float,
    cash: float,
    unsettled: float,
    sod_equity: float,
    day_trades: deque[int],
    today_trades: int,
) -> _Pending | str:
    """A pending entry, or the reason it is refused (the spec's sizing and account rules)."""
    if len(positions) + len(pending) >= limits.max_positions:
        return "max_positions"
    if account == "margin_pdt" and sod_equity < PDT_EQUITY:
        recent = sum(list(day_trades)[-(PDT_WINDOW - 1) :]) + today_trades + len(pending)
        if recent >= PDT_MAX_DAY_TRADES:
            return "pdt_limit"
    estimate = float(ps.bars[sym].c[k])
    risk = estimate - trig.stop
    if risk <= 0 or risk < MIN_RISK_FRACTION * estimate:
        return "stop_too_close"
    by_risk = math.floor(equity * limits.max_risk_per_trade_pct / risk)
    by_position = math.floor(equity * limits.max_position_pct / estimate)
    if by_position < 1:
        return "position_cap"
    exposure = sum(p.shares * p.entry for p in positions.values()) + sum(
        p.shares * p.estimate for p in pending.values()
    )
    by_gross = math.floor((equity * limits.max_gross_exposure_pct - exposure) / estimate)
    reserved = sum(p.shares * p.estimate for p in pending.values())
    by_cash = math.floor((cash - reserved) / estimate)
    shares = min(by_risk, by_position, by_gross, by_cash)
    if shares >= 1:
        return _Pending(trig.setup, shares, trig.stop, k + 1, estimate, equity)
    if by_cash < 1:
        return "unsettled_cash" if account == "cash" and unsettled > 0 else "insufficient_cash"
    if by_gross < 1:
        return "gross_exposure"
    return "risk_budget"


def run_matrix(
    prepared: list[PreparedSession],
    *,
    start_equity: float,
    limits: RiskLimits,
    slippages: tuple[float, ...] = (10.0, 20.0, 30.0),
    accounts: tuple[AccountModel, ...] = ACCOUNT_MODELS,
) -> list[RunResult]:
    return [
        replay(prepared, start_equity=start_equity, limits=limits, account=a, slippage_bps=s)
        for a in accounts
        for s in slippages
    ]
