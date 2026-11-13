"""Cache-only backtest inputs and reproducible data fingerprints."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import pandas as pd

from semiedge.config.models import DataConfig, UniverseConfig
from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import (
    REQUIRED_BAR_COLUMNS,
    Adjustment,
    DataFeed,
    DataSource,
    empty_bar_frame,
    validate_bar_frame,
)
from semiedge.data.store import ParquetBarStore


class BacktestInputError(ValueError):
    """Missing, inconsistent or invalid simulation inputs."""

    @property
    def reason_code(self) -> str:
        """Only fixed diagnostic codes may reach the CLI; never echo a cache/vendor message."""
        known = {
            "invalid_window",
            "missing_benchmark",
            "mixed_provenance",
            "mixed_or_missing_sources",
            "fixture_manifest_unavailable",
            "invalid_provenance",
            "mixed_synthetic_provenance",
            "split_adjustment_required",
            "fixture_manifest_required",
            "reproducing_command_required",
            "verified_point_in_time_earnings_unavailable",
            "configured_adjustment_mismatch",
            "configured_feed_mismatch",
            "regime_warmup_unavailable",
            "end_of_window_no_bar",
            "incomplete_window",
        }
        # The code is either the message prefix or the final colon-delimited field.
        for field in str(self).split(":"):
            if field.strip() in known:
                return field.strip()
        return "invalid_backtest_input"


@dataclass(frozen=True)
class BacktestData:
    bars: Mapping[str, pd.DataFrame]
    feed: DataFeed
    adjustment: Adjustment
    source: DataSource
    fixture_manifest: str | None = None

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        digest.update(json.dumps([self.feed, self.adjustment, self.source]).encode())
        for symbol, frame in sorted(self.bars.items()):
            digest.update(json.dumps(symbol).encode())
            for session, row in frame.iterrows():
                digest.update(str(session).encode())
                for column in REQUIRED_BAR_COLUMNS:
                    digest.update((":" + repr(float(row[column]))).encode())
        return digest.hexdigest()


def load_backtest_data(
    store: ParquetBarStore,
    universe: UniverseConfig,
    data: DataConfig,
    *,
    start: date,
    end: date,
    calendar: TradingCalendar,
    corporate_actions: object | None,
) -> BacktestData:
    if corporate_actions is not None:
        raise BacktestInputError("corporate actions integration unavailable until T17")
    if start > end or not all(calendar.is_session(d) for d in (start, end)):
        raise BacktestInputError("invalid_window")
    bars = {}
    sources: set[DataSource] = set()
    benchmarks = {universe.regime_benchmark, *universe.comparison_benchmarks}
    for symbol in sorted(set(universe.tradable_symbols) | benchmarks):
        try:
            series = store.read(symbol, feed=data.daily_feed, adjustment=data.adjustment)
        except (ValueError, OSError) as exc:
            raise BacktestInputError(f"{symbol}: cache read failed: {exc}") from exc
        if series is None:
            if symbol in benchmarks:
                raise BacktestInputError(f"{symbol}: missing_benchmark")
            bars[symbol] = empty_bar_frame()
            continue
        if series.feed != data.daily_feed or series.adjustment != data.adjustment:
            raise BacktestInputError(f"{symbol}: mixed_provenance")
        sources.add(series.source)
        bars[symbol] = series.frame.loc[: pd.Timestamp(end)].copy()
        frame = bars[symbol]
        if frame.empty or frame.index[0].date() > start or frame.index[-1].date() < end:
            raise BacktestInputError(f"{symbol}: incomplete_window")
    if len(sources) != 1:
        raise BacktestInputError("mixed_or_missing_sources")
    source = sources.pop()
    # The current store has no fixture manifest metadata. Fail closed rather than invent provenance.
    if source == DataSource.FIXTURE:
        raise BacktestInputError("fixture_manifest_unavailable: supply BacktestData with manifest")
    return BacktestData(bars, data.daily_feed, data.adjustment, source)


def validate_inputs(data: BacktestData, universe: UniverseConfig) -> None:
    if (
        not isinstance(data.feed, DataFeed)
        or not isinstance(data.adjustment, Adjustment)
        or not isinstance(data.source, DataSource)
    ):
        raise BacktestInputError("invalid_provenance")
    if (data.feed == DataFeed.SYNTHETIC) != (data.source == DataSource.SYNTHETIC):
        raise BacktestInputError("mixed_synthetic_provenance")
    if data.adjustment != Adjustment.SPLIT:
        raise BacktestInputError("split_adjustment_required: PRICE RETURN excludes dividends")
    if data.source == DataSource.FIXTURE and (
        not isinstance(data.fixture_manifest, str) or not data.fixture_manifest.strip()
    ):
        raise BacktestInputError("fixture_manifest_required")
    for symbol in {universe.regime_benchmark, *universe.comparison_benchmarks}:
        if symbol not in data.bars or data.bars[symbol].empty:
            raise BacktestInputError(f"{symbol}: missing_benchmark")
    for symbol, frame in data.bars.items():
        try:
            validate_bar_frame(frame, symbol=symbol)
        except ValueError as exc:
            raise BacktestInputError(str(exc)) from exc
        for column, expected in (
            ("feed", data.feed),
            ("adjustment", data.adjustment),
            ("source", data.source),
        ):
            if column in frame and not frame[column].eq(expected.value).all():
                raise BacktestInputError(f"{symbol}: mixed_{column}")
