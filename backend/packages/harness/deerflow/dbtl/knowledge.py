"""Pure Phase 8 contracts for Learn and governed knowledge.

Learn output is provisional.  These types deliberately separate an
AI-generated candidate, a human-promoted project claim, and an explicitly
targeted publication.  Nothing in this module writes storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class KnowledgeLifecycleRefused(ValueError):
    """A requested knowledge transition is unsafe or out of order."""


KNOWLEDGE_AUTHORITY_ROLES = frozenset({"owner", "admin"})


def require_knowledge_authority(role: str) -> None:
    """Require a project role allowed to alter governed knowledge."""
    if (role or "").strip().lower() not in KNOWLEDGE_AUTHORITY_ROLES:
        raise KnowledgeLifecycleRefused("Only a project owner or administrator may promote, publish, supersede, or retract governed knowledge.")


class ClaimGrade(StrEnum):
    SUPPORTED = "supported"
    VALID_NEGATIVE = "valid_negative"
    METHODOLOGICAL = "methodological"
    QA_LESSON = "qa_lesson"


@dataclass(frozen=True, slots=True)
class CandidateEligibility:
    outcome: str
    eligible: bool
    reason: str
    allowed_grades: tuple[ClaimGrade, ...] = ()


def candidate_eligibility(outcome: str) -> CandidateEligibility:
    """Return the fail-closed promotion policy for a Test outcome."""
    normalized = (outcome or "").strip().lower()
    if normalized == "supported":
        return CandidateEligibility(
            normalized,
            True,
            "The validity pack supports a bounded positive finding.",
            (ClaimGrade.SUPPORTED, ClaimGrade.METHODOLOGICAL, ClaimGrade.QA_LESSON),
        )
    if normalized == "not_supported":
        return CandidateEligibility(
            normalized,
            True,
            "A valid negative result may become bounded project knowledge.",
            (ClaimGrade.VALID_NEGATIVE, ClaimGrade.METHODOLOGICAL, ClaimGrade.QA_LESSON),
        )
    if normalized == "inconclusive":
        return CandidateEligibility(
            normalized,
            False,
            "An inconclusive cycle may close, but it cannot create a scientific claim.",
        )
    if normalized == "invalidated":
        return CandidateEligibility(
            normalized,
            False,
            "An invalidated result cannot become a scientific claim.",
        )
    return CandidateEligibility(
        normalized or "unknown",
        False,
        "No recognized human-owned Test outcome authorizes knowledge candidates.",
    )


def validate_candidate_grade(outcome: str, grade: str) -> ClaimGrade:
    eligibility = candidate_eligibility(outcome)
    try:
        parsed = ClaimGrade(grade)
    except ValueError as exc:
        raise KnowledgeLifecycleRefused(f"Unknown claim grade {grade!r}.") from exc
    if not eligibility.eligible:
        raise KnowledgeLifecycleRefused(eligibility.reason)
    if parsed not in eligibility.allowed_grades:
        allowed = ", ".join(item.value for item in eligibility.allowed_grades)
        raise KnowledgeLifecycleRefused(f"Grade {parsed.value!r} is not compatible with outcome {eligibility.outcome!r}; choose one of: {allowed}.")
    return parsed


def render_claim_markdown(
    *,
    claim_id: str,
    statement: str,
    grade: ClaimGrade,
    evidence: list[dict[str, Any]],
    limitations: list[str],
    reviewer_user_id: str,
    status: str,
) -> str:
    """Render the portable human view; SQL remains the authority."""
    evidence_lines = [f"- `{item.get('kind', 'evidence')}` — {item.get('reference', '')}" + (f": {item.get('description')}" if item.get("description") else "") for item in evidence] or ["- No evidence reference recorded."]
    limitation_lines = [f"- {item}" for item in limitations] or ["- None recorded."]
    return "\n".join(
        [
            "# Validated project knowledge",
            "",
            f"> SQL authority: `{claim_id}` · status: **{status}** · grade: **{grade.value}**",
            "",
            statement.strip(),
            "",
            "## Evidence",
            "",
            *evidence_lines,
            "",
            "## Limitations",
            "",
            *limitation_lines,
            "",
            "## Human review",
            "",
            f"Promoted by `{reviewer_user_id}`. Publication, if any, is a separate scoped decision.",
            "",
        ]
    )


def publication_pointer(
    *,
    claim_id: str,
    source_project_id: str,
    target_project_id: str,
    statement: str,
    grade: str,
    status: str = "active",
) -> dict[str, str]:
    """The bounded retrieval projection for one selected target project."""
    return {
        "claim_id": claim_id,
        "source_project_id": source_project_id,
        "target_project_id": target_project_id,
        "statement": statement.strip(),
        "grade": grade,
        "status": status,
        "authority": "sql",
    }
