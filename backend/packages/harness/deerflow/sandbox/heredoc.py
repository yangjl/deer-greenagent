"""Small shared classifier for heredocs crossing the local Bash boundary."""

from __future__ import annotations

import re
import shlex

HEREDOC_START = re.compile(
    r"<<(?!<)(?P<strip>-)?[ \t]*(?P<word>'[^'\r\n]+'|\"[^\"\r\n]+\"|(?:\\.|[^\s;&|<>()])+)",
)

_LITERAL_SINKS = frozenset({"cat", "tee"})
_COMMAND_WRAPPERS = frozenset({"builtin", "command"})
_DATA_TARGET = re.compile(r"\.(?:csv|json|log|md|toml|tsv|txt|yaml|yml)(?=[\s\"';&|<>]|$)", re.IGNORECASE)
_CODE_TARGET = re.compile(r"\.(?P<extension>js|pl|py|rb|sh|ts)(?=[\s\"';&|<>]|$)", re.IGNORECASE)


def heredoc_command_name(line: str, marker: re.Match[str]) -> str:
    """Return the simple command receiving this heredoc, when recognizable."""
    prefix = line[: marker.start()]
    segment = re.split(r"&&|\|\||[;|&()]", prefix)[-1].strip()
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return ""
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        tokens.pop(0)
    if tokens and tokens[0] in _COMMAND_WRAPPERS:
        tokens.pop(0)
    return tokens[0].rsplit("/", 1)[-1] if tokens else ""


def heredoc_is_nonexpanding(marker: re.Match[str]) -> bool:
    """Whether Bash will treat the body literally instead of expanding it."""
    raw_word = marker.group("word")
    return raw_word.startswith(("'", '"', "\\")) or "\\" in raw_word


def heredoc_delimiter(marker: re.Match[str]) -> str:
    """Return the delimiter bytes Bash compares against body lines."""
    delimiter = marker.group("word")
    if len(delimiter) >= 2 and delimiter[0] == delimiter[-1] and delimiter[0] in {"'", '"'}:
        delimiter = delimiter[1:-1]
    return re.sub(r"\\(.)", r"\1", delimiter)


def heredoc_is_literal_data(line: str, marker: re.Match[str]) -> bool:
    """Whether virtual paths in this body are portable stored data.

    Only a non-expanding body sent directly to ``cat``/``tee`` qualifies.
    A positively recognized data-file target must appear on either side of
    ``<<``. Extensionless and code targets fail closed because a later command
    can execute them; same-line command chaining is likewise not literal data.
    """
    if not heredoc_is_nonexpanding(marker) or heredoc_command_name(line, marker) not in _LITERAL_SINKS:
        return False
    suffix = line[marker.end() :]
    if re.search(r"&&|\|\||[;|&]", suffix):
        return False
    return _DATA_TARGET.search(line) is not None


def heredoc_code_kind(line: str) -> str:
    """Return the known code family targeted on either side of ``<<``."""
    match = _CODE_TARGET.search(line)
    if match is None:
        return ""
    extension = match.group("extension").lower()
    if extension == "py":
        return "python"
    if extension == "sh":
        return "shell"
    return "code"
