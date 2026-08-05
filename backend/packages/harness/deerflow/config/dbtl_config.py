"""Configuration for GreenAgent's project-scoped DBTL workflow."""

from typing import Literal

from pydantic import BaseModel, Field

DbtlMode = Literal["disabled", "audit_only", "manual", "graph_enabled"]


class DbtlStageMeetingsConfig(BaseModel):
    """Independent rollout switches for post-evidence review meetings."""

    build: bool = False
    test: bool = False
    learn: bool = False

    def enabled_for(self, stage: str) -> bool:
        return bool(getattr(self, (stage or "").strip().lower(), False))


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
    conversational_discovery: bool = Field(
        default=False,
        description=("Route explicit new-cycle requests into a durable, read-only conversation before cycle creation. This interaction switch does not grant workflow authority and requires graph_enabled."),
    )
    discovery_global_memory: bool = Field(
        default=False,
        description="Allow separately labelled user-global memory retrieval during conversational discovery.",
    )
    discovery_project_history: bool = Field(
        default=False,
        description="Allow bounded authorized prior-thread summaries during conversational discovery.",
    )
    discovery_classifier_entry: bool = Field(
        default=False,
        description=("Route classifier suggestions into conversational discovery after deterministic routing and suppression checks. Explicit cycle-start requests remain controlled by conversational_discovery."),
    )
    discovery_auto_offer: bool = Field(
        default=False,
        description=("Automatically append a start card when classifier-entered discovery becomes ready. Explicitly requested discovery may always show its review card."),
    )
    design_deck_feedback: bool = Field(
        default=True,
        description=("Use authenticated Design feedback decks for chair answers and Design review. Set false to restore the visible Design Human Input card and Design stage sheet without deleting surfaces, actions, reviews, or artifacts."),
    )

    reconciliation_required: bool = Field(
        default=True,
        description=(
            "Gate Build on a settled Data Reconciliation matrix. Default true. Set false and an approved Design opens "
            "Build directly, while the reconciliation stage, its endpoints, and its matrix remain available for projects "
            "that use them. The data guarantees are not dropped: Build still binds the content hashes of the datasets it "
            "ran on and still refuses to record lineage when none are declared or a raw source is not declared immutable. "
            "What is given up is the human-settled judgement matrix — contradictory sources, trait direction, exclusions, "
            "leakage, and train/test separation are no longer required to be adjudicated before Build."
        ),
    )

    build_workflow_steps: bool = Field(
        default=False,
        description=(
            "Execute Build as the versioned five-step workflow (read the approved Design, plan, run the phases, "
            "summarize, render the deck) instead of one opaque worker. On, Build resolves and hash-verifies the "
            "approved Design before dispatching anything, runs each planned phase as its own attempt whose outputs are "
            "published and hashed before it is recorded as succeeded, replays a committed step instead of re-running it, "
            "and produces its review package through a read-only summarizer that may describe the evidence but never "
            "restate it — so a presentational failure cannot discard sandbox work. A plan that stops at a failed phase "
            "or a pause boundary keeps its finished phases and writes no review package, because a fraction of the "
            "planned work is not the build a person would be approving. Off keeps today's monolithic Build path exactly "
            "as it was, including not re-reading the Design — deliberately, because the resolution is a new refusal and "
            "a project whose approved package is not readable through the project root must discover that in the manual "
            "profile rather than mid-experiment. A paused Build raises a bound control in chat — confirm the plan, continue "
            "past a phase boundary, or choose Retry / Replan / Restart / Hold after a failure — and every option states "
            "what it costs. Retrying one step from a button in the read model is deliberately not offered: the card owns "
            "that decision so it stays durable in the conversation."
        ),
    )

    build_implementer_agent: str | None = Field(
        default=None,
        description=(
            "Agent that implements Build phases whose capability has no registered specialist. null keeps the "
            "existing behaviour, which selects the registered generalist and records the stand-in. This is an "
            "efficiency dial and never a correctness one: the server-issued path grant, the entry-point refusal, "
            "and the server's own execution of that entry point hold whichever agent runs, because the observed "
            "failure crossed both the specialist and the generalist. A name that is not a registered agent is "
            "ignored with a warning rather than failing the Build."
        ),
    )

    build_plan_confirmation: bool = Field(
        default=False,
        description=(
            "Show the recorded Build plan as a card — Start the build, Change the plan, Hold here — before any phase is "
            "dispatched. Redirecting a build here costs a sentence; redirecting it afterwards costs the run. 'Change the "
            "plan' carries the person's words verbatim into a replan rather than paraphrasing them into a planner-owned "
            "decision, and nothing is preselected. Off runs the recorded plan immediately, which is the behaviour before "
            "this existed. Requires dbtl.build_workflow_steps, since a Build with no recorded plan has nothing to confirm."
        ),
    )

    build_work_meetings: bool = Field(
        default=False,
        description=(
            "Offer a bounded Build work meeting when a Build step pauses on a question one exchange cannot settle. The "
            "meeting reads the recorded evidence and proposes options; it is advisory and can never resume the Build by "
            "itself — only the person's bound answer does that. Off leaves the pause answerable directly, which is the "
            "cheaper interaction and the right one for most questions."
        ),
    )

    conditional_test: bool = Field(
        default=False,
        description=(
            "Let a person decide whether a Build result is worth qualifying for retention. On, an approved Build offers "
            "two routes instead of one: 'Keep and validate' opens Test as it does today, and 'Learn from this exploration' "
            "opens Learn directly with Test recorded as explicitly skipped. Off keeps today's mandatory Design → Build → "
            "Test → Learn path exactly as it was. "
            "Skipping is never silent success: the skip is a durable, human-owned record bound to the Build evidence it "
            "was taken against, the Test stage reports 'skipped' rather than 'locked' or 'approved', and the resulting "
            "Learn synthesis is marked unvalidated — knowledge promotion and cross-project publication are refused at the "
            "persistence boundary for a claim that never passed retention qualification. What is given up is the "
            "guarantee that every recorded cycle carries a validity assessment; what is gained is that an exploratory "
            "pilot no longer has to fail a predictive contract it never claimed to satisfy."
        ),
    )

    progressive_gate: bool = Field(
        default=False,
        description=(
            "Enable the progressive gate: the stage-transition path strip, per-transition difficulty "
            "assessment, route-chooser deck, one-click routine gate, and park/resume behavior. Off restores the uniform "
            "four-stage display. Transition records accumulate either way, so toggling this never creates an audit gap."
        ),
    )
    transition_assessor_model_name: str | None = Field(
        default=None,
        description=("Model used for the nostream assessment of work remaining at a stage boundary. null, an unavailable model, or malformed output fails safely to the standard review path."),
    )
    stage_meetings: DbtlStageMeetingsConfig = Field(
        default_factory=DbtlStageMeetingsConfig,
        description=("Per-stage post-evidence meeting rollout. Build, Test, and Learn default off and can be enabled independently."),
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
    def conversational_discovery_enabled(self) -> bool:
        return self.graph_execution_enabled and self.conversational_discovery

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
