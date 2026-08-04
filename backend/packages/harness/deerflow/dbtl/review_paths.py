"""Human-meaningful output paths for DBTL stage review packages.

A project's outputs folder is browsed in Finder by the scientist who owns it, so
these names are read by people. Hash tokens satisfied uniqueness and nothing
else — two of them cannot be told apart at a glance, and neither says which
cycle or stage it belongs to.

The naming is therefore *meaning first, disambiguator last*:

    dbtl/genomic-selection-in-maize-5486db1c/design/design-review-rev2-fd616c.md

The short content suffix stays because it is load-bearing: two runs at the same
cycle revision produce different documents, and the earlier one must not be
silently overwritten. The short cycle-id suffix on the directory is what keeps
two identically titled cycles apart.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Long enough to stay readable, short enough that nested paths do not approach
#: filesystem limits.
DEFAULT_SLUG_LENGTH = 48
_CYCLE_ID_TOKEN_LENGTH = 8
_CONTENT_SUFFIX_LENGTH = 6

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, max_length: int = DEFAULT_SLUG_LENGTH) -> str:
    """A lowercase, hyphenated, ASCII-only path segment.

    Non-ASCII characters are dropped rather than transliterated: guessing a
    romanization would produce a name the author does not recognise, and the
    short id suffix already guarantees uniqueness.
    """
    lowered = (value or "").strip().lower()
    # Encoding through ASCII first means a dropped accent cannot leave a
    # surprising byte in a filename.
    ascii_only = lowered.encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG.sub("-", ascii_only).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    # "." and ".." survive neither the regex nor this guard: both become "".
    return slug


def _cycle_token(cycle_id: str) -> str:
    """A short, readable piece of the cycle id for disambiguation."""
    raw = (cycle_id or "").strip().lower()
    if raw.startswith("cycle-"):
        raw = raw[len("cycle-") :]
    first = _NON_SLUG.sub("-", raw.encode("ascii", "ignore").decode("ascii")).strip("-")
    head = first.split("-")[0] if first else ""
    return head[:_CYCLE_ID_TOKEN_LENGTH]


def stage_output_dir(*, cycle_id: str, cycle_title: str, stage: str) -> Path:
    """The relative directory for one cycle's stage outputs.

    Always relative and always traversal-free: the caller joins it onto the
    project's outputs root, so an absolute or `..`-bearing segment would escape
    the project folder.
    """
    title_slug = slugify(cycle_title)
    token = _cycle_token(cycle_id)

    if title_slug and token:
        cycle_segment = f"{title_slug}-{token}"
    elif title_slug:
        cycle_segment = title_slug
    else:
        # No usable title: the sanitized id is still more meaningful than a hash.
        cycle_segment = slugify(cycle_id) or "cycle"

    stage_segment = slugify(stage) or "stage"
    return Path("dbtl") / cycle_segment / stage_segment


def stage_file_name(
    *,
    stage: str,
    kind: str,
    revision: object,
    content_hash: str,
) -> str:
    """One output file's name.

    ``kind`` is "review" (Markdown, the document the approval binds to),
    "slides" (the HTML deck the meeting's outcome is presented from), "rerun"
    (the shell driver re-running a phased Build's entry points in order), or
    "package" (JSON, the machine record). Anything unrecognized falls back to
    the JSON package shape rather than raising: a new caller getting an
    oddly-named file is a smaller failure than a stage that cannot write its
    evidence.
    """
    stage_slug = slugify(stage) or "stage"
    kind_slug, extension = {
        "review": ("review", "md"),
        "slides": ("slides", "html"),
        "rerun": ("rerun", "sh"),
    }.get(kind, ("package", "json"))

    parts = [stage_slug, kind_slug]
    if revision is not None:
        parts.append(f"rev{revision}")
    suffix = slugify(content_hash)[:_CONTENT_SUFFIX_LENGTH] or "unknown"
    parts.append(suffix)
    return "-".join(parts) + f".{extension}"
