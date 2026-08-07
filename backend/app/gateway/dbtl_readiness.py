"""Read-only Phase 0 inventory for legacy GreenAgent DBTL records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from deerflow.config.dbtl_config import DbtlConfig, DbtlMode
from deerflow.dbtl.policy import DBTL_POLICY_VERSION

DBTL_TEST_OUTCOMES = ("supported", "not_supported", "inconclusive", "invalidated")
KNOWLEDGE_CANDIDATE_STATUSES = ("candidate", "promoted", "rejected", "superseded")

ReadinessClassification = Literal[
    "compatible",
    "repairable",
    "invalid_or_ambiguous",
    "safe_to_supersede",
]

_KNOWN_STATES = frozenset(
    {
        "requested",
        "designing",
        "awaiting-design-review",
        "approved-for-build",
        "build-planning",
        "building",
        "awaiting-builder-handoff",
        "test-planning",
        "testing",
        "pass",
        "awaiting-evidence-review",
        "learning",
        "awaiting-knowledge-review",
        "completed",
    }
)
_COUNTS_TEMPLATE = {
    "compatible": 0,
    "repairable": 0,
    "invalid_or_ambiguous": 0,
    "safe_to_supersede": 0,
}


class DbtlInventoryItem(BaseModel):
    path: str
    record_type: Literal["cycle", "handoff"]
    classification: ReadinessClassification
    reason: str
    record_id: str | None = None


class DbtlReadinessCheck(BaseModel):
    id: str
    status: Literal["ready", "attention"]
    summary: str


class DbtlReadinessReport(BaseModel):
    mode: DbtlMode
    mutations_enabled: bool
    graph_execution_enabled: bool
    reason: str
    policy_version: str
    test_outcomes: tuple[str, ...] = Field(default=DBTL_TEST_OUTCOMES)
    knowledge_candidate_statuses: tuple[str, ...] = Field(default=KNOWLEDGE_CANDIDATE_STATUSES)
    counts: dict[str, int]
    items: list[DbtlInventoryItem]
    checks: list[DbtlReadinessCheck]


def dbtl_mode_reason(config: DbtlConfig) -> str:
    if config.mode == "disabled":
        return "DBTL is disabled. Existing records are not executed or mutated."
    if config.mode == "audit_only":
        return "DBTL records are available for inspection, but all DBTL mutations and graph execution are disabled."
    if config.mode == "manual":
        return "Manual DBTL record maintenance is enabled, but LangGraph workflow execution remains disabled."
    return "DBTL LangGraph execution is enabled by explicit operator configuration."


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _load_record(root: Path, path: Path, record_type: Literal["cycle", "handoff"]) -> DbtlInventoryItem:
    relative = _relative_path(root, path)
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return DbtlInventoryItem(
            path=relative,
            record_type=record_type,
            classification="invalid_or_ambiguous",
            reason=f"Record cannot be parsed as JSON: {type(exc).__name__}.",
        )

    if not isinstance(body, dict):
        return DbtlInventoryItem(
            path=relative,
            record_type=record_type,
            classification="invalid_or_ambiguous",
            reason="Record root must be a JSON object.",
        )

    record_id = body.get("id") if isinstance(body.get("id"), str) else None
    if isinstance(body.get("superseded_by"), str) and body["superseded_by"].strip():
        return DbtlInventoryItem(
            path=relative,
            record_type=record_type,
            classification="safe_to_supersede",
            reason=f"Record explicitly declares successor {body['superseded_by']}.",
            record_id=record_id,
        )
    if body.get("schema_version") != 1 or not record_id:
        return DbtlInventoryItem(
            path=relative,
            record_type=record_type,
            classification="invalid_or_ambiguous",
            reason="Record is missing a supported schema_version or stable id.",
            record_id=record_id,
        )

    if record_type == "cycle":
        state = body.get("state")
        if state not in _KNOWN_STATES:
            return DbtlInventoryItem(
                path=relative,
                record_type=record_type,
                classification="repairable",
                reason=f"Cycle state {state!r} is outside the Phase 0 vocabulary and requires a human mapping decision.",
                record_id=record_id,
            )
        if not isinstance(body.get("history"), list) or not isinstance(body.get("artifacts"), dict):
            return DbtlInventoryItem(
                path=relative,
                record_type=record_type,
                classification="repairable",
                reason="Cycle has a known state but incomplete history or artifact indexes.",
                record_id=record_id,
            )
    else:
        required = ("work_item_id", "from_role", "to_role", "evidence", "authorization")
        if any(key not in body for key in required):
            return DbtlInventoryItem(
                path=relative,
                record_type=record_type,
                classification="repairable",
                reason="Handoff is readable but missing one or more Phase 0 compatibility fields.",
                record_id=record_id,
            )

    return DbtlInventoryItem(
        path=relative,
        record_type=record_type,
        classification="compatible",
        reason="Record is readable and matches the supported Phase 0 legacy shape.",
        record_id=record_id,
    )


def scan_dbtl_readiness(root: Path, config: DbtlConfig) -> DbtlReadinessReport:
    """Inspect legacy DBTL files without opening any path for writing."""
    root = root.resolve()
    greenagent = root / ".greenagent"
    candidates = [
        *((path, "cycle") for path in (greenagent / "dbtl-cycles").glob("*.json")),
        *((path, "handoff") for path in (greenagent / "handoffs").glob("*.json")),
    ]
    items = [_load_record(root, path, record_type) for path, record_type in sorted(candidates, key=lambda candidate: candidate[0].as_posix()) if path.is_file()]
    counts = dict(_COUNTS_TEMPLATE)
    for item in items:
        counts[item.classification] += 1

    policies_present = (greenagent / "policies/review-gates.yaml").is_file()
    checks = [
        DbtlReadinessCheck(
            id="execution-gate",
            status="ready" if not config.graph_execution_enabled else "attention",
            summary=("Legacy graph execution is blocked." if not config.graph_execution_enabled else "Graph execution is enabled; Phase 0 audit-only posture is not active."),
        ),
        DbtlReadinessCheck(
            id="legacy-inventory",
            status="attention" if counts["repairable"] or counts["invalid_or_ambiguous"] else "ready",
            summary=f"Inspected {len(items)} cycle and handoff record(s) without modifying them.",
        ),
        DbtlReadinessCheck(
            id="review-policy",
            status="ready" if policies_present else "attention",
            summary="Legacy review-gate policy found." if policies_present else "Legacy review-gate policy was not found.",
        ),
    ]
    return DbtlReadinessReport(
        mode=config.mode,
        mutations_enabled=config.mutations_enabled,
        graph_execution_enabled=config.graph_execution_enabled,
        reason=dbtl_mode_reason(config),
        policy_version=DBTL_POLICY_VERSION,
        counts=counts,
        items=items,
        checks=checks,
    )
