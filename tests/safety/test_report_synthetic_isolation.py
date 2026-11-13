"""Safety: synthetic data is labelled SYNTHETIC and never mixed with market data (issue #14)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from semiedge.config import load_app_config
from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import Adjustment, DataFeed, DataSource
from semiedge.data.synthetic import SyntheticMarketDataSource
from semiedge.reporting import (
    SYNTHETIC_BANNER,
    ReportDataError,
    SeriesProvenance,
    build_signal_report,
    persist_report,
    render_json,
    render_markdown,
    render_table,
)

pytestmark = pytest.mark.safety

AS_OF = date(2024, 6, 28)
NOW = datetime(2024, 6, 29, 1, 0, tzinfo=UTC)


def _inputs(config_dir: Path, source: DataSource, feed: DataFeed) -> tuple[dict, list]:  # type: ignore[type-arg]
    config = load_app_config(config_dir)
    calendar = TradingCalendar()
    symbols = (*config.universe.tradable_symbols, config.universe.regime_benchmark)
    start = calendar.sessions_window(AS_OF, config.strategy.required_history())[0]
    bars = SyntheticMarketDataSource(calendar=calendar).get_daily_bars(symbols, start, AS_OF)
    provenance = [
        SeriesProvenance(s, source, feed, Adjustment.SPLIT, None, None, None, len(f))
        for s, f in bars.items()
    ]
    return bars, provenance


def _build(config_dir: Path, bars: dict, provenance: list, source: str, feed: DataFeed):  # type: ignore[no-untyped-def, type-arg]
    return build_signal_report(
        bars,
        provenance,
        config=load_app_config(config_dir),
        config_fingerprint="0" * 64,
        as_of=AS_OF,
        source=source,  # type: ignore[arg-type]
        feed=feed,
        adjustment=Adjustment.SPLIT,
        equity=100_000.0,
        equity_source="reference",
        mode="scanner",
        calendar=TradingCalendar(),
        run_id="20240629T010000Z-00000000",
        generated_at=NOW,
        semiedge_version="test",
        git_sha=None,
    )


def test_synthetic_report_is_labelled_everywhere_and_stored_apart(
    config_dir: Path, tmp_path: Path
) -> None:
    bars, provenance = _inputs(config_dir, DataSource.SYNTHETIC, DataFeed.SYNTHETIC)
    report = _build(config_dir, bars, provenance, "synthetic", DataFeed.SYNTHETIC)
    assert report.synthetic
    table, markdown = render_table(report), render_markdown(report)
    for text in (table, markdown):
        lines = text.strip().splitlines()
        assert SYNTHETIC_BANNER in lines[0]
        assert SYNTHETIC_BANNER in lines[-1]
    payload = json.loads(render_json(report))
    assert payload["synthetic"] is True
    assert payload["banner"] == SYNTHETIC_BANNER
    assert payload["data"]["feed"] == "synthetic"
    path = persist_report(report, tmp_path)
    assert path.relative_to(tmp_path).parts[:3] == ("synthetic", "reports", "signals")
    assert not (tmp_path / "reports").exists()


@pytest.mark.parametrize(
    ("record_source", "record_feed", "report_source", "report_feed"),
    [
        (DataSource.SYNTHETIC, DataFeed.SYNTHETIC, "cache", DataFeed.SIP),  # feed mismatch
        (DataSource.SYNTHETIC, DataFeed.SIP, "cache", DataFeed.SIP),  # synthetic in a market report
        (DataSource.ALPACA, DataFeed.SYNTHETIC, "synthetic", DataFeed.SYNTHETIC),  # market in synth
        (DataSource.FIXTURE, DataFeed.SIP, "synthetic", DataFeed.SIP),  # synthetic needs its feed
        (
            DataSource.ALPACA,
            DataFeed.SYNTHETIC,
            "cache",
            DataFeed.SYNTHETIC,
        ),  # only synthetic uses it
    ],
)
def test_synthetic_and_market_data_are_never_mixed(
    config_dir: Path,
    record_source: DataSource,
    record_feed: DataFeed,
    report_source: str,
    report_feed: DataFeed,
) -> None:
    bars, _ = _inputs(config_dir, record_source, record_feed)
    provenance = [
        SeriesProvenance(s, record_source, record_feed, Adjustment.SPLIT, NOW, None, None, len(f))
        for s, f in bars.items()
    ]
    with pytest.raises(ReportDataError):
        _build(config_dir, bars, provenance, report_source, report_feed)


def test_one_synthetic_series_poisons_a_market_report(config_dir: Path) -> None:
    bars, provenance = _inputs(config_dir, DataSource.FIXTURE, DataFeed.SIP)
    provenance[0] = SeriesProvenance(
        provenance[0].symbol, DataSource.SYNTHETIC, DataFeed.SIP, Adjustment.SPLIT, None, None,
        None, 1,
    )  # fmt: skip
    with pytest.raises(ReportDataError, match="synthetic and market data cannot be mixed"):
        _build(config_dir, bars, provenance, "cache", DataFeed.SIP)


def test_market_sources_are_not_mixed_with_each_other(config_dir: Path) -> None:
    bars, provenance = _inputs(config_dir, DataSource.FIXTURE, DataFeed.SIP)
    first = provenance[0]
    provenance[0] = SeriesProvenance(
        first.symbol, DataSource.ALPACA, DataFeed.SIP, Adjustment.SPLIT, NOW, None, None, 1
    )
    with pytest.raises(ReportDataError, match="more than one data source"):
        _build(config_dir, bars, provenance, "cache", DataFeed.SIP)
