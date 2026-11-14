# Specification: cross-checked earnings calendar (#20)

Status: **Specified (issue #20, ADR-009).** It implements the `EarningsCalendar` protocol of
`phase2-4-interfaces.md` §2.1 for PAPER and SCANNER use. It does not change R16, the blackout
length (`config/risk.yaml: earnings_blackout_sessions`), `require_verified_earnings_data: true`,
or the backtest waiver (§3.6). This calendar is **not point-in-time**: it holds today's view
of upcoming dates. Backtests keep `earnings_policy: waive_unavailable` and their label.

Owner decision (2026-11-13): Finnhub's earnings calendar is the primary source, Alpha Vantage's
`EARNINGS_CALENDAR` is the cross-check, and a hand-maintained override file records dates checked
on investor-relations pages. No Yahoo or other unofficial feed is used.

## 1. Modules and layers

| Module | Layer | Role | Must not import |
|---|---|---|---|
| `semiedge.data.earnings` | L1 | Pure types, strict parsers, override loader, resolution rule (§4) | network modules, `semiedge.data.earnings_sources`, `semiedge.risk` |
| `semiedge.data.earnings_sources` | L1 | HTTPS fetchers, JSON cache, refresh policy (§3) | `semiedge.risk`, `alpaca` |
| `semiedge.risk.earnings` | L2 | `CrossCheckedEarningsCalendar`: resolution → `EarningsInfo` (pure) | everything denied to `semiedge.risk`, plus `semiedge.data.earnings_sources` |
| `semiedge.config.models.EarningsSettings` | L0 | Strict settings from `config/earnings.yaml` | — |

The rows for `semiedge.data.earnings` and `semiedge.risk` → `semiedge.data.earnings_sources` are
appended to the §0.2 import table (`tests/safety/test_import_boundaries.py`).

## 2. Secrets

- Keys come only from environment variables `FINNHUB_API_KEY` and `ALPHAVANTAGE_API_KEY`
  (or the git-ignored `.env`), read by `AppSettings` as `SecretStr`, and are included in
  `AppSettings.secret_values()` so log redaction masks them.
- The Finnhub key is sent in the `X-Finnhub-Token` header, never in a URL. Alpha Vantage accepts
  the key only as the `apikey` query parameter, so the request URL is a secret. It is built inside
  the fetcher and never logged, cached, raised or returned.
- Fetch errors are reduced to fixed codes (`http_401`, `rate_limited`, `transport_URLError`,
  `parse_error`, `api_message`, `missing_api_key`). Exception messages and response bodies are
  never stored or logged.
- No test uses a real key or the network. Tests inject the HTTP function and use saved fixture
  responses under `tests/fixtures/earnings/`.

## 3. Fetching, cache and refresh policy

### 3.1 Requests
- Finnhub: one request per tradable universe symbol,
  `GET https://finnhub.io/api/v1/calendar/earnings?from=D&to=D+lookahead_days&symbol=S`,
  at least `finnhub_min_interval_seconds` apart (free tier: 60 calls/min). Every response must be
  a JSON object whose `earningsCalendar` is a list of objects. Each object has a `symbol` equal to
  the requested symbol and an ISO `date` inside the requested window. Anything else is a
  `parse_error`. A transport or HTTP failure for any one symbol fails the whole Finnhub snapshot
  (fail closed). An answer for one symbol that does not parse (Finnhub returns `2330.TW` for TSM)
  leaves only that symbol uncovered, so it resolves as `finnhub_symbol_not_covered` and R16 blocks
  it alone; if no symbol parses, the snapshot fails with `parse_error` (owner decision
  2026-11-14, #114).
- Alpha Vantage: one bulk request per refresh,
  `GET https://www.alphavantage.co/query?function=EARNINGS_CALENDAR&horizon=3month&apikey=…`
  (free tier: 25 requests/day). The response must be CSV with at least the columns `symbol` and
  `reportDate`. A JSON body (rate-limit or error notice) is `api_message`. Coverage is
  `[D, D + 89 days]` for every symbol, because a 3-month horizon always covers 89 days.
- HTTPS only, fixed hosts, timeout `request_timeout_seconds`. HTTP status 429 is `rate_limited`,
  and any other non-200 status is `http_<code>`.

### 3.2 Snapshot cache
`<SEMIEDGE_DATA_DIR>/earnings/<source>.json` (git-ignored with `/data/`), written atomically
(temp file + `os.replace`), schema version 1. It holds the source, `fetched_at` (UTC), the coverage
window, the symbols covered (`null` = every symbol), events `(symbol, report_date, hour)`, the
status (`ok`/`error`) with an error code, and the attempt log `{date, count, last_attempt_at}`.
A cache file that is unreadable or invalid, or has the wrong schema version, counts as missing.

### 3.3 Refresh policy (free tiers)
`refresh(symbols, force=False)` decides per source:
1. Missing key → record `missing_api_key`; no request.
2. A snapshot with `ok` status, a `fetched_at` UTC date equal to today and the same symbol set
   → no request ("at most once a day").
3. Attempts today ≥ `max_attempts_per_day`, or the last attempt is less than
   `retry_interval_minutes` ago → no request (`force` skips rules 2–3, never rule 1).
4. Otherwise fetch. Success replaces the snapshot. Failure keeps the previous events but sets
   status `error` with its code, so the source is unusable until a later success.

## 4. Resolution rule (per symbol, decided at query time)

`resolve(symbol, on_or_after, now)` returns `EarningsResolution(symbol, next_date, verified,
source, reason)`. Reasons are stable snake_case codes.

1. **Override.** If `config/earnings_overrides.yaml` has an entry for the symbol with
   `verified_on <= today`, `today - verified_on <= override_max_age_days`, and
   `next_earnings_date >= on_or_after`, the result is verified with that date, source
   `manual_override@<verified_on>`. An expired entry is ignored (reason suffix
   `override_expired`) and the rule continues. An override file that exists but is invalid
   (unknown or missing field, duplicate symbol, bad date, `verified_on` in the future, symbol
   outside the universe) raises `EarningsDataError` at load time: fail closed.
2. **Source health.** For each of Finnhub and Alpha Vantage, the source is unusable if: no
   snapshot (`<src>_missing`), status error (`<src>_error_<code>`),
   `now - fetched_at > max_age_hours` (`<src>_stale`), `fetched_at > now + 5 min`
   (`<src>_clock_skew`), `on_or_after < coverage_start` or
   `coverage_end < on_or_after + min_forward_coverage_days` (`<src>_coverage`), or the symbol
   was not requested (`<src>_symbol_not_covered`). Any unusable source → `verified=False`.
3. **Cross-check.** For each source, take the earliest event date `>= on_or_after` for the
   symbol (or `None`). Equal values → verified, source `finnhub+alphavantage`, and `None` means no
   report inside coverage. Different values → `verified=False`, reason
   `sources_disagree` with both dates in the detail.

R16 then applies unchanged. `verified=False` gives `earnings_data_unavailable` and rejects the
entry. A verified date inside `earnings_blackout_sessions` gives `earnings_blackout`. Disagreement
blocks only the affected symbol. A missing, stale or failed source blocks every symbol.
`next_earnings_session` holds the reported calendar date, which R16's session count handles
whether or not that date is a session.

`CrossCheckedEarningsCalendar` logs one WARNING per `(symbol, reason)` per instance when a
resolution is unverified. The record carries the symbol, reason and source and never a key or URL.

## 5. Configuration: `config/earnings.yaml` (all fields required, unknown fields rejected)

| Field | Type / bounds | Meaning |
|---|---|---|
| `lookahead_days` | int, 14..89 | Finnhub query window |
| `min_forward_coverage_days` | int, 7..`lookahead_days − 1` | coverage needed after the query date |
| `max_age_hours` | number, (0, 48] | a snapshot older than this is stale |
| `max_attempts_per_day` | int, 1..5 | per source |
| `retry_interval_minutes` | int, 15..1440 | between failed attempts |
| `request_timeout_seconds` | number, (0, 60] | per request |
| `finnhub_min_interval_seconds` | number, [1, 60] | spacing between Finnhub requests |
| `override_file` | relative file name inside the config dir | manual overrides |
| `override_max_age_days` | int, 1..90 | how long a hand check stays valid |

Override file schema (`config/earnings_overrides.yaml`): `overrides:` is a list (possibly empty) of
`{symbol, next_earnings_date, verified_on, source_url (https), note (optional)}`. A missing file
means "no overrides". The shipped file is empty.

## 6. CLI
- `semiedge earnings refresh [--force]`: applies §3.3 for the tradable universe and prints
  each source's status. Exit 0 when both sources are usable afterwards, otherwise `EXIT_DATA`.
- `semiedge earnings status [--as-of YYYY-MM-DD]`: prints each source's health and per-symbol
  resolution (date, verified, source, reason) from the cache only, with no network access. Exit 0
  when every tradable symbol is verified, otherwise `EXIT_DATA`.

## 7. Acceptance tests (offline, saved fixtures)
- Parsers: valid Finnhub JSON and Alpha Vantage CSV fixtures parse. A wrong symbol, a date outside
  the window, a non-list `earningsCalendar`, a missing column, a JSON notice or an empty body is
  each rejected with its code.
- Secrets: the Alpha Vantage key never appears in the snapshot, the cache file, log records or the
  error code, even when the HTTP function raises an exception whose text contains the URL. The
  Finnhub key is sent only in the header.
- Refresh: a second refresh on the same UTC day makes no request. Failed attempts honour the
  retry interval and daily cap. A missing key makes no request. A failure keeps the error status.
- Resolution: agree → verified. Disagree, missing, error, stale, clock skew, short coverage or an
  uncovered symbol → unverified with the reason. A fresh override wins. An expired or past-dated
  override is ignored. An invalid override file raises.
- Risk integration (`-m safety`): R16 rejects with `earnings_data_unavailable` when the sources
  disagree or one is stale, rejects with `earnings_blackout` when a verified date is within the
  blackout, and passes for verified dates outside it.
