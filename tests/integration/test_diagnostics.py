from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from semiedge.cli import main

pytestmark = pytest.mark.integration


def test_diagnostics_is_offline_and_redacts_credentials(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbid_network(*args: object, **kwargs: object) -> None:
        pytest.fail("offline diagnostics attempted network access")

    monkeypatch.setattr(socket, "socket", forbid_network)
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "PK-diagnostic-secret-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "diagnostic-secret-value")
    assert main(["diagnostics", "--config-dir", str(config_dir)]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert "diagnostic-secret" not in captured.out + captured.err
    assert report["offline"] is True
    assert report["execution_available"] is False
    assert report["orders_submitted"] == 0
    assert report["safety"]["configured_order_permission"] is False
    assert "SMH" in report["universe"]["benchmark_symbols"]
    assert report["strategy"]["required_completed_sessions"] > 50


def test_enabled_paper_settings_do_not_enable_execution(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ENABLE_ORDER_SUBMISSION", "true")
    assert main(["diagnostics", "--config-dir", str(config_dir)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["safety"]["configured_order_permission"] is True
    assert report["execution_available"] is False
    assert report["orders_submitted"] == 0


@pytest.mark.parametrize("mode", ["live", "accidental-secret-value"])
def test_invalid_settings_fail_without_echoing_inputs(
    mode: str,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    assert main(["diagnostics", "--config-dir", str(config_dir)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert mode not in captured.err


def test_missing_config_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diagnostics", "--config-dir", str(tmp_path)]) == 2
    assert capsys.readouterr().out == ""


def test_documented_module_command(config_dir: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(config_dir.parent / "src")
    result = subprocess.run(
        [sys.executable, "-m", "semiedge.cli", "diagnostics", "--config-dir", str(config_dir)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "configuration_valid"


def test_html_dashboard_escapes_config_and_states_limitations(
    config_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    copied = tmp_path / "config"
    shutil.copytree(config_dir, copied)
    strategy = copied / "strategies/momentum_v1.yaml"
    strategy.write_text(
        strategy.read_text(encoding="utf-8").replace(
            'version: "1.0.0"', 'version: "<script>alert(1)</script>"'
        ),
        encoding="utf-8",
    )
    output = tmp_path / "dashboard.html"
    assert main(["diagnostics", "--config-dir", str(copied), "--html", str(output)]) == 0
    html = output.read_text(encoding="utf-8")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>" not in html
    assert "Trading engine unavailable" in html
    assert "No market data has been fetched" in html
    assert "cannot submit orders" in html
    assert "NVDA" in html
    assert json.loads(capsys.readouterr().out)["execution_available"] is False


def test_html_output_does_not_overwrite_existing_file(config_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "existing.html"
    output.write_text("preserve", encoding="utf-8")
    assert main(["diagnostics", "--config-dir", str(config_dir), "--html", str(output)]) == 2
    assert output.read_text(encoding="utf-8") == "preserve"
