"""Deterministic synthetic demo bars; these are not observations of market prices."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import date

import numpy as np
import pandas as pd

from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import Adjustment, DataFeed, DataSource


class SyntheticMarketDataSource:
    source = DataSource.SYNTHETIC
    feed = DataFeed.SYNTHETIC
    adjustment = Adjustment.SPLIT

    def __init__(self, seed: int = 7, calendar: TradingCalendar | None = None) -> None:
        self.seed = seed
        self.calendar = calendar if calendar is not None else TradingCalendar()

    def get_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]:
        sessions = self.calendar.sessions_in_range(start, end)
        result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            digest = hashlib.sha256(f"{self.seed}:{symbol}".encode()).digest()
            rng = np.random.default_rng(int.from_bytes(digest[:16], "big"))
            n = len(sessions)
            phase = digest[16] / 255 * 2 * np.pi
            regime = np.sin(np.arange(n) / (35 + digest[17] % 60) + phase)
            volatility = 0.008 + digest[18] / 255 * 0.018
            returns = 0.0003 + 0.002 * regime + rng.normal(0, volatility, n)
            close = (30 + digest[19]) * np.exp(np.cumsum(returns))
            previous = np.r_[float(30 + digest[19]), close[:-1]][:n]
            opened = previous * np.exp(rng.normal(0, volatility / 4, n))
            spread = rng.uniform(0.001, volatility / 2, n)
            volume = (200_000 + digest[20] * 20_000) * np.exp(
                rng.normal(0, 0.4, n) + np.abs(returns) / volatility * 0.45
            )
            result[symbol] = pd.DataFrame(
                {
                    "open": opened,
                    "high": np.maximum(opened, close) * (1 + spread),
                    "low": np.minimum(opened, close) / (1 + spread),
                    "close": close,
                    "volume": volume,
                },
                index=pd.DatetimeIndex(sessions, name="session", dtype="datetime64[ns]"),
                dtype="float64",
            )
        return result
