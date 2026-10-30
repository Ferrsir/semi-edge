# Review protocol

The runner and pure gate evaluator are implementation prototypes. They are not a deployed security boundary. Reports contain role, provider, model, PR, repository, base/head SHA, findings and evidence. Missing, stale, malformed or failed reviews must block merging.

The proposed Actions review workflow is disabled pending an independently controlled integration. Ordinary GitHub Actions status tokens are spoofable by other same-repository workflows. Do not store model or review-publisher credentials in feature-accessible workflows. A dedicated integration must attest execution identity, immutable reviewed content, repository/PR/base/head and separate roles, and publish required checks from its own protected identity. Protect CI/security/safety definitions as well.

Reviewer execution must isolate untrusted repository configuration, hooks, MCP servers, network egress and credentials. Tool allowlists alone are not an OS sandbox. Validate installed CLI flags before activation. Review artifacts and local agent reports are evidence, not authorization to merge.

Recheck on code pushes, base updates and review submission/dismissal. Invalidate old success before reruns. Resolve findings in at most three cycles; escalation is durable, never reset by a feature-controlled comment.
