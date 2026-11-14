"""5-minute bar contract, cache and Alpaca adapter (#118). Fake vendor frames only; no network."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest

from semiedge import cli
from semiedge.data.alpaca_source import AlpacaMarketDataSource, MarketDataError
from semiedge.data.calendar import TradingCalendar
from semiedge.data.intraday import (
    INTRADAY_INDEX_NAME,
    IntradayBarStore,
    session_bar_starts,
    validate_intraday_frame,
)
from semiedge.data.schema import Adjustment, DataFeed, DataSchemaError, DataSource

CAL = TradingCalendar()
DAY = date(2026, 11, 13)  # Friday, regular session
EARLY = date(2026, 11, 27)  # day after Thanksgiving, 13:00 close
FETCHED = datetime(2026, 11, 14, 1, 0, tzinfo=UTC)


def _bars(starts: pd.DatetimeIndex, base: float = 100.0) -> pd.DataFrame:
    n = len(starts)
    close = base + np.arange(n) * 0.1
    return pd.DataFrame(
        {
            "open": close - 0.05,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": np.full(n, 1000.0),
            "trade_count": np.full(n, 10.0),
            "vwap": close,
        },
        index=pd.DatetimeIndex(starts, name=INTRADAY_INDEX_NAME),
    )


def test_session_bar_starts_cover_regular_hours_and_early_closes():
    full = session_bar_starts(CAL, DAY)
    assert len(full) == 78
    assert full[0] == pd.Timestamp("2026-11-13 14:30", tz=UTC)  # 09:30 New York
    assert full[-1] == pd.Timestamp("2026-11-13 20:55", tz=UTC)  # 15:55 New York
    assert len(session_bar_starts(CAL, EARLY)) == 42  # 09:30 to 12:55


def test_valid_frame_passes():
    validate_intraday_frame(_bars(session_bar_starts(CAL, DAY)), symbol="NVDA", calendar=CAL)


def _bad(mutate):
    frame = _bars(session_bar_starts(CAL, DAY))
    frame = mutate(frame)
    with pytest.raises(DataSchemaError):
        validate_intraday_frame(frame, symbol="NVDA", calendar=CAL)


def test_off_grid_start_is_refused():
    _bad(lambda f: f.set_axis(f.index + timedelta(minutes=1)).rename_axis(INTRADAY_INDEX_NAME))


def test_premarket_bar_is_refused():
    pre = pd.DatetimeIndex([pd.Timestamp("2026-11-13 14:25", tz=UTC)], name=INTRADAY_INDEX_NAME)
    _bad(lambda f: pd.concat([_bars(pre), f]))


def test_bar_after_an_early_close_is_refused():
    starts = session_bar_starts(CAL, EARLY)
    late = pd.DatetimeIndex([starts[-1] + timedelta(minutes=5)], name=INTRADAY_INDEX_NAME)
    frame = pd.concat([_bars(starts), _bars(late)])
    with pytest.raises(DataSchemaError, match="outside regular hours"):
        validate_intraday_frame(frame, symbol="NVDA", calendar=CAL)


def test_weekend_bars_are_refused():
    sat = pd.DatetimeIndex([pd.Timestamp("2026-11-14 15:00", tz=UTC)], name=INTRADAY_INDEX_NAME)
    with pytest.raises(DataSchemaError, match="not a session"):
        validate_intraday_frame(_bars(sat), symbol="NVDA", calendar=CAL)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: pd.concat([f, f.iloc[[0]]]),  # duplicate start
        lambda f: f.iloc[::-1],  # unsorted
        lambda f: f.tz_convert("America/New_York"),  # not UTC
        lambda f: f.tz_localize(None),  # naive
        lambda f: f.assign(close=np.nan),  # non-finite
        lambda f: f.assign(high=f["close"] - 1),  # high below close
        lambda f: f.assign(volume=-1.0),  # negative volume
        lambda f: f.assign(extra=1.0),  # unexpected column
        lambda f: f.rename_axis("session"),  # wrong index name
    ],
)
def test_malformed_frames_are_refused(mutate):
    _bad(mutate)


def test_store_replaces_whole_file_and_reads_back(tmp_path: Path):
    store = IntradayBarStore(tmp_path)
    first = _bars(session_bar_starts(CAL, date(2026, 11, 12)))
    second = _bars(session_bar_starts(CAL, DAY), base=200.0)
    kwargs = {"feed": DataFeed.SIP, "adjustment": Adjustment.SPLIT, "source": DataSource.ALPACA}
    store.replace("NVDA", first, fetched_at=FETCHED, calendar=CAL, **kwargs)
    store.replace("NVDA", second, fetched_at=FETCHED + timedelta(hours=1), calendar=CAL, **kwargs)
    series = store.read("NVDA", feed=DataFeed.SIP, adjustment=Adjustment.SPLIT)
    assert series is not None
    assert len(series.frame) == 78  # the first fetch is gone, not merged
    assert series.frame["close"].iloc[0] == 200.0
    assert series.fetched_at == FETCHED + timedelta(hours=1)
    assert store.symbols(feed=DataFeed.SIP, adjustment=Adjustment.SPLIT) == ["NVDA"]
    assert store.summary(feed=DataFeed.SIP, adjustment=Adjustment.SPLIT)["NVDA"]["rows"] == 78


def test_refused_frame_leaves_the_stored_file_untouched(tmp_path: Path):
    store = IntradayBarStore(tmp_path)
    good = _bars(session_bar_starts(CAL, DAY))
    kwargs = {"feed": DataFeed.SIP, "adjustment": Adjustment.SPLIT, "source": DataSource.ALPACA}
    path = store.replace("NVDA", good, fetched_at=FETCHED, calendar=CAL, **kwargs)
    before = path.read_bytes()
    with pytest.raises(DataSchemaError):
        store.replace("NVDA", good.assign(volume=-1.0), fetched_at=FETCHED, calendar=CAL, **kwargs)
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="synthetic"):
        store.replace(
            "NVDA",
            good,
            feed=DataFeed.SYNTHETIC,
            adjustment=Adjustment.SPLIT,
            source=DataSource.ALPACA,
            fetched_at=FETCHED,
            calendar=CAL,
        )


def test_store_rejects_a_tampered_file(tmp_path: Path):
    store = IntradayBarStore(tmp_path)
    kwargs = {"feed": DataFeed.SIP, "adjustment": Adjustment.SPLIT, "source": DataSource.ALPACA}
    path = store.replace(
        "NVDA", _bars(session_bar_starts(CAL, DAY)), fetched_at=FETCHED, calendar=CAL, **kwargs
    )
    raw = pd.read_parquet(path)
    raw.iloc[0, raw.columns.get_loc("fetched_at")] = pd.Timestamp("2020-01-01", tz=UTC)
    raw.to_parquet(path)
    with pytest.raises(DataSchemaError, match="mixes fetches"):
        store.read("NVDA", feed=DataFeed.SIP, adjustment=Adjustment.SPLIT)


# ------------------------------------------------------------------ Alpaca adapter


def _vendor(symbol: str, starts: pd.DatetimeIndex) -> pd.DataFrame:
    bars = _bars(starts)
    bars.index = pd.MultiIndex.from_arrays(
        [[symbol] * len(starts), list(starts)], names=["symbol", "timestamp"]
    )
    return bars


class _Clock:
    def __init__(self, now: datetime) -> None:
        self._now = now
        self.combine = datetime.combine

    def now(self, tz: Any = None) -> datetime:
        return self._now


def _source(frame: pd.DataFrame) -> tuple[AlpacaMarketDataSource, Mock]:
    client = Mock(spec=["get_stock_bars"])
    client.get_stock_bars.return_value = Mock(df=frame)
    return AlpacaMarketDataSource(client, DataFeed.SIP, Adjustment.SPLIT, calendar=CAL), client


def test_adapter_drops_extended_hours_and_unfinished_bars():
    regular = session_bar_starts(CAL, DAY)
    extended = pd.DatetimeIndex(
        [pd.Timestamp("2026-11-13 13:00", tz=UTC), pd.Timestamp("2026-11-13 21:30", tz=UTC)]
    )
    frame = _vendor("NVDA", regular.append(extended).sort_values())
    source, client = _source(frame)
    # 16:00 New York + 30 min: the SIP clamp (now - 16 min) still covers the whole session.
    with patch(
        "semiedge.data.alpaca_source.datetime", _Clock(datetime(2026, 11, 13, 21, 30, tzinfo=UTC))
    ):
        out = source.get_intraday_bars(["NVDA"], DAY, DAY)["NVDA"]
    assert len(out) == 78
    request = client.get_stock_bars.call_args.args[0]
    assert request.timeframe.amount == 5
    # At 15:20 New York the SIP clamp ends at 15:04: bars from 15:00 on are not complete yet.
    with patch(
        "semiedge.data.alpaca_source.datetime", _Clock(datetime(2026, 11, 13, 20, 20, tzinfo=UTC))
    ):
        partial = source.get_intraday_bars(["NVDA"], DAY, DAY)["NVDA"]
    assert partial.index[-1] == pd.Timestamp("2026-11-13 19:55", tz=UTC)  # 14:55 New York


def test_adapter_errors_never_carry_vendor_text():
    client = Mock(spec=["get_stock_bars"])
    client.get_stock_bars.side_effect = RuntimeError("https://x.invalid?apikey=FAKE_SECRET")
    source = AlpacaMarketDataSource(client, DataFeed.SIP, Adjustment.SPLIT, calendar=CAL)
    clock = _Clock(datetime(2026, 11, 14, tzinfo=UTC))
    with (
        patch("semiedge.data.alpaca_source.datetime", clock),
        pytest.raises(MarketDataError) as caught,
    ):
        source.get_intraday_bars(["NVDA"], DAY, DAY)
    assert "FAKE_SECRET" not in str(caught.value)


def test_cli_fetch_intraday_stores_and_status_reports(
    config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("SEMIEDGE_DATA_DIR", str(data_dir))
    now = datetime(2026, 11, 14, 1, 0, tzinfo=UTC)
    monkeypatch.setattr(cli, "utcnow", lambda: now)
    source, _ = _source(_vendor("NVDA", session_bar_starts(CAL, DAY)))
    monkeypatch.setattr(cli, "market_data_source", lambda settings, config: source)
    with patch("semiedge.data.alpaca_source.datetime", _Clock(now)):
        code = cli.main(
            [
                "data",
                "fetch-intraday",
                "--symbols",
                "NVDA",
                "--start",
                "2026-11-13",
                "--end",
                "2026-11-13",
                "--config-dir",
                str(config_dir),
            ]
        )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["symbols"]["NVDA"]["rows"] == 78
    assert cli.main(["data", "status", "--config-dir", str(config_dir)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["intraday_5min"]["NVDA"]["rows"] == 78
