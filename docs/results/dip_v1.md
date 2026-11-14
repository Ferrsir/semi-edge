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

## Holdout 2025-01-02..2026-11-13 (run once, S1, after `4743980` recorded the selection)

`semiedge backtest-dip --period holdout --variant S1 --equity 5000 --risk-profile 5k`

| run | bps | trades | win % | mean R | 95% CI |
|---|---|---|---|---|---|
| S1 signal study | 10 | 158 | 62.7 | +0.053 | -0.036..+0.142 |
| S1 signal study | 20 | 158 | 62.0 | +0.027 | -0.063..+0.116 |
| S1 signal study | 30 | 158 | 60.8 | +0.000 | -0.088..+0.089 |
| baseline | 20 | 3863 | 51.7 | -0.018 | -0.035..-0.001 |

$5,000 portfolio, S1, every account model alike: 74 trades at 20 bps, net **+$181 (+3.63%)**,
maximum drawdown -6.66%, 5% drawdown halt on 2026-10-14 (+$267 at 10 bps, +$161 at 30 bps).
SMH buy-and-hold over the same period: +99.06%.

**Pass test: PASS** (at least 60 trades; mean R above 0 and above the baseline; positive
$5,000 net). Reading: the dip condition beats buying any uptrend day in both periods, by about
0.05R per trade, but S1's own edge is small, its interval includes zero at 20 bps in both
periods, and it earns a small fraction of holding SMH. It is a candidate for paper trading, not
evidence of a profitable system (ground rule 7).
