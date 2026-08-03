"""Phase 4: the shadow-evaluation and proposal API.

The demo path is exercised end to end — ordinary requests stay ordinary, a
research request produces a proposal, an explicit request goes straight to
setup, a selected cycle continues rather than forking — and so are the two
things that must never happen: an evaluation creating a durable record, and a
confident classifier bypassing confirmation.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import anyio
import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.gateway.auth.models import User
from app.gateway.routers import dbtl_proposals, workspaces
from deerflow.config.database_config import DatabaseConfig
from deerflow.config.dbtl_config import DbtlConfig
from deerflow.persistence.dbtl import DbtlCycleRepository
from deerflow.persistence.dbtl.model import DbtlCycleRow
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.telemetry import ClassifierEvaluationRepository
from deerflow.persistence.workspaces import WorkspaceRepository

_USER_ID = UUID("11111111-2222-3333-4444-555555555555")
_OTHER_USER_ID = UUID("99999999-8888-7777-6666-555555555555")

RESEARCH_TEXT = "Design and validate a genomic-selection experiment"
ORDINARY_TEXT = "Explain this README"


@pytest.fixture(autouse=True)
def _close_test_engine():
    yield
    anyio.run(close_engine)


def _user() -> User:
    return User(id=_USER_ID, email="breeder@example.com", password_hash="x", system_role="user")


def _other_user() -> User:
    return User(id=_OTHER_USER_ID, email="stranger@example.com", password_hash="x", system_role="user")


def _admin_user() -> User:
    return User(id=_USER_ID, email="admin@example.com", password_hash="x", system_role="admin")


async def _make_repos(tmp_path: Path):
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    session_factory = get_session_factory()
    assert session_factory is not None
    return WorkspaceRepository(session_factory), ClassifierEvaluationRepository(session_factory), session_factory


def _make_app(workspace_repo, evaluation_repo, *, mode: str = "manual", proposals_visible: bool = True, user_factory=_user):
    app = make_authed_test_app(user_factory=user_factory)
    app.state.workspace_repo = workspace_repo
    app.state.classifier_evaluation_repo = evaluation_repo
    session_factory = get_session_factory()
    app.state.dbtl_cycle_repo = DbtlCycleRepository(session_factory) if session_factory is not None else None
    app.state.dbtl_config_override = DbtlConfig(mode=mode, proposals_visible=proposals_visible)
    app.include_router(workspaces.router)
    app.include_router(dbtl_proposals.router)
    return app


def _seed_project(client) -> str:
    workspace = client.post("/api/workspaces", json={"name": "Maize Program"}).json()
    return client.post(f"/api/workspaces/{workspace['id']}/projects", json={"name": "Drought"}).json()["id"]


def _evaluate(client, project_id: str, **overrides) -> dict:
    payload = {"text": RESEARCH_TEXT, "thread_id": "thread-1", "idempotency_key": "eval-1"}
    payload.update(overrides)
    response = client.post(f"/api/projects/{project_id}/dbtl/proposals/evaluate", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# ── The demo path ────────────────────────────────────────────────────────


def test_ordinary_requests_produce_no_proposal(tmp_path: Path) -> None:
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        for index, text in enumerate(("Explain this README", "Create a small chart")):
            body = _evaluate(client, project_id, text=text, idempotency_key=f"ordinary-{index}")
            assert body["route_kind"] == "ordinary"
            assert body["proposal"] is None


def test_a_research_request_produces_a_no_record_proposal(tmp_path: Path) -> None:
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        body = _evaluate(client, project_id)

        assert body["route_kind"] == "proposal"
        proposal = body["proposal"]
        assert proposal is not None
        assert proposal["creates_record"] is False
        assert proposal["notice"] == "No cycle has been created yet."
        assert proposal["requires_confirmation"] is True
        assert "target trait" in proposal["missing_fields"]


def test_a_data_request_produces_a_high_confidence_proposal(tmp_path: Path) -> None:
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        body = _evaluate(
            client,
            project_id,
            text=("Can plant height and leaf count predict grain yield well enough to pre-screen genotypes before harvest? Data is trial_2025_yield.csv."),
            idempotency_key="data-request",
        )

        assert body["route_kind"] == "proposal"
        assert body["proposal"] is not None


def test_fresh_project_prior_is_reflected_in_shadow_evaluation(tmp_path: Path) -> None:
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        body = _evaluate(
            client,
            project_id,
            text="Evaluate the trial",
            is_new_conversation=True,
        )

        assert body["route_kind"] == "proposal"
        assert body["proposal"] is not None


def test_an_explicit_request_routes_to_setup_without_classification(tmp_path: Path) -> None:
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        body = _evaluate(client, project_id, text="start a DBTL cycle")
        assert body["route_kind"] == "cycle_setup"
        assert body["route_source"] == "explicit_request"


def test_a_selected_cycle_produces_a_continuation_not_a_new_cycle(tmp_path: Path) -> None:
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        body = _evaluate(client, project_id, selected_cycle_id="cycle-3")
        assert body["route_kind"] == "cycle_continuation"
        assert body["proposal"] is None


def test_an_explicit_choice_overrides_the_classifier(tmp_path: Path) -> None:
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        body = _evaluate(client, project_id, explicit_choice="ordinary")
        assert body["route_kind"] == "ordinary"
        assert body["route_source"] == "explicit_choice"
        assert body["proposal"] is None


# ── The no-go ────────────────────────────────────────────────────────────


def test_evaluating_never_creates_a_dbtl_record(tmp_path: Path) -> None:
    """The Phase 4 no-go, checked against the database rather than the response."""
    workspace_repo, evaluation_repo, session_factory = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        for index in range(5):
            _evaluate(client, project_id, idempotency_key=f"eval-{index}")

    async def _count() -> int:
        async with session_factory() as session:
            return int((await session.execute(select(func.count()).select_from(DbtlCycleRow))).scalar_one())

    assert anyio.run(_count) == 0


def test_a_proposal_is_never_returned_when_proposals_are_not_visible(tmp_path: Path) -> None:
    """Shadow mode still measures; it just does not interrupt anyone."""
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo, proposals_visible=False)) as client:
        project_id = _seed_project(client)
        body = _evaluate(client, project_id)
        assert body["proposal"] is None
        assert body["route_kind"] == "proposal", "the evaluation is still recorded honestly"
        assert body["proposals_visible"] is False


def test_proposals_are_not_shown_while_the_workflow_is_off(tmp_path: Path) -> None:
    """Offering an upgrade that cannot be accepted would be a dead end."""
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo, mode="audit_only")) as client:
        project_id = _seed_project(client)
        body = _evaluate(client, project_id)
        assert body["proposal"] is None
        assert body["proposals_visible"] is False


# ── Outcomes ─────────────────────────────────────────────────────────────


def test_a_human_choice_is_recorded_against_its_evaluation(tmp_path: Path) -> None:
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        body = _evaluate(client, project_id)
        response = client.post(
            f"/api/projects/{project_id}/dbtl/proposals/{body['evaluation_id']}/outcome",
            json={"outcome": "keep_ordinary"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["human_choice"] == "keep_ordinary"


def test_dismissal_is_a_recordable_outcome(tmp_path: Path) -> None:
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        body = _evaluate(client, project_id)
        response = client.post(
            f"/api/projects/{project_id}/dbtl/proposals/{body['evaluation_id']}/outcome",
            json={"outcome": "dismissed"},
        )
        assert response.status_code == 200
        assert response.json()["human_choice"] == "dismissed"


def test_an_unknown_outcome_is_rejected(tmp_path: Path) -> None:
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        body = _evaluate(client, project_id)
        response = client.post(
            f"/api/projects/{project_id}/dbtl/proposals/{body['evaluation_id']}/outcome",
            json={"outcome": "created_the_cycle"},
        )
        assert response.status_code == 422


def test_an_outcome_for_an_unknown_evaluation_is_404(tmp_path: Path) -> None:
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        response = client.post(
            f"/api/projects/{project_id}/dbtl/proposals/eval-missing/outcome",
            json={"outcome": "keep_ordinary"},
        )
        assert response.status_code == 404


# ── Access ───────────────────────────────────────────────────────────────


def test_a_non_member_cannot_evaluate_against_a_project(tmp_path: Path) -> None:
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)

    with TestClient(_make_app(workspace_repo, evaluation_repo, user_factory=_other_user)) as stranger:
        response = stranger.post(
            f"/api/projects/{project_id}/dbtl/proposals/evaluate",
            json={"text": RESEARCH_TEXT, "idempotency_key": "x"},
        )
        assert response.status_code == 404


def test_the_evaluation_drawer_is_admin_only(tmp_path: Path) -> None:
    """ "Available only to authorized testers" — enforced, not just hidden."""
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        _evaluate(client, project_id)
        assert client.get(f"/api/projects/{project_id}/dbtl/proposals/evaluations").status_code == 403

    with TestClient(_make_app(workspace_repo, evaluation_repo, user_factory=_admin_user)) as admin:
        response = admin.get(f"/api/projects/{project_id}/dbtl/proposals/evaluations")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["evaluations"]
        assert set(payload["stats"]) >= {
            "proposed",
            "classifier_ordinary",
            "false_upgrades",
            "missed_cycles",
            "decided",
        }
        row = payload["evaluations"][0]
        assert {"route_kind", "route_source", "band", "confidence", "rule_hits", "human_choice"} <= set(row)


def test_the_request_body_forbids_unknown_fields(tmp_path: Path) -> None:
    """A client must not be able to smuggle a decision into an evaluation."""
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        response = client.post(
            f"/api/projects/{project_id}/dbtl/proposals/evaluate",
            json={"text": RESEARCH_TEXT, "idempotency_key": "x", "creates_record": True},
        )
        assert response.status_code == 422


def test_replaying_an_evaluation_returns_the_same_record(tmp_path: Path) -> None:
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        first = _evaluate(client, project_id)
        second = _evaluate(client, project_id)
        assert first["evaluation_id"] == second["evaluation_id"]


def test_an_evaluation_key_cannot_be_reused_for_different_text(tmp_path: Path) -> None:
    workspace_repo, evaluation_repo, _ = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, evaluation_repo)) as client:
        project_id = _seed_project(client)
        _evaluate(client, project_id)
        response = client.post(
            f"/api/projects/{project_id}/dbtl/proposals/evaluate",
            json={
                "text": ORDINARY_TEXT,
                "thread_id": "thread-1",
                "idempotency_key": "eval-1",
            },
        )
        assert response.status_code == 409
        assert "different request" in response.json()["detail"]
