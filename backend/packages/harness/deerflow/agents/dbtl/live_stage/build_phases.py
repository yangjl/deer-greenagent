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
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from deerflow.agents.dbtl.live_stage.workspace import SHELL_WORKSPACE_IDIOM, STAGE_UNIT_WORKSPACE_PLACEHOLDER
from deerflow.dbtl.agent_selector import AgentCandidate
from deerflow.dbtl.build_grant import INPUT_ENV_PREFIX, WORKSPACE_ENV, describe_foreign_paths, scan_foreign_paths
from deerflow.dbtl.build_plan import PLANNER_CONTRACT, BuildPhase, BuildPhasePlan
from deerflow.dbtl.capabilities import Capability
from deerflow.dbtl.stage_runner import BUILD_PLAN_OUTPUT, WorkUnit
from deerflow.dbtl.stage_spec import StageSpec
from deerflow.dbtl.worker_result import StageWorkerResult, WorkerStatus

#: The seat that draws the plan. Read-only by role, like the summarizer: it
#: writes nothing, runs nothing, and dispatches nobody.
PLANNER_ROLE = "planner"
PHASE_ROLE = "phase"
PHASE_DONE_CHECK = "phase_done_condition"

BUILD_PRESENTATION_RESULT_NOTE = """
Build result declarations (required in addition to the shared result):
- Return `key_outcomes` as a list of every verified numeric result this phase
  produced: [{"name": "metric or result", "value": 0.0, "unit": "optional"}].
- Return `figures` as a list of every figure this phase produced:
  [{"path": "/mnt/user-data/...", "caption": "what it is", "shows": "what it demonstrates"}].
- Use an empty list only when this phase genuinely produced none. A numeric
  result mentioned in summary, claims, evidence, or an output file must also
  appear in `key_outcomes`; otherwise the verified Build cannot be presented
  for human review.
""".strip()

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
    if manifest.entry_point not in published_uris:
        return None, "The Build phase entry point is not one of its governed published files."
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
    if set(manifest.declared_outputs) != set(result.artifact_refs) or len(manifest.declared_outputs) != len(result.artifact_refs):
        return None, "The Build phase manifest does not name exactly the outputs it asked the server to publish."
    if manifest.entry_point not in result.artifact_refs:
        return None, "The Build phase entry point is not one of its declared output files."
    if manifest.completion_condition != completion_condition.strip():
        return None, "The Build phase manifest changed the versioned completion condition from the recorded plan."
    if manifest.version >= 3 and not set(manifest.execution_inputs).issubset(manifest.declared_inputs):
        return None, "The Build phase manifest's execution_inputs must be a subset of declared_inputs."
    return manifest, ""


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
    return "reproduc" in normalized or "repeat run" in normalized or "rerun" in normalized


def gating_failed_phase_checks(result: StageWorkerResult, required_name: str = PHASE_DONE_CHECK) -> tuple[str, ...]:
    """Name failed implementation checks eligible for one bounded correction."""

    return tuple(item.name.strip() for item in result.quality_checks if item.name.strip() and not item.passed and (item.name.strip() == required_name or not is_non_gating_build_check(item.name)))


def phase_completion_error(result: StageWorkerResult, required_name: str) -> str:
    """Explain why a server-required Build phase assertion did not pass."""

    if not required_name or result.status is not WorkerStatus.COMPLETED:
        return ""
    checks = [item for item in result.quality_checks if item.name.strip() == required_name]
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
    *,
    implementer: str = "",
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
    # A configured implementer replaces only the *stand-in*, never a registered
    # specialist: the deployment is stating which agent implements best, not
    # overruling a declared capability. It is still recorded as a stand-in,
    # because it is one -- nothing about it covers the capability, and a
    # reviewer who cannot tell a specialist from a preference has lost the
    # distinction capability selection exists to keep. An unregistered name
    # falls through to the generalist rather than failing the phase: this is an
    # efficiency dial, and correctness does not rest on which agent runs.
    preferred = next((item for item in available if implementer and item.name == implementer), None)
    if preferred is not None:
        return PhaseAssignment(phase=phase, agent_name=preferred.name, via_generalist=True)
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
                "A failed repeat-run/reproducibility check is the sole exception: record that as",
                "a limitation because Test and the human reviewer own that verdict.",
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
    lines = [
        f"You are correcting phase {index} of the {spec.title} stage.",
        f"Phase: {phase.title}",
        f"Objective: {phase.objective}",
        *([f"Done when: {phase.done_condition}"] if phase.done_condition else []),
        "",
        "The server rejected the first implementation for exactly this reason:",
        failure[:2_000],
        "",
        f"Its staged files are read-only at {previous_workspace}.",
        f"Write the corrected implementation under {STAGE_UNIT_WORKSPACE_PLACEHOLDER}; do not modify the previous workspace.",
        "Inspect only the files needed to fix the named failure. Do not repeat discovery or restate the Design.",
        f"Read data paths from {INPUT_ENV_PREFIX}1, {INPUT_ENV_PREFIX}2, ... and write beneath {WORKSPACE_ENV}; never hardcode a host or mount path.",
        "Server-issued input order for this correction:",
        *(f"  {INPUT_ENV_PREFIX}{position}={path}" for position, path in enumerate(granted_inputs, start=1)),
        f"In provenance.phase_manifest return version={required_phase_manifest_version(spec)}, the executable entry_point path,",
        "declared_outputs containing every artifact_refs path exactly once, and",
        "completion_condition copied verbatim from Done when (or an empty string when none was recorded).",
        "In declared_inputs list only exact workspace files actually consumed to implement or execute the correction.",
        "In execution_inputs list only server-issued inputs the corrected entry point consumes at runtime.",
        "execution_inputs is a subset of declared_inputs and does not renumber DBTL_INPUT_n.",
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
        max_tokens=40_000,
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
