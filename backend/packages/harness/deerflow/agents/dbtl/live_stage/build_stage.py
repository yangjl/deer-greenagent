"""How one Build run is described, paused, and handed to the next phase.

Extracted from ``adapter.py`` unchanged. This is the vocabulary a Build is
reasoned about in — the bounded ``execution`` map a plan row carries, a settled
control answer, the note a phase leaves behind, whether a phase stops at a
declared boundary — and every function here is a pure function of values
already in hand. Nothing reads a repository, starts a worker, or writes a
durable row; the adapter still owns all three.

Restore and resume deliberately did **not** come with them. ``_restore_phase``
and ``_restore_build_summary`` reach ``_sha256_file`` and the artifact
intactness helpers, and ``_sha256_file`` is a ``monkeypatch.setattr`` target on
the adapter module guarding the tampered-or-unreadable-file path. Moving those
would resolve the name in this module instead, leaving the patch rebinding a
name nobody calls — the tests would stay green while no longer testing the
thing they exist for. They move when their own dependencies do, not before.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from deerflow.agents.dbtl.live_stage.build_phases import PhaseAssignment
from deerflow.dbtl.build_control import BuildControlAction, BuildControlAnswer, BuildControlKind
from deerflow.dbtl.build_input import BuildInputBundle
from deerflow.dbtl.build_plan import BuildPhasePlan
from deerflow.dbtl.build_workflow import BuildErrorCode
from deerflow.dbtl.stage_runner import StageExecutionOutcome
from deerflow.dbtl.worker_result import StageWorkerResult


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
