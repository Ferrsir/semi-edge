"""Decision-close exit levels and exchange-session holding counts (spec §3.3)."""

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


def _positive(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("exit inputs must be positive finite numbers")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError("exit inputs must be positive finite numbers") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError("exit inputs must be positive finite numbers")
    return number


def exit_levels(entry_reference: float, atr: float, params: MomentumV1Params) -> ExitLevels:
    """Anchor levels at decision close; reject invalid inputs and computed levels."""
    entry, volatility = _positive(entry_reference), _positive(atr)
    stop_multiple, target_multiple = (
        _positive(params.stop_atr_multiple),
        _positive(params.target_atr_multiple),
    )
    stop = entry - stop_multiple * volatility
    target = entry + target_multiple * volatility
    if not (math.isfinite(stop) and math.isfinite(target) and 0 < stop < entry < target):
        raise ValueError("exit levels must be finite with 0 < stop < entry < target")
    return ExitLevels(stop, target)


def held_sessions(entry_session: date, session: date, calendar: TradingCalendar) -> int:
    """Count exchange sessions inclusively; endpoints must be valid exchange dates."""
    if type(entry_session) is not date or type(session) is not date:
        raise ValueError("holding endpoints must be session dates")
    if session < entry_session:
        raise ValueError("session must not precede entry_session")
    if not calendar.is_session(entry_session) or not calendar.is_session(session):
        raise ValueError("holding endpoints must be exchange sessions")
    return len(calendar.sessions_in_range(entry_session, session))


def time_exit_due(
    entry_session: date, session: date, max_holding_sessions: int, calendar: TradingCalendar
) -> bool:
    """Decide at close; caller executes at next open (entry session counts as one)."""
    if type(max_holding_sessions) is not int or max_holding_sessions <= 0:
        raise ValueError("max_holding_sessions must be a positive integer")
    return held_sessions(entry_session, session, calendar) >= max_holding_sessions
