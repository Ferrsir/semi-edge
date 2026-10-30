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


### F-008 ? P2 ? Missing app configuration sections silently selected defaults (Codex, #37)
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
