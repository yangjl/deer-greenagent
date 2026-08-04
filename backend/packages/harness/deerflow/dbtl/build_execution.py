"""What a Build produced, as values a reviewer can check.

A Build package used to answer "what files exist". The question a reviewer
actually has is "what did we get", and the difference is entirely in the
typing: a path list cannot tell you which plot is worth opening or what number
came out, so every reader had to reconstruct both from prose.

Three declarations carry that here — **figures declared as figures**, **key
outcomes as `{name, value, unit}`**, and **the rerun procedure** — and all three
are parsed fail-soft. A malformed figure entry costs that figure, never the
Build: the science is already on disk and hash-bound by the time this runs, and
refusing the whole bundle over a missing caption would discard it to protect a
display detail.

One rule is not fail-soft. **A figure the server did not publish does not
exist.** Declarations are matched against the published output map, so a worker
cannot cite a plot it never wrote, and a later summarizer cannot describe one it
never received.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

#: Bounds on what one Build may declare. Generous — this is an audit record, not
#: a slide deck — but finite, because these travel into prompts and decks.
MAX_FIGURES = 24
MAX_KEY_OUTCOMES = 24
MAX_TEXT_CHARS = 600
MAX_RERUN_ITEMS = 64
MAX_RERUN_COMMAND_CHARS = 2_000

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")


def _text(value: Any, *, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    trimmed = value.strip()
    return trimmed if len(trimmed) <= limit else trimmed[: limit - 1] + "…"


@dataclass(frozen=True, slots=True)
class BuildFigure:
    """A plot the Build produced, with what it is meant to show.

    `shows` is separate from `caption` on purpose: a caption names the figure
    and the intent says why anybody should look at it. A reviewer scanning ten
    figures needs the second one.
    """

    path: str
    caption: str = ""
    shows: str = ""
    phase_key: str = ""
    content_hash: str = ""

    @property
    def is_embeddable(self) -> bool:
        """Whether a renderer can inline these bytes.

        Extension-based, and deliberately so: the renderer names what it skipped
        rather than guessing at a format it cannot decode, and a mislabelled file
        is caught when the bytes are read rather than being silently dropped.
        """
        return self.path.lower().endswith(_IMAGE_SUFFIXES)

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "caption": self.caption, "shows": self.shows, "phase_key": self.phase_key, "content_hash": self.content_hash}


@dataclass(frozen=True, slots=True)
class KeyOutcome:
    """One number the Build produced, named and carrying its unit.

    Typed rather than prose because a reviewer comparing two attempts, and a
    Test stage checking a ceiling, both need the value without parsing a
    sentence — and because "0.42" with no unit is not a result.
    """

    name: str
    value: str
    unit: str = ""
    phase_key: str = ""
    figure: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "unit": self.unit, "phase_key": self.phase_key, "figure": self.figure}


@dataclass(frozen=True, slots=True)
class BuildRerunSpec:
    """A bounded, executable Build rerun record carried into Test."""

    entry_point: str
    command: str
    seed: str = ""
    inputs: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    configuration: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "entry_point": self.entry_point,
            "command": self.command,
            "seed": self.seed,
            "inputs": list(self.inputs),
            "environment": dict(self.environment),
            "configuration": list(self.configuration),
            "expected_outputs": list(self.expected_outputs),
        }


@dataclass(frozen=True, slots=True)
class BuildExecutionBundle:
    """The server's verified account of one Build's execution."""

    outputs: tuple[str, ...] = ()
    figures: tuple[BuildFigure, ...] = ()
    key_outcomes: tuple[KeyOutcome, ...] = ()
    rerun_spec: BuildRerunSpec | None = None
    rerun_procedure: str = ""
    deviations: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    #: Declarations that named something the server never published. Kept rather
    #: than dropped: "the worker said it made a figure and there is no figure"
    #: is a thing a reviewer should be able to see.
    unverified: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def figure_for(self, path: str) -> BuildFigure | None:
        return next((figure for figure in self.figures if figure.path == path), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outputs": list(self.outputs),
            "figures": [figure.as_dict() for figure in self.figures],
            "key_outcomes": [outcome.as_dict() for outcome in self.key_outcomes],
            "rerun_spec": self.rerun_spec.as_dict() if self.rerun_spec is not None else None,
            "rerun_procedure": self.rerun_procedure,
            "deviations": list(self.deviations),
            "limitations": list(self.limitations),
            "unverified": list(self.unverified),
        }


def _strings(value: Any, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(text for text in (_text(item) for item in value) if text)[:limit]


def parse_rerun_spec(value: Any) -> BuildRerunSpec | None:
    """Parse the structured rerun record fail-soft for legacy Build results."""

    if not isinstance(value, Mapping):
        return None
    version = value.get("version", 1)
    if version != 1 or isinstance(version, bool):
        return None
    raw_entry_point = value.get("entry_point")
    raw_command = value.get("command")
    if not isinstance(raw_entry_point, str) or not isinstance(raw_command, str):
        return None
    entry_point = raw_entry_point.strip()
    command = raw_command.strip()
    if not entry_point or len(entry_point) > 1024 or not command or len(command) > MAX_RERUN_COMMAND_CHARS:
        return None

    def exact_strings(field_name: str, *, required: bool) -> tuple[str, ...] | None:
        raw = value.get(field_name)
        if not isinstance(raw, (list, tuple)) or len(raw) > MAX_RERUN_ITEMS:
            return None
        items: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                return None
            text = item.strip()
            if not text or len(text) > 1024:
                return None
            items.append(text)
        if required and not items:
            return None
        return tuple(items)

    inputs = exact_strings("inputs", required=True)
    configuration = exact_strings("configuration", required=False)
    expected_outputs = exact_strings("expected_outputs", required=True)
    raw_environment = value.get("environment")
    if inputs is None or configuration is None or expected_outputs is None or not isinstance(raw_environment, Mapping) or len(raw_environment) > MAX_RERUN_ITEMS:
        return None
    environment: list[tuple[str, str]] = []
    for raw_key, raw_value in raw_environment.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            return None
        key = raw_key.strip()
        item = raw_value.strip()
        if not key or len(key) > 160 or not item or len(item) > 600:
            return None
        environment.append((key, item))
    if not environment:
        return None
    seed_value = value.get("seed")
    seed = str(seed_value)[:160] if isinstance(seed_value, (str, int, float)) and not isinstance(seed_value, bool) else ""
    return BuildRerunSpec(
        entry_point=entry_point,
        command=command,
        seed=seed,
        inputs=inputs,
        environment=tuple(environment),
        configuration=configuration,
        expected_outputs=expected_outputs,
    )


def parse_figures(value: Any) -> tuple[BuildFigure, ...]:
    """Read a worker's figure declarations. Never raises.

    A figure is a *presentational* declaration on top of an artifact the stage
    contract already validated, so a malformed entry costs that figure. Failing
    the result would discard verified execution to protect a caption.
    """
    figures: list[BuildFigure] = []
    for raw in _mappings(value):
        path = _text(raw.get("path") or raw.get("uri") or raw.get("reference"), limit=1024)
        if not path or any(figure.path == path for figure in figures):
            continue
        figures.append(
            BuildFigure(
                path=path,
                caption=_text(raw.get("caption")),
                shows=_text(raw.get("shows") or raw.get("intent") or raw.get("description")),
                phase_key=_text(raw.get("phase_key"), limit=96),
                content_hash=_text(raw.get("content_hash"), limit=64),
            )
        )
    return tuple(figures[:MAX_FIGURES])


def parse_key_outcomes(value: Any) -> tuple[KeyOutcome, ...]:
    """Read a worker's numeric outcomes. Never raises, for the same reason."""
    outcomes: list[KeyOutcome] = []
    for raw in _mappings(value):
        name = _text(raw.get("name"), limit=160)
        number = raw.get("value")
        text_value = str(number) if isinstance(number, (int, float)) and not isinstance(number, bool) else _text(number, limit=160)
        if not name or not text_value:
            continue
        outcomes.append(
            KeyOutcome(
                name=name,
                value=text_value,
                unit=_text(raw.get("unit"), limit=48),
                phase_key=_text(raw.get("phase_key"), limit=96),
                figure=_text(raw.get("figure"), limit=1024),
            )
        )
    return tuple(outcomes[:MAX_KEY_OUTCOMES])


def parse_execution_bundle(
    payloads: Sequence[Mapping[str, Any]],
    *,
    published: Mapping[str, str],
    hashes: Mapping[str, str] | None = None,
) -> BuildExecutionBundle:
    """Fold worker declarations into a bundle, keeping only verified paths.

    `published` maps whatever the worker called an output to the URI the server
    actually wrote and hashed. A declaration naming anything else is recorded in
    `unverified` rather than carried: the record's whole value is that its
    claims point at bytes somebody can open.

    Never raises. Every worker payload here belongs to a result that already
    passed the stage contract, so a shape problem in an optional declaration is
    a presentation defect, and failing the bundle would throw away the
    execution behind it.
    """
    digests = dict(hashes or {})
    figures: list[BuildFigure] = []
    outcomes: list[KeyOutcome] = []
    outputs: list[str] = []
    deviations: list[str] = []
    limitations: list[str] = []
    unverified: list[str] = []
    rerun = ""
    rerun_spec: BuildRerunSpec | None = None
    rerun_conflict = False

    def merge_rerun_specs(current: BuildRerunSpec, incoming: BuildRerunSpec) -> BuildRerunSpec | None:
        if current.entry_point != incoming.entry_point or current.command != incoming.command or current.seed != incoming.seed or current.environment != incoming.environment:
            return None
        inputs = tuple(dict.fromkeys((*current.inputs, *incoming.inputs)))
        configuration = tuple(dict.fromkeys((*current.configuration, *incoming.configuration)))
        expected_outputs = tuple(dict.fromkeys((*current.expected_outputs, *incoming.expected_outputs)))
        if max(len(inputs), len(configuration), len(expected_outputs)) > MAX_RERUN_ITEMS:
            return None
        return BuildRerunSpec(
            entry_point=current.entry_point,
            command=current.command,
            seed=current.seed,
            inputs=inputs,
            environment=current.environment,
            configuration=configuration,
            expected_outputs=expected_outputs,
        )

    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        provenance = payload.get("provenance")
        provenance = provenance if isinstance(provenance, Mapping) else {}
        parsed_rerun = parse_rerun_spec(provenance.get("rerun_spec"))
        if parsed_rerun is not None and not rerun_conflict:
            if rerun_spec is None:
                rerun_spec = parsed_rerun
            else:
                rerun_spec = merge_rerun_specs(rerun_spec, parsed_rerun)
                rerun_conflict = rerun_spec is None
        rerun = rerun or _text(provenance.get("recorded_rerun_procedure"))
        deviations.extend(_strings(payload.get("deviations"), limit=MAX_FIGURES))
        limitations.extend(_strings(payload.get("limitations"), limit=MAX_FIGURES))

        for declared_figure in parse_figures(payload.get("figures")):
            resolved = published.get(declared_figure.path, declared_figure.path if declared_figure.path in published.values() else "")
            if not resolved:
                unverified.append(declared_figure.path)
                continue
            if any(figure.path == resolved for figure in figures):
                continue
            figures.append(replace(declared_figure, path=resolved, content_hash=digests.get(resolved, declared_figure.content_hash)))

        for declared_outcome in parse_key_outcomes(payload.get("key_outcomes")):
            figure = declared_outcome.figure
            outcomes.append(replace(declared_outcome, figure=published.get(figure, figure if figure in published.values() else "")))

        outputs.extend(reference for reference in _strings(payload.get("artifact_refs"), limit=MAX_FIGURES * 4) if reference in published.values())

    return BuildExecutionBundle(
        outputs=tuple(dict.fromkeys(outputs)),
        figures=tuple(figures[:MAX_FIGURES]),
        key_outcomes=tuple(outcomes[:MAX_KEY_OUTCOMES]),
        rerun_spec=(None if rerun_conflict else rerun_spec),
        rerun_procedure=(rerun_spec.command if rerun_spec is not None else rerun),
        deviations=tuple(dict.fromkeys(deviations)),
        limitations=tuple(dict.fromkeys(limitations)),
        unverified=tuple(dict.fromkeys(unverified)),
    )


def _mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))
