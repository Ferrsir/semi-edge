<!-- Keep PRs small and focused. One issue per PR where possible. -->

Closes #<!-- issue number (required) -->

Implemented-by: <!-- agent + model, e.g. claude-code / claude-opus-5-5, or codex / gpt-x, or human -->

## Summary
<!-- What changed and why. -->

## Risk classification
- [ ] Touches restricted paths (`.github/**`, `AGENTS.md`, `CLAUDE.md`, `docs/governance/**`, `scripts/governance/**`, `config/risk.yaml`, `src/semiedge/safety/**`) → human approval of the head commit required
- [ ] Touches trading logic (signals, sizing, risk, execution, backtest) → Review B must cover it
- [ ] Changes configuration defaults or risk limits
- [ ] Database / persisted-data schema change (migration described below)
- [ ] None of the above

## Strategy changes (only if a strategy changed)
- Hypothesis:
- Backtest / out-of-sample / cost-sensitivity evidence (link artifacts; no unsupported claims):
- Baseline comparison:

## Tests
<!-- Exact commands run locally and their results (pass/fail counts). New behaviour must have tests. -->
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run mypy`
- [ ] `uv run pytest`
- [ ] Safety tests added/updated where invariants are affected (`-m safety`)

## Documentation
- [ ] Docs updated (ARCHITECTURE / specs / RISK_POLICY / TESTING / CHANGELOG as relevant)
- [ ] Docs distinguish planned vs implemented vs tested

## Review gate
Both independent AI reviews (A: software engineering, B: quant/security/financial safety) must
PASS for the **latest** commit; findings are posted by the `ai-review` workflow. Do not mark
review findings resolved without a code change or evidence.
