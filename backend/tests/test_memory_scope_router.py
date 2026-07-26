"""Phase 2: the project's Memory scope migration API.

Privacy is the whole point of this surface. The landing view is counts only;
a fact's body reaches exactly one person — its owner. Bulk sharing does not
exist, and every mutation requires project membership.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

import anyio
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import memory_scope, workspaces
from deerflow.agents.memory.scope import scoped_memory_user_id
from deerflow.agents.memory.scopes import bind_scope, shared_project_scope
from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.engine import get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository

_USER_ID = UUID("11111111-2222-3333-4444-555555555555")
_OTHER_USER_ID = UUID("99999999-8888-7777-6666-555555555555")


def _user() -> User:
    return User(id=_USER_ID, email="breeder@example.com", password_hash="x", system_role="user")


def _other_user() -> User:
    return User(id=_OTHER_USER_ID, email="stranger@example.com", password_hash="x", system_role="user")


async def _make_repo(tmp_path: Path) -> WorkspaceRepository:
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    session_factory = get_session_factory()
    assert session_factory is not None
    return WorkspaceRepository(session_factory)


class FakeFactStore:
    def __init__(self) -> None:
        self.buckets: dict[tuple[str, str], dict[str, dict]] = {}

    def get_fact(self, fact_id, *, user_id, agent_name):
        return self.buckets.get((user_id, agent_name), {}).get(fact_id)

    def upsert_fact(self, fact, *, user_id, agent_name):
        self.buckets.setdefault((user_id, agent_name), {})[fact["id"]] = dict(fact)
        return dict(fact)

    def delete_fact(self, fact_id, *, user_id, agent_name):
        self.buckets.get((user_id, agent_name), {}).pop(fact_id, None)
        return {"complete": False}


def _write_fact(
    root: Path,
    bucket: str,
    fact_id: str,
    *,
    category: str,
    content: str,
    agent_name: str = "__default__",
) -> None:
    prefix = hashlib.sha256(fact_id.encode("utf-8")).hexdigest()[:2]
    path = root / "users" / bucket / "agents" / agent_name / "facts" / prefix / f"{fact_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {fact_id}\ncategory: {category}\nconfidence: 0.9\nrevision: 1\n---\n\n# T\n\n{content}\n",
        encoding="utf-8",
    )


def _make_app(repo, memory_root: Path, store: FakeFactStore, *, user_factory=_user):
    app = make_authed_test_app(user_factory=user_factory)
    app.state.workspace_repo = repo
    app.state.memory_storage_root = str(memory_root)
    app.state.memory_fact_store = store
    app.include_router(workspaces.router)
    app.include_router(memory_scope.router)
    return app


def _seed(client) -> str:
    workspace = client.post("/api/workspaces", json={"name": "Maize Program"}).json()
    return client.post(
        f"/api/workspaces/{workspace['id']}/projects",
        json={"name": "Drought Resistance"},
    ).json()["id"]


def _seed_memory(root: Path, project_id: str) -> None:
    bucket = scoped_memory_user_id(str(_USER_ID), {"project_id": project_id})
    _write_fact(root, bucket, "fact_ctx", category="context", content="Tropical lines flower late.")
    _write_fact(root, bucket, "fact_self", category="identity", content="I am a maize breeder.")
    _write_fact(root, "ghost--project--" + "0" * 24, "fact_orphan", category="context", content="Unknown origin.")


def _store_with(root: Path, project_id: str) -> FakeFactStore:
    store = FakeFactStore()
    bucket = scoped_memory_user_id(str(_USER_ID), {"project_id": project_id})
    store.buckets[(bucket, "__default__")] = {
        "fact_ctx": {"id": "fact_ctx", "content": "Tropical lines flower late.", "category": "context"},
        "fact_self": {"id": "fact_self", "content": "I am a maize breeder.", "category": "identity"},
    }
    return store


def _decision_payload(
    client: TestClient,
    project_id: str,
    fact_id: str,
    decision: str,
    *,
    agent_name: str = "__default__",
    edited_content: str | None = None,
) -> dict:
    queue = client.get(f"/api/projects/{project_id}/memory/migration/suggestions").json()
    suggestion = next(item for item in queue["suggestions"] if item["fact_id"] == fact_id and item["agent_name"] == agent_name)
    payload = {
        "fact_id": fact_id,
        "agent_name": agent_name,
        "source_sha256": suggestion["sha256"],
        "decision": decision,
    }
    if edited_content is not None:
        payload["edited_content"] = edited_content
    return payload


# --------------------------------------------------------------------------
# Landing view
# --------------------------------------------------------------------------


def test_landing_view_returns_counts_and_no_fact_bodies(tmp_path: Path) -> None:
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as client:
        project_id = _seed(client)
        _seed_memory(memory_root, project_id)

        response = client.get(f"/api/projects/{project_id}/memory/migration")

    assert response.status_code == 200
    body = response.json()
    assert body["counts"] == {
        "private_legacy": 2,
        "suggested_for_project": 1,
        "already_project_scoped": 0,
        # An unattributed bucket may belong to another project or user. A
        # project member must not learn that it exists through this view.
        "needs_classification": 0,
    }
    assert "Tropical lines flower late" not in response.text
    assert "I am a maize breeder" not in response.text


def test_a_non_member_cannot_see_a_projects_counts(tmp_path: Path) -> None:
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as owner:
        project_id = _seed(owner)
        _seed_memory(memory_root, project_id)

    with TestClient(_make_app(repo, memory_root, FakeFactStore(), user_factory=_other_user)) as stranger:
        assert stranger.get(f"/api/projects/{project_id}/memory/migration").status_code == 404


# --------------------------------------------------------------------------
# The owner's own review queue
# --------------------------------------------------------------------------


def test_review_queue_serves_only_the_callers_own_facts_with_content(tmp_path: Path) -> None:
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as client:
        project_id = _seed(client)
        _seed_memory(memory_root, project_id)
        other_bucket = scoped_memory_user_id(str(_OTHER_USER_ID), {"project_id": project_id})
        _write_fact(memory_root, other_bucket, "fact_theirs", category="context", content="Their private note.")

        body = client.get(f"/api/projects/{project_id}/memory/migration/suggestions").json()

    fact_ids = {item["fact_id"] for item in body["suggestions"]}
    assert fact_ids == {"fact_ctx", "fact_self"}
    assert "Their private note" not in str(body)
    # The owner does see their own content — that is what makes review possible.
    assert any(item["content"] for item in body["suggestions"])


def test_review_queue_reports_checksum_source_bucket_and_reason(tmp_path: Path) -> None:
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as client:
        project_id = _seed(client)
        _seed_memory(memory_root, project_id)

        body = client.get(f"/api/projects/{project_id}/memory/migration/suggestions").json()

    entry = next(item for item in body["suggestions"] if item["fact_id"] == "fact_ctx")
    assert len(entry["sha256"]) == 64
    assert entry["bucket_id"].endswith(scoped_memory_user_id(str(_USER_ID), {"project_id": project_id}).split("--")[-1])
    assert entry["suggested_decision"] == "share"
    assert entry["reason"]


# --------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------


def test_sharing_one_fact_makes_it_readable_in_the_shared_bucket(tmp_path: Path) -> None:
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as setup:
        project_id = _seed(setup)
    _seed_memory(memory_root, project_id)
    store = _store_with(memory_root, project_id)

    with TestClient(_make_app(repo, memory_root, store)) as client:
        response = client.post(
            f"/api/projects/{project_id}/memory/migration/decisions",
            json=_decision_payload(client, project_id, "fact_ctx", "share"),
        )

    assert response.status_code == 200
    assert response.json()["outcome"] == "applied"
    shared_bucket = bind_scope(shared_project_scope(project_id)).user_id
    assert store.buckets[(shared_bucket, "__default__")]


def test_keeping_a_fact_private_writes_nothing_to_the_shared_bucket(tmp_path: Path) -> None:
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as setup:
        project_id = _seed(setup)
    _seed_memory(memory_root, project_id)
    store = _store_with(memory_root, project_id)

    with TestClient(_make_app(repo, memory_root, store)) as client:
        response = client.post(
            f"/api/projects/{project_id}/memory/migration/decisions",
            json=_decision_payload(client, project_id, "fact_self", "keep_private"),
        )

    assert response.json()["outcome"] == "recorded"
    assert bind_scope(shared_project_scope(project_id)).user_id not in {key[0] for key in store.buckets}


def test_a_member_cannot_share_another_members_fact(tmp_path: Path) -> None:
    """The decision endpoint resolves the bucket from the *caller*, not the body."""
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as setup:
        project_id = _seed(setup)
    _seed_memory(memory_root, project_id)
    other_bucket = scoped_memory_user_id(str(_OTHER_USER_ID), {"project_id": project_id})
    _write_fact(memory_root, other_bucket, "fact_theirs", category="context", content="Their private note.")
    store = _store_with(memory_root, project_id)

    with TestClient(_make_app(repo, memory_root, store)) as client:
        response = client.post(
            f"/api/projects/{project_id}/memory/migration/decisions",
            json={
                "fact_id": "fact_theirs",
                "agent_name": "__default__",
                "source_sha256": "0" * 64,
                "decision": "share",
            },
        )

    assert response.status_code == 404


def test_bulk_share_is_not_available(tmp_path: Path) -> None:
    """Deliberate product constraint: sharing is one considered act at a time."""
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as client:
        project_id = _seed(client)
        _seed_memory(memory_root, project_id)

        response = client.post(
            f"/api/projects/{project_id}/memory/migration/decisions",
            json={"fact_ids": ["fact_ctx", "fact_self"], "decision": "share"},
        )

    assert response.status_code == 422


def test_edit_then_share_stores_the_edited_text(tmp_path: Path) -> None:
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as setup:
        project_id = _seed(setup)
    _seed_memory(memory_root, project_id)
    store = _store_with(memory_root, project_id)

    with TestClient(_make_app(repo, memory_root, store)) as client:
        response = client.post(
            f"/api/projects/{project_id}/memory/migration/decisions",
            json={
                "fact_id": "fact_ctx",
                "agent_name": "__default__",
                "source_sha256": "0" * 64,
                "decision": "edit_then_share",
                "edited_content": "Lines flower late in this environment.",
            },
        )

    # The supplied checksum was not the version the human reviewed.
    assert response.status_code == 409

    with TestClient(_make_app(repo, memory_root, store)) as client:
        response = client.post(
            f"/api/projects/{project_id}/memory/migration/decisions",
            json=_decision_payload(
                client,
                project_id,
                "fact_ctx",
                "edit_then_share",
                edited_content="Lines flower late in this environment.",
            ),
        )

    assert response.status_code == 200
    shared_bucket = bind_scope(shared_project_scope(project_id)).user_id
    stored = next(iter(store.buckets[(shared_bucket, "__default__")].values()))
    assert stored["content"] == "Lines flower late in this environment."


def test_edit_then_share_without_content_is_rejected(tmp_path: Path) -> None:
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as setup:
        project_id = _seed(setup)
    _seed_memory(memory_root, project_id)

    with TestClient(_make_app(repo, memory_root, _store_with(memory_root, project_id))) as client:
        response = client.post(
            f"/api/projects/{project_id}/memory/migration/decisions",
            json={
                **_decision_payload(client, project_id, "fact_ctx", "edit_then_share"),
                "edited_content": None,
            },
        )

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Manifest and rollback
# --------------------------------------------------------------------------


def test_manifest_download_is_checksum_bearing_and_body_free(tmp_path: Path) -> None:
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as client:
        project_id = _seed(client)
        _seed_memory(memory_root, project_id)

        response = client.get(f"/api/projects/{project_id}/memory/migration/manifest")

    assert response.status_code == 200
    body = response.json()
    assert body["version"] >= 1
    assert body["storage_root"] == "."
    assert {fact["fact_id"] for fact in body["facts"]} == {"fact_ctx", "fact_self"}
    assert all(len(fact["sha256"]) == 64 for fact in body["facts"])
    assert all(bucket["classification"] in {"project", "shared_project"} for bucket in body["buckets"])
    assert "fact_orphan" not in response.text
    assert str(memory_root) not in response.text
    assert "Tropical lines flower late" not in response.text


def test_rollback_removes_shared_copies_and_is_reported(tmp_path: Path) -> None:
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as setup:
        project_id = _seed(setup)
    _seed_memory(memory_root, project_id)
    store = _store_with(memory_root, project_id)

    with TestClient(_make_app(repo, memory_root, store)) as client:
        client.post(
            f"/api/projects/{project_id}/memory/migration/decisions",
            json=_decision_payload(client, project_id, "fact_ctx", "share"),
        )
        response = client.post(f"/api/projects/{project_id}/memory/migration/rollback")

    assert response.json()["reverted"] == 1
    shared_bucket = bind_scope(shared_project_scope(project_id)).user_id
    assert store.buckets[(shared_bucket, "__default__")] == {}


def test_decision_is_bound_to_the_version_the_human_reviewed(tmp_path: Path) -> None:
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as setup:
        project_id = _seed(setup)
    _seed_memory(memory_root, project_id)
    store = _store_with(memory_root, project_id)

    with TestClient(_make_app(repo, memory_root, store)) as client:
        payload = _decision_payload(client, project_id, "fact_ctx", "share")
        bucket = scoped_memory_user_id(str(_USER_ID), {"project_id": project_id})
        _write_fact(
            memory_root,
            bucket,
            "fact_ctx",
            category="context",
            content="The source changed after the user read it.",
        )
        response = client.post(
            f"/api/projects/{project_id}/memory/migration/decisions",
            json=payload,
        )

    assert response.status_code == 409
    assert "changed" in response.json()["detail"].lower()


def test_same_fact_id_in_two_agent_buckets_is_unambiguous(tmp_path: Path) -> None:
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as setup:
        project_id = _seed(setup)
    _seed_memory(memory_root, project_id)
    bucket = scoped_memory_user_id(str(_USER_ID), {"project_id": project_id})
    _write_fact(
        memory_root,
        bucket,
        "fact_ctx",
        category="context",
        content="Custom-agent version.",
        agent_name="breeding-bot",
    )
    store = _store_with(memory_root, project_id)
    store.buckets[(bucket, "breeding-bot")] = {
        "fact_ctx": {
            "id": "fact_ctx",
            "content": "Custom-agent version.",
            "category": "context",
        }
    }

    with TestClient(_make_app(repo, memory_root, store)) as client:
        response = client.post(
            f"/api/projects/{project_id}/memory/migration/decisions",
            json=_decision_payload(
                client,
                project_id,
                "fact_ctx",
                "share",
                agent_name="breeding-bot",
            ),
        )

    assert response.status_code == 200
    shared_bucket = bind_scope(shared_project_scope(project_id)).user_id
    assert store.buckets[(shared_bucket, "breeding-bot")]
    assert (shared_bucket, "__default__") not in store.buckets


def test_completed_decision_disappears_from_review_queue(tmp_path: Path) -> None:
    repo = anyio.run(_make_repo, tmp_path)
    memory_root = tmp_path / "mem"
    with TestClient(_make_app(repo, memory_root, FakeFactStore())) as setup:
        project_id = _seed(setup)
    _seed_memory(memory_root, project_id)
    store = _store_with(memory_root, project_id)

    with TestClient(_make_app(repo, memory_root, store)) as client:
        payload = _decision_payload(client, project_id, "fact_self", "keep_private")
        assert (
            client.post(
                f"/api/projects/{project_id}/memory/migration/decisions",
                json=payload,
            ).status_code
            == 200
        )
        queue = client.get(f"/api/projects/{project_id}/memory/migration/suggestions").json()

    assert "fact_self" not in {item["fact_id"] for item in queue["suggestions"]}
