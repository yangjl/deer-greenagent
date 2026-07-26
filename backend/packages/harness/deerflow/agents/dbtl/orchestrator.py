"""DBTL orchestrator graph — Phase 1 skeleton.

A thin LangGraph state machine that drives one greenagent DBTL cycle:

    advance ──▶ (human_review) ──▶ transition ──▶ advance ──▶ ... ──▶ END

- ``advance``      : stub-produces the artifacts the next state requires, then
                     runs ``greenagent dbtl validate``.
- ``human_review`` : ``interrupt()`` for greenagent's three human-gated
                     transitions (approved-for-build / learning / completed).
                     Phase 1 deliberately rejects serialized resume payloads
                     until they can be resolved to a durable SQL review.
- ``transition``   : runs ``greenagent dbtl check-transition`` and, if allowed,
                     advances the cycle state.

Execution inside each stage is stubbed here (no LLM). Phase 2 replaces the stub
with role-scoped ``make_lead_agent`` subgraphs (bounded autonomy per stage).

This graph is registered as a run target via the ``dbtl_orchestrator``
assistant_id (see ``app/gateway/services.py::resolve_agent_factory``); the
default lead-agent path is untouched.
"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from deerflow.agents.dbtl.greenagent_cli import GreenAgentGate, SubprocessGreenAgentGate
from deerflow.agents.dbtl.state import (
    ARTIFACT_REQUIREMENTS,
    HAPPY_PATH,
    HUMAN_GATED_TARGETS,
    TERMINAL_STATE,
    DBTLState,
)

logger = logging.getLogger(__name__)


def _stub_produce_artifacts(state: DBTLState, target: str) -> dict[str, str]:
    """Phase 1 stub: 'produce' the artifacts *target* requires.

    Writes a placeholder JSON per required artifact under the cycle directory
    and returns the merged ``key -> relative path`` map. Idempotent: existing
    keys are left untouched. Real role agents replace this in Phase 2.
    """
    artifacts = dict(state.get("dbtl_artifacts") or {})
    project = Path(state["dbtl_project_path"])
    cycle_id = state["dbtl_cycle_id"]
    stub_dir = project / ".greenagent" / "dbtl-cycles" / cycle_id / "artifacts"
    for key in ARTIFACT_REQUIREMENTS.get(target, ()):
        if key in artifacts:
            continue
        rel = f".greenagent/dbtl-cycles/{cycle_id}/artifacts/{key}.json"
        try:
            stub_dir.mkdir(parents=True, exist_ok=True)
            (project / rel).write_text('{"stub": true}\n', encoding="utf-8")
        except OSError:
            logger.debug("stub artifact write skipped for %s", key, exc_info=True)
        artifacts[key] = rel
    return artifacts


def _parse_human_decision(_decision: object) -> tuple[bool, str]:
    """Fail closed until a resume is resolved to a durable SQL review.

    LangGraph resume values are serialized client input. Even a mapping with a
    plausible decision, reviewer id, and authorization reference can be forged
    or replayed, and this harness layer cannot verify its project membership,
    artifact revision, policy version, or single-use database record.

    Phase 1 therefore accepts no client-shaped decision here. The Gateway's
    authenticated review endpoint remains the authority; a later graph phase
    must inject a server-side resolver for those durable review records before
    this gate can advance.
    """
    return False, ""


def build_dbtl_graph(gate: GreenAgentGate) -> StateGraph:
    """Build (but do not compile) the DBTL orchestrator graph.

    ``gate`` is injected so tests can supply a scripted fake; production passes
    a :class:`SubprocessGreenAgentGate`.
    """

    def advance(state: DBTLState) -> dict:
        current = state["dbtl_state"]
        if current == TERMINAL_STATE:
            return {"dbtl_done": True}
        target = HAPPY_PATH.get(current)
        if target is None:
            # No known next state — treat as terminal rather than looping.
            return {"dbtl_done": True}

        artifacts = _stub_produce_artifacts(state, target)
        result = gate.validate(state["dbtl_project_path"], state["dbtl_cycle_id"])
        if not result.ok:
            return {
                "dbtl_artifacts": artifacts,
                "dbtl_error": f"validate failed at {current}: {'; '.join(result.errors)}",
                "messages": [AIMessage(content=f"DBTL validate blocked at {current}: {list(result.errors)}")],
            }
        return {"dbtl_artifacts": artifacts, "dbtl_pending_target": target}

    def route_after_advance(state: DBTLState) -> str:
        if state.get("dbtl_error"):
            return END
        if state.get("dbtl_done"):
            return END
        target = state.get("dbtl_pending_target")
        return "human_review" if target in HUMAN_GATED_TARGETS else "transition"

    def human_review(state: DBTLState) -> dict:
        target = state.get("dbtl_pending_target")
        decision = interrupt(
            {
                "type": "dbtl_review",
                "cycle_id": state["dbtl_cycle_id"],
                "from_state": state["dbtl_state"],
                "to_state": target,
                "prompt": (f"Approve DBTL transition {state['dbtl_state']} -> {target}? Reply with an authorization reference to proceed."),
            }
        )
        approved, authorization = _parse_human_decision(decision)
        if not approved or not authorization:
            return {
                "dbtl_authorization": None,
                "dbtl_error": f"durable human review resumption is not connected for {target}",
                "messages": [AIMessage(content=f"DBTL human gate is fail-closed for {target} until durable review binding is connected")],
            }
        return {"dbtl_authorization": authorization}

    def route_after_human(state: DBTLState) -> str:
        return END if state.get("dbtl_error") else "transition"

    def transition(state: DBTLState) -> dict:
        target = state.get("dbtl_pending_target")
        # Until a durable review resolver exists, this graph never claims human
        # authority. Human-gated targets route through ``human_review`` and stop
        # there; this coordinator identity is also a second fail-closed layer
        # for a manually constructed or legacy checkpoint.
        actor = "coordinator"
        authorization = ""
        result = gate.check_transition(
            state["dbtl_project_path"],
            state["dbtl_cycle_id"],
            target,
            actor=actor,
            authorization=authorization,
        )
        if not result.ok:
            return {
                # Authorization is single-attempt state. Clear legacy/stale
                # values even when the transition fails so a checkpoint retry
                # cannot reuse them.
                "dbtl_authorization": None,
                "dbtl_error": f"transition to {target} blocked: {'; '.join(result.errors)}",
                "messages": [AIMessage(content=f"DBTL transition blocked -> {target}: {list(result.errors)}")],
            }
        update = {
            "dbtl_state": target,
            "dbtl_pending_target": None,
            # Consume any legacy authorization on every transition attempt.
            "dbtl_authorization": None,
            "dbtl_history": [f"{state['dbtl_state']} -> {target}"],
            "messages": [AIMessage(content=f"DBTL advanced to {target}")],
        }
        if target == TERMINAL_STATE:
            update["dbtl_done"] = True
        return update

    def route_after_transition(state: DBTLState) -> str:
        if state.get("dbtl_error"):
            return END
        if state["dbtl_state"] == TERMINAL_STATE:
            return END
        return "advance"

    builder = StateGraph(DBTLState)
    builder.add_node("advance", advance)
    builder.add_node("human_review", human_review)
    builder.add_node("transition", transition)
    builder.add_edge(START, "advance")
    builder.add_conditional_edges("advance", route_after_advance, ["human_review", "transition", END])
    builder.add_conditional_edges("human_review", route_after_human, ["transition", END])
    builder.add_conditional_edges("transition", route_after_transition, ["advance", END])
    return builder


def make_dbtl_orchestrator(config: RunnableConfig):
    """LangGraph graph factory for the ``dbtl_orchestrator`` assistant_id.

    Signature mirrors ``make_lead_agent(config)`` so the Gateway run worker
    drives it identically. Checkpoint-channel mode is frozen/injected exactly
    as the lead agent does, keeping the runtime's checkpoint machinery
    consistent. The compiled graph carries no checkpointer (the runtime injects
    one), matching ``create_agent``.
    """
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

    return build_dbtl_graph(SubprocessGreenAgentGate()).compile()
