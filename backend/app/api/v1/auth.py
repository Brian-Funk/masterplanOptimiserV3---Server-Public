"""Authentication endpoints  -  session management, exchange, logout."""
import hashlib
import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core import runtime_settings
from app.core.security import (
    get_current_user,
    get_current_user_for_commissioning,
    require_root_admin,
    require_root_recent_reauth,
)
from app.core.commissioning import commissioning_required, commissioning_stage
from app.core.sessions import (
    _coarse_user_agent,
    create_session,
    revoke_session,
    validate_session,
)
from app.core.audit import audit
from app.db.database import get_db
from app.models.user import AuthSession, ExchangeCode, User
from app.core.rate_limit import client_ip_rate_key, limiter, runtime_limit

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ExchangeRequest(BaseModel):
    """Short-lived passkey exchange code submitted after WebAuthn login."""

    code: str = Field(..., min_length=20, max_length=256)


class UserMeResponse(BaseModel):
    """Authenticated user profile returned to the frontend."""

    id: int
    username: str
    display_name: str
    email: Optional[str] = None
    is_root_admin: bool
    is_admin: bool
    is_issuer: bool
    can_edit: bool
    is_active: bool
    is_activated: bool
    linked_person_id: Optional[int] = None
    event_id: Optional[int] = None
    offline_access_ttl_hours: int = 24
    commissioning_required: bool = False
    commissioning_stage: str = "complete"

    model_config = ConfigDict(from_attributes=True)


class ExchangeResponse(BaseModel):
    """Session exchange response returned after successful passkey login."""

    id: int
    username: str
    display_name: str
    is_root_admin: bool
    is_admin: bool
    commissioning_required: bool = False
    commissioning_stage: str = "complete"


class SessionResponse(BaseModel):
    """Minimal active-session metadata visible only to its account owner."""

    id: int
    current: bool
    device: str
    created_at: datetime
    last_seen_at: Optional[datetime] = None
    expires_at: datetime


class SessionRevocationResponse(BaseModel):
    """Result of revoking one account-owned session."""

    revoked: bool
    current: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_session_cookie(
    response: Response,
    session_token: str,
    csrf_token: str,
    expires_at: datetime,
):
    """Set cookies for exactly the server-enforced session lifetime."""
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    max_age = max(0, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
        max_age=max_age,
    )
    response.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
        max_age=max_age,
    )


def _clear_session_cookies(response: Response):
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        secure=settings.COOKIE_SECURE,
        samesite="lax",
    )
    response.delete_cookie(
        key=settings.CSRF_COOKIE_NAME,
        path="/",
        secure=settings.COOKIE_SECURE,
        samesite="lax",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/exchange", response_model=ExchangeResponse)
@limiter.limit(
    runtime_limit("passkey_requests_per_minute"),
    key_func=client_ip_rate_key,
)
def exchange_code_for_session(
    body: ExchangeRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Exchange a short-lived one-time code (from passkey auth) for a session cookie."""
    now = datetime.now(timezone.utc)
    exchange = (
        db.query(ExchangeCode)
        .filter(
            ExchangeCode.code == hashlib.sha256(body.code.encode()).hexdigest(),
            ExchangeCode.used_at.is_(None),
        )
        .first()
    )
    if exchange is None:
        raise HTTPException(status_code=400, detail="Invalid or already-used code")

    expires = exchange.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if now > expires:
        raise HTTPException(status_code=400, detail="Code expired")

    user = db.query(User).filter(User.id == exchange.user_id).first()
    if not user or not user.is_active or (
        not user.is_activated and not user.is_root_admin
    ):
        raise HTTPException(status_code=400, detail="Authentication failed")

    consumed = (
        db.query(ExchangeCode)
        .filter(
            ExchangeCode.id == exchange.id,
            ExchangeCode.used_at.is_(None),
            ExchangeCode.expires_at > now,
        )
        .update({"used_at": now}, synchronize_session=False)
    )
    if consumed != 1:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid or already-used code")
    db.commit()

    is_privileged = user.is_root_admin or user.is_admin or user.is_issuer
    session = create_session(
        user_id=user.id,
        db=db,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        accept_language=request.headers.get("accept-language"),
        is_privileged=is_privileged,
        reauthenticated=True,
    )
    _set_session_cookie(
        response,
        session._raw_token,
        session.csrf_token,
        session.expires_at,
    )

    user.last_login_at = now
    db.commit()

    audit(db, user=user, action="auth.login", request=request)
    db.commit()

    return ExchangeResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_root_admin=user.is_root_admin,
        is_admin=user.is_admin,
        commissioning_required=user.is_root_admin and commissioning_required(db),
        commissioning_stage=commissioning_stage(db) if user.is_root_admin else "complete",
    )


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Revoke the current session and clear auth cookies."""

    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    user = None
    if token:
        try:
            auth_sess = validate_session(
                token,
                db,
                user_agent=request.headers.get("user-agent"),
                accept_language=request.headers.get("accept-language"),
            )
            if auth_sess:
                user = db.query(User).filter(User.id == auth_sess.user_id).first()
        except Exception:
            user = None

        revoke_session(token, db)

    if user:
        audit(db, user=user, action="auth.logout", request=request)
        db.commit()

    _clear_session_cookies(response)
    response.headers["Cache-Control"] = "no-store"
    return {"message": "Logged out"}


@router.get("/me", response_model=UserMeResponse)
def get_me(
    current_user: User = Depends(get_current_user_for_commissioning),
    db: Session = Depends(get_db),
):
    """Return the currently authenticated user."""

    return UserMeResponse(
        id=current_user.id,
        username=current_user.username,
        display_name=current_user.display_name,
        email=current_user.email,
        is_root_admin=current_user.is_root_admin,
        is_admin=current_user.is_admin,
        is_issuer=current_user.is_issuer,
        can_edit=current_user.can_edit,
        is_active=current_user.is_active,
        is_activated=current_user.is_activated,
        linked_person_id=current_user.linked_person_id,
        event_id=current_user.event_id,
        offline_access_ttl_hours=runtime_settings.get_int(
            "offline_access_ttl_hours",
            db,
        ),
        commissioning_required=(current_user.is_root_admin and commissioning_required(db)),
        commissioning_stage=(commissioning_stage(db) if current_user.is_root_admin else "complete"),
    )


@router.get("/root-access")
def root_access(current_user: User = Depends(require_root_admin)):
    """Authorise HTTP delivery of a root-only frontend route."""

    return {"status": "ok"}


@router.get("/recovery-key-access")
def recovery_key_access(
    current_user: User = Depends(require_root_recent_reauth),
):
    """Unlock the browser-local recovery-key generator after root WebAuthn."""

    return {"status": "ok"}


@router.get("/sessions", response_model=List[SessionResponse])
def list_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List non-expired sessions for the current account only."""

    current_session = getattr(current_user, "_auth_session", None)
    if current_session is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    now = datetime.now(timezone.utc)
    sessions = (
        db.query(AuthSession)
        .filter(
            AuthSession.user_id == current_user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
        .order_by(AuthSession.created_at.desc(), AuthSession.id.desc())
        .all()
    )
    return [
        SessionResponse(
            id=session.id,
            current=session.id == current_session.id,
            device=_coarse_user_agent(session.user_agent) or "Browser on Other",
            created_at=session.created_at,
            last_seen_at=session.last_seen_at,
            expires_at=session.expires_at,
        )
        for session in sessions
    ]


@router.delete("/sessions/{session_id}", response_model=SessionRevocationResponse)
def revoke_owned_session(
    session_id: int,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Revoke one active session owned by the current account."""

    current_session = getattr(current_user, "_auth_session", None)
    if current_session is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    target = (
        db.query(AuthSession)
        .filter(
            AuthSession.id == session_id,
            AuthSession.user_id == current_user.id,
            AuthSession.revoked_at.is_(None),
        )
        .first()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Active session not found")
    is_current = target.id == current_session.id
    target.revoked_at = datetime.now(timezone.utc)
    audit(
        db,
        user=current_user,
        action="auth.session_revoke",
        detail=json.dumps({"target": "current" if is_current else "other"}),
        request=request,
    )
    db.commit()
    if is_current:
        _clear_session_cookies(response)
    response.headers["Cache-Control"] = "no-store"
    return SessionRevocationResponse(revoked=True, current=is_current)
