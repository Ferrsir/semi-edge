"""Structured logging with secret redaction.

``configure_logging`` installs a single stderr handler emitting one JSON object per
line (or plain text), and a filter that masks every configured secret value plus
common credential patterns before a record is formatted.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from semiedge._redaction import REDACTED, credential_key, redact_structure

# Defence in depth for values not registered explicitly (e.g. echoed request headers).
_ASSIGNMENT = re.compile(
    r"(?i)(?<![a-z0-9_-])(?P<prefix>['\"]?(?P<key>[a-z][a-z0-9_-]{0,127})"
    r"['\"]?\s*[:=]\s*)"
)
_ASSIGNED_VALUE = re.compile(
    r"(?i)\"(?:\\[\s\S]|[^\"\\])*\"|'(?:\\[\s\S]|[^'\\])*'|"
    r"(?:(?:Bearer|Basic|Token)\s+)?(?:\\(?:[\s\S]|$)|[^\s,;'\"\\}&\]])+"
)
_UNPARSED_VALUE = re.compile(r"[^\r\n,;&}\]]*")
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bBearer\s+[^\s,'\"\\}&\]]+"),
    re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/[^\s'\"\\]+"),
)

_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys() | {"message", "asctime"}
)


class SecretRedactionFilter(logging.Filter):
    """Mask registered secret values and credential-looking substrings in log records."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        values = {s for s in secrets if s}
        # Exception/set repr can escape a value before it is subsequently JSON-escaped.
        values.update(repr(s)[1:-1] for s in tuple(values))
        values.update(json.dumps(s)[1:-1] for s in tuple(values))
        self._secrets = sorted(values, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)

        parts: list[str] = []
        cursor = 0
        for match in _ASSIGNMENT.finditer(text):
            if match.start() < cursor or not credential_key(match.group("key")):
                continue
            value = _ASSIGNED_VALUE.match(text, match.end())
            if value is None:
                # Truncated/malformed upstream text is still credential-bearing. Mask through
                # the next delimiter or line ending instead of exposing the unparsable value.
                fallback = _UNPARSED_VALUE.match(text, match.end())
                parts.extend((text[cursor : match.end()], REDACTED))
                cursor = fallback.end() if fallback is not None else len(text)
                continue
            quote = value.group(0)[0] if value.group(0)[0] in "'\"" else ""
            parts.extend((text[cursor : match.end()], quote + REDACTED + quote))
            cursor = value.end()
        text = "".join((*parts, text[cursor:]))
        for pattern in _PATTERNS:
            text = pattern.sub(lambda m: (m.group(1) if m.groups() else "") + REDACTED, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = None
        for key, value in list(record.__dict__.items()):
            if key not in _STANDARD_ATTRS and isinstance(value, str):
                setattr(record, key, self.redact(value))
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line; extra ``logger.info(..., extra={...})`` fields are included."""

    def __init__(self, redactor: SecretRedactionFilter | None = None) -> None:
        super().__init__()
        self._redactor = redactor if redactor is not None else SecretRedactionFilter()

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        try:
            clean = redact_structure(payload, self._redactor.redact, strict=False)
            return json.dumps(clean, allow_nan=False)
        except (ValueError, TypeError, OverflowError):
            return json.dumps(
                {
                    "ts": payload["ts"],
                    "logger": self._redactor.redact(record.name),
                    "level": record.levelname,
                    "msg": "Log payload redaction failed",
                },
                allow_nan=False,
            )


class _RedactingFormatter(logging.Formatter):
    """Redact the complete rendered record, including exceptions and nested extras."""

    def __init__(self, formatter: logging.Formatter, redactor: SecretRedactionFilter) -> None:
        super().__init__()
        self._formatter = formatter
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        if isinstance(self._formatter, JsonFormatter):
            # Every field is already redacted structurally, then encoded exactly once.
            return self._formatter.format(record)
        return self._redactor.redact(self._formatter.format(record))


def configure_logging(
    level: str = "INFO", fmt: str = "json", secrets: Iterable[str] = ()
) -> logging.Logger:
    """Configure the ``semiedge`` logger tree. Idempotent: replaces previous handlers."""
    logger = logging.getLogger("semiedge")
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler()
    redactor = SecretRedactionFilter(secrets)
    handler.addFilter(redactor)
    formatter = (
        JsonFormatter(redactor)
        if fmt == "json"
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler.setFormatter(_RedactingFormatter(formatter, redactor))
    logger.addHandler(handler)
    return logger
