"""Safety invariants of ``semiedge.state`` (spec phase2-4-interfaces.md §0.2, §5, §6.4, §12, §18).

* ``semiedge.state`` never imports broker/execution/engine/notify/dashboard/backtest, alpaca or
  network modules -- including ``if TYPE_CHECKING:`` imports, relative imports and literal
  ``importlib.import_module`` calls (the checker is proven on a synthetic tree first).
* ``semiedge.state`` reads the wall clock only through ``semiedge.clock``.
* ``semiedge.dashboard`` never references ``clear_halt`` (it may only request a MANUAL halt).
* The order state machine has exactly one backward edge (the §6.4 recovery exception) and no
  edge out of a terminal status; fills above the broker cumulative fail closed and halt.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

import semiedge
from semiedge.risk.types import EntryProposal, HaltKind, OrderSide, RiskCheck, RiskDecision
from semiedge.safety.modes import TradingMode
from semiedge.state.models import TERMINAL_STATUSES, BrokerFillInput, OrderIntent
from semiedge.state.order_status import ALLOWED, RECOVERY_EDGE
from semiedge.state.store import StateStore, StateStoreError

pytestmark = pytest.mark.safety

SRC = Path(semiedge.__file__).resolve().parent
STATE_FORBIDDEN = (
    "alpaca",
    "semiedge.broker",
    "semiedge.execution",
    "semiedge.engine",
    "semiedge.notify",
    "semiedge.dashboard",
    "semiedge.backtest",
    "requests",
    "httpx",
    "urllib.request",
    "urllib3",
    "http.client",
    "socket",
    "aiohttp",
    "websockets",
)
WALL_CLOCK_CALLS = {"now", "utcnow", "today", "time", "time_ns", "monotonic", "perf_counter"}


# ------------------------------------------------------------------ static checkers


def _module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path, root: Path) -> Iterator[str]:
    """Every imported module of one file (absolute, relative, TYPE_CHECKING, import_module)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = _module_name(path, root)
    if path.name != "__init__.py":
        package = package.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            yield module
            yield from (f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "import_module":
                arg = node.args[0] if node.args else None
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    yield "<non-literal import_module>"
                else:
                    yield arg.value


def _violations(root: Path, prefix: str, forbidden: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        module = _module_name(path, root)
        if module != prefix and not module.startswith(prefix + "."):
            continue
        for imported in _imports(path, root):
            if imported == "<non-literal import_module>" or any(
                imported == f or imported.startswith(f + ".") for f in forbidden
            ):
                found.append(f"{module} -> {imported}")
    return found


def _wall_clock_calls(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in WALL_CLOCK_CALLS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"datetime", "date", "time"}
        ):
            found.append(f"{path.name}:{node.lineno} {node.func.value.id}.{node.func.attr}")
    return found


def _references(path: Path, name: str) -> bool:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Name) and node.id == name) or (
            isinstance(node, ast.Attribute) and node.attr == name
        ):
            return True
        if isinstance(node, ast.alias) and name in {node.name.rsplit(".", 1)[-1], node.asname}:
            return True
        if isinstance(node, ast.Constant) and node.value == name:
            return True
    return False


# ------------------------------------------------------------------ checker proofs


@pytest.mark.parametrize(
    "source",
    [
        "import alpaca.trading\n",
        "from semiedge.broker import gateway\n",
        "from ..execution import orders\n",
        "from .. import engine\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from semiedge.notify import x\n",
        "import importlib\nimportlib.import_module('semiedge.dashboard.app')\n",
        "import importlib\nname = 'x'\nimportlib.import_module(name)\n",
        "import socket\n",
        "from urllib.request import urlopen\n",
        "from semiedge.backtest.engine import run\n",
    ],
)
def test_checker_detects_each_forbidden_edge(tmp_path: Path, source: str) -> None:
    root = tmp_path / "semiedge"
    (root / "state").mkdir(parents=True)
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "state" / "__init__.py").write_text("", encoding="utf-8")
    (root / "state" / "store.py").write_text(source, encoding="utf-8")
    assert _violations(root, "semiedge.state", STATE_FORBIDDEN)


def test_checker_allows_permitted_imports(tmp_path: Path) -> None:
    root = tmp_path / "semiedge"
    (root / "state").mkdir(parents=True)
    (root / "state" / "store.py").write_text(
        "import sqlite3\nfrom semiedge.risk.types import Halt\nfrom ..reporting import x\n"
        "from .models import OrderIntent\n",
        encoding="utf-8",
    )
    assert _violations(root, "semiedge.state", STATE_FORBIDDEN) == []


def test_wall_clock_and_reference_checkers_work(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from datetime import datetime, UTC\nimport time\nx = datetime.now(UTC)\ny = time.time()\n",
        encoding="utf-8",
    )
    assert len(_wall_clock_calls(bad)) == 2
    for source in (
        "from semiedge.state.halt_control import clear_halt\n",
        "from semiedge.state import halt_control\nhalt_control.clear_halt(p, 1, 'a', n)\n",
        "f = getattr(hc, 'clear_halt')\n",
    ):
        bad.write_text(source, encoding="utf-8")
        assert _references(bad, "clear_halt"), source
    bad.write_text("from semiedge.state.halt_control import request_manual_halt\n", "utf-8")
    assert not _references(bad, "clear_halt")


# ------------------------------------------------------------------ the real tree


def test_state_package_respects_import_boundaries() -> None:
    assert (SRC / "state" / "store.py").is_file()
    assert _violations(SRC, "semiedge.state", STATE_FORBIDDEN) == []


def test_state_package_reads_the_wall_clock_only_through_semiedge_clock() -> None:
    files = sorted((SRC / "state").rglob("*.py"))
    assert files
    assert [hit for path in files for hit in _wall_clock_calls(path)] == []


def test_dashboard_never_references_clear_halt() -> None:
    dashboard = SRC / "dashboard"
    files = sorted(dashboard.rglob("*.py")) if dashboard.exists() else []
    assert [p.name for p in files if _references(p, "clear_halt")] == []
    imports = [
        f"{p.name} -> {m}"
        for p in files
        for m in _imports(p, SRC)
        if m == "semiedge.state.store" or m.startswith("semiedge.state.store.")
    ]
    assert imports == []


def test_halt_control_does_not_import_the_writer() -> None:
    imported = set(_imports(SRC / "state" / "halt_control.py", SRC))
    assert not any(m.startswith("semiedge.state.store") for m in imported)


# ------------------------------------------------------------------ state machine


def test_only_backward_edge_is_the_recovery_exception() -> None:
    order = [
        "staged",
        "submitting",
        "unknown",
        "submitted",
        "accepted",
        "partially_filled",
        "cancel_pending",
    ]
    rank = {s: i for i, s in enumerate(order)}
    backward = {
        (src, dst)
        for src, targets in ALLOWED.items()
        for dst in targets
        if src in rank and dst in rank and rank[dst] < rank[src]
    }
    assert backward == {RECOVERY_EDGE}
    assert {s for s, t in ALLOWED.items() if not t} == TERMINAL_STATUSES
    assert all("staged" not in t for s, t in ALLOWED.items() if (s, "staged") != RECOVERY_EDGE)


# ------------------------------------------------------------------ fail-closed store behaviour

T0 = datetime(2026, 11, 13, 13, 0, tzinfo=UTC)
SESSION = date(2026, 11, 12)


@pytest.fixture
def entry_store(tmp_path: Path) -> Iterator[tuple[StateStore, str]]:
    store = StateStore(tmp_path / "state" / "semiedge.sqlite3", data_source="synthetic", now=T0)
    store.start_run("run-1", "post_close", TradingMode.PAPER, "c" * 64, None, T0)
    p = EntryProposal(
        "NVDA", SESSION, SESSION, "momentum_v1", "1.0.0", 1, 80.0, 100.0, 2.0, 96.0, 108.0, 100
    )
    (decision_id,) = store.record_risk_decisions(
        "run-1",
        [
            RiskDecision(
                p, True, 100, (), (RiskCheck("R01_mode", True, None, None, "ok"),), "f" * 64, T0
            )
        ],
    )
    cid = "se1-abcd1234-en-20261112-NVDA-1"
    store.insert_order_intent(
        OrderIntent(
            cid,
            "entry",
            "NVDA",
            OrderSide.BUY,
            100,
            "market",
            "opg",
            "simple",
            None,
            None,
            SESSION,
            T0 + timedelta(hours=1),
            decision_id,
            None,
            T0,
        )
    )
    yield store, cid
    store.close()


def test_entry_cannot_be_sent_while_any_halt_is_active(entry_store: tuple[StateStore, str]) -> None:
    store, cid = entry_store
    store.set_halt(HaltKind.MANUAL, "owner", "cli:owner", T0)
    with pytest.raises(StateStoreError, match="halted"):
        store.transition_order(cid, "submitting", now=T0, event="begin_submit")
    order = store.get_order(cid)
    assert order is not None
    assert order.status == "staged"


def test_fills_never_exceed_the_broker_cumulative(entry_store: tuple[StateStore, str]) -> None:
    store, cid = entry_store
    store.transition_order(cid, "submitting", now=T0, event="begin_submit")
    store.transition_order(cid, "accepted", now=T0, event="ok", broker_order_id="b-1")
    with pytest.raises(StateStoreError, match="fill_mismatch"):
        store.ingest_order_fills(
            cid,
            broker_filled_qty=50,
            broker_avg_price=100.0,
            activities=[BrokerFillInput("A", 60, 100.0, T0)],
            now=T0,
        )
    assert store.fills(include_superseded=True) == []
    order = store.get_order(cid)
    assert order is not None
    assert order.filled_qty == 0
    assert [h.kind for h in store.active_halts()] == [HaltKind.RECONCILIATION]


def test_illegal_transition_halts_and_leaves_the_row(entry_store: tuple[StateStore, str]) -> None:
    store, cid = entry_store
    with pytest.raises(StateStoreError, match="illegal_transition"):
        store.transition_order(cid, "filled", now=T0, event="bad")
    order = store.get_order(cid)
    assert order is not None
    assert order.status == "staged"
    assert [h.kind for h in store.active_halts()] == [HaltKind.RECONCILIATION]


def test_live_mode_is_never_recorded(entry_store: tuple[StateStore, str]) -> None:
    store, _cid = entry_store
    with pytest.raises(StateStoreError, match="never live"):
        store.start_run("run-2", "engine", TradingMode.LIVE, "c", None, T0)
