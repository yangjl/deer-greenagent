"""A conversation's project membership is a durable record, not chat metadata.

`threads_meta` carries `project_id` / `workspace_id` / `scope_type`; these
tests pin the write/read contract for both the SQL and in-memory stores so the
Gateway can resolve a run's owning project without trusting client metadata.
"""

import pytest
from langgraph.store.memory import InMemoryStore

from deerflow.persistence.thread_meta import MemoryThreadMetaStore
from deerflow.persistence.thread_meta.sql import ThreadMetaRepository


@pytest.fixture(params=["memory", "sql"])
async def repo(request, tmp_path):
    """Both backends must satisfy the same conversation-scope contract."""
    if request.param == "memory":
        yield MemoryThreadMetaStore(InMemoryStore())
        return

    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    url = f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
    yield ThreadMetaRepository(get_session_factory())
    await close_engine()


@pytest.mark.anyio
class TestConversationScope:
    async def test_new_conversations_default_to_the_inbox(self, repo):
        record = await repo.create("thread-1", user_id="alice")
        assert record["project_id"] is None
        assert record["scope_type"] == "inbox"

    async def test_create_accepts_a_project_scope(self, repo):
        record = await repo.create(
            "thread-1",
            user_id="alice",
            workspace_id="ws-1",
            project_id="project-abc",
        )
        assert record["workspace_id"] == "ws-1"
        assert record["project_id"] == "project-abc"
        assert record["scope_type"] == "project"

    async def test_set_conversation_scope_files_an_existing_conversation(self, repo):
        await repo.create("thread-1", user_id="alice")
        await repo.set_conversation_scope(
            "thread-1",
            workspace_id="ws-1",
            project_id="project-abc",
            user_id="alice",
        )
        record = await repo.get("thread-1", user_id="alice")
        assert record is not None
        assert record["project_id"] == "project-abc"
        assert record["workspace_id"] == "ws-1"
        assert record["scope_type"] == "project"

    async def test_clearing_the_project_returns_it_to_the_inbox(self, repo):
        await repo.create("thread-1", user_id="alice", workspace_id="ws-1", project_id="project-abc")
        await repo.set_conversation_scope("thread-1", workspace_id=None, project_id=None, user_id="alice")
        record = await repo.get("thread-1", user_id="alice")
        assert record is not None
        assert record["project_id"] is None
        assert record["scope_type"] == "inbox"

    async def test_scope_write_is_owner_scoped(self, repo):
        """Another user must not be able to file someone else's conversation."""
        await repo.create("thread-1", user_id="alice")
        await repo.set_conversation_scope(
            "thread-1",
            workspace_id="ws-1",
            project_id="project-abc",
            user_id="mallory",
        )
        record = await repo.get("thread-1", user_id="alice")
        assert record is not None
        assert record["project_id"] is None

    async def test_list_by_project_returns_only_that_projects_conversations(self, repo):
        await repo.create("thread-1", user_id="alice", workspace_id="ws-1", project_id="project-abc")
        await repo.create("thread-2", user_id="alice", workspace_id="ws-1", project_id="project-xyz")
        await repo.create("thread-3", user_id="alice")

        rows = await repo.list_by_project("project-abc", user_id="alice")
        assert [row["thread_id"] for row in rows] == ["thread-1"]

    async def test_list_by_project_is_owner_scoped(self, repo):
        await repo.create("thread-1", user_id="alice", workspace_id="ws-1", project_id="project-abc")
        assert await repo.list_by_project("project-abc", user_id="mallory") == []
