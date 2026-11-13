"""Unit tests for ``semiedge.reporting.signal_report`` (issue #14).

Small hand-computable windows (as in ``tests/unit/strategy/momentum_fixtures.py``): NVDA breaks
out on 2024-01-08 with close 12, ATR(2) 1.25, so the proposed stop is 9.5 and the target 17.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from semiedge.config import AppConfig, MomentumV1Params, ScoreNormalization, load_app_config
from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import Adjustment, DataFeed, DataSource
from semiedge.reporting import (
    SYNTHETIC_BANNER,
    ReportDataError,
    SeriesProvenance,
    SignalReport,
    build_signal_report,
    new_run_id,
    persist_report,
    render_json,
    render_markdown,
    render_table,
    report_path,
    report_to_dict,
)
from semiedge.risk import propose_position_size

AS_OF = date(2024, 1, 8)
NOW = datetime(2024, 1, 9, 3, 0, tzinfo=UTC)
RUN_ID = "20240109T030000Z-0123abcd"
SMALL_PARAMS = MomentumV1Params(
    regime_sma_window=3,
    fast_sma_window=2,
    slow_sma_window=3,
    breakout_lookback=2,
    return_lookback=2,
    volume_lookback=2,
    atr_window=2,
    score_normalization=ScoreNormalization(trend_persistence_window=2),
)
SESSIONS = pd.DatetimeIndex(
    ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"],
    name="session",
    dtype="datetime64[ns]",
)


def _frame(columns: dict[str, list[float]]) -> pd.DataFrame:
    return pd.DataFrame({k: np.asarray(v, dtype=np.float64) for k, v in columns.items()}, SESSIONS)


def _stock() -> pd.DataFrame:
    return _frame(
        {
            "open": [10.0, 10.0, 10.0, 10.5, 11.5],
            "high": [10.5, 10.5, 10.5, 11.0, 12.5],
            "low": [9.5, 9.5, 9.5, 10.0, 11.0],
            "close": [10.0, 10.0, 10.0, 11.0, 12.0],
            "volume": [100.0, 100.0, 100.0, 100.0, 300.0],
        }
    )


def _regime() -> pd.DataFrame:
    close = [100.0, 100.0, 100.0, 105.0, 110.0]
    return _frame(
        {
            "open": close,
            "high": [c + 1 for c in close],
            "low": [c - 1 for c in close],
            "close": close,
            "volume": [1e6] * 5,
        }
    )


@pytest.fixture
def config(config_dir: Path) -> AppConfig:
    return load_app_config(config_dir).model_copy(update={"strategy": SMALL_PARAMS})


def _provenance(
    bars: dict[str, pd.DataFrame],
    source: DataSource = DataSource.FIXTURE,
    feed: DataFeed = DataFeed.SIP,
) -> list[SeriesProvenance]:
    return [
        SeriesProvenance(
            symbol,
            source,
            feed,
            Adjustment.SPLIT,
            None if source == DataSource.SYNTHETIC else NOW,
            frame.index[0].date(),
            frame.index[-1].date(),
            len(frame),
        )
        for symbol, frame in bars.items()
    ]


def _build(
    config: AppConfig,
    bars: dict[str, pd.DataFrame] | None = None,
    provenance: list[SeriesProvenance] | None = None,
    **overrides: object,
) -> SignalReport:
    bars = {"NVDA": _stock(), "SMH": _regime()} if bars is None else bars
    kwargs: dict[str, object] = {
        "config": config,
        "config_fingerprint": "f" * 64,
        "as_of": AS_OF,
        "source": "cache",
        "feed": DataFeed.SIP,
        "adjustment": Adjustment.SPLIT,
        "equity": 100_000.0,
        "equity_source": "reference",
        "mode": "scanner",
        "calendar": TradingCalendar(),
        "run_id": RUN_ID,
        "generated_at": NOW,
        "semiedge_version": "0.1.0.dev0",
        "git_sha": "abc123def456",
    }
    kwargs.update(overrides)
    prov = _provenance(bars) if provenance is None else provenance
    return build_signal_report(bars, prov, **kwargs)  # type: ignore[arg-type]


# ------------------------------------------------------------------ eligible candidate


def test_eligible_candidate_is_ranked_with_conditions_and_proposed_size(config: AppConfig) -> None:
    report = _build(config)
    assert report.regime_actionable
    [nvda] = report.eligible
    assert (nvda.symbol, nvda.rank, nvda.close, nvda.proposed_stop) == ("NVDA", 1, 12.0, 9.5)
    # The smaller of the two budgets: risk 0.25% * 100k / 2.5 = 100 < allocation 5% * 100k / 12.
    size = report.sizes["NVDA"]
    assert size == propose_position_size(
        100_000.0,
        12.0,
        9.5,
        max_risk_per_trade_pct=config.risk.max_risk_per_trade_pct,
        max_position_pct=config.risk.max_position_pct,
    )
    assert (size.shares, size.binding_constraint) == (100, "risk")
    # Symbols without bars are evaluated, rejected and explained; they get no size.
    rejected = [e for e in report.evaluations if not e.eligible]
    assert {e.symbol for e in rejected} == set(config.universe.tradable_symbols) - {"NVDA"}
    assert all(e.rejection_reasons for e in rejected)
    assert set(report.sizes) == {"NVDA"}


def test_json_schema_carries_every_condition_and_run_metadata(config: AppConfig) -> None:
    payload = json.loads(render_json(_build(config)))
    assert payload["schema_version"] == 1
    assert payload["synthetic"] is False
    assert payload["banner"] is None
    assert payload["run"] == {
        "run_id": RUN_ID,
        "generated_at": NOW.isoformat(),
        "as_of": "2024-01-08",
        "mode": "scanner",
        "semiedge_version": "0.1.0.dev0",
        "git_sha": "abc123def456",
        "config_fingerprint": "f" * 64,
        "orders_submitted": 0,
    }
    assert payload["equity"] == {"used": 100_000.0, "source": "reference"}
    assert payload["data"]["source"] == "cache"
    assert {s["source"] for s in payload["data"]["series"]} == {"fixture"}
    nvda = next(e for e in payload["evaluations"] if e["symbol"] == "NVDA")
    assert nvda["proposed_size"]["shares"] == 100
    assert len(nvda["conditions"]) == 6
    for condition in nvda["conditions"]:
        assert set(condition) == {"name", "passed", "value", "threshold", "description"}
        assert condition["passed"] is True
    assert payload["summary"] == {"evaluated": len(config.universe.tradable_symbols), "eligible": 1}


def test_renderings_show_candidates_conditions_and_disclaimer(config: AppConfig) -> None:
    report = _build(config)
    table = render_table(report)
    markdown = render_markdown(report)
    for text in (table, markdown):
        assert "NVDA" in text
        assert "SMH close 110 > SMA3 105" in text
        assert "no order was placed" in text
        assert SYNTHETIC_BANNER not in text
    assert "| 1 | NVDA |" in markdown


def test_no_candidates_means_no_proposed_trades(config: AppConfig) -> None:
    stock = _stock()
    stock.loc[stock.index[-1], "volume"] = 100.0  # relative volume 1.0 < 1.2
    report = _build(config, {"NVDA": stock, "SMH": _regime()})
    assert report.eligible == ()
    assert dict(report.sizes) == {}
    assert "No candidates" in render_table(report)
    assert "No candidates" in render_markdown(report)
    assert report.regime_actionable  # no candidates is not a data failure


def test_missing_regime_benchmark_is_not_actionable(config: AppConfig) -> None:
    report = _build(config, {"NVDA": _stock()})
    assert not report.regime_actionable
    assert report.eligible == ()
    assert "NOT ACTIONABLE" in render_table(report)
    assert report.quality["SMH"].issues == ("no bars available",)


def test_non_finite_values_serialise_as_null(config: AppConfig) -> None:
    stock = _stock().iloc[:2]  # too short: indicators and score are undefined
    payload = report_to_dict(_build(config, {"NVDA": stock, "SMH": _regime()}))
    json.dumps(payload, allow_nan=False)  # raises on NaN or inf
    nvda = next(e for e in payload["evaluations"] if e["symbol"] == "NVDA")
    assert nvda["eligible"] is False
    assert nvda["atr"] is None


# ------------------------------------------------------------------ fail closed


@pytest.mark.parametrize("equity", [0.0, -1.0, math.nan, math.inf, True, "100000"])
def test_invalid_equity_is_rejected(config: AppConfig, equity: object) -> None:
    with pytest.raises(ReportDataError, match="equity"):
        _build(config, equity=equity)


@pytest.mark.parametrize(
    ("run_id", "generated_at"),
    [
        ("../escape", NOW),
        ("20240109T030000Z-0123ABCD", NOW),
        (RUN_ID, datetime(2024, 1, 9, 3, 0)),  # noqa: DTZ001 - naive on purpose
    ],
)
def test_invalid_run_metadata_is_rejected(
    config: AppConfig, run_id: str, generated_at: datetime
) -> None:
    with pytest.raises(ReportDataError):
        _build(config, run_id=run_id, generated_at=generated_at)


def test_new_run_id_format() -> None:
    assert new_run_id(NOW, "0123abcd") == RUN_ID
    with pytest.raises(ReportDataError):
        new_run_id(NOW, "../../x")


def test_every_series_needs_one_provenance_record(config: AppConfig) -> None:
    bars = {"NVDA": _stock(), "SMH": _regime()}
    with pytest.raises(ReportDataError, match="exactly one provenance"):
        _build(config, bars, _provenance({"NVDA": bars["NVDA"]}))
    with pytest.raises(ReportDataError, match="exactly one provenance"):
        _build(config, bars, _provenance(bars) + _provenance({"SMH": bars["SMH"]}))


def test_feed_or_adjustment_mismatch_is_rejected(config: AppConfig) -> None:
    bars = {"NVDA": _stock(), "SMH": _regime()}
    with pytest.raises(ReportDataError, match="feed or adjustment"):
        _build(config, bars, _provenance(bars, feed=DataFeed.IEX))


# ------------------------------------------------------------------ persistence


def test_persisted_report_round_trips_and_never_overwrites(
    config: AppConfig, tmp_path: Path
) -> None:
    report = _build(config)
    path = persist_report(report, tmp_path)
    assert path == tmp_path / "reports" / "signals" / f"2024-01-08_{RUN_ID}.json"
    assert json.loads(path.read_text(encoding="utf-8")) == report_to_dict(report)
    with pytest.raises(FileExistsError):
        persist_report(report, tmp_path)


def test_report_path_rejects_unsafe_run_id(config: AppConfig, tmp_path: Path) -> None:
    report = _build(config)
    unsafe = SignalReport(**{**report.__dict__, "run_id": "../../etc"})
    with pytest.raises(ReportDataError):
        report_path(unsafe, tmp_path)
