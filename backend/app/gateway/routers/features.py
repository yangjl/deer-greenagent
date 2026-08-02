"""Read-only feature-flag endpoint for the frontend bootstrap.

Reports which optional, config-gated features are exposed over HTTP so the
frontend can gate UI and avoid firing requests that the backend would reject
with 403. Reads through ``get_config`` so edits to ``config.yaml`` take effect
on the next request without a restart (config hot-reload boundary).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.gateway.browser_capability import browser_capability
from app.gateway.dbtl_readiness import dbtl_mode_reason
from app.gateway.deps import get_config
from deerflow.config.app_config import AppConfig

router = APIRouter(prefix="/api", tags=["features"])


class AgentsApiFeature(BaseModel):
    """Availability of the custom-agent management API."""

    enabled: bool = Field(..., description="Whether the agents_api routes are exposed over HTTP")


class BrowserControlFeature(BaseModel):
    """Availability of live agentic browser control."""

    enabled: bool = Field(..., description="Whether the live browser routes and UI are available")


class AgentActivityFeature(BaseModel):
    """Whether the project rail shows the live runtime-activity block.

    Gates the *view*, not the recording: rows are written either way, so turning
    this off during a rollout costs visibility rather than history.
    """

    enabled: bool = Field(..., description="Whether the rail renders the runtime agent-activity block")
    durable: bool = Field(..., description="Whether activity survives a restart; false on the in-memory run-event backend, where the UI must say Status unavailable rather than guessing idle")


class DbtlFeature(BaseModel):
    """Current project-scoped DBTL operating mode."""

    mode: str
    mutations_enabled: bool
    graph_execution_enabled: bool
    design_deck_feedback: bool
    progressive_gate: bool
    #: False when an approved Design opens Build directly. The UI needs the
    #: rule because stage statuses alone cannot distinguish a stage that is
    #: locked because it was skipped from one locked because it is not reached.
    reconciliation_required: bool
    stage_meetings: dict[str, bool]
    #: Build runs as the five-step workflow rather than one opaque worker. The
    #: UI cannot infer this: a step list and a single stretch of work look the
    #: same until one of them fails halfway.
    build_workflow_steps: bool
    reason: str


class FeaturesResponse(BaseModel):
    """Frontend-facing feature availability flags."""

    agents_api: AgentsApiFeature
    browser_control: BrowserControlFeature
    agent_activity: AgentActivityFeature
    dbtl: DbtlFeature


@router.get(
    "/features",
    response_model=FeaturesResponse,
    summary="List Feature Flags",
    description="Report which optional config-gated features are enabled, so the frontend can gate UI before issuing requests.",
)
async def list_features(config: AppConfig = Depends(get_config)) -> FeaturesResponse:
    """Return availability of optional, config-gated frontend features."""
    browser = browser_capability(config)
    # Read defensively like ``stage_meetings`` below: this endpoint is the
    # frontend's bootstrap, so a partial or stubbed config must degrade to the
    # safe answer rather than 500 the whole page.
    run_events = getattr(config, "run_events", None)
    stage_meetings = getattr(config.dbtl, "stage_meetings", None)
    stage_meeting_flags = stage_meetings.model_dump() if hasattr(stage_meetings, "model_dump") else {"build": False, "test": False, "learn": False}
    return FeaturesResponse(
        agents_api=AgentsApiFeature(enabled=config.agents_api.enabled),
        browser_control=BrowserControlFeature(enabled=browser.available),
        agent_activity=AgentActivityFeature(
            enabled=bool(getattr(run_events, "agent_activity_visibility", False)),
            # ``memory`` is the default development backend and loses everything
            # on restart. The rail must render that as "Status unavailable"
            # rather than as an idle agent, which is a claim it cannot support.
            durable=getattr(run_events, "backend", "memory") != "memory",
        ),
        dbtl=DbtlFeature(
            mode=config.dbtl.mode,
            mutations_enabled=config.dbtl.mutations_enabled,
            graph_execution_enabled=config.dbtl.graph_execution_enabled,
            design_deck_feedback=config.dbtl.design_deck_feedback,
            progressive_gate=config.dbtl.progressive_gate,
            # Defensive like ``stage_meetings`` above: a partial config must not
            # 500 this endpoint, and the safe answer is the strict rule.
            reconciliation_required=bool(getattr(config.dbtl, "reconciliation_required", True)),
            stage_meetings=stage_meeting_flags,
            build_workflow_steps=bool(getattr(config.dbtl, "build_workflow_steps", False)),
            reason=dbtl_mode_reason(config.dbtl),
        ),
    )
