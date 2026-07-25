"""A project's conversations share one human-visible folder in the sandbox.

`LocalSandboxProvider.acquire(..., project_root=...)` maps the agent-visible
`/mnt/user-data` tree into the project's folder: the root itself is the
workspace, with `uploads/` and `outputs/` as plain visible subfolders. The
Gateway upload path moves in lockstep (`get_uploads_dir(...,
project_root=...)`), so the agent and the human always see the same files.
"""

from pathlib import Path

import pytest

from deerflow.config.paths import Paths
from deerflow.sandbox.local.local_sandbox import PathMapping
from deerflow.sandbox.local.local_sandbox_provider import LocalSandboxProvider


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = Paths(str(tmp_path / "internal"))
    monkeypatch.setattr("deerflow.config.paths._paths", paths, raising=False)
    yield paths


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path / "Documents" / "projects" / "G2F"


def _mapping(provider_sandbox, container_path: str) -> str:
    for mapping in provider_sandbox.path_mappings:
        if mapping.container_path == container_path:
            return mapping.local_path
    raise AssertionError(f"no mapping for {container_path}")


class TestProjectScopedWorkspace:
    def test_the_project_folder_is_the_workspace(self, project_root: Path):
        provider = LocalSandboxProvider()
        sandbox = provider.get(provider.acquire("thread-1", user_id="alice", project_id="project-abc", project_root=str(project_root)))
        assert sandbox is not None
        assert _mapping(sandbox, "/mnt/user-data") == str(project_root)
        assert _mapping(sandbox, "/mnt/user-data/workspace") == str(project_root)
        assert _mapping(sandbox, "/mnt/user-data/uploads") == str(project_root / "uploads")
        assert _mapping(sandbox, "/mnt/user-data/outputs") == str(project_root / "outputs")

    def test_the_human_folder_is_created_on_acquire(self, project_root: Path):
        provider = LocalSandboxProvider()
        provider.acquire("thread-1", user_id="alice", project_id="project-abc", project_root=str(project_root))
        assert project_root.is_dir()
        assert (project_root / "uploads").is_dir()

    def test_two_conversations_share_the_folder(self, project_root: Path):
        provider = LocalSandboxProvider()
        first = provider.get(provider.acquire("thread-1", user_id="alice", project_id="project-abc", project_root=str(project_root)))
        second = provider.get(provider.acquire("thread-2", user_id="alice", project_id="project-abc", project_root=str(project_root)))
        assert first is not None and second is not None
        assert _mapping(first, "/mnt/user-data/workspace") == _mapping(second, "/mnt/user-data/workspace")

    def test_projectless_conversations_are_unchanged(self, isolated_paths: Paths):
        provider = LocalSandboxProvider()
        sandbox = provider.get(provider.acquire("thread-1", user_id="alice"))
        assert sandbox is not None
        assert _mapping(sandbox, "/mnt/user-data/workspace") == str(isolated_paths.sandbox_work_dir("thread-1", user_id="alice"))

    def test_filing_a_cached_thread_rebuilds_its_mappings(self, isolated_paths: Paths, project_root: Path):
        provider = LocalSandboxProvider()
        before = provider.get(provider.acquire("thread-1", user_id="alice"))
        assert before is not None
        assert _mapping(before, "/mnt/user-data/workspace") == str(isolated_paths.sandbox_work_dir("thread-1", user_id="alice"))

        after = provider.get(provider.acquire("thread-1", user_id="alice", project_id="project-abc", project_root=str(project_root)))
        assert after is not None
        assert _mapping(after, "/mnt/user-data/workspace") == str(project_root)

    def test_reacquiring_the_same_scope_reuses_the_sandbox(self, project_root: Path):
        provider = LocalSandboxProvider()
        first = provider.acquire("thread-1", user_id="alice", project_id="project-abc", project_root=str(project_root))
        second = provider.acquire("thread-1", user_id="alice", project_id="project-abc", project_root=str(project_root))
        assert first == second

    def test_project_scope_removes_an_overlapping_parent_mount(self, project_root: Path):
        provider = LocalSandboxProvider()
        provider._path_mappings.append(
            PathMapping(
                container_path="/mnt/projects",
                local_path=str(project_root.parent),
                read_only=False,
            )
        )

        scoped = provider.get(
            provider.acquire(
                "thread-1",
                user_id="alice",
                project_id="project-abc",
                project_root=str(project_root),
            )
        )
        unscoped = provider.get(provider.acquire("thread-2", user_id="alice"))

        assert scoped is not None and unscoped is not None
        assert all(not (mapping.container_path == "/mnt/projects" and mapping.local_path == str(project_root.parent)) for mapping in scoped.path_mappings)
        assert any(mapping.container_path == "/mnt/projects" and mapping.local_path == str(project_root.parent) for mapping in unscoped.path_mappings)


class TestAsyncAcquire:
    @pytest.mark.asyncio
    async def test_acquire_async_forwards_the_root(self, project_root: Path):
        provider = LocalSandboxProvider()
        sandbox = provider.get(await provider.acquire_async("thread-1", user_id="alice", project_id="project-abc", project_root=str(project_root)))
        assert sandbox is not None
        assert _mapping(sandbox, "/mnt/user-data/workspace") == str(project_root)


class TestUploadsManagerProjectScope:
    def test_get_uploads_dir_resolves_the_project_folder(self, isolated_paths: Paths, project_root: Path):
        from deerflow.uploads.manager import get_uploads_dir

        assert get_uploads_dir("thread-1", user_id="alice", project_root=str(project_root)) == project_root / "uploads"
        assert get_uploads_dir("thread-1", user_id="alice") == isolated_paths.sandbox_uploads_dir("thread-1", user_id="alice")

    def test_ensure_uploads_dir_creates_the_folder(self, project_root: Path):
        from deerflow.uploads.manager import ensure_uploads_dir

        created = ensure_uploads_dir("thread-1", user_id="alice", project_root=str(project_root))
        assert created.is_dir()
        assert created == project_root / "uploads"
