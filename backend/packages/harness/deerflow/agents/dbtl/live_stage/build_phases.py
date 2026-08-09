"""Turning a Build plan into work units, one phase at a time.

Each phase asks for a **capability** and gets the best available agent through
the selection machinery the Design council already uses. Three properties follow,
and all three matter more as specialists are added:

* a deployment with no registered specialists still works — every phase runs as
  `general-purpose`, honestly recorded as a generalist stand-in, which is
  today's behaviour and not a regression;
* registering a specialist later changes **who runs which phase and nothing
  else**, because the phase asked for a capability rather than an agent name; and
* the record names the capability requested *and* the agent that covered it, so
  a reviewer reading "quantitative genetics: general-purpose" knows what they
  are looking at.

Phases are **sequential**, and a phase may read the outputs of the phases before
it — that is what lets phase 3 fit a model phase 1 simulated — but never modify
them. An earlier phase's output is an input, hash-bound like any other.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any

from deerflow.agents.dbtl.live_stage.workspace import SHELL_WORKSPACE_IDIOM, STAGE_UNIT_WORKSPACE_PLACEHOLDER
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.build_grant import INPUT_ENV_PREFIX, WORKSPACE_ENV, describe_foreign_paths, scan_foreign_paths
from deerflow.dbtl.build_plan import PLANNER_CONTRACT, BuildPhase, BuildPhasePlan
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_runner import BUILD_PLAN_OUTPUT, WorkUnit
from deerflow.dbtl.stage_spec import StageSpec
from deerflow.dbtl.worker_result import QualityCheck, StageWorkerResult, WorkerStatus

#: The seat that draws the plan. Read-only by role, like the summarizer: it
#: writes nothing, runs nothing, and dispatches nobody.
PLANNER_ROLE = "planner"
PHASE_ROLE = "phase"
PHASE_DONE_CHECK = "phase_done_condition"
CORRECTION_HANDOFF_MAX_ITEMS = 20

BUILD_PRESENTATION_RESULT_NOTE = """
Build result declarations (required in addition to the shared result):
- Return `key_outcomes` as a list of every verified numeric result this phase
  produced: [{"name": "metric or result", "value": 0.0, "unit": "optional"}].
- Return `figures` as a list of every figure this phase produced. Save each
  figure beneath DBTL_WORKSPACE — e.g.
  `fig.savefig(os.path.join(os.environ['DBTL_WORKSPACE'], 'artifacts', 'name.png'))`
  — and never hardcode a figure location (no '/test/...', no bare '/mnt/...', no
  '/plot.png'); a hardcoded path is refused before the phase runs:
  [{"path": "<the DBTL_WORKSPACE-relative file you saved>", "caption": "what it is", "shows": "what it demonstrates"}].
- Use an empty list only when this phase genuinely produced none. A numeric
  result mentioned in summary, claims, evidence, or an output file must also
  appear in `key_outcomes`; otherwise the verified Build cannot be presented
  for human review.
""".strip()

#: The house plot style, applied *in the entry point source* rather than through
#: the environment. Test re-runs the recorded command in a fresh workspace and
#: requires every figure to match its approved hash byte for byte, so a style set
#: via matplotlibrc/MPLCONFIGDIR would not travel with the script and the re-run
#: would render differently. In the source it reproduces anywhere.
#:
#: Kept byte-identical to the ``dbtl-plot-style`` skill by
#: ``test_dbtl_plot_style.py`` — edit the SKILL.md and the test will name this
#: constant if the two drift.
DBTL_PLOT_STYLE_BLOCK = """plt.rcParams.update({
    "figure.figsize": (6.4, 4.0), "figure.dpi": 150, "savefig.dpi": 150,
    "savefig.bbox": "tight", "savefig.facecolor": "white",
    "font.family": "sans-serif", "font.sans-serif": ["DejaVu Sans"],
    "font.size": 11, "axes.titlesize": 13, "axes.labelsize": 12,
    "xtick.labelsize": 10, "ytick.labelsize": 10, "legend.fontsize": 10,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.alpha": 0.30, "grid.linewidth": 0.6,
    "axes.axisbelow": True, "legend.frameon": False,
    "lines.linewidth": 2.0, "lines.markersize": 6,
    "patch.edgecolor": "white", "patch.linewidth": 0.5,
    "axes.prop_cycle": plt.cycler(color=[
        "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
        "#e87ba4", "#008300", "#4a3aa7", "#e34948"]),
})"""

BUILD_RERUN_RESULT_NOTE = """
Build rerun declaration (required in provenance.rerun_spec):
- Return {"version": 1, "entry_point": "/mnt/user-data/...", "command": "exact command",
  "seed": "", "inputs": ["/mnt/user-data/..."], "environment": {"runtime": "version"},
  "configuration": [], "expected_outputs": ["/mnt/user-data/..."]}.
- expected_outputs contains only files the entry point itself creates when run
  in a fresh DBTL_WORKSPACE. Do not include source code, worker-created audit
  logs, or files that merely existed before the entry point ran.
""".strip()

GENERALIST = "general-purpose"

#: Token ceiling for a fresh correction attempt. 40K proved systematically too
#: small: every correction of a reporting/packaging phase token_capped at
#: 48–55K observed usage — the worker must re-read the failure evidence, write
#: the fixed implementation, run it, and emit the structured result. Corrections
#: stay cheaper than a full phase (500K) but need real headroom.
CORRECTION_MAX_TOKENS = 200_000
SERVER_NON_EXECUTABLE_SUFFIXES = frozenset({".ipynb", ".md", ".json", ".csv", ".html", ".txt"})


def is_server_executable_entry_point(path: str) -> bool:
    """Whether the server knows how to invoke this phase entry point."""

    # Unknown suffixes may be compiled executables and are run directly. Known
    # document/data formats can never be an entry point.
    return PurePosixPath(path).suffix.lower() not in SERVER_NON_EXECUTABLE_SUFFIXES


def required_phase_manifest_version(spec: StageSpec) -> int:
    if "server_executed_entry_point" in spec.validity_gates:
        return 3
    if "narrow_implementation_inputs" in spec.validity_gates:
        return 2
    return 1


@dataclass(frozen=True, slots=True)
class BuildPhaseManifest:
    """The minimal worker declaration the server binds to workspace facts."""

    entry_point: str
    declared_outputs: tuple[str, ...]
    completion_condition: str
    declared_inputs: tuple[str, ...] = ()
    execution_inputs: tuple[str, ...] = ()
    version: int = 1

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "version": self.version,
            "entry_point": self.entry_point,
            "declared_outputs": list(self.declared_outputs),
            "completion_condition": self.completion_condition,
        }
        if self.version >= 2:
            payload["declared_inputs"] = list(self.declared_inputs)
        if self.version >= 3:
            payload["execution_inputs"] = list(self.execution_inputs)
        return payload


def parse_phase_manifest(value: Any) -> BuildPhaseManifest | None:
    if not isinstance(value, Mapping) or value.get("version") not in {1, 2, 3} or isinstance(value.get("version"), bool):
        return None
    version = int(value["version"])
    entry_point = value.get("entry_point")
    outputs = value.get("declared_outputs")
    completion = value.get("completion_condition")
    if not isinstance(entry_point, str) or not entry_point.strip() or len(entry_point) > 1_024:
        return None
    if not isinstance(outputs, list) or not outputs or len(outputs) > 500:
        return None
    normalized: list[str] = []
    for item in outputs:
        if not isinstance(item, str) or not item.strip() or len(item) > 1_024:
            return None
        normalized.append(item.strip())
    if len(set(normalized)) != len(normalized) or not isinstance(completion, str) or len(completion) > 600:
        return None
    raw_inputs = value.get("declared_inputs", [])
    if version >= 2 and (not isinstance(raw_inputs, list) or len(raw_inputs) > 500):
        return None
    declared_inputs: list[str] = []
    for item in raw_inputs if isinstance(raw_inputs, list) else []:
        if not isinstance(item, str) or not item.strip() or len(item) > 1_024:
            return None
        declared_inputs.append(item.strip())
    if len(set(declared_inputs)) != len(declared_inputs):
        return None
    raw_execution_inputs = value.get("execution_inputs", [])
    if version >= 3 and (not isinstance(raw_execution_inputs, list) or len(raw_execution_inputs) > 500):
        return None
    execution_inputs: list[str] = []
    for item in raw_execution_inputs if isinstance(raw_execution_inputs, list) else []:
        if not isinstance(item, str) or not item.strip() or len(item) > 1_024:
            return None
        execution_inputs.append(item.strip())
    if len(set(execution_inputs)) != len(execution_inputs):
        return None
    return BuildPhaseManifest(
        entry_point=entry_point.strip(),
        declared_outputs=tuple(normalized),
        completion_condition=completion.strip(),
        declared_inputs=tuple(declared_inputs),
        execution_inputs=tuple(execution_inputs),
        version=version,
    )


def _entry_point_published(entry_point: str, refs: Sequence[str]) -> bool:
    """Whether the entry point names one of the published files.

    Tolerates a granularity mismatch that is not a real error: a worker may
    give the entry point as its unit-relative path (``src/run.py``) while
    listing the same file in ``artifact_refs`` as its full virtual path
    (``/mnt/user-data/outputs/.../src/run.py``). Both resolve to one file, so
    match when one path's trailing components equal the other's — which still
    rejects an entry point that is genuinely not among the published files
    (``run.py`` never matches ``.../subrun.py``, only ``.../run.py``).
    """
    ep = str(entry_point).strip()
    if not ep:
        return False
    ep_parts = [p for p in ep.strip("/").split("/") if p]
    if not ep_parts:
        return False
    for ref in refs:
        r = str(ref).strip()
        if not r:
            continue
        if r == ep:
            return True
        r_parts = [p for p in r.strip("/").split("/") if p]
        n = min(len(ep_parts), len(r_parts))
        if n and ep_parts[-n:] == r_parts[-n:]:
            return True
    return False


def verify_phase_manifest(
    result: StageWorkerResult,
    *,
    published: Sequence[Mapping[str, Any]],
    completion_condition: str,
    required_version: int = 1,
) -> tuple[BuildPhaseManifest | None, str]:
    """Bind one worker manifest to the files the server actually published."""

    manifest = parse_phase_manifest(result.provenance.get("phase_manifest"))
    if manifest is None:
        return None, "The Build phase did not return a valid versioned phase manifest."
    if manifest.version != required_version:
        return None, f"The Build phase manifest must use version {required_version}."
    published_uris = tuple(str(item.get("uri") or "") for item in published if str(item.get("uri") or ""))
    if set(manifest.declared_outputs) != set(published_uris) or len(manifest.declared_outputs) != len(published_uris):
        return None, "The Build phase manifest does not name exactly the outputs the server published."
    if not _entry_point_published(manifest.entry_point, published_uris):
        return None, "The Build phase entry point is not one of its governed published files."
    if required_version >= 3 and not is_server_executable_entry_point(manifest.entry_point):
        return None, "The Build phase entry point is not an executable script type supported by the server."
    if manifest.completion_condition != completion_condition.strip():
        return None, "The Build phase manifest changed the versioned completion condition from the recorded plan."
    if manifest.version >= 3 and not set(manifest.execution_inputs).issubset(manifest.declared_inputs):
        return None, "The Build phase manifest's execution_inputs must be a subset of declared_inputs."
    return manifest, ""


def verify_unpublished_phase_manifest(
    result: StageWorkerResult,
    *,
    completion_condition: str,
    required_version: int,
) -> tuple[BuildPhaseManifest | None, str]:
    """Validate the declaration before any worker bytes become governed.

    The post-publication verifier below binds remapped URIs. This sibling binds
    the worker's original grant paths so the server can execute the entry point
    *before* publication; otherwise a failing command would already have copied
    its outputs into the governed tree.
    """
    manifest = parse_phase_manifest(result.provenance.get("phase_manifest"))
    if manifest is None:
        return None, "The Build phase did not return a valid versioned phase manifest."
    if manifest.version != required_version:
        return None, f"The Build phase manifest must use version {required_version}."
    # Publication keys off artifact_refs (the files the worker asks to publish),
    # and the post-publication verifier below binds declared_outputs to the files
    # the server actually wrote. So artifact_refs is the worker's real output set;
    # reconcile the manifest to it instead of discarding a completed phase over a
    # bookkeeping desync (e.g. a notebook listed in one array but not the other).
    # The genuine integrity guarantee is still enforced downstream against ground
    # truth, and this reconciled list is what the granted-paths source scan reads.
    published_refs = tuple(dict.fromkeys(result.artifact_refs))
    if not published_refs:
        return None, "The Build phase did not ask the server to publish any outputs."
    if not _entry_point_published(manifest.entry_point, published_refs):
        return None, "The Build phase entry point is not one of the files it asked the server to publish."
    manifest = replace(manifest, declared_outputs=published_refs)
    if required_version >= 3 and not is_server_executable_entry_point(manifest.entry_point):
        return None, "The Build phase entry point is not an executable script type supported by the server."
    if manifest.completion_condition != completion_condition.strip():
        return None, "The Build phase manifest changed the versioned completion condition from the recorded plan."
    if manifest.version >= 3 and not set(manifest.execution_inputs).issubset(manifest.declared_inputs):
        return None, "The Build phase manifest's execution_inputs must be a subset of declared_inputs."
    return manifest, ""


BUILD_BOOKKEEPING_CHECK = "build_manifest_bookkeeping"


def reconcile_published_manifest(
    result: StageWorkerResult,
    *,
    published: Sequence[Mapping[str, Any]],
    completion_condition: str,
    required_version: int,
) -> BuildPhaseManifest | None:
    """Bind a phase manifest to the files the server actually published.

    Used when the worker's manifest disagreed with reality *after* the entry
    point already ran and published — a bookkeeping desync, not a failure. The
    returned manifest's declared_outputs are the published files, so the
    post-publish security scan still targets a known, published entry point.
    Returns None when there is nothing to bind to (no parseable manifest, wrong
    version, or an entry point not among the published files); those are not
    bookkeeping and stay hard.
    """
    manifest = parse_phase_manifest(result.provenance.get("phase_manifest"))
    if manifest is None or manifest.version != required_version or manifest.completion_condition != completion_condition.strip() or (manifest.version >= 3 and not set(manifest.execution_inputs).issubset(manifest.declared_inputs)):
        return None
    published_uris = tuple(dict.fromkeys(str(item.get("uri") or "") for item in published if str(item.get("uri") or "")))
    if not published_uris or not _entry_point_published(manifest.entry_point, published_uris):
        return None
    if required_version >= 3 and not is_server_executable_entry_point(manifest.entry_point):
        return None
    declared_names = [PurePosixPath(path).name for path in manifest.declared_outputs]
    published_names = [PurePosixPath(str(item.get("source_path") or item.get("uri") or "")).name for item in published if str(item.get("uri") or "")]
    if len(declared_names) != len(published_names) or len(set(declared_names)) != len(declared_names) or sorted(declared_names) != sorted(published_names):
        return None
    return replace(manifest, declared_outputs=published_uris)


def record_build_observation(result: StageWorkerResult, note: str) -> StageWorkerResult:
    """Record a bookkeeping discrepancy on a phase result without failing it.

    The note rides out on ``limitations`` — which already flow to the Build
    summary, the review deck, and Test — and as a failed quality check, so Test
    can judge whether it touches the science and the human sees it at the gate.
    """
    note = note.strip()
    if not note:
        return result
    limitation = f"Build bookkeeping observation (published bytes unaffected): {note}"
    check = QualityCheck(name=BUILD_BOOKKEEPING_CHECK, passed=False, detail=note)
    return replace(
        result,
        limitations=tuple(dict.fromkeys((*result.limitations, limitation))),
        quality_checks=(*result.quality_checks, check),
    )


#: A phase whose worker was stopped by the token budget *after* it had already
#: written its structured result and manifest. The work is still judged by the
#: server's own execution of the entry point; only the worker's further prose
#: and self-review were cut short.
BUILD_CAP_SALVAGE_CHECK = "build_phase_cap_salvage"
BUILD_CAP_SALVAGE_MARKER = "build_phase_cap_salvage_v1"

#: Deliberately narrower than ``was_capped``. A turn or loop cap means the worker
#: was going in circles, so its own report is the part least worth trusting; a
#: token cap only means it ran long.
SALVAGEABLE_STOP_REASON = "token_capped"


def is_capped_phase_salvageable(result: StageWorkerResult, *, required_version: int) -> bool:
    """Whether a capped phase reported enough to be worth putting through the gates.

    This is only the question "is there anything to verify against?". It grants
    nothing: the containment and server-execution gates still decide, and a cap
    never excuses either. A cap that landed before the structured result has no
    manifest, so there is nothing to check and it stays a total failure.
    """

    if result.status is not WorkerStatus.COMPLETED or result.stop_reason != SALVAGEABLE_STOP_REASON:
        return False
    manifest = parse_phase_manifest(result.provenance.get("phase_manifest"))
    return manifest is not None and manifest.version == required_version


def admit_capped_phase(result: StageWorkerResult) -> tuple[StageWorkerResult, str]:
    """Carry a token-capped but complete phase into the hard gates, flagged.

    The cap is cleared from the result so the rest of the Build path treats it
    as ordinary evidence — the gates it must still clear are unchanged — and the
    original stop reason survives on provenance. The flag rides the same two
    carriers as :func:`record_build_observation`: a limitation that reaches the
    Build summary, deck and Test, and a failed non-gating check Test is told how
    to weigh. Returns the result unchanged when already salvaged.
    """

    reason = result.stop_reason or SALVAGEABLE_STOP_REASON
    if result.provenance.get("capped_phase_salvage"):
        return result, reason
    note = (
        f"This phase's worker was stopped by its token budget ({reason}) after it had already written "
        "its structured result and phase manifest. The server then executed the declared entry point, "
        "verified the declared outputs and published the bytes, so the execution this phase is judged on "
        "is complete. What the cap cut short is the worker's own further work and self-review, so treat "
        "its prose and self-reported claims as possibly unfinished."
    )
    limitation = f"Build phase cap salvage (server execution verified): {note}"
    check = QualityCheck(name=BUILD_CAP_SALVAGE_CHECK, passed=False, detail=note)
    salvaged = replace(
        result,
        stop_reason=None,
        limitations=tuple(dict.fromkeys((*result.limitations, limitation))),
        quality_checks=(*result.quality_checks, check),
        provenance={**result.provenance, "source_stop_reason": reason, "capped_phase_salvage": BUILD_CAP_SALVAGE_MARKER},
    )
    return salvaged, reason


MAX_SCANNED_ENTRY_POINT_BYTES = 2 * 1024 * 1024
_SCANNED_SOURCE_SUFFIXES = frozenset({".py", ".r", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jl", ".rb", ".pl"})


def verify_granted_paths(
    manifest: BuildPhaseManifest,
    *,
    read_source: Callable[[str], str | None],
    allowed_roots: Sequence[str],
) -> str:
    """Refuse an entry point that names a location outside this phase's grant.

    ``read_source`` resolves a published URI to its text, or returns ``None``
    when the file is not readable text -- a compiled or binary entry point is
    not refused here, because this check reads source and has nothing to say
    about bytes it cannot read. The server's own execution of the entry point is
    what decides those cases.

    Returning a message rather than raising keeps this on the same footing as
    ``verify_phase_manifest``: the caller records one failed phase with a
    reviewer-readable reason instead of losing the run to an exception.
    """
    references = [manifest.entry_point]
    references.extend(reference for reference in manifest.declared_outputs if reference != manifest.entry_point and PurePosixPath(reference).suffix.lower() in _SCANNED_SOURCE_SUFFIXES)
    for reference in references:
        source = read_source(reference)
        if source is None:
            continue
        findings = scan_foreign_paths(source, allowed_roots=allowed_roots)
        if findings:
            # One exact refusal is enough to stop publication. Keeping the
            # diagnostic bounded also prevents a generated source tree from
            # turning the failure report itself into another large prompt.
            return f"{reference}: {describe_foreign_paths(findings)}"
    return ""


def is_non_gating_build_check(name: str) -> bool:
    """Whether Test, rather than Build, owns this failed check's verdict."""

    normalized = " ".join(name.lower().replace("_", " ").replace("-", " ").split())
    return (
        "reproduc" in normalized
        or "repeat run" in normalized
        or "rerun" in normalized
        # A manifest/output declaration desync the server already reconciled to the
        # published files is bookkeeping, not a Build gate: the science bytes ran and
        # published. Test and the human reviewer own whether the discrepancy matters.
        or "bookkeeping" in normalized
        # An optional notebook or report the sandbox cannot *execute* because a runtime
        # tool (e.g. jupyter/nbconvert) is unavailable is a recorded limitation, not a
        # Build gate: the phase is judged on its executable entry point and required data
        # outputs. Test and the human reviewer own whether an un-run notebook matters.
        or "notebook" in normalized
        or "jupyter" in normalized
        or "nbconvert" in normalized
        # A token budget that cut the worker short *after* it wrote its structured
        # result is a recorded limitation, not a Build gate: the server still
        # executed the entry point, verified the declared outputs and published the
        # bytes. Test and the human reviewer own whether the unfinished worker-side
        # work matters. Two words rather than a bare "salvage" because a worker
        # authors its own check names.
        or "cap salvage" in normalized
    )


def gating_failed_phase_checks(result: StageWorkerResult, required_name: str = PHASE_DONE_CHECK) -> tuple[str, ...]:
    """Name failed implementation checks eligible for one bounded correction."""

    return tuple(item.name.strip() for item in result.quality_checks if item.name.strip() and not item.passed and (item.name.strip() == required_name or not is_non_gating_build_check(item.name)))


def phase_completion_error(result: StageWorkerResult, required_name: str) -> str:
    """Explain why a server-required Build phase assertion did not pass."""

    if not required_name or result.status not in {WorkerStatus.COMPLETED, WorkerStatus.FAILED}:
        return ""
    checks = [item for item in result.quality_checks if item.name.strip() == required_name]
    # A failed result never satisfies the phase, but one precise failed done
    # check is enough to tell v12's separate correction worker what to repair.
    # Other failed shapes remain untrusted and fail closed as before.
    if result.status is WorkerStatus.FAILED:
        if len(checks) == 1 and not checks[0].passed:
            return checks[0].detail.strip() or f"The worker reported that {required_name!r} was not satisfied."
        return ""
    if len(checks) != 1:
        return f"The worker must return exactly one {required_name!r} quality check before this phase can finish."
    if not checks[0].passed:
        detail = checks[0].detail.strip()
        return detail or f"The worker reported that {required_name!r} was not satisfied."
    contradictions = [item for item in result.quality_checks if item.name.strip() != required_name and not item.passed and not is_non_gating_build_check(item.name)]
    if contradictions:
        names = ", ".join(item.name.strip() for item in contradictions[:4])
        return f"The phase reported {required_name!r} as complete, but these implementation checks failed: {names}."
    return ""


@dataclass(frozen=True, slots=True)
class PhaseAssignment:
    """Which agent covered one phase's capability, and whether it specialises.

    `agent_name` is empty exactly when nothing can cover the phase. `covered`
    is the property callers ask, because "no agent" and "the generalist" must
    never be the same branch.
    """

    phase: BuildPhase
    agent_name: str
    via_generalist: bool

    @property
    def covered(self) -> bool:
        return bool(self.agent_name)


def assign_phase(
    phase: BuildPhase,
    candidates: Sequence[AgentCandidate],
) -> PhaseAssignment:
    """Resolve one phase's capability against the registered agents.

    A specialist wins; otherwise the **registered generalist** covers it and the
    stand-in is recorded rather than hidden. Recording it is the whole point: a
    reviewer reading nothing cannot tell a specialist from a stand-in.

    "Generalist" means the agent registered as one, not whichever agent happens
    to sort first. Falling back to an arbitrary candidate is the same silent
    swap capability selection exists to prevent, and worse than the version it
    replaced: `bash` is a real registered subagent, so a deployment that
    registered no generalist would have run a modelling phase on a command
    runner and recorded it as a generalist stand-in. With nothing able to cover
    it the phase is refused, which is a sentence a person can act on.
    """
    available = [item for item in candidates if getattr(item, "available", True)]
    specialist = next((item for item in available if phase.capability in item.capabilities), None)
    if specialist is not None:
        return PhaseAssignment(phase=phase, agent_name=specialist.name, via_generalist=False)
    generalist = next((item for item in available if item.name == GENERALIST), None)
    return PhaseAssignment(phase=phase, agent_name=generalist.name if generalist else "", via_generalist=bool(generalist))


def planner_unit(
    *,
    attempt_id: str,
    agent_name: str,
    context: str,
    model: str | None = None,
) -> WorkUnit:
    """The bounded planning seat.

    It receives the input bundle and the cycle's own question, and returns a
    plan. Nothing it can say dispatches anything: the plan is proposed, and the
    server decides what to run from it.
    """
    prompt = "\n\n".join([PLANNER_CONTRACT, "Build input bundle:", context.strip() or "(none supplied)"])
    return WorkUnit(
        unit_id=f"{attempt_id}-plan",
        capability=Capability.SOFTWARE_ENGINEERING.value,
        agent_name=agent_name,
        prompt=prompt,
        role=PLANNER_ROLE,
        model=model,
        output_contract=BUILD_PLAN_OUTPUT,
    )


def phase_unit(
    assignment: PhaseAssignment,
    *,
    index: int,
    attempt_id: str,
    attempt_token: str,
    spec: StageSpec,
    context: str,
    completed: Sequence[Mapping[str, object]] = (),
    result_contract: str = "",
    granted_inputs: Sequence[str] = (),
) -> WorkUnit:
    """One phase's work unit, carrying what the phases before it produced.

    `attempt_token` is the step attempt this unit belongs to, and it is in the
    unit id because the unit id is what the isolated workspace is derived from.
    Without it a retry inherited the failed attempt's directory: half-written
    files, a stale log, and an output the previous run had already declared —
    which the publisher would then copy into the governed tree as this attempt's
    evidence.
    """
    phase = assignment.phase
    preceding = (
        [
            "Outputs of the phases before this one. You may read them; you may not modify them —",
            "they are inputs, hash-bound like any other.",
            json.dumps(list(completed), sort_keys=True, ensure_ascii=False),
        ]
        if completed
        else ["This is the first phase; nothing precedes it."]
    )
    lines = [
        f"You are running one phase of the {spec.title} stage of a DBTL research cycle.",
        "",
        f"Phase {index} of this build: {phase.title}",
        f"Objective: {phase.objective}",
        f"Capability requested: {phase.capability.value}",
        *([f"Expected inputs: {'; '.join(phase.inputs)}"] if phase.inputs else []),
        *([f"Expected outputs: {'; '.join(phase.outputs)}"] if phase.outputs else []),
        *([f"Done when: {phase.done_condition}"] if phase.done_condition else []),
        "",
        *preceding,
        "",
        "Project context:",
        context.strip() or "(none supplied)",
        "",
        "Write each approved deliverable at its exact project-relative path from",
        "build_input_bundle.deliverable_manifest. Do not add an `outputs/` prefix or",
        "otherwise move a path unless that prefix is already part of expected_paths.",
        f"Write every new implementation, derived output, and execution log under {STAGE_UNIT_WORKSPACE_PLACEHOLDER}.",
        "The server has already created src/, tests/, config/, artifacts/, and logs/ there.",
        "Use write_file or str_replace for source, configuration, and documentation. Use Bash",
        "only for short execution and verification commands; do not embed complete files in",
        "Bash heredocs or in a Python write_text wrapper.",
        "Before changing an existing file, read its current version. After each successful edit,",
        "re-read before editing that file again. If a tool returns a recoverable error, follow its",
        "recommended next action or choose a different tool; do not repeat the identical failing call.",
        SHELL_WORKSPACE_IDIOM,
        *(
            [
                "",
                "The server issues your paths; do not compose your own. Your code must read its inputs",
                f"from {INPUT_ENV_PREFIX}1, {INPUT_ENV_PREFIX}2, ... in the server-issued order below, with {INPUT_ENV_PREFIX}COUNT",
                f"holding how many) and write beneath {WORKSPACE_ENV}, either by reading those environment",
                "variables directly. An entry point that",
                "names an absolute path to its data is refused before it runs, and the refusal names the",
                "literal and the line. A path you compose yourself is a guess about a filesystem you",
                "cannot see, and a wrong guess costs the whole phase.",
                "Server-issued input order for this phase:",
                *(f"  {INPUT_ENV_PREFIX}{position}={path}" for position, path in enumerate(granted_inputs, start=1)),
                "In phase_manifest.declared_inputs list the exact subset your implementation consumed; this does not renumber the environment.",
            ]
            if "granted_paths_only" in spec.validity_gates
            else []
        ),
        *(
            [
                "",
                "WORKED EXAMPLE — the exact shape a correct entry point must have. Every path is",
                "derived from the environment; not one is a literal. Copy this discipline:",
                "```python",
                "import os, json",
                "ws = os.environ['DBTL_WORKSPACE']                      # your workspace root",
                "n = int(os.environ.get('DBTL_INPUT_COUNT', '0'))",
                "inputs = [os.environ[f'DBTL_INPUT_{i}'] for i in range(1, n + 1)]",
                "out_csv = os.path.join(ws, 'artifacts', 'result.csv')  # write only under ws",
                "fig_png = os.path.join(ws, 'artifacts', 'plot.png')    # figures too: fig.savefig(fig_png)",
                "# ... do the work; write every output (data AND figures) beneath ws ...",
                "print(json.dumps({'ok': True}))",
                "```",
                "Hard rules the server enforces — each one costs the ENTIRE phase when broken:",
                "- Never put an absolute path literal in your source (no '/src/...', '/mnt/...',",
                "  '/Users/...'). A static scan refuses the file and names the exact line. Build",
                "  every path from os.environ['DBTL_WORKSPACE'] or DBTL_INPUT_n via os.path.join.",
                "- Create the entry-point script inside the workspace (e.g. src/run.py) and refer",
                "  to it only by that workspace-relative name — never by an absolute path.",
                "- The server re-runs your entry point with a bare interpreter that already has",
                "  numpy, scipy, pandas, matplotlib, statsmodels, scikit-learn, seaborn and jupyter.",
                "  Import what you need directly; do NOT pip install at runtime and do NOT depend on",
                "  a virtualenv you built — the verifier will not use it.",
                "- The server gives Jupyter and IPython writable state directories inside this phase",
                "  workspace. For notebook structure only, prefer `python -m json.tool file.ipynb`;",
                "  use `python -m jupyter nbconvert --execute ...` only when executed-cell evidence is required.",
                "- Run every reproduction or audit command from the workspace root (cd into",
                "  DBTL_WORKSPACE first) and verify outputs by exact relative path, never by glob —",
                "  a verification that fails on its own mechanics costs the phase exactly like a",
                "  real failure.",
                "- If this phase draws ANY figure, paste this house style block immediately after",
                "  your matplotlib imports and before the first plot. It is not optional and not",
                "  yours to adjust — it is what makes every figure in this project legible and",
                "  colourblind-safe, and it lives in the source (never a matplotlibrc) so the",
                "  server's re-run reproduces your figure byte for byte in a different workspace:",
                "```python",
                DBTL_PLOT_STYLE_BLOCK,
                "```",
                "  Then: label BOTH axes with units, title what the figure shows rather than what",
                "  it is, and save PNG only — PDF and SVG embed a timestamp, so they hash",
                "  differently on every run and fail the reproducibility check.",
                "- Record package versions with importlib.metadata.version('numpy'), etc. NEVER read",
                "  pkg.__version__: the jupyter meta-package has no __version__ and raises AttributeError,",
                "  which has sunk whole phases here. Wrap each lookup in try/except and record 'unknown'",
                "  on failure; a missing version string is never a reason to fail the phase.",
                "- Run the whole build with the single interpreter already on PATH; it has the full stack.",
                "  Never run python -m venv or pip install: a fresh venv lacks pandas and wastes the attempt.",
                "- Use ONE identical path string for a file everywhere it appears. A file's entry_point,",
                "  its artifact_refs entry, and its declared_outputs entry must be byte-for-byte the same",
                "  string. The server keys its publish remap on that exact string, so listing a file as",
                "  'outputs/fit.py' in one place and '/mnt/user-data/.../outputs/fit.py' in another makes",
                "  the server treat them as two different files and discard the whole phase. Pick the short",
                "  workspace-relative form and reuse it verbatim. Correct, consistent shape:",
                '    "artifact_refs": ["outputs/fit.py", "outputs/model.json", "outputs/preds.csv"],',
                '    "provenance": {"phase_manifest": {"version": 3, "entry_point": "outputs/fit.py",',
                '      "declared_outputs": ["outputs/fit.py", "outputs/model.json", "outputs/preds.csv"],',
                '      "completion_condition": "..."}}   # entry_point is character-identical in all three',
                "- Your final structured result MUST be exactly one JSON object with nothing printed before",
                "  or after it. Extra text or a trailing second object makes the result unparseable and",
                "  discards the entire build.",
            ]
            if "granted_paths_only" in spec.validity_gates
            else []
        ),
        "Your objective above was derived from the approved Design, so you normally do not need",
        "the Design itself. Project context names it and the manifest lists the project's files;",
        "read a named file only when you need its exact bytes, and do not read the Design merely",
        "to restate it.",
        "",
        "This phase reports; it does not grade itself. A check you ran and that failed is a",
        "recorded failed check with its detail — not a reason to hide the work.",
        *(
            [
                f"You MUST include exactly one quality check named {PHASE_DONE_CHECK!r}.",
                "Set it to passed=true only after the phase's declared Done when condition is met",
                "and every expected output exists. If either is incomplete, set it to false,",
                "and do not set it true while another implementation quality check is false.",
                "Two kinds of failed check are exceptions, recorded as a limitation rather than",
                "failing the phase: a repeat-run/reproducibility check, and an optional notebook or",
                "report that could not be executed because a runtime tool (for example jupyter) is",
                "unavailable — provided your executable entry point runs clean and every required",
                "data output exists. Test and the human reviewer own whether an un-run notebook matters.",
                "report status=failed, name the missing work in its detail, and stop. Partial files",
                "remain auditable, but they cannot advance this build plan.",
                *(
                    [
                        f"In provenance.phase_manifest return version={required_phase_manifest_version(spec)}, the executable entry_point path,",
                        "declared_outputs containing every artifact_refs path exactly once, and",
                        "completion_condition copied verbatim from Done when (or an empty string when none was recorded).",
                        *(
                            [
                                "Also return declared_inputs containing only exact workspace files actually consumed to implement or execute this phase.",
                                "Do not include files read only for orientation, discovery, or restating project context.",
                                *(
                                    [
                                        "Also return execution_inputs containing only the server-issued inputs the entry point consumes at runtime.",
                                        "execution_inputs is a subset of declared_inputs and does not renumber DBTL_INPUT_n.",
                                        "The entry_point must be an executable script ending in .py, .sh, .bash, .R, .js, .mjs, .cjs, .ts, .tsx, .jl, .rb, or .pl.",
                                        "A notebook may be a declared output, but it is a human replay playbook and must never be the entry_point.",
                                        "Create the entry-point script inside this phase workspace and include that exact path in both artifact_refs and declared_outputs.",
                                        "Do not name an earlier phase's read-only output as this phase's entry_point. For a notebook phase, create one concise validator script as this phase's executable output.",
                                    ]
                                    if "server_executed_entry_point" in spec.validity_gates
                                    else []
                                ),
                            ]
                            if "narrow_implementation_inputs" in spec.validity_gates
                            else []
                        ),
                    ]
                    if "server_verified_phase_manifest" in spec.validity_gates
                    else []
                ),
            ]
            if spec.version >= 6
            else ["Report status=failed only when the work could not be done at all."]
        ),
        "",
        BUILD_PRESENTATION_RESULT_NOTE,
        "",
        BUILD_RERUN_RESULT_NOTE,
        "",
        result_contract,
    ]
    return WorkUnit(
        unit_id=f"{attempt_id}-{index}-{phase.phase_key}-{attempt_token}",
        capability=phase.capability.value,
        agent_name=assignment.agent_name,
        prompt="\n".join(line for line in lines if line is not None),
        via_generalist=assignment.via_generalist,
        role=PHASE_ROLE,
        completion_check=PHASE_DONE_CHECK if spec.version >= 6 else "",
        skills=phase.skills,
        tool_contract={"fresh_correction": spec.version >= 12, "granted_inputs": tuple(granted_inputs)},
    )


def phase_correction_unit(
    assignment: PhaseAssignment,
    *,
    index: int,
    attempt_id: str,
    attempt_token: str,
    spec: StageSpec,
    previous_workspace: str,
    previous_result: StageWorkerResult,
    failure: str,
    result_contract: str,
    granted_inputs: Sequence[str] = (),
) -> WorkUnit:
    """A fresh, compact executor for one failed implementation check.

    It deliberately does not receive the first worker's conversation. The
    previous workspace is readable evidence; the transcript that grew while
    producing it is not useful implementation input and was the dominant cost
    of the old same-agent loop.
    """
    phase = assignment.phase
    phase_manifest = parse_phase_manifest(previous_result.provenance.get("phase_manifest"))
    manifest_snapshot = phase_manifest.as_dict() if phase_manifest is not None else None
    if manifest_snapshot is not None:
        for field_name in ("declared_outputs", "declared_inputs", "execution_inputs"):
            if field_name in manifest_snapshot:
                manifest_snapshot[field_name] = manifest_snapshot[field_name][:CORRECTION_HANDOFF_MAX_ITEMS]
    previous_result_handoff = {
        "status": previous_result.status.value,
        "summary": previous_result.summary,
        "artifact_refs": list(previous_result.artifact_refs[:CORRECTION_HANDOFF_MAX_ITEMS]),
        "failed_quality_checks": [check.as_dict() for check in previous_result.quality_checks if not check.passed][:CORRECTION_HANDOFF_MAX_ITEMS],
        "limitations": list(previous_result.limitations[:CORRECTION_HANDOFF_MAX_ITEMS]),
        "provenance": {"phase_manifest": manifest_snapshot} if manifest_snapshot is not None else {},
    }
    lines = [
        f"You are correcting phase {index} of the {spec.title} stage.",
        f"Phase: {phase.title}",
        f"Objective: {phase.objective}",
        *([f"Done when: {phase.done_condition}"] if phase.done_condition else []),
        "",
        "The server rejected the first implementation for exactly this reason:",
        failure[:2_000],
        "",
        "The server parsed this bounded result from the first worker. Use it as recovery context, not as proof that the phase passed:",
        "BEGIN SERVER-PARSED PREVIOUS RESULT",
        json.dumps(previous_result_handoff, sort_keys=True, ensure_ascii=False),
        "END SERVER-PARSED PREVIOUS RESULT",
        "",
        f"Its staged files are read-only at {previous_workspace}.",
        f"Write the corrected implementation under {STAGE_UNIT_WORKSPACE_PLACEHOLDER}; do not modify the previous workspace.",
        "Inspect only the files needed to fix the named failure. Do not repeat discovery or restate the Design.",
        "When the named failure is inside a generated source file, REWRITE that file cleanly from the phase objective — never copy the previous file and patch the named line.",
        "Copied files have repeatedly resurrected their other latent defects here: a hardcoded '/test/...' output path, stale audit globs, an unbalanced nested quote.",
        "After writing it, run a syntax check (python -m py_compile) and scan it yourself for absolute path literals before executing.",
        f"Read data paths from {INPUT_ENV_PREFIX}1, {INPUT_ENV_PREFIX}2, ... and write beneath {WORKSPACE_ENV}; never hardcode a host or mount path.",
        "A diagnosis alone is a failed correction: produce the corrected implementation, run it, and return the structured result.",
        "Hard rules the server enforces — the same ones that cost whole phases here:",
        "- No absolute path literal in generated source ('/src/...', '/mnt/...', '/Users/...'); a static scan refuses the file and names the line.",
        "- Use the one provisioned interpreter (numpy, scipy, pandas, matplotlib, statsmodels, scikit-learn, seaborn, jupyter are available); never build a venv or pip install.",
        "- Record versions with importlib.metadata.version('pkg') wrapped in try/except -> 'unknown'; never pkg.__version__.",
        "- Return exactly ONE JSON object as the structured result; no prose before or after it.",
        "- Verify from the workspace root: cd into DBTL_WORKSPACE before running any reproduction or audit command, and check outputs by exact relative path (no globs).",
        "  Corrections here have died on a reproduce script invoked from the wrong directory and on audit globs that missed the real files.",
        "Server-issued input order for this correction:",
        *(f"  {INPUT_ENV_PREFIX}{position}={path}" for position, path in enumerate(granted_inputs, start=1)),
        f"In provenance.phase_manifest return version={required_phase_manifest_version(spec)}, the executable entry_point path,",
        "declared_outputs containing every artifact_refs path exactly once, and",
        "completion_condition copied verbatim from Done when (or an empty string when none was recorded).",
        "In declared_inputs list only exact workspace files actually consumed to implement or execute the correction.",
        "In execution_inputs list only server-issued inputs the corrected entry point consumes at runtime.",
        "execution_inputs is a subset of declared_inputs and does not renumber DBTL_INPUT_n.",
        "Create the entry-point script inside this correction workspace and include that exact path in both artifact_refs and declared_outputs.",
        "A notebook is a human replay output, never the entry_point; use a concise validator script for a notebook-only correction.",
        f"Return exactly one {PHASE_DONE_CHECK!r} check, passed only after the corrected entry point runs and the Done when condition holds.",
        "Return the complete phase manifest and shared structured result.",
        "",
        BUILD_PRESENTATION_RESULT_NOTE,
        "",
        BUILD_RERUN_RESULT_NOTE,
        "",
        result_contract,
    ]
    return WorkUnit(
        unit_id=f"{attempt_id}-{index}-{phase.phase_key}-{attempt_token}-correction",
        capability=phase.capability.value,
        agent_name=assignment.agent_name,
        prompt="\n".join(lines),
        via_generalist=assignment.via_generalist,
        role=PHASE_ROLE,
        max_tokens=CORRECTION_MAX_TOKENS,
        completion_check=PHASE_DONE_CHECK,
        skills=phase.skills,
        tool_contract={"fresh_correction": True, "correction_attempt": True, "granted_inputs": tuple(granted_inputs)},
    )


def plan_notes(plan: BuildPhasePlan, assignments: Sequence[PhaseAssignment]) -> tuple[str, ...]:
    """Human-readable notes recorded beside the plan.

    A generalist stand-in is named here so it reaches the review package, where
    the alternative — silence — reads as a specialist having run.
    """
    notes: list[str] = []
    if plan.note:
        notes.append(plan.note)
    for assignment in assignments:
        if not assignment.covered:
            notes.append(f"{assignment.phase.capability.value}: no registered agent could cover it, so the phase was not run.")
        elif assignment.via_generalist:
            notes.append(f"{assignment.phase.capability.value}: covered by {assignment.agent_name} (no registered specialist).")
    return tuple(notes)
