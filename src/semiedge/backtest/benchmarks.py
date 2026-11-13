"""§4 buy-and-hold comparisons using the run's provenance, sessions and costs.

Buy whole shares at the first open plus slippage, reserving entry commissions;
mark at closes, including the final close. The position remains held: there
is no unexecuted sell charge or sell notional. Correlation and beta use aligned daily returns,
sample covariance/variance and zero risk-free rate. Missing bars remain explicit
comparison rows; nothing is filled forward. Buy-and-hold has no risk unit R.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from statistics import covariance, fmean, stdev, variance

import pandas as pd

from semiedge.backtest.costs import fill_cost
from semiedge.backtest.data import BacktestData
from semiedge.backtest.fills import market_price
from semiedge.backtest.metrics import (
    Metric,
    PerformanceMetrics,
    RegimeBreakdown,
    compute_metrics,
    daily_returns,
    finite_metric,
    period_result,
    regime_breakdown,
    unavailable,
    zero_volatility,
)
from semiedge.backtest.metrics_context import read_context, validate_series
from semiedge.backtest.results import BacktestResult, EquityPoint
from semiedge.config.models import BacktestSettings

__all__ = ["BenchmarkComparison", "RegimeBreakdown", "compare_benchmarks", "regime_breakdown"]


@dataclass(frozen=True)
class BenchmarkComparison:
    symbol: str
    metrics: PerformanceMetrics
    excess_net_return: Metric
    correlation: Metric
    beta: Metric


def _benchmark_result(
    result: BacktestResult, data: BacktestData, settings: BacktestSettings, symbol: str
) -> tuple[BacktestResult, float, float]:
    validate_series(result, data, symbol)
    context = read_context(result)
    if settings != context.settings:
        raise ValueError("benchmark_settings_mismatch")
    frame = data.bars[symbol].loc[pd.Timestamp(result.start) : pd.Timestamp(result.end)]
    costs = settings.costs
    buy = market_price(float(frame.iloc[0]["open"]), costs.slippage_bps, sell=False, context=symbol)
    qty = math.floor(
        (settings.initial_equity - costs.commission_per_order) / (buy + costs.commission_per_share)
    )
    if qty <= 0:
        raise ValueError("insufficient_benchmark_capital")
    notional, entry_cost = fill_cost(buy, qty, costs, sell=False, context=symbol)
    cash = settings.initial_equity - notional - entry_cost
    if cash < 0:
        raise ValueError("insufficient_benchmark_capital")
    points = []
    for day, row in frame.iterrows():
        close_notional = float(row["close"]) * qty
        points.append(
            EquityPoint(
                pd.Timestamp(str(day)).date(), cash + close_notional, cash, close_notional, 1
            )
        )
    benchmark = replace(
        result,
        equity_curve=tuple(points),
        initial_equity=settings.initial_equity,
        trades=(),
        cancelled_entries=(),
        risk_decisions=(),
        halts=(),
        data_gaps=(),
        cap_overshoots=(),
    )
    return benchmark, notional, entry_cost


def compare_benchmarks(
    result: BacktestResult,
    data: BacktestData,
    settings: BacktestSettings,
    *,
    origin: BacktestResult | None = None,
) -> tuple[BenchmarkComparison, ...]:
    context = read_context(result)
    strategy = compute_metrics(result)
    comparisons = []
    for symbol in context.universe.comparison_benchmarks:
        try:
            benchmark, notional, entry_cost = _benchmark_result(
                origin or result, data, settings, symbol
            )
            if origin is not None:
                benchmark = period_result(benchmark, result.start, result.end)
                if result.start > origin.start:
                    notional = entry_cost = 0.0
            metrics = replace(
                compute_metrics(benchmark),
                expectancy_r=Metric(None, "benchmark_has_no_risk_unit"),
                total_costs=entry_cost,
                turnover=finite_metric(
                    notional
                    / fmean(p.equity for p in benchmark.equity_curve)
                    * 252
                    / len(benchmark.equity_curve)
                ),
            )
            s, b = daily_returns(result), daily_returns(benchmark)
            if len(s) < 2:
                corr = beta = Metric(None, "fewer_than_2_returns")
            elif zero_volatility(b):
                corr = beta = Metric(None, "zero_benchmark_variance")
            elif zero_volatility(s):
                corr = beta = Metric(None, "zero_strategy_variance")
            else:
                cov = covariance(s, b)
                beta = finite_metric(cov / variance(b))
                corr = (
                    finite_metric(cov / stdev(s) / stdev(b))
                    if not zero_volatility(s)
                    else Metric(None, "zero_strategy_variance")
                )
            excess = (
                finite_metric(strategy.net_return.value - metrics.net_return.value)
                if strategy.net_return.value is not None and metrics.net_return.value is not None
                else Metric(None, strategy.net_return.reason or metrics.net_return.reason)
            )
            comparisons.append(BenchmarkComparison(symbol, metrics, excess, corr, beta))
        except (ValueError, KeyError, OverflowError, ZeroDivisionError):
            # Reasons are fixed text, never vendor messages or raw input.
            reason = "invalid_benchmark"
            try:
                validate_series(result, data, symbol)
            except ValueError as exc:
                if str(exc) in {
                    "benchmark_provenance_mismatch",
                    "missing_benchmark",
                    "missing_or_stale_benchmark_bar",
                    "incomplete_equity_sessions",
                    "no_sessions_in_period",
                }:
                    reason = str(exc)
            missing = Metric(None, reason)
            comparisons.append(
                BenchmarkComparison(symbol, unavailable(reason), missing, missing, missing)
            )
    return tuple(comparisons)
