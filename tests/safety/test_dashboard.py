"""Offline dashboard safety and renderer checks; no network or broker clients."""

import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

import semiedge.dashboard
from semiedge.backtest.results import BacktestResult
from semiedge.dashboard.app import render
from semiedge.dashboard.viewmodel import heartbeat_freshness, load_backtests, load_view, source_root
from semiedge.reporting import SYNTHETIC_BANNER
from semiedge.safety.modes import TradingMode
from semiedge.state.halt_control import request_manual_halt
from semiedge.state.models import EquitySnapshot, PositionRecord
from semiedge.state.readonly import ReadOnlyStateStore
from semiedge.state.store import StateStore

pytestmark = pytest.mark.safety
NOW = datetime(2026, 11, 13, 20, tzinfo=UTC)
PAGES = ["Overview", "Signals", "Positions", "Trade history", "Operations"]


def fixture_db(tmp_path, source):
    root = source_root(tmp_path, source)
    path = root / "state" / "semiedge.sqlite3"
    store = StateStore(path, data_source="real" if source == "cache" else "synthetic", now=NOW)
    store.start_run("fixture-run", "post_close", TradingMode.SCANNER, "f" * 64, None, NOW)
    store.heartbeat("engine", "ok", "<script>alert(1)</script>", NOW - timedelta(seconds=20))
    store.finish_run("fixture-run", "failed", "fixture failure", NOW)
    store.close()
    return path


class FakeUI:
    def __init__(self, page):
        self.page = page
        self.output = []
        self.sidebar = self

    def set_page_config(self, **kwargs):
        pass

    def text(self, value):
        self.output.append(str(value))

    title = text
    header = text

    def radio(self, label, options):
        assert self.page in options
        return self.page

    def button(self, label):
        return False

    def dataframe(self, frame, **kwargs):
        self.output.append(frame.to_string())

    def form(self, label):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def text_input(self, label):
        return {"Actor": "tester", "Reason": "fixture halt", "Type HALT to confirm": "HALT"}[label]

    def form_submit_button(self, label):
        return True


@pytest.mark.parametrize("source", ["cache", "synthetic"])
@pytest.mark.parametrize("page", PAGES)
def test_pages_and_single_halt(tmp_path, monkeypatch, source, page):
    path = fixture_db(tmp_path, source)
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path))
    ui = FakeUI(page)
    render(ui, source=source, now=NOW)
    render(ui, source=source, now=NOW)
    text = "\n".join(ui.output)
    assert "Mode: SCANNER" in text
    assert (SYNTHETIC_BANNER in text) == (source == "synthetic")
    reader = ReadOnlyStateStore(path)
    assert len(reader.active_halts()) == (1 if page == "Operations" else 0)
    reader.close()


def test_missing_database_is_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path))
    ui = FakeUI("Overview")
    render(ui)
    assert "Dashboard unavailable" in ui.output
    assert not (tmp_path / "state").exists()


def test_source_mismatch_and_clock(tmp_path):
    path = fixture_db(tmp_path, "cache")
    with pytest.raises(ValueError, match="source"):
        load_view(path, source="synthetic", now=NOW)
    with pytest.raises(ValueError, match="timezone"):
        load_view(path, source="cache", now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="future"):
        load_view(path, source="cache", now=NOW - timedelta(days=1))


def test_view_reads_actual_rows(tmp_path):
    path = fixture_db(tmp_path, "cache")
    request_manual_halt(path, "fixture", "tester", NOW)
    view = load_view(path, source="cache", now=NOW)
    assert view.sections["Overview"]["equity"] == []
    assert view.sections["Overview"]["heartbeats"][0]["age_seconds"] == 20
    assert view.sections["Operations"]["failed_runs"][0]["detail"] == "fixture failure"


def test_static_dashboard_surface():
    directory = Path(semiedge.dashboard.__file__).parent
    text = "\n".join(p.read_text(encoding="utf-8") for p in directory.glob("*.py"))
    assert "clear_halt" not in text
    assert "unsafe_allow_html" not in text
    assert "import streamlit" not in text


@pytest.mark.parametrize(("age", "expected"), [(30, "fresh"), (30.01, "stale")])
def test_heartbeat_threshold(age, expected):
    assert heartbeat_freshness('{"interval_seconds": 10}', age) == expected


@pytest.mark.parametrize("interval", [0, -1, True, "ten", float("inf")])
def test_invalid_interval(interval):
    with pytest.raises(ValueError, match="interval"):
        heartbeat_freshness(json.dumps({"interval_seconds": interval}), 20)


@pytest.mark.parametrize("bad", ["labels", "command", "source", "finite", "schema"])
def test_report_rejected(tmp_path, bad):
    directory = tmp_path / "reports" / "backtests"
    directory.mkdir(parents=True)
    raw = artifact().to_dict()
    if bad == "source":
        raw["labels"] = [SYNTHETIC_BANNER]
    elif bad == "finite":
        raw["equity_curve"] = [float("inf")]
    elif bad == "schema":
        raw["schema_version"] = 99
    else:
        del raw[bad]
    (directory / "fixture.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match=r"backtest|non-finite"):
        load_backtests(tmp_path, source="cache")


def artifact():
    return BacktestResult(
        schema_version=1,
        run_id="20261113T200000Z-0123abcd",
        command="fixture-only command",
        git_sha=None,
        semiedge_version="fixture",
        config_fingerprint="f" * 64,
        data_fingerprint="d" * 64,
        start=NOW.date(),
        end=NOW.date(),
        labels=(
            "SURVIVORSHIP-BIASED UNIVERSE: current hand-picked list, not point-in-time",
            "PRICE RETURN: dividends excluded",
        ),
        trades=(),
        cancelled_entries=(),
        equity_curve=(),
        risk_decisions=(),
        halts=(),
        data_gaps=(),
        cap_overshoots=(),
    )


def test_saved_report_roundtrip(tmp_path):
    result = artifact()
    result.save(tmp_path)
    assert load_backtests(tmp_path, source="cache") == [result.to_dict()]


@pytest.mark.skipif(
    importlib.util.find_spec("streamlit") is None, reason="dashboard extra not cached"
)
@pytest.mark.parametrize("source", ["cache", "synthetic"])
@pytest.mark.parametrize("page", PAGES)
def test_streamlit_pages(tmp_path, monkeypatch, source, page):
    from streamlit.testing.v1 import AppTest

    fixture_db(tmp_path, source)
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path))
    app = AppTest.from_string(
        f"from semiedge.dashboard.app import render\nimport streamlit as st\n"
        f"from datetime import datetime\nrender(st, source={source!r}, "
        f"now=datetime.fromisoformat({NOW.isoformat()!r}))"
    ).run()
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception


@pytest.mark.parametrize(
    ("session", "expected"),
    [(date(2026, 11, 13), 1), (date(2026, 11, 16), 2), (date(2026, 11, 27), 10)],
)
def test_held_sessions_includes_entry_and_skips_non_sessions(harness, session, expected):
    entry_id = harness.stage_entry()
    harness.store.open_position(
        PositionRecord(
            symbol="NVDA",
            qty=100,
            avg_entry_price=100.0,
            entry_session=NOW.date(),
            entry_client_order_id=entry_id,
            entry_reference=100.0,
            atr=2.0,
            stop=96.0,
            target=108.0,
            split_ratio=1.0,
            status="open",
            opened_at=NOW,
            closed_at=None,
            updated_at=NOW,
        )
    )
    as_of = datetime.combine(session, NOW.timetz())
    harness.store.record_equity_snapshot(
        EquitySnapshot(as_of, session, 100_000.0, 100_000.0, 90_000.0, 10_000.0, "broker_close")
    )
    harness.store.close()
    view = load_view(harness.path, source="cache", now=as_of)
    [position] = view.sections["Positions"]
    assert position["held_sessions"] == expected
    assert position["held_sessions_as_of"] == session
