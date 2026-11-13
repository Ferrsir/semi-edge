"""Environment-derived runtime settings (secrets and safety flags).

Secrets come ONLY from environment variables (or a local, git-ignored ``.env``).
Safety flags are validated at construction: an unsafe combination raises
``SafetyViolation`` before anything else runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from semiedge.safety.modes import TradingMode, order_submission_permitted, validate_safety_flags

REQUIRED_ALPACA_ENV_VARS: tuple[str, ...] = ("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_SECRET_KEY")


class MissingCredentialsError(RuntimeError):
    """Raised when an operation needs credentials that are not configured."""


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )

    trading_mode: TradingMode = Field(TradingMode.SCANNER, validation_alias="TRADING_MODE")
    enable_order_submission: bool = Field(False, validation_alias="ENABLE_ORDER_SUBMISSION")
    allow_live_trading: bool = Field(False, validation_alias="ALLOW_LIVE_TRADING")

    alpaca_paper_api_key: SecretStr | None = Field(None, validation_alias="ALPACA_PAPER_API_KEY")
    alpaca_paper_secret_key: SecretStr | None = Field(
        None, validation_alias="ALPACA_PAPER_SECRET_KEY"
    )
    discord_webhook_url: SecretStr | None = Field(None, validation_alias="DISCORD_WEBHOOK_URL")
    # Earnings calendar (#20): read-only data APIs; never logged (included in secret_values).
    finnhub_api_key: SecretStr | None = Field(None, validation_alias="FINNHUB_API_KEY")
    alphavantage_api_key: SecretStr | None = Field(None, validation_alias="ALPHAVANTAGE_API_KEY")

    config_dir: Path = Field(Path("config"), validation_alias="SEMIEDGE_CONFIG_DIR")
    data_dir: Path = Field(Path("data"), validation_alias="SEMIEDGE_DATA_DIR")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        "INFO", validation_alias="SEMIEDGE_LOG_LEVEL"
    )
    log_format: Literal["json", "text"] = Field("json", validation_alias="SEMIEDGE_LOG_FORMAT")

    @field_validator("trading_mode", mode="before")
    @classmethod
    def _normalise_mode(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _safety(self) -> AppSettings:
        validate_safety_flags(
            self.trading_mode,
            enable_order_submission=self.enable_order_submission,
            allow_live_trading=self.allow_live_trading,
        )
        return self

    @property
    def order_submission_permitted(self) -> bool:
        return order_submission_permitted(
            self.trading_mode,
            enable_order_submission=self.enable_order_submission,
            allow_live_trading=self.allow_live_trading,
        )

    @property
    def has_alpaca_credentials(self) -> bool:
        return bool(
            self.alpaca_paper_api_key
            and self.alpaca_paper_api_key.get_secret_value()
            and self.alpaca_paper_secret_key
            and self.alpaca_paper_secret_key.get_secret_value()
        )

    def alpaca_credentials(self) -> tuple[str, str]:
        """Return ``(key_id, secret)`` or raise :class:`MissingCredentialsError`."""
        if not self.has_alpaca_credentials:
            raise MissingCredentialsError(
                "Alpaca PAPER credentials are not configured. Set environment variables "
                + " and ".join(REQUIRED_ALPACA_ENV_VARS)
                + " (paper keys only; see .env.example)."
            )
        key, secret = self.alpaca_paper_api_key, self.alpaca_paper_secret_key
        if key is None or secret is None:  # unreachable; narrows types for mypy
            raise MissingCredentialsError("Alpaca PAPER credentials are not configured.")
        return key.get_secret_value(), secret.get_secret_value()

    def secret_values(self) -> list[str]:
        """All configured secret values, for log redaction."""
        secrets = (
            self.alpaca_paper_api_key,
            self.alpaca_paper_secret_key,
            self.discord_webhook_url,
            self.finnhub_api_key,
            self.alphavantage_api_key,
        )
        return [s.get_secret_value() for s in secrets if s is not None and s.get_secret_value()]
