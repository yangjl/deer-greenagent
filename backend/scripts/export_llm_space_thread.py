#!/usr/bin/env python3
"""Export DeerFlow message rows as a native LLM Space Thread JSON file.

The Gateway persists assistant tool requests and tool results as separate
LangChain-shaped messages. LLM Space keeps a tool result inside the originating
assistant ``toolCall``. This exporter joins those records by ``tool_call_id`` so
the resulting Thread can be inspected and replayed in LLM Space.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://localhost:2026"
DEFAULT_SYSTEM_PROMPT = """You are debugging a replay exported from DeerFlow.

Treat the imported messages, tool arguments, and tool results as captured
evidence. Imported function tools describe calls observed in DeerFlow but do
not have an automatic execution backend in LLM Space. Change one variable at a
time, replay from the message under investigation, and compare the new behavior
with the captured result."""

SAMPLE_ROWS: list[dict[str, Any]] = [
    {
        "seq": 1,
        "thread_id": "sample-thread",
        "run_id": "sample-run",
        "event_type": "llm.human.input",
        "category": "message",
        "content": {
            "type": "human",
            "id": "sample-human",
            "content": "Read the repository instructions and summarize the first rule.",
            "additional_kwargs": {},
        },
        "metadata": {"caller": "lead_agent"},
    },
    {
        "seq": 2,
        "thread_id": "sample-thread",
        "run_id": "sample-run",
        "event_type": "llm.ai.response",
        "category": "message",
        "content": {
            "type": "ai",
            "id": "sample-tool-request",
            "content": "I will inspect AGENTS.md first.",
            "additional_kwargs": {},
            "tool_calls": [
                {
                    "id": "call-read-agents",
                    "name": "read_file",
                    "args": {"path": "/Users/example/deer-flow/AGENTS.md"},
                    "type": "tool_call",
                }
            ],
        },
        "metadata": {"caller": "lead_agent"},
    },
    {
        "seq": 3,
        "thread_id": "sample-thread",
        "run_id": "sample-run",
        "event_type": "llm.tool.result",
        "category": "message",
        "content": {
            "type": "tool",
            "id": "sample-tool-result",
            "content": "# AGENTS.md\nFollow repository instructions.",
            "tool_call_id": "call-read-agents",
            "name": "read_file",
            "status": "success",
            "additional_kwargs": {},
        },
        "metadata": {},
    },
    {
        "seq": 4,
        "thread_id": "sample-thread",
        "run_id": "sample-run",
        "event_type": "llm.ai.response",
        "category": "message",
        "content": {
            "type": "ai",
            "id": "sample-final",
            "content": "The first rule is to follow the repository instructions before changing code.",
            "additional_kwargs": {},
            "tool_calls": [],
        },
        "metadata": {"caller": "lead_agent"},
    },
]


class ExportError(RuntimeError):
    """Raised when DeerFlow data cannot be exported safely."""


def build_llm_space_thread(
    rows: Iterable[Mapping[str, Any]],
    *,
    title: str,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> dict[str, Any]:
    """Convert ordered DeerFlow message rows into one native LLM Space Thread."""
    ordered_rows = sorted(rows, key=_sequence_key)
    messages: list[dict[str, Any]] = []
    tool_calls_by_id: dict[str, dict[str, Any]] = {}
    observed_arguments: dict[str, list[dict[str, Any]]] = {}

    for index, row in enumerate(ordered_rows):
        content = row.get("content")
        if not isinstance(content, Mapping):
            continue
        if _is_hidden(content):
            continue

        message_type = _message_type(row, content)
        if message_type == "human":
            messages.append(
                {
                    "id": _message_id(content, row, index),
                    "role": "user",
                    "content": _content_blocks(content.get("content")),
                }
            )
            continue

        if message_type == "ai":
            caller = str((row.get("metadata") or {}).get("caller", "lead_agent"))
            if caller != "lead_agent":
                continue
            assistant_message: dict[str, Any] = {
                "id": _message_id(content, row, index),
                "role": "assistant",
                "content": _content_blocks(content.get("content")),
            }
            exported_tool_calls: list[dict[str, Any]] = []
            raw_tool_calls = content.get("tool_calls")
            if isinstance(raw_tool_calls, list):
                for call_index, raw_tool_call in enumerate(raw_tool_calls):
                    normalized = _normalize_tool_call(
                        raw_tool_call,
                        fallback_id=f"{assistant_message['id']}-tool-{call_index + 1}",
                    )
                    if normalized is None:
                        continue
                    tool_call = {
                        "id": normalized["id"],
                        "input": {
                            "name": normalized["name"],
                            "arguments": normalized["arguments"],
                        },
                    }
                    exported_tool_calls.append(tool_call)
                    tool_calls_by_id[normalized["id"]] = tool_call
                    observed_arguments.setdefault(normalized["name"], []).append(normalized["arguments"])
            if exported_tool_calls:
                assistant_message["toolCalls"] = exported_tool_calls
            messages.append(assistant_message)
            continue

        if message_type == "tool":
            tool_call_id = content.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                continue
            tool_call = tool_calls_by_id.get(tool_call_id)
            if tool_call is None:
                continue
            tool_call["output"] = {
                "content": _content_blocks(content.get("content")),
                "isError": _is_error_tool_result(content),
            }

    tools = [
        {
            "type": "function",
            "name": name,
            "description": f"Imported DeerFlow tool observed in the captured run: {name}.",
            "parameters": _infer_arguments_schema(argument_samples),
        }
        for name, argument_samples in sorted(observed_arguments.items())
    ]
    return {
        "title": title,
        "context": {
            "systemPrompt": system_prompt,
            "tools": tools,
            "messages": messages,
        },
    }


def fetch_deerflow_messages(
    base_url: str,
    *,
    thread_id: str,
    run_id: str | None = None,
    headers: Mapping[str, str] | None = None,
    opener: Callable[..., Any] = urlopen,
) -> list[dict[str, Any]]:
    """Fetch every message page for a DeerFlow thread or one run."""
    escaped_thread_id = quote(thread_id, safe="")
    if run_id:
        escaped_run_id = quote(run_id, safe="")
        endpoint = f"/api/threads/{escaped_thread_id}/runs/{escaped_run_id}/messages"
    else:
        endpoint = f"/api/threads/{escaped_thread_id}/messages/page"

    collected_descending_pages: list[list[dict[str, Any]]] = []
    before_seq: int | None = None
    prior_cursor: int | None = None

    for _page in range(10_000):
        query: dict[str, Any] = {"limit": 200}
        if before_seq is not None:
            query["before_seq"] = before_seq
        url = f"{base_url.rstrip('/')}{endpoint}?{urlencode(query)}"
        request_headers = {"Accept": "application/json", **dict(headers or {})}
        request = Request(url, headers=request_headers)
        payload = _read_json_response(request, opener=opener)
        if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
            raise ExportError(f"Unexpected DeerFlow message response from {url}")

        page = [dict(row) for row in payload["data"] if isinstance(row, Mapping)]
        collected_descending_pages.append(page)
        if not payload.get("has_more"):
            break
        seqs = [row.get("seq") for row in page if isinstance(row.get("seq"), int)]
        if not seqs:
            raise ExportError("DeerFlow reported more messages but returned no sequence cursor")
        before_seq = min(seqs)
        if before_seq == prior_cursor:
            raise ExportError("DeerFlow message pagination did not advance")
        prior_cursor = before_seq
    else:
        raise ExportError("DeerFlow message export exceeded the pagination safety limit")

    rows: list[dict[str, Any]] = []
    for page in reversed(collected_descending_pages):
        rows.extend(page)
    return sorted(rows, key=_sequence_key)


def load_message_rows(path: Path) -> list[dict[str, Any]]:
    """Load a saved Gateway message list or paginated response."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"Could not read DeerFlow message JSON from {path}: {exc}") from exc
    if isinstance(payload, Mapping):
        payload = payload.get("data")
    if not isinstance(payload, list):
        raise ExportError("Input JSON must be a message array or an object containing a data array")
    return [dict(row) for row in payload if isinstance(row, Mapping)]


def write_thread_file(payload: Mapping[str, Any], output: Path, *, force: bool = False) -> None:
    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if force:
        output.write_text(serialized, encoding="utf-8")
        return
    try:
        with output.open("x", encoding="utf-8") as file:
            file.write(serialized)
    except FileExistsError as exc:
        raise ExportError(f"Output already exists: {output}. Pass --force to replace it.") from exc


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    output = Path(args.output).expanduser()
    system_prompt = _load_system_prompt(args.system_prompt_file)

    if args.sample:
        rows = SAMPLE_ROWS
        title = args.title or "DeerFlow exporter smoke test"
        source = "built-in sample"
    elif args.input_file:
        rows = load_message_rows(Path(args.input_file).expanduser())
        title = args.title or Path(args.input_file).stem
        source = str(Path(args.input_file).expanduser())
    else:
        headers = _authentication_headers()
        rows = fetch_deerflow_messages(
            args.base_url,
            thread_id=args.thread_id,
            run_id=args.run_id,
            headers=headers,
        )
        title = args.title or f"DeerFlow {args.run_id or args.thread_id}"
        source = f"{args.base_url.rstrip('/')} thread={args.thread_id}"
        if args.run_id:
            source += f" run={args.run_id}"

    payload = build_llm_space_thread(rows, title=title, system_prompt=system_prompt)
    write_thread_file(payload, output, force=args.force)
    message_count = len(payload["context"]["messages"])
    tool_count = len(payload["context"]["tools"])
    print(f"Exported {message_count} messages and {tool_count} observed tools from {source} to {output}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export DeerFlow messages to a native LLM Space Thread JSON file.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sample", action="store_true", help="Export a built-in four-message smoke-test run.")
    source.add_argument("--input-file", help="Read a saved DeerFlow message array or {data: [...]} response.")
    source.add_argument("--thread-id", help="Fetch this thread from the DeerFlow Gateway.")
    parser.add_argument("--run-id", help="Limit a live --thread-id export to one run.")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("DEERFLOW_URL", DEFAULT_BASE_URL),
        help=f"DeerFlow public base URL (default: DEERFLOW_URL or {DEFAULT_BASE_URL}).",
    )
    parser.add_argument("--output", required=True, help="Destination LLM Space .json Thread file.")
    parser.add_argument("--title", help="Thread title; defaults from the selected source.")
    parser.add_argument("--system-prompt-file", help="Optional text file replacing the replay-oriented system prompt.")
    parser.add_argument("--force", action="store_true", help="Replace an existing output file.")
    return parser


def _authentication_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    bearer = os.environ.get("DEERFLOW_BEARER_TOKEN")
    cookie = os.environ.get("DEERFLOW_COOKIE")
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _load_system_prompt(path: str | None) -> str:
    if path is None:
        return DEFAULT_SYSTEM_PROMPT
    prompt_path = Path(path).expanduser()
    try:
        return prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExportError(f"Could not read system prompt from {prompt_path}: {exc}") from exc


def _read_json_response(request: Request, *, opener: Callable[..., Any]) -> Any:
    try:
        with opener(request, timeout=30) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ExportError(f"DeerFlow returned HTTP {exc.code} for {request.full_url}: {detail}") from exc
    except URLError as exc:
        raise ExportError(f"Could not reach DeerFlow at {request.full_url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ExportError(f"DeerFlow returned invalid JSON for {request.full_url}") from exc


def _sequence_key(row: Mapping[str, Any]) -> tuple[int, int]:
    seq = row.get("seq")
    if isinstance(seq, int):
        return (0, seq)
    return (1, 0)


def _message_type(row: Mapping[str, Any], content: Mapping[str, Any]) -> str:
    value = content.get("type")
    if value in {"human", "user"}:
        return "human"
    if value in {"ai", "assistant"}:
        return "ai"
    if value == "tool":
        return "tool"
    event_type = row.get("event_type")
    if event_type in {"llm.human.input", "human_message"}:
        return "human"
    if event_type in {"llm.ai.response", "ai_message"}:
        return "ai"
    if event_type in {"llm.tool.result", "tool_message"}:
        return "tool"
    return ""


def _message_id(content: Mapping[str, Any], row: Mapping[str, Any], index: int) -> str:
    message_id = content.get("id")
    if isinstance(message_id, str) and message_id:
        return message_id
    seq = row.get("seq")
    return f"deerflow-message-{seq if isinstance(seq, int) else index + 1}"


def _is_hidden(content: Mapping[str, Any]) -> bool:
    additional_kwargs = content.get("additional_kwargs")
    return isinstance(additional_kwargs, Mapping) and additional_kwargs.get("hide_from_ui") is True


def _content_blocks(value: Any) -> list[dict[str, Any]]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    if isinstance(value, list):
        blocks: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                blocks.append({"type": "text", "text": item})
            elif isinstance(item, Mapping) and item.get("type") == "text":
                blocks.append({"type": "text", "text": str(item.get("text", ""))})
            elif isinstance(item, Mapping) and item.get("type") == "image_data":
                blocks.append(dict(item))
            else:
                blocks.append({"type": "text", "text": json.dumps(item, ensure_ascii=False, default=str)})
        return blocks
    return [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}]


def _normalize_tool_call(raw: Any, *, fallback_id: str) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    input_value = raw.get("input")
    function_value = raw.get("function")
    if isinstance(input_value, Mapping):
        name = input_value.get("name")
        arguments = input_value.get("arguments", {})
    elif isinstance(function_value, Mapping):
        name = function_value.get("name")
        arguments = function_value.get("arguments", {})
    else:
        name = raw.get("name")
        arguments = raw.get("args", raw.get("arguments", {}))
    if not isinstance(name, str) or not name:
        return None
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"_partial_arguments": arguments}
    if not isinstance(arguments, Mapping):
        arguments = {"value": arguments}
    tool_call_id = raw.get("id")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        tool_call_id = fallback_id
    return {
        "id": tool_call_id,
        "name": name,
        "arguments": dict(arguments),
    }


def _is_error_tool_result(content: Mapping[str, Any]) -> bool:
    status = content.get("status")
    if isinstance(status, str) and status.lower() == "error":
        return True
    if content.get("is_error") is True or content.get("isError") is True:
        return True
    additional_kwargs = content.get("additional_kwargs")
    return isinstance(additional_kwargs, Mapping) and (additional_kwargs.get("is_error") is True or additional_kwargs.get("isError") is True)


def _infer_arguments_schema(samples: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, dict[str, Any]] = {}
    required: set[str] | None = None
    for sample in samples:
        sample_keys = set(sample)
        required = sample_keys if required is None else required & sample_keys
        for key, value in sample.items():
            properties.setdefault(key, _infer_value_schema(value))
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": True,
    }
    if required:
        schema["required"] = sorted(required)
    return schema


def _infer_value_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        schema: dict[str, Any] = {"type": "array"}
        if value:
            schema["items"] = _infer_value_schema(value[0])
        return schema
    if isinstance(value, Mapping):
        return {
            "type": "object",
            "properties": {str(key): _infer_value_schema(item) for key, item in value.items()},
            "additionalProperties": True,
        }
    if value is None:
        return {"type": "null"}
    return {}


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
