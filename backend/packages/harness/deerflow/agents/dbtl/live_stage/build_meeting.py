"""The Build work meeting: three seats over a question one exchange cannot settle.

A Build pauses on questions of two very different sizes. "Which column is the
response?" is one sentence from the person who owns the data. "These three
implementations trade scientific fidelity against runtime and the Design does
not say which matters" is not — and answering it in one line means guessing on
behalf of somebody who would have wanted to think about it.

This meeting exists for the second kind, and three rules keep it from becoming
the first kind's expensive replacement.

**It is convened, never self-started.** No model opens a meeting by labelling
its own question complex; the option appears on a paused control and a person
chooses it. That is why this module builds units and parses a result but never
decides to run.

**It is advisory.** The chair returns options and a recommendation. It cannot
resume the Build, approve anything, or write stage evidence — the human answer
that follows is what resumes, and the recommendation is context for it. A
meeting that could resume the work would be a second, unreviewed approval path.

**It reads; it does not write.** Participants analyse recorded evidence and the
approved Design. The later phase attempt remains the single writer, so nothing
here is given a workspace grant.

Distinct from the Build *review* meeting, which annotates completed evidence
before the human gate. This one helps decide how to perform the current step.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from deerflow.dbtl.stage_runner import BUILD_WORK_MEETING_OUTPUT, WorkUnit

logger = logging.getLogger(__name__)

#: Pinned beside the other stage contracts so a recorded meeting can name what
#: it ran under.
BUILD_WORK_MEETING_CONTRACT = "generic:build-work-meeting:v1"

MAX_OPTIONS = 5
MAX_TEXT_CHARS = 1_200

_FENCE = re.compile(r"^\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*$", re.DOTALL)

#: At minimum an implementation position, an independent challenge, and a chair.
#: One reviewer is an opinion; the disagreement is what makes the recommendation
#: worth more than the worker's own next guess.
MEETING_ROLES: tuple[tuple[str, str, str], ...] = (
    (
        "position",
        "implementation",
        "Argue for the implementation you would actually choose. Name the approach, what it costs in runtime and maintenance, and what it gives up scientifically. Be concrete about the files and parameters involved.",
    ),
    (
        "red_team",
        "red-team",
        "Attack the implementation position. Name what it would get wrong, what evidence would expose that, and any cheaper approach it dismissed too quickly. Do not propose a compromise — that is the chair's job.",
    ),
    (
        "chair",
        "chair",
        "Weigh both positions and return the options a person should choose between. Keep incompatible positions distinct rather than averaging them, and say plainly which you recommend and why. You advise; the owner decides.",
    ),
)

RESULT_CONTRACT = """Return one JSON object and nothing else:

{
  "outcome": "recommendation_ready" | "needs_input" | "return_to_design",
  "summary": "two or three sentences a person can act on",
  "options": [
    {"label": "short name", "consequence": "what choosing it means for the build", "evidence": "what supports it"}
  ],
  "recommended": "the label you recommend, or an empty string",
  "reasoning": "why, in one or two sentences",
  "cannot_decide": ["anything only the project owner can settle"]
}

Use `return_to_design` only when continuing would change the approved scientific
design rather than merely implement it. Use `needs_input` when even the options
depend on something nobody in this meeting can know."""

#: Stated in the prompt rather than merely enforced downstream: a participant
#: that believes it is deciding will write as if it has.
BOUNDARIES = """What this meeting may not do:
- It may not approve, reject, or advance any stage.
- It may not change the approved design; it may only say that the design must change.
- It may not write, run, or modify anything in the project workspace.
- Its recommendation does not resume the build. The project owner's answer does."""


def _text(value: Any, *, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    trimmed = value.strip()
    return trimmed if len(trimmed) <= limit else f"{trimmed[: limit - 1]}…"


@dataclass(frozen=True, slots=True)
class MeetingOption:
    label: str
    consequence: str = ""
    evidence: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"label": self.label, "consequence": self.consequence, "evidence": self.evidence}


@dataclass(frozen=True, slots=True)
class MeetingRecommendation:
    """What the chair concluded, and what it explicitly could not settle."""

    outcome: str
    summary: str = ""
    options: tuple[MeetingOption, ...] = ()
    recommended: str = ""
    reasoning: str = ""
    cannot_decide: tuple[str, ...] = ()
    #: Set when the chair's answer could not be read. The meeting is still
    #: recorded; what is lost is the structure, not the transcript.
    refusal: str = ""

    @property
    def usable(self) -> bool:
        return not self.refusal and bool(self.summary or self.options)

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": BUILD_WORK_MEETING_CONTRACT,
            "outcome": self.outcome,
            "summary": self.summary,
            "options": [option.as_dict() for option in self.options],
            "recommended": self.recommended,
            "reasoning": self.reasoning,
            "cannot_decide": list(self.cannot_decide),
            "refusal": self.refusal,
        }

    def as_briefing(self) -> str:
        """The meeting, as the sentence a person reads above their answer.

        Options come before the recommendation on purpose: a reader who has
        already been told what to pick reads the alternatives as objections
        rather than as choices.
        """
        lines = [self.summary] if self.summary else []
        if self.options:
            lines.append("")
            lines.extend(f"- {option.label}: {option.consequence}".rstrip(": ") for option in self.options)
        if self.recommended:
            lines.extend(["", f"The meeting leans towards {self.recommended}. {self.reasoning}".strip()])
        if self.cannot_decide:
            lines.extend(["", "It could not settle:", *(f"- {item}" for item in self.cannot_decide)])
        return "\n".join(lines).strip()


_OUTCOMES = frozenset({"recommendation_ready", "needs_input", "return_to_design"})


def parse_recommendation(raw: str) -> MeetingRecommendation:
    """Read the chair's answer, or say why it could not be read.

    Never raises. A meeting whose chair returned prose is still a meeting that
    happened, and discarding the whole exchange over a malformed envelope would
    lose the argument as well as the conclusion.
    """
    text = (raw or "").strip()
    if not text:
        return MeetingRecommendation(outcome="needs_input", refusal="The meeting chair returned nothing.")
    fenced = _FENCE.match(text)
    body = fenced.group("body") if fenced else text
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return MeetingRecommendation(outcome="needs_input", summary=_text(text), refusal="The meeting chair did not return a readable result.")
    if not isinstance(payload, Mapping):
        return MeetingRecommendation(outcome="needs_input", refusal="The meeting chair's result was not an object.")

    outcome = str(payload.get("outcome") or "").strip().lower()
    options: list[MeetingOption] = []
    for entry in payload.get("options") or ():
        if not isinstance(entry, Mapping):
            continue
        label = _text(entry.get("label"), limit=120)
        if not label:
            continue
        options.append(MeetingOption(label=label, consequence=_text(entry.get("consequence"), limit=400), evidence=_text(entry.get("evidence"), limit=400)))
        if len(options) >= MAX_OPTIONS:
            break
    recommended = _text(payload.get("recommended"), limit=120)
    # A recommendation naming nothing on the table is not a recommendation. It
    # costs the label, never the meeting.
    if recommended and not any(option.label == recommended for option in options):
        recommended = ""
    return MeetingRecommendation(
        outcome=outcome if outcome in _OUTCOMES else "recommendation_ready",
        summary=_text(payload.get("summary")),
        options=tuple(options),
        recommended=recommended,
        reasoning=_text(payload.get("reasoning"), limit=600),
        cannot_decide=tuple(_text(item, limit=300) for item in (payload.get("cannot_decide") or ()) if _text(item)),
    )


@dataclass(frozen=True, slots=True)
class MeetingContext:
    """The bounded, content-addressed package the meeting is given."""

    question: str
    step_key: str
    cycle_title: str = ""
    research_question: str = ""
    objective: str = ""
    success_criteria: str = ""
    design_uri: str = ""
    design_hash: str = ""
    completed_phases: tuple[Mapping[str, Any], ...] = ()
    failures: tuple[str, ...] = ()
    prior_answers: tuple[str, ...] = ()
    workspace_note: str = ""
    manifest: Sequence[Mapping[str, Any]] = field(default_factory=tuple)

    def as_prompt_block(self) -> str:
        return json.dumps(
            {
                "question": self.question,
                "paused_step": self.step_key,
                "cycle": {
                    "title": self.cycle_title,
                    "research_question": self.research_question,
                    "objective": self.objective,
                    "success_criteria": self.success_criteria,
                },
                "approved_design": {"uri": self.design_uri, "content_hash": self.design_hash},
                "completed_phases": [dict(item) for item in self.completed_phases],
                "relevant_failures": list(self.failures),
                "prior_human_answers": list(self.prior_answers),
                "project_files": [dict(item) for item in self.manifest],
            },
            sort_keys=True,
            ensure_ascii=False,
        )


def meeting_units(
    *,
    attempt_id: str,
    agent_name: str,
    model: str,
    via_generalist: bool,
    context: MeetingContext,
) -> tuple[WorkUnit, ...]:
    """One seat per role, all reading the same recorded context."""
    header = [
        "You are one seat in a build work meeting. The build has paused on a question that one exchange could not settle.",
        "",
        f"The question: {context.question}",
        "",
        context.workspace_note,
        "",
        BOUNDARIES,
        "",
        "Recorded context:",
        context.as_prompt_block(),
    ]
    return tuple(
        WorkUnit(
            unit_id=f"{attempt_id}-meeting-{slug}",
            capability=f"build_work_meeting_{slug.replace('-', '_')}",
            agent_name=agent_name,
            prompt="\n".join([*header, "", "Your seat:", instruction, "", RESULT_CONTRACT if role == "chair" else ""]).rstrip(),
            via_generalist=via_generalist,
            model=model,
            role=role,
            focus=instruction.split(".")[0].lower(),
            round=1,
            output_contract=BUILD_WORK_MEETING_OUTPUT,
        )
        for role, slug, instruction in MEETING_ROLES
    )
