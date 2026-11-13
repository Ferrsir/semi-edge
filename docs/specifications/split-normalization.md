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
never adjusts earlier research. If the supplied ledger includes an already-effective event
unknown by the cutoff, raise rather than label an unresolved mixed-basis series adjusted. This
refusal is a data-availability result, not permission to use the future event in a decision.
Event iterables are materialized once; input order has no effect. Bars after as_of are excluded.

For each applied event, rows strictly before the effective session have OHLC and optional VWAP
divided by new/old shares, and volume multiplied by that ratio. Effective-session rows are
already on the new basis. Trade counts do not change. Multiple events compose in effective-date
order. Reject nonfinite/invalid values, numerical overflow/underflow, unknown columns, malformed
sessions, mismatched symbols, and already-adjusted inputs; never clamp or fill bad data. Frame
structure (index, column names and dtypes) is validated before slicing; numerical values and
session membership are validated only on the historical prefix, so future price corruption
does not affect that prefix. Sessions/events before 1990 are unsupported and fail explicitly.
Input frames and ledgers remain unchanged. Both adjustment=RAW and frame.attrs["adjustment"]
="raw" are required; missing provenance is rejected. Callers must set that tag from verified
source provenance, not guessed prices. Output carries split-adjustment attributes and an
immutable list of applied events. A second invocation is rejected while provenance is retained;
no utility can detect adjusted data deliberately relabelled RAW after its metadata was stripped.

The information cutoff describes what this invocation may know; callers must use the actual
historical decision time. A current vendor-adjusted download cannot establish historical event
knowledge. Tests use only hand-written fixtures. No profitability or integration claim follows.
