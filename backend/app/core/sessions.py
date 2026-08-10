"""
Server-side session management  -  ported from MasterplanOptimiserV2 Server.
All users authenticate via session cookies (HttpOnly, Secure, SameSite=Lax).
"""
import hashlib
import hmac
import ipaddress
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core import runtime_settings
from app.models.user import AuthSession


_COARSE_USER_AGENT = re.compile(
    r"^(Firefox|Edge|Chrome|Safari|Browser) on "
    r"(Windows|macOS|iOS|Android|ChromeOS|Linux|Other)$"
)


def _hash_ip(ip: Optional[str]) -> Optional[str]:
    """Pseudonymise a canonical IP with a purpose-specific daily HMAC.

    The key identifier supports deliberate rotation without exposing the key.
    Invalid or non-IP input is not retained.
    """
    if not ip:
        return None
    try:
        canonical_ip = ipaddress.ip_address(ip.strip()).compressed
    except ValueError:
        return None
    if not settings.IP_HMAC_KEY:
        return None
    day_context = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    message = f"mp-opt-ip-v1\x00{day_context}\x00{canonical_ip}".encode("utf-8")
    digest = hmac.new(
        settings.IP_HMAC_KEY.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"v1:{settings.ip_hmac_key_id}:{digest}"


def _compute_fingerprint(user_agent: Optional[str], accept_language: Optional[str]) -> Optional[str]:
    """Compute a session fingerprint from browser characteristics."""
    if not user_agent:
        return None
    raw = f"{user_agent or ''}|{accept_language or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def _coarse_user_agent(user_agent: Optional[str]) -> Optional[str]:
    """Return a bounded browser and operating-system family label.

    Versions, device models and the raw header are deliberately discarded.
    Existing already-coarsened values remain stable.
    """

    if not user_agent:
        return None
    value = user_agent.strip()
    if _COARSE_USER_AGENT.fullmatch(value):
        return value
    if "Edg/" in value or "EdgiOS/" in value or "EdgA/" in value:
        browser = "Edge"
    elif "Firefox/" in value or "FxiOS/" in value:
        browser = "Firefox"
    elif "Chrome/" in value or "CriOS/" in value:
        browser = "Chrome"
    elif "Safari/" in value and "Version/" in value:
        browser = "Safari"
    else:
        browser = "Browser"

    if "Windows" in value:
        operating_system = "Windows"
    elif "CrOS" in value:
        operating_system = "ChromeOS"
    elif "Android" in value:
        operating_system = "Android"
    elif "iPhone" in value or "iPad" in value or "iPod" in value:
        operating_system = "iOS"
    elif "Macintosh" in value or "Mac OS X" in value:
        operating_system = "macOS"
    elif "Linux" in value:
        operating_system = "Linux"
    else:
        operating_system = "Other"
    return f"{browser} on {operating_system}"


def _hash_token(token: str) -> str:
    """One-way hash a session token for storage.

    The raw token is sent to the client as a cookie; only the hash is
    persisted in the database so a DB leak does not expose sessions.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(
    user_id: int,
    db: Session,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    accept_language: Optional[str] = None,
    is_privileged: bool = False,
    reauthenticated: bool = False,
) -> AuthSession:
    """Create a new server-side session."""
    ttl_hours = (
        runtime_settings.get_int("session_ttl_hours_admin", db)
        if is_privileged
        else runtime_settings.get_int("session_ttl_hours", db)
    )
    now = datetime.now(timezone.utc)

    raw_token = secrets.token_urlsafe(48)
    session = AuthSession(
        user_id=user_id,
        session_token=_hash_token(raw_token),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=now + timedelta(hours=ttl_hours),
        last_seen_at=now,
        reauth_at=now if reauthenticated else None,
        ip_address=_hash_ip(ip_address),
        user_agent=_coarse_user_agent(user_agent),
        fingerprint=_compute_fingerprint(user_agent, accept_language),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    # Attach the raw token so callers can set the cookie; the DB only has the hash.
    session._raw_token = raw_token  # type: ignore[attr-defined]
    return session


def validate_session(
    session_token: str,
    db: Session,
    user_agent: Optional[str] = None,
    accept_language: Optional[str] = None,
    *,
    update_last_seen: bool = True,
) -> Optional[AuthSession]:
    """Look up a session token and return it if still valid."""
    token_hash = _hash_token(session_token)
    session = (
        db.query(AuthSession)
        .filter(
            AuthSession.session_token == token_hash,
            AuthSession.revoked_at.is_(None),
        )
        .first()
    )
    if session is None:
        return None

    now = datetime.now(timezone.utc)

    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        return None

    last_seen = session.last_seen_at
    if last_seen is not None:
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        inactivity_limit = last_seen + timedelta(
            minutes=runtime_settings.get_int("session_inactivity_minutes", db)
        )
        if now > inactivity_limit:
            return None

    # Fingerprint validation: reject if stored fingerprint doesn't match
    if session.fingerprint:
        current_fp = _compute_fingerprint(user_agent, accept_language)
        if current_fp is None or not secrets.compare_digest(
            current_fp,
            session.fingerprint,
        ):
            return None

    if update_last_seen:
        session.last_seen_at = now
        db.commit()
    return session


def revoke_session(session_token: str, db: Session) -> bool:
    """Revoke a single session."""
    token_hash = _hash_token(session_token)
    session = (
        db.query(AuthSession)
        .filter(
            AuthSession.session_token == token_hash,
            AuthSession.revoked_at.is_(None),
        )
        .first()
    )
    if session is None:
        return False
    session.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return True


def revoke_all_user_sessions(user_id: int, db: Session) -> int:
    """Revoke every active session for a user."""
    now = datetime.now(timezone.utc)
    count = (
        db.query(AuthSession)
        .filter(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
        .update({"revoked_at": now})
    )
    db.commit()
    return count


def cleanup_expired_sessions(
    db: Session,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> int:
    """Delete sessions that are expired or were revoked beyond retention period."""
    now = now or datetime.now(timezone.utc)

    # Expired sessions past retention window
    expired_cutoff = now - timedelta(
        days=runtime_settings.get_int("retention_expired_sessions_days", db)
    )
    expired = (
        db.query(AuthSession)
        .filter(AuthSession.expires_at < expired_cutoff)
        .delete()
    )

    # Revoked sessions past retention window
    revoked_cutoff = now - timedelta(
        days=runtime_settings.get_int("retention_revoked_sessions_days", db)
    )
    revoked = (
        db.query(AuthSession)
        .filter(
            AuthSession.revoked_at.isnot(None),
            AuthSession.revoked_at < revoked_cutoff,
        )
        .delete()
    )

    if commit:
        db.commit()
    return expired + revoked
