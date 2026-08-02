"""The plan a Build runs under: typed, bounded, and content-addressed.

`plan_build` answers one question — *what are the pieces of work this Design
implies, in what order, and what kind of specialist should do each one?* — and
writes nothing, runs no Bash, and dispatches no worker. It is cheap and
re-runnable by construction, which is what makes replanning an ordinary act
rather than a decision to throw work away.

Three rules shape the parsing, and they pull in different directions on purpose.

**`single_phase` is a real answer.** A short simulation script is not four
phases pretending to be a project. The feasibility verdict is part of the
contract so a legitimate one-piece Build does not read as a planner failure.

**Shape problems degrade; they do not fail.** Unparseable output, an over-long
list, a phase with no objective — each costs the decomposition and leaves a
recorded note, because losing the structure costs structure while failing here
costs the whole Build.

**An unknown capability is never quietly the generalist.** That silent swap is
the exact bug capability selection exists to prevent, so a phase asking for
something unregistered collapses the plan to `single_phase` *and says which
capability it could not honour*. Nobody is left believing a specialist ran.

The plan is data, not a runtime shape: it carries its own digest, so a phase
attempt binds to the plan it sat in and a changed plan invalidates the phases
beneath it rather than silently rebinding them.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from deerflow.dbtl.build_workflow import MAX_BUILD_PHASES
from deerflow.dbtl.capabilities import Capability

MAX_TEXT_CHARS = 1_200
MAX_LIST_ITEMS = 12

_SLUG = re.compile(r"[^a-z0-9]+")
_FENCE = re.compile(r"^\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*$", re.DOTALL)


class PlanFeasibility(StrEnum):
    """What the planner concluded about decomposing this Build."""

    #: A decomposition it can defend.
    PLANNED = "planned"
    #: The work does not usefully decompose. A legitimate outcome, not a failure.
    SINGLE_PHASE = "single_phase"
    #: The Design leaves something the planner cannot resolve, or the work as
    #: specified cannot be built at all.
    NEEDS_INPUT = "needs_input"


def phase_slug(value: str, *, index: int) -> str:
    """A stable, readable key. Stable is the load-bearing half.

    A phase's key is part of its attempt identity, so a key derived from
    position alone would make inserting a phase renumber every phase after it —
    and a retry of "phase 3" would then be a retry of different work.
    """
    slug = _SLUG.sub("-", (value or "").strip().lower()).strip("-")[:48]
    return slug or f"phase-{index}"


def _text(value: Any, *, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    trimmed = value.strip()
    return trimmed if len(trimmed) <= limit else trimmed[: limit - 1] + "…"


def _lines(value: Any, *, limit: int = MAX_LIST_ITEMS) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(text for text in (_text(item, limit=400) for item in value) if text)[:limit]


@dataclass(frozen=True, slots=True)
class BuildPhase:
    """One piece of work, declared by capability rather than by agent name.

    Declaring a capability is what lets a deployment register a specialist later
    and change **who runs which phase and nothing else** — no workflow, contract,
    or UI change, because the phase never named an agent.
    """

    phase_key: str
    title: str
    objective: str
    capability: Capability
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    done_condition: str = ""
    #: A phase whose result a person should see before the next one consumes it.
    #: A phase boundary is a committed, resumable state with no worker lease
    #: held, which makes it the cheapest possible place to stop.
    pause_after: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase_key": self.phase_key,
            "title": self.title,
            "objective": self.objective,
            "capability": self.capability.value,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "done_condition": self.done_condition,
            "pause_after": self.pause_after,
        }


@dataclass(frozen=True, slots=True)
class BuildPhasePlan:
    """An ordered, bounded decomposition — or an honest statement that there is none."""

    feasibility: PlanFeasibility
    phases: tuple[BuildPhase, ...] = ()
    rationale: str = ""
    assumptions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    clarification_question: str = ""
    #: Why this plan is shaped as it is when the planner's own answer was not
    #: used verbatim — a degradation, a cap, a refused capability. Recorded
    #: rather than logged, because a reviewer reading a one-phase Build should
    #: be able to tell "it did not decompose" from "we could not read the plan".
    note: str = ""

    def __post_init__(self) -> None:
        if self.feasibility is PlanFeasibility.NEEDS_INPUT and not self.clarification_question.strip():
            raise ValueError("A plan that needs input must state the question.")
        if self.feasibility is PlanFeasibility.PLANNED and len(self.phases) < 2:
            raise ValueError("A decomposed plan needs at least two phases; one phase is `single_phase`.")
        if self.feasibility is PlanFeasibility.SINGLE_PHASE and len(self.phases) != 1:
            raise ValueError("A single-phase plan has exactly one phase.")
        if len(self.phases) > MAX_BUILD_PHASES:
            raise ValueError(f"A Build plan is capped at {MAX_BUILD_PHASES} phases.")
        if len({phase.phase_key for phase in self.phases}) != len(self.phases):
            raise ValueError("Phase keys must be unique within a plan.")

    @property
    def digest(self) -> str:
        """This plan's identity. A phase attempt binds to it.

        Deliberately excludes `note` and `rationale`: those explain the plan to a
        person and do not change what the phases *are*, so a reworded rationale
        must not invalidate completed phases.
        """
        return hashlib.sha256(
            json.dumps(
                {"feasibility": self.feasibility.value, "phases": [phase.as_dict() for phase in self.phases]},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @property
    def dispatchable(self) -> bool:
        return self.feasibility in {PlanFeasibility.PLANNED, PlanFeasibility.SINGLE_PHASE} and bool(self.phases)

    def as_dict(self) -> dict[str, Any]:
        return {
            "feasibility": self.feasibility.value,
            "phases": [phase.as_dict() for phase in self.phases],
            "rationale": self.rationale,
            "assumptions": list(self.assumptions),
            "open_questions": list(self.open_questions),
            "clarification_question": self.clarification_question,
            "note": self.note,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class PlanParse:
    """A plan, always — plus what had to be done to get one."""

    plan: BuildPhasePlan
    degraded: bool = False
    reasons: tuple[str, ...] = field(default=())


def single_phase_plan(
    *,
    objective: str,
    capability: Capability = Capability.SOFTWARE_ENGINEERING,
    note: str = "",
    title: str = "Implement the approved design",
) -> BuildPhasePlan:
    """The plan a Build runs under when it does not usefully decompose.

    Also the destination of every degradation, which is why it takes a `note`:
    "this work is one piece" and "we could not read the planner" produce the same
    execution and must not produce the same record.
    """
    return BuildPhasePlan(
        feasibility=PlanFeasibility.SINGLE_PHASE,
        phases=(
            BuildPhase(
                phase_key="build",
                title=title,
                objective=_text(objective) or "Implement and execute the approved design.",
                capability=capability,
                done_condition="The implementation runs and its outputs are recorded.",
            ),
        ),
        rationale="The work was not decomposed.",
        note=note,
    )


def _load(raw: str) -> Mapping[str, Any] | None:
    """Two deterministic repairs, then give up.

    The common failure is presentational — a fenced block, or prose wrapped
    around otherwise valid JSON — and losing a decomposition to a code fence
    would be a silly reason to run a four-phase Build as one piece.
    """
    candidate = (raw or "").strip()
    fenced = _FENCE.match(candidate)
    start, end = candidate.find("{"), candidate.rfind("}")
    attempts = [candidate]
    if fenced:
        attempts.append(fenced.group("body"))
    if 0 <= start < end:
        attempts.append(candidate[start : end + 1])
    for text in attempts:
        if not text.strip():
            continue
        try:
            loaded = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(loaded, Mapping):
            return loaded
    return None


def parse_build_plan(raw: str, *, objective: str) -> PlanParse:
    """Read a planner's answer, degrading to `single_phase` rather than failing.

    Always returns a dispatchable plan or an explicit `needs_input`. A Build that
    cannot be planned is still a Build that can be run as one piece, and that is
    strictly better than a Build that cannot start.
    """
    payload = _load(raw)
    if payload is None:
        return PlanParse(
            plan=single_phase_plan(objective=objective, note="The planner's answer could not be read, so the build runs as one piece."),
            degraded=True,
            reasons=("unparseable_plan",),
        )

    question = _text(payload.get("clarification_question"), limit=800)
    declared = _text(payload.get("feasibility"), limit=32).lower()
    if declared == PlanFeasibility.NEEDS_INPUT.value or question:
        if not question:
            return PlanParse(
                plan=single_phase_plan(objective=objective, note="The planner asked for input without stating a question, so the build runs as one piece."),
                degraded=True,
                reasons=("needs_input_without_question",),
            )
        return PlanParse(
            plan=BuildPhasePlan(
                feasibility=PlanFeasibility.NEEDS_INPUT,
                rationale=_text(payload.get("rationale")),
                assumptions=_lines(payload.get("assumptions")),
                open_questions=_lines(payload.get("open_questions")),
                clarification_question=question,
            )
        )

    raw_phases = payload.get("phases")
    entries = [item for item in raw_phases if isinstance(item, Mapping)] if isinstance(raw_phases, Sequence) and not isinstance(raw_phases, (str, bytes)) else []
    reasons: list[str] = []
    if len(entries) > MAX_BUILD_PHASES:
        # A planner that wants more says so as an open question rather than
        # emitting a project plan.
        return PlanParse(
            plan=single_phase_plan(objective=objective, note=f"The planner proposed {len(entries)} phases, above the limit of {MAX_BUILD_PHASES}, so the build runs as one piece."),
            degraded=True,
            reasons=("plan_too_long",),
        )

    phases: list[BuildPhase] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        title = _text(entry.get("title"), limit=160)
        objective_text = _text(entry.get("objective"))
        if not title or not objective_text:
            reasons.append("phase_missing_title_or_objective")
            break
        raw_capability = _text(entry.get("capability"), limit=96)
        try:
            capability = Capability(raw_capability)
        except ValueError:
            # Never the generalist by default. A silent swap here is the bug
            # capability selection exists to prevent, so the plan collapses and
            # says which capability it could not honour.
            return PlanParse(
                plan=single_phase_plan(
                    objective=objective,
                    note=f"The plan asked for an unregistered capability {raw_capability or '(unnamed)'!r}, so it was not used and the build runs as one piece.",
                ),
                degraded=True,
                reasons=("unknown_capability",),
            )
        key = phase_slug(_text(entry.get("phase_key"), limit=64) or title, index=index)
        while key in seen:
            key = f"{key}-{index}"
        seen.add(key)
        phases.append(
            BuildPhase(
                phase_key=key,
                title=title,
                objective=objective_text,
                capability=capability,
                inputs=_lines(entry.get("inputs")),
                outputs=_lines(entry.get("outputs")),
                done_condition=_text(entry.get("done_condition"), limit=600),
                pause_after=entry.get("pause_after") is True,
            )
        )

    if not phases:
        return PlanParse(
            plan=single_phase_plan(objective=objective, note="The planner proposed no usable phase, so the build runs as one piece."),
            degraded=True,
            reasons=tuple(reasons) or ("no_phases",),
        )

    if len(phases) == 1:
        # Honest rather than flattering: one phase *is* `single_phase`, whatever
        # the planner labelled it.
        return PlanParse(
            plan=BuildPhasePlan(
                feasibility=PlanFeasibility.SINGLE_PHASE,
                phases=tuple(phases),
                rationale=_text(payload.get("rationale")),
                assumptions=_lines(payload.get("assumptions")),
                open_questions=_lines(payload.get("open_questions")),
                note="; ".join(reasons),
            ),
            degraded=bool(reasons),
            reasons=tuple(reasons),
        )

    return PlanParse(
        plan=BuildPhasePlan(
            feasibility=PlanFeasibility.PLANNED,
            phases=tuple(phases),
            rationale=_text(payload.get("rationale")),
            assumptions=_lines(payload.get("assumptions")),
            open_questions=_lines(payload.get("open_questions")),
            note="; ".join(reasons),
        ),
        degraded=bool(reasons),
        reasons=tuple(reasons),
    )


_CAPABILITY_LIST = ", ".join(item.value for item in Capability)

PLANNER_CONTRACT = f"""You are planning a Build that has not started. You write nothing, run nothing, and
dispatch nobody: your only output is the plan.

Answer: what are the pieces of work this approved design implies, in what order, and what
kind of specialist should do each one?

Return one JSON object and nothing else:

{{
  "feasibility": "planned" | "single_phase" | "needs_input",
  "rationale": "why this decomposition",
  "phases": [
    {{
      "phase_key": "short-stable-slug",
      "title": "short visible title",
      "objective": "one or two sentences",
      "capability": "<one of the registered capabilities listed below>",
      "inputs": ["what this phase expects"],
      "outputs": ["what it should produce, including figures"],
      "done_condition": "something a later reader can check",
      "pause_after": false
    }}
  ],
  "assumptions": ["..."],
  "open_questions": ["..."],
  "clarification_question": "only when feasibility is needs_input"
}}

Rules:
- "single_phase" is a real answer. A short script is not four phases pretending to be a
  project. Use it whenever the work does not usefully decompose, and give it one phase.
- At most {MAX_BUILD_PHASES} phases. If the work needs more, say so as an open question
  rather than emitting a project plan.
- `capability` must be one of the registered values. An unregistered one is refused and the
  whole plan is discarded — it is never quietly replaced by a generalist.
- Set `pause_after` only when a person genuinely should see that phase's result before the
  next phase consumes it.
- Use "needs_input" when the design leaves something you cannot resolve, or the work as
  specified cannot be built at all.

Registered capabilities: {_CAPABILITY_LIST}
"""
