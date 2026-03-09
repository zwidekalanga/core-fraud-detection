"""API endpoints for system configuration."""

from fastapi import APIRouter, Depends, Security

from app.dependencies import get_current_user
from app.providers import ConfigSvc
from app.schemas.config import ConfigResponse, ConfigUpdateRequest
from app.utils.audit import audit_logged

router = APIRouter()


@router.get(
    "",
    response_model=ConfigResponse,
    dependencies=[Security(get_current_user, scopes=["admin", "analyst", "viewer"])],
)
async def get_config(service: ConfigSvc) -> ConfigResponse:
    """Get all system configuration values."""
    return await service.get_config()


@router.put(
    "",
    response_model=ConfigResponse,
    dependencies=[
        Security(get_current_user, scopes=["admin"]),
        Depends(audit_logged("update_config")),
    ],
)
async def update_config(
    body: ConfigUpdateRequest,
    service: ConfigSvc,
) -> ConfigResponse:
    """Update system configuration (admin only). Accepts partial updates."""
    return await service.update_config(body)
