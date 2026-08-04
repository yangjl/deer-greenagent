"""Give a governed Build phase one bounded chance to fix a failed check.

The phase's first structured result is still only a worker report. When that
report names a failed implementation check, ending the child graph immediately
throws away the most useful recovery context: the files, tool results, and
failure detail are all still present in the same attempt. This middleware loops
back once, tells the worker exactly which checks failed, and leaves its existing
tools and budget unchanged.

It never changes a verdict itself. The worker must repair the implementation,
rerun the failed check, and return the complete structured result again. If the
second result still fails, the ordinary server contract rejects it. Repeat-run
and reproducibility checks are excluded because Test owns that verdict.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

from deerflow.agents.dbtl.live_stage.build_phases import PHASE_DONE_CHECK, gating_failed_phase_checks
from deerflow.dbtl.worker_result import WorkerResultRejected, WorkerStatus, extract_result_payload, parse_worker_result
from deerflow.utils.messages import message_content_to_text

# One startup reset plus the first and final ``after_agent`` visits. The
# dispatcher holds these one-time nodes out of the graph recursion budget.
BUILD_PHASE_CORRECTION_HEADROOM_STEPS = 3

_GUARD_STOP_REASONS = frozenset({"token_capped", "loop_capped", "turn_capped", "safety_capped"})

_CORRECTION_PROMPT = """<system_reminder>
Your structured Build result reported these failed implementation checks: {checks}.
You have one correction cycle in this same attempt. Use the existing files and tool
results to fix only the named failure, then rerun each named check. Do not change a
failed check to passed unless the rerun actually passes. Return the complete required
JSON result again. If the failure cannot be corrected within the remaining budget,
return it honestly as failed with the evidence and limitation preserved.
</system_reminder>"""


class BuildPhaseCorrectionMiddleware(AgentMiddleware[AgentState]):
    """Loop a Build phase back to the model at most once after failed checks."""

    def __init__(
        self,
        *,
        capability: str,
        agent_name: str,
        retry_blocked: Callable[[Runtime], bool] | None = None,
    ) -> None:
        super().__init__()
        self._capability = capability
        self._agent_name = agent_name
        self._retry_blocked = retry_blocked
        self._lock = threading.Lock()
        self._used = False

    def _reset(self) -> None:
        with self._lock:
            self._used = False

    def _apply(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        context = getattr(runtime, "context", None)
        if isinstance(context, dict) and context.get("stop_reason") in _GUARD_STOP_REASONS:
            return None
        if self._retry_blocked is not None and self._retry_blocked(runtime):
            return None

        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        last = messages[-1]
        if last.tool_calls or getattr(last, "invalid_tool_calls", None):
            return None

        try:
            payload = extract_result_payload(message_content_to_text(last.content))
            result = parse_worker_result(
                payload,
                capability=self._capability,
                agent_name=self._agent_name,
                stage="build",
            )
        except WorkerResultRejected:
            # Schema repair belongs to the existing parser compatibility
            # boundary. This retry is only for a check the worker actually ran.
            return None
        if result.status not in {WorkerStatus.COMPLETED, WorkerStatus.FAILED}:
            return None
        failed_checks = gating_failed_phase_checks(result, PHASE_DONE_CHECK)
        if not failed_checks:
            return None

        with self._lock:
            if self._used:
                return None
            self._used = True

        reminder = HumanMessage(
            content=_CORRECTION_PROMPT.format(checks=", ".join(failed_checks[:4])),
            name="build_phase_correction",
            additional_kwargs={"hide_from_ui": True},
        )
        # Keep the failed terminal report in the child history. Besides giving
        # the correction call the exact evidence it is repairing, this keeps
        # FinalizationDeadlineMiddleware's message-based model-call counter
        # honest. Removing the AI message would refund one call and could let
        # the correction overrun the graph recursion budget.
        return {"messages": [reminder], "jump_to": "model"}

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._reset()
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._reset()
        return None

    @hook_config(can_jump_to=["model"])
    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._apply(state, runtime)

    @hook_config(can_jump_to=["model"])
    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._apply(state, runtime)
