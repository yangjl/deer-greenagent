"""Render a stage review package as the Markdown a human actually reads.

The durable package is JSON because gates, hashes, and later phases need
structure. A JSON file is not a reading surface, though, and a reviewer being
asked for a scientific judgement should not have to parse one — so every package
is also written as Markdown, and *that* is the artifact the approval binds to.
Binding to the document the human read is the point: the hash then covers
exactly what was reviewed.

Two properties are load-bearing:

* **Deterministic.** No timestamps, no dict ordering surprises. The approval
  binds to this file's hash, so two renders of one package must be byte-equal.
* **Never more confident than its evidence.** Limitations, failed quality
  checks, rejected work units, and stop reasons get their own headings beside
  the claims rather than being folded away. A brief that buries them is how a
  weak design comes to read like a strong one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: Where the reviewer's decision actually happens. Editing a Markdown file is
#: not a governed review: reviewer identity and the revision binding are
#: server-owned, and a gate cannot bind to a file edit.
DECISION_NOTICE = "Record your decision on the Human gate slide in this package's HTML review deck, not by editing this file. The deck binds your identity and this package's revision to the durable record."

_CHAIR_MARKER = "chair"


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _bullets(items: Sequence[Any]) -> list[str]:
    return [f"- {str(item).strip()}" for item in items if str(item).strip()]


def _reading_order(results: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Chair synthesis first, then the individual positions in dispatch order.

    Council results arrive in dispatch order, which puts the synthesis last —
    the opposite of how a reviewer wants to read them.
    """
    chairs = [r for r in results if _CHAIR_MARKER in str(r.get("capability", "")).lower()]
    others = [r for r in results if _CHAIR_MARKER not in str(r.get("capability", "")).lower()]
    return [*chairs, *others]


def _quality_check_line(check: Mapping[str, Any]) -> str:
    name = str(check.get("name") or "check").strip()
    passed = check.get("passed")
    verdict = "passed" if passed is True else "FAILED"
    detail = str(check.get("detail") or "").strip()
    return f"- {name}: {verdict}" + (f" — {detail}" if detail else "")


def _evidence_line(ref: Mapping[str, Any]) -> str:
    kind = str(ref.get("kind") or "reference").strip()
    reference = str(ref.get("reference") or "").strip()
    description = str(ref.get("description") or "").strip()
    head = f"- `{reference}` ({kind})" if reference else f"- ({kind})"
    return head + (f" — {description}" if description else "")


def _render_result(result: Mapping[str, Any]) -> list[str]:
    capability = str(result.get("capability") or "position").strip()
    agent = str(result.get("agent_name") or "").strip()
    status = str(result.get("status") or "").strip()

    header = f"## {capability}"
    meta = " · ".join(part for part in (agent, status) if part)
    lines = [header]
    if meta:
        lines.append(f"_{meta}_")

    usage = result.get("token_usage")
    if isinstance(usage, Mapping):
        total = int(usage.get("total_tokens") or 0)
        if total > 0:
            lines.append(f"_Token usage: {int(usage.get('input_tokens') or 0):,} input · {int(usage.get('output_tokens') or 0):,} output · {total:,} total_")

    summary = str(result.get("summary") or "").strip()
    if summary:
        lines += ["", summary]

    claims = _bullets(_as_list(result.get("claims")))
    lines += ["", "### Claims"]
    lines += claims if claims else ["_This position recorded no claims._"]

    limitations = _bullets(_as_list(result.get("limitations")))
    if limitations:
        lines += ["", "### Limitations", *limitations]

    checks = [_quality_check_line(c) for c in _as_list(result.get("quality_checks")) if isinstance(c, Mapping)]
    if checks:
        lines += ["", "### Quality checks", *checks]

    actions = _bullets(_as_list(result.get("recommended_next_actions")))
    if actions:
        lines += ["", "### Recommended next actions", *actions]

    evidence = [_evidence_line(e) for e in _as_list(result.get("evidence_refs")) if isinstance(e, Mapping)]
    if evidence:
        lines += ["", "### Evidence", *evidence]

    stop_reason = str(result.get("stop_reason") or "").strip()
    if stop_reason:
        lines += ["", f"**Stopped:** {stop_reason}"]

    return lines


def _render_consensus(results: Sequence[Mapping[str, Any]]) -> list[str]:
    """Where the council converged and where it did not.

    Read off the chair's result rather than passed in separately, so the
    document cannot describe a consensus the recorded chair never reported.
    """
    consensus: Mapping[str, Any] | None = None
    for result in results:
        candidate = result.get("consensus")
        if isinstance(candidate, Mapping):
            consensus = candidate
            break
    if consensus is None:
        return []

    lines = ["", "## Where the meeting landed"]

    if consensus.get("unanimous"):
        # Not an accusation, and not hidden either. Every position and the red
        # team agreeing on everything is possible; it is also what a debate
        # nobody really had looks like, and the reviewer decides which.
        lines += [
            "",
            "> Every position agreed and no disagreement was recorded. That can be right, but it is also what a debate that did not really happen looks like — read the positions below before accepting it.",
        ]

    agreements = [str(item).strip() for item in _as_list(consensus.get("agreements")) if str(item).strip()]
    if agreements:
        lines += ["", "**Agreed**", *_bullets(agreements)]

    disagreements = [item for item in _as_list(consensus.get("disagreements")) if isinstance(item, Mapping)]
    if disagreements:
        lines += ["", "**Disagreed**"]
        for item in disagreements:
            topic = str(item.get("topic") or "").strip()
            positions = [str(value).strip() for value in _as_list(item.get("positions")) if str(value).strip()]
            resolution = str(item.get("resolution") or "").strip()
            lines += ["", f"*{topic}*" if topic else "*Contested*"]
            lines += [f"  - {value}" for value in positions]
            # An unsettled disagreement is named as such rather than left to be
            # inferred from a missing line.
            lines += [f"  - **Resolved:** {resolution}" if resolution else "  - **Not resolved.** This is left for the reviewer to settle."]

    open_questions = [str(item).strip() for item in _as_list(consensus.get("open_questions")) if str(item).strip()]
    if open_questions:
        lines += ["", "**Only the project owner can decide**", *_bullets(open_questions)]

    return lines


def render_review_markdown(
    payload: Mapping[str, Any],
    *,
    data_filename: str,
    data_hash: str,
) -> str:
    """Render one review package. Pure and deterministic."""
    spec_key = str(payload.get("stage_spec_key") or "").strip()
    stage = spec_key.split(":")[1].title() if ":" in spec_key else "Stage"
    cycle_id = str(payload.get("cycle_id") or "").strip()
    revision = payload.get("cycle_db_revision")

    lines: list[str] = [f"# {stage} review package"]

    subtitle_parts = []
    if cycle_id:
        subtitle_parts.append(cycle_id)
    if revision is not None:
        subtitle_parts.append(f"revision {revision}")
    if spec_key:
        subtitle_parts.append(spec_key)
    if subtitle_parts:
        lines.append(" · ".join(subtitle_parts))

    # Stated before any claim, so the reader never mistakes this document for an
    # advanced gate.
    if not payload.get("satisfies_gate", False):
        lines += [
            "",
            "> This package **does not satisfy** the review gate. A person must read it and decide; nothing here advances the cycle on its own.",
        ]

    pilot = payload.get("pilot_review")
    if isinstance(pilot, Mapping):
        lines += [
            "",
            "> **Pilot Design package.** Pre-existing data and execution tools were not required at this depth. Missing inputs remain explicit limitations, and a human may approve this bounded draft to continue to Data reconciliation.",
        ]
        if pilot.get("fallback_used"):
            lines += [
                "> The strict worker-evidence contract was not completed; this package uses the recorded Light-pilot fallback and must not be represented as a full Design review.",
            ]

    authored = str(payload.get("authored_design") or "").strip()
    if authored:
        # Said before the text, not after it. A reader who scrolls into an
        # unattributed design in a file called "review package" will assume a
        # council wrote it, and the empty worker table further down is far too
        # weak a signal to correct that.
        lines += [
            "",
            "## Design",
            "",
            "This design was **written by the project owner**, not decided in a design meeting. No agent was consulted and no worker ran for this attempt.",
            "",
            authored,
        ]

    deliverable_manifest = payload.get("deliverable_manifest")
    if isinstance(deliverable_manifest, Mapping):
        deliverables = [item for item in _as_list(deliverable_manifest.get("deliverables")) if isinstance(item, Mapping)]
        if deliverables:
            lines += ["", "## Deliverables", ""]
            for item in deliverables:
                title = str(item.get("title") or item.get("id") or "Deliverable").strip()
                kind = str(item.get("kind") or "other").strip()
                required = "required" if item.get("required") is True else "optional"
                lines += [f"### {title}", f"`{str(item.get('id') or '').strip()}` · {kind} · {required}"]
                paths = [str(value).strip() for value in _as_list(item.get("expected_paths")) if str(value).strip()]
                if paths:
                    lines.append("- Expected: " + ", ".join(f"`{path}`" for path in paths))
                criteria = [str(value).strip() for value in _as_list(item.get("acceptance_criteria")) if str(value).strip()]
                if criteria:
                    lines.append("- Acceptance: " + "; ".join(criteria))
                validation = str(item.get("validation") or "").strip()
                if validation:
                    lines.append(f"- Test: {validation}")
                lines.append("")

    deliverable_audit = payload.get("deliverable_audit")
    if isinstance(deliverable_audit, Mapping):
        audit_items = [item for item in _as_list(deliverable_audit.get("items")) if isinstance(item, Mapping)]
        if audit_items:
            lines += ["", "## Deliverable audit", ""]
            for item in audit_items:
                item_id = str(item.get("deliverable_id") or "Deliverable").strip()
                verdict = str(item.get("verdict") or "not_testable").strip()
                lines.append(f"- **{item_id}**: {verdict}")
                notes = str(item.get("notes") or "").strip()
                if notes:
                    lines.append(f"  - {notes}")

    results = [r for r in _as_list(payload.get("results")) if isinstance(r, Mapping)]
    rejected = [str(item).strip() for item in _as_list(payload.get("rejected")) if str(item).strip()]
    usage = payload.get("token_usage")
    if isinstance(usage, Mapping) and int(usage.get("total_tokens") or 0) > 0:
        lines += [
            "",
            "## Token usage",
            "",
            f"{int(usage.get('input_tokens') or 0):,} input · {int(usage.get('output_tokens') or 0):,} output · {int(usage.get('total_tokens') or 0):,} total",
        ]

    if rejected:
        # What the council did *not* consider changes how much the rest is worth.
        lines += [
            "",
            "## Work units not included",
            "These were dispatched but excluded from the package:",
            *_bullets(rejected),
        ]

    # Before the positions, not after them. Where the council disagreed is what
    # tells a reader whether the synthesis below is a conclusion or an average,
    # and it is worthless once they have already read the synthesis as settled.
    lines += _render_consensus(results)

    if not results:
        lines += ["", "No positions were recorded for this stage."]
    else:
        for result in _reading_order(results):
            lines += ["", *_render_result(result)]

    lines += [
        "",
        "---",
        "",
        "## Recording a decision",
        DECISION_NOTICE,
        "",
        "## Machine record",
        f"- Structured package: `{data_filename}`",
        f"- SHA-256: `{data_hash}`",
    ]

    return "\n".join(lines).rstrip() + "\n"


#: Bounds on the chat digest. A run summary competes with the conversation for
#: attention, so it states the decision-relevant facts and stops.
_DIGEST_SUMMARY_CHARS = 700
_DIGEST_MAX_FAILED_CHECKS = 4
_DIGEST_MAX_OPEN_QUESTIONS = 2


def _leading_summary(results: Sequence[Mapping[str, Any]]) -> str:
    """The chair synthesis if there is one, else the first position's summary."""
    for result in _reading_order(results):
        summary = str(result.get("summary") or "").strip()
        if summary:
            return summary[:_DIGEST_SUMMARY_CHARS]
    return ""


def _failed_checks(results: Sequence[Mapping[str, Any]]) -> list[str]:
    failures: list[str] = []
    for result in results:
        for check in _as_list(result.get("quality_checks")):
            if not isinstance(check, Mapping) or check.get("passed") is True:
                continue
            name = str(check.get("name") or "check").strip()
            detail = str(check.get("detail") or "").strip()
            failures.append(f"{name}" + (f" ({detail})" if detail else ""))
    return failures[:_DIGEST_MAX_FAILED_CHECKS]


def _consensus_digest(results: Sequence[Mapping[str, Any]]) -> list[str]:
    consensus: Mapping[str, Any] | None = None
    for result in _reading_order(results):
        candidate = result.get("consensus")
        if isinstance(candidate, Mapping):
            consensus = candidate
            break
    if consensus is None:
        return []

    agreements = [str(item).strip() for item in _as_list(consensus.get("agreements")) if str(item).strip()]
    disagreements = [item for item in _as_list(consensus.get("disagreements")) if isinstance(item, Mapping)]
    open_questions = [str(item).strip() for item in _as_list(consensus.get("open_questions")) if str(item).strip()]
    owner_label = "owner decision" if len(open_questions) == 1 else "owner decisions"
    lines = [
        "",
        f"**Council:** {len(agreements)} agreed · {len(disagreements)} contested · {len(open_questions)} {owner_label}",
    ]
    if open_questions:
        lines.append("**Still needs you:** " + "; ".join(open_questions[:_DIGEST_MAX_OPEN_QUESTIONS]))
    return lines


def render_stage_digest(payload: Mapping[str, Any], *, document_path: str) -> str:
    """A short, readable digest of a stage run, for the chat reply.

    The chat should say what the council concluded, not merely where a file
    landed — a reply that is only a link forces the reader to open a file before
    learning anything. This is deliberately a digest and not the package: the
    full document stays on disk and in the review panel, and the digest names
    both so nobody mistakes it for the reviewed record.
    """
    results = [r for r in _as_list(payload.get("results")) if isinstance(r, Mapping)]
    lines: list[str] = []

    summary = _leading_summary(results)
    if summary:
        lines.append(summary)

    lines += _consensus_digest(results)

    failures = _failed_checks(results)
    if failures:
        # A failed check changes the decision, so it is stated in the reply
        # rather than left for whoever opens the document.
        lines += ["", "**Checks that did not pass:** " + "; ".join(failures)]

    rejected = [str(item).strip() for item in _as_list(payload.get("rejected")) if str(item).strip()]
    if rejected:
        lines += ["", f"**Work units not included:** {len(rejected)}"]

    lines += [
        "",
        f"Full review package: `{document_path}` — open the companion HTML review deck and use its Human gate slide to record a decision. This run cannot satisfy the gate.",
    ]
    return "\n".join(lines).strip() + "\n"
