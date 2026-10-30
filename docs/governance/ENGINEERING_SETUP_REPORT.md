# Engineering setup report

## Implemented, not deployed
PR #33 contains canonical governance and operating documentation, CI/build/security/integration/safety workflows, review runner and fail-closed evaluator, audit workflow, templates and ownership declarations. A CLI integration test verifies rejection of live configuration and acceptance of safe defaults. Scanner tests do not establish runtime broker safety.

Two independent local reviews of f1b70014c97548e884207f29d8633d0f0cc2456c returned FAIL. Findings drove fixes for superseded approvals, unresolved required changes, malformed/duplicate artifacts, dirty reviewer checkouts and unsafe Claude configuration fallback. These historical reviews do not approve later revisions.

## Verified external blockers
- Repository exists and is private: https://github.com/Ferrsir/semi-edge.
- Both rulesets and main protection API calls return HTTP 403 requiring a GitHub plan upgrade or public visibility. No visibility or plan change was made.
- Repository secret listing is empty. Do not add model credentials to ordinary feature-accessible workflows.
- T3 history, delegation capability and PR-link tools were not callable. No persistent issue-to-PR orchestrator is configured.
- The proposed shared Actions review status is spoofable by another same-repository workflow. It is not an enforceable independent review integration.

## Safe activation state
Privileged review orchestration is manually dispatchable only and has an unconditional failing activation job; all review/publisher jobs are blocked by the context condition. No automatic merge code is enabled. This is deliberately incomplete activation, not a functioning autonomous team.

## Owner/integration actions required
1. Resolve private repository protection entitlement without exposing repository contents.
2. Provision separately administered, least-privileged review and CI identities with credentials outside feature-controlled workflows. Ensure agent and human identities differ.
3. Supply model authentication to isolated review runtimes through secure secret provisioning. Claude API key mode is required by this runner.
4. Configure required protected checks and no bypass, force pushes or deletion. Validate the trusted integration against spoofing, stale reports and altered workflow definitions.
5. Configure an available persistent orchestrator explicitly, or continue bounded foreground tasks with durable state. Feedback comments alone are not automatic repair execution.
6. Run a protected failing test PR and confirm rejected merge; then run the successful independent-review path. Record exact SHAs, run links and merge rejection evidence. Only afterward activate auto-merge. No deployment is included.

See BRANCH_PROTECTION.md and REVIEW_PROTOCOL.md. Issue #1 remains open; the draft must not merge while these conditions remain unmet.
