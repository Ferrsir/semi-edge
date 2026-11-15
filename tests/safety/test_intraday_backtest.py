"""intraday_v1 backtest (#130): setup triggers, no look-ahead, fills, exits and account models.

Hand-built bars only; no network and no cached market data.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from semiedge import cli
from semiedge.backtest.intraday import (
    PreparedSession,
    prepare_sessions,
    replay,
    run_matrix,
)
from semiedge.backtest.intraday_signals import (
    SessionBars,
    SessionContext,
    Trigger,
    history_ok,
    last_decision_index,
    setup_a_day_eligible,
    slot_volume,
    triggers_at,
    vwap,
)
from semiedge.config.loader import load_risk_profile
from semiedge.data.calendar import TradingCalendar
from semiedge.data.intraday import INTRADAY_INDEX_NAME, IntradayBarStore, session_bar_starts
from semiedge.data.schema import Adjustment, DataFeed, DataSource

N = 78
CAL = TradingCalendar()


@pytest.fixture
def limits_5k(config_dir: Path):
    return load_risk_profile(config_dir, "5k")


# ------------------------------------------------------------------ helpers


def _flat(price: float, n: int = N, volume: float = 1000.0) -> SessionBars:
    p = np.full(n, price, dtype="float64")
    return SessionBars(p.copy(), p.copy(), p.copy(), p.copy(), np.full(n, volume))


def _ctx(*, slots: int = 20, volume: float = 1000.0, orv: float = 3000.0) -> SessionContext:
    return SessionContext(
        prior_orv=[orv] * slots,
        prior_slots=[np.full(N, volume) for _ in range(slots)],
        pdh=1000.0,
        atr_d1=4.0,
    )


def _rising_smh(n: int = N) -> SessionBars:
    c = 100.0 + np.arange(n) * 0.1
    return SessionBars(c - 0.02, c + 0.05, c - 0.05, c, np.full(n, 1000.0))


def _session(
    bars: dict[str, SessionBars],
    triggers: dict[int, list[tuple[str, Trigger]]],
    *,
    day: date = date(2026, 11, 13),
    smh: SessionBars | None = None,
) -> PreparedSession:
    smh = smh or _rising_smh()
    return PreparedSession(day, N, smh, vwap(smh), triggers, bars, ())


def _run(sessions, limits, *, account="unrestricted", slip=0.0, equity=5000.0):
    return replay(sessions, start_equity=equity, limits=limits, account=account, slippage_bps=slip)


# ------------------------------------------------------------------ setup triggers


def _orb_bars() -> SessionBars:
    b = _flat(100.0)
    b.h[:3], b.lo[:3], b.v[:3] = 101.0, 99.0, 3000.0  # OR width 2 = 0.5 ATR, ORV 3x average
    b.c[5], b.h[5] = 101.5, 101.6
    return b


def test_setup_a_fires_on_close_above_the_opening_range():
    b, ctx = _orb_bars(), _ctx(orv=2000.0)
    assert setup_a_day_eligible(b, ctx)
    trig = triggers_at(b, vwap(b), ctx, 5, a_eligible=True)
    assert [(t.setup, t.stop) for t in trig] == [("A", 99.0)]


def test_setup_a_needs_opening_range_volume_and_width():
    b = _orb_bars()
    assert not setup_a_day_eligible(b, _ctx(orv=9000.0))  # ORV/ORV_avg = 1.0 < 1.5
    wide = SessionContext(**{**_ctx(orv=2000.0).__dict__, "atr_d1": 1.0})  # width 2 > 1 ATR
    assert not setup_a_day_eligible(b, wide)


def test_setup_a_window_ends_at_b24():
    b, ctx = _flat(100.0), _ctx()
    b.h[:3], b.lo[:3] = 101.0, 99.0
    b.c[24], b.h[24] = 101.5, 101.6
    assert triggers_at(b, vwap(b), ctx, 24, a_eligible=True) == []  # b_25 is outside
    b.c[23], b.h[23] = 101.5, 101.6
    assert [t.setup for t in triggers_at(b, vwap(b), ctx, 23, a_eligible=True)] == ["A"]


def test_setup_b_needs_a_cross_bar_and_volume():
    b, ctx = _flat(100.0), _ctx()
    ctx.pdh = 101.0
    b.c[30], b.h[30], b.lo[30], b.v[30] = 101.5, 101.6, 100.0, 1500.0
    trig = triggers_at(b, vwap(b), ctx, 30, a_eligible=False)
    assert [(t.setup, t.stop) for t in trig] == [("B", 100.0)]
    b.v[30] = 1499.0  # below 1.5 x SV_k
    assert triggers_at(b, vwap(b), ctx, 30, a_eligible=False) == []
    b.v[30] = 1500.0
    b.c[29] = 101.2  # the previous bar already closed above PDH: no cross
    assert triggers_at(b, vwap(b), ctx, 30, a_eligible=False) == []


def test_setup_b_is_off_when_the_session_opens_above_pdh():
    b, ctx = _flat(100.0), _ctx()
    ctx.pdh = 99.5
    b.c[30], b.v[30] = 101.5, 2000.0
    b.c[29] = 99.0
    assert triggers_at(b, vwap(b), ctx, 30, a_eligible=False) == []


def _pullback_bars() -> SessionBars:
    c = 100.0 + np.arange(N) * 0.05  # steady trend well above VWAP
    b = SessionBars(c - 0.01, c + 0.02, c - 0.02, c.copy(), np.full(N, 1000.0))
    k = 30
    vw = vwap(b)
    b.lo[k] = vw[k] * 1.001  # touches within 0.2% of VWAP
    b.o[k], b.c[k] = c[k] - 0.03, c[k]  # green bar, still above VWAP
    return b


def test_setup_c_vwap_pullback_fires_with_its_own_stop():
    b = _pullback_bars()
    vw = vwap(b)
    trig = triggers_at(b, vw, _ctx(), 30, a_eligible=False)
    assert [(t.setup, t.stop) for t in trig] == [("C", float(b.lo[30]))]


def test_setup_c_needs_a_green_bar():
    b = _pullback_bars()
    b.o[30] = b.c[30] + 0.01
    assert triggers_at(b, vwap(b), _ctx(), 30, a_eligible=False) == []


def test_history_and_slot_volume_need_15_of_20_sessions():
    ctx = _ctx(slots=19)
    assert not history_ok(ctx)
    assert slot_volume(ctx, 10) is None
    ctx = _ctx()
    for s in ctx.prior_slots[:6]:
        s[10] = np.nan  # six sessions miss bar 10 -> only 14 remain
    assert slot_volume(ctx, 10) is None
    assert not history_ok(ctx)  # six incomplete sessions leave 14 complete
    ctx.prior_slots[0][10] = 1000.0
    assert slot_volume(ctx, 10) == 1000.0
    assert history_ok(ctx)


def test_windows_shrink_on_an_early_close():
    assert last_decision_index(78, 71) == 71
    assert last_decision_index(42, 71) == 35  # 13:00 close: decisions end 12:25 (b_36)


def test_triggers_use_no_future_bars():
    """Every trigger at bar k is unchanged when bars after k are removed."""
    seen = 0
    for seed in range(10):
        rng = np.random.default_rng(seed)
        c = 100 + np.cumsum(rng.normal(0.04, 0.3, N))
        o = c + rng.normal(0, 0.1, N)
        h = np.maximum(o, c) + rng.uniform(0, 0.4, N)
        lo = np.minimum(o, c) - rng.uniform(0, 0.4, N)
        v = rng.uniform(500, 3000, N)
        full = SessionBars(o, h, lo, c, v)
        ctx = _ctx()
        ctx.pdh = float(c[5]) + 0.5
        for k in range(2, N - 6):
            cut = SessionBars(*(np.where(np.arange(N) <= k, x, np.nan) for x in (o, h, lo, c, v)))
            expected = triggers_at(full, vwap(full), ctx, k, a_eligible=True)
            assert triggers_at(cut, vwap(cut), ctx, k, a_eligible=True) == expected
            seen += len(expected)
    assert seen > 0  # the property was exercised on real triggers


def test_a_missing_bar_ends_the_session_for_that_symbol():
    b = _orb_bars()
    b.c[4] = np.nan
    for x in (b.o, b.h, b.lo, b.v):
        x[4] = np.nan
    assert triggers_at(b, vwap(b), _ctx(orv=2000.0), 5, a_eligible=True) == []


# ------------------------------------------------------------------ fills and exits


def _one_trade(limits, mutate, *, stop: float = 99.0, slip: float = 0.0, setup: str = "A"):
    b = _flat(100.0)
    mutate(b)
    ps = _session({"NVDA": b}, {10: [("NVDA", Trigger(setup, 10, stop, 1.0))]})
    return _run([ps], limits, slip=slip)


def test_target_at_two_r(limits_5k):
    def m(b):
        b.h[15] = 102.5

    r = _one_trade(limits_5k, m)
    (t,) = r.trades
    assert (t.entry_bar, t.exit_bar, t.reason, t.exit) == (11, 15, "target", 102.0)
    assert t.shares == 12  # 25% of $5,000 at $100 binds before the $50 risk budget
    assert t.r_multiple == 2.0


@pytest.mark.parametrize("account", ["unrestricted", "margin_pdt", "cash"])
@pytest.mark.parametrize("slip", [0.0, 20.0])
def test_gap_up_fill_caps_actual_stop_risk(limits_5k, account, slip):
    b = _flat(100.0)
    # The signal reserves 12 shares with $1 estimated risk. The $110 next open
    # leaves only four shares within the $50 entry-to-stop budget.
    for values in (b.o, b.h, b.lo, b.c):
        values[11:] = 110.0
    b.lo[12], b.c[12] = 99.0, 99.0
    ps = _session({"NVDA": b}, {10: [("NVDA", Trigger("A", 10, 99.0, 1.0))]})
    result = _run([ps], limits_5k, account=account, slip=slip)
    (trade,) = result.trades
    budget = 5000.0 * limits_5k.max_risk_per_trade_pct
    assert trade.shares * (trade.entry - trade.stop) <= budget
    assert trade.shares == 4
    assert trade.reason == "stop"
    assert trade.target == pytest.approx(trade.entry + 2 * (trade.entry - trade.stop))
    assert result.session_equity[-1][1] == pytest.approx(5000.0 + trade.pnl, abs=0.01)


@pytest.mark.parametrize("account", ["unrestricted", "margin_pdt", "cash"])
def test_gap_up_fill_refuses_when_one_share_exceeds_risk_budget(limits_5k, account):
    b = _flat(100.0)
    for values in (b.o, b.h, b.lo, b.c):
        values[11:] = 160.0  # $61 risk per share exceeds the $50 budget
    ps = _session({"NVDA": b}, {10: [("NVDA", Trigger("A", 10, 99.0, 1.0))]})
    result = _run([ps], limits_5k, account=account)
    assert result.trades == []
    assert result.refusals["fill_over_cap"] == 1
    assert result.session_equity == [(ps.session.isoformat(), 5000.0)]


def test_favorable_fill_does_not_increase_reserved_shares(limits_5k):
    def mutate(b):
        for values in (b.o, b.h, b.lo, b.c):
            values[11:] = 99.5

    (trade,) = _one_trade(limits_5k, mutate).trades
    assert trade.shares == 12
    assert trade.entry == 99.5


def test_a_bar_touching_stop_and_target_counts_as_the_stop(limits_5k):
    def m(b):
        b.h[15], b.lo[15] = 102.5, 98.5

    (t,) = _one_trade(limits_5k, m).trades
    assert (t.reason, t.exit, t.r_multiple) == ("stop", 99.0, -1.0)


def test_gap_through_the_stop_exits_at_the_open(limits_5k):
    def m(b):
        b.o[15], b.lo[15] = 98.0, 97.5

    (t,) = _one_trade(limits_5k, m).trades
    assert (t.reason, t.exit) == ("stop_gap", 98.0)


def test_stop_is_live_on_the_entry_bar(limits_5k):
    def m(b):
        b.lo[11] = 98.5

    (t,) = _one_trade(limits_5k, m).trades
    assert (t.entry_bar, t.exit_bar, t.reason) == (11, 11, "stop")


def test_entry_gapping_through_the_stop_is_cancelled(limits_5k):
    def m(b):
        b.o[11] = 98.9

    r = _one_trade(limits_5k, m)
    assert r.trades == []
    assert r.refusals["gap_through_stop"] == 1


def test_minimum_risk_cancels_the_entry(limits_5k):
    r = _one_trade(limits_5k, lambda b: None, stop=99.97)  # R = 0.03 < 0.05% of 100
    assert r.trades == []
    assert r.refusals["stop_too_close"] == 1


def test_open_positions_are_flattened_at_1555_with_slippage(limits_5k):
    (t,) = _one_trade(limits_5k, lambda b: None, slip=10.0).trades
    assert t.reason == "flat_1555"
    assert t.exit_bar == N - 1
    assert t.entry == pytest.approx(100.1)
    assert t.exit == pytest.approx(99.9)


def test_no_decision_in_the_last_half_hour(limits_5k):
    b = _flat(100.0)
    ps = _session({"NVDA": b}, {N - 6: [("NVDA", Trigger("B", N - 6, 99.0, 1.0))]})
    assert _run([ps], limits_5k).trades == []


# ------------------------------------------------------------------ market filter and ranking


def test_failed_market_filter_does_not_use_up_the_trigger(limits_5k):
    smh = _rising_smh()
    smh.c[10] = smh.h[10] = smh.lo[10] = smh.o[10] = 90.0  # below VWAP on bar 10 only
    b = _flat(100.0)
    trig = {
        10: [("NVDA", Trigger("A", 10, 99.0, 1.0))],
        12: [("NVDA", Trigger("A", 12, 99.0, 1.0))],
    }
    (t,) = _run([_session({"NVDA": b}, trig, smh=smh)], limits_5k).trades
    assert t.entry_bar == 13


def test_each_setup_fires_once_per_symbol_per_day(limits_5k):
    def m(b):
        b.lo[12] = 98.5  # stopped out, then the same setup triggers again

    b = _flat(100.0)
    m(b)
    trig = {k: [("NVDA", Trigger("A", k, 99.0, 1.0))] for k in (10, 20)}
    trig[30] = [("NVDA", Trigger("C", 30, 99.0, 1.0))]
    r = _run([_session({"NVDA": b}, trig)], limits_5k)
    assert [(t.setup, t.entry_bar) for t in r.trades] == [("A", 11), ("C", 31)]


def test_same_bar_ranking_and_position_cap(limits_5k):
    bars = {s: _flat(100.0) for s in ("AMD", "MU", "NVDA", "TSM")}
    trig = {
        10: [
            ("TSM", Trigger("C", 10, 99.0, 9.0)),
            ("MU", Trigger("B", 10, 99.0, 1.0)),
            ("NVDA", Trigger("A", 10, 99.0, 1.0)),
            ("AMD", Trigger("A", 10, 99.0, 2.0)),
            ("NVDA", Trigger("C", 10, 99.0, 5.0)),
        ]
    }
    r = _run([_session(bars, trig)], limits_5k)
    assert sorted((t.symbol, t.setup) for t in r.trades) == [
        ("AMD", "A"),
        ("MU", "B"),
        ("NVDA", "A"),
    ]
    assert r.refusals["max_positions"] == 1  # TSM's C ranks fourth of three slots


def test_daily_loss_halt_stops_new_entries(limits_5k):
    a, b = _flat(100.0), _flat(100.0)
    a.o[15], a.lo[15] = 90.0, 89.0  # -$120 on 12 shares: more than 1% of $5,000
    trig = {10: [("NVDA", Trigger("A", 10, 99.0, 1.0))], 20: [("AMD", Trigger("A", 20, 99.0, 1.0))]}
    r = _run([_session({"NVDA": a, "AMD": b}, trig)], limits_5k)
    assert [t.symbol for t in r.trades] == ["NVDA"]
    assert r.refusals["daily_loss_halt"] == 1


# ------------------------------------------------------------------ account models


def _daily_sessions(count: int) -> list[PreparedSession]:
    days = CAL.sessions_in_range(date(2026, 11, 2), date(2026, 11, 30))[:count]
    out = []
    for day in days:
        b = _flat(100.0)
        b.h[15] = 102.5
        out.append(_session({"NVDA": b}, {10: [("NVDA", Trigger("A", 10, 99.0, 1.0))]}, day=day))
    return out


def test_margin_pdt_refuses_a_fourth_day_trade_in_five_sessions(limits_5k):
    sessions = _daily_sessions(6)
    free = _run(sessions, limits_5k)
    pdt = _run(sessions, limits_5k, account="margin_pdt")
    assert len(free.trades) == 6
    # Sessions 4 and 5 already hold three day trades in their window; by session 6 the first
    # trade has rolled out of the five-session window.
    expected = [sessions[i].session.isoformat() for i in (0, 1, 2, 5)]
    assert [t.session for t in pdt.trades] == expected
    assert pdt.refusals["pdt_limit"] == 2


def test_margin_pdt_does_not_apply_at_25k(limits_5k):
    assert len(_run(_daily_sessions(6), limits_5k, account="margin_pdt", equity=25_000).trades) == 6


def test_cash_account_waits_for_settlement(limits_5k):
    def sessions():
        out = []
        for day in (date(2026, 11, 12), date(2026, 11, 13)):
            bars, trig = {}, {}
            for i, sym in enumerate(("AMD", "AVGO", "MU", "NVDA", "TSM")):
                b = _flat(125.0)
                k = 10 + 10 * i
                b.h[k + 2] = 127.5  # each trade hits its target two bars after the fill
                bars[sym] = b
                trig[k] = [(sym, Trigger("A", k, 124.0, 1.0))]
            out.append(_session(bars, trig, day=day))
        return out

    free = _run(sessions(), limits_5k)
    cash = _run(sessions(), limits_5k, account="cash")
    assert len(free.trades) == 10
    # Four $1,250 round trips use all settled cash; the fifth waits for the next session.
    assert [t.symbol for t in cash.trades if t.session == "2026-11-12"] == [
        "AMD",
        "AVGO",
        "MU",
        "NVDA",
    ]
    assert cash.refusals["unsettled_cash"] == 2
    assert len([t for t in cash.trades if t.session == "2026-11-13"]) == 4


def test_run_matrix_covers_three_accounts_and_slippages(limits_5k):
    runs = run_matrix(_daily_sessions(2), start_equity=5000.0, limits=limits_5k)
    assert {(r.account, r.slippage_bps) for r in runs} == {
        (a, s) for a in ("unrestricted", "margin_pdt", "cash") for s in (10.0, 20.0, 30.0)
    }
    summary = runs[0].summary()
    assert summary["all"]["trades"] == 2
    assert "net_without_best_5_sessions" in summary


def test_replay_rejects_a_non_positive_equity(limits_5k):
    with pytest.raises(ValueError, match="start_equity"):
        _run(_daily_sessions(1), limits_5k, equity=0.0)


# ------------------------------------------------------------------ preparation and CLI


def _frame(day: date, base: float) -> pd.DataFrame:
    starts = session_bar_starts(CAL, day)
    c = base + np.arange(len(starts)) * 0.01
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


def test_prepare_sessions_flags_gaps_and_needs_daily_history():
    days = CAL.sessions_in_range(date(2026, 11, 9), date(2026, 11, 13))
    smh = pd.concat([_frame(d, 300.0) for d in days])
    nvda = smh.drop(smh.index[100])  # one bar missing on the second session
    prepared = prepare_sessions(
        {"SMH": smh, "NVDA": nvda},
        {},
        tradable=("NVDA",),
        benchmark="SMH",
        start=days[0],
        end=days[-1],
        calendar=CAL,
    )
    assert [p.session for p in prepared] == days
    assert [p.gap_symbols for p in prepared] == [(), ("NVDA",), (), (), ()]
    assert all(not p.triggers for p in prepared)  # no daily bars: no PDH or ATR, no setups


def test_cli_rejects_bad_inputs(config_dir: Path, tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path / "data"))
    base = ["backtest-intraday", "--config-dir", str(config_dir), "--start", "2026-11-09"]
    assert cli.main([*base, "--end", "2026-11-13", "--equity", "0"]) != 0
    assert cli.main([*base, "--end", "2026-11-06"]) != 0
    assert cli.main([*base, "--end", "2026-11-13", "--risk-profile", "nope"]) != 0
    assert cli.main([*base, "--end", "2026-11-13"]) != 0  # no SMH cache
    capsys.readouterr()


def test_cli_writes_a_labelled_report(config_dir: Path, tmp_path: Path, monkeypatch, capsys):
    data = tmp_path / "data"
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(data))
    days = CAL.sessions_in_range(date(2026, 11, 9), date(2026, 11, 13))
    IntradayBarStore(data).replace(
        "SMH",
        pd.concat([_frame(d, 300.0) for d in days]),
        fetched_at=datetime(2026, 11, 14, 1, tzinfo=UTC) + timedelta(0),
        calendar=CAL,
        feed=DataFeed.SIP,
        adjustment=Adjustment.SPLIT,
        source=DataSource.ALPACA,
    )
    out = tmp_path / "report"
    code = cli.main(
        [
            "backtest-intraday",
            "--config-dir",
            str(config_dir),
            "--start",
            "2026-11-09",
            "--end",
            "2026-11-13",
            "--output",
            str(out),
        ]
    )
    assert code == 0
    report = json.loads((out / "intraday_backtest.json").read_text(encoding="utf-8"))
    assert report["command"].startswith("semiedge backtest-intraday --start 2026-11-09")
    assert report["risk_profile"] == "5k"
    assert report["start_equity"] == 5000.0
    assert any(label.startswith("SURVIVORSHIP") for label in report["labels"])
    assert len(report["runs"]) == 9
    assert "Command: semiedge backtest-intraday" in capsys.readouterr().out
    assert (
        cli.main(
            [
                "backtest-intraday",
                "--config-dir",
                str(config_dir),
                "--start",
                "2026-11-09",
                "--end",
                "2026-11-13",
                "--output",
                str(out),
            ]
        )
        != 0
    )  # never overwrites an earlier report
