"""What Test does when the evidence it was handed cannot be trusted.

Test's job is to re-run an approved Build's contract and say what happened. When
the evidence itself is unusable — quarantined, unreadable, or contradicted — Test
still owes a durable, reviewable account of *why*, and that account is what this
module writes.

The rule the plan states and this module keeps: Test may **report** invalid
evidence; it may not repair it. Nothing here rewrites a rerun spec, guesses a
path, or injects an ambient input. It writes a package, reads one back, and
renders the deck a human answers from.

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
from deerflow.agents.dbtl.live_stage.feedback_surfaces import RenderedDeck, _persist_deck
from deerflow.agents.dbtl.live_stage.test_rerun import TestRerunRecord, parse_test_rerun_record
from deerflow.agents.dbtl.live_stage.workspace import WORKSPACE_VIRTUAL_ROOT, atomic_write, verified_workspace_files
from deerflow.dbtl.council_deck import (
    extract_commentable_slides,
    render_design_deck_shell,
    render_design_deck_slide,
    render_stage_feedback_bridge,
    render_stage_review_controls,
)
from deerflow.dbtl.evidence_exception import EvidenceExceptionDossier
from deerflow.dbtl.review_paths import stage_file_name, stage_output_dir
from deerflow.dbtl.stage_runner import StageExecutionOutcome, WorkUnit
from deerflow.dbtl.worker_result import StageWorkerResult, WorkerResultRejected, parse_worker_result
from deerflow.projects.storage import ensure_project_dirs, project_outputs_dir

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
