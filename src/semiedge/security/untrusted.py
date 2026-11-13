"""Untrusted strings are inert display data, never commands, settings or order parameters."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass

from semiedge.logconfig import SecretRedactionFilter

_SOURCES = frozenset({"broker", "discord", "news", "ai", "file"})
MAX_TEXT_LENGTH = 2000


def _clean(raw: str, *, truncate: bool = True) -> str:
    cleaned = "".join(
        char
        for char in unicodedata.normalize("NFC", raw[: 4 * MAX_TEXT_LENGTH])
        if char == "\n" or unicodedata.category(char) not in {"Cc", "Cf", "Cs", "Zl", "Zp", "Co"}
    )
    return cleaned[:MAX_TEXT_LENGTH] if truncate else cleaned


@dataclass(frozen=True, slots=True)
class UntrustedText:
    source: str
    text: str

    def __post_init__(self) -> None:
        if type(self.source) is not str or self.source not in _SOURCES:
            raise ValueError("unsupported untrusted-text source")
        if type(self.text) is not str:
            raise ValueError("untrusted text must be a string")
        object.__setattr__(self, "text", _clean(self.text))

    @classmethod
    def from_raw(
        cls,
        source: str,
        raw: object,
        *,
        max_len: int = 2000,
        redactor: SecretRedactionFilter | None = None,
    ) -> UntrustedText:
        """Redact raw text before normalization/truncation when a redactor is supplied.

        Credential-bearing consumers must supply their configured redactor here; wrapping first
        and redacting later cannot recover a secret whose prefix was already truncated.
        """
        if type(raw) is not str:
            raise ValueError("untrusted text must be a string")
        if type(max_len) is not int or not 1 <= max_len <= MAX_TEXT_LENGTH:
            raise ValueError("max_len must be an integer from 1 through 2000")
        if redactor is not None:
            raw = redactor.redact(raw)
        cleaned = _clean(raw, truncate=False)
        if redactor is not None:
            # Removing control characters can join fragments into a registered secret. Do
            # this second pass before the display cap can cut that reconstructed value apart.
            cleaned = redactor.redact(cleaned)
        return cls(source, cleaned[:max_len])

    def for_html(self) -> str:
        """HTML text escaping only; never interpolate the result as executable code."""
        return html.escape(self.text, quote=True)

    def for_discord(self) -> str:
        """Escape Markdown and neutralize mentions; notifier must also disable allowed_mentions.

        Escaping is not a command/SQL sanitizer. Consumers must never use this as executable
        input, a path, configuration, an order argument, or privileged model instructions.
        """
        escaped = re.sub(r"([\\`*_{}\[\]()#+\-.!|<>~])", r"\\\1", self.text)
        return escaped.replace("@", "@\u200b")

    def for_display(self) -> str:
        """Compatibility alias for Discord/plain Markdown; HTML consumers use for_html."""
        return self.for_discord()
