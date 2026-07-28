"""A half-streamed thinking block must not brick a conversation forever.

Anthropic streams extended thinking as a content block: ``content_block_start``
announces ``{"type": "thinking"}`` and the text arrives afterwards as
``thinking_delta`` events. If the stream ends between those — a cancelled run, a
provider error, a 400 from something else in the same request — the accumulated
``AIMessage`` is checkpointed carrying a thinking block with no ``thinking``
field.

Every later turn in that thread then replays it and is rejected with

    messages.N.content.0.thinking.thinking: Field required

which nothing in the thread can recover from, because the damage is durable and
the request that hits it is unrelated to the one that caused it. One interrupted
stream permanently ends a conversation.

This is the same class of damage as a dangling tool call — an interrupted run
leaving history a strict provider refuses — so it is repaired in the same place
and the same way: in the **model-bound request only**, leaving the checkpoint
untouched.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.dangling_tool_call_middleware import (
    DanglingToolCallMiddleware,
)


def _request(messages):
    request = MagicMock()
    request.messages = list(messages)

    def override(messages=None, **_kwargs):
        replacement = MagicMock()
        replacement.messages = messages if messages is not None else request.messages
        replacement.override = override
        return replacement

    request.override = override
    return request


def _run(messages):
    """Return the messages the model would actually be sent."""
    captured: list = []

    def handler(req):
        captured.append(req)
        return MagicMock()

    DanglingToolCallMiddleware().wrap_model_call(_request(messages), handler)
    return captured[0].messages


def _ai(content, *, tool_calls=None) -> AIMessage:
    return AIMessage(id="ai-1", content=content, tool_calls=tool_calls or [])


class TestDroppingTheDamage:
    def test_a_thinking_block_with_no_text_is_dropped(self):
        messages = _run(
            [
                HumanMessage(content="hi"),
                _ai([{"type": "thinking", "index": 0}, {"type": "text", "text": "Here you go."}]),
            ]
        )

        blocks = messages[-1].content
        assert all(block.get("type") != "thinking" for block in blocks)
        assert {"type": "text", "text": "Here you go."} in blocks

    def test_an_empty_thinking_string_is_dropped(self):
        messages = _run([_ai([{"type": "thinking", "thinking": "   ", "signature": "s"}, {"type": "text", "text": "ok"}])])

        assert all(block.get("type") != "thinking" for block in messages[-1].content)

    def test_a_redacted_block_with_no_data_is_dropped(self):
        messages = _run([_ai([{"type": "redacted_thinking"}, {"type": "text", "text": "ok"}])])

        assert all(block.get("type") != "redacted_thinking" for block in messages[-1].content)


class TestPreservingWhatIsValid:
    def test_a_complete_thinking_block_survives_untouched(self):
        """Dropping a *valid* thinking block would break signature validation.

        Anthropic verifies the signature on a replayed thinking block, so the
        repair has to be surgical: only structurally unusable blocks go.
        """
        block = {"type": "thinking", "thinking": "Let me work through this.", "signature": "abc123"}
        messages = _run([_ai([block, {"type": "text", "text": "Done."}])])

        assert messages[-1].content[0] == block

    def test_plain_string_content_is_untouched(self):
        messages = _run([_ai("just text")])

        assert messages[-1].content == "just text"

    def test_a_message_with_nothing_wrong_is_not_copied(self):
        original = _ai([{"type": "text", "text": "fine"}])
        messages = _run([HumanMessage(content="hi"), original])

        # Identity, not equality: rebuilding every message on every model call
        # would churn ids and defeat prefix caching.
        assert messages[-1] is original

    def test_a_human_message_is_never_rewritten(self):
        original = HumanMessage(content=[{"type": "thinking"}])
        messages = _run([original])

        assert messages[0] is original


class TestNotCreatingANewProblem:
    def test_a_message_left_with_no_content_keeps_a_placeholder(self):
        """An assistant turn serialized with empty content is also a 400.

        Trading "malformed thinking block" for "empty assistant message" would
        move the rejection rather than remove it.
        """
        messages = _run([HumanMessage(content="hi"), _ai([{"type": "thinking", "index": 0}])])

        content = messages[-1].content
        assert content
        assert any(str(block.get("text", "")).strip() for block in content)

    def test_an_emptied_message_that_still_has_tool_calls_needs_no_placeholder(self):
        """The tool_use block carries the turn, so no filler text is required."""
        messages = _run(
            [
                HumanMessage(content="hi"),
                _ai(
                    [{"type": "thinking", "index": 0}],
                    tool_calls=[{"name": "bash", "args": {"command": "ls"}, "id": "call_1"}],
                ),
            ]
        )

        # The dangling-tool-call pass appends its own placeholder result after
        # the assistant turn, so the message under test is not the last one.
        assistant = next(message for message in messages if getattr(message, "type", None) == "ai")
        assert assistant.content == []
        assert assistant.tool_calls

    def test_the_repair_does_not_touch_the_checkpoint(self):
        """The middleware rewrites the request; state is left alone.

        A checkpoint rewrite would be a durable mutation of recorded history,
        which is a much larger claim than "this provider will not accept it".
        """
        original = _ai([{"type": "thinking", "index": 0}, {"type": "text", "text": "ok"}])
        state_content = list(original.content)

        _run([original])

        assert original.content == state_content
