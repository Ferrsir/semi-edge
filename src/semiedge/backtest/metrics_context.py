"""Run metadata needed to reproduce metrics without guessing capital or provenance."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from semiedge.backtest.data import BacktestData
from semiedge.backtest.results import BacktestResult
from semiedge.config.models import BacktestSettings, DataConfig, MomentumV1Params, UniverseConfig
from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import Adjustment, DataFeed, DataSource, validate_bar_frame


@dataclass(frozen=True)
class MetricsContext:
    universe: UniverseConfig
    params: MomentumV1Params
    settings: BacktestSettings
    data_config: DataConfig
    source: DataSource
    feed: DataFeed
    adjustment: Adjustment


def read_context(result: BacktestResult) -> MetricsContext:
    raw = result.metrics_context
    if raw is None:
        raise ValueError("missing_metrics_context")
    return MetricsContext(
        UniverseConfig.model_validate(raw["universe"]),
        MomentumV1Params.model_validate(raw["strategy"]),
        BacktestSettings.model_validate(raw["BacktestSettings"]),
        DataConfig.model_validate(raw["data"]),
        DataSource(str(raw["source"])),
        DataFeed(str(raw["feed"])),
        Adjustment(str(raw["adjustment"])),
    )


def validate_series(result: BacktestResult, data: BacktestData, symbol: str) -> None:
    context = read_context(result)
    if (data.source, data.feed, data.adjustment) != (
        context.source,
        context.feed,
        context.adjustment,
    ):
        raise ValueError("benchmark_provenance_mismatch")
    frame = data.bars.get(symbol)
    if frame is None or frame.empty:
        raise ValueError("missing_benchmark")
    validate_bar_frame(frame, symbol=symbol)
    for column, expected in (
        ("source", data.source),
        ("feed", data.feed),
        ("adjustment", data.adjustment),
    ):
        if column in frame and not frame[column].eq(expected.value).all():
            raise ValueError("benchmark_provenance_mismatch")
    calendar = TradingCalendar(context.data_config.calendar)
    expected_sessions = calendar.sessions_in_range(result.start, result.end)
    if not expected_sessions or not result.equity_curve:
        raise ValueError("no_sessions_in_period")
    if [p.session for p in result.equity_curve] != expected_sessions:
        raise ValueError("incomplete_equity_sessions")
    if any(day not in pd.DatetimeIndex(frame.index).date for day in expected_sessions):
        raise ValueError("missing_or_stale_benchmark_bar")
