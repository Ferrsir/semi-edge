"""Static detection of unsafe trading configuration and code patterns.

RESTRICTED PATH (scripts/governance/**): changes require explicit human approval.

Stdlib only. Run from the repository root:
  python scripts/governance/check_unsafe_config.py [--root .]

Fails (exit 1) when it finds, outside explicitly allow-listed locations:
* ``paper=False`` / ``url_override`` passed to a broker client;
* the live Alpaca trading host (``api.alpaca.markets`` not preceded by ``paper-``/``data.``);
* ``TradingClient(`` constructed anywhere except ``src/semiedge/safety/broker.py``;
* environment/compose files enabling live trading or a non-scanner default mode in examples;
* committed ``.env`` files.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

BROKER_FACTORY = "src/semiedge/safety/broker.py"
SCANNED_SUFFIXES = {".py", ".yaml", ".yml", ".toml", ".json", ".env", ".example", ".cfg", ".ini"}
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "data",
    "build",
    "dist",
    ".claude",
    ".worktrees",
}
# Tests and this checker legitimately mention unsafe values in order to prove they are refused.
ALLOW_UNSAFE_MENTIONS = ("tests/", "scripts/governance/check_unsafe_config.py")

LIVE_HOST = re.compile(r"(?<![\w.-])api\.alpaca\.markets")
PAPER_FALSE = re.compile(r"\bpaper\s*=\s*False\b")
URL_OVERRIDE = re.compile(r"\burl_override\s*=")
TRADING_CLIENT = re.compile(r"\bTradingClient\s*\(")
ENV_LINE = re.compile(
    r"^\s*(ALLOW_LIVE_TRADING|TRADING_MODE|ENABLE_ORDER_SUBMISSION)\s*[=:]\s*\"?([^\s\"#]*)"
)
EXPECTED_EXAMPLE_DEFAULTS = {
    "TRADING_MODE": "scanner",
    "ENABLE_ORDER_SUBMISSION": "false",
    "ALLOW_LIVE_TRADING": "false",
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def iter_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]) or not path.is_file():
            continue
        if (
            path.suffix in SCANNED_SUFFIXES
            or path.name.startswith(".env")
            or "compose" in path.name
        ):
            yield path


def scan(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_files(root):
        rel = path.relative_to(root).as_posix()
        if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
            findings.append(Finding(rel, 1, "committed environment file (secrets risk)"))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if rel.startswith(ALLOW_UNSAFE_MENTIONS):
            continue
        for n, line in enumerate(text.splitlines(), start=1):
            findings.extend(_check_line(rel, path, n, line))
    return findings


def _check_line(rel: str, path: Path, n: int, line: str) -> list[Finding]:
    findings: list[Finding] = []
    code = line.split("#", 1)[0] if path.suffix == ".py" else line
    if PAPER_FALSE.search(code):
        findings.append(Finding(rel, n, "paper=False is forbidden (paper-only MVP)"))
    if URL_OVERRIDE.search(code):
        findings.append(Finding(rel, n, "url_override is forbidden for broker clients"))
    if LIVE_HOST.search(line) and rel != BROKER_FACTORY:
        findings.append(Finding(rel, n, "live Alpaca trading host referenced"))
    if path.suffix == ".py" and TRADING_CLIENT.search(code) and rel != BROKER_FACTORY:
        findings.append(Finding(rel, n, f"TradingClient constructed outside {BROKER_FACTORY}"))
    m = ENV_LINE.match(line)
    if m:
        key, value = m.group(1), m.group(2).strip().lower()
        if key == "ALLOW_LIVE_TRADING" and value not in {"false", "0", ""}:
            findings.append(Finding(rel, n, "ALLOW_LIVE_TRADING must be false"))
        if key == "TRADING_MODE" and value == "live":
            findings.append(Finding(rel, n, "TRADING_MODE=live is unsupported"))
        if path.name == ".env.example" and value != EXPECTED_EXAMPLE_DEFAULTS[key]:
            expected = EXPECTED_EXAMPLE_DEFAULTS[key]
            findings.append(Finding(rel, n, f"{key} default in .env.example must be {expected}"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    findings = scan(Path(args.root).resolve())
    for f in findings:
        print(f"UNSAFE: {f}")
    print(f"check_unsafe_config: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
