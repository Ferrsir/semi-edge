"""Static enforcement of phase 2-4 import direction, including relative/dynamic imports."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

import semiedge

pytestmark = pytest.mark.safety
NETWORK = ("requests", "httpx", "urllib.request", "socket")
UPPER = (
    "alpaca",
    "semiedge.broker",
    "semiedge.execution",
    "semiedge.engine",
    "semiedge.state",
    "semiedge.backtest",
    "semiedge.notify",
    "semiedge.dashboard",
    "semiedge.data.alpaca_source",
    "semiedge.data.store",
    "sqlite3",
    *NETWORK,
)
DENY = {
    "semiedge.risk": (*UPPER, "semiedge.data.earnings_sources"),
    "semiedge.strategy": (*UPPER, "semiedge.data.synthetic", "semiedge.risk"),
    "semiedge.indicators": (*UPPER, "semiedge.data.synthetic"),
    "semiedge.reporting": (
        "alpaca",
        "semiedge.broker",
        "semiedge.execution",
        "semiedge.engine",
        "semiedge.state",
        "semiedge.notify",
        "semiedge.dashboard",
        "semiedge.backtest",
    ),
    "semiedge.backtest": (
        "alpaca",
        "semiedge.broker",
        "semiedge.execution",
        "semiedge.engine",
        "semiedge.state",
        "semiedge.notify",
        "semiedge.dashboard",
        "semiedge.data.alpaca_source",
        *NETWORK,
    ),
    "semiedge.state": (
        "alpaca",
        "semiedge.broker",
        "semiedge.execution",
        "semiedge.engine",
        "semiedge.notify",
        "semiedge.dashboard",
        "semiedge.backtest",
        *NETWORK,
    ),
    "semiedge.broker": (
        "semiedge.execution",
        "semiedge.engine",
        "semiedge.notify",
        "semiedge.dashboard",
        "semiedge.backtest",
    ),
    "semiedge.execution": (
        "semiedge.engine",
        "semiedge.notify",
        "semiedge.dashboard",
        "semiedge.backtest",
        "alpaca",
    ),
    "semiedge.notify": (
        "alpaca",
        "semiedge.broker",
        "semiedge.execution",
        "semiedge.engine",
        "semiedge.backtest",
        "semiedge.dashboard",
    ),
    "semiedge.dashboard": (
        "alpaca",
        "semiedge.broker",
        "semiedge.execution",
        "semiedge.engine",
        "semiedge.notify",
        "semiedge.data.alpaca_source",
        "semiedge.state.store",
        *NETWORK,
    ),
    # #20 earnings: the pure resolution module never reaches the network or the risk layer.
    "semiedge.data.earnings": (
        "alpaca",
        "semiedge.risk",
        "semiedge.data.earnings_sources",
        *NETWORK,
    ),
    "semiedge.data.earnings_sources": (
        "alpaca",
        "semiedge.risk",
        "semiedge.broker",
        "semiedge.execution",
        "semiedge.engine",
        "semiedge.state",
        "semiedge.backtest",
        "semiedge.notify",
        "semiedge.dashboard",
    ),
    "semiedge.ai": (
        "semiedge.execution",
        "semiedge.broker",
        "semiedge.engine",
        "semiedge.risk.manager",
        "semiedge.state.store",
    ),
}


def prefix(name, parent):
    return name == parent or name.startswith(parent + ".")


def violations(source, module, *, package=False):  # noqa: PLR0912 - explicit AST cases
    edges, problems = [], []
    dynamic = {"import_module"}
    importlibs = {"importlib"}
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                edges.append(alias.name)
                if alias.name == "importlib":
                    importlibs.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            target = node.module or ""
            if node.level:
                parent = module if package else module.rpartition(".")[0]
                target = importlib.util.resolve_name("." * node.level + target, parent)
            edges.extend([target, *(target + "." + a.name for a in node.names)])
            if target == "importlib":
                dynamic.update(a.asname or a.name for a in node.names if a.name == "import_module")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            is_dynamic = (isinstance(f, ast.Name) and f.id in dynamic) or (
                isinstance(f, ast.Attribute)
                and f.attr == "import_module"
                and isinstance(f.value, ast.Name)
                and f.value.id in importlibs
            )
            if is_dynamic:
                if (
                    not node.args
                    or not isinstance(node.args[0], ast.Constant)
                    or not isinstance(node.args[0].value, str)
                ):
                    problems.append("nonliteral import_module")
                else:
                    target = node.args[0].value
                    if target.startswith("."):
                        parent = module if package else module.rpartition(".")[0]
                        target = importlib.util.resolve_name(target, parent)
                    edges.append(target)
    for edge in edges:
        for owner, denied in DENY.items():
            if prefix(module, owner) and any(prefix(edge, d) for d in denied):
                problems.append(f"{module} -> {edge}")
        if prefix(edge, "alpaca.trading") and not (
            prefix(module, "semiedge.broker") or module == "semiedge.safety.broker"
        ):
            problems.append(f"{module} -> {edge}")
        if prefix(edge, "alpaca.data") and module not in {
            "semiedge.data.alpaca_source",
            "semiedge.data.corporate_actions",
        }:
            problems.append(f"{module} -> {edge}")
    return problems


def test_real_import_boundaries():
    root = Path(semiedge.__file__).parent
    problems = []
    for path in root.rglob("*.py"):
        parts = path.relative_to(root).with_suffix("").parts
        module = "semiedge." + ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        problems.extend(
            violations(path.read_text(encoding="utf-8"), module, package=path.name == "__init__.py")
        )
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(("owner", "denied"), [(o, d) for o, row in DENY.items() for d in row])
def test_checker_detects_every_edge(owner, denied, tmp_path):
    path = tmp_path / "probe.py"
    path.write_text(f"if TYPE_CHECKING:\n    import {denied}\n", encoding="utf-8")
    assert violations(path.read_text(encoding="utf-8"), owner + ".probe")


@pytest.mark.parametrize(
    "source",
    [
        "from ..broker import x",
        'import importlib as il; il.import_module("semiedge.broker")',
        'from importlib import import_module as load; load("semiedge.broker")',
        "importlib.import_module(variable)",
    ],
)
def test_relative_and_dynamic_imports(source):
    assert violations(source, "semiedge.backtest.probe")


def test_new_modules_do_not_read_wall_clock():
    root = Path(semiedge.__file__).parent
    prefixes = ("risk", "backtest", "state", "execution", "engine", "notify")
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if relative.parts[0] not in prefixes and relative.as_posix() != "broker/gateway.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"now", "utcnow", "time"}, str(path)
