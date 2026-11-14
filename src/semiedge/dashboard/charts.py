"""Price charts for the dashboard's Charts page (#116). Pure Python, no UI library.

Reads the local price cache read-only (``ParquetBarStore``) and builds plain Vega-Lite specs, so
nothing here needs Streamlit or Altair and the base install can test it. Symbols come only from
cache file names that match the store's symbol pattern.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

from semiedge.data.schema import Adjustment, DataFeed
from semiedge.data.store import ParquetBarStore

UP = "#35b779"  # same up/down pair as the desk; each candle is also labelled in its tooltip
DOWN = "#e5484d"
SMA_FAST = ("SMA 20", "#3987e5")
SMA_SLOW = ("SMA 50", "#d9a21b")
RANGES = {"3 months": 63, "6 months": 126, "1 year": 252}
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")

# The configured cache (config/app.yaml): consolidated tape, split-adjusted daily bars.
DAILY_FEED = DataFeed.SIP
DAILY_ADJUSTMENT = Adjustment.SPLIT


def cached_symbols(root: Path) -> list[str]:
    """Symbols with a cached daily series, from file names only; anything odd is ignored."""
    folder = (
        root
        / "raw"
        / "bars"
        / "daily"
        / f"feed={DAILY_FEED.value}"
        / f"adjustment={DAILY_ADJUSTMENT.value}"
    )
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.parquet") if _SYMBOL.fullmatch(p.stem))


def load_daily(root: Path, symbol: str) -> pd.DataFrame | None:
    """The cached daily bars for one symbol, or ``None`` if there are none."""
    if not _SYMBOL.fullmatch(symbol):
        raise ValueError("invalid symbol")
    series = ParquetBarStore(root).read(symbol, feed=DAILY_FEED, adjustment=DAILY_ADJUSTMENT)
    return None if series is None else series.frame


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def candle_rows(frame: pd.DataFrame, sessions: int) -> list[dict[str, Any]]:
    """The last ``sessions`` bars as chart rows; averages use the full history before slicing."""
    closes = frame["close"].astype(float)
    sma_fast = closes.rolling(20, min_periods=20).mean()
    sma_slow = closes.rolling(50, min_periods=50).mean()
    rows = []
    for ts in frame.index[-sessions:]:
        bar = frame.loc[ts]
        values = [_finite(bar[k]) for k in ("open", "high", "low", "close", "volume")]
        if any(x is None for x in values):
            continue  # a malformed bar is left out, never repaired
        o, h, lo, c, v = (float(x) for x in values if x is not None)
        rows.append(
            {
                "session": pd.Timestamp(ts).date().isoformat(),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": v,
                "direction": "up" if c >= o else "down",
                "sma20": _finite(sma_fast.loc[ts]),
                "sma50": _finite(sma_slow.loc[ts]),
            }
        )
    return rows


def candle_spec(rows: list[dict[str, Any]], *, title: str) -> dict[str, Any] | None:
    """Daily candles with 20/50-day averages over a volume panel; ``None`` with no rows."""
    if not rows:
        return None
    x = {
        "field": "session",
        "type": "ordinal",
        "sort": None,
        "title": None,
        "axis": {
            "labelAngle": 0,
            "labelOverlap": True,
            "labelExpr": "timeFormat(timeParse(datum.value, '%Y-%m-%d'), '%b %d')",
        },
    }
    color = {
        "field": "direction",
        "type": "nominal",
        "scale": {"domain": ["up", "down"], "range": [UP, DOWN]},
        "legend": None,
    }
    tooltip = [
        {"field": "session", "type": "ordinal", "title": "Session"},
        {"field": "open", "type": "quantitative", "format": ",.2f", "title": "Open"},
        {"field": "high", "type": "quantitative", "format": ",.2f", "title": "High"},
        {"field": "low", "type": "quantitative", "format": ",.2f", "title": "Low"},
        {"field": "close", "type": "quantitative", "format": ",.2f", "title": "Close"},
        {"field": "volume", "type": "quantitative", "format": ",.0f", "title": "Volume"},
        {"field": "direction", "type": "nominal", "title": "Candle"},
    ]
    price_y = {"type": "quantitative", "scale": {"zero": False}, "title": None}
    lines = [
        {
            "mark": {"type": "line", "strokeWidth": 1.5, "color": hue},
            "encoding": {"x": x, "y": {"field": field, **price_y}},
        }
        for field, (_, hue) in (("sma20", SMA_FAST), ("sma50", SMA_SLOW))
    ]
    price = {
        "height": 320,
        "layer": [
            {
                "mark": {"type": "rule"},
                "encoding": {
                    "x": x,
                    "y": {"field": "low", **price_y},
                    "y2": {"field": "high"},
                    "color": color,
                },
            },
            {
                "mark": {"type": "bar", "size": 5},
                "encoding": {
                    "x": x,
                    "y": {"field": "open", **price_y},
                    "y2": {"field": "close"},
                    "color": color,
                    "tooltip": tooltip,
                },
            },
            *lines,
        ],
    }
    volume = {
        "height": 90,
        "mark": {"type": "bar", "size": 5, "opacity": 0.7},
        "encoding": {
            "x": {**x, "axis": None},
            "y": {
                "field": "volume",
                "type": "quantitative",
                "title": None,
                "axis": {"format": "~s"},
            },
            "color": color,
            "tooltip": tooltip,
        },
    }
    return {
        "title": {"text": title, "anchor": "start", "color": "#e8ecf3"},
        "data": {"values": rows},
        "vconcat": [price, volume],
        "spacing": 6,
        "config": {
            "background": "transparent",
            "view": {"stroke": None},
            "axis": {"gridColor": "#1f2838", "labelColor": "#94a0b4", "domainColor": "#1f2838"},
        },
    }


def summary(rows: list[dict[str, Any]]) -> dict[str, str]:
    """A one-line read of the latest bar for the caption."""
    if not rows:
        return {}
    last = rows[-1]
    prev = rows[-2]["close"] if len(rows) > 1 else None
    change = "" if prev is None else f" ({(last['close'] / prev - 1):+.2%} on the day)"
    return {
        "last": f"Close {last['close']:,.2f} on {last['session']}{change}",
        "averages": (
            "Blue line: SMA 20 "
            + ("n/a" if last["sma20"] is None else f"{last['sma20']:,.2f}")
            + " · amber line: SMA 50 "
            + ("n/a" if last["sma50"] is None else f"{last['sma50']:,.2f}")
        ),
    }


# ------------------------------------------------------------------ 5-minute bars (#121)

NY = "America/New_York"
VWAP_HUE = "#9085e9"  # dataviz reference violet (dark step): distinct from candles and averages
LEVEL_HUE = "#94a0b4"


def cached_intraday_symbols(root: Path) -> list[str]:
    from semiedge.data.intraday import IntradayBarStore  # noqa: PLC0415 - keeps daily use light

    return IntradayBarStore(root).symbols(feed=DAILY_FEED, adjustment=DAILY_ADJUSTMENT)


def load_intraday(root: Path, symbol: str) -> pd.DataFrame | None:
    from semiedge.data.intraday import IntradayBarStore  # noqa: PLC0415

    if not _SYMBOL.fullmatch(symbol):
        raise ValueError("invalid symbol")
    series = IntradayBarStore(root).read(symbol, feed=DAILY_FEED, adjustment=DAILY_ADJUSTMENT)
    return None if series is None else series.frame


def intraday_sessions(frame: pd.DataFrame) -> list[str]:
    """Sessions present in a 5-minute frame, newest first, as ISO dates (New York)."""
    days = sorted({ts.tz_convert(NY).date() for ts in frame.index}, reverse=True)
    return [d.isoformat() for d in days]


def intraday_rows(frame: pd.DataFrame, session: str) -> list[dict[str, Any]]:
    """One session's 5-minute bars with the running VWAP, labelled in New York time."""
    local = pd.DatetimeIndex(frame.index).tz_convert(NY)
    mask = [d.isoformat() == session for d in local.date]
    day = frame.loc[mask]
    rows: list[dict[str, Any]] = []
    cum_pv = cum_v = 0.0
    for stamp, (_, bar) in zip(local[mask], day.iterrows(), strict=True):
        values = [_finite(bar[k]) for k in ("open", "high", "low", "close", "volume")]
        if any(x is None for x in values):
            continue
        o, h, lo, c, v = (float(x) for x in values if x is not None)
        cum_pv += (h + lo + c) / 3 * v
        cum_v += v
        rows.append(
            {
                "time": stamp.strftime("%H:%M"),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": v,
                "direction": "up" if c >= o else "down",
                "vwap": cum_pv / cum_v if cum_v > 0 else None,
            }
        )
    return rows


def session_levels(rows: list[dict[str, Any]], prior_high: float | None) -> dict[str, float | None]:
    """The strategy's reference levels for one session (spec ``intraday_v1``).

    The opening range is the first three 5-minute bars (09:30-09:45) and is only defined when
    all three exist; the prior-day high comes from the daily cache.
    """
    first = rows[:3]
    complete = [r["time"] for r in first] == ["09:30", "09:35", "09:40"]
    return {
        "OR high": max(r["high"] for r in first) if complete else None,
        "OR low": min(r["low"] for r in first) if complete else None,
        "Prior-day high": prior_high,
    }


def prior_day_high(daily: pd.DataFrame | None, session: str) -> float | None:
    """The daily high of the last cached session before ``session``."""
    if daily is None:
        return None
    earlier = daily.loc[[pd.Timestamp(ts).date().isoformat() < session for ts in daily.index]]
    if earlier.empty:
        return None
    return _finite(earlier["high"].iloc[-1])


def intraday_spec(
    rows: list[dict[str, Any]], *, title: str, levels: dict[str, float | None]
) -> dict[str, Any] | None:
    """5-minute candles with VWAP and dashed reference levels over a volume panel."""
    if not rows:
        return None
    x = {
        "field": "time",
        "type": "ordinal",
        "sort": None,
        "title": None,
        "axis": {"labelAngle": 0, "labelOverlap": True},
    }
    color = {
        "field": "direction",
        "type": "nominal",
        "scale": {"domain": ["up", "down"], "range": [UP, DOWN]},
        "legend": None,
    }
    tooltip = [
        {"field": "time", "type": "ordinal", "title": "Bar (New York)"},
        {"field": "open", "type": "quantitative", "format": ",.2f", "title": "Open"},
        {"field": "high", "type": "quantitative", "format": ",.2f", "title": "High"},
        {"field": "low", "type": "quantitative", "format": ",.2f", "title": "Low"},
        {"field": "close", "type": "quantitative", "format": ",.2f", "title": "Close"},
        {"field": "vwap", "type": "quantitative", "format": ",.2f", "title": "VWAP"},
        {"field": "volume", "type": "quantitative", "format": ",.0f", "title": "Volume"},
    ]
    price_y = {"type": "quantitative", "scale": {"zero": False}, "title": None}
    level_rows = [{"level": k, "price": v} for k, v in levels.items() if v is not None]
    layers: list[dict[str, Any]] = [
        {
            "mark": {"type": "rule"},
            "encoding": {
                "x": x,
                "y": {"field": "low", **price_y},
                "y2": {"field": "high"},
                "color": color,
            },
        },
        {
            "mark": {"type": "bar", "size": 4},
            "encoding": {
                "x": x,
                "y": {"field": "open", **price_y},
                "y2": {"field": "close"},
                "color": color,
                "tooltip": tooltip,
            },
        },
        {
            "mark": {"type": "line", "strokeWidth": 1.5, "color": VWAP_HUE},
            "encoding": {"x": x, "y": {"field": "vwap", **price_y}},
        },
    ]
    if level_rows:
        layers += [
            {
                "data": {"values": level_rows},
                "mark": {"type": "rule", "strokeDash": [4, 4], "color": LEVEL_HUE},
                "encoding": {"y": {"field": "price", **price_y}},
            },
            {
                "data": {"values": level_rows},
                "mark": {
                    "type": "text",
                    "align": "left",
                    "dx": 4,
                    "dy": -6,
                    "color": LEVEL_HUE,
                    "fontSize": 11,
                },
                "encoding": {
                    "y": {"field": "price", **price_y},
                    "x": {"value": 0},
                    "text": {"field": "level"},
                },
            },
        ]
    return {
        "title": {"text": title, "anchor": "start", "color": "#e8ecf3"},
        "data": {"values": rows},
        "vconcat": [
            {"height": 320, "layer": layers},
            {
                "height": 80,
                "mark": {"type": "bar", "size": 4, "opacity": 0.7},
                "encoding": {
                    "x": {**x, "axis": None},
                    "y": {
                        "field": "volume",
                        "type": "quantitative",
                        "title": None,
                        "axis": {"format": "~s"},
                    },
                    "color": color,
                    "tooltip": tooltip,
                },
            },
        ],
        "spacing": 6,
        "config": {
            "background": "transparent",
            "view": {"stroke": None},
            "axis": {"gridColor": "#1f2838", "labelColor": "#94a0b4", "domainColor": "#1f2838"},
        },
    }
