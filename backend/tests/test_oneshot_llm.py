import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from langgraph.constants import TAG_NOSTREAM

from deerflow.utils import oneshot_llm


def test_run_oneshot_llm_is_hidden_from_parent_message_stream(monkeypatch):
    """Internal one-shot prompts and replies must never become chat messages."""
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock(return_value=SimpleNamespace(content='{"questions": []}'))
    monkeypatch.setattr(
        oneshot_llm,
        "create_chat_model",
        MagicMock(return_value=fake_model),
    )

    result = asyncio.run(
        oneshot_llm.run_oneshot_llm(
            system_instruction="Reply with JSON only.",
            user_content="Draft private setup questions.",
            run_name="dbtl_setup_questions",
            app_config=object(),
        )
    )

    assert result == '{"questions": []}'
    invoke_config = fake_model.ainvoke.await_args.kwargs["config"]
    assert TAG_NOSTREAM in invoke_config["tags"]
