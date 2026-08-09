"""The house plot style must be one thing, and it must reproduce.

Two guarantees live here and nowhere else.

**One source.** The style exists twice by necessity — as a human-editable
`SKILL.md` any agent can read, and as the constant the Build phase prompt pastes
into a worker's instructions. A copy that silently drifts from the skill is worse
than no skill, because a person edits the file they can see and the figures never
change. This test fails the moment the two disagree.

**It reproduces.** Test re-runs a Build's recorded entry point in a *fresh*
workspace and refuses any figure whose bytes differ from the approved Build hash.
A style that renders differently across runs, machines, or matplotlib config
directories would invalidate honest cycles over nothing — so the block is
exercised end to end and its output hashed twice under deliberately hostile
conditions.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from deerflow.agents.dbtl.live_stage.build_phases import DBTL_PLOT_STYLE_BLOCK

SKILL = Path(__file__).resolve().parents[2] / "skills" / "public" / "dbtl-plot-style" / "SKILL.md"


def _skill_style_block() -> str:
    """The single ```python fence in the skill, minus its matplotlib imports."""
    body = SKILL.read_text(encoding="utf-8")
    fences = re.findall(r"```python\n(.*?)```", body, flags=re.DOTALL)
    assert len(fences) == 1, f"expected exactly one python block in {SKILL.name}, found {len(fences)}"
    return fences[0].split("plt.rcParams.update(", 1)[-1]


class TestTheStyleHasOneSource:
    def test_the_skill_exists_where_the_loader_reads_skills_from(self) -> None:
        # `<project_root>/skills` is one of the resolved skill roots, so a skill
        # written anywhere else is invisible to every agent that would use it.
        assert SKILL.is_file(), f"{SKILL} is missing"

    def test_the_prompt_constant_matches_the_skill_byte_for_byte(self) -> None:
        constant = DBTL_PLOT_STYLE_BLOCK.split("plt.rcParams.update(", 1)[-1]
        assert constant.strip() == _skill_style_block().strip(), "DBTL_PLOT_STYLE_BLOCK in build_phases.py has drifted from skills/public/dbtl-plot-style/SKILL.md. Edit the SKILL.md, then copy its python block into the constant."

    def test_the_phase_prompt_carries_the_style_and_the_png_only_rule(self) -> None:
        from deerflow.agents.dbtl.live_stage.build_phases import phase_unit
        from deerflow.dbtl.build_plan import BuildPhase
        from deerflow.dbtl.capabilities import Capability
        from deerflow.dbtl.stage_spec import resolve_stage_spec

        prompt = phase_unit(
            _assignment(BuildPhase(phase_key="fit", title="Fit", objective="Fit a model", capability=Capability.SOFTWARE_ENGINEERING, done_condition="model.bin exists")),
            index=0,
            attempt_id="attempt-1",
            attempt_token="token",
            spec=resolve_stage_spec("build"),
            context="ctx",
            result_contract="contract",
            granted_inputs=(),
        ).prompt

        assert "axes.prop_cycle" in prompt, "the style block never reaches the worker"
        assert "DejaVu Sans" in prompt
        assert "PDF and SVG embed a timestamp" in prompt


class TestTheStyleReproduces:
    """A figure drawn under this style must hash identically anywhere.

    The two runs below differ in `MPLCONFIGDIR` (so the font cache is rebuilt
    from scratch), in working directory, and in wall-clock time — the three
    things that have historically made image output non-deterministic. PDF and
    SVG are *not* tested as reproducible because they are not: both embed a
    creation date, which is exactly why the style forbids them.
    """

    @staticmethod
    def _script(out: str) -> str:
        return textwrap.dedent(
            f"""
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            {textwrap.indent(DBTL_PLOT_STYLE_BLOCK, " " * 12).lstrip()}
            import numpy as np
            rng = np.random.default_rng(7)
            fig, ax = plt.subplots()
            for i in range(3):
                ax.plot(np.arange(20), rng.normal(size=20).cumsum(), label=f"family {{i}}")
            ax.set_xlabel("Generation")
            ax.set_ylabel("Additive value (t/ha)")
            ax.set_title("Response to selection")
            ax.legend()
            fig.savefig({out!r})
            """
        )

    def test_the_same_figure_hashes_the_same_in_a_foreign_workspace(self, tmp_path: Path) -> None:
        pytest.importorskip("matplotlib")
        digests = []
        for run in ("a", "b"):
            work = tmp_path / run
            work.mkdir()
            script = work / "draw.py"
            png = work / "plot.png"
            script.write_text(self._script(str(png)), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(script)],
                cwd=work,
                check=True,
                capture_output=True,
                # A fresh MPLCONFIGDIR per run forces the font cache to be
                # rebuilt, which is the closest local stand-in for Test's rerun
                # landing on a machine that has never drawn a figure.
                env={"PATH": "/usr/bin:/bin", "MPLCONFIGDIR": str(work / ".matplotlib"), "HOME": str(work)},
            )
            digests.append(hashlib.sha256(png.read_bytes()).hexdigest())

        assert digests[0] == digests[1], "the house style does not render deterministically; Test's rerun would reject every figure"


def _assignment(phase):
    from deerflow.agents.dbtl.live_stage.build_phases import PhaseAssignment

    return PhaseAssignment(phase=phase, agent_name="builder", via_generalist=False)
