"""Pure, look-ahead-free technical indicators on per-session ``pd.Series``.

Contract (docs/specifications/phase1-interfaces.md, ``semiedge.indicators``):

* Inputs are ``pd.Series`` indexed by session, sorted strictly ascending (unique index).
* Outputs are ``float64`` Series aligned to the input index (same index, same name).
* ``min_periods == window``: a value is produced only when the *whole* window is defined;
  otherwise the output is NaN. Partial windows are never used.
* Non-finite input values (NaN, +/-inf) are treated as *undefined* and make every window
  containing them undefined. Undefined is never silently replaced by a default.

Look-ahead guarantee: the value at position ``t`` is a function of input values at
positions ``<= t`` only. Rolling windows are evaluated with a fixed per-window summation
order (left to right inside the window), so a value depends *only* on the values inside
its window and is bit-for-bit reproducible on any prefix of the data (truncation tests in
``tests/safety/test_signal_lookahead.py``).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

FloatArray = npt.NDArray[np.float64]

__all__ = [
    "atr",
    "prior_rolling_max",
    "prior_rolling_mean",
    "rolling_fraction_true",
    "sma",
    "trailing_return",
    "true_range",
]


# ------------------------------------------------------------------ validation helpers


def _check_window(window: int) -> None:
    # bool is a subclass of int; a window of True/False is always a caller bug.
    if isinstance(window, bool) or not isinstance(window, int):
        raise TypeError(f"window must be an int, got {type(window).__name__}")
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")


def _check_series(series: Any, label: str) -> None:
    if not isinstance(series, pd.Series):
        raise TypeError(f"{label} must be a pandas Series, got {type(series).__name__}")
    if not series.index.is_unique:
        raise ValueError(f"{label}: index must be unique (one row per session)")
    if not series.index.is_monotonic_increasing:
        raise ValueError(f"{label}: index must be sorted ascending by session")


def _float_values(series: pd.Series[float], label: str) -> FloatArray:
    """Return a writable float64 copy of ``series`` with non-finite values set to NaN."""
    _check_series(series, label)
    dtype = series.dtype
    if pd.api.types.is_bool_dtype(dtype) or not pd.api.types.is_numeric_dtype(dtype):
        raise TypeError(f"{label}: expected a numeric (non-boolean) series, got dtype {dtype}")
    values = np.array(series.to_numpy(dtype=np.float64, na_value=np.nan), dtype=np.float64)
    values[~np.isfinite(values)] = np.nan
    return values


def _aligned(label: str, *series: pd.Series[float]) -> None:
    first = series[0].index
    for other in series[1:]:
        if not first.equals(other.index):
            raise ValueError(f"{label}: high, low and close must share an identical index")


def _wrap(values: FloatArray, like: pd.Series[Any], *, keep_name: bool = True) -> pd.Series[float]:
    return pd.Series(
        values, index=like.index, dtype=np.float64, name=like.name if keep_name else None
    )


# ------------------------------------------------------------------ window kernels


def _window_sum(values: FloatArray, window: int) -> FloatArray:
    """Sum of ``values[t-window+1 .. t]``; NaN until ``window`` values exist or if any is NaN.

    The summation inside each window is strictly left to right, so the result for a window
    depends only on that window's values (no running-sum drift, no dependence on history).
    """
    n = values.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n < window:
        return out
    m = n - window + 1
    acc = values[0:m].copy()
    for k in range(1, window):
        acc += values[k : k + m]
    out[window - 1 :] = acc
    return out


def _window_max(values: FloatArray, window: int) -> FloatArray:
    """Max of ``values[t-window+1 .. t]``; NaN until ``window`` values exist or if any is NaN."""
    n = values.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n < window:
        return out
    m = n - window + 1
    acc = values[0:m].copy()
    for k in range(1, window):
        np.maximum(acc, values[k : k + m], out=acc)  # np.maximum propagates NaN
    out[window - 1 :] = acc
    return out


def _shift_forward_one(values: FloatArray) -> FloatArray:
    """``out[t] = values[t-1]`` (``out[0]`` is NaN): moves the window end to ``t-1``."""
    out = np.full(values.shape[0], np.nan, dtype=np.float64)
    out[1:] = values[:-1]
    return out


# ------------------------------------------------------------------ public indicators


def sma(series: pd.Series[float], window: int) -> pd.Series[float]:
    """Simple moving average ``mean(X[t-window+1] .. X[t])`` — includes the current bar."""
    _check_window(window)
    values = _float_values(series, "sma")
    return _wrap(_window_sum(values, window) / window, series)


def true_range(
    high: pd.Series[float], low: pd.Series[float], close: pd.Series[float]
) -> pd.Series[float]:
    """True range ``max(H-L, |H-C[t-1]|, |L-C[t-1]|)``; the first bar is ``H-L``.

    NaN when any required input of that bar (including the previous close, after the first
    bar) is undefined.
    """
    h = _float_values(high, "true_range high")
    lo = _float_values(low, "true_range low")
    c = _float_values(close, "true_range close")
    _aligned("true_range", high, low, close)
    tr = h - lo
    if tr.shape[0] > 1:
        prev_close = c[:-1]
        gap_up = np.abs(h[1:] - prev_close)
        gap_down = np.abs(lo[1:] - prev_close)
        # np.maximum propagates NaN: an undefined previous close makes TR undefined.
        tr[1:] = np.maximum(tr[1:], np.maximum(gap_up, gap_down))
    return _wrap(tr, close, keep_name=False)


def atr(
    high: pd.Series[float],
    low: pd.Series[float],
    close: pd.Series[float],
    window: int = 14,
) -> pd.Series[float]:
    """Wilder's Average True Range (docs/specifications/momentum_v1.md).

    The first value is produced at the ``window``-th true range and equals the arithmetic
    mean of the first ``window`` true ranges (``TR_1 = H_1 - L_1``). Afterwards
    ``ATR_t = (ATR_{t-1} * (window - 1) + TR_t) / window``.

    An undefined true range (missing/non-finite bar) makes ATR undefined at that bar and
    restarts the seeding: ATR is defined again only after ``window`` consecutive defined
    true ranges, re-seeded with their mean. ATR is therefore never computed from a partial
    window or across a gap.
    """
    _check_window(window)
    tr_series = true_range(high, low, close)
    tr = tr_series.to_numpy(dtype=np.float64)
    out = np.full(tr.shape[0], np.nan, dtype=np.float64)
    previous = math.nan
    seed_count = 0
    seed_sum = 0.0
    for i in range(tr.shape[0]):
        value = float(tr[i])
        if math.isnan(value):
            previous = math.nan
            seed_count = 0
            seed_sum = 0.0
            continue
        if math.isnan(previous):
            seed_count += 1
            seed_sum += value
            if seed_count == window:
                previous = seed_sum / window
                out[i] = previous
        else:
            previous = (previous * (window - 1) + value) / window
            out[i] = previous
    return _wrap(out, close, keep_name=False)


def prior_rolling_max(series: pd.Series[float], window: int) -> pd.Series[float]:
    """``max(X[t-window] .. X[t-1])`` — the current bar ``t`` is EXCLUDED (shift then roll)."""
    _check_window(window)
    values = _float_values(series, "prior_rolling_max")
    return _wrap(_window_max(_shift_forward_one(values), window), series)


def prior_rolling_mean(series: pd.Series[float], window: int) -> pd.Series[float]:
    """``mean(X[t-window] .. X[t-1])`` — the current bar ``t`` is EXCLUDED (shift then roll)."""
    _check_window(window)
    values = _float_values(series, "prior_rolling_mean")
    return _wrap(_window_sum(_shift_forward_one(values), window) / window, series)


def trailing_return(close: pd.Series[float], window: int) -> pd.Series[float]:
    """Simple return ``close[t] / close[t-window] - 1``.

    NaN unless both prices are defined and strictly positive (a return on a non-positive
    price is undefined, never a large or infinite number).
    """
    _check_window(window)
    values = _float_values(close, "trailing_return")
    out = np.full(values.shape[0], np.nan, dtype=np.float64)
    if values.shape[0] > window:
        current = values[window:]
        base = values[:-window]
        valid = (current > 0) & (base > 0)  # NaN compares False
        ratio = np.full(current.shape[0], np.nan, dtype=np.float64)
        np.divide(current, base, out=ratio, where=valid)
        out[window:] = ratio - 1.0
    return _wrap(out, close)


def rolling_fraction_true(mask: pd.Series[Any], window: int) -> pd.Series[float]:
    """Fraction of True values over ``mask[t-window+1] .. mask[t]`` (current bar included).

    ``mask`` may be ``bool``, nullable ``boolean`` (``<NA>`` = undefined) or a float series
    holding only 0.0, 1.0 or NaN (NaN = undefined). The result is NaN when the window is
    incomplete or contains any undefined value.
    """
    _check_window(window)
    _check_series(mask, "rolling_fraction_true")
    dtype = mask.dtype
    if pd.api.types.is_bool_dtype(dtype) or pd.api.types.is_numeric_dtype(dtype):
        values = np.array(mask.to_numpy(dtype=np.float64, na_value=np.nan), dtype=np.float64)
    else:
        raise TypeError(f"rolling_fraction_true: mask must be boolean or 0/1 float, got {dtype}")
    defined = values[~np.isnan(values)]
    if not np.isin(defined, (0.0, 1.0)).all():
        raise ValueError("rolling_fraction_true: mask values must be True/False (1/0) or NaN")
    return _wrap(_window_sum(values, window) / window, mask)
