"""``momentum_v1`` features, mandatory conditions and ranking score (issue #12).

Formulas: docs/specifications/momentum_v1.md. Parameters: ``config/strategies/momentum_v1.yaml``.

The ranking weights (40/30/20/10) and saturation points are **heuristic priors with no
statistical evidence behind them** (DECISIONS.md D-006). They order candidates; they do not
predict returns. Phase 2 sensitivity analysis must report how results change with them.

Fail-closed conventions (any of these makes the affected values undefined, never a default):

* A bar is *invalid* when any OHLCV value is non-finite, any price is ``<= 0``, volume is
  ``< 0``, or OHLC is inconsistent (``high < low``, ``high < max(open, close)``,
  ``low > min(open, close)``). An invalid bar is treated as a missing session.
* Stock and regime series are outer-joined on session. A session missing (or invalid) in
  EITHER series makes that whole row undefined for both, so every window that contains it is
  undefined too. Rows before the later of the two first sessions are dropped.
* A session missing from BOTH series cannot be seen by the join alone. When
  ``expected_sessions`` (the exchange calendar) is supplied, the aligned frame is reindexed
  onto every expected session in range, so such a gap is an undefined row as well; a bar on a
  session that is not an expected session is rejected (:class:`DataSchemaError`).
  :func:`evaluate_universe` requires the calendar; callers of :func:`compute_features`
  (the backtester) must pass it, or supply gap-free bars and accept that gaps are unchecked.
* Comparisons are evaluated on IEEE-754 doubles with no tolerance. A value that is exactly
  at a threshold in decimal but rounds below it (e.g. an excess return computing to
  ``0.0199999999999998``) FAILS the inclusive ``>=`` test (conservative reading).
* Relative volume is undefined when the prior-volume baseline is ``<= 0`` (no division by
  zero); the breakout component is undefined when ``ATR <= 0``.
* ``cond_*`` columns are ``False`` whenever an input is undefined (a NaN comparison never
  passes). :func:`evaluate_universe` reports such conditions as *undefined*, not as failed
  comparisons.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from types import MappingProxyType
from typing import Final, cast

import numpy as np
import numpy.typing as npt
import pandas as pd

from semiedge.config.models import MomentumV1Params
from semiedge.data.schema import (
    REQUIRED_BAR_COLUMNS,
    SESSION_INDEX_NAME,
    DataSchemaError,
    validate_bar_frame,
)
from semiedge.indicators import (
    atr,
    prior_rolling_max,
    prior_rolling_mean,
    rolling_fraction_true,
    sma,
    trailing_return,
)
from semiedge.strategy.base import ConditionResult, QualityReport, SignalEvaluation

FloatArray = npt.NDArray[np.float64]

STRATEGY_NAME: Final = "momentum_v1"

CONDITION_NAMES: Final[tuple[str, ...]] = (
    "sector_regime",
    "above_sma_fast",
    "above_sma_slow",
    "breakout",
    "relative_strength",
    "relative_volume",
)

SCORE_COMPONENTS: Final[tuple[str, ...]] = ("s_rs", "s_bo", "s_rv", "s_tp")

FEATURE_COLUMNS: Final[tuple[str, ...]] = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "sma_fast",
    "sma_slow",
    "prior_high",
    "atr",
    "ret",
    "regime_close",
    "regime_sma",
    "regime_ret",
    "excess_ret",
    "prior_volume",
    "rel_volume",
    "trend_persistence",
    *(f"cond_{name}" for name in CONDITION_NAMES),
    "all_conditions",
    *SCORE_COMPONENTS,
    "score",
)

__all__ = [
    "CONDITION_NAMES",
    "FEATURE_COLUMNS",
    "SCORE_COMPONENTS",
    "STRATEGY_NAME",
    "compute_features",
    "evaluate_universe",
]


# ------------------------------------------------------------------ helpers


def _clip01(values: FloatArray) -> FloatArray:
    """``min(1, max(0, x))`` element-wise; NaN stays NaN."""
    return np.clip(values, 0.0, 1.0)


def _invalid_rows(frame: pd.DataFrame) -> npt.NDArray[np.bool_]:
    """Rows that cannot be used: non-finite, non-positive prices, negative volume, bad OHLC."""
    o = frame["open"].to_numpy(dtype=np.float64)
    h = frame["high"].to_numpy(dtype=np.float64)
    lo = frame["low"].to_numpy(dtype=np.float64)
    c = frame["close"].to_numpy(dtype=np.float64)
    v = frame["volume"].to_numpy(dtype=np.float64)
    values = np.column_stack([o, h, lo, c, v])
    with np.errstate(invalid="ignore"):
        valid = np.isfinite(values).all(axis=1)
        valid &= (o > 0) & (h > 0) & (lo > 0) & (c > 0) & (v >= 0)
        valid &= (h >= lo) & (h >= np.maximum(o, c)) & (lo <= np.minimum(o, c))
    return np.asarray(~valid, dtype=np.bool_)


def _expected_index(expected_sessions: Iterable[date | pd.Timestamp]) -> pd.DatetimeIndex:
    """Normalise the expected session calendar to a sorted, unique, tz-naive index."""
    if isinstance(cast(object, expected_sessions), str):
        raise TypeError("expected_sessions must be a collection of sessions, not a string")
    index = pd.DatetimeIndex(list(expected_sessions))
    if index.tz is not None:
        raise ValueError("expected_sessions must be tz-naive session dates")
    if index.hasnans:
        raise ValueError("expected_sessions must not contain missing values")
    if not (index == index.normalize()).all():
        raise ValueError("expected_sessions must be dates (midnight), not intraday times")
    if not index.is_unique:
        raise ValueError("expected_sessions must be unique")
    return index.sort_values().as_unit("ns")


def _aligned_frames(
    bars: pd.DataFrame, regime: pd.DataFrame, expected: pd.DatetimeIndex | None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Outer-join both series on session; a row missing/invalid in either is NaN in both.

    With an ``expected`` calendar the index is every expected session in range, so a session
    absent from both series is an all-NaN (undefined) row.
    """
    cols = list(REQUIRED_BAR_COLUMNS)
    stock = bars[cols].astype(np.float64)
    reg = regime[cols].astype(np.float64)
    if expected is not None:
        for label, frame in (("bars", stock), ("regime", reg)):
            unexpected = frame.index.difference(expected)
            if len(unexpected):
                shown = ", ".join(str(ts.date()) for ts in unexpected[:5])
                raise DataSchemaError(
                    f"{label}: {len(unexpected)} bar(s) on sessions that are not expected "
                    f"trading sessions ({shown})"
                )
    if stock.empty or reg.empty:
        index = pd.DatetimeIndex([], dtype="datetime64[ns]", name=SESSION_INDEX_NAME)
    else:
        start = max(stock.index[0], reg.index[0])
        union = stock.index.union(reg.index)
        if expected is not None:
            end = union[-1]
            union = union.union(expected[(expected >= start) & (expected <= end)])
        index = pd.DatetimeIndex(union[union >= start], name=SESSION_INDEX_NAME)
    stock_arr = stock.reindex(index).to_numpy(dtype=np.float64, copy=True)
    reg_arr = reg.reindex(index).to_numpy(dtype=np.float64, copy=True)
    stock_aligned = pd.DataFrame(stock_arr, index=index, columns=cols)
    reg_aligned = pd.DataFrame(reg_arr, index=index, columns=cols)
    undefined = _invalid_rows(stock_aligned) | _invalid_rows(reg_aligned)
    stock_arr[undefined] = np.nan
    reg_arr[undefined] = np.nan
    return (
        pd.DataFrame(stock_arr, index=index, columns=cols),
        pd.DataFrame(reg_arr, index=index, columns=cols),
    )


def _arr(series: pd.Series[float]) -> FloatArray:
    return np.asarray(series.to_numpy(dtype=np.float64), dtype=np.float64)


# ------------------------------------------------------------------ features


def compute_features(
    bars: pd.DataFrame,
    regime: pd.DataFrame,
    params: MomentumV1Params,
    *,
    expected_sessions: Iterable[date | pd.Timestamp] | None = None,
) -> pd.DataFrame:
    """Vectorised per-session ``momentum_v1`` features for ONE symbol (reused by the backtester).

    ``bars`` and ``regime`` must satisfy the daily bar frame contract
    (:func:`semiedge.data.schema.validate_bar_frame`; :class:`DataSchemaError` otherwise).
    The result is indexed by the aligned sessions (see module docstring) with
    :data:`FEATURE_COLUMNS`. Row ``t`` depends only on bars at sessions ``<= t`` (truncation
    and perturb-the-future tests in ``tests/safety/test_signal_lookahead.py``).

    ``expected_sessions`` is the exchange session calendar (at least covering the bars'
    range). When given, sessions missing from both series become undefined rows and bars on
    non-sessions raise :class:`DataSchemaError`. Without it, only sessions missing from ONE
    series are detected; pass it whenever the bars may contain gaps.
    """
    validate_bar_frame(bars, symbol="bars")
    validate_bar_frame(regime, symbol="regime")
    expected = None if expected_sessions is None else _expected_index(expected_sessions)
    stock, reg = _aligned_frames(bars, regime, expected)
    norm = params.score_normalization
    weights = params.score_weights

    close_s = stock["close"]
    close = _arr(close_s)
    volume = _arr(stock["volume"])
    sma_fast = _arr(sma(close_s, params.fast_sma_window))
    sma_slow_s = sma(close_s, params.slow_sma_window)
    sma_slow = _arr(sma_slow_s)
    prior_high = _arr(prior_rolling_max(stock["high"], params.breakout_lookback))
    atr_v = _arr(atr(stock["high"], stock["low"], close_s, params.atr_window))
    ret = _arr(trailing_return(close_s, params.return_lookback))
    regime_close = _arr(reg["close"])
    regime_sma = _arr(sma(reg["close"], params.regime_sma_window))
    regime_ret = _arr(trailing_return(reg["close"], params.return_lookback))
    excess_ret = ret - regime_ret
    prior_volume = _arr(prior_rolling_mean(stock["volume"], params.volume_lookback))

    n = close.shape[0]
    rel_volume = np.full(n, np.nan, dtype=np.float64)
    np.divide(volume, prior_volume, out=rel_volume, where=prior_volume > 0)

    # Trend persistence: share of the last W sessions with C_u > SMA_slow(C)_u.
    # Undefined (NaN) wherever SMA_slow or C is undefined, so the window is never partial.
    above_slow = np.where(np.isnan(close) | np.isnan(sma_slow), np.nan, close > sma_slow)
    trend_persistence = _arr(
        rolling_fraction_true(
            pd.Series(np.asarray(above_slow, dtype=np.float64), index=stock.index),
            norm.trend_persistence_window,
        )
    )

    with np.errstate(invalid="ignore"):
        conditions = {
            "sector_regime": regime_close > regime_sma,
            "above_sma_fast": close > sma_fast,
            "above_sma_slow": close > sma_slow,
            "breakout": close > prior_high,
            "relative_strength": excess_ret >= params.min_excess_return,
            "relative_volume": rel_volume >= params.min_relative_volume,
        }
    all_conditions = np.ones(n, dtype=np.bool_)
    for passed in conditions.values():
        all_conditions &= passed

    s_rs = _clip01(
        (excess_ret - params.min_excess_return)
        / (norm.relative_strength_saturation - params.min_excess_return)
    )
    breakout_atr = np.full(n, np.nan, dtype=np.float64)
    np.divide(close - prior_high, atr_v, out=breakout_atr, where=atr_v > 0)
    s_bo = _clip01(breakout_atr / norm.breakout_atr_saturation)
    s_rv = _clip01(
        (rel_volume - params.min_relative_volume)
        / (norm.relative_volume_saturation - params.min_relative_volume)
    )
    s_tp = trend_persistence
    raw_score = 100.0 * (
        weights.relative_strength * s_rs
        + weights.breakout * s_bo
        + weights.relative_volume * s_rv
        + weights.trend_persistence * s_tp
    )
    # Weights sum to 1 only within 1e-9 (config validation); clip so the bound is exact.
    score = np.clip(raw_score, 0.0, 100.0)

    data: dict[str, npt.NDArray[np.float64] | npt.NDArray[np.bool_]] = {
        "open": _arr(stock["open"]),
        "high": _arr(stock["high"]),
        "low": _arr(stock["low"]),
        "close": close,
        "volume": volume,
        "sma_fast": sma_fast,
        "sma_slow": sma_slow,
        "prior_high": prior_high,
        "atr": atr_v,
        "ret": ret,
        "regime_close": regime_close,
        "regime_sma": regime_sma,
        "regime_ret": regime_ret,
        "excess_ret": excess_ret,
        "prior_volume": prior_volume,
        "rel_volume": rel_volume,
        "trend_persistence": trend_persistence,
    }
    for name, passed in conditions.items():
        data[f"cond_{name}"] = np.asarray(passed, dtype=np.bool_)
    data["all_conditions"] = all_conditions
    data.update({"s_rs": s_rs, "s_bo": s_bo, "s_rv": s_rv, "s_tp": s_tp, "score": score})
    return pd.DataFrame(data, index=stock.index, columns=list(FEATURE_COLUMNS))


# ------------------------------------------------------------------ evaluation


def _opt(value: object) -> float | None:
    """A finite float, or ``None`` for undefined (NaN/inf)."""
    number = float(value)  # type: ignore[arg-type]
    return number if math.isfinite(number) else None


def _fmt(value: float | None) -> str:
    return "undefined" if value is None else f"{value:.6g}"


def _condition_results(
    values: Mapping[str, float | None], params: MomentumV1Params, regime_symbol: str
) -> tuple[ConditionResult, ...]:
    def get(column: str) -> float | None:
        return values.get(column)

    close = get("close")
    specs: list[tuple[str, float | None, float | None, str, str]] = [
        (
            "sector_regime",
            get("regime_close"),
            get("regime_sma"),
            ">",
            f"{regime_symbol} close {{v}} > SMA{params.regime_sma_window} {{t}}",
        ),
        (
            "above_sma_fast",
            close,
            get("sma_fast"),
            ">",
            f"close {{v}} > SMA{params.fast_sma_window} {{t}}",
        ),
        (
            "above_sma_slow",
            close,
            get("sma_slow"),
            ">",
            f"close {{v}} > SMA{params.slow_sma_window} {{t}}",
        ),
        (
            "breakout",
            close,
            get("prior_high"),
            ">",
            f"close {{v}} > prior {params.breakout_lookback}-session high {{t}}",
        ),
        (
            "relative_strength",
            get("excess_ret"),
            params.min_excess_return,
            ">=",
            f"{params.return_lookback}-session excess return vs {regime_symbol} {{v}} >= {{t}}",
        ),
        (
            "relative_volume",
            get("rel_volume"),
            params.min_relative_volume,
            ">=",
            f"volume / prior {params.volume_lookback}-session mean {{v}} >= {{t}}",
        ),
    ]
    results: list[ConditionResult] = []
    for name, value, threshold, op, template in specs:
        if value is None or threshold is None:
            passed = False
        else:
            passed = value > threshold if op == ">" else value >= threshold
        description = template.format(v=_fmt(value), t=_fmt(threshold))
        results.append(ConditionResult(name, passed, value, threshold, description))
    return tuple(results)


def _quality_reasons(
    symbol: str,
    as_of: date,
    quality: Mapping[str, QualityReport] | None,
    reference_feed: object,
) -> list[str]:
    if quality is None:
        return []  # reported once, by the caller
    report = quality.get(symbol)
    if report is None:
        return [f"{symbol}: no data quality report (missing sessions/feed unverified)"]
    reasons: list[str] = []
    if report.symbol != symbol:
        reasons.append(f"{symbol}: data quality report is for {report.symbol!r}")
    if report.as_of != as_of:
        reasons.append(f"{symbol}: data quality report is as of {report.as_of}, not {as_of}")
    if not report.is_actionable:
        detail = "; ".join(report.issues) if report.issues else "not actionable"
        reasons.append(f"{symbol}: data quality not actionable ({detail})")
    if report.feed is None:
        reasons.append(f"{symbol}: data feed unknown")
    elif reference_feed is not None and report.feed != reference_feed:
        reasons.append(f"{symbol}: feed {report.feed} differs from regime benchmark feed")
    return reasons


def _completed(
    frame: pd.DataFrame | None, symbol: str, as_of: date
) -> tuple[pd.DataFrame | None, list[str]]:
    """Validate and truncate to sessions ``<= as_of``; return reasons when unusable/stale."""
    if frame is None:
        return None, [f"{symbol}: no bars supplied"]
    try:
        validate_bar_frame(frame, symbol=symbol)
    except DataSchemaError as exc:
        return None, [f"{symbol}: invalid bar frame ({exc})"]
    completed = frame.loc[frame.index <= pd.Timestamp(as_of)]
    if completed.empty:
        return None, [f"{symbol}: no completed bars on or before {as_of}"]
    last = completed.index[-1].date()
    if last != as_of:
        return completed, [f"{symbol}: stale data (last session {last}, as_of {as_of})"]
    return completed, []


def evaluate_universe(
    bars: Mapping[str, pd.DataFrame],
    regime_symbol: str,
    params: MomentumV1Params,
    as_of: date,
    *,
    tradable: Sequence[str],
    quality: Mapping[str, QualityReport] | None = None,
    expected_sessions: Iterable[date | pd.Timestamp] | None = None,
) -> list[SignalEvaluation]:
    """Evaluate every tradable symbol at ``as_of``; eligible first, by score desc then symbol.

    Bars after ``as_of`` are ignored (never look ahead). A symbol is eligible only if every
    data gate passes (fresh stock and regime bars at ``as_of``, actionable quality reports for
    both, same feed, not the regime benchmark), all six conditions pass and the score is
    defined. ``quality`` should come from ``assess_quality`` with ``lookback_sessions >=
    params.required_history()``; when it is ``None`` the missing-session and feed gates cannot
    be verified, so every symbol is ineligible (scores are still computed for display).
    ``expected_sessions`` is the exchange session calendar covering the supplied bars up to
    ``as_of``; it makes a session missing from BOTH the stock and the regime series an
    undefined row (see :func:`compute_features`). When it is ``None``, or ``as_of`` is not one
    of its sessions, every symbol is ineligible. An eligible symbol whose proposed stop
    ``close - stop_atr_multiple * ATR`` is ``<= 0`` is made ineligible (no valid stop exists).
    Ineligible symbols follow the ranked ones in symbol order.
    """
    if isinstance(as_of, datetime) or not isinstance(as_of, date):
        raise TypeError("as_of must be a datetime.date session, not a datetime")
    if isinstance(tradable, str):
        raise TypeError("tradable must be a sequence of symbols, not a string")
    symbols = list(tradable)
    duplicates = sorted({s for s in symbols if symbols.count(s) > 1})
    if duplicates:
        raise ValueError(f"duplicate tradable symbols: {duplicates}")

    expected = None if expected_sessions is None else _expected_index(expected_sessions)

    regime_frame, regime_reasons = _completed(bars.get(regime_symbol), regime_symbol, as_of)
    regime_reasons = [f"regime benchmark {r}" for r in regime_reasons]
    if expected is None:
        regime_reasons.append(
            "no expected session calendar supplied (sessions missing from both the stock "
            "and regime series cannot be detected)"
        )
    elif pd.Timestamp(as_of) not in expected:
        regime_reasons.append(f"as_of {as_of} is not an expected trading session")
    reference_feed: object = None
    if quality is None:
        regime_reasons.append(
            "no data quality reports supplied (missing-session and feed gates unverified)"
        )
    else:
        regime_quality = quality.get(regime_symbol)
        reference_feed = None if regime_quality is None else regime_quality.feed
        regime_reasons += [
            f"regime benchmark {r}"
            for r in _quality_reasons(regime_symbol, as_of, quality, reference_feed)
        ]

    evaluations = [
        _evaluate_symbol(
            symbol,
            bars.get(symbol),
            regime_symbol=regime_symbol,
            regime_frame=regime_frame,
            regime_reasons=regime_reasons,
            params=params,
            as_of=as_of,
            quality=quality,
            reference_feed=reference_feed,
            expected=expected,
        )
        for symbol in sorted(symbols)
    ]
    eligible_sorted = sorted(
        (e for e in evaluations if e.eligible),
        key=lambda e: (-(e.score if e.score is not None else 0.0), e.symbol),
    )
    ranked = [replace(e, rank=i) for i, e in enumerate(eligible_sorted, start=1)]
    return ranked + [e for e in evaluations if not e.eligible]


def _evaluate_symbol(
    symbol: str,
    raw_frame: pd.DataFrame | None,
    *,
    regime_symbol: str,
    regime_frame: pd.DataFrame | None,
    regime_reasons: Sequence[str],
    params: MomentumV1Params,
    as_of: date,
    quality: Mapping[str, QualityReport] | None,
    reference_feed: object,
    expected: pd.DatetimeIndex | None,
) -> SignalEvaluation:
    reasons = list(regime_reasons)
    if symbol == regime_symbol:
        reasons.append(f"{symbol}: regime benchmark is never a candidate")
    frame, frame_reasons = _completed(raw_frame, symbol, as_of)
    reasons += frame_reasons
    reasons += _quality_reasons(symbol, as_of, quality, reference_feed)

    values: dict[str, float | None] = {}
    session: date | None = None
    if frame is not None:
        session = frame.index[-1].date()
        if regime_frame is not None:
            try:
                features = compute_features(frame, regime_frame, params, expected_sessions=expected)
            except DataSchemaError as exc:
                features = None
                reasons.append(f"{symbol}: {exc}")
            as_of_ts = pd.Timestamp(as_of)
            if features is not None and as_of_ts in features.index:
                values = {
                    column: _opt(features.at[as_of_ts, column])
                    for column in FEATURE_COLUMNS
                    if not column.startswith(("cond_", "all_"))
                }

    conditions = _condition_results(values, params, regime_symbol)
    for cond in conditions:
        if cond.value is None or cond.threshold is None:
            reasons.append(f"{cond.name}: undefined input ({cond.description})")
        elif not cond.passed:
            reasons.append(f"{cond.name}: failed ({cond.description})")

    components = {
        name: value for name in SCORE_COMPONENTS if (value := values.get(name)) is not None
    }
    score = values.get("score")
    if score is None:
        missing = [name for name in SCORE_COMPONENTS if name not in components]
        reasons.append(f"score: undefined (missing components: {', '.join(missing)})")
    close = values.get("close")
    atr_value = values.get("atr")

    if not reasons and close is not None and atr_value is not None:
        candidate_stop = close - params.stop_atr_multiple * atr_value
        if not candidate_stop > 0:
            reasons.append(
                f"proposed stop {candidate_stop:.6g} = close {close:.6g} - "
                f"{params.stop_atr_multiple:g} x ATR {atr_value:.6g} is not positive"
            )
    eligible = not reasons
    stop = target = None
    if eligible and close is not None and atr_value is not None:
        stop = close - params.stop_atr_multiple * atr_value
        target = close + params.target_atr_multiple * atr_value
    return SignalEvaluation(
        symbol=symbol,
        session=session,
        strategy=STRATEGY_NAME,
        strategy_version=params.version,
        eligible=eligible,
        conditions=conditions,
        score=score,
        score_components=MappingProxyType(components),
        close=close,
        atr=atr_value,
        proposed_stop=stop,
        proposed_target=target,
        rejection_reasons=tuple(reasons),
        rank=None,
    )
