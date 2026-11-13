# Project status dashboard

Private Site: https://semi-edge-project-status.ferrislavigne.chatgpt.site

Issue #47. This project-management dashboard is separate from the operational trading UI (#25).

It shows the current phase, roadmap, board items by status and owner, dependency waits, open pull requests and owner setup items. Search and phase/owner/status filters work locally. Task details link to GitHub. Personal setup checkmarks stay in browser storage; they do not change the coordination board or confirm an integration works.

## Source and refresh

Reviewed source is under `project-status/`. The canonical hosted source has its own private Sites repository and is also checked out locally at `C:/Users/ferri/.t3/projects/semi-edge-status-site`. Preserve the Site ID in `.openai/hosting.json`.

The dashboard uses an explicit snapshot from AGENTS.md and GitHub; it is not automatically synchronized. Reloading the page does not refresh project metadata. Its checked-at timestamp and stale-snapshot notice make this visible. No GitHub token or other credential is shipped to the browser.

To refresh the versioned source from a current project checkout:

```powershell
cd project-status
python scripts/refresh-snapshot.py --repo ..
npm.cmd ci
npm.cmd test
```

Then copy reviewed changes to the separate canonical Site checkout, synchronize its private source repository using the Sites workflow, package and publish the existing owner-private Site. Do not change the SEMI-EDGE repository's origin to the Site remote. Publishing a new snapshot is a separate action from merging source changes.

The supplied Claude artifact link could not be fetched. Content instead uses the owner's previously supplied Nov 12 task list, the current local coordination board and fetched GitHub metadata. Phase counts are board items of varying size, not a claim of operational readiness.

DOM interaction tests cover navigation, search/filtering, task dialogs, review counts, inert rendering of untrusted strings, browser checklist persistence and snapshot-help text. No visual browser layout verification was available in this session.
