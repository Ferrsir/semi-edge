# Specification: `intraday_orb_v1`

Status: **Specified, not implemented (issue #111).** It replaces `momentum_v1` as SEMI-EDGE's
strategy (owner decision 2026-11-14, ADR-010). Every rule and number below was fixed before any
intraday data was looked at (ground rule 3). Changing one after results are seen needs a new
version of this spec, with its rationale, written before the new results are read.

Long-only, same-day opening-range breakout on 5-minute bars. Every position is closed before the
close of the session it was opened in.

## Hypothesis (to be tested, not assumed)

The semiconductor and AI-infrastructure stocks in the universe are among the most liquid and most
volatile US names, and a large share of their daily range and volume arrives in the first hour.
When one of them breaks above its first-15-minute range on unusually heavy opening volume, while
the sector ETF (SMH) is trading above its own intraday VWAP, the move continues far enough on
average to beat a stop at the bottom of the range plus trading costs. **There is no evidence for
this yet.** The tests in "What would falsify it" exist to try to reject it.

## Data

- Bars: 5-minute OHLCV, SIP feed (`data.daily_feed` stays `sip`; IEX volume is not comparable),
  regular session 09:30–16:00 America/New_York only. A bar is labelled by its start time and is
  complete when its end time plus `bar_settle_seconds` (10) has passed.
- Daily bars (already cached) supply the prior day's ATR and nothing else.
- A session with any missing 5-minute bar for a symbol, before or at the decision bar, makes that
  symbol ineligible for the day (fail closed, reason `intraday_gap`). Early-close sessions use the
  exchange calendar's close, and every time below that is later than `close − 5 min` moves to
  `close − 5 min`.

## Notation

For one symbol on session `d`, bars are `b_1` (09:30–09:35) … `b_78`.
`O, H, L, C, V` are a bar's open, high, low, close, volume.

| Name | Definition |
|---|---|
| `ORH`, `ORL` | max high / min low of `b_1 … b_3` (09:30–09:45) |
| `ORW` | `ORH − ORL` |
| `ORV` | `V(b_1) + V(b_2) + V(b_3)` |
| `ORV_avg` | mean of `ORV` over the prior 20 sessions; current session excluded |
| `RVOL_OR` | `ORV / ORV_avg` |
| `ATR_d1` | Wilder ATR(14) of daily bars up to and including session `d−1` (as in `momentum_v1`) |
| `VWAP_k` | `Σ(TP·V) / ΣV` over `b_1 … b_k`, with `TP = (H + L + C) / 3`; resets every session |

## Eligibility for the day (decided once, at the close of `b_3`, 09:45)

All must hold, otherwise the symbol is ineligible for the session with an explicit reason:

| # | Condition | Rule |
|---|---|---|
| 1 | Stock in play | `RVOL_OR ≥ 1.5` |
| 2 | Range is tradable | `0.25 · ATR_d1 ≤ ORW ≤ 1.0 · ATR_d1` (too narrow is noise, too wide puts the stop too far away) |
| 3 | History | 20 prior sessions of complete 5-minute data and 15 daily bars exist |
| 4 | Earnings | R16 (`earnings_blackout_sessions`, verified earnings data) allows the symbol today |
| 5 | Role | `role: tradable` in the universe |

## Entry signal (evaluated at the close of each bar `b_k`, `k = 3 … 72`)

The first bar of the day for which all of these hold triggers one entry, and that symbol is done
for the day whether the trade wins or loses:

1. `C(b_k) > ORH` (the bar *closes* above the range; an intrabar poke does not count)
2. `C(b_k) > VWAP_k` (the stock is above its own VWAP)
3. Market filter: `C_SMH(b_k) > VWAP_SMH_k` (SMH above its own VWAP on the same bar)
4. `b_k` ends at or before 15:30 (`k ≤ 72`)

Comparisons are strict where written `>` and inclusive where written `≥`. Undefined inputs make
the condition fail, never pass.

The order is a market order at the open of `b_{k+1}`. In backtests that open is the fill price,
plus slippage.

## Exits

- **Stop:** `stop = ORL`. Risk per share `R = entry − stop`. If `R ≤ 0` (the next bar gapped
  below the range), the entry is cancelled. In a backtest, a bar whose low reaches `stop` fills at
  `stop` minus slippage, or at the bar's open if it opened below `stop`. Live, this is a stop
  order placed together with the entry (bracket), as the order lifecycle (T9) already requires.
- **Target:** `target = entry + 2 · R`, a limit order in the same bracket. If one bar touches both
  stop and target, the backtest assumes the stop was hit first (conservative).
- **Flat by 15:55:** anything still open is sold at the open of the 15:55 bar (market order),
  plus slippage. No position is ever carried overnight.

## Sizing and limits

`shares = min( floor(equity · max_risk_per_trade_pct / R), floor(equity · max_position_pct / entry) )`.
The existing risk manager (T8) enforces `max_positions` (4), `max_gross_exposure_pct` (20%
including pending orders), its circuit breakers and its halts unchanged. The ceilings in
`config/risk.yaml` stay as they are (owner, 2026-11-14). When more symbols trigger on the same
bar than slots are free, the higher `RVOL_OR` goes first, ties by symbol.

## Costs

Slippage 10 bps against the trade on every fill (`config/backtest.yaml`), no commissions.
Results must also be reported at 20 and 30 bps, because intraday edges are often smaller than the
spread on a fast move.

## No-look-ahead rules

1. A decision at bar `b_k` sees only bars that are complete by the end of `b_k`, plus daily bars
   up to `d−1`.
2. `ORV_avg` excludes the current session. `ATR_d1` excludes the current session.
3. Fills happen at the open of the bar after the decision, never at the decision bar's close.
4. A truncation test must show that recomputing every signal on data cut at the end of `b_k`
   gives the same answer at `b_k`.

## What would falsify it

Over at least two years of 5-minute data, split into walk-forward windows (T7):
- net expectancy per trade at 10 bps is not clearly above zero, or turns negative at 20 bps;
- the result depends on a handful of days (for example, removing the best 5 sessions removes
  the edge);
- out-of-sample windows do not resemble in-sample windows;
- small changes to `1.5`, `0.25`, `1.0` or `2` (the sensitivity grid in T7) flip the sign.

Any of these and the strategy is not used, however good the headline number looks.

## Pattern day trader rule

Paper trading is not affected. A real US margin account under $25,000 is limited to three day
trades in five business days. That matters only if SEMI-EDGE is ever pointed at a real account,
which ground rule 1 currently forbids.

## Implementation plan (separate tasks; none in this spec)

1. Intraday data: fetch, validate and cache 5-minute SIP bars (adds to T1's store).
2. Intraday backtest: bar-by-bar engine with the fill rules above (extends T5/T6).
3. Walk-forward and the sensitivity grid (T7, redirected to this strategy).
4. Live loop: a scheduler that runs on 5-minute bar closes, places brackets and flattens at 15:55
   (T12, with T9–T11).
5. Dashboard: intraday signals and positions on the desk, plus 5-minute candle charts (#108).
