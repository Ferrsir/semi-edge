# Specification: same-day execution and SIM mode

Status: **Specified, not implemented (issue #162).** Amends `phase2-4-interfaces.md` §6 (order
lifecycle), §9 (position exits), §10 (scheduler) and §12 (dashboard) for **same-day strategies**:
`intraday_v1`, `intraday_dip_v1`, `gap_reversal_v1`, `orb_inplay_v1`, `closing_momentum_v1` and any
later strategy that produces `semiedge.backtest.intraday_dip.Signal`s on 5-minute bars. The daily
(`momentum_v1`, `dip_v1`) rules in those sections are unchanged. Where this file and §6/§9/§10/§12
differ, this file governs same-day strategies only.

The goal: one execution path, with two interchangeable gateways. **SIM** fills against live 5-minute
bars and never contacts the broker. **PAPER** sends orders to Alpaca paper. Both write the same state
DB rows (§5), so the dashboard shows SIM and PAPER trading identically, labelled by mode. No strategy
is approved for SIM or PAPER by this spec; that needs a passing pre-registered backtest, or an explicit
owner decision recorded as an ADR.

## 1. Strategy interface (pure)

```python
class SameDayStrategy(Protocol):
    name: str                               # e.g. "orb_inplay_v1"
    variant: str                            # the variant selected by its pre-registered test
    def signals_at(self, session: date, k: int, bars: Mapping[str, SessionBars],
                   context: StrategyContext) -> tuple[Signal, ...]: ...
        # every Signal satisfies Signal.k == k and uses bars 0..k only (truncation test, §7)
```
`StrategyContext` carries what the backtest modules already compute from earlier sessions (prior
daily close and ATR, RVOL history, SMH bars for the session so far). Each backtest module gets a thin
adapter that calls its existing `session_signals` on bars truncated at `k`. The truncation tests that
already exist per strategy are the no-look-ahead guarantee; the adapter adds one parity test (§7).

## 2. Run modes (extends the §10.3 mode matrix)

| `TRADING_MODE` | `ENABLE_ORDER_SUBMISSION` | Gateway | State DB | Behaviour |
|---|---|---|---|---|
| `sim` | false (true is a SafetyViolation) | `SimGateway` | `{data_dir}/sim/state/semiedge.sqlite3` | live 5-minute bars, simulated fills, **zero broker calls**, no credentials needed beyond market data |
| `paper` | false | `AlpacaPaperGateway` | `{data_dir}/state/semiedge.sqlite3` | full loop with broker reads; every intent ends `dry_run` |
| `paper` | true | `AlpacaPaperGateway` | `{data_dir}/state/semiedge.sqlite3` | paper order submission |

`sim` is a new `TradingMode` value; `assert_order_submission_allowed` refuses it as it refuses
`scanner`. A SIM DB's `meta.data_source` is `"sim"`, and the store refuses to open a SIM DB in paper
mode and the reverse (same rule as the real/synthetic separation, F-043). The SIM DB has its own
namespace (§6.2), so its client IDs can never be adopted by reconciliation of the paper DB.

## 3. Order lifecycle changes (amends §6.1 and §6.3)

- **Entries:** `order_type="market"`, `time_in_force="day"`, `order_class="simple"`, submitted in the
  first `bar_submit_seconds` after the decision bar's close is confirmed (the bar `k+1` open is the
  target fill time, as in the backtests). The §6.1 gateway guard "BUY only with `opg`" becomes "BUY
  only with `opg` for daily strategies, or `day` market for same-day strategies". The strategy class
  is recorded in the order intent, never inferred.
- **Valid-until:** an entry intent is valid only until bar `k+1`'s open plus `bar_submit_seconds`.
  It is never submitted later and never chased.
- **Protection:** after an entry fill, one protective sell for exactly the filled qty:
  - with a finite target: `order_class="oco"` (limit = target, stop = stop), `time_in_force="day"`;
  - with no target (`orb_inplay_v1`): a simple `stop` sell, `time_in_force="day"`.

  The gap rule of §6.3 applies unchanged (a fill at or beyond a level → `flatten`, no protection).
  The protection deadline (§6.3) applies.
- **Flatten:** at the last bar's open (15:55 on a full day, 12:55 on a 13:00 close),
  cancel → confirm → re-read → market `day` sell for the remaining qty (the §9.1 sequence). Nothing is
  held past the close. A position still open after `close − 1 minute` → `PROTECTION` halt and alert.
- **Risk:** sizing re-checks the actual fill against the risk budget, exactly as the backtests do
  after F-179/F-180 (#152, #158). The risk manager's per-trade, position, gross, positions, daily-loss
  and drawdown checks (§2) apply unchanged, with the `5k` profile for the $5,000 account.
- **PDT:** in `paper` mode under $25,000 equity, an entry is refused when it would be the fourth day
  trade in five sessions (Alpaca would refuse it too). In `sim` mode the account model is chosen at
  startup (`unrestricted`, `margin_pdt` or `cash`, the backtests' three models) and recorded in the DB.

## 4. SimGateway (new; implements `BrokerGateway`)

```python
class SimGateway:   # implements BrokerGateway; no network, no credentials
    def __init__(self, bars: LiveBarFeed, *, slippage_bps: float, account: SimAccount,
                 clock: Clock, namespace: str) -> None: ...
```
- `send_order` records the order and returns `accepted`. A market `day` BUY or SELL fills at the next
  5-minute bar's open plus/minus slippage, when that bar arrives. Stop and limit legs fill by the
  backtests' bar rules: a gap through the level fills at the open, the stop wins a tie, and a limit
  fills at its price.
- Fills are reported through `list_fills` with deterministic activity IDs, so the normal
  `ingest_order_fills` path (§6.4) is used. Positions and equity are never written directly.
- `get_account_snapshot` returns the simulated account (cash, settled cash for `cash`, equity marked
  at the last bar close). `get_positions` returns the simulated broker positions.
- The same guards as `AlpacaPaperGateway`, except the paper-client check: no shorting, positive integer
  qty, allowed order classes only, client IDs parsed with its namespace.
- Slippage defaults to the strategy's pre-registered decision level (for example 10 bps for
  `orb_inplay_v1`).

## 5. Scheduler changes (amends §10.2)

A new job `BAR_CLOSE(S, k)` runs at the close of every 5-minute bar `k` plus `bar_settle_seconds`:
1. fetch and validate that bar for the universe (the existing intraday fetch, #118);
2. `sync_open_orders` and `ensure_protection`;
3. if `k` is a decision bar and no halt is active, `signals_at`, then the risk manager, then stage
   entries;
4. `submit_staged(kinds={"entry", "flatten"})`;
5. record a `broker_intraday` equity snapshot and a heartbeat.

`PRE_OPEN` is not used by same-day strategies. `POST_CLOSE(S)` records the `broker_close` equity
snapshot, reconciles, and asserts that no position is open. A late bar (not available by
`k + bar_late_seconds`) skips that bar's decisions and is recorded. Late entries are never submitted.

New `EngineConfig` fields (all required, validated): `bar_settle_seconds` (≥ 5),
`bar_late_seconds` (> `bar_settle_seconds`), `bar_submit_seconds` (≥ 5).

## 6. Dashboard (amends §12)

- `semiedge dashboard --source cache|synthetic|sim` opens exactly one DB. The mode banner shows
  **SIM — NO ORDERS SENT** for a SIM DB, **PAPER** for the paper DB.
- Pages unchanged in structure. Positions show the stop, the target (or "held to 15:55"), the
  strategy and variant. Trade history shows each round trip with entry and exit bar times, the exit
  reason (target, stop, gap, flatten) and the dollar P&L. Overview shows today's realised P&L, open
  P&L and day-trade count (for `margin_pdt`).
- The 60-second auto-refresh (#127) already covers live updates.

## 7. Acceptance

- **Parity:** a SIM run over cached bars of a past window produces exactly the backtest's portfolio
  trade list for the same strategy, variant, account model, slippage and equity: same symbols, bars,
  shares, exit reasons, and prices to the cent.
- **SIM safety:** zero calls to any Alpaca trading endpoint (a fake client factory that fails if called);
  `ENABLE_ORDER_SUBMISSION=true` with `sim` → SafetyViolation; a SIM DB refused in paper mode and the
  reverse.
- **Same-day:** no position is open after the last bar; flatten uses cancel-confirm-sell; a missing last
  bar flattens at the next available price and alerts.
- **Order guards:** `day` market BUY allowed only for a same-day strategy intent; `opg` rules for daily
  strategies unchanged (existing tests stay green).
- **Look-ahead:** each adapter's `signals_at(k)` equals the backtest's signals at `k` on truncated bars.
- **Dashboard:** AppTest smoke test of every page on a SIM fixture DB, with the SIM banner on every page.

## 8. Order of work

1. This spec (docs; review: Codex).
2. T9 part 2: `semiedge.execution.client_ids`, gateway types and `SimGateway`, `OrderManager` for
   same-day intents (code-owned; owner approval).
3. T11/T12: flatten and `BAR_CLOSE` loop; `semiedge run --mode sim`.
4. The dashboard `--source sim`.
5. `AlpacaPaperGateway` same-day support, plus the documented manual paper check that Alpaca accepts
   `day` market entries and `day` OCO/stop exits (§6.3), recorded as an ADR.
