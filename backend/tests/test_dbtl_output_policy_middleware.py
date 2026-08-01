from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from deerflow.agents.middlewares.dbtl_output_policy_middleware import (
    DbtlOutputPolicyMiddleware,
    is_dbtl_owned_path,
)


def _request(name: str, args: dict):
    return SimpleNamespace(
        tool_call={"name": name, "id": "call-1", "args": args},
        runtime=SimpleNamespace(context={}),
    )


@pytest.mark.parametrize(
    "path",
    [
        "outputs/dbtl/cycle-1/build/result.md",
        "/mnt/user-data/workspace/outputs/dbtl/cycle-1/test/result.json",
        "./outputs/dbtl/file",
    ],
)
def test_recognizes_governed_paths(path: str) -> None:
    assert is_dbtl_owned_path(path)


def test_blocks_path_aware_writes_and_never_calls_the_tool() -> None:
    called = False

    def handler(_request):
        nonlocal called
        called = True
        return ToolMessage(content="wrote", tool_call_id="call-1")

    result = DbtlOutputPolicyMiddleware().wrap_tool_call(
        _request("write_file", {"path": "/mnt/user-data/workspace/outputs/dbtl/cycle/build/file"}),
        handler,
    )

    assert not called
    assert "read-only" in str(result.content)


@pytest.mark.parametrize(
    "command",
    [
        "cp /tmp/result outputs/dbtl/cycle/build/result",
        "echo ok > /mnt/user-data/workspace/outputs/dbtl/cycle/build/result",
        "touch /mnt/user-data/outputs/dbtl/cycle/build/result",
        "cd outputs/dbtl && mv /tmp/result .",
    ],
)
def test_blocks_direct_shell_routes(command: str) -> None:
    result = DbtlOutputPolicyMiddleware().wrap_tool_call(
        _request("bash", {"command": command}),
        lambda _request: ToolMessage(content="ran", tool_call_id="call-1"),
    )
    assert "blocked" in str(result.content)


def test_ordinary_output_paths_remain_writable() -> None:
    result = DbtlOutputPolicyMiddleware().wrap_tool_call(
        _request("write_file", {"path": "/mnt/user-data/outputs/report.md"}),
        lambda _request: ToolMessage(content="wrote", tool_call_id="call-1"),
    )
    assert result.content == "wrote"
