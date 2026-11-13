"""Earnings settings, API-key settings and the ``semiedge earnings`` CLI (#20; spec §2, §5, §6).

Offline: sockets are disabled, and the only refresh exercised through the CLI has no keys.
"""

from __future__ import annotations

import logging
import shutil
import socket
from pathlib import Path

import pytest
import yaml

from semiedge import cli
from semiedge.config.loader import ConfigError, load_earnings_settings
from semiedge.config.settings import AppSettings

FH_KEY = "q" * 31  # low-entropy placeholder, never a real key
AV_KEY = "z" * 31  # low-entropy placeholder, never a real key


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("network access in an offline test")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_repository_earnings_config_loads(config_dir: Path) -> None:
    settings = load_earnings_settings(config_dir)
    assert settings.override_file == "earnings_overrides.yaml"
    assert settings.min_forward_coverage_days < settings.lookahead_days


def _write(tmp_path: Path, config_dir: Path, **changes: object) -> Path:
    data = yaml.safe_load((config_dir / "earnings.yaml").read_text(encoding="utf-8"))
    data.update(changes)
    for key in [k for k, v in changes.items() if v is None]:
        del data[key]
    (tmp_path / "earnings.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "changes",
    [
        {"lookahead_days": 90},  # beyond Alpha Vantage's guaranteed 89-day horizon
        {"lookahead_days": "60"},
        {"min_forward_coverage_days": 60},  # not below lookahead_days
        {"max_age_hours": 49},
        {"max_age_hours": True},
        {"max_attempts_per_day": 6},
        {"finnhub_min_interval_seconds": 0.5},  # faster than the free tier allows
        {"override_file": "../secrets.yaml"},
        {"override_max_age_days": 91},
        {"unexpected": 1},
        {"retry_interval_minutes": None},  # missing field
    ],
)
def test_invalid_earnings_config_rejected(
    config_dir: Path, tmp_path: Path, changes: dict[str, object]
) -> None:
    with pytest.raises(ConfigError):
        load_earnings_settings(_write(tmp_path, config_dir, **changes))


def test_missing_earnings_config_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_earnings_settings(tmp_path)


def test_api_keys_are_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINNHUB_API_KEY", FH_KEY)
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", AV_KEY)
    settings = AppSettings()
    assert {FH_KEY, AV_KEY} <= set(settings.secret_values())
    assert FH_KEY not in repr(settings)
    assert AV_KEY not in repr(settings)


# ------------------------------------------------------------------ CLI


@pytest.fixture
def cli_env(config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    conf = tmp_path / "config"
    shutil.copytree(config_dir, conf)
    monkeypatch.setenv("SEMIEDGE_CONFIG_DIR", str(conf))
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path / "data"))
    return conf


def test_refresh_without_keys_makes_no_request_and_exits_data(
    cli_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["earnings", "refresh"]) == cli.EXIT_DATA
    out = capsys.readouterr().out
    assert out.count("missing_api_key") >= 2


def test_status_without_cache_blocks_every_symbol(
    cli_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["earnings", "status", "--as-of", "2026-11-13"]) == cli.EXIT_DATA
    out = capsys.readouterr().out
    assert "finnhub       missing" in out
    assert "VERIFIED" not in out
    assert "reason=finnhub_missing+alphavantage_missing" in out


def test_status_with_invalid_override_file_fails_closed(
    cli_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (cli_env / "earnings_overrides.yaml").write_text("overrides: {}\n", encoding="utf-8")
    assert cli.main(["earnings", "status"]) == cli.EXIT_CONFIG
    assert "all entries blocked" in capsys.readouterr().err


def test_cli_never_prints_keys(
    cli_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("FINNHUB_API_KEY", FH_KEY)
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", AV_KEY)
    assert cli.main(["earnings", "status"]) == cli.EXIT_DATA
    captured = capsys.readouterr()
    for text in (captured.out, captured.err, caplog.text):
        assert FH_KEY not in text
        assert AV_KEY not in text
