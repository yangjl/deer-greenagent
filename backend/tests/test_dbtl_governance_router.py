from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID

import anyio
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.dbtl_governance import stage_output_isolation
from app.gateway.routers import dbtl
from deerflow.config.database_config import DatabaseConfig
from deerflow.config.dbtl_config import DbtlConfig
from deerflow.persistence.dbtl import DbtlGovernanceRepository
from deerflow.persistence.engine import get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository

_USER_ID = UUID("11111111-2222-3333-4444-555555555555")
_OTHER_ID = UUID("99999999-8888-7777-6666-555555555555")


def _user(system_role="user"):
    return User(
        id=_USER_ID,
        email="breeder@example.com",
        password_hash="x",
        system_role=system_role,
    )


def _other_user():
    return User(
        id=_OTHER_ID,
        email="other@example.com",
        password_hash="x",
        system_role="user",
    )


def test_stage_output_readiness_requires_enforced_isolation() -> None:
    local_safe, _ = stage_output_isolation(
        "deerflow.sandbox.local:LocalSandboxProvider",
        allow_host_bash=False,
    )
    local_host_bash, host_detail = stage_output_isolation(
        "deerflow.sandbox.local:LocalSandboxProvider",
        allow_host_bash=True,
    )
    unknown_provider, unknown_detail = stage_output_isolation(
        "example.sandbox:UnverifiedProvider",
        allow_host_bash=False,
    )

    assert local_safe is True
    assert local_host_bash is False
    assert "allow_host_bash=true" in host_detail
    assert unknown_provider is False
    assert "does not prove" in unknown_detail


async def _seed(tmp_path):
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    sf = get_session_factory()
    assert sf is not None
    workspaces = WorkspaceRepository(sf)
    governance = DbtlGovernanceRepository(sf)
    await workspaces.create_workspace(
        workspace_id="ws-1",
        name="Maize",
        slug="maize",
        description=None,
        created_by=str(_USER_ID),
    )
    await workspaces.create_project(
        project_id="project-g2f",
        workspace_id="ws-1",
        name="G2F",
        slug="g2f",
        description=None,
        crop_profile="maize-v1",
        created_by=str(_USER_ID),
    )
    fixture = await governance.create_foundation_fixture(
        cycle_id="cycle-1",
        project_id="project-g2f",
        title="Genomic selection",
        created_by=str(_USER_ID),
        policy_version="policy-v1",
    )
    return workspaces, governance, fixture


def _app(
    workspaces,
    governance,
    tmp_path,
    *,
    user_factory=lambda: _user(),
    auth_source: str | None = None,
):
    app = make_authed_test_app(user_factory=user_factory)
    if auth_source is not None:

        @app.middleware("http")
        async def stamp_auth_source(request, call_next):
            request.state.auth_source = auth_source
            return await call_next(request)

    app.state.workspace_repo = workspaces
    app.state.dbtl_governance_repo = governance
    app.state.dbtl_root_override = tmp_path
    app.state.dbtl_database_backend = "sqlite"
    app.state.dbtl_config_override = DbtlConfig()
    app.include_router(dbtl.router)
    return app


def _payload(fixture):
    return {
        "stage_attempt_id": fixture["stage_attempt"]["id"],
        "artifact_id": fixture["artifact"]["id"],
        "artifact_revision": 1,
        "expected_db_revision": 1,
        "expected_stage_revision": 1,
        "expected_projection_hash": fixture["cycle"]["projection_hash"],
        "policy_version": "policy-v1",
        "idempotency_key": "key-1",
        "review": {
            "decision": "approve",
            "rationale": "Evidence is sufficient.",
        },
    }


def test_review_api_rejects_bare_string_and_client_identity(tmp_path) -> None:
    workspaces, governance, fixture = anyio.run(_seed, tmp_path)
    with TestClient(_app(workspaces, governance, tmp_path)) as client:
        bare = client.post(
            "/api/projects/project-g2f/dbtl/cycles/cycle-1/reviews",
            json="approved",
        )
        forged = _payload(fixture)
        forged["reviewer_user_id"] = str(_OTHER_ID)
        extra = client.post(
            "/api/projects/project-g2f/dbtl/cycles/cycle-1/reviews",
            json=forged,
        )

    assert bare.status_code == 422
    assert extra.status_code == 422


def test_review_api_rejects_blank_rationale_and_internal_principal(tmp_path) -> None:
    workspaces, governance, fixture = anyio.run(_seed, tmp_path)
    blank = _payload(fixture)
    blank["review"]["rationale"] = "   "
    with TestClient(_app(workspaces, governance, tmp_path)) as client:
        malformed = client.post(
            "/api/projects/project-g2f/dbtl/cycles/cycle-1/reviews",
            json=blank,
        )
    with TestClient(
        _app(
            workspaces,
            governance,
            tmp_path,
            auth_source="internal",
        )
    ) as internal:
        forbidden = internal.post(
            "/api/projects/project-g2f/dbtl/cycles/cycle-1/reviews",
            json=_payload(fixture),
        )

    assert malformed.status_code == 422
    assert forbidden.status_code == 403


def test_review_api_captures_authenticated_identity_and_rejects_replay(tmp_path) -> None:
    workspaces, governance, fixture = anyio.run(_seed, tmp_path)
    with TestClient(_app(workspaces, governance, tmp_path)) as client:
        accepted = client.post(
            "/api/projects/project-g2f/dbtl/cycles/cycle-1/reviews",
            json=_payload(fixture),
        )
        replay = client.post(
            "/api/projects/project-g2f/dbtl/cycles/cycle-1/reviews",
            json=_payload(fixture),
        )

    assert accepted.status_code == 201
    assert accepted.json()["reviewer_user_id"] == str(_USER_ID)
    assert replay.status_code == 409


def test_review_api_hides_cycles_from_other_projects(tmp_path) -> None:
    workspaces, governance, fixture = anyio.run(_seed, tmp_path)
    with TestClient(_app(workspaces, governance, tmp_path, user_factory=_other_user)) as client:
        response = client.post(
            "/api/projects/project-g2f/dbtl/cycles/cycle-1/reviews",
            json=_payload(fixture),
        )

    assert response.status_code == 404


def test_review_api_rejects_unauthorized_project_role(tmp_path) -> None:
    workspaces, governance, fixture = anyio.run(_seed, tmp_path)
    workspaces = AsyncMock()
    workspaces.get_project.return_value = {
        "id": "project-g2f",
        "workspace_id": "ws-1",
        "current_user_role": "viewer",
    }
    with TestClient(_app(workspaces, governance, tmp_path)) as client:
        response = client.post(
            "/api/projects/project-g2f/dbtl/cycles/cycle-1/reviews",
            json=_payload(fixture),
        )

    assert response.status_code == 403


def test_governance_validation_requires_operator_and_blocks_sqlite_cutover(tmp_path) -> None:
    workspaces, governance, _ = anyio.run(_seed, tmp_path)
    with TestClient(_app(workspaces, governance, tmp_path)) as ordinary:
        assert ordinary.post("/api/dbtl/governance/validate").status_code == 403

    with TestClient(
        _app(
            workspaces,
            governance,
            tmp_path,
            user_factory=lambda: _user("admin"),
        )
    ) as operator:
        validation = operator.post("/api/dbtl/governance/validate")
        cutover = operator.post(
            "/api/dbtl/governance/cutover",
            json={"validation_id": validation.json()["validation_id"]},
        )

    assert validation.status_code == 200
    assert validation.json()["database_backend"] == "sqlite"
    assert validation.json()["technical_ready"] is False
    assert validation.json()["total_checks"] == 10
    assert validation.json()["checks"][-1]["status"] == "waiting"
    assert validation.json()["conversational_discovery"] == {
        "rollout_stage": "server_owned_setup_fallback",
        "enabled": False,
        "classifier_entry": False,
        "automatic_offers": False,
        "project_history": False,
        "global_memory": False,
        "cycle_creation_authority": "server",
        "setup_fallback": "server_owned_confirmation_card",
        "browser_creation_authority": False,
    }
    assert "browser never creates" in validation.json()["rollback_posture"]
    assert cutover.status_code == 409
