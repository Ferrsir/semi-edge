"""``semiedge account``: the real paper-factory client over a fake transport (issue #14)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from semiedge import cli

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"

pytestmark = pytest.mark.integration

Factory = Callable[[dict[str, tuple[int, object]]], tuple[Any, Any]]


@pytest.fixture
def _credentials(monkeypatch: pytest.MonkeyPatch, fake_credentials: Any) -> None:
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", fake_credentials.key_id)
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", fake_credentials.secret)


@pytest.mark.usefixtures("_credentials")
def test_account_prints_masked_read_only_snapshot(
    *,
    paper_client_factory: Factory,
    account_payload: dict[str, Any],
    clock_payload: dict[str, Any],
    positions_payload: list[dict[str, Any]],
    fake_credentials: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, transport = paper_client_factory(
        {
            "/account": (200, account_payload),
            "/clock": (200, clock_payload),
            "/positions": (200, positions_payload),
        }
    )
    built: list[object] = []

    def factory(settings: Any) -> Any:
        assert settings.alpaca_credentials() == (fake_credentials.key_id, fake_credentials.secret)
        built.append(client)
        return client

    monkeypatch.setattr(cli, "paper_account_client", factory)
    assert cli.main(["account", "--config-dir", str(CONFIG_DIR)]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert built == [client]
    assert [method for method, _ in transport.requests] == ["GET", "GET", "GET"]
    assert report["paper"] is True
    assert report["orders_submitted"] == 0
    assert report["account_number"].startswith("****")
    assert report["is_tradable"] is True
    assert report["block_reasons"] == []
    assert [p["symbol"] for p in report["positions"]] == ["AMD", "NVDA"]
    text = captured.out + captured.err
    for secret in (
        fake_credentials.account_number,
        fake_credentials.key_id,
        fake_credentials.secret,
    ):
        assert secret not in text


@pytest.mark.usefixtures("_credentials")
def test_account_reports_blocking_flags(
    paper_client_factory: Factory,
    account_payload: dict[str, Any],
    clock_payload: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    account_payload["trading_blocked"] = True
    client, _ = paper_client_factory(
        {
            "/account": (200, account_payload),
            "/clock": (200, clock_payload),
            "/positions": (200, []),
        }
    )
    monkeypatch.setattr(cli, "paper_account_client", lambda settings: client)
    assert cli.main(["account", "--config-dir", str(CONFIG_DIR)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["is_tradable"] is False
    assert report["block_reasons"] == ["trading_blocked"]


@pytest.mark.usefixtures("_credentials")
@pytest.mark.parametrize(
    ("status", "body"),
    [(500, {"message": "internal error with secret-ish detail"}), (200, {"trading_blocked": 0})],
)
def test_account_broker_failures_exit_4_sanitised(
    *,
    paper_client_factory: Factory,
    clock_payload: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: int,
    body: object,
) -> None:
    client, _ = paper_client_factory(
        {"/account": (status, body), "/clock": (200, clock_payload), "/positions": (200, [])}
    )
    monkeypatch.setattr(cli, "paper_account_client", lambda settings: client)
    assert cli.main(["account", "--config-dir", str(CONFIG_DIR)]) == cli.EXIT_DATA
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "secret-ish" not in captured.err
    assert "paper-api.alpaca.markets" not in captured.err


def test_account_requires_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["account", "--config-dir", str(CONFIG_DIR)]) == cli.EXIT_CREDENTIALS
    assert "ALPACA_PAPER_API_KEY" in capsys.readouterr().err


def test_account_uses_the_paper_factory(
    monkeypatch: pytest.MonkeyPatch, fake_credentials: Any
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_factory(key: str, secret: str) -> object:
        calls.append((key, secret))
        raise cli.SafetyViolation("refused")

    monkeypatch.setenv("ALPACA_PAPER_API_KEY", fake_credentials.key_id)
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", fake_credentials.secret)
    monkeypatch.setattr(cli, "create_paper_trading_client", fake_factory)
    assert cli.main(["account", "--config-dir", str(CONFIG_DIR)]) == cli.EXIT_CONFIG
    assert calls == [(fake_credentials.key_id, fake_credentials.secret)]
