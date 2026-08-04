"""Server-owned execution receipt for one Build phase entry point."""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from deerflow.agents.dbtl.live_stage.build_phases import BuildPhaseManifest
from deerflow.agents.dbtl.live_stage.workspace import sha256_file, verified_workspace_files, workspace_relative_path
from deerflow.dbtl.build_grant import build_input_grant

VERIFY_STDOUT = "logs/server-verification.stdout.log"
VERIFY_STDERR = "logs/server-verification.stderr.log"
VERIFY_STATUS = ".server-verification-exit-status"
MAX_VERIFY_LOG_BYTES = 5 * 1024 * 1024


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
    quoted = shlex.quote(entry_point)
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
            f"mkdir -p {shlex.quote(workspace + '/logs')}",
            f"cd {shlex.quote(workspace)} || exit 97",
            "ulimit -f 1048576 || exit 98",
            f"({command}) > {shlex.quote(stdout)} 2> {shlex.quote(stderr)}",
            "dbtl_verify_status=$?",
            f"printf '%s\\n' \"$dbtl_verify_status\" > {shlex.quote(status)}",
            "exit 0",
        ]
    )
    env = build_input_grant(
        workspace=workspace,
        project_root="/mnt/user-data",
        declared_inputs=manifest.execution_inputs if issued_inputs is None else issued_inputs,
    )
    return shell, env


def _verified_file(reference: str, *, project_root: str, unit_workspace: str, max_bytes: int | None = None) -> tuple[str, int, str] | None:
    try:
        files = verified_workspace_files(
            reference,
            project_root=project_root,
            containment_reference=unit_workspace,
            max_files=1,
        )
        if len(files) != 1 or not files[0][1].is_file():
            return None
        relative, path = files[0]
        before = path.stat()
        if max_bytes is not None and before.st_size > max_bytes:
            return None
        content_hash = sha256_file(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return None
        return relative, after.st_size, content_hash
    except (FileNotFoundError, OSError, ValueError):
        return None


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
    entry = _verified_file(manifest.entry_point, project_root=project_root, unit_workspace=unit_workspace)
    if entry is None:
        return BuildPhaseVerification(False, "The declared Build entry point is missing or outside its phase grant.", "")
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
    status_file = _verified_file(status_ref, project_root=project_root, unit_workspace=unit_workspace, max_bytes=32)
    if status_file is None:
        return BuildPhaseVerification(False, "The server execution produced no readable exit-status receipt.", entry_command(manifest.entry_point))
    resolved_status = workspace_relative_path(status_ref, project_root=project_root)
    try:
        exit_status = int(resolved_status[1].read_text(encoding="utf-8").strip()) if resolved_status is not None else -1
    except (OSError, UnicodeError, ValueError):
        return BuildPhaseVerification(False, "The server execution exit-status receipt is malformed.", entry_command(manifest.entry_point))

    logs: list[dict[str, Any]] = []
    for stream, filename in (("stdout", VERIFY_STDOUT), ("stderr", VERIFY_STDERR)):
        verified = _verified_file(
            f"{unit_workspace.rstrip('/')}/{filename}",
            project_root=project_root,
            unit_workspace=unit_workspace,
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
        verified = _verified_file(declared, project_root=project_root, unit_workspace=unit_workspace)
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
