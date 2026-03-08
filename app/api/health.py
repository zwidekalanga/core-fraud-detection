"""Health check endpoints."""

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Liveness check — is the process running?"""
    return {
        "status": "healthy",
        "service": "core-fraud-detection-service",
        "version": "1.0.0",
    }


@router.get("/ready", response_model=None)
async def readiness_check(request: Request) -> dict[str, str | dict[str, str]] | JSONResponse:
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


@router.get("/api/v1/ping")
async def ping() -> dict[str, str]:
    """Simple ping endpoint for debugging."""
    return {"ping": "pong"}
