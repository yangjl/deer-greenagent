"""Recording one Build run as a chain of durable step attempts.

The chain is the point. Each step's identity is its predecessors' outputs plus
server-owned material, so a presentational failure — a summary that will not
parse, a deck that will not render — records a failure against *that* step and
leaves the sandbox work before it selected and reusable. Before this, the whole
Build was one opaque unit and an hour of execution was thrown away to recover a
rendering bug.

Two rules keep the recorder safe to switch on:

**Recording failure stops governed progression.** Once the rollout switch is
on, this chain is not optional instrumentation: it is what proves every selected
phase succeeded before the review gate is offered. If the repository or replay
store refuses a write, the current run stops visibly. Worker files already
written remain on disk and a later retry may recover them, but no evidence or
review surface may be attached without the durable chain.

**A step that cannot be replayed is re-opened, not silently re-run.** When the
payload behind a committed digest cannot be produced, the work has to happen
again — and `reopen` appends a fresh attempt for it, because a re-run settled
against nothing leaves the record describing a result that no longer exists
while every later step is still computed from its digest.

**A replayed step does not re-run.** `open_step_attempt` returns a committed
success against the same input digest instead of opening a new attempt, so a
retried turn resumes the chain rather than spending the work again — and
`succeed` keeps that step's own output beside the digest, because a caller that
cannot rebuild the result has no choice but to dispatch again. A recorded
`replayed=True` with nothing to hand back is the shape that let the whole
mechanism report a resume while re-running an hour of sandbox work.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from deerflow.agents.dbtl.live_stage.step_store import StepOutputStore
from deerflow.dbtl.build_workflow import (
    BuildErrorCode,
    BuildStepKey,
    BuildWorkflowSpec,
    StepState,
    input_digest,
    phase_step_material,
    resolve_build_workflow,
)

logger = logging.getLogger(__name__)


class BuildStepRecordingError(RuntimeError):
    """The enabled Build workflow could not persist its authoritative chain."""


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
    #: Exactly what `begin` opened this attempt against, kept so `reopen` can
    #: open a second attempt at the *same* identity — and put the chain cursor
    #: back where it was — without the caller having to hand it all in again.
    predecessor_digests: tuple[str, ...] = ()
    predecessor_ids: tuple[str, ...] = ()
    open_overrides: Mapping[str, Any] = field(default_factory=dict)

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
        store: StepOutputStore | None = None,
    ) -> None:
        self._repo = repo
        self._store = store
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

    def _raise_recording_error(self, step: BuildStepKey, exc: BaseException) -> None:
        logger.error(
            "Could not record Build workflow step %s for stage attempt %s; governed Build progression stopped.",
            step.value,
            self._stage_attempt_id,
            exc_info=exc,
        )
        self._enabled = False
        raise BuildStepRecordingError(
            f"Build paused because its durable {step.value!r} step could not be recorded. No review evidence was attached; retry after persistence is available."
        ) from exc

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
            # A phase's identity includes which phase it is, the plan it sat in,
            # and who was selected to do it: a phase attempt whose plan changed
            # is a different phase, and one a newly registered specialist would
            # now cover is not a retry of the generalist's run.
            material.update(
                phase_step_material(
                    phase_key=phase_key,
                    plan_digest=str(overrides.get("plan_digest") or ""),
                    capability=str(overrides.get("capability") or ""),
                    agent_name=str(overrides.get("agent_name") or ""),
                    via_generalist=bool(overrides.get("via_generalist")),
                )
            )
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
        except Exception as exc:  # noqa: BLE001 - translated to a bounded workflow refusal
            self._raise_recording_error(step, exc)
        bindings = {
            "predecessor_digests": tuple(predecessors_digests),
            "predecessor_ids": tuple(predecessor_ids),
            "open_overrides": dict(overrides),
        }
        if not dispatched:
            # Already committed against this exact material. Advance the chain
            # from the recorded row rather than opening a second attempt.
            self._advance(str(payload["id"]), str(payload.get("output_digest") or ""), phase=bool(phase_key))
            return StepHandle(step=step, input_digest=digest, replayed=True, output_digest=str(payload.get("output_digest") or ""), phase_key=phase_key, **bindings)
        return StepHandle(step=step, step_run_id=str(payload["id"]), input_digest=digest, phase_key=phase_key, **bindings)

    async def reopen(self, handle: StepHandle) -> StepHandle:
        """Open a fresh attempt at a step whose committed output cannot be used.

        A replay that cannot produce its payload has to run again, and this is
        what stops that re-run from being invisible. Without it the work
        happened, `succeed` no-opped because the handle said "replayed", and
        every later step stayed computed from a digest describing a result
        nobody could read — the record and reality disagreeing in the one place
        the chain exists to keep them together.

        The chain cursor is put back to what `begin` bound this attempt against
        first, so a re-run that then *fails* leaves the chain where the failure
        left it rather than descending from an output this run rejected.
        """
        if not handle.replayed or not self._enabled or self._repo is None:
            return handle
        self._rewind(handle)
        try:
            payload, _dispatched = await self._repo.open_step_attempt(
                project_id=self._project_id,
                cycle_id=self._cycle_id,
                stage_attempt_id=self._stage_attempt_id,
                workflow_spec_key=self._spec.spec_key,
                step_key=handle.step.value,
                input_digest=handle.input_digest,
                predecessor_step_run_ids=list(handle.predecessor_ids),
                parent_run_id=self._parent_run_id or None,
                force_new_attempt=True,
                **dict(handle.open_overrides),
            )
        except Exception as exc:  # noqa: BLE001 - translated to a bounded workflow refusal
            self._raise_recording_error(handle.step, exc)
        return StepHandle(step=handle.step, step_run_id=str(payload["id"]), input_digest=handle.input_digest, phase_key=handle.phase_key)

    def _rewind(self, handle: StepHandle) -> None:
        """Undo the advance a replay performed, back to what `begin` saw."""
        if handle.is_phase:
            self._phase_outputs = handle.predecessor_digests
            self._phase_ids = list(handle.predecessor_ids)
            return
        self._previous_outputs = handle.predecessor_digests
        self._previous_ids = list(handle.predecessor_ids)

    def _advance(self, step_run_id: str, output_digest: str, *, phase: bool = False) -> None:
        if phase:
            self._phase_outputs = (output_digest,)
            self._phase_ids = [step_run_id]
            return
        self._previous_outputs = (output_digest,)
        self._previous_ids = [step_run_id]

    def replay(self, handle: StepHandle) -> Any | None:
        """The output of a step that was already committed, or `None`.

        `None` means "dispatch": either this is not a replay, or the payload
        behind the digest cannot be produced. A caller must never treat a bare
        `replayed=True` as permission to skip work — that reports a resume and
        performs a re-run, which is worse than either.
        """
        if not handle.replayed or self._store is None or not handle.output_digest:
            return None
        return self._store.load(handle.step, handle.output_digest)

    async def succeed(self, handle: StepHandle, output_digest: str, *, execution: dict[str, Any] | None = None, payload: Any | None = None) -> None:
        """Settle an attempt as succeeded and hand its output to the next step.

        `payload` is what a later replay hands back in place of re-running.
        It is written **before** the row is settled: a payload with no committed
        row is unreachable and harmless, while a committed row whose payload
        never landed is a step that reports a replay and silently dispatches.
        """
        if handle.replayed:
            return
        if not handle.recorded or self._repo is None or not self._enabled:
            return
        try:
            if payload is not None and self._store is not None:
                self._store.save(handle.step, output_digest, payload)
            await self._repo.settle_step_attempt(
                step_run_id=str(handle.step_run_id),
                project_id=self._project_id,
                status=StepState.SUCCEEDED.value,
                output_digest=output_digest,
                execution=execution,
            )
        except Exception as exc:  # noqa: BLE001 - translated to a bounded workflow refusal
            self._raise_recording_error(handle.step, exc)
        self._advance(str(handle.step_run_id), output_digest, phase=handle.is_phase)

    async def settle(
        self,
        handle: StepHandle,
        *,
        state: StepState,
        code: BuildErrorCode | None = None,
        summary: str = "",
        execution: dict[str, Any] | None = None,
        human_input_request_id: str = "",
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
                # A paused step names the control holding its question, so a
                # reader of the record can find the exchange rather than only
                # the sentence.
                human_input_request_id=human_input_request_id or None,
                execution=execution,
            )
        except Exception as exc:  # noqa: BLE001 - translated to a bounded workflow refusal
            self._raise_recording_error(handle.step, exc)

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
    #: Where committed step outputs are kept. Empty disables replay: the chain
    #: still records, and every step dispatches.
    project_root: str = ""


async def make_build_step_recorder(repo: _StepRepository | None, request: RecorderRequest) -> BuildStepRecorder:
    """Build a recorder, loading the material the writer and reader must share.

    Reading the material through the repository rather than recomputing it is
    the whole safety property: the read model computes expected digests from the
    same query, so a step opened here cannot be reported stale the instant it
    succeeds.
    """
    spec = request.spec or resolve_build_workflow()
    if not request.enabled:
        return DISABLED_RECORDER
    if repo is None or not (request.project_id and request.cycle_id and request.stage_attempt_id):
        raise BuildStepRecordingError(
            "Build paused because its durable workflow could not be initialized. No worker was dispatched; retry after persistence is available."
        )
    try:
        material = await repo.build_step_material_for(
            project_id=request.project_id,
            stage_attempt_id=request.stage_attempt_id,
            workflow_spec_key=spec.spec_key,
        )
    except Exception as exc:  # noqa: BLE001 - translated to a bounded workflow refusal
        logger.error("Could not read Build workflow step material for stage attempt %s; governed Build progression stopped.", request.stage_attempt_id, exc_info=True)
        raise BuildStepRecordingError(
            "Build paused because its durable workflow could not be initialized. No worker was dispatched; retry after persistence is available."
        ) from exc
    store = StepOutputStore(project_root=request.project_root, stage_attempt_id=request.stage_attempt_id) if request.project_root else None
    return BuildStepRecorder(
        repo,
        enabled=True,
        project_id=request.project_id,
        cycle_id=request.cycle_id,
        stage_attempt_id=request.stage_attempt_id,
        parent_run_id=request.parent_run_id,
        spec=spec,
        material=material,
        store=store if store is not None and store.available else None,
    )
