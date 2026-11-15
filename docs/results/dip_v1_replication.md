# `dip_v1` replication results

Spec: `docs/specifications/dip_v1_replication.md`. Spec and CLI option committed at `603958c`
before any run. Data: the Alpaca SIP split-adjusted daily cache (U16 and SMH fetched
2026-11-14, the 64 added symbols fetched 2026-11-15, 2016-01-04..2026-11-13). Labels:
survivorship-biased universes, no earnings blackout, price returns only, daily-bar stop fills.

## Gate 1: 64 added symbols, 2016-04-01..2024-12-31

`semiedge backtest-dip --period development --universe u80-added --equity 5000 --risk-profile 5k`

| run | bps | trades | win % | mean R | 95% CI |
|---|---|---|---|---|---|
| S1 | 10 | 3050 | 56.9 | +0.021 | +0.000..+0.041 |
| S1 | 20 | 3050 | 54.0 | -0.019 | -0.040..+0.002 |
| S1 | 30 | 3050 | 50.4 | -0.059 | -0.080..-0.038 |
| baseline | 20 | 64154 | 47.6 | -0.056 | -0.059..-0.052 |

**FAIL.** At 20 bps the mean R is above the baseline but not above 0.

## Gate 2: 64 added symbols, 2025-01-02..2026-11-13

`semiedge backtest-dip --period holdout --variant S1 --universe u80-added --equity 5000 --risk-profile 5k`

| run | bps | trades | win % | mean R | 95% CI |
|---|---|---|---|---|---|
| S1 | 10 | 664 | 62.5 | +0.111 | +0.064..+0.158 |
| S1 | 20 | 664 | 60.5 | +0.084 | +0.036..+0.130 |
| S1 | 30 | 664 | 58.3 | +0.056 | +0.008..+0.102 |
| baseline | 20 | 13464 | 50.4 | -0.019 | -0.028..-0.010 |

**PASS.** $5,000 on these 64 symbols alone: +$336 (+6.7%) at 20 bps and +$659 (+13.2%) at
10 bps over 187 trades, maximum drawdown -4.8%, no halt. At 30 bps: -$145, halted 2026-01-02.

## Gate 3: U80, $5,000 portfolio, 2025-01-02..2026-11-13

`semiedge backtest-dip --period holdout --variant S1 --universe u80 --equity 5000 --risk-profile 5k`

| account | bps | trades | net | max drawdown | halted |
|---|---|---|---|---|---|
| unrestricted | 10 | 194 | +$439 (+8.8%) | -4.7% | no |
| unrestricted | 20 | 112 | -$103 (-2.1%) | -5.2% | 2026-01-02 |
| unrestricted | 30 | 112 | -$236 (-4.7%) | -5.6% | 2025-12-31 |

The three account models give the same numbers. Signal study on all 80 symbols at 20 bps:
+0.073R over 822 trades (interval +0.030..+0.114), baseline -0.019R. SMH buy-and-hold over the
period: +99.1%.

**FAIL.** The 20 bps run reached the 5% drawdown halt on 2026-01-02 and took no trade after
that, so it ended at -$103.

## Verdict: FAIL (gates 1 and 3)

`dip_v1` S1 does not replicate as a strategy that clears 20 bps a side on symbols it was never
fitted on. It stays a weak candidate, not a validated one.

What the four samples now say together, at each cost (signal study, mean R):

| sample | trades | 10 bps | 20 bps | baseline at 20 bps |
|---|---|---|---|---|
| U16, 2016-2024 (selection sample) | 865 | +0.046 | +0.008 | -0.039 |
| U16, 2025-2026 (holdout) | 158 | +0.053 | +0.027 | -0.018 |
| 64 added, 2016-2024 | 3050 | +0.021 | -0.019 | -0.056 |
| 64 added, 2025-2026 | 664 | +0.111 | +0.084 | -0.019 |

- The dip condition beat the baseline in all four samples, by 0.04R to 0.10R a trade. That part
  did replicate on unseen symbols and it is the most consistent result this project has.
- Whether it makes money depends on the cost. At 10 bps a side all four samples are positive.
  At 20 bps the long history is about zero and only 2025-2026 is positive.
- 20 bps a side was chosen as a deliberately high assumption. The real cost of a market order
  of about $1,250 in these stocks has never been measured here. That measurement is what SIM
  and paper trading are for, and it decides whether this rule is tradable.
- In the portfolio runs one drawdown halt ends the whole run, because the 5% halt waits for an
  owner reset. The +$439 and -$103 results on U80 differ mainly through that halt.
- None of this is evidence of a profitable system (ground rule 7). SMH itself roughly doubled
  over the holdout period.
