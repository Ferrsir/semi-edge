"""Strategy result types shared by scanners, reports and the backtester (issue #12).

See docs/specifications/phase1-interfaces.md (``semiedge.strategy``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from semiedge.data.schema import DataFeed


@dataclass(frozen=True)
class ConditionResult:
    """One mandatory condition at the signal session, with the observed value and threshold."""

    name: str  # e.g. "sector_regime", "above_sma_fast", "breakout", ...
    passed: bool
    value: float | None  # observed value (None if undefined)
    threshold: float | None
    description: str  # human-readable, e.g. "SMH close 251.30 > SMA50 240.12"


@dataclass(frozen=True)
class SignalEvaluation:
    """The evaluation of one tradable symbol at one ``as_of`` session."""

    symbol: str
    session: date | None  # latest completed stock session <= as_of (None if no bars)
    strategy: str
    strategy_version: str
    eligible: bool
    conditions: tuple[ConditionResult, ...]
    score: float | None  # 0..100 when every score input is defined
    score_components: Mapping[str, float]  # s_RS, s_BO, s_RV, s_TP (defined ones only)
    close: float | None
    atr: float | None
    proposed_stop: float | None  # set only for eligible symbols
    proposed_target: float | None  # set only for eligible symbols
    rejection_reasons: tuple[str, ...]  # empty iff eligible
    rank: int | None  # 1-based among eligible symbols, else None


class QualityReport(Protocol):
    """Structural view of ``semiedge.data.validation.DataQualityReport`` used for gating.

    Declared as a protocol so the strategy depends only on the fields it reads, not on the
    data-layer implementation.
    """

    @property
    def symbol(self) -> str: ...

    @property
    def as_of(self) -> date: ...

    @property
    def feed(self) -> DataFeed | None: ...

    @property
    def issues(self) -> tuple[str, ...]: ...

    @property
    def is_actionable(self) -> bool: ...
