"""Structured-output schema the discovery Lead turn fills.

This is the ``response_format`` bound to the discovery ``lead_agent.ainvoke``
call. The model puts its ordinary conversational reply in
``assistant_response`` alongside the authoritative proposal fields. The
Supervisor reads it from ``structured_response`` and passes it
through :func:`deerflow.dbtl.discovery.normalize_model_discovery_package`, which
closes and bounds it (the schema guides the model, the normaliser is the
authority). The class name must equal
:data:`deerflow.dbtl.discovery.DISCOVERY_PACKAGE_TOOL_NAME` because LangChain
derives the structured tool name from it, and the discovery read-only tool
policy allows that exact name through.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DbtlDiscoveryPackage(BaseModel):
    """A proposed DBTL cycle seed drawn from the conversation so far.

    Only propose one when the conversation contains a concrete objective, at
    least one concrete intended output, and a real reason that explicit
    Design/Build/Test/Learn boundaries reduce risk or improve reproducibility.
    Read-only questions, explanations, and small errands are not cycles.
    """

    assistant_response: str = Field(
        description=(
            "The concise, conversational reply shown to the owner before the DBTL decision card. "
            "Faithfully summarize this same proposal in normal chat prose, explain briefly why DBTL helps, "
            "and ask at most one material unresolved question. Do not claim that a cycle exists."
        ),
    )
    proposed_title: str = Field(description="Short human-readable title for the proposed cycle.")
    objective: str = Field(description="The concrete question, decision, or outcome the cycle would settle.")
    rationale: str = Field(description="Why explicit DBTL boundaries help this specific work. One or two sentences; no generic boilerplate.")
    intended_outputs: list[str] = Field(
        description=("1-10 concrete named artifacts or observable decisions the cycle should produce (e.g. 'fit.py', 'model.json', 'holdout_metrics.json'). Never a placeholder like 'a reviewed result for ...'."),
    )
    known_inputs: list[str] = Field(default_factory=list, description="Inputs the owner has already named (files, datasets, prior results). Empty if none stated.")
    success_criteria: list[str] = Field(default_factory=list, description="Observable criteria that would count the cycle a success. Empty if not material yet; do not invent.")
    rejection_criteria: list[str] = Field(default_factory=list, description="Disconfirming or stop criteria the owner stated. Empty if none.")
    conflicts: list[str] = Field(
        default_factory=list,
        description="Incompatible statements from the conversation, preserved verbatim without choosing a side. Empty if none.",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description=(
            "At most 5 questions that materially change the experimental design, ordered by impact. "
            "Each must name the unresolved decision and the choice it affects. Never restate a field "
            "label ('success criteria?') and never ask something already answered in the conversation. "
            "Omit a field rather than asking about it when it does not change the design."
        ),
    )
    accepted_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Names of fields whose value the owner explicitly stated (from: objective, rationale, "
            "known_inputs, intended_outputs, success_criteria, rejection_criteria). Everything not "
            "listed is treated as your tentative suggestion, not the owner's decision."
        ),
    )
