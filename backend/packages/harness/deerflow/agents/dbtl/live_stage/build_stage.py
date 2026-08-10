"""How one Build run is described, paused, and handed to the next phase.

Extracted from ``adapter.py`` unchanged. This is the vocabulary a Build is
reasoned about in — the bounded ``execution`` map a plan row carries, a settled
control answer, the note a phase leaves behind, whether a phase stops at a
declared boundary — and every function here is a pure function of values
already in hand. Nothing reads a repository, starts a worker, or writes a
durable row; the adapter still owns all three.

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

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from deerflow.agents.dbtl.live_stage import workspace
from deerflow.agents.dbtl.live_stage.build_phases import (
    PhaseAssignment,
    phase_completion_error,
    phase_unit,
    required_phase_manifest_version,
    verify_phase_manifest,
)
from deerflow.agents.dbtl.live_stage.build_review import parse_summary
from deerflow.agents.dbtl.live_stage.workspace import workspace_relative_path
from deerflow.dbtl.build_control import BuildControlAction, BuildControlAnswer, BuildControlKind
from deerflow.dbtl.build_execution import BuildExecutionBundle
from deerflow.dbtl.build_input import BuildInputBundle
from deerflow.dbtl.build_plan import BuildPhasePlan, restore_build_plan
from deerflow.dbtl.build_summary import BuildReviewPackage
from deerflow.dbtl.build_workflow import BuildErrorCode, phase_output_digest, plan_output_digest
from deerflow.dbtl.stage_runner import StageExecutionOutcome, WorkUnit
from deerflow.dbtl.stage_spec import StageSpec
from deerflow.dbtl.worker_result import StageWorkerResult, parse_worker_result

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
