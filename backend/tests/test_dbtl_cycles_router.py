"""Phase 3: the durable cycle workflow API.

The demo path in the plan is driven end to end here — create, attach evidence,
approve Design, enter Reconciliation, record and resolve a blocker, reach
``ready_for_build`` — plus the two things that must fail: mutating while the
workflow is off, and acting from outside the project.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import anyio
import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import dbtl_cycles, workspaces
from deerflow.config.database_config import DatabaseConfig
from deerflow.config.dbtl_config import DbtlConfig
from deerflow.persistence.dbtl import DbtlCycleRepository
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository

_USER_ID = UUID("11111111-2222-3333-4444-555555555555")
_OTHER_USER_ID = UUID("99999999-8888-7777-6666-555555555555")


@pytest.fixture(autouse=True)
def _close_test_engine():
    yield
    anyio.run(close_engine)


def _user() -> User:
    return User(id=_USER_ID, email="breeder@example.com", password_hash="x", system_role="user")


def _other_user() -> User:
    return User(id=_OTHER_USER_ID, email="stranger@example.com", password_hash="x", system_role="user")


def test_reconciliation_evidence_references_are_individually_bounded() -> None:
    with pytest.raises(ValueError, match="at most 1000 characters"):
        dbtl_cycles.ReconciliationDecisionRequest(
            status="resolved",
            resolution="Verified.",
            evidence_refs=["x" * 1001],
            expected_db_revision=1,
            expected_work_item_revision=1,
            idempotency_key="decision-1",
        )


def test_validity_request_cannot_claim_reviewer_identity() -> None:
    with pytest.raises(ValueError, match="reviewer_user_id"):
        dbtl_cycles.ValidityAssessmentRequest(
            metrics=[],
            checks=[],
            recommendation="close_cycle",
            limitations=[],
            rationale="No claim can be made.",
            expected_db_revision=1,
            idempotency_key="validity-1",
            reviewer_user_id="someone-else",
        )


def _internal_user():
    return SimpleNamespace(id=_USER_ID, system_role="internal")


async def _make_repos(tmp_path: Path):
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    session_factory = get_session_factory()
    assert session_factory is not None
    return WorkspaceRepository(session_factory), DbtlCycleRepository(session_factory)


def _make_app(workspace_repo, cycle_repo, *, mode: str = "manual", user_factory=_user):
    app = make_authed_test_app(user_factory=user_factory)
    app.state.workspace_repo = workspace_repo
    app.state.dbtl_cycle_repo = cycle_repo
    app.state.dbtl_config_override = DbtlConfig(mode=mode)
    app.include_router(workspaces.router)
    app.include_router(dbtl_cycles.router)
    return app


def _seed_project(client) -> str:
    workspace = client.post("/api/workspaces", json={"name": "Maize Program"}).json()
    return client.post(f"/api/workspaces/{workspace['id']}/projects", json={"name": "Drought"}).json()["id"]


def _create_cycle(client, project_id: str, **overrides) -> dict:
    payload = {
        "title": "Drought tolerance screen",
        "cycle_class": "computational",
        "research_question": "Which lines hold yield under late drought?",
        "objective": "Rank 200 lines",
        "success_criteria": "Top decile reproducible across two sites",
        "idempotency_key": "create-1",
    }
    payload.update(overrides)
    return client.post(f"/api/projects/{project_id}/dbtl/cycles", json=payload).json()


def _attach(client, project_id: str, cycle_id: str, stage: str, *, seed: str = "a") -> dict:
    current = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle_id}").json()
    return client.post(
        f"/api/projects/{project_id}/dbtl/cycles/{cycle_id}/artifacts",
        json={
            "stage": stage,
            "artifact_type": f"{stage}_package",
            "uri": f"/mnt/user-data/workspace/{stage}.json",
            "content_hash": seed * 64,
            "expected_db_revision": current["db_revision"],
            "idempotency_key": f"artifact-{stage}-{seed}",
        },
    ).json()


def _declare_dataset(client, project_id: str, cycle_id: str, *, key: str, content_hash: str | None = None) -> dict:
    """Phase 6: the readiness gate refuses a reconciliation review with no inputs."""
    current = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle_id}").json()
    return client.post(
        f"/api/projects/{project_id}/dbtl/cycles/{cycle_id}/datasets",
        json={
            "source_key": "yield_trial",
            "uri": "/mnt/user-data/workspace/yield.csv",
            "content_hash": content_hash or "a" * 64,
            "expected_db_revision": current["db_revision"],
            "idempotency_key": f"dataset-{key}",
        },
    ).json()


def _approve(client, project_id: str, cycle_id: str, stage: str, revision: int, *, key: str) -> dict:
    del revision
    if stage == "reconciliation":
        _declare_dataset(client, project_id, cycle_id, key=key)
        current = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle_id}").json()
        row = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle_id}/reconciliation/rows",
            json={
                "check": "units_and_encoding",
                "field_name": "Yield units",
                "expected_db_revision": current["db_revision"],
                "idempotency_key": f"row-{key}",
            },
        ).json()
        current = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle_id}").json()
        client.post(
            f"/api/projects/{project_id}/dbtl/reconciliation/rows/{row['id']}/decide",
            json={
                "status": "resolved",
                "resolution": "Verified as Mg/ha.",
                "expected_db_revision": current["db_revision"],
                "expected_work_item_revision": row["db_revision"],
                "idempotency_key": f"decide-row-{key}",
            },
        ).raise_for_status()
    current = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle_id}").json()
    submitted = client.post(
        f"/api/projects/{project_id}/dbtl/cycles/{cycle_id}/stages/{stage}/submit",
        json={"expected_db_revision": current["db_revision"], "idempotency_key": f"submit-{key}"},
    ).json()
    return client.post(
        f"/api/projects/{project_id}/dbtl/cycles/{cycle_id}/stages/{stage}/review",
        json={
            "decision": "approve",
            "rationale": "Evidence supports the plan.",
            "expected_db_revision": submitted["db_revision"],
            "idempotency_key": f"review-{key}",
        },
    ).json()


# --------------------------------------------------------------------------
# The mode gate
# --------------------------------------------------------------------------


def test_creating_a_cycle_is_refused_while_the_workflow_is_off(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo, mode="audit_only")) as client:
        project_id = _seed_project(client)

        response = client.post(
            f"/api/projects/{project_id}/dbtl/cycles",
            json={
                "title": "T",
                "cycle_class": "computational",
                "research_question": "Q",
                "idempotency_key": "create-1",
            },
        )

    assert response.status_code == 409
    assert "dbtl.mode=manual" in response.json()["detail"]


def test_reads_stay_available_while_the_workflow_is_off(tmp_path: Path) -> None:
    """The rail must be able to say "no cycles" honestly, not just error."""
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo, mode="audit_only")) as client:
        project_id = _seed_project(client)

        response = client.get(f"/api/projects/{project_id}/dbtl/cycles")

    assert response.status_code == 200
    assert response.json()["cycles"] == []
    assert response.json()["stages"] == ["design", "reconciliation", "build", "test", "learn"]


# --------------------------------------------------------------------------
# The demo path
# --------------------------------------------------------------------------


def test_the_full_manual_path_reaches_build_readiness(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        assert cycle["state"] == "design"

        _attach(client, project_id, cycle["id"], "design")
        after_design = _approve(client, project_id, cycle["id"], "design", cycle["db_revision"], key="design")
        assert after_design["state"] == "reconciliation"

        blocker = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/work-items",
            json={
                "title": "Genotype file missing 12 entries",
                "kind": "blocker",
                "expected_db_revision": after_design["db_revision"],
                "idempotency_key": "work-1",
            },
        ).json()
        assert blocker["status"] == "open"

        resolved = client.post(
            f"/api/projects/{project_id}/dbtl/work-items/{blocker['id']}/resolve",
            json={
                "resolution": "Re-exported from the source LIMS.",
                "expected_db_revision": blocker["db_revision"],
                "expected_work_item_revision": blocker["db_revision"],
                "idempotency_key": "resolve-1",
            },
        ).json()
        assert resolved["status"] == "resolved"

        _attach(client, project_id, cycle["id"], "reconciliation", seed="b")
        detail = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}").json()
        final = _approve(client, project_id, cycle["id"], "reconciliation", detail["db_revision"], key="recon")

    assert final["state"] == "ready_for_build"


def test_activity_records_every_action_with_actor_and_revision(tmp_path: Path) -> None:
    """The human exit review reads exactly this feed."""
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        _attach(client, project_id, cycle["id"], "design")
        _approve(client, project_id, cycle["id"], "design", cycle["db_revision"], key="design")

        events = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/activity").json()["events"]

    assert [event["event_type"] for event in events] == [
        "cycle.created",
        "artifact.attached",
        "stage.submitted",
        "stage.reviewed",
    ]
    assert all(event["actor_user_id"] == str(_USER_ID) for event in events)
    assert all(event["payload"]["db_revision"] for event in events)


def test_a_review_reports_the_server_side_reviewer_not_a_client_claim(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        _attach(client, project_id, cycle["id"], "design")
        _approve(client, project_id, cycle["id"], "design", cycle["db_revision"], key="design")

        events = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/activity").json()["events"]

    review = next(event for event in events if event["event_type"] == "stage.reviewed")
    assert review["actor_user_id"] == str(_USER_ID)


def test_an_internal_principal_cannot_approve_a_stage(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo, user_factory=_internal_user)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        _attach(client, project_id, cycle["id"], "design")
        current = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}").json()
        submitted = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/stages/design/submit",
            json={"expected_db_revision": current["db_revision"], "idempotency_key": "submit-1"},
        ).json()

        response = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/stages/design/review",
            json={
                "decision": "approve",
                "rationale": "Automated approval must not count.",
                "expected_db_revision": submitted["db_revision"],
                "idempotency_key": "review-1",
            },
        )

    assert response.status_code == 403
    assert "human" in response.json()["detail"]


def test_an_internal_principal_cannot_record_validity(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(
        _make_app(
            workspace_repo,
            cycle_repo,
            user_factory=_internal_user,
        )
    ) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        response = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/test/assessment",
            json={
                "metrics": [],
                "checks": [],
                "recommendation": "close_cycle",
                "limitations": ["No independent holdout."],
                "rationale": "An internal principal must not decide this.",
                "expected_db_revision": cycle["db_revision"],
                "idempotency_key": "validity-1",
            },
        )

    assert response.status_code == 403
    assert "human" in response.json()["detail"]


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_a_reviewer_claiming_an_identity_in_the_body_is_rejected(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)

        response = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/stages/design/review",
            json={
                "decision": "approve",
                "rationale": "Fine.",
                "expected_db_revision": 1,
                "idempotency_key": "review-1",
                "reviewer_user_id": "someone-else",
            },
        )

    assert response.status_code == 422


def test_a_stale_revision_is_reported_as_a_conflict(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        _attach(client, project_id, cycle["id"], "design")
        client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/stages/design/submit",
            json={"expected_db_revision": cycle["db_revision"], "idempotency_key": "submit-1"},
        )

        response = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/stages/design/submit",
            json={"expected_db_revision": cycle["db_revision"], "idempotency_key": "submit-2"},
        )

    assert response.status_code == 409
    assert "Reload" in response.json()["detail"]


def test_a_second_active_cycle_is_reported_as_a_conflict(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        _create_cycle(client, project_id)

        response = client.post(
            f"/api/projects/{project_id}/dbtl/cycles",
            json={
                "title": "Second",
                "cycle_class": "computational",
                "research_question": "Q",
                "idempotency_key": "create-2",
            },
        )

    assert response.status_code == 409
    assert "already has an active cycle" in response.json()["detail"]


def test_approving_a_stage_with_no_evidence_is_refused(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)

        response = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/stages/design/submit",
            json={"expected_db_revision": cycle["db_revision"], "idempotency_key": "submit-1"},
        )

    assert response.status_code == 409
    assert "artifact" in response.json()["detail"]


def test_a_non_member_cannot_read_or_mutate_a_projects_cycles(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as owner:
        project_id = _seed_project(owner)
        _create_cycle(owner, project_id)

    with TestClient(_make_app(workspace_repo, cycle_repo, user_factory=_other_user)) as stranger:
        assert stranger.get(f"/api/projects/{project_id}/dbtl/cycles").status_code == 404
        assert (
            stranger.post(
                f"/api/projects/{project_id}/dbtl/cycles",
                json={
                    "title": "T",
                    "cycle_class": "computational",
                    "research_question": "Q",
                    "idempotency_key": "x",
                },
            ).status_code
            == 404
        )


@pytest.mark.parametrize("stage", ["build", "test", "learn"])
def test_a_locked_stage_cannot_be_submitted(tmp_path: Path, stage: str) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        _attach(client, project_id, cycle["id"], stage)

        response = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/stages/{stage}/submit",
            json={"expected_db_revision": cycle["db_revision"] + 1, "idempotency_key": "submit-1"},
        )

    assert response.status_code == 409


def test_an_unknown_stage_name_is_rejected_by_the_schema(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)

        response = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/stages/vibes/submit",
            json={"expected_db_revision": 1, "idempotency_key": "submit-1"},
        )

    assert response.status_code == 422


def test_a_review_without_a_rationale_is_rejected(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)

        response = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/stages/design/review",
            json={
                "decision": "approve",
                "rationale": "   ",
                "expected_db_revision": 1,
                "idempotency_key": "review-1",
            },
        )

    assert response.status_code == 422
