"""Broker access — Alpaca PAPER only.

Phase 1 is read-only: :mod:`semiedge.broker.account` builds an account snapshot from three read
requests. Trading clients are created exclusively by
:func:`semiedge.safety.broker.create_paper_trading_client`.
"""

from semiedge.broker.account import (
    AccountBlockReason,
    AccountReadPath,
    AccountSnapshot,
    BrokerDataError,
    BrokerError,
    PositionSnapshot,
    ReadOnlyTradingClient,
    fetch_account_snapshot,
    mask_account_number,
)

__all__ = [
    "AccountBlockReason",
    "AccountReadPath",
    "AccountSnapshot",
    "BrokerDataError",
    "BrokerError",
    "PositionSnapshot",
    "ReadOnlyTradingClient",
    "fetch_account_snapshot",
    "mask_account_number",
]
