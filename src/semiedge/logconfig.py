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

REDACTED = "***REDACTED***"

# Defence in depth for values not registered explicitly (e.g. echoed request headers).
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Nested extras are serialized after the record filter. Mask quoted JSON fields in the
    # final rendered line too, including escaped quotes/backslashes inside their values.
    re.compile(
        r'(?i)("(?:APCA-API-(?:KEY-ID|SECRET-KEY)|(?:secret|api)[_-]?key|password|'
        r"authorization|secret|(?:access|refresh)[_-]?token|token|"
        r'discord[_-]?webhook[_-]?url)"\s*:\s*")(?:\\.|[^"\\])*'
    ),
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:Bearer\s+)?)[^\s,'\"]+"),
    re.compile(r"(?i)(APCA-API-(?:KEY-ID|SECRET-KEY)\s*[:=]\s*)[^\s,'\"]+"),
    re.compile(r"(?i)((?:secret|api)_?key\s*[:=]\s*)[^\s,'\"]+"),
    re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/[^\s'\"]+"),
)

_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys() | {"message", "asctime"}
)


class SecretRedactionFilter(logging.Filter):
    """Mask registered secret values and credential-looking substrings in log records."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        values = {s for s in secrets if s}
        # Final JSON formatting escapes control characters; mask both representations.
        values.update(json.dumps(s)[1:-1] for s in tuple(values))
        self._secrets = sorted(values, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
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
        return json.dumps(payload, default=str)


class _RedactingFormatter(logging.Formatter):
    """Redact the complete rendered record, including exceptions and nested extras."""

    def __init__(self, formatter: logging.Formatter, redactor: SecretRedactionFilter) -> None:
        super().__init__()
        self._formatter = formatter
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
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
        JsonFormatter()
        if fmt == "json"
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler.setFormatter(_RedactingFormatter(formatter, redactor))
    logger.addHandler(handler)
    return logger
