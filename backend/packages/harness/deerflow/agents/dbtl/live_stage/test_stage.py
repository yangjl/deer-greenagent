"""What Test does when the evidence it was handed cannot be trusted.

Test's job is to re-run an approved Build's contract and say what happened. When
the evidence itself is unusable — quarantined, unreadable, or contradicted — Test
still owes a durable, reviewable account of *why*, and that account is what this
module writes.

The rule the plan states and this module keeps: Test may **report** invalid
evidence; it may not repair it. Nothing here rewrites a rerun spec, guesses a
path, or injects an ambient input. It writes a package, reads one back, and
renders the deck a human answers from.

Extracted from ``adapter.py`` unchanged. Every function is a pure function of
values already in hand plus one deck write, and none is a monkeypatch target, so
relocating them cannot silently stop a patch intercepting. The Test evidence
*reuse* helpers stayed behind on purpose: they reach ``_sha256_file``, which is
patched on the adapter module to simulate an unreadable artifact, and moving
them would leave that patch rebinding a name nobody calls.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from deerflow.agents.dbtl.live_stage.feedback_surfaces import RenderedDeck, _persist_deck
from deerflow.agents.dbtl.live_stage.workspace import atomic_write
from deerflow.dbtl.council_deck import (
    extract_commentable_slides,
    render_design_deck_shell,
    render_design_deck_slide,
    render_stage_feedback_bridge,
    render_stage_review_controls,
)
from deerflow.dbtl.evidence_exception import EvidenceExceptionDossier
from deerflow.dbtl.review_paths import stage_file_name, stage_output_dir
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
