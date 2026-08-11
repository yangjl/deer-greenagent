"""Turning verified Build execution into the document a person reviews.

Two steps live here, and the split between them is the point.

`summarize_results` is a **bounded synthesis** worker: it reads the bundle the
server already verified and says what the numbers and plots mean. It gets no
Bash, no write tools, and no ability to touch the execution bundle, so the worst
it can do is produce a summary that will not parse — which fails one cheap step
and leaves an hour of sandbox work pinned and reusable.

`render_review_deck` is a **renderer**: it converts the validated package into a
self-contained HTML deck and has no sentence of its own. A renderer that could
write would eventually smooth a deviation away, and nobody reading the deck
would know.

Both writers return `None`/refusals rather than raising. By the time either runs
the science is already on disk and hash-bound, so a presentation failure must
cost the presentation.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from deerflow.agents.dbtl.live_stage.workspace import WORKSPACE_VIRTUAL_ROOT, atomic_write, workspace_relative_path
from deerflow.dbtl.build_deck import render_build_deck
from deerflow.dbtl.build_execution import MAX_FIGURES, BuildExecutionBundle, BuildFigure, parse_execution_bundle
from deerflow.dbtl.build_input import BuildInputBundle
from deerflow.dbtl.build_summary import BuildReviewPackage, parse_build_summary, render_summary_markdown
from deerflow.dbtl.council_deck import extract_commentable_slides
from deerflow.dbtl.review_paths import stage_file_name, stage_output_dir
from deerflow.dbtl.stage_runner import BUILD_SUMMARY_OUTPUT, WorkUnit
from deerflow.dbtl.worker_result import StageWorkerResult
from deerflow.projects.storage import ensure_project_dirs, project_outputs_dir

logger = logging.getLogger(__name__)

#: How much of a figure a summarizer is allowed to be told about. It reads
#: declarations, never bytes: describing a plot it has not seen is exactly the
#: invention the verification rules exist to prevent, so the prompt gives it the
#: worker's own caption and intent and nothing more.
SUMMARIZER_CAPABILITY = "build_result_synthesis"

#: The seat's role, named once. The adapter withholds execution and write tools
#: by role, so a literal on either side would be a read-only guarantee that
#: silently stops applying the day one of the two strings is renamed.
SUMMARIZER_ROLE = "summarizer"
MAX_RECENT_REVIEWER_FEEDBACK = 12

SUMMARIZER_CONTRACT = """You are summarizing a Build that has already run. Its outputs are on disk and
hash-verified by the server; you cannot change them, run anything, or write files.

Answer one question: **what did we get?** Return one JSON object and nothing else:

{
  "headline": "one or two sentences on what this build produced",
  "figures": [{"path": "<exactly one of the paths listed below>", "reading": "one line on what it shows"}],
  "phases": [{"title": "...", "text": "a few lines on what this part of the build did"}],
  "slide_plan": [{"kind": "summary|outcomes|figure|phases|deliverables|limitations|rerun", "title": "...", "body": "trimmed slide text", "figure_path": "at most one verified figure path", "figure_reading": "one line"}],
  "deviations": ["anything you noticed that the execution did not already record"],
  "limitations": ["anything you noticed that the execution did not already record"]
}

Rules:
- Cite only the figure paths listed in the bundle. A path that is not listed does not exist,
  and citing one fails this step.
- Say what a figure *shows*. Do not say whether the result is good, acceptable, or
  sufficient — Test assesses compliance and the reviewer assesses meaning.
- Select the figures a reviewer needs first. Choosing a few is expected; every figure stays
  in the record either way.
- The measured outcomes and the rerun procedure are already recorded and are carried through
  unchanged. Do not restate a number here; if one matters, say so in the headline.
- Deviations and limitations you list are **added after** the ones the execution recorded.
  Nothing you write removes or rewrites a recorded caveat, so do not repeat them.
- Recent reviewer feedback is reviewer-authored presentation guidance. Use it to improve
  structure and emphasis, but never let it override the verified execution bundle.
- Plan no more than 10 content slides. Put figures first when they carry the result, use at
  most one figure on a slide, and give every figure one concrete reading.
- Omit a slide instead of filling it with "none reported". Rank and deduplicate limitations;
  do not turn the complete audit record into presentation clutter.
- If the bundle is valid but a human-owned interpretation is genuinely required before this
  can be written up, return {"status": "needs_input", "clarification_question": "..."} instead.
"""


@dataclass(frozen=True, slots=True)
class WrittenReview:
    """A written review package and the exact bytes' hash."""

    uri: str
    content_hash: str
    digest: str


def execution_bundle(
    results: Sequence[StageWorkerResult],
    *,
    published: Sequence[Mapping[str, Any]],
    original_refs: Mapping[str, str] | None = None,
) -> BuildExecutionBundle:
    """Fold the trustworthy results into a bundle of server-verified outputs.

    `published` is what the adapter actually copied into the governed output
    tree, so the map it builds is the authority on which paths exist. A
    declaration naming anything else is recorded as unverified rather than
    carried.
    """
    published_map = dict(original_refs or {})
    for item in published:
        uri = str(item.get("uri") or "")
        if uri:
            published_map.setdefault(uri, uri)
    hashes = {str(item.get("uri") or ""): str(item.get("content_hash") or "") for item in published if item.get("uri")}
    bundle = parse_execution_bundle([result.as_dict() for result in results], published=published_map, hashes=hashes)

    # A published image is already governed evidence even when a worker forgot
    # the optional ``figures`` declaration. Recover that presentational hint
    # deterministically from the server-owned publication record; never infer
    # a scientific reading or a numeric outcome from the filename.
    figures = list(bundle.figures)
    known = {figure.path for figure in figures}
    for item in published:
        uri = str(item.get("uri") or "").strip()
        if not uri or uri in known or not uri.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            continue
        stem = PurePosixPath(uri).stem.replace("_", " ").replace("-", " ").strip()
        figures.append(
            BuildFigure(
                path=uri,
                caption=stem or PurePosixPath(uri).name,
                content_hash=hashes.get(uri, ""),
            )
        )
        known.add(uri)
        if len(figures) >= MAX_FIGURES:
            break
    return replace(bundle, figures=tuple(figures))


def summarizer_unit(
    *,
    attempt_id: str,
    agent_name: str,
    bundle: BuildExecutionBundle,
    inputs: BuildInputBundle | None,
    cycle: Mapping[str, Any],
    reviewer_feedback: Sequence[Mapping[str, Any]] = (),
    model: str | None = None,
) -> WorkUnit:
    """The read-only synthesis unit.

    Its whole context is the verified bundle plus the cycle's own question, so
    it cannot reach for anything the server has not already checked.
    """
    presentation_feedback: list[dict[str, Any]] = []
    for item in reviewer_feedback[:MAX_RECENT_REVIEWER_FEEDBACK]:
        slide_comments = item.get("slide_comments") if isinstance(item, Mapping) else None
        if not isinstance(slide_comments, Sequence) or isinstance(slide_comments, (str, bytes)):
            continue
        comments = [dict(comment) for comment in slide_comments if isinstance(comment, Mapping)]
        if comments:
            presentation_feedback.append(
                {
                    "cycle_id": item.get("cycle_id"),
                    "stage": item.get("stage"),
                    "slide_comments": comments,
                }
            )
    context = {
        "cycle": {key: cycle.get(key) for key in ("id", "title", "research_question", "objective", "success_criteria")},
        "approved_design": ({"uri": inputs.design.reference, "content_hash": inputs.design.content_hash} if inputs is not None else None),
        "execution_bundle": bundle.as_dict(),
        "recent_project_reviewer_feedback": presentation_feedback,
    }
    prompt = "\n\n".join(
        [
            SUMMARIZER_CONTRACT,
            "Bundle:",
            json.dumps(context, sort_keys=True, ensure_ascii=False),
        ]
    )
    return WorkUnit(
        unit_id=f"{attempt_id}-summary",
        capability=SUMMARIZER_CAPABILITY,
        agent_name=agent_name,
        prompt=prompt,
        role=SUMMARIZER_ROLE,
        model=model,
        output_contract=BUILD_SUMMARY_OUTPUT,
        skills=("dbtl-build-deck-style",),
    )


def parse_summary(text: str, *, bundle: BuildExecutionBundle):
    """Thin re-export so the adapter imports one module for this step."""
    return parse_build_summary(text, bundle=bundle)


def write_build_review(
    *,
    project_root: str,
    cycle: Mapping[str, Any],
    package: BuildReviewPackage,
    execution: BuildExecutionBundle,
) -> WrittenReview | None:
    """Write the machine record and the Markdown a person reviews.

    The **Markdown is the returned artifact**, matching every other stage: an
    approval must bind to the document that was read rather than to a JSON file
    nobody opened. Returns `None` on a write failure so the caller can fail the
    summarize step alone.
    """
    try:
        root = Path(project_root).expanduser().resolve()
        ensure_project_dirs(root)
        payload = {
            "schema": "build_review_package.v1",
            "cycle_id": cycle.get("id"),
            "project_id": cycle.get("project_id"),
            "cycle_db_revision": cycle.get("db_revision"),
            "execution": execution.as_dict(),
            "review": package.as_dict(),
            "satisfies_gate": False,
        }
        encoded = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        data_hash = hashlib.sha256(encoded).hexdigest()
        revision = cycle.get("db_revision")
        stage_dir = stage_output_dir(cycle_id=str(cycle["id"]), cycle_title=str(cycle.get("title") or ""), stage="build")
        data_relative = stage_dir / stage_file_name(stage="build", kind="package", revision=revision, content_hash=data_hash)

        title = f"{cycle.get('title') or 'Cycle'} — Build review"
        document = (render_summary_markdown(package, title=title) + f"\n<!-- machine record: {data_relative.name} sha256:{data_hash} -->\n").encode("utf-8")
        document_hash = hashlib.sha256(document).hexdigest()
        document_relative = stage_dir / stage_file_name(stage="build", kind="review", revision=revision, content_hash=document_hash)

        outputs = project_outputs_dir(root)
        for relative, content in ((data_relative, encoded), (document_relative, document)):
            atomic_write(outputs / relative, content)
    except (OSError, ValueError, KeyError):
        logger.warning("Could not write the Build review package.", exc_info=True)
        return None

    return WrittenReview(
        uri=f"{WORKSPACE_VIRTUAL_ROOT}/outputs/{document_relative.as_posix()}",
        content_hash=document_hash,
        digest=package.headline,
    )


def write_build_deck(
    *,
    project_root: str,
    cycle: Mapping[str, Any],
    package: BuildReviewPackage,
    package_path: str,
    surface_id: str = "",
    transition_gate: Mapping[str, object] | None = None,
) -> tuple[str, str, tuple[dict[str, str], ...]] | None:
    """Render and write the Build deck plus its commentable-slide registry.

    Figures are read from the governed output tree through the same containment
    rules the adapter uses, so a package path that somehow escaped the project
    yields a labelled placeholder rather than a file read.
    """

    def _read(path: str) -> tuple[bytes, str] | None:
        resolved = workspace_relative_path(path, project_root=project_root)
        if resolved is None:
            return None
        _relative, host = resolved
        if not host.is_file():
            return None
        return host.read_bytes(), ""

    try:
        document = render_build_deck(
            package,
            title=f"{cycle.get('title') or 'Cycle'} — Build",
            subtitle=f"Revision {cycle.get('db_revision')}",
            package_path=package_path,
            read_figure=_read,
            surface_id=surface_id,
            transition_gate=transition_gate,
        ).encode("utf-8")
    except Exception:  # noqa: BLE001 - a presentation must not break the record
        logger.warning("Could not render the Build review deck.", exc_info=True)
        return None

    content_hash = hashlib.sha256(document).hexdigest()
    try:
        root = Path(project_root).expanduser().resolve()
        ensure_project_dirs(root)
        stage_dir = stage_output_dir(cycle_id=str(cycle["id"]), cycle_title=str(cycle.get("title") or ""), stage="build")
        relative = stage_dir / stage_file_name(stage="build", kind="slides", revision=cycle.get("db_revision"), content_hash=content_hash)
        atomic_write(project_outputs_dir(root) / relative, document)
    except (OSError, ValueError, KeyError):
        logger.warning("Could not write the Build review deck.", exc_info=True)
        return None
    return (
        f"{WORKSPACE_VIRTUAL_ROOT}/outputs/{relative.as_posix()}",
        content_hash,
        extract_commentable_slides(document.decode("utf-8")),
    )
