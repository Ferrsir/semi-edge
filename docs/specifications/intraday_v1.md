# Specification: `intraday_v1`

Status: **Specified, not implemented (issue #111).** It replaces `momentum_v1` as SEMI-EDGE's
strategy (owner decision 2026-11-14, ADR-010). Every rule and number below was fixed before any
intraday data was looked at (ground rule 3). Changing one after results are seen needs a new
version of this spec, with its rationale, written before the new results are read.

Long-only day trading on 5-minute bars. Three setups run through the session, so the system can
take several trades a day, and every position is closed before the close of the session it was
opened in. The setups follow the methods of the owner's research pack (`day-trader-skill`, MIT):
its opening-range breakout and its level breakout, plus a VWAP pullback for the middle of the
day. They use SEMI-EDGE's own Alpaca data, never that pack's Yahoo data (ground rule 6).

## Hypothesis (to be tested, not assumed)

The semiconductor and AI-infrastructure stocks in the universe are among the most liquid and most
volatile US names. When one of them clears a well-watched intraday level (the opening range, the
prior day's high) on unusually heavy volume, or pulls back to VWAP and holds while it is trending,
and the sector ETF (SMH) is trading above its own VWAP, the move continues far enough on average
to beat a structural stop plus trading costs. **There is no evidence for this yet.** Each setup is
reported separately, and the tests in "What would falsify it" exist to try to reject each one.

## Data

- Bars: 5-minute OHLCV, SIP feed (`data.daily_feed` stays `sip`; IEX volume is not comparable),
  regular session 09:30–16:00 America/New_York only. A bar is labelled by its start time and is
  complete when its end time plus `bar_settle_seconds` (10) has passed.
- Daily bars (already cached) supply the prior day's high and ATR. Both bar sizes use the same
  adjustment (`adjustment: split`), so `PDH` and today's 5-minute prices are on one basis; a split
  on session `d` without a matching adjusted series makes the symbol ineligible that day.
- A missing 5-minute bar for a symbol, before or at the decision bar, makes that symbol
  ineligible for the rest of the day (fail closed, reason `intraday_gap`). A missing SMH bar makes
  every symbol ineligible. On early-close sessions the session has fewer bars; every window below
  that would end after `close − 30 min` ends at `close − 30 min`, and the flat-by time moves to
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
| `SV_k` | slot volume: mean of `V(b_k)` over the prior 20 sessions (the same bar of the day) |
| `PDH` | prior session's daily high |
| `ATR_d1` | Wilder ATR(14) of daily bars up to and including session `d−1` (as in `momentum_v1`) |
| `VWAP_k` | `Σ(TP·V) / ΣV` over `b_1 … b_k`, with `TP = (H + L + C) / 3`; resets every session |
| `HOD_k` | highest high of `b_1 … b_k` |

## Conditions shared by every setup

At the decision bar `b_k` all of these must hold, otherwise no setup fires for that symbol on
that bar:

1. **Market filter:** `C_SMH(b_k) > VWAP_SMH_k`.
2. **Earnings:** R16 (`earnings_blackout_sessions`, verified earnings data) allows the symbol today.
3. **History:** 20 prior sessions of complete 5-minute data and 15 daily bars exist.
4. **Role:** `role: tradable` in the universe.
5. **One position per symbol:** the symbol has no open position or pending entry.

Comparisons are strict where written `>` and inclusive where written `≥`. An undefined input makes
its condition fail, never pass.

## Setup A: opening-range breakout (morning)

Decision bars `b_3 … b_24` (the signal bar ends by 11:30).
- Eligible for the day (decided at 09:45): `ORV / ORV_avg ≥ 1.5` and
  `0.25 · ATR_d1 ≤ ORW ≤ 1.0 · ATR_d1`.
- Trigger: `C(b_k) > ORH` and `C(b_k) > VWAP_k`.
- Stop: `ORL`.

## Setup B: prior-day-high breakout (all day)

Decision bars `b_3 … b_72` (the signal bar ends by 15:30).
- The session must open at or below the level: `O(b_1) ≤ PDH` (a gap above it is not a break).
- Trigger: `C(b_k) > PDH` and `C(b_{k−1}) ≤ PDH` (this bar is the break), `V(b_k) ≥ 1.5 · SV_k`,
  and `C(b_k) > VWAP_k`.
- Stop: the lowest low of `b_{k−2} … b_k`.

## Setup C: VWAP pullback in a trend (midday)

Decision bars `b_12 … b_66` (the signal bar ends between 10:30 and 15:00).
- Trend: `C(b_j) > VWAP_j` for every `j` in `k−6 … k−1` (the prior 30 minutes held above VWAP),
  and the latest bar whose high equals `HOD_{k−1}` is one of `b_{max(1,k−12)} … b_{k−1}` (a new
  high within the last hour).
- Pullback that holds: `L(b_k) ≤ VWAP_k · 1.002`, `C(b_k) > VWAP_k` and `C(b_k) > O(b_k)`.
- Stop: `L(b_k)`.

## Entries, exits and how many trades

- **Entry:** a market order at the open of `b_{k+1}`; in backtests that open is the fill price,
  plus slippage. With `R = entry − stop`, the entry is cancelled (a backtest skips it, live it is
  never sent) if `R ≤ 0` or `R < 0.0005 · entry` (a stop that close cannot survive the spread).
- **One trigger per setup per symbol per day.** A symbol can therefore trade up to three times in
  a day, once per setup, never with two positions open at once. A stop-out does not re-arm the
  setup that lost.
- **Target:** `entry + 2 · R`, a limit order in the same bracket as the stop. Stop and target are
  live from the entry bar itself (after its open). If one bar touches both, the backtest assumes
  the stop was hit first (conservative).
- **Stop fills:** in a backtest, a bar whose low reaches the stop fills at the stop minus slippage,
  or at that bar's open if it opened below the stop. Live, the stop and target go in as a bracket
  with the entry, as the order lifecycle (T9) already requires.
- **Flat by 15:55:** anything still open is sold at the open of the 15:55 bar (market order), plus
  slippage. No position is ever carried overnight.
- **Same-bar conflicts:** when more triggers arrive on one bar than slots are free, setups rank
  A, then B, then C; within a setup, the higher `V(b_k) / SV_k` goes first; ties by symbol.

## Sizing and limits

`shares = min( floor(equity · max_risk_per_trade_pct / R), floor(equity · max_position_pct / entry) )`.
The existing risk manager (T8) enforces `max_positions` (4), `max_gross_exposure_pct` (20%
including pending orders) and its halts, including `daily_loss_halt_pct` (no new entries after a
1% loss on the day) and `drawdown_halt_pct` (5%). The ceilings in `config/risk.yaml` stay as they
are (owner, 2026-11-14).

## Costs

Slippage 10 bps against the trade on every fill (`config/backtest.yaml`), no commissions.
Results must also be reported at 20 and 30 bps, because intraday edges are often smaller than the
spread on a fast move.

## No-look-ahead rules

1. A decision at bar `b_k` sees only bars complete by the end of `b_k`, plus daily bars up to
   `d−1`.
2. `ORV_avg` and `SV_k` exclude the current session. `PDH` and `ATR_d1` come from `d−1`.
3. Fills happen at the open of the bar after the decision, never at the decision bar's close.
4. A truncation test must show that recomputing every signal on data cut at the end of `b_k`
   gives the same answer at `b_k`.

## What would falsify it

Over at least two years of 5-minute data, split into walk-forward windows (T7), for each setup
on its own and for all three together:
- net expectancy per trade at 10 bps is not clearly above zero, or turns negative at 20 bps;
- the result depends on a handful of days (removing the best 5 sessions removes the edge);
- out-of-sample windows do not resemble in-sample windows;
- small changes to `1.5`, `0.25`, `1.0`, `1.002`, `0.0005` or `2` (the sensitivity grid in T7)
  flip the sign.

A setup that fails is dropped, however good the headline number looks. If all three fail, the
strategy is not used.

## Pattern day trader rule

Paper trading is not affected. A real US margin account under $25,000 is limited to three day
trades in five business days. That matters only if SEMI-EDGE is ever pointed at a real account,
which ground rule 1 currently forbids.

## Implementation plan (separate tasks; none in this spec)

1. Intraday data: fetch, validate and cache 5-minute SIP bars (adds to T1's store).
2. Intraday backtest: bar-by-bar engine with the fill rules above (extends T5/T6), reporting each
   setup separately.
3. Walk-forward and the sensitivity grid (T7, redirected to this strategy).
4. Live loop: a scheduler that runs on 5-minute bar closes, places brackets and flattens at 15:55
   (T12, with T9–T11).
5. Dashboard: intraday signals and positions on the desk, plus 5-minute candle charts (#108).
