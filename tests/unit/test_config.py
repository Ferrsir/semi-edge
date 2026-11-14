from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from semiedge.config import (
    REQUIRED_ALPACA_ENV_VARS,
    AppSettings,
    ConfigError,
    MissingCredentialsError,
    MomentumV1Params,
    RiskLimits,
    ScoreWeights,
    UniverseConfig,
    config_fingerprint,
    load_app_config,
)


def _copy_config(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst / "config")
    return dst / "config"


def test_repository_config_loads(config_dir: Path) -> None:
    cfg = load_app_config(config_dir)
    assert "NVDA" in cfg.universe.tradable_symbols
    assert set(cfg.universe.benchmark_symbols) == {"SMH", "SOXX", "QQQ", "SPY"}
    assert cfg.universe.regime_benchmark == "SMH"
    assert len(cfg.universe.tradable_symbols) == 16
    assert cfg.strategy.min_excess_return == pytest.approx(0.02)
    assert cfg.risk.max_risk_per_trade_pct == pytest.approx(0.0025)


def test_fingerprint_is_stable_and_sensitive(config_dir: Path, tmp_path: Path) -> None:
    a = config_fingerprint(load_app_config(config_dir))
    assert a == config_fingerprint(load_app_config(config_dir))
    cfg_dir = _copy_config(config_dir, tmp_path)
    path = cfg_dir / "strategies" / "momentum_v1.yaml"
    data = yaml.safe_load(path.read_text())
    data["min_relative_volume"] = 1.3
    path.write_text(yaml.safe_dump(data))
    assert config_fingerprint(load_app_config(cfg_dir)) != a


def test_unknown_key_is_rejected(config_dir: Path, tmp_path: Path) -> None:
    cfg_dir = _copy_config(config_dir, tmp_path)
    path = cfg_dir / "risk.yaml"
    data = yaml.safe_load(path.read_text())
    data["max_risk_per_trde_pct"] = 0.001  # typo must not be silently ignored
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="max_risk_per_trde_pct"):
        load_app_config(cfg_dir)


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing configuration file"):
        load_app_config(tmp_path)


def test_unknown_app_section_rejected(config_dir: Path, tmp_path: Path) -> None:
    cfg_dir = _copy_config(config_dir, tmp_path)
    (cfg_dir / "app.yaml").write_text("data: {}\nexecution: {}\n")
    with pytest.raises(ConfigError, match="unknown sections"):
        load_app_config(cfg_dir)


def test_universe_rejects_duplicates_and_missing_regime() -> None:
    member = {"symbol": "NVDA", "group": "x", "role": "tradable"}
    bench = {"symbol": "SMH", "group": "b", "role": "benchmark"}
    with pytest.raises(ValueError, match="duplicate"):
        UniverseConfig.model_validate(
            {"members": [member, member, bench], "comparison_benchmarks": ["SMH"]}
        )
    with pytest.raises(ValueError, match="regime_benchmark"):
        UniverseConfig.model_validate({"members": [member], "comparison_benchmarks": []})


def test_score_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match=r"sum to 1\.0"):
        ScoreWeights(
            relative_strength=0.5, breakout=0.3, relative_volume=0.2, trend_persistence=0.1
        )


def test_required_history_covers_all_windows() -> None:
    p = MomentumV1Params()
    # trend persistence needs 20 defined SMA(50) values -> 69 sessions
    assert p.required_history() == 69


def test_strategy_param_cross_validation() -> None:
    with pytest.raises(ValueError, match="fast_sma_window"):
        MomentumV1Params(fast_sma_window=60)
    with pytest.raises(ValueError, match="relative_volume_saturation"):
        MomentumV1Params(min_relative_volume=3.5)


@pytest.mark.safety
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_risk_per_trade_pct", 0.011),  # ceilings raised for the $5k profile (#128)
        ("max_position_pct", 0.26),
        ("max_gross_exposure_pct", 0.76),
        ("max_positions", 5),
        ("daily_loss_halt_pct", 0.02),
        ("drawdown_halt_pct", 0.10),
    ],
)
def test_risk_limits_cannot_exceed_ceilings(field: str, value: float) -> None:
    with pytest.raises(ValueError, match="ceiling"):
        RiskLimits.model_validate({field: value})


@pytest.mark.safety
def test_risk_limits_can_be_tightened() -> None:
    limits = RiskLimits(max_risk_per_trade_pct=0.001, max_positions=2)
    assert limits.max_positions == 2


@pytest.mark.safety
@pytest.mark.parametrize(
    "flag", ["allow_leverage", "allow_short_selling", "allow_options", "allow_extended_hours"]
)
def test_prohibited_capabilities_cannot_be_enabled(flag: str) -> None:
    with pytest.raises(ValueError, match=flag):
        RiskLimits.model_validate({flag: True})


def test_missing_credentials_message_lists_env_vars() -> None:
    settings = AppSettings()
    assert not settings.has_alpaca_credentials
    with pytest.raises(MissingCredentialsError) as exc:
        settings.alpaca_credentials()
    for var in REQUIRED_ALPACA_ENV_VARS:
        assert var in str(exc.value)


def test_credentials_and_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "PKABC")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "s3cr3t-value")
    settings = AppSettings()
    assert settings.alpaca_credentials() == ("PKABC", "s3cr3t-value")
    assert "s3cr3t-value" in settings.secret_values()
    assert "s3cr3t-value" not in repr(settings)


def test_empty_credentials_count_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "")
    assert not AppSettings().has_alpaca_credentials


def test_dotenv_file_is_read(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TRADING_MODE=backtest\n")
    assert AppSettings().trading_mode == "backtest"


@pytest.mark.safety
@pytest.mark.parametrize("text", ["{}", "data: {}", "scanner: {}"])
def test_missing_app_sections_fail_closed(config_dir: Path, tmp_path: Path, text: str) -> None:
    cfg_dir = _copy_config(config_dir, tmp_path)
    (cfg_dir / "app.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="missing sections"):
        load_app_config(cfg_dir)


# ------------------------------------------------------------------ #128 $5k profile


def test_default_limits_did_not_loosen_when_the_ceilings_rose() -> None:
    from semiedge.config.models import RiskLimits

    r = RiskLimits()
    assert (r.max_risk_per_trade_pct, r.max_position_pct, r.max_gross_exposure_pct) == (
        0.0025,
        0.05,
        0.20,
    )
    assert (r.max_positions, r.daily_loss_halt_pct, r.drawdown_halt_pct) == (4, 0.01, 0.05)


def test_repository_risk_files(config_dir: Path) -> None:
    from semiedge.config.loader import load_risk_profile

    default = load_risk_profile(config_dir, None)
    assert default.max_risk_per_trade_pct == 0.0025  # config/risk.yaml unchanged
    small = load_risk_profile(config_dir, "5k")
    assert (small.max_risk_per_trade_pct, small.max_position_pct) == (0.01, 0.25)
    assert (small.max_gross_exposure_pct, small.max_positions) == (0.75, 3)
    assert (small.daily_loss_halt_pct, small.drawdown_halt_pct) == (0.01, 0.05)


@pytest.mark.parametrize("name", ["../risk", "5K", "", "a" * 40, "risk.yaml"])
def test_malformed_profile_names_are_refused(name: str, config_dir: Path) -> None:
    from semiedge.config.loader import ConfigError, load_risk_profile

    with pytest.raises(ConfigError):
        load_risk_profile(config_dir, name)


def test_unknown_profile_and_over_ceiling_profile_are_refused(
    tmp_path: Path, config_dir: Path
) -> None:
    from semiedge.config.loader import ConfigError, load_risk_profile

    with pytest.raises(ConfigError, match="does not exist"):
        load_risk_profile(config_dir, "nope")
    (tmp_path / "risk_big.yaml").write_text("max_risk_per_trade_pct: 0.02\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="ceiling"):
        load_risk_profile(tmp_path, "big")
