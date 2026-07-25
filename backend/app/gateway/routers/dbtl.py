"""Read-only DBTL readiness endpoints."""

import asyncio

from fastapi import APIRouter, Depends, Request

from app.gateway.authz import require_permission
from app.gateway.dbtl_readiness import DbtlReadinessReport, scan_dbtl_readiness
from app.gateway.deps import get_config
from deerflow.config.app_config import AppConfig

router = APIRouter(prefix="/api/dbtl", tags=["dbtl"])


@router.get("/readiness", response_model=DbtlReadinessReport)
@require_permission("threads", "read")
async def get_dbtl_readiness(request: Request, config: AppConfig = Depends(get_config)) -> DbtlReadinessReport:
    """Return an audit-only inventory rooted beside the active config file."""
    root = AppConfig.resolve_config_path().parent
    return await asyncio.to_thread(scan_dbtl_readiness, root, config.dbtl)
