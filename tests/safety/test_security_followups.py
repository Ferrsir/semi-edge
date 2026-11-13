"""Fake-only regressions from Claude findings F-070..F-076 (#74)."""

import html
import json
import logging
import unicodedata

import pytest

from semiedge.logconfig import REDACTED, SecretRedactionFilter, configure_logging
from semiedge.security import UntrustedText, redact_json
from semiedge.security.untrusted import MAX_TEXT_LENGTH

pytestmark = pytest.mark.safety


@pytest.fixture
def logger():
    target = logging.getLogger("semiedge")
    previous = target.handlers[:], target.level, target.propagate
    yield target
    target.handlers = previous[0]
    target.setLevel(previous[1])
    target.propagate = previous[2]


@pytest.mark.parametrize("fmt", ["json", "text"])
@pytest.mark.parametrize(
    "message",
    [
        "{'Authorization': 'Bearer FAKE_CREDENTIAL_70'}",
        "Authorization: Basic FAKE_CREDENTIAL_70",
        "https://example.invalid/?token=FAKE_CREDENTIAL_70&safe=keep",
        "https://example.invalid/?access_token=FAKE_CREDENTIAL_70&safe=keep",
        "Bearer FAKE_CREDENTIAL_70",
    ],
)
def test_unregistered_text_credentials_never_reach_logs(logger, capsys, fmt, message):
    configured = configure_logging(fmt=fmt)
    configured.info(message)
    line = capsys.readouterr().err
    assert "FAKE_CREDENTIAL_70" not in line
    assert REDACTED in line
    if fmt == "json":
        json.loads(line)


@pytest.mark.parametrize(
    "key",
    [
        "APCA_API_SECRET_KEY",
        "x-api-key",
        "Proxy-Authorization",
        "client_secret",
        "webhook_url",
        "id_token",
        "access_token",
        "refresh-token",
        "Password",
    ],
)
@pytest.mark.parametrize(
    "value",
    [
        "FAKE_STRUCTURAL_70",
        ["Bearer FAKE_STRUCTURAL_70"],
        {"nested": "FAKE_STRUCTURAL_70"},
        12345,
        None,
    ],
)
def test_shared_credential_key_rule_masks_whole_values(logger, capsys, key, value):
    raw = {"nested": {key: value, "safe": "keep"}}
    configured = configure_logging()
    configured.info("safe", extra={"payload": raw})
    parsed = json.loads(capsys.readouterr().err)
    assert parsed["payload"] == {"nested": {key: REDACTED, "safe": "keep"}}
    assert redact_json(raw, SecretRedactionFilter()) == parsed["payload"]
    assert raw["nested"][key] == value


def test_sensitive_top_level_extras_and_non_json_values_are_masked(logger, capsys):
    configured = configure_logging()
    configured.info("safe", extra={"client_secret": object()})
    assert json.loads(capsys.readouterr().err)["client_secret"] == REDACTED


@pytest.mark.parametrize("secret", ["fixture-sec\\ret-ONLY", "fixture-'quote'\nsecret"])
def test_registered_repr_forms_cannot_leak_in_sets_or_exception_text(logger, capsys, secret):
    configured = configure_logging(secrets=[secret])
    try:
        raise KeyError(secret)
    except KeyError:
        configured.exception("failed", extra={"payload": {secret}})
    parsed = json.loads(capsys.readouterr().err)
    assert parsed["payload"] == [REDACTED]
    assert secret not in parsed["exc"]
    assert repr(secret)[1:-1] not in parsed["exc"]
    assert REDACTED in parsed["exc"]


@pytest.mark.parametrize(
    "message",
    [
        'auth failed authorization=abc"def',
        "auth failed api_key=abc\\tail",
        "Authorization: Basic abc'def",
    ],
)
def test_redaction_happens_before_json_encoding(logger, capsys, message):
    configure_logging().info(message)
    parsed = json.loads(capsys.readouterr().err)
    assert REDACTED in parsed["msg"]


@pytest.mark.parametrize("fmt", ["json", "text"])
@pytest.mark.parametrize(
    "value",
    [r"abc\FAKE_PRIVATE_TAIL", r"abc\"FAKE_PRIVATE_TAIL", r"abc\\FAKE_PRIVATE_TAIL", "abc\\"],
)
def test_unquoted_backslash_credentials_mask_the_entire_value(logger, capsys, fmt, value):
    configured = configure_logging(fmt=fmt)
    configured.info("api_key=%s", value)
    line = capsys.readouterr().err
    assert "FAKE_PRIVATE_TAIL" not in line
    assert "abc" not in line
    assert REDACTED in line
    if fmt == "json":
        assert json.loads(line)["msg"] == "api_key=" + REDACTED


def test_direct_encoded_json_redaction_preserves_escape_boundaries():
    encoded = json.dumps({"message": 'auth failed api_key=abc\\"FAKE_PRIVATE_TAIL'})
    line = SecretRedactionFilter().redact(encoded)
    assert "FAKE_PRIVATE_TAIL" not in line
    assert json.loads(line)["message"] == "auth failed api_key=" + REDACTED


def test_raw_redaction_precedes_control_removal_and_truncation():
    secret = "PKFAKE0123456789ABCD"
    redactor = SecretRedactionFilter([secret, "fake\tsecret\tvalue"])
    clipped = UntrustedText.from_raw("broker", "x" * 1990 + secret, redactor=redactor)
    assert "PKFAKE" not in clipped.text
    cleaned = UntrustedText.from_raw("broker", "fake\tsecret\tvalue", redactor=redactor)
    assert cleaned.text == REDACTED
    assert "fakesecretvalue" not in cleaned.text


def test_html_and_discord_renderers_have_separate_escaping_rules():
    text = "it's & <b> @here [x](y)"
    value = UntrustedText.from_raw("news", text)
    assert value.for_html() == html.escape(text, quote=True)
    assert "&\\#x27;" not in value.for_html()
    assert "&lt;" not in value.for_discord()
    assert "&amp;" not in value.for_discord()
    assert "@here" not in value.for_discord()
    assert "\\[x\\]\\(y\\)" in value.for_discord()


def test_cleaning_bounds_work_before_normalization_and_strips_line_separators(monkeypatch):
    lengths = []
    normalize = unicodedata.normalize

    def observed(form, value):
        lengths.append(len(value))
        return normalize(form, value)

    monkeypatch.setattr(unicodedata, "normalize", observed)
    value = UntrustedText.from_raw("file", "x" * 100_000)
    assert len(value.text) == MAX_TEXT_LENGTH
    assert max(lengths) <= 4 * MAX_TEXT_LENGTH
    assert UntrustedText.from_raw("file", "a\u2028b\u2029c\ue000d").text == "abcd"


def test_cycles_in_log_extras_produce_a_safe_json_failure_record(logger, capsys):
    cyclic = []
    cyclic.append(cyclic)
    configure_logging().info("message", extra={"payload": cyclic})
    parsed = json.loads(capsys.readouterr().err)
    assert parsed["msg"] == "Log payload redaction failed"


MALFORMED_CREDENTIALS = [
    'api_key="FAKE_UNTERMINATED_100',
    "password='FAKE_UNTERMINATED_100",
    '{"client_secret": "FAKE_TRUNCATED_100',
    'token="FAKE_START_100\\\nFAKE_TAIL_100"',
    "token='FAKE_START_100\\\nFAKE_TAIL_100'",
    "api_key=FAKE_START_100\\\nFAKE_TAIL_100",
]


@pytest.mark.parametrize("fmt", ["json", "text"])
@pytest.mark.parametrize("message", MALFORMED_CREDENTIALS)
def test_malformed_credential_values_fail_closed_in_logs(logger, capsys, fmt, message):
    configure_logging(fmt=fmt).info("upstream body: %s", message)
    line = capsys.readouterr().err
    assert "FAKE_" not in line
    assert REDACTED in line
    if fmt == "json":
        json.loads(line)


@pytest.mark.parametrize("message", MALFORMED_CREDENTIALS)
def test_malformed_credential_values_fail_closed_in_persisted_text(message):
    result = redact_json({"note": message}, SecretRedactionFilter())
    assert "FAKE_" not in json.dumps(result)
    assert REDACTED in result["note"]


@pytest.mark.parametrize("separator", ["\u200b", "\x00"])
@pytest.mark.parametrize("prefix", ["echo ", "x" * 1990])
def test_cleaning_cannot_reassemble_registered_secret(separator, prefix):
    secret = "FAKESECRETVALUE101"
    raw = prefix + "FAKE" + separator + "SECRETVALUE101"
    value = UntrustedText.from_raw("broker", raw, redactor=SecretRedactionFilter([secret]))
    assert "FAKE" not in value.text
    assert len(value.text) <= MAX_TEXT_LENGTH


def test_json_encoding_failure_keeps_safe_metadata(logger, capsys):
    configure_logging(secrets=["FAKE_REGISTERED_104"]).info(
        "m FAKE_REGISTERED_104", extra={"p": 10**5000}
    )
    line = capsys.readouterr().err
    parsed = json.loads(line)
    assert parsed["msg"] == "Log payload redaction failed"
    assert parsed["logger"] == "semiedge"
    assert parsed["level"] == "INFO"
    assert parsed["ts"]
    assert "FAKE_REGISTERED_104" not in line
