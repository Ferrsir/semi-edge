# SEMI-EDGE project control

Owner-private project status Site. This is a management dashboard, separate from trading dashboard issue #25. It contains no market data, account credentials or order controls.

## Update data

The app displays a timestamped snapshot of BOARD.md and GitHub metadata, not an automatic live integration. Refresh using a locally authenticated GitHub CLI:

```powershell
python scripts/refresh-snapshot.py --repo ..
npm.cmd test
```

Then use the Sites source/package/publish workflow for the existing project ID in `.openai/hosting.json`. Preserve private access. Refreshing the web page alone does not fetch new metadata. No access token is exported. Local setup checkmarks are browser-only reminders and never modify GitHub.

Sources: current coordination board, GitHub issue/PR snapshot and the owner-provided Nov 12 to-do list. The supplied Claude artifact URL was inaccessible during authoring. Board status supplies ownership and workflow detail; GitHub supplies issue state, PR heads and check summaries. Scope-sized board items are not a project-readiness percentage.

## Development

Buildless static files in `dist/`; no runtime dependencies or external fonts/assets. `npm.cmd ci --ignore-scripts` installs jsdom for DOM interaction tests. `npm.cmd test` checks rendering, filters, dialogs, private checklist storage and source-navigation behavior. Browser layout QA was not available in the authoring session.

Refresh fails without replacing the prior snapshot when the board has an unknown task ID/status, malformed or duplicate rows, missing metadata, missing GitHub CLI, or a result reaches the 1,000-item safety limit. Add explicit phase mappings or pagination before retrying. Review handoffs come from the board review queue; phase and blocker counts are derived from snapshot tasks. Research rows absent from the board are labeled roadmap placeholders, with open/closed state from GitHub.

Python refresh validation runs in pytest and strict mypy. Dashboard DOM regression tests run in the required CI dashboard-tests job using locked npm dependencies with install scripts disabled. No browser layout claim is made.
