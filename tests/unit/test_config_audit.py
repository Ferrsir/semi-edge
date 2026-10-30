"""Regression cases for configuration ambiguity and non-finite financial inputs."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from semiedge.config.loader import ConfigError, _read_yaml
from semiedge.config.models import MomentumV1Params, RiskLimits, ScannerConfig
from semiedge.data.schema import Adjustment, DataFeed


@pytest.mark.safety
@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
@pytest.mark.parametrize("field", ["min_excess_return", "stop_atr_multiple", "target_atr_multiple"])
def test_strategy_rejects_nonfinite_values(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        MomentumV1Params.model_validate({field: value})


@pytest.mark.safety
@pytest.mark.parametrize("value", [float("inf"), float("nan"), True, "100000"])
def test_scanner_equity_requires_finite_numeric_value(value: Any) -> None:
    with pytest.raises(ValidationError):
        ScannerConfig.model_validate({"reference_equity": value})


@pytest.mark.safety
@pytest.mark.parametrize("value", [True, 2.0, "2"])
def test_position_count_requires_integer(value: Any) -> None:
    with pytest.raises(ValidationError):
        RiskLimits.model_validate({"max_positions": value})


@pytest.mark.safety
@pytest.mark.parametrize("value", [0, "false"])
def test_risk_boolean_cannot_be_coerced(value: Any) -> None:
    with pytest.raises(ValidationError):
        RiskLimits.model_validate({"allow_leverage": value})


@pytest.mark.parametrize(
    "text",
    [
        "max_position_pct: 0.01\nmax_position_pct: 0.05\n",
        "data: {daily_feed: sip, daily_feed: iex}",
    ],
)
def test_duplicate_yaml_values_are_rejected(tmp_path: Path, text: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate configuration key"):
        _read_yaml(path)


@pytest.mark.parametrize("text", ["", "null", "[]", "{1: value}"])
def test_empty_or_nonstring_key_configuration_is_invalid(tmp_path: Path, text: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        _read_yaml(path)


def test_minimal_data_contracts_are_importable() -> None:
    assert DataFeed("sip") is DataFeed.SIP
    assert Adjustment("split") is Adjustment.SPLIT
