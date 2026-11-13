"""Typed, validated configuration models loaded from ``config/*.yaml``.

All models are frozen and reject unknown keys so a typo can never silently fall back
to a default. Risk limits are additionally bounded by the pre-approved ceilings in
``semiedge.safety.limits`` (config may tighten, never loosen).
"""

from __future__ import annotations

import math
from datetime import date
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from semiedge.data.schema import Adjustment, DataFeed
from semiedge.safety.limits import RISK_LIMIT_CEILINGS


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @field_validator("*", mode="before")
    @classmethod
    def _require_numeric_and_boolean_types(cls, value: object, info: ValidationInfo) -> object:
        annotation = cls.model_fields[info.field_name or ""].annotation
        if annotation is float and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError("expected a numeric value, not a string or boolean")
        if annotation is int and type(value) is not int:
            raise ValueError("expected an integer, not a coerced value")
        boolean = annotation is bool or get_args(annotation) == (False,)
        if boolean and type(value) is not bool:
            raise ValueError("expected a boolean, not a coerced value")
        return value


# ------------------------------------------------------------------ universe


class UniverseMember(_Frozen):
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,9}$")
    name: str | None = None
    group: str
    role: Literal["tradable", "benchmark"]


class UniverseConfig(_Frozen):
    """The monitored security list (``config/universe.yaml``)."""

    members: tuple[UniverseMember, ...]
    regime_benchmark: str = "SMH"
    comparison_benchmarks: tuple[str, ...] = ("SMH", "QQQ")

    @model_validator(mode="after")
    def _check(self) -> UniverseConfig:
        symbols = [m.symbol for m in self.members]
        dupes = sorted({s for s in symbols if symbols.count(s) > 1})
        if dupes:
            raise ValueError(f"duplicate universe symbols: {dupes}")
        benchmarks = {m.symbol for m in self.members if m.role == "benchmark"}
        if self.regime_benchmark not in benchmarks:
            raise ValueError(
                f"regime_benchmark {self.regime_benchmark!r} must be a member with role=benchmark"
            )
        missing = [b for b in self.comparison_benchmarks if b not in benchmarks]
        if missing:
            raise ValueError(f"comparison_benchmarks not declared as benchmarks: {missing}")
        if not any(m.role == "tradable" for m in self.members):
            raise ValueError("universe must contain at least one tradable symbol")
        return self

    @property
    def tradable_symbols(self) -> tuple[str, ...]:
        return tuple(m.symbol for m in self.members if m.role == "tradable")

    @property
    def benchmark_symbols(self) -> tuple[str, ...]:
        return tuple(m.symbol for m in self.members if m.role == "benchmark")

    @property
    def all_symbols(self) -> tuple[str, ...]:
        return tuple(m.symbol for m in self.members)


# ------------------------------------------------------------------ strategy


class ScoreWeights(_Frozen):
    """Ranking weights. Heuristic priors - NOT statistically validated (see DECISIONS.md)."""

    relative_strength: float = Field(0.40, ge=0)
    breakout: float = Field(0.30, ge=0)
    relative_volume: float = Field(0.20, ge=0)
    trend_persistence: float = Field(0.10, ge=0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> ScoreWeights:
        total = self.relative_strength + self.breakout + self.relative_volume
        total += self.trend_persistence
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError(f"score weights must sum to 1.0 (got {total})")
        return self


class ScoreNormalization(_Frozen):
    """Saturation points of the linear 0..1 ramps used by the ranking score.

    See docs/specifications/momentum_v1.md for the exact formulas.
    """

    relative_strength_saturation: float = Field(0.15, gt=0)
    breakout_atr_saturation: float = Field(1.0, gt=0)
    relative_volume_saturation: float = Field(3.0, gt=0)
    trend_persistence_window: int = Field(20, ge=1, le=250)


class MomentumV1Params(_Frozen):
    """Parameters of the ``momentum_v1`` long-only sector-relative momentum breakout."""

    name: Literal["momentum_v1"] = "momentum_v1"
    version: str = "1.0.0"
    regime_sma_window: int = Field(50, ge=2)
    fast_sma_window: int = Field(20, ge=2)
    slow_sma_window: int = Field(50, ge=2)
    breakout_lookback: int = Field(20, ge=1)
    return_lookback: int = Field(20, ge=1)
    min_excess_return: float = 0.02
    volume_lookback: int = Field(20, ge=1)
    min_relative_volume: float = Field(1.2, gt=0)
    atr_window: int = Field(14, ge=1)
    stop_atr_multiple: float = Field(2.0, gt=0)
    target_atr_multiple: float = Field(4.0, gt=0)
    max_holding_sessions: int = Field(5, ge=1)
    score_weights: ScoreWeights = ScoreWeights()
    score_normalization: ScoreNormalization = ScoreNormalization()

    @model_validator(mode="after")
    def _check(self) -> MomentumV1Params:
        if self.fast_sma_window >= self.slow_sma_window:
            raise ValueError("fast_sma_window must be shorter than slow_sma_window")
        norm = self.score_normalization
        if norm.relative_strength_saturation <= self.min_excess_return:
            raise ValueError("relative_strength_saturation must exceed min_excess_return")
        if norm.relative_volume_saturation <= self.min_relative_volume:
            raise ValueError("relative_volume_saturation must exceed min_relative_volume")
        return self

    def required_history(self) -> int:
        """Minimum number of completed sessions needed for every input to be defined."""
        return max(
            self.regime_sma_window,
            self.slow_sma_window,
            self.fast_sma_window,
            self.breakout_lookback + 1,
            self.return_lookback + 1,
            self.volume_lookback + 1,
            self.atr_window + 1,
            self.score_normalization.trend_persistence_window + self.slow_sma_window - 1,
        )


# ------------------------------------------------------------------ risk


class RiskLimits(_Frozen):
    """Portfolio and per-trade limits (``config/risk.yaml``). Fractions of equity.

    Values may be tightened below the pre-approved ceilings, never raised above them.
    """

    max_risk_per_trade_pct: float = Field(RISK_LIMIT_CEILINGS.max_risk_per_trade_pct, gt=0)
    max_position_pct: float = Field(RISK_LIMIT_CEILINGS.max_position_pct, gt=0)
    max_gross_exposure_pct: float = Field(RISK_LIMIT_CEILINGS.max_gross_exposure_pct, gt=0)
    max_positions: int = Field(RISK_LIMIT_CEILINGS.max_positions, ge=1)
    daily_loss_halt_pct: float = Field(RISK_LIMIT_CEILINGS.daily_loss_halt_pct, gt=0)
    drawdown_halt_pct: float = Field(RISK_LIMIT_CEILINGS.drawdown_halt_pct, gt=0)
    allow_leverage: Literal[False] = False
    allow_short_selling: Literal[False] = False
    allow_options: Literal[False] = False
    allow_extended_hours: Literal[False] = False
    earnings_blackout_sessions: int = Field(3, ge=0, le=20)
    require_verified_earnings_data: bool = True

    @model_validator(mode="after")
    def _within_ceilings(self) -> RiskLimits:
        names = (
            "max_risk_per_trade_pct",
            "max_position_pct",
            "max_gross_exposure_pct",
            "max_positions",
            "daily_loss_halt_pct",
            "drawdown_halt_pct",
        )
        violations = [
            f"{n}={getattr(self, n)} > ceiling {getattr(RISK_LIMIT_CEILINGS, n)}"
            for n in names
            if getattr(self, n) > getattr(RISK_LIMIT_CEILINGS, n)
        ]
        if violations:
            raise ValueError(
                "risk limits exceed pre-approved ceilings (human approval required to change "
                "semiedge/safety/limits.py): " + "; ".join(violations)
            )
        if self.max_position_pct > self.max_gross_exposure_pct:
            raise ValueError("max_position_pct cannot exceed max_gross_exposure_pct")
        return self


# ------------------------------------------------------------------ data & scanner


class DataConfig(_Frozen):
    daily_feed: DataFeed = DataFeed.SIP
    adjustment: Adjustment = Adjustment.SPLIT
    history_start: date = date(2016, 1, 4)
    calendar: Literal["XNYS"] = "XNYS"
    bar_settle_minutes: int = Field(
        30,
        ge=0,
        le=240,
        description="Minutes after the session close before that session's daily bar is "
        "treated as complete.",
    )
    max_missing_sessions_in_lookback: int = Field(0, ge=0)

    @field_validator("daily_feed")
    @classmethod
    def _no_synthetic(cls, v: DataFeed) -> DataFeed:
        if v is DataFeed.SYNTHETIC:
            raise ValueError("synthetic is not a vendor feed; use the synthetic source explicitly")
        return v


class ScannerConfig(_Frozen):
    reference_equity: float = Field(
        100_000.0,
        gt=0,
        description="Equity used for PROPOSED sizes in scanner mode when no account is used.",
    )


class AppConfig(_Frozen):
    """Everything loaded from the YAML configuration directory."""

    universe: UniverseConfig
    strategy: MomentumV1Params
    risk: RiskLimits
    data: DataConfig = DataConfig()
    scanner: ScannerConfig = ScannerConfig()


class CostSettings(_Frozen):
    slippage_bps: float = Field(ge=0, lt=10_000)
    commission_per_share: float = Field(ge=0)
    commission_per_order: float = Field(ge=0)
    sell_fee_rate: float = Field(ge=0)


class BacktestSettings(_Frozen):
    initial_equity: float = Field(gt=0)
    costs: CostSettings
    earnings_policy: Literal["require_data", "waive_unavailable"]


# ------------------------------------------------------------------ earnings (#20)


class EarningsSettings(_Frozen):
    """``config/earnings.yaml`` (spec earnings-calendar.md §5). Every field is required."""

    lookahead_days: int = Field(ge=14, le=89)
    min_forward_coverage_days: int = Field(ge=7)
    max_age_hours: float = Field(gt=0, le=48)
    max_attempts_per_day: int = Field(ge=1, le=5)
    retry_interval_minutes: int = Field(ge=15, le=1440)
    request_timeout_seconds: float = Field(gt=0, le=60)
    finnhub_min_interval_seconds: float = Field(ge=1, le=60)
    override_file: str = Field(pattern=r"^[A-Za-z0-9_\-]+\.ya?ml$")
    override_max_age_days: int = Field(ge=1, le=90)

    @model_validator(mode="after")
    def _coverage_inside_window(self) -> EarningsSettings:
        if self.min_forward_coverage_days >= self.lookahead_days:
            raise ValueError("min_forward_coverage_days must be below lookahead_days")
        return self
