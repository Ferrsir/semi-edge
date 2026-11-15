from __future__ import annotations

import pytest

from semiedge.config.settings import AppSettings
from semiedge.safety import (
    SafetyViolation,
    TradingMode,
    assert_order_submission_allowed,
    order_submission_permitted,
    validate_safety_flags,
)

pytestmark = pytest.mark.safety


def test_defaults_are_scanner_with_submission_and_live_disabled() -> None:
    settings = AppSettings()
    assert settings.trading_mode is TradingMode.SCANNER
    assert settings.enable_order_submission is False
    assert settings.allow_live_trading is False
    assert settings.order_submission_permitted is False


@pytest.mark.parametrize("value", ["live", "LIVE"])
def test_live_mode_refuses_to_start(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("TRADING_MODE", value)
    with pytest.raises(SafetyViolation, match="not supported"):
        AppSettings()


@pytest.mark.parametrize("mode", ["scanner", "backtest", "paper"])
def test_allow_live_trading_true_always_refuses(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")
    with pytest.raises(SafetyViolation, match="ALLOW_LIVE_TRADING"):
        AppSettings()


@pytest.mark.parametrize("mode", ["scanner", "backtest"])
def test_order_submission_rejected_outside_paper(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    monkeypatch.setenv("ENABLE_ORDER_SUBMISSION", "true")
    with pytest.raises(SafetyViolation, match="only valid with TRADING_MODE=paper"):
        AppSettings()


def test_paper_mode_without_flag_does_not_permit_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADING_MODE", "paper")
    settings = AppSettings()
    assert settings.order_submission_permitted is False
    with pytest.raises(SafetyViolation, match="disabled"):
        assert_order_submission_allowed(
            settings.trading_mode,
            enable_order_submission=settings.enable_order_submission,
            allow_live_trading=settings.allow_live_trading,
        )


def test_paper_mode_with_flag_permits_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ENABLE_ORDER_SUBMISSION", "true")
    settings = AppSettings()
    assert settings.order_submission_permitted is True


def test_unknown_mode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "yolo")
    with pytest.raises(ValueError, match=r"trading_mode|TRADING_MODE|Input should be"):
        AppSettings()


def test_live_enum_member_rejected_by_pure_guards() -> None:
    with pytest.raises(SafetyViolation):
        validate_safety_flags(
            TradingMode.LIVE, enable_order_submission=False, allow_live_trading=False
        )
    with pytest.raises(SafetyViolation):
        order_submission_permitted(
            TradingMode.LIVE, enable_order_submission=True, allow_live_trading=True
        )


def test_non_enum_mode_rejected() -> None:
    with pytest.raises(SafetyViolation, match="Unknown trading mode"):
        validate_safety_flags(
            "paper",  # type: ignore[arg-type]
            enable_order_submission=True,
            allow_live_trading=False,
        )


def test_sim_mode_is_supported_but_can_never_submit_orders():
    from semiedge.safety.modes import (
        SUPPORTED_MODES,
        assert_order_submission_allowed,
        order_submission_permitted,
    )

    assert TradingMode("sim") is TradingMode.SIM
    assert TradingMode.SIM in SUPPORTED_MODES
    assert not order_submission_permitted(
        TradingMode.SIM, enable_order_submission=False, allow_live_trading=False
    )
    with pytest.raises(SafetyViolation):
        order_submission_permitted(
            TradingMode.SIM, enable_order_submission=True, allow_live_trading=False
        )
    with pytest.raises(SafetyViolation):
        assert_order_submission_allowed(
            TradingMode.SIM, enable_order_submission=False, allow_live_trading=False
        )
    with pytest.raises(SafetyViolation):
        order_submission_permitted(
            TradingMode.SIM, enable_order_submission=False, allow_live_trading=True
        )
