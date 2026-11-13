"""Exact synthetic fill scenarios for the §3.2 timeline."""

import json
from dataclasses import replace

import pytest

from semiedge.backtest.engine import run_backtest
from semiedge.config.models import CostSettings
from semiedge.reporting import SYNTHETIC_BANNER


def run(case):
    request, data, gate, calendar = case
    return run_backtest(request, data, gate=gate, calendar=calendar)


@pytest.mark.parametrize(
    ("tail", "reason", "price"),
    [
        ([(12, 13, 11, 12, 100), (9, 10, 8, 9, 100)], "stop_gap", 9),
        ([(12, 18, 9, 12, 100)], "stop", 9.5),
        ([(12, 13, 11, 12, 100), (18, 19, 17, 18, 100)], "target_gap", 17),
        ([(9, 10, 8, 9, 100)], "entry_gap_flatten", 9),
        ([(18, 19, 17, 18, 100)], "entry_gap_flatten", 18),
        ([(12, 18, 11, 12, 100)], "target", 17),
        ([(12, 13, 11, 12, 100)], "end_of_window", 12),
    ],
)
def test_timeline(scenario, tail, reason, price):
    result = run(scenario(tail))
    (trade,) = result.trades
    assert trade.stop == 9.5
    assert trade.target == 17
    assert trade.exit_reason == reason
    assert trade.exit_price == price
    assert trade.net_pnl == (trade.exit_price - trade.entry_price) * trade.shares
    assert len(result.equity_curve) == len(tail)
    last = result.equity_curve[-1]
    assert last.equity == last.cash == 10000 + trade.net_pnl
    assert last.gross_exposure == last.positions == 0


def test_time_exit_is_next_open_and_counts_entry(scenario):
    result = run(scenario([(12, 13, 11, 12, 100), (13, 14, 12, 13, 100)], holding=1))
    (trade,) = result.trades
    assert trade.exit_reason == "time"
    assert trade.exit_price == 13
    assert trade.held_sessions == 2


def test_missing_entry_bar_is_cancelled(scenario):
    request, data, gate, calendar = scenario()
    bars = dict(data.bars)
    bars["AAA"] = bars["AAA"].iloc[:-1]
    result = run((request, replace(data, bars=bars), gate, calendar))
    assert not result.trades
    assert result.cancelled_entries[0].reason == "entry_no_bar"


def test_missing_held_bar_is_marked_at_previous_close(scenario):
    request, data, gate, calendar = scenario([(12, 13, 11, 12, 100)] * 3)
    bars = dict(data.bars)
    bars["AAA"] = bars["AAA"].drop(bars["AAA"].index[-2])
    result = run((request, replace(data, bars=bars), gate, calendar))
    assert result.data_gaps == ((calendar.previous_session(request.end), "AAA"),)
    assert result.equity_curve[0].equity == result.equity_curve[1].equity


def test_costs_and_final_liquidation(scenario):
    costs = CostSettings(
        slippage_bps=10.0, commission_per_share=0.0, commission_per_order=1.0, sell_fee_rate=0.001
    )
    result = run(scenario(costs=costs))
    (trade,) = result.trades
    assert trade.entry_price == pytest.approx(12 * 1.001)
    assert trade.exit_price == pytest.approx(12 * 0.999)
    assert trade.costs == pytest.approx(2 + trade.shares * 12 * 0.999 * 0.001)
    assert trade.net_pnl == pytest.approx((12 * 0.999 - 12 * 1.001) * trade.shares - trade.costs)
    assert result.equity_curve[-1].equity == pytest.approx(10000 + trade.net_pnl)
    assert len(result.equity_curve) == 1


def test_slipped_entry_invalidation(scenario):
    # Decision levels are 9.5/17; an open below the target crosses it only after slippage.
    high = 17 / 1.02
    with_slip = run(scenario([(high, high + 0.1, high - 0.1, high, 100)], slip=200))
    without_slip = run(scenario([(high, high + 0.1, high - 0.1, high, 100)]))
    assert with_slip.trades[0].exit_reason == "entry_gap_flatten"
    assert without_slip.trades[0].exit_reason == "end_of_window"


def test_deterministic_and_exclusive_artifact(scenario, tmp_path):
    case = scenario(count=2)
    result = run(case)
    request, data, gate, calendar = case
    reordered = replace(data, bars=dict(reversed(list(data.bars.items()))))
    assert (
        result.deterministic_payload()
        == run((request, reordered, gate, calendar)).deterministic_payload()
    )
    assert SYNTHETIC_BANNER in result.labels
    assert "PRICE RETURN: dividends excluded" in result.labels
    path = result.save(tmp_path)
    assert path.is_relative_to(tmp_path / "synthetic")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert next(iter(payload)) == "labels"
    assert payload["trades"][0]["entry_session"] == request.start.isoformat()
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        result.save(tmp_path)
    assert path.read_bytes() == before


def test_cost_overflow_aborts_with_trade_context(scenario):
    costs = CostSettings(
        slippage_bps=0.0, commission_per_share=1e308, commission_per_order=0.0, sell_fee_rate=0.0
    )
    request, data, gate, calendar = scenario(costs=costs)
    with pytest.raises(ValueError, match=r"AAA-20240109.*cost"):
        run_backtest(request, data, gate=gate, calendar=calendar)


def test_time_exit_survives_missing_open(scenario):
    request, data, gate, calendar = scenario([(12, 13, 11, 12, 100)] * 3, holding=1)
    bars = dict(data.bars)
    bars["AAA"] = bars["AAA"].drop(bars["AAA"].index[-2])
    result = run((request, replace(data, bars=bars), gate, calendar))
    assert result.trades[0].exit_reason == "time"
    assert result.trades[0].exit_session == request.end


def test_gap_overshoot_is_recorded(scenario):
    # Tighten position cap: decision allocates at C_t; next open rises inside the fixed target.
    request, data, _, calendar = scenario([(16, 16.5, 15, 16, 100)])
    limits = request.risk.model_copy(update={"max_position_pct": 0.005})
    request = replace(request, risk=limits)
    from semiedge.risk.manager import RiskManager, UnavailableEarningsCalendar

    gate = RiskManager(
        limits,
        calendar=calendar,
        earnings=UnavailableEarningsCalendar(),
        tradable_symbols=frozenset(request.universe.tradable_symbols),
        earnings_waiver="backtest_unavailable",
    )
    result = run((request, data, gate, calendar))
    assert (request.start, "AAA", "R11_position_cap") in result.cap_overshoots
    assert any(label.startswith("GAP OVERSHOOT") for label in result.labels)


def test_invalid_run_id_cannot_escape_output(scenario, tmp_path):
    result = run(scenario())
    with pytest.raises(ValueError, match="run_id"):
        replace(result, run_id="../../outside").save(tmp_path)
    assert not list(tmp_path.iterdir())


def test_final_missing_close_aborts(scenario):
    from semiedge.backtest.engine import BacktestInputError

    request, data, gate, calendar = scenario([(12, 13, 11, 12, 100)] * 2)
    bars = dict(data.bars)
    bars["AAA"] = bars["AAA"].iloc[:-1]
    with pytest.raises(BacktestInputError, match="end_of_window_no_bar"):
        run((request, replace(data, bars=bars), gate, calendar))


def test_exact_spec_slipped_fill_uses_engine(scenario):
    request, data, gate, calendar = scenario([(99, 99.5, 98.5, 99, 100)], slip=200)
    bars = {s: f.copy() for s, f in data.bars.items()}
    # Uniform rescale preserves conditions; target 17 becomes 100 at the decision close.
    bars["AAA"].iloc[:5, :4] *= 100 / 17
    data = replace(data, bars=bars)
    result = run((request, data, gate, calendar))
    (trade,) = result.trades
    assert trade.target == pytest.approx(100)
    assert trade.entry_price == 100.98
    assert trade.exit_reason == "entry_gap_flatten"
    zero = replace(
        request,
        settings=request.settings.model_copy(
            update={"costs": request.settings.costs.model_copy(update={"slippage_bps": 0.0})}
        ),
    )
    assert run((zero, data, gate, calendar)).trades[0].exit_reason == "end_of_window"


def test_f096_open_below_stop_invalidates_even_if_slipped_fill_is_above(scenario):
    """F-096: an open at or below the stop invalidates the entry; slippage cannot hide it.

    Stop 9.5, 200 bps: open 9.4 fills the entry at 9.588 (> stop). The position is flattened
    at the open, 9.4 * 0.98, not stopped later at the more favourable 9.5 * 0.98.
    """
    (trade,) = run(scenario([(9.4, 9.6, 9.3, 9.4, 100)], slip=200)).trades
    assert trade.entry_price == pytest.approx(9.4 * 1.02)
    assert trade.exit_reason == "entry_gap_flatten"
    assert trade.exit_price == pytest.approx(9.4 * 0.98)
