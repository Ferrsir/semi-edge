# Specification: `gap_reversal_v1`

Status: **Specified, not implemented (issue #141).** It's a same-day strategy for the owner's
main goal: buy a semiconductor stock that is low, and sell it later the same day once it comes
back (ADR-013). Every rule, number, period and pass test below was fixed before any
`gap_reversal_v1` outcome was computed (ground rule 3).

**Where the idea came from, disclosed in full:** the owner pointed at NVDA on 2026-11-12. It
closed at $173.73, opened at $151.00 (-13%) after Jensen Huang's resignation was announced,
reached $163.10 within 30 minutes and closed at $166.19. Daily and 5-minute bars of all 16
symbols and SMH from 2026-10-26 to 2026-11-13 were looked at to understand it. That window is
**excluded from both test periods** below. Gap-day **counts** (not outcomes) were also taken
to set the minimum trade counts: 100 gaps of at least 1 ATR in development and 78 in the
holdout, across the 16 symbols.

## Hypothesis (to be tested, not assumed)

When a liquid large-cap semi opens far below the previous close on news, the first trades are
often forced or emotional selling. If buyers then take the price back above where it has
traded on average since the open (VWAP), or above the high of the first 15 minutes, a good part
of the gap tends to fill the same day. A confirmed reclaim should beat buying the gap blindly at
the open. **There is no evidence for this yet.**

## Data

Alpaca SIP 5-minute bars (split-adjusted, regular hours) for the 16 tradable symbols; daily bars
for the previous close and the previous session's Wilder ATR(14). A bar with zero volume counts as
missing; no decision is made after a symbol's first missing bar in a session.

## Periods (fixed now)

- **Development:** 2024-12-16 to 2025-12-31. Selection uses this period only.
- **Holdout:** 2026-01-02 to 2026-10-23. Run once with the selected variant, after the selection is
  recorded in a commit.
- **Seen window, 2026-10-26 to 2026-11-13:** never used for selection or for the pass test. It
  may be shown afterwards as an illustration, labelled as seen.

## Notation (session s; bar k = 0 is 09:30)

- `PC`: the previous session's daily close.
- `ATR_d1`: daily Wilder ATR(14) up to the previous session.
- `VWAP_k`, `LOD_k`: as in `intraday_dip_v1`.
- `ORH`: the highest high of bars 0..2 (09:30–09:45).
- **Gap day:** `O_0 <= PC - 1.0 * ATR_d1`.
- **Half-gap target:** `T = O_0 + 0.5 * (PC - O_0)`.

## Signals (one trade per symbol per session, the first qualifying bar only)

All need a gap day and every bar of the symbol present from 0 to k. At the signal, `stop = LOD_k`,
`target = T`, and planned risk `R0 = C_k - LOD_k`. The signal is skipped if `C_k >= T` (the gap is
already half filled) or if `R0 < 0.0005 * C_k`.

Two pre-registered variants; exactly one is carried forward:
- **G1, VWAP reclaim:** the first bar `k` in 1..11 (closes from 09:40 to 10:30) with `C_k > VWAP_k`.
- **G2, opening-range breakout:** the first bar `k` in 3..23 (closes from 09:50 to 11:30) with
  `C_k > ORH`.

**Baseline:** buy the gap blindly. The signal is at the close of bar 0, with `stop = L_0` and the
same target, so it tests whether waiting for confirmation adds anything.

## Entries, exits, sizing, accounts and costs

Exactly as in `intraday_dip_v1`:
- buy at the next bar's open plus slippage, cancelled if the open is at or through the stop or
  the target;
- gap exits at the open, then the stop (which wins a tie), then the target;
- flat at the open of the last bar;
- the `5k` profile, at most 3 positions, the 1% daily and 5% drawdown halts;
- the `unrestricted`, `margin_pdt` and `cash` account models;
- 10, 20 and 30 bps of slippage on each side.

Ranking when signals exceed free slots: the largest gap in ATRs first, then symbol.

## Runs and gates

Signal study and portfolio runs, as in `intraday_dip_v1`, with R measured against the planned
`R0`.

**Selection (development, 20 bps):** carry forward the variant with the higher mean R among
those with:
- at least 40 trades;
- a mean R above 0;
- a mean R above the baseline's;
- a mean R without its top five trades that is also above 0.

If neither qualifies, the strategy fails and the holdout is not run.

**Pass (holdout, one run, 20 bps):**
- at least 25 trades;
- a mean R above 0, above the baseline's, and above 0 without the top five trades;
- a positive net result on the $5,000 unrestricted run.

The samples are small (dozens of trades), and every report says so.

## Labels

Survivorship-biased universe. No earnings blackout: many large gaps are earnings reactions. In
live and paper trading, R16 blocks entries around verified earnings dates, so the backtest
includes trades that live trading would skip. Fills are at the next bar's open plus slippage.

## Implementation plan

1. `backtest/gap_reversal.py`, reusing `intraday_dip`'s study and replay, and the CLI
   `backtest-gap-reversal`, committed before any outcome is computed.
2. Development run, then a selection commit, then one holdout run.
