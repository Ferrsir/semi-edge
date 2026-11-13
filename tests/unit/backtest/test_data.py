"""Cache loading rejects unavailable benchmarks and inconsistent provenance."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from semiedge.backtest.data import BacktestInputError, load_backtest_data
from semiedge.data.schema import DataSource
from semiedge.data.store import StoredSeries


def test_cache_inputs_fail_closed(scenario):
    request, data, _, calendar = scenario(count=2)

    class Store:
        missing = None
        mixed = False

        def read(self, symbol, *, feed, adjustment):
            if symbol == self.missing:
                return None
            source = DataSource.ALPACA if self.mixed and symbol == "BBB" else DataSource.FIXTURE
            return StoredSeries(
                symbol,
                data.bars[symbol],
                feed,
                adjustment,
                source,
                datetime(2024, 1, 15, tzinfo=UTC),
            )

    store = Store()
    kw = {
        "start": request.start,
        "end": request.end,
        "calendar": calendar,
        "corporate_actions": None,
    }
    with pytest.raises(BacktestInputError, match="fixture_manifest"):
        load_backtest_data(store, request.universe, request.data_settings, **kw)
    store.missing = "SMH"
    with pytest.raises(BacktestInputError, match="missing_benchmark"):
        load_backtest_data(store, request.universe, request.data_settings, **kw)
    store.missing = None
    store.mixed = True
    with pytest.raises(BacktestInputError, match="mixed_or_missing_sources"):
        load_backtest_data(store, request.universe, request.data_settings, **kw)
    with pytest.raises(BacktestInputError, match="T17"):
        load_backtest_data(
            store, request.universe, request.data_settings, **(kw | {"corporate_actions": object()})
        )


def test_missing_comparison_benchmark(scenario):
    from semiedge.backtest.engine import run_backtest

    request, data, gate, calendar = scenario()
    bars = dict(data.bars)
    del bars["SMH"]
    with pytest.raises(BacktestInputError, match="missing_benchmark"):
        run_backtest(request, replace(data, bars=bars), gate=gate, calendar=calendar)
