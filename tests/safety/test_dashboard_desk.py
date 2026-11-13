"""Dashboard desk (#108): numbers come from state; markup never carries raw external text."""

import html
import re

import pytest

from semiedge.dashboard.desk import Desk, OpenPosition, Trade, collect_desk, render_desk

pytestmark = pytest.mark.safety

ATTACKS = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "</style><style>body{display:none}</style>",
    "javascript:alert(1)",
    "<svg/onload=alert(1)>",
]


def _desk(**changes):
    base = {
        "mode": "PAPER",
        "source": "synthetic",
        "halted": False,
        "stale_components": ("notifier",),
        "equity": (
            ("2026-11-09", 100_000.0),
            ("2026-11-10", 100_500.0),
            ("2026-11-13", 101_340.23),
        ),
        "scan_session": "2026-11-13",
        "scan_run": "scan-1",
        "evaluated": 4,
        "eligible": 2,
        "risk_approved": 1,
        "risk_rejected": 1,
        "entry_orders": 1,
        "stops_armed": 1,
        "top_symbol": "NVDA",
        "last_rejected": "MU",
        "last_approved": "NVDA",
        "last_ordered": "NVDA",
        "reasons": (("no_breakout", 2), ("risk: max_positions", 1)),
        "positions": (
            OpenPosition("AVGO", 20, 336.10, 316.0, 374.0, "3", protected=True),
            OpenPosition("AMD", 10, 148.0, None, None, "unavailable", protected=False),
        ),
        "trades": (
            Trade("MU", 245.0, "target hit", "2026-11-12"),
            Trade("AMD", -50.0, "stop hit", "2026-11-11"),
        ),
        "heartbeats": (("engine", "fresh", 45.0), ("notifier", "stale", 1400.0)),
        "last_run": ("post_close, ok", "2026-11-13 21:22 UTC"),
    }
    base.update(changes)
    return Desk(**base)


def _visible(markup):
    """Text a reader sees: tags and the stylesheet removed, entities decoded."""
    body = re.sub(r"<style>.*?</style>", "", markup, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", body))


def test_desk_shows_the_numbers_it_was_given():
    markup = render_desk(_desk())
    seen = _visible(markup)
    assert "$101,340" in seen
    assert ".23" in seen
    assert "+1,340.23 since Nov 09" in seen
    assert "+1.34%" in seen
    assert "2 closed · 50% win" in seen
    assert "1 green / 1 red" in seen
    assert "+245.00" in seen
    assert "-50.00" in seen
    assert "1 approved · 1 rejected" in seen
    assert "stop armed" in seen
    assert "no stop order" in seen
    assert "stop/target not recorded" in seen
    assert "no breakout" in seen
    assert "risk: max positions" in seen
    assert "PAPER · STALE HEARTBEAT" in seen


@pytest.mark.parametrize("attack", ATTACKS)
def test_external_text_never_becomes_markup(attack):
    desk = _desk(
        mode=attack,
        scan_session=attack,
        reasons=((attack, 3),),
        heartbeats=((attack, "fresh", 1.0),),
        last_run=(attack, attack),
        trades=(Trade("MU", 1.0, attack, attack),),
        positions=(OpenPosition("AVGO", 1, 1.0, 0.5, 2.0, attack, protected=True),),
        top_symbol=attack,
        last_rejected=attack,
    )
    markup = render_desk(desk)
    body = re.sub(r"<style>.*?</style>", "", markup, flags=re.S)
    for needle in ("<script", "<svg/", "</style>", "onerror=alert(1)>"):
        assert needle not in body.lower()
    # No real tag may carry an event handler; escaped text like "&lt;img onerror=" is inert.
    assert not re.search(r"<[a-z][^>]*\son\w+\s*=", body, re.I)
    # The only src attributes are images built here from numbers and fixed SVG paths.
    for src in re.findall(r'\ssrc="([^"]*)"', body):
        assert src.startswith("data:image/svg+xml;base64,")
    assert not re.search(r"<[a-z][^>]*\shref\s*=", body, re.I)
    assert markup.count("<style>") == 1
    assert markup.count("</style>") == 1
    assert "?" in _visible(markup)  # an invalid symbol renders as "?"


def test_empty_state_says_so_instead_of_inventing_numbers():
    desk = _desk(
        equity=(),
        evaluated=0,
        eligible=0,
        risk_approved=None,
        risk_rejected=None,
        entry_orders=None,
        stops_armed=0,
        reasons=(),
        positions=(),
        trades=(),
        heartbeats=(),
        scan_session=None,
        top_symbol=None,
        last_rejected=None,
        last_approved=None,
        last_ordered=None,
    )
    seen = _visible(render_desk(desk))
    assert "No equity snapshot has been recorded yet." in seen
    assert "No open positions." in seen
    assert "No closed trades yet." in seen
    assert "Nothing was rejected." in seen
    assert "no decisions yet" in seen
    assert "no scan yet" in seen
    assert "no heartbeats recorded" in seen
    assert "$" not in seen.split("Total equity")[1].split("No equity")[0]


def test_halt_wins_over_running():
    assert "PAPER · HALTED" in _visible(render_desk(_desk(halted=True)))


class _Frame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        assert orient == "records"
        return self.rows


class _Intent:
    def __init__(self, coid, kind, symbol, *, side="buy", parent=None, session="2026-11-13"):
        self.client_order_id = coid
        self.kind = kind
        self.symbol = symbol
        self.side = side
        self.parent_client_order_id = parent
        self.decision_session = session


class _Order:
    def __init__(self, intent, status="filled"):
        self.intent = intent
        self.status = status


class _Fill:
    def __init__(self, coid, qty, price, status="final"):
        self.client_order_id = coid
        self.qty = qty
        self.price = price
        self.status = status


class _Pos:
    def __init__(self):
        from datetime import UTC, datetime

        self.symbol = "MU"
        self.status = "closed"
        self.entry_client_order_id = "e1"
        self.avg_entry_price = 100.0
        self.stop = 95.0
        self.target = 110.0
        self.closed_at = datetime(2026, 11, 12, 21, tzinfo=UTC)


class _Store:
    def risk_decisions(self, session):
        assert session == "2026-11-13"
        return _Frame(
            [
                {"symbol": "NVDA", "approved": 1, "approved_shares": 5, "reasons_json": "[]"},
                {
                    "symbol": "AVGO",
                    "approved": 0,
                    "approved_shares": 0,
                    "reasons_json": '["max_positions"]',
                },
            ]
        )

    def orders(self, kinds):
        orders = [
            _Order(_Intent("e1", "entry", "MU", session="2026-11-10")),
            _Order(_Intent("e2", "entry", "NVDA")),
            _Order(_Intent("p1", "protection", "MU", side="sell", parent="e1")),
            _Order(
                _Intent("p2", "protection", "NVDA", side="sell", parent="e2"), status="accepted"
            ),
        ]
        return [o for o in orders if o.intent.kind in kinds]

    def fills(self):
        return [
            _Fill("p1", 6, 110.0),
            _Fill("p1", 4, 109.0),
            _Fill("p1", 4, 1.0, status="superseded"),
        ]

    def positions(self, include_closed=False):
        return [_Pos()] if include_closed else []

    def active_halts(self):
        return ()


def test_collect_desk_counts_and_realized_pnl_from_fills():
    signals = [
        {"session": "2026-11-13", "symbol": "NVDA", "eligible": 1, "payload_json": "{}"},
        {"session": "2026-11-13", "symbol": "AVGO", "eligible": 1, "payload_json": "{}"},
        {
            "session": "2026-11-13",
            "symbol": "MU",
            "eligible": 0,
            "payload_json": '{"rejection_reasons": ["no_breakout"]}',
        },
        {"session": "2026-11-13", "symbol": "AMD", "eligible": 0, "payload_json": "{broken"},
    ]
    desk = collect_desk(
        _Store(),
        mode="PAPER",
        source="synthetic",
        scan="scan-1",
        signals=signals,
        equity=[
            {"session": "2026-11-12", "equity": 100_000.0},
            {"session": "2026-11-13", "equity": True},
        ],
        heartbeats=[{"component": "engine", "freshness": "fresh", "age_seconds": 5.0}],
        positions=[],
        last_run=("post_close, ok", "now"),
    )
    assert (desk.evaluated, desk.eligible) == (4, 2)
    assert (desk.risk_approved, desk.risk_rejected, desk.entry_orders) == (1, 1, 1)
    assert desk.stops_armed == 0  # a live stop counts only while its position is open
    assert desk.equity == (("2026-11-12", 100_000.0),)  # a bool is not an equity value
    assert dict(desk.reasons) == {
        "no_breakout": 1,
        "payload_unreadable": 1,
        "risk: max_positions": 1,
    }
    [trade] = desk.trades
    assert trade.symbol == "MU"
    assert trade.pnl == pytest.approx(6 * 110 + 4 * 109 - 10 * 100)  # superseded fill excluded
    assert trade.exit_label == "target hit"


def test_desk_symbol_rule_matches_the_store():
    from semiedge.dashboard.desk import SYMBOL_PATTERN as DESK_RULE
    from semiedge.state.store import SYMBOL_PATTERN as STORE_RULE

    assert DESK_RULE.pattern == STORE_RULE.pattern
