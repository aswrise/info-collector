# GitHub Issue Triage To Ready-For-Agent Brief

## Loop

Turn incoming GitHub issues into agent-ready implementation briefs.

## Trigger

Schedule: every 2 hours.

## Inputs

- Open GitHub issues in this repo labelled `needs-triage`.
- Canonical triage labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`.
- Domain docs: `CONTEXT.md` and `docs/adr/`.

## Output

A GitHub issue labelled `ready-for-agent` whose body or latest comment contains a brief an implementer agent can act on without asking another question.

## Brief

The ready-for-agent brief has exactly these sections:

1. Goal: the user-visible outcome the implementation must produce.
2. Scope: what is in scope, what is explicitly out of scope, and the repo areas likely involved.
3. Acceptance Criteria: observable pass/fail bullets.
4. E2E Verification: the end-to-end path, required tool, command or UI path, and smallest real API smoke test when relevant.
5. Final Update Requirements: test evidence to report, screenshots to include, and links to changed issues or artifacts.

## Comment Templates

Ready-for-agent comment:

```md
## Goal

...

## Scope

...

## Acceptance Criteria

- ...

## E2E Verification

- Tool: ...
- Path: ...
- Real API smoke: ...

## Final Update Requirements

- Record tests run and results.
- Include a final screenshot for visual or browser-facing changes.
```

Needs-info comment:

```md
I need one detail before this can become ready for an agent:

...
```

## Ready-For-Agent Criteria

An issue qualifies only when the brief makes the end-to-end check clear:

- The expected E2E path is named.
- Frontend/browser-facing work must be verified with the relevant frontend tool: Computer Use for native UI, Browser Use for browser flows.
- Browser flows must be verified end to end, not only by unit tests.
- Real API integration must be smoke-tested when the issue touches API behavior, using the smallest live call that proves the path.
- Test evidence must be recorded in the final update.
- The final update must include a screenshot when the result is visual or browser-facing.

## Checkpoint

Ask the user only when one of these is true:

- The goal is unclear.
- Acceptance criteria cannot be inferred from the issue and repo docs.
- The work would spend obvious real money or a large amount of real API tokens.

Otherwise, push right: prepare the brief, apply the correct label, and leave the user a compact triage comment.

## Run Steps

For each open issue labelled `needs-triage`:

1. Read the issue body, comments, labels, `CONTEXT.md`, and relevant ADRs.
2. Decide whether the issue can become `ready-for-agent` using the criteria above.
3. If yes, comment with the five-section brief, remove `needs-triage`, and add `ready-for-agent`.
4. If no, comment with the smallest question that would unblock the brief, remove `needs-triage`, and add `needs-info`.

## Run Report

Notify the user only when the run changes at least one issue. The report contains:

- Count moved to `ready-for-agent`.
- Count moved to `needs-info`.
- Links to changed issues.

If no issue changes, stay quiet.

## Current Decisions

- External PRs are not part of this triage loop.
- GitHub Issues are the only request surface.
- The loop runs every 2 hours.
- Each run scans only open issues labelled `needs-triage`.
- The brief has five sections: Goal, Scope, Acceptance Criteria, E2E Verification, Final Update Requirements.
- `ready-for-agent` requires clear E2E verification instructions, including tool choice, real API smoke coverage where relevant, recorded test evidence, and final screenshots for visual/browser-facing work.
- The workflow asks the user only for unclear goals, uninferable acceptance criteria, or expensive real API usage.
- Triage writes one comment per issue, then moves the label from `needs-triage` to either `ready-for-agent` or `needs-info`.
- Scheduled runs report only when they change issues.
