"""Authentication API endpoints.

Token issuance (login/refresh) and user profile (/me) are handled by
core-banking.  This service validates tokens statelessly via shared JWT
secret.  No auth endpoints are exposed here — only the dependency
(``CurrentUser``) is used by other routers for RBAC.
"""

from fastapi import APIRouter

router = APIRouter()
