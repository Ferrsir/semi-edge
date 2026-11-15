"""swing_v1 (#180): rules, held-session order, no look-ahead, sizing, halts, Monte Carlo, gates."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from semiedge import cli
from semiedge.backtest import dip, swing
from semiedge.config.loader import load_risk_profile
from semiedge.data.calendar import TradingCalendar

CAL = TradingCalendar("XNYS")
START = date(2016, 1, 4)


def _frame(closes: np.ndarray, spread: float = 1.0, opens: np.ndarray | None = None):
    sessions = CAL.sessions_in_range(START, date(2026, 12, 31))[: len(closes)]
    c = np.asarray(closes, dtype=float)
    o = c if opens is None else np.asarray(opens, dtype=float)
    return pd.DataFrame(
        {
            "open": o,
            "high": np.maximum(o, c) + spread,
            "low": np.minimum(o, c) - spread,
            "close": c,
            "volume": np.full(len(c), 1e6),
        },
        index=pd.DatetimeIndex([pd.Timestamp(s) for s in sessions], name="session"),
    )


def _walk(rng: np.random.Generator, n: int, drift: float = 0.0005, vol: float = 0.02):
    closes = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    opens = closes * np.exp(rng.normal(0, vol / 3, n))
    return _frame(closes, spread=0.5, opens=opens)


def _panel(frames: dict[str, pd.DataFrame], index: str = "IDX", n: int | None = None):
    n = n or len(next(iter(frames.values())))
    end = CAL.sessions_in_range(START, date(2026, 12, 31))[n - 1]
    symbols = tuple(s for s in frames if s != index)
    return swing.panel_for(frames, symbols, index, end=end, calendar=CAL), symbols


@pytest.fixture
def limits(config_dir: Path):
    return load_risk_profile(config_dir, "5k")


# ------------------------------------------------------------------ rules


def test_r1_reproduces_the_dip_v1_engine_trade_for_trade(monkeypatch):
    """R1 is dip_v1 S1: with dip_v1's 60-bar eligibility the two engines must agree exactly."""
    monkeypatch.setattr(swing, "MIN_BARS", dip.MIN_HISTORY)
    rng = np.random.default_rng(11)
    n = 700
    frames = {s: _walk(rng, n, vol=0.025) for s in ("AAA", "BBB", "CCC", "DDD", "IDX")}
    panel, symbols = _panel(frames)
    a, b = panel.sessions[70], panel.sessions[-1]
    old = dip.prepare_panel(frames, tradable=symbols, benchmark="IDX", end=b)
    for mine, theirs in (("R1", "S1"), ("R1b", "baseline")):
        for bps in (0.0, 20.0):
            got = swing.signal_study(
                panel, swing.build(panel, symbols, "IDX", mine), start=a, end=b, slippage_bps=bps
            )
            want = dip.signal_study(old, theirs, start=a, end=b, slippage_bps=bps)
            key_got = sorted((t.symbol, t.entry_session, t.exit_session, t.reason) for t in got)
            key_want = sorted((t.symbol, t.entry_session, t.exit_session, t.reason) for t in want)
            assert key_got == key_want
            assert len(got) > (20 if mine == "R1" else 500)
            assert sum(t.r_multiple for t in got) == pytest.approx(
                sum(t.r_multiple for t in want), abs=1e-6
            )


def test_dip_rules_need_their_trend_regime_and_dip():
    n = 330
    up = np.linspace(100, 200, n)
    stock = up.copy()
    stock[-3:] = [stock[-4] * 0.995, stock[-4] * 0.99, stock[-4] * 0.985]  # three down closes
    frames = {"X": _frame(stock), "IDX": _frame(up)}
    panel, symbols = _panel(frames)
    for name in ("R1", "R2", "R3"):
        book = swing.build(panel, symbols, "IDX", name)
        assert book.entry["X"][-1], name
        assert not book.entry["X"][:-3].any(), name  # a steady rise is never a dip
        assert swing.build(panel, symbols, "IDX", swing.RULES[name].baseline).entry["X"][-10]
    falling = {"X": _frame(stock), "IDX": _frame(np.linspace(200, 100, n))}
    panel, symbols = _panel(falling)
    for name in ("R1", "R2", "R3", "T2", "Tb"):
        assert not swing.build(panel, symbols, "IDX", name).entry["X"].any(), name
    young = {"X": _frame(stock[-250:]), "IDX": _frame(up[-250:])}
    panel, symbols = _panel(young)
    assert not swing.build(panel, symbols, "IDX", "R1").entry["X"].any()  # under 260 bars


def test_trend_rules_rotation_takes_the_top_three_on_week_ends_only():
    n = 400
    rates = {"A": 0.004, "B": 0.003, "C": 0.002, "D": 0.001, "E": 0.0005}
    frames = {s: _frame(100 * np.exp(r * np.arange(n))) for s, r in rates.items()}
    frames["IDX"] = _frame(np.linspace(100, 150, n))
    panel, symbols = _panel(frames)
    weekly = swing.week_ends(panel.sessions)
    t1 = swing.build(panel, symbols, "IDX", "T1")
    tb = swing.build(panel, symbols, "IDX", "Tb")
    late = np.arange(len(panel.sessions)) >= 300
    for sym in "ABC":
        np.testing.assert_array_equal(t1.entry[sym][late], weekly[late])
    for sym in "DE":
        assert not t1.entry[sym].any()
        np.testing.assert_array_equal(tb.entry[sym][late], weekly[late])
    assert not t1.entry["A"][~weekly].any()
    t2 = swing.build(panel, symbols, "IDX", "T2")
    assert t2.entry["A"][late].all()  # a new 100-day closing high every session
    assert t1.score["A"][-1] < t1.score["B"][-1]  # the higher MOM ranks first


def test_signals_never_read_later_bars():
    rng = np.random.default_rng(5)
    n = 420
    frames = {s: _walk(rng, n) for s in ("A", "B", "C", "D", "IDX")}
    cut = 380
    full, symbols = _panel(frames)
    short, _ = _panel({s: f.iloc[:cut] for s, f in frames.items()}, n=cut)
    for name in (*swing.CANDIDATES, *swing.BASELINES):
        a, b = swing.build(full, symbols, "IDX", name), swing.build(short, symbols, "IDX", name)
        for sym in symbols:
            # the cut panel's last session counts as a week end, so compare up to the bar before
            np.testing.assert_array_equal(a.entry[sym][: cut - 1], b.entry[sym][: cut - 1])


# ------------------------------------------------------------------ held-session rules


def _trend_case(tail: list[float], opens: list[float] | None = None):
    n = 320
    base = list(np.linspace(100, 160, n))
    closes = np.array(base + tail)
    o = None if opens is None else np.array(base + opens)
    frames = {"X": _frame(closes, opens=o), "IDX": _frame(np.linspace(100, 160, len(closes)))}
    panel, symbols = _panel(frames)
    book = swing.build(panel, symbols, "IDX", "T2")
    start, end = panel.sessions[n - 1], panel.sessions[-1]
    return panel, book, start, end, n


def test_trend_trade_trails_then_sells_at_the_next_open():
    tail = [161.0, 170.0, 180.0, 165.0, 166.0, 167.0]  # 165 is more than 3 ATR below 180
    panel, book, start, end, n = _trend_case(tail)
    trades = swing.signal_study(panel, book, start=start, end=end, slippage_bps=0.0)
    first = trades[0]
    assert first.entry_session == panel.sessions[n].isoformat()
    assert first.reason == "trail"
    assert first.exit_session == panel.sessions[n + 4].isoformat()  # decided at 165, sold next open
    atr = panel.atr["X"][n - 1]
    assert first.r_multiple == pytest.approx((166.0 - 161.0) / (3 * atr), abs=1e-3)
    # non-overlapping: the next study trade starts only after the first one closed
    assert all(t.entry_session > first.exit_session for t in trades[1:])


def test_stop_is_checked_before_close_exits_and_gaps_fill_at_the_open():
    panel, book, start, end, n = _trend_case([161.0, 120.0, 121.0], opens=[161.0, 125.0, 121.0])
    (t,) = swing.signal_study(panel, book, start=start, end=end, slippage_bps=0.0)
    assert (t.reason, t.exit_session) == ("stop_gap", panel.sessions[n + 1].isoformat())
    atr = panel.atr["X"][n - 1]
    assert t.r_multiple == pytest.approx((125.0 - 161.0) / (3 * atr), abs=1e-3)
    panel, book, start, end, n = _trend_case([161.0, 162.0, 163.0])
    panel.lo["X"] = panel.lo["X"].copy()
    panel.lo["X"][n + 1] = 100.0  # trades through the stop intraday
    t = swing.signal_study(panel, book, start=start, end=end, slippage_bps=10.0)[0]
    assert t.reason == "stop"
    stop = 160.0 - 3 * panel.atr["X"][n - 1]
    assert t.r_multiple == pytest.approx(
        (stop * 0.999 - 161.0 * 1.001) / (3 * panel.atr["X"][n - 1]), abs=1e-3
    )


def test_an_open_at_or_below_the_stop_cancels_the_entry():
    panel, book, start, end, _ = _trend_case([150.0, 151.0], opens=[100.0, 151.0])
    assert swing.signal_study(panel, book, start=start, end=end, slippage_bps=0.0) == []


# ------------------------------------------------------------------ portfolio run


def _market(seed: int = 2, n: int = 520, symbols: int = 8, drift: float = 0.001):
    rng = np.random.default_rng(seed)
    frames = {f"S{k}": _walk(rng, n, drift=drift) for k in range(symbols)}
    frames["IDX"] = _frame(np.linspace(100, 180, n))
    return _panel(frames)


def test_replay_respects_every_limit_and_conserves_cash(limits):
    panel, symbols = _market()
    book = swing.build(panel, symbols, "IDX", "Tb")
    a, b = panel.sessions[300], panel.sessions[-1]
    run = swing.replay(
        panel, book, start=a, end=b, start_equity=5000.0, limits=limits, slippage_bps=10.0
    )
    assert len(run.trades) > 5
    assert max(run.exposure) <= limits.max_gross_exposure_pct + 0.08  # caps apply at the fill
    by_day: dict[str, int] = {}
    for t in run.trades:
        assert t.shares >= 1
        assert t.shares * t.fill <= limits.max_position_pct * max(run.equity) + 1e-6
        by_day[t.entry_session] = by_day.get(t.entry_session, 0) + 1
    assert max(by_day.values()) <= limits.max_positions
    flat = swing.replay(
        panel, swing.build(panel, symbols, "IDX", "R3"), start=a, end=b,
        start_equity=5000.0, limits=limits, slippage_bps=10.0,
    )  # fmt: skip
    closed = sum(t.pnl for t in flat.trades)
    if flat.exposure[-1] == 0:  # flat at the end: equity is the start plus realised results
        assert flat.equity[-1] == pytest.approx(5000.0 + closed, abs=0.05)
    s = run.summary()
    assert s["trades"] == len(run.trades)
    assert s["max_drawdown_pct"] <= 0
    assert len(run.daily_returns()) == len(run.equity)


def test_drawdown_halt_stops_entries_and_only_the_reset_model_resumes(limits):
    panel, symbols = _market(seed=4, drift=-0.004)  # falling stocks under a rising index
    book = swing.build(panel, symbols, "IDX", "R1b")
    a, b = panel.sessions[280], panel.sessions[-1]
    kw = {"start": a, "end": b, "start_equity": 5000.0, "limits": limits, "slippage_bps": 10.0}
    strict = swing.replay(panel, book, halt_model="strict", **kw)
    reset = swing.replay(panel, book, halt_model="reset", **kw)
    assert len(strict.drawdown_halts) == 1
    halt = strict.drawdown_halts[0]
    assert all(t.entry_session <= panel.sessions[panel.index_of(date.fromisoformat(halt)) + 1]
               .isoformat() for t in strict.trades)  # fmt: skip
    assert reset.drawdown_halts[0] == halt
    assert len(reset.trades) > len(strict.trades)
    resumed = [t for t in reset.trades if t.entry_session > halt]
    first_allowed = panel.sessions[
        panel.index_of(date.fromisoformat(halt)) + swing.RESET_SESSIONS + 1
    ]
    assert min(t.entry_session for t in resumed) >= first_allowed.isoformat()


# ------------------------------------------------------------------ Monte Carlo and gates


def test_monte_carlo_checks_are_reproducible_and_directional():
    rng = np.random.default_rng(0)
    good = swing.account_paths(rng.normal(0.001, 0.005, 800))
    bad = swing.account_paths(rng.normal(-0.001, 0.005, 800))
    assert good == swing.account_paths(np.random.default_rng(0).normal(0.001, 0.005, 800))
    assert good["share_positive"] > 0.95
    assert bad["share_positive"] < 0.05
    assert good["final_return_pct"]["p5"] < good["final_return_pct"]["p50"]
    assert good["max_drawdown_pct"]["p95_worst"] <= good["max_drawdown_pct"]["median"] <= 0
    assert swing.account_paths(np.array([])) == {"paths": 0}

    def trades(mean: float, n: int) -> list[swing.StudyTrade]:
        return [
            swing.StudyTrade("X", "2020-01-02", "2020-01-03", "time", float(v))
            for v in np.random.default_rng(1).normal(mean, 1.0, n)
        ]

    edge = swing.edge_over_baseline(trades(0.3, 800), trades(0.0, 5000))
    assert edge["ci95"][0] > 0
    none = swing.edge_over_baseline(trades(0.0, 800), trades(0.0, 5000))
    assert none["ci95"][0] < 0 < none["ci95"][1]
    assert swing.edge_over_baseline([], trades(0.0, 10))["difference"] is None
    stats = swing.study_stats(trades(0.3, 800))
    assert stats["mean_r_95ci"][0] < stats["mean_r"] < stats["mean_r_95ci"][1]
    assert swing.study_stats([]) == {"trades": 0}


def test_gates_and_selection():
    study = {"trades": 400, "mean_r": 0.05, "mean_r_without_top5": 0.03, "mean_r_95ci": [0.01, 0.1]}
    edge, run, paths = {"ci95": [0.01, 0.2]}, {"net_pnl": 10.0}, {"share_positive": 0.93}
    assert swing.selection_failures(study, edge, run, paths) == []
    assert swing.selection_failures(
        {**study, "trades": 299, "mean_r_95ci": [-0.01, 0.1]},
        {"ci95": [-0.01, 0.2]},
        {"net_pnl": -1.0},
        {"share_positive": 0.89},
    ) == [
        "fewer than 300 trades",
        "MC1 lower bound not above 0",
        "MC2 lower bound not above 0",
        "portfolio net not positive",
        "MC3 below 90% positive paths",
    ]
    assert swing.holdout_failures({**study, "trades": 40}, {"mean_r": 0.0}, run) == []
    assert swing.holdout_failures({**study, "trades": 39}, {"mean_r": 0.06}, run) == [
        "fewer than 40 trades",
        "mean R not above the baseline",
    ]
    results = {
        c: {"failures": [], "portfolio": {"net_pnl": pnl}}
        for c, pnl in zip(swing.CANDIDATES, (100.0, 300.0, 200.0, 300.0, 50.0), strict=True)
    }
    results["T1"]["failures"] = ["mean R not above 0"]
    assert swing.select(results)[0] == ["R2", "R3"]
    assert len(swing.X60) == 60
    assert len(swing.Y60) == 60
    assert not set(swing.Y60) & (set(swing.X60) | set(swing.U80_EXTRA))
    assert not set(swing.X60) & set(swing.U80_EXTRA)
    dev, hold = swing.SAMPLES["development"], swing.SAMPLES["holdout"]
    assert dev[3] < hold[2]
    assert hold[3] < date(2026, 10, 26)


# ------------------------------------------------------------------ CLI


def test_cli_guards(config_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path / "data"))
    base = ["backtest-swing", "--config-dir", str(config_dir)]
    assert cli.main([*base, "--sample", "holdout"]) != 0  # needs --candidates
    assert cli.main([*base, "--sample", "unseen", "--candidates", "R1", "R2", "R3"]) != 0
    assert cli.main([*base, "--sample", "development", "--candidates", "R1"]) != 0
    assert cli.main([*base, "--sample", "development"]) != 0  # no cached bars
    assert cli.main([*base, "--sample", "development", "--equity", "0"]) != 0


def test_cli_confirmation_run_writes_the_report(
    config_dir: Path, tmp_path: Path, monkeypatch, capsys
):
    rng = np.random.default_rng(9)
    n = len(CAL.sessions_in_range(START, date(2026, 10, 23)))
    daily = {s: _walk(rng, n) for s in swing.X60}
    daily["SPY"] = _frame(np.linspace(100, 300, n))

    def fake(*_a: object, **_k: object):
        return {}, daily

    monkeypatch.setattr(cli, "_read_intraday_caches", fake)
    monkeypatch.setattr(swing, "MC_PATHS", 500)
    monkeypatch.setattr(swing, "BOOTSTRAP_RESAMPLES", 200)
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path / "data"))
    out = tmp_path / "report"
    code = cli.main(
        ["backtest-swing", "--config-dir", str(config_dir), "--sample", "unseen",
         "--candidates", "R1", "--output", str(out)]
    )  # fmt: skip
    assert code == 0
    report = json.loads((out / "swing_v1_backtest.json").read_text(encoding="utf-8"))
    res = report["results"]["R1"]
    assert report["universe"] == "X60"
    assert set(res["study"]) == {"5", "10", "20"}
    assert "R1b" in res["baseline"]
    assert set(res["other_runs"]) == {"strict@10", "reset@5", "reset@20"}
    assert res["account_paths"]["paths"] == 500
    assert len(report["equity_curves"]["R1"]["equity"]) == len(
        report["equity_curves"]["R1"]["sessions"]
    )
    assert "selection" not in report
    assert "swing_v1 unseen: X60" in capsys.readouterr().out


# ------------------------------------------------------------------ trend_v1


def test_trend_null_enters_on_week_ends_without_trend_or_regime():
    n = 420
    falling = np.linspace(200, 100, n)
    frames = {"X": _frame(falling), "IDX": _frame(falling)}
    panel, symbols = _panel(frames)
    tn = swing.build(panel, symbols, "IDX", "Tn")
    weekly = swing.week_ends(panel.sessions)
    late = np.arange(len(panel.sessions)) >= 300
    np.testing.assert_array_equal(tn.entry["X"][late], weekly[late])  # a downtrend still enters
    assert not swing.build(panel, symbols, "IDX", "Tb").entry["X"].any()
    assert swing.RULES["Tn"].overlap is False


def test_trend_gates():
    study = {"trades": 150, "mean_r": 0.3, "mean_r_without_top5": 0.1, "mean_r_95ci": [0.05, 0.6]}
    null = {"mean_r": 0.1}
    ok = ({"ci95": [0.01, 0.4]}, {"net_pnl": 5.0}, {"share_positive": 0.9})
    assert swing.trend_failures("unseen", study, null, *ok) == []
    assert swing.trend_failures(
        "unseen-growth",
        {**study, "trades": 149},
        null,
        {"ci95": [-0.01, 0.4]},
        {"net_pnl": 0.0},
        {"share_positive": 0.5},
    ) == [
        "fewer than 150 trades",
        "MC2 lower bound not above 0",
        "portfolio net not positive",
        "MC3 below 90% positive paths",
    ]
    short = {"trades": 30, "mean_r": 0.2}
    weak = ({"ci95": [-1.0, 1.0]}, {"net_pnl": 1.0}, {"share_positive": 0.1})
    assert swing.trend_failures("holdout", short, null, *weak) == []  # the short set only
    assert swing.trend_failures("holdout", {"trades": 29, "mean_r": 0.05}, null, *weak) == [
        "fewer than 30 trades",
        "mean R not above the null",
    ]


def test_cli_trend_run_uses_the_null_and_both_rules(
    config_dir: Path, tmp_path: Path, monkeypatch, capsys
):
    rng = np.random.default_rng(13)
    n = len(CAL.sessions_in_range(START, date(2026, 10, 23)))
    daily = {s: _walk(rng, n) for s in swing.Y60}
    daily["QQQ"] = _frame(np.linspace(100, 300, n))
    monkeypatch.setattr(cli, "_read_intraday_caches", lambda *_a, **_k: ({}, daily))
    monkeypatch.setattr(swing, "MC_PATHS", 500)
    monkeypatch.setattr(swing, "BOOTSTRAP_RESAMPLES", 200)
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path / "data"))
    out = tmp_path / "report"
    code = cli.main(
        ["backtest-trend", "--config-dir", str(config_dir), "--sample", "unseen-growth",
         "--output", str(out)]
    )  # fmt: skip
    assert code == 0
    report = json.loads((out / "trend_v1_backtest.json").read_text(encoding="utf-8"))
    assert report["strategy"] == "trend_v1"
    assert report["universe"] == "Y60"
    assert set(report["results"]) == {"T1", "T2"}
    for res in report["results"].values():
        assert set(res["baseline"]) == {"Tn"}
        assert set(res["reference"]) == {"Tb"}
    assert "selection" not in report
    assert "trend_v1 unseen-growth: Y60" in capsys.readouterr().out
