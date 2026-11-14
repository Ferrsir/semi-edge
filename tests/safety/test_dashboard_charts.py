"""Charts page (#116): daily candles from the price cache, with no state database needed."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from semiedge.dashboard.app import render
from semiedge.dashboard.charts import cached_symbols, candle_rows, candle_spec, summary
from semiedge.data.schema import Adjustment, DataFeed, DataSource
from semiedge.data.store import ParquetBarStore

pytestmark = pytest.mark.safety


def _frame(n=80, start=100.0):
    idx = pd.bdate_range("2026-07-01", periods=n)
    close = [start + i for i in range(n)]
    return pd.DataFrame(
        {
            "open": [c - 0.5 if i % 2 else c + 0.5 for i, c in enumerate(close)],
            "high": [c + 2 for c in close],
            "low": [c - 2 for c in close],
            "close": close,
            "volume": [1_000_000.0 + i for i in range(n)],
        },
        index=pd.DatetimeIndex(idx, name="session").astype("datetime64[ns]"),
    )


def test_cached_symbols_reads_only_valid_file_names(tmp_path):
    folder = tmp_path / "raw" / "bars" / "daily" / "feed=sip" / "adjustment=split"
    folder.mkdir(parents=True)
    for name in ("NVDA", "AMD", "bad name", "1ABC", "lower", "TOOLONGSYMBOL"):
        (folder / f"{name}.parquet").write_bytes(b"")
    assert cached_symbols(tmp_path) == ["AMD", "NVDA"]
    assert cached_symbols(tmp_path / "missing") == []


def test_candle_rows_use_full_history_for_averages():
    frame = _frame()
    rows = candle_rows(frame, 10)
    assert len(rows) == 10
    last = rows[-1]
    assert last["sma20"] == pytest.approx(sum(frame["close"].iloc[-20:]) / 20)
    assert last["sma50"] == pytest.approx(sum(frame["close"].iloc[-50:]) / 50)
    assert {r["direction"] for r in rows} == {"up", "down"}
    assert candle_rows(frame.iloc[:30], 30)[-1]["sma50"] is None  # undefined, not invented


def test_malformed_bars_are_left_out():
    frame = _frame(5)
    frame.iloc[2, frame.columns.get_loc("close")] = float("nan")
    assert len(candle_rows(frame, 5)) == 4


def test_candle_spec_is_a_plain_dict_with_candles_and_volume():
    rows = candle_rows(_frame(), 63)
    spec = candle_spec(rows, title="NVDA")
    assert spec["data"]["values"] == rows
    price, volume = spec["vconcat"]
    marks = [layer["mark"]["type"] for layer in price["layer"]]
    assert marks == ["rule", "bar", "line", "line"]
    assert price["layer"][0]["encoding"]["y"]["scale"] == {"zero": False}
    assert volume["encoding"]["y"]["field"] == "volume"
    assert candle_spec([], title="X") is None
    assert summary(rows)["last"].startswith("Close ")


class _UI:
    def __init__(self, page="Charts"):
        self.page = page
        self.output = []
        self.sidebar = self

    def set_page_config(self, **_):
        pass

    def text(self, value):
        self.output.append(str(value))

    header = caption = warning = error = subheader = text

    def radio(self, label, options, **kwargs):
        return self.page if label == "Page" else options[kwargs.get("index", 0)]

    def selectbox(self, label, options, index=0):
        return options[index]

    def button(self, label):
        return False

    def vega_lite_chart(self, spec, **_):
        self.output.append("chart:" + spec["title"]["text"])


def test_charts_page_works_without_a_state_database(tmp_path, monkeypatch):
    store = ParquetBarStore(tmp_path)
    for symbol in ("SMH", "NVDA"):
        store.write(
            symbol,
            _frame(),
            feed=DataFeed.SIP,
            adjustment=Adjustment.SPLIT,
            source=DataSource.ALPACA,
            fetched_at=datetime(2026, 11, 13, 22, tzinfo=UTC),
        )
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path))
    ui = _UI()
    render(ui, source="cache")
    assert "chart:SMH" in ui.output  # SMH is the default symbol
    assert not (tmp_path / "state").exists()
    other = _UI("Overview")
    render(other, source="cache")
    assert "Dashboard unavailable" in other.output
    assert any("has not run a session yet" in line for line in other.output)


# ------------------------------------------------------------------ 5-minute bars (#121)

from datetime import date  # noqa: E402

from semiedge.dashboard.charts import (  # noqa: E402
    intraday_rows,
    intraday_sessions,
    intraday_spec,
    prior_day_high,
    session_levels,
)
from semiedge.data.calendar import TradingCalendar  # noqa: E402
from semiedge.data.intraday import IntradayBarStore, session_bar_starts  # noqa: E402

CAL = TradingCalendar()


def _intraday(sessions=(date(2026, 11, 12), date(2026, 11, 13))):
    starts = session_bar_starts(CAL, sessions[0])
    for s in sessions[1:]:
        starts = starts.append(session_bar_starts(CAL, s))
    n = len(starts)
    close = [100.0 + (i % 78) * 0.1 for i in range(n)]
    return pd.DataFrame(
        {
            "open": [c - 0.05 for c in close],
            "high": [c + 0.3 for c in close],
            "low": [c - 0.3 for c in close],
            "close": close,
            "volume": [1000.0] * n,
        },
        index=pd.DatetimeIndex(starts, name="ts"),
    )


def test_intraday_rows_are_one_session_in_new_york_time_with_running_vwap():
    frame = _intraday()
    assert intraday_sessions(frame) == ["2026-11-13", "2026-11-12"]
    rows = intraday_rows(frame, "2026-11-13")
    assert len(rows) == 78
    assert (rows[0]["time"], rows[-1]["time"]) == ("09:30", "15:55")
    first = rows[0]
    assert first["vwap"] == pytest.approx((first["high"] + first["low"] + first["close"]) / 3)


def test_opening_range_needs_all_three_bars():
    rows = intraday_rows(_intraday(), "2026-11-13")
    levels = session_levels(rows, 105.0)
    assert levels["OR high"] == pytest.approx(max(r["high"] for r in rows[:3]))
    assert levels["Prior-day high"] == 105.0
    missing = session_levels(rows[1:], None)
    assert missing["OR high"] is None
    assert missing["OR low"] is None


def test_prior_day_high_uses_the_last_session_before():
    daily = _frame(10)
    session = pd.Timestamp(daily.index[5]).date().isoformat()
    assert prior_day_high(daily, session) == daily["high"].iloc[4]
    assert prior_day_high(None, session) is None


def test_intraday_spec_draws_candles_vwap_and_levels():
    rows = intraday_rows(_intraday(), "2026-11-13")
    spec = intraday_spec(rows, title="NVDA", levels={"OR high": 101.0, "Prior-day high": None})
    marks = [layer["mark"]["type"] for layer in spec["vconcat"][0]["layer"]]
    assert marks == ["rule", "bar", "line", "rule", "text"]
    assert spec["vconcat"][0]["layer"][3]["data"]["values"] == [
        {"level": "OR high", "price": 101.0}
    ]
    assert intraday_spec([], title="X", levels={}) is None


class _UI5(_UI):
    def radio(self, label, options, **kwargs):
        if label == "Bar size":
            return "5-minute"
        return super().radio(label, options, **kwargs)


def test_five_minute_page_reads_the_intraday_cache(tmp_path, monkeypatch):
    IntradayBarStore(tmp_path).replace(
        "SMH",
        _intraday(),
        feed=DataFeed.SIP,
        adjustment=Adjustment.SPLIT,
        source=DataSource.ALPACA,
        fetched_at=datetime(2026, 11, 14, 1, tzinfo=UTC),
        calendar=CAL,
    )
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(tmp_path))
    ui = _UI5()
    render(ui, source="cache")
    assert "chart:SMH · 2026-11-13" in ui.output  # newest session first
    assert any(line.startswith("78 bars · OR high") for line in ui.output)
