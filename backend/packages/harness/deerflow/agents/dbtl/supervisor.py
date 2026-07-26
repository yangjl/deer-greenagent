"""The thin project supervisor graph (Phase 5).

    START ──▶ (route) ──┬──▶ ordinary ─────────────▶ END   (the real lead agent)
                        ├──▶ clarification ────────▶ END
                        ├──▶ cycle_setup ──────────▶ END
                        └──▶ cycle_continuation ───▶ END

Four properties are worth explaining, because each is load-bearing for the
phase's no-go ("routing must not drop messages, project scope, file access, or
artifact inspection"):

**The ordinary branch *is* the lead agent.** It is the compiled lead-agent graph
added directly as a node, sharing this graph's state schema — not a
reimplementation and not a wrapper that copies fields across. Every middleware,
tool, sandbox mount, and artifact path therefore behaves exactly as it does
today, because it is the same graph. Nothing about ordinary work is re-derived
here, which is the only way "indistinguishable from the pre-supervisor
experience" can be true rather than aspirational.

**Delegation depends on idempotent reducers.** A compiled child used as a node
returns its *entire final state* as its update, which this graph then re-applies
through its own reducers. That is safe only because every ``ThreadState`` channel
merges by id or key (``add_messages`` dedupes by message id, ``merge_artifacts``
by value) rather than blindly accumulating. A channel added later with a naive
accumulator would duplicate the whole conversation on the first delegated turn,
so ``tests/test_dbtl_supervisor.py`` pins that invariant explicitly.

**Routing reads a value, not ambient state.** The selected project and cycle
arrive as an explicit :class:`SupervisorContext` captured per run from the
request's runtime context. They decide which folder and which research record a
request may touch, so they are passed in rather than looked up mid-graph where a
stale or forged value would be invisible.

**Every branch is terminal.** The supervisor routes once and the graph ends. It
never loops between branches, so one request cannot silently become several, and
"thin" stays checkable rather than being a description of the original intent.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from deerflow.dbtl.branches import (
    BranchDecision,
    SupervisorBranch,
    SupervisorContext,
    resolve_branch,
)
from deerflow.dbtl.proposal import (
    CONFIRMATION_REQUIRED_NOTICE,
    NO_RECORD_NOTICE,
    RECORD_EFFECT,
    REQUIRED_GATES,
)
from deerflow.dbtl.routing import ExplicitChoice
from deerflow.dbtl.stage_stub import ManualStageAdapter

logger = logging.getLogger(__name__)

# Runtime-context keys the frontend's context chip sets for the *next* request
# only. Read from runtime context rather than ``configurable`` because
# ``configurable`` is checkpointed: a per-request selection written there would
# outlive the request and silently apply to later turns.
SELECTED_CYCLE_CONTEXT_KEY = "dbtl_selected_cycle_id"
EXPLICIT_CHOICE_CONTEXT_KEY = "dbtl_explicit_choice"


def _latest_user_text(state: dict) -> str:
    """The newest visible user message, as plain text.

    Hidden ``HumanMessage``s are skipped: goal continuations, human-input card
    replies, and injected context blocks are machine-authored, and routing on
    one would let internal plumbing steer a research decision.
    """
    from deerflow.utils.messages import message_content_to_text

    for message in reversed(state.get("messages") or []):
        if not isinstance(message, HumanMessage):
            continue
        extra = getattr(message, "additional_kwargs", None) or {}
        if extra.get("hide_from_ui") or extra.get("human_input_response"):
            continue
        return message_content_to_text(message.content) or ""
    return ""


def _bullets(items: tuple[str, ...] | list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _render_clarification(decision: BranchDecision, context: SupervisorContext) -> str:
    project = context.project_name or "this project"
    return f"Before starting a DBTL cycle in {project}, I need a few things the request does not say yet:\n\n{_bullets(decision.missing_fields)}\n\n{NO_RECORD_NOTICE}"


def _render_cycle_setup(decision: BranchDecision, context: SupervisorContext) -> str:
    project = context.project_name or "this project"
    lines = [
        f"This looks like it could be a DBTL cycle in {project}.",
        "",
        f"Proposed objective: {decision.objective}" if decision.objective else "Proposed objective: (not stated)",
    ]
    if decision.missing_fields:
        lines += ["", "Missing before start:", _bullets(decision.missing_fields)]
    lines += [
        "",
        "Required human gates:",
        _bullets(REQUIRED_GATES),
        "",
        RECORD_EFFECT,
        "",
        CONFIRMATION_REQUIRED_NOTICE,
        NO_RECORD_NOTICE,
    ]
    return "\n".join(lines)


def _render_continuation(decision: BranchDecision, note: str) -> str:
    cycle = decision.cycle_id or "the selected cycle"
    return f"This request is scoped to {cycle}.\n\n{note}\n\nNo results have been recorded against {cycle}. Stage work advances through the project's Design and Data reconciliation reviews, which a person completes."


def build_supervisor_graph(
    *,
    lead_agent,
    context: SupervisorContext,
    state_schema,
    stage_adapter: ManualStageAdapter | None = None,
) -> StateGraph:
    """Build (but do not compile) the supervisor graph.

    ``lead_agent`` is the compiled lead-agent graph and ``context`` the run's
    resolved project/cycle selection; both are injected so tests can drive every
    branch without an LLM and without a checkpointed thread.
    """
    adapter = stage_adapter or ManualStageAdapter()

    def decide(state: dict) -> BranchDecision:
        return resolve_branch(_latest_user_text(state), context)

    def route(state: dict) -> str:
        decision = decide(state)
        logger.debug(
            "dbtl supervisor route: branch=%s source=%s project=%s cycle=%s",
            decision.branch,
            decision.route.source,
            context.project_id,
            decision.cycle_id,
        )
        return decision.branch.value

    def clarification(state: dict) -> dict:
        decision = decide(state)
        return {"messages": [AIMessage(content=_render_clarification(decision, context))]}

    def cycle_setup(state: dict) -> dict:
        decision = decide(state)
        return {"messages": [AIMessage(content=_render_cycle_setup(decision, context))]}

    def cycle_continuation(state: dict) -> dict:
        decision = decide(state)
        result = adapter.execute(stage="design", cycle_id=decision.cycle_id)
        return {"messages": [AIMessage(content=_render_continuation(decision, result.note))]}

    builder = StateGraph(state_schema)
    # The ordinary branch is the lead agent itself, unmodified.
    builder.add_node(SupervisorBranch.ORDINARY.value, lead_agent)
    builder.add_node(SupervisorBranch.CLARIFICATION.value, clarification)
    builder.add_node(SupervisorBranch.CYCLE_SETUP.value, cycle_setup)
    builder.add_node(SupervisorBranch.CYCLE_CONTINUATION.value, cycle_continuation)

    builder.add_conditional_edges(START, route, [branch.value for branch in SupervisorBranch])
    for branch in SupervisorBranch:
        builder.add_edge(branch.value, END)
    return builder


def _resolve_explicit_choice(raw: object) -> ExplicitChoice | None:
    """Coerce a client-supplied choice, ignoring anything unrecognized.

    The chip sends this per request, so it is untrusted input. An unknown value
    must fall through to normal routing rather than raising, or a stale frontend
    would break the conversation instead of merely losing a preference.
    """
    if not isinstance(raw, str):
        return None
    try:
        return ExplicitChoice(raw)
    except ValueError:
        logger.debug("ignoring unrecognized DBTL explicit choice %r", raw)
        return None


def supervisor_context_from_config(config: RunnableConfig) -> SupervisorContext:
    """Read the run's project and cycle selection from its runtime context.

    ``project_id`` is server-owned: the Gateway drops any caller-supplied value
    and re-stamps it from the durable membership record, so it is read from the
    merged runtime view.

    The per-request selection is read from ``context`` **only**, never from the
    merged view. ``configurable`` is checkpointed: a selection accepted from
    there would survive into later turns and keep steering them, which is
    exactly the "affects the next request only" guarantee the context chip
    makes to the user. Reading one key from one place is what enforces it.

    ``selected_cycle_id`` is a client preference and is deliberately *not*
    verified here — Phase 5 writes nothing, so the worst a forged value can do
    is route to the stub adapter, which records nothing. Any phase that gives
    the continuation branch real authority must verify it against project
    membership first.
    """
    from deerflow.agents.lead_agent.agent import _get_runtime_config

    cfg = _get_runtime_config(config)
    raw_context = config.get("context") or {}
    request_context = raw_context if isinstance(raw_context, dict) else {}

    project_id = cfg.get("project_id")
    selected = request_context.get(SELECTED_CYCLE_CONTEXT_KEY)
    return SupervisorContext(
        project_id=str(project_id) if project_id else None,
        project_name=str(cfg.get("project_name") or ""),
        selected_cycle_id=str(selected) if selected else None,
        explicit_choice=_resolve_explicit_choice(request_context.get(EXPLICIT_CHOICE_CONTEXT_KEY)),
    )


def make_project_supervisor(config: RunnableConfig):
    """LangGraph factory for the ``project_supervisor`` assistant_id.

    Mirrors ``make_lead_agent(config)`` so the run worker drives it identically,
    and freezes/injects the checkpoint channel mode the same way so the
    runtime's checkpoint machinery stays consistent. The compiled graph carries
    no checkpointer; the runtime injects one.
    """
    from deerflow.agents.lead_agent.agent import make_lead_agent
    from deerflow.agents.thread_state import get_thread_state_schema
    from deerflow.config.app_config import AppConfig, get_app_config
    from deerflow.runtime.checkpoint_mode import (
        INTERNAL_CHECKPOINT_MODE_KEY,
        freeze_checkpoint_channel_mode,
        frozen_checkpoint_channel_mode,
        inject_checkpoint_mode,
    )

    configurable = config.get("configurable", {}) or {}
    runtime_app_config = configurable.get("app_config")
    if not isinstance(runtime_app_config, AppConfig):
        runtime_app_config = get_app_config()

    frozen_mode = frozen_checkpoint_channel_mode()
    if frozen_mode is None:
        requested_mode = runtime_app_config.database.checkpoint_channel_mode
    else:
        requested_mode = configurable.get(
            INTERNAL_CHECKPOINT_MODE_KEY,
            runtime_app_config.database.checkpoint_channel_mode,
        )
    mode = freeze_checkpoint_channel_mode(requested_mode)
    inject_checkpoint_mode(config, mode)

    # ``make_lead_agent`` re-freezes the same mode (idempotent) and builds the
    # full middleware chain, so the ordinary branch is the production agent.
    lead_agent = make_lead_agent(config)

    graph = build_supervisor_graph(
        lead_agent=lead_agent,
        context=supervisor_context_from_config(config),
        state_schema=get_thread_state_schema(mode),
    )
    return graph.compile()
