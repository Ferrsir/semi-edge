# Engineering setup checkpoint

Status: incomplete; automatic merging MUST remain disabled.

## Verified on 2026-10-29
- Private repository: https://github.com/Ferrsir/semi-edge.
- Both rulesets and main branch protection API reads return HTTP 403 with: Upgrade to GitHub Pro or make this repository public to enable this feature.
- Existing governance implementation: 65 tests pass; Ruff passes for scripts and governance tests; mypy passes for governance scripts.
- No end-to-end review or protected merge demonstration has completed.
- T3 history, orchestration capability, and PR-link tools are unavailable in this session.

## Remaining work
1. Owner must enable a GitHub plan supporting protection for this private repository. Do not change repository visibility automatically.
2. Independently review this implementation, including model sandboxing, prompt injection, exact-revision validation, and trusted status provenance. Binding checks to the shared GitHub Actions app alone does not isolate trusted workflows from other workflows using that app.
3. Configure a trusted review integration with credentials unavailable to feature-controlled code. Confirm provider availability and pinned dependencies.
4. Finish canonical policy and operational documentation. Define the issue-to-PR worker and review-feedback handoff; do not describe comments as a functioning persistent orchestrator.
5. Configure branch protection and required trusted CI/security/safety/review gates under the initial setup authorization, with no bypass.
6. Run a failing test PR and verify merge rejection, then demonstrate two independent exact-head approvals and a protected merge. Only then enable ordinary auto-merge.

The draft contains Claude's interrupted implementation, checkpointed for review; passing unit tests are not evidence that GitHub enforces it. No deployment or live trading is authorized by this checkpoint.
