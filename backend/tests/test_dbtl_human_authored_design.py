"""Recording a design the scientist wrote, without pretending an agent did.

At the Human Input depth nothing is dispatched, so there are no worker results
to fold into a package. The temptation is to synthesize one — attribute the
person's text to ``general-purpose`` and reuse the existing path — and that is
exactly what must not happen: the audit record would then say a council
produced a design it never saw, and the whole point of the roster work was that
a reviewer can tell a real debate from a stand-in.

So this path writes a package with **zero** worker runs, says on the document
who wrote it, and still refuses to satisfy the gate. The human authored it; they
must still review it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_dbtl_live_stage_execution import (  # noqa: F401 - shared fixtures
    FakeDispatcher,
    FakeRepo,
    _cycle,
    _runtime_config,
)

from deerflow.agents.dbtl.stage_execution import LiveStageAdapter
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.council import CouncilDepth


def _config(project_root: Path, *, depth: str | None = CouncilDepth.HUMAN_INPUT.value) -> dict:
    config = _runtime_config(project_root)
    if depth is not None:
        config["context"]["dbtl_council_depth"] = depth
    return config


def _candidates() -> list[AgentCandidate]:
    return [
        AgentCandidate(name="general-purpose", capabilities=frozenset(), available=True, is_generalist=True),
        AgentCandidate(
            name="quant-geneticist",
            capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
            available=True,
        ),
    ]


def _adapter(repo: FakeRepo, dispatcher: FakeDispatcher) -> LiveStageAdapter:
    return LiveStageAdapter(
        repo=repo,
        app_config=None,
        candidate_provider=_candidates,
        dispatcher=dispatcher,
    )


@pytest.mark.anyio
async def test_it_asks_for_the_design_instead_of_convening(tmp_path: Path) -> None:
    repo = FakeRepo(_cycle())
    dispatcher = FakeDispatcher(text="{}")
    adapter = _adapter(repo, dispatcher)

    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Design the drought trial.",
        state={},
        config=_config(tmp_path),
    )

    assert dispatcher.calls == []
    assert result.authoring_request
    assert result.artifact_uri is None
    assert result.worker_count == 0
    # Nothing durable happens on the ask. A person who opens the card and walks
    # away must leave the cycle exactly as they found it.
    assert repo.recorded == []


@pytest.mark.anyio
async def test_the_authored_text_becomes_the_review_package(tmp_path: Path) -> None:
    repo = FakeRepo(_cycle())
    dispatcher = FakeDispatcher(text="{}")
    adapter = _adapter(repo, dispatcher)
    design = "Randomized complete block across three sites, two seasons. Reject if held-out rank correlation is below 0.3."

    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Design the drought trial.",
        state={},
        config=_config(tmp_path),
        authored_design=design,
    )

    assert dispatcher.calls == []
    assert result.authoring_request is None
    assert result.artifact_uri and result.artifact_uri.endswith(".md")
    assert result.produced_usable_evidence is True

    document = (tmp_path / "outputs" / Path(result.artifact_uri).relative_to("/mnt/user-data/outputs")).read_text()
    assert design in document
    # The reader must not have to infer this from an empty worker table.
    assert "written by" in document.lower() or "authored" in document.lower()


@pytest.mark.anyio
async def test_no_worker_run_is_invented_for_the_person(tmp_path: Path) -> None:
    repo = FakeRepo(_cycle())
    adapter = _adapter(repo, FakeDispatcher(text="{}"))

    await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Design it.",
        state={},
        config=_config(tmp_path),
        authored_design="My design.",
    )

    assert len(repo.recorded) == 1
    recorded = repo.recorded[0]
    assert recorded["results"] == []
    assert recorded["artifact_type"] == "design_brief"
    assert recorded["artifact_content_hash"]


@pytest.mark.anyio
async def test_authoring_it_does_not_approve_it(tmp_path: Path) -> None:
    repo = FakeRepo(_cycle())
    adapter = _adapter(repo, FakeDispatcher(text="{}"))

    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Design it.",
        state={},
        config=_config(tmp_path),
        authored_design="My design.",
    )

    assert result.satisfies_gate is False


@pytest.mark.anyio
async def test_blank_authored_text_is_treated_as_no_answer(tmp_path: Path) -> None:
    """An empty submission must re-ask rather than record an empty design."""
    repo = FakeRepo(_cycle())
    adapter = _adapter(repo, FakeDispatcher(text="{}"))

    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Design it.",
        state={},
        config=_config(tmp_path),
        authored_design="   \n  ",
    )

    assert result.authoring_request
    assert result.artifact_uri is None
    assert repo.recorded == []


@pytest.mark.anyio
async def test_another_depth_ignores_authored_text_entirely(tmp_path: Path) -> None:
    """Authored text is only meaningful for the depth that asked for it.

    Otherwise a stale card answer replayed at medium depth would bypass the
    council it was never routed to.
    """
    repo = FakeRepo(_cycle())
    dispatcher = FakeDispatcher(text="{}")
    adapter = _adapter(repo, dispatcher)

    await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Design it.",
        state={},
        config=_config(tmp_path, depth=CouncilDepth.MEDIUM.value),
        authored_design="My design.",
    )

    assert dispatcher.calls, "the council must still run at medium depth"
