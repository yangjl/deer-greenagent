"""Live DBTL stage execution over ``SubagentExecutor``.

The adapter is deliberately narrower than the lead agent: one selected,
project-owned cycle, one currently active executable stage, one bounded fan-out,
then durable evidence. It can never approve a stage; review remains a separate
human write bound to the evidence revision.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import platform
import re
import sys
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from inspect import isawaitable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig
    from deerflow.subagents.config import SubagentConfig

from deerflow.agents.dbtl.live_stage import token_usage as tokens
from deerflow.agents.dbtl.live_stage import workspace
from deerflow.agents.dbtl.live_stage.build_controls import DISABLED_GATE, BuildControlGate, BuildControlNotRecorded
from deerflow.agents.dbtl.live_stage.build_meeting import BUILD_WORK_MEETING_CONTRACT, MeetingContext, meeting_units, parse_recommendation
from deerflow.agents.dbtl.live_stage.build_phase_verification import (
    BuildPhaseVerification,
    entry_command,
    execute_and_verify_phase,
    local_dbtl_runtime_env,
    missing_scientific_packages,
    resolve_issued_input_tokens,
)
from deerflow.agents.dbtl.live_stage.build_phases import (
    GENERALIST,
    MAX_SCANNED_ENTRY_POINT_BYTES,
    PLANNER_ROLE,
    BuildPhaseManifest,
    admit_capped_phase,
    assign_phase,
    is_capped_phase_salvageable,
    parse_phase_manifest,
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
from deerflow.agents.dbtl.live_stage.build_recorder import (
    DISABLED_RECORDER,
    BuildStepRecorder,
    BuildStepRecordingError,
    RecorderRequest,
    StepHandle,
    make_build_step_recorder,
)
from deerflow.agents.dbtl.live_stage.build_review import (
    MAX_RECENT_REVIEWER_FEEDBACK,
    SUMMARIZER_ROLE,
    execution_bundle,
    parse_summary,
    summarizer_unit,
    write_build_deck,
    write_build_review,
)
from deerflow.agents.dbtl.live_stage.build_stage import (
    _build_phase_context,
    _build_plan_display_summary,
    _BuildSummary,
    _control_context,
    _declared_deliverable_fulfillments,
    _pause_note,
    _phase_note,
    _PhaseRun,
    _plan_execution,
    _restore_build_summary,
    _restore_phase,
    _restored_build_plan,
    _settled_control,
    _stops_at_boundary,
)
from deerflow.agents.dbtl.live_stage.design_input import approved_design_artifact, resolve_build_inputs
from deerflow.agents.dbtl.live_stage.feedback_surfaces import (
    FeedbackSurfacePlan,
    RenderedDeck,
    _persist_deck,
    plan_surface,
    registration_kwargs,
)
from deerflow.agents.dbtl.live_stage.replay import ReplayService
from deerflow.agents.dbtl.live_stage.test_rerun import (
    PreparedTestRerun,
    TestRerunRecord,
    build_test_rerun_tool,
    build_test_rerun_unit,
    prepare_test_rerun,
    rerun_result,
    validate_test_rerun,
)
from deerflow.agents.dbtl.live_stage.test_review import (
    TestReviewService,
)
from deerflow.agents.dbtl.live_stage.test_review import (
    validated_test_assessment as _validated_test_assessment,
)
from deerflow.agents.dbtl.live_stage.test_stage import (
    _has_reusable_test_evidence,
    _publish_test_owned_deliverables,
    _read_evidence_exception_package,
    _reusable_test_worker_results,
    _test_owned_deliverable_candidates,
    _write_evidence_exception_deck,
    _write_evidence_exception_package,
)
from deerflow.agents.dbtl.live_stage.types import LiveStageResult
from deerflow.agents.dbtl.live_stage.workspace import (
    SHELL_WORKSPACE_IDIOM,
    STAGE_UNIT_WORKSPACE_PLACEHOLDER,
    WORKSPACE_VIRTUAL_ROOT,
    atomic_write,
    prepare_stage_workspace,
    project_file_snapshot,
    project_manifest,
    safe_token,
    verified_workspace_files,
    workspace_lexical_path,
    workspace_relative_path,
)
from deerflow.agents.dbtl.model_access import authorize_model_use, filter_authorized_model_names
from deerflow.agents.middlewares.build_phase_correction_middleware import (
    BUILD_PHASE_CORRECTION_HEADROOM_STEPS,
    BuildPhaseCorrectionMiddleware,
)
from deerflow.agents.middlewares.dbtl_output_policy_middleware import bubblewrap_exec_command, sandbox_exec_command
from deerflow.agents.middlewares.finalization_deadline_middleware import (
    SUBAGENT_SUPERSTEPS_PER_TURN,
    FinalizationDeadlineMiddleware,
    model_call_budget,
)
from deerflow.authz.principal import normalize_authz_attributes
from deerflow.dbtl.agent_selector import AgentCandidate, Assignment, SelectionResult, build_candidates, select_agents
from deerflow.dbtl.build_control import (
    BuildControlAction,
    BuildControlAnswer,
    BuildControlKind,
    change_plan_request,
    execution_preflight_request,
    no_presentable_results_request,
    paused_build_recovery_request,
    phase_pause_request,
    plan_confirmation_request,
    step_failure_request,
    worker_question_request,
)
from deerflow.dbtl.build_driver import DriverPhase, driver_rerun_spec, render_driver_script
from deerflow.dbtl.build_execution import BuildExecutionBundle, BuildRerunSpec, parse_rerun_spec
from deerflow.dbtl.build_fulfillment import BUILD_FULFILLMENT_CONTRACT, BuildFulfillment, derive_build_fulfillment
from deerflow.dbtl.build_grant import INPUT_ENV_PREFIX, build_input_grant
from deerflow.dbtl.build_input import BuildInputBundle, BuildInputError, restore_build_input_bundle
from deerflow.dbtl.build_plan import BuildPhasePlan, parse_build_plan, single_phase_plan
from deerflow.dbtl.build_workflow import BuildErrorCode, BuildStepKey, StepState, phase_output_digest, plan_output_digest, resolve_build_workflow
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
from deerflow.dbtl.council_deck import chair_result as _chair_result_of
from deerflow.dbtl.council_deck import (
    extract_commentable_slides,
    render_authored_design_deck,
    render_council_deck,
)
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
from deerflow.dbtl.deliverable_audit import DeliverableAudit, DeliverableAuditRejected, parse_deliverable_audit
from deerflow.dbtl.deliverables import (
    DELIVERABLE_MANIFEST_CONTRACT,
    DeliverableManifestRejected,
    parse_deliverable_manifest,
)
from deerflow.dbtl.evidence_exception import (
    EvidenceExceptionDossier,
    EvidenceReason,
    build_evidence_exception_dossier,
    invalidated_test_exception_facts,
)
from deerflow.dbtl.meeting_intent import _NEW_DEBATE_PATTERN as _shared_new_debate_pattern
from deerflow.dbtl.meeting_intent import _RESTART_TYPOS as _shared_restart_typos
from deerflow.dbtl.meeting_intent import wants_new_debate
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
    BUILD_PLAN_OUTPUT,
    BUILD_SUMMARY_OUTPUT,
    BUILD_WORK_MEETING_OUTPUT,
    FORCED_BUILD_FINALIZATION_FAILURE,
    RESULT_CONTRACT,
    TEST_RERUN_OUTPUT,
    WORKSPACE_PATH_NOTE,
    AsyncWorkerDispatcher,
    DispatchOutcome,
    StageExecutionOutcome,
    StageExecutionPlan,
    WorkUnit,
    arun_stage,
    capped_worker_failure,
    collect_results,
    plan_stage,
    worker_rejection_failure,
)
from deerflow.dbtl.stage_spec import (
    StageSpec,
    StageSpecNotFound,
    WorkerBudget,
    resolve_review_stage_spec,
    resolve_spec_by_key,
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
    MAX_SUMMARY_CHARS,
    EvidenceRef,
    QualityCheck,
    StageWorkerResult,
    WorkerResultRejected,
    WorkerStatus,
    extract_result_payload,
    failed_result,
    parse_worker_result,
)
from deerflow.projects.storage import ensure_project_dirs, project_outputs_dir
from deerflow.runtime.activity.emitter import ActivityHandle, current_activity, current_activity_id, make_activity_handle
from deerflow.runtime.activity.envelope import ActivityScope
from deerflow.runtime.activity.lineage import supervisor_activity_id
from deerflow.runtime.activity.spans import optional_activity_span
from deerflow.runtime.activity.vocabulary import ActivityState, ActorKind
from deerflow.sandbox import get_sandbox_provider
from deerflow.sandbox.overwrite import unwrap_sandbox
from deerflow.subagents.step_streaming import SubagentStepStreamer, run_with_step_stream
from deerflow.tools.mcp_metadata import get_mcp_source, is_mcp_tool
from deerflow.trace_context import (
    DEERFLOW_TRACE_METADATA_KEY,
    get_current_trace_id,
    normalize_trace_id,
)

logger = logging.getLogger(__name__)

_BUILD_UNIT_SUBDIRECTORIES = ("src", "tests", "config", "artifacts", "logs")


def _prepare_unit_workspace(path: Path, *, stage: str) -> None:
    """Create the server-owned scratch layout before a worker is dispatched."""
    path.mkdir(parents=True, exist_ok=True)
    if stage == "build":
        for name in _BUILD_UNIT_SUBDIRECTORIES:
            path.joinpath(name).mkdir(exist_ok=True)


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

#: Seats that may inspect and must not act. `role` carries this rather than a
#: capability name because the rule is about what the seat is *for*, and a
#: capability can be covered by an agent with any tool set.
#:
#: The planner belongs here for the same reason the summarizer does, and its
#: absence was the gap between a contract and a guarantee: its prompt promised
#: "you write nothing, run nothing, and dispatch nobody" while it held the full
#: Build tool set, so the only thing stopping a planner from starting the build
#: it was asked to plan was the sentence asking it not to.
_READ_ONLY_ROLES = frozenset({SUMMARIZER_ROLE, PLANNER_ROLE})
_READ_ONLY_TOOL_NAMES = frozenset({"read_file", "ls", "glob", "grep"})
_MEMORY_TOOL_NAMES = frozenset({"memory_search", "memory_add", "memory_update", "memory_delete"})
_SUMMARIZER_TOOL_NAMES = _READ_ONLY_TOOL_NAMES | _MEMORY_TOOL_NAMES


def _runtime_view(config: RunnableConfig) -> dict[str, Any]:
    merged = dict(config.get("configurable", {}) or {})
    context = config.get("context", {}) or {}
    if isinstance(context, dict):
        merged.update(context)
    return merged


def _initial_stage_spec(stage: str) -> StageSpec:
    """Resolve the current contract for a new attempt."""
    return resolve_stage_spec(stage)


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


def _tools_for_unit(tools: Sequence[Any], unit: WorkUnit) -> list[Any]:
    """Withhold execution and write tools from a read-only seat.

    The Build summarizer reads a bundle the server already verified and says
    what it means. Giving it Bash would let a "summary" quietly become a second
    execution whose outputs nobody hashed, and giving it write tools would let
    it edit the evidence it is describing. Withholding the tools is what makes
    "read-only" a property rather than an instruction in a prompt.
    """
    if unit.role not in _READ_ONLY_ROLES:
        return list(tools)
    allowed = _SUMMARIZER_TOOL_NAMES if unit.role == SUMMARIZER_ROLE else _READ_ONLY_TOOL_NAMES
    return [tool for tool in tools if str(getattr(tool, "name", "")) in allowed]


def _with_craft_memory_tools(
    tools: Sequence[Any],
    worker_config: SubagentConfig,
    *,
    app_config: AppConfig,
) -> list[Any]:
    """Add existing explicit memory tools for an opted-in specialist."""
    if not worker_config.craft_memory:
        return list(tools)
    memory_config = getattr(app_config, "memory", None)
    if memory_config is None or not memory_config.enabled:
        return list(tools)

    from deerflow.agents.memory.manager import backend_requires_passive_writes_in_tool_mode
    from deerflow.agents.memory.tools import get_memory_tools

    if backend_requires_passive_writes_in_tool_mode(memory_config.manager_class):
        raise ValueError("The configured memory backend requires passive writes and cannot provide isolated craft memory tools.")

    result = list(tools)
    existing_names = {str(getattr(tool, "name", "")) for tool in result}
    result.extend(tool for tool in get_memory_tools() if str(getattr(tool, "name", "")) not in existing_names)
    return result


_HOST_FILESYSTEM_MCP_TOOL_NAMES = frozenset(
    {
        "create_directory",
        "directory_tree",
        "edit_file",
        "get_file_info",
        "list_allowed_directories",
        "list_directory",
        "list_directory_with_sizes",
        "move_file",
        "read_file",
        "read_media_file",
        "read_multiple_files",
        "read_text_file",
        "search_files",
        "write_file",
    }
)


def _is_host_filesystem_mcp_tool(tool: Any) -> bool:
    if not is_mcp_tool(tool):
        return False
    source = get_mcp_source(tool)
    original_name = str((source or {}).get("original_name") or getattr(tool, "name", ""))
    return original_name in _HOST_FILESYSTEM_MCP_TOOL_NAMES or str(getattr(tool, "name", "")).startswith("filesystem_")


def _tools_for_virtual_workspace(tools: Sequence[Any], *, writable_workspace: str | None) -> list[Any]:
    """Remove host-path filesystem MCP tools from a virtual-path worker.

    DBTL workers receive paths below ``/mnt/user-data`` and the built-in
    sandbox tools translate those paths to the project mount.  The optional
    filesystem MCP server has a different authority model: it accepts host
    paths below its configured roots and therefore cannot use the virtual path
    named in the stage contract.  Offering both tool families turns a denied
    shell call into a misleading second denial and invites a worker to try a
    host path that must never appear in its prompt.
    """
    if not writable_workspace or not writable_workspace.startswith("/mnt/user-data/"):
        return list(tools)
    return [tool for tool in tools if not _is_host_filesystem_mcp_tool(tool)]


def _token_limit_for_worker(unit: WorkUnit, budget: WorkerBudget) -> int | None:
    """Resolve an enforced ceiling, or ``None`` for metered-only execution."""
    if not budget.token_limit_enforced:
        return None
    return min(unit.max_tokens, budget.max_tokens) if unit.max_tokens else budget.max_tokens


def _effective_dispatch_budget(stage: str, budget: WorkerBudget) -> WorkerBudget:
    """Apply the operational safety ceiling to legacy uncapped Build specs.

    Existing stage attempts stay pinned to the version they started under, so
    publishing V7 alone would leave the exact V6 attempt that exposed this bug
    able to spend another 300k tokens.  This is an executor safety boundary,
    not a changed evidence contract: old attempts remain labelled V6 while no
    single worker may exceed the current operational ceiling.
    """
    if stage != "build" or budget.token_limit_enforced:
        return budget
    return replace(
        budget,
        max_turns=min(budget.max_turns, 450),
        max_tokens=min(budget.max_tokens, 120_000),
        token_limit_enforced=True,
    )


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


def _model_call_budget(max_turns: int, *, extra_headroom_steps: int = 0) -> int:
    """How many model calls fit in a turn budget.

    Delegates to the middleware that enforces the deadline, so the number the
    stage layer reserves and the number the deadline fires on are one
    computation. See ``SUBAGENT_SUPERSTEPS_PER_TURN`` for why this is not simply
    half of ``max_turns``.
    """
    return model_call_budget(
        max_turns,
        steps_per_turn=SUBAGENT_SUPERSTEPS_PER_TURN,
        extra_headroom_steps=extra_headroom_steps,
    )


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


#: Why a Build with entirely healthy workers can still refuse. Stated once so
#: the durable workflow step and the conversation note cannot drift into two
#: descriptions of one refusal.
MISSING_STRUCTURED_RERUN_REASON = "Build did not return the structured rerun record required by its pinned contract, so Test would have no command to re-execute. No review package was written."

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


def _terminal_seat_event(
    unit: WorkUnit,
    outcome: DispatchOutcome,
    *,
    model: str,
    meeting_stage: str | None = "design",
    stage: str | None = None,
    lineage: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Report contract-valid evidence progress, not child-graph termination."""
    base = {
        # Lineage first, so the unit's own identity below always wins: these
        # keys are additive context, never a channel for renaming the worker.
        **(dict(lineage) if lineage else {}),
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
    if stage == "build" and unit.role == "phase" and outcome.forced_finalization:
        return {
            "type": "task_failed",
            **base,
            "error": FORCED_BUILD_FINALIZATION_FAILURE,
            "stop_reason": outcome.stop_reason,
        }
    # Typed helper workers are deliberately not StageWorkerResult producers.
    # Their owning parser is the authority; applying the generic schema here
    # created a second, contradictory gate that could mark a successfully
    # consumed result as failed in the live lane.
    if unit.output_contract == BUILD_PLAN_OUTPUT:
        planned = parse_build_plan(outcome.text, objective="Build the approved design.")
        if "unparseable_plan" in planned.reasons:
            return {
                "type": "task_completed",
                **base,
                # Keep the worker's own response as audit data. The canonical
                # fallback plan is parsed once more with the cycle's actual
                # objective in ``_plan_build`` and recorded by the Build
                # workflow; reconstructing it here with a generic objective
                # would create two conflicting plans.
                "result": outcome.text or "",
                "display_summary": "The planner response was unreadable, so Build will run as one recorded fallback phase.",
                "degraded": True,
                "stop_reason": outcome.stop_reason,
            }
        return {
            "type": "task_completed",
            **base,
            # Preserve the planner's typed response here; the authoritative
            # normalized plan is stored by the Build workflow using the real
            # cycle objective.
            "result": outcome.text or "",
            "display_summary": _build_plan_display_summary(planned.plan),
            "stop_reason": outcome.stop_reason,
        }
    if unit.output_contract == BUILD_SUMMARY_OUTPUT:
        try:
            payload = extract_result_payload(outcome.text)
        except WorkerResultRejected as exc:
            return {
                "type": "task_failed",
                **base,
                "error": f"The Build result synthesis was unreadable: {exc}",
                "stop_reason": outcome.stop_reason,
            }
        question = payload.get("clarification_question")
        question = question.strip() if isinstance(question, str) else ""
        status = str(payload.get("status") or "").strip().lower()
        if status == WorkerStatus.NEEDS_INPUT.value or question:
            if not question:
                return {
                    "type": "task_failed",
                    **base,
                    "error": "The Build result synthesis asked for input without stating a question.",
                    "stop_reason": outcome.stop_reason,
                }
            return {
                "type": "task_completed",
                **base,
                "result": json.dumps(payload, ensure_ascii=False),
                "display_summary": question,
                "stop_reason": outcome.stop_reason,
            }
        headline = payload.get("headline") or payload.get("summary")
        headline = headline.strip() if isinstance(headline, str) else ""
        if not headline:
            return {
                "type": "task_failed",
                **base,
                "error": "The Build result synthesis did not state what the build produced.",
                "stop_reason": outcome.stop_reason,
            }
        return {
            "type": "task_completed",
            **base,
            "result": json.dumps(payload, ensure_ascii=False),
            "display_summary": headline,
            "stop_reason": outcome.stop_reason,
        }
    if unit.output_contract == BUILD_WORK_MEETING_OUTPUT:
        if unit.role != "chair":
            return {
                "type": "task_completed",
                **base,
                "result": outcome.text,
                "display_summary": outcome.text.strip()[:MAX_SUMMARY_CHARS],
                "stop_reason": outcome.stop_reason,
            }
        recommendation = parse_recommendation(outcome.text)
        if recommendation.refusal:
            return {
                "type": "task_failed",
                **base,
                "error": recommendation.refusal,
                "stop_reason": outcome.stop_reason,
            }
        return {
            "type": "task_completed",
            **base,
            "result": json.dumps(recommendation.as_dict(), ensure_ascii=False),
            "display_summary": recommendation.summary or recommendation.outcome.replace("_", " ").title(),
            "stop_reason": outcome.stop_reason,
        }
    if unit.output_contract == TEST_RERUN_OUTPUT:
        if outcome.error:
            return {
                "type": "task_failed",
                **base,
                "error": outcome.error,
                "stop_reason": outcome.stop_reason,
            }
        return {
            "type": "task_completed",
            **base,
            "result": "The command attempt finished; the server is verifying its fixed receipt files.",
            "display_summary": "Command attempt finished · verifying logs and output hashes",
            "stop_reason": outcome.stop_reason,
        }
    try:
        parsed = parse_worker_result(
            extract_result_payload(outcome.text),
            capability=unit.capability,
            agent_name=unit.agent_name,
            stop_reason=outcome.stop_reason,
            stage=stage,
        )
    except WorkerResultRejected as exc:
        return {
            "type": "task_failed",
            **base,
            # A capped worker was cut off before it could write its structured
            # result, so the shape of the fragment is the symptom and the cap is
            # the cause. Naming the fragment sends a reader to fix formatting
            # when the budget is the only lever, and reads as though the
            # phase's staged work was never worth anything.
            "error": worker_rejection_failure(outcome.stop_reason, str(exc)),
            "stop_reason": outcome.stop_reason,
        }
    if parsed.was_capped and parsed.status is not WorkerStatus.NEEDS_INPUT:
        return {
            "type": "task_failed",
            **base,
            # The worker's summary describes partial work, not why the phase
            # failed. Presenting it as the error paints success-like prose red
            # and hides the guardrail that made the result untrustworthy.
            "error": capped_worker_failure(outcome.stop_reason),
            "stop_reason": outcome.stop_reason,
        }
    completion_error = phase_completion_error(parsed, unit.completion_check)
    if completion_error:
        return {
            "type": "task_failed",
            **base,
            "error": completion_error,
            "stop_reason": outcome.stop_reason,
        }
    if parsed.status in {WorkerStatus.FAILED, WorkerStatus.BLOCKED}:
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
        "display_summary": parsed.summary,
        "stop_reason": outcome.stop_reason,
    }


RosterWriter = Callable[[str], Any]

#: Same shape as :data:`RosterWriter`: an async callable from prompt to raw text.
IntentInterpreter = Callable[[str], Any]
TransitionAssessor = Callable[[str], Any]


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


def make_llm_intent_interpreter(request_context: Mapping[str, Any] | None = None) -> IntentInterpreter | None:
    """The production debate-intent interpreter: one nostream model call.

    Returns ``None`` when no drafting model is configured, which the adapter
    reads as "the deterministic phrases are the whole answer". The same
    fail-soft contract as the roster writer: interpretation is an improvement
    on the phrase table, and no failure here may cost the owner their design —
    an unreadable verdict holds the package on the table, it never convenes.

    ``request_context`` carries the run's principal; ``model:use`` is enforced
    against it on every call, so a role denied the drafting model cannot invoke
    it through this side channel. A fail-closed denial raises and the caller's
    existing fail-soft handling holds.
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
            model_name=authorize_model_use(model_name, context=request_context, app_config=app_config),
        )

    return interpret


def make_llm_revision_interpreter(request_context: Mapping[str, Any] | None = None) -> RevisionInterpreter | None:
    """The production revision-route reader: one nostream model call.

    Returns ``None`` when no drafting model is configured, which the adapter
    reads as "take the cheap route". Same fail-soft contract as its siblings,
    with the direction of the failure chosen deliberately: an unavailable
    reader must never be the reason a full meeting reconvenes. ``model:use``
    is enforced against ``request_context`` on every call.
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
            model_name=authorize_model_use(model_name, context=request_context, app_config=app_config),
        )

    return interpret


def make_llm_roster_writer(request_context: Mapping[str, Any] | None = None) -> RosterWriter | None:
    """The production roster writer: one non-graph model call, tagged nostream.

    Returns ``None`` when no drafting model is configured, which the adapter
    reads as "fall back to capability selection". The same fail-soft contract as
    the setup-question writer: a roster is an improvement on selection, and no
    failure here may cost a cycle its Design stage. The call goes through
    ``run_oneshot_llm``, so its prompt and raw JSON never enter the thread.
    ``model:use`` is enforced against ``request_context`` on every call.
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
            model_name=authorize_model_use(model_name, context=request_context, app_config=app_config),
        )

    return write


def make_llm_transition_assessor(request_context: Mapping[str, Any] | None = None) -> TransitionAssessor | None:
    """The production remaining-work assessor: one nostream model call.

    ``model:use`` is enforced against ``request_context`` on every call; a
    fail-closed denial raises and the caller falls back to the standard gate.
    """
    from deerflow.config.app_config import get_app_config

    try:
        app_config = get_app_config()
    except Exception:  # noqa: BLE001 - assessment failure keeps the standard gate
        return None
    model_name = getattr(getattr(app_config, "dbtl", None), "setup_draft_model_name", None)
    if not model_name:
        return None

    async def assess(prompt: str) -> str:
        from deerflow.utils.oneshot_llm import run_oneshot_llm

        return await run_oneshot_llm(
            system_instruction=("You assess the difficulty of remaining research workflow work. You never approve evidence or invent routes. Reply with JSON only."),
            user_content=prompt,
            run_name="dbtl_transition_assessment",
            app_config=app_config,
            model_name=authorize_model_use(model_name, context=request_context, app_config=app_config),
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
    required_limitations: Sequence[str] = (),
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
            limitations = list(dict.fromkeys([*list(result.get("limitations") or []), *required_limitations]))
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
#:
#: The pattern and its predicate now live in ``deerflow.dbtl.meeting_intent``,
#: below both this adapter and the routing ladder. Routing has to answer the
#: same question one step earlier — should this request reach stage execution
#: at all — and two regexes that agree today drift apart the first time either
#: is edited. Re-exported here under the original private names so the existing
#: call sites and their tests keep reading the way they did.
_RESTART_TYPOS = _shared_restart_typos
_NEW_DEBATE_PATTERN = _shared_new_debate_pattern
_wants_new_debate = wants_new_debate


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

    Delegates to the same lookup ``load_design`` uses: the brief a model is
    shown and the document the step binds have to be the same file, or an
    approval names one thing while the worker implements another.
    """
    return approved_design_artifact(cycle)


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


# The containment and listing rules live in one module so the Build workflow's
# own steps resolve a worker-authored path exactly the way the adapter does. Two
# copies is how a check ends up enforced on one path and not the other.
_safe_token = safe_token
_prepare_stage_workspace = prepare_stage_workspace
_project_manifest = project_manifest
_project_file_snapshot = project_file_snapshot


async def _declared_skill_bindings(
    names: Sequence[str],
    *,
    user_id: str,
    app_config: Any,
) -> dict[str, str]:
    """Resolve enabled skill winners and bind their exact SKILL.md bytes."""

    wanted = tuple(dict.fromkeys(str(name).strip() for name in names if str(name).strip()))
    if not wanted:
        return {}
    from deerflow.skills.storage import get_or_new_user_skill_storage

    storage = await asyncio.to_thread(
        get_or_new_user_skill_storage,
        user_id,
        app_config=app_config,
    )
    skills = await asyncio.to_thread(storage.load_skills, enabled_only=True)
    by_name = {skill.name: skill for skill in skills}
    bindings: dict[str, str] = {}
    for name in wanted:
        skill = by_name.get(name)
        if skill is None:
            raise ValueError(f"Build skill {name!r} is not enabled for this user.")
        path = Path(skill.skill_file)
        try:
            if path.is_symlink() or not path.is_file():
                raise OSError("not a regular file")
            digest = await asyncio.to_thread(workspace.sha256_file, path)
        except OSError:
            raise ValueError(f"Build skill {name!r} has no readable regular SKILL.md file.") from None
        bindings[name] = f"skill:{name}:sha256:{digest}"
    return bindings


_workspace_lexical_path = workspace_lexical_path
_workspace_relative_path = workspace_relative_path
_verified_workspace_files = verified_workspace_files


def _bind_stage_unit_workspaces(
    units: Sequence[WorkUnit],
    stage_workspace: str | None,
) -> tuple[WorkUnit, ...]:
    if not stage_workspace:
        return tuple(units)
    return tuple(
        replace(
            unit,
            prompt=unit.prompt.replace(
                STAGE_UNIT_WORKSPACE_PLACEHOLDER,
                workspace.unit_stage_workspace(stage_workspace, unit.unit_id),
            ),
        )
        for unit in units
    )


def _directory_input_artifacts(
    *,
    relative: str,
    path: Path,
    project_root: str,
    pre_run_files: Mapping[str, tuple[int, int]],
    published_hashes: Mapping[str, str],
    required: bool,
) -> list[str]:
    """Expand a declared input directory into exact, hash-bound files.

    Workers naturally cite an earlier phase's output directory. The publisher
    records its files individually, so treating the directory as one missing
    file rejected valid sequential builds. Expansion is strict: every current
    file must belong either to the pre-run snapshot or this run's published
    index, and every byte/metadata binding is checked again.
    """
    prefix = relative.rstrip("/") + "/"
    del path  # Resolution is shared with output publication; do not trust a caller-resolved path.
    try:
        current = dict(
            _verified_workspace_files(
                relative,
                project_root=project_root,
                containment_reference=relative,
            )
        )
    except ValueError:
        raise ValueError(f"Build input directory {relative!r} contains a file outside the governed workspace.") from None
    except (FileNotFoundError, OSError):
        if required:
            raise ValueError(f"Build input directory {relative!r} is no longer readable.") from None
        return []

    published = {name: digest for name, digest in published_hashes.items() if name.startswith(prefix)}
    # A broad directory may legitimately contain unchanged source files and
    # outputs published by an earlier phase. The two authorities are additive,
    # with same-run publication winning on the impossible-but-safe overlap.
    snapshotted = {name: metadata for name, metadata in pre_run_files.items() if name.startswith(prefix) and name not in published}
    known = set(published) | set(snapshotted)
    if not known and required:
        raise ValueError(f"Build input directory {relative!r} was not present when this Build run started or published by an earlier phase.")
    if not known:
        return []
    if set(current) != known:
        raise ValueError(f"Build input directory {relative!r} changed during execution; rerun Build from unchanged source files and phase outputs.")

    artifacts: list[str] = []
    for name in sorted(published):
        try:
            digest = workspace.sha256_file(current[name])
        except OSError:
            raise ValueError(f"Build input {name!r} is no longer readable.") from None
        if digest != published[name]:
            raise ValueError(f"Build input {name!r} changed after this run published it; rerun Build from an unchanged source file.")
        artifacts.append(f"workspace_file:{name}:sha256:{digest}")

    for name in sorted(snapshotted):
        try:
            stat = current[name].stat()
            digest = workspace.sha256_file(current[name])
        except OSError:
            raise ValueError(f"Build input {name!r} is no longer readable.") from None
        if (stat.st_size, stat.st_mtime_ns) != snapshotted[name]:
            raise ValueError(f"Build input {name!r} changed during execution; rerun Build from an unchanged source file.")
        artifacts.append(f"workspace_file:{name}:sha256:{digest}")
    return artifacts


def _build_input_artifacts(
    *,
    datasets: Sequence[Mapping[str, Any]],
    results: Sequence[StageWorkerResult],
    project_root: str,
    pre_run_files: Mapping[str, tuple[int, int]],
    strict_workspace_inputs: bool = False,
    run_published: Mapping[str, str] | None = None,
    implementation_inputs: Sequence[str] | None = None,
) -> list[str]:
    """Bind Build's actual inputs without a separate declaration ceremony.

    Durable dataset bindings may contribute context, while exact workspace
    paths come from the validated worker contract and hashes are computed by
    the server, never requested from the person running the cycle.

    `run_published` is what *this run* already published, keyed by project-
    relative path and carrying the hash the publisher computed.  A multi-phase
    Build is sequential precisely so a later phase can read an earlier one's
    output, and those bytes cannot be in the pre-run snapshot by construction —
    the server wrote them minutes into the same run.  Judging every input
    against the snapshot alone therefore refused the normal shape of a
    multi-phase plan, with a message describing the snapshot rather than
    anything wrong with the work, and left the finished phases stranded behind
    a recovery card.

    These are bindings, not exemptions: the file is re-hashed and must still
    match what was published, so a governed output edited after publication is
    refused exactly like a source that changed mid-run.
    """
    published_hashes = dict(run_published or {})
    artifacts: list[str] = []
    for item in datasets:
        source_key = str(item.get("source_key") or "").strip()
        content_hash = str(item.get("content_hash") or "").strip().lower()
        if source_key and re.fullmatch(r"[0-9a-f]{64}", content_hash):
            artifacts.append(f"dataset:{source_key}:{content_hash}")

    references: list[tuple[str, bool]] = []
    if implementation_inputs is not None:
        references.extend((str(item), True) for item in implementation_inputs if isinstance(item, str))
    else:
        for result in results:
            inputs_examined = result.provenance.get("inputs_examined", ())
            if isinstance(inputs_examined, Sequence) and not isinstance(inputs_examined, (str, bytes)):
                references.extend((str(item), strict_workspace_inputs) for item in inputs_examined if isinstance(item, str))
            # Evidence may describe a produced output as a workspace file. Keep the
            # legacy discovery fallback, but only provenance-declared inputs are
            # strict: a new output was not present in the pre-run snapshot by design.
            references.extend((ref.reference, False) for ref in result.evidence_refs if ref.kind in {"workspace_file", "dataset"})

    for reference, required in references:
        if reference.startswith("dataset:"):
            continue
        # Workers sometimes name non-file context (for example "cycle
        # metadata") beside actual paths. It is not a hashable workspace input
        # and is ignored; anything path-shaped is required to resolve and bind.
        if required and not (reference.startswith("/") or "/" in reference or reference in pre_run_files):
            continue
        resolved = _workspace_relative_path(reference, project_root=project_root)
        if resolved is None:
            if required:
                raise ValueError(f"Build input {reference!r} is not a contained workspace file.")
            continue
        relative, path = resolved
        if path.is_dir():
            artifacts.extend(
                _directory_input_artifacts(
                    relative=relative,
                    path=path,
                    project_root=project_root,
                    pre_run_files=pre_run_files,
                    published_hashes=published_hashes,
                    required=required,
                )
            )
            continue
        published_hash = published_hashes.get(relative)
        if published_hash is not None:
            # An earlier phase's output. Already hashed by the publisher into a
            # content-addressed, model-unwritable path, so the binding is the
            # recorded hash -- re-read here so a file altered after publication
            # is refused rather than silently rebound to its new bytes.
            try:
                current = workspace.sha256_file(path)
            except OSError:
                raise ValueError(f"Build input {relative!r} is no longer readable.") from None
            if current != published_hash:
                raise ValueError(f"Build input {relative!r} changed after this run published it; rerun Build from an unchanged source file.")
            artifacts.append(f"workspace_file:{relative}:sha256:{published_hash}")
            continue
        before = pre_run_files.get(relative)
        if before is None:
            if required:
                raise ValueError(f"Build input {relative!r} was not present when this Build run started.")
            continue
        try:
            stat = path.stat()
        except OSError:
            if required:
                raise ValueError(f"Build input {relative!r} is no longer readable.") from None
            continue
        if (stat.st_size, stat.st_mtime_ns) != before:
            raise ValueError(f"Build input {relative!r} changed during execution; rerun Build from an unchanged source file.")
        artifacts.append(f"workspace_file:{relative}:sha256:{workspace.sha256_file(path)}")

    return list(dict.fromkeys(artifacts))


def _extend_unique(target: list[str], items: Iterable[str]) -> None:
    """Append what is not already there, preserving first-seen order.

    Spelled out rather than folded into an `extend` over a filtering generator:
    that reads the list it is appending to, so it only dedupes within a batch
    because `list.extend` happens to consume lazily. Correctness should not rest
    on that.
    """
    seen = set(target)
    for item in items:
        if item not in seen:
            seen.add(item)
            target.append(item)


def _published_input_index(
    published: Sequence[Mapping[str, Any]],
    *,
    project_root: str,
) -> dict[str, str]:
    """Index what this run has published so far, for later phases to bind against.

    Keyed the same way `_project_file_snapshot` keys its entries — project-
    relative POSIX — so one lookup answers "was this file here before the run,
    or did the run produce it?" without the two sides normalizing differently.
    """
    index: dict[str, str] = {}
    for entry in published:
        content_hash = str(entry.get("content_hash") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
            continue
        resolved = _workspace_relative_path(str(entry.get("uri") or ""), project_root=project_root)
        if resolved is None:
            continue
        index[resolved[0]] = content_hash
    return index


def _phase_granted_inputs(
    *,
    datasets: Sequence[Mapping[str, Any]],
    prior_published: Sequence[Mapping[str, Any]],
    pre_run_files: Mapping[str, tuple[int, int]],
    project_root: str,
    limit: int = 24,
) -> tuple[str, ...]:
    """Resolve a compact, stable input env before a worker spends model tokens."""
    candidates: list[str] = []
    for item in datasets:
        for field_name in ("uri", "path", "source_key"):
            value = str(item.get(field_name) or "").strip()
            if not value:
                continue
            reference = value if value.startswith("/mnt/user-data/") else f"/mnt/user-data/{value.lstrip('/')}"
            resolved = _workspace_relative_path(reference, project_root=project_root)
            if resolved is not None and resolved[1].is_file():
                candidates.append(f"/mnt/user-data/{resolved[0]}")
                break
    candidates.extend(str(item.get("uri") or "") for item in prior_published if str(item.get("uri") or ""))

    # Optional-Reconciliation projects may have no durable dataset URI. The
    # server already took this bounded snapshot; prefer data-shaped files, then
    # notes, rather than making every specialist rediscover the directory.
    priority_suffixes = (".csv", ".tsv", ".parquet", ".feather", ".xlsx", ".xls", ".json", ".md", ".txt")
    for relative in sorted(pre_run_files, key=lambda value: (next((i for i, suffix in enumerate(priority_suffixes) if value.lower().endswith(suffix)), len(priority_suffixes)), value)):
        if len(candidates) >= limit:
            break
        if relative.lower().endswith(priority_suffixes):
            candidates.append(f"/mnt/user-data/{relative}")

    granted: list[str] = []
    for reference in candidates:
        resolved = _workspace_relative_path(reference, project_root=project_root)
        canonical = f"/mnt/user-data/{resolved[0]}" if resolved is not None and resolved[1].is_file() else ""
        if canonical and canonical not in granted:
            granted.append(canonical)
        if len(granted) >= limit:
            break
    return tuple(granted)


def _published_source_text(reference: str, *, project_root: str) -> str | None:
    """Read a published file as source text, or report that it is not readable text.

    Returning ``None`` rather than raising on unreadable bytes is deliberate: a
    compiled or binary entry point has no source for the grant scanner to judge,
    and refusing it here would be this check asserting something it did not
    read. The size cap exists because the whole point is to inspect a script,
    and anything larger than a script is not one.
    """
    resolved = _workspace_relative_path(reference, project_root=project_root)
    if resolved is None or not resolved[1].is_file():
        return None
    try:
        if resolved[1].stat().st_size > MAX_SCANNED_ENTRY_POINT_BYTES:
            return None
        return resolved[1].read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _execute_server_build_command(
    command: str,
    env: dict[str, str],
    timeout_seconds: float,
    *,
    sandbox_state: Any,
    writable_workspace: str,
    thread_id: str,
    user_id: str,
    project_id: str,
    project_root: str,
) -> str:
    """Execute a verifier command in the run's sandbox, never on bare host Bash."""
    execution_env = {
        **env,
        "JUPYTER_CONFIG_DIR": f"{writable_workspace}/.jupyter/config",
        "JUPYTER_DATA_DIR": f"{writable_workspace}/.jupyter/data",
        "JUPYTER_RUNTIME_DIR": f"{writable_workspace}/.jupyter/runtime",
        "IPYTHONDIR": f"{writable_workspace}/.ipython",
        # Matplotlib is the same class as the Jupyter dirs above: importing
        # pyplot writes a font cache, and the home directory is outside the
        # phase grant. Left unset it warns on every figure and silently caches
        # in /tmp — an unmanaged write outside the workspace that is rebuilt on
        # each run. Keeping it phase-local makes the write governed and the
        # cache reusable within the phase.
        "MPLCONFIGDIR": f"{writable_workspace}/.matplotlib",
    }
    unwrapped, _ = unwrap_sandbox(sandbox_state)
    sandbox_id = unwrapped.get("sandbox_id") if isinstance(unwrapped, dict) else None
    provider = get_sandbox_provider()
    acquired = False
    if not isinstance(sandbox_id, str) or not sandbox_id or provider.get(sandbox_id) is None:
        if not thread_id or not user_id:
            raise RuntimeError("the run has no initialized sandbox and no identity with which to acquire one")
        sandbox_id = provider.acquire(thread_id, user_id=user_id, project_id=project_id, project_root=project_root)
        acquired = True
    sandbox = provider.get(sandbox_id)
    if sandbox is None:
        raise RuntimeError("the run's sandbox is no longer available")
    try:
        is_local = sandbox_id == "local" or sandbox_id.startswith("local:")
        if is_local:
            execution_env.update(local_dbtl_runtime_env())
        if is_local and sys.platform != "darwin":
            raise RuntimeError("local Build verification has no process-tree write sandbox on this platform")
        readable_inputs = tuple(value for key, value in env.items() if key.startswith(INPUT_ENV_PREFIX) and key != f"{INPUT_ENV_PREFIX}COUNT")
        if is_local:
            isolated = sandbox_exec_command(
                command,
                writable_paths=(writable_workspace,),
                readable_paths=readable_inputs,
                restricted_read_roots=("/mnt/user-data",),
            )
        else:
            probe = sandbox.execute_command("command -v bwrap", timeout=min(timeout_seconds, 10.0))
            executable = next((line.strip() for line in str(probe).splitlines() if line.strip().startswith("/") and line.strip().rsplit("/", 1)[-1] == "bwrap"), "")
            if not executable:
                raise RuntimeError("the remote sandbox cannot enforce the Build input read grant because bubblewrap (bwrap) is unavailable")
            isolated = bubblewrap_exec_command(
                command,
                executable=executable,
                writable_path=writable_workspace,
                readable_paths=readable_inputs,
            )
        return sandbox.execute_command(isolated, env=execution_env, timeout=timeout_seconds)
    finally:
        if acquired:
            provider.release(sandbox_id)


def _uses_local_sandbox(sandbox_state: Any) -> bool:
    """Whether this run executes commands in the Gateway host environment."""
    unwrapped, _ = unwrap_sandbox(sandbox_state)
    sandbox_id = unwrapped.get("sandbox_id") if isinstance(unwrapped, dict) else None
    if isinstance(sandbox_id, str) and sandbox_id:
        return sandbox_id == "local" or sandbox_id.startswith("local:")
    return type(get_sandbox_provider()).__name__ == "LocalSandboxProvider"


def _dbtl_worker_execution_env(
    *,
    sandbox_state: Any,
    workspace: str,
    project_root: str,
    declared_inputs: tuple[str, ...],
) -> dict[str, str]:
    """Issue the file grant and, only for local sandboxes, the gateway venv."""
    env = build_input_grant(
        workspace=workspace,
        project_root=project_root,
        declared_inputs=declared_inputs,
    )
    if _uses_local_sandbox(sandbox_state):
        env.update(local_dbtl_runtime_env())
    return env


def _design_deliverable_manifest(
    chair_result: StageWorkerResult,
    *,
    cycle_class: str,
) -> tuple[dict[str, object] | None, str]:
    """Validate the chair's promised products before Design becomes evidence."""
    raw = chair_result.provenance.get("deliverable_manifest")
    if raw is None:
        return None, "The Design chair did not return the required deliverable manifest, so the package is not reviewable."
    try:
        parsed = parse_deliverable_manifest(raw, cycle_class=cycle_class)
    except DeliverableManifestRejected as exc:
        return None, f"The Design deliverable manifest was rejected: {exc}"
    return parsed.as_dict(), ""


def _validated_deliverable_audit(
    results: Sequence[StageWorkerResult],
    *,
    manifest: Any,
    build_test: Mapping[str, Any],
    stage_artifacts: Sequence[Mapping[str, Any]] = (),
) -> tuple[DeliverableAudit | None, str]:
    lineage = build_test.get("build_lineage")
    outputs = lineage.get("output_artifacts") if isinstance(lineage, Mapping) else []
    output_rows = [
        item
        for item in [
            *(outputs if isinstance(outputs, Sequence) else []),
            *stage_artifacts,
        ]
        if isinstance(item, Mapping)
    ]
    expected_by_basename: dict[str, list[str]] = {}
    for deliverable in manifest.deliverables:
        for expected_path in deliverable.expected_paths:
            expected_by_basename.setdefault(expected_path.rsplit("/", 1)[-1], []).append(expected_path)
    published_basename_counts: dict[str, int] = {}
    for item in output_rows:
        source_path = str(item.get("source_path") or "")
        basename = source_path.rsplit("/", 1)[-1]
        published_basename_counts[basename] = published_basename_counts.get(basename, 0) + 1

    def canonical_path(item: Mapping[str, object]) -> str:
        source_path = str(item.get("source_path") or "")
        basename = source_path.rsplit("/", 1)[-1]
        candidates = expected_by_basename.get(basename, [])
        if len(candidates) == 1 and published_basename_counts.get(basename) == 1:
            return candidates[0]
        return source_path

    published = {canonical_path(item): str(item.get("content_hash") or "") for item in output_rows}
    published_by_uri: dict[str, tuple[str, str]] = {}
    for item in output_rows:
        bound = (canonical_path(item), str(item.get("content_hash") or ""))
        for uri_field in ("uri", "source_uri"):
            uri = str(item.get(uri_field) or "")
            if uri:
                published_by_uri[uri] = bound
    _hash_to_paths: dict[str, set[str]] = {}
    for item in output_rows:
        _h = str(item.get("content_hash") or "").lower()
        _sp = canonical_path(item)
        if _h and _sp:
            _hash_to_paths.setdefault(_h, set()).add(_sp)
    # Only bind by hash when it names exactly one published file. Two deliverables
    # with identical bytes share a hash, so hash alone cannot disambiguate them —
    # those fall through to path matching rather than risk a wrong binding.
    published_by_hash = {_h: next(iter(_sps)) for _h, _sps in _hash_to_paths.items() if len(_sps) == 1}
    refusals: list[str] = []
    for result in results:
        raw = result.provenance.get("deliverable_audit")
        if raw is None:
            continue
        if isinstance(raw, Mapping) and isinstance(raw.get("items"), Sequence):
            normalized_items: list[object] = []
            for item in raw["items"]:
                if not isinstance(item, Mapping) or not isinstance(item.get("observed_artifacts"), Sequence):
                    normalized_items.append(item)
                    continue
                normalized_artifacts: list[object] = []
                for artifact in item["observed_artifacts"]:
                    if not isinstance(artifact, Mapping):
                        normalized_artifacts.append(artifact)
                        continue
                    uri = str(artifact.get("path") or "")
                    claimed_hash = str(artifact.get("content_hash") or artifact.get("sha256") or "").lower()
                    bound = published_by_uri.get(uri)
                    if bound is not None and claimed_hash == bound[1].lower():
                        normalized_artifacts.append({**artifact, "path": bound[0], "content_hash": bound[1]})
                    elif claimed_hash and claimed_hash in published_by_hash:
                        # A worker that cites a deliverable by a guessed path (e.g.
                        # the full content hash, or the governed filename it did
                        # not read exactly) but reports the correct sha256 is still
                        # auditing a real published artifact. Bind it by content
                        # hash — the byte identity — to the governed source path
                        # rather than rejecting the whole audit over the path
                        # string. The subsequent hash check still guards integrity.
                        normalized_artifacts.append({**artifact, "path": published_by_hash[claimed_hash], "content_hash": claimed_hash})
                    else:
                        normalized_artifacts.append(artifact)
                normalized_items.append({**item, "observed_artifacts": normalized_artifacts})
            raw = {**raw, "items": normalized_items}
        try:
            audit = parse_deliverable_audit(raw, manifest=manifest)
        except DeliverableAuditRejected as exc:
            refusals.append(str(exc))
            continue
        if any(published.get(artifact.path) != artifact.content_hash for item in audit.items for artifact in item.observed_artifacts):
            refusals.append("The deliverable audit cited an artifact hash that is not in the server-owned Build lineage.")
            continue
        return audit, ""
    detail = f" ({'; '.join(refusals[:3])})" if refusals else ""
    return None, f"Test did not return a valid independent audit for every approved Design deliverable{detail}."


#: The design-council chair's result contract: the rules the server actually
#: validates, plus the JSON shape it validates them against. Chairing a fresh
#: meeting and resuming one the human answered are different prompts, but these
#: rules are identical -- and two copies is how one caller ends up stating a
#: rule the other has already dropped.
CHAIR_RESULT_CONTRACT_LINES: tuple[str, ...] = (
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
    "",
    DELIVERABLE_MANIFEST_CONTRACT,
)


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
            *CHAIR_RESULT_CONTRACT_LINES,
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
            *CHAIR_RESULT_CONTRACT_LINES,
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
        "Report your review using the stage-worker contract below. Claims must point to the recorded evidence URI above; this meeting annotates that evidence and cannot replace it.",
        RESULT_CONTRACT,
        "",
        "The chair may additionally report structured consensus:",
        CONSENSUS_CONTRACT,
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
    deliverable_audit: DeliverableAudit | None = None,
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
    if outcome.plan.spec.stage == "design" and cycle.get("cycle_class"):
        chair = next((item for item in reversed(outcome.results) if item.capability == "design_council_chair"), None)
        if chair is not None:
            deliverable_manifest, refusal = _design_deliverable_manifest(
                chair,
                cycle_class=str(cycle["cycle_class"]),
            )
            if deliverable_manifest is None:
                raise ValueError(refusal)
            payload["deliverable_manifest"] = deliverable_manifest
    if outcome.plan.spec.stage == "test" and deliverable_audit is not None:
        # Persist the exact audit the server validated against Build lineage,
        # never a raw worker payload that merely happened to appear first.
        payload["deliverable_audit"] = deliverable_audit.as_dict()
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
        atomic_write(outputs / relative, content)

    uri = f"/mnt/user-data/outputs/{document_relative.as_posix()}"
    digest = render_stage_digest(payload, document_path=f"outputs/{document_relative.as_posix()}")
    return uri, document_hash, digest


def _write_build_driver(
    *,
    project_root: str,
    cycle: dict[str, Any],
    results: Sequence[StageWorkerResult],
    bound_inputs: Sequence[str] = (),
) -> BuildRerunSpec | None:
    """Record how to re-run a phased Build, as one command the server wrote.

    A phased Build has one entry point per phase, and the worker-supplied rerun
    specs merge only when they agree — so two phases naming different entry
    points conflicted, `parse_execution_bundle` reported no rerun record, and a
    Build that ran every planned phase failed `structured_rerun_spec` and could
    never reach its human gate. The phase prompt never asked for the record
    either, so the gate was unsatisfiable in production regardless.

    Deriving it is also the more trustworthy answer: these entry points are the
    ones the server verified and executed itself, so the record describes what
    ran rather than a worker's account of it.

    Returns ``None`` and writes nothing when no phase declared a runnable entry
    point — the gate must still be able to fail.
    """
    phases: list[DriverPhase] = []
    outputs: list[str] = []
    for result in results:
        manifest = parse_phase_manifest((result.provenance or {}).get("phase_manifest"))
        if manifest is None:
            continue
        phases.append(DriverPhase(title=result.summary[:80], entry_point=manifest.entry_point, execution_inputs=tuple(manifest.execution_inputs)))
        phase_rerun = parse_rerun_spec((result.provenance or {}).get("rerun_spec"))
        outputs.extend(phase_rerun.expected_outputs if phase_rerun is not None else (path for path in manifest.declared_outputs if path != manifest.entry_point))
    script = render_driver_script(phases, workspace_root=WORKSPACE_VIRTUAL_ROOT, project_root=WORKSPACE_VIRTUAL_ROOT)
    if not script:
        return None

    document = script.encode("utf-8")
    content_hash = hashlib.sha256(document).hexdigest()
    try:
        root = Path(project_root).expanduser().resolve()
        ensure_project_dirs(root)
        relative = stage_output_dir(
            cycle_id=str(cycle["id"]),
            cycle_title=str(cycle.get("title") or ""),
            stage="build",
        ) / stage_file_name(stage="build", kind="rerun", revision=cycle.get("db_revision"), content_hash=content_hash)
        atomic_write(project_outputs_dir(root) / relative, document)
    except Exception:  # noqa: BLE001 - a Build with no rerun record refuses at the gate
        logger.warning("Could not write the Build rerun driver.", exc_info=True)
        return None

    # The interpreters the server actually used, rather than a guess about the
    # host: a rerun record naming an environment nobody verified is the kind of
    # unchecked reassurance this gate exists to prevent.
    interpreters = sorted({entry_command(phase.entry_point).split(" ", 1)[0] for phase in phases if phase.entry_point.strip()})
    return driver_rerun_spec(
        phases,
        driver_path=f"/mnt/user-data/outputs/{relative.as_posix()}",
        expected_outputs=outputs,
        bound_inputs=bound_inputs,
        environment={"interpreters": ", ".join(interpreters)} if interpreters else {"interpreters": "unknown"},
    )


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
        deck_html = render_council_deck(
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
            # Background and Objectives are the cycle's own words, quoted from
            # the record it was opened with. The deck authors nothing here.
            research_question=str(cycle.get("research_question") or ""),
            objective=str(cycle.get("objective") or ""),
            success_criteria=cycle.get("success_criteria") or (),
        )
        document = deck_html.encode("utf-8")
    except Exception:  # noqa: BLE001 - a presentation must not break the record
        logger.warning("Could not render the design meeting slide deck.", exc_info=True)
        return None

    return _persist_deck(
        project_root=project_root,
        cycle=cycle,
        stage=stage,
        document=document,
        commentable_slides=extract_commentable_slides(deck_html),
    )


def _write_authored_design_deck(
    *,
    project_root: str,
    cycle: Mapping[str, Any],
    authored_design: str,
    package_path: str,
    surface_id: str,
    surface_mode: str,
) -> RenderedDeck | None:
    """The owner's own design, rendered into the deck they answer it in."""
    try:
        deck_html = render_authored_design_deck(
            cycle_title=str(cycle.get("title") or ""),
            authored_design=authored_design,
            package_path=package_path,
            surface_id=surface_id,
            surface_mode=surface_mode,
            research_question=str(cycle.get("research_question") or ""),
            objective=str(cycle.get("objective") or ""),
            success_criteria=cycle.get("success_criteria") or (),
        )
        document = deck_html.encode("utf-8")
    except Exception:  # noqa: BLE001 - a presentation must not break the record
        logger.warning("Could not render the authored design slide deck.", exc_info=True)
        return None
    return _persist_deck(
        project_root=project_root,
        cycle=cycle,
        stage="design",
        document=document,
        commentable_slides=extract_commentable_slides(deck_html),
    )


_MAX_BUILD_PUBLISHED_FILES_PER_UNIT = 500


def _publish_build_worker_artifacts(
    *,
    project_root: str,
    cycle: Mapping[str, Any],
    outcome: StageExecutionOutcome,
    stage_workspace: str,
    attempt_id: str,
) -> tuple[StageExecutionOutcome, list[dict[str, Any]]]:
    """Validate and publish Build outputs before they can count as evidence.

    Worker-authored references are untrusted strings.  Each completed Build
    result must point to at least one regular file inside that unit's isolated
    directory.  The adapter hashes and copies those bytes into the governed,
    content-addressed output tree, then rewrites the durable result to the
    published URI.  A bad reference converts only that worker to a failed
    result; it can never become Build lineage.
    """
    published: list[dict[str, Any]] = []
    validated_results: list[StageWorkerResult] = []
    rejected = list(outcome.rejected)
    stage_dir = stage_output_dir(
        cycle_id=str(cycle["id"]),
        cycle_title=str(cycle.get("title") or ""),
        stage="build",
    )
    outputs_root = project_outputs_dir(Path(project_root).expanduser().resolve())

    for unit, result in zip(outcome.plan.units, outcome.results, strict=True):
        if not result.is_trustworthy:
            validated_results.append(result)
            continue
        unit_workspace = workspace.unit_stage_workspace(stage_workspace, unit.unit_id)
        lexical_root = _workspace_lexical_path(unit_workspace, project_root=project_root)
        failure = ""
        remapped: dict[str, tuple[str, ...]] = {}
        file_remapped: dict[str, str] = {}
        published_sources: dict[str, str] = {}
        published_uris: set[str] = set()
        unit_outputs: list[dict[str, Any]] = []
        if lexical_root is None:
            failure = "The adapter could not resolve the worker's isolated Build workspace."
        elif not result.artifact_refs:
            failure = "A completed Build worker must return at least one artifact created in its isolated workspace."
        else:
            workspace_lexical = lexical_root[1]
            workspace_host = workspace_lexical.resolve()
            try:
                workspace_host.relative_to(Path(project_root).expanduser().resolve())
            except ValueError:
                failure = "The worker's Build workspace resolves outside the project."
            for reference in result.artifact_refs:
                if failure:
                    break
                remaining_files = _MAX_BUILD_PUBLISHED_FILES_PER_UNIT - len(published_sources)
                if remaining_files < 1:
                    failure = f"Build worker {unit.unit_id!r} declared more than {_MAX_BUILD_PUBLISHED_FILES_PER_UNIT} output files."
                    break
                try:
                    sources = _verified_workspace_files(
                        reference,
                        project_root=project_root,
                        containment_reference=unit_workspace,
                        relative_to_containment=True,
                        max_files=remaining_files,
                    )
                except FileNotFoundError:
                    failure = f"Build artifact {reference!r} does not exist in this worker's isolated workspace."
                    break
                except OSError:
                    failure = f"Build artifact {reference!r} could not be read from this worker's isolated workspace."
                    break
                except ValueError as exc:
                    detail = str(exc)
                    if "file limit" in detail:
                        failure = f"Build artifact directory {reference!r} exceeds the {_MAX_BUILD_PUBLISHED_FILES_PER_UNIT}-file publication limit."
                    elif "not a regular file or directory" in detail:
                        failure = f"Build artifact {reference!r} is not a regular file or directory."
                    else:
                        failure = f"Build artifact {reference!r} is outside this worker's isolated workspace."
                    break
                if not sources:
                    failure = f"Build artifact directory {reference!r} contains no regular files."
                    break
                reference_uris: list[str] = []
                for source_relative, source in sources:
                    uri = published_sources.get(source_relative)
                    if uri is None:
                        try:
                            content_hash = workspace.sha256_file(source)
                            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", source.name).strip("-.") or "artifact"
                            destination_relative = stage_dir / "artifacts" / attempt_id / _safe_token(unit.unit_id) / f"{content_hash[:16]}-{safe_name[:96]}"
                            destination = outputs_root / destination_relative
                            workspace.atomic_copy(source, destination, expected_hash=content_hash)
                        except (OSError, ValueError):
                            failure = f"Build artifact {reference!r} changed or became unreadable while it was being published."
                            break
                        uri = f"{WORKSPACE_VIRTUAL_ROOT}/outputs/{destination_relative.as_posix()}"
                        published_sources[source_relative] = uri
                        try:
                            grant_relative = source.relative_to(workspace_host).as_posix()
                        except ValueError:
                            grant_relative = ""
                        if uri not in published_uris:
                            published_uris.add(uri)
                            unit_outputs.append(
                                {
                                    "uri": uri,
                                    "content_hash": content_hash,
                                    "revision": 1,
                                    "unit_id": unit.unit_id,
                                    "source_path": grant_relative or source_relative,
                                }
                            )
                    reference_uris.append(uri)
                    file_remapped[source_relative] = uri
                    file_remapped[f"{WORKSPACE_VIRTUAL_ROOT}/{source_relative}"] = uri
                    # Preserve the worker's exact locator as an alias only
                    # when it names this file itself. A directory that happens
                    # to contain one file must not become an entry point by
                    # inference; its relative name differs from its child's.
                    try:
                        grant_relative = source.relative_to(workspace_host).as_posix()
                    except ValueError:
                        grant_relative = ""
                    normalized_reference = PurePosixPath(reference.strip()).as_posix()
                    if normalized_reference in {
                        source_relative,
                        f"{WORKSPACE_VIRTUAL_ROOT}/{source_relative}",
                        grant_relative,
                    }:
                        file_remapped[reference] = uri
                if failure:
                    break
                remapped[reference] = tuple(reference_uris)

        if failure:
            rejected.append(f"{unit.unit_id}: {failure}")
            validated_results.append(
                replace(
                    failed_result(
                        capability=result.capability,
                        agent_name=result.agent_name,
                        reason=failure,
                    ),
                    token_usage=result.token_usage,
                )
            )
            continue

        evidence_refs: list[EvidenceRef] = []
        for ref in result.evidence_refs:
            targets = remapped.get(ref.reference)
            if targets is None and ref.reference in file_remapped:
                targets = (file_remapped[ref.reference],)
            if targets is None:
                evidence_refs.append(ref)
                continue
            evidence_refs.extend(EvidenceRef(kind="artifact", reference=target, description=ref.description) for target in targets)

        def remap_single(reference: str) -> str:
            direct = file_remapped.get(reference)
            if direct is not None:
                return direct
            expanded = remapped.get(reference, ())
            return expanded[0] if len(expanded) == 1 else reference

        def remap_many(value: Any) -> list[Any]:
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                return []
            mapped_values: list[Any] = []
            for item in value:
                if not isinstance(item, str):
                    mapped_values.append(item)
                    continue
                direct = file_remapped.get(item)
                expanded = remapped.get(item)
                mapped_values.extend(expanded or ((direct,) if direct else (item,)))
            deduplicated: list[Any] = []
            seen_strings: set[str] = set()
            for item in mapped_values:
                if isinstance(item, str):
                    if item in seen_strings:
                        continue
                    seen_strings.add(item)
                deduplicated.append(item)
            return deduplicated

        def remap_entry_point(raw_entry_point: str) -> str:
            # The remap is keyed on the worker's exact declared strings. A worker
            # that names its entry point in a different but equivalent form
            # (unit-relative vs full virtual) would otherwise skip the remap and
            # leave a governed-tree check comparing an un-rewritten path —
            # discarding a phase that already ran and published. Fall back to the
            # published file whose path tail matches.
            direct = file_remapped.get(raw_entry_point)
            if direct is not None:
                return direct
            ep_parts = [p for p in raw_entry_point.strip("/").split("/") if p]
            for key, mapped in file_remapped.items():
                key_parts = [p for p in str(key).strip("/").split("/") if p]
                n = min(len(ep_parts), len(key_parts))
                if n and ep_parts[-n:] == key_parts[-n:]:
                    return mapped
            return raw_entry_point

        provenance = dict(result.provenance)
        raw_rerun = provenance.get("rerun_spec")
        if isinstance(raw_rerun, Mapping):
            rerun = dict(raw_rerun)
            raw_entry_point = rerun.get("entry_point")
            if isinstance(raw_entry_point, str):
                # An entry point is one exact file. Expanding a one-file
                # directory into that child would let the server guess a
                # declaration the worker never made.
                rerun["entry_point"] = remap_entry_point(raw_entry_point)
            for field_name in ("inputs", "configuration", "expected_outputs"):
                rerun[field_name] = remap_many(rerun.get(field_name))
            provenance["rerun_spec"] = rerun
        raw_phase_manifest = provenance.get("phase_manifest")
        if isinstance(raw_phase_manifest, Mapping):
            phase_manifest = dict(raw_phase_manifest)
            raw_entry_point = phase_manifest.get("entry_point")
            if isinstance(raw_entry_point, str):
                phase_manifest["entry_point"] = remap_entry_point(raw_entry_point)
            phase_manifest["declared_outputs"] = remap_many(phase_manifest.get("declared_outputs"))
            provenance["phase_manifest"] = phase_manifest

        validated_results.append(
            replace(
                result,
                artifact_refs=tuple(dict.fromkeys(uri for reference in result.artifact_refs for uri in remapped[reference])),
                evidence_refs=tuple(evidence_refs),
                provenance=provenance,
                # Declarations are remapped alongside the artifacts they
                # describe. A figure still naming the worker's own scratch path
                # would fail verification against the published outputs and be
                # reported as a plot that does not exist — when it does, at the
                # governed path the server just wrote.
                figures=tuple(replace(figure, path=remap_single(figure.path)) for figure in result.figures),
                key_outcomes=tuple(replace(outcome, figure=remap_single(outcome.figure)) for outcome in result.key_outcomes),
            )
        )
        published.extend(unit_outputs)

    return (
        replace(
            outcome,
            results=tuple(validated_results),
            rejected=tuple(rejected),
        ),
        published,
    )


async def _settle_execution_step(
    recorder: BuildStepRecorder,
    handle: StepHandle,
    *,
    published: Sequence[Mapping[str, Any]],
    outcome: StageExecutionOutcome,
    incomplete_because: str = "",
) -> None:
    """Close the execution step against the artifacts actually published.

    The digest binds the *published* bytes rather than the worker's own account
    of them: the copies under the governed output tree are what a later step
    reads, and hashing what the worker claimed would let a step be reported
    valid against files that were never written.

    `incomplete_because` is how a half-run plan settles. The container answers
    "did the build run", and a plan that stopped at a failed phase or a
    `pause_after` boundary did not — succeeding it because the phases that *did*
    run published something would tell the next step, and the reader, that the
    build is finished. Its own phase rows stay succeeded and reusable, which is
    the whole point of recording them separately.
    """
    if not handle.recorded:
        return
    if incomplete_because:
        await recorder.fail(handle, BuildErrorCode.EXECUTION_CONTRACT_REJECTED, incomplete_because)
        return
    if not outcome.trustworthy_results:
        await recorder.fail(
            handle,
            BuildErrorCode.EXECUTION_CONTRACT_REJECTED,
            "; ".join(outcome.rejected) or "No Build worker returned a usable structured result.",
        )
        return
    if not published:
        await recorder.fail(
            handle,
            BuildErrorCode.EXECUTION_OUTPUT_MISSING,
            "The Build worker completed but published no output the server could verify.",
        )
        return
    digest = hashlib.sha256(
        json.dumps(
            [{"uri": str(item.get("uri") or ""), "content_hash": str(item.get("content_hash") or "")} for item in published],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    await recorder.succeed(handle, digest, execution={"published_outputs": len(published)})


async def _emit_build_terminal_correction(unit: WorkUnit, event: dict[str, Any]) -> None:
    """Correct an earlier terminal event for this worker after server verification."""

    try:
        from langgraph.config import get_stream_writer

        from deerflow.utils.custom_events import aemit_custom_event

        await aemit_custom_event(
            {"task_id": unit.unit_id, "dbtl_stage": "build", **event},
            writer=get_stream_writer(),
        )
    except RuntimeError:
        # Unit tests and non-stream callers have no LangGraph stream writer.
        pass


async def _emit_build_verification_failure(unit: WorkUnit, error: str) -> None:
    await _emit_build_terminal_correction(
        unit,
        {"type": "task_failed", "error": error, "display_summary": "Build output verification failed."},
    )


async def _emit_build_cap_salvage_admitted(unit: WorkUnit, result: StageWorkerResult) -> None:
    """Undo the `task_failed` the cap raised, now that the hard gates have passed."""

    await _emit_build_terminal_correction(
        unit,
        {
            "type": "task_completed",
            "result": json.dumps(result.as_dict(), ensure_ascii=False),
            "display_summary": result.summary,
        },
    )


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

    async def recover_test_learn_handoff(
        self,
        *,
        project_id: str | None,
        cycle_id: str | None,
    ) -> dict[str, Any] | None:
        """Recover the visible Learn start control after a Test API write."""
        if not project_id or not cycle_id:
            return None
        cycle = await self._repo.get_cycle(cycle_id, project_id=project_id)
        if cycle is None or cycle.get("state") != "learn":
            return None
        stages = {str(item.get("stage") or ""): str(item.get("status") or "") for item in cycle.get("stages", [])}
        if stages.get("learn") != "in_progress":
            return None
        view = await self._repo.build_test_view(cycle_id, project_id=project_id)
        assessment = dict((view or {}).get("validity_assessment") or {})
        test_route = (stages.get("test"), assessment.get("recommendation"))
        if test_route not in {
            ("approved", "advance_to_learn"),
            ("advanced_with_exception", "learn_from_invalidated_evidence"),
        }:
            return None
        marker = {
            "version": 1,
            "cycle_id": cycle_id,
            "cycle_revision": int(cycle.get("db_revision") or 0),
            "approved_stage": "test",
            "next_stage": "learn",
            "surface_id": str(assessment.get("id") or cycle_id),
        }
        if test_route == ("advanced_with_exception", "learn_from_invalidated_evidence"):
            marker["advanced_with_exception"] = True
        return marker

    async def recover_test_retry_control(
        self,
        *,
        project_id: str | None,
        cycle_id: str | None,
    ) -> dict[str, Any] | None:
        """Recover Start Test after a human selected Repeat Test."""
        if not project_id or not cycle_id:
            return None
        cycle = await self._repo.get_cycle(cycle_id, project_id=project_id)
        if cycle is None or _executable_stage(cycle) != "test":
            return None
        view = await self._repo.build_test_view(cycle_id, project_id=project_id)
        assessment = dict((view or {}).get("validity_assessment") or {})
        repeat_test = assessment.get("recommendation") == "repeat_test"
        test_attempt = next((item for item in cycle.get("stages", []) if item.get("stage") == "test"), None)
        stage_attempt_id = str((test_attempt or {}).get("id") or "")
        evidence_recovery = False
        if not repeat_test and stage_attempt_id:
            stored = await self._repo.list_worker_runs(cycle_id, project_id=project_id, stage="test")
            evidence_recovery = _has_reusable_test_evidence(stored, stage_attempt_id=stage_attempt_id)
        if not repeat_test and not evidence_recovery:
            return None
        return {
            "version": 1,
            "cycle_id": cycle_id,
            "cycle_revision": int(cycle.get("db_revision") or 0),
            "approved_stage": "test",
            "next_stage": "test",
            "surface_id": (str(assessment.get("id") or cycle_id) if repeat_test else f"test-evidence:{stage_attempt_id}"),
            "repeat_stage": True,
        }

    async def validate_evidence_retry(
        self,
        *,
        project_id: str,
        cycle_id: str,
        request: Mapping[str, Any],
    ) -> bool:
        """Revalidate a delayed chat answer against the current dossier."""
        cycle = await self._repo.get_cycle(cycle_id, project_id=project_id)
        if cycle is None:
            return False
        expected_revision = request.get("cycle_revision")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision != int(cycle.get("db_revision") or 0):
            return False
        stage = str(request.get("stage") or "").strip().lower()
        dossier_hash = str(request.get("dossier_hash") or "")
        attempt = next(
            (item for item in cycle.get("stages", []) if item.get("stage") == stage and item.get("status") in {"in_progress", "changes_requested", "awaiting_review"}),
            None,
        )
        if attempt is None:
            return False
        dossier = max(
            (item for item in cycle.get("artifacts", []) if item.get("stage_attempt_id") == attempt.get("id") and item.get("artifact_type") == "evidence_exception"),
            key=lambda item: int(item.get("revision") or 0),
            default=None,
        )
        return bool(dossier and len(dossier_hash) == 64 and dossier.get("content_hash") == dossier_hash)

    async def recover_evidence_retry(
        self,
        *,
        project_id: str,
        cycle_id: str,
        stage: str,
    ) -> dict[str, Any] | None:
        """Recreate a consumed guidance card from the current exception dossier."""
        normalized = stage.strip().lower()
        if normalized not in {"build", "test"}:
            return None
        cycle = await self._repo.get_cycle(cycle_id, project_id=project_id)
        if cycle is None:
            return None
        attempt = next(
            (item for item in cycle.get("stages", []) if item.get("stage") == normalized and item.get("status") in {"in_progress", "changes_requested", "awaiting_review"}),
            None,
        )
        if attempt is None:
            return None
        dossier = max(
            (item for item in cycle.get("artifacts", []) if item.get("stage_attempt_id") == attempt.get("id") and item.get("artifact_type") == "evidence_exception"),
            key=lambda item: int(item.get("revision") or 0),
            default=None,
        )
        if dossier is None or len(str(dossier.get("content_hash") or "")) != 64:
            return None
        surface = await self._repo.latest_stage_feedback_surface(
            project_id=project_id,
            cycle_id=cycle_id,
            stage=normalized,
            stage_attempt_id=str(attempt.get("id") or ""),
            mode="stage_review",
        )
        exception = dict(dict(dict((surface or {}).get("decision_request") or {}).get("transition_gate") or {}).get("evidence_exception") or {})
        if exception.get("content_hash") != dossier.get("content_hash"):
            return None
        return {
            "version": 1,
            "cycle_id": cycle_id,
            "cycle_revision": int(cycle.get("db_revision") or 0),
            "stage": normalized,
            "dossier_hash": str(dossier["content_hash"]),
            "reason_codes": list(exception.get("reason_codes") or []),
            "available_artifacts": list(exception.get("available_artifacts") or []),
        }

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

    async def conversation_cycle_status(self, *, project_id: str, thread_id: str) -> dict[str, Any] | None:
        """Latest cycle opened by this conversation, with bounded evidence refs."""
        if not project_id or not thread_id:
            return None
        cycles = await self._repo.list_cycles(project_id)
        owned = [cycle for cycle in cycles or [] if isinstance(cycle, dict) and str(cycle.get("originating_thread_id") or "") == thread_id]
        if not owned:
            return None
        newest = max(
            owned,
            key=lambda cycle: (
                str(cycle.get("created_at") or ""),
                str(cycle.get("id") or ""),
            ),
        )
        cycle_id = str(newest.get("id") or "")
        cycle = await self._repo.get_cycle(cycle_id, project_id=project_id)
        if not isinstance(cycle, dict):
            return None
        attempts = {str(item.get("id") or ""): str(item.get("stage") or "") for item in cycle.get("stages", []) if isinstance(item, dict)}
        latest_artifacts: dict[tuple[str, str], dict[str, Any]] = {}
        for item in cycle.get("artifacts", []):
            if not isinstance(item, dict):
                continue
            stage = attempts.get(str(item.get("stage_attempt_id") or ""), "")
            artifact_type = str(item.get("artifact_type") or "")
            uri = str(item.get("uri") or "")
            if not stage or not artifact_type or not uri:
                continue
            key = (stage, artifact_type)
            candidate = {
                "stage": stage,
                "artifact_type": artifact_type,
                "revision": int(item.get("revision") or 0),
                "uri": uri,
                "content_hash": str(item.get("content_hash") or ""),
            }
            current = latest_artifacts.get(key)
            if current is None or candidate["revision"] >= current["revision"]:
                latest_artifacts[key] = candidate
        artifacts = sorted(
            latest_artifacts.values(),
            key=lambda item: (item["stage"], item["artifact_type"]),
        )[:20]
        return {
            "cycle_id": cycle_id,
            "title": str(cycle.get("title") or ""),
            "state": str(cycle.get("state") or ""),
            "parked": bool(cycle.get("parked")),
            "stages": {str(item.get("stage") or ""): str(item.get("status") or "") for item in cycle.get("stages", []) if isinstance(item, dict)},
            "artifacts": artifacts,
        }

    async def recover_paused_build_control(
        self,
        *,
        project_id: str | None,
        cycle_id: str | None,
        requested_action: str,
        config: RunnableConfig,
    ) -> dict[str, Any] | None:
        """Raise a new governed choice after an earlier Build control disappeared.

        This is deliberately not a free-text command executor.  It turns an
        explicit Build command into a fresh server-owned card. This also
        recovers an open durable control that chat has already marked answered
        after a failed dispatch; the replacement still requires a second,
        bound human choice before anything runs.
        """
        if not project_id or not cycle_id:
            return None
        cycle = await self._repo.get_cycle(cycle_id, project_id=project_id)
        if cycle is None or _executable_stage(cycle) != "build":
            return None
        attempt = _stage_attempt(cycle, "build") or {}
        stage_attempt_id = str(attempt.get("id") or "")
        if not stage_attempt_id or str(attempt.get("status") or "") not in {
            StageStatus.IN_PROGRESS.value,
            StageStatus.CHANGES_REQUESTED.value,
        }:
            return None
        latest = await self._repo.latest_build_collaboration(
            project_id=project_id,
            stage_attempt_id=stage_attempt_id,
            lifecycle=None,
        )
        if not isinstance(latest, dict) or str(latest.get("lifecycle") or "") not in {
            "open",
            "held",
            "answered",
        }:
            return None
        runtime = self._runtime(config)
        gate = BuildControlGate(
            repo=self._repo,
            project_id=project_id,
            cycle_id=cycle_id,
            stage_attempt_id=stage_attempt_id,
            thread_id=str(runtime.get("thread_id") or ""),
            run_id=str(runtime.get("run_id") or ""),
            responder_user_id=str(runtime.get("user_id") or ""),
        )
        return await gate.raise_control(
            paused_build_recovery_request(
                previous=latest,
                cycle_revision=int(cycle.get("db_revision") or 0),
                requested_action=requested_action,
                changes_requested=(str(attempt.get("status") or "") == StageStatus.CHANGES_REQUESTED.value),
            )
        )

    def _build_execution_preflight_error(
        self,
        *,
        config: RunnableConfig,
        stage_workspace: str | None,
        sandbox_state: Any = None,
    ) -> str:
        """Return a refusal before planning when no executable shell exists."""
        if self._dispatcher is not None:
            # An injected dispatcher is itself the execution boundary used by
            # tests and alternate runtimes; its capabilities are not described
            # by the production tool registry.
            return ""
        from deerflow.tools import get_available_tools

        metadata = dict(config.get("metadata", {}) or {})
        try:
            tools = get_available_tools(
                model_name=str(metadata.get("model_name") or "") or None,
                groups=metadata.get("tool_groups"),
                subagent_enabled=False,
                include_upload_tool=False,
                app_config=self._app_config,
            )
            tools = _tools_for_virtual_workspace(tools, writable_workspace=stage_workspace)
        except Exception:  # noqa: BLE001 - preflight fails closed before spend
            logger.warning("Could not inspect the Build worker toolset.", exc_info=True)
            return "The Build worker toolset could not be inspected. No planner or Build worker ran."
        names = {str(getattr(tool, "name", "")).strip() for tool in tools}
        if "bash" not in names:
            return "The Build worker has no Bash execution tool. No planner or Build worker ran. Enable Bash for this sandbox, then retry the preflight."
        missing_packages = missing_scientific_packages() if _uses_local_sandbox(sandbox_state) else ()
        if missing_packages:
            return f"The DBTL scientific Python runtime is incomplete (missing: {', '.join(missing_packages)}). No planner or Build worker ran. Start DeerFlow through the standard launcher so the dbtl-build extra is installed, then retry."
        return ""

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
        """Configured model names the current principal may see.

        Filtered through the authorization provider so the participant card's
        pickers — and ``parse_participant_settings``' edit validation, which
        reads the same list — cannot offer a model the role is denied. The
        principal comes from the run's own context, the same merged view the
        worker dispatch path reads.
        """
        app_config = self._app_config
        models = getattr(app_config, "models", None) if app_config is not None else None
        if not models:
            return ()
        names = tuple(str(getattr(item, "name", "") or "") for item in models if getattr(item, "name", None))
        return filter_authorized_model_names(names, context=self._runtime({}), app_config=app_config)

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
        depth: CouncilDepth | None = None,
        proposal: CouncilProposal | None = None,
        participant_settings: Mapping[str, ParticipantSettings] | None = None,
    ) -> CouncilPlan | None:
        """The roster this request would convene, without convening it.

        Returns ``None`` whenever there is nothing to preview — no cycle, a
        cycle in this project the caller does not own, or a cycle sitting at a
        stage other than Design. The caller shows a card only when this returns
        a plan, so a preflight can never appear in front of work it does not
        describe.

        ``depth``, ``proposal``, and ``participant_settings`` open the card on a
        setup somebody already confirmed — a re-run of the same meeting. A
        supplied proposal is used as-is rather than written again: re-writing it
        is what silently changed the seats and the models between two meetings
        that were asked for in the same words.
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
            depth=depth,
        )
        if plan.human_authored or not plan.dispatchable:
            # Nothing to propose for: one is a deliberate choice to seat nobody,
            # the other cannot run at all. Asked before ``dispatchable`` because
            # they are false for opposite reasons.
            return plan
        if proposal is None:
            # The preview *is* the proposal. Building the card from capability
            # selection while dispatch used a proposed roster meant the card
            # described a council that never convened — and in a generalist-only
            # deployment (the common one) selection shows one undifferentiated
            # seat where four differentiated ones then ran.
            proposal = await self._propose_roster(
                request_text=request_text,
                stage_context=_preview_context(cycle),
                max_positions=depth_policy(plan.depth).max_positions,
                adjustment=adjustment,
            )
        if proposal is not None:
            plan = plan_from_proposal(plan, proposal)
        # Applied to the previewed plan, not only at dispatch: the card is drawn
        # from this plan, so a dial that lands later is one the person cannot see
        # and cannot tell was kept.
        return apply_participant_settings(plan, participant_settings)

    def _plan_council(
        self,
        spec: StageSpec,
        *,
        config: RunnableConfig,
        request_text: str,
        attempt_id: str,
        depth: CouncilDepth | None = None,
    ) -> CouncilPlan:
        """The roster this Design run will use.

        Depth comes from the human's confirmed choice when there is one, and
        otherwise from the request itself. An explicit *depth* is a choice
        recovered from an earlier meeting's card, and outranks both. An unrecognized value degrades to the
        recommendation rather than raising: a stale client losing a preference
        is a much smaller failure than a cycle that cannot be designed.

        The model a seat runs on defaults to ``dbtl.council_model_name`` rather
        than to the composer's. Inheriting the composer meant a meeting convened
        from an expensive chat quietly ran every unassigned seat on that model —
        the person had chosen a model to *talk* to, not a budget for four
        workers to argue on. A seat that names its own model still wins, and the
        setup card can still override any of them.
        """
        depth = depth or council_depth_from_config(config) or recommend_depth(request_text).depth
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
        cycle_id: str | None = None,
        stage: str = "design",
        meeting: bool = True,
        stage_workspace: str | None = None,
    ) -> AsyncWorkerDispatcher:
        runtime = self._runtime(config)
        metadata = dict(config.get("metadata", {}) or {})

        async def dispatch(
            units: Sequence[WorkUnit],
            *,
            budget: WorkerBudget,
        ) -> Sequence[DispatchOutcome]:
            # A dispatch round reports on the stage's own row rather than
            # opening one of its own. A Design meeting calls this closure
            # several times — positions, red team, chair — so a row per round
            # made the coordinator appear to finish and restart between waves,
            # and a shared deterministic id would have reopened a terminal row
            # outright. One row per ``execute`` describes the actor that is
            # genuinely present for the whole stage; the rounds are what it is
            # *doing*, which is a state change.
            handle = current_activity()
            if handle is not None:
                await handle.update(state=ActivityState.DISPATCHING, operation="stage.coordinate")
            try:
                return await self._dispatch_units(
                    units,
                    budget=budget,
                    config=config,
                    state=state,
                    runtime=runtime,
                    metadata=metadata,
                    project_id=project_id,
                    project_root=project_root,
                    cycle_id=cycle_id,
                    stage=stage,
                    meeting=meeting,
                    stage_workspace=stage_workspace,
                )
            finally:
                if handle is not None:
                    # Back to coordinating whether the round returned results or
                    # raised: leaving the row reading "dispatching" after the
                    # workers are gone is the stale state this projection exists
                    # to remove.
                    await handle.update(state=ActivityState.COORDINATING, operation="stage.coordinate")

        return dispatch

    @asynccontextmanager
    async def _stage_activity(
        self,
        *,
        config: RunnableConfig,
        stage: str,
        cycle_id: str,
    ) -> AsyncIterator[ActivityHandle | None]:
        """Open the one activity row that describes this stage's whole execution.

        Scoped to ``execute`` rather than to a dispatch round so that
        preparation, repository reads, roster planning, waiting on workers,
        evidence recording, and an early failure are all visible as one actor
        being present — and so a stage that fails before dispatching anything
        still closes a row rather than never opening one.
        """
        run_id = self._runtime(config).get("run_id")
        supervisor_id = supervisor_activity_id(run_id) if isinstance(run_id, str) and run_id else None
        async with optional_activity_span(
            run_id,
            actor_kind=ActorKind.STAGE_ADAPTER,
            actor_id=f"{stage}-stage",
            operation="stage.prepare",
            state=ActivityState.PREPARING,
            stage=stage,
            parent_activity_id=supervisor_id,
            dispatcher_activity_id=supervisor_id,
            scope=ActivityScope(cycle_id=cycle_id, stage=stage),
        ) as handle:
            yield handle

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
        cycle_id: str | None = None,
        stage: str,
        meeting: bool,
        stage_workspace: str | None,
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

        run_id = runtime.get("run_id")
        # The adapter's own row is open in this coroutine, and ``asyncio.gather``
        # copies the context into each worker task, so every worker names the
        # coordinator that dispatched it without being handed an id.
        adapter_activity_id = current_activity_id()

        async def run_one(unit: WorkUnit, index: int) -> DispatchOutcome:
            unit_workspace = workspace.unit_stage_workspace(stage_workspace, unit.unit_id) if stage_workspace else None
            if unit_workspace:
                resolved_workspace = _workspace_relative_path(unit_workspace, project_root=project_root)
                if resolved_workspace is None:
                    return DispatchOutcome(
                        unit_id=unit.unit_id,
                        text=None,
                        error="The stage adapter could not resolve this worker's isolated workspace.",
                    )
                await asyncio.to_thread(
                    _prepare_unit_workspace,
                    resolved_workspace[1],
                    stage=stage,
                )
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
            dispatch_budget = _effective_dispatch_budget(stage, budget)
            worker_config = _stage_worker_config(base_config, dispatch_budget)
            if stage == "build":
                # Build's versioned ceiling is intentionally roomier than the
                # general subagent default; otherwise the generic 150-step
                # clamp recreates the premature-finalization bug V7 replaced.
                worker_config = replace(
                    worker_config,
                    max_turns=dispatch_budget.max_turns,
                )
                if unit.role == "phase":
                    # Phase declarations are the complete skill allowlist.
                    # Empty means no skill discovery or activation for this
                    # worker; no configured/global skill may widen it.
                    worker_config = replace(worker_config, skills=list(unit.skills or ()))
                elif unit.role == SUMMARIZER_ROLE:
                    worker_config = replace(worker_config, skills=list(unit.skills or ()))
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
            # ``model:use`` is enforced here, immediately before dispatch,
            # because the picker filter above is only a visibility rule: a
            # cached card reply or a proposal-written seat can still name a
            # model the principal's role is denied. Deny falls back to the
            # first authorized model; fail-closed with nothing allowed refuses
            # the worker instead of running it on a restricted model.
            if effective_model:
                try:
                    effective_model = authorize_model_use(effective_model, context=runtime, app_config=self._app_config)
                except ValueError as exc:
                    return DispatchOutcome(
                        unit_id=unit.unit_id,
                        text=None,
                        error=f"Model {effective_model!r} is not authorized for this role: {exc}",
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
            if unit.role == "rerun":
                if not unit_workspace:
                    return DispatchOutcome(
                        unit_id=unit.unit_id,
                        text=None,
                        error="The Test rerun has no isolated writable workspace.",
                    )
                tools = [build_test_rerun_tool(unit, unit_workspace=unit_workspace)]
            else:
                try:
                    tools = _with_craft_memory_tools(
                        tools,
                        worker_config,
                        app_config=self._app_config,
                    )
                except ValueError as exc:
                    return DispatchOutcome(
                        unit_id=unit.unit_id,
                        text=None,
                        error=str(exc),
                    )
                tools = _tools_for_unit(_tools_for_stage_budget(tools, dispatch_budget), unit)
            tools = _tools_for_virtual_workspace(tools, writable_workspace=unit_workspace)
            trace_id = str(metadata.get("trace_id") or "") or None
            # A stage worker is graded on its final message, but the turn budget
            # is enforced by ``recursion_limit``, which aborts from inside a tool
            # loop — so a worker that spends its budget could never land the JSON
            # its result is parsed from. The deadline reserves the last few model
            # calls for writing that answer.
            # Reserve the final model calls for the structured result. V7's
            # roomy ceiling avoids V5's premature six-call finalization while
            # still preventing a tool loop from consuming an unbounded run.
            deadline = FinalizationDeadlineMiddleware(
                # Config may impose a lower per-agent turn limit than the
                # versioned stage budget. Derive the deadline from the limit
                # the executor will actually enforce. Build's correction uses
                # one-time entry/exit nodes, reserved as extra headroom below.
                max_model_calls=_model_call_budget(
                    worker_config.max_turns,
                    extra_headroom_steps=(BUILD_PHASE_CORRECTION_HEADROOM_STEPS if stage == "build" and unit.role == "phase" else 0),
                ),
                # The turn axis is not the binding one for Build: 450 turns
                # against 120K tokens means the tokens run out first, the turn
                # deadline never fires, and the worker dies mid-investigation
                # with prose. The same ceiling the executor enforces is what the
                # deadline warns ahead of; ``None`` (metered-only execution)
                # leaves the token axis inert.
                max_tokens=_token_limit_for_worker(unit, dispatch_budget),
            )
            correction = (
                BuildPhaseCorrectionMiddleware(
                    capability=unit.capability,
                    agent_name=unit.agent_name,
                    retry_blocked=deadline.forced_finalization,
                )
                if stage == "build" and unit.role == "phase" and not unit.tool_contract.get("fresh_correction")
                else None
            )
            extra_middlewares = [deadline]
            if correction is not None:
                # Correction runs at the agent-exit boundary, after the native
                # loop/token/safety hooks and the deadline have had authority
                # over the run. It therefore cannot jump around a guardrail.
                extra_middlewares.append(correction)
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
                token_budget_max_tokens=_token_limit_for_worker(unit, dispatch_budget),
                token_budget_enabled=None,
                loop_detection_enabled=None,
                dbtl_writable_paths=(() if unit.role in _READ_ONLY_ROLES else ((unit_workspace,) if unit_workspace else ())),
                thinking_enabled=unit.reasoning == REASONING_EXTENDED,
                extra_middlewares=extra_middlewares,
                execution_env=(
                    _dbtl_worker_execution_env(
                        sandbox_state=state.get("sandbox"),
                        workspace=unit_workspace,
                        project_root=WORKSPACE_VIRTUAL_ROOT,
                        declared_inputs=tuple(unit.tool_contract.get("granted_inputs") or ()),
                    )
                    if (unit_workspace and ((stage == "build" and unit.role == "phase") or (stage == "test" and unit.role == "rerun")))
                    else None
                ),
            )
            # One activity row per real work unit, opened after the guards above
            # so a worker that never ran does not appear to have started. A
            # meeting seat keeps its validated role label; ordinary stage work
            # is numbered, because "Build worker 2" is what a reader can match
            # against the two rows running beside each other.
            worker_activity = (
                make_activity_handle(
                    run_id=str(run_id),
                    actor_kind=ActorKind.STAGE_WORKER,
                    actor_id=unit.unit_id,
                    operation="meeting.participate" if meeting else "worker.run",
                    state=ActivityState.COMPUTING,
                    stage=stage,
                    index=index,
                    display_name=(_SEAT_ROLE_LABELS.get(unit.role) if meeting else None),
                    parent_activity_id=adapter_activity_id,
                    scope=ActivityScope(cycle_id=cycle_id, stage=stage, task_id=unit.unit_id),
                    writer=writer,
                )
                if isinstance(run_id, str) and run_id
                else None
            )
            worker_lineage = worker_activity.lineage_fields() if worker_activity is not None else {}
            if worker_activity is not None:
                await worker_activity.open()

            await emit(
                {
                    "type": "task_started",
                    "task_id": unit.unit_id,
                    "description": (_seat_description(unit) if meeting else f"{stage.title()} work: {unit.capability.replace('_', ' ')}"),
                    "model_name": effective_model,
                    "dbtl_stage": stage,
                    **({"council_seat": _seat_identity(unit, model=effective_model, stage=stage)} if meeting else {}),
                    **worker_lineage,
                }
            )
            holder = SubagentResult(
                task_id=unit.unit_id,
                trace_id=executor.trace_id,
                status=SubagentStatus.PENDING,
            )
            # The holder the executor writes into is the same list the streamer
            # reads, so a stage worker's reads, tools, and Bash output appear
            # while it works instead of arriving all at once at the end — or,
            # when its structured result is rejected, never appearing at all.
            # `council_seat` deliberately stays off these events: the seat was
            # asserted once at `task_started` and repeating it per step would
            # both bloat the stream and let ordinary Build work be mistaken for
            # a meeting.
            step_stream = SubagentStepStreamer(
                task_id=unit.unit_id,
                emit=emit,
                base_event={"model_name": effective_model, "dbtl_stage": stage, **worker_lineage},
            )
            try:
                result = await run_with_step_stream(
                    functools.partial(executor.execute, unit.prompt, holder),
                    result=holder,
                    streamer=step_stream,
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
                        **worker_lineage,
                    }
                )
                if worker_activity is not None:
                    await worker_activity.settle(ActivityState.CANCELLED)
                raise

            tokens._report_subagent_token_usage(config, result)
            token_usage = tokens._summarize_token_usage(result.token_usage_records)
            if result.status is SubagentStatus.COMPLETED:
                dispatch_outcome = DispatchOutcome(
                    unit_id=unit.unit_id,
                    text=result.result,
                    stop_reason=result.stop_reason,
                    forced_finalization=deadline.forced_any() if deadline is not None else False,
                    token_usage=token_usage,
                )
                terminal_event = _terminal_seat_event(
                    unit,
                    dispatch_outcome,
                    model=effective_model,
                    meeting_stage=stage if meeting else None,
                    stage=stage,
                    lineage=worker_lineage,
                )
                await emit(terminal_event)
                if worker_activity is not None:
                    # The row's outcome follows the *contract*, not the child
                    # graph: a worker that stopped cleanly but returned prose is
                    # a failed piece of evidence, and reporting it as completed
                    # would disagree with its own terminal event.
                    await worker_activity.settle(ActivityState.FAILED if terminal_event.get("type") == "task_failed" else ActivityState.COMPLETED)
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
                    stage=stage,
                    lineage=worker_lineage,
                )
            )
            if worker_activity is not None:
                await worker_activity.settle(ActivityState.FAILED)
            return dispatch_outcome

        return await asyncio.gather(*(run_one(unit, index) for index, unit in enumerate(units, start=1)))

    async def _plan_build(
        self,
        *,
        dispatcher: Callable[..., Any],
        budget: WorkerBudget,
        attempt_id: str,
        inputs: BuildInputBundle,
        cycle: Mapping[str, Any],
        candidates: Sequence[AgentCandidate],
        adjustment: str = "",
        answer: str = "",
    ) -> tuple[BuildPhasePlan, tuple[str, ...]]:
        """Ask for a decomposition; accept a single phase; never fail here.

        Every failure degrades to a one-phase plan with a recorded note, because
        losing the decomposition costs structure while failing here costs the
        whole Build. The note is what keeps that honest: a reviewer reading a
        one-phase Build can tell "it did not decompose" from "we could not read
        the planner".
        """
        objective = str(cycle.get("objective") or cycle.get("research_question") or "")
        agent = next((item.name for item in candidates if item.name == GENERALIST), None) or (candidates[0].name if candidates else GENERALIST)
        context = json.dumps(
            {
                "cycle": {key: cycle.get(key) for key in ("id", "title", "research_question", "objective", "success_criteria")},
                "build_input_bundle": inputs.as_dict(),
                # The owner's words, verbatim. Paraphrasing them into a planner-owned
                # instruction is the failure this second exchange exists to avoid:
                # the point of asking was to hear what *they* wanted changed.
                **({"owner_requested_changes": adjustment} if adjustment else {}),
                # The exchange that followed the question this planner asked
                # last time — its own sentence and the owner's, each labelled.
                # Without it the planner re-runs on byte-identical inputs and
                # asks the same question again, forever.
                **({"previous_exchange_with_the_owner": answer} if answer else {}),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        unit = planner_unit(attempt_id=attempt_id, agent_name=agent, context=context)
        try:
            dispatched = await dispatcher((unit,), budget=budget)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.warning("The Build planner could not be dispatched.", exc_info=True)
            return single_phase_plan(objective=objective, note="The build planner could not be run, so the build runs as one piece."), ("planner_unavailable",)
        text = str(getattr(dispatched[0], "text", "") or "") if dispatched else ""
        parsed = parse_build_plan(text, objective=objective)
        if parsed.degraded:
            logger.info("The Build plan degraded to a single phase: %s", "; ".join(parsed.reasons))
        return parsed.plan, parsed.reasons

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
        """Run the plan's phases in order, each as its own attempt.

        Sequential by design: one sandbox writer at a time is what makes the
        workspace grant, the input snapshot, and the mutation checks tractable.
        A phase that fails stops the run — later phases depend on outputs that
        do not exist, and dispatching them anyway would spend budget producing
        evidence nobody planned.

        **A phase succeeds only once its outputs are in the governed tree.**
        Publication used to happen once, after every phase had already been
        recorded as succeeded from the worker's own JSON, so a phase naming a
        file that was missing, escaped its workspace, or changed underneath it
        left a *reusable success* in the chain — and a later resume would replay
        it as work that had produced evidence. Validating and copying each
        phase's bytes before settling its row makes the record say what actually
        happened.

        **Completion is reported, not inferred.** A failed phase, an uncovered
        capability, and a `pause_after` boundary all stop the loop with earlier
        phases legitimately committed, and the caller has to be able to tell
        "the plan finished" from "some of it did".

        **A boundary somebody already crossed is not a boundary.**
        `boundaries_released` is durable, not request-scoped: it used to mean
        only "this exact request carries a Continue", so a plan continued in one
        turn and stopped by a later failure paused at the same finished phase on
        every retry afterwards — the question re-asked forever and the cheap
        summary/deck retry unreachable. A freshly run phase still stops at its
        own boundary regardless, because that one has never been shown.
        """
        assignments = [assign_phase(phase, candidates) for phase in plan.phases]
        selection = SelectionResult(
            assignments=tuple(Assignment(capability=item.phase.capability, agent_name=item.agent_name, via_generalist=item.via_generalist or not item.covered) for item in assignments),
            notes=plan_notes(plan, assignments),
        )
        units: list[WorkUnit] = []
        results: list[StageWorkerResult] = []
        rejected: list[str] = []
        completed: list[Mapping[str, Any]] = []
        published: list[dict[str, Any]] = []
        # Each phase's exact bindings, in the order they were established. Build
        # lineage is assembled from these rather than recomputed at the end,
        # because only the phase loop knows which outputs already existed when a
        # given phase ran -- the aggregate view cannot tell a phase's own output
        # from one it legitimately read.
        input_artifacts: list[str] = []
        stopped = ""
        paused = False
        paused_title = ""
        failure_code: BuildErrorCode | None = None
        control_request: dict[str, Any] | None = None

        declared_skill_names = tuple(name for assignment in assignments for name in assignment.phase.skills)
        try:
            skill_catalog = await _declared_skill_bindings(
                declared_skill_names,
                user_id=user_id,
                app_config=self._app_config,
            )
        except Exception as exc:  # noqa: BLE001 - registry failure is a bounded phase refusal
            logger.warning("Build could not bind its declared skill catalog.", exc_info=True)
            return _PhaseRun(
                outcome=StageExecutionOutcome(
                    plan=StageExecutionPlan(spec=spec, selection=selection),
                    rejected=(str(exc),),
                ),
                stopped=str(exc),
                failure_code=BuildErrorCode.EXECUTION_CONTRACT_REJECTED,
            )

        for index, assignment in enumerate(assignments, start=1):
            if not assignment.covered:
                # Nothing registered can do this work. Refusing names the
                # capability; the alternative — handing it to whichever agent
                # sorted first — is the silent swap capability selection exists
                # to prevent.
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

            restored = (
                await asyncio.to_thread(
                    _restore_phase,
                    recorder.replay(handle),
                    assignment=assignment,
                    index=index,
                    spec=spec,
                    expected_digest=str(handle.output_digest or ""),
                    project_root=project_root,
                )
                if handle.replayed
                else None
            )
            if restored is None and handle.replayed:
                # A committed success whose output cannot be produced. The work
                # has to happen again, and it has to be *recorded* as happening
                # again: settling nothing would leave the chain descending from
                # a digest that no longer describes anything on disk.
                handle = await recorder.reopen(handle)
            if restored is not None:
                unit, result, phase_published, phase_inputs = restored
                units.append(unit)
                results.append(result)
                published.extend(phase_published)
                _extend_unique(input_artifacts, phase_inputs)
                completed.append(_phase_note(assignment, result))
                # A replayed phase's boundary was already shown and answered —
                # that is what "Continue" meant. Stopping at it again would ask
                # the same question forever and make the plan unfinishable.
                if _stops_at_boundary(assignment, index=index, total=len(assignments)) and not boundaries_released:
                    stopped = _pause_note(assignment)
                    rejected.append(stopped)
                    paused, paused_title = True, assignment.phase.title
                    break
                continue

            # The unit id carries the step attempt, and the isolated workspace is
            # derived from the unit id — so a retry gets a clean directory rather
            # than the failed attempt's half-written files.
            phase_context = context
            if answer:
                phase_context = "\n\n".join(
                    (
                        context,
                        "The previous attempt at this phase asked for human input. Carry this exchange verbatim into the retry:\n" + answer,
                    )
                )
            granted_inputs = _phase_granted_inputs(
                datasets=datasets,
                prior_published=published,
                pre_run_files=pre_run_files,
                project_root=project_root,
            )
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
            except Exception as exc:  # noqa: BLE001 - a crashed phase is a recorded phase
                logger.warning("Build phase %s could not be dispatched.", assignment.phase.phase_key, exc_info=True)
                dispatched = []
                rejected.append(f"{unit.unit_id}: {exc}")
            phase_outcome = collect_results(phase_plan, dispatched)
            result = phase_outcome.results[0] if phase_outcome.results else None
            rejected.extend(phase_outcome.rejected)

            # The registry is live and user-editable. Re-hash after the worker
            # returns so work cannot commit against one skill revision after
            # executing another. A later retry resolves the new winner and
            # therefore opens a new phase input digest.
            try:
                current_skill_catalog = await _declared_skill_bindings(
                    assignment.phase.skills,
                    user_id=user_id,
                    app_config=self._app_config,
                )
                current_skill_bindings = tuple(current_skill_catalog[name] for name in assignment.phase.skills)
                if current_skill_bindings != phase_skill_bindings:
                    raise ValueError("A declared Build skill changed while this phase was running; retry the phase against the new skill revision.")
            except Exception as exc:  # noqa: BLE001 - registry/read drift is a phase failure
                stopped = str(exc)
                results.append(
                    failed_result(
                        capability=assignment.phase.capability.value,
                        agent_name=assignment.agent_name,
                        reason=stopped,
                    )
                )
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
                await recorder.settle(
                    handle,
                    state=StepState.NEEDS_INPUT,
                    summary=question,
                    human_input_request_id=request_id,
                    execution={"phase_key": assignment.phase.phase_key},
                )
                control_request = await control_gate.raise_control(request)
                results.append(result)
                stopped = question
                paused, paused_title = True, assignment.phase.title
                break

            salvaged_cap = ""

            async def dispatch_fresh_correction(
                failure: str,
                *,
                correct_live_terminal: bool = False,
            ) -> bool:
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
                # A phase has one accepted result. The rejected first worker
                # remains visible in its task timeline and is named by
                # `correction_of`, but keeping both units beside one corrected
                # result violates StageExecutionOutcome's one-unit/one-result
                # invariant and crashes the downstream strict zip.
                units[-1] = correction
                correction_plan = StageExecutionPlan(spec=spec, selection=selection, units=(correction,))
                try:
                    correction_dispatched = await dispatcher((correction,), budget=spec.budget)
                except Exception:  # noqa: BLE001 - the normal rejection path records it
                    logger.warning("Build phase correction could not be dispatched.", exc_info=True)
                    correction_dispatched = []
                correction_outcome = collect_results(correction_plan, correction_dispatched)
                rejected.extend(entry for entry in correction_outcome.rejected if entry not in rejected)
                corrected = correction_outcome.results[0] if correction_outcome.results else None
                if corrected is not None and corrected.is_trustworthy:
                    corrected = replace(
                        corrected,
                        token_usage=tokens._merge_token_usage(first_result.token_usage, corrected.token_usage),
                        provenance={
                            **corrected.provenance,
                            "correction_of": first_unit.unit_id,
                        },
                    )
                    correction_outcome = replace(correction_outcome, results=(corrected,))
                unit = correction
                result = corrected
                phase_outcome = correction_outcome
                return True

            # A token-capped phase is admitted only where the server itself runs the
            # entry point; the hard gates below then still decide. Without that
            # conjunct nothing verifies the real-execution family and a cap would be
            # excused on the worker's own word.
            if result is not None and "server_executed_entry_point" in spec.validity_gates and enforce_server_execution and is_capped_phase_salvageable(result, required_version=required_phase_manifest_version(spec)):
                result, salvaged_cap = admit_capped_phase(result)
                phase_outcome = replace(phase_outcome, results=(result,))

            completion_error = phase_completion_error(result, unit.completion_check) if result is not None else ""
            correction_eligible = bool(result is not None and completion_error and spec.version >= 12 and not unit.tool_contract.get("correction_attempt") and not result.was_capped and not salvaged_cap)
            if result is None or (not result.is_trustworthy and not correction_eligible):
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
                results.append(
                    replace(
                        failed_result(
                            capability=result.capability,
                            agent_name=result.agent_name,
                            reason=completion_error,
                        ),
                        token_usage=result.token_usage,
                    )
                )
                rejected.append(completion_error)
                stopped = completion_error
                failure_code = BuildErrorCode.EXECUTION_CONTRACT_REJECTED
                await recorder.fail(handle, failure_code, stopped)
                break

            server_verification: BuildPhaseVerification | None = None
            if "server_executed_entry_point" in spec.validity_gates and enforce_server_execution:

                async def verify_server_result(
                    current_result: StageWorkerResult,
                    current_unit: WorkUnit,
                ) -> tuple[StageWorkerResult, BuildPhaseVerification]:
                    raw_manifest, raw_manifest_error = verify_unpublished_phase_manifest(
                        current_result,
                        completion_condition=assignment.phase.done_condition,
                        required_version=required_phase_manifest_version(spec),
                    )
                    unit_workspace = workspace.unit_stage_workspace(stage_workspace, current_unit.unit_id)
                    if raw_manifest is None:
                        return current_result, BuildPhaseVerification(False, raw_manifest_error, "")
                    raw_manifest = resolve_issued_input_tokens(raw_manifest, issued_inputs=granted_inputs)
                    current_result = replace(
                        current_result,
                        provenance={
                            **current_result.provenance,
                            "phase_manifest": raw_manifest.as_dict(),
                        },
                    )
                    phase_rerun = parse_rerun_spec((current_result.provenance or {}).get("rerun_spec"))
                    if phase_rerun is not None:
                        rerun_inputs = resolve_issued_input_tokens(
                            replace(
                                raw_manifest,
                                declared_inputs=tuple(phase_rerun.inputs),
                                execution_inputs=tuple(phase_rerun.inputs),
                            ),
                            issued_inputs=granted_inputs,
                        ).execution_inputs
                        if rerun_inputs != raw_manifest.execution_inputs:
                            return current_result, BuildPhaseVerification(
                                False,
                                "The Build phase rerun_spec.inputs must exactly match phase_manifest.execution_inputs in runtime order; Build and Test cannot verify different input environments.",
                                "",
                            )
                    grant_error = verify_granted_paths(
                        raw_manifest,
                        read_source=functools.partial(_published_source_text, project_root=project_root),
                        allowed_roots=(),
                    )
                    if grant_error:
                        return current_result, BuildPhaseVerification(False, grant_error, "")
                    verification = await asyncio.to_thread(
                        execute_and_verify_phase,
                        raw_manifest,
                        project_root=project_root,
                        unit_workspace=unit_workspace,
                        execute=functools.partial(
                            _execute_server_build_command,
                            sandbox_state=sandbox_state,
                            writable_workspace=unit_workspace,
                            thread_id=thread_id,
                            user_id=user_id,
                            project_id=str(cycle.get("project_id") or ""),
                            project_root=project_root,
                        ),
                        timeout_seconds=min(float(spec.budget.timeout_seconds), 300.0),
                        issued_inputs=granted_inputs,
                    )
                    return current_result, verification

                result, server_verification = await verify_server_result(result, unit)
                phase_outcome = replace(phase_outcome, results=(result,))
                if not server_verification.passed and await dispatch_fresh_correction(
                    server_verification.reason,
                    correct_live_terminal=True,
                ):
                    if result is None or not result.is_trustworthy:
                        results.extend(phase_outcome.results)
                        stopped = "; ".join(phase_outcome.rejected) or (str(result.summary).strip() if result is not None else "") or f"The fresh correction for phase {assignment.phase.title!r} returned no usable result."
                        failure_code = BuildErrorCode.EXECUTION_CONTRACT_REJECTED
                        await recorder.fail(handle, failure_code, stopped)
                        break
                    completion_error = phase_completion_error(result, unit.completion_check)
                    if completion_error:
                        results.append(
                            replace(
                                failed_result(
                                    capability=result.capability,
                                    agent_name=result.agent_name,
                                    reason=completion_error,
                                ),
                                token_usage=result.token_usage,
                            )
                        )
                        rejected.append(completion_error)
                        stopped = completion_error
                        failure_code = BuildErrorCode.EXECUTION_CONTRACT_REJECTED
                        await recorder.fail(handle, failure_code, stopped)
                        break
                    result, server_verification = await verify_server_result(result, unit)
                    phase_outcome = replace(phase_outcome, results=(result,))
                if not server_verification.passed:
                    stopped = server_verification.reason
                    rejected.append(stopped)
                    results.append(
                        replace(
                            failed_result(
                                capability=result.capability,
                                agent_name=result.agent_name,
                                reason=stopped,
                            ),
                            token_usage=result.token_usage,
                        )
                    )
                    failure_code = BuildErrorCode.EXECUTION_CONTRACT_REJECTED
                    await _emit_build_verification_failure(unit, stopped)
                    await recorder.fail(
                        handle,
                        failure_code,
                        stopped,
                        execution={"server_phase_verification": server_verification.as_dict()},
                    )
                    break
                result = replace(
                    result,
                    provenance={
                        **result.provenance,
                        "server_phase_verification": server_verification.as_dict(),
                    },
                )
                phase_outcome = replace(phase_outcome, results=(result,))

            # Publish *this* phase before its row is settled, so a success in the
            # chain always means "the bytes are in the governed tree and hashed".
            phase_outcome, phase_published = await asyncio.to_thread(
                _publish_build_worker_artifacts,
                project_root=project_root,
                cycle=cycle,
                outcome=phase_outcome,
                stage_workspace=stage_workspace,
                attempt_id=attempt_id,
            )
            result = phase_outcome.results[0] if phase_outcome.results else None
            rejected.extend(entry for entry in phase_outcome.rejected if entry not in rejected)
            if result is None or not result.is_trustworthy or not phase_published:
                results.extend(phase_outcome.results)
                stopped = "; ".join(phase_outcome.rejected) or f"Phase {assignment.phase.title!r} produced no output the server could verify."
                failure_code = BuildErrorCode.EXECUTION_OUTPUT_MISSING
                # The worker's structured JSON passed before the server
                # inspected its artifact references, so the dispatcher already
                # emitted task_completed. Verification is authoritative; emit
                # the correcting terminal event under the same task id so the
                # UI cannot keep claiming a missing artifact completed.
                await _emit_build_verification_failure(unit, stopped)
                await recorder.fail(handle, failure_code, stopped)
                break

            phase_manifest: BuildPhaseManifest | None = None
            if "server_verified_phase_manifest" in spec.validity_gates:
                phase_manifest, manifest_error = verify_phase_manifest(
                    result,
                    published=phase_published,
                    completion_condition=assignment.phase.done_condition,
                    required_version=required_phase_manifest_version(spec),
                )
                if manifest_error:
                    # The entry point already ran clean and its bytes are
                    # published by this point, so a manifest that does not name
                    # exactly those bytes is a bookkeeping desync, not a failed
                    # build. Bind the manifest to what was actually published so
                    # the security scan below still targets a known entry point,
                    # record the discrepancy as an observation for Test and the
                    # human to weigh, and keep going. Only a manifest that cannot
                    # be bound at all (unparseable, or an entry point missing from
                    # the published set) stays hard.
                    reconciled = reconcile_published_manifest(
                        result,
                        published=phase_published,
                        completion_condition=assignment.phase.done_condition,
                        required_version=required_phase_manifest_version(spec),
                    )
                    if reconciled is None:
                        stopped = manifest_error
                        rejected.append(manifest_error)
                        results.append(
                            replace(
                                failed_result(
                                    capability=result.capability,
                                    agent_name=result.agent_name,
                                    reason=manifest_error,
                                ),
                                token_usage=result.token_usage,
                            )
                        )
                        failure_code = BuildErrorCode.EXECUTION_CONTRACT_REJECTED
                        await _emit_build_verification_failure(unit, stopped)
                        await recorder.fail(handle, failure_code, stopped)
                        break
                    phase_manifest = reconciled
                    result = record_build_observation(result, manifest_error)
                    phase_outcome = replace(phase_outcome, results=(result,))

            if phase_manifest is not None and "granted_paths_only" in spec.validity_gates:
                grant_error = await asyncio.to_thread(
                    verify_granted_paths,
                    phase_manifest,
                    read_source=functools.partial(_published_source_text, project_root=project_root),
                    allowed_roots=(),
                )
                if grant_error:
                    stopped = grant_error
                    rejected.append(grant_error)
                    results.append(
                        replace(
                            failed_result(
                                capability=result.capability,
                                agent_name=result.agent_name,
                                reason=grant_error,
                            ),
                            token_usage=result.token_usage,
                        )
                    )
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
                    # Only the phases *before* this one: `published` is extended
                    # with this phase's own outputs below, and a phase must not
                    # be able to bind what it just wrote as something it read.
                    run_published=_published_input_index(published, project_root=project_root),
                    implementation_inputs=(phase_manifest.declared_inputs if phase_manifest is not None and "narrow_implementation_inputs" in spec.validity_gates else None),
                )
            except ValueError as exc:
                stopped = str(exc)
                results.append(
                    replace(
                        failed_result(
                            capability=result.capability,
                            agent_name=result.agent_name,
                            reason=stopped,
                        ),
                        token_usage=result.token_usage,
                    )
                )
                failure_code = BuildErrorCode.INPUT_CHANGED_DURING_EXECUTION
                await _emit_build_verification_failure(unit, stopped)
                await recorder.fail(handle, failure_code, stopped)
                break

            results.append(result)
            published.extend(phase_published)
            _extend_unique(input_artifacts, phase_input_artifacts)
            if salvaged_cap:
                # The cap already raised `task_failed`. Correct it only here, so a
                # phase whose entry point then failed to verify keeps that failure.
                await _emit_build_cap_salvage_admitted(unit, result)
            await recorder.succeed(
                handle,
                # The same function the restorer recomputes with, so a replay
                # cannot be refused — or accepted — on a difference in how the
                # two sides happened to serialize the same result.
                phase_output_digest(
                    result=result.as_dict(),
                    published=phase_published,
                    input_artifacts=phase_input_artifacts,
                ),
                execution={
                    "phase_key": assignment.phase.phase_key,
                    "outputs": len(phase_published),
                    "skill_binding_count": len(phase_skill_bindings),
                    **(
                        {
                            "entry_point": phase_manifest.entry_point,
                            "completion_condition_hash": hashlib.sha256(phase_manifest.completion_condition.encode("utf-8")).hexdigest(),
                        }
                        if phase_manifest is not None
                        else {}
                    ),
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
                # A phase boundary is a committed, resumable state with no worker
                # lease held, so honouring the plan's own request to stop here
                # costs nothing and is the cheapest possible pause.
                stopped = _pause_note(assignment)
                rejected.append(stopped)
                paused, paused_title = True, assignment.phase.title
                break

        return _PhaseRun(
            outcome=StageExecutionOutcome(
                plan=StageExecutionPlan(spec=spec, selection=selection, units=tuple(units)),
                results=tuple(results),
                rejected=tuple(rejected),
            ),
            published=published,
            input_artifacts=input_artifacts,
            complete=len(completed) == len(plan.phases) and not stopped,
            stopped_because=stopped,
            paused=paused,
            paused_phase_title=paused_title,
            completed_count=len(completed),
            failure_code=failure_code,
            control_request=control_request,
        )

    async def _run_build_work_meeting(
        self,
        *,
        dispatcher: Callable[..., Any],
        budget: WorkerBudget,
        attempt_id: str,
        context: MeetingContext,
        candidates: Sequence[AgentCandidate],
    ) -> tuple[str, dict[str, Any]]:
        """Convene the meeting and return ``(briefing, record)``.

        Never raises and never fails the Build. The meeting is advisory, so an
        outage costs the advice — the person still has the question in front of
        them and can answer it directly, which is the cheaper interaction this
        meeting was an escalation from.
        """
        agent = next((item.name for item in candidates if item.name == GENERALIST), None) or (candidates[0].name if candidates else GENERALIST)
        units = meeting_units(
            attempt_id=attempt_id,
            agent_name=agent,
            model=self._council_model(),
            via_generalist=agent == GENERALIST,
            context=context,
        )
        try:
            dispatched = await dispatcher(units, budget=budget)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.warning("The Build work meeting could not be dispatched.", exc_info=True)
            return "", {"contract": BUILD_WORK_MEETING_CONTRACT, "refusal": "The meeting could not be run."}
        by_id = {str(getattr(item, "unit_id", "")): str(getattr(item, "text", "") or "") for item in dispatched}
        chair = next((unit for unit in units if unit.role == "chair"), None)
        recommendation = parse_recommendation(by_id.get(chair.unit_id, "") if chair is not None else "")
        record = {
            **recommendation.as_dict(),
            "question": context.question,
            "step_key": context.step_key,
            "seats": [{"unit_id": unit.unit_id, "role": unit.role, "reported": bool(by_id.get(unit.unit_id))} for unit in units],
        }
        return (recommendation.as_briefing() if recommendation.usable else ""), record

    async def _build_pause_control(
        self,
        phase_run: _PhaseRun | None,
        *,
        gate: BuildControlGate,
        cycle: Mapping[str, Any],
        plan: BuildPhasePlan | None,
        attempt: Mapping[str, Any] | None,
        workflow_spec_key: str,
        summary_refusal: str,
    ) -> dict[str, Any] | None:
        """The control this Build's stopping point calls for, if any.

        Three states, three different questions. A plan that stopped at its own
        boundary asks whether to carry on. A plan that stopped on a failure asks
        what to do about it. And a write-up that could not be produced asks the
        same thing about a much cheaper step — which matters, because a person
        told only "the summary failed" has no way to know their sandbox work is
        still pinned and reusable.

        Returns ``None`` when the Build finished, when it was never running this
        workflow, or when the plan is still whole. Raising a control for a
        successful Build would put a question in front of somebody who has an
        answer already.
        """
        common = {
            "cycle_id": str(cycle.get("id") or ""),
            "stage_attempt_id": str((attempt or {}).get("id") or ""),
            "workflow_spec_key": workflow_spec_key,
            "cycle_revision": int(cycle.get("db_revision") or 0),
        }
        if phase_run is not None and not phase_run.complete:
            if phase_run.control_request is not None:
                return phase_run.control_request
            if phase_run.paused and plan is not None:
                return await gate.raise_control(
                    phase_pause_request(
                        plan=plan,
                        completed_phases=phase_run.completed_count,
                        paused_phase_title=phase_run.paused_phase_title,
                        **common,
                    )
                )
            return await gate.raise_control(
                step_failure_request(
                    step_key=BuildStepKey.EXECUTE_PHASES.value,
                    step_label="Run the build",
                    error_code=(phase_run.failure_code.value if phase_run.failure_code else BuildErrorCode.INTERNAL_ERROR.value),
                    error_summary=phase_run.stopped_because,
                    completed_phases=phase_run.completed_count,
                    plan_digest=str(getattr(plan, "digest", "") or ""),
                    plan=plan,
                    **common,
                )
            )
        if summary_refusal:
            # Presentational, and the card says so through its options: retrying
            # the write-up reuses the execution rather than re-running it.
            return await gate.raise_control(
                step_failure_request(
                    step_key=BuildStepKey.SUMMARIZE_RESULTS.value,
                    step_label="Summarize results",
                    error_code=BuildErrorCode.SUMMARY_CONTRACT_REJECTED.value,
                    error_summary=summary_refusal,
                    completed_phases=(phase_run.completed_count if phase_run is not None else 0),
                    plan_digest=str(getattr(plan, "digest", "") or ""),
                    plan=plan,
                    **common,
                )
            )
        return None

    async def _summarize_build(
        self,
        *,
        dispatcher: Callable[..., Any],
        budget: WorkerBudget,
        attempt_id: str,
        outcome: StageExecutionOutcome,
        inputs: BuildInputBundle | None,
        cycle: Mapping[str, Any],
        bundle: BuildExecutionBundle,
        answer: str = "",
    ) -> _BuildSummary:
        """Run the read-only summarizer over the verified execution bundle.

        Never raises: the execution behind this is already committed and
        hash-bound, so a provider outage or an unparseable answer must cost the
        write-up rather than the Build.

        The worker's raw answer travels back with the parsed package because it
        is what a replay is rebuilt from — a later run re-parses it against the
        same bundle rather than paying for a second synthesis, and the
        recomputed package digest is what proves the two are the same write-up.
        """
        agent = next((assignment.agent_name for assignment in outcome.plan.selection.assignments), "general-purpose")
        reviewer_feedback: Sequence[Mapping[str, Any]] = ()
        feedback_reader = getattr(self._repo, "recent_project_stage_feedback", None)
        if callable(feedback_reader):
            try:
                reviewer_feedback = await feedback_reader(
                    project_id=str(cycle.get("project_id") or ""),
                    limit=MAX_RECENT_REVIEWER_FEEDBACK,
                )
            except Exception:  # noqa: BLE001 - presentation history is fail-soft
                logger.warning("Recent project reviewer feedback could not be loaded.", exc_info=True)
        unit = summarizer_unit(
            attempt_id=attempt_id,
            agent_name=agent,
            bundle=bundle,
            inputs=inputs,
            cycle=cycle,
            reviewer_feedback=reviewer_feedback,
        )
        if answer:
            # Quoted rather than paraphrased: it is the one part of the
            # write-up nobody else may decide.
            unit = replace(unit, prompt=f"{unit.prompt}\n\n{answer}")
        try:
            dispatched = await dispatcher((unit,), budget=budget)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.warning("The Build summarizer could not be dispatched.", exc_info=True)
            return _BuildSummary(refusal="The Build summarizer could not be run.")
        text = str(getattr(dispatched[0], "text", "") or "") if dispatched else ""
        parsed = parse_summary(text, bundle=bundle)
        if parsed.needs_input:
            # A question, not a refusal, and the difference decides what the
            # person is shown: one asks them to fix something, the other asks
            # them to decide something. The caller turns this into a control
            # they can answer.
            return _BuildSummary(question=parsed.clarification_question)
        if not parsed.ok:
            return _BuildSummary(refusal=parsed.refusal)
        return _BuildSummary(package=parsed.package, text=text)

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
        originating_thread_id: str = "",
    ) -> LiveStageResult:
        """Record a design the person wrote, or ask them to write it.

        No worker runs, no synthetic result, no agent attribution. The package
        is still a package — same path, same content addressing, same refusal to
        satisfy the gate — because the reviewer's job does not change just
        because the author was human.

        It also gets a registered deck. Design is submitted and decided in that
        deck and nowhere else, and this depth produces no chair result, which is
        what the council path reaches its deck through. Without one the design
        was recorded and then unapprovable: the stage sheet is inspection-only,
        so no control existed anywhere in the product that could move the gate,
        and Build stayed locked behind a design nobody could accept.
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

        # The gate the owner answers this through. Planned before rendering,
        # because the deck must carry its own surface id and registering binds
        # the bytes that id was assigned for.
        surface_plan = await self._plan_feedback_surface(
            stage=spec.stage,
            cycle_id=cycle_id,
            project_id=project_id,
            execution_key=execution_key,
            round_number=1,
            originating_thread_id=originating_thread_id,
            paused=False,
            artifact_uri=artifact_uri,
            artifact_hash=artifact_hash,
            # No meeting ran, so no worker run may be named as its chair.
            chair_worker_run_id=None,
        )
        deck = await asyncio.to_thread(
            _write_authored_design_deck,
            project_root=project_root,
            cycle=cycle,
            authored_design=text,
            package_path=artifact_uri,
            surface_id=(surface_plan.surface_id if surface_plan is not None and surface_plan.answerable else ""),
            surface_mode=(surface_plan.mode if surface_plan is not None else ""),
        )
        surface_id = None
        if deck is not None and surface_plan is not None:
            await self._register_feedback_surface(
                surface_plan,
                deck,
                cycle_id=cycle_id,
                project_id=project_id,
            )
            surface_id = surface_plan.surface_id if surface_plan.answerable else None
        return LiveStageResult(
            stage=spec.stage,
            cycle_id=cycle_id,
            note=digest,
            worker_count=0,
            produced_usable_evidence=True,
            artifact_uri=artifact_uri,
            deck_uri=(deck.uri if deck is not None else None),
            feedback_surface_id=surface_id,
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
    ) -> FeedbackSurfacePlan | None:
        """Read the cycle, then let ``feedback_surfaces`` decide the surface.

        Deciding before rendering breaks a circle: the deck has to carry its own
        surface id, but registering binds the deck's content hash, so the id
        cannot be assigned afterwards without changing the bytes it was assigned
        for. The rules themselves live in ``feedback_surfaces.plan_surface``;
        this method owns only the repository read they need.

        Returns ``None`` when there is nothing to bind to; the deck is still
        written, just without a bridge.
        """
        try:
            cycle = await self._repo.get_cycle(cycle_id, project_id=project_id)
        except Exception:  # noqa: BLE001 - a descriptor must not break the record
            logger.warning("Could not read cycle %s to plan its design feedback surface.", cycle_id, exc_info=True)
            return None
        if cycle is None:
            return None
        return plan_surface(
            cycle,
            stage=stage,
            execution_key=execution_key,
            round_number=round_number,
            originating_thread_id=originating_thread_id,
            paused=paused,
            artifact_uri=artifact_uri,
            artifact_hash=artifact_hash,
            decision_request=decision_request,
            chair_worker_run_id=chair_worker_run_id,
            review_issue_ids=review_issue_ids,
            transition_gate=transition_gate,
        )

    async def _register_feedback_surface(
        self,
        plan: FeedbackSurfacePlan,
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

        The row's shape is built by ``feedback_surfaces.registration_kwargs``;
        this method owns the durable write, and it stays the only place that
        performs it.
        """
        await self._repo.register_stage_feedback_surface(**registration_kwargs(plan, deck, cycle_id=cycle_id, project_id=project_id))

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
        activity: AsyncExitStack,
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
        # A convened meeting knows its stage from the server-registered deck, so
        # its row can open here rather than waiting on cycle state.
        meeting_activity = await activity.enter_async_context(
            self._stage_activity(config=config, stage=normalized, cycle_id=cycle_id),
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
        # deck. The meeting is an attachment to that assessment, not a fresh
        # transition that gets to reassess itself.
        transition_gate = None
        try:
            prior_surface = await self._repo.latest_stage_feedback_surface(
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
            cycle_id=cycle_id,
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
        if meeting_activity is not None:
            await meeting_activity.update(state=ActivityState.RECORDING, operation="stage.record")
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

    async def test_evidence_retry_snapshot(
        self,
        *,
        project_id: str,
        cycle_id: str,
    ) -> dict[str, Any] | None:
        """Read a degraded Test dossier only for the repeat-Test decision."""
        return await TestReviewService(
            repo=self._repo,
            app_config=self._app_config,
            runtime_reader=self._runtime,
        ).snapshot(
            project_id=project_id,
            cycle_id=cycle_id,
            allow_degraded_retry=True,
        )

    async def record_test_evidence_retry(
        self,
        *,
        project_id: str,
        cycle_id: str,
        snapshot: Mapping[str, Any],
        config: RunnableConfig,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Reopen Test from its hash-bound exception without exposing outcome routes."""
        return await TestReviewService(
            repo=self._repo,
            app_config=self._app_config,
            runtime_reader=self._runtime,
        ).record_outcome(
            project_id=project_id,
            cycle_id=cycle_id,
            snapshot=snapshot,
            recommendation="repeat_test",
            config=config,
            idempotency_key=idempotency_key,
            allow_degraded_retry=True,
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
        build_control: BuildControlAnswer | None = None,
        reuse_recorded_test_evidence: bool = False,
    ) -> LiveStageResult:
        """Run the cycle's currently executable stage and record what it produced.

        The activity row this stage reports on is opened from inside, once the
        stage is actually known, and closed by this stack however the work ends
        — including an exception, which settles the row ``failed`` rather than
        leaving it spinning. Opening it here instead would have to name a stage
        nobody has resolved yet.
        """
        try:
            async with AsyncExitStack() as activity:
                return await self._execute_stage(
                    activity=activity,
                    project_id=project_id,
                    cycle_id=cycle_id,
                    request_text=request_text,
                    state=state,
                    config=config,
                    authored_design=authored_design,
                    council_adjustment=council_adjustment,
                    participant_settings=participant_settings,
                    approved_council_proposal=approved_council_proposal,
                    clarification_answer=clarification_answer,
                    review_meeting_stage=review_meeting_stage,
                    expected_stage=expected_stage,
                    expected_cycle_revision=expected_cycle_revision,
                    build_control=build_control,
                    reuse_recorded_test_evidence=reuse_recorded_test_evidence,
                )
        except asyncio.CancelledError:
            # A graceful Gateway reload/cancel reaches this boundary while the
            # persistence engine is still available.  Settle every Build step
            # owned by this run immediately; leaving it ``running`` forces the
            # next request to wait for the six-hour orphan-reclaim backstop and
            # leaves every read model spinning in the meantime.
            runtime = self._runtime(config)
            run_id = runtime.get("run_id")
            cancel_steps = getattr(self._repo, "cancel_running_step_attempts", None)
            if callable(cancel_steps) and isinstance(run_id, str) and run_id and project_id and cycle_id:
                cleanup = asyncio.create_task(
                    cancel_steps(
                        project_id=project_id,
                        cycle_id=cycle_id,
                        parent_run_id=run_id,
                        summary="The run owning this Build step was interrupted before the step settled. Retry resumes from the last committed predecessor.",
                    )
                )
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        # Shutdown can cancel the parent more than once. The
                        # database cleanup is bounded and must finish before
                        # the run releases ownership of its durable step.
                        continue
                    except Exception:  # noqa: BLE001 - cancellation itself must still propagate
                        break
                try:
                    cleanup.result()
                except asyncio.CancelledError:
                    logger.warning("Interrupted Build-step cleanup was itself cancelled for run %s.", run_id)
                except Exception:  # noqa: BLE001 - cancellation itself must still propagate
                    logger.warning("Could not settle interrupted Build steps for run %s.", run_id, exc_info=True)
            raise
        except (BuildStepRecordingError, BuildControlNotRecorded) as refusal:
            # Persistence is part of the enabled Build workflow's authority.
            # Return a visible, retryable pause instead of attaching evidence
            # from an execution whose step chain or human decision is missing.
            return LiveStageResult(
                stage=expected_stage or "build",
                cycle_id=cycle_id or "",
                note=str(refusal),
            )

    async def _execute_stage(
        self,
        *,
        activity: AsyncExitStack,
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
        build_control: BuildControlAnswer | None = None,
        reuse_recorded_test_evidence: bool = False,
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
                activity=activity,
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
        stage_activity = await activity.enter_async_context(
            self._stage_activity(config=config, stage=stage or "", cycle_id=cycle_id),
        )
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
        dbtl_config = getattr(self._app_config, "dbtl", None)
        degraded_evidence_enabled = bool(getattr(dbtl_config, "degraded_evidence_continuation", False))
        recorded_spec_key = str((attempt or {}).get("stage_spec_key") or "").strip()
        try:
            spec = resolve_spec_by_key(recorded_spec_key) if recorded_spec_key else _initial_stage_spec(stage)
        except StageSpecNotFound as exc:
            return LiveStageResult(
                stage=stage,
                cycle_id=cycle_id,
                note=f"The stage records an unavailable execution contract ({recorded_spec_key}): {exc}",
            )
        # Build is a resumable, multi-call workflow. Test also owns a
        # server-verified rerun contract. Pin either version before dispatch so
        # a deployment cannot change its output or validity authority midway
        # through an attempt.
        # opening the first durable step so a deployment between phases cannot
        # change the output contract, prompt budget, or retry material for an
        # attempt that is already under way. The repository lock also resolves
        # two concurrent starters to the same pinned version.
        if stage in {"build", "test"}:
            pin_stage_spec = getattr(self._repo, "pin_stage_spec", None)
            if callable(pin_stage_spec):
                try:
                    pinned_key = await pin_stage_spec(
                        project_id=project_id,
                        stage_attempt_id=str((attempt or {}).get("id") or ""),
                        stage_spec_key=spec.spec_key,
                    )
                    spec = resolve_spec_by_key(pinned_key)
                    if spec.stage != stage:
                        raise ValueError(f"Pinned stage spec {pinned_key!r} belongs to {spec.stage!r}, not {stage!r}.")
                except (LookupError, StageSpecNotFound, ValueError) as exc:
                    return LiveStageResult(
                        stage=stage,
                        cycle_id=cycle_id,
                        note=f"The {stage.title()} execution contract could not be pinned safely: {exc}",
                    )

        datasets = await self._repo.list_datasets(cycle_id, project_id=project_id)
        reconciliation = await self._repo.reconciliation_view(cycle_id, project_id=project_id) if stage in {"design", "reconciliation", "build", "test", "learn"} else None
        build_test = await self._repo.build_test_view(cycle_id, project_id=project_id) if stage in {"build", "test", "learn"} else None
        upstream_evidence_exception: dict[str, Any] | None = None
        if stage == "test" and degraded_evidence_enabled:
            build_attempt = next(
                (item for item in cycle.get("stages", []) if item.get("stage") == "build" and item.get("status") == StageStatus.ADVANCED_WITH_EXCEPTION.value),
                None,
            )
            if build_attempt is not None:
                artifact = max(
                    (item for item in cycle.get("artifacts", []) if item.get("stage_attempt_id") == build_attempt.get("id") and item.get("artifact_type") == "evidence_exception"),
                    key=lambda item: int(item.get("revision") or 0),
                    default=None,
                )
                if artifact is not None:
                    upstream_evidence_exception = await asyncio.to_thread(
                        _read_evidence_exception_package,
                        project_root=project_root,
                        artifact=artifact,
                    )
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

        attempt_id = f"dbtl-{_safe_token(execution_key)}"
        stage_workspace = None
        if stage != "design":
            stage_workspace, _ = await asyncio.to_thread(
                _prepare_stage_workspace,
                project_root,
                attempt_id=attempt_id,
                stage=stage,
            )
        project_manifest = await asyncio.to_thread(_project_manifest, project_root)
        pre_run_files = await asyncio.to_thread(_project_file_snapshot, project_root) if stage == "build" else {}

        # `load_design` runs before anything is dispatched, because the one
        # thing a Build must not do is spend an hour implementing a document
        # nobody approved.
        #
        # It is gated on the rollout switch along with the recording, and
        # deliberately so: this is a *new refusal* on a path that previously had
        # none, and a deployment whose approved package is not readable through
        # the project root — an older cycle, a different sandbox mapping — would
        # go from running Build to being unable to run it at all. The switch is
        # what makes that discoverable in the manual profile first rather than
        # in somebody's experiment. With the flag off, Build behaves exactly as
        # it did.
        build_workflow_enabled = stage == "build" and bool(getattr(dbtl_config, "build_workflow_steps", False))
        build_recorder = DISABLED_RECORDER
        build_inputs: BuildInputBundle | None = None
        control_gate = DISABLED_GATE
        plan_adjustment = ""
        worker_answer = ""
        worker_answer_step = ""
        if build_workflow_enabled:
            stage_attempt_row_id = str((attempt or {}).get("id") or "")
            control_gate = BuildControlGate(
                repo=self._repo,
                project_id=project_id,
                cycle_id=cycle_id,
                stage_attempt_id=stage_attempt_row_id,
                thread_id=str(runtime.get("thread_id") or ""),
                run_id=str(run_id or ""),
                responder_user_id=str(user_id or ""),
            )
            # The answer is recorded **before** the recorder loads its material,
            # because a restart or a replan moves the digest chain: recording it
            # afterwards would open every step against the material of the run
            # the person just asked to abandon, replay its committed success,
            # and leave the button doing nothing.
            if build_control is not None and build_control.stage_attempt_id == stage_attempt_row_id:
                settled = _settled_control(build_control)
                try:
                    answered_control = await control_gate.record_answer(settled)
                except BuildControlNotRecorded as refusal:
                    # Replan and Restart move the digest chain through that
                    # record. Proceeding on an unrecorded one would replay the
                    # committed work the person asked to discard while telling
                    # them it had been discarded.
                    #
                    # The control is re-presented with the refusal, because a
                    # reply counts as answered the moment it resolves: without
                    # this the routing fence has already stood down and "try
                    # again" reaches ordinary chat instead of the control.
                    return LiveStageResult(
                        stage=stage,
                        cycle_id=cycle_id,
                        note=str(refusal),
                        control_request=await control_gate.reopen_card(),
                    )
                if settled.action is BuildControlAction.START_MEETING:
                    # Advisory by construction: the meeting returns options and
                    # a recommendation, and the same question is put back with
                    # that briefing above it. Nothing about running a meeting
                    # resumes the Build — only the person's answer does.
                    question = str((answered_control or {}).get("question") or "")
                    design = _approved_design_brief(cycle) or {}
                    briefing, record = await self._run_build_work_meeting(
                        dispatcher=self._dispatcher
                        or self._production_dispatcher(
                            config=config,
                            state=state,
                            project_id=project_id,
                            project_root=project_root,
                            cycle_id=cycle_id,
                            stage=stage,
                            meeting=True,
                        ),
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
                    return LiveStageResult(
                        stage=stage,
                        cycle_id=cycle_id,
                        note="Holding here. Nothing was dispatched, and every finished part of this build stays recorded.",
                    )
                if settled.action is BuildControlAction.CHANGE_PLAN:
                    return LiveStageResult(
                        stage=stage,
                        cycle_id=cycle_id,
                        note="Tell me what to change and I will draw the plan again from your words.",
                        control_request=await control_gate.raise_control(
                            change_plan_request(
                                previous=_control_context(build_control, workflow_spec_key=resolve_build_workflow().spec_key),
                                remaining_only=settled.kind is BuildControlKind.PHASE_PAUSE,
                            )
                        ),
                    )
                if settled.action is BuildControlAction.REPLAN_BUILD:
                    plan_adjustment = settled.comment
                if settled.action is BuildControlAction.ANSWER_DIRECTLY:
                    # The answer to a worker's own question. The step that asked
                    # settled `needs_input` — terminal and never a success — so
                    # this run opens a fresh attempt at it, and the words have to
                    # reach that attempt or it asks the same question again.
                    # Labelled, because the question is the *worker's* own
                    # sentence: shipping the two as one string would attribute
                    # it to the person, which is the one thing every other
                    # verbatim-capture rule here exists to prevent.
                    asked = str((answered_control or {}).get("question") or "")
                    worker_answer = "\n\n".join(part for part in (f"You asked: {asked}" if asked else "", f"The project owner answered: {settled.comment}") if part)
                    worker_answer_step = settled.step_key

            # Execution is part of the Build contract, so prove the worker can
            # do it before spending even the planner's tokens.  The observed
            # failure spent ~382k tokens authoring files in a runtime where the
            # Bash tool had been removed by configuration; no decomposition or
            # retry could make that run executable.
            preflight_error = self._build_execution_preflight_error(
                config=config,
                stage_workspace=stage_workspace,
                sandbox_state=state.get("sandbox"),
            )
            if preflight_error:
                control = await control_gate.raise_control(
                    execution_preflight_request(
                        cycle_id=cycle_id,
                        stage_attempt_id=stage_attempt_row_id,
                        workflow_spec_key=resolve_build_workflow().spec_key,
                        cycle_revision=int(cycle.get("db_revision") or 0),
                    )
                )
                if stage_activity is not None:
                    await stage_activity.settle(
                        ActivityState.PAUSED,
                        operation="stage.preflight_failed",
                    )
                return LiveStageResult(
                    stage=stage,
                    cycle_id=cycle_id,
                    note=preflight_error,
                    control_request=control,
                )
            build_recorder = await make_build_step_recorder(
                self._repo,
                RecorderRequest(
                    enabled=True,
                    project_id=project_id,
                    cycle_id=cycle_id,
                    stage_attempt_id=stage_attempt_row_id,
                    parent_run_id=str(run_id),
                    project_root=str(project_root),
                ),
            )
            load_design = await build_recorder.begin(BuildStepKey.LOAD_DESIGN)
            try:
                current_build_inputs = await asyncio.to_thread(
                    resolve_build_inputs,
                    cycle,
                    project_root=project_root,
                    datasets=datasets,
                    manifest=project_manifest,
                    policy={
                        "stage_spec_key": spec.spec_key,
                    },
                )
            except BuildInputError as refusal:
                await build_recorder.fail(load_design, refusal.code, refusal.summary)
                return LiveStageResult(
                    stage=stage,
                    cycle_id=cycle_id,
                    note=f"{refusal.summary} No Build worker was dispatched and nothing was recorded as Build evidence.",
                )
            if load_design.replayed:
                restored_inputs = restore_build_input_bundle(build_recorder.replay(load_design))
                if restored_inputs is not None and restored_inputs.digest == str(load_design.output_digest or ""):
                    # A retry uses the exact manifest/policy bundle the committed
                    # plan and finished phases saw. Recomputing it here mixed an
                    # old digest chain with today's workspace and later claimed
                    # the old phases had read today's files.
                    build_inputs = restored_inputs
                else:
                    load_design = await build_recorder.reopen(load_design)
                    build_inputs = current_build_inputs
            else:
                build_inputs = current_build_inputs
            await build_recorder.succeed(
                load_design,
                build_inputs.digest,
                execution={"design_revision": build_inputs.design_revision, "design_truncated": build_inputs.design_truncated},
                payload=build_inputs.as_dict(),
            )
        elif stage == "test":
            try:
                build_inputs = await asyncio.to_thread(
                    resolve_build_inputs,
                    cycle,
                    project_root=project_root,
                    datasets=datasets,
                    manifest=project_manifest,
                    policy={
                        "stage_spec_key": spec.spec_key,
                    },
                )
            except BuildInputError as refusal:
                # Legacy Test attempts predate the deliverables contract. They
                # remain executable without silently inventing a manifest; a
                # new hash-bound Design package resolves above and activates
                # the strict audit path.
                logger.info("Test has no resolvable Design deliverables manifest: %s", refusal.summary)

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
            # Do not hand later workers an unsettled reconciliation matrix as
            # if it were a Build prerequisite. Reconciliation is shown only to
            # its own evidence workflow; Build/Test authority comes from the
            # server-bound lineage.
            "reconciliation": (
                reconciliation
                if stage == "reconciliation"
                else {
                    "status": "not_required",
                    "instruction": ("Data Reconciliation is not a Build prerequisite. Missing dataset declarations or reconciliation matrix rows are not a blocker, limitation, or failed validity check."),
                }
            ),
            "build_test": build_test,
            "input_provenance_policy": {
                "authority": "server_bound_build_lineage",
                "instruction": (
                    "Build binds the exact files it reads with server-computed content hashes, and Test verifies that durable Build lineage. "
                    "For compatibility, a validity check named reconciled_inputs means bound input provenance; judge the Build lineage, "
                    "not the existence of reconciliation rows. An older Build package may describe absent reconciliation as a limitation; that is "
                    "historical worker commentary, not active policy."
                ),
            },
            "test_validity_contract": (
                {
                    "pack_key": DEFAULT_VALIDITY_PACK.pack_key,
                    "required_checks": [check.value for check in DEFAULT_VALIDITY_PACK.required_checks],
                    "metric_schema": {
                        "required_fields": ["name", "value", "threshold", "criterion", "plausible_max", "unit"],
                        "criterion_values": ["gte", "lte"],
                        "instruction": (
                            "Use only `gte` or `lte` for every metric criterion. For an exact target, use `gte` with value and threshold equal; "
                            "the named validity checks carry exactness and direction semantics. Never emit `equal_to`, `greater_than`, or prose synonyms."
                        ),
                    },
                    "authoritative_rules": [
                        "Only required_checks may determine the overall Test outcome. Do not invent or require an additional gate.",
                        "The server-bound Build lineage satisfies reconciled_inputs when present. Do not require a declaration, matrix, or reconciliation artifact.",
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
            "stage_workspace": (
                {
                    # Replaced with a distinct attempt/stage/unit path inside
                    # the production dispatcher immediately before the unit
                    # runs. No two concurrent workers receive the same grant.
                    "path": STAGE_UNIT_WORKSPACE_PLACEHOLDER,
                    "instruction": (
                        "Write every new implementation, derived output, and execution log under this exact directory. "
                        "Do not write under outputs/dbtl; the stage adapter publishes validated review evidence there after your result passes its contract."
                    ),
                    # The same idiom the phased Build workflow teaches. A worker
                    # dispatched through the monolithic path pays the identical
                    # per-command repeats, so leaving it out here would make the
                    # saving depend on which dispatch shape happened to run.
                    "shell_note": SHELL_WORKSPACE_IDIOM,
                }
                if stage_workspace
                else None
            ),
            "project_workspace_manifest": project_manifest,
            "build_input_policy": (
                {
                    "instruction": (
                        "Read the data files needed to implement the approved design and list every exact workspace path in "
                        "provenance.inputs_examined. The server will compute and record their hashes automatically. "
                        "No dataset declaration or reconciliation matrix is required, and their absence must not be reported as a failure or limitation."
                    ),
                }
                if stage == "build"
                else None
            ),
            "prior_design_council_runs": _compact_design_history(prior_design_runs),
            # Accepted pre-cycle context is server-bound to the cycle and is
            # shared before seat-specific instructions. Every Design
            # participant sees the same package and hash; the frontend never
            # reconstructs this from setup answers.
            "discovery_package": (
                {
                    "hash": cycle.get("discovery_package_hash"),
                    "content": cycle.get("discovery_package"),
                }
                if stage == "design" and cycle.get("discovery_package_hash") and isinstance(cycle.get("discovery_package"), Mapping)
                else None
            ),
            # Only present once a person has approved a Design package.
            # Its absence is meaningful: a later stage seeing no brief is
            # working before the gate, not merely without context.
            "approved_design_brief": _approved_design_brief(cycle),
            # Build and Test get the resolved bundle: the design already read and
            # hash-verified by the server, so the worker's first act is
            # implementing or auditing it rather than searching for it.
            "build_input_bundle": build_inputs.as_dict() if build_inputs is not None else None,
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
        build_phase_context = _build_phase_context(stage_context_payload, build_inputs) if stage == "build" and build_inputs is not None else stage_context
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
                    originating_thread_id=str(runtime.get("thread_id") or ""),
                )
        base_dispatcher = self._dispatcher or self._production_dispatcher(
            config=config,
            state=state,
            project_id=project_id,
            project_root=project_root,
            cycle_id=cycle_id,
            stage=stage,
            meeting=stage == "design",
            stage_workspace=stage_workspace,
        )

        async def dispatcher(
            units: Sequence[WorkUnit],
            *,
            budget: WorkerBudget,
        ) -> Sequence[DispatchOutcome]:
            return await base_dispatcher(
                _bind_stage_unit_workspaces(units, stage_workspace),
                budget=budget,
            )

        # `plan_build` is cheap and re-runnable by construction: it writes
        # nothing, runs nothing, and dispatches nobody. Its answer is data — a
        # content-addressed plan every phase attempt binds to — so a replan
        # invalidates the phases beneath it rather than silently rebinding them.
        build_plan: BuildPhasePlan | None = None
        phase_run: _PhaseRun | None = None
        if build_workflow_enabled and build_inputs is not None:
            plan_handle = await build_recorder.begin(BuildStepKey.PLAN_BUILD)
            # What the chain says `load_design` produced — which on a resume is
            # the digest that step *committed*, not the one this run recomputed.
            # The project manifest rides in the input bundle, so a Build that
            # wrote outputs changes its own recomputed digest; binding the plan
            # to that would leave the plan and every phase beneath it
            # invalidated by their own success.
            design_output_digest = plan_handle.predecessor_digests[0] if plan_handle.predecessor_digests else build_inputs.digest
            # A committed plan is read back rather than redrawn. Replanning is
            # cheap, but a *different* plan would give every phase beneath it a
            # new identity and discard finished work — the planner is not
            # deterministic, so re-running it on a resume is how a retry turns
            # into a restart.
            build_plan = _restored_build_plan(
                build_recorder.replay(plan_handle),
                expected_digest=str(plan_handle.output_digest or ""),
                input_digest_value=design_output_digest,
            )
            if build_plan is None:
                if plan_handle.replayed:
                    # The committed plan cannot be produced, so it is redrawn —
                    # and the redraw is recorded. Settling nothing would leave
                    # every phase beneath this bound to a plan nobody can read.
                    plan_handle = await build_recorder.reopen(plan_handle)
                build_plan, plan_reasons = await self._plan_build(
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
                    # `needs_input`. The stage stays safely paused rather than
                    # guessing at what the Design left unresolved — and the
                    # question is raised as a control bound to this step, so the
                    # answer comes back to the step that asked rather than to
                    # whatever the next request happens to be.
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
                    await build_recorder.settle(
                        plan_handle,
                        state=StepState.NEEDS_INPUT,
                        summary=build_plan.clarification_question,
                        human_input_request_id=str(control.get("request_id") or ""),
                    )
                    if stage_activity is not None:
                        await stage_activity.settle(ActivityState.PAUSED, operation="stage.wait_human")
                    return LiveStageResult(
                        stage=stage,
                        cycle_id=cycle_id,
                        note="The build planner needs one decision before any work starts.",
                        control_request=control,
                    )
                await build_recorder.succeed(
                    plan_handle,
                    # Bound to what the plan was drawn *from*, not only to what
                    # it says. Two different approved Designs can imply the same
                    # decomposition, and a phase chained to the plan's own
                    # content digest would then survive a Design change that
                    # reshaped the work. The value is the chain's, so what a
                    # later run recomputes to check this is what was recorded.
                    plan_output_digest(plan_digest=build_plan.digest, input_digest_value=design_output_digest),
                    execution=_plan_execution(build_plan, degraded=bool(plan_reasons)),
                    payload=build_plan.as_dict(),
                )

            # The cheapest intervention there is: the plan exists, nothing has
            # run, and redirecting it costs a sentence. Behind its own switch
            # because it interrupts every Build, and a deployment that trusts
            # its planner should not be asked four times a day.
            if bool(getattr(dbtl_config, "build_plan_confirmation", False)) and build_plan.dispatchable and not await control_gate.plan_is_confirmed(build_plan.digest):
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
                return LiveStageResult(
                    stage=stage,
                    cycle_id=cycle_id,
                    note="Here is the plan for this build. Nothing has run yet.",
                    control_request=control,
                )

        proposal: CouncilProposal | None = None
        test_rerun_record: TestRerunRecord | None = None
        test_rerun_pair: tuple[WorkUnit, StageWorkerResult] | None = None
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
        # A roster somebody just confirmed outranks the cheap revision route.
        # Folding an objection into the existing synthesis is the right default
        # for an unattended refinement — reconvening spends a second meeting
        # re-arguing what the reviewer accepted — but it is the wrong answer to
        # a person who read a roster of three and pressed Start meeting: the
        # card described a council and one chair ran, which is the failure the
        # roster proposal exists to prevent, arriving by the route that avoids
        # its cost. The objection is not lost with the route; it travels into
        # the round as the change request either way.
        elif stage == "design" and council_plan is not None and approved_proposal is None and revision_verdict is not None and not revision_verdict.reconvenes and revision_positions:
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
        elif build_plan is not None:
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
                # The answer on this request, or one already recorded against
                # this plan. The request-scoped half carries the same scope
                # guard every other use of the answer carries — this is the one
                # place a cross-attempt answer could influence dispatch, and it
                # should not be the exception — while the durable half is what
                # keeps an answered boundary answered on every later retry.
                boundaries_released=(
                    (build_control is not None and build_control.stage_attempt_id == str((attempt or {}).get("id") or "") and build_control.action in {BuildControlAction.CONTINUE_BUILD, BuildControlAction.RETRY_STEP})
                    or await control_gate.boundary_released(build_plan.digest)
                ),
                user_id=str(user_id),
                sandbox_state=state.get("sandbox"),
                # A custom dispatcher is the adapter's test/integration seam;
                # production always executes through the run's real sandbox.
                enforce_server_execution=self._dispatcher is None,
                thread_id=str(self._runtime(config).get("thread_id") or ""),
            )
            outcome = phase_run.outcome
        elif stage == "test" and isinstance(upstream_evidence_exception, Mapping) and upstream_evidence_exception.get("condition") == "untrusted":
            # The human chose to carry failed evidence forward for evaluation,
            # not to spend another worker pretending the missing execution or
            # quarantined bytes can now be tested.
            preliminary = plan_stage(
                spec,
                self._candidates(),
                attempt_id=attempt_id,
                context=stage_context,
            )
            outcome = StageExecutionOutcome(plan=preliminary)
        elif stage == "test" and "server_verified_build_rerun" in spec.validity_gates:
            preliminary = plan_stage(spec, self._candidates(), attempt_id=attempt_id, context=stage_context)
            if not preliminary.dispatchable:
                logger.info("dbtl stage %s not dispatchable: %s", spec.spec_key, "; ".join(preliminary.selection.notes) or "no work units")
                outcome = StageExecutionOutcome(plan=preliminary)
            else:
                lineage = dict((build_test or {}).get("build_lineage") or {})
                prepared = await asyncio.to_thread(
                    prepare_test_rerun,
                    lineage,
                    project_root=project_root,
                )
                selected = preliminary.units[0]
                reused_test_outcome = False
                if isinstance(prepared, PreparedTestRerun):
                    rerun_unit = build_test_rerun_unit(
                        prepared,
                        attempt_id=attempt_id,
                        agent_name=selected.agent_name,
                        via_generalist=selected.via_generalist,
                    )
                    reused = None
                    if reuse_recorded_test_evidence:
                        stored_test_runs = await self._repo.list_worker_runs(
                            cycle_id,
                            project_id=project_id,
                            stage="test",
                        )
                        reused = _reusable_test_worker_results(
                            stored_test_runs,
                            stage_attempt_id=str((attempt or {}).get("id") or ""),
                            units=(rerun_unit, *preliminary.units),
                        )
                    if reused is not None:
                        reused_units, reused_results, test_rerun_record = reused
                        outcome = StageExecutionOutcome(
                            plan=replace(preliminary, units=reused_units),
                            results=reused_results,
                        )
                        reused_test_outcome = True
                        logger.info("Reused %d recorded Test worker results for %s after a server-side evidence refusal.", len(reused_results), cycle_id)
                    else:
                        rerun_budget = replace(
                            spec.budget,
                            max_workers=1,
                            max_turns=min(spec.budget.max_turns, 80),
                            max_tokens=min(spec.budget.max_tokens, 120_000),
                            timeout_seconds=min(spec.budget.timeout_seconds, 600),
                            token_limit_enforced=True,
                        )
                        dispatched_rerun = await dispatcher((rerun_unit,), budget=rerun_budget)
                        dispatched_receipt = dispatched_rerun[0] if dispatched_rerun else DispatchOutcome(unit_id=rerun_unit.unit_id, text=None, error="The rerun worker returned no dispatch outcome.")
                        worker_rerun = StageWorkerResult(
                            status=(WorkerStatus.FAILED if dispatched_receipt.error else WorkerStatus.COMPLETED),
                            summary=dispatched_receipt.error or "The command attempt returned; server verification owns its verdict.",
                            capability=rerun_unit.capability,
                            agent_name=rerun_unit.agent_name,
                            stop_reason=dispatched_receipt.stop_reason,
                            token_usage=dict(dispatched_receipt.token_usage or {}),
                        )
                        test_rerun_record = await asyncio.to_thread(
                            validate_test_rerun,
                            worker_rerun,
                            prepared,
                            project_root=project_root,
                            unit_workspace=workspace.unit_stage_workspace(stage_workspace, rerun_unit.unit_id),
                        )
                        test_rerun_pair = (
                            rerun_unit,
                            rerun_result(
                                test_rerun_record,
                                agent_name=worker_rerun.agent_name,
                                token_usage=dict(worker_rerun.token_usage),
                            ),
                        )
                        rerun_rejected = ()
                else:
                    test_rerun_record = prepared
                    rerun_unit = WorkUnit(
                        unit_id=f"{attempt_id}-build-rerun",
                        capability="reproducibility_rerun",
                        agent_name="server",
                        prompt="The server refused the Build rerun before dispatch.",
                        role="rerun",
                        focus="records why the Build rerun could not start",
                    )
                    test_rerun_pair = (
                        rerun_unit,
                        rerun_result(test_rerun_record, agent_name="server"),
                    )
                    rerun_rejected = ()
                if not reused_test_outcome:
                    test_context = "\n".join(
                        [
                            stage_context,
                            "",
                            "Server-owned Build rerun result (this overrides any worker-authored reproducibility check):",
                            json.dumps(test_rerun_record.as_dict(), sort_keys=True, ensure_ascii=False),
                        ]
                    )
                    outcome = await arun_stage(
                        spec,
                        self._candidates(),
                        dispatcher,
                        attempt_id=attempt_id,
                        context=test_context,
                    )
                    if test_rerun_pair is not None:
                        outcome = replace(
                            outcome,
                            plan=replace(outcome.plan, units=(test_rerun_pair[0], *outcome.plan.units)),
                            results=(test_rerun_pair[1], *outcome.results),
                            rejected=(*rerun_rejected, *outcome.rejected),
                        )
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

        published_build_artifacts: list[dict[str, Any]] = []
        build_execution_record: BuildExecutionBundle | None = None
        build_fulfillment: BuildFulfillment | None = None
        build_fulfillment_refusal = ""
        if phase_run is not None:
            # Already published, one phase at a time, before each phase's own row
            # was settled. Running the bulk publisher again here would resolve
            # references that now point at the governed output tree rather than
            # at a worker's isolated workspace, and reject every one of them.
            published_build_artifacts = phase_run.published
        elif stage == "build" and stage_workspace:
            outcome, published_build_artifacts = await asyncio.to_thread(
                _publish_build_worker_artifacts,
                project_root=project_root,
                cycle=cycle,
                outcome=outcome,
                stage_workspace=stage_workspace,
                attempt_id=attempt_id,
            )
            unit_result_pairs = list(zip(outcome.plan.units, outcome.results, strict=True))
        if stage == "build" and build_inputs is not None and build_inputs.deliverable_manifest is not None:
            build_fulfillment = derive_build_fulfillment(
                build_inputs.deliverable_manifest,
                published=published_build_artifacts,
                declarations=_declared_deliverable_fulfillments(outcome.results),
            )
            if not build_fulfillment.reviewable:
                build_fulfillment_refusal = "Build did not attempt every approved Design deliverable, so the result is not reviewable."
            for artifact in published_build_artifacts:
                source_path = str(artifact.get("source_path") or "")
                matched = next(
                    (item.id for item in build_inputs.deliverable_manifest.deliverables if source_path in item.expected_paths),
                    None,
                )
                if matched is not None:
                    artifact["deliverable_id"] = matched
        if stage == "build" and published_build_artifacts:
            build_execution_record = replace(
                execution_bundle(outcome.trustworthy_results, published=published_build_artifacts),
                deliverable_fulfillment=build_fulfillment,
            )
            if phase_run is not None and build_execution_record.rerun_spec is None:
                # The phased path derives its own record rather than merging the
                # workers'. Only when they produced none, so a single-phase Build
                # whose worker recorded one keeps the worker's own account.
                driver_spec = await asyncio.to_thread(
                    _write_build_driver,
                    project_root=project_root,
                    cycle=cycle,
                    results=list(phase_run.outcome.trustworthy_results),
                    bound_inputs=list(phase_run.input_artifacts),
                )
                if driver_spec is not None:
                    build_execution_record = replace(build_execution_record, rerun_spec=driver_spec, rerun_procedure=driver_spec.command)
        missing_structured_rerun = bool(stage == "build" and "structured_rerun_spec" in spec.validity_gates and (build_execution_record is None or build_execution_record.rerun_spec is None))
        # **A stage can refuse for a reason no worker owns.** Every worker here
        # may have returned a contract-valid result and the stage still not be
        # completable — the pinned contract wants a structured rerun record and
        # none arrived. Naming that reason once, here, is what keeps the two
        # places that report it from describing one refusal two ways; without it
        # the durable note fell through to the per-worker list, blamed the
        # workers, and then listed nothing, because none of them had failed.
        stage_refusal = MISSING_STRUCTURED_RERUN_REASON if missing_structured_rerun else ""
        if build_workflow_enabled:
            # Settled from the *published* artifacts, which is what makes the
            # step reusable: the bytes are already copied into the governed
            # output tree and hashed, so a summary or deck that fails afterwards
            # cannot take this work with it. The container is opened here rather
            # than before dispatch because the per-phase attempts are what a
            # resume reads; this row is the fold over them.
            await _settle_execution_step(
                build_recorder,
                await build_recorder.begin(BuildStepKey.EXECUTE_PHASES),
                published=published_build_artifacts,
                outcome=outcome,
                incomplete_because=(phase_run.stopped_because if phase_run is not None and not phase_run.complete else stage_refusal),
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
        design_manifest = None
        design_contract_refusal = ""
        if stage == "design" and chair_result is not None and chair_result.is_trustworthy and cycle.get("cycle_class"):
            design_manifest, design_contract_refusal = _design_deliverable_manifest(
                chair_result,
                cycle_class=str(cycle["cycle_class"]),
            )
        design_ready = stage != "design" or (
            design_debate_complete and chair_result is not None and chair_result.is_trustworthy and chair_result.status is WorkerStatus.COMPLETED and (not cycle.get("cycle_class") or design_manifest is not None)
        )
        if design_contract_refusal:
            stage_refusal = design_contract_refusal
        if build_fulfillment_refusal:
            stage_refusal = build_fulfillment_refusal
        _deliverable_audit = None
        deliverable_audit_refusal = ""
        test_owned_artifacts: list[dict[str, Any]] = []
        if stage == "test" and build_inputs is not None and build_inputs.deliverable_manifest is not None:
            if stage_workspace:
                test_owned_artifacts = await asyncio.to_thread(
                    _test_owned_deliverable_candidates,
                    project_root=project_root,
                    stage_workspace=stage_workspace,
                    outcome=outcome,
                    manifest=build_inputs.deliverable_manifest,
                )
            _deliverable_audit, deliverable_audit_refusal = _validated_deliverable_audit(
                outcome.trustworthy_results,
                manifest=build_inputs.deliverable_manifest,
                build_test=build_test,
                stage_artifacts=test_owned_artifacts,
            )
            if deliverable_audit_refusal:
                stage_refusal = deliverable_audit_refusal
            elif test_owned_artifacts:
                await asyncio.to_thread(
                    _publish_test_owned_deliverables,
                    test_owned_artifacts,
                )
        test_assessment = _validated_test_assessment(outcome.trustworthy_results, build_test=build_test, rerun=test_rerun_record) if stage == "test" else None
        if stage == "test" and outcome.produced_usable_evidence and test_assessment is None:
            stage_refusal = (
                "The Test workers returned evidence, but no complete server-readable validity assessment was present. Every headline metric must use criterion `gte` or `lte`, and the check set must exactly match the pinned validity pack."
            )
        # **A plan that did not finish is not a Build.** Every phase that ran
        # ran truthfully, so `produced_usable_evidence` is true of a Build whose
        # second phase failed and of one that stopped at a `pause_after`
        # boundary — and it would have written a review package and a deck for
        # both, presenting a fraction of the planned work as the completed
        # thing a person approves and Test measures. The committed phases stay
        # committed and reusable; what they do not do is become evidence.
        build_plan_incomplete = (phase_run is not None and not phase_run.complete) or missing_structured_rerun
        produced_usable_evidence = outcome.produced_usable_evidence and design_ready and (stage != "test" or test_assessment is not None) and not deliverable_audit_refusal and not build_plan_incomplete and not build_fulfillment_refusal
        # Not opened at all when the plan did not finish. Opening it would
        # settle as `summary_contract_rejected` — a *presentational* code, which
        # the UI renders as "the build ran; the write-up broke". The build did
        # not run, and telling somebody to retry the write-up would send them to
        # fix the one part that is fine.
        summary_handle = await build_recorder.begin(BuildStepKey.SUMMARIZE_RESULTS) if build_workflow_enabled and not build_plan_incomplete else StepHandle(step=BuildStepKey.SUMMARIZE_RESULTS)
        # For Build under the workflow, the summarizer *replaces* the generic
        # package writer. The generic renderer answers "which work units ran",
        # and a Build reviewer needs "what did we get" — the numbers and the
        # plots. Every other stage, and Build with the switch off, is unchanged.
        build_summary_owns_evidence = build_workflow_enabled and produced_usable_evidence and bool(published_build_artifacts)
        build_package = None
        summary_refusal = ""
        summary_question = ""
        no_slide_results = False
        #: What a later run rebuilds this write-up from instead of paying for a
        #: second synthesis. `None` for a replay, which wrote nothing new.
        summary_payload: dict[str, Any] | None = None
        if produced_usable_evidence and not build_summary_owns_evidence:
            artifact_uri, artifact_hash, artifact_digest = await asyncio.to_thread(
                _write_stage_package,
                project_root=project_root,
                cycle=cycle,
                outcome=outcome,
                idempotency_key=execution_key,
                council=council_plan,
                deliverable_audit=_deliverable_audit,
            )
            artifact_type = spec.required_artifact_types[0]
        if build_summary_owns_evidence:
            bundle = build_execution_record or replace(
                execution_bundle(outcome.trustworthy_results, published=published_build_artifacts),
                deliverable_fulfillment=build_fulfillment,
            )
            # The whole reason the write-up is its own step: a deck that failed
            # to render is retried against *this* package rather than against a
            # second, differently-hashed one nobody reviewed.
            restored_summary = (
                await asyncio.to_thread(
                    _restore_build_summary,
                    build_recorder.replay(summary_handle),
                    bundle=bundle,
                    expected_digest=str(summary_handle.output_digest or ""),
                    project_root=project_root,
                )
                if summary_handle.replayed
                else None
            )
            if restored_summary is not None:
                if restored_summary.package.has_slide_results:
                    build_package = restored_summary.package
                    artifact_uri, artifact_hash, artifact_digest = restored_summary.uri, restored_summary.content_hash, restored_summary.package.headline
                    artifact_type = spec.required_artifact_types[0]
                else:
                    # Older workflow rows may have committed a prose-only
                    # package before the result-evidence rule existed. Do not
                    # replay it into an empty, answerable deck.
                    no_slide_results = True
                    summary_refusal = "The Build finished, but it recorded no verified numeric outcomes or figures to present."
                    produced_usable_evidence = False
                    summary_handle = await build_recorder.reopen(summary_handle)
            else:
                if summary_handle.replayed:
                    # Committed, but unusable. Re-running is right; re-running
                    # invisibly is not, so the second synthesis gets its own row.
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
                build_package, summary_refusal, summary_question = summary.package, summary.refusal, summary.question
                if build_package is not None and not build_package.has_slide_results:
                    no_slide_results = True
                    summary_refusal = "The Build finished, but it recorded no verified numeric outcomes or figures to present."
                    build_package = None
                written = (
                    await asyncio.to_thread(
                        write_build_review,
                        project_root=project_root,
                        cycle=cycle,
                        package=build_package,
                        execution=bundle,
                    )
                    if build_package is not None
                    else None
                )
                if written is not None:
                    artifact_uri, artifact_hash, artifact_digest = written.uri, written.content_hash, written.digest
                    artifact_type = spec.required_artifact_types[0]
                    summary_payload = {"text": summary.text, "package_digest": build_package.digest, "artifact_uri": written.uri}
                else:
                    # The execution stays selected and reusable; only this step
                    # failed, and its code says so, so a person is not told to
                    # re-run an hour of sandbox work to recover a write-up.
                    produced_usable_evidence = False
        summary_control: dict[str, Any] | None = None
        if build_workflow_enabled and summary_handle.recorded:
            if artifact_hash and build_summary_owns_evidence:
                await build_recorder.succeed(summary_handle, artifact_hash, execution={"artifact_uri": artifact_uri or ""}, payload=summary_payload)
            elif summary_question:
                # `needs_input` is deliberately not a failure code: it renders
                # as **Waiting for you**, must not count against worker failure
                # telemetry, and the execution behind it stays selected — so the
                # answer resumes the write-up instead of the Build.
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
                await build_recorder.settle(
                    summary_handle,
                    state=StepState.NEEDS_INPUT,
                    summary=summary_question,
                    human_input_request_id=str(summary_control.get("request_id") or ""),
                )
            elif no_slide_results and not degraded_evidence_enabled:
                summary_control = await control_gate.raise_control(
                    no_presentable_results_request(
                        cycle_id=cycle_id,
                        stage_attempt_id=str((attempt or {}).get("id") or ""),
                        workflow_spec_key=build_recorder.spec_key,
                        cycle_revision=int(cycle.get("db_revision") or 0),
                        plan_digest=str(getattr(build_plan, "digest", "") or ""),
                        completed_phases=(phase_run.completed_count if phase_run is not None else 0),
                        plan=build_plan,
                    )
                )
                await build_recorder.settle(
                    summary_handle,
                    state=StepState.NEEDS_INPUT,
                    summary=summary_refusal,
                    human_input_request_id=str(summary_control.get("request_id") or ""),
                )
            else:
                await build_recorder.fail(
                    summary_handle,
                    BuildErrorCode.SUMMARY_CONTRACT_REJECTED if build_package is None else BuildErrorCode.REVIEW_PACKAGE_WRITE_FAILED,
                    summary_refusal or "; ".join(_failure_reasons(results)) or "No Build worker returned a result that satisfied the stage contract.",
                )

        evidence_exception: EvidenceExceptionDossier | None = None
        evidence_exception_uri = ""
        evidence_exception_hash = ""
        test_exception_reasons, test_exception_checks = invalidated_test_exception_facts(test_assessment) if stage == "test" else ((), ())
        if degraded_evidence_enabled and stage in {"build", "test"} and (not produced_usable_evidence or bool(test_exception_reasons) or isinstance(upstream_evidence_exception, Mapping)) and not summary_question:
            reasons: list[EvidenceReason] = []
            failed_checks: list[dict[str, object]] = []
            affected_deliverables: list[dict[str, object]] = []
            verified_facts: list[str] = []
            available_artifacts = [
                {
                    "path": str(item.get("path") or item.get("source_path") or ""),
                    "content_hash": str(item.get("content_hash") or ""),
                }
                for item in published_build_artifacts
                if item.get("content_hash")
            ]
            if isinstance(upstream_evidence_exception, Mapping):
                for value in upstream_evidence_exception.get("reason_codes", []):
                    try:
                        reasons.append(EvidenceReason(value))
                    except ValueError:
                        continue
                upstream_hash = str(upstream_evidence_exception.get("content_hash") or "")
                if upstream_hash:
                    verified_facts.append(f"The Test decision is bound to upstream Build exception {upstream_hash}.")
                    available_artifacts.append({"path": "upstream_build_evidence_exception", "content_hash": upstream_hash})
            reasons.extend(test_exception_reasons)
            failed_checks.extend(test_exception_checks)
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
            if deliverable_audit_refusal:
                reasons.append(EvidenceReason.AUDIT_INCOMPLETE)
                failed_checks.append({"check": "deliverable_audit", "status": "failed", "detail": deliverable_audit_refusal})
            if stage == "test" and test_assessment is None:
                reasons.append(EvidenceReason.AUDIT_INCOMPLETE)
                failed_checks.append({"check": "validity_pack", "status": "missing", "detail": stage_refusal})
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
                continuation_route=(
                    "advance_to_learn" if stage == "test" and isinstance(upstream_evidence_exception, Mapping) and str(dict((test_assessment or {}).get("evaluation") or {}).get("outcome") or "") in {"supported", "not_supported"} else None
                ),
            )
            if evidence_exception is not None:
                evidence_exception_uri, evidence_exception_hash, exception_digest = await asyncio.to_thread(
                    _write_evidence_exception_package,
                    project_root=project_root,
                    cycle=cycle,
                    dossier=evidence_exception,
                )
                # With a complete typed Test assessment the validity report is
                # still the core review evidence. The dossier sits beside it
                # and supplies the permanent red flag. Only a stage with no
                # trustworthy review package uses the dossier as its sole
                # review artifact.
                if not produced_usable_evidence:
                    artifact_uri = evidence_exception_uri
                    artifact_hash = evidence_exception_hash
                    artifact_digest = exception_digest
                    artifact_type = "evidence_exception"

        if stage_activity is not None:
            await stage_activity.update(state=ActivityState.RECORDING, operation="stage.record")
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
        if evidence_exception is not None and produced_usable_evidence and evidence_exception_uri and evidence_exception_hash:
            current = await self._repo.get_cycle(cycle_id, project_id=project_id)
            if current is None:  # pragma: no cover - scope was verified above
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
                required_limitations=(list(assessment.get("limitations") or []) if assessment.get("evidence_exception_hash") else []),
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

        if stage == "build" and artifact_uri and artifact_hash and build_execution_record is not None:
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
            if phase_run is not None:
                # The phase loop already bound each phase's inputs against the
                # state that phase actually started from. Recomputing here would
                # judge every phase against the pre-run snapshot alone, which by
                # construction cannot contain an earlier phase's output.
                input_artifacts = list(phase_run.input_artifacts)
            else:
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
                rerun_spec=(build_execution_record.rerun_spec.as_dict() if build_execution_record and build_execution_record.rerun_spec else None),
                input_artifacts=input_artifacts,
                output_artifacts=published_build_artifacts,
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
        deck_registered = False
        registration_error: Exception | None = None
        # Build, Test, and Learn each get a *pre-meeting* decision surface,
        # rendered from the stage's own evidence rather than from a chair result
        # — no meeting has happened when it is written. None of them carries a
        # route menu: each stage's verdict is taken at review time against this
        # evidence (Test's outcome is computed there from the validity pack),
        # and a menu rendered beforehand would pre-empt the decision it exists
        # to record. Design is the exception in the other direction: its deck
        # *is* a chair result, so it needs one to exist.
        stage_has_reviewable_evidence = stage in REVIEW_MEETING_STAGES and (produced_usable_evidence or evidence_exception is not None) and bool(artifact_uri and artifact_hash)
        review_meeting_requirement = None
        if (stage == "design" and chair_has_presentable_outcome) or stage_has_reviewable_evidence:
            transition_gate = None
            if stage_has_reviewable_evidence:
                if evidence_exception is not None:
                    exception_payload = evidence_exception.as_dict()
                    if stage == "test" and str(dict((test_assessment or {}).get("evaluation") or {}).get("outcome") or "") == "invalidated":
                        exception_payload = {
                            **exception_payload,
                            "scientific_effect": "invalidates_support",
                        }
                    transition_gate = {
                        "stage": stage,
                        "assessment": {
                            "difficulty": "exception",
                            "rationale": "The server could not establish the clean evidence contract.",
                        },
                        "routes": [],
                        "evidence_exception": exception_payload,
                    }
                    review_meeting_requirement = MeetingRequirement.SKIPPED.value
                elif bool(getattr(getattr(self._app_config, "dbtl", None), "progressive_gate", False)):
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
                routes = compute_stage_routes(
                    RouteContext(
                        stage="design",
                        outcome="approved",
                    )
                )
                transition_gate = {
                    "stage": "design",
                    "assessment": assessment.as_dict(),
                    "routes": [route.as_dict() for route in routes],
                }
            if stage == "test" and isinstance(test_assessment, Mapping):
                evaluation = dict(test_assessment.get("evaluation") or {})
                labels = {
                    "advance_to_learn": "Accept outcome and advance to Learn",
                    "learn_from_invalidated_evidence": "Learn from invalid evidence",
                    "repeat_test": "Repeat Test",
                    "return_to_build": "Return to Build",
                    "return_to_design": "Return to Design",
                    "close_cycle": "Close this cycle",
                }
                test_routes = [{"slug": route, "label": labels[route], "blocked": False} for route in labels if route in {str(item) for item in evaluation.get("allowed_recommendations", [])}]
                transition_gate = {
                    **(
                        transition_gate
                        or {
                            "stage": "test",
                            "assessment": {
                                "difficulty": "standard",
                                "rationale": "The server computed the Test outcome from the pinned validity pack.",
                            },
                        }
                    ),
                    "routes": test_routes,
                }
            if stage == "learn" and isinstance(build_test, Mapping):
                upstream_assessment = build_test.get("validity_assessment")
                upstream_assessment = dict(upstream_assessment) if isinstance(upstream_assessment, Mapping) else {}
                exception_hash = str(upstream_assessment.get("evidence_exception_hash") or "").strip()
                if exception_hash:
                    invalidated_exception = upstream_assessment.get("recommendation") == "learn_from_invalidated_evidence"
                    transition_gate = {
                        **(
                            transition_gate
                            or {
                                "stage": "learn",
                                "assessment": {
                                    "difficulty": "exception",
                                    "rationale": (
                                        "Learn is operating on invalidated evidence and may record process lessons only."
                                        if invalidated_exception
                                        else "Learn must preserve the upstream evidence exception as a limitation on every candidate."
                                    ),
                                },
                                "routes": [],
                            }
                        ),
                        "evidence_exception": {
                            "condition": "untrusted" if invalidated_exception else "degraded_verified",
                            "scientific_effect": "invalidates_support" if invalidated_exception else "limits_scope",
                            "content_hash": exception_hash,
                        },
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
            if evidence_exception is not None and not produced_usable_evidence:
                deck = await asyncio.to_thread(
                    _write_evidence_exception_deck,
                    project_root=project_root,
                    cycle=cycle,
                    dossier=evidence_exception,
                    package_path=artifact_uri or "",
                    surface_id=(surface_plan.surface_id if surface_plan is not None and surface_plan.answerable else ""),
                    transition_gate=transition_gate or {},
                )
            elif build_package is not None:
                # Build gets its own deck: figures embedded, numbers first. The
                # meeting deck renders positions and a synthesis, which is the
                # wrong shape for a result nobody argued about.
                rendered = await asyncio.to_thread(
                    write_build_deck,
                    project_root=project_root,
                    cycle=cycle,
                    package=build_package,
                    package_path=artifact_uri or "",
                    surface_id=(surface_plan.surface_id if surface_plan is not None and surface_plan.answerable else ""),
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
                    clarification_question=clarification_question or "",
                    decision_request=(chair_result.decision_request if chair_result is not None else None),
                    surface_id=(surface_plan.surface_id if surface_plan is not None and surface_plan.answerable else ""),
                    surface_mode=(surface_plan.mode if surface_plan is not None else ""),
                    transition_gate=transition_gate,
                )
            if deck is not None:
                deck_uri = deck.uri
                if surface_plan is not None:
                    try:
                        await self._register_feedback_surface(
                            surface_plan,
                            deck,
                            cycle_id=cycle_id,
                            project_id=project_id,
                        )
                        deck_registered = True
                    except Exception as exc:  # noqa: BLE001 - recorded below, then re-raised
                        registration_error = exc
        if build_workflow_enabled and artifact_uri and artifact_hash and evidence_exception is None:
            # Opened here rather than around the render call: with the summary
            # missing there is nothing to render, and an attempt whose
            # predecessor never succeeded would be refused by the chain anyway.
            deck_handle = await build_recorder.begin(BuildStepKey.RENDER_REVIEW_DECK)
            if deck is None or not deck.content_hash:
                await build_recorder.fail(
                    deck_handle,
                    BuildErrorCode.DECK_RENDER_FAILED,
                    "The Build review deck could not be rendered from the recorded review package.",
                )
            elif not deck_registered:
                # **A deck nobody can answer is not a review surface.** The step
                # used to succeed on a rendered file alone, so a registration
                # that returned no plan left the workflow reporting a finished
                # Build whose deck could never carry a verdict — and one that
                # raised did so *before* this step opened, so the code that
                # names this failure could never be recorded at all.
                await build_recorder.fail(
                    deck_handle,
                    BuildErrorCode.DECK_REGISTRATION_FAILED,
                    (str(registration_error) if registration_error is not None else "The Build review deck was rendered but could not be bound to a stage attempt, so it cannot carry a decision."),
                )
            else:
                await build_recorder.succeed(deck_handle, deck.content_hash, execution={"deck_uri": deck.uri})
        if registration_error is not None:
            # Fail-visible, and now recorded first. Stage evidence is durable by
            # this point, so a retry is safe; returning success would hand
            # somebody a deck that can never answer its gate.
            raise registration_error

        # The Test deck is the human input surface, but it can bind a decision
        # only after the complete typed validity pack reaches
        # ``awaiting_review``. Move the stage across that non-decision boundary
        # here so the deck's Human gate can record the server-computed outcome;
        # a person can never decide against a half-written pack.
        if stage == "test" and (produced_usable_evidence or evidence_exception is not None) and artifact_uri and artifact_hash:
            submitter = getattr(self._repo, "submit_stage_for_review", None)
            if callable(submitter):
                current = await self._repo.get_cycle(cycle_id, project_id=project_id)
                if current is None:  # pragma: no cover - scope was verified above
                    raise RuntimeError("Cycle disappeared before Test could enter human review.")
                await submitter(
                    cycle_id=cycle_id,
                    project_id=project_id,
                    stage="test",
                    expected_db_revision=int(current["db_revision"]),
                    actor_user_id=str(user_id),
                    idempotency_key=f"{execution_key}:test-auto-submit",
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
        elif build_plan_incomplete and phase_run is not None:
            # The generic "none produced usable evidence" line is false here and
            # sends the reader to the wrong place: the phases that ran did
            # produce evidence, it is published and pinned, and the plan simply
            # did not finish. Saying how far it got is what makes the next step
            # obvious — resume, or fix the phase that stopped it.
            done = sum(1 for item in phase_run.outcome.results if item.is_trustworthy)
            note = "\n".join(
                [
                    f"Ran {done} of {len(build_plan.phases) if build_plan else done} planned build phase(s) and kept every finished phase's outputs, so a retry resumes rather than starting over.",
                    f"It stopped there: {phase_run.stopped_because}" if phase_run.stopped_because else "",
                    "No review package was written, because a plan that has not finished is not the build a person would be approving.",
                ]
            ).strip()
        elif build_plan_incomplete and stage_refusal:
            # Every worker succeeded and the stage still refused. The generic
            # line below would say "none produced usable evidence" and then
            # print an empty reason list — a blank explanation, which is worse
            # than a wrong one, because it sends the reader to inspect three
            # workers that did nothing wrong.
            note = "\n".join(
                [
                    f"Ran {len(results)} bounded {stage} worker(s) and kept every outcome, but the stage could not be completed.",
                    "",
                    stage_refusal,
                ]
            )
        elif artifact_uri:
            # The digest carries what the council concluded. A reply that is only
            # a file path makes the reader open a file to learn anything at all.
            note = artifact_digest or f"Ran {len(results)} bounded {stage} worker(s) and attached a review package at {artifact_uri}."
        elif stage_refusal:
            note = "\n".join(
                [
                    f"Ran {len(results)} bounded {stage} worker(s) and recorded every outcome, but the stage could not create review evidence.",
                    "",
                    stage_refusal,
                ]
            )
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
        # A Build that stopped is a decision waiting to be made, not a dead end.
        # The control is raised last, from state already committed: every phase
        # that ran is recorded, no worker lease is held, and the person's answer
        # starts a new attempt rather than resuming a process.
        # A question the summarizer asked outranks a pause card derived from
        # where the Build stopped: it is already recorded and already bound to
        # the step waiting on it, and returning the other one would leave a
        # control nobody can see holding an answer nobody can give.
        control_request = (
            summary_control
            or await self._build_pause_control(
                phase_run,
                gate=control_gate,
                cycle=cycle,
                plan=build_plan,
                attempt=attempt,
                workflow_spec_key=build_recorder.spec_key,
                summary_refusal=summary_refusal if build_workflow_enabled else "",
            )
            if build_workflow_enabled
            else None
        )

        if stage_activity is not None:
            if clarification_question or control_request is not None:
                await stage_activity.settle(ActivityState.PAUSED, operation="stage.wait_human")
            elif build_plan_incomplete or not produced_usable_evidence:
                await stage_activity.settle(ActivityState.FAILED)

        _summary_chair = _chair_result_of(results) or {}
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
            control_request=control_request,
            # Inputs for the one sentence that introduces this round in chat.
            # Taken from the same chair result the deck is rendered from, so the
            # reply and the deck can never describe different meetings.
            research_question=str(cycle.get("research_question") or ""),
            chair_summary=str(_summary_chair.get("summary") or ""),
            chair_consensus=_summary_chair.get("consensus"),
        )
