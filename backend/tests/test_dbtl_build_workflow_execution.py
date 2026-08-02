"""The Build workflow, end to end, against the real repository.

Two things are only provable here and nowhere else.

**The writer and the read model must agree.** A step is opened against material
the projection later recomputes; if the two disagree by one field, every step is
reported stale the instant it succeeds and the read model's answer is worse than
no answer. A fake repository would compute both sides from the same stub and
prove nothing, so these tests drive the real SQLite schema through the real
adapter.

**A presentational failure must not take the sandbox work with it.** That is the
whole reason the workflow exists. The deck-failure test below is the guarantee:
execution stays selected, the deck alone is where a retry resumes.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from deerflow.agents.dbtl.live_stage import adapter as adapter_module
from deerflow.agents.dbtl.live_stage.adapter import LiveStageAdapter
from deerflow.config.database_config import DatabaseConfig
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.build_workflow import BUILD_WORKFLOW_V1, BuildErrorCode, BuildStepKey, StepState
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.reconciliation_policy import reconciliation_required
from deerflow.dbtl.stage_runner import DispatchOutcome
from deerflow.persistence.dbtl import DbtlCycleRepository
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository

pytestmark = pytest.mark.asyncio

DESIGN_BODY = "# Approved design\n\nFit a genomic prediction model and report held-out accuracy.\n"
DESIGN_HASH = hashlib.sha256(DESIGN_BODY.encode("utf-8")).hexdigest()
DESIGN_RELATIVE = "outputs/dbtl/design/design-rev1.md"
DESIGN_URI = f"/mnt/user-data/{DESIGN_RELATIVE}"
DATA_HASH = "a" * 64


@pytest_asyncio.fixture(autouse=True)
async def _close_test_engine():
    yield
    await close_engine()


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64

SINGLE_PHASE_PLAN = json.dumps(
    {
        "feasibility": "single_phase",
        "rationale": "The work is one script.",
        "phases": [{"phase_key": "build", "title": "Implement", "objective": "Fit the model.", "capability": "software_and_workflow_engineering"}],
    }
)

TWO_PHASE_PLAN = json.dumps(
    {
        "feasibility": "planned",
        "rationale": "Simulate, then fit.",
        "phases": [
            {"phase_key": "simulate", "title": "Simulate", "objective": "Generate the training set.", "capability": "software_and_workflow_engineering"},
            {"phase_key": "fit", "title": "Fit", "objective": "Fit the model on the simulated data.", "capability": "statistical_analysis"},
        ],
    }
)


def _build_result(*, artifact: str, figure: str) -> str:
    return json.dumps(
        {
            "status": "completed",
            "summary": "Implemented and executed the approved design.",
            "artifact_refs": [artifact, figure],
            "claims": ["The pipeline runs end to end on the declared inputs."],
            "evidence_refs": [{"kind": "workspace_file", "reference": artifact, "description": "Fitted model."}],
            "limitations": [],
            "quality_checks": [{"name": "execution completed", "passed": True, "detail": ""}],
            "recommended_next_actions": ["Review the build."],
            "figures": [{"path": figure, "caption": "Held-out accuracy", "shows": "Predicted against observed yield."}],
            "key_outcomes": [{"name": "Held-out accuracy", "value": 0.62, "unit": "r"}],
            "provenance": {
                "inputs_examined": ["/mnt/user-data/yield.csv"],
                "recorded_rerun_procedure": "uv run python fit.py --seed 7",
            },
        }
    )


class _WritingDispatcher:
    """Answers a build unit by writing real files, and a summarizer by reading.

    The adapter refuses a Build result whose artifacts it cannot find and hash,
    so a dispatcher that only returns JSON would never reach the step boundary
    these tests are about. The summarizer branch exists because a read-only seat
    has no grant at all — asking it for one is how a summarizer that could write
    would be noticed.
    """

    def __init__(self, *, summary: str | None = None, plan: str | None = None) -> None:
        self.calls: list[tuple] = []
        self.summarizer_units: list = []
        self.planner_units: list = []
        self.phase_units: list = []
        self._summary = summary
        self._plan = plan

    async def __call__(self, units, *, budget):
        self.calls.append((tuple(units), budget))
        outcomes = []
        for unit in units:
            if unit.role == "planner":
                self.planner_units.append(unit)
                outcomes.append(DispatchOutcome(unit_id=unit.unit_id, text=self._plan if self._plan is not None else SINGLE_PHASE_PLAN))
                continue
            if unit.role == "summarizer":
                self.summarizer_units.append(unit)
                outcomes.append(DispatchOutcome(unit_id=unit.unit_id, text=self._summary if self._summary is not None else _summary_for(unit.prompt)))
                continue
            if unit.role == "phase":
                self.phase_units.append(unit)
            grant = _grant_from_prompt(unit.prompt)
            grant.mkdir(parents=True, exist_ok=True)
            produced = grant / "model.bin"
            produced.write_text("fitted", encoding="utf-8")
            plot = grant / "accuracy.png"
            plot.write_bytes(PNG)
            outcomes.append(
                DispatchOutcome(
                    unit_id=unit.unit_id,
                    text=_build_result(artifact=_virtual(produced), figure=_virtual(plot)),
                )
            )
        return outcomes


def _virtual(path: Path) -> str:
    return f"/mnt/user-data/{path.relative_to(_PROJECT_ROOT[0]).as_posix()}"


def _summary_for(prompt: str) -> str:
    """A summary that cites exactly the figures the bundle actually offered."""
    bundle = json.loads(prompt.split("Bundle:", 1)[1].strip())
    figures = bundle["execution_bundle"]["figures"]
    return json.dumps(
        {
            "headline": "The model fits and generalizes to the held-out site.",
            "key_outcomes": [{"name": "Held-out accuracy", "value": 0.62, "unit": "r"}],
            "figures": [{"path": figures[0]["path"], "reading": "Predictions track observations across the range."}] if figures else [],
            "phases": [{"title": "Fit", "text": "Trained on the declared inputs."}],
            "deviations": [],
            "limitations": [],
            "rerun_procedure": "uv run python fit.py --seed 7",
        }
    )


#: One-element box so the dispatcher above can map its grant back to a host path
#: without the test threading a root through the adapter's prompt.
_PROJECT_ROOT: list[Path] = [Path("/")]


def _grant_from_prompt(prompt: str) -> Path:
    """The grant the adapter bound into this unit's prompt.

    The path is embedded in a JSON context, so it is delimited by quotes and
    braces as well as whitespace — a naive scan to the next space picks up a
    trailing `"}` and the adapter then refuses the artifact as outside the
    grant, which is exactly the containment check working correctly.
    """
    # Anchored on the sentence that *grants* the directory. An unanchored scan
    # matches the first stage-work path in the prompt, which for a later phase is
    # a preceding phase's output — a path this unit may read and must not write.
    match = re.search(r"execution log under (/mnt/user-data/outputs/\.dbtl-stage-work/[A-Za-z0-9._/-]+?)[.\s\"]", prompt) or re.search(r"(/mnt/user-data/outputs/\.dbtl-stage-work/[A-Za-z0-9._/-]+)", prompt)
    assert match is not None, "the adapter did not bind a writable grant into the prompt"
    return _PROJECT_ROOT[0] / match.group(1)[len("/mnt/user-data/") :]


async def _revision(repo: DbtlCycleRepository) -> int:
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    return int(cycle["db_revision"])


async def _approve_design(repo: DbtlCycleRepository, *, content_hash: str) -> None:
    await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="design",
        artifact_type="design_brief.v2",
        uri=DESIGN_URI,
        content_hash=content_hash,
        created_by="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="artifact-design",
    )
    await repo.submit_stage_for_review(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="design",
        expected_db_revision=await _revision(repo),
        actor_user_id="user-1",
        idempotency_key="submit-design",
    )
    await repo.review_stage(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="design",
        decision="approve",
        rationale="The design is sound.",
        expected_db_revision=await _revision(repo),
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        idempotency_key="review-design",
    )


async def _ready_for_build(repo: DbtlCycleRepository, *, design_hash: str = DESIGN_HASH) -> None:
    """Walk the cycle to the point where Build is the executable stage.

    Reconciliation is only walked when this deployment requires it: with
    `dbtl.reconciliation_required` off, an approved Design opens Build directly
    and the reconciliation stage is legitimately closed, so submitting evidence
    to it is refused. Reading the policy rather than assuming one keeps this
    fixture honest under either setting.
    """
    await _approve_design(repo, content_hash=design_hash)
    if not reconciliation_required():
        return
    await repo.declare_dataset(
        cycle_id="cycle-1",
        project_id="project-1",
        source_key="yield",
        uri="/mnt/user-data/yield.csv",
        content_hash=DATA_HASH,
        recorded_by="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="dataset",
    )
    row = await repo.open_reconciliation_row(
        cycle_id="cycle-1",
        project_id="project-1",
        check="units_and_encoding",
        field_name="Yield units",
        created_by="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="row",
    )
    await repo.decide_reconciliation_row(
        row_id=row["id"],
        project_id="project-1",
        status="resolved",
        resolution="Converted to Mg/ha.",
        actor_type="human",
        actor_user_id="reviewer-1",
        expected_db_revision=await _revision(repo),
        expected_work_item_revision=row["db_revision"],
        idempotency_key="row-decision",
    )
    await repo.attach_artifact(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="reconciliation",
        artifact_type="reconciliation_report",
        uri="/mnt/user-data/outputs/reconciliation.json",
        content_hash="b" * 64,
        created_by="user-1",
        expected_db_revision=await _revision(repo),
        idempotency_key="artifact-reconciliation",
    )
    await repo.submit_stage_for_review(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="reconciliation",
        expected_db_revision=await _revision(repo),
        actor_user_id="user-1",
        idempotency_key="submit-reconciliation",
    )
    await repo.review_stage(
        cycle_id="cycle-1",
        project_id="project-1",
        stage="reconciliation",
        decision="approve",
        rationale="Every judgement row is settled.",
        expected_db_revision=await _revision(repo),
        reviewer_user_id="reviewer-1",
        reviewer_project_role="owner",
        idempotency_key="review-reconciliation",
    )


@pytest_asyncio.fixture
async def project(tmp_path: Path) -> tuple[DbtlCycleRepository, Path]:
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path / "db")))
    sf = get_session_factory()
    assert sf is not None
    workspaces = WorkspaceRepository(sf)
    workspace = await workspaces.create_workspace(workspace_id="ws-1", name="Maize", slug="maize", description=None, created_by="user-1")
    await workspaces.create_project(
        project_id="project-1",
        workspace_id=workspace["id"],
        name="Drought",
        slug="drought",
        description=None,
        crop_profile="maize",
        created_by="user-1",
    )
    repo = DbtlCycleRepository(sf)
    await repo.create_cycle(
        cycle_id="cycle-1",
        project_id="project-1",
        title="Drought model",
        cycle_class="computational",
        research_question="Does the model generalize?",
        objective="Test an independent population",
        success_criteria="Accuracy >= 0.7",
        created_by="user-1",
        policy_version="greenagent-dbtl-v2-draft",
        idempotency_key="create",
    )
    root = tmp_path / "workspace"
    design = root / DESIGN_RELATIVE
    design.parent.mkdir(parents=True, exist_ok=True)
    design.write_text(DESIGN_BODY, encoding="utf-8")
    (root / "yield.csv").write_text("id,yield\n1,4.2\n", encoding="utf-8")
    _PROJECT_ROOT[0] = root
    return repo, root


#: What a real deployment looks like: one declared specialist plus the
#: registered generalist. The generalist has to be here, because it is the only
#: agent allowed to stand in for a capability nobody declared — the tests that
#: leave it out are asserting what happens when nothing can cover a phase.
CANDIDATES = (
    AgentCandidate(name="builder", capabilities=frozenset({Capability.SOFTWARE_ENGINEERING})),
    AgentCandidate(name="general-purpose", capabilities=frozenset()),
)


def _adapter(repo: DbtlCycleRepository, *, workflow: bool, dispatcher, candidates=CANDIDATES) -> LiveStageAdapter:
    return LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(dbtl=SimpleNamespace(build_workflow_steps=workflow)),
        candidate_provider=lambda: candidates,
        dispatcher=dispatcher,
    )


def _runtime(root: Path, *, run_id: str = "run-1") -> dict:
    return {
        "context": {
            "project_id": "project-1",
            "project_root": str(root),
            "thread_id": "thread-1",
            "run_id": run_id,
            "user_id": "user-1",
        },
        "configurable": {"thread_id": "thread-1"},
        "metadata": {"model_name": "test-model"},
    }


async def _build_stage_attempt_id(repo: DbtlCycleRepository) -> str:
    cycle = await repo.get_cycle("cycle-1", project_id="project-1")
    assert cycle is not None
    return str(next(stage for stage in cycle["stages"] if stage["stage"] == "build")["id"])


async def _run_build(repo: DbtlCycleRepository, root: Path, *, workflow: bool = True, dispatcher=None, candidates=CANDIDATES, run_id: str = "run-1"):
    """One Build request.

    `run_id` matters whenever a test runs Build twice: the stage-execution
    idempotency key is bound to the run, so a second request under the same id
    replays the whole recorded stage and never reaches the workflow at all.
    A test about step-level resume that reuses `run-1` proves nothing.
    """
    dispatcher = dispatcher or _WritingDispatcher()
    result = await _adapter(repo, workflow=workflow, dispatcher=dispatcher, candidates=candidates).execute(
        project_id="project-1",
        cycle_id="cycle-1",
        request_text="Build the approved design.",
        state={},
        config=_runtime(root, run_id=run_id),
    )
    return result, dispatcher


def _step(view: dict, key: BuildStepKey) -> dict:
    return next(entry for entry in view["steps"] if entry["key"] == key.value)


class TestTheWriterAndTheReadModelAgree:
    async def test_a_successful_build_records_a_complete_chain(self, project) -> None:
        """The property a fake repository cannot show.

        If the material the adapter opens each step against differed from the
        material the projection recomputes, every step here would come back
        `invalidated` while having plainly just succeeded.
        """
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        result, _dispatcher = await _run_build(repo, root)
        assert result.stage == "build"
        assert result.produced_usable_evidence, result.note

        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        assert view["workflow_spec_key"] == BUILD_WORKFLOW_V1.spec_key
        assert view["staleness_checked"] is True
        assert [entry["status"] for entry in view["steps"]] == [StepState.SUCCEEDED.value] * 5
        assert view["next_step"] is None
        assert view["is_complete"] is True
        # Nothing was reported stale, which is the same statement made from the
        # other direction and the one that broke when the two sides disagreed.
        assert all(not entry["invalidated_attempts"] for entry in view["steps"])

    async def test_load_design_binds_the_document_that_was_approved(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        await _run_build(repo, root)

        load = _step(await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id), BuildStepKey.LOAD_DESIGN)
        assert load["attempts"][0]["execution"]["design_revision"] == 1
        assert load["attempts"][0]["execution"]["design_truncated"] is False

    async def test_the_worker_is_handed_the_design_rather_than_asked_to_find_it(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        _result, dispatcher = await _run_build(repo, root)

        prompt = dispatcher.calls[0][0][0].prompt
        assert "Fit a genomic prediction model" in prompt
        assert DESIGN_HASH in prompt


class TestAPresentationalFailureKeepsTheScience:
    async def test_a_failed_deck_leaves_execution_selected(self, project, monkeypatch) -> None:
        """The guarantee the whole workflow exists for.

        Before this, a deck that would not render meant re-running the Build.
        Here the deck alone is where a retry resumes: `next_step` names it, and
        the two steps before it are still selected successes.
        """
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        monkeypatch.setattr(adapter_module, "write_build_deck", lambda **_kwargs: None)

        result, _dispatcher = await _run_build(repo, root)
        assert result.produced_usable_evidence

        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        assert _step(view, BuildStepKey.EXECUTE_PHASES)["status"] == StepState.SUCCEEDED.value
        assert _step(view, BuildStepKey.SUMMARIZE_RESULTS)["status"] == StepState.SUCCEEDED.value
        deck = _step(view, BuildStepKey.RENDER_REVIEW_DECK)
        assert deck["status"] == StepState.FAILED.value
        assert deck["error_code"] == BuildErrorCode.DECK_RENDER_FAILED.value
        assert BuildErrorCode(deck["error_code"]).is_presentational
        assert view["next_step"] == BuildStepKey.RENDER_REVIEW_DECK.value

    async def test_a_rejected_worker_contract_fails_execution_not_the_design(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        class _ProseDispatcher:
            calls: list = []

            async def __call__(self, units, *, budget):
                return [DispatchOutcome(unit_id=unit.unit_id, text="I built it, trust me.") for unit in units]

        await _run_build(repo, root, dispatcher=_ProseDispatcher())

        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        assert _step(view, BuildStepKey.LOAD_DESIGN)["status"] == StepState.SUCCEEDED.value
        execute = _step(view, BuildStepKey.EXECUTE_PHASES)
        assert execute["status"] == StepState.FAILED.value
        assert execute["error_code"] == BuildErrorCode.EXECUTION_CONTRACT_REJECTED.value
        # The read model must be able to say where it stopped, not merely that
        # something is unfinished.
        assert view["next_step"] == BuildStepKey.EXECUTE_PHASES.value


class TestTheSummarizerWritesTheReviewedDocument:
    async def test_the_build_review_answers_what_we_got(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        result, dispatcher = await _run_build(repo, root)

        assert dispatcher.summarizer_units, "no summarizer was dispatched"
        markdown = (root / result.artifact_uri[len("/mnt/user-data/") :]).read_text(encoding="utf-8")
        assert "## Key outcomes" in markdown
        assert "Held-out accuracy" in markdown
        assert "Figures worth looking at" in markdown
        # The headline is what chat reports, rather than a file path.
        assert "generalizes to the held-out site" in result.note

    async def test_the_summarizer_seat_is_read_only(self, project) -> None:
        """Read-only is a property of the seat, not an instruction in a prompt.

        The production dispatcher withholds execution and write tools from this
        role and grants it no writable path; a seat that could write could turn a
        "summary" into a second execution nobody hashed.
        """
        from deerflow.agents.dbtl.live_stage.adapter import _READ_ONLY_ROLES, _READ_ONLY_TOOL_NAMES, _tools_for_unit

        repo, root = project
        await _ready_for_build(repo)
        _result, dispatcher = await _run_build(repo, root)
        (unit,) = dispatcher.summarizer_units

        assert unit.role in _READ_ONLY_ROLES
        tools = [SimpleNamespace(name=name) for name in ("read_file", "bash", "write_file", "str_replace", "grep")]
        assert {tool.name for tool in _tools_for_unit(tools, unit)} == {"read_file", "grep"} <= _READ_ONLY_TOOL_NAMES

    async def test_a_summarizer_that_invents_a_figure_fails_only_its_own_step(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        invented = json.dumps({"headline": "Fitted.", "figures": [{"path": "/mnt/user-data/outputs/imaginary.png"}]})

        result, _dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(summary=invented))

        assert not result.produced_usable_evidence
        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        assert _step(view, BuildStepKey.EXECUTE_PHASES)["status"] == StepState.SUCCEEDED.value
        summary = _step(view, BuildStepKey.SUMMARIZE_RESULTS)
        assert summary["status"] == StepState.FAILED.value
        assert summary["error_code"] == BuildErrorCode.SUMMARY_CONTRACT_REJECTED.value
        assert "did not verify" in summary["attempts"][0]["error_summary"]

    async def test_the_build_deck_embeds_its_figures(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        result, _dispatcher = await _run_build(repo, root)

        assert result.deck_uri
        deck = (root / result.deck_uri[len("/mnt/user-data/") :]).read_text(encoding="utf-8")
        assert "data:image/png;base64," in deck
        assert "Key outcomes" in deck
        # The Build deck, not the meeting deck: a Build result nobody argued
        # about has no positions to render.
        assert "participants" not in deck.lower()


class TestABuildStopsBeingOneOpaqueWorker:
    async def test_each_planned_phase_is_its_own_attempt(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        _result, dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN))

        assert [unit.prompt.count("Phase 1 of this build") for unit in dispatcher.phase_units] == [1, 0]
        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        phases = view["phases"]
        assert [entry["phase_key"] for entry in phases] == ["simulate", "fit"]
        assert [entry["phase_index"] for entry in phases] == [1, 2]
        assert {entry["status"] for entry in phases} == {StepState.SUCCEEDED.value}
        # Every phase binds the plan it sat in, so a replan invalidates them
        # rather than silently rebinding them to a different account of the work.
        assert len({entry["plan_digest"] for entry in phases}) == 1
        assert all(entry["plan_digest"] for entry in phases)

    async def test_a_phase_records_the_capability_and_who_covered_it(self, project) -> None:
        """A reviewer reading "statistical analysis: general-purpose" knows what
        they are looking at; a reviewer reading nothing does not."""
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN))

        phases = (await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id))["phases"]
        by_key = {entry["phase_key"]: entry for entry in phases}
        # The fixture registers one engineering specialist plus the generalist.
        assert by_key["simulate"]["capability"] == "software_and_workflow_engineering"
        assert by_key["simulate"]["via_generalist"] is False
        assert by_key["simulate"]["agent_name"] == "builder"
        # Nothing declares statistics, so the *registered generalist* covers it
        # and the stand-in is recorded. Never `builder`: an engineering
        # specialist is not a generalist, and handing it a statistics phase
        # while recording "covered by a generalist" is the swap this names.
        assert by_key["fit"]["capability"] == "statistical_analysis"
        assert by_key["fit"]["via_generalist"] is True
        assert by_key["fit"]["agent_name"] == "general-purpose"

    async def test_a_capability_nothing_can_cover_stops_the_plan_and_names_it(self, project) -> None:
        """No registered generalist means no stand-in, not an arbitrary agent."""
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        result, dispatcher = await _run_build(
            repo,
            root,
            dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN),
            candidates=(AgentCandidate(name="builder", capabilities=frozenset({Capability.SOFTWARE_ENGINEERING})),),
        )

        assert [unit.capability for unit in dispatcher.phase_units] == ["software_and_workflow_engineering"]
        assert "statistical_analysis" in result.note
        # The engineering phase that did run stays committed and reusable.
        phases = (await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id))["phases"]
        assert [entry["status"] for entry in phases] == [StepState.SUCCEEDED.value]
        assert not result.produced_usable_evidence

    async def test_a_later_phase_receives_the_outputs_of_the_ones_before_it(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        _result, dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN))

        second = dispatcher.phase_units[1].prompt
        assert "Outputs of the phases before this one" in second
        assert "simulate" in second
        assert "you may not modify them" in second

    async def test_a_failed_phase_stops_the_run_rather_than_spending_the_rest(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        class _SecondPhaseFails(_WritingDispatcher):
            async def __call__(self, units, *, budget):
                if units[0].role == "phase" and len(self.phase_units) >= 1:
                    self.phase_units.append(units[0])
                    return [DispatchOutcome(unit_id=units[0].unit_id, text="it did not work")]
                return await super().__call__(units, budget=budget)

        _result, dispatcher = await _run_build(repo, root, dispatcher=_SecondPhaseFails(plan=TWO_PHASE_PLAN))

        assert len(dispatcher.phase_units) == 2, "a third phase should never have been dispatched"
        phases = (await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id))["phases"]
        assert [entry["status"] for entry in phases] == [StepState.SUCCEEDED.value, StepState.FAILED.value]
        assert phases[1]["error_code"] == BuildErrorCode.EXECUTION_CONTRACT_REJECTED.value

    async def test_an_unplannable_build_still_runs_as_one_piece(self, project) -> None:
        """Losing the decomposition costs structure; failing here would cost the
        whole Build."""
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        result, dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan="I have thought about it at length."))

        assert result.produced_usable_evidence
        assert len(dispatcher.phase_units) == 1
        plan_step = _step(await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id), BuildStepKey.PLAN_BUILD)
        assert plan_step["status"] == StepState.SUCCEEDED.value
        assert plan_step["attempts"][0]["execution"] == {
            "feasibility": "single_phase",
            "phases": 1,
            "degraded": True,
            "phase_1_key": "build",
            "phase_1_title": "Implement the approved design",
        }

    async def test_a_planner_that_needs_a_decision_pauses_before_any_phase(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        question = json.dumps({"feasibility": "needs_input", "clarification_question": "Which year is the holdout?"})

        result, dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=question))

        assert dispatcher.phase_units == []
        # Raised as a control bound to `plan_build`, so the answer comes back to
        # the step that asked rather than to whatever the next request happens
        # to be.
        assert result.control_request is not None
        assert result.control_request["question"] == "Which year is the holdout?"
        assert result.control_request["step_key"] == BuildStepKey.PLAN_BUILD.value
        plan_step = _step(await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id), BuildStepKey.PLAN_BUILD)
        assert plan_step["status"] == StepState.NEEDS_INPUT.value

    async def test_a_pause_after_phase_stops_at_its_own_boundary(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        paused = json.loads(TWO_PHASE_PLAN)
        paused["phases"][0]["pause_after"] = True

        result, dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=json.dumps(paused)))

        assert [unit.capability for unit in dispatcher.phase_units] == ["software_and_workflow_engineering"]
        # A plan that stopped halfway is not the build a person would approve,
        # so nothing is written up — and the reply says how far it got.
        assert not result.produced_usable_evidence
        assert result.artifact_uri is None
        assert "1 of 2 planned build phase(s)" in result.note


class TestACommittedStepIsReplayedRatherThanReRun:
    """The point of the whole chain, and the thing it did not actually do.

    The recorder found a committed success and reported `replayed=True`; every
    caller dispatched anyway. So a Build whose deck failed re-ran the planner
    and every phase to recover a rendering bug — the resume was in the record
    and the re-run was in reality.
    """

    async def test_a_second_run_dispatches_no_planner_and_no_phase(self, project, monkeypatch) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        monkeypatch.setattr(adapter_module, "write_build_deck", lambda **_kwargs: None)

        await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-1")
        _result, second = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-2")

        assert second.planner_units == [], "the plan was already committed"
        assert second.phase_units == [], "both phases were already committed"
        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        assert [entry["status"] for entry in view["phases"]] == [StepState.SUCCEEDED.value] * 2
        # A replayed phase is already in the worker record; recording it again
        # violates the one-run-per-unit index and used to raise out of the run.
        workers = await repo.list_worker_runs("cycle-1", project_id="project-1", stage="build")
        assert len(workers) == len({entry["unit_id"] for entry in workers}) == 2

    async def test_the_replayed_evidence_is_the_evidence_the_first_run_published(self, project, monkeypatch) -> None:
        repo, root = project
        await _ready_for_build(repo)
        monkeypatch.setattr(adapter_module, "write_build_deck", lambda **_kwargs: None)

        first, _ = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-1")
        monkeypatch.undo()
        second, dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-2")

        assert first.produced_usable_evidence and second.produced_usable_evidence
        # No phase ran, and the deck still had real published figures to embed.
        assert dispatcher.phase_units == []
        assert second.deck_uri is not None

    async def test_an_unreadable_payload_costs_a_re_run_and_never_a_wrong_result(self, project, monkeypatch) -> None:
        """Fail-soft in the only safe direction."""
        repo, root = project
        await _ready_for_build(repo)
        monkeypatch.setattr(adapter_module, "write_build_deck", lambda **_kwargs: None)
        await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-1")

        for kept in (root / "outputs" / ".dbtl-stage-work" / "steps").rglob("*.json"):
            kept.write_text("{not json", encoding="utf-8")

        _result, second = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-2")

        assert len(second.phase_units) == 2, "an unreadable payload must dispatch, not resume"


class TestAPhaseSucceedsOnlyOnceItsOutputsArePublished:
    async def test_an_escaped_artifact_leaves_no_reusable_success(self, project) -> None:
        """Recording success from the worker's own JSON was the ordering bug.

        A phase naming a file outside its isolated workspace was settled as
        succeeded, and the reference was only checked afterwards — so the chain
        held a reusable success for work whose outputs the server had refused.
        """
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        escaped = root / "elsewhere.bin"
        escaped.write_text("not in the grant", encoding="utf-8")

        class _EscapesItsWorkspace(_WritingDispatcher):
            async def __call__(self, units, *, budget):
                if units[0].role == "phase":
                    self.phase_units.append(units[0])
                    return [DispatchOutcome(unit_id=units[0].unit_id, text=_build_result(artifact=_virtual(escaped), figure=_virtual(escaped)))]
                return await super().__call__(units, budget=budget)

        result, dispatcher = await _run_build(repo, root, dispatcher=_EscapesItsWorkspace(plan=TWO_PHASE_PLAN))

        assert len(dispatcher.phase_units) == 1, "the second phase must not run on a refused first phase"
        phases = (await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id))["phases"]
        assert [entry["status"] for entry in phases] == [StepState.FAILED.value]
        assert phases[0]["error_code"] == BuildErrorCode.EXECUTION_OUTPUT_MISSING.value
        assert not result.produced_usable_evidence

    async def test_a_refused_phase_is_re_run_rather_than_replayed(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        escaped = root / "elsewhere.bin"
        escaped.write_text("not in the grant", encoding="utf-8")

        class _EscapesOnce(_WritingDispatcher):
            def __init__(self, **kwargs) -> None:
                super().__init__(**kwargs)
                self.escaped = False

            async def __call__(self, units, *, budget):
                if units[0].role == "phase" and not self.escaped:
                    self.escaped = True
                    self.phase_units.append(units[0])
                    return [DispatchOutcome(unit_id=units[0].unit_id, text=_build_result(artifact=_virtual(escaped), figure=_virtual(escaped)))]
                return await super().__call__(units, budget=budget)

        await _run_build(repo, root, dispatcher=_EscapesOnce(plan=SINGLE_PHASE_PLAN), run_id="run-1")
        result, second = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=SINGLE_PHASE_PLAN), run_id="run-2")

        assert len(second.phase_units) == 1
        assert result.produced_usable_evidence

    async def test_a_retry_gets_a_workspace_of_its_own(self, project) -> None:
        """A retry that inherits the failed attempt's directory inherits its files."""
        repo, root = project
        await _ready_for_build(repo)

        class _FailsOnce(_WritingDispatcher):
            def __init__(self, **kwargs) -> None:
                super().__init__(**kwargs)
                self.failed = False

            async def __call__(self, units, *, budget):
                if units[0].role == "phase" and not self.failed:
                    self.failed = True
                    self.phase_units.append(units[0])
                    return [DispatchOutcome(unit_id=units[0].unit_id, text="it did not work")]
                return await super().__call__(units, budget=budget)

        _first, first_dispatcher = await _run_build(repo, root, dispatcher=_FailsOnce(plan=SINGLE_PHASE_PLAN), run_id="run-1")
        _second, second_dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=SINGLE_PHASE_PLAN), run_id="run-2")

        assert first_dispatcher.phase_units[0].unit_id != second_dispatcher.phase_units[0].unit_id
        assert _grant_from_prompt(first_dispatcher.phase_units[0].prompt) != _grant_from_prompt(second_dispatcher.phase_units[0].prompt)


class TestTheSeatsThatMayOnlyReadAreGivenNothingElse:
    async def test_neither_the_planner_nor_the_summarizer_may_act(self, project) -> None:
        """A contract promising "you write nothing, run nothing" is not a guarantee.

        The planner's prompt said exactly that while it held the full Build tool
        set, so the only thing stopping it from starting the build it was asked
        to plan was the sentence asking it not to.
        """
        repo, root = project
        await _ready_for_build(repo)

        _result, dispatcher = await _run_build(repo, root)

        assert dispatcher.planner_units and dispatcher.summarizer_units
        for unit in (*dispatcher.planner_units, *dispatcher.summarizer_units):
            assert adapter_module.STAGE_UNIT_WORKSPACE_PLACEHOLDER not in unit.prompt
            assert "/mnt/user-data/outputs/.dbtl-stage-work/" not in unit.prompt

    async def test_the_read_only_roles_are_named_from_the_modules_that_seat_them(self) -> None:
        assert adapter_module._READ_ONLY_ROLES == {"summarizer", "planner"}


class TestLoadDesignRefusesBeforeDispatch:
    async def test_a_design_that_moved_under_its_approval_stops_the_build(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        (root / DESIGN_RELATIVE).write_text("something else entirely", encoding="utf-8")

        result, dispatcher = await _run_build(repo, root)

        assert dispatcher.calls == []
        assert "no longer matches" in result.note
        assert not result.produced_usable_evidence
        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        load = _step(view, BuildStepKey.LOAD_DESIGN)
        assert load["status"] == StepState.FAILED.value
        assert load["error_code"] == BuildErrorCode.DESIGN_MISSING_OR_STALE.value
        assert view["next_step"] == BuildStepKey.LOAD_DESIGN.value

    async def test_an_unreadable_design_is_reported_as_unreadable(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        (root / DESIGN_RELATIVE).unlink()
        stage_attempt_id = await _build_stage_attempt_id(repo)

        _result, dispatcher = await _run_build(repo, root)

        assert dispatcher.calls == []
        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        assert _step(view, BuildStepKey.LOAD_DESIGN)["error_code"] == BuildErrorCode.DESIGN_UNREADABLE.value


class TestTheRolloutSwitchIsReal:
    async def test_with_the_flag_off_nothing_is_recorded_and_nothing_is_refused(self, project) -> None:
        """A new refusal on the default path is a behaviour change, not a rollout.

        With the switch off the design is never re-read, so a project whose
        approved package is not readable through the project root keeps running
        Build exactly as it did before this workflow existed.
        """
        repo, root = project
        await _ready_for_build(repo)
        (root / DESIGN_RELATIVE).unlink()
        stage_attempt_id = await _build_stage_attempt_id(repo)

        _result, dispatcher = await _run_build(repo, root, workflow=False)

        assert dispatcher.calls, "the Build was refused on the path the flag is supposed to leave alone"
        assert await repo.list_step_attempts(project_id="project-1", stage_attempt_id=stage_attempt_id) == []


class TestTheReadModelCanNameThePhases:
    """A plan a reader can check, rather than a count they cannot."""

    async def test_the_recorded_plan_travels_with_its_phase_titles(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN))

        plan = (await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id))["plan"]
        assert plan is not None
        assert plan["feasibility"] == "planned"
        assert [(entry["phase_key"], entry["title"]) for entry in plan["phases"]] == [("simulate", "Simulate"), ("fit", "Fit")]

    async def test_a_running_phase_carries_its_own_title(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN))

        phases = (await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id))["phases"]
        assert [row["execution"]["title"] for row in phases] == ["Simulate", "Fit"]

    async def test_a_build_that_never_planned_reports_no_plan(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        assert (await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id))["plan"] is None


class TestADeckRetryDoesNotReRunTheSummarizer:
    """The step boundary has to hold in both directions.

    A failed deck kept the execution selected, which was half the promise. The
    other half is that the write-up it retries against is the *same* write-up:
    re-running the summarizer produced a differently-hashed document (the review
    embeds the cycle revision), and the replayed step could no longer record it —
    so the deck rendered one package while the chain named another.
    """

    async def test_the_summarizer_is_not_dispatched_a_second_time(self, project, monkeypatch) -> None:
        repo, root = project
        await _ready_for_build(repo)
        monkeypatch.setattr(adapter_module, "write_build_deck", lambda **_kwargs: None)
        await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-1")
        monkeypatch.undo()

        _result, second = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-2")

        assert second.summarizer_units == [], "the committed write-up was re-synthesized instead of replayed"

    async def test_the_deck_is_retried_against_the_document_the_chain_names(self, project, monkeypatch) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        monkeypatch.setattr(adapter_module, "write_build_deck", lambda **_kwargs: None)
        first, _ = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-1")
        monkeypatch.undo()

        second, _ = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-2")

        assert second.artifact_uri == first.artifact_uri
        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        summary = _step(view, BuildStepKey.SUMMARIZE_RESULTS)
        deck = _step(view, BuildStepKey.RENDER_REVIEW_DECK)
        assert deck["status"] == StepState.SUCCEEDED.value
        assert len(summary["attempts"]) == 1, "the write-up was recorded twice for one execution"
        # The deck descends from the write-up that is actually on disk.
        assert deck["attempts"][-1]["predecessor_step_run_ids"] == [summary["selected_step_run_id"]]

    async def test_an_unreadable_write_up_is_re_run_and_recorded(self, project, monkeypatch) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        monkeypatch.setattr(adapter_module, "write_build_deck", lambda **_kwargs: None)
        await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-1")
        monkeypatch.undo()
        for kept in (root / "outputs" / ".dbtl-stage-work" / "steps").rglob("summarize_results-*.json"):
            kept.write_text("{not json", encoding="utf-8")

        _result, second = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-2")

        assert second.summarizer_units, "an unreadable write-up must be produced again"
        summary = _step(await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id), BuildStepKey.SUMMARIZE_RESULTS)
        assert len(summary["attempts"]) == 2, "the second synthesis was performed and never recorded"


class TestARestoredPayloadIsCheckedAgainstItsRecord:
    """Scratch is an ordinary file inside a folder the person can open.

    A shape check answers "is this a phase result?"; only the recomputed digest
    answers "is this *the* result that attempt committed?". Accepting the first
    for the second is how edited or half-written scratch becomes review evidence.
    """

    async def test_an_edited_payload_is_refused_and_the_phase_runs_again(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-1")

        edited = 0
        for kept in (root / "outputs" / ".dbtl-stage-work" / "steps").rglob("execute_phases-*.json"):
            payload = json.loads(kept.read_text(encoding="utf-8"))
            payload["result"]["summary"] = "Something nobody's worker said."
            kept.write_text(json.dumps(payload), encoding="utf-8")
            edited += 1
        assert edited, "no phase payload was kept to edit"

        _result, second = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-2")

        assert len(second.phase_units) == edited, "an edited payload was replayed as work that happened"

    async def test_a_published_output_that_is_gone_is_refused(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        first, _ = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-1")
        assert first.produced_usable_evidence, first.note

        artifacts = sorted((root / "outputs" / "dbtl").rglob("*/artifacts/**/*.bin"))
        assert artifacts, "the phase published nothing to remove"
        artifacts[0].unlink()

        _result, second = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-2")

        assert second.phase_units, "a phase whose published bytes are gone was replayed as evidence"


class TestADeckNobodyCanAnswerIsNotASuccess:
    """A rendered file is not a review surface.

    The step used to succeed on the bytes alone, so a registration that returned
    no plan left a finished-looking Build whose deck could never carry a verdict
    — and a registration that *raised* did so before this step opened, so the
    error code that names the failure could never be recorded at all.
    """

    async def test_a_registration_failure_is_recorded_before_it_is_raised(self, project, monkeypatch) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        async def _refuse(**_kwargs):
            raise RuntimeError("the surface store is unavailable")

        monkeypatch.setattr(repo, "register_stage_feedback_surface", _refuse)

        with pytest.raises(RuntimeError):
            await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN))

        deck = _step(await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id), BuildStepKey.RENDER_REVIEW_DECK)
        assert deck["status"] == StepState.FAILED.value
        assert deck["error_code"] == BuildErrorCode.DECK_REGISTRATION_FAILED.value

    async def test_a_deck_bound_to_nothing_does_not_succeed(self, project, monkeypatch) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        async def _no_surface(**_kwargs):
            return None

        monkeypatch.setattr(LiveStageAdapter, "_plan_feedback_surface", staticmethod(_no_surface))

        await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN))

        deck = _step(await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id), BuildStepKey.RENDER_REVIEW_DECK)
        assert deck["status"] == StepState.FAILED.value
        assert deck["error_code"] == BuildErrorCode.DECK_REGISTRATION_FAILED.value
