# Build enhancement: harden the governed worker

**Status:** Design decision only. This document does not authorize
implementation.

**Date:** 2026-08-03

**Scope:** The Build worker and its product contract. The five-step workflow
remains owned by
[2026-08-01-dbtl-build-observable-retryable-workflow-plan.md](2026-08-01-dbtl-build-observable-retryable-workflow-plan.md).
Test retains the product shape defined by the August 2 Test plans; this plan adds
one prerequisite: Test must actually re-execute Build work.

## Decision

Keep and harden the existing Build worker. Do **not** create a second,
lead-like agent graph.

The worker already uses DeerFlow's native agent substrate: configured models,
tools, skills, authorization, sandboxing, and shared subagent middleware. What
makes it a Build worker is its governed wrapper:

- one bounded `WorkUnit` at a time;
- an attempt-scoped writable workspace;
- typed outputs and explicit completion conditions;
- server-owned verification, publication, hashes, lineage, and replay; and
- human review bound to an exact evidence revision.

Bring selected lead-agent strengths into that wrapper only when they preserve
those boundaries. A conversational lead agent may discuss or prepare Build work,
but it must not execute, record, or advance a DBTL Build.

**Reuse before invention:** implement this plan by extending existing DeerFlow
code and scripts wherever possible. Reuse the current `WorkUnit` dispatcher,
`SubagentExecutor`, shared middleware, Build workflow and recorder, publication
validators, lineage repository, Test execution tools, manual checkpoint scripts,
and existing contracts. Add a new abstraction only when no current owner can
support the behavior without breaking its boundary; document that gap first.

## Product principle

A Build is audited by the product it leaves behind: implementation bytes,
declared inputs, environment, rerun procedure, outputs, logs, and hashes. Agent
steps remain useful telemetry, not scientific evidence.

**Why reclassifying process as telemetry is safe.** The obvious objection is
that a richer, more stateful worker breaks the digest chain's guarantee, so "the
record stops being true." It does not, because the digest chain is a **caching
and work-preservation mechanism, not the audit record**. Its properties are all
about *not re-running work*: an upstream change invalidates its descendants, a
successful predecessor is not re-run because a later step failed, and an
invalidated success is reported rather than deleted. The audit record is a
different set of objects — the published artifacts and their hashes, the Build
lineage row, and the human gate bound to an exact evidence revision. So when a
skill, prompt, or affordance changes and a committed phase does not re-run,
nothing true becomes false: the script is still the bytes that were published,
still produces the outputs it produced, and Test still re-runs it. The cache is
stale; the evidence is not wrong.

That distinction is load-bearing further down. Binding a phase's skills by
content hash (below) exists for **cache correctness** — so a changed skill
invalidates the phases that used it rather than silently replaying them — not
for audit correctness, which the published bytes already carry. An implementer
who mistakes the one for the other will make that binding stricter than it needs
to be.

This is also not a reversal. `BUILD_SPEC_V4` already made the same move once,
replacing the `reproducible_execution` gate with `recorded_rerun_procedure` and
moving the verdict to Test. This plan carries that existing direction to its
conclusion rather than re-opening a settled question.

This requires a **dual contract**, not a choice between JSON and files:

1. The server verifies facts derivable from the workspace: containment, file
   existence, entry-point execution, declared outputs, and hashes.
2. The worker returns a small typed manifest for facts the filesystem cannot
   supply.

**That manifest's contents are normative, not illustrative.** "Small" erodes in
both directions — left vague it either never shrinks, and nothing changes, or it
shrinks past what governance needs. Exactly these fields are required, and a
change to the list is a change to this plan:

- **artifact roles** — which published files are the implementation, the
  configuration, the outputs, and the logs. The filesystem knows the bytes; only
  the worker knows which of them is the thing being reviewed.
- **declared inputs** — the files the *implementation consumes*, which is not
  the same set as the files the agent opened (see delivery item 8).
- **rerun fields** — entry point, command, seed, environment, expected outputs.
  Not derivable, and Test cannot re-execute without them.
- **limitations** — what the work does not establish.
- **the phase completion assertion** — the worker's claim that its declared
  done-condition is met, which server verification then checks rather than
  trusts.

A harmless representation mismatch must not discard otherwise verified work.
Missing governance fields, unverified outputs, failed completion conditions, or
containment violations still fail closed.

## Hardening rules

### Preserve the boundary

- Keep `WorkUnit`, the five-step workflow, phase decomposition, and resumable
  digest chains.
- Prefer extending the current workflow step, validator, repository operation,
  middleware, or test fixture over creating a parallel Build-specific path.
- Keep the isolated write grant, path audit, server-owned publication, and the
  `outputs/dbtl` block.
- Keep typed results and exact hash-bound human review.
- Keep `needs_input` as the only worker-to-human route; do not add
  `ask_clarification` inside workers.
- Keep delegation disabled inside a Build worker. A phase worker completes its
  assigned phase; it does not create another orchestration tree.

### Import capabilities selectively

Adopt an affordance only when it is deterministic, bounded, and attributable to
the phase:

| Affordance | Decision |
| --- | --- |
| Context summarization | Use the shared subagent implementation; verify that Build receives it and test long phases. |
| Read-before-write and tool-error recovery | Harden inside the current worker. |
| In-unit correction after a failed check | Allow within the same attempt and budget; never turn a failed check into success without rerunning it. |
| Skills | Allow only when declared for the phase and bound by name plus content hash. |
| Memory | Reject as ambient state. Use approved Design evidence or explicitly bound knowledge claims. |
| Uploads and title generation | Reject; these are conversation concerns. |
| Lead-agent delegation and chat controls | Reject; they duplicate or bypass governed stage controls. |

Budgets and deadlines remain operational safeguards. Tune them from evidence,
but never treat a cap-triggered or forced-finalization result as completed Build
work.

## Verified gap: Test does not re-execute today

The current Test worker can use execution tools, but its prompt asks for a
validity assessment rather than requiring a rerun. `reproducibility` is folded
from worker-supplied status, and the Build rerun procedure is rendered for
humans but is not invoked by the server or Test worker.

**Two facts from that inspection change what delivery item 3 costs, and both
argue it is smaller than it looks.**

*Test can already execute.* A stage workspace is prepared for every stage except
Design, and Test units are not in `_READ_ONLY_ROLES` — which contains only
Build's summarizer and planner — so a Test worker already receives bash and the
isolated writable grant. Item 3 is therefore **prompt, plumbing, and
verification work, not a new execution capability**: nothing needs a new tool
grant, sandbox mode, or fence review.

*Server-owning a check is an established pattern.* `evaluate_validity` is pure
over the statuses the worker supplied, but the server already overrides one of
them — `reconciled_inputs` is replaced with a server-owned pass derived from
Build lineage when reconciliation is optional. Deriving `reproducibility` from a
real rerun follows that same shape rather than inventing a new authority, which
is this plan's reuse rule applied to its hardest item.

Therefore Build's result contract must not be relaxed before Test has a real,
evidence-producing rerun path.

The rerun record must be structured and stored with Build lineage:

- entry point and exact command;
- seed and declared inputs;
- required environment and configuration; and
- expected output paths or checks.

Test must execute that record in a fresh Test workspace, retain the command,
logs, exit status, and produced hashes, and derive the reproducibility check
from those facts rather than model assertion.

## Delivery constraint

The phased implementation plan below is the delivery order. Test re-execution
must ship before any Build contract relaxation, and every phase must preserve
replay of committed work.

## Evaluation gate

Use captured manual checkpoints for matched-state Build runs. Change one
variable at a time and record:

- phase and full-Build completion rate;
- tokens, model calls, and wall time;
- retries and recovered partial phases;
- parser compatibility fallbacks;
- workspace-verification failures; and
- false-success or ungoverned-output incidents.

Prefer hardening that reduces discarded valid work without increasing false
success. Cost increases must be justified by improved completion or recovery.

A lead-like wrapper may be reconsidered only if, after the delivery sequence
above, matched evaluations show that failures are predominantly caused by
missing long-horizon reasoning autonomy—not contracts, tools, budgets, context
handling, or progress/failure classification. Even then, the proposal must reuse
the same `WorkUnit`, workspace, typed manifest, publication, lineage, replay,
and human-gate boundaries rather than introduce a parallel Build authority.

## Verification

- A working implementation is not discarded solely because a non-governing
  summary used a compatible representation.
- A declared output directory publishes as verified files.
- A missing entry point, failed completion condition, or absent declared output
  cannot commit a phase, regardless of worker prose.
- Test reruns the recorded command in a fresh workspace and binds its
  reproducibility verdict to logs, exit status, and hashes.
- A changed declared input or bound skill invalidates only dependent phases.
- A capped, timed-out, or forced-finalization worker is never reported as
  successful.
- Workers remain unable to write outside their attempt grant, delegate work,
  answer their own human gate, or mutate DBTL state directly.
- The implementation reuses existing dispatch, middleware, workflow,
  publication, lineage, Test execution, and manual-checkpoint machinery; any
  new parallel mechanism has an explicit, reviewed necessity.

## Non-goals

- A second agent kind or a copy of the lead-agent graph.
- Parallel scripts, validators, or persistence paths that duplicate an existing
  DeerFlow owner.
- Removing typed results, human review, hash bindings, or server publication.
- Removing the five-step workflow or phase-level recovery.
- Treating agent activity logs as scientific evidence.
- Redesigning Test beyond the rerun prerequisite defined here.

## Phased implementation plan

### Gate for every phase

Keep each phase independently shippable and reversible. Before advancing:

- run its focused backend tests plus the affected DBTL suites;
- review the diff with OCR in delegate mode and resolve all material findings;
- replay the captured `test1-build` checkpoint and record completion, token,
  timing, progress, and failure-classification results; and
- name reused components in the PR, with an explicit justification for any new
  module, script, contract, table, or feature flag.

Do not combine a persistence migration, execution-policy change, and parser
relaxation in one phase.

### Phase 0 — Baseline and contract inventory

Freeze a matched-state baseline from `test1-build`. Record the current Build
spec, workflow-step digests, worker configuration, emitted activity, lineage,
published files, parser fallbacks, and Test reproducibility behavior.

**Reuse:** `scripts/dbtl_manual.py`, workflow/read-model endpoints, run events,
and existing Build workflow/live-stage tests.

**Exit:** the same checkpoint can be restored and measured repeatedly, and each
later phase has a before/after comparison. No product behavior changes.

### Phase 1 — Correct output publication

Teach the existing Build publisher to expand a declared output directory into
verified regular files, preserving containment, hashes, stable ordering, and
per-file failure reporting. Make this the first rule of the workspace verifier
used again in Phase 4, not a publication-local checker with different rules.

**Reuse:** `_publish_build_worker_artifacts`, `_directory_input_artifacts`, the
current workspace/path helpers, `BuildStepRecorder`, and publication tests in
`test_dbtl_live_stage_execution.py` and
`test_dbtl_build_workflow_execution.py`.

**Exit:** output directories publish as governed files; symlinks, escaped paths,
missing files, and changed bytes still fail closed; replay does not republish a
committed phase.

### Phase 2 — Make the rerun record typed lineage

Replace rerun prose as the execution authority with structured fields for entry
point, command, seed, declared inputs, environment/configuration, and expected
outputs. Keep prose as a derived review view.

**Reuse:** `BuildExecutionBundle`, `DbtlBuildLineageRow`,
`BuildTestOpsMixin.record_build_lineage`, existing lineage serialization, and
the Build summary/deck renderers. Extend the current migration chain rather than
creating a second execution record.

**Compatibility:** historical rows remain readable but are marked
`rerun_unverified`; they cannot produce a verified Test reproducibility pass.

**Exit:** typed rerun data round-trips through persistence, API projections,
review Markdown, and the Build deck without the summarizer rewriting it.

### Phase 3 — Make Test perform the rerun

Add a bounded Test rerun unit that receives the exact lineage record, executes
it in a fresh Test workspace, and preserves command, logs, exit status, and
output hashes. Derive the required `reproducibility` check from those facts;
worker prose cannot override it.

**Reuse:** the existing Test `StageSpec`, `WorkUnit`, `_dispatch_units`, sandbox
Bash/file tools, stage workspace grant, validity pack, and assessment repository.
Do not add an unsandboxed server command runner.

**Exit:** successful, non-zero-exit, missing-command, timeout, changed-input,
and changed-output cases deterministically produce the expected validity result.
Strong headline metrics cannot override a failed rerun.

### Phase 4 — Enforce the dual Build contract

Verify workspace facts before a phase commits: entry point, declared outputs,
containment, hashes, and the versioned completion condition. Reduce the worker
payload to the manifest facts the server cannot derive.

**Reuse:** existing publication verification, `StageWorkerResult`,
`phase_done_condition`, `BuildStepRecorder`, phase replay, and Build workflow
digests. Add checks to these owners rather than a parallel validator service.

**Exit:** a compatible non-governing summary shape no longer discards verified
work, while missing manifest authority, failed completion, absent outputs, or
workspace violations cannot commit. This phase may ship only after Phase 3.

### Phase 5 — Harden the existing worker interior

First confirm which capabilities already arrive through
`build_subagent_runtime_middlewares`; enable or fix them rather than copying lead
middleware. Then improve read-before-write behavior, tool-error recovery, and
one bounded in-attempt correction cycle after a failed check. Keep delegation,
chat controls, memory, uploads, and title generation disabled.

**Reuse:** `SubagentExecutor`, the built-in `general-purpose` agent, shared
summarization/durable-context/tool-error middleware, file tools, and
`FinalizationDeadlineMiddleware`.

**Exit:** matched runs show fewer discarded phases or retries without more false
success, ungoverned files, or unexplained cost. Cap, timeout, loop-stop, and
forced-finalization outcomes remain explicit failures.

### Phase 6 — Bind skills and narrow provenance

Allow only phase-declared skills, resolved through the existing skill registry
and bound by name plus content hash into workflow material. Record implementation
inputs declared and actually consumed; do not bind every file read for agent
orientation.

**Reuse:** `SkillActivationMiddleware`, `SkillToolPolicyMiddleware`, `StageSpec`,
workflow digest material, `_build_input_artifacts`, and lineage fingerprinting.

**Exit:** changing a bound skill or consumed input invalidates exactly the
dependent phase chain; changing an unrelated enabled skill or orientation-only
file does not.

### Phase 7 — Tune, simplify, and roll out

Use matched checkpoints to tune worker limits and finalization timing. Retire a
parser compatibility path only after telemetry shows the dual contract has made
it unnecessary. Roll out through the existing DBTL configuration boundary and
retain the prior behavior as the rollback path until the evaluation gate passes.

**Reuse:** existing DBTL configuration, activity/run-event telemetry, manual
checkpoint commands, compatibility parser, and targeted regression suites.

**Exit:** the hardened worker improves completion or recovery at acceptable
cost, reports progress and failures truthfully, and introduces no second agent,
execution authority, publication path, or persistence model.
