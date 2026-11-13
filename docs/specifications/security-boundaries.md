# Security boundary primitives (#29)

This additive implementation follows section13 of the Phase2–4 draft. It uses only existing
settings/logging; no missing engine, state store, notifier or trading dashboard is invented.

`build_redactor(settings)` registers only supplied secret values. `redact_json` recursively copies
strict JSON primitives, redacts strings and keys, and masks credential-labelled values even if
unregistered. It rejects cycles, unsupported objects, nonfinite floats, non-string/colliding keys,
depth above32 and more than100000 nodes. Errors never include the rejected value. It neither
silently truncates data nor calls arbitrary object string representations.

`UntrustedText` accepts exactly broker/discord/news/ai/file sources and exact strings. Both its
constructor and factory normalize NFC, strip control/format/surrogate characters except newline,
and cap text at2000 codepoints. `max_len` may tighten the cap (1..2000). Display escapes HTML and
Markdown and neutralizes mentions; the escaped representation can be longer than the raw cap.
Consumers must apply their transport limit after escaping. It is still untrusted data, never a
command, SQL, path, setting, order parameter or privileged prompt instruction.

Tests exercise existing logging, JSON serialization and a temporary SQLite fixture as an
independent boundary harness. They do not establish end-to-end engine/outbox/dashboard coverage.
Future state/notification consumers must call these boundaries before persistence/display; issue29
remains open for that integration and the full section13 end-to-end acceptance scenario.
