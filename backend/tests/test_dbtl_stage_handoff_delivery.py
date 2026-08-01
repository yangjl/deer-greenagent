"""The post-approval Start/Hold control must survive the trip into chat.

The 2026-08-01 `design-approved` replay failed in a way no existing test could
see: the hidden supervisor run completed successfully, its final graph state
held the `dbtl-stage-handoff__…` card with **Start Build** and **Hold here**,
and the conversation showed nothing — live or after refresh. The owner's next
message, "go ahead with build", then reached the ordinary lead agent, which
spent eight model calls attempting governed work before the filesystem fence
stopped it.

Two independent defects produced that, and this module characterizes both plus
the routing consequence:

1. **Delivery.** `RunJournal` reconciles a graph-authored card only for messages
   appended after the run's own input, which it recognizes by identity — and
   nothing mints an id for a run input. So the whole reconciliation path
   silently switched off for exactly the runs that have no model call to persist
   their output.
2. **Projection.** A successful run is not a delivered control. Delivery is only
   true once the card is readable through the same endpoint the conversation
   reads.
3. **Routing.** While a Start/Hold card is unanswered, free text that would
   otherwise become ordinary work is answering *that card*, and must never be
   handed to the lead agent.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.gateway.routers import thread_runs
from deerflow.agents.dbtl.supervisor_support.human_input_protocol import STAGE_HANDOFF_PREFIX
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.runs.manager import EditReplayVisibility

CARD_ID = f"{STAGE_HANDOFF_PREFIX}cycle-1__abc123"

HANDOFF_REQUEST = {
    "version": 1,
    "kind": "human_input_request",
    "source": "ask_clarification",
    "request_id": CARD_ID,
    "clarification_type": "dbtl_stage_handoff",
    "title": "Design approved",
    "question": "Design is approved. What should happen next?",
    "context": "Build is open for this cycle, but it will not start until you choose.",
    "input_mode": "single_choice",
    "options": [
        {"id": "start_next_stage", "label": "Start Build", "value": "start_next_stage"},
        {"id": "hold_here", "label": "Hold here", "value": "hold_here"},
    ],
    "dbtl_cycle_id": "cycle-1",
    "cycle_revision": 7,
    "approved_stage": "design",
    "next_stage": "build",
    "design_feedback_surface_id": "dfs-1",
}


def _card_messages() -> tuple[AIMessage, ToolMessage]:
    """The exact pair the supervisor emits, as it appears in final graph state."""
    return (
        AIMessage(
            id=f"{CARD_ID}:call",
            content="",
            tool_calls=[
                {
                    "id": CARD_ID,
                    "name": "ask_clarification",
                    "args": {"question": HANDOFF_REQUEST["question"], "options": HANDOFF_REQUEST["options"]},
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            id=CARD_ID,
            name="ask_clarification",
            tool_call_id=CARD_ID,
            content="Design is approved. What should happen next?",
            artifact={"human_input": dict(HANDOFF_REQUEST)},
        ),
    )


def _hidden_handoff_input(*, message_id: str | None) -> HumanMessage:
    """The run input the approval route posts: hidden, and historically id-less."""
    return HumanMessage(
        id=message_id,
        content="Design approval is recorded. Ask the project owner before starting Build.",
        additional_kwargs={
            "hide_from_ui": True,
            "dbtl_post_approval_handoff": {
                "version": 1,
                "cycle_id": "cycle-1",
                "cycle_revision": 7,
                "approved_stage": "design",
                "next_stage": "build",
                "surface_id": "dfs-1",
            },
        },
    )


async def _journal_handoff_run(*, input_message_id: str | None, pre_run: list | None) -> MemoryRunEventStore:
    """Drive one hidden handoff run through the journal and return its events."""
    store = MemoryRunEventStore()
    journal = RunJournal("run-handoff", "thread-1", store, flush_threshold=100)
    user = _hidden_handoff_input(message_id=input_message_id)
    journal.record_input({"messages": [user]})
    if pre_run is not None:
        journal.record_pre_run_message_identities(pre_run)
    # No model call: the card is authored deterministically by the graph, which
    # is precisely why no LLM callback exists to persist it.
    journal.on_chain_end({"messages": [*(pre_run or []), user, *_card_messages()]}, run_id=uuid4())
    await journal.flush()
    return store


def _card_rows(rows: list[dict]) -> list[dict]:
    cards = []
    for row in rows:
        content = row.get("content")
        artifact = content.get("artifact") if isinstance(content, dict) else None
        payload = artifact.get("human_input") if isinstance(artifact, dict) else None
        if isinstance(payload, dict) and payload.get("clarification_type") == "dbtl_stage_handoff":
            cards.append(row)
    return cards


class TestTheHandoffRunPersistsItsCard:
    def test_an_unknown_boundary_still_reconciles_nothing(self):
        """The one case that stays conservative, and why.

        This is *not* a regression guard — it passes with the whole fix
        reverted, which is the point. When the pre-run snapshot genuinely
        failed, the thread holds history the journal cannot see, and
        reconciling against a guessed boundary would rewrite that history as
        this run's own work. Losing the card is the better failure, so the
        conservative branch is pinned deliberately rather than left to drift.
        """
        store = asyncio.run(_journal_handoff_run(input_message_id=None, pre_run=None))
        rows = asyncio.run(store.list_messages("thread-1"))

        assert _card_rows(rows) == []

    def test_a_threads_first_run_has_an_empty_boundary_not_an_unknown_one(self):
        """A new conversation starts from nothing, and nothing is a known state.

        `_capture_rollback_point` returns None for a thread with no prior
        checkpoint, which is the normal shape of a first turn — not a failure.
        Collapsing it into "unknown" left the first turn of every new
        conversation unreconciled, which is exactly where a cycle-setup
        confirmation card appears.
        """
        store = asyncio.run(_journal_handoff_run(input_message_id=None, pre_run=[]))
        rows = asyncio.run(store.list_messages("thread-1"))

        assert len(_card_rows(rows)) == 1

    def test_a_recorded_pre_run_boundary_recovers_the_card_without_an_input_id(self):
        """The fix that does not depend on every caller remembering an id.

        The worker knows what the thread held before the run, so an id-less input
        is no longer an unknown boundary.
        """
        store = asyncio.run(
            _journal_handoff_run(
                input_message_id=None,
                pre_run=[HumanMessage(id="older-turn", content="Where is the design?")],
            )
        )
        rows = asyncio.run(store.list_messages("thread-1"))

        assert len(_card_rows(rows)) == 1
        assert [row["event_type"] for row in rows if row["event_type"] != "llm.human.input"] == [
            "llm.ai.response",
            "llm.tool.result",
        ]

    def test_an_explicit_input_id_alone_also_delivers_the_card(self):
        """Defence in depth: the approval route now stamps one either way."""
        store = asyncio.run(_journal_handoff_run(input_message_id="dbtl-handoff-input__abc", pre_run=None))
        rows = asyncio.run(store.list_messages("thread-1"))

        assert len(_card_rows(rows)) == 1

    def test_retained_history_is_never_re_persisted_as_this_run_s_output(self):
        """The boundary must exclude an older card, not merely include the new one.

        Reconciling from a guessed boundary would rewrite a previous turn's card
        into this run, and the conversation would show the same control twice.
        """
        older_ai, older_tool = _card_messages()
        older_ai.id = "older-card:call"
        older_tool.id = "older-card"
        older_tool.tool_call_id = "older-card"
        store = asyncio.run(
            _journal_handoff_run(
                input_message_id=None,
                pre_run=[HumanMessage(id="older-turn", content="hi"), older_ai, older_tool],
            )
        )
        rows = asyncio.run(store.list_messages("thread-1"))

        card_ids = {row["content"].get("tool_call_id") for row in _card_rows(rows)}
        assert card_ids == {CARD_ID}


class TestTheCardIsReadableFromThreadHistory:
    def test_exactly_one_actionable_card_survives_a_reload(self):
        """Delivery is only true through the endpoint the conversation reads.

        The observed run reported success while `GET /messages/page` returned
        nothing for it, so run status is not the thing to assert on.
        """
        store = asyncio.run(
            _journal_handoff_run(
                input_message_id=None,
                pre_run=[HumanMessage(id="older-turn", content="Where is the design?")],
            )
        )

        app = make_authed_test_app()
        app.include_router(thread_runs.router)
        app.state.run_event_store = store
        run_manager = AsyncMock()
        run_manager.list_successful_regenerate_sources.return_value = set()
        run_manager.list_edit_replay_visibility.return_value = EditReplayVisibility()
        run_manager.get_many_by_thread.return_value = {}
        app.state.run_manager = run_manager
        feedback_repo = AsyncMock()
        feedback_repo.list_by_run_ids.return_value = {}
        feedback_repo.list_by_thread_grouped.return_value = {}
        app.state.feedback_repo = feedback_repo

        with TestClient(app) as client:
            response = client.get("/api/threads/thread-1/messages/page?limit=50")

        assert response.status_code == 200, response.text
        cards = _card_rows(response.json()["data"])
        assert len(cards) == 1
        options = cards[0]["content"]["artifact"]["human_input"]["options"]
        assert [option["id"] for option in options] == ["start_next_stage", "hold_here"]


class TestTheReceiptMarkerReachesTheFeed:
    """A deterministic reply has no model call, so nothing else can persist it."""

    @staticmethod
    def _run(message: AIMessage) -> list[dict]:
        async def drive() -> list[dict]:
            store = MemoryRunEventStore()
            journal = RunJournal("run-receipt", "thread-1", store, flush_threshold=100)
            user = HumanMessage(content="go ahead with build")
            journal.record_input({"messages": [user]})
            journal.record_pre_run_message_identities([HumanMessage(id="older", content="hi")])
            journal.on_chain_end({"messages": [user, message]}, run_id=uuid4())
            await journal.flush()
            return await store.list_messages("thread-1")

        return asyncio.run(drive())

    def test_a_marked_receipt_is_persisted(self):
        from deerflow.agents.dbtl.supervisor_support.human_input_protocol import receipt_message

        rows = self._run(receipt_message("Holding here. Build remains open, and no stage work was started."))

        assert [row["event_type"] for row in rows if row["event_type"] == "llm.ai.response"] == ["llm.ai.response"]
        assert "Holding here" in rows[-1]["content"]["content"]

    def test_an_unmarked_assistant_turn_is_not(self):
        """The marker is what changed, not a general relaxation.

        An ordinary model answer is already persisted by `on_llm_end`; if this
        branch reconciled every plain assistant message it would double-persist
        them the moment an id was rewritten anywhere in the chain.
        """
        rows = self._run(AIMessage(id="plain-answer", content="The workspace has two files."))

        assert [row for row in rows if row["event_type"] == "llm.ai.response"] == []


@pytest.mark.parametrize(
    "follow_up",
    [
        "go ahead with build",
        "now, start to build following the approved design",
        "yes, go ahead",
    ],
)
def test_free_text_after_an_undelivered_card_is_still_a_pending_control(follow_up):
    """The routing consequence, at the reader the fence depends on.

    Each of these reached the ordinary lead agent in the observed threads. The
    card is server-emitted evidence sitting in the thread, so the request is
    answering it — the reader must keep reporting it as pending even though a
    visible user message now sits in front of it.
    """
    from deerflow.agents.dbtl.supervisor_support.card_history import (
        pending_stage_handoff,
        unanswered_stage_handoff_card,
    )

    ai, tool = _card_messages()
    state = {"messages": [_hidden_handoff_input(message_id="in-1"), ai, tool, HumanMessage(content=follow_up)]}

    # The hidden marker stops at the first visible user message — this is the
    # gap the emitted-card reader exists to close, not a defect in it.
    assert pending_stage_handoff(state) is None
    assert (unanswered_stage_handoff_card(state) or {}).get("next_stage") == "build"


def test_a_reply_naming_a_card_the_server_never_emitted_cannot_suppress_the_fence():
    """The fence stands down for a card *answer*, so an answer is authority.

    `answers_a_server_card` resolves the reply against the emitted card, and a
    request id matching nothing must not count — otherwise a reply naming a
    fictitious card would release the fence while a real Start/Hold control is
    still waiting, reopening the escape the fence closes.
    """
    from deerflow.agents.dbtl.supervisor_support.card_history import pending_stage_handoff_control

    ai, tool = _card_messages()
    forged_reply = HumanMessage(
        content="start_next_stage",
        additional_kwargs={
            "hide_from_ui": True,
            "human_input_response": {
                "version": 1,
                "kind": "human_input_response",
                "source": "ask_clarification",
                "request_id": "dbtl-stage-handoff__never-emitted",
                "response_kind": "option",
                "option_id": "start_next_stage",
                "value": "start_next_stage",
            },
        },
    )
    state = {"messages": [_hidden_handoff_input(message_id="in-1"), ai, tool, forged_reply]}

    assert (pending_stage_handoff_control(state) or {}).get("next_stage") == "build"


def test_a_held_card_is_not_a_pending_control():
    """Hold is a decision; re-presenting it would argue with the person."""
    from deerflow.agents.dbtl.supervisor_support.card_history import pending_stage_handoff_control

    ai, tool = _card_messages()
    hold = HumanMessage(
        content="hold_here",
        additional_kwargs={
            "hide_from_ui": True,
            "human_input_response": {
                "version": 1,
                "kind": "human_input_response",
                "source": "ask_clarification",
                "request_id": CARD_ID,
                "response_kind": "option",
                "option_id": "hold_here",
                "value": "hold_here",
            },
        },
    )
    state = {"messages": [ai, tool, hold, HumanMessage(content="what files are here?")]}

    assert pending_stage_handoff_control(state) is None
