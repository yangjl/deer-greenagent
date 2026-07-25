# Independent Tester Plan: Workspace-Oriented Breeding Foundation

Status: predeclared before test execution  
Cycle: `workspace-oriented-breeding-design`  
Tester authorization: `human-tester-request-2026-07-24`  
Builder handoff: `.greenagent/handoffs/workspace-oriented-breeding-design-1784937178545.json`

## Verdict boundary

This test evaluates only the Builder's declared **B0–B3 foundation
increment**. It does not treat B4–B8 as delivered.

- The foundation verdict is `PASS` only when all critical known-answer checks
  pass and no high-severity implementation drift is found.
- The foundation verdict is `FAIL` when a critical security, migration,
  compatibility, or navigation check fails.
- The foundation verdict is `BLOCKED` when required evidence or an executable
  boundary is unavailable and the missing evidence prevents a defensible
  result.
- The complete approved plant-breeding system remains `NOT COMPLETE` while
  B4–B8 are pending, regardless of the foundation verdict.

Expected outcomes are fixed by this document and will not be changed after
execution.

## Independence strategy

Tester will not read Builder source or implementation files and will not
modify implementation. Validation combines:

1. Black-box command and HTTP boundary checks against documented application
   interfaces.
2. Contract-to-artifact comparison using the approved design, phased plan,
   Builder handoff, and Builder foundation report.
3. Existing regression suites as supporting evidence only; a Builder rerun is
   not accepted as independent validation by itself.
4. Negative controls at authentication and cross-user boundaries where the
   documented interface makes them available.

Full implementation independence is not feasible in this increment because
the handoff contains no separate Tester fixture package, authenticated browser
fixture, screenshots, orientation-study fixture, PostgreSQL concurrency
environment, or independently implemented reference client. Those gaps are
recorded as evidence limitations and as drift when the phased plan required
them.

## Known-answer checks

| ID | Boundary | Predeclared expected result | Critical |
|---|---|---|---|
| K01 | GreenAgent protocol | Builder→Tester handoff exists; cycle validates; test execution is project-scoped and human-authorized | yes |
| K02 | Anonymous API | `GET /api/workspaces` rejects an unauthenticated caller with `401` | yes |
| K03 | Anonymous UI | `/workspace/projects` preserves the authentication boundary rather than exposing project data | yes |
| K04 | Workspace isolation | User A can create/list its workspace; User B cannot list or access User A's workspace/project | yes |
| K05 | Project identity | Duplicate project slug within a workspace is rejected with the documented conflict response | yes |
| K06 | Legacy conversation classification | Legacy/unassigned threads resolve to `scope_type=inbox`, `visibility=private-owner`, and nullable workspace/project IDs | yes |
| K07 | Migration parity | Current Alembic head and fresh schema bootstrap agree for the foundation schema | yes |
| K08 | Compatibility regression | Relevant backend thread/router/persistence tests remain green | yes |
| K09 | Frontend contract | Workspace APIs retain the documented typed request/response behavior | yes |
| K10 | Workspace-first navigation | `/workspace` resolves to Projects; Projects and Inbox are primary navigation destinations | yes |
| K11 | Static quality | Frontend type/lint check and focused backend formatting/lint checks pass | yes |
| K12 | Full-plan honesty | B4–B8 remain explicitly pending and are not represented as implemented | yes |

## Required evidence and drift rules

The Tester will compare the declared B0–B3 outputs and tester-handoff items
against the artifact inventory. Missing items are not excused by passing unit
tests. Findings are categorized as:

- `implementation-fix`: the approved plan is clear, but the implementation or
  handoff is absent or inconsistent.
- `design-revision`: the approved design is ambiguous or internally
  inconsistent and requires Designer/human resolution.

Significant drift is reported to the human and routed to Builder or Designer;
Tester will not fix it.

## Execution set

- GreenAgent cycle validation and transition guards.
- Backend foundation and compatibility regression commands from the public
  module interface.
- Frontend unit suite and `pnpm check` from the public module interface.
- Running-stack anonymous HTTP probes through the unified public entry.
- Artifact inventory and documented deliverable comparison.

## Human Input Needed

No additional decision is required to execute this project-scoped test. A
human decision will be requested only if the evidence is ambiguous or a
significant design drift is found.
