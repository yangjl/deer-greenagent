# DBTL Phase 6 — implementation notes

Status: human-approved to proceed; remaining exit decisions explicitly deferred
Date: 2026-07-26
Plan authority: `plan/build/2026-07-25-dbtl-human-visible-phased-implementation-plan.md` § "Phase 6"
Design authority: `docs/plans/2026-07-25-dbtl-workflow-final-plan.md` §§ 6.3, 7.1–7.3

This is the cumulative handoff record for Phase 6 — what shipped, what it
deliberately does not do, and what the human exit review deferred for later.
It does not supersede the plan; where the two differ, the plan wins.

## Exit decision recorded 2026-07-26

The product owner approved moving from Phase 6 to Phase 7. This is a sequencing
decision, not evidence that the real-reference-project exit demonstration or
the policy choices below occurred. They remain recorded here so Phase 7 does
not silently invent answers:

- select the first maize/genomic-selection `StageSpec`;
- define the reconciliation-blocked retry/alert policy;
- review the `HUMAN_RESOLVED_CHECKS` boundary; and
- decide whether a generalist fallback remains permissible.

The real reference-project walkthrough to `ready_for_build` also remains a
deferred human demonstration. Automated coverage continues to protect the
gate while that walkthrough is outstanding.

## What shipped

### Pure contracts (`backend/packages/harness/deerflow/dbtl/`)

| Module | Owns |
|---|---|
| `capabilities.py` | The closed capability set from design § 7.2 |
| `stage_spec.py` | The versioned `StageSpec` registry, budgets, human-gate policy |
| `agent_selector.py` | Constrained capability→agent matching, generalist fallback |
| `worker_result.py` | The structured `StageWorkerResult` contract |
| `stage_runner.py` | Sync/async plan → dispatch → collect, with injected dispatch |
| `reconciliation.py` | The matrix, the readiness gate, approval invalidation |

These own no storage and import nothing that can persist, so the same functions
answer "is this legal?" for a reviewer and for the repository.

### Durable layer

- Migration `0014_dbtl_stage_execution`: `dbtl_datasets`,
  `dbtl_stage_worker_runs`, and three nullable columns on `dbtl_stage_runs`
  (`stage_spec_key`, `approved_dataset_fingerprint`, `approved_policy_version`).
- Migration `0015_repair_dbtl_schema_drift` repairs already-versioned
  development databases missing the cycle idempotency or classifier
  fingerprint columns, preserving their cycle and telemetry rows.
- `ReconciliationOpsMixin`, mixed into `DbtlCycleRepository` because these rows
  are part of the same cycle aggregate and share its revision and idempotency
  ledger.
- Atomic worker-result plus review-artifact commits and completed-event replay,
  so a retry returns the durable result rather than dispatching again.
- Six new API endpoints on the existing DBTL cycles router.

### Live graph execution

- `LiveStageAdapter` is now the production continuation adapter. It verifies
  `(project_id, cycle_id)` through the durable repository, derives the active
  Design/Reconciliation stage, and refuses foreign, locked, or awaiting-review
  work before dispatch.
- Work units run concurrently through real `SubagentExecutor` instances. Stage
  max-turn, timeout, and token limits clamp each worker; authenticated identity,
  tracing, and verified project scope reach the child runtime.
- Custom subagents may declare `dbtl_capabilities`; the selector records any
  use of the `general-purpose` fallback.
- Successful structured results produce a content-addressed JSON review package
  under the human-visible project `outputs/dbtl/` tree. Failed/unparseable
  workers are still recorded, but cannot create usable evidence or satisfy a
  gate.
- The continuation branch emits the existing task lifecycle events and always
  reports that human review remains required.

### Frontend

- `src/core/dbtl/reconciliation-{view,api,hooks}.ts`
- `src/components/workspace/project-rail/reconciliation-matrix.tsx`, mounted at
  the top of the reconciliation stage sheet.

### Tests

Focused contract, repository, migration, router, and frontend view-model suites
cover the shipped surface. Run the commands in the verification section below
for current counts; recorded counts are not treated as permanent documentation.

## The five guarantees, and where each is enforced

1. **An agent cannot silently resolve a contradiction.**
   `reconciliation.apply_resolution` refuses an agent `RESOLVED`/`WAIVED` on any
   check in `HUMAN_RESOLVED_CHECKS`; `decide_reconciliation_row` calls it before
   writing; `resolve_work_item` refuses reconciliation rows entirely; the HTTP
   endpoint hardcodes `actor_type="human"`. Four layers, because the first three
   each have a plausible way around them.

2. **A proposal is not a decision.** `RowStatus.PROPOSED` blocks the gate in
   both the backend and the view model.

3. **Raw data is immutable.** A raw source not declared immutable blocks the
   gate — a condition, not a lint.

4. **A dataset change invalidates the approval.** `review_stage` binds source
   identity, content hash, role, immutability, spec key, and policy version. A
   material redeclaration moves a `ready_for_build` cycle back to
   reconciliation and locks downstream stages; the read model also explains the
   invalidation. An identical redeclaration leaves the approval intact.

5. **Build cannot start early.** `resolve_stage_spec` refuses `build`, `test`,
   and `learn` outright, and `submit_stage_for_review` evaluates the gate for
   reconciliation before a reviewer is asked.

## Deliberately not done

- **No maize/genomic-selection domain pack.** Only the `generic` profile ships.
  The plan reserves "select the first maize/genomic-selection `StageSpec`" for
  the P6 review, so shipping a speculative one would pre-empt that decision.
- **No agent-authored matrix rows.** Rows are opened and decided through the
  authenticated API. The `PROPOSED` state and the agent authority rule exist and
  are tested, but nothing writes a proposal yet.
- **No reconciliation-blocked retry/alert policy.** The plan assigns this to the
  P6 review.
- **No browser E2E spec.** The compiled supervisor-to-SQL execution path has an
  integration test, and the plan's demo conditions (synthetic G2F-like fixture with
  contradictory treatment codes, mixed yield units, missing hybrid IDs, and a
  changed dataset hash) are covered at the repository level by
  `TestReadinessGate` and `TestApprovalBinding`, but not yet as a Playwright
  walkthrough.

## Behavioural change to earlier phases

Reconciliation can no longer be submitted for review with no declared inputs or
with an empty/unreadable matrix. Three Phase 3 tests were updated to declare a
dataset and settle a real matrix row
(`_declare_dataset` helpers in `test_dbtl_cycle_repository.py` and
`test_dbtl_cycles_router.py`). This is the intended effect of the phase, not a
workaround: reconciliation with nothing declared is meaningless.

An approved matrix is immutable. A material input change is the explicit
invalidation path that reopens it; otherwise post-approval row edits are
refused. Evidence references render in the reviewer matrix and are bounded at
the API boundary.

## Verification

From `backend/`:

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_dbtl_*.py \
  tests/test_persistence_bootstrap.py \
  tests/test_persistence_bootstrap_concurrency.py \
  tests/test_persistence_bootstrap_regression.py -q
```

From `frontend/`:

```bash
pnpm exec rstest run reconciliation-view
pnpm check
```

## Deferred exit-review decisions

Carried forward verbatim from the plan:

- Select the first maize/genomic-selection `StageSpec`.
- Define the reconciliation-blocked retry/alert policy.

And added by this implementation:

- Whether `HUMAN_RESOLVED_CHECKS` draws the line in the right place. Six of the
  sixteen checks currently require a person. Moving one in either direction is a
  one-line change; getting it wrong in the permissive direction means an agent
  settles something it should not, and in the strict direction means reviewers
  learn to click through the matrix.
- Whether a generalist fallback should be permitted at all in a project with no
  registered specialists, or whether such a project should be refused until it
  registers one. Currently permitted and recorded.

## Exit condition

> One real reference project reaches `ready_for_build` with a human-reviewed
> reconciliation report.

Deferred; not yet demonstrated on a real project. The path is exercised end to end through
the compiled supervisor and durable storage by
`test_dbtl_live_stage_graph_integration.py`, while
`TestReadinessGate::test_resolving_every_row_reaches_ready_for_build` covers the
human-reviewed transition to `ready_for_build`.
