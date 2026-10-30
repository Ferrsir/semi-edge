from __future__ import annotations

import io
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from semiedge.logconfig import configure_logging
from semiedge.safety.broker import assert_paper_client
from semiedge.safety.modes import SafetyViolation, TradingMode, order_submission_permitted

pytestmark = pytest.mark.safety


@pytest.mark.parametrize("flag", ["false", "true", 1, 0, None, [], {}])
@pytest.mark.parametrize("field", ["enable_order_submission", "allow_live_trading"])
def test_raw_guard_rejects_non_boolean_flags(flag: Any, field: str) -> None:
    flags = {"enable_order_submission": True, "allow_live_trading": False}
    flags[field] = flag
    with pytest.raises(SafetyViolation, match="explicit booleans"):
        order_submission_permitted(TradingMode.PAPER, **flags)


@pytest.mark.parametrize("fmt", ["json", "text"])
@pytest.mark.parametrize("secret", ["very-secret-token", "xy", "secret\nwith-newline"])
def test_configured_logging_redacts_nested_fields_and_exception(fmt: str, secret: str) -> None:
    logger = configure_logging(fmt=fmt, secrets=[secret])
    output = io.StringIO()
    handler = logger.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(output)
    try:
        raise RuntimeError(f"broker rejected {secret}")
    except RuntimeError:
        logger.exception("broker failed", extra={"details": {"headers": [secret]}})
    rendered = output.getvalue()
    assert secret not in rendered
    assert json.dumps(secret)[1:-1] not in rendered
    assert "***REDACTED***" in rendered
    if fmt == "json":
        assert json.loads(rendered)["exc"]


def test_endpoint_rejection_does_not_echo_url_credentials() -> None:
    with pytest.raises(SafetyViolation) as exc:
        assert_paper_client(SimpleNamespace(_base_url="https://user:password@evil.example"))
    assert "password" not in str(exc.value)
