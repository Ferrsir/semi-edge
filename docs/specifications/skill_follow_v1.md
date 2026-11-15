# Specification: `skill_follow_v1`

Status: **Specified, being implemented (issue #173).** Owner request of 2026-11-15, after six
same-day strategies failed their development tests: test the rules in the `day-trader-skill`
pack and a sector-follow idea, on the current universe and on a wider one, and report what
works. Every rule, number, universe, period and gate below is fixed before any
`skill_follow_v1` result has been computed (ground rule 3). Changing one after results are seen
needs a new version of this spec, with its rationale, written before the new results are read.

This is a screen of 19 pre-registered candidates on two universes (38 tests). Running many tests
makes a lucky winner likely, so a candidate counts only if it passes on development data and
then again on a holdout that the screen never read.

## Where the candidates come from

**The skill pack** (`Jakeschincariol/day-trader-skill`, commit `3d0f4ef`, MIT). All eleven skills
were read. Six chart rules in two of them can be tested as trading rules:

- `trader-scanner` daily scan, on daily closes: 20-day closing-high breakout, bull flag,
  reclaim of the 20-day average, oversold, oversold bounce.
- `trader-scanner`, `trader-entry` and `trader-exit` together: the 15-minute opening-range
  breakout on 5-minute bars, stop at the range midpoint, target 2R, flat by 15:55.

The other skills hold no entry rule. `trader-timemachine` is a bootstrap of the same plan and
its own example reads "close to a coin flip". `trader-risk`, `trader-budget` and `trader-alerts`
size, check and watch a trade that already exists. `trader-news`, `trader-analyst` and
`trader-pelosi` read Yahoo headlines, SEC filings and House disclosures, for which SEMI-EDGE has
no point-in-time history, so they cannot be backtested here. `trader-broker` is not installed
(AGENTS.md).

The rules are re-implemented from the skill's documentation on Alpaca SIP bars. The skill's code
and its Yahoo data are not used and nothing in SEMI-EDGE imports them (ground rule 6).

**Sector follow** (owner idea): "when one tech company goes up or down they all usually follow,
so when one is going up we can guess another will also go up." Tested as: on a session when the
sector index is strongly up, buy the stocks that have not caught up yet (laggards), or the
stocks that are leading it (leaders). Long-only, so a falling sector gives no entry.

## Hypotheses (to be tested, not assumed)

1. A skill setup marks stocks whose next move is better than an ordinary stock-day's.
2. After a strong sector move, laggards catch up or leaders keep leading, by more than costs.

There is no evidence for either in SEMI-EDGE's data. Prior tests point the other way for the
current 16 names: `intraday_v1` and `orb_inplay_v1` (opening-range breakouts),
`intraday_dip_v1` V2 (fell behind SMH) and `closing_momentum_v2` (up with SMH) all failed.

## Universes (fixed now)

- **U16:** the 16 tradable symbols in `config/universe.yaml`. Sector index: SMH.
- **U80:** U16 plus the 64 symbols below. Sector index: QQQ.

```
QCOM TXN ADI NXPI MCHP ON MPWR TER ENTG ARM GFS SWKS QRVO LSCC AMKR COHR ONTO CRDO ALAB MTSI
RMBS STM UMC NVMI AAPL DELL HPE NTAP PSTG WDC STX CIEN LITE CLS FN CSCO IBM GLW APH JBL MSFT
GOOGL AMZN META TSLA NFLX ORCL CRM ADBE NOW PLTR SNOW PANW CRWD NET DDOG MDB SHOP UBER INTU
WDAY ZS TEAM APP
```

The list was written from memory as liquid US-listed technology names, before any bar of the 64
was fetched. It is today's list, so it is survivorship-biased like U16. A symbol for which
Alpaca returns no bars is dropped and named in the results. `config/universe.yaml` is not
changed by this spec.

## Data

- Alpaca SIP, split-adjusted. Daily bars from 2016-01-04 and 5-minute bars from 2024-11-13,
  both through 2026-11-13, or from a symbol's first bar if later.
- A daily bar with high equal to low and volume under 1,000 is missing (as in `dip_v1`). A
  5-minute bar with zero volume is missing.
- A symbol is eligible on a session once it has 60 earlier daily bars and `ATR14 > 0`.
- 5-minute candidates use full sessions only (78 bars on the grid) and need every bar they read.
- Dividends are excluded (price returns).

## Periods (fixed now)

| | 5-minute candidates (A, B, N) | daily candidates (D, S) |
|---|---|---|
| History check | none (no 5-minute bars) | 2016-04-01..2024-12-13 |
| Development | 2024-12-16..2026-06-30 | 2024-12-16..2026-06-30 |
| Holdout | 2026-07-01..2026-10-23 | 2026-07-01..2026-10-23 |

2026-10-26..2026-11-13 was inspected for `gap_reversal_v1` and is in no period. Development
follows the owner's current-regime decision (ADR-015), widened to the start of the 5-minute
cache because six months of daily signals are too few to select on. Disclosure: earlier
strategies read U16 bars in the development period, and `dip_v1`'s single holdout run read U16
daily bars through 2026-11-13 for one RSI statistic. No statistic of this spec has been
computed on any period, and no bar of the 64 added symbols has been read.

## Notation

Daily session `t`: `O, H, L, C` and `ATR14(t)` (Wilder, `semiedge.indicators.atr`),
`ret(t) = C(t)/C(t-1) - 1`. 5-minute bar `k` starts at 09:30 + 5k minutes, so bars 0..2 are the
opening range, bar 5 closes at 10:00, bar 75 closes at 15:50 and bar 77 is the 15:55 bar.
`pc` is the previous session's daily close. `idx` is the universe's sector index.
`slip` is the cost per side.

## Candidates

### A. Skill opening-range breakout (5-minute, same day)

`ORH = max high`, `ORL = min low` of bars 0..2. The trigger is the first bar `k` in 3..71 with
`close(k) > ORH`, once per symbol per session. `stop = floor_to_cent((ORH + ORL) / 2)`,
`r0 = close(k) - stop`, `target = close(k) + 2 * r0`. Refused when `r0 < 0.0005 * close(k)`.
Entry at the open of bar `k+1`. Exits as in `intraday_dip_v1`: stop, target, or the open of the
15:55 bar. This is the skill's default plan.

- **A0:** every trigger.
- **A1..A5:** only symbols that carried a daily setup at the previous session's close, computed
  on the last 30 or more closes exactly as the skill documents them:
  - A1 `breakout_20d`: the last close is above each of the 20 closes before it.
  - A2 `bull_flag`: `C(t-5)/C(t-15) - 1 > 0.10` and the last five closes span under 5% of `C(t)`.
  - A3 `reclaim_ma20`: `C(t-1)` under the 20-close average ending at `t-1`, and `C(t)` above
    the 20-close average ending at `t`.
  - A4 `oversold`: Wilder RSI14 on closes under 30.
  - A5 `oversold_bounce`: RSI14 under 30 at `t-1` and `C(t) > C(t-1)`.

### B. Sector follow at 10:00 (5-minute, same day)

`r = close(5)/pc - 1` for the stock and `r_idx` likewise for the index.
`stop = close(5) - 0.5 * ATR14(d-1)`, `r0 = 0.5 * ATR14(d-1)`, no target. Entry at the open of
bar 6, exit at the stop or the open of the 15:55 bar.

- **B0 (baseline):** every eligible stock on every session.
- **B1 laggard:** `r_idx >= +0.5%` and `r < r_idx`.
- **B2 leader:** `r_idx >= +0.5%` and `r > r_idx`.

### D. Daily setups and daily follow, traded the next session (daily bars)

Signal at the close of `t`. Entry at `O(t+1)`. `stop = O(t+1) - ATR14(t)`. If `L(t+1) <= stop`
the exit is the stop, otherwise `C(t+1)`. `R = (exit*(1-slip) - O(t+1)*(1+slip)) / ATR14(t)`.

- **D0 (baseline):** every eligible stock-day.
- **D1..D5:** the five skill setups above, at the close of `t`.
- **D6 laggard:** `ret_idx(t) >= +1.5%` and `ret(t) < ret_idx(t)`.
- **D7 leader:** `ret_idx(t) >= +1.5%` and `ret(t) > ret_idx(t)`.

### S. Daily follow held three sessions (daily bars)

Signals D6 and D7 (as **S6**, **S7**) and every eligible stock-day (**S0**, baseline). Entry at
`O(t+1)`, `stop = O(t+1) - 2 * ATR14(t)`. On the entry session, exit at the stop if
`L <= stop`. On sessions 2 and 3, exit at the open if `O <= stop`, else at the stop if
`L <= stop`. Otherwise exit at the close of session 3.
`R = (exit*(1-slip) - O(t+1)*(1+slip)) / (2 * ATR14(t))`.

### N. Sector follow held overnight (5-minute bars)

`r = close(75)/pc - 1` at 15:50, and `r_idx` likewise. Entry at the open of bar 76 (15:50).
Exit at the open of bar 0 of the next session. There is no stop, because none can be worked
overnight. `R = (exit*(1-slip) - entry*(1+slip)) / ATR14(d-1)`. Both sessions must be full.

- **N0 (baseline):** every eligible stock on every session.
- **N1 leader:** `r_idx >= +1.0%` and `r > r_idx`.
- **N2 laggard:** `r_idx >= +1.0%` and `r < r_idx`.

An overnight position with no stop is outside the current risk design. A passing N candidate
would need an owner decision and a risk spec before any SIM or paper use.

### Baselines

A0 is judged against B0. A1..A5 against A0. B1 and B2 against B0. D1..D7 against D0. S6 and S7
against S0. N1 and N2 against N0. Each on the same universe and period.

## Signal study (the edge test)

Every signal is taken as its own one-share trade with no position limit, sizing, halts or
account rules, scored in R as defined above. Costs: **5, 10 and 20 bps a side, decided at
10 bps** (the reasoning in `orb_inplay_v1`, "Costs", applies unchanged). Statistics: trades,
win rate, mean R, mean R without the five best trades, and a 95% bootstrap interval of mean R
(2,000 resamples, seed 0).

## Selection (development, 10 bps)

A candidate-universe pair qualifies only with all of:

1. at least 300 trades;
2. mean R above 0 and above its baseline's mean R;
3. mean R without the five best trades above 0;
4. the lower bound of the 95% interval above 0;
5. for D and S candidates, mean R above 0 in the history check at 10 bps.

At most **two** pairs go to the holdout: the qualifying pairs with the highest lower interval
bound, ties to the alphabetically first name. If none qualifies, `skill_follow_v1` fails, the
holdout is not run, and the result is reported as a failure. The selection and its numbers are
recorded in a commit before any holdout run.

## Holdout (one run per selected pair, 10 bps)

A selected pair passes the signal study only with at least 60 trades, mean R above 0 and above
its baseline's, and mean R without the five best trades above 0.

## $5,000 portfolio replay (only for a pair that passes the holdout signal study)

The `5k` risk profile unchanged (`config/risk_5k.yaml`): 1% of equity at risk per trade against
the stop, 25% position cap, 75% gross cap, at most 3 positions, the 1% daily-loss and 5%
drawdown halts, and the actual-fill risk cap of #154. Same-bar ranking when signals exceed free
slots: A by `close(k)/ORH` descending. B1, D6, S6 and N2 by `r - r_idx` ascending. B2, D7, S7
and N1 by `r - r_idx` descending. D1..D5 alphabetical. N candidates, which have no stop, are
sized by the position cap alone. A and B use the `intraday_dip_v1` replay and its three account
models. D, S and N get a daily replay written in a follow-up PR, with these rules, before it is
run. A pair is a **PASS** only if this replay (unrestricted account, 10 bps, holdout period)
also ends with a positive net result.

## Labels on every report

- Survivorship-biased universes (today's lists, no delisted names).
- Earnings blackout not applied (no point-in-time earnings history).
- Price returns only.
- Daily-bar candidates assume the stop price was reachable when the low crossed it and that the
  exit fills at the official close.
- 38 tests were run. A development result alone is not evidence of an edge.

## Live and paper use

Nothing here authorizes SIM or paper trading. A passing pair is a candidate for the paper
plumbing (#21, #162), with R16 and every risk limit unchanged.
