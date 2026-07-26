"""Live DBTL Design/Reconciliation execution over ``SubagentExecutor``.

The adapter is deliberately narrower than the lead agent: one selected,
project-owned cycle, one currently active Phase 6 stage, one bounded fan-out,
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig

from deerflow.authz.principal import normalize_authz_attributes
from deerflow.dbtl.agent_selector import AgentCandidate, build_candidates
from deerflow.dbtl.cycle_state import StageStatus, stage_for_state
from deerflow.dbtl.stage_runner import (
    AsyncWorkerDispatcher,
    DispatchOutcome,
    StageExecutionOutcome,
    StageExecutionPlan,
    WorkUnit,
    arun_stage,
    collect_results,
)
from deerflow.dbtl.stage_spec import WorkerBudget, resolve_stage_spec
from deerflow.dbtl.worker_result import WorkerStatus
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

    @property
    def satisfies_gate(self) -> bool:
        """Always false: only a typed human review can satisfy a gate."""
        return False


CandidateProvider = Callable[[], Sequence[AgentCandidate]]


def _runtime_view(config: RunnableConfig) -> dict[str, Any]:
    merged = dict(config.get("configurable", {}) or {})
    context = config.get("context", {}) or {}
    if isinstance(context, dict):
        merged.update(context)
    return merged


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
            "Return one JSON object and nothing else:",
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
        ]
    )
    return WorkUnit(
        unit_id=f"{attempt_id}-chair",
        capability="design_council_chair",
        agent_name=first.agent_name,
        prompt=prompt,
        via_generalist=first.via_generalist,
    )


def _design_red_team_unit(
    outcome: StageExecutionOutcome,
    *,
    attempt_id: str,
) -> WorkUnit | None:
    """Guarantee an adversarial second position when only one specialist exists."""
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
    )


def _write_stage_package(
    *,
    project_root: str,
    cycle: dict[str, Any],
    outcome: StageExecutionOutcome,
    idempotency_key: str,
) -> tuple[str, str]:
    """Atomically write the human-visible evidence package and return URI/hash."""
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
    encoded = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    content_hash = hashlib.sha256(encoded).hexdigest()
    relative = Path("dbtl") / _safe_token(str(cycle["id"])) / outcome.plan.spec.stage / f"stage-run-{_safe_token(idempotency_key)}-{content_hash[:12]}.json"
    destination = project_outputs_dir(root) / relative
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
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
        temp_path = None
    finally:
        if temp_path is not None:
            Path(temp_path).unlink(missing_ok=True)

    uri = f"/mnt/user-data/outputs/{relative.as_posix()}"
    return uri, content_hash


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
    ) -> None:
        self._repo = repo
        self._app_config = app_config
        self._candidate_provider = candidate_provider
        self._dispatcher = dispatcher
        self._runtime_config = runtime_config

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
            worker_config = replace(
                base_config,
                max_turns=min(base_config.max_turns, budget.max_turns),
                timeout_seconds=min(
                    base_config.timeout_seconds,
                    budget.timeout_seconds,
                ),
            )
            parent_model = metadata.get("model_name")
            effective_model = resolve_subagent_model_name(
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
            )
            await emit(
                {
                    "type": "task_started",
                    "task_id": unit.unit_id,
                    "description": f"DBTL {unit.capability.replace('_', ' ')}",
                    "model_name": effective_model,
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
                    }
                )
                raise

            if result.status is SubagentStatus.COMPLETED:
                await emit(
                    {
                        "type": "task_completed",
                        "task_id": unit.unit_id,
                        "result": result.result or "",
                        "stop_reason": result.stop_reason,
                    }
                )
                return DispatchOutcome(
                    unit_id=unit.unit_id,
                    text=result.result,
                    stop_reason=result.stop_reason,
                )

            error = result.error or f"Subagent ended with status {result.status.value}."
            await emit(
                {
                    "type": "task_failed",
                    "task_id": unit.unit_id,
                    "error": error,
                    "stop_reason": result.stop_reason,
                }
            )
            return DispatchOutcome(
                unit_id=unit.unit_id,
                text=result.result,
                stop_reason=result.stop_reason,
                error=error,
            )

        return await asyncio.gather(*(run_one(unit) for unit in units))

    async def execute(
        self,
        *,
        project_id: str | None,
        cycle_id: str | None,
        request_text: str,
        state: dict[str, Any],
        config: RunnableConfig,
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
        if stage not in {"design", "reconciliation", "build", "test"}:
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
        reconciliation = await self._repo.reconciliation_view(cycle_id, project_id=project_id) if stage in {"reconciliation", "build", "test"} else None
        build_test = await self._repo.build_test_view(cycle_id, project_id=project_id) if stage in {"build", "test"} else None
        prior_design_runs = (
            await self._repo.list_worker_runs(
                cycle_id,
                project_id=project_id,
                stage="design",
            )
            if stage == "design"
            else []
        )
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
                "prior_design_council_runs": prior_design_runs[-12:],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        spec = resolve_stage_spec(stage)
        attempt_id = f"dbtl-{_safe_token(execution_key)}"
        dispatcher = self._dispatcher or self._production_dispatcher(
            config=config,
            state=state,
            project_id=project_id,
            project_root=project_root,
        )
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
            if len(outcome.plan.units) < 2:
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
        design_ready = stage != "design" or (chair_result is not None and chair_result.is_trustworthy and chair_result.status is WorkerStatus.COMPLETED)
        produced_usable_evidence = outcome.produced_usable_evidence and design_ready
        if produced_usable_evidence:
            artifact_uri, artifact_hash = await asyncio.to_thread(
                _write_stage_package,
                project_root=project_root,
                cycle=cycle,
                outcome=outcome,
                idempotency_key=execution_key,
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
            note = f"Ran {len(results)} bounded {stage} worker(s) and recorded their structured results. A review package was attached at {artifact_uri}. The stage remains open until a person submits and reviews it."
        else:
            note = f"Ran {len(results)} bounded {stage} worker(s) and recorded every outcome, but none produced usable evidence, so no review artifact was attached."
        return LiveStageResult(
            stage=stage,
            cycle_id=cycle_id,
            note=note,
            worker_count=len(results),
            produced_usable_evidence=produced_usable_evidence,
            artifact_uri=artifact_uri,
            clarification_question=clarification_question,
        )
