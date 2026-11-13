"""Offline reproductions of the backtest review's input and audit gaps (#79)."""

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from semiedge import cli
from semiedge.backtest.data import BacktestInputError, load_backtest_data
from semiedge.backtest.engine import run_backtest
from semiedge.config.loader import run_fingerprint
from semiedge.config.models import AppConfig
from semiedge.config.settings import AppSettings


def wire_cli(monkeypatch, request):
    app = AppConfig(
        universe=request.universe,
        strategy=request.params,
        risk=request.risk,
        data=request.data_settings,
    )
    monkeypatch.setattr(cli, "_load", lambda args: (AppSettings(_env_file=None), app, "test"))
    monkeypatch.setattr(cli, "load_backtest_settings", lambda path: request.settings)


@pytest.mark.parametrize("source", ["cache", "synthetic"])
@pytest.mark.parametrize(
    "window",
    [
        ("2024-01-07", "2024-01-09"),
        ("2024-01-10", "2024-01-09"),
        ("1800-01-07", "1800-01-09"),
    ],
)
def test_non_session_window_is_config_error_before_loading(
    scenario,
    monkeypatch,
    capsys,
    source,
    window,
):
    request, _, _, _ = scenario()
    wire_cli(monkeypatch, request)
    monkeypatch.setattr(cli, "load_backtest_data", lambda *a, **kw: pytest.fail("loaded cache"))
    monkeypatch.setattr(cli, "SyntheticMarketDataSource", lambda **kw: pytest.fail("built source"))
    assert (
        cli.main(
            [
                "backtest",
                "--source",
                source,
                "--start",
                window[0],
                "--end",
                window[1],
            ]
        )
        == cli.EXIT_CONFIG
    )
    assert "invalid_window" in capsys.readouterr().err


def test_existing_output_is_refused_before_simulation(scenario, monkeypatch, tmp_path, capsys):
    request, _, _, _ = scenario()
    wire_cli(monkeypatch, request)
    monkeypatch.setattr(cli, "load_backtest_data", lambda *a, **kw: pytest.fail("loaded cache"))
    output = tmp_path / "existing"
    output.mkdir()
    assert (
        cli.main(
            [
                "backtest",
                "--start",
                str(request.start),
                "--end",
                str(request.end),
                "--output",
                str(output),
            ]
        )
        == cli.EXIT_DATA
    )
    assert "output_path_exists" in capsys.readouterr().err
    assert list(output.iterdir()) == []


def test_known_data_failure_has_safe_reason(scenario, monkeypatch, capsys):
    request, _, _, _ = scenario()
    wire_cli(monkeypatch, request)

    def missing(*args, **kwargs):
        raise BacktestInputError("SMH: missing_benchmark")

    monkeypatch.setattr(cli, "load_backtest_data", missing)
    assert (
        cli.main(
            [
                "backtest",
                "--start",
                str(request.start),
                "--end",
                str(request.end),
            ]
        )
        == cli.EXIT_DATA
    )
    assert "missing_benchmark" in capsys.readouterr().err


@pytest.mark.parametrize("symbol", ["SMH", "AAA"])
def test_missing_unheld_or_regime_bars_are_recorded(scenario, symbol):
    request, data, gate, calendar = scenario([(12, 13, 11, 12, 100)] * 3)
    bars = {s: f.copy() for s, f in data.bars.items()}
    # No position is held: remove the breakout so the test covers gaps outside mark().
    bars["AAA"]["volume"] = 100.0
    bars[symbol] = bars[symbol].iloc[:-2]
    result = run_backtest(request, replace(data, bars=bars), gate=gate, calendar=calendar)
    for day in calendar.sessions_in_range(request.start, request.end)[-2:]:
        assert (day, symbol) in result.data_gaps
    assert any(label.startswith("DATA GAPS:") for label in result.labels)
    assert len(result.data_gaps) == len(set(result.data_gaps))


@pytest.mark.parametrize("symbol", ["SMH", "AAA"])
@pytest.mark.parametrize("edge", ["start", "end"])
def test_cache_loader_refuses_partial_window(scenario, symbol, edge):
    request, data, _, calendar = scenario([(12, 13, 11, 12, 100)] * 3)

    class Store:
        def read(self, name, *, feed, adjustment):
            frame = data.bars[name]
            if name == symbol:
                frame = frame.iloc[:-1] if edge == "end" else frame.loc[str(request.end) :]
            return SimpleNamespace(
                frame=frame, feed=feed, adjustment=adjustment, source=data.source
            )

    with pytest.raises(BacktestInputError, match="incomplete_window"):
        load_backtest_data(
            Store(),
            request.universe,
            request.data_settings,
            start=request.start,
            end=request.end,
            calendar=calendar,
            corporate_actions=None,
        )


def test_regime_warmup_respects_configured_missing_allowance(scenario):
    request, data, gate, calendar = scenario()
    bars = {s: f.copy() for s, f in data.bars.items()}
    regime = bars["SMH"]
    prior = pd.Timestamp(calendar.previous_session(regime.index[0].date()))
    regime.loc[prior] = regime.iloc[0]
    bars["SMH"] = regime.sort_index().drop(regime.index[2])
    allowed = replace(
        request,
        data_settings=request.data_settings.model_copy(
            update={"max_missing_sessions_in_lookback": 1},
        ),
    )
    run_backtest(allowed, replace(data, bars=bars), gate=gate, calendar=calendar)
    with pytest.raises(BacktestInputError, match="regime_warmup_unavailable"):
        run_backtest(request, replace(data, bars=bars), gate=gate, calendar=calendar)


def test_pure_api_uses_canonical_run_fingerprint(scenario):
    request, data, gate, calendar = scenario()
    app = AppConfig(
        universe=request.universe,
        strategy=request.params,
        risk=request.risk,
        data=request.data_settings,
    )
    result = run_backtest(request, data, gate=gate, calendar=calendar)
    assert result.config_fingerprint == run_fingerprint(app, request.settings)

    scanner = request.scanner_settings.model_copy(update={"reference_equity": 20000.0})
    changed = run_backtest(
        replace(request, scanner_settings=scanner),
        data,
        gate=gate,
        calendar=calendar,
    )
    assert changed.config_fingerprint == run_fingerprint(
        app.model_copy(update={"scanner": scanner}),
        request.settings,
    )
    assert changed.config_fingerprint != result.config_fingerprint


def test_error_reason_does_not_echo_untrusted_context(scenario, monkeypatch, capsys):
    request, _, _, _ = scenario()
    wire_cli(monkeypatch, request)

    def failed(*args, **kwargs):
        raise BacktestInputError("api_key=FAKE_PRIVATE_79 untrusted cache path")

    monkeypatch.setattr(cli, "load_backtest_data", failed)
    assert (
        cli.main(
            [
                "backtest",
                "--start",
                str(request.start),
                "--end",
                str(request.end),
            ]
        )
        == cli.EXIT_DATA
    )
    error = capsys.readouterr().err
    assert "invalid_backtest_input" in error
    assert "FAKE_PRIVATE_79" not in error
