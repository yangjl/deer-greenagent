"""What a worker cost, counted once and reported once.

Three small functions the adapter used to own privately. They are here for the
same reason the file-integrity helpers moved to ``workspace``: tests replace
them to observe or silence token accounting, and a private copy in the module
that happens to call them is a patch that stops working the moment the caller
moves. One owner means one patch target, wherever the caller ends up.

Nothing here decides anything — it sums, merges, and hands the totals to the
run's callbacks. A merge that double-counted would inflate every budget
decision downstream, so the merge is deliberately dumb and total.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)


def _summarize_token_usage(
    records: Sequence[Mapping[str, int | str | None]] | None,
) -> dict[str, int] | None:
    """Collapse a seat's per-call records into one provider-reported meter."""
    if not records:
        return None
    usage = {key: sum(int(record.get(key, 0) or 0) for record in records if isinstance(record.get(key, 0), (int, float))) for key in ("input_tokens", "output_tokens", "total_tokens")}
    return usage if any(usage.values()) else None


def _merge_token_usage(*values: Mapping[str, int] | None) -> dict[str, int]:
    return {key: sum(int(value.get(key, 0) or 0) for value in values if value is not None) for key in ("input_tokens", "output_tokens", "total_tokens")}


def _report_subagent_token_usage(
    config: RunnableConfig,
    result: Any,
) -> None:
    """Add direct DBTL subagent calls to the parent run's usage journal once."""
    if getattr(result, "usage_reported", True):
        return
    records = getattr(result, "token_usage_records", None) or []
    if not records:
        return
    callbacks = config.get("callbacks")
    handlers = getattr(callbacks, "handlers", callbacks)
    if not isinstance(handlers, Sequence) or isinstance(handlers, (str, bytes)):
        return
    for handler in handlers:
        recorder = getattr(handler, "record_external_llm_usage_records", None)
        if not callable(recorder):
            continue
        try:
            recorder(records)
            result.usage_reported = True
        except Exception:  # noqa: BLE001 - metering failure must not lose work
            logger.warning("Failed to record Design council token usage.", exc_info=True)
        return
