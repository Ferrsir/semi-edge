"""Atomic, provenance-separated Parquet series with DuckDB inventory queries."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from semiedge.data.schema import (
    PROVENANCE_COLUMNS,
    REQUIRED_BAR_COLUMNS,
    Adjustment,
    DataFeed,
    DataSchemaError,
    DataSource,
    validate_bar_frame,
)

logger = logging.getLogger(__name__)
SUMMARY_COLUMNS = [
    "symbol",
    "feed",
    "adjustment",
    "source",
    "first_session",
    "last_session",
    "rows",
    "last_fetched_at",
]


@dataclass(frozen=True)
class StoredSeries:
    symbol: str
    frame: pd.DataFrame
    feed: DataFeed
    adjustment: Adjustment
    source: DataSource
    fetched_at: datetime


class ParquetBarStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, symbol: str, feed: DataFeed, adjustment: Adjustment) -> Path:
        if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol) is None:
            raise ValueError("invalid symbol")
        return (
            self.root
            / "raw"
            / "bars"
            / "daily"
            / f"feed={feed.value}"
            / f"adjustment={adjustment.value}"
            / f"{symbol}.parquet"
        )

    def _check_synthetic(self, feed: DataFeed, source: DataSource) -> None:
        synthetic = source == DataSource.SYNTHETIC
        if synthetic != (feed == DataFeed.SYNTHETIC):
            raise ValueError("synthetic source and feed must agree")
        if synthetic and "synthetic" not in [part.lower() for part in self.root.parts]:
            raise ValueError("synthetic data requires a synthetic root component")

    def _read_raw(self, path: Path, feed: DataFeed, adjustment: Adjustment) -> pd.DataFrame:
        raw = pd.read_parquet(path)
        if raw.empty or not all(c in raw for c in PROVENANCE_COLUMNS):
            raise DataSchemaError("stored series has no rows or missing provenance")
        if (
            raw.loc[:, list(PROVENANCE_COLUMNS)].isna().any().any()
            or not raw["feed"].eq(feed.value).all()
            or not raw["adjustment"].eq(adjustment.value).all()
            or raw["source"].isna().any()
            or raw["source"].nunique() != 1
        ):
            raise DataSchemaError("stored series has mixed or mismatched provenance")
        source = DataSource(raw["source"].iloc[0])
        self._check_synthetic(feed, source)
        timestamps = pd.DatetimeIndex(raw["fetched_at"])
        if timestamps.tz is None or timestamps.hasnans:
            raise DataSchemaError("stored fetched_at must be timezone-aware and non-null")
        validate_bar_frame(raw.drop(columns=list(PROVENANCE_COLUMNS)))
        return raw

    def write(
        self,
        symbol: str,
        frame: pd.DataFrame,
        *,
        feed: DataFeed,
        adjustment: Adjustment,
        source: DataSource,
        fetched_at: datetime,
    ) -> Path:
        feed, adjustment, source = DataFeed(feed), Adjustment(adjustment), DataSource(source)
        self._check_synthetic(feed, source)
        validate_bar_frame(frame, symbol=symbol)
        if any(c in frame for c in PROVENANCE_COLUMNS):
            raise ValueError("bar frames must not contain provenance columns")
        if frame.empty:
            raise ValueError("cannot persist an empty series")
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        path = self._path(symbol, feed, adjustment)
        incoming = frame.copy()
        incoming["feed"] = feed.value
        incoming["adjustment"] = adjustment.value
        incoming["source"] = source.value
        incoming["fetched_at"] = pd.Timestamp(fetched_at.astimezone(UTC))
        if path.exists():
            old = self._read_raw(path, feed, adjustment)
            if not old["source"].eq(source.value).all():
                raise ValueError("cannot merge different data sources")
            if adjustment != Adjustment.RAW:
                _check_adjustment_basis(symbol, old, incoming)
            # Stable ordering keeps the existing row for equal fetch timestamps.
            incoming = pd.concat([incoming, old]).sort_values("fetched_at", kind="stable")
            incoming = incoming.loc[~incoming.index.duplicated(keep="last")].sort_index()
            columns = list(REQUIRED_BAR_COLUMNS)
            # Warn only when a stored session's values are actually replaced.
            if not old.loc[:, columns].equals(incoming.loc[old.index, columns]):
                logger.warning("OHLCV correction for %s on existing sessions", symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".bars-", suffix=".tmp", dir=path.parent)
        os.close(descriptor)
        try:
            incoming.to_parquet(temporary, index=True)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return path

    def read(self, symbol: str, *, feed: DataFeed, adjustment: Adjustment) -> StoredSeries | None:
        path = self._path(symbol, feed, adjustment)
        if not path.exists():
            return None
        raw = self._read_raw(path, feed, adjustment)
        return StoredSeries(
            symbol,
            raw.drop(columns=list(PROVENANCE_COLUMNS)),
            feed,
            adjustment,
            DataSource(raw["source"].iloc[0]),
            pd.Timestamp(raw["fetched_at"].max()).to_pydatetime(),
        )

    def symbols(self, *, feed: DataFeed, adjustment: Adjustment) -> list[str]:
        directory = self._path("A", feed, adjustment).parent
        return sorted(path.stem for path in directory.glob("*.parquet"))

    def summary(self) -> pd.DataFrame:
        paths = sorted((self.root / "raw" / "bars" / "daily").glob("feed=*/adjustment=*/*.parquet"))
        if not paths:
            return pd.DataFrame(columns=SUMMARY_COLUMNS)
        # Validate provenance before reporting; Hive partition columns must not mask row values.
        for path in paths:
            feed = DataFeed(path.parent.parent.name.removeprefix("feed="))
            adjustment = Adjustment(path.parent.name.removeprefix("adjustment="))
            self._read_raw(path, feed, adjustment)
        with duckdb.connect() as connection:
            return connection.execute(
                """SELECT regexp_extract(filename, '([^/\\\\]+)[.]parquet$', 1) AS symbol,
                          feed, adjustment, source, min(session) AS first_session,
                          max(session) AS last_session, count(*) AS rows,
                          max(fetched_at) AS last_fetched_at
                   FROM read_parquet(?, filename=true, hive_partitioning=false)
                   GROUP BY symbol, feed, adjustment, source ORDER BY symbol, feed, adjustment""",
                [[str(path) for path in paths]],
            ).df()


def _check_adjustment_basis(symbol: str, old: pd.DataFrame, incoming: pd.DataFrame) -> None:
    """Refuse merges that would splice two vendor adjustment bases into one series.

    Adjusted history is restated retroactively (splits, dividends). Stored rows that the incoming
    frame does not replace are only kept when a shared session proves they are on the incoming
    basis: the earliest shared session anchors retained older rows, the latest anchors newer ones.
    """
    shared = old.index.intersection(incoming.index)
    if shared.empty:
        raise ValueError(
            f"{symbol}: adjusted bars must overlap the stored sessions "
            f"({old.index[0].date()}..{old.index[-1].date()}) to verify the adjustment basis"
        )
    anchors = []
    if old.index[0] < incoming.index[0]:
        anchors.append(shared[0])
    if old.index[-1] > incoming.index[-1]:
        anchors.append(shared[-1])
    for anchor in anchors:
        # Splits and dividends both rescale the close; high/low late-print corrections do not
        # indicate a basis change, so only the close is compared.
        stored = old.loc[[anchor], "close"].to_numpy(dtype="float64")
        fresh = incoming.loc[[anchor], "close"].to_numpy(dtype="float64")
        if not np.allclose(stored, fresh, rtol=1e-9, atol=0.0):
            raise ValueError(
                f"{symbol}: adjustment basis changed at {anchor.date()}; "
                "refetch the full stored range before merging"
            )
