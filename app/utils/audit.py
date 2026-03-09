"""Audit logging for privileged actions (M45)."""

import logging
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request

from app.dependencies import get_current_user
from app.schemas.auth import TokenUser

logger = logging.getLogger("audit")


def audit_logged(action: str) -> Callable[..., Coroutine[Any, Any, None]]:
    """Dependency factory that logs privileged actions.

    Usage::

        @router.post("/rules", dependencies=[Depends(audit_logged("create_rule"))])
    """

    async def _log(request: Request, current_user: Annotated[TokenUser, Depends(get_current_user)]) -> None:
        client_ip = request.client.host if request.client else "unknown"
        request_id = getattr(request.state, "request_id", "n/a")
        logger.info(
            "AUDIT action=%s user=%s role=%s ip=%s request_id=%s path=%s",
            action,
            current_user.username,
            current_user.role,
            client_ip,
            request_id,
            request.url.path,
        )

    return _log
