"""Live DBTL stage execution over ``SubagentExecutor``.

The adapter is deliberately narrower than the lead agent: one selected,
project-owned cycle, one currently active executable stage, one bounded fan-out,
then durable evidence. It can never approve a stage; review remains a separate
human write bound to the evidence revision.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from inspect import isawaitable
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from deerflow.agents.dbtl.live_stage.replay import ReplayService
from deerflow.agents.dbtl.live_stage.test_review import (
    TestReviewService,
)
from deerflow.agents.dbtl.live_stage.test_review import (
    validated_test_assessment as _validated_test_assessment,
)
from deerflow.agents.dbtl.live_stage.types import LiveStageResult
from deerflow.agents.middlewares.finalization_deadline_middleware import (
    FinalizationDeadlineMiddleware,
    model_call_budget,
)
from deerflow.authz.principal import normalize_authz_attributes
from deerflow.dbtl.agent_selector import AgentCandidate, Assignment, SelectionResult, build_candidates, select_agents
from deerflow.dbtl.consensus import CONSENSUS_CONTRACT
from deerflow.dbtl.council import (
    ROLE_BRIEFS,
    CouncilDepth,
    CouncilPlan,
    CouncilRole,
    council_depth_from_config,
    depth_policy,
    plan_council,
    plan_from_proposal,
    recommend_depth,
)
from deerflow.dbtl.council_deck import render_council_deck
from deerflow.dbtl.council_proposal import (
    CouncilProposal,
    build_proposal_prompt,
    parse_council_proposal,
    seatable_agents,
)
from deerflow.dbtl.council_settings import (
    REASONING_EXTENDED,
    ParticipantSettings,
    apply_participant_settings,
    owner_instruction_lines,
)
from deerflow.dbtl.cycle_state import TERMINAL_CYCLE_STATES, StageStatus, stage_for_state
from deerflow.dbtl.decision_request import DECISION_REQUEST_CONTRACT, DecisionRequest
from deerflow.dbtl.reconciliation_policy import reconciliation_required
from deerflow.dbtl.review_markdown import render_review_markdown, render_stage_digest
from deerflow.dbtl.review_paths import stage_file_name, stage_output_dir
from deerflow.dbtl.revision_intent import (
    REVISION_INTENT_INSTRUCTION,
    RevisionInterpreter,
    RevisionVerdict,
    interpret_revision,
)
from deerflow.dbtl.stage_meetings import (
    REVIEW_MEETING_STAGES,
    MeetingRequirement,
    sanitize_meeting_attachment,
    surface_meeting_gate,
)
from deerflow.dbtl.stage_routes import RouteContext, compute_stage_routes
from deerflow.dbtl.stage_runner import (
    RESULT_CONTRACT,
    WORKSPACE_PATH_NOTE,
    AsyncWorkerDispatcher,
    DispatchOutcome,
    StageExecutionOutcome,
    StageExecutionPlan,
    WorkUnit,
    arun_stage,
    collect_results,
    plan_stage,
)
from deerflow.dbtl.stage_spec import (
    StageSpec,
    StageSpecNotFound,
    WorkerBudget,
    resolve_review_stage_spec,
    resolve_stage_spec,
)
from deerflow.dbtl.transition_assessment import (
    TransitionAssessment,
    build_transition_assessment_prompt,
    parse_transition_assessment,
    standard_assessment,
)
from deerflow.dbtl.validity import (
    DEFAULT_VALIDITY_PACK,
    ValidityCheckName,
)
from deerflow.dbtl.worker_result import (
    QualityCheck,
    StageWorkerResult,
    WorkerResultRejected,
    WorkerStatus,
    extract_result_payload,
    parse_worker_result,
)
from deerflow.projects.storage import ensure_project_dirs, project_outputs_dir
from deerflow.trace_context import (
    DEERFLOW_TRACE_METADATA_KEY,
    get_current_trace_id,
    normalize_trace_id,
)

logger = logging.getLogger(__name__)


#: How long the roster proposal may take before the meeting proceeds without it.
#:
#: This is not a performance tuning knob. The proposal runs *in front of the
#: preflight card*, so an unbounded wait here does not slow the meeting down —
#: it removes it: nothing is shown, nothing is dispatched, and cancelling is the
#: user's only exit. Generous enough for a reasoning model on a cold start,
#: short enough that a wedged provider costs a better roster rather than the
#: whole interaction.
ROSTER_PROPOSAL_TIMEOUT_SECONDS = 75.0

CandidateProvider = Callable[[], Sequence[AgentCandidate]]

_DESIGN_HISTORY_TURNS = 4
_DESIGN_HISTORY_SUMMARY_CHARS = 3_000
_DESIGN_HISTORY_DETAIL_CHARS = 600
_LIGHT_DEBATE_MANIFEST_ENTRIES = 24
_LIGHT_DEBATE_DATASETS = 12
_LIGHT_DEBATE_HISTORY_TURNS = 1

_LIGHT_DEBATE_INSTRUCTIONS = (
    "Optimize for a useful pilot decision in minutes, not an exhaustive design review.",
    "Start from the cycle metadata and request. Inspect at most 2 clearly relevant workspace files, and only when the decision cannot be made without them.",
    "Do not survey the workspace, conduct external research, install packages, run scripts, or implement the study.",
    "Missing or unreadable data, packages, and tools are pilot assumptions to record in limitations, not blockers to reconstruct or reasons to withhold a design.",
    "Return one concrete recommendation, the most important failure mode, and the assumptions a full Design review would need to revisit.",
    "Keep the summary under 400 words and every result list to at most 5 entries. Preserve the required JSON result contract.",
)

_LIGHT_PILOT_FALLBACK_AGENT = "system:light-pilot-fallback"
_LIGHT_PILOT_TOOL_NAMES = frozenset({"read_file"})


def _runtime_view(config: RunnableConfig) -> dict[str, Any]:
    merged = dict(config.get("configurable", {}) or {})
    context = config.get("context", {}) or {}
    if isinstance(context, dict):
        merged.update(context)
    return merged


def _bounded_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _compact_design_history(prior_runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep bounded prior chair decisions, not every full council payload."""
    chair_runs = [item for item in prior_runs if str(item.get("capability") or "") == "design_council_chair"]
    selected = chair_runs[-_DESIGN_HISTORY_TURNS:]
    compact: list[dict[str, Any]] = []
    for item in selected:
        result = item.get("result")
        payload = result if isinstance(result, dict) else {}
        compact.append(
            {
                "unit_id": str(item.get("unit_id") or ""),
                "status": str(item.get("status") or payload.get("status") or ""),
                "summary": _bounded_text(
                    payload.get("summary"),
                    max_chars=_DESIGN_HISTORY_SUMMARY_CHARS,
                ),
                "clarification_question": _bounded_text(
                    payload.get("clarification_question"),
                    max_chars=_DESIGN_HISTORY_DETAIL_CHARS,
                ),
                "limitations": [_bounded_text(value, max_chars=_DESIGN_HISTORY_DETAIL_CHARS) for value in list(payload.get("limitations") or [])[:4]],
                "created_at": str(item.get("created_at") or ""),
            }
        )
    return compact


def _light_design_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Scope Design context to the promise made by the Light chooser.

    Removing the token guardrail does not by itself make a meeting quick.
    Light receives a small manifest, only the latest chair synthesis, and an
    explicit stop rule. The request and cycle metadata remain intact; this is
    a quick decision over the same question, not a different question.
    """
    scoped = dict(payload)
    scoped["declared_datasets"] = list(payload.get("declared_datasets") or ())[:_LIGHT_DEBATE_DATASETS]
    scoped["project_workspace_manifest"] = list(payload.get("project_workspace_manifest") or ())[:_LIGHT_DEBATE_MANIFEST_ENTRIES]
    scoped["prior_design_council_runs"] = list(payload.get("prior_design_council_runs") or ())[-_LIGHT_DEBATE_HISTORY_TURNS:]
    scoped["council_execution"] = {
        "mode": "quick_pilot",
        "token_accounting": "metered_not_capped",
        "instructions": list(_LIGHT_DEBATE_INSTRUCTIONS),
    }
    return scoped


def _tools_for_stage_budget(tools: Sequence[Any], budget: WorkerBudget) -> list[Any]:
    """Keep Light on metadata and targeted reads instead of tool exploration."""
    if budget != depth_policy(CouncilDepth.LIGHT).budget:
        return list(tools)
    return [tool for tool in tools if str(getattr(tool, "name", "")) in _LIGHT_PILOT_TOOL_NAMES]


def _token_limit_for_worker(unit: WorkUnit, budget: WorkerBudget) -> int | None:
    """Resolve an enforced ceiling, or ``None`` for metered-only execution."""
    if not budget.token_limit_enforced:
        return None
    return unit.max_tokens or budget.max_tokens


def _light_pilot_chair_fallback(
    result: StageWorkerResult,
    *,
    dispatch: DispatchOutcome | None,
    unit: WorkUnit,
    cycle: Mapping[str, Any],
) -> StageWorkerResult:
    """Make a reviewable pilot draft without pretending capped work completed.

    The fallback itself is deterministic and therefore is not capped. It quotes
    the recoverable chair draft (when present), anchors it to server-owned cycle
    metadata, and records the original worker and stop reason in provenance.
    Medium and Heavy never call this function.
    """
    if result.is_trustworthy or result.status is WorkerStatus.NEEDS_INPUT:
        return result

    raw_text = _bounded_text(dispatch.text if dispatch is not None else "", max_chars=2_000)
    if not raw_text:
        # Cycle metadata is context, not a meeting. If the chair never returned
        # anything, a provider/executor/sandbox failure cannot be converted into
        # a server-authored conclusion merely because Light permits a bounded
        # draft. Keep the failed record and let the owner retry the meeting.
        return result
    worker_draft = result.summary if result.status is WorkerStatus.COMPLETED else raw_text
    question = _bounded_text(cycle.get("research_question"), max_chars=800) or "(not stated)"
    objective = _bounded_text(cycle.get("objective"), max_chars=800) or "(not stated)"
    success = _bounded_text(cycle.get("success_criteria"), max_chars=800) or "(not stated)"
    summary = "\n".join(
        [
            "Light-pilot Design draft.",
            f"Research question: {question}",
            f"Objective: {objective}",
            f"Pilot success criterion: {success}",
            (f"Recoverable chair draft: {worker_draft}" if worker_draft else "No chair draft was recoverable; proceed from the cycle metadata and resolve implementation details during Data reconciliation."),
        ]
    )
    source_stop_reason = (dispatch.stop_reason if dispatch is not None else None) or result.stop_reason or result.status.value
    return StageWorkerResult(
        status=WorkerStatus.COMPLETED,
        summary=summary,
        capability=unit.capability,
        agent_name=_LIGHT_PILOT_FALLBACK_AGENT,
        limitations=(
            "Light pilot fallback: the chair did not satisfy the strict Design evidence contract, so this is a reviewable draft assembled from cycle metadata and recoverable meeting output.",
            "Pre-existing datasets, readable project files, installed packages, and execution tools were not required for this pilot Design; their absence must be resolved or accepted during Data reconciliation.",
            f"The source chair ended with {source_stop_reason}; its output is context for this draft, not completed evidence.",
        ),
        provenance={
            "fallback": "light_pilot_design_v1",
            "source_agent": unit.agent_name,
            "source_stop_reason": source_stop_reason,
            "inputs_examined": ["cycle metadata", "recoverable chair output"],
        },
        quality_checks=(
            QualityCheck(
                name="pilot scope is explicit",
                passed=True,
                detail="Strict evidence and pre-existing input requirements are deferred, not represented as satisfied.",
            ),
        ),
        recommended_next_actions=(
            "Review and approve this as a pilot Design if the stated objective and success criterion are sufficient.",
            "Resolve concrete datasets, parameters, packages, and reproducibility requirements during Data reconciliation.",
        ),
        token_usage=result.token_usage,
    )


def _preview_context(cycle: Mapping[str, Any]) -> str:
    """The little a roster proposal needs, cheap enough to build interactively.

    Deliberately not the full ``stage_context`` the dispatch path assembles:
    that walks the project workspace and prior council runs, and this one runs
    in front of a person waiting for a card. Who should sit on a council is
    decided by what the cycle is asking, which is all of this.
    """
    return json.dumps(
        {key: cycle.get(key) for key in ("title", "cycle_class", "research_question", "objective", "success_criteria")},
        ensure_ascii=False,
        sort_keys=True,
    )


def _model_call_budget(max_turns: int) -> int:
    """How many model calls fit in a turn budget.

    Delegates to the middleware that enforces the deadline, so the number the
    stage layer reserves and the number the deadline fires on are one
    computation. See ``SUBAGENT_SUPERSTEPS_PER_TURN`` for why this is not simply
    half of ``max_turns``.
    """
    return model_call_budget(max_turns)


_FAILURE_REASON_CHARS = 300
_MAX_FAILURE_REASONS = 6

_HUMAN_AUTHORING_QUESTION = "Write the design for this cycle. What is the question, what will you measure, on what population and over what seasons, and what result would make you reject it?"
_HUMAN_AUTHORING_NOTE = (
    "This design meeting is set to **Write it myself**, so no agent was consulted and no worker ran. Your answer is recorded as the Design review package exactly as you write it; you still review and approve it yourself."
)


def _failure_reasons(results: Sequence[dict[str, Any]]) -> list[str]:
    """One actionable line per worker that produced no usable evidence.

    A capped run and a contract violation look identical from the outside and
    need opposite fixes — raise the budget versus fix the prompt — so the cap is
    named separately rather than folded into the summary text.
    """
    lines: list[str] = []
    for item in results:
        if item.get("is_trustworthy"):
            continue
        unit = str(item.get("unit_id") or item.get("capability") or "worker")
        detail = _bounded_text(item.get("summary"), max_chars=_FAILURE_REASON_CHARS) or "No reason was recorded."
        cap = str(item.get("stop_reason") or "")
        suffix = f" (stopped by the {cap.replace('_', ' ')} guardrail)" if cap else ""
        lines.append(f"- {unit}: {detail}{suffix}")
        if len(lines) >= _MAX_FAILURE_REASONS:
            break
    return lines


_SEAT_ROLE_LABELS = {
    "position": "Independent position",
    "red_team": "Red team",
    "chair": "Chair",
}


def _seat_description(unit: WorkUnit) -> str:
    """The one line a live view shows for this seat while it works."""
    role = _SEAT_ROLE_LABELS.get(unit.role, "Council seat")
    if unit.focus:
        return f"{role}: {unit.focus}"
    return f"{role}: {unit.capability.replace('_', ' ')}"


def _seat_identity(unit: WorkUnit, *, model: str, stage: str = "design") -> dict[str, Any]:
    """Who is speaking, in what role, on whose behalf.

    Carried on the event rather than left for a consumer to parse out of the
    unit id. A live debate view has to say "the red team is arguing" while it is
    happening, and a view that derives that from an identifier is one rename
    away from labelling every seat wrong.
    """
    return {
        "stage": stage,
        "role": unit.role,
        "role_label": _SEAT_ROLE_LABELS.get(unit.role, "Council seat"),
        "focus": unit.focus,
        "capability": unit.capability,
        "agent_name": unit.agent_name,
        "via_generalist": unit.via_generalist,
        "model": model,
        "round": unit.round,
        # Only the chair's synthesis is the stage's answer, and a reader
        # watching three lanes finish cannot otherwise tell which one mattered.
        "counts_toward_stage_output": unit.role == "chair",
    }


def _summarize_token_usage(
    records: Sequence[Mapping[str, int | str | None]] | None,
) -> dict[str, int] | None:
    """Collapse a seat's per-call records into one provider-reported meter."""
    if not records:
        return None
    usage = {key: sum(int(record.get(key, 0) or 0) for record in records if isinstance(record.get(key, 0), (int, float))) for key in ("input_tokens", "output_tokens", "total_tokens")}
    return usage if any(usage.values()) else None


def _report_subagent_token_usage(
    config: RunnableConfig,
    result: Any,
) -> None:
    """Add direct DBTL subagent calls to the parent run's usage journal once."""
    if getattr(result, "usage_reported", True):
        return
    records = getattr(result, "token_usage_records", None) or []
    if not records:
        return
    callbacks = config.get("callbacks")
    handlers = getattr(callbacks, "handlers", callbacks)
    if not isinstance(handlers, Sequence) or isinstance(handlers, (str, bytes)):
        return
    for handler in handlers:
        recorder = getattr(handler, "record_external_llm_usage_records", None)
        if not callable(recorder):
            continue
        try:
            recorder(records)
            result.usage_reported = True
        except Exception:  # noqa: BLE001 - metering failure must not lose work
            logger.warning("Failed to record Design council token usage.", exc_info=True)
        return


def _terminal_seat_event(
    unit: WorkUnit,
    outcome: DispatchOutcome,
    *,
    model: str,
    meeting_stage: str | None = "design",
) -> dict[str, Any]:
    """Report contract-valid evidence progress, not child-graph termination."""
    base = {
        "task_id": unit.unit_id,
        **({"council_seat": _seat_identity(unit, model=model, stage=meeting_stage)} if meeting_stage else {}),
        **({"dbtl_stage": meeting_stage} if meeting_stage else {}),
        **({"usage": dict(outcome.token_usage)} if outcome.token_usage else {}),
    }
    if outcome.error or not outcome.text:
        return {
            "type": "task_failed",
            **base,
            "error": outcome.error or "The worker returned no output.",
            "stop_reason": outcome.stop_reason,
        }
    try:
        parsed = parse_worker_result(
            extract_result_payload(outcome.text),
            capability=unit.capability,
            agent_name=unit.agent_name,
            stop_reason=outcome.stop_reason,
        )
    except WorkerResultRejected as exc:
        return {
            "type": "task_failed",
            **base,
            "error": (f"The worker's result did not satisfy the stage contract: {exc}"),
            "stop_reason": outcome.stop_reason,
        }
    if parsed.status in {WorkerStatus.FAILED, WorkerStatus.BLOCKED} or (parsed.was_capped and parsed.status is not WorkerStatus.NEEDS_INPUT):
        return {
            "type": "task_failed",
            **base,
            "error": parsed.summary,
            "stop_reason": outcome.stop_reason,
        }
    return {
        "type": "task_completed",
        **base,
        # Publish the validated, normalized contract. Workers sometimes wrap
        # otherwise-valid JSON in prose or a code fence; forwarding that raw
        # text would make the browser's bounded live summary unreadable even
        # though the durable stage package parsed correctly.
        "result": json.dumps(parsed.as_dict(), ensure_ascii=False),
        "stop_reason": outcome.stop_reason,
    }


RosterWriter = Callable[[str], Any]

#: Same shape as :data:`RosterWriter`: an async callable from prompt to raw text.
IntentInterpreter = Callable[[str], Any]
TransitionAssessor = Callable[[str], Any]


def _reconciliation_ready_after_design_approval(
    view: Mapping[str, Any] | None,
) -> bool:
    """Whether data-specific Build preconditions are already settled.

    A pre-verdict view always says Design itself is unapproved. The progressive
    one-click action satisfies that reason atomically; it may not waive any
    dataset, matrix, or unreadable-row reason.
    """
    gate = dict((view or {}).get("gate") or {})
    data_reasons = [str(reason) for reason in gate.get("reasons", []) if "Design stage has not been approved" not in str(reason)]
    return bool(gate and (bool(gate.get("ready")) or (not data_reasons and not gate.get("blocking_rows"))))


#: The deterministic phrases stay the fast path and the audit anchor; this
#: interpreter reads only the requests they did not match. Human chat input is
#: kept verbatim in the record but may carry typos and paraphrases, and those
#: must not change what the request *means* — production-grade determinism is a
#: rule for code, data, and figures, not for reading a chatbox.
_DEBATE_INTENT_INSTRUCTION = (
    "You read one project-owner chat message and decide whether it asks to run the design meeting (debate/council) again. "
    "The message may contain typos, misspellings, or paraphrases; judge the intent, not the spelling. "
    "Reply with exactly one word: CONVENE if the message asks to re-run, restart, redo, or hold the meeting again; HOLD for anything else (questions about the design, review remarks, unrelated requests). "
    "If you are unsure, reply HOLD."
)


def make_llm_intent_interpreter() -> IntentInterpreter | None:
    """The production debate-intent interpreter: one nostream model call.

    Returns ``None`` when no drafting model is configured, which the adapter
    reads as "the deterministic phrases are the whole answer". The same
    fail-soft contract as the roster writer: interpretation is an improvement
    on the phrase table, and no failure here may cost the owner their design —
    an unreadable verdict holds the package on the table, it never convenes.
    """
    from deerflow.config.app_config import get_app_config

    try:
        app_config = get_app_config()
    except Exception:  # noqa: BLE001 - config trouble degrades interpretation, not the run
        return None
    model_name = getattr(getattr(app_config, "dbtl", None), "setup_draft_model_name", None)
    if not model_name:
        return None

    async def interpret(prompt: str) -> str:
        from deerflow.utils.oneshot_llm import run_oneshot_llm

        return await run_oneshot_llm(
            system_instruction=_DEBATE_INTENT_INSTRUCTION,
            user_content=prompt,
            run_name="dbtl_debate_intent",
            app_config=app_config,
            model_name=model_name,
        )

    return interpret


def make_llm_revision_interpreter() -> RevisionInterpreter | None:
    """The production revision-route reader: one nostream model call.

    Returns ``None`` when no drafting model is configured, which the adapter
    reads as "take the cheap route". Same fail-soft contract as its siblings,
    with the direction of the failure chosen deliberately: an unavailable
    reader must never be the reason a full meeting reconvenes.
    """
    from deerflow.config.app_config import get_app_config

    try:
        app_config = get_app_config()
    except Exception:  # noqa: BLE001 - config trouble degrades the reading, not the run
        return None
    model_name = getattr(getattr(app_config, "dbtl", None), "setup_draft_model_name", None)
    if not model_name:
        return None

    async def interpret(prompt: str) -> str:
        from deerflow.utils.oneshot_llm import run_oneshot_llm

        return await run_oneshot_llm(
            system_instruction=REVISION_INTENT_INSTRUCTION,
            user_content=prompt,
            run_name="dbtl_revision_intent",
            app_config=app_config,
            model_name=model_name,
        )

    return interpret


def make_llm_roster_writer() -> RosterWriter | None:
    """The production roster writer: one non-graph model call, tagged nostream.

    Returns ``None`` when no drafting model is configured, which the adapter
    reads as "fall back to capability selection". The same fail-soft contract as
    the setup-question writer: a roster is an improvement on selection, and no
    failure here may cost a cycle its Design stage. The call goes through
    ``run_oneshot_llm``, so its prompt and raw JSON never enter the thread.
    """
    from deerflow.config.app_config import get_app_config

    try:
        app_config = get_app_config()
    except Exception:  # noqa: BLE001 - config trouble degrades the roster, not the run
        return None
    model_name = getattr(getattr(app_config, "dbtl", None), "setup_draft_model_name", None)
    if not model_name:
        return None

    async def write(prompt: str) -> str:
        from deerflow.utils.oneshot_llm import run_oneshot_llm

        return await run_oneshot_llm(
            system_instruction="You assemble expert panels for research design. Reply with JSON only.",
            user_content=prompt,
            run_name="dbtl_council_roster",
            app_config=app_config,
            model_name=model_name,
        )

    return write


def make_llm_transition_assessor() -> TransitionAssessor | None:
    """The production remaining-work assessor: one nostream model call."""
    from deerflow.config.app_config import get_app_config

    try:
        app_config = get_app_config()
    except Exception:  # noqa: BLE001 - assessment failure keeps the standard gate
        return None
    model_name = getattr(getattr(app_config, "dbtl", None), "transition_assessor_model_name", None)
    if not model_name:
        return None

    async def assess(prompt: str) -> str:
        from deerflow.utils.oneshot_llm import run_oneshot_llm

        return await run_oneshot_llm(
            system_instruction=("You assess the difficulty of remaining research workflow work. You never approve evidence or invent routes. Reply with JSON only."),
            user_content=prompt,
            run_name="dbtl_transition_assessment",
            app_config=app_config,
            model_name=model_name,
        )

    return assess


def _proposed_selection(proposal: CouncilProposal) -> SelectionResult:
    """Record a proposed roster in the shape the package already understands.

    The review package reads ``selection`` to say who ran, and a reviewer
    comparing two attempts should not have to know which of them used a
    proposal. The seat's focus and the refusals ride in ``notes`` — a seat that
    was asked for and refused is exactly the thing a reviewer needs to see,
    since its absence is otherwise indistinguishable from never having been
    considered.
    """
    return SelectionResult(
        assignments=tuple(
            Assignment(
                capability=seat.capability,
                agent_name=seat.agent_name,
                via_generalist=seat.agent_name == "general-purpose",
            )
            for seat in proposal.positions
        ),
        used_generalist_for=tuple(seat.capability for seat in proposal.positions if seat.agent_name == "general-purpose"),
        notes=(
            "Roster proposed for this request rather than selected by capability.",
            *(f"Seat: {seat.focus} ({seat.agent_name}, {seat.model or 'inherited model'})" for seat in proposal.positions),
            *(f"Refused: {reason}" for reason in proposal.rejected),
            *proposal.notes,
        ),
    )


def _proposed_units(
    proposal: CouncilProposal,
    spec: StageSpec,
    *,
    attempt_id: str,
    context: str,
    round_number: int = 1,
    change_request: str | None = None,
    settings: Mapping[str, ParticipantSettings] | None = None,
) -> tuple[WorkUnit, ...]:
    """Turn a validated roster into work units.

    The seat's own brief replaces the generic capability sentence. That is the
    whole point: with no specialists registered every seat resolves to the same
    agent, and identical prompts to identical agents produce corroboration
    rather than debate. Different briefs disagree even when the agent does not
    change.
    """
    units: list[WorkUnit] = []
    for index, seat in enumerate(proposal.positions, start=1):
        override = (settings or {}).get(f"position-{index}")
        prompt = "\n".join(
            [
                f"You are contributing to the {spec.title} stage of a DBTL research cycle.",
                "",
                f"Stage purpose: {spec.purpose}",
                f"Your seat in the meeting: {seat.focus}",
                f"What you argue from: {seat.brief}",
                "",
                "You are one of several independent positions and you cannot see the others.",
                "Argue your own case as strongly as the evidence allows; a chair will weigh it against the rest.",
                "Do not hedge toward what you imagine the others will say.",
                *owner_instruction_lines(_owner_note(override, seat.brief)),
                *(
                    [
                        "",
                        f"This is round {round_number}. A previous design was reviewed and the project owner asked for changes:",
                        f"    {change_request}",
                        "Answer that objection specifically. Do not re-open the parts of the design they did not contest;",
                        "re-litigating what they accepted wastes the round and buries the change they asked for.",
                    ]
                    if change_request
                    else []
                ),
                "",
                "Project context:",
                context.strip() or "(none supplied)",
                "",
                WORKSPACE_PATH_NOTE,
                "",
                RESULT_CONTRACT,
            ]
        )
        units.append(
            WorkUnit(
                unit_id=f"{attempt_id}-{index}-{seat.capability.value}",
                capability=seat.capability.value,
                agent_name=seat.agent_name,
                prompt=prompt,
                via_generalist=seat.agent_name == "general-purpose",
                model=(override.model if override else None) or seat.model,
                role="position",
                focus=seat.focus,
                round=round_number,
                max_tokens=override.max_tokens if override else None,
                reasoning=(override.reasoning if override else None) or "",
            )
        )
    return tuple(units)


def _proposal_scoped_to_plan(
    proposal: CouncilProposal,
    plan: CouncilPlan,
    *,
    change_request: str | None,
) -> CouncilProposal:
    """Trim an approved proposal to the depth the person selected.

    The preview normally opens at medium depth. A light answer must therefore
    keep only the first approved position, while a heavy answer must never
    invent seats that were absent from the approved card. Red team and chair
    remain represented by ``proposal.chair`` and are never trimmed.
    """
    limit = _refinement_positions(
        depth_policy(plan.depth).max_positions,
        change_request=change_request,
    )
    return replace(proposal, positions=proposal.positions[:limit])


def _owner_note(override: ParticipantSettings | None, prefill: str) -> str:
    """The owner's note to one participant, or nothing.

    The card prefills the instructions box with the seat's brief, so an
    untouched box comes back byte-identical to the suggestion. Quoting that
    into the prompt as the owner's words would attribute the roster writer's
    text to a person, and say it twice.
    """
    if override is None:
        return ""
    note = override.instructions.strip()
    if note.casefold() == (prefill or "").strip().casefold():
        return ""
    return note


def _unit_with_settings(unit: WorkUnit, override: ParticipantSettings | None, *, prefill: str = "") -> WorkUnit:
    """One capability-selected unit, carrying the owner's edits for its seat."""
    if override is None:
        return unit
    note = _owner_note(override, prefill)
    return replace(
        unit,
        model=override.model or unit.model,
        max_tokens=override.max_tokens if override.max_tokens is not None else unit.max_tokens,
        reasoning=override.reasoning or unit.reasoning,
        prompt="\n".join([unit.prompt, *owner_instruction_lines(note)]) if note else unit.prompt,
    )


def _stage_worker_config(base_config, budget: WorkerBudget):
    """Clamp a stage worker and prevent implicit inheritance of every skill."""
    skills = [] if base_config.skills is None else list(base_config.skills)
    return replace(
        base_config,
        max_turns=min(base_config.max_turns, budget.max_turns),
        timeout_seconds=min(
            base_config.timeout_seconds,
            budget.timeout_seconds,
        ),
        skills=skills,
    )


def _learn_synthesis_payload(
    results: Sequence[dict[str, Any]],
    *,
    test_outcome: str,
    fallback_summary: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Derive bounded candidates only from trustworthy structured Learn output."""
    grade = "supported" if test_outcome == "supported" else "valid_negative" if test_outcome == "not_supported" else ""
    candidates: list[dict[str, Any]] = []
    summaries: list[str] = []
    if grade:
        for result in results:
            if not result.get("is_trustworthy"):
                continue
            summary = str(result.get("summary") or "").strip()
            if summary:
                summaries.append(summary)
            evidence = list(result.get("evidence_refs") or [])
            limitations = list(result.get("limitations") or [])
            for value in list(result.get("claims") or []):
                statement = str(value).strip()
                if statement and evidence:
                    candidates.append(
                        {
                            "statement": statement,
                            "evidence": evidence,
                            "limitations": limitations,
                            "grade": grade,
                        }
                    )
    return (
        "\n\n".join(summaries) or fallback_summary or "Learn completed without a promotable candidate.",
        candidates,
    )


#: How many Design rounds are numbered. The cap does not stop a reviewer from
#: asking for changes again — it stops the numbering from claiming a depth of
#: debate the budget never funded.
MAX_DESIGN_ROUNDS = 5

_CHANGE_REQUEST_CHARS = 2_000


def _design_reviews(activity: Any) -> list[dict[str, Any]]:
    """Design-stage review decisions, oldest first. Never raises."""
    if not isinstance(activity, Sequence) or isinstance(activity, str):
        return []
    reviews: list[dict[str, Any]] = []
    for item in activity:
        if not isinstance(item, dict) or item.get("event_type") != "stage.reviewed":
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict) or payload.get("stage") != "design":
            continue
        reviews.append(payload)
    return reviews


def _change_request(activity: Any) -> str | None:
    """The objection the council should be answering, if there is one.

    Only the *latest* Design review counts, and only when it asked for changes.
    An approval clears a previous objection — otherwise a cycle re-opened for an
    unrelated reason would keep arguing about something already settled — and a
    rejection is not "try again addressing this", it ends the attempt.
    """
    reviews = _design_reviews(activity)
    if not reviews:
        return None
    latest = reviews[-1]
    # ``stage.reviewed`` records the review decision (``request_changes``),
    # while older projections used the resulting stage status
    # (``changes_requested``). Read both so an actual review event opens the
    # refinement round and existing records remain valid.
    if str(latest.get("decision") or "") not in {"request_changes", "changes_requested"}:
        return None
    rationale = str(latest.get("rationale") or "").strip()[:_CHANGE_REQUEST_CHARS]
    return rationale or None


def _design_round(activity: Any) -> int:
    """Which round of this Design debate the next attempt is."""
    requested = sum(1 for payload in _design_reviews(activity) if str(payload.get("decision") or "") in {"request_changes", "changes_requested"})
    return min(requested + 1, MAX_DESIGN_ROUNDS)


def _refinement_positions(max_positions: int, *, change_request: str | None) -> int:
    """How wide a refinement round should be.

    Narrower than a first pass, deliberately. A reviewer who objected to one
    thing is owed an answer to that thing, and re-opening the full debate spends
    a second council's budget re-litigating the parts they accepted. Never below
    two, because a refinement with a single voice and a red team is still a
    debate and one with a single voice alone is not.
    """
    if not change_request:
        return max_positions
    return max(2, min(max_positions, 2))


#: One-slip misspellings of "restart" (dropped, transposed, or swapped letter),
#: recognized the same narrow way the classifier recognizes "similate": each is
#: an enumerated literal, never a fuzzy match, so the trigger stays auditable.
#: "restate" is deliberately absent — it is a real word asking to rephrase.
_RESTART_TYPOS = r"restat|restar|restrat|retsart|rstart|resart|retart"

#: Ways of asking for the meeting to be held again. Deterministic, like every
#: other DBTL routing signal: the person whose request was read as "convene four
#: workers" deserves to see the words that did it.
_NEW_DEBATE_PATTERN = re.compile(
    r"\b(?:re-?run|re-?open|re-?start|re-?try|re-?launch|re-?convene|re-?do|repeat|rehold|" + _RESTART_TYPOS + r")\b[^.\n]{0,40}\b(?:meeting|debate|discussion|council|round)\b"
    r"|\b(?:run|hold|convene|start|open|schedule)\b[^.\n]{0,40}\b(?:another|a new|a second|again)\b[^.\n]{0,20}\b(?:meeting|debate|discussion|council|round)\b"
    r"|\b(?:another|a second|a new|one more)\s+(?:round|meeting|debate|discussion)\b"
    r"|\b(?:meet|debate|discuss|argue)\s+(?:it\s+)?again\b"
    r"|\b(?:meeting|debate|discussion|council)\s+again\b",
    re.IGNORECASE,
)


def _wants_new_debate(request_text: str) -> bool:
    """Whether the request asks for the meeting to be convened again.

    The default is *not* to convene. A Design stage stays ``in_progress`` until
    a person submits it for review, so before this rule every later message in
    the cycle re-ran the whole meeting — the owner would answer one question and
    watch four fresh workers argue the design they had just been handed.
    Convening several workers is expensive and slow enough that it should be
    something a person asked for.
    """
    return bool(_NEW_DEBATE_PATTERN.search((request_text or "").strip()))


#: The refinement round the review endpoint dispatches for the reviewer, sent
#: as a hidden kickoff message. Recognised deterministically so "request
#: changes" still convenes without a model: an interpreter that happens to be
#: unavailable must not cost a reviewer the round their verdict asked for.
_REFINEMENT_KICKOFF_PREFIX = "refine the approved design candidate for"


def _is_refinement_kickoff(request_text: str) -> bool:
    """Whether this request is the server's own post-verdict refinement."""
    return (request_text or "").strip().lower().startswith(_REFINEMENT_KICKOFF_PREFIX)


def _unreviewed_design_package(cycle: dict[str, Any]) -> dict[str, Any] | None:
    """A Design package this cycle already has and nobody has contested.

    Both ``in_progress`` and ``changes_requested`` count as "there is a package
    on the table". ``changes_requested`` used to yield ``None`` on the grounds
    that the verdict *is* the request to argue again — true of the verdict, but
    not of every message that arrives afterwards. Because the hold was skipped
    entirely for that status, a cycle sitting in changes-requested convened a
    round for *anything* sent to it, including "hello". The reviewer's round is
    dispatched once, by the review endpoint's own kickoff
    (:func:`_is_refinement_kickoff`); a person typing later has to ask.
    """
    attempt = _stage_attempt(cycle, "design")
    if not isinstance(attempt, dict) or str(attempt.get("status") or "") not in {
        StageStatus.IN_PROGRESS.value,
        StageStatus.CHANGES_REQUESTED.value,
    }:
        return None
    attempt_id = str(attempt.get("id") or "")
    if not attempt_id:
        return None
    artifacts = cycle.get("artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, str):
        return None
    newest: dict[str, Any] | None = None
    for item in artifacts:
        if not isinstance(item, dict) or str(item.get("stage_attempt_id") or "") != attempt_id:
            continue
        if newest is None or int(item.get("revision") or 0) > int(newest.get("revision") or 0):
            newest = item
    if newest is None:
        return None
    return {"uri": str(newest.get("uri") or ""), "revision": int(newest.get("revision") or 0)}


def _pending_design_question(prior_runs: Sequence[dict[str, Any]]) -> str | None:
    """The question the meeting's chair last asked and nobody has answered yet.

    Read from the newest chair run only. An older unanswered question that a
    later completed synthesis moved past is not pending, and resuming on it
    would put the meeting back in front of a decision it already made.
    """
    for item in reversed(list(prior_runs)):
        if str(item.get("capability") or "") != "design_council_chair":
            continue
        payload = item.get("result")
        payload = payload if isinstance(payload, dict) else {}
        question = str(payload.get("clarification_question") or "").strip()
        return question or None
    return None


#: How much of an already-argued position the resuming chair is shown. Generous:
#: it is re-reading what it weighed before, not summarising it for a person.
_RESUMED_POSITION_CHARS = 6_000
_MAX_RESUMED_POSITIONS = 8


def _prior_positions(prior_runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The positions already argued, for a chair resuming after a question."""
    positions: list[dict[str, Any]] = []
    for item in prior_runs:
        capability = str(item.get("capability") or "")
        if capability == "design_council_chair":
            continue
        payload = item.get("result")
        payload = payload if isinstance(payload, dict) else {}
        positions.append(
            {
                "unit_id": str(item.get("unit_id") or ""),
                "capability": capability,
                "status": str(item.get("status") or payload.get("status") or ""),
                "summary": _bounded_text(payload.get("summary"), max_chars=_RESUMED_POSITION_CHARS),
                "claims": [str(value) for value in list(payload.get("claims") or [])[:12]],
                "limitations": [str(value) for value in list(payload.get("limitations") or [])[:6]],
                "evidence_refs": list(payload.get("evidence_refs") or [])[:12],
            }
        )
    return positions[-_MAX_RESUMED_POSITIONS:]


def _prior_chair_execution(prior_runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Execution dials from the chair run that most recently paused.

    The approved preflight remains the primary source on an immediate resume.
    These durable fields are the fallback after message compaction or a later
    process restart, when the card may no longer be available in state.
    """
    for item in reversed(list(prior_runs)):
        if str(item.get("capability") or "") != "design_council_chair":
            continue
        payload = item.get("result")
        payload = payload if isinstance(payload, dict) else {}
        execution = payload.get("execution")
        restored = dict(execution) if isinstance(execution, Mapping) else {}
        restored.setdefault("agent_name", str(item.get("agent_name") or ""))
        restored.setdefault("via_generalist", bool(item.get("via_generalist", False)))
        return restored
    return {}


def _approved_design_brief(cycle: dict[str, Any]) -> dict[str, Any] | None:
    """The Design package a human approved, for the stages that implement it.

    Build, Test, and Learn received the datasets and the reconciliation matrix
    but not the design those exist to serve, so a Build worker had to re-derive
    the study's intent from the cycle title.

    Two rules make carrying it safe. Only an **approved** design travels — an
    unapproved one would let later work proceed from something nobody agreed
    to, which is the gate this whole workflow is built around. And the content
    hash travels with the URI, because the approval bound a specific document
    and a stage naming only the path could silently work from a later revision
    of it.

    Never raises: this runs on every Build/Test/Learn request and must not be
    the reason a stage cannot run.
    """
    stages = cycle.get("stages")
    if not isinstance(stages, Sequence) or isinstance(stages, str):
        return None
    attempt_id = ""
    for item in stages:
        if isinstance(item, dict) and item.get("stage") == "design" and str(item.get("status") or "") == "approved":
            attempt_id = str(item.get("id") or "")
            break
    if not attempt_id:
        return None

    artifacts = cycle.get("artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, str):
        return None
    newest: dict[str, Any] | None = None
    for item in artifacts:
        if not isinstance(item, dict) or str(item.get("stage_attempt_id") or "") != attempt_id:
            continue
        if newest is None or int(item.get("revision") or 0) > int(newest.get("revision") or 0):
            newest = item
    if newest is None:
        return None
    return {
        "uri": str(newest.get("uri") or ""),
        "content_hash": str(newest.get("content_hash") or ""),
        "revision": int(newest.get("revision") or 0),
        "artifact_type": str(newest.get("artifact_type") or ""),
    }


def _stage_attempt(cycle: dict[str, Any], stage: str) -> dict[str, Any] | None:
    for item in cycle.get("stages") or []:
        if isinstance(item, dict) and item.get("stage") == stage:
            return item
    return None


def _executable_stage(cycle: Mapping[str, Any]) -> str | None:
    """Select the stage rows say is active, with cycle state as fallback.

    Older one-click Build approvals could leave ``cycle.state='build'`` while
    atomically marking Build approved and Test in progress. Trusting only the
    checkpoint in that recoverable shape reruns Build. A single active stage
    row is the more specific durable fact; ambiguous or legacy projections
    still fall back to the state machine's checkpoint.
    """
    state = str(cycle.get("state") or "")
    fallback = "build" if state == "ready_for_build" else stage_for_state(state)
    # A stale stage row must never reopen a terminal/unknown cycle. Recovery is
    # only a tie-breaker between executable checkpoints.
    if fallback is None:
        return None
    stages = cycle.get("stages")
    if not isinstance(stages, Sequence) or isinstance(stages, (str, bytes)):
        return fallback
    active = [
        str(item.get("stage") or "")
        for item in stages
        if isinstance(item, Mapping) and str(item.get("stage") or "") in {"design", "reconciliation", "build", "test", "learn"} and str(item.get("status") or "") in {StageStatus.IN_PROGRESS.value, StageStatus.CHANGES_REQUESTED.value}
    ]
    if len(active) == 1:
        return active[0]
    return fallback


def _stage_handoff_refusal(
    cycle: Mapping[str, Any],
    *,
    expected_db_revision: int,
    expected_stage: str,
) -> str | None:
    """Refuse a card whose recorded approval no longer names current state."""
    current_revision = int(cycle.get("db_revision") or 0)
    if current_revision != expected_db_revision:
        return f"This next-stage prompt was created at cycle revision {expected_db_revision}, but the cycle is now at revision {current_revision}. Nothing was started; open the current cycle state and try again."
    current_stage = _executable_stage(cycle)
    if current_stage != expected_stage:
        return f"This prompt offered {expected_stage.title()}, but the cycle's current executable stage is {current_stage.title() if current_stage else 'none'}. Nothing was started."
    return None


def _safe_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


#: What the sandbox calls the project root. The manifest lists the human-visible
#: folder, but a worker can only *read* through the virtual path, so the two must
#: be joined before the listing is shown to anyone who will act on it.
WORKSPACE_VIRTUAL_ROOT = "/mnt/user-data"


def _project_manifest(project_root: str, *, limit: int = 120) -> list[dict[str, Any]]:
    """Return a bounded, metadata-only view of the human-visible project folder.

    Paths are emitted as the **virtual paths a worker can actually open**
    (``/mnt/user-data/...``), not as paths relative to the project root.
    ``read_file`` rejects anything outside the virtual prefix, so a relative
    listing was an invitation to a permission error: a whole design meeting
    reported "every file read was denied", each participant returned no
    result, and the chair could only record that it had nothing to synthesize
    from. The listing is the only place most workers learn a path exists, so
    it has to name the path in the form they can use.
    """
    root = Path(project_root).expanduser().resolve()
    ignored = {".git", ".greenagent", "node_modules", "__pycache__"}
    entries: list[dict[str, Any]] = []
    try:
        paths = sorted(root.rglob("*"), key=lambda item: item.as_posix())
    except OSError:
        return entries
    for path in paths:
        if len(entries) >= limit:
            break
        try:
            relative = path.relative_to(root)
            if any(part in ignored for part in relative.parts):
                continue
            stat = path.stat()
        except (OSError, ValueError):
            continue
        entries.append(
            {
                "path": f"{WORKSPACE_VIRTUAL_ROOT}/{relative.as_posix()}",
                "kind": "directory" if path.is_dir() else "file",
                "size_bytes": 0 if path.is_dir() else stat.st_size,
            }
        )
    return entries


def _project_file_snapshot(project_root: str, *, limit: int = 5_000) -> dict[str, tuple[int, int]]:
    """Remember which project files existed before Build touched the workspace.

    Build owns input discovery when Reconciliation is optional.  The worker
    reports the files it actually examined and the server binds their bytes,
    but only files present in this pre-run snapshot qualify as inputs.  That
    keeps a newly generated model or report from being mistaken for source
    data and lets us refuse a source that changed during the run.
    """
    root = Path(project_root).expanduser().resolve()
    ignored = {".git", ".greenagent", "node_modules", "__pycache__"}
    snapshot: dict[str, tuple[int, int]] = {}
    try:
        paths = sorted(root.rglob("*"), key=lambda item: item.as_posix())
    except OSError:
        return snapshot
    for path in paths:
        if len(snapshot) >= limit:
            break
        try:
            relative = path.relative_to(root)
            if any(part in ignored for part in relative.parts) or not path.is_file():
                continue
            stat = path.stat()
        except (OSError, ValueError):
            continue
        snapshot[relative.as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_relative_path(reference: str, *, project_root: str) -> tuple[str, Path] | None:
    """Resolve one worker-authored workspace reference without escaping scope."""
    value = reference.strip()
    if not value:
        return None
    virtual_prefix = f"{WORKSPACE_VIRTUAL_ROOT}/"
    if value.startswith(virtual_prefix):
        relative = value[len(virtual_prefix) :]
    elif value.startswith("/") or "://" in value:
        return None
    else:
        relative = value
    root = Path(project_root).expanduser().resolve()
    candidate = (root / relative).resolve()
    try:
        normalized = candidate.relative_to(root).as_posix()
    except ValueError:
        return None
    return normalized, candidate


def _build_input_artifacts(
    *,
    datasets: Sequence[Mapping[str, Any]],
    results: Sequence[StageWorkerResult],
    project_root: str,
    pre_run_files: Mapping[str, tuple[int, int]],
) -> list[str]:
    """Bind Build's actual inputs without a separate declaration ceremony.

    Required-Reconciliation deployments continue to contribute their durable
    dataset bindings.  In optional mode, exact workspace paths come from the
    validated worker contract and hashes are computed by the server, never
    requested from the person running the cycle.
    """
    artifacts: list[str] = []
    for item in datasets:
        source_key = str(item.get("source_key") or "").strip()
        content_hash = str(item.get("content_hash") or "").strip().lower()
        if source_key and re.fullmatch(r"[0-9a-f]{64}", content_hash):
            artifacts.append(f"dataset:{source_key}:{content_hash}")

    references: list[str] = []
    for result in results:
        inputs_examined = result.provenance.get("inputs_examined", ())
        if isinstance(inputs_examined, Sequence) and not isinstance(inputs_examined, (str, bytes)):
            references.extend(str(item) for item in inputs_examined if isinstance(item, str))
        references.extend(ref.reference for ref in result.evidence_refs if ref.kind in {"workspace_file", "dataset"})

    for reference in references:
        resolved = _workspace_relative_path(reference, project_root=project_root)
        if resolved is None:
            continue
        relative, path = resolved
        before = pre_run_files.get(relative)
        if before is None:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if (stat.st_size, stat.st_mtime_ns) != before:
            raise ValueError(f"Build input {relative!r} changed during execution; rerun Build from an unchanged source file.")
        artifacts.append(f"workspace_file:{relative}:sha256:{_sha256_file(path)}")

    return list(dict.fromkeys(artifacts))


def _design_chair_unit(
    outcome: StageExecutionOutcome,
    *,
    attempt_id: str,
    stage_context: str,
    council: CouncilPlan | None = None,
    settings: Mapping[str, ParticipantSettings] | None = None,
) -> WorkUnit | None:
    if not outcome.plan.units:
        return None
    override = (settings or {}).get("chair")
    positions = json.dumps(
        [item.as_dict() for item in outcome.results],
        sort_keys=True,
        ensure_ascii=False,
    )
    first = outcome.plan.units[0]
    chair_seat = next(
        (seat for seat in (council.seats if council is not None else ()) if seat.role is CouncilRole.CHAIR),
        None,
    )
    prompt = "\n".join(
        [
            "You chair the design meeting for this DBTL research cycle.",
            "",
            "Project context:",
            stage_context,
            "",
            WORKSPACE_PATH_NOTE,
            "",
            "independent meeting positions:",
            positions,
            "",
            "Debate instructions:",
            "- Compare disagreements, assumptions, risks, and evidence across the positions.",
            "- Do not average incompatible positions; explain the tradeoff.",
            "- If one project-owner decision is required, return status needs_input and ask exactly one focused clarification_question.",
            "- Otherwise return status completed with an operational design synthesis, explicit success and rejection criteria, and a recommendation to present it for human review.",
            "- You may recommend readiness, but you cannot submit, approve, or advance the stage.",
            *owner_instruction_lines(chair_seat.instructions if chair_seat is not None else ""),
            "",
            "Result rules (these are validated, not stylistic):",
            "- Every entry in claims must be traceable to an entry in evidence_refs. A claim with no evidence rejects the whole result, so cite the meeting position it came from or move it to summary.",
            "- An evidence_refs entry needs a kind of artifact, workspace_file, dataset, or external, plus a non-empty reference. A meeting position is kind 'external' with the position's unit id as its reference.",
            '- quality_checks[].passed must be a JSON boolean, not the string "true".',
            "- needs_input requires a non-empty clarification_question; every other status requires it to be omitted or null.",
            "",
            "Return one JSON object and nothing else. The arrays below are shown empty only to give the shape; fill them in:",
            """{
  "status": "completed" | "needs_input" | "blocked" | "failed",
  "summary": "the meeting synthesis",
  "artifact_refs": [],
  "claims": [],
  "evidence_refs": [],
  "limitations": [],
  "quality_checks": [{"name": "check", "passed": true, "detail": ""}],
  "recommended_next_actions": [],
  "clarification_question": "required only for needs_input",
  "provenance": {"inputs_examined": [], "tools_used": []}
}""",
            "",
            CONSENSUS_CONTRACT,
            "",
            DECISION_REQUEST_CONTRACT,
        ]
    )
    unit = WorkUnit(
        unit_id=f"{attempt_id}-chair",
        capability="design_council_chair",
        agent_name=chair_seat.agent_name if chair_seat is not None else first.agent_name,
        prompt=prompt,
        via_generalist=chair_seat.via_generalist if chair_seat is not None else first.via_generalist,
        model=chair_seat.model if chair_seat is not None else first.model,
        role="chair",
        focus=(chair_seat.focus if chair_seat is not None and chair_seat.focus else "weighs the positions against each other"),
        round=first.round,
        max_tokens=chair_seat.max_tokens if chair_seat is not None else None,
        reasoning=chair_seat.reasoning if chair_seat is not None else "",
    )
    if chair_seat is not None:
        return unit
    return _unit_with_settings(
        unit,
        override,
        prefill=ROLE_BRIEFS[CouncilRole.CHAIR],
    )


def _resumed_chair_unit(
    council: CouncilPlan | None,
    *,
    attempt_id: str,
    stage_context: str,
    positions: Sequence[Mapping[str, Any]],
    question: str,
    answer: str,
    round_number: int,
    objection: str = "",
    settings: Mapping[str, ParticipantSettings] | None = None,
    prior_execution: Mapping[str, Any] | None = None,
) -> WorkUnit | None:
    """The chair, resuming the meeting it paused — no new positions dispatched.

    ``objection`` switches this to the other single-chair round: a reviewer
    asked for changes, and the reading of their objection said the recorded
    positions already contain what is needed to answer it. Same mechanism,
    different framing — the chair re-weighs the positions it already had, so
    the two share one prompt body rather than drifting into two.

    A chair that returns ``needs_input`` has not failed and has not finished; it
    is waiting. Re-running the whole meeting on the answer spends a second
    meeting's budget re-arguing the parts nobody questioned, and it reads to the
    owner as the meeting ignoring them and starting over. The positions it
    weighed are already durable, so the answer plus those positions is enough to
    finish the synthesis.

    Returns ``None`` when there is no seat to resume into, so the caller falls
    back to convening normally rather than dropping the turn.
    """
    if not positions:
        return None
    seat = next((item for item in (council.seats if council is not None else ()) if item.role is CouncilRole.CHAIR), None)
    if seat is None:
        return None
    prior = prior_execution or {}
    prior_model = str(prior.get("model") or "").strip() or None
    prior_agent = str(prior.get("agent_name") or "").strip() or seat.agent_name
    prior_reasoning = str(prior.get("reasoning") or "").strip()
    raw_tokens = prior.get("max_tokens")
    prior_tokens = raw_tokens if isinstance(raw_tokens, int) and not isinstance(raw_tokens, bool) and raw_tokens > 0 else None
    if objection:
        framing = [
            "You chair the design meeting for this DBTL research cycle, and you are revising its conclusion.",
            "",
            "A human reviewer read the design you wrote and asked for changes, quoted exactly:",
            '"""',
            objection,
            '"""',
            "",
            "Their request is a decision, not a suggestion. Revise the synthesis so it answers them.",
            "The meeting has not been re-run: the positions below are the ones you already weighed.",
            "Change what their request touches and leave the settled parts of the debate alone.",
        ]
    else:
        framing = [
            "You chair the design meeting for this DBTL research cycle, and you are resuming it.",
            "",
            "You previously paused and asked the project owner one question:",
            f"    {question}",
            "",
            "They answered, quoted exactly:",
            '"""',
            answer,
            '"""',
            "",
            "Their answer is a decision, not a suggestion. Treat it as settled and synthesize on top of it.",
            "The meeting has not been re-run: the positions below are the ones you already weighed.",
            "Do not ask the same question again, and do not re-open the parts of the debate their answer does not touch.",
        ]
    prompt = "\n".join(
        [
            *framing,
            "",
            "Project context:",
            stage_context,
            "",
            WORKSPACE_PATH_NOTE,
            "",
            "Independent meeting positions already argued:",
            json.dumps(list(positions), sort_keys=True, ensure_ascii=False),
            "",
            "Debate instructions:",
            "- Compare disagreements, assumptions, risks, and evidence across the positions.",
            "- Do not average incompatible positions; explain the tradeoff.",
            "- Return status completed with an operational design synthesis, explicit success and rejection criteria, and a recommendation to present it for human review.",
            "- Return needs_input only if their answer created a genuinely new decision that only they can make. Repeating the answered question is not that.",
            "- You may recommend readiness, but you cannot submit, approve, or advance the stage.",
            "",
            "Result rules (these are validated, not stylistic):",
            "- Every entry in claims must be traceable to an entry in evidence_refs. A claim with no evidence rejects the whole result, so cite the meeting position it came from or move it to summary.",
            "- An evidence_refs entry needs a kind of artifact, workspace_file, dataset, or external, plus a non-empty reference. A meeting position is kind 'external' with the position's unit id as its reference.",
            '- quality_checks[].passed must be a JSON boolean, not the string "true".',
            "- needs_input requires a non-empty clarification_question; every other status requires it to be omitted or null.",
            "",
            "Return one JSON object and nothing else. The arrays below are shown empty only to give the shape; fill them in:",
            """{
  "status": "completed" | "needs_input" | "blocked" | "failed",
  "summary": "the meeting synthesis",
  "artifact_refs": [],
  "claims": [],
  "evidence_refs": [],
  "limitations": [],
  "quality_checks": [{"name": "check", "passed": true, "detail": ""}],
  "recommended_next_actions": [],
  "clarification_question": "required only for needs_input",
  "provenance": {"inputs_examined": [], "tools_used": []}
}""",
            "",
            CONSENSUS_CONTRACT,
            "",
            DECISION_REQUEST_CONTRACT,
        ]
    )
    return _unit_with_settings(
        WorkUnit(
            unit_id=f"{attempt_id}-chair",
            capability="design_council_chair",
            agent_name=prior_agent,
            prompt=prompt,
            via_generalist=bool(prior.get("via_generalist", seat.via_generalist)),
            model=prior_model or seat.model,
            role="chair",
            focus=("revises the meeting's conclusion on the reviewer's request" if objection else "resumes the meeting on the owner's answer"),
            round=round_number,
            max_tokens=prior_tokens if prior_tokens is not None else seat.max_tokens,
            reasoning=prior_reasoning or seat.reasoning,
        ),
        (settings or {}).get("chair"),
        prefill=ROLE_BRIEFS[CouncilRole.CHAIR],
    )


def _resumed_selection(
    unit: WorkUnit,
    *,
    positions: Sequence[Mapping[str, Any]],
    revision_reason: str = "",
) -> SelectionResult:
    """Record that a resume happened, and that no new positions were seated.

    Without this the package would list one worker and no explanation, which
    reads as a meeting that lost its participants rather than one that finished
    the synthesis it had already started. ``revision_reason`` says the same
    thing for the other single-chair round — a reviewer asked for changes and
    the reading of their objection said no new argument was needed — and states
    that reading, so a reader can disagree with it.
    """
    opening = (
        f"Revised the existing meeting rather than reconvening it: {revision_reason}"
        if revision_reason
        else "Resumed the existing meeting: the project owner answered the chair's question, so the chair completed the synthesis it had paused."
    )
    return SelectionResult(
        assignments=(),
        notes=(
            opening,
            f"No new positions were dispatched; the chair re-weighed {len(positions)} position(s) already recorded for this cycle.",
            f"Chair: {unit.agent_name} ({unit.model or 'inherited model'}).",
        ),
    )


def _design_red_team_unit(
    outcome: StageExecutionOutcome,
    *,
    attempt_id: str,
    council: CouncilPlan | None = None,
    settings: Mapping[str, ParticipantSettings] | None = None,
) -> WorkUnit | None:
    """Guarantee an adversarial position, however many specialists were selected.

    Its brief is built from the first unit's, so it argues against the same
    stated task rather than a summary of it. It runs after the base round, so
    the chair always has at least one position and one challenge to weigh.
    """
    if not outcome.plan.units:
        return None
    first = outcome.plan.units[0]
    red_team_seat = next(
        (seat for seat in (council.seats if council is not None else ()) if seat.role is CouncilRole.RED_TEAM),
        None,
    )
    prompt = "\n".join(
        [
            first.prompt,
            "",
            "Independent debate role:",
            "Act as the design meeting's red team. Challenge the proposed population, "
            "controls, leakage risks, success threshold, rejection criteria, and hidden "
            "assumptions. Seek a materially different defensible position rather than "
            "agreeing by default.",
            *owner_instruction_lines(red_team_seat.instructions if red_team_seat is not None else ""),
        ]
    )
    unit = WorkUnit(
        unit_id=f"{attempt_id}-red-team",
        capability="design_red_team",
        agent_name=red_team_seat.agent_name if red_team_seat is not None else first.agent_name,
        prompt=prompt,
        via_generalist=red_team_seat.via_generalist if red_team_seat is not None else first.via_generalist,
        model=red_team_seat.model if red_team_seat is not None else first.model,
        role="red_team",
        focus=(red_team_seat.focus if red_team_seat is not None and red_team_seat.focus else "argues against the proposed design"),
        round=first.round,
        max_tokens=red_team_seat.max_tokens if red_team_seat is not None else None,
        reasoning=red_team_seat.reasoning if red_team_seat is not None else "",
    )
    if red_team_seat is not None:
        return unit
    return _unit_with_settings(
        unit,
        (settings or {}).get("red-team"),
        prefill=ROLE_BRIEFS[CouncilRole.RED_TEAM],
    )


#: What each review meeting argues about. The stage's own result is settled by
#: the time this runs, so the question is never "what should we do" — it is
#: whether the recorded evidence supports what it claims.
_REVIEW_MEETING_BRIEFS: Mapping[str, str] = {
    "build": (
        "Review the recorded Build execution: environment, code and config revisions, "
        "plan-vs-actual deviations, versioned outputs, and whether another person could "
        "reproduce it from this record alone. Attack execution risk, not the scientific "
        "design — a design objection belongs in a new Design round, not here."
    ),
    "test": (
        "Review the recorded validity pack: leakage between train and test, fold "
        "construction, holdout handling, plausible performance ceilings, direction of "
        "effect, and reproducibility. The outcome itself is computed from the pack at "
        "review time and is not yours to state or change — argue about whether the pack "
        "supports what it reports."
    ),
    "learn": (
        "Review the provisional candidates against the human-owned Test outcome: is every "
        "candidate traceable to evidence, is any claim broader than what was tested, and "
        "does anything here belong in a later cycle instead. You recommend only — "
        "promotion and publication are separate human decisions you cannot make."
    ),
}

_REVIEW_MEETING_ROLES: tuple[tuple[str, str, str], ...] = (
    (
        "position",
        "reviewer",
        "States independently whether the recorded evidence supports what it claims, naming the specific parts that do and do not.",
    ),
    (
        "red_team",
        "red-team",
        ("Argues the opposite case. Look for the reading of this evidence under which the recorded result does not hold, and state it plainly rather than agreeing by default."),
    ),
    (
        "chair",
        "chair",
        ("Synthesizes the positions. Keep both sides of any disagreement and say how each was settled; do not average incompatible readings into a middle one."),
    ),
)


def _review_meeting_units(
    *,
    stage: str,
    attempt_id: str,
    assignment: Assignment,
    model: str,
    evidence_uri: str,
    evidence_hash: str,
    context: Mapping[str, Any],
) -> tuple[WorkUnit, ...]:
    """One seat per role, all reading the same recorded evidence.

    Deliberately the same disagreement-before-synthesis shape as the Design
    council rather than a single reviewer: one opinion about a validity pack is
    not a review meeting, and a deck rendered from it would have nothing to show
    a person but that opinion.
    """
    brief = _REVIEW_MEETING_BRIEFS[stage]
    header = [
        f"You are one seat in the {stage.title()} review meeting for cycle {context.get('cycle_title') or context.get('cycle_id')}.",
        "",
        f"Research question: {context.get('research_question') or '(not recorded)'}",
        f"Objective: {context.get('objective') or '(not recorded)'}",
        f"Success criteria: {context.get('success_criteria') or '(not recorded)'}",
        "",
        "The evidence under review is already recorded and must not be changed:",
        f"  {evidence_uri}",
        f"  content hash {evidence_hash}",
        WORKSPACE_PATH_NOTE,
        "",
        brief,
        "",
        CONSENSUS_CONTRACT if stage else "",
    ]
    units: list[WorkUnit] = []
    for role, slug, instruction in _REVIEW_MEETING_ROLES:
        units.append(
            WorkUnit(
                unit_id=f"{attempt_id}-review-{slug}",
                capability=f"{stage}_review_{slug.replace('-', '_')}",
                agent_name=assignment.agent_name,
                prompt="\n".join([*header, "", "Your seat:", instruction]),
                via_generalist=assignment.via_generalist,
                model=model,
                role=role,
                focus=instruction.split(".")[0].lower(),
                round=1,
            )
        )
    return tuple(units)


def _write_stage_package(
    *,
    project_root: str,
    cycle: dict[str, Any],
    outcome: StageExecutionOutcome,
    idempotency_key: str,
    council: CouncilPlan | None = None,
    authored_design: str | None = None,
) -> tuple[str, str, str]:
    """Write the review package and return the URI/hash of the reviewed document.

    Two files are written: the structured JSON that gates and later phases need,
    and the Markdown rendering a person actually reads. **The Markdown is the
    returned artifact**, so the approval binds to the document that was read
    rather than to a machine record nobody opened. The Markdown names the JSON
    and its hash, so the audit chain stays intact in one direction.
    """
    root = Path(project_root).expanduser().resolve()
    ensure_project_dirs(root)
    payload = {
        "schema": outcome.plan.spec.output_schema,
        "stage_spec_key": outcome.plan.spec.spec_key,
        "cycle_id": cycle["id"],
        "project_id": cycle["project_id"],
        "cycle_db_revision": cycle["db_revision"],
        "selection": outcome.plan.selection.as_dict(),
        "results": [item.as_dict() for item in outcome.results],
        "rejected": list(outcome.rejected),
        "token_usage": outcome.token_usage,
        "satisfies_gate": False,
    }
    if council is not None:
        # The depth is a parameter of this attempt, not of the versioned
        # contract, so it is recorded here rather than by forking the spec.
        # Without it the budget a reviewer reconstructs from `stage_spec_key`
        # would not be the budget the workers actually had.
        payload["council"] = council.as_dict()
        if council.depth is CouncilDepth.LIGHT:
            fallback_used = any(item.agent_name == _LIGHT_PILOT_FALLBACK_AGENT for item in outcome.results)
            payload["pilot_review"] = {
                "mode": "light",
                "fallback_used": fallback_used,
                "strict_evidence_complete": all(item.is_trustworthy for item in outcome.results),
                "preexisting_data_required": False,
                "execution_tools_required": False,
                "advancement": "eligible_for_human_approval",
            }
    if authored_design:
        # Kept as its own key rather than folded into ``results``: a synthetic
        # worker entry would put a person's words behind an agent's name in the
        # audit record, which is the one thing the roster work exists to make
        # impossible.
        payload["authored_design"] = authored_design
        payload["authored_by"] = "human"
    encoded = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    data_hash = hashlib.sha256(encoded).hexdigest()

    stage = outcome.plan.spec.stage
    revision = cycle.get("db_revision")
    # Named for a person browsing the project folder, not for a machine: the
    # cycle's own title leads, and the content suffix only disambiguates.
    stage_dir = stage_output_dir(
        cycle_id=str(cycle["id"]),
        cycle_title=str(cycle.get("title") or ""),
        stage=stage,
    )
    data_relative = stage_dir / stage_file_name(
        stage=stage,
        kind="package",
        revision=revision,
        content_hash=data_hash,
    )

    document = render_review_markdown(
        payload,
        data_filename=data_relative.name,
        data_hash=data_hash,
    ).encode("utf-8")
    document_hash = hashlib.sha256(document).hexdigest()
    document_relative = stage_dir / stage_file_name(
        stage=stage,
        kind="review",
        revision=revision,
        content_hash=document_hash,
    )

    outputs = project_outputs_dir(root)
    for relative, content in ((data_relative, encoded), (document_relative, document)):
        _atomic_write(outputs / relative, content)

    uri = f"/mnt/user-data/outputs/{document_relative.as_posix()}"
    digest = render_stage_digest(payload, document_path=f"outputs/{document_relative.as_posix()}")
    return uri, document_hash, digest


@dataclass(frozen=True, slots=True)
class _FeedbackSurfacePlan:
    """What a deck will be registered as, decided before it is rendered."""

    surface_id: str
    mode: str
    #: Which DBTL stage this deck speaks for. A verdict recorded against
    #: another stage's attempt is a verdict on the wrong document, so this
    #: travels with the plan rather than being assumed by the writer.
    stage: str
    stage_attempt_id: str
    originating_thread_id: str
    round_number: int
    evidence: Mapping[str, Any] | None = None
    evidence_content_hash: str = ""
    decision_request: Mapping[str, Any] | None = None
    chair_worker_run_id: str | None = None

    @property
    def answerable(self) -> bool:
        """Whether this deck should carry a bridge at all.

        A ``read_only`` deck ships with no bridge script whatsoever rather than
        a disabled one: the safest version of "this file cannot answer" is a
        file containing no code that could.
        """
        # Design still uses its registered feedback deck during the migration
        # window. Build/Test/Learn decisions now live in chat cards; their
        # decks remain useful evidence but must contain no bridge that can turn
        # the right-side artifact viewer into a second authority surface.
        return self.stage == "design" and self.mode in {
            "chair_feedback",
            "stage_review",
        }


@dataclass(frozen=True, slots=True)
class RenderedDeck:
    """A written deck and the hash of the exact bytes written.

    The hash is returned rather than recomputed by the caller because the file
    on disk is what a person is shown, and a second hash of a second render
    could differ from it without anyone noticing.
    """

    uri: str
    content_hash: str


def _write_council_deck(
    *,
    project_root: str,
    cycle: dict[str, Any],
    results: Sequence[Mapping[str, Any]],
    round_number: int,
    stage: str = "design",
    package_path: str,
    clarification_question: str,
    decision_request: DecisionRequest | None = None,
    surface_id: str = "",
    surface_mode: str = "",
    transition_gate: Mapping[str, Any] | None = None,
) -> RenderedDeck | None:
    """Write the meeting's outcome as a slide deck, beside the review package.

    Deliberately **not** registered as a durable artifact and never returned as
    the reviewed document: an approval must bind to the review Markdown, and a
    second approvable-looking file is exactly how a gate ends up bound to a
    summary of the evidence instead of the evidence. This is a presentation of
    a record that already exists.

    Returns ``None`` rather than raising. A deck that cannot be written costs a
    convenience; letting it fail the turn would cost the meeting whose results
    are already committed by the time this runs.
    """
    try:
        document = render_council_deck(
            cycle_title=str(cycle.get("title") or ""),
            stage_title=f"{stage.title()} meeting",
            round_number=round_number,
            results=results,
            package_path=package_path,
            clarification_question=clarification_question,
            decision_request=decision_request,
            surface_id=surface_id,
            surface_mode=surface_mode,
            transition_gate=transition_gate,
            stage=stage,
        ).encode("utf-8")
    except Exception:  # noqa: BLE001 - a presentation must not break the record
        logger.warning("Could not render the design meeting slide deck.", exc_info=True)
        return None

    content_hash = hashlib.sha256(document).hexdigest()
    try:
        root = Path(project_root).expanduser().resolve()
        ensure_project_dirs(root)
        stage_dir = stage_output_dir(
            cycle_id=str(cycle["id"]),
            cycle_title=str(cycle.get("title") or ""),
            stage=stage,
        )
        relative = stage_dir / stage_file_name(
            stage=stage,
            kind="slides",
            revision=cycle.get("db_revision"),
            content_hash=content_hash,
        )
        _atomic_write(project_outputs_dir(root) / relative, document)
    except Exception:  # noqa: BLE001 - same reason
        logger.warning("Could not write the design meeting slide deck.", exc_info=True)
        return None
    return RenderedDeck(uri=f"/mnt/user-data/outputs/{relative.as_posix()}", content_hash=content_hash)


def _stage_attempt_row_id(cycle: Mapping[str, Any], stage: str) -> str:
    """The durable ``dbtl_stage_runs`` id, not the worker plan's attempt token."""
    stages = cycle.get("stages")
    if not isinstance(stages, Sequence) or isinstance(stages, str):
        return ""
    for item in stages:
        if isinstance(item, dict) and item.get("stage") == stage:
            return str(item.get("id") or "")
    return ""


def _bound_evidence(cycle: Mapping[str, Any], *, artifact_uri: str, content_hash: str) -> Mapping[str, Any] | None:
    """The artifact row a review deck projects, matched by its exact hash.

    Matched on the content hash rather than "the newest artifact", because
    attachment order is not evidence: the deck must bind to the document it was
    rendered from or to nothing at all.
    """
    artifacts = cycle.get("artifacts")
    if not artifacts or not isinstance(artifacts, Sequence) or isinstance(artifacts, str):
        return None
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        if str(item.get("content_hash") or "") == content_hash and str(item.get("uri") or "") == artifact_uri:
            return item
    return None


def _atomic_write(destination: Path, content: bytes) -> None:
    """Write via a temp file in the same directory, then rename."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            temp_path = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
        temp_path = None
    finally:
        if temp_path is not None:
            Path(temp_path).unlink(missing_ok=True)


class LiveStageAdapter:
    """Verify scope, fan out workers, and persist their structured evidence."""

    def __init__(
        self,
        *,
        repo,
        app_config,
        candidate_provider: CandidateProvider | None = None,
        dispatcher: AsyncWorkerDispatcher | None = None,
        runtime_config: RunnableConfig | None = None,
        roster_writer: RosterWriter | None = None,
        intent_interpreter: IntentInterpreter | None = None,
        revision_interpreter: RevisionInterpreter | None = None,
        transition_assessor: TransitionAssessor | None = None,
    ) -> None:
        self._repo = repo
        self._app_config = app_config
        self._candidate_provider = candidate_provider
        self._dispatcher = dispatcher
        self._runtime_config = runtime_config
        # Injected so a test can drive a roster without a model, and so an
        # absent writer degrades to capability selection rather than to nothing.
        self._roster_writer = roster_writer
        # Injected for the same reason; absent, the deterministic phrase table
        # is the whole re-run decision, which is exactly the pre-LLM behavior.
        self._intent_interpreter = intent_interpreter
        # Injected on the same fail-soft contract. Absent, every "request
        # changes" takes the cheap route — the chair revises its own synthesis
        # — because reconvening a whole meeting is the spend this reading
        # exists to justify, and an unavailable reader justifies nothing.
        self._revision_interpreter = revision_interpreter
        self._transition_assessor = transition_assessor

    async def _assess_transition(
        self,
        *,
        stage: str,
        cycle: dict[str, Any],
        evidence_summary: str,
    ) -> TransitionAssessment:
        if not bool(getattr(getattr(self._app_config, "dbtl", None), "progressive_gate", False)):
            return standard_assessment(source="feature_disabled")
        if self._transition_assessor is None:
            return standard_assessment(
                rationale="No transition assessor model is configured, so the standard human-review path is required.",
                source="config_default",
            )
        prompt = build_transition_assessment_prompt(
            stage=stage,
            cycle=cycle,
            evidence_summary=evidence_summary,
        )
        try:
            reply = self._transition_assessor(prompt)
            if isawaitable(reply):
                reply = await reply
            return parse_transition_assessment(reply)
        except Exception:  # noqa: BLE001 - fail safely to standard
            logger.warning("DBTL transition assessment failed; using the standard gate.", exc_info=True)
            return standard_assessment()

    def _runtime(self, config: RunnableConfig) -> dict[str, Any]:
        merged = _runtime_view(self._runtime_config) if self._runtime_config is not None else {}
        merged.update(_runtime_view(config))
        return merged

    async def validate_stage_handoff(
        self,
        *,
        project_id: str | None,
        cycle_id: str | None,
        expected_db_revision: int,
        expected_stage: str,
    ) -> str | None:
        """Validate a durable handoff marker without dispatching workers."""
        if not project_id or not cycle_id:
            return "The handoff no longer has a project-owned cycle. Nothing was started."
        cycle = await self._repo.get_cycle(cycle_id, project_id=project_id)
        if cycle is None:
            return "The handoff's cycle is no longer available in this project. Nothing was started."
        return _stage_handoff_refusal(
            cycle,
            expected_db_revision=expected_db_revision,
            expected_stage=expected_stage,
        )

    async def active_cycle_status(self, *, project_id: str) -> list[dict[str, Any]]:
        """The project's live cycles, as read-only orientation for ordinary work.

        Terminal cycles are omitted: a completed or abandoned record is history,
        and the question this answers is "what governed work is in flight around
        this conversation right now". Nothing here is authority — it is the same
        durable state the project rail shows, handed to the lead agent so it can
        name the boundary rather than discover it by crossing it.
        """
        if not project_id:
            return []
        cycles = await self._repo.list_cycles(project_id)
        live: list[dict[str, Any]] = []
        for cycle in cycles or []:
            if not isinstance(cycle, dict) or str(cycle.get("state") or "") in TERMINAL_CYCLE_STATES:
                continue
            live.append(
                {
                    "cycle_id": str(cycle.get("id") or ""),
                    "title": str(cycle.get("title") or ""),
                    "state": str(cycle.get("state") or ""),
                    "parked": bool(cycle.get("parked")),
                    "stages": {str(item.get("stage") or ""): str(item.get("status") or "") for item in cycle.get("stages", []) if isinstance(item, dict)},
                }
            )
        return live

    async def parked_design_context(
        self,
        *,
        project_id: str | None,
        cycle_id: str | None,
    ) -> dict[str, Any] | None:
        """Return the server-owned unapproved brief for a parked cycle."""
        if not project_id or not cycle_id:
            return None
        cycle = await self._repo.get_cycle(cycle_id, project_id=project_id)
        if not cycle or not cycle.get("parked"):
            return None
        evidence = cycle.get("parked_evidence")
        if not isinstance(evidence, dict) or not evidence.get("content_hash"):
            return None
        return {
            "cycle_id": cycle_id,
            "cycle_title": str(cycle.get("title") or ""),
            "stage": str(cycle.get("parked_stage") or "design"),
            "approval_status": "unapproved",
            "evidence": dict(evidence),
        }

    async def _interpreted_wants_new_debate(self, request_text: str) -> bool:
        """LLM reading of a re-run request the deterministic phrases missed.

        The owner's message is passed verbatim — typos included — because the
        record keeps what was said while interpretation absorbs the errors.
        Only an explicit CONVENE verdict convenes; an absent interpreter, a
        provider failure, or any other reply holds, so routing never depends on
        provider health and a misread can cost at most one rephrase, never a
        council's budget.
        """
        text = (request_text or "").strip()
        if self._intent_interpreter is None or not text:
            return False
        prompt = "\n".join(
            [
                "A design meeting already produced a design that is awaiting human review.",
                "The project owner sent this message (verbatim, may contain typos):",
                "",
                text,
            ]
        )
        try:
            reply = await self._intent_interpreter(prompt)
        except Exception:  # noqa: BLE001 - interpretation failure must hold, never crash the turn
            logger.warning("Debate-intent interpretation failed; holding the design on the table.", exc_info=True)
            return False
        verdict = str(reply or "").strip().split()
        wants = bool(verdict) and verdict[0].strip(".,!:;\"'").upper() == "CONVENE"
        if wants:
            logger.info("Debate-intent interpreter read a cycle-scoped request as asking to re-run the design meeting.")
        return wants

    def _candidates(self) -> Sequence[AgentCandidate]:
        if self._candidate_provider is not None:
            return self._candidate_provider()
        from deerflow.dbtl.capabilities import parse_capabilities
        from deerflow.subagents import get_available_subagent_names, list_subagents

        available = get_available_subagent_names(app_config=self._app_config)
        configs = {item.name: item for item in list_subagents(app_config=self._app_config)}
        declared = {name: parse_capabilities(configs[name].dbtl_capabilities) for name in available if name in configs and configs[name].dbtl_capabilities}
        return build_candidates(
            available,
            declared_capabilities=declared,
        )

    def known_models(self) -> tuple[str, ...]:
        """The configured model names, for the preflight card's model pickers."""
        return self._known_models()

    def _known_models(self) -> tuple[str, ...]:
        app_config = self._app_config
        models = getattr(app_config, "models", None) if app_config is not None else None
        if not models:
            return ()
        return tuple(str(getattr(item, "name", "") or "") for item in models if getattr(item, "name", None))

    def _council_model(self) -> str:
        """The configured default model for meeting seats, if any.

        Validated against the configured model list: an operator typo must not
        become a model name the dispatcher then fails to build. An unrecognized
        value degrades to the old inherit-the-composer behaviour and says so,
        because refusing to convene the meeting over a config typo is a far
        worse trade than running it on the wrong model once.
        """
        configured = str(getattr(getattr(self._app_config, "dbtl", None), "council_model_name", None) or "").strip()
        if not configured:
            return ""
        known = self._known_models()
        if known and configured not in known:
            logger.warning(
                "dbtl.council_model_name %r is not a configured model; meeting seats fall back to the composer's model.",
                configured,
            )
            return ""
        return configured

    async def _propose_roster(
        self,
        *,
        request_text: str,
        stage_context: str,
        max_positions: int,
        adjustment: str | None = None,
    ) -> CouncilProposal | None:
        """Ask for a roster written for this question. Never fatal.

        The call is a ``nostream`` one-shot, so its prompt and raw JSON stay out
        of the conversation. Any failure — no writer configured, a provider
        outage, an unparseable reply — returns ``None`` and the caller falls
        back to capability selection, which is what ran before proposals
        existed. Raising here would trade a better council for no council.

        **A slow reply is a failure too, and it was the loudest one.** This call
        sits in front of the preflight card, so before the timeout a reasoning
        model that took minutes meant the design meeting simply never appeared:
        the user watched "Working…" with nothing on screen, and cancelling was
        the only way out. The whole contract of this module is "degrade, never
        raise" — a wait with no bound degrades to nothing at all, which is the
        one outcome it is not allowed to produce.
        """
        writer = self._roster_writer
        if writer is None:
            return None
        # Filtered at the source so the "known agents" a refusal names is the
        # same list the model was shown; an execution specialist appearing in
        # one but not the other reads as an arbitrary rejection.
        known_agents = seatable_agents(tuple(dict.fromkeys(item.name for item in self._candidates() if item.available)))
        if not known_agents:
            return None
        prompt = build_proposal_prompt(
            request_text=request_text,
            stage_context=stage_context,
            known_agents=known_agents,
            known_models=self._known_models(),
            max_positions=max_positions,
            adjustment=adjustment,
        )
        try:
            reply = writer(prompt)
            if isawaitable(reply):
                reply = await asyncio.wait_for(reply, timeout=ROSTER_PROPOSAL_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning(
                "The Design meeting roster was not proposed within %ss; falling back to capability selection so the preflight card is not held up.",
                ROSTER_PROPOSAL_TIMEOUT_SECONDS,
            )
            return None
        except Exception:
            logger.warning("The Design council roster could not be proposed; falling back to capability selection.", exc_info=True)
            return None
        proposal = parse_council_proposal(
            str(reply or ""),
            known_agents=known_agents,
            known_models=self._known_models(),
            max_positions=max_positions,
        )
        if proposal.rejected:
            logger.info("Design council roster seats refused: %s", "; ".join(proposal.rejected))
        return proposal if proposal.usable else None

    async def preview_council(
        self,
        *,
        project_id: str | None,
        cycle_id: str | None,
        request_text: str,
        config: RunnableConfig,
        adjustment: str | None = None,
    ) -> CouncilPlan | None:
        """The roster this request would convene, without convening it.

        Returns ``None`` whenever there is nothing to preview — no cycle, a
        cycle in this project the caller does not own, or a cycle sitting at a
        stage other than Design. The caller shows a card only when this returns
        a plan, so a preflight can never appear in front of work it does not
        describe.
        """
        if not project_id or not cycle_id:
            return None
        cycle = await self._repo.get_cycle(cycle_id, project_id=project_id)
        if cycle is None:
            return None
        stage = _executable_stage(cycle)
        if stage != "design":
            return None
        attempt = _stage_attempt(cycle, stage)
        status = str((attempt or {}).get("status") or "")
        if status not in {StageStatus.IN_PROGRESS.value, StageStatus.CHANGES_REQUESTED.value}:
            return None
        plan = self._plan_council(
            resolve_stage_spec(stage),
            config=config,
            request_text=request_text,
            attempt_id="preview",
        )
        if plan.human_authored or not plan.dispatchable:
            # Nothing to propose for: one is a deliberate choice to seat nobody,
            # the other cannot run at all. Asked before ``dispatchable`` because
            # they are false for opposite reasons.
            return plan
        # The preview *is* the proposal. Building the card from capability
        # selection while dispatch used a proposed roster meant the card
        # described a council that never convened — and in a generalist-only
        # deployment (the common one) selection shows one undifferentiated seat
        # where four differentiated ones then ran.
        proposal = await self._propose_roster(
            request_text=request_text,
            stage_context=_preview_context(cycle),
            max_positions=depth_policy(plan.depth).max_positions,
            adjustment=adjustment,
        )
        return plan if proposal is None else plan_from_proposal(plan, proposal)

    def _plan_council(
        self,
        spec: StageSpec,
        *,
        config: RunnableConfig,
        request_text: str,
        attempt_id: str,
    ) -> CouncilPlan:
        """The roster this Design run will use.

        Depth comes from the human's confirmed choice when there is one, and
        otherwise from the request itself. An unrecognized value degrades to the
        recommendation rather than raising: a stale client losing a preference
        is a much smaller failure than a cycle that cannot be designed.

        The model a seat runs on defaults to ``dbtl.council_model_name`` rather
        than to the composer's. Inheriting the composer meant a meeting convened
        from an expensive chat quietly ran every unassigned seat on that model —
        the person had chosen a model to *talk* to, not a budget for four
        workers to argue on. A seat that names its own model still wins, and the
        setup card can still override any of them.
        """
        depth = council_depth_from_config(config) or recommend_depth(request_text).depth
        metadata = dict(config.get("metadata", {}) or {})
        model = self._council_model() or str(metadata.get("model_name") or "").strip() or "inherited"
        return plan_council(
            spec,
            self._candidates(),
            depth=depth,
            model=model,
            tools_by_agent=self._declared_tools(),
            attempt_id=attempt_id,
        )

    def _declared_tools(self) -> dict[str, tuple[str, ...]]:
        """Each agent's declared tool whitelist, for the roster preview.

        An agent that declares none inherits the lead agent's tools, which the
        seat reports as ``inherits_all_tools`` rather than as an empty list —
        showing "tools: none" for the common case would be a lie a reviewer
        would act on.
        """
        if self._candidate_provider is not None:
            return {}
        try:
            from deerflow.subagents import list_subagents

            return {item.name: tuple(item.tools) for item in list_subagents(app_config=self._app_config) if item.tools}
        except Exception:  # pragma: no cover - registry problems must not block a run
            logger.debug("Could not read declared subagent tools for the council roster.", exc_info=True)
            return {}

    def _production_dispatcher(
        self,
        *,
        config: RunnableConfig,
        state: dict[str, Any],
        project_id: str,
        project_root: str,
        stage: str = "design",
        meeting: bool = True,
    ) -> AsyncWorkerDispatcher:
        runtime = self._runtime(config)
        metadata = dict(config.get("metadata", {}) or {})

        async def dispatch(
            units: Sequence[WorkUnit],
            *,
            budget: WorkerBudget,
        ) -> Sequence[DispatchOutcome]:
            return await self._dispatch_units(
                units,
                budget=budget,
                config=config,
                state=state,
                runtime=runtime,
                metadata=metadata,
                project_id=project_id,
                project_root=project_root,
                stage=stage,
                meeting=meeting,
            )

        return dispatch

    async def _dispatch_units(
        self,
        units: Sequence[WorkUnit],
        *,
        budget: WorkerBudget,
        config: RunnableConfig,
        state: dict[str, Any],
        runtime: dict[str, Any],
        metadata: dict[str, Any],
        project_id: str,
        project_root: str,
        stage: str,
        meeting: bool,
    ) -> Sequence[DispatchOutcome]:
        from langgraph.config import get_stream_writer

        from deerflow.subagents import SubagentExecutor, get_subagent_config
        from deerflow.subagents.config import resolve_subagent_model_name
        from deerflow.subagents.executor import SubagentResult, SubagentStatus
        from deerflow.tools import get_available_tools
        from deerflow.utils.custom_events import aemit_custom_event

        try:
            writer = get_stream_writer()
        except RuntimeError:
            writer = None

        async def emit(payload: dict[str, Any]) -> None:
            if writer is not None:
                await aemit_custom_event(payload, writer=writer)

        async def run_one(unit: WorkUnit) -> DispatchOutcome:
            base_config = get_subagent_config(
                unit.agent_name,
                app_config=self._app_config,
            )
            if base_config is None:
                return DispatchOutcome(
                    unit_id=unit.unit_id,
                    text=None,
                    error=f"Selected subagent {unit.agent_name!r} is no longer registered.",
                )
            worker_config = _stage_worker_config(base_config, budget)
            parent_model = metadata.get("model_name")
            # A seat that named its own model wins over the composer's. The name
            # was validated against the configured set when the roster was
            # parsed, so an unrecognized one cannot arrive here; falling back to
            # the parent keeps an unset seat behaving exactly as before.
            effective_model = unit.model or resolve_subagent_model_name(
                worker_config,
                str(parent_model) if parent_model else None,
                app_config=self._app_config,
            )
            # Pin the same effective model onto the executor config. Previously
            # only tool loading and stream labels used ``unit.model`` while the
            # executor still resolved ``model="inherit"`` from the composer's
            # parent model, so the UI could say Claude while the worker called
            # Codex.
            worker_config = replace(worker_config, model=effective_model)
            tools = get_available_tools(
                model_name=effective_model,
                groups=metadata.get("tool_groups"),
                subagent_enabled=False,
                include_upload_tool=False,
                app_config=self._app_config,
            )
            tools = _tools_for_stage_budget(tools, budget)
            trace_id = str(metadata.get("trace_id") or "") or None
            # A stage worker is graded on its final message, but the turn budget
            # is enforced by ``recursion_limit``, which aborts from inside a tool
            # loop — so a worker that spends its budget could never land the JSON
            # its result is parsed from. The deadline reserves the last few model
            # calls for writing that answer.
            deadline = FinalizationDeadlineMiddleware(
                # Config may impose a lower per-agent turn limit than the
                # versioned stage budget. Derive the deadline from the limit
                # the executor will actually enforce.
                max_model_calls=_model_call_budget(worker_config.max_turns),
            )
            executor = SubagentExecutor(
                config=worker_config,
                tools=tools,
                app_config=self._app_config,
                parent_model=str(parent_model) if parent_model else None,
                sandbox_state=state.get("sandbox"),
                thread_data=state.get("thread_data"),
                thread_id=str(runtime.get("thread_id") or "") or None,
                trace_id=trace_id,
                user_id=str(runtime.get("user_id") or "") or None,
                user_role=str(runtime.get("user_role") or "") or None,
                oauth_provider=str(runtime.get("oauth_provider") or "") or None,
                oauth_id=str(runtime.get("oauth_id") or "") or None,
                run_id=str(runtime.get("run_id") or "") or None,
                channel_user_id=str(runtime.get("channel_user_id") or "") or None,
                is_internal=runtime.get("is_internal") is True,
                authz_attributes=normalize_authz_attributes(runtime.get("authz_attributes")),
                deerflow_trace_id=normalize_trace_id(runtime.get(DEERFLOW_TRACE_METADATA_KEY)) or normalize_trace_id(metadata.get(DEERFLOW_TRACE_METADATA_KEY)) or get_current_trace_id(),
                project_id=project_id,
                project_root=project_root,
                # Council token use is metered rather than capped. When the
                # depth disables enforcement, even a stale participant edit
                # must not quietly turn the kill switch back on.
                token_budget_max_tokens=_token_limit_for_worker(unit, budget),
                thinking_enabled=unit.reasoning == REASONING_EXTENDED,
                extra_middlewares=[deadline],
            )
            await emit(
                {
                    "type": "task_started",
                    "task_id": unit.unit_id,
                    "description": (_seat_description(unit) if meeting else f"{stage.title()} work: {unit.capability.replace('_', ' ')}"),
                    "model_name": effective_model,
                    "dbtl_stage": stage,
                    **({"council_seat": _seat_identity(unit, model=effective_model, stage=stage)} if meeting else {}),
                }
            )
            holder = SubagentResult(
                task_id=unit.unit_id,
                trace_id=executor.trace_id,
                status=SubagentStatus.PENDING,
            )
            try:
                result = await asyncio.to_thread(
                    executor.execute,
                    unit.prompt,
                    holder,
                )
            except asyncio.CancelledError:
                holder.cancel_event.set()
                await emit(
                    {
                        "type": "task_failed",
                        "task_id": unit.unit_id,
                        "error": "DBTL stage run cancelled.",
                        "dbtl_stage": stage,
                        **({"council_seat": _seat_identity(unit, model=effective_model, stage=stage)} if meeting else {}),
                    }
                )
                raise

            _report_subagent_token_usage(config, result)
            token_usage = _summarize_token_usage(result.token_usage_records)
            if result.status is SubagentStatus.COMPLETED:
                dispatch_outcome = DispatchOutcome(
                    unit_id=unit.unit_id,
                    text=result.result,
                    stop_reason=result.stop_reason,
                    forced_finalization=deadline.forced_any(),
                    token_usage=token_usage,
                )
                await emit(
                    _terminal_seat_event(
                        unit,
                        dispatch_outcome,
                        model=effective_model,
                        meeting_stage=stage if meeting else None,
                    )
                )
                return dispatch_outcome

            error = result.error or f"Subagent ended with status {result.status.value}."
            dispatch_outcome = DispatchOutcome(
                unit_id=unit.unit_id,
                text=result.result,
                stop_reason=result.stop_reason,
                error=error,
                token_usage=token_usage,
            )
            await emit(
                _terminal_seat_event(
                    unit,
                    dispatch_outcome,
                    model=effective_model,
                    meeting_stage=stage if meeting else None,
                )
            )
            return dispatch_outcome

        return await asyncio.gather(*(run_one(unit) for unit in units))

    async def _record_human_authored_design(
        self,
        *,
        spec,
        cycle: dict[str, Any],
        council: CouncilPlan,
        authored_design: str | None,
        project_root: str,
        project_id: str,
        cycle_id: str,
        user_id: str,
        execution_key: str,
    ) -> LiveStageResult:
        """Record a design the person wrote, or ask them to write it.

        No worker runs, no synthetic result, no agent attribution. The package
        is still a package — same path, same content addressing, same refusal to
        satisfy the gate — because the reviewer's job does not change just
        because the author was human.
        """
        text = (authored_design or "").strip()
        if not text:
            return LiveStageResult(
                stage=spec.stage,
                cycle_id=cycle_id,
                note=_HUMAN_AUTHORING_NOTE,
                authoring_request=_HUMAN_AUTHORING_QUESTION,
            )

        empty_outcome = StageExecutionOutcome(
            plan=StageExecutionPlan(spec=spec, selection=SelectionResult(), units=()),
        )
        artifact_uri, artifact_hash, digest = _write_stage_package(
            project_root=project_root,
            cycle=cycle,
            outcome=empty_outcome,
            idempotency_key=execution_key,
            council=council,
            authored_design=text,
        )
        await self._repo.record_worker_runs(
            cycle_id=cycle_id,
            project_id=project_id,
            stage=spec.stage,
            stage_spec_key=spec.spec_key,
            results=[],
            actor_user_id=user_id,
            expected_db_revision=int(cycle["db_revision"]),
            idempotency_key=execution_key,
            artifact_type="design_brief",
            artifact_uri=artifact_uri,
            artifact_content_hash=artifact_hash,
        )
        return LiveStageResult(
            stage=spec.stage,
            cycle_id=cycle_id,
            note=digest,
            worker_count=0,
            produced_usable_evidence=True,
            artifact_uri=artifact_uri,
        )

    async def _plan_feedback_surface(
        self,
        *,
        stage: str,
        cycle_id: str,
        project_id: str,
        execution_key: str,
        round_number: int,
        originating_thread_id: str,
        paused: bool,
        artifact_uri: str,
        artifact_hash: str,
        decision_request: DecisionRequest | None = None,
        chair_worker_run_id: str | None = None,
        review_issue_ids: Sequence[str] = (),
        transition_gate: Mapping[str, Any] | None = None,
    ) -> _FeedbackSurfacePlan | None:
        """Decide the surface *before* the deck is rendered.

        The deck has to carry its own surface id — that identifier is how a
        parent asks the server whether the file in front of it is a Design
        surface at all — but registering binds the deck's content hash, so the
        id cannot be assigned afterwards without changing the bytes it was
        assigned for. Deciding first breaks that circle.

        The id is derived rather than random so a retried turn produces the same
        id, hence the same bytes, hence the same hash, and re-registration
        collapses onto the existing row instead of superseding it with a copy
        of itself. ``mode`` is part of the derivation because two runs of one
        execution can legitimately differ (a round that paused, then completed),
        and those are different surfaces.

        Returns ``None`` when there is nothing to bind to; the deck is still
        written, just without a bridge.
        """
        thread_id = (originating_thread_id or "").strip()
        try:
            cycle = await self._repo.get_cycle(cycle_id, project_id=project_id)
        except Exception:  # noqa: BLE001 - a descriptor must not break the record
            logger.warning("Could not read cycle %s to plan its design feedback surface.", cycle_id, exc_info=True)
            return None
        if cycle is None:
            return None
        attempt_row_id = _stage_attempt_row_id(cycle, stage)
        if not attempt_row_id:
            return None

        evidence: Mapping[str, Any] | None = None
        if artifact_uri and artifact_hash:
            evidence = _bound_evidence(cycle, artifact_uri=artifact_uri, content_hash=artifact_hash)

        if not thread_id:
            mode = "read_only"
        elif paused:
            mode = "chair_feedback"
        elif evidence is not None:
            mode = "stage_review"
        else:
            # A completed round whose evidence could not be matched by hash.
            # Registering it as reviewable would bind a future verdict to a
            # document nobody confirmed this deck was rendered from.
            mode = "read_only"

        # The stage is deliberately absent from the digest: Design ids were
        # derived before stages were a parameter, and adding one would move
        # every already-registered Design surface off the row a retry must
        # land back on. One execution never spans two stages, so the attempt
        # id inside ``execution_key`` already separates them.
        digest = hashlib.sha256("\x1f".join((execution_key, mode, str(round_number))).encode("utf-8")).hexdigest()
        return _FeedbackSurfacePlan(
            surface_id=f"dfs-{digest[:32]}",
            mode=mode,
            stage=stage,
            stage_attempt_id=attempt_row_id,
            # A read-only surface still needs a non-empty column; it names no
            # live conversation and is refused as an answer target.
            originating_thread_id=thread_id or "unbound",
            round_number=round_number,
            evidence=evidence if mode == "stage_review" else None,
            evidence_content_hash=artifact_hash if mode == "stage_review" and evidence is not None else "",
            decision_request={
                **(decision_request.as_dict() if decision_request is not None else {}),
                **({"review_issue_ids": list(review_issue_ids)} if review_issue_ids else {}),
                **({"transition_gate": dict(transition_gate)} if transition_gate is not None else {}),
            }
            or None,
            chair_worker_run_id=chair_worker_run_id,
        )

    async def _register_feedback_surface(
        self,
        plan: _FeedbackSurfacePlan,
        deck: RenderedDeck,
        *,
        cycle_id: str,
        project_id: str,
    ) -> None:
        """Record that this workflow produced this deck, for this conversation.

        Registration is fail-visible. The registered deck is the authenticated
        review surface, so returning success after this write fails would leave
        an owner with a deck that can never answer its gate. Stage evidence is
        already durable at this point and remains available for a safe retry.
        """
        # Resolved without touching the legacy attribute: ``getattr`` with a
        # default evaluates that default eagerly, so a repository exposing
        # only the stage-generic method raised AttributeError before the lookup
        # it would have succeeded at.
        stage_register = getattr(self._repo, "register_stage_feedback_surface", None)
        register = stage_register if stage_register is not None else self._repo.register_design_feedback_surface
        kwargs = dict(
            surface_id=plan.surface_id,
            project_id=project_id,
            cycle_id=cycle_id,
            stage_attempt_id=plan.stage_attempt_id,
            design_round=plan.round_number,
            originating_thread_id=plan.originating_thread_id,
            mode=plan.mode,
            chair_worker_run_id=plan.chair_worker_run_id,
            decision_request=dict(plan.decision_request) if plan.decision_request is not None else None,
            deck_uri=deck.uri,
            deck_content_hash=deck.content_hash,
            evidence_artifact_id=str(plan.evidence["id"]) if plan.evidence is not None else None,
            evidence_artifact_revision=int(plan.evidence["revision"]) if plan.evidence is not None else None,
            evidence_content_hash=plan.evidence_content_hash or None,
        )
        if stage_register is not None:
            kwargs["stage"] = plan.stage
        await register(**kwargs)

    async def bind_feedback_request(
        self,
        *,
        project_id: str,
        surface_id: str,
        human_input_request_id: str,
    ) -> None:
        """Attach the supervisor-emitted card to the deck that replaces it."""
        await self._repo.bind_design_feedback_request(
            surface_id,
            project_id=project_id,
            human_input_request_id=human_input_request_id,
        )

    async def consume_feedback_request(
        self,
        *,
        project_id: str,
        human_input_request_id: str,
        answer: str,
    ) -> None:
        """Make a rollback-card answer consume the same deck surface."""
        await self._repo.mark_bound_design_feedback_answer(
            project_id=project_id,
            human_input_request_id=human_input_request_id,
            answer=answer,
        )

    async def _execute_review_meeting(
        self,
        *,
        stage: str,
        project_id: str,
        cycle_id: str,
        cycle: dict[str, Any],
        runtime: Mapping[str, Any],
        project_root: str,
        user_id: str,
        execution_key: str,
        config: RunnableConfig,
        state: dict[str, Any],
    ) -> LiveStageResult:
        """Argue about a stage's recorded evidence without re-running the stage.

        This is a *reader*, which is what makes it legal at ``awaiting_review``
        — the status ordinary execution refuses, because re-running the stage
        there would replace the evidence a person is in the middle of reading.
        The meeting never touches that evidence; it attaches its own beside it.
        """
        normalized = (stage or "").strip().lower()
        if normalized not in REVIEW_MEETING_STAGES:
            return LiveStageResult(
                stage=normalized or "unknown",
                cycle_id=cycle_id,
                note=f"The {normalized or 'requested'} stage has no review meeting, so nothing was convened.",
            )
        attempt = _stage_attempt(cycle, normalized)
        attempt_id = str((attempt or {}).get("id") or "")
        evidence = max(
            [item for item in cycle.get("artifacts", []) if isinstance(item, Mapping) and item.get("stage_attempt_id") == attempt_id and str(item.get("artifact_type") or "") != f"{normalized}_review_meeting"],
            key=lambda item: int(item.get("revision") or 0),
            default=None,
        )
        if not attempt_id or evidence is None:
            return LiveStageResult(
                stage=normalized,
                cycle_id=cycle_id,
                note=(f"The {normalized} stage has recorded no evidence yet, so there is nothing for a review meeting to argue about. No participants were run."),
            )

        # Carry the assessment from the pre-meeting surface onto the successor
        # deck.  The meeting is an attachment to that assessment, not a fresh
        # transition that gets to reassess itself.  Repository doubles used by
        # older integrations may not expose the generalized surface read yet;
        # missing it safely leaves the legacy gate in place.
        transition_gate = None
        latest_surface = getattr(self._repo, "latest_stage_feedback_surface", None)
        if callable(latest_surface):
            try:
                prior_surface = await latest_surface(
                    project_id=project_id,
                    cycle_id=cycle_id,
                    stage=normalized,
                    stage_attempt_id=attempt_id,
                    mode="stage_review",
                )
                request_payload = prior_surface.get("decision_request") if isinstance(prior_surface, Mapping) else None
                candidate_gate = request_payload.get("transition_gate") if isinstance(request_payload, Mapping) else None
                if isinstance(candidate_gate, Mapping):
                    transition_gate = dict(candidate_gate)
            except Exception:  # noqa: BLE001 - losing a label must not lose the meeting
                logger.warning(
                    "Could not recover the %s transition assessment for its review meeting.",
                    normalized,
                    exc_info=True,
                )
        try:
            spec = resolve_review_stage_spec(normalized)
        except StageSpecNotFound:
            return LiveStageResult(
                stage=normalized,
                cycle_id=cycle_id,
                note=f"No review meeting contract is registered for the {normalized} stage.",
            )

        selection = select_agents(spec, self._candidates())
        assignment = next(iter(selection.assignments), None)
        if assignment is None:
            return LiveStageResult(
                stage=normalized,
                cycle_id=cycle_id,
                note=(f"No available agent covers the {normalized} review meeting's required capabilities, so nobody was dispatched. " + ("; ".join(selection.notes) if selection.notes else "")).strip(),
            )
        units = _review_meeting_units(
            stage=normalized,
            attempt_id=attempt_id,
            assignment=assignment,
            model=self._council_model(),
            evidence_uri=str(evidence.get("uri") or ""),
            evidence_hash=str(evidence.get("content_hash") or ""),
            context={
                "cycle_id": cycle_id,
                "cycle_title": cycle.get("title"),
                "research_question": cycle.get("research_question"),
                "objective": cycle.get("objective"),
                "success_criteria": cycle.get("success_criteria"),
            },
        )
        plan = StageExecutionPlan(spec=spec, selection=selection, units=units)
        dispatcher = self._dispatcher or self._production_dispatcher(
            config=config,
            state=state,
            project_id=project_id,
            project_root=project_root,
            stage=normalized,
            meeting=True,
        )
        outcome = collect_results(plan, await dispatcher(units, budget=spec.budget))
        results = [
            {
                # A meeting annotates; it cannot restate what the stage's own
                # result computes. Applied to what is *recorded*, not merely
                # offered as a helper, or the rule is advisory.
                **sanitize_meeting_attachment(normalized, result.as_dict()),
                "unit_id": unit.unit_id,
                "via_generalist": unit.via_generalist,
                "execution": {
                    "model": unit.model,
                    "max_tokens": unit.max_tokens,
                    "token_limit_enforced": spec.budget.token_limit_enforced,
                    "reasoning": unit.reasoning,
                },
                "counts_toward_stage_output": unit.role == "chair",
            }
            for unit, result in zip(plan.units, outcome.results, strict=True)
        ]
        chair_result = next(
            (result for unit, result in zip(plan.units, outcome.results, strict=True) if unit.role == "chair"),
            None,
        )
        debate_complete = any(unit.role == "position" and result.is_trustworthy for unit, result in zip(plan.units, outcome.results, strict=True)) and any(
            unit.role == "red_team" and result.is_trustworthy for unit, result in zip(plan.units, outcome.results, strict=True)
        )
        usable = bool(debate_complete and chair_result is not None and chair_result.is_trustworthy)

        artifact_uri = artifact_hash = None
        artifact_digest = ""
        if usable:
            artifact_uri, artifact_hash, artifact_digest = await asyncio.to_thread(
                _write_stage_package,
                project_root=project_root,
                cycle=cycle,
                outcome=outcome,
                idempotency_key=execution_key,
                council=None,
            )
        await self._repo.record_worker_runs(
            cycle_id=cycle_id,
            project_id=project_id,
            stage=normalized,
            stage_spec_key=spec.spec_key,
            results=results,
            actor_user_id=str(user_id),
            expected_db_revision=int(cycle["db_revision"]),
            idempotency_key=execution_key,
            artifact_type=(spec.required_artifact_types[0] if usable else None),
            artifact_uri=artifact_uri,
            artifact_content_hash=artifact_hash,
            reviewed_artifact_id=(str(evidence.get("id") or "") if usable else None),
            reviewed_artifact_revision=(int(evidence.get("revision") or 0) if usable else None),
            reviewed_artifact_content_hash=(str(evidence.get("content_hash") or "") if usable else None),
        )

        deck_uri = None
        deck = None
        surface_plan = None
        if usable and artifact_uri and artifact_hash:
            surface_plan = await self._plan_feedback_surface(
                stage=normalized,
                cycle_id=cycle_id,
                project_id=project_id,
                execution_key=execution_key,
                round_number=1,
                originating_thread_id=str(runtime.get("thread_id") or ""),
                paused=False,
                # The surface and eventual human verdict stay bound to the
                # core evidence the meeting reviewed.  The meeting package is
                # presented by the deck below, but never replaces this binding.
                artifact_uri=str(evidence.get("uri") or ""),
                artifact_hash=str(evidence.get("content_hash") or ""),
                review_issue_ids=(tuple(f"issue-{index + 1}" for index, _item in enumerate(chair_result.consensus.disagreements)) if chair_result is not None and chair_result.consensus is not None else ()),
                transition_gate=transition_gate,
            )
            deck = await asyncio.to_thread(
                _write_council_deck,
                project_root=project_root,
                cycle=cycle,
                results=results,
                round_number=1,
                stage=normalized,
                package_path=artifact_uri,
                clarification_question="",
                decision_request=None,
                surface_id=(surface_plan.surface_id if surface_plan is not None and surface_plan.answerable else ""),
                surface_mode=(surface_plan.mode if surface_plan is not None else ""),
                transition_gate=transition_gate,
            )
            if deck is not None:
                deck_uri = deck.uri
                if surface_plan is not None:
                    await self._register_feedback_surface(
                        surface_plan,
                        deck,
                        cycle_id=cycle_id,
                        project_id=project_id,
                    )
        note = artifact_digest or (f"The {normalized.title()} review meeting ran but produced no usable synthesis, so no review evidence was attached.\n" + "\n".join(_failure_reasons(results)))
        return LiveStageResult(
            stage=normalized,
            cycle_id=cycle_id,
            note=note,
            worker_count=len(results),
            produced_usable_evidence=usable,
            artifact_uri=artifact_uri,
            deck_uri=deck_uri,
            feedback_surface_id=(surface_plan.surface_id if surface_plan is not None and deck is not None else None),
        )

    async def test_review_snapshot(
        self,
        *,
        project_id: str,
        cycle_id: str,
    ) -> dict[str, Any] | None:
        """Read the durable Test evidence needed to render a chat decision."""
        return await TestReviewService(
            repo=self._repo,
            app_config=self._app_config,
            runtime_reader=self._runtime,
        ).snapshot(project_id=project_id, cycle_id=cycle_id)

    async def record_test_outcome(
        self,
        *,
        project_id: str,
        cycle_id: str,
        snapshot: Mapping[str, Any],
        recommendation: str,
        config: RunnableConfig,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Persist one explicit human card choice against Test evidence."""
        return await TestReviewService(
            repo=self._repo,
            app_config=self._app_config,
            runtime_reader=self._runtime,
        ).record_outcome(
            project_id=project_id,
            cycle_id=cycle_id,
            snapshot=snapshot,
            recommendation=recommendation,
            config=config,
            idempotency_key=idempotency_key,
        )

    async def execute(
        self,
        *,
        project_id: str | None,
        cycle_id: str | None,
        request_text: str,
        state: dict[str, Any],
        config: RunnableConfig,
        authored_design: str | None = None,
        council_adjustment: str | None = None,
        participant_settings: Mapping[str, ParticipantSettings] | None = None,
        approved_council_proposal: CouncilProposal | None = None,
        clarification_answer: str | None = None,
        review_meeting_stage: str | None = None,
        expected_stage: str | None = None,
        expected_cycle_revision: int | None = None,
    ) -> LiveStageResult:
        if not project_id or not cycle_id:
            return LiveStageResult(
                stage="unknown",
                cycle_id=cycle_id or "",
                note="A project-owned cycle is required before stage work can run.",
            )
        cycle = await self._repo.get_cycle(cycle_id, project_id=project_id)
        if cycle is None:
            return LiveStageResult(
                stage="unknown",
                cycle_id=cycle_id,
                note=f"Cycle {cycle_id} is not available in this project; no workers were dispatched and nothing was recorded.",
            )
        if expected_stage is not None and expected_cycle_revision is not None:
            refusal = _stage_handoff_refusal(
                cycle,
                expected_db_revision=expected_cycle_revision,
                expected_stage=expected_stage,
            )
            if refusal is not None:
                return LiveStageResult(
                    stage=expected_stage,
                    cycle_id=cycle_id,
                    note=refusal,
                )

        runtime = self._runtime(config)
        project_root = runtime.get("project_root")
        run_id = runtime.get("run_id")
        user_id = runtime.get("user_id")
        if not isinstance(project_root, str) or not project_root:
            return LiveStageResult(
                stage="unknown",
                cycle_id=cycle_id,
                note="The server did not provide the project workspace root, so no workers were dispatched.",
            )
        if not run_id or not user_id:
            return LiveStageResult(
                stage="unknown",
                cycle_id=cycle_id,
                note="The server did not provide durable run/user identity, so no workers were dispatched.",
            )
        execution_key = f"dbtl-stage:{run_id}:{cycle_id}"
        replay = await ReplayService(self._repo).resolve(
            project_id=project_id,
            cycle_id=cycle_id,
            user_id=str(user_id),
            execution_key=execution_key,
            learn_synthesis=_learn_synthesis_payload,
        )
        if replay is not None:
            return replay

        if review_meeting_stage:
            # A convened meeting reads the stage's recorded evidence instead of
            # deriving a stage from cycle state, so it deliberately skips the
            # status checks below — ``awaiting_review`` is exactly when it runs.
            return await self._execute_review_meeting(
                stage=review_meeting_stage,
                project_id=project_id,
                cycle_id=cycle_id,
                cycle=cycle,
                runtime=runtime,
                project_root=project_root,
                user_id=str(user_id),
                execution_key=execution_key,
                config=config,
                state=state,
            )

        stage = _executable_stage(cycle)
        if stage not in {"design", "reconciliation", "build", "test", "learn"}:
            return LiveStageResult(
                stage=stage or "checkpoint",
                cycle_id=cycle_id,
                note=f"Cycle {cycle_id} is at {cycle.get('state')}; no executable worker stage is available here.",
            )
        attempt = _stage_attempt(cycle, stage)
        status = str((attempt or {}).get("status") or "")
        if status == StageStatus.AWAITING_REVIEW.value:
            return LiveStageResult(
                stage=stage,
                cycle_id=cycle_id,
                note=f"The {stage} stage is awaiting human review, so it was not run again.",
            )
        if status not in {
            StageStatus.IN_PROGRESS.value,
            StageStatus.CHANGES_REQUESTED.value,
        }:
            return LiveStageResult(
                stage=stage,
                cycle_id=cycle_id,
                note=f"The {stage} stage is {status or 'unavailable'} and cannot accept worker evidence.",
            )

        datasets = await self._repo.list_datasets(cycle_id, project_id=project_id)
        requires_reconciliation = reconciliation_required()
        reconciliation = await self._repo.reconciliation_view(cycle_id, project_id=project_id) if stage in {"design", "reconciliation", "build", "test", "learn"} else None
        build_test = await self._repo.build_test_view(cycle_id, project_id=project_id) if stage in {"build", "test", "learn"} else None
        prior_design_runs = (
            await self._repo.list_worker_runs(
                cycle_id,
                project_id=project_id,
                stage="design",
            )
            if stage == "design"
            else []
        )
        # The reviewer's objection lives in a review rationale nobody read back,
        # so a second attempt argued the same points from the same starting
        # position and could not know what had been rejected. Failing to load
        # the activity feed costs the focus, not the round.
        activity: list[dict[str, Any]] = []
        if stage == "design":
            try:
                activity = await self._repo.list_activity(cycle_id, project_id=project_id)
            except Exception:  # noqa: BLE001 - a missing feed must not block a design round
                logger.warning("Could not read cycle activity for the Design council's refinement context.", exc_info=True)
        change_request = _change_request(activity)
        design_round = _design_round(activity)

        # Answering the chair's question resumes the meeting; it does not
        # convene a new one. Only a question that is actually outstanding
        # resumes, so a stray card reply after a completed synthesis falls
        # through to the ordinary rules below rather than re-running the chair.
        pending_question = _pending_design_question(prior_design_runs) if stage == "design" else None
        resumed_answer = (clarification_answer or "").strip() if pending_question else ""
        resumed_positions = _prior_positions(prior_design_runs) if resumed_answer else []

        # "Request changes" used to reconvene the whole meeting the moment it
        # was clicked, whatever the objection said. Most objections are
        # corrections the chair can fold into the synthesis it already wrote,
        # over positions that are already recorded, so the objection is read
        # first and the reading picks the route. Every failure of that reading
        # takes the cheap route: an unavailable reader must never be the reason
        # four workers run.
        revision_verdict: RevisionVerdict | None = None
        revision_positions: list[dict[str, Any]] = []
        if stage == "design" and change_request and not resumed_answer and authored_design is None:
            revision_positions = _prior_positions(prior_design_runs)
            revision_verdict = await interpret_revision(
                change_request,
                positions=tuple(str(item.get("summary") or "") for item in revision_positions),
                interpreter=self._revision_interpreter,
            )

        # Nothing outstanding, a package already on the table, and no request to
        # argue again: hold. A Design stage stays ``in_progress`` until a person
        # submits it for review, so without this every later message in the
        # cycle convened the whole meeting over again. The deterministic
        # phrases decide first and free; the interpreter reads only what they
        # did not match, so a typo or paraphrase still means what it meant.
        if stage == "design" and not resumed_answer and authored_design is None and not _is_refinement_kickoff(request_text):
            settled = _unreviewed_design_package(cycle)
            if settled is not None and not _wants_new_debate(request_text) and not await self._interpreted_wants_new_debate(request_text):
                return LiveStageResult(
                    stage=stage,
                    cycle_id=cycle_id,
                    note="\n".join(
                        [
                            "This cycle already has a design from the last meeting, and nobody has asked for changes to it, so no new meeting was convened.",
                            "",
                            f"The design under review: {settled['uri']}",
                            "",
                            'From here you can approve it, request changes, or reject it in the Design review sheet — or say "run the meeting again" if you want the participants to argue it afresh.',
                        ]
                    ),
                )

        project_manifest = await asyncio.to_thread(_project_manifest, project_root)
        pre_run_files = await asyncio.to_thread(_project_file_snapshot, project_root) if stage == "build" else {}
        stage_context_payload = {
            "request": request_text,
            "cycle": {
                key: cycle.get(key)
                for key in (
                    "id",
                    "title",
                    "cycle_class",
                    "state",
                    "research_question",
                    "objective",
                    "success_criteria",
                )
            },
            "declared_datasets": datasets,
            # Optional mode deliberately moves data authority into Build/Test.
            # Do not hand later workers the old gate's unsettled matrix as if
            # it were still an active prerequisite: that caused a correct
            # Build to report ``reconciled_inputs`` as failed and made Test
            # invalidate a cycle solely because the skipped stage was skipped.
            "reconciliation": (
                reconciliation
                if requires_reconciliation
                else {
                    "required": False,
                    "status": "skipped",
                    "instruction": ("Data Reconciliation is intentionally skipped for this deployment. Missing dataset declarations or reconciliation matrix rows are not a blocker, limitation, or failed validity check."),
                }
            ),
            "build_test": build_test,
            "input_provenance_policy": {
                "reconciliation_required": requires_reconciliation,
                "authority": "approved_reconciliation" if requires_reconciliation else "server_bound_build_lineage",
                "instruction": (
                    "Use the approved reconciliation record as the input prerequisite."
                    if requires_reconciliation
                    else (
                        "Build binds the exact files it reads with server-computed content hashes, and Test verifies that durable Build lineage. "
                        "For compatibility, a validity check named reconciled_inputs means bound input provenance in this mode; judge the Build lineage, "
                        "not the existence of reconciliation rows. An older Build package may describe absent reconciliation as a limitation; that is "
                        "historical worker commentary, not the active deployment policy."
                    )
                ),
            },
            "test_validity_contract": (
                {
                    "pack_key": DEFAULT_VALIDITY_PACK.pack_key,
                    "required_checks": [check.value for check in DEFAULT_VALIDITY_PACK.required_checks],
                    "authoritative_rules": [
                        "Only required_checks may determine the overall Test outcome. Do not invent or require an additional gate.",
                        (
                            "The server-bound Build lineage satisfies reconciled_inputs when present. Data Reconciliation is intentionally skipped; do not require a declaration, matrix, or reconciliation artifact."
                            if not requires_reconciliation
                            else "Judge reconciled_inputs from the approved reconciliation and Build lineage."
                        ),
                        (
                            "duplicates_relatedness is not in this validity pack. Missing pedigree, genotype, kinship, or relatedness columns may be noted as a limitation, but cannot fail, block, or make this Test inconclusive."
                            if ValidityCheckName.DUPLICATES_RELATEDNESS not in DEFAULT_VALIDITY_PACK.required_checks
                            else "Evaluate duplicates_relatedness as a required check."
                        ),
                        "The approved Design and this server-owned contract outrank commentary in an older Build package.",
                    ],
                }
                if stage == "test"
                else None
            ),
            # Named explicitly beside the listing, because a worker that
            # *constructs* a path (rather than copying one from the
            # manifest) has no other way to learn the prefix its tools
            # require, and a path outside it is refused outright.
            "workspace_root": WORKSPACE_VIRTUAL_ROOT,
            "project_workspace_manifest": project_manifest,
            "build_input_policy": (
                {
                    "reconciliation_required": requires_reconciliation,
                    "instruction": (
                        "Read the data files needed to implement the approved design and list every exact workspace path in "
                        "provenance.inputs_examined. The server will compute and record their hashes automatically. "
                        + (
                            "An approved reconciliation remains a prerequisite."
                            if requires_reconciliation
                            else "No dataset declaration or reconciliation matrix is required, and their absence must not be reported as a failure or limitation."
                        )
                    ),
                }
                if stage == "build"
                else None
            ),
            "prior_design_council_runs": _compact_design_history(prior_design_runs),
            # Only present once a person has approved a Design package.
            # Its absence is meaningful: a later stage seeing no brief is
            # working before the gate, not merely without context.
            "approved_design_brief": _approved_design_brief(cycle),
            # Verbatim, not summarized. The council is being asked to answer
            # this specific sentence, and a paraphrase is the failure mode
            # the refinement round exists to fix.
            "human_change_request": change_request,
            # The question the chair asked and the owner's own words back.
            # Verbatim for the same reason the change request is: the
            # synthesis is being built on this answer, and a paraphrase of a
            # decision is not the decision.
            "chair_question_answered": pending_question if resumed_answer else None,
            "human_answer": resumed_answer or None,
            "design_round": design_round,
        }
        stage_context = json.dumps(
            stage_context_payload,
            sort_keys=True,
            ensure_ascii=False,
        )
        spec = resolve_stage_spec(stage)
        attempt_id = f"dbtl-{_safe_token(execution_key)}"
        council_plan: CouncilPlan | None = None
        approved_proposal: CouncilProposal | None = None
        if stage == "design":
            council_plan = self._plan_council(
                spec,
                config=config,
                request_text=request_text,
                attempt_id=attempt_id,
            )
            if approved_council_proposal is not None:
                approved_proposal = _proposal_scoped_to_plan(
                    approved_council_proposal,
                    council_plan,
                    change_request=change_request,
                )
                council_plan = plan_from_proposal(
                    council_plan,
                    approved_proposal,
                )
            # Applied to the plan as well as the dispatched units, because the
            # plan is what the review package records — a package describing
            # the proposal's dials while the workers ran on the owner's would
            # misreport what happened.
            council_plan = apply_participant_settings(council_plan, participant_settings)
            # Scoping the spec is what keeps the previewed roster and the
            # dispatched one the same computation: selection reads its worker
            # ceiling off the spec, and so does the dispatch budget.
            spec = replace(spec, budget=council_plan.budget)
            if council_plan.depth is CouncilDepth.LIGHT:
                # Light stays quick through a different execution contract:
                # bounded context, bounded inspection, and a concise answer.
                stage_context = json.dumps(
                    _light_design_context(stage_context_payload),
                    sort_keys=True,
                    ensure_ascii=False,
                )
            if council_plan.human_authored:
                # Asked *before* ``dispatchable``, which is false here for a
                # completely different reason. Reaching the fan-out at this depth
                # would convene the council the person just declined.
                return await self._record_human_authored_design(
                    spec=spec,
                    cycle=cycle,
                    council=council_plan,
                    authored_design=authored_design,
                    project_root=project_root,
                    project_id=project_id,
                    cycle_id=cycle_id,
                    user_id=str(user_id),
                    execution_key=execution_key,
                )
        dispatcher = self._dispatcher or self._production_dispatcher(
            config=config,
            state=state,
            project_id=project_id,
            project_root=project_root,
            stage=stage,
            meeting=stage == "design",
        )
        proposal: CouncilProposal | None = None
        resumed_chair: WorkUnit | None = None
        #: The positions the single-chair round re-weighs, whichever round it is.
        chair_positions: Sequence[Mapping[str, Any]] = resumed_positions
        if stage == "design" and council_plan is not None and resumed_positions:
            resumed_chair = _resumed_chair_unit(
                council_plan,
                attempt_id=attempt_id,
                stage_context=stage_context,
                positions=resumed_positions,
                question=str(pending_question or ""),
                answer=resumed_answer,
                round_number=design_round,
                settings=participant_settings,
                prior_execution=_prior_chair_execution(prior_design_runs),
            )
        elif stage == "design" and council_plan is not None and revision_verdict is not None and not revision_verdict.reconvenes and revision_positions:
            chair_positions = revision_positions
            resumed_chair = _resumed_chair_unit(
                council_plan,
                attempt_id=attempt_id,
                stage_context=stage_context,
                positions=revision_positions,
                question="",
                answer="",
                # Verbatim. The reading chose the route; the reviewer's own
                # words are what the chair has to answer.
                objection=change_request or "",
                round_number=design_round,
                settings=participant_settings,
                prior_execution=_prior_chair_execution(prior_design_runs),
            )
        # A resume seats nobody new, so there is no roster to draw. Asking for
        # one anyway would spend a model call on a council that will not convene.
        if approved_proposal is not None:
            proposal = approved_proposal
        elif stage == "design" and council_plan is not None and resumed_chair is None:
            proposal = await self._propose_roster(
                request_text=request_text,
                stage_context=stage_context,
                # The depth's ceiling, not how many seats capability selection
                # managed to fill. Selection is limited by which specialists
                # happen to be registered, and inheriting that limit here would
                # cap a heavy council at one position in exactly the
                # generalist-only deployment this feature exists for.
                max_positions=_refinement_positions(
                    depth_policy(council_plan.depth).max_positions,
                    change_request=change_request,
                ),
                # Carried from the preflight the person approved, so the roster
                # that runs is the one they were shown. A reconvene decided by
                # the revision reading contributes what the objection says the
                # new seats have to argue — the reviewer's own words still
                # travel separately as ``change_request``, so this adds focus
                # rather than replacing them.
                adjustment=council_adjustment or (revision_verdict.roster_note if revision_verdict is not None and revision_verdict.reconvenes else None),
            )
        if resumed_chair is not None:
            resume_plan = StageExecutionPlan(
                spec=spec,
                selection=_resumed_selection(
                    resumed_chair,
                    positions=chair_positions,
                    revision_reason=(revision_verdict.reason if revision_verdict is not None and not revision_verdict.reconvenes and not resumed_positions else ""),
                ),
                units=(resumed_chair,),
            )
            outcome = collect_results(resume_plan, await dispatcher((resumed_chair,), budget=spec.budget))
        elif proposal is not None:
            # The roster replaces selection's units rather than sitting beside
            # them: two sources of seats would let the package describe a
            # council that did not run, which is the failure the roster work
            # exists to prevent.
            units = _proposed_units(
                proposal,
                spec,
                attempt_id=attempt_id,
                context=stage_context,
                round_number=design_round,
                change_request=change_request,
                settings=participant_settings,
            )
            plan = StageExecutionPlan(spec=spec, selection=_proposed_selection(proposal), units=units)
            outcome = collect_results(plan, await dispatcher(units, budget=spec.budget))
        elif stage == "design" and participant_settings:
            # The proposal writer failed, so the seats fall back to capability
            # selection — but the owner's edits still apply. The card numbered
            # these seats position-1..N in selection order, and dropping a
            # person's instructions because a model call failed would be the
            # verbatim-carry rule losing to an outage.
            plan = plan_stage(spec, self._candidates(), attempt_id=attempt_id, context=stage_context)
            plan = replace(
                plan,
                units=tuple(_unit_with_settings(unit, participant_settings.get(f"position-{index}")) for index, unit in enumerate(plan.units, start=1)),
            )
            if plan.dispatchable:
                outcome = collect_results(plan, await dispatcher(plan.units, budget=spec.budget))
            else:
                logger.info("dbtl stage %s not dispatchable: %s", spec.spec_key, "; ".join(plan.selection.notes) or "no work units")
                outcome = StageExecutionOutcome(plan=plan)
        else:
            outcome = await arun_stage(
                spec,
                self._candidates(),
                dispatcher,
                attempt_id=attempt_id,
                context=stage_context,
            )
        unit_result_pairs = list(zip(outcome.plan.units, outcome.results, strict=True))
        chair_result = None
        if resumed_chair is not None:
            # The chair is the only worker that ran, so it is also the result the
            # stage is graded on. No red team: it already argued, and dispatching
            # a fresh one here would be the second debate this path exists to
            # avoid.
            chair_result = outcome.results[0] if outcome.results else None
        elif stage == "design" and outcome.plan.dispatchable:
            # Unconditional: several specialists are several *positions*, not an
            # adversarial one. Skipping the red team once a second specialist
            # existed gave a better-configured council a weaker debate.
            red_team_unit = _design_red_team_unit(
                outcome,
                attempt_id=attempt_id,
                council=council_plan,
                settings=participant_settings,
            )
            if red_team_unit is not None:
                red_team_plan = StageExecutionPlan(
                    spec=spec,
                    selection=outcome.plan.selection,
                    units=(red_team_unit,),
                )
                red_team_dispatch = await dispatcher(
                    (red_team_unit,),
                    budget=spec.budget,
                )
                red_team_outcome = collect_results(
                    red_team_plan,
                    red_team_dispatch,
                )
                unit_result_pairs.append((red_team_unit, red_team_outcome.results[0]))
                outcome = StageExecutionOutcome(
                    plan=outcome.plan,
                    results=outcome.results + red_team_outcome.results,
                    rejected=outcome.rejected + red_team_outcome.rejected,
                )
            chair_unit = _design_chair_unit(
                outcome,
                attempt_id=attempt_id,
                stage_context=stage_context,
                council=council_plan,
                settings=participant_settings,
            )
            if chair_unit is not None:
                chair_plan = StageExecutionPlan(
                    spec=spec,
                    selection=outcome.plan.selection,
                    units=(chair_unit,),
                )
                chair_dispatch = await dispatcher(
                    (chair_unit,),
                    budget=spec.budget,
                )
                chair_outcome = collect_results(chair_plan, chair_dispatch)
                chair_result = chair_outcome.results[0]
                if council_plan is not None and council_plan.depth is CouncilDepth.LIGHT:
                    chair_result = _light_pilot_chair_fallback(
                        chair_result,
                        dispatch=chair_dispatch[0] if chair_dispatch else None,
                        unit=chair_unit,
                        cycle=cycle,
                    )
                    chair_outcome = replace(
                        chair_outcome,
                        results=(chair_result,),
                    )
                unit_result_pairs.append((chair_unit, chair_result))
                outcome = StageExecutionOutcome(
                    plan=outcome.plan,
                    results=outcome.results + chair_outcome.results,
                    rejected=outcome.rejected + chair_outcome.rejected,
                )

        results = [
            {
                **result.as_dict(),
                "unit_id": unit.unit_id,
                "via_generalist": unit.via_generalist,
                # A clarification resumes this exact worker. Persist its dials
                # beside the result so message compaction or a process restart
                # cannot silently replace the chair with today's defaults.
                "execution": {
                    "model": unit.model,
                    "max_tokens": unit.max_tokens,
                    "token_limit_enforced": spec.budget.token_limit_enforced,
                    "reasoning": unit.reasoning,
                },
                "counts_toward_stage_output": (stage != "design" or unit.capability == "design_council_chair"),
            }
            for unit, result in unit_result_pairs
        ]

        artifact_uri = None
        artifact_hash = None
        artifact_type = None
        artifact_digest = ""
        # A chair cannot turn an empty room into a concluded meeting. Light may
        # deliberately preserve completed-but-capped participant reports as a
        # limited pilot draft, but a provider failure or contract rejection is
        # not a position and is not a red-team argument. A resumed chair is the
        # exception because those two reports were durably recorded by the
        # paused round and are supplied through ``resumed_chair``.
        debate_report_statuses = {WorkerStatus.COMPLETED, WorkerStatus.NEEDS_INPUT}
        design_debate_complete = (
            stage != "design"
            or resumed_chair is not None
            or (
                any(unit.role == "position" and result.status in debate_report_statuses for unit, result in unit_result_pairs)
                and any(unit.role == "red_team" and result.status in debate_report_statuses for unit, result in unit_result_pairs)
            )
        )
        design_ready = stage != "design" or (design_debate_complete and chair_result is not None and chair_result.is_trustworthy and chair_result.status is WorkerStatus.COMPLETED)
        test_assessment = _validated_test_assessment(outcome.trustworthy_results, build_test=build_test) if stage == "test" else None
        produced_usable_evidence = outcome.produced_usable_evidence and design_ready and (stage != "test" or test_assessment is not None)
        if produced_usable_evidence:
            artifact_uri, artifact_hash, artifact_digest = await asyncio.to_thread(
                _write_stage_package,
                project_root=project_root,
                cycle=cycle,
                outcome=outcome,
                idempotency_key=execution_key,
                council=council_plan,
            )
            artifact_type = spec.required_artifact_types[0]

        recorded_worker_runs = await self._repo.record_worker_runs(
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
        chair_worker_run_id = None
        if chair_result is not None:
            chair_unit_id = next(
                (unit.unit_id for unit, result in unit_result_pairs if result is chair_result),
                None,
            )
            chair_worker_run_id = next(
                (str(item.get("id")) for item in (recorded_worker_runs or []) if chair_unit_id and item.get("unit_id") == chair_unit_id),
                None,
            )

        if stage == "learn":
            assessment = dict((build_test or {}).get("validity_assessment") or {})
            outcome_name = str(assessment.get("outcome") or "")
            learn_summary, learn_candidates = _learn_synthesis_payload(
                results,
                test_outcome=outcome_name,
                fallback_summary=artifact_digest,
            )
            current = await self._repo.get_cycle(cycle_id, project_id=project_id)
            if current is None:  # pragma: no cover - verified above
                raise RuntimeError("Cycle disappeared after Learn workers were recorded.")
            await self._repo.record_learn_synthesis(
                cycle_id=cycle_id,
                project_id=project_id,
                summary=learn_summary,
                candidates=learn_candidates,
                actor_user_id=f"agent:{user_id}",
                expected_db_revision=int(current["db_revision"]),
                idempotency_key=f"{execution_key}:learn",
            )

        if stage == "build" and artifact_uri and artifact_hash:
            current = await self._repo.get_cycle(cycle_id, project_id=project_id)
            if current is None:  # pragma: no cover - scope was verified above
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
            input_artifacts = await asyncio.to_thread(
                _build_input_artifacts,
                datasets=datasets,
                results=outcome.trustworthy_results,
                project_root=project_root,
                pre_run_files=pre_run_files,
            )
            await self._repo.record_build_lineage(
                cycle_id=cycle_id,
                project_id=project_id,
                code_revision=code_revision,
                config_revision=config_revision,
                environment={
                    "python": platform.python_version(),
                    "implementation": platform.python_implementation(),
                    "platform": platform.platform(),
                    "executable": sys.executable,
                    "stage_runner": "LiveStageAdapter",
                },
                input_artifacts=input_artifacts,
                output_artifacts=[
                    {
                        "uri": artifact_uri,
                        "content_hash": artifact_hash,
                        "revision": 1,
                    }
                ],
                deviations=deviations,
                logs_uri=artifact_uri,
                recorded_by=str(user_id),
                expected_db_revision=int(current["db_revision"]),
                idempotency_key=f"{execution_key}:lineage",
            )

        clarification_question = chair_result.clarification_question if chair_result is not None and chair_result.status is WorkerStatus.NEEDS_INPUT else None
        independent_count = sum(1 for unit, _result in unit_result_pairs if unit.role == "position")
        non_chair_pairs = [(unit, result) for unit, result in unit_result_pairs if unit.role != "chair"]
        failed_participant_count = sum(1 for _unit, result in non_chair_pairs if not result.is_trustworthy)

        # Written after the record, from the record. A completed or deliberately
        # paused chair result is a meeting outcome; a failed/blocked chair result
        # is only an audit record. Rendering the latter as a deck makes a
        # provider outage look like a concluded meeting and creates a feedback
        # surface for a decision that does not exist.
        chair_has_presentable_outcome = design_debate_complete and chair_result is not None and (chair_result.is_trustworthy or (chair_result.status is WorkerStatus.NEEDS_INPUT and not chair_result.was_capped))
        deck_uri = None
        deck = None
        surface_plan = None
        # Build, Test, and Learn each get a *pre-meeting* decision surface,
        # rendered from the stage's own evidence rather than from a chair result
        # — no meeting has happened when it is written. None of them carries a
        # route menu: each stage's verdict is taken at review time against this
        # evidence (Test's outcome is computed there from the validity pack),
        # and a menu rendered beforehand would pre-empt the decision it exists
        # to record. Design is the exception in the other direction: its deck
        # *is* a chair result, so it needs one to exist.
        stage_has_reviewable_evidence = stage in REVIEW_MEETING_STAGES and produced_usable_evidence and bool(artifact_uri and artifact_hash)
        review_meeting_requirement = None
        if (stage == "design" and chair_has_presentable_outcome) or stage_has_reviewable_evidence:
            transition_gate = None
            if stage_has_reviewable_evidence:
                if bool(getattr(getattr(self._app_config, "dbtl", None), "progressive_gate", False)):
                    assessment = await self._assess_transition(
                        stage=stage,
                        cycle=cycle,
                        evidence_summary=artifact_digest or f"{stage.title()} evidence: {artifact_uri} ({artifact_hash})",
                    )
                    # Assessment only. The difficulty is what decides whether a
                    # review meeting is skipped, offered, or required.
                    transition_gate = {
                        "stage": stage,
                        "assessment": assessment.as_dict(),
                        "routes": [],
                    }
                    meetings = getattr(getattr(self._app_config, "dbtl", None), "stage_meetings", None)
                    enabled = bool(getattr(meetings, stage, False))
                    gate = surface_meeting_gate(
                        stage=stage,
                        assessed_difficulty=assessment.difficulty.value,
                        enabled=enabled,
                    )
                    review_meeting_requirement = gate.requirement.value if gate is not None else MeetingRequirement.SKIPPED.value
            elif artifact_uri and artifact_hash and bool(getattr(getattr(self._app_config, "dbtl", None), "progressive_gate", False)):
                assessment = await self._assess_transition(
                    stage="design",
                    cycle=cycle,
                    evidence_summary=artifact_digest or f"Design evidence: {artifact_uri} ({artifact_hash})",
                )
                # Before this Design verdict, the reconciliation evaluator
                # necessarily includes one reason saying Design is not yet
                # approved. For the one-click action, approval and route
                # selection are atomic, so that reason is satisfied by the
                # click itself; every data-specific reason must already be
                # absent. This does not approve reconciliation or bypass its
                # durable review—it only decides whether the Build edge may be
                # offered after Design approval.
                reconciliation_settled = _reconciliation_ready_after_design_approval(reconciliation if isinstance(reconciliation, Mapping) else None)
                routes = compute_stage_routes(
                    RouteContext(
                        stage="design",
                        outcome="approved",
                        reconciliation_settled=reconciliation_settled,
                    )
                )
                transition_gate = {
                    "stage": "design",
                    "assessment": assessment.as_dict(),
                    "routes": [route.as_dict() for route in routes],
                }
            surface_plan = await self._plan_feedback_surface(
                stage=stage,
                cycle_id=cycle_id,
                project_id=project_id,
                execution_key=execution_key,
                round_number=design_round,
                originating_thread_id=str(runtime.get("thread_id") or ""),
                paused=bool(clarification_question),
                artifact_uri=artifact_uri or "",
                artifact_hash=artifact_hash or "",
                decision_request=(chair_result.decision_request if chair_result is not None else None),
                chair_worker_run_id=chair_worker_run_id,
                review_issue_ids=(tuple(f"issue-{index + 1}" for index, _item in enumerate(chair_result.consensus.disagreements)) if chair_result is not None and chair_result.consensus is not None else ()),
                transition_gate=transition_gate,
            )
            deck = await asyncio.to_thread(
                _write_council_deck,
                project_root=project_root,
                cycle=cycle,
                results=results,
                round_number=design_round,
                stage=stage,
                package_path=artifact_uri or "",
                clarification_question=clarification_question or "",
                decision_request=(chair_result.decision_request if chair_result is not None else None),
                surface_id=(surface_plan.surface_id if surface_plan is not None and surface_plan.answerable else ""),
                surface_mode=(surface_plan.mode if surface_plan is not None else ""),
                transition_gate=transition_gate,
            )
            if deck is not None:
                deck_uri = deck.uri
                if surface_plan is not None:
                    await self._register_feedback_surface(
                        surface_plan,
                        deck,
                        cycle_id=cycle_id,
                        project_id=project_id,
                    )

        # A revision round has to say which route it took and why. The failure
        # this replaces was silence: four workers ran, three of them died, and
        # the only visible symptom was a card that never came back.
        revision_prefix = ""
        if revision_verdict is not None:
            if not revision_verdict.reconvenes and not resumed_positions:
                revision_prefix = f"Your requested changes were read as something the meeting chair can settle on its own, so no participants were re-run. {revision_verdict.reason}".strip() + "\n\n"
            elif revision_verdict.reconvenes:
                revision_prefix = f"Your requested changes were read as needing an argument nobody made yet, so the meeting reconvened. {revision_verdict.reason}".strip() + "\n\n"

        if clarification_question and resumed_chair is not None:
            note = "The meeting chair resumed on your answer and still needs one more decision before it can write the design up for review. No participants were re-run."
        elif clarification_question and failed_participant_count:
            note = (
                f"The meeting ran {independent_count} independent position(s) and one red team, "
                f"but {failed_participant_count} of {len(non_chair_pairs)} returned no usable result. "
                "The chair produced a partial synthesis from the available project context and needs "
                "one human decision before the meeting can create a review package."
            )
        elif clarification_question:
            note = f"Ran {independent_count} independent Design meeting position(s), one red team, and a chair synthesis. The meeting paused before creating a review package because one human decision is required."
        elif artifact_uri:
            # The digest carries what the council concluded. A reply that is only
            # a file path makes the reader open a file to learn anything at all.
            note = artifact_digest or f"Ran {len(results)} bounded {stage} worker(s) and attached a review package at {artifact_uri}."
        else:
            # No package is written when nothing is trustworthy, so the review
            # Markdown that normally carries "Work units not included" never
            # reaches disk. Without the reasons here the only visible symptom is
            # "none produced usable evidence", which reads as three bad workers
            # and hides the one thing a person can act on — the contract
            # violation, the cap, or the crash that actually happened.
            note = "\n".join(
                [
                    f"Ran {len(results)} bounded {stage} worker(s) and recorded every outcome, but none produced usable evidence, so no review artifact was attached.",
                    *(["", "Why each worker did not count:", *_failure_reasons(results)] if results else []),
                ]
            )
        return LiveStageResult(
            stage=stage,
            cycle_id=cycle_id,
            note=revision_prefix + note,
            worker_count=len(results),
            produced_usable_evidence=produced_usable_evidence,
            artifact_uri=artifact_uri,
            clarification_question=clarification_question,
            deck_uri=deck_uri,
            feedback_surface_id=(surface_plan.surface_id if surface_plan is not None and deck is not None else None),
            test_assessment=test_assessment,
            review_meeting_requirement=review_meeting_requirement,
        )
