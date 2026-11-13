"""CLI T5 wiring and exclusive output directory contract."""

import pytest

from semiedge import cli
from semiedge.backtest.engine import run_backtest
from semiedge.config.models import AppConfig
from semiedge.config.settings import AppSettings
from semiedge.risk.manager import RiskManager


@pytest.mark.parametrize("source", ["cache", "synthetic"])
def test_cli_uses_real_risk_manager_and_labels(scenario, monkeypatch, tmp_path, capsys, source):
    request, data, _, _calendar = scenario()
    config = AppConfig(
        universe=request.universe,
        strategy=request.params,
        risk=request.risk,
        data=request.data_settings,
    )
    monkeypatch.setattr(cli, "_load", lambda args: (AppSettings(_env_file=None), config, "test"))
    monkeypatch.setattr(cli, "load_backtest_settings", lambda path: request.settings)
    monkeypatch.setattr(cli, "load_backtest_data", lambda *a, **kw: data)

    class SyntheticSource:
        feed = data.feed
        source = data.source
        adjustment = data.adjustment

        def __init__(self, *, calendar):
            pass

        def get_daily_bars(self, symbols, first, last):
            return data.bars

    monkeypatch.setattr(cli, "SyntheticMarketDataSource", SyntheticSource)
    seen = []

    def checked_run(request, data, *, gate, calendar):
        assert isinstance(gate, RiskManager)
        seen.append(gate)
        return run_backtest(request, data, gate=gate, calendar=calendar)

    monkeypatch.setattr(cli, "run_backtest", checked_run)
    output = tmp_path / "new-output"
    argv = [
        "backtest",
        "--source",
        source,
        "--start",
        str(request.start),
        "--end",
        str(request.end),
        "--output",
        str(output),
    ]
    assert cli.main(argv) == cli.EXIT_OK
    assert len(seen) == 1
    text = capsys.readouterr().out
    assert text.startswith("SYNTHETIC DATA")
    assert "PRICE RETURN: dividends excluded" in text
    artifacts = list(output.rglob("*.json"))
    assert len(artifacts) == 1
    before = artifacts[0].read_bytes()
    assert cli.main(argv) == cli.EXIT_DATA
    assert artifacts[0].read_bytes() == before


def test_cli_config_failure(config_dir, tmp_path, capsys):
    assert (
        cli.main(
            [
                "backtest",
                "--start",
                "2024-01-09",
                "--end",
                "2024-01-10",
                "--config-dir",
                str(tmp_path),
            ]
        )
        == cli.EXIT_CONFIG
    )
    assert "configuration" in capsys.readouterr().err.lower()
