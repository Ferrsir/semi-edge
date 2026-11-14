"""The trading desk on the dashboard's Overview page (#108). Pure Python, no UI library.

``collect_desk`` reads numbers from the state store. ``render_desk`` turns them into one HTML
fragment for ``st.html``. Every value that came from the database or a broker passes through
``_t`` (cleaned by ``display``, then HTML-escaped) or is a number we format ourselves; symbols
must also match the store's symbol pattern. Nothing is invented: missing data renders as an
explicit "unavailable" line or an empty state.
"""

from __future__ import annotations

import base64
import html
import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from semiedge.security.untrusted import UntrustedText
from semiedge.state.models import TERMINAL_STATUSES

UNAVAILABLE = "unavailable"
# Same rule as the state store's symbol check; the dashboard may not import the writer.
SYMBOL_PATTERN = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")


def _t(value: object) -> str:
    """Clean external text, then escape it for HTML. The only way strings enter the markup."""
    return html.escape(UntrustedText.from_raw("file", str(value)).text, quote=True)


def _sym(value: object) -> str:
    text = str(value)
    return html.escape(text) if SYMBOL_PATTERN.fullmatch(text) else "?"


def _money(value: float, *, sign: bool = False) -> str:
    return f"{value:+,.2f}" if sign else f"{value:,.2f}"


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


@dataclass(frozen=True)
class Trade:
    symbol: str
    pnl: float
    exit_label: str
    closed: str


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    qty: int
    entry: float
    stop: float | None
    target: float | None
    held: str
    protected: bool


@dataclass(frozen=True)
class Desk:
    mode: str
    source: str
    halted: bool
    stale_components: tuple[str, ...]
    equity: tuple[tuple[str, float], ...]
    scan_session: str | None
    scan_run: str | None
    evaluated: int
    eligible: int
    risk_approved: int | None
    risk_rejected: int | None
    entry_orders: int | None
    stops_armed: int
    top_symbol: str | None
    last_rejected: str | None
    last_approved: str | None
    last_ordered: str | None
    reasons: tuple[tuple[str, int], ...]
    positions: tuple[OpenPosition, ...]
    trades: tuple[Trade, ...]
    heartbeats: tuple[tuple[str, str, float], ...]
    last_run: tuple[str, str] = (UNAVAILABLE, UNAVAILABLE)
    notes: tuple[str, ...] = field(default=())


# ------------------------------------------------------------------ collection


def _reasons_from_payload(raw: object) -> list[str]:
    try:
        payload = json.loads(str(raw))
    except ValueError:
        return ["payload_unreadable"]
    reasons = payload.get("rejection_reasons") if isinstance(payload, dict) else None
    return [str(r) for r in reasons] if isinstance(reasons, list) else []


def _json_list(raw: object) -> list[str]:
    try:
        value = json.loads(str(raw))
    except ValueError:
        return ["reasons_unreadable"]
    return [str(v) for v in value] if isinstance(value, list) else []


def _exit_label(kind: str, price: float | None, stop: float | None, target: float | None) -> str:
    if kind == "time_exit":
        return "time exit"
    if kind == "flatten":
        return "flattened"
    if kind == "protection" and price is not None and stop is not None and target is not None:
        return "target hit" if abs(price - target) < abs(price - stop) else "stop hit"
    return "exit"


def collect_desk(
    store: Any,
    *,
    mode: str,
    source: str,
    scan: str | None,
    signals: list[dict[str, Any]],
    equity: list[dict[str, Any]],
    heartbeats: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    last_run: tuple[str, str],
) -> Desk:
    """Build the desk numbers from rows the view model already loaded plus three store reads."""
    points = tuple(
        (str(r["session"]), v) for r in equity if (v := _finite(r.get("equity"))) is not None
    )
    eligible_rows = [r for r in signals if r.get("eligible") in (1, True)]
    rejected_rows = [r for r in signals if r.get("eligible") in (0, False)]
    session = str(signals[0]["session"]) if signals else None
    reasons: Counter[str] = Counter()
    for row in rejected_rows:
        reasons.update(_reasons_from_payload(row.get("payload_json")) or ["no_reason_recorded"])

    risk_approved = risk_rejected = entry_orders = None
    last_rejected = str(rejected_rows[-1]["symbol"]) if rejected_rows else None
    last_approved = last_ordered = None
    if session is not None:
        frame = store.risk_decisions(session)
        decisions = frame.to_dict(orient="records")
        if decisions:
            risk_approved = sum(1 for d in decisions if d["approved"])
            risk_rejected = len(decisions) - risk_approved
            for d in decisions:
                if not d["approved"]:
                    reasons.update(f"risk: {r}" for r in _json_list(d["reasons_json"]))
            approved = [d for d in decisions if d["approved"]]
            last_approved = str(approved[-1]["symbol"]) if approved else None
        entries = [
            o for o in store.orders(kinds=["entry"]) if str(o.intent.decision_session) == session
        ]
        entry_orders = len(entries)
        last_ordered = entries[-1].intent.symbol if entries else None

    open_symbols = {str(p["symbol"]) for p in positions if p.get("status") in {"open", "exiting"}}
    protection = [
        o
        for o in store.orders(kinds=["protection"])
        if o.status not in TERMINAL_STATUSES and o.intent.symbol in open_symbols
    ]
    protected_symbols = {o.intent.symbol for o in protection}
    open_positions = tuple(
        OpenPosition(
            symbol=str(p["symbol"]),
            qty=int(p["qty"]),
            entry=float(p["avg_entry_price"]),
            stop=_finite(p.get("stop")),
            target=_finite(p.get("target")),
            held=str(p.get("held_sessions", UNAVAILABLE)),
            protected=str(p["symbol"]) in protected_symbols,
        )
        for p in positions
        if p.get("status") in {"open", "exiting"}
    )

    trades = tuple(_closed_trades(store))
    return Desk(
        mode=mode,
        source=source,
        halted=bool(store.active_halts()),
        stale_components=tuple(
            str(h["component"]) for h in heartbeats if h["freshness"] == "stale"
        ),
        equity=points,
        scan_session=session,
        scan_run=scan,
        evaluated=len(signals),
        eligible=len(eligible_rows),
        risk_approved=risk_approved,
        risk_rejected=risk_rejected,
        entry_orders=entry_orders,
        stops_armed=len(protection),
        top_symbol=str(eligible_rows[0]["symbol"]) if eligible_rows else None,
        last_rejected=last_rejected,
        last_approved=last_approved,
        last_ordered=last_ordered,
        reasons=tuple(reasons.most_common(6)),
        positions=open_positions,
        trades=trades,
        heartbeats=tuple(
            (str(h["component"]), str(h["freshness"]), float(h["age_seconds"])) for h in heartbeats
        ),
        last_run=last_run,
    )


def _closed_trades(store: Any, limit: int = 6) -> Iterable[Trade]:
    """Realized P&L per closed position: exit fills against the average entry price."""
    closed = [p for p in store.positions(include_closed=True) if p.status == "closed"]
    if not closed:
        return []
    exits = [
        o
        for o in store.orders(kinds=["protection", "time_exit", "flatten"])
        if o.intent.side == "sell"
    ]
    fills_by_order: dict[str, list[Any]] = {}
    for f in store.fills():
        if f.status != "superseded":
            fills_by_order.setdefault(f.client_order_id, []).append(f)
    out = []

    def closed_key(p: Any) -> float:
        return p.closed_at.timestamp() if p.closed_at is not None else float("-inf")

    for pos in sorted(closed, key=closed_key, reverse=True)[:limit]:
        mine = [o for o in exits if o.intent.parent_client_order_id == pos.entry_client_order_id]
        fills = [f for o in mine for f in fills_by_order.get(o.intent.client_order_id, [])]
        qty = sum(f.qty for f in fills)
        if not fills or qty <= 0:
            continue
        proceeds = math.fsum(f.qty * f.price for f in fills)
        pnl = proceeds - qty * pos.avg_entry_price
        last = mine[-1]
        out.append(
            Trade(
                symbol=pos.symbol,
                pnl=pnl,
                exit_label=_exit_label(last.intent.kind, proceeds / qty, pos.stop, pos.target),
                closed=pos.closed_at.date().isoformat() if pos.closed_at else UNAVAILABLE,
            )
        )
    return out


# ------------------------------------------------------------------ rendering

_ICONS = {
    "scan": '<path d="M4 12h3l2-5 4 10 2-5h5"/>',
    "rules": '<path d="M4 5h16l-6 8v5l-4 2v-7z"/>',
    "risk": '<path d="M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z"/>',
    "orders": '<path d="M5 7h14M5 12h14M5 17h9"/>',
    "guard": '<circle cx="12" cy="12" r="8"/><path d="M12 4v4M12 16v4M4 12h4M16 12h4"/>',
}


_TONE_HEX = {"blue": "#3987e5", "bad": "#e5484d", "warn": "#d9a21b", "ok": "#35b779", "": "#e8ecf3"}


def _svg_img(svg: str, *, cls: str, alt: str = "") -> str:
    """Inline SVG as a data-URI image: st.html's sanitizer drops <svg>, and an SVG loaded as an
    image cannot run script. Only markup built here from numbers and fixed paths goes in."""
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img class="{cls}" alt="{html.escape(alt)}" src="data:image/svg+xml;base64,{data}">'


def _icon(name: str, tone: str = "") -> str:
    color = _TONE_HEX.get(tone, _TONE_HEX[""])
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round">'
        f"{_ICONS[name]}</svg>"
    )
    return _svg_img(svg, cls="ic")


def _count(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


def _short_day(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%b %d")
    except ValueError:
        return _t(iso)


def _curve(points: tuple[tuple[str, float], ...]) -> str:
    if len(points) < 2:
        return '<p class="muted pad">The curve appears after two equity snapshots.</p>'
    w, h, pad = 1000.0, 160.0, 10.0  # close to the card's aspect, so dots stay round
    values = [v for _, v in points]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = (w - 2 * pad) / (len(points) - 1)
    xy = [
        (pad + i * step, pad + (h - 2 * pad) * (1 - (v - lo) / span))
        for i, (_, v) in enumerate(points)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    area = f"{pad:.1f},{h:.1f} {line} {xy[-1][0]:.1f},{h:.1f}"
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="#9cc5f5"/>' for x, y in xy[:-1]
    )
    end_x, end_y = xy[-1]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" '
        'preserveAspectRatio="none">'
        '<defs><linearGradient id="se-fill" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#3987e5" stop-opacity=".28"/>'
        '<stop offset="1" stop-color="#3987e5" stop-opacity="0"/></linearGradient>'
        '<filter id="se-glow" x="-10%" y="-30%" width="120%" height="160%">'
        '<feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/>'
        '<feMergeNode in="SourceGraphic"/></feMerge></filter></defs>'
        f'<polygon points="{area}" fill="url(#se-fill)"/>'
        f'<polyline points="{line}" fill="none" stroke="#3987e5" stroke-width="2" '
        'filter="url(#se-glow)" vector-effect="non-scaling-stroke"/>'
        f"<g>{dots}</g>"
        f'<circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="5" fill="#9cc5f5" stroke="#3987e5" '
        'stroke-width="3"/></svg>'
    )
    return _svg_img(svg, cls="curve", alt="Equity by session")


def _status(desk: Desk) -> tuple[str, str]:
    if desk.halted:
        return "HALTED", "bad"
    if desk.stale_components:
        return "STALE HEARTBEAT", "warn"
    if any(
        component == "engine" and freshness == "fresh"
        for component, freshness, _ in desk.heartbeats
    ):
        return "RUNNING", "ok"
    if desk.mode.upper().startswith("UNAVAILABLE"):
        return "NO RUN YET", "warn"
    return "STATUS UNKNOWN", "warn"


def _hero(desk: Desk) -> str:
    if not desk.equity:
        return (
            '<section class="card hero"><div class="lbl">Total equity</div>'
            '<div class="big">unavailable</div>'
            '<p class="muted">No equity snapshot has been recorded yet.</p></section>'
        )
    first_day, first = desk.equity[0]
    last_day, last = desk.equity[-1]
    dollars, cents = f"{last:,.2f}".split(".")
    change = last - first
    pct = (last / first - 1) if first else None
    wins = sum(1 for t in desk.trades if t.pnl > 0)
    record = (
        f"{len(desk.trades)} closed · {wins / len(desk.trades):.0%} win"
        if desk.trades
        else "no closed trades yet"
    )
    tone = "up" if change >= 0 else "down"
    pct_text = "—" if pct is None else f"{pct:+.2%}"
    return (
        '<section class="card hero">'
        '<div class="hero-top"><div>'
        '<div class="lbl">Total equity</div>'
        f'<div class="big">${dollars}<span class="cents">.{cents}</span></div>'
        f'<div class="delta {tone}">{_money(change, sign=True)} since {_short_day(first_day)}</div>'
        '</div><div class="hero-side">'
        f'<div class="mult {tone}">{pct_text}</div><div class="muted sm">since first snapshot</div>'
        f'<div class="rec">{record}</div></div></div>'
        f"{_curve(desk.equity)}"
        '<div class="hero-foot">'
        f"<span>Start {_money(first)} · {_short_day(first_day)}</span>"
        f"<span>{_short_day(last_day)}</span></div></section>"
    )


def _tiles(desk: Desk) -> str:
    filtered = desk.evaluated - desk.eligible
    return (
        '<section class="tiles">'
        f'<div class="card tile"><div class="num">{desk.evaluated:,}</div>'
        '<div class="lbl">Signals evaluated</div></div>'
        f'<div class="card tile"><div class="num bad">{filtered:,}</div>'
        '<div class="lbl">Filtered out</div></div>'
        f'<div class="card tile"><div class="num blue">{_count(desk.entry_orders)}</div>'
        '<div class="lbl">Orders placed</div></div></section>'
    )


def _stage(icon: str, title: str, state: str, tone: str, symbol: str | None) -> str:
    sym = _sym(symbol) if symbol else "—"
    return (
        f'<div class="card stage {tone}"><div class="st-head">{_icon(icon, tone)}'
        f"<b>{title}</b></div>"
        f'<div class="st-state">{state}</div><div class="st-sym">{sym}</div></div>'
    )


def _pipeline(desk: Desk) -> str:
    filtered = desk.evaluated - desk.eligible
    risk_state = (
        "no decisions yet"
        if desk.risk_approved is None
        else f"{desk.risk_approved} approved · {desk.risk_rejected} rejected"
    )
    return (
        '<section class="stages">'
        + _stage("scan", "Scanner", f"{desk.evaluated} evaluated", "blue", desk.top_symbol)
        + _stage(
            "rules", "Rules", f"{filtered} rejected", "bad" if filtered else "", desk.last_rejected
        )
        + _stage(
            "risk", "Risk", risk_state, "warn" if desk.risk_rejected else "", desk.last_approved
        )
        + _stage("orders", "Orders", f"{_count(desk.entry_orders)} placed", "", desk.last_ordered)
        + _stage(
            "guard",
            "Guard",
            f"{desk.stops_armed} stop{'' if desk.stops_armed == 1 else 's'} armed",
            "ok" if desk.stops_armed else "",
            desk.positions[0].symbol if desk.positions else None,
        )
        + "</section>"
    )


def _funnel(desk: Desk) -> str:
    filtered = desk.evaluated - desk.eligible
    not_ordered = (
        None
        if desk.risk_rejected is None
        else desk.risk_rejected + max(0, (desk.risk_approved or 0) - (desk.entry_orders or 0))
    )
    nodes = [("Scan", "scan"), ("Rules", "rules"), ("Risk", "risk"), ("Orders", "orders")]
    node_html = "".join(
        f'<div class="node"><span class="dot">{_icon(icon)}</span><span>{name}</span></div>'
        for name, icon in nodes
    )
    return (
        '<section class="card funnel"><div class="sec-head"><div><h3>Signal funnel</h3>'
        '<p class="muted">One failed check stops a signal.</p></div>'
        f'<span class="tag">{desk.eligible:,} passed the rules</span></div>'
        f'<div class="track">{node_html}</div><div class="buckets">'
        f'<div class="bucket bad"><div class="num">{filtered:,}</div>'
        '<div class="lbl">Filtered</div>'
        '<div class="muted sm">failed a hard rule</div></div>'
        f'<div class="bucket warn"><div class="num">{_count(not_ordered)}</div>'
        '<div class="lbl">Not ordered</div><div class="muted sm">risk check said no</div></div>'
        f'<div class="bucket blue"><div class="num">{_count(desk.entry_orders)}</div>'
        '<div class="lbl">Ordered</div><div class="muted sm">stop set before entry</div></div>'
        "</div></section>"
    )


def _range_bar(p: OpenPosition) -> str:
    if p.stop is None or p.target is None or p.target <= p.stop:
        return '<div class="muted sm">stop/target not recorded</div>'
    pos = min(1.0, max(0.0, (p.entry - p.stop) / (p.target - p.stop)))
    return (
        f'<div class="range"><span class="bar"><i style="left:{pos * 100:.1f}%"></i></span>'
        f'<span class="ends"><em>stop {_money(p.stop)}</em><em>target {_money(p.target)}</em>'
        "</span></div>"
    )


def _held(held: str) -> str:
    if held.isdigit():
        return f"held {held} session" + ("" if held == "1" else "s")
    return f"held: {_t(held)}"


def _positions(desk: Desk) -> str:
    head = (
        '<div class="sec-head"><h3>Where the bot is trading</h3>'
        f'<span class="tag">{len(desk.positions)} open</span></div>'
    )
    if not desk.positions:
        return (
            f'<section class="block">{head}<div class="card empty">No open positions. The bot '
            "buys only after a breakout passes every rule and the risk check.</div></section>"
        )
    cards = "".join(
        f'<div class="card pos"><div class="pos-head"><b>{_sym(p.symbol)}</b>'
        f'<span class="pill {"ok" if p.protected else "bad"}">'
        f"{'stop armed' if p.protected else 'no stop order'}</span></div>"
        f'<div class="pos-line">{p.qty:,} sh @ {_money(p.entry)}</div>'
        f"{_range_bar(p)}"
        f'<div class="muted sm">{_held(p.held)}</div></div>'
        for p in desk.positions
    )
    return f'<section class="block">{head}<div class="grid3">{cards}</div></section>'


def _trades(desk: Desk) -> str:
    wins = sum(1 for t in desk.trades if t.pnl > 0)
    head = (
        '<div class="sec-head"><h3>Closed trades</h3>'
        f'<span class="tag">{wins} green / {len(desk.trades) - wins} red</span></div>'
    )
    if not desk.trades:
        empty = '<div class="card empty">No closed trades yet.</div>'
        return f'<section class="block">{head}{empty}</section>'
    cards = "".join(
        f'<div class="card trade"><div class="tr-head">'
        f'<i class="dot {"ok" if t.pnl > 0 else "bad"}">'
        f"</i><b>{_sym(t.symbol)}</b></div>"
        f'<div class="pnl {"up" if t.pnl > 0 else "down"}">{_money(t.pnl, sign=True)}</div>'
        f'<div class="lbl">{_t(t.exit_label)}</div><div class="muted sm">{_t(t.closed)}</div></div>'
        for t in desk.trades
    )
    return f'<section class="block">{head}<div class="grid3">{cards}</div></section>'


def _reasons(desk: Desk) -> str:
    head = (
        '<div class="sec-head"><h3>Why signals did not qualify</h3>'
        '<span class="tag">latest scan</span></div>'
    )
    if not desk.reasons:
        empty = '<p class="muted">Nothing was rejected.</p>'
        return f'<section class="card block">{head}{empty}</section>'
    top = desk.reasons[0][1] or 1
    rows = "".join(
        f'<div class="reason"><span>{_t(r.replace("_", " "))}</span>'
        f'<span class="rbar"><i style="width:{n / top * 100:.0f}%"></i></span><b>{n:,}</b></div>'
        for r, n in desk.reasons
    )
    return f'<section class="card block">{head}{rows}</section>'


_BEAT_TONE = {"stale": "bad", "fresh": "ok"}


def _system(desk: Desk) -> str:
    beats = (
        "".join(
            f'<span class="beat"><i class="dot {_BEAT_TONE.get(f, "warn")}"></i>{_t(c)}</span>'
            for c, f, _age in desk.heartbeats
        )
        or '<span class="muted">no heartbeats recorded</span>'
    )
    return (
        f'<section class="sys"><div>{beats}</div>'
        f'<div class="muted sm">last run: {_t(desk.last_run[0])} · {_t(desk.last_run[1])}</div>'
        "</section>"
    )


def render_desk(desk: Desk) -> str:
    """The full desk as one escaped HTML fragment."""
    status, tone = _status(desk)
    synthetic = ""
    session = _t(desk.scan_session) if desk.scan_session else "no scan yet"
    return (
        f"<style>{DESK_CSS}</style>"
        '<div class="se-desk">'
        '<header class="top"><div class="brand"><span class="mark">SE</span><div>'
        "<b>SEMI-EDGE</b><small>trading desk</small></div></div>"
        f'<div class="pills">{synthetic}<span class="pill {tone}">PAPER · {status}</span></div>'
        "</header>"
        f'<div class="rule"><span class="chip">SESSION {session}</span>'
        f"<span>Mode {_t(desk.mode)} · "
        "read-only status and recorded trading activity</span>"
        "</div>"
        f"{_hero(desk)}{_tiles(desk)}{_pipeline(desk)}{_funnel(desk)}"
        f"{_positions(desk)}{_trades(desk)}{_reasons(desk)}{_system(desk)}"
        "</div>"
    )


DESK_CSS = """
.se-desk{--bg:#0a0d14;--panel:#111723;--hero:#0c1424;--line:#1f2838;--text:#e8ecf3;
  --muted:#94a0b4;--blue:#3987e5;--ok:#35b779;--bad:#e5484d;--warn:#d9a21b;color:var(--text);
  font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  font-variant-numeric:tabular-nums;display:flex;flex-direction:column;gap:14px;
  max-width:1180px;}
.se-desk h3{margin:0;font-size:17px;font-weight:650;letter-spacing:-.01em;}
.se-desk p{margin:0;}
.se-desk .muted{color:var(--muted);}
.se-desk .sm{font-size:12px;}
.se-desk .lbl{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);}
.se-desk .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:14px 16px;}
.se-desk .top{display:flex;justify-content:space-between;align-items:center;gap:12px;
  flex-wrap:wrap;}
.se-desk .brand{display:flex;gap:10px;align-items:center;}
.se-desk .brand b{font-size:20px;letter-spacing:-.02em;}
.se-desk .brand small{display:block;color:var(--blue);font-size:11px;letter-spacing:.08em;
  text-transform:uppercase;}
.se-desk .mark{width:36px;height:36px;border-radius:10px;display:grid;place-items:center;
  background:#e8ecf3;color:#0a0d14;font-weight:800;font-size:14px;}
.se-desk .pills{display:flex;gap:8px;flex-wrap:wrap;}
.se-desk .pill{font-size:11px;font-weight:700;letter-spacing:.06em;padding:5px 10px;
  border-radius:999px;border:1px solid var(--line);background:#0d1320;}
.se-desk .pill.ok{color:var(--ok);border-color:#1f4d36;}
.se-desk .pill.bad{color:var(--bad);border-color:#5a2326;}
.se-desk .pill.warn{color:var(--warn);border-color:#5a4512;}
.se-desk .rule{display:flex;gap:10px;align-items:center;background:var(--panel);
  border:1px solid var(--line);border-radius:14px;padding:6px 8px;color:var(--muted);
  font-size:13px;flex-wrap:wrap;}
.se-desk .chip{background:#e8ecf3;color:#0a0d14;border-radius:999px;padding:3px 10px;
  font-size:11px;font-weight:700;letter-spacing:.06em;}
.se-desk .hero{background:var(--hero);border-color:#22314a;padding:18px 20px 12px;}
.se-desk .hero-top{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;}
.se-desk .big{font-size:52px;font-weight:750;letter-spacing:-.03em;line-height:1.05;
  margin:4px 0;}
.se-desk .cents{font-size:28px;color:var(--muted);font-weight:600;}
.se-desk .delta{font-weight:600;}
.se-desk .up{color:var(--ok);}
.se-desk .down{color:var(--bad);}
.se-desk .hero-side{text-align:right;}
.se-desk .mult{font-size:30px;font-weight:750;letter-spacing:-.02em;}
.se-desk .rec{margin-top:10px;color:var(--text);font-weight:600;}
.se-desk .curve{display:block;width:100%;height:150px;margin-top:10px;object-fit:fill;}
.se-desk .hero-foot{display:flex;justify-content:space-between;color:var(--muted);
  font-size:11px;letter-spacing:.06em;text-transform:uppercase;border-top:1px solid #1d2a40;
  padding-top:8px;margin-top:6px;}
.se-desk .tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;}
.se-desk .num{font-size:28px;font-weight:750;letter-spacing:-.02em;}
.se-desk .num.bad{color:var(--bad);}
.se-desk .num.blue{color:var(--blue);}
.se-desk .stages{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;}
.se-desk .stage{padding:12px;}
.se-desk .st-head{display:flex;gap:8px;align-items:center;font-size:15px;}
.se-desk .ic{width:18px;height:18px;flex:none;display:block;}
.se-desk .st-state{font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
  color:var(--muted);margin-top:8px;}
.se-desk .st-sym{font-weight:700;margin-top:2px;}
.se-desk .stage.blue{border-color:#244a7a;}
.se-desk .stage.blue .st-state,.se-desk .stage.blue .ic{color:var(--blue);}
.se-desk .stage.bad{border-color:#5a2326;}
.se-desk .stage.bad .st-state,.se-desk .stage.bad .ic{color:var(--bad);}
.se-desk .stage.warn .st-state,.se-desk .stage.warn .ic{color:var(--warn);}
.se-desk .stage.ok .st-state,.se-desk .stage.ok .ic{color:var(--ok);}
.se-desk .sec-head{display:flex;justify-content:space-between;align-items:baseline;gap:10px;
  margin-bottom:12px;}
.se-desk .tag{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);}
.se-desk .track{display:flex;justify-content:space-between;position:relative;
  margin:4px 6px 16px;}
.se-desk .track:before{content:"";position:absolute;left:14px;right:14px;top:15px;height:2px;
  background:var(--line);}
.se-desk .node{display:flex;flex-direction:column;align-items:center;gap:6px;position:relative;
  font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);}
.se-desk .node .dot{width:32px;height:32px;border-radius:50%;display:grid;place-items:center;
  background:var(--panel);border:1px solid var(--line);color:var(--text);}
.se-desk .buckets{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;}
.se-desk .bucket{border:1px solid var(--line);border-radius:12px;padding:12px;
  background:#0d1320;}
.se-desk .bucket.bad .num{color:var(--bad);}
.se-desk .bucket.warn .num{color:var(--warn);}
.se-desk .bucket.blue .num{color:var(--blue);}
.se-desk .grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;}
.se-desk .empty{color:var(--muted);}
.se-desk .pos-head,.se-desk .tr-head{display:flex;justify-content:space-between;
  align-items:center;gap:8px;}
.se-desk .tr-head{justify-content:flex-start;}
.se-desk .pos-head b,.se-desk .tr-head b{font-size:16px;}
.se-desk .pos-line{margin:6px 0 8px;font-weight:600;}
.se-desk .range .bar{display:block;height:6px;border-radius:3px;position:relative;
  background:linear-gradient(90deg,#5a2326,#1d2a40 40%,#1f4d36);}
.se-desk .range .bar i{position:absolute;top:-4px;width:3px;height:14px;border-radius:2px;
  background:var(--text);}
.se-desk .range .ends{display:flex;justify-content:space-between;font-size:11px;
  color:var(--muted);margin:4px 0;}
.se-desk .range em{font-style:normal;}
.se-desk .pnl{font-size:28px;font-weight:750;letter-spacing:-.02em;margin:4px 0;}
.se-desk i.dot{width:8px;height:8px;border-radius:50%;display:inline-block;
  background:var(--muted);}
.se-desk i.dot.ok{background:var(--ok);}
.se-desk i.dot.bad{background:var(--bad);}
.se-desk i.dot.warn{background:var(--warn);}
.se-desk .reason{display:grid;grid-template-columns:minmax(120px,1.2fr) 2fr 48px;gap:12px;
  align-items:center;padding:5px 0;color:var(--muted);}
.se-desk .reason b{text-align:right;color:#f08a8d;}
.se-desk .rbar{height:6px;border-radius:3px;background:#1a2232;overflow:hidden;}
.se-desk .rbar i{display:block;height:100%;background:#a3373b;border-radius:3px;}
.se-desk .sys{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;
  align-items:center;color:var(--muted);font-size:13px;padding:2px 4px;}
.se-desk .beat{display:inline-flex;gap:6px;align-items:center;margin-right:14px;}
@media (max-width:820px){
  .se-desk .stages{grid-template-columns:repeat(2,1fr);}
  .se-desk .grid3{grid-template-columns:1fr;}
  .se-desk .big{font-size:40px;}
}
@media (max-width:520px){
  .se-desk .tiles,.se-desk .buckets{grid-template-columns:1fr;}
  .se-desk .hero-side{text-align:left;}
  .se-desk .reason{grid-template-columns:1fr 1fr 40px;}
}
"""
