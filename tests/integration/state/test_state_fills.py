"""Fill ingestion by the per-order watermark (spec §6.4, §5.4 F-038/F-044/F-046; issue #21)."""

from __future__ import annotations

import math
from datetime import timedelta

import pytest

from semiedge.risk.types import HaltKind
from semiedge.state.models import BrokerFillInput, FillRecord, OrderRecord
from semiedge.state.store import StateStoreError
from state_support import T0, Harness, snapshot

pytestmark = pytest.mark.integration

NOW = T0 + timedelta(hours=2)


def act(activity_id: str, qty: int, price: float, minute: int) -> BrokerFillInput:
    return BrokerFillInput(activity_id, qty, price, T0 + timedelta(hours=1, minutes=minute))


def notional(order: OrderRecord, fills: list[FillRecord]) -> float:
    """§6.4 order notional: W * filled_avg_price while a provisional row is active, else
    sum(qty * price) of the final rows."""
    active = [f for f in fills if f.status != "superseded"]
    if any(f.status == "provisional" for f in active):
        assert order.filled_avg_price is not None
        return order.filled_qty * order.filled_avg_price
    return math.fsum(f.qty * f.price for f in active if f.status == "final")


def _rows(h: Harness, cid: str) -> list[tuple[str, int, int, str, str]]:
    return [
        (f.fill_key, f.qty, f.cum_qty, f.source, f.status)
        for f in h.store.fills(client_order_id=cid, include_superseded=True)
    ]


def _replay_is_noop(h: Harness, cid: str, **kwargs: object) -> None:
    before = snapshot(h.path)
    result = h.store.ingest_order_fills(cid, **kwargs)  # type: ignore[arg-type]
    assert result.added_qty == 0
    assert result.inserted == ()
    assert result.superseded == ()
    assert snapshot(h.path) == before  # byte-identical rows


@pytest.fixture
def entry(harness: Harness) -> str:
    cid = harness.stage_entry(qty=100)
    harness.send(cid, "broker-1")
    return cid


def test_fill_source_migration_f038(harness: Harness, entry: str) -> None:
    store = harness.store
    # 1. broker cumulative 30, activities unavailable -> one provisional row cid:30
    step1 = {"broker_filled_qty": 30, "broker_avg_price": 100.5, "activities": None, "now": NOW}
    r = store.ingest_order_fills(entry, **step1)  # type: ignore[arg-type]
    assert (r.added_qty, r.inserted, r.superseded, r.local_filled_qty) == (
        30,
        (f"{entry}:30",),
        (),
        30,
    )
    assert _rows(harness, entry) == [(f"{entry}:30", 30, 30, "cumulative", "provisional")]
    _replay_is_noop(harness, entry, **step1)
    # 2. activities recover with A(30): A final, cid:30 superseded, local 30 (added 0)
    step2 = {
        "broker_filled_qty": 30,
        "broker_avg_price": 100.5,
        "activities": [act("A", 30, 100.5, 0)],
        "now": NOW + timedelta(minutes=1),
    }
    r = store.ingest_order_fills(entry, **step2)  # type: ignore[arg-type]
    assert (r.added_qty, r.inserted, r.superseded, r.local_filled_qty) == (
        0,
        ("A",),
        (f"{entry}:30",),
        30,
    )
    _replay_is_noop(harness, entry, **step2)
    # 3. broker cumulative 50 with activity B(20) -> local 50, exactly one superseded row
    step3 = {
        "broker_filled_qty": 50,
        "broker_avg_price": 100.6,
        "activities": [act("A", 30, 100.5, 0), act("B", 20, 100.75, 3)],
        "now": NOW + timedelta(minutes=2),
    }
    r = store.ingest_order_fills(entry, **step3)  # type: ignore[arg-type]
    assert (r.added_qty, r.inserted, r.superseded, r.local_filled_qty) == (20, ("B",), (), 50)
    rows = _rows(harness, entry)
    assert sorted(rows) == sorted(
        [
            (f"{entry}:30", 30, 30, "cumulative", "superseded"),
            ("A", 30, 30, "activity", "final"),
            ("B", 20, 50, "activity", "final"),
        ]
    )
    assert [row[4] for row in rows].count("superseded") == 1
    _replay_is_noop(harness, entry, **step3)
    order = store.get_order(entry)
    assert order is not None
    assert order.filled_qty == 50
    assert order.filled_avg_price == pytest.approx((30 * 100.5 + 20 * 100.75) / 50)
    assert store.active_halts() == ()


def test_activities_above_broker_cumulative_fail_closed_f038(harness: Harness, entry: str) -> None:
    store = harness.store
    store.ingest_order_fills(
        entry, broker_filled_qty=30, broker_avg_price=100.0, activities=None, now=NOW
    )
    before = {t: rows for t, rows in snapshot(harness.path).items() if t != "halts"}
    with pytest.raises(StateStoreError, match="fill_mismatch"):
        store.ingest_order_fills(
            entry,
            broker_filled_qty=50,
            broker_avg_price=100.0,
            activities=[act("A", 30, 100.0, 0), act("C", 30, 100.0, 1)],
            now=NOW + timedelta(minutes=1),
        )
    after = snapshot(harness.path)
    assert {t: rows for t, rows in after.items() if t != "halts"} == before  # nothing written
    (halt,) = store.active_halts()  # recorded in its own transaction after the rollback
    assert halt.kind is HaltKind.RECONCILIATION
    assert halt.created_by == "engine"
    assert halt.reason.startswith("fill_mismatch")


def test_incomplete_activities_f044_f046(harness: Harness, entry: str) -> None:
    store = harness.store
    # 1. activities available but EMPTY, B=40, avg 10.0 -> provisional cid:40, W=40, notional 400
    step1 = {"broker_filled_qty": 40, "broker_avg_price": 10.0, "activities": [], "now": NOW}
    store.ingest_order_fills(entry, **step1)  # type: ignore[arg-type]
    assert _rows(harness, entry) == [(f"{entry}:40", 40, 40, "cumulative", "provisional")]
    order = store.get_order(entry)
    assert order is not None
    assert order.filled_qty == 40
    assert notional(order, store.fills(client_order_id=entry)) == 400.0
    _replay_is_noop(harness, entry, **step1)
    # 2. activities [A(30)], B=50, avg 10.2 -> A final; cid:40 stays; remainder cid:50 (qty 10)
    step2 = {
        "broker_filled_qty": 50,
        "broker_avg_price": 10.2,
        "activities": [act("A", 30, 10.1, 0)],
        "now": NOW + timedelta(minutes=1),
    }
    r = store.ingest_order_fills(entry, **step2)  # type: ignore[arg-type]
    assert r.inserted == ("A", f"{entry}:50")
    assert r.superseded == ()
    assert sorted(_rows(harness, entry)) == sorted(
        [
            (f"{entry}:40", 40, 40, "cumulative", "provisional"),
            ("A", 30, 40, "activity", "final"),
            (f"{entry}:50", 10, 50, "cumulative", "provisional"),
        ]
    )
    order = store.get_order(entry)
    assert order is not None
    assert order.filled_qty == 50
    assert notional(order, store.fills(client_order_id=entry)) == pytest.approx(510.0)
    _replay_is_noop(harness, entry, **step2)
    # 3. activities [A(30), C(20)], B=50 -> C inserted; cid:40 and cid:50 superseded; no new row
    step3 = {
        "broker_filled_qty": 50,
        "broker_avg_price": 10.2,
        "activities": [act("A", 30, 10.1, 0), act("C", 20, 10.35, 2)],
        "now": NOW + timedelta(minutes=2),
    }
    r = store.ingest_order_fills(entry, **step3)  # type: ignore[arg-type]
    assert r.inserted == ("C",)
    assert set(r.superseded) == {f"{entry}:40", f"{entry}:50"}
    assert r.added_qty == 0
    order = store.get_order(entry)
    assert order is not None
    assert order.filled_qty == 50
    active = store.fills(client_order_id=entry)
    assert {f.fill_key for f in active} == {"A", "C"}
    assert notional(order, active) == pytest.approx(30 * 10.1 + 20 * 10.35)
    _replay_is_noop(harness, entry, **step3)
    assert store.active_halts() == ()


def test_filled_qty_decrease_raises_and_halts(harness: Harness, entry: str) -> None:
    store = harness.store
    store.ingest_order_fills(
        entry, broker_filled_qty=30, broker_avg_price=100.0, activities=None, now=NOW
    )
    store.transition_order(
        entry, "partially_filled", now=NOW, event="sync", filled_qty=30, broker_status="x"
    )
    with pytest.raises(StateStoreError, match="filled_qty_decrease"):
        store.transition_order(entry, "partially_filled", now=NOW, event="sync", filled_qty=20)
    with pytest.raises(StateStoreError, match="below local"):
        store.ingest_order_fills(
            entry, broker_filled_qty=20, broker_avg_price=100.0, activities=None, now=NOW
        )
    assert [h.kind for h in store.active_halts()] == [
        HaltKind.RECONCILIATION
    ]  # one row, idempotent
    order = store.get_order(entry)
    assert order is not None
    assert order.filled_qty == 30


def test_replaying_fills_twice_leaves_identical_state(harness: Harness, entry: str) -> None:
    store = harness.store
    inputs = {
        "broker_filled_qty": 100,
        "broker_avg_price": 100.2,
        "activities": [act("A", 60, 100.0, 0), act("B", 40, 100.5, 1)],
        "now": NOW,
    }
    store.ingest_order_fills(entry, **inputs)  # type: ignore[arg-type]
    first = snapshot(harness.path)
    _replay_is_noop(harness, entry, **inputs)
    assert snapshot(harness.path) == first
    store.transition_order(entry, "filled", now=NOW, event="sync", filled_qty=100)
    final = store.get_order(entry)
    assert final is not None
    assert final.status == "filled"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"broker_filled_qty": -1}, "broker_filled_qty"),
        ({"broker_filled_qty": True}, "broker_filled_qty"),
        ({"broker_filled_qty": 101}, "fill_mismatch"),
        ({"broker_avg_price": math.nan}, "broker_avg_price"),
        ({"broker_avg_price": None}, "without an average price"),
        ({"activities": [("A", 1, 1.0)]}, "BrokerFillInput"),
        ({"activities": "A"}, "sequence"),
        ({"activities": [BrokerFillInput("A", 0, 1.0, T0)]}, "activity qty"),
        ({"activities": [BrokerFillInput("A", 1, -1.0, T0)]}, "activity price"),
        ({"activities": [BrokerFillInput("A", 1, 1.0, T0.replace(tzinfo=None))]}, "activity ts"),
        (
            {"activities": [BrokerFillInput("A", 1, 1.0, T0), BrokerFillInput("A", 2, 1.0, T0)]},
            "twice",
        ),
    ],
)
def test_invalid_ingest_inputs_write_nothing(
    harness: Harness, entry: str, kwargs: dict[str, object], match: str
) -> None:
    args: dict[str, object] = {
        "broker_filled_qty": 10,
        "broker_avg_price": 100.0,
        "activities": None,
        "now": NOW,
    }
    args.update(kwargs)
    before = {t: r for t, r in snapshot(harness.path).items() if t != "halts"}
    with pytest.raises(StateStoreError, match=match):
        harness.store.ingest_order_fills(entry, **args)  # type: ignore[arg-type]
    assert {t: r for t, r in snapshot(harness.path).items() if t != "halts"} == before


def test_activity_conflicts_and_unsent_orders(harness: Harness, entry: str) -> None:
    store = harness.store
    store.ingest_order_fills(
        entry,
        broker_filled_qty=10,
        broker_avg_price=100.0,
        activities=[act("A", 10, 100.0, 0)],
        now=NOW,
    )
    with pytest.raises(StateStoreError, match="differs from the stored row"):
        store.ingest_order_fills(
            entry,
            broker_filled_qty=10,
            broker_avg_price=100.0,
            activities=[act("A", 10, 101.0, 0)],
            now=NOW,
        )
    other = harness.stage_entry("AMD", qty=10)
    assert (
        store.ingest_order_fills(
            other, broker_filled_qty=0, broker_avg_price=None, activities=[], now=NOW
        ).local_filled_qty
        == 0
    )  # staged, nothing reported: a no-op
    with pytest.raises(StateStoreError, match="unsent order"):
        store.ingest_order_fills(
            other, broker_filled_qty=5, broker_avg_price=1.0, activities=None, now=NOW
        )
    with pytest.raises(StateStoreError, match="unknown order"):
        store.ingest_order_fills(
            "se1-x", broker_filled_qty=0, broker_avg_price=None, activities=None, now=NOW
        )
