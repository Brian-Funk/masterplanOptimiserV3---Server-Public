"""
Security helpers  -  session auth, current user dependency.
Passkey-only: no password hashing needed for regular auth.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.sessions import validate_session
from app.core import runtime_settings
from app.core.commissioning import commissioning_required, commissioning_stage
from app.db.database import get_db
from app.models.event import Event
from app.models.user import User
from app.core.tenancy import TENANCY_HOSTED, apply_membership_projection, tenancy_mode
from app.core.database_tenancy import (
    authenticated_subject_context,
    authenticated_user_context,
)


# Sentinel value for root admin (no password  -  passkey only)
PASSKEY_ONLY_HASH = "!passkey-only"


def _get_session_token_from_request(request: Request) -> Optional[str]:
    """Extract session token from the HttpOnly cookie."""
    return request.cookies.get(settings.SESSION_COOKIE_NAME)


def _get_current_user(
    request: Request,
    db: Session,
    *,
    update_last_seen: bool,
    allow_commissioning: bool = False,
) -> User:
    """Resolve an authenticated user with optional session activity tracking."""
    session_token = _get_session_token_from_request(request)
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    auth_session = validate_session(
        session_token,
        db,
        user_agent=request.headers.get("user-agent"),
        accept_language=request.headers.get("accept-language"),
        update_last_seen=update_last_seen,
    )
    if auth_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )

    authenticated_subject_context(db, auth_session.user_id)
    user = db.query(User).filter(User.id == auth_session.user_id).first()
    if (
        user is None
        or not user.is_active
        or (
            not user.is_activated
            and not (allow_commissioning and user.is_root_admin)
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found, inactive, or not activated",
        )
    if user.is_root_admin:
        authenticated_user_context(
            db,
            user_id=user.id,
            event_id=None,
            controller_id=None,
            is_root=True,
        )
    else:
        # The legacy event column is only a bootstrap projection used to set a
        # restrictive context before EventMembership itself is loaded.
        authenticated_user_context(
            db,
            user_id=user.id,
            event_id=user.event_id,
            controller_id=None,
            is_root=False,
        )
        event = db.get(Event, user.event_id) if user.event_id is not None else None
        authenticated_user_context(
            db,
            user_id=user.id,
            event_id=user.event_id,
            controller_id=event.controller_id if event is not None else None,
            is_root=False,
        )
    context = apply_membership_projection(db, user)
    if (
        not user.is_root_admin
        and context.event_id is None
        and tenancy_mode(db) == TENANCY_HOSTED
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account has no active event membership",
        )
    user._auth_session = auth_session  # type: ignore[attr-defined]
    # The access log uses the random evidence reference rather than a database
    # identifier or username. It remains pseudonymous and follows log retention.
    request.state.subject_ref = user.evidence_subject_id
    if user.is_root_admin and not allow_commissioning and commissioning_required(db):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                "code": "ROOT_COMMISSIONING_REQUIRED",
                "commissioning_stage": commissioning_stage(db),
                "setup_url": "/setup",
                "message": "Complete root commissioning before using administration.",
            },
        )
    return user


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """Get the current authenticated user and refresh session activity."""

    return _get_current_user(request, db, update_last_seen=True)


def get_current_user_read_only(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """Authenticate without writing session activity to a fenced database."""

    return _get_current_user(request, db, update_last_seen=False)


def get_current_user_for_commissioning(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """Authenticate a root session without lifting the commissioning fence."""

    return _get_current_user(
        request,
        db,
        update_last_seen=True,
        allow_commissioning=True,
    )


def require_commissioning_root(
    current_user: User = Depends(get_current_user_for_commissioning),
) -> User:
    """Require the authenticated root while the setup wizard is active."""
    if not current_user.is_root_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Root administrator access required")
    return current_user


def require_commissioning_root_recent_reauth(
    current_user: User = Depends(require_commissioning_root),
    db: Session = Depends(get_db),
) -> User:
    """Require a setup root whose current session has a recent passkey proof."""
    return ensure_recent_reauth(current_user, db)


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency: require that the current user is an admin or root admin."""
    if not current_user.is_root_admin and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


def require_admin_or_issuer(current_user: User = Depends(get_current_user)) -> User:
    """Dependency: require admin, root admin, or issuer."""
    if not current_user.is_root_admin and not current_user.is_admin and not current_user.is_issuer:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or issuer access required",
        )
    return current_user


def require_root_or_issuer(current_user: User = Depends(get_current_user)) -> User:
    """Dependency: require a root administrator or an issuer account."""
    if not current_user.is_root_admin and not current_user.is_issuer:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Root administrator or issuer access required",
        )
    return current_user


def _is_issuer_only(user: User) -> bool:
    """True when the user is an issuer but NOT a full admin or root."""
    return user.is_issuer and not user.is_admin and not user.is_root_admin


def require_same_event(target_user: User, current_user: User) -> None:
    """Require exact event equality for every non-root account."""

    if current_user.is_root_admin:
        return
    if current_user.event_id is None or target_user.event_id != current_user.event_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )


def require_user_management_access(target_user: User, current_user: User) -> None:
    """Enforce account hierarchy and issuer event scope for user management.

    Only root may manage another root, global administrator, or issuer account.
    Issuers may manage ordinary users only within their own event.
    """
    # Resolve the tenant boundary before the account hierarchy. Otherwise a
    # foreign root/admin/issuer identifier would return 403 while a foreign
    # ordinary identifier returns 404, leaking the target's existence/role.
    if not current_user.is_root_admin:
        require_same_event(target_user, current_user)
    if target_user.is_root_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot manage the root admin account",
        )
    if (
        target_user.is_admin or target_user.is_issuer
    ) and not current_user.is_root_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only root admin can manage privileged accounts",
        )


def require_event_access(event_id: int, current_user: User, db: Session) -> Event:
    """Return an event when the current user may access it.

    Root may access every event. All non-root roles, including event admins,
    must have the exact active event membership projected onto the user.
    """
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    if current_user.is_root_admin:
        return event
    if current_user.event_id is None or current_user.event_id != event_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )
    return event


def require_root_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency: require root admin."""
    if not current_user.is_root_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Root admin access required",
        )
    return current_user


def require_root_admin_read_only(
    current_user: User = Depends(get_current_user_read_only),
) -> User:
    """Require root access without mutating session state."""

    if not current_user.is_root_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Root admin access required",
        )
    return current_user


def ensure_recent_reauth(current_user: User, db: Session) -> User:
    """Require a recent passkey verification on the current session."""
    auth_session = getattr(current_user, "_auth_session", None)
    if auth_session is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Re-authentication required",
        )
    reauth_at = auth_session.reauth_at
    if reauth_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Re-authentication required",
        )
    if reauth_at.tzinfo is None:
        reauth_at = reauth_at.replace(tzinfo=timezone.utc)
    window = runtime_settings.get_int("reauth_window_minutes", db)
    if datetime.now(timezone.utc) > reauth_at + timedelta(minutes=window):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Re-authentication required",
        )
    return current_user


def require_recent_reauth(
    current_user: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
) -> User:
    """Require a recently re-authenticated global admin or issuer."""
    return ensure_recent_reauth(current_user, db)


def require_admin_recent_reauth(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> User:
    """Require a recently re-authenticated root or global admin."""
    return ensure_recent_reauth(current_user, db)


def require_root_recent_reauth(
    current_user: User = Depends(require_root_admin),
    db: Session = Depends(get_db),
) -> User:
    """Dependency: require root admin with recent re-authentication."""
    return ensure_recent_reauth(current_user, db)


def create_default_admin(db: Session) -> User:
    """Create root admin user (passkey-only) if it doesn't exist."""
    root = db.query(User).filter(User.is_root_admin == True).first()
    if not root:
        root = User(
            username="root.admin",
            display_name="Root Administrator",
            email="root-admin",
            is_root_admin=True,
            is_admin=True,
            is_activated=True,
        )
        db.add(root)
        db.commit()
        db.refresh(root)
        print(f"[Startup] Created root admin (passkey-only): {root.username}")
    return root
