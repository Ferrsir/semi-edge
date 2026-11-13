"""Opt-in smoke test against the real Alpaca PAPER endpoint (issue #13).

Skipped unless BOTH are set in the environment of the pytest process:

* ``SEMIEDGE_PAPER_SMOKE=1`` (explicit opt-in, so CI never reaches the network), and
* ``ALPACA_PAPER_API_KEY`` / ``ALPACA_PAPER_SECRET_KEY`` (PAPER keys only).

The values are captured at import time because the autouse ``_isolated_env`` fixture strips
broker variables from every test. The client comes only from the paper-only factory, and the
snapshot module is read-only, so the test cannot change broker state.
"""

from __future__ import annotations

import os

import pytest

from semiedge.broker.account import fetch_account_snapshot
from semiedge.safety.broker import create_paper_trading_client

pytestmark = pytest.mark.integration

_OPT_IN = os.environ.get("SEMIEDGE_PAPER_SMOKE") == "1"
_KEY = os.environ.get("ALPACA_PAPER_API_KEY", "")
_SECRET = os.environ.get("ALPACA_PAPER_SECRET_KEY", "")


@pytest.mark.skipif(
    not (_OPT_IN and _KEY and _SECRET),
    reason="opt-in paper smoke test: set SEMIEDGE_PAPER_SMOKE=1 and PAPER credentials",
)
def test_paper_account_snapshot_live() -> None:
    client = create_paper_trading_client(_KEY, _SECRET)

    snapshot = fetch_account_snapshot(client)

    assert snapshot.account_number_masked.startswith("****")
    assert snapshot.equity >= 0
    assert snapshot.as_of.utcoffset() is not None
    rendered = repr(snapshot)
    # pytest.fail with a fixed message: an assert would print the real key in its diff output.
    if _KEY in rendered or _SECRET in rendered:
        pytest.fail("snapshot repr leaks a credential")
