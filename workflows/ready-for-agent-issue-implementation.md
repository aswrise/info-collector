# Ready-For-Agent Issue Implementation

## Loop

Implement GitHub issues that have already been triaged into agent-ready briefs.

## Trigger

Schedule: every 2 hours. Each run scans for unassigned open issues labelled `ready-for-agent`.

## Inputs

- Unassigned open GitHub issues in this repo labelled `ready-for-agent`.
- The issue's latest ready-for-agent brief.
- Domain docs: `CONTEXT.md` and `docs/adr/`.
- Agent instructions: `AGENTS.md`.

## Output

A ready pull request with green tests, independent review completed, recorded verification evidence, and a final issue update.

## Claiming

The scheduled runner claims one issue at a time:

- Select the oldest unassigned open issue labelled `ready-for-agent`.
- Assign the issue to `@me`.
- Comment that automated implementation has started.
- Skip assigned issues.

## Worktree And Agent

For each claimed issue:

- Create a fresh git worktree outside the repo root, under `/Users/aqua/D/Code/info-collector-worktrees/`.
- Branch name: `codex/issue-<number>-<short-slug>`.
- Start a Codex implementation run in that worktree.
- Model: `5.5 high`.
- The prompt must begin with `/goal`.
- The goal must tell Codex to keep working until the issue is implemented, reviewed, tested, and a PR is opened.

Prompt shape:

```text
/goal Implement GitHub issue #<number> in /Users/aqua/D/Code/info-collector-worktrees/<worktree>.

Read AGENTS.md, CONTEXT.md, relevant ADRs, and the ready-for-agent brief.
Use the smallest implementation that satisfies the brief.
Run required tests and E2E verification.
Run /code-review against main after implementation. If review finds issues, fix them and rerun /code-review until it passes.
Open a pull request when tests are green and review passes.
```

## Run Steps

For each selected `ready-for-agent` issue:

1. Read the issue brief, comments, `AGENTS.md`, `CONTEXT.md`, and relevant ADRs.
2. Claim the issue by assigning it to `@me`.
3. Create a fresh worktree and branch.
4. Start the Codex implementation run with a `/goal` prompt using model `5.5 high`.
5. Implement the smallest change that satisfies the brief.
6. Run the brief's required checks and the repo test suite.
7. Verify browser-facing work with Browser Use and native UI work with Computer Use.
8. Run the smallest real API smoke test when required by the brief.
9. Capture final screenshots for visual or browser-facing changes.
10. Start an independent review subagent with `/code-review` against `main`.
11. If review finds issues, fix them and repeat review until it passes.
12. Ensure all tests are green after the final review fix.
13. Commit, push, and open a ready PR.
14. Comment on the issue with the PR link, test evidence, review result, screenshots, and any remaining risk.
15. Remove `ready-for-agent` and add `ready-for-human`.

## Review Loop

- Use `/code-review` as the installed Matt Pocock review skill. If `/codex-review` exists as an alias in the execution environment, that alias may be used, but the required behavior is the two-axis `/code-review` against `main`.
- The review must run in an independent subagent after the implementation is complete.
- Any actionable review finding must be fixed.
- After fixes, rerun tests and rerun review.
- The loop ends only when review has no actionable findings and tests are green.

## Token And API Budget

- Real API verification is a smoke test only: the smallest live call that proves the path.
- Do not run broad live replays, load tests, or repeated API calls after the path is proven.
- Prefer mocked or local tests for coverage beyond the smoke path.

## Pull Request

- PR title starts with `Fix #<number>:` or `Implement #<number>:` followed by the issue title.
- PR body links the issue and includes:
  - Summary of changes.
  - Tests and E2E verification evidence.
  - Review result.
  - Screenshots for visual or browser-facing changes.
- The PR is ready for review, not draft.

## Checkpoint

Do not ask the user during implementation unless blocked by user-only credentials, missing paid API access, or an external service outage that prevents required verification.

If blocked, comment on the issue with the blocker, remove `ready-for-agent`, add `needs-info`, and unassign the issue.

## Run Report

Notify the user only when a PR is opened or an issue becomes blocked. The report contains:

- Issue link.
- PR link, when opened.
- Test status.
- Review status.
- Screenshot links or paths when relevant.

## Current Decisions

- Implementation starts from issues labelled `ready-for-agent`.
- The loop runs every 2 hours and automatically implements the oldest unassigned `ready-for-agent` issue.
- Each issue gets a fresh worktree under `/Users/aqua/D/Code/info-collector-worktrees/`.
- Implementation runs as a Codex `/goal` using model `5.5 high`.
- The implementer must follow the E2E verification and final update requirements from the triage brief.
- Real API checks must be the smallest live call that proves the path.
- Implementation must run independent `/code-review` review against `main`; fix and rerun until review passes.
- All tests must be green before opening the PR.
- Real API use is limited to the smallest smoke test that proves the path.
- Final output is a ready PR for the user to review.
