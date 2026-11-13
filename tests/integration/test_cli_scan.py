"""End-to-end ``semiedge`` CLI: scan, data fetch/status, config check, version (issue #14).

Offline: sockets are disabled, the clock is pinned and market data comes from the synthetic
generator. Bars written to a temporary cache are labelled ``fixture``, never ``alpaca``,
except where a test needs a second market source to prove sources are never mixed.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from semiedge import cli
from semiedge.config import load_app_config
from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import Adjustment, DataFeed, DataSource
from semiedge.data.store import ParquetBarStore
from semiedge.data.synthetic import SyntheticMarketDataSource
from semiedge.reporting import SYNTHETIC_BANNER

pytestmark = pytest.mark.integration

AS_OF = date(2024, 6, 28)  # a Friday session
NOW = datetime(2024, 6, 29, 1, 0, tzinfo=UTC)  # after the 2024-06-28 close + settle time


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    def forbid(*args: object, **kwargs: object) -> None:
        pytest.fail("the CLI attempted network access")

    monkeypatch.setattr(socket, "socket", forbid)
    monkeypatch.setattr(cli, "utcnow", lambda: NOW)
    monkeypatch.setattr(cli, "git_sha", lambda: "abc123def456")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(data_dir))
    return data_dir


@pytest.fixture
def data_dir(_offline: Path) -> Path:
    return _offline


def _run(args: Sequence[str], config_dir: Path) -> int:
    return cli.main([*args, "--config-dir", str(config_dir)])


def _symbols(config_dir: Path) -> tuple[str, ...]:
    config = load_app_config(config_dir)
    return (*config.universe.tradable_symbols, config.universe.regime_benchmark)


def _bars(symbols: Sequence[str], end: date = AS_OF) -> dict[str, pd.DataFrame]:
    return SyntheticMarketDataSource().get_daily_bars(list(symbols), date(2023, 6, 1), end)


def _fill_cache(
    data_dir: Path, symbols: Sequence[str], source: DataSource = DataSource.FIXTURE
) -> None:
    store = ParquetBarStore(data_dir)
    for symbol, frame in _bars(symbols).items():
        store.write(
            symbol, frame, feed=DataFeed.SIP, adjustment=Adjustment.SPLIT, source=source,
            fetched_at=NOW,
        )  # fmt: skip


def _saved(data_dir: Path, *parts: str) -> list[Path]:
    return sorted(data_dir.joinpath(*parts, "reports", "signals").glob("*.json"))


# ------------------------------------------------------------------ scan


def test_synthetic_scan_is_labelled_and_persisted_apart(
    config_dir: Path, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(["scan", "--source", "synthetic"], config_dir) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"*** {SYNTHETIC_BANNER} ***")
    assert out.rstrip().endswith(f"*** {SYNTHETIC_BANNER} ***")
    [saved] = _saved(data_dir, "synthetic")
    assert saved.name.startswith("2024-06-28_20240629T010000Z-")
    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["synthetic"] is True
    assert payload["banner"] == SYNTHETIC_BANNER
    assert {s["source"] for s in payload["data"]["series"]} == {"synthetic"}
    assert payload["run"]["git_sha"] == "abc123def456"
    assert not (data_dir / "reports").exists()  # never next to market-data reports
    assert not (data_dir / "raw").exists()  # synthetic bars are never cached


def test_cache_scan_reports_every_symbol_with_provenance(
    config_dir: Path, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fill_cache(data_dir, _symbols(config_dir))
    assert _run(["scan", "--format", "json", "--equity", "25000"], config_dir) == 0
    stdout = json.loads(capsys.readouterr().out)
    [saved] = _saved(data_dir)
    assert json.loads(saved.read_text(encoding="utf-8")) == stdout
    assert stdout["synthetic"] is False
    assert stdout["data"]["source"] == "cache"
    assert stdout["data"]["regime_actionable"] is True
    assert {s["source"] for s in stdout["data"]["series"]} == {"fixture"}
    assert {s["fetched_at"] for s in stdout["data"]["series"]} == {NOW.isoformat()}
    assert stdout["equity"] == {"used": 25_000.0, "source": "cli"}
    tradable = load_app_config(config_dir).universe.tradable_symbols
    assert sorted(e["symbol"] for e in stdout["evaluations"]) == sorted(tradable)
    for evaluation in stdout["evaluations"]:
        assert len(evaluation["conditions"]) == 6
        assert evaluation["eligible"] == (evaluation["rejection_reasons"] == [])
        assert (evaluation["proposed_size"] is not None) == evaluation["eligible"]
    assert not (data_dir / "synthetic").exists()


def test_scan_as_of_uses_only_bars_up_to_that_session(
    config_dir: Path, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fill_cache(data_dir, _symbols(config_dir))
    assert _run(["scan", "--as-of", "2024-06-27", "--format", "json"], config_dir) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["as_of"] == "2024-06-27"
    # Bars after --as-of exist in the cache, so the data is fresh and the regime actionable.
    assert payload["data"]["regime_actionable"] is True
    assert {e["session"] for e in payload["evaluations"]} == {"2024-06-27"}


def test_empty_cache_fails_closed_but_still_explains(
    config_dir: Path, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(["scan"], config_dir) == cli.EXIT_DATA
    captured = capsys.readouterr()
    assert "NOT ACTIONABLE" in captured.out
    assert "no symbol is actionable" in captured.err
    [saved] = _saved(data_dir)
    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["data"]["regime_actionable"] is False
    assert payload["summary"]["eligible"] == 0


def test_stale_regime_benchmark_fails_closed(
    config_dir: Path, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    symbols = _symbols(config_dir)
    _fill_cache(data_dir, [s for s in symbols if s != "SMH"])
    stale = _bars(["SMH"], end=date(2024, 6, 21))["SMH"]
    ParquetBarStore(data_dir).write(
        "SMH", stale, feed=DataFeed.SIP, adjustment=Adjustment.SPLIT, source=DataSource.FIXTURE,
        fetched_at=NOW,
    )  # fmt: skip
    assert _run(["scan", "--format", "json"], config_dir) == cli.EXIT_DATA
    payload = json.loads(capsys.readouterr().out)
    smh = next(q for q in payload["data_quality"] if q["symbol"] == "SMH")
    assert smh["is_fresh"] is False
    assert payload["summary"]["eligible"] == 0


def test_mixed_market_sources_are_refused(
    config_dir: Path, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    symbols = _symbols(config_dir)
    _fill_cache(data_dir, symbols[:-1])
    # Generated bars labelled "alpaca" only to prove that two market sources never mix.
    _fill_cache(data_dir, symbols[-1:], source=DataSource.ALPACA)
    assert _run(["scan"], config_dir) == cli.EXIT_DATA
    assert "more than one data source" in capsys.readouterr().err
    assert _saved(data_dir) == []


def test_tampered_cache_file_fails_closed(
    config_dir: Path, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fill_cache(data_dir, _symbols(config_dir))
    path = next((data_dir / "raw").rglob("NVDA.parquet"))
    path.write_bytes(b"not parquet")
    assert _run(["scan"], config_dir) == cli.EXIT_DATA
    assert "NVDA failed validation" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("as_of", "message"),
    [
        ("2024-06-22", "exchange session"),  # Saturday
        ("2024-07-01", "after the last completed session"),  # future relative to NOW
        ("2099-12-31", "after the last completed session"),
        ("1800-01-02", "exchange session"),  # outside the calendar
    ],
)
def test_scan_as_of_must_be_a_completed_session(
    config_dir: Path, capsys: pytest.CaptureFixture[str], as_of: str, message: str
) -> None:
    assert _run(["scan", "--source", "synthetic", "--as-of", as_of], config_dir) == 2
    assert message in capsys.readouterr().err


def test_in_progress_session_is_not_completed(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "utcnow", lambda: datetime(2024, 6, 28, 18, 0, tzinfo=UTC))
    assert _run(["scan", "--source", "synthetic", "--as-of", "2024-06-28"], config_dir) == 2
    assert "2024-06-27" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["20240628", "2024-W26-5", "2024-6-28", "yesterday"])
def test_scan_rejects_non_iso_dates(config_dir: Path, bad: str) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(["scan", "--as-of", bad], config_dir)
    assert exc.value.code == 2


@pytest.mark.parametrize("bad", ["0", "-5", "nan", "inf", "abc"])
def test_scan_rejects_invalid_equity(config_dir: Path, bad: str) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(["scan", "--equity", bad], config_dir)
    assert exc.value.code == 2


@pytest.mark.parametrize("fmt", ["table", "markdown", "json"])
def test_output_file_is_created_exclusively(
    config_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], fmt: str
) -> None:
    output = tmp_path / f"report.{fmt}"
    assert _run(["scan", "--source", "synthetic", "--format", fmt, "--output", str(output)],
                config_dir) == 0  # fmt: skip
    assert output.read_text(encoding="utf-8") == capsys.readouterr().out
    assert _run(["scan", "--source", "synthetic", "--output", str(output)], config_dir) == 2
    assert "Cannot create the output file" in capsys.readouterr().err


def test_invalid_configuration_exits_2_without_echoing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRADING_MODE", "accidental-secret-value")
    assert cli.main(["scan", "--source", "synthetic"]) == 2
    captured = capsys.readouterr()
    assert "accidental-secret-value" not in captured.out + captured.err


# ------------------------------------------------------------------ data fetch / status


class FakeMarketData:
    """Stands in for the Alpaca source: same contract, generated bars, records requests."""

    source = DataSource.FIXTURE
    feed = DataFeed.SIP
    adjustment = Adjustment.SPLIT

    def __init__(self, extra_session: bool = False) -> None:
        self.requests: list[tuple[list[str], date, date]] = []
        self.extra_session = extra_session

    def get_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]:
        self.requests.append((list(symbols), start, end))
        last = TradingCalendar().next_session(end) if self.extra_session else end
        return SyntheticMarketDataSource().get_daily_bars(list(symbols), start, last)


def test_fetch_then_status_then_scan(
    config_dir: Path,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeMarketData()
    monkeypatch.setattr(cli, "market_data_source", lambda settings, config: fake)
    assert _run(["data", "fetch", "--start", "2023-06-01"], config_dir) == 0
    fetched = json.loads(capsys.readouterr().out)
    [(symbols, start, end)] = fake.requests
    assert set(symbols) == set(load_app_config(config_dir).universe.all_symbols)
    assert (start, end) == (date(2023, 6, 1), AS_OF)  # defaults to the last completed session
    assert fetched["symbols"]["NVDA"]["last_session"] == "2024-06-28"

    assert _run(["data", "status"], config_dir) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["last_completed_session"] == "2024-06-28"
    assert {s["symbol"] for s in status["series"]} == set(symbols)
    assert all(s["fresh"] and s["source"] == "fixture" for s in status["series"])

    assert _run(["scan", "--format", "json"], config_dir) == 0
    assert json.loads(capsys.readouterr().out)["data"]["regime_actionable"] is True


def test_fetch_refuses_bars_after_the_requested_end(
    config_dir: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeMarketData(extra_session=True)
    monkeypatch.setattr(cli, "market_data_source", lambda settings, config: fake)
    args = ["data", "fetch", "--symbols", "NVDA", "--start", "2024-01-02", "--end", "2024-06-27"]
    assert _run(args, config_dir) == cli.EXIT_DATA
    assert not (data_dir / "raw").exists()


def test_fetch_requires_credentials(config_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(["data", "fetch"], config_dir) == cli.EXIT_CREDENTIALS
    assert "ALPACA_PAPER_API_KEY" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--symbols", "NVDA", "ZZZZ"], "not in the configured universe"),
        (["--end", "2024-07-01"], "after the last completed session"),
        (["--start", "2024-06-28", "--end", "2024-06-27"], "--start must not be after --end"),
    ],
)
def test_fetch_validates_arguments_before_any_request(
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    message: str,
) -> None:
    def no_source(settings: object, config: object) -> None:
        pytest.fail("a market data source was created before arguments were validated")

    monkeypatch.setattr(cli, "market_data_source", no_source)
    assert _run(["data", "fetch", *args], config_dir) == 2
    assert message in capsys.readouterr().err


def test_status_of_empty_cache(config_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(["data", "status"], config_dir) == 0
    assert json.loads(capsys.readouterr().out)["series"] == []


# ------------------------------------------------------------------ config check / version


def test_config_check_masks_credentials(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "PK-config-check-secret")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "config-check-secret-value")
    assert _run(["config", "check"], config_dir) == 0
    captured = capsys.readouterr()
    assert "config-check-secret" not in captured.out + captured.err
    report = json.loads(captured.out)
    assert report["alpaca_paper_credentials_configured"] is True
    assert report["safety"]["live_trading_allowed"] is False
    assert len(report["configuration_fingerprint"]) == 64


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["version"]) == 0
    assert capsys.readouterr().out.startswith("semiedge ")
