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
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import ValidationError

from semiedge import __version__
from semiedge.broker.account import BrokerError, fetch_account_snapshot
from semiedge.config import (
    AppConfig,
    AppSettings,
    ConfigError,
    MissingCredentialsError,
    config_fingerprint,
    load_app_config,
)
from semiedge.data.alpaca_source import AlpacaMarketDataSource, MarketDataError
from semiedge.data.calendar import TradingCalendar
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
    source = market_data_source(settings, config)
    try:
        frames = source.get_daily_bars(symbols, start, end)
    except MarketDataError:
        raise CommandError(EXIT_DATA, "Market data request failed; nothing was stored.") from None
    except Exception as exc:  # sanitised: vendor errors may carry URLs or response bodies
        raise CommandError(
            EXIT_DATA, f"Market data request failed ({type(exc).__name__}); nothing was stored."
        ) from None
    store = ParquetBarStore(settings.data_dir)
    stored: dict[str, dict[str, object]] = {}
    for symbol in symbols:
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            stored[symbol] = {"rows": 0, "first_session": None, "last_session": None}
            continue
        if frame.index[-1].date() > end:  # fail closed: a vendor must not hand back later bars
            raise CommandError(EXIT_DATA, f"Refused to store {symbol}: bars after --end {end}")
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
                "last_fetched_at": pd.Timestamp(row["last_fetched_at"]).isoformat(),
                "fresh": last_session == last,
            }
        )
    _print_json({"last_completed_session": last.isoformat(), "series": series})
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
    try:
        saved = persist_report(report, settings.data_dir)
    except OSError:
        raise CommandError(
            EXIT_CONFIG, "Cannot persist the report under the configured data directory."
        ) from None
    if args.output:
        _write_new(args.output, rendered)
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
    client = paper_account_client(settings)
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


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config-dir", type=Path, help="configuration directory")
    parser = argparse.ArgumentParser(prog="semiedge", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

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
