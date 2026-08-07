"""Server-owned execution receipt for one Build phase entry point."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any

from deerflow.agents.dbtl.live_stage.build_phases import BuildPhaseManifest, is_server_executable_entry_point
from deerflow.agents.dbtl.live_stage.workspace import verified_workspace_file, workspace_relative_path
from deerflow.dbtl.build_grant import build_input_grant

VERIFY_STDOUT = "logs/server-verification.stdout.log"
VERIFY_STDERR = "logs/server-verification.stderr.log"
VERIFY_STATUS = ".server-verification-exit-status"
MAX_VERIFY_LOG_BYTES = 5 * 1024 * 1024


def resolve_issued_input_tokens(
    manifest: BuildPhaseManifest,
    *,
    issued_inputs: tuple[str, ...],
) -> BuildPhaseManifest:
    """Resolve exact ``DBTL_INPUT_n`` declarations against the server grant.

    Workers read those environment variables at runtime and sometimes report
    the variable name instead of its value. The server already owns the
    one-based mapping, so accepting a known token is equivalent to accepting
    its issued path; unknown tokens remain untouched and fail closed below.
    """

    def resolve(value: str) -> str:
        if not value.startswith("DBTL_INPUT_"):
            return value
        position = value.removeprefix("DBTL_INPUT_")
        if not position.isdigit():
            return value
        index = int(position) - 1
        return issued_inputs[index] if 0 <= index < len(issued_inputs) else value

    return replace(
        manifest,
        declared_inputs=tuple(resolve(value) for value in manifest.declared_inputs),
        execution_inputs=tuple(resolve(value) for value in manifest.execution_inputs),
    )


def _quoted_path(value: str) -> str:
    """Quote a virtual path before a local mount can add shell metacharacters."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


@dataclass(frozen=True, slots=True)
class BuildPhaseVerification:
    passed: bool
    reason: str
    command: str
    exit_status: int | None = None
    logs: tuple[dict[str, Any], ...] = ()
    outputs: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "command": self.command,
            "exit_status": self.exit_status,
            "logs": [dict(item) for item in self.logs],
            "outputs": [dict(item) for item in self.outputs],
        }


def entry_command(entry_point: str) -> str:
    suffix = PurePosixPath(entry_point).suffix.lower()
    quoted = _quoted_path(entry_point)
    interpreters = {
        ".py": "python",
        ".sh": "/bin/bash",
        ".bash": "/bin/bash",
        ".r": "Rscript",
        ".js": "node",
        ".mjs": "node",
        ".cjs": "node",
        ".jl": "julia",
        ".rb": "ruby",
        ".pl": "perl",
    }
    if interpreter := interpreters.get(suffix):
        return f"{interpreter} {quoted}"
    if suffix in {".ts", ".tsx"}:
        # `--no-install` is the preflight: a configured local `tsx` may run,
        # while a missing tool fails instead of downloading code at Build time.
        return f"npx --no-install tsx {quoted}"
    return quoted


def verification_shell_command(
    manifest: BuildPhaseManifest,
    *,
    unit_workspace: str,
    issued_inputs: tuple[str, ...] | None = None,
) -> tuple[str, dict[str, str]]:
    """Return the bounded command and server-issued environment for a phase."""
    workspace = unit_workspace.rstrip("/")
    command = entry_command(manifest.entry_point)
    stdout = f"{workspace}/{VERIFY_STDOUT}"
    stderr = f"{workspace}/{VERIFY_STDERR}"
    status = f"{workspace}/{VERIFY_STATUS}"
    shell = "\n".join(
        [
            "set +e",
            f"mkdir -p {_quoted_path(workspace + '/logs')}",
            f"cd {_quoted_path(workspace)} || exit 97",
            "ulimit -f 1048576 || exit 98",
            f"({command}) > {_quoted_path(stdout)} 2> {_quoted_path(stderr)}",
            "dbtl_verify_status=$?",
            f"printf '%s\\n' \"$dbtl_verify_status\" > {_quoted_path(status)}",
            "exit 0",
        ]
    )
    env = build_input_grant(
        workspace=workspace,
        project_root="/mnt/user-data",
        declared_inputs=manifest.execution_inputs if issued_inputs is None else issued_inputs,
    )
    return shell, env


def _clear_verification_receipts(*, project_root: str, unit_workspace: str) -> str:
    """Remove worker-visible receipt names before the server starts.

    The workspace belongs to the worker until this point, so a status/log file
    already present there is evidence of nothing. Clearing all three names is
    the freshness boundary: a timeout or a command that never started then
    leaves no status receipt and must fail closed.
    """
    for filename in (VERIFY_STATUS, VERIFY_STDOUT, VERIFY_STDERR):
        reference = f"{unit_workspace.rstrip('/')}/{filename}"
        resolved = workspace_relative_path(reference, project_root=project_root)
        if resolved is None:
            return f"The server verification receipt path {reference!r} is outside the phase grant."
        path = resolved[1]
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                return f"The server verification receipt path {reference!r} was pre-created as a directory."
        except OSError as exc:
            return f"The server could not clear the untrusted verification receipt {reference!r}: {exc}"
    return ""


def execute_and_verify_phase(
    manifest: BuildPhaseManifest,
    *,
    project_root: str,
    unit_workspace: str,
    execute: Callable[[str, dict[str, str], float], str],
    timeout_seconds: float,
    issued_inputs: tuple[str, ...] = (),
) -> BuildPhaseVerification:
    """Execute the declared entry point and derive the receipt from files."""
    entry = verified_workspace_file(
        manifest.entry_point,
        project_root=project_root,
        containment_reference=unit_workspace,
        relative_to_containment=True,
    )
    if entry is None:
        return BuildPhaseVerification(False, "The declared Build entry point is missing or outside its phase grant.", "")
    if not is_server_executable_entry_point(manifest.entry_point):
        return BuildPhaseVerification(
            False,
            "The declared file is not an executable Build entry point supported by the server; use a script and keep notebooks as outputs.",
            "",
        )
    issued = set(issued_inputs)
    for declared in manifest.execution_inputs:
        if declared not in issued:
            return BuildPhaseVerification(False, f"The declared Build input {declared!r} was not in the server-issued phase grant.", "")
        resolved = workspace_relative_path(declared, project_root=project_root)
        if resolved is None or not resolved[1].is_file():
            return BuildPhaseVerification(False, f"The declared Build input {declared!r} is missing or outside the project grant.", "")

    receipt_error = _clear_verification_receipts(project_root=project_root, unit_workspace=unit_workspace)
    if receipt_error:
        return BuildPhaseVerification(False, receipt_error, "")

    try:
        shell, env = verification_shell_command(manifest, unit_workspace=unit_workspace, issued_inputs=issued_inputs)
        execute(shell, env, timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - converted into a bounded stage receipt
        return BuildPhaseVerification(False, f"The server could not execute the Build entry point: {exc}", entry_command(manifest.entry_point))

    status_ref = f"{unit_workspace.rstrip('/')}/{VERIFY_STATUS}"
    status_file = verified_workspace_file(status_ref, project_root=project_root, containment_reference=unit_workspace, max_bytes=32)
    if status_file is None:
        return BuildPhaseVerification(False, "The server execution produced no readable exit-status receipt.", entry_command(manifest.entry_point))
    resolved_status = workspace_relative_path(status_ref, project_root=project_root)
    try:
        exit_status = int(resolved_status[1].read_text(encoding="utf-8").strip()) if resolved_status is not None else -1
    except (OSError, UnicodeError, ValueError):
        return BuildPhaseVerification(False, "The server execution exit-status receipt is malformed.", entry_command(manifest.entry_point))

    logs: list[dict[str, Any]] = []
    for stream, filename in (("stdout", VERIFY_STDOUT), ("stderr", VERIFY_STDERR)):
        verified = verified_workspace_file(
            f"{unit_workspace.rstrip('/')}/{filename}",
            project_root=project_root,
            containment_reference=unit_workspace,
            max_bytes=MAX_VERIFY_LOG_BYTES,
        )
        if verified is None:
            return BuildPhaseVerification(False, f"The server execution {stream} log is missing or too large.", entry_command(manifest.entry_point), exit_status=exit_status)
        relative, size, content_hash = verified
        logs.append({"stream": stream, "path": f"/mnt/user-data/{relative}", "bytes": size, "content_hash": content_hash})

    if exit_status != 0:
        return BuildPhaseVerification(
            False,
            f"The server executed the declared Build entry point and it exited with status {exit_status}.",
            entry_command(manifest.entry_point),
            exit_status=exit_status,
            logs=tuple(logs),
        )

    outputs: list[dict[str, Any]] = []
    for declared in manifest.declared_outputs:
        verified = verified_workspace_file(
            declared,
            project_root=project_root,
            containment_reference=unit_workspace,
            relative_to_containment=True,
        )
        if verified is None:
            return BuildPhaseVerification(
                False,
                f"The server execution could not verify declared output {declared!r} inside the phase grant.",
                entry_command(manifest.entry_point),
                exit_status=exit_status,
                logs=tuple(logs),
            )
        relative, size, content_hash = verified
        outputs.append({"path": f"/mnt/user-data/{relative}", "bytes": size, "content_hash": content_hash})

    return BuildPhaseVerification(
        True,
        "The server executed the declared Build entry point successfully.",
        entry_command(manifest.entry_point),
        exit_status=exit_status,
        logs=tuple(logs),
        outputs=tuple(outputs),
    )
