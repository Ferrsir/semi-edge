"""gap_reversal_v1 (#141): gap signals, no look-ahead, periods and gates. Hand-built bars only."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from semiedge import cli
from semiedge.backtest import gap_reversal as gr
from semiedge.backtest.intraday_signals import SessionBars, vwap

N = 78


def _gap_day(open_: float = 90.0) -> SessionBars:
    """Previous close 100 (fixed by the tests), a gap open, a dip, then a reclaim."""
    c = np.full(N, open_ + 3.0)
    b = SessionBars(c.copy(), c + 0.2, c - 0.2, c.copy(), np.full(N, 1000.0))
    b.o[0], b.h[0], b.lo[0], b.c[0] = open_, open_ + 0.3, open_ - 1.0, open_ - 0.5
    b.o[1], b.h[1], b.lo[1], b.c[1] = open_ - 0.5, open_, open_ - 1.5, open_ - 1.0
    b.o[2], b.h[2], b.lo[2], b.c[2] = open_ - 1.0, open_ + 0.5, open_ - 1.2, open_ + 0.4
    b.o[3:6], b.h[3:6], b.lo[3:6], b.c[3:6] = open_ + 0.4, open_ + 1.2, open_ + 0.3, open_ + 1.0
    return b


def test_gap_day_signals_for_each_variant_and_the_baseline():
    b = _gap_day()
    sig = gr.session_signals(b, 100.0, 5.0)  # gap of 10 = 2 ATR
    (base,) = sig["baseline"][0]
    assert base.stop == pytest.approx(89.0)
    assert base.target == pytest.approx(95.0)  # half the gap
    g1_k = min(sig["G1"])
    vw = vwap(b)
    assert float(b.c[g1_k]) > float(vw[g1_k])
    assert all(float(b.c[k]) <= float(vw[k]) for k in range(1, g1_k))
    (g2,) = sig["G2"][3]  # first close above the 15-minute high of 90.5
    assert g2.stop == pytest.approx(88.5)  # the low of the day so far
    assert base.score == pytest.approx(-2.0)


def test_no_signal_without_a_gap_of_one_atr():
    sig = gr.session_signals(_gap_day(), 100.0, 10.5)  # gap 10 < 1 ATR of 10.5
    assert sig == {"G1": {}, "G2": {}, "baseline": {}}


def test_signal_skipped_once_half_the_gap_is_filled():
    b = _gap_day()
    b.c[3:6] = 95.5  # already above the 95 target when G2 would fire
    b.h[3:6] = 95.7
    sig = gr.session_signals(b, 100.0, 5.0)
    assert sig["G2"] == {}


def test_only_the_first_qualifying_bar_is_a_signal():
    sig = gr.session_signals(_gap_day(), 100.0, 5.0)
    assert len(sig["G1"]) <= 1
    assert len(sig["G2"]) == 1


def test_no_prior_data_means_no_signal():
    assert gr.session_signals(_gap_day(), None, 5.0)["baseline"] == {}
    assert gr.session_signals(_gap_day(), 100.0, None)["baseline"] == {}


def test_signals_use_no_future_bars():
    seen = 0
    for seed in range(10):
        rng = np.random.default_rng(seed)
        c = 90 + np.cumsum(rng.normal(0.05, 0.4, N))
        o = c + rng.normal(0, 0.15, N)
        o[0] = 89.5
        h = np.maximum(o, c) + rng.uniform(0, 0.3, N)
        lo = np.minimum(o, c) - rng.uniform(0, 0.3, N)
        v = rng.uniform(500, 3000, N)
        full = SessionBars(o, h, lo, c, v)
        base = gr.session_signals(full, 100.0, 5.0)
        for k in range(0, 30):
            cut = SessionBars(*(np.where(np.arange(N) <= k, x, np.nan) for x in (o, h, lo, c, v)))
            got = gr.session_signals(cut, 100.0, 5.0)
            for variant in ("G1", "G2", "baseline"):
                if k in base[variant]:
                    assert got[variant].get(k) == base[variant][k], (seed, k, variant)
                    seen += 1
    assert seen > 0


def test_the_inspected_window_is_outside_both_test_periods():
    seen_start, seen_end = gr.SEEN_WINDOW
    for start, end in (gr.DEVELOPMENT, gr.HOLDOUT):
        assert end < seen_start or start > seen_end
    assert (date(2026, 1, 2), date(2026, 10, 23)) == gr.HOLDOUT


def _s(n: int, m: float, trimmed: float) -> dict[str, float]:
    return {"trades": n, "mean_r": m, "mean_r_without_top5": trimmed}


def test_gates_use_the_gap_trade_minimums():
    base = _s(100, 0.0, 0.0)
    assert (
        gr.select_variant({"G1": _s(40, 0.1, 0.05), "G2": _s(39, 0.3, 0.2), "baseline": base})[0]
        == "G1"
    )
    assert gr.holdout_pass({"G2": _s(25, 0.1, 0.05), "baseline": base}, "G2", 1.0) == []
    assert gr.holdout_pass({"G2": _s(24, 0.1, 0.05), "baseline": base}, "G2", 1.0) == [
        "fewer than 25 trades"
    ]


def test_cli_holdout_needs_a_variant(config_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path / "data"))
    base = ["backtest-gap-reversal", "--config-dir", str(config_dir)]
    assert cli.main([*base, "--period", "holdout"]) != 0
    assert cli.main([*base, "--period", "development"]) != 0  # no SMH cache
