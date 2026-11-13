"""Required research assumptions and canonical settings fingerprints."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError, create_model

from semiedge.config.loader import (
    ConfigError,
    config_fingerprint,
    load_app_config,
    load_backtest_settings,
    run_fingerprint,
)
from semiedge.config.models import BacktestSettings, CostSettings

ROOT = Path(__file__).resolve().parents[3]


def payload():
    return {
        "initial_equity": 100_000.0,
        "costs": {
            "slippage_bps": 10.0,
            "commission_per_share": 0.0,
            "commission_per_order": 0.0,
            "sell_fee_rate": 0.0,
        },
        "earnings_policy": "waive_unavailable",
    }


def write(tmp_path, data):
    (tmp_path / "backtest.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_declared_assumptions_and_frozen_models():
    settings = load_backtest_settings(ROOT / "config")
    assert settings.model_dump() == payload()
    with pytest.raises(ValidationError):
        settings.initial_equity = 1


@pytest.mark.parametrize("field", list(payload()))
def test_required_top_level_fields(tmp_path, field):
    data = payload()
    del data[field]
    write(tmp_path, data)
    with pytest.raises(ConfigError):
        load_backtest_settings(tmp_path)


@pytest.mark.parametrize("field", list(payload()["costs"]))
def test_required_cost_fields(tmp_path, field):
    data = payload()
    del data["costs"][field]
    write(tmp_path, data)
    with pytest.raises(ConfigError):
        load_backtest_settings(tmp_path)


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), True, "0"])
@pytest.mark.parametrize("field", list(payload()["costs"]))
def test_cost_inputs_fail_closed(tmp_path, field, value):
    data = payload()
    data["costs"][field] = value
    write(tmp_path, data)
    with pytest.raises(ConfigError):
        load_backtest_settings(tmp_path)


@pytest.mark.parametrize("slippage", [10_000, 10_001])
def test_slippage_domain(slippage):
    with pytest.raises(ValidationError):
        CostSettings.model_validate({**payload()["costs"], "slippage_bps": slippage})
    assert CostSettings.model_validate({**payload()["costs"], "slippage_bps": 9999.99})


@pytest.mark.parametrize("value", [0, -1, True, "100", float("inf"), float("nan")])
def test_invalid_initial_equity(value):
    with pytest.raises(ValidationError):
        BacktestSettings.model_validate({**payload(), "initial_equity": value})


def test_invalid_files_and_unknown_fields(tmp_path):
    with pytest.raises(ConfigError):
        load_backtest_settings(tmp_path)
    for text in ("[]", "costs: [", "initial_equity: 1\ninitial_equity: 2"):
        (tmp_path / "backtest.yaml").write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError):
            load_backtest_settings(tmp_path)
    for data in ({**payload(), "unknown": 1}, {**payload(), "earnings_policy": "ignore"}):
        write(tmp_path, data)
        with pytest.raises(ConfigError):
            load_backtest_settings(tmp_path)


def test_fingerprint_matches_independent_hash_and_is_order_independent():
    app = load_app_config(ROOT / "config")
    before = config_fingerprint(app)
    settings = BacktestSettings.model_validate(payload())
    encoded = json.dumps(
        {"app": app.model_dump(mode="json"), "BacktestSettings": settings.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    assert run_fingerprint(app, settings) == hashlib.sha256(encoded.encode()).hexdigest()
    extra_type = create_model("OtherSettings", value=(int, ...))
    extra = extra_type(value=2)
    assert run_fingerprint(app, settings, extra) == run_fingerprint(app, extra, settings)
    assert run_fingerprint(app, settings) != run_fingerprint(
        app, settings.model_copy(update={"initial_equity": 200_000})
    )
    assert config_fingerprint(app) == before


def test_ambiguous_duplicate_extra_class_names_rejected():
    app = load_app_config(ROOT / "config")
    first_type, second_type = create_model("Same"), create_model("Same")
    with pytest.raises(ValueError, match="duplicate"):
        run_fingerprint(app, first_type(), second_type())
    with pytest.raises(ValueError, match="duplicate"):
        run_fingerprint(app, create_model("app")())
