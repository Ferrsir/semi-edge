# Specification: `closing_momentum_v2`

Status: **Specified, not implemented (issue #169).** It succeeds Codex's `closing_momentum_v1`
(`docs/specifications/closing_momentum_v1.md` on PR #150, issue #149). It keeps v1's hypothesis,
signal and variants, and applies the two Claude review findings on #150 **before any result**:

- F-181: v1's one-bar stop made a 1R move smaller than the round-trip cost. Here the stop is
  sized from the daily ATR, with no target, held to 15:55.
- F-182: periods follow the owner's current-regime decision (ADR-015), with the decision at 10 bps
  as in `orb_inplay_v1`.

Every rule, number, period and gate below is fixed before any `closing_momentum` outcome is
computed (ground rule 3).

## Research rationale (hypothesis, not evidence)

Gao, Han, Li and Zhou (*Intraday Momentum: The First Half-Hour Return Predicts the Last Half-Hour
Return*, SSRN 2552752) report that the S&P 500 ETF's return from the previous close to 10:00
predicted its last half-hour return in 1993–2013, and saw similar behavior in other liquid ETFs.
This is ETF evidence; whether it carries over to individual semiconductor stocks, in 2026, after
costs, is what this tests. SEMI-EDGE is long-only, so only a positive first half-hour is traded.

## Data and disclosure

- Alpaca SIP 5-minute bars (split-adjusted, regular hours) for the 16 tradable symbols and SMH,
  plus daily bars for the previous close and the previous session's Wilder ATR(14). A zero-volume
  bar counts as missing.
- Full 78-bar sessions only; early closes have no 15:30–16:00 window and are skipped.
- Disclosure: four same-day strategies were tested before this one (`intraday_v1`,
  `intraday_dip_v1`, `gap_reversal_v1`, `orb_inplay_v1`). Their periods overlap this one, and
  `orb_inplay_v1` used the same 2026 development window. A descriptive statistic was also taken
  on the daily cache on 2026-11-14: in 2026 the 16 stocks' mean open-to-close move was -1.3 bps
  and close-to-open +12.5 bps. No first-half-hour or last-half-hour statistic has been computed.
  Because several strategies have now been tried on the same bars, a pass here is weaker evidence
  than a single pre-planned test. That is stated in every report.

## Periods (fixed now)

- **Development:** 2026-01-02 to 2026-06-30. Selection uses this period only.
- **Holdout:** 2026-07-01 to 2026-10-23. Run once, after the selection is recorded in a commit.
- **Seen window:** 2026-10-26 to 2026-11-13; excluded.

## Notation

- `PC`: the previous session's daily close.
- `ATR_d1`: daily Wilder ATR(14) up to the previous session.
- `C5`: close of bar `k = 5` (09:55–10:00).
- `r = C5 / PC - 1` for the stock; `r_smh` the same for SMH.
- `k = 71`: the 15:25–15:30 bar; `k = 72`: the 15:30 bar; `k = 77`: the 15:55 bar.

## Signal (decided at the close of bar 71; at most one trade per symbol per session)

Shared conditions:
- a full 78-bar session;
- every bar of the symbol present from 0 to 71;
- SMH bars 0 to 5 present;
- `ATR_d1` and `PC` defined.

Two pre-registered variants; exactly one is carried forward:
- **M1, market up:** `r > 0` and `r_smh > 0`.
- **M2, relative strength:** `r > 0`, `r_smh > 0` and `r > r_smh`.

**Baseline:** every symbol-session meeting the shared conditions, with no return condition.

At the signal: `stop = C71 - 0.25 * ATR_d1`, no target, and planned risk `R0 = 0.25 * ATR_d1`.
Ranking: M1 by the largest `r`, M2 by the largest `r - r_smh`, the baseline by the largest `r`.
Ties go to the alphabetically first symbol.

## Entries, exits, sizing, accounts, costs

As in `intraday_dip_v1` and `orb_inplay_v1`:
- buy at the 15:30 open (bar 72) plus slippage, cancelled if that open is at or below the stop;
- the stop is live from the entry bar, and a gap through it fills at the open;
- flat at the 15:55 open;
- the `5k` profile with the actual-fill risk cap (#158), the 1% daily and 5% drawdown halts, and
  the three account models;
- slippage of 5, 10 and 20 bps on each side, **decided at 10 bps**.

## Gates

**Selection (development, 10 bps, signal study):** the variant with the higher mean R among those
with:
- at least 150 trades;
- a mean R above 0;
- a mean R above the baseline's;
- a mean R without its top five trades that is also above 0.

None qualifies → the strategy fails and the holdout is not run.

**Pass (holdout, one run, 10 bps):**
- at least 80 trades;
- a mean R above 0, above the baseline's, and above 0 without its top five trades;
- a positive net result on the $5,000 unrestricted run.

## Labels

Survivorship-biased universe; no earnings blackout (R16 applies live); several strategies tested
on the same bars (above).
