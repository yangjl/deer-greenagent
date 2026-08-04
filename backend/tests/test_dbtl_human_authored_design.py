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


def _reviewable_repo() -> FakeRepo:
    """A repo that keeps the artifact row, as production does.

    The deck binds its evidence by content hash off the cycle's artifact list,
    so a fake that only recorded the call would make every surface read_only.
    """
    repo = FakeRepo(_cycle())
    repo.records_artifacts = True
    return repo


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


class TestTheAuthoredDesignStillHasAGate:
    """A design nobody can approve is a cycle nobody can finish.

    Design is submitted and decided in its registered slide deck and nowhere
    else — the stage sheet is inspection-only and says so. The council path
    reaches that deck through a chair result, which this depth deliberately
    never produces, so the authored design used to be recorded and then
    stranded: a package on disk, no surface, no deck, no control anywhere in
    the product capable of approving it, and Build locked forever behind it.

    The fix is not to invent a chair. It is to render the person's own text
    into the same canonical deck and register it as the surface they answer.
    """

    @pytest.mark.anyio
    async def test_it_registers_a_surface_and_writes_a_deck(self, tmp_path: Path) -> None:
        repo = _reviewable_repo()
        adapter = _adapter(repo, FakeDispatcher(text="{}"))

        result = await adapter.execute(
            project_id="project-1",
            cycle_id="cycle-1",
            request_text="Design it.",
            state={},
            config=_config(tmp_path),
            authored_design="Randomized complete block across three sites.",
        )

        assert result.deck_uri, "an authored design must still produce a deck"
        assert result.feedback_surface_id, "the deck must be a registered, answerable surface"
        assert len(repo.surfaces) == 1
        surface = repo.surfaces[0]
        assert surface["mode"] == "stage_review"
        # Bound to the authored package, by hash — the verdict must attach to
        # the document the deck was rendered from.
        assert surface["evidence_content_hash"] == repo.recorded[0]["artifact_content_hash"]
        # No chair ran, so nothing may claim one did.
        assert surface["chair_worker_run_id"] is None

    @pytest.mark.anyio
    async def test_the_deck_carries_the_gate_and_the_authored_text(self, tmp_path: Path) -> None:
        repo = _reviewable_repo()
        adapter = _adapter(repo, FakeDispatcher(text="{}"))
        design = "Two seasons, three sites. Reject below rank correlation 0.3."

        result = await adapter.execute(
            project_id="project-1",
            cycle_id="cycle-1",
            request_text="Design it.",
            state={},
            config=_config(tmp_path),
            authored_design=design,
        )

        deck = (tmp_path / "outputs" / Path(result.deck_uri).relative_to("/mnt/user-data/outputs")).read_text()
        assert design in deck
        assert 'data-deck-action="approve"' in deck
        assert "design-feedback-bridge" in deck or "data-deck-gate-submit" in deck
        # Inert on disk: authority is the authenticated parent's to grant.
        assert "Open this deck in DeerFlow to respond." in deck

    @pytest.mark.anyio
    async def test_the_deck_does_not_stage_a_meeting_that_never_happened(self, tmp_path: Path) -> None:
        """The renderer has no sentence of its own, and no chair to quote.

        Rendering the council's own slides here would fill them from an absent
        chair — "The chair recorded no agreement" reads as a meeting with a
        poor outcome, not as the meeting nobody convened.
        """
        repo = _reviewable_repo()
        adapter = _adapter(repo, FakeDispatcher(text="{}"))

        result = await adapter.execute(
            project_id="project-1",
            cycle_id="cycle-1",
            request_text="Design it.",
            state={},
            config=_config(tmp_path),
            authored_design="My design.",
        )

        deck = (tmp_path / "outputs" / Path(result.deck_uri).relative_to("/mnt/user-data/outputs")).read_text()
        # The council's own section headings and its empty-chair fallbacks are
        # what would misdescribe this. The bridge's machine vocabulary still
        # contains "chair_option"; that is protocol, not a claim about a
        # meeting, so the assertion is on the rendered sections.
        assert "The chair recorded no agreement" not in deck
        assert "Contested" not in deck
        assert "Where the meeting split" not in deck
        # It must say plainly who wrote this and that nobody argued it.
        assert "no meeting" in deck.lower()

    @pytest.mark.anyio
    async def test_writing_the_design_still_does_not_approve_it(self, tmp_path: Path) -> None:
        repo = _reviewable_repo()
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
        assert repo.recorded[0]["results"] == []
