"""Unit tests for the pure earnings module (#20; spec earnings-calendar.md §4, §7)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from semiedge.data.earnings import (
    ALPHAVANTAGE,
    FINNHUB,
    AlphaVantageParseError,
    EarningsDataError,
    EarningsEvent,
    FinnhubParseError,
    ManualOverride,
    ResolutionPolicy,
    SourceSnapshot,
    load_overrides,
    parse_alphavantage_csv,
    parse_finnhub_json,
    resolve_earnings,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "earnings"
NOW = datetime(2026, 11, 13, 22, 0, tzinfo=UTC)
TODAY = NOW.date()
POLICY = ResolutionPolicy(
    max_age=timedelta(hours=30),
    min_forward_coverage=timedelta(days=21),
    override_max_age=timedelta(days=14),
)
NVDA_DATE = date(2026, 11, 25)


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _snap(source: str = FINNHUB, **changes: object) -> SourceSnapshot:
    base: dict[str, object] = {
        "source": source,
        "fetched_at": NOW - timedelta(hours=1),
        "coverage_start": TODAY,
        "coverage_end": TODAY + timedelta(days=60),
        "symbols": frozenset({"NVDA", "AMD"}) if source == FINNHUB else None,
        "events": (EarningsEvent("NVDA", NVDA_DATE, "amc"),),
        "status": "ok",
        "error": None,
    }
    base.update(changes)
    return SourceSnapshot(**base)  # type: ignore[arg-type]


def _resolve(symbol: str = "NVDA", on_or_after: date = TODAY, **kw: object):  # type: ignore[no-untyped-def]
    params: dict[str, object] = {
        "finnhub": _snap(FINNHUB),
        "alphavantage": _snap(ALPHAVANTAGE),
        "overrides": {},
        "now": NOW,
        "policy": POLICY,
    }
    params.update(kw)
    return resolve_earnings(symbol, on_or_after, **params)  # type: ignore[arg-type]


# ------------------------------------------------------------------ parsers


def test_finnhub_fixture_parses() -> None:
    events = parse_finnhub_json(
        _fixture("finnhub_nvda.json"), symbol="NVDA", start=TODAY, end=TODAY + timedelta(days=60)
    )
    assert events == [EarningsEvent("NVDA", NVDA_DATE, "amc")]
    assert (
        parse_finnhub_json(_fixture("finnhub_empty.json"), symbol="NVDA", start=TODAY, end=TODAY)
        == []
    )


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"[]",
        b'{"earningsCalendar": {}}',
        b'{"earningsCalendar": ["x"]}',
        b'{"earningsCalendar": [{"symbol": "AMD", "date": "2026-11-25"}]}',
        b'{"earningsCalendar": [{"symbol": "NVDA", "date": "2027-06-01"}]}',
        b'{"earningsCalendar": [{"symbol": "NVDA", "date": "2026-02-30"}]}',
        b'{"earningsCalendar": [{"symbol": "NVDA", "date": "25/11/2026"}]}',
        b'{"earningsCalendar": [{"symbol": "NVDA"}]}',
    ],
)
def test_finnhub_rejects_malformed_responses(body: bytes) -> None:
    with pytest.raises(FinnhubParseError):
        parse_finnhub_json(body, symbol="NVDA", start=TODAY, end=TODAY + timedelta(days=60))


def test_alphavantage_fixture_parses() -> None:
    events = parse_alphavantage_csv(_fixture("alphavantage_calendar.csv"))
    assert [(e.symbol, e.report_date) for e in events] == [
        ("NVDA", NVDA_DATE),
        ("AMD", date(2027, 1, 5)),
        ("ZZZZ.TO", date(2026, 12, 1)),  # harmless: the provider keeps only universe symbols
        ("MSFT", date(2027, 1, 28)),
    ]
    crlf = b"\xef\xbb\xbfsymbol,name,reportDate\r\nNVDA,N,2026-11-25\r\n,blank,2026-11-25\r\n"
    assert parse_alphavantage_csv(crlf) == [EarningsEvent("NVDA", NVDA_DATE, None)]


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"", "parse_error"),
        (b"   \n", "parse_error"),
        (b"\xff\xfe", "parse_error"),
        (b"symbol,name\nNVDA,N\n", "parse_error"),
        (b"symbol,name,reportDate\nNVDA,N,soon\n", "parse_error"),
    ],
)
def test_alphavantage_rejects_malformed_csv(body: bytes, code: str) -> None:
    with pytest.raises(AlphaVantageParseError) as info:
        parse_alphavantage_csv(body)
    assert info.value.code == code


def test_alphavantage_rate_limit_notice_is_an_api_message() -> None:
    with pytest.raises(AlphaVantageParseError) as info:
        parse_alphavantage_csv(_fixture("alphavantage_rate_limit.json"))
    assert info.value.code == "api_message"
    assert "XXXXXXXX" not in str(info.value)  # the notice text is never carried


# ------------------------------------------------------------------ snapshot invariants


@pytest.mark.parametrize(
    "changes",
    [
        {"fetched_at": datetime(2026, 11, 13, 12, 0)},  # noqa: DTZ001 - naive on purpose
        {"coverage_end": TODAY - timedelta(days=1)},
        {"status": "error"},  # error without code
        {"error": "http_500"},  # code without error status
        {"status": "error", "error": "Has Spaces And Caps"},
        {"events": (EarningsEvent("MSFT", NVDA_DATE, None),)},  # outside covered symbols
    ],
)
def test_snapshot_rejects_inconsistent_state(changes: dict[str, object]) -> None:
    with pytest.raises(EarningsDataError):
        _snap(FINNHUB, **changes)


# ------------------------------------------------------------------ resolution


def test_agreeing_sources_are_verified() -> None:
    r = _resolve()
    assert (r.verified, r.next_date, r.source, r.reason) == (
        True,
        NVDA_DATE,
        "finnhub+alphavantage",
        "verified",
    )


def test_both_sources_without_a_report_are_verified_none() -> None:
    r = _resolve("AMD")
    assert (r.verified, r.next_date) == (True, None)


def test_past_events_are_ignored() -> None:
    r = _resolve(on_or_after=NVDA_DATE + timedelta(days=1))
    assert (r.verified, r.next_date) == (True, None)


@pytest.mark.parametrize(
    ("finnhub_events", "av_events", "detail"),
    [
        ((EarningsEvent("NVDA", NVDA_DATE, None),), (), "alphavantage=none"),
        ((), (EarningsEvent("NVDA", NVDA_DATE, None),), "finnhub=none"),
        (
            (EarningsEvent("NVDA", NVDA_DATE, None),),
            (EarningsEvent("NVDA", NVDA_DATE + timedelta(days=1), None),),
            "alphavantage=2026-11-26",
        ),
    ],
)
def test_disagreement_blocks_the_symbol(
    finnhub_events: tuple[EarningsEvent, ...], av_events: tuple[EarningsEvent, ...], detail: str
) -> None:
    r = _resolve(
        finnhub=_snap(FINNHUB, events=finnhub_events),
        alphavantage=_snap(ALPHAVANTAGE, events=av_events),
    )
    assert (r.verified, r.next_date, r.reason) == (False, None, "sources_disagree")
    assert detail in r.detail


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"finnhub": None}, "finnhub_missing"),
        ({"alphavantage": None}, "alphavantage_missing"),
        ({"finnhub": _snap(ALPHAVANTAGE)}, "finnhub_missing"),  # wrong source in the slot
        ({"finnhub": _snap(FINNHUB, status="error", error="http_401")}, "finnhub_error_http_401"),
        (
            {"alphavantage": _snap(ALPHAVANTAGE, status="error", error="api_message")},
            "alphavantage_error_api_message",
        ),
        ({"finnhub": _snap(FINNHUB, fetched_at=NOW - timedelta(hours=31))}, "finnhub_stale"),
        ({"finnhub": _snap(FINNHUB, fetched_at=NOW + timedelta(minutes=6))}, "finnhub_clock_skew"),
        (
            {"alphavantage": _snap(ALPHAVANTAGE, coverage_end=TODAY + timedelta(days=20))},
            "alphavantage_coverage",
        ),
        (
            {"finnhub": _snap(FINNHUB, coverage_start=TODAY + timedelta(days=1))},
            "finnhub_coverage",
        ),
        (
            {"finnhub": _snap(FINNHUB, symbols=frozenset({"AMD"}), events=())},
            "finnhub_symbol_not_covered",
        ),
    ],
)
def test_unusable_source_blocks(kw: dict[str, object], reason: str) -> None:
    r = _resolve(**kw)
    assert (r.verified, r.next_date) == (False, None)
    assert r.reason == reason


def test_both_sources_unusable_lists_both_reasons() -> None:
    r = _resolve(finnhub=None, alphavantage=None)
    assert r.reason == "finnhub_missing+alphavantage_missing"


def test_stale_boundary_is_inclusive_of_max_age() -> None:
    assert _resolve(finnhub=_snap(FINNHUB, fetched_at=NOW - timedelta(hours=30))).verified


def test_naive_now_raises() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _resolve(now=datetime(2026, 11, 13))  # noqa: DTZ001 - naive on purpose


def _override(**changes: object) -> ManualOverride:
    base: dict[str, object] = {
        "symbol": "NVDA",
        "next_earnings_date": date(2026, 11, 26),
        "verified_on": TODAY,
        "source_url": "https://investor.example.com/events",
        "note": None,
    }
    base.update(changes)
    return ManualOverride(**base)  # type: ignore[arg-type]


def test_fresh_override_wins_even_over_a_broken_source() -> None:
    r = _resolve(overrides={"NVDA": _override()}, finnhub=None)
    assert (r.verified, r.next_date, r.source) == (
        True,
        date(2026, 11, 26),
        f"manual_override@{TODAY}",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"verified_on": TODAY - timedelta(days=15)},  # older than override_max_age
        {"next_earnings_date": TODAY - timedelta(days=1)},  # already passed
        {"verified_on": TODAY + timedelta(days=1)},  # future check date
    ],
)
def test_expired_override_falls_back_to_the_sources(changes: dict[str, object]) -> None:
    r = _resolve(overrides={"NVDA": _override(**changes)})
    assert (r.verified, r.next_date, r.source) == (True, NVDA_DATE, "finnhub+alphavantage")
    blocked = _resolve(overrides={"NVDA": _override(**changes)}, finnhub=None)
    assert blocked.reason == "finnhub_missing+override_expired"


# ------------------------------------------------------------------ override file


UNIVERSE = ("NVDA", "AMD")


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "earnings_overrides.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_override_file_means_no_overrides(tmp_path: Path) -> None:
    assert load_overrides(tmp_path / "absent.yaml", universe=UNIVERSE, today=TODAY) == {}


def test_shipped_override_file_is_empty_and_valid() -> None:
    shipped = Path(__file__).resolve().parents[3] / "config" / "earnings_overrides.yaml"
    assert load_overrides(shipped, universe=UNIVERSE, today=TODAY) == {}


def test_valid_override_file_loads(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "overrides:\n"
        "  - symbol: NVDA\n"
        "    next_earnings_date: 2026-11-26\n"
        "    verified_on: 2026-11-13\n"
        "    source_url: https://investor.example.com/events\n"
        "    note: checked by hand\n",
    )
    assert load_overrides(path, universe=UNIVERSE, today=TODAY) == {
        "NVDA": _override(note="checked by hand")
    }


_ENTRY = (
    "  - symbol: NVDA\n"
    "    next_earnings_date: 2026-11-26\n"
    "    verified_on: 2026-11-13\n"
    "    source_url: https://investor.example.com/events\n"
)


@pytest.mark.parametrize(
    "text",
    [
        "overrides: {}\n",
        "other: []\n",
        "overrides: []\nextra: 1\n",
        "- 1\n",
        "overrides:\n" + _ENTRY + _ENTRY,  # duplicate symbol
        "overrides:\n" + _ENTRY.replace("NVDA", "MSFT"),  # outside the universe
        "overrides:\n" + _ENTRY.replace("2026-11-13", "2026-11-14"),  # verified in the future
        "overrides:\n" + _ENTRY.replace("https://", "http://"),
        "overrides:\n"
        + _ENTRY.replace("    source_url: https://investor.example.com/events\n", ""),
        "overrides:\n" + _ENTRY + "    confirmed: true\n",  # unknown field
        "overrides:\n" + _ENTRY.replace("2026-11-26", "next week"),
        "overrides:\n" + _ENTRY.replace("2026-11-26", "2026-11-26T10:00:00"),
        "overrides:\n" + _ENTRY + "    note: 5\n",
        "overrides:\n  - symbol: NVDA\n    symbol: AMD\n",  # duplicate key
        "overrides: [\n",  # invalid YAML
    ],
)
def test_invalid_override_file_raises(tmp_path: Path, text: str) -> None:
    with pytest.raises(EarningsDataError):
        load_overrides(_write(tmp_path, text), universe=UNIVERSE, today=TODAY)
