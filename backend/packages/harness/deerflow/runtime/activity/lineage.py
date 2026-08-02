"""Lineage identifiers that survive a graph-node boundary.

A ContextVar carries the current activity down an ordinary call stack and across
the isolated subagent loop, but it does **not** reliably cross from one LangGraph
node to the next: the routing edge and the branch node it selects are separate
scheduled units, and neither the config nor the context dict is guaranteed to be
the same object by the time the second one runs.

Threading the parent id through graph state would work and costs a schema
change; mutating a shared context dict would work until LangGraph copied it. A
derived id needs neither. Both sides compute the same value from the run id and
a fixed actor key, so the branch node can name its dispatcher without anything
having been handed to it.

Determinism is also what makes a retry honest: the same run recomputes the same
supervisor id, so a replayed transition updates that row rather than opening a
second one beside it.
"""

import hashlib
from typing import Final

#: Fixed actor keys. These are *lineage* names, never display names — they never
#: reach a screen, so they may stay internal vocabulary.
SUPERVISOR_ACTOR_KEY: Final[str] = "dbtl-supervisor"
LEAD_ACTOR_KEY: Final[str] = "lead-agent"


def deterministic_activity_id(run_id: str, actor_key: str) -> str:
    """Return the activity id for one well-known actor within one run.

    Collision-free in practice and, more importantly, *stable*: two components
    that never speak to each other still agree on which row they are describing.
    """
    digest = hashlib.sha256(f"{run_id}:{actor_key}".encode()).hexdigest()[:24]
    return f"act_{digest}"


def supervisor_activity_id(run_id: str) -> str:
    """Return the cycle supervisor's activity id for this run."""
    return deterministic_activity_id(run_id, SUPERVISOR_ACTOR_KEY)


def lead_activity_id(run_id: str) -> str:
    """Return the lead agent's activity id for this run.

    Deterministic for the same reason the supervisor's is, and safe to derive
    because there is exactly one lead row per run — ``AgentActivityMiddleware``
    opens it once and refuses a second. That is what lets ``task_tool`` name its
    dispatcher: the delegation runs in the tools node, several graph nodes away
    from the hook that opened the row, so nothing has handed it a parent id and
    a ContextVar set in the opening node is long out of scope.
    """
    return deterministic_activity_id(run_id, LEAD_ACTOR_KEY)


def stage_activity_id(run_id: str, cycle_id: str, stage: str) -> str:
    """Return the stage adapter's activity id for one stage of one cycle.

    Keyed by cycle and stage as well as run, because a single request may
    legitimately touch a review meeting and then the stage it reviewed.
    """
    return deterministic_activity_id(run_id, f"stage:{cycle_id}:{stage}")
