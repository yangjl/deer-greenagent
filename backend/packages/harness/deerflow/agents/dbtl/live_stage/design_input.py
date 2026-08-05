"""`load_design`: resolve the approved Design deterministically, or refuse.

This is the first Build step, and the only one that can stop a Build before any
sandbox time is spent. It exists because asking a model to rediscover which
document it is implementing is both expensive and silently wrong — a worker that
guessed produced plausible work against the wrong plan, and nothing downstream
could tell.

The refusals below are deliberately split into two codes rather than one:
`DESIGN_MISSING_OR_STALE` means the governance record does not currently support
a Build (nothing approved, no artifact, or the document moved out from under the
approval), while `DESIGN_UNREADABLE` means the record is fine and the file is
not. They are fixed by different people, so they must not be reported the same
way.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from deerflow.agents.dbtl.live_stage.workspace import manifest_entries, read_workspace_text
from deerflow.dbtl.build_input import (
    MAX_DESIGN_EXCERPT_CHARS,
    BuildInputBundle,
    BuildInputError,
    BundleInput,
    BundleInputKind,
    bounded_excerpt,
    dataset_inputs,
)
from deerflow.dbtl.build_workflow import BuildErrorCode
from deerflow.dbtl.deliverables import DeliverableManifest, DeliverableManifestRejected, parse_deliverable_manifest

_SHA256 = re.compile(r"[0-9a-f]{64}")
_MACHINE_RECORD = re.compile(
    r"(?m)^## Machine record\r?\n- Structured package: `(?P<filename>[^`\r\n]+)`\r?\n"
    r"- SHA-256: `(?P<hash>[0-9a-f]{64})`$"
)
_PACKAGE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.json\Z")


def approved_design_artifact(cycle: Mapping[str, Any]) -> dict[str, Any] | None:
    """The newest artifact on the *approved* Design attempt, or nothing.

    Never raises. It runs on every Build/Test/Learn request, and a malformed
    cycle projection must not be the reason a stage cannot run — the callers
    that need a design decide for themselves what its absence means.
    """
    stages = cycle.get("stages")
    if not isinstance(stages, Sequence) or isinstance(stages, str):
        return None
    attempt_id = ""
    for item in stages:
        if isinstance(item, Mapping) and item.get("stage") == "design" and str(item.get("status") or "") == "approved":
            attempt_id = str(item.get("id") or "")
            break
    if not attempt_id:
        return None

    artifacts = cycle.get("artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, str):
        return None
    newest: Mapping[str, Any] | None = None
    for item in artifacts:
        if not isinstance(item, Mapping) or str(item.get("stage_attempt_id") or "") != attempt_id:
            continue
        if newest is None or int(item.get("revision") or 0) > int(newest.get("revision") or 0):
            newest = item
    if newest is None:
        return None
    return {
        "uri": str(newest.get("uri") or ""),
        "content_hash": str(newest.get("content_hash") or ""),
        "revision": int(newest.get("revision") or 0),
        "artifact_type": str(newest.get("artifact_type") or ""),
    }


def _is_meeting_package(payload: Mapping[str, Any]) -> bool:
    if isinstance(payload.get("council"), Mapping):
        return True
    results = payload.get("results")
    return isinstance(results, Sequence) and not isinstance(results, str) and any(isinstance(item, Mapping) and str(item.get("capability") or "").startswith("design_council_") for item in results)


def _deliverables_from_review(
    text: str,
    *,
    review_uri: str,
    project_root: str,
    cycle: Mapping[str, Any],
) -> DeliverableManifest | None:
    """Resolve the sibling machine package named and hashed by approved Markdown."""

    match = _MACHINE_RECORD.search(text)
    has_machine_marker = "Structured package:" in text or "SHA-256:" in text
    if match is None:
        if has_machine_marker:
            raise BuildInputError(
                BuildErrorCode.DESIGN_MISSING_OR_STALE,
                "The approved Design package has an invalid structured package binding, so Build cannot verify its deliverables.",
            )
        # Compatibility for approved Design documents created before the
        # structured review package existed. New meeting packages always carry
        # the server-rendered machine record and therefore cannot enter here.
        return None

    filename = match.group("filename")
    if _PACKAGE_FILENAME.fullmatch(filename) is None or PurePosixPath(filename).name != filename:
        raise BuildInputError(
            BuildErrorCode.DESIGN_MISSING_OR_STALE,
            "The approved Design package names an unsafe structured package path.",
        )
    package_uri = str(PurePosixPath(review_uri).with_name(filename))
    try:
        package_text, package_hash = read_workspace_text(package_uri, project_root=project_root)
    except (FileNotFoundError, OSError, UnicodeDecodeError) as exc:
        raise BuildInputError(
            BuildErrorCode.DESIGN_UNREADABLE,
            f"The approved Design structured package could not be read from {package_uri}: {exc}",
        ) from exc
    if package_hash != match.group("hash"):
        raise BuildInputError(
            BuildErrorCode.DESIGN_MISSING_OR_STALE,
            "The approved Design structured package no longer matches the SHA-256 recorded in the reviewed document.",
        )
    try:
        payload = json.loads(package_text)
    except json.JSONDecodeError as exc:
        raise BuildInputError(
            BuildErrorCode.DESIGN_MISSING_OR_STALE,
            "The approved Design structured package is not valid JSON.",
        ) from exc
    if not isinstance(payload, Mapping):
        raise BuildInputError(
            BuildErrorCode.DESIGN_MISSING_OR_STALE,
            "The approved Design structured package must be a JSON object.",
        )

    manifest_payload = payload.get("deliverable_manifest")
    if manifest_payload is None and not _is_meeting_package(payload):
        # Human-authored Design packages predate the meeting deliverable
        # contract and remain explicitly distinguishable in the machine record.
        return None
    try:
        return parse_deliverable_manifest(
            manifest_payload,
            cycle_class=str(cycle.get("cycle_class") or ""),
        )
    except DeliverableManifestRejected as exc:
        raise BuildInputError(
            BuildErrorCode.DESIGN_MISSING_OR_STALE,
            f"The approved Design deliverable manifest is missing or invalid: {exc}",
        ) from exc


def resolve_build_inputs(
    cycle: Mapping[str, Any],
    *,
    project_root: str,
    datasets: Sequence[Mapping[str, Any]],
    manifest: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    excerpt_limit: int = MAX_DESIGN_EXCERPT_CHARS,
) -> BuildInputBundle:
    """Read the approved Design and everything Build starts from.

    Raises :class:`BuildInputError` rather than returning a partial bundle: a
    Build that cannot say what it is implementing must dispatch nothing, and a
    bundle that exists is one whose design is approved and hash-bound.
    """
    artifact = approved_design_artifact(cycle)
    if artifact is None:
        raise BuildInputError(
            BuildErrorCode.DESIGN_MISSING_OR_STALE,
            "This cycle has no approved Design package, so there is nothing for Build to implement.",
        )

    uri = artifact["uri"].strip()
    declared_hash = artifact["content_hash"].strip().lower()
    if not uri or not _SHA256.fullmatch(declared_hash):
        raise BuildInputError(
            BuildErrorCode.DESIGN_MISSING_OR_STALE,
            "The approved Design package is not bound to a specific document, so Build cannot verify what it would implement.",
        )

    try:
        text, actual_hash = read_workspace_text(uri, project_root=project_root)
    except (FileNotFoundError, OSError, UnicodeDecodeError) as exc:
        raise BuildInputError(
            BuildErrorCode.DESIGN_UNREADABLE,
            f"The approved Design package could not be read from {uri}: {exc}",
        ) from exc

    if actual_hash != declared_hash:
        # A governance failure, not an IO one: the approval bound these exact
        # bytes, and continuing would implement a document nobody approved.
        raise BuildInputError(
            BuildErrorCode.DESIGN_MISSING_OR_STALE,
            f"The approved Design package at {uri} no longer matches the document the approval was recorded against.",
        )

    deliverable_manifest = _deliverables_from_review(
        text,
        review_uri=uri,
        project_root=project_root,
        cycle=cycle,
    )
    excerpt, truncated = bounded_excerpt(text, limit=excerpt_limit)
    return BuildInputBundle(
        design=BundleInput(
            kind=BundleInputKind.APPROVED_ARTIFACT,
            reference=uri,
            content_hash=declared_hash,
            description=artifact["artifact_type"],
        ),
        design_revision=artifact["revision"],
        design_text=excerpt,
        design_truncated=truncated,
        deliverable_manifest=deliverable_manifest,
        inputs=dataset_inputs(datasets),
        manifest=manifest_entries(list(manifest)),
        policy=dict(policy),
    )
