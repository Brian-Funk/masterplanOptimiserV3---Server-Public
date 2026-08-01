"""Rate limiting configuration for security-sensitive endpoints."""
import hashlib
from collections.abc import Callable

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(key_func=get_remote_address, default_limits=[])

PASSKEY_COARSE_IP_LIMIT = "300/minute"


def _secret_rate_key(scope: str, value: str) -> str:
    """Return a scoped digest without retaining the supplied secret."""
    digest = hashlib.sha256(value.encode()).hexdigest()
    return f"{scope}:{digest}"


def client_ip_rate_key(request: Request) -> str:
    """Return a namespaced rate-limit key for the request's client address."""
    return f"ip:{get_remote_address(request)}"


def passkey_registration_rate_key(request: Request) -> str:
    """Scope passkey registration limits to an activation or account session.

    Activation tokens and session cookies are hashed before being used as
    in-memory limiter keys. Invalid requests without either credential fall
    back to the client address.
    """
    activation_token = request.headers.get("x-activation-token", "")
    if activation_token:
        return _secret_rate_key("passkey-activation", activation_token)

    session_token = request.cookies.get(settings.SESSION_COOKIE_NAME, "")
    if session_token:
        return _secret_rate_key("passkey-session", session_token)

    return client_ip_rate_key(request)


def passkey_session_rate_key(request: Request) -> str:
    """Scope an authenticated passkey limit to its session cookie."""
    session_token = request.cookies.get(settings.SESSION_COOKIE_NAME, "")
    if session_token:
        return _secret_rate_key("passkey-session", session_token)
    return client_ip_rate_key(request)


def runtime_limit(setting_key: str) -> Callable[[], str]:
    """Return a per-request SlowAPI limit provider backed by a runtime setting."""

    def provide_limit() -> str:
        from app.core import runtime_settings
        from app.db.database import SessionLocal

        db = SessionLocal()
        try:
            value = runtime_settings.get_int(setting_key, db)
        finally:
            db.close()
        return f"{value}/minute"

    return provide_limit
