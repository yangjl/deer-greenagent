"""Project storage is a human-visible folder, not an internal bucket.

A project's root (e.g. ``~/Documents/projects/G2F``) IS the agent's
workspace: ``/mnt/user-data`` and ``/mnt/user-data/workspace`` both resolve to
the root itself, with ``uploads/`` and ``outputs/`` as plain visible
subfolders. These tests pin that layout, folder-name safety, and traversal
containment.
"""

from pathlib import Path

import pytest

from deerflow.projects.storage import (
    ensure_project_dirs,
    project_folder_name,
    project_outputs_dir,
    project_uploads_dir,
    project_workspace_dir,
    resolve_project_virtual_path,
)


class TestLayout:
    def test_the_root_is_the_workspace(self, tmp_path: Path):
        assert project_workspace_dir(tmp_path) == tmp_path

    def test_uploads_and_outputs_are_visible_subfolders(self, tmp_path: Path):
        assert project_uploads_dir(tmp_path) == tmp_path / "uploads"
        assert project_outputs_dir(tmp_path) == tmp_path / "outputs"

    def test_ensure_creates_the_human_folder_tree(self, tmp_path: Path):
        root = tmp_path / "G2F"
        ensure_project_dirs(root)
        assert root.is_dir()
        assert (root / "uploads").is_dir()
        assert (root / "outputs").is_dir()

    def test_ensure_adopts_an_existing_folder_without_touching_files(self, tmp_path: Path):
        root = tmp_path / "G2F"
        (root / "data").mkdir(parents=True)
        (root / "data" / "trial.csv").write_text("rows")
        ensure_project_dirs(root)
        assert (root / "data" / "trial.csv").read_text() == "rows"


class TestFolderName:
    def test_keeps_human_readable_names(self):
        assert project_folder_name("G2F") == "G2F"
        assert project_folder_name("Drought Resistance 2032") == "Drought Resistance 2032"

    def test_strips_path_separators_and_traversal(self):
        assert "/" not in project_folder_name("a/b")
        assert ".." not in project_folder_name("..")
        assert project_folder_name("../../etc") != "../../etc"

    def test_never_returns_an_empty_or_hidden_name(self):
        assert project_folder_name("") == "project"
        assert project_folder_name("...") == "project"
        assert not project_folder_name(".hidden").startswith(".")


class TestResolveProjectVirtualPath:
    def test_user_data_root_is_the_project_root(self, tmp_path: Path):
        assert resolve_project_virtual_path(tmp_path, "/mnt/user-data") == tmp_path.resolve()

    def test_workspace_prefix_aliases_the_root(self, tmp_path: Path):
        assert resolve_project_virtual_path(tmp_path, "/mnt/user-data/workspace/src/plan.md") == tmp_path.resolve() / "src" / "plan.md"
        assert resolve_project_virtual_path(tmp_path, "/mnt/user-data/workspace") == tmp_path.resolve()

    def test_uploads_and_outputs_resolve_to_their_subfolders(self, tmp_path: Path):
        assert resolve_project_virtual_path(tmp_path, "/mnt/user-data/uploads/a.csv") == tmp_path.resolve() / "uploads" / "a.csv"
        assert resolve_project_virtual_path(tmp_path, "/mnt/user-data/outputs/report.pdf") == tmp_path.resolve() / "outputs" / "report.pdf"

    def test_plain_project_files_resolve_directly(self, tmp_path: Path):
        assert resolve_project_virtual_path(tmp_path, "/mnt/user-data/data/raw/x.txt") == tmp_path.resolve() / "data" / "raw" / "x.txt"

    def test_rejects_other_prefixes(self, tmp_path: Path):
        with pytest.raises(ValueError, match="must start with"):
            resolve_project_virtual_path(tmp_path, "/etc/passwd")
        with pytest.raises(ValueError, match="must start with"):
            resolve_project_virtual_path(tmp_path, "/mnt/user-dataX/secret")

    def test_rejects_traversal_escape(self, tmp_path: Path):
        with pytest.raises(ValueError, match="traversal"):
            resolve_project_virtual_path(tmp_path, "/mnt/user-data/../../../etc/passwd")
        with pytest.raises(ValueError, match="traversal"):
            resolve_project_virtual_path(tmp_path, "/mnt/user-data/workspace/../../escape")
