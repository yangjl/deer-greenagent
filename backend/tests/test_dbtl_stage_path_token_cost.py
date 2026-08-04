"""Stage-work paths are re-sent on every model call, so their length is a cost.

A stage worker's every `write_file`, `read_file`, and `bash` call carries the
full virtual path in its arguments, the result echoes it, and the whole
conversation so far is re-sent on each of that phase's model calls. Two things
make those paths expensive out of proportion to what they say:

* the two content-addressed segments are random hex, which tokenizes at roughly
  half the density of ordinary prose, so a 20-character digest costs about as
  much as forty characters of English; and
* a shell command repeats the whole prefix on every path it names, even though
  Bash can bind it once.

Neither of these is a correctness property, which is exactly why they need
tests: nothing else fails when a path gets longer. What these pin is that the
digests stay short enough to be cheap and long enough to be safe, and that the
prompt teaches the one shell idiom the path audit already permits.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from deerflow.agents.dbtl.live_stage.build_phases import PhaseAssignment, phase_unit
from deerflow.agents.dbtl.live_stage.workspace import (
    STAGE_UNIT_WORKSPACE_PLACEHOLDER,
    WORKSPACE_VIRTUAL_ROOT,
    prepare_stage_workspace,
    safe_token,
    unit_stage_workspace,
)
from deerflow.dbtl.build_plan import BuildPhase
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_spec import resolve_stage_spec
from deerflow.sandbox.tools import validate_local_bash_command_paths


def _phase_prompt(*, stage: str = "build") -> str:
    spec = resolve_stage_spec(stage)
    assignment = PhaseAssignment(
        phase=BuildPhase(
            phase_key="simulate",
            title="Simulate the population",
            objective="Generate the founder population.",
            capability=Capability.SOFTWARE_ENGINEERING,
        ),
        agent_name="general-purpose",
        via_generalist=True,
    )
    return phase_unit(
        assignment,
        index=1,
        attempt_id="dbtl-abc123",
        attempt_token="tok",
        spec=spec,
        context="(none)",
    ).prompt


class TestTheDigestIsShortEnoughToBeCheap:
    """A content-addressed segment pays hex-rate tokens for every character."""

    def test_a_workspace_token_is_twelve_hex_characters(self) -> None:
        token = safe_token("dbtl-stage:run-1:cycle-1")
        assert len(token) == 12, "a longer digest costs real tokens on every path in every call"
        assert token == hashlib.sha256(b"dbtl-stage:run-1:cycle-1").hexdigest()[:12]

    def test_a_token_is_still_wide_enough_to_separate_concurrent_workers(self) -> None:
        # 12 hex characters is 48 bits. The values being separated are unit ids
        # within one stage attempt and attempt ids within one project — tens of
        # values, not billions — so a collision here is not a realistic event.
        # This asserts the floor rather than the exact width, because the reason
        # to keep the digest wide is safety and the reason to keep it narrow is
        # cost; only the former belongs in a hard bound.
        assert len(safe_token("x")) >= 12

    def test_distinct_units_still_receive_distinct_directories(self) -> None:
        stage = f"{WORKSPACE_VIRTUAL_ROOT}/outputs/.dbtl-stage-work/dbtl-abc123def456/build"
        first = unit_stage_workspace(stage, "dbtl-abc-1-simulate-tok")
        second = unit_stage_workspace(stage, "dbtl-abc-2-fit-tok")
        assert first != second
        assert first.startswith(stage) and second.startswith(stage)

    def test_a_stage_workspace_path_stays_under_the_budget(self, tmp_path: Path) -> None:
        virtual, host = prepare_stage_workspace(
            str(tmp_path),
            attempt_id=f"dbtl-{safe_token('dbtl-stage:run-1:cycle-1')}",
            stage="build",
        )
        assert host.is_dir()
        # The prefix is fixed and shared; the digest is the only part that grew.
        # 80 characters leaves room for a `src/simulate.py` suffix inside a
        # comfortable single line, which is what keeps a tool-call argument from
        # wrapping into a second expensive fragment.
        assert len(virtual) <= 80, f"stage workspace path is {len(virtual)} characters: {virtual}"


class TestThePromptTeachesTheCheapShellIdiom:
    """Bash can bind the prefix once; the prompt has to say so."""

    def test_the_phase_prompt_shows_binding_the_workspace_to_a_variable(self) -> None:
        prompt = _phase_prompt()
        assert "STAGE=" in prompt, "a worker that is not shown the idiom repeats the full path in every command"
        assert '"$STAGE"' in prompt

    def test_the_phase_prompt_still_requires_full_paths_where_they_are_required(self) -> None:
        # The variable is a *shell* convenience. `write_file` and the result
        # contract resolve their own arguments and have no shell to expand it,
        # so teaching the idiom without this sentence trades tokens for a run of
        # refused writes.
        prompt = _phase_prompt()
        assert "write_file" in prompt
        assert "full path" in prompt.lower()

    def test_the_taught_idiom_is_the_one_the_path_audit_permits(self, tmp_path: Path) -> None:
        # The audit allows a single literal, ordered assignment to authorize a
        # later `cd "$STAGE"`. Teaching any other shape — a reassignment, a
        # command substitution, an assignment after `&&` — would produce a
        # prompt whose own example is refused.
        thread_data = {
            "workspace_dir": str(tmp_path / "workspace"),
            "uploads_dir": str(tmp_path / "uploads"),
            "outputs_dir": str(tmp_path / "outputs"),
        }
        stage = f"{WORKSPACE_VIRTUAL_ROOT}/outputs/.dbtl-stage-work/dbtl-abc123def456/build"
        validate_local_bash_command_paths(
            f'STAGE={stage}; cd "$STAGE"; python src/simulate.py',
            thread_data,  # type: ignore[arg-type]
        )

    def test_the_placeholder_is_still_what_the_dispatcher_replaces(self) -> None:
        # The guidance names the variable, but the directory itself must remain
        # the placeholder the production dispatcher rewrites per unit; inlining
        # a literal path here would hand every concurrent worker the same grant.
        assert STAGE_UNIT_WORKSPACE_PLACEHOLDER in _phase_prompt()


@pytest.mark.parametrize("stage", ["build", "test"])
def test_every_executable_stage_prompt_carries_the_shell_guidance(stage: str) -> None:
    # Test workers run scripts against the same tree and pay the same repeats.
    assert "STAGE=" in _phase_prompt(stage=stage)
