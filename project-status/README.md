# SEMI-EDGE project control

Owner-private project status Site. This is a management dashboard, separate from trading dashboard issue #25. It contains no market data, account credentials or order controls.

## Update data

The app displays a timestamped snapshot of AGENTS.md and GitHub metadata, not an automatic live integration. Refresh using a locally authenticated GitHub CLI:

```powershell
python scripts/refresh-snapshot.py --repo C:/Users/ferri/.t3/projects/semi-edge
npm.cmd test
```

Then use the Sites source/package/publish workflow for the existing project ID in `.openai/hosting.json`. Preserve private access. Refreshing the web page alone does not fetch new metadata. No access token is exported. Local setup checkmarks are browser-only reminders and never modify GitHub.

Sources: current coordination board, GitHub issue/PR snapshot and the owner-provided Nov 12 to-do list. The supplied Claude artifact URL was inaccessible during authoring. Board status supplies ownership and workflow detail; GitHub supplies issue state, PR heads and check summaries. Scope-sized board items are not a project-readiness percentage.

## Development

Buildless static files in `dist/`; no runtime dependencies or external fonts/assets. `npm.cmd ci` installs jsdom for DOM interaction tests. `npm.cmd test` checks rendering, filters, dialogs, private checklist storage and source-navigation behavior. Browser layout QA was not available in the authoring session.
