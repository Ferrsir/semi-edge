"""Canonical in-memory contract for daily bar frames shared by data, signals and backtests.

A *daily bar frame* is a ``pandas.DataFrame`` for ONE symbol where:

* the index is named ``session``; dtype ``datetime64[ns]``, tz-naive, normalised to
  midnight; each value is an exchange session date (XNYS); strictly increasing; unique;
* required columns ``open, high, low, close, volume`` are ``float64``;
* optional columns ``trade_count, vwap`` may be present (``float64``);
* provenance columns (``feed, adjustment, source, fetched_at``) live in the raw store,
  not in frames passed to strategies.

Bars in a frame handed to a strategy MUST be completed bars (see
``semiedge.data.validation``); strategies never receive an in-progress session.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pandas as pd

SESSION_INDEX_NAME = "session"
REQUIRED_BAR_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")
OPTIONAL_BAR_COLUMNS: tuple[str, ...] = ("trade_count", "vwap")
PROVENANCE_COLUMNS: tuple[str, ...] = ("feed", "adjustment", "source", "fetched_at")


class DataFeed(StrEnum):
    """Market-data feed a bar came from. IEX-only volume is NOT comparable to consolidated."""

    IEX = "iex"
    SIP = "sip"  # consolidated tape
    DELAYED_SIP = "delayed_sip"
    SYNTHETIC = "synthetic"  # deterministic generated data; never mixed with real data


class Adjustment(StrEnum):
    """Corporate-action adjustment applied by the data vendor."""

    RAW = "raw"
    SPLIT = "split"
    DIVIDEND = "dividend"
    ALL = "all"


class DataSource(StrEnum):
    ALPACA = "alpaca"
    SYNTHETIC = "synthetic"
    FIXTURE = "fixture"


class DataSchemaError(ValueError):
    """A frame violates the daily bar frame contract."""


def validate_bar_frame(frame: pd.DataFrame, *, symbol: str = "?") -> None:
    """Raise :class:`DataSchemaError` unless ``frame`` satisfies the daily bar frame contract."""
    if frame.index.name != SESSION_INDEX_NAME:
        raise DataSchemaError(f"{symbol}: index must be named {SESSION_INDEX_NAME!r}")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is not None:
        raise DataSchemaError(f"{symbol}: index must be a tz-naive DatetimeIndex")
    if frame.index.dtype != np.dtype("datetime64[ns]") or frame.index.hasnans:
        raise DataSchemaError(f"{symbol}: session index must be datetime64[ns] without NaT")
    if not frame.columns.is_unique:
        raise DataSchemaError(f"{symbol}: duplicate columns")
    if len(frame.index) and not (frame.index == frame.index.normalize()).all():
        raise DataSchemaError(f"{symbol}: session index values must be normalised to midnight")
    if not frame.index.is_unique:
        raise DataSchemaError(f"{symbol}: duplicate sessions")
    if not frame.index.is_monotonic_increasing:
        raise DataSchemaError(f"{symbol}: sessions must be sorted ascending")
    missing = [c for c in REQUIRED_BAR_COLUMNS if c not in frame.columns]
    if missing:
        raise DataSchemaError(f"{symbol}: missing columns {missing}")
    for col in (*REQUIRED_BAR_COLUMNS, *(c for c in OPTIONAL_BAR_COLUMNS if c in frame)):
        if frame[col].dtype != np.float64:
            raise DataSchemaError(
                f"{symbol}: column {col!r} must be float64, got {frame[col].dtype}"
            )


def empty_bar_frame() -> pd.DataFrame:
    """Return an empty frame that satisfies the contract."""
    index = pd.DatetimeIndex([], dtype="datetime64[ns]", name=SESSION_INDEX_NAME)
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in REQUIRED_BAR_COLUMNS}, index=index)
