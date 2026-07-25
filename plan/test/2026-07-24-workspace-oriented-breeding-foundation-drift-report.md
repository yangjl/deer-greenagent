# Tester Verdict: Workspace-Oriented Breeding Foundation

Verdict: **FAIL — evidence-blocked and not independently accepted**  
Automated regression gate: **PASS**  
Complete approved program: **NOT COMPLETE (B4–B8 pending)**

## Outcome

The executable foundation checks are green:

- Backend foundation, thread, router, bootstrap, and migration set:
  `148 passed`, with one dependency deprecation warning.
- Frontend suite: `88` files and `757` tests passed.
- Frontend ESLint and TypeScript: passed.
- Focused backend Ruff: passed.
- Anonymous `/api/workspaces`: `401`.
- Anonymous `/workspace/projects`: `307` to `/login`.
- GreenAgent cycle validation and doctor: passed.

That is not enough for an independent GreenAgent `PASS`. The same Codex agent
performed the Builder and Tester roles in one continuous context, so the role
switch does not provide independent agent/person separation. The Builder
handoff also omitted decisive authenticated, PostgreSQL, browser,
accessibility, and orientation-study fixtures. Existing Builder regressions
were therefore counted as supporting evidence only.

## Known-answer results

| Check | Result | Evidence |
|---|---|---|
| K01 protocol | PASS | Handoff exists; test plan accepted by transition guard; validate/doctor pass |
| K02 anonymous API | PASS | `401` |
| K03 anonymous UI | PASS | `307` to `/login` |
| K04 workspace isolation | BLOCKED | Regression supports it; no independent authenticated cross-user fixture |
| K05 duplicate project identity | BLOCKED | Regression supports it; no independent authenticated API fixture |
| K06 legacy Inbox defaults | BLOCKED | Regression supports it; no independent backfill/idempotency fixture |
| K07 migration parity | PASS—supporting | Migration regressions pass; PostgreSQL fixture absent |
| K08 backend compatibility | PASS | `148 passed` |
| K09 frontend contract | PASS | `757 passed` |
| K10 workspace-first navigation | BLOCKED | Auth boundary passes; authenticated UI/a11y/orientation evidence absent |
| K11 static quality | PASS | Frontend check and focused Ruff pass |
| K12 scope honesty | PASS | B4–B8 explicitly pending |

## Drift findings

| ID | Severity | Category | Finding | Route |
|---|---|---|---|---|
| TDRIFT-001 | high | implementation-fix | Builder handoff lacks required independent fixtures, RBAC matrix, PostgreSQL evidence, browser artifacts, and orientation fixtures | Builder |
| TDRIFT-002 | high | design-revision | Work-item acceptance still forbids implementation changes despite later explicit implementation approval | Designer + human |
| TDRIFT-003 | medium | implementation-fix | No new B0 cross-component contract package or standalone database-authority ADR appears in the artifact inventory | Builder |
| TDRIFT-004 | high | implementation-fix | No independent authenticated cross-user, PostgreSQL concurrency, or reconciliation failure-injection evidence | Builder |
| TDRIFT-005 | medium | implementation-fix | No workspace/project Playwright, screenshot, mobile, keyboard, accessibility, or H0/H1 orientation artifact | Builder |

## Deliverable alignment

| Declared deliverable | Result | Verdict |
|---|---|---|
| Workspace/project schema, repository, and APIs | Regression evidence present; independent authenticated fixture absent | DRIFT |
| Global Inbox and private legacy defaults | Regression evidence present; share/move/backfill fixture absent | DRIFT |
| Workspace-first portfolio and Inbox | Routes exist behind auth; authenticated browser evidence absent | DRIFT |
| Versioned B0 cross-component contracts and ADR | Not present in the handoff inventory | MISSING |
| PostgreSQL production/concurrency evidence | Explicitly deferred in Builder report | MISSING |
| Full B4–B8 breeding/DBTL/team/knowledge system | Explicitly pending and outside this increment | NOT TESTED |

## Human review

This report does not decide the required correction. The available actions are:

1. Return the increment to Builder for the missing evidence package and
   implementation gaps, then test with an independent Tester context.
2. Human/Designer formally narrow and repair the work-item acceptance contract,
   accepting that this is only a foundation demonstration rather than B0–B3
   phase completion.

Tester does not hand this result to Learner as validated knowledge while the
evidence review remains unresolved.
