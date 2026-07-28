"""Phase 6 live stage execution through the existing subagent runtime."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.agents.dbtl.stage_execution import (
    LiveStageAdapter,
    _compact_design_history,
    _stage_worker_config,
)
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_runner import DispatchOutcome
from deerflow.dbtl.stage_spec import WorkerBudget, resolve_stage_spec
from deerflow.subagents.config import SubagentConfig


def _cycle(*, state: str = "design", status: str = "in_progress", revision: int = 3) -> dict:
    stage_statuses = {
        "design": status if state == "design" else "approved",
        "reconciliation": (status if state == "reconciliation" else ("approved" if state in {"ready_for_build", "build", "test", "learn"} else "locked")),
        "build": (status if state in {"ready_for_build", "build"} else "approved" if state in {"test", "learn"} else "locked"),
        "test": (status if state == "test" else "approved" if state == "learn" else "locked"),
        "learn": status if state == "learn" else "locked",
    }
    return {
        "id": "cycle-1",
        "project_id": "project-1",
        "state": state,
        "db_revision": revision,
        "research_question": "Which hybrids retain yield under drought?",
        "objective": "Rank the hybrids.",
        "success_criteria": "Held-out-site rank correlation exceeds 0.4.",
        "stages": [
            {
                "id": f"attempt-{stage}",
                "stage": stage,
                "status": stage_status,
            }
            for stage, stage_status in stage_statuses.items()
        ],
    }


class FakeRepo:
    def __init__(self, cycle: dict | None) -> None:
        self.cycle = cycle
        self.recorded: list[dict] = []
        self.lineage: list[dict] = []
        self.learn_syntheses: list[dict] = []
        self.replay: dict | None = None
        self.worker_runs: list[dict] = []

    async def get_cycle(self, cycle_id: str, *, project_id: str):
        if self.cycle is None or cycle_id != self.cycle["id"] or project_id != self.cycle["project_id"]:
            return None
        return dict(self.cycle)

    async def list_datasets(self, cycle_id: str, *, project_id: str):
        return [
            {
                "source_key": "yield",
                "content_hash": "a" * 64,
            }
        ]

    async def reconciliation_view(self, cycle_id: str, *, project_id: str):
        return {}

    async def build_test_view(self, cycle_id: str, *, project_id: str):
        return {
            "cycle_id": cycle_id,
            "build_lineage": self.lineage[-1] if self.lineage else None,
            "validity_assessment": ({"outcome": "supported"} if self.cycle and self.cycle["state"] == "learn" else None),
        }

    async def list_worker_runs(self, cycle_id: str, *, project_id: str, stage: str):
        return list(self.worker_runs)

    async def get_stage_execution_replay(self, cycle_id: str, *, project_id: str, idempotency_key: str):
        return self.replay

    async def record_worker_runs(self, **kwargs):
        self.recorded.append(kwargs)
        self.cycle["db_revision"] += 1
        return kwargs["results"]

    async def record_build_lineage(self, **kwargs):
        self.lineage.append(kwargs)
        return kwargs

    async def record_learn_synthesis(self, **kwargs):
        self.learn_syntheses.append(kwargs)
        return kwargs

    async def knowledge_view(self, project_id: str, *, cycle_id: str | None = None):
        return {
            "project_id": project_id,
            "cycle_id": cycle_id,
            "candidates": [],
            "claims": [],
            "publications": [],
            "events": [],
        }


class FakeDispatcher:
    def __init__(self, *, text: str | None = None, error: str | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[tuple[object, object]] = []

    async def __call__(self, units, *, budget):
        self.calls.append((units, budget))
        return [
            DispatchOutcome(
                unit_id=unit.unit_id,
                text=self.text,
                error=self.error,
            )
            for unit in units
        ]


def _structured_result() -> str:
    return json.dumps(
        {
            "status": "completed",
            "summary": "Prepared an operational design brief.",
            "artifact_refs": ["/mnt/user-data/outputs/design-notes.md"],
            "claims": ["The success criterion is measurable."],
            "evidence_refs": [
                {
                    "kind": "workspace_file",
                    "reference": "/mnt/user-data/outputs/design-notes.md",
                    "description": "Operational criteria.",
                }
            ],
            "limitations": [],
            "quality_checks": [
                {
                    "name": "criterion has a threshold",
                    "passed": True,
                    "detail": "",
                }
            ],
            "recommended_next_actions": ["Review the design."],
            "provenance": {"inputs_examined": ["cycle metadata"]},
        }
    )


def _runtime_config(project_root: Path) -> dict:
    return {
        "context": {
            "project_id": "project-1",
            "project_root": str(project_root),
            "thread_id": "thread-1",
            "run_id": "run-1",
            "user_id": "user-1",
        },
        "configurable": {"thread_id": "thread-1"},
        "metadata": {"model_name": "test-model"},
    }


def test_stage_workers_do_not_implicitly_load_every_enabled_skill() -> None:
    budget = WorkerBudget(
        max_workers=3,
        max_turns=4,
        max_tokens=20_000,
        timeout_seconds=60,
    )
    inherited = SubagentConfig(
        name="general-purpose",
        description="generalist",
        skills=None,
        max_turns=20,
        timeout_seconds=300,
    )
    explicit = SubagentConfig(
        name="quant-genetics",
        description="specialist",
        skills=["quantitative-genetics"],
        max_turns=20,
        timeout_seconds=300,
    )

    bounded_inherited = _stage_worker_config(inherited, budget)
    bounded_explicit = _stage_worker_config(explicit, budget)

    assert bounded_inherited.skills == []
    assert bounded_explicit.skills == ["quantitative-genetics"]
    assert bounded_inherited.max_turns == 4
    assert bounded_inherited.timeout_seconds == 60


def test_design_history_keeps_only_four_bounded_chair_syntheses() -> None:
    runs = [
        {
            "unit_id": "specialist",
            "capability": "experimental_design",
            "status": "completed",
            "result": {"summary": "must not be copied"},
        }
    ]
    runs.extend(
        {
            "unit_id": f"chair-{index}",
            "capability": "design_council_chair",
            "status": "completed",
            "created_at": f"2026-07-2{index}",
            "result": {
                "summary": str(index) * 4_000,
                "claims": ["large claim payload"] * 20,
                "evidence_refs": [{"reference": "large evidence payload"}] * 20,
                "limitations": ["bounded limitation"] * 8,
                "clarification_question": "q" * 1_000,
            },
        }
        for index in range(6)
    )

    compact = _compact_design_history(runs)

    assert [item["unit_id"] for item in compact] == [
        "chair-2",
        "chair-3",
        "chair-4",
        "chair-5",
    ]
    assert all(len(item["summary"]) <= 3_000 for item in compact)
    assert all(len(item["clarification_question"]) <= 600 for item in compact)
    assert all(len(item["limitations"]) == 4 for item in compact)
    assert all("claims" not in item and "evidence_refs" not in item for item in compact)


@pytest.mark.asyncio
async def test_a_live_design_run_persists_workers_and_a_reviewable_package(
    tmp_path: Path,
) -> None:
    repo = FakeRepo(_cycle())
    dispatcher = FakeDispatcher(text=_structured_result())
    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (
            AgentCandidate(
                name="designer",
                capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
            ),
        ),
        dispatcher=dispatcher,
    )

    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert result.stage == "design"
    assert result.worker_count == 3
    assert result.produced_usable_evidence
    assert result.satisfies_gate is False
    assert len(repo.recorded) == 1
    write = repo.recorded[0]
    assert write["stage_spec_key"] == "generic:design:v2"
    assert write["results"][0]["unit_id"].startswith("dbtl-")
    assert write["results"][0]["is_trustworthy"] is True
    assert write["artifact_type"] == "design_brief"
    assert write["artifact_content_hash"]
    # The attached artifact is the Markdown a human reads, so the approval binds
    # to the reviewed document rather than to a machine record nobody opened.
    document = tmp_path / write["artifact_uri"].removeprefix("/mnt/user-data/")
    assert document.suffix == ".md"
    assert document.exists()
    rendered = document.read_text()
    assert rendered.startswith("# Design review package")
    assert "does not satisfy" in rendered
    # Named for a person browsing the folder, not for a machine. This fixture's
    # cycle has no title, so the readable fallback is the cycle id itself.
    assert document.name == "design-review-rev3-" + document.name.split("-")[-1]
    assert document.parent.as_posix().endswith("dbtl/cycle-1/design")
    # The structured package sits beside it and is named from the document,
    # keeping the audit chain intact.
    package = next(document.parent.glob("design-package-rev3-*.json"))
    package_payload = json.loads(package.read_text())
    assert package_payload["stage_spec_key"] == "generic:design:v2"
    assert package_payload["results"][-1]["capability"] == "design_council_chair"
    assert package.name in rendered
    # The chat note carries the synthesis, not only a path.
    assert "outputs/dbtl/cycle-1/design/" in result.note
    assert result.note.index("\n") < result.note.index("outputs/")
    assert len(dispatcher.calls) == 3
    assert "red team" in dispatcher.calls[1][0][0].prompt
    assert "independent meeting positions" in dispatcher.calls[2][0][0].prompt


@pytest.mark.asyncio
async def test_the_red_team_convenes_even_when_several_specialists_are_declared(
    tmp_path: Path,
) -> None:
    """Several specialists are several *positions*, not an adversarial one.

    Declaring a second specialist used to cancel the red team, so a project
    that configured its council more carefully got a weaker one: agreeing
    experts and no one whose job is to attack the design. The guarantee is a
    floor on the debate, not a headcount.
    """
    repo = FakeRepo(_cycle())
    dispatcher = FakeDispatcher(text=_structured_result())
    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (
            AgentCandidate(
                name="designer",
                capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
            ),
            AgentCandidate(
                name="geneticist",
                capabilities=frozenset({Capability.QUANTITATIVE_GENETICS}),
            ),
        ),
        dispatcher=dispatcher,
    )

    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=_runtime_config(tmp_path),
    )

    # Two specialist positions, then the red team, then the chair.
    assert len(dispatcher.calls) == 3
    assert len(dispatcher.calls[0][0]) == 2
    assert "red team" in dispatcher.calls[1][0][0].prompt
    assert "independent meeting positions" in dispatcher.calls[2][0][0].prompt
    assert result.worker_count == 4

    capabilities = [item["capability"] for item in repo.recorded[0]["results"]]
    assert "design_red_team" in capabilities
    assert capabilities[-1] == "design_council_chair"


@pytest.mark.asyncio
async def test_the_previewed_roster_matches_the_council_that_actually_runs(
    tmp_path: Path,
) -> None:
    """The preflight card is only worth showing if it is true.

    `plan_council` is what a person reviews before the council convenes; the
    adapter is what convenes it. They are computed separately today, so this
    pins them against each other — a card that promised two specialists and a
    chair while the run dispatched something else would be worse than no card.
    """
    from deerflow.dbtl.council import CouncilDepth, plan_council
    from deerflow.dbtl.stage_spec import resolve_stage_spec

    candidates = (
        AgentCandidate(
            name="designer",
            capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
        ),
        AgentCandidate(
            name="geneticist",
            capabilities=frozenset({Capability.QUANTITATIVE_GENETICS}),
        ),
    )
    repo = FakeRepo(_cycle())
    dispatcher = FakeDispatcher(text=_structured_result())
    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: candidates,
        dispatcher=dispatcher,
    )

    await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=_runtime_config(tmp_path),
    )

    plan = plan_council(
        resolve_stage_spec("design", domain_profile="generic"),
        candidates,
        depth=CouncilDepth.MEDIUM,
        model="test-model",
    )

    dispatched = [unit for call in dispatcher.calls for unit in call[0]]
    assert [unit.agent_name for unit in dispatched] == [seat.agent_name for seat in plan.seats]
    assert len(dispatched) == len(plan.seats)
    # The last dispatch is the chair in both accounts, and it is the only one
    # whose output becomes the stage's answer.
    assert dispatched[-1].capability == "design_council_chair"
    assert plan.seats[-1].counts_toward_stage_output
    assert sum(seat.counts_toward_stage_output for seat in plan.seats) == 1


@pytest.mark.asyncio
async def test_the_confirmed_depth_changes_the_council_that_is_dispatched(
    tmp_path: Path,
) -> None:
    """Depth is the human's dial, and it has to reach the workers.

    A card that offered light/medium/heavy and then ran the same council
    regardless would be worse than not offering the choice.
    """
    candidates = (
        AgentCandidate(name="designer", capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN})),
        AgentCandidate(name="geneticist", capabilities=frozenset({Capability.QUANTITATIVE_GENETICS})),
    )

    async def _run(depth: str | None) -> FakeDispatcher:
        dispatcher = FakeDispatcher(text=_structured_result())
        adapter = LiveStageAdapter(
            repo=FakeRepo(_cycle()),
            app_config=SimpleNamespace(),
            candidate_provider=lambda: candidates,
            dispatcher=dispatcher,
        )
        config = _runtime_config(tmp_path)
        if depth is not None:
            config["context"]["dbtl_council_depth"] = depth
        await adapter.execute(
            project_id="project-1",
            cycle_id="cycle-1",
            request_text="Draft the Design package.",
            state={},
            config=config,
        )
        return dispatcher

    light = await _run("light")
    heavy = await _run("heavy")

    light_units = [unit for call in light.calls for unit in call[0]]
    heavy_units = [unit for call in heavy.calls for unit in call[0]]
    assert len(light_units) < len(heavy_units)
    # The budget reaches the workers too, not just the headcount.
    assert light.calls[0][1].max_turns < heavy.calls[0][1].max_turns
    # Whatever the depth, the debate keeps its shape.
    for units in (light_units, heavy_units):
        assert [unit.capability for unit in units][-2:] == ["design_red_team", "design_council_chair"]


@pytest.mark.asyncio
async def test_an_unrecognized_depth_degrades_instead_of_failing_the_cycle(
    tmp_path: Path,
) -> None:
    # A stale client losing a preference is a far smaller failure than a cycle
    # that cannot be designed, so an unknown value falls back rather than raises.
    dispatcher = FakeDispatcher(text=_structured_result())
    adapter = LiveStageAdapter(
        repo=FakeRepo(_cycle()),
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (AgentCandidate(name="designer", capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN})),),
        dispatcher=dispatcher,
    )
    config = _runtime_config(tmp_path)
    config["context"]["dbtl_council_depth"] = "exhaustive"

    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=config,
    )

    assert result.produced_usable_evidence


@pytest.mark.asyncio
async def test_the_review_package_records_who_sat_on_the_council(
    tmp_path: Path,
) -> None:
    # The reviewer approves a document; if the roster is not in the record, the
    # budget they would reconstruct from the spec key is not the one the workers
    # actually had.
    repo = FakeRepo(_cycle())
    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (AgentCandidate(name="designer", capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN})),),
        dispatcher=FakeDispatcher(text=_structured_result()),
    )
    config = _runtime_config(tmp_path)
    config["context"]["dbtl_council_depth"] = "heavy"

    await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=config,
    )

    document = tmp_path / repo.recorded[0]["artifact_uri"].removeprefix("/mnt/user-data/")
    package = json.loads(next(document.parent.glob("design-package-rev3-*.json")).read_text())
    council = package["council"]
    assert council["depth"] == "heavy"
    # The *chosen depth's* budget, not the stage spec's default — a reviewer
    # reconstructing the allowance from ``stage_spec_key`` alone would read the
    # wrong one. Asserted against the policy rather than a literal so retuning a
    # depth does not look like a regression here.
    from deerflow.dbtl.council import CouncilDepth, depth_policy
    from deerflow.dbtl.stage_spec import resolve_stage_spec

    assert council["budget"]["max_turns"] == depth_policy(CouncilDepth.HEAVY).budget.max_turns
    assert council["budget"]["max_turns"] != resolve_stage_spec("design").budget.max_turns
    assert [seat["role"] for seat in council["seats"]][-1] == "chair"
    assert council["seats"][0]["agent_name"] == "designer"


@pytest.mark.asyncio
async def test_design_council_pauses_for_one_human_clarification_without_artifact(
    tmp_path: Path,
) -> None:
    repo = FakeRepo(_cycle())

    class ClarifyingDispatcher:
        def __init__(self) -> None:
            self.calls = []

        async def __call__(self, units, *, budget):
            self.calls.append((units, budget))
            if len(self.calls) < 3:
                text = _structured_result()
            else:
                text = json.dumps(
                    {
                        "status": "needs_input",
                        "summary": "The council cannot operationalize the holdout.",
                        "artifact_refs": [],
                        "claims": [],
                        "evidence_refs": [],
                        "limitations": ["The held-out environments are unspecified."],
                        "quality_checks": [],
                        "recommended_next_actions": ["Ask the project owner."],
                        "provenance": {"inputs_examined": ["cycle metadata"]},
                        "clarification_question": ("Which environments should be reserved as the held-out validation set?"),
                    }
                )
            return [DispatchOutcome(unit_id=unit.unit_id, text=text) for unit in units]

    dispatcher = ClarifyingDispatcher()
    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (
            AgentCandidate(
                name="designer",
                capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
            ),
        ),
        dispatcher=dispatcher,
    )

    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Start the Design council.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert result.clarification_question == ("Which environments should be reserved as the held-out validation set?")
    assert not result.produced_usable_evidence
    assert result.artifact_uri is None
    assert repo.recorded[0]["artifact_type"] is None
    assert repo.recorded[0]["results"][-1]["status"] == "needs_input"
    assert len(dispatcher.calls) == 3


@pytest.mark.asyncio
async def test_design_council_receives_a_bounded_project_file_manifest(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("trial protocol")
    (tmp_path / "data.csv").write_text("line,yield\nA,10\n")
    repo = FakeRepo(_cycle())
    dispatcher = FakeDispatcher(text=_structured_result())
    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (
            AgentCandidate(
                name="designer",
                capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
            ),
        ),
        dispatcher=dispatcher,
    )

    await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Start the Design council.",
        state={},
        config=_runtime_config(tmp_path),
    )

    first_prompt = dispatcher.calls[0][0][0].prompt
    assert "README.md" in first_prompt
    assert "data.csv" in first_prompt


@pytest.mark.asyncio
async def test_a_failed_fanout_is_recorded_but_does_not_create_review_evidence(
    tmp_path: Path,
) -> None:
    repo = FakeRepo(_cycle())
    dispatcher = FakeDispatcher(error="sandbox unavailable")
    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (
            AgentCandidate(
                name="designer",
                capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
            ),
        ),
        dispatcher=dispatcher,
    )

    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert not result.produced_usable_evidence
    assert repo.recorded[0]["results"][0]["status"] == "failed"
    assert repo.recorded[0]["artifact_type"] is None
    assert repo.recorded[0]["artifact_uri"] is None


@pytest.mark.asyncio
async def test_ready_for_build_runs_build_and_records_reproducibility_lineage(
    tmp_path: Path,
) -> None:
    repo = FakeRepo(_cycle(state="ready_for_build"))
    dispatcher = FakeDispatcher(text=_structured_result())
    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (
            AgentCandidate(
                name="builder",
                capabilities=frozenset({Capability.SOFTWARE_ENGINEERING}),
            ),
        ),
        dispatcher=dispatcher,
    )

    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Build the reproducible pipeline.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert result.stage == "build"
    assert repo.recorded[0]["stage_spec_key"] == "generic:build:v1"
    assert len(repo.lineage) == 1
    assert repo.lineage[0]["expected_db_revision"] == 4
    assert repo.lineage[0]["output_artifacts"][0]["content_hash"]
    assert repo.lineage[0]["code_revision"] == "workspace:unversioned"
    assert repo.lineage[0]["deviations"]


@pytest.mark.asyncio
async def test_learn_records_only_provisional_evidence_bound_candidates(
    tmp_path: Path,
) -> None:
    repo = FakeRepo(_cycle(state="learn"))
    dispatcher = FakeDispatcher(text=_structured_result())
    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (
            AgentCandidate(
                name="knowledge-synthesizer",
                capabilities=frozenset({Capability.KNOWLEDGE_SYNTHESIS}),
            ),
        ),
        dispatcher=dispatcher,
    )

    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Synthesize the bounded Learn closeout.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert result.stage == "learn"
    assert repo.recorded[0]["stage_spec_key"] == "generic:learn:v1"
    assert len(repo.learn_syntheses) == 1
    synthesis = repo.learn_syntheses[0]
    assert synthesis["expected_db_revision"] == 4
    assert synthesis["candidates"][0]["grade"] == "supported"
    assert synthesis["candidates"][0]["evidence"]


@pytest.mark.asyncio
async def test_a_cycle_outside_the_project_is_never_dispatched(tmp_path: Path) -> None:
    repo = FakeRepo(None)
    dispatcher = FakeDispatcher(text=_structured_result())
    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (),
        dispatcher=dispatcher,
    )

    result = await adapter.execute(
        project_id="project-1",
        cycle_id="foreign-cycle",
        request_text="Continue it.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert result.worker_count == 0
    assert "not available in this project" in result.note
    assert dispatcher.calls == []
    assert repo.recorded == []


@pytest.mark.asyncio
async def test_a_stage_awaiting_human_review_is_not_run_again(tmp_path: Path) -> None:
    repo = FakeRepo(_cycle(status="awaiting_review"))
    dispatcher = FakeDispatcher(text=_structured_result())
    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (),
        dispatcher=dispatcher,
    )

    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Continue it.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert "awaiting human review" in result.note
    assert dispatcher.calls == []
    assert repo.recorded == []


@pytest.mark.asyncio
async def test_a_replayed_run_returns_the_durable_result_without_dispatch(
    tmp_path: Path,
) -> None:
    repo = FakeRepo(_cycle())
    repo.replay = {
        "stage": "design",
        "worker_count": 1,
        "trustworthy_count": 1,
        "artifact_uri": "/mnt/user-data/outputs/dbtl/design.json",
    }
    dispatcher = FakeDispatcher(text=_structured_result())
    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (),
        dispatcher=dispatcher,
    )

    result = await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Continue it.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert result.worker_count == 1
    assert result.produced_usable_evidence
    assert "already recorded" in result.note
    assert dispatcher.calls == []


def _design_adapter(repo: FakeRepo, dispatcher: FakeDispatcher) -> LiveStageAdapter:
    return LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (
            AgentCandidate(
                name="designer",
                capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
            ),
        ),
        dispatcher=dispatcher,
    )


def _with_design_package(cycle: dict) -> dict:
    cycle["title"] = "Genomic selection in maize"
    cycle["artifacts"] = [
        {
            "stage_attempt_id": "attempt-design",
            "artifact_type": "design_brief",
            "uri": "/mnt/user-data/outputs/dbtl/cycle-1/design/design-review-rev3-abc123.md",
            "content_hash": "b" * 64,
            "revision": 1,
        }
    ]
    return cycle


@pytest.mark.asyncio
async def test_a_design_already_on_the_table_is_not_debated_again(
    tmp_path: Path,
) -> None:
    """A Design stage stays in_progress until someone submits it for review.

    Before this rule, every later message in the cycle convened the whole
    meeting again over a design that was already sitting there waiting.
    """
    repo = FakeRepo(_with_design_package(_cycle()))
    dispatcher = FakeDispatcher(text=_structured_result())

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="How does this handle the drought sites?",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert dispatcher.calls == []
    assert repo.recorded == []
    assert result.worker_count == 0
    assert "design-review-rev3-abc123.md" in result.note
    assert "run the meeting again" in result.note


@pytest.mark.asyncio
async def test_asking_for_another_meeting_convenes_one(tmp_path: Path) -> None:
    repo = FakeRepo(_with_design_package(_cycle()))
    dispatcher = FakeDispatcher(text=_structured_result())

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Run the meeting again with the drought sites in scope.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert dispatcher.calls
    assert result.worker_count == 3


@pytest.mark.asyncio
async def test_a_change_request_still_reopens_the_debate_without_being_asked(
    tmp_path: Path,
) -> None:
    """``changes_requested`` *is* the request to argue again."""
    cycle = _with_design_package(_cycle(status="changes_requested"))
    repo = FakeRepo(cycle)
    dispatcher = FakeDispatcher(text=_structured_result())

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Here is the design.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert dispatcher.calls
    assert result.worker_count == 3


def _paused_meeting_runs() -> list[dict]:
    return [
        {
            "unit_id": "dbtl-x-1-experimental_design",
            "capability": "experimental_design",
            "status": "completed",
            "result": {
                "status": "completed",
                "summary": "Argue for a matched-model benchmark.",
                "claims": ["The benchmark is restricted."],
                "limitations": [],
                "evidence_refs": [{"kind": "external", "reference": "position-1"}],
            },
        },
        {
            "unit_id": "dbtl-x-chair",
            "capability": "design_council_chair",
            "status": "needs_input",
            "result": {
                "status": "needs_input",
                "summary": "The positions converge on one gating decision.",
                "clarification_question": "Toy benchmark or credible simulator?",
            },
        },
    ]


@pytest.mark.asyncio
async def test_answering_the_chair_resumes_it_instead_of_re_running_the_meeting(
    tmp_path: Path,
) -> None:
    repo = FakeRepo(_cycle())
    repo.worker_runs = _paused_meeting_runs()
    dispatcher = FakeDispatcher(text=_structured_result())

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="A credible simulator.",
        state={},
        config=_runtime_config(tmp_path),
        clarification_answer="A credible simulator.",
    )

    # One worker, and it is the chair. No positions, no red team: they already
    # argued and their results are durable.
    assert len(dispatcher.calls) == 1
    units = dispatcher.calls[0][0]
    assert [unit.capability for unit in units] == ["design_council_chair"]
    assert result.worker_count == 1
    assert result.produced_usable_evidence

    prompt = units[0].prompt
    # The owner's words travel verbatim, and so does the question they answer.
    assert "A credible simulator." in prompt
    assert "Toy benchmark or credible simulator?" in prompt
    assert "Argue for a matched-model benchmark." in prompt

    notes = repo.recorded[0]["results"]
    assert notes[0]["counts_toward_stage_output"] is True


@pytest.mark.asyncio
async def test_an_answer_with_no_outstanding_question_does_not_resume(
    tmp_path: Path,
) -> None:
    """A stray card reply after a completed synthesis is not a resume."""
    repo = FakeRepo(_cycle())
    repo.worker_runs = [
        {
            "unit_id": "dbtl-x-chair",
            "capability": "design_council_chair",
            "status": "completed",
            "result": {"status": "completed", "summary": "Done."},
        }
    ]
    dispatcher = FakeDispatcher(text=_structured_result())

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Something else.",
        state={},
        config=_runtime_config(tmp_path),
        clarification_answer="Something else.",
    )

    assert result.worker_count == 3


@pytest.mark.asyncio
async def test_every_round_writes_a_slide_deck_beside_the_review_package(
    tmp_path: Path,
) -> None:
    repo = FakeRepo(_cycle())
    dispatcher = FakeDispatcher(text=_structured_result())

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert result.deck_uri is not None
    deck = tmp_path / result.deck_uri.removeprefix("/mnt/user-data/")
    assert deck.suffix == ".html"
    assert deck.exists()
    assert deck.parent == (tmp_path / result.artifact_uri.removeprefix("/mnt/user-data/")).parent
    rendered = deck.read_text()
    assert rendered.startswith("<!doctype html>")
    # It presents the record; it never becomes the record.
    assert "Nothing here approves anything" in rendered
    assert result.artifact_uri.endswith(".md")
    assert repo.recorded[0]["artifact_uri"] == result.artifact_uri


@pytest.mark.asyncio
async def test_a_paused_meeting_still_gets_a_deck(tmp_path: Path) -> None:
    """The round that asks for a decision is the one that most needs a deck."""
    repo = FakeRepo(_cycle())
    dispatcher = FakeDispatcher(
        text=json.dumps(
            {
                "status": "needs_input",
                "summary": "One decision is required.",
                "artifact_refs": [],
                "claims": [],
                "evidence_refs": [],
                "limitations": [],
                "quality_checks": [{"name": "scope stated", "passed": True, "detail": ""}],
                "recommended_next_actions": [],
                "clarification_question": "Toy benchmark or credible simulator?",
                "consensus": {
                    "agreements": ["The lineage is restricted."],
                    "disagreements": [],
                    "open_questions": [],
                },
                "provenance": {"inputs_examined": []},
            }
        )
    )

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert result.clarification_question == "Toy benchmark or credible simulator?"
    assert result.artifact_uri is None
    assert result.deck_uri is not None
    rendered = (tmp_path / result.deck_uri.removeprefix("/mnt/user-data/")).read_text()
    assert "Toy benchmark or credible simulator?" in rendered
    assert "Needs your decision" in rendered


def test_a_configured_meeting_model_beats_the_composers(tmp_path: Path) -> None:
    """A meeting convened from an expensive chat should not cost that much."""
    app_config = SimpleNamespace(
        dbtl=SimpleNamespace(council_model_name="cheap-model"),
        models=[SimpleNamespace(name="cheap-model"), SimpleNamespace(name="test-model")],
    )
    adapter = LiveStageAdapter(
        repo=FakeRepo(_cycle()),
        app_config=app_config,
        candidate_provider=lambda: (
            AgentCandidate(
                name="designer",
                capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
            ),
        ),
        dispatcher=FakeDispatcher(text=_structured_result()),
    )

    plan = adapter._plan_council(
        resolve_stage_spec("design"),
        config=_runtime_config(tmp_path),
        request_text="Draft the Design package.",
        attempt_id="attempt",
    )

    assert {seat.model for seat in plan.seats} == {"cheap-model"}


def test_an_unconfigured_meeting_model_falls_back_rather_than_failing(
    tmp_path: Path,
) -> None:
    app_config = SimpleNamespace(
        dbtl=SimpleNamespace(council_model_name="typo-model"),
        models=[SimpleNamespace(name="test-model")],
    )
    adapter = LiveStageAdapter(
        repo=FakeRepo(_cycle()),
        app_config=app_config,
        candidate_provider=lambda: (
            AgentCandidate(
                name="designer",
                capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
            ),
        ),
        dispatcher=FakeDispatcher(text=_structured_result()),
    )

    plan = adapter._plan_council(
        resolve_stage_spec("design"),
        config=_runtime_config(tmp_path),
        request_text="Draft the Design package.",
        attempt_id="attempt",
    )

    assert {seat.model for seat in plan.seats} == {"test-model"}
