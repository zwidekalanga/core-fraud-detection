"""Health check endpoints."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/ping")
async def ping() -> dict:
    """Simple ping endpoint for debugging."""
    return {"ping": "pong"}
