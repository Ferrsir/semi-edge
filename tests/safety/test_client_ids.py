"""Client order IDs (phase2-4 §6.2, #21): deterministic, bounded, round-trip, namespaced."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from semiedge.execution.client_ids import (
    MAX_LENGTH,
    make_client_order_id,
    namespace,
    parse_client_order_id,
)

pytestmark = pytest.mark.safety
D = date(2026, 11, 13)


def test_namespace_is_eight_hex_and_stable():
    ns = namespace("store-a")
    assert ns == namespace("store-a")
    assert len(ns) == 8
    assert int(ns, 16) >= 0
    assert namespace("store-b") != ns


@pytest.mark.parametrize("kind", ["entry", "protection", "time_exit", "flatten"])
@pytest.mark.parametrize("symbol", ["NVDA", "BRK.B", "BF-B", "A", "ABCDEFGHIJ"])
def test_round_trip(kind, symbol):
    ns = namespace("store")
    cid = make_client_order_id(ns, kind, D, symbol, 7)
    assert len(cid) <= MAX_LENGTH
    p = parse_client_order_id(cid)
    assert p is not None
    assert (p.namespace, p.kind, p.decision_session, p.symbol, p.attempt) == (
        ns,
        kind,
        D,
        symbol,
        7,
    )


def test_deterministic_and_distinct_across_namespaces():
    a, b = namespace("one"), namespace("two")
    assert make_client_order_id(a, "entry", D, "NVDA", 1) == make_client_order_id(
        a, "entry", D, "NVDA", 1
    )
    assert make_client_order_id(a, "entry", D, "NVDA", 1) != make_client_order_id(
        b, "entry", D, "NVDA", 1
    )


@pytest.mark.parametrize(
    "args",
    [
        ("ABCDEFGH", "entry", D, "NVDA", 1),  # namespace not lowercase hex
        ("abcdef12", "buy", D, "NVDA", 1),
        ("abcdef12", "entry", datetime(2026, 11, 13, tzinfo=UTC), "NVDA", 1),
        ("abcdef12", "entry", D, "nvda", 1),
        ("abcdef12", "entry", D, "TOOLONGSYMBOL", 1),
        ("abcdef12", "entry", D, "NVDA", 0),
        ("abcdef12", "entry", D, "NVDA", 100),
        ("abcdef12", "entry", D, "NVDA", True),
    ],
)
def test_invalid_inputs_are_refused(args):
    with pytest.raises(ValueError, match=r"must|unknown|does not"):
        make_client_order_id(*args)


@pytest.mark.parametrize(
    "value",
    [
        None,
        12,
        "",
        "se2-abcdef12-en-20261113-NVDA-1",
        "se1-abcdef1-en-20261113-NVDA-1",
        "se1-abcdef12-xx-20261113-NVDA-1",
        "se1-abcdef12-en-20261341-NVDA-1",
        "se1-abcdef12-en-20261113-NVDA",
        "se1-abcdef12-en-20261113-NVDA-01",
        "se1-abcdef12-en-20261113-NVDA-0",
        "se1-abcdef12-en-20261113-nvda-1",
        "x" * 49,
    ],
)
def test_foreign_or_malformed_values_parse_as_none(value):
    assert parse_client_order_id(value) is None
