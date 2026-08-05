"""The server's own record of how to re-run a Build that ran as several phases.

Test re-runs a Build as one command. A phased Build has one entry point per
phase and the worker-supplied specs merge only when they agree, so before this
a multi-phase Build recorded no rerun spec at all and could never satisfy
`structured_rerun_spec`.
"""

from __future__ import annotations

import shlex

from deerflow.dbtl.build_driver import (
    MAX_DRIVER_PHASES,
    DriverPhase,
    driver_rerun_spec,
    render_driver_script,
)

WORKSPACE = "/mnt/user-data/outputs/.dbtl-stage-work/w1"
PROJECT = "/mnt/user-data"


def _phase(title: str, entry: str, *inputs: str) -> DriverPhase:
    return DriverPhase(title=title, entry_point=entry, execution_inputs=tuple(inputs))


def _render(*phases: DriverPhase) -> str:
    return render_driver_script(list(phases), workspace_root=WORKSPACE, project_root=PROJECT)


class TestThePhasesRunInTheOrderTheyRan:
    def test_every_entry_point_appears_once_in_plan_order(self) -> None:
        script = _render(
            _phase("Freeze the spec", "/mnt/user-data/src/freeze.py"),
            _phase("Run the pilot", "/mnt/user-data/src/run.py"),
            _phase("Assess", "/mnt/user-data/src/assess.R"),
        )

        assert script.index("freeze.py") < script.index("run.py") < script.index("assess.R")

    def test_each_entry_point_uses_the_interpreter_the_server_used(self) -> None:
        # The driver must re-run a phase the way server verification ran it, or
        # a reproduction failure would be an artefact of the driver.
        script = _render(
            _phase("Python", "/mnt/user-data/src/a.py"),
            _phase("R", "/mnt/user-data/src/b.R"),
            _phase("Shell", "/mnt/user-data/src/c.sh"),
        )

        assert "python '/mnt/user-data/src/a.py'" in script
        assert "Rscript '/mnt/user-data/src/b.R'" in script
        assert "/bin/bash '/mnt/user-data/src/c.sh'" in script

    def test_a_failing_phase_stops_the_script(self) -> None:
        # Without this a phase that failed would be stepped over and the script
        # would still exit 0 — Test would record a successful reproduction of a
        # Build that did not reproduce.
        assert "set -euo pipefail" in _render(_phase("One", "/mnt/user-data/src/a.py"))

    def test_test_can_override_the_build_workspace_with_its_fresh_workspace(self) -> None:
        script = _render(_phase("One", "/mnt/user-data/src/a.py"))

        assert 'if [ -z "${DBTL_WORKSPACE:-}" ]' in script


class TestInputsAreNumberedWithinTheirOwnPhase:
    def test_each_phase_renumbers_from_one(self) -> None:
        # DBTL_INPUT_n is per phase. Exporting a union would hand phase two the
        # wrong file under the right name.
        script = _render(
            _phase("First", "/mnt/user-data/src/a.py", "/mnt/user-data/trials.csv"),
            _phase("Second", "/mnt/user-data/src/b.py", "/mnt/user-data/pilot.parquet"),
        )

        assert "export DBTL_INPUT_1=/mnt/user-data/trials.csv" in script
        assert "export DBTL_INPUT_1=/mnt/user-data/pilot.parquet" in script

    def test_a_phase_that_reads_nothing_still_states_its_count(self) -> None:
        script = _render(_phase("None", "/mnt/user-data/src/a.py"))

        assert "export DBTL_INPUT_COUNT=0" in script

    def test_a_path_with_a_space_is_quoted(self) -> None:
        script = _render(_phase("Spaces", "/mnt/user-data/src/a.py", "/mnt/user-data/my trials.csv"))

        assert shlex.quote("/mnt/user-data/my trials.csv") in script


class TestThereIsNothingToRun:
    def test_no_phases_renders_no_script(self) -> None:
        assert _render() == ""

    def test_a_phase_with_no_entry_point_is_not_runnable(self) -> None:
        assert _render(_phase("Empty", "  ")) == ""

    def test_an_implausible_plan_is_refused_rather_than_rendered(self) -> None:
        many = [_phase(f"P{index}", f"/mnt/user-data/src/{index}.py") for index in range(MAX_DRIVER_PHASES + 1)]

        assert render_driver_script(many, workspace_root=WORKSPACE, project_root=PROJECT) == ""


class TestTheRerunRecordNamesTheDriver:
    def test_the_command_runs_the_driver_script(self) -> None:
        spec = driver_rerun_spec(
            [_phase("One", "/mnt/user-data/src/a.py", "/mnt/user-data/trials.csv")],
            driver_path="/mnt/user-data/outputs/dbtl/c1/build/rerun-build.sh",
            expected_outputs=["/mnt/user-data/outputs/dbtl/c1/build/model.json"],
            environment={"runtime": "python3.12"},
        )

        assert spec is not None
        assert spec.entry_point == "/mnt/user-data/outputs/dbtl/c1/build/rerun-build.sh"
        assert spec.command == "/bin/bash /mnt/user-data/outputs/dbtl/c1/build/rerun-build.sh"

    def test_inputs_and_outputs_are_unioned_in_order_without_duplicates(self) -> None:
        spec = driver_rerun_spec(
            [
                _phase("One", "/mnt/user-data/src/a.py", "/mnt/user-data/trials.csv"),
                _phase("Two", "/mnt/user-data/src/b.py", "/mnt/user-data/trials.csv", "/mnt/user-data/pilot.parquet"),
            ],
            driver_path="/mnt/user-data/outputs/dbtl/c1/build/rerun-build.sh",
            expected_outputs=["/mnt/user-data/out/x.json", "/mnt/user-data/out/x.json"],
            environment={"runtime": "python3.12"},
        )

        assert spec is not None
        assert spec.inputs == ("/mnt/user-data/trials.csv", "/mnt/user-data/pilot.parquet")
        assert spec.expected_outputs == ("/mnt/user-data/out/x.json",)

    def test_lineage_bindings_become_paths_and_never_enter_the_executable_spec(self) -> None:
        digest = "a" * 64
        spec = driver_rerun_spec(
            [_phase("One", "/mnt/user-data/src/a.py", "/mnt/user-data/data/train.csv")],
            driver_path="/mnt/user-data/outputs/dbtl/c1/build/rerun-build.sh",
            expected_outputs=["/mnt/user-data/out/x.json"],
            environment={"runtime": "python3.12"},
            bound_inputs=[
                f"workspace_file:data/train.csv:sha256:{digest}",
                f"workspace_file:data/holdout.csv:sha256:{digest}",
                f"hash:{digest}",
            ],
        )

        assert spec is not None
        assert spec.inputs == ("/mnt/user-data/data/train.csv", "/mnt/user-data/data/holdout.csv")
        assert all(not path.startswith("workspace_file:") for path in spec.inputs)

    def test_a_build_with_nothing_runnable_records_no_rerun(self) -> None:
        # An empty spec would satisfy the gate while promising a reproduction
        # nobody could perform, which is the failure the gate exists to catch.
        assert (
            driver_rerun_spec(
                [],
                driver_path="/mnt/user-data/outputs/dbtl/c1/build/rerun-build.sh",
                expected_outputs=[],
                environment={},
            )
            is None
        )
