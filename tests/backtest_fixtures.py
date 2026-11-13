"""Hand-built bars and tiny windows; no downloaded market observations."""

from datetime import date

import pandas as pd
import pytest

from semiedge.backtest.engine import BacktestData, BacktestRequest
from semiedge.config.models import (
    BacktestSettings,
    CostSettings,
    DataConfig,
    MomentumV1Params,
    RiskLimits,
    ScannerConfig,
    ScoreNormalization,
    UniverseConfig,
    UniverseMember,
)
from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import Adjustment, DataFeed, DataSource
from semiedge.risk.manager import RiskManager, UnavailableEarningsCalendar


def frame(rows, index):
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close", "volume"],
        index=pd.DatetimeIndex(index, name="session", dtype="datetime64[ns]"),
        dtype="float64",
    )


@pytest.fixture
def scenario():
    def make(tail=None, *, slip=0, holding=5, costs=None, count=1):
        calendar = TradingCalendar()
        sessions = calendar.sessions_in_range(date(2024, 1, 2), date(2024, 1, 12))
        tail = tail if tail is not None else [(12, 13, 11, 12, 100)]
        sessions = sessions[: 5 + len(tail)]
        stock = frame(
            [(10, 10.5, 9.5, 10, 100)] * 3
            + [(10.5, 11, 10, 11, 100), (11.5, 12.5, 11, 12, 300)]
            + tail,
            sessions,
        )
        regime = frame(
            [(c, c + 1, c - 1, c, 1000000) for c in [100, 100, 100, 105, 110] + [110] * len(tail)],
            sessions,
        )
        symbols = ["AAA", "BBB"][:count]
        universe = UniverseConfig(
            members=tuple(
                [UniverseMember(symbol=s, group="test", role="tradable") for s in symbols]
                + [UniverseMember(symbol="SMH", group="test", role="benchmark")]
            ),
            comparison_benchmarks=("SMH",),
        )
        params = MomentumV1Params(
            regime_sma_window=3,
            fast_sma_window=2,
            slow_sma_window=3,
            breakout_lookback=2,
            return_lookback=2,
            volume_lookback=2,
            atr_window=2,
            max_holding_sessions=holding,
            score_normalization=ScoreNormalization(trend_persistence_window=2),
        )
        settings = BacktestSettings(
            initial_equity=10000.0,
            costs=costs
            or CostSettings(
                slippage_bps=float(slip),
                commission_per_share=0.0,
                commission_per_order=0.0,
                sell_fee_rate=0.0,
            ),
            earnings_policy="waive_unavailable",
        )
        request = BacktestRequest(
            sessions[5],
            sessions[-1],
            universe,
            params,
            RiskLimits(),
            settings,
            DataConfig(),
            "hand-built synthetic test fixture",
            ScannerConfig(),
        )
        data = BacktestData(
            {**{s: stock.copy() for s in symbols}, "SMH": regime},
            DataFeed.SYNTHETIC,
            Adjustment.SPLIT,
            DataSource.SYNTHETIC,
        )
        gate = RiskManager(
            request.risk,
            calendar=calendar,
            earnings=UnavailableEarningsCalendar(),
            tradable_symbols=frozenset(symbols),
            earnings_waiver="backtest_unavailable",
        )
        return request, data, gate, calendar

    return make
