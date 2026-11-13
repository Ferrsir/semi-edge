"""Vendor-independent daily bar source contract."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

import pandas as pd

from semiedge.data.schema import Adjustment, DataFeed, DataSource


class MarketDataSource(Protocol):
    source: DataSource
    feed: DataFeed
    adjustment: Adjustment

    def get_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]: ...
