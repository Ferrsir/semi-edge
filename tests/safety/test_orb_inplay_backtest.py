"""orb_inplay_v1 (#145): in-play signals, relative volume, periods and gates. Hand-built bars."""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from semiedge import cli
from semiedge.backtest import intraday_dip as idip
from semiedge.backtest import orb_inplay as orb
from semiedge.backtest.intraday_dip import DaySession
from semiedge.backtest.intraday_signals import SessionBars

N = 78
HISTORY = [1000.0] * 14


def _day(v0: float = 3000.0, *, green: bool = True) -> SessionBars:
    p = np.full(N, 100.0)
    b = SessionBars(p.copy(), p + 0.2, p - 0.2, p.copy(), np.full(N, 1000.0))
    b.o[0], b.c[0], b.h[0], b.lo[0], b.v[0] = 100.0, 100.6 if green else 99.6, 100.8, 99.5, v0
    return b


def test_in_play_green_candle_signals_both_variants():
    sig = orb.session_signals(_day(), HISTORY, 4.0)
    (o1,) = sig["O1"][0]
    (o2,) = sig["O2"][0]
    assert o1.stop == pytest.approx(100.6 - 0.4)  # 10% of the daily ATR
    assert o2.stop == pytest.approx(99.5)  # the first candle's low
    assert math.isinf(o1.target)
    assert o1.score == pytest.approx(-3.0)
    assert 0 in sig["baseline"]


def test_not_in_play_only_the_baseline_signals():
    sig = orb.session_signals(_day(v0=900.0), HISTORY, 4.0)
    assert sig["O1"] == {}
    assert sig["O2"] == {}
    assert 0 in sig["baseline"]


def test_red_first_candle_never_signals():
    assert orb.session_signals(_day(green=False), HISTORY, 4.0) == {
        "O1": {},
        "O2": {},
        "baseline": {},
    }


def test_rvol_needs_ten_of_fourteen_sessions():
    assert orb.rvol(2000.0, [1000.0] * 9 + [math.nan] * 5) is None
    assert orb.rvol(2000.0, [1000.0] * 10 + [math.nan] * 4) == 2.0
    assert orb.rvol(2000.0, [500.0] * 30 + [1000.0] * 14) == 2.0  # only the last 14 count


def _named(sig, sym="NVDA"):
    return {
        v: {k: [idip.Signal(sym, s.k, s.stop, s.target, s.r0, s.close, s.score) for s in f]}
        for v, by_k in sig.items()
        for k, f in by_k.items()
    }


def test_trades_are_held_to_the_flatten_or_the_stop():
    b = _day()
    b.c[1:], b.o[1:], b.h[1:], b.lo[1:] = 101.0, 101.0, 101.2, 100.8
    ds = DaySession(date(2026, 3, 2), N, {"NVDA": b}, _named(orb.session_signals(b, HISTORY, 4.0)))
    (t,) = idip.signal_study([ds], "O1", slippage_bps=0.0)
    assert (t.reason, t.exit_bar) == ("flat_1555", N - 1)
    assert t.r_multiple == pytest.approx((101.0 - 101.0) / 0.4)
    b2 = _day()
    b2.c[1:], b2.o[1:], b2.h[1:], b2.lo[1:] = 101.0, 101.0, 101.2, 100.8
    b2.lo[5] = 100.0  # through the 100.2 stop
    ds2 = DaySession(
        date(2026, 3, 2), N, {"NVDA": b2}, _named(orb.session_signals(b2, HISTORY, 4.0))
    )
    (t2,) = idip.signal_study([ds2], "O1", slippage_bps=0.0)
    assert (t2.reason, t2.exit_bar) == ("stop", 5)


def test_history_uses_only_prior_sessions(config_dir: Path):
    """A session's own first bar never enters its RVOL average."""
    assert orb.rvol(1000.0, [1000.0] * 14) == 1.0
    sig = orb.session_signals(_day(v0=1000.0), [1000.0] * 14, 4.0)
    assert 0 in sig["O1"]  # RVOL exactly 1.0 qualifies


def test_periods_are_the_current_regime_and_exclude_the_seen_window():
    seen_start, seen_end = orb.SEEN_WINDOW
    assert orb.DEVELOPMENT[0].year == 2026
    for start, end in (orb.DEVELOPMENT, orb.HOLDOUT):
        assert end < seen_start or start > seen_end
    assert orb.DEVELOPMENT[1] < orb.HOLDOUT[0]
    assert orb.SELECTION_SLIPPAGE == 10.0


def _s(n: int, m: float, trimmed: float) -> dict[str, float]:
    return {"trades": n, "mean_r": m, "mean_r_without_top5": trimmed}


def test_gates():
    base = _s(500, 0.0, 0.0)
    chosen, _ = orb.select_variant(
        {"O1": _s(60, 0.05, 0.01), "O2": _s(80, 0.04, 0.02), "baseline": base}
    )
    assert chosen == "O1"
    chosen, _ = orb.select_variant(
        {"O1": _s(59, 0.05, 0.01), "O2": _s(80, -0.01, 0.02), "baseline": base}
    )
    assert chosen is None
    assert orb.holdout_pass({"O2": _s(40, 0.05, 0.01), "baseline": base}, "O2", 1.0) == []


def test_cli_guards(config_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path / "data"))
    base = ["backtest-orb-inplay", "--config-dir", str(config_dir)]
    assert cli.main([*base, "--period", "holdout"]) != 0
    assert cli.main([*base, "--period", "development"]) != 0
