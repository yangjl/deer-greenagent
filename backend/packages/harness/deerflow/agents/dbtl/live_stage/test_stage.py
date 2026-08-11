"""Own the governed Test rerun, assessment, and review surface.

Test's job is to re-run an approved Build's contract and say what happened. When
the evidence itself is unusable — quarantined, unreadable, or contradicted — Test
still owes a durable, reviewable account of *why*, and that account is what this
module writes.

The rule the plan states and this module keeps: Test may **report** invalid
evidence; it may not repair it. Nothing here rewrites a rerun spec, guesses a
path, or injects an ambient input. It writes a package, reads one back, and
renders the deck a human answers from.

``TestStageCoordinator`` receives validated Build lineage from the adapter,
runs the independent contract, computes the typed assessment, records the
evidence, and renders its review surface. ``TestReviewService`` remains the
separate owner of the human outcome and route write.

The evidence-*reuse* helpers live here too. A Test attempt that already ran
should not pay to run again when its worker results are still exactly what they
were, so these decide what may be carried forward: which deliverables Test owns
rather than inherits, and whether recorded results still belong to this attempt.
Reuse is refused on any doubt — a stale result presented as fresh evidence is
the one failure this whole stage exists to prevent.

They arrived late. Re-checking recorded bytes meant reaching hashing helpers the
adapter had aliased privately, and a test patched that alias to simulate an
unreadable artifact; moving them then would have left the patch rebinding a name
nobody calls. Once those helpers got a single real owner in ``workspace``, one
patch there intercepted every caller and the hazard went with it.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from deerflow.agents.dbtl.live_stage import workspace
from deerflow.agents.dbtl.live_stage.design_input import resolve_build_inputs
from deerflow.agents.dbtl.live_stage.feedback_surfaces import RenderedDeck, _persist_deck
from deerflow.agents.dbtl.live_stage.test_rerun import PreparedTestRerun, TestRerunRecord, build_test_rerun_unit, parse_test_rerun_record, prepare_test_rerun, rerun_result, validate_test_rerun
from deerflow.agents.dbtl.live_stage.test_review import validated_test_assessment as _validated_test_assessment
from deerflow.agents.dbtl.live_stage.types import LiveStageResult
from deerflow.agents.dbtl.live_stage.workspace import SHELL_WORKSPACE_IDIOM, STAGE_UNIT_WORKSPACE_PLACEHOLDER, WORKSPACE_VIRTUAL_ROOT, atomic_write, verified_workspace_files
from deerflow.dbtl.build_input import BuildInputBundle, BuildInputError
from deerflow.dbtl.council_deck import chair_result as _chair_result_of
from deerflow.dbtl.council_deck import (
    extract_commentable_slides,
    render_design_deck_shell,
    render_design_deck_slide,
    render_stage_feedback_bridge,
    render_stage_review_controls,
)
from deerflow.dbtl.cycle_state import StageStatus
from deerflow.dbtl.evidence_exception import EvidenceExceptionDossier, EvidenceReason, build_evidence_exception_dossier, invalidated_test_exception_facts
from deerflow.dbtl.review_paths import stage_file_name, stage_output_dir
from deerflow.dbtl.stage_meetings import REVIEW_MEETING_STAGES, MeetingRequirement, surface_meeting_gate
from deerflow.dbtl.stage_runner import DispatchOutcome, StageExecutionOutcome, WorkUnit, arun_stage, plan_stage
from deerflow.dbtl.stage_spec import WorkerBudget
from deerflow.dbtl.validity import DEFAULT_VALIDITY_PACK, ValidityCheckName
from deerflow.dbtl.worker_result import StageWorkerResult, WorkerResultRejected, WorkerStatus, parse_worker_result
from deerflow.projects.storage import ensure_project_dirs, project_outputs_dir
from deerflow.runtime.activity.vocabulary import ActivityState

logger = logging.getLogger(__name__)


def _write_evidence_exception_package(
    *,
    project_root: str,
    cycle: Mapping[str, Any],
    dossier: EvidenceExceptionDossier,
) -> tuple[str, str, str]:
    """Persist the canonical dossier bytes whose SHA-256 is its contract hash."""
    payload = dossier.as_dict()
    content_hash = str(payload.pop("content_hash"))
    document = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    if hashlib.sha256(document).hexdigest() != content_hash:
        raise ValueError("Evidence exception serialization does not match its canonical hash.")
    root = Path(project_root).expanduser().resolve()
    ensure_project_dirs(root)
    relative = stage_output_dir(
        cycle_id=str(cycle["id"]),
        cycle_title=str(cycle.get("title") or ""),
        stage=dossier.stage,
    ) / stage_file_name(
        stage=dossier.stage,
        kind="evidence-exception",
        revision=cycle.get("db_revision"),
        content_hash=content_hash,
    )
    atomic_write(project_outputs_dir(root) / relative, document)
    reasons = ", ".join(reason.value.replace("_", " ") for reason in dossier.reason_codes)
    return (
        f"/mnt/user-data/outputs/{relative.as_posix()}",
        content_hash,
        f"{dossier.condition.value.replace('_', ' ').title()} evidence exception: {reasons}.",
    )


def _read_evidence_exception_package(
    *,
    project_root: str,
    artifact: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Read only a content-addressed dossier from the governed output tree."""
    uri = str(artifact.get("uri") or "")
    expected_hash = str(artifact.get("content_hash") or "")
    prefix = "/mnt/user-data/outputs/"
    if not uri.startswith(prefix) or len(expected_hash) != 64:
        return None
    root = project_outputs_dir(Path(project_root).expanduser().resolve())
    candidate = (root / uri.removeprefix(prefix)).resolve()
    try:
        candidate.relative_to(root.resolve())
        document = candidate.read_bytes()
        if hashlib.sha256(document).hexdigest() != expected_hash:
            return None
        payload = json.loads(document)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["content_hash"] = expected_hash
    return payload


def _write_evidence_exception_deck(
    *,
    project_root: str,
    cycle: Mapping[str, Any],
    dossier: EvidenceExceptionDossier,
    package_path: str,
    surface_id: str,
    transition_gate: Mapping[str, Any],
) -> RenderedDeck | None:
    """Render metadata only; quarantined artifact bytes are never embedded."""

    def items(values: Sequence[object], empty: str) -> str:
        rendered = "".join(f"<li>{html.escape(str(value))}</li>" for value in values)
        return f"<ul>{rendered}</ul>" if rendered else f"<p>{html.escape(empty)}</p>"

    payload = dossier.as_dict()
    slides = [
        render_design_deck_slide(
            kind="title",
            eyebrow="Evidence exception",
            title=f"{dossier.stage.title()} cannot take the clean review path",
            body=(
                '<p class="lede"><strong>Red flag:</strong> this evidence remains failed or untrusted. '
                "Continuing is not approval and cannot turn it into scientific support.</p>"
                f'<p class="stamp">Dossier SHA-256: {html.escape(dossier.content_hash)}</p>'
            ),
            note_id="exception-summary",
            note_label="Exception summary",
        ),
        render_design_deck_slide(
            kind="evidence",
            eyebrow="Server classification",
            title=dossier.condition.value.replace("_", " ").title(),
            body=(
                f"<p><strong>Scientific effect:</strong> {html.escape(dossier.scientific_effect.value.replace('_', ' '))}</p>"
                f"<p><strong>Reason codes:</strong> {html.escape(', '.join(reason.value for reason in dossier.reason_codes))}</p>"
                f'<p><a href="{html.escape(package_path)}">Open the immutable dossier</a></p>'
            ),
            note_id="classification",
            note_label="Classification",
        ),
        render_design_deck_slide(
            kind="evidence",
            eyebrow="Trusted record",
            title="What the server verified",
            body=items(dossier.verified_facts, "No positive execution fact was independently verified."),
            note_id="verified-facts",
            note_label="Verified facts",
        ),
        render_design_deck_slide(
            kind="contested",
            eyebrow="Quarantine",
            title="What must not be relied on",
            body=(
                f"<p>{len(dossier.untrusted_claims)} worker/client claim(s) remain quarantined in the immutable dossier; their content is not rendered here.</p>"
                + f"<p><strong>Affected deliverables:</strong> {html.escape(json.dumps(payload['affected_deliverables'], ensure_ascii=False, default=str))}</p>"
                + f"<p><strong>Failed checks:</strong> {html.escape(json.dumps(payload['failed_checks'], ensure_ascii=False, default=str))}</p>"
            ),
            note_id="quarantine",
            note_label="Quarantined evidence",
        ),
        render_design_deck_slide(
            kind="review",
            eyebrow="Human gate",
            title=f"Review the {dossier.stage.title()} exception",
            body=render_stage_review_controls(dossier.stage, transition_gate),
            note_id="human-gate",
            note_label="Human decision",
        ),
    ]
    try:
        deck_html = render_design_deck_shell(
            title=html.escape(f"{cycle.get('title') or 'DBTL'} — evidence exception"),
            slides=slides,
            bridge=render_stage_feedback_bridge(surface_id, dossier.stage),
        )
    except Exception:  # noqa: BLE001 - the immutable dossier already exists
        logger.warning("Could not render the evidence exception deck.", exc_info=True)
        return None
    return _persist_deck(
        project_root=project_root,
        cycle=cycle,
        stage=dossier.stage,
        document=deck_html.encode("utf-8"),
        commentable_slides=extract_commentable_slides(deck_html),
    )


def _test_owned_deliverable_candidates(
    *,
    project_root: str,
    stage_workspace: str,
    outcome: StageExecutionOutcome,
    manifest: Any,
) -> list[dict[str, Any]]:
    """Verify Test-created manifest products before the audit can cite them."""
    candidates: list[dict[str, Any]] = []
    claimed_paths: set[str] = set()
    specs = {item.id: item for item in manifest.deliverables}
    outputs_root = project_outputs_dir(Path(project_root).expanduser().resolve())
    for unit, result in zip(outcome.plan.units, outcome.results, strict=True):
        if not result.is_trustworthy:
            continue
        raw = result.provenance.get("deliverable_audit")
        items = raw.get("items") if isinstance(raw, Mapping) else None
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            continue
        unit_workspace = workspace.unit_stage_workspace(stage_workspace, unit.unit_id)
        for item in items:
            if not isinstance(item, Mapping):
                continue
            spec = specs.get(str(item.get("deliverable_id") or item.get("id") or ""))
            if spec is None:
                continue
            expected = [path for path in spec.expected_paths if "/test/" in f"/{path}"]
            if len(expected) != 1 or expected[0] in claimed_paths:
                continue
            artifacts = item.get("observed_artifacts")
            if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)):
                continue
            for artifact in artifacts:
                if not isinstance(artifact, Mapping):
                    continue
                source_uri = str(artifact.get("path") or "")
                content_hash = str(artifact.get("content_hash") or artifact.get("sha256") or "").lower()
                if PurePosixPath(source_uri).name != PurePosixPath(expected[0]).name or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
                    continue
                try:
                    verified = verified_workspace_files(
                        source_uri,
                        project_root=project_root,
                        containment_reference=unit_workspace,
                        relative_to_containment=True,
                        max_files=1,
                    )
                except (FileNotFoundError, OSError, ValueError):
                    continue
                if len(verified) != 1 or workspace.sha256_file(verified[0][1]) != content_hash:
                    continue
                relative = PurePosixPath(expected[0])
                if not relative.parts or relative.parts[0] != "outputs":
                    continue
                destination = outputs_root.joinpath(*relative.parts[1:])
                candidates.append(
                    {
                        "source_uri": source_uri,
                        "source_path": expected[0],
                        "uri": f"{WORKSPACE_VIRTUAL_ROOT}/{relative.as_posix()}",
                        "content_hash": content_hash,
                        "unit_id": unit.unit_id,
                        "_source": verified[0][1],
                        "_destination": destination,
                    }
                )
                claimed_paths.add(expected[0])
                break
    return candidates


def _publish_test_owned_deliverables(candidates: Sequence[Mapping[str, Any]]) -> None:
    """Publish only candidates already accepted by the complete typed audit."""
    for item in candidates:
        source = item.get("_source")
        destination = item.get("_destination")
        if not isinstance(source, Path) or not isinstance(destination, Path):
            raise ValueError("A verified Test deliverable lost its publication path.")
        workspace.atomic_copy(source, destination, expected_hash=str(item.get("content_hash") or ""))


def _reusable_test_worker_results(
    stored: Sequence[Mapping[str, object]],
    *,
    stage_attempt_id: str,
    units: Sequence[WorkUnit],
) -> tuple[tuple[WorkUnit, ...], tuple[StageWorkerResult, ...], TestRerunRecord] | None:
    """Recover a complete current-attempt Test result without spending workers again."""

    current = [item for item in stored if str(item.get("stage_attempt_id") or "") == stage_attempt_id]
    rerun_row = next(
        (item for item in reversed(current) if str(item.get("unit_id") or "").endswith("-build-rerun")),
        None,
    )
    if rerun_row is None:
        return None
    prefix = str(rerun_row.get("unit_id") or "").removesuffix("-build-rerun")
    reused_units: list[WorkUnit] = []
    parsed: list[StageWorkerResult] = []
    rerun: TestRerunRecord | None = None
    for unit in units:
        item = (
            rerun_row
            if unit.unit_id.endswith("-build-rerun")
            else next(
                (row for row in reversed(current) if str(row.get("unit_id") or "").startswith(f"{prefix}-") and str(row.get("capability") or "") == unit.capability),
                None,
            )
        )
        raw = item.get("result") if isinstance(item, Mapping) else None
        if not isinstance(raw, Mapping):
            return None
        try:
            result = parse_worker_result(
                raw,
                capability=str(raw.get("capability") or item.get("capability") or unit.capability),
                agent_name=str(raw.get("agent_name") or item.get("agent_name") or unit.agent_name),
                stop_reason=(str(raw.get("stop_reason")) if raw.get("stop_reason") else None),
            )
        except WorkerResultRejected:
            return None
        if not result.is_trustworthy:
            return None
        if unit.unit_id.endswith("-build-rerun"):
            rerun = parse_test_rerun_record(result.provenance.get("rerun_execution"))
            if rerun is None:
                return None
        reused_units.append(replace(unit, unit_id=str(item.get("unit_id") or unit.unit_id)))
        parsed.append(result)
    return (tuple(reused_units), tuple(parsed), rerun) if rerun is not None and len(parsed) == len(units) else None


def _has_reusable_test_evidence(
    stored: Sequence[Mapping[str, object]],
    *,
    stage_attempt_id: str,
) -> bool:
    """Whether the current Test attempt has both trustworthy evidence roles."""

    parsed: list[tuple[str, StageWorkerResult]] = []
    for item in stored:
        unit_id = str(item.get("unit_id") or "")
        if str(item.get("stage_attempt_id") or "") != stage_attempt_id or not unit_id:
            continue
        raw = item.get("result")
        if not isinstance(raw, Mapping):
            continue
        try:
            result = parse_worker_result(
                raw,
                capability=str(raw.get("capability") or item.get("capability") or "recorded-test"),
                agent_name=str(raw.get("agent_name") or item.get("agent_name") or "recorded-worker"),
                stop_reason=(str(raw.get("stop_reason")) if raw.get("stop_reason") else None),
            )
        except WorkerResultRejected:
            continue
        if not result.is_trustworthy:
            continue
        parsed.append((unit_id, result))
    for unit_id, result in reversed(parsed):
        if not unit_id.endswith("-build-rerun") or parse_test_rerun_record(result.provenance.get("rerun_execution")) is None:
            continue
        prefix = unit_id.removesuffix("-build-rerun")
        if any(other_id.startswith(f"{prefix}-") and isinstance(other.provenance.get("validity_assessment"), Mapping) for other_id, other in parsed):
            return True
    return False


class TestStageCoordinator:
    def __init__(self, owner):
        self._owner = owner

    def __getattr__(self, name):
        return getattr(self._owner, name)

    async def execute(self, *, stage_activity, project_id, cycle_id, cycle, attempt, request_text, state, config, runtime, project_root, run_id, user_id, execution_key, spec, build_control, reuse_recorded_test_evidence) -> LiveStageResult:
        from deerflow.agents.dbtl.live_stage import adapter as _legacy

        _approved_design_brief = _legacy._approved_design_brief
        _bind_stage_unit_workspaces = _legacy._bind_stage_unit_workspaces
        _change_request = _legacy._change_request
        _compact_design_history = _legacy._compact_design_history
        _design_round = _legacy._design_round
        _failure_reasons = _legacy._failure_reasons
        _prepare_stage_workspace = _legacy._prepare_stage_workspace
        _project_manifest = _legacy._project_manifest
        _safe_token = _legacy._safe_token
        _validated_deliverable_audit = _legacy._validated_deliverable_audit
        _write_council_deck = _legacy._write_council_deck
        _write_stage_package = _legacy._write_stage_package
        stage = "test"
        dbtl_config = getattr(self._app_config, "dbtl", None)
        degraded_evidence_enabled = bool(getattr(dbtl_config, "degraded_evidence_continuation", False))
        datasets = await self._repo.list_datasets(cycle_id, project_id=project_id)
        await self._repo.reconciliation_view(cycle_id, project_id=project_id)
        build_test = await self._repo.build_test_view(cycle_id, project_id=project_id)
        upstream_evidence_exception: dict[str, Any] | None = None
        if degraded_evidence_enabled:
            build_attempt = next((item for item in cycle.get("stages", []) if item.get("stage") == "build" and item.get("status") == StageStatus.ADVANCED_WITH_EXCEPTION.value), None)
            if build_attempt is not None:
                artifact = max(
                    (item for item in cycle.get("artifacts", []) if item.get("stage_attempt_id") == build_attempt.get("id") and item.get("artifact_type") == "evidence_exception"),
                    key=lambda item: int(item.get("revision") or 0),
                    default=None,
                )
                if artifact is not None:
                    upstream_evidence_exception = await asyncio.to_thread(_read_evidence_exception_package, project_root=project_root, artifact=artifact)
        activity: list[dict[str, Any]] = []
        change_request = _change_request(activity)
        design_round = _design_round(activity)
        attempt_id = f"dbtl-{_safe_token(execution_key)}"
        stage_workspace = None
        (stage_workspace, _) = await asyncio.to_thread(_prepare_stage_workspace, project_root, attempt_id=attempt_id, stage=stage)
        project_manifest = await asyncio.to_thread(_project_manifest, project_root)
        build_inputs: BuildInputBundle | None = None
        try:
            build_inputs = await asyncio.to_thread(resolve_build_inputs, cycle, project_root=project_root, datasets=datasets, manifest=project_manifest, policy={"stage_spec_key": spec.spec_key})
        except BuildInputError as refusal:
            logger.info("Test has no resolvable Design deliverables manifest: %s", refusal.summary)
        stage_context_payload = {
            "request": request_text,
            "cycle": {key: cycle.get(key) for key in ("id", "title", "cycle_class", "state", "research_question", "objective", "success_criteria")},
            "declared_datasets": datasets,
            "reconciliation": {"status": "not_required", "instruction": "Data Reconciliation is not a Build prerequisite. Missing dataset declarations or reconciliation matrix rows are not a blocker, limitation, or failed validity check."},
            "build_test": build_test,
            "input_provenance_policy": {
                "authority": "server_bound_build_lineage",
                "instruction": "Build binds the exact files it reads with server-computed content hashes, and Test verifies that durable Build lineage. For compatibility, a validity check named reconciled_inputs means bound input provenance; judge the Build lineage, not the existence of reconciliation rows. An older Build package may describe absent reconciliation as a limitation; that is historical worker commentary, not active policy.",  # noqa: E501
            },
            "test_validity_contract": {
                "pack_key": DEFAULT_VALIDITY_PACK.pack_key,
                "required_checks": [check.value for check in DEFAULT_VALIDITY_PACK.required_checks],
                "metric_schema": {
                    "required_fields": ["name", "value", "threshold", "criterion", "plausible_max", "unit"],
                    "criterion_values": ["gte", "lte"],
                    "instruction": "Use only `gte` or `lte` for every metric criterion. For an exact target, use `gte` with value and threshold equal; the named validity checks carry exactness and direction semantics. Never emit `equal_to`, `greater_than`, or prose synonyms.",  # noqa: E501
                },
                "authoritative_rules": [
                    "Only required_checks may determine the overall Test outcome. Do not invent or require an additional gate.",
                    "A missed numeric headline threshold belongs only in metrics; it is not a failed direction check. Fail direction only when evidence contradicts a separately declared expected sign or qualitative direction.",
                    "The server-bound Build lineage satisfies reconciled_inputs when present. Do not require a declaration, matrix, or reconciliation artifact.",
                    "duplicates_relatedness is not in this validity pack. Missing pedigree, genotype, kinship, or relatedness columns may be noted as a limitation, but cannot fail, block, or make this Test inconclusive."
                    if ValidityCheckName.DUPLICATES_RELATEDNESS not in DEFAULT_VALIDITY_PACK.required_checks
                    else "Evaluate duplicates_relatedness as a required check.",
                    "The approved Design and this server-owned contract outrank commentary in an older Build package.",
                ],
            },
            "workspace_root": WORKSPACE_VIRTUAL_ROOT,
            "stage_workspace": {
                "path": STAGE_UNIT_WORKSPACE_PLACEHOLDER,
                "instruction": "Write every new implementation, derived output, and execution log under this exact directory. Do not write under outputs/dbtl; the stage adapter publishes validated review evidence there after your result passes its contract.",  # noqa: E501
                "shell_note": SHELL_WORKSPACE_IDIOM,
            }
            if stage_workspace
            else None,
            "project_workspace_manifest": project_manifest,
            "build_input_policy": None,
            "prior_design_council_runs": _compact_design_history([]),
            "discovery_package": None,
            "approved_design_brief": _approved_design_brief(cycle),
            "build_input_bundle": build_inputs.as_dict() if build_inputs is not None else None,
            "human_change_request": change_request,
            "chair_question_answered": None,
            "human_answer": "" or None,
            "design_round": design_round,
        }
        stage_context = json.dumps(stage_context_payload, sort_keys=True, ensure_ascii=False)
        base_dispatcher = self._dispatcher or self._production_dispatcher(config=config, state=state, project_id=project_id, project_root=project_root, cycle_id=cycle_id, stage=stage, meeting=False, stage_workspace=stage_workspace)

        async def dispatcher(units: Sequence[WorkUnit], *, budget: WorkerBudget) -> Sequence[DispatchOutcome]:
            return await base_dispatcher(_bind_stage_unit_workspaces(units, stage_workspace), budget=budget)  # noqa: F821

        test_rerun_record: TestRerunRecord | None = None
        test_rerun_pair: tuple[WorkUnit, StageWorkerResult] | None = None
        if isinstance(upstream_evidence_exception, Mapping) and upstream_evidence_exception.get("condition") == "untrusted":
            preliminary = plan_stage(spec, self._candidates(), attempt_id=attempt_id, context=stage_context)
            outcome = StageExecutionOutcome(plan=preliminary)
        elif "server_verified_build_rerun" in spec.validity_gates:
            preliminary = plan_stage(spec, self._candidates(), attempt_id=attempt_id, context=stage_context)
            if not preliminary.dispatchable:
                logger.info("dbtl stage %s not dispatchable: %s", spec.spec_key, "; ".join(preliminary.selection.notes) or "no work units")
                outcome = StageExecutionOutcome(plan=preliminary)
            else:
                lineage = dict((build_test or {}).get("build_lineage") or {})
                prepared = await asyncio.to_thread(prepare_test_rerun, lineage, project_root=project_root)
                selected = preliminary.units[0]
                reused_test_outcome = False
                if isinstance(prepared, PreparedTestRerun):
                    rerun_unit = build_test_rerun_unit(prepared, attempt_id=attempt_id, agent_name=selected.agent_name, via_generalist=selected.via_generalist)
                    reused = None
                    if reuse_recorded_test_evidence:
                        stored_test_runs = await self._repo.list_worker_runs(cycle_id, project_id=project_id, stage="test")
                        reused = _reusable_test_worker_results(stored_test_runs, stage_attempt_id=str((attempt or {}).get("id") or ""), units=(rerun_unit, *preliminary.units))
                    if reused is not None:
                        (reused_units, reused_results, test_rerun_record) = reused
                        outcome = StageExecutionOutcome(plan=replace(preliminary, units=reused_units), results=reused_results)
                        reused_test_outcome = True
                        logger.info("Reused %d recorded Test worker results for %s after a server-side evidence refusal.", len(reused_results), cycle_id)
                    else:
                        rerun_budget = replace(
                            spec.budget, max_workers=1, max_turns=min(spec.budget.max_turns, 80), max_tokens=min(spec.budget.max_tokens, 120000), timeout_seconds=min(spec.budget.timeout_seconds, 600), token_limit_enforced=True
                        )
                        dispatched_rerun = await dispatcher((rerun_unit,), budget=rerun_budget)
                        dispatched_receipt = dispatched_rerun[0] if dispatched_rerun else DispatchOutcome(unit_id=rerun_unit.unit_id, text=None, error="The rerun worker returned no dispatch outcome.")
                        worker_rerun = StageWorkerResult(
                            status=WorkerStatus.FAILED if dispatched_receipt.error else WorkerStatus.COMPLETED,
                            summary=dispatched_receipt.error or "The command attempt returned; server verification owns its verdict.",
                            capability=rerun_unit.capability,
                            agent_name=rerun_unit.agent_name,
                            stop_reason=dispatched_receipt.stop_reason,
                            token_usage=dict(dispatched_receipt.token_usage or {}),
                        )
                        test_rerun_record = await asyncio.to_thread(validate_test_rerun, worker_rerun, prepared, project_root=project_root, unit_workspace=workspace.unit_stage_workspace(stage_workspace, rerun_unit.unit_id))
                        test_rerun_pair = (rerun_unit, rerun_result(test_rerun_record, agent_name=worker_rerun.agent_name, token_usage=dict(worker_rerun.token_usage)))
                        rerun_rejected = ()
                else:
                    test_rerun_record = prepared
                    rerun_unit = WorkUnit(
                        unit_id=f"{attempt_id}-build-rerun",
                        capability="reproducibility_rerun",
                        agent_name="server",
                        prompt="The server refused the Build rerun before dispatch.",
                        role="rerun",
                        focus="records why the Build rerun could not start",
                    )
                    test_rerun_pair = (rerun_unit, rerun_result(test_rerun_record, agent_name="server"))
                    rerun_rejected = ()
                if not reused_test_outcome:
                    test_context = "\n".join([stage_context, "", "Server-owned Build rerun result (this overrides any worker-authored reproducibility check):", json.dumps(test_rerun_record.as_dict(), sort_keys=True, ensure_ascii=False)])
                    outcome = await arun_stage(spec, self._candidates(), dispatcher, attempt_id=attempt_id, context=test_context)
                    if test_rerun_pair is not None:
                        outcome = replace(outcome, plan=replace(outcome.plan, units=(test_rerun_pair[0], *outcome.plan.units)), results=(test_rerun_pair[1], *outcome.results), rejected=(*rerun_rejected, *outcome.rejected))
        else:
            outcome = await arun_stage(spec, self._candidates(), dispatcher, attempt_id=attempt_id, context=stage_context)
        unit_result_pairs = list(zip(outcome.plan.units, outcome.results, strict=True))
        stage_refusal = ""
        results = [
            {
                **result.as_dict(),
                "unit_id": unit.unit_id,
                "via_generalist": unit.via_generalist,
                "execution": {"model": unit.model, "max_tokens": unit.max_tokens, "token_limit_enforced": spec.budget.token_limit_enforced, "reasoning": unit.reasoning},
                "counts_toward_stage_output": True,
            }
            for (unit, result) in unit_result_pairs
        ]
        artifact_uri = None
        artifact_hash = None
        artifact_type = None
        artifact_digest = ""
        _deliverable_audit = None
        deliverable_audit_refusal = ""
        test_owned_artifacts: list[dict[str, Any]] = []
        if build_inputs is not None and build_inputs.deliverable_manifest is not None:
            if stage_workspace:
                test_owned_artifacts = await asyncio.to_thread(_test_owned_deliverable_candidates, project_root=project_root, stage_workspace=stage_workspace, outcome=outcome, manifest=build_inputs.deliverable_manifest)
            (_deliverable_audit, deliverable_audit_refusal) = _validated_deliverable_audit(outcome.trustworthy_results, manifest=build_inputs.deliverable_manifest, build_test=build_test, stage_artifacts=test_owned_artifacts)
            if deliverable_audit_refusal:
                stage_refusal = deliverable_audit_refusal
            elif test_owned_artifacts:
                await asyncio.to_thread(_publish_test_owned_deliverables, test_owned_artifacts)
        test_assessment = _validated_test_assessment(outcome.trustworthy_results, build_test=build_test, rerun=test_rerun_record)
        if outcome.produced_usable_evidence and test_assessment is None:
            stage_refusal = (
                "The Test workers returned evidence, but no complete server-readable validity assessment was present. Every headline metric must use criterion `gte` or `lte`, and the check set must exactly match the pinned validity pack."
            )
        produced_usable_evidence = outcome.produced_usable_evidence and (test_assessment is not None) and (not deliverable_audit_refusal)
        if produced_usable_evidence:
            (artifact_uri, artifact_hash, artifact_digest) = await asyncio.to_thread(
                _write_stage_package, project_root=project_root, cycle=cycle, outcome=outcome, idempotency_key=execution_key, council=None, deliverable_audit=_deliverable_audit
            )
            artifact_type = spec.required_artifact_types[0]
        evidence_exception: EvidenceExceptionDossier | None = None
        evidence_exception_uri = ""
        evidence_exception_hash = ""
        (test_exception_reasons, test_exception_checks) = invalidated_test_exception_facts(test_assessment)
        if degraded_evidence_enabled and (not produced_usable_evidence or bool(test_exception_reasons) or isinstance(upstream_evidence_exception, Mapping)):
            reasons: list[EvidenceReason] = []
            failed_checks: list[dict[str, object]] = []
            affected_deliverables: list[dict[str, object]] = []
            verified_facts: list[str] = []
            available_artifacts: list[dict[str, str]] = []
            if isinstance(upstream_evidence_exception, Mapping):
                for value in upstream_evidence_exception.get("reason_codes", []):
                    try:
                        reasons.append(EvidenceReason(value))
                    except ValueError:
                        continue
                upstream_hash = str(upstream_evidence_exception.get("content_hash") or "")
                if upstream_hash:
                    verified_facts.append(f"The Test decision is bound to upstream Build exception {upstream_hash}.")
                    available_artifacts.append({"path": "upstream_build_evidence_exception", "content_hash": upstream_hash})
            reasons.extend(test_exception_reasons)
            failed_checks.extend(test_exception_checks)
            if artifact_uri and artifact_hash:
                available_artifacts.append({"path": artifact_uri, "content_hash": artifact_hash})
            if deliverable_audit_refusal:
                reasons.append(EvidenceReason.AUDIT_INCOMPLETE)
                failed_checks.append({"check": "deliverable_audit", "status": "failed", "detail": deliverable_audit_refusal})
            if test_assessment is None:
                reasons.append(EvidenceReason.AUDIT_INCOMPLETE)
                failed_checks.append({"check": "validity_pack", "status": "missing", "detail": stage_refusal})
            if outcome.produced_usable_evidence:
                verified_facts.append("At least one worker returned server-readable evidence.")
            if not reasons:
                reasons.append(EvidenceReason.EXECUTION_ABSENT)
                failed_checks.append({"check": "stage_execution", "status": "missing", "detail": stage_refusal or "No trustworthy stage evidence was recorded."})
            evidence_exception = build_evidence_exception_dossier(
                stage=stage,
                stage_attempt_id=str((attempt or {}).get("id") or ""),
                reason_codes=reasons,
                verified_facts=verified_facts,
                untrusted_claims=[str(item.get("summary") or "") for item in results if str(item.get("summary") or "") and str(item.get("status") or "") not in {"completed", "needs_input"}],
                affected_deliverables=affected_deliverables,
                failed_checks=failed_checks,
                available_artifacts=available_artifacts,
                continuation_route="advance_to_learn" if isinstance(upstream_evidence_exception, Mapping) and str(dict((test_assessment or {}).get("evaluation") or {}).get("outcome") or "") in {"supported", "not_supported"} else None,
            )
            if evidence_exception is not None:
                (evidence_exception_uri, evidence_exception_hash, exception_digest) = await asyncio.to_thread(_write_evidence_exception_package, project_root=project_root, cycle=cycle, dossier=evidence_exception)
                if not produced_usable_evidence:
                    artifact_uri = evidence_exception_uri
                    artifact_hash = evidence_exception_hash
                    artifact_digest = exception_digest
                    artifact_type = "evidence_exception"
        if stage_activity is not None:
            await stage_activity.update(state=ActivityState.RECORDING, operation="stage.record")
        await self._repo.record_worker_runs(
            cycle_id=cycle_id,
            project_id=project_id,
            stage=stage,
            stage_spec_key=spec.spec_key,
            results=results,
            actor_user_id=str(user_id),
            expected_db_revision=int(cycle["db_revision"]),
            idempotency_key=execution_key,
            artifact_type=artifact_type,
            artifact_uri=artifact_uri,
            artifact_content_hash=artifact_hash,
        )
        if evidence_exception is not None and produced_usable_evidence and evidence_exception_uri and evidence_exception_hash:
            current = await self._repo.get_cycle(cycle_id, project_id=project_id)
            if current is None:
                raise RuntimeError("Cycle disappeared before the evidence exception could be attached.")
            await self._repo.attach_artifact(
                cycle_id=cycle_id,
                project_id=project_id,
                stage=stage,
                artifact_type="evidence_exception",
                uri=evidence_exception_uri,
                content_hash=evidence_exception_hash,
                created_by=str(user_id),
                expected_db_revision=int(current["db_revision"]),
                idempotency_key=f"{execution_key}:evidence-exception",
            )
        deck_uri = None
        deck = None
        surface_plan = None
        registration_error: Exception | None = None
        stage_has_reviewable_evidence = stage in REVIEW_MEETING_STAGES and (produced_usable_evidence or evidence_exception is not None) and bool(artifact_uri and artifact_hash)
        review_meeting_requirement = None
        if stage_has_reviewable_evidence:
            transition_gate = None
            if evidence_exception is not None:
                exception_payload = evidence_exception.as_dict()
                if str(dict((test_assessment or {}).get("evaluation") or {}).get("outcome") or "") == "invalidated":
                    exception_payload = {**exception_payload, "scientific_effect": "invalidates_support"}
                transition_gate = {"stage": stage, "assessment": {"difficulty": "exception", "rationale": "The server could not establish the clean evidence contract."}, "routes": [], "evidence_exception": exception_payload}
                review_meeting_requirement = MeetingRequirement.SKIPPED.value
            elif bool(getattr(getattr(self._app_config, "dbtl", None), "progressive_gate", False)):
                assessment = await self._assess_transition(stage=stage, cycle=cycle, evidence_summary=artifact_digest or f"{stage.title()} evidence: {artifact_uri} ({artifact_hash})")
                transition_gate = {"stage": stage, "assessment": assessment.as_dict(), "routes": []}
                meetings = getattr(getattr(self._app_config, "dbtl", None), "stage_meetings", None)
                enabled = bool(getattr(meetings, stage, False))
                gate = surface_meeting_gate(stage=stage, assessed_difficulty=assessment.difficulty.value, enabled=enabled)
                review_meeting_requirement = gate.requirement.value if gate is not None else MeetingRequirement.SKIPPED.value
            if isinstance(test_assessment, Mapping):
                evaluation = dict(test_assessment.get("evaluation") or {})
                labels = {
                    "advance_to_learn": "Accept outcome and advance to Learn",
                    "learn_from_invalidated_evidence": "Learn from invalid evidence",
                    "repeat_test": "Repeat Test",
                    "return_to_build": "Return to Build",
                    "return_to_design": "Return to Design",
                    "close_cycle": "Close this cycle",
                }
                test_routes = [{"slug": route, "label": labels[route], "blocked": False} for route in labels if route in {str(item) for item in evaluation.get("allowed_recommendations", [])}]
                transition_gate = {**(transition_gate or {"stage": "test", "assessment": {"difficulty": "standard", "rationale": "The server computed the Test outcome from the pinned validity pack."}}), "routes": test_routes}
            surface_plan = await self._plan_feedback_surface(
                stage=stage,
                cycle_id=cycle_id,
                project_id=project_id,
                execution_key=execution_key,
                round_number=design_round,
                originating_thread_id=str(runtime.get("thread_id") or ""),
                paused=False,
                artifact_uri=artifact_uri or "",
                artifact_hash=artifact_hash or "",
                decision_request=None,
                chair_worker_run_id=None,
                review_issue_ids=(),
                transition_gate=transition_gate,
            )
            if evidence_exception is not None and (not produced_usable_evidence):
                deck = await asyncio.to_thread(
                    _write_evidence_exception_deck,
                    project_root=project_root,
                    cycle=cycle,
                    dossier=evidence_exception,
                    package_path=artifact_uri or "",
                    surface_id=surface_plan.surface_id if surface_plan is not None and surface_plan.answerable else "",
                    transition_gate=transition_gate or {},
                )
            else:
                deck = await asyncio.to_thread(
                    _write_council_deck,
                    project_root=project_root,
                    cycle=cycle,
                    results=results,
                    round_number=design_round,
                    stage=stage,
                    package_path=artifact_uri or "",
                    clarification_question="",
                    decision_request=None,
                    surface_id=surface_plan.surface_id if surface_plan is not None and surface_plan.answerable else "",
                    surface_mode=surface_plan.mode if surface_plan is not None else "",
                    transition_gate=transition_gate,
                )
            if deck is not None:
                deck_uri = deck.uri
                if surface_plan is not None:
                    try:
                        await self._register_feedback_surface(surface_plan, deck, cycle_id=cycle_id, project_id=project_id)
                    except Exception as exc:
                        registration_error = exc
        if registration_error is not None:
            raise registration_error
        if (produced_usable_evidence or evidence_exception is not None) and artifact_uri and artifact_hash:
            submitter = getattr(self._repo, "submit_stage_for_review", None)
            if callable(submitter):
                current = await self._repo.get_cycle(cycle_id, project_id=project_id)
                if current is None:
                    raise RuntimeError("Cycle disappeared before Test could enter human review.")
                await submitter(cycle_id=cycle_id, project_id=project_id, stage="test", expected_db_revision=int(current["db_revision"]), actor_user_id=str(user_id), idempotency_key=f"{execution_key}:test-auto-submit")
        if artifact_uri:
            note = artifact_digest or f"Ran {len(results)} bounded {stage} worker(s) and attached a review package at {artifact_uri}."
        elif stage_refusal:
            note = "\n".join([f"Ran {len(results)} bounded {stage} worker(s) and recorded every outcome, but the stage could not create review evidence.", "", stage_refusal])
        else:
            note = "\n".join(
                [
                    f"Ran {len(results)} bounded {stage} worker(s) and recorded every outcome, but none produced usable evidence, so no review artifact was attached.",
                    *(["", "Why each worker did not count:", *_failure_reasons(results)] if results else []),
                ]
            )
        control_request = None
        if stage_activity is not None:
            if not produced_usable_evidence:
                await stage_activity.settle(ActivityState.FAILED)
        _summary_chair = _chair_result_of(results) or {}
        return LiveStageResult(
            stage=stage,
            cycle_id=cycle_id,
            note=note,
            worker_count=len(results),
            produced_usable_evidence=produced_usable_evidence,
            artifact_uri=artifact_uri,
            clarification_question=None,
            deck_uri=deck_uri,
            feedback_surface_id=surface_plan.surface_id if surface_plan is not None and deck is not None else None,
            test_assessment=test_assessment,
            review_meeting_requirement=review_meeting_requirement,
            control_request=control_request,
            research_question=str(cycle.get("research_question") or ""),
            chair_summary=str(_summary_chair.get("summary") or ""),
            chair_consensus=_summary_chair.get("consensus"),
        )
