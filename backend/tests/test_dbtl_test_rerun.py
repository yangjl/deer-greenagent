"""The server-owned Test rerun, from lineage preflight through hash verdict."""

from __future__ import annotations

import hashlib
from pathlib import Path

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
from deerflow.dbtl.worker_result import StageWorkerResult, WorkerStatus

UNIT_WORKSPACE = "/mnt/user-data/outputs/.dbtl-stage-work/test-attempt/test/rerun-unit"
EXPECTED_OUTPUT = "/mnt/user-data/outputs/dbtl/build/model.bin"
COMMAND = "python /mnt/user-data/fit.py --seed 7"


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


def test_duplicate_expected_output_filenames_fail_before_dispatch(tmp_path: Path) -> None:
    lineage, build_output = _project(tmp_path)
    duplicate_uri = "/mnt/user-data/outputs/dbtl/other/model.bin"
    lineage["rerun_spec"]["expected_outputs"].append(duplicate_uri)
    lineage["output_artifacts"].append(
        {
            "uri": duplicate_uri,
            "content_hash": _sha(build_output.read_bytes()),
            "revision": 1,
        }
    )

    record = prepare_test_rerun(lineage, project_root=str(tmp_path))

    assert isinstance(record, RerunRecord)
    assert record.status is RerunStatus.FAILED
    assert "duplicate filenames" in record.reason


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

    tool = build_test_rerun_tool(
        unit,
        unit_workspace=UNIT_WORKSPACE,
    )
    assert tool.return_direct is True
    assert tool.tool_call_schema.model_json_schema()["properties"] == {}
