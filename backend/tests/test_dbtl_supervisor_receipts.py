"""Every deterministic supervisor reply has to survive the run that wrote it.

These replies are authored by the graph with no model call behind them, so no
LLM callback exists to persist them: without the server-owned
``deerflow_graph_receipt`` marker they live in the checkpoint alone — visible
while the run streams, gone on refresh, and absent from the thread's durable
feed entirely.

That is not a cosmetic loss. Someone answered the Design setup questions and
the conversation went silent: the reply telling them where to review the Design
was written, held in graph state, and never saved. From the chat side it looked
like the cycle had stalled.

`receipt_message` is the established way to author one, and the rule this file
pins is that it is the *only* way — a bare ``AIMessage`` from one of these nodes
is a message nobody will ever read.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from deerflow.agents.dbtl.supervisor_support.human_input_protocol import receipt_message
from deerflow.runtime.journal import GRAPH_RECEIPT_KEY

SUPERVISOR = Path(__file__).resolve().parents[1] / "packages/harness/deerflow/agents/dbtl/supervisor.py"


class TestAReceiptIsMarkedAndIdentified:
    def test_it_carries_the_server_owned_marker(self) -> None:
        message = receipt_message("Holding here.")

        assert message.additional_kwargs.get(GRAPH_RECEIPT_KEY) is True

    def test_it_mints_its_own_id(self) -> None:
        # Reconciliation identifies a message by id and skips one that has none,
        # so leaving the id to the reducer would make durable delivery depend on
        # a framework detail.
        assert receipt_message("Holding here.").id


class TestNoSupervisorNodeAuthorsAnUnmarkedReply:
    """The bug, stated so it cannot come back by a different route.

    A bare ``AIMessage(content=...)`` returned from a supervisor node is a
    deterministic reply with no model call behind it, which is exactly the shape
    the marker exists for. Rather than pin the three sites that were wrong, this
    reads the module and refuses any of them.

    The check mirrors ``RunJournal._should_reconcile``: a graph-authored
    assistant turn is persisted when it carries the marker *or* an allowlisted
    tool call (``ask_clarification`` / ``present_files``), so an ``AIMessage``
    built with ``tool_calls`` is already covered and is not an offender.
    """

    @staticmethod
    def _bare_ai_message_lines() -> list[int]:
        tree = ast.parse(SUPERVISOR.read_text(encoding="utf-8"))
        lines: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name != "AIMessage":
                continue
            keywords = {keyword.arg for keyword in node.keywords}
            if not keywords & {"additional_kwargs", "tool_calls"}:
                lines.append(node.lineno)
        return lines

    def test_the_supervisor_builds_no_unmarked_assistant_turn(self) -> None:
        offenders = self._bare_ai_message_lines()

        assert not offenders, f"supervisor.py authors an unmarked AIMessage at line(s) {offenders}; use receipt_message() so the reply is persisted."


@pytest.mark.parametrize(
    "content",
    [
        "Recorded your design inputs for this cycle.",
        "No cycle was created.",
    ],
)
def test_a_setup_acknowledgement_is_a_receipt(content: str) -> None:
    message = receipt_message(content)

    assert message.additional_kwargs.get(GRAPH_RECEIPT_KEY) is True
    assert message.content == content


class TestTheAcknowledgementNamesAnActionThatExists:
    """A cycle created from chat has no design package, and cannot get one here.

    Automatic Design-meeting kickoff is written only by the project-rail
    creation endpoints, so a chat-started cycle reaches `design` with zero
    worker runs and zero artifacts. The acknowledgement told its owner to
    "review the Design stage and submit it for approval" -- and
    `submit_stage_for_review` refuses exactly that with "Stage 'design' has no
    artifact to review; attach evidence first."

    So the one sentence standing between the owner and an apparently dead cycle
    pointed at a guaranteed refusal, and never mentioned the step that actually
    produces a package. Restoring its delivery is not enough if what it says
    cannot be done.
    """

    REQUEST_ID = "dbtl-setup__project-1__abc123"

    def _state(self) -> dict:
        from langchain_core.messages import HumanMessage, ToolMessage

        return {
            "messages": [
                ToolMessage(
                    content="Answer these.",
                    name="ask_clarification",
                    tool_call_id=self.REQUEST_ID,
                    artifact={"human_input": {"request_id": self.REQUEST_ID, "source": "ask_clarification"}},
                ),
                HumanMessage(
                    content="accept all",
                    additional_kwargs={
                        "hide_from_ui": True,
                        "human_input_response": {
                            "version": 1,
                            "kind": "human_input_response",
                            "source": "ask_clarification",
                            "request_id": self.REQUEST_ID,
                            "response_kind": "text",
                            "value": "accept all",
                        },
                    },
                ),
            ]
        }

    def _text(self) -> str:
        from deerflow.agents.dbtl.supervisor import _design_inputs_acknowledgement

        text = _design_inputs_acknowledgement(self._state())
        assert text is not None
        return text

    def test_it_does_not_send_the_owner_to_a_refusal(self) -> None:
        # "submit it for approval" is precisely what the repository refuses
        # while the stage holds no artifact.
        assert "submit" not in self._text().lower()

    def test_it_says_the_design_meeting_has_not_run(self) -> None:
        assert "meeting" in self._text().lower()

    def test_it_names_how_to_run_one(self) -> None:
        # The composer's per-request cycle scope is the only way to reach Design
        # from here, so an acknowledgement that omits it leaves the owner stuck.
        lowered = self._text().lower()
        assert "cycle selected" in lowered or "select this cycle" in lowered
