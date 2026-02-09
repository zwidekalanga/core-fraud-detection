"""Application configuration using pydantic-settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
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

    # Notifications
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    notification_email_from: str = "fraud-alerts@capitec.co.za"
    notification_email_to: str = "fraud-team@capitec.co.za"

    # JWT / Auth
    jwt_secret_key: str = "CHANGE-ME-IN-PRODUCTION"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_minutes: int = 1440  # 24 hours

    # Observability
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "core-fraud-detection-service"
    log_level: str = "INFO"

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Ensure database URL uses asyncpg driver."""
        if v and "postgresql://" in v and "asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://")
        return v

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
