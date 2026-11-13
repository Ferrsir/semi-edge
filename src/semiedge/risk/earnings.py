"""Cross-checked earnings calendar for R16 (#20; spec earnings-calendar.md §4).

Pure adapter: it turns an :class:`~semiedge.data.earnings.EarningsResolution` into the
:class:`~semiedge.risk.types.EarningsInfo` the risk manager consumes. It performs no I/O and
imports no network module. The resolver is built by
:meth:`semiedge.data.earnings_sources.EarningsProvider.resolver` (or a test double).

Any exception from the resolver becomes ``verified=False`` (fail closed): R16 then rejects the
entry with ``earnings_data_unavailable``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

from semiedge.data.earnings import EarningsResolution
from semiedge.risk.types import EarningsInfo

__all__ = ["CrossCheckedEarningsCalendar", "Resolver"]

log = logging.getLogger(__name__)

Resolver = Callable[[str, date], EarningsResolution]


class CrossCheckedEarningsCalendar:
    """``EarningsCalendar`` backed by Finnhub + Alpha Vantage + manual overrides."""

    def __init__(self, resolver: Resolver) -> None:
        self._resolver = resolver
        self._warned: set[tuple[str, str]] = set()

    def resolution(self, symbol: str, on_or_after: date) -> EarningsResolution:
        try:
            result = self._resolver(symbol, on_or_after)
        except Exception as exc:  # fail closed; keep only the class name
            reason = f"resolver_error_{type(exc).__name__.lower()}"
            return EarningsResolution(symbol, None, False, "none", reason, "resolver raised")
        if not isinstance(result, EarningsResolution) or result.symbol != symbol:
            return EarningsResolution(
                symbol, None, False, "none", "resolver_invalid_result", "wrong record"
            )
        return result

    def next_earnings(self, symbol: str, on_or_after: date) -> EarningsInfo:
        result = self.resolution(symbol, on_or_after)
        verified = result.verified is True
        if not verified and (symbol, result.reason) not in self._warned:
            self._warned.add((symbol, result.reason))
            log.warning(
                "earnings unverified for %s (%s: %s); R16 blocks new entries for this symbol",
                symbol,
                result.reason,
                result.detail,
                extra={"symbol": symbol, "reason": result.reason},
            )
        return EarningsInfo(
            symbol=symbol,
            next_earnings_session=result.next_date if verified else None,
            verified=verified,
            source=result.source if verified else f"none ({result.reason})",
        )
