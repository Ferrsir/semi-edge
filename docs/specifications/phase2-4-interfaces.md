# Specification — Phase 2–4 module interfaces

Status: **Specified (issues #15–#19, #21–#27, #29).** #20 (earnings source) is an owner decision
(§19); #28 (AI) is out of scope except for its import-deny row (§0.2). This is the contract that lets Claude and
Codex implement Phase 2–4 in parallel without colliding. It extends
`docs/specifications/phase1-interfaces.md` (whose general rules apply unchanged) and never
changes `momentum_v1` (`docs/specifications/momentum_v1.md`). Changing a signature, table,
invariant or acceptance test here requires updating this document in the same PR.

Ground rules that every section inherits (AGENTS.md):
1. PAPER only. Trading clients come only from `semiedge.safety.broker.create_paper_trading_client`.
2. Nothing here raises a ceiling in `semiedge.safety.limits` or loosens `config/risk.yaml`.
3. No strategy change. Execution and backtest modelling choices below are fixed **before** any
   backtest result exists; changing them later needs a spec change with rationale.
4. Fail closed: invalid/missing input → explicit reason or exception, never a default.
5. No fabricated results: every reported number carries the command that reproduces it.

Conventions used below
- "Session" = XNYS session date (`datetime.date`). `t` = decision (signal) session,
  `t+1` = the next XNYS session per `TradingCalendar.next_session`, never "next row in a frame".
- "Decision at close t" = after `session_close(t) + bar_settle_minutes`, using bars `≤ t` only.
- All timestamps are tz-aware UTC. Money is `float` dollars in computations; persisted as REAL.
  Share quantities are `int` (whole shares only; fractional shares are out of scope).
- `Reason` strings are stable snake_case codes (machine-readable) plus a human `detail`.
- Every module listed is `mypy --strict` clean and has no import side effects (no network, no
  file writes, no logging configuration at import time).
- "Spec-verbatim" module: its public surface is fully defined here; the first PR that needs it
  creates it exactly as written; later PRs import it and rebase on conflicts.

---

## 0. Package map, ownership and dependency direction

| Package / module | Issue | Board task | Kind |
|---|---|---|---|
| `semiedge.clock` | #24 | first PR that needs it (spec-verbatim, §0.3) | pure types |
| `semiedge.data.calendar.TradingCalendar.session_open` | #24 | first PR that needs it (additive, §0.3) | data layer |
| `semiedge.risk.types` | #19 | T8 (spec-verbatim; T5 may create first) | pure types |
| `semiedge.risk.proposals` | #15/#19 | T5 or T8, whichever first (spec-verbatim, §1.1) | pure |
| `semiedge.risk.manager` | #19 | T8 | pure |
| `semiedge.strategy.exits` | #15/#23 | T5 (spec-verbatim; T11 imports) | pure |
| `semiedge.backtest.costs`, `.fills`, `.engine`, `.results` | #15 | T5 | pure (no I/O except explicit `save`) |
| `semiedge.backtest.metrics`, `.benchmarks` | #16 | T6 | pure |
| `semiedge.backtest.walkforward`, `.sensitivity`, `.stress` | #17 | T7 | pure + explicit export |
| `semiedge.data.corporate_actions` | #18 | T17 | data layer |
| `semiedge.state.*` (SQLite store, schema, read-only view, halt control) | #21 | T9 (schema migration 001, `semiedge.state.models` and `semiedge.state.order_status` spec-verbatim) | persistence |
| `semiedge.broker.gateway` | #21 | T9 | broker adapter |
| `semiedge.execution.client_ids`, `.orders`, `.portfolio` | #21 | T9 | orchestration |
| `semiedge.execution.reconcile` | #22 | T10 | orchestration |
| `semiedge.execution.positions` | #23 | T11 | orchestration |
| `semiedge.engine.schedule`, `.loop` | #24 | T12 | runtime |
| `semiedge.engine.health`, `.shutdown`; `Dockerfile`, `compose.yaml` | #27 | T15 | runtime / deploy |
| `semiedge.notify.*` | #26 | T14 | side channel |
| `semiedge.dashboard.*` | #25 | T13 | UI (read-only) |
| `semiedge.security.*` | #29 | T16 | cross-cutting |

Dependency corrections for the board (lead to apply in AGENTS.md; this PR does not edit it):
T5 also depends on `semiedge.risk.types` (spec-verbatim, no wait on T8 code); T13 also needs the
`semiedge.state` schema (T9) — it may build against migration 001 fixtures before T9 merges;
T14 needs the `notifications_outbox` table (T9).

### 0.1 Layers (imports point downward only)

```
L6  semiedge.cli, semiedge.dashboard                                  (entry points)
L5  semiedge.engine  ·  semiedge.notify
L4  semiedge.execution
L3  semiedge.broker  ·  semiedge.state  ·  semiedge.backtest  ·  semiedge.reporting (exists, Phase 1)
L2  semiedge.risk  ·  semiedge.strategy   (lateral: only semiedge.risk → semiedge.strategy)
L1  semiedge.data  ·  semiedge.indicators
L0  semiedge.safety  ·  semiedge.config  ·  semiedge.data.schema  ·  semiedge.logconfig  ·  semiedge.security  ·  semiedge.clock
```

Consequences that implementers must not "fix" locally: `Clock` lives in `semiedge.clock` (L0)
because `semiedge.execution` (L4) needs it and must not import `semiedge.engine`;
`Notification`/`NotificationCategory` live in `semiedge.state.models` because `semiedge.state` and
`semiedge.execution` enqueue notifications and must not import `semiedge.notify` (`semiedge.notify`
re-exports them). The engine obtains a gateway only via `semiedge.broker.gateway.build_paper_gateway`
so that no module outside `semiedge.broker.*`/`semiedge.safety.broker` imports `alpaca.trading`.
Lateral imports within L2 are allowed only from `semiedge.risk` to `semiedge.strategy`
(`semiedge.risk.proposals` reads `SignalEvaluation`/`MomentumV1Params`); `semiedge.strategy`
never imports `semiedge.risk` (deny row in §0.2). `semiedge.reporting` (Phase 1 signal reports)
imports `semiedge.config`, `semiedge.data`, `semiedge.risk.sizing` and `semiedge.strategy`.
Lateral imports within L3 are allowed only into `semiedge.reporting`: `semiedge.backtest` →
`semiedge.reporting` (`new_run_id`, `SYNTHETIC_BANNER`, §3.5/§3.6) and `semiedge.state` →
`semiedge.reporting` (`evaluation_to_dict`, §5.1); `semiedge.reporting` imports neither.

### 0.2 Import allow/deny table (enforced by `tests/safety/test_import_boundaries.py`)

The test parses every `**/*.py` under `Path(semiedge.__file__).parent` (never a CWD-relative
`src/` path: `tests/conftest.py` chdirs every test to `tmp_path`) with `ast` (static; no import execution), resolves
absolute and relative imports, and fails on any forbidden edge. Rows for packages that do not
exist yet are skipped. The table is **append-only**: a PR may add rows or tighten, never loosen
(loosening needs a spec change). The first Phase 2 PR to merge creates the test.

| Module (prefix) | MUST NOT import |
|---|---|
| `semiedge.risk` | `alpaca`, `semiedge.broker`, `semiedge.execution`, `semiedge.engine`, `semiedge.state`, `semiedge.backtest`, `semiedge.notify`, `semiedge.dashboard`, `semiedge.data.alpaca_source`, `semiedge.data.store`, `sqlite3`, `requests`, `httpx`, `urllib.request`, `socket` |
| `semiedge.strategy`, `semiedge.indicators` | everything above plus `semiedge.data.alpaca_source`, `semiedge.data.store`, `semiedge.data.synthetic` |
| `semiedge.strategy` | `semiedge.risk` (the only lateral L2 edge is `semiedge.risk` → `semiedge.strategy`) |
| `semiedge.reporting` | `alpaca`, `semiedge.broker`, `semiedge.execution`, `semiedge.engine`, `semiedge.state`, `semiedge.notify`, `semiedge.dashboard`, `semiedge.backtest` |
| `semiedge.backtest` | `alpaca`, `semiedge.broker`, `semiedge.execution`, `semiedge.engine`, `semiedge.state`, `semiedge.notify`, `semiedge.dashboard`, `semiedge.data.alpaca_source`, network modules |
| `semiedge.state` | `alpaca`, `semiedge.broker`, `semiedge.execution`, `semiedge.engine`, `semiedge.notify`, `semiedge.dashboard`, `semiedge.backtest`, network modules |
| `semiedge.broker` | `semiedge.execution`, `semiedge.engine`, `semiedge.notify`, `semiedge.dashboard`, `semiedge.backtest` |
| `semiedge.execution` | `semiedge.engine`, `semiedge.notify`, `semiedge.dashboard`, `semiedge.backtest`; `alpaca` (must go through `semiedge.broker.gateway`) |
| `semiedge.notify` | `alpaca`, `semiedge.broker`, `semiedge.execution`, `semiedge.engine`, `semiedge.backtest`, `semiedge.dashboard` |
| `semiedge.dashboard` | `alpaca`, `semiedge.broker`, `semiedge.execution`, `semiedge.engine`, `semiedge.notify`, `semiedge.data.alpaca_source`, `semiedge.state.store` (writer), network modules. Allowed: `semiedge.state.readonly`, `semiedge.state.halt_control`, `semiedge.config`, `semiedge.safety.modes`, `semiedge.security` |
| `semiedge.ai` (future, #28) | `semiedge.execution`, `semiedge.broker`, `semiedge.engine`, `semiedge.risk.manager`, `semiedge.state.store` |
| every module except `semiedge.safety.broker` | constructing `alpaca.trading.client.TradingClient` (already enforced by `check_unsafe_config.py`) |
| every module except `semiedge.broker.*` and `semiedge.safety.broker` | `alpaca.trading` |
| every module except `semiedge.data.alpaca_source` and `semiedge.data.corporate_actions` | `alpaca.data` |

Acceptance (module-level `pytestmark = pytest.mark.safety`; markers are always explicit, there
is no auto-marking by directory):
- A synthetic module tree written to `tmp_path` with one forbidden edge per row is detected
  (proves the checker works); the real tree passes.
- Relative imports (`from ..broker import x`) and `importlib.import_module("...")` with a literal
  string are both resolved; a non-literal `import_module` call in `src/` fails the test.
- `if TYPE_CHECKING:` imports count (a type-only import is still an edge).

### 0.3 Small shared additions (spec-verbatim; first PR that needs them creates them)

```python
# semiedge/clock.py
class Clock(Protocol):
    def now(self) -> datetime: ...            # tz-aware UTC, always

class SystemClock:                            # datetime.now(UTC)
    def now(self) -> datetime: ...

class FrozenClock:                            # tests; naive datetime -> ValueError
    def __init__(self, at: datetime) -> None: ...
    def now(self) -> datetime: ...
    def advance(self, delta: timedelta) -> None: ...   # delta >= 0, else ValueError

# semiedge/data/calendar.py — additive method on the Phase 1 TradingCalendar
    def session_open(self, session: date) -> datetime: ...   # tz-aware UTC; ValueError if not a session
```
No other *new* module reads the wall clock: everything below takes a `Clock` or an explicit `now`.
(Phase 1 `semiedge.cli` and `semiedge.data.alpaca_source` already call `datetime.now(UTC)`; they
are not in the §18 no-wall-clock AST scan.)

### 0.4 Configuration files

`config/app.yaml` keeps exactly its Phase 1 sections (`data`, `scanner`; the loader rejects unknown
sections). New settings live in **new files**, each with its own strict loader in
`semiedge.config.loader` and model in `semiedge.config.models` (all fields required, `_Frozen`):

| File | Model | Loader | Owner |
|---|---|---|---|
| `config/backtest.yaml` | `BacktestSettings` (§3.1) | `load_backtest_settings(config_dir) -> BacktestSettings` | T5 |
| `config/engine.yaml` | `EngineConfig` (§10.1) | `load_engine_config(config_dir) -> EngineConfig` | T9 (T9 needs `protection_deadline_seconds`; T12 may only append fields) |
| `config/research/*.yaml` | `ParameterGrid` / `WalkForwardPlan` (§15) | `load_research_file(path) -> ...` | T7 |

`run_fingerprint(app, *extra: BaseModel) -> str` (in `semiedge.config.loader`) = sha256 of the
canonical JSON (`sort_keys=True, separators=(",", ":")`) of `{"app": app.model_dump(mode="json"),
"<ClassName>": extra.model_dump(mode="json"), ...}`. Backtests fingerprint `(app, BacktestSettings)`;
the engine fingerprints `(app, EngineConfig)`. `config_fingerprint(app)` stays unchanged.

### 0.5 Amendments to existing safety tests (allowed, and only these)

PR #39 (`semiedge.broker.account`) added three AST scans in `tests/safety/test_broker_readonly.py`:
(a) no module in `semiedge.broker` references any `TradingClient` method name except `get`;
(b) in `semiedge.broker`, a name `client` is used only as `client.get("<literal in READ_PATHS>")`
or handed to a paper guard, with no network/dynamic-lookup identifiers; (c) no file anywhere in
`src/semiedge` contains an identifier, attribute, keyword or string equal to a `TradingClient`
method name matching `order|cancel|close|replace|exercise`. T9 applies **one** single-file
allowlist, exactly `src/semiedge/broker/gateway.py` (path equality, asserted by a test that the
allowlist has length 1), to **all three** scans; every other file stays scanned by all three.
Only `gateway.py` may reference or call SDK order methods (it calls exactly `submit_order` and
`cancel_order_by_id`; its reads use `get`, §6.1). Consequently no `BrokerGateway` method name (§6.1) equals any
`TradingClient` method name, so callers in `semiedge.execution`/`.engine` pass scan (c)
unchanged. No other existing safety test may be relaxed by Phase 2–4 work.

---

## 1. Shared pure types — `semiedge.risk.types` (spec-verbatim)

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from semiedge.safety.modes import TradingMode


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class HaltKind(StrEnum):
    MANUAL = "manual"                 # owner via dashboard/CLI
    DAILY_LOSS = "daily_loss"         # auto; clears at the next decision cycle (see §8)
    DRAWDOWN = "drawdown"             # auto; latched until a human clears it
    RECONCILIATION = "reconciliation" # irreconcilable broker/local state; human clears
    BROKER = "broker"                 # repeated broker failures / account blocked; human clears
    DATA = "data"                     # regime benchmark stale/invalid; clears when data is fresh
    PROTECTION = "protection"         # a filled position lacked a protective stop past deadline


@dataclass(frozen=True, slots=True)
class Halt:
    kind: HaltKind
    reason: str
    since: datetime


@dataclass(frozen=True, slots=True)
class EntryProposal:
    """A ranked, eligible momentum_v1 candidate turned into a proposed long entry."""
    symbol: str
    decision_session: date        # t; bars <= t were used
    data_as_of: date              # last completed session in the inputs; must equal decision_session
    strategy: str                 # "momentum_v1"
    strategy_version: str
    rank: int                     # 1-based among eligible (SignalEvaluation.rank)
    score: float
    entry_reference: float        # C_t
    atr: float                    # ATR_t
    stop: float                   # C_t - stop_atr_multiple * ATR_t (from SignalEvaluation)
    target: float                 # C_t + target_atr_multiple * ATR_t
    proposed_shares: int          # semiedge.risk.sizing.propose_position_size(...).shares
    side: OrderSide = OrderSide.BUY   # anything else is rejected (long-only)


@dataclass(frozen=True, slots=True)
class PositionView:
    symbol: str
    qty: int                      # > 0 (long only); negative/zero is an invalid input
    avg_entry_price: float
    mark_price: float             # latest known price used for exposure (close t in backtest)


@dataclass(frozen=True, slots=True)
class PendingOrderView:
    symbol: str
    side: OrderSide
    remaining_qty: int            # qty - filled_qty, > 0
    valuation_price: float        # limit price if any, else the intent's entry_reference
    is_entry: bool


@dataclass(frozen=True, slots=True)
class PortfolioView:
    mode: TradingMode             # BACKTEST or PAPER; SCANNER is rejected by the risk manager
    as_of: datetime               # decision timestamp (UTC), supplied by caller; never read inside
    session: date                 # decision session t
    equity: float                 # equity at close t (backtest) / account equity (paper)
    start_of_day_equity: float    # equity at close t-1 (backtest) / account.last_equity (paper)
    peak_equity: float            # max equity observed up to and including as_of
    cash: float
    positions: tuple[PositionView, ...]
    pending_orders: tuple[PendingOrderView, ...]
    halts: tuple[Halt, ...]       # active halts (any kind)


@dataclass(frozen=True, slots=True)
class EarningsInfo:
    symbol: str
    next_earnings_session: date | None   # first session on/after the query date with a report
    verified: bool                # False => unknown; treated per RiskLimits.require_verified_earnings_data
    source: str                   # e.g. "none", "<vendor>", "manual_yaml@2026-10-30"


@dataclass(frozen=True, slots=True)
class RiskCheck:
    check_id: str                 # stable id from §2.2, e.g. "R10_gross_exposure"
    passed: bool
    observed: float | None
    limit: float | None
    detail: str


@dataclass(frozen=True, slots=True)
class RiskDecision:
    proposal: EntryProposal
    approved: bool
    approved_shares: int          # 0 if rejected; 1 <= approved_shares <= proposal.proposed_shares if approved
    reasons: tuple[str, ...]      # reason codes; empty iff approved with approved_shares == proposed_shares
    checks: tuple[RiskCheck, ...] # every check evaluated, in §2.2 order
    limits_fingerprint: str       # semiedge.risk.manager.limits_fingerprint(limits) (§2.1, the one definition)
    decided_at: datetime          # == portfolio.as_of
```

Invariants: all dataclasses are frozen; constructors do **not** validate (the risk manager does,
so that invalid input becomes a recorded rejection rather than an exception in the caller).

### 1.1 Proposal builder — `semiedge.risk.proposals` (spec-verbatim; used by backtest AND engine)

```python
def build_entry_proposals(evaluations: Sequence[SignalEvaluation], *, equity: float,
                          limits: RiskLimits, params: MomentumV1Params,
                          decision_session: date, data_as_of: date) -> tuple[EntryProposal, ...]:
    """One EntryProposal per *eligible* evaluation, in (rank, symbol) order.
    entry_reference = ev.close, atr = ev.atr, stop/target = semiedge.strategy.exits.exit_levels(...)
    (must equal ev.proposed_stop/ev.proposed_target within 1e-9 relative, else ValueError — a
    divergence between scanner and exits is a bug, not a rejection), proposed_shares =
    propose_position_size(equity, ev.close, stop, max_risk_per_trade_pct=..., max_position_pct=...).shares.
    Evaluations with proposed_shares == 0 are still emitted (the risk manager records the rejection).
    Raises ValueError if, for an eligible ev, any of ev.session/score/close/atr/proposed_stop/
    proposed_target is None, or ev.session != decision_session (eligible evaluations guarantee
    proposed_stop > 0 and a finite proposed_target > close: momentum_v1._trade_levels, F-026)."""
```
Both the backtest (§3.2 step 6c) and the engine (§10.3 POST_CLOSE) must call this function;
neither may build `EntryProposal`s any other way (parity).

---

## 2. Independent risk manager — `semiedge.risk.manager` (#19, T8)

### 2.1 API

```python
class EarningsCalendar(Protocol):
    def next_earnings(self, symbol: str, on_or_after: date) -> EarningsInfo: ...

class UnavailableEarningsCalendar:
    """Returns verified=False for every symbol. The only implementation until #20 (H1) is decided."""
    def next_earnings(self, symbol: str, on_or_after: date) -> EarningsInfo: ...

class RiskManager:
    def __init__(self, limits: RiskLimits, *, calendar: TradingCalendar,
                 earnings: EarningsCalendar, tradable_symbols: frozenset[str],
                 earnings_waiver: Literal["none", "backtest_unavailable"] = "none") -> None:
        """Raises SafetyViolation if any limit exceeds RISK_LIMIT_CEILINGS (defence in depth,
        re-checked field by field even though RiskLimits validated; also if any allow_* flag is
        not False; also if limits.require_verified_earnings_data is False — fail closed, R16
        is never relaxed). An earnings_waiver != "none" is accepted here but makes R01 reject
        every proposal whose portfolio.mode is not BACKTEST."""

    @property
    def limits(self) -> RiskLimits: ...      # read-only; the validated limits passed in

    def evaluate_entries(self, proposals: Sequence[EntryProposal],
                         portfolio: PortfolioView) -> tuple[RiskDecision, ...]:
        """Deterministic. Returns one decision per proposal, in processing order."""

    def entry_halts(self, portfolio: PortfolioView) -> tuple[Halt, ...]:
        """Halts implied by the portfolio itself (DAILY_LOSS, DRAWDOWN) plus portfolio.halts."""

def limits_fingerprint(limits: RiskLimits) -> str: ...
    # the ONE definition (used by RiskDecision.limits_fingerprint §1, run_backtest §3.4, the store):
    # sha256 hex of json.dumps(limits.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
```

`RiskManager` has no override parameters for any limit: the only limit input is a validated
`RiskLimits`. There is no method that approves an exit; exits are never gated (§2.4).

### 2.2 Checks (evaluated for every proposal, in this order; all are recorded)

Processing order: proposals sorted by `(rank, symbol)`; ties or duplicate ranks are allowed and
broken by symbol. Each **approved** proposal is immediately added to a working copy of the
portfolio as a `PendingOrderView(is_entry=True, valuation_price=entry_reference)` and counts
against every later proposal in the same batch.

| ID | Rule (reject unless …) | Reason code |
|---|---|---|
| R01_mode | `portfolio.mode in {BACKTEST, PAPER}`; waiver only with BACKTEST | `invalid_mode` |
| R02_inputs | every float in proposal/portfolio finite; `equity, start_of_day_equity, peak_equity > 0`; `peak_equity >= equity`; `cash >= 0`; position/pending qty > 0 | `invalid_input` |
| R03_halts | no active persisted halt in `portfolio.halts` (DAILY_LOSS/DRAWDOWN computed from the portfolio are R14/R15) | `halt_active:<kind>` |
| R04_long_only | `side == BUY` | `not_long` |
| R05_tradable | symbol ∈ `tradable_symbols` | `not_tradable` |
| R06_fresh | `data_as_of == decision_session == portfolio.session` | `stale_data` |
| R07_levels | `stop`, `entry_reference`, `target`, `atr` finite; `0 < stop < entry_reference < target`, `atr > 0` (same boundary as F-026) | `invalid_levels` |
| R08_duplicate | no position, no pending order (either side), no earlier proposal in this batch for the symbol | `duplicate_or_conflict` |
| R09_max_positions | `#positions + #pending entries < max_positions` (pending entries of the working copy, which already include this batch's earlier approvals; nothing is counted twice) | `max_positions` |
| R10_risk_per_trade | sized: `propose_position_size(equity, entry_reference, stop, …).shares_by_risk` | (downsize) |
| R11_position_cap | sized: `propose_position_size(equity, entry_reference, stop, …).shares_by_allocation` | (downsize) |
| R12_gross_exposure | sized: `floor((equity·max_gross_exposure_pct − E) / entry_reference)`, `E = Σ qty·mark_price (positions) + Σ remaining_qty·valuation_price (pending BUY)` | (downsize) |
| R13_cash | sized: `floor((cash − Σ pending BUY notional) / entry_reference)` (no leverage, no margin) | (downsize) |
| R14_daily_loss | `equity / start_of_day_equity − 1 > −daily_loss_halt_pct` | `daily_loss_halt` |
| R15_drawdown | `equity / peak_equity − 1 > −drawdown_halt_pct` | `drawdown_halt` |
| R16_earnings | `verified` and (`next_earnings_session is None` or `k > earnings_blackout_sessions`) where `k` = number of XNYS sessions strictly after `decision_session` up to and including `next_earnings_session` | `earnings_blackout` / `earnings_data_unavailable` |
| R17_min_shares | `approved_shares = min(proposed_shares, R10, R11, R12, R13) >= 1` | `size_below_one_share` |

- R16 with `verified=False`: if `limits.require_verified_earnings_data` → reject
  `earnings_data_unavailable`. With `earnings_waiver="backtest_unavailable"` (BACKTEST only) the
  check is recorded as `passed=True, detail="waived: no point-in-time earnings data"` and the
  backtest result is labelled (§3.6). There is no waiver path in PAPER. Since
  `require_verified_earnings_data=False` is refused at construction (§2.1), and the only
  calendar until owner issue #20 provides a verified source is `UnavailableEarningsCalendar`,
  **every new PAPER entry is rejected by R16 (`earnings_data_unavailable`) until #20 is resolved**
  (§19 item 1). This only tightens; no PR may relax it.
- R10/R11 call `semiedge.risk.sizing.propose_position_size(equity, entry_reference, stop,
  max_risk_per_trade_pct=limits.max_risk_per_trade_pct, max_position_pct=limits.max_position_pct)`
  itself (exact `Fraction` floors); the risk manager never re-implements this arithmetic in
  float. If the result has `shares == 0`, its `SizingResult.reason` is carried into the
  `RiskCheck.detail` of R10/R11 (whichever is binding).
- Downsizing (R10–R13) may only lower the size. If `approved_shares < proposed_shares` the
  binding check IDs are appended to `reasons` as `downsized:<check_id>` while `approved=True`.
- Pending SELL orders never reduce exposure or free cash (conservative); only filled exits do.
- `E` in R12 and the pending notional in R13 include the approved proposals earlier in the same
  batch (the working copy above).
- Exposure is valued at `entry_reference` (`C_t`) because entries are market orders at the next
  open (§3.2, §6.3). Known residual: an opening gap can make realised exposure exceed a cap by
  the gap percentage. The risk manager never force-sells; a cap already exceeded simply blocks
  further entries (R12/R13 yield 0 shares). This residual is reported by the backtest (§3.6
  `cap_overshoots`) and by the paper engine (logged + notified).

### 2.3 Invariants
- Pure and deterministic: no clock reads (`decided_at = portfolio.as_of`), no randomness, no I/O,
  no logging side effects that change results. Same inputs → byte-identical
  `[asdict(d) for d in decisions]`.
- Never approves more shares than any of R10–R13 allow; never approves when any reject check
  fails; never approves a non-BUY; never mutates inputs.
- `RiskLimits` beyond ceilings → `SafetyViolation` at construction (not a rejection).
- Exceptions inside a check are caught per proposal and become `reasons=("internal_error",)`,
  `approved=False` (fail closed), with the exception type in `RiskCheck.detail`.

### 2.4 Exits are never gated
Halts (all kinds) block **new entries only**. Protective stops, targets, time exits and manual
flattening are never blocked by the risk manager. (Order submission as a whole can still be
disabled by `ENABLE_ORDER_SUBMISSION=false`; see §6.)

### 2.5 Persistence
The caller persists every `RiskDecision` (paper: `risk_decisions` table, §5.2; backtest:
`BacktestResult.risk_decisions`). Decisions are persisted **before** any order intent derived
from them is created.

### 2.6 Acceptance tests (`tests/safety/test_risk_manager.py`, all `@pytest.mark.safety`)
- One test per check R01–R17 showing pass and fail at the boundary (`>` vs `≥` as written).
- Pending orders count: a portfolio at 16% gross with a pending 4% entry rejects/downsizes a
  new 5% proposal to 0 (S-004).
- Batch accumulation: five equal proposals with `max_positions=4` → exactly four approved, fifth
  `max_positions`; order independence of input sequence (shuffled input → same decisions).
- Missing/NaN/inf/negative inputs → rejection with `invalid_input`, never an exception.
- `RiskLimits.model_construct(max_position_pct=0.5)` (bypassing validation) → `SafetyViolation`.
- `RiskLimits(require_verified_earnings_data=False)` → `RiskManager(...)` raises (§2.1).
- R10/R11 equal `propose_position_size(...).shares_by_risk/.shares_by_allocation` on the same
  inputs; a 0-share result carries `SizingResult.reason` in the check detail.
- Waiver with `mode=PAPER` → every proposal rejected `invalid_mode`.
- Earnings: unverified → reject in PAPER; `k = 0..N` reject, `k = N+1` pass; holiday-spanning
  windows use the calendar.
- Determinism: 1,000 seeded random portfolios evaluated twice → identical outputs.
- Property: for random valid inputs, post-approval gross exposure ≤ cap and count ≤ max.

---

## 3. Backtest engine — `semiedge.backtest` (#15, T5)

### 3.1 Configuration (`config/backtest.yaml`, model `BacktestSettings` in `semiedge.config.models`)

```python
class CostSettings(_Frozen):
    slippage_bps: float           # finite, 0 <= bps < 10_000 (validator, F-040); adverse, applied to
                                  # every MARKET-type fill (entry, stop, time exit, end of window)
    commission_per_share: float   # >= 0
    commission_per_order: float   # >= 0
    sell_fee_rate: float          # >= 0; fraction of sell notional (regulatory fees); source cited in YAML comment

class BacktestSettings(_Frozen):
    initial_equity: float         # > 0
    costs: CostSettings
    earnings_policy: Literal["require_data", "waive_unavailable"]
    # require_data: a run without verified point-in-time earnings data fails with BacktestInputError.
    # waive_unavailable: blackout R16 waived (BACKTEST only) and the result is labelled.

def load_backtest_settings(config_dir: Path) -> BacktestSettings: ...   # in semiedge.config.loader
```
All fields are **required** in the YAML (no model defaults; a missing key → `ConfigError`).
Every numeric field must be finite (`nan`/`inf` → `ConfigError`); `slippage_bps` outside
`[0, 10_000)` → `ConfigError` (at 10 000 bps a sell fills at 0, above it at a negative price).
This is input-domain validation, not a cost choice.
The YAML values are set by T5 in its PR, each with a comment stating its source; no value may be
chosen or changed after looking at backtest output (record changes in DECISIONS.md).

### 3.2 Event timeline (the one fill model; deterministic)

For every XNYS session `d` in `[start, end]` (from `TradingCalendar.sessions_in_range`, never
from a frame index), in this exact order, symbols processed in ascending order within each step:

1. **Open of d — pending exits.** Time exits decided at `d−1` fill at `open_d · (1 − slip)`.
2. **Open of d — gap-through stops/targets** for positions held before `d`:
   if `open_d ≤ stop` → exit at `open_d · (1 − slip)`, reason `stop_gap`;
   elif `open_d ≥ target` → exit at `target` (limit order; gap improvement ignored —
   conservative), reason `target_gap`.
3. **Open of d — pending entries** decided at `d−1` (market-on-open):
   if no bar for the symbol on `d` → cancelled, `entry_no_bar`;
   if cash after the entry's costs would be < 0 → cancelled `entry_insufficient_cash`;
   else fill `approved_shares` at `fill = open_d · (1 + slip)` (an `opg` market order cannot be
   withdrawn after the auction, so a gap does **not** cancel it);
   then, if `fill ≤ stop` or `fill ≥ target` (the **slipped fill price**, the same quantity the
   paper engine compares, `filled_avg_price` in §6.3), the entry is invalidated: the position is
   immediately flattened at `open_d · (1 − slip)` with exit reason `entry_gap_flatten` (both
   legs' costs charged). Example: `open_d = 99`, `target = 100`, 200 bps → `fill = 100.98 ≥ 100`
   → invalidated, although `open_d < target`.
   Stop and target stay the decision-time levels from `semiedge.strategy.exits` (anchored at
   `C_t`, not at the fill price).
4. **Intraday of d** for every open position (including those entered at step 3 and not
   flattened):
   if `low_d ≤ stop` → exit at `stop · (1 − slip)`, reason `stop`
   (**stop first** when both `low_d ≤ stop` and `high_d ≥ target` — same-bar ambiguity
   resolves to the stop);
   elif `high_d ≥ target` → exit at `target`, reason `target`.
5. **Close of d — mark to market** at `close_d`; equity = cash + Σ qty·close_d. A held symbol
   without a bar on `d` is marked at its last close and the session is recorded in
   `data_gaps`; no stop/target is evaluated for it that day.
6. **Decision at close d** (skipped on the final session `end`):
   a. time exits: positions with `held_sessions(entry_session, d) ≥ max_holding_sessions` →
      pending market exit for `d+1` (entry session counts as held session 1);
   b. signals, exactly:
      `evaluate_universe(bars≤d, universe.regime_benchmark, params, d,
      tradable=universe.tradable_symbols, quality={s: assess_quality(bars[s], symbol=s, as_of=d,
      calendar=calendar, lookback_sessions=params.required_history(), feed=<BacktestData.feed>,
      max_missing_sessions=data.max_missing_sessions_in_lookback) for s in tradable ∪ {regime}},
      expected_sessions=calendar.sessions_in_range(first_bar_session, d))`, where here `data` is
      the `DataConfig` (`request.data_settings`, not `BacktestData`) and `first_bar_session` the
      earliest session in the supplied bars. Without `expected_sessions` every symbol is ineligible; precomputed features
      likewise use `compute_features(..., expected_sessions=...)` with the same sessions;
   c. proposals: `build_entry_proposals(evaluations, equity=equity_d, …, decision_session=d,
      data_as_of=d)` (§1.1);
   d. `RiskManager.evaluate_entries(proposals, PortfolioView(mode=BACKTEST, session=d,
      equity=equity_d, start_of_day_equity=equity_{d−1}, peak_equity=max equity ≤ d, …))`;
      positions are marked at `close_d`; pending entries are those approved at this decision
      only (all earlier pending entries were resolved at step 3); `equity_{d−1}` for the first
      decision is `initial_equity`; `as_of = session_close(d) + bar_settle_minutes`;
   e. approved → pending entries for `d+1`, with `approved_shares`.
7. **End of window:** after step 5 of `end`, open positions are closed at
   `close_end · (1 − slip)`, reason `end_of_window`, with the normal sell costs, and labelled.
   The liquidation **replaces** the `end` `EquityPoint` (it is not appended): its `cash` is the
   cash after liquidation proceeds and costs, `equity == cash`, `gross_exposure == 0`,
   `positions == 0`. The final equity therefore includes the liquidation costs.

`slip = slippage_bps / 10_000`. Costs per fill: `commission_per_order + commission_per_share·qty`,
plus `sell_fee_rate · notional` on sells. Cash is debited/credited at fill.
Numeric guard (F-040): every computed fill price must be finite and `> 0`, and every computed
cost, notional and cash/equity value finite; otherwise the run aborts with
`ValueError` naming the `trade_id` (or symbol and session) and the offending quantity. No
clamping, no skipped trade, no partial result is saved.
Trade levels (same boundary as F-026): a proposal is acted on only if `stop`, `entry_reference`
and `target` are finite with `0 < stop < entry_reference < target`; otherwise it is rejected
by R07 (`invalid_levels`, §2.2) and never becomes a pending entry.

Halts in backtest (same semantics as paper, §8): DAILY_LOSS is evaluated at each decision
from `equity_d / equity_{d−1}`; DRAWDOWN latches at the first decision where it triggers and
blocks entries for the rest of the run (there is no human to clear it); both are recorded with
their session in `BacktestResult.halts`.

### 3.3 `semiedge.strategy.exits` (spec-verbatim; shared by backtest and paper)

```python
@dataclass(frozen=True, slots=True)
class ExitLevels:
    stop: float
    target: float

def exit_levels(entry_reference: float, atr: float, params: MomentumV1Params) -> ExitLevels:
    """stop = C_t − stop_atr_multiple·ATR_t; target = C_t + target_atr_multiple·ATR_t.
    Raises ValueError on non-finite/non-positive inputs, stop <= 0, or a result that is not
    finite with 0 < stop < entry_reference < target (F-026 boundary)."""

def held_sessions(entry_session: date, session: date, calendar: TradingCalendar) -> int:
    """Number of XNYS sessions in [entry_session, session] inclusive; ValueError if session < entry_session."""

def time_exit_due(entry_session: date, session: date, max_holding_sessions: int,
                  calendar: TradingCalendar) -> bool:
    """True iff held_sessions(...) >= max_holding_sessions (decision at close of `session`)."""
```
This counting of `max_holding_sessions` (entry session = held session 1, decided at the close,
exit at the next open) is **new in this spec**: `momentum_v1.md` defines the parameter but not
how it is counted. It is fixed before any backtest result exists and recorded as ADR-005 in
`docs/DECISIONS.md`; changing it is a spec change.

### 3.4 Engine API

```python
class EntryGate(Protocol):   # satisfied by semiedge.risk.manager.RiskManager
    @property
    def limits(self) -> RiskLimits: ...
    def evaluate_entries(self, proposals: Sequence[EntryProposal],
                         portfolio: PortfolioView) -> tuple[RiskDecision, ...]: ...

class BacktestInputError(ValueError): ...

@dataclass(frozen=True)
class BacktestData:
    bars: Mapping[str, pd.DataFrame]   # every universe symbol incl. regime + comparison benchmarks
    feed: DataFeed
    adjustment: Adjustment
    source: DataSource
    def fingerprint(self) -> str: ...  # sha256 over symbol, session, OHLCV (sorted, float repr) + feed/adjustment/source

@dataclass(frozen=True)
class BacktestRequest:
    start: date                        # first session on which a FILL may occur
    end: date                          # last session (marks; no decisions)
    universe: UniverseConfig
    params: MomentumV1Params
    risk: RiskLimits
    settings: BacktestSettings
    data_settings: DataConfig
    command: str                       # exact CLI that reproduces the run (ground rule 5)

def run_backtest(request: BacktestRequest, data: BacktestData, *, gate: EntryGate,
                 calendar: TradingCalendar) -> BacktestResult: ...
```
- `gate` is required (no default). The CLI and walk-forward always pass a real `RiskManager`;
  fake gates are allowed only under `tests/`. A test asserts the CLI path wires `RiskManager`.
  `run_backtest` raises `ValueError` unless the gate's limits equal `request.risk`, compared as
  `limits_fingerprint(gate.limits) == limits_fingerprint(request.risk)` (§2.1; `EntryGate`
  therefore also exposes `limits: RiskLimits`, which `RiskManager` provides).
- Warm-up: the decision for the first fill session `start` happens at `previous_session(start)`;
  data must cover `required_history()` sessions before it (else that symbol is ineligible; if the
  **regime benchmark** lacks it → `BacktestInputError`, fail closed for the whole run).
- Validation before running (each → `BacktestInputError` with reason): every frame passes
  `validate_bar_frame`; one feed/adjustment/source for all symbols (never mixed, S-003);
  `DataSource.SYNTHETIC` and `DataSource.FIXTURE` never mixed with others; `start ≤ end`, both sessions; synthetic data is
  allowed only when the result is labelled synthetic; `earnings_policy="require_data"` with only
  `UnavailableEarningsCalendar` available → error.
- Features may be precomputed once per symbol with `compute_features` (truncation-safe by #12
  tests); the engine must still only *read* rows `≤ d` at decision `d`.

```python
def load_backtest_data(store: ParquetBarStore, universe: UniverseConfig, data: DataConfig, *,
                       start: date, end: date, calendar: TradingCalendar,
                       corporate_actions: CorporateActionStore | None) -> BacktestData: ...
```
Reads `{tradable ∪ regime_benchmark ∪ comparison_benchmarks}` with the configured feed and
adjustment from the cache only (no network). A missing regime/comparison benchmark series →
`BacktestInputError`. When `corporate_actions` is given (T17), symbols failing
`check_adjustment_consistency` are dropped from the tradable set and listed in the result
(`labels` gains `ADJUSTMENT-INCONSISTENT SYMBOLS EXCLUDED: …`); `None` is allowed only until
T17 merges and adds the label `CORPORATE ACTIONS NOT CHECKED`.

### 3.5 Results (`semiedge.backtest.results`)

```python
ExitReason = Literal["stop", "stop_gap", "target", "target_gap", "time", "entry_gap_flatten",
                     "end_of_window"]

@dataclass(frozen=True)
class Trade:
    trade_id: str                      # f"{symbol}-{entry_session:%Y%m%d}"
    symbol: str
    signal_session: date
    entry_session: date
    entry_reference: float             # C_t at the signal session
    entry_price: float                 # incl. slippage
    shares: int
    stop: float
    target: float
    exit_session: date
    exit_price: float                  # incl. slippage where applicable
    exit_reason: ExitReason
    held_sessions: int
    costs: float                       # commissions + fees, both legs
    gross_pnl: float                   # (exit_price - entry_price) * shares
    net_pnl: float                     # gross_pnl - costs
    r_multiple: float                  # net_pnl / (shares * (entry_reference - stop))
    score: float
    rank: int

@dataclass(frozen=True)
class CancelledEntry:
    symbol: str; signal_session: date; session: date; reason: str

@dataclass(frozen=True)
class EquityPoint:
    session: date; equity: float; cash: float; gross_exposure: float; positions: int

@dataclass(frozen=True)
class BacktestResult:
    schema_version: int                # 1
    run_id: str                        # semiedge.reporting.new_run_id(...) ("YYYYMMDDTHHMMSSZ-<8 hex>"); NOT part of the deterministic payload
    command: str
    git_sha: str | None
    semiedge_version: str
    config_fingerprint: str            # app config fingerprint + BacktestSettings + RiskLimits
    data_fingerprint: str
    start: date; end: date
    labels: tuple[str, ...]            # §3.6; never empty for real-data runs
    trades: tuple[Trade, ...]
    cancelled_entries: tuple[CancelledEntry, ...]
    equity_curve: tuple[EquityPoint, ...]
    risk_decisions: tuple[RiskDecision, ...]
    halts: tuple[tuple[date, HaltKind, str], ...]
    data_gaps: tuple[tuple[date, str], ...]
    cap_overshoots: tuple[tuple[date, str, str], ...]   # (session, symbol, check id) after gap fills
    def deterministic_payload(self) -> dict[str, object]: ...   # everything except run_id/git_sha
    def save(self, directory: Path) -> Path: ...  # {data_dir}/reports/backtests/{start}_{end}_{run_id}.json, exclusive create
```
JSON: floats as `repr`-round-trippable numbers; `NaN`/`inf` forbidden (`allow_nan=False`);
dates ISO. Synthetic runs save under `{data_dir}/synthetic/reports/backtests/`. Every artifact
file is created exclusively (`open(path, "x")`, as `semiedge.reporting.persist_report` does);
an existing file is never overwritten.

### 3.6 Mandatory labels (rendered first in every report, JSON and text)
- `SURVIVORSHIP-BIASED UNIVERSE: current hand-picked list, not point-in-time` (always, while
  `universe.yaml` is not point-in-time; S-001).
- `PRICE RETURN: dividends excluded` (adjustment `split`; ADR-007 (D-007)).
- `EARNINGS BLACKOUT NOT APPLIED` when waived.
- `SYNTHETIC DATA — NOT MARKET DATA` for synthetic sources: `semiedge.reporting.SYNTHETIC_BANNER`,
  imported, never redefined.
- `FIXTURE DATA — OFFLINE IMPORT, PROVENANCE: <manifest>` for `DataSource.FIXTURE` (never mixed
  with `ALPACA` rows; same rule as synthetic).
- `GAP OVERSHOOT OF EXPOSURE CAPS: <n> sessions` when `cap_overshoots` is non-empty.
- `OPEN POSITIONS CLOSED AT WINDOW END` when any `end_of_window` exit exists.

### 3.7 No-look-ahead obligations
1. Decision at `d` reads only bars `≤ d`; fills at `d+1` read only `open_{d+1}` (step 3) and the
   intraday `high/low` of the fill day (step 4).
2. Sizing equity is `equity_d` (known after close `d`).
3. Session progression comes from the calendar; a missing bar is never treated as a holiday.
4. Split-adjusted prices: conditions are scale-invariant; dollar P&L is invariant to the constant
   rescale except per-share commission and whole-share rounding — therefore the default cost
   model should not rely on `commission_per_share` (documented limitation).

### 3.8 Acceptance tests (`tests/unit/backtest/`, `tests/safety/test_backtest_lookahead.py`)
- Hand-built fixture scenarios (≤ 30 sessions, 2–3 symbols) with exact expected trades/equity:
  gap-through stop fills at `open·(1−slip)`; same-bar stop+target → stop; gap above target →
  target price; entry gap below stop and entry gap above target → filled then `entry_gap_flatten`; time exit at open of entry+`max_holding_sessions`;
  missing fill-day bar → `entry_no_bar`; end-of-window close.
- Slipped-fill invalidation: `open = 99`, `target = 100`, `slippage_bps = 200` → entry fill
  `100.98`, then `entry_gap_flatten` (and with `slippage_bps = 0` the same bar is not invalidated).
- End of window: the last `EquityPoint` is the post-liquidation point (one point for `end`, not
  two), `positions == 0`, `gross_exposure == 0`, `equity == cash`, and equals the hand-computed
  value including the liquidation sell costs.
- Levels: proposals with `stop <= 0`, `stop >= entry_reference`, `target <= entry_reference` or a
  non-finite level → R07 `invalid_levels`, no pending entry.
- Slippage domain (F-040): `slippage_bps` = `10000`, `1e6`, `nan`, `inf` (and `-1`) →
  `ConfigError` from `BacktestSettings`; `9999.99` is accepted. A cost overflow (e.g.
  `commission_per_share = 1e308`, a valid finite config value, with `qty ≥ 2`) → `ValueError`
  naming the trade, no result saved.
- **Future poisoning** (safety): run on data `D`; run again with every bar after session `T`
  replaced by random garbage (and with sessions after `T` deleted) → all trades with
  `exit_session ≤ T`, all decisions at sessions `< T` and the equity curve up to `T−1` are
  identical.
- Determinism: two runs → identical `deterministic_payload()`; insensitive to dict ordering of
  `bars`.
- Mixed feed / mixed synthetic / regime missing history → `BacktestInputError` with reason.
- Costs: zero-cost run vs `slippage_bps=10` run on the same fixture differ by the exact
  hand-computed amount.
- Risk parity: the backtest never holds more than `max_positions`, never exceeds gross cap at
  decision time, and a fixture with a −1.2% day yields no entries decided that day.
- Labels present; JSON round-trips; `save` refuses to overwrite.

---

## 4. Metrics and benchmarks — `semiedge.backtest.metrics`, `.benchmarks` (#16, T6)

### 4.1 API

```python
@dataclass(frozen=True)
class Metric:
    value: float | None
    reason: str | None            # set iff value is None, e.g. "no_losing_trades", "fewer_than_2_returns"

@dataclass(frozen=True)
class PerformanceMetrics:
    sessions: int
    net_return: Metric            # E_end / E_0 - 1
    cagr: Metric                  # (E_end / E_0) ** (252 / sessions) - 1
    max_drawdown: Metric          # min_d (E_d / max_{u<=d} E_u - 1)  (<= 0)
    max_drawdown_sessions: Metric # longest peak-to-recovery length (sessions); an unrecovered
                                  # drawdown counts to the last session
    sharpe: Metric                # mean(r) / std(r, ddof=1) * sqrt(252); rf = 0 (stated)
    sortino: Metric               # mean(r) / sqrt(mean(min(r, 0)**2)) * sqrt(252); None "no_downside_returns"
    trade_count: int
    win_rate: Metric              # share of trades with net_pnl > 0
    profit_factor: Metric         # sum(net_pnl > 0) / |sum(net_pnl < 0)|; None if no losers
    expectancy: Metric            # mean net_pnl per trade ($)
    expectancy_r: Metric          # mean r_multiple
    avg_holding_sessions: Metric
    exposure: Metric              # mean(gross_exposure_d / equity_d)
    turnover: Metric              # sum(traded notional) / mean(equity) * 252 / sessions
    exit_reason_counts: Mapping[str, int]
    total_costs: float

@dataclass(frozen=True)
class BenchmarkComparison:
    symbol: str                   # "SMH", "QQQ" (UniverseConfig.comparison_benchmarks)
    metrics: PerformanceMetrics   # buy-and-hold: buy at open of `start` (+slip), mark at closes
    excess_net_return: Metric     # strategy.net_return - benchmark.net_return
    correlation: Metric           # of daily returns
    beta: Metric                  # cov(r_s, r_b) / var(r_b)

@dataclass(frozen=True)
class RegimeBreakdown:
    regime_on: PerformanceMetrics     # daily returns on sessions where regime close > SMA(regime_sma_window) at close d-1
    regime_off: PerformanceMetrics

def compute_metrics(result: BacktestResult) -> PerformanceMetrics: ...
def compare_benchmarks(result: BacktestResult, data: BacktestData,
                       settings: BacktestSettings) -> tuple[BenchmarkComparison, ...]: ...
def regime_breakdown(result: BacktestResult, data: BacktestData,
                     params: MomentumV1Params) -> RegimeBreakdown: ...

@dataclass(frozen=True)
class PeriodMetrics:
    label: str                    # "2019", "2018Q4", "drawdown_1", ...
    start: date; end: date
    metrics: PerformanceMetrics   # computed on the equity sub-series, re-based to 1.0 at start
    trade_count: int              # trades with entry_session in [start, end]

def period_breakdown(result: BacktestResult, *,
                     periods: Mapping[str, tuple[date, date]]) -> tuple[PeriodMetrics, ...]: ...
def worst_drawdowns(result: BacktestResult, n: int = 3) -> tuple[PeriodMetrics, ...]: ...
    # the n deepest peak-to-trough episodes (non-overlapping), labelled drawdown_1..n
```
Every metrics report (S-002) contains: whole-period metrics, `period_breakdown` per calendar year
**and** per `STRESS_PERIODS` (§15) intersecting the run, `worst_drawdowns(3)`, the regime
breakdown, and the benchmark comparison over the same windows. A period with no equity points
is reported with `Metric(None, "no_sessions_in_period")`, never dropped.
- `r_d = E_d / E_{d−1} − 1` over consecutive equity points; `E_0 = initial_equity` before the
  first session. 252 is the stated annualisation constant.
- Undefined → `Metric(None, reason)`; NaN/inf never appear. Benchmarks use the same feed,
  adjustment, session range and cost settings as the strategy; a missing benchmark bar inside the
  range → that comparison is `None` with reason, never forward-filled silently.
- Regime classification for session `d` uses data `≤ d−1` (no look-ahead into the day scored).

### 4.2 Acceptance tests
- Hand-checked fixtures (equity series of ≤ 10 points, ≤ 6 trades) with every metric computed
  by hand in the test docstring; edge cases: no trades, all winners (profit factor `None`),
  constant equity (Sharpe `None`, reason `zero_volatility`), one session.
- Benchmark buy-and-hold on a 3-session fixture matches hand computation incl. slippage.
- Metric definitions are documented in module docstrings matching this section.

---

## 5. Persistence — `semiedge.state` (#21, T9)

### 5.1 Store

```python
# semiedge/state/models.py (spec-verbatim; frozen dataclasses mirroring the §5.2 rows)
OrderKind = Literal["entry", "protection", "time_exit", "flatten"]
OrderStatus = Literal[                     # §6.4
    "staged", "submitting", "submitted", "unknown", "accepted", "partially_filled",
    "cancel_pending", "filled", "canceled", "expired", "rejected",
    "expired_unsubmitted", "withdrawn", "dry_run"]
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"filled", "canceled", "expired", "rejected", "expired_unsubmitted", "withdrawn", "dry_run"})

@dataclass(frozen=True, slots=True)
class OrderIntent:                         # inserted with status "staged"
    client_order_id: str; kind: OrderKind; symbol: str; side: OrderSide; qty: int
    order_type: Literal["market", "limit"]; time_in_force: Literal["opg", "day", "gtc"]
    order_class: Literal["simple", "oco"]
    limit_price: float | None; stop_price: float | None    # protection: limit=target, stop=stop
    decision_session: date; valid_until: datetime
    risk_decision_id: int | None           # required for kind="entry"
    parent_client_order_id: str | None     # protection/time_exit/flatten -> the entry of the position
    created_at: datetime

@dataclass(frozen=True, slots=True)
class OrderRecord:                         # one orders row
    intent: OrderIntent; status: OrderStatus; broker_order_id: str | None
    filled_qty: int; filled_avg_price: float | None; legs_json: str
    submitted_at: datetime | None; updated_at: datetime; last_error: str | None

FillSource = Literal["activity", "cumulative"]
FillStatus = Literal["final", "provisional", "superseded"]

@dataclass(frozen=True, slots=True)
class FillRecord:                          # §6.4 "Fill ingestion" (F-038)
    fill_key: str                          # activity: broker activity id; cumulative: f"{client_order_id}:{cum_qty}"
    client_order_id: str; ts: datetime; qty: int; price: float
    source: FillSource                     # activity rows are "final"; cumulative rows are "provisional"
    status: FillStatus                     # "superseded": kept for audit, excluded from every quantity
    cum_qty: int                           # this order's local cumulative after the row was ingested
    superseded_at: datetime | None

@dataclass(frozen=True, slots=True)
class CorporateActionAdjustment:           # one corporate_action_adjustments row (§9.4, F-039)
    symbol: str; ex_date: date; event_id: str; new_shares: int; old_shares: int  # exact ratio (F-042)
    qty_before: int; qty_after: int; applied_at: datetime
    # non-fill quantity delta = qty_after - qty_before

@dataclass(frozen=True, slots=True)
class PositionRecord:                      # one positions row
    symbol: str; qty: int; avg_entry_price: float; entry_session: date
    entry_client_order_id: str; entry_reference: float; atr: float; stop: float; target: float
    split_ratio: float; status: Literal["open", "exiting", "closed"]
    opened_at: datetime; closed_at: datetime | None; updated_at: datetime

@dataclass(frozen=True, slots=True)
class HaltRecord:
    id: int; kind: HaltKind; reason: str; created_at: datetime; created_by: str
    cleared_at: datetime | None; cleared_by: str | None
    def as_halt(self) -> Halt: ...

class NotificationCategory(StrEnum):       # re-exported by semiedge.notify
    SIGNAL = "signal"; ORDER = "order"; FILL = "fill"; EXIT = "exit"; SUMMARY = "summary"
    RISK = "risk"; HALT = "halt"; BROKER = "broker"; SYSTEM = "system"

@dataclass(frozen=True, slots=True)
class Notification:                        # re-exported by semiedge.notify
    category: NotificationCategory
    mode: TradingMode                      # __post_init__: LIVE -> ValueError (the only validating ctor)
    title: str                             # truncated to 200 chars after sanitising
    body: str                              # truncated to 1800 chars after sanitising
    created_at: datetime

@dataclass(frozen=True, slots=True)
class EquitySnapshot:                      # one equity_snapshots row; built by the engine from
    ts: datetime; session: date            # semiedge.broker.account.AccountSnapshot (equity,
    equity: float; last_equity: float      # last_equity, cash, long_market_value), so that
    cash: float; long_market_value: float  # semiedge.state never imports semiedge.broker
    source: Literal["broker_close", "broker_intraday"]   # (not even under TYPE_CHECKING, §0.2)

# ReconciliationReport / Discrepancy / DiscrepancyKind (§7) are also defined here, so that the
# store never imports semiedge.execution; semiedge.execution.reconcile re-exports them.

@runtime_checkable
class StateReader(Protocol):               # F-037: the ONE read surface used by diff_state (§7);
    # StateStore and ReadOnlyStateStore implement every method with exactly these signatures,
    # parameter names and return types (no adapters, no positional-only variants).
    @property
    def store_id(self) -> str: ...
    def data_source(self) -> Literal["real", "synthetic"]: ...
        # F-043: the DB's source class, recorded at creation; §9.4 split verification compares a
        # CorporateAction's source class with this (SYNTHETIC/FIXTURE ↔ "synthetic", else "real").
    def orders(self, *, statuses: Collection[OrderStatus] | None = None, symbol: str | None = None,
               kinds: Collection[OrderKind] | None = None,
               limit: int | None = None) -> list[OrderRecord]: ...
        # filters AND-combined; None = no filter; an empty collection matches nothing.
        # limit None -> all rows, ordered (created_at, client_order_id) ascending;
        # limit set -> the newest `limit` rows, ordered (created_at, client_order_id) descending.
        # limit must be None or an int > 0 (type(limit) is int; bool, 0, negatives -> ValueError).
    def fills(self, *, client_order_id: str | None = None, include_superseded: bool = False,
              limit: int | None = None) -> list[FillRecord]: ...
        # same ordering/limit rules as orders(), keyed on (ts, fill_key)
    def positions(self, *, include_closed: bool = False) -> tuple[PositionRecord, ...]: ...
    def active_halts(self) -> tuple[HaltRecord, ...]: ...
    def corporate_action_adjustments(self, *, symbol: str | None = None
                                     ) -> tuple[CorporateActionAdjustment, ...]: ...
        # ordered (symbol, ex_date, event_id)

# semiedge/state/store.py
class StateStoreError(RuntimeError): ...

class StateStore:                          # the single WRITER (engine process; CLI when engine stopped); implements StateReader
    def __init__(self, path: Path, *, data_source: Literal["real", "synthetic"]) -> None: ...
        # takes the writer lock (§5.3), creates/migrates, sets PRAGMAs;
        # an existing DB whose meta.data_source differs -> StateStoreError
    @property
    def store_id(self) -> str: ...         # uuid4 hex created once at initialisation (meta table)
    def transaction(self) -> AbstractContextManager[None]: ...
        # BEGIN IMMEDIATE ... COMMIT/ROLLBACK; re-entrant (nested calls join the outer transaction).
        # Every method below runs in its own transaction unless called inside one.
    def close(self) -> None: ...           # also releases the writer lock
    # runs / heartbeats
    def start_run(self, run_id: str, kind: str, mode: TradingMode, fingerprint: str,
                  git_sha: str | None, now: datetime) -> None: ...
    def finish_run(self, run_id: str, status: Literal["ok", "failed", "aborted", "skipped"],
                   detail: str, now: datetime) -> None: ...
    def heartbeat(self, component: str, status: str, detail: str, now: datetime) -> None: ...
    # decisions
    def record_signals(self, run_id: str, session: date,
                       evaluations: Sequence[SignalEvaluation], *,
                       sizes: Mapping[str, SizingResult]) -> None: ...
        # payload_json = json.dumps(evaluation_to_dict(ev, sizes.get(ev.symbol)), allow_nan=False,
        # sort_keys=True): an explicit dict conversion, never asdict()/repr of the dataclass.
        # semiedge.reporting.signal_report exposes the public evaluation_to_dict(evaluation, size),
        # used by both report_to_dict and this store; the first PR that needs it extracts it from
        # report_to_dict without changing the report JSON (existing report tests stay green).
    def record_risk_decisions(self, run_id: str,
                              decisions: Sequence[RiskDecision]) -> tuple[int, ...]: ...  # row ids, same order
    def risk_decision(self, decision_id: int) -> RiskDecision: ...
    # orders (every status change goes through transition_order; there is no other UPDATE path)
    def insert_order_intent(self, intent: OrderIntent) -> None: ...   # status "staged" + event row
    def next_attempt(self, kind: OrderKind, decision_session: date, symbol: str) -> int: ...  # §6.2
    def transition_order(self, client_order_id: str, to_status: OrderStatus, *, now: datetime,
                         event: str, broker_order_id: str | None = None,
                         broker_status: str | None = None, filled_qty: int | None = None,
                         filled_avg_price: float | None = None, legs_json: str | None = None,
                         detail: Mapping[str, object] | None = None) -> None: ...
        # validates against semiedge.state.order_status.ALLOWED (else StateStoreError);
        # self-transitions are allowed only to record new fills/legs; filled_qty may only increase;
        # writes orders + order_events in one transaction
    def get_order(self, client_order_id: str) -> OrderRecord | None: ...
    # StateReader (signatures exactly as in models.StateReader): orders, fills, positions,
    # active_halts, corporate_action_adjustments, store_id
    def ingest_order_fills(self, client_order_id: str, *, broker_filled_qty: int,
                           broker_avg_price: float | None,
                           activities: Sequence[BrokerFillInput] | None,
                           now: datetime) -> FillIngestResult: ...
        # §6.4 "Fill ingestion" (F-038); one transaction per broker order; activities=None means
        # the activities endpoint was unavailable this cycle. Raises StateStoreError (caller sets a
        # RECONCILIATION halt, FILL_MISMATCH) if the local cumulative would exceed broker_filled_qty.
        # BrokerFillInput(activity_id: str, qty: int, price: float, ts: datetime) is defined in
        # models (the store never imports semiedge.broker); FillIngestResult(added_qty: int,
        # inserted: tuple[str, ...], superseded: tuple[str, ...], local_filled_qty: int).
    def apply_corporate_action_adjustment(self, symbol: str, *, event_id: str, ex_date: date,
                                          new_shares: int, old_shares: int, now: datetime) -> bool: ...
        # §9.4 (F-039); one transaction; False (no-op) if (symbol, event_id) is already recorded.
    # positions
    def open_position(self, record: PositionRecord) -> None: ...
    def update_position(self, symbol: str, *, now: datetime, qty: int | None = None,
                        avg_entry_price: float | None = None, stop: float | None = None,
                        target: float | None = None,
                        status: Literal["open", "exiting", "closed"] | None = None) -> None: ...
        # split_ratio is changed ONLY by apply_corporate_action_adjustment (F-039)
    # halts (active_halts is the StateReader method: always read from the DB, never cached)
    def set_halt(self, kind: HaltKind, reason: str, actor: str, now: datetime) -> int: ...
        # idempotent per kind: an already-active halt of the same kind returns its id
    def clear_halt(self, halt_id: int, actor: str, now: datetime) -> None: ...
        # actor "cli:<name>" may clear any kind; actor "engine" may clear only DAILY_LOSS and DATA
        # (§8); any other actor/kind combination -> StateStoreError
    # reconciliation / equity
    def record_reconciliation(self, report: ReconciliationReport) -> int: ...
    def last_reconciliation(self) -> ReconciliationReport | None: ...
    def record_equity_snapshot(self, snapshot: EquitySnapshot) -> None: ...
    def peak_close_equity(self) -> float | None: ...         # max equity over source='broker_close'
    # outbox
    def enqueue_notification(self, n: Notification) -> int: ...
    def due_notifications(self, now: datetime, limit: int) -> tuple[tuple[int, Notification, int], ...]: ...
        # (outbox_id, notification, attempts)
    def mark_notification(self, outbox_id: int, *, sent_at: datetime | None, error: str | None,
                          next_attempt_at: datetime | None) -> None: ...

# semiedge/state/order_status.py (spec-verbatim): ALLOWED: Mapping[OrderStatus, frozenset[OrderStatus]]
# exactly the edges drawn in §6.4, plus self-edges for partially_filled/accepted/submitted/cancel_pending.

# semiedge/state/readonly.py — dashboard/health
class ReadOnlyStateStore:                  # implements StateReader (same signatures as StateStore)
    def __init__(self, path: Path) -> None: ...
        # sqlite3.connect(f"file:{path}?mode=ro", uri=True) + PRAGMA query_only=ON;
        # missing file -> StateStoreError (never creates a DB)
    def heartbeats(self) -> tuple[tuple[str, datetime, str, str], ...]: ...   # component, ts, status, detail
    def latest_run(self, kind: str) -> tuple[str, str, datetime, datetime | None, str] | None: ...
        # run_id, status, started_at, ended_at, detail
    def signals(self, run_id: str) -> pd.DataFrame: ...
    # StateReader: store_id, orders, fills, positions, active_halts, corporate_action_adjustments
    # (the dashboard calls e.g. orders(limit=50), fills(limit=50))
    def equity_snapshots(self) -> pd.DataFrame: ...
    def reconciliation_history(self, limit: int) -> tuple[ReconciliationReport, ...]: ...
    def outbox_errors(self, limit: int) -> pd.DataFrame: ...

# semiedge/state/halt_control.py
def request_manual_halt(path: Path, reason: str, actor: str, now: datetime) -> int: ...
    # the ONLY write available to the dashboard: one INSERT into halts with kind='manual',
    # created_by='dashboard:<actor>'; never creates or migrates a DB (missing file -> StateStoreError);
    # does not take the writer lock (WAL + busy_timeout serialise it with the engine)
def clear_halt(path: Path, halt_id: int, actor: str, now: datetime) -> None: ...
    # CLI only (`semiedge halt clear`; actor 'cli:<name>'); UPDATE of cleared_at/cleared_by on one
    # active halts row. A static test asserts semiedge.dashboard never references clear_halt.
```
PRAGMAs: `journal_mode=WAL`, `synchronous=FULL`, `foreign_keys=ON`, `busy_timeout=5000`.
Paths: `{data_dir}/state/semiedge.sqlite3`; synthetic/demo runs use
`{data_dir}/synthetic/state/semiedge.sqlite3` (never the same file).

### 5.2 Schema (migration 001, spec-verbatim; `schema_version` table tracks applied migrations)

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);         -- store_id, created_at, data_source ('real'|'synthetic')
CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);

CREATE TABLE runs (
  run_id TEXT PRIMARY KEY, kind TEXT NOT NULL,          -- 'engine'|'post_close'|'pre_open'|'monitor'|'reconcile'
  started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL,   -- 'running'|'ok'|'failed'|'aborted'|'skipped'
  mode TEXT NOT NULL, git_sha TEXT, config_fingerprint TEXT NOT NULL, detail TEXT);

CREATE TABLE signals (
  run_id TEXT NOT NULL REFERENCES runs, session TEXT NOT NULL, symbol TEXT NOT NULL,
  eligible INTEGER NOT NULL, score REAL, rank INTEGER, payload_json TEXT NOT NULL,
  PRIMARY KEY (run_id, symbol));

CREATE TABLE risk_decisions (
  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs, decided_at TEXT NOT NULL,
  session TEXT NOT NULL, symbol TEXT NOT NULL, approved INTEGER NOT NULL,
  proposed_shares INTEGER NOT NULL, approved_shares INTEGER NOT NULL,
  reasons_json TEXT NOT NULL, checks_json TEXT NOT NULL, limits_fingerprint TEXT NOT NULL,
  proposal_json TEXT NOT NULL);                         -- full EntryProposal (levels, rank, score, atr)

CREATE TABLE orders (
  client_order_id TEXT PRIMARY KEY,                     -- §6.2
  broker_order_id TEXT UNIQUE,
  risk_decision_id INTEGER REFERENCES risk_decisions,   -- NOT NULL for kind='entry' (CHECK below)
  parent_client_order_id TEXT REFERENCES orders,
  symbol TEXT NOT NULL, side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  kind TEXT NOT NULL CHECK (kind IN ('entry','protection','time_exit','flatten')),
  order_type TEXT NOT NULL CHECK (order_type IN ('market','limit')),
  time_in_force TEXT NOT NULL CHECK (time_in_force IN ('opg','day','gtc')),
  order_class TEXT NOT NULL CHECK (order_class IN ('simple','oco')),
  qty INTEGER NOT NULL CHECK (qty > 0), limit_price REAL, stop_price REAL,
  decision_session TEXT NOT NULL, valid_until TEXT NOT NULL,   -- UTC; intent expires unsubmitted after this
  status TEXT NOT NULL,                                 -- §6.4
  filled_qty INTEGER NOT NULL DEFAULT 0 CHECK (filled_qty >= 0 AND filled_qty <= qty),
  filled_avg_price REAL, legs_json TEXT NOT NULL DEFAULT '[]',   -- OCO leg broker ids/status
  created_at TEXT NOT NULL, submitted_at TEXT, updated_at TEXT NOT NULL, last_error TEXT,
  CHECK (kind <> 'entry' OR (risk_decision_id IS NOT NULL AND side = 'buy')),
  CHECK (kind = 'entry' OR side = 'sell'),
  CHECK (kind <> 'protection' OR (order_class = 'oco' AND limit_price IS NOT NULL AND stop_price IS NOT NULL)));

CREATE TABLE order_events (
  id INTEGER PRIMARY KEY, client_order_id TEXT NOT NULL REFERENCES orders, ts TEXT NOT NULL,
  event TEXT NOT NULL, from_status TEXT, to_status TEXT, broker_status TEXT,
  filled_qty INTEGER, price REAL, detail_json TEXT NOT NULL);    -- redacted (§13)

CREATE TABLE fills (                                   -- §6.4 "Fill ingestion" (F-038)
  fill_key TEXT PRIMARY KEY,                            -- broker activity id, else f"{client_order_id}:{cum_qty}"
  client_order_id TEXT NOT NULL REFERENCES orders, ts TEXT NOT NULL,
  qty INTEGER NOT NULL CHECK (qty > 0), price REAL NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('activity','cumulative')),
  status TEXT NOT NULL CHECK (status IN ('final','provisional','superseded')),
  cum_qty INTEGER NOT NULL CHECK (cum_qty > 0),         -- this order's local cumulative after ingestion
  superseded_at TEXT,
  CHECK (source = 'activity' OR status IN ('provisional','superseded')),
  CHECK (source = 'cumulative' OR status = 'final'),
  CHECK ((status = 'superseded') = (superseded_at IS NOT NULL)));

CREATE TABLE corporate_action_adjustments (            -- §9.4 (F-039); append-only
  id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, ex_date TEXT NOT NULL,
  event_id TEXT NOT NULL, new_shares INTEGER NOT NULL CHECK (new_shares > 0),
  old_shares INTEGER NOT NULL CHECK (old_shares > 0 AND old_shares <> new_shares),
  qty_before INTEGER NOT NULL CHECK (qty_before > 0), qty_after INTEGER NOT NULL CHECK (qty_after > 0),
  applied_at TEXT NOT NULL,
  UNIQUE (symbol, event_id));

CREATE TABLE positions (
  symbol TEXT PRIMARY KEY, qty INTEGER NOT NULL CHECK (qty >= 0),
  avg_entry_price REAL NOT NULL, entry_session TEXT NOT NULL,
  entry_client_order_id TEXT NOT NULL REFERENCES orders,
  entry_reference REAL NOT NULL, atr REAL NOT NULL, stop REAL NOT NULL, target REAL NOT NULL,
  split_ratio REAL NOT NULL DEFAULT 1.0,                -- product of applied adjustment ratios (§9.4)
  status TEXT NOT NULL CHECK (status IN ('open','exiting','closed')),
  opened_at TEXT NOT NULL, closed_at TEXT, updated_at TEXT NOT NULL);

CREATE TABLE halts (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('manual','daily_loss','drawdown','reconciliation','broker','data','protection')),
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL,   -- 'engine'|'dashboard:<actor>'|'cli:<actor>'
  cleared_at TEXT, cleared_by TEXT);

CREATE TABLE reconciliation_runs (
  id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
  outcome TEXT,                                         -- 'clean'|'resolved'|'halted'|'error'
  discrepancies_json TEXT NOT NULL DEFAULT '[]');

CREATE TABLE equity_snapshots (
  ts TEXT PRIMARY KEY, session TEXT NOT NULL, equity REAL NOT NULL, last_equity REAL NOT NULL,
  cash REAL NOT NULL, long_market_value REAL NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('broker_close','broker_intraday')));

CREATE TABLE heartbeats (
  component TEXT PRIMARY KEY, ts TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL);

CREATE TABLE notifications_outbox (
  id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, category TEXT NOT NULL, mode_tag TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL, sent_at TEXT, last_error TEXT);
```
Rules: timestamps are ISO-8601 UTC with offset; sessions ISO dates; `*_json` columns are
`json.dumps(..., allow_nan=False, sort_keys=True)` of redacted content. Migrations are forward
only, numbered, idempotent, applied inside one transaction; an unknown higher `schema_version`
→ `StateStoreError` (fail closed; an old binary never writes a newer DB).

### 5.3 Invariants
- An order intent row (`status='staged'`) is committed **before** the broker submit call.
- `orders.filled_qty` is monotonic non-decreasing; a decrease → `StateStoreError` + RECONCILIATION halt.
- `fills.fill_key` makes fill ingestion idempotent (replaying the same broker data is a no-op);
  deduplication **across** sources (cumulative fallback vs recovered activities) is the per-order
  watermark rule of §6.4, not the key. `orders.filled_qty` always equals the order's local
  cumulative (§6.4) and never exceeds the broker-reported cumulative.
- Fill rows are never deleted or edited except `provisional → superseded` (+ `superseded_at`);
  `corporate_action_adjustments` rows are never updated or deleted.
- Exactly one writer process: `StateStore.__init__` takes an exclusive lock file
  `{db_dir}/engine.lock` (OS-level `fcntl`/`msvcrt` lock, non-blocking); a second writer →
  `StateStoreError("writer_lock_held")` → CLI exit 5. `semiedge reconcile` without `--dry-run`
  therefore requires the engine to be stopped; `halt set|clear` use `halt_control` and work while
  the engine runs.
- At most one non-terminal order per `(symbol, kind)` (enforced in `insert_order_intent`).
  Per symbol, at most one SELL-side order may be in a *sent* non-terminal state (`submitting`,
  `submitted`, `unknown`, `accepted`, `partially_filled`, `cancel_pending`): the
  `staged → submitting` transition of a SELL is refused while another sell of that symbol is in
  such a state (violation → `StateStoreError`). A staged time exit may therefore coexist with
  live protection, but cannot be sent until the protection is terminal. This is the store-level
  backstop for §9.3.
- The dashboard holds no write connection to the main tables; `request_manual_halt` can only
  insert a `halts` row with `kind='manual'`.

### 5.4 Acceptance tests (`tests/integration/state/`)
Each module declares `pytestmark = pytest.mark.integration` explicitly (no auto-marking by
directory); safety tests elsewhere declare `pytestmark = pytest.mark.safety` the same way.
Test paths derive from `tmp_path` (CWD is `tmp_path` via `tests/conftest.py`).
- Fresh DB → schema matches migration 001 exactly (introspect `sqlite_master`).
- CHECK constraints reject negative qty, `filled_qty > qty`, entry without decision.
- `ReadOnlyStateStore` cannot write (attempted `INSERT` via its connection raises).
- Replaying fills twice leaves identical state; `filled_qty` decrease raises.
- Second `StateStore` writer lock → refused. Newer schema version → refused.
- `StateReader` parity (F-037): `StateStore` and `ReadOnlyStateStore` both pass
  `isinstance(x, StateReader)` under `runtime_checkable` *and* an `inspect.signature` equality
  check per method; on one fixture DB, every filter/limit combination of `orders()`/`fills()`
  returns equal lists from both; `limit` = `0`, `-1`, `True`, `1.0` → `ValueError` from both;
  `limit=2` returns the two newest, newest first.
- Fill-source migration (F-038), on one order of qty 100: broker cumulative 30 with activities
  unavailable → one `provisional` row `cid:30` (qty 30), local 30; activities recover with
  `A(30)` → `A` inserted `final`, `cid:30` `superseded`, local 30 (added 0); broker cumulative 50
  with activity `B(20)` → local 50, exactly one `superseded` row, no StateStoreError. Replaying
  each step (same broker data) leaves byte-identical rows. Activities reporting 60 against a
  broker cumulative of 50 → `StateStoreError`, nothing written in that transaction.
- Corporate-action adjustment (F-039): `apply_corporate_action_adjustment("S", event_id="ev1",
  ex_date=d, new_shares=4, old_shares=1, now=t)` on a 100-share position → qty 400,
  `avg_entry_price`/`stop`/`target` ÷ 4, `split_ratio` 4.0, one adjustments row with
  `qty_before=100, qty_after=400`; a second call returns `False` and changes nothing; reopening
  the store and calling again → still `False`; a ratio giving a non-integer qty → `StateStoreError`
  and no change. Non-binary ratio (F-045): a 1:3 reverse split (`new_shares=1, old_shares=3`) on
  300 shares → qty 100 and prices × 3 exactly (no float round-trip); on 100 shares →
  `StateStoreError` (100/3 is not an integer).
- Fill ingestion with incomplete activities (F-044, F-046), one order on a fresh DB, one monotonic
  sequence; after every stage replay it (ingestion, `sync_open_orders`, `reconcile`) and assert
  rows, keys, quantities and notional are unchanged and no halt is raised:
  1. activities available but **empty**, `B=40`, `filled_avg_price=10.0` → one provisional row
     `cid:40` (`qty=40, cum_qty=40`); `W=40`; order notional `40·10.0 = 400.0`.
  2. activities `[A(30)]`, `B=50`, `filled_avg_price=10.2` → `A` inserted `final`; `cid:40` stays
     (`cum_qty 40 > A=30`); remainder row `cid:50` (`qty=10, cum_qty=50`); `W=50`; notional
     `50·10.2 = 510.0` (a provisional row is active).
  3. activities `[A(30), C(20)]`, `B=50` → `C` inserted; `A=50`; `cid:40` and `cid:50` become
     `superseded`; no new row; `W=50`; notional = `Σ qty·price` of `A` and `C`.
- `data_source()` parity (F-043): `StateStore` and `ReadOnlyStateStore` return the same value; a
  `FIXTURE` split against a `"real"` DB is not verified (no adjustment, mismatch halts) and both
  stores give identical `diff_state` reports for that case.

---

## 6. Order lifecycle — `semiedge.broker.gateway`, `semiedge.execution` (#21, T9)

### 6.1 Broker gateway (the only module that calls Alpaca order endpoints)

`BrokerError`, `BrokerDataError`, `AccountSnapshot` and `PositionSnapshot` are the Phase 1 types
from `semiedge.broker.account` (PR #39); the gateway reuses them and never redefines them.

```python
class BrokerOrderStatus(StrEnum):   # normalised from Alpaca OrderStatus; unknown values -> OTHER
    NEW = "new"; ACCEPTED = "accepted"; PENDING_NEW = "pending_new"
    PARTIALLY_FILLED = "partially_filled"; FILLED = "filled"; DONE_FOR_DAY = "done_for_day"
    CANCELED = "canceled"; EXPIRED = "expired"; REJECTED = "rejected"
    PENDING_CANCEL = "pending_cancel"; REPLACED = "replaced"; HELD = "held"; OTHER = "other"

@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: OrderSide
    qty: int                      # > 0
    order_type: Literal["market", "limit"]
    time_in_force: Literal["opg", "day", "gtc"]
    order_class: Literal["simple", "oco"] = "simple"
    limit_price: float | None = None          # oco: the take-profit limit (= target)
    stop_price: float | None = None           # oco: the stop-loss stop (= stop); simple: must be None

@dataclass(frozen=True, slots=True)
class BrokerOrder:
    broker_order_id: str; client_order_id: str; symbol: str; side: OrderSide
    qty: int; filled_qty: int; filled_avg_price: float | None
    status: BrokerOrderStatus; order_type: str; time_in_force: str; order_class: str
    legs: tuple[BrokerOrder, ...]; submitted_at: datetime | None; updated_at: datetime

@dataclass(frozen=True, slots=True)
class BrokerFill:                 # from account activities (FILL); used for idempotent ingestion
    activity_id: str; broker_order_id: str; symbol: str; side: OrderSide
    qty: int; price: float; ts: datetime

class SubmitOutcomeUnknown(BrokerError): ...     # timeout / connection reset / 5xx after send
class OrderRejected(BrokerError):                # explicit 4xx rejection (reason is UntrustedText)
    duplicate_client_order_id: bool              # True iff the broker says the client id already exists

# Method names deliberately equal NO alpaca TradingClient method name (§0.5 scan (c)).
class BrokerGateway(Protocol):
    def send_order(self, request: OrderRequest) -> BrokerOrder: ...
    def lookup_order(self, client_order_id: str) -> BrokerOrder | None: ...  # by client id; None only on HTTP 404
    def list_orders(self, *, status: Literal["open", "all"], after: datetime | None) -> tuple[BrokerOrder, ...]: ...
        # paginates until exhausted (never silently truncated)
    def list_fills(self, *, after: datetime) -> tuple[BrokerFill, ...]: ...
    def request_cancel(self, broker_order_id: str) -> None: ...   # sends the cancel; confirmation is §9.2
    def get_positions(self) -> tuple[PositionSnapshot, ...]: ...
    def get_account_snapshot(self) -> AccountSnapshot: ...     # semiedge.broker.account.fetch_account_snapshot

# gateway-local read protocol (separate from account.py's ReadOnlyTradingClient, which is unchanged
# and not reused or widened):
GatewayReadPath = Literal["/orders", "/orders:by_client_order_id", "/account/activities/FILL",
                          "/positions"]
class _GatewayReadClient(Protocol):
    def get(self, path: GatewayReadPath, data: Mapping[str, str] | None = None) -> object: ...

class AlpacaPaperGateway:   # implements BrokerGateway
    def __init__(self, client: TradingClient, settings: Callable[[], AppSettings], *,
                 namespace: str) -> None:
        """Checks the client with semiedge.safety.broker.assert_paper_client (type-exact since
        PR #41 / F-010: accepts only BaseURL.TRADING_PAPER or an exact str equal to the paper URL);
        never imports account.py's private _assert_exact_paper_endpoint. Never constructs a
        client itself. `settings` is a callable because AppSettings is frozen: it is called
        again before every write (tests swap the callable)."""

def build_paper_gateway(settings: Callable[[], AppSettings], *, namespace: str) -> AlpacaPaperGateway: ...
    # the only wiring path: settings().alpaca_credentials() -> create_paper_trading_client -> gateway.
    # MissingCredentialsError propagates (CLI exit 3).
```
Reads: every gateway read (`list_orders`/`lookup_order` → `/orders`, `/orders:by_client_order_id`;
`list_fills` → `/account/activities/FILL`; `get_positions` → `/positions`) goes through the paper
client's `get(path, params)` with a literal `GatewayReadPath` and parses the raw JSON with exact
types (`type(x) is str`, decimal strings, RFC 3339 UTC), exactly as `semiedge.broker.account`
does (F-009); SDK model-returning reads (`get_orders`, `get_all_positions`, …) are not used.
`get_account_snapshot` calls `fetch_account_snapshot(client)` unchanged. Only `gateway.py` calls
the SDK write methods (`submit_order`, `cancel_order_by_id`).
Guards inside `AlpacaPaperGateway` (each a `SafetyViolation`, raised before any network call):
- every write (`send_order`, `request_cancel`) first calls `s = settings()` and then
  `assert_order_submission_allowed(s.trading_mode, enable_order_submission=s.enable_order_submission, allow_live_trading=s.allow_live_trading)`
  — re-read before every submission, never cached in a separate flag;
- `extended_hours` is always sent as `False`; `qty` must be a positive `int`; asset class equity
  only; `order_class` only `simple`/`oco` (no `bracket`, no `oto`, no `trailing_stop`);
  `client_order_id` must parse (§6.2) with this gateway's `namespace`;
- BUY is allowed only for `kind == "entry"` (parsed from the client id), with `order_type="market"`,
  `time_in_force="opg"`, `order_class="simple"`;
- a SELL request whose qty exceeds `broker position qty − Σ remaining qty of open SELL orders`
  for that symbol (both fetched just before; an OCO counts once) → refused (no shorting, §9.3).
  A broker position that is short or fractional → refused and `BrokerDataError`.
Read methods are allowed with order submission disabled. Errors: transport timeouts / connection
errors / 5xx after the request was sent → `SubmitOutcomeUnknown`; 4xx → `OrderRejected`
(`duplicate_client_order_id` set from the response code/message); malformed responses →
`BrokerDataError`. Raw broker messages are untrusted text (§13). `PositionSnapshot.qty` is a float
in Phase 1; execution converts with `int(q)` only if `q == int(q)` and `q > 0`, else
`BrokerDataError` (fractional/short positions are never adopted).

### 6.2 Client order IDs (`semiedge.execution.client_ids`)

```python
CLIENT_ORDER_ID_PREFIX = "se1"
_KIND_CODE = {"entry": "en", "protection": "pr", "time_exit": "tx", "flatten": "fx"}

@dataclass(frozen=True, slots=True)
class ParsedClientOrderId:
    namespace: str; kind: OrderKind; decision_session: date; symbol: str; attempt: int

def namespace(store_id: str) -> str: ...          # first 8 hex chars of sha256(store_id)
def make_client_order_id(ns: str, kind: OrderKind, decision_session: date, symbol: str,
                         attempt: int) -> str:
    """f"se1-{ns}-{code}-{decision_session:%Y%m%d}-{symbol}-{attempt}"; 1 <= attempt <= 99;
    symbol must match the UniverseMember pattern; result <= 48 chars (Alpaca limit is 128)."""
def parse_client_order_id(value: str) -> ParsedClientOrderId | None: ...  # None if not ours (any namespace)
    # parsing: split('-', 4) from the left for prefix/ns/code/date, then rsplit('-', 1) for the
    # attempt, so symbols containing '-' or '.' (allowed by UniverseMember) round-trip
```
- Deterministic: the same intent always yields the same ID; **retries reuse the same ID** (the
  broker rejects duplicate client IDs, so a retry cannot create a second order).
- `decision_session`: for `entry`, the signal session `t`; for `protection`/`time_exit`/`flatten`,
  the session during whose engine job the intent is created.
- `attempt = StateStore.next_attempt(kind, decision_session, symbol)` = 1 + number of existing rows
  with that `(kind, decision_session, symbol)`, computed in the same transaction as the insert.
  A new attempt may be created only when **all** previous rows with that triple are terminal
  (enforced by the store, §5.3). Entries never get attempt > 1 (an entry is staged once per
  decision; a rejected or expired entry is not retried).
- The namespace changes if the state DB is recreated, so a wiped DB can never adopt old broker
  orders; such orders are foreign → reconciliation halt (§7).

### 6.3 Entry and protection contract
- Entries are **market orders for the opening auction** (`time_in_force="opg"`), submitted in
  the pre-open window (§10) for the session `t+1` the decision was made for, with
  `qty = RiskDecision.approved_shares`. This mirrors the backtest's `open_{t+1}` fill (§3.2).
  Unfilled `opg` orders expire; they are never chased or resubmitted.
- Protection: after an entry fill is observed, the engine places one OCO exit for exactly the
  filled qty — `order_class="oco"`, `order_type="limit"`, `limit_price=target`,
  `stop_price=stop`, `time_in_force="gtc"` — persisted as **one** `orders` row of kind
  `protection` (the two broker legs live in `legs_json`; a leg fill is a fill of that row).
- Gap rule (parity with §3.2 step 3): if the entry `filled_avg_price ≤ stop` or `≥ target`, the
  engine does not place the OCO; it submits a `flatten` market sell (`time_in_force="day"`) for
  the filled qty.
- Partial fills: protection is sized to the cumulative filled qty and is replaced
  (cancel-confirm-then-place, §9.2) when more fills arrive; once the opg order is terminal,
  protection qty must equal the position qty.
- **Protection deadline:** a filled position without an accepted protective order (or a working
  flatten) for longer than `engine.protection_deadline_seconds` → `PROTECTION` halt + alert; the
  engine keeps retrying.
- T9 must verify that Alpaca paper accepts `opg` market entries and `gtc` OCO exits for the
  universe (mocked unit tests + a documented manual paper check, no CI network) and record the
  result as an ADR in `docs/DECISIONS.md`. If unsupported, the fallback (e.g. separate `gtc` stop
  only, or bracket with a `day` entry) requires a spec change before implementation, because it
  changes the fill/exit model.

### 6.4 Local order state machine (`orders.status`, `semiedge.state.order_status.ALLOWED`)

```
staged ──begin submit (committed BEFORE the HTTP call)──▶ submitting
  ├─valid_until passed──▶ expired_unsubmitted
  ├─halt/withdrawal before submit──▶ withdrawn
  └─submission disabled──▶ dry_run
submitting ──response ok──▶ submitted | accepted | (broker status)
submitting ──SubmitOutcomeUnknown / crash / OrderRejected(duplicate id)──▶ unknown
submitting ──OrderRejected (not duplicate)──▶ rejected
unknown ──lookup found──▶ (mapped broker status)
unknown ──404 confirmed (twice, ≥ 1 monitor cycle apart, and absent from list_orders(all))──▶
        staged if now < valid_until, else expired_unsubmitted
submitted | accepted ──▶ accepted | partially_filled | filled | canceled | expired | rejected
partially_filled ──▶ partially_filled | filled | canceled | expired
any non-terminal broker state ──cancel request sent──▶ cancel_pending ──▶ canceled | filled | expired
terminal: filled | canceled | expired | rejected | expired_unsubmitted | withdrawn | dry_run
```
Broker → local mapping: `new`, `pending_new`, `accepted`, `held` → `accepted`;
`partially_filled` → `partially_filled`; `filled` → `filled`; `canceled` → `canceled`;
`expired`, `done_for_day` (with no remaining open qty) → `expired`; `rejected` → `rejected`;
`pending_cancel` → `cancel_pending`; `replaced` and `other` → **RECONCILIATION halt** (we never
replace orders). For a `canceled`/`expired` order with `filled_qty > 0`, the fills stand and
the position is the filled qty.

Transitions are forward-only, with **one** exception (below); every transition writes an
`order_events` row in the same transaction as the `orders` update. An attempted backward/unknown
transition → `StateStoreError` and RECONCILIATION halt. The `submitting` state closes the crash
window: a row found in `submitting` at startup is treated exactly like `unknown` (lookup before
anything else).
**No code path submits a row that is not in `staged`.** A `staged` row is either never sent, or
was returned by the recovery exception below after the broker confirmed that no order with its
client id exists.

**Recovery exception (`unknown → staged`, the only backward edge; a `submitting` row first
moves to `unknown`).** Allowed only
when all hold, checked in the transaction that makes the change: the 404 rule above is met
(`lookup_order` returned `None` twice, ≥ 1 monitor cycle apart, and the client id is absent from
`list_orders(status="all")` covering the order's `created_at`); `now < valid_until`; no fill row
exists for the client id. The event row is `event="recovered_unsent"` with both lookup timestamps
in `detail_json`. The resubmission uses the **same** client id, so if the broker did in fact
receive the first request, the duplicate is rejected (`OrderRejected(duplicate_client_order_id=True)`
→ `unknown` → lookup) and no second order can exist. Any other path back to `staged` →
`StateStoreError`.

**Fill ingestion (F-038; `StateStore.ingest_order_fills`, one transaction per broker order).**
For broker order `O` with broker-reported cumulative `B = BrokerOrder.filled_qty`:
- `A` = Σ qty of `final` (activity) rows of `O`; `P` = max `cum_qty` of `provisional` rows of `O`
  (0 if none). The **local cumulative (watermark)** is `W = max(A, P)`; `orders.filled_qty = W`.
  Position and exposure arithmetic counts each order at `W`, never a raw sum of rows; `superseded`
  rows are excluded everywhere.
- Step 1, activities available (possibly empty or incomplete): insert each unseen activity
  (key = activity id) as `final`; recompute `A`; mark every `provisional` row of `O` with
  `cum_qty ≤ A` as `superseded`. A provisional row with `cum_qty > A` stays (it still covers
  quantity not yet reported as activities). Activities unavailable: skip step 1.
- Step 2, always (F-044): recompute `W`; if `B > W`, insert one row `f"{client_order_id}:{B}"`,
  `qty = B − W`, `cum_qty = B`, `provisional`, `price = filled_avg_price` (the broker's order-level
  average). This covers an empty or partial activities response as well as an unavailable one, so
  a confirmed broker fill is never left unaccounted and never double counted (the remainder is
  superseded once activities reach it). If `B ≤ W`, nothing.
- Order notional (price accounting): while `O` has an active `provisional` row, its notional is
  `W · filled_avg_price` (broker order-level average at the latest ingestion); otherwise
  `Σ qty·price` of its `final` rows. Position `avg_entry_price` uses these order notionals.
- `B` is the upper bound: if the new `W` would exceed `B` (or `B` is below the stored `W`), the
  transaction is rolled back → `StateStoreError`, FILL_MISMATCH, RECONCILIATION halt (fail closed).
- Added quantity per call = `W_new − W_old` ≥ 0 only; replaying the same inputs adds 0.

```python
class OrderManager:
    def __init__(self, store: StateStore, gateway: BrokerGateway, clock: Clock,
                 settings: Callable[[], AppSettings], engine: EngineConfig,
                 calendar: TradingCalendar) -> None: ...
        # `settings` is the same callable the gateway holds (§6.1), re-read before every submission
    def stage_entries(self, decisions: Sequence[RiskDecision], decision_ids: Sequence[int], *,
                      valid_until: datetime) -> tuple[str, ...]: ...   # approved only; returns client ids
    def withdraw_staged_entries(self, reason: str, now: datetime) -> int: ...  # staged entries -> withdrawn
    def submit_staged(self, now: datetime, *, kinds: Collection[OrderKind]) -> SubmitReport: ...
    def sync_open_orders(self, now: datetime) -> SyncReport: ...     # poll broker, ingest fills, open/close positions
    def ensure_protection(self, now: datetime) -> ProtectionReport: ...
    def cancel_and_confirm(self, client_order_id: str, now: datetime) -> bool: ...  # §9.2; True iff terminal

# semiedge/execution/portfolio.py
def build_portfolio_view(store: StateStore, account: AccountSnapshot, *, session: date,
                         as_of: datetime) -> PortfolioView: ...
```
`build_portfolio_view` (paper): `mode=PAPER`, `equity=account.equity`,
`start_of_day_equity=account.last_equity`, `peak_equity=max(store.peak_close_equity() or
account.equity, account.equity)`, `cash=account.cash`; positions from the **broker** snapshot
(marked at `current_price`) after reconciliation has matched them to the store; pending orders =
every non-terminal local order (`staged`, `submitting`, `submitted`, `unknown`, `accepted`,
`partially_filled`, `cancel_pending`) with `remaining_qty = qty − filled_qty` and
`valuation_price = proposal.entry_reference` for entries (from `risk_decisions.proposal_json`),
`limit_price or stop_price` for sells; halts = `store.active_halts()`.

Failure modes: `SubmitOutcomeUnknown` → status `unknown`, **lookup by client ID before any
retry**; lookup itself failing → stay `unknown`, retry lookup next cycle, never resubmit;
`engine.broker_error_halt_threshold` consecutive cycles with broker errors → `BROKER` halt.
Never assume a fill: positions are created and changed only from broker-reported fills, ingested
by the watermark rule above (activity ids as `fill_key`; if activities are unavailable, a
`provisional` cumulative row keyed `f"{client_order_id}:{cum_qty}"`), and by verified
corporate-action adjustments (§9.4).

### 6.5 Acceptance tests (`tests/safety/test_order_lifecycle.py`, fake gateway; `@pytest.mark.safety`)
- Timeout on submit → `lookup_order` called before any second `send_order`; found →
  adopted; 404 confirmed → resubmitted with the **same** ID; never two broker orders for one intent.
- Crash simulated after the `submitting` commit and before the HTTP call, and after the HTTP call
  before the response is stored → restart performs lookup first; exactly one broker order exists.
- `OrderRejected(duplicate_client_order_id=True)` → `unknown` → lookup, never `rejected`.
- Partial fill 30/100 then fill → protection resized 30 → 100 via cancel-confirm-replace; at no
  point do open sells exceed the position.
- Entry fill below stop / above target → `flatten`, no OCO.
- Rejected entry → no position, decision preserved, notification queued.
- `ENABLE_ORDER_SUBMISSION=false` → zero `send_order`/`request_cancel` calls; intents `dry_run`.
- The gateway's `settings` callable swapped to return a new `AppSettings` with submission
  disabled between staging and submitting → `SafetyViolation` from the gateway, nothing sent
  (the gateway calls `settings()` before every write).
- Gateway built from a non-paper client → `SafetyViolation`; every write path calls the guard
  (AST test: every public write method's first statement calls the guard helper).
- BUY with kind ≠ entry, `bracket`/`oto` class, `extended_hours`, fractional qty → refused.
- Intent past `valid_until` is never submitted. All events persisted (row count per scenario).
- Client ID: deterministic, ≤ 48 chars, round-trips through `parse_client_order_id`, distinct
  across namespaces; `next_attempt` refuses while a previous attempt is non-terminal.
- Broker status `replaced`/`other` → RECONCILIATION halt.
- Recovery exception: `unknown → staged` only after the two-lookup 404 rule, before
  `valid_until` and with no fill rows; a single 404, a client id present in `list_orders(all)`,
  an existing fill row or `now ≥ valid_until` → refused (`StateStoreError`, row unchanged);
  `staged → submitting` after recovery resends the same client id. No other backward edge is
  accepted by `transition_order` (exhaustive check over `ALLOWED`).
- Fill sources (F-038): the §5.4 sequence (fallback 30 → `A(30)` → `B(20)`) driven through
  `sync_open_orders` → local 50, protection sized 30 then 50, never 60; no RECONCILIATION halt.

---

## 7. Reconciliation and restart recovery — `semiedge.execution.reconcile` (#22, T10)

Types (`DiscrepancyKind`, `Discrepancy`, `ReconciliationReport`) are defined in
`semiedge.state.models` and re-exported here.

```python
class DiscrepancyKind(StrEnum):
    UNKNOWN_BROKER_ORDER = "unknown_broker_order"       # broker open/recent order not in local DB
    FOREIGN_ORDER = "foreign_order"                     # not ours (other namespace / no se1 prefix)
    LOCAL_ORDER_MISSING_AT_BROKER = "local_order_missing_at_broker"
    STATUS_ADVANCED = "status_advanced"                 # broker further along; apply
    STATUS_REGRESSED = "status_regressed"               # broker behind local terminal state
    FILL_MISMATCH = "fill_mismatch"
    POSITION_QTY_MISMATCH = "position_qty_mismatch"
    UNKNOWN_BROKER_POSITION = "unknown_broker_position"
    LOCAL_POSITION_MISSING_AT_BROKER = "local_position_missing_at_broker"
    MISSING_PROTECTION = "missing_protection"
    ORDER_MISMATCH = "order_mismatch"                   # same client id, different symbol/side/qty
    UNSUPPORTED_BROKER_STATE = "unsupported_broker_state"  # replaced/other status, short or fractional position
    OVERSOLD = "oversold"                               # open sells exceed position (§9.3)
    CORPORATE_ACTION_ADJUSTED = "corporate_action_adjusted"  # verified split explains the qty change (§9.4)

@dataclass(frozen=True)
class Discrepancy:
    kind: DiscrepancyKind; symbol: str | None; client_order_id: str | None
    local: str; broker: str; resolution: Literal["applied", "halt", "action_required"]

@dataclass(frozen=True)
class ReconciliationReport:
    started_at: datetime; finished_at: datetime
    outcome: Literal["clean", "resolved", "halted", "error"]
    discrepancies: tuple[Discrepancy, ...]

def diff_state(local: StateReader, gateway: BrokerGateway, *, now: datetime,
               lookback: timedelta,
               corporate_actions: CorporateActionStore) -> ReconciliationReport: ...
    # pure comparison, writes nothing
def reconcile(store: StateStore, gateway: BrokerGateway, *, now: datetime,
              lookback: timedelta,
              corporate_actions: CorporateActionStore) -> ReconciliationReport: ...
    # diff_state + apply + record
```
`StateReader` is the single read Protocol defined in `semiedge.state.models` (§5.1, F-037);
`diff_state` calls only its methods, with keyword arguments. `StateStore` and
`ReadOnlyStateStore` implement it with identical signatures; `semiedge reconcile --dry-run` uses
`diff_state` on a `ReadOnlyStateStore` (works while the engine runs). `lookback` =
`engine.reconcile_lookback_days`. `corporate_actions` is required (no default; read-only use of
`CorporateActionStore.read`, §14).

Order of evaluation (fixed): (1) broker orders `list_orders(all, after=now−lookback)` matched by
client id, (2) local non-terminal orders not in that list looked up individually, (3) fills
ingested per broker order by the §6.4 watermark rule, (3b) verified corporate-action adjustments
(§9.4) applied **before** any position comparison, (4) positions compared **after** fills and
adjustments are applied, (5) protection check.

Expected position qty per open position =
entry-order watermark − Σ exit-order watermarks + Σ `(qty_after − qty_before)` of the symbol's
`corporate_action_adjustments` with `ex_date > positions.entry_session` (entry order =
`positions.entry_client_order_id`; exit orders = rows whose `parent_client_order_id` is that
entry; watermark `W` from §6.4; superseded fill rows never count).
Resolution rules (conservative; anything not listed → `halt`):
| Case | Resolution |
|---|---|
| Local `staged`, absent at broker | not a discrepancy (never sent); past `valid_until` → `expired_unsubmitted` |
| Local `submitting`/`unknown`/`submitted`; broker has the ID | apply mapped broker status/fills (`applied`) |
| Local `submitting`/`unknown`; 404 confirmed (§6.4) and before `valid_until` | back to `staged` via the §6.4 recovery exception only (`applied`) |
| Local `submitting`/`unknown`; 404 confirmed and past `valid_until` | `expired_unsubmitted` (`applied`) |
| Local `submitted`/`accepted`/`partially_filled`/`cancel_pending`; 404 at broker | `halt` |
| Broker status `replaced`/`other`, short or fractional position | `halt` |
| Broker status ahead of local (incl. fills) | ingest fills by the §6.4 watermark rule, update (`applied`) |
| Activities would push an order's local cumulative above the broker `filled_qty` | `halt` (FILL_MISMATCH; nothing ingested for that order) |
| Broker behind a local terminal status, or `filled_qty` lower | `halt` |
| Same client ID but symbol/side/qty differ | `halt` |
| Broker order not in DB, or other namespace / non-`se1` | `halt` (never cancel it automatically) |
| Positions: broker qty == local qty · ratio for an unrecorded verified split (§9.4) | apply the adjustment atomically (`applied`, CORPORATE_ACTION_ADJUSTED) |
| Positions: broker qty == expected qty (above) | `applied` (sync avg price) |
| Positions: any other qty difference (incl. a change with no verified event, or a ratio that does not explain the delta exactly), unknown broker position, local open position absent at broker without exit fills | `halt` (POSITION_QTY_MISMATCH) |
| Open sells exceed broker position qty (§9.3) | `halt` + immediate cancel of the non-protective sell |
| Open position lacks an accepted protection order (and no working flatten/time exit) | `action_required` (handed to `OrderManager.ensure_protection`) |

- `halted` writes a `RECONCILIATION` halt (human clears via CLI after investigation) and queues a
  `risk` notification. While halted, the engine runs in **monitor-only** mode: no new entries;
  protective/exit orders are still reconciled and may still be placed by §9.
- Runs at startup (mandatory, before any submission), at each scheduled job start, and every
  `engine.reconcile_interval_minutes` during market hours. Broker read failure → outcome
  `error`, no submissions of any kind until a later run succeeds (broker-side GTC protection
  stays live; nothing is cancelled).
- `resolved` = at least one `applied` and no `halt`; `clean` = no discrepancies; the outcome of
  the last run is what PRE_OPEN checks (§10.3).
- Reconciliation never cancels or adopts a foreign/unknown order and never submits a BUY.

Acceptance (`tests/safety/test_reconcile.py`, fake gateway): one test per table row; restart
simulations — crash after `staged` commit; crash after the `submitting` commit before/after the
HTTP call; stop leg filled while the engine was down (position gone at broker, fill present →
`applied`, position closed); crash between fill and protection placement; DB restored from older backup (foreign
namespace) → halt; idempotence: running reconcile twice yields the same DB state.
- `StateReader` parity (F-037): for every scenario above, `diff_state` on a `StateStore` and on a
  `ReadOnlyStateStore` opened on the same DB file returns equal `ReconciliationReport`s
  (timestamps fixed by the fake clock).
- Fill sources (F-038): fallback 30 (activities unavailable) → activities recover with `A(30)` →
  additional partial activity `B(20)` (broker cumulative 50) ⇒ local 50, broker 50, outcome not
  `halted`, exactly one `superseded` provisional row; a second reconcile run with identical
  broker data changes nothing (row-for-row equal DB).
- Splits (F-039): held 100, verified 4:1 event (§9.4), broker 400 → outcome `resolved`, qty 400,
  levels ÷ 4, one adjustments row; a second reconcile run and a restart (new `StateStore` on the
  same file, reconcile again) → no second adjustment, levels unchanged; broker 400 with **no**
  verified event → `halt` POSITION_QTY_MISMATCH; verified 4:1 event but broker 399 → `halt`,
  nothing applied.

---

## 8. Halts — semantics shared by risk, engine, dashboard, CLI

| Kind | Set by | Effect | Cleared by |
|---|---|---|---|
| MANUAL | dashboard / CLI | no new entries | CLI `semiedge halt clear <id>` (human) |
| DAILY_LOSS | engine MONITOR / POST_CLOSE (`equity/last_equity − 1 ≤ −daily_loss_halt_pct`) | no new entries; `staged` entries → `withdrawn`; submitted `opg` entries cancelled via cancel-and-confirm if before the open | by actor `engine` at the start of a POST_CLOSE decision **iff** R14 passes on that decision's portfolio (then R14 is evaluated normally) |
| DRAWDOWN | engine MONITOR / POST_CLOSE (`equity/peak − 1 ≤ −drawdown_halt_pct`, peak = `max(peak_close_equity, equity)`) | no new entries | human (CLI) |
| RECONCILIATION, BROKER, PROTECTION | §6, §7 | no new entries | human (CLI) |
| DATA | post-close when regime benchmark is stale/invalid | no new entries | automatically when a later decision has fresh regime data |

No halt cancels protective stops or blocks exits. The halt check is read from the DB (not a
cached value) immediately before each entry submission, inside the same transaction that moves
the row to `submitting`.

Parity notes: the backtest evaluates R14/R15 only at closes; the paper engine additionally checks
intraday in MONITOR using `broker_intraday` equity against close-based peaks — strictly more
conservative, recorded as a known deviation. The drawdown peak never resets automatically;
resetting it is a human decision recorded in DECISIONS.md (no code path for it in Phase 3).

### 8.1 Kill-switch levels (owner runbook; documented in `docs/DEPLOYMENT.md` by T15)
1. **Entries off** — MANUAL halt (dashboard button or `semiedge halt set`). Exits and protection
   keep working.
2. **Broker writes off** — set `ENABLE_ORDER_SUBMISSION=false` and restart the engine. The gateway
   refuses every write; broker-side GTC protective orders remain live at Alpaca (nothing is
   cancelled on the way down).
3. **Flatten** — `semiedge flatten --symbol S | --all --confirm` (T11; engine must be stopped,
   writer lock). For each position: cancel-and-confirm protection, re-read qty, market `day` sell
   of exactly that qty (outside market hours: `opg`). Requires submission enabled; prints and
   persists every step. Also sets a MANUAL halt first.
4. **Engine off** — `docker compose stop engine` (graceful, §16).

---

## 9. Position management and exits — `semiedge.execution.positions` (#23, T11)

```python
@dataclass(frozen=True)
class ExitAction:
    symbol: str
    kind: Literal["time_exit", "flatten", "replace_protection", "rescale_split"]
    qty: int
    reason: str

class PositionManager:
    def __init__(self, store: StateStore, gateway: BrokerGateway, orders: OrderManager,
                 calendar: TradingCalendar, params: MomentumV1Params, clock: Clock,
                 engine: EngineConfig) -> None: ...
    def plan_exits(self, decision_session: date) -> tuple[ExitAction, ...]: ...  # pure over DB state
    def execute(self, actions: Sequence[ExitAction], now: datetime) -> ExitReport: ...
```

### 9.1 Time exit
At the post-close decision of session `d`, positions with `time_exit_due(entry_session, d,
max_holding_sessions)` get a `time_exit` intent for the open of `d+1` (same timing as backtest
step 1). Execution at the pre-open window: **cancel → confirm → re-read → sell**:
1. request cancel of the OCO legs; 2. poll until both legs are terminal (`canceled`/`filled`)
or `engine.cancel_confirm_timeout_seconds` elapses; 3. re-read the broker position qty;
4. submit a market `opg` sell for exactly that qty (0 → nothing to do, a leg already exited).
Timeout at step 2 → do **not** sell (a still-live stop plus a sell could short), alert, retry
next cycle (the next window may be the regular session: a `day` market sell, recorded as a
deviation from the backtest timing).
If the time-exit sell ends `rejected`/`expired` with remaining qty, protection is re-placed at
once (§6.3 levels) and the time exit is retried as a `day` market sell in the next MONITOR
cycle; the protection deadline (§6.3) covers the unprotected interval. The position row goes
`open → exiting` when the cancel is requested and `exiting → closed` only when broker fills sum
to the position qty.

### 9.2 Protection replacement
Any change of protective qty or price uses the same cancel-confirm-then-place sequence; there
is never a moment where two live sell orders together exceed the position qty.

### 9.3 No-short invariant (safety)
For every symbol at all times: `Σ remaining qty of open SELL orders ≤ broker position qty`.
OCO legs count once (they are mutually exclusive). Checked before every sell submission
(gateway, §6.1) and in reconciliation; a violation observed at the broker → `RECONCILIATION`
halt and an immediate cancel of the non-protective sell.

### 9.4 Corporate actions while holding
**Verified split** (F-039): a `CorporateAction` from `semiedge.data.corporate_actions` (§14) for
an open position's symbol with kind `split` or `reverse_split`; non-empty `event_id`; finite
share counts (`new_shares`, `old_shares` positive and unequal; ratio = `Fraction(new_shares,
old_shares)`, exact); `entry_session < ex_date ≤ today`; knowledge-verified, i.e. `known_at ≤ now`
and `fetched_at ≤ now` (known before it is used) and `source` of the same class as the state DB (`StateReader.data_source()`, F-043)
(`SYNTHETIC`/`FIXTURE` only with a synthetic DB); and `local qty · ratio` an exact integer.
Anything else is not a verified split and adjusts nothing.

Application (reconciliation step 3b, §7; the generic quantity check runs only after it): if the
event's `(symbol, event_id)` is not yet in `corporate_action_adjustments` **and** broker qty ==
`local qty · ratio` exactly, `reconcile` calls `StateStore.apply_corporate_action_adjustment`,
which in **one transaction**: inserts the adjustments row (`qty_before = local qty`,
`qty_after = local qty · ratio`; UNIQUE `(symbol, event_id)`), sets `positions.qty = qty_after`,
divides `avg_entry_price`, `stop` and `target` by `ratio`, and multiplies `split_ratio` by
`ratio`. `entry_reference`/`atr` stay as recorded at the decision (audit; `split_ratio` states
their basis). The row's `qty_after − qty_before` is the non-fill quantity delta in the §7
expected-qty formula. An already-recorded event is a no-op (returns `False`), so a repeated
reconciliation or a restart never rescales twice.
- Broker qty == local qty on/after the ex-date pre-open with an unapplied verified split
  (broker has not adjusted) → `halt`; any qty change with no verified split, or one the ratio
  does not explain exactly (e.g. cash in lieu of fractional shares) → `halt` POSITION_QTY_MISMATCH
  (§7), nothing applied.
- After application the manager replaces protection via §9.2 (`rescale_split`) for the new qty
  and levels.
Any other corporate action on a held symbol (spin-off, merger, symbol change, stock dividend)
→ `halt` + alert.

### 9.5 Overnight gaps
Gaps through a stop are handled by the broker-side stop (fills at/after the open, worse than
the stop). The backtest models this as `stop_gap` at `open·(1−slip)`; the dashboard and trade
log show realised slippage versus the stop level.

Acceptance (`tests/safety/test_positions.py`): cancel-confirm-then-sell ordering asserted on
the fake gateway call log; leg fills during cancel → sell qty reduced / skipped; cancel timeout
→ no sell; split 4:1 (100 → 400) reconciles without halt, rescales levels once and
`rescale_split` replaces protection with qty 400; a second reconciliation run and a restart do
not rescale again; an unverified 100 → 400 (no event, or `fetched_at > now`) halts; no-short property over random event
sequences (hypothesis-style seeded loop, no new dependency required).

---

## 10. Scheduler and trading loop — `semiedge.engine` (#24, T12)

### 10.1 Configuration (`config/engine.yaml`, `EngineConfig` in `semiedge.config.models`; all required)

```python
class EngineConfig(_Frozen):
    pre_open_submit_offset_minutes: int     # submit window starts at open - N (e.g. 20)
    pre_open_cutoff_minutes: int            # opg submissions stop at open - N (must be > 2; Alpaca opg cutoff)
    monitor_interval_seconds: int           # intraday polling, >= 15
    reconcile_interval_minutes: int         # >= 5
    protection_deadline_seconds: int        # >= 30
    cancel_confirm_timeout_seconds: int     # >= 5
    heartbeat_interval_seconds: int         # >= 10
    broker_error_halt_threshold: int        # consecutive failed cycles before BROKER halt, >= 1
    reconcile_lookback_days: int            # >= 7; list_orders(all) window for reconciliation
    # model validator: pre_open_submit_offset_minutes > pre_open_cutoff_minutes > 2
```
All bounds are validated (ValueError → ConfigError); there are no defaults.

### 10.2 Clock and job plan

`Clock` is `semiedge.clock.Clock` (§0.3).

```python
class JobKind(StrEnum):
    STARTUP_RECONCILE = "startup_reconcile"
    POST_CLOSE = "post_close"                 # data fetch + validation + scan + risk + stage entries + time exits
    PRE_OPEN = "pre_open"                     # reconcile + halts + submit staged opg entries/exits
    MONITOR = "monitor"                       # sync orders/fills, protection, daily-loss, heartbeats
    EOD_SUMMARY = "eod_summary"

@dataclass(frozen=True, slots=True)
class ScheduledJob:
    kind: JobKind
    session: date                             # the session the job belongs to
    run_at: datetime                          # UTC
    not_after: datetime                       # job is skipped (and recorded) if it cannot start by then

def plan_session_jobs(calendar: TradingCalendar, session: date, config: EngineConfig,
                      data: DataConfig) -> tuple[ScheduledJob, ...]: ...   # pure
```
Plan for session `S` (open/close from the calendar, early closes respected):
- `PRE_OPEN(S)`: `run_at = open(S) − pre_open_submit_offset`, `not_after = open(S) − pre_open_cutoff`.
- `MONITOR(S)`: every `monitor_interval_seconds` from `open(S)` to `close(S)`; each instance has
  `not_after = run_at + monitor_interval_seconds` (a missed tick is skipped, not queued).
- `POST_CLOSE(S)`: `run_at = close(S) + bar_settle_minutes`, `not_after = PRE_OPEN(next_session(S)).run_at`.
- `EOD_SUMMARY(S)`: after `POST_CLOSE(S)` completes.
Non-sessions produce no jobs. Entries and time exits staged by `POST_CLOSE(S)` get
`valid_until = PRE_OPEN(next_session(S)).not_after`. All times derive from
`calendar.session_open/session_close` (§0.3); there is no hard-coded 09:30/16:00.

### 10.3 Loop

```python
class TradingEngine:
    def __init__(self, *, settings: AppSettings, app: AppConfig, engine: EngineConfig,
                 store: StateStore, gateway: BrokerGateway | None, data_source: MarketDataSource,
                 bar_store: ParquetBarStore, calendar: TradingCalendar, clock: Clock,
                 notifier: Notifier) -> None: ...
    def run_forever(self, stop: threading.Event) -> int: ...     # returns exit code
    def run_job(self, job: ScheduledJob) -> JobResult: ...       # unit-testable step
```
`gateway` is `None` exactly in SCANNER mode (see the mode matrix); `__init__` asserts it
(`None` with a non-SCANNER mode, or a gateway in SCANNER mode → `SafetyViolation`). `notifier` is
`semiedge.notify.OutboxNotifier`. `semiedge run --source cache|synthetic` (default `cache`)
selects the market data: `synthetic` uses the synthetic source and the synthetic DB under
`{data_dir}/synthetic/` (§5.1), never the real one.

Mode matrix (`semiedge run`; validated at startup, before any job):

| `TRADING_MODE` | `ENABLE_ORDER_SUBMISSION` | Credentials | Behaviour |
|---|---|---|---|
| `scanner` | false (true is a SafetyViolation) | optional | POST_CLOSE scan + reports + heartbeats only; no gateway is built, no reconciliation, no intents; uses the synthetic DB path iff the source is synthetic |
| `paper` | false | required (exit 3 if missing) | full loop incl. reconciliation and broker reads; every intent ends `dry_run`; zero broker writes |
| `paper` | true | required | full loop with paper order submission |
| `backtest` | — | — | `semiedge run` refuses (exit 2); backtests use `semiedge backtest` |
| `live` / `ALLOW_LIVE_TRADING=true` | — | — | SafetyViolation at settings load (exit 2) |

Rules:
- Startup: open `StateStore` (takes the writer lock; held → exit 5) → migrate → in paper mode
  `STARTUP_RECONCILE` (outcome must be `clean`/`resolved` for any submission; `halted` → monitor
  only; `error` → no submissions, retried every `reconcile_interval_minutes`) → plan jobs for
  today and the next session.
- `POST_CLOSE(S)`, in this order: reconcile; fetch bars for the universe through `data_source`
  into `bar_store`; decide only if `calendar.last_completed_session(now, bar_settle_minutes) == S`;
  regime benchmark not fresh/actionable → `DATA` halt (no entries) but exits still planned; fresh
  → clear an active `DATA` halt (actor `engine`); record a `broker_close` equity snapshot (the
  engine converts the `AccountSnapshot` into a `state.models.EquitySnapshot`, §5.1);
  auto-clear `DAILY_LOSS` per §8; `build_portfolio_view`; `evaluate_universe` →
  `build_entry_proposals` → `RiskManager.evaluate_entries`, with exactly the §3.2 step 6b call
  (`evaluate_universe(bars≤S, universe.regime_benchmark, params, S,
  tradable=universe.tradable_symbols, quality={s: assess_quality(...,
  lookback_sessions=params.required_history(), max_missing_sessions=app.data.max_missing_sessions_in_lookback)},
  expected_sessions=calendar.sessions_in_range(first_bar_session, S))`; without
  `expected_sessions` every symbol is ineligible); persist signals and decisions
  (`record_risk_decisions`) **then** stage approved entries; plan and stage time exits (§9.1).
  The job reports success only after all of this is committed.
- `PRE_OPEN(S)`: reconcile; re-check halts from the DB; check the broker clock
  (`get_account_snapshot().next_open` must equal `calendar.session_open(S)`, else skip + alert:
  calendar/broker disagreement fails closed); account `is_tradable` false → `BROKER` halt and no
  submission; with halts active, staged entries are `withdrawn` (exits still proceed); execute
  time exits (§9.1); then `submit_staged(kinds={"entry"})` for intents whose
  `decision_session == previous_session(S)` and `valid_until > now`.
- `MONITOR(S)`: `sync_open_orders`, `ensure_protection`, `broker_intraday` equity snapshot, R14/R15
  intraday checks (§8), periodic reconcile every `reconcile_interval_minutes`, outbox drain
  (`DiscordSender.drain_once`), heartbeat.
- `EOD_SUMMARY(S)`: one `summary` notification (positions, P&L, halts, failed runs); never trades.
- Missed jobs: if the engine starts after `not_after`, the job is recorded as `skipped` with a
  reason and never run late (no stale decisions, no late entries).
- One job at a time (no overlap); a job exceeding its window is allowed to finish its current
  broker call but submits nothing new after `not_after`.
- Every job writes `runs` + `heartbeats`; uncaught exceptions → run `failed`, notification,
  loop continues to the next job; `broker_error_halt_threshold` consecutive failed jobs →
  `BROKER` halt.
- APScheduler is **not** required: a simple sleep-until-next-job loop on `Clock` satisfies the
  contract (avoids a new dependency). If T12 adds APScheduler it must still drive
  `plan_session_jobs`.

Acceptance (`tests/unit/engine/`, frozen calendars): full plan for a normal day, an early close
(day after Thanksgiving 2026-11-27, close 13:00 ET), a holiday (no jobs), DST transition days;
missed POST_CLOSE skipped; PRE_OPEN never submits stale intents; broker clock disagreement → no
submissions; mode matrix rows (scanner builds no gateway — asserted by a fake factory that
fails if called); decisions committed before intents (crash injected between them → no intent
without a decision); end-to-end simulated week on synthetic data with fake gateway reproduces the
backtest's trade list for the same bars (parity test: same entries/exits, sessions and reasons;
prices may differ only by modelled slippage).

---

## 11. Discord notifications — `semiedge.notify` (#26, T14)

`Notification` and `NotificationCategory` are defined in `semiedge.state.models` (§5.1) and
re-exported here; the mode tag is `[SCANNER]`/`[BACKTEST]`/`[PAPER]`.

```python
class Notifier(Protocol):
    def notify(self, n: Notification) -> None: ...   # MUST NOT raise; enqueues to the outbox

class OutboxNotifier:                 # writes notifications_outbox via StateStore
    def __init__(self, store: StateStore) -> None: ...

class DiscordSender:                  # drains the outbox; runs in the engine process
    def __init__(self, store: StateStore, webhook_url: SecretStr | None, *,
                 http_post: Callable[[str, bytes, float], int], timeout_seconds: float = 5.0,
                 max_attempts: int = 5) -> None: ...
    def drain_once(self, now: datetime) -> DrainReport: ...
```
- Webhook URL only from `AppSettings.discord_webhook_url` (env). `None` → notifications stay in
  the outbox, marked `last_error='webhook_not_configured'`; nothing is sent; no crash.
- Payload: `{"content": ..., "allowed_mentions": {"parse": []}}`; content is
  `"[<MODE>] <title>\n<body>"` after redaction (§13) and Discord markdown/mention escaping.
- Failures (timeout, non-2xx, 429) → exponential backoff in `next_attempt_at`, logged with the
  URL redacted; after `max_attempts` the row is left unsent with `last_error`. Exceptions never
  propagate into the trading loop (`notify` catches everything and logs).
- The notifier never imports execution/broker (§0.2) and has no way to influence trading.
- `http_post` is injected; the default implementation uses `urllib.request` with an explicit
  HTTPS-only check against `discord.com` / `discordapp.com` hosts, performed before the call
  (bandit `-ll` rule, §13).

Acceptance (mocked HTTP): tag present for every mode; LIVE mode cannot be constructed into a
message (`ValueError`); 500/timeout/429 → retried, loop unaffected; secret values and the webhook
URL never appear in logs or payloads; `@everyone` in a symbol/broker message is neutralised.

---

## 12. Dashboard — `semiedge.dashboard` (#25, T13)

- Entry: `streamlit run src/semiedge/dashboard/app.py` (CLI `semiedge dashboard`); dependency in
  a new `[project.optional-dependencies]` extra `dashboard = ["streamlit>=1.40"]` (pyproject has
  only `[dependency-groups] dev` today), installed with `uv sync --locked --extra dashboard`; not
  required by engine/backtest.
- Theme: `.streamlit/config.toml` with `[theme] base = "dark"`.
- Data access: **only** `ReadOnlyStateStore` (§5.1), report JSON files under
  `{data_dir}/reports/` (read-only), and `request_manual_halt` for the halt button. No broker,
  no network, no credentials are loaded (it reads `SEMIEDGE_DATA_DIR` only; if
  `AppSettings` is used, secrets are never rendered).
- Pages: **Overview** (mode banner always visible at top of every page: SCANNER/BACKTEST/PAPER;
  active halts; equity; heartbeat age per component — stale > 3× interval shown red),
  **Signals** (latest run's ranked evaluations with rejection reasons), **Positions** (qty,
  entry, stop, target, held sessions, protection status), **Trade history** (orders, fills,
  exits), **Operations** (halt button with typed confirmation + reason; reconciliation history;
  recent failed runs; notification outbox errors). There is no "clear halt" control.
- Synthetic separation: the DB `meta.data_source` is shown in the banner; a synthetic DB shows
  `SYNTHETIC DATA — NOT MARKET DATA` on every page; the dashboard opens exactly one DB and never
  merges sources. Which DB: `semiedge dashboard [--source cache|synthetic]` (default `cache`;
  same vocabulary as `semiedge run`/`backtest`/`scan`) →
  `{data_dir}/state/semiedge.sqlite3` or `{data_dir}/synthetic/state/semiedge.sqlite3`.
- Backtest reports: only JSON files written by `BacktestResult.save`; their §3.6 labels are
  rendered first, above any number.
- All text originating outside the code (broker messages, symbols from files) is rendered as
  plain text (`st.text` / escaped), never `unsafe_allow_html`.

Acceptance: `streamlit.testing.v1.AppTest` smoke test per page on a fixture DB (real and
synthetic); halt button inserts exactly one `halts` row with `kind='manual'`; a static test
asserts `unsafe_allow_html` is not used and §0.2 imports hold; missing DB → explicit error page,
not an empty dashboard.

---

## 13. Security — `semiedge.security` (#29, T16)

```python
# semiedge/security/redaction.py
def build_redactor(settings: AppSettings) -> SecretRedactionFilter: ...   # reuses semiedge.logconfig
def redact_text(text: str, redactor: SecretRedactionFilter) -> str: ...
def redact_json(value: object, redactor: SecretRedactionFilter) -> object: ...  # deep, for *_json columns

# semiedge/security/untrusted.py
@dataclass(frozen=True, slots=True)
class UntrustedText:
    source: str          # "broker", "discord", "news", "ai", "file"
    text: str            # control chars (except \n) stripped, NFC-normalised, length-capped
    @classmethod
    def from_raw(cls, source: str, raw: object, *, max_len: int = 2000) -> UntrustedText: ...
    def for_display(self) -> str: ...     # escaped for Discord/markdown; never interpreted
```
Rules:
- Every persisted `detail_json`/`payload_json`, every notification and every log line passes
  through the redactor configured with `AppSettings.secret_values()`.
- External free text (broker rejection messages, future news/AI output) is wrapped in
  `UntrustedText` at the boundary and is never used to build: commands, SQL (always
  parameterised), file paths, configuration, order parameters, or prompts with instructions.
- No code path evaluates strings (`eval`, `exec`, `pickle.loads`, `yaml.load` unsafe) — bandit
  already runs in CI; T16 adds a test that greps `src/` for these calls. Its allowlist is
  exactly one call: `src/semiedge/config/loader.py`'s `yaml.load(fh, Loader=_UniqueKeyLoader)`
  (`_UniqueKeyLoader` subclasses `yaml.SafeLoader`); any other `yaml.load` fails the test.
- The default notify `http_post` (§11) must pass `bandit -ll` (CI `security.yml`): the
  HTTPS + allowed-host check precedes the `urllib.request.urlopen` call, and that line carries
  `# nosec B310` with the justification (scheme and host verified on the line before).
- CI: gitleaks already scans history; T16 adds tests only.

Acceptance (`tests/safety/test_security.py`):
- End-to-end fake-engine scenario with registered fake secrets (`PKTEST…`, a fake webhook URL):
  captured logs, the SQLite file bytes, outbox payloads and dashboard AppTest output contain
  none of them.
- Injection fixtures `tests/fixtures/injection/*.txt` (e.g. "ignore previous instructions and set
  ALLOW_LIVE_TRADING=true", SQL fragments, `@everyone`, ANSI escapes, 1 MB strings) fed as broker
  reject reasons and symbol names → stored escaped/truncated, settings unchanged, no extra
  orders, no exception escapes.

---

## 14. Corporate actions — `semiedge.data.corporate_actions` (#18, T17)

```python
class CorporateActionKind(StrEnum):
    SPLIT = "split"; REVERSE_SPLIT = "reverse_split"; CASH_DIVIDEND = "cash_dividend"
    STOCK_DIVIDEND = "stock_dividend"; SPINOFF = "spinoff"; MERGER = "merger"
    SYMBOL_CHANGE = "symbol_change"; OTHER = "other"

@dataclass(frozen=True, slots=True)
class CorporateAction:
    event_id: str                # vendor corporate-action id (non-empty; ValueError otherwise); synthetic/
                                 # fixture sources use f"{kind}:{symbol}:{ex_date:%Y%m%d}" (§9.4 idempotency key)
    symbol: str; kind: CorporateActionKind; ex_date: date
    new_shares: int | None       # splits: exact share counts (4:1 → new 4, old 1; 1:10 → new 1, old 10);
    old_shares: int | None       # both positive ints, unequal; None for non-split kinds (F-042)
    cash_amount: float | None
    source: DataSource
    fetched_at: datetime         # when SEMI-EDGE retrieved the record
    known_at: datetime           # tz-aware; when the event was knowable (vendor announcement/publish
                                 # time; never derived from fetched_at); point-in-time cutoffs use this
    # ratio (derived, exact): Fraction(new_shares, old_shares); never a stored float

class CorporateActionSource(Protocol):
    def get_actions(self, symbols: Sequence[str], start: date, end: date) -> tuple[CorporateAction, ...]: ...

class AlpacaCorporateActionSource: ...     # alpaca-py market-data corporate actions client; MarketDataError on failure
class CorporateActionStore:                # {root}/raw/corporate_actions/{SYMBOL}.parquet, atomic writes
    def write(self, actions: Sequence[CorporateAction]) -> None: ...
    def read(self, symbol: str) -> tuple[CorporateAction, ...]: ...

@dataclass(frozen=True, slots=True)
class AdjustmentCheck:
    symbol: str
    is_consistent: bool
    issues: tuple[str, ...]      # e.g. "split 2024-06-10 after latest fetched_at: full refetch required",
                                 # "unexplained 1-day move -74.9% on 2024-06-10"

def check_adjustment_consistency(stored: StoredSeries, actions: Sequence[CorporateAction],
                                 *, jump_threshold: float = 0.35) -> AdjustmentCheck: ...
```
Policy (documented in DECISIONS.md by T17):
- T46 (#46, PR #61, `feature/46-split-core`) implements the offline point-in-time split core as
  `semiedge.data.splits` (`SplitEvent(symbol, effective_session, new_shares, old_shares, known_at)`,
  `normalize_raw_splits(...)`). This section builds on it (imports it) and never duplicates it; a
  split `CorporateAction` converts to a `SplitEvent` with `effective_session = ex_date` and the same
  share counts and `known_at` (no float round-trip). Note the two meanings of "split-adjusted":
  vendor `adjustment: split` bars are adjusted as of their `fetched_at`; `normalize_raw_splits`
  output is adjusted as of its `as_of`/`known_at` cutoff. They are never mixed in one series.
- Bars are vendor split-adjusted (`adjustment: split`). `StoredSeries.fetched_at` is the
  **latest** fetch (`ParquetBarStore.read` exposes only the max per-row `fetched_at`), so the rule
  uses that value: a split with `ex_date` later than `StoredSeries.fetched_at` means the stored
  rows were adjusted on a pre-split basis → `is_consistent=False` ("full refetch required").
  Mixed-basis rows cannot arise through the store (`ParquetBarStore.write` refuses mixed-basis
  merges, F-012/F-024); tests therefore write mixed-basis fixtures as raw Parquet directly.
  Inconsistent symbols are ineligible
  (scanner, backtest data loading and engine POST_CLOSE treat them like stale data) until a full
  history refetch replaces every row.
- A one-session close-to-close move beyond `±jump_threshold` with no matching action → issue
  (ineligible until a human or a matching action explains it). Ratio-consistent moves on a
  split ex-date with matching action are accepted.
- Held-position handling: §9.4.

Acceptance: synthetic 4:1 split fixture — mixed-basis stored series (written as raw Parquet in
the test, bypassing `ParquetBarStore.write`) → inconsistent; after full
refetch → consistent and **no false breakout** (`cond_breakout` false on the split day of the
consistent adjusted series where the unadjusted series would trigger) and **no false stop** in
the backtest; unexplained −75% jump flagged; reverse split handled; source errors →
`MarketDataError`, never an empty "no actions" result.

---

## 15. Walk-forward, sensitivity and stress — `semiedge.backtest.walkforward/.sensitivity/.stress` (#17, T7)

Principle: the configured `momentum_v1` parameters are **frozen**. These tools measure how
fragile the result is; they never write configuration and never pick "the best" parameters for
use (ground rule 3).

```python
@dataclass(frozen=True)
class WalkForwardPlan:
    start: date; end: date
    train_sessions: int          # >= required_history; used ONLY by mode="selection"
    test_sessions: int           # >= 20
    step_sessions: int           # == test_sessions (non-overlapping OOS) unless stated

@dataclass(frozen=True)
class WindowResult:
    train: tuple[date, date] | None
    test: tuple[date, date]
    params_fingerprint: str
    metrics: PerformanceMetrics
    result_path: Path

def walk_forward(plan: WalkForwardPlan, request: BacktestRequest, data: BacktestData, *,
                 mode: Literal["fixed", "selection"], grid: ParameterGrid | None,
                 objective: Literal["expectancy_r", "sharpe"] | None,
                 gate_factory: Callable[[RiskLimits], EntryGate],
                 calendar: TradingCalendar) -> WalkForwardReport: ...

@dataclass(frozen=True)
class ParameterGrid:
    axes: Mapping[str, tuple[float | int, ...]]   # dotted MomentumV1Params / CostSettings field names
    def points(self) -> tuple[Mapping[str, float | int], ...]: ...   # cartesian, deterministic order

def sensitivity(grid: ParameterGrid, request: BacktestRequest, data: BacktestData, *,
                gate_factory: Callable[[RiskLimits], EntryGate],
                calendar: TradingCalendar) -> SensitivityReport: ...

# Defined in semiedge.backtest.metrics (T6, needed by period_breakdown); re-exported here.
STRESS_PERIODS: Mapping[str, tuple[date, date]] = {
    "2018Q4": (date(2018, 10, 1), date(2018, 12, 31)),
    "2020Q1": (date(2020, 1, 2), date(2020, 3, 31)),
    "2022":   (date(2022, 1, 3), date(2022, 12, 30)),
}
def stress_report(request: BacktestRequest, data: BacktestData, *, gate_factory: ...,
                  calendar: TradingCalendar) -> StressReport: ...
```
- `mode="fixed"` (primary): configured params on every test window; results reported per window
  and aggregated (median, IQR, share of windows with positive net return, worst window).
- `mode="selection"` (diagnostic only): within each train window choose the grid point
  maximising the pre-declared `objective` using **only train data** (bars after the train end are
  not passed to the selection step at all), then run it on the next test window. Reported
  separately as "selection procedure OOS", next to fixed-params OOS. Never applied to config.
- Every window is a fresh run (fresh equity, peak and halts) with warm-up data before its start;
  open positions at window end are closed and labelled.
- Grid axes may only name existing fields; a grid point that fails model validation is recorded
  as `invalid` with the validation message (not dropped). Risk limits are **not** a grid axis.
  Grids are committed (e.g. `config/research/sensitivity_v1.yaml`, required keys) before results
  are generated; reports include the grid file hash.
- Sensitivity output: tidy table exported to CSV and Parquet
  (`point_id, <axis columns…>, metric, value, reason`) for heatmaps, plus dispersion summary and
  the configured point's percentile rank per metric. Cost axis must include
  `slippage_bps ∈ {0, 5, 10, 20, 50}`; weights/saturations axes must be reported (momentum_v1.md
  "heuristic priors"; ADR-006 (D-006)).
- Stress: each period run with warm-up; if data does not cover a period (or the regime
  benchmark lacks history) → period reported `unavailable` with reason, never silently omitted.
  Each stress row repeats the survivorship label and lists which universe symbols had no data in
  the period (e.g. later IPOs), so survivorship is visible per period.

Acceptance: deterministic small-sample (synthetic, ≤ 300 sessions) runs; train/test windows
never overlap the OOS segment they score; **selection leakage test** — perturbing bars inside a
test window does not change the grid point selected for that window; invalid grid points are
reported; exported CSV/Parquet schema fixed; stress periods present (or `unavailable` with
reason) in every report.

---

## 16. Docker, health and graceful shutdown (#27, T15)

- `Dockerfile`: multi-stage, `python:3.12-slim`, `uv sync --locked --no-dev` (the dashboard image
  stage adds `--extra dashboard`, §12), non-root user
  `semiedge`, `WORKDIR /app`, no secrets baked, `HEALTHCHECK` via `semiedge health`.
- `compose.yaml`: services `engine` (`semiedge run`), `dashboard` (`semiedge dashboard`,
  `ports: ["127.0.0.1:8501:8501"]` — never `0.0.0.0`); named volume `semiedge-data:/app/data`
  shared (the dashboard needs write access to the state directory because SQLite WAL readers
  touch the `-shm` file and the halt insert writes; its read-only behaviour is enforced in code by
  §0.2 and §5.1, not by the mount);
  `env_file: .env` (never committed); `restart: unless-stopped`; `stop_grace_period: 60s`;
  no other ports. Example env defaults stay `TRADING_MODE=scanner`, `ENABLE_ORDER_SUBMISSION=false`,
  `ALLOW_LIVE_TRADING=false` (checked by `check_unsafe_config.py`).

```python
# semiedge/engine/health.py
@dataclass(frozen=True)
class HealthStatus:
    healthy: bool
    reasons: tuple[str, ...]          # e.g. "heartbeat engine stale 412s > 180s", "halt active: drawdown"

def check_health(store: ReadOnlyStateStore, *, component: str, max_age: timedelta,
                 now: datetime) -> HealthStatus: ...
```
CLI `semiedge health --component engine --max-age 180` exits 0 healthy / 1 unhealthy. An active
halt is reported but is **not** unhealthy (restarting would not fix it); a stale heartbeat or a
failed last `STARTUP_RECONCILE` is unhealthy.

Graceful shutdown (`semiedge.engine.shutdown`): SIGTERM/SIGINT set the `stop` event; the engine
finishes the in-flight broker call and its DB transaction, submits nothing new, does **not**
cancel any broker order (protective stops must stay live), writes heartbeat `status='stopped'`,
closes the DB, releases the lock and exits 0 within `stop_grace_period`. A second signal → exit
immediately (state is still consistent because of §5.3). On restart, `STARTUP_RECONCILE` runs
before anything else.

Acceptance: CI builds the image and runs `docker compose up -d` with scanner mode and no
credentials → `engine` becomes healthy (monitor-only, no broker access) and `docker compose
down` exits cleanly; unit test sends SIGTERM to an engine subprocess mid-job (fake gateway) →
exit 0, no partial order rows, restart reconciles clean.

---

## 17. New CLI commands (added by the owning task)

| Command | Task | Behaviour / exit codes |
|---|---|---|
| `semiedge backtest --start D --end D [--source cache\|synthetic] [--output DIR]` | T5 | runs §3 with real `RiskManager`; prints labels first; `--output` is a **new directory** created exclusively (an existing path is refused, nothing overwritten), unlike `scan --output`, which is a new file; `EXIT_OK` ok, `EXIT_CONFIG` config, `EXIT_DATA` data |
| `semiedge metrics RESULT.json` | T6 | §4 report |
| `semiedge walkforward --plan …` / `semiedge sensitivity --grid FILE` / `semiedge stress` | T7 | §15 |
| `semiedge run [--source cache\|synthetic]` | T12 | engine loop (§10.3; `synthetic` → synthetic DB under `{data_dir}/synthetic/`); 2 config/safety, 3 credentials, 5 lock held |
| `semiedge reconcile [--dry-run]` | T10 | §7; `EXIT_OK` clean/resolved, 6 halted, `EXIT_DATA` broker read error, 5 writer lock held (non-dry-run needs the engine stopped). `main` does not catch `BrokerError`, so the `reconcile` handler itself maps `BrokerError` → `EXIT_DATA` |
| `semiedge halt status \| set REASON \| clear ID --actor NAME` | T9 | human halt control via `semiedge.state.halt_control` (works while the engine runs; clear only here) |
| `semiedge flatten (--symbol S \| --all) --confirm` | T11 | §8.1 level 3; MANUAL halt first; 0 done, 5 lock held, 2 submission disabled |
| `semiedge health --component C --max-age S` | T15 | §16 |
| `semiedge dashboard [--source cache\|synthetic]` | T13 | launches Streamlit (§12) |

Exit codes 0/2/3/4 keep their Phase 1 meaning and are referenced only through the
`semiedge.cli` constants `EXIT_OK`/`EXIT_CONFIG`/`EXIT_CREDENTIALS`/`EXIT_DATA` (never literals);
1 is used only by `semiedge health`; 5 = writer lock held; 6 = reconciliation halted (new
constants added next to them by the owning task).

---

## 18. Cross-cutting acceptance checklist (reviewer)

- [ ] §0.2 import-boundary test exists and passes; no rows loosened.
- [ ] No module other than `semiedge.safety.broker` constructs a `TradingClient`; every broker
      write goes through `AlpacaPaperGateway` and calls `assert_order_submission_allowed`.
- [ ] No new configuration loosens `config/risk.yaml` or touches `semiedge/safety/limits.py`.
- [ ] Every new YAML field is required (no silent model defaults) and validated strictly.
- [ ] Every failure path produces a reason code; no bare `except: pass`; NaN/inf never persisted.
- [ ] Backtest/paper parity: same fill timeline (§3.2 ↔ §6.3/§9.1), same exit levels
      (`semiedge.strategy.exits`), same risk manager.
- [ ] All reported numbers carry the reproducing command; labels from §3.6 rendered first.
- [ ] Safety-marked tests added for every safety invariant; safety test count does not drop.
- [ ] Order intents only via `StateStore.insert_order_intent`; status only via `transition_order`;
      the `submitting` commit precedes every `send_order` call.
- [ ] Every risk decision is committed before any intent derived from it.
- [ ] `EntryProposal`s only via `build_entry_proposals`; exit levels only via `semiedge.strategy.exits`.
- [ ] In `semiedge.risk`, `.backtest`, `.state`, `.broker.gateway`, `.execution`, `.engine`, `.notify` the wall clock is read only through `semiedge.clock` (AST test: no `datetime.now`/`time.time` calls there).

## 19. Open questions (do not block implementation; decided by the named owner)

1. Earnings source (#20, H1, owner): until decided, PAPER entries are all rejected with
   `earnings_data_unavailable` (fail closed, `require_verified_earnings_data: true`); backtests
   must use `earnings_policy: waive_unavailable` and are labelled. **Consequence: the paper
   engine places no entries at all until H1 is resolved.**
2. Alpaca `opg` + `gtc` OCO support (§6.3): T9 verifies; a negative result requires a spec change.
3. Gap overshoot of exposure caps (§2.2): accepted residual for paper; owner may later choose
   limit-at-open entries, which is a strategy-execution spec change.
4. Entry gap handling (§3.2 step 3, §6.3): an entry whose open fill is at/below the stop or
   at/above the target is flattened immediately (both modes). Owner may prefer keeping the
   above-target case open with protection; changing it is a spec change made before results.
5. Drawdown peak reset (§8): no code path; owner decides if/when a reset is ever allowed.
6. `semiedge flatten` (§8.1 level 3) is specified for T11; owner to confirm it is wanted as a
   CLI rather than a manual action in the Alpaca paper dashboard.
