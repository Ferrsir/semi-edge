"""dip_v1 backtest (#133): indicators, signals, exits, account models and the pre-registered gates.

Hand-built bars and synthetic fixtures only; no network and no cached market data.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from semiedge import cli
from semiedge.backtest import dip
from semiedge.backtest.dip import Panel
from semiedge.config.loader import load_risk_profile
from semiedge.data.schema import Adjustment, DataFeed, DataSource
from semiedge.data.store import ParquetBarStore
from semiedge.data.synthetic import SyntheticMarketDataSource

D0 = date(2020, 1, 6)


@pytest.fixture
def limits_5k(config_dir: Path):
    return load_risk_profile(config_dir, "5k")


# ------------------------------------------------------------------ indicators


def test_rsi2_follows_the_spec_definition():
    close = pd.Series([10.0, 11.0, 10.0, 9.0, 9.5])
    r = dip.rsi2(close).to_numpy()
    assert np.isnan(r[:2]).all()
    # seed after two changes: gains (1, 0) -> 0.5, losses (0, 1) -> 0.5 -> RSI 50
    assert r[2] == pytest.approx(50.0)
    # next: gain 0, loss 1 -> avg_g 0.25, avg_l 0.75 -> 25
    assert r[3] == pytest.approx(25.0)
    # next: gain 0.5 -> avg_g 0.375, avg_l 0.375 -> 50
    assert r[4] == pytest.approx(50.0)


def test_rsi2_is_100_without_losses_and_restarts_after_a_gap():
    r = dip.rsi2(pd.Series([1.0, 2.0, 3.0, np.nan, 4.0, 5.0, 6.0])).to_numpy()
    assert r[2] == 100.0
    assert np.isnan(r[3:6]).all()  # seeding restarts: two defined changes after the gap
    assert r[6] == 100.0


# ------------------------------------------------------------------ hand-built panel


def _panel(n: int, symbols: tuple[str, ...] = ("NVDA",), price: float = 100.0) -> Panel:
    sessions = [D0 + timedelta(days=k) for k in range(n)]

    def arr(v: float) -> dict[str, np.ndarray]:
        return {s: np.full(n, v) for s in symbols}

    return Panel(
        sessions,
        symbols,
        arr(price),
        arr(price),
        arr(price),
        arr(price),
        arr(price + 1.0),  # SMA5 above the close: no bounce exit unless a test sets one
        arr(2.0),
        arr(50.0),
        arr(0.0),
        {s: np.zeros(n, dtype=bool) for s in symbols},
        np.full(n, 300.0),
        np.full(n, 300.0),
    )


def _signal(p: Panel, sym: str, i: int, *, rsi: float = 5.0) -> None:
    p.shared[sym][i] = True
    p.rsi[sym][i] = rsi  # stop = 100 - 2 * 2 = 96


def _study(p: Panel, slip: float = 0.0) -> list[dip.StudyTrade]:
    return dip.signal_study(p, "S1", start=p.sessions[0], end=p.sessions[-1], slippage_bps=slip)


def test_bounce_exit_sells_at_the_next_open():
    p = _panel(10)
    _signal(p, "NVDA", 1)
    p.c["NVDA"][3] = 101.0  # the close of session 3 is above its SMA5
    p.sma5["NVDA"][3] = 100.5
    p.o["NVDA"][4] = 102.0
    (t,) = _study(p)
    assert (t.entry_session, t.exit_session, t.reason) == (
        p.sessions[2].isoformat(),
        p.sessions[4].isoformat(),
        "bounce",
    )
    assert t.r_multiple == pytest.approx((102 - 100) / 4)


def test_time_exit_after_three_held_sessions():
    p = _panel(10)
    _signal(p, "NVDA", 1)
    (t,) = _study(p)
    assert t.reason == "time"
    assert t.exit_session == p.sessions[5].isoformat()  # held 2, 3, 4; sold at the open of 5


def test_stop_is_live_on_the_entry_session():
    p = _panel(10)
    _signal(p, "NVDA", 1)
    p.lo["NVDA"][2] = 95.0
    (t,) = _study(p)
    assert (t.reason, t.exit_session, t.r_multiple) == ("stop", p.sessions[2].isoformat(), -1.0)


def test_gap_through_the_stop_sells_at_the_open():
    p = _panel(10)
    _signal(p, "NVDA", 1)
    p.o["NVDA"][3], p.lo["NVDA"][3] = 94.0, 93.0
    (t,) = _study(p)
    assert t.reason == "stop_gap"
    assert t.r_multiple == pytest.approx((94 - 100) / 4)  # planned risk C(t) - stop = 4


def test_r_uses_the_risk_planned_at_the_signal():
    """Revision 1.1: an open that gaps toward the stop cannot inflate R."""
    p = _panel(10)
    _signal(p, "NVDA", 1)
    p.o["NVDA"][2] = 96.5  # fill 0.5 above the stop; planned risk is still 4
    p.sma5["NVDA"][2] = 95.0
    p.o["NVDA"][3] = 98.5
    (t,) = _study(p)
    assert t.reason == "bounce"
    assert t.r_multiple == pytest.approx((98.5 - 96.5) / 4)  # not (98.5 - 96.5) / 0.5


def test_placeholder_bars_are_missing():
    frames = _daily(("NVDA", "SMH"), date(2019, 1, 2), date(2019, 12, 31))
    nvda = frames["NVDA"].copy()
    rows = nvda.index[100:110]
    nvda.loc[rows, ["open", "high", "low", "close"]] = 50.0
    nvda.loc[rows, "volume"] = 0.0
    p = dip.prepare_panel(
        {**frames, "NVDA": nvda}, tradable=("NVDA",), benchmark="SMH", end=date(2019, 12, 31)
    )
    assert np.isnan(p.c["NVDA"][100:110]).all()
    assert not p.shared["NVDA"][100:161].any()
    busy = nvda.copy()
    busy.loc[rows, "volume"] = 5_000.0  # a real zero-range session on volume is kept
    q = dip.prepare_panel(
        {**frames, "NVDA": busy}, tradable=("NVDA",), benchmark="SMH", end=date(2019, 12, 31)
    )
    assert (q.c["NVDA"][100:110] == 50.0).all()


def test_entry_opening_at_or_below_the_stop_is_cancelled():
    p = _panel(10)
    _signal(p, "NVDA", 1)
    p.o["NVDA"][2] = 96.0
    assert _study(p) == []


def test_slippage_applies_to_both_legs():
    p = _panel(10)
    _signal(p, "NVDA", 1)
    (t,) = _study(p, slip=20.0)
    fill, sale = 100 * 1.002, 100 * 0.998
    assert t.r_multiple == pytest.approx((sale - fill) / 4.0, abs=1e-4)  # planned risk


def test_an_open_trade_closes_at_the_end_of_the_window():
    p = _panel(4)
    _signal(p, "NVDA", 1)
    (t,) = _study(p)
    assert (t.reason, t.exit_session) == ("end_of_window", p.sessions[3].isoformat())


def test_signals_need_the_shared_conditions_and_rank_lowest_first():
    p = _panel(5, ("AMD", "MU", "NVDA"))
    _signal(p, "NVDA", 1, rsi=3.0)
    _signal(p, "AMD", 1, rsi=8.0)
    p.rsi["MU"][1] = 1.0  # oversold but shared conditions fail
    _signal(p, "AMD", 2, rsi=10.0)  # not below 10
    assert [s.symbol for s in dip.signals_at(p, "S1", 1)] == ["NVDA", "AMD"]
    assert dip.signals_at(p, "S1", 2) == []
    p.rel_drop["AMD"][1] = -1.5
    p.rel_drop["NVDA"][1] = -1.49
    assert [s.symbol for s in dip.signals_at(p, "S2", 1)] == ["AMD"]
    assert {s.symbol for s in dip.signals_at(p, "baseline", 1)} == {"AMD", "NVDA"}


# ------------------------------------------------------------------ real indicators, no look-ahead


def _daily(symbols: tuple[str, ...], start: date, end: date) -> dict[str, pd.DataFrame]:
    return SyntheticMarketDataSource().get_daily_bars(list(symbols), start, end)


def test_panel_uses_no_bars_after_its_end():
    frames = _daily(("NVDA", "AMD", "SMH"), date(2019, 1, 2), date(2020, 6, 30))
    kw = {"tradable": ("NVDA", "AMD"), "benchmark": "SMH"}
    full = dip.prepare_panel(frames, end=date(2020, 6, 30), **kw)
    cut = dip.prepare_panel(frames, end=date(2020, 1, 31), **kw)
    assert cut.sessions[-1] <= date(2020, 1, 31)
    n = len(cut.sessions)
    assert full.sessions[:n] == cut.sessions
    for name in ("c", "sma5", "atr14", "rsi", "rel_drop", "shared"):
        for sym in ("NVDA", "AMD"):
            a, b = getattr(full, name)[sym][:n], getattr(cut, name)[sym]
            np.testing.assert_array_equal(a, b, err_msg=f"{name} {sym}")


def test_eligibility_needs_60_sessions_and_a_full_50_session_window():
    frames = _daily(("NVDA", "SMH"), date(2019, 1, 2), date(2019, 12, 31))
    p = dip.prepare_panel(frames, tradable=("NVDA",), benchmark="SMH", end=date(2019, 12, 31))
    assert not p.shared["NVDA"][:59].any()
    nvda = frames["NVDA"].drop(frames["NVDA"].index[150])
    gap = dip.prepare_panel(
        {**frames, "NVDA": nvda}, tradable=("NVDA",), benchmark="SMH", end=date(2019, 12, 31)
    )
    assert not gap.shared["NVDA"][150:201].any()  # every window touching the gap is refused


# ------------------------------------------------------------------ portfolio replay


def _replay(p: Panel, limits, *, account="unrestricted", equity=5000.0, slip=0.0):
    return dip.replay(
        p,
        variant="S1",
        start=p.sessions[0],
        end=p.sessions[-1],
        start_equity=equity,
        limits=limits,
        account=account,
        slippage_bps=slip,
    )


def test_portfolio_sizes_by_risk_and_caps(limits_5k):
    p = _panel(10)
    _signal(p, "NVDA", 1)
    r = _replay(p, limits_5k)
    (t,) = r.trades
    # risk budget $50 / $4 = 12 shares; position cap $1,250 / $100 = 12 shares
    assert t.shares == 12
    assert r.summary()["net_pnl"] == 0.0


@pytest.mark.parametrize("account", ["unrestricted", "margin_pdt", "cash"])
@pytest.mark.parametrize("slip", [0.0, 20.0])
def test_gap_up_fill_caps_stop_risk_but_preserves_planned_r(limits_5k, account, slip):
    p = _panel(10)
    _signal(p, "NVDA", 1)  # planned R = 4; stop = 96
    for values in (p.o, p.h, p.lo, p.c):
        values["NVDA"][2:] = 110.0
    p.lo["NVDA"][2] = 96.0
    (t,) = _replay(p, limits_5k, account=account, slip=slip).trades
    assert t.shares * (t.entry - t.stop) <= 50.0
    assert t.shares == 3
    assert t.reason == "stop"
    assert t.r_multiple == pytest.approx((t.exit - t.entry) / 4.0, abs=1e-4)


@pytest.mark.parametrize("account", ["unrestricted", "margin_pdt", "cash"])
def test_gap_up_fill_refuses_a_share_over_budget(limits_5k, account):
    p = _panel(10)
    _signal(p, "NVDA", 1)
    for values in (p.o, p.h, p.lo, p.c):
        values["NVDA"][2:] = 160.0  # actual R = 64 > $50 budget
    r = _replay(p, limits_5k, account=account)
    assert r.trades == []
    assert r.refusals["fill_over_cap"] == 1
    assert all(equity == 5000.0 for _, equity in r.session_equity)


def test_max_positions_and_ranking(limits_5k):
    syms = ("AMD", "AVGO", "MU", "NVDA")
    p = _panel(10, syms)
    for sym, rsi in zip(syms, (4.0, 3.0, 2.0, 1.0), strict=True):
        _signal(p, sym, 1, rsi=rsi)
    r = _replay(p, limits_5k)
    assert sorted(t.symbol for t in r.trades) == ["AVGO", "MU", "NVDA"]
    assert r.refusals["max_positions"] == 1


def test_margin_pdt_defers_a_fourth_entry_day_stop(limits_5k):
    p = _panel(20)
    for k in (1, 2, 3, 4):  # four entries in a row, each stopped out on its entry session
        _signal(p, "NVDA", k)
        p.lo["NVDA"][k + 1] = 95.5
    free = _replay(p, limits_5k)
    pdt = _replay(p, limits_5k, account="margin_pdt")
    assert [t.reason for t in free.trades] == ["stop"] * 4
    assert [t.reason for t in pdt.trades] == ["stop", "stop", "stop", "stop_pdt_deferred"]
    assert pdt.trades[-1].exit_session == p.sessions[6].isoformat()  # sold at the next open
    assert len(_replay(p, limits_5k, account="margin_pdt", equity=25_000).trades) == 4


def test_cash_account_waits_for_settled_cash(limits_5k):
    syms = ("AMD", "AVGO", "MU")
    p = _panel(12, syms)
    for sym in syms:
        _signal(p, sym, 1)  # three $900 positions on $3,600 leave $900 of cash
        p.sma5[sym][2] = 99.0  # all bounce at the close of session 2, sold at the open of 3
        _signal(p, sym, 3)  # and signal again at the close of session 3
    free = _replay(p, limits_5k, equity=3600.0)
    cash = _replay(p, limits_5k, account="cash", equity=3600.0)
    second = [t for t in free.trades if t.signal_session == p.sessions[3].isoformat()]
    assert len(second) == 3
    # The $2,700 of sales from session 3 settle at session 4: only $900 can be used now.
    second_cash = [t for t in cash.trades if t.signal_session == p.sessions[3].isoformat()]
    assert [t.symbol for t in second_cash] == ["AMD"]
    assert cash.refusals["unsettled_cash"] == 2


def test_drawdown_halt_stops_new_entries(limits_5k):
    p = _panel(20)
    _signal(p, "NVDA", 1)
    p.o["NVDA"][3], p.lo["NVDA"][3] = 70.0, 69.0  # 12 shares x $30 = -$360, beyond 5%
    p.c["NVDA"][3:] = 70.0
    p.o["NVDA"][4:], p.h["NVDA"][4:], p.lo["NVDA"][4:] = 70.0, 70.0, 70.0
    p.atr14["NVDA"][:] = 2.0
    _signal(p, "NVDA", 6)
    r = _replay(p, limits_5k)
    assert len(r.trades) == 1
    assert r.drawdown_halt_on == p.sessions[3].isoformat()
    assert r.refusals["drawdown_halt"] == 1


def test_replay_refuses_a_non_positive_equity(limits_5k):
    with pytest.raises(ValueError, match="start_equity"):
        _replay(_panel(5), limits_5k, equity=0.0)


# ------------------------------------------------------------------ the pre-registered gates


def _s(n: int, m: float) -> dict[str, float]:
    return {"trades": n, "mean_r": m}


def test_selection_rule():
    assert (
        dip.select_variant({"S1": _s(250, 0.10), "S2": _s(300, 0.08), "baseline": _s(9000, 0.02)})[
            0
        ]
        == "S1"
    )
    chosen, notes = dip.select_variant(
        {"S1": _s(150, 0.30), "S2": _s(300, 0.01), "baseline": _s(9000, 0.02)}
    )
    assert chosen is None
    assert notes == ["S1: fewer than 200 trades", "S2: mean R not above the baseline"]
    assert (
        dip.select_variant({"S1": _s(250, -0.01), "S2": _s(300, 0.05), "baseline": _s(9000, -0.2)})[
            0
        ]
        == "S2"
    )


def test_holdout_pass_rule():
    ok = {"S2": _s(80, 0.05), "baseline": _s(3000, 0.0)}
    assert dip.holdout_pass(ok, "S2", 10.0) == []
    assert dip.holdout_pass(ok, "S2", -1.0) == ["$5,000 unrestricted run not positive"]
    assert dip.holdout_pass({"S2": _s(59, 0.05), "baseline": _s(3000, 0.0)}, "S2", 10.0) == [
        "fewer than 60 trades"
    ]


def test_periods_are_the_spec_dates():
    assert (date(2016, 4, 1), date(2024, 12, 31)) == dip.DEVELOPMENT
    assert (date(2025, 1, 2), date(2026, 11, 13)) == dip.HOLDOUT


# ------------------------------------------------------------------ CLI


def _write_cache(data: Path, start: date, end: date) -> None:
    store = ParquetBarStore(data)
    for sym, frame in _daily(("NVDA", "AMD", "SMH"), start, end).items():
        store.write(
            sym,
            frame,
            feed=DataFeed.SIP,
            adjustment=Adjustment.SPLIT,
            source=DataSource.FIXTURE,
            fetched_at=datetime(2026, 11, 14, tzinfo=UTC),
        )


def test_cli_holdout_needs_the_selected_variant(config_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path / "data"))
    base = ["backtest-dip", "--config-dir", str(config_dir)]
    assert cli.main([*base, "--period", "holdout"]) != 0
    assert cli.main([*base, "--period", "development"]) != 0  # no SMH cache
    assert cli.main([*base, "--period", "development", "--equity", "-1"]) != 0


def test_cli_development_run_writes_a_labelled_report(
    config_dir: Path, tmp_path: Path, monkeypatch, capsys
):
    data = tmp_path / "data"
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(data))
    _write_cache(data, date(2023, 1, 3), date(2025, 3, 31))  # extends past the development end
    out = tmp_path / "report"
    args = ["backtest-dip", "--config-dir", str(config_dir), "--period", "development"]
    assert cli.main([*args, "--output", str(out)]) == 0
    report = json.loads((out / "dip_backtest.json").read_text(encoding="utf-8"))
    assert report["command"] == (
        "semiedge backtest-dip --period development --equity 5000 --risk-profile 5k"
    )
    assert report["window"] == ["2016-04-01", "2024-12-31"]
    assert set(report["study"]) == {
        f"{v}@{s}" for v in ("S1", "S2", "baseline") for s in ("10", "20", "30")
    }
    assert "selected" in report["selection"]
    assert len(report["runs"]) == 18
    last = max(e[0] for r in report["runs"] for e in r["session_equity"])
    assert last <= "2024-12-31"  # nothing from the holdout period
    assert "Selection (20 bps)" in capsys.readouterr().out
    assert cli.main([*args, "--output", str(out)]) != 0  # never overwrites a report
