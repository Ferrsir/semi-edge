"""Required backtest configuration fields and pure shared entry/exit boundaries."""

from dataclasses import replace
from datetime import date

import pytest

from semiedge.backtest.costs import fill_cost
from semiedge.backtest.fills import market_price, protective_fill
from semiedge.config.loader import (
    ConfigError,
    load_app_config,
    load_backtest_settings,
    run_fingerprint,
)
from semiedge.config.models import BacktestSettings, CostSettings
from semiedge.risk.proposals import build_entry_proposals
from semiedge.strategy.base import SignalEvaluation
from semiedge.strategy.exits import exit_levels, held_sessions, time_exit_due


@pytest.mark.parametrize("value", [-1, 10000, 1e6, float("nan"), float("inf"), True, '"10"'])
def test_bad_slippage_is_config_error(tmp_path, value):
    text = (
        f"initial_equity: 10000\ncosts:\n  slippage_bps: {value}\n"
        "  commission_per_share: 0\n  commission_per_order: 0\n"
        "  sell_fee_rate: 0\nearnings_policy: waive_unavailable\n"
    )
    (tmp_path / "backtest.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_backtest_settings(tmp_path)


def test_required_and_numeric_settings(config_dir, tmp_path):
    settings = load_backtest_settings(config_dir)
    assert settings.costs.slippage_bps < 10000
    for key in settings.model_dump():
        payload = settings.model_dump()
        del payload[key]
        with pytest.raises(ValueError, match=r".+"):
            BacktestSettings.model_validate(payload)
    for field in settings.costs.model_dump():
        payload = settings.costs.model_dump()
        del payload[field]
        with pytest.raises(ValueError, match=r".+"):
            CostSettings.model_validate(payload)
        payload[field] = float("inf")
        with pytest.raises(ValueError, match=r".+"):
            CostSettings.model_validate(payload)
    edge = settings.costs.model_dump() | {"slippage_bps": 9999.99}
    assert CostSettings.model_validate(edge).slippage_bps == 9999.99
    with pytest.raises(ConfigError):
        load_backtest_settings(tmp_path)
    app = load_app_config(config_dir)
    assert run_fingerprint(app, settings) == run_fingerprint(app, settings)
    assert run_fingerprint(app, settings) != run_fingerprint(
        app, settings.model_copy(update={"initial_equity": 1.0})
    )
    with pytest.raises(ValueError, match="duplicate"):
        run_fingerprint(app, settings, settings)


def test_exits_and_proposals(scenario):
    request, _, _, calendar = scenario()
    levels = exit_levels(12, 1.25, request.params)
    assert levels.stop == 9.5
    assert levels.target == 17
    ev = SignalEvaluation(
        "AAA",
        calendar.previous_session(request.start),
        "momentum_v1",
        "1",
        True,
        (),
        80.0,
        {},
        12.0,
        1.25,
        9.5,
        17.0,
        (),
        1,
    )
    kw = {
        "equity": 10000.0,
        "limits": request.risk,
        "params": request.params,
        "decision_session": ev.session,
        "data_as_of": ev.session,
    }
    (proposal,) = build_entry_proposals([ev], **kw)
    assert proposal.proposed_shares == 10  # risk budget 25 / 2.5, allocation budget 500 / 12
    assert build_entry_proposals([replace(ev, eligible=False)], **kw) == ()
    assert build_entry_proposals([ev], **(kw | {"equity": 1.0}))[0].proposed_shares == 0
    for field in ("session", "score", "close", "atr", "proposed_stop", "proposed_target", "rank"):
        with pytest.raises(ValueError, match=r".+"):
            build_entry_proposals([replace(ev, **{field: None})], **kw)
    with pytest.raises(ValueError, match="divergence"):
        build_entry_proposals([replace(ev, proposed_target=18)], **kw)
    assert held_sessions(date(2024, 1, 12), date(2024, 1, 16), calendar) == 2  # MLK holiday
    assert time_exit_due(date(2024, 1, 12), date(2024, 1, 16), 2, calendar)
    with pytest.raises(ValueError, match=r".+"):
        held_sessions(request.start, calendar.previous_session(request.start), calendar)
    with pytest.raises(ValueError, match=r".+"):
        time_exit_due(request.start, request.start, 0, calendar)
    for entry, atr in [(0, 1), (12, 0), (float("inf"), 1), (1, 10)]:
        with pytest.raises(ValueError, match=r".+"):
            exit_levels(entry, atr, request.params)


def test_exact_slipped_fill_invalidation_and_numeric_guards():
    fill = market_price(99, 200, sell=False, context="AAA")
    assert fill == 100.98
    assert fill >= 100
    assert market_price(99, 0, sell=False, context="AAA") < 100
    assert protective_fill(
        opened=120,
        high=125,
        low=119,
        stop=90,
        target=100,
        gap=True,
        slippage_bps=200,
        context="AAA",
    ) == (100, "target_gap")
    with pytest.raises(ValueError, match=r"AAA.*fill_price"):
        market_price(1e308, 9999, sell=False, context="AAA")
    costs = CostSettings(
        slippage_bps=0.0, commission_per_share=0.0, commission_per_order=0.0, sell_fee_rate=0.0
    )
    with pytest.raises(ValueError, match=r"AAA.*notional"):
        fill_cost(1e308, 2, costs, sell=True, context="AAA")
