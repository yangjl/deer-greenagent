"""Give a bounded worker a deadline it can actually meet.

A DBTL stage worker is graded on its *final message*: ``extract_result_payload``
reads the structured result out of it and rejects anything else. But the turn
budget is enforced by ``recursion_limit``, which raises ``GraphRecursionError``
from the middle of a tool loop. The executor then recovers "the last AIMessage
carrying text", and mid-loop that is prose, a thinking fragment, or nothing at
all. So a worker that spends its budget cannot satisfy its own contract however
well it investigated — and the whole Design council failed exactly this way,
three seats at a time, with the rejection recorded as "returned prose instead of
a structured result".

The other two guardrail axes already solved this shape. ``TokenBudgetMiddleware``
and ``LoopDetectionMiddleware`` do not raise: they strip ``tool_calls`` so the
agent loop terminates naturally and the model writes a real final answer. This
middleware closes the turn axis the same way, with one difference that matters.

**A met deadline is not a cap.** The other two guards record a
``stop_reason``, which folds into ``CAPPED_STOP_REASONS`` and makes
``StageWorkerResult.is_trustworthy`` false — correct for them, because a run
that blew through a safety limit really is a truncated investigation. A worker
that was warned "you have N calls left, write your result now" and did so has
answered on time, and discarding that as untrustworthy would reproduce the bug
this exists to fix. So the forced finalization is exposed as an inspectable flag
instead of a stop reason, and the caller decides how to show it to a reviewer.
Deliberately no ``consume_stop_reason`` method: the executor collects guards by
duck-typing on that name, so defining it would enrol this in the cap channel by
accident.

The deadline is an execution-layer behaviour, not a contract term. ``StageSpec``
budgets stay part of the versioned spec so a reviewer can reconstruct what a
worker was allowed to spend; this only makes the existing allowance reachable.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

from deerflow.agents.middlewares._bounded_dict import BoundedDict

logger = logging.getLogger(__name__)

DEFAULT_RESERVE_CALLS = 3

#: What one tool-calling worker turn actually costs in LangGraph super-steps.
#:
#: ``max_turns`` reaches the graph as ``recursion_limit``, which counts
#: super-steps rather than conversational turns — and LangGraph gives **every**
#: middleware ``before_model`` / ``after_model`` hook its own node. So a turn is
#: not "model + tools" (2); it is the model node, plus one node per hook in the
#: shared subagent chain, plus the tools node, plus this middleware's own
#: ``after_model``. Reading ``max_turns`` as turns overstates the real allowance
#: by more than fourfold, which is how a deadline set at half the limit came to
#: fire after the run had already been aborted.
#:
#: Measured from the real chain and pinned by
#: ``tests/test_finalization_deadline_middleware.py::TestTheTurnCostAssumption``:
#: if a middleware gains or loses a hook, that test fails and names the new
#: number rather than letting every stage worker quietly lose budget.
SUBAGENT_SUPERSTEPS_PER_TURN = 9

#: Super-steps held back so the forced final answer has somewhere to land.
#: Roughly one turn's worth: at the moment the deadline fires, the graph still
#: has to run this middleware's rewrite and the remaining hook nodes.
_DEADLINE_HEADROOM_STEPS = SUBAGENT_SUPERSTEPS_PER_TURN


def model_call_budget(max_turns: int, *, steps_per_turn: int = SUBAGENT_SUPERSTEPS_PER_TURN) -> int:
    """How many model calls actually fit in a ``recursion_limit`` of ``max_turns``.

    Divides by the true per-turn super-step cost, then reserves a turn's worth
    of headroom so the forced answer can be produced *and committed* before the
    graph aborts. The floor keeps a degenerate budget legal: a deadline that
    warns on the first call and stops on the second is worse than none, so the
    result never drops below the reserve plus the one call needed to answer in.
    """
    usable = max(0, int(max_turns) - _DEADLINE_HEADROOM_STEPS)
    return max(DEFAULT_RESERVE_CALLS + 1, usable // max(1, int(steps_per_turn)))


_DEADLINE_NOTICE = (
    "[FINALIZATION DEADLINE] You have {remaining} model call(s) left before this "
    "run ends. Stop investigating now and spend the next call writing your "
    "result. Your final message is the only thing that is read: it must be the "
    "single JSON object your instructions specified, and nothing else. Report "
    "what you could not finish under 'limitations' rather than continuing to "
    "look into it."
)

_FORCED_NOTICE = "\n\n[FINALIZATION DEADLINE] The turn budget for this run is exhausted, so no further tool calls are possible. This message is the worker's final answer."


class FinalizationDeadlineMiddleware(AgentMiddleware[AgentState]):
    """Make a worker land its structured result before its turns run out."""

    def __init__(self, *, max_model_calls: int, reserve_calls: int = DEFAULT_RESERVE_CALLS) -> None:
        super().__init__()
        # A reserve wider than the whole budget would warn on the first call and
        # force a stop on the second, which is worse than no deadline at all.
        # One call must always remain for the model to actually answer in.
        self._max_model_calls = max(1, int(max_model_calls))
        self._reserve_calls = max(0, min(int(reserve_calls), self._max_model_calls - 1))
        self._lock = threading.Lock()

        self._warned: BoundedDict[str, bool] = BoundedDict(1000)
        self._pending_notices: BoundedDict[str, list[str]] = BoundedDict(1000)
        # Not cleared with the rest of the run state: the dispatcher reads it
        # after the run returns to annotate the result for a human reviewer.
        self._forced: BoundedDict[str, bool] = BoundedDict(1000)

    @staticmethod
    def _run_id(runtime: Runtime) -> str:
        context = getattr(runtime, "context", None)
        if isinstance(context, dict) and "run_id" in context:
            return str(context["run_id"])
        return str(id(runtime))

    def forced_finalization(self, runtime: Runtime | None) -> bool:
        """Whether this run had to be stopped to get its answer.

        Read rather than popped: the dispatcher may consult it more than once
        while assembling the result, and the bounded dict caps the leak from
        abandoned runs on a reused instance.
        """
        if runtime is None:
            return False
        with self._lock:
            return bool(self._forced.get(self._run_id(runtime), False))

    def forced_any(self) -> bool:
        """Whether any run on this instance had to be stopped to answer.

        The DBTL dispatcher builds one middleware per work unit, so it has no
        handle on the child's run id — the subagent mints its own. With a
        single-run instance the two questions collapse, and asking this one
        avoids threading a runtime the caller does not own.
        """
        with self._lock:
            return any(self._forced.values())

    def reset(self) -> None:
        with self._lock:
            self._warned.clear()
            self._pending_notices.clear()
            self._forced.clear()

    @staticmethod
    def _model_calls(messages: list[Any]) -> int:
        return sum(1 for message in messages if isinstance(message, AIMessage))

    @staticmethod
    def _append_text(content: Any, text: str) -> Any:
        if isinstance(content, list):
            return [*content, {"type": "text", "text": text}]
        if isinstance(content, str):
            return f"{content}{text}" if content else text.lstrip()
        return f"{content}{text}"

    def _force_finalization(self, message: AIMessage) -> dict[str, Any]:
        """Strip the tool calls so the agent loop ends with a real answer.

        Mirrors ``TokenBudgetMiddleware._build_hard_stop_update``: the raw
        provider payloads have to go too, or a strict backend re-issues the
        tool calls from ``additional_kwargs`` on the next request.
        """
        additional_kwargs = dict(message.additional_kwargs or {})
        additional_kwargs.pop("tool_calls", None)
        additional_kwargs.pop("function_call", None)

        response_metadata = dict(getattr(message, "response_metadata", {}) or {})
        if response_metadata.get("finish_reason") == "tool_calls":
            response_metadata["finish_reason"] = "stop"

        stopped = message.model_copy(
            update={
                "content": self._append_text(message.content, _FORCED_NOTICE),
                "tool_calls": [],
                "additional_kwargs": additional_kwargs,
                "response_metadata": response_metadata,
            }
        )
        return {"messages": [stopped]}

    def _apply(self, state: AgentState, runtime: Runtime) -> dict | None:
        messages = list(state.get("messages", []) or [])
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None

        run_id = self._run_id(runtime)
        used = self._model_calls(messages)
        remaining = self._max_model_calls - used

        with self._lock:
            # Nothing to force once the model has stopped calling tools — it is
            # already writing its answer, and rewriting that message would only
            # append a deadline notice to a result the parser then has to read
            # around.
            if remaining <= 0 and last.tool_calls:
                logger.info(
                    "Finalization deadline reached for run %s after %s model call(s); forcing a final answer",
                    run_id,
                    used,
                )
                self._forced[run_id] = True
                return self._force_finalization(last)

            if remaining <= self._reserve_calls and not self._warned.get(run_id, False):
                self._warned[run_id] = True
                self._pending_notices.setdefault(run_id, []).append(_DEADLINE_NOTICE.format(remaining=max(remaining, 0)))
            return None

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    def drain_pending_notices(self, runtime: Runtime) -> list[str]:
        with self._lock:
            return self._pending_notices.pop(self._run_id(runtime), None) or []

    def _with_notices(self, request: ModelRequest) -> ModelRequest:
        notices = self.drain_pending_notices(request.runtime)
        if not notices:
            return request
        # The warning says "write the result now", so make that instruction
        # mechanically true. Leaving tools available let the worker ignore the
        # notice, call one more tool, and eventually reach recursion_limit with
        # prose again—the exact failure this middleware exists to prevent.
        return request.override(
            messages=[
                *request.messages,
                *(HumanMessage(content=text) for text in notices),
            ],
            tools=[],
        )

    @override
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelCallResult:
        return handler(self._with_notices(request))

    @override
    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelCallResult:
        return await handler(self._with_notices(request))
