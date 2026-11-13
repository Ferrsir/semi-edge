"""Hand-written action fixtures and fake read transport only; no external market observations."""

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from fractions import Fraction

import pandas as pd
import pytest
from alpaca.common.enums import BaseURL

from semiedge.data.alpaca_source import MarketDataError
from semiedge.data.calendar import TradingCalendar
from semiedge.data.corporate_actions import (
    AlpacaCorporateActionSource,
    CorporateAction,
    CorporateActionStore,
    check_adjustment_consistency,
)
from semiedge.data.corporate_actions import (
    CorporateActionKind as Kind,
)
from semiedge.data.schema import Adjustment, DataFeed, DataSource
from semiedge.data.splits import normalize_raw_splits
from semiedge.data.store import ParquetBarStore, StoredSeries

pytestmark = pytest.mark.safety

FETCHED = datetime(2024, 1, 5, 22, tzinfo=UTC)
KNOWN = datetime(2024, 1, 2, 12, tzinfo=UTC)
DAY = date(2024, 1, 3)


def action(**changes):
    values = {
        "event_id": "event-1",
        "symbol": "AAA",
        "kind": Kind.SPLIT,
        "ex_date": DAY,
        "new_shares": 4,
        "old_shares": 1,
        "cash_amount": None,
        "source": DataSource.ALPACA,
        "fetched_at": FETCHED,
        "known_at": KNOWN,
    }
    return CorporateAction(**(values | changes))


def raw(**changes):
    return {
        "id": "event-1",
        "symbol": "AAA",
        "ex_date": DAY.isoformat(),
        "new_rate": 4.0,
        "old_rate": 1.0,
    } | changes


def page(rows=None, token=None):
    return {
        "corporate_actions": {"forward_splits": [raw()] if rows is None else rows},
        "next_page_token": token,
    }


class Client:
    _base_url = BaseURL.DATA

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, path, data):
        self.calls.append((path, data))
        if not self.pages:
            raise RuntimeError("FAKE_SECRET_SENTINEL")
        return self.pages.pop(0)


def source(client, knowledge=lambda _: KNOWN, now=lambda: FETCHED):
    return AlpacaCorporateActionSource(client, knowledge=knowledge, now=now)


def test_exact_model_mapping_and_frozen_fields():
    event = action(kind=Kind.REVERSE_SPLIT, new_shares=1, old_shares=3)
    assert event.ratio == Fraction(1, 3)
    assert event.to_split_event().new_shares == 1
    assert event.to_split_event().old_shares == 3
    assert event.to_split_event().known_at == KNOWN
    with pytest.raises(FrozenInstanceError):
        event.symbol = "BBB"
    dividend = action(kind=Kind.CASH_DIVIDEND, new_shares=None, old_shares=None, cash_amount=2.5)
    assert dividend.ratio is None
    with pytest.raises(ValueError, match="exact split"):
        dividend.to_split_event()


@pytest.mark.parametrize(
    "changes",
    [
        {"event_id": ""},
        {"symbol": "../bad"},
        {"kind": "split"},
        {"source": "alpaca"},
        {"ex_date": FETCHED},
        {"new_shares": True},
        {"new_shares": 4.0},
        {"new_shares": 2**53 + 1},
        {"new_shares": 0},
        {"old_shares": -1},
        {"new_shares": 1},
        {"kind": Kind.REVERSE_SPLIT},
        {"cash_amount": 1.0},
        {"fetched_at": FETCHED.replace(tzinfo=None)},
        {"known_at": None},
        {"known_at": datetime(2024, 1, 6, tzinfo=UTC)},
        {"source": DataSource.FIXTURE},
    ],
)
def test_invalid_model_terms_fail_explicitly(changes):
    with pytest.raises(
        ValueError,
        match=(
            r"corporate-action|split|share counts|event knowledge|timestamps|event ids|"
            r"cash_amount"
        ),
    ):
        action(**changes)


@pytest.mark.parametrize("amount", [None, True, 0, -1, float("nan"), float("inf"), 10**1000])
def test_cash_dividend_requires_positive_finite_amount(amount):
    with pytest.raises(ValueError, match="cash_amount"):
        action(kind=Kind.CASH_DIVIDEND, new_shares=None, old_shares=None, cash_amount=amount)


def test_fixture_identity_and_exact_integer_parquet_roundtrip(tmp_path):
    event = action(event_id="split:AAA:20240103", source=DataSource.FIXTURE, new_shares=2**53)
    store = CorporateActionStore(tmp_path)
    store.write([event])
    assert store.read("AAA") == (event,)
    assert store.read("AAA")[0].new_shares == 2**53
    assert store.read("AAA")[0].ratio == Fraction(2**53)
    assert len(list(tmp_path.rglob("*.tmp"))) == 0


def test_ledger_merges_retrieval_times_but_rejects_term_changes(tmp_path):
    store = CorporateActionStore(tmp_path)
    old = action(fetched_at=datetime(2024, 1, 4, 22, tzinfo=UTC))
    store.write([old])
    store.write([action()])
    store.write([old])
    assert store.read("AAA") == (action(),)
    path = next(tmp_path.rglob("AAA.parquet"))
    original = path.read_bytes()
    with pytest.raises(ValueError, match="conflicting"):
        store.write([action(new_shares=5)])
    assert path.read_bytes() == original
    with pytest.raises(FileNotFoundError, match="unavailable"):
        store.read("BBB")
    store.write([])
    assert not (tmp_path / "raw/corporate_actions/BBB.parquet").exists()


@pytest.mark.parametrize("stage", ["write", "replace"])
def test_ledger_atomic_failure_preserves_original(tmp_path, monkeypatch, stage):
    store = CorporateActionStore(tmp_path)
    store.write([action()])
    path = next(tmp_path.rglob("AAA.parquet"))
    original = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("fixture write failure")

    if stage == "write":
        monkeypatch.setattr(pd.DataFrame, "to_parquet", fail)
    else:
        monkeypatch.setattr("semiedge.data.corporate_actions.os.replace", fail)
    with pytest.raises(OSError, match="fixture"):
        store.write([replace(action(), fetched_at=datetime(2024, 1, 6, tzinfo=UTC))])
    assert path.read_bytes() == original
    assert list(path.parent.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda frame: frame.assign(schema_version=2),
        lambda frame: frame.assign(schema_version=1.0),
        lambda frame: frame.iloc[:0],
        lambda frame: frame.assign(payload_json='{"event_id":"FAKE_SECRET_SENTINEL"}'),
    ],
)
def test_tampered_ledger_fails_closed(tmp_path, mutate):
    store = CorporateActionStore(tmp_path)
    store.write([action()])
    path = next(tmp_path.rglob("AAA.parquet"))
    mutate(pd.read_parquet(path)).to_parquet(path, index=False)
    with pytest.raises(ValueError, match=r"ledger|version") as error:
        store.read("AAA")
    assert "FAKE_SECRET_SENTINEL" not in str(error.value)


def test_invalid_timestamp_payload_is_not_normalized_silently(tmp_path):
    store = CorporateActionStore(tmp_path)
    store.write([action()])
    path = next(tmp_path.rglob("AAA.parquet"))
    frame = pd.read_parquet(path)
    payload = json.loads(frame.payload_json.iloc[0])
    payload["known_at"] = "2024-01-02T12:00:00+00:60"
    frame["payload_json"] = json.dumps(payload)
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="payload failed validation"):
        store.read("AAA")


def test_paginated_raw_source_and_verified_knowledge():
    client = Client([page(token="next"), page([raw(id="event-2", ex_date="2024-01-04")])])
    events = source(client).get_actions(["AAA"], date(2024, 1, 1), DAY)
    assert len(events) == 2
    assert events[0] == action()
    assert events[0].known_at != events[0].fetched_at
    assert all(path == "/corporate-actions" for path, _ in client.calls)
    assert client.calls[0][1]["start"] == "2024-01-01"
    assert client.calls[1][1]["page_token"] == "next"
    # API bounds process_date, not ex_date: do not silently discard the second ex-date.
    assert events[1].ex_date == date(2024, 1, 4)


@pytest.mark.parametrize(
    "pages",
    [
        [{}],
        [{"corporate_actions": []}],
        [page([raw(new_rate=True)])],
        [page([raw(new_rate=0.3333333333333333)])],
        [page([raw(symbol="BBB")])],
        [page([raw(), raw()])],
        [page(token="again"), page([], token="again")],
        [{"corporate_actions": {"unrecognized": []}}],
        [page([raw(id="")])],
        [page([raw(ex_date="2024-W01-3")])],
        [page([raw(ex_date=None)])],
    ],
)
def test_invalid_source_responses_raise_not_empty(pages):
    with pytest.raises(MarketDataError, match="unavailable"):
        source(Client(pages)).get_actions(["AAA"], DAY, DAY)


def test_source_failure_and_missing_knowledge_are_sanitized():
    for adapter in (source(Client([])), source(Client([page()]), knowledge=lambda _: None)):
        with pytest.raises(MarketDataError) as error:
            adapter.get_actions(["AAA"], DAY, DAY)
        assert "FAKE_SECRET_SENTINEL" not in str(error.value)
    assert source(Client([page([])])).get_actions(["AAA"], DAY, DAY) == ()


def test_source_refuses_wrong_endpoint_and_has_bounded_pagination():
    client = Client([page()])
    client._base_url = "https://wrong-data.invalid"
    with pytest.raises(MarketDataError, match="market-data endpoint"):
        source(client)
    assert client.calls == []
    client = Client([page([], token=str(i)) for i in range(100)])
    with pytest.raises(MarketDataError, match="unavailable"):
        source(client).get_actions(["AAA"], DAY, DAY)
    assert len(client.calls) == 100


def bars(closes):
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [100.0] * len(closes),
        },
        index=pd.date_range("2024-01-02", periods=len(closes), freq="B", name="session").as_unit(
            "ns"
        ),
        dtype="float64",
    )


def stored(frame, adjustment=Adjustment.SPLIT):
    return StoredSeries("AAA", frame, DataFeed.SIP, adjustment, DataSource.ALPACA, FETCHED)


def test_mixed_adjusted_split_basis_is_rejected_and_full_refetch_recovers(tmp_path):
    frame = bars([100.0, 100.0, 25.0, 25.0])
    event = action(ex_date=date(2024, 1, 4))
    # Deliberately bypass the merge guard to model a bad vendor snapshot/legacy file.
    path = tmp_path / "raw/bars/daily/feed=sip/adjustment=split/AAA.parquet"
    path.parent.mkdir(parents=True)
    frame.assign(feed="sip", adjustment="split", source="alpaca", fetched_at=FETCHED).to_parquet(
        path
    )
    store = ParquetBarStore(tmp_path)
    mixed = store.read("AAA", feed=DataFeed.SIP, adjustment=Adjustment.SPLIT)
    result = check_adjustment_consistency(mixed, [event])
    assert not result.is_consistent
    assert any("unadjusted split discontinuity" in reason for reason in result.issues)
    frame.attrs["adjustment"] = "raw"
    normalized = normalize_raw_splits(
        frame,
        [event.to_split_event()],
        symbol="AAA",
        adjustment=Adjustment.RAW,
        as_of=date(2024, 1, 5),
        known_at=FETCHED,
        calendar=TradingCalendar(),
    )
    store.write(
        "AAA",
        normalized.frame,
        feed=DataFeed.SIP,
        adjustment=Adjustment.SPLIT,
        source=DataSource.ALPACA,
        fetched_at=datetime(2024, 1, 6, tzinfo=UTC),
    )
    fresh = store.read("AAA", feed=DataFeed.SIP, adjustment=Adjustment.SPLIT)
    assert fresh.frame.close.tolist() == [25.0] * 4
    assert check_adjustment_consistency(fresh, [event]).is_consistent
    # Independent stop-level harness: prices and levels must share the same basis.
    assert frame.low.iloc[2] < 95
    assert normalized.frame.low.iloc[2] > 95 / 4


def test_reverse_split_cannot_create_a_breakout_after_normalization():
    from semiedge.config.models import MomentumV1Params, ScoreNormalization
    from semiedge.strategy.momentum_v1 import compute_features

    frame = bars([100.0, 100.0, 100.0, 400.0])
    event = action(kind=Kind.REVERSE_SPLIT, ex_date=date(2024, 1, 5), new_shares=1, old_shares=4)
    params = MomentumV1Params(
        regime_sma_window=3,
        fast_sma_window=2,
        slow_sma_window=3,
        breakout_lookback=2,
        return_lookback=2,
        volume_lookback=2,
        atr_window=2,
        score_normalization=ScoreNormalization(trend_persistence_window=2),
    )
    regime = bars([100.0] * 4)
    assert bool(compute_features(frame, regime, params).cond_breakout.iloc[-1])
    frame.attrs["adjustment"] = "raw"
    normalized = normalize_raw_splits(
        frame,
        [event.to_split_event()],
        symbol="AAA",
        adjustment=Adjustment.RAW,
        as_of=date(2024, 1, 5),
        known_at=FETCHED,
        calendar=TradingCalendar(),
    )
    assert not bool(compute_features(normalized.frame, regime, params).cond_breakout.iloc[-1])
    assert check_adjustment_consistency(stored(normalized.frame), [event]).is_consistent
    assert not check_adjustment_consistency(stored(frame), [event]).is_consistent


def test_future_announced_actions_do_not_invalidate_historical_series():
    series = stored(bars([100.0] * 4))
    future = action(ex_date=date(2024, 1, 8))
    assert check_adjustment_consistency(series, [future]).is_consistent
    assert not check_adjustment_consistency(
        stored(bars([100.0, 100.0, 20.0, 20.0])), []
    ).is_consistent


def test_cash_price_return_and_all_adjusted_series_have_distinct_expectations():
    event = action(
        kind=Kind.CASH_DIVIDEND,
        ex_date=date(2024, 1, 4),
        new_shares=None,
        old_shares=None,
        cash_amount=75.0,
    )
    frame = bars([100.0, 100.0, 25.0, 25.0])
    assert check_adjustment_consistency(stored(frame), [event]).is_consistent
    assert not check_adjustment_consistency(stored(frame, Adjustment.ALL), [event]).is_consistent


@pytest.mark.parametrize("threshold", [True, 0, -1, 1, float("nan"), 10**1000])
def test_invalid_jump_threshold_refused(threshold):
    with pytest.raises(ValueError, match="jump_threshold"):
        check_adjustment_consistency(stored(bars([100.0] * 4)), [], jump_threshold=threshold)


def test_source_factory_is_raw_read_only_data_client(monkeypatch):
    from unittest.mock import Mock

    factory = Mock(return_value=Client([page()]))
    monkeypatch.setattr("semiedge.data.corporate_actions.CorporateActionsClient", factory)
    adapter = AlpacaCorporateActionSource.from_credentials(
        "PKFIXTURE", "fake-secret", knowledge=lambda _: KNOWN, now=lambda: FETCHED
    )
    factory.assert_called_once_with(api_key="PKFIXTURE", secret_key="fake-secret", raw_data=True)
    assert adapter.get_actions(["AAA"], DAY, DAY) == (action(),)
    assert factory.return_value.calls[0][1]["data_quality"] == "all"
    assert factory.return_value.calls[0][1]["region"] == "us"


def test_incomplete_retrieval_clock_and_untrusted_provenance_are_not_consistent():
    series = stored(bars([100.0] * 4))
    premature = replace(series, fetched_at=datetime(2024, 1, 5, 20, tzinfo=UTC))
    assert not check_adjustment_consistency(premature, []).is_consistent
    assert not check_adjustment_consistency(replace(series, source="alpaca"), []).is_consistent
    with pytest.raises(ValueError, match="match the stored symbol"):
        check_adjustment_consistency(series, [action(symbol="BBB")])
    with pytest.raises(ValueError, match="duplicate"):
        check_adjustment_consistency(series, [action(), action()])


@pytest.mark.parametrize(
    "change",
    [
        lambda f: f.iloc[:0],
        lambda f: f.assign(volume=-1.0),
        lambda f: f.assign(close=float("nan")),
        lambda f: f.assign(high=f.low - 1),
        lambda f: f.drop(columns="volume"),
    ],
)
def test_invalid_histories_have_explicit_inconsistent_results(change):
    result = check_adjustment_consistency(stored(change(bars([100.0] * 4))), [])
    assert not result.is_consistent
    assert result.issues


def test_missing_dependencies_bad_requests_and_factory_errors_never_make_empty_success(monkeypatch):
    from unittest.mock import Mock

    client = Client([page([])])
    with pytest.raises(MarketDataError, match="explicit knowledge"):
        source(client, knowledge=None)
    assert client.calls == []
    with pytest.raises(MarketDataError, match="unavailable"):
        source(client).get_actions(["AAA"], date(2024, 1, 4), DAY)
    assert client.calls == []
    factory = Mock(side_effect=RuntimeError("FAKE_SECRET_SENTINEL"))
    monkeypatch.setattr("semiedge.data.corporate_actions.CorporateActionsClient", factory)
    with pytest.raises(MarketDataError) as error:
        AlpacaCorporateActionSource.from_credentials(
            "PKFIXTURE", "fake", knowledge=lambda _: KNOWN, now=lambda: FETCHED
        )
    assert "FAKE_SECRET_SENTINEL" not in str(error.value)
    with pytest.raises(MarketDataError, match="credentials are missing"):
        AlpacaCorporateActionSource.from_credentials(
            "", "", knowledge=lambda _: KNOWN, now=lambda: FETCHED
        )
