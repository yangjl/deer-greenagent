"""One re-runnable command for a Build that ran as several phases.

Test re-runs a Build by executing a single recorded command
(`live_stage/test_rerun.py`), and a phased Build has one entry point per phase.
Asking each worker for its own `provenance.rerun_spec` cannot answer that: the
specs merge only when they agree, so two phases naming different entry points
conflict and `parse_execution_bundle` reports no rerun record at all — which
fails the `structured_rerun_spec` gate and leaves a Build that ran every planned
phase unable to reach its human gate.

So the server writes the command instead of asking for it. It already knows each
phase's verified entry point and the inputs it issued, and it ran those entry
points itself during phase verification, so a driver that repeats them in order
is a record of what actually happened rather than a worker's account of it.

The script is deliberately dumb: no branching, no discovery, no cleanup. It is
read by a person deciding whether the Build is reproducible, so anything it does
beyond "run these, in this order, with these inputs" is something they would
have to verify before trusting the record.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from deerflow.agents.dbtl.live_stage.build_phase_verification import entry_command
from deerflow.dbtl.build_execution import BuildRerunSpec
from deerflow.dbtl.build_grant import INPUT_ENV_PREFIX, PROJECT_ROOT_ENV, WORKSPACE_ENV

#: Name of the script written beside the Build's review package.
DRIVER_FILENAME = "rerun-build.sh"

#: An upper bound on how many phases a driver will describe. A plan far larger
#: than any real Build is a sign something is wrong upstream, and a script that
#: grows without limit is one nobody reads.
MAX_DRIVER_PHASES = 64


@dataclass(frozen=True, slots=True)
class DriverPhase:
    """One phase as the driver needs to re-run it."""

    title: str
    entry_point: str
    execution_inputs: tuple[str, ...] = ()


def _phase_block(phase: DriverPhase, *, position: int) -> list[str]:
    """The lines that re-run one phase.

    Inputs are exported per phase rather than once at the top, because
    `DBTL_INPUT_n` is numbered within a phase: phase 2's first input is its own
    `DBTL_INPUT_1`, not a continuation of phase 1's numbering. Exporting a
    union would silently hand a phase the wrong file under the right name.
    """
    lines = [
        "",
        f"# Phase {position}: {phase.title}",
        f'echo "== phase {position}: {phase.title}"',
    ]
    for index, path in enumerate(phase.execution_inputs, start=1):
        lines.append(f"export {INPUT_ENV_PREFIX}{index}={shlex.quote(path)}")
    lines.append(f"export {INPUT_ENV_PREFIX}COUNT={len(phase.execution_inputs)}")
    lines.append(entry_command(phase.entry_point))
    return lines


def render_driver_script(phases: Sequence[DriverPhase], *, workspace_root: str, project_root: str) -> str:
    """Render the ordered re-run script, or `""` when there is nothing to run.

    `set -euo pipefail` is what makes the script a *check* rather than a
    demonstration: without it a phase that failed would be stepped over and the
    script would still exit 0, so Test would record a successful reproduction of
    a Build that did not reproduce.
    """
    runnable = [phase for phase in phases if phase.entry_point.strip()]
    if not runnable or len(runnable) > MAX_DRIVER_PHASES:
        return ""
    lines = [
        "#!/bin/bash",
        "# Re-runs this Build's phases in the order they were executed.",
        "# Written by DeerFlow from the verified phase manifests; do not edit.",
        "set -euo pipefail",
        "",
        f"export {WORKSPACE_ENV}={shlex.quote(workspace_root)}",
        f"export {PROJECT_ROOT_ENV}={shlex.quote(project_root)}",
    ]
    for position, phase in enumerate(runnable, start=1):
        lines.extend(_phase_block(phase, position=position))
    lines.append("")
    return "\n".join(lines)


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value.strip()))


def driver_rerun_spec(
    phases: Sequence[DriverPhase],
    *,
    driver_path: str,
    expected_outputs: Sequence[str],
    environment: dict[str, str],
    bound_inputs: Sequence[str] = (),
    seed: str = "",
) -> BuildRerunSpec | None:
    """The rerun record naming the driver, or `None` when there is none to name.

    `bound_inputs` is what the Build actually read, as the server bound it into
    lineage. It is unioned with the phases' runtime inputs rather than replacing
    them, because the two answer different questions: the runtime inputs are
    what the entry points consume (and are all a pre-v3 manifest can report,
    which is none), while lineage is the full set Test verifies has not changed
    under the record.

    Returning `None` rather than an empty spec keeps the gate meaningful: a
    Build with no runnable entry point has not recorded how to re-run itself,
    and saying so is the whole point of `structured_rerun_spec`.
    """
    runnable = [phase for phase in phases if phase.entry_point.strip()]
    if not runnable or not driver_path.strip():
        return None
    return BuildRerunSpec(
        entry_point=driver_path,
        command=f"/bin/bash {shlex.quote(driver_path)}",
        seed=seed,
        inputs=_ordered_unique((*(path for phase in runnable for path in phase.execution_inputs), *bound_inputs)),
        environment=dict(environment),
        configuration=(),
        expected_outputs=_ordered_unique(expected_outputs),
    )
