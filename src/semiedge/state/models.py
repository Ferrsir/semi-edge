"""Persistent state records (spec phase2-4-interfaces.md §5.1, spec-verbatim; issue #21).

Frozen dataclasses mirroring the §5.2 rows. Constructors do not validate (the store does), with
one exception: :class:`Notification` refuses ``TradingMode.LIVE``. The reconciliation types of §7
also live here so that ``semiedge.state`` never imports ``semiedge.execution``
(``semiedge.execution.reconcile`` re-exports them), and ``Notification``/``NotificationCategory``
live here so that ``semiedge.state`` never imports ``semiedge.notify`` (§0.1).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Final, Literal, Protocol, runtime_checkable

from semiedge.risk.types import Halt, HaltKind, OrderSide
from semiedge.safety.modes import TradingMode

__all__ = [
    "NOTIFICATION_BODY_MAX",
    "NOTIFICATION_TITLE_MAX",
    "TERMINAL_STATUSES",
    "BrokerFillInput",
    "CorporateActionAdjustment",
    "Discrepancy",
    "DiscrepancyKind",
    "EquitySnapshot",
    "FillIngestResult",
    "FillRecord",
    "FillSource",
    "FillStatus",
    "HaltRecord",
    "Notification",
    "NotificationCategory",
    "OrderIntent",
    "OrderKind",
    "OrderRecord",
    "OrderStatus",
    "PositionRecord",
    "ReconciliationReport",
    "StateReader",
]

OrderKind = Literal["entry", "protection", "time_exit", "flatten"]
OrderStatus = Literal[  # §6.4
    "staged",
    "submitting",
    "submitted",
    "unknown",
    "accepted",
    "partially_filled",
    "cancel_pending",
    "filled",
    "canceled",
    "expired",
    "rejected",
    "expired_unsubmitted",
    "withdrawn",
    "dry_run",
]
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"filled", "canceled", "expired", "rejected", "expired_unsubmitted", "withdrawn", "dry_run"}
)


@dataclass(frozen=True, slots=True)
class OrderIntent:  # inserted with status "staged"
    client_order_id: str
    kind: OrderKind
    symbol: str
    side: OrderSide
    qty: int
    order_type: Literal["market", "limit"]
    time_in_force: Literal["opg", "day", "gtc"]
    order_class: Literal["simple", "oco"]
    limit_price: float | None  # protection: limit=target, stop=stop
    stop_price: float | None
    decision_session: date
    valid_until: datetime
    risk_decision_id: int | None  # required for kind="entry"
    parent_client_order_id: str | None  # protection/time_exit/flatten -> the entry of the position
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OrderRecord:  # one orders row
    intent: OrderIntent
    status: OrderStatus
    broker_order_id: str | None
    filled_qty: int
    filled_avg_price: float | None
    legs_json: str
    submitted_at: datetime | None
    updated_at: datetime
    last_error: str | None


FillSource = Literal["activity", "cumulative"]
FillStatus = Literal["final", "provisional", "superseded"]


@dataclass(frozen=True, slots=True)
class FillRecord:  # §6.4 "Fill ingestion" (F-038)
    fill_key: str  # activity: broker activity id; cumulative: f"{client_order_id}:{cum_qty}"
    client_order_id: str
    ts: datetime
    qty: int
    price: float
    source: FillSource  # activity rows are "final"; cumulative rows are "provisional"
    status: FillStatus  # "superseded": kept for audit, excluded from every quantity
    cum_qty: int  # this order's local cumulative after the row was ingested
    superseded_at: datetime | None


@dataclass(frozen=True, slots=True)
class BrokerFillInput:  # one broker FILL activity as handed to StateStore.ingest_order_fills
    activity_id: str
    qty: int
    price: float
    ts: datetime


@dataclass(frozen=True, slots=True)
class FillIngestResult:
    added_qty: int  # W_new - W_old (>= 0)
    inserted: tuple[str, ...]  # fill keys inserted by this call, in insertion order
    superseded: tuple[str, ...]  # provisional fill keys superseded by this call
    local_filled_qty: int  # W_new == orders.filled_qty


@dataclass(frozen=True, slots=True)
class CorporateActionAdjustment:  # one corporate_action_adjustments row (§9.4, F-039)
    symbol: str
    ex_date: date
    event_id: str
    new_shares: int
    old_shares: int  # exact ratio (F-042)
    qty_before: int
    qty_after: int
    applied_at: datetime
    # non-fill quantity delta = qty_after - qty_before


@dataclass(frozen=True, slots=True)
class PositionRecord:  # one positions row
    symbol: str
    qty: int
    avg_entry_price: float
    entry_session: date
    entry_client_order_id: str
    entry_reference: float
    atr: float
    stop: float
    target: float
    split_ratio: float
    status: Literal["open", "exiting", "closed"]
    opened_at: datetime
    closed_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class HaltRecord:
    id: int
    kind: HaltKind
    reason: str
    created_at: datetime
    created_by: str
    cleared_at: datetime | None
    cleared_by: str | None

    def as_halt(self) -> Halt:
        return Halt(kind=self.kind, reason=self.reason, since=self.created_at)


class NotificationCategory(StrEnum):  # re-exported by semiedge.notify
    SIGNAL = "signal"
    ORDER = "order"
    FILL = "fill"
    EXIT = "exit"
    SUMMARY = "summary"
    RISK = "risk"
    HALT = "halt"
    BROKER = "broker"
    SYSTEM = "system"


NOTIFICATION_TITLE_MAX: Final = 200
NOTIFICATION_BODY_MAX: Final = 1800


@dataclass(frozen=True, slots=True)
class Notification:  # re-exported by semiedge.notify
    category: NotificationCategory
    mode: TradingMode  # __post_init__: LIVE -> ValueError (the only validating ctor)
    title: str  # truncated to 200 chars after sanitising
    body: str  # truncated to 1800 chars after sanitising
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.mode, TradingMode) or self.mode is TradingMode.LIVE:
            raise ValueError("notifications exist only for scanner, backtest and paper modes")
        if not isinstance(self.title, str) or not isinstance(self.body, str):
            raise ValueError("notification title and body must be strings")
        # Content sanitising (redaction, mention escaping) belongs to semiedge.security /
        # semiedge.notify (§11, §13); the length caps are enforced here so no oversized text
        # can ever be queued.
        object.__setattr__(self, "title", self.title[:NOTIFICATION_TITLE_MAX])
        object.__setattr__(self, "body", self.body[:NOTIFICATION_BODY_MAX])


@dataclass(frozen=True, slots=True)
class EquitySnapshot:  # one equity_snapshots row; built by the engine from
    ts: datetime  # semiedge.broker.account.AccountSnapshot (equity,
    session: date  # last_equity, cash, long_market_value), so that
    equity: float  # semiedge.state never imports semiedge.broker
    last_equity: float  # (not even under TYPE_CHECKING, §0.2)
    cash: float
    long_market_value: float
    source: Literal["broker_close", "broker_intraday"]


# ------------------------------------------------------------------ reconciliation types (§7)


class DiscrepancyKind(StrEnum):
    UNKNOWN_BROKER_ORDER = "unknown_broker_order"  # broker open/recent order not in local DB
    FOREIGN_ORDER = "foreign_order"  # not ours (other namespace / no se1 prefix)
    LOCAL_ORDER_MISSING_AT_BROKER = "local_order_missing_at_broker"
    STATUS_ADVANCED = "status_advanced"  # broker further along; apply
    STATUS_REGRESSED = "status_regressed"  # broker behind local terminal state
    FILL_MISMATCH = "fill_mismatch"
    POSITION_QTY_MISMATCH = "position_qty_mismatch"
    UNKNOWN_BROKER_POSITION = "unknown_broker_position"
    LOCAL_POSITION_MISSING_AT_BROKER = "local_position_missing_at_broker"
    MISSING_PROTECTION = "missing_protection"
    ORDER_MISMATCH = "order_mismatch"  # same client id, different symbol/side/qty
    UNSUPPORTED_BROKER_STATE = "unsupported_broker_state"  # replaced/other, short/fractional
    OVERSOLD = "oversold"  # open sells exceed position (§9.3)
    CORPORATE_ACTION_ADJUSTED = "corporate_action_adjusted"  # verified split explains it (§9.4)


@dataclass(frozen=True)
class Discrepancy:
    kind: DiscrepancyKind
    symbol: str | None
    client_order_id: str | None
    local: str
    broker: str
    resolution: Literal["applied", "halt", "action_required"]


@dataclass(frozen=True)
class ReconciliationReport:
    started_at: datetime
    finished_at: datetime
    outcome: Literal["clean", "resolved", "halted", "error"]
    discrepancies: tuple[Discrepancy, ...]


# ------------------------------------------------------------------ the one read surface (F-037)


@runtime_checkable
class StateReader(Protocol):  # F-037: the ONE read surface used by diff_state (§7);
    # StateStore and ReadOnlyStateStore implement every method with exactly these signatures,
    # parameter names and return types (no adapters, no positional-only variants).
    @property
    def store_id(self) -> str: ...

    def data_source(self) -> Literal["real", "synthetic"]: ...

    # F-043: the DB's source class, recorded at creation; §9.4 split verification compares a
    # CorporateAction's source class with this (SYNTHETIC/FIXTURE <-> "synthetic", else "real").

    def orders(
        self,
        *,
        statuses: Collection[OrderStatus] | None = None,
        symbol: str | None = None,
        kinds: Collection[OrderKind] | None = None,
        limit: int | None = None,
    ) -> list[OrderRecord]: ...

    # filters AND-combined; None = no filter; an empty collection matches nothing.
    # limit None -> all rows, ordered (created_at, client_order_id) ascending;
    # limit set -> the newest `limit` rows, ordered (created_at, client_order_id) descending.
    # limit must be None or an int > 0 (type(limit) is int; bool, 0, negatives -> ValueError).

    def fills(
        self,
        *,
        client_order_id: str | None = None,
        include_superseded: bool = False,
        limit: int | None = None,
    ) -> list[FillRecord]: ...

    # same ordering/limit rules as orders(), keyed on (ts, fill_key)

    def positions(self, *, include_closed: bool = False) -> tuple[PositionRecord, ...]: ...

    def active_halts(self) -> tuple[HaltRecord, ...]: ...

    def corporate_action_adjustments(
        self, *, symbol: str | None = None
    ) -> tuple[CorporateActionAdjustment, ...]: ...

    # ordered (symbol, ex_date, event_id)
