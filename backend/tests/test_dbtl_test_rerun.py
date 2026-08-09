"""The server-owned Test rerun, from lineage preflight through hash verdict."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from deerflow.agents.dbtl.live_stage.adapter import (
    LiveStageAdapter,
    _has_reusable_test_evidence,
    _reusable_test_worker_results,
)
from deerflow.agents.dbtl.live_stage.test_rerun import (
    RERUN_EXIT_STATUS_NAME,
    RERUN_STDERR_NAME,
    RERUN_STDOUT_NAME,
    PreparedTestRerun,
    build_test_rerun_tool,
    parse_test_rerun_record,
    prepare_test_rerun,
    validate_test_rerun,
)
from deerflow.agents.dbtl.live_stage.test_rerun import (
    TestRerunRecord as RerunRecord,
)
from deerflow.agents.dbtl.live_stage.test_rerun import (
    TestRerunStatus as RerunStatus,
)
from deerflow.agents.dbtl.live_stage.test_rerun import (
    build_test_rerun_unit as make_test_rerun_unit,
)
from deerflow.dbtl.stage_runner import WorkUnit
from deerflow.dbtl.worker_result import StageWorkerResult, WorkerStatus

UNIT_WORKSPACE = "/mnt/user-data/outputs/.dbtl-stage-work/test-attempt/test/rerun-unit"
EXPECTED_OUTPUT = "/mnt/user-data/outputs/dbtl/build/model.bin"
COMMAND = "python /mnt/user-data/fit.py --seed 7"


def test_path_only_recovery_reuses_recorded_test_workers() -> None:
    rerun_unit = WorkUnit(
        unit_id="new-attempt-build-rerun",
        capability="reproducibility_rerun",
        agent_name="tester",
        prompt="Rerun.",
    )
    validity_unit = WorkUnit(
        unit_id="new-attempt-1-validity_assessment",
        capability="validity_assessment",
        agent_name="tester",
        prompt="Assess.",
    )
    rerun_record = RerunRecord(status=RerunStatus.PASSED, command="python fit.py", reason="Hashes match.")
    recorded = [
        {
            "stage_attempt_id": "stage-test-1",
            "unit_id": "recorded-attempt-build-rerun",
            "capability": rerun_unit.capability,
            "agent_name": rerun_unit.agent_name,
            "result": StageWorkerResult(
                status=WorkerStatus.COMPLETED,
                summary="Server-owned rerun passed.",
                capability=rerun_unit.capability,
                agent_name=rerun_unit.agent_name,
                provenance={"rerun_execution": rerun_record.as_dict()},
            ).as_dict(),
        },
        {
            "stage_attempt_id": "stage-test-1",
            "unit_id": "recorded-attempt-1-validity_assessment",
            "capability": validity_unit.capability,
            "agent_name": validity_unit.agent_name,
            "result": StageWorkerResult(
                status=WorkerStatus.COMPLETED,
                summary="Scientific checks passed.",
                capability=validity_unit.capability,
                agent_name=validity_unit.agent_name,
            ).as_dict(),
        },
    ]

    reused = _reusable_test_worker_results(
        recorded,
        stage_attempt_id="stage-test-1",
        units=(rerun_unit, validity_unit),
    )

    assert reused is not None
    reused_units, results, recovered_rerun = reused
    assert [item.unit_id for item in reused_units] == [
        "recorded-attempt-build-rerun",
        "recorded-attempt-1-validity_assessment",
    ]
    assert [item.summary for item in results] == ["Server-owned rerun passed.", "Scientific checks passed."]
    assert recovered_rerun == rerun_record


def test_recovery_control_requires_complete_current_attempt_test_evidence() -> None:
    rerun = StageWorkerResult(
        status=WorkerStatus.COMPLETED,
        summary="Rerun passed.",
        capability="reproducibility_rerun",
        agent_name="server",
        provenance={"rerun_execution": RerunRecord(status=RerunStatus.PASSED, command="python fit.py", reason="Hashes match.").as_dict()},
    ).as_dict()
    validity = StageWorkerResult(
        status=WorkerStatus.COMPLETED,
        summary="Checks passed.",
        capability="validity_assessment",
        agent_name="tester",
        provenance={"validity_assessment": {"metrics": [], "checks": []}},
    ).as_dict()
    rows = [
        {"stage_attempt_id": "current", "unit_id": "current-build-rerun", "result": rerun},
        {"stage_attempt_id": "current", "unit_id": "current-validity", "result": validity},
    ]

    assert _has_reusable_test_evidence(rows, stage_attempt_id="current") is True
    assert _has_reusable_test_evidence(rows[:1], stage_attempt_id="current") is False
    assert _has_reusable_test_evidence(rows, stage_attempt_id="different") is False
    mismatched = [
        rows[0],
        {**rows[1], "unit_id": "different-run-validity"},
    ]
    assert _has_reusable_test_evidence(mismatched, stage_attempt_id="current") is False


@pytest.mark.asyncio
async def test_path_recovery_uses_a_distinct_surface_even_when_an_assessment_exists() -> None:
    rerun = StageWorkerResult(
        status=WorkerStatus.COMPLETED,
        summary="Rerun passed.",
        capability="reproducibility_rerun",
        agent_name="server",
        provenance={"rerun_execution": RerunRecord(status=RerunStatus.PASSED, command="python fit.py", reason="Hashes match.").as_dict()},
    ).as_dict()
    validity = StageWorkerResult(
        status=WorkerStatus.COMPLETED,
        summary="Checks passed.",
        capability="validity_assessment",
        agent_name="tester",
        provenance={"validity_assessment": {"metrics": [], "checks": []}},
    ).as_dict()

    class Repo:
        async def get_cycle(self, cycle_id, *, project_id):
            return {
                "id": cycle_id,
                "state": "test",
                "db_revision": 10,
                "stages": [{"id": "test-attempt", "stage": "test", "status": "in_progress"}],
            }

        async def build_test_view(self, cycle_id, *, project_id):
            return {"validity_assessment": {"id": "assessment-1", "recommendation": "advance_to_learn"}}

        async def list_worker_runs(self, cycle_id, *, project_id, stage):
            return [
                {"stage_attempt_id": "test-attempt", "unit_id": "run-1-build-rerun", "result": rerun},
                {"stage_attempt_id": "test-attempt", "unit_id": "run-1-validity", "result": validity},
            ]

    marker = await LiveStageAdapter(repo=Repo(), app_config=None).recover_test_retry_control(
        project_id="project-1",
        cycle_id="cycle-1",
    )

    assert marker is not None
    assert marker["surface_id"] == "test-evidence:test-attempt"


@pytest.mark.asyncio
async def test_invalidated_evidence_route_recovers_the_learn_handoff() -> None:
    class Repo:
        async def get_cycle(self, cycle_id, *, project_id):
            return {
                "id": cycle_id,
                "state": "learn",
                "db_revision": 15,
                "stages": [
                    {"stage": "test", "status": "advanced_with_exception"},
                    {"stage": "learn", "status": "in_progress"},
                ],
            }

        async def build_test_view(self, cycle_id, *, project_id):
            return {
                "validity_assessment": {
                    "id": "assessment-invalidated",
                    "recommendation": "learn_from_invalidated_evidence",
                }
            }

    marker = await LiveStageAdapter(repo=Repo(), app_config=None).recover_test_learn_handoff(
        project_id="project-1",
        cycle_id="cycle-1",
    )

    assert marker == {
        "version": 1,
        "cycle_id": "cycle-1",
        "cycle_revision": 15,
        "approved_stage": "test",
        "next_stage": "learn",
        "surface_id": "assessment-invalidated",
        "advanced_with_exception": True,
    }


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _project(tmp_path: Path) -> tuple[dict, Path]:
    (tmp_path / "fit.py").write_text("print('fit')\n", encoding="utf-8")
    source = tmp_path / "yield.csv"
    source.write_text("yield\n1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='rerun'\n", encoding="utf-8")
    build_output = tmp_path / EXPECTED_OUTPUT.removeprefix("/mnt/user-data/")
    build_output.parent.mkdir(parents=True)
    build_output.write_bytes(b"model-v1")
    lineage = {
        "rerun_status": "verified",
        "rerun_spec": {
            "version": 1,
            "entry_point": "/mnt/user-data/fit.py",
            "command": COMMAND,
            "seed": 7,
            "inputs": ["/mnt/user-data/yield.csv"],
            "environment": {"python": "3.12"},
            "configuration": ["/mnt/user-data/pyproject.toml"],
            "expected_outputs": [EXPECTED_OUTPUT],
        },
        "input_artifacts": [f"workspace_file:yield.csv:sha256:{_sha(source.read_bytes())}"],
        "output_artifacts": [
            {
                "uri": EXPECTED_OUTPUT,
                "content_hash": _sha(build_output.read_bytes()),
                "revision": 1,
            }
        ],
    }
    return lineage, build_output


def _fresh_files(tmp_path: Path, *, output: bytes = b"model-v1", exit_status: int = 0) -> tuple[Path, Path, Path]:
    workspace = tmp_path / UNIT_WORKSPACE.removeprefix("/mnt/user-data/")
    workspace.mkdir(parents=True)
    stdout = workspace / RERUN_STDOUT_NAME
    stderr = workspace / RERUN_STDERR_NAME
    rerun_output = workspace / "model.bin"
    stdout.write_text("fit complete\n", encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    rerun_output.write_bytes(output)
    (workspace / RERUN_EXIT_STATUS_NAME).write_text(f"{exit_status}\n", encoding="utf-8")
    return stdout, stderr, rerun_output


def _result(
    tmp_path: Path,
    *,
    exit_status: int = 0,
    output: bytes = b"model-v1",
    status: WorkerStatus = WorkerStatus.COMPLETED,
    stop_reason: str | None = None,
) -> StageWorkerResult:
    _stdout, _stderr, _rerun_output = _fresh_files(tmp_path, output=output, exit_status=exit_status)
    return StageWorkerResult(
        status=status,
        summary="Executed the recorded command and retained its receipt.",
        capability="reproducibility_rerun",
        agent_name="reviewer",
        stop_reason=stop_reason,
    )


def _prepared(tmp_path: Path) -> PreparedTestRerun:
    lineage, _output = _project(tmp_path)
    prepared = prepare_test_rerun(lineage, project_root=str(tmp_path))
    assert isinstance(prepared, PreparedTestRerun)
    return prepared


def test_successful_rerun_binds_command_logs_exit_and_matching_hashes(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)

    record = validate_test_rerun(
        _result(tmp_path),
        prepared,
        project_root=str(tmp_path),
        unit_workspace=UNIT_WORKSPACE,
    )

    assert record.status is RerunStatus.PASSED
    assert record.command == COMMAND
    assert record.exit_status == 0
    assert len(record.logs) == 2
    assert record.outputs[0]["content_hash"] == record.outputs[0]["build_hash"]
    assert parse_test_rerun_record(record.as_dict()) == record


def test_nonzero_exit_is_a_failed_rerun_with_retained_logs(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)

    record = validate_test_rerun(
        _result(tmp_path, exit_status=9),
        prepared,
        project_root=str(tmp_path),
        unit_workspace=UNIT_WORKSPACE,
    )

    assert record.status is RerunStatus.FAILED
    assert record.exit_status == 9
    assert len(record.logs) == 2


def test_missing_typed_command_is_recorded_as_unverified_not_dispatched(tmp_path: Path) -> None:
    record = prepare_test_rerun(
        {"rerun_status": "rerun_unverified", "rerun_spec": {}},
        project_root=str(tmp_path),
    )

    assert isinstance(record, RerunRecord)
    assert record.status is RerunStatus.MISSING


def test_timeout_or_cap_cannot_be_reported_as_reproducible(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    timed_out = StageWorkerResult(
        status=WorkerStatus.FAILED,
        summary="The worker timed out before the tool completed.",
        capability="reproducibility_rerun",
        agent_name="reviewer",
        stop_reason="turn_capped",
    )

    record = validate_test_rerun(
        timed_out,
        prepared,
        project_root=str(tmp_path),
        unit_workspace=UNIT_WORKSPACE,
    )

    assert record.status is RerunStatus.FAILED
    assert "turn_capped" in record.reason


def test_changed_declared_input_fails_preflight_before_dispatch(tmp_path: Path) -> None:
    lineage, _output = _project(tmp_path)
    (tmp_path / "yield.csv").write_text("yield\n2\n", encoding="utf-8")

    record = prepare_test_rerun(lineage, project_root=str(tmp_path))

    assert isinstance(record, RerunRecord)
    assert record.status is RerunStatus.FAILED
    assert "has changed" in record.reason


def test_legacy_lineage_binding_in_rerun_inputs_is_normalized_to_its_project_path(tmp_path: Path) -> None:
    lineage, _output = _project(tmp_path)
    binding = lineage["input_artifacts"][0]
    lineage["rerun_spec"]["inputs"].append(binding)

    prepared = prepare_test_rerun(lineage, project_root=str(tmp_path))

    assert isinstance(prepared, PreparedTestRerun)
    assert prepared.spec.inputs == ("/mnt/user-data/yield.csv",)


def test_published_entrypoint_rebinds_a_simple_relative_command_and_ignores_configuration_prose(tmp_path: Path) -> None:
    lineage, _output = _project(tmp_path)
    published = tmp_path / "outputs/dbtl/build/0123456789abcdef-fit.py"
    published.parent.mkdir(parents=True, exist_ok=True)
    published.write_text("print('fit')\n", encoding="utf-8")
    lineage["rerun_spec"].update(
        {
            "entry_point": "/mnt/user-data/outputs/dbtl/build/0123456789abcdef-fit.py",
            "command": "python fit.py",
            "configuration": ["Run this from a clean workspace.", "/mnt/user-data/pyproject.toml"],
        }
    )
    lineage["output_artifacts"].append(
        {
            "uri": "/mnt/user-data/outputs/dbtl/build/0123456789abcdef-fit.py",
            "content_hash": _sha(published.read_bytes()),
            "revision": 1,
        }
    )

    prepared = prepare_test_rerun(lineage, project_root=str(tmp_path))

    assert isinstance(prepared, PreparedTestRerun)
    # The portable command resolves through the Test worker's execution PATH.
    assert prepared.spec.command == "python 'fit.py'"
    assert prepared.staged_files == (("/mnt/user-data/outputs/dbtl/build/0123456789abcdef-fit.py", "fit.py"),)
    assert prepared.spec.configuration == ("/mnt/user-data/pyproject.toml",)


def test_published_build_support_files_are_staged_without_preseeding_expected_outputs(tmp_path: Path) -> None:
    lineage, build_output = _project(tmp_path)
    published = tmp_path / "outputs/dbtl/build"
    published.mkdir(parents=True, exist_ok=True)
    entrypoint = published / "0123456789abcdef-validate_build.py"
    support = published / "fedcba9876543210-fit.py"
    notebook = published / "0011223344556677-replay.ipynb"
    entrypoint.write_text("print('validate')\n", encoding="utf-8")
    support.write_text("print('fit')\n", encoding="utf-8")
    notebook.write_text("{}\n", encoding="utf-8")
    lineage["rerun_spec"].update(
        {
            "entry_point": f"/mnt/user-data/{entrypoint.relative_to(tmp_path)}",
            "command": "python validate_build.py",
        }
    )
    lineage["output_artifacts"].extend(
        {
            "uri": f"/mnt/user-data/{path.relative_to(tmp_path)}",
            "content_hash": _sha(path.read_bytes()),
            "revision": 1,
        }
        for path in (entrypoint, support, notebook)
    )

    prepared = prepare_test_rerun(lineage, project_root=str(tmp_path))

    assert isinstance(prepared, PreparedTestRerun)
    assert prepared.spec.command == "python 'validate_build.py'"
    assert prepared.staged_files == (
        (f"/mnt/user-data/{entrypoint.relative_to(tmp_path)}", "validate_build.py"),
        (f"/mnt/user-data/{support.relative_to(tmp_path)}", "fit.py"),
        (f"/mnt/user-data/{notebook.relative_to(tmp_path)}", "replay.ipynb"),
    )
    assert all(source != EXPECTED_OUTPUT for source, _name in prepared.staged_files)

    unit = make_test_rerun_unit(
        prepared,
        attempt_id="attempt-1",
        agent_name="reviewer",
        via_generalist=True,
    )
    assert unit.tool_contract["staged_files"] == [{"source": source, "name": name} for source, name in prepared.staged_files]


def test_published_build_support_file_can_be_a_declared_rerun_input(tmp_path: Path) -> None:
    lineage, _build_output = _project(tmp_path)
    published = tmp_path / "outputs/dbtl/build/fedcba9876543210-training.csv"
    published.parent.mkdir(parents=True, exist_ok=True)
    published.write_text("x,y\n0,1\n", encoding="utf-8")
    uri = f"/mnt/user-data/{published.relative_to(tmp_path)}"
    lineage["rerun_spec"]["inputs"].append(uri)
    lineage["output_artifacts"].append(
        {
            "uri": uri,
            "content_hash": _sha(published.read_bytes()),
            "revision": 1,
            "source_path": "training.csv",
        }
    )

    prepared = prepare_test_rerun(lineage, project_root=str(tmp_path))

    assert isinstance(prepared, PreparedTestRerun)
    assert (uri, "training.csv") in prepared.staged_files


def test_published_build_support_files_restore_their_package_relative_paths(tmp_path: Path) -> None:
    lineage, _build_output = _project(tmp_path)
    published = tmp_path / "outputs/dbtl/build"
    published.mkdir(parents=True, exist_ok=True)
    entrypoint = published / "0123456789abcdef-run.py"
    training = published / "fedcba9876543210-training.csv"
    entrypoint.write_text("from pathlib import Path\nPath('data/training.csv').read_text()\n", encoding="utf-8")
    training.write_text("x,y\n0,1\n", encoding="utf-8")
    lineage["rerun_spec"].update(
        {
            "entry_point": f"/mnt/user-data/{entrypoint.relative_to(tmp_path)}",
            "command": "python src/run.py",
        }
    )
    lineage["output_artifacts"].extend(
        [
            {
                "uri": f"/mnt/user-data/{entrypoint.relative_to(tmp_path)}",
                "content_hash": _sha(entrypoint.read_bytes()),
                "revision": 1,
                "source_path": "src/run.py",
            },
            {
                "uri": f"/mnt/user-data/{training.relative_to(tmp_path)}",
                "content_hash": _sha(training.read_bytes()),
                "revision": 1,
                "source_path": "data/training.csv",
            },
        ]
    )

    prepared = prepare_test_rerun(lineage, project_root=str(tmp_path))

    assert isinstance(prepared, PreparedTestRerun)
    assert prepared.spec.command == "python 'src/run.py'"
    assert prepared.staged_files == (
        (f"/mnt/user-data/{entrypoint.relative_to(tmp_path)}", "src/run.py"),
        (f"/mnt/user-data/{training.relative_to(tmp_path)}", "data/training.csv"),
    )


def test_unsafe_published_source_path_fails_before_test_dispatch(tmp_path: Path) -> None:
    lineage, _build_output = _project(tmp_path)
    published = tmp_path / "outputs/dbtl/build/0123456789abcdef-run.py"
    published.parent.mkdir(parents=True, exist_ok=True)
    published.write_text("print('run')\n", encoding="utf-8")
    lineage["rerun_spec"].update(
        {
            "entry_point": f"/mnt/user-data/{published.relative_to(tmp_path)}",
            "command": "python run.py",
        }
    )
    lineage["output_artifacts"].append(
        {
            "uri": f"/mnt/user-data/{published.relative_to(tmp_path)}",
            "content_hash": _sha(published.read_bytes()),
            "revision": 1,
            "source_path": "../run.py",
        }
    )

    record = prepare_test_rerun(lineage, project_root=str(tmp_path))

    assert isinstance(record, RerunRecord)
    assert record.status is RerunStatus.FAILED
    assert "unsafe recorded source path" in record.reason


def test_published_entrypoint_itself_must_be_in_the_hash_bound_staging_set(tmp_path: Path) -> None:
    lineage, _build_output = _project(tmp_path)
    published = tmp_path / "outputs/dbtl/build"
    published.mkdir(parents=True, exist_ok=True)
    entrypoint = published / "0123456789abcdef-run.py"
    decoy = published / "fedcba9876543210-run.py"
    entrypoint.write_text("print('approved entry')\n", encoding="utf-8")
    decoy.write_text("print('different support file')\n", encoding="utf-8")
    lineage["rerun_spec"].update(
        {
            "entry_point": f"/mnt/user-data/{entrypoint.relative_to(tmp_path)}",
            "command": "python run.py",
        }
    )
    lineage["output_artifacts"].append(
        {
            "uri": f"/mnt/user-data/{decoy.relative_to(tmp_path)}",
            "content_hash": _sha(decoy.read_bytes()),
            "revision": 1,
            "source_path": "run.py",
        }
    )

    record = prepare_test_rerun(lineage, project_root=str(tmp_path))

    assert isinstance(record, RerunRecord)
    assert record.status is RerunStatus.FAILED
    assert "entry point is not available" in record.reason


def test_duplicate_expected_output_filenames_fail_before_dispatch(tmp_path: Path) -> None:
    lineage, build_output = _project(tmp_path)
    duplicate_uri = "/mnt/user-data/outputs/dbtl/other/model.bin"
    different = tmp_path / duplicate_uri.removeprefix("/mnt/user-data/")
    different.parent.mkdir(parents=True, exist_ok=True)
    different.write_bytes(b"different-model")
    lineage["rerun_spec"]["expected_outputs"].append(duplicate_uri)
    lineage["output_artifacts"].append(
        {
            "uri": duplicate_uri,
            "content_hash": _sha(different.read_bytes()),
            "revision": 1,
        }
    )

    record = prepare_test_rerun(lineage, project_root=str(tmp_path))

    assert isinstance(record, RerunRecord)
    assert record.status is RerunStatus.FAILED
    assert "duplicate filenames and different approved hashes" in record.reason


def test_published_hash_prefix_is_not_part_of_the_fresh_output_filename(tmp_path: Path) -> None:
    lineage, build_output = _project(tmp_path)
    published = "/mnt/user-data/outputs/dbtl/build/" + _sha(build_output.read_bytes())[:16] + "-model.bin"
    lineage["rerun_spec"]["expected_outputs"] = [published]
    lineage["output_artifacts"] = [{"uri": published, "content_hash": _sha(build_output.read_bytes()), "revision": 1}]

    prepared = prepare_test_rerun(lineage, project_root=str(tmp_path))
    assert isinstance(prepared, PreparedTestRerun)
    record = validate_test_rerun(_result(tmp_path), prepared, project_root=str(tmp_path), unit_workspace=UNIT_WORKSPACE)

    assert record.status is RerunStatus.PASSED


def test_changed_output_hash_is_retained_and_fails_reproducibility(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)

    record = validate_test_rerun(
        _result(tmp_path, output=b"different-model"),
        prepared,
        project_root=str(tmp_path),
        unit_workspace=UNIT_WORKSPACE,
    )

    assert record.status is RerunStatus.FAILED
    assert record.outputs[0]["content_hash"] != record.outputs[0]["build_hash"]


def test_approved_output_outside_fresh_workspace_cannot_substitute_for_a_rerun_output(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)
    result = _result(tmp_path)
    fresh_output = tmp_path / UNIT_WORKSPACE.removeprefix("/mnt/user-data/") / "model.bin"
    fresh_output.unlink()

    record = validate_test_rerun(
        result,
        prepared,
        project_root=str(tmp_path),
        unit_workspace=UNIT_WORKSPACE,
    )

    assert record.status is RerunStatus.FAILED
    assert "produced 0 fresh files" in record.reason


def test_rerun_unit_uses_the_existing_stage_workspace_and_exact_command(tmp_path: Path) -> None:
    prepared = _prepared(tmp_path)

    unit = make_test_rerun_unit(
        prepared,
        attempt_id="attempt-1",
        agent_name="reviewer",
        via_generalist=True,
    )

    assert COMMAND in unit.prompt
    assert "__DBTL_UNIT_WORKSPACE__" in unit.prompt
    assert unit.role == "rerun"
    assert unit.tool_contract["granted_inputs"] == list(prepared.spec.inputs)

    tool = build_test_rerun_tool(
        unit,
        unit_workspace=UNIT_WORKSPACE,
    )
    assert tool.return_direct is False
    assert tool.tool_call_schema.model_json_schema()["properties"] == {}


@pytest.mark.anyio
async def test_rerun_tool_executes_bound_command_then_returns_control_to_worker(monkeypatch, tmp_path: Path) -> None:
    base = _prepared(tmp_path)
    prepared = PreparedTestRerun(
        spec=base.spec,
        output_hashes=base.output_hashes,
        staged_files=(("/mnt/user-data/outputs/dbtl/build/0123456789abcdef-fit.py", "src/fit.py"),),
    )
    unit = make_test_rerun_unit(
        prepared,
        attempt_id="attempt-1",
        agent_name="reviewer",
        via_generalist=True,
    )
    calls: list[tuple[object, str, str]] = []

    async def fake_bash(runtime, description: str, command: str) -> str:
        calls.append((runtime, description, command))
        return "ok"

    from deerflow.agents.dbtl.live_stage import test_rerun

    monkeypatch.setattr(test_rerun.bash_tool, "coroutine", fake_bash)
    tool = build_test_rerun_tool(unit, unit_workspace=UNIT_WORKSPACE)
    runtime = object()

    result = await tool.coroutine(runtime)

    assert tool.return_direct is False
    assert result.endswith("Native sandbox response: ok")
    assert calls[0][0] is runtime
    assert calls[0][1] == "Execute the server-bound Build rerun and retain its receipt."
    assert f"mkdir -p {UNIT_WORKSPACE}/src" in calls[0][2]
    assert f"cp /mnt/user-data/outputs/dbtl/build/0123456789abcdef-fit.py {UNIT_WORKSPACE}/src/fit.py" in calls[0][2]
    assert COMMAND in calls[0][2]
