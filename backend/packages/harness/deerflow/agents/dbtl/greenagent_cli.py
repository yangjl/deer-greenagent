"""Thin wrapper around the greenagent CLI used as the DBTL gate.

greenagent is a zero-dependency Node.js CLI: JSON in / exit code out. The
orchestrator calls it to *validate* cycle artifacts and *gate* state
transitions. greenagent stays the deterministic authority; the orchestrator
never decides legality itself.

The gate is a Protocol so tests can inject a scripted fake without a subprocess.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GateResult:
    """Outcome of a greenagent validate / check-transition call."""

    ok: bool
    errors: tuple[str, ...] = ()
    raw: dict = field(default_factory=dict)


@runtime_checkable
class GreenAgentGate(Protocol):
    def validate(self, project_path: str, cycle_id: str) -> GateResult: ...

    def check_transition(
        self,
        project_path: str,
        cycle_id: str,
        to_state: str,
        *,
        actor: str,
        authorization: str,
    ) -> GateResult: ...


class GreenAgentUnavailableError(RuntimeError):
    """Raised when the greenagent CLI cannot be located at call time."""


def _resolve_greenagent_binary() -> str:
    found = shutil.which("greenagent")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "greenagent"
    if fallback.exists():
        return str(fallback)
    raise GreenAgentUnavailableError("greenagent CLI not found on PATH or at ~/.local/bin/greenagent. Install the greenagent harness to enable DBTL gating.")


class SubprocessGreenAgentGate:
    """Production gate: shells out to the greenagent CLI.

    Binary resolution is lazy so constructing the gate (e.g. at graph-factory
    time) never fails; a missing binary surfaces only when a gate call is made.
    """

    def __init__(self, binary: str | None = None, timeout: float = 30.0) -> None:
        self._binary = binary
        self._timeout = timeout

    def _resolve_binary(self) -> str:
        if self._binary is None:
            self._binary = _resolve_greenagent_binary()
        return self._binary

    def _run(self, args: list[str]) -> GateResult:
        binary = self._resolve_binary()
        try:
            proc = subprocess.run(  # noqa: S603 - trusted local CLI, fixed argv
                [binary, *args, "--json"],
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            return GateResult(ok=False, errors=("greenagent CLI timed out",))

        raw: dict = {}
        stdout = (proc.stdout or "").strip()
        if stdout:
            try:
                parsed = json.loads(stdout)
                if isinstance(parsed, dict):
                    raw = parsed
            except json.JSONDecodeError:
                logger.debug("greenagent CLI produced non-JSON stdout: %r", stdout)

        if raw:
            ok = bool(raw.get("ok"))
            errors = tuple(str(e) for e in (raw.get("errors") or ()))
        else:
            # No parseable JSON: fall back to exit code + stderr (e.g. the CLI
            # raised "DBTL cycle not found" and printed to stderr with code 1).
            ok = proc.returncode == 0
            stderr = (proc.stderr or "").strip()
            errors = () if ok else ((stderr or "greenagent CLI failed"),)
        return GateResult(ok=ok, errors=errors, raw=raw)

    def validate(self, project_path: str, cycle_id: str) -> GateResult:
        return self._run(["dbtl", "validate", "--project", project_path, "--cycle", cycle_id])

    def check_transition(
        self,
        project_path: str,
        cycle_id: str,
        to_state: str,
        *,
        actor: str,
        authorization: str,
    ) -> GateResult:
        return self._run(
            [
                "dbtl",
                "check-transition",
                "--project",
                project_path,
                "--cycle",
                cycle_id,
                "--to",
                to_state,
                "--actor",
                actor,
                "--authorization",
                authorization,
            ]
        )
