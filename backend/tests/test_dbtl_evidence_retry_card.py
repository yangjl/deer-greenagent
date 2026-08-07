"""Evidence retries pause in chat for human guidance before dispatch."""

from types import SimpleNamespace

import pytest

from deerflow.agents.dbtl.live_stage.adapter import LiveStageAdapter
from deerflow.agents.dbtl.supervisor import _evidence_retry_message
from deerflow.agents.dbtl.supervisor_support.human_input_protocol import (
    EVIDENCE_RETRY_PREFIX,
)
from deerflow.dbtl.branches import BranchDecision, SupervisorBranch


def test_retry_card_names_failure_retained_evidence_and_work_to_rerun() -> None:
    decision = BranchDecision(
        branch=SupervisorBranch.CYCLE_CONTINUATION,
        route=SimpleNamespace(source="explicit", confidence=1.0),
        cycle_id="cycle-1",
    )

    _call, card = _evidence_retry_message(
        decision,
        {
            "cycle_id": "cycle-1",
            "cycle_revision": 8,
            "stage": "build",
            "dossier_hash": "a" * 64,
            "reason_codes": ["rerun_unavailable"],
            "available_artifacts": [{"path": "results.json", "content_hash": "b" * 64}],
            "initial_hint": "Keep the verified table and regenerate the notebook.",
        },
        request_nonce="run-1",
    )

    request = card.artifact["human_input"]
    assert request["request_id"].startswith(EVIDENCE_RETRY_PREFIX)
    assert request["input_mode"] == "free_text"
    assert request["dossier_hash"] == "a" * 64
    assert "rerun_unavailable" in request["context"]
    assert "results.json" in request["context"]
    assert "Nothing reruns until you submit guidance here" in request["context"]
    assert "regenerate the notebook" in request["context"]


@pytest.mark.asyncio
async def test_evidence_retry_revalidates_cycle_revision_and_dossier_hash() -> None:
    class Repo:
        async def get_cycle(self, cycle_id: str, *, project_id: str):
            return {
                "id": cycle_id,
                "db_revision": 9,
                "stages": [
                    {
                        "id": "attempt-build",
                        "stage": "build",
                        "status": "in_progress",
                    }
                ],
                "artifacts": [
                    {
                        "stage_attempt_id": "attempt-build",
                        "artifact_type": "evidence_exception",
                        "revision": 1,
                        "content_hash": "a" * 64,
                    }
                ],
            }

    adapter = LiveStageAdapter(repo=Repo(), app_config=None)
    current = {
        "stage": "build",
        "cycle_revision": 9,
        "dossier_hash": "a" * 64,
    }

    assert await adapter.validate_evidence_retry(
        project_id="project-1",
        cycle_id="cycle-1",
        request=current,
    )
    assert not await adapter.validate_evidence_retry(
        project_id="project-1",
        cycle_id="cycle-1",
        request={**current, "cycle_revision": 8},
    )
    assert not await adapter.validate_evidence_retry(
        project_id="project-1",
        cycle_id="cycle-1",
        request={**current, "dossier_hash": "c" * 64},
    )
