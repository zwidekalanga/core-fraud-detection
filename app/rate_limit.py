"""Shared rate limiter instance.

Extracted from main.py to avoid circular imports when route modules
need to apply per-endpoint rate limits via ``@limiter.limit()``.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def _get_client_ip(request: Request) -> str:
    """Extract the real client IP, respecting X-Forwarded-For behind a proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # First entry is the original client; proxies append their own.
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_get_client_ip, default_limits=["120/minute"])
