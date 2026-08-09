"""Server-verified Test execution of the typed Build rerun record."""

from __future__ import annotations

import json
import re
import shlex
import threading
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from langchain.tools import tool
from langchain_core.tools import BaseTool

from deerflow.agents.dbtl.live_stage.build_phase_verification import entry_command
from deerflow.agents.dbtl.live_stage.workspace import (
    STAGE_UNIT_WORKSPACE_PLACEHOLDER,
    sha256_file,
    verified_workspace_file,
    verified_workspace_files,
    workspace_relative_path,
)
from deerflow.dbtl.build_execution import BuildRerunSpec, parse_rerun_spec
from deerflow.dbtl.stage_runner import RESULT_CONTRACT, TEST_RERUN_OUTPUT, WorkUnit
from deerflow.dbtl.worker_result import EvidenceRef, QualityCheck, StageWorkerResult, WorkerStatus
from deerflow.sandbox.tools import bash_tool
from deerflow.tools.types import Runtime

MAX_RERUN_LOG_BYTES = 5 * 1024 * 1024
MAX_RERUN_OUTPUT_BYTES = 512 * 1024 * 1024
MAX_RERUN_TOTAL_OUTPUT_BYTES = 2 * 1024 * 1024 * 1024
_WORKSPACE_BINDING = re.compile(r"^workspace_file:(.+):sha256:([0-9a-f]{64})$")
_PUBLISHED_NAME = re.compile(r"^[0-9a-f]{16}-(.+)$")
RERUN_STDOUT_NAME = "rerun.stdout.log"
RERUN_STDERR_NAME = "rerun.stderr.log"
RERUN_EXIT_STATUS_NAME = ".deerflow-rerun-exit-status"


class TestRerunStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class TestRerunRecord:
    status: TestRerunStatus
    command: str
    reason: str
    exit_status: int | None = None
    logs: tuple[dict[str, Any], ...] = ()
    outputs: tuple[dict[str, Any], ...] = ()
    inputs_verified: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "command": self.command,
            "reason": self.reason,
            "exit_status": self.exit_status,
            "logs": [dict(item) for item in self.logs],
            "outputs": [dict(item) for item in self.outputs],
            "inputs_verified": self.inputs_verified,
        }


@dataclass(frozen=True, slots=True)
class PreparedTestRerun:
    spec: BuildRerunSpec
    output_hashes: dict[str, str]
    staged_files: tuple[tuple[str, str], ...] = ()


def _rerun_output_name(expected: str) -> str:
    name = expected.rstrip("/").rsplit("/", 1)[-1]
    match = _PUBLISHED_NAME.fullmatch(name)
    return match.group(1) if match is not None else name


def _safe_staged_path(value: str) -> str | None:
    path = PurePosixPath(value)
    unsafe = not value or value.startswith(("/", "~")) or "\\" in value or ":" in value or any(ord(character) < 32 for character in value) or any(part in {"", ".", ".."} for part in value.split("/")) or path.as_posix() != value
    return None if unsafe else value


def _server_bound_simple_command(spec: BuildRerunSpec, *, staged_entry_path: str | None = None) -> str:
    """Rebind only a simple relative command to its published entry point.

    Publication changes ``fit.py`` into a content-addressed path. Older worker
    records remapped ``entry_point`` but left ``python fit.py`` unchanged. A
    two-token command with the same unprefixed filename has no arguments to
    preserve, so the server-owned interpreter mapping is unambiguous. Anything
    more complex remains byte-for-byte worker-authored and fails closed later.
    """
    try:
        tokens = shlex.split(spec.command)
    except ValueError:
        return spec.command
    if len(tokens) != 2:
        return spec.command
    recorded_name = PurePosixPath(tokens[1]).name
    entry_name = PurePosixPath(spec.entry_point).name
    published_match = _PUBLISHED_NAME.fullmatch(entry_name)
    published_name = published_match.group(1) if published_match is not None else entry_name
    if recorded_name != published_name:
        return spec.command
    # Execute the hash-verified staged copy when publication renamed the file.
    # This preserves ordinary sibling imports and relative support-file reads
    # inside the clean Test workspace.
    return entry_command(staged_entry_path or (published_name if published_match is not None else spec.entry_point))


def parse_test_rerun_record(value: Any) -> TestRerunRecord | None:
    """Read back only the bounded record shape written by this module."""

    if not isinstance(value, dict):
        return None
    try:
        status = TestRerunStatus(value.get("status"))
    except ValueError:
        return None
    command = value.get("command")
    reason = value.get("reason")
    exit_status = value.get("exit_status")
    if not isinstance(command, str) or not isinstance(reason, str):
        return None
    if exit_status is not None and (not isinstance(exit_status, int) or isinstance(exit_status, bool)):
        return None

    def records(field_name: str, *, limit: int) -> tuple[dict[str, Any], ...] | None:
        raw = value.get(field_name)
        if not isinstance(raw, list) or len(raw) > limit or any(not isinstance(item, dict) for item in raw):
            return None
        return tuple(dict(item) for item in raw)

    logs = records("logs", limit=2)
    outputs = records("outputs", limit=64)
    if logs is None or outputs is None:
        return None
    return TestRerunRecord(
        status=status,
        command=command[:2_000],
        reason=reason[:4_000],
        exit_status=exit_status,
        logs=logs,
        outputs=outputs,
        inputs_verified=value.get("inputs_verified") is True,
    )


def _failure(command: str, reason: str, *, status: TestRerunStatus = TestRerunStatus.FAILED) -> TestRerunRecord:
    return TestRerunRecord(status=status, command=command, reason=reason)


def prepare_test_rerun(
    lineage: dict[str, Any],
    *,
    project_root: str,
) -> PreparedTestRerun | TestRerunRecord:
    """Revalidate every durable prerequisite before a Test worker is dispatched."""

    spec = parse_rerun_spec(lineage.get("rerun_spec"))
    if spec is None or str(lineage.get("rerun_status") or "") != "verified":
        return _failure(
            "",
            "Build lineage has no verified structured rerun record.",
            status=TestRerunStatus.MISSING,
        )

    # Compatibility for phased Build records written before the driver kept
    # executable paths separate from hash-bound lineage strings.  The hash is
    # still revalidated against ``input_artifacts`` below; only the path-shaped
    # view belongs in the rerun spec.
    normalized_inputs: list[str] = []
    for declared_input in spec.inputs:
        match = _WORKSPACE_BINDING.fullmatch(declared_input)
        normalized = f"/mnt/user-data/{match.group(1)}" if match is not None else declared_input
        if normalized not in normalized_inputs:
            normalized_inputs.append(normalized)
    spec = replace(spec, inputs=tuple(normalized_inputs))
    spec = replace(
        spec,
        # The contract defines configuration as project files. Some older
        # workers put explanatory sentences here; treating prose as a path made
        # an otherwise executable rerun fail before dispatch.
        configuration=tuple(item for item in spec.configuration if item.startswith("/mnt/user-data/")),
    )

    try:
        entrypoint = verified_workspace_files(
            spec.entry_point,
            project_root=project_root,
            max_files=1,
        )
    except (FileNotFoundError, OSError, ValueError):
        return _failure(spec.command, "The recorded Build entry point is missing, unreadable, or outside the project workspace.")
    resolved_entrypoint = workspace_relative_path(spec.entry_point, project_root=project_root)
    if len(entrypoint) != 1 or resolved_entrypoint is None or not resolved_entrypoint[1].is_file():
        return _failure(spec.command, "The recorded Build entry point is not one regular project file.")

    bound_hashes: dict[str, str] = {}
    for binding in lineage.get("input_artifacts") or ():
        if not isinstance(binding, str):
            continue
        match = _WORKSPACE_BINDING.fullmatch(binding)
        if match is not None:
            relative, content_hash = match.groups()
            bound_hashes[relative] = content_hash
        else:
            candidate_hash = binding.rsplit(":", 1)[-1].lower()
            if re.fullmatch(r"[0-9a-f]{64}", candidate_hash):
                bound_hashes[f"hash:{candidate_hash}"] = candidate_hash

    output_artifacts = [item for item in lineage.get("output_artifacts") or () if isinstance(item, dict)]
    for artifact in output_artifacts:
        uri = str(artifact.get("uri") or "")
        content_hash = str(artifact.get("content_hash") or "").lower()
        if uri.startswith("/mnt/user-data/") and re.fullmatch(r"[0-9a-f]{64}", content_hash):
            relative = uri.removeprefix("/mnt/user-data/")
            if _safe_staged_path(relative) is not None:
                # Published Build support files are server-hashed lineage too.
                # Test may consume them, but expected outputs remain excluded
                # from the fresh staging set below.
                bound_hashes.setdefault(relative, content_hash)

    try:
        for declared_input in spec.inputs:
            files = verified_workspace_files(
                declared_input,
                project_root=project_root,
                max_files=5_000,
            )
            if not files:
                return _failure(spec.command, f"The declared rerun input {declared_input!r} contains no regular files.")
            for relative, path in files:
                current_hash = sha256_file(path)
                if bound_hashes.get(relative) != current_hash and bound_hashes.get(f"hash:{current_hash}") != current_hash:
                    return _failure(spec.command, f"The declared rerun input {relative!r} is not bound to the recorded Build lineage or has changed.")
        for configuration in spec.configuration:
            files = verified_workspace_files(
                configuration,
                project_root=project_root,
                max_files=5_000,
            )
            if not files:
                return _failure(spec.command, f"The recorded configuration {configuration!r} contains no regular files.")
    except (FileNotFoundError, OSError, ValueError):
        return _failure(spec.command, "A declared rerun input or configuration is missing, unreadable, or outside the project workspace.")

    output_hashes = {str(item.get("uri") or ""): str(item.get("content_hash") or "").lower() for item in output_artifacts if re.fullmatch(r"[0-9a-f]{64}", str(item.get("content_hash") or "").lower())}
    missing_outputs = [expected for expected in spec.expected_outputs if expected not in output_hashes]
    if missing_outputs:
        return _failure(spec.command, f"The rerun record names expected outputs that are not bound by Build lineage: {', '.join(missing_outputs[:4])}.")
    unique_outputs: dict[str, str] = {}
    for expected in spec.expected_outputs:
        name = _rerun_output_name(expected)
        previous = unique_outputs.get(name)
        if previous is not None and output_hashes[previous] != output_hashes[expected]:
            return _failure(spec.command, "The rerun record has expected outputs with duplicate filenames and different approved hashes.")
        unique_outputs.setdefault(name, expected)
    spec = replace(spec, expected_outputs=tuple(unique_outputs.values()))
    output_names = list(unique_outputs)
    reserved_names = {RERUN_STDOUT_NAME, RERUN_STDERR_NAME, RERUN_EXIT_STATUS_NAME}
    if any(name in reserved_names for name in output_names):
        return _failure(spec.command, "The rerun record uses a filename reserved for the server-owned execution receipt.")

    staged_files: list[tuple[str, str]] = []
    staged_hashes: dict[str, str] = {}
    staged_bytes = 0
    staged_entry_path: str | None = None
    for artifact in output_artifacts:
        source = str(artifact.get("uri") or "")
        content_hash = str(artifact.get("content_hash") or "").lower()
        if source in spec.expected_outputs or not re.fullmatch(r"[0-9a-f]{64}", content_hash):
            continue
        try:
            verified = verified_workspace_file(
                source,
                project_root=project_root,
                containment_reference=None,
                max_bytes=MAX_RERUN_OUTPUT_BYTES,
            )
        except (FileNotFoundError, OSError, ValueError):
            verified = None
        if verified is None or verified[2] != content_hash:
            return _failure(spec.command, f"The published Build support file {source!r} is missing, unreadable, or no longer matches its recorded hash.")
        recorded_source_path = artifact.get("source_path")
        if isinstance(recorded_source_path, str) and recorded_source_path:
            name = _safe_staged_path(recorded_source_path)
            if name is None:
                return _failure(spec.command, f"The published Build support file {source!r} has an unsafe recorded source path.")
        else:
            name = _rerun_output_name(source)
        if name in reserved_names or name in output_names:
            return _failure(spec.command, f"The published Build support filename {name!r} conflicts with a rerun output or server receipt.")
        previous = staged_hashes.get(name)
        if previous is not None:
            if previous != content_hash:
                return _failure(spec.command, f"Published Build support files named {name!r} have different recorded hashes.")
            continue
        staged_hashes[name] = content_hash
        staged_files.append((source, name))
        if source == spec.entry_point:
            staged_entry_path = name
        staged_bytes += verified[1]
        if len(staged_files) > 64 or staged_bytes > MAX_RERUN_TOTAL_OUTPUT_BYTES:
            return _failure(spec.command, "The published Build support files exceed the Test staging limits.")
    published_entry = _PUBLISHED_NAME.fullmatch(PurePosixPath(spec.entry_point).name)
    if published_entry is not None and staged_entry_path is None:
        return _failure(spec.command, "The published Build entry point is not available in the hash-bound Test staging set.")
    spec = replace(spec, command=_server_bound_simple_command(spec, staged_entry_path=staged_entry_path))
    return PreparedTestRerun(spec=spec, output_hashes=output_hashes, staged_files=tuple(staged_files))


def build_test_rerun_unit(
    prepared: PreparedTestRerun,
    *,
    attempt_id: str,
    agent_name: str,
    via_generalist: bool,
) -> WorkUnit:
    """Build the one bounded Test unit that uses the existing sandbox tools."""

    contract = {
        "command": prepared.spec.command,
        "entry_point": prepared.spec.entry_point,
        "seed": prepared.spec.seed,
        "inputs": list(prepared.spec.inputs),
        "environment": dict(prepared.spec.environment),
        "configuration": list(prepared.spec.configuration),
        "expected_outputs": [{"expected": path, "build_hash": prepared.output_hashes[path]} for path in prepared.spec.expected_outputs],
        "fresh_workspace": STAGE_UNIT_WORKSPACE_PLACEHOLDER,
    }
    prompt = "\n".join(
        [
            "You are the bounded reproducibility worker for Test.",
            "Use the server-bound rerun tool to execute the exact recorded command once.",
            "Run it with the fresh workspace below as the working directory. Read declared project inputs, but write only inside that workspace; never overwrite Build evidence.",
            "Call execute_build_rerun exactly once. It is the only execution tool and owns the command, working directory, logs, and exit-status receipt.",
            "Do not repair the Build or try to recreate evidence after the tool returns.",
            "",
            "Server-bound rerun contract:",
            json.dumps(contract, sort_keys=True, ensure_ascii=False),
            "",
            "Return the shared stage-worker JSON contract after the tool finishes. The server reads the fixed receipt files and determines reproducibility; your prose and provenance do not.",
            "Use status completed when the tool call finished, even when the recorded command exited non-zero.",
            RESULT_CONTRACT,
        ]
    )
    return WorkUnit(
        unit_id=f"{attempt_id}-build-rerun",
        capability="reproducibility_rerun",
        agent_name=agent_name,
        prompt=prompt,
        via_generalist=via_generalist,
        role="rerun",
        focus="executes the exact Build rerun record in a fresh Test workspace",
        output_contract=TEST_RERUN_OUTPUT,
        tool_contract={
            "kind": "build_rerun",
            "command": prepared.spec.command,
            "granted_inputs": list(prepared.spec.inputs),
            "staged_files": [{"source": source, "name": name} for source, name in prepared.staged_files],
        },
    )


def build_test_rerun_tool(unit: WorkUnit, *, unit_workspace: str) -> BaseTool:
    """Wrap native sandbox Bash so the model can execute only the bound command."""

    if unit.tool_contract.get("kind") != "build_rerun":
        raise ValueError("The Test rerun unit has no server-owned command contract.")
    command = unit.tool_contract.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("The Test rerun unit has no executable command.")
    raw_staged_files = unit.tool_contract.get("staged_files", [])
    if not isinstance(raw_staged_files, list):
        raise ValueError("The Test rerun unit has a malformed staging contract.")
    staged_files: list[tuple[str, str]] = []
    for item in raw_staged_files:
        if not isinstance(item, dict):
            raise ValueError("The Test rerun unit has a malformed staging contract.")
        source = item.get("source")
        name = item.get("name")
        if not isinstance(source, str) or not source.startswith("/mnt/user-data/") or not isinstance(name, str) or _safe_staged_path(name) is None:
            raise ValueError("The Test rerun unit has a malformed staging contract.")
        staged_files.append((source, name))
    workspace = unit_workspace.rstrip("/")
    stdout_path = f"{workspace}/{RERUN_STDOUT_NAME}"
    stderr_path = f"{workspace}/{RERUN_STDERR_NAME}"
    status_path = f"{workspace}/{RERUN_EXIT_STATUS_NAME}"
    stage_commands = []
    for source, name in staged_files:
        destination = f"{workspace}/{name}"
        parent = str(PurePosixPath(destination).parent)
        stage_commands.append(f"mkdir -p {shlex.quote(parent)} && cp {shlex.quote(source)} {shlex.quote(destination)} || exit 96")
    shell_command = "\n".join(
        [
            "set +e",
            f"mkdir -p {shlex.quote(workspace)}",
            *stage_commands,
            f"cd {shlex.quote(workspace)} || exit 97",
            f"export DBTL_WORKSPACE={shlex.quote(workspace)}",
            "export DBTL_PROJECT_ROOT=/mnt/user-data",
            # Bound any single file the command creates to 512 MiB. Logs have a
            # tighter acceptance cap below; this is the execution-time disk
            # guard that prevents a failed check from first filling the mount.
            "ulimit -f 1048576 || exit 98",
            f"(\n{command}\n) > {shlex.quote(stdout_path)} 2> {shlex.quote(stderr_path)}",
            "deerflow_rerun_status=$?",
            f"printf '%s\\n' \"$deerflow_rerun_status\" > {shlex.quote(status_path)}",
            "exit 0",
        ]
    )
    call_lock = threading.Lock()
    called = False

    @tool("execute_build_rerun", parse_docstring=True)
    async def execute_build_rerun(runtime: Runtime) -> str:
        """Execute the server-bound Build command once and retain its fixed receipt files."""

        nonlocal called
        with call_lock:
            if called:
                return "Error: the server-bound Build rerun has already been executed once."
            called = True
        coroutine = bash_tool.coroutine
        if coroutine is None:  # pragma: no cover - configured by sandbox.tools at import
            return "Error: the native sandbox Bash tool is unavailable."
        result = await coroutine(
            runtime,
            "Execute the server-bound Build rerun and retain its receipt.",
            shell_command,
        )
        return f"The server-bound rerun tool finished. Native sandbox response: {result}"

    return execute_build_rerun


def validate_test_rerun(
    result: StageWorkerResult,
    prepared: PreparedTestRerun,
    *,
    project_root: str,
    unit_workspace: str,
) -> TestRerunRecord:
    """Replace worker assertion with hashes and bounded files the server read."""

    logs: list[dict[str, Any]] = []
    for stream, filename in (("stdout", RERUN_STDOUT_NAME), ("stderr", RERUN_STDERR_NAME)):
        verified = verified_workspace_file(
            f"{unit_workspace.rstrip('/')}/{filename}",
            project_root=project_root,
            containment_reference=unit_workspace,
            max_bytes=MAX_RERUN_LOG_BYTES,
        )
        if verified is None:
            reason = f"The rerun {stream} log is missing, outside the fresh workspace, or too large."
            if result.stop_reason:
                reason = f"The rerun worker stopped with {result.stop_reason}; {reason}"
            return _failure(prepared.spec.command, reason)
        relative, size, content_hash = verified
        logs.append(
            {
                "stream": stream,
                "path": f"/mnt/user-data/{relative}",
                "content_hash": content_hash,
                "bytes": size,
            }
        )

    status_file = verified_workspace_file(
        f"{unit_workspace.rstrip('/')}/{RERUN_EXIT_STATUS_NAME}",
        project_root=project_root,
        containment_reference=unit_workspace,
        max_bytes=32,
    )
    if status_file is None:
        reason = "The server-bound rerun tool did not produce its exit-status receipt."
        if result.stop_reason:
            reason = f"The rerun worker stopped with {result.stop_reason}; {reason}"
        return _failure(prepared.spec.command, reason)
    status_relative, _status_size, _status_hash = status_file
    resolved_status = workspace_relative_path(status_relative, project_root=project_root)
    if resolved_status is None:
        return _failure(prepared.spec.command, "The rerun exit-status receipt is no longer readable.")
    try:
        status_text = resolved_status[1].read_text(encoding="utf-8").strip()
        exit_status = int(status_text)
    except (OSError, UnicodeError, ValueError):
        return _failure(prepared.spec.command, "The rerun exit-status receipt is not one integer.")
    if not 0 <= exit_status <= 255:
        return _failure(prepared.spec.command, "The rerun exit-status receipt is outside the shell status range.")

    if exit_status != 0:
        return TestRerunRecord(
            status=TestRerunStatus.FAILED,
            command=prepared.spec.command,
            reason=f"The recorded command exited with status {exit_status}.",
            exit_status=exit_status,
            logs=tuple(logs),
            inputs_verified=True,
        )

    def failed_after_execution(reason: str, *, outputs: tuple[dict[str, Any], ...] = ()) -> TestRerunRecord:
        return TestRerunRecord(
            status=TestRerunStatus.FAILED,
            command=prepared.spec.command,
            reason=reason,
            exit_status=exit_status,
            logs=tuple(logs),
            outputs=outputs,
            inputs_verified=True,
        )

    try:
        fresh_files = verified_workspace_files(
            unit_workspace,
            project_root=project_root,
            containment_reference=unit_workspace,
            max_files=5_000,
        )
    except (FileNotFoundError, OSError, ValueError):
        return failed_after_execution("The fresh rerun workspace is missing, unreadable, or exceeds its file limit.")
    receipt_names = {RERUN_STDOUT_NAME, RERUN_STDERR_NAME, RERUN_EXIT_STATUS_NAME}
    output_candidates = [item for item in fresh_files if item[1].name not in receipt_names]
    try:
        total_output_bytes = sum(path.stat().st_size for _relative, path in output_candidates)
    except OSError:
        return failed_after_execution("A fresh rerun output became unreadable before verification.")
    if total_output_bytes > MAX_RERUN_TOTAL_OUTPUT_BYTES:
        return failed_after_execution("The fresh rerun outputs exceed the 2 GiB verification limit.")

    outputs: list[dict[str, Any]] = []
    for expected in prepared.spec.expected_outputs:
        basename = _rerun_output_name(expected)
        matches = [relative for relative, path in output_candidates if path.name == basename]
        if not matches:
            return failed_after_execution(f"The successful rerun produced 0 fresh files named {basename!r}; at least one is required for {expected!r}.")
        build_hash = prepared.output_hashes[expected]
        verified_matches = [
            verified_workspace_file(
                f"/mnt/user-data/{match}",
                project_root=project_root,
                containment_reference=unit_workspace,
                max_bytes=MAX_RERUN_OUTPUT_BYTES,
            )
            for match in matches
        ]
        if any(item is None for item in verified_matches):
            return failed_after_execution(f"A rerun output for {expected!r} is outside the fresh workspace or too large.")
        relative, size, content_hash = next(item for item in verified_matches if item is not None)
        output_record = {
            "expected": expected,
            "path": f"/mnt/user-data/{relative}",
            "content_hash": content_hash,
            "build_hash": build_hash,
            "bytes": size,
        }
        if any(item[2] != build_hash for item in verified_matches if item is not None):
            return failed_after_execution(
                f"Fresh files named {basename!r} disagree with the approved Build hash for {expected!r}.",
                outputs=(*tuple(outputs), output_record),
            )
        outputs.append(output_record)
    return TestRerunRecord(
        status=TestRerunStatus.PASSED,
        command=prepared.spec.command,
        reason="The exact recorded command exited zero and every expected output matched its approved Build hash.",
        exit_status=exit_status,
        logs=tuple(logs),
        outputs=tuple(outputs),
        inputs_verified=True,
    )


def rerun_result(
    record: TestRerunRecord,
    *,
    agent_name: str,
    token_usage: dict[str, int] | None = None,
) -> StageWorkerResult:
    """Create the durable worker row from server-verified facts only."""

    artifact_refs = tuple(str(item["path"]) for item in (*record.logs, *record.outputs))
    evidence_refs = tuple(
        EvidenceRef(
            kind="workspace_file",
            reference=reference,
            description="Server-hashed Test rerun evidence.",
        )
        for reference in artifact_refs
    )
    return StageWorkerResult(
        status=(WorkerStatus.COMPLETED if record.status is TestRerunStatus.PASSED else WorkerStatus.FAILED),
        summary=record.reason,
        capability="reproducibility_rerun",
        agent_name=agent_name,
        artifact_refs=artifact_refs,
        evidence_refs=evidence_refs,
        provenance={"rerun_execution": record.as_dict()},
        quality_checks=(
            QualityCheck(
                name="server_verified_build_rerun",
                passed=record.status is TestRerunStatus.PASSED,
                detail=record.reason,
            ),
        ),
        recommended_next_actions=("Use the server-owned reproducibility check in the Test validity pack.",),
        token_usage=dict(token_usage or {}),
    )
