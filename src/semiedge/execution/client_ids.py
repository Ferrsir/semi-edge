"""Deterministic client order IDs (spec ``phase2-4-interfaces.md`` §6.2, #21).

``se1-{ns}-{code}-{YYYYMMDD}-{SYMBOL}-{attempt}``. The same intent always yields the same ID and
retries reuse it, so the broker's duplicate-ID rejection prevents a second order. The namespace is
derived from the state DB's ``store_id``, so a recreated DB can never adopt old broker orders.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Final, get_args

from semiedge.state.models import OrderKind

CLIENT_ORDER_ID_PREFIX: Final = "se1"
MAX_LENGTH: Final = 48
_KIND_CODE: Final[dict[str, str]] = {
    "entry": "en",
    "protection": "pr",
    "time_exit": "tx",
    "flatten": "fx",
}
_CODE_KIND: Final = {v: k for k, v in _KIND_CODE.items()}
_SYMBOL: Final = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")
_NAMESPACE: Final = re.compile(r"[0-9a-f]{8}")
_ATTEMPT: Final = re.compile(r"[1-9][0-9]?")
if set(_KIND_CODE) != set(get_args(OrderKind)):  # pragma: no cover - guards a schema change
    raise RuntimeError("client id kind codes do not match OrderKind")
_ID: Final = re.compile(
    rf"{CLIENT_ORDER_ID_PREFIX}-(?P<ns>[0-9a-f]{{8}})-(?P<code>en|pr|tx|fx)-"
    r"(?P<day>[0-9]{8})-(?P<rest>.+)"
)


@dataclass(frozen=True, slots=True)
class ParsedClientOrderId:
    namespace: str
    kind: OrderKind
    decision_session: date
    symbol: str
    attempt: int


def namespace(store_id: str) -> str:
    """First 8 hex characters of ``sha256(store_id)``."""
    if type(store_id) is not str or not store_id:
        raise ValueError("store_id must be a non-empty string")
    return hashlib.sha256(store_id.encode("utf-8")).hexdigest()[:8]


def make_client_order_id(
    ns: str, kind: OrderKind, decision_session: date, symbol: str, attempt: int
) -> str:
    if type(ns) is not str or _NAMESPACE.fullmatch(ns) is None:
        raise ValueError("namespace must be 8 lowercase hex characters")
    if kind not in _KIND_CODE:
        raise ValueError(f"unknown order kind {kind!r}")
    if type(decision_session) is not date:  # a datetime is a date subclass; refuse it
        raise ValueError("decision_session must be a date")
    if type(symbol) is not str or _SYMBOL.fullmatch(symbol) is None:
        raise ValueError("symbol does not match the universe pattern")
    if type(attempt) is not int or not 1 <= attempt <= 99:
        raise ValueError("attempt must be an int from 1 to 99")
    value = (
        f"{CLIENT_ORDER_ID_PREFIX}-{ns}-{_KIND_CODE[kind]}-{decision_session:%Y%m%d}-"
        f"{symbol}-{attempt}"
    )
    if len(value) > MAX_LENGTH:  # pragma: no cover - unreachable with the patterns above
        raise ValueError("client order id too long")
    return value


def parse_client_order_id(value: object) -> ParsedClientOrderId | None:
    """The parsed ID, or ``None`` if the value is not one of ours (in any namespace)."""
    if type(value) is not str or len(value) > MAX_LENGTH:
        return None
    m = _ID.fullmatch(value)
    tail = m["rest"].rsplit("-", 1) if m else []
    if m is None or len(tail) != 2:
        return None
    symbol, attempt = tail
    try:
        day = m["day"]
        session = date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}")
        kind: OrderKind = _CODE_KIND[m["code"]]  # type: ignore[assignment]
        parsed = ParsedClientOrderId(m["ns"], kind, session, symbol, int(attempt))
        canonical = make_client_order_id(m["ns"], kind, session, symbol, parsed.attempt)
    except (KeyError, ValueError):
        return None
    if _ATTEMPT.fullmatch(attempt) is None or canonical != value:
        return None  # only canonical forms round-trip
    return parsed
