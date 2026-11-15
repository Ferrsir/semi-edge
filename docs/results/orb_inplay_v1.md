# `orb_inplay_v1` results

Spec: `docs/specifications/orb_inplay_v1.md` (current regime, decided at 10 bps). Alpaca SIP
caches fetched 2026-11-14 on the PC (5-minute 2024-11-13..2026-11-13; daily 2016-01-04..2026-11-13).
Labels: survivorship-biased universe, no earnings blackout, bought at the 09:35 open plus slippage.

## Development 2026-01-02..2026-06-30 (engine `b253411`): FAIL

`semiedge backtest-orb-inplay --period development --equity 5000 --risk-profile 5k`

| signal | bps | trades | win % | mean R | 95% CI | median R | w/o top 5 |
|---|---|---|---|---|---|---|---|
| O1 (in play, 0.1-ATR stop) | 10 | 434 | 16.4 | -0.221 | -0.511..+0.083 | -1.409 | -0.411 |
| O2 (in play, first-candle-low stop) | 10 | 434 | 39.6 | -0.033 | -0.189..+0.124 | -1.058 | -0.122 |
| baseline (any green first candle) | 10 | 1028 | 16.8 | -0.307 | -0.491..-0.125 | -1.389 | -0.388 |
| O2 | 5 | 434 | 40.3 | +0.059 | -0.096..+0.214 | -1.012 | -0.030 |

**Selection: none qualifies** (both variants: mean R not above 0, and not above 0 without the
top five trades). The holdout is not run. The in-play filter beats the baseline, but neither
variant makes money at 10 bps. Only O2 at 5 bps is positive, and its interval includes zero.

$5,000 portfolio: every run hits the 5% drawdown halt between 2026-01-30 and 2026-05-28. O2
unrestricted ends +$32 at 10 bps (+$213 at 5 bps) after its halt.
