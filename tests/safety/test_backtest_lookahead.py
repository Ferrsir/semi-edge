"""Backtest no-look-ahead, risk parity and offline wiring invariants."""

from dataclasses import replace

import numpy as np
import pytest

from backtest_fixtures import scenario as scenario  # noqa: PLC0414 - pytest fixture export
from semiedge.backtest.engine import BacktestInputError, run_backtest
from semiedge.data.schema import DataFeed, DataSource
from semiedge.risk.manager import RiskManager, UnavailableEarningsCalendar

pytestmark = pytest.mark.safety


def test_future_poisoning_and_deletion(scenario):
    request, data, gate, calendar = scenario([(12, 13, 9, 12, 100)] + [(12, 13, 11, 12, 100)] * 3)
    baseline = run_backtest(request, data, gate=gate, calendar=calendar)
    rng = np.random.default_rng(79094)  # fixed seed: failures reproduce exactly
    cutoffs = [
        calendar.previous_session(request.start),
        *calendar.sessions_in_range(
            request.start,
            request.end,
        ),
    ]
    for cutoff in cutoffs:
        bars = {s: f.copy() for s, f in data.bars.items()}
        for frame in bars.values():
            future = frame.index > str(cutoff)
            count = int(future.sum())
            opened = rng.uniform(1.0, 1000.0, count)
            closed = rng.uniform(1.0, 1000.0, count)
            frame.loc[future, "open"] = opened
            frame.loc[future, "close"] = closed
            frame.loc[future, "high"] = np.maximum(opened, closed) + rng.uniform(0, 10, count)
            frame.loc[future, "low"] = np.minimum(opened, closed) * rng.uniform(0.5, 1, count)
            frame.loc[future, "volume"] = rng.integers(0, 1000000, count)
        poisoned = run_backtest(request, replace(data, bars=bars), gate=gate, calendar=calendar)
        deleted = run_backtest(
            request,
            replace(data, bars={s: f.loc[: str(cutoff)] for s, f in bars.items()}),
            gate=gate,
            calendar=calendar,
        )
        for other in (poisoned, deleted):
            assert [d for d in baseline.risk_decisions if d.proposal.decision_session < cutoff] == [
                d for d in other.risk_decisions if d.proposal.decision_session < cutoff
            ]
            assert [e for e in baseline.equity_curve if e.session < cutoff] == [
                e for e in other.equity_curve if e.session < cutoff
            ]
            assert [t for t in baseline.trades if t.exit_session <= cutoff] == [
                t for t in other.trades if t.exit_session <= cutoff
            ]


def test_limits_must_match(scenario):
    request, data, gate, calendar = scenario()
    tighter = request.risk.model_copy(update={"max_positions": 1})
    with pytest.raises(ValueError, match="limits differ"):
        run_backtest(replace(request, risk=tighter), data, gate=gate, calendar=calendar)


@pytest.mark.parametrize(
    "change", ["synthetic", "feed_column", "source_column", "regime", "earnings"]
)
def test_invalid_inputs_fail_closed(scenario, change):
    request, data, gate, calendar = scenario()
    if change == "synthetic":
        data = replace(data, feed=DataFeed.SIP)
    elif change in {"feed_column", "source_column"}:
        bars = {s: f.copy() for s, f in data.bars.items()}
        bars["AAA"][change.removesuffix("_column")] = (
            "alpaca" if change == "source_column" else "sip"
        )
        data = replace(data, bars=bars)
    elif change == "regime":
        data = replace(data, bars={s: f.iloc[-1:] for s, f in data.bars.items()})
    else:
        request = replace(
            request,
            settings=request.settings.model_copy(update={"earnings_policy": "require_data"}),
        )
    with pytest.raises(BacktestInputError):
        run_backtest(request, data, gate=gate, calendar=calendar)


def test_risk_parity_and_loss_halt(scenario):
    # Tighten the halt threshold so the hand-built stop loss trips it; no ceilings raised.
    request, data, _, calendar = scenario(
        [(12, 13, 11, 12, 100), (9, 10, 8, 9, 100), (12, 13, 11, 12, 100)], count=2
    )
    limits = request.risk.model_copy(update={"daily_loss_halt_pct": 0.001, "max_positions": 1})
    request = replace(request, risk=limits)
    gate = RiskManager(
        limits,
        calendar=calendar,
        earnings=UnavailableEarningsCalendar(),
        tradable_symbols=frozenset(request.universe.tradable_symbols),
        earnings_waiver="backtest_unavailable",
    )
    result = run_backtest(request, data, gate=gate, calendar=calendar)
    assert max(p.positions for p in result.equity_curve) <= limits.max_positions
    loss_day = calendar.next_session(request.start)
    assert any(day == loss_day and kind.value == "daily_loss" for day, kind, _ in result.halts)
    assert not any(
        d.approved and d.proposal.decision_session == loss_day for d in result.risk_decisions
    )
    for point in result.equity_curve:
        assert point.gross_exposure / point.equity <= limits.max_gross_exposure_pct


def test_fixture_requires_manifest(scenario):
    request, data, gate, calendar = scenario()
    fixture = replace(data, source=DataSource.FIXTURE, feed=DataFeed.SIP)
    with pytest.raises(BacktestInputError, match="manifest"):
        run_backtest(request, fixture, gate=gate, calendar=calendar)
    result = run_backtest(
        request, replace(fixture, fixture_manifest="handwritten:test"), gate=gate, calendar=calendar
    )
    assert result.labels[0].endswith("handwritten:test")


def test_exact_daily_loss_halt_and_latched_drawdown(scenario):
    request, data, _, calendar = scenario(
        [(12, 13, 11, 12, 100), (6, 7, 5, 6, 100), (12, 13, 11, 12, 100), (12, 13, 11, 12, 100)],
        count=2,
    )
    limits = request.risk.model_copy(update={"drawdown_halt_pct": 0.01})
    request = replace(request, risk=limits)
    gate = RiskManager(
        limits,
        calendar=calendar,
        earnings=UnavailableEarningsCalendar(),
        tradable_symbols=frozenset(request.universe.tradable_symbols),
        earnings_waiver="backtest_unavailable",
    )
    result = run_backtest(request, data, gate=gate, calendar=calendar)
    assert result.equity_curve[1].equity / result.equity_curve[0].equity - 1 == pytest.approx(
        -0.012
    )
    assert sum(kind.value == "drawdown" for _, kind, _ in result.halts) == 1
    assert sum(kind.value == "daily_loss" for _, kind, _ in result.halts) == 1
    assert all(
        not d.approved
        for d in result.risk_decisions
        if d.proposal.decision_session >= calendar.next_session(request.start)
    )
