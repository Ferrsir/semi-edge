"""Redact JSON-compatible values before serialization, without mutating the caller."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import cast

from semiedge.config.settings import AppSettings
from semiedge.logconfig import REDACTED, SecretRedactionFilter

_CREDENTIAL_KEYS = frozenset(
    {
        "apikey",
        "secretkey",
        "secret",
        "password",
        "authorization",
        "token",
        "accesstoken",
        "refreshtoken",
        "apcaapikeyid",
        "apcaapisecretkey",
        "discordwebhookurl",
    }
)
_MAX_DEPTH = 32
_MAX_NODES = 100_000


def build_redactor(settings: AppSettings) -> SecretRedactionFilter:
    """Use only the supplied settings; never load environment/files or log secret values."""
    values = settings.secret_values()
    # UntrustedText normalizes NFC; cover that display representation as well as the original.
    return SecretRedactionFilter([*values, *(unicodedata.normalize("NFC", s) for s in values)])


def redact_text(text: str, redactor: SecretRedactionFilter) -> str:
    if type(text) is not str:
        raise ValueError("redact_text requires a string")
    return redactor.redact(text)


def redact_json(value: object, redactor: SecretRedactionFilter) -> object:
    """Copy/redact strict JSON primitives. Reject cycles, key collisions and nonfinite numbers.

    Registered secrets are removed from keys and values. Credential-labelled values are masked
    even when not registered. Unsupported objects are never coerced with str/repr. Bounds are
    operational limits (depth32 / 100000 nodes), not permission to truncate persisted data.
    """
    active: set[int] = set()
    nodes = 0

    def visit(item: object, depth: int) -> object:
        nonlocal nodes
        nodes += 1
        if depth > _MAX_DEPTH or nodes > _MAX_NODES:
            raise ValueError("JSON value exceeds redaction complexity limits")
        if item is None or type(item) in (bool, int):
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("JSON numbers must be finite")
            return item
        if type(item) is str:
            return redactor.redact(item)
        if type(item) not in (dict, list):
            raise ValueError("redaction requires JSON-compatible values")
        identity = id(item)
        if identity in active:
            raise ValueError("cyclic JSON value")
        active.add(identity)
        try:
            if isinstance(item, list):
                return [visit(child, depth + 1) for child in item]
            mapping = cast(dict[str, object], item)  # exact builtin dict checked above
            result: dict[str, object] = {}
            for key, child in mapping.items():
                if type(key) is not str:
                    raise ValueError("JSON object keys must be strings")
                clean_key = redactor.redact(key)
                if clean_key in result:
                    raise ValueError("redaction produced duplicate JSON keys")
                clean_value = visit(child, depth + 1)
                credential = re.sub(r"[^a-z0-9]", "", key.casefold()) in _CREDENTIAL_KEYS
                result[clean_key] = REDACTED if credential else clean_value
            return result
        finally:
            active.remove(identity)

    return visit(value, 0)
