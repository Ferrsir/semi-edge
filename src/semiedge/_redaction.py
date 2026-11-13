"""Shared pure redaction rules; no logging, configuration or security-package imports."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import cast

REDACTED = "***REDACTED***"
_MAX_DEPTH = 32
_MAX_NODES = 100_000


def credential_key(key: str) -> bool:
    """Prefer over-redaction to leaking unregistered credential-labelled values."""
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    return any(
        marker in normalized
        for marker in ("key", "secret", "token", "password", "authorization", "webhook")
    )


def _primitive(item: object, redact: Callable[[str], str], strict: bool) -> object:
    if type(item) is float and not math.isfinite(item):
        if strict:
            raise ValueError("JSON numbers must be finite")
        return "[NONFINITE]"
    return redact(item) if type(item) is str else item


def redact_structure(value: object, redact: Callable[[str], str], *, strict: bool = True) -> object:
    """Copy and redact before serialization. Logging can safely adapt non-JSON extras."""
    active: set[int] = set()
    nodes = 0

    def visit(item: object, depth: int) -> object:
        nonlocal nodes
        nodes += 1
        if depth > _MAX_DEPTH or nodes > _MAX_NODES:
            raise ValueError("JSON value exceeds redaction complexity limits")
        if item is None or type(item) in (bool, int, float, str):
            return _primitive(item, redact, strict)
        containers = (dict, list) if strict else (dict, list, tuple, set, frozenset)
        if type(item) not in containers:
            if strict:
                raise ValueError("redaction requires JSON-compatible values")
            try:
                return redact(str(item))
            except Exception:
                return "[UNSERIALIZABLE]"
        identity = id(item)
        if identity in active:
            raise ValueError("cyclic JSON value")
        active.add(identity)
        try:
            if not isinstance(item, dict):
                return [visit(child, depth + 1) for child in cast(list[object], item)]
            result: dict[str, object] = {}
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("JSON object keys must be strings")
                clean_key = redact(key)
                if clean_key in result:
                    raise ValueError("redaction produced duplicate JSON keys")
                # Mask the entire value, regardless of its type, without rendering it first.
                result[clean_key] = REDACTED if credential_key(key) else visit(child, depth + 1)
            return result
        finally:
            active.remove(identity)

    return visit(value, 0)
