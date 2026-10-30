from __future__ import annotations

import json
import logging

import pytest

from semiedge.logconfig import REDACTED, JsonFormatter, SecretRedactionFilter, configure_logging


def _record(msg: str, *args: object, **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("semiedge.test", logging.INFO, __file__, 1, msg, args or None, None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


@pytest.mark.safety
def test_registered_secrets_are_redacted_in_message_args_and_extras() -> None:
    flt = SecretRedactionFilter(["PKSECRETKEYID", "supersecretvalue"])
    rec = _record("key=%s", "PKSECRETKEYID", detail="token supersecretvalue here")
    assert flt.filter(rec)
    out = JsonFormatter().format(rec)
    assert "PKSECRETKEYID" not in out
    assert "supersecretvalue" not in out
    assert REDACTED in out


@pytest.mark.safety
@pytest.mark.parametrize(
    "text",
    [
        "APCA-API-SECRET-KEY: abcdef123456",  # gitleaks:allow (fake fixture)
        "secret_key=abcdef123456",  # gitleaks:allow (fake fixture)
        "https://discord.com/api/webhooks/123/abcdefTOKEN",
    ],
)
def test_credential_patterns_redacted(text: str) -> None:
    flt = SecretRedactionFilter()
    assert "abcdef" not in flt.redact(text)


def test_json_formatter_includes_extras() -> None:
    payload = json.loads(JsonFormatter().format(_record("hello", run_id="r1")))
    assert payload["msg"] == "hello"
    assert payload["run_id"] == "r1"
    assert payload["level"] == "INFO"


def test_configure_logging_is_idempotent() -> None:
    a = configure_logging("DEBUG", "text")
    b = configure_logging("INFO", "json", secrets=["x" * 8])
    assert a is b
    assert len(b.handlers) == 1
