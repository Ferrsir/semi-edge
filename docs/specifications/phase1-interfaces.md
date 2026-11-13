# Specification — Phase 1 module interfaces

Status: **Specified (issues #8–#14).** This is the contract between Phase 1 modules so they can
be implemented and reviewed independently. Changing a signature here requires updating this
document in the same PR.

Already implemented (foundation, #8): `semiedge.config` (models, settings, loader),
`semiedge.safety` (modes, limits, broker factory), `semiedge.data.schema` (frame contract,
`DataFeed`, `Adjustment`, `DataSource`), `semiedge.logconfig`.

General rules
- Python ≥ 3.12, `from __future__ import annotations`, full type hints, `mypy --strict` clean.
- pandas 3.x semantics (copy-on-write; default string dtype). Never use chained assignment.
- Pure functions where possible; no network or filesystem access in strategy/indicator code.
- Datetimes are timezone-aware UTC (`datetime.now(UTC)`); session *dates* are `datetime.date`.
- Fail closed: missing/invalid inputs produce explicit reasons or exceptions, never defaults.
- Tests: no network, no real credentials. Mark invariant tests `@pytest.mark.safety`,
  adapter/persistence tests `@pytest.mark.integration`.

---

## `semiedge.data.calendar` (#9)

```python
class TradingCalendar:
    def __init__(self, name: str = "XNYS") -> None: ...
    def sessions_in_range(self, start: date, end: date) -> list[date]: ...      # inclusive
    def is_session(self, day: date) -> bool: ...
    def session_close(self, session: date) -> datetime: ...                    # tz-aware UTC; early closes respected
    def previous_session(self, session: date) -> date: ...
    def next_session(self, day: date) -> date: ...                             # first session strictly after `day`
    def last_completed_session(self, now: datetime, settle_minutes: int) -> date:
        """Latest session whose close + settle_minutes <= now (now must be tz-aware)."""
    def sessions_window(self, end: date, count: int) -> list[date]: ...        # last `count` sessions ending at `end` (inclusive)
```
Backed by `exchange_calendars`. Must handle holidays and early closes. Naive `now` → `ValueError`.

## `semiedge.data.source` (#9)

```python
class MarketDataSource(Protocol):
    source: DataSource
    feed: DataFeed
    adjustment: Adjustment
    def get_daily_bars(self, symbols: Sequence[str], start: date, end: date) -> dict[str, pd.DataFrame]:
        """Frames satisfy semiedge.data.schema.validate_bar_frame. Symbols with no data map to an empty frame."""
```

## `semiedge.data.alpaca_source` (#9)

```python
class AlpacaMarketDataSource:  # implements MarketDataSource
    def __init__(self, client: StockHistoricalDataClient, feed: DataFeed, adjustment: Adjustment,
                 *, calendar: TradingCalendar | None = None) -> None: ...      # default TradingCalendar("XNYS")
    @classmethod
    def from_credentials(cls, api_key: str, secret_key: str, feed: DataFeed, adjustment: Adjustment) -> AlpacaMarketDataSource: ...
```
- Uses `StockBarsRequest(timeframe=TimeFrame.Day, feed=..., adjustment=...)`. Maps our enums to alpaca enums.
- Converts Alpaca daily bar timestamps to the America/New_York calendar date → `session` index.
- Never returns a bar for a session that has not closed yet (`session_close > now`, early closes
  respected): an in-progress daily bar is partial and would otherwise be cached as complete.
- Raises `MarketDataError` (defined in this module) on API errors — never returns partial data silently.
- Never requests bars that end after "now − 16 minutes" when `feed=sip` (free-plan SIP restriction);
  the caller passes `end`; the adapter clamps and records the clamp in a log line.

## `semiedge.data.synthetic` (#9, #14)

```python
class SyntheticMarketDataSource:  # implements MarketDataSource; source=SYNTHETIC, feed=SYNTHETIC
    def __init__(self, seed: int = 7, calendar: TradingCalendar | None = None) -> None: ...
```
Deterministic (seeded, per-symbol derived seed) daily bars on real XNYS sessions. Output must be
identical across runs/platforms for the same arguments. Clearly labelled synthetic everywhere it
is surfaced. Used for demos and tests only — never mixed with real data.

## `semiedge.data.store` (#9)

```python
@dataclass(frozen=True)
class StoredSeries:
    symbol: str
    frame: pd.DataFrame            # bar frame contract (provenance columns stripped)
    feed: DataFeed
    adjustment: Adjustment
    source: DataSource
    fetched_at: datetime           # most recent fetch timestamp among rows

class ParquetBarStore:
    def __init__(self, root: Path) -> None: ...
    def write(self, symbol: str, frame: pd.DataFrame, *, feed: DataFeed, adjustment: Adjustment,
              source: DataSource, fetched_at: datetime) -> Path: ...
    def read(self, symbol: str, *, feed: DataFeed, adjustment: Adjustment) -> StoredSeries | None: ...
    def symbols(self, *, feed: DataFeed, adjustment: Adjustment) -> list[str]: ...
    def summary(self) -> pd.DataFrame: ...   # DuckDB over all parquet files: symbol, feed, adjustment, source, first_session, last_session, rows, last_fetched_at
```
- Layout: `{root}/raw/bars/daily/feed={feed}/adjustment={adjustment}/{SYMBOL}.parquet`.
- Raw rows persisted with provenance columns `feed, adjustment, source, fetched_at` (UTC).
- `write` merges with existing rows: on duplicate `session`, the newer `fetched_at` wins
  (vendor corrections), and a WARNING is logged when OHLCV values change for an existing session.
- Adjusted series (`adjustment != raw`) are restated retroactively by the vendor (splits,
  dividends), so a merge must prove that retained stored rows share the incoming basis: the
  incoming frame must overlap at least one stored session, and every retained stored row (one the
  incoming frame does not replace, including stored sessions missing *inside* the incoming range)
  is anchored by its nearest shared session on each side that exists (the following one for rows
  before all shared ones, the preceding one for rows after them, both for rows in between); the
  close on each anchor must match. "Retained" means rows that survive the fetch-time tie-break
  from the store, so a shared session whose stored fetch is newer is retained and anchors itself.
  Otherwise `write` raises `ValueError` and the caller refetches the full stored range. Raw series may be appended without overlap.
- Writes are atomic (write temp file in the same dir, then `os.replace`).
- Synthetic data may only be written under a root whose path contains a `synthetic` component
  (raise otherwise) so it can never pollute the real cache.

## `semiedge.data.validation` (#10)

```python
def filter_completed_bars(frame: pd.DataFrame, as_of: date) -> pd.DataFrame: ...   # drop sessions > as_of

@dataclass(frozen=True)
class DataQualityReport:
    symbol: str
    as_of: date
    last_session: date | None
    is_fresh: bool                 # last_session == as_of
    missing_sessions: tuple[date, ...]   # expected sessions (within lookback window ending at as_of) absent from frame
    feed: DataFeed | None
    issues: tuple[str, ...]        # human-readable reasons (also covers OHLC sanity: high>=max(open,close), low<=min(open,close), volume>=0, positive prices)
    @property
    def is_actionable(self) -> bool: ...   # fresh, no blocking issues, missing <= allowed

def assess_quality(frame: pd.DataFrame, *, symbol: str, as_of: date, calendar: TradingCalendar,
                   lookback_sessions: int, feed: DataFeed | None,
                   max_missing_sessions: int = 0) -> DataQualityReport: ...
```

## `semiedge.indicators` (#11)

Pure functions on `pd.Series` (index = session). All return float64 Series aligned to input,
NaN where the window is incomplete (`min_periods = window`; never partial windows).

```python
def sma(series: pd.Series, window: int) -> pd.Series: ...
def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series: ...
def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series: ...  # Wilder, see momentum_v1.md
def prior_rolling_max(series: pd.Series, window: int) -> pd.Series: ...   # max of t-window .. t-1
def prior_rolling_mean(series: pd.Series, window: int) -> pd.Series: ...  # mean of t-window .. t-1
def trailing_return(close: pd.Series, window: int) -> pd.Series: ...      # close_t / close_{t-window} - 1
def rolling_fraction_true(mask: pd.Series, window: int) -> pd.Series: ... # fraction of True over t-window+1..t; NaN if any input NaN in window
```

## `semiedge.strategy` (#12)

```python
# semiedge/strategy/base.py
@dataclass(frozen=True)
class ConditionResult:
    name: str                 # e.g. "sector_regime", "above_sma_fast", "above_sma_slow", "breakout", "relative_strength", "relative_volume"
    passed: bool
    value: float | None       # observed value (None if undefined)
    threshold: float | None
    description: str          # human-readable, e.g. "SMH close 251.30 > SMA50 240.12"

@dataclass(frozen=True)
class SignalEvaluation:
    symbol: str
    session: date | None
    strategy: str             # "momentum_v1"
    strategy_version: str
    eligible: bool
    conditions: tuple[ConditionResult, ...]
    score: float | None       # 0..100 when inputs defined
    score_components: Mapping[str, float]   # keys s_rs, s_bo, s_rv, s_tp (defined ones only)
    close: float | None
    atr: float | None
    proposed_stop: float | None
    proposed_target: float | None
    rejection_reasons: tuple[str, ...]      # empty iff eligible
    rank: int | None          # 1-based among eligible, else None

# semiedge/strategy/momentum_v1.py
def compute_features(bars: pd.DataFrame, regime: pd.DataFrame, params: MomentumV1Params, *,
                     expected_sessions: Iterable[date] | None = None) -> pd.DataFrame:
    """Vectorised per-session features & condition booleans for ONE symbol (reused by the backtester).
    Columns include: close, sma_fast, sma_slow, prior_high, atr, ret, regime_close, regime_sma, regime_ret,
    excess_ret, prior_volume, rel_volume, trend_persistence, cond_* booleans, s_rs, s_bo, s_rv, s_tp, score, all_conditions."""

def evaluate_universe(bars: Mapping[str, pd.DataFrame], regime_symbol: str, params: MomentumV1Params,
                      as_of: date, *, tradable: Sequence[str],
                      quality: Mapping[str, DataQualityReport] | None = None,
                      expected_sessions: Iterable[date] | None = None) -> list[SignalEvaluation]:
    """Evaluate every tradable symbol at `as_of`; eligible first ranked by score desc then symbol."""
```
Regime frame alignment: join on session; a session missing from either series makes that row undefined.
`expected_sessions` is the exchange session calendar (e.g. `TradingCalendar.sessions_in_range`) covering
the bars. With it, a session missing from BOTH series is also an undefined row, and a bar on a
non-session is a `DataSchemaError`. The backtester must pass it to `compute_features`.
`evaluate_universe` makes every symbol ineligible when it is `None` or does not contain `as_of`, and
makes an otherwise eligible symbol ineligible when its proposed stop `C_t - k·ATR_t` is `<= 0`.
Threshold comparisons use IEEE-754 doubles without tolerance (a value that rounds just below an
inclusive threshold fails — the conservative reading).

## `semiedge.risk.sizing` (#12)

```python
@dataclass(frozen=True)
class SizingResult:
    shares: int
    shares_by_risk: int
    shares_by_allocation: int
    binding_constraint: Literal["risk", "allocation", "invalid"]
    dollar_risk: float        # shares * (entry - stop)
    notional: float           # shares * entry
    reason: str | None        # set when shares == 0

def propose_position_size(equity: float, entry_price: float, stop_price: float, *,
                          max_risk_per_trade_pct: float, max_position_pct: float) -> SizingResult: ...
```
Non-finite or non-positive inputs, or `stop >= entry`, → `shares=0, binding_constraint="invalid"`.

## `semiedge.broker.account` (#13) — read-only

```python
AccountReadPath = Literal["/account", "/clock", "/positions"]

class ReadOnlyTradingClient(Protocol):          # alpaca TradingClient satisfies it structurally
    def get(self, path: AccountReadPath) -> object: ...   # HTTP GET, returns the decoded JSON

class AccountBlockReason(StrEnum):
    STATUS_NOT_ACTIVE; ACCOUNT_BLOCKED; TRADING_BLOCKED; TRADE_SUSPENDED_BY_USER; UNSUPPORTED_CURRENCY

@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str; qty: float; side: Literal["long", "short"]; avg_entry_price: float
    market_value: float; cost_basis: float; unrealized_pl: float; unrealized_plpc: float
    current_price: float; asset_class: str

@dataclass(frozen=True)
class AccountSnapshot:
    account_number_masked: str; status: str; currency: str
    equity: float; last_equity: float; cash: float; buying_power: float
    long_market_value: float; pattern_day_trader: bool | None   # None = not reported (Alpaca removed it)
    trading_blocked: bool; account_blocked: bool; trade_suspended_by_user: bool
    market_is_open: bool; next_open: datetime; next_close: datetime; as_of: datetime
    positions: tuple[PositionSnapshot, ...]                    # sorted by symbol
    @property
    def block_reasons(self) -> tuple[AccountBlockReason, ...]: ...  # empty iff tradable
    @property
    def is_tradable(self) -> bool: ...

class BrokerError(RuntimeError): ...            # sanitised request failure (.operation, .status_code)
class BrokerDataError(BrokerError): ...         # response violates the snapshot contract

def fetch_account_snapshot(client: ReadOnlyTradingClient) -> AccountSnapshot: ...   # GET /v2/account, /v2/clock, /v2/positions
```
Clients are only obtained via `semiedge.safety.broker.create_paper_trading_client`; the client is
re-checked (type-exact base URL, then `assert_paper_client`) before any request. No order methods are called anywhere in
Phase 1. The raw JSON is validated, not the SDK models: their lax pydantic coercion turns `0` or
`"false"` into `False` for a blocking flag (F-009). Exact JSON types are required: block flags,
`is_open` and (when present) `pattern_day_trader` must be JSON booleans; timestamps must be RFC 3339
strings with an explicit offset. Fail closed: missing/malformed/non-finite numbers, negative `equity`/`last_equity`/
`buying_power`/`long_market_value`, missing or non-ISO currency, missing, `null` or non-boolean block flags,
position values whose sign contradicts the side, and an inconsistent clock raise
`BrokerDataError` (never defaults). Blocked accounts are reported via explicit `block_reasons`, not hidden.
Money stays `float` (strictly parsed from Alpaca's decimal strings) to match the float-based
sizing and risk interfaces. Errors never echo credentials, URLs or response bodies.

## `semiedge.reporting` and `semiedge.cli` (#14)

`semiedge` console entry point (`semiedge.cli:main(argv: Sequence[str] | None = None) -> int`):

| Command | Behaviour |
|---|---|
| `semiedge version` | package version, git SHA if available |
| `semiedge config check` | load + validate env and YAML; print safety status, config fingerprint; secrets masked |
| `semiedge data fetch [--symbols ...] [--start D] [--end D]` | Alpaca → ParquetBarStore (requires credentials) |
| `semiedge data status` | store summary + freshness per symbol |
| `semiedge scan [--source cache\|synthetic] [--as-of D] [--equity X] [--format table\|json\|markdown] [--output PATH]` | ranked report |
| `semiedge account` | read-only paper account snapshot (requires credentials) |

Signal report (`semiedge.reporting.signal_report`): `SignalReport` dataclass with `run_id`,
`generated_at`, `as_of`, `mode`, `source`, `feed`, `adjustment`, `strategy`+`version`,
`config_fingerprint`, `semiedge_version`, `git_sha`, `equity_used` + `equity_source`
("reference" | "account" | "cli"), `evaluations`, per-symbol `DataQualityReport`s, and
`proposed sizes`. Renderers: table (plain text), markdown, JSON (stable schema). Reports are
persisted to `{data_dir}/reports/signals/{as_of}_{run_id}.json`. Synthetic reports carry a
prominent `SYNTHETIC DATA — NOT MARKET DATA` banner and are written under
`{data_dir}/synthetic/...`.

Exit codes: 0 success (including "no candidates"), 2 configuration/safety error,
3 missing credentials, 4 data unavailable/stale for the regime benchmark (fail closed).
