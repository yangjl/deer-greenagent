"""Planning and collecting one stage's worker fan-out (Phase 6).

**A stage run produces evidence; it never satisfies a gate.**
:class:`StageExecutionOutcome` has no approval field, ``satisfies_gate`` is a
constant ``False`` property, and nothing here imports the review path. Approval
remains a typed human-review record bound to the evidence revision the reviewer
saw.

Dispatch is injected rather than imported. ``SubagentExecutor`` owns
cancellation, tracing, task events, and the child runtime; the production
adapter clamps it to this module's stage budget. Taking dispatch as a parameter
means the plan, each worker prompt, and partial-failure folding are testable
without a model, sandbox, or thread. The executor is wired in at the call site
in ``deerflow.agents.dbtl``.

A worker that fails is *kept*, as a ``failed`` result. A fan-out where two of
three workers crashed must not read as a tidy run with one worker.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from deerflow.dbtl.agent_selector import Assignment, SelectionResult, capability_brief, select_agents
from deerflow.dbtl.build_fulfillment import BUILD_FULFILLMENT_CONTRACT
from deerflow.dbtl.build_grant import INPUT_ENV_PREFIX, PROJECT_ROOT_ENV, WORKSPACE_ENV
from deerflow.dbtl.deliverable_audit import DELIVERABLE_AUDIT_CONTRACT
from deerflow.dbtl.stage_spec import StageSpec, WorkerBudget
from deerflow.dbtl.worker_result import (
    StageWorkerResult,
    WorkerResultRejected,
    extract_result_payload,
    failed_result,
    parse_worker_result,
)

logger = logging.getLogger(__name__)

STAGE_WORKER_OUTPUT = "stage_worker"
BUILD_PLAN_OUTPUT = "build_plan"
BUILD_SUMMARY_OUTPUT = "build_summary"
BUILD_WORK_MEETING_OUTPUT = "build_work_meeting"
TEST_RERUN_OUTPUT = "test_rerun"
FORCED_BUILD_FINALIZATION_FAILURE = "The Build worker was forcibly finalized at its turn deadline; partial files remain staged, but the phase did not complete normally."

#: What each guardrail actually stopped, in the worker's own terms.
_CAPPED_WORKER_CAUSES = {
    "token_capped": "reaching its token budget",
    "turn_capped": "reaching its turn limit",
    "loop_capped": "the repeated-tool-call guard firing",
}


def capped_worker_failure(stop_reason: str | None, *, wrote_structured_result: bool = True) -> str:
    """Why a capped worker failed, or ``""`` when no cap applies.

    **A cap is the cause; whatever survives it is the symptom.** A worker
    stopped at its budget did not *choose* to answer in prose — it was cut off
    before it could write its structured result, so reporting the shape of the
    fragment ("returned prose instead of a structured result") sends a reader to
    fix the worker's formatting when the only lever that would help is the
    budget. It also hides the expensive part: a phase that spent its whole
    allowance leaves real staged work behind, and the message that names a
    formatting problem reads as though nothing was lost.

    Both the live lane and the durable record resolve their failure text through
    here, because one failure described two ways is how a reader ends up
    debugging the wrong thing — and the two paths are required to agree.
    """
    cause = _CAPPED_WORKER_CAUSES.get(str(stop_reason or "").strip())
    if not cause:
        return ""
    truncated = "" if wrote_structured_result else " before it could write its structured result"
    return f"The worker stopped after {cause}{truncated}. Any generated files remain staged and were not accepted as stage evidence."


def worker_rejection_failure(stop_reason: str | None, rejection: str) -> str:
    """The failure text for a worker whose answer could not be parsed."""
    capped = capped_worker_failure(stop_reason, wrote_structured_result=False)
    return capped or f"The worker's result did not satisfy the stage contract: {rejection}"


@dataclass(frozen=True, slots=True)
class WorkUnit:
    """One dispatchable piece of a stage."""

    unit_id: str
    capability: str
    agent_name: str
    prompt: str
    via_generalist: bool = False
    #: The model this seat should run on, or ``None`` to inherit the parent
    #: run's. A chair synthesizing a research design and a worker surveying
    #: files are not the same task, and before this every seat ran on whatever
    #: the composer happened to be set to.
    model: str | None = None
    #: What this seat is doing in the debate (``position`` / ``red_team`` /
    #: ``chair``) and which round it belongs to. Carried on the unit rather than
    #: inferred from the unit id downstream, because a live view that parses
    #: identifiers to work out who is speaking breaks the moment an id changes.
    role: str = "position"
    round: int = 1
    #: A few words naming what this seat brings, when a proposed roster supplied
    #: one. Empty for a capability-selected seat, whose angle is its capability.
    focus: str = ""
    #: Owner-edited dials from the meeting preflight card. ``max_tokens`` is a
    #: legacy recorded override and applies only when the enclosing budget
    #: explicitly enforces token limits; Design councils currently do not.
    #: ``reasoning`` is ``"extended"`` to enable extended thinking.
    max_tokens: int | None = None
    reasoning: str = ""
    #: A server-owned completion assertion this unit must return as a passing
    #: quality check before its terminal event or durable step can be called
    #: complete. Empty for contracts that predate phase-level completion.
    completion_check: str = ""
    #: Which parser owns this unit's final answer. Build has several typed
    #: helper seats that intentionally do not return ``StageWorkerResult``.
    #: The live event layer must not grade those answers against the generic
    #: schema before their owning parser sees them.
    output_contract: str = STAGE_WORKER_OUTPUT
    #: Skill names this unit alone may discover and activate. ``None`` keeps
    #: the subagent's configured catalog; an empty tuple disables skills.
    skills: tuple[str, ...] | None = None
    #: Optional server-owned tool contract for a narrowly wrapped unit. The
    #: model may see its prompt but cannot author or alter this payload.
    tool_contract: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False, hash=False)


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """What a dispatcher reports back for one work unit.

    Deliberately shaped like ``SubagentResult`` rather than like the structured
    contract: the dispatcher's job is to run the worker and hand back its text
    and stop reason, and *parsing* is this module's job so every caller gets the
    same validation.
    """

    unit_id: str
    text: str | None
    stop_reason: str | None = None
    error: str | None = None
    #: Provider-reported usage for this worker. Metering is independent of
    #: enforcement: an uncapped council still records exactly what it spent.
    token_usage: Mapping[str, int] | None = None
    #: The worker was stopped to make it write its answer. Not a
    #: ``stop_reason``: a run that met a deadline it was warned about is finished
    #: work, and routing it through the cap channel would discard the evidence.
    #: It still narrows what the worker could examine. Build treats that as an
    #: explicit failed phase; older stage contracts record it as a limitation.
    forced_finalization: bool = False


class AsyncWorkerDispatcher(Protocol):
    """Async sibling used by graph nodes that fan out real subagents."""

    def __call__(
        self,
        units: Sequence[WorkUnit],
        *,
        budget: WorkerBudget,
    ) -> Awaitable[Sequence[DispatchOutcome]]: ...


@dataclass(frozen=True, slots=True)
class StageExecutionPlan:
    """What a stage intends to do, before it does any of it."""

    spec: StageSpec
    selection: SelectionResult
    units: tuple[WorkUnit, ...] = ()

    @property
    def dispatchable(self) -> bool:
        return self.selection.satisfied and bool(self.units)

    def as_dict(self) -> dict[str, object]:
        return {
            "spec_key": self.spec.spec_key,
            "stage": self.spec.stage,
            "dispatchable": self.dispatchable,
            "selection": self.selection.as_dict(),
            "units": [
                {
                    "unit_id": unit.unit_id,
                    "capability": unit.capability,
                    "agent_name": unit.agent_name,
                    "via_generalist": unit.via_generalist,
                    "output_contract": unit.output_contract,
                }
                for unit in self.units
            ],
        }


@dataclass(frozen=True, slots=True)
class StageExecutionOutcome:
    """Everything one stage run produced, and nothing it decided."""

    plan: StageExecutionPlan
    results: tuple[StageWorkerResult, ...] = ()
    rejected: tuple[str, ...] = field(default_factory=tuple)

    @property
    def satisfies_gate(self) -> bool:
        """Always ``False``.

        A property rather than a field, carried over from the Phase 5 stub for
        the same reason it existed there: a gate is closed by a typed human
        review bound to an evidence revision, and a graph node is not a
        reviewer. There is no call that sets this to ``True``.
        """
        return False

    @property
    def trustworthy_results(self) -> tuple[StageWorkerResult, ...]:
        """Results that may count toward the stage's required output."""
        return tuple(item for item in self.results if item.is_trustworthy)

    @property
    def produced_usable_evidence(self) -> bool:
        return bool(self.trustworthy_results)

    @property
    def token_usage(self) -> dict[str, int]:
        """Aggregate provider-reported usage across every recorded worker."""
        return {key: sum(int(item.token_usage.get(key, 0) or 0) for item in self.results) for key in ("input_tokens", "output_tokens", "total_tokens")}

    def as_dict(self) -> dict[str, object]:
        return {
            "plan": self.plan.as_dict(),
            "results": [item.as_dict() for item in self.results],
            "rejected": list(self.rejected),
            "token_usage": self.token_usage,
            "produced_usable_evidence": self.produced_usable_evidence,
            "satisfies_gate": self.satisfies_gate,
        }


_FORCED_FINALIZATION_LIMITATION = "This worker was stopped at its turn deadline and wrote its result from the work it had completed by then; it may not have examined everything it intended to."

#: Every stage worker reads files through the sandbox's virtual paths, and a
#: path outside that prefix is refused rather than resolved. Saying so in the
#: prompt costs four lines; not saying it cost a whole design meeting, where
#: each participant reported "every file read was denied" and the chair had
#: nothing to synthesize from.
WORKSPACE_PATH_NOTE = """
Reading files:
- Every path you open must start with /mnt/user-data/ — that is how the project
  folder is mounted for you. A path outside it is refused, not resolved.
- The manifest in the context above already lists paths in that form. Use them
  as given rather than shortening them to project-relative paths.
- If a read is refused, report it in limitations; do not keep retrying the same
  path in a different shorthand.
""".strip()

RESULT_CONTRACT = """
Answer with a single JSON object and nothing else:

{
  "status": "completed" | "needs_input" | "blocked" | "failed",
  "summary": "what you did and what you found",
  "artifact_refs": ["workspace/output paths you created", {"name": "stable_alias", "path": "workspace/output path"}],
  "claims": ["each specific finding, one per entry"],
  "evidence_refs": [{"kind": "workspace_file" | "dataset" | "artifact" | "external",
                     "reference": "path or id", "description": "what it shows"}],
  "limitations": ["what this does not establish"],
  "quality_checks": [{"name": "check you ran on your own work", "passed": true, "detail": ""}],
  "recommended_next_actions": ["what a person should do next"],
  "clarification_question": "required only when status is needs_input; ask one focused question",
  "provenance": {"inputs_examined": [], "tools_used": []}
}

Every claim must be traceable to an entry in evidence_refs. If you could not
verify something, say so in limitations rather than asserting it. Report
status "blocked" when the data or the design prevents the work, not when you
merely found a negative answer. Use the exact field shapes above: artifact_refs
contains path strings or named-path objects, evidence_refs uses kind/reference,
and quality_checks uses a boolean passed field.
""".strip()


def build_prompt(spec: StageSpec, assignment: Assignment, *, context: str) -> str:
    """The task one worker receives.

    The stage's purpose, the worker's own angle, the project context, and the
    result contract — in that order, so the worker knows what it is contributing
    to before it is told how to format the answer.
    """
    lines = [
        f"You are contributing to the {spec.title} stage of a DBTL research cycle.",
        "",
        f"Stage purpose: {spec.purpose}",
        f"Your contribution: {capability_brief_for(assignment.capability)}",
        "",
        "Project context:",
        context.strip() or "(none supplied)",
        "",
        WORKSPACE_PATH_NOTE,
        "",
        "Constraints:",
        "- Do not modify any file declared as raw data. Write derived output to a separate path.",
        "- You cannot approve this stage. A person reviews your output before the cycle advances.",
        "- If two sources disagree, report the disagreement; do not pick a winner on your own.",
        "",
        RESULT_CONTRACT,
    ]
    if spec.stage == "build" and ({"recorded_rerun_procedure", "structured_rerun_spec"} & set(spec.validity_gates)):
        lines.extend(["", BUILD_FULFILLMENT_CONTRACT])
        lines.extend(
            [
                "",
                "Build evidence vocabulary:",
                "- Evidence kind says where the evidence lives, not what role the file plays.",
                "- Use workspace_file for manifests, logs, source code, tests, generated artifact directories, documentation, scripts, and input specifications under /mnt/user-data/.",
                "- Put labels such as manifest, execution_log, implementation, or test_suite in description instead of kind.",
                "",
                "Reproducibility (read this before judging your own result):",
                "- Record what someone else needs to re-run this: the exact command, any seed,",
                "  the input paths, and the interpreter or environment you used.",
                "- Demonstrating a second identical run is welcome but NOT required. If you could",
                "  not run it twice — or could not run it at all — say so in limitations and keep",
                "  status completed when the implementation and its record are otherwise sound.",
                "- Do not mark your own result failed for an unrepeated or unexecuted run.",
                "  Whether this work is reproducible is checked at the Test stage and decided by",
                "  a human reviewer; it is not yours to settle here.",
            ]
        )
        if "structured_rerun_spec" in spec.validity_gates:
            lines.extend(
                [
                    "- In provenance.rerun_spec return exactly this structured record:",
                    '  {"version": 1, "entry_point": "/mnt/user-data/...", "command": "exact command",',
                    '   "seed": "seed or empty", "inputs": ["/mnt/user-data/..."],',
                    '   "environment": {"runtime": "version or requirement"},',
                    '   "configuration": ["/mnt/user-data/..."], "expected_outputs": ["/mnt/user-data/..."]}.',
                    "- The command must run unchanged from a fresh working directory.",
                    "- Name absolute /mnt/user-data paths in this record. The record is the server's, and it is",
                    "  what lets the server re-run your work later; it is not a licence to embed those paths in code.",
                    "- Write each expected output into the current directory with the same filename it has in expected_outputs.",
                    "- recorded_rerun_procedure prose is legacy display data and does not satisfy this contract.",
                ]
            )
        if "granted_paths_only" in spec.validity_gates:
            lines.extend(
                [
                    "- Your code must not contain an absolute path to its data. The server supplies them:",
                    f"  {WORKSPACE_ENV} is the directory you write to, {PROJECT_ROOT_ENV} is the project root, and",
                    f"  {INPUT_ENV_PREFIX}1, {INPUT_ENV_PREFIX}2, ... are the server-issued candidate inputs in the order shown in the phase prompt ({INPUT_ENV_PREFIX}COUNT holds how many).",
                    "  provenance.phase_manifest.declared_inputs names the exact subset consumed and does not renumber those variables.",
                    "  Read them from the environment, or accept them as command-line arguments and pass them in the command.",
                    "- An entry point naming a location outside this workspace is refused before it is run, and the",
                    "  refusal names the literal and the line. This is not a style rule: a path you compose yourself is",
                    "  a guess about a filesystem you cannot see, and a wrong one costs the whole phase.",
                ]
            )
    if spec.stage == "test":
        lines.extend(["", DELIVERABLE_AUDIT_CONTRACT])
        lines.extend(
            [
                "",
                "Test outcome contract (required in addition to the shared result):",
                "- Put one object at provenance.validity_assessment.",
                "- metrics is a non-empty list of {name, value, threshold, criterion, plausible_max, unit}.",
                "- checks contains exactly the names in test_validity_contract.required_checks from Project context.",
                "- Each check is {check, status, detail, evidence_refs}; a passed check needs at least one evidence reference.",
                "- status is passed, failed, missing, or not_applicable. Use real JSON numbers and null, never numeric strings.",
                "- rationale explains the assessment; limitations lists non-gating caveats.",
                "- Do not put a route or overall outcome in this object. The server computes both from the pinned validity pack.",
                "",
                'provenance.validity_assessment shape: {"metrics": [], "checks": [], "limitations": [], "rationale": ""}',
                "",
                "Build bookkeeping observations may appear in the Build package's limitations and as a failed",
                "'build_manifest_bookkeeping' quality check. They record a manifest/output declaration desync the",
                "server already reconciled to the files it actually published — the science bytes are intact, so this",
                "is not a Build failure. For each observation, decide whether it could affect the result's validity.",
                "If it cannot, carry it forward as a non-gating limitation and do NOT fail any check or the stage",
                "over it. If it could, add the relevant check with a diagnostic detail and a recommended_next_action",
                "naming the exact Build correction to request; even then, do not fail a required validity check on the",
                "bookkeeping alone. Whether to accept despite an open observation is the human reviewer's decision.",
            ]
        )
    return "\n".join(lines)


def capability_brief_for(capability: str) -> str:
    """Look up a capability's brief by its string value, tolerating unknowns."""
    from deerflow.dbtl.capabilities import Capability

    try:
        return capability_brief(Capability(capability))
    except ValueError:
        return f"Contribute {capability.replace('_', ' ')} expertise."


def plan_stage(
    spec: StageSpec,
    candidates: Sequence,
    *,
    attempt_id: str,
    context: str = "",
) -> StageExecutionPlan:
    """Decide who does what, without dispatching anything."""
    selection = select_agents(spec, candidates)
    units = tuple(
        WorkUnit(
            unit_id=f"{attempt_id}-{index + 1}-{assignment.capability.value}",
            capability=assignment.capability.value,
            agent_name=assignment.agent_name,
            prompt=build_prompt(spec, assignment, context=context),
            via_generalist=assignment.via_generalist,
        )
        for index, assignment in enumerate(selection.assignments)
    )
    return StageExecutionPlan(spec=spec, selection=selection, units=units)


def collect_results(plan: StageExecutionPlan, outcomes: Sequence[DispatchOutcome]) -> StageExecutionOutcome:
    """Fold dispatch outcomes into validated, structured results.

    Every unit in the plan produces exactly one result. A unit with no outcome,
    an outcome carrying an error, and an outcome whose text does not satisfy the
    structured contract all become ``failed`` results carrying the reason —
    three different ways to end up with nothing usable, all of which the
    reviewer needs to be able to tell apart from success.
    """
    typed_units = [unit.unit_id for unit in plan.units if unit.output_contract != STAGE_WORKER_OUTPUT]
    if typed_units:
        raise ValueError(f"collect_results only accepts {STAGE_WORKER_OUTPUT!r} units; typed output must be handled by its owning parser: {', '.join(typed_units)}")

    by_unit = {item.unit_id: item for item in outcomes}
    results: list[StageWorkerResult] = []
    rejected: list[str] = []

    for unit in plan.units:
        outcome = by_unit.get(unit.unit_id)
        if outcome is None:
            results.append(failed_result(capability=unit.capability, agent_name=unit.agent_name, reason="The worker never reported a result."))
            continue
        if outcome.error or not outcome.text:
            failed = failed_result(
                capability=unit.capability,
                agent_name=unit.agent_name,
                reason=outcome.error or "The worker returned no output.",
                stop_reason=outcome.stop_reason,
            )
            results.append(replace(failed, token_usage=dict(outcome.token_usage or {})))
            continue
        if outcome.forced_finalization and plan.spec.stage == "build":
            rejected.append(f"{unit.unit_id}: {FORCED_BUILD_FINALIZATION_FAILURE}")
            failed = failed_result(
                capability=unit.capability,
                agent_name=unit.agent_name,
                reason=FORCED_BUILD_FINALIZATION_FAILURE,
            )
            results.append(replace(failed, token_usage=dict(outcome.token_usage or {})))
            continue
        try:
            payload = extract_result_payload(outcome.text)
            parsed = parse_worker_result(
                payload,
                capability=unit.capability,
                agent_name=unit.agent_name,
                stop_reason=outcome.stop_reason,
                stage=plan.spec.stage,
            )
            if outcome.forced_finalization:
                parsed = replace(
                    parsed,
                    limitations=(*parsed.limitations, _FORCED_FINALIZATION_LIMITATION),
                )
            results.append(replace(parsed, token_usage=dict(outcome.token_usage or {})))
        except WorkerResultRejected as exc:
            logger.info("dbtl stage worker %s returned an unusable result: %s", unit.unit_id, exc)
            rejected.append(f"{unit.unit_id}: {exc}")
            failed = failed_result(
                capability=unit.capability,
                agent_name=unit.agent_name,
                reason=worker_rejection_failure(outcome.stop_reason, str(exc)),
                stop_reason=outcome.stop_reason,
            )
            results.append(replace(failed, token_usage=dict(outcome.token_usage or {})))

    return StageExecutionOutcome(plan=plan, results=tuple(results), rejected=tuple(rejected))


async def arun_stage(
    spec: StageSpec,
    candidates: Sequence,
    dispatcher: AsyncWorkerDispatcher,
    *,
    attempt_id: str,
    context: str = "",
) -> StageExecutionOutcome:
    """Async production path for a stage's real ``SubagentExecutor`` fan-out."""
    plan = plan_stage(spec, candidates, attempt_id=attempt_id, context=context)
    if not plan.dispatchable:
        logger.info(
            "dbtl stage %s not dispatchable: %s",
            spec.spec_key,
            "; ".join(plan.selection.notes) or "no work units",
        )
        return StageExecutionOutcome(plan=plan)
    outcomes = await dispatcher(plan.units, budget=spec.budget)
    return collect_results(plan, outcomes)
