"""Recording one Build run as a chain of durable step attempts.

The chain is the point. Each step's identity is its predecessors' outputs plus
server-owned material, so a presentational failure — a summary that will not
parse, a deck that will not render — records a failure against *that* step and
leaves the sandbox work before it selected and reusable. Before this, the whole
Build was one opaque unit and an hour of execution was thrown away to recover a
rendering bug.

Two rules keep the recorder safe to switch on:

**Recording never fails a Build.** This is instrumentation attached to a
governance record, behind a rollout switch. If the repository refuses a write,
the recorder logs, disables itself for the rest of the run, and the Build carries
on — a half-recorded chain is a gap in a read model, while a raised exception is
a lost experiment. What this does *not* soften is the `load_design` gate: that
runs whether or not the recorder does, and its refusal stops the dispatch.

**A replayed step does not re-run.** `open_step_attempt` returns a committed
success against the same input digest instead of opening a new attempt, so a
retried turn resumes the chain rather than spending the work again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from deerflow.dbtl.build_workflow import (
    BuildErrorCode,
    BuildStepKey,
    BuildWorkflowSpec,
    StepState,
    input_digest,
    resolve_build_workflow,
)

logger = logging.getLogger(__name__)


class _StepRepository(Protocol):
    """The slice of `DbtlCycleRepository` this recorder needs."""

    async def build_step_material_for(self, *, project_id: str, stage_attempt_id: str, workflow_spec_key: str) -> dict[str, dict[str, str]]: ...

    async def open_step_attempt(self, **kwargs: Any) -> tuple[dict[str, Any], bool]: ...

    async def settle_step_attempt(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class StepHandle:
    """One open (or replayed, or never-recorded) attempt.

    `step_run_id` is `None` for both the disabled recorder and a replay, so a
    caller settles a handle unconditionally and the recorder decides whether
    that means a database write.
    """

    step: BuildStepKey
    step_run_id: str | None = None
    input_digest: str = ""
    replayed: bool = False
    output_digest: str | None = None
    #: Set for a phase of `execute_phases`. Phases run on their own chain,
    #: beneath the container, so a phase must not advance the step chain the
    #: container's own identity is computed from.
    phase_key: str = ""

    @property
    def recorded(self) -> bool:
        return self.step_run_id is not None

    @property
    def is_phase(self) -> bool:
        return bool(self.phase_key)


class BuildStepRecorder:
    """Walks the Build workflow's steps in order, recording each attempt."""

    def __init__(
        self,
        repo: _StepRepository | None,
        *,
        enabled: bool,
        project_id: str = "",
        cycle_id: str = "",
        stage_attempt_id: str = "",
        parent_run_id: str = "",
        spec: BuildWorkflowSpec | None = None,
        material: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._repo = repo
        self._project_id = project_id
        self._cycle_id = cycle_id
        self._stage_attempt_id = stage_attempt_id
        self._parent_run_id = parent_run_id
        self._spec = spec or resolve_build_workflow()
        self._material = material or {}
        self._enabled = bool(enabled and repo is not None and project_id and cycle_id and stage_attempt_id)
        #: The chain: the selected output of the step before this one, and the
        #: row it came from. A step's identity is computed from these.
        self._previous_outputs: tuple[str, ...] = ()
        self._previous_ids: list[str] = []
        #: Phases run on their own chain beneath `execute_phases`: phase N binds
        #: phase N-1's output, while the container still binds the plan. Sharing
        #: one cursor made the container chain from the last phase, so its own
        #: expected digest never matched and a finished Build read as stale.
        self._phase_outputs: tuple[str, ...] | None = None
        self._phase_ids: list[str] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def spec_key(self) -> str:
        return self._spec.spec_key

    def _disable(self, step: BuildStepKey, exc: BaseException) -> None:
        logger.warning(
            "Could not record Build workflow step %s for stage attempt %s; the Build continues without a step record.",
            step.value,
            self._stage_attempt_id,
            exc_info=exc,
        )
        self._enabled = False

    async def begin(self, step: BuildStepKey, **overrides: Any) -> StepHandle:
        """Open an attempt at `step`, or replay the one already committed.

        Passing `phase_key` puts the attempt on the phase chain: it binds the
        phase before it rather than the step before it, and settling it leaves
        the step chain where the plan left it.
        """
        phase_key = str(overrides.get("phase_key") or "")
        if not self._enabled or self._repo is None:
            return StepHandle(step=step, phase_key=phase_key)
        if phase_key and self._phase_outputs is None:
            # Seeded from the plan, which is what a phase actually depends on.
            self._phase_outputs, self._phase_ids = self._previous_outputs, list(self._previous_ids)
        predecessors_digests = (self._phase_outputs or ()) if phase_key else self._previous_outputs
        predecessor_ids = self._phase_ids if phase_key else self._previous_ids
        material = dict(self._material.get(step.value, {}))
        if phase_key:
            # A phase's identity includes which phase it is and the plan it sat
            # in: a phase attempt whose plan changed is a different phase, not a
            # retry of this one.
            material.update({"phase_key": phase_key, "plan_digest": str(overrides.get("plan_digest") or "")})
        digest = input_digest(step, predecessors=predecessors_digests, material=material)
        try:
            payload, dispatched = await self._repo.open_step_attempt(
                project_id=self._project_id,
                cycle_id=self._cycle_id,
                stage_attempt_id=self._stage_attempt_id,
                workflow_spec_key=self._spec.spec_key,
                step_key=step.value,
                input_digest=digest,
                predecessor_step_run_ids=list(predecessor_ids),
                parent_run_id=self._parent_run_id or None,
                **overrides,
            )
        except Exception as exc:  # noqa: BLE001 - instrumentation must not fail a Build
            self._disable(step, exc)
            return StepHandle(step=step, input_digest=digest, phase_key=phase_key)
        if not dispatched:
            # Already committed against this exact material. Advance the chain
            # from the recorded row rather than opening a second attempt.
            self._advance(str(payload["id"]), str(payload.get("output_digest") or ""), phase=bool(phase_key))
            return StepHandle(step=step, input_digest=digest, replayed=True, output_digest=str(payload.get("output_digest") or ""), phase_key=phase_key)
        return StepHandle(step=step, step_run_id=str(payload["id"]), input_digest=digest, phase_key=phase_key)

    def _advance(self, step_run_id: str, output_digest: str, *, phase: bool = False) -> None:
        if phase:
            self._phase_outputs = (output_digest,)
            self._phase_ids = [step_run_id]
            return
        self._previous_outputs = (output_digest,)
        self._previous_ids = [step_run_id]

    async def succeed(self, handle: StepHandle, output_digest: str, *, execution: dict[str, Any] | None = None) -> None:
        """Settle an attempt as succeeded and hand its output to the next step."""
        if handle.replayed:
            return
        if not handle.recorded or self._repo is None or not self._enabled:
            return
        try:
            await self._repo.settle_step_attempt(
                step_run_id=str(handle.step_run_id),
                project_id=self._project_id,
                status=StepState.SUCCEEDED.value,
                output_digest=output_digest,
                execution=execution,
            )
        except Exception as exc:  # noqa: BLE001 - see the module docstring
            self._disable(handle.step, exc)
            return
        self._advance(str(handle.step_run_id), output_digest, phase=handle.is_phase)

    async def settle(
        self,
        handle: StepHandle,
        *,
        state: StepState,
        code: BuildErrorCode | None = None,
        summary: str = "",
        execution: dict[str, Any] | None = None,
    ) -> None:
        """Settle a non-success outcome; the chain deliberately does not advance.

        A failed, cancelled, or paused step produces no output for the next step
        to be computed from, so leaving the chain where it is *is* the record
        that work stopped here.
        """
        if handle.replayed or not handle.recorded or self._repo is None or not self._enabled:
            return
        try:
            await self._repo.settle_step_attempt(
                step_run_id=str(handle.step_run_id),
                project_id=self._project_id,
                status=state.value,
                error_code=code.value if code else None,
                error_summary=summary,
                execution=execution,
            )
        except Exception as exc:  # noqa: BLE001 - see the module docstring
            self._disable(handle.step, exc)

    async def fail(self, handle: StepHandle, code: BuildErrorCode, summary: str, *, execution: dict[str, Any] | None = None) -> None:
        await self.settle(handle, state=StepState.FAILED, code=code, summary=summary, execution=execution)


#: Every caller holds a recorder, so the disabled path is an object rather than a
#: `None` check repeated at each of the five steps — the check that gets missed
#: is always the one guarding the write.
DISABLED_RECORDER = BuildStepRecorder(None, enabled=False)


@dataclass(slots=True)
class RecorderRequest:
    """What a caller knows before it can ask for a recorder."""

    enabled: bool
    project_id: str
    cycle_id: str
    stage_attempt_id: str
    parent_run_id: str = ""
    spec: BuildWorkflowSpec | None = field(default=None)


async def make_build_step_recorder(repo: _StepRepository | None, request: RecorderRequest) -> BuildStepRecorder:
    """Build a recorder, loading the material the writer and reader must share.

    Reading the material through the repository rather than recomputing it is
    the whole safety property: the read model computes expected digests from the
    same query, so a step opened here cannot be reported stale the instant it
    succeeds.
    """
    spec = request.spec or resolve_build_workflow()
    if not request.enabled or repo is None or not (request.project_id and request.cycle_id and request.stage_attempt_id):
        return DISABLED_RECORDER
    try:
        material = await repo.build_step_material_for(
            project_id=request.project_id,
            stage_attempt_id=request.stage_attempt_id,
            workflow_spec_key=spec.spec_key,
        )
    except Exception:  # noqa: BLE001 - see the module docstring
        logger.warning("Could not read Build workflow step material for stage attempt %s; steps will not be recorded.", request.stage_attempt_id, exc_info=True)
        return DISABLED_RECORDER
    return BuildStepRecorder(
        repo,
        enabled=True,
        project_id=request.project_id,
        cycle_id=request.cycle_id,
        stage_attempt_id=request.stage_attempt_id,
        parent_run_id=request.parent_run_id,
        spec=spec,
        material=material,
    )
