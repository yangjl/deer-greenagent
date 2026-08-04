"""The paths a Build phase may name, and the ones the server hands it.

Two workers on one cycle wrote a host path into generated code — one that had
never existed on this machine — and then spent most of their budget failing to
run it. Neither was short of reasoning: the path simply was not theirs to
choose. The server knows where the grant is, so it issues the paths through the
environment and refuses source that names somewhere else.

This module is pure. It resolves nothing against a real filesystem, because it
runs against a worker's *generated source* — text that has not been executed and
may never be — and a check that needed the file to exist could only run after
the run it exists to prevent.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

WORKSPACE_ENV = "DBTL_WORKSPACE"
PROJECT_ROOT_ENV = "DBTL_PROJECT_ROOT"
INPUT_ENV_PREFIX = "DBTL_INPUT_"

# Reported findings are bounded: a refusal is read by a person, and a wall of
# near-identical lines is what makes one unreadable. The scan itself is not
# truncated -- it stops collecting, not looking, so the count stays honest.
MAX_REPORTED_PATHS = 20

# Interpreters and devices a script legitimately names. These are executables
# and streams rather than places a Build reads or writes its data, which is the
# only thing this scanner is judging. Deliberately narrow: adding a data-bearing
# directory here would make "outside the grant" untrue.
_SYSTEM_PREFIXES = (
    "/bin/",
    "/sbin/",
    "/usr/bin/",
    "/usr/sbin/",
    "/usr/local/bin/",
    "/opt/homebrew/bin/",
    "/dev/",
)

# A path literal, as it appears in source. The leading guards drop `a/b`
# (preceded by a word character, so division and relative segments do not
# match) and `https://host/path` (preceded by a colon-slash). At least two
# segments are required, because a lone `"/"` is a separator far more often
# than it is a file.
_PATH_PATTERN = re.compile(r"""(?<![:\w\\])(?<!:/)(/[A-Za-z0-9._@+-]+(?:/[A-Za-z0-9._@+-]*)+)""")
_ROUTE_REGISTRATION = re.compile(r"(?:@|\b)(?:app|router|server)\s*\.\s*(?:get|post|put|patch|delete|options|head|route|use)\s*\(", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ForeignPath:
    """One absolute path a phase's source named that its grant does not cover."""

    literal: str
    line: int


def _within(path: str, root: str) -> bool:
    """Whether ``path`` is ``root`` itself or something beneath it.

    Compared segment-wise rather than by string prefix: `/mnt/user-data-other`
    starts with `/mnt/user-data` and is a different directory, so a prefix test
    would grant it.
    """
    normalized_root = root.rstrip("/")
    if not normalized_root:
        return False
    return path == normalized_root or path.startswith(f"{normalized_root}/")


def _is_system_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _SYSTEM_PREFIXES)


def _is_application_route(line: str, literal: str) -> bool:
    """Drop a slash literal only in a recognizable route-registration call.

    Exempting by prefix alone makes `open("/api/secrets/token")` invisible to
    the scanner. Context is what distinguishes a URL path from a file path.
    """
    before = line.split(literal, 1)[0]
    return bool(_ROUTE_REGISTRATION.search(before))


def _constant_path_expression(node: ast.AST) -> str | None:
    """Evaluate the narrow Python path compositions workers commonly emit.

    This is intentionally not a general evaluator. It recognizes only string
    constants, `+`, pathlib-style `/`, and one-argument `Path(...)` wrappers,
    which closes common split-literal bypasses without executing source.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and len(node.args) == 1 and not node.keywords:
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if name in {"Path", "PurePath", "PurePosixPath"}:
            return _constant_path_expression(node.args[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
        left = _constant_path_expression(node.left)
        right = _constant_path_expression(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        return f"{left.rstrip('/')}/{right.lstrip('/')}"
    return None


def scan_foreign_paths(source: str, *, allowed_roots: Sequence[str] = ()) -> tuple[ForeignPath, ...]:
    """Report absolute paths in ``source`` that no granted root covers.

    Findings are advisory in the sense that this reads text rather than
    resolving it, so an exotic construction can hide a path from it. It is a
    cheap, early, specific refusal in front of the server's own execution of the
    entry point, which is what actually decides whether the paths were real.
    """
    roots = tuple(root for root in allowed_roots if root and root.strip())
    lines = source.splitlines()
    findings: list[ForeignPath] = []
    seen: set[tuple[str, int]] = set()

    def record(literal: str, line: int) -> bool:
        item = (literal, line)
        source_line = lines[line - 1] if 0 < line <= len(lines) else ""
        if item in seen or _is_system_path(literal) or _is_application_route(source_line, literal) or any(_within(literal, root) for root in roots):
            return False
        seen.add(item)
        findings.append(ForeignPath(literal=literal, line=line))
        return len(findings) >= MAX_REPORTED_PATHS

    for index, line in enumerate(lines, start=1):
        for match in _PATH_PATTERN.finditer(line):
            literal = match.group(1)
            if record(literal, index):
                return tuple(findings)

    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            literal = _constant_path_expression(node)
            if literal and _PATH_PATTERN.fullmatch(literal) and record(literal, getattr(node, "lineno", 1)):
                return tuple(findings)
    return tuple(findings)


def describe_foreign_paths(paths: Iterable[ForeignPath]) -> str:
    """Render findings as a sentence naming what to change and where."""

    items = tuple(paths)
    if not items:
        return ""
    listed = "; ".join(f"{item.literal!r} on line {item.line}" for item in items)
    return (
        f"This Build phase's generated source names {len(items)} path(s) outside its granted workspace: {listed}. "
        f"Read inputs from the paths the server supplies in the environment ({INPUT_ENV_PREFIX}1, "
        f"{INPUT_ENV_PREFIX}2, ...) and write outputs under {WORKSPACE_ENV} instead of hardcoding a location."
    )


def build_input_grant(
    *,
    workspace: str,
    project_root: str,
    declared_inputs: Sequence[str],
) -> dict[str, str]:
    """Build the environment that tells a phase's entry point where its data is.

    Inputs are numbered from one in their declared order rather than named after
    their filenames: a name derived from the data would change when the data
    changed, and the script would have to be edited to keep reading the same
    input.

    An input outside the grant is refused here as well as in the source scan.
    The environment must not become the channel by which a foreign path reaches
    the script that the scanner would have refused for naming it directly.
    """
    roots = tuple(root for root in (project_root, workspace) if root)
    grant: dict[str, str] = {
        WORKSPACE_ENV: str(workspace),
        PROJECT_ROOT_ENV: str(project_root),
        f"{INPUT_ENV_PREFIX}COUNT": str(len(declared_inputs)),
    }
    for position, declared in enumerate(declared_inputs, start=1):
        path = str(declared)
        if not any(_within(path, root) for root in roots):
            raise ValueError(f"Build input {path!r} is outside this phase's granted workspace.")
        grant[f"{INPUT_ENV_PREFIX}{position}"] = path
    return grant
