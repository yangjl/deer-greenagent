"""The versioned ``StageSpec`` registry (Phase 6).

A stage is *data*, not code. The design states it directly — "every stage or
bridge node is versioned data" — and the version is the load-bearing part: a
human approves a stage attempt that ran under one contract, and if the contract
later changes, that approval no longer describes what the system would do now.
Phase 6's no-go list turns that into a requirement ("invalidate approval when
reconciled inputs or relevant policy changes"), which is only enforceable if
each attempt records the exact spec version it ran under. Hence
:attr:`StageSpec.spec_key` and :func:`resolve_stage_spec`, which always returns
a pinned version rather than "the current one".

Two structural guarantees live here rather than in prose:

* ``human_gate_policy.allows_agent_approval`` is a constant ``False`` property.
  The design permits reconsidering that "only in a later, separately reviewed
  policy change", so there is deliberately no field an operator could flip.
* The Data Readiness bridge has its own spec but maps onto the Design-to-Build
  transition rather than becoming a fifth DBTL phase, matching
  ``deerflow.dbtl.cycle_state``'s five-stage order.

The registry holds no storage and imports nothing that can persist anything, so
resolving a spec cannot be a write.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.cycle_state import STAGE_ORDER, CycleClass


class CycleWeight(StrEnum):
    """How much process a cycle carries.

    A retroactive cycle documents work that already happened, so it cannot
    demand that agents produce the artifacts; a light cycle skips fan-out but
    keeps the gates. The weight never removes a human gate — it changes how much
    is expected before one.
    """

    FULL = "full"
    LIGHT = "light"
    RETROACTIVE = "retroactive"


class MemoryReadPolicy(StrEnum):
    """Which memory scope a stage's workers may read.

    Mirrors the scopes Phase 2 built. ``PROJECT_SCOPED`` is the private-then-
    shared bucket chain a project run already uses; ``NONE`` exists for stages
    that must reason only from the evidence in front of them.
    """

    NONE = "none"
    PROJECT_SCOPED = "project_scoped"


class MemoryWritePolicy(StrEnum):
    """What a stage's workers may leave behind in memory.

    There is no "write directly" option. Phase 8 owns promotion, and the whole
    point of that separation is that a stage worker cannot manufacture durable
    knowledge as a side effect of doing its job.
    """

    NONE = "none"
    CANDIDATE_ONLY = "candidate_only"


class TransitionPolicy(StrEnum):
    """What it takes to leave a stage.

    Only one value today. It is an enum rather than a bare constant so a future
    policy change is a visible addition here — reviewable on its own — instead
    of an ad-hoc branch somewhere in the graph.
    """

    HUMAN_REVIEW_REQUIRED = "human_review_required"


@dataclass(frozen=True, slots=True)
class WorkerBudget:
    """The ceiling a stage places on one worker.

    Budgets are part of the spec rather than the call site because the reviewer
    of a stage attempt needs to know what the worker was allowed to spend, and a
    caller-supplied budget would not be reproducible from the recorded version.
    """

    max_workers: int = 3
    max_turns: int = 40
    max_tokens: int = 400_000
    timeout_seconds: int = 900

    def __post_init__(self) -> None:
        for name in ("max_workers", "max_turns", "max_tokens", "timeout_seconds"):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"WorkerBudget.{name} must be a positive integer, got {value!r}.")


@dataclass(frozen=True, slots=True)
class HumanGatePolicy:
    """Who may close a stage's gate, and on what.

    ``required_reviewer_roles`` is checked against the reviewer's *current*
    project membership at review time, not against a role copied into a request
    — Phase 1 established that reviewer identity is server-owned, and this
    policy is the thing that identity is checked against.
    """

    required_reviewer_roles: tuple[str, ...] = ("owner", "admin", "member")
    rationale_required: bool = True

    @property
    def allows_agent_approval(self) -> bool:
        """Always ``False``.

        A property, not a field, so "human reviewers approve all gates" is a
        property of the type. The design allows revisiting this only through a
        separately reviewed policy change, which would show up here as a real
        code change rather than a configuration value nobody reviewed.
        """
        return False


@dataclass(frozen=True, slots=True)
class StageSpec:
    """One versioned stage or bridge contract."""

    stage: str
    domain_profile: str
    version: int
    title: str
    purpose: str
    cycle_classes: tuple[CycleClass, ...]
    cycle_weights: tuple[CycleWeight, ...]
    required_inputs: tuple[str, ...]
    required_artifact_types: tuple[str, ...]
    output_schema: str
    required_capabilities: tuple[Capability, ...]
    optional_capabilities: tuple[Capability, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    memory_read_policy: MemoryReadPolicy = MemoryReadPolicy.PROJECT_SCOPED
    memory_write_policy: MemoryWritePolicy = MemoryWritePolicy.NONE
    validity_gates: tuple[str, ...] = ()
    transition_policy: TransitionPolicy = TransitionPolicy.HUMAN_REVIEW_REQUIRED
    human_gate_policy: HumanGatePolicy = field(default_factory=HumanGatePolicy)
    budget: WorkerBudget = field(default_factory=WorkerBudget)

    def __post_init__(self) -> None:
        if self.stage not in STAGE_ORDER:
            raise ValueError(f"StageSpec.stage must be one of {STAGE_ORDER}, got {self.stage!r}.")
        if self.version < 1:
            raise ValueError(f"StageSpec.version must be >= 1, got {self.version}.")
        if not self.required_capabilities:
            raise ValueError(f"StageSpec {self.stage!r} declares no required capabilities.")
        if not self.required_artifact_types:
            raise ValueError(f"StageSpec {self.stage!r} declares no required artifact types.")
        overlap = set(self.required_capabilities) & set(self.optional_capabilities)
        if overlap:
            names = ", ".join(sorted(item.value for item in overlap))
            raise ValueError(f"StageSpec {self.stage!r} lists {names} as both required and optional.")

    @property
    def spec_key(self) -> str:
        """The stable identifier an attempt records.

        Everything needed to fetch this exact contract back: stage, domain
        profile, and version. A recorded attempt that cannot name its contract
        cannot have its approval invalidated when the contract moves.
        """
        return f"{self.domain_profile}:{self.stage}:v{self.version}"

    def applies_to(self, cycle_class: CycleClass, weight: CycleWeight) -> bool:
        return cycle_class in self.cycle_classes and weight in self.cycle_weights


# ---------------------------------------------------------------------------
# The shipped specs.
#
# ``generic`` is the fallback profile every project resolves against when it has
# no domain pack. Domain packs (maize genomic selection first) add their own
# profile and are selected per cycle; the human exit review for Phase 6 chooses
# the first one, so shipping a speculative maize pack here would pre-empt a
# decision the plan explicitly reserves for that review.
# ---------------------------------------------------------------------------

GENERIC_PROFILE = "generic"

_ALL_CLASSES: tuple[CycleClass, ...] = tuple(CycleClass)
_ALL_WEIGHTS: tuple[CycleWeight, ...] = tuple(CycleWeight)


DESIGN_SPEC_V1 = StageSpec(
    stage="design",
    domain_profile=GENERIC_PROFILE,
    version=1,
    title="Design",
    purpose="State the question, the population, and what would count as success or rejection before any data is touched.",
    cycle_classes=_ALL_CLASSES,
    cycle_weights=_ALL_WEIGHTS,
    required_inputs=("research_question", "objective", "success_criteria"),
    required_artifact_types=("design_brief",),
    output_schema="design_brief.v1",
    required_capabilities=(Capability.EXPERIMENTAL_DESIGN,),
    optional_capabilities=(
        Capability.BREEDING_STRATEGY,
        Capability.QUANTITATIVE_GENETICS,
        Capability.LITERATURE_REVIEW,
        Capability.STATISTICAL_ANALYSIS,
    ),
    # "Design may not advance until success and rejection criteria are
    # operational, not merely descriptive" — the gate that makes the rest of the
    # cycle checkable, so it is named rather than left to reviewer judgement.
    validity_gates=("operational_success_criteria", "stated_rejection_criteria"),
    memory_write_policy=MemoryWritePolicy.NONE,
)

DESIGN_SPEC_V2 = StageSpec(
    stage="design",
    domain_profile=GENERIC_PROFILE,
    version=2,
    title="Design council",
    purpose=("Ground the research design in the project workspace, compare independent specialist positions, and synthesize an operational design or one focused human clarification before any data is touched."),
    cycle_classes=_ALL_CLASSES,
    cycle_weights=_ALL_WEIGHTS,
    required_inputs=(
        "project_workspace_manifest",
        "research_question",
        "objective",
        "success_criteria",
        "human_clarifications",
    ),
    required_artifact_types=("design_brief",),
    output_schema="design_brief.v2",
    required_capabilities=(Capability.EXPERIMENTAL_DESIGN,),
    optional_capabilities=(
        Capability.BREEDING_STRATEGY,
        Capability.QUANTITATIVE_GENETICS,
        Capability.LITERATURE_REVIEW,
        Capability.STATISTICAL_ANALYSIS,
    ),
    validity_gates=("operational_success_criteria", "stated_rejection_criteria"),
    memory_write_policy=MemoryWritePolicy.NONE,
)


RECONCILIATION_SPEC_V1 = StageSpec(
    stage="reconciliation",
    domain_profile=GENERIC_PROFILE,
    version=1,
    title="Data readiness and reconciliation",
    purpose="Reconcile every declared input against the design before Build may use it, and stop visibly when sources contradict each other.",
    cycle_classes=_ALL_CLASSES,
    cycle_weights=_ALL_WEIGHTS,
    required_inputs=("approved_design_brief", "declared_data_sources"),
    required_artifact_types=("reconciliation_report",),
    output_schema="reconciliation_report.v1",
    required_capabilities=(Capability.DATA_RECONCILIATION,),
    optional_capabilities=(
        Capability.GERMPLASM_AND_PEDIGREE,
        Capability.FIELD_TRIAL_QC,
        Capability.STATISTICAL_ANALYSIS,
        Capability.QUANTITATIVE_GENETICS,
    ),
    validity_gates=(
        "all_required_rows_resolved",
        "raw_data_immutable",
        "dataset_hashes_bound",
    ),
    memory_write_policy=MemoryWritePolicy.NONE,
)

BUILD_SPEC_V1 = StageSpec(
    stage="build",
    domain_profile=GENERIC_PROFILE,
    version=1,
    title="Build",
    purpose="Produce a rerunnable implementation from the approved design and reconciled, immutable inputs.",
    cycle_classes=_ALL_CLASSES,
    cycle_weights=_ALL_WEIGHTS,
    required_inputs=(
        "approved_design_brief",
        "approved_reconciliation_report",
        "bound_dataset_fingerprint",
    ),
    required_artifact_types=("build_package",),
    output_schema="build_package.v1",
    required_capabilities=(Capability.SOFTWARE_ENGINEERING,),
    optional_capabilities=(
        Capability.STATISTICAL_ANALYSIS,
        Capability.QUANTITATIVE_GENETICS,
        Capability.FIELD_TRIAL_QC,
    ),
    validity_gates=(
        "reconciled_input_lineage",
        "immutable_raw_inputs",
        "versioned_derived_outputs",
        "reproducible_execution",
    ),
    memory_write_policy=MemoryWritePolicy.NONE,
)


TEST_SPEC_V1 = StageSpec(
    stage="test",
    domain_profile=GENERIC_PROFILE,
    version=1,
    title="Test",
    purpose="Assess scientific validity separately from headline performance and route failed evidence visibly.",
    cycle_classes=_ALL_CLASSES,
    cycle_weights=_ALL_WEIGHTS,
    required_inputs=("approved_build_package", "build_lineage"),
    required_artifact_types=("validity_report",),
    output_schema="validity_report.v1",
    required_capabilities=(Capability.VALIDITY_ASSESSMENT,),
    optional_capabilities=(
        Capability.STATISTICAL_ANALYSIS,
        Capability.QUANTITATIVE_GENETICS,
        Capability.FIELD_TRIAL_QC,
    ),
    validity_gates=("generic-predictive:v1",),
    memory_write_policy=MemoryWritePolicy.NONE,
)


_REGISTRY: dict[str, StageSpec] = {
    spec.spec_key: spec
    for spec in (
        DESIGN_SPEC_V1,
        DESIGN_SPEC_V2,
        RECONCILIATION_SPEC_V1,
        BUILD_SPEC_V1,
        TEST_SPEC_V1,
    )
}

# The version a new attempt gets, per (domain_profile, stage). Separate from the
# registry so an older version stays resolvable forever: an attempt approved
# under v1 must still be able to name what it ran under after v2 ships.
_CURRENT: MappingProxyType[tuple[str, str], int] = MappingProxyType(
    {
        (GENERIC_PROFILE, "design"): DESIGN_SPEC_V2.version,
        (GENERIC_PROFILE, "reconciliation"): RECONCILIATION_SPEC_V1.version,
        (GENERIC_PROFILE, "build"): BUILD_SPEC_V1.version,
        (GENERIC_PROFILE, "test"): TEST_SPEC_V1.version,
    }
)

#: Stages through Phase 7. Learn remains unavailable until its promotion and
#: publication semantics ship in Phase 8.
EXECUTABLE_STAGES: tuple[str, ...] = ("design", "reconciliation", "build", "test")


class StageSpecNotFound(LookupError):
    """No spec exists for the requested stage, profile, or version."""


def resolve_stage_spec(
    stage: str,
    *,
    domain_profile: str = GENERIC_PROFILE,
    version: int | None = None,
) -> StageSpec:
    """Return a pinned :class:`StageSpec`.

    ``version=None`` means "whatever is current for this profile and stage", and
    the returned spec still carries its concrete version — callers record
    :attr:`StageSpec.spec_key`, never the word "current", so an attempt's
    contract stays retrievable after the current version moves on.
    """
    normalized = (stage or "").strip().lower()
    profile = (domain_profile or GENERIC_PROFILE).strip().lower()
    if normalized not in EXECUTABLE_STAGES:
        raise StageSpecNotFound(f"Stage {stage!r} has no executable spec; available stages: {', '.join(EXECUTABLE_STAGES)}.")

    resolved_version = version if version is not None else _CURRENT.get((profile, normalized))
    if resolved_version is None:
        raise StageSpecNotFound(f"No stage spec registered for profile {profile!r}, stage {normalized!r}.")

    key = f"{profile}:{normalized}:v{resolved_version}"
    spec = _REGISTRY.get(key)
    if spec is None:
        raise StageSpecNotFound(f"No stage spec registered under {key!r}.")
    return spec


def resolve_spec_by_key(spec_key: str) -> StageSpec:
    """Fetch back the exact contract an attempt recorded."""
    spec = _REGISTRY.get(spec_key)
    if spec is None:
        raise StageSpecNotFound(f"No stage spec registered under {spec_key!r}.")
    return spec


def registered_spec_keys() -> tuple[str, ...]:
    """Every spec key, sorted — for readiness reporting and tests."""
    return tuple(sorted(_REGISTRY))


def current_spec_keys(domain_profile: str = GENERIC_PROFILE) -> tuple[str, ...]:
    """The spec keys a new cycle in *domain_profile* would run under."""
    profile = (domain_profile or GENERIC_PROFILE).strip().lower()
    return tuple(resolve_stage_spec(stage, domain_profile=profile).spec_key for stage in EXECUTABLE_STAGES if (profile, stage) in _CURRENT)


def specs_for_cycle(cycle_class: CycleClass, weight: CycleWeight, *, domain_profile: str = GENERIC_PROFILE) -> tuple[StageSpec, ...]:
    """The executable specs that apply to one cycle, in stage order."""
    resolved: list[StageSpec] = []
    for stage in EXECUTABLE_STAGES:
        try:
            spec = resolve_stage_spec(stage, domain_profile=domain_profile)
        except StageSpecNotFound:
            continue
        if spec.applies_to(cycle_class, weight):
            resolved.append(spec)
    return tuple(resolved)


def parse_cycle_weight(value: str | None) -> CycleWeight:
    """Coerce a stored or requested weight, defaulting to ``full``."""
    if value is None or value == "":
        return CycleWeight.FULL
    try:
        return CycleWeight(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in CycleWeight)
        raise ValueError(f"Unknown cycle weight {value!r}; expected one of: {allowed}") from exc


def describe_specs(specs: Iterable[StageSpec]) -> tuple[dict[str, object], ...]:
    """A JSON-safe projection, for readiness reports and the stage detail API."""
    return tuple(
        {
            "spec_key": spec.spec_key,
            "stage": spec.stage,
            "domain_profile": spec.domain_profile,
            "version": spec.version,
            "title": spec.title,
            "purpose": spec.purpose,
            "required_inputs": list(spec.required_inputs),
            "required_artifact_types": list(spec.required_artifact_types),
            "output_schema": spec.output_schema,
            "required_capabilities": [item.value for item in spec.required_capabilities],
            "optional_capabilities": [item.value for item in spec.optional_capabilities],
            "validity_gates": list(spec.validity_gates),
            "transition_policy": spec.transition_policy.value,
            "allows_agent_approval": spec.human_gate_policy.allows_agent_approval,
            "budget": {
                "max_workers": spec.budget.max_workers,
                "max_turns": spec.budget.max_turns,
                "max_tokens": spec.budget.max_tokens,
                "timeout_seconds": spec.budget.timeout_seconds,
            },
        }
        for spec in specs
    )
