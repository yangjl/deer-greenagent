"""The Build workflow: five versioned steps and the digest chain between them.

Build executes today as one opaque `WorkUnit`. When the *final* structured
answer fails to parse — or the deck fails to render — scientifically complete
sandbox work is discarded and the whole thing runs again. That is the failure
this module exists to make impossible.

**The spec is fixed; the plan is data.** The five step keys never vary: a
deployment always reads the Design, plans, executes, summarizes, and renders.
What varies is the `BuildPhasePlan` that `plan_build` produces, and phases are
therefore auditable exactly the way steps are — a reviewer can ask which plan an
attempt ran under, and a changed plan invalidates the phases beneath it rather
than silently rebinding them.

**The digest chain turns a guarantee into a mechanism.** "A presentational
failure must never invalidate prior sandbox work" is otherwise a matter of care;
here it falls out of the arithmetic. Every step's identity is computed from
server-owned material plus its selected predecessors' outputs, so:

* an upstream change invalidates **all and only** its descendants;
* a successful predecessor is never rerun because a later step failed; and
* an invalidated success is *reported*, not deleted — the record of what was
  attempted is the point.

This module owns no storage and imports nothing that can persist anything, so
computing a projection can never be a write.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

#: A plan longer than this is a project, not a Build. A planner that wants more
#: says so as an open question rather than emitting one.
MAX_BUILD_PHASES = 8


class BuildStepKey(StrEnum):
    """Stable identifiers. Order is the spec's, not this enum's."""

    LOAD_DESIGN = "load_design"
    PLAN_BUILD = "plan_build"
    EXECUTE_PHASES = "execute_phases"
    SUMMARIZE_RESULTS = "summarize_results"
    RENDER_REVIEW_DECK = "render_review_deck"


class ExecutorKind(StrEnum):
    """What runs a step, which is what decides how expensive a retry is."""

    #: Server code. No model, no sandbox — cheap and safely re-runnable.
    DETERMINISTIC = "deterministic"
    #: One bounded model-backed worker.
    WORKER = "worker"
    #: Expands into the recorded plan's phases, each its own attempt.
    PHASE_CONTAINER = "phase_container"
    #: Deterministic projection of an already-validated package. It has no
    #: sentence of its own, so it cannot quietly improve a result.
    RENDERER = "renderer"


class StepState(StrEnum):
    """A closed vocabulary. `waiting` is a UI projection, not a state."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    INVALIDATED = "invalidated"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """`needs_input` is terminal and immutable.

        It records the exact question, releases worker/runtime ownership, and
        leaves the stage safely paused. A human answer creates a **new** attempt
        linked to the paused one; it never mutates this attempt back to running.
        """
        return self in _TERMINAL_STATES

    @property
    def is_reusable(self) -> bool:
        """Only a success can be selected as a predecessor's output."""
        return self is StepState.SUCCEEDED


_TERMINAL_STATES = frozenset(
    {
        StepState.SUCCEEDED,
        StepState.NEEDS_INPUT,
        StepState.FAILED,
        StepState.INVALIDATED,
        StepState.CANCELLED,
    }
)


class BuildErrorCode(StrEnum):
    """Bounded, server-owned failure reasons.

    Two absences are deliberate. `needs_input` is not here: it renders as
    **Waiting for you**, carries its own collaboration request, and must not
    count against worker failure telemetry. And there is no code for "the result
    looked wrong" — a check the work did not pass is a recorded result that
    travels to Test as evidence and to the reviewer as something to weigh. No
    code here should be able to stop a Build on a judgement that belongs to a
    person.
    """

    DESIGN_MISSING_OR_STALE = "design_missing_or_stale"
    DESIGN_UNREADABLE = "design_unreadable"
    PLAN_CONTRACT_REJECTED = "plan_contract_rejected"
    PLAN_CAPABILITY_UNKNOWN = "plan_capability_unknown"
    PLAN_NOT_FEASIBLE = "plan_not_feasible"
    SANDBOX_SETUP_FAILED = "sandbox_setup_failed"
    SANDBOX_EXECUTION_FAILED = "sandbox_execution_failed"
    WORKER_TIMED_OUT = "worker_timed_out"
    EXECUTION_CONTRACT_REJECTED = "execution_contract_rejected"
    EXECUTION_OUTPUT_MISSING = "execution_output_missing"
    INPUT_CHANGED_DURING_EXECUTION = "input_changed_during_execution"
    SUMMARY_CONTRACT_REJECTED = "summary_contract_rejected"
    REVIEW_PACKAGE_WRITE_FAILED = "review_package_write_failed"
    DECK_RENDER_FAILED = "deck_render_failed"
    DECK_REGISTRATION_FAILED = "deck_registration_failed"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"

    @property
    def is_presentational(self) -> bool:
        """Did this failure leave the science intact?

        The UI has to be able to say "the build ran; the write-up broke", so a
        person is not told to re-run an hour of sandbox work to recover a
        rendering bug.
        """
        return self in _PRESENTATIONAL_CODES


_PRESENTATIONAL_CODES = frozenset(
    {
        BuildErrorCode.SUMMARY_CONTRACT_REJECTED,
        BuildErrorCode.REVIEW_PACKAGE_WRITE_FAILED,
        BuildErrorCode.DECK_RENDER_FAILED,
        BuildErrorCode.DECK_REGISTRATION_FAILED,
    }
)


@dataclass(frozen=True, slots=True)
class BuildStepSpec:
    """One step's contract: what it is, who runs it, and how it retries."""

    key: BuildStepKey
    #: Shown to a person. Server-owned, because a client string table would
    #: eventually describe work that did not happen.
    label: str
    executor: ExecutorKind
    #: May a person retry this step alone, reusing valid predecessors?
    retryable: bool = True


@dataclass(frozen=True, slots=True)
class BuildWorkflowSpec:
    """A pinned workflow version. Attempts record `spec_key`, never "current"."""

    domain_profile: str
    version: int
    steps: tuple[BuildStepSpec, ...]

    @property
    def spec_key(self) -> str:
        return f"{self.domain_profile}:build-workflow:v{self.version}"

    @property
    def step_order(self) -> tuple[BuildStepKey, ...]:
        return tuple(step.key for step in self.steps)

    def step(self, key: BuildStepKey) -> BuildStepSpec:
        for spec in self.steps:
            if spec.key is key:
                return spec
        raise LookupError(f"{key!r} is not a step of {self.spec_key}.")


BUILD_WORKFLOW_V1 = BuildWorkflowSpec(
    domain_profile="generic",
    version=1,
    steps=(
        BuildStepSpec(key=BuildStepKey.LOAD_DESIGN, label="Read approved Design", executor=ExecutorKind.DETERMINISTIC),
        BuildStepSpec(key=BuildStepKey.PLAN_BUILD, label="Plan the build", executor=ExecutorKind.WORKER),
        BuildStepSpec(key=BuildStepKey.EXECUTE_PHASES, label="Run the build", executor=ExecutorKind.PHASE_CONTAINER),
        BuildStepSpec(key=BuildStepKey.SUMMARIZE_RESULTS, label="Summarize results", executor=ExecutorKind.WORKER),
        BuildStepSpec(key=BuildStepKey.RENDER_REVIEW_DECK, label="Prepare review slide deck", executor=ExecutorKind.RENDERER),
    ),
)

_REGISTRY: Mapping[str, BuildWorkflowSpec] = MappingProxyType({BUILD_WORKFLOW_V1.spec_key: BUILD_WORKFLOW_V1})

_CURRENT: Mapping[str, BuildWorkflowSpec] = MappingProxyType({"generic": BUILD_WORKFLOW_V1})


def resolve_build_workflow(domain_profile: str = "generic") -> BuildWorkflowSpec:
    """The current workflow for a profile, always returned as a pinned version."""
    return _CURRENT.get(domain_profile) or BUILD_WORKFLOW_V1


def resolve_build_workflow_by_key(spec_key: str) -> BuildWorkflowSpec:
    """Reconstruct the exact workflow an attempt ran under."""
    spec = _REGISTRY.get(spec_key)
    if spec is None:
        raise LookupError(f"No Build workflow registered under {spec_key!r}.")
    return spec


def registered_build_workflow_keys() -> tuple[str, ...]:
    return tuple(_REGISTRY)


#: Contract versions pinned to this workflow version.
#:
#: They belong in the digest chain because they change what a step *means*
#: without changing anything upstream of it: a new result contract makes an
#: existing summary stale even though the execution behind it is untouched.
CONTRACT_VERSIONS: Mapping[BuildStepKey, str] = MappingProxyType(
    {
        BuildStepKey.PLAN_BUILD: "planner:v1",
        BuildStepKey.SUMMARIZE_RESULTS: "result:v1",
        BuildStepKey.RENDER_REVIEW_DECK: "renderer:v1",
    }
)


def build_step_material(
    *,
    workflow_spec_key: str,
    design_artifact_id: str | None,
    design_content_hash: str | None,
    design_review_id: str | None,
    policy_version: str | None,
    stage_spec_key: str | None,
    dataset_fingerprint: str | None,
) -> dict[BuildStepKey, dict[str, str]]:
    """The server-owned material each step's identity is computed from.

    One function, called by whoever opens an attempt *and* by whoever reads the
    projection. Two implementations would drift, and the symptom would be the
    read model reporting perfectly good work as stale — or, worse, reporting
    stale work as current.

    `load_design` binds the approved Design and the policy/contract it was
    approved under; the later steps bind only their own contract version,
    because everything else they depend on arrives through their predecessors'
    output digests.

    **The cycle's `db_revision` is deliberately not here.** It bumps on every
    mutation, including recording this very Build's own workers, so a chain
    keyed on it would invalidate itself the instant it committed anything —
    every step reading as stale the moment the first one succeeded. The
    approved dataset fingerprint is the value that actually answers "did the
    material change?", and it moves only when the data does.
    """
    return {
        BuildStepKey.LOAD_DESIGN: {
            "workflow": workflow_spec_key,
            "design_artifact_id": design_artifact_id or "",
            "design_content_hash": design_content_hash or "",
            "design_review_id": design_review_id or "",
            "policy_version": policy_version or "",
            "stage_spec_key": stage_spec_key or "",
            "dataset_fingerprint": dataset_fingerprint or "",
        },
        BuildStepKey.PLAN_BUILD: {"contract": CONTRACT_VERSIONS[BuildStepKey.PLAN_BUILD]},
        BuildStepKey.EXECUTE_PHASES: {},
        BuildStepKey.SUMMARIZE_RESULTS: {"contract": CONTRACT_VERSIONS[BuildStepKey.SUMMARIZE_RESULTS]},
        BuildStepKey.RENDER_REVIEW_DECK: {"contract": CONTRACT_VERSIONS[BuildStepKey.RENDER_REVIEW_DECK]},
    }


def _digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def input_digest(
    step: BuildStepKey,
    *,
    predecessors: Sequence[str] = (),
    material: Mapping[str, object] | None = None,
) -> str:
    """This step's identity, from server-owned material and its predecessors.

    The step itself is part of the identity: two steps fed identical material
    are still different steps. Predecessor **order** is significant — phase 2
    reading phase 1's output is not the same work as the reverse — while
    `material` is a mapping and so order-independent by construction.
    """
    return _digest(
        {
            "step": step.value,
            "predecessors": list(predecessors),
            "material": dict(material or {}),
        }
    )


def phase_bundle_digest(phase_output_digests: Sequence[str]) -> str:
    """The ordered digests of every successful phase, as one value.

    `execute_phases` is a container: what it hands downstream is the sequence of
    its phases' outputs, and the sequence is what a summarizer read.
    """
    return _digest({"phases": list(phase_output_digests)})


@dataclass(frozen=True, slots=True)
class StepAttempt:
    """One immutable attempt at one step.

    Attempts are append-only. A retry appends; it never overwrites the failed
    record, and multiple terminal attempts are retained.
    """

    step: BuildStepKey
    attempt: int
    state: StepState
    input_digest: str
    output_digest: str | None = None
    #: Set only for a phase of `execute_phases`. A phase attempt whose plan
    #: changed is a different phase, not a retry of this one — hence
    #: `plan_digest` here rather than only on the container.
    phase_index: int | None = None
    phase_key: str | None = None
    plan_digest: str | None = None
    #: What the phase asked for, and who actually covered it. A reviewer reading
    #: "quantitative genetics: general-purpose" knows what they are looking at;
    #: a reviewer reading nothing does not.
    capability: str | None = None
    agent_name: str | None = None
    via_generalist: bool = False
    error_code: BuildErrorCode | None = None
    error_summary: str = ""

    @property
    def is_phase(self) -> bool:
        return self.phase_index is not None


@dataclass(frozen=True, slots=True)
class WorkflowProjection:
    """What is still valid, what is stale, and where work resumes."""

    spec: BuildWorkflowSpec
    selected: Mapping[BuildStepKey, StepAttempt | None]
    invalidated: Mapping[BuildStepKey, tuple[StepAttempt, ...]]
    expected_inputs: Mapping[BuildStepKey, str]
    next_step: BuildStepKey | None
    all_attempts: tuple[StepAttempt, ...] = field(default=())

    @property
    def is_complete(self) -> bool:
        return self.next_step is None

    def phases_for(self, plan_digest: str) -> tuple[StepAttempt, ...]:
        """Phase attempts belonging to one plan, in recorded order.

        Scoped by plan because a replan produces a different account of the
        work; carrying old phase outputs into it would produce evidence nobody
        planned.
        """
        return tuple(sorted((attempt for attempt in self.all_attempts if attempt.is_phase and attempt.plan_digest == plan_digest), key=lambda a: (a.phase_index or 0, a.attempt)))


def project_workflow(
    attempts: Sequence[StepAttempt],
    *,
    spec: BuildWorkflowSpec | None = None,
    expected_inputs: Mapping[BuildStepKey, str] | None = None,
    step_material: Mapping[BuildStepKey, Mapping[str, object]] | None = None,
) -> WorkflowProjection:
    """Walk the steps in order, selecting the newest success that is still valid.

    The expected input digest of a step depends on the *selected* output of the
    one before it, so this cannot be a filter — it is a walk. A step with no
    valid success stops the walk and becomes `next_step`; everything after it is
    simply unreached, which is different from being invalidated.

    `expected_inputs` lets a caller state the digests directly (what the server
    computes from live material); otherwise they are derived from
    `step_material`, which is what a test or a clean first run does.
    """
    workflow = spec or BUILD_WORKFLOW_V1
    material = step_material or {}
    container: list[StepAttempt] = []
    phases: list[StepAttempt] = []
    for attempt in attempts:
        (phases if attempt.is_phase else container).append(attempt)

    selected: dict[BuildStepKey, StepAttempt | None] = {}
    invalidated: dict[BuildStepKey, tuple[StepAttempt, ...]] = {}
    resolved_inputs: dict[BuildStepKey, str] = {}
    next_step: BuildStepKey | None = None
    previous_outputs: tuple[str, ...] = ()

    for step in workflow.step_order:
        expected = (expected_inputs or {}).get(step) or input_digest(step, predecessors=previous_outputs, material=material.get(step, {}))
        resolved_inputs[step] = expected

        for_step = [attempt for attempt in container if attempt.step is step]
        successes = [attempt for attempt in for_step if attempt.state.is_reusable]
        # A success recorded against a different input digest described work on
        # material that has since changed. It is reported rather than deleted.
        stale = tuple(attempt for attempt in successes if attempt.input_digest != expected)
        if stale:
            invalidated[step] = stale

        valid = [attempt for attempt in successes if attempt.input_digest == expected]
        winner = max(valid, key=lambda a: a.attempt) if valid else None
        selected[step] = winner

        if winner is None:
            # The walk stops here: every later step's identity depends on an
            # output that does not exist yet, so nothing beyond this point can
            # be judged valid or stale.
            if next_step is None:
                next_step = step
            for later in workflow.step_order[workflow.step_order.index(step) + 1 :]:
                selected.setdefault(later, None)
                later_successes = tuple(attempt for attempt in container if attempt.step is later and attempt.state.is_reusable)
                if later_successes:
                    invalidated[later] = later_successes
                resolved_inputs.setdefault(later, "")
            break

        previous_outputs = (winner.output_digest or "",)

    return WorkflowProjection(
        spec=workflow,
        selected=MappingProxyType(selected),
        invalidated=MappingProxyType(invalidated),
        expected_inputs=MappingProxyType(resolved_inputs),
        next_step=next_step,
        all_attempts=tuple(attempts),
    )
