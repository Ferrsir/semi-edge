# SEMI-EDGE independent audit report

Running record of independently verified findings. Each entry names the severity, location,
reproduction, impact, fix and test evidence. **Confirmed** findings were reproduced; **Speculative**
concerns are risks that have not been demonstrated and must not be read as bugs.

Severity: **P0** could place unintended orders / reach a live endpoint / lose money uncontrolled ·
**P1** wrong trading decision, wrong P&L, or broken build/safety guarantee · **P2** robustness or
diagnosability gap with a workaround · **P3** hygiene.

Auditors: Claude Code (lead/integrator), Codex (independent auditor, issue #34).

---

## Confirmed findings

### F-001 · P1 · `.gitignore` excluded the `semiedge.data` source package
- **Where:** `.gitignore` rule `data/` (unanchored); affects `src/semiedge/data/**`.
- **Reproduce:** `git check-ignore -v src/semiedge/data/schema.py` → matched `data/`.
  `git ls-files src/semiedge/data` at commit `1ebc169` returned nothing, although that commit's
  message and `phase1-interfaces.md` state `semiedge.data.schema` is implemented.
- **Impact:** every fresh clone (CI, deployment, the other agent) lacked the bar-frame contract;
  `semiedge.config.models` imports it, so configuration loading — including the paper-only safety
  guard — failed with `ImportError`. Any new module under `src/semiedge/data/` (calendar, store,
  validation) would also have been silently dropped from commits by `git add -A`.
  Codex hit this independently and had recreated a reduced stand-in `schema.py` to restore imports.
- **Fix (implemented):** anchor the rules to the repository root (`/data/`, `/logs/`); restore the
  full specified contract module (`DataFeed` incl. `DELAYED_SIP`, `DataSource`,
  `validate_bar_frame`, `empty_bar_frame`).
- **Test evidence:** `tests/test_repo_hygiene.py::test_no_source_file_is_gitignored` runs
  `git check-ignore --no-index` over every source/test/config file;
  `test_data_package_importable` imports the contract.

### F-002 · P2 · Duplicate YAML keys silently applied the last value (found by Codex)
- **Where:** `semiedge.config.loader._read_yaml` (used `yaml.safe_load`).
- **Reproduce:** a `risk.yaml` containing `max_position_pct: 0.01` then `max_position_pct: 0.05`
  loaded as `0.05`.
- **Impact:** a reviewer reading the first occurrence approves a tighter limit than the one
  enforced. Within ceilings, so not a ceiling bypass, but an approval-integrity hole.
- **Fix (implemented):** `_UniqueKeyLoader` (SafeLoader subclass) rejects duplicate and non-string
  keys; empty files are rejected instead of becoming `{}`.
- **Test evidence:** `tests/unit/test_config_audit.py::test_duplicate_yaml_values_are_rejected`,
  `test_empty_or_nonstring_key_configuration_is_invalid`.

### F-003 · P1 · Safety flags and numeric limits accepted coerced / non-finite values (found by Codex)
- **Where:** `semiedge.config.models._Frozen`, `semiedge.safety.modes.validate_safety_flags`.
- **Reproduce:** `order_submission_permitted(PAPER, enable_order_submission="false", ...)` treated
  the non-empty string as truthy; `RiskLimits(allow_leverage=0)`, `max_positions=2.0`,
  `reference_equity="100000"` / `inf` / `nan` validated.
- **Impact:** a typo or string value could enable order submission, and `nan`/`inf` equity or
  thresholds make every comparison false/true, silently disabling sizing or signal gates.
- **Fix (implemented):** strict type checks for float/int/bool fields; `allow_inf_nan=False`;
  the raw safety guard requires real `bool` objects.
- **Test evidence:** `tests/safety/test_safety_audit.py::test_raw_guard_rejects_non_boolean_flags`
  (14 cases), `tests/unit/test_config_audit.py` non-finite and coercion cases.

### F-004 · P2 · Secrets could leak through tracebacks, nested extras and JSON escaping (found by Codex)
- **Where:** `semiedge.logconfig.SecretRedactionFilter` / `configure_logging`.
- **Reproduce:** `logger.exception(...)` with the secret in the exception message, in a nested
  `extra={"details": {...}}`, or containing a newline (JSON-escaped form) rendered unredacted;
  secrets shorter than 4 characters were skipped.
- **Impact:** broker credentials or Discord webhook URLs written to logs.
- **Fix (implemented):** redact the fully rendered record (`_RedactingFormatter`), register the
  JSON-escaped form of each secret, drop the length floor.
- **Test evidence:** `tests/safety/test_safety_audit.py::test_configured_logging_redacts_nested_fields_and_exception` (6 cases).

### F-005 · P3 · Non-paper endpoint rejection echoed the URL, including userinfo (found by Codex)
- **Where:** `semiedge.safety.broker.assert_paper_client`.
- **Fix (implemented):** the message names only the allowed paper URL.
- **Test evidence:** `test_endpoint_rejection_does_not_echo_url_credentials`.

### F-006 · P3 · `ruff format` (>=0.17) rewrote Python blocks inside Markdown specs
- **Where:** `pyproject.toml` `[tool.ruff]`.
- **Impact:** `ruff format --check .` in CI would fail once specs are merged; specs lost alignment.
- **Fix (implemented):** `extend-exclude = ["*.md"]`.

### F-007 · Confirmed · P2 · Bar schema accepted invalid index units and optional dtypes
- **Where:** `semiedge.data.schema.validate_bar_frame`.
- **Reproduction:** `f = empty_bar_frame(); f.index = f.index.as_unit("us");
  f["vwap"] = pd.Series(dtype="str"); validate_bar_frame(f)` returned successfully.
  Duplicate `close` columns instead raised an incidental `AttributeError` at `.dtype`.
- **Impact:** adapters could pass frames violating the documented nanosecond index and
  float64 optional-column contract, causing downstream failures outside the explicit
  `DataSchemaError` rejection path. Missing (`NaT`) session labels were also not explicitly rejected.
- **Fix:** require nanosecond session labels without NaT, unique columns, and float64
  optional columns when present. No signatures or safety limits changed.
- **Test evidence:** `tests/unit/data/test_schema_audit.py` (five regression cases), run with
  `.venv/Scripts/python -m pytest -q tests/unit/data/test_schema_audit.py`.

### F-008 · P2 · Missing app configuration sections silently selected defaults (Codex, #37)
- **Where:** `semiedge.config.loader.load_app_config`.
- **Reproduce:** replace `app.yaml` with `{}`, `data: {}`, or `scanner: {}` and run
  `.venv/Scripts/python -m pytest -q tests/unit/test_config.py -k missing_app_sections`.
  Before the fix all three cases failed with `DID NOT RAISE ConfigError`.
- **Impact:** accidentally deleted sections reset the feed/adjustment or proposed sizing equity
  to model defaults without an explicit configuration error, violating fail-closed loading.
- **Fix:** require both top-level sections before model validation; explicitly present sections
  retain their documented field defaults. No risk ceilings or strategy parameters changed.
- **Regression:** `test_missing_app_sections_fail_closed` covers each missing-section combination;
  existing repository-config loading covers the valid configuration.

### F-009 · P1 · Bar store spliced split/dividend adjustment bases into one series (Claude, PR #36)
- **Where:** `semiedge.data.store.ParquetBarStore.write` (merge path) for `adjustment != raw`;
  `config/app.yaml` uses `adjustment: split`.
- **Reproduce:** store split-adjusted bars through 2024-05-31, then write post-split (restated,
  /10) bars from 2024-06-03 without overlap, or a partial refetch whose shared sessions changed.
  Before the fix both writes succeeded and the stored series showed a fictitious ~90% gap
  (`tests/integration/data/test_store_adjustment_basis.py`: 3 cases failed with
  `DID NOT RAISE ValueError`).
- **Impact:** vendors restate adjusted history retroactively, but the store kept rows outside the
  new fetch range on the old basis. SMA, breakout, ATR, return and stop levels computed on the
  merged series would be wrong (wrong trading decision), and nothing downstream could detect it.
- **Fix:** adjusted merges must overlap the stored range; the close on the earliest/latest shared
  session anchoring retained rows must match, else `ValueError` (refetch the full range). Raw series
  still accept disjoint appends. Spec `phase1-interfaces.md` (store) updated.
- **Regression:** `tests/integration/data/test_store_adjustment_basis.py` (6 cases),
  `tests/unit/data/test_store.py::test_disjoint_adjusted_append_rejected`,
  `test_dividend_rebase_detected_on_anchor_close`, `test_backfill_checks_latest_shared_session`.

### F-010 · P2 · Alpaca adapter returned in-progress (partial) daily bars (Claude, PR #36)
- **Where:** `semiedge.data.alpaca_source.AlpacaMarketDataSource.get_daily_bars`.
- **Reproduce:** fake client returning a 2024-03-12 bar with `now = 2024-03-12 15:00 UTC`
  (11:00 ET, session open); before the fix the partial bar was returned for IEX, SIP and
  delayed SIP (`tests/unit/data/test_alpaca_source.py::test_in_progress_session_bar_is_not_returned`).
- **Impact:** a partial bar is indistinguishable from a completed one once cached; any later fetch
  that does not re-cover that session leaves it permanently, so a later `as_of` treats an intraday
  snapshot as the session's close/high/volume.
- **Fix:** drop sessions whose exchange close (early closes respected) is after `now`, with an INFO
  log; optional keyword `calendar` on the constructor (spec updated). Settle-window handling stays
  with the caller via `last_completed_session(now, bar_settle_minutes)`.
- **Regression:** `test_in_progress_session_bar_is_not_returned` (3 feeds),
  `test_early_close_session_is_returned_once_closed`.

### F-011 · P3 · Store logged an OHLCV correction when nothing stored changed (Claude, PR #36)
- **Where:** `ParquetBarStore.write`.
- **Reproduce:** write a correction, then rewrite the original rows with an older `fetched_at`; the
  older rows lose the merge but the WARNING was still emitted.
- **Fix:** compare stored rows with the merged result. **Regression:**
  `tests/integration/data/test_adapters_store.py::test_roundtrip_merge_summary` (older refetch).

### F-012 · P1 · Adjusted refetch with an interior gap kept a stored row on the old basis (Claude review, PR #36)
- **Where:** `semiedge.data.store._check_adjustment_basis` (head `492b75e`).
- **Reproduce:** store split-adjusted bars 2024-01-02..2024-06-28, then write the same range
  restated /10 (post-split) but missing one interior session (vendor gap). Before the fix the write
  succeeded and the stored series had a ~9x one-day return at the retained old-basis session.
- **Impact:** a fictitious price jump inside the series corrupts SMA/ATR/breakout
  and stop levels; the anchors only checked the first/last shared session when stored rows extended
  past the incoming range, so rows retained *inside* the range were never verified.
- **Fix:** every retained stored row is anchored by its nearest shared session on each side that
  exists; spec `phase1-interfaces.md` (store) updated. The first fix (`64eb0b4`) used only the
  following session. That still accepted a gap on the session before an ex-date, because the split
  rescales the preceding session but not the following one. Closed by the both-sides anchor
  (2026-11-13).
- **Regression:** `tests/unit/data/test_store.py::test_interior_gap_in_rebased_refetch_rejected`,
  `test_interior_gap_before_ex_date_rejected` (fails on `64eb0b4`),
  `tests/integration/data/test_store_adjustment_basis.py::test_rebased_refetch_with_interior_gap_is_rejected`,
  `test_same_basis_refetch_with_interior_gap_keeps_stored_session`.


### F-024 · P1 · Fetch-time tie-breaking kept stored rows that the basis check never anchored (Codex review, PR #36)
- **Where:** `semiedge.data.store.ParquetBarStore.write` / `_check_adjustment_basis` (head `b2c43f0`).
- **Reproduce:** write split-adjusted 2024-01-02..03 (closes 100) at fetched_at Jan 4, refetch Jan 3
  unchanged at Jan 6, then write both sessions rebased /10 at Jan 5. The check only anchored stored
  sessions missing from the refetch (none), but tie-breaking kept the newer stored Jan 3: the
  persisted closes were [10, 100], a fictitious +900% one-session return.
- **Fix:** the merge runs the fetch-time tie-break first. The basis check then anchors every row that
  survives *from the store*, including shared sessions where the stored fetch is newer; such a row
  is its own anchor. A rebased write whose rows are all superseded by newer stored rows, or one at an
  equal fetch time, is now rejected instead of silently ignored. Equal-timestamp keep-existing still
  holds for same-basis corrections.
- **Regression:** `tests/unit/data/test_store.py::test_newer_stored_row_cannot_splice_bases_with_older_refetch`,
  `test_fully_newer_store_ignores_older_rebased_refetch_rows`,
  `tests/integration/data/test_adapters_store.py::test_equal_timestamp_rebased_write_is_rejected_not_ignored`
  (all three fail on `b2c43f0`); same-basis out-of-order and full newer replacement stay accepted
  (`test_same_basis_out_of_order_refetch_is_accepted`, `test_newer_rebased_refetch_replacing_everything_is_accepted`).

### F-026 · P2 · Validated finite target multiple produced an eligible signal with an infinite target (Codex review, PR #48)
- **Where:** `semiedge.strategy.momentum_v1._evaluate_symbol` (head `38b7865`).
- **Reproduce:** `target_atr_multiple=1.7e308` (accepted by `MomentumV1Params`) on the standard eligible
  fixture gave `eligible=True`, `proposed_target=inf`.
- **Fix:** `_trade_levels` computes stop and target before eligibility; a stop that is not positive
  or a target that is not finite and above the close makes the symbol ineligible with an explicit
  reason. Default formulas and thresholds are unchanged (no strategy change).
- **Re-review (Codex, `92e9b48`): not fully closed.** Finite highs of 1e308 overflow the ATR seed to
  inf, `_opt` maps it to `None`, and `_trade_levels` was only called when ATR was defined, so the
  symbol was eligible with no stop or target. Fixed: once every condition passes, `_trade_levels`
  always runs and rejects an undefined close or a non-finite or non-positive ATR.
- **Regression:** `tests/unit/strategy/test_momentum_v1_evaluate.py::test_non_finite_proposed_target_makes_symbol_ineligible`,
  `test_overflowing_atr_makes_symbol_ineligible` (fails on `92e9b48`).

### F-027 · P3 · Oversized integer sizing input raised OverflowError (Codex review, PR #48)
- **Where:** `semiedge.risk.sizing._positive_finite`.
- **Reproduce:** `propose_position_size(10**1000, 100, 95, ...)` raised instead of returning the
  documented invalid result.
- **Fix:** float conversion failures return `shares=0`, `binding_constraint="invalid"` with a reason.
- **Regression:** `tests/safety/test_position_sizing.py::test_integer_too_large_for_float_is_invalid_not_an_exception`.

### F-030 · P2 · `semiedge account` let paper-client construction errors escape unsanitised (Codex review, PR #50)
- **Where:** `semiedge.cli._cmd_account` (head `c1f8cf8`).
- **Reproduce:** patch `cli.paper_account_client` to raise `ValueError("FAKE_SECRET_SENTINEL")`;
  `cli.main(["account"])` raised uncaught, so the traceback printed the sentinel.
- **Fix:** construction is inside the sanitised boundary. `MissingCredentialsError` (exit 3) and
  `SafetyViolation` (exit 2) are re-raised unchanged; any other exception exits 4 naming only
  the exception type.
- **Regression:** `tests/integration/broker/test_cli_account.py::test_account_client_construction_errors_are_sanitised`
  (3 exception types; fails on `c1f8cf8`).

### F-037 · P2 · `StateReader.orders` had two incompatible signatures (Codex review, PR #62)
- **Where:** `docs/specifications/phase2-4-interfaces.md` §5.1, §7 (head `4f8eb8d`).
- **Reproduce:** `StateStore.orders(*, statuses, symbol, kinds)` vs `ReadOnlyStateStore.orders(limit)`;
  §7 requires both to satisfy one `StateReader` for `diff_state` / `reconcile --dry-run`.
- **Impact:** dry-run reconciliation could not be implemented against the spec without an
  undocumented adapter; the two paths could diverge.
- **Fix (spec):** one `@runtime_checkable StateReader` Protocol in `semiedge.state.models`:
  `orders(*, statuses, symbol, kinds, limit) -> list[OrderRecord]` (limit `None` or int > 0, newest
  first when set), `fills`, `positions`, `active_halts`, `corporate_action_adjustments`,
  `store_id`; identical on both stores.
- **Acceptance:** signature equality + equal results on one DB (§5.4); `diff_state` on both stores
  gives equal reports for every reconcile scenario (§7).

### F-038 · P2 · No deduplication between fallback and activity fill sources (Codex review, PR #62)
- **Where:** §5.2 `fills`, §6.4, §7 (head `4f8eb8d`).
- **Reproduce:** activities unavailable → cumulative 30 ingested as `cid:30`; activities recover →
  the same execution arrives as activity `A(30)` with a different key and passes key idempotency:
  local 60 vs broker 30.
- **Impact:** spurious reconciliation halt and oversized protection attempts (refused by the
  gateway's no-short guard).
- **Fix (spec):** per-order cumulative watermark `W = max(Σ activity qty, max provisional cum_qty)`;
  broker cumulative `filled_qty` is the upper bound; activity rows are authoritative (`final`);
  fallback rows record only `B − W` as `provisional`; on recovery, provisional rows covered by
  activities become `superseded` (kept, excluded) in one transaction; exceeding the broker
  cumulative → `StateStoreError` + FILL_MISMATCH halt.
- **Acceptance:** fallback 30 → `A(30)` → `B(20)` ⇒ local 50, no halt, one superseded row; replay
  idempotent; activities above broker cumulative → error, nothing written (§5.4, §6.5, §7).

### F-039 · P2 · Split recovery contradicted fill-derived quantity and was not idempotent (Codex review, PR #62)
- **Where:** §7, §9.4 (head `4f8eb8d`).
- **Reproduce:** 4:1 split, broker 100 → 400 with no executions; §7 declares any qty ≠ entry − exit
  fills a POSITION_QTY_MISMATCH halt; §9.4 rescaled levels but had no quantity adjustment record
  or event identity, so a restart could rescale again.
- **Impact:** every held split either halted or risked double rescaling.
- **Fix (spec):** verified split (§14 event with `event_id`, knowledge-verified `fetched_at ≤ now`)
  applied at reconcile step 3b, before the generic check, via
  `StateStore.apply_corporate_action_adjustment`: new table `corporate_action_adjustments`
  UNIQUE `(symbol, event_id)`; one transaction scales qty, divides avg price/stop/target, records
  the non-fill delta used in expected qty; re-application is a no-op. Unverified or inexact →
  POSITION_QTY_MISMATCH halt.
- **Acceptance:** 100 → 400 reconciles without halt; second run and restart do not rescale;
  unverified 100 → 400 halts (§5.4, §7, §9.4).

### F-040 · P2 · `slippage_bps` domain allowed zero/negative sell fills (Codex review, PR #62)
- **Where:** §3.1, §3.2 (head `4f8eb8d`).
- **Reproduce:** `slippage_bps=10000` (valid under `>= 0`) → sell fill `open·(1−1) = 0`; larger →
  negative; `nan`/`inf` unrestricted.
- **Impact:** nonsensical P&L instead of a fail-closed error.
- **Fix (spec):** `BacktestSettings` validator: finite `0 <= slippage_bps < 10000`; every computed
  fill price finite and `> 0`, every cost finite, else `ValueError` naming the trade. Input-domain
  validation only; no cost value or risk limit changed.
- **Acceptance:** `10000`, `1e6`, `nan`, `inf` → `ConfigError`; cost overflow → `ValueError` (§3.8).
- **Also clarified (same review):** entry gap invalidation uses the slipped fill (open 99, target
  100, 200 bps → 100.98 ⇒ invalidated); end-of-window liquidation costs replace the last equity
  point (zero positions/exposure); levels must be finite `0 < stop < entry_reference < target`
  (R07, F-026 boundary); §6.4 states the narrow `unknown → staged` recovery exception.

### F-042 · P3 · Corporate-action contract did not match the split core (Claude review of #61, fixed in #62)
- **Where:** `phase2-4-interfaces.md` §14 `CorporateAction`, §9.4, §5.
- **Issue:** §14 used a float `ratio` and `fetched_at`, while `semiedge.data.splits` (#61) needs exact
  integer share counts and a separate `known_at`. A float like 0.333… can't round-trip to an exact
  1:3, and using `fetched_at` as `known_at` misstates point-in-time knowledge. The branch name was also wrong.
- **Fix (spec):** `CorporateAction` carries `new_shares`, `old_shares` and `known_at`, with the ratio
  derived exactly as a `Fraction`. Adjustment rows store share counts. A verified split requires
  `known_at ≤ now`. §14 imports `semiedge.data.splits` and documents the two meanings of
  "split-adjusted".

### F-043 · P2 · Split verification needed a read missing from `StateReader` (Codex re-review, PR #62)
- **Fix (spec):** `data_source()` is part of `StateReader`, implemented identically by both stores and
  used by §9.4 verification. Parity acceptance covers matching and mismatching source classes.

### F-044 · P2 · Available but incomplete fill activities left confirmed fills unaccounted (Codex re-review, PR #62)
- **Example:** provisional 30, broker cumulative 50, activities `[A(30)]` → watermark 30, then a
  reconciliation halt. Likewise an empty activities response with `B > 0`.
- **Fix (spec):** ingestion step 2 always inserts a provisional remainder when `B > W`, whether
  activities were available or not, priced at the broker's order-level average. Order notional is
  defined while provisional rows are active. Acceptance covers partial, empty and catch-up
  responses, plus replay.

### F-045 · P3 · Acceptance example used the removed float split API (Codex re-review, PR #62)
- **Fix (spec):** the example uses `new_shares`/`old_shares`; a 1:3 case was added.

### F-046 · P3 · Ambiguous F-044 acceptance sequence (Codex re-review, PR #62)
- **Fix (spec):** one monotonic sequence on a fresh DB (empty at B=40 → [A(30)] at B=50 →
  [A(30), C(20)] at B=50), with replay and provisional-notional assertions at every stage.

### F-061 · P2 · R02 did not validate position entry prices (Codex review, PR #69)
- **Reproduce:** a `PositionView("ZZZ", 1, nan, 100.0)` passed R02, so the proposal could be approved.
- **Fix:** R02 requires `avg_entry_price` (as well as `mark_price`) to be finite and > 0.
- **Regression:** `test_f061_position_entry_price_must_be_finite_positive` (nan, ±inf, 0, negative).

### F-062 · P2 · `max_positions=3.5` via `model_construct` passed construction (Codex review, PR #69)
- **Reproduce:** with three positions, R09 evaluated `3 < 3.5` and approved a fourth.
- **Fix:** the constructor requires `max_positions` to be a positive builtin int.
- **Regression:** `test_f062_max_positions_must_be_a_positive_int` (3.5, 4.0, True, 0, -1).

### F-063 · P3 · Risk-manager tests missed pending orders and batch exhaustion (Codex review, PR #69)
- **Fix:** tests for batch approvals exhausting gross exposure and cash with exact sizes, equal-rank
  symbol ties, a rejected earlier duplicate, and a property test with pending BUY/SELL orders.

### F-096 · P3 · Backtest entry could be stopped out better than its open (Claude review of #75; fixed in #80)
- **Fix:** ADR-008 also invalidates the entry when `open_d ≤ stop`. Regression: `test_f096_open_below_stop_invalidates_even_if_slipped_fill_is_above`.

### F-130 · P2 · ADR-008 broke the stated backtest/paper parity (Codex review, PR #80)
- **Fix (spec):** ADR-008 is a backtest-only conservative adjustment. §6.3 documents the parity
  exception (paper has no trustworthy opening price at fill time), and the §10 parity test excludes
  exactly that case. §19 item 4 and ADR-008 are updated.
- **F-131 (P2, Codex re-review):** the §18 checklist still claimed unconditional parity; it now carries the same exception.

### F-120 · P2 · Recovery `unknown → staged` wrote a supplied broker id and trusted stale lookups (Codex review, PR #78)
- **Reproduce (9060783):** recovery with `broker_order_id="known-broker-order"` succeeded and
  stored that id. 404 lookup timestamps from before the submission attempt were accepted, and
  so were lookups with no spacing evidence.
- **Fix:** recovery refuses any `broker_order_id`, so the recovered row never has one. Every
  lookup must be strictly after the order's last submission attempt (`submitted_at`). The
  caller must supply `detail.monitor_cycle_seconds` (finite > 0), and consecutive lookups must
  be at least that far apart; without it, recovery is refused.
- **Regression:** `tests/safety/test_state_review_fixes.py::test_f120_*`.

### F-121 · P2 · Weighted fill average could overflow to inf and be committed (Codex review, PR #78)
- **Reproduce (9060783):** a 2-share activity at 1.5e308 gave a `fsum` of inf, so inf was
  committed as `filled_avg_price` and `get_order` could no longer decode the row.
- **Fix:** averages are computed exactly with `Fraction`. Every derived persisted price
  (fill average, split-adjusted avg/stop/target/split_ratio) goes through `_derived_float`,
  which requires finite > 0; otherwise it raises `StateStoreError` and the transaction rolls
  back. `_apply_transition` re-checks the average before it writes.
- **Regression:** `test_f121_weighted_average_never_overflows`,
  `test_f121_non_finite_derived_split_price_rolls_back` (asserts an unchanged DB snapshot).

### F-122 · P2 · Unknown target status raised without a RECONCILIATION halt (Codex review, PR #78)
- **Reproduce (9060783):** `transition_order(cid, "done", ...)` raised `StateStoreError`, but
  no halt row was recorded.
- **Fix:** an unknown status is now an `illegal_transition` through the halting error path.
  The halt is written after a rollback, and also when an outer transaction swallows the error.
- **Regression:** `test_f122_*`.

### F-123 · P2 · `ingest_order_fills` updated `orders` outside `transition_order` (Codex review, PR #78)
- **Reproduce (9060783):** `store.py` had two `UPDATE orders` sites, and `transition_order`
  could not record a `filled_qty` increase. Both contradict §5.1 ("no other UPDATE path";
  "filled_qty may only increase").
- **Fix:** `_apply_transition` is now the only `UPDATE orders` path. `transition_order` uses it
  after validating the edge, and it accepts any `filled_qty` ≥ the stored value that equals the
  ingested-fill watermark; a decrease halts. Ingestion records fills through the same path with
  the status unchanged (`event="fills_ingested"`, from/to = current status). The spec is
  unchanged: the implementation now conforms to it.
- **Regression:** `test_f123_orders_rows_have_exactly_one_update_path` (AST) and
  `test_f123_ingestion_records_filled_qty_through_the_transition_path`.

### F-140 · P2 · Recovery spacing could round to zero (Codex re-review, PR #78)
- **Reproduce:** `monitor_cycle_seconds=1e-10` became `timedelta(0)`, so two identical post-submission lookups allowed `unknown → staged`.
- **Fix:** the cycle must be finite and ≥ 1 s, and lookups must be strictly increasing and at least one cycle apart.
- **Regression:** two new cases in `test_f120_recovery_needs_post_submission_spaced_evidence`.

---

## Speculative concerns (not demonstrated; tracked for the relevant phase)

- **S-001 Survivorship/selection bias.** `config/universe.yaml` is today's hand-picked list. Any
  backtest on it overstates performance; reports must label this (Phase 2, #15–#17).
- **S-002 Bull-market dependence.** `momentum_v1` requires `SMH > SMA50`; results from 2016–2026
  are dominated by a historic semiconductor bull run. Walk-forward and sub-period analysis must
  include 2018 Q4, 2020 Q1 and 2022 drawdowns (#17).
- **S-003 Feed mixing.** Volume conditions are not comparable across IEX and SIP; the data layer
  must reject mixed feeds (spec requires it; verify when #9/#10 land).
- **S-004 Pending-order exposure.** `max_gross_exposure_pct` says "incl. pending orders"; the
  risk manager (#19) must count open buy orders at their limit/notional, not only filled positions.

### F-009 · P2 · SDK coercion bypasses strict account blocking-flag validation (Codex, PR #39)
- **Where:** PR #39 head `ad383a7232317819d49f973ad5321ff23885efe7`, `src/semiedge/broker/account.py`, `fetch_account_snapshot` / `_flag`.
- **Reproduce:** using `tests/integration/broker/conftest.py`'s real paper-factory client and fake HTTP transport, parameterize `account_payload[field] = value` for each of `trading_blocked`, `account_blocked`, `trade_suspended_by_user` and values `0`, `"false"`; return the normal clock payload and empty positions. `fetch_account_snapshot(client)` accepts all six, reports the flag as `False` and `is_tradable=True`. Temporary probe command: `.venv/Scripts/python -m pytest -q tests/integration/broker/test_review_probe.py` (6 passed confirming acceptance).
- **Impact:** malformed blocking states are silently normalized by the SDK before strict `_flag` validation, violating the fail-closed contract. No order or live-endpoint bypass demonstrated.
- **Fix requested:** validate original payload types before SDK coercion via a reviewed adapter/strict response model; add the six fake-transport integration regressions expecting `BrokerDataError`, preserving paper-only and read-only guards.
- **Review:** FAIL posted at https://github.com/Ferrsir/semi-edge/pull/39#issuecomment-6533090862. Full existing suite: 406 passed, 1 opt-in smoke skipped; ruff check/format and mypy passed. Offline fixtures only.
- **Fix (implemented, Claude, branch `feature/13-paper-account`):** `fetch_account_snapshot` no longer uses the SDK model methods. It sends the same three `GET` requests through the paper-factory client's own `get("/account" | "/clock" | "/positions")` (no new client, no new endpoint) and validates the decoded JSON with exact types before anything can coerce it: block flags, `is_open` and (when present) `pattern_day_trader` must be JSON booleans (`0`, `1`, `"false"`, `"true"`, `null`, missing → `BrokerDataError`); text must be exact `str`; timestamps must be RFC 3339 strings with an explicit offset (an epoch number or offset-less string, which the SDK would accept, is rejected); responses must be an exact `dict`/`list`. `ReadOnlyTradingClient` now declares only `get(path: Literal["/account", "/clock", "/positions"])`; the safety suite's strict client answers only `get` with one allowlisted path, and the AST scan permits only `client.get("<allowlisted literal>")`, so the read-only surface is unchanged in effect. Paper endpoint guards are untouched and still run before any request.
- **Test evidence:** `tests/integration/broker/test_account_sdk.py::test_sdk_coercible_blocking_flags_fail_closed` holds the six requested regressions (each flag × `0`, `"false"`, real paper-factory `TradingClient` + `FakeAlpacaTransport`), plus missing/`null`/other malformed flags, SDK-coercible clock/`pattern_day_trader`/position fields and non-object bodies; with the previous `account.py` restored, 38 of the 42 cases selected by `-k "coercible or malformed_or_missing"` fail, including all six F-009 cases (accepted as tradable). Full suite `.venv/Scripts/python -m pytest -q`: 539 passed, 1 opt-in paper smoke skipped; `ruff check .`, `ruff format --check .`, `mypy` clean. Offline only, fake credentials.


### F-010 ? P2 ? Shared paper endpoint guard trusted URL display instead of type (Codex, #40)
- **Where:** `src/semiedge/safety/broker.py::assert_paper_client`.
- **Reproduce:** a `str` subclass containing a non-paper URL can return the paper URL from `__str__` or a `.value` property. The old guard accepts it while URL concatenation retains the underlying non-paper destination. Regressions: `pytest -q tests/safety/test_broker_factory.py -k disguised`. This was independently reproduced against the pre-fix source, without making a request.
- **Impact:** future callers relying only on the shared guard could accept an incorrectly configured client. The existing factory still constructs paper=True; no live request or order was demonstrated. PR #39 already has a local compensating check.
- **Fix:** accept only identity-equal SDK `BaseURL.TRADING_PAPER` or an exact builtin `str` matching the paper endpoint (existing trailing-slash support retained). Never coerce arbitrary `.value` or display representations. Error remains sanitized.
- **Tests:** seven regression/compatibility cases cover two disguises, a custom value wrapper, accepted SDK/string representations, and factory rejection of a disguised constructed client. Full suite: 219 passed; Ruff check/format and mypy passed. Offline fixtures only; no risk ceiling or trading permission changes.


## PR #45 review fixes ? F-013, F-014, F-015

### F-013 ? P2 ? Future/in-progress sessions accepted as completed
- **Where:** `data.csv_import.parse_csv_bars` and `data.fixture_ingest.ingest_fixture`.
- **Confirmed reproduction:** before the fix, a 2031-03-03 row, a 2099-12-31 cutoff on a historical row, and a Saturday cutoff all passed. Reproduced offline against PR head 5df112e using its exact source; no network or vendor data.
- **Impact:** fixture provenance could call future or unfinished sessions completed, violating the no-future-bars contract.
- **Fix:** require an injected aware `now` at all library entry points; the CLI passes the real UTC clock. Require cutoff and every input row to be an XNYS session closed plus a validated settlement delay (30 minutes by default). Reject future/unsettled rows even if after the historical cutoff. Record the actual last session and missing-session dates separately from the cutoff.
- **Regression evidence:** `tests/unit/test_csv_completion.py` covers regular close boundaries, settlement equality, July 3 early close, DST, offset clocks, future rows/cutoffs, weekends, holidays, malformed clocks/delays and gap recording. The integration CLI test confirms a future cutoff writes no artifact.

### F-014 ? P2 ? Fixture ancestor could permit vendor-cache pollution
- **Where:** `data.fixture_ingest.ingest_fixture` output validation.
- **Confirmed reproduction:** the old ancestor-name guard accepted a path under `fixtures/data/raw/bars/daily/feed=iex/adjustment=raw/`.
- **Impact:** malformed fixture output in the vendor layout can break real-cache reads/status/fetch, though the store rejects its provenance rather than reading it as market data.
- **Fix:** require the immediate resolved parent to be named fixture/fixtures, and reject the vendor raw/bars/daily sequence and feed=/adjustment= components anywhere in the destination.
- **Regression evidence:** five vendor-layout and nested-parent cases in `tests/integration/test_fixture_ingest.py`; existing exclusive creation/symlink tests remain.

### F-015 ? P3 ? Input-domain and coverage gaps
- **Where:** CSV volume parsing, supported session range and fixture metadata.
- **Fix:** validate share volume using exact decimal integrality/sign and a float64 exact-integer ceiling; reject fractional, negative-zero and oversized values. Explicitly limit the importer to sessions from 1990 onward. Preserve gaps without filling and list missing sessions through the cutoff; mark adjustment unknown. No claim of vendor point-in-time availability or complete event coverage.
- **Regression evidence:** exact-volume, pre-history and missing-session tests. Additional library UTF-8 error normalization and interrupted-write cleanup are covered by integration tests.
- **Remaining limitation:** output publication is exclusive but not atomic for concurrent readers; consumers must wait for command success. No vendor connection, broker request, credential access, risk-limit or strategy change occurred.
## Dashboard review fixes ? PR #49 (F-016..F-023)

- **F-016 / P2, refresh drift:** Unknown board IDs and statuses previously vanished or became READY. The parser now requires both source sections, validates every board/review row and rejects unknown/duplicate identifiers, statuses or missing roadmap metadata. GitHub result limits fail explicitly rather than truncating. Missing gh on PATH fails explicitly. Previous snapshots remain unchanged on errors; successful writes replace atomically. Python regression fixtures cover each failure class and successful source preservation.
- **F-017 / P2, stale claims:** Hero phase, phase progress, owner-blocker count and callout now derive from tasks. Review-card notes come from the parsed Review queue, not PR-number-specific literals. Snapshot regenerated from current board and GitHub. Research rows are explicitly labeled roadmap placeholders and use real issue open/closed state. DOM regressions change source states and confirm the UI changes with them.
- **F-018 / P3, URLs:** Only known internal hash routes and HTTPS URLs under the exact repository origin/path are clickable. Malformed/javascript URLs render inert text; DOM regression verifies no unsafe anchor.
- **F-019 / P3, PR association:** PR numbers use word boundaries, preventing #4 from matching #45. DOM regression covers both links in one snapshot.
- **F-020 / P3, ignored source:** Root gitignore explicitly includes project-status/dist. Repository hygiene scans web source extensions and ignores installed dependencies. A required dashboard-tests CI job runs npm ci --ignore-scripts and npm test.
- **F-021 / P3, typing:** Refresh helpers are typed and explicitly included in strict mypy scope.
- **F-022 / P3, browser storage:** Null/array/scalar/invalid local checklist values become an empty checklist; only known boolean fields survive. DOM regressions cover all malformed shapes.
- **F-023 / P3, local paths:** Documentation uses relative checkout paths; publishing still uses the existing private Site identity. No personal machine paths are required to refresh.
- **Scope/evidence:** Local crafted snapshots, stubbed GitHub responses and DOM tests only; no trading/credentials. Source timestamps disclose snapshot freshness. Browser layout remains unverified; this dashboard is a project tracker, not the operational trading UI.

### Dashboard follow-up F-028 / F-029 (P3)
- Restored the hero separator, roadmap arrow and completion checkmark with Unicode escapes to prevent shell encoding loss. DOM regression rejects question marks in the hero.
- Setup cards now describe instructions and guides, rather than asserting an unselected earnings source or unresolved credential status. Local checkmarks remain personal reminders, never integration evidence. DOM regression verifies the stale provider claim is absent.
- Rebased on current main, preserving both ingestion and dashboard audit sections. Hosted publishing remains blocked by Sites Git client availability; no updated deployment is claimed.


## F-036 / P2 / Unverified retained adjusted sessions (#63)
- **Where:** data.store._check_adjustment_basis. Independently found by the Codex data-boundary audit and reproduced by the coordinator on merged main.
- **Reproduction:** stored Jan2..5 OHLC100/volume100. A reverse1:2 split Jan3 followed by forward2:1 Jan4 restates only Jan3 to OHLC50/volume200. A new refetch missing Jan3 has matching Jan2/Jan4 anchors; the old guard accepted [100,100,100,100] instead of [100,50,100,100]. Fetch timestamps arbitrate correctly; this is distinct from F-024.
- **Impact:** a retained historical row can remain on the wrong basis, distorting indicators and P&L, despite matching neighboring closes. The price-anchor proof in the old contract was insufficient for multiple opposite events.
- **Fix:** until verified corporate-action/basis evidence exists, every retained adjusted session must occur in incoming history. Missing retained rows require a full stored-range refetch; existing overlapping close comparisons remain. Raw append semantics, source checks and atomic publication are unchanged. This intentionally rejects formerly accepted partial adjusted appends/backfills and gapped refetches, even when surrounding prices match.
- **Evidence:** new reverse/forward gap test failed before the fix (DID NOT RAISE); now verifies rejection leaves exact original Parquet bytes and frame unchanged, followed by successful full-history recovery. Existing adjustment-basis and timestamp tests retained, with legacy mixed fetch timestamps seeded explicitly; generic correction tests supply full coverage. No strategy/risk ceiling or trading permissions changed. Claude cross-provider review pending.

## Offline split normalization implementation (#46)
- This is a new isolated research utility, not a newly discovered production bug. Contract: `docs/specifications/split-normalization.md`; implementation: `data.splits.normalize_raw_splits`.
- Only explicitly RAW bars are accepted; the output is tagged split-adjusted and rejected on a second invocation. Events require symbol, effective session, exact positive integer share counts and aware knowledge timestamps. Duplicate/conflicting effective sessions fail explicitly.
- Applied events must be both effective by as_of and known by the caller's cutoff. Future rows are excluded; the as_of session must have closed at that cutoff. Pre-effective prices/VWAP divide and volume multiplies by new/old shares; trade counts remain unchanged. Invalid inputs and numeric overflow/underflow raise; inputs remain unchanged.
- Historical evidence at initial a225f7c implementation: 35 deterministic unit cases covered forward/reverse/fractional/multiple splits, conservation, effective boundaries, historical prefixes, information cutoffs, timezone boundaries, malformed events/frames and arithmetic failures. Full offline suite: 806 passed, one opt-in paper smoke skipped; Ruff check/format, mypy and Bandit passed.
- Limitations: no vendor event ingestion or ledger-coverage claim; no positions/orders/store/scanner integration; knowledge timestamps must be supplied from genuine historical availability evidence. This utility does not establish strategy performance or readiness for execution. Claude cross-provider review required.

### PR #61 cross-provider review fixes (F-031..F-035)
- Claude Code independently reviewed a225f7c using read-only source tools, verdict FAIL. The coordinator posted the report summary on PR #61. Reviewer did not execute tests; the following reproductions were subsequently executed by Codex.
- **F-031 / P2:** events generator exhausted by validation. Materialize the iterable once. Regression observed old applied=() with raw close100 tagged split; fixed result includes the event and close50.
- **F-032 / P2:** supplied already-effective but not-yet-known event left a mixed raw basis labelled adjusted. Fail explicitly with SplitDataError; never use future event information to adjust historical values. Revised fixture has an actual 2:1 price jump and raises on the unresolved event. Future-effective events remain excluded.
- **F-033 / P3:** require explicit RAW enum and raw frame provenance tag, reject missing attrs. Document that stripped/relabelled provenance cannot be inferred from prices, and ledger coverage is still unverified.
- **F-034 / P3:** validate numerical values and session membership on the historical prefix. Future NaNs/non-sessions no longer change earlier results. Full-frame structural schema validation remains required and documented.
- **F-035 / P3:** added applied-event prefix/effective-date equality, nested nonempty attrs isolation, early-close equality, maximum accepted share count and unsupported pre-1990 ledger cases.
- Old-source regression evidence: four selected tests all FAIL when PYTHONPATH points to the unchanged a225f7c review checkout; they all PASS on the new implementation. Focused suite: 39 passed. No network, credentials, broker requests or risk/strategy changes.

## #68 follow-ups (F-041, F-050, F-051)
- **F-041 / P3:** Split normalization composes each row's later-event ratios exactly with Fraction, then applies the final factor once. A net factor one leaves floats unchanged; float64 representation/rounding and explicit numeric overflow/underflow refusal remain. Exact-equality inverse-chain regression fails on the old source and passes now. Cumulative factors outside float64 representation raise rather than clamp. Focused split suite:43 passed.
- **F-050 / P3:** docs/ADJUSTED_CACHE_RECOVERY.md and the Phase1 interface contract describe start/end coverage constraints, permanently omitted vendor sessions, preserved original provenance and deliberate rebuild in a new empty cache root. Fetch success is not quality/actionability proof; no automatic deletion, unofficial replacement or invented bar is permitted. No cache was deleted or network contacted to write this guidance.
- **F-051 / P3:** Gap/partial-append tests match the specific "unverified for retained sessions" refusal rather than the ambiguous "full stored range" text shared by older anchor failures.
- **Additional review notes:** events=None now raises explicit SplitDataError; initial test totals above are marked historical. The two new inverse-chain/None regressions both failed against the unchanged pre-fix source, and now pass. No new strategy, risk ceiling, broker or credential changes. Claude cross-provider review required.

## F-060 / P2 / Nested JSON log credentials escaped pattern redaction (#29)
- **Where:** logconfig._PATTERNS / _RedactingFormatter; reproduced by the independent security boundary harness.
- **Reproduction:** configure_logging with registered fixture secrets, then log extra={"payload":{"authorization":"unregistered-fixture-token"}}. Existing post-format patterns did not recognize quoted JSON credential keys, so the token remained in the rendered JSON line. The new assertion failed on the old implementation; all inputs are hand-written fakes.
- **Impact:** credential-labelled broker/HTTP metadata not already registered in settings could appear in nested log extras. Registered settings secrets were already masked; this closes the defense-in-depth gap rather than claiming they had leaked.
- **Fix:** mask quoted JSON credential fields including escaped value characters and plaintext bearer authorization headers, preserving the JSON envelope. Regression tests verify nested APCA/API/token/authorization fields and valid JSON after redaction.
- **Additional #29 primitives:** security.redaction builds the configured redactor (including NFC display representations), deep-copies/redacts strict JSON values/keys, rejects cycles/nonfinite/unsupported/colliding/oversized structures without echoing inputs; security.untrusted normalizes and bounds exact strings, strips controls/bidi and escapes display/mentions. No command/SQL/path/order use is permitted.
- **Evidence/scope:** deterministic logs + temporary parameterized SQLite boundary harness, malicious text fixtures, large/control strings, unchanged settings/input assertions, AST evaluation-call guard with one SafeLoader exception. This is not a claim of engine/outbox/dashboard integration: those modules do not yet exist; #29 remains open for consumer integration and the full end-to-end scenario. No broker or credential access, governance change or risk/strategy modification. Claude cross-provider review required.
