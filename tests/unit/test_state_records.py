"""Runs, decisions, positions, halts, reconciliation, equity and the outbox (spec §5.1; #21)."""

from __future__ import annotations

import contextlib
import json
import math
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from semiedge.reporting.signal_report import evaluation_to_dict
from semiedge.risk.sizing import propose_position_size
from semiedge.risk.types import HaltKind, OrderSide
from semiedge.safety.modes import TradingMode
from semiedge.state import halt_control
from semiedge.state.models import (
    Discrepancy,
    DiscrepancyKind,
    EquitySnapshot,
    Notification,
    NotificationCategory,
    PositionRecord,
    ReconciliationReport,
)
from semiedge.state.readonly import ReadOnlyStateStore
from semiedge.state.store import StateStore, StateStoreError
from semiedge.strategy.base import ConditionResult, SignalEvaluation
from state_support import RUN_ID, SESSION, T0, Harness, decision, proposal, snapshot

LATER = T0 + timedelta(minutes=30)


def _evaluation(symbol: str, *, eligible: bool, rank: int | None) -> SignalEvaluation:
    return SignalEvaluation(
        symbol=symbol,
        session=SESSION,
        strategy="momentum_v1",
        strategy_version="1.0.0",
        eligible=eligible,
        conditions=(ConditionResult("breakout", eligible, 1.02, 1.0, "close > 20d high"),),
        score=72.5 if eligible else math.nan,
        score_components={"s_RS": 0.8, "s_BO": math.inf},
        close=100.0,
        atr=2.0,
        proposed_stop=96.0 if eligible else None,
        proposed_target=108.0 if eligible else None,
        rejection_reasons=() if eligible else ("no_breakout",),
        rank=rank,
    )


# ------------------------------------------------------------------ runs, heartbeats, signals


def test_runs_and_heartbeats(harness: Harness) -> None:
    store = harness.store
    with pytest.raises(StateStoreError, match="run kind"):
        store.start_run("r2", "backtest", TradingMode.PAPER, "c", None, T0)
    with pytest.raises(StateStoreError, match="never live"):
        store.start_run("r2", "engine", TradingMode.LIVE, "c", None, T0)
    with pytest.raises(StateStoreError, match="database error"):
        store.start_run(RUN_ID, "engine", TradingMode.PAPER, "c", None, T0)  # duplicate id
    store.start_run("r2", "engine", TradingMode.PAPER, "c" * 64, "abc1234", T0)
    store.finish_run("r2", "ok", "done", LATER)
    with pytest.raises(StateStoreError, match="already finished"):
        store.finish_run("r2", "failed", "again", LATER)
    with pytest.raises(StateStoreError, match="unknown run"):
        store.finish_run("nope", "ok", "", LATER)
    with pytest.raises(StateStoreError, match="run status"):
        store.finish_run(RUN_ID, "running", "", LATER)  # type: ignore[arg-type]
    store.heartbeat("engine", "ok", "loop", T0)
    store.heartbeat("engine", "degraded", "broker slow", LATER)
    store.heartbeat("notifier", "ok", "", T0)
    reader = ReadOnlyStateStore(harness.path)
    try:
        assert reader.heartbeats() == (
            ("engine", LATER, "degraded", "broker slow"),
            ("notifier", T0, "ok", ""),
        )
        assert reader.latest_run("engine") == ("r2", "ok", T0, LATER, "done")
        assert reader.latest_run("monitor") is None
        latest = reader.latest_run("post_close")
        assert latest is not None
        assert latest[:2] == (RUN_ID, "running")
        assert latest[3] is None
    finally:
        reader.close()


def test_record_signals_uses_the_report_conversion(harness: Harness) -> None:
    evaluations = [
        _evaluation("NVDA", eligible=True, rank=1),
        _evaluation("AMD", eligible=False, rank=None),
    ]
    sizes = {
        "NVDA": propose_position_size(
            100_000.0, 100.0, 96.0, max_risk_per_trade_pct=0.005, max_position_pct=0.1
        )
    }
    harness.store.record_signals(RUN_ID, SESSION, evaluations, sizes=sizes)
    reader = ReadOnlyStateStore(harness.path)
    try:
        frame = reader.signals(RUN_ID)
    finally:
        reader.close()
    assert list(frame["symbol"]) == ["NVDA", "AMD"]
    assert list(frame["eligible"]) == [1, 0]
    assert frame["score"].iloc[0] == 72.5
    assert math.isnan(frame["score"].iloc[1])  # NULL, never NaN persisted
    for row, ev in zip(frame.itertuples(), evaluations, strict=True):
        expected = json.dumps(
            evaluation_to_dict(ev, sizes.get(ev.symbol)), allow_nan=False, sort_keys=True
        )
        assert row.payload_json == expected
    assert b"NaN" not in harness.path.read_bytes()
    assert b"Infinity" not in harness.path.read_bytes()


@pytest.mark.parametrize(
    ("evaluations", "sizes", "match"),
    [
        (["x"], {}, "SignalEvaluation"),
        ([_evaluation("NVDA", eligible=True, rank=1)] * 2, {}, "duplicate"),
        ([_evaluation("NVDA", eligible=True, rank=1)], {"AMD": None}, "not evaluated"),
        ([_evaluation("NVDA", eligible=True, rank=1)], {"NVDA": "big"}, "SizingResult"),
        ([_evaluation("nv", eligible=True, rank=1)], {}, "invalid symbol"),
    ],
)
def test_record_signals_rejects_bad_input(
    harness: Harness, evaluations: list[object], sizes: dict[str, object], match: str
) -> None:
    with pytest.raises(StateStoreError, match=match):
        harness.store.record_signals(RUN_ID, SESSION, evaluations, sizes=sizes)  # type: ignore[arg-type]


def test_signals_need_an_existing_run(harness: Harness) -> None:
    with pytest.raises(StateStoreError, match="unknown run"):
        harness.store.record_signals("missing", SESSION, [], sizes={})
    with pytest.raises(StateStoreError, match="unknown run"):
        harness.store.record_risk_decisions("missing", [])


def test_risk_decisions_round_trip(harness: Harness) -> None:
    approved = decision(proposal("NVDA", 100))
    rejected = replace(
        decision(proposal("AMD", 0), approved=False),
        proposal=replace(proposal("AMD", 0), score=math.nan),
    )
    ids = harness.store.record_risk_decisions(RUN_ID, [approved, rejected])
    assert len(ids) == 2
    assert ids[1] == ids[0] + 1
    assert harness.store.risk_decision(ids[0]) == approved
    back = harness.store.risk_decision(ids[1])
    assert math.isnan(back.proposal.score)  # non-finite input recorded as null (rejection)
    assert replace(back, proposal=replace(back.proposal, score=0.0)) == replace(
        rejected, proposal=replace(rejected.proposal, score=0.0)
    )
    with pytest.raises(StateStoreError, match="unknown risk decision"):
        harness.store.risk_decision(999)
    with pytest.raises(StateStoreError, match="inconsistent approved"):
        harness.store.record_risk_decisions(RUN_ID, [replace(approved, approved_shares=101)])
    with pytest.raises(StateStoreError, match="inconsistent approved"):
        harness.store.record_risk_decisions(
            RUN_ID, [replace(approved, proposal=replace(approved.proposal, stop=math.inf))]
        )
    with pytest.raises(StateStoreError, match="OrderSide"):
        harness.store.record_risk_decisions(
            RUN_ID,
            [replace(approved, proposal=replace(approved.proposal, side="buy"))],  # type: ignore[arg-type]
        )


# ------------------------------------------------------------------ positions


def _position(entry: str, **changes: object) -> PositionRecord:
    base = PositionRecord(
        symbol="NVDA",
        qty=100,
        avg_entry_price=100.0,
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
    return replace(base, **changes)  # type: ignore[arg-type]


def test_position_lifecycle(harness: Harness) -> None:
    entry = harness.stage_entry()
    store = harness.store
    for changes, match in [
        ({"split_ratio": 2.0}, "split_ratio"),
        ({"status": "exiting"}, "must be open"),
        ({"qty": 0}, "qty"),
        ({"stop": math.nan}, "stop"),
        ({"entry_client_order_id": "se1-nope"}, "entry order"),
    ]:
        with pytest.raises(StateStoreError, match=match):
            store.open_position(_position(entry, **changes))
    store.open_position(_position(entry))
    with pytest.raises(StateStoreError, match="already open"):
        store.open_position(_position(entry))
    with pytest.raises(StateStoreError, match="at least one field"):
        store.update_position("NVDA", now=LATER)
    store.update_position("NVDA", now=LATER, stop=97.0, status="exiting")
    store.update_position("NVDA", now=LATER, status="open")
    with pytest.raises(StateStoreError, match="closed exactly when qty is 0"):
        store.update_position("NVDA", now=LATER, status="closed")
    with pytest.raises(StateStoreError, match="closed exactly when qty is 0"):
        store.update_position("NVDA", now=LATER, qty=0)
    store.update_position("NVDA", now=LATER, qty=0, status="closed")
    assert store.positions() == ()
    (closed,) = store.positions(include_closed=True)
    assert (closed.status, closed.qty, closed.stop, closed.closed_at) == ("closed", 0, 97.0, LATER)
    with pytest.raises(StateStoreError, match="is closed"):
        store.update_position("NVDA", now=LATER, stop=1.0)
    store.open_position(_position(entry, opened_at=LATER, updated_at=LATER))  # reopen after close
    (reopened,) = store.positions()
    assert (reopened.qty, reopened.status, reopened.opened_at) == (100, "open", LATER)
    with pytest.raises(StateStoreError, match="no position"):
        store.update_position("AMD", now=LATER, qty=1)


# ------------------------------------------------------------------ halts


def test_set_halt_is_idempotent_per_kind(harness: Harness) -> None:
    store = harness.store
    first = store.set_halt(HaltKind.DRAWDOWN, "dd 12%", "engine", T0)
    assert store.set_halt(HaltKind.DRAWDOWN, "dd 13%", "engine", LATER) == first
    other = store.set_halt(HaltKind.DATA, "stale", "engine", T0)
    assert [h.id for h in store.active_halts()] == [first, other]
    (dd, _data) = store.active_halts()
    assert dd.as_halt().kind is HaltKind.DRAWDOWN
    assert dd.as_halt().since == T0
    assert dd.reason == "dd 12%"
    for actor in ("dashboard:owner", "cli:", "cli:a b", "", "owner"):
        with pytest.raises(StateStoreError, match="actor"):
            store.set_halt(HaltKind.MANUAL, "x", actor, T0)
    with pytest.raises(StateStoreError, match="HaltKind"):
        store.set_halt("manual", "x", "engine", T0)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", list(HaltKind))
def test_clear_halt_actor_rules(harness: Harness, kind: HaltKind) -> None:
    store = harness.store
    halt_id = store.set_halt(kind, "reason", "engine", T0)
    if kind in (HaltKind.DAILY_LOSS, HaltKind.DATA):
        store.clear_halt(halt_id, "engine", LATER)
    else:
        with pytest.raises(StateStoreError, match="may not clear"):
            store.clear_halt(halt_id, "engine", LATER)
        store.clear_halt(halt_id, "cli:owner", LATER)
    assert store.active_halts() == ()
    with pytest.raises(StateStoreError, match="not an active halt"):
        store.clear_halt(halt_id, "cli:owner", LATER)


def test_halt_control_works_while_the_writer_runs(harness: Harness) -> None:
    path = harness.path
    halt_id = halt_control.request_manual_halt(path, "owner pause", "owner", T0)
    assert halt_control.request_manual_halt(path, "again", "owner", LATER) == halt_id
    (row,) = snapshot(path)["halts"]
    assert row[1:5] == (
        "manual",
        "owner pause",
        T0.isoformat(timespec="microseconds"),
        "dashboard:owner",
    )
    assert [h.id for h in harness.store.active_halts()] == [halt_id]  # never cached
    for bad in ("cli:owner", "", "a b", "dashboard:x"):
        with pytest.raises(StateStoreError, match="bare name"):
            halt_control.request_manual_halt(path, "x", bad, T0)
    with pytest.raises(StateStoreError, match="reason"):
        halt_control.request_manual_halt(path, "", "owner", T0)
    harness.store.set_halt(HaltKind.DRAWDOWN, "dd", "engine", T0)
    for active in harness.store.active_halts():
        halt_control.clear_halt(path, active.id, "owner", LATER)
    assert harness.store.active_halts() == ()
    halts = snapshot(path)["halts"]
    assert {r[6] for r in halts} == {"cli:owner"}
    with pytest.raises(StateStoreError, match="not an active halt"):
        halt_control.clear_halt(path, halt_id, "owner", LATER)
    with pytest.raises(StateStoreError, match="halt_id"):
        halt_control.clear_halt(path, True, "owner", LATER)  # type: ignore[arg-type]


# ------------------------------------------------------------------ reconciliation, equity


def test_reconciliation_round_trip(harness: Harness) -> None:
    store = harness.store
    assert store.last_reconciliation() is None
    clean = ReconciliationReport(T0, T0 + timedelta(seconds=3), "clean", ())
    halted = ReconciliationReport(
        LATER,
        LATER + timedelta(seconds=2),
        "halted",
        (
            Discrepancy(DiscrepancyKind.FOREIGN_ORDER, "NVDA", "xyz", "-", "open", "halt"),
            Discrepancy(
                DiscrepancyKind.MISSING_PROTECTION, "AMD", None, "open", "-", "action_required"
            ),
        ),
    )
    store.record_reconciliation(clean)
    store.record_reconciliation(halted)
    assert store.last_reconciliation() == halted
    reader = ReadOnlyStateStore(harness.path)
    try:
        assert reader.reconciliation_history(5) == (halted, clean)
        assert reader.reconciliation_history(1) == (halted,)
        with pytest.raises(ValueError, match="limit"):
            reader.reconciliation_history(0)
    finally:
        reader.close()
    with pytest.raises(StateStoreError, match="finished before"):
        store.record_reconciliation(ReconciliationReport(LATER, T0, "clean", ()))
    with pytest.raises(StateStoreError, match="outcome"):
        store.record_reconciliation(ReconciliationReport(T0, T0, "fine", ()))  # type: ignore[arg-type]
    with pytest.raises(StateStoreError, match="Discrepancy"):
        store.record_reconciliation(ReconciliationReport(T0, T0, "clean", ("x",)))  # type: ignore[arg-type]


def test_equity_snapshots_and_close_peak(harness: Harness) -> None:
    store = harness.store
    assert store.peak_close_equity() is None
    for minutes, equity, source in [
        (0, 100_000.0, "broker_close"),
        (60, 104_000.0, "broker_intraday"),
        (120, 101_500.0, "broker_close"),
    ]:
        store.record_equity_snapshot(
            EquitySnapshot(
                T0 + timedelta(minutes=minutes),
                SESSION,
                equity,
                99_000.0,
                50_000.0,
                equity - 50_000.0,
                source,
            )  # type: ignore[arg-type]
        )
    assert store.peak_close_equity() == 101_500.0  # intraday values never set the peak
    with pytest.raises(StateStoreError, match="database error"):
        store.record_equity_snapshot(
            EquitySnapshot(T0, SESSION, 1.0, 1.0, 1.0, 0.0, "broker_close")
        )
    with pytest.raises(StateStoreError, match="finite"):
        store.record_equity_snapshot(
            EquitySnapshot(LATER, SESSION, math.nan, 1.0, 1.0, 0.0, "broker_close")
        )
    with pytest.raises(StateStoreError, match="source"):
        store.record_equity_snapshot(
            EquitySnapshot(LATER, SESSION, 1.0, 1.0, 1.0, 0.0, "guess")  # type: ignore[arg-type]
        )
    reader = ReadOnlyStateStore(harness.path)
    try:
        frame = reader.equity_snapshots()
    finally:
        reader.close()
    assert list(frame["equity"]) == [100_000.0, 104_000.0, 101_500.0]


# ------------------------------------------------------------------ outbox


def test_notifications_refuse_live_and_cap_lengths() -> None:
    with pytest.raises(ValueError, match=r"never|only"):
        Notification(NotificationCategory.SYSTEM, TradingMode.LIVE, "t", "b", T0)
    with pytest.raises(ValueError, match="only"):
        Notification(NotificationCategory.SYSTEM, "paper", "t", "b", T0)  # type: ignore[arg-type]
    n = Notification(NotificationCategory.FILL, TradingMode.PAPER, "t" * 500, "b" * 5000, T0)
    assert (len(n.title), len(n.body)) == (200, 1800)


def test_outbox_flow(harness: Harness) -> None:
    store = harness.store
    first = store.enqueue_notification(
        Notification(NotificationCategory.FILL, TradingMode.PAPER, "Filled", "NVDA 100", T0)
    )
    second = store.enqueue_notification(
        Notification(NotificationCategory.HALT, TradingMode.PAPER, "Halt", "manual", LATER)
    )
    due = store.due_notifications(T0, 10)
    assert [(i, n.title, a) for i, n, a in due] == [(first, "Filled", 0)]
    assert due[0][1].mode is TradingMode.PAPER
    store.mark_notification(first, sent_at=None, error="timeout", next_attempt_at=LATER)
    store.mark_notification(
        second, sent_at=None, error="webhook_not_configured", next_attempt_at=None
    )
    assert [(i, a) for i, _n, a in store.due_notifications(LATER, 10)] == [(first, 1), (second, 1)]
    assert len(store.due_notifications(LATER, 1)) == 1
    reader = ReadOnlyStateStore(harness.path)
    try:
        errors = reader.outbox_errors(10)
    finally:
        reader.close()
    assert list(errors["last_error"]) == ["webhook_not_configured", "timeout"]
    store.mark_notification(first, sent_at=LATER, error=None, next_attempt_at=None)
    assert [i for i, _n, _a in store.due_notifications(LATER, 10)] == [second]
    with pytest.raises(StateStoreError, match="already sent"):
        store.mark_notification(first, sent_at=LATER, error=None, next_attempt_at=None)
    with pytest.raises(StateStoreError, match="exactly one"):
        store.mark_notification(second, sent_at=LATER, error="x", next_attempt_at=None)
    with pytest.raises(StateStoreError, match="no next attempt"):
        store.mark_notification(second, sent_at=LATER, error=None, next_attempt_at=LATER)
    with pytest.raises(StateStoreError, match="limit"):
        store.due_notifications(LATER, 0)


# ------------------------------------------------------------------ transactions


def test_nested_transactions_are_atomic_per_call(
    harness: Harness, open_store: Callable[..., StateStore], db_path: Path
) -> None:
    store = harness.store
    entry = harness.stage_entry()
    with store.transaction():
        store.set_halt(HaltKind.DATA, "stale", "engine", T0)
        with pytest.raises(StateStoreError):
            store.insert_order_intent(
                replace(store.get_order(entry).intent, client_order_id="dup")  # type: ignore[union-attr]
            )
        store.heartbeat("engine", "ok", "", T0)
    # the outer transaction committed what succeeded; the failed call left nothing behind
    assert [h.kind for h in store.active_halts()] == [HaltKind.DATA]
    assert [o.intent.client_order_id for o in store.orders()] == [entry]

    def failing_unit() -> None:
        with store.transaction():
            store.set_halt(HaltKind.BROKER, "x", "engine", T0)
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        failing_unit()
    assert [h.kind for h in store.active_halts()] == [HaltKind.DATA]


def test_swallowed_halting_error_still_records_the_halt(harness: Harness) -> None:
    entry = harness.stage_entry()
    store = harness.store
    with store.transaction(), contextlib.suppress(StateStoreError):
        store.transition_order(entry, "filled", now=LATER, event="bad")
    (halt,) = store.active_halts()
    assert (halt.kind, halt.created_by) == (HaltKind.RECONCILIATION, "engine")
    assert "illegal_transition" in halt.reason
    order = store.get_order(entry)
    assert order is not None
    assert order.status == "staged"
    assert OrderSide.BUY is order.intent.side
