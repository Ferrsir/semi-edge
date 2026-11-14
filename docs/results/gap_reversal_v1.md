# `gap_reversal_v1` results

Spec: `docs/specifications/gap_reversal_v1.md`. Alpaca SIP 5-minute and daily caches fetched
2026-11-14. The inspected window 2026-10-26..11-13 is outside both periods.

## Development 2024-12-16..2025-12-31 (engine `e766111`): FAIL

`semiedge backtest-gap-reversal --period development --equity 5000 --risk-profile 5k`

| signal | bps | trades | win % | mean R | 95% CI | median R | w/o top 5 |
|---|---|---|---|---|---|---|---|
| G1 (VWAP reclaim) | 20 | 83 | 39.8 | -0.249 | -0.504..+0.019 | -0.789 | -0.440 |
| G2 (15-minute high) | 20 | 37 | 56.8 | -0.060 | -0.269..+0.140 | +0.084 | -0.202 |
| baseline (buy the gap at 09:35) | 20 | 98 | 28.6 | -0.608 | -1.056..+0.004 | -1.220 | -1.037 |

**Selection: none qualifies** (G1: mean R not above 0, and not above 0 without its top five
trades; G2: fewer than 40 trades, and the same two failures). The holdout is not run.
Confirmation beats buying the gap blindly, but neither confirmed variant makes money at 20 bps.

$5,000 portfolio at 20 bps: G1 -$207 (unrestricted, halted 2025-04-03), G2 -$22 (no halt).
