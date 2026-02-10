"""Service layer for system configuration management."""

from app.models.config import CONFIG_DEFAULTS
from app.repositories.config_repository import ConfigRepository
from app.schemas.config import ConfigResponse, ConfigUpdateRequest


class ConfigService:
    """Version-agnostic business logic for system configuration."""

    def __init__(self, repo: ConfigRepository):
        self._repo = repo

    @staticmethod
    def _build_response(values: dict[str, str]) -> ConfigResponse:
        return ConfigResponse(
            auto_escalation_threshold=int(
                values.get(
                    "auto_escalation_threshold", CONFIG_DEFAULTS["auto_escalation_threshold"]
                )
            ),
            data_retention_days=int(
                values.get("data_retention_days", CONFIG_DEFAULTS["data_retention_days"])
            ),
        )

    async def get_config(self) -> ConfigResponse:
        values = await self._repo.get_all()
        return self._build_response(values)

    async def update_config(self, body: ConfigUpdateRequest) -> ConfigResponse:
        if body.auto_escalation_threshold is not None:
            await self._repo.upsert(
                "auto_escalation_threshold",
                str(body.auto_escalation_threshold),
                "Risk score above which alerts auto-escalate",
            )

        if body.data_retention_days is not None:
            await self._repo.upsert(
                "data_retention_days",
                str(body.data_retention_days),
                "Days to retain reviewed alerts before archiving",
            )

        values = await self._repo.get_all()
        return self._build_response(values)
