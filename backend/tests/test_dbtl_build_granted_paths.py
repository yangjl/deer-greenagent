"""A Build phase runs on paths the server issued, whichever agent implements it.

The failure this covers crossed two agents on one cycle: a specialist and the
generalist each wrote a host path into generated code, and one of those paths
had never existed on this machine. The contract itself asked for it -- workers
were told to use absolute paths -- so the fix is a contract that issues the
paths and a refusal that catches source which invents its own.
"""

from __future__ import annotations

from deerflow.agents.dbtl.live_stage.build_phases import (
    BuildPhaseManifest,
    assign_phase,
    verify_granted_paths,
)
from deerflow.dbtl.agent_selector import AgentCandidate, Assignment
from deerflow.dbtl.build_grant import INPUT_ENV_PREFIX, WORKSPACE_ENV
from deerflow.dbtl.build_plan import BuildPhase
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_runner import build_prompt
from deerflow.dbtl.stage_spec import resolve_stage_spec

WORKSPACE = "/mnt/user-data"


def _manifest(entry_point: str = "/mnt/user-data/outputs/dbtl/c/build/src/run.py") -> BuildPhaseManifest:
    return BuildPhaseManifest(
        entry_point=entry_point,
        declared_outputs=(entry_point,),
        completion_condition="outputs exist",
        declared_inputs=(),
        version=2,
    )


class TestAnEntryPointMayNotNameItsOwnLocation:
    def test_the_observed_host_path_is_refused(self) -> None:
        source = 'DATA = Path("/Users/someone/app/backend/.deer-flow/users/u1/trial.csv")\n'

        error = verify_granted_paths(_manifest(), read_source=lambda _ref: source, allowed_roots=(WORKSPACE,))

        assert "/Users/someone/app/backend/.deer-flow/users/u1/trial.csv" in error
        assert "line 1" in error

    def test_reading_the_input_from_the_environment_passes(self) -> None:
        source = f'import os\nDATA = os.environ["{INPUT_ENV_PREFIX}1"]\nOUT = os.environ["{WORKSPACE_ENV}"]\n'

        assert verify_granted_paths(_manifest(), read_source=lambda _ref: source, allowed_roots=(WORKSPACE,)) == ""

    def test_a_hardcoded_workspace_input_is_refused_when_the_server_issues_it(self) -> None:
        source = 'DATA = "/mnt/user-data/trial_2025_yield.csv"\n'

        error = verify_granted_paths(_manifest(), read_source=lambda _ref: source, allowed_roots=())

        assert "/mnt/user-data/trial_2025_yield.csv" in error

    def test_an_unreadable_entry_point_is_not_refused_by_this_check(self) -> None:
        # A compiled or binary entry point has no source to judge. Refusing it
        # here would be this check asserting something it never read; the
        # server's own execution is what decides those cases.
        assert verify_granted_paths(_manifest(), read_source=lambda _ref: None, allowed_roots=(WORKSPACE,)) == ""

    def test_the_refusal_says_what_to_do_instead(self) -> None:
        source = 'DATA = "/tmp/scratch/trial.csv"\n'

        error = verify_granted_paths(_manifest(), read_source=lambda _ref: source, allowed_roots=(WORKSPACE,))

        assert INPUT_ENV_PREFIX in error
        assert WORKSPACE_ENV in error

    def test_a_hardcoded_path_in_an_imported_source_file_is_also_refused(self) -> None:
        manifest = BuildPhaseManifest(
            entry_point="/mnt/user-data/outputs/dbtl/c/run.py",
            declared_outputs=(
                "/mnt/user-data/outputs/dbtl/c/run.py",
                "/mnt/user-data/outputs/dbtl/c/helpers.py",
                "/mnt/user-data/outputs/dbtl/c/report.json",
            ),
            completion_condition="outputs exist",
            version=2,
        )
        sources = {
            manifest.entry_point: "from helpers import load\n",
            "/mnt/user-data/outputs/dbtl/c/helpers.py": 'DATA = "/Users/a/trial.csv"\n',
            # Data/report outputs are not source-scanned.
            "/mnt/user-data/outputs/dbtl/c/report.json": '{"note":"/Users/a/not-an-execution-path"}',
        }

        error = verify_granted_paths(manifest, read_source=sources.get, allowed_roots=())

        assert "helpers.py" in error
        assert "/Users/a/trial.csv" in error
        assert "not-an-execution-path" not in error


class TestTheContractIssuesPathsRatherThanDemandingThem:
    def test_v12_tells_the_worker_where_its_inputs_are(self) -> None:
        spec = resolve_stage_spec("build", version=12)

        prompt = build_prompt(spec, Assignment(capability=Capability.SOFTWARE_ENGINEERING, agent_name="coder", via_generalist=False), context="ctx")

        assert f"{INPUT_ENV_PREFIX}1" in prompt
        assert WORKSPACE_ENV in prompt

    def test_v12_no_longer_instructs_the_worker_to_use_absolute_paths(self) -> None:
        # This sentence is the one that specified the failure: workers were told
        # to embed absolute paths, and two of them did.
        spec = resolve_stage_spec("build", version=12)

        prompt = build_prompt(spec, Assignment(capability=Capability.SOFTWARE_ENGINEERING, agent_name="coder", via_generalist=False), context="ctx")

        assert "Use absolute /mnt/user-data paths for the entry point" not in prompt

    def test_the_gate_is_absent_from_the_previous_contract(self) -> None:
        assert "granted_paths_only" not in resolve_stage_spec("build", version=11).validity_gates
        assert "granted_paths_only" in resolve_stage_spec("build", version=12).validity_gates

    def test_the_rerun_record_still_names_absolute_paths(self) -> None:
        # The record is the server's, and it is what lets the server re-run the
        # work. Only the *code* is barred from composing paths.
        spec = resolve_stage_spec("build", version=12)

        prompt = build_prompt(spec, Assignment(capability=Capability.SOFTWARE_ENGINEERING, agent_name="coder", via_generalist=False), context="ctx")

        assert "provenance.rerun_spec" in prompt


class TestChoosingAnImplementerIsEfficiencyNotAuthority:
    def _phase(self) -> BuildPhase:
        return BuildPhase(
            phase_key="implement",
            title="Implement",
            objective="write it",
            capability=Capability.SOFTWARE_ENGINEERING,
        )

    def test_a_configured_implementer_replaces_the_stand_in(self) -> None:
        candidates = [
            AgentCandidate(name="general-purpose", is_generalist=True),
            AgentCandidate(name="coder"),
        ]

        assignment = assign_phase(self._phase(), candidates, implementer="coder")

        assert assignment.agent_name == "coder"

    def test_a_configured_implementer_is_still_recorded_as_a_stand_in(self) -> None:
        # Nothing about the preferred agent covers the capability. A reviewer
        # who cannot tell a specialist from a preference has lost the
        # distinction capability selection exists to keep.
        candidates = [
            AgentCandidate(name="general-purpose", is_generalist=True),
            AgentCandidate(name="coder"),
        ]

        assignment = assign_phase(self._phase(), candidates, implementer="coder")

        assert assignment.via_generalist is True

    def test_a_registered_specialist_still_wins(self) -> None:
        candidates = [
            AgentCandidate(name="general-purpose", is_generalist=True),
            AgentCandidate(name="coder"),
            AgentCandidate(name="engineer", capabilities=frozenset({Capability.SOFTWARE_ENGINEERING})),
        ]

        assignment = assign_phase(self._phase(), candidates, implementer="coder")

        assert assignment.agent_name == "engineer"
        assert assignment.via_generalist is False

    def test_an_unregistered_implementer_falls_back_rather_than_failing(self) -> None:
        candidates = [AgentCandidate(name="general-purpose", is_generalist=True)]

        assignment = assign_phase(self._phase(), candidates, implementer="not-registered")

        assert assignment.agent_name == "general-purpose"
        assert assignment.covered is True

    def test_no_implementer_configured_keeps_the_existing_behaviour(self) -> None:
        candidates = [AgentCandidate(name="general-purpose", is_generalist=True)]

        assert assign_phase(self._phase(), candidates).agent_name == "general-purpose"

    def test_an_uncoverable_phase_is_still_refused(self) -> None:
        assignment = assign_phase(self._phase(), [AgentCandidate(name="other")], implementer="missing")

        assert assignment.agent_name == ""
        assert assignment.covered is False
