# Decisions

ADR-001: fail closed until trusted enforcement exists. Local tests and AI comments cannot authorize merging.

ADR-002: separate research, merge authorization and deployment authorization. No model output can grant financial permissions.

ADR-003: use GitHub issues as durable backlog; no persistent worker is claimed without an available configured orchestrator.

ADR-004 (2026-10-30): the owner waived the GitHub branch-protection and trusted-review-issuer
requirements of ADR-001 for this private repository and delegated PR merging to the lead agent.
A PR may merge when CI is green and a cross-provider review (Claude ↔ Codex) recorded on the PR is
PASS with no open P0/P1. This does not relax ADR-002: no agent or model output can grant live
trading, raise risk ceilings, or add credentials. Paper-only enforcement stays in code.

ADR-005 (2026-11-13): `max_holding_sessions` counting. The entry session is held session 1; the
time exit is decided at the close of the session where `held_sessions >= max_holding_sessions`
and executed at the next open (`semiedge.strategy.exits`, phase2-4-interfaces.md §3.3). Made
before any backtest result exists. Rationale: `momentum_v1.md` defines the parameter but not its
counting, and backtest and paper must count identically. Changing it is a spec change.

ADR-006 (cited as D-006) (2026-11-13): the `momentum_v1` score weights (40/30/20/10) and
saturation points are heuristic priors with no statistical evidence behind them. They only order
eligible candidates and do not predict returns. Phase 2 sensitivity (phase2-4-interfaces.md §15)
must report their effect.

ADR-007 (cited as D-007) (2026-11-13): research data are split-adjusted price returns
(`adjustment: split`); dividends are excluded. Every backtest and research report carries the
label `PRICE RETURN: dividends excluded` (phase2-4-interfaces.md §3.6).

ADR-008 (2026-11-13): backtest entry invalidation also triggers when the raw open is at or below the
stop (`open_d ≤ stop`), not only when the slipped fill is. Without it, an adverse slippage could lift
the entry fill above the stop, and the position would later stop out at `stop · (1 − slip)`, which
is better than the open, so results would be optimistic. Made before any backtest result exists
(rule 3); found as F-096 in the review of #75. Strictly more conservative. Scope: backtest-only.
The paper engine has no trustworthy opening price at fill time, so §6.3 documents a parity exception
(F-130).

ADR-009 (2026-11-13): earnings data for R16 (#20). Owner decision: Finnhub's earnings calendar is
the primary source and Alpha Vantage's `EARNINGS_CALENDAR` is the cross-check. Keys come only from
`FINNHUB_API_KEY` and `ALPHAVANTAGE_API_KEY`. A date counts as verified when both sources are fresh
(`max_age_hours`), cover the query window and agree, or when a hand-checked entry in
`config/earnings_overrides.yaml` (investor-relations page, `verified_on` within
`override_max_age_days`) exists. Anything else (a missing key, a stale snapshot, a fetch error,
short coverage or a disagreement) is `verified=False`, which R16 rejects with
`earnings_data_unavailable`. A disagreement blocks only the affected symbol. An unusable source
blocks every symbol. Each source is refreshed at most once per UTC day (free-tier limits;
failures retry at most `max_attempts_per_day` times). R16, `earnings_blackout_sessions` and
`require_verified_earnings_data: true` are unchanged. Yahoo and other unofficial feeds are
excluded. The vendor calendar is not point-in-time, so backtests keep the labelled waiver.
Spec: `docs/specifications/earnings-calendar.md`.

ADR-010 (2026-11-14): SEMI-EDGE is a day-trading system. Owner decision: replace the daily swing
strategy `momentum_v1` (daily bars, next-open entries, holds of up to 5 sessions, a 50-day sector
filter) with a same-day strategy on 5-minute bars, flat by 15:55 ET, on the same 16 tradable semis
with SMH as the market filter, under the unchanged risk ceilings. The strategy (`docs/specifications/intraday_v1.md`) runs three
setups through the session so it can trade several times a day, as the owner asked: an
opening-range breakout in the morning (the lead's recommendation as the lead setup, since these
stocks concentrate range and volume in the first hour), a prior-day-high breakout all day, and a
VWAP pullback in the middle of the day. The first two follow the methods of the owner's
`day-trader-skill` research pack, rebuilt on SEMI-EDGE's own data. Each setup is reported and
judged on its own. It was chosen and parameterised before
any intraday data was examined. For the record, the one swing backtest run on real data
(`semiedge backtest --source cache --start 2023-06-01 --end 2026-11-13`) returned +3.4% over the
period, while buying and holding SMH or QQQ returned far more. That result did not drive this
decision; the owner made it because the system is meant to day trade.

ADR-009 amendment (2026-11-14, #114): with real keys, Finnhub answered TSM with a row for the
Taiwan listing `2330.TW`, and the rule that one symbol's parse failure fails the whole snapshot
blocked all 16 symbols. Owner decision: an unparseable answer now leaves only that symbol
uncovered (`finnhub_symbol_not_covered`, still blocked by R16); transport and HTTP failures and an
all-symbols parse failure still fail the whole snapshot. R16 itself is unchanged.

ADR-011 (2026-11-14, #128): a $5k account profile and account models. Owner decisions: the
day-trading strategy is tested with about $5,000 so results reflect a similar amount of real money,
through a backtest, a local simulation on live bars and Alpaca paper. `config/risk_5k.yaml` holds
the $5k limits (1% risk per trade, 25% per position, 3 positions, 75% gross exposure, unchanged 1%
daily-loss and 5% drawdown halts) and is used only when a run selects it; `config/risk.yaml` is
unchanged. The ceilings in `src/semiedge/safety/limits.py` rise just enough to allow that profile,
and `RiskLimits` defaults are now `DEFAULT_RISK_LIMITS` (today's values) instead of the ceilings,
so raising a ceiling never loosens a run that relies on defaults. Every backtest reports three
account models (`unrestricted`, `margin_pdt`, `cash`) because a real account of this size is bound
by the pattern-day-trader rule or by T+1 settlement. Spec: `intraday_v1.md`, "Accounts, profiles
and run modes", written before any intraday result.


ADR-012 (2026-11-14, #133): after `intraday_v1` showed no edge on real data (#130: every $5k run
hit the 5% drawdown halt; expectancy -0.01R to -0.18R even before slippage), the owner asked for
dip-buying that sells later the same day or the next day, and delegated the choice ("choose
whichever will yield the best results"). The lead chose a swing variant, `dip_v1`
(`docs/specifications/dip_v1.md`): daily bars, holds of up to three sessions, a wide 2-ATR stop
and a bounce exit. It supersedes ADR-010's "flat by 15:55" rule for this strategy, so positions
may be held overnight. The reasons were fixed before any test: slippage on tight intraday stops
cost 0.3–0.9R per trade in #130, overnight holds are not day trades under the PDT rule at $5k, and
daily bars give about ten years of history. "Best results" cannot be known in advance, and
picking whichever variant tests best would overfit. So two dip signals are pre-registered, one is
selected on 2016–2024 only, and 2025–2026 is held out and run once against a baseline of buying
any day in an uptrend. The intraday dip variant is deferred, not tested. Risk limits are
unchanged (the `5k` profile, ADR-011).

ADR-013 (2026-11-14, #137): owner decision: SEMI-EDGE's main strategy is same-day dip-buying,
traded at high frequency between the open and the close, with no multi-day holds and no 50-day
filter ("we are DAY TRADING ... we just need the signal that a stock is low and that it should
go back up"). The strategy is `intraday_dip_v1` (`docs/specifications/intraday_dip_v1.md`): a
stock stretched below today's VWAP prints a reversal bar; the target is VWAP, the stop is the
day's low, and everything is flat by 15:55. Two stretch signals are pre-registered (distance
below VWAP; drop relative to SMH since the open), selected on 2024-12-16..2025-12-31 and tested
once on 2026. It replaces `dip_v1` (ADR-012) as the main strategy; `dip_v1`'s results stay on
record. ADR-010's same-day rule applies again. Risk limits are unchanged (`5k` profile, ADR-011).
At $5,000 the pattern-day-trader rule allows 3 day trades in 5 sessions in a margin account; a
cash account has no day-trade limit but trades only settled cash. Both are reported.

ADR-014 (2026-11-14, #141): after `intraday_dip_v1` failed (#139), the owner asked for a same-day
strategy that fits moves like NVDA on 2026-11-12: it opened 13% below the previous close on news
of the CEO's resignation and recovered most of the way within the session. The candidate is
`gap_reversal_v1` (`docs/specifications/gap_reversal_v1.md`): a gap down of at least 1 daily ATR,
bought only after a reclaim (of VWAP, or of the 15-minute high), with a half-gap target and the
day's-low stop, flat by 15:55. The idea came from inspecting 2026-10-26..2026-11-13, so that
window is excluded from both test periods and the disclosure is in the spec. R16 is unchanged:
live trading skips earnings gaps that the backtest includes.
