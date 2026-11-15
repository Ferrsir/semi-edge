"""closing_momentum_v2 (#169): first-half-hour conditions, timing, no look-ahead, gates."""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from semiedge import cli
from semiedge.backtest import closing_momentum as cm
from semiedge.backtest import intraday_dip as idip
from semiedge.backtest.intraday_dip import DaySession
from semiedge.backtest.intraday_signals import SessionBars

N = 78


def _flat(price: float, n: int = N) -> SessionBars:
    p = np.full(n, price)
    return SessionBars(p.copy(), p + 0.1, p - 0.1, p.copy(), np.full(n, 1000.0))


def _signals(stock_up=True, smh_up=True, stronger=True, **kw):
    b, smh = _flat(102.0), _flat(505.0)
    b.c[5] = 102.0 if stock_up else 99.0  # PC = 100
    smh.c[5] = (501.0 if stronger else 520.0) if smh_up else 495.0  # PC_smh = 500
    args = {"pc": 100.0, "pc_smh": 500.0, "atr_d1": 4.0, **kw}
    return b, cm.session_signals(b, smh, **args)


def test_both_up_and_stronger_signals_m1_and_m2_at_1530():
    _, sig = _signals()
    (m1,) = sig["M1"][71]
    assert 71 in sig["M2"]
    assert m1.stop == pytest.approx(102.0 - 1.0)  # 0.25 x ATR 4
    assert m1.r0 == pytest.approx(1.0)
    assert math.isinf(m1.target)
    assert 71 in sig["baseline"]


def test_weaker_than_smh_is_m1_only():
    _, sig = _signals(stronger=False)  # SMH +4% vs stock +2%
    assert 71 in sig["M1"]
    assert sig["M2"] == {}


@pytest.mark.parametrize(("stock_up", "smh_up"), [(False, True), (True, False), (False, False)])
def test_a_down_first_half_hour_never_signals(stock_up, smh_up):
    _, sig = _signals(stock_up=stock_up, smh_up=smh_up)
    assert sig["M1"] == {}
    assert sig["M2"] == {}
    assert 71 in sig["baseline"]


def test_early_close_and_missing_inputs_never_signal():
    b, smh = _flat(102.0, n=42), _flat(505.0, n=42)
    assert cm.session_signals(b, smh, pc=100.0, pc_smh=500.0, atr_d1=4.0)["baseline"] == {}
    for kw in ({"pc": None}, {"pc_smh": None}, {"atr_d1": None}):
        assert _signals(**kw)[1]["baseline"] == {}
    b, smh = _flat(102.0), _flat(505.0)
    b.c[40] = np.nan
    assert cm.session_signals(b, smh, pc=100.0, pc_smh=500.0, atr_d1=4.0)["baseline"] == {}


def test_signal_uses_no_bars_after_1530():
    b, sig = _signals()
    cut = SessionBars(
        *(np.where(np.arange(N) <= 71, x, np.nan) for x in (b.o, b.h, b.lo, b.c, b.v))
    )
    smh = _flat(505.0)
    smh.c[5] = 501.0
    assert cm.session_signals(cut, smh, pc=100.0, pc_smh=500.0, atr_d1=4.0) == sig


def test_entry_at_1530_and_flat_at_1555():
    b, sig = _signals()
    b.o[72:], b.c[72:], b.h[72:], b.lo[72:] = 102.5, 102.5, 102.6, 102.4
    named = {
        v: {k: [idip.Signal("NVDA", s.k, s.stop, s.target, s.r0, s.close, s.score) for s in f]}
        for v, by_k in sig.items()
        for k, f in by_k.items()
    }
    (t,) = idip.signal_study(
        [DaySession(date(2026, 3, 2), N, {"NVDA": b}, named)], "M1", slippage_bps=0.0
    )
    assert (t.signal_bar, t.exit_bar, t.reason) == (71, 77, "flat_1555")
    assert t.r_multiple == pytest.approx(0.0)


def test_periods_and_decision_level():
    seen_start, seen_end = cm.SEEN_WINDOW
    for start, end in (cm.DEVELOPMENT, cm.HOLDOUT):
        assert end < seen_start or start > seen_end
    assert cm.DEVELOPMENT[0] == date(2026, 1, 2)
    assert cm.SELECTION_SLIPPAGE == 10.0


def _s(n, m, t):
    return {"trades": n, "mean_r": m, "mean_r_without_top5": t}


def test_gates():
    base = _s(2000, 0.0, 0.0)
    assert (
        cm.select_variant({"M1": _s(150, 0.02, 0.01), "M2": _s(149, 0.5, 0.4), "baseline": base})[0]
        == "M1"
    )
    assert cm.holdout_pass({"M1": _s(80, 0.02, 0.01), "baseline": base}, "M1", 1.0) == []
    assert cm.holdout_pass({"M1": _s(79, 0.02, 0.01), "baseline": base}, "M1", 1.0) == [
        "fewer than 80 trades"
    ]


def test_cli_guards(config_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path / "data"))
    base = ["backtest-closing-momentum", "--config-dir", str(config_dir)]
    assert cli.main([*base, "--period", "holdout"]) != 0
    assert cli.main([*base, "--period", "development"]) != 0
