"""Immutable backtest artifacts, explicitly and exclusively persisted."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from semiedge.reporting import SYNTHETIC_BANNER
from semiedge.risk.types import HaltKind, RiskDecision

ExitReason = Literal[
    "stop", "stop_gap", "target", "target_gap", "time", "entry_gap_flatten", "end_of_window"
]


@dataclass(frozen=True)
class Trade:
    trade_id: str
    symbol: str
    signal_session: date
    entry_session: date
    entry_reference: float
    entry_price: float
    shares: int
    stop: float
    target: float
    exit_session: date
    exit_price: float
    exit_reason: ExitReason
    held_sessions: int
    costs: float
    gross_pnl: float
    net_pnl: float
    r_multiple: float
    score: float
    rank: int


@dataclass(frozen=True)
class CancelledEntry:
    symbol: str
    signal_session: date
    session: date
    reason: str


@dataclass(frozen=True)
class EquityPoint:
    session: date
    equity: float
    cash: float
    gross_exposure: float
    positions: int


def _json_default(value: object) -> str:
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"unsupported JSON type: {type(value).__name__}")


@dataclass(frozen=True)
class BacktestResult:
    schema_version: int
    run_id: str
    command: str
    git_sha: str | None
    semiedge_version: str
    config_fingerprint: str
    data_fingerprint: str
    start: date
    end: date
    labels: tuple[str, ...]
    trades: tuple[Trade, ...]
    cancelled_entries: tuple[CancelledEntry, ...]
    equity_curve: tuple[EquityPoint, ...]
    risk_decisions: tuple[RiskDecision, ...]
    halts: tuple[tuple[date, HaltKind, str], ...]
    data_gaps: tuple[tuple[date, str], ...]
    cap_overshoots: tuple[tuple[date, str, str], ...]
    initial_equity: float | None = None
    metrics_context: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        # Labels lead every JSON artifact. JSON conversion also validates all floats.
        raw = asdict(self)
        ordered = {"labels": raw.pop("labels"), **raw}
        result: dict[str, object] = json.loads(
            json.dumps(ordered, default=_json_default, allow_nan=False)
        )
        return result

    def deterministic_payload(self) -> dict[str, object]:
        result = self.to_dict()
        del result["run_id"]
        del result["git_sha"]
        return result

    def save(self, directory: Path) -> Path:
        if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", self.run_id):
            raise ValueError("invalid run_id")
        text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        root = directory / "synthetic" if SYNTHETIC_BANNER in self.labels else directory
        root = root / "reports" / "backtests"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{self.start}_{self.end}_{self.run_id}.json"
        with path.open("x", encoding="utf-8") as stream:
            stream.write(text)
        return path
