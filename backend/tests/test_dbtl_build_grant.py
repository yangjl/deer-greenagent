"""The server issues a Build phase its paths, and refuses source that invents its own.

Two workers on one cycle each wrote a host path into generated code — one that
had never existed on this machine — and then spent most of their token budget
failing to run it. The path was never theirs to choose: the server knows where
the grant is, so it hands the paths over and refuses source that names somewhere
else.
"""

from __future__ import annotations

import pytest

from deerflow.dbtl.build_grant import (
    INPUT_ENV_PREFIX,
    PROJECT_ROOT_ENV,
    WORKSPACE_ENV,
    build_input_grant,
    describe_foreign_paths,
    scan_foreign_paths,
)


class TestSourceMayNotNameAPathOutsideItsGrant:
    def test_a_host_path_is_refused(self) -> None:
        source = 'DATA = Path("/Users/someone/projects/app/backend/.deer-flow/users/u1/trial.csv")\n'

        found = scan_foreign_paths(source, allowed_roots=("/mnt/user-data",))

        assert [item.literal for item in found] == ["/Users/someone/projects/app/backend/.deer-flow/users/u1/trial.csv"]
        assert found[0].line == 1

    def test_a_workspace_path_is_allowed(self) -> None:
        source = 'DATA = Path("/mnt/user-data/trial_2025_yield.csv")\n'

        assert scan_foreign_paths(source, allowed_roots=("/mnt/user-data",)) == ()

    def test_a_path_under_an_extra_granted_root_is_allowed(self) -> None:
        source = 'OUT = "/mnt/stage-work/abc/artifacts/out.csv"\n'

        assert scan_foreign_paths(source, allowed_roots=("/mnt/user-data", "/mnt/stage-work/abc")) == ()

    def test_a_sibling_of_a_granted_root_is_not_inside_it(self) -> None:
        # "/mnt/user-data-other" starts with the root string but is a different
        # directory; a prefix comparison that missed this would grant it.
        source = 'DATA = "/mnt/user-data-other/secret.csv"\n'

        found = scan_foreign_paths(source, allowed_roots=("/mnt/user-data",))

        assert [item.literal for item in found] == ["/mnt/user-data-other/secret.csv"]

    def test_the_mount_a_failing_worker_reached_for_is_refused(self) -> None:
        source = 'mounted = Path("/mnt/data") / requested.name\n'

        found = scan_foreign_paths(source, allowed_roots=("/mnt/user-data",))

        assert [item.literal for item in found] == ["/mnt/data"]

    def test_an_interpreter_shebang_is_not_a_data_path(self) -> None:
        source = "#!/usr/bin/env python3\nimport csv\n"

        assert scan_foreign_paths(source, allowed_roots=("/mnt/user-data",)) == ()

    def test_a_url_is_not_a_filesystem_path(self) -> None:
        source = 'DOCS = "https://example.com/reference/guide"\n'

        assert scan_foreign_paths(source, allowed_roots=("/mnt/user-data",)) == ()

    def test_an_http_route_is_not_a_filesystem_path(self) -> None:
        source = "app.get('/api/health', handler)\n"

        assert scan_foreign_paths(source, allowed_roots=("/mnt/user-data",)) == ()

    def test_a_route_shaped_literal_used_as_a_file_is_still_refused(self) -> None:
        source = 'open("/api/secrets/token")\n'

        found = scan_foreign_paths(source, allowed_roots=())

        assert [item.literal for item in found] == ["/api/secrets/token"]

    def test_python_constant_composition_cannot_hide_a_foreign_path(self) -> None:
        source = "from pathlib import Path\na = Path('/') / 'etc' / 'passwd'\nb = '/' + 'Users/example/data.csv'\n"

        found = scan_foreign_paths(source, allowed_roots=("/mnt/user-data",))

        assert {item.literal for item in found} >= {"/etc/passwd", "/Users/example/data.csv"}

    def test_division_is_not_a_path(self) -> None:
        source = "rate = passed/total\nshare = passed / total\n"

        assert scan_foreign_paths(source, allowed_roots=("/mnt/user-data",)) == ()

    def test_a_single_segment_root_reference_is_not_reported(self) -> None:
        # A lone "/" or a bare "/tmp" with no child is far more often a regex,
        # a separator, or prose than an input this build would actually read.
        source = 'sep = "/"\nparts = value.split("/")\n'

        assert scan_foreign_paths(source, allowed_roots=("/mnt/user-data",)) == ()

    def test_a_hardcoded_temp_output_is_refused(self) -> None:
        source = 'REPORT = "/tmp/build-report.json"\n'

        found = scan_foreign_paths(source, allowed_roots=("/mnt/user-data",))

        assert [item.literal for item in found] == ["/tmp/build-report.json"]

    def test_every_offending_line_is_reported_once(self) -> None:
        source = 'A = "/Users/a/one.csv"\nB = "/Users/a/one.csv"\nC = "/home/b/two.csv"\n'

        found = scan_foreign_paths(source, allowed_roots=("/mnt/user-data",))

        assert [(item.literal, item.line) for item in found] == [
            ("/Users/a/one.csv", 1),
            ("/Users/a/one.csv", 2),
            ("/home/b/two.csv", 3),
        ]

    def test_the_refusal_names_the_literal_and_the_line(self) -> None:
        source = 'DATA = "/Users/a/trial.csv"\n'

        message = describe_foreign_paths(scan_foreign_paths(source, allowed_roots=("/mnt/user-data",)))

        assert "/Users/a/trial.csv" in message
        assert "line 1" in message

    def test_no_findings_describe_as_empty(self) -> None:
        assert describe_foreign_paths(()) == ""

    def test_reporting_is_bounded(self) -> None:
        source = "".join(f'P{index} = "/Users/a/file{index}.csv"\n' for index in range(200))

        found = scan_foreign_paths(source, allowed_roots=("/mnt/user-data",))

        assert len(found) <= 20


class TestTheServerIssuesTheInputPaths:
    def test_the_grant_names_the_workspace_and_project_root(self) -> None:
        grant = build_input_grant(
            workspace="/mnt/user-data/outputs/.dbtl-stage-work/a/build/b",
            project_root="/mnt/user-data",
            declared_inputs=(),
        )

        assert grant[WORKSPACE_ENV] == "/mnt/user-data/outputs/.dbtl-stage-work/a/build/b"
        assert grant[PROJECT_ROOT_ENV] == "/mnt/user-data"

    def test_declared_inputs_are_numbered_from_one(self) -> None:
        grant = build_input_grant(
            workspace="/w",
            project_root="/mnt/user-data",
            declared_inputs=("/mnt/user-data/trial.csv", "/mnt/user-data/notes.md"),
        )

        assert grant[f"{INPUT_ENV_PREFIX}1"] == "/mnt/user-data/trial.csv"
        assert grant[f"{INPUT_ENV_PREFIX}2"] == "/mnt/user-data/notes.md"
        assert grant[f"{INPUT_ENV_PREFIX}COUNT"] == "2"

    def test_the_grant_carries_only_strings(self) -> None:
        grant = build_input_grant(workspace="/w", project_root="/p", declared_inputs=("/p/a.csv",))

        assert all(isinstance(key, str) and isinstance(value, str) for key, value in grant.items())

    def test_an_input_outside_the_grant_is_refused(self) -> None:
        # The env channel must not become the way a foreign path reaches the
        # script that the source scanner would have refused.
        with pytest.raises(ValueError):
            build_input_grant(
                workspace="/mnt/user-data/w",
                project_root="/mnt/user-data",
                declared_inputs=("/Users/a/trial.csv",),
            )

    def test_the_input_order_is_the_declared_order(self) -> None:
        grant = build_input_grant(
            workspace="/mnt/user-data/w",
            project_root="/mnt/user-data",
            declared_inputs=("/mnt/user-data/b.csv", "/mnt/user-data/a.csv"),
        )

        assert grant[f"{INPUT_ENV_PREFIX}1"] == "/mnt/user-data/b.csv"
        assert grant[f"{INPUT_ENV_PREFIX}2"] == "/mnt/user-data/a.csv"
