"""Data readiness and reconciliation (Phase 6).

This is the bridge the design places between Design approval and Build, and its
whole purpose is to make a disagreement between two data sources *visible and
unresolvable-in-silence*. Two rules shape everything here, and both come
straight from the phase's no-go list — "no-go if an agent can silently resolve a
contradiction or mutate raw inputs":

**An agent proposes; a person decides — but only where it matters.** A unit
conversion from bushels per acre to megagrams per hectare is arithmetic, and
demanding a human signature on it would train reviewers to click through the
whole matrix. A contradiction between two sources about what a treatment code
*means* is not arithmetic: whichever way it is resolved changes the result, and
the evidence does not decide it. :data:`HUMAN_RESOLVED_CHECKS` is that line, and
:func:`apply_resolution` enforces it rather than leaving it to the caller.

**Raw data is immutable, and the gate has to be able to tell.** Every declared
source carries a content hash. The reconciliation approval binds the fingerprint
of that whole set, so a dataset that changes afterwards invalidates the approval
instead of quietly carrying it forward into Build — the design's "if a dataset
changes, the reconciliation gate is invalidated and must be rerun".

Everything in this module is pure. The gate outcome is a function of the rows
and the datasets, so the same computation answers "may Build start?" for a
person looking at the matrix and for the repository about to commit a
transition, and the two cannot disagree.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType


class ReconciliationCheck(StrEnum):
    """The checks the bridge may require, from the design's list.

    A closed set, so a domain pack selects from these rather than inventing a
    check nobody wrote a resolution rule for.
    """

    SOURCE_INVENTORY = "source_inventory"
    DATASET_VERSIONS = "dataset_versions"
    RAW_DATA_IMMUTABLE = "raw_data_immutable"
    IDENTIFIER_INTEGRITY = "identifier_integrity"
    ENTITY_MAPPING = "entity_mapping"
    UNITS_AND_ENCODING = "units_and_encoding"
    CONTRADICTORY_SOURCES = "contradictory_sources"
    EXPECTED_COUNTS = "expected_counts"
    DUPLICATION_AND_EXCLUSION = "duplication_and_exclusion"
    MISSINGNESS = "missingness"
    IMPLAUSIBLE_VALUES = "implausible_values"
    TRAIT_DIRECTION = "trait_direction"
    ALIGNMENT = "alignment"
    POPULATION_STRUCTURE = "population_structure"
    TRAIN_TEST_SEPARATION = "train_test_separation"
    LEAKAGE_RISK = "leakage_risk"


CHECK_LABELS: Mapping[ReconciliationCheck, str] = MappingProxyType(
    {
        ReconciliationCheck.SOURCE_INVENTORY: "Source inventory and provenance",
        ReconciliationCheck.DATASET_VERSIONS: "File and dataset versions",
        ReconciliationCheck.RAW_DATA_IMMUTABLE: "Immutable raw-data declaration",
        ReconciliationCheck.IDENTIFIER_INTEGRITY: "Identifier uniqueness and referential integrity",
        ReconciliationCheck.ENTITY_MAPPING: "Germplasm, pedigree, plot, treatment, and environment mapping",
        ReconciliationCheck.UNITS_AND_ENCODING: "Units, encodings, and missing-value conventions",
        ReconciliationCheck.CONTRADICTORY_SOURCES: "Contradictory source comparison",
        ReconciliationCheck.EXPECTED_COUNTS: "Expected sample and material counts",
        ReconciliationCheck.DUPLICATION_AND_EXCLUSION: "Duplication and exclusion report",
        ReconciliationCheck.MISSINGNESS: "Missingness report",
        ReconciliationCheck.IMPLAUSIBLE_VALUES: "Biologically implausible values",
        ReconciliationCheck.TRAIT_DIRECTION: "Known-direction trait contrasts",
        ReconciliationCheck.ALIGNMENT: "Phenotype, genotype, and sample alignment",
        ReconciliationCheck.POPULATION_STRUCTURE: "Population structure",
        ReconciliationCheck.TRAIN_TEST_SEPARATION: "Train/test and temporal separation",
        ReconciliationCheck.LEAKAGE_RISK: "Leakage risk",
    }
)


#: Checks whose resolution a person must make. Each one is a judgement the
#: evidence underdetermines: what a code *means*, which of two disagreeing
#: sources is authoritative, whether an exclusion is defensible, or whether a
#: leakage path matters for this design. An agent may propose a resolution for
#: any of them — it just cannot be the actor that closes the row.
HUMAN_RESOLVED_CHECKS: frozenset[ReconciliationCheck] = frozenset(
    {
        ReconciliationCheck.CONTRADICTORY_SOURCES,
        ReconciliationCheck.TRAIT_DIRECTION,
        ReconciliationCheck.DUPLICATION_AND_EXCLUSION,
        ReconciliationCheck.IMPLAUSIBLE_VALUES,
        ReconciliationCheck.LEAKAGE_RISK,
        ReconciliationCheck.TRAIN_TEST_SEPARATION,
    }
)


class RowStatus(StrEnum):
    """Where one matrix row stands."""

    #: Nobody has looked at it yet.
    OPEN = "open"
    #: An agent proposed a resolution and a person has not confirmed it.
    PROPOSED = "proposed"
    #: Settled. The row no longer blocks the gate.
    RESOLVED = "resolved"
    #: Settled as "this cannot be fixed from here". Always blocks the gate.
    BLOCKED = "blocked"
    #: Deliberately accepted as out of scope, with a rationale. Does not block.
    WAIVED = "waived"


class ActorType(StrEnum):
    """Who acted on a row. Recorded because the matrix must show it."""

    HUMAN = "human"
    AGENT = "agent"


class BlockerKind(StrEnum):
    """Why a row cannot be settled, which decides the gate's outcome code."""

    MISSING_DATA = "missing_data"
    CONFLICTING_SOURCES = "conflicting_sources"
    INDETERMINATE = "indeterminate"


class GateOutcome(StrEnum):
    """The bridge's verdict, from the design's fixed list.

    Build cannot begin from any outcome other than :attr:`READY_FOR_BUILD`.
    """

    READY_FOR_BUILD = "ready_for_build"
    CHANGES_REQUIRED = "changes_required"
    BLOCKED_MISSING_DATA = "blocked_missing_data"
    BLOCKED_CONFLICTING_SOURCES = "blocked_conflicting_sources"
    INCONCLUSIVE_DATA = "inconclusive_data"


class ReconciliationRefused(ValueError):
    """A requested reconciliation action is not permitted."""


@dataclass(frozen=True, slots=True)
class DatasetBinding:
    """One declared input, pinned by content hash.

    ``declared_immutable`` is the steward's assertion that the path holds raw
    data Build must not write to. It is carried here rather than inferred from a
    filesystem mode because the assertion is what a reviewer approves, and a
    permission bit can change without anyone deciding anything.
    """

    source_key: str
    uri: str
    content_hash: str
    declared_immutable: bool = True
    role: str = "raw"

    def __post_init__(self) -> None:
        if not self.source_key.strip():
            raise ReconciliationRefused("A dataset binding needs a source key.")
        if not _is_sha256(self.content_hash):
            raise ReconciliationRefused(f"Dataset {self.source_key!r} needs a lowercase SHA-256 content hash.")

    def as_dict(self) -> dict[str, object]:
        return {
            "source_key": self.source_key,
            "uri": self.uri,
            "content_hash": self.content_hash,
            "declared_immutable": self.declared_immutable,
            "role": self.role,
        }


@dataclass(frozen=True, slots=True)
class ReconciliationRow:
    """One line of the matrix: a field, what each source says, and the decision."""

    row_id: str
    check: ReconciliationCheck
    field_name: str
    source_a_label: str = ""
    source_a_value: str = ""
    source_b_label: str = ""
    source_b_value: str = ""
    required: bool = True
    status: RowStatus = RowStatus.OPEN
    resolution: str = ""
    resolved_by_actor: ActorType | None = None
    resolved_by_user_id: str | None = None
    blocker_kind: BlockerKind | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.row_id.strip():
            raise ReconciliationRefused("A reconciliation row needs an id.")
        if not self.field_name.strip():
            raise ReconciliationRefused("A reconciliation row needs a field name.")
        if self.status is RowStatus.BLOCKED and self.blocker_kind is None:
            raise ReconciliationRefused(f"Row {self.row_id!r} is blocked but records no blocker kind.")
        if self.status in _SETTLED_STATUSES and not self.resolution.strip():
            raise ReconciliationRefused(f"Row {self.row_id!r} is {self.status} but records no rationale.")

    @property
    def needs_human_decision(self) -> bool:
        """Whether closing this row requires a person."""
        return self.check in HUMAN_RESOLVED_CHECKS

    @property
    def blocks_gate(self) -> bool:
        """Whether this row prevents ``ready_for_build``.

        A proposed-but-unconfirmed resolution still blocks. That is the point of
        the state: an agent's proposal is visible work, not a decision, and a
        gate that treated it as one would be the silent resolution this bridge
        exists to prevent.
        """
        if self.status is RowStatus.BLOCKED:
            return True
        if not self.required:
            return False
        return self.status in {RowStatus.OPEN, RowStatus.PROPOSED}

    def as_dict(self) -> dict[str, object]:
        return {
            "row_id": self.row_id,
            "check": self.check.value,
            "check_label": CHECK_LABELS[self.check],
            "field_name": self.field_name,
            "source_a_label": self.source_a_label,
            "source_a_value": self.source_a_value,
            "source_b_label": self.source_b_label,
            "source_b_value": self.source_b_value,
            "required": self.required,
            "status": self.status.value,
            "resolution": self.resolution,
            "resolved_by_actor": self.resolved_by_actor.value if self.resolved_by_actor else None,
            "resolved_by_user_id": self.resolved_by_user_id,
            "blocker_kind": self.blocker_kind.value if self.blocker_kind else None,
            "evidence_refs": list(self.evidence_refs),
            "needs_human_decision": self.needs_human_decision,
            "blocks_gate": self.blocks_gate,
        }


_SETTLED_STATUSES: frozenset[RowStatus] = frozenset({RowStatus.PROPOSED, RowStatus.RESOLVED, RowStatus.BLOCKED, RowStatus.WAIVED})


@dataclass(frozen=True, slots=True)
class GateEvaluation:
    """The bridge's computed verdict and why."""

    outcome: GateOutcome
    blocking_rows: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    dataset_fingerprint: str = ""
    resolved_count: int = 0
    total_required: int = 0

    @property
    def ready(self) -> bool:
        return self.outcome is GateOutcome.READY_FOR_BUILD

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome.value,
            "ready": self.ready,
            "blocking_rows": list(self.blocking_rows),
            "reasons": list(self.reasons),
            "dataset_fingerprint": self.dataset_fingerprint,
            "resolved_count": self.resolved_count,
            "total_required": self.total_required,
        }


def _is_sha256(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def dataset_fingerprint(datasets: Iterable[DatasetBinding]) -> str:
    """A single hash over every declared input.

    Sorted by source key so the fingerprint depends on the *set* of inputs and
    their contents, not on the order someone happened to declare them — a
    reordering must not read as a data change, and a swapped file must.
    """
    payload = [
        [
            item.source_key,
            item.uri,
            item.content_hash,
            item.role,
            item.declared_immutable,
        ]
        for item in sorted(datasets, key=lambda binding: binding.source_key)
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def apply_resolution(
    row: ReconciliationRow,
    *,
    status: RowStatus,
    resolution: str,
    actor: ActorType,
    actor_user_id: str | None = None,
    blocker_kind: BlockerKind | None = None,
    evidence_refs: Sequence[str] = (),
) -> ReconciliationRow:
    """Return a **new** row with the decision applied.

    The rule this enforces is the phase's central one. An agent acting on a
    judgement check may only reach :attr:`RowStatus.PROPOSED`; asking for
    ``RESOLVED`` or ``WAIVED`` there is refused outright rather than downgraded,
    because a silently downgraded write would leave the caller believing the row
    was closed.

    Reporting a blocker is deliberately *not* restricted to humans: an agent
    finding 19 missing hybrid IDs is exactly the work it was dispatched to do,
    and a blocker only ever makes the gate stricter.
    """
    text = resolution.strip()
    if not text:
        raise ReconciliationRefused("A reconciliation decision requires a rationale.")
    if status is RowStatus.OPEN:
        raise ReconciliationRefused("A decision cannot reopen a row; record a new revision instead.")
    if status is RowStatus.BLOCKED and blocker_kind is None:
        raise ReconciliationRefused("Blocking a row requires a blocker kind.")

    if actor is ActorType.AGENT and status in {RowStatus.RESOLVED, RowStatus.WAIVED} and row.needs_human_decision:
        raise ReconciliationRefused(f"{CHECK_LABELS[row.check]} is a human decision; an agent may only propose a resolution for row {row.row_id!r}.")
    if actor is ActorType.HUMAN and not actor_user_id:
        raise ReconciliationRefused("A human decision must record the reviewer's identity.")
    if status is RowStatus.PROPOSED and actor is ActorType.HUMAN:
        raise ReconciliationRefused("A person's decision is a decision, not a proposal.")

    return replace(
        row,
        status=status,
        resolution=text,
        resolved_by_actor=actor,
        resolved_by_user_id=actor_user_id,
        blocker_kind=blocker_kind if status is RowStatus.BLOCKED else None,
        evidence_refs=tuple(dict.fromkeys(str(item) for item in evidence_refs if str(item).strip())) or row.evidence_refs,
    )


def dataset_readiness_reasons(datasets: Sequence[DatasetBinding]) -> tuple[str, ...]:
    """Why these declared inputs are not usable evidence, if they are not.

    Public because these two guarantees — a result names the data it ran on,
    and raw inputs are declared immutable — outlive the reconciliation gate
    itself. When a deployment does not require reconciliation, Build enforces
    them at the point it records lineage instead.
    """
    return _dataset_reasons(datasets)


def _dataset_reasons(datasets: Sequence[DatasetBinding]) -> tuple[str, ...]:
    if not datasets:
        return ("No data sources have been declared, so nothing can be reconciled.",)
    mutable = [item.source_key for item in datasets if item.role == "raw" and not item.declared_immutable]
    if mutable:
        return (f"Raw sources not declared immutable: {', '.join(sorted(mutable))}.",)
    return ()


def evaluate_gate(
    rows: Sequence[ReconciliationRow],
    datasets: Sequence[DatasetBinding],
    *,
    design_approved: bool = True,
    unreadable_row_ids: Sequence[str] = (),
) -> GateEvaluation:
    """Compute the bridge's verdict from the matrix and the declared inputs.

    The outcome code is chosen by the *worst* blocker present, in a fixed
    order — a conflicting source outranks missing data, which outranks an
    indeterminate row — so the code a reviewer sees names the thing that
    actually has to be fixed first rather than whichever blocker sorted first.
    """
    fingerprint = dataset_fingerprint(datasets)
    required_rows = [row for row in rows if row.required]
    resolved = sum(1 for row in required_rows if row.status in {RowStatus.RESOLVED, RowStatus.WAIVED})

    reasons: list[str] = []
    if not design_approved:
        reasons.append("The Design stage has not been approved, so there is nothing to reconcile against.")
    reasons.extend(_dataset_reasons(datasets))
    if not rows:
        reasons.append("No reconciliation rows have been recorded, so data readiness has not been demonstrated.")
    if unreadable_row_ids:
        reasons.append(f"{len(unreadable_row_ids)} reconciliation row(s) could not be read and must be repaired before Build.")

    blocking = [row for row in rows if row.blocks_gate]
    blocked_kinds = {row.blocker_kind for row in blocking if row.status is RowStatus.BLOCKED and row.blocker_kind}

    for row in blocking:
        if row.status is RowStatus.BLOCKED:
            reasons.append(f"{row.field_name}: {row.resolution}")
        elif row.status is RowStatus.PROPOSED:
            reasons.append(f"{row.field_name}: a resolution is proposed but not confirmed by a reviewer.")
        else:
            reasons.append(f"{row.field_name}: not yet reconciled.")

    if not reasons and not blocking:
        outcome = GateOutcome.READY_FOR_BUILD
    elif BlockerKind.CONFLICTING_SOURCES in blocked_kinds:
        outcome = GateOutcome.BLOCKED_CONFLICTING_SOURCES
    elif BlockerKind.MISSING_DATA in blocked_kinds:
        outcome = GateOutcome.BLOCKED_MISSING_DATA
    elif BlockerKind.INDETERMINATE in blocked_kinds:
        outcome = GateOutcome.INCONCLUSIVE_DATA
    else:
        outcome = GateOutcome.CHANGES_REQUIRED

    return GateEvaluation(
        outcome=outcome,
        blocking_rows=tuple(dict.fromkeys([row.row_id for row in blocking] + [str(row_id) for row_id in unreadable_row_ids])),
        reasons=tuple(dict.fromkeys(reasons)),
        dataset_fingerprint=fingerprint,
        resolved_count=resolved,
        total_required=len(required_rows),
    )


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    """What a reconciliation approval was bound to.

    Recorded at approval time so a later change can be *detected* rather than
    assumed absent. Both halves matter: the datasets are the science, and the
    spec version is the contract under which the reviewer judged it.
    """

    dataset_fingerprint: str
    stage_spec_key: str
    policy_version: str

    def as_dict(self) -> dict[str, str]:
        return {
            "dataset_fingerprint": self.dataset_fingerprint,
            "stage_spec_key": self.stage_spec_key,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True, slots=True)
class InvalidationCheck:
    """Whether a recorded approval still describes the current world."""

    invalidated: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {"invalidated": self.invalidated, "reasons": list(self.reasons)}


def check_approval_still_valid(
    binding: ApprovalBinding | None,
    *,
    current_fingerprint: str,
    current_spec_key: str,
    current_policy_version: str,
) -> InvalidationCheck:
    """Compare a recorded approval against the world as it stands now.

    An absent binding is treated as invalidated. An approval nobody can pin to a
    dataset set is an approval nobody can check, and defaulting to "still fine"
    would let exactly the pre-Phase-6 records this phase is meant to supersede
    carry into Build.
    """
    if binding is None:
        return InvalidationCheck(invalidated=True, reasons=("No approval binding was recorded, so the approval cannot be verified.",))

    reasons: list[str] = []
    if binding.dataset_fingerprint != current_fingerprint:
        reasons.append("A declared dataset changed since this reconciliation was approved.")
    if binding.stage_spec_key != current_spec_key:
        reasons.append(f"The reconciliation contract moved from {binding.stage_spec_key} to {current_spec_key}.")
    if binding.policy_version != current_policy_version:
        reasons.append(f"The review policy moved from {binding.policy_version} to {current_policy_version}.")
    return InvalidationCheck(invalidated=bool(reasons), reasons=tuple(reasons))


def summarize_matrix(rows: Sequence[ReconciliationRow]) -> dict[str, int]:
    """Counts per status, for the rail and the stage header."""
    counts = {status.value: 0 for status in RowStatus}
    for row in rows:
        counts[row.status.value] += 1
    return counts
