"""Verified split adjustments while holding (spec §9.4, §5.4 F-039/F-045; issue #21)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

import pytest

from semiedge.state.models import PositionRecord
from semiedge.state.store import StateStore, StateStoreError
from state_support import SESSION, T0, Harness, snapshot

pytestmark = pytest.mark.integration

EX_DATE = date(2026, 11, 13)
NOW = T0 + timedelta(minutes=10)


def _open(h: Harness, qty: int, *, price: float = 100.0, stop: float = 96.0) -> None:
    entry = h.stage_entry(qty=qty)
    h.store.open_position(
        PositionRecord(
            symbol="NVDA",
            qty=qty,
            avg_entry_price=price,
            entry_session=SESSION,
            entry_client_order_id=entry,
            entry_reference=100.0,
            atr=2.0,
            stop=stop,
            target=108.0,
            split_ratio=1.0,
            status="open",
            opened_at=T0,
            closed_at=None,
            updated_at=T0,
        )
    )


def _apply(h: Harness, new: int, old: int, event_id: str = "ev1") -> bool:
    return h.store.apply_corporate_action_adjustment(
        "NVDA", event_id=event_id, ex_date=EX_DATE, new_shares=new, old_shares=old, now=NOW
    )


def test_forward_split_applies_once_f039(
    harness: Harness, db_path: Path, open_store: Callable[..., StateStore]
) -> None:
    _open(harness, 100)
    assert _apply(harness, 4, 1) is True
    (p,) = harness.store.positions()
    assert (p.qty, p.avg_entry_price, p.stop, p.target, p.split_ratio) == (
        400,
        25.0,
        24.0,
        27.0,
        4.0,
    )
    assert (p.entry_reference, p.atr) == (100.0, 2.0)  # audit values keep their basis
    (adj,) = harness.store.corporate_action_adjustments(symbol="NVDA")
    assert (adj.qty_before, adj.qty_after, adj.new_shares, adj.old_shares) == (100, 400, 4, 1)
    assert adj.applied_at == NOW
    before = snapshot(db_path)
    assert _apply(harness, 4, 1) is False
    assert snapshot(db_path) == before
    harness.store.close()
    reopened = open_store()
    assert (
        reopened.apply_corporate_action_adjustment(
            "NVDA", event_id="ev1", ex_date=EX_DATE, new_shares=4, old_shares=1, now=NOW
        )
        is False
    )
    assert snapshot(db_path) == before
    with pytest.raises(StateStoreError, match="different terms"):
        reopened.apply_corporate_action_adjustment(
            "NVDA", event_id="ev1", ex_date=EX_DATE, new_shares=5, old_shares=1, now=NOW
        )


def test_non_integer_result_is_refused_f039(harness: Harness, db_path: Path) -> None:
    _open(harness, 101)
    before = snapshot(db_path)
    with pytest.raises(StateStoreError, match="not a whole number"):
        _apply(harness, 3, 2)
    assert snapshot(db_path) == before


def test_reverse_split_is_exact_f045(harness: Harness) -> None:
    _open(harness, 300, price=10.1, stop=9.7)
    assert _apply(harness, 1, 3) is True
    (p,) = harness.store.positions()
    assert p.qty == 100
    # prices x 3 with one rounding of the exact rational (no float round-trip through 1/3)
    assert p.avg_entry_price == float(3 * Fraction(10.1))
    assert p.stop == float(3 * Fraction(9.7))
    assert p.target == 324.0
    assert p.split_ratio == float(Fraction(1, 3))


def test_reverse_split_non_integer_qty_is_refused_f045(harness: Harness, db_path: Path) -> None:
    _open(harness, 100)
    before = snapshot(db_path)
    with pytest.raises(StateStoreError, match="not a whole number"):
        _apply(harness, 1, 3)
    assert snapshot(db_path) == before
    assert harness.store.corporate_action_adjustments() == ()


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"new_shares": 1, "old_shares": 1}, "differ"),
        ({"new_shares": 0}, "new_shares"),
        ({"old_shares": -2}, "old_shares"),
        ({"new_shares": True}, "new_shares"),
        ({"event_id": ""}, "event_id"),
        ({"ex_date": SESSION}, "after the entry session"),
        ({"ex_date": date(2026, 11, 16)}, "not in the future"),
        ({"symbol": "AMD"}, "no position"),
        ({"symbol": "nvda"}, "invalid symbol"),
    ],
)
def test_invalid_adjustments_change_nothing(
    harness: Harness, db_path: Path, kwargs: dict[str, object], match: str
) -> None:
    _open(harness, 100)
    args: dict[str, object] = {
        "symbol": "NVDA",
        "event_id": "ev1",
        "ex_date": EX_DATE,
        "new_shares": 4,
        "old_shares": 1,
        "now": NOW,
    }
    args.update(kwargs)
    before = snapshot(db_path)
    with pytest.raises(StateStoreError, match=match):
        harness.store.apply_corporate_action_adjustment(**args)  # type: ignore[arg-type]
    assert snapshot(db_path) == before


def test_closed_position_is_not_adjusted(harness: Harness) -> None:
    _open(harness, 100)
    harness.store.update_position("NVDA", now=NOW, qty=0, status="closed")
    with pytest.raises(StateStoreError, match="not held"):
        _apply(harness, 2, 1)
