"""Order intents and the §6.4 state machine at the store level (spec §5.1, §5.3, §6.4; #21)."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from semiedge.risk.types import HaltKind, OrderSide
from semiedge.state.models import OrderIntent
from semiedge.state.order_status import ALLOWED
from semiedge.state.store import StateStoreError
from state_support import RUN_ID, SESSION, T0, Harness, cid, decision, proposal, snapshot

pytestmark = pytest.mark.safety

LATER = T0 + timedelta(minutes=10)


def _entry_intent(h: Harness, **changes: object) -> OrderIntent:
    (decision_id,) = h.store.record_risk_decisions(RUN_ID, [decision(proposal("AMD", 10))])
    base = OrderIntent(
        client_order_id=cid("entry", "AMD"),
        kind="entry",
        symbol="AMD",
        side=OrderSide.BUY,
        qty=10,
        order_type="market",
        time_in_force="opg",
        order_class="simple",
        limit_price=None,
        stop_price=None,
        decision_session=SESSION,
        valid_until=T0 + timedelta(hours=1),
        risk_decision_id=decision_id,
        parent_client_order_id=None,
        created_at=T0,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_insert_writes_staged_row_and_event(harness: Harness) -> None:
    client_id = harness.stage_entry()
    order = harness.store.get_order(client_id)
    assert order is not None
    assert (order.status, order.filled_qty, order.legs_json, order.broker_order_id) == (
        "staged",
        0,
        "[]",
        None,
    )
    events = snapshot(harness.path)["order_events"]
    assert [(e[1], e[3], e[4], e[5]) for e in events] == [(client_id, "staged", None, "staged")]
    assert harness.store.get_order("missing") is None


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"qty": 0}, "qty"),
        ({"qty": -5}, "qty"),
        ({"qty": 10.0}, "qty"),
        ({"qty": 9}, "approved_shares"),
        ({"risk_decision_id": None}, "risk_decision_id"),
        ({"risk_decision_id": 999}, "unknown risk decision"),
        ({"side": OrderSide.SELL}, "BUY"),
        ({"time_in_force": "day"}, "opening auction"),
        ({"order_type": "limit", "limit_price": 99.0}, "opening auction"),
        ({"symbol": "NVDA"}, "differ from its decision"),
        ({"decision_session": SESSION - timedelta(days=1)}, "differ from its decision"),
        ({"valid_until": T0.replace(tzinfo=None)}, "valid_until"),
        ({"client_order_id": "has space"}, "client_order_id"),
        ({"kind": "bracket"}, "kind"),
        ({"parent_client_order_id": "se1-x"}, "no parent"),
        ({"limit_price": float("nan")}, "limit_price"),
    ],
)
def test_invalid_entry_intents_are_refused(
    harness: Harness, changes: dict[str, object], match: str
) -> None:
    intent = _entry_intent(harness, **changes)
    before = snapshot(harness.path)
    with pytest.raises(StateStoreError, match=match):
        harness.store.insert_order_intent(intent)
    assert snapshot(harness.path) == before


def test_rejected_decision_cannot_back_an_entry(harness: Harness) -> None:
    (rejected,) = harness.store.record_risk_decisions(
        RUN_ID, [decision(proposal("AMD", 10), approved=False)]
    )
    with pytest.raises(StateStoreError, match="rejected risk decision"):
        harness.store.insert_order_intent(_entry_intent(harness, risk_decision_id=rejected, qty=10))


def test_check_constraints_reject_bad_rows_at_the_sql_level(harness: Harness) -> None:
    """§5.4: CHECKs reject negative qty, filled_qty > qty and an entry without a decision."""
    conn = harness.store._conn
    columns = (
        "client_order_id, symbol, side, kind, order_type, time_in_force, order_class, qty, "
        "decision_session, valid_until, status, filled_qty, created_at, updated_at, "
        "risk_decision_id"
    )
    sql = f"INSERT INTO orders ({columns}) VALUES (?, 'NVDA', 'buy', 'entry', 'market', 'opg', 'simple', ?, '2026-11-12', 'x', 'staged', ?, 'x', 'x', ?)"  # noqa: E501, S608
    harness.stage_entry("AVGO", qty=1)
    decision_id = harness.decisions["AVGO"]
    for qty, filled, decision_id_value in (
        (-1, 0, decision_id),
        (10, 11, decision_id),
        (10, 0, None),
    ):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(sql, ("raw-1", qty, filled, decision_id_value))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute(sql, ("raw-2", 10, 0, 12345))


def test_one_non_terminal_order_per_symbol_and_kind(harness: Harness) -> None:
    entry = harness.stage_entry()
    harness.stage_sell("flatten", entry, qty=100)
    with pytest.raises(StateStoreError, match="non-terminal flatten"):
        harness.stage_sell("flatten", entry, qty=100, attempt=2)
    with pytest.raises(StateStoreError, match="a previous attempt is not terminal"):
        harness.store.next_attempt("flatten", SESSION, "NVDA")
    harness.store.transition_order(cid("flatten"), "withdrawn", now=LATER, event="withdraw")
    assert harness.store.next_attempt("flatten", SESSION, "NVDA") == 2
    harness.stage_sell("flatten", entry, qty=100, attempt=2)
    assert harness.store.next_attempt("protection", SESSION, "NVDA") == 1


def test_entries_are_never_retried(harness: Harness) -> None:
    entry = harness.stage_entry()
    harness.store.transition_order(entry, "withdrawn", now=LATER, event="withdraw")
    with pytest.raises(StateStoreError, match="never retried"):
        harness.store.next_attempt("entry", SESSION, "NVDA")
    with pytest.raises(StateStoreError, match="once per decision"):
        harness.stage_entry()


def test_sell_intents_need_their_entry(harness: Harness) -> None:
    entry = harness.stage_entry()
    with pytest.raises(StateStoreError, match="entry order of the same symbol"):
        harness.stage_sell("flatten", "se1-unknown")
    other = harness.stage_entry("AMD", qty=10)
    with pytest.raises(StateStoreError, match="entry order of the same symbol"):
        harness.stage_sell("flatten", other)
    harness.stage_sell("protection", entry)
    order = harness.store.get_order(cid("protection"))
    assert order is not None
    assert (order.intent.limit_price, order.intent.stop_price) == (108.0, 96.0)


def test_exhaustive_transition_matrix(harness: Harness) -> None:
    """Every (from, to) pair not in ALLOWED is refused with the row unchanged."""
    entry = harness.stage_entry()
    conn = harness.store._conn
    statuses = sorted(ALLOWED)
    refused = 0
    for current in statuses:
        for target in statuses:
            if target in ALLOWED[current]:
                continue
            conn.execute("UPDATE orders SET status = ? WHERE client_order_id = ?", (current, entry))
            with pytest.raises(StateStoreError, match="illegal_transition"):
                harness.store.transition_order(entry, target, now=LATER, event="probe")  # type: ignore[arg-type]
            order = harness.store.get_order(entry)
            assert order is not None
            assert order.status == current
            refused += 1
    assert refused == len(statuses) ** 2 - sum(len(v) for v in ALLOWED.values())
    assert [h.kind for h in harness.store.active_halts()] == [HaltKind.RECONCILIATION]


def test_begin_submit_backstops(harness: Harness) -> None:
    entry = harness.stage_entry()
    with pytest.raises(StateStoreError, match="past valid_until"):
        harness.store.transition_order(
            entry, "submitting", now=T0 + timedelta(hours=2), event="begin_submit"
        )
    harness.store.set_halt(HaltKind.MANUAL, "owner pause", "cli:owner", T0)
    with pytest.raises(StateStoreError, match="entries are halted"):
        harness.store.transition_order(entry, "submitting", now=LATER, event="begin_submit")
    order = harness.store.get_order(entry)
    assert order is not None
    assert order.status == "staged"
    # exits are never gated by halts (§2.4): a staged flatten can still be sent
    harness.stage_sell("flatten", entry)
    harness.store.transition_order(cid("flatten"), "submitting", now=LATER, event="begin_submit")


def test_only_one_sent_sell_per_symbol(harness: Harness) -> None:
    entry = harness.stage_entry()
    protection = harness.stage_sell("protection", entry)
    time_exit = harness.stage_sell("time_exit", entry)  # staged may coexist with protection
    harness.send(protection, "b-prot")
    with pytest.raises(StateStoreError, match="one sent sell at a time"):
        harness.store.transition_order(time_exit, "submitting", now=LATER, event="begin_submit")
    harness.store.transition_order(protection, "cancel_pending", now=LATER, event="cancel")
    with pytest.raises(StateStoreError, match="one sent sell at a time"):
        harness.store.transition_order(time_exit, "submitting", now=LATER, event="begin_submit")
    harness.store.transition_order(protection, "canceled", now=LATER, event="cancel_confirmed")
    harness.store.transition_order(time_exit, "submitting", now=LATER, event="begin_submit")


def test_self_transitions_only_record_new_fills_or_legs(harness: Harness) -> None:
    entry = harness.stage_entry()
    harness.send(entry, "b-1")
    with pytest.raises(StateStoreError, match="no new fills or legs"):
        harness.store.transition_order(entry, "accepted", now=LATER, event="sync")
    harness.store.ingest_order_fills(
        entry, broker_filled_qty=30, broker_avg_price=100.0, activities=None, now=LATER
    )
    harness.store.transition_order(entry, "partially_filled", now=LATER, event="sync")
    with pytest.raises(StateStoreError, match="no new fills or legs"):
        harness.store.transition_order(entry, "partially_filled", now=LATER, event="sync")
    harness.store.ingest_order_fills(
        entry, broker_filled_qty=60, broker_avg_price=100.0, activities=None, now=LATER
    )
    harness.store.transition_order(
        entry, "partially_filled", now=LATER, event="sync", filled_qty=60, filled_avg_price=100.0
    )
    harness.store.transition_order(
        entry, "partially_filled", now=LATER, event="legs", legs_json='[{"id": "x"}]'
    )
    with pytest.raises(StateStoreError, match="not the ingested local cumulative"):
        harness.store.transition_order(entry, "filled", now=LATER, event="sync", filled_qty=100)
    with pytest.raises(StateStoreError, match="cannot mark filled"):
        harness.store.transition_order(entry, "filled", now=LATER, event="sync")
    with pytest.raises(StateStoreError, match="filled_avg_price differs"):
        harness.store.transition_order(
            entry, "canceled", now=LATER, event="sync", filled_avg_price=101.0
        )
    with pytest.raises(StateStoreError, match="JSON list"):
        harness.store.transition_order(entry, "canceled", now=LATER, event="x", legs_json="{}")
    harness.store.transition_order(entry, "canceled", now=LATER, event="expired_partial")
    order = harness.store.get_order(entry)
    assert order is not None
    assert (order.status, order.filled_qty, order.legs_json) == ("canceled", 60, '[{"id": "x"}]')


def test_broker_order_id_is_set_once_and_unique(harness: Harness) -> None:
    first = harness.stage_entry()
    second = harness.stage_entry("AMD", qty=10)
    harness.send(first, "b-1")
    harness.store.transition_order(second, "submitting", now=LATER, event="begin_submit")
    with pytest.raises(StateStoreError, match="broker_order_id_mismatch"):
        harness.store.transition_order(
            first, "partially_filled", now=LATER, event="x", broker_order_id="b-2"
        )
    with pytest.raises(StateStoreError, match="already belongs to"):
        harness.store.transition_order(
            second, "accepted", now=LATER, event="x", broker_order_id="b-1"
        )
    assert len(harness.store.active_halts()) == 1


def test_recovery_exception_unknown_to_staged(harness: Harness) -> None:
    entry = harness.stage_entry()
    store = harness.store
    store.transition_order(entry, "submitting", now=LATER, event="begin_submit")
    store.transition_order(entry, "unknown", now=LATER, event="submit_timeout")
    good = {
        "lookup_404_at": [LATER.isoformat(), (LATER + timedelta(minutes=5)).isoformat()],
        "absent_from_list_orders_all": True,
    }
    when = LATER + timedelta(minutes=6)
    bad_details = [
        {"lookup_404_at": [LATER.isoformat()], "absent_from_list_orders_all": True},
        {**good, "absent_from_list_orders_all": False},
        {**good, "lookup_404_at": [LATER.isoformat(), LATER.isoformat()]},
        {**good, "lookup_404_at": ["garbage", LATER.isoformat()]},
        {**good, "lookup_404_at": [LATER.isoformat(), (when + timedelta(hours=1)).isoformat()]},
    ]
    for detail in bad_details:
        with pytest.raises(StateStoreError, match="recovery"):
            store.transition_order(
                entry, "staged", now=when, event="recovered_unsent", detail=detail
            )
    with pytest.raises(StateStoreError, match="recovered_unsent"):
        store.transition_order(entry, "staged", now=when, event="other", detail=good)
    with pytest.raises(StateStoreError, match="past valid_until"):
        store.transition_order(
            entry, "staged", now=T0 + timedelta(hours=2), event="recovered_unsent", detail=good
        )
    order = store.get_order(entry)
    assert order is not None
    assert order.status == "unknown"
    store.transition_order(entry, "staged", now=when, event="recovered_unsent", detail=good)
    order = store.get_order(entry)
    assert order is not None
    assert (order.status, order.submitted_at, order.intent.client_order_id) == (
        "staged",
        None,
        entry,
    )
    # the same client id goes out again; a fill row blocks any later recovery
    store.transition_order(entry, "submitting", now=when, event="begin_submit")
    store.transition_order(entry, "unknown", now=when, event="submit_timeout")
    store.ingest_order_fills(
        entry, broker_filled_qty=5, broker_avg_price=100.0, activities=None, now=when
    )
    with pytest.raises(StateStoreError, match="recovery refused"):
        store.transition_order(entry, "staged", now=when, event="recovered_unsent", detail=good)


def test_events_record_every_transition_and_errors(harness: Harness) -> None:
    entry = harness.stage_entry()
    harness.send(entry, "b-1")
    harness.store.transition_order(
        entry,
        "rejected",
        now=LATER,
        event="rejected",
        broker_status="rejected",
        detail={"error": "insufficient buying power", "at": LATER, "side": OrderSide.BUY},
    )
    order = harness.store.get_order(entry)
    assert order is not None
    assert order.last_error == "insufficient buying power"
    events = snapshot(harness.path)["order_events"]
    assert [(e[3], e[4], e[5]) for e in events] == [
        ("staged", None, "staged"),
        ("begin_submit", "staged", "submitting"),
        ("submit_ok", "submitting", "accepted"),
        ("rejected", "accepted", "rejected"),
    ]
    assert '"side": "buy"' in events[-1][9]
    with pytest.raises(StateStoreError, match="JSON-safe"):
        harness.store.transition_order(
            harness.stage_entry("AMD", qty=10),
            "withdrawn",
            now=LATER,
            event="x",
            detail={"obj": object()},
        )
    with pytest.raises(StateStoreError, match="finite"):
        harness.store.transition_order(
            cid("entry", "AMD"), "withdrawn", now=LATER, event="x", detail={"v": float("inf")}
        )
    with pytest.raises(StateStoreError, match="unknown order status"):
        harness.store.transition_order(cid("entry", "AMD"), "done", now=LATER, event="x")  # type: ignore[arg-type]
