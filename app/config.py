"""Application configuration using pydantic-settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/fraud_engine"
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_pool_size: int = 10

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_consumer_group: str = "core-fraud-detection-consumer"
    kafka_auto_offset_reset: str = "earliest"

    # Celery (RabbitMQ broker, Redis result backend)
    celery_broker_url: str = "amqp://guest:guest@localhost:5672//"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Rules Engine
    scoring_strategy: Literal["sum", "weighted", "max"] = "weighted"
    threshold_approve: int = 30
    threshold_review: int = 70
    rule_cache_ttl: int = 300  # seconds

    # JWT / Auth
    jwt_secret_key: str = Field(
        default="CHANGE-ME-IN-PRODUCTION",
        description="JWT signing secret. MUST be overridden in production.",
    )
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_minutes: int = 1440  # 24 hours

    # Observability
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "core-fraud-detection-service"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Ensure database URL uses asyncpg driver."""
        if v and "postgresql://" in v and "asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://")
        return v

    @model_validator(mode="after")
    def enforce_jwt_secret_strength(self) -> "Settings":
        """Enforce JWT secret requirements based on environment.

        - Non-dev: reject the placeholder secret AND require >= 32 characters.
        - Dev: emit a warning for short secrets so local runs aren't blocked.
        """
        if self.environment != "development":
            if self.jwt_secret_key == "CHANGE-ME-IN-PRODUCTION":
                raise ValueError(
                    "jwt_secret_key must be changed from its default value "
                    "in staging/production environments"
                )
            if len(self.jwt_secret_key) < 32:
                raise ValueError(
                    "jwt_secret_key must be at least 32 characters "
                    "in staging/production environments"
                )
        else:
            if len(self.jwt_secret_key) < 32:
                import warnings

                warnings.warn(
                    "jwt_secret_key is shorter than 32 characters — "
                    "use a strong, randomly-generated secret in production",
                    UserWarning,
                    stacklevel=2,
                )
        return self

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.environment == "development"

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
