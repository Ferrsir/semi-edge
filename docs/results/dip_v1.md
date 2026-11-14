# `dip_v1` results

Spec: `docs/specifications/dip_v1.md` (revision 1.1). Every number here comes from the command
shown, run on the Alpaca SIP split-adjusted daily cache fetched 2026-11-14 04:13 UTC
(2016-01-04..2026-11-13). Labels: survivorship-biased universe, no earnings blackout, price
returns only, daily-bar stop fills.

## Development 2016-04-01..2024-12-31 (engine `75e2bf5`, revision 1.1)

`semiedge backtest-dip --period development --equity 5000 --risk-profile 5k`

Signal study (one-share trades; R against the planned 2-ATR risk):

| variant | bps | trades | win % | mean R | 95% CI |
|---|---|---|---|---|---|
| S1 (RSI2 < 10) | 10 | 865 | 58.8 | +0.046 | +0.005..+0.087 |
| S1 | 20 | 865 | 55.7 | +0.008 | -0.032..+0.049 |
| S1 | 30 | 865 | 52.1 | -0.029 | -0.070..+0.012 |
| S2 (relative drop) | 20 | 581 | 49.2 | -0.064 | -0.111..-0.021 |
| baseline (any uptrend day) | 20 | 18980 | 48.9 | -0.039 | -0.046..-0.032 |

**Selection at 20 bps (mechanical rule): S1.** S1 qualifies (at least 200 trades, mean R above 0
and above the baseline). S2 fails (mean R not above 0, and not above the baseline). The edge
over the baseline is about 0.05R per trade, and S1's own interval at 20 bps includes zero.

$5,000 portfolio, S1: every account model halts at the 5% drawdown in 2016-12 or 2017-01
(35–39 trades, net +$2 to -$127). The three account models are identical: overnight holds
are not day trades.

Void run, kept for the record: engine `76d6ed5` (revision 1.0) selected S1 on a mean R of +0.70
that came from three SMCI trades on frozen delisting bars (+363R to +440R). See revision 1.1 in
the spec.

## Holdout 2025-01-02..2026-11-13

Not run yet. This file, with the S1 selection above, is committed before the holdout run.
