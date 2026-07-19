# Notes

- Repo: Info Collector, a macOS Chrome extension plus local scripts for turning saved articles into external processing flows.
- Primary user-facing loop today: Chrome Bookmark Source `收藏文章` -> Article Queue -> `translate` External Processing Flow -> Markdown output.
- Repo work channels: GitHub Issues via `gh`; PRs are not a triage surface.
- Agent-facing docs: `AGENTS.md` is canonical; `CLAUDE.md` imports it.
- Canonical domain language lives in `CONTEXT.md`.
- Existing automation surface: Dashboard actions, native host, `~/.info-collector/` spool files, launchd translate flow.
- Workflow specs live in `workflows/*.md`.
- Current workflow spec: `workflows/github-issue-triage-ready-for-agent.md`.
- Next workflow candidate after triage: implement `ready-for-agent` issues end to end.
- Periodically ask the user whether there are other recurring loops worth specifying.
