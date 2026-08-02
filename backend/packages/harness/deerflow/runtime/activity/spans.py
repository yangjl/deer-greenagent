"""Convenience wrappers around :func:`activity_span` for instrumentation sites.

Instrumentation sits inside production control flow, so it has to be
unremarkable to read and impossible to trip over. These helpers exist so a call
site never has to write "if we happen to know the run id" branching around the
work it is describing.
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from deerflow.runtime.activity.emitter import ActivityHandle, activity_span


def run_id_from_config(config: Any) -> str | None:
    """Read the server-owned run id from either half of a ``RunnableConfig``.

    Reading the merged view is safe here specifically because the run worker
    *overwrites* ``context["run_id"]`` rather than defaulting it
    (``_install_runtime_context``), so a caller-supplied value can never be the
    one read back. That overwrite is what makes this read safe; it is not an
    assumption about client behaviour.

    A per-request selection such as ``dbtl_selected_cycle_id`` deliberately does
    *not* follow this pattern: ``configurable`` is checkpointed, so a value read
    from there would keep asserting itself on later turns.
    """
    if not isinstance(config, Mapping):
        return None

    configurable = config.get("configurable")
    nested_context = configurable.get("context") if isinstance(configurable, Mapping) else None
    pregel_runtime = configurable.get("__pregel_runtime") if isinstance(configurable, Mapping) else None
    runtime_context = getattr(pregel_runtime, "context", None)

    # LangGraph relocates top-level request context to
    # ``configurable["context"]`` before invoking a graph node. The private
    # Pregel runtime is the server-authored copy used by middleware and is read
    # as a second proof when present. Direct ``configurable.run_id`` remains a
    # legacy fallback for embedded callers.
    for section in (
        config.get("context"),
        runtime_context,
        nested_context,
        configurable,
    ):
        if isinstance(section, Mapping):
            value = section.get("run_id")
            if isinstance(value, str) and value:
                return value
    return None


@asynccontextmanager
async def optional_activity_span(run_id: object, **kwargs: Any) -> AsyncIterator[ActivityHandle | None]:
    """Open a span when the run identity is known, otherwise do nothing.

    An activity row keyed to no run cannot be reconciled with anything, so the
    honest response to a missing run id is silence rather than an orphan row.
    """
    if not isinstance(run_id, str) or not run_id:
        yield None
        return
    async with activity_span(run_id=run_id, **kwargs) as handle:
        yield handle
