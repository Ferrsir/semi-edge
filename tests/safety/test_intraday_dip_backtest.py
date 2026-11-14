"""intraday_dip_v1 backtest (#137): signals, no look-ahead, same-day exits, accounts and gates.

Hand-built bars and synthetic fixtures only; no network and no cached market data.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from semiedge import cli
from semiedge.backtest import intraday_dip as idip
from semiedge.backtest.intraday_dip import DaySession, Signal
from semiedge.backtest.intraday_signals import SessionBars, vwap
from semiedge.config.loader import load_risk_profile
from semiedge.data.calendar import TradingCalendar
from semiedge.data.intraday import INTRADAY_INDEX_NAME, IntradayBarStore, session_bar_starts
from semiedge.data.schema import Adjustment, DataFeed, DataSource
from semiedge.data.store import ParquetBarStore
from semiedge.data.synthetic import SyntheticMarketDataSource

N = 78
CAL = TradingCalendar()


@pytest.fixture
def limits_5k(config_dir: Path):
    return load_risk_profile(config_dir, "5k")


def _flat(price: float = 100.0, n: int = N, volume: float = 1000.0) -> SessionBars:
    p = np.full(n, price, dtype="float64")
    return SessionBars(p.copy(), p.copy(), p.copy(), p.copy(), np.full(n, volume))


def _dip_bars() -> SessionBars:
    """Flat at 100, a drop to a 94.9 low on bar 14, and a reversal bar on bar 15."""
    b = _flat()
    for k, close in zip(range(10, 14), (99.0, 98.0, 97.0, 96.0), strict=True):
        b.o[k], b.h[k], b.lo[k], b.c[k] = close + 1.0, close + 1.0, close - 0.1, close
    b.o[14], b.h[14], b.lo[14], b.c[14] = 95.2, 95.2, 94.9, 95.0  # the low of the dip
    b.o[15], b.h[15], b.lo[15], b.c[15] = 95.0, 95.6, 95.0, 95.5
    b.o[16:], b.h[16:], b.lo[16:], b.c[16:] = 95.5, 95.6, 95.4, 95.5
    return b


# ------------------------------------------------------------------ signals


def test_a_stretched_reversal_signals_for_both_variants():
    b, smh = _dip_bars(), _flat(300.0)
    sig = idip.session_signals(b, smh, 4.0)
    assert sorted(sig["V1"]) == sorted(sig["V2"]) == sorted(sig["baseline"]) == [15]
    (s,) = sig["V1"][15]
    assert s.stop == pytest.approx(94.9)
    assert s.target == pytest.approx(float(vwap(b)[15]))
    assert s.r0 == pytest.approx(0.6)


def test_v2_needs_the_stock_to_fall_more_than_smh():
    b = _dip_bars()
    smh = _flat(300.0)
    smh.c[14] = 300.0 * 0.95  # SMH fell 5% too: no relative stretch
    sig = idip.session_signals(b, smh, 4.0)
    assert 15 in sig["V1"]
    assert sig["V2"] == {}


def test_v1_needs_half_a_daily_range_below_vwap():
    sig = idip.session_signals(_dip_bars(), _flat(300.0), 20.0)  # stretch about 0.2 ATR
    assert sig["V1"] == {}
    assert 15 in sig["baseline"]


def test_no_signal_without_a_reversal_bar():
    b = _dip_bars()
    b.c[15] = 95.15  # green, but not above the prior bar's high of 95.2
    assert idip.session_signals(b, _flat(300.0), 4.0)["baseline"] == {}


def test_no_signal_without_room_to_vwap():
    b = _dip_bars()
    b.lo[14] = 90.0  # risk 5.5 against about 3.4 of room
    assert idip.session_signals(b, _flat(300.0), 4.0)["baseline"] == {}


def test_no_decision_after_a_gap_or_without_daily_atr():
    b = _dip_bars()
    b.c[12] = np.nan
    assert idip.session_signals(b, _flat(300.0), 4.0)["baseline"] == {}
    assert idip.session_signals(_dip_bars(), _flat(300.0), None)["baseline"] == {}


def test_decision_window_ends_at_1530_and_shrinks_on_early_closes():
    b = _flat()
    k = 72  # the 15:35 bar
    b.o[k - 2 : k], b.lo[k - 2 : k], b.c[k - 2 : k], b.h[k - 2 : k] = 90.0, 89.9, 90.0, 90.1
    b.o[k], b.lo[k], b.h[k], b.c[k] = 90.0, 90.0, 90.5, 90.4
    assert idip.session_signals(b, _flat(300.0), 4.0)["baseline"] == {}


def test_signals_use_no_future_bars():
    seen = 0
    for seed in range(8):
        rng = np.random.default_rng(seed)
        c = 100 + np.cumsum(rng.normal(-0.03, 0.35, N))
        o = c + rng.normal(0, 0.15, N)
        h = np.maximum(o, c) + rng.uniform(0, 0.3, N)
        lo = np.minimum(o, c) - rng.uniform(0, 0.3, N)
        v = rng.uniform(500, 3000, N)
        full = SessionBars(o, h, lo, c, v)
        smh = _flat(300.0)
        base = idip.session_signals(full, smh, 3.0)
        for k in range(3, 72):
            cut = SessionBars(*(np.where(np.arange(N) <= k, x, np.nan) for x in (o, h, lo, c, v)))
            got = idip.session_signals(cut, smh, 3.0)
            for variant in ("V1", "V2", "baseline"):
                assert got[variant].get(k, []) == base[variant].get(k, []), (seed, k)
        seen += sum(len(x) for x in base["baseline"].values())
    assert seen > 0


def test_zero_volume_bars_count_as_missing():
    b = _flat()
    b.v[5] = 0.0
    out = idip._without_empty_bars(b)
    assert np.isnan(out.c[5])
    assert out.c[4] == 100.0


# ------------------------------------------------------------------ exits


def _session(bars: dict[str, SessionBars], sigs: list[Signal], *, day=date(2026, 3, 2)):
    by_k: dict[int, list[Signal]] = {}
    for s in sigs:
        by_k.setdefault(s.k, []).append(s)
    return DaySession(day, N, bars, {"V1": by_k, "V2": {}, "baseline": by_k})


def _sig(sym="NVDA", k=10, stop=99.0, target=102.0, close=100.0) -> Signal:
    return Signal(sym, k, stop, target, close - stop, close, -1.0)


def _study(ds: DaySession, slip: float = 0.0):
    return idip.signal_study([ds], "V1", slippage_bps=slip)


def test_target_exit():
    b = _flat()
    b.h[14] = 102.5
    (t,) = _study(_session({"NVDA": b}, [_sig()]))
    assert (t.reason, t.exit_bar, t.r_multiple) == ("target", 14, 2.0)


def test_a_bar_touching_both_counts_as_the_stop():
    b = _flat()
    b.h[14], b.lo[14] = 102.5, 98.5
    (t,) = _study(_session({"NVDA": b}, [_sig()]))
    assert (t.reason, t.r_multiple) == ("stop", -1.0)


def test_gaps_through_stop_or_target_fill_at_the_open():
    b = _flat()
    b.o[13], b.lo[13] = 98.0, 97.5
    (t,) = _study(_session({"NVDA": b}, [_sig()]))
    assert (t.reason, t.r_multiple) == ("stop_gap", -2.0)
    b = _flat()
    b.o[13], b.h[13] = 103.0, 103.5
    (t,) = _study(_session({"NVDA": b}, [_sig()]))
    assert (t.reason, t.r_multiple) == ("target_gap", 3.0)


def test_stop_and_target_are_live_on_the_entry_bar():
    b = _flat()
    b.lo[11] = 98.9
    (t,) = _study(_session({"NVDA": b}, [_sig()]))
    assert (t.reason, t.exit_bar) == ("stop", 11)


def test_entries_opening_through_a_level_are_cancelled():
    b = _flat()
    b.o[11] = 99.0
    assert _study(_session({"NVDA": b}, [_sig()])) == []
    b = _flat()
    b.o[11] = 102.0
    assert _study(_session({"NVDA": b}, [_sig()])) == []


def test_flat_at_the_open_of_the_last_bar_with_slippage():
    (t,) = _study(_session({"NVDA": _flat()}, [_sig()]), slip=10.0)
    assert (t.reason, t.exit_bar) == ("flat_1555", N - 1)
    assert t.r_multiple == pytest.approx((100 * 0.999 - 100 * 1.001) / 1.0, abs=1e-4)


def test_study_stats_report_the_heavy_tail():
    trades = [idip.StudyTrade("X", "d", 1, 2, "stop", -1.0)] * 10 + [
        idip.StudyTrade("X", "d", 1, 2, "target", 30.0)
    ]
    s = idip.study_stats(trades)
    assert s["mean_r"] > 0
    assert s["mean_r_without_top5"] == -1.0
    assert s["median_r"] == -1.0


# ------------------------------------------------------------------ portfolio replay


def _replay(sessions, limits, *, account="unrestricted", equity=5000.0, slip=0.0):
    return idip.replay(
        sessions,
        variant="V1",
        start_equity=equity,
        limits=limits,
        account=account,
        slippage_bps=slip,
    )


def test_portfolio_sizing_and_round_trip(limits_5k):
    b = _flat()
    b.h[14] = 102.5
    r = _replay([_session({"NVDA": b}, [_sig()])], limits_5k)
    (t,) = r.trades
    assert t.shares == 12  # $1,250 cap at $100 binds before $50 / $1 = 50 shares
    assert r.summary()["net_pnl"] == 24.0


@pytest.mark.parametrize("account", ["unrestricted", "margin_pdt", "cash"])
@pytest.mark.parametrize("slip", [0.0, 20.0])
def test_gap_up_fill_caps_stop_risk_but_preserves_planned_r(limits_5k, account, slip):
    b = _flat()
    for values in (b.o, b.h, b.lo, b.c):
        values[11:] = 110.0
    b.lo[12] = 99.0
    ds = _session({"NVDA": b}, [_sig(target=150.0)])
    (t,) = _replay([ds], limits_5k, account=account, slip=slip).trades
    assert t.shares * (t.entry - t.stop) <= 50.0
    assert t.shares == 4
    assert t.reason == "stop"
    assert t.r_multiple == pytest.approx(t.exit - t.entry, abs=1e-4)  # planned R0 = 1


@pytest.mark.parametrize("account", ["unrestricted", "margin_pdt", "cash"])
def test_gap_up_fill_refuses_a_share_over_budget(limits_5k, account):
    b = _flat()
    for values in (b.o, b.h, b.lo, b.c):
        values[11:] = 160.0
    r = _replay([_session({"NVDA": b}, [_sig(target=250.0)])], limits_5k, account=account)
    assert r.trades == []
    assert r.refusals["fill_over_cap"] == 1
    assert r.session_equity == [("2026-03-02", 5000.0)]


def test_several_trades_a_day_in_one_symbol(limits_5k):
    b = _flat()
    b.h[14] = b.h[30] = 102.5
    sigs = [_sig(k=10), _sig(k=20), _sig(k=40)]
    r = _replay([_session({"NVDA": b}, sigs)], limits_5k)
    assert [(t.entry_bar, t.reason) for t in r.trades] == [
        (11, "target"),
        (21, "target"),
        (41, "flat_1555"),
    ]


def test_ranking_and_max_positions(limits_5k):
    syms = ("AMD", "AVGO", "MU", "NVDA")
    bars = {s: _flat() for s in syms}
    sigs = [
        Signal(s, 10, 99.0, 102.0, 1.0, 100.0, score)
        for s, score in zip(syms, (-1.0, -3.0, -2.0, -4.0), strict=True)
    ]
    sigs.sort(key=lambda s: (s.score, s.symbol))
    r = _replay([_session(bars, sigs)], limits_5k)
    assert sorted(t.symbol for t in r.trades) == ["AVGO", "MU", "NVDA"]
    assert r.refusals["max_positions"] == 1


def _sessions(count: int, *, per_day: int = 1) -> list[DaySession]:
    days = CAL.sessions_in_range(date(2026, 3, 2), date(2026, 4, 30))[:count]
    out = []
    for day in days:
        b = _flat()
        sigs = []
        for j in range(per_day):
            k = 10 + 15 * j
            b.h[k + 4] = 102.5
            sigs.append(_sig(k=k))
        out.append(_session({"NVDA": b}, sigs, day=day))
    return out


def test_margin_pdt_allows_three_day_trades_in_five_sessions(limits_5k):
    s = _sessions(6, per_day=2)
    free = _replay(s, limits_5k)
    pdt = _replay(s, limits_5k, account="margin_pdt")
    assert len(free.trades) == 12
    by_day = [len([t for t in pdt.trades if t.session == d.session.isoformat()]) for d in s]
    assert by_day == [2, 1, 0, 0, 0, 2]  # the window clears once day 1 rolls out
    assert pdt.refusals["pdt_limit"] == 7


def test_cash_account_trades_only_settled_cash(limits_5k):
    syms = ("AMD", "AVGO", "MU", "NVDA", "TSM")
    bars = {s: _flat() for s in syms}
    sigs = []
    for i, s in enumerate(syms):
        k = 10 + 8 * i
        bars[s].h[k + 2] = 102.5
        sigs.append(Signal(s, k, 99.0, 102.0, 1.0, 100.0, -1.0))
    ds = _session(bars, sigs)
    free = _replay([ds], limits_5k)
    cash = _replay([ds], limits_5k, account="cash")
    assert len(free.trades) == 5
    # Four $1,200 round trips use $4,800 of $5,000; their proceeds settle tomorrow.
    assert [t.shares for t in cash.trades] == [12, 12, 12, 12, 2]  # $200 settled cash left


def test_daily_loss_halt_and_drawdown_halt(limits_5k):
    a, b = _flat(), _flat()
    a.o[13], a.lo[13] = 90.0, 89.0  # 12 shares x -$10 = -$120 (2.4%)
    ds = _session({"NVDA": a, "AMD": b}, [_sig("NVDA", 10), _sig("AMD", 20)])
    r = _replay([ds], limits_5k)
    assert [t.symbol for t in r.trades] == ["NVDA"]
    assert r.refusals["daily_loss_halt"] == 1
    big = []
    for day in CAL.sessions_in_range(date(2026, 3, 2), date(2026, 3, 6)):
        x = _flat()
        x.o[13], x.lo[13] = 90.0, 89.0
        big.append(_session({"NVDA": x}, [_sig("NVDA", 10)], day=day))
    r = _replay(big, limits_5k)
    assert r.drawdown_halt_on == "2026-03-04"  # -2.4%, -4.8%, then -7.2% crosses 5%
    assert len(r.trades) == 3


def test_replay_refuses_non_positive_equity(limits_5k):
    with pytest.raises(ValueError, match="start_equity"):
        _replay(_sessions(1), limits_5k, equity=0.0)


# ------------------------------------------------------------------ gates


def _s(n: int, m: float, trimmed: float) -> dict[str, float]:
    return {"trades": n, "mean_r": m, "mean_r_without_top5": trimmed}


def test_selection_needs_a_result_without_its_top_five_trades():
    study = {"V1": _s(400, 0.10, -0.01), "V2": _s(350, 0.05, 0.02), "baseline": _s(9000, 0.0, 0.0)}
    chosen, notes = idip.select_variant(study)
    assert chosen == "V2"
    assert notes[0] == "V1: mean R without the top five trades not above 0"
    assert idip.select_variant({**study, "V2": _s(299, 0.05, 0.02)})[0] is None


def test_holdout_pass_rule():
    study = {"V1": _s(120, 0.04, 0.03), "baseline": _s(5000, -0.02, -0.02)}
    assert idip.holdout_pass(study, "V1", 50.0) == []
    assert idip.holdout_pass(study, "V1", -5.0) == ["$5,000 unrestricted run not positive"]


def test_periods_are_the_spec_dates():
    assert (date(2024, 12, 16), date(2025, 12, 31)) == idip.DEVELOPMENT
    assert (date(2026, 1, 2), date(2026, 11, 13)) == idip.HOLDOUT


# ------------------------------------------------------------------ CLI


def _frame(day: date) -> pd.DataFrame:
    starts = session_bar_starts(CAL, day)
    c = 300 + np.arange(len(starts)) * 0.01
    return pd.DataFrame(
        {
            "open": c,
            "high": c + 0.05,
            "low": c - 0.05,
            "close": c,
            "volume": np.full(len(starts), 1000.0),
            "trade_count": np.full(len(starts), 10.0),
            "vwap": c,
        },
        index=pd.DatetimeIndex(starts, name=INTRADAY_INDEX_NAME),
    )


def test_cli_guards(config_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path / "data"))
    base = ["backtest-intraday-dip", "--config-dir", str(config_dir)]
    assert cli.main([*base, "--period", "holdout"]) != 0
    assert cli.main([*base, "--period", "development"]) != 0  # no SMH cache
    assert cli.main([*base, "--period", "development", "--equity", "0"]) != 0


def test_cli_development_run_writes_a_report(config_dir: Path, tmp_path: Path, monkeypatch, capsys):
    data = tmp_path / "data"
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(data))
    days = CAL.sessions_in_range(date(2024, 12, 16), date(2024, 12, 20))
    kw = {"feed": DataFeed.SIP, "adjustment": Adjustment.SPLIT, "source": DataSource.ALPACA}
    for sym in ("SMH", "NVDA"):
        IntradayBarStore(data).replace(
            sym,
            pd.concat([_frame(d) for d in days]),
            fetched_at=datetime(2026, 11, 14, tzinfo=UTC),
            calendar=CAL,
            **kw,
        )
    daily = SyntheticMarketDataSource().get_daily_bars(
        ["NVDA"], date(2024, 10, 1), date(2024, 12, 13)
    )
    ParquetBarStore(data).write(
        "NVDA",
        daily["NVDA"],
        feed=DataFeed.SIP,
        adjustment=Adjustment.SPLIT,
        source=DataSource.FIXTURE,
        fetched_at=datetime(2026, 11, 14, tzinfo=UTC),
    )
    out = tmp_path / "report"
    args = ["backtest-intraday-dip", "--config-dir", str(config_dir), "--period", "development"]
    assert cli.main([*args, "--output", str(out)]) == 0
    report = json.loads((out / "intraday_dip_v1_backtest.json").read_text(encoding="utf-8"))
    assert report["command"] == (
        "semiedge backtest-intraday-dip --period development --equity 5000 --risk-profile 5k"
    )
    assert report["window"] == ["2024-12-16", "2025-12-31"]
    assert len(report["runs"]) == 18
    assert "selected" in report["selection"]
    assert "intraday_dip_v1 development" in capsys.readouterr().out
    assert cli.main([*args, "--output", str(out)]) != 0
