"""Shared fixtures/builders for the SQLite state store tests (spec §5.4; issue #21).

Loaded as a plugin by tests/conftest.py; used by tests/integration/state (§5.4 acceptance),
tests/safety/test_state_order_machine.py and tests/unit/test_state_records.py.

Every database lives under ``tmp_path`` (CWD is ``tmp_path`` via tests/conftest.py). Times are
fixed: the store's clock is frozen and every mutating call gets an explicit ``now``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from semiedge.clock import FrozenClock
from semiedge.risk.types import EntryProposal, OrderSide, RiskCheck, RiskDecision
from semiedge.safety.modes import TradingMode
from semiedge.state.models import OrderIntent
from semiedge.state.store import StateStore

T0 = datetime(2026, 11, 13, 13, 0, tzinfo=UTC)  # 08:00 New York, pre-open of 2026-11-13
SESSION = date(2026, 11, 12)  # decision session t
RUN_ID = "20261112T211500Z-0123abcd"
NS = "abcd1234"


def proposal(symbol: str = "NVDA", shares: int = 100) -> EntryProposal:
    return EntryProposal(
        symbol=symbol,
        decision_session=SESSION,
        data_as_of=SESSION,
        strategy="momentum_v1",
        strategy_version="1.0.0",
        rank=1,
        score=81.5,
        entry_reference=100.0,
        atr=2.0,
        stop=96.0,
        target=108.0,
        proposed_shares=shares,
    )


def decision(p: EntryProposal, *, approved: bool = True) -> RiskDecision:
    return RiskDecision(
        proposal=p,
        approved=approved,
        approved_shares=p.proposed_shares if approved else 0,
        reasons=() if approved else ("max_positions",),
        checks=(
            RiskCheck("R01_mode", True, None, None, "paper"),
            RiskCheck("R09_max_positions", approved, 1.0, 3.0, "1 of 3"),
        ),
        limits_fingerprint="f" * 64,
        decided_at=T0 - timedelta(hours=15),
    )


def cid(kind: str, symbol: str = "NVDA", attempt: int = 1, session: date = SESSION) -> str:
    code = {"entry": "en", "protection": "pr", "time_exit": "tx", "flatten": "fx"}[kind]
    return f"se1-{NS}-{code}-{session:%Y%m%d}-{symbol}-{attempt}"


@dataclass
class Harness:
    """Small builders around one open writer store."""

    path: Path
    store: StateStore
    decisions: dict[str, int] = field(default_factory=dict)

    def stage_entry(self, symbol: str = "NVDA", qty: int = 100) -> str:
        (decision_id,) = self.store.record_risk_decisions(RUN_ID, [decision(proposal(symbol, qty))])
        self.decisions[symbol] = decision_id
        client_id = cid("entry", symbol)
        self.store.insert_order_intent(
            OrderIntent(
                client_order_id=client_id,
                kind="entry",
                symbol=symbol,
                side=OrderSide.BUY,
                qty=qty,
                order_type="market",
                time_in_force="opg",
                order_class="simple",
                limit_price=None,
                stop_price=None,
                decision_session=SESSION,
                valid_until=T0 + timedelta(hours=1, minutes=30),
                risk_decision_id=decision_id,
                parent_client_order_id=None,
                created_at=T0,
            )
        )
        return client_id

    def stage_sell(
        self,
        kind: str,
        parent: str,
        *,
        symbol: str = "NVDA",
        qty: int = 100,
        attempt: int = 1,
        created_at: datetime = T0,
    ) -> str:
        client_id = cid(kind, symbol, attempt)
        protection = kind == "protection"
        self.store.insert_order_intent(
            OrderIntent(
                client_order_id=client_id,
                kind=kind,  # type: ignore[arg-type]
                symbol=symbol,
                side=OrderSide.SELL,
                qty=qty,
                order_type="limit" if protection else "market",
                time_in_force="gtc" if protection else "day",
                order_class="oco" if protection else "simple",
                limit_price=108.0 if protection else None,
                stop_price=96.0 if protection else None,
                decision_session=SESSION,
                valid_until=T0 + timedelta(days=30),
                risk_decision_id=None,
                parent_client_order_id=parent,
                created_at=created_at,
            )
        )
        return client_id

    def send(self, client_id: str, broker_id: str, *, at: datetime | None = None) -> None:
        now = at or T0 + timedelta(minutes=5)
        self.store.transition_order(client_id, "submitting", now=now, event="begin_submit")
        self.store.transition_order(
            client_id,
            "accepted",
            now=now + timedelta(seconds=1),
            event="submit_ok",
            broker_order_id=broker_id,
            broker_status="accepted",
        )


def snapshot(path: Path) -> dict[str, list[tuple[object, ...]]]:
    """Every row of every table, read through an independent read-only connection."""
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
        return {
            t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()  # noqa: S608
            for t in tables
        }
    finally:
        conn.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "semiedge.sqlite3"


@pytest.fixture
def open_store(db_path: Path) -> Iterator[Callable[..., StateStore]]:
    opened: list[StateStore] = []

    def _open(path: Path = db_path, data_source: str = "real") -> StateStore:
        store = StateStore(path, data_source=data_source, clock=FrozenClock(T0))  # type: ignore[arg-type]
        opened.append(store)
        return store

    yield _open
    for store in opened:
        store.close()


@pytest.fixture
def harness(db_path: Path, open_store: Callable[..., StateStore]) -> Harness:
    store = open_store()
    store.start_run(
        RUN_ID, "post_close", TradingMode.PAPER, "c" * 64, None, T0 - timedelta(hours=16)
    )
    return Harness(db_path, store)
