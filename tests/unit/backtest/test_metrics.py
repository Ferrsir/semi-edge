"""Hand-computed synthetic acceptance cases; no downloaded observations."""

import json
import math
from dataclasses import asdict, replace
from datetime import date

import pytest

from semiedge import cli
from semiedge.backtest.benchmarks import _benchmark_result, compare_benchmarks
from semiedge.backtest.engine import run_backtest
from semiedge.backtest.metrics import (
    Metric,
    compute_metrics,
    period_breakdown,
    regime_breakdown,
    worst_drawdowns,
)
from semiedge.backtest.metrics_report import metrics_report
from semiedge.config.models import AppConfig, CostSettings
from semiedge.config.settings import AppSettings
from semiedge.data.schema import DataFeed, DataSource


def result_for(scenario, values, *, initial=100.0):
    request, data, gate, calendar = scenario([(12, 13, 11, 12, 100)] * len(values))
    result = run_backtest(request, data, gate=gate, calendar=calendar)
    points = tuple(
        replace(p, equity=float(value), cash=float(value), gross_exposure=0.0)
        for p, value in zip(result.equity_curve, values, strict=True)
    )
    return replace(result, initial_equity=initial, equity_curve=points, trades=()), data, request


def test_every_metric_by_hand(scenario):
    """E0=100, closes 110,99,106; r=(1/10,-1/10,7/99).

    Mean r=7/297; sample variance=sum((r-mean)^2)/2; downside RMS=sqrt(.01/3).
    Return=6/100; CAGR=1.06^84-1. Drawdown=-.1, unrecovered duration=2.
    Trades: five shares 10->12 less $1 = $9; two shares 10->9 less $1 = -$3.
    Win rate=1/2; PF=9/3; expectancy=(9-3)/2; mean R=(.9-.3)/2;
    holding=(2+3)/2 includes exit days. Exposure=(1+.5+0)/3.
    Turnover=(5*(10+12)+2*(10+9))/((110+99+106)/3)*84; costs=2.
    """
    result, _, _ = result_for(scenario, [110, 99, 106])
    request, data, gate, calendar = scenario()
    base = run_backtest(request, data, gate=gate, calendar=calendar).trades[0]
    first = replace(
        base,
        shares=5,
        entry_price=10,
        exit_price=12,
        gross_pnl=10,
        net_pnl=9,
        costs=1,
        r_multiple=0.9,
        held_sessions=2,
        exit_reason="target",
    )
    second = replace(
        base,
        shares=2,
        entry_price=10,
        exit_price=9,
        gross_pnl=-2,
        net_pnl=-3,
        costs=1,
        r_multiple=-0.3,
        held_sessions=3,
        exit_reason="stop",
    )
    points = tuple(
        replace(p, gross_exposure=e)
        for p, e in zip(result.equity_curve, [110, 49.5, 0], strict=True)
    )
    m = compute_metrics(replace(result, trades=(first, second), equity_curve=points))
    mean = 7 / 297
    variance = ((0.1 - mean) ** 2 + (-0.1 - mean) ** 2 + (7 / 99 - mean) ** 2) / 2
    expected = {
        "net_return": 0.06,
        "cagr": 1.06**84 - 1,
        "max_drawdown": -0.1,
        "max_drawdown_sessions": 2,
        "sharpe": mean / math.sqrt(variance) * math.sqrt(252),
        "sortino": mean / math.sqrt(0.01 / 3) * math.sqrt(252),
        "win_rate": 0.5,
        "profit_factor": 3,
        "expectancy": 3,
        "expectancy_r": 0.3,
        "avg_holding_sessions": 2.5,
        "exposure": 0.5,
        "turnover": 148 / 105 * 84,
    }
    for name, value in expected.items():
        assert getattr(m, name).value == pytest.approx(value)
        assert getattr(m, name).reason is None
    assert m.sessions == 3
    assert m.trade_count == 2
    assert m.exit_reason_counts == {"stop": 1, "target": 1}
    assert m.total_costs == 2
    json.dumps(asdict(m), allow_nan=False)


@pytest.mark.parametrize("values", [[100], [100, 100, 100]])
def test_constant_and_no_trades(scenario, values):
    result, _, _ = result_for(scenario, values)
    m = compute_metrics(result)
    assert m.sharpe.reason == ("zero_volatility" if len(values) > 1 else "fewer_than_2_returns")
    assert m.sortino.reason == "no_downside_returns"
    assert m.win_rate.reason == m.expectancy.reason == m.expectancy_r.reason == "no_trades"
    assert m.avg_holding_sessions.reason == "no_trades"
    assert m.profit_factor.reason == "no_losing_trades"
    assert m.net_return.value == m.max_drawdown.value == m.turnover.value == 0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), 0, -1])
def test_invalid_equity_is_explicit_and_serializable(scenario, bad):
    result, _, _ = result_for(scenario, [100, bad])
    m = compute_metrics(result)
    assert m.net_return.reason == "invalid_equity_point"
    json.dumps(asdict(m), allow_nan=False)


def test_empty_missing_initial_and_invalid_trade(scenario):
    result, _, _ = result_for(scenario, [100])
    assert (
        compute_metrics(replace(result, equity_curve=())).net_return.reason
        == "no_sessions_in_period"
    )
    assert (
        compute_metrics(replace(result, initial_equity=None)).net_return.reason
        == "missing_or_invalid_initial_equity"
    )
    request, data, gate, calendar = scenario()
    traded = run_backtest(request, data, gate=gate, calendar=calendar)
    trade = replace(traded.trades[0], net_pnl=float("nan"))
    assert compute_metrics(replace(traded, trades=(trade,))).expectancy.reason == "invalid_trade"
    winners = replace(traded, trades=(replace(traded.trades[0], net_pnl=1),))
    assert compute_metrics(winners).profit_factor.reason == "no_losing_trades"


@pytest.mark.parametrize(("value", "reason"), [(None, None), (float("nan"), None), (1.0, "bad")])
def test_metric_invariant(value, reason):
    with pytest.raises(ValueError, match="metric"):
        Metric(value, reason)


def test_benchmark_three_sessions_with_costs(scenario):
    """Buy 98 at 101; entry cost=10.8; cash=91.2.

    Close marks: 10871.2,11851.2,12831.2 (cash plus 98 times close).
    Net=12831.2/10000-1; total costs=10.8, only the buy executes.
    Turnover=9898/11851.2*84; there are no closed trades.
    """
    costs = CostSettings(
        slippage_bps=100.0, commission_per_order=1.0, commission_per_share=0.1, sell_fee_rate=0.001
    )
    request, data, gate, calendar = scenario([(12, 13, 11, 12, 100)] * 3, costs=costs)
    bars = dict(data.bars)
    frame = bars["SMH"].copy()
    for i, close in enumerate([110, 120, 130], len(frame) - 3):
        frame.iloc[i] = [100 if i == len(frame) - 3 else close, close + 1, 99, close, 1000000]
    bars["SMH"] = frame
    data = replace(data, bars=bars)
    result = run_backtest(request, data, gate=gate, calendar=calendar)
    benchmark, _, _ = _benchmark_result(result, data, request.settings, "SMH")
    assert [p.equity for p in benchmark.equity_curve] == pytest.approx([10871.2, 11851.2, 12831.2])
    (comparison,) = compare_benchmarks(result, data, request.settings)
    assert comparison.metrics.net_return.value == pytest.approx(12831.2 / 10000 - 1)
    assert comparison.metrics.total_costs == pytest.approx(10.8)
    assert comparison.metrics.avg_holding_sessions.reason == "no_trades"
    assert comparison.metrics.trade_count == 0
    assert comparison.metrics.turnover.value == pytest.approx(9898 / 11851.2 * 84)
    assert comparison.metrics.expectancy_r.reason == "benchmark_has_no_risk_unit"
    assert comparison.excess_net_return.value == pytest.approx(
        compute_metrics(result).net_return.value - (12831.2 / 10000 - 1)
    )
    # Comparing the exact same equity series: corr=1, beta=1, excess=0.
    (same,) = compare_benchmarks(benchmark, data, request.settings)
    assert same.correlation.value == pytest.approx(1)
    assert same.beta.value == pytest.approx(1)
    assert same.excess_net_return.value == 0


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("missing", "missing_benchmark"),
        ("stale", "missing_or_stale_benchmark_bar"),
        ("feed", "benchmark_provenance_mismatch"),
        ("source", "benchmark_provenance_mismatch"),
    ],
)
def test_benchmark_missing_stale_provenance_retained(scenario, kind, reason):
    result, data, request = result_for(scenario, [100, 100, 100])
    if kind == "missing":
        data = replace(data, bars={})
    elif kind == "stale":
        data = replace(data, bars={"SMH": data.bars["SMH"].iloc[:-1]})
    elif kind == "feed":
        data = replace(data, feed=DataFeed.IEX)
    else:
        data = replace(data, source=DataSource.ALPACA)
    (comparison,) = compare_benchmarks(result, data, request.settings)
    assert comparison.symbol == "SMH"
    assert comparison.metrics.net_return.reason == reason
    json.dumps(asdict(comparison), allow_nan=False)


def test_zero_benchmark_variance_and_single_session(scenario):
    result, data, request = result_for(scenario, [100, 100, 100])
    (comparison,) = compare_benchmarks(result, data, request.settings)
    assert comparison.beta.reason == "zero_benchmark_variance"
    result, data, request = result_for(scenario, [100])
    (comparison,) = compare_benchmarks(result, data, request.settings)
    assert comparison.beta.reason == comparison.correlation.reason == "fewer_than_2_returns"


def test_periods_drawdowns_and_entry_attribution(scenario):
    """Closes 100,90,100,95: recovered DD=.1 duration=2; unrecovered=.05 duration=1."""
    result, _, _ = result_for(scenario, [100, 90, 100, 95])
    m = compute_metrics(result)
    assert m.max_drawdown.value == pytest.approx(-0.1)
    assert m.max_drawdown_sessions.value == 2
    rows = worst_drawdowns(result)
    assert [r.label for r in rows] == ["drawdown_1", "drawdown_2"]
    assert rows[0].start == result.start
    assert rows[0].end == result.equity_curve[1].session
    assert rows[1].metrics.net_return.value == pytest.approx(-0.05)
    periods = {
        "tail": (result.equity_curve[1].session, result.end),
        "absent": (date(2025, 1, 1), date(2025, 1, 2)),
    }
    tail, absent = period_breakdown(result, periods=periods)
    assert tail.metrics.net_return.value == pytest.approx(95 / 90 - 1)
    assert absent.metrics.net_return.reason == "no_sessions_in_period"
    assert worst_drawdowns(result, n=0) == ()
    with pytest.raises(ValueError, match="nonnegative"):
        worst_drawdowns(result, n=-1)


@pytest.mark.safety
def test_regime_prior_session_and_future_poisoning(scenario):
    """Prior closes 100,105,110 put first return ON; today's close is excluded."""
    result, data, request = result_for(scenario, [110])
    original = regime_breakdown(result, data, request.params)
    assert original.regime_on.net_return.value == pytest.approx(0.1)
    assert original.regime_off.net_return.reason == "no_sessions_in_period"
    frame = data.bars["SMH"].copy()
    # Valid OHLC on the scored day is irrelevant to yesterday's classification.
    frame.iloc[-1] = [1, 2, 0.5, 1, 100]
    poisoned = replace(data, bars={"SMH": frame})
    assert regime_breakdown(result, poisoned, request.params) == original
    missing = replace(data, bars={"SMH": frame.iloc[3:]})
    assert (
        regime_breakdown(result, missing, request.params).regime_on.net_return.reason
        == "regime_data_unavailable"
    )


def test_report_labels_windows_and_missing_data(scenario):
    result, data, _ = result_for(scenario, [110, 99, 106])
    report = metrics_report(result, data, command="synthetic fixture metrics")
    assert next(iter(report)) == "labels"
    assert report["labels"] == list(result.labels)
    assert report["backtest_command"] == result.command
    assert report["periods"][0]["label"] == "2024"
    assert report["window_benchmarks"]
    json.dumps(report, default=str, allow_nan=False)
    missing = metrics_report(result, None, command="synthetic fixture metrics")
    assert "UNAVAILABLE" in missing["labels"][-1]
    assert missing["benchmarks"][0]["metrics"]["net_return"]["value"] is None


def test_cli_artifact_round_trip_and_legacy_rejection(scenario, monkeypatch, tmp_path, capsys):
    request, data, gate, calendar = scenario()
    result = run_backtest(request, data, gate=gate, calendar=calendar)
    config = AppConfig(
        universe=request.universe,
        strategy=request.params,
        risk=request.risk,
        data=request.data_settings,
    )
    monkeypatch.setattr(cli, "_load", lambda args: (AppSettings(_env_file=None), config, "test"))

    class SyntheticSource:
        feed, source, adjustment = data.feed, data.source, data.adjustment

        def __init__(self, **kwargs):
            pass

        def get_daily_bars(self, *args):
            return data.bars

    monkeypatch.setattr(cli, "SyntheticMarketDataSource", SyntheticSource)
    path = result.save(tmp_path)
    original = path.read_bytes()
    assert cli.main(["metrics", str(path)]) == cli.EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert next(iter(report)) == "labels"
    assert report["benchmarks"][0]["metrics"]["sessions"] == 1
    assert path.read_bytes() == original
    raw = result.to_dict()
    raw.pop("initial_equity")
    raw.pop("metrics_context")
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(raw), encoding="utf-8")
    assert cli.main(["metrics", str(legacy)]) == cli.EXIT_DATA
    assert "missing run context" in capsys.readouterr().err


def test_qqq_row_is_not_dropped(scenario):
    from semiedge.config.models import UniverseMember

    result, data, request = result_for(scenario, [100, 100])
    universe = request.universe.model_copy(
        update={
            "members": (
                *request.universe.members,
                UniverseMember(symbol="QQQ", group="test", role="benchmark"),
            ),
            "comparison_benchmarks": ("SMH", "QQQ"),
        }
    )
    context = {**result.metrics_context, "universe": universe.model_dump(mode="json")}
    result = replace(result, metrics_context=context)
    rows = compare_benchmarks(result, data, request.settings)
    assert [row.symbol for row in rows] == ["SMH", "QQQ"]
    assert rows[0].metrics.net_return.value is not None
    assert rows[1].metrics.net_return.reason == "missing_benchmark"


def test_regime_compounds_selected_returns(scenario):
    result, data, request = result_for(scenario, [110, 99, 106])
    frame = data.bars["SMH"].copy()
    frame.iloc[-3] = [90, 91, 89, 90, 100]
    frame.iloc[-2] = [90, 91, 89, 90, 100]
    data = replace(data, bars={"SMH": frame})
    regime = regime_breakdown(result, data, request.params)
    assert regime.regime_on.sessions == 1
    assert regime.regime_off.sessions == 2
    assert regime.regime_on.net_return.value == pytest.approx(0.1)
    assert regime.regime_off.net_return.value == pytest.approx(106 / 110 - 1)


def test_entry_period_retains_full_trade_and_stress_window(scenario):
    request, data, gate, calendar = scenario([(12, 13, 11, 12, 100)] * 3)
    result = run_backtest(request, data, gate=gate, calendar=calendar)
    (row,) = period_breakdown(result, periods={"entry_only": (result.start, result.start)})
    assert row.trade_count == row.metrics.trade_count == 1
    assert row.metrics.expectancy.value == result.trades[0].net_pnl
    days = calendar.sessions_in_range(date(2020, 1, 2), date(2020, 1, 6))
    points = tuple(replace(p, session=d) for p, d in zip(result.equity_curve, days, strict=True))
    shifted = replace(result, start=days[0], end=days[-1], equity_curve=points, trades=())
    report = metrics_report(shifted, None, command="synthetic fixture metrics")
    assert {p["label"] for p in report["periods"]} == {"2020", "2020Q1"}


def test_initial_peak_drawdown_and_cagr_overflow(scenario):
    result, _, _ = result_for(scenario, [90, 80])
    m = compute_metrics(result)
    assert m.max_drawdown.value == pytest.approx(-0.2)
    assert m.max_drawdown_sessions.value == 2
    (row,) = worst_drawdowns(result)
    assert row.metrics.net_return.value == pytest.approx(-0.2)
    result, _, _ = result_for(scenario, [10000])
    result = replace(result, initial_equity=1.0)
    m = compute_metrics(result)
    assert m.net_return.value == 9999
    assert m.cagr.reason == "non_finite_arithmetic"
    json.dumps(asdict(m), allow_nan=False)
