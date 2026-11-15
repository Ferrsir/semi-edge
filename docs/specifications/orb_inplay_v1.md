# Specification: `orb_inplay_v1`

Status: **Specified, not implemented (issue #145).** A same-day candidate for SEMI-EDGE's main
strategy (ADR-013). Every rule, number, period and pass test below is fixed before any
`orb_inplay_v1` outcome is computed (ground rule 3).

## Source and hypothesis

This follows Zarattini, Barbon and Aziz (2024), *A Profitable Day Trading Strategy for the U.S.
Equity Market* (SSRN 4729284): a 5-minute opening-range breakout traded only in **stocks in
play**, meaning names whose first 5-minute volume is far above normal. It trades in the direction
of the first candle, with a stop at 10% of the daily ATR, and holds to the close. Their reading:
unusual opening volume marks news or institutional demand, and that kind of opening move tends to
continue through the day. That fits what SEMI-EDGE's own failed tests showed: intraday weakness in
these semis continued rather than reverting (`intraday_dip_v1`), and confirmed breaks beat blind
entries (`gap_reversal_v1`). SEMI-EDGE is long-only, so only green first candles are traded.
**Whether it works on 16 semis, without the paper's leverage, is what this tests.**

## Current-regime periods (owner decision 2026-11-14: test the current market regime only)

- **Development:** 2026-01-02 to 2026-06-30. Selection uses this period only.
- **Holdout:** 2026-07-01 to 2026-10-23. Run once, after the selection is recorded in a commit.
- **Seen window, 2026-10-26 to 2026-11-13:** inspected for `gap_reversal_v1`, including NVDA's
  first-30-minute ranges. Excluded from both periods; it may be shown afterwards, labelled as seen.
- Disclosure: `intraday_v1` (a 15-minute opening-range breakout with a 2R target and no volume
  filter) was run on 2024-12..2026-11; `intraday_dip_v1` and `gap_reversal_v1` were run on
  2024-12..2025 only. No 5-minute opening-range or relative-volume statistic has been computed.

## Notation (session s; bar k = 0 is 09:30–09:35)

- `RVOL = V_0 / mean(V_0 over the prior 14 sessions)`. At least 10 of those 14 sessions must
  have bar 0.
- `ATR_d1`: daily Wilder ATR(14) up to the previous session.

## Signals (decided at the close of bar 0; one trade per symbol per session)

Shared conditions:
- bar 0 is present;
- the first candle is green (`C_0 > O_0`);
- `ATR_d1` and `RVOL` are defined.

Two pre-registered variants; exactly one is carried forward:
- **O1, in play with the paper's stop:** `RVOL >= 1.0`; `stop = C_0 - 0.10 * ATR_d1`.
- **O2, in play with the candle's stop:** `RVOL >= 1.0`; `stop = L_0` (the first candle's low).

**Baseline:** any green first candle (no `RVOL` condition), with O1's stop. It tests whether the
in-play filter adds anything.

There is no profit target: the trade is held to the flatten. Planned risk is `R0 = C_0 - stop`.
A signal is skipped if `R0 < 0.0005 * C_0`. Ranking: highest `RVOL` first, then symbol.

## Entries, exits, sizing, accounts

As in `intraday_dip_v1`:
- buy at the open of bar 1 plus slippage, cancelled if that open is at or below the stop;
- the stop is live from the entry bar, with a gap through it filled at the open;
- flat at the open of the last bar;
- the `5k` profile (1% risk, 25% position cap, 3 positions, 75% gross), with the 1% daily and
  5% drawdown halts;
- the `unrestricted`, `margin_pdt` and `cash` account models.

## Costs

Slippage of **5, 10 and 20 bps** on each side, **decided at 10 bps**.

Rationale, written down before any result: these 16 stocks trade with spreads of a few basis
points, Alpaca charges no commission, and a $1,250 order has no meaningful market impact, so 10
bps a side is still several times the expected cost. The previous strategies were judged at
20 bps. This spec moves the decision level for this strategy only, and says so. A result that
holds only at 5 bps does not pass.

## Gates

**Selection (development, 10 bps, signal study):** carry forward the variant with the higher mean
R among those with:
- at least 60 trades;
- a mean R above 0;
- a mean R above the baseline's;
- a mean R without its top five trades that is also above 0.

If neither qualifies, the strategy fails and the holdout is not run.

**Pass (holdout, one run, 10 bps):**
- at least 40 trades;
- a mean R above 0, above the baseline's, and above 0 without the top five trades;
- a positive net result on the $5,000 unrestricted run.

## What $5,000 can earn under these limits (stated before any result)

With a stop of about 0.1 ATR (roughly 0.4% of the price), the 1% risk budget would buy far more
than the 25% position cap allows. So each trade risks about $5, not $50, and at most $3,750 is
invested at once. To make $200–300 a day, that $3,750 would have to gain 5–8% every day. The
report gives dollars per day as measured; the risk limits are owner-controlled (rule 2) and are
not changed here.

## Labels

Survivorship-biased universe; no earnings blackout (R16 applies live); the decision bar's close
is the 09:35 close, and the fill is the 09:35 bar-1 open plus slippage.
