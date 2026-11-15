"""skill_follow_v1 (#173): setup rules, trade arithmetic, no look-ahead, gates and CLI guards."""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from semiedge import cli
from semiedge.backtest import skill_follow as sf
from semiedge.backtest.intraday_signals import SessionBars
from semiedge.data.calendar import TradingCalendar
from semiedge.data.intraday import session_bar_starts

CAL = TradingCalendar("XNYS")
N = sf.FULL_SESSION_BARS


# ------------------------------------------------------------------ the skill's daily setups


def _reference_rsi(closes: list[float], n: int = 14) -> float | None:
    """The skill's documented Wilder RSI, written out independently."""
    if len(closes) < n + 1:
        return None
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def _reference_setups(c: list[float]) -> set[str]:
    """The five rules as the skill's SKILL.md states them, on closes (oldest first, >= 30)."""
    last = c[-1]
    tags = set()
    if last > max(c[-21:-1]):
        tags.add("breakout_20d")
    if c[-6] / c[-16] - 1 > 0.10 and (max(c[-5:]) - min(c[-5:])) / last < 0.05:
        tags.add("bull_flag")
    if c[-2] < sum(c[-21:-1]) / 20 and last > sum(c[-20:]) / 20:
        tags.add("reclaim_ma20")
    r, r_prev = _reference_rsi(c), _reference_rsi(c[:-1])
    if r is not None and r < 30:
        tags.add("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.add("oversold_bounce")
    return tags


def test_daily_setups_match_the_documented_rules_on_random_walks():
    rng = np.random.default_rng(7)
    seen: set[str] = set()
    for _ in range(6):
        closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, 160)))
        got = sf.daily_setups(pd.Series(closes))
        for t in range(29, 160):
            want = _reference_setups(list(closes[: t + 1]))
            assert {k for k in sf.SETUPS if got[k].iloc[t]} == want, t
            seen |= want
        assert not got.iloc[:29].to_numpy().any()  # fewer than 30 closes: nothing
    assert seen == set(sf.SETUPS)  # every rule fired at least once


def test_a_missing_close_inside_a_window_makes_the_rule_false():
    closes = pd.Series(np.linspace(100, 160, 60))
    assert sf.daily_setups(closes)["breakout_20d"].iloc[-1]
    closes.iloc[50] = np.nan
    assert not sf.daily_setups(closes)["breakout_20d"].iloc[-1]


# ------------------------------------------------------------------ daily panel helpers


def _daily(closes: np.ndarray, start: date = date(2025, 1, 2), spread: float = 1.0) -> pd.DataFrame:
    sessions = CAL.sessions_in_range(start, date(2026, 12, 31))[: len(closes)]
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": c,
            "high": c + spread,
            "low": c - spread,
            "close": c,
            "volume": np.full(len(c), 1e6),
        },
        index=pd.DatetimeIndex([pd.Timestamp(s) for s in sessions], name="session"),
    )


def _panel(frames: dict[str, pd.DataFrame], end: date = date(2026, 6, 30)) -> sf.DailyPanel:
    return sf.prepare_daily(frames, tuple(frames), end=end, calendar=CAL)


def test_panel_never_reads_after_end_and_marks_placeholders_missing():
    frame = _daily(np.linspace(100, 130, 120))
    frame.iloc[70, frame.columns.get_loc("high")] = frame.iloc[70]["low"]
    frame.iloc[70, frame.columns.get_loc("volume")] = 10
    end = frame.index[99].date()
    panel = _panel({"X": frame}, end=end)
    assert panel.sessions[-1] == end
    i70 = panel.index_of(frame.index[70].date())
    assert math.isnan(panel.c["X"][i70])
    first = panel.index_of(frame.index[0].date())
    assert not panel.eligible["X"][first + 58]
    assert panel.eligible["X"][first + 69]  # 60 bars and a seeded ATR, before the gap
    assert not panel.eligible["X"][i70]  # ATR is undefined on a missing bar
    later = frame.copy()
    later.iloc[100:, :4] *= 9  # bars after `end` must not change anything
    again = _panel({"X": later}, end=end)
    np.testing.assert_array_equal(panel.c["X"], again.c["X"])
    np.testing.assert_array_equal(panel.atr["X"], again.atr["X"])


def _study_frames() -> tuple[dict[str, pd.DataFrame], list[date]]:
    n = 100
    stock, idx = np.full(n, 100.0), np.full(n, 400.0)
    frames = {"X": _daily(stock, spread=1.0), "SMH": _daily(idx, spread=1.0)}
    return frames, [ts.date() for ts in frames["X"].index]


def test_daily_trade_exits_at_the_close_or_the_stop_with_costs():
    frames, days = _study_frames()
    x = frames["X"]
    # session 80: open 100, low 99, close 101 -> close exit; session 81: low 97 -> stop
    x.iloc[80, :4] = [100.0, 101.5, 99.5, 101.0]
    x.iloc[81, :4] = [101.0, 101.2, 97.0, 100.5]
    panel = _panel(frames)
    kw = {"start": days[80], "end": days[81]}
    t80, t81 = sf.daily_study(panel, ("X",), "SMH", "D0", slippage_bps=0.0, **kw)
    a79 = panel.atr["X"][panel.index_of(days[79])]
    a80 = panel.atr["X"][panel.index_of(days[80])]
    assert (t80.reason, t80.session) == ("close", days[80].isoformat())
    assert t80.r_multiple == pytest.approx((101.0 - 100.0) / a79)
    assert t81.reason == "stop"
    assert t81.r_multiple == pytest.approx(-1.0)  # stop = open - 1 ATR
    costly = sf.daily_study(panel, ("X",), "SMH", "D0", slippage_bps=10.0, **kw)[0]
    assert costly.r_multiple == pytest.approx((101.0 * 0.999 - 100.0 * 1.001) / a79)
    assert a80 > 0


def test_follow_masks_need_a_strong_index_day_and_split_laggards_from_leaders():
    frames, days = _study_frames()
    frames["Y"] = _daily(np.full(100, 50.0))
    for sym, close in (("SMH", 408.0), ("X", 101.0), ("Y", 51.5)):  # +2%, +1%, +3%
        frames[sym].iloc[80, frames[sym].columns.get_loc("close")] = close
        frames[sym].iloc[80, frames[sym].columns.get_loc("high")] = close + 1
    panel = _panel(frames)
    kw = {"start": days[81], "end": days[81], "slippage_bps": 0.0}
    lag = sf.daily_study(panel, ("X", "Y"), "SMH", "D6", **kw)
    lead = sf.daily_study(panel, ("X", "Y"), "SMH", "D7", **kw)
    assert [t.symbol for t in lag] == ["X"]
    assert [t.symbol for t in lead] == ["Y"]
    quiet = {"start": days[70], "end": days[79], "slippage_bps": 0.0}
    assert sf.daily_study(panel, ("X", "Y"), "SMH", "D6", **quiet) == []
    assert len(sf.daily_study(panel, ("X", "Y"), "SMH", "D0", **quiet)) == 20


def test_swing_exits_time_stop_and_gap_and_stays_inside_the_period():
    frames, days = _study_frames()
    x = frames["X"]
    panel0 = _panel(frames)
    kw = {"slippage_bps": 0.0}
    (t,) = sf.swing_study(panel0, ("X",), "SMH", "S0", start=days[80], end=days[82], **kw)
    assert (t.reason, t.r_multiple) == ("time", pytest.approx(0.0))
    # the exit session must be inside the period: a 2-session period holds no trade
    assert sf.swing_study(panel0, ("X",), "SMH", "S0", start=days[80], end=days[81], **kw) == []
    x.iloc[81, :4] = [90.0, 91.0, 89.0, 90.0]  # opens below the 2-ATR stop on session 2
    (gap,) = sf.swing_study(_panel(frames), ("X",), "SMH", "S0", start=days[80], end=days[82], **kw)
    a = panel0.atr["X"][panel0.index_of(days[79])]
    assert gap.reason == "stop_gap"
    assert gap.r_multiple == pytest.approx((90.0 - 100.0) / (2 * a))
    x.iloc[81, :4] = [100.0, 100.5, 80.0, 99.0]  # trades through the stop intraday
    (hit,) = sf.swing_study(_panel(frames), ("X",), "SMH", "S0", start=days[80], end=days[82], **kw)
    assert (hit.reason, hit.r_multiple) == ("stop", pytest.approx(-1.0))


# ------------------------------------------------------------------ 5-minute candidates


def _flat(price: float, n: int = N) -> SessionBars:
    p = np.full(n, float(price))
    return SessionBars(p.copy(), p + 0.1, p - 0.1, p.copy(), np.full(n, 1000.0))


def test_orb_triggers_on_the_first_close_above_the_range_with_the_skill_plan():
    b = _flat(100.0)
    b.h[1], b.lo[2] = 101.0, 99.0  # range 99.00..101.00, midpoint 100.00
    b.h[6] = 102.0  # a wick above is not a trigger
    b.c[9] = 101.5
    b.c[12] = 103.0  # later closes do not reset it
    sig = sf.orb_signal(b)
    assert sig is not None
    assert (sig.k, sig.stop, sig.r0) == (9, 100.0, pytest.approx(1.5))
    assert sig.target == pytest.approx(101.5 + 2 * 1.5)
    cut = SessionBars(*(np.where(np.arange(N) <= 9, x, np.nan) for x in (b.o, b.h, b.lo, b.c, b.v)))
    assert sf.orb_signal(cut) == sig  # nothing after the trigger bar is read


def test_orb_refusals():
    b = _flat(100.0)
    b.c[9] = 100.1  # equal to the range high: not above it
    assert sf.orb_signal(b) is None
    b.c[72] = 105.0  # closes at 15:35, after the 15:30 cutoff
    assert sf.orb_signal(b) is None
    b.c[40] = 105.0
    b.c[20] = np.nan  # a gap before the trigger
    assert sf.orb_signal(b) is None
    assert sf.orb_signal(_flat(100.0, n=42)) is None  # early close
    odd = _flat(100.0)
    odd.h[0], odd.lo[0] = 100.105, 99.0  # midpoint 99.5525 floors to 99.55
    odd.c[5] = 100.5
    assert sf.orb_signal(odd).stop == 99.55


def test_follow_at_ten_splits_laggards_and_leaders():
    idx = _flat(404.0)  # +1% on a 400 close
    args = {"pc_idx": 400.0, "atr_d1": 4.0}
    lag = sf.follow_signals(_flat(100.5), idx, pc=100.0, **args)
    lead = sf.follow_signals(_flat(102.0), idx, pc=100.0, **args)
    assert set(lag) == {"B0", "B1"}
    assert set(lead) == {"B0", "B2"}
    assert lag["B1"].stop == pytest.approx(100.5 - 2.0)
    assert math.isinf(lag["B1"].target)
    quiet = sf.follow_signals(_flat(102.0), _flat(401.0), pc=100.0, **args)  # index +0.25%
    assert set(quiet) == {"B0"}
    gap = _flat(102.0)
    gap.c[3] = np.nan
    assert sf.follow_signals(gap, idx, pc=100.0, **args) == {}


def _intraday(sessions: list[date], price_by_session: dict[date, np.ndarray]) -> pd.DataFrame:
    parts = []
    for s in sessions:
        starts = session_bar_starts(CAL, s)
        p = price_by_session[s][: len(starts)]
        parts.append(
            pd.DataFrame(
                {"open": p, "high": p + 0.1, "low": p - 0.1, "close": p, "volume": 1000.0},
                index=starts,
            )
        )
    return pd.concat(parts)


def _inputs(prices: dict[str, dict[date, np.ndarray]], sessions: list[date]) -> sf.Inputs:
    daily = {s: _daily(np.full(400, 400.0 if s == "SMH" else 100.0)) for s in prices}
    intraday = {s: _intraday(sessions, p) for s, p in prices.items()}
    panel = sf.prepare_daily(daily, tuple(daily), end=sessions[-1], calendar=CAL)
    bars = sf.prepare_intraday(
        intraday, tuple(prices), start=sessions[0], end=sessions[-1], calendar=CAL
    )
    symbols = tuple(s for s in prices if s != "SMH")
    return sf.Inputs(
        panel, symbols, "SMH", CAL, bars, sf.prepare_sessions(bars, panel, symbols, "SMH")
    )


def test_same_day_and_overnight_studies_end_to_end():
    d1, d2, d3 = CAL.sessions_in_range(date(2025, 6, 2), date(2025, 6, 4))
    up = np.full(N, 100.0)
    up[5:] = 101.5  # +1.5% by 10:00 against a flat 100 daily close
    up[70:] = 102.0
    idx = np.full(N, 404.0)  # +1% all day
    x = {d1: up, d2: np.full(N, 103.0), d3: np.full(N, 103.0)}
    smh = dict.fromkeys((d1, d2, d3), idx)
    inp = _inputs({"X": x, "SMH": smh}, [d1, d2, d3])
    kw = {"start": d1, "end": d3, "slippage_bps": 0.0}
    atr_d0 = inp.panel.atr["X"][inp.panel.index_of(d1) - 1]
    b2 = [t for t in sf.study(inp, "B2", **kw) if t.session == d1.isoformat()]
    assert sf.study(inp, "B1", **kw) == []  # X is ahead of the index every day
    (t,) = b2
    assert t.reason == "flat_1555"
    assert t.r_multiple == pytest.approx((102.0 - 101.5) / (0.5 * atr_d0))
    a0 = [t for t in sf.study(inp, "A0", **kw) if t.session == d1.isoformat()]
    assert len(a0) == 1  # the 09:55 bar closed above the 100.1 range high
    n1 = sf.study(inp, "N1", **kw)
    first = next(t for t in n1 if t.session == d1.isoformat())
    assert first.r_multiple == pytest.approx((103.0 - 102.0) / atr_d0)
    assert all(t.session != d3.isoformat() for t in sf.study(inp, "N0", **kw))  # exit is outside
    with pytest.raises(ValueError, match="intraday panel"):
        sf.study(sf.Inputs(inp.panel, ("X",), "SMH", CAL), "A0", **kw)


def test_zero_volume_bars_are_missing_and_half_days_are_skipped():
    half = date(2025, 11, 28)  # 13:00 close
    full = CAL.previous_session(half)
    frame = _intraday([full, half], {full: np.full(N, 100.0), half: np.full(N, 100.0)})
    frame.iloc[4, frame.columns.get_loc("volume")] = 0.0
    bars = sf.prepare_intraday({"X": frame}, ("X",), start=full, end=half, calendar=CAL)
    assert bars.sessions == [full]
    assert math.isnan(bars.c["X"][0, 4])
    assert not math.isnan(bars.c["X"][0, 5])


# ------------------------------------------------------------------ gates and selection


def _s(n: int, m: float, trimmed: float, low: float) -> dict[str, object]:
    return {"trades": n, "mean_r": m, "mean_r_without_top5": trimmed, "mean_r_95ci": [low, m + 0.1]}


def test_stats_and_baselines():
    s = sf.stats(np.array([1.0, -1.0, 0.5, 0.5, 2.0, -0.5, 0.25]))
    assert s["trades"] == 7
    assert s["mean_r"] == pytest.approx(0.3929, abs=1e-4)
    assert s["mean_r_95ci"][0] < s["mean_r"] < s["mean_r_95ci"][1]
    assert sf.stats(np.array([])) == {"trades": 0}
    assert [sf.baseline_of(c) for c in ("A0", "A3", "B1", "D2", "S7", "N1")] == [
        "B0", "A0", "B0", "D0", "S0", "N0",
    ]  # fmt: skip
    assert len(sf.CANDIDATES) == 19
    assert len(sf.U80_ADDED) == 64
    assert sf.DEVELOPMENT[1] < sf.HOLDOUT[0]
    assert sf.HOLDOUT[1] < date(2026, 10, 26)  # the inspected window is in no period
    assert sf.HISTORY[1] < sf.DEVELOPMENT[0]


def test_selection_takes_at_most_two_pairs_by_lower_bound_and_applies_every_gate():
    bad = _s(1000, -0.05, -0.06, -0.1)
    base = _s(5000, 0.0, 0.0, -0.01)

    def table(**good: dict[str, object]) -> dict[str, dict[str, object]]:
        out = dict.fromkeys(sf.CANDIDATES, bad)
        out.update(dict.fromkeys(sf.BASELINES, base))
        out.update(good)
        return out

    results = {
        "U16": table(
            B1=_s(400, 0.05, 0.04, 0.01),
            D6=_s(400, 0.20, 0.10, 0.05),  # fails the history check below
            A2=_s(299, 0.50, 0.40, 0.30),  # too few trades
            N1=_s(400, 0.03, 0.02, -0.001),  # interval includes zero
        ),
        "U80": table(B2=_s(900, 0.04, 0.03, 0.02), D7=_s(900, 0.06, 0.05, 0.03)),
    }
    hist_bad, hist_ok = {"mean_r": -0.01}, {"mean_r": 0.01}
    history = {
        "U16": {c: hist_bad for c in sf.CANDIDATES if sf.needs_history(c)},
        "U80": {c: hist_ok for c in sf.CANDIDATES if sf.needs_history(c)},
    }
    selected, notes = sf.select(results, history)
    assert selected == ["D7:U80", "B2:U80"]
    assert notes["B1:U16"] == []  # qualifies, but only two pairs go forward
    assert notes["D6:U16"] == ["history-check mean R not above 0"]
    assert notes["A2:U16"][0] == "fewer than 300 trades"
    assert notes["N1:U16"] == ["lower bound of the 95% interval not above 0"]
    assert sf.holdout_failures(_s(60, 0.02, 0.01, -0.5), base) == []
    assert sf.holdout_failures(_s(59, 0.02, -0.01, 0.1), base) == [
        "fewer than 60 trades",
        "mean R without the five best trades not above 0",
    ]


# ------------------------------------------------------------------ CLI


def test_cli_guards(config_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path / "data"))
    base = ["backtest-skill-follow", "--config-dir", str(config_dir)]
    assert cli.main([*base, "--period", "holdout"]) != 0  # needs --pairs
    assert cli.main([*base, "--period", "holdout", "--pairs", "Z9:U16"]) != 0
    assert cli.main([*base, "--period", "holdout", "--pairs", "A0:U16", "B1:U16", "B2:U16"]) != 0
    assert cli.main([*base, "--period", "development", "--pairs", "A0:U16"]) != 0
    assert cli.main([*base, "--period", "development"]) != 0  # no cached bars
    existing = tmp_path / "out"
    existing.mkdir()
    assert cli.main([*base, "--period", "development", "--output", str(existing)]) != 0


def test_cli_holdout_reports_only_the_requested_pair(
    config_dir: Path, tmp_path: Path, monkeypatch, capsys
):
    start, end = sf.HOLDOUT
    sessions = CAL.sessions_in_range(start, end)
    rng = np.random.default_rng(3)
    daily, intraday = {}, {}
    for sym in ("NVDA", "AMD", "SMH", "QQQ"):
        closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 480)))
        daily[sym] = _daily(closes, start=date(2025, 1, 2), spread=2.0)
        prices = {s: 100 * np.exp(np.cumsum(rng.normal(0, 0.002, N))) for s in sessions}
        intraday[sym] = _intraday(sessions, prices)

    def fake(*_a: object, **_k: object) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
        return intraday, daily

    monkeypatch.setattr(cli, "_read_intraday_caches", fake)
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path / "data"))
    out = tmp_path / "report"
    code = cli.main(
        ["backtest-skill-follow", "--config-dir", str(config_dir), "--period", "holdout",
         "--pairs", "A1:U16", "--output", str(out)]
    )  # fmt: skip
    assert code == 0
    report = json.loads((out / "skill_follow_v1_screen.json").read_text(encoding="utf-8"))
    assert set(report["results"]) == {"U16"}
    assert set(report["results"]["U16"]) == {"A1", "A0", "B0"}
    assert set(report["results"]["U16"]["B0"]) == {"5", "10", "20"}
    assert "A1:U16" in report["holdout"]
    assert "selection" not in report
    assert "HOLDOUT signal study A1:U16" in capsys.readouterr().out


def test_dip_replication_universe_needs_every_added_symbol(
    config_dir: Path, tmp_path: Path, monkeypatch
):
    seen: dict[str, tuple[str, ...]] = {}

    def fake(_dir: object, _config: object, _symbols: object, *, daily_symbols: tuple[str, ...]):
        seen["daily"] = daily_symbols
        frame = _daily(np.linspace(100, 130, 120))
        return {}, {"SMH": frame, "QCOM": frame}

    monkeypatch.setattr(cli, "_read_intraday_caches", fake)
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path / "data"))
    base = ["backtest-dip", "--config-dir", str(config_dir), "--period", "development"]
    assert cli.main([*base, "--universe", "u80-added"]) != 0  # 63 symbols have no bars
    assert seen["daily"] == (*sf.U80_ADDED, "SMH")
    assert cli.main([*base, "--universe", "u80"]) != 0
    assert len(seen["daily"]) == 16 + 64 + 1
    assert seen["daily"][16:80] == sf.U80_ADDED
