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

import asyncio
import hashlib
import json
import re
from contextlib import asynccontextmanager
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
from deerflow.dbtl.council_deck import extract_commentable_slides
from deerflow.dbtl.reconciliation_policy import reconciliation_required
from deerflow.dbtl.stage_runner import DispatchOutcome
from deerflow.persistence.dbtl import DbtlCycleRepository, DbtlWorkflowRefused
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.workspaces import WorkspaceRepository
from deerflow.runtime.activity.vocabulary import ActivityState

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
            "quality_checks": [
                {"name": "execution completed", "passed": True, "detail": ""},
                {"name": "phase_done_condition", "passed": True, "detail": "Every expected output exists."},
            ],
            "recommended_next_actions": ["Review the build."],
            "figures": [{"path": figure, "caption": "Held-out accuracy", "shows": "Predicted against observed yield."}],
            "key_outcomes": [{"name": "Held-out accuracy", "value": 0.62, "unit": "r"}],
            "provenance": {
                "inputs_examined": ["/mnt/user-data/yield.csv"],
                "recorded_rerun_procedure": "uv run python fit.py --seed 7",
                "rerun_spec": {
                    "version": 1,
                    "entry_point": "/mnt/user-data/fit.py",
                    "command": "uv run python fit.py --seed 7",
                    "seed": 7,
                    "inputs": ["/mnt/user-data/yield.csv"],
                    "environment": {"python": "3.12", "uv": "pinned lockfile"},
                    "configuration": ["/mnt/user-data/pyproject.toml"],
                    "expected_outputs": [artifact, figure],
                },
            },
        }
    )


def _with_phase_manifest(
    payload: dict,
    *,
    unit,
    entry_point: str,
    declared_outputs: list[str] | None = None,
) -> dict:
    done_match = re.search(r"^Done when: (?P<condition>.*)$", unit.prompt, re.MULTILINE)
    version_match = re.search(r"return version=(?P<version>[123])", unit.prompt)
    manifest_version = int(version_match.group("version")) if version_match is not None else 1
    payload["provenance"]["phase_manifest"] = {
        "version": manifest_version,
        "entry_point": entry_point,
        "declared_outputs": declared_outputs if declared_outputs is not None else list(payload["artifact_refs"]),
        "completion_condition": done_match.group("condition") if done_match is not None else "",
        **({"declared_inputs": list(payload["provenance"].get("inputs_examined") or ())} if manifest_version >= 2 else {}),
        **({"execution_inputs": []} if manifest_version >= 3 else {}),
    }
    return payload


class _WritingDispatcher:
    """Answers a build unit by writing real files, and a summarizer by reading.

    The adapter refuses a Build result whose artifacts it cannot find and hash,
    so a dispatcher that only returns JSON would never reach the step boundary
    these tests are about. The summarizer branch exists because a read-only seat
    has no grant at all — asking it for one is how a summarizer that could write
    would be noticed.
    """

    def __init__(
        self,
        *,
        summary: str | None = None,
        plan: str | None = None,
        presentable_results: bool = True,
        directory_artifact: bool = False,
        include_rerun_spec: bool = True,
    ) -> None:
        self.calls: list[tuple] = []
        self.summarizer_units: list = []
        self.planner_units: list = []
        self.phase_units: list = []
        self._summary = summary
        self._plan = plan
        self._presentable_results = presentable_results
        self._directory_artifact = directory_artifact
        self._include_rerun_spec = include_rerun_spec

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
            if self._presentable_results:
                plot.write_bytes(PNG)
            payload = json.loads(_build_result(artifact=_virtual(produced), figure=_virtual(plot)))
            if not self._include_rerun_spec:
                payload["provenance"].pop("rerun_spec")
            if self._directory_artifact:
                bundle = grant / "bundle"
                bundle.mkdir()
                produced.rename(bundle / produced.name)
                figures = bundle / "figures"
                figures.mkdir()
                plot.rename(figures / plot.name)
                produced = bundle / produced.name
                plot = figures / plot.name
                payload["artifact_refs"] = [_virtual(bundle)]
                payload["evidence_refs"] = [{"kind": "workspace_file", "reference": _virtual(bundle), "description": "Complete Build bundle."}]
                payload["figures"] = [{"path": _virtual(plot), "caption": "Held-out accuracy", "shows": "Predicted against observed yield."}]
                payload["key_outcomes"] = [{"name": "Held-out accuracy", "value": 0.62, "unit": "r", "figure": _virtual(plot)}]
            if not self._presentable_results:
                payload["artifact_refs"] = [_virtual(produced)]
                payload["figures"] = []
                payload["key_outcomes"] = []
            _with_phase_manifest(
                payload,
                unit=unit,
                entry_point=_virtual(produced),
            )
            outcomes.append(
                DispatchOutcome(
                    unit_id=unit.unit_id,
                    text=json.dumps(payload),
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
    match = (
        re.search(r"Write the corrected implementation under (/mnt/user-data/outputs/\.dbtl-stage-work/[A-Za-z0-9._/-]+?)[;\s\"]", prompt)
        or re.search(r"execution log under (/mnt/user-data/outputs/\.dbtl-stage-work/[A-Za-z0-9._/-]+?)[.\s\"]", prompt)
        or re.search(r"(/mnt/user-data/outputs/\.dbtl-stage-work/[A-Za-z0-9._/-]+)", prompt)
    )
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


def _adapter(
    repo: DbtlCycleRepository,
    *,
    workflow: bool,
    dispatcher,
    candidates=CANDIDATES,
) -> LiveStageAdapter:
    return LiveStageAdapter(
        repo=repo,
        app_config=SimpleNamespace(
            dbtl=SimpleNamespace(
                build_workflow_steps=workflow,
            )
        ),
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


async def _run_build(
    repo: DbtlCycleRepository,
    root: Path,
    *,
    workflow: bool = True,
    dispatcher=None,
    candidates=CANDIDATES,
    run_id: str = "run-1",
):
    """One Build request.

    `run_id` matters whenever a test runs Build twice: the stage-execution
    idempotency key is bound to the run, so a second request under the same id
    replays the whole recorded stage and never reaches the workflow at all.
    A test about step-level resume that reuses `run-1` proves nothing.
    """
    dispatcher = dispatcher or _WritingDispatcher()
    result = await _adapter(
        repo,
        workflow=workflow,
        dispatcher=dispatcher,
        candidates=candidates,
    ).execute(
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
    async def test_build_contract_is_pinned_before_the_first_worker_dispatch(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        events: list[tuple[str, str]] = []
        observed_token_caps: list[int] = []
        original_pin = repo.pin_stage_spec

        async def observed_pin(**kwargs):
            pinned_key = await original_pin(**kwargs)
            events.append(("pin", pinned_key))
            return pinned_key

        repo.pin_stage_spec = observed_pin  # type: ignore[method-assign]

        class _ObservingDispatcher:
            async def __call__(self, units, *, budget):
                events.append(("dispatch", ""))
                observed_token_caps.append(budget.max_tokens)
                return [DispatchOutcome(unit_id=unit.unit_id, text="not a valid result") for unit in units]

        dispatcher = _ObservingDispatcher()
        await _run_build(repo, root, dispatcher=dispatcher)

        assert events[0] == ("pin", "generic:build:v12")
        assert events[1][0] == "dispatch"
        assert observed_token_caps[0] == 120_000

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
        lineage = (await repo.build_test_view("cycle-1", project_id="project-1"))["build_lineage"]
        assert lineage["stage_spec_key"] == "generic:build:v12"
        assert lineage["rerun_status"] == "verified"
        assert lineage["rerun_spec"]["command"] == "uv run python fit.py --seed 7"

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

    async def test_a_phased_build_records_the_servers_own_rerun_driver(self, project) -> None:
        """The server writes the rerun record rather than asking for it.

        A phased Build has one entry point per phase, and the worker-supplied
        specs merge only when they agree — so two phases naming different entry
        points conflicted and the bundle carried no rerun record at all. The
        phase prompt never asked for one either, so `structured_rerun_spec` was
        unsatisfiable in production however well the phases ran, and a Build
        that completed every planned phase could never reach its human gate.

        Deriving it from the verified manifests is also the more trustworthy
        answer: those entry points are the ones the server executed itself.
        The case where there is genuinely nothing runnable to record is covered
        in `test_dbtl_build_driver.py`.
        """
        repo, root = project
        await _ready_for_build(repo)

        result, _dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(include_rerun_spec=False))

        assert result.produced_usable_evidence
        assert result.artifact_uri is not None
        lineage = (await repo.build_test_view("cycle-1", project_id="project-1"))["build_lineage"]
        assert lineage is not None
        rerun = lineage["rerun_spec"]
        assert rerun["entry_point"].endswith(".sh")
        assert rerun["command"].startswith("/bin/bash ")
        # The driver has to exist as a real project file, or Test cannot re-run it.
        driver = root / "outputs" / rerun["entry_point"].removeprefix("/mnt/user-data/outputs/")
        assert driver.is_file()
        assert "set -euo pipefail" in driver.read_text(encoding="utf-8")

    async def test_a_failed_execution_does_not_open_a_fake_deck_failure(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        class _ProseDispatcher:
            async def __call__(self, units, *, budget):
                return [DispatchOutcome(unit_id=unit.unit_id, text="I built it, trust me.") for unit in units]

        await _run_build(repo, root, dispatcher=_ProseDispatcher())

        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        assert _step(view, BuildStepKey.EXECUTE_PHASES)["status"] == StepState.FAILED.value
        deck = _step(view, BuildStepKey.RENDER_REVIEW_DECK)
        assert deck["status"] == "waiting"
        assert deck["attempts"] == []
        assert deck["error_code"] is None
        assert view["next_step"] == BuildStepKey.EXECUTE_PHASES.value


async def test_virtual_workspace_workers_do_not_receive_host_filesystem_mcp_tools() -> None:
    from deerflow.agents.dbtl.live_stage.adapter import _tools_for_virtual_workspace
    from deerflow.tools.mcp_metadata import tag_mcp_tool

    builtin_tools = [
        SimpleNamespace(name="read_file"),
        SimpleNamespace(name="write_file"),
    ]
    filesystem_tools = [
        tag_mcp_tool(
            SimpleNamespace(name="filesystem_create_directory", metadata={}),
            source_name="filesystem",
            original_name="create_directory",
        ),
        tag_mcp_tool(
            SimpleNamespace(name="fs_write_file", metadata={}),
            source_name="fs",
            original_name="write_file",
        ),
        tag_mcp_tool(
            SimpleNamespace(name="write_file", metadata={}),
            source_name="project_files",
            original_name="write_file",
        ),
    ]
    web_tool = tag_mcp_tool(
        SimpleNamespace(name="web_search", metadata={}),
        source_name="web",
        original_name="search",
    )
    tools = [*builtin_tools, *filesystem_tools, web_tool]

    selected = _tools_for_virtual_workspace(
        tools,
        writable_workspace="/mnt/user-data/outputs/.dbtl-stage-work/attempt/build/unit",
    )

    assert selected == [*builtin_tools, web_tool]

    host_selected = _tools_for_virtual_workspace(tools, writable_workspace="/private/tmp/build/unit")
    assert [tool.name for tool in host_selected] == [tool.name for tool in tools]


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
        from deerflow.dbtl.stage_runner import BUILD_SUMMARY_OUTPUT

        repo, root = project
        await _ready_for_build(repo)
        _result, dispatcher = await _run_build(repo, root)
        (unit,) = dispatcher.summarizer_units

        assert unit.role in _READ_ONLY_ROLES
        assert unit.output_contract == BUILD_SUMMARY_OUTPUT
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
        assert "Human gate" in deck
        assert 'data-deck-action="submit_for_review" disabled' in deck
        assert "send('ready')" in deck
        assert 'class="track"' in deck
        assert 'data-step="-1"' in deck
        surface = await repo.latest_stage_feedback_surface(cycle_id="cycle-1", project_id="project-1", stage="build")
        assert surface is not None
        assert surface["decision_request"]["commentable_slides"] == list(extract_commentable_slides(deck))
        # The Build deck, not the meeting deck: a Build result nobody argued
        # about has no positions to render.
        assert "participants" not in deck.lower()

    async def test_no_result_evidence_surfaces_one_card_and_no_deck(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        result, _dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(presentable_results=False))

        assert not result.produced_usable_evidence
        assert result.artifact_uri is None
        assert result.deck_uri is None
        assert result.feedback_surface_id is None
        assert result.control_request is not None
        assert "without verified outcomes or figures" in result.control_request["question"]
        assert {option["value"] for option in result.control_request["options"]} == {
            "replan_build",
            "restart_build",
            "hold_here",
        }
        assert await repo.latest_stage_feedback_surface(cycle_id="cycle-1", project_id="project-1", stage="build") is None


class TestABuildStopsBeingOneOpaqueWorker:
    async def test_a_declared_output_directory_publishes_each_regular_file(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        result, _dispatcher = await _run_build(
            repo,
            root,
            dispatcher=_WritingDispatcher(plan=SINGLE_PHASE_PLAN, directory_artifact=True),
        )

        assert result.produced_usable_evidence, result.note
        view = await repo.build_test_view("cycle-1", project_id="project-1")
        outputs = view["build_lineage"]["output_artifacts"]
        assert len(outputs) == 2
        assert [item["uri"].rsplit("-", 1)[-1] for item in outputs] == ["accuracy.png", "model.bin"]
        assert all(item["content_hash"] for item in outputs)
        assert result.deck_uri
        deck = (root / result.deck_uri.removeprefix("/mnt/user-data/")).read_text(encoding="utf-8")
        assert "data:image/png;base64," in deck

    async def test_an_empty_declared_output_directory_fails_the_phase(self, project) -> None:
        class _EmptyDirectoryDispatcher(_WritingDispatcher):
            async def __call__(self, units, *, budget):
                outcomes = await super().__call__(units, budget=budget)
                revised = []
                by_id = {unit.unit_id: unit for unit in units}
                for outcome in outcomes:
                    unit = by_id[outcome.unit_id]
                    if unit.role != "phase":
                        revised.append(outcome)
                        continue
                    empty = _grant_from_prompt(unit.prompt) / "empty-bundle"
                    empty.mkdir()
                    payload = json.loads(outcome.text)
                    payload["artifact_refs"] = [_virtual(empty)]
                    revised.append(DispatchOutcome(unit_id=outcome.unit_id, text=json.dumps(payload)))
                return revised

        repo, root = project
        await _ready_for_build(repo)

        result, _dispatcher = await _run_build(
            repo,
            root,
            dispatcher=_EmptyDirectoryDispatcher(plan=SINGLE_PHASE_PLAN),
        )

        assert not result.produced_usable_evidence
        assert "contains no regular files" in result.note

    async def test_an_output_hash_read_error_fails_the_phase_instead_of_crashing(self, project, monkeypatch) -> None:
        repo, root = project
        await _ready_for_build(repo)
        original = adapter_module._sha256_file

        def _unreadable(path: Path) -> str:
            if ".dbtl-stage-work" in path.parts and path.name == "model.bin":
                raise OSError("worker output became unreadable")
            return original(path)

        monkeypatch.setattr(adapter_module, "_sha256_file", _unreadable)

        result, _dispatcher = await _run_build(
            repo,
            root,
            dispatcher=_WritingDispatcher(plan=SINGLE_PHASE_PLAN),
        )

        assert not result.produced_usable_evidence
        assert "became unreadable" in result.note

    async def test_each_phase_gets_compact_build_context_and_tool_guidance(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        _result, dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=SINGLE_PHASE_PLAN))

        prompt = dispatcher.phase_units[0].prompt
        assert '"build_input_bundle"' in prompt
        assert '"prior_design_council_runs"' not in prompt
        assert '"test_validity_contract"' not in prompt
        assert '"approved_design_brief"' not in prompt
        assert "Use write_file or str_replace" in prompt
        assert "do not embed complete files in" in prompt

    async def test_server_prepares_the_standard_build_workspace_layout(self, tmp_path: Path) -> None:
        adapter_module._prepare_unit_workspace(tmp_path, stage="build")

        assert {path.name for path in tmp_path.iterdir()} == {"src", "tests", "config", "artifacts", "logs"}

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

    async def test_a_failed_phase_stops_the_run_rather_than_spending_the_rest(self, project, monkeypatch) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        settled: list[tuple[ActivityState, str | None]] = []

        class _Handle:
            async def update(self, **_kwargs):
                return None

            async def settle(self, state, *, operation=None, **_kwargs):
                settled.append((state, operation))

        @asynccontextmanager
        async def _activity(_run_id, **kwargs):
            yield _Handle() if kwargs.get("actor_id") == "build-stage" else None

        monkeypatch.setattr(adapter_module, "optional_activity_span", _activity)

        class _SecondPhaseFails(_WritingDispatcher):
            async def __call__(self, units, *, budget):
                if units[0].role == "phase" and len(self.phase_units) >= 1:
                    self.phase_units.append(units[0])
                    return [DispatchOutcome(unit_id=units[0].unit_id, text="it did not work")]
                return await super().__call__(units, budget=budget)

        result, dispatcher = await _run_build(repo, root, dispatcher=_SecondPhaseFails(plan=TWO_PHASE_PLAN))

        assert len(dispatcher.phase_units) == 2, "a third phase should never have been dispatched"
        phases = (await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id))["phases"]
        assert [entry["status"] for entry in phases] == [StepState.SUCCEEDED.value, StepState.FAILED.value]
        assert phases[1]["error_code"] == BuildErrorCode.EXECUTION_CONTRACT_REJECTED.value
        assert result.control_request is not None
        assert settled[-1] == (ActivityState.PAUSED, "stage.wait_human")

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

    async def test_a_boundary_after_the_last_phase_is_not_a_boundary(self, project) -> None:
        """`pause_after` on the final phase has nothing to pause before.

        A planner may set it on every phase, including the last one. Honouring
        it there offers "Continue — runs the remaining 0 phases": a card whose
        only real option does nothing, and which marks a plan that ran to
        completion as unfinished, so no review package is ever written and the
        Build can never reach its human gate. The boundary exists to stop
        *before the next phase*, so with no next phase there is nothing to ask.
        """
        repo, root = project
        await _ready_for_build(repo)
        paused = json.loads(TWO_PHASE_PLAN)
        paused["phases"][-1]["pause_after"] = True

        result, dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=json.dumps(paused)))

        assert [unit.capability for unit in dispatcher.phase_units] == ["software_and_workflow_engineering", "statistical_analysis"]
        assert result.produced_usable_evidence
        assert result.artifact_uri is not None
        assert "planned build phase(s)" not in result.note


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

    async def test_retry_after_second_phase_failure_replays_only_the_first_phase(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        class _SecondPhaseFails(_WritingDispatcher):
            async def __call__(self, units, *, budget):
                if units[0].role == "phase" and len(self.phase_units) >= 1:
                    self.phase_units.append(units[0])
                    return [DispatchOutcome(unit_id=units[0].unit_id, text="it did not work")]
                return await super().__call__(units, budget=budget)

        first_result, first = await _run_build(
            repo,
            root,
            dispatcher=_SecondPhaseFails(plan=TWO_PHASE_PLAN),
            run_id="run-1",
        )
        second_result, second = await _run_build(
            repo,
            root,
            dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN),
            run_id="run-2",
        )

        assert not first_result.produced_usable_evidence
        assert len(first.phase_units) == 2
        assert [unit.capability for unit in second.phase_units] == ["statistical_analysis"]
        assert second_result.produced_usable_evidence

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

    async def test_a_changed_phase_input_reopens_the_phase_instead_of_relabelling_old_work(self, project, monkeypatch) -> None:
        repo, root = project
        await _ready_for_build(repo)
        monkeypatch.setattr(adapter_module, "write_build_deck", lambda **_kwargs: None)

        await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-1")
        (root / "yield.csv").write_text("id,yield\n1,9.9\n", encoding="utf-8")

        _result, second = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN), run_id="run-2")

        assert len(second.phase_units) == 2, "phases that read the old bytes were replayed under the new input hash"

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


class TestWorkflowPersistenceIsAuthoritative:
    async def test_a_recorder_initialization_failure_dispatches_no_worker(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        class _UnavailableRecorderRepo:
            def __getattr__(self, name):
                return getattr(repo, name)

            async def build_step_material_for(self, **kwargs):
                raise RuntimeError("step storage is unavailable")

        dispatcher = _WritingDispatcher(plan=TWO_PHASE_PLAN)
        result = await _adapter(
            _UnavailableRecorderRepo(),
            workflow=True,
            dispatcher=dispatcher,
        ).execute(
            project_id="project-1",
            cycle_id="cycle-1",
            request_text="Build the approved design.",
            state={},
            config=_runtime(root, run_id="run-recorder-down"),
        )

        assert "durable workflow could not be initialized" in result.note
        assert dispatcher.calls == []
        assert result.artifact_uri is None

    async def test_an_interrupted_phase_releases_its_durable_step_immediately(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        class _CancellingDispatcher:
            async def __call__(self, units, *, budget):
                unit = units[0]
                if unit.role == "planner":
                    return [DispatchOutcome(unit_id=unit.unit_id, text=SINGLE_PHASE_PLAN)]
                raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await _run_build(repo, root, dispatcher=_CancellingDispatcher(), run_id="run-interrupted")

        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        phase = next(item for item in view["phases"] if item["phase_key"] == "build")
        assert phase["status"] == StepState.CANCELLED.value
        assert phase["error_code"] == BuildErrorCode.CANCELLED.value
        assert view["next_step"] == BuildStepKey.EXECUTE_PHASES.value

    async def test_repeated_parent_cancellation_cannot_abandon_step_cleanup(self, project, monkeypatch) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        phase_started = asyncio.Event()
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()

        class _BlockingDispatcher:
            async def __call__(self, units, *, budget):
                unit = units[0]
                if unit.role == "planner":
                    return [DispatchOutcome(unit_id=unit.unit_id, text=SINGLE_PHASE_PLAN)]
                phase_started.set()
                await asyncio.Event().wait()

        real_cleanup = repo.cancel_running_step_attempts

        async def delayed_cleanup(**kwargs):
            cleanup_started.set()
            await release_cleanup.wait()
            return await real_cleanup(**kwargs)

        monkeypatch.setattr(repo, "cancel_running_step_attempts", delayed_cleanup)
        task = asyncio.create_task(_run_build(repo, root, dispatcher=_BlockingDispatcher(), run_id="run-interrupted-twice"))
        await phase_started.wait()
        task.cancel()
        await cleanup_started.wait()
        task.cancel()
        release_cleanup.set()

        with pytest.raises(asyncio.CancelledError):
            await task

        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        phase = next(item for item in view["phases"] if item["phase_key"] == "build")
        assert phase["status"] == StepState.CANCELLED.value

    async def test_an_incomplete_workflow_cannot_be_submitted_even_with_artifact_and_lineage(self, project, monkeypatch) -> None:
        from deerflow.config import app_config as app_config_module

        repo, root = project
        await _ready_for_build(repo)
        monkeypatch.setattr(adapter_module, "write_build_deck", lambda **_kwargs: None)
        result, _dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN))
        assert result.artifact_uri is not None, "the test needs a package whose deck step alone failed"
        monkeypatch.setattr(
            app_config_module,
            "get_app_config",
            lambda: SimpleNamespace(
                dbtl=SimpleNamespace(
                    build_workflow_steps=True,
                    reconciliation_required=False,
                )
            ),
        )

        with pytest.raises(DbtlWorkflowRefused, match="durable workflow is incomplete"):
            await repo.submit_stage_for_review(
                cycle_id="cycle-1",
                project_id="project-1",
                stage="build",
                expected_db_revision=await _revision(repo),
                actor_user_id="user-1",
                idempotency_key="submit-incomplete-build",
            )


class TestAPhaseSucceedsOnlyOnceItsOutputsArePublished:
    async def test_a_missing_artifact_is_named_as_missing_not_outside(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        class _NamesAFileItNeverWrote(_WritingDispatcher):
            async def __call__(self, units, *, budget):
                if units[0].role == "phase":
                    self.phase_units.append(units[0])
                    missing = _grant_from_prompt(units[0].prompt) / "never-created.bin"
                    text = _build_result(
                        artifact=_virtual(missing),
                        figure=_virtual(missing),
                    )
                    return [DispatchOutcome(unit_id=units[0].unit_id, text=text)]
                return await super().__call__(units, budget=budget)

        await _run_build(
            repo,
            root,
            dispatcher=_NamesAFileItNeverWrote(plan=SINGLE_PHASE_PLAN),
        )

        phases = (
            await repo.build_workflow_view(
                project_id="project-1",
                stage_attempt_id=stage_attempt_id,
            )
        )["phases"]
        assert "does not exist" in phases[0]["error_summary"]
        assert "outside this worker" not in phases[0]["error_summary"]

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


class _SecondPhaseReadsTheFirst(_WritingDispatcher):
    """A phase that consumes the phase before it, which is the normal shape.

    Sequential phases exist so a later one can build on an earlier one's output.
    This dispatcher does exactly that: it reads the published paths the adapter
    put in its prompt and declares one of them as an input it examined.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.declared: list[str] = []

    async def __call__(self, units, *, budget):
        outcomes = []
        for unit in units:
            if unit.role != "phase" or "This is the first phase" in unit.prompt:
                outcomes.extend(await super().__call__((unit,), budget=budget))
                continue
            self.phase_units.append(unit)
            preceding = json.loads(unit.prompt.split("they are inputs, hash-bound like any other.\n", 1)[1].split("\n", 1)[0])
            upstream = preceding[0]["outputs"][0]
            self.declared.append(upstream)
            grant = _grant_from_prompt(unit.prompt)
            grant.mkdir(parents=True, exist_ok=True)
            produced = grant / "model.bin"
            produced.write_text("fitted", encoding="utf-8")
            plot = grant / "accuracy.png"
            plot.write_bytes(PNG)
            payload = json.loads(_build_result(artifact=_virtual(produced), figure=_virtual(plot)))
            payload["provenance"]["inputs_examined"] = [upstream]
            _with_phase_manifest(payload, unit=unit, entry_point=_virtual(produced))
            outcomes.append(DispatchOutcome(unit_id=unit.unit_id, text=json.dumps(payload)))
        return outcomes


class _SecondPhaseReadsTheFirstDirectory(_SecondPhaseReadsTheFirst):
    """Declare the prior phase's directory, as real workers commonly do."""

    async def __call__(self, units, *, budget):
        outcomes = []
        for unit in units:
            if unit.role != "phase" or "This is the first phase" in unit.prompt:
                outcomes.extend(await _WritingDispatcher.__call__(self, (unit,), budget=budget))
                continue
            self.phase_units.append(unit)
            preceding = json.loads(unit.prompt.split("they are inputs, hash-bound like any other.\n", 1)[1].split("\n", 1)[0])
            upstream_file = preceding[0]["outputs"][0]
            upstream_directory = upstream_file.rsplit("/", 1)[0]
            self.declared.append(upstream_directory)
            grant = _grant_from_prompt(unit.prompt)
            grant.mkdir(parents=True, exist_ok=True)
            produced = grant / "model.bin"
            produced.write_text("fitted", encoding="utf-8")
            plot = grant / "accuracy.png"
            plot.write_bytes(PNG)
            payload = json.loads(_build_result(artifact=_virtual(produced), figure=_virtual(plot)))
            payload["provenance"]["inputs_examined"] = [upstream_directory]
            _with_phase_manifest(payload, unit=unit, entry_point=_virtual(produced))
            outcomes.append(DispatchOutcome(unit_id=unit.unit_id, text=json.dumps(payload)))
        return outcomes


class _IncompletePhaseDispatcher(_WritingDispatcher):
    """A worker that claims completed while admitting its done condition failed."""

    async def __call__(self, units, *, budget):
        outcomes = await super().__call__(units, budget=budget)
        revised = []
        for unit, outcome in zip(units, outcomes, strict=True):
            if unit.role != "phase" or not outcome.text:
                revised.append(outcome)
                continue
            payload = json.loads(outcome.text)
            marker = next(item for item in payload["quality_checks"] if item["name"] == "phase_done_condition")
            marker.update(passed=False, detail="The simulator and generated dataset are still missing.")
            revised.append(DispatchOutcome(unit_id=outcome.unit_id, text=json.dumps(payload)))
        return revised


class _ContradictoryPhaseDispatcher(_IncompletePhaseDispatcher):
    """Claims done while its own implementation test says the opposite."""

    async def __call__(self, units, *, budget):
        outcomes = await super().__call__(units, budget=budget)
        revised = []
        for unit, outcome in zip(units, outcomes, strict=True):
            if unit.role != "phase" or not outcome.text:
                revised.append(outcome)
                continue
            payload = json.loads(outcome.text)
            marker = next(item for item in payload["quality_checks"] if item["name"] == "phase_done_condition")
            marker.update(passed=True, detail="Done.")
            payload["quality_checks"].append(
                {
                    "name": "Automated implementation tests",
                    "passed": False,
                    "detail": "The simulator and generated dataset are still missing.",
                }
            )
            revised.append(DispatchOutcome(unit_id=outcome.unit_id, text=json.dumps(payload)))
        return revised


class _FreshCorrectionDispatcher(_WritingDispatcher):
    """Fail the first implementation check, then complete the fresh unit."""

    async def __call__(self, units, *, budget):
        outcomes = await super().__call__(units, budget=budget)
        revised = []
        for unit, outcome in zip(units, outcomes, strict=True):
            if unit.role != "phase" or not outcome.text:
                revised.append(outcome)
                continue
            payload = json.loads(outcome.text)
            marker = next(item for item in payload["quality_checks"] if item["name"] == "phase_done_condition")
            is_correction = bool(unit.tool_contract.get("correction_attempt"))
            if not is_correction:
                marker.update(passed=False, detail="The generated implementation test failed.")
            revised.append(
                DispatchOutcome(
                    unit_id=outcome.unit_id,
                    text=json.dumps(payload),
                    token_usage={
                        "input_tokens": 20 if is_correction else 100,
                        "output_tokens": 5 if is_correction else 10,
                        "total_tokens": 25 if is_correction else 110,
                    },
                )
            )
        return revised


class TestAPhaseMayBuildOnThePhaseBeforeIt:
    """The pre-run snapshot cannot contain what the run itself published.

    Judging a phase's declared inputs against that snapshot alone refused every
    plan whose phases build on each other: the second phase reported a clean,
    contract-valid result, the server failed its step with "was not present when
    this Build run started", and the Build stopped behind a recovery card with
    the finished phase stranded. Nothing was wrong with the work.
    """

    async def test_the_second_phase_succeeds_and_binds_the_first_phases_output(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        _result, dispatcher = await _run_build(repo, root, dispatcher=_SecondPhaseReadsTheFirst(plan=TWO_PHASE_PLAN))

        assert dispatcher.declared, "the second phase never declared the first phase's output"
        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        phases = view["phases"]
        assert [entry["phase_key"] for entry in phases] == ["simulate", "fit"]
        assert {entry["status"] for entry in phases} == {StepState.SUCCEEDED.value}
        assert _step(view, BuildStepKey.EXECUTE_PHASES)["status"] == StepState.SUCCEEDED.value

    async def test_build_lineage_records_the_upstream_phase_output_it_consumed(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        _result, dispatcher = await _run_build(repo, root, dispatcher=_SecondPhaseReadsTheFirst(plan=TWO_PHASE_PLAN))

        view = await repo.build_test_view("cycle-1", project_id="project-1")
        bound = view["build_lineage"]["input_artifacts"]
        upstream_relative = dispatcher.declared[0][len("/mnt/user-data/") :]
        assert any(entry.startswith(f"workspace_file:{upstream_relative}:sha256:") for entry in bound), bound

    async def test_a_prior_phase_directory_expands_to_its_published_files(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        result, dispatcher = await _run_build(repo, root, dispatcher=_SecondPhaseReadsTheFirstDirectory(plan=TWO_PHASE_PLAN))

        view = await repo.build_test_view("cycle-1", project_id="project-1")
        assert result.produced_usable_evidence, result.note
        prefix = dispatcher.declared[0][len("/mnt/user-data/") :] + "/"
        bound = [entry for entry in view["build_lineage"]["input_artifacts"] if entry.startswith(f"workspace_file:{prefix}")]
        assert len(bound) == 2, bound

    async def test_a_directory_can_mix_pre_run_and_prior_phase_files(self, project) -> None:
        _repo, root = project
        directory = root / "mixed-inputs"
        directory.mkdir()
        source = directory / "source.csv"
        source.write_text("source", encoding="utf-8")
        source_stat = source.stat()
        generated = directory / "generated.csv"
        generated.write_text("generated", encoding="utf-8")
        generated_hash = hashlib.sha256(generated.read_bytes()).hexdigest()

        artifacts = adapter_module._directory_input_artifacts(
            relative="mixed-inputs",
            path=directory,
            project_root=str(root),
            pre_run_files={"mixed-inputs/source.csv": (source_stat.st_size, source_stat.st_mtime_ns)},
            published_hashes={"mixed-inputs/generated.csv": generated_hash},
            required=True,
        )

        assert {entry.split(":sha256:")[0] for entry in artifacts} == {
            "workspace_file:mixed-inputs/source.csv",
            "workspace_file:mixed-inputs/generated.csv",
        }

    async def test_a_directory_hash_read_error_becomes_a_recorded_refusal(self, project, monkeypatch) -> None:
        _repo, root = project
        directory = root / "source-inputs"
        directory.mkdir()
        source = directory / "source.csv"
        source.write_text("source", encoding="utf-8")
        source_stat = source.stat()

        def unreadable(_path):
            raise OSError("unreadable")

        monkeypatch.setattr(adapter_module, "_sha256_file", unreadable)

        with pytest.raises(ValueError, match="no longer readable"):
            adapter_module._directory_input_artifacts(
                relative="source-inputs",
                path=directory,
                project_root=str(root),
                pre_run_files={"source-inputs/source.csv": (source_stat.st_size, source_stat.st_mtime_ns)},
                published_hashes={},
                required=True,
            )


class TestTheServerOwnsThePhaseManifestVerdict:
    async def test_a_compatible_build_result_shape_keeps_verified_work(self, project) -> None:
        class _CompatibleShapeDispatcher(_WritingDispatcher):
            async def __call__(self, units, *, budget):
                outcomes = await super().__call__(units, budget=budget)
                revised = []
                for unit, outcome in zip(units, outcomes, strict=True):
                    if unit.role != "phase" or not outcome.text:
                        revised.append(outcome)
                        continue
                    payload = json.loads(outcome.text)
                    payload["headline"] = payload.pop("summary")
                    payload["outputs"] = [{"path": path} for path in payload.pop("artifact_refs")]
                    payload["evidence"] = [item["reference"] for item in payload.pop("evidence_refs")]
                    payload["findings"] = payload.pop("claims")
                    payload["checks"] = {item["name"]: item["passed"] for item in payload.pop("quality_checks")}
                    revised.append(DispatchOutcome(unit_id=outcome.unit_id, text=json.dumps(payload)))
                return revised

        repo, root = project
        await _ready_for_build(repo)

        result, _dispatcher = await _run_build(
            repo,
            root,
            dispatcher=_CompatibleShapeDispatcher(plan=SINGLE_PHASE_PLAN),
        )

        assert result.produced_usable_evidence, result.note

    async def test_grant_relative_result_paths_publish_under_the_same_containment_rules(self, project) -> None:
        class _GrantRelativeDispatcher(_WritingDispatcher):
            async def __call__(self, units, *, budget):
                outcomes = await super().__call__(units, budget=budget)
                revised = []
                for unit, outcome in zip(units, outcomes, strict=True):
                    if unit.role != "phase" or not outcome.text:
                        revised.append(outcome)
                        continue
                    grant_prefix = _virtual(_grant_from_prompt(unit.prompt)).rstrip("/") + "/"

                    def relative(value: str) -> str:
                        return value.removeprefix(grant_prefix) if value.startswith(grant_prefix) else value

                    payload = json.loads(outcome.text)
                    payload["artifact_refs"] = [relative(value) for value in payload["artifact_refs"]]
                    for evidence in payload["evidence_refs"]:
                        evidence["reference"] = relative(evidence["reference"])
                    for figure in payload.get("figures", []):
                        figure["path"] = relative(figure["path"])
                    manifest = payload["provenance"]["phase_manifest"]
                    manifest["entry_point"] = relative(manifest["entry_point"])
                    manifest["declared_outputs"] = [relative(value) for value in manifest["declared_outputs"]]
                    revised.append(DispatchOutcome(unit_id=outcome.unit_id, text=json.dumps(payload)))
                return revised

        repo, root = project
        await _ready_for_build(repo)

        result, _dispatcher = await _run_build(
            repo,
            root,
            dispatcher=_GrantRelativeDispatcher(plan=SINGLE_PHASE_PLAN),
        )

        assert result.produced_usable_evidence, result.note
        lineage = (await repo.build_test_view("cycle-1", project_id="project-1"))["build_lineage"]
        assert lineage["output_artifacts"]
        assert all(item["uri"].startswith("/mnt/user-data/outputs/dbtl/") for item in lineage["output_artifacts"])

    @pytest.mark.parametrize(
        ("mutation", "expected"),
        [
            (lambda provenance: provenance.pop("phase_manifest"), "valid versioned phase manifest"),
            (lambda provenance: provenance["phase_manifest"].pop("version"), "valid versioned phase manifest"),
            (lambda provenance: provenance["phase_manifest"].update(entry_point="/mnt/user-data/not-published.py"), "entry point"),
            (lambda provenance: provenance["phase_manifest"].update(declared_outputs=["/mnt/user-data/not-published.bin"]), "exactly the outputs"),
            (lambda provenance: provenance["phase_manifest"].update(completion_condition="A different condition."), "changed the versioned completion condition"),
        ],
        ids=("missing", "unversioned", "unpublished-entry-point", "wrong-outputs", "changed-completion"),
    )
    async def test_missing_or_unverified_manifest_authority_cannot_commit(self, project, mutation, expected) -> None:
        class _InvalidManifestDispatcher(_WritingDispatcher):
            async def __call__(self, units, *, budget):
                outcomes = await super().__call__(units, budget=budget)
                revised = []
                for unit, outcome in zip(units, outcomes, strict=True):
                    if unit.role != "phase" or not outcome.text:
                        revised.append(outcome)
                        continue
                    payload = json.loads(outcome.text)
                    mutation(payload["provenance"])
                    revised.append(DispatchOutcome(unit_id=outcome.unit_id, text=json.dumps(payload)))
                return revised

        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        result, dispatcher = await _run_build(
            repo,
            root,
            dispatcher=_InvalidManifestDispatcher(plan=TWO_PHASE_PLAN),
        )

        assert not result.produced_usable_evidence
        assert len(dispatcher.phase_units) == 1
        phases = (await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id))["phases"]
        assert len(phases) == 1
        assert phases[0]["status"] == StepState.FAILED.value
        assert phases[0]["error_code"] == BuildErrorCode.EXECUTION_CONTRACT_REJECTED.value
        assert expected in phases[0]["error_summary"]
        worker_runs = await repo.list_worker_runs("cycle-1", project_id="project-1", stage="build")
        assert worker_runs[-1]["status"] == "failed"

    async def test_a_one_file_directory_is_not_guessed_as_the_entry_point(self, project) -> None:
        class _DirectoryEntryPointDispatcher(_WritingDispatcher):
            async def __call__(self, units, *, budget):
                outcomes = await super().__call__(units, budget=budget)
                revised = []
                for unit, outcome in zip(units, outcomes, strict=True):
                    if unit.role != "phase" or not outcome.text:
                        revised.append(outcome)
                        continue
                    payload = json.loads(outcome.text)
                    grant = _grant_from_prompt(unit.prompt)
                    payload["artifact_refs"] = [_virtual(grant)]
                    payload["figures"] = []
                    payload["key_outcomes"] = []
                    payload["provenance"]["phase_manifest"].update(
                        entry_point=_virtual(grant),
                        declared_outputs=[_virtual(grant)],
                    )
                    revised.append(DispatchOutcome(unit_id=outcome.unit_id, text=json.dumps(payload)))
                return revised

        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)

        result, _dispatcher = await _run_build(
            repo,
            root,
            dispatcher=_DirectoryEntryPointDispatcher(plan=SINGLE_PHASE_PLAN, presentable_results=False),
        )

        assert not result.produced_usable_evidence
        phases = (await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id))["phases"]
        assert phases[0]["error_code"] == BuildErrorCode.EXECUTION_CONTRACT_REJECTED.value
        assert "entry point" in phases[0]["error_summary"]


class TestAPartialPhaseCannotAdvanceThePlan:
    async def test_v12_uses_one_fresh_40k_correction_and_keeps_the_first_usage(self, project, monkeypatch) -> None:
        repo, root = project
        await _ready_for_build(repo)
        dispatcher = _FreshCorrectionDispatcher(plan=SINGLE_PHASE_PLAN)
        merged_usage: list[dict[str, int]] = []
        original_merge = adapter_module._merge_token_usage

        def observed_merge(*values):
            merged = original_merge(*values)
            merged_usage.append(merged)
            return merged

        monkeypatch.setattr(adapter_module, "_merge_token_usage", observed_merge)

        result, _ = await _run_build(
            repo,
            root,
            dispatcher=dispatcher,
        )

        assert result.produced_usable_evidence, result.note
        assert len(dispatcher.phase_units) == 2
        first, correction = dispatcher.phase_units
        assert not first.tool_contract.get("correction_attempt")
        assert correction.tool_contract["correction_attempt"] is True
        assert correction.tool_contract["fresh_correction"] is True
        assert correction.max_tokens == 40_000
        assert "The generated implementation test failed" in correction.prompt
        assert "read-only" in correction.prompt
        assert {"input_tokens": 120, "output_tokens": 15, "total_tokens": 135} in merged_usage

        stage_attempt_id = await _build_stage_attempt_id(repo)
        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        phase = view["phases"][0]
        assert phase["status"] == StepState.SUCCEEDED.value
        # The unit id in the committed digest/payload is the fresh worker, so a
        # replay cannot accidentally resurrect the rejected first result.
        assert "correction" in correction.unit_id

    async def test_a_failed_done_condition_stops_before_the_next_phase(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        dispatcher = _IncompletePhaseDispatcher(plan=TWO_PHASE_PLAN)

        result, _ = await _run_build(repo, root, dispatcher=dispatcher)

        assert not result.produced_usable_evidence
        assert len(dispatcher.phase_units) == 2
        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        assert [entry["phase_key"] for entry in view["phases"]] == ["simulate"]
        assert view["phases"][0]["status"] == StepState.FAILED.value
        assert view["phases"][0]["error_code"] == BuildErrorCode.EXECUTION_CONTRACT_REJECTED.value
        assert "simulator and generated dataset" in result.note
        assert view["next_step"] == BuildStepKey.EXECUTE_PHASES.value
        worker_runs = await repo.list_worker_runs("cycle-1", project_id="project-1", stage="build")
        assert worker_runs[-1]["status"] == "failed"

    async def test_a_true_done_marker_cannot_override_a_failed_implementation_check(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)
        stage_attempt_id = await _build_stage_attempt_id(repo)
        dispatcher = _ContradictoryPhaseDispatcher(plan=TWO_PHASE_PLAN)

        result, _ = await _run_build(repo, root, dispatcher=dispatcher)

        assert not result.produced_usable_evidence
        assert len(dispatcher.phase_units) == 2
        view = await repo.build_workflow_view(project_id="project-1", stage_attempt_id=stage_attempt_id)
        assert view["phases"][0]["status"] == StepState.FAILED.value
        assert "Automated implementation tests" in result.note
        assert view["next_step"] == BuildStepKey.EXECUTE_PHASES.value


class TestAPhaseDoesNotPayForTheDesignOnEveryTurn:
    """The excerpt is a first-call convenience; a phase is a tool loop.

    Whatever sits in a phase's prompt is re-sent on every model call it makes,
    and the approved Design is by far the largest thing there — measured at 45%
    of every call on a real pilot, four calls per phase, for a document the
    planner had already decomposed into that phase's own objective. The planner
    still receives it in full: it is the seat whose whole job is reading it.
    """

    async def test_the_planner_reads_the_design_and_the_phases_do_not(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        _result, dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN))

        body = "Fit a genomic prediction model and report held-out accuracy."
        assert all(body in unit.prompt for unit in dispatcher.planner_units)
        assert dispatcher.phase_units
        assert not any(body in unit.prompt for unit in dispatcher.phase_units)

    async def test_a_phase_is_still_told_which_design_it_implements(self, project) -> None:
        repo, root = project
        await _ready_for_build(repo)

        _result, dispatcher = await _run_build(repo, root, dispatcher=_WritingDispatcher(plan=TWO_PHASE_PLAN))

        prompt = dispatcher.phase_units[0].prompt
        # Dropping the body must not drop the binding: a phase that cannot name
        # the document it implements is the guessing this bundle exists to end.
        assert DESIGN_URI in prompt
        assert DESIGN_HASH in prompt
        assert "read" in prompt.lower()


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
