from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from scripts.export_llm_space_thread import (
    SAMPLE_ROWS,
    ExportError,
    build_llm_space_thread,
    fetch_deerflow_messages,
    main,
)


def test_sample_conversion_pairs_tool_results_and_infers_function_schema():
    thread = build_llm_space_thread(
        SAMPLE_ROWS,
        title="DeerFlow exporter smoke test",
        system_prompt="You are replaying a DeerFlow run.",
    )

    assert thread["title"] == "DeerFlow exporter smoke test"
    assert thread["context"]["systemPrompt"] == "You are replaying a DeerFlow run."
    assert [message["role"] for message in thread["context"]["messages"]] == [
        "user",
        "assistant",
        "assistant",
    ]

    tool = thread["context"]["tools"][0]
    assert tool["type"] == "function"
    assert tool["name"] == "read_file"
    assert tool["parameters"]["properties"]["path"]["type"] == "string"
    assert tool["parameters"]["required"] == ["path"]

    tool_call = thread["context"]["messages"][1]["toolCalls"][0]
    assert tool_call["id"] == "call-read-agents"
    assert tool_call["input"] == {
        "name": "read_file",
        "arguments": {"path": "/Users/example/deer-flow/AGENTS.md"},
    }
    assert tool_call["output"]["content"] == [
        {
            "type": "text",
            "text": "# AGENTS.md\nFollow repository instructions.",
        }
    ]
    assert tool_call["output"]["isError"] is False


def test_conversion_skips_internal_ai_and_marks_failed_tool_results():
    rows = [
        _row(
            1,
            "llm.human.input",
            {"type": "human", "id": "human-1", "content": "Run a check."},
        ),
        _row(
            2,
            "llm.ai.response",
            {
                "type": "ai",
                "id": "internal-1",
                "content": "hidden middleware output",
            },
            caller="middleware:title",
        ),
        _row(
            3,
            "llm.ai.response",
            {
                "type": "ai",
                "id": "assistant-1",
                "content": "I will run the check.",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "bash",
                        "args": {"command": "false", "timeout": 1000},
                    }
                ],
            },
        ),
        _row(
            4,
            "llm.tool.result",
            {
                "type": "tool",
                "id": "tool-1",
                "tool_call_id": "call-1",
                "content": "exit code 1",
                "status": "error",
            },
        ),
    ]

    thread = build_llm_space_thread(rows, title="failed check")

    assert [message["id"] for message in thread["context"]["messages"]] == [
        "human-1",
        "assistant-1",
    ]
    tool_call = thread["context"]["messages"][1]["toolCalls"][0]
    assert tool_call["output"]["isError"] is True
    assert {tool["name"] for tool in thread["context"]["tools"]} == {"bash"}
    bash_schema = thread["context"]["tools"][0]["parameters"]
    assert bash_schema["properties"]["timeout"]["type"] == "integer"


def test_fetch_deerflow_messages_paginates_oldest_to_newest():
    requested_urls: list[str] = []

    def opener(request, timeout):
        assert timeout == 30
        requested_urls.append(request.full_url)
        query = parse_qs(urlparse(request.full_url).query)
        before_seq = query.get("before_seq")
        if before_seq is None:
            payload = {
                "data": [
                    _row(3, "llm.ai.response", {"type": "ai", "id": "a-1", "content": "answer"}),
                    _row(4, "llm.tool.result", {"type": "tool", "id": "t-1", "content": "done", "tool_call_id": "c-1"}),
                ],
                "has_more": True,
            }
        else:
            assert before_seq == ["3"]
            payload = {
                "data": [
                    _row(1, "llm.human.input", {"type": "human", "id": "h-1", "content": "question"}),
                    _row(
                        2,
                        "llm.ai.response",
                        {
                            "type": "ai",
                            "id": "a-0",
                            "content": "",
                            "tool_calls": [{"id": "c-1", "name": "check", "args": {}}],
                        },
                    ),
                ],
                "has_more": False,
            }
        return _Response(payload)

    rows = fetch_deerflow_messages(
        "http://localhost:2026",
        thread_id="thread/with spaces",
        run_id="run-1",
        headers={"Cookie": "session=redacted"},
        opener=opener,
    )

    assert [row["seq"] for row in rows] == [1, 2, 3, 4]
    assert len(requested_urls) == 2
    assert "/api/threads/thread%2Fwith%20spaces/runs/run-1/messages" in requested_urls[0]


def test_cli_sample_writes_native_thread_and_refuses_accidental_overwrite(tmp_path: Path):
    output = tmp_path / "sample.json"

    assert main(["--sample", "--output", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["title"] == "DeerFlow exporter smoke test"
    assert payload["context"]["messages"][1]["toolCalls"][0]["output"]["isError"] is False

    with pytest.raises(ExportError, match="already exists"):
        main(["--sample", "--output", str(output)])

    assert main(["--sample", "--output", str(output), "--force"]) == 0


def _row(seq: int, event_type: str, content: dict, *, caller: str = "lead_agent") -> dict:
    return {
        "seq": seq,
        "thread_id": "thread-1",
        "run_id": "run-1",
        "event_type": event_type,
        "category": "message",
        "content": content,
        "metadata": {"caller": caller},
    }


class _Response:
    def __init__(self, payload: object):
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self._payload
