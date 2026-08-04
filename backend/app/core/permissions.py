"""
Permission enforcement middleware for V3 Server.

Simplified from V2: no desktop/web mode distinction.
- Unauthenticated paths (passkey, activation, publish) always pass through.
- All other writes require a valid session cookie + CSRF token.
- Admin endpoints require admin role (enforced by route dependencies).
- Calendar edits require can_edit flag (enforced by route dependencies).

Returns JSONResponse (not raise HTTPException) so outer CORSMiddleware
can still add CORS headers on denied requests.
"""
import hmac

from fastapi import Request
from fastapi.responses import JSONResponse
from app.core.config import settings

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Paths/prefixes that skip CSRF & session checks.
# These either use their own auth (Bearer token) or are unauthenticated flows.
ALWAYS_ALLOWED_WRITE_PATHS = {
    "/api/v1/auth/exchange",
    "/api/v1/passkey/bootstrap/begin",
    "/api/v1/passkey/bootstrap/complete",
    "/api/v1/passkey/bootstrap/recovery/complete",
    "/api/v1/passkey/auth/begin",
    "/api/v1/passkey/auth/complete",
    "/api/v1/activation/validate",
}

ALWAYS_ALLOWED_WRITE_PREFIXES = [
    "/api/v1/publish/",      # Desktop publish (Bearer token auth)
]


def _matches_prefix(path: str, prefixes: list) -> bool:
    for pfx in prefixes:
        stripped = pfx.rstrip("/")
        if path == stripped or path.startswith(stripped + "/"):
            return True
    return False


def _verify_csrf(request: Request) -> bool:
    """X-CSRF-Token header must match the csrf_token cookie."""
    csrf_header = request.headers.get("x-csrf-token", "")
    csrf_cookie = request.cookies.get(settings.CSRF_COOKIE_NAME, "")
    if not csrf_header or not csrf_cookie:
        return False
    return hmac.compare_digest(csrf_header, csrf_cookie)


async def enforce_permissions_middleware(request: Request, call_next):
    """Enforce CSRF on cookie-authenticated write requests.

    Route-level dependencies handle role checks (require_admin, can_edit).
    This middleware only ensures CSRF protection on writes that use cookies.
    """
    if request.method in WRITE_METHODS:
        path = request.url.path

        # Always allow unauthenticated / non-cookie auth paths
        if path in ALWAYS_ALLOWED_WRITE_PATHS:
            return await call_next(request)
        if (
            path in {
                "/api/v1/passkey/register/begin",
                "/api/v1/passkey/register/complete",
            }
            and request.headers.get("x-activation-token")
        ):
            return await call_next(request)
        if _matches_prefix(path, ALWAYS_ALLOWED_WRITE_PREFIXES):
            return await call_next(request)

        # CSRF check for cookie-authenticated writes
        has_session_cookie = settings.SESSION_COOKIE_NAME in request.cookies
        if has_session_cookie and not _verify_csrf(request):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid"},
            )

    return await call_next(request)
