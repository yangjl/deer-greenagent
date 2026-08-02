"""Fail-safe assessment of the work remaining after a DBTL stage.

The assessment changes review ceremony, never route legality or human
authority.  A missing model, outage, timeout, or malformed reply therefore
degrades to ``standard`` rather than blocking the gate or silently making it
cheaper.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TransitionDifficulty(StrEnum):
    ROUTINE = "routine"
    STANDARD = "standard"
    HIGH_STAKES = "high_stakes"


DEFAULT_STANDARD_RATIONALE = "No valid transition assessment was available, so the standard human-review path is required."


@dataclass(frozen=True, slots=True)
class TransitionAssessment:
    difficulty: TransitionDifficulty
    rationale: str
    source: str

    def as_dict(self) -> dict[str, str]:
        return {
            "difficulty": self.difficulty.value,
            "rationale": self.rationale,
            "source": self.source,
        }


def standard_assessment(*, rationale: str = DEFAULT_STANDARD_RATIONALE, source: str = "fallback") -> TransitionAssessment:
    return TransitionAssessment(
        difficulty=TransitionDifficulty.STANDARD,
        rationale=rationale.strip() or DEFAULT_STANDARD_RATIONALE,
        source=source,
    )


def parse_transition_assessment(raw: object) -> TransitionAssessment:
    """Parse one bounded JSON result, refusing prose and unknown values."""
    if not isinstance(raw, str):
        raise ValueError("Transition assessment must be JSON text.")
    text = raw.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    payload: Any = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Transition assessment must be one JSON object.")
    try:
        difficulty = TransitionDifficulty(str(payload.get("difficulty") or "").strip().lower())
    except ValueError as exc:
        raise ValueError("Unknown transition difficulty.") from exc
    rationale = str(payload.get("rationale") or "").strip()
    if not rationale:
        raise ValueError("Transition assessment requires a rationale.")
    return TransitionAssessment(
        difficulty=difficulty,
        rationale=rationale[:2_000],
        source="model",
    )


def build_transition_assessment_prompt(
    *,
    stage: str,
    cycle: dict[str, Any],
    evidence_summary: str,
) -> str:
    """Build the one-shot prompt from bounded, already-recorded evidence."""
    cycle_context = {
        key: cycle.get(key)
        for key in (
            "id",
            "title",
            "cycle_class",
            "research_question",
            "objective",
            "success_criteria",
        )
    }
    return "\n".join(
        [
            f"Assess the work remaining after the {stage.capitalize()} evidence below.",
            "",
            "Cycle:",
            json.dumps(cycle_context, sort_keys=True, ensure_ascii=False),
            "",
            "Bound evidence summary:",
            evidence_summary[:20_000],
            "",
            "Classify only the remaining transition work:",
            "- routine: bounded, well-supported, reversible work with no material unresolved risk",
            "- standard: normal expert review, some judgement or uncertainty, no exceptional consequence",
            "- high_stakes: irreversible, safety-critical, scientifically consequential, or materially disputed work",
            "",
            "Do not propose routes and do not approve anything. Reply with exactly one JSON object:",
            '{"difficulty":"routine|standard|high_stakes","rationale":"specific evidence-bound reasons"}',
        ]
    )
