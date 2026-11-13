"""Labels-first §4 report composition; all computation is offline and deterministic."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

from semiedge.backtest.benchmarks import compare_benchmarks
from semiedge.backtest.data import BacktestData
from semiedge.backtest.metrics import (
    STRESS_PERIODS,
    compute_metrics,
    period_breakdown,
    period_result,
    regime_breakdown,
    worst_drawdowns,
)
from semiedge.backtest.metrics_context import read_context
from semiedge.backtest.results import BacktestResult


def metrics_report(
    result: BacktestResult, data: BacktestData | None, *, command: str
) -> dict[str, object]:
    context = read_context(result)
    periods = {
        str(year): (max(result.start, date(year, 1, 1)), min(result.end, date(year, 12, 31)))
        for year in range(result.start.year, result.end.year + 1)
    }
    periods.update(
        {
            name: (max(result.start, start), min(result.end, end))
            for name, (start, end) in STRESS_PERIODS.items()
            if start <= result.end and end >= result.start
        }
    )
    # Missing/changed cache data is never substituted into a historical comparison.
    labels = list(result.labels)
    if data is None or data.fingerprint() != result.data_fingerprint:
        labels.append("BENCHMARK/REGIME DATA UNAVAILABLE: original data fingerprint not reproduced")
        data = BacktestData({}, context.feed, context.adjustment, context.source)
    whole = compute_metrics(result)
    rows = period_breakdown(result, periods=periods)
    drawdowns = worst_drawdowns(result)
    windows = []
    for row in (*rows, *drawdowns):
        subset = period_result(result, row.start, row.end)
        windows.append(
            {
                "label": row.label,
                "comparisons": [
                    asdict(c)
                    for c in compare_benchmarks(subset, data, context.settings, origin=result)
                ],
            }
        )
    return {
        "labels": labels,
        "command": command,
        "backtest_command": result.command,
        "run_id": result.run_id,
        "annualisation_sessions": 252,
        "risk_free_rate": 0,
        "whole_period": asdict(whole),
        "periods": [asdict(row) for row in rows],
        "worst_drawdowns": [asdict(row) for row in drawdowns],
        "regime": asdict(regime_breakdown(result, data, context.params)),
        "benchmarks": [asdict(c) for c in compare_benchmarks(result, data, context.settings)],
        "window_benchmarks": windows,
    }
