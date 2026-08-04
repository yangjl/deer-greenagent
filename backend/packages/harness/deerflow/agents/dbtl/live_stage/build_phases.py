"""Turning a Build plan into work units, one phase at a time.

Each phase asks for a **capability** and gets the best available agent through
the selection machinery the Design council already uses. Three properties follow,
and all three matter more as specialists are added:

* a deployment with no registered specialists still works — every phase runs as
  `general-purpose`, honestly recorded as a generalist stand-in, which is
  today's behaviour and not a regression;
* registering a specialist later changes **who runs which phase and nothing
  else**, because the phase asked for a capability rather than an agent name; and
* the record names the capability requested *and* the agent that covered it, so
  a reviewer reading "quantitative genetics: general-purpose" knows what they
  are looking at.

Phases are **sequential**, and a phase may read the outputs of the phases before
it — that is what lets phase 3 fit a model phase 1 simulated — but never modify
them. An earlier phase's output is an input, hash-bound like any other.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from deerflow.agents.dbtl.live_stage.workspace import SHELL_WORKSPACE_IDIOM, STAGE_UNIT_WORKSPACE_PLACEHOLDER
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.build_plan import PLANNER_CONTRACT, BuildPhase, BuildPhasePlan
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_runner import BUILD_PLAN_OUTPUT, WorkUnit
from deerflow.dbtl.stage_spec import StageSpec

#: The seat that draws the plan. Read-only by role, like the summarizer: it
#: writes nothing, runs nothing, and dispatches nobody.
PLANNER_ROLE = "planner"
PHASE_ROLE = "phase"
PHASE_DONE_CHECK = "phase_done_condition"

GENERALIST = "general-purpose"


@dataclass(frozen=True, slots=True)
class PhaseAssignment:
    """Which agent covered one phase's capability, and whether it specialises.

    `agent_name` is empty exactly when nothing can cover the phase. `covered`
    is the property callers ask, because "no agent" and "the generalist" must
    never be the same branch.
    """

    phase: BuildPhase
    agent_name: str
    via_generalist: bool

    @property
    def covered(self) -> bool:
        return bool(self.agent_name)


def assign_phase(phase: BuildPhase, candidates: Sequence[AgentCandidate]) -> PhaseAssignment:
    """Resolve one phase's capability against the registered agents.

    A specialist wins; otherwise the **registered generalist** covers it and the
    stand-in is recorded rather than hidden. Recording it is the whole point: a
    reviewer reading nothing cannot tell a specialist from a stand-in.

    "Generalist" means the agent registered as one, not whichever agent happens
    to sort first. Falling back to an arbitrary candidate is the same silent
    swap capability selection exists to prevent, and worse than the version it
    replaced: `bash` is a real registered subagent, so a deployment that
    registered no generalist would have run a modelling phase on a command
    runner and recorded it as a generalist stand-in. With nothing able to cover
    it the phase is refused, which is a sentence a person can act on.
    """
    available = [item for item in candidates if getattr(item, "available", True)]
    specialist = next((item for item in available if phase.capability in item.capabilities), None)
    if specialist is not None:
        return PhaseAssignment(phase=phase, agent_name=specialist.name, via_generalist=False)
    generalist = next((item for item in available if item.name == GENERALIST), None)
    return PhaseAssignment(phase=phase, agent_name=generalist.name if generalist else "", via_generalist=bool(generalist))


def planner_unit(
    *,
    attempt_id: str,
    agent_name: str,
    context: str,
    model: str | None = None,
) -> WorkUnit:
    """The bounded planning seat.

    It receives the input bundle and the cycle's own question, and returns a
    plan. Nothing it can say dispatches anything: the plan is proposed, and the
    server decides what to run from it.
    """
    prompt = "\n\n".join([PLANNER_CONTRACT, "Build input bundle:", context.strip() or "(none supplied)"])
    return WorkUnit(
        unit_id=f"{attempt_id}-plan",
        capability=Capability.SOFTWARE_ENGINEERING.value,
        agent_name=agent_name,
        prompt=prompt,
        role=PLANNER_ROLE,
        model=model,
        output_contract=BUILD_PLAN_OUTPUT,
    )


def phase_unit(
    assignment: PhaseAssignment,
    *,
    index: int,
    attempt_id: str,
    attempt_token: str,
    spec: StageSpec,
    context: str,
    completed: Sequence[Mapping[str, object]] = (),
    result_contract: str = "",
) -> WorkUnit:
    """One phase's work unit, carrying what the phases before it produced.

    `attempt_token` is the step attempt this unit belongs to, and it is in the
    unit id because the unit id is what the isolated workspace is derived from.
    Without it a retry inherited the failed attempt's directory: half-written
    files, a stale log, and an output the previous run had already declared —
    which the publisher would then copy into the governed tree as this attempt's
    evidence.
    """
    phase = assignment.phase
    preceding = (
        [
            "Outputs of the phases before this one. You may read them; you may not modify them —",
            "they are inputs, hash-bound like any other.",
            json.dumps(list(completed), sort_keys=True, ensure_ascii=False),
        ]
        if completed
        else ["This is the first phase; nothing precedes it."]
    )
    lines = [
        f"You are running one phase of the {spec.title} stage of a DBTL research cycle.",
        "",
        f"Phase {index} of this build: {phase.title}",
        f"Objective: {phase.objective}",
        f"Capability requested: {phase.capability.value}",
        *([f"Expected inputs: {'; '.join(phase.inputs)}"] if phase.inputs else []),
        *([f"Expected outputs: {'; '.join(phase.outputs)}"] if phase.outputs else []),
        *([f"Done when: {phase.done_condition}"] if phase.done_condition else []),
        "",
        *preceding,
        "",
        "Project context:",
        context.strip() or "(none supplied)",
        "",
        f"Write every new implementation, derived output, and execution log under {STAGE_UNIT_WORKSPACE_PLACEHOLDER}.",
        "The server has already created src/, tests/, config/, artifacts/, and logs/ there.",
        "Use write_file or str_replace for source, configuration, and documentation. Use Bash",
        "only for short execution and verification commands; do not embed complete files in",
        "Bash heredocs or in a Python write_text wrapper.",
        SHELL_WORKSPACE_IDIOM,
        "Your objective above was derived from the approved Design, so you normally do not need",
        "the Design itself. Project context names it and the manifest lists the project's files;",
        "read a named file only when you need its exact bytes, and do not read the Design merely",
        "to restate it.",
        "",
        "This phase reports; it does not grade itself. A check you ran and that failed is a",
        "recorded failed check with its detail — not a reason to hide the work.",
        *(
            [
                f"You MUST include exactly one quality check named {PHASE_DONE_CHECK!r}.",
                "Set it to passed=true only after the phase's declared Done when condition is met",
                "and every expected output exists. If either is incomplete, set it to false,",
                "and do not set it true while another implementation quality check is false.",
                "A failed repeat-run/reproducibility check is the sole exception: record that as",
                "a limitation because Test and the human reviewer own that verdict.",
                "report status=failed, name the missing work in its detail, and stop. Partial files",
                "remain auditable, but they cannot advance this build plan.",
            ]
            if spec.version >= 6
            else ["Report status=failed only when the work could not be done at all."]
        ),
        "",
        result_contract,
    ]
    return WorkUnit(
        unit_id=f"{attempt_id}-{index}-{phase.phase_key}-{attempt_token}",
        capability=phase.capability.value,
        agent_name=assignment.agent_name,
        prompt="\n".join(line for line in lines if line is not None),
        via_generalist=assignment.via_generalist,
        role=PHASE_ROLE,
        completion_check=PHASE_DONE_CHECK if spec.version >= 6 else "",
    )


def plan_notes(plan: BuildPhasePlan, assignments: Sequence[PhaseAssignment]) -> tuple[str, ...]:
    """Human-readable notes recorded beside the plan.

    A generalist stand-in is named here so it reaches the review package, where
    the alternative — silence — reads as a specialist having run.
    """
    notes: list[str] = []
    if plan.note:
        notes.append(plan.note)
    for assignment in assignments:
        if not assignment.covered:
            notes.append(f"{assignment.phase.capability.value}: no registered agent could cover it, so the phase was not run.")
        elif assignment.via_generalist:
            notes.append(f"{assignment.phase.capability.value}: covered by {assignment.agent_name} (no registered specialist).")
    return tuple(notes)
