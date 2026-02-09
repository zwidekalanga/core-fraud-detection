"""Pydantic schemas for authentication."""

from pydantic import BaseModel


class TokenResponse(BaseModel):
    """Response schema for login/refresh endpoints."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    """Request schema for token refresh."""

    refresh_token: str


class TokenUser(BaseModel):
    """Lightweight user representation from JWT claims. No DB query needed."""

    id: str
    username: str
    role: str
    email: str = ""


class UserResponse(BaseModel):
    """Public user information."""

    id: str
    username: str
    email: str
    full_name: str = ""
    role: str
    is_active: bool = True

    model_config = {"from_attributes": True}
