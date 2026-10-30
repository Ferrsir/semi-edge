"""Offline diagnostics for the implemented foundation; never constructs a broker."""

from __future__ import annotations

import argparse
import json
import sys
from html import escape
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from semiedge.config import AppSettings, ConfigError, config_fingerprint, load_app_config
from semiedge.safety.modes import SafetyViolation


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
            "No signal, sizing, execution, reconciliation or P&L engine is implemented.",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    diagnostic = commands.add_parser("diagnostics", help="validate local configuration offline")
    diagnostic.add_argument("--config-dir", type=Path)
    diagnostic.add_argument("--html", type=Path, help="write a new offline dashboard HTML file")
    args = parser.parse_args(argv)
    try:
        settings = AppSettings()
        report = diagnostics(args.config_dir or settings.config_dir, settings)
    except (ConfigError, ValidationError, SafetyViolation, OSError):
        # Validation errors may contain raw input, including accidentally pasted credentials.
        sys.stderr.write("Configuration validation failed; check local settings and YAML files.\n")
        return 2
    if args.html:
        try:
            # Exclusive creation prevents accidentally overwriting configuration or user files.
            with args.html.open("x", encoding="utf-8") as output:
                output.write(render_dashboard(report))
        except OSError:
            sys.stderr.write("Cannot create dashboard; use a new path in an existing directory.\n")
            return 2
    sys.stdout.write(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
