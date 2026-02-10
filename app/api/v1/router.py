"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1 import alerts, auth, config, rules

api_router = APIRouter()

# Include routers
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(rules.router, prefix="/rules", tags=["Rules"])
api_router.include_router(alerts.router, prefix="/alerts", tags=["Alerts"])
api_router.include_router(config.router, prefix="/config", tags=["Config"])
