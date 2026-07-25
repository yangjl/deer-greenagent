# Builder Plan: Workspace-Oriented AI Plant Breeding System

Status: approved by human  
Role: Builder  
Cycle: `workspace-oriented-breeding-design`  
Design: `workspace-oriented-breeding-design-package`  
Authorization: `human-builder-go-2026-07-24`

The schema-valid source for this plan is
`plan/build/2026-07-24-workspace-oriented-breeding-phase-action-plan.json`.

## Outcome

Deliver a PostgreSQL-authoritative, workspace/project-first DeerFlow in which:

- humans and workspace agents can collaborate in a projectless global Inbox;
- breeding projects own durable DBTL cycles, work, evidence, team, data, and
  knowledge;
- `.greenagent` is a temporary database-derived evaluator/projection and never
  the final state authority;
- file/database mismatch visibly blocks DBTL progression and is repaired only
  from PostgreSQL;
- the breeding core stays crop-neutral while a versioned `maize-v1` profile
  proves the extension mechanism;
- Learn output enters provisional Project Working Memory and requires a human
  promotion into Validated Project Knowledge;
- current chat streaming, history, scheduling, channels, and run invariants
  remain compatible.

## Builder proposals carried forward

These proposals resolve Designer-deferred details without changing the approved
direction:

1. Workspace is the collaboration/security boundary. Deployment administrators
   do not receive implicit project-data access.
2. Legacy unassigned threads enter Inbox as `private-owner`; sharing and project
   moves are explicit and audited.
3. Agents are propose-only by default. Direct record writes require assignment
   capability; approvals, promotions, membership, policies, and reconciliation
   imports are human-only.
4. Existing GreenAgent design, evidence, and knowledge gates remain. Membership,
   promotion, policy, reconciliation-import, and methodological schema-profile
   changes also require humans.
5. Projection refresh retries three times with bounded backoff, then blocks and
   alerts. Only project owners or reconciliation operators rebuild from
   PostgreSQL.
6. `maize-v1` uses generic breeding tables plus versioned extensions for
   heterotic group, maturity group, inbred/hybrid classification, and maize
   trait metadata.

## Dependency order

```text
B0 Contracts and baseline
  └─ B1 Workspace/project persistence and RBAC
      ├─ B2 Inbox and conversation scopes
      │   └─ B3 Workspace-first frontend shell
      └─ B4 DBTL persistence, reconciliation, and review
          └─ B5 Team, agents, automation, and activity
              └─ B6 Generic breeding core and maize-v1
                  └─ B7 Working Memory and Validated Knowledge
                      └─ B8 Migration, hardening, and release readiness
```

## Phase summary

| Phase | Primary result | Mutation boundary |
|---|---|---|
| B0 | Cross-component contracts, ADR, failing-first baselines | Contracts, tests, docs only |
| B1 | Workspaces, projects, membership, RBAC, PostgreSQL preflight | Additive schema and APIs |
| B2 | Global Inbox, Scratchpads, project conversations, safe legacy backfill | Nullable thread scope |
| B3 | Project portfolio, Inbox, nested project shell, reused chat | Feature-flagged frontend |
| B4 | Database-authoritative DBTL, Review Inbox, reconciliation | Additive DBTL schema and adapter |
| B5 | Human/agent teams, work, schedules, activity, reporting | Capability-scoped mutations |
| B6 | Generic breeding records and `maize-v1` | Project-scoped additive schema |
| B7 | Working Memory and Validated Knowledge promotion | Human-gated lifecycle |
| B8 | Backfill, failure recovery, QA, docs, release rollback | Default enablement only after review |

## Critical implementation contracts

### Database authority

PostgreSQL owns all production project, DBTL, approval, artifact, activity, and
knowledge state. SQLite is limited to single-user local development, demos, and
tests. Production or multi-user startup with SQLite fails preflight.

### Reconciliation

Every DBTL cycle has a monotonic `db_revision` and canonical projection hash.
The temporary GreenAgent projection carries the same values. PostgreSQL
transition state commits atomically before the projection refresh. Missing,
stale, corrupt, or failed projections become `reconciliation_blocked`; further
transitions stop while history remains readable. Repair is PostgreSQL →
projection only.

### Conversation scope

Every thread has:

```text
workspace_id
project_id: nullable
scope_type: inbox | project | scratchpad
visibility: workspace | project-members | private-owner
```

Projectless is valid. Legacy threads backfill to Inbox/private-owner and are
never silently shared.

### Knowledge governance

```text
Learn output
  → Project Working Memory (provisional)
  → human promotion review
  → Validated Project Knowledge
  → superseded or retracted
```

Default authoritative retrieval excludes provisional Working Memory. Promotion
is a dedicated, identity-bound, immutable event—not a mutable status label.

### Breeding data

Core relational entities remain generic: germplasm, pedigree, cross,
population, site, season, environment, trial, plot, trait, observation, and
selection. Maize-specific attributes remain in validated, versioned
`maize-v1` JSONB or extension records until stable query patterns justify typed
columns.

## Quality gates

- Backend TDD before implementation.
- Create-all and Alembic-upgrade schema parity.
- PostgreSQL concurrency and failure-injection coverage.
- Cross-workspace, cross-project, agent-scope, and retrieval isolation tests.
- Thread event-order, streaming, scheduler overlap, channel, and legacy-route
  regression tests.
- Frontend typecheck, lint, unit, mocked Playwright, accessibility, mobile, and
  reduced-motion checks.
- Synthetic maize fixtures only until research data receives separate approval.
- README and relevant AGENTS updates in the same change set.
- Drift recorded before deviations are implemented.

## Rollback strategy

Every new product area is feature-flagged. Schema additions remain in place
once production data exists; rollback disables mutations and restores legacy
routing while keeping new records readable. Backfills are non-destructive and
idempotent. PostgreSQL remains authoritative even if DBTL projection support is
disabled. Historical scientific data is superseded, never deleted or
overwritten.

## Approval and execution authorization

The human approved this action plan, confirmed the six Builder proposals, and
authorized project-file mutation on 2026-07-24 under
`human-approval-2026-07-24-builder-plan`.

The human subsequently authorized project-scoped Phase-2 Builder execution
under `human-approval-2026-07-24-phase-2-builder-execution`. The project-local
Builder contract permits approved implementation actions and self-tests. The
user-global GreenAgent installation remains unchanged.
