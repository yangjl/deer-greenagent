"""What a work unit needs, expressed as capabilities rather than role names.

The design's rule is that "each work unit declares capabilities, not a fixed
role name". The distinction matters because a role name is an organisational
fact that changes without the science changing: a project that renames its
"data steward" to "data manager" must not thereby lose its reconciliation
worker, and a project with no such person at all must still be able to satisfy
the requirement with someone (or something) that can actually do the work.

The set is closed on purpose. A free-form capability string would let a stage
declare a requirement no selector could ever evaluate, and the failure would
look like a selection bug rather than a spec defect.
"""

from __future__ import annotations

from enum import StrEnum


class Capability(StrEnum):
    """The capabilities a stage may require of a worker.

    Taken verbatim from the design's list so a reviewer can check the two
    against each other by eye.
    """

    BREEDING_STRATEGY = "breeding_strategy"
    GERMPLASM_AND_PEDIGREE = "germplasm_and_pedigree_analysis"
    EXPERIMENTAL_DESIGN = "experimental_design"
    QUANTITATIVE_GENETICS = "quantitative_genetics"
    STATISTICAL_ANALYSIS = "statistical_analysis"
    DATA_RECONCILIATION = "data_reconciliation_and_lineage"
    LITERATURE_REVIEW = "literature_review"
    SOFTWARE_ENGINEERING = "software_and_workflow_engineering"
    FIELD_TRIAL_QC = "field_trial_quality_control"
    VALIDITY_ASSESSMENT = "validity_assessment"
    SCIENTIFIC_REPORTING = "scientific_reporting"
    KNOWLEDGE_SYNTHESIS = "knowledge_synthesis"


CAPABILITIES: frozenset[Capability] = frozenset(Capability)


def parse_capability(value: str) -> Capability:
    """Return the :class:`Capability` for *value*, or raise ``ValueError``.

    Used at every boundary where capabilities arrive as configuration or JSON.
    Unknown values are refused rather than ignored: silently dropping one would
    turn "this stage requires quantitative genetics" into "this stage requires
    nothing", which is the wrong direction to fail in.
    """
    try:
        return Capability(value)
    except ValueError as exc:
        known = ", ".join(sorted(item.value for item in Capability))
        raise ValueError(f"Unknown capability {value!r}; expected one of: {known}") from exc


def parse_capabilities(values: object) -> tuple[Capability, ...]:
    """Parse an iterable of capability strings, preserving order and deduping."""
    if isinstance(values, str) or not hasattr(values, "__iter__"):
        raise ValueError(f"Expected a list of capability names, got {type(values).__name__}.")
    seen: dict[Capability, None] = {}
    for item in values:
        if not isinstance(item, str):
            raise ValueError(f"Capability names must be strings, got {type(item).__name__}.")
        seen[parse_capability(item)] = None
    return tuple(seen)
