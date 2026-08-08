"""The authenticated read model a parent application consults before trusting a deck.

This endpoint is the only way a browser learns whether some HTML it is about to
render is a Design surface at all. It therefore has to be boring and strict:
membership first, project scope always, and no actionable state for a deck that
is stale, superseded, or from somewhere else.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import anyio
import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.gateway.auth.models import User
from app.gateway.routers import dbtl_cycles, workspaces
from deerflow.config.database_config import DatabaseConfig
from deerflow.config.dbtl_config import DbtlConfig
from deerflow.persistence.dbtl import DbtlCycleRepository
from deerflow.persistence.dbtl.model import DbtlReviewRow
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository
from deerflow.runtime.events.store.memory import MemoryRunEventStore

_USER_ID = UUID("11111111-2222-3333-4444-555555555555")
_OTHER_USER_ID = UUID("99999999-8888-7777-6666-555555555555")

DECK_HASH = "a" * 64
OTHER_DECK_HASH = "b" * 64
EVIDENCE_HASH = "c" * 64
DECK_URI = "/mnt/user-data/outputs/dbtl/cycle/design/design-slides-rev1-aaaaaa.html"


def test_core_evidence_wins_over_a_supplemental_exception_dossier() -> None:
    artifacts = [
        {"artifact_type": "evidence_exception", "id": "dossier"},
        {"artifact_type": "build_package", "id": "core"},
    ]

    assert dbtl_cycles._reviewable_attempt_artifacts("build", artifacts) == [artifacts[1]]


def test_exception_dossier_is_reviewable_when_no_core_evidence_exists() -> None:
    dossier = {"artifact_type": "evidence_exception", "id": "dossier"}

    assert dbtl_cycles._reviewable_attempt_artifacts("test", [dossier]) == [dossier]


def test_transition_gate_preserves_the_server_owned_evidence_exception() -> None:
    evidence_exception = {
        "version": 1,
        "stage": "test",
        "content_hash": "d" * 64,
        "recovery_options": ["retry_with_guidance", "learn_from_invalidated_evidence"],
    }

    gate = dbtl_cycles._surface_transition_gate(
        {
            "decision_request": {
                "transition_gate": {
                    "stage": "test",
                    "assessment": {
                        "difficulty": "exception",
                        "rationale": "The clean evidence contract could not be established.",
                    },
                    "routes": [],
                    "evidence_exception": evidence_exception,
                }
            }
        }
    )

    assert gate is not None
    assert gate["evidence_exception"] == evidence_exception


def test_handoff_marker_reports_exception_advance() -> None:
    cycle = {
        "stages": [
            {"stage": "build", "status": "approved"},
            {"stage": "test", "status": "advanced_with_exception"},
        ]
    }

    assert dbtl_cycles._stage_advanced_with_exception(cycle, "test") is True
    assert dbtl_cycles._stage_advanced_with_exception(cycle, "build") is False


def test_downstream_learn_deck_reviews_its_own_evidence_normally() -> None:
    dossier = {"condition": "untrusted", "content_hash": "d" * 64}

    assert dbtl_cycles._evidence_exception_review_actions(
        stage="test",
        stage_status="in_progress",
        evidence_exception=dossier,
        enabled=True,
        route_slugs={"learn_from_invalidated_evidence"},
    ) == ["retry_with_guidance", "continue_with_red_flag"]
    assert (
        dbtl_cycles._evidence_exception_review_actions(
            stage="test",
            stage_status="awaiting_review",
            evidence_exception=dossier,
            enabled=True,
            route_slugs={"advance_to_learn", "repeat_test", "close_cycle"},
        )
        is None
    )
    assert (
        dbtl_cycles._evidence_exception_review_actions(
            stage="learn",
            stage_status="in_progress",
            evidence_exception=dossier,
            enabled=True,
            route_slugs=set(),
        )
        is None
    )


def test_test_exception_without_a_reviewable_route_only_offers_retry() -> None:
    dossier = {
        "condition": "degraded_verified",
        "content_hash": "d" * 64,
        "recovery_options": ["retry_with_guidance", "hold", "close_cycle"],
    }

    assert dbtl_cycles._evidence_exception_review_actions(
        stage="test",
        stage_status="awaiting_review",
        evidence_exception=dossier,
        enabled=True,
        route_slugs={"advance_to_learn", "repeat_test", "close_cycle"},
    ) == ["retry_with_guidance"]


@pytest.fixture(autouse=True)
def _close_test_engine():
    yield
    anyio.run(close_engine)


@pytest.fixture(autouse=True)
def _stub_background_runs(monkeypatch):
    """Router tests admit deterministic runs without booting the full runtime."""
    started: list[tuple[object, str]] = []

    async def fake_start_run(body, thread_id, request):
        started.append((body, thread_id))
        return SimpleNamespace(run_id=f"run-stub-{len(started)}")

    monkeypatch.setattr(dbtl_cycles, "start_run", fake_start_run)
    return started


def _user() -> User:
    return User(id=_USER_ID, email="breeder@example.com", password_hash="x", system_role="user")


def _other_user() -> User:
    return User(id=_OTHER_USER_ID, email="stranger@example.com", password_hash="x", system_role="user")


def _internal_user():
    return SimpleNamespace(id=_USER_ID, system_role="internal")


async def _make_repos(tmp_path: Path):
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    session_factory = get_session_factory()
    assert session_factory is not None
    return WorkspaceRepository(session_factory), DbtlCycleRepository(session_factory)


def _make_app(
    workspace_repo,
    cycle_repo,
    *,
    mode: str = "manual",
    user_factory=_user,
    progressive_gate: bool = False,
):
    app = make_authed_test_app(user_factory=user_factory)
    app.state.workspace_repo = workspace_repo
    app.state.dbtl_cycle_repo = cycle_repo
    app.state.run_event_store = MemoryRunEventStore()
    app.state.dbtl_config_override = DbtlConfig(
        mode=mode,
        progressive_gate=progressive_gate,
    )
    app.include_router(workspaces.router)
    app.include_router(dbtl_cycles.router)
    return app


def _seed_project(client, *, name: str = "Drought") -> str:
    workspace = client.post("/api/workspaces", json={"name": "Maize Program"}).json()
    return client.post(f"/api/workspaces/{workspace['id']}/projects", json={"name": name}).json()["id"]


def _create_cycle(client, project_id: str, *, key: str = "create-1") -> dict:
    return client.post(
        f"/api/projects/{project_id}/dbtl/cycles",
        json={
            "title": "Drought tolerance screen",
            "cycle_class": "computational",
            "research_question": "Which lines hold yield under late drought?",
            "objective": "Rank 200 lines",
            "success_criteria": "Top decile reproducible",
            "idempotency_key": key,
        },
    ).json()


async def _register(
    repo,
    cycle: dict,
    *,
    deck_content_hash: str = DECK_HASH,
    mode: str = "chair_feedback",
    design_round: int = 1,
    **overrides,
) -> dict:
    attempt = next(item["id"] for item in cycle["stages"] if item["stage"] == "design")
    return await repo.register_stage_feedback_surface(
        stage="design",
        project_id=cycle["project_id"],
        cycle_id=cycle["id"],
        stage_attempt_id=attempt,
        design_round=design_round,
        originating_thread_id="thread-1",
        mode=mode,
        deck_uri=DECK_URI,
        deck_content_hash=deck_content_hash,
        **overrides,
    )


def _url(project_id: str, cycle_id: str, surface_id: str) -> str:
    return f"/api/projects/{project_id}/dbtl/cycles/{cycle_id}/design-feedback/{surface_id}"


def test_a_member_reads_the_surface_and_its_bindings(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        surface = anyio.run(_register, cycle_repo, cycle)

        response = client.get(_url(project_id, cycle["id"], surface["surface_id"]))

        assert response.status_code == 200
        body = response.json()
        assert body["surface_id"] == surface["surface_id"]
        assert body["deck_content_hash"] == DECK_HASH
        assert body["originating_thread_id"] == "thread-1"
        assert body["is_current"] is True


def test_the_read_model_reports_no_actions_yet(tmp_path: Path) -> None:
    """Phase 1 ships the descriptor, not the ability to answer through it."""
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        surface = anyio.run(_register, cycle_repo, cycle)

        body = client.get(_url(project_id, cycle["id"], surface["surface_id"])).json()

        assert body["allowed_actions"] == []
        assert body["interactive"] is False


def test_a_stale_surface_points_at_the_newest_one(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        first = anyio.run(_register, cycle_repo, cycle)
        second = anyio.run(partial(_register, cycle_repo, cycle, deck_content_hash=OTHER_DECK_HASH, design_round=2))

        body = client.get(_url(project_id, cycle["id"], first["surface_id"])).json()

        assert body["is_current"] is False
        assert body["superseded_by_surface_id"] == second["surface_id"]
        assert body["newest_surface_id"] == second["surface_id"]


def test_a_read_only_deck_does_not_report_the_reviewable_deck_as_replaced(tmp_path: Path) -> None:
    """Record and authority are different questions, and the note says so.

    A later round that produced no package renders a ``read_only`` deck. The
    audit record still shows it came after — ``is_current`` stays false — but
    the reviewable deck is not reported as replaced, because nothing replaced
    the package awaiting a verdict.
    """
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        artifact = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/artifacts",
            json={
                "stage": "design",
                "artifact_type": "design_brief.v2",
                "uri": "/mnt/user-data/outputs/design-review-rev1.md",
                "content_hash": EVIDENCE_HASH,
                "expected_db_revision": cycle["db_revision"],
                "idempotency_key": "artifact-1",
            },
        ).json()
        reviewable = anyio.run(
            partial(
                _register,
                cycle_repo,
                cycle,
                mode="stage_review",
                evidence_artifact_id=artifact["id"],
                evidence_artifact_revision=artifact["revision"],
                evidence_content_hash=EVIDENCE_HASH,
            )
        )
        anyio.run(partial(_register, cycle_repo, cycle, mode="read_only", deck_content_hash=OTHER_DECK_HASH, design_round=2))

        body = client.get(_url(project_id, cycle["id"], reviewable["surface_id"])).json()

    assert body["is_current"] is False
    assert body["note"] != "A newer Design round replaced this deck."


def test_a_non_member_gets_a_not_found_rather_than_the_surface(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as owner_client:
        project_id = _seed_project(owner_client)
        cycle = _create_cycle(owner_client, project_id)
        surface = anyio.run(_register, cycle_repo, cycle)

    with TestClient(_make_app(workspace_repo, cycle_repo, user_factory=_other_user)) as stranger:
        response = stranger.get(_url(project_id, cycle["id"], surface["surface_id"]))

    assert response.status_code == 404


def test_a_surface_from_another_cycle_is_not_served(tmp_path: Path) -> None:
    """The cycle in the path is part of the addressing rule, not decoration."""
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)
        other = _create_cycle(client, project_id, key="create-2")
        surface = anyio.run(_register, cycle_repo, cycle)

        response = client.get(_url(project_id, other["id"], surface["surface_id"]))

    assert response.status_code == 404


def test_an_unknown_surface_is_a_not_found(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        cycle = _create_cycle(client, project_id)

        response = client.get(_url(project_id, cycle["id"], "dfs-forged"))

    assert response.status_code == 404


def test_reads_work_while_mutations_are_disabled(tmp_path: Path) -> None:
    """A read model that vanishes in audit_only cannot report that nothing is actionable."""
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as seeded:
        project_id = _seed_project(seeded)
        cycle = _create_cycle(seeded, project_id)
        surface = anyio.run(_register, cycle_repo, cycle)

    with TestClient(_make_app(workspace_repo, cycle_repo, mode="audit_only")) as client:
        response = client.get(_url(project_id, cycle["id"], surface["surface_id"]))

    assert response.status_code == 200
    assert response.json()["surface_id"] == surface["surface_id"]


def test_an_internal_principal_may_read_but_gains_no_actions(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as seeded:
        project_id = _seed_project(seeded)
        cycle = _create_cycle(seeded, project_id)
        surface = anyio.run(_register, cycle_repo, cycle)

    with TestClient(_make_app(workspace_repo, cycle_repo, user_factory=_internal_user)) as client:
        body = client.get(_url(project_id, cycle["id"], surface["surface_id"])).json()

    assert body["allowed_actions"] == []


def test_a_recorded_chair_option_starts_one_originating_thread_run_and_watcher(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    background_watches: list[dict] = []

    async def fake_start_run(body, thread_id, request):
        assert thread_id == "thread-1"
        message = body.input["messages"][0]
        assert message["additional_kwargs"]["human_input_response"]["option_id"] == "family"
        assert 'Slide "Objectives" [objectives]: Tighten the success threshold.' in message["content"]
        return SimpleNamespace(run_id="run-resume-1")

    monkeypatch.setattr(dbtl_cycles, "start_run", fake_start_run)
    monkeypatch.setattr(
        dbtl_cycles,
        "_watch_round_if_possible",
        lambda _request, **kwargs: background_watches.append(kwargs),
    )
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        client.app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-1", "project_id": project_id})
        cycle = _create_cycle(client, project_id)
        surface = anyio.run(
            partial(
                _register,
                cycle_repo,
                cycle,
                human_input_request_id="dbtl-design__request-1",
                decision_request={
                    "question": "Which validation split?",
                    "options": [
                        {
                            "id": "family",
                            "label": "Family holdout",
                            "value": "Use family holdout.",
                        }
                    ],
                    "commentable_slides": [{"id": "objectives", "title": "Objectives"}],
                },
            )
        )

        response = client.post(
            f"{_url(project_id, cycle['id'], surface['surface_id'])}/actions",
            json={
                "version": 1,
                "action": {"kind": "chair_option", "option_ids": ["family"]},
                "comment": "Keep one site external.",
                "slide_comments": {"objectives": "Tighten the success threshold."},
                "active_slide_id": "objectives",
                "client_submission_id": "submission-1",
                "originating_thread_id": "thread-1",
                "expected_db_revision": cycle["db_revision"],
                "expected_evidence": None,
                "expected_deck_hash": DECK_HASH,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "resume_started"
    assert response.json()["run_id"] == "run-resume-1"
    assert response.json()["slide_comments"] == {"objectives": "Tighten the success threshold."}
    assert response.json()["active_slide_id"] == "objectives"
    messages = anyio.run(
        partial(
            client.app.state.run_event_store.list_messages,
            "thread-1",
            user_id=str(_USER_ID),
        )
    )
    meeting_turn = messages[-1]["content"]
    assert meeting_turn["id"].startswith("dbtl-meeting-turn__")
    assert "Design meeting · Round 1" in meeting_turn["content"]
    assert "Recorded decision: **Family holdout**." in meeting_turn["content"]
    assert "Keep one site external." in meeting_turn["content"]
    assert "follow-up slide deck below this meeting" in meeting_turn["content"]
    assert meeting_turn["additional_kwargs"]["run_id"] == "run-resume-1"
    assert "dbtl_meeting_progress" not in meeting_turn["additional_kwargs"]
    assert len(background_watches) == 1
    assert background_watches[0]["run_id"] == "run-resume-1"
    assert background_watches[0]["surface_id"] == surface["surface_id"]
    assert background_watches[0]["success_has_follow_up"] is not None


def test_a_terminal_chair_resume_without_a_followup_surface_reopens_the_same_answer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A failed chair worker must not consume the only answer surface forever."""
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    started_runs: list[str] = []

    async def fake_start_run(_body, _thread_id, _request):
        started_runs.append("started")
        return SimpleNamespace(run_id="run-resume-failed")

    monkeypatch.setattr(dbtl_cycles, "start_run", fake_start_run)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        client.app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-1", "project_id": project_id})
        client.app.state.run_store = SimpleNamespace(
            get=AsyncMock(
                return_value={
                    "run_id": "run-resume-failed",
                    "status": "success",
                }
            )
        )
        cycle = _create_cycle(client, project_id)
        surface = anyio.run(
            partial(
                _register,
                cycle_repo,
                cycle,
                human_input_request_id="dbtl-design__request-retry",
                decision_request={
                    "question": "Which validation split?",
                    "options": [
                        {
                            "id": "family",
                            "label": "Family holdout",
                            "value": "Use family holdout.",
                        }
                    ],
                },
            )
        )
        action_url = f"{_url(project_id, cycle['id'], surface['surface_id'])}/actions"

        started = client.post(
            action_url,
            json={
                "version": 1,
                "action": {"kind": "chair_option", "option_ids": ["family"]},
                "comment": "",
                "client_submission_id": "submission-retry",
                "originating_thread_id": "thread-1",
                "expected_db_revision": cycle["db_revision"],
                "expected_evidence": None,
                "expected_deck_hash": DECK_HASH,
            },
        )
        assert started.status_code == 200
        assert started.json()["status"] == "resume_started"

        cycle_repo.list_worker_runs = AsyncMock(
            return_value=[
                {
                    "unit_id": "chair-rejected",
                    "capability": "design_council_chair",
                    "status": "failed",
                    "result": {"summary": ("The worker's result did not satisfy the stage contract: Each artifact reference object must contain a non-empty string 'path'.")},
                }
            ]
        )

        reopened = client.get(f"{_url(project_id, cycle['id'], surface['surface_id'])}?viewer_thread_id=thread-1")

    assert reopened.status_code == 200
    body = reopened.json()
    assert body["interactive"] is True
    assert body["allowed_actions"] == ["chair_option"]
    assert body["receipt"]["status"] == "failed"
    assert body["receipt"]["failure_code"] == "resume_no_feedback_surface"
    assert "response was rejected" in body["note"]
    assert "artifact reference object" in body["note"]
    assert "Edit your answer" in body["note"]
    assert body["receipt"]["receipt"]["failure_detail"] == "Each artifact reference object must contain a non-empty string 'path'."

    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        client.app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-1", "project_id": project_id})
        retried = client.post(
            action_url,
            json={
                "version": 1,
                "action": {"kind": "chair_option", "option_ids": ["family"]},
                "comment": "Use family holdout, but keep one site external.",
                "client_submission_id": "submission-retry",
                "originating_thread_id": "thread-1",
                "expected_db_revision": body["current_db_revision"],
                "expected_evidence": None,
                "expected_deck_hash": DECK_HASH,
            },
        )

    assert retried.status_code == 200
    assert retried.json()["status"] == "resume_started"
    assert retried.json()["selected_card_ids"] == ["family"]
    assert retried.json()["human_comment"] == "Use family holdout, but keep one site external."
    previous_attempt = retried.json()["receipt"]["failed_attempts"][-1]
    assert previous_attempt["human_comment"] == ""
    assert "artifact reference object" in previous_attempt["receipt"]["failure_detail"]
    assert started_runs == ["started", "started"]


def test_design_submission_and_approval_are_two_bound_deck_transitions(
    tmp_path: Path,
    _stub_background_runs,
) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    evidence_hash = "c" * 64
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        client.app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-1", "project_id": project_id})
        cycle = _create_cycle(client, project_id)
        attached = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/artifacts",
            json={
                "stage": "design",
                "artifact_type": "design_brief.v2",
                "uri": "/mnt/user-data/outputs/design-review.md",
                "content_hash": evidence_hash,
                "expected_db_revision": cycle["db_revision"],
                "idempotency_key": "artifact-1",
            },
        )
        assert attached.status_code == 201
        cycle = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}").json()
        artifact = cycle["artifacts"][-1]
        surface = anyio.run(
            partial(
                _register,
                cycle_repo,
                cycle,
                mode="stage_review",
                evidence_artifact_id=artifact["id"],
                evidence_artifact_revision=artifact["revision"],
                evidence_content_hash=evidence_hash,
                decision_request={"review_issue_ids": ["issue-1"]},
            )
        )
        action_url = f"{_url(project_id, cycle['id'], surface['surface_id'])}/actions"
        common = {
            "version": 1,
            "comment": "",
            "originating_thread_id": "thread-1",
            "expected_evidence": {
                "artifact_id": artifact["id"],
                "revision": artifact["revision"],
                "content_hash": evidence_hash,
            },
            "expected_deck_hash": DECK_HASH,
        }

        submitted = client.post(
            action_url,
            json={
                **common,
                "action": {"kind": "submit_for_review", "option_ids": []},
                "client_submission_id": "submit-1",
                "expected_db_revision": cycle["db_revision"],
            },
        )
        assert submitted.status_code == 200
        submitted_cycle = submitted.json()["cycle"]
        assert next(item for item in submitted_cycle["stages"] if item["stage"] == "design")["status"] == "awaiting_review"

        reviewed = client.post(
            action_url,
            json={
                **common,
                "action": {"kind": "approve", "option_ids": []},
                "comment": "The validation split is explicit.",
                "client_submission_id": "review-1",
                "expected_db_revision": submitted_cycle["db_revision"],
            },
        )

        assert reviewed.status_code == 200
        assert reviewed.json()["status"] == "review_recorded"
        handoff_body, handoff_thread = _stub_background_runs[-1]
        assert handoff_thread == "thread-1"
        handoff_message = handoff_body.input["messages"][0]
        marker = handoff_message["additional_kwargs"]["dbtl_post_approval_handoff"]
        assert handoff_message["additional_kwargs"]["hide_from_ui"] is True
        assert marker["cycle_id"] == cycle["id"]
        assert marker["approved_stage"] == "design"
        assert marker["next_stage"] in {"reconciliation", "build"}
        assert reviewed.json()["receipt"]["handoff_run_id"].startswith("run-stub-")
        activity = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/activity").json()["events"]
        review_event = next(item for item in activity if item["event_type"] == "stage.reviewed")
        provenance = review_event["payload"]["design_feedback_provenance"]
        assert provenance["input_source"] == "design_deck"
        assert provenance["feedback_surface_id"] == surface["surface_id"]
        assert provenance["deck_content_hash"] == DECK_HASH

        # A Gateway restart can lose the process-local watcher. The durable read
        # model still recovers a terminal handoff failure without undoing the
        # review and reopens only the exact approval action for retry.
        client.app.state.run_store = SimpleNamespace(get=AsyncMock(return_value={"status": "success"}))
        anyio.run(
            partial(
                client.app.state.run_event_store.put,
                thread_id="thread-1",
                run_id=reviewed.json()["receipt"]["handoff_run_id"],
                event_type="run.delivery",
                category="outputs",
                content={"presented": 0, "paths": [], "by_tool": {}},
            )
        )
        recovered = client.get(f"{_url(project_id, cycle['id'], surface['surface_id'])}?viewer_thread_id=thread-1").json()
        assert recovered["receipt"]["status"] == "handoff_failed"
        assert recovered["allowed_actions"] == ["approve"]
        assert recovered["interactive"] is True
        assert "approval remains recorded" in recovered["note"].lower()
        assert next(item for item in client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}").json()["stages"] if item["stage"] == "design")["status"] == "approved"


def test_approved_deck_reports_handoff_only_after_card_is_in_chat_history(
    tmp_path: Path,
    _stub_background_runs,
    monkeypatch,
) -> None:
    handoff_watches: list[dict] = []

    def capture_watch(*_args, **kwargs) -> None:
        handoff_watches.append(kwargs)

    monkeypatch.setattr(dbtl_cycles, "_watch_round_if_possible", capture_watch)
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        client.app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-1", "project_id": project_id})
        cycle = _create_cycle(client, project_id)
        client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/artifacts",
            json={
                "stage": "design",
                "artifact_type": "design_brief.v2",
                "uri": "/mnt/user-data/outputs/delivered-handoff.md",
                "content_hash": EVIDENCE_HASH,
                "expected_db_revision": cycle["db_revision"],
                "idempotency_key": "artifact-delivered-handoff",
            },
        )
        cycle = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}").json()
        artifact = cycle["artifacts"][-1]
        surface = anyio.run(
            partial(
                _register,
                cycle_repo,
                cycle,
                mode="stage_review",
                evidence_artifact_id=artifact["id"],
                evidence_artifact_revision=artifact["revision"],
                evidence_content_hash=EVIDENCE_HASH,
            )
        )
        action_url = f"{_url(project_id, cycle['id'], surface['surface_id'])}/actions"
        common = {
            "version": 1,
            "comment": "",
            "originating_thread_id": "thread-1",
            "expected_evidence": {
                "artifact_id": artifact["id"],
                "revision": artifact["revision"],
                "content_hash": EVIDENCE_HASH,
            },
            "expected_deck_hash": DECK_HASH,
        }
        submitted = client.post(
            action_url,
            json={
                **common,
                "action": {"kind": "submit_for_review", "option_ids": []},
                "client_submission_id": "submit-delivered-handoff",
                "expected_db_revision": cycle["db_revision"],
            },
        ).json()
        reviewed = client.post(
            action_url,
            json={
                **common,
                "action": {"kind": "approve", "option_ids": []},
                "client_submission_id": "review-delivered-handoff",
                "expected_db_revision": submitted["cycle"]["db_revision"],
            },
        ).json()
        handoff_run_id = reviewed["receipt"]["handoff_run_id"]
        client.app.state.run_store = SimpleNamespace(get=AsyncMock(return_value={"status": "success"}))

        # Run success alone is not delivery: the authenticated surface stays
        # in the started state until the conversation's durable projection has
        # the actual Start/Hold control.
        before_delivery = client.get(f"{_url(project_id, cycle['id'], surface['surface_id'])}?viewer_thread_id=thread-1").json()
        assert before_delivery["receipt"]["receipt"]["handoff_status"] == "started"
        assert len(handoff_watches) == 1
        with pytest.raises(RuntimeError, match="still finalizing"):
            anyio.run(handoff_watches[0]["success_has_follow_up"])

        anyio.run(
            partial(
                client.app.state.run_event_store.put,
                thread_id="thread-1",
                run_id=handoff_run_id,
                event_type="tool.result",
                category="message",
                content={"artifact": {"human_input": {"clarification_type": "dbtl_stage_handoff"}}},
            )
        )
        # Inverse ordering: the failure callback already decided the card was
        # absent, but it committed before the callback's transition. The final
        # recheck promotes delivery instead of reopening approval for a
        # duplicate handoff.
        anyio.run(handoff_watches[0]["on_failure"], "success_without_follow_up")
        delivered = client.get(f"{_url(project_id, cycle['id'], surface['surface_id'])}?viewer_thread_id=thread-1").json()

        assert delivered["receipt"]["status"] == "review_recorded"
        assert delivered["receipt"]["receipt"]["handoff_status"] == "delivered"
        assert "ready in this conversation" in delivered["note"]

        # The detached failure watcher may have observed the old started row
        # before this GET proved delivery. Its later write must lose the
        # compare-and-set instead of replacing delivered with handoff_failed.
        stale_failure_receipt = {
            **before_delivery["receipt"]["receipt"],
            "handoff_status": "failed",
        }
        current, changed = anyio.run(
            partial(
                cycle_repo.transition_stage_feedback_handoff,
                "review-delivered-handoff",
                project_id=project_id,
                run_id=handoff_run_id,
                status="handoff_failed",
                receipt=stale_failure_receipt,
                failure_code="late_watcher",
            )
        )
        assert changed is False
        assert current["status"] == "review_recorded"
        assert current["receipt"]["handoff_status"] == "delivered"


def test_recorded_approval_survives_handoff_admission_failure_and_retries_exactly_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        client.app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-1", "project_id": project_id})
        cycle = _create_cycle(client, project_id)
        attached = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/artifacts",
            json={
                "stage": "design",
                "artifact_type": "design_brief.v2",
                "uri": "/mnt/user-data/outputs/handoff-retry.md",
                "content_hash": EVIDENCE_HASH,
                "expected_db_revision": cycle["db_revision"],
                "idempotency_key": "artifact-handoff-retry",
            },
        ).json()
        cycle = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}").json()
        surface = anyio.run(
            partial(
                _register,
                cycle_repo,
                cycle,
                mode="stage_review",
                evidence_artifact_id=attached["id"],
                evidence_artifact_revision=attached["revision"],
                evidence_content_hash=EVIDENCE_HASH,
                decision_request={"review_issue_ids": []},
            )
        )
        action_url = f"{_url(project_id, cycle['id'], surface['surface_id'])}/actions"
        submitted = client.post(
            action_url,
            json={
                "version": 1,
                "action": {"kind": "submit_for_review", "option_ids": []},
                "comment": "",
                "client_submission_id": "submit-before-handoff-retry",
                "originating_thread_id": "thread-1",
                "expected_db_revision": cycle["db_revision"],
                "expected_evidence": {
                    "artifact_id": attached["id"],
                    "revision": attached["revision"],
                    "content_hash": EVIDENCE_HASH,
                },
                "expected_deck_hash": DECK_HASH,
            },
        )
        assert submitted.status_code == 200, submitted.text
        cycle = submitted.json()["cycle"]
        common = {
            "version": 1,
            "action": {"kind": "approve", "option_ids": []},
            "comment": "The evidence is bounded.",
            "client_submission_id": "approval-with-retry",
            "originating_thread_id": "thread-1",
            "expected_db_revision": cycle["db_revision"],
            "expected_evidence": {
                "artifact_id": attached["id"],
                "revision": attached["revision"],
                "content_hash": EVIDENCE_HASH,
            },
            "expected_deck_hash": DECK_HASH,
        }

        async def fail_start_run(*args, **kwargs):
            raise RuntimeError("run admission unavailable")

        monkeypatch.setattr(dbtl_cycles, "start_run", fail_start_run)
        failed_handoff = client.post(action_url, json=common)

        assert failed_handoff.status_code == 200, failed_handoff.text
        assert failed_handoff.json()["status"] == "handoff_failed"
        approved_cycle = failed_handoff.json()["cycle"]
        approved_revision = approved_cycle["db_revision"]
        assert next(item for item in approved_cycle["stages"] if item["stage"] == "design")["status"] == "approved"

        read = client.get(f"{_url(project_id, cycle['id'], surface['surface_id'])}?viewer_thread_id=thread-1").json()
        assert read["allowed_actions"] == ["approve"]
        assert read["receipt"]["expected_db_revision"] == common["expected_db_revision"]

        started: list[tuple[object, str]] = []

        async def succeed_start_run(body, thread_id, request):
            started.append((body, thread_id))
            return SimpleNamespace(run_id="run-handoff-retry")

        monkeypatch.setattr(dbtl_cycles, "start_run", succeed_start_run)
        retried = client.post(action_url, json=common)

        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "review_recorded"
        assert retried.json()["replayed"] is True
        assert retried.json()["cycle"]["db_revision"] == approved_revision
        assert len(started) == 1
        activity = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/activity").json()["events"]
        assert len([item for item in activity if item["event_type"] == "stage.reviewed"]) == 1


def test_routine_progressive_gate_records_submit_and_approval_in_one_action(
    tmp_path: Path,
) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo, progressive_gate=True)) as client:
        project_id = _seed_project(client)
        client.app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-1", "project_id": project_id})
        cycle = _create_cycle(client, project_id)
        attached = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/artifacts",
            json={
                "stage": "design",
                "artifact_type": "design_brief.v2",
                "uri": "/mnt/user-data/outputs/design-review.md",
                "content_hash": EVIDENCE_HASH,
                "expected_db_revision": cycle["db_revision"],
                "idempotency_key": "artifact-routine",
            },
        ).json()
        cycle = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}").json()
        gate = {
            "stage": "design",
            "assessment": {
                "difficulty": "routine",
                "rationale": "All evidence is bounded and the next action is reversible.",
                "source": "model",
            },
            "routes": [
                {
                    "slug": "advance",
                    "to_stage": "build",
                    "label": "Continue to Build",
                    "value": "Approve Design and move toward Build.",
                },
                {
                    "slug": "park",
                    "to_stage": "design",
                    "label": "Park",
                    "value": "Work with the lead agent.",
                },
            ],
        }
        surface = anyio.run(
            partial(
                _register,
                cycle_repo,
                cycle,
                mode="stage_review",
                evidence_artifact_id=attached["id"],
                evidence_artifact_revision=attached["revision"],
                evidence_content_hash=EVIDENCE_HASH,
                decision_request={
                    "review_issue_ids": [],
                    "transition_gate": gate,
                },
            )
        )
        payload = {
            "version": 1,
            "action": {
                "kind": "advance",
                "option_ids": [],
                "difficulty_override": None,
            },
            "comment": "",
            "client_submission_id": "advance-routine",
            "originating_thread_id": "thread-1",
            "expected_db_revision": cycle["db_revision"],
            "expected_evidence": {
                "artifact_id": attached["id"],
                "revision": attached["revision"],
                "content_hash": EVIDENCE_HASH,
            },
            "expected_deck_hash": DECK_HASH,
        }
        response = client.post(
            f"{_url(project_id, cycle['id'], surface['surface_id'])}/actions",
            json=payload,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "review_recorded"
        design = next(item for item in body["cycle"]["stages"] if item["stage"] == "design")
        assert design["status"] == "approved"
        activity = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/activity").json()["events"]
        reviewed = [item for item in activity if item["event_type"] == "stage.reviewed"]
        submitted = [item for item in activity if item["event_type"] == "stage.submitted"]
        assert len(reviewed) == 1
        assert submitted == []
        assert reviewed[0]["payload"]["auto_submit"] is True

        async def load_review():
            session_factory = get_session_factory()
            assert session_factory is not None
            async with session_factory() as session:
                return await session.scalar(select(DbtlReviewRow).where(DbtlReviewRow.cycle_id == cycle["id"]))

        review = anyio.run(load_review)
        assert review is not None
        assert review.bound_db_revision == cycle["db_revision"] + 1
        assert review.bound_stage_revision == cycle["db_revision"] + 1
        assert review.artifact_id == attached["id"]
        assert review.artifact_revision == attached["revision"]
        assert review.feedback_surface_id == surface["surface_id"]
        assert review.deck_content_hash == DECK_HASH
        assert review.selected_action == "advance"
        detail = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}").json()
        transition = detail["transitions"][-1]
        assert transition["assessed_difficulty"] == "routine"
        assert transition["assessment_rationale"] == gate["assessment"]["rationale"]
        assert transition["human_override"] is None
        assert transition["evidence_hash"] == EVIDENCE_HASH


def test_a_standard_gate_approves_in_one_action_too(tmp_path: Path) -> None:
    """The card offers one Approve, whatever depth the agent assessed.

    Depth is advice about how carefully to read, not a different number of
    clicks: a standard assessment used to demand submit-then-approve while a
    routine one recorded both at once, which made the same decision cost
    different work for no reason a reviewer could act on.
    """
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo, progressive_gate=True)) as client:
        project_id = _seed_project(client)
        client.app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-1", "project_id": project_id})
        cycle = _create_cycle(client, project_id)
        attached = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/artifacts",
            json={
                "stage": "design",
                "artifact_type": "design_brief.v2",
                "uri": "/mnt/user-data/outputs/standard-design.md",
                "content_hash": EVIDENCE_HASH,
                "expected_db_revision": cycle["db_revision"],
                "idempotency_key": "artifact-standard",
            },
        ).json()
        cycle = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}").json()
        surface = anyio.run(
            partial(
                _register,
                cycle_repo,
                cycle,
                mode="stage_review",
                evidence_artifact_id=attached["id"],
                evidence_artifact_revision=attached["revision"],
                evidence_content_hash=EVIDENCE_HASH,
                decision_request={
                    "review_issue_ids": [],
                    "transition_gate": {
                        "stage": "design",
                        "assessment": {
                            "difficulty": "standard",
                            "rationale": "Two positions remain contested.",
                            "source": "model",
                        },
                        "routes": [
                            {
                                "slug": "advance",
                                "to_stage": "build",
                                "label": "Continue to Build",
                                "value": "Approve Design.",
                            },
                            {"slug": "park", "to_stage": "design", "label": "Park"},
                        ],
                    },
                },
            )
        )
        read = client.get(_url(project_id, cycle["id"], surface["surface_id"]) + "?viewer_thread_id=thread-1").json()
        assert set(read["allowed_actions"]) >= {"advance", "request_changes", "park"}

        response = client.post(
            f"{_url(project_id, cycle['id'], surface['surface_id'])}/actions",
            json={
                "version": 1,
                "action": {"kind": "advance", "option_ids": []},
                "comment": "",
                "client_submission_id": "advance-standard",
                "originating_thread_id": "thread-1",
                "expected_db_revision": cycle["db_revision"],
                "expected_evidence": {
                    "artifact_id": attached["id"],
                    "revision": attached["revision"],
                    "content_hash": EVIDENCE_HASH,
                },
                "expected_deck_hash": DECK_HASH,
            },
        )

        assert response.status_code == 200, response.text
        design = next(item for item in response.json()["cycle"]["stages"] if item["stage"] == "design")
        assert design["status"] == "approved"


def test_a_stale_blocked_build_edge_cannot_restore_the_removed_gate(tmp_path: Path) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo, progressive_gate=True)) as client:
        project_id = _seed_project(client)
        client.app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-1", "project_id": project_id})
        cycle = _create_cycle(client, project_id)
        attached = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/artifacts",
            json={
                "stage": "design",
                "artifact_type": "design_brief.v2",
                "uri": "/mnt/user-data/outputs/blocked-build.md",
                "content_hash": EVIDENCE_HASH,
                "expected_db_revision": cycle["db_revision"],
                "idempotency_key": "artifact-blocked",
            },
        ).json()
        cycle = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}").json()
        surface = anyio.run(
            partial(
                _register,
                cycle_repo,
                cycle,
                mode="stage_review",
                evidence_artifact_id=attached["id"],
                evidence_artifact_revision=attached["revision"],
                evidence_content_hash=EVIDENCE_HASH,
                decision_request={
                    "review_issue_ids": [],
                    "transition_gate": {
                        "stage": "design",
                        "assessment": {"difficulty": "standard", "rationale": "Ordinary review."},
                        "routes": [
                            {
                                "slug": "advance",
                                "to_stage": "build",
                                "label": "Continue to Build",
                                "blocked": True,
                                "blocked_reason": "Build is locked until every required reconciliation matrix row is settled.",
                            },
                            {"slug": "park", "to_stage": "design", "label": "Park"},
                        ],
                    },
                },
            )
        )
        read = client.get(_url(project_id, cycle["id"], surface["surface_id"]) + "?viewer_thread_id=thread-1").json()

        # The verdict remains server-owned; stale deck metadata cannot restore
        # reconciliation as a runtime prerequisite.
        assert "approve" in read["allowed_actions"]
        assert "advance" in read["allowed_actions"]

        response = client.post(
            f"{_url(project_id, cycle['id'], surface['surface_id'])}/actions",
            json={
                "version": 1,
                "action": {"kind": "approve", "option_ids": []},
                "comment": "",
                "client_submission_id": "approve-while-build-blocked",
                "originating_thread_id": "thread-1",
                "expected_db_revision": cycle["db_revision"],
                "expected_evidence": {
                    "artifact_id": attached["id"],
                    "revision": attached["revision"],
                    "content_hash": EVIDENCE_HASH,
                },
                "expected_deck_hash": DECK_HASH,
            },
        )

        assert response.status_code == 200, response.text
        stages = {item["stage"]: item["status"] for item in response.json()["cycle"]["stages"]}
        assert stages["design"] == "approved"
        assert stages["reconciliation"] == "locked"
        assert stages["build"] == "in_progress"


def test_revise_records_the_verdict_without_a_separate_submit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Revise is the second of three answers, not a two-step ceremony."""
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo, progressive_gate=True)) as client:
        project_id = _seed_project(client)
        client.app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-1", "project_id": project_id})
        started: dict[str, object] = {}

        async def fake_start_run(body, thread_id, request):
            started["thread_id"] = thread_id
            started["body"] = body
            return SimpleNamespace(run_id="run-revise")

        monkeypatch.setattr(dbtl_cycles, "start_run", fake_start_run)
        cycle = _create_cycle(client, project_id)
        attached = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/artifacts",
            json={
                "stage": "design",
                "artifact_type": "design_brief.v2",
                "uri": "/mnt/user-data/outputs/revise-design.md",
                "content_hash": EVIDENCE_HASH,
                "expected_db_revision": cycle["db_revision"],
                "idempotency_key": "artifact-revise",
            },
        ).json()
        cycle = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}").json()
        surface = anyio.run(
            partial(
                _register,
                cycle_repo,
                cycle,
                mode="stage_review",
                evidence_artifact_id=attached["id"],
                evidence_artifact_revision=attached["revision"],
                evidence_content_hash=EVIDENCE_HASH,
                decision_request={
                    "review_issue_ids": [],
                    "commentable_slides": [{"id": "objectives", "title": "Objectives"}],
                    "transition_gate": {
                        "stage": "design",
                        "assessment": {
                            "difficulty": "standard",
                            "rationale": "Ordinary review.",
                            "source": "model",
                        },
                        "routes": [
                            {"slug": "advance", "to_stage": "build", "label": "Continue to Build"},
                            {"slug": "park", "to_stage": "design", "label": "Park"},
                        ],
                    },
                },
            )
        )

        response = client.post(
            f"{_url(project_id, cycle['id'], surface['surface_id'])}/actions",
            json={
                "version": 1,
                "action": {"kind": "request_changes", "option_ids": []},
                "comment": "",
                "slide_comments": {"objectives": "The holdout is not separated by family."},
                "active_slide_id": "objectives",
                "client_submission_id": "revise-once",
                "originating_thread_id": "thread-1",
                "expected_db_revision": cycle["db_revision"],
                "expected_evidence": {
                    "artifact_id": attached["id"],
                    "revision": attached["revision"],
                    "content_hash": EVIDENCE_HASH,
                },
                "expected_deck_hash": DECK_HASH,
            },
        )

        assert response.status_code == 200
        design = next(item for item in response.json()["cycle"]["stages"] if item["stage"] == "design")
        assert design["status"] == "changes_requested"
        # One recorded review, no separate submitted event, and the reviewer's
        # words reach the refinement run verbatim.
        activity = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/activity").json()["events"]
        assert [item for item in activity if item["event_type"] == "stage.submitted"] == []
        assert len([item for item in activity if item["event_type"] == "stage.reviewed"]) == 1
        assert started["thread_id"] == "thread-1"
        content = started["body"].input["messages"][0]["content"]
        assert 'Slide "Objectives" [objectives]' in content
        assert "The holdout is not separated by family." in content


def test_a_high_stakes_approval_requires_the_reviewers_written_rationale(
    tmp_path: Path,
) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    with TestClient(_make_app(workspace_repo, cycle_repo, progressive_gate=True)) as client:
        project_id = _seed_project(client)
        client.app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-1", "project_id": project_id})
        cycle = _create_cycle(client, project_id)
        artifact = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/artifacts",
            json={
                "stage": "design",
                "artifact_type": "design_brief.v2",
                "uri": "/mnt/user-data/outputs/high-stakes.md",
                "content_hash": EVIDENCE_HASH,
                "expected_db_revision": cycle["db_revision"],
                "idempotency_key": "artifact-high",
            },
        ).json()
        cycle = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}").json()
        surface = anyio.run(
            partial(
                _register,
                cycle_repo,
                cycle,
                mode="stage_review",
                evidence_artifact_id=artifact["id"],
                evidence_artifact_revision=artifact["revision"],
                evidence_content_hash=EVIDENCE_HASH,
                decision_request={
                    "review_issue_ids": [],
                    "transition_gate": {
                        "stage": "design",
                        "assessment": {
                            "difficulty": "high_stakes",
                            "rationale": "The decision changes an irreversible field protocol.",
                            "source": "model",
                        },
                        "routes": [
                            {
                                "slug": "advance",
                                "to_stage": "build",
                                "label": "Continue to Build",
                                "value": "Advance.",
                            }
                        ],
                    },
                },
            )
        )
        action_url = f"{_url(project_id, cycle['id'], surface['surface_id'])}/actions"
        base = {
            "version": 1,
            "comment": "",
            "originating_thread_id": "thread-1",
            "expected_db_revision": cycle["db_revision"],
            "expected_evidence": {
                "artifact_id": artifact["id"],
                "revision": artifact["revision"],
                "content_hash": EVIDENCE_HASH,
            },
            "expected_deck_hash": DECK_HASH,
        }
        refused = client.post(
            action_url,
            json={
                **base,
                "action": {"kind": "advance", "option_ids": []},
                "client_submission_id": "high-no-rationale",
            },
        )
        assert refused.status_code == 409

        accepted = client.post(
            action_url,
            json={
                **base,
                "comment": "Reviewed the field protocol with the station lead.",
                "action": {
                    "kind": "advance",
                    "option_ids": [],
                    "difficulty_override": "routine",
                },
                "client_submission_id": "high-with-rationale",
            },
        )
        assert accepted.status_code == 200
        detail = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}").json()
        # The assessment is what the agent judged; an override, when a caller
        # still supplies one, is recorded beside it rather than replacing it.
        assert detail["transitions"][-1]["assessed_difficulty"] == "high_stakes"
        assert detail["transitions"][-1]["human_override"] == "routine"


def test_request_changes_starts_a_focused_run_in_the_originating_thread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_repo, cycle_repo = anyio.run(_make_repos, tmp_path)
    started: list[object] = []

    async def fake_start_run(body, thread_id, request):
        assert thread_id == "thread-1"
        message = body.input["messages"][0]
        assert message["additional_kwargs"]["hide_from_ui"] is True
        assert message["additional_kwargs"]["dbtl_design_kickoff"] is True
        assert "issue-1" in message["content"]
        assert "Selected contested Design issues require refinement" in message["content"]
        started.append(body)
        return SimpleNamespace(run_id="run-refinement-1")

    monkeypatch.setattr(dbtl_cycles, "start_run", fake_start_run)
    evidence_hash = "d" * 64
    with TestClient(_make_app(workspace_repo, cycle_repo)) as client:
        project_id = _seed_project(client)
        client.app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-1", "project_id": project_id})
        cycle = _create_cycle(client, project_id)
        attached = client.post(
            f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}/artifacts",
            json={
                "stage": "design",
                "artifact_type": "design_brief.v2",
                "uri": "/mnt/user-data/outputs/design-review.md",
                "content_hash": evidence_hash,
                "expected_db_revision": cycle["db_revision"],
                "idempotency_key": "artifact-refinement",
            },
        )
        assert attached.status_code == 201
        cycle = client.get(f"/api/projects/{project_id}/dbtl/cycles/{cycle['id']}").json()
        artifact = cycle["artifacts"][-1]
        surface = anyio.run(
            partial(
                _register,
                cycle_repo,
                cycle,
                mode="stage_review",
                evidence_artifact_id=artifact["id"],
                evidence_artifact_revision=artifact["revision"],
                evidence_content_hash=evidence_hash,
                decision_request={"review_issue_ids": ["issue-1"]},
            )
        )
        action_url = f"{_url(project_id, cycle['id'], surface['surface_id'])}/actions"
        binding = {
            "version": 1,
            "originating_thread_id": "thread-1",
            "expected_evidence": {
                "artifact_id": artifact["id"],
                "revision": artifact["revision"],
                "content_hash": evidence_hash,
            },
            "expected_deck_hash": DECK_HASH,
        }
        submitted = client.post(
            action_url,
            json={
                **binding,
                "action": {"kind": "submit_for_review", "option_ids": []},
                "comment": "",
                "client_submission_id": "submit-refinement",
                "expected_db_revision": cycle["db_revision"],
            },
        )
        assert submitted.status_code == 200

        reviewed = client.post(
            action_url,
            json={
                **binding,
                "action": {
                    "kind": "request_changes",
                    "option_ids": ["issue-1"],
                },
                "comment": "",
                "client_submission_id": "review-refinement",
                "expected_db_revision": submitted.json()["cycle"]["db_revision"],
            },
        )

    assert reviewed.status_code == 200
    assert reviewed.json()["run_id"] == "run-refinement-1"
    assert reviewed.json()["receipt"]["run_id"] == "run-refinement-1"
    assert len(started) == 1
