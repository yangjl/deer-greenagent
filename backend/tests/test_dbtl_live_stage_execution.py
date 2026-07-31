"""Phase 6 live stage execution through the existing subagent runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.agents.dbtl.stage_execution import (
    LiveStageAdapter,
    _bound_evidence,
    _build_input_artifacts,
    _compact_design_history,
    _project_file_snapshot,
    _report_subagent_token_usage,
    _stage_worker_config,
    _summarize_token_usage,
    _token_limit_for_worker,
    _tools_for_stage_budget,
    _wants_new_debate,
)
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_runner import DispatchOutcome, WorkUnit
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
        self.surfaces: list[dict] = []
        #: Set to raise from surface registration, to prove a descriptor
        #: failure cannot cost the meeting whose results are already committed.
        self.surface_error: Exception | None = None

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

    async def register_design_feedback_surface(self, **kwargs):
        if self.surface_error is not None:
            raise self.surface_error
        self.surfaces.append(kwargs)
        return {"surface_id": f"dfs-{len(self.surfaces)}", **kwargs}

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
    def __init__(
        self,
        *,
        text: str | None = None,
        error: str | None = None,
        token_usage: dict[str, int] | None = None,
    ) -> None:
        self.text = text
        self.error = error
        self.token_usage = token_usage
        self.calls: list[tuple[object, object]] = []

    async def __call__(self, units, *, budget):
        self.calls.append((units, budget))
        return [
            DispatchOutcome(
                unit_id=unit.unit_id,
                text=self.text,
                error=self.error,
                token_usage=self.token_usage,
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


def test_light_pilot_keeps_only_targeted_file_read_tools() -> None:
    from deerflow.dbtl.council import CouncilDepth, depth_policy

    tools = [
        SimpleNamespace(name="read_file"),
        SimpleNamespace(name="bash"),
        SimpleNamespace(name="web_search"),
    ]

    light = _tools_for_stage_budget(
        tools,
        depth_policy(CouncilDepth.LIGHT).budget,
    )
    medium = _tools_for_stage_budget(
        tools,
        depth_policy(CouncilDepth.MEDIUM).budget,
    )

    assert [tool.name for tool in light] == ["read_file"]
    assert [tool.name for tool in medium] == ["read_file", "bash", "web_search"]


def test_design_council_ignores_stale_per_participant_token_caps() -> None:
    """A cached card edit must not re-enable the guardrail for an uncapped depth."""
    from deerflow.dbtl.council import CouncilDepth, depth_policy

    unit = WorkUnit(
        unit_id="chair",
        capability="design_council_chair",
        agent_name="general-purpose",
        prompt="Synthesize.",
        max_tokens=10_000,
    )

    assert (
        _token_limit_for_worker(
            unit,
            depth_policy(CouncilDepth.LIGHT).budget,
        )
        is None
    )


def test_design_council_usage_is_summarized_and_reported_to_the_parent_run() -> None:
    records = [
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
        },
        {
            "input_tokens": 50,
            "output_tokens": 10,
            "total_tokens": 60,
        },
    ]
    recorded = []

    class Recorder:
        def record_external_llm_usage_records(self, value):
            recorded.append(value)

    result = SimpleNamespace(
        usage_reported=False,
        token_usage_records=records,
    )

    assert _summarize_token_usage(records) == {
        "input_tokens": 150,
        "output_tokens": 30,
        "total_tokens": 180,
    }
    _report_subagent_token_usage({"callbacks": [Recorder()]}, result)
    _report_subagent_token_usage({"callbacks": [Recorder()]}, result)

    assert recorded == [records]
    assert result.usage_reported is True


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
    dispatcher = FakeDispatcher(
        text=_structured_result(),
        token_usage={
            "input_tokens": 100,
            "output_tokens": 25,
            "total_tokens": 125,
        },
    )
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
    assert write["results"][0]["token_usage"]["total_tokens"] == 125
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
    assert "375 total" in rendered
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
    assert package_payload["token_usage"] == {
        "input_tokens": 300,
        "output_tokens": 75,
        "total_tokens": 375,
    }
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
async def test_light_debate_runs_on_a_bounded_quick_pilot_context(
    tmp_path: Path,
) -> None:
    """Light must reduce exploration even though tokens are no longer capped.

    An uncapped worker with a full research prompt is still not a quick pilot.
    It should receive a small, explicit assignment that tells it when to stop.
    """
    for index in range(40):
        (tmp_path / f"{index:03}.txt").write_text(f"pilot input {index}")
    dispatcher = FakeDispatcher(text=_structured_result())
    adapter = LiveStageAdapter(
        repo=FakeRepo(_cycle()),
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (
            AgentCandidate(
                name="designer",
                capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
            ),
        ),
        dispatcher=dispatcher,
    )
    config = _runtime_config(tmp_path)
    config["context"]["dbtl_council_depth"] = "light"

    await adapter.execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Give me a quick pilot design.",
        state={},
        config=config,
    )

    from deerflow.dbtl.council import CouncilDepth, depth_policy

    policy = depth_policy(CouncilDepth.LIGHT)
    assert len(dispatcher.calls) == 3
    assert all(budget == policy.budget for _, budget in dispatcher.calls)
    prompts = [unit.prompt for units, _ in dispatcher.calls for unit in units]
    assert all('"mode": "quick_pilot"' in prompt for prompt in prompts)
    assert all("Inspect at most 2 clearly relevant workspace files" in prompt for prompt in prompts)
    assert "023.txt" in prompts[0]
    assert "024.txt" not in prompts[0]


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
async def test_failed_participants_make_the_chair_note_partial_and_count_roles(
    tmp_path: Path,
) -> None:
    repo = FakeRepo(_cycle())

    class PartialDispatcher:
        def __init__(self) -> None:
            self.calls = []

        async def __call__(self, units, *, budget):
            self.calls.append((units, budget))
            if len(self.calls) < 3:
                return [
                    DispatchOutcome(
                        unit_id=unit.unit_id,
                        text=None,
                        error="Codex API response.failed: upstream_overloaded",
                    )
                    for unit in units
                ]
            text = json.dumps(
                {
                    "status": "needs_input",
                    "summary": "Only project context was available to the chair.",
                    "artifact_refs": [],
                    "claims": [],
                    "evidence_refs": [],
                    "limitations": ["Both debating participants failed."],
                    "quality_checks": [],
                    "recommended_next_actions": ["Ask the project owner."],
                    "provenance": {"inputs_examined": ["cycle metadata"]},
                    "clarification_question": "Which benchmark should govern the cycle?",
                }
            )
            return [DispatchOutcome(unit_id=unit.unit_id, text=text) for unit in units]

    result = await _design_adapter(repo, PartialDispatcher()).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Start the Design meeting.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert "1 independent position(s) and one red team" in result.note
    assert "2 of 2 returned no usable result" in result.note
    assert "partial synthesis" in result.note
    assert "2 independent" not in result.note


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
async def test_a_provider_outage_does_not_create_a_conclusion_deck_or_feedback_surface(
    tmp_path: Path,
) -> None:
    """A failed chair record is an audit event, not a meeting outcome."""
    repo = FakeRepo(_cycle())
    dispatcher = FakeDispatcher(error=("Codex API error: server_is_overloaded: Our servers are currently overloaded."))

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Start the Design meeting.",
        state={},
        config={
            **_runtime_config(tmp_path),
            "context": {
                **_runtime_config(tmp_path)["context"],
                "dbtl_council_depth": "light",
            },
        },
    )

    assert result.worker_count == 3
    assert not result.produced_usable_evidence
    assert result.artifact_uri is None
    assert result.clarification_question is None
    assert result.deck_uri is None
    assert result.feedback_surface_id is None
    assert repo.surfaces == []
    assert all(item["status"] == "failed" for item in repo.recorded[0]["results"])
    assert not list(tmp_path.rglob("design-review-*.md"))
    assert not list(tmp_path.rglob("design-slides-*.html"))
    assert "none produced usable evidence" in result.note
    assert "server_is_overloaded" in result.note


class _FailedDebateMalformedChairDispatcher:
    """Match a live Light run: both debaters fail and the chair returns prose."""

    def __init__(self) -> None:
        self.calls = []

    async def __call__(self, units, *, budget):
        self.calls.append((units, budget))
        if len(self.calls) < 3:
            return [
                DispatchOutcome(
                    unit_id=unit.unit_id,
                    text=None,
                    error="The configured LLM provider rejected the request.",
                )
                for unit in units
            ]
        return [
            DispatchOutcome(
                unit_id=unit.unit_id,
                text=('{"status":"completed","summary":"Pilot synthesis","artifact_refs":[{"path":"not-a-string"}]}'),
            )
            for unit in units
        ]


@pytest.mark.asyncio
async def test_light_cannot_turn_a_failed_debate_and_malformed_chair_into_a_conclusion(
    tmp_path: Path,
) -> None:
    """Recoverable chair prose is insufficient when nobody completed the debate."""
    repo = FakeRepo(_cycle())
    dispatcher = _FailedDebateMalformedChairDispatcher()
    config = _runtime_config(tmp_path)
    config["context"]["dbtl_council_depth"] = "light"

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft a quick pilot design.",
        state={},
        config=config,
    )

    assert result.worker_count == 3
    assert not result.produced_usable_evidence
    assert result.artifact_uri is None
    assert result.deck_uri is None
    assert result.feedback_surface_id is None
    assert repo.surfaces == []
    assert not list(tmp_path.rglob("design-review-*.md"))
    assert not list(tmp_path.rglob("design-slides-*.html"))


class _CappedPilotDispatcher:
    """Reproduce the live failure: capped positions and prose from the chair."""

    def __init__(self) -> None:
        self.calls = []

    async def __call__(self, units, *, budget):
        self.calls.append((units, budget))
        text = (
            _structured_result()
            if len(self.calls) < 3
            else ("Use the cycle's stated simulation objective and success criterion as the pilot design. Treat unavailable packages and input files as assumptions to resolve during Data reconciliation.")
        )
        return [
            DispatchOutcome(
                unit_id=unit.unit_id,
                text=text,
                stop_reason="token_capped",
            )
            for unit in units
        ]


@pytest.mark.asyncio
async def test_light_pilot_turns_capped_prose_into_an_explicitly_limited_review_package(
    tmp_path: Path,
) -> None:
    """Light is allowed to produce a draft where strict evidence cannot.

    The fallback is a new, server-attributed result rather than laundering the
    capped worker as trustworthy. Its source cap and missing inputs remain
    visible for the person deciding whether this pilot may proceed.
    """
    repo = FakeRepo(_cycle())
    dispatcher = _CappedPilotDispatcher()
    config = _runtime_config(tmp_path)
    config["context"]["dbtl_council_depth"] = "light"

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft a quick pilot design.",
        state={},
        config=config,
    )

    assert result.produced_usable_evidence
    assert result.artifact_uri
    assert repo.recorded[0]["artifact_type"] == "design_brief"
    chair = repo.recorded[0]["results"][-1]
    assert chair["status"] == "completed"
    assert chair["agent_name"] == "system:light-pilot-fallback"
    assert chair["is_trustworthy"] is True
    assert chair["provenance"]["source_stop_reason"] == "token_capped"
    assert any("pilot fallback" in item.lower() for item in chair["limitations"])

    document = tmp_path / result.artifact_uri.removeprefix("/mnt/user-data/")
    package = json.loads(next(document.parent.glob("design-package-rev3-*.json")).read_text())
    assert package["pilot_review"]["strict_evidence_complete"] is False
    assert package["pilot_review"]["preexisting_data_required"] is False
    assert "Pilot Design package" in document.read_text()


@pytest.mark.asyncio
async def test_medium_keeps_rejecting_the_same_capped_prose(
    tmp_path: Path,
) -> None:
    repo = FakeRepo(_cycle())
    dispatcher = _CappedPilotDispatcher()
    config = _runtime_config(tmp_path)
    config["context"]["dbtl_council_depth"] = "medium"

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the full Design package.",
        state={},
        config=config,
    )

    assert not result.produced_usable_evidence
    assert result.artifact_uri is None
    assert repo.recorded[0]["artifact_type"] is None


@pytest.mark.asyncio
async def test_light_pilot_does_not_invent_a_review_package_when_the_chair_returns_no_output(
    tmp_path: Path,
) -> None:
    repo = FakeRepo(_cycle())
    config = _runtime_config(tmp_path)
    config["context"]["dbtl_council_depth"] = "light"

    result = await _design_adapter(
        repo,
        FakeDispatcher(error="sandbox and project tools unavailable"),
    ).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft a quick pilot design.",
        state={},
        config=config,
    )

    assert not result.produced_usable_evidence
    assert result.artifact_uri is None
    assert result.deck_uri is None
    assert repo.surfaces == []
    assert repo.recorded[0]["results"][-1]["status"] == "failed"


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
    assert repo.recorded[0]["stage_spec_key"] == "generic:build:v2"
    assert len(repo.lineage) == 1
    assert repo.lineage[0]["expected_db_revision"] == 4
    assert repo.lineage[0]["output_artifacts"][0]["content_hash"]
    assert repo.lineage[0]["code_revision"] == "workspace:unversioned"
    assert repo.lineage[0]["deviations"]


def test_build_discovers_and_hashes_the_workspace_input_reported_by_a_worker(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    source = uploads / "tiny.csv"
    source.write_text("height_cm,yield_g\n10,2\n", encoding="utf-8")
    snapshot = _project_file_snapshot(str(tmp_path))
    worker = SimpleNamespace(
        provenance={"inputs_examined": ["/mnt/user-data/uploads/tiny.csv"]},
        evidence_refs=(),
    )

    artifacts = _build_input_artifacts(
        datasets=(),
        results=(worker,),
        project_root=str(tmp_path),
        pre_run_files=snapshot,
    )

    assert artifacts == [f"workspace_file:uploads/tiny.csv:sha256:{hashlib.sha256(source.read_bytes()).hexdigest()}"]


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


def _design_adapter(
    repo: FakeRepo,
    dispatcher: FakeDispatcher,
    *,
    intent_interpreter=None,
) -> LiveStageAdapter:
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
        intent_interpreter=intent_interpreter,
    )


class FakeIntentInterpreter:
    """Records the prompts it saw and replies with a fixed verdict."""

    def __init__(self, reply: str = "HOLD", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.reply


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


@pytest.mark.parametrize(
    "request_text",
    [
        "restart the meeting",
        "Restart the design meeting with the drought sites in scope.",
        "Please restart the debate.",
        "Can you relaunch the council?",
        "retry the meeting",
        "rerun the meeting",
        "re-open the discussion",
        "redo the round",
        "hold another meeting",
        "start a new debate",
        "debate it again",
        # Common one-slip typos of "restart", same precedent as the
        # classifier's narrowly recognized "similate" misspelling.
        "restat the meeting",
        "Restar the meeting please.",
        "restrat the design meeting",
        "retsart the meeting",
        "rstart the debate",
        "resart the council",
        "retart the meeting",
    ],
)
def test_asking_to_restart_the_meeting_counts_as_a_new_debate(request_text: str) -> None:
    """ "Restart" and its neighbours are how people actually ask for a re-run."""
    assert _wants_new_debate(request_text)


@pytest.mark.parametrize(
    "request_text",
    [
        "How does this handle the drought sites?",
        "The gateway restarted during the meeting.",
        "Please restart the gateway.",
        "We should retry the field trial next season.",
        "Could you restate the discussion outcome?",
        "restat the gateway",
        "",
    ],
)
def test_ordinary_requests_do_not_convene_a_meeting(request_text: str) -> None:
    assert not _wants_new_debate(request_text)


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
async def test_a_paraphrased_rerun_request_convenes_via_the_interpreter(tmp_path: Path) -> None:
    """The record keeps the owner's words verbatim; interpretation absorbs the errors.

    A phrasing (or typo) the deterministic pattern never anticipated still
    convenes when the intent interpreter reads it as a re-run request.
    """
    repo = FakeRepo(_with_design_package(_cycle()))
    dispatcher = FakeDispatcher(text=_structured_result())
    interpreter = FakeIntentInterpreter(reply="CONVENE")

    request_text = "That meeting died on the provider outage — give it anothr go."
    result = await _design_adapter(repo, dispatcher, intent_interpreter=interpreter).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text=request_text,
        state={},
        config=_runtime_config(tmp_path),
    )

    assert dispatcher.calls
    assert result.worker_count == 3
    # The interpreter was shown the owner's original words, unrepaired.
    assert any(request_text in prompt for prompt in interpreter.prompts)


@pytest.mark.asyncio
async def test_the_interpreter_holding_keeps_the_design_on_the_table(tmp_path: Path) -> None:
    repo = FakeRepo(_with_design_package(_cycle()))
    dispatcher = FakeDispatcher(text=_structured_result())
    interpreter = FakeIntentInterpreter(reply="HOLD")

    result = await _design_adapter(repo, dispatcher, intent_interpreter=interpreter).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="How does this handle the drought sites?",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert dispatcher.calls == []
    assert result.worker_count == 0
    assert "design-review-rev3-abc123.md" in result.note


@pytest.mark.asyncio
async def test_an_interpreter_failure_fails_soft_to_holding(tmp_path: Path) -> None:
    """Routing must never depend on provider health; an outage holds, not crashes."""
    repo = FakeRepo(_with_design_package(_cycle()))
    dispatcher = FakeDispatcher(text=_structured_result())
    interpreter = FakeIntentInterpreter(error=RuntimeError("provider overloaded"))

    result = await _design_adapter(repo, dispatcher, intent_interpreter=interpreter).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="please give the meeting anothr go",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert dispatcher.calls == []
    assert result.worker_count == 0
    assert "design-review-rev3-abc123.md" in result.note


@pytest.mark.asyncio
async def test_a_gibberish_interpreter_reply_holds(tmp_path: Path) -> None:
    """Only an explicit CONVENE verdict spends a council's budget."""
    repo = FakeRepo(_with_design_package(_cycle()))
    dispatcher = FakeDispatcher(text=_structured_result())
    interpreter = FakeIntentInterpreter(reply="Well, it depends on what the owner meant...")

    result = await _design_adapter(repo, dispatcher, intent_interpreter=interpreter).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="hmm maybe we shold think about it more",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert dispatcher.calls == []
    assert result.worker_count == 0


@pytest.mark.asyncio
async def test_a_deterministic_match_never_consults_the_interpreter(tmp_path: Path) -> None:
    """The phrase check is the fast path; a literal match spends no model call."""
    repo = FakeRepo(_with_design_package(_cycle()))
    dispatcher = FakeDispatcher(text=_structured_result())
    interpreter = FakeIntentInterpreter(reply="HOLD")

    result = await _design_adapter(repo, dispatcher, intent_interpreter=interpreter).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Run the meeting again with the drought sites in scope.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert dispatcher.calls
    assert result.worker_count == 3
    assert interpreter.prompts == []


@pytest.mark.asyncio
async def test_a_change_request_reopens_the_debate_through_the_servers_own_kickoff(
    tmp_path: Path,
) -> None:
    """The verdict dispatches the round; a later message does not.

    ``changes_requested`` used to skip the hold entirely, on the grounds that a
    reviewer asking for changes *is* the request to argue again. That is true of
    the verdict and false of every message that arrives after it — a cycle in
    changes-requested convened a meeting for the word "hello". The review
    endpoint's kickoff is recognised deterministically instead, so an
    unavailable interpreter cannot cost a reviewer the round they asked for.
    """
    cycle = _with_design_package(_cycle(status="changes_requested"))
    repo = FakeRepo(cycle)
    dispatcher = FakeDispatcher(text=_structured_result())

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Refine the approved Design candidate for the reviewer's written objection.\n\nUse 4,000 individuals.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert dispatcher.calls
    assert result.worker_count == 3


@pytest.mark.asyncio
async def test_an_ordinary_message_to_a_changes_requested_cycle_convenes_nobody(
    tmp_path: Path,
) -> None:
    cycle = _with_design_package(_cycle(status="changes_requested"))
    dispatcher = FakeDispatcher(text=_structured_result())

    result = await _design_adapter(FakeRepo(cycle), dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Here is the design.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert dispatcher.calls == []
    assert result.worker_count == 0


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
            "agent_name": "experimental-design",
            "via_generalist": False,
            "status": "needs_input",
            "result": {
                "status": "needs_input",
                "summary": "The positions converge on one gating decision.",
                "clarification_question": "Toy benchmark or credible simulator?",
                "execution": {
                    "model": "gpt-5.5",
                    "max_tokens": 81_000,
                    "reasoning": "extended",
                },
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
    assert units[0].model == "gpt-5.5"
    assert units[0].max_tokens == 81_000
    assert units[0].reasoning == "extended"
    assert result.worker_count == 1
    assert result.produced_usable_evidence

    prompt = units[0].prompt
    # The owner's words travel verbatim, and so does the question they answer.
    assert "A credible simulator." in prompt
    assert "Toy benchmark or credible simulator?" in prompt
    assert "Argue for a matched-model benchmark." in prompt

    notes = repo.recorded[0]["results"]
    assert notes[0]["counts_toward_stage_output"] is True
    assert notes[0]["execution"] == {
        "model": "gpt-5.5",
        "max_tokens": 81_000,
        "token_limit_enforced": False,
        "reasoning": "extended",
    }


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
async def test_a_completed_round_writes_a_slide_deck_beside_the_review_package(
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


@pytest.mark.asyncio
async def test_a_selected_seat_model_is_pinned_on_the_subagent_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The UI label and the model that executes the seat must be the same."""
    from enum import Enum

    import deerflow.subagents as subagents_module
    import deerflow.subagents.executor as executor_module
    import deerflow.tools as tools_module

    captured: dict[str, object] = {}

    class FakeStatus(Enum):
        PENDING = "pending"
        COMPLETED = "completed"

    class FakeResult:
        def __init__(self, *, task_id, trace_id, status):
            self.task_id = task_id
            self.trace_id = trace_id
            self.status = status
            self.result = None
            self.error = None
            self.stop_reason = None
            self.token_usage_records = []
            self.usage_reported = True

    class CapturingExecutor:
        def __init__(self, *, config, parent_model, **kwargs):
            captured["config_model"] = config.model
            captured["parent_model"] = parent_model
            self.trace_id = "trace-seat-model"

        def execute(self, prompt, holder):
            holder.status = FakeStatus.COMPLETED
            holder.result = _structured_result()
            return holder

    monkeypatch.setattr(
        subagents_module,
        "get_subagent_config",
        lambda *args, **kwargs: SubagentConfig(
            name="experimental-design",
            description="designer",
            model="inherit",
        ),
    )
    monkeypatch.setattr(subagents_module, "SubagentExecutor", CapturingExecutor)
    monkeypatch.setattr(tools_module, "get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(executor_module, "SubagentResult", FakeResult)
    monkeypatch.setattr(executor_module, "SubagentStatus", FakeStatus)

    adapter = _design_adapter(FakeRepo(_cycle()), FakeDispatcher())
    config = _runtime_config(tmp_path)
    config["metadata"]["model_name"] = "gpt-5.6-sol"
    dispatcher = adapter._production_dispatcher(
        config=config,
        state={},
        project_id="project-1",
        project_root=str(tmp_path),
    )
    outcomes = await dispatcher(
        (
            WorkUnit(
                unit_id="seat-1",
                capability="experimental_design",
                agent_name="experimental-design",
                prompt="Return the design.",
                model="claude-opus-5",
            ),
        ),
        budget=resolve_stage_spec("design").budget,
    )

    assert outcomes[0].error is None
    assert captured["config_model"] == "claude-opus-5"
    assert captured["parent_model"] == "gpt-5.6-sol"


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


@pytest.mark.asyncio
async def test_a_wedged_roster_writer_does_not_swallow_the_meeting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The proposal runs in front of the preflight card.

    An unbounded wait there does not make the meeting slow, it makes it
    invisible: nothing is shown, nothing is dispatched, and cancelling is the
    only way out. The timeout degrades to capability selection, which is what
    ran before proposals existed.
    """
    import deerflow.agents.dbtl.stage_execution as module

    monkeypatch.setattr(module, "ROSTER_PROPOSAL_TIMEOUT_SECONDS", 0.05)

    async def never_answers(prompt: str) -> str:
        await asyncio.sleep(30)
        return "{}"

    repo = FakeRepo(_cycle())
    adapter = LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(),
        candidate_provider=lambda: (
            AgentCandidate(
                name="designer",
                capabilities=frozenset({Capability.EXPERIMENTAL_DESIGN}),
            ),
        ),
        roster_writer=never_answers,
    )

    plan = await asyncio.wait_for(
        adapter.preview_council(
            project_id="project-1",
            cycle_id="cycle-1",
            request_text="Draft the Design package.",
            config=_runtime_config(tmp_path),
        ),
        timeout=5,
    )

    assert plan is not None
    assert plan.dispatchable
    # Capability selection's seats, not the proposal's: no focus was written.
    assert [seat.focus for seat in plan.seats] == ["", "", ""]


@pytest.mark.asyncio
async def test_a_completed_round_registers_a_surface_bound_to_its_review_package(
    tmp_path: Path,
) -> None:
    """The descriptor is what later separates this deck from any other HTML."""
    cycle = _cycle()
    repo = FakeRepo(cycle)
    dispatcher = FakeDispatcher(text=_structured_result())

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert len(repo.surfaces) == 1
    surface = repo.surfaces[0]
    assert surface["deck_uri"] == result.deck_uri
    assert surface["stage_attempt_id"] == "attempt-design"
    assert surface["project_id"] == "project-1"
    # The hash is of the bytes actually written, so the descriptor and the file
    # on disk cannot describe different decks.
    deck = tmp_path / result.deck_uri.removeprefix("/mnt/user-data/")
    assert surface["deck_content_hash"] == hashlib.sha256(deck.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_a_completed_round_without_matchable_evidence_is_not_reviewable(
    tmp_path: Path,
) -> None:
    """Binding a verdict to an unconfirmed document is the bug this prevents.

    ``FakeRepo`` exposes no ``artifacts`` on the cycle, so the review package
    cannot be matched by hash. The surface must then decline ``stage_review``
    rather than name evidence nobody confirmed the deck was rendered from.
    """
    repo = FakeRepo(_cycle())
    dispatcher = FakeDispatcher(text=_structured_result())

    await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert repo.surfaces[0]["mode"] == "read_only"
    assert repo.surfaces[0]["evidence_artifact_id"] is None


@pytest.mark.asyncio
async def test_a_paused_meeting_registers_a_chair_feedback_surface(
    tmp_path: Path,
) -> None:
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
                "provenance": {"inputs_examined": []},
            }
        )
    )

    await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=_runtime_config(tmp_path),
    )

    surface = repo.surfaces[0]
    assert surface["mode"] == "chair_feedback"
    # A paused meeting has no package yet; a surface that claimed one would be
    # describing evidence that does not exist.
    assert surface["evidence_artifact_id"] is None


@pytest.mark.asyncio
async def test_a_failed_registration_does_not_cost_the_meeting(
    tmp_path: Path,
) -> None:
    """The results are already committed; a descriptor must not undo that.

    This inverts at cutover: once the deck is the only way to respond, an
    unregistered deck is an owner who cannot answer, and the failure has to
    become visible instead of silent.
    """
    repo = FakeRepo(_cycle())
    repo.surface_error = RuntimeError("descriptor store unavailable")
    dispatcher = FakeDispatcher(text=_structured_result())

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert repo.surfaces == []
    assert result.deck_uri is not None
    assert result.artifact_uri is not None


def test_evidence_matching_uses_the_keys_the_repository_actually_returns() -> None:
    """A silent rename here would disable the review path without failing anything.

    ``_bound_evidence`` reads an artifact's ``content_hash`` and ``uri`` out of a
    ``get_cycle`` payload. If either key moved, every completed round would
    quietly register ``read_only`` forever and no deck would ever be reviewable —
    a failure with no error, no log, and no test unless it is this one.
    """
    from deerflow.persistence.dbtl.cycles import DbtlCycleRepository

    # The exact projection the repository builds for an artifact row.
    row = SimpleNamespace(
        id="artifact-1",
        stage_attempt_id="attempt-design",
        artifact_type="design_brief.v2",
        revision=2,
        content_hash="d" * 64,
        uri="/mnt/user-data/outputs/dbtl/x/design/design-review-rev2-dddddd.md",
        created_by="user-1",
        created_at=None,
    )
    payload = DbtlCycleRepository._cycle_payload(
        SimpleNamespace(
            id="cycle-1",
            project_id="project-1",
            parent_cycle_id=None,
            title="Drought",
            cycle_class="computational",
            state="design",
            policy_version="v1",
            db_revision=3,
            projection_hash="e" * 64,
            projection_json={},
            created_by="user-1",
            created_at=None,
            updated_at=None,
        ),
        [],
        artifacts=[row],
    )

    matched = _bound_evidence(payload, artifact_uri=row.uri, content_hash=row.content_hash)

    assert matched is not None
    assert matched["id"] == "artifact-1"
    assert matched["revision"] == 2
    # A different document at the same path is not the evidence this deck showed.
    assert _bound_evidence(payload, artifact_uri=row.uri, content_hash="f" * 64) is None


@pytest.mark.asyncio
async def test_a_paused_deck_embeds_the_surface_id_it_is_registered_under(
    tmp_path: Path,
) -> None:
    """The bridge is only meaningful if the file and the row agree on the id."""
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

    surface = repo.surfaces[0]
    rendered = (tmp_path / result.deck_uri.removeprefix("/mnt/user-data/")).read_text()
    assert surface["surface_id"].startswith("dfs-")
    assert surface["surface_id"] in rendered
    assert "deerflow-design-deck" in rendered


@pytest.mark.asyncio
async def test_a_read_only_deck_carries_no_bridge_at_all(tmp_path: Path) -> None:
    """The safest 'cannot answer' is a file with no code that could."""
    repo = FakeRepo(_cycle())
    dispatcher = FakeDispatcher(text=_structured_result())
    config = _runtime_config(tmp_path)
    config["context"].pop("thread_id")
    config["configurable"].pop("thread_id")

    result = await _design_adapter(repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=config,
    )

    assert repo.surfaces[0]["mode"] == "read_only"
    rendered = (tmp_path / result.deck_uri.removeprefix("/mnt/user-data/")).read_text()
    assert "deerflow-design-deck" not in rendered
    assert "submit_intent" not in rendered


@pytest.mark.asyncio
async def test_a_retried_execution_reuses_one_surface_identity(tmp_path: Path) -> None:
    """A derived id means a retry re-renders the same bytes, not a rival surface."""
    dispatcher = FakeDispatcher(text=_structured_result())

    first_repo = FakeRepo(_cycle())
    await _design_adapter(first_repo, dispatcher).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=_runtime_config(tmp_path),
    )

    second_repo = FakeRepo(_cycle())
    await _design_adapter(second_repo, FakeDispatcher(text=_structured_result())).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Draft the Design package.",
        state={},
        config=_runtime_config(tmp_path),
    )

    assert first_repo.surfaces[0]["surface_id"] == second_repo.surfaces[0]["surface_id"]
