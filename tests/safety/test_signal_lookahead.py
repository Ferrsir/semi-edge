"""No-look-ahead invariants for indicators and ``momentum_v1`` (momentum_v1.md, rules 1-3).

For every session ``t``: (a) recomputing on data truncated at ``t`` gives bit-identical values
at ``t``; (b) arbitrarily perturbing every bar AFTER ``t`` leaves all values ``<= t`` unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import pytest

from semiedge.config.models import MomentumV1Params
from semiedge.data.schema import DataFeed
from semiedge.indicators import (
    atr,
    prior_rolling_max,
    prior_rolling_mean,
    rolling_fraction_true,
    sma,
    trailing_return,
    true_range,
)
from semiedge.strategy import compute_features, evaluate_universe

pytestmark = pytest.mark.safety

N = 180
PARAMS = MomentumV1Params()


def _bars(seed: int, n: int = N, drift: float = 0.002) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.02, n)))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.02, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.02, n))
    volume = rng.lognormal(14, 0.5, n)
    index = pd.bdate_range("2023-01-02", periods=n, name="session").as_unit("ns")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=index
    )


def _perturb_after(frame: pd.DataFrame, t: int, seed: int) -> pd.DataFrame:
    """Scale every bar after position ``t`` by a random (consistent) factor."""
    rng = np.random.default_rng(seed)
    out = frame.copy()
    k = len(frame) - t - 1
    price_factor = np.concatenate([np.ones(t + 1), rng.uniform(0.3, 3.0, k)])
    for column in ("open", "high", "low", "close"):
        out[column] = frame[column].to_numpy() * price_factor
    out["volume"] = frame["volume"].to_numpy() * np.concatenate(
        [np.ones(t + 1), rng.uniform(0.0, 10.0, k)]
    )
    return out


CUTS = (0, 1, 13, 19, 20, 49, 50, 67, 68, 69, 100, N - 2)


@pytest.mark.parametrize("t", CUTS)
def test_features_truncation_invariance(t: int) -> None:
    stock, regime = _bars(1), _bars(2, drift=0.001)
    full = compute_features(stock, regime, PARAMS)
    cut = compute_features(stock.iloc[: t + 1], regime.iloc[: t + 1], PARAMS)
    pd.testing.assert_frame_equal(cut, full.iloc[: t + 1], check_exact=True)


@pytest.mark.parametrize("t", CUTS)
def test_features_perturb_the_future(t: int) -> None:
    stock, regime = _bars(3), _bars(4, drift=0.001)
    base = compute_features(stock, regime, PARAMS)
    moved = compute_features(
        _perturb_after(stock, t, 10 + t), _perturb_after(regime, t, 20 + t), PARAMS
    )
    pd.testing.assert_frame_equal(moved.iloc[: t + 1], base.iloc[: t + 1], check_exact=True)
    # Sanity: the perturbation really changed the future.
    assert not moved["close"].iloc[t + 1 :].equals(base["close"].iloc[t + 1 :])


def test_future_missing_session_does_not_leak_backwards() -> None:
    stock, regime = _bars(5), _bars(6)
    t = 120
    base = compute_features(stock, regime, PARAMS)
    holed = compute_features(stock.drop(index=stock.index[t + 3]), regime, PARAMS)
    pd.testing.assert_frame_equal(holed.iloc[: t + 1], base.iloc[: t + 1], check_exact=True)


@pytest.mark.parametrize("t", [5, 30, 100, N - 2])
def test_indicators_perturb_the_future(t: int) -> None:
    bars = _bars(7)
    moved = _perturb_after(bars, t, 99)
    h, lo, c, v = (bars[k] for k in ("high", "low", "close", "volume"))
    mh, ml, mc, mv = (moved[k] for k in ("high", "low", "close", "volume"))
    mask = (c > sma(c, 20)).astype("float64")
    moved_mask = (mc > sma(mc, 20)).astype("float64")
    pairs = [
        (sma(c, 20), sma(mc, 20)),
        (true_range(h, lo, c), true_range(mh, ml, mc)),
        (atr(h, lo, c, 14), atr(mh, ml, mc, 14)),
        (prior_rolling_max(h, 20), prior_rolling_max(mh, 20)),
        (prior_rolling_mean(v, 20), prior_rolling_mean(mv, 20)),
        (trailing_return(c, 20), trailing_return(mc, 20)),
        (rolling_fraction_true(mask, 20), rolling_fraction_true(moved_mask, 20)),
    ]
    for base, perturbed in pairs:
        pd.testing.assert_series_equal(
            perturbed.iloc[: t + 1], base.iloc[: t + 1], check_exact=True
        )


def test_prior_window_baselines_exclude_current_bar() -> None:
    bars = _bars(8)
    t = 90
    spiked = bars.copy()
    spiked.loc[spiked.index[t], "high"] = bars["high"].iloc[t] * 10
    spiked.loc[spiked.index[t], "volume"] = bars["volume"].iloc[t] * 10
    assert (
        prior_rolling_max(spiked["high"], 20).iloc[t] == prior_rolling_max(bars["high"], 20).iloc[t]
    )
    assert (
        prior_rolling_mean(spiked["volume"], 20).iloc[t]
        == prior_rolling_mean(bars["volume"], 20).iloc[t]
    )


@dataclass(frozen=True)
class _Quality:
    symbol: str
    as_of: date
    feed: DataFeed | None = DataFeed.SIP
    issues: tuple[str, ...] = ()
    is_actionable: bool = True


@pytest.mark.parametrize("t", [60, 100, 150])
def test_evaluate_universe_ignores_bars_after_as_of(t: int) -> None:
    bars = {"SMH": _bars(9, drift=0.001), "AAA": _bars(10), "BBB": _bars(11, drift=0.004)}
    as_of = bars["SMH"].index[t].date()
    quality = {s: _Quality(s, as_of) for s in bars}
    future = {s: _perturb_after(f, t, 30 + i) for i, (s, f) in enumerate(bars.items())}
    past = {s: f.iloc[: t + 1] for s, f in bars.items()}
    results = [
        evaluate_universe(
            b,
            "SMH",
            PARAMS,
            as_of,
            tradable=["AAA", "BBB"],
            quality=quality,
            expected_sessions=bars["SMH"].index,
        )
        for b in (bars, future, past)
    ]
    assert results[0] == results[1] == results[2]
