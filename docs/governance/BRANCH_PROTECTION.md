# Protection activation runbook

Current GitHub API response for this private repository is HTTP 403: upgrade to GitHub Pro or make the repository public. The owner must resolve the plan restriction; agents must not change visibility or purchase a plan.

After entitlement is available, configure protected main: PR required; strict up-to-date checks; no bypass, force push or deletion; protected governance ownership. Required contexts: ci-required, security-required, integration-required, trading-safety-required, and two independently issued review checks. Bind to a separately controlled integration, not only the shared GitHub Actions app. Keep protection credentials outside agent access.

Before activation create deliberately failing and spoofing test PRs. Verify GitHub rejects merging with missing reviews, stale reviews, failed CI, missing safety tests, altered workflow definitions and unresolved restricted changes. Never attempt an actual merge test while protection is absent. Demonstrate the successful two-review path afterward and preserve API/check URLs and SHAs in the audit issue. No such demonstration has completed yet.
