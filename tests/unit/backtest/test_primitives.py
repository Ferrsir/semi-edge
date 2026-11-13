"""Exact offline accounting fixtures for the shared backtest/paper primitives."""

from datetime import UTC, date, datetime

import pytest

from semiedge.backtest.costs import fill_cost, market_price
from semiedge.config.models import CostSettings, MomentumV1Params
from semiedge.data.calendar import TradingCalendar
from semiedge.strategy.exits import exit_levels, held_sessions, time_exit_due


def costs(**overrides):
    return CostSettings.model_validate(
        {
            "slippage_bps": 10.0,
            "commission_per_share": 0.01,
            "commission_per_order": 1.0,
            "sell_fee_rate": 0.001,
            **overrides,
        }
    )


def test_hand_calculated_prices_and_fill_costs():
    config = costs()
    assert market_price(100, "buy", config) == pytest.approx(100.1)
    assert market_price(100, "sell", config) == pytest.approx(99.9)
    assert fill_cost(100.1, 10, "buy", config) == pytest.approx(1.1)
    assert fill_cost(99.9, 10, "sell", config) == pytest.approx(2.099)
    # Slippage is incorporated into execution prices, not charged a second time.
    assert fill_cost(100, 10, "buy", costs(slippage_bps=500)) == pytest.approx(1.1)


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_zero_cost_and_nonmutating_inputs(side):
    config = costs(slippage_bps=0, commission_per_share=0, commission_per_order=0, sell_fee_rate=0)
    original = config.model_dump()
    assert market_price(100, side, config) == 100
    assert fill_cost(100, 10, side, config) == 0
    assert config.model_dump() == original


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "100", 10**1000])
def test_invalid_price_inputs(value):
    with pytest.raises(
        ValueError, match=r"price|shares|cost|exit|holding|session|validation|notional"
    ):
        market_price(value, "buy", costs())
    with pytest.raises(
        ValueError, match=r"price|shares|cost|exit|holding|session|validation|notional"
    ):
        fill_cost(value, 1, "sell", costs())


@pytest.mark.parametrize("shares", [0, -1, 1.0, True, "1", 10**1000])
def test_invalid_or_unrepresentable_share_counts(shares):
    with pytest.raises(
        ValueError, match=r"price|shares|cost|exit|holding|session|validation|notional"
    ):
        fill_cost(100, shares, "buy", costs())


@pytest.mark.parametrize("side", ["BUY", "short", None, 1])
def test_invalid_sides(side):
    with pytest.raises(ValueError, match="side"):
        market_price(100, side, costs())
    with pytest.raises(ValueError, match="side"):
        fill_cost(100, 1, side, costs())


def test_arithmetic_overflows_and_bypassed_models_fail_closed():
    with pytest.raises(
        ValueError, match=r"price|shares|cost|exit|holding|session|validation|notional"
    ):
        market_price(1.7e308, "buy", costs(slippage_bps=9999))
    with pytest.raises(
        ValueError, match=r"price|shares|cost|exit|holding|session|validation|notional"
    ):
        market_price(5e-324, "sell", costs(slippage_bps=9999))
    with pytest.raises(
        ValueError, match=r"price|shares|cost|exit|holding|session|validation|notional"
    ):
        fill_cost(1.7e308, 2, "sell", costs())
    with pytest.raises(
        ValueError, match=r"price|shares|cost|exit|holding|session|validation|notional"
    ):
        fill_cost(100, 10, "buy", costs(commission_per_share=1.7e308))
    for name, value in [
        ("slippage_bps", 10_000),
        ("sell_fee_rate", float("nan")),
        ("commission_per_order", -1),
    ]:
        invalid = costs().model_copy(update={name: value})
        with pytest.raises(
            ValueError, match=r"price|shares|cost|exit|holding|session|validation|notional"
        ):
            market_price(100, "buy", invalid)
    with pytest.raises(ValueError, match="CostSettings"):
        fill_cost(100, 1, "buy", {})


def test_exit_levels_use_decision_reference_not_any_fill():
    params = MomentumV1Params()
    result = exit_levels(100, 2, params)
    assert result.stop == 100 - params.stop_atr_multiple * 2
    assert result.target == 100 + params.target_atr_multiple * 2


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "2", 10**1000])
def test_exit_invalid_inputs(value):
    for entry, atr in ((value, 2), (100, value)):
        with pytest.raises(
            ValueError, match=r"price|shares|cost|exit|holding|session|validation|notional"
        ):
            exit_levels(entry, atr, MomentumV1Params())


def test_exit_level_overflow_nonpositive_stop_and_rounding_collapse():
    with pytest.raises(
        ValueError, match=r"price|shares|cost|exit|holding|session|validation|notional"
    ):
        exit_levels(100, 100, MomentumV1Params())
    for field, value in [
        ("target_atr_multiple", 1.7e308),
        ("stop_atr_multiple", float("inf")),
        ("stop_atr_multiple", -1),
        ("target_atr_multiple", True),
    ]:
        with pytest.raises(
            ValueError, match=r"price|shares|cost|exit|holding|session|validation|notional"
        ):
            exit_levels(100, 2, MomentumV1Params().model_copy(update={field: value}))
    with pytest.raises(
        ValueError, match=r"price|shares|cost|exit|holding|session|validation|notional"
    ):
        exit_levels(1e308, 1, MomentumV1Params())  # additions/subtractions round back to entry


@pytest.mark.safety
def test_holding_count_skips_holidays_and_exit_is_decided_before_next_open():
    calendar = TradingCalendar()
    entry, after_holiday = date(2024, 1, 12), date(2024, 1, 16)
    assert held_sessions(entry, entry, calendar) == 1
    assert held_sessions(entry, after_holiday, calendar) == 2
    assert not time_exit_due(entry, entry, 2, calendar)
    assert time_exit_due(entry, after_holiday, 2, calendar)
    assert calendar.next_session(after_holiday) == date(2024, 1, 17)
    assert time_exit_due(entry, date(2024, 1, 17), 2, calendar)


@pytest.mark.parametrize(
    ("entry", "end"),
    [
        (date(2024, 1, 16), date(2024, 1, 12)),
        (date(2024, 1, 13), date(2024, 1, 16)),
        (date(2024, 1, 12), date(2024, 1, 15)),
        ("2024-01-12", date(2024, 1, 16)),
        (datetime(2024, 1, 12, tzinfo=UTC), date(2024, 1, 16)),
    ],
)
def test_invalid_holding_endpoints(entry, end):
    with pytest.raises(
        ValueError, match=r"price|shares|cost|exit|holding|session|validation|notional"
    ):
        held_sessions(entry, end, TradingCalendar())


@pytest.mark.parametrize("maximum", [0, -1, True, 2.0])
def test_invalid_holding_maximum(maximum):
    with pytest.raises(
        ValueError, match=r"price|shares|cost|exit|holding|session|validation|notional"
    ):
        time_exit_due(date(2024, 1, 12), date(2024, 1, 16), maximum, TradingCalendar())
