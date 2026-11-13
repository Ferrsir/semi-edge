"""Ranked daily signal report: build, render (table / markdown / JSON) and persist (issue #14).

See docs/specifications/phase1-interfaces.md (``semiedge.reporting``). A report is research
output: it ranks candidates and *proposes* sizes for review. Nothing here talks to a broker or
places, changes or cancels orders.

Synthetic data is never mixed with market data. A report is either entirely synthetic (every
series from the synthetic generator, feed ``synthetic``) or built from a single non-synthetic
cache source. Synthetic reports carry :data:`SYNTHETIC_BANNER` in every rendering and are
persisted only under ``{data_dir}/synthetic/``.

Fail closed: inconsistent provenance or invalid equity raises :class:`ReportDataError` rather
than producing a report, and a report whose regime benchmark is not actionable is still
rendered (so the reasons are visible) but flagged by :attr:`SignalReport.regime_actionable`.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal

import pandas as pd

from semiedge.config.models import AppConfig
from semiedge.data.calendar import TradingCalendar
from semiedge.data.schema import Adjustment, DataFeed, DataSource
from semiedge.data.validation import DataQualityReport, assess_quality
from semiedge.risk.sizing import SizingResult, propose_position_size
from semiedge.strategy.base import SignalEvaluation
from semiedge.strategy.momentum_v1 import evaluate_universe

__all__ = [
    "SCHEMA_VERSION",
    "SYNTHETIC_BANNER",
    "ReportDataError",
    "SeriesProvenance",
    "SignalReport",
    "build_signal_report",
    "persist_report",
    "render_json",
    "render_markdown",
    "render_table",
    "report_path",
    "report_to_dict",
]

SCHEMA_VERSION: Final = 1
SYNTHETIC_BANNER: Final = "SYNTHETIC DATA — NOT MARKET DATA"
DISCLAIMER: Final = (
    "Research output only. Sizes are proposals, not orders; no order was placed. "
    "Score weights are unvalidated heuristic priors. Entry reference is the signal-session "
    "close; any actual entry would be the next session open."
)
_RUN_ID: Final = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")

ReportSource = Literal["cache", "synthetic"]
EquitySource = Literal["reference", "account", "cli"]


class ReportDataError(ValueError):
    """The inputs cannot produce a trustworthy report (provenance, equity or run metadata)."""


@dataclass(frozen=True)
class SeriesProvenance:
    """Where one symbol's bars came from."""

    symbol: str
    source: DataSource
    feed: DataFeed
    adjustment: Adjustment
    fetched_at: datetime | None  # None for bars generated in this run (synthetic)
    first_session: date | None
    last_session: date | None
    rows: int


@dataclass(frozen=True)
class SignalReport:
    run_id: str
    generated_at: datetime
    as_of: date
    mode: str
    source: ReportSource
    feed: DataFeed
    adjustment: Adjustment
    strategy: str
    strategy_version: str
    config_fingerprint: str
    semiedge_version: str
    git_sha: str | None
    equity_used: float
    equity_source: EquitySource
    regime_benchmark: str
    evaluations: tuple[SignalEvaluation, ...]
    quality: Mapping[str, DataQualityReport]
    sizes: Mapping[str, SizingResult]  # eligible symbols only
    provenance: tuple[SeriesProvenance, ...]

    @property
    def synthetic(self) -> bool:
        return self.source == "synthetic"

    @property
    def regime_actionable(self) -> bool:
        report = self.quality.get(self.regime_benchmark)
        return report is not None and report.is_actionable

    @property
    def eligible(self) -> tuple[SignalEvaluation, ...]:
        return tuple(e for e in self.evaluations if e.eligible)


def build_signal_report(
    bars: Mapping[str, pd.DataFrame],
    provenance: Sequence[SeriesProvenance],
    *,
    config: AppConfig,
    config_fingerprint: str,
    as_of: date,
    source: ReportSource,
    feed: DataFeed,
    adjustment: Adjustment,
    equity: float,
    equity_source: EquitySource,
    mode: str,
    calendar: TradingCalendar,
    run_id: str,
    generated_at: datetime,
    semiedge_version: str,
    git_sha: str | None,
) -> SignalReport:
    """Assess data quality, evaluate ``momentum_v1`` at ``as_of`` and propose sizes."""
    _check_run_metadata(run_id, generated_at)
    if isinstance(equity, bool) or not isinstance(equity, int | float):
        raise ReportDataError("equity must be a number")
    if not math.isfinite(equity) or equity <= 0:
        raise ReportDataError("equity must be finite and positive")
    _check_provenance(bars, provenance, source=source, feed=feed, adjustment=adjustment)

    params = config.strategy
    regime = config.universe.regime_benchmark
    tradable = config.universe.tradable_symbols
    lookback = params.required_history()
    quality: dict[str, DataQualityReport] = {}
    for symbol in (*tradable, regime):
        frame = bars.get(symbol)
        if frame is None:
            quality[symbol] = DataQualityReport(
                symbol,
                as_of,
                None,
                False,
                tuple(calendar.sessions_window(as_of, lookback)),
                feed,
                ("no bars available",),
            )
            continue
        quality[symbol] = assess_quality(
            frame,
            symbol=symbol,
            as_of=as_of,
            calendar=calendar,
            lookback_sessions=lookback,
            feed=feed,
            max_missing_sessions=config.data.max_missing_sessions_in_lookback,
        )

    starts = [frame.index[0].date() for frame in bars.values() if len(frame)]
    first = min([*starts, as_of])
    evaluations = evaluate_universe(
        bars,
        regime,
        params,
        as_of,
        tradable=tradable,
        quality=quality,
        expected_sessions=calendar.sessions_in_range(first, as_of),
    )
    sizes: dict[str, SizingResult] = {}
    for evaluation in evaluations:
        if not evaluation.eligible:
            continue
        if evaluation.close is None or evaluation.proposed_stop is None:  # eligible ⇒ both set
            raise ReportDataError(f"{evaluation.symbol}: eligible without close and stop")
        sizes[evaluation.symbol] = propose_position_size(
            equity,
            evaluation.close,
            evaluation.proposed_stop,
            max_risk_per_trade_pct=config.risk.max_risk_per_trade_pct,
            max_position_pct=config.risk.max_position_pct,
        )
    return SignalReport(
        run_id=run_id,
        generated_at=generated_at,
        as_of=as_of,
        mode=mode,
        source=source,
        feed=feed,
        adjustment=adjustment,
        strategy=params.name,
        strategy_version=params.version,
        config_fingerprint=config_fingerprint,
        semiedge_version=semiedge_version,
        git_sha=git_sha,
        equity_used=float(equity),
        equity_source=equity_source,
        regime_benchmark=regime,
        evaluations=tuple(evaluations),
        quality=MappingProxyType(quality),
        sizes=MappingProxyType(sizes),
        provenance=tuple(sorted(provenance, key=lambda p: p.symbol)),
    )


def new_run_id(generated_at: datetime, token: str) -> str:
    """``YYYYMMDDTHHMMSSZ-<8 hex>`` from a UTC timestamp and an 8-hex-digit random token."""
    run_id = f"{generated_at.astimezone(UTC):%Y%m%dT%H%M%SZ}-{token}"
    _check_run_metadata(run_id, generated_at)
    return run_id


def _check_run_metadata(run_id: str, generated_at: datetime) -> None:
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ReportDataError("generated_at must be timezone-aware")
    if type(run_id) is not str or _RUN_ID.fullmatch(run_id) is None:
        raise ReportDataError("run_id must look like YYYYMMDDTHHMMSSZ-<8 hex digits>")


def _check_provenance(
    bars: Mapping[str, pd.DataFrame],
    provenance: Sequence[SeriesProvenance],
    *,
    source: ReportSource,
    feed: DataFeed,
    adjustment: Adjustment,
) -> None:
    """One provenance per series; synthetic and market data never share a report."""
    by_symbol = {p.symbol: p for p in provenance}
    if len(by_symbol) != len(provenance) or set(by_symbol) != set(bars):
        raise ReportDataError("every series needs exactly one provenance record")
    synthetic = source == "synthetic"
    if synthetic != (feed == DataFeed.SYNTHETIC):
        raise ReportDataError("synthetic reports must use the synthetic feed, and only they may")
    sources = {p.source for p in provenance}
    for record in provenance:
        if record.feed != feed or record.adjustment != adjustment:
            raise ReportDataError(f"{record.symbol}: feed or adjustment differs from the report")
        if (record.source == DataSource.SYNTHETIC) != synthetic:
            raise ReportDataError(f"{record.symbol}: synthetic and market data cannot be mixed")
    if len(sources) > 1:
        names = ", ".join(sorted(s.value for s in sources))
        raise ReportDataError(f"series come from more than one data source ({names})")


# ------------------------------------------------------------------ serialisation


def _number(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _iso(value: date | datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _size_dict(size: SizingResult | None) -> dict[str, Any] | None:
    if size is None:
        return None
    return {
        "shares": size.shares,
        "shares_by_risk": size.shares_by_risk,
        "shares_by_allocation": size.shares_by_allocation,
        "binding_constraint": size.binding_constraint,
        "dollar_risk": _number(size.dollar_risk),
        "notional": _number(size.notional),
        "reason": size.reason,
    }


def report_to_dict(report: SignalReport) -> dict[str, Any]:
    """The stable JSON schema (``schema_version`` 1) of a report."""
    return {
        "schema_version": SCHEMA_VERSION,
        "synthetic": report.synthetic,
        "banner": SYNTHETIC_BANNER if report.synthetic else None,
        "disclaimer": DISCLAIMER,
        "run": {
            "run_id": report.run_id,
            "generated_at": _iso(report.generated_at),
            "as_of": _iso(report.as_of),
            "mode": report.mode,
            "semiedge_version": report.semiedge_version,
            "git_sha": report.git_sha,
            "config_fingerprint": report.config_fingerprint,
            "orders_submitted": 0,
        },
        "strategy": {"name": report.strategy, "version": report.strategy_version},
        "equity": {"used": report.equity_used, "source": report.equity_source},
        "data": {
            "source": report.source,
            "feed": report.feed.value,
            "adjustment": report.adjustment.value,
            "regime_benchmark": report.regime_benchmark,
            "regime_actionable": report.regime_actionable,
            "series": [
                {
                    "symbol": p.symbol,
                    "source": p.source.value,
                    "feed": p.feed.value,
                    "adjustment": p.adjustment.value,
                    "fetched_at": _iso(p.fetched_at),
                    "first_session": _iso(p.first_session),
                    "last_session": _iso(p.last_session),
                    "rows": p.rows,
                }
                for p in report.provenance
            ],
        },
        "summary": {
            "evaluated": len(report.evaluations),
            "eligible": len(report.eligible),
        },
        "evaluations": [
            {
                "rank": e.rank,
                "symbol": e.symbol,
                "session": _iso(e.session),
                "eligible": e.eligible,
                "score": _number(e.score),
                "score_components": {k: _number(v) for k, v in sorted(e.score_components.items())},
                "close": _number(e.close),
                "atr": _number(e.atr),
                "proposed_stop": _number(e.proposed_stop),
                "proposed_target": _number(e.proposed_target),
                "proposed_size": _size_dict(report.sizes.get(e.symbol)),
                "conditions": [
                    {
                        "name": c.name,
                        "passed": c.passed,
                        "value": _number(c.value),
                        "threshold": _number(c.threshold),
                        "description": c.description,
                    }
                    for c in e.conditions
                ],
                "rejection_reasons": list(e.rejection_reasons),
            }
            for e in report.evaluations
        ],
        "data_quality": [
            {
                "symbol": q.symbol,
                "as_of": _iso(q.as_of),
                "last_session": _iso(q.last_session),
                "is_fresh": q.is_fresh,
                "is_actionable": q.is_actionable,
                "missing_sessions": [_iso(s) for s in q.missing_sessions],
                "feed": None if q.feed is None else q.feed.value,
                "issues": list(q.issues),
            }
            for q in sorted(report.quality.values(), key=lambda q: q.symbol)
        ],
    }


def render_json(report: SignalReport) -> str:
    return json.dumps(report_to_dict(report), indent=2, sort_keys=True, allow_nan=False) + "\n"


def _fmt(value: float | None, digits: int = 2) -> str:
    number = _number(value)
    return "-" if number is None else f"{number:.{digits}f}"


def _header_lines(report: SignalReport) -> list[str]:
    return [
        f"SEMI-EDGE signal report · {report.strategy} {report.strategy_version}",
        f"as of {report.as_of.isoformat()} · run {report.run_id} · mode {report.mode}",
        (
            f"data: {report.source} · feed {report.feed.value} · adjustment "
            f"{report.adjustment.value} · regime {report.regime_benchmark} "
            f"({'actionable' if report.regime_actionable else 'NOT ACTIONABLE'})"
        ),
        f"equity for proposed sizes: {report.equity_used:,.2f} ({report.equity_source})",
        f"config {report.config_fingerprint[:16]} · semiedge {report.semiedge_version}"
        + (f" · git {report.git_sha}" if report.git_sha else ""),
    ]


def render_table(report: SignalReport) -> str:
    """Plain-text table; the synthetic banner frames the whole report."""
    lines: list[str] = []
    if report.synthetic:
        lines += [f"*** {SYNTHETIC_BANNER} ***", ""]
    lines += _header_lines(report)
    lines.append("")
    eligible = report.eligible
    if not eligible:
        lines.append("No candidates: no symbol met every condition. No trades are proposed.")
    else:
        head = f"{'Rank':>4}  {'Symbol':<8}{'Score':>7}{'Close':>11}{'Stop':>11}{'Target':>11}"
        lines += [head + f"{'Shares':>8}  Binding", "-" * (len(head) + 17)]
        for e in eligible:
            size = report.sizes.get(e.symbol)
            lines.append(
                f"{e.rank or 0:>4}  {e.symbol:<8}{_fmt(e.score, 1):>7}{_fmt(e.close):>11}"
                f"{_fmt(e.proposed_stop):>11}{_fmt(e.proposed_target):>11}"
                f"{(size.shares if size else 0):>8}  {size.binding_constraint if size else '-'}"
            )
    lines += ["", "Conditions"]
    for e in report.evaluations:
        status = f"ELIGIBLE #{e.rank}" if e.eligible else "REJECTED"
        lines.append(f"  {e.symbol} [{status}] score {_fmt(e.score, 1)}")
        lines += [f"    {'PASS' if c.passed else 'FAIL'}  {c.description}" for c in e.conditions]
        lines += [f"    reason: {reason}" for reason in e.rejection_reasons]
    issues = [q for q in sorted(report.quality.values(), key=lambda q: q.symbol) if q.issues]
    if issues:
        lines += ["", "Data quality issues"]
        lines += [f"  {q.symbol}: {'; '.join(q.issues)}" for q in issues]
    lines += ["", DISCLAIMER]
    if report.synthetic:
        lines += ["", f"*** {SYNTHETIC_BANNER} ***"]
    return "\n".join(lines) + "\n"


def _md(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: SignalReport) -> str:
    lines: list[str] = []
    if report.synthetic:
        lines += [f"> **{SYNTHETIC_BANNER}**", ""]
    lines += [f"# {_md(_header_lines(report)[0])}", ""]
    lines += [f"- {_md(line)}" for line in _header_lines(report)[1:]]
    lines.append("")
    eligible = report.eligible
    if not eligible:
        lines.append("**No candidates:** no symbol met every condition. No trades are proposed.")
    else:
        lines += [
            "| Rank | Symbol | Score | Close | Stop | Target | Shares | Binding |",
            "|---:|---|---:|---:|---:|---:|---:|---|",
        ]
        for e in eligible:
            size = report.sizes.get(e.symbol)
            lines.append(
                f"| {e.rank} | {_md(e.symbol)} | {_fmt(e.score, 1)} | {_fmt(e.close)} | "
                f"{_fmt(e.proposed_stop)} | {_fmt(e.proposed_target)} | "
                f"{size.shares if size else 0} | {size.binding_constraint if size else '-'} |"
            )
    lines += ["", "## Conditions", ""]
    for e in report.evaluations:
        status = f"eligible #{e.rank}" if e.eligible else "rejected"
        lines += [f"### {_md(e.symbol)} ({status}, score {_fmt(e.score, 1)})", ""]
        lines += [
            "| Condition | Result | Value | Threshold | Detail |",
            "|---|---|---:|---:|---|",
        ]
        lines += [
            f"| {_md(c.name)} | {'pass' if c.passed else 'FAIL'} | {_fmt(c.value, 4)} | "
            f"{_fmt(c.threshold, 4)} | {_md(c.description)} |"
            for c in e.conditions
        ]
        if e.rejection_reasons:
            lines += ["", *[f"- {_md(reason)}" for reason in e.rejection_reasons]]
        lines.append("")
    lines += [f"_{_md(DISCLAIMER)}_"]
    if report.synthetic:
        lines += ["", f"> **{SYNTHETIC_BANNER}**"]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ persistence


def report_path(report: SignalReport, data_dir: Path) -> Path:
    """``{data_dir}/reports/signals/{as_of}_{run_id}.json``; synthetic under ``synthetic/``."""
    _check_run_metadata(report.run_id, report.generated_at)
    root = data_dir / "synthetic" if report.synthetic else data_dir
    return root / "reports" / "signals" / f"{report.as_of.isoformat()}_{report.run_id}.json"


def persist_report(report: SignalReport, data_dir: Path) -> Path:
    """Write the JSON report with exclusive creation (never overwrites); return its path."""
    path = report_path(report, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        output.write(render_json(report))
    return path
