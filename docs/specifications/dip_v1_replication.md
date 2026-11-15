# Specification: `dip_v1` replication on unseen symbols

Status: **Specified (issue #173), before any result.** `dip_v1` S1 (`dip_v1.md`, revision 1.1)
is the only strategy in this project that has passed a pre-registered holdout, and it passed
narrowly on 16 hand-picked semiconductors. The `skill_follow_v1` screen then found that the
only skill rule with a positive mean was also an oversold-buy rule. This spec asks one question:
does `dip_v1` S1, with nothing changed, also work on 64 symbols it was never fitted on or
tested on?

Every rule and gate here is fixed before any `dip_v1` statistic has been computed on the 64
added symbols (ground rule 3).

## What is tested

- **Rule:** `dip_v1` S1 exactly as specified in `dip_v1.md` revision 1.1 with the #154
  amendment: `RSI2 < 10`, the stock and SMH above their 50-day averages, entry at the next
  open, 2-ATR stop, bounce exit or three sessions. No parameter, filter, exit or benchmark is
  changed. S2 is not carried (it failed `dip_v1`'s selection) and no selection happens here.
- **Symbols:** the 64 added symbols of `skill_follow_v1` ("U80 added"), and U80 (those plus
  the original 16) for the portfolio run.
- **Periods:** `dip_v1`'s own. Development 2016-04-01..2024-12-31 and holdout
  2025-01-02..2026-11-13. Both are out of sample for the 64 symbols, because the rule was
  selected on U16 only.
- **Cost:** `dip_v1`'s decision cost of 20 bps a side. 10 and 30 bps are reported.

## Gates

The replication is a **PASS** only with all three, at 20 bps:

1. U80 added, development period, signal study: at least 200 trades, mean R above 0 and above
   the baseline's mean R.
2. U80 added, holdout period, signal study: at least 60 trades, mean R above 0 and above the
   baseline's mean R.
3. U80, holdout period, $5,000 portfolio run (unrestricted account): positive net result.

The baseline is `dip_v1`'s: every symbol-session in an uptrend, with the same entry, stop and
exits. Also reported, without being gates: the 95% bootstrap interval of mean R, results at 10
and 30 bps, the other account models, maximum drawdown, and buying and holding SMH.

## Disclosure

- The `skill_follow_v1` development screen (`docs/results/skill_follow_v1.md`) computed a
  related statistic on U80: the skill's oversold rule (Wilder RSI14 under 30, no trend filter,
  next open to close) made +0.070R on 2024-12-16..2026-06-30 and -0.013R on 2016-2024. That
  rule differs from S1 in its indicator, filter, holding period and stop, but it means this
  test is not fully blind. Running it after seeing that result is the reason it is called a
  replication with fixed rules and not a new strategy.
- The universes are survivorship-biased, the earnings blackout is not applied and returns
  exclude dividends, as in `dip_v1`.

## What a result means

- PASS: `dip_v1` S1 becomes the candidate for SIM and then paper trading on U80, through the
  existing plumbing (#21, #162), with R16 and every risk limit unchanged. It is still not
  evidence of a profitable system (ground rule 7) until paper results exist.
- FAIL: the U16 pass is more likely luck or a semiconductor-specific effect, and `dip_v1`
  stays a weak U16-only candidate.

## Implementation

`semiedge backtest-dip` gains `--universe {config,u80-added,u80}` (default `config`, which is
unchanged behaviour). Commands:

```
semiedge backtest-dip --period development --universe u80-added --equity 5000 --risk-profile 5k
semiedge backtest-dip --period holdout --variant S1 --universe u80-added --equity 5000 --risk-profile 5k
semiedge backtest-dip --period holdout --variant S1 --universe u80 --equity 5000 --risk-profile 5k
```
