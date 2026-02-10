"""FastAPI application entry point."""

import asyncio
import logging
import uuid as _uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bound_contextvars

from app.api.v1.router import api_router
from app.config import get_settings
from app.dependencies import create_engine, create_redis, create_session_factory
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
# H9: Request ID middleware — pure ASGI (no BaseHTTPMiddleware overhead)
# ---------------------------------------------------------------------------


class RequestIDMiddleware:
    """Inject a unique request ID into every request/response cycle."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract or generate request ID from headers
        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode() or str(_uuid.uuid4())
        # Store on scope state so downstream can access it
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = response_headers
            await send(message)

        # Scoped bind: automatically restored on exit, no stale context leaks
        with bound_contextvars(request_id=request_id):
            await self.app(scope, receive, send_with_request_id)


# ---------------------------------------------------------------------------
# H18: Security headers middleware — pure ASGI (no BaseHTTPMiddleware overhead)
# ---------------------------------------------------------------------------

# Headers applied to every response
_SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"x-xss-protection", b"1; mode=block"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"geolocation=(), camera=(), microphone=()"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
]


class SecurityHeadersMiddleware:
    """Add standard security headers to every response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.extend(_SECURITY_HEADERS)
                if get_settings().is_production:
                    response_headers.append(
                        (
                            b"strict-transport-security",
                            b"max-age=63072000; includeSubDomains; preload",
                        )
                    )
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


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

    # H9 + H18: Request ID and security headers (outermost = runs first)
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

    # Include API router
    app.include_router(api_router, prefix="/api/v1")

    # ------------------------------------------------------------------
    # Health check endpoints (no prefix)
    # ------------------------------------------------------------------

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict[str, str]:
        """Liveness check — is the process running?"""
        return {
            "status": "healthy",
            "service": "core-fraud-detection-service",
            "version": "1.0.0",
        }

    @app.get("/ready", tags=["Health"])
    async def readiness_check(request: Request):
        """Readiness check — can the service handle traffic?"""
        checks: dict[str, str] = {}

        # Database
        try:
            async with request.app.state.session_factory() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "unavailable"

        # Redis
        try:
            await request.app.state.redis.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "unavailable"

        all_ok = all(v == "ok" for v in checks.values())
        payload = {
            "status": "ready" if all_ok else "degraded",
            "checks": checks,
        }

        if not all_ok:
            return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)
        return payload

    @app.get("/api/v1/ping", tags=["Health"])
    async def ping() -> dict:
        """Simple ping endpoint for debugging."""
        return {"ping": "pong"}

    return app


# Create the application instance
app = create_application()
