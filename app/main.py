"""FastAPI application entry point."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import get_settings
from app.utils.logging import setup_logging

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup/shutdown events."""
    # Startup
    logger.info("Starting Core Fraud Detection Service...")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"Debug mode: {settings.debug}")

    # TODO: Initialize database connection pool
    # TODO: Initialize Redis connection
    # TODO: Initialize Kafka producer

    yield

    # Shutdown
    logger.info("Shutting down Core Fraud Detection Service...")
    # TODO: Close database connections
    # TODO: Close Redis connections
    # TODO: Close Kafka producer


def create_application() -> FastAPI:
    """Application factory."""
    # Setup logging first
    setup_logging(settings.log_level)

    app = FastAPI(
        title="Core Fraud Detection Service API",
        description="Real-time core fraud detection service powered by pylitmus rules engine.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"]
        if settings.is_development
        else ["https://admin.capitec.co.za"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API router
    app.include_router(api_router, prefix="/api/v1")

    # Health check endpoints (no prefix)
    @app.get("/health", tags=["Health"])
    async def health_check() -> dict[str, str]:
        """Liveness check - is the service running?"""
        return {
            "status": "healthy",
            "service": "core-fraud-detection-service",
            "version": "1.0.0",
        }

    @app.get("/ready", tags=["Health"])
    async def readiness_check() -> dict[str, str | dict[str, str]]:
        """Readiness check - is the service ready to accept traffic?"""
        # TODO: Check database connectivity
        # TODO: Check Redis connectivity
        # TODO: Check Kafka connectivity

        checks = {
            "database": "ok",  # TODO: Implement actual check
            "redis": "ok",  # TODO: Implement actual check
            "kafka": "ok",  # TODO: Implement actual check
        }

        all_ok = all(v == "ok" for v in checks.values())

        return {
            "status": "ready" if all_ok else "degraded",
            "checks": checks,
        }

    return app


# Create the application instance
app = create_application()
