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
    subheader = text
    caption = text
    warning = text
    error = text

    def columns(self, spec, **kwargs):
        return [self for _ in spec]

    def html(self, markup):
        self.output.append("html:" + markup)

    def radio(self, label, options, **kwargs):
        if label == "Page":
            assert self.page in options
            return self.page
        return options[kwargs.get("index", 0)]

    def selectbox(self, label, options, index=0):
        return options[index]

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
    assert "Mode SCANNER" in text or "· SCANNER ·" in text  # Overview: inside the desk
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


# ------------------------------------------------------------------ UI follow-ups F-168..F-174


def _rich_db(tmp_path, *, interval=60, age=timedelta(seconds=45), snapshots=5):
    root = source_root(tmp_path, "synthetic")
    path = root / "state" / "semiedge.sqlite3"
    store = StateStore(path, data_source="synthetic", now=NOW - timedelta(days=10))
    store.start_run("scan-1", "post_close", TradingMode.SCANNER, "f" * 64, None, NOW)
    store.finish_run("scan-1", "ok", "done", NOW)
    store.heartbeat("engine", "ok", json.dumps({"interval_seconds": interval}), NOW - age)
    for i in range(snapshots):
        day = date(2026, 11, 9) + timedelta(days=i)
        store.record_equity_snapshot(
            EquitySnapshot(
                datetime(day.year, day.month, day.day, 21, tzinfo=UTC),
                day,
                100_000.0 + 100 * i,
                100_000.0 + 100 * (i - 1) if i else 100_000.0,
                80_000.0,
                20_000.0,
                "broker_close",
            )
        )
    store.close()
    return path


def test_overview_shows_desk_and_formatted_records(tmp_path, monkeypatch):
    _rich_db(tmp_path)
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path))
    ui = FakeUI("Overview")
    render(ui, source="synthetic", now=NOW)
    text = "\n".join(ui.output)
    [desk] = [line for line in ui.output if line.startswith("html:")]
    assert ui.output.index(SYNTHETIC_BANNER) < ui.output.index(desk)  # banner first
    assert "Total equity" in desk
    assert "$100,400" in desk  # latest equity, dollars
    assert "+400.00 since Nov 09" in desk  # change since the first snapshot
    assert "PAPER · RUNNING" in desk
    assert "Records" in text
    assert "Equity by session" in text
    assert "2026-11-13 21:00 UTC" in text  # readable timestamps in the records tables
    assert "000000+00:00" not in text


def test_stale_alert_names_component_age_and_interval(tmp_path, monkeypatch):
    _rich_db(tmp_path, interval=300, age=timedelta(minutes=22))
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path))
    ui = FakeUI("Overview")
    render(ui, source="synthetic", now=NOW)
    assert "Stale heartbeat: engine last reported 22 min ago, expected every 5 min." in ui.output


def test_stale_alert_never_echoes_markup_from_component_names():
    from semiedge.dashboard.format import stale_message

    message = stale_message({"component": "**x**<img src=y>", "age_seconds": 999.0, "detail": "{}"})
    assert "unnamed component" in message
    assert "<img" not in message
    assert "**" not in message


def test_signal_payload_becomes_columns():
    from semiedge.dashboard.format import table_rows

    payload = json.dumps(
        {
            "close": 182.3,
            "atr": 5.1,
            "proposed_stop": 172.1,
            "proposed_target": 202.7,
            "rejection_reasons": [],
            "score_components": {"s_RS": 0.8},
        }
    )
    [row] = table_rows(
        [
            {
                "run_id": "r",
                "session": "2026-11-13",
                "symbol": "NVDA",
                "eligible": 1,
                "score": 81.4,
                "rank": 1.0,
                "payload_json": payload,
            }
        ]
    )
    assert "payload_json" not in row
    assert "Payload json" not in row
    assert row["Eligible"] == "yes"
    assert row["Rank"] == "1"
    assert row["Stop"] == "172.10"
    assert row["Rejected because"] == "—"


def test_missing_values_stay_visibly_missing():
    from semiedge.dashboard.format import MISSING, cell

    assert cell("score", float("nan")) == MISSING
    assert cell("rank", None) == MISSING
    assert cell("eligible", 0) == "no"


def test_unreadable_payload_says_so():
    from semiedge.dashboard.format import table_rows

    [row] = table_rows([{"symbol": "NVDA", "payload_json": "{broken"}])
    assert row["Stop"] == "unavailable: payload unreadable"


def test_streamlit_toolbar_is_viewer_only():
    config = (
        Path(semiedge.dashboard.__file__).parents[3] / ".streamlit" / "config.toml"
    ).read_text()
    assert 'toolbarMode = "viewer"' in config


# ------------------------------------------------------------------ auto-refresh (#125)


class _AutoUI(FakeUI):
    def __init__(self, page):
        super().__init__(page)
        self.fragments = []

    def toggle(self, label, value=False):
        return True

    def fragment(self, run_every=None):
        def wrap(fn):
            self.fragments.append(run_every)
            return fn

        return wrap


@pytest.mark.parametrize(("page", "timed"), [("Overview", True), ("Operations", False)])
def test_auto_refresh_reruns_pages_but_never_the_halt_form(tmp_path, monkeypatch, page, timed):
    fixture_db(tmp_path, "cache")
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path))
    ui = _AutoUI(page)
    render(ui, source="cache", now=NOW)
    assert ui.fragments == ([60] if timed else [])
    assert any(line.startswith("Updated ") for line in ui.output)
