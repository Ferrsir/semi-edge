"""Cached exchange sessions and UTC completion boundaries (1990 through 2050)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Any

import exchange_calendars as xcals
import pandas as pd


@lru_cache(maxsize=8)
def _calendar(name: str) -> Any:
    # Explicit bounds avoid the library's moving, wall-clock-relative default range.
    return xcals.get_calendar(name, start="1990-01-01", end="2050-12-31")


class TradingCalendar:
    def __init__(self, name: str = "XNYS") -> None:
        self._calendar = _calendar(name)

    def sessions_in_range(self, start: date, end: date) -> list[date]:
        if start > end:
            raise ValueError("start must not exceed end")
        return [s.date() for s in self._calendar.sessions_in_range(start, end)]

    def is_session(self, day: date) -> bool:
        return bool(self._calendar.is_session(day))

    def session_close(self, session: date) -> datetime:
        return pd.Timestamp(self._calendar.session_close(session)).to_pydatetime()

    def session_open(self, session: date) -> datetime:
        """Regular-session open as a UTC datetime (09:30 New York, or a late open if any)."""
        return pd.Timestamp(self._calendar.session_open(session)).to_pydatetime()

    def previous_session(self, session: date) -> date:
        return pd.Timestamp(self._calendar.previous_session(session)).date()

    def next_session(self, day: date) -> date:
        return pd.Timestamp(
            self._calendar.date_to_session(day + timedelta(days=1), direction="next")
        ).date()

    def last_completed_session(self, now: datetime, settle_minutes: int) -> date:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if type(settle_minutes) is not int or settle_minutes < 0:
            raise ValueError("settle_minutes must be a nonnegative integer")
        cutoff = now.astimezone(UTC) - timedelta(minutes=settle_minutes)
        day = pd.Timestamp(cutoff).tz_convert("UTC").date()
        session = pd.Timestamp(self._calendar.date_to_session(day, direction="previous")).date()
        while self.session_close(session) > cutoff:
            session = self.previous_session(session)
        return session

    def sessions_window(self, end: date, count: int) -> list[date]:
        if type(count) is not int or count <= 0:
            raise ValueError("count must be a positive integer")
        return [s.date() for s in self._calendar.sessions_window(end, -count)]
