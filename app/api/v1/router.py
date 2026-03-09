"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1.endpoints import alerts, config, rules

api_router = APIRouter()

# Include routers
api_router.include_router(rules.router, prefix="/rules", tags=["Rules"])
api_router.include_router(alerts.router, prefix="/alerts", tags=["Alerts"])
api_router.include_router(config.router, prefix="/config", tags=["Config"])
