"""Authentication API endpoints.

Token issuance (login/refresh) is handled by core-banking.
This service validates tokens statelessly via shared JWT secret.
Only the /me endpoint remains here for token introspection.
"""

from fastapi import APIRouter, Request

from app.auth.dependencies import CurrentUser
from app.rate_limit import limiter
from app.schemas.auth import UserResponse

router = APIRouter()


@router.get("/me", response_model=UserResponse)
@limiter.limit("30/minute")
async def get_current_user_info(request: Request, current_user: CurrentUser) -> UserResponse:
    """Return the authenticated user's profile from JWT claims."""
    return UserResponse.model_validate(current_user)
