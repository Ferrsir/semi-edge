"""Refresh policy, cache and secret handling of the earnings provider (#20; spec §2, §3, §7).

No test performs a network request: every provider gets a fake ``http_get`` that serves saved
fixture bodies, and a guard fails the test if the default urllib transport is ever reached.
"""

from __future__ import annotations

import email.message
import io
import json
import logging
import socket
import urllib.request
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from semiedge.clock import FrozenClock
from semiedge.data import earnings_sources
from semiedge.data.earnings import ALPHAVANTAGE, FINNHUB, EarningsDataError
from semiedge.data.earnings_sources import (
    EarningsProvider,
    EarningsProviderSettings,
    load_snapshot,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "earnings"
START = datetime(2026, 11, 13, 22, 0, tzinfo=UTC)
FH_KEY = "q" * 31  # low-entropy placeholder, never a real key
AV_KEY = "z" * 31  # low-entropy placeholder, never a real key
SETTINGS = EarningsProviderSettings(
    lookahead_days=60,
    min_forward_coverage_days=21,
    max_age_hours=30,
    max_attempts_per_day=3,
    retry_interval_minutes=60,
    request_timeout_seconds=5,
    finnhub_min_interval_seconds=1.1,
    override_max_age_days=14,
)


@pytest.fixture(autouse=True)
def _no_real_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a test reached the real network transport")

    monkeypatch.setattr(earnings_sources.urllib.request, "urlopen", refuse)

    class _RefusingOpener:
        open = staticmethod(refuse)

    monkeypatch.setattr(earnings_sources, "_OPENER", _RefusingOpener())


class FakeHttp:
    """Serves fixture bodies by host/symbol and records every call."""

    def __init__(self, responses: Mapping[str, tuple[int, bytes] | Exception] | None = None):
        default = {
            "NVDA": (200, (FIXTURES / "finnhub_nvda.json").read_bytes()),
            "AMD": (200, (FIXTURES / "finnhub_amd.json").read_bytes()),
            "av": (200, (FIXTURES / "alphavantage_calendar.csv").read_bytes()),
        }
        self.responses: dict[str, tuple[int, bytes] | Exception] = {**default, **(responses or {})}
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
        self.calls.append((url, dict(headers), timeout))
        if url.startswith("https://www.alphavantage.co/query?"):
            key = "av"
        else:
            assert url.startswith("https://finnhub.io/api/v1/calendar/earnings?")
            key = url.rsplit("symbol=", 1)[1]
        response = self.responses[key]
        if isinstance(response, Exception):
            raise response
        return response

    def hosts(self) -> list[str]:
        return ["av" if "alphavantage" in u else "fh" for u, _, _ in self.calls]


def _provider(
    tmp_path: Path,
    http: FakeHttp,
    clock: FrozenClock,
    *,
    fh_key: str | None = FH_KEY,
    av_key: str | None = AV_KEY,
    overrides: str | None = None,
) -> EarningsProvider:
    override_path = tmp_path / "earnings_overrides.yaml"
    if overrides is not None:
        override_path.write_text(overrides, encoding="utf-8")
    return EarningsProvider(
        SETTINGS,
        cache_dir=tmp_path / "earnings",
        override_path=override_path,
        universe=("NVDA", "AMD"),
        finnhub_api_key=fh_key,
        alphavantage_api_key=av_key,
        clock=clock,
        http_get=http,
        sleep=lambda _: None,
    )


def _all_text(tmp_path: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json"))


# ------------------------------------------------------------------ happy path


def test_refresh_fetches_both_and_resolution_verifies(tmp_path: Path) -> None:
    http, clock = FakeHttp(), FrozenClock(START)
    provider = _provider(tmp_path, http, clock)
    outcomes = provider.refresh()
    assert [(o.source, o.action, o.status) for o in outcomes] == [
        (FINNHUB, "fetched", "ok"),
        (ALPHAVANTAGE, "fetched", "ok"),
    ]
    assert http.hosts() == ["fh", "fh", "av"]  # one request per symbol + one bulk request
    resolve = provider.resolver()
    nvda = resolve("NVDA", START.date())
    assert (nvda.verified, nvda.next_date) == (True, date(2026, 11, 25))
    amd = resolve("AMD", START.date())
    assert (amd.verified, amd.next_date) == (True, date(2027, 1, 5))


def test_alphavantage_snapshot_keeps_only_universe_symbols(tmp_path: Path) -> None:
    provider = _provider(tmp_path, FakeHttp(), FrozenClock(START))
    provider.refresh()
    snap, _ = load_snapshot(provider.cache_path(ALPHAVANTAGE), ALPHAVANTAGE)
    assert snap is not None
    assert {e.symbol for e in snap.events} == {"NVDA", "AMD"}


def test_finnhub_requests_are_spaced(tmp_path: Path) -> None:
    slept: list[float] = []
    provider = _provider(tmp_path, FakeHttp(), FrozenClock(START))
    provider._sleep = slept.append
    provider.refresh()
    assert slept == [1.1]  # between the two Finnhub requests, not before the first


# ------------------------------------------------------------------ secrets


def test_keys_never_reach_urls_cache_or_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    http = FakeHttp()
    provider = _provider(tmp_path, http, FrozenClock(START))
    provider.refresh()
    finnhub_calls = [c for c in http.calls if "finnhub" in c[0]]
    assert all(
        FH_KEY not in url and headers == {"X-Finnhub-Token": FH_KEY}
        for url, headers, _ in finnhub_calls
    )
    text = _all_text(tmp_path) + caplog.text + repr(provider)
    assert FH_KEY not in text
    assert AV_KEY not in text


def test_transport_error_text_with_the_key_is_discarded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    leaky = OSError(f"connection reset for https://www.alphavantage.co/query?apikey={AV_KEY}")
    provider = _provider(tmp_path, FakeHttp({"av": leaky}), FrozenClock(START))
    outcomes = provider.refresh()
    assert outcomes[1].error == "transport_oserror"
    assert AV_KEY not in _all_text(tmp_path) + caplog.text + repr(outcomes)


def test_missing_keys_make_no_request(tmp_path: Path) -> None:
    http = FakeHttp()
    provider = _provider(tmp_path, http, FrozenClock(START), fh_key=None, av_key="")
    outcomes = provider.refresh(force=True)
    assert [(o.action, o.error) for o in outcomes] == [("missing_api_key", "missing_api_key")] * 2
    assert http.calls == []
    r = provider.resolver()("NVDA", START.date())
    assert (r.verified, r.reason) == (False, "finnhub_missing+alphavantage_missing")


# ------------------------------------------------------------------ refresh policy


def test_second_refresh_same_day_makes_no_request(tmp_path: Path) -> None:
    http, clock = FakeHttp(), FrozenClock(START)
    provider = _provider(tmp_path, http, clock)
    provider.refresh()
    clock.advance(timedelta(hours=1))
    outcomes = provider.refresh()
    assert [o.action for o in outcomes] == ["skipped_fresh", "skipped_fresh"]
    assert len(http.calls) == 3
    clock.advance(timedelta(hours=2))  # next UTC day
    assert [o.action for o in provider.refresh()] == ["fetched", "fetched"]
    assert len(http.calls) == 6


def test_force_refetches_on_the_same_day(tmp_path: Path) -> None:
    http = FakeHttp()
    provider = _provider(tmp_path, http, FrozenClock(START))
    provider.refresh()
    assert [o.action for o in provider.refresh(force=True)] == ["fetched", "fetched"]


def test_universe_change_triggers_a_finnhub_refetch(tmp_path: Path) -> None:
    http, clock = FakeHttp(), FrozenClock(START)
    _provider(tmp_path, http, clock).refresh()
    wider = EarningsProvider(
        SETTINGS,
        cache_dir=tmp_path / "earnings",
        override_path=tmp_path / "none.yaml",
        universe=("NVDA",),
        finnhub_api_key=FH_KEY,
        alphavantage_api_key=AV_KEY,
        clock=clock,
        http_get=http,
        sleep=lambda _: None,
    )
    assert [o.action for o in wider.refresh()] == ["fetched", "skipped_fresh"]


def test_failures_honour_retry_interval_and_daily_cap(tmp_path: Path) -> None:
    http, clock = FakeHttp({"av": (429, b"")}), FrozenClock(START.replace(hour=1))
    provider = _provider(tmp_path, http, clock)
    first = provider.refresh()[1]
    assert (first.action, first.error) == ("failed", "rate_limited")
    clock.advance(timedelta(minutes=30))
    assert provider.refresh()[1].action == "skipped_retry_wait"
    clock.advance(timedelta(minutes=30))
    assert provider.refresh()[1].action == "failed"
    clock.advance(timedelta(minutes=60))
    assert provider.refresh()[1].action == "failed"
    clock.advance(timedelta(minutes=60))
    assert provider.refresh()[1].action == "skipped_attempt_limit"  # 3 attempts used today
    assert sum(1 for h in http.hosts() if h == "av") == 3


def test_failure_keeps_error_status_and_blocks_until_success(tmp_path: Path) -> None:
    http, clock = FakeHttp(), FrozenClock(START)
    provider = _provider(tmp_path, http, clock)
    provider.refresh()
    clock.advance(timedelta(hours=3))  # next day
    http.responses["NVDA"] = (500, b"")
    outcomes = provider.refresh()
    assert (outcomes[0].action, outcomes[0].error) == ("failed", "http_500")
    r = provider.resolver()("NVDA", clock.now().date())
    assert (r.verified, r.reason) == (False, "finnhub_error_http_500")


@pytest.mark.parametrize(
    ("response", "code"),
    [
        ((401, b""), "http_401"),
        (TimeoutError("timed out"), "transport_timeouterror"),
    ],
)
def test_finnhub_failure_codes(tmp_path: Path, response: object, code: str) -> None:
    provider = _provider(tmp_path, FakeHttp({"NVDA": response}), FrozenClock(START))  # type: ignore[dict-item]
    assert provider.refresh()[0].error == code


def test_alphavantage_rate_limit_notice(tmp_path: Path) -> None:
    body = (FIXTURES / "alphavantage_rate_limit.json").read_bytes()
    provider = _provider(tmp_path, FakeHttp({"av": (200, body)}), FrozenClock(START))
    assert provider.refresh()[1].error == "api_message"


# ------------------------------------------------------------------ cache


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "{}",
        json.dumps({"schema_version": 2}),
        json.dumps({"schema_version": 1, "source": "finnhub"}),
    ],
)
def test_corrupt_cache_counts_as_missing(tmp_path: Path, content: str) -> None:
    provider = _provider(tmp_path, FakeHttp(), FrozenClock(START))
    path = provider.cache_path(ALPHAVANTAGE)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    assert load_snapshot(path, ALPHAVANTAGE) == (None, {})


def test_cache_from_the_other_source_is_rejected(tmp_path: Path) -> None:
    provider = _provider(tmp_path, FakeHttp(), FrozenClock(START))
    provider.refresh()
    swapped = provider.cache_path(ALPHAVANTAGE)
    swapped.write_text(provider.cache_path(FINNHUB).read_text(encoding="utf-8"), encoding="utf-8")
    assert load_snapshot(swapped, ALPHAVANTAGE)[0] is None


def test_stale_cache_blocks_after_max_age(tmp_path: Path) -> None:
    clock = FrozenClock(START)
    provider = _provider(tmp_path, FakeHttp(), clock)
    provider.refresh()
    clock.advance(timedelta(hours=31))
    r = provider.resolver()("NVDA", clock.now().date())
    assert (r.verified, r.reason) == (False, "finnhub_stale+alphavantage_stale")


def test_invalid_override_file_fails_closed(tmp_path: Path) -> None:
    provider = _provider(tmp_path, FakeHttp(), FrozenClock(START), overrides="overrides: {}\n")
    with pytest.raises(EarningsDataError):
        provider.resolver()


def test_override_resolves_a_disagreement(tmp_path: Path) -> None:
    http = FakeHttp({"NVDA": (200, (FIXTURES / "finnhub_empty.json").read_bytes())})
    overrides = (
        "overrides:\n  - symbol: NVDA\n    next_earnings_date: 2026-11-25\n"
        "    verified_on: 2026-11-13\n    source_url: https://investor.example.com/events\n"
    )
    provider = _provider(tmp_path, http, FrozenClock(START), overrides=overrides)
    provider.refresh()
    r = provider.resolver()("NVDA", START.date())
    assert (r.verified, r.next_date, r.source) == (
        True,
        date(2026, 11, 25),
        "manual_override@2026-11-13",
    )
    no_override = _provider(tmp_path / "b", http, FrozenClock(START))
    no_override.refresh(force=True)
    assert no_override.resolver()("NVDA", START.date()).reason == "sources_disagree"


def test_empty_universe_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        EarningsProvider(
            SETTINGS,
            cache_dir=tmp_path,
            override_path=tmp_path / "o.yaml",
            universe=(),
            finnhub_api_key=None,
            alphavantage_api_key=None,
            clock=FrozenClock(START),
        )


def test_default_transport_refuses_other_urls() -> None:
    with pytest.raises(ValueError, match="unexpected URL"):
        earnings_sources.urllib_get("https://example.com/?x=1", {}, 1.0)


def test_network_guard_covers_the_default_transport() -> None:
    """The autouse guard must stop the real opener, or 'no network' tests would not mean much."""
    with pytest.raises(AssertionError, match="real network transport"):
        earnings_sources.urllib_get("https://finnhub.io/api/v1/calendar/earnings?x=1", {}, 1.0)


# ------------------------------------------------------------------ F-166: redirects


class _FakeVendorHTTPS(urllib.request.HTTPSHandler):
    """Answers in-process through a real urllib opener; records (host, token) for every request."""

    def __init__(self, status: int, location: str | None) -> None:
        super().__init__()
        self.status = status
        self.location = location
        self.seen: list[tuple[str, str | None]] = []

    def https_open(self, req: urllib.request.Request) -> urllib.request.addinfourl:  # type: ignore[override]
        self.seen.append((req.host, req.get_header("X-finnhub-token")))
        headers = email.message.Message()
        if self.location is not None and not self.seen[1:]:
            headers["Location"] = self.location
            status = self.status
        else:
            status = 200
        response = urllib.request.addinfourl(io.BytesIO(b"[]"), headers, req.full_url, status)
        response.msg = "fake"
        return response

    http_open = https_open  # catch an https -> http downgrade too


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize(
    "location",
    [
        "https://untrusted.invalid/collect",
        "http://finnhub.io/api/v1/calendar/earnings?from=x",  # downgrade, same host
        "https://finnhub.io/api/v1/other",  # same origin, other path
    ],
)
def test_default_transport_never_follows_a_redirect(
    monkeypatch: pytest.MonkeyPatch, status: int, location: str
) -> None:
    def no_sockets(*_: object, **__: object) -> None:
        raise AssertionError("a socket was opened")

    monkeypatch.setattr(socket, "socket", no_sockets)
    vendor = _FakeVendorHTTPS(status, location)
    monkeypatch.setattr(earnings_sources, "_OPENER", earnings_sources._build_opener(vendor))
    url = "https://finnhub.io/api/v1/calendar/earnings?from=2026-11-13&symbol=NVDA"
    result = earnings_sources.urllib_get(url, {"X-Finnhub-Token": FH_KEY}, 1.0)
    assert result == (status, b"")
    assert vendor.seen == [("finnhub.io", FH_KEY)]  # one request; the token went nowhere else


def test_default_transport_still_returns_a_direct_200(monkeypatch: pytest.MonkeyPatch) -> None:
    vendor = _FakeVendorHTTPS(200, None)
    monkeypatch.setattr(earnings_sources, "_OPENER", earnings_sources._build_opener(vendor))
    url = "https://finnhub.io/api/v1/calendar/earnings?symbol=NVDA"
    assert earnings_sources.urllib_get(url, {"X-Finnhub-Token": FH_KEY}, 1.0) == (200, b"[]")


def test_a_refused_redirect_becomes_a_failed_refresh(tmp_path: Path) -> None:
    http = FakeHttp({"NVDA": (302, b"")})
    outcome = _provider(tmp_path, http, FrozenClock(START)).refresh()[0]
    assert (outcome.action, outcome.status, outcome.error) == ("failed", "error", "http_302")


# ------------------------------------------------------------------ F-167: malformed cache


def _fresh_cache(tmp_path: Path) -> tuple[EarningsProvider, dict[str, object], dict[str, object]]:
    provider = _provider(tmp_path, FakeHttp(), FrozenClock(START))
    provider.refresh()
    fh = json.loads(provider.cache_path(FINNHUB).read_text(encoding="utf-8"))
    av = json.loads(provider.cache_path(ALPHAVANTAGE).read_text(encoding="utf-8"))
    return provider, fh, av


_MALFORMED: list[tuple[str, object]] = [
    ("events", {}),
    ("events", ""),
    ("events", None),
    ("events", [{"symbol": "nvda", "report_date": "2026-11-20", "hour": None}]),
    ("events", [{"symbol": "NVDA", "report_date": "2026-11-20", "hour": 5}]),
    ("events", [{"symbol": "NVDA", "report_date": 20261120, "hour": None}]),
    ("events", [{"symbol": "NVDA", "report_date": "2026-11-20"}]),
    ("events", [["NVDA", "2026-11-20", None]]),
    ("symbols", "NVDA"),
    ("symbols", {"NVDA": 1}),
    ("symbols", ["NVDA", 7]),
    ("fetched_at", 1763071200),
    ("coverage_end", None),
    ("status", 1),
    ("error", 0),
    ("attempts", {"date": "2026-11-13", "count": "1", "last_attempt_at": START.isoformat()}),
    ("attempts", {"date": "2026-11-13", "count": True, "last_attempt_at": START.isoformat()}),
    ("attempts", {"date": "2026-11-13", "count": -1, "last_attempt_at": START.isoformat()}),
    ("attempts", {"date": "2026-11-13", "count": 1}),
    ("attempts", {"date": "2026-11-13", "count": 1, "last_attempt_at": "2026-11-13T22:00"}),
    ("attempts", []),
]


@pytest.mark.parametrize(("field", "value"), _MALFORMED)
@pytest.mark.parametrize("source", [FINNHUB, ALPHAVANTAGE])
def test_malformed_cache_field_counts_as_missing(
    tmp_path: Path, source: str, field: str, value: object
) -> None:
    provider, fh, av = _fresh_cache(tmp_path)
    raw = fh if source == FINNHUB else av
    raw[field] = value
    path = provider.cache_path(source)  # type: ignore[arg-type]
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert load_snapshot(path, source) == (None, {})  # type: ignore[arg-type]


def test_both_caches_with_object_events_never_verify_absence(tmp_path: Path) -> None:
    """Codex's F-167 reproduction: ``events: {}`` in both fresh caches is not a 'no report'."""
    provider, fh, av = _fresh_cache(tmp_path)
    for source, raw in ((FINNHUB, fh), (ALPHAVANTAGE, av)):
        raw["events"] = {}
        provider.cache_path(source).write_text(json.dumps(raw), encoding="utf-8")  # type: ignore[arg-type]
    resolution = provider.resolver()("NVDA", START.date())
    assert resolution.verified is False
    assert resolution.reason != "verified"


def test_well_formed_cache_still_loads(tmp_path: Path) -> None:
    provider, _, _ = _fresh_cache(tmp_path)
    for source in (FINNHUB, ALPHAVANTAGE):
        snap, attempts = load_snapshot(provider.cache_path(source), source)
        assert snap is not None
        assert {"date", "count", "last_attempt_at"} <= set(attempts)
        assert set(attempts) <= {"date", "count", "last_attempt_at", "requested"}


# ------------------------------------------------------------------ #114: per-symbol parse failures

_TAIWAN_ROW = b'{"earningsCalendar":[{"symbol":"2330.TW","date":"2027-01-13","hour":"bmo"}]}'


@pytest.mark.parametrize(
    "bad_body",
    [_TAIWAN_ROW, b"not json", b'{"earningsCalendar":[{"symbol":"AMD","date":"2026-11-25"}]}'],
)
def test_one_unparseable_symbol_blocks_only_that_symbol(tmp_path: Path, bad_body: bytes) -> None:
    """Finnhub answers TSM with 2330.TW: that symbol is left uncovered, the rest stay verified."""
    clock = FrozenClock(START)
    provider = _provider(tmp_path, FakeHttp({"NVDA": (200, bad_body)}), clock)
    finnhub, _ = provider.refresh()
    assert (finnhub.action, finnhub.status, finnhub.error) == ("fetched", "ok", None)
    snap = provider.snapshots()[FINNHUB]
    assert snap is not None
    assert snap.symbols == frozenset({"AMD"})
    resolve = provider.resolver()
    nvda = resolve("NVDA", START.date())
    assert (nvda.verified, nvda.reason) == (False, "finnhub_symbol_not_covered")
    assert resolve("AMD", START.date()).verified is True


def test_every_symbol_unparseable_still_fails_the_snapshot(tmp_path: Path) -> None:
    http = FakeHttp({"NVDA": (200, _TAIWAN_ROW), "AMD": (200, b"not json")})
    assert _provider(tmp_path, http, FrozenClock(START)).refresh()[0].error == "parse_error"


def test_an_http_failure_on_one_symbol_still_fails_the_whole_snapshot(tmp_path: Path) -> None:
    http = FakeHttp({"NVDA": (500, b"")})
    assert _provider(tmp_path, http, FrozenClock(START)).refresh()[0].error == "http_500"


def test_a_left_out_symbol_does_not_force_a_refetch_the_same_day(tmp_path: Path) -> None:
    """Without the requested list, 15 of 16 covered looked like a changed universe every refresh."""
    clock = FrozenClock(START)
    http = FakeHttp({"NVDA": (200, _TAIWAN_ROW)})
    provider = _provider(tmp_path, http, clock)
    provider.refresh()
    calls = len(http.calls)
    clock.advance(timedelta(hours=1))
    assert provider.refresh()[0].action == "skipped_fresh"
    assert len(http.calls) == calls  # no new requests


@pytest.mark.parametrize("requested", ["NVDA", ["nvda"], ["NVDA", "AMD"], [1]])
def test_malformed_requested_list_counts_as_missing(tmp_path: Path, requested: object) -> None:
    provider, fh, _ = _fresh_cache(tmp_path)
    fh["attempts"] = {**fh["attempts"], "requested": requested}  # type: ignore[dict-item]
    path = provider.cache_path(FINNHUB)
    path.write_text(json.dumps(fh), encoding="utf-8")
    assert load_snapshot(path, FINNHUB) == (None, {})
