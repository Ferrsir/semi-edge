"""The one wall-clock seam for Phase 2-4 modules (spec phase2-4-interfaces.md §0.3, spec-verbatim).

No other new module reads the wall clock: everything takes a :class:`Clock` or an explicit ``now``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

__all__ = ["Clock", "FrozenClock", "SystemClock"]


class Clock(Protocol):
    def now(self) -> datetime: ...  # tz-aware UTC, always


class SystemClock:
    """``datetime.now(UTC)``."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """A manually advanced clock for tests; a naive datetime is a ``ValueError``."""

    def __init__(self, at: datetime) -> None:
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("FrozenClock needs a timezone-aware datetime")
        self._at = at.astimezone(UTC)

    def now(self) -> datetime:
        return self._at

    def advance(self, delta: timedelta) -> None:
        if not isinstance(delta, timedelta) or delta < timedelta(0):
            raise ValueError("FrozenClock.advance needs a non-negative timedelta")
        self._at = self._at + delta
