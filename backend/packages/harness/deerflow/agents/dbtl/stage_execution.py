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
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from inspect import isawaitable
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from deerflow.agents.middlewares.finalization_deadline_middleware import (
    FinalizationDeadlineMiddleware,
    model_call_budget,
)
from deerflow.authz.principal import normalize_authz_attributes
from deerflow.dbtl.agent_selector import AgentCandidate, Assignment, SelectionResult, build_candidates
from deerflow.dbtl.consensus import CONSENSUS_CONTRACT
from deerflow.dbtl.council import (
    CouncilPlan,
    council_depth_from_config,
    depth_policy,
    plan_council,
    plan_from_proposal,
    recommend_depth,
)
from deerflow.dbtl.council_proposal import (
    CouncilProposal,
    build_proposal_prompt,
    parse_council_proposal,
    seatable_agents,
)
from deerflow.dbtl.cycle_state import StageStatus, stage_for_state
from deerflow.dbtl.review_markdown import render_review_markdown, render_stage_digest
from deerflow.dbtl.review_paths import stage_file_name, stage_output_dir
from deerflow.dbtl.stage_runner import (
    RESULT_CONTRACT,
    AsyncWorkerDispatcher,
    DispatchOutcome,
    StageExecutionOutcome,
    StageExecutionPlan,
    WorkUnit,
    arun_stage,
    collect_results,
)
from deerflow.dbtl.stage_spec import StageSpec, WorkerBudget, resolve_stage_spec
from deerflow.dbtl.worker_result import (
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


@dataclass(frozen=True, slots=True)
class LiveStageResult:
    """User-visible result of one supervisor stage request."""

    stage: str
    cycle_id: str
    note: str
    worker_count: int = 0
    produced_usable_evidence: bool = False
    artifact_uri: str | None = None
    clarification_question: str | None = None
    #: The council is set to Human Input and is waiting for the person to write
    #: the design. Distinct from ``clarification_question``, which means a
    #: council ran and hit a decision only its owner can make — here no council
    #: ran, and the supervisor raises a different card for it.
    authoring_request: str | None = None

    @property
    def satisfies_gate(self) -> bool:
        """Always false: only a typed human review can satisfy a gate."""
        return False


CandidateProvider = Callable[[], Sequence[AgentCandidate]]

_DESIGN_HISTORY_TURNS = 4
_DESIGN_HISTORY_SUMMARY_CHARS = 3_000
_DESIGN_HISTORY_DETAIL_CHARS = 600


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
_HUMAN_AUTHORING_NOTE = "This council is set to **Write it myself**, so no agent was consulted and no worker ran. Your answer is recorded as the Design review package exactly as you write it; you still review and approve it yourself."


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


def _seat_identity(unit: WorkUnit, *, model: str) -> dict[str, Any]:
    """Who is speaking, in what role, on whose behalf.

    Carried on the event rather than left for a consumer to parse out of the
    unit id. A live debate view has to say "the red team is arguing" while it is
    happening, and a view that derives that from an identifier is one rename
    away from labelling every seat wrong.
    """
    return {
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


def _terminal_seat_event(
    unit: WorkUnit,
    outcome: DispatchOutcome,
    *,
    model: str,
) -> dict[str, Any]:
    """Report contract-valid evidence progress, not child-graph termination."""
    base = {
        "task_id": unit.unit_id,
        "council_seat": _seat_identity(unit, model=model),
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
        prompt = "\n".join(
            [
                f"You are contributing to the {spec.title} stage of a DBTL research cycle.",
                "",
                f"Stage purpose: {spec.purpose}",
                f"Your seat on the council: {seat.focus}",
                f"What you argue from: {seat.brief}",
                "",
                "You are one of several independent positions and you cannot see the others.",
                "Argue your own case as strongly as the evidence allows; a chair will weigh it against the rest.",
                "Do not hedge toward what you imagine the others will say.",
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
                model=seat.model,
                role="position",
                focus=seat.focus,
                round=round_number,
            )
        )
    return tuple(units)


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
    if str(latest.get("decision") or "") != "changes_requested":
        return None
    rationale = str(latest.get("rationale") or "").strip()[:_CHANGE_REQUEST_CHARS]
    return rationale or None


def _design_round(activity: Any) -> int:
    """Which round of this Design debate the next attempt is."""
    requested = sum(1 for payload in _design_reviews(activity) if str(payload.get("decision") or "") == "changes_requested")
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


def _safe_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _project_manifest(project_root: str, *, limit: int = 120) -> list[dict[str, Any]]:
    """Return a bounded, metadata-only view of the human-visible project folder."""
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
                "path": relative.as_posix(),
                "kind": "directory" if path.is_dir() else "file",
                "size_bytes": 0 if path.is_dir() else stat.st_size,
            }
        )
    return entries


def _design_chair_unit(
    outcome: StageExecutionOutcome,
    *,
    attempt_id: str,
    stage_context: str,
) -> WorkUnit | None:
    if not outcome.plan.units:
        return None
    positions = json.dumps(
        [item.as_dict() for item in outcome.results],
        sort_keys=True,
        ensure_ascii=False,
    )
    first = outcome.plan.units[0]
    prompt = "\n".join(
        [
            "You chair the Design council for this DBTL research cycle.",
            "",
            "Project context:",
            stage_context,
            "",
            "independent council positions:",
            positions,
            "",
            "Debate instructions:",
            "- Compare disagreements, assumptions, risks, and evidence across the positions.",
            "- Do not average incompatible positions; explain the tradeoff.",
            "- If one project-owner decision is required, return status needs_input and ask exactly one focused clarification_question.",
            "- Otherwise return status completed with an operational design synthesis, explicit success and rejection criteria, and a recommendation to present it for human review.",
            "- You may recommend readiness, but you cannot submit, approve, or advance the stage.",
            "",
            "Result rules (these are validated, not stylistic):",
            "- Every entry in claims must be traceable to an entry in evidence_refs. A claim with no evidence rejects the whole result, so cite the council position it came from or move it to summary.",
            "- An evidence_refs entry needs a kind of artifact, workspace_file, dataset, or external, plus a non-empty reference. A council position is kind 'external' with the position's unit id as its reference.",
            '- quality_checks[].passed must be a JSON boolean, not the string "true".',
            "- needs_input requires a non-empty clarification_question; every other status requires it to be omitted or null.",
            "",
            "Return one JSON object and nothing else. The arrays below are shown empty only to give the shape; fill them in:",
            """{
  "status": "completed" | "needs_input" | "blocked" | "failed",
  "summary": "the council synthesis",
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
        ]
    )
    return WorkUnit(
        unit_id=f"{attempt_id}-chair",
        capability="design_council_chair",
        agent_name=first.agent_name,
        prompt=prompt,
        via_generalist=first.via_generalist,
        model=first.model,
        role="chair",
        focus="weighs the positions against each other",
        round=first.round,
    )


def _design_red_team_unit(
    outcome: StageExecutionOutcome,
    *,
    attempt_id: str,
) -> WorkUnit | None:
    """Guarantee an adversarial position, however many specialists were selected.

    Its brief is built from the first unit's, so it argues against the same
    stated task rather than a summary of it. It runs after the base round, so
    the chair always has at least one position and one challenge to weigh.
    """
    if not outcome.plan.units:
        return None
    first = outcome.plan.units[0]
    prompt = "\n".join(
        [
            first.prompt,
            "",
            "Independent debate role:",
            "Act as the Design council's red team. Challenge the proposed population, "
            "controls, leakage risks, success threshold, rejection criteria, and hidden "
            "assumptions. Seek a materially different defensible position rather than "
            "agreeing by default.",
        ]
    )
    return WorkUnit(
        unit_id=f"{attempt_id}-red-team",
        capability="design_red_team",
        agent_name=first.agent_name,
        prompt=prompt,
        via_generalist=first.via_generalist,
        model=first.model,
        role="red_team",
        focus="argues against the proposed design",
        round=first.round,
    )


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
        "satisfies_gate": False,
    }
    if council is not None:
        # The depth is a parameter of this attempt, not of the versioned
        # contract, so it is recorded here rather than by forking the spec.
        # Without it the budget a reviewer reconstructs from `stage_spec_key`
        # would not be the budget the workers actually had.
        payload["council"] = council.as_dict()
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
    ) -> None:
        self._repo = repo
        self._app_config = app_config
        self._candidate_provider = candidate_provider
        self._dispatcher = dispatcher
        self._runtime_config = runtime_config
        # Injected so a test can drive a roster without a model, and so an
        # absent writer degrades to capability selection rather than to nothing.
        self._roster_writer = roster_writer

    def _runtime(self, config: RunnableConfig) -> dict[str, Any]:
        merged = _runtime_view(self._runtime_config) if self._runtime_config is not None else {}
        merged.update(_runtime_view(config))
        return merged

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

    def _known_models(self) -> tuple[str, ...]:
        app_config = self._app_config
        models = getattr(app_config, "models", None) if app_config is not None else None
        if not models:
            return ()
        return tuple(str(getattr(item, "name", "") or "") for item in models if getattr(item, "name", None))

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
                reply = await reply
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
        cycle_state = str(cycle.get("state") or "")
        stage = "build" if cycle_state == "ready_for_build" else stage_for_state(cycle_state)
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
        """
        depth = council_depth_from_config(config) or recommend_depth(request_text).depth
        metadata = dict(config.get("metadata", {}) or {})
        model = str(metadata.get("model_name") or "").strip() or "inherited"
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
            tools = get_available_tools(
                model_name=effective_model,
                groups=metadata.get("tool_groups"),
                subagent_enabled=False,
                include_upload_tool=False,
                app_config=self._app_config,
            )
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
                token_budget_max_tokens=budget.max_tokens,
                extra_middlewares=[deadline],
            )
            await emit(
                {
                    "type": "task_started",
                    "task_id": unit.unit_id,
                    "description": _seat_description(unit),
                    "model_name": effective_model,
                    "council_seat": _seat_identity(unit, model=effective_model),
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
                        "council_seat": _seat_identity(unit, model=effective_model),
                    }
                )
                raise

            if result.status is SubagentStatus.COMPLETED:
                dispatch_outcome = DispatchOutcome(
                    unit_id=unit.unit_id,
                    text=result.result,
                    stop_reason=result.stop_reason,
                    forced_finalization=deadline.forced_any(),
                )
                await emit(
                    _terminal_seat_event(
                        unit,
                        dispatch_outcome,
                        model=effective_model,
                    )
                )
                return dispatch_outcome

            error = result.error or f"Subagent ended with status {result.status.value}."
            dispatch_outcome = DispatchOutcome(
                unit_id=unit.unit_id,
                text=result.result,
                stop_reason=result.stop_reason,
                error=error,
            )
            await emit(
                _terminal_seat_event(
                    unit,
                    dispatch_outcome,
                    model=effective_model,
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
        replay = await self._repo.get_stage_execution_replay(
            cycle_id,
            project_id=project_id,
            idempotency_key=execution_key,
        )
        if replay is not None:
            replay_stage = str(replay.get("stage") or "unknown")
            artifact_uri = replay.get("artifact_uri")
            worker_count = int(replay.get("worker_count") or 0)
            trustworthy_count = int(replay.get("trustworthy_count") or 0)
            clarification_question = None
            if replay_stage == "design" and trustworthy_count == 0:
                prior_runs = await self._repo.list_worker_runs(
                    cycle_id,
                    project_id=project_id,
                    stage="design",
                )
                for prior in reversed(prior_runs):
                    candidate = (prior.get("result") or {}).get("clarification_question")
                    if isinstance(candidate, str) and candidate.strip():
                        clarification_question = candidate.strip()
                        break
            if replay_stage == "learn":
                knowledge = await self._repo.knowledge_view(project_id, cycle_id=cycle_id)
                if not any(event.get("event_type") == "learn.synthesized" for event in knowledge["events"]):
                    prior_runs = await self._repo.list_worker_runs(
                        cycle_id,
                        project_id=project_id,
                        stage="learn",
                    )
                    build_test = await self._repo.build_test_view(cycle_id, project_id=project_id)
                    assessment = dict((build_test or {}).get("validity_assessment") or {})
                    summary, candidates = _learn_synthesis_payload(
                        [dict(item.get("result") or {}) for item in prior_runs],
                        test_outcome=str(assessment.get("outcome") or ""),
                        fallback_summary=str(artifact_uri or ""),
                    )
                    current = await self._repo.get_cycle(cycle_id, project_id=project_id)
                    if current is not None:
                        await self._repo.record_learn_synthesis(
                            cycle_id=cycle_id,
                            project_id=project_id,
                            summary=summary,
                            candidates=candidates,
                            actor_user_id=f"agent:{user_id}",
                            expected_db_revision=int(current["db_revision"]),
                            idempotency_key=f"{execution_key}:learn",
                        )
            return LiveStageResult(
                stage=replay_stage,
                cycle_id=cycle_id,
                note=(f"This run already recorded {worker_count} bounded {replay_stage} worker(s)" + (f" and the review package at {artifact_uri}." if artifact_uri else "; no usable review package was produced.")),
                worker_count=worker_count,
                produced_usable_evidence=trustworthy_count > 0,
                artifact_uri=str(artifact_uri) if artifact_uri else None,
                clarification_question=clarification_question,
            )

        cycle_state = str(cycle.get("state") or "")
        stage = "build" if cycle_state == "ready_for_build" else stage_for_state(cycle_state)
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
        reconciliation = await self._repo.reconciliation_view(cycle_id, project_id=project_id) if stage in {"reconciliation", "build", "test", "learn"} else None
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
        stage_context = json.dumps(
            {
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
                "reconciliation": reconciliation,
                "build_test": build_test,
                "project_workspace_manifest": await asyncio.to_thread(
                    _project_manifest,
                    project_root,
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
                "design_round": design_round,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        spec = resolve_stage_spec(stage)
        attempt_id = f"dbtl-{_safe_token(execution_key)}"
        council_plan: CouncilPlan | None = None
        if stage == "design":
            council_plan = self._plan_council(
                spec,
                config=config,
                request_text=request_text,
                attempt_id=attempt_id,
            )
            # Scoping the spec is what keeps the previewed roster and the
            # dispatched one the same computation: selection reads its worker
            # ceiling off the spec, and so does the dispatch budget.
            spec = replace(spec, budget=council_plan.budget)
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
        )
        proposal: CouncilProposal | None = None
        if stage == "design" and council_plan is not None:
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
                # that runs is the one they were shown.
                adjustment=council_adjustment,
            )
        if proposal is not None:
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
            )
            plan = StageExecutionPlan(spec=spec, selection=_proposed_selection(proposal), units=units)
            outcome = collect_results(plan, await dispatcher(units, budget=spec.budget))
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
        if stage == "design" and outcome.plan.dispatchable:
            # Unconditional: several specialists are several *positions*, not an
            # adversarial one. Skipping the red team once a second specialist
            # existed gave a better-configured council a weaker debate.
            red_team_unit = _design_red_team_unit(
                outcome,
                attempt_id=attempt_id,
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
                "counts_toward_stage_output": (stage != "design" or unit.capability == "design_council_chair"),
            }
            for unit, result in unit_result_pairs
        ]

        artifact_uri = None
        artifact_hash = None
        artifact_type = None
        artifact_digest = ""
        design_ready = stage != "design" or (chair_result is not None and chair_result.is_trustworthy and chair_result.status is WorkerStatus.COMPLETED)
        produced_usable_evidence = outcome.produced_usable_evidence and design_ready
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
                input_artifacts=[f"dataset:{item['source_key']}:{item['content_hash']}" for item in datasets],
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
        if clarification_question:
            note = f"Ran {len(results) - 1} independent Design council position(s) and a chair synthesis. The council paused before creating a review package because one human decision is required."
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
            note=note,
            worker_count=len(results),
            produced_usable_evidence=produced_usable_evidence,
            artifact_uri=artifact_uri,
            clarification_question=clarification_question,
        )
