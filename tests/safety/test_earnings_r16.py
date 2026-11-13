"""R16 with the cross-checked earnings calendar (#20; spec earnings-calendar.md §4, §7).

The risk manager is real; the calendar resolves real ``SourceSnapshot``s built in memory.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from semiedge.clock import FrozenClock
from semiedge.config.models import RiskLimits
from semiedge.data.calendar import TradingCalendar
from semiedge.data.earnings import (
    ALPHAVANTAGE,
    FINNHUB,
    EarningsEvent,
    EarningsResolution,
    ResolutionPolicy,
    SourceSnapshot,
    resolve_earnings,
)
from semiedge.data.earnings_sources import EarningsProvider, EarningsProviderSettings
from semiedge.risk import RiskManager
from semiedge.risk.earnings import CrossCheckedEarningsCalendar
from semiedge.risk.types import EntryProposal, PortfolioView
from semiedge.safety.modes import TradingMode

pytestmark = pytest.mark.safety

SESSION = date(2024, 6, 28)  # Friday; Jul 4 is a holiday
NOW = datetime(2024, 6, 28, 21, 0, tzinfo=UTC)
POLICY = ResolutionPolicy(timedelta(hours=30), timedelta(days=21), timedelta(days=14))


def _snap(source: str, events: tuple[EarningsEvent, ...], **changes: object) -> SourceSnapshot:
    base: dict[str, object] = {
        "source": source,
        "fetched_at": NOW - timedelta(hours=2),
        "coverage_start": SESSION,
        "coverage_end": SESSION + timedelta(days=60),
        "symbols": frozenset({"AAA"}) if source == FINNHUB else None,
        "events": events,
        "status": "ok",
        "error": None,
    }
    base.update(changes)
    return SourceSnapshot(**base)  # type: ignore[arg-type]


def _calendar(
    fh: tuple[EarningsEvent, ...], av: tuple[EarningsEvent, ...], **fh_changes: object
) -> CrossCheckedEarningsCalendar:
    finnhub = _snap(FINNHUB, fh, **fh_changes)
    alphavantage = _snap(ALPHAVANTAGE, av)

    def resolve(symbol: str, on_or_after: date) -> EarningsResolution:
        return resolve_earnings(
            symbol,
            on_or_after,
            finnhub=finnhub,
            alphavantage=alphavantage,
            overrides={},
            now=NOW,
            policy=POLICY,
        )

    return CrossCheckedEarningsCalendar(resolve)


def _decide(calendar: CrossCheckedEarningsCalendar):  # type: ignore[no-untyped-def]
    manager = RiskManager(
        RiskLimits(),
        calendar=TradingCalendar(),
        earnings=calendar,
        tradable_symbols=frozenset({"AAA"}),
    )
    proposal = EntryProposal(
        symbol="AAA",
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
    portfolio = PortfolioView(
        mode=TradingMode.PAPER,
        as_of=NOW,
        session=SESSION,
        equity=100_000.0,
        start_of_day_equity=100_000.0,
        peak_equity=100_000.0,
        cash=100_000.0,
        positions=(),
        pending_orders=(),
        halts=(),
    )
    [decision] = manager.evaluate_entries([proposal], portfolio)
    return decision


def _ev(day: date) -> tuple[EarningsEvent, ...]:
    return (EarningsEvent("AAA", day, None),)


@pytest.mark.parametrize(
    ("report", "approved"),
    [
        (date(2024, 7, 3), False),  # k = 3 = blackout
        (date(2024, 7, 5), True),  # k = 4 (Jul 4 holiday skipped)
    ],
)
def test_agreeing_sources_apply_the_blackout(report: date, approved: bool) -> None:
    decision = _decide(_calendar(_ev(report), _ev(report)))
    assert decision.approved is approved
    if not approved:
        assert "earnings_blackout" in decision.reasons


def test_agreeing_sources_with_no_report_pass() -> None:
    assert _decide(_calendar((), ())).approved is True


def test_disagreement_rejects_as_unavailable(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    calendar = _calendar(_ev(date(2024, 7, 25)), _ev(date(2024, 7, 26)))
    decision = _decide(calendar)
    assert decision.approved is False
    assert "earnings_data_unavailable" in decision.reasons
    assert "sources_disagree" in caplog.text
    _decide(calendar)  # same instance: the warning is logged once per (symbol, reason)
    assert caplog.text.count("R16 blocks new entries") == 1


@pytest.mark.parametrize(
    "fh_changes",
    [
        {"fetched_at": NOW - timedelta(hours=31)},
        {"status": "error", "error": "rate_limited"},
        {"coverage_end": SESSION + timedelta(days=10)},
        {"symbols": frozenset({"BBB"}), "events": ()},
    ],
)
def test_unusable_primary_rejects_even_when_dates_agree(fh_changes: dict[str, object]) -> None:
    report = date(2024, 8, 20)
    changes = dict(fh_changes)
    events = changes.pop("events", _ev(report))
    decision = _decide(_calendar(events, _ev(report), **changes))  # type: ignore[arg-type]
    assert decision.approved is False
    assert "earnings_data_unavailable" in decision.reasons


def test_resolver_exception_fails_closed() -> None:
    def broken(symbol: str, on_or_after: date) -> EarningsResolution:
        raise RuntimeError("boom")

    calendar = CrossCheckedEarningsCalendar(broken)
    info = calendar.next_earnings("AAA", SESSION)
    assert (info.verified, info.next_earnings_session) == (False, None)
    assert "resolver_error_runtimeerror" in info.source
    assert "earnings_data_unavailable" in _decide(calendar).reasons


def test_resolver_returning_another_symbol_fails_closed() -> None:
    good = EarningsResolution("BBB", None, True, "finnhub+alphavantage", "verified", "")
    calendar = CrossCheckedEarningsCalendar(lambda s, d: good)
    assert calendar.next_earnings("AAA", SESSION).verified is False
    assert calendar.next_earnings("AAA", SESSION).source == "none (resolver_invalid_result)"


def test_unverified_result_never_carries_a_date() -> None:
    sneaky = EarningsResolution("AAA", date(2024, 9, 1), False, "x", "sources_disagree", "")
    info = CrossCheckedEarningsCalendar(lambda s, d: sneaky).next_earnings("AAA", SESSION)
    assert (info.verified, info.next_earnings_session) == (False, None)
    truthy = replace(sneaky, verified=1)  # type: ignore[arg-type]
    assert (
        CrossCheckedEarningsCalendar(lambda s, d: truthy).next_earnings("AAA", SESSION).verified
        is False
    )


# ------------------------------------------------------------------ F-167: cache -> risk manager


def _cache_provider(tmp_path: Path, events: object) -> EarningsProvider:
    """Both caches fresh and healthy except ``events``, loaded by the real provider."""
    settings = EarningsProviderSettings(
        lookahead_days=60,
        min_forward_coverage_days=21,
        max_age_hours=30,
        max_attempts_per_day=3,
        retry_interval_minutes=60,
        request_timeout_seconds=5,
        finnhub_min_interval_seconds=1.1,
        override_max_age_days=14,
    )
    provider = EarningsProvider(
        settings,
        cache_dir=tmp_path / "earnings",
        override_path=tmp_path / "earnings_overrides.yaml",
        universe=("AAA",),
        finnhub_api_key=None,
        alphavantage_api_key=None,
        clock=FrozenClock(NOW),
    )
    for source, symbols in ((FINNHUB, ["AAA"]), (ALPHAVANTAGE, None)):
        path = provider.cache_path(source)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = {
            "schema_version": 1,
            "source": source,
            "fetched_at": (NOW - timedelta(hours=2)).isoformat(),
            "coverage_start": SESSION.isoformat(),
            "coverage_end": (SESSION + timedelta(days=60)).isoformat(),
            "symbols": symbols,
            "events": events,
            "status": "ok",
            "error": None,
            "attempts": {},
        }
        path.write_text(json.dumps(raw), encoding="utf-8")
    return provider


@pytest.mark.parametrize("events", [{}, "", None, 0])
def test_malformed_cache_events_reject_the_entry(tmp_path: Path, events: object) -> None:
    calendar = CrossCheckedEarningsCalendar(_cache_provider(tmp_path, events).resolver())
    decision = _decide(calendar)
    assert decision.approved is False
    assert "earnings_data_unavailable" in decision.reasons


def test_well_formed_empty_cache_still_approves(tmp_path: Path) -> None:
    """Control: the same files with ``events: []`` are a verified 'no report' and pass R16."""
    calendar = CrossCheckedEarningsCalendar(_cache_provider(tmp_path, []).resolver())
    assert _decide(calendar).approved is True
