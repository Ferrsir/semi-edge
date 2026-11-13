"""Load and fingerprint the YAML configuration directory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from semiedge.config.models import (
    AppConfig,
    BacktestSettings,
    DataConfig,
    MomentumV1Params,
    RiskLimits,
    ScannerConfig,
    UniverseConfig,
)

UNIVERSE_FILE = "universe.yaml"
RISK_FILE = "risk.yaml"
APP_FILE = "app.yaml"
STRATEGY_FILE = "strategies/momentum_v1.yaml"
BACKTEST_FILE = "backtest.yaml"


class ConfigError(ValueError):
    """Configuration is missing or invalid."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject ambiguous YAML instead of silently applying the last value."""


def _unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[str, Any]:
    loader.flatten_mapping(node)
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise ConfigError("configuration mapping keys must be strings")
        if key in result:
            raise ConfigError(f"duplicate configuration key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"missing configuration file: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.load(fh, Loader=_UniqueKeyLoader)  # noqa: S506  # nosec B506 - SafeLoader subclass
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return data


def load_app_config(config_dir: Path) -> AppConfig:
    """Load ``universe.yaml``, ``risk.yaml``, ``app.yaml`` and the strategy file.

    Raises :class:`ConfigError` (wrapping pydantic errors) on any problem — never
    falls back to defaults for a file that exists but is invalid.
    """
    try:
        app = _read_yaml(config_dir / APP_FILE)
        unknown = set(app) - {"data", "scanner"}
        if unknown:
            raise ConfigError(f"{APP_FILE}: unknown sections {sorted(unknown)}")
        missing = {"data", "scanner"} - set(app)
        if missing:
            raise ConfigError(f"{APP_FILE}: missing sections {sorted(missing)}")
        return AppConfig(
            universe=UniverseConfig.model_validate(_read_yaml(config_dir / UNIVERSE_FILE)),
            strategy=MomentumV1Params.model_validate(_read_yaml(config_dir / STRATEGY_FILE)),
            risk=RiskLimits.model_validate(_read_yaml(config_dir / RISK_FILE)),
            data=DataConfig.model_validate(app["data"]),
            scanner=ScannerConfig.model_validate(app["scanner"]),
        )
    except ConfigError:
        raise
    except (ValueError, yaml.YAMLError, OSError) as exc:
        raise ConfigError(str(exc)) from exc


def config_fingerprint(config: AppConfig) -> str:
    """Stable SHA-256 of the effective configuration, recorded with every run for audit."""
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_backtest_settings(config_dir: Path) -> BacktestSettings:
    """Load required backtest assumptions; missing/invalid data never selects defaults."""
    try:
        return BacktestSettings.model_validate(_read_yaml(config_dir / BACKTEST_FILE))
    except ConfigError:
        raise
    except (ValueError, yaml.YAMLError, OSError) as exc:
        raise ConfigError(str(exc)) from exc


def run_fingerprint(app: AppConfig, *extra: BaseModel) -> str:
    """Hash app and named run settings; duplicate class names are ambiguous and rejected."""
    payload = {"app": app.model_dump(mode="json")}
    for settings in extra:
        name = type(settings).__name__
        if name in payload:
            raise ValueError(f"duplicate run configuration class name: {name}")
        payload[name] = settings.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
