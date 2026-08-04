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

**Recorded baseline (Phase 0 complete):**

- Scenario `test1-build` is content-addressed by database SHA-256
  `8b7fb9ff5fa4ce8cea467c7a9ccfa93d08bd099feedf57678f0f0ed3e7a16f16`
  and project-tree SHA-256
  `96249aace17091f4095814f9e421a84f68e1db30864e88c13842d935e82c2fd1`.
  It contains one cycle at `test` revision 10 and a matched cycle at
  `ready_for_build` revision 4.
- The completed Build is pinned to `generic:build:v6`; the current resolver
  selects `generic:build:v7`. Both use `generic:build-workflow:v1` and its five
  existing steps.
- All recorded Build phases used the built-in `general-purpose` agent as an
  explicit generalist stand-in. The captured attempt contains four durable
  worker rows: three completed phase results and one rejected predecessor.
- The recorded timeline spans 43 minutes 27 seconds from `load_design` opening
  to review-deck completion, including human-triggered retries. Persisted
  subagent usage records account for at least 574,752 Build tokens across two
  planner calls, four phase executions, and one failed summary call.
- The baseline rejected one completed validation result because an artifact
  object lacked `id` and `path`, and initially rejected the summary because it
  lacked `status`. These are the parser-brittleness controls for later phases.
- Build lineage records hashes, revisions, environment, inputs, outputs, and a
  review-document URI, but no structured executable rerun record. The Test
  stage has no validity assessment and did not re-execute Build.
- The restored proxy, frontend, and Gateway are healthy. The focused baseline
  suite passes: 374 tests across stage contracts, workflow/digests, step runs,
  lineage, live execution, validity, and the manual checkpoint pipeline.

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

**Implementation record (Phase 1 complete):**

- Added `verified_workspace_files` as the shared, symlink-refusing resolver for
  Build input directories and worker output files/directories.
- Output directories expand in stable order into individually hashed governed
  artifacts. Artifact/evidence/figure references are remapped to published
  URIs, overlapping declarations deduplicate, empty directories fail, and one
  worker may publish at most 500 files.
- OCR delegate review found and fixed unhandled hash-read failures, unbounded
  directory expansion, and duplicate durable references.
- Ruff passes; 156 focused execution tests and the 381-test affected DBTL suite
  pass. `test1-build` restores to the same `test` revision 10 and
  `ready_for_build` revision 4 states with proxy, frontend, and Gateway healthy.

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

**Implementation record (Phase 2 complete):**

- Added immutable `generic:build:v8` with a `structured_rerun_spec` gate while
  preserving v4-v7. The bounded record carries the entry point, exact command,
  seed, declared inputs, environment, configuration, and expected outputs;
  review prose is derived from its command.
- Reused `BuildExecutionBundle` and `DbtlBuildLineageRow`. Migration 0030 adds
  one JSON column with `{}` for historical rows, which remain readable through
  the API as `rerun_unverified`.
- Publication remaps rerun output paths to governed URIs. Compatible
  multi-phase records merge their bound inputs and outputs; conflicting
  commands or malformed/oversized fields leave no executable authority. The
  pinned-spec repository boundary refuses a v8 lineage write without the typed
  record.
- OCR delegate review found and fixed missing write-boundary enforcement,
  invalid-field coercion during publication, and first-worker authority when
  multi-phase commands conflict.
- Ruff passes; 422 focused and affected repository/workflow/migration tests
  pass. `test1-build` restores with its historical v6 lineage readable, the
  live database migrates to revision 0030, the captured cycles remain at
  `test` revision 10 and `ready_for_build` revision 4, and proxy, frontend, and
  Gateway all return HTTP 200.

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

**Implementation record (Phase 3 complete):**

- Added immutable `generic:test:v4` and pinned it before dispatch. V1-v3 remain
  resolvable; only v4 requires `server_verified_build_rerun`.
- Added the focused `live_stage.test_rerun` protocol module because command
  wrapping and byte verification are a distinct security boundary. It reuses
  the existing `WorkUnit`, Test stage grant, native sandbox Bash tool, typed
  worker row, whole-stage replay, and validity pack rather than adding a runner,
  sandbox capability, persistence table, or unsandboxed subprocess.
- The model receives one argument-free, return-direct tool. Its server-only
  contract executes the exact lineage command once in the fresh Test workspace,
  writes fixed stdout/stderr/exit-status receipts, and exposes no general Bash
  or file tool. The server verifies contained regular files, stable hashes,
  unique expected-output filenames, per-file/aggregate size bounds, changed
  inputs, exit status, and equality with approved Build hashes.
- `validated_test_assessment` replaces worker-authored `reproducibility` with
  those facts. Snapshot recovery accepts the record only from the deterministic
  rerun unit and fails closed when a v4 attempt lacks it; strong metrics cannot
  override a failed rerun.
- OCR delegate review found and fixed a forgeable model-authored receipt,
  unrestricted output copying, ambiguous duplicate filenames, unbounded output
  verification, premature success-like progress, and a recovery path that could
  trust the worker when the durable v4 rerun row was absent.
- Ruff passes; 439 affected DBTL, Test, persistence, and migration tests pass,
  including success, nonzero exit, missing record, cap/timeout, changed input,
  changed output, outside-workspace substitution, and current-vs-historical
  contracts. `test1-build` restores at `test` revision 10 and
  `ready_for_build` revision 4 with migration 0030 applied and all three manual
  services returning HTTP 200.

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

**Implementation record (Phase 4 complete):**

- Added immutable `generic:build:v9` with a
  `server_verified_phase_manifest` gate; v1-v8 remain resolvable. The bounded
  manifest contains one exact entry-point file, the complete declared output
  set, and the recorded plan's completion condition.
- Reused the existing per-phase publisher, `StageWorkerResult`,
  `phase_done_condition`, `BuildStepRecorder`, phase output digest, and replay.
  Publication establishes containment, regular-file status, stable hashes, and
  governed URIs before manifest verification; no parallel validator or
  persistence path was added.
- Replay rechecks the committed result digest, input and output bytes,
  completion marker, and manifest. A one-file directory is not guessed to be
  an executable entry point. Compatible Build field and summary
  representations still normalize without changing the governing manifest.
- OCR delegate review found and fixed exact-entry-point inference plus a split
  verdict where post-response verification failed but the live and durable
  worker records still said completed. All server-side verification refusals
  now emit the correcting terminal event, and manifest/completion/input
  failures persist a failed worker result.
- Ruff passes; 383 affected Build, Test, contract, workflow, review, and
  repository tests pass, including missing/unversioned manifests, changed
  completion conditions, output mismatch, exact entry points, directory
  publication, compatible representations, and replay. `test1-build` restores
  at `test` revision 10 and `ready_for_build` revision 4 with migration 0030
  applied and proxy, frontend, and Gateway all returning HTTP 200.

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

**Implementation record (Phase 5 complete):**

- Audited `build_subagent_runtime_middlewares` before adding behavior. The
  existing `SubagentExecutor` already supplies read-before-write, normalized
  recoverable tool errors, progress, sandbox/output policy, durable context,
  summarization without memory flush, and token/loop guards. Build continues to
  omit delegation, uploads, memory, chat controls, and title generation; no lead
  wrapper or duplicate middleware chain was added.
- Strengthened the phase prompt around read/edit/reread and recoverable tool
  next actions. Added one same-run correction only for a structured failed
  implementation check; repeat-run/reproducibility checks stay owned by Test.
  The failed result remains in child history and the correction runs at
  `after_agent`, after native guardrails, so it cannot refund a model call or
  jump around token, loop, safety, or forced-deadline enforcement.
- Reserved the correction's three one-time graph steps as deadline headroom.
  Forced-finalized Build work now produces an explicit failed live event and a
  failed typed worker result; partial files remain staged and never satisfy the
  phase contract. Existing cap, timeout, loop, and tool failures keep their
  native failure paths.
- OCR delegate plus senior review found and fixed the model-call refund and
  guardrail-bypass ordering defects. The broader run also exposed and fixed a
  pytest child-module restoration leak that made later live-stage tests patch a
  different executor module object.
- Ruff passes; 232 focused tests and the 513-test native middleware/DBTL
  regression suite pass. `test1-build` restores at `test` revision 10 and
  `ready_for_build` revision 4 with migration 0030 applied; proxy, frontend,
  and Gateway all return HTTP 200.

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

**Implementation record (Phase 6 complete):**

- Added immutable `generic:build:v10`; v1-v9 remain resolvable. The planner may
  declare at most eight skill names per phase, and those names are part of the
  plan digest.
- Reused the enabled per-user skill registry, `SubagentExecutor`'s `skills`
  allowlist, `SkillActivationMiddleware`, and `SkillToolPolicyMiddleware`.
  Each enabled registry winner is bound as `skill:<name>:sha256:<SKILL.md>` in
  phase workflow material; an empty declaration disables skills for that phase.
  The registry and bytes are checked again after execution, so missing or
  changed skill content fails before commit and corrects the live task verdict.
- Manifest v2 adds `declared_inputs`. For v10, `_build_input_artifacts` hashes
  only those implementation-consumed workspace paths plus durable datasets;
  broad `inputs_examined` and evidence fallbacks remain only for historical
  contracts. Orientation-only reads therefore do not invalidate the phase.
- OCR delegate review found and fixed skill-content TOCTOU at commit, split
  live/durable failure reporting on drift, non-scalar execution metadata, and
  loose restored-plan skill shapes.
- Ruff passes; 413 focused Build/subagent tests and the 461-test skill-policy
  and DBTL regression suite pass. `test1-build` restores at `test` revision 10
  and `ready_for_build` revision 4 with migration 0030 applied; proxy,
  frontend, and Gateway all return HTTP 200.

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

**Implementation record (Phase 7 code complete; rollout evaluation remains):**

- Added one validated setting at the existing DBTL boundary:
  `dbtl.build_worker_contract: hardened_v10 | legacy_v9`. It selects only new,
  unpinned phased Build attempts; a durable `stage_spec_key` always wins, and
  repository pinning remains the concurrency authority.
- `hardened_v10` is the shipped selection and `legacy_v9` is the bounded
  rollback. Both reuse the same `LiveStageAdapter`, `WorkUnit`, step recorder,
  worker middleware, publication, lineage, Test rerun, activity, and human
  gate. No wrapper agent, table, endpoint, or parallel execution path was
  added. Config schema version is 43.
- Kept the v9 manifest parser and broad provenance fallback intact. They will
  be removed only after matched-checkpoint telemetry shows they are unused;
  this phase does not manufacture that evidence from unit tests.
- OCR delegate selection and rule resolution covered the config, adapter, YAML,
  manual-profile healer, and rollout tests. Host review found no material
  defect. Ruff passes; 83 focused configuration/workflow tests, 404 broader
  Build/Test/config regressions, and 27 manual-pipeline tests pass.
- The existing manual-profile healer now adds the missing hardened selector
  without overwriting an explicit `legacy_v9` choice. `test1-build` restores at
  `test` revision 10 and `ready_for_build` revision 4 with migration 0030;
  proxy, frontend, and Gateway docs return HTTP 200.
- The remaining rollout gate is empirical: run repeated matched `test1-build`
  trials in both modes and record completion, tokens, calls, wall time,
  recovery, compatibility fallbacks, verification failures, and false-success
  incidents. Tune limits only from those measurements; do not retire v9 until
  the gate passes.

**Post-implementation tuning (2026-08-04):**

- Added immutable `generic:build:v11`, retaining every v10 governance gate and
  changing only the enforced per-worker token ceiling from 120,000 to 500,000.
  V10 and v9 remain resolvable and selectable; pinned attempts never inherit
  the new budget.
- `dbtl.build_worker_contract` now defaults to `hardened_v11`, with
  `hardened_v10` and `legacy_v9` as explicit rollback choices. The isolated
  manual profile defaults to v11 for new profiles while preserving explicit
  rollback selections.
- Captured v10 retries exposed a result-envelope compatibility gap rather than
  a sandbox escape: a completed worker wrote valid files inside its grant but
  returned `src/...` paths. Publication now resolves grant-relative output
  paths only against that worker's containment root and remaps exact files into
  the governed manifest; all existing traversal, symlink, hash, and directory
  entry-point refusals remain in force. The Build parser also preserves the
  observed unambiguous failure shapes (`incomplete`, nested `phase.status`,
  `{item: ...}` limitations, and `not_completed` checks) as truthful failures
  instead of replacing them with schema-error failures.

**Post-implementation evidence (2026-08-04) — the first matched trial, and what
it says about the wrapper question:**

The evaluation gate asked for token, completion, and false-success counts from a
real run. One arrived, and it argues against the lead-like wrapper rather than
for it. Three v11 workers on one cycle spent 863,047 tokens for 27,150 of output
(3%); two failed, and both failed the same way. The **succeeding** phase carries
the same defect: it published a governed, hash-bound `analysis-spec-v1.json`
naming its dataset at a host path that does not exist, so it is a completed,
`is_trustworthy: true` record that Test could never re-run — one
false-success incident, on the phase that passed.

The cause was not reasoning autonomy, which §"Evaluation gate" makes the sole
condition for reconsidering a wrapper. It was the contract: `build_prompt` told
workers to *"use absolute /mnt/user-data paths for the entry point, inputs, and
configuration"*, and both a registered specialist and the generalist obliged by
hardcoding a path neither could see. The budget was not binding either (249K of
a 500K ceiling, never reaching the 75% reserve warning), and the worker's own
escalation route went unused — `needs_input` exists precisely for a phase
blocked on its environment, and the failed result carried an empty
`clarification_question`.

`generic:build:v12` therefore answers it as a contract fix, per this plan's
reuse rule:

- The rerun clause no longer demands absolute paths in code. The *record* still
  names them — it is the server's, and Test re-runs from it — while the code
  reads `DBTL_INPUT_1..N` and writes beneath `DBTL_WORKSPACE`.
- `deerflow.dbtl.build_grant` is the pure half: `build_input_grant` refuses an
  input outside the grant (the environment must not become the channel a
  scanner-refused path arrives through), and `scan_foreign_paths` reports
  absolute literals no granted root covers, compared segment-wise.
- `verify_granted_paths` refuses the phase with the literal and the line, on the
  same footing as `verify_phase_manifest`. An unreadable entry point is **not**
  refused — that belongs to `server_executed_entry_point`, the sibling gate.
- `dbtl.build_implementer_agent` is the implementer dial, deliberately recorded
  as a stand-in and deliberately unable to affect correctness.

`server_executed_entry_point` is enforced before publication. The adapter runs
the manifest's entry point in the existing sandbox under the phase's exact
write grant, injects numbered input paths through the environment, and derives
exit status, bounded stdout/stderr logs, and output hashes from fixed receipt
files. The receipt is stored in the existing `BuildStepRecorder` execution and
payload rows, so replay remains in the same digest chain as the phase output.
No bare host command runner exists: local macOS verification uses the identical
`sandbox-exec` profile builder as model-facing Bash and restricts project reads
to issued inputs plus the phase workspace. Remote providers preflight `bwrap`
and execute in a bubblewrap namespace that hides the rest of `/mnt/user-data`;
without it they fail closed. A local platform without process-tree confinement
also fails closed. V12 restores v10's 120K phase ceiling and replaces the old
same-history correction loop with one fresh 40K specialist invocation carrying
only the failure and the previous staged workspace.

V12's phase manifest is version 3. `declared_inputs` is the narrow union of
files consumed while implementing or executing the phase, while
`execution_inputs` identifies only the server-issued subset consumed by the
entry point at runtime. The server injects the full original grant so subset
declaration never renumbers `DBTL_INPUT_n`, and refuses an execution input that
was not issued. Source scanning covers every declared source-code output, not
only the entry point.

**Implementation record (finalization timing — the token axis):**

This phase's "tune finalization timing" had a precondition stated in its own
exit criteria: cap and forced-finalization outcomes must be *explicit failures*.
They were not — they were explicit **misattributions**, in three places, and all
three were repaired before the deadline was touched.

- **A cap was reported as a formatting problem.** A worker stopped at its token
  ceiling is cut off mid-answer; prose is what survives. But the parse-failure
  branch preceded the capped branch in `_terminal_seat_event`, so the capped
  check could only ever fire for a worker whose output already parsed, and
  `collect_results` — the durable record — had the identical ordering. A phase
  that spent 125.5K tokens against a 120K ceiling was reported to its owner as
  "returned prose instead of a structured result", which sends a reader to fix
  the worker's formatting when the only lever is the budget. Both paths now
  resolve through `stage_runner.worker_rejection_failure`; a genuinely
  unparseable answer *inside* budget still names the contract, because those two
  need opposite fixes.
- **A stage-level refusal printed a blank explanation.** Where every worker
  succeeded and the Build still refused for want of a structured rerun record,
  the note emitted "Why each worker did not count:" and then nothing — no worker
  had failed. `MISSING_STRUCTURED_RERUN_REASON` is stated once and reaches both
  the durable workflow step and the conversation note.

With classification honest, the deadline gained its **token axis**. The turn
axis was never the binding one for Build: 450 turns against 120K tokens means
the tokens run out first, so the turn deadline never fired.
`TokenBudgetMiddleware` does hard-stop at the ceiling, but it strips tool calls
from *the message the model just wrote* — mid-loop, that is prose — and records
`token_capped`, so even a good answer is discarded as untrustworthy.

- The axis **warns and never forces**. Forcing would duplicate a working hard
  stop and, worse, relabel a worker that blew through its budget as one that met
  a deadline. The turn axis still forces, because `recursion_limit` *raises* and
  nothing downstream can recover an answer from that.
- It fires at 75% (`DEFAULT_TOKEN_RESERVE_FRACTION`): one more full call must
  re-send the conversation as input *and* produce the result as output, so too
  small a reserve warns a worker that can no longer afford to answer. It removes
  tools, so "write your result now" is mechanically true rather than advice —
  the same rule that made the turn axis work.
- One warning per run, on whichever axis binds first. The instruction is
  identical either way and the tools go either way, so a second notice would
  spend budget to change nothing, on a run short of budget by definition.
- Both middlewares now share `_token_usage.accumulate_usage`. Not tidying: a
  deadline counting differently from the guard it front-runs fires at the wrong
  moment, and the disagreement is invisible because both numbers look plausible
  alone. The accounting is delta-per-message rather than a sum, because
  `TokenUsageMiddleware` rewrites a message's usage retroactively once its
  subagents report.
- `max_tokens` comes from `_token_limit_for_worker(unit, dispatch_budget)` — the
  *same* resolver the executor's own budget uses, pinned by a test that counts
  both call sites — and is `None` for metered-only execution, leaving the axis
  inert.

Two test-hygiene repairs came with it, both instances of a rule this repo
already states: a test states the switch it exercises and never inherits one.
`test_dbtl_stage_review_pages.py` inherited the ambient `build_workflow_steps`,
so it failed on a developer machine and would pass in CI; and a
`_model_call_budget` stub had not followed the new `extra_headroom_steps`
keyword, failing eight progress tests with a `TypeError` about neither progress
nor budgets.
