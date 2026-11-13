# Offline split normalization core (#46)

This pure research utility accepts one symbol's explicitly RAW daily bars and a caller-supplied
event ledger. It does not fetch events, certify ledger coverage, adjust positions or orders, or
connect to the scanner/backtest automatically. Missing events cannot be inferred from prices.

`SplitEvent` records symbol, effective exchange session, positive integer new/old share counts
(each at most 2**53), and an aware `known_at` timestamp. A ratio of one is invalid. Duplicate
effective sessions are rejected rather than guessing whether events duplicate or compose.

`normalize_raw_splits` requires symbol, adjustment=RAW, `as_of` exchange session, aware information
cutoff `known_at`, and an exchange calendar. The as-of session must have closed at that cutoff.
Only events known by the cutoff AND effective by as_of apply. A known future effective event
never adjusts earlier research. Event input order has no effect. Bars after as_of are excluded.

For each applied event, rows strictly before the effective session have OHLC and optional VWAP
divided by new/old shares, and volume multiplied by that ratio. Effective-session rows are
already on the new basis. Trade counts do not change. Multiple events compose in effective-date
order. Reject nonfinite/invalid values, numerical overflow/underflow, unknown columns, malformed
sessions, mismatched symbols, and already-adjusted inputs; never clamp, fill or silently drop bad
data. Input frames and ledgers remain unchanged. The output carries split-adjustment attributes
and an immutable list of applied events, making accidental repeated normalization rejectable.

The information cutoff describes what this invocation may know; callers must use the actual
historical decision time. A current vendor-adjusted download cannot establish historical event
knowledge. Tests use only hand-written fixtures. No profitability or integration claim follows.
