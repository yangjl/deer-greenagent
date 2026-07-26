"""Choosing workers for a stage's work units (Phase 6).

The design's rule is that "each work unit declares capabilities, not a fixed
role name", so selection is a match between what a stage needs and what the
project's agents can do. Three things make this worth its own pure module:

**Selection is constrained, and refusal is a real outcome.** A stage that
requires data reconciliation and finds no agent that can do it must fail
visibly, not fall back to "someone will figure it out". :class:`SelectionResult`
therefore carries ``unmet_capabilities`` and a ``satisfied`` flag rather than
just a list of agents, and the dispatcher stops on it.

**Falling back to a generalist is allowed but never invisible.** In practice
most projects start with only the built-in ``general-purpose`` agent, and
refusing every stage until someone registers a quantitative geneticist would
make Phase 6 undemonstrable. So a generalist may cover a capability — and the
selection records that it did, in ``used_generalist_for``, which the stage
detail surfaces. A reviewer reading "quantitative genetics: general-purpose"
knows what they are looking at; a reviewer reading nothing does not.

**Nothing here executes anything.** Selection is a decision about who to ask,
computed from values, so it is testable without a model, a sandbox, or a
registry lookup.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_spec import StageSpec

#: The built-in agent every deployment has. Treated as a generalist rather than
#: given a capability list, because claiming it specialises in quantitative
#: genetics would be a lie the selection record then repeats to a reviewer.
GENERALIST_AGENT = "general-purpose"


class SelectionRefused(ValueError):
    """Selection cannot proceed at all (bad request, not an unmet capability)."""


@dataclass(frozen=True, slots=True)
class AgentCandidate:
    """One agent that could take a work unit."""

    name: str
    capabilities: frozenset[Capability] = frozenset()
    is_generalist: bool = False
    available: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise SelectionRefused("An agent candidate needs a name.")

    def covers(self, capability: Capability) -> bool:
        """Whether this agent can take work requiring *capability*.

        A generalist covers everything it was not told it cannot do. That is a
        deliberate over-claim in the *candidate*, corrected by recording the
        fallback in the result rather than by silently dropping the work.
        """
        if not self.available:
            return False
        return capability in self.capabilities or self.is_generalist


@dataclass(frozen=True, slots=True)
class Assignment:
    """One capability, and the agent that will exercise it."""

    capability: Capability
    agent_name: str
    via_generalist: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "capability": self.capability.value,
            "agent_name": self.agent_name,
            "via_generalist": self.via_generalist,
        }


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """Who was chosen, what could not be covered, and why."""

    assignments: tuple[Assignment, ...] = ()
    unmet_capabilities: tuple[Capability, ...] = ()
    used_generalist_for: tuple[Capability, ...] = ()
    dropped_for_budget: tuple[Capability, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def satisfied(self) -> bool:
        """Whether every *required* capability found a worker.

        Budget-dropped optional capabilities do not make a selection
        unsatisfied — they are the fan-out narrowing, which is what a budget is
        for. A dropped *required* capability is impossible by construction:
        :func:`select_agents` fills required capabilities first.
        """
        return not self.unmet_capabilities

    @property
    def agent_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.agent_name for item in self.assignments))

    def as_dict(self) -> dict[str, object]:
        return {
            "satisfied": self.satisfied,
            "assignments": [item.as_dict() for item in self.assignments],
            "unmet_capabilities": [item.value for item in self.unmet_capabilities],
            "used_generalist_for": [item.value for item in self.used_generalist_for],
            "dropped_for_budget": [item.value for item in self.dropped_for_budget],
            "notes": list(self.notes),
        }


def _pick(capability: Capability, candidates: Sequence[AgentCandidate]) -> AgentCandidate | None:
    """The best candidate for one capability.

    Specialists beat generalists, and among equals the first declared wins so
    selection is deterministic — a stage attempt that cannot be reproduced from
    its record is not evidence of anything.
    """
    specialists = [item for item in candidates if item.available and capability in item.capabilities]
    if specialists:
        return specialists[0]
    generalists = [item for item in candidates if item.available and item.is_generalist]
    return generalists[0] if generalists else None


def select_agents(spec: StageSpec, candidates: Sequence[AgentCandidate]) -> SelectionResult:
    """Match a stage's capabilities against the available agents.

    Required capabilities are filled first and are never dropped for budget;
    optional ones fill the remaining worker slots in declared order. The budget
    comes from the spec (``spec.budget.max_workers``) rather than the call site,
    so what a stage was allowed to spend is reconstructable from its recorded
    contract.
    """
    if not candidates:
        return SelectionResult(
            unmet_capabilities=tuple(spec.required_capabilities),
            notes=("No agents are registered for this project, so no stage work can be dispatched.",),
        )

    max_workers = spec.budget.max_workers
    assignments: list[Assignment] = []
    unmet: list[Capability] = []
    generalist_used: list[Capability] = []
    dropped: list[Capability] = []

    for capability in spec.required_capabilities:
        chosen = _pick(capability, candidates)
        if chosen is None:
            unmet.append(capability)
            continue
        via_generalist = capability not in chosen.capabilities
        assignments.append(Assignment(capability=capability, agent_name=chosen.name, via_generalist=via_generalist))
        if via_generalist:
            generalist_used.append(capability)

    for capability in spec.optional_capabilities:
        if len(assignments) >= max_workers:
            dropped.append(capability)
            continue
        # An optional capability is only worth a worker slot if someone actually
        # specialises in it. Spending the budget on a generalist restating the
        # required worker's answer adds cost and no independent evidence.
        specialists = [item for item in candidates if item.available and capability in item.capabilities]
        if not specialists:
            continue
        assignments.append(Assignment(capability=capability, agent_name=specialists[0].name, via_generalist=False))

    notes: list[str] = []
    if unmet:
        names = ", ".join(item.value for item in unmet)
        notes.append(f"No available agent can cover: {names}.")
    if generalist_used:
        names = ", ".join(item.value for item in generalist_used)
        notes.append(f"Covered by a general-purpose agent rather than a specialist: {names}.")
    if dropped:
        names = ", ".join(item.value for item in dropped)
        notes.append(f"Not dispatched — the stage's worker budget of {max_workers} was already spent: {names}.")

    return SelectionResult(
        assignments=tuple(assignments),
        unmet_capabilities=tuple(unmet),
        used_generalist_for=tuple(generalist_used),
        dropped_for_budget=tuple(dropped),
        notes=tuple(notes),
    )


def build_candidates(
    agent_names: Iterable[str],
    *,
    declared_capabilities: Mapping[str, Sequence[Capability]] | None = None,
    unavailable: Iterable[str] = (),
) -> tuple[AgentCandidate, ...]:
    """Turn a project's registered agent names into candidates.

    ``declared_capabilities`` comes from agent configuration. An agent with no
    declaration is a candidate for nothing unless it is the built-in generalist
    — the alternative, treating an undeclared agent as capable of everything,
    would make the whole selection record meaningless.
    """
    declared = declared_capabilities or {}
    offline = {str(item) for item in unavailable}
    candidates: list[AgentCandidate] = []
    for name in dict.fromkeys(agent_names):
        capabilities = frozenset(declared.get(name, ()))
        candidates.append(
            AgentCandidate(
                name=name,
                capabilities=capabilities,
                is_generalist=name == GENERALIST_AGENT and not capabilities,
                available=name not in offline,
            )
        )
    return tuple(candidates)


#: Prompt fragments describing what each capability is being asked for. Used to
#: build a worker's task, and kept beside the capability set so adding one
#: without saying what it means is a visible omission.
CAPABILITY_BRIEFS: Mapping[Capability, str] = MappingProxyType(
    {
        Capability.BREEDING_STRATEGY: "Assess how this fits the breeding program's selection strategy and timeline.",
        Capability.GERMPLASM_AND_PEDIGREE: "Check germplasm identity, pedigree relationships, and relatedness structure.",
        Capability.EXPERIMENTAL_DESIGN: "State the question, population, treatments, and what would count as success or rejection.",
        Capability.QUANTITATIVE_GENETICS: "Assess heritability, genetic architecture, and the plausible predictive ceiling.",
        Capability.STATISTICAL_ANALYSIS: "Assess the analysis plan, its assumptions, and how uncertainty will be reported.",
        Capability.DATA_RECONCILIATION: "Reconcile every declared source: identifiers, units, coding, joins, exclusions, and missingness.",
        Capability.LITERATURE_REVIEW: "Summarise what is already known and what has already been tried.",
        Capability.SOFTWARE_ENGINEERING: "Assess whether the work can be executed and reproduced from what is written down.",
        Capability.FIELD_TRIAL_QC: "Check plot, environment, and trial-management data for implausible or inconsistent values.",
        Capability.VALIDITY_ASSESSMENT: "Assess what could make a result look good while being wrong.",
        Capability.SCIENTIFIC_REPORTING: "State the findings, their limitations, and what remains unresolved.",
        Capability.KNOWLEDGE_SYNTHESIS: "Draw out what this cycle changes about what the project believes.",
    }
)


def capability_brief(capability: Capability) -> str:
    return CAPABILITY_BRIEFS.get(capability, f"Contribute {capability.value.replace('_', ' ')} expertise.")
