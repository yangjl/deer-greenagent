"""Own one governed Build from planning through its review deck.

``BuildStageCoordinator`` owns Build controls, planning, phased dispatch,
publication, summary, lineage, and review-surface orchestration. The adapter
validates project/cycle scope and the pinned stage contract, then delegates one
call here. The smaller helpers above the coordinator remain the pure vocabulary
and replay checks used by that flow.

Restore and resume live here too, and could not until recently. They re-check
that recorded bytes are still the bytes on disk, which meant reaching hashing
and intactness helpers the adapter had aliased privately — and a test patched
that alias to simulate an unreadable artifact. Moving them then would have
resolved the name here instead, leaving the patch rebinding something nobody
calls, and the tests green while testing nothing. Once those helpers moved to
``workspace``, where one patch on the owner intercepts every caller, the hazard
went with them.

What restore is *for*: a Build that already ran must be replayable from its
durable record without paying to run it again, and a record that no longer
matches the world it described must be refused rather than trusted. Every
function below returns ``None`` on any mismatch, which sends the phase back
through a real run. Silence is the safe answer here; a restored phase that is
subtly wrong is not.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import platform
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from deerflow.agents.dbtl.live_stage import token_usage as tokens
from deerflow.agents.dbtl.live_stage import workspace
from deerflow.agents.dbtl.live_stage.build_controls import DISABLED_GATE, BuildControlGate, BuildControlNotRecorded
from deerflow.agents.dbtl.live_stage.build_meeting import BUILD_WORK_MEETING_CONTRACT, MeetingContext, meeting_units, parse_recommendation
from deerflow.agents.dbtl.live_stage.build_phase_verification import BuildPhaseVerification, execute_and_verify_phase, resolve_issued_input_tokens
from deerflow.agents.dbtl.live_stage.build_phases import (
    GENERALIST,
    BuildPhaseManifest,
    PhaseAssignment,
    admit_capped_phase,
    assign_phase,
    is_capped_phase_salvageable,
    phase_completion_error,
    phase_correction_unit,
    phase_unit,
    plan_notes,
    planner_unit,
    reconcile_published_manifest,
    record_build_observation,
    required_phase_manifest_version,
    verify_granted_paths,
    verify_phase_manifest,
    verify_unpublished_phase_manifest,
)
from deerflow.agents.dbtl.live_stage.build_recorder import DISABLED_RECORDER, BuildStepRecorder, RecorderRequest, StepHandle, make_build_step_recorder
from deerflow.agents.dbtl.live_stage.build_review import MAX_RECENT_REVIEWER_FEEDBACK, execution_bundle, parse_summary, summarizer_unit, write_build_deck, write_build_review
from deerflow.agents.dbtl.live_stage.design_input import resolve_build_inputs
from deerflow.agents.dbtl.live_stage.feedback_surfaces import RenderedDeck
from deerflow.agents.dbtl.live_stage.test_stage import _write_evidence_exception_deck, _write_evidence_exception_package
from deerflow.agents.dbtl.live_stage.types import LiveStageResult
from deerflow.agents.dbtl.live_stage.workspace import SHELL_WORKSPACE_IDIOM, STAGE_UNIT_WORKSPACE_PLACEHOLDER, WORKSPACE_VIRTUAL_ROOT, safe_token, workspace_relative_path
from deerflow.dbtl.agent_selector import AgentCandidate, Assignment, SelectionResult
from deerflow.dbtl.build_control import (
    BuildControlAction,
    BuildControlAnswer,
    BuildControlKind,
    change_plan_request,
    execution_preflight_request,
    no_presentable_results_request,
    phase_pause_request,
    plan_confirmation_request,
    step_failure_request,
    worker_question_request,
)
from deerflow.dbtl.build_execution import BuildExecutionBundle, parse_rerun_spec
from deerflow.dbtl.build_fulfillment import BUILD_FULFILLMENT_CONTRACT, BuildFulfillment, derive_build_fulfillment
from deerflow.dbtl.build_input import BuildInputBundle, BuildInputError, restore_build_input_bundle
from deerflow.dbtl.build_plan import BuildPhasePlan, parse_build_plan, restore_build_plan, single_phase_plan
from deerflow.dbtl.build_summary import BuildReviewPackage
from deerflow.dbtl.build_workflow import BuildErrorCode, BuildStepKey, StepState, phase_output_digest, plan_output_digest, resolve_build_workflow
from deerflow.dbtl.council_deck import chair_result as _chair_result_of
from deerflow.dbtl.evidence_exception import EvidenceExceptionDossier, EvidenceReason, build_evidence_exception_dossier
from deerflow.dbtl.stage_meetings import REVIEW_MEETING_STAGES, MeetingRequirement, surface_meeting_gate
from deerflow.dbtl.stage_runner import RESULT_CONTRACT, WORKSPACE_PATH_NOTE, DispatchOutcome, StageExecutionOutcome, StageExecutionPlan, WorkUnit, arun_stage, collect_results
from deerflow.dbtl.stage_spec import StageSpec, WorkerBudget
from deerflow.dbtl.worker_result import StageWorkerResult, WorkerStatus, failed_result, parse_worker_result
from deerflow.runtime.activity.vocabulary import ActivityState

logger = logging.getLogger(__name__)


def _build_plan_display_summary(plan: BuildPhasePlan) -> str:
    """A bounded human view of the plan; the typed JSON remains audit data."""
    if not plan.phases:
        return plan.clarification_question or plan.rationale or "The Build plan needs input."
    label = "phase" if len(plan.phases) == 1 else "phases"
    lines = [f"Build plan ready · {len(plan.phases)} {label}"]
    lines.extend(f"{index}. {phase.title}" for index, phase in enumerate(plan.phases, start=1))
    return "\n".join(lines)[:1_600]


def _plan_execution(plan: BuildPhasePlan, *, degraded: bool) -> dict[str, Any]:
    """The recorded plan, flattened into the step row's bounded execution map.

    A queued phase has no row of its own yet, so without this the read model can
    say a Build has four phases and name none of them — which is the difference
    between a plan a person can check and a progress bar. Flat scalar keys are
    what `execution` accepts, and `MAX_BUILD_PHASES` (8) keeps the count inside
    its key cap by construction rather than by hoping.
    """
    execution: dict[str, Any] = {
        "feasibility": plan.feasibility.value,
        "phases": len(plan.phases),
        "degraded": degraded,
    }
    for index, phase in enumerate(plan.phases, start=1):
        execution[f"phase_{index}_key"] = phase.phase_key
        execution[f"phase_{index}_title"] = phase.title
    return execution


def _settled_control(answer: BuildControlAnswer) -> BuildControlAnswer:
    """Read a free-text answer to a plan card as what it actually is: a replan.

    The person typed what they wanted changed, so the *decision* is "draw the
    plan again", and recording it as a generic direct answer would leave the
    replan epoch unmoved — the committed plan would replay and their words would
    reach nothing. Their comment travels unchanged; only the verb is named
    honestly.
    """
    if answer.action is BuildControlAction.ANSWER_DIRECTLY and answer.kind in {BuildControlKind.PLAN_CONFIRMATION, BuildControlKind.PHASE_PAUSE}:
        return replace(answer, action=BuildControlAction.REPLAN_BUILD)
    return answer


def _control_context(answer: BuildControlAnswer, *, workflow_spec_key: str = "") -> dict[str, Any]:
    """The bindings a follow-up control inherits from the one being answered.

    The spec key comes from the caller rather than the reply: an answer carries
    no contract, and a record that cannot name the one it belongs to is not
    reviewable later.
    """
    return {
        "build_control_kind": answer.kind.value,
        "dbtl_cycle_id": answer.cycle_id,
        "stage_attempt_id": answer.stage_attempt_id,
        "workflow_spec_key": workflow_spec_key,
        "step_key": answer.step_key,
        "cycle_revision": answer.cycle_revision,
        "plan_digest": answer.plan_digest,
        "input_digest": answer.input_digest,
    }


@dataclass(frozen=True, slots=True)
class _PhaseRun:
    """What running a Build plan produced, and whether it produced all of it.

    `complete` is the field the caller must consult before treating any of this
    as reviewable Build evidence: earlier phases commit legitimately even when a
    later one fails or the plan asks to pause, so a truthful outcome full of
    trustworthy results is exactly what a half-finished plan looks like.
    """

    outcome: StageExecutionOutcome
    published: list[dict[str, Any]]
    complete: bool
    #: What every phase bound as an input, established phase by phase. Build
    #: lineage uses this instead of a single end-of-run recomputation, which
    #: cannot reconstruct what existed at each phase's own starting point.
    input_artifacts: list[str] = field(default_factory=list)
    stopped_because: str = ""
    #: A plan that stopped at its own boundary and one that stopped on a failure
    #: are both incomplete, and they are not the same thing to a person: one
    #: asks "carry on?", the other asks "what now?". Kept apart here so the
    #: caller does not have to read `stopped_because` to tell them apart.
    paused: bool = False
    paused_phase_title: str = ""
    completed_count: int = 0
    failure_code: BuildErrorCode | None = None
    #: A phase may pause on its own focused question. The card is recorded and
    #: carried out with the partial run rather than being rewritten as a generic
    #: execution failure after the worker has already stated what it needs.
    control_request: dict[str, Any] | None = None


def _phase_note(assignment: PhaseAssignment, result: StageWorkerResult) -> dict[str, Any]:
    """What a later phase is told about an earlier one."""
    return {
        "phase_key": assignment.phase.phase_key,
        "title": assignment.phase.title,
        "outputs": list(result.artifact_refs),
        "summary": result.summary,
    }


def _declared_deliverable_fulfillments(results: Sequence[StageWorkerResult]) -> list[Mapping[str, object]]:
    items: list[Mapping[str, object]] = []
    for result in results:
        raw = result.provenance.get("deliverable_fulfillment")
        raw_items = raw.get("items") if isinstance(raw, Mapping) else None
        if isinstance(raw_items, Sequence) and not isinstance(raw_items, (str, bytes)):
            items.extend(item for item in raw_items if isinstance(item, Mapping))
    return items


def _build_phase_context(
    stage_context: Mapping[str, Any],
    inputs: BuildInputBundle,
) -> str:
    """Return the one bounded context packet an implementation phase needs.

    The generic stage context also carries prior meeting turns, duplicate
    Design projections, review state, and Test-only policy. Sending all of it
    to every tool-loop turn made a small Build pay repeatedly for governance
    history it could not act on. The hash-bound input bundle is authoritative;
    one cycle summary and the two Build policies are enough orientation.

    The approved Design's **text** is dropped here for the same reason, and it
    is what was left of that cost: on a measured pilot the excerpt was 19,380
    characters, 92% of the bundle and roughly 45% of every one of a phase's four
    model calls -- for a document the planner had already decomposed into this
    phase's objective, inputs, outputs, and done-condition. The excerpt is
    described by its own module as "a convenience for the worker's first model
    call", which is true of the planner (one call, whose whole job is reading
    the Design) and false of a tool loop that re-sends it every turn.

    The *binding* stays: the phase is still told exactly which approved artifact
    it implements and the hash it was approved under, and may read it when it
    needs the wording. Dropping the pointer as well would restore the guessing
    the bundle exists to end, and none of this moves a digest -- the bundle's
    own identity already excludes the excerpt.
    """
    bundle = inputs.as_dict()
    bundle.pop("design_text", None)
    bundle.pop("design_truncated", None)
    bundle["design_text_note"] = "The approved Design is not inlined here. Read it at the design reference above if you need its exact wording; it is bound by the content hash recorded there."
    payload = {
        "cycle": stage_context.get("cycle"),
        "build_input_bundle": bundle,
        "input_provenance_policy": stage_context.get("input_provenance_policy"),
        "build_input_policy": stage_context.get("build_input_policy"),
        "workspace_root": stage_context.get("workspace_root"),
        "stage_workspace": stage_context.get("stage_workspace"),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _pause_note(assignment: PhaseAssignment) -> str:
    return f"Paused after {assignment.phase.title!r} because the plan asked for a look before the next phase."


def _stops_at_boundary(assignment: PhaseAssignment, *, index: int, total: int) -> bool:
    """Whether this phase's `pause_after` is a boundary anyone can stand at.

    A boundary stops *before the next phase*, so on the final phase there is
    nothing to stop before. Honouring it there raised a card offering
    "Continue — runs the remaining 0 phases": its only real option did nothing,
    and because the pause marks the plan incomplete, a Build that had run every
    planned phase wrote no review package and could never reach its human gate.
    A planner setting `pause_after` on every phase is not wrong, so this is read
    as the plan asking to be looked at wherever a look is still possible.

    Both the replay and fresh-execution paths ask through here, because a
    boundary honoured on one and skipped on the other would pause a plan that
    cannot then be resumed past it.
    """
    return assignment.phase.pause_after and index < total


@dataclass(frozen=True, slots=True)
class _BuildSummary:
    """What one `summarize_results` attempt produced.

    `text` is the summarizer's own answer, kept only so a later run can rebuild
    this package without paying for a second synthesis. It is scratch, never
    evidence: the document a person reviews is the Markdown written from the
    parsed package, and that is what the approval binds to.
    """

    package: BuildReviewPackage | None = None
    refusal: str = ""
    question: str = ""
    text: str = ""


@dataclass(frozen=True, slots=True)
class _RestoredSummary:
    """A committed write-up, proven to be the one the record describes."""

    package: BuildReviewPackage
    uri: str
    content_hash: str


def _restore_build_summary(payload: Any, *, bundle: BuildExecutionBundle, expected_digest: str, project_root: str) -> _RestoredSummary | None:
    """Rebuild a committed write-up, or `None` to run the summarizer again.

    A deck that failed to render must be retryable on its own — that is the
    whole reason the write-up is a separate step. Re-running the summarizer
    instead produced a *different* package (the review document embeds the cycle
    revision, so its hash moves), which the replayed step could no longer record:
    the deck then rendered one write-up while the chain named another.

    Two things are checked, because the payload is an ordinary file inside the
    project: the re-parsed package must recompute the digest recorded beside it,
    and the review document must still hash to what the attempt committed.
    """
    if not isinstance(payload, Mapping):
        return None
    uri = str(payload.get("artifact_uri") or "")
    recorded = str(payload.get("package_digest") or "")
    if not uri or not recorded or not expected_digest:
        return None
    parsed = parse_summary(str(payload.get("text") or ""), bundle=bundle)
    if not parsed.ok or parsed.package is None or parsed.package.digest != recorded:
        logger.warning("A recorded Build write-up did not match the package its attempt committed; the summarizer will run again.")
        return None
    resolved = workspace_relative_path(uri, project_root=project_root)
    if resolved is None:
        return None
    _relative, host = resolved
    try:
        if not host.is_file() or host.is_symlink() or workspace.sha256_file(host) != expected_digest:
            logger.warning("The recorded Build review document is no longer the one its attempt committed; the summarizer will run again.")
            return None
    except OSError:
        return None
    return _RestoredSummary(package=parsed.package, uri=uri, content_hash=expected_digest)


def _restored_build_plan(payload: Any, *, expected_digest: str, input_digest_value: str) -> BuildPhasePlan | None:
    """A committed plan read back, or `None` to draw it again.

    `restore_build_plan` answers "is this a plan?"; the recomputed digest
    answers "is this *the* plan this attempt committed, drawn from the Design
    this run resolved?". Scratch is an ordinary file inside the project, so a
    plan that merely parses is not evidence that these phases were the ones
    approved — and a swapped one would silently rebind every phase beneath it.
    """
    plan = restore_build_plan(payload)
    if plan is None:
        return None
    if not expected_digest or plan_output_digest(plan_digest=plan.digest, input_digest_value=input_digest_value) != expected_digest:
        logger.warning("A recorded Build plan did not match the digest its attempt committed; the plan will be drawn again.")
        return None
    return plan


def _restore_phase(
    payload: Any,
    *,
    assignment: PhaseAssignment,
    index: int,
    spec: StageSpec,
    expected_digest: str,
    project_root: str,
) -> tuple[WorkUnit, StageWorkerResult, list[dict[str, Any]], list[str]] | None:
    """Rebuild a phase that already committed, or `None` to run it again.

    A phase's committed row proves the work happened and its outputs are in the
    governed tree; this is what turns that proof into something the run can use
    instead of dispatching a second time. Everything about it is fail-soft — a
    payload that is missing, malformed, or no longer trustworthy costs one
    re-run, while accepting a damaged one would file work nobody did.

    **The payload is only ever accepted against the digest it was recorded
    under.** Scratch is an ordinary file in the project the person can edit, and
    a shape check answers "is this a phase result?" rather than "is this *the*
    phase result this row committed?" — so the digest is recomputed and the
    published bytes are re-hashed before any of it counts as work that happened.
    """
    if not isinstance(payload, Mapping):
        return None
    unit_id = str(payload.get("unit_id") or "")
    raw_result = payload.get("result")
    if not unit_id or not isinstance(raw_result, Mapping):
        return None
    published = [dict(item) for item in payload.get("published") or () if isinstance(item, Mapping)]
    if not published:
        return None
    # Older scratch payloads did not bind inputs. They cannot prove which bytes
    # a replayed phase read, so they are deliberately re-opened once rather than
    # being grandfathered into a provenance guarantee they never made.
    if "input_artifacts" not in payload:
        return None
    input_artifacts = [str(item) for item in payload.get("input_artifacts") or () if isinstance(item, str)]
    if not expected_digest or phase_output_digest(result=raw_result, published=published, input_artifacts=input_artifacts) != expected_digest:
        logger.warning("A recorded Build phase payload did not match the digest its attempt committed; the phase will run again.")
        return None
    if not workspace.input_artifacts_intact(input_artifacts, project_root=project_root):
        logger.warning("A recorded Build phase's inputs changed after it ran; the phase will run again.")
        return None
    if not workspace.published_bytes_intact(published, project_root=project_root):
        logger.warning("A recorded Build phase's published outputs are no longer what it published; the phase will run again.")
        return None
    try:
        result = parse_worker_result(
            raw_result,
            capability=assignment.phase.capability.value,
            agent_name=assignment.agent_name,
            stage="build",
        )
    except Exception:  # noqa: BLE001 - a payload this server wrote is still untrusted on the way back
        logger.warning("A recorded Build phase payload could not be read back; the phase will run again.", exc_info=True)
        return None
    if not result.is_trustworthy:
        return None
    unit = phase_unit(
        assignment,
        index=index,
        attempt_id="",
        attempt_token="",
        spec=spec,
        context="",
        result_contract="",
    )
    if phase_completion_error(result, unit.completion_check):
        logger.warning("A recorded Build phase no longer satisfies its pinned completion contract; the phase will run again.")
        return None
    if "server_verified_phase_manifest" in spec.validity_gates:
        _manifest, manifest_error = verify_phase_manifest(
            result,
            published=published,
            completion_condition=assignment.phase.done_condition,
            required_version=required_phase_manifest_version(spec),
        )
        if manifest_error:
            logger.warning("A recorded Build phase no longer satisfies its pinned manifest contract: %s", manifest_error)
            return None
    # The recorded bindings travel out too: Build lineage is assembled from what
    # each phase actually read, and a replayed phase that contributed nothing
    # would quietly drop its inputs from the provenance record.
    return replace(unit, unit_id=unit_id), result, published, input_artifacts


class BuildStageCoordinator:
    def __init__(self, owner):
        self._owner = owner

    def __getattr__(self, name):
        return getattr(self._owner, name)

    async def _plan_build(
        self, *, dispatcher: Callable[..., Any], budget: WorkerBudget, attempt_id: str, inputs: BuildInputBundle, cycle: Mapping[str, Any], candidates: Sequence[AgentCandidate], adjustment: str = "", answer: str = ""
    ) -> tuple[BuildPhasePlan, tuple[str, ...]]:
        'Ask for a decomposition; accept a single phase; never fail here.\n\n        Every failure degrades to a one-phase plan with a recorded note, because\n        losing the decomposition costs structure while failing here costs the\n        whole Build. The note is what keeps that honest: a reviewer reading a\n        one-phase Build can tell "it did not decompose" from "we could not read\n        the planner".\n'  # noqa: E501
        objective = str(cycle.get("objective") or cycle.get("research_question") or "")
        agent = next((item.name for item in candidates if item.name == GENERALIST), None) or (candidates[0].name if candidates else GENERALIST)
        context = json.dumps(
            {
                "cycle": {key: cycle.get(key) for key in ("id", "title", "research_question", "objective", "success_criteria")},
                "build_input_bundle": inputs.as_dict(),
                **({"owner_requested_changes": adjustment} if adjustment else {}),
                **({"previous_exchange_with_the_owner": answer} if answer else {}),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        unit = planner_unit(attempt_id=attempt_id, agent_name=agent, context=context)
        try:
            dispatched = await dispatcher((unit,), budget=budget)
        except Exception:
            logger.warning("The Build planner could not be dispatched.", exc_info=True)
            return (single_phase_plan(objective=objective, note="The build planner could not be run, so the build runs as one piece."), ("planner_unavailable",))
        text = str(getattr(dispatched[0], "text", "") or "") if dispatched else ""
        parsed = parse_build_plan(text, objective=objective)
        if parsed.degraded:
            logger.info("The Build plan degraded to a single phase: %s", "; ".join(parsed.reasons))
        return (parsed.plan, parsed.reasons)

    async def _execute_build_phases(
        self,
        *,
        plan: BuildPhasePlan,
        spec: StageSpec,
        dispatcher: Callable[..., Any],
        recorder: BuildStepRecorder,
        control_gate: BuildControlGate,
        attempt_id: str,
        stage_attempt_id: str,
        context: str,
        candidates: Sequence[AgentCandidate],
        project_root: str,
        cycle: Mapping[str, Any],
        datasets: Sequence[Mapping[str, Any]],
        pre_run_files: Mapping[str, tuple[int, int]],
        stage_workspace: str,
        answer: str = "",
        meeting_available: bool = False,
        boundaries_released: bool = False,
        user_id: str = "",
        sandbox_state: Any = None,
        enforce_server_execution: bool = False,
        thread_id: str = "",
    ) -> _PhaseRun:
        from deerflow.agents.dbtl.live_stage import adapter as _legacy

        _build_input_artifacts = _legacy._build_input_artifacts
        _declared_skill_bindings = _legacy._declared_skill_bindings
        _emit_build_cap_salvage_admitted = _legacy._emit_build_cap_salvage_admitted
        _emit_build_verification_failure = _legacy._emit_build_verification_failure
        _execute_server_build_command = _legacy._execute_server_build_command
        _extend_unique = _legacy._extend_unique
        _phase_granted_inputs = _legacy._phase_granted_inputs
        _publish_build_worker_artifacts = _legacy._publish_build_worker_artifacts
        _published_input_index = _legacy._published_input_index
        _published_source_text = _legacy._published_source_text
        'Run the plan\'s phases in order, each as its own attempt.\n\n        Sequential by design: one sandbox writer at a time is what makes the\n        workspace grant, the input snapshot, and the mutation checks tractable.\n        A phase that fails stops the run — later phases depend on outputs that\n        do not exist, and dispatching them anyway would spend budget producing\n        evidence nobody planned.\n\n        **A phase succeeds only once its outputs are in the governed tree.**\n        Publication used to happen once, after every phase had already been\n        recorded as succeeded from the worker\'s own JSON, so a phase naming a\n        file that was missing, escaped its workspace, or changed underneath it\n        left a *reusable success* in the chain — and a later resume would replay\n        it as work that had produced evidence. Validating and copying each\n        phase\'s bytes before settling its row makes the record say what actually\n        happened.\n\n        **Completion is reported, not inferred.** A failed phase, an uncovered\n        capability, and a `pause_after` boundary all stop the loop with earlier\n        phases legitimately committed, and the caller has to be able to tell\n        "the plan finished" from "some of it did".\n\n        **A boundary somebody already crossed is not a boundary.**\n        `boundaries_released` is durable, not request-scoped: it used to mean\n        only "this exact request carries a Continue", so a plan continued in one\n        turn and stopped by a later failure paused at the same finished phase on\n        every retry afterwards — the question re-asked forever and the cheap\n        summary/deck retry unreachable. A freshly run phase still stops at its\n        own boundary regardless, because that one has never been shown.\n        '  # noqa: E501
        assignments = [assign_phase(phase, candidates) for phase in plan.phases]
        selection = SelectionResult(
            assignments=tuple(Assignment(capability=item.phase.capability, agent_name=item.agent_name, via_generalist=item.via_generalist or not item.covered) for item in assignments), notes=plan_notes(plan, assignments)
        )
        units: list[WorkUnit] = []
        results: list[StageWorkerResult] = []
        rejected: list[str] = []
        completed: list[Mapping[str, Any]] = []
        published: list[dict[str, Any]] = []
        input_artifacts: list[str] = []
        stopped = ""
        paused = False
        paused_title = ""
        failure_code: BuildErrorCode | None = None
        control_request: dict[str, Any] | None = None
        declared_skill_names = tuple(name for assignment in assignments for name in assignment.phase.skills)
        try:
            skill_catalog = await _declared_skill_bindings(declared_skill_names, user_id=user_id, app_config=self._app_config)
        except Exception as exc:
            logger.warning("Build could not bind its declared skill catalog.", exc_info=True)
            return _PhaseRun(outcome=StageExecutionOutcome(plan=StageExecutionPlan(spec=spec, selection=selection), rejected=(str(exc),)), stopped=str(exc), failure_code=BuildErrorCode.EXECUTION_CONTRACT_REJECTED)
        for index, assignment in enumerate(assignments, start=1):
            if not assignment.covered:
                stopped = f"No registered agent can cover {assignment.phase.capability.value!r}, which phase {assignment.phase.title!r} asks for."
                rejected.append(stopped)
                failure_code = BuildErrorCode.PLAN_CAPABILITY_UNKNOWN
                break
            phase_skill_bindings = tuple(skill_catalog[name] for name in assignment.phase.skills)
            handle = await recorder.begin(
                BuildStepKey.EXECUTE_PHASES,
                phase_index=index,
                phase_key=assignment.phase.phase_key,
                plan_digest=plan.digest,
                capability=assignment.phase.capability.value,
                agent_name=assignment.agent_name,
                via_generalist=assignment.via_generalist,
                skill_bindings=phase_skill_bindings,
                execution={"title": assignment.phase.title},
            )
            restored = await asyncio.to_thread(_restore_phase, recorder.replay(handle), assignment=assignment, index=index, spec=spec, expected_digest=str(handle.output_digest or ""), project_root=project_root) if handle.replayed else None
            if restored is None and handle.replayed:
                handle = await recorder.reopen(handle)
            if restored is not None:
                (unit, result, phase_published, phase_inputs) = restored
                units.append(unit)
                results.append(result)
                published.extend(phase_published)
                _extend_unique(input_artifacts, phase_inputs)
                completed.append(_phase_note(assignment, result))
                if _stops_at_boundary(assignment, index=index, total=len(assignments)) and (not boundaries_released):
                    stopped = _pause_note(assignment)
                    rejected.append(stopped)
                    (paused, paused_title) = (True, assignment.phase.title)
                    break
                continue
            phase_context = context
            if answer:
                phase_context = "\n\n".join((context, "The previous attempt at this phase asked for human input. Carry this exchange verbatim into the retry:\n" + answer))
            granted_inputs = _phase_granted_inputs(datasets=datasets, prior_published=published, pre_run_files=pre_run_files, project_root=project_root)
            unit = phase_unit(
                assignment,
                index=index,
                attempt_id=attempt_id,
                attempt_token=safe_token(handle.step_run_id or f"{attempt_id}:{plan.digest}:{index}"),
                spec=spec,
                context=phase_context,
                completed=completed,
                result_contract=f"{RESULT_CONTRACT}\n\n{BUILD_FULFILLMENT_CONTRACT}",
                granted_inputs=granted_inputs,
            )
            units.append(unit)
            phase_plan = StageExecutionPlan(spec=spec, selection=selection, units=(unit,))
            try:
                dispatched = await dispatcher((unit,), budget=spec.budget)
            except Exception as exc:
                logger.warning("Build phase %s could not be dispatched.", assignment.phase.phase_key, exc_info=True)
                dispatched = []
                rejected.append(f"{unit.unit_id}: {exc}")
            phase_outcome = collect_results(phase_plan, dispatched)
            result = phase_outcome.results[0] if phase_outcome.results else None
            rejected.extend(phase_outcome.rejected)
            try:
                current_skill_catalog = await _declared_skill_bindings(assignment.phase.skills, user_id=user_id, app_config=self._app_config)
                current_skill_bindings = tuple(current_skill_catalog[name] for name in assignment.phase.skills)
                if current_skill_bindings != phase_skill_bindings:
                    raise ValueError("A declared Build skill changed while this phase was running; retry the phase against the new skill revision.")
            except Exception as exc:
                stopped = str(exc)
                results.append(failed_result(capability=assignment.phase.capability.value, agent_name=assignment.agent_name, reason=stopped))
                rejected.append(stopped)
                failure_code = BuildErrorCode.INPUT_CHANGED_DURING_EXECUTION
                await _emit_build_verification_failure(unit, stopped)
                await recorder.fail(handle, failure_code, stopped)
                break
            if result is not None and result.status is WorkerStatus.NEEDS_INPUT:
                question = str(result.clarification_question or "").strip()
                request = worker_question_request(
                    question=question,
                    rationale=result.summary,
                    step_key=BuildStepKey.EXECUTE_PHASES.value,
                    cycle_id=str(cycle.get("id") or ""),
                    stage_attempt_id=stage_attempt_id,
                    workflow_spec_key=recorder.spec_key,
                    cycle_revision=int(cycle.get("db_revision") or 0),
                    plan_digest=plan.digest,
                    input_digest=handle.input_digest,
                    step_run_id=str(handle.step_run_id or ""),
                    meeting_available=meeting_available,
                )
                request_id = control_gate.request_id_for(request)
                await recorder.settle(handle, state=StepState.NEEDS_INPUT, summary=question, human_input_request_id=request_id, execution={"phase_key": assignment.phase.phase_key})
                control_request = await control_gate.raise_control(request)
                results.append(result)
                stopped = question
                (paused, paused_title) = (True, assignment.phase.title)
                break
            salvaged_cap = ""

            async def dispatch_fresh_correction(failure: str, *, correct_live_terminal: bool = False) -> bool:
                nonlocal unit, result, phase_outcome
                if result is None or spec.version < 12 or unit.tool_contract.get("correction_attempt") or result.was_capped or salvaged_cap:
                    return False
                first_unit = unit
                first_result = result
                if correct_live_terminal:
                    await _emit_build_verification_failure(first_unit, failure)
                first_workspace = workspace.unit_stage_workspace(stage_workspace, first_unit.unit_id)
                correction = phase_correction_unit(
                    assignment,
                    index=index,
                    attempt_id=attempt_id,
                    attempt_token=safe_token(handle.step_run_id or f"{attempt_id}:{plan.digest}:{index}"),
                    spec=spec,
                    previous_workspace=first_workspace,
                    previous_result=first_result,
                    failure=failure,
                    result_contract=f"{RESULT_CONTRACT}\n\n{BUILD_FULFILLMENT_CONTRACT}",
                    granted_inputs=granted_inputs,
                )
                units[-1] = correction
                correction_plan = StageExecutionPlan(spec=spec, selection=selection, units=(correction,))
                try:
                    correction_dispatched = await dispatcher((correction,), budget=spec.budget)
                except Exception:
                    logger.warning("Build phase correction could not be dispatched.", exc_info=True)
                    correction_dispatched = []
                correction_outcome = collect_results(correction_plan, correction_dispatched)
                rejected.extend(entry for entry in correction_outcome.rejected if entry not in rejected)
                corrected = correction_outcome.results[0] if correction_outcome.results else None
                if corrected is not None and corrected.is_trustworthy:
                    corrected = replace(corrected, token_usage=tokens._merge_token_usage(first_result.token_usage, corrected.token_usage), provenance={**corrected.provenance, "correction_of": first_unit.unit_id})
                    correction_outcome = replace(correction_outcome, results=(corrected,))
                unit = correction
                result = corrected
                phase_outcome = correction_outcome
                return True

            if result is not None and "server_executed_entry_point" in spec.validity_gates and enforce_server_execution and is_capped_phase_salvageable(result, required_version=required_phase_manifest_version(spec)):
                (result, salvaged_cap) = admit_capped_phase(result)
                phase_outcome = replace(phase_outcome, results=(result,))
            completion_error = phase_completion_error(result, unit.completion_check) if result is not None else ""
            correction_eligible = bool(result is not None and completion_error and (spec.version >= 12) and (not unit.tool_contract.get("correction_attempt")) and (not result.was_capped) and (not salvaged_cap))
            if result is None or (not result.is_trustworthy and (not correction_eligible)):
                results.extend(phase_outcome.results)
                stopped = "; ".join(phase_outcome.rejected) or (str(result.summary).strip() if result is not None else "") or f"Phase {assignment.phase.title!r} returned no usable result."
                failure_code = BuildErrorCode.EXECUTION_CONTRACT_REJECTED
                await recorder.fail(handle, failure_code, stopped)
                break
            if correction_eligible:
                await dispatch_fresh_correction(completion_error)
                if result is None or not result.is_trustworthy:
                    results.extend(phase_outcome.results)
                    stopped = "; ".join(phase_outcome.rejected) or (str(result.summary).strip() if result is not None else "") or f"The fresh correction for phase {assignment.phase.title!r} returned no usable result."
                    failure_code = BuildErrorCode.EXECUTION_CONTRACT_REJECTED
                    await recorder.fail(handle, failure_code, stopped)
                    break
                completion_error = phase_completion_error(result, unit.completion_check)
            if completion_error:
                results.append(replace(failed_result(capability=result.capability, agent_name=result.agent_name, reason=completion_error), token_usage=result.token_usage))
                rejected.append(completion_error)
                stopped = completion_error
                failure_code = BuildErrorCode.EXECUTION_CONTRACT_REJECTED
                await recorder.fail(handle, failure_code, stopped)
                break
            server_verification: BuildPhaseVerification | None = None
            if "server_executed_entry_point" in spec.validity_gates and enforce_server_execution:

                async def verify_server_result(current_result: StageWorkerResult, current_unit: WorkUnit) -> tuple[StageWorkerResult, BuildPhaseVerification]:
                    (raw_manifest, raw_manifest_error) = verify_unpublished_phase_manifest(current_result, completion_condition=assignment.phase.done_condition, required_version=required_phase_manifest_version(spec))
                    unit_workspace = workspace.unit_stage_workspace(stage_workspace, current_unit.unit_id)
                    if raw_manifest is None:
                        return (current_result, BuildPhaseVerification(False, raw_manifest_error, ""))
                    raw_manifest = resolve_issued_input_tokens(raw_manifest, issued_inputs=granted_inputs)
                    current_result = replace(current_result, provenance={**current_result.provenance, "phase_manifest": raw_manifest.as_dict()})
                    phase_rerun = parse_rerun_spec((current_result.provenance or {}).get("rerun_spec"))
                    if phase_rerun is not None:
                        rerun_inputs = resolve_issued_input_tokens(replace(raw_manifest, declared_inputs=tuple(phase_rerun.inputs), execution_inputs=tuple(phase_rerun.inputs)), issued_inputs=granted_inputs).execution_inputs
                        if rerun_inputs != raw_manifest.execution_inputs:
                            return (
                                current_result,
                                BuildPhaseVerification(False, "The Build phase rerun_spec.inputs must exactly match phase_manifest.execution_inputs in runtime order; Build and Test cannot verify different input environments.", ""),
                            )
                    grant_error = verify_granted_paths(raw_manifest, read_source=functools.partial(_published_source_text, project_root=project_root), allowed_roots=())
                    if grant_error:
                        return (current_result, BuildPhaseVerification(False, grant_error, ""))
                    verification = await asyncio.to_thread(
                        execute_and_verify_phase,
                        raw_manifest,
                        project_root=project_root,
                        unit_workspace=unit_workspace,
                        execute=functools.partial(
                            _execute_server_build_command, sandbox_state=sandbox_state, writable_workspace=unit_workspace, thread_id=thread_id, user_id=user_id, project_id=str(cycle.get("project_id") or ""), project_root=project_root
                        ),
                        timeout_seconds=min(float(spec.budget.timeout_seconds), 300.0),
                        issued_inputs=granted_inputs,
                    )
                    return (current_result, verification)

                (result, server_verification) = await verify_server_result(result, unit)
                phase_outcome = replace(phase_outcome, results=(result,))
                if not server_verification.passed and await dispatch_fresh_correction(server_verification.reason, correct_live_terminal=True):
                    if result is None or not result.is_trustworthy:
                        results.extend(phase_outcome.results)
                        stopped = "; ".join(phase_outcome.rejected) or (str(result.summary).strip() if result is not None else "") or f"The fresh correction for phase {assignment.phase.title!r} returned no usable result."
                        failure_code = BuildErrorCode.EXECUTION_CONTRACT_REJECTED
                        await recorder.fail(handle, failure_code, stopped)
                        break
                    completion_error = phase_completion_error(result, unit.completion_check)
                    if completion_error:
                        results.append(replace(failed_result(capability=result.capability, agent_name=result.agent_name, reason=completion_error), token_usage=result.token_usage))
                        rejected.append(completion_error)
                        stopped = completion_error
                        failure_code = BuildErrorCode.EXECUTION_CONTRACT_REJECTED
                        await recorder.fail(handle, failure_code, stopped)
                        break
                    (result, server_verification) = await verify_server_result(result, unit)
                    phase_outcome = replace(phase_outcome, results=(result,))
                if not server_verification.passed:
                    stopped = server_verification.reason
                    rejected.append(stopped)
                    results.append(replace(failed_result(capability=result.capability, agent_name=result.agent_name, reason=stopped), token_usage=result.token_usage))
                    failure_code = BuildErrorCode.EXECUTION_CONTRACT_REJECTED
                    await _emit_build_verification_failure(unit, stopped)
                    await recorder.fail(handle, failure_code, stopped, execution={"server_phase_verification": server_verification.as_dict()})
                    break
                result = replace(result, provenance={**result.provenance, "server_phase_verification": server_verification.as_dict()})
                phase_outcome = replace(phase_outcome, results=(result,))
            (phase_outcome, phase_published) = await asyncio.to_thread(_publish_build_worker_artifacts, project_root=project_root, cycle=cycle, outcome=phase_outcome, stage_workspace=stage_workspace, attempt_id=attempt_id)
            result = phase_outcome.results[0] if phase_outcome.results else None
            rejected.extend(entry for entry in phase_outcome.rejected if entry not in rejected)
            if result is None or not result.is_trustworthy or (not phase_published):
                results.extend(phase_outcome.results)
                stopped = "; ".join(phase_outcome.rejected) or f"Phase {assignment.phase.title!r} produced no output the server could verify."
                failure_code = BuildErrorCode.EXECUTION_OUTPUT_MISSING
                await _emit_build_verification_failure(unit, stopped)
                await recorder.fail(handle, failure_code, stopped)
                break
            phase_manifest: BuildPhaseManifest | None = None
            if "server_verified_phase_manifest" in spec.validity_gates:
                (phase_manifest, manifest_error) = verify_phase_manifest(result, published=phase_published, completion_condition=assignment.phase.done_condition, required_version=required_phase_manifest_version(spec))
                if manifest_error:
                    reconciled = reconcile_published_manifest(result, published=phase_published, completion_condition=assignment.phase.done_condition, required_version=required_phase_manifest_version(spec))
                    if reconciled is None:
                        stopped = manifest_error
                        rejected.append(manifest_error)
                        results.append(replace(failed_result(capability=result.capability, agent_name=result.agent_name, reason=manifest_error), token_usage=result.token_usage))
                        failure_code = BuildErrorCode.EXECUTION_CONTRACT_REJECTED
                        await _emit_build_verification_failure(unit, stopped)
                        await recorder.fail(handle, failure_code, stopped)
                        break
                    phase_manifest = reconciled
                    result = record_build_observation(result, manifest_error)
                    phase_outcome = replace(phase_outcome, results=(result,))
            if phase_manifest is not None and "granted_paths_only" in spec.validity_gates:
                grant_error = await asyncio.to_thread(verify_granted_paths, phase_manifest, read_source=functools.partial(_published_source_text, project_root=project_root), allowed_roots=())
                if grant_error:
                    stopped = grant_error
                    rejected.append(grant_error)
                    results.append(replace(failed_result(capability=result.capability, agent_name=result.agent_name, reason=grant_error), token_usage=result.token_usage))
                    failure_code = BuildErrorCode.EXECUTION_CONTRACT_REJECTED
                    await _emit_build_verification_failure(unit, stopped)
                    await recorder.fail(handle, failure_code, stopped)
                    break
            try:
                phase_input_artifacts = _build_input_artifacts(
                    datasets=datasets,
                    results=(result,),
                    project_root=project_root,
                    pre_run_files=pre_run_files,
                    strict_workspace_inputs=True,
                    run_published=_published_input_index(published, project_root=project_root),
                    implementation_inputs=phase_manifest.declared_inputs if phase_manifest is not None and "narrow_implementation_inputs" in spec.validity_gates else None,
                )
            except ValueError as exc:
                stopped = str(exc)
                results.append(replace(failed_result(capability=result.capability, agent_name=result.agent_name, reason=stopped), token_usage=result.token_usage))
                failure_code = BuildErrorCode.INPUT_CHANGED_DURING_EXECUTION
                await _emit_build_verification_failure(unit, stopped)
                await recorder.fail(handle, failure_code, stopped)
                break
            results.append(result)
            published.extend(phase_published)
            _extend_unique(input_artifacts, phase_input_artifacts)
            if salvaged_cap:
                await _emit_build_cap_salvage_admitted(unit, result)
            await recorder.succeed(
                handle,
                phase_output_digest(result=result.as_dict(), published=phase_published, input_artifacts=phase_input_artifacts),
                execution={
                    "phase_key": assignment.phase.phase_key,
                    "outputs": len(phase_published),
                    "skill_binding_count": len(phase_skill_bindings),
                    **({"entry_point": phase_manifest.entry_point, "completion_condition_hash": hashlib.sha256(phase_manifest.completion_condition.encode("utf-8")).hexdigest()} if phase_manifest is not None else {}),
                    **({"server_phase_verification": server_verification.as_dict()} if server_verification is not None else {}),
                },
                payload={
                    "unit_id": unit.unit_id,
                    "result": result.as_dict(),
                    "published": phase_published,
                    "input_artifacts": phase_input_artifacts,
                    "skill_bindings": list(phase_skill_bindings),
                    **({"phase_manifest": phase_manifest.as_dict()} if phase_manifest is not None else {}),
                    **({"server_phase_verification": server_verification.as_dict()} if server_verification is not None else {}),
                },
            )
            completed.append(_phase_note(assignment, result))
            if _stops_at_boundary(assignment, index=index, total=len(assignments)):
                stopped = _pause_note(assignment)
                rejected.append(stopped)
                (paused, paused_title) = (True, assignment.phase.title)
                break
        return _PhaseRun(
            outcome=StageExecutionOutcome(plan=StageExecutionPlan(spec=spec, selection=selection, units=tuple(units)), results=tuple(results), rejected=tuple(rejected)),
            published=published,
            input_artifacts=input_artifacts,
            complete=len(completed) == len(plan.phases) and (not stopped),
            stopped_because=stopped,
            paused=paused,
            paused_phase_title=paused_title,
            completed_count=len(completed),
            failure_code=failure_code,
            control_request=control_request,
        )

    async def _run_build_work_meeting(self, *, dispatcher: Callable[..., Any], budget: WorkerBudget, attempt_id: str, context: MeetingContext, candidates: Sequence[AgentCandidate]) -> tuple[str, dict[str, Any]]:
        "Convene the meeting and return ``(briefing, record)``.\n\n        Never raises and never fails the Build. The meeting is advisory, so an\n        outage costs the advice — the person still has the question in front of\n        them and can answer it directly, which is the cheaper interaction this\n        meeting was an escalation from.\n"  # noqa: E501
        agent = next((item.name for item in candidates if item.name == GENERALIST), None) or (candidates[0].name if candidates else GENERALIST)
        units = meeting_units(attempt_id=attempt_id, agent_name=agent, model=self._council_model(), via_generalist=agent == GENERALIST, context=context)
        try:
            dispatched = await dispatcher(units, budget=budget)
        except Exception:
            logger.warning("The Build work meeting could not be dispatched.", exc_info=True)
            return ("", {"contract": BUILD_WORK_MEETING_CONTRACT, "refusal": "The meeting could not be run."})
        by_id = {str(getattr(item, "unit_id", "")): str(getattr(item, "text", "") or "") for item in dispatched}
        chair = next((unit for unit in units if unit.role == "chair"), None)
        recommendation = parse_recommendation(by_id.get(chair.unit_id, "") if chair is not None else "")
        record = {**recommendation.as_dict(), "question": context.question, "step_key": context.step_key, "seats": [{"unit_id": unit.unit_id, "role": unit.role, "reported": bool(by_id.get(unit.unit_id))} for unit in units]}
        return (recommendation.as_briefing() if recommendation.usable else "", record)

    async def _build_pause_control(
        self, phase_run: _PhaseRun | None, *, gate: BuildControlGate, cycle: Mapping[str, Any], plan: BuildPhasePlan | None, attempt: Mapping[str, Any] | None, workflow_spec_key: str, summary_refusal: str
    ) -> dict[str, Any] | None:
        'The control this Build\'s stopping point calls for, if any.\n\n        Three states, three different questions. A plan that stopped at its own\n        boundary asks whether to carry on. A plan that stopped on a failure asks\n        what to do about it. And a write-up that could not be produced asks the\n        same thing about a much cheaper step — which matters, because a person\n        told only "the summary failed" has no way to know their sandbox work is\n        still pinned and reusable.\n\n        Returns ``None`` when the Build finished, when it was never running this\n        workflow, or when the plan is still whole. Raising a control for a\n        successful Build would put a question in front of somebody who has an\n        answer already.\n'  # noqa: E501
        common = {"cycle_id": str(cycle.get("id") or ""), "stage_attempt_id": str((attempt or {}).get("id") or ""), "workflow_spec_key": workflow_spec_key, "cycle_revision": int(cycle.get("db_revision") or 0)}
        if phase_run is not None and (not phase_run.complete):
            if phase_run.control_request is not None:
                return phase_run.control_request
            if phase_run.paused and plan is not None:
                return await gate.raise_control(phase_pause_request(plan=plan, completed_phases=phase_run.completed_count, paused_phase_title=phase_run.paused_phase_title, **common))
            return await gate.raise_control(
                step_failure_request(
                    step_key=BuildStepKey.EXECUTE_PHASES.value,
                    step_label="Run the build",
                    error_code=phase_run.failure_code.value if phase_run.failure_code else BuildErrorCode.INTERNAL_ERROR.value,
                    error_summary=phase_run.stopped_because,
                    completed_phases=phase_run.completed_count,
                    plan_digest=str(getattr(plan, "digest", "") or ""),
                    plan=plan,
                    **common,
                )
            )
        if summary_refusal:
            return await gate.raise_control(
                step_failure_request(
                    step_key=BuildStepKey.SUMMARIZE_RESULTS.value,
                    step_label="Summarize results",
                    error_code=BuildErrorCode.SUMMARY_CONTRACT_REJECTED.value,
                    error_summary=summary_refusal,
                    completed_phases=phase_run.completed_count if phase_run is not None else 0,
                    plan_digest=str(getattr(plan, "digest", "") or ""),
                    plan=plan,
                    **common,
                )
            )
        return None

    async def _summarize_build(
        self, *, dispatcher: Callable[..., Any], budget: WorkerBudget, attempt_id: str, outcome: StageExecutionOutcome, inputs: BuildInputBundle | None, cycle: Mapping[str, Any], bundle: BuildExecutionBundle, answer: str = ""
    ) -> _BuildSummary:
        "Run the read-only summarizer over the verified execution bundle.\n\n        Never raises: the execution behind this is already committed and\n        hash-bound, so a provider outage or an unparseable answer must cost the\n        write-up rather than the Build.\n\n        The worker's raw answer travels back with the parsed package because it\n        is what a replay is rebuilt from — a later run re-parses it against the\n        same bundle rather than paying for a second synthesis, and the\n        recomputed package digest is what proves the two are the same write-up.\n"  # noqa: E501
        agent = next((assignment.agent_name for assignment in outcome.plan.selection.assignments), "general-purpose")
        reviewer_feedback: Sequence[Mapping[str, Any]] = ()
        feedback_reader = getattr(self._repo, "recent_project_stage_feedback", None)
        if callable(feedback_reader):
            try:
                reviewer_feedback = await feedback_reader(project_id=str(cycle.get("project_id") or ""), limit=MAX_RECENT_REVIEWER_FEEDBACK)
            except Exception:
                logger.warning("Recent project reviewer feedback could not be loaded.", exc_info=True)
        unit = summarizer_unit(attempt_id=attempt_id, agent_name=agent, bundle=bundle, inputs=inputs, cycle=cycle, reviewer_feedback=reviewer_feedback)
        if answer:
            unit = replace(unit, prompt=f"{unit.prompt}\n\n{answer}")
        try:
            dispatched = await dispatcher((unit,), budget=budget)
        except Exception:
            logger.warning("The Build summarizer could not be dispatched.", exc_info=True)
            return _BuildSummary(refusal="The Build summarizer could not be run.")
        text = str(getattr(dispatched[0], "text", "") or "") if dispatched else ""
        parsed = parse_summary(text, bundle=bundle)
        if parsed.needs_input:
            return _BuildSummary(question=parsed.clarification_question)
        if not parsed.ok:
            return _BuildSummary(refusal=parsed.refusal)
        return _BuildSummary(package=parsed.package, text=text)

    async def execute(self, *, stage_activity, project_id, cycle_id, cycle, attempt, request_text, state, config, runtime, project_root, run_id, user_id, execution_key, spec, build_control, reuse_recorded_test_evidence) -> LiveStageResult:
        from deerflow.agents.dbtl.live_stage import adapter as _legacy

        MISSING_STRUCTURED_RERUN_REASON = _legacy.MISSING_STRUCTURED_RERUN_REASON
        _approved_design_brief = _legacy._approved_design_brief
        _bind_stage_unit_workspaces = _legacy._bind_stage_unit_workspaces
        _build_input_artifacts = _legacy._build_input_artifacts
        _change_request = _legacy._change_request
        _compact_design_history = _legacy._compact_design_history
        _design_round = _legacy._design_round
        _failure_reasons = _legacy._failure_reasons
        _prepare_stage_workspace = _legacy._prepare_stage_workspace
        _project_file_snapshot = _legacy._project_file_snapshot
        _project_manifest = _legacy._project_manifest
        _publish_build_worker_artifacts = _legacy._publish_build_worker_artifacts
        _safe_token = _legacy._safe_token
        _settle_execution_step = _legacy._settle_execution_step
        _write_build_driver = _legacy._write_build_driver
        _write_council_deck = _legacy._write_council_deck
        _write_stage_package = _legacy._write_stage_package
        stage = "build"
        dbtl_config = getattr(self._app_config, "dbtl", None)
        degraded_evidence_enabled = bool(getattr(dbtl_config, "degraded_evidence_continuation", False))
        datasets = await self._repo.list_datasets(cycle_id, project_id=project_id)
        await self._repo.reconciliation_view(cycle_id, project_id=project_id)
        build_test = await self._repo.build_test_view(cycle_id, project_id=project_id)
        activity: list[dict[str, Any]] = []
        try:
            activity = await self._repo.list_activity(cycle_id, project_id=project_id)
        except Exception:
            logger.warning("Could not read cycle activity for the Build change-request context.", exc_info=True)
        change_request = _change_request(activity, stage=stage)
        design_round = _design_round(activity)
        attempt_id = f"dbtl-{_safe_token(execution_key)}"
        stage_workspace = None
        (stage_workspace, _) = await asyncio.to_thread(_prepare_stage_workspace, project_root, attempt_id=attempt_id, stage=stage)
        project_manifest = await asyncio.to_thread(_project_manifest, project_root)
        pre_run_files = await asyncio.to_thread(_project_file_snapshot, project_root)
        build_workflow_enabled = bool(getattr(dbtl_config, "build_workflow_steps", False))
        build_recorder = DISABLED_RECORDER
        build_inputs: BuildInputBundle | None = None
        control_gate = DISABLED_GATE
        plan_adjustment = change_request or ""
        worker_answer = ""
        worker_answer_step = ""
        if build_workflow_enabled:
            stage_attempt_row_id = str((attempt or {}).get("id") or "")
            control_gate = BuildControlGate(
                repo=self._repo, project_id=project_id, cycle_id=cycle_id, stage_attempt_id=stage_attempt_row_id, thread_id=str(runtime.get("thread_id") or ""), run_id=str(run_id or ""), responder_user_id=str(user_id or "")
            )
            if build_control is not None and build_control.stage_attempt_id == stage_attempt_row_id:
                settled = _settled_control(build_control)
                try:
                    answered_control = await control_gate.record_answer(settled)
                except BuildControlNotRecorded as refusal:
                    return LiveStageResult(stage=stage, cycle_id=cycle_id, note=str(refusal), control_request=await control_gate.reopen_card())
                if settled.action is BuildControlAction.START_MEETING:
                    question = str((answered_control or {}).get("question") or "")
                    design = _approved_design_brief(cycle) or {}
                    (briefing, record) = await self._run_build_work_meeting(
                        dispatcher=self._dispatcher or self._production_dispatcher(config=config, state=state, project_id=project_id, project_root=project_root, cycle_id=cycle_id, stage=stage, meeting=True),
                        budget=spec.budget,
                        attempt_id=attempt_id,
                        context=MeetingContext(
                            question=question,
                            step_key=settled.step_key,
                            cycle_title=str(cycle.get("title") or ""),
                            research_question=str(cycle.get("research_question") or ""),
                            objective=str(cycle.get("objective") or ""),
                            success_criteria=str(cycle.get("success_criteria") or ""),
                            design_uri=str(design.get("uri") or ""),
                            design_hash=str(design.get("content_hash") or ""),
                            workspace_note=WORKSPACE_PATH_NOTE,
                            manifest=project_manifest[:24],
                        ),
                        candidates=self._candidates(),
                    )
                    logger.info("Build work meeting for %s recorded outcome %s.", cycle_id, record.get("outcome"))
                    return LiveStageResult(
                        stage=stage,
                        cycle_id=cycle_id,
                        note="The build meeting is finished. It can advise, but the decision stays yours.",
                        control_request=await control_gate.raise_control(
                            worker_question_request(
                                question=question or "How should the build continue?",
                                rationale=briefing or "The meeting could not reach a usable recommendation, so answer directly.",
                                step_key=settled.step_key,
                                cycle_id=cycle_id,
                                stage_attempt_id=stage_attempt_row_id,
                                workflow_spec_key=resolve_build_workflow().spec_key,
                                cycle_revision=int(cycle.get("db_revision") or 0),
                                plan_digest=settled.plan_digest,
                                input_digest=f"meeting:{settled.request_id}",
                            )
                        ),
                    )
                if settled.action is BuildControlAction.HOLD:
                    return LiveStageResult(stage=stage, cycle_id=cycle_id, note="Holding here. Nothing was dispatched, and every finished part of this build stays recorded.")
                if settled.action is BuildControlAction.CHANGE_PLAN:
                    return LiveStageResult(
                        stage=stage,
                        cycle_id=cycle_id,
                        note="Tell me what to change and I will draw the plan again from your words.",
                        control_request=await control_gate.raise_control(
                            change_plan_request(previous=_control_context(build_control, workflow_spec_key=resolve_build_workflow().spec_key), remaining_only=settled.kind is BuildControlKind.PHASE_PAUSE)
                        ),
                    )
                if settled.action is BuildControlAction.REPLAN_BUILD and settled.comment:
                    plan_adjustment = settled.comment
                if settled.action is BuildControlAction.ANSWER_DIRECTLY:
                    asked = str((answered_control or {}).get("question") or "")
                    worker_answer = "\n\n".join(part for part in (f"You asked: {asked}" if asked else "", f"The project owner answered: {settled.comment}") if part)
                    worker_answer_step = settled.step_key
            preflight_error = self._build_execution_preflight_error(config=config, stage_workspace=stage_workspace, sandbox_state=state.get("sandbox"))
            if preflight_error:
                control = await control_gate.raise_control(
                    execution_preflight_request(cycle_id=cycle_id, stage_attempt_id=stage_attempt_row_id, workflow_spec_key=resolve_build_workflow().spec_key, cycle_revision=int(cycle.get("db_revision") or 0))
                )
                if stage_activity is not None:
                    await stage_activity.settle(ActivityState.PAUSED, operation="stage.preflight_failed")
                return LiveStageResult(stage=stage, cycle_id=cycle_id, note=preflight_error, control_request=control)
            build_recorder = await make_build_step_recorder(
                self._repo, RecorderRequest(enabled=True, project_id=project_id, cycle_id=cycle_id, stage_attempt_id=stage_attempt_row_id, parent_run_id=str(run_id), project_root=str(project_root))
            )
            load_design = await build_recorder.begin(BuildStepKey.LOAD_DESIGN)
            try:
                current_build_inputs = await asyncio.to_thread(resolve_build_inputs, cycle, project_root=project_root, datasets=datasets, manifest=project_manifest, policy={"stage_spec_key": spec.spec_key})
            except BuildInputError as refusal:
                await build_recorder.fail(load_design, refusal.code, refusal.summary)
                return LiveStageResult(stage=stage, cycle_id=cycle_id, note=f"{refusal.summary} No Build worker was dispatched and nothing was recorded as Build evidence.")
            if load_design.replayed:
                restored_inputs = restore_build_input_bundle(build_recorder.replay(load_design))
                if restored_inputs is not None and restored_inputs.digest == str(load_design.output_digest or ""):
                    build_inputs = restored_inputs
                else:
                    load_design = await build_recorder.reopen(load_design)
                    build_inputs = current_build_inputs
            else:
                build_inputs = current_build_inputs
            await build_recorder.succeed(load_design, build_inputs.digest, execution={"design_revision": build_inputs.design_revision, "design_truncated": build_inputs.design_truncated}, payload=build_inputs.as_dict())
        stage_context_payload = {
            "request": request_text,
            "cycle": {key: cycle.get(key) for key in ("id", "title", "cycle_class", "state", "research_question", "objective", "success_criteria")},
            "declared_datasets": datasets,
            "reconciliation": {"status": "not_required", "instruction": "Data Reconciliation is not a Build prerequisite. Missing dataset declarations or reconciliation matrix rows are not a blocker, limitation, or failed validity check."},
            "build_test": build_test,
            "input_provenance_policy": {
                "authority": "server_bound_build_lineage",
                "instruction": "Build binds the exact files it reads with server-computed content hashes, and Test verifies that durable Build lineage. For compatibility, a validity check named reconciled_inputs means bound input provenance; judge the Build lineage, not the existence of reconciliation rows. An older Build package may describe absent reconciliation as a limitation; that is historical worker commentary, not active policy.",  # noqa: E501
            },
            "test_validity_contract": None,
            "workspace_root": WORKSPACE_VIRTUAL_ROOT,
            "stage_workspace": {
                "path": STAGE_UNIT_WORKSPACE_PLACEHOLDER,
                "instruction": "Write every new implementation, derived output, and execution log under this exact directory. Do not write under outputs/dbtl; the stage adapter publishes validated review evidence there after your result passes its contract.",  # noqa: E501
                "shell_note": SHELL_WORKSPACE_IDIOM,
            }
            if stage_workspace
            else None,
            "project_workspace_manifest": project_manifest,
            "build_input_policy": {
                "instruction": "Read the data files needed to implement the approved design and list every exact workspace path in provenance.inputs_examined. The server will compute and record their hashes automatically. No dataset declaration or reconciliation matrix is required, and their absence must not be reported as a failure or limitation."  # noqa: E501
            },
            "prior_design_council_runs": _compact_design_history([]),
            "discovery_package": None,
            "approved_design_brief": _approved_design_brief(cycle),
            "build_input_bundle": build_inputs.as_dict() if build_inputs is not None else None,
            "human_change_request": change_request,
            "chair_question_answered": None,
            "human_answer": "" or None,
            "design_round": design_round,
        }
        stage_context = json.dumps(stage_context_payload, sort_keys=True, ensure_ascii=False)
        build_phase_context = _build_phase_context(stage_context_payload, build_inputs) if build_inputs is not None else stage_context
        base_dispatcher = self._dispatcher or self._production_dispatcher(config=config, state=state, project_id=project_id, project_root=project_root, cycle_id=cycle_id, stage=stage, meeting=False, stage_workspace=stage_workspace)

        async def dispatcher(units: Sequence[WorkUnit], *, budget: WorkerBudget) -> Sequence[DispatchOutcome]:
            return await base_dispatcher(_bind_stage_unit_workspaces(units, stage_workspace), budget=budget)  # noqa: F821

        build_plan: BuildPhasePlan | None = None
        phase_run: _PhaseRun | None = None
        if build_workflow_enabled and build_inputs is not None:
            plan_handle = await build_recorder.begin(BuildStepKey.PLAN_BUILD)
            design_output_digest = plan_handle.predecessor_digests[0] if plan_handle.predecessor_digests else build_inputs.digest
            build_plan = _restored_build_plan(build_recorder.replay(plan_handle), expected_digest=str(plan_handle.output_digest or ""), input_digest_value=design_output_digest)
            if build_plan is None:
                if plan_handle.replayed:
                    plan_handle = await build_recorder.reopen(plan_handle)
                (build_plan, plan_reasons) = await self._plan_build(
                    dispatcher=dispatcher,
                    budget=spec.budget,
                    attempt_id=attempt_id,
                    inputs=build_inputs,
                    cycle=cycle,
                    candidates=self._candidates(),
                    adjustment=plan_adjustment,
                    answer=worker_answer if worker_answer_step == BuildStepKey.PLAN_BUILD.value else "",
                )
                if not build_plan.dispatchable:
                    control = await control_gate.raise_control(
                        worker_question_request(
                            question=build_plan.clarification_question,
                            rationale="Answering this lets the plan be drawn; nothing has run yet.",
                            step_key=BuildStepKey.PLAN_BUILD.value,
                            cycle_id=cycle_id,
                            stage_attempt_id=str((attempt or {}).get("id") or ""),
                            workflow_spec_key=build_recorder.spec_key,
                            cycle_revision=int(cycle.get("db_revision") or 0),
                            input_digest=build_inputs.digest,
                            meeting_available=bool(getattr(dbtl_config, "build_work_meetings", False)),
                            assumptions=build_plan.assumptions,
                            open_questions=build_plan.open_questions,
                        )
                    )
                    await build_recorder.settle(plan_handle, state=StepState.NEEDS_INPUT, summary=build_plan.clarification_question, human_input_request_id=str(control.get("request_id") or ""))
                    if stage_activity is not None:
                        await stage_activity.settle(ActivityState.PAUSED, operation="stage.wait_human")
                    return LiveStageResult(stage=stage, cycle_id=cycle_id, note="The build planner needs one decision before any work starts.", control_request=control)
                await build_recorder.succeed(
                    plan_handle, plan_output_digest(plan_digest=build_plan.digest, input_digest_value=design_output_digest), execution=_plan_execution(build_plan, degraded=bool(plan_reasons)), payload=build_plan.as_dict()
                )
            if bool(getattr(dbtl_config, "build_plan_confirmation", False)) and build_plan.dispatchable and (not await control_gate.plan_is_confirmed(build_plan.digest)):
                control = await control_gate.raise_control(
                    plan_confirmation_request(
                        plan=build_plan,
                        cycle_id=cycle_id,
                        stage_attempt_id=str((attempt or {}).get("id") or ""),
                        workflow_spec_key=build_recorder.spec_key,
                        cycle_revision=int(cycle.get("db_revision") or 0),
                        input_digest=build_inputs.digest,
                    )
                )
                if stage_activity is not None:
                    await stage_activity.settle(ActivityState.PAUSED, operation="stage.wait_human")
                return LiveStageResult(stage=stage, cycle_id=cycle_id, note="Here is the plan for this build. Nothing has run yet.", control_request=control)
        if build_plan is not None:
            phase_run = await self._execute_build_phases(
                plan=build_plan,
                spec=spec,
                dispatcher=dispatcher,
                recorder=build_recorder,
                control_gate=control_gate,
                attempt_id=attempt_id,
                stage_attempt_id=str((attempt or {}).get("id") or ""),
                context=build_phase_context,
                candidates=self._candidates(),
                project_root=project_root,
                cycle=cycle,
                datasets=datasets,
                pre_run_files=pre_run_files,
                stage_workspace=stage_workspace,
                answer=worker_answer if worker_answer_step == BuildStepKey.EXECUTE_PHASES.value else "",
                meeting_available=bool(getattr(dbtl_config, "build_work_meetings", False)),
                boundaries_released=build_control is not None
                and build_control.stage_attempt_id == str((attempt or {}).get("id") or "")
                and (build_control.action in {BuildControlAction.CONTINUE_BUILD, BuildControlAction.RETRY_STEP})
                or await control_gate.boundary_released(build_plan.digest),
                user_id=str(user_id),
                sandbox_state=state.get("sandbox"),
                enforce_server_execution=self._dispatcher is None,
                thread_id=str(self._runtime(config).get("thread_id") or ""),
            )
            outcome = phase_run.outcome
        else:
            outcome = await arun_stage(spec, self._candidates(), dispatcher, attempt_id=attempt_id, context=stage_context)
        unit_result_pairs = list(zip(outcome.plan.units, outcome.results, strict=True))
        published_build_artifacts: list[dict[str, Any]] = []
        build_execution_record: BuildExecutionBundle | None = None
        build_fulfillment: BuildFulfillment | None = None
        build_fulfillment_refusal = ""
        if phase_run is not None:
            published_build_artifacts = phase_run.published
        elif stage_workspace:
            (outcome, published_build_artifacts) = await asyncio.to_thread(_publish_build_worker_artifacts, project_root=project_root, cycle=cycle, outcome=outcome, stage_workspace=stage_workspace, attempt_id=attempt_id)
            unit_result_pairs = list(zip(outcome.plan.units, outcome.results, strict=True))
        if build_inputs is not None and build_inputs.deliverable_manifest is not None:
            build_fulfillment = derive_build_fulfillment(build_inputs.deliverable_manifest, published=published_build_artifacts, declarations=_declared_deliverable_fulfillments(outcome.results))
            if not build_fulfillment.reviewable:
                build_fulfillment_refusal = "Build did not attempt every approved Design deliverable, so the result is not reviewable."
            for artifact in published_build_artifacts:
                source_path = str(artifact.get("source_path") or "")
                matched = next((item.id for item in build_inputs.deliverable_manifest.deliverables if source_path in item.expected_paths), None)
                if matched is not None:
                    artifact["deliverable_id"] = matched
        if published_build_artifacts:
            build_execution_record = replace(execution_bundle(outcome.trustworthy_results, published=published_build_artifacts), deliverable_fulfillment=build_fulfillment)
            if phase_run is not None and build_execution_record.rerun_spec is None:
                driver_spec = await asyncio.to_thread(_write_build_driver, project_root=project_root, cycle=cycle, results=list(phase_run.outcome.trustworthy_results), bound_inputs=list(phase_run.input_artifacts))
                if driver_spec is not None:
                    build_execution_record = replace(build_execution_record, rerun_spec=driver_spec, rerun_procedure=driver_spec.command)
        missing_structured_rerun = bool("structured_rerun_spec" in spec.validity_gates and (build_execution_record is None or build_execution_record.rerun_spec is None))
        stage_refusal = MISSING_STRUCTURED_RERUN_REASON if missing_structured_rerun else ""
        if build_workflow_enabled:
            await _settle_execution_step(
                build_recorder,
                await build_recorder.begin(BuildStepKey.EXECUTE_PHASES),
                published=published_build_artifacts,
                outcome=outcome,
                incomplete_because=phase_run.stopped_because if phase_run is not None and (not phase_run.complete) else stage_refusal,
            )
        results = [
            {
                **result.as_dict(),
                "unit_id": unit.unit_id,
                "via_generalist": unit.via_generalist,
                "execution": {"model": unit.model, "max_tokens": unit.max_tokens, "token_limit_enforced": spec.budget.token_limit_enforced, "reasoning": unit.reasoning},
                "counts_toward_stage_output": True,
            }
            for (unit, result) in unit_result_pairs
        ]
        artifact_uri = None
        artifact_hash = None
        artifact_type = None
        artifact_digest = ""
        if build_fulfillment_refusal:
            stage_refusal = build_fulfillment_refusal
        _deliverable_audit = None
        build_plan_incomplete = phase_run is not None and (not phase_run.complete) or missing_structured_rerun
        produced_usable_evidence = outcome.produced_usable_evidence and (not build_plan_incomplete) and (not build_fulfillment_refusal)
        summary_handle = await build_recorder.begin(BuildStepKey.SUMMARIZE_RESULTS) if build_workflow_enabled and (not build_plan_incomplete) else StepHandle(step=BuildStepKey.SUMMARIZE_RESULTS)
        build_summary_owns_evidence = build_workflow_enabled and produced_usable_evidence and bool(published_build_artifacts)
        build_package = None
        summary_refusal = ""
        summary_question = ""
        no_slide_results = False
        summary_payload: dict[str, Any] | None = None
        if produced_usable_evidence and (not build_summary_owns_evidence):
            (artifact_uri, artifact_hash, artifact_digest) = await asyncio.to_thread(
                _write_stage_package, project_root=project_root, cycle=cycle, outcome=outcome, idempotency_key=execution_key, council=None, deliverable_audit=_deliverable_audit
            )
            artifact_type = spec.required_artifact_types[0]
        if build_summary_owns_evidence:
            bundle = build_execution_record or replace(execution_bundle(outcome.trustworthy_results, published=published_build_artifacts), deliverable_fulfillment=build_fulfillment)
            restored_summary = (
                await asyncio.to_thread(_restore_build_summary, build_recorder.replay(summary_handle), bundle=bundle, expected_digest=str(summary_handle.output_digest or ""), project_root=project_root) if summary_handle.replayed else None
            )
            if restored_summary is not None:
                if restored_summary.package.has_slide_results:
                    build_package = restored_summary.package
                    (artifact_uri, artifact_hash, artifact_digest) = (restored_summary.uri, restored_summary.content_hash, restored_summary.package.headline)
                    artifact_type = spec.required_artifact_types[0]
                else:
                    no_slide_results = True
                    summary_refusal = "The Build finished, but it recorded no verified numeric outcomes or figures to present."
                    produced_usable_evidence = False
                    summary_handle = await build_recorder.reopen(summary_handle)
            else:
                if summary_handle.replayed:
                    summary_handle = await build_recorder.reopen(summary_handle)
                summary = await self._summarize_build(
                    dispatcher=dispatcher,
                    budget=spec.budget,
                    attempt_id=attempt_id,
                    outcome=outcome,
                    inputs=build_inputs,
                    cycle=cycle,
                    bundle=bundle,
                    answer=worker_answer if worker_answer_step == BuildStepKey.SUMMARIZE_RESULTS.value else "",
                )
                (build_package, summary_refusal, summary_question) = (summary.package, summary.refusal, summary.question)
                if build_package is not None and (not build_package.has_slide_results):
                    no_slide_results = True
                    summary_refusal = "The Build finished, but it recorded no verified numeric outcomes or figures to present."
                    build_package = None
                written = await asyncio.to_thread(write_build_review, project_root=project_root, cycle=cycle, package=build_package, execution=bundle) if build_package is not None else None
                if written is not None:
                    (artifact_uri, artifact_hash, artifact_digest) = (written.uri, written.content_hash, written.digest)
                    artifact_type = spec.required_artifact_types[0]
                    summary_payload = {"text": summary.text, "package_digest": build_package.digest, "artifact_uri": written.uri}
                else:
                    produced_usable_evidence = False
        summary_control: dict[str, Any] | None = None
        if build_workflow_enabled and summary_handle.recorded:
            if artifact_hash and build_summary_owns_evidence:
                await build_recorder.succeed(summary_handle, artifact_hash, execution={"artifact_uri": artifact_uri or ""}, payload=summary_payload)
            elif summary_question:
                summary_control = await control_gate.raise_control(
                    worker_question_request(
                        question=summary_question,
                        rationale="The build ran and its outputs are recorded. This decides how they are written up.",
                        step_key=BuildStepKey.SUMMARIZE_RESULTS.value,
                        cycle_id=cycle_id,
                        stage_attempt_id=str((attempt or {}).get("id") or ""),
                        workflow_spec_key=build_recorder.spec_key,
                        cycle_revision=int(cycle.get("db_revision") or 0),
                        plan_digest=str(getattr(build_plan, "digest", "") or ""),
                        meeting_available=bool(getattr(dbtl_config, "build_work_meetings", False)),
                    )
                )
                await build_recorder.settle(summary_handle, state=StepState.NEEDS_INPUT, summary=summary_question, human_input_request_id=str(summary_control.get("request_id") or ""))
            elif no_slide_results and (not degraded_evidence_enabled):
                summary_control = await control_gate.raise_control(
                    no_presentable_results_request(
                        cycle_id=cycle_id,
                        stage_attempt_id=str((attempt or {}).get("id") or ""),
                        workflow_spec_key=build_recorder.spec_key,
                        cycle_revision=int(cycle.get("db_revision") or 0),
                        plan_digest=str(getattr(build_plan, "digest", "") or ""),
                        completed_phases=phase_run.completed_count if phase_run is not None else 0,
                        plan=build_plan,
                    )
                )
                await build_recorder.settle(summary_handle, state=StepState.NEEDS_INPUT, summary=summary_refusal, human_input_request_id=str(summary_control.get("request_id") or ""))
            else:
                await build_recorder.fail(
                    summary_handle,
                    BuildErrorCode.SUMMARY_CONTRACT_REJECTED if build_package is None else BuildErrorCode.REVIEW_PACKAGE_WRITE_FAILED,
                    summary_refusal or "; ".join(_failure_reasons(results)) or "No Build worker returned a result that satisfied the stage contract.",
                )
        evidence_exception: EvidenceExceptionDossier | None = None
        evidence_exception_uri = ""
        evidence_exception_hash = ""
        if degraded_evidence_enabled and (not produced_usable_evidence) and (not summary_question):
            reasons: list[EvidenceReason] = []
            failed_checks: list[dict[str, object]] = []
            affected_deliverables: list[dict[str, object]] = []
            verified_facts: list[str] = []
            available_artifacts = [{"path": str(item.get("path") or item.get("source_path") or ""), "content_hash": str(item.get("content_hash") or "")} for item in published_build_artifacts if item.get("content_hash")]
            if artifact_uri and artifact_hash:
                available_artifacts.append({"path": artifact_uri, "content_hash": artifact_hash})
            if build_fulfillment is not None:
                affected_deliverables = [item.as_dict() for item in build_fulfillment.items if item.status.value != "delivered"]
                statuses = {str(item.get("status") or "") for item in affected_deliverables}
                if "not_attempted" in statuses:
                    reasons.append(EvidenceReason.DELIVERABLE_NOT_ATTEMPTED)
                if statuses - {"not_attempted", "not_applicable"}:
                    reasons.append(EvidenceReason.DELIVERABLE_ATTEMPT_FAILED)
            if missing_structured_rerun:
                reasons.append(EvidenceReason.RERUN_UNAVAILABLE)
                failed_checks.append({"check": "structured_rerun_spec", "status": "missing", "detail": stage_refusal})
            if no_slide_results:
                reasons.append(EvidenceReason.CORE_OUTPUT_MISSING)
                failed_checks.append({"check": "presentable_core_result", "status": "missing", "detail": summary_refusal})
            if outcome.produced_usable_evidence:
                verified_facts.append("At least one worker returned server-readable evidence.")
            if published_build_artifacts:
                verified_facts.append(f"The server published and hashed {len(published_build_artifacts)} Build artifact(s).")
            if not reasons:
                reasons.append(EvidenceReason.EXECUTION_ABSENT)
                failed_checks.append({"check": "stage_execution", "status": "missing", "detail": stage_refusal or "No trustworthy stage evidence was recorded."})
            evidence_exception = build_evidence_exception_dossier(
                stage=stage,
                stage_attempt_id=str((attempt or {}).get("id") or ""),
                reason_codes=reasons,
                verified_facts=verified_facts,
                untrusted_claims=[str(item.get("summary") or "") for item in results if str(item.get("summary") or "") and str(item.get("status") or "") not in {"completed", "needs_input"}],
                affected_deliverables=affected_deliverables,
                failed_checks=failed_checks,
                available_artifacts=available_artifacts,
                continuation_route=None,
            )
            if evidence_exception is not None:
                (evidence_exception_uri, evidence_exception_hash, exception_digest) = await asyncio.to_thread(_write_evidence_exception_package, project_root=project_root, cycle=cycle, dossier=evidence_exception)
                if not produced_usable_evidence:
                    artifact_uri = evidence_exception_uri
                    artifact_hash = evidence_exception_hash
                    artifact_digest = exception_digest
                    artifact_type = "evidence_exception"
        if stage_activity is not None:
            await stage_activity.update(state=ActivityState.RECORDING, operation="stage.record")
        await self._repo.record_worker_runs(
            cycle_id=cycle_id,
            project_id=project_id,
            stage=stage,
            stage_spec_key=spec.spec_key,
            results=results,
            actor_user_id=str(user_id),
            expected_db_revision=int(cycle["db_revision"]),
            idempotency_key=execution_key,
            artifact_type=artifact_type,
            artifact_uri=artifact_uri,
            artifact_content_hash=artifact_hash,
        )
        if evidence_exception is not None and produced_usable_evidence and evidence_exception_uri and evidence_exception_hash:
            current = await self._repo.get_cycle(cycle_id, project_id=project_id)
            if current is None:
                raise RuntimeError("Cycle disappeared before the evidence exception could be attached.")
            await self._repo.attach_artifact(
                cycle_id=cycle_id,
                project_id=project_id,
                stage=stage,
                artifact_type="evidence_exception",
                uri=evidence_exception_uri,
                content_hash=evidence_exception_hash,
                created_by=str(user_id),
                expected_db_revision=int(current["db_revision"]),
                idempotency_key=f"{execution_key}:evidence-exception",
            )
        if artifact_uri and artifact_hash and (build_execution_record is not None):
            current = await self._repo.get_cycle(cycle_id, project_id=project_id)
            if current is None:
                raise RuntimeError("Cycle disappeared after Build workers were recorded.")
            metadata = dict(config.get("metadata", {}) or {})
            supplied_code_revision = str(runtime.get("code_revision") or metadata.get("code_revision") or os.getenv("GIT_COMMIT") or "").strip()
            code_revision = supplied_code_revision or "workspace:unversioned"
            try:
                config_payload = self._app_config.model_dump(mode="json")
            except AttributeError:
                config_payload = repr(self._app_config)
            config_revision = "config:sha256:" + hashlib.sha256(json.dumps(config_payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            deviations = []
            if not supplied_code_revision:
                deviations.append("Runtime did not provide a source-control revision; recorded workspace:unversioned.")
            if phase_run is not None:
                input_artifacts = list(phase_run.input_artifacts)
            else:
                input_artifacts = await asyncio.to_thread(_build_input_artifacts, datasets=datasets, results=outcome.trustworthy_results, project_root=project_root, pre_run_files=pre_run_files)
            await self._repo.record_build_lineage(
                cycle_id=cycle_id,
                project_id=project_id,
                code_revision=code_revision,
                config_revision=config_revision,
                environment={"python": platform.python_version(), "implementation": platform.python_implementation(), "platform": platform.platform(), "executable": sys.executable, "stage_runner": "LiveStageAdapter"},
                rerun_spec=build_execution_record.rerun_spec.as_dict() if build_execution_record and build_execution_record.rerun_spec else None,
                input_artifacts=input_artifacts,
                output_artifacts=published_build_artifacts,
                deviations=deviations,
                logs_uri=artifact_uri,
                recorded_by=str(user_id),
                expected_db_revision=int(current["db_revision"]),
                idempotency_key=f"{execution_key}:lineage",
            )
        deck_uri = None
        deck = None
        surface_plan = None
        deck_registered = False
        registration_error: Exception | None = None
        stage_has_reviewable_evidence = stage in REVIEW_MEETING_STAGES and (produced_usable_evidence or evidence_exception is not None) and bool(artifact_uri and artifact_hash)
        review_meeting_requirement = None
        if stage_has_reviewable_evidence:
            transition_gate = None
            if evidence_exception is not None:
                exception_payload = evidence_exception.as_dict()
                transition_gate = {"stage": stage, "assessment": {"difficulty": "exception", "rationale": "The server could not establish the clean evidence contract."}, "routes": [], "evidence_exception": exception_payload}
                review_meeting_requirement = MeetingRequirement.SKIPPED.value
            elif bool(getattr(getattr(self._app_config, "dbtl", None), "progressive_gate", False)):
                assessment = await self._assess_transition(stage=stage, cycle=cycle, evidence_summary=artifact_digest or f"{stage.title()} evidence: {artifact_uri} ({artifact_hash})")
                transition_gate = {"stage": stage, "assessment": assessment.as_dict(), "routes": []}
                meetings = getattr(getattr(self._app_config, "dbtl", None), "stage_meetings", None)
                enabled = bool(getattr(meetings, stage, False))
                gate = surface_meeting_gate(stage=stage, assessed_difficulty=assessment.difficulty.value, enabled=enabled)
                review_meeting_requirement = gate.requirement.value if gate is not None else MeetingRequirement.SKIPPED.value
            surface_plan = await self._plan_feedback_surface(
                stage=stage,
                cycle_id=cycle_id,
                project_id=project_id,
                execution_key=execution_key,
                round_number=design_round,
                originating_thread_id=str(runtime.get("thread_id") or ""),
                paused=False,
                artifact_uri=artifact_uri or "",
                artifact_hash=artifact_hash or "",
                decision_request=None,
                chair_worker_run_id=None,
                review_issue_ids=(),
                transition_gate=transition_gate,
            )
            if evidence_exception is not None and (not produced_usable_evidence):
                deck = await asyncio.to_thread(
                    _write_evidence_exception_deck,
                    project_root=project_root,
                    cycle=cycle,
                    dossier=evidence_exception,
                    package_path=artifact_uri or "",
                    surface_id=surface_plan.surface_id if surface_plan is not None and surface_plan.answerable else "",
                    transition_gate=transition_gate or {},
                )
            elif build_package is not None:
                rendered = await asyncio.to_thread(
                    write_build_deck,
                    project_root=project_root,
                    cycle=cycle,
                    package=build_package,
                    package_path=artifact_uri or "",
                    surface_id=surface_plan.surface_id if surface_plan is not None and surface_plan.answerable else "",
                    transition_gate=transition_gate,
                )
                deck = RenderedDeck(uri=rendered[0], content_hash=rendered[1], commentable_slides=rendered[2]) if rendered is not None else None
            else:
                deck = await asyncio.to_thread(
                    _write_council_deck,
                    project_root=project_root,
                    cycle=cycle,
                    results=results,
                    round_number=design_round,
                    stage=stage,
                    package_path=artifact_uri or "",
                    clarification_question="",
                    decision_request=None,
                    surface_id=surface_plan.surface_id if surface_plan is not None and surface_plan.answerable else "",
                    surface_mode=surface_plan.mode if surface_plan is not None else "",
                    transition_gate=transition_gate,
                )
            if deck is not None:
                deck_uri = deck.uri
                if surface_plan is not None:
                    try:
                        await self._register_feedback_surface(surface_plan, deck, cycle_id=cycle_id, project_id=project_id)
                        deck_registered = True
                    except Exception as exc:
                        registration_error = exc
        if build_workflow_enabled and artifact_uri and artifact_hash and (evidence_exception is None):
            deck_handle = await build_recorder.begin(BuildStepKey.RENDER_REVIEW_DECK)
            if deck is None or not deck.content_hash:
                await build_recorder.fail(deck_handle, BuildErrorCode.DECK_RENDER_FAILED, "The Build review deck could not be rendered from the recorded review package.")
            elif not deck_registered:
                await build_recorder.fail(
                    deck_handle,
                    BuildErrorCode.DECK_REGISTRATION_FAILED,
                    str(registration_error) if registration_error is not None else "The Build review deck was rendered but could not be bound to a stage attempt, so it cannot carry a decision.",
                )
            else:
                await build_recorder.succeed(deck_handle, deck.content_hash, execution={"deck_uri": deck.uri})
        if registration_error is not None:
            raise registration_error
        if build_plan_incomplete and phase_run is not None:
            done = sum(1 for item in phase_run.outcome.results if item.is_trustworthy)
            note = "\n".join(
                [
                    f"Ran {done} of {(len(build_plan.phases) if build_plan else done)} planned build phase(s) and kept every finished phase's outputs, so a retry resumes rather than starting over.",
                    f"It stopped there: {phase_run.stopped_because}" if phase_run.stopped_because else "",
                    "No review package was written, because a plan that has not finished is not the build a person would be approving.",
                ]
            ).strip()
        elif build_plan_incomplete and stage_refusal:
            note = "\n".join([f"Ran {len(results)} bounded {stage} worker(s) and kept every outcome, but the stage could not be completed.", "", stage_refusal])
        elif artifact_uri:
            note = artifact_digest or f"Ran {len(results)} bounded {stage} worker(s) and attached a review package at {artifact_uri}."
        elif stage_refusal:
            note = "\n".join([f"Ran {len(results)} bounded {stage} worker(s) and recorded every outcome, but the stage could not create review evidence.", "", stage_refusal])
        else:
            note = "\n".join(
                [
                    f"Ran {len(results)} bounded {stage} worker(s) and recorded every outcome, but none produced usable evidence, so no review artifact was attached.",
                    *(["", "Why each worker did not count:", *_failure_reasons(results)] if results else []),
                ]
            )
        control_request = (
            summary_control
            or await self._build_pause_control(phase_run, gate=control_gate, cycle=cycle, plan=build_plan, attempt=attempt, workflow_spec_key=build_recorder.spec_key, summary_refusal=summary_refusal if build_workflow_enabled else "")
            if build_workflow_enabled
            else None
        )
        if stage_activity is not None:
            if control_request is not None:
                await stage_activity.settle(ActivityState.PAUSED, operation="stage.wait_human")
            elif build_plan_incomplete or not produced_usable_evidence:
                await stage_activity.settle(ActivityState.FAILED)
        _summary_chair = _chair_result_of(results) or {}
        return LiveStageResult(
            stage=stage,
            cycle_id=cycle_id,
            note=note,
            worker_count=len(results),
            produced_usable_evidence=produced_usable_evidence,
            artifact_uri=artifact_uri,
            clarification_question=None,
            deck_uri=deck_uri,
            feedback_surface_id=surface_plan.surface_id if surface_plan is not None and deck is not None else None,
            test_assessment=None,
            review_meeting_requirement=review_meeting_requirement,
            control_request=control_request,
            research_question=str(cycle.get("research_question") or ""),
            chair_summary=str(_summary_chair.get("summary") or ""),
            chair_consensus=_summary_chair.get("consensus"),
        )
