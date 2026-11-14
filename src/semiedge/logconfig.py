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

from semiedge._redaction import (
    REDACTED,
    credential_key,
    normalized_credential_key,
    redact_structure,
)

# Defence in depth for values not registered explicitly (e.g. echoed request headers).
_ASSIGNMENT = re.compile(
    r"(?i)(?<![a-z0-9_%\-])(?P<prefix>['\"]?"
    r"(?P<key>(?:[a-z]|%(?!3[ad])[0-9a-f]{2})"
    r"(?:[a-z0-9_-]|%(?!3[ad])[0-9a-f]{2}){0,127})"
    r"['\"]?\s*(?:[:=]|%3[ad])\s*)"
)
_ASSIGNED_VALUE = re.compile(
    r"(?i)\"(?:\\[\s\S]|[^\"\\])*\"|'(?:\\[\s\S]|[^'\\])*'|"
    r"(?:(?:Bearer|Basic|Token)\s+)?(?:\\(?:[\s\S]|$)|[^\s,;'\"\\}&\]])+"
)
_UNPARSED_VALUE = re.compile(r"[^\r\n,;&}\]]*")
_COOKIE_HEADER_VALUE = re.compile(r"[^\r\n]*")
_MALFORMED_TAIL = re.compile(r"(?:\\[\s\S]|[^\s,;&}\]])+")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]{0,40}PRIVATE KEY)-----"
    r"[\s\S]*?(?:-----END (?P=label)-----|\Z)"
)
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bBearer\s+[^\s,'\"\\}&\]]+"),
    re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/[^\s'\"\\]+"),
    # Greedy userinfo reaches the final @ in the authority, so a malformed extra @ cannot
    # leave a password tail behind. The @ and host remain outside the replaced span.
    re.compile(r"(?i)(\b[a-z][a-z0-9+.-]{0,31}://)[^\s/?#'\"]+(?=@)"),
)

_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys() | {"message", "asctime"}
)


def _collection_end(text: str, start: int) -> int:
    """Consume a credential-labelled collection without evaluating its contents.

    Unbalanced/ambiguous or overly deep collections mask the remaining input. Each character
    is visited once; nested assignment matches are skipped by the caller's cursor.
    """
    stack = [text[start]]
    pairs = {"]": "[", "}": "{", ")": "("}
    quote = ""
    offset = start + 1
    while offset < len(text):
        char = text[offset]
        if char == "\\":
            offset += 2
            continue
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char in "[{(":
            stack.append(char)
            if len(stack) > 32:
                return len(text)
        elif char in "]})":
            if stack.pop() != pairs[char]:
                return len(text)
            if not stack:
                return offset + 1
        offset += 1
    return len(text)


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
        text = _PRIVATE_KEY.sub(REDACTED, text)
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)

        parts: list[str] = []
        cursor = 0
        for match in _ASSIGNMENT.finditer(text):
            if match.start() < cursor or not credential_key(match.group("key")):
                continue
            if match.end() < len(text) and text[match.end()] in "[{(":
                parts.extend((text[cursor : match.end()], '"' + REDACTED + '"'))
                cursor = _collection_end(text, match.end())
                if cursor < len(text) and text[cursor] not in " \t\r\n,;&}]":
                    tail = _MALFORMED_TAIL.match(text, cursor)
                    if tail is not None:
                        cursor = tail.end()
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
            if (
                not quote
                and normalized_credential_key(match.group("key")) in {"cookie", "setcookie"}
                and any(marker in value.group(0).casefold() for marker in ("=", "%3d"))
            ):
                # In a cookie header semicolons separate more credential values, not safe fields.
                cookie = _COOKIE_HEADER_VALUE.match(text, cursor)
                if cookie is not None:
                    cursor = cookie.end()
            if quote and cursor < len(text) and text[cursor] not in " \t\r\n,;&}]":
                # Empty/repeated quotes followed directly by a tail are one malformed value.
                tail = _MALFORMED_TAIL.match(text, cursor)
                if tail is not None:
                    cursor = tail.end()
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
