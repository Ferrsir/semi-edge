"""The local order state machine (spec phase2-4-interfaces.md §6.4; spec-verbatim, issue #21).

``ALLOWED[from_status]`` is the set of statuses ``StateStore.transition_order`` accepts next:
exactly the edges drawn in §6.4, plus self-edges for ``partially_filled``, ``accepted``,
``submitted`` and ``cancel_pending``. Terminal statuses have no outgoing edge. The single
backward edge, ``unknown -> staged``, is the §6.4 recovery exception and carries extra
preconditions that the store checks (:data:`RECOVERY_EDGE`).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, get_args

from semiedge.state.models import TERMINAL_STATUSES, OrderStatus

__all__ = [
    "ALLOWED",
    "ALL_STATUSES",
    "BROKER_MAPPED_STATUSES",
    "RECOVERY_EDGE",
    "SENT_NON_TERMINAL",
    "UNSENT_STATUSES",
]

ALL_STATUSES: Final[frozenset[OrderStatus]] = frozenset(get_args(OrderStatus))

# Every local status a broker order status maps to (§6.4 "Broker -> local mapping");
# replaced/other map to no status (RECONCILIATION halt).
BROKER_MAPPED_STATUSES: Final[frozenset[OrderStatus]] = frozenset(
    {"accepted", "partially_filled", "filled", "canceled", "expired", "rejected", "cancel_pending"}
)

# Sent to the broker (or possibly sent) and not terminal (§5.3 one-live-sell rule).
SENT_NON_TERMINAL: Final[frozenset[OrderStatus]] = frozenset(
    {"submitting", "submitted", "unknown", "accepted", "partially_filled", "cancel_pending"}
)

# Never reached the broker: fills for these rows are impossible.
UNSENT_STATUSES: Final[frozenset[OrderStatus]] = frozenset(
    {"staged", "expired_unsubmitted", "withdrawn", "dry_run"}
)

RECOVERY_EDGE: Final[tuple[OrderStatus, OrderStatus]] = ("unknown", "staged")


def _s(*statuses: OrderStatus) -> frozenset[OrderStatus]:
    return frozenset(statuses)


_EDGES: dict[OrderStatus, frozenset[OrderStatus]] = {
    # staged -> begin submit (committed BEFORE the HTTP call) | valid_until passed |
    # halt/withdrawal before submit | submission disabled
    "staged": _s("submitting", "expired_unsubmitted", "withdrawn", "dry_run"),
    # response ok -> submitted | accepted | (broker status);
    # SubmitOutcomeUnknown / crash / OrderRejected(duplicate id) -> unknown;
    # OrderRejected (not duplicate) -> rejected
    "submitting": _s("submitted", "unknown", "rejected") | BROKER_MAPPED_STATUSES,
    # lookup found -> (mapped broker status); 404 confirmed (twice, >= 1 monitor cycle apart,
    # absent from list_orders(all)) -> staged if now < valid_until (the recovery exception),
    # else expired_unsubmitted
    "unknown": _s("staged", "expired_unsubmitted") | BROKER_MAPPED_STATUSES,
    # submitted | accepted -> accepted | partially_filled | filled | canceled | expired |
    # rejected; any non-terminal broker state -> cancel_pending (cancel request sent)
    "submitted": _s(
        "submitted",
        "accepted",
        "partially_filled",
        "filled",
        "canceled",
        "expired",
        "rejected",
        "cancel_pending",
    ),
    "accepted": _s(
        "accepted",
        "partially_filled",
        "filled",
        "canceled",
        "expired",
        "rejected",
        "cancel_pending",
    ),
    # partially_filled -> partially_filled | filled | canceled | expired; -> cancel_pending
    "partially_filled": _s("partially_filled", "filled", "canceled", "expired", "cancel_pending"),
    # cancel_pending -> canceled | filled | expired (self-edge to record fills/legs)
    "cancel_pending": _s("cancel_pending", "canceled", "filled", "expired"),
    # terminal: filled | canceled | expired | rejected | expired_unsubmitted | withdrawn | dry_run
    "filled": _s(),
    "canceled": _s(),
    "expired": _s(),
    "rejected": _s(),
    "expired_unsubmitted": _s(),
    "withdrawn": _s(),
    "dry_run": _s(),
}

ALLOWED: Final[Mapping[OrderStatus, frozenset[OrderStatus]]] = MappingProxyType(_EDGES)

_SINKS = {status for status, nxt in ALLOWED.items() if not nxt}
if set(ALLOWED) != ALL_STATUSES or _SINKS != TERMINAL_STATUSES:  # pragma: no cover
    raise AssertionError("ALLOWED must define every OrderStatus; exactly terminals are sinks")
