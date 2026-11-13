"""Hand-written residual F-102 credential forms; no real keys or network."""

import json
import logging

import pytest

from semiedge.logconfig import REDACTED, SecretRedactionFilter, configure_logging
from semiedge.security import redact_json

pytestmark = pytest.mark.safety


def pem_marker(kind, label="PRIVATE KEY"):
    # Build the same synthetic marker at runtime so history scanners do not mistake the
    # fixture source for a stored private key. There is deliberately no real key body.
    return "-" * 5 + kind + " " + label + "-" * 5


FORMS = [
    "%63ookie%3Duid%3DFAKE_PRIVATE_A; sid=FAKE_PRIVATE_B\nsafe=keep",
    "token=[]FAKE_PRIVATE_A; safe=keep",
    "%63ookie%3Duid=FAKE_PRIVATE_A; sid=FAKE_PRIVATE_B\nsafe=keep",
    "Cookie: uid=FAKE_PRIVATE_A; sid=FAKE_PRIVATE_B\nsafe=keep",
    "Set-Cookie: uid=FAKE_PRIVATE_A; opaque=FAKE_PRIVATE_B\nsafe=keep",
    "postgres://user:FAKE_PRIVATE_A@db.invalid/path safe=keep",
    "https://FAKE_PRIVATE_A@host.invalid/path safe=keep",
    "postgres://user:FAKE_PRIVATE_A@FAKE_PRIVATE_B@db.invalid/path safe=keep",
    "access_token%3DFAKE_PRIVATE_A%3DFAKE_PRIVATE_B&safe=keep",
    "%61pi%5Fkey%3aFAKE_PRIVATE_A&safe=keep",
    'tokens=["FAKE_PRIVATE_A", "FAKE_PRIVATE_B"]; safe=keep',
    'token={"first": "FAKE_PRIVATE_A", "nested": ["FAKE_PRIVATE_B"]}; safe=keep',
    'token=["FAKE_PRIVATE_A", "FAKE_PRIVATE_B"',
    'token=["FAKE_PRIVATE_A",\n"FAKE_PRIVATE_B"] safe=keep',
    'token=["escaped\\"bracket]FAKE_PRIVATE_A", "FAKE_PRIVATE_B"] safe=keep',
    'secret=""FAKE_PRIVATE_A; safe=keep',
    "secret=''FAKE_PRIVATE_A; safe=keep",
    'secret="""FAKE_PRIVATE_A"""; safe=keep',
    pem_marker("BEGIN") + "\nFAKE_PRIVATE_A\n" + pem_marker("END") + " safe=keep",
    pem_marker("BEGIN", "RSA PRIVATE KEY")
    + "\nFAKE_PRIVATE_A\n"
    + pem_marker("END", "RSA PRIVATE KEY")
    + " safe=keep",
    pem_marker("BEGIN", "ENCRYPTED PRIVATE KEY") + "\nFAKE_PRIVATE_A\n",
    *[
        f"{key}=FAKE_PRIVATE_A; safe=keep"
        for key in (
            "pass",
            "passwd",
            "pwd",
            "auth",
            "credentials",
            "cookie",
            "set-cookie",
            "signature",
            "sig",
        )
    ],
]


@pytest.fixture
def logger():
    target = logging.getLogger("semiedge")
    saved = target.handlers[:], target.level, target.propagate
    yield target
    target.handlers, target.level, target.propagate = saved


@pytest.mark.parametrize("message", FORMS)
@pytest.mark.parametrize("surface", ["text", "json", "persisted"])
def test_residual_forms_mask_complete_values(logger, capsys, message, surface):
    redactor = SecretRedactionFilter()
    if surface == "persisted":
        clean = redact_json({"note": message}, redactor)["note"]
    else:
        configure_logging(fmt=surface).info("upstream: %s", message)
        line = capsys.readouterr().err
        clean = json.loads(line)["msg"] if surface == "json" else line
    assert "FAKE_PRIVATE" not in clean
    assert REDACTED in clean
    if "safe=keep" in message:
        assert "safe=keep" in clean
    assert redactor.redact(redactor.redact(message)) == redactor.redact(message)


@pytest.mark.parametrize(
    "key",
    ["pass", "passwd", "pwd", "auth", "credentials", "cookie", "set-cookie", "signature", "sig"],
)
def test_alias_keys_mask_structured_values_before_encoding(logger, capsys, key):
    raw = {key: ["FAKE_PRIVATE_A", {"nested": "FAKE_PRIVATE_B"}], "safe": "keep"}
    configure_logging().info("safe", extra={"payload": raw})
    expected = {key: REDACTED, "safe": "keep"}
    assert json.loads(capsys.readouterr().err)["payload"] == expected
    assert redact_json(raw, SecretRedactionFilter()) == expected
    assert raw[key][0] == "FAKE_PRIVATE_A"


def test_collection_depth_refusal_masks_tail_without_parsing_or_evaluating():
    message = "token=" + "[" * 100 + '"FAKE_PRIVATE_A"' + "]" * 100
    assert "FAKE_PRIVATE" not in SecretRedactionFilter().redact(message)


def test_direct_json_text_keeps_an_envelope_when_a_collection_is_masked():
    raw = '{"tokens": ["FAKE_PRIVATE_A", {"value": "FAKE_PRIVATE_B"}], "safe": "keep"}'
    assert json.loads(SecretRedactionFilter().redact(raw)) == {"tokens": REDACTED, "safe": "keep"}


def test_private_key_without_matching_end_refuses_the_remaining_tail():
    raw = (
        pem_marker("BEGIN", "EC PRIVATE KEY")
        + "\nFAKE_PRIVATE_A\n"
        + pem_marker("END", "PUBLIC KEY")
        + "\nFAKE_PRIVATE_B"
    )
    assert SecretRedactionFilter().redact(raw) == REDACTED


def test_aliases_do_not_mask_unrelated_diagnostics_or_weaken_existing_markers():
    raw = {"signal_count": 3, "author": "safe", "monkey": "FAKE_PRIVATE_A"}
    assert redact_json(raw, SecretRedactionFilter()) == {
        "signal_count": 3,
        "author": "safe",
        "monkey": REDACTED,
    }


@pytest.mark.parametrize("prefix", ["token=[" * 10000, (pem_marker("BEGIN") + "\n") * 10000])
def test_many_unterminated_prefixes_fail_closed_without_recursion(prefix):
    assert "FAKE_PRIVATE" not in SecretRedactionFilter().redact(prefix + "FAKE_PRIVATE_A")
