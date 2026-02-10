"""FastAPI application entry point."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi_pagination import add_pagination
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.health import router as health_router
from app.api.v1.router import api_router
from app.config import get_settings
from app.dependencies import create_engine, create_redis, create_session_factory
from app.middleware import RequestIDMiddleware, SecurityHeadersMiddleware
from app.rate_limit import limiter
from app.utils.logging import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager — owns all shared resources."""
    settings = get_settings()
    # Startup
    logger.info("Starting Core Fraud Detection Service...")
    logger.info("Environment: %s", settings.environment)
    logger.info("Debug mode: %s", settings.debug)

    try:
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
    except Exception:
        logger.exception("Failed to initialise database engine")
        raise

    try:
        app.state.redis = create_redis(settings)
    except Exception:
        logger.exception("Failed to initialise Redis client")
        await engine.dispose()
        raise

    yield

    # Shutdown — dispose every resource; ensure both run even if one fails
    logger.info("Shutting down Core Fraud Detection Service...")
    try:
        await app.state.redis.aclose()
    except Exception:
        logger.exception("Error closing Redis connection")
    finally:
        await engine.dispose()
    logger.info("Shutdown complete.")


# ---------------------------------------------------------------------------
# Global exception handler — never leak internals
# ---------------------------------------------------------------------------


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        # Let cancellation propagate — swallowing it breaks graceful shutdown
        if isinstance(exc, asyncio.CancelledError):
            raise
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_application() -> FastAPI:
    """Application factory."""
    settings = get_settings()
    # Setup logging first
    setup_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="Core Fraud Detection Service API",
        description="Real-time core fraud detection service powered by pylitmus rules engine.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Rate limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]  # slowapi typing mismatch
    app.add_middleware(SlowAPIMiddleware)

    # Request ID and security headers (outermost = runs first)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"]
        if settings.is_development
        else ["https://admin.capitec.co.za"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    # Exception handlers
    _register_exception_handlers(app)

    # Include routers
    app.include_router(health_router)
    app.include_router(api_router, prefix="/api/v1")

    add_pagination(app)

    return app


# Create the application instance
app = create_application()
