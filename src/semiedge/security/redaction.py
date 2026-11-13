"""Redact JSON-compatible values before serialization, without mutating the caller."""

from __future__ import annotations

import unicodedata

from semiedge._redaction import redact_structure
from semiedge.config.settings import AppSettings
from semiedge.logconfig import SecretRedactionFilter


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
    return redact_structure(value, redactor.redact)
