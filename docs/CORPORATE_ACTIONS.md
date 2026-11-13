# Corporate-action ledger and validation core (#18)

This module provides typed immutable event terms, exact split counts, an atomic per-symbol
Parquet ledger, a read-only Alpaca market-data adapter and explicit consistency results.
It does not place/cancel orders, adjust held positions or automatically modify cached bars.

The adapter uses only `https://data.alpaca.markets/v1/corporate-actions` through the SDK's raw
GET method, with US region, all data-quality records and bounded pagination. Unknown/incomplete
records, unsupported categories, fractional/unrepresentable share counts, duplicate IDs,
repeated tokens and transport errors raise `MarketDataError`; no partial result is returned.
Start/end bound the provider's **process_date**, not ex-date. Do not describe a query window as
complete ex-date coverage. Some non-split record types lacking an actual effective date are
deliberately unsupported rather than assigned a guessed date.

Alpaca documents delays and no guarantee of action creation/availability time:
[Corporate actions API](https://docs.alpaca.markets/us/reference/corporateactions-1).
The installed SDK models have process/ex dates but no verified announcement timestamp.
Therefore adapter construction requires a caller-supplied `knowledge(event_id)` resolver backed
by verified announcement/publication evidence, plus an explicit clock. The code validates the
returned timestamp, not the truth of that evidence. It never substitutes `fetched_at`, midnight
on a process date, or a guessed historical availability time. No such resolver/source is
configured by this PR, and no real API integration or complete event coverage is demonstrated.

`CorporateActionStore` writes schema-versioned JSON payloads inside Parquet so integer share
counts never pass through nullable float storage. An existing event's terms are immutable;
only retrieval time may advance. Conflicting facts or sources require review. Publication is
atomic per symbol, not a multi-symbol transaction. Missing ledgers raise; an empty response/write
does not certify that no corporate actions exist. Originals are preserved on rejected updates
and interrupted publication. Paths reject traversal and escaping/symlink files.

`to_split_event()` uses the already reviewed `data.splits` types with the same integer counts
and knowledge time. Vendor split-adjusted bars and point-in-time normalized raw fixtures remain
separate. Callers still need normal bar quality, freshness and required-history gates.

Consistency policy proposed for the §14 clarification on issue18:

- First flag every supplied split whose ex-date is after the stored fetch date as requiring a
  full refetch, even beyond the stored prefix. This preserves the existing §14 conservative
  rule, including announced future splits; there is no evaluation-date argument to distinguish
  them from splits already effective since the fetch. Other events are evaluated through the
  stored last session. This is cache validation, not proof of event coverage.
- A split-ratio discontinuity in **already SPLIT/ALL-adjusted** history is inconsistent even
  when a matching event explains the raw jump. Explaining a 100→25 move is not evidence that
  those observations share a basis. Require a complete authoritative refetch; never repair
  prices silently. A correctly normalized/refetched 25→25 history passes.
  The split-day log return is compared to zero and the raw split basis change: if it is at
  least as close to the raw change, flag it even below the generic jump threshold. This
  recognizes fractional splits with a nonzero market return; it is not a proof that every
  possible mixture can be identified from prices. Split ex-dates must be exchange sessions.
- Unexplained moves beyond the fixed 35% default are flagged. A known cash-dividend drop can
  explain a price-return (`SPLIT`) series, but cannot explain an already dividend-adjusted
  (`ALL`) series. Same-day cash dividends are summed. Cash matching is best-effort and uses
  fixed 1e-6 relative tolerance; market returns may still cause conservative rejection.
- Missing/invalid input produces reasons/exceptions, not a default consistent result. No
  data-quality threshold or strategy is tuned against research performance.

Tests use hand-written events, fake transport and temporary files, including an indicator
breakout and a same-basis protective-level harness. They do not demonstrate the full future
backtest/engine lifecycle. Issue18 remains open for verified source provisioning, consumer
integration, coverage policy and an end-to-end backtest acceptance scenario after T5 is ready.
