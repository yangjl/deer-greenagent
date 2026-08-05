"""Core behavior tests for present_files path normalization."""

import importlib
from types import SimpleNamespace

import pytest

from deerflow.config.paths import Paths

present_file_tool_module = importlib.import_module("deerflow.tools.builtins.present_file_tool")


def _make_runtime(outputs_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        state={"thread_data": {"outputs_path": outputs_path}},
        context={"thread_id": "thread-1"},
        config={},
    )


def test_present_files_normalizes_host_outputs_path(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    artifact_path = outputs_dir / "report.md"
    artifact_path.write_text("ok")

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=[str(artifact_path)],
        tool_call_id="tc-1",
    )

    assert result.update["artifacts"] == ["/mnt/user-data/outputs/report.md"]
    assert result.update["messages"][0].content == "Successfully presented files"


def test_present_files_keeps_virtual_outputs_path(tmp_path, monkeypatch):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    artifact_path = outputs_dir / "summary.json"
    artifact_path.write_text("{}")

    monkeypatch.setattr(
        present_file_tool_module,
        "get_paths",
        lambda: SimpleNamespace(resolve_virtual_path=lambda thread_id, path, *, user_id=None: artifact_path),
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=["/mnt/user-data/outputs/summary.json"],
        tool_call_id="tc-2",
    )

    assert result.update["artifacts"] == ["/mnt/user-data/outputs/summary.json"]


@pytest.mark.no_auto_user
def test_present_files_uses_runtime_user_for_virtual_outputs_path(tmp_path, monkeypatch):
    """A runtime user must resolve virtual output paths even without a request ContextVar."""
    paths = Paths(tmp_path)
    user_id = "runtime-user"
    thread_id = "thread-runtime-user"
    outputs_dir = paths.sandbox_outputs_dir(thread_id, user_id=user_id)
    outputs_dir.mkdir(parents=True)
    (outputs_dir / "report.md").write_text("ok")

    monkeypatch.setattr(present_file_tool_module, "get_paths", lambda: paths)
    runtime = SimpleNamespace(
        state={"thread_data": {"outputs_path": str(outputs_dir)}},
        context={"thread_id": thread_id, "user_id": user_id},
        config={},
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=runtime,
        filepaths=["/mnt/user-data/outputs/report.md"],
        tool_call_id="tc-runtime-user",
    )

    assert result.update["artifacts"] == ["/mnt/user-data/outputs/report.md"]
    assert result.update["messages"][0].content == "Successfully presented files"
    assert not paths.sandbox_outputs_dir(thread_id, user_id="default").exists()


def test_present_files_uses_config_thread_id_when_context_missing(tmp_path, monkeypatch):
    outputs_dir = tmp_path / "threads" / "thread-from-config" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    artifact_path = outputs_dir / "summary.json"
    artifact_path.write_text("{}")

    monkeypatch.setattr(
        present_file_tool_module,
        "get_paths",
        lambda: SimpleNamespace(resolve_virtual_path=lambda thread_id, path: artifact_path),
    )

    runtime = SimpleNamespace(
        state={"thread_data": {"outputs_path": str(outputs_dir)}},
        context={},
        config={"configurable": {"thread_id": "thread-from-config"}},
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=runtime,
        filepaths=["/mnt/user-data/outputs/summary.json"],
        tool_call_id="tc-config",
    )

    assert result.update["artifacts"] == ["/mnt/user-data/outputs/summary.json"]
    assert result.update["messages"][0].content == "Successfully presented files"


def test_present_files_rejects_paths_outside_outputs(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    workspace_dir = tmp_path / "threads" / "thread-1" / "user-data" / "workspace"
    outputs_dir.mkdir(parents=True)
    workspace_dir.mkdir(parents=True)
    leaked_path = workspace_dir / "notes.txt"
    leaked_path.write_text("leak")

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=[str(leaked_path)],
        tool_call_id="tc-3",
    )

    assert "artifacts" not in result.update
    assert result.update["messages"][0].content == f"Error: Only files in /mnt/user-data/outputs can be presented: {leaked_path}"


def test_present_files_resolves_a_project_conversation_outputs_path(tmp_path):
    """A project's outputs folder is not the thread's internal one.

    `Paths.resolve_virtual_path` resolves `/mnt/user-data/...` against the
    thread's own `.deer-flow/users/<u>/threads/<t>/user-data` directory and
    knows nothing about projects — `Paths` deliberately keeps no project
    layout. But in a project conversation `thread_data["outputs_path"]` is the
    project's human-visible `outputs/`, so the resolved path and the
    containment root pointed at different places and every present failed with
    "Only files in /mnt/user-data/outputs can be presented" naming a path that
    was plainly inside it.

    Resolving the outputs prefix against `outputs_path` itself is correct for
    both layouts, because that value is exactly the directory this tool is
    allowed to present from.
    """
    project_root = tmp_path / "Documents" / "projects" / "G2F"
    outputs_dir = project_root / "outputs"
    outputs_dir.mkdir(parents=True)
    (outputs_dir / "design_package.md").write_text("# design")

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=["/mnt/user-data/outputs/design_package.md"],
        tool_call_id="tc-project",
    )

    assert result.update["artifacts"] == ["/mnt/user-data/outputs/design_package.md"]
    assert result.update["messages"][0].content == "Successfully presented files"


def test_present_files_still_rejects_a_project_workspace_path(tmp_path):
    """Widening resolution must not widen what may be presented.

    In a project the workspace IS the project root, so `outputs/` sits inside
    it. A file at the root must still be refused — otherwise the one directory
    boundary this tool enforces would disappear exactly where the folders
    overlap.
    """
    project_root = tmp_path / "Documents" / "projects" / "G2F"
    outputs_dir = project_root / "outputs"
    outputs_dir.mkdir(parents=True)
    (project_root / "trial.csv").write_text("plot_id\n")

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=["/mnt/user-data/trial.csv"],
        tool_call_id="tc-refuse",
    )

    assert "can be presented" in result.update["messages"][0].content


def test_present_files_refuses_traversal_out_of_project_outputs(tmp_path):
    project_root = tmp_path / "Documents" / "projects" / "G2F"
    outputs_dir = project_root / "outputs"
    outputs_dir.mkdir(parents=True)
    (project_root / "secret.txt").write_text("no")

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=["/mnt/user-data/outputs/../secret.txt"],
        tool_call_id="tc-traversal",
    )

    assert "can be presented" in result.update["messages"][0].content
