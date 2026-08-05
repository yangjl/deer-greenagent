"""The typed control a person answers while a Build is in progress.

A Build pauses for four different reasons and they are not variations on a
theme: the plan is drawn and nobody has agreed to it yet; a phase declared a
boundary and stopped there; a step failed; or a worker cannot safely continue
without one human answer. Each needs a different question, different options,
and a different consequence — but all four must bind to the *same* things, or
the answer cannot be trusted: which cycle, which stage attempt, which workflow
step, which plan, and which input digest the paused work was computed from.

This module owns that shape and nothing else. It has no repository, no message
type, and no knowledge of how a card is rendered, so the rules below hold
wherever a control is raised.

**A control offers only what the server put on it.** `resolve_answer` matches a
reply against the options of the request the server itself emitted, so a client
that invents an option id selects nothing. This is the same rule the Design deck
and the stage-handoff card follow, and for the same reason: the reply is the one
part of the exchange an untrusted client authors.

**Nothing is preselected, including a recommendation.** A default that becomes
the answer is a decision nobody made. `recommended_option_id` is a label, never
a fallback — `resolve_answer` does not consult it.

**A hold is a decision.** It is a first-class action rather than the absence of
one, because "the person chose to stop here" and "nobody has answered yet" lead
to opposite behaviour: one leaves the conversation alone, the other keeps the
control in front of them.

The four kinds deliberately share one action vocabulary. A retry card and a
pause card both offer "change the plan", and giving each its own verb would
mean two code paths that must stay in agreement about what changing a plan
does.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

#: A control with more choices than this is a form, and a form is the wrong
#: shape for an interruption: it asks someone mid-experiment to read rather
#: than decide.
MAX_CONTROL_OPTIONS = 5
MAX_TEXT_CHARS = 1_200
MAX_LABEL_CHARS = 80
MAX_PLAN_ROWS = 8

_OPTION_ID = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class BuildControlKind(StrEnum):
    """Why the Build stopped. Four reasons, four different questions."""

    #: The plan exists and nothing has run. The cheapest place to intervene.
    PLAN_CONFIRMATION = "plan_confirmation"
    #: A phase declared `pause_after` and finished. Everything before it is
    #: committed; nothing holds a worker lease.
    PHASE_PAUSE = "phase_pause"
    #: A step failed. Predecessors stay selected and reusable.
    STEP_FAILURE = "step_failure"
    #: A worker returned `needs_input`: one focused question it cannot answer.
    WORKER_QUESTION = "worker_question"


class BuildControlAction(StrEnum):
    """What answering does. One vocabulary across all four kinds."""

    START_BUILD = "start_build"
    CONTINUE_BUILD = "continue_build"
    CHANGE_PLAN = "change_plan"
    RETRY_STEP = "retry_step"
    REPLAN_BUILD = "replan_build"
    RESTART_BUILD = "restart_build"
    ANSWER_DIRECTLY = "answer_directly"
    START_MEETING = "start_meeting"
    HOLD = "hold_here"

    @property
    def dispatches(self) -> bool:
        """Does choosing this start work?

        `change_plan` and `start_meeting` are deliberately excluded: both are
        answered with another exchange first, and treating either as a dispatch
        would run the build the person is still describing.
        """
        return self in _DISPATCHING

    @property
    def discards_work(self) -> bool:
        """Does choosing this throw away committed phases?

        Surfaced so a card can *say so* rather than leaving a reviewer to infer
        it. Replan is the honest answer to a decomposition that was wrong, and
        it is only honest if its cost is stated.
        """
        return self in {BuildControlAction.REPLAN_BUILD, BuildControlAction.RESTART_BUILD}


_DISPATCHING = frozenset(
    {
        BuildControlAction.START_BUILD,
        BuildControlAction.CONTINUE_BUILD,
        BuildControlAction.RETRY_STEP,
        BuildControlAction.REPLAN_BUILD,
        BuildControlAction.RESTART_BUILD,
        BuildControlAction.ANSWER_DIRECTLY,
    }
)


def _text(value: Any, *, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    trimmed = value.strip()
    return trimmed if len(trimmed) <= limit else f"{trimmed[: limit - 1]}…"


@dataclass(frozen=True, slots=True)
class BuildControlOption:
    """One choice, and what choosing it means.

    `consequence` is required rather than decorative: a card that lists
    "Replan the build" beside "Retry" without saying that one discards finished
    phases is offering a choice nobody can make well.
    """

    id: str
    label: str
    action: BuildControlAction
    consequence: str = ""

    def __post_init__(self) -> None:
        if not _OPTION_ID.match(self.id or ""):
            raise ValueError(f"{self.id!r} is not a usable option id.")
        if not (self.label or "").strip():
            raise ValueError(f"Option {self.id!r} has no label.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": _text(self.label, limit=MAX_LABEL_CHARS),
            # `value` is what every other DBTL card calls the machine-readable
            # answer, and a client reads them all the same way.
            "value": self.action.value,
            "description": _text(self.consequence, limit=400),
        }


@dataclass(frozen=True, slots=True)
class BuildControlRequest:
    """One paused Build, as a thing a person can answer.

    The bindings are the point. A reply that cannot name the cycle revision,
    stage attempt, step, and plan it belongs to is not an answer to *this*
    pause, and replaying it against a Build that has since moved is exactly the
    failure the digests exist to prevent.
    """

    kind: BuildControlKind
    question: str
    cycle_id: str
    stage_attempt_id: str
    workflow_spec_key: str
    step_key: str
    cycle_revision: int = 0
    rationale: str = ""
    plan_digest: str = ""
    input_digest: str = ""
    step_run_id: str = ""
    options: tuple[BuildControlOption, ...] = ()
    #: Rows a card renders so the person is deciding about work they can see.
    plan_rows: tuple[Mapping[str, Any], ...] = ()
    assumptions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    #: A label, never a fallback. `resolve_answer` does not read it.
    recommended_option_id: str = ""
    #: True when free text is the answer rather than a choice.
    free_text: bool = False
    error_code: str = ""
    #: The durable id of the card this becomes. Deterministic, so a retried turn
    #: re-renders the same control instead of opening a second one for the same
    #: question — and so the durable record and the card in thread history name
    #: the same exchange.
    request_id: str = ""
    #: The durable collaboration row this card was emitted from. The request id
    #: recurs by design — the same pause re-derives it — so the row id is what
    #: distinguishes *this emission* from the last one, and it is what an answer
    #: must name to be recorded against the exchange it actually answers.
    collaboration_id: str = ""

    def bound_to(self, request_id: str) -> BuildControlRequest:
        return replace(self, request_id=request_id)

    def __post_init__(self) -> None:
        if not (self.question or "").strip():
            raise ValueError("A Build control must state its question.")
        if not self.free_text and len(self.options) < 2:
            raise ValueError("A Build control offering a choice needs at least two options.")
        if len(self.options) > MAX_CONTROL_OPTIONS:
            raise ValueError(f"A Build control may offer at most {MAX_CONTROL_OPTIONS} options.")
        ids = [option.id for option in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError("Build control option ids must be unique.")
        if self.recommended_option_id and self.recommended_option_id not in ids:
            raise ValueError(f"{self.recommended_option_id!r} is recommended but is not offered.")

    @property
    def actions(self) -> tuple[BuildControlAction, ...]:
        return tuple(option.action for option in self.options)

    def as_card(self) -> dict[str, Any]:
        """The server-owned payload a Human Input Card carries.

        Flat, scalar, and bounded on purpose: it is read back from thread
        history by code that must not have to trust it, and every binding below
        is re-validated by whoever acts on the answer.
        """
        return {
            "clarification_type": "dbtl_build_control",
            "request_id": self.request_id,
            "collaboration_id": self.collaboration_id,
            "build_control_kind": self.kind.value,
            "dbtl_cycle_id": self.cycle_id,
            "cycle_revision": int(self.cycle_revision),
            "stage_attempt_id": self.stage_attempt_id,
            "workflow_spec_key": self.workflow_spec_key,
            "step_key": self.step_key,
            "step_run_id": self.step_run_id,
            "plan_digest": self.plan_digest,
            "input_digest": self.input_digest,
            "error_code": self.error_code,
            "question": _text(self.question),
            "rationale": _text(self.rationale),
            "assumptions": [_text(item, limit=400) for item in self.assumptions[:MAX_PLAN_ROWS]],
            "open_questions": [_text(item, limit=400) for item in self.open_questions[:MAX_PLAN_ROWS]],
            "build_plan_rows": [dict(row) for row in self.plan_rows[:MAX_PLAN_ROWS]],
            "recommended_option_id": self.recommended_option_id,
            # Human Input's wire vocabulary is shared with the frontend.  A
            # free-text control that also offers Discuss/Hold is exactly the
            # protocol's ``choice_with_other`` shape; ``text`` was never a
            # valid mode and made the entire request fail frontend parsing.
            "input_mode": ("choice_with_other" if self.options else "free_text") if self.free_text else "single_choice",
            "options": [option.as_dict() for option in self.options],
        }


@dataclass(frozen=True, slots=True)
class BuildControlAnswer:
    """A resolved reply, carrying the bindings of the card it answered."""

    action: BuildControlAction
    kind: BuildControlKind
    cycle_id: str
    stage_attempt_id: str
    step_key: str
    cycle_revision: int = 0
    plan_digest: str = ""
    input_digest: str = ""
    step_run_id: str = ""
    #: The person's own words, verbatim. Never paraphrased into a decision.
    comment: str = ""
    request_id: str = ""
    collaboration_id: str = ""

    @property
    def dispatches(self) -> bool:
        return self.action.dispatches

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "kind": self.kind.value,
            "cycle_id": self.cycle_id,
            "stage_attempt_id": self.stage_attempt_id,
            "step_key": self.step_key,
            "cycle_revision": self.cycle_revision,
            "plan_digest": self.plan_digest,
            "input_digest": self.input_digest,
            "step_run_id": self.step_run_id,
            "comment": self.comment,
            "request_id": self.request_id,
            "collaboration_id": self.collaboration_id,
        }


def resolve_answer(request: Mapping[str, Any] | None, response: Mapping[str, Any] | None) -> BuildControlAnswer | None:
    """Resolve a reply against the card the **server** emitted, or refuse.

    Returns ``None`` for anything that is not an answer to this control: a
    payload that is not one of ours, an option id the card never offered, or a
    free-text reply to a card that asked for a choice. Refusing costs one
    rephrase; accepting a fabricated option would let a client start, replan, or
    restart a governed Build by writing a string.
    """
    if not isinstance(request, Mapping) or request.get("clarification_type") != "dbtl_build_control":
        return None
    if not isinstance(response, Mapping):
        return None
    try:
        kind = BuildControlKind(str(request.get("build_control_kind") or ""))
    except ValueError:
        return None

    comment = _text(response.get("value"))
    action: BuildControlAction | None = None
    option_id = str(response.get("option_id") or "")
    if option_id:
        offered = next((item for item in request.get("options", []) if isinstance(item, Mapping) and item.get("id") == option_id), None)
        if offered is None:
            return None
        try:
            action = BuildControlAction(str(offered.get("value") or ""))
        except ValueError:
            return None
        # An option's own label is not the person's comment. A card answered by
        # picking a choice carries no words unless a free-text box was offered
        # too, and echoing the label back as one would attribute a sentence the
        # server wrote to the person who clicked it.
        if comment == str(offered.get("label") or "") or comment == action.value:
            comment = ""
    elif request.get("input_mode") in {"free_text", "choice_with_other"}:
        if not comment:
            return None
        action = BuildControlAction.ANSWER_DIRECTLY
    else:
        return None

    if action is None:
        return None
    return BuildControlAnswer(
        action=action,
        kind=kind,
        cycle_id=str(request.get("dbtl_cycle_id") or ""),
        stage_attempt_id=str(request.get("stage_attempt_id") or ""),
        step_key=str(request.get("step_key") or ""),
        cycle_revision=int(request.get("cycle_revision") or 0),
        plan_digest=str(request.get("plan_digest") or ""),
        input_digest=str(request.get("input_digest") or ""),
        step_run_id=str(request.get("step_run_id") or ""),
        comment=comment,
        request_id=str(request.get("request_id") or ""),
        collaboration_id=str(request.get("collaboration_id") or ""),
    )


def plan_rows(plan: Any) -> tuple[dict[str, Any], ...]:
    """Render a `BuildPhasePlan`'s phases as bounded display rows.

    Kept here rather than in the card builder because the same rows appear on
    the confirmation card, the pause card, and the retry card, and three copies
    would eventually describe three different plans.
    """
    phases = getattr(plan, "phases", ()) or ()
    rows: list[dict[str, Any]] = []
    for index, phase in enumerate(phases[:MAX_PLAN_ROWS], start=1):
        rows.append(
            {
                "index": index,
                "phase_key": str(getattr(phase, "phase_key", "") or ""),
                "title": _text(getattr(phase, "title", ""), limit=MAX_LABEL_CHARS),
                "objective": _text(getattr(phase, "objective", ""), limit=400),
                "capability": str(getattr(getattr(phase, "capability", None), "value", "") or ""),
                "pause_after": bool(getattr(phase, "pause_after", False)),
            }
        )
    return tuple(rows)


def _option(id: str, label: str, action: BuildControlAction, consequence: str) -> BuildControlOption:
    return BuildControlOption(id=id, label=label, action=action, consequence=consequence)


_HOLD = _option(
    "hold",
    "Hold here",
    BuildControlAction.HOLD,
    "Nothing runs. Everything already finished stays recorded, and you can pick this up later.",
)


def plan_confirmation_request(
    *,
    plan: Any,
    cycle_id: str,
    stage_attempt_id: str,
    workflow_spec_key: str,
    cycle_revision: int = 0,
    input_digest: str = "",
) -> BuildControlRequest:
    """The plan, before anything runs.

    Redirecting a build here costs a sentence; redirecting it afterwards costs
    the run. Nothing is preselected — the whole value of this control is that
    someone reads the decomposition before it is spent.
    """
    rows = plan_rows(plan)
    count = len(rows)
    return BuildControlRequest(
        kind=BuildControlKind.PLAN_CONFIRMATION,
        question=("Start the build with this plan?" if count > 1 else "Start the build?"),
        rationale=_text(getattr(plan, "rationale", "")),
        cycle_id=cycle_id,
        stage_attempt_id=stage_attempt_id,
        workflow_spec_key=workflow_spec_key,
        step_key="plan_build",
        cycle_revision=cycle_revision,
        plan_digest=str(getattr(plan, "digest", "") or ""),
        input_digest=input_digest,
        plan_rows=rows,
        assumptions=tuple(getattr(plan, "assumptions", ()) or ()),
        open_questions=tuple(getattr(plan, "open_questions", ()) or ()),
        options=(
            _option(
                "start",
                "Start the build",
                BuildControlAction.START_BUILD,
                f"Runs {count} planned phase{'s' if count != 1 else ''} in order. Each one is recorded as it finishes.",
            ),
            _option(
                "change",
                "Change the plan",
                BuildControlAction.CHANGE_PLAN,
                "Describe what to change and the plan is drawn again from your words. Nothing has run, so nothing is lost.",
            ),
            _HOLD,
        ),
    )


def phase_pause_request(
    *,
    plan: Any,
    completed_phases: int,
    paused_phase_title: str,
    cycle_id: str,
    stage_attempt_id: str,
    workflow_spec_key: str,
    cycle_revision: int = 0,
) -> BuildControlRequest:
    """A committed boundary the plan itself asked to stop at.

    Only phases *after* this one can be changed here. Rewriting a phase that
    already produced bound outputs is a replan with the invalidation that
    implies, not an edit.
    """
    rows = plan_rows(plan)
    remaining = max(len(rows) - completed_phases, 0)
    return BuildControlRequest(
        kind=BuildControlKind.PHASE_PAUSE,
        question=f"{paused_phase_title or 'That phase'} is finished. Continue with the rest of the build?",
        rationale=(f"{completed_phases} of {len(rows)} phases are recorded. {remaining} still to run." if rows else ""),
        cycle_id=cycle_id,
        stage_attempt_id=stage_attempt_id,
        workflow_spec_key=workflow_spec_key,
        step_key="execute_phases",
        cycle_revision=cycle_revision,
        plan_digest=str(getattr(plan, "digest", "") or ""),
        plan_rows=rows,
        options=(
            _option(
                "continue",
                "Continue",
                BuildControlAction.CONTINUE_BUILD,
                f"Runs the remaining {remaining} phase{'s' if remaining != 1 else ''}. Finished phases are not run again.",
            ),
            _option(
                "change_rest",
                "Change the remaining plan",
                BuildControlAction.CHANGE_PLAN,
                "Describe what should change. Finished phases stay recorded, and the phases after this one are drawn again.",
            ),
            _HOLD,
        ),
    )


def step_failure_request(
    *,
    step_key: str,
    step_label: str,
    error_code: str,
    error_summary: str,
    cycle_id: str,
    stage_attempt_id: str,
    workflow_spec_key: str,
    cycle_revision: int = 0,
    plan_digest: str = "",
    input_digest: str = "",
    step_run_id: str = "",
    completed_phases: int = 0,
    plan: Any = None,
) -> BuildControlRequest:
    """After a failure: retry the one step, replan, restart, or hold.

    Replan exists because the honest answer to a repeatedly failing phase is
    often that the decomposition was wrong, and the alternative — restarting the
    whole Build to change one phase — is expensive enough that nobody does it.
    Its consequence line states plainly that finished phases are discarded,
    since that is the cost.
    """
    kept = f" {completed_phases} finished phase{'s' if completed_phases != 1 else ''} stay recorded and are not run again." if completed_phases else ""
    return BuildControlRequest(
        kind=BuildControlKind.STEP_FAILURE,
        question=f"{step_label or step_key} did not finish. What next?",
        rationale=_text(error_summary),
        error_code=error_code,
        cycle_id=cycle_id,
        stage_attempt_id=stage_attempt_id,
        workflow_spec_key=workflow_spec_key,
        step_key=step_key,
        cycle_revision=cycle_revision,
        plan_digest=plan_digest,
        input_digest=input_digest,
        step_run_id=step_run_id,
        plan_rows=plan_rows(plan) if plan is not None else (),
        recommended_option_id="retry",
        options=(
            _option(
                "retry",
                f"Retry {step_label or step_key}",
                BuildControlAction.RETRY_STEP,
                f"Runs this step again and reuses everything before it.{kept}",
            ),
            _option(
                "replan",
                "Replan the build",
                BuildControlAction.REPLAN_BUILD,
                "Keeps the approved design and draws a new plan. Every finished phase is discarded, because they belong to the old plan.",
            ),
            _option(
                "restart",
                "Restart the build",
                BuildControlAction.RESTART_BUILD,
                "Starts again at reading the approved design. Nothing from this build is reused.",
            ),
            _HOLD,
        ),
    )


def execution_preflight_request(
    *,
    cycle_id: str,
    stage_attempt_id: str,
    workflow_spec_key: str,
    cycle_revision: int = 0,
    missing_tool: str = "bash",
) -> BuildControlRequest:
    """Pause before planning when Build cannot execute its own work.

    Replanning cannot add a server capability, and sending the same request
    back to a worker cannot make a missing tool appear.  The only productive
    retry is after an administrator changes the runtime; the card says that
    explicitly and otherwise offers a durable Hold.
    """
    tool = _text(missing_tool, limit=80) or "execution tool"
    return BuildControlRequest(
        kind=BuildControlKind.STEP_FAILURE,
        question="Build cannot start because its execution preflight failed. What next?",
        rationale=(f"The Build worker does not have the required {tool!r} tool. No planner or Build worker ran. Enable the tool for this runtime, then retry."),
        error_code="execution_tool_unavailable",
        cycle_id=cycle_id,
        stage_attempt_id=stage_attempt_id,
        workflow_spec_key=workflow_spec_key,
        step_key="execution_preflight",
        cycle_revision=cycle_revision,
        input_digest=f"execution-preflight:{tool}",
        recommended_option_id="retry",
        options=(
            _option(
                "retry",
                "Retry preflight",
                BuildControlAction.RETRY_STEP,
                "Checks the runtime again. No worker starts unless the execution tool is now available.",
            ),
            _HOLD,
        ),
    )


def paused_build_recovery_request(
    *,
    previous: Mapping[str, Any],
    cycle_revision: int,
    requested_action: str = "",
) -> BuildControlRequest:
    """Open a fresh control after a prior Hold released the conversation.

    The previous card remains an immutable record of the decision to hold.  A
    later explicit “start/retry/replan Build” therefore raises a new row rather
    than mutating or silently overriding that answer.
    """
    requested = _text(requested_action, limit=40).lower()
    recommended = "replan" if requested in {"replan", "restart"} else "retry"
    previous_id = _text(previous.get("id"), limit=120) or _text(previous.get("request_id"), limit=120)
    return BuildControlRequest(
        kind=BuildControlKind.STEP_FAILURE,
        question="This Build was paused. What should happen next?",
        rationale="The earlier Hold remains recorded. Choose a new governed action; nothing starts from these words alone.",
        error_code="paused_build_reopened",
        cycle_id=_text(previous.get("cycle_id"), limit=160),
        stage_attempt_id=_text(previous.get("stage_attempt_id"), limit=160),
        workflow_spec_key=_text(previous.get("workflow_spec_key"), limit=160),
        step_key=_text(previous.get("step_key"), limit=160) or "execute_phases",
        cycle_revision=cycle_revision,
        plan_digest=_text(previous.get("plan_digest"), limit=200),
        input_digest=f"resume-after:{previous_id}",
        recommended_option_id=recommended,
        options=(
            _option(
                "retry",
                "Retry the stopped step",
                BuildControlAction.RETRY_STEP,
                "Reuses every committed predecessor and runs only the stopped step again.",
            ),
            _option(
                "replan",
                "Replan the build",
                BuildControlAction.REPLAN_BUILD,
                "Keeps the approved design but discards phases from the old plan and draws a new one.",
            ),
            _option(
                "restart",
                "Restart the build",
                BuildControlAction.RESTART_BUILD,
                "Starts again at the approved design and reuses none of this Build attempt.",
            ),
            _HOLD,
        ),
    )


def no_presentable_results_request(
    *,
    cycle_id: str,
    stage_attempt_id: str,
    workflow_spec_key: str,
    cycle_revision: int = 0,
    plan_digest: str = "",
    completed_phases: int = 0,
    plan: Any = None,
) -> BuildControlRequest:
    """Ask how to recover when Build produced no reviewable result evidence.

    Retrying the summarizer cannot manufacture a missing measurement or plot,
    so this card intentionally omits that looping option. It is the only human
    surface emitted in this state: there is no empty deck beside it.
    """
    kept = f" {completed_phases} finished phase{'s' if completed_phases != 1 else ''} remain recorded for audit." if completed_phases else ""
    return BuildControlRequest(
        kind=BuildControlKind.STEP_FAILURE,
        question="The Build finished without verified outcomes or figures. What should happen next?",
        rationale="There is no trustworthy result to present in a review deck. Choose how the result-producing work should be redone.",
        error_code="execution_output_missing",
        cycle_id=cycle_id,
        stage_attempt_id=stage_attempt_id,
        workflow_spec_key=workflow_spec_key,
        step_key="summarize_results",
        cycle_revision=cycle_revision,
        plan_digest=plan_digest,
        plan_rows=plan_rows(plan) if plan is not None else (),
        recommended_option_id="replan",
        options=(
            _option(
                "replan",
                "Replan the build",
                BuildControlAction.REPLAN_BUILD,
                f"Draws a new plan that must produce measurable outcomes or figures.{kept}",
            ),
            _option(
                "restart",
                "Restart the build",
                BuildControlAction.RESTART_BUILD,
                "Starts again from the approved design. Nothing from this attempt is reused.",
            ),
            _HOLD,
        ),
    )


def worker_question_request(
    *,
    question: str,
    rationale: str,
    step_key: str,
    cycle_id: str,
    stage_attempt_id: str,
    workflow_spec_key: str,
    cycle_revision: int = 0,
    plan_digest: str = "",
    input_digest: str = "",
    step_run_id: str = "",
    meeting_available: bool = False,
    assumptions: Sequence[str] = (),
    open_questions: Sequence[str] = (),
) -> BuildControlRequest:
    """One focused question a worker could not answer for itself.

    Free text, because the answers worth having here are the ones enumerated
    choices cannot hold — a missing constraint, an example, a correction. The
    meeting option is offered rather than started: no model convenes a meeting
    by labelling its own question complex.
    """
    options: tuple[BuildControlOption, ...] = ()
    if meeting_available:
        options = (
            _option(
                "meeting",
                "Discuss it first",
                BuildControlAction.START_MEETING,
                "Runs a bounded build meeting over the recorded evidence and comes back with options. It cannot decide for you.",
            ),
            _HOLD,
        )
    return BuildControlRequest(
        kind=BuildControlKind.WORKER_QUESTION,
        question=_text(question),
        rationale=_text(rationale),
        cycle_id=cycle_id,
        stage_attempt_id=stage_attempt_id,
        workflow_spec_key=workflow_spec_key,
        step_key=step_key,
        cycle_revision=cycle_revision,
        plan_digest=plan_digest,
        input_digest=input_digest,
        step_run_id=step_run_id,
        free_text=True,
        options=options,
        assumptions=tuple(_text(item, limit=400) for item in assumptions if _text(item, limit=400)),
        open_questions=tuple(_text(item, limit=400) for item in open_questions if _text(item, limit=400)),
    )


def change_plan_request(
    *,
    previous: Mapping[str, Any],
    remaining_only: bool = False,
) -> BuildControlRequest:
    """The free-text follow-up to "Change the plan".

    The person's words travel verbatim into the replan; they are never
    paraphrased into a planner-owned decision, which is the whole reason this
    is a second exchange rather than a dropdown of pre-written adjustments.
    """
    return BuildControlRequest(
        kind=BuildControlKind(str(previous.get("build_control_kind") or BuildControlKind.PLAN_CONFIRMATION.value)),
        question=("What should change about the phases that have not run yet?" if remaining_only else "What should change about the plan?"),
        rationale="Your words are carried into the new plan exactly as you write them.",
        cycle_id=str(previous.get("dbtl_cycle_id") or ""),
        stage_attempt_id=str(previous.get("stage_attempt_id") or ""),
        workflow_spec_key=str(previous.get("workflow_spec_key") or ""),
        step_key=str(previous.get("step_key") or "plan_build"),
        cycle_revision=int(previous.get("cycle_revision") or 0),
        plan_digest=str(previous.get("plan_digest") or ""),
        input_digest=str(previous.get("input_digest") or ""),
        free_text=True,
    )


__all__ = [
    "MAX_CONTROL_OPTIONS",
    "BuildControlAction",
    "BuildControlAnswer",
    "BuildControlKind",
    "BuildControlOption",
    "BuildControlRequest",
    "change_plan_request",
    "phase_pause_request",
    "plan_confirmation_request",
    "plan_rows",
    "resolve_answer",
    "step_failure_request",
    "worker_question_request",
]
