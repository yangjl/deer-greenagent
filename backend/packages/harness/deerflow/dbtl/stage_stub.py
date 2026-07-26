"""The controlled manual/stub stage adapter (Phase 5).

Phase 5 turns on *routing*, not stage execution. The plan states the constraint
twice — "keep stage execution behind a controlled manual/stub adapter" and
"prevent the stub from writing scientific results or satisfying gates" — because
a stub that quietly produced plausible-looking output would be the worst
possible outcome: a reviewer reading the demo transcript could not tell whether
the science happened.

So the guarantee is structural rather than a matter of discipline:

* This module imports nothing that can persist anything, and nothing from
  greenagent's transition gate. It has no way to write a result or approve a
  stage, which a test pins by reading this file's own source.
* ``writes_scientific_result`` and ``satisfies_gate`` are constants on a frozen
  value object, not fields. There is no call that sets them to ``True``.
* The rendered note says plainly that nothing was recorded, so the honest
  answer reaches the user rather than only the code.

Phase 6 replaces this with the real ``StageSpec`` registry and worker fan-out.
When it does, the thing to preserve is that gate satisfaction stays a
human-review record in Phase 1's governance tables, never a graph output.
"""

from __future__ import annotations

from dataclasses import dataclass

# The five DBTL stages, mirroring ``deerflow.dbtl.cycle_state``. Duplicated as
# plain strings rather than imported so this module keeps no dependency that
# could later grow a write path.
KNOWN_STAGES: frozenset[str] = frozenset({"design", "reconciliation", "build", "test", "learn"})

NO_EXECUTION_NOTE = "Stage execution is not enabled yet, so nothing has been recorded and no review gate has been satisfied."


@dataclass(frozen=True, slots=True)
class StageStubResult:
    """What the stub can say: that it did not do the work."""

    stage: str
    cycle_id: str | None
    note: str = NO_EXECUTION_NOTE

    @property
    def writes_scientific_result(self) -> bool:
        """Always ``False`` — a constant, so no caller can set it."""
        return False

    @property
    def satisfies_gate(self) -> bool:
        """Always ``False``.

        A gate is satisfied by a typed human review bound to the exact evidence
        revision the reviewer saw (Phase 1/3). A graph node is not a reviewer,
        so this can never be anything else.
        """
        return False


class ManualStageAdapter:
    """The only stage adapter Phase 5 ships.

    Named for what it is: stages advance manually, through the authenticated
    review endpoints, and this adapter exists so the continuation branch has
    something honest to say instead of improvising.
    """

    def execute(self, *, stage: str, cycle_id: str | None) -> StageStubResult:
        """Report what would happen, without doing any of it."""
        normalized = (stage or "").strip().lower()
        resolved = normalized if normalized in KNOWN_STAGES else "unknown"
        return StageStubResult(stage=resolved, cycle_id=cycle_id)
