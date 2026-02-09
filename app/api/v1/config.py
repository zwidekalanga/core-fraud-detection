"""API endpoints for system configuration."""

from fastapi import APIRouter, Depends

from app.auth.dependencies import require_role
from app.dependencies import DBSession
from app.repositories.config_repository import ConfigRepository
from app.schemas.config import ConfigResponse, ConfigUpdateRequest

router = APIRouter()


@router.get(
    "",
    response_model=ConfigResponse,
    dependencies=[Depends(require_role("admin", "analyst", "viewer"))],
)
async def get_config(db: DBSession) -> ConfigResponse:
    """Get all system configuration values."""
    repo = ConfigRepository(db)
    values = await repo.get_all()
    return ConfigResponse(
        auto_escalation_threshold=int(values.get("auto_escalation_threshold", "90")),
        data_retention_days=int(values.get("data_retention_days", "90")),
    )


@router.put(
    "",
    response_model=ConfigResponse,
    dependencies=[Depends(require_role("admin"))],
)
async def update_config(
    body: ConfigUpdateRequest,
    db: DBSession,
) -> ConfigResponse:
    """Update system configuration (admin only). Accepts partial updates."""
    repo = ConfigRepository(db)

    if body.auto_escalation_threshold is not None:
        await repo.upsert(
            "auto_escalation_threshold",
            str(body.auto_escalation_threshold),
            "Risk score above which alerts auto-escalate",
        )

    if body.data_retention_days is not None:
        await repo.upsert(
            "data_retention_days",
            str(body.data_retention_days),
            "Days to retain reviewed alerts before archiving",
        )

    # Return updated config
    values = await repo.get_all()
    return ConfigResponse(
        auto_escalation_threshold=int(values.get("auto_escalation_threshold", "90")),
        data_retention_days=int(values.get("data_retention_days", "90")),
    )
