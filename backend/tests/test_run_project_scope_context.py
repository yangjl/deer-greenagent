"""The run's owning project is resolved server-side, never trusted from a client.

The sandbox maps a project's workspace from ``context["project_id"]``, so a
client that could set it would gain read/write access to another project's
files. These tests pin that the Gateway stamps it from the conversation's
durable ``threads_meta`` row and strips any caller-supplied value.
"""

from __future__ import annotations

import pytest

from app.gateway.services import apply_project_scope_context


class _Store:
    def __init__(self, record: dict | None) -> None:
        self._record = record
        self.calls: list[str] = []

    async def get(self, thread_id: str, **_kwargs):
        self.calls.append(thread_id)
        return self._record


class _RootRepo:
    """Workspace repo double with a stored, already-materialized root."""

    def __init__(self, roots: dict[str, str]) -> None:
        self._roots = roots

    async def get_project_record(self, project_id: str):
        root = self._roots.get(project_id)
        if root is None:
            return None
        return {"id": project_id, "name": project_id, "root_path": root}

    async def update_project_root(self, project_id: str, root_path: str) -> None:
        self._roots[project_id] = root_path


class _CycleRepo:
    def __init__(self, summary: dict) -> None:
        self.summary = summary
        self.calls: list[str] = []

    async def project_cycle_summary(self, project_id: str) -> dict:
        self.calls.append(project_id)
        return self.summary


@pytest.mark.anyio
class TestProjectScopeContext:
    async def test_stamps_the_conversations_project_and_root(self, tmp_path):
        root = tmp_path / "G2F"
        config: dict = {"context": {}, "configurable": {}}
        await apply_project_scope_context(
            config,
            "thread-1",
            _Store({"project_id": "project-abc"}),
            workspace_repo=_RootRepo({"project-abc": str(root)}),
        )
        assert config["context"]["project_id"] == "project-abc"
        assert config["context"]["project_name"] == "project-abc"
        assert config["context"]["project_root"] == str(root)
        # ensure_project_root materializes the human folder
        assert root.is_dir()

    async def test_stamps_server_owned_dbtl_lifecycle_context(self, tmp_path):
        config: dict = {
            "context": {
                "dbtl_project_cycle_count": 99,
                "dbtl_has_unfinished_cycles": True,
            },
            "configurable": {
                "dbtl_project_cycle_count": 99,
                "dbtl_has_unfinished_cycles": True,
            },
        }
        cycles = _CycleRepo(
            {
                "project_cycle_count": 0,
                "has_unfinished_cycles": False,
            }
        )

        await apply_project_scope_context(
            config,
            "thread-1",
            _Store({"project_id": "project-abc"}),
            workspace_repo=_RootRepo({"project-abc": str(tmp_path / "G2F")}),
            dbtl_cycle_repo=cycles,
        )

        assert config["context"]["dbtl_project_cycle_count"] == 0
        assert config["context"]["dbtl_has_unfinished_cycles"] is False
        assert "dbtl_project_cycle_count" not in config["configurable"]
        assert "dbtl_has_unfinished_cycles" not in config["configurable"]
        assert cycles.calls == ["project-abc"]

    async def test_unfiled_conversations_carry_no_project(self):
        config: dict = {"context": {}, "configurable": {}}
        await apply_project_scope_context(config, "thread-1", _Store({"project_id": None}))
        assert "project_id" not in config["context"]

    async def test_client_supplied_project_and_root_are_overwritten(self, tmp_path):
        """A caller must not be able to point a run at another folder."""
        config: dict = {
            "context": {
                "project_id": "project-victim",
                "project_name": "Victim",
                "project_root": "/etc",
            },
            "configurable": {
                "project_id": "project-victim",
                "project_name": "Victim",
                "project_root": "/etc",
            },
        }
        await apply_project_scope_context(
            config,
            "thread-1",
            _Store({"project_id": "project-abc"}),
            workspace_repo=_RootRepo({"project-abc": str(tmp_path / "G2F")}),
        )
        assert config["context"]["project_id"] == "project-abc"
        assert config["context"]["project_name"] == "project-abc"
        assert config["context"]["project_root"] == str(tmp_path / "G2F")
        assert "project_id" not in config["configurable"]
        assert "project_name" not in config["configurable"]
        assert "project_root" not in config["configurable"]

    async def test_client_supplied_project_is_dropped_for_unfiled_conversations(self):
        config: dict = {
            "context": {
                "project_id": "project-victim",
                "project_name": "Victim",
                "project_root": "/etc",
            },
            "configurable": {
                "project_id": "project-victim",
                "project_name": "Victim",
            },
        }
        await apply_project_scope_context(config, "thread-1", _Store({"project_id": None}))
        assert "project_id" not in config["context"]
        assert "project_name" not in config["context"]
        assert "project_root" not in config["context"]
        assert "project_id" not in config["configurable"]
        assert "project_name" not in config["configurable"]

    async def test_missing_repo_still_stamps_the_id_but_no_root(self):
        config: dict = {"context": {}, "configurable": {}}
        await apply_project_scope_context(config, "thread-1", _Store({"project_id": "project-abc"}))
        assert config["context"]["project_id"] == "project-abc"
        assert "project_root" not in config["context"]

    async def test_missing_thread_record_drops_the_scope(self):
        config: dict = {"context": {"project_id": "project-victim"}, "configurable": {}}
        await apply_project_scope_context(config, "thread-1", _Store(None))
        assert "project_id" not in config["context"]

    async def test_store_failure_is_not_fatal_and_leaves_no_scope(self):
        class _Broken:
            async def get(self, *_args, **_kwargs):
                raise RuntimeError("database down")

        config: dict = {"context": {"project_id": "project-victim"}, "configurable": {}}
        await apply_project_scope_context(config, "thread-1", _Broken())
        assert "project_id" not in config["context"]

    async def test_no_store_drops_the_scope(self):
        config: dict = {"context": {"project_id": "project-victim"}, "configurable": {}}
        await apply_project_scope_context(config, "thread-1", None)
        assert "project_id" not in config["context"]


class _WorkspaceRepo:
    def __init__(self, member_projects: dict[str, dict]) -> None:
        self._projects = member_projects

    async def get_project(self, project_id: str, *, user_id: str):
        project = self._projects.get(project_id)
        if project is None or project.get("_member") != user_id:
            return None
        return project


class _FilingStore:
    def __init__(self) -> None:
        self.filed: list[tuple[str, str | None, str | None, str | None]] = []
        self.created: list[tuple[str, str | None]] = []

    async def get(self, thread_id, user_id=None):
        return None

    async def create(self, thread_id, *, user_id=None, **_kwargs):
        # Filing a brand-new conversation creates its threads_meta row first:
        # generic row creation moved into the attached run worker, which runs
        # after filing, and set_conversation_scope no-ops on a missing row.
        self.created.append((thread_id, user_id))
        return {"thread_id": thread_id, "user_id": user_id}

    async def set_conversation_scope(self, thread_id, *, workspace_id, project_id, user_id=None):
        self.filed.append((thread_id, workspace_id, project_id, user_id))


@pytest.mark.anyio
class TestRequestedProjectFiling:
    """A run request may ask to file its (new) conversation into a project.

    This closes the first-run race: the frontend's PUT lands after the run
    already started, so the run request itself carries the intent — and the
    server validates project membership before honoring it.
    """

    async def test_files_the_thread_when_the_caller_is_a_member(self):
        from app.gateway.services import file_thread_into_requested_project

        store = _FilingStore()
        repo = _WorkspaceRepo({"project-abc": {"id": "project-abc", "workspace_id": "ws-1", "_member": "alice"}})
        await file_thread_into_requested_project(
            {"project_id": "project-abc"},
            thread_id="thread-1",
            owner_user_id="alice",
            thread_store=store,
            workspace_repo=repo,
        )
        assert store.filed == [("thread-1", "ws-1", "project-abc", "alice")]

    async def test_non_members_cannot_file_into_a_project(self):
        from app.gateway.services import file_thread_into_requested_project

        store = _FilingStore()
        repo = _WorkspaceRepo({"project-abc": {"id": "project-abc", "workspace_id": "ws-1", "_member": "alice"}})
        await file_thread_into_requested_project(
            {"project_id": "project-abc"},
            thread_id="thread-1",
            owner_user_id="mallory",
            thread_store=store,
            workspace_repo=repo,
        )
        assert store.filed == []

    async def test_no_request_or_missing_pieces_are_no_ops(self):
        from app.gateway.services import file_thread_into_requested_project

        store = _FilingStore()
        repo = _WorkspaceRepo({})
        for context in (None, {}, {"project_id": ""}, {"project_id": 42}):
            await file_thread_into_requested_project(context, thread_id="thread-1", owner_user_id="alice", thread_store=store, workspace_repo=repo)
        await file_thread_into_requested_project({"project_id": "project-abc"}, thread_id="thread-1", owner_user_id=None, thread_store=store, workspace_repo=repo)
        await file_thread_into_requested_project({"project_id": "project-abc"}, thread_id="thread-1", owner_user_id="alice", thread_store=store, workspace_repo=None)
        assert store.filed == []

    async def test_repo_failure_is_not_fatal(self):
        from app.gateway.services import file_thread_into_requested_project

        class _Broken:
            async def get_project(self, *_a, **_k):
                raise RuntimeError("db down")

        store = _FilingStore()
        await file_thread_into_requested_project({"project_id": "project-abc"}, thread_id="thread-1", owner_user_id="alice", thread_store=store, workspace_repo=_Broken())
        assert store.filed == []
