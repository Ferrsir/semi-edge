"""Pure §4 metrics, with 252 sessions/year and zero risk-free rate.

Returns include the first close versus pre-session initial equity. Sharpe uses
sample standard deviation; Sortino uses the RMS of negative returns (zeros
included). Drawdown duration includes recovery, or ends at the last session.
Turnover counts both filled legs, costs include commissions/fees, and holding
length uses Trade.held_sessions verbatim, including the exit day. Period returns
use the equity immediately before the first included session;
trades are attributed by entry session.
Undefined quantities carry reasons; no non-finite value can be a Metric.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from datetime import date
from statistics import fmean, stdev
from types import MappingProxyType

import pandas as pd

from semiedge.backtest.data import BacktestData
from semiedge.backtest.metrics_context import read_context, validate_series
from semiedge.backtest.results import BacktestResult, EquityPoint
from semiedge.config.models import MomentumV1Params
from semiedge.data.calendar import TradingCalendar
from semiedge.indicators import sma

ANNUAL_SESSIONS = 252
STRESS_PERIODS: Mapping[str, tuple[date, date]] = MappingProxyType(
    {
        "2018Q4": (date(2018, 10, 1), date(2018, 12, 31)),
        "2020Q1": (date(2020, 1, 2), date(2020, 3, 31)),
        "2022": (date(2022, 1, 3), date(2022, 12, 30)),
    }
)


@dataclass(frozen=True)
class Metric:
    value: float | None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.value is None:
            if not self.reason:
                raise ValueError("undefined metric requires a reason")
        elif not math.isfinite(self.value) or self.reason is not None:
            raise ValueError("metric must be finite and have no reason")


@dataclass(frozen=True)
class PerformanceMetrics:
    sessions: int
    net_return: Metric
    cagr: Metric
    max_drawdown: Metric
    max_drawdown_sessions: Metric
    sharpe: Metric
    sortino: Metric
    trade_count: int
    win_rate: Metric
    profit_factor: Metric
    expectancy: Metric
    expectancy_r: Metric
    avg_holding_sessions: Metric
    exposure: Metric
    turnover: Metric
    exit_reason_counts: Mapping[str, int]
    total_costs: float


@dataclass(frozen=True)
class PeriodMetrics:
    label: str
    start: date
    end: date
    metrics: PerformanceMetrics
    trade_count: int


def finite_metric(value: float) -> Metric:
    return Metric(value) if math.isfinite(value) else Metric(None, "non_finite_arithmetic")


def _annual_return(ratio: float, sessions: int) -> Metric:
    try:
        return finite_metric(math.expm1(math.log(ratio) * ANNUAL_SESSIONS / sessions))
    except (OverflowError, ValueError):
        return Metric(None, "non_finite_arithmetic")


def unavailable(reason: str, *, sessions: int = 0, trades: int = 0) -> PerformanceMetrics:
    values = {
        f.name: Metric(None, reason)
        for f in fields(PerformanceMetrics)
        if f.name not in {"sessions", "trade_count", "exit_reason_counts", "total_costs"}
    }
    return PerformanceMetrics(
        sessions=sessions, trade_count=trades, exit_reason_counts={}, total_costs=0.0, **values
    )


def daily_returns(result: BacktestResult) -> tuple[float, ...]:
    initial = result.initial_equity
    if initial is None or not math.isfinite(initial) or initial <= 0:
        raise ValueError("missing_or_invalid_initial_equity")
    previous = initial
    returns = []
    last: date | None = None
    for point in result.equity_curve:
        if (
            not math.isfinite(point.equity)
            or point.equity <= 0
            or not math.isfinite(point.gross_exposure)
            or point.gross_exposure < 0
            or not math.isfinite(point.cash)
        ):
            raise ValueError("invalid_equity_point")
        if not result.start <= point.session <= result.end or (last and point.session <= last):
            raise ValueError("invalid_equity_sessions")
        returns.append(point.equity / previous - 1)
        previous, last = point.equity, point.session
    if not all(math.isfinite(r) for r in returns):
        raise ValueError("non_finite_returns")
    return tuple(returns)


def drawdown_episodes(result: BacktestResult) -> tuple[tuple[int, int, int, float], ...]:
    """(peak index, trough index, recovery/last index, depth); initial peak is -1."""
    if result.initial_equity is None:
        return ()
    peak, peak_i, trough_i, depth = result.initial_equity, -1, -1, 0.0
    episodes = []
    for i, point in enumerate(result.equity_curve):
        if point.equity >= peak:
            if depth < 0:
                episodes.append((peak_i, trough_i, i, depth))
            peak, peak_i, depth = point.equity, i, 0.0
        else:
            dd = point.equity / peak - 1
            if dd < depth:
                depth, trough_i = dd, i
    if depth < 0:
        episodes.append((peak_i, trough_i, len(result.equity_curve) - 1, depth))
    return tuple(episodes)


def compute_metrics(result: BacktestResult) -> PerformanceMetrics:
    """Compute §4 definitions without I/O or inferred starting capital."""
    size, count = len(result.equity_curve), len(result.trades)
    if not size:
        return unavailable("no_sessions_in_period", trades=count)
    try:
        returns = daily_returns(result)
    except ValueError as exc:
        return unavailable(str(exc), sessions=size, trades=count)
    for trade in result.trades:
        if (
            not all(
                math.isfinite(x)
                for x in (
                    trade.net_pnl,
                    trade.r_multiple,
                    trade.entry_price,
                    trade.exit_price,
                    trade.costs,
                    trade.gross_pnl,
                    trade.entry_reference,
                    trade.stop,
                    trade.target,
                    trade.score,
                )
            )
            or type(trade.shares) is not int
            or trade.shares <= 0
            or type(trade.held_sessions) is not int
            or trade.held_sessions < 1
            or trade.costs < 0
            or trade.entry_price <= 0
            or trade.exit_price <= 0
            or trade.entry_session > trade.exit_session
        ):
            return unavailable("invalid_trade", sessions=size, trades=count)
    try:
        return _compute(result, returns)
    except (OverflowError, ValueError, ZeroDivisionError):
        return unavailable("non_finite_arithmetic", sessions=size, trades=count)


def _compute(result: BacktestResult, returns: Sequence[float]) -> PerformanceMetrics:
    size, count = len(returns), len(result.trades)
    initial = result.initial_equity
    assert initial is not None  # validated by daily_returns # noqa: S101
    ratio = result.equity_curve[-1].equity / initial
    mean = fmean(returns)
    sigma = stdev(returns) if size >= 2 else 0.0
    downside = math.sqrt(fmean(min(r, 0) ** 2 for r in returns))
    sharpe = (
        Metric(None, "fewer_than_2_returns")
        if size < 2
        else Metric(None, "zero_volatility")
        if zero_volatility(returns)
        else finite_metric(mean / sigma * math.sqrt(ANNUAL_SESSIONS))
    )
    sortino = (
        Metric(None, "no_downside_returns")
        if downside == 0
        else finite_metric(mean / downside * math.sqrt(ANNUAL_SESSIONS))
    )
    episodes = drawdown_episodes(result)
    winners = math.fsum(t.net_pnl for t in result.trades if t.net_pnl > 0)
    losers = -math.fsum(t.net_pnl for t in result.trades if t.net_pnl < 0)

    def average(values: Sequence[float]) -> Metric:
        return finite_metric(fmean(values)) if values else Metric(None, "no_trades")

    costs = math.fsum(t.costs for t in result.trades)
    if not math.isfinite(costs):
        raise ValueError("non_finite_costs")
    return PerformanceMetrics(
        size,
        finite_metric(ratio - 1),
        _annual_return(ratio, size),
        Metric(min((e[3] for e in episodes), default=0.0)),
        Metric(float(max((e[2] - e[0] for e in episodes), default=0))),
        sharpe,
        sortino,
        count,
        Metric(sum(t.net_pnl > 0 for t in result.trades) / count)
        if count
        else Metric(None, "no_trades"),
        finite_metric(winners / losers)
        if losers
        else Metric(None, "no_losing_trades" if count else "no_trades"),
        average([t.net_pnl for t in result.trades]),
        average([t.r_multiple for t in result.trades]),
        average([float(t.held_sessions) for t in result.trades]),
        finite_metric(fmean(p.gross_exposure / p.equity for p in result.equity_curve)),
        finite_metric(
            math.fsum((t.entry_price + t.exit_price) * t.shares for t in result.trades)
            / fmean(p.equity for p in result.equity_curve)
            * ANNUAL_SESSIONS
            / size
        ),
        dict(sorted(Counter(t.exit_reason for t in result.trades).items())),
        costs,
    )


def zero_volatility(returns: Sequence[float]) -> bool:
    return stdev(returns) <= 1e-12 * max(1.0, fmean(abs(r) for r in returns))


def period_result(result: BacktestResult, start: date, end: date) -> BacktestResult:
    """Slice inclusive sessions, retaining capital immediately before start."""
    if start > end:
        raise ValueError("invalid_period")
    previous = result.initial_equity
    for point in result.equity_curve:
        if point.session >= start:
            break
        previous = point.equity
    return replace(
        result,
        start=start,
        end=end,
        initial_equity=previous,
        equity_curve=tuple(p for p in result.equity_curve if start <= p.session <= end),
        trades=tuple(t for t in result.trades if start <= t.entry_session <= end),
    )


def period_breakdown(
    result: BacktestResult, *, periods: Mapping[str, tuple[date, date]]
) -> tuple[PeriodMetrics, ...]:
    rows = []
    for label, (start, end) in periods.items():
        subset = period_result(result, start, end)
        trades = subset.trades
        rows.append(PeriodMetrics(label, start, end, compute_metrics(subset), len(trades)))
    return tuple(rows)


def worst_drawdowns(result: BacktestResult, n: int = 3) -> tuple[PeriodMetrics, ...]:
    if type(n) is not int or n < 0:
        raise ValueError("n must be a nonnegative integer")
    daily_returns(result)  # refuse malformed series before finding episodes
    episodes = sorted(drawdown_episodes(result), key=lambda e: (e[3], e[0]))[:n]
    rows = []
    for i, (peak, trough, _, _) in enumerate(episodes, 1):
        # Start after the peak: the shared helper retains the peak as capital.
        start = result.equity_curve[peak + 1].session
        end = result.equity_curve[trough].session
        row = period_breakdown(result, periods={f"drawdown_{i}": (start, end)})[0]
        rows.append(row)
    return tuple(rows)


def selected_sessions(result: BacktestResult, sessions: set[date]) -> PerformanceMetrics:
    """Compound only selected daily returns, preserving daily exposure ratios."""
    returns = daily_returns(result)
    initial = result.initial_equity
    assert initial is not None  # noqa: S101
    equity = initial
    points = []
    for point, ret in zip(result.equity_curve, returns, strict=True):
        if point.session in sessions:
            equity *= 1 + ret
            points.append(
                EquityPoint(
                    point.session,
                    equity,
                    equity,
                    point.gross_exposure / point.equity * equity,
                    point.positions,
                )
            )
    return compute_metrics(
        replace(
            result,
            equity_curve=tuple(points),
            trades=tuple(t for t in result.trades if t.entry_session in sessions),
        )
    )


@dataclass(frozen=True)
class RegimeBreakdown:
    regime_on: PerformanceMetrics
    regime_off: PerformanceMetrics


def regime_breakdown(
    result: BacktestResult, data: BacktestData, params: MomentumV1Params
) -> RegimeBreakdown:
    """Classify each return using only the prior session's close and full SMA."""
    try:
        context = read_context(result)
        if params != context.params:
            raise ValueError("regime_settings_mismatch")
        symbol = context.universe.regime_benchmark
        validate_series(result, data, symbol)
        calendar = TradingCalendar(context.data_config.calendar)
        frame = data.bars[symbol]
        # Truncate before indicator evaluation so future values cannot affect classification.
        closes = frame.loc[: pd.Timestamp(result.end), "close"]
        averages = sma(closes, params.regime_sma_window)
        on: set[date] = set()
        off: set[date] = set()
        for point in result.equity_curve:
            previous = calendar.previous_session(point.session)
            window = calendar.sessions_window(previous, params.regime_sma_window)
            if any(pd.Timestamp(day) not in closes.index for day in window):
                raise ValueError("missing_regime_history")
            key = pd.Timestamp(previous)
            average = float(averages.loc[key])
            if not math.isfinite(average):
                raise ValueError("invalid_regime_history")
            (on if float(closes.loc[key]) > average else off).add(point.session)
        return RegimeBreakdown(selected_sessions(result, on), selected_sessions(result, off))
    except (KeyError, ValueError, OverflowError):
        missing = unavailable("regime_data_unavailable")
        return RegimeBreakdown(missing, missing)
