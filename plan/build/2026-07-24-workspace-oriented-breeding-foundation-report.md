# Builder Increment: Workspace and Inbox Foundation

Status: implemented and self-tested  
Cycle: `workspace-oriented-breeding-design`  
Authorization: `human-approval-2026-07-24-phase-2-builder-execution`

## Delivered

- Project-scoped GreenAgent Phase-2 Builder execution record.
- Additive SQL models and Alembic migrations for workspaces, active
  memberships, breeding projects, and conversation scope.
- Workspace-owner creation, member-filtered project access, duplicate-slug
  conflict handling, and Gateway APIs.
- PostgreSQL-authoritative project language with SQLite retained for local
  single-user development.
- Projectless and legacy conversation defaults:
  `scope_type=inbox`, `visibility=private-owner`, nullable `workspace_id` and
  `project_id`.
- Workspace-first navigation and default route.
- Global Inbox UI that reuses existing chat routes.
- Project portfolio, project creation, maize-v1 reference profile, DBTL phase,
  reconciliation state, and project operational overview UI.
- README plus root, backend, and frontend architecture guidance.

## Phase status

| Plan phase | Status | Evidence |
|---|---|---|
| B0 Contracts and baseline | complete for foundation increment | Failing-first tests, migration parity, documentation |
| B1 Workspace/project persistence and RBAC | foundation complete | Membership-filtered repository and API |
| B2 Inbox and conversation scopes | foundation complete | Thread scope migration and private Inbox defaults |
| B3 Workspace-first frontend shell | foundation complete | Projects, Inbox, project overview routes |
| B4 DBTL persistence/reconciliation/review | pending | Later increment |
| B5 Team/agents/automation/activity | pending | Later increment |
| B6 Breeding core and maize-v1 | pending | Later increment |
| B7 Working/validated knowledge | pending | Later increment |
| B8 Migration hardening/release | pending | Later increment |

## Self-check evidence

- Backend workspace/router tests: 6 passed.
- Backend repository, thread, router, and migration regression set: 142 passed
  after the scope migration; the targeted parity rerun passed 3/3.
- Frontend suite: 88 files, 757 tests passed.
- Frontend TypeScript: passed.
- Frontend ESLint: passed.
- Focused backend Ruff checks: passed.
- Running-stack route probe: Projects returned the expected anonymous auth
  redirect; `/api/workspaces` returned the expected 401 auth boundary.
- GreenAgent DBTL validation and doctor: passed at increment close.

## Residual risks

- Workspace invitations, membership-management APIs, and project-specific
  capability policy are not implemented yet.
- Project conversation creation currently opens the stable chat composer; the
  validated move/attach operation is still pending.
- Durable DBTL, review events, reconciliation workers, breeding data, and
  knowledge promotion remain later approved phases.
- PostgreSQL concurrency/failure-injection testing remains a release gate.

This is Builder self-test evidence, not independent Tester validation.
