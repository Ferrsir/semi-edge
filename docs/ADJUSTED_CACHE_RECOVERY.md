# Recovering an adjusted bar cache

Adjusted history can change when a vendor restates corporate actions. The store refuses an
update if a retained stored session is absent from the incoming history, even if nearby prices
match. This protects indicators and research from mixed adjustment bases.

For an existing adjusted series, fetch coverage must include every stored session that would
otherwise survive. `data fetch --start D` after its first stored session, moving
`data.history_start` later, or ending before its last session can trigger refusal. Use the
actual stored range from `semiedge data status`, not just the desired incremental date window.
Raw-series append behavior is different and does not solve an adjusted-series refusal.

A full-range refetch can still fail if the vendor permanently drops a previously stored session.
Repeating that fetch indefinitely cannot reconstruct the missing observation. Recovery is a
deliberate rebuild, not permission to retain or fabricate a value:

1. Stop cache-writing commands and any consumers using that cache while planning a switch.
   Record the symbol, feed, adjustment, session range and error. Preserve/archive the original
   series and its provenance before any manual filesystem change; agents do not delete it.
2. Choose a new, empty cache directory through `SEMIEDGE_DATA_DIR`. Use the same configured
   feed/adjustment and fetch authoritative history covering the required warm-up and research
   window. Rebuild every symbol/benchmark needed by the intended scan. Creating a new cache
   avoids deleting the original and avoids silently retaining absent old rows.
3. Run `semiedge data status` and the intended `semiedge scan --source cache --as-of DATE` against
   the new root. Inspect validation/rejection reasons and gaps. A successful fetch/write alone
   is not proof of complete or actionable history; configured quality checks still apply.
   Never fill a missing bar from an old adjustment basis, unofficial feed or invented prices.
4. Switch consumers only after the new history and configured quality checks meet the intended
   use. Keep the old cache as an audit artifact. If necessary history remains unavailable,
   leave the affected scan/research unavailable and resolve the data-source issue.

This procedure performs no trading, grants no permissions, and changes no risk or strategy
setting. It is guidance for a manual recovery; no automatic delete/rebuild command is added.
