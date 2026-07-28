"""Who will sit on the Design council, decided before it convenes.

The council already worked; what it did not do was let anyone *look* at it
first. A scientist would type a request and, some minutes later, receive a
synthesis produced by agents they never chose, on a model they could not see,
after a debate whose depth nobody set. That is a poor deal even when the output
is good: the reviewer cannot tell a one-generalist monologue from a genuine
three-specialist disagreement, because both arrive looking the same.

This module is the roster, computed as data:

**One computation answers "who will run" and "who did run".** :func:`plan_council`
produces the seats, and both the preflight card and the executor read the same
plan. If the card were built from a separate estimate it would eventually
describe a council that did not happen, which is worse than showing nothing.

**Depth is the human's dial, not the model's.** Light, medium, and heavy change
how many independent positions are heard and what each worker may spend. They
never change the *shape* of the debate: every council, at every depth, hears at
least one position, one challenge to it, and a chair who weighs them. A "light"
council that dropped the red team would be a single opinion wearing a council's
name, so depth cannot buy that saving.

**Depth is recorded, not folded into the contract.** ``StageSpec.budget`` is
deliberately part of the versioned spec so a reviewer can reconstruct what a
worker was allowed to spend. A per-run depth would quietly break that, so the
effective budget and the chosen depth are carried on the plan and recorded with
the attempt: the spec key still names the contract, and the depth names the
parameter the human picked within it.

**A recommendation is a starting point, never a decision.** :func:`recommend_depth`
is deterministic and rule-based for the same reason the request classifier is:
the person overriding it deserves to see which words drove it, and a suggestion
that changes between identical requests cannot be argued with.

Nothing here dispatches anything. The plan is values, so what the council will
do is testable without a model, a sandbox, or a registry.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType

from deerflow.dbtl.agent_selector import (
    AgentCandidate,
    SelectionResult,
    capability_brief,
    select_agents,
)
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_spec import StageSpec, WorkerBudget


class UnknownAgent(ValueError):
    """A seat was reassigned to an agent this project has not registered."""


class CouncilRole(StrEnum):
    """What a seat is for.

    Roles are fixed rather than free-form: the chair's synthesis is the only
    output that counts toward the stage, and the red team is the guaranteed
    challenge. Both facts are read off the role, so a new role string would
    silently be treated as neither.
    """

    POSITION = "position"
    RED_TEAM = "red_team"
    CHAIR = "chair"


ROLE_LABELS: Mapping[CouncilRole, str] = MappingProxyType(
    {
        CouncilRole.POSITION: "Independent position",
        CouncilRole.RED_TEAM: "Red team",
        CouncilRole.CHAIR: "Chair",
    }
)

ROLE_BRIEFS: Mapping[CouncilRole, str] = MappingProxyType(
    {
        CouncilRole.POSITION: "Proposes a design from its own expertise, without seeing the other positions.",
        CouncilRole.RED_TEAM: "Argues against the proposed design: controls, leakage, thresholds, and hidden assumptions.",
        CouncilRole.CHAIR: "Weighs the positions against each other and either synthesizes a design or asks one question.",
    }
)


class CouncilDepth(StrEnum):
    """How much debate the question is worth.

    ``HUMAN_INPUT`` is not simply the smallest of these. The other three are
    judgements about how much scrutiny a question deserves; this one says the
    person already holds the answer and wants it recorded rather than argued.
    That is why it is the only depth that seats nobody, and why nothing
    recommends it.
    """

    HUMAN_INPUT = "human_input"
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"


@dataclass(frozen=True, slots=True)
class DepthPolicy:
    """What one depth setting buys, and what it costs."""

    depth: CouncilDepth
    label: str
    description: str
    max_positions: int
    budget: WorkerBudget

    @property
    def max_seats(self) -> int:
        """Positions plus the red team plus the chair."""
        return self.max_positions + 2

    def as_dict(self) -> dict[str, object]:
        return {
            "depth": self.depth.value,
            "label": self.label,
            "description": self.description,
            "max_positions": self.max_positions,
            "max_seats": self.max_seats,
            "budget": {
                "max_turns": self.budget.max_turns,
                "max_tokens": self.budget.max_tokens,
                "timeout_seconds": self.budget.timeout_seconds,
            },
        }


#: The four settings, described in the terms the chooser thinks in.
#:
#: ``max_turns`` reaches LangGraph as ``recursion_limit``, which counts
#: super-steps, not turns — see ``SUBAGENT_SUPERSTEPS_PER_TURN``. These numbers
#: were originally read as turns, which bought every depth two to eight model
#: calls: not enough for a worker to read its context, argue, and emit a
#: structured result, so each one was cut off mid-loop and the whole council
#: reported prose. They are now sized so ``model_call_budget`` yields a usable
#: number of calls, and ``TestCouncilBudgetsFitRealWork`` fails if a future edit
#: takes one back below that.
DEPTH_POLICIES: Mapping[CouncilDepth, DepthPolicy] = MappingProxyType(
    {
        CouncilDepth.HUMAN_INPUT: DepthPolicy(
            depth=CouncilDepth.HUMAN_INPUT,
            label="Write it myself",
            description="No agent is consulted. You write the design and it is recorded for review exactly as you wrote it. For a design you have already settled, or one only you can make.",
            max_positions=0,
            # Carried for shape only; nothing is dispatched at this depth. The
            # values stay legal because ``WorkerBudget`` refuses non-positive
            # limits, and a budget nobody spends is better left obviously small
            # than set to something a reader might mistake for an allowance.
            budget=WorkerBudget(max_workers=1, max_turns=1, max_tokens=1, timeout_seconds=1),
        ),
        CouncilDepth.LIGHT: DepthPolicy(
            depth=CouncilDepth.LIGHT,
            label="Light debate",
            description="One position, one challenge, one synthesis. For a quick look, a pilot, or a design you mostly already have.",
            max_positions=1,
            budget=WorkerBudget(max_workers=1, max_turns=80, max_tokens=150_000, timeout_seconds=420),
        ),
        CouncilDepth.MEDIUM: DepthPolicy(
            depth=CouncilDepth.MEDIUM,
            label="Medium debate",
            description="Up to two independent positions before the challenge and synthesis. The default for ordinary cycle work.",
            max_positions=2,
            budget=WorkerBudget(max_workers=2, max_turns=120, max_tokens=400_000, timeout_seconds=900),
        ),
        CouncilDepth.HEAVY: DepthPolicy(
            depth=CouncilDepth.HEAVY,
            label="Heavy research",
            description="Up to four independent positions, each with room to read the workspace and argue in detail. For work that has to survive outside review.",
            max_positions=4,
            budget=WorkerBudget(max_workers=4, max_turns=190, max_tokens=900_000, timeout_seconds=1800),
        ),
    }
)

DEFAULT_DEPTH = CouncilDepth.MEDIUM

#: Where a confirmed depth travels. Read from the request's ``context`` only,
#: never the merged runtime view: ``configurable`` is checkpointed, so a depth
#: accepted from there would keep steering every later turn in the thread
#: instead of the one it was chosen for — the rule the selected cycle follows.
COUNCIL_DEPTH_CONTEXT_KEY = "dbtl_council_depth"


def request_context(config: Mapping[str, object] | None) -> Mapping[str, object]:
    """The per-request context, wherever the caller is standing.

    A run request carries ``context`` at the top level, but LangGraph relocates
    it to ``configurable["context"]`` before a node sees it — so code that runs
    both inside and outside the graph (this does) has to look in both places or
    it silently reads nothing in one of them. Deliberately *not* the merged
    `configurable` mapping: that is checkpointed, and a per-request choice read
    from there would keep steering later turns.
    """
    if not isinstance(config, Mapping):
        return {}
    direct = config.get("context")
    if isinstance(direct, Mapping) and direct:
        return direct
    configurable = config.get("configurable")
    nested = configurable.get("context") if isinstance(configurable, Mapping) else None
    return nested if isinstance(nested, Mapping) else {}


def council_depth_from_config(config: Mapping[str, object] | None) -> CouncilDepth | None:
    """The depth a human confirmed, for this request only.

    An unrecognized value returns ``None`` rather than raising: a stale client
    losing a preference is a far smaller failure than a cycle that cannot be
    designed.
    """
    raw = request_context(config).get(COUNCIL_DEPTH_CONTEXT_KEY)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return CouncilDepth(raw.strip().lower())
    except ValueError:
        return None


def depth_policy(depth: CouncilDepth) -> DepthPolicy:
    return DEPTH_POLICIES[CouncilDepth(depth)]


@dataclass(frozen=True, slots=True)
class CouncilSeat:
    """One worker the council will dispatch, described for a person."""

    seat_id: str
    role: CouncilRole
    capability: Capability
    agent_name: str
    model: str
    via_generalist: bool = False
    tools: tuple[str, ...] = ()
    inherits_all_tools: bool = True
    #: What this seat argues *from*, when a proposal wrote it for the question.
    #: Empty for a capability-selected seat, which has only its capability.
    focus: str = ""
    #: The proposal's own brief. Overrides the capability's generic one: three
    #: seats that all resolve to ``general-purpose`` are a debate only because
    #: each was told to argue from somewhere different, and showing the same
    #: generic line under each would read as one agent listed three times.
    proposed_brief: str = ""

    @property
    def role_label(self) -> str:
        return ROLE_LABELS[self.role]

    @property
    def brief(self) -> str:
        """What this seat is being asked to do, in one line."""
        if self.proposed_brief:
            return self.proposed_brief
        if self.role is CouncilRole.POSITION:
            return capability_brief(self.capability)
        return ROLE_BRIEFS[self.role]

    @property
    def counts_toward_stage_output(self) -> bool:
        """Only the chair's synthesis is the stage's answer."""
        return self.role is CouncilRole.CHAIR

    def as_dict(self) -> dict[str, object]:
        return {
            "seat_id": self.seat_id,
            "role": self.role.value,
            "role_label": self.role_label,
            "capability": self.capability.value,
            "agent_name": self.agent_name,
            "model": self.model,
            "via_generalist": self.via_generalist,
            "tools": list(self.tools),
            "inherits_all_tools": self.inherits_all_tools,
            "brief": self.brief,
            "focus": self.focus,
            "counts_toward_stage_output": self.counts_toward_stage_output,
        }


@dataclass(frozen=True, slots=True)
class CouncilPlan:
    """The whole roster: who sits, at what depth, on what budget."""

    stage_spec_key: str
    depth: CouncilDepth
    seats: tuple[CouncilSeat, ...]
    budget: WorkerBudget
    unmet_capabilities: tuple[Capability, ...] = ()
    notes: tuple[str, ...] = ()
    known_agents: tuple[str, ...] = ()

    @property
    def dispatchable(self) -> bool:
        """Whether this council can actually be run.

        An unmet required capability produces no seats at all rather than a
        partial roster: half a council is evidence of nothing, and showing one
        would invite a person to approve work that cannot answer the question.
        """
        return bool(self.seats) and not self.unmet_capabilities

    @property
    def human_authored(self) -> bool:
        """Whether the empty roster is a choice rather than a failure.

        ``dispatchable`` is false for both an unmet capability and the
        Human Input depth, and they mean opposite things: one is "this council
        cannot answer the question", the other is "the person is answering it".
        Rendering the first message for the second would tell someone their
        deliberate choice had gone wrong, so callers must ask this **first**.
        """
        return self.depth is CouncilDepth.HUMAN_INPUT

    @property
    def position_count(self) -> int:
        return sum(1 for seat in self.seats if seat.role is CouncilRole.POSITION)

    @property
    def agent_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(seat.agent_name for seat in self.seats))

    def seat(self, seat_id: str) -> CouncilSeat:
        for item in self.seats:
            if item.seat_id == seat_id:
                return item
        raise KeyError(f"No council seat {seat_id!r}.")

    def with_seat_agent(self, seat_id: str, agent_name: str) -> CouncilPlan:
        """Reassign one seat, refusing an agent nobody registered.

        Fail closed rather than falling back to the generalist: a mistyped name
        that quietly ran as ``general-purpose`` would produce exactly the
        undisclosed monologue this module exists to prevent.
        """
        if agent_name not in self.known_agents:
            known = ", ".join(self.known_agents) or "none"
            raise UnknownAgent(f"{agent_name!r} is not a registered agent for this project (known: {known}).")
        target = self.seat(seat_id)
        moved = replace(target, agent_name=agent_name, via_generalist=False)
        return replace(self, seats=tuple(moved if item.seat_id == seat_id else item for item in self.seats))

    def with_depth(self, depth: CouncilDepth) -> CouncilPlan:
        """Re-scope this roster to a different depth.

        Trims positions and re-budgets; it cannot *add* positions, because the
        candidate pool is not carried on the plan. A caller that still holds the
        candidates — the executor does — should call :func:`plan_council` again
        so a deeper setting actually seats more specialists. Positions are
        trimmed from the end and the red team and chair are preserved
        explicitly, because slicing the seat list would drop them first.
        """
        depth = CouncilDepth(depth)
        if depth is self.depth:
            return self
        policy = depth_policy(depth)
        if depth is CouncilDepth.HUMAN_INPUT:
            # Not a trim. The red team and chair are preserved explicitly at
            # every other depth, and keeping them here would dispatch two
            # workers for the one setting whose whole point is that none run.
            return replace(self, depth=depth, seats=(), budget=policy.budget)
        positions = tuple(seat for seat in self.seats if seat.role is CouncilRole.POSITION)
        others = tuple(seat for seat in self.seats if seat.role is not CouncilRole.POSITION)
        return replace(
            self,
            depth=depth,
            seats=positions[: policy.max_positions] + others,
            budget=policy.budget,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "stage_spec_key": self.stage_spec_key,
            "depth": self.depth.value,
            "depth_label": depth_policy(self.depth).label,
            "dispatchable": self.dispatchable,
            "human_authored": self.human_authored,
            "position_count": self.position_count,
            "seats": [seat.as_dict() for seat in self.seats],
            "budget": {
                "max_turns": self.budget.max_turns,
                "max_tokens": self.budget.max_tokens,
                "timeout_seconds": self.budget.timeout_seconds,
            },
            "unmet_capabilities": [item.value for item in self.unmet_capabilities],
            "notes": list(self.notes),
            "known_agents": list(self.known_agents),
        }


def plan_council(
    spec: StageSpec,
    candidates: Sequence[AgentCandidate],
    *,
    depth: CouncilDepth = DEFAULT_DEPTH,
    model: str,
    tools_by_agent: Mapping[str, Sequence[str]] | None = None,
    attempt_id: str = "council",
) -> CouncilPlan:
    """Work out who will sit on the council, without dispatching anything.

    Positions come from the existing capability selection, so the roster cannot
    drift from how workers are actually chosen; depth only decides how many of
    them are affordable. The red team and chair are appended unconditionally —
    they are the debate's shape, not part of its budget.
    """
    depth = CouncilDepth(depth)
    policy = depth_policy(depth)
    tools_map = {str(name): tuple(str(tool) for tool in tools) for name, tools in (tools_by_agent or {}).items()}
    known = tuple(dict.fromkeys(item.name for item in candidates if item.available))

    if depth is CouncilDepth.HUMAN_INPUT:
        # Selection is skipped entirely rather than run and discarded. Running
        # it would let an unmet capability populate ``unmet_capabilities`` on a
        # plan that was never going to dispatch, and the preflight card would
        # then warn someone about a shortfall that cannot affect them.
        return CouncilPlan(
            stage_spec_key=spec.spec_key,
            depth=depth,
            seats=(),
            budget=policy.budget,
            known_agents=known,
        )

    # Selection reads its worker ceiling off the spec, so give it a spec whose
    # budget is the one this depth actually grants.
    scoped = replace(spec, budget=policy.budget)
    selection: SelectionResult = select_agents(scoped, candidates)

    if not selection.satisfied:
        return CouncilPlan(
            stage_spec_key=spec.spec_key,
            depth=depth,
            seats=(),
            budget=policy.budget,
            unmet_capabilities=selection.unmet_capabilities,
            notes=selection.notes,
            known_agents=known,
        )

    def _seat(seat_id: str, role: CouncilRole, capability: Capability, agent_name: str, via_generalist: bool) -> CouncilSeat:
        declared = tools_map.get(agent_name)
        return CouncilSeat(
            seat_id=seat_id,
            role=role,
            capability=capability,
            agent_name=agent_name,
            model=model,
            via_generalist=via_generalist,
            tools=declared or (),
            inherits_all_tools=declared is None,
        )

    seats: list[CouncilSeat] = []
    for index, assignment in enumerate(selection.assignments[: policy.max_positions], start=1):
        seats.append(
            _seat(
                f"{attempt_id}-position-{index}",
                CouncilRole.POSITION,
                assignment.capability,
                assignment.agent_name,
                assignment.via_generalist,
            )
        )

    # The red team and chair argue about the design itself, so they inherit the
    # required capability rather than claiming one of their own.
    lead = seats[0]
    seats.append(_seat(f"{attempt_id}-red-team", CouncilRole.RED_TEAM, lead.capability, lead.agent_name, lead.via_generalist))
    seats.append(_seat(f"{attempt_id}-chair", CouncilRole.CHAIR, lead.capability, lead.agent_name, lead.via_generalist))

    return CouncilPlan(
        stage_spec_key=spec.spec_key,
        depth=depth,
        seats=tuple(seats),
        budget=policy.budget,
        unmet_capabilities=(),
        notes=selection.notes,
        known_agents=known,
    )


def plan_from_proposal(plan: CouncilPlan, proposal) -> CouncilPlan:
    """The same council, re-described from a roster written for the question.

    Depth, budget, and stage spec are kept from *plan* — those are the human's
    setting and the contract the attempt runs under, and a proposal has no
    business changing either. Only who sits, and what each seat argues from,
    comes from the proposal.

    Positions are capped by the plan's **depth**, not by however many the model
    returned: the person chose how much debate to buy. A proposal with no chair
    falls back to *plan* untouched, because a council with nobody to synthesize
    it is not a council.

    Takes ``proposal`` untyped to keep this module free of an import cycle with
    ``council_proposal``, which already depends on the capability vocabulary.
    """
    chair = getattr(proposal, "chair", None)
    positions = tuple(getattr(proposal, "positions", ()) or ())
    if chair is None or not positions:
        return plan

    policy = depth_policy(plan.depth)
    attempt_id = plan.seats[0].seat_id.rsplit("-position-", 1)[0] if plan.seats else "council"

    def _seat(seat_id: str, role: CouncilRole, source, *, describe: bool) -> CouncilSeat:
        return CouncilSeat(
            seat_id=seat_id,
            role=role,
            capability=source.capability,
            agent_name=source.agent_name,
            model=source.model or (plan.seats[0].model if plan.seats else ""),
            # A proposal names an agent outright, so nothing is standing in for
            # a specialist nobody registered — the badge would be a lie here.
            via_generalist=False,
            # The red team borrows the chair's agent but not its brief: its job
            # is fixed by its role, and labelling it "synthesis" would describe
            # the opposite of what it does.
            focus=source.focus if describe else "",
            proposed_brief=source.brief if describe else "",
        )

    seats = [_seat(f"{attempt_id}-position-{index}", CouncilRole.POSITION, seat, describe=True) for index, seat in enumerate(positions[: policy.max_positions], start=1)]
    seats.append(_seat(f"{attempt_id}-red-team", CouncilRole.RED_TEAM, chair, describe=False))
    seats.append(_seat(f"{attempt_id}-chair", CouncilRole.CHAIR, chair, describe=True))

    return replace(plan, seats=tuple(seats), unmet_capabilities=(), notes=(*plan.notes, *tuple(getattr(proposal, "rejected", ()) or ())))


# ---------------------------------------------------------------------------
# Recommending a depth from the request itself.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DepthRuleHit:
    """One rule that fired, and the words that fired it."""

    rule: str
    matched: str

    def as_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "matched": self.matched}


@dataclass(frozen=True, slots=True)
class DepthRecommendation:
    """A suggested depth, with the evidence for it."""

    depth: CouncilDepth
    reason: str
    rule_hits: tuple[DepthRuleHit, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "depth": self.depth.value,
            "reason": self.reason,
            "rule_hits": [hit.as_dict() for hit in self.rule_hits],
        }


#: Phrases that mean "this is a look, not a commitment".
_LIGHT_RULES: Mapping[str, str] = MappingProxyType(
    {
        "scope.quick": r"\b(quick|quickly|rough|rough[- ]cut|fast|simple|sanity[- ]check|smoke[- ]test|first[- ]pass|back[- ]of[- ]the[- ]envelope)\b",
        "scope.pilot": r"\b(pilot|prototype|proof[- ]of[- ]concept|poc|scratch|toy|throwaway|exploratory)\b",
        "scope.small": r"\b(just|only|small|minor|trivial)\b",
    }
)

#: Phrases that mean "someone outside this room will check this".
_HEAVY_RULES: Mapping[str, str] = MappingProxyType(
    {
        "stakes.published": r"\b(publication|publish|paper|manuscript|thesis|dissertation|peer[- ]review|referee)\b",
        "stakes.decision": r"\b(production|deploy|commercial|release|regulatory|audit|grant|funding|high[- ]stakes)\b",
        "scope.longitudinal": r"\b(multi[- ](season|year|site|environment)|several (seasons|years|sites)|three (seasons|years|sites)|across (seasons|years|sites|environments))\b",
        "scope.rigorous": r"\b(rigorous|comprehensive|exhaustive|thorough|definitive|full validation|cross[- ]validat\w*)\b",
    }
)


def _hits(text: str, rules: Mapping[str, str]) -> tuple[DepthRuleHit, ...]:
    found: list[DepthRuleHit] = []
    for name, pattern in rules.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            found.append(DepthRuleHit(rule=name, matched=match.group(0)))
    return tuple(found)


def recommend_depth(request_text: str) -> DepthRecommendation:
    """Suggest a depth from the request, showing which words drove it.

    Deliberately rule-based. The person reading this is about to override it or
    accept it, and "the model felt this was a light question" is not something
    anyone can argue with; a named phrase is. Ties and silence fall to medium,
    because the default has to be the setting that is rarely wrong rather than
    the cheapest or the most thorough.
    """
    text = (request_text or "").strip()
    if not text:
        return DepthRecommendation(
            depth=DEFAULT_DEPTH,
            reason="Nothing in the request indicated how much debate this needs, so it opens on the usual setting.",
        )

    light = _hits(text, _LIGHT_RULES)
    heavy = _hits(text, _HEAVY_RULES)

    # Stakes beat brevity. "A quick check before we submit the paper" is not a
    # quick question; someone outside the room is still going to read it.
    if heavy and len(heavy) >= len(light):
        phrases = ", ".join(sorted({hit.matched.lower() for hit in heavy}))
        return DepthRecommendation(
            depth=CouncilDepth.HEAVY,
            reason=f"This reads as work that has to hold up outside the project ({phrases}), so it opens on a heavy council.",
            rule_hits=heavy + light,
        )
    if light:
        phrases = ", ".join(sorted({hit.matched.lower() for hit in light}))
        return DepthRecommendation(
            depth=CouncilDepth.LIGHT,
            reason=f"This reads as a first look rather than a commitment ({phrases}), so it opens on a light council.",
            rule_hits=light,
        )
    return DepthRecommendation(
        depth=DEFAULT_DEPTH,
        reason="Nothing in the request pointed to an unusually quick or unusually high-stakes question, so it opens on the usual setting.",
    )
