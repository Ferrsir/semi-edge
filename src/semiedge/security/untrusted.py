"""Untrusted strings are inert display data, never commands, settings or order parameters."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass

_SOURCES = frozenset({"broker", "discord", "news", "ai", "file"})
MAX_TEXT_LENGTH = 2000


def _clean(raw: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFC", raw)
        if char == "\n" or unicodedata.category(char) not in {"Cc", "Cf", "Cs"}
    )[:MAX_TEXT_LENGTH]


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
    def from_raw(cls, source: str, raw: object, *, max_len: int = 2000) -> UntrustedText:
        if type(raw) is not str:
            raise ValueError("untrusted text must be a string")
        if type(max_len) is not int or not 1 <= max_len <= MAX_TEXT_LENGTH:
            raise ValueError("max_len must be an integer from 1 through 2000")
        return cls(source, _clean(raw)[:max_len])

    def for_display(self) -> str:
        """Escape HTML/Markdown delimiters and neutralize Discord mentions.

        Escaping is not a command/SQL sanitizer. Consumers must never use this as executable
        input, a path, configuration, an order argument, or privileged model instructions.
        """
        escaped = html.escape(self.text, quote=True)
        escaped = re.sub(r"([\\`*_{}\[\]()#+\-.!|>~])", r"\\\1", escaped)
        return escaped.replace("@", "@\u200b")
