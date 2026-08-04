"""Where stage work is allowed to read and write, and how a path is proved safe.

Extracted from the adapter so the Build workflow's own steps can use the same
resolution rules. Two callers with two copies of "is this reference inside the
project?" is how a containment check ends up enforced on one path and not the
other, and the one that is missed is always the one a worker authored.

Every function here treats a reference as **untrusted text** until it has been
proved to live inside the project root: no symlink is followed on the way in,
`..` is refused rather than normalized away, and an absolute or scheme-bearing
reference is refused outright.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from deerflow.projects.storage import project_outputs_dir

#: What the sandbox calls the project root. The manifest lists the human-visible
#: folder, but a worker can only *read* through the virtual path, so the two must
#: be joined before the listing is shown to anyone who will act on it.
WORKSPACE_VIRTUAL_ROOT = "/mnt/user-data"
STAGE_WORK_ROOT = ".dbtl-stage-work"
STAGE_UNIT_WORKSPACE_PLACEHOLDER = "__DBTL_UNIT_WORKSPACE__"

#: Never listed to a worker and never treated as Build input: version control,
#: legacy state, the stage's own scratch tree, and dependency caches.
IGNORED_PROJECT_DIRS = frozenset({".git", ".greenagent", STAGE_WORK_ROOT, "node_modules", "__pycache__"})

#: A shell command repeats the whole workspace prefix on every path it names,
#: and every repeat is re-sent on each of the phase's later model calls. Bash
#: can bind it once, and the local path audit already permits exactly this
#: shape: one literal, ordered assignment authorizes a later ``cd "$STAGE"``.
#: The shape matters — a reassignment, a command substitution, or an assignment
#: after ``&&`` is refused — so the prompt shows the permitted form rather than
#: describing the idea and letting a worker guess at it.
#:
#: The second half is not padding. The variable is a *shell* convenience;
#: ``write_file``, ``str_replace``, and every path in the result contract are
#: resolved by tools that have no shell to expand it, so teaching the idiom
#: without saying where it does not apply trades a few tokens for a run of
#: refused writes.
SHELL_WORKSPACE_IDIOM = """
In Bash, bind that directory once instead of repeating it:
  STAGE=<the directory above>; cd "$STAGE"; python src/simulate.py
Assign it literally and before its first use, in that order — a reassignment or
a computed value is refused by the path guard. Everywhere outside Bash — the
full path arguments to write_file and str_replace, and every path in your
result — name the full path; those tools have no shell to expand $STAGE.
""".strip()


#: How much of the digest reaches a path. Random hex tokenizes at roughly half
#: the density of prose, so every character here is paid twice over — and paid
#: again on every tool call, every echoed result, and every model call in the
#: phase, because the conversation so far is re-sent each time. Twelve hex
#: characters is 48 bits, separating unit ids within one stage attempt and
#: attempt ids within one project: tens of values, not billions. Widen it if the
#: thing being separated ever becomes adversarial or global; this is a
#: collision-avoidance width, not a security one.
_TOKEN_HEX_CHARS = 12


def safe_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_TOKEN_HEX_CHARS]


def prepare_stage_workspace(
    project_root: str,
    *,
    attempt_id: str,
    stage: str,
) -> tuple[str, Path]:
    """Create the worker's writable area outside repository-owned DBTL output.

    Workers author implementation files and derived outputs here. The adapter
    alone publishes validated review packages under ``outputs/dbtl``. Keeping
    those two paths separate preserves the ordinary-agent write fence while
    giving a real Build/Test worker somewhere it can execute its contract.
    """
    root = Path(project_root).expanduser().resolve()
    host = project_outputs_dir(root) / STAGE_WORK_ROOT / attempt_id / stage
    host.mkdir(parents=True, exist_ok=True)
    return f"{WORKSPACE_VIRTUAL_ROOT}/outputs/{STAGE_WORK_ROOT}/{attempt_id}/{stage}", host


def unit_stage_workspace(stage_workspace: str, unit_id: str) -> str:
    """Return the isolated writable directory for one concurrent worker."""
    return f"{stage_workspace.rstrip('/')}/{safe_token(unit_id)}"


def project_manifest(project_root: str, *, limit: int = 120) -> list[dict[str, Any]]:
    """Return a bounded, metadata-only view of the human-visible project folder.

    Paths are emitted as the **virtual paths a worker can actually open**
    (``/mnt/user-data/...``), not as paths relative to the project root.
    ``read_file`` rejects anything outside the virtual prefix, so a relative
    listing was an invitation to a permission error: a whole design meeting
    reported "every file read was denied", each participant returned no
    result, and the chair could only record that it had nothing to synthesize
    from. The listing is the only place most workers learn a path exists, so
    it has to name the path in the form they can use.
    """
    root = Path(project_root).expanduser().resolve()
    entries: list[dict[str, Any]] = []
    try:
        paths = sorted(root.rglob("*"), key=lambda item: item.as_posix())
    except OSError:
        return entries
    for path in paths:
        if len(entries) >= limit:
            break
        try:
            relative = path.relative_to(root)
            if any(part in IGNORED_PROJECT_DIRS for part in relative.parts):
                continue
            stat = path.stat()
        except (OSError, ValueError):
            continue
        entries.append(
            {
                "path": f"{WORKSPACE_VIRTUAL_ROOT}/{relative.as_posix()}",
                "kind": "directory" if path.is_dir() else "file",
                "size_bytes": 0 if path.is_dir() else stat.st_size,
            }
        )
    return entries


def project_file_snapshot(project_root: str, *, limit: int = 5_000) -> dict[str, tuple[int, int]]:
    """Remember which project files existed before Build touched the workspace.

    Build owns input discovery when Reconciliation is optional.  The worker
    reports the files it actually examined and the server binds their bytes,
    but only files present in this pre-run snapshot qualify as inputs.  That
    keeps a newly generated model or report from being mistaken for source
    data and lets us refuse a source that changed during the run.
    """
    root = Path(project_root).expanduser().resolve()
    snapshot: dict[str, tuple[int, int]] = {}
    try:
        paths = sorted(root.rglob("*"), key=lambda item: item.as_posix())
    except OSError:
        return snapshot
    for path in paths:
        if len(snapshot) >= limit:
            break
        try:
            relative = path.relative_to(root)
            if any(part in IGNORED_PROJECT_DIRS for part in relative.parts) or not path.is_file():
                continue
            stat = path.stat()
        except (OSError, ValueError):
            continue
        snapshot[relative.as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_lexical_path(reference: str, *, project_root: str) -> tuple[str, Path] | None:
    """Map one virtual reference without following worker-authored symlinks."""
    value = reference.strip()
    if not value:
        return None
    virtual_prefix = f"{WORKSPACE_VIRTUAL_ROOT}/"
    if value.startswith(virtual_prefix):
        relative = value[len(virtual_prefix) :]
    elif value.startswith("/") or "://" in value:
        return None
    else:
        relative = value
    relative_path = PurePosixPath(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    normalized = relative_path.as_posix()
    root = Path(project_root).expanduser().resolve()
    return normalized, root.joinpath(*relative_path.parts)


def workspace_relative_path(reference: str, *, project_root: str) -> tuple[str, Path] | None:
    """Resolve one worker-authored workspace reference without escaping scope."""
    lexical = workspace_lexical_path(reference, project_root=project_root)
    if lexical is None:
        return None
    _relative, candidate = lexical
    root = Path(project_root).expanduser().resolve()
    candidate = candidate.resolve()
    try:
        normalized = candidate.relative_to(root).as_posix()
    except ValueError:
        return None
    return normalized, candidate


def read_workspace_text(
    reference: str,
    *,
    project_root: str,
    max_bytes: int = 4 * 1024 * 1024,
) -> tuple[str, str]:
    """Read one contained workspace file, returning ``(text, sha256)``.

    Hashes the **whole** file even when only part of it is decoded: the hash is
    what binds the record to the document, and hashing a truncated prefix would
    quietly produce a binding that matches nothing.

    Raises ``FileNotFoundError`` when the reference does not resolve inside the
    project or is not a regular file, and ``OSError``/``UnicodeDecodeError``
    for the ordinary read failures — callers classify those, because "the
    approved Design is not where it says it is" and "the disk is failing" are
    different things to report.
    """
    resolved = workspace_relative_path(reference, project_root=project_root)
    if resolved is None:
        raise FileNotFoundError(f"{reference!r} is outside the project workspace.")
    _relative, path = resolved
    if not path.is_file():
        raise FileNotFoundError(f"{reference!r} is not a regular file in the project workspace.")
    content_hash = sha256_file(path)
    with path.open("rb") as source:
        raw = source.read(max_bytes)
    return raw.decode("utf-8"), content_hash


def manifest_entries(manifest: object) -> tuple[Mapping[str, Any], ...]:
    """Normalize a manifest listing into hashable, comparable entries."""
    if not isinstance(manifest, (list, tuple)):
        return ()
    return tuple(dict(entry) for entry in manifest if isinstance(entry, Mapping))
