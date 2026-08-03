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
from deerflow.sandbox.tools import (
    _resolve_and_validate_user_data_path,
    _thread_virtual_to_actual_mappings,
    replace_virtual_path,
)


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

    def test_dbtl_outputs_are_read_only_to_agent_file_tools(self, project_root: Path):
        provider = LocalSandboxProvider()
        sandbox = provider.get(
            provider.acquire(
                "thread-1",
                user_id="alice",
                project_id="project-abc",
                project_root=str(project_root),
            )
        )
        assert sandbox is not None
        mapping = next(item for item in sandbox.path_mappings if item.container_path == "/mnt/user-data/outputs/dbtl")
        assert mapping.local_path == str(project_root / "outputs" / "dbtl")
        assert mapping.read_only is True

        with pytest.raises(OSError, match="Read-only file system"):
            sandbox.write_file("/mnt/user-data/outputs/dbtl/cycle-1/result.json", "{}")

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


class TestFilesAtTheProjectRootAreReadable:
    """`/mnt/user-data/<file>` must resolve for a file sitting at the project root.

    The sandbox's own `PathMapping` table has always mapped the virtual root
    onto the project folder, but the *tool* layer resolves virtual paths
    through `replace_virtual_path`, which built its root mapping only when
    workspace, uploads, and outputs shared a common parent. That holds for a
    thread sandbox (all three sit under `user-data/`) and is false for a
    project, where the workspace *is* the parent of the other two. So the root
    mapping was silently absent, `/mnt/user-data/trial.csv` came back
    unresolved, and the containment check then rejected the literal virtual
    path as traversal.

    This is the shape every DBTL stage worker is handed: `_project_manifest`
    emits `/mnt/user-data/<relative>` for project files, so a design meeting
    reported every read denied and returned no evidence at all.
    """

    def test_the_virtual_root_maps_onto_the_project_folder(self, project_root: Path):
        thread_data = {
            "workspace_path": str(project_root),
            "uploads_path": str(project_root / "uploads"),
            "outputs_path": str(project_root / "outputs"),
        }

        assert _thread_virtual_to_actual_mappings(thread_data)["/mnt/user-data"] == str(project_root)

    def test_a_root_level_file_resolves_into_the_project(self, project_root: Path):
        thread_data = {
            "workspace_path": str(project_root),
            "uploads_path": str(project_root / "uploads"),
            "outputs_path": str(project_root / "outputs"),
        }

        resolved = replace_virtual_path("/mnt/user-data/trial_2025_yield.csv", thread_data)

        assert resolved == str(project_root / "trial_2025_yield.csv")

    def test_the_containment_check_accepts_it(self, project_root: Path):
        """Resolution alone is not enough — the security gate must also pass."""
        thread_data = {
            "workspace_path": str(project_root),
            "uploads_path": str(project_root / "uploads"),
            "outputs_path": str(project_root / "outputs"),
        }

        _resolve_and_validate_user_data_path("/mnt/user-data/DATA_NOTES.md", thread_data)

    def test_read_file_returns_the_bytes(self, project_root: Path):
        """End to end through the sandbox, the way a stage worker reads."""
        provider = LocalSandboxProvider()
        sandbox_id = provider.acquire("thread-1", user_id="alice", project_id="project-abc", project_root=str(project_root))
        sandbox = provider.get(sandbox_id)
        assert sandbox is not None
        (project_root / "trial_2025_yield.csv").write_text("plot_id,grain_yield_g\nP0001,148.2\n", encoding="utf-8")

        thread_data = {
            "workspace_path": str(project_root),
            "uploads_path": str(project_root / "uploads"),
            "outputs_path": str(project_root / "outputs"),
        }
        host_path = _resolve_and_validate_user_data_path("/mnt/user-data/trial_2025_yield.csv", thread_data)

        assert "P0001" in sandbox.read_file(host_path)

    def test_the_subfolders_still_resolve(self, project_root: Path):
        """The explicit longer prefixes must keep winning over the new root."""
        thread_data = {
            "workspace_path": str(project_root),
            "uploads_path": str(project_root / "uploads"),
            "outputs_path": str(project_root / "outputs"),
        }

        assert replace_virtual_path("/mnt/user-data/uploads/a.csv", thread_data) == str(project_root / "uploads" / "a.csv")
        assert replace_virtual_path("/mnt/user-data/outputs/b.md", thread_data) == str(project_root / "outputs" / "b.md")
        assert replace_virtual_path("/mnt/user-data/workspace/c.txt", thread_data) == str(project_root / "c.txt")

    def test_a_thread_sandbox_is_unchanged(self, isolated_paths: Paths):
        """The common-parent case that already worked must keep working."""
        base = Path(isolated_paths.base_dir) / "user-data"
        thread_data = {
            "workspace_path": str(base / "workspace"),
            "uploads_path": str(base / "uploads"),
            "outputs_path": str(base / "outputs"),
        }

        assert _thread_virtual_to_actual_mappings(thread_data)["/mnt/user-data"] == str(base)
        assert replace_virtual_path("/mnt/user-data/x.csv", thread_data) == str(base / "x.csv")

    def test_an_unrelated_host_path_is_still_refused(self, project_root: Path):
        """Widening the mapping must not widen the containment check."""
        thread_data = {
            "workspace_path": str(project_root),
            "uploads_path": str(project_root / "uploads"),
            "outputs_path": str(project_root / "outputs"),
        }

        with pytest.raises(PermissionError):
            _resolve_and_validate_user_data_path("/mnt/user-data/../../etc/passwd", thread_data)


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
