"""Server-owned execution receipt for one Build phase entry point."""

from __future__ import annotations

import importlib.util
import os
import sys
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

REQUIRED_SCIENTIFIC_MODULES: tuple[tuple[str, str], ...] = (
    ("numpy", "NumPy"),
    ("scipy", "SciPy"),
    ("pandas", "pandas"),
    ("matplotlib", "Matplotlib"),
    ("statsmodels", "statsmodels"),
    ("sklearn", "scikit-learn"),
    ("seaborn", "seaborn"),
    ("jupyter", "Jupyter"),
    ("nbconvert", "nbconvert"),
    ("ipykernel", "ipykernel"),
)


def missing_scientific_packages() -> tuple[str, ...]:
    """Scientific packages absent from the gateway interpreter."""
    return tuple(label for module, label in REQUIRED_SCIENTIFIC_MODULES if importlib.util.find_spec(module) is None)


def local_dbtl_runtime_env() -> dict[str, str]:
    """Select the gateway venv for DBTL commands executed on the local host.

    The command stays portable (``python script.py``). PATH is the environment
    contract that makes that name resolve to the same provisioned interpreter
    which imported the gateway and owns the ``dbtl-build`` scientific stack.
    """
    # Preserve the venv path even when its ``python`` is a symlink to a base
    # interpreter. Resolving the symlink would discard the environment whose
    # site-packages are the reason for this overlay.
    bin_dir = os.path.dirname(os.path.abspath(sys.executable))
    current = os.environ.get("PATH", "")
    path_parts = [bin_dir, *(part for part in current.split(os.pathsep) if part and part != bin_dir)]
    return {
        "PATH": os.pathsep.join(path_parts),
        "VIRTUAL_ENV": os.path.dirname(bin_dir),
    }


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
    """The portable command recorded on the phase receipt and rerun contract.

    Deliberately uses generic interpreter names: this string travels into the
    Test rerun spec and remains reproducible outside this host. Local execution
    selects the provisioned interpreter through :func:`local_dbtl_runtime_env`.
    """
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
) -> tuple[str, dict[str, str]]:
    """Return the bounded command and the phase's declared runtime environment.

    ``issued_inputs`` is a discovery catalog, not ambient execution authority.
    Test numbers ``rerun_spec.inputs`` compactly from one, so Build must prove
    the entry point under that same contract. Injecting the whole issued catalog
    here let code depend on an undeclared ``DBTL_INPUT_n`` and pass Build only to
    fail the authoritative Test rerun.
    """
    workspace = unit_workspace.rstrip("/")
    run_script = entry_command(manifest.entry_point)
    stdout = f"{workspace}/{VERIFY_STDOUT}"
    stderr = f"{workspace}/{VERIFY_STDERR}"
    status = f"{workspace}/{VERIFY_STATUS}"
    shell = "\n".join(
        [
            "set +e",
            f"mkdir -p {_quoted_path(workspace + '/logs')}",
            f"cd {_quoted_path(workspace)} || exit 97",
            "ulimit -f 1048576 || exit 98",
            f"{run_script} > {_quoted_path(stdout)} 2> {_quoted_path(stderr)}",
            "dbtl_verify_status=$?",
            f"printf '%s\\n' \"$dbtl_verify_status\" > {_quoted_path(status)}",
            "exit 0",
        ]
    )
    env = build_input_grant(
        workspace=workspace,
        project_root="/mnt/user-data",
        declared_inputs=manifest.execution_inputs,
    )
    return shell, env


#: How much of a failing phase's stderr rides along in the receipt text.
MAX_STDERR_EXCERPT_CHARS = 400


def _stderr_excerpt(*, project_root: str, unit_workspace: str) -> str:
    """The last meaningful stderr lines of a failed phase, bounded for a receipt.

    The tail rather than the head: a traceback ends with the error that actually
    stopped the run. Best-effort by design — an unreadable log must never turn a
    real execution failure into a different one.
    """
    resolved = workspace_relative_path(f"{unit_workspace.rstrip('/')}/{VERIFY_STDERR}", project_root=project_root)
    if resolved is None:
        return ""
    try:
        lines = [line.strip() for line in resolved[1].read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return ""
    if not lines:
        return ""
    excerpt = " | ".join(lines[-3:])
    return excerpt[:MAX_STDERR_EXCERPT_CHARS] + ("…" if len(excerpt) > MAX_STDERR_EXCERPT_CHARS else "")


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
        shell, env = verification_shell_command(manifest, unit_workspace=unit_workspace)
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
        # Carry the failure's own words, not just its number. A bare "exited
        # with status 127" sent every reader hunting for a log file that already
        # said "python: command not found"; the cause belongs in the receipt the
        # human and Test actually read.
        cause = _stderr_excerpt(project_root=project_root, unit_workspace=unit_workspace)
        reason = f"The server executed the declared Build entry point and it exited with status {exit_status}."
        if cause:
            reason = f"{reason} It reported: {cause}"
        return BuildPhaseVerification(
            False,
            reason,
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
