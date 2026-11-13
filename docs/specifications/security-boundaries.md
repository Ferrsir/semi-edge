# Security boundary primitives (#29)

This additive implementation follows section13 of the Phase2–4 contract. It uses only existing
settings/logging; no missing engine, state store, notifier or trading dashboard is invented.

`build_redactor(settings)` registers only supplied secret values. `redact_json` recursively copies
strict JSON primitives, redacts strings and keys, and masks credential-labelled values even if
unregistered. Logging and persisted JSON share a normalized credential-key rule (key, secret,
token, password, authorization or webhook anywhere in the alphanumeric key). It masks the whole
credential-labelled value before visiting or serializing it, regardless of its type. Other JSON
values reject cycles, unsupported objects, nonfinite floats, non-string/colliding keys,
depth above32 and more than100000 nodes. Errors never include the rejected value. It neither
silently truncates data nor calls arbitrary object string representations.

`UntrustedText` accepts exactly broker/discord/news/ai/file sources and exact strings. Both its
constructor and factory first limit normalization input to8000 codepoints, normalize NFC, strip
control/format/surrogate/private-use/line-separator/paragraph-separator characters except newline,
and cap text at2000 codepoints. `max_len` may tighten the cap (1..2000). Credential-bearing
consumers must pass `redactor=build_redactor(settings)` to `from_raw`: raw text is redacted before
cleaning or truncation, which might otherwise alter or partly expose a registered credential.
Direct construction and factory calls without a redactor only make inert display text; they
cannot guarantee secret masking. Never wrap first and redact the truncated result afterward.

`for_html()` uses HTML text escaping only. `for_discord()` escapes Markdown and neutralizes
mentions without HTML entities; the compatibility `for_display()` alias means Discord/Markdown.
The escaped representation can be longer than the raw cap.
Consumers must apply their transport limit after escaping. It is still untrusted data, never a
command, SQL, path, setting, order parameter or privileged prompt instruction.

Tests exercise existing logging, JSON serialization and a temporary SQLite fixture as an
independent boundary harness. They do not establish end-to-end engine/outbox/dashboard coverage.
The future Discord notifier must also send `allowed_mentions={"parse": []}`; escaping alone is
not the transport-level mention control. Logging structurally redacts extras, messages and
exception text before encoding JSON exactly once. Unsupported logging objects are safely
stringified/redacted; strict persisted JSON does not permit that adaptation.

Future state/notification consumers must call these boundaries before persistence/display; issue29
remains open for that integration and the full section13 end-to-end acceptance scenario.
