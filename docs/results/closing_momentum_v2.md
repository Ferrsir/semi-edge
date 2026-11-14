# `closing_momentum_v2` results

Spec: `docs/specifications/closing_momentum_v2.md` (Codex's v1 from #150, with F-181/F-182
applied before any result). Caches fetched 2026-11-14 on the PC. Labels: survivorship-biased
universe, no earnings blackout, several strategies tested on the same bars.

## Development 2026-01-02..2026-06-30 (engine `0619572`): FAIL

`semiedge backtest-closing-momentum --period development --equity 5000 --risk-profile 5k`

| signal | bps | trades | win % | mean R | 95% CI | w/o top 5 |
|---|---|---|---|---|---|---|
| M1 (stock and SMH up to 10:00) | 10 | 847 | 34.0 | -0.177 | -0.210..-0.144 | -0.187 |
| M2 (also stronger than SMH) | 10 | 527 | 34.7 | -0.174 | -0.214..-0.132 | -0.191 |
| baseline (every full session) | 10 | 1968 | 39.2 | -0.114 | -0.135..-0.093 | -0.119 |
| M1 | 5 | 847 | 43.1 | -0.083 | -0.116..-0.051 | -0.094 |

**Selection: none qualifies** (both variants: mean R not above 0, not above the baseline, and not
above 0 without the top five trades). The holdout is not run. A positive first half-hour made the
last 25 minutes worse than no filter, the opposite of the hypothesis, and every confidence interval
is below zero even at 5 bps.
