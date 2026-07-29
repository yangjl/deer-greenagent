"""Configuration for GreenAgent's project-scoped DBTL workflow."""

from typing import Literal

from pydantic import BaseModel, Field

DbtlMode = Literal["disabled", "audit_only", "manual", "graph_enabled"]


class DbtlConfig(BaseModel):
    """Controls DBTL visibility and mutation boundaries.

    Phase 0 intentionally defaults to ``audit_only``: operators can inspect
    legacy records and readiness, while workflow mutations and LangGraph
    execution remain fail-closed.
    """

    mode: DbtlMode = Field(
        default="audit_only",
        description="DBTL operating mode: disabled, audit_only, manual, or graph_enabled.",
    )
    policy_version: str = Field(
        default="greenagent-dbtl-v2-draft",
        description="Vocabulary/policy contract shown in the readiness report.",
    )
    classifier_shadow_enabled: bool = Field(
        default=True,
        description="Record classifier shadow evaluations. Observation only: an evaluation never creates or advances a cycle.",
    )
    proposals_visible: bool = Field(
        default=False,
        description=(
            "Show the DBTL Upgrade Proposal card to users. Defaults to off so shadow evaluation can run and be measured "
            "before anyone is interrupted by a card — the human exit review approves thresholds and wording before this is turned on."
        ),
    )
    design_deck_feedback: bool = Field(
        default=True,
        description=("Use authenticated Design feedback decks for chair answers and Design review. Set false to restore the visible Design Human Input card and Design stage sheet without deleting surfaces, actions, reviews, or artifacts."),
    )

    setup_draft_model_name: str | None = Field(
        default=None,
        description=(
            "Model used to pre-fill the cycle setup form from the user's request. "
            "null disables drafting and the form opens blank — the behaviour before drafting existed. "
            "Drafted values are proposals only: values the request does not support are marked as assumptions, "
            "and a human confirmation is still what creates the durable record."
        ),
    )

    council_model_name: str | None = Field(
        default=None,
        description=(
            "Default model for design-meeting participants whose seat does not name one. "
            "null keeps the previous behaviour, which is to inherit whatever model the composer is set to — "
            "so a meeting convened from an expensive chat runs every unassigned seat on that expensive model. "
            "Set this to the model a routine meeting should cost; a seat may still name a different one, "
            "and the owner can override any seat on the meeting setup card."
        ),
    )

    @property
    def mutations_enabled(self) -> bool:
        return self.mode in {"manual", "graph_enabled"}

    @property
    def setup_draft_enabled(self) -> bool:
        return bool(self.setup_draft_model_name)

    @property
    def graph_execution_enabled(self) -> bool:
        return self.mode == "graph_enabled"

    @property
    def proposals_enabled(self) -> bool:
        """Whether a card may be shown.

        Two switches, because they answer different questions. Shadow
        evaluation is measurement and is safe to leave on; showing a card
        interrupts someone's work and is gated on both the DBTL mode being at
        least ``manual`` (a proposal the user accepts must be able to become a
        cycle) and an explicit operator opt-in after the exit review.
        """
        return self.proposals_visible and self.mutations_enabled
