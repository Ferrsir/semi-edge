"""``StateReader`` parity: writer vs read-only store (spec §5.1, §5.4 F-037/F-043)."""

from __future__ import annotations

import inspect
import itertools
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from semiedge.risk.types import HaltKind
from semiedge.state.models import BrokerFillInput, PositionRecord, StateReader
from semiedge.state.readonly import ReadOnlyStateStore
from semiedge.state.store import StateStore
from state_support import SESSION, T0, Harness

pytestmark = pytest.mark.integration

READER_METHODS = (
    "data_source",
    "orders",
    "fills",
    "positions",
    "active_halts",
    "corporate_action_adjustments",
)


@pytest.fixture
def fixture_db(harness: Harness) -> Harness:
    """Several orders, fills (incl. superseded), a position, a halt and an adjustment."""
    h, store = harness, harness.store
    nvda = h.stage_entry("NVDA", qty=100)
    h.send(nvda, "b-nvda")
    store.ingest_order_fills(
        nvda, broker_filled_qty=30, broker_avg_price=100.0, activities=None, now=T0
    )
    store.ingest_order_fills(
        nvda,
        broker_filled_qty=100,
        broker_avg_price=100.1,
        activities=[
            BrokerFillInput("A1", 30, 100.0, T0 + timedelta(minutes=31)),
            BrokerFillInput("A2", 70, 100.2, T0 + timedelta(minutes=32)),
        ],
        now=T0 + timedelta(minutes=40),
    )
    store.transition_order(nvda, "filled", now=T0 + timedelta(minutes=41), event="sync")
    for symbol in ("AMD", "AVGO"):
        h.stage_entry(symbol, qty=10)
    store.open_position(
        PositionRecord(
            symbol="NVDA",
            qty=100,
            avg_entry_price=100.14,
            entry_session=SESSION,
            entry_client_order_id=nvda,
            entry_reference=100.0,
            atr=2.0,
            stop=96.0,
            target=108.0,
            split_ratio=1.0,
            status="open",
            opened_at=T0 + timedelta(minutes=41),
            closed_at=None,
            updated_at=T0 + timedelta(minutes=41),
        )
    )
    h.stage_sell("protection", nvda, created_at=T0 + timedelta(minutes=42))
    store.apply_corporate_action_adjustment(
        "NVDA",
        event_id="ev1",
        ex_date=SESSION + timedelta(days=1),
        new_shares=2,
        old_shares=1,
        now=T0 + timedelta(hours=1),
    )
    store.set_halt(HaltKind.DATA, "regime stale", "engine", T0)
    return h


@pytest.fixture
def both(fixture_db: Harness) -> Iterator[tuple[StateStore, ReadOnlyStateStore]]:
    reader = ReadOnlyStateStore(fixture_db.path)
    yield fixture_db.store, reader
    reader.close()


def test_both_stores_satisfy_the_protocol_with_identical_signatures(
    both: tuple[StateStore, ReadOnlyStateStore],
) -> None:
    writer, reader = both
    assert isinstance(writer, StateReader)
    assert isinstance(reader, StateReader)
    for name in READER_METHODS:
        proto = inspect.signature(getattr(StateReader, name))
        assert inspect.signature(getattr(StateStore, name)) == proto, name
        assert inspect.signature(getattr(ReadOnlyStateStore, name)) == proto, name
    assert isinstance(inspect.getattr_static(StateStore, "store_id"), property)
    assert isinstance(inspect.getattr_static(ReadOnlyStateStore, "store_id"), property)
    assert writer.store_id == reader.store_id


def test_every_filter_and_limit_combination_is_equal(
    both: tuple[StateStore, ReadOnlyStateStore],
) -> None:
    writer, reader = both
    status_options = [None, [], ["staged"], ["filled", "staged"], ["accepted"]]
    symbol_options = [None, "NVDA", "AMD", "QQQ"]
    kind_options = [None, [], ["entry"], ["protection", "entry"]]
    limit_options = [None, 1, 2, 50]
    checked = 0
    for statuses, symbol, kinds, limit in itertools.product(
        status_options, symbol_options, kind_options, limit_options
    ):
        args = {"statuses": statuses, "symbol": symbol, "kinds": kinds, "limit": limit}
        assert writer.orders(**args) == reader.orders(**args), args  # type: ignore[arg-type]
        checked += 1
    for cid, include, limit in itertools.product(
        [None, "se1-abcd1234-en-20261112-NVDA-1", "missing"], [False, True], [None, 1, 2, 50]
    ):
        args = {"client_order_id": cid, "include_superseded": include, "limit": limit}
        assert writer.fills(**args) == reader.fills(**args), args  # type: ignore[arg-type]
        checked += 1
    for include_closed in (False, True):
        assert writer.positions(include_closed=include_closed) == reader.positions(
            include_closed=include_closed
        )
    for symbol in (None, "NVDA", "AMD"):
        assert writer.corporate_action_adjustments(
            symbol=symbol
        ) == reader.corporate_action_adjustments(symbol=symbol)
    assert writer.active_halts() == reader.active_halts()
    assert writer.data_source() == reader.data_source() == "real"
    assert checked == 5 * 4 * 4 * 4 + 3 * 2 * 4


def test_ordering_and_limit_semantics(both: tuple[StateStore, ReadOnlyStateStore]) -> None:
    for store in both:
        everything = store.orders()
        keys = [(o.intent.created_at, o.intent.client_order_id) for o in everything]
        assert keys == sorted(keys)
        newest_two = store.orders(limit=2)
        assert newest_two == list(reversed(everything))[:2]  # the two newest, newest first
        assert newest_two[0].intent.kind == "protection"
        fills = store.fills(include_superseded=True)
        assert [(f.ts, f.fill_key) for f in fills] == sorted((f.ts, f.fill_key) for f in fills)
        assert store.fills(include_superseded=True, limit=2) == list(reversed(fills))[:2]
        assert {f.status for f in store.fills()} == {"final"}
        assert store.orders(statuses=[]) == []
        assert store.orders(kinds=[]) == []


@pytest.mark.parametrize("bad", [0, -1, True, 1.0, "2"])
def test_invalid_limits_raise_value_error_from_both(
    both: tuple[StateStore, ReadOnlyStateStore], bad: object
) -> None:
    for store in both:
        with pytest.raises(ValueError, match="limit"):
            store.orders(limit=bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="limit"):
            store.fills(limit=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"statuses": "filled"},
        {"statuses": ["bogus"]},
        {"kinds": ["bracket"]},
        {"kinds": "entry"},
        {"symbol": 5},
    ],
)
def test_invalid_filters_raise_value_error_from_both(
    both: tuple[StateStore, ReadOnlyStateStore], kwargs: dict[str, object]
) -> None:
    for store in both:
        with pytest.raises(ValueError, match=r"statuses|kinds|symbol"):
            store.orders(**kwargs)  # type: ignore[arg-type]


def test_data_source_parity_f043(tmp_path: Path, open_store: Callable[..., StateStore]) -> None:
    for source in ("real", "synthetic"):
        path = tmp_path / source / "state" / "semiedge.sqlite3"
        writer = open_store(path=path, data_source=source)
        reader = ReadOnlyStateStore(path)
        try:
            assert writer.data_source() == reader.data_source() == source
        finally:
            reader.close()
