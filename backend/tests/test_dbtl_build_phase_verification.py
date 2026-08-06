from __future__ import annotations

import sys
from pathlib import Path

import pytest

from deerflow.agents.dbtl.live_stage import adapter as adapter_module
from deerflow.agents.dbtl.live_stage.build_phase_verification import (
    VERIFY_STATUS,
    VERIFY_STDERR,
    VERIFY_STDOUT,
    execute_and_verify_phase,
    verification_shell_command,
)
from deerflow.agents.dbtl.live_stage.build_phases import BuildPhaseManifest
from deerflow.dbtl.build_grant import INPUT_ENV_PREFIX, WORKSPACE_ENV
from deerflow.sandbox.local.local_sandbox import LocalSandbox, PathMapping


def _workspace(root: Path) -> tuple[str, Path]:
    virtual = "/mnt/user-data/outputs/.dbtl-stage-work/attempt/build/phase"
    host = root / "outputs/.dbtl-stage-work/attempt/build/phase"
    (host / "src").mkdir(parents=True)
    (host / "logs").mkdir()
    (host / "src/run.py").write_text("print('ok')\n", encoding="utf-8")
    (host / "result.json").write_text("{}\n", encoding="utf-8")
    return virtual, host


def _manifest(
    workspace: str,
    *,
    inputs: tuple[str, ...] = (),
    execution_inputs: tuple[str, ...] | None = None,
) -> BuildPhaseManifest:
    return BuildPhaseManifest(
        entry_point=f"{workspace}/src/run.py",
        declared_outputs=(f"{workspace}/src/run.py", f"{workspace}/result.json"),
        completion_condition="the check passes",
        declared_inputs=inputs,
        execution_inputs=inputs if execution_inputs is None else execution_inputs,
        version=3,
    )


def test_the_server_issues_paths_and_derives_a_passing_receipt(tmp_path: Path) -> None:
    workspace, host = _workspace(tmp_path)
    data = tmp_path / "trial.csv"
    data.write_text("x\n1\n", encoding="utf-8")
    observed: dict[str, object] = {}

    def execute(command: str, env: dict[str, str], timeout: float) -> str:
        observed.update(command=command, env=env, timeout=timeout)
        (host / VERIFY_STDOUT).write_text("ok\n", encoding="utf-8")
        (host / VERIFY_STDERR).write_text("", encoding="utf-8")
        (host / VERIFY_STATUS).write_text("0\n", encoding="utf-8")
        return ""

    record = execute_and_verify_phase(
        _manifest(workspace, inputs=("/mnt/user-data/trial.csv",)),
        project_root=str(tmp_path),
        unit_workspace=workspace,
        execute=execute,
        timeout_seconds=45,
        issued_inputs=("/mnt/user-data/trial.csv",),
    )

    assert record.passed is True
    assert record.exit_status == 0
    assert len(record.logs) == 2
    assert len(record.outputs) == 2
    assert observed["timeout"] == 45
    env = observed["env"]
    assert isinstance(env, dict)
    assert env[WORKSPACE_ENV] == workspace
    assert env[f"{INPUT_ENV_PREFIX}1"] == "/mnt/user-data/trial.csv"


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is the local macOS process boundary")
def test_local_server_verifier_handles_a_project_path_with_spaces(tmp_path: Path) -> None:
    from deerflow.agents.middlewares.dbtl_output_policy_middleware import sandbox_exec_command

    project_root = tmp_path / "project with spaces"
    project_root.mkdir()
    workspace, _host = _workspace(project_root)
    issued = project_root / "trial.csv"
    issued.write_text("x\n1\n", encoding="utf-8")
    sandbox = LocalSandbox(
        "test",
        path_mappings=[PathMapping(container_path="/mnt/user-data", local_path=str(project_root))],
    )

    def execute(command: str, env: dict[str, str], timeout: float) -> str:
        isolated = sandbox_exec_command(
            command,
            writable_paths=(workspace,),
            readable_paths=("/mnt/user-data/trial.csv",),
            restricted_read_roots=("/mnt/user-data",),
        )
        return sandbox.execute_command(isolated, env=env, timeout=timeout)

    record = execute_and_verify_phase(
        _manifest(workspace, inputs=("/mnt/user-data/trial.csv",)),
        project_root=str(project_root),
        unit_workspace=workspace,
        execute=execute,
        timeout_seconds=45,
        issued_inputs=("/mnt/user-data/trial.csv",),
    )

    assert record.passed is True, record.reason


def test_a_nonzero_server_execution_is_a_failure(tmp_path: Path) -> None:
    workspace, host = _workspace(tmp_path)

    def execute(_command: str, _env: dict[str, str], _timeout: float) -> str:
        (host / VERIFY_STDOUT).write_text("", encoding="utf-8")
        (host / VERIFY_STDERR).write_text("missing column\n", encoding="utf-8")
        (host / VERIFY_STATUS).write_text("2\n", encoding="utf-8")
        return ""

    record = execute_and_verify_phase(
        _manifest(workspace),
        project_root=str(tmp_path),
        unit_workspace=workspace,
        execute=execute,
        timeout_seconds=45,
    )

    assert record.passed is False
    assert record.exit_status == 2
    assert "status 2" in record.reason


def test_a_notebook_cannot_be_used_as_the_server_entry_point(tmp_path: Path) -> None:
    workspace, host = _workspace(tmp_path)
    notebook = host / "outputs/replay.ipynb"
    notebook.parent.mkdir()
    notebook.write_text("{}\n", encoding="utf-8")
    called = False

    def execute(_command: str, _env: dict[str, str], _timeout: float) -> str:
        nonlocal called
        called = True
        return ""

    record = execute_and_verify_phase(
        BuildPhaseManifest(
            entry_point=f"{workspace}/outputs/replay.ipynb",
            declared_outputs=(f"{workspace}/outputs/replay.ipynb",),
            completion_condition="the playbook exists",
            version=3,
        ),
        project_root=str(tmp_path),
        unit_workspace=workspace,
        execute=execute,
        timeout_seconds=45,
    )

    assert record.passed is False
    assert "not an executable Build entry point" in record.reason
    assert called is False


def test_worker_authored_stale_receipts_cannot_fake_server_success(tmp_path: Path) -> None:
    workspace, host = _workspace(tmp_path)
    (host / VERIFY_STDOUT).write_text("forged\n", encoding="utf-8")
    (host / VERIFY_STDERR).write_text("", encoding="utf-8")
    (host / VERIFY_STATUS).write_text("0\n", encoding="utf-8")

    record = execute_and_verify_phase(
        _manifest(workspace),
        project_root=str(tmp_path),
        unit_workspace=workspace,
        execute=lambda _command, _env, _timeout: "Command timed out before it started",
        timeout_seconds=45,
    )

    assert record.passed is False
    assert "no readable exit-status receipt" in record.reason


def test_a_declared_input_outside_the_project_never_executes(tmp_path: Path) -> None:
    workspace, _host = _workspace(tmp_path)
    called = False

    def execute(_command: str, _env: dict[str, str], _timeout: float) -> str:
        nonlocal called
        called = True
        return ""

    record = execute_and_verify_phase(
        _manifest(workspace, inputs=("/Users/elsewhere/trial.csv",)),
        project_root=str(tmp_path),
        unit_workspace=workspace,
        execute=execute,
        timeout_seconds=45,
    )

    assert record.passed is False
    assert called is False


def test_implementation_only_inputs_are_not_injected_at_runtime(tmp_path: Path) -> None:
    workspace, host = _workspace(tmp_path)
    implementation_input = f"{workspace}/src/helper.py"
    (host / "src/helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    observed: dict[str, str] = {}

    def execute(_command: str, env: dict[str, str], _timeout: float) -> str:
        observed.update(env)
        (host / VERIFY_STDOUT).write_text("ok\n", encoding="utf-8")
        (host / VERIFY_STDERR).write_text("", encoding="utf-8")
        (host / VERIFY_STATUS).write_text("0\n", encoding="utf-8")
        return ""

    record = execute_and_verify_phase(
        _manifest(workspace, inputs=(implementation_input,), execution_inputs=()),
        project_root=str(tmp_path),
        unit_workspace=workspace,
        execute=execute,
        timeout_seconds=45,
    )

    assert record.passed is True
    assert f"{INPUT_ENV_PREFIX}1" not in observed


def test_execution_inputs_must_come_from_the_issued_grant(tmp_path: Path) -> None:
    workspace, _host = _workspace(tmp_path)
    called = False

    def execute(_command: str, _env: dict[str, str], _timeout: float) -> str:
        nonlocal called
        called = True
        return ""

    record = execute_and_verify_phase(
        _manifest(workspace, inputs=("/mnt/user-data/unissued.csv",)),
        project_root=str(tmp_path),
        unit_workspace=workspace,
        execute=execute,
        timeout_seconds=45,
        issued_inputs=("/mnt/user-data/issued.csv",),
    )

    assert record.passed is False
    assert "not in the server-issued phase grant" in record.reason
    assert called is False


def test_python_entry_points_have_a_server_owned_command() -> None:
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/a/build/p"

    command, _env = verification_shell_command(_manifest(workspace), unit_workspace=workspace)

    assert f"python '{workspace}/src/run.py'" in command
    assert "server-verification.stdout.log" in command


def test_verifier_quotes_virtual_paths_before_local_mount_rewrite() -> None:
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/a/build/p"

    command, _env = verification_shell_command(_manifest(workspace), unit_workspace=workspace)

    assert f"cd '{workspace}'" in command
    assert f"> '{workspace}/.server-verification-exit-status'" in command


def test_declared_script_languages_use_their_server_owned_interpreter() -> None:
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/a/build/p"
    expected = {
        ".R": "Rscript",
        ".js": "node",
        ".jl": "julia",
        ".rb": "ruby",
        ".pl": "perl",
        ".ts": "npx --no-install tsx",
    }

    for suffix, interpreter in expected.items():
        manifest = BuildPhaseManifest(
            entry_point=f"{workspace}/src/run{suffix}",
            declared_outputs=(f"{workspace}/src/run{suffix}",),
            completion_condition="done",
            version=3,
        )
        command, _env = verification_shell_command(manifest, unit_workspace=workspace)
        assert f"{interpreter} '{workspace}/src/run{suffix}'" in command


def test_remote_verification_fails_preflight_without_a_read_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    class Sandbox:
        def execute_command(self, command, env=None, timeout=None):
            assert command == "command -v bwrap"
            return "(no output)"

    class Provider:
        def get(self, sandbox_id):
            assert sandbox_id == "aio:test"
            return Sandbox()

    monkeypatch.setattr(adapter_module, "get_sandbox_provider", lambda: Provider())

    with pytest.raises(RuntimeError, match="bubblewrap"):
        adapter_module._execute_server_build_command(
            "true",
            {WORKSPACE_ENV: "/mnt/user-data/outputs/.dbtl-stage-work/a/build/p"},
            30,
            sandbox_state={"sandbox_id": "aio:test"},
            writable_workspace="/mnt/user-data/outputs/.dbtl-stage-work/a/build/p",
            thread_id="thread-1",
            user_id="user-1",
            project_id="project-1",
            project_root="/host/project",
        )


def test_build_verifier_keeps_jupyter_state_inside_the_phase_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/a/build/p"
    original_env = {WORKSPACE_ENV: workspace}
    captured: dict[str, object] = {}

    class Sandbox:
        def execute_command(self, command, env=None, timeout=None):
            captured.update(command=command, env=env, timeout=timeout)
            return "ok"

    class Provider:
        def get(self, sandbox_id):
            assert sandbox_id == "local:test"
            return Sandbox()

    monkeypatch.setattr(adapter_module.sys, "platform", "darwin")
    monkeypatch.setattr(adapter_module, "get_sandbox_provider", lambda: Provider())
    monkeypatch.setattr(adapter_module, "sandbox_exec_command", lambda command, **_kwargs: command)

    result = adapter_module._execute_server_build_command(
        "python -m jupyter --version",
        original_env,
        30,
        sandbox_state={"sandbox_id": "local:test"},
        writable_workspace=workspace,
        thread_id="thread-1",
        user_id="user-1",
        project_id="project-1",
        project_root="/host/project",
    )

    assert result == "ok"
    assert original_env == {WORKSPACE_ENV: workspace}
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["JUPYTER_CONFIG_DIR"] == f"{workspace}/.jupyter/config"
    assert env["JUPYTER_DATA_DIR"] == f"{workspace}/.jupyter/data"
    assert env["JUPYTER_RUNTIME_DIR"] == f"{workspace}/.jupyter/runtime"
    assert env["IPYTHONDIR"] == f"{workspace}/.ipython"
