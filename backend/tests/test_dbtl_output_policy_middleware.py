from __future__ import annotations

import json
import shlex
import subprocess
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from deerflow.agents.middlewares.dbtl_output_policy_middleware import (
    DbtlOutputPolicyMiddleware,
    is_dbtl_owned_path,
)
from deerflow.sandbox.tools import bash_tool


def _request(name: str, args: dict, *, runtime=None):
    request = SimpleNamespace(
        tool_call={"name": name, "id": "call-1", "args": args},
        runtime=runtime or SimpleNamespace(context={}),
    )
    request.override = lambda **updates: SimpleNamespace(
        tool_call=updates.get("tool_call", request.tool_call),
        runtime=request.runtime,
    )
    return request


@pytest.mark.parametrize(
    "path",
    [
        "outputs/dbtl/cycle-1/build/result.md",
        "/mnt/user-data/workspace/outputs/dbtl/cycle-1/test/result.json",
        "./outputs/dbtl/file",
    ],
)
def test_recognizes_governed_paths(path: str) -> None:
    assert is_dbtl_owned_path(path)


def test_blocks_path_aware_writes_and_never_calls_the_tool() -> None:
    called = False

    def handler(_request):
        nonlocal called
        called = True
        return ToolMessage(content="wrote", tool_call_id="call-1")

    result = DbtlOutputPolicyMiddleware().wrap_tool_call(
        _request("write_file", {"path": "/mnt/user-data/workspace/outputs/dbtl/cycle/build/file"}),
        handler,
    )

    assert not called
    assert "read-only" in str(result.content)


@pytest.mark.parametrize(
    "command",
    [
        "cp /tmp/result outputs/dbtl/cycle/build/result",
        "echo ok > /mnt/user-data/workspace/outputs/dbtl/cycle/build/result",
        "touch /mnt/user-data/outputs/dbtl/cycle/build/result",
        "cd outputs/dbtl && mv /tmp/result .",
    ],
)
def test_blocks_direct_shell_routes(command: str) -> None:
    result = DbtlOutputPolicyMiddleware().wrap_tool_call(
        _request("bash", {"command": command}),
        lambda _request: ToolMessage(content="ran", tool_call_id="call-1"),
    )
    assert "blocked" in str(result.content)


def test_ordinary_output_paths_remain_writable() -> None:
    result = DbtlOutputPolicyMiddleware().wrap_tool_call(
        _request("write_file", {"path": "/mnt/user-data/outputs/report.md"}),
        lambda _request: ToolMessage(content="wrote", tool_call_id="call-1"),
    )
    assert result.content == "wrote"


def test_ordinary_agents_cannot_write_the_stage_workspace() -> None:
    path = "/mnt/user-data/outputs/.dbtl-stage-work/attempt-1/build/result.json"

    result = DbtlOutputPolicyMiddleware().wrap_tool_call(
        _request("write_file", {"path": path}),
        lambda _request: ToolMessage(content="wrote", tool_call_id="call-1"),
    )

    assert "blocked" in str(result.content)


def test_stage_worker_can_write_only_its_exact_workspace() -> None:
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/attempt-1/build"
    middleware = DbtlOutputPolicyMiddleware(writable_paths=(workspace,))

    allowed = middleware.wrap_tool_call(
        _request("write_file", {"path": f"{workspace}/result.json"}),
        lambda _request: ToolMessage(content="wrote", tool_call_id="call-1"),
    )
    sibling = middleware.wrap_tool_call(
        _request("write_file", {"path": "/mnt/user-data/outputs/.dbtl-stage-work/attempt-2/build/result.json"}),
        lambda _request: ToolMessage(content="wrote", tool_call_id="call-1"),
    )
    escaped = middleware.wrap_tool_call(
        _request("write_file", {"path": f"{workspace}/../../attempt-2/build/result.json"}),
        lambda _request: ToolMessage(content="wrote", tool_call_id="call-1"),
    )
    governed = middleware.wrap_tool_call(
        _request("write_file", {"path": "/mnt/user-data/outputs/dbtl/cycle/build/result.json"}),
        lambda _request: ToolMessage(content="wrote", tool_call_id="call-1"),
    )

    assert allowed.content == "wrote"
    assert "blocked" in str(sibling.content)
    assert "blocked" in str(escaped.content)
    assert "blocked" in str(governed.content)


def test_stage_worker_shell_is_limited_to_its_exact_workspace() -> None:
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/attempt-1/build"
    middleware = DbtlOutputPolicyMiddleware(writable_paths=(workspace,))

    allowed = middleware.wrap_tool_call(
        _request("bash", {"command": f"cd {workspace} && python simulate.py"}),
        lambda _request: ToolMessage(content="ran", tool_call_id="call-1"),
    )
    escaped = middleware.wrap_tool_call(
        _request("bash", {"command": f"cd {workspace}/../.. && touch attempt-2/result.json"}),
        lambda _request: ToolMessage(content="ran", tool_call_id="call-1"),
    )

    assert allowed.content == "ran"
    assert "blocked" in str(escaped.content)


def test_local_shell_uses_process_level_isolation_for_relative_path_bypasses() -> None:
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/attempt-1/build/unit-1"
    middleware = DbtlOutputPolicyMiddleware(
        writable_paths=(workspace,),
        shell_isolation="sandbox-exec",
    )
    seen: list[str] = []

    result = middleware.wrap_tool_call(
        _request(
            "bash",
            {"command": ("cd /mnt/user-data/outputs && touch .dbtl-stage-work/attempt-2/build/forged.json")},
        ),
        lambda request: seen.append(request.tool_call["args"]["command"]) or ToolMessage(content="ran", tool_call_id="call-1"),
    )

    assert result.content == "ran"
    assert seen and seen[0].startswith("sandbox-exec -p ")
    assert "(deny file-write*)" in seen[0]
    assert workspace in seen[0]


def test_stage_shell_may_embed_governed_input_paths_as_heredoc_data() -> None:
    """A provenance string is data; sandbox-exec still guards real writes."""
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/attempt-1/build/unit-1"
    command = f"""cat > {workspace}/result.json <<'JSON'
{{"source": "/mnt/user-data/outputs/dbtl/cycle/design/review.md"}}
JSON"""
    seen: list[str] = []

    result = DbtlOutputPolicyMiddleware(
        writable_paths=(workspace,),
        shell_isolation="sandbox-exec",
    ).wrap_tool_call(
        _request("bash", {"command": command}),
        lambda request: seen.append(request.tool_call["args"]["command"]) or ToolMessage(content="ran", tool_call_id="call-1"),
    )

    assert result.content == "ran"
    assert seen and "(deny file-write*)" in seen[0]
    assert f'(allow file-write* (subpath "{workspace}"))' in seen[0]


@pytest.mark.parametrize(
    ("opening_delimiter", "closing_delimiter"),
    [
        ("'JSON'", "JSON"),
        ("'JSON-DOC'", "JSON-DOC"),
        (r"\JSON-DOC", "JSON-DOC"),
    ],
)
def test_real_local_bash_preprocessing_preserves_heredoc_provenance(
    tmp_path,
    monkeypatch,
    opening_delimiter: str,
    closing_delimiter: str,
) -> None:
    """The bash tool translates the target operand, never the JSON body."""
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/attempt-1/build/unit-1"
    source = "/mnt/user-data/outputs/dbtl/cycle/design/review.md"
    host_outputs = tmp_path / "outputs"
    host_workspace = host_outputs / ".dbtl-stage-work" / "attempt-1" / "build" / "unit-1"
    host_workspace.mkdir(parents=True)
    thread_data = {
        "workspace_path": str(tmp_path / "workspace"),
        "uploads_path": str(tmp_path / "uploads"),
        "outputs_path": str(host_outputs),
    }
    runtime = SimpleNamespace(
        context={},
        state={"sandbox": {"sandbox_id": "local:test:thread"}, "thread_data": thread_data},
    )

    class _ExecutingSandbox:
        def execute_command(self, command, env=None, timeout=None):
            arguments = shlex.split(command)
            bash_index = arguments.index("/bin/bash")
            completed = subprocess.run(
                arguments[bash_index:],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
            )
            return completed.stdout + completed.stderr

    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda _runtime: _ExecutingSandbox())
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda _runtime: None)
    monkeypatch.setattr("deerflow.sandbox.tools.is_host_bash_allowed", lambda: True)

    command = f"""cat > {workspace}/result.json <<{opening_delimiter}
{{"source": "{source}"}}
{closing_delimiter}"""
    request = _request("bash", {"command": command}, runtime=runtime)

    result = DbtlOutputPolicyMiddleware(
        writable_paths=(workspace,),
        shell_isolation="sandbox-exec",
    ).wrap_tool_call(
        request,
        lambda prepared: ToolMessage(
            content=bash_tool.func(
                runtime=runtime,
                description="write the stage result",
                command=prepared.tool_call["args"]["command"],
            ),
            tool_call_id="call-1",
        ),
    )

    assert "Error:" not in str(result.content)
    saved = json.loads((host_workspace / "result.json").read_text())
    assert saved == {"source": source}
    assert str(tmp_path) not in (host_workspace / "result.json").read_text()


def test_real_build_shell_can_write_and_execute_a_quoted_heredoc_script(tmp_path, monkeypatch) -> None:
    """Exercise the full middleware → audit → path translation pipeline."""
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/attempt-1/build/unit-1"
    host_outputs = tmp_path / "outputs"
    host_workspace = host_outputs / ".dbtl-stage-work" / "attempt-1" / "build" / "unit-1"
    host_workspace.mkdir(parents=True)
    thread_data = {
        "workspace_path": str(tmp_path / "workspace"),
        "uploads_path": str(tmp_path / "uploads"),
        "outputs_path": str(host_outputs),
    }
    runtime = SimpleNamespace(
        context={},
        state={"sandbox": {"sandbox_id": "local:test:thread"}, "thread_data": thread_data},
    )

    class _ExecutingSandbox:
        def execute_command(self, command, env=None, timeout=None):
            arguments = shlex.split(command)
            bash_index = arguments.index("/bin/bash")
            completed = subprocess.run(
                arguments[bash_index:],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
            )
            return completed.stdout + completed.stderr

    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda _runtime: _ExecutingSandbox())
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda _runtime: None)
    monkeypatch.setattr("deerflow.sandbox.tools.is_host_bash_allowed", lambda: True)

    command = f"""set -euo pipefail
D={workspace}
cat > "$D/run.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
printf 'ok' > "$ROOT/result.txt"
EOF
chmod +x "$D"/run.sh
"$D"/run.sh > "$D"/run.log 2>&1"""
    request = _request("bash", {"command": command}, runtime=runtime)

    result = DbtlOutputPolicyMiddleware(
        writable_paths=(workspace,),
        shell_isolation="sandbox-exec",
    ).wrap_tool_call(
        request,
        lambda prepared: ToolMessage(
            content=bash_tool.func(
                runtime=runtime,
                description="write and run the implementation",
                command=prepared.tool_call["args"]["command"],
            ),
            tool_call_id="call-1",
        ),
    )

    assert "Error:" not in str(result.content)
    assert (host_workspace / "result.txt").read_text() == "ok"
    assert (host_workspace / "run.log").exists()


def test_real_build_python_heredoc_translates_executable_virtual_paths(tmp_path, monkeypatch) -> None:
    """Immediate interpreter input is code, not portable provenance data."""
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/attempt-1/build/unit-1"
    host_outputs = tmp_path / "outputs"
    host_workspace = host_outputs / ".dbtl-stage-work" / "attempt-1" / "build" / "unit-1"
    host_workspace.mkdir(parents=True)
    thread_data = {
        "workspace_path": str(tmp_path / "workspace"),
        "uploads_path": str(tmp_path / "uploads"),
        "outputs_path": str(host_outputs),
    }
    runtime = SimpleNamespace(
        context={},
        state={"sandbox": {"sandbox_id": "local:test:thread"}, "thread_data": thread_data},
    )

    class _ExecutingSandbox:
        def execute_command(self, command, env=None, timeout=None):
            arguments = shlex.split(command)
            bash_index = arguments.index("/bin/bash")
            completed = subprocess.run(
                arguments[bash_index:],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
            )
            return completed.stdout + completed.stderr

    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda _runtime: _ExecutingSandbox())
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda _runtime: None)
    monkeypatch.setattr("deerflow.sandbox.tools.is_host_bash_allowed", lambda: True)

    command = f'''python - <<'PY'
from pathlib import Path
Path("{workspace}/python-result.txt").write_text("ok")
PY'''
    request = _request("bash", {"command": command}, runtime=runtime)

    result = DbtlOutputPolicyMiddleware(
        writable_paths=(workspace,),
        shell_isolation="sandbox-exec",
    ).wrap_tool_call(
        request,
        lambda prepared: ToolMessage(
            content=bash_tool.func(
                runtime=runtime,
                description="run implementation source",
                command=prepared.tool_call["args"]["command"],
            ),
            tool_call_id="call-1",
        ),
    )

    assert "Error:" not in str(result.content)
    assert (host_workspace / "python-result.txt").read_text() == "ok"


@pytest.mark.parametrize(
    "opening",
    [
        "cat > {workspace}/script.py <<'PY'",
        "cat <<'PY' > {workspace}/script.py",
    ],
)
def test_code_file_heredoc_translates_virtual_paths_for_later_execution(opening: str) -> None:
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/attempt-1/build/unit-1"
    command = f'''{opening.format(workspace=workspace)}
from pathlib import Path
Path("{workspace}/result.txt").write_text("ok")
PY'''
    seen: list[str] = []

    result = DbtlOutputPolicyMiddleware(
        writable_paths=(workspace,),
        shell_isolation="sandbox-exec",
    ).wrap_tool_call(
        _request("bash", {"command": command}),
        lambda request: seen.append(request.tool_call["args"]["command"]) or ToolMessage(content="ran", tool_call_id="call-1"),
    )

    assert result.content == "ran"
    assert "__DEERFLOW_VIRTUAL_LITERAL_" not in seen[0]
    assert seen[0].count(workspace) >= 3


def test_unquoted_data_heredoc_does_not_protect_expanding_virtual_paths() -> None:
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/attempt-1/build/unit-1"
    command = f"""cat > {workspace}/result.txt <<EOF
$(cat /mnt/user-data/uploads/input.txt)
EOF"""
    seen: list[str] = []

    result = DbtlOutputPolicyMiddleware(
        writable_paths=(workspace,),
        shell_isolation="sandbox-exec",
    ).wrap_tool_call(
        _request("bash", {"command": command}),
        lambda request: seen.append(request.tool_call["args"]["command"]) or ToolMessage(content="ran", tool_call_id="call-1"),
    )

    assert result.content == "ran"
    assert "__DEERFLOW_VIRTUAL_LITERAL_" not in seen[0]


def test_literal_shell_guard_still_blocks_the_same_governed_path_mention() -> None:
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/attempt-1/build/unit-1"
    command = f"printf '%s' /mnt/user-data/outputs/dbtl/cycle/design/review.md > {workspace}/result.txt"

    result = DbtlOutputPolicyMiddleware(
        writable_paths=(workspace,),
        shell_isolation="literal",
    ).wrap_tool_call(
        _request("bash", {"command": command}),
        lambda _request: ToolMessage(content="ran", tool_call_id="call-1"),
    )

    assert "blocked" in str(result.content)


def test_ordinary_local_shell_cannot_bypass_governed_paths_with_cd() -> None:
    seen: list[str] = []
    result = DbtlOutputPolicyMiddleware(shell_isolation="sandbox-exec").wrap_tool_call(
        _request(
            "bash",
            {"command": "cd /mnt/user-data/outputs && touch dbtl/forged.json"},
        ),
        lambda request: seen.append(request.tool_call["args"]["command"]) or ToolMessage(content="ran", tool_call_id="call-1"),
    )

    assert result.content == "ran"
    assert "sandbox-exec -p" in seen[0]
    assert '(deny file-write* (subpath "/mnt/user-data/outputs/dbtl"))' in seen[0]
    assert '(deny file-write* (subpath "/mnt/user-data/outputs/.dbtl-stage-work"))' in seen[0]


def test_stage_shell_fails_closed_without_a_process_isolation_backend() -> None:
    workspace = "/mnt/user-data/outputs/.dbtl-stage-work/attempt-1/build/unit-1"
    result = DbtlOutputPolicyMiddleware(
        writable_paths=(workspace,),
        shell_isolation="deny",
    ).wrap_tool_call(
        _request("bash", {"command": f"python {workspace}/pipeline.py"}),
        lambda _request: ToolMessage(content="ran", tool_call_id="call-1"),
    )

    assert "cannot enforce" in str(result.content)


class TestTheIsolationWrapperSurvivesTheLocalBashPathGuard:
    """The middleware's own wrapper is server-authored, not model input.

    ``validate_local_bash_command_paths`` is a best-effort guard over paths a
    *model* wrote. The sandbox-exec profile this middleware injects carries host
    scratch directories (``/tmp``, ``tempfile.gettempdir()``) that the guard's
    allowlist deliberately excludes, so auditing the rewritten string rejected
    every command a DBTL stage worker issued — and only when a stage grant made
    the profile carry those paths at all, which is exactly when a Build worker
    needs to execute something.
    """

    WORKSPACE = "/mnt/user-data/outputs/.dbtl-stage-work/attempt-1/build/unit-1"

    def _wrap(self, command: str):
        request = _request("bash", {"command": command})
        seen: list[str] = []

        def handler(prepared):
            seen.append(prepared.tool_call["args"]["command"])
            return ToolMessage(content="ran", tool_call_id="call-1")

        DbtlOutputPolicyMiddleware(
            writable_paths=(self.WORKSPACE,),
            shell_isolation="sandbox-exec",
        ).wrap_tool_call(request, handler)
        return seen[0], request.runtime.context

    def test_the_guard_audits_the_model_command_not_the_wrapper(self) -> None:
        from deerflow.runtime.secret_context import read_pre_isolation_command
        from deerflow.sandbox.tools import validate_local_bash_command_paths

        command = f"cd {self.WORKSPACE} && python3 simulate.py --outdir run1"
        wrapped, context = self._wrap(command)
        thread_data = {"thread_id": "t", "user_id": "default", "base_dir": "/tmp"}

        audited = read_pre_isolation_command(context, authored=wrapped)
        assert audited == command
        validate_local_bash_command_paths(audited, thread_data)

    def test_an_unwrapped_command_is_still_audited_as_written(self) -> None:
        from deerflow.runtime.secret_context import read_pre_isolation_command

        _, context = self._wrap(f"cd {self.WORKSPACE} && python3 simulate.py")

        assert read_pre_isolation_command(context, authored="rm -rf /Users/someone") is None
        assert read_pre_isolation_command({}, authored="anything") is None

    def test_the_record_is_redacted_from_observable_context_copies(self) -> None:
        from deerflow.runtime.secret_context import redact_secret_context_keys

        _, context = self._wrap(f"cd {self.WORKSPACE} && ls")

        assert context
        assert redact_secret_context_keys(context) == {}
