"""Shared decision-time exit levels and exchange-session holding periods."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from semiedge.config.models import MomentumV1Params
from semiedge.data.calendar import TradingCalendar


@dataclass(frozen=True, slots=True)
class ExitLevels:
    stop: float
    target: float


def exit_levels(entry_reference: float, atr: float, params: MomentumV1Params) -> ExitLevels:
    if not all(math.isfinite(v) and v > 0 for v in (entry_reference, atr)):
        raise ValueError("exit_levels: inputs must be finite and positive")
    stop = entry_reference - params.stop_atr_multiple * atr
    target = entry_reference + params.target_atr_multiple * atr
    if not all(math.isfinite(v) for v in (stop, target)) or not 0 < stop < entry_reference < target:
        raise ValueError("exit_levels: invalid_levels")
    return ExitLevels(stop, target)


def held_sessions(entry_session: date, session: date, calendar: TradingCalendar) -> int:
    if session < entry_session:
        raise ValueError("session precedes entry_session")
    if not calendar.is_session(entry_session) or not calendar.is_session(session):
        raise ValueError("holding endpoints must be exchange sessions")
    return len(calendar.sessions_in_range(entry_session, session))


def time_exit_due(
    entry_session: date, session: date, max_holding_sessions: int, calendar: TradingCalendar
) -> bool:
    if type(max_holding_sessions) is not int or max_holding_sessions <= 0:
        raise ValueError("max_holding_sessions must be a positive integer")
    return held_sessions(entry_session, session, calendar) >= max_holding_sessions
