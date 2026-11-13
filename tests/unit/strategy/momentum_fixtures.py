"""Shared hand-computable fixtures for ``momentum_v1`` tests.

Small windows keep every value computable by hand (see the per-test comments):
regime SMA 3, fast SMA 2, slow SMA 3, breakout/return/volume lookback 2, ATR 2,
trend-persistence window 2. Defaults otherwise (thresholds 0.02 / 1.2; saturations
0.15 / 1.0 / 3.0; weights 40/30/20/10). required_history() == 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from semiedge.config.models import MomentumV1Params, ScoreNormalization
from semiedge.data.schema import DataFeed

SMALL_PARAMS = MomentumV1Params(
    regime_sma_window=3,
    fast_sma_window=2,
    slow_sma_window=3,
    breakout_lookback=2,
    return_lookback=2,
    volume_lookback=2,
    atr_window=2,
    score_normalization=ScoreNormalization(trend_persistence_window=2),
)

# Stock fixture: 5 sessions; t4 is a qualifying breakout (hand values in test_momentum_v1_features).
STOCK = {
    "open": [10.0, 10.0, 10.0, 10.5, 11.5],
    "high": [10.5, 10.5, 10.5, 11.0, 12.5],
    "low": [9.5, 9.5, 9.5, 10.0, 11.0],
    "close": [10.0, 10.0, 10.0, 11.0, 12.0],
    "volume": [100.0, 100.0, 100.0, 100.0, 300.0],
}
REGIME_CLOSE = [100.0, 100.0, 100.0, 105.0, 110.0]


def sessions(n: int, start: str = "2024-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n, name="session").as_unit("ns")


def bar_frame(
    columns: dict[str, list[float]], index: pd.DatetimeIndex | None = None
) -> pd.DataFrame:
    n = len(columns["close"])
    idx = sessions(n) if index is None else index
    return pd.DataFrame({k: np.asarray(v, dtype=np.float64) for k, v in columns.items()}, index=idx)


def regime_frame(closes: list[float], index: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    c = np.asarray(closes, dtype=np.float64)
    return bar_frame(
        {
            "open": list(c),
            "high": list(c + 1),
            "low": list(c - 1),
            "close": list(c),
            "volume": [1e6] * len(c),
        },
        index,
    )


@dataclass(frozen=True)
class FakeQuality:
    """Satisfies ``semiedge.strategy.QualityReport`` structurally."""

    symbol: str
    as_of: date
    feed: DataFeed | None = DataFeed.SIP
    issues: tuple[str, ...] = ()
    actionable: bool = True

    @property
    def is_actionable(self) -> bool:
        return self.actionable


def quality_for(
    as_of: date, *symbols: str, feed: DataFeed | None = DataFeed.SIP
) -> dict[str, FakeQuality]:
    return {s: FakeQuality(s, as_of, feed) for s in symbols}
