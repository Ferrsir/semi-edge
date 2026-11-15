# `intraday_dip_v1` results

Spec: `docs/specifications/intraday_dip_v1.md`. Alpaca SIP 5-minute cache (2024-11-13..2026-11-13)
and daily cache fetched 2026-11-14. Labels: survivorship-biased universe, no earnings blackout,
fills at the next bar's open plus slippage, flat at 15:55.

## Development 2024-12-16..2025-12-31 (engine `42e72ad`): FAIL

`semiedge backtest-intraday-dip --period development --equity 5000 --risk-profile 5k`

| signal | bps | trades | win % | mean R | 95% CI | median R | mean R w/o top 5 |
|---|---|---|---|---|---|---|---|
| V1 (VWAP stretch) | 20 | 861 | 25.1 | -0.692 | -0.787..-0.603 | -1.270 | -0.724 |
| V2 (relative to SMH) | 20 | 1461 | 28.3 | -0.786 | -0.858..-0.716 | -1.283 | -0.806 |
| baseline (any reversal under VWAP) | 20 | 6448 | 30.1 | -0.881 | -0.916..-0.847 | -1.335 | -0.886 |

Both variants are negative at 10, 20 and 30 bps. **Selection: none qualifies; the strategy
fails, and the 2026 holdout is not run** (it stays unused).

$5,000 portfolio: all 18 runs (2 variants × 3 accounts × 3 slippages) lose about 5% and stop at
the 5% drawdown halt, between 2025-01-15 and 2025-12-04.

### Diagnostic (does not change the verdict)

The same trades at lower costs, computed with the same engine:

| signal | 0 bps mean R (95% CI) | 3 bps mean R |
|---|---|---|
| V1 | -0.057 (-0.151..+0.035) | -0.152 |
| V2 | +0.011 (-0.057..+0.082) | -0.108 |
| baseline | +0.042 (+0.011..+0.073) | -0.097 |

The median planned risk is 0.73% of the price. At 0 bps, 487 of V1's 861 trades hit the
day's-low stop, and only 87 reached VWAP. Even without costs there is no edge. A larger stretch
below VWAP did worse than an ordinary reversal bar, so in this data intraday weakness in these
stocks tended to continue rather than snap back.
