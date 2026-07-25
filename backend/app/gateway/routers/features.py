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


class DbtlFeature(BaseModel):
    """Current project-scoped DBTL operating mode."""

    mode: str
    mutations_enabled: bool
    graph_execution_enabled: bool
    reason: str


class FeaturesResponse(BaseModel):
    """Frontend-facing feature availability flags."""

    agents_api: AgentsApiFeature
    browser_control: BrowserControlFeature
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
    return FeaturesResponse(
        agents_api=AgentsApiFeature(enabled=config.agents_api.enabled),
        browser_control=BrowserControlFeature(enabled=browser.available),
        dbtl=DbtlFeature(
            mode=config.dbtl.mode,
            mutations_enabled=config.dbtl.mutations_enabled,
            graph_execution_enabled=config.dbtl.graph_execution_enabled,
            reason=dbtl_mode_reason(config.dbtl),
        ),
    )
