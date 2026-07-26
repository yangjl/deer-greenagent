"""Phase 6 live stage execution through the existing subagent runtime."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.agents.dbtl.stage_execution import LiveStageAdapter
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_runner import DispatchOutcome


def _cycle(*, state: str = "design", status: str = "in_progress", revision: int = 3) -> dict:
    stage_statuses = {
        "design": status if state == "design" else "approved",
        "reconciliation": (status if state == "reconciliation" else ("approved" if state in {"ready_for_build", "build", "test"} else "locked")),
        "build": (status if state in {"ready_for_build", "build"} else ("approved" if state == "test" else "locked")),
        "test": status if state == "test" else "locked",
        "learn": "locked",
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
        self.replay: dict | None = None

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
        }

    async def list_worker_runs(self, cycle_id: str, *, project_id: str, stage: str):
        return []

    async def get_stage_execution_replay(self, cycle_id: str, *, project_id: str, idempotency_key: str):
        return self.replay

    async def record_worker_runs(self, **kwargs):
        self.recorded.append(kwargs)
        self.cycle["db_revision"] += 1
        return kwargs["results"]

    async def record_build_lineage(self, **kwargs):
        self.lineage.append(kwargs)
        return kwargs


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
    assert "independent council positions" in dispatcher.calls[2][0][0].prompt


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
