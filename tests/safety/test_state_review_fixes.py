"""Regressions for the Codex review of PR #78 (F-120..F-123; spec §5.1, §5.3, §6.4, §9.4)."""

from __future__ import annotations

import ast
import contextlib
from datetime import date, timedelta
from pathlib import Path

import pytest

import semiedge.state.store as store_module
from semiedge.risk.types import HaltKind
from semiedge.state.models import BrokerFillInput, PositionRecord
from semiedge.state.store import StateStoreError
from state_support import SESSION, T0, Harness, snapshot

pytestmark = pytest.mark.safety

SUBMIT_AT = T0 + timedelta(minutes=10)
CYCLE = 60  # seconds; the caller-supplied monitor interval used as spacing evidence


def _unknown_entry(h: Harness) -> str:
    entry = h.stage_entry()
    h.store.transition_order(entry, "submitting", now=SUBMIT_AT, event="begin_submit")
    h.store.transition_order(entry, "unknown", now=SUBMIT_AT, event="submit_timeout")
    return entry


def _evidence(*offsets_s: int, cycle: object = CYCLE) -> dict[str, object]:
    detail: dict[str, object] = {
        "lookup_404_at": [(SUBMIT_AT + timedelta(seconds=s)).isoformat() for s in offsets_s],
        "absent_from_list_orders_all": True,
    }
    if cycle is not None:
        detail["monitor_cycle_seconds"] = cycle
    return detail


RECOVER_AT = SUBMIT_AT + timedelta(minutes=10)


# ------------------------------------------------------------------ F-120


def test_f120_recovery_refuses_a_supplied_broker_order_id(harness: Harness) -> None:
    entry = _unknown_entry(harness)
    before = snapshot(harness.path)
    with pytest.raises(StateStoreError, match="recovery refused"):
        harness.store.transition_order(
            entry,
            "staged",
            now=RECOVER_AT,
            event="recovered_unsent",
            broker_order_id="known-broker-order",
            detail=_evidence(30, 120),
        )
    assert snapshot(harness.path) == before
    order = harness.store.get_order(entry)
    assert order is not None
    assert (order.status, order.broker_order_id) == ("unknown", None)


@pytest.mark.parametrize(
    "detail",
    [
        _evidence(-300, 120),  # first lookup predates the submission attempt
        _evidence(0, 120),  # not strictly after the submission attempt
        _evidence(30, 120, cycle=None),  # no spacing evidence
        _evidence(30, 60, cycle=CYCLE),  # 30 s apart < one 60 s monitor cycle
        _evidence(30, 120, cycle=0),
        _evidence(30, 120, cycle=True),
        _evidence(30, 120, cycle=float("nan")),
        _evidence(30, 120, cycle="60"),
        _evidence(30, 30, cycle=1e-10),  # F-140: spacing that rounds to zero, equal timestamps
        _evidence(30, 31, cycle=0.5),  # F-140: sub-second monitor cycle is not credible evidence
    ],
)
def test_f120_recovery_needs_post_submission_spaced_evidence(
    harness: Harness, detail: dict[str, object]
) -> None:
    entry = _unknown_entry(harness)
    # a NaN cycle is already refused as non-JSON-safe detail (never persisted)
    with pytest.raises(StateStoreError, match=r"recovery refused|finite"):
        harness.store.transition_order(
            entry, "staged", now=RECOVER_AT, event="recovered_unsent", detail=detail
        )
    order = harness.store.get_order(entry)
    assert order is not None
    assert order.status == "unknown"


def test_f120_valid_recovery_leaves_no_broker_id(harness: Harness) -> None:
    entry = _unknown_entry(harness)
    harness.store.transition_order(
        entry, "staged", now=RECOVER_AT, event="recovered_unsent", detail=_evidence(30, 90)
    )
    order = harness.store.get_order(entry)
    assert order is not None
    assert (order.status, order.broker_order_id, order.submitted_at) == ("staged", None, None)


# ------------------------------------------------------------------ F-121


def test_f121_weighted_average_never_overflows(harness: Harness) -> None:
    entry = harness.stage_entry(qty=100)
    harness.send(entry, "b-1")
    huge = 1.5e308
    harness.store.ingest_order_fills(
        entry,
        broker_filled_qty=2,
        broker_avg_price=huge,
        activities=[BrokerFillInput("A", 2, huge, T0)],
        now=SUBMIT_AT,
    )
    order = harness.store.get_order(entry)  # must stay readable (no inf persisted)
    assert order is not None
    assert order.filled_avg_price == huge


def test_f121_non_finite_derived_split_price_rolls_back(harness: Harness) -> None:
    entry = harness.stage_entry(qty=300)
    harness.store.open_position(
        PositionRecord(
            symbol="NVDA",
            qty=300,
            avg_entry_price=1e308,
            entry_session=SESSION,
            entry_client_order_id=entry,
            entry_reference=100.0,
            atr=2.0,
            stop=96.0,
            target=108.0,
            split_ratio=1.0,
            status="open",
            opened_at=T0,
            closed_at=None,
            updated_at=T0,
        )
    )
    before = snapshot(harness.path)
    with pytest.raises(StateStoreError, match="finite"):
        harness.store.apply_corporate_action_adjustment(
            "NVDA",
            event_id="ev1",
            ex_date=date(2026, 11, 13),
            new_shares=1,
            old_shares=3,
            now=SUBMIT_AT,
        )
    assert snapshot(harness.path) == before


# ------------------------------------------------------------------ F-122


def test_f122_unknown_target_status_records_a_reconciliation_halt(harness: Harness) -> None:
    entry = harness.stage_entry()
    with pytest.raises(StateStoreError, match="illegal_transition"):
        harness.store.transition_order(entry, "done", now=SUBMIT_AT, event="x")  # type: ignore[arg-type]
    assert [h.kind for h in harness.store.active_halts()] == [HaltKind.RECONCILIATION]


def test_f122_halt_survives_an_outer_transaction_that_swallows_it(harness: Harness) -> None:
    entry = harness.stage_entry()
    with harness.store.transaction(), contextlib.suppress(StateStoreError):
        harness.store.transition_order(entry, "bogus", now=SUBMIT_AT, event="x")  # type: ignore[arg-type]
    assert [h.kind for h in harness.store.active_halts()] == [HaltKind.RECONCILIATION]


# ------------------------------------------------------------------ F-123


def _update_orders_sites() -> list[str]:
    """Functions in store.py containing an ``UPDATE orders`` SQL literal."""
    tree = ast.parse(Path(store_module.__file__).read_text(encoding="utf-8"))
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Constant)
                    and isinstance(sub.value, str)
                    and sub.value.lstrip().startswith("UPDATE orders")
                ):
                    sites.append(node.name)
    return sites


def test_f123_orders_rows_have_exactly_one_update_path() -> None:
    assert _update_orders_sites() == ["_apply_transition"]


def test_f123_ingestion_records_filled_qty_through_the_transition_path(harness: Harness) -> None:
    entry = harness.stage_entry(qty=100)
    harness.send(entry, "b-1")
    harness.store.ingest_order_fills(
        entry, broker_filled_qty=40, broker_avg_price=10.0, activities=None, now=SUBMIT_AT
    )
    events = snapshot(harness.path)["order_events"]
    last = events[-1]
    # (event, from_status, to_status, filled_qty, price): status unchanged, fills recorded
    assert (last[3], last[4], last[5], last[7], last[8]) == (
        "fills_ingested",
        "accepted",
        "accepted",
        40,
        10.0,
    )
    # transition_order records the increase consistent with ingested fills (self-edge)
    harness.store.transition_order(
        entry, "partially_filled", now=SUBMIT_AT, event="sync", filled_qty=40
    )
    with pytest.raises(StateStoreError, match="ingested fills"):
        harness.store.transition_order(
            entry, "partially_filled", now=SUBMIT_AT, event="sync", filled_qty=60
        )
    order = harness.store.get_order(entry)
    assert order is not None
    assert (order.status, order.filled_qty) == ("partially_filled", 40)
