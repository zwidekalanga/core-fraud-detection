"""Repository for system configuration data access."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import CONFIG_DEFAULTS, SystemConfig


class ConfigRepository:
    """Async data access layer for system configuration."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, key: str) -> str | None:
        """Get a config value by key, returning None if not found."""
        result = await self.session.execute(
            select(SystemConfig.value).where(SystemConfig.key == key)
        )
        row = result.scalar_one_or_none()
        return row if row is not None else CONFIG_DEFAULTS.get(key)

    async def get_int(self, key: str, default: int = 0) -> int:
        """Get a config value as an integer."""
        value = await self.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    async def get_all(self) -> dict[str, str]:
        """Get all config values, filling in defaults for missing keys."""
        result = await self.session.execute(select(SystemConfig.key, SystemConfig.value))
        stored = {row.key: row.value for row in result.all()}
        # Merge defaults for any missing keys
        merged = {**CONFIG_DEFAULTS, **stored}
        return merged

    async def upsert(self, key: str, value: str, description: str | None = None) -> SystemConfig:
        """Insert or update a config value."""
        result = await self.session.execute(select(SystemConfig).where(SystemConfig.key == key))
        config = result.scalar_one_or_none()

        if config is None:
            config = SystemConfig(key=key, value=value, description=description)
            self.session.add(config)
        else:
            config.value = value
            if description is not None:
                config.description = description

        await self.session.flush()
        await self.session.refresh(config)
        return config
