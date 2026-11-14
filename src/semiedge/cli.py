"""``semiedge`` command line: configuration checks, data cache, signal scan, paper account.

Paper only. Nothing here places, changes or cancels orders; ``account`` only reads the PAPER
account through :func:`semiedge.safety.broker.create_paper_trading_client`.

Exit codes (docs/specifications/phase1-interfaces.md): 0 success (including "no candidates"),
2 configuration or safety error, 3 missing credentials, 4 data unavailable, untrustworthy or
stale for the regime benchmark (fail closed). Error messages never echo raw input or secrets.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import secrets
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import SecretStr, TypeAdapter, ValidationError

from semiedge import __version__
from semiedge.backtest.engine import (
    BacktestData,
    BacktestInputError,
    BacktestRequest,
    load_backtest_data,
    run_backtest,
)
from semiedge.backtest.metrics_context import read_context
from semiedge.backtest.metrics_report import metrics_report
from semiedge.backtest.results import BacktestResult
from semiedge.broker.account import BrokerError, fetch_account_snapshot
from semiedge.clock import SystemClock
from semiedge.config import (
    AppConfig,
    AppSettings,
    ConfigError,
    MissingCredentialsError,
    config_fingerprint,
    load_app_config,
)
from semiedge.config.loader import load_backtest_settings, load_earnings_settings
from semiedge.dashboard.launch import launch
from semiedge.data.alpaca_source import AlpacaMarketDataSource, MarketDataError
from semiedge.data.calendar import TradingCalendar
from semiedge.data.earnings import EarningsDataError
from semiedge.data.earnings_sources import EarningsProvider, EarningsProviderSettings
from semiedge.data.intraday import IntradayBarStore
from semiedge.data.schema import DataFeed, DataSource
from semiedge.data.source import MarketDataSource
from semiedge.data.store import ParquetBarStore
from semiedge.data.synthetic import SyntheticMarketDataSource
from semiedge.reporting import (
    ReportDataError,
    SeriesProvenance,
    SignalReport,
    build_signal_report,
    new_run_id,
    persist_report,
    render_json,
    render_markdown,
    render_table,
)
from semiedge.reporting.signal_report import EquitySource
from semiedge.risk.manager import RiskManager, UnavailableEarningsCalendar
from semiedge.safety.broker import create_paper_trading_client
from semiedge.safety.modes import SafetyViolation

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_CREDENTIALS = 3
EXIT_DATA = 4


def diagnostics(config_dir: Path, settings: AppSettings) -> dict[str, Any]:
    """Return only explicitly allowlisted non-secret configuration diagnostics."""
    config = load_app_config(config_dir)
    return {
        "schema_version": 1,
        "status": "configuration_valid",
        "offline": True,
        "execution_available": False,
        "orders_submitted": 0,
        "limitations": [
            "No market data has been fetched or validated.",
            "This diagnostic scans no signals; no execution, reconciliation or P&L engine exists.",
            "Configuration validation does not demonstrate trading readiness or profitability.",
        ],
        "configuration_fingerprint": config_fingerprint(config),
        "safety": {
            "mode": settings.trading_mode.value,
            "configured_order_permission": settings.order_submission_permitted,
            "live_trading_allowed": False,
        },
        "universe": {
            "members": [member.model_dump(mode="json") for member in config.universe.members],
            "tradable_symbols": list(config.universe.tradable_symbols),
            "benchmark_symbols": list(config.universe.benchmark_symbols),
            "regime_benchmark": config.universe.regime_benchmark,
        },
        "strategy": {
            "name": config.strategy.name,
            "version": config.strategy.version,
            "required_completed_sessions": config.strategy.required_history(),
        },
    }


def render_dashboard(report: dict[str, Any]) -> str:
    """Render escaped, script-free HTML from the same allowlisted diagnostic report."""
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{escape(str(member.get(key) or ''))}</td>"
            for key in ("symbol", "name", "group", "role")
        )
        + "</tr>"
        for member in report["universe"]["members"]
    )
    limitations = "".join(f"<li>{escape(str(item))}</li>" for item in report["limitations"])
    mode = escape(str(report["safety"]["mode"]))
    permission = escape(str(report["safety"]["configured_order_permission"]))
    history = escape(str(report["strategy"]["required_completed_sessions"]))
    version = escape(str(report["strategy"]["version"]))
    fingerprint = escape(str(report["configuration_fingerprint"]))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>SEMI-EDGE · Offline diagnostics</title>
<style>
body{{margin:0;background:#101722;color:#e7edf6;font:16px system-ui,sans-serif}}
main{{max-width:1050px;margin:auto;padding:40px 24px}}h1{{font-size:32px;margin-bottom:8px}}
p,li{{line-height:1.6}}.sub{{color:#a4b5cc}}.notice{{border-left:4px solid #e7ae54;
background:#262432;padding:16px 20px;border-radius:6px}}.cards{{display:flex;gap:16px;
flex-wrap:wrap;margin:28px 0}}.card{{flex:1;min-width:180px;background:#1b2738;padding:20px;
border-radius:10px}}.card strong{{display:block;font-size:22px;margin-top:10px}}
table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:12px;
border-bottom:1px solid #314058}}th{{color:#a4b5cc}}.table{{overflow-x:auto}}
code{{overflow-wrap:anywhere}}footer{{color:#a4b5cc;margin-top:28px;font-size:13px}}
</style></head><body><main>
<p class="sub">SEMI-EDGE / Foundation diagnostics</p><h1>Configuration, not trading readiness</h1>
<p class="sub">Offline snapshot · no market data connection · no order controls</p>
<section class="notice"><strong>Trading engine unavailable</strong><ul>{limitations}</ul></section>
<section class="cards"><div class="card">Configured mode<strong>{mode}</strong></div>
<div class="card">Live trading<strong>Prohibited</strong></div>
<div class="card">Execution available<strong>No</strong></div></section>
<h2>Strategy configuration</h2><p>Version {version}; requires {history} completed sessions.
Configured paper-order permission: {permission}. This diagnostic tool cannot submit orders.</p>
<h2>Configured stock universe</h2><p class="sub">Membership is configuration only;
tradability, prices, earnings and current data availability have not been verified.</p>
<div class="table"><table><thead><tr><th>Symbol</th><th>Name</th><th>Group</th><th>Role</th>
</tr></thead><tbody>{rows}</tbody></table></div>
<footer>Configuration fingerprint: <code>{fingerprint}</code><br>
No equity, return or P&amp;L estimates are available.</footer></main></body></html>
"""


# ------------------------------------------------------------------ commands

_CONFIG_FAILED = "Configuration validation failed; check local settings and YAML files."
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # compact and week-date ISO forms refused


class CommandError(Exception):
    """A failure with a fixed exit code and a message that is safe to print."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def utcnow() -> datetime:
    """The current UTC time (a seam for tests)."""
    return datetime.now(UTC)


def git_sha() -> str | None:
    """Short SHA of the checkout this package runs from, if it is a git checkout."""
    try:
        result = subprocess.run(  # fixed argument list, no shell, no user input
            ["git", "rev-parse", "--short=12", "HEAD"],  # noqa: S607 - git resolved from PATH
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = result.stdout.strip()
    if result.returncode != 0 or not sha or any(c not in "0123456789abcdef" for c in sha):
        return None
    return sha


def market_data_source(settings: AppSettings, config: AppConfig) -> MarketDataSource:
    """The Alpaca historical-data source (read-only market data; never a trading client)."""
    key, secret = settings.alpaca_credentials()
    return AlpacaMarketDataSource.from_credentials(
        key, secret, config.data.daily_feed, config.data.adjustment
    )


def paper_account_client(settings: AppSettings) -> Any:
    """A PAPER trading client from the only allowed factory (used for reads only)."""
    key, secret = settings.alpaca_credentials()
    return create_paper_trading_client(key, secret)


def _iso_date(text: str) -> date:
    if _ISO_DATE.fullmatch(text) is None:
        raise argparse.ArgumentTypeError("expected a date as YYYY-MM-DD")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a date as YYYY-MM-DD") from None


def _positive_amount(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a positive number") from None
    if not (value > 0 and math.isfinite(value)):  # also rejects nan
        raise argparse.ArgumentTypeError("expected a positive, finite number")
    return value


def _load(args: argparse.Namespace) -> tuple[AppSettings, AppConfig, str]:
    try:
        settings = AppSettings()
        config = load_app_config(args.config_dir or settings.config_dir)
    except (ConfigError, ValidationError, SafetyViolation, OSError):
        # Validation errors may contain raw input, including accidentally pasted credentials.
        raise CommandError(EXIT_CONFIG, _CONFIG_FAILED) from None
    return settings, config, config_fingerprint(config)


def _last_completed(calendar: TradingCalendar, config: AppConfig, now: datetime) -> date:
    return calendar.last_completed_session(now, config.data.bar_settle_minutes)


def _completed_session(
    calendar: TradingCalendar, config: AppConfig, requested: date | None, now: datetime, flag: str
) -> date:
    """``requested`` if it is a session that has completed by ``now``; else the latest one."""
    last = _last_completed(calendar, config, now)
    if requested is None:
        return last
    if requested > last:
        raise CommandError(
            EXIT_CONFIG, f"{flag} must not be after the last completed session {last.isoformat()}"
        )
    try:
        is_session = calendar.is_session(requested)
    except Exception:  # outside the calendar's bounds
        is_session = False
    if not is_session:
        raise CommandError(EXIT_CONFIG, f"{flag} must be an exchange session (XNYS)")
    return requested


def _write_new(path: Path, text: str) -> None:
    try:
        # Exclusive creation prevents accidentally overwriting configuration or user files.
        with path.open("x", encoding="utf-8") as output:
            output.write(text)
    except OSError:
        raise CommandError(
            EXIT_CONFIG, "Cannot create the output file; use a new path in an existing directory."
        ) from None


def _print_json(payload: object) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n")


def _cmd_diagnostics(args: argparse.Namespace) -> int:
    try:
        settings = AppSettings()
        report = diagnostics(args.config_dir or settings.config_dir, settings)
    except (ConfigError, ValidationError, SafetyViolation, OSError):
        raise CommandError(EXIT_CONFIG, _CONFIG_FAILED) from None
    if args.html:
        try:
            with args.html.open("x", encoding="utf-8") as output:
                output.write(render_dashboard(report))
        except OSError:
            raise CommandError(
                EXIT_CONFIG, "Cannot create dashboard; use a new path in an existing directory."
            ) from None
    _print_json(report)
    return EXIT_OK


def _cmd_version(args: argparse.Namespace) -> int:
    sha = git_sha()
    sys.stdout.write(f"semiedge {__version__}" + (f" (git {sha})" if sha else "") + "\n")
    return EXIT_OK


def _cmd_config_check(args: argparse.Namespace) -> int:
    settings, _, _ = _load(args)
    try:
        report = diagnostics(args.config_dir or settings.config_dir, settings)
    except (ConfigError, ValidationError, SafetyViolation, OSError):
        raise CommandError(EXIT_CONFIG, _CONFIG_FAILED) from None
    report["alpaca_paper_credentials_configured"] = settings.has_alpaca_credentials
    _print_json(report)
    return EXIT_OK


def _cmd_data_fetch(args: argparse.Namespace) -> int:
    settings, config, _ = _load(args)
    universe = config.universe.all_symbols
    symbols = list(dict.fromkeys(args.symbols or universe))
    unknown = [s for s in symbols if s not in universe]
    if unknown:
        raise CommandError(
            EXIT_CONFIG, f"{len(unknown)} requested symbol(s) are not in the configured universe"
        )
    calendar = TradingCalendar(config.data.calendar)
    now = utcnow()
    end = _completed_session(calendar, config, args.end, now, "--end")
    start = args.start or config.data.history_start
    if start > end:
        raise CommandError(EXIT_CONFIG, "--start must not be after --end")
    try:
        source = market_data_source(settings, config)
    except (MissingCredentialsError, SafetyViolation):
        raise
    except Exception as exc:  # sanitised: SDK constructor errors may carry key material
        raise CommandError(
            EXIT_DATA, f"Cannot create the market data client ({type(exc).__name__})."
        ) from None
    try:
        frames = source.get_daily_bars(symbols, start, end)
    except MarketDataError:
        raise CommandError(EXIT_DATA, "Market data request failed; nothing was stored.") from None
    except Exception as exc:  # sanitised: vendor errors may carry URLs or response bodies
        raise CommandError(
            EXIT_DATA, f"Market data request failed ({type(exc).__name__}); nothing was stored."
        ) from None
    for symbol in symbols:  # validate every frame before writing any, so a refusal stores nothing
        frame = frames.get(symbol)
        if frame is not None and len(frame) and frame.index[-1].date() > end:
            raise CommandError(
                EXIT_DATA, f"Refused {symbol}: bars after --end {end}; nothing was stored."
            )
    store = ParquetBarStore(settings.data_dir)
    stored: dict[str, dict[str, object]] = {}
    for symbol in symbols:
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            stored[symbol] = {"rows": 0, "first_session": None, "last_session": None}
            continue
        try:
            store.write(
                symbol,
                frame,
                feed=source.feed,
                adjustment=source.adjustment,
                source=source.source,
                fetched_at=now,
            )
        except ValueError as exc:  # includes DataSchemaError; messages name symbol and dates
            raise CommandError(EXIT_DATA, f"Refused to store {symbol}: {exc}") from None
        stored[symbol] = {
            "rows": len(frame),
            "first_session": frame.index[0].date().isoformat(),
            "last_session": frame.index[-1].date().isoformat(),
        }
    _print_json(
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "feed": source.feed.value,
            "adjustment": source.adjustment.value,
            "fetched_at": now.isoformat(),
            "symbols": stored,
        }
    )
    missing = [s for s, info in stored.items() if info["rows"] == 0]
    if missing:
        sys.stderr.write(f"No bars returned for: {', '.join(missing)}\n")
        return EXIT_DATA
    return EXIT_OK


def _utc_iso(value: object) -> str:
    stamp = pd.Timestamp(value)  # type: ignore[arg-type]
    if stamp.tzinfo is None:  # the store only holds tz-aware fetch times
        raise CommandError(EXIT_DATA, "The bar cache holds a fetch time without a time zone.")
    return stamp.tz_convert("UTC").isoformat()


def _cmd_data_status(args: argparse.Namespace) -> int:
    settings, config, _ = _load(args)
    calendar = TradingCalendar(config.data.calendar)
    last = _last_completed(calendar, config, utcnow())
    try:
        summary = ParquetBarStore(settings.data_dir).summary()
    except Exception as exc:  # a tampered or unreadable cache file fails the whole status
        raise CommandError(
            EXIT_DATA, f"The bar cache failed validation ({type(exc).__name__})."
        ) from None
    series = []
    for row in summary.to_dict("records"):
        last_session = pd.Timestamp(row["last_session"]).date()
        series.append(
            {
                "symbol": str(row["symbol"]),
                "feed": str(row["feed"]),
                "adjustment": str(row["adjustment"]),
                "source": str(row["source"]),
                "first_session": pd.Timestamp(row["first_session"]).date().isoformat(),
                "last_session": last_session.isoformat(),
                "rows": int(row["rows"]),
                "last_fetched_at": _utc_iso(row["last_fetched_at"]),
                "fresh": last_session == last,
            }
        )
    try:
        intraday = IntradayBarStore(settings.data_dir).summary(
            feed=config.data.daily_feed, adjustment=config.data.adjustment
        )
    except Exception as exc:  # a tampered or unreadable cache file fails the whole status
        raise CommandError(
            EXIT_DATA, f"The 5-minute cache failed validation ({type(exc).__name__})."
        ) from None
    _print_json(
        {"last_completed_session": last.isoformat(), "series": series, "intraday_5min": intraday}
    )
    return EXIT_OK


def _read_intraday_caches(
    data_dir: Path,
    config: AppConfig,
    symbols: tuple[str, ...],
    *,
    daily_symbols: tuple[str, ...] | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """Read the cached 5-minute and daily frames; a corrupt file is an error, never skipped."""
    feed, adjustment = config.data.daily_feed, config.data.adjustment
    intraday_store = IntradayBarStore(data_dir)
    daily_store = ParquetBarStore(data_dir)
    intraday: dict[str, pd.DataFrame] = {}
    daily: dict[str, pd.DataFrame] = {}
    try:
        for symbol in symbols:
            series = intraday_store.read(symbol, feed=feed, adjustment=adjustment)
            if series is not None:
                intraday[symbol] = series.frame
        for symbol in symbols if daily_symbols is None else daily_symbols:
            stored = daily_store.read(symbol, feed=feed, adjustment=adjustment)
            if stored is not None:
                daily[symbol] = stored.frame
    except (OSError, ValueError) as exc:
        raise CommandError(EXIT_DATA, f"Cache rejected: {type(exc).__name__}") from None
    return intraday, daily


def _intraday_summary_lines(
    args: argparse.Namespace, sessions: int, labels: list[str], runs: list[Any]
) -> list[str]:
    lines = [
        f"intraday_v1 backtest {args.start}..{args.end}, {sessions} sessions, "
        f"start equity {args.equity:,.0f}, profile {args.risk_profile or 'default'}",
        *labels,
        "",
        f"{'account':<13}{'slip':>5}{'trades':>8}{'win%':>7}{'exp R':>8}{'net $':>11}"
        f"{'net %':>8}{'maxDD %':>9}{'w/o best5':>11}   A / B / C net $",
    ]
    for r in runs:
        s_all = r.summary()
        a = s_all["all"]
        per = " / ".join(
            f"{s_all[x].get('net_pnl', 0):,.0f}({s_all[x].get('trades', 0)})"
            for x in ("A", "B", "C")
        )
        lines.append(
            f"{r.account:<13}{r.slippage_bps:>5.0f}{a.get('trades', 0):>8}"
            f"{a.get('win_rate', 0) * 100:>7.1f}{a.get('expectancy_r', 0):>8.3f}"
            f"{a.get('net_pnl', 0):>11,.0f}{a.get('net_return_pct', 0):>8.2f}"
            f"{s_all['max_drawdown_pct']:>9.2f}"
            f"{s_all['net_without_best_5_sessions']:>11,.0f}   {per}"
        )
    return lines


def _risk_limits(args: argparse.Namespace, config_dir: Path) -> Any:
    from semiedge.config.loader import load_risk_profile  # noqa: PLC0415

    try:
        profile = None if args.risk_profile in (None, "default") else args.risk_profile
        return load_risk_profile(config_dir, profile)
    except ConfigError as exc:
        raise CommandError(EXIT_CONFIG, f"Risk profile rejected: {exc}") from None


def _dip_report(args: argparse.Namespace, panel: Any, limits: Any) -> dict[str, Any]:
    from semiedge.backtest import dip  # noqa: PLC0415

    start, end = dip.DEVELOPMENT if args.period == "development" else dip.HOLDOUT
    variants = list(dip.VARIANTS) if args.variant is None else [args.variant]
    study: dict[str, dict[str, Any]] = {}
    for slip in dip.SLIPPAGES:
        for v in (*variants, "baseline"):
            trades = dip.signal_study(panel, v, start=start, end=end, slippage_bps=slip)
            study[f"{v}@{slip:g}"] = dip.study_stats(trades)
    at20 = {v: study[f"{v}@20"] for v in (*variants, "baseline")}
    runs = [
        dip.replay(
            panel,
            variant=v,
            start=start,
            end=end,
            start_equity=args.equity,
            limits=limits,
            account=a,
            slippage_bps=slip,
        )
        for v in variants
        for a in dip.ACCOUNT_MODELS
        for slip in dip.SLIPPAGES
    ]
    report: dict[str, Any] = {"study": study}
    if args.period == "development":
        chosen, notes = dip.select_variant(at20)
        report["selection"] = {"selected": chosen, "notes": notes}
    else:
        net = next(
            r.summary()["net_pnl"]
            for r in runs
            if r.account == "unrestricted" and r.slippage_bps == dip.SELECTION_SLIPPAGE
        )
        failed = dip.holdout_pass(at20, args.variant, net)
        report["holdout"] = {"variant": args.variant, "passed": not failed, "failed": failed}
    report["runs"] = [r.to_dict() for r in runs]
    report["smh_buy_and_hold_pct"] = dip.smh_buy_and_hold(panel, start, end)
    report["window"] = [start.isoformat(), end.isoformat()]
    return report


_DIP_LABELS = [
    "SURVIVORSHIP-BIASED UNIVERSE: current hand-picked list, not point-in-time",
    "EARNINGS BLACKOUT NOT APPLIED: no point-in-time earnings history",
    "PRICE RETURNS ONLY: dividends excluded",
    "DAILY BARS: an intraday stop fill assumes the stop price traded",
]


def _cmd_backtest_dip(args: argparse.Namespace) -> int:
    """dip_v1 on cached daily bars: signal study and $5k portfolio runs (#133)."""
    from semiedge.backtest import dip  # noqa: PLC0415

    settings, config, _ = _load(args)
    limits = _risk_limits(args, args.config_dir or settings.config_dir)
    if not (args.equity > 0 and math.isfinite(args.equity)):
        raise CommandError(EXIT_CONFIG, "--equity must be a positive number")
    if args.period == "holdout" and args.variant is None:
        raise CommandError(EXIT_CONFIG, "the holdout runs once, with the selected --variant")
    if args.output is not None and args.output.exists():
        raise CommandError(EXIT_DATA, "Backtest failed: output_path_exists.")
    tradable = config.universe.tradable_symbols
    benchmark = config.universe.regime_benchmark
    _, end = dip.DEVELOPMENT if args.period == "development" else dip.HOLDOUT
    _, daily = _read_intraday_caches(
        settings.data_dir, config, (), daily_symbols=(*tradable, benchmark)
    )
    if benchmark not in daily:
        raise CommandError(EXIT_DATA, f"No cached daily bars for {benchmark}.")
    panel = dip.prepare_panel(daily, tradable=tradable, benchmark=benchmark, end=end)
    body = _dip_report(args, panel, limits)
    command = (
        f"semiedge backtest-dip --period {args.period}"
        + (f" --variant {args.variant}" if args.variant else "")
        + f" --equity {args.equity:g} --risk-profile {args.risk_profile or 'default'}"
    )
    holdout_label = ["HOLDOUT RUN: run once with the pre-selected variant"]
    report = {
        "strategy": "dip_v1",
        "period": args.period,
        "command": command,
        "start_equity": args.equity,
        "risk_profile": args.risk_profile or "default",
        "limits": limits.model_dump(mode="json"),
        "labels": [*_DIP_LABELS, *(holdout_label if args.period == "holdout" else [])],
        **body,
    }
    if args.output is not None:
        args.output.mkdir(parents=True, exist_ok=False)
        path = args.output / "dip_backtest.json"
        path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    sys.stdout.write("\n".join(_dip_summary_lines(report)) + "\n")
    return EXIT_OK


def _dip_summary_lines(report: dict[str, Any]) -> list[str]:
    w = report["window"]
    lines = [
        f"{report.get('strategy_title', 'dip_v1')} {report['period']} {w[0]}..{w[1]}, start equity "
        f"{report['start_equity']:,.0f}, profile {report['risk_profile']}",
        *report["labels"],
        "",
        "Signal study (one-share trades, no limits):",
        f"{'variant@bps':<16}{'trades':>8}{'win%':>7}{'mean R':>9}{'95% CI':>20}",
    ]
    for key, s in report["study"].items():
        ci = s.get("mean_r_95ci", ["", ""])
        trimmed = s.get("mean_r_without_top5")
        lines.append(
            f"{key:<16}{s.get('trades', 0):>8}{s.get('win_rate', 0) * 100:>7.1f}"
            f"{s.get('mean_r', 0):>9.3f}{ci!s:>20}"
            + (
                f"   median {s.get('median_r', 0):+.3f}, w/o top5 {trimmed:+.3f}"
                if trimmed is not None
                else ""
            )
        )
    if "selection" in report:
        sel = report["selection"]
        lines += ["", f"Selection (20 bps): {sel['selected'] or 'none qualifies'}", *sel["notes"]]
    if "holdout" in report:
        h = report["holdout"]
        lines += ["", f"Holdout pass test: {'PASS' if h['passed'] else 'FAIL'}", *h["failed"]]
    lines += [
        "",
        f"Portfolio runs (SMH buy-and-hold {report['smh_buy_and_hold_pct']}%):",
        f"{'variant':<9}{'account':<13}{'slip':>5}{'trades':>8}{'win%':>7}{'mean R':>8}"
        f"{'net $':>10}{'net %':>8}{'maxDD %':>9}  halted",
    ]
    for r in report["runs"]:
        s = r["summary"]
        lines.append(
            f"{r['variant']:<9}{r['account']:<13}{r['slippage_bps']:>5.0f}{s['trades']:>8}"
            f"{s.get('win_rate', 0) * 100:>7.1f}{s.get('mean_r', 0):>8.3f}"
            f"{s['net_pnl']:>10,.0f}{s['net_return_pct']:>8.2f}"
            f"{s['max_drawdown_pct']:>9.2f}  {s['drawdown_halt_on'] or '-'}"
        )
    lines += ["", f"Command: {report['command']}"]
    return lines


def _idip_report(
    args: argparse.Namespace, sessions: Any, limits: Any, intraday: Any, benchmark: str
) -> dict[str, Any]:
    from semiedge.backtest import intraday_dip as idip  # noqa: PLC0415

    start, end = idip.DEVELOPMENT if args.period == "development" else idip.HOLDOUT
    variants = list(idip.VARIANTS) if args.variant is None else [args.variant]
    study: dict[str, dict[str, Any]] = {}
    for slip in idip.SLIPPAGES:
        for v in (*variants, "baseline"):
            trades = idip.signal_study(sessions, v, slippage_bps=slip)
            study[f"{v}@{slip:g}"] = idip.study_stats(trades)
    at20 = {v: study[f"{v}@20"] for v in (*variants, "baseline")}
    runs = [
        idip.replay(
            sessions,
            variant=v,
            start_equity=args.equity,
            limits=limits,
            account=a,
            slippage_bps=slip,
        )
        for v in variants
        for a in idip.ACCOUNT_MODELS
        for slip in idip.SLIPPAGES
    ]
    report: dict[str, Any] = {"study": study}
    if args.period == "development":
        chosen, notes = idip.select_variant(at20)
        report["selection"] = {"selected": chosen, "notes": notes}
    else:
        net = next(
            r.summary()["net_pnl"]
            for r in runs
            if r.account == "unrestricted" and r.slippage_bps == idip.SELECTION_SLIPPAGE
        )
        failed = idip.holdout_pass(at20, args.variant, net)
        report["holdout"] = {"variant": args.variant, "passed": not failed, "failed": failed}
    report["runs"] = [r.to_dict() for r in runs]
    report["smh_buy_and_hold_pct"] = idip.smh_change_pct(intraday, benchmark, start, end)
    report["window"] = [start.isoformat(), end.isoformat()]
    return report


def _cmd_backtest_intraday_dip(args: argparse.Namespace) -> int:
    """intraday_dip_v1 on cached 5-minute bars: signal study and $5k runs (#137)."""
    from semiedge.backtest import intraday_dip as idip  # noqa: PLC0415

    settings, config, _ = _load(args)
    limits = _risk_limits(args, args.config_dir or settings.config_dir)
    if not (args.equity > 0 and math.isfinite(args.equity)):
        raise CommandError(EXIT_CONFIG, "--equity must be a positive number")
    if args.period == "holdout" and args.variant is None:
        raise CommandError(EXIT_CONFIG, "the holdout runs once, with the selected --variant")
    if args.output is not None and args.output.exists():
        raise CommandError(EXIT_DATA, "Backtest failed: output_path_exists.")
    tradable = config.universe.tradable_symbols
    benchmark = config.universe.regime_benchmark
    start, end = idip.DEVELOPMENT if args.period == "development" else idip.HOLDOUT
    intraday, daily = _read_intraday_caches(settings.data_dir, config, (*tradable, benchmark))
    if benchmark not in intraday:
        raise CommandError(EXIT_DATA, f"No cached 5-minute bars for {benchmark}.")
    sessions = idip.prepare_sessions(
        intraday,
        daily,
        tradable=tradable,
        benchmark=benchmark,
        start=start,
        end=end,
        calendar=TradingCalendar(config.data.calendar),
    )
    body = _idip_report(args, sessions, limits, intraday, benchmark)
    command = (
        f"semiedge backtest-intraday-dip --period {args.period}"
        + (f" --variant {args.variant}" if args.variant else "")
        + f" --equity {args.equity:g} --risk-profile {args.risk_profile or 'default'}"
    )
    labels = [
        "SURVIVORSHIP-BIASED UNIVERSE: current hand-picked list, not point-in-time",
        "EARNINGS BLACKOUT NOT APPLIED: no point-in-time earnings history",
        "5-MINUTE SIP BARS: fills at the next bar's open plus slippage; flat at 15:55",
        *(
            ["HOLDOUT RUN: run once with the pre-selected variant"]
            if args.period == "holdout"
            else []
        ),
    ]
    report = {
        "strategy": "intraday_dip_v1",
        "period": args.period,
        "command": command,
        "start_equity": args.equity,
        "risk_profile": args.risk_profile or "default",
        "limits": limits.model_dump(mode="json"),
        "labels": labels,
        **body,
    }
    if args.output is not None:
        args.output.mkdir(parents=True, exist_ok=False)
        path = args.output / "intraday_dip_backtest.json"
        path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    report["strategy_title"] = "intraday_dip_v1"
    sys.stdout.write("\n".join(_dip_summary_lines(report)) + "\n")
    return EXIT_OK


def _cmd_backtest_intraday(args: argparse.Namespace) -> int:
    """Replay intraday_v1 on cached 5-minute bars: 3 account models x 3 slippages (#130)."""
    from semiedge.backtest.intraday import prepare_sessions, run_matrix  # noqa: PLC0415
    from semiedge.config.loader import load_risk_profile  # noqa: PLC0415

    settings, config, _ = _load(args)
    config_dir = args.config_dir or settings.config_dir
    try:
        profile = None if args.risk_profile in (None, "default") else args.risk_profile
        limits = load_risk_profile(config_dir, profile)
    except ConfigError as exc:
        raise CommandError(EXIT_CONFIG, f"Risk profile rejected: {exc}") from None
    if not (args.equity > 0 and math.isfinite(args.equity)):
        raise CommandError(EXIT_CONFIG, "--equity must be a positive number")
    if args.start > args.end:
        raise CommandError(EXIT_CONFIG, "--start must not be after --end")
    if args.output is not None and args.output.exists():
        raise CommandError(EXIT_DATA, "Backtest failed: output_path_exists.")
    calendar = TradingCalendar(config.data.calendar)
    tradable = config.universe.tradable_symbols
    benchmark = config.universe.regime_benchmark
    intraday, daily = _read_intraday_caches(settings.data_dir, config, (*tradable, benchmark))
    if benchmark not in intraday:
        raise CommandError(EXIT_DATA, f"No cached 5-minute bars for {benchmark}.")
    prepared = prepare_sessions(
        intraday,
        daily,
        tradable=tradable,
        benchmark=benchmark,
        start=args.start,
        end=args.end,
        calendar=calendar,
    )
    if not prepared:
        raise CommandError(EXIT_DATA, "No sessions in the requested window.")
    runs = run_matrix(prepared, start_equity=args.equity, limits=limits)
    gaps = sum(len(p.gap_symbols) for p in prepared)
    command = (
        f"semiedge backtest-intraday --start {args.start} --end {args.end} "
        f"--equity {args.equity:g} --risk-profile {args.risk_profile or 'default'}"
    )
    report = {
        "strategy": "intraday_v1",
        "command": command,
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "sessions": len(prepared),
        "start_equity": args.equity,
        "risk_profile": args.risk_profile or "default",
        "limits": limits.model_dump(mode="json"),
        "labels": [
            "SURVIVORSHIP-BIASED UNIVERSE: current hand-picked list, not point-in-time",
            "EARNINGS BLACKOUT NOT APPLIED: no point-in-time earnings history",
            f"DATA GAPS: {gaps} symbol-sessions with a missing 5-minute bar "
            "(no trades after a gap)",
            "5-MINUTE SIP BARS, split-adjusted; fills at the next bar's open plus slippage",
        ],
        "runs": [r.to_dict() for r in runs],
    }
    path = args.output / "intraday_backtest.json" if args.output else None
    if path is not None:
        args.output.mkdir(parents=True, exist_ok=False)
        path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    lines = _intraday_summary_lines(args, len(prepared), report["labels"], runs)
    lines += ["", f"Command: {command}"]
    if path is not None:
        lines.append(f"Report: {path}")
    sys.stdout.write("\n".join(lines) + "\n")
    return EXIT_OK


def _cmd_data_fetch_intraday(args: argparse.Namespace) -> int:
    """Fetch completed 5-minute bars and replace each symbol's intraday file (#118)."""
    settings, config, _ = _load(args)
    universe = config.universe.all_symbols
    symbols = list(dict.fromkeys(args.symbols or universe))
    unknown = [s for s in symbols if s not in universe]
    if unknown:
        raise CommandError(
            EXIT_CONFIG, f"{len(unknown)} requested symbol(s) are not in the configured universe"
        )
    calendar = TradingCalendar(config.data.calendar)
    now = utcnow()
    end = _completed_session(calendar, config, args.end, now, "--end")
    if args.start > end:
        raise CommandError(EXIT_CONFIG, "--start must not be after --end")
    try:
        source = market_data_source(settings, config)
    except (MissingCredentialsError, SafetyViolation):
        raise
    except Exception as exc:  # sanitised: SDK constructor errors may carry key material
        raise CommandError(
            EXIT_DATA, f"Cannot create the market data client ({type(exc).__name__})."
        ) from None
    if not isinstance(source, AlpacaMarketDataSource):
        raise CommandError(EXIT_CONFIG, "5-minute bars need the Alpaca data source.")
    try:
        frames = source.get_intraday_bars(symbols, args.start, end)
    except MarketDataError:
        raise CommandError(EXIT_DATA, "Market data request failed; nothing was stored.") from None
    except Exception as exc:  # sanitised: vendor errors may carry URLs or response bodies
        raise CommandError(
            EXIT_DATA, f"Market data request failed ({type(exc).__name__}); nothing was stored."
        ) from None
    store = IntradayBarStore(settings.data_dir)
    stored: dict[str, dict[str, object]] = {}
    for symbol in symbols:
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            stored[symbol] = {"rows": 0, "first_bar": None, "last_bar": None}
            continue
        try:
            store.replace(
                symbol,
                frame,
                feed=source.feed,
                adjustment=source.adjustment,
                source=source.source,
                fetched_at=now,
                calendar=calendar,
            )
        except ValueError as exc:  # includes DataSchemaError; messages name symbol and times
            raise CommandError(EXIT_DATA, f"Refused to store {symbol}: {exc}") from None
        stored[symbol] = {
            "rows": len(frame),
            "first_bar": frame.index[0].isoformat(),
            "last_bar": frame.index[-1].isoformat(),
        }
    _print_json(
        {
            "start": args.start.isoformat(),
            "end": end.isoformat(),
            "bar_minutes": 5,
            "feed": source.feed.value,
            "adjustment": source.adjustment.value,
            "fetched_at": now.isoformat(),
            "symbols": stored,
        }
    )
    return EXIT_OK


def _synthetic_bars(
    symbols: Sequence[str], calendar: TradingCalendar, as_of: date, config: AppConfig
) -> tuple[dict[str, pd.DataFrame], list[SeriesProvenance]]:
    source = SyntheticMarketDataSource(calendar=calendar)
    start = calendar.sessions_window(as_of, config.strategy.required_history())[0]
    frames = source.get_daily_bars(symbols, start, as_of)
    provenance = [
        SeriesProvenance(
            symbol=symbol,
            source=source.source,
            feed=source.feed,
            adjustment=source.adjustment,
            fetched_at=None,
            first_session=frame.index[0].date() if len(frame) else None,
            last_session=frame.index[-1].date() if len(frame) else None,
            rows=len(frame),
        )
        for symbol, frame in frames.items()
    ]
    return frames, provenance


def _cached_bars(
    data_dir: Path, symbols: Sequence[str], config: AppConfig
) -> tuple[dict[str, pd.DataFrame], list[SeriesProvenance]]:
    store = ParquetBarStore(data_dir)
    bars: dict[str, pd.DataFrame] = {}
    provenance: list[SeriesProvenance] = []
    for symbol in symbols:
        try:
            series = store.read(
                symbol, feed=config.data.daily_feed, adjustment=config.data.adjustment
            )
        except Exception as exc:  # tampered, mixed-provenance or unreadable file
            raise CommandError(
                EXIT_DATA, f"Cached bars for {symbol} failed validation ({type(exc).__name__})."
            ) from None
        if series is None:
            continue
        if series.source == DataSource.SYNTHETIC or series.feed == DataFeed.SYNTHETIC:
            raise CommandError(EXIT_DATA, f"Cached bars for {symbol} are synthetic.")
        bars[symbol] = series.frame
        provenance.append(
            SeriesProvenance(
                symbol=symbol,
                source=series.source,
                feed=series.feed,
                adjustment=series.adjustment,
                fetched_at=series.fetched_at,
                first_session=series.frame.index[0].date(),
                last_session=series.frame.index[-1].date(),
                rows=len(series.frame),
            )
        )
    return bars, provenance


_RENDERERS: dict[str, Callable[[SignalReport], str]] = {
    "table": render_table,
    "markdown": render_markdown,
    "json": render_json,
}


def _cmd_scan(args: argparse.Namespace) -> int:
    settings, config, fingerprint = _load(args)
    calendar = TradingCalendar(config.data.calendar)
    now = utcnow()
    as_of = _completed_session(calendar, config, args.as_of, now, "--as-of")
    required = config.strategy.required_history()
    try:
        calendar.sessions_window(as_of, required)
    except ValueError:  # the window would start before the calendar's first session
        raise CommandError(
            EXIT_CONFIG, f"--as-of needs at least {required} sessions of calendar history"
        ) from None
    symbols = (*config.universe.tradable_symbols, config.universe.regime_benchmark)
    if args.source == "synthetic":
        bars, provenance = _synthetic_bars(symbols, calendar, as_of, config)
        feed, adjustment = SyntheticMarketDataSource.feed, SyntheticMarketDataSource.adjustment
    else:
        bars, provenance = _cached_bars(settings.data_dir, symbols, config)
        feed, adjustment = config.data.daily_feed, config.data.adjustment
    equity_source: EquitySource
    if args.equity is None:
        equity, equity_source = config.scanner.reference_equity, "reference"
    else:
        equity, equity_source = args.equity, "cli"
    try:
        report = build_signal_report(
            bars,
            provenance,
            config=config,
            config_fingerprint=fingerprint,
            as_of=as_of,
            source=args.source,
            feed=feed,
            adjustment=adjustment,
            equity=equity,
            equity_source=equity_source,
            mode=settings.trading_mode.value,
            calendar=calendar,
            run_id=new_run_id(now, secrets.token_hex(4)),
            generated_at=now,
            semiedge_version=__version__,
            git_sha=git_sha(),
        )
    except ReportDataError as exc:
        raise CommandError(EXIT_DATA, f"Cannot build a trustworthy report: {exc}") from None
    rendered = _RENDERERS[args.format](report)
    if args.output:  # claimed first, so a taken path never leaves an orphan persisted report
        _write_new(args.output, rendered)
    try:
        saved = persist_report(report, settings.data_dir)
    except OSError:
        if args.output:
            args.output.unlink(missing_ok=True)
        raise CommandError(
            EXIT_CONFIG, "Cannot persist the report under the configured data directory."
        ) from None
    sys.stdout.write(rendered)
    sys.stderr.write(f"Report saved: {saved}\n")
    if not report.regime_actionable:
        sys.stderr.write(
            f"Regime benchmark {report.regime_benchmark} data is unavailable or stale at "
            f"{as_of.isoformat()}; no symbol is actionable.\n"
        )
        return EXIT_DATA
    return EXIT_OK


def _cmd_account(args: argparse.Namespace) -> int:
    settings, _, _ = _load(args)
    try:
        client = paper_account_client(settings)
    except (MissingCredentialsError, SafetyViolation):
        raise  # exit 3 / exit 2, mapped by main
    except Exception as exc:  # sanitised (F-030): SDK constructor errors may carry key material
        raise CommandError(
            EXIT_DATA, f"Cannot create the paper trading client ({type(exc).__name__})."
        ) from None
    try:
        snapshot = fetch_account_snapshot(client)
    except BrokerError as exc:  # messages are sanitised by semiedge.broker.account
        raise CommandError(EXIT_DATA, str(exc)) from None
    _print_json(
        {
            "paper": True,
            "orders_submitted": 0,
            "account_number": snapshot.account_number_masked,
            "status": snapshot.status,
            "currency": snapshot.currency,
            "equity": snapshot.equity,
            "last_equity": snapshot.last_equity,
            "cash": snapshot.cash,
            "buying_power": snapshot.buying_power,
            "long_market_value": snapshot.long_market_value,
            "pattern_day_trader": snapshot.pattern_day_trader,
            "is_tradable": snapshot.is_tradable,
            "block_reasons": [reason.value for reason in snapshot.block_reasons],
            "market_is_open": snapshot.market_is_open,
            "next_open": snapshot.next_open.isoformat(),
            "next_close": snapshot.next_close.isoformat(),
            "as_of": snapshot.as_of.isoformat(),
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "side": p.side,
                    "avg_entry_price": p.avg_entry_price,
                    "market_value": p.market_value,
                    "unrealized_pl": p.unrealized_pl,
                }
                for p in snapshot.positions
            ],
        }
    )
    return EXIT_OK


def _cmd_backtest(args: argparse.Namespace) -> int:
    settings, config, _ = _load(args)
    try:
        backtest = load_backtest_settings(args.config_dir or settings.config_dir)
    except (ConfigError, OSError):
        raise CommandError(EXIT_CONFIG, _CONFIG_FAILED) from None
    calendar = TradingCalendar(config.data.calendar)
    try:
        valid_window = args.start <= args.end and all(
            calendar.is_session(d) for d in (args.start, args.end)
        )
    except ValueError:
        valid_window = False
    if not valid_window:
        raise CommandError(EXIT_CONFIG, "Backtest failed: invalid_window.")
    try:
        if args.output is not None and (args.output.exists() or args.output.is_symlink()):
            raise CommandError(EXIT_DATA, "Backtest failed: output_path_exists.")
    except OSError:
        raise CommandError(EXIT_DATA, "Backtest failed: output_path_unavailable.") from None
    try:
        if args.source == "synthetic":
            source = SyntheticMarketDataSource(calendar=calendar)
            previous = calendar.previous_session(args.start)
            first = calendar.sessions_window(previous, config.strategy.required_history())[0]
            symbols = sorted(
                {
                    *config.universe.tradable_symbols,
                    config.universe.regime_benchmark,
                    *config.universe.comparison_benchmarks,
                }
            )
            data = BacktestData(
                source.get_daily_bars(symbols, first, args.end),
                source.feed,
                source.adjustment,
                source.source,
            )
        else:
            data = load_backtest_data(
                ParquetBarStore(settings.data_dir),
                config.universe,
                config.data,
                start=args.start,
                end=args.end,
                calendar=calendar,
                corporate_actions=None,
            )
        gate = RiskManager(
            config.risk,
            calendar=calendar,
            earnings=UnavailableEarningsCalendar(),
            tradable_symbols=frozenset(config.universe.tradable_symbols),
            earnings_waiver="backtest_unavailable"
            if backtest.earnings_policy == "waive_unavailable"
            else "none",
        )
        request = BacktestRequest(
            args.start,
            args.end,
            config.universe,
            config.strategy,
            config.risk,
            backtest,
            config.data,
            args.reproduce_command,
            config.scanner,
        )
        result = run_backtest(request, data, gate=gate, calendar=calendar)
        result = replace(result, git_sha=git_sha())
        # No output directory is created until the entire simulation succeeds.
        if args.output is not None:
            args.output.mkdir(parents=True, exist_ok=False)
        path = result.save(args.output if args.output is not None else settings.data_dir)
    except BacktestInputError as exc:
        raise CommandError(EXIT_DATA, f"Backtest failed: {exc.reason_code}.") from None
    except (ValueError, OSError, OverflowError):
        raise CommandError(
            EXIT_DATA, "Backtest failed: invalid or unavailable data/output."
        ) from None
    sys.stdout.write("\n".join(result.labels) + "\n")
    sys.stdout.write(f"Command: {result.command}\nArtifact: {path}\n")
    return EXIT_OK


def _cmd_metrics(args: argparse.Namespace) -> int:
    settings, _, _ = _load(args)
    try:
        result = TypeAdapter(BacktestResult).validate_json(
            args.result.read_text(encoding="utf-8"), strict=True
        )
        if result.schema_version != 1 or not result.labels or not result.command:
            raise ValueError("invalid_result_metadata")
        result.to_dict()
        context = read_context(result)
        if (
            result.metrics_fingerprint != result.context_fingerprint()
            or result.initial_equity != context.settings.initial_equity
        ):
            raise ValueError("invalid_metrics_context")
        calendar = TradingCalendar(context.data_config.calendar)
        data = None
        if context.source == DataSource.SYNTHETIC:
            source = SyntheticMarketDataSource(calendar=calendar)
            first = calendar.sessions_window(
                calendar.previous_session(result.start), context.params.required_history()
            )[0]
            symbols = sorted(
                {
                    *context.universe.tradable_symbols,
                    context.universe.regime_benchmark,
                    *context.universe.comparison_benchmarks,
                }
            )
            data = BacktestData(
                source.get_daily_bars(symbols, first, result.end),
                source.feed,
                source.adjustment,
                source.source,
            )
        else:
            try:
                data = load_backtest_data(
                    ParquetBarStore(settings.data_dir),
                    context.universe,
                    context.data_config,
                    start=result.start,
                    end=result.end,
                    calendar=calendar,
                    corporate_actions=None,
                )
            except (ValueError, OSError):
                data = None
        report = metrics_report(result, data, command=args.reproduce_command)
        rendering = json.dumps(
            report, default=lambda d: d.isoformat(), ensure_ascii=False, allow_nan=False, indent=2
        )
    except (ValueError, KeyError, TypeError, OSError, OverflowError):
        raise CommandError(
            EXIT_DATA, "Metrics failed: invalid result or missing run context."
        ) from None
    sys.stdout.write(rendering + "\n")
    return EXIT_OK


def _cmd_dashboard(args: argparse.Namespace) -> int:
    try:
        return launch(args.source)
    except ValueError as exc:
        raise CommandError(EXIT_CONFIG, str(exc)) from None


# ------------------------------------------------------------------ earnings (#20)


def _earnings_provider(args: argparse.Namespace) -> EarningsProvider:
    settings, config, _ = _load(args)
    config_dir = args.config_dir or settings.config_dir
    try:
        es = load_earnings_settings(config_dir)
    except (ConfigError, OSError):
        raise CommandError(EXIT_CONFIG, _CONFIG_FAILED) from None

    def secret(value: SecretStr | None) -> str | None:
        return value.get_secret_value() if value is not None else None

    return EarningsProvider(
        EarningsProviderSettings(
            lookahead_days=es.lookahead_days,
            min_forward_coverage_days=es.min_forward_coverage_days,
            max_age_hours=es.max_age_hours,
            max_attempts_per_day=es.max_attempts_per_day,
            retry_interval_minutes=es.retry_interval_minutes,
            request_timeout_seconds=es.request_timeout_seconds,
            finnhub_min_interval_seconds=es.finnhub_min_interval_seconds,
            override_max_age_days=es.override_max_age_days,
        ),
        cache_dir=settings.data_dir / "earnings",
        override_path=config_dir / es.override_file,
        universe=config.universe.tradable_symbols,
        finnhub_api_key=secret(settings.finnhub_api_key),
        alphavantage_api_key=secret(settings.alphavantage_api_key),
        clock=SystemClock(),
    )


def _cmd_earnings_refresh(args: argparse.Namespace) -> int:
    provider = _earnings_provider(args)
    try:
        outcomes = provider.refresh(force=args.force)
    except OSError:
        raise CommandError(EXIT_CONFIG, "Cannot write the earnings cache.") from None
    for o in outcomes:
        error = o.error or "-"
        sys.stdout.write(f"{o.source:<13} {o.action:<22} status={o.status} error={error}\n")
    return EXIT_OK if all(o.status == "ok" for o in outcomes) else EXIT_DATA


def _cmd_earnings_status(args: argparse.Namespace) -> int:
    provider = _earnings_provider(args)
    on_or_after = args.as_of or utcnow().date()
    try:
        resolve = provider.resolver()
    except EarningsDataError as exc:
        raise CommandError(
            EXIT_CONFIG, f"Earnings override file rejected (all entries blocked): {exc}"
        ) from None
    for source, snap in provider.snapshots().items():
        if snap is None:
            sys.stdout.write(f"{source:<13} missing\n")
            continue
        sys.stdout.write(
            f"{source:<13} {snap.status} fetched_at={snap.fetched_at.isoformat()} "
            f"coverage={snap.coverage_start}..{snap.coverage_end} error={snap.error or '-'}\n"
        )
    sys.stdout.write(f"\nR16 view for decisions on/after {on_or_after.isoformat()}:\n")
    all_verified = True
    for symbol in provider.universe:
        r = resolve(symbol, on_or_after)
        all_verified &= r.verified
        when = r.next_date.isoformat() if r.next_date else "-"
        state = "VERIFIED" if r.verified else "BLOCKED"
        sys.stdout.write(
            f"{symbol:<8} {state:<9} next={when:<10} source={r.source} reason={r.reason}\n"
        )
    return EXIT_OK if all_verified else EXIT_DATA


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915 - one flat parser table
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config-dir", type=Path, help="configuration directory")
    parser = argparse.ArgumentParser(prog="semiedge", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    dashboard = commands.add_parser("dashboard", help="local read-only PAPER dashboard")
    dashboard.add_argument("--source", choices=("cache", "synthetic"), default="cache")
    dashboard.set_defaults(handler=_cmd_dashboard)

    diagnostic = commands.add_parser(
        "diagnostics", parents=[common], help="validate local configuration offline"
    )
    diagnostic.add_argument("--html", type=Path, help="write a new offline dashboard HTML file")
    diagnostic.set_defaults(handler=_cmd_diagnostics)

    commands.add_parser("version", help="package version and git SHA").set_defaults(
        handler=_cmd_version
    )

    config = commands.add_parser("config", help="configuration commands")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser(
        "check", parents=[common], help="validate env + YAML; safety status and fingerprint"
    ).set_defaults(handler=_cmd_config_check)

    data = commands.add_parser("data", help="market data cache")
    data_commands = data.add_subparsers(dest="data_command", required=True)
    fetch = data_commands.add_parser(
        "fetch", parents=[common], help="fetch completed daily bars from Alpaca into the cache"
    )
    fetch.add_argument("--symbols", nargs="+", metavar="SYMBOL")
    fetch.add_argument("--start", type=_iso_date, metavar="YYYY-MM-DD")
    fetch.add_argument("--end", type=_iso_date, metavar="YYYY-MM-DD")
    fetch.set_defaults(handler=_cmd_data_fetch)
    data_commands.add_parser(
        "status", parents=[common], help="cache summary and freshness per symbol"
    ).set_defaults(handler=_cmd_data_status)
    bti = commands.add_parser(
        "backtest-intraday", parents=[common], help="replay intraday_v1 on cached 5-minute bars"
    )
    bti.add_argument("--start", type=_iso_date, metavar="YYYY-MM-DD", required=True)
    bti.add_argument("--end", type=_iso_date, metavar="YYYY-MM-DD", required=True)
    bti.add_argument("--equity", type=float, default=5000.0)
    bti.add_argument("--risk-profile", default="5k", help="risk_<name>.yaml; 'default' = risk.yaml")
    bti.add_argument("--output", type=Path, help="new output directory (existing paths refused)")
    bti.set_defaults(handler=_cmd_backtest_intraday)
    btd = commands.add_parser(
        "backtest-dip", parents=[common], help="dip_v1 on cached daily bars (spec periods only)"
    )
    btd.add_argument("--period", choices=("development", "holdout"), required=True)
    btd.add_argument("--variant", choices=("S1", "S2"), help="required for the holdout")
    btd.add_argument("--equity", type=float, default=5000.0)
    btd.add_argument("--risk-profile", default="5k", help="risk_<name>.yaml; 'default' = risk.yaml")
    btd.add_argument("--output", type=Path, help="new output directory (existing paths refused)")
    btd.set_defaults(handler=_cmd_backtest_dip)
    btid = commands.add_parser(
        "backtest-intraday-dip",
        parents=[common],
        help="intraday_dip_v1 on cached 5-minute bars (spec periods only)",
    )
    btid.add_argument("--period", choices=("development", "holdout"), required=True)
    btid.add_argument("--variant", choices=("V1", "V2"), help="required for the holdout")
    btid.add_argument("--equity", type=float, default=5000.0)
    btid.add_argument(
        "--risk-profile", default="5k", help="risk_<name>.yaml; 'default' = risk.yaml"
    )
    btid.add_argument("--output", type=Path, help="new output directory (existing paths refused)")
    btid.set_defaults(handler=_cmd_backtest_intraday_dip)
    intraday = data_commands.add_parser(
        "fetch-intraday",
        parents=[common],
        help="fetch completed 5-minute regular-session bars; replaces each symbol's 5-minute file",
    )
    intraday.add_argument("--symbols", nargs="+", metavar="SYMBOL")
    intraday.add_argument("--start", type=_iso_date, metavar="YYYY-MM-DD", required=True)
    intraday.add_argument("--end", type=_iso_date, metavar="YYYY-MM-DD")
    intraday.set_defaults(handler=_cmd_data_fetch_intraday)

    scan = commands.add_parser("scan", parents=[common], help="ranked momentum_v1 report")
    scan.add_argument("--source", choices=("cache", "synthetic"), default="cache")
    scan.add_argument("--as-of", type=_iso_date, metavar="YYYY-MM-DD")
    scan.add_argument("--equity", type=_positive_amount, help="equity for proposed sizes")
    scan.add_argument("--format", choices=tuple(_RENDERERS), default="table")
    scan.add_argument("--output", type=Path, help="also write the rendering to a new file")
    scan.set_defaults(handler=_cmd_scan)

    commands.add_parser(
        "account", parents=[common], help="read-only PAPER account snapshot"
    ).set_defaults(handler=_cmd_account)
    backtest = commands.add_parser("backtest", parents=[common], help="offline next-open backtest")
    backtest.add_argument("--start", required=True, type=_iso_date)
    backtest.add_argument("--end", required=True, type=_iso_date)
    backtest.add_argument("--source", choices=("cache", "synthetic"), default="cache")
    backtest.add_argument(
        "--output", type=Path, help="new output directory (existing paths refused)"
    )
    backtest.set_defaults(handler=_cmd_backtest)
    metrics = commands.add_parser("metrics", parents=[common], help="offline performance report")
    metrics.add_argument("result", type=Path, metavar="RESULT.json")
    metrics.set_defaults(handler=_cmd_metrics)

    earnings = commands.add_parser("earnings", help="earnings calendar for R16 (#20)")
    earnings_commands = earnings.add_subparsers(dest="earnings_command", required=True)
    refresh = earnings_commands.add_parser(
        "refresh", parents=[common], help="refresh Finnhub + Alpha Vantage (at most daily)"
    )
    refresh.add_argument("--force", action="store_true", help="ignore the daily/retry limits")
    refresh.set_defaults(handler=_cmd_earnings_refresh)
    status = earnings_commands.add_parser(
        "status", parents=[common], help="per-symbol R16 view from the cache (no network)"
    )
    status.add_argument("--as-of", type=_iso_date, metavar="YYYY-MM-DD")
    status.set_defaults(handler=_cmd_earnings_status)
    return parser


def _utf8_streams() -> None:
    # Reports contain non-ASCII text (the synthetic banner's em dash). Without this, Windows
    # pipes fall back to the locale code page and garble it.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_streams()
    args = build_parser().parse_args(argv)
    args.reproduce_command = shlex.join(["semiedge", *(sys.argv[1:] if argv is None else argv)])
    try:
        return int(args.handler(args))
    except CommandError as exc:
        sys.stderr.write(exc.message + "\n")
        return exc.code
    except MissingCredentialsError as exc:  # fixed text naming the env vars, never values
        sys.stderr.write(f"{exc}\n")
        return EXIT_CREDENTIALS
    except SafetyViolation:
        sys.stderr.write("A paper-only safety check refused the operation.\n")
        return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
