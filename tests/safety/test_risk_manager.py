"""Safety tests for the independent risk manager (issue #19; spec phase2-4-interfaces.md §2.6)."""

from __future__ import annotations

import math
import random  # deterministic seeded test data, not cryptography
from dataclasses import asdict, replace
from datetime import UTC, date, datetime

import pytest

from semiedge.config.models import RiskLimits
from semiedge.data.calendar import TradingCalendar
from semiedge.risk import (
    CHECK_IDS,
    RiskManager,
    UnavailableEarningsCalendar,
    limits_fingerprint,
    propose_position_size,
)
from semiedge.risk.types import (
    EarningsInfo,
    EntryProposal,
    Halt,
    HaltKind,
    OrderSide,
    PendingOrderView,
    PortfolioView,
    PositionView,
)
from semiedge.safety.modes import SafetyViolation, TradingMode

pytestmark = pytest.mark.safety

# ruff: noqa: S311 - seeded pseudo-random inputs make the property tests reproducible

SESSION = date(2024, 6, 28)  # Friday
AS_OF = datetime(2024, 6, 28, 21, 0, tzinfo=UTC)
CAL = TradingCalendar()
TRADABLE = frozenset({"AAA", "BBB", "CCC", "DDD", "EEE", "FFF"})


class VerifiedCalendar:
    """Verified earnings source with fixed next-report sessions (None = no upcoming report)."""

    def __init__(self, upcoming: dict[str, date | None] | None = None) -> None:
        self.upcoming = upcoming or {}

    def next_earnings(self, symbol: str, on_or_after: date) -> EarningsInfo:
        return EarningsInfo(symbol, self.upcoming.get(symbol), verified=True, source="test")


def _manager(limits: RiskLimits | None = None, **kwargs: object) -> RiskManager:
    params: dict[str, object] = {
        "calendar": CAL,
        "earnings": VerifiedCalendar(),
        "tradable_symbols": TRADABLE,
    }
    params.update(kwargs)
    return RiskManager(limits or RiskLimits(), **params)  # type: ignore[arg-type]


def _proposal(symbol: str = "AAA", **changes: object) -> EntryProposal:
    # entry 100, stop 95: risk budget 0.25% x 100k / 5 = 50; allocation 5% x 100k / 100 = 50.
    base = EntryProposal(
        symbol=symbol,
        decision_session=SESSION,
        data_as_of=SESSION,
        strategy="momentum_v1",
        strategy_version="1.0.0",
        rank=1,
        score=80.0,
        entry_reference=100.0,
        atr=2.5,
        stop=95.0,
        target=110.0,
        proposed_shares=50,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def _portfolio(**changes: object) -> PortfolioView:
    base = PortfolioView(
        mode=TradingMode.PAPER,
        as_of=AS_OF,
        session=SESSION,
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
        cash=100_000.0,
        positions=(),
        pending_orders=(),
        halts=(),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def _one(manager: RiskManager, proposal: EntryProposal, portfolio: PortfolioView):  # type: ignore[no-untyped-def]
    [decision] = manager.evaluate_entries([proposal], portfolio)
    return decision


def _check(decision, check_id: str):  # type: ignore[no-untyped-def]
    return next(c for c in decision.checks if c.check_id == check_id)


# ------------------------------------------------------------------ happy path and recording


def test_valid_proposal_is_approved_with_every_check_recorded_in_order() -> None:
    decision = _one(_manager(), _proposal(), _portfolio())
    assert decision.approved
    assert decision.approved_shares == 50
    assert decision.reasons == ()
    assert tuple(c.check_id for c in decision.checks) == CHECK_IDS
    assert all(c.passed for c in decision.checks)
    assert decision.decided_at == AS_OF
    assert decision.limits_fingerprint == limits_fingerprint(RiskLimits())


def test_limits_fingerprint_is_sorted_compact_json_sha256() -> None:
    import hashlib
    import json

    limits = RiskLimits()
    expected = hashlib.sha256(
        json.dumps(limits.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert limits_fingerprint(limits) == expected
    assert limits_fingerprint(RiskLimits(max_positions=3)) != expected


# ------------------------------------------------------------------ construction (fail closed)


def test_limits_above_ceiling_bypassing_validation_raise() -> None:
    with pytest.raises(SafetyViolation, match="max_position_pct"):
        _manager(RiskLimits.model_construct(max_position_pct=0.5))


@pytest.mark.parametrize("flag", ["allow_leverage", "allow_short_selling", "allow_options",
                                  "allow_extended_hours"])  # fmt: skip
def test_allow_flags_bypassing_validation_raise(flag: str) -> None:
    with pytest.raises(SafetyViolation, match=flag):
        _manager(RiskLimits.model_construct(**{flag: True}))


def test_unverified_earnings_requirement_cannot_be_disabled() -> None:
    with pytest.raises(SafetyViolation, match="require_verified_earnings_data"):
        _manager(RiskLimits(require_verified_earnings_data=False))


@pytest.mark.parametrize("value", [math.nan, math.inf, 0.0, -0.01])
def test_non_finite_or_non_positive_limits_raise(value: float) -> None:
    with pytest.raises(SafetyViolation):
        _manager(RiskLimits.model_construct(max_risk_per_trade_pct=value))


def test_unknown_waiver_raises() -> None:
    with pytest.raises(SafetyViolation, match="earnings_waiver"):
        _manager(earnings_waiver="always")


def test_limits_property_returns_the_validated_limits() -> None:
    limits = RiskLimits(max_positions=3)
    assert _manager(limits).limits is limits


# ------------------------------------------------------------------ R01..R09 reject checks


@pytest.mark.parametrize(
    ("mode", "waiver", "ok"),
    [
        (TradingMode.PAPER, "none", True),
        (TradingMode.BACKTEST, "none", True),
        (TradingMode.SCANNER, "none", False),
        (TradingMode.LIVE, "none", False),
        (TradingMode.BACKTEST, "backtest_unavailable", True),
        (TradingMode.PAPER, "backtest_unavailable", False),
    ],
)
def test_r01_mode(mode: TradingMode, waiver: str, ok: bool) -> None:
    decision = _one(_manager(earnings_waiver=waiver), _proposal(), _portfolio(mode=mode))
    assert decision.approved is ok
    assert ("invalid_mode" in decision.reasons) is not ok


@pytest.mark.parametrize(
    "changes",
    [
        {"proposal": {"entry_reference": math.nan}},
        {"proposal": {"stop": math.inf}},
        {"proposal": {"score": -math.inf}},
        {"proposal": {"proposed_shares": -1}},
        {"proposal": {"proposed_shares": 2.5}},
        {"proposal": {"proposed_shares": True}},
        {"proposal": {"rank": 0}},
        {"portfolio": {"equity": 0.0}},
        {"portfolio": {"equity": math.nan}},
        {"portfolio": {"start_of_day_equity": -1.0}},
        {"portfolio": {"peak_equity": 99_999.0}},  # peak below equity
        {"portfolio": {"cash": -0.01}},
        {"portfolio": {"positions": (PositionView("ZZZ", 0, 10.0, 10.0),)}},
        {"portfolio": {"positions": (PositionView("ZZZ", 5, 10.0, math.nan),)}},
        {
            "portfolio": {
                "pending_orders": (PendingOrderView("ZZZ", OrderSide.BUY, -3, 10.0, True),)
            }
        },
    ],
)
def test_r02_invalid_inputs_reject_without_exception(changes: dict[str, dict[str, object]]) -> None:
    decision = _one(
        _manager(),
        _proposal(**changes.get("proposal", {})),
        _portfolio(**changes.get("portfolio", {})),
    )
    assert not decision.approved
    assert decision.approved_shares == 0
    assert "invalid_input" in decision.reasons
    assert tuple(c.check_id for c in decision.checks) == CHECK_IDS  # still all recorded


@pytest.mark.parametrize("kind", list(HaltKind))
def test_r03_any_persisted_halt_blocks_entries(kind: HaltKind) -> None:
    halt = Halt(kind, "test", AS_OF)
    decision = _one(_manager(), _proposal(), _portfolio(halts=(halt,)))
    assert not decision.approved
    assert f"halt_active:{kind}" in decision.reasons


def test_r04_long_only() -> None:
    decision = _one(_manager(), _proposal(side=OrderSide.SELL), _portfolio())
    assert decision.reasons[0] == "not_long"


def test_r05_tradable() -> None:
    decision = _one(_manager(), _proposal("SMH"), _portfolio())
    assert "not_tradable" in decision.reasons


@pytest.mark.parametrize(
    ("proposal", "portfolio"),
    [
        ({"data_as_of": date(2024, 6, 27)}, {}),
        ({"decision_session": date(2024, 6, 27)}, {}),
        ({}, {"session": date(2024, 7, 1)}),
    ],
)
def test_r06_fresh(proposal: dict[str, object], portfolio: dict[str, object]) -> None:
    decision = _one(_manager(), _proposal(**proposal), _portfolio(**portfolio))
    assert "stale_data" in decision.reasons


@pytest.mark.parametrize(
    "levels",
    [
        {"stop": 0.0},
        {"stop": 100.0},  # stop == entry
        {"target": 100.0},  # target == entry
        {"target": 99.0},
        {"atr": 0.0},
        {"stop": -1.0},
    ],
)
def test_r07_levels_boundaries(levels: dict[str, float]) -> None:
    decision = _one(_manager(), _proposal(**levels), _portfolio())
    assert not decision.approved
    assert "invalid_levels" in decision.reasons


def test_r08_duplicate_position_pending_and_batch() -> None:
    held = _portfolio(positions=(PositionView("AAA", 10, 90.0, 100.0),))
    assert "duplicate_or_conflict" in _one(_manager(), _proposal(), held).reasons
    sell = PendingOrderView("AAA", OrderSide.SELL, 5, 100.0, is_entry=False)
    assert "duplicate_or_conflict" in _one(_manager(), _proposal(), _portfolio(
        pending_orders=(sell,))).reasons  # fmt: skip
    first, second = _manager().evaluate_entries(
        [_proposal(rank=1), _proposal(rank=2)], _portfolio()
    )
    assert first.approved
    assert "duplicate_or_conflict" in second.reasons


def test_r09_max_positions_boundary_counts_pending_entries() -> None:
    limits = RiskLimits(max_positions=2)
    one_held = _portfolio(positions=(PositionView("BBB", 1, 100.0, 100.0),))
    assert _one(_manager(limits), _proposal(), one_held).approved  # 1 < 2
    pending = PendingOrderView("CCC", OrderSide.BUY, 1, 100.0, is_entry=True)
    full = replace(one_held, pending_orders=(pending,))
    assert "max_positions" in _one(_manager(limits), _proposal(), full).reasons  # 2 < 2 fails


# ------------------------------------------------------------------ R10..R13 downsizing


def test_r10_r11_use_propose_position_size_exactly() -> None:
    proposal = _proposal(entry_reference=123.45, stop=117.0, target=140.0, proposed_shares=10_000)
    decision = _one(_manager(), proposal, _portfolio())
    sized = propose_position_size(
        100_000.0, 123.45, 117.0, max_risk_per_trade_pct=0.0025, max_position_pct=0.05
    )
    assert _check(decision, "R10_risk_per_trade").observed == sized.shares_by_risk
    assert _check(decision, "R11_position_cap").observed == sized.shares_by_allocation
    assert decision.approved_shares == sized.shares
    assert decision.approved
    assert all(r.startswith("downsized:R1") for r in decision.reasons)
    assert decision.reasons


def test_zero_share_sizing_carries_the_sizing_reason() -> None:
    proposal = _proposal(entry_reference=100.0, stop=1.0, target=200.0, atr=40.0)
    small = _portfolio(
        equity=1_000.0, start_of_day_equity=1_000.0, peak_equity=1_000.0, cash=1_000.0
    )
    decision = _one(_manager(), proposal, small)
    sized = propose_position_size(1_000.0, 100.0, 1.0, max_risk_per_trade_pct=0.0025,
                                  max_position_pct=0.05)  # fmt: skip
    assert sized.shares == 0
    assert sized.reason is not None
    assert sized.reason in _check(decision, "R10_risk_per_trade").detail
    assert "size_below_one_share" in decision.reasons


def test_pending_orders_count_against_gross_exposure() -> None:
    """S-004: 16% held + a pending 4% entry leaves no room for a new 5% proposal."""
    positions = tuple(PositionView(s, 40, 100.0, 100.0) for s in ("BBB", "CCC", "DDD", "EEE"))
    held = sum(p.qty * p.mark_price for p in positions)
    assert held == 16_000.0
    pending = PendingOrderView("FFF", OrderSide.BUY, 40, 100.0, is_entry=True)
    decision = _one(_manager(RiskLimits(max_positions=4)), _proposal(),
                    _portfolio(positions=positions[:3], pending_orders=(pending,)))  # fmt: skip
    # 12% held + 4% pending = 16%: cap 20% leaves 4,000 / 100 = 40 shares.
    assert _check(decision, "R12_gross_exposure").observed == 40.0
    # 16% held + 4% pending = 20%: no room left (R09 also blocks: 4 + 1 >= 4).
    full = _portfolio(positions=positions, pending_orders=(pending,))
    decision = _one(_manager(), _proposal(), full)
    assert _check(decision, "R12_gross_exposure").observed == 0.0
    assert not decision.approved
    assert "size_below_one_share" in decision.reasons


def test_pending_sells_never_free_exposure_or_cash() -> None:
    sell = PendingOrderView("BBB", OrderSide.SELL, 1_000, 100.0, is_entry=False)
    with_sell = _one(_manager(), _proposal(), _portfolio(cash=2_000.0, pending_orders=(sell,)))
    without = _one(_manager(), _proposal(), _portfolio(cash=2_000.0))
    assert _check(with_sell, "R13_cash").observed == _check(without, "R13_cash").observed == 20.0


def test_r13_cash_downsizes() -> None:
    decision = _one(_manager(), _proposal(), _portfolio(cash=3_050.0))
    assert decision.approved
    assert decision.approved_shares == 30
    assert "downsized:R13_cash" in decision.reasons


# ------------------------------------------------------------------ R14..R16


def test_r14_daily_loss_boundary() -> None:
    ok = _portfolio(equity=99_001.0, peak_equity=100_000.0)  # -0.999% > -1%
    assert _one(_manager(), _proposal(), ok).approved
    at_limit = _portfolio(equity=99_000.0)  # exactly -1%: not strictly greater
    decision = _one(_manager(), _proposal(), at_limit)
    assert "daily_loss_halt" in decision.reasons
    assert HaltKind.DAILY_LOSS in {h.kind for h in _manager().entry_halts(at_limit)}


def test_r15_drawdown_boundary() -> None:
    peak = 105_000.0
    ok = _portfolio(equity=99_751.0, start_of_day_equity=99_751.0, peak_equity=peak)
    assert _one(_manager(), _proposal(), ok).approved  # -4.999% > -5%
    at_limit = _portfolio(equity=99_750.0, start_of_day_equity=99_750.0, peak_equity=peak)
    decision = _one(_manager(), _proposal(), at_limit)
    assert "drawdown_halt" in decision.reasons
    assert HaltKind.DRAWDOWN in {h.kind for h in _manager().entry_halts(at_limit)}


def test_entry_halts_includes_persisted_halts() -> None:
    halt = Halt(HaltKind.MANUAL, "owner", AS_OF)
    assert _manager().entry_halts(_portfolio(halts=(halt,))) == (halt,)


def test_r16_unverified_earnings_rejects_paper() -> None:
    manager = _manager(earnings=UnavailableEarningsCalendar())
    decision = _one(manager, _proposal(), _portfolio())
    assert not decision.approved
    assert "earnings_data_unavailable" in decision.reasons


def test_r16_backtest_waiver_is_recorded_and_paper_waiver_rejected() -> None:
    manager = _manager(
        earnings=UnavailableEarningsCalendar(), earnings_waiver="backtest_unavailable"
    )
    decision = _one(manager, _proposal(), _portfolio(mode=TradingMode.BACKTEST))
    assert decision.approved
    assert _check(decision, "R16_earnings").detail.startswith("waived")
    paper = _one(manager, _proposal(), _portfolio(mode=TradingMode.PAPER))
    assert "invalid_mode" in paper.reasons


@pytest.mark.parametrize(
    ("upcoming", "ok"),
    [
        (date(2024, 6, 28), False),  # k = 0 (report on the decision session)
        (date(2024, 7, 1), False),  # k = 1
        (date(2024, 7, 3), False),  # k = 3 = blackout
        (date(2024, 7, 5), True),  # k = 4: 7/4 is a holiday (Jul 1, 2, 3, 5)
        (None, True),
    ],
)
def test_r16_blackout_window_uses_the_exchange_calendar(upcoming: date | None, ok: bool) -> None:
    manager = _manager(earnings=VerifiedCalendar({"AAA": upcoming}))
    decision = _one(manager, _proposal(), _portfolio())
    assert decision.approved is ok
    if not ok:
        assert "earnings_blackout" in decision.reasons


# ------------------------------------------------------------------ batch behaviour


def _five() -> list[EntryProposal]:
    return [_proposal(s, rank=i + 1, proposed_shares=10) for i, s in enumerate("ABCDE")]


def test_batch_accumulation_respects_max_positions() -> None:
    manager = _manager(tradable_symbols=frozenset("ABCDE"))
    decisions = manager.evaluate_entries(_five(), _portfolio())
    assert [d.approved for d in decisions] == [True, True, True, True, False]
    assert "max_positions" in decisions[4].reasons


def test_batch_order_independent_of_input_sequence() -> None:
    manager = _manager(tradable_symbols=frozenset("ABCDE"))
    expected = manager.evaluate_entries(_five(), _portfolio())
    shuffled = _five()
    random.Random(7).shuffle(shuffled)
    assert manager.evaluate_entries(shuffled, _portfolio()) == expected


def test_inputs_are_not_mutated() -> None:
    proposals, portfolio = _five(), _portfolio()
    snapshot = ([asdict(p) for p in proposals], asdict(portfolio))
    _manager(tradable_symbols=frozenset("ABCDE")).evaluate_entries(proposals, portfolio)
    assert ([asdict(p) for p in proposals], asdict(portfolio)) == snapshot


def test_exception_inside_a_check_is_an_internal_error_rejection() -> None:
    class Broken:
        def next_earnings(self, symbol: str, on_or_after: date) -> EarningsInfo:
            raise RuntimeError("source down")

    decision = _one(_manager(earnings=Broken()), _proposal(), _portfolio())
    assert not decision.approved
    assert decision.reasons == ("internal_error",)
    assert "RuntimeError" in decision.checks[0].detail


# ------------------------------------------------------------------ determinism and properties


def _random_case(rng: random.Random) -> tuple[list[EntryProposal], PortfolioView]:
    equity = rng.uniform(5_000, 500_000)
    symbols = rng.sample(sorted(TRADABLE), rng.randint(0, 3))
    positions = tuple(
        PositionView(s, rng.randint(1, 50), 100.0, rng.uniform(10, 300)) for s in symbols
    )
    portfolio = _portfolio(
        equity=equity,
        start_of_day_equity=equity * rng.uniform(0.98, 1.02),
        peak_equity=equity * rng.uniform(1.0, 1.08),
        cash=rng.uniform(0, equity),
        positions=positions,
    )
    proposals = []
    for i, symbol in enumerate(sorted(TRADABLE)):
        entry = rng.uniform(5, 500)
        atr = entry * rng.uniform(0.005, 0.08)
        proposals.append(_proposal(symbol, rank=i + 1, entry_reference=entry, atr=atr,
                                   stop=entry - 2 * atr, target=entry + 4 * atr,
                                   proposed_shares=rng.randint(0, 2_000)))  # fmt: skip
    return proposals, portfolio


def test_determinism_over_random_portfolios() -> None:
    manager = _manager()
    for seed in range(1_000):
        proposals, portfolio = _random_case(random.Random(seed))
        first = [asdict(d) for d in manager.evaluate_entries(proposals, portfolio)]
        second = [asdict(d) for d in manager.evaluate_entries(proposals, portfolio)]
        assert first == second


def test_property_post_approval_exposure_and_count_within_limits() -> None:
    limits = RiskLimits()
    manager = _manager(limits)
    for seed in range(300):
        proposals, portfolio = _random_case(random.Random(seed))
        decisions = manager.evaluate_entries(proposals, portfolio)
        approved = [d for d in decisions if d.approved]
        held = sum(p.qty * p.mark_price for p in portfolio.positions)
        added = sum(d.approved_shares * d.proposal.entry_reference for d in approved)
        cap = portfolio.equity * limits.max_gross_exposure_pct
        if held <= cap:
            assert held + added <= cap + 1e-6
        assert len(portfolio.positions) + len(approved) <= max(
            limits.max_positions, len(portfolio.positions)
        )
        assert sum(d.approved_shares * d.proposal.entry_reference for d in approved) <= (
            portfolio.cash + 1e-6
        )
        for d in decisions:
            assert 0 <= d.approved_shares <= max(d.proposal.proposed_shares, 0)
            assert d.approved or d.approved_shares == 0
