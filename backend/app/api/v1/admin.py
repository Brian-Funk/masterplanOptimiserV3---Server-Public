"""
Admin endpoints  -  event CRUD (with secret generation) and user management.
"""
import hashlib
import io
import json
import logging
import re
import secrets
import unicodedata
import zipfile
import os
from pathlib import Path
import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    verify_authentication_response,
)
from webauthn.helpers import base64url_to_bytes, options_to_json
from webauthn.helpers.structs import UserVerificationRequirement

from app.core.activation import (
    ADDITIONAL_PASSKEY,
    CREDENTIAL_RESET,
    INITIAL_SETUP,
    ActivationPurpose,
    ActivationDeliveryInProgressError,
    ManagedPasskeyPurpose,
    create_activation_link,
    resolve_activation_purpose,
    validate_activation_token,
)
from app.core.activation_email import (
    ActivationMailError,
    ActivationMailer,
    activation_url as absolute_activation_url,
    build_activation_message,
    build_test_message,
    normalise_recipient,
    recover_stale_deliveries,
    render_activation_qr_png,
    safe_mail_settings,
)
from app.core.audit import audit
from app.core.config import settings
from app.core.ha_replication import protect_current_state, request_ha_replication
from app.core.retention import materialise_event_purge_deadline, retention_status
from app.core.governance import current_policy_identity, require_current_policy_identity
from app.core.web_edits import (
    derive_web_edit_summary,
    revert_web_edit,
    revert_web_edits,
)
from app.core.security import (
    get_current_user,
    get_current_user_for_commissioning,
    require_admin,
    require_admin_or_issuer,
    require_recent_reauth,
    require_admin_recent_reauth,
    require_root_admin,
    require_root_admin_read_only,
    require_root_recent_reauth,
    require_same_event,
    require_user_management_access,
    ensure_recent_reauth,
    _is_issuer_only,
)
from app.core import runtime_settings
from app.core.rate_limit import (
    PASSKEY_COARSE_IP_LIMIT,
    client_ip_rate_key,
    limiter,
    passkey_session_rate_key,
    runtime_limit,
)
from app.core.sessions import revoke_all_user_sessions
from app.db.database import get_db
from app.api.v1.passkey import CeremonyCompletion, _credential_id, _verify_user_handle
from app.core.passkey_ceremonies import (
    REAUTHENTICATION,
    consume_ceremony,
    create_ceremony,
)
from app.models.event import Event
from app.models.audit import AuditLog
from app.models.deletion import DeletionCase
from app.models.governance import DataPolicyAcknowledgement, GovernancePublication
from app.models.notification import Announcement, PushSubscription, ScheduleChange
from app.models.published import PublishedPerson, PublishedTask, TaskEdit
from app.models.public_schedule_link import PublicScheduleLink
from app.models.user import (
    ActivationEmailDelivery, ActivationLink, AuthSession, ExchangeCode,
    PasskeyChallenge, PasskeyCeremony, User, WebAuthnCredential,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _ensure_aware_utc(value: datetime | None) -> datetime | None:
    """Return a timezone-aware UTC datetime for database values."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Re-authentication for destructive operations
# ---------------------------------------------------------------------------


@router.post("/reauth/begin")
@limiter.limit(PASSKEY_COARSE_IP_LIMIT, key_func=client_ip_rate_key)
@limiter.limit(
    runtime_limit("passkey_requests_per_minute"),
    key_func=passkey_session_rate_key,
)
def reauth_begin(
    request: Request,
    admin: User = Depends(get_current_user_for_commissioning),
    db: Session = Depends(get_db),
):
    """Start a passkey re-authentication challenge for the current account."""
    options = generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        user_verification=UserVerificationRequirement.REQUIRED,
    )

    auth_session = getattr(admin, "_auth_session", None)
    if auth_session is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    entry = create_ceremony(
        options.challenge,
        REAUTHENTICATION,
        db,
        user_id=admin.id,
        session_id=auth_session.id,
        ttl_minutes=runtime_settings.get_int("reauth_challenge_ttl_minutes", db),
    )

    return {"options": options_to_json(options), "ceremony_id": entry.id}


@router.post("/reauth/complete")
@limiter.limit(PASSKEY_COARSE_IP_LIMIT, key_func=client_ip_rate_key)
@limiter.limit(
    runtime_limit("passkey_requests_per_minute"),
    key_func=passkey_session_rate_key,
)
def reauth_complete(
    body: CeremonyCompletion,
    request: Request,
    admin: User = Depends(get_current_user_for_commissioning),
    db: Session = Depends(get_db),
):
    """Verify passkey re-authentication and mark the current session."""
    auth_session = getattr(admin, "_auth_session", None)
    if auth_session is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    ceremony = consume_ceremony(
        body.ceremony_id,
        REAUTHENTICATION,
        db,
        user_id=admin.id,
        session_id=auth_session.id,
    )
    try:
        credential_id_bytes = _credential_id(body.credential)
    except HTTPException as exc:
        audit(
            db,
            user=admin,
            action="auth.reauth_failed",
            request=request,
            outcome="denied",
        )
        db.commit()
        raise HTTPException(status_code=400, detail="Re-authentication failed") from exc
    stored_cred = (
        db.query(WebAuthnCredential)
        .filter(
            WebAuthnCredential.credential_id == credential_id_bytes,
            WebAuthnCredential.user_id == admin.id,
        )
        .with_for_update()
        .first()
    )
    if stored_cred is None:
        audit(
            db,
            user=admin,
            action="auth.reauth_failed",
            request=request,
            outcome="denied",
        )
        db.commit()
        raise HTTPException(status_code=400, detail="Re-authentication failed")

    try:
        _verify_user_handle(body.credential, admin.id)
        verification = verify_authentication_response(
            credential=body.credential,
            expected_challenge=base64url_to_bytes(ceremony.challenge),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            credential_public_key=stored_cred.public_key,
            credential_current_sign_count=stored_cred.sign_count,
            require_user_verification=True,
        )
    except Exception as e:
        logger.warning(
            "Re-authentication failed for uid=%s (%s)",
            admin.id,
            type(e).__name__,
        )
        audit(
            db,
            user=admin,
            action="auth.reauth_failed",
            request=request,
            outcome="denied",
        )
        db.commit()
        raise HTTPException(status_code=400, detail="Re-authentication failed")

    now = datetime.now(timezone.utc)
    stored_cred.sign_count = verification.new_sign_count
    stored_cred.last_used_at = now

    auth_session.reauth_at = now
    audit(db, user=admin, action="auth.reauth", request=request)
    db.commit()

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class EventCreateIn(BaseModel):
    """Admin payload for creating a server event."""

    name: str = Field(..., min_length=1, max_length=128)
    evidence_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    )
    location: Optional[str] = Field(None, max_length=256)
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None
    policy_version: Optional[int] = Field(None, ge=1)
    policy_sha256: Optional[str] = Field(None, pattern=r"^[0-9a-f]{64}$")


class EventOut(BaseModel):
    """Event record returned to administration screens."""

    id: int
    evidence_id: str
    name: str
    location: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: str
    purge_grace_days: Optional[int] = None
    purge_due_at: Optional[datetime] = None
    purge_case_request_id: Optional[str] = None
    purge_started_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    secret_created_at: Optional[datetime] = None
    secret_age_days: Optional[int] = None
    logo_color_1: Optional[str] = None
    logo_color_2: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class EventCreateResponse(BaseModel):
    """Event creation response that includes the one-time publish secret."""

    event: EventOut
    publish_secret: str  # Raw secret  -  shown ONCE


class WebEditItemOut(BaseModel):
    """One committed web edit shown in the operations review list."""

    task_id: int
    task_name: str
    day: Optional[str] = None
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    location: Optional[str] = None
    edited_at: Optional[datetime] = None
    edited_by: Optional[str] = None
    edited_by_user_id: Optional[int] = None
    change_summary: List[str] = Field(default_factory=list)
    original_summary: str
    current_summary: str


class WebEditSummaryOut(BaseModel):
    """Event-level web-edit confidence state for admins and issuers."""

    level: str
    edited_task_count: int
    last_edited_at: Optional[datetime] = None
    last_edited_by: Optional[str] = None
    has_published_baseline: bool
    headline: str
    description: str
    items: List[WebEditItemOut] = Field(default_factory=list)


class RevertWebEditRequest(BaseModel):
    """Bulk revert request for committed server web edits."""

    task_ids: Optional[List[int]] = None
    revert_all: bool = False


class RevertWebEditResultOut(BaseModel):
    """Result returned after reverting one or more web edits."""

    success: bool
    reverted_count: int
    remaining_web_edit_count: int
    message: str
    task_id: Optional[int] = None


class UserCreateIn(BaseModel):
    """Admin payload for creating an event-scoped user."""

    username: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    email: Optional[EmailStr] = None
    event_id: Optional[int] = None
    is_admin: bool = False
    is_issuer: bool = False
    can_edit: bool = False
    tags: List[str] = Field(default_factory=list, max_length=100)


class BulkUserCreateRowIn(BaseModel):
    """One editable row in a bulk user creation request."""

    username: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    email: Optional[EmailStr] = None
    can_edit: bool = False
    tags: List[str] = Field(default_factory=list, max_length=100)


class BulkUserCreateIn(BaseModel):
    """Event-scoped request for creating multiple ordinary users at once."""

    event_id: Optional[int] = None
    bulk_tags: List[str] = Field(default_factory=list, max_length=100)
    users: List[BulkUserCreateRowIn] = Field(..., min_length=1, max_length=200)


class UserOut(BaseModel):
    """User record returned to administration screens."""

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
    has_activation_link: bool = False
    last_activation_link_created_at: Optional[datetime] = None
    last_activation_at: Optional[datetime] = None
    activation_email_status: Optional[
        Literal["sending", "accepted", "failed", "unknown", "not_attempted"]
    ] = None
    activation_email_attempted_at: Optional[datetime] = None
    activation_email_accepted_at: Optional[datetime] = None
    activation_email_error_code: Optional[str] = None
    activation_email_error_message: Optional[str] = None
    activation_email_purpose: Optional[
        Literal["initial_setup", "additional_passkey", "credential_reset"]
    ] = None
    has_valid_email: bool = False
    linked_person_id: Optional[int] = None
    event_id: Optional[int] = None
    tags: Optional[List[str]] = None
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    deletion_requested_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class UserCreateResponse(BaseModel):
    """User creation response with the one-time activation URL."""

    user: UserOut
    activation_url: str  # One-time link for passkey setup
    expires_at: datetime


class BulkUserCreateError(BaseModel):
    """Row-level failure returned from bulk user creation."""

    index: int
    username: Optional[str] = None
    field: str
    message: str


class BulkUserCreateResponse(BaseModel):
    """Partial success response for bulk user creation."""

    created: List[UserOut]
    errors: List[BulkUserCreateError]


def _activation_link_metadata(
    user_ids: list[int],
    db: Session,
) -> dict[int, dict[str, object]]:
    """Return non-secret activation campaign metadata for users."""
    meta: dict[int, dict[str, object]] = {
        user_id: {
            "has_activation_link": False,
            "last_activation_link_created_at": None,
            "last_activation_at": None,
            "activation_email_status": None,
            "activation_email_attempted_at": None,
            "activation_email_accepted_at": None,
            "activation_email_error_code": None,
            "activation_email_error_message": None,
            "activation_email_purpose": None,
        }
        for user_id in user_ids
    }
    if not user_ids:
        return meta

    now = datetime.now(timezone.utc)
    links = (
        db.query(ActivationLink)
        .filter(ActivationLink.user_id.in_(user_ids))
        .all()
    )
    for link in links:
        item = meta.setdefault(
            link.user_id,
            {
                "has_activation_link": False,
                "last_activation_link_created_at": None,
                "last_activation_at": None,
                "activation_email_status": None,
                "activation_email_attempted_at": None,
                "activation_email_accepted_at": None,
                "activation_email_error_code": None,
                "activation_email_error_message": None,
                "activation_email_purpose": None,
            },
        )
        created_at = _ensure_aware_utc(link.created_at)
        used_at = _ensure_aware_utc(link.used_at)
        expires_at = _ensure_aware_utc(link.expires_at)
        invalidated_at = _ensure_aware_utc(link.invalidated_at)

        if created_at and (
            item["last_activation_link_created_at"] is None
            or created_at > item["last_activation_link_created_at"]
        ):
            item["last_activation_link_created_at"] = created_at
        if used_at and (
            item["last_activation_at"] is None
            or used_at > item["last_activation_at"]
        ):
            item["last_activation_at"] = used_at
        if (
            used_at is None
            and invalidated_at is None
            and not link.delivery_pending
            and (expires_at is None or expires_at >= now)
        ):
            item["has_activation_link"] = True

    deliveries = (
        db.query(ActivationEmailDelivery)
        .filter(ActivationEmailDelivery.user_id.in_(user_ids))
        .order_by(
            ActivationEmailDelivery.user_id.asc(),
            ActivationEmailDelivery.started_at.asc(),
            ActivationEmailDelivery.id.asc(),
        )
        .all()
    )
    for delivery in deliveries:
        item = meta[delivery.user_id]
        item["activation_email_status"] = delivery.status
        item["activation_email_attempted_at"] = _ensure_aware_utc(delivery.started_at)
        item["activation_email_accepted_at"] = (
            _ensure_aware_utc(delivery.completed_at)
            if delivery.status == "accepted"
            else None
        )
        item["activation_email_error_code"] = delivery.error_code
        item["activation_email_error_message"] = delivery.error_message
        item["activation_email_purpose"] = delivery.purpose
    return meta


def _has_valid_email(value: str | None) -> bool:
    """Return whether an existing user email is safe to use for delivery."""

    try:
        normalise_recipient(value)
        return True
    except ActivationMailError:
        return False


def _direct_removal_blockers(db: Session, user: User) -> list[str]:
    """Explain why an account needs the signed deletion workflow.

    Direct removal is deliberately limited to an unused invitation.  Records
    created solely to deliver that invitation are not operational history and
    are removed with it.
    """

    blockers: list[str] = []
    if user.is_root_admin or user.is_admin or user.is_issuer or user.can_edit:
        blockers.append("privileged_account")
    if user.is_activated:
        blockers.append("activated_account")
    if user.last_login_at is not None:
        blockers.append("login_history")
    if user.linked_person_id is not None:
        blockers.append("published_person_link")

    history_checks = (
        (WebAuthnCredential, WebAuthnCredential.user_id, "passkey_history"),
        (AuthSession, AuthSession.user_id, "session_history"),
        (TaskEdit, TaskEdit.edited_by_user_id, "schedule_edit_history"),
        (Announcement, Announcement.created_by_id, "announcement_history"),
        (PublicScheduleLink, PublicScheduleLink.created_by_id, "public_link_history"),
        (GovernancePublication, GovernancePublication.published_by_id, "governance_history"),
        (DataPolicyAcknowledgement, DataPolicyAcknowledgement.user_id, "policy_history"),
        (PushSubscription, PushSubscription.user_id, "notification_history"),
        (ScheduleChange, ScheduleChange.user_id, "schedule_history"),
        (AuditLog, AuditLog.user_id, "audit_actor_history"),
        (DeletionCase, DeletionCase.user_id, "deletion_workflow_exists"),
    )
    for model, field, code in history_checks:
        if db.query(model).filter(field == user.id).first() is not None:
            blockers.append(code)
    return blockers


def _user_out(user: User) -> UserOut:
    """Serialise a user record for administration responses."""

    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        is_root_admin=user.is_root_admin,
        is_admin=user.is_admin,
        is_issuer=user.is_issuer,
        can_edit=user.can_edit,
        is_active=user.is_active,
        is_activated=user.is_activated,
        has_valid_email=_has_valid_email(user.email),
        linked_person_id=user.linked_person_id,
        event_id=user.event_id,
        tags=user.tags or [],
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        deletion_requested_at=user.deletion_requested_at,
    )


def _normalise_tags(*tag_groups: list[str]) -> list[str]:
    """Return unique, trimmed tags in user-entered order."""

    tags: list[str] = []
    seen: set[str] = set()
    for group in tag_groups:
        for raw_tag in group:
            tag = str(raw_tag).strip()[:100]
            if not tag or tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)
    return tags[:100]


class UserUpdateIn(BaseModel):
    """Partial update payload for an existing user."""

    display_name: Optional[str] = Field(None, max_length=128)
    email: Optional[EmailStr] = None
    is_admin: Optional[bool] = None
    is_issuer: Optional[bool] = None
    can_edit: Optional[bool] = None
    is_active: Optional[bool] = None
    linked_person_id: Optional[int] = Field(None, gt=0)
    event_id: Optional[int] = Field(None, gt=0)
    tags: Optional[List[str]] = Field(None, max_length=100)


class UserTagActionIn(BaseModel):
    """One atomic, explicitly scoped user-tag operation."""

    action: Literal["add", "remove", "rename", "delete"]
    tag: str = Field(..., min_length=1, max_length=100)
    replacement: Optional[str] = Field(None, min_length=1, max_length=100)
    user_ids: Optional[List[int]] = Field(None, min_length=1, max_length=1000)
    event_id: Optional[int] = Field(None, gt=0)

    @field_validator("tag", "replacement")
    @classmethod
    def clean_tag(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Tags may not be empty")
        return cleaned

    @field_validator("user_ids")
    @classmethod
    def unique_user_ids(cls, value: Optional[List[int]]) -> Optional[List[int]]:
        if value is None:
            return None
        if len(value) != len(set(value)) or any(user_id <= 0 for user_id in value):
            raise ValueError("User IDs must be unique positive integers")
        return value

    @model_validator(mode="after")
    def validate_scope(self) -> "UserTagActionIn":
        if self.action in {"add", "remove"}:
            if not self.user_ids or self.event_id is not None or self.replacement is not None:
                raise ValueError("Add/remove requires only an explicit user_ids selection")
        elif self.action == "rename":
            if self.event_id is None or not self.replacement or self.user_ids is not None:
                raise ValueError("Rename requires event_id and replacement")
            if self.replacement == self.tag:
                raise ValueError("The replacement tag must be different")
        elif self.event_id is None or self.user_ids is not None or self.replacement is not None:
            raise ValueError("Delete requires only event_id")
        return self


class UserTagActionOut(BaseModel):
    """Public result of an atomic tag operation."""

    action: str
    affected_user_ids: List[int]
    affected_count: int


# ---------------------------------------------------------------------------
# Event endpoints
# ---------------------------------------------------------------------------

@router.post("/events", response_model=EventCreateResponse)
@limiter.limit("20/minute")
def create_event(
    request: Request,
    body: EventCreateIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new event. Returns the publish secret ONCE."""
    policy_identity = current_policy_identity(db)
    if policy_identity is not None:
        if body.policy_version is None or body.policy_sha256 is None:
            raise HTTPException(
                status_code=428,
                detail={
                    "code": "data_policy_acknowledgement_required",
                    "policy_version": policy_identity[0],
                    "policy_sha256": policy_identity[1],
                    "message": "Review and acknowledge the current exact permitted-data policy before creating an event.",
                },
            )
        policy_version, policy_sha256 = require_current_policy_identity(
            body.policy_version, body.policy_sha256, db
        )
    raw_secret = secrets.token_urlsafe(48)
    secret_hash = hashlib.sha256(raw_secret.encode()).hexdigest()

    event = Event(
        evidence_id=body.evidence_id,
        name=body.name,
        location=body.location,
        start_date=datetime.strptime(body.start_date, "%Y-%m-%d").date() if body.start_date else None,
        end_date=datetime.strptime(body.end_date, "%Y-%m-%d").date() if body.end_date else None,
        status="draft",
        publish_secret_hash=secret_hash,
        secret_created_at=datetime.now(timezone.utc),
    )
    db.add(event)
    db.flush()
    materialise_event_purge_deadline(event, db)
    if policy_identity is not None:
        db.add(DataPolicyAcknowledgement(
            user_id=admin.id,
            event_id=event.id,
            policy_version=policy_version,
            policy_sha256=policy_sha256,
            scope="event_creator",
        ))
    db.commit()
    db.refresh(event)

    audit(db, user=admin, action="event.create", resource_type="event",
          resource_id=event.id, request=request)
    db.commit()

    protection = protect_current_state("publisher-secret-create")
    if not protection.protected:
        db.delete(event)
        db.commit()
        request_ha_replication("publisher-secret-create-rollback")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "standby_protection_failed",
                "message": "The event was not created because its publisher token could not be protected on the standby.",
            },
        )

    return EventCreateResponse(
        event=EventOut(
            id=event.id,
            evidence_id=event.evidence_id,
            name=event.name,
            location=event.location,
            start_date=event.start_date.isoformat() if event.start_date else None,
            end_date=event.end_date.isoformat() if event.end_date else None,
            status=event.status,
            purge_grace_days=event.purge_grace_days,
            purge_due_at=event.purge_due_at,
            purge_case_request_id=event.purge_case_request_id,
            purge_started_at=event.purge_started_at,
            created_at=event.created_at,
            secret_created_at=event.secret_created_at,
            secret_age_days=0,
            logo_color_1=None,
            logo_color_2=None,
        ),
        publish_secret=raw_secret,
    )


@router.get("/events", response_model=List[EventOut])
def list_events(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all server events for administrators."""

    events = db.query(Event).order_by(Event.created_at.desc()).all()
    now = datetime.now(timezone.utc)
    return [
        EventOut(
            id=e.id,
            evidence_id=e.evidence_id,
            name=e.name,
            location=e.location,
            start_date=e.start_date.isoformat() if e.start_date else None,
            end_date=e.end_date.isoformat() if e.end_date else None,
            status=e.status,
            purge_grace_days=e.purge_grace_days,
            purge_due_at=e.purge_due_at,
            purge_case_request_id=e.purge_case_request_id,
            purge_started_at=e.purge_started_at,
            created_at=e.created_at,
            secret_created_at=e.secret_created_at,
            secret_age_days=(now - _ensure_aware_utc(e.secret_created_at)).days if e.secret_created_at else None,
            logo_color_1=None,
            logo_color_2=None,
        )
        for e in events
    ]


def _web_edit_audit_entries(
    event_id: int,
    db: Session,
    task_ids: list[int] | None = None,
) -> list[dict[str, object]]:
    """Return only field names and references, never edited task content."""
    selected = set(task_ids) if task_ids is not None else None
    change_codes = {
        "Created on web": "created",
        "Deleted from live schedule": "deleted",
        "Name changed": "title",
        "Time changed": "time",
        "Location changed": "location",
        "People changed": "people",
        "Assignments changed": "assignments",
        "Fields changed": "fields",
        "Attachments changed": "attachments",
        "Text changed": "text",
        "Colour changed": "colour",
        "Edited on the web": "other",
    }
    entries = []
    for item in derive_web_edit_summary(event_id, db).items:
        if selected is not None and item.task_id not in selected:
            continue
        entries.append(
            {
                "task_id": item.task_id,
                "change_codes": sorted(change_codes[value] for value in item.change_summary),
            }
        )
    return entries


@router.get("/events/{event_id}/web-edits", response_model=WebEditSummaryOut)
def get_event_web_edits(
    event_id: int,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Return compact web-edit confidence state for one event."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if _is_issuer_only(admin) and admin.event_id != event_id:
        raise HTTPException(status_code=403, detail="No access to this event")

    summary = derive_web_edit_summary(event_id, db)
    return WebEditSummaryOut(
        level=summary.level,
        edited_task_count=summary.edited_task_count,
        last_edited_at=summary.last_edited_at,
        last_edited_by=summary.last_edited_by,
        has_published_baseline=summary.has_published_baseline,
        headline=summary.headline,
        description=summary.description,
        items=[WebEditItemOut(**item.__dict__) for item in summary.items],
    )


@router.post(
    "/events/{event_id}/web-edits/{task_id}/revert",
    response_model=RevertWebEditResultOut,
)
def revert_event_web_edit(
    event_id: int,
    task_id: int,
    request: Request,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Revert one committed web edit for an event."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if _is_issuer_only(admin) and admin.event_id != event_id:
        raise HTTPException(status_code=403, detail="No access to this event")

    audit_entries = _web_edit_audit_entries(event_id, db, [task_id])
    try:
        task_name, remaining = revert_web_edit(event_id, task_id, db)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    audit(
        db,
        user=admin,
        action="web_edit.revert",
        resource_type="published_task",
        resource_id=task_id,
        detail=json.dumps(
            {
                "event_id": event_id,
                "task_id": task_id,
                "tasks": audit_entries,
            }
        ),
        request=request,
    )
    db.commit()
    return RevertWebEditResultOut(
        success=True,
        reverted_count=1,
        remaining_web_edit_count=remaining,
        message=f"{task_name} reverted to the published version.",
        task_id=task_id,
    )


@router.post(
    "/events/{event_id}/web-edits/revert",
    response_model=RevertWebEditResultOut,
)
def revert_event_web_edits(
    event_id: int,
    body: RevertWebEditRequest,
    request: Request,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Revert selected or all committed web edits for an event."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if _is_issuer_only(admin) and admin.event_id != event_id:
        raise HTTPException(status_code=403, detail="No access to this event")
    if not body.revert_all and not body.task_ids:
        raise HTTPException(status_code=400, detail="Select at least one web edit to revert")

    audit_entries = _web_edit_audit_entries(
        event_id,
        db,
        None if body.revert_all else body.task_ids,
    )
    reverted, remaining = revert_web_edits(
        event_id,
        db,
        task_ids=body.task_ids,
        revert_all=body.revert_all,
    )
    audit(
        db,
        user=admin,
        action="web_edit.revert_bulk",
        resource_type="event",
        resource_id=event_id,
        detail=json.dumps(
            {
                "event_id": event_id,
                "task_ids": body.task_ids,
                "revert_all": body.revert_all,
                "reverted_count": reverted,
                "tasks": audit_entries,
            }
        ),
        request=request,
    )
    db.commit()
    return RevertWebEditResultOut(
        success=True,
        reverted_count=reverted,
        remaining_web_edit_count=remaining,
        message=f"{reverted} web edit{'s' if reverted != 1 else ''} reverted.",
    )


@router.post("/events/{event_id}/regenerate-secret")
def regenerate_event_secret(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    """Regenerate the publish secret for an event. Returns the new secret ONCE."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.purge_case_request_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EVENT_PURGE_IN_PROGRESS",
                "message": "The publish credential is reserved for the pending Desktop deletion report.",
            },
        )

    previous_hash = event.publish_secret_hash
    previous_created_at = event.secret_created_at
    raw_secret = secrets.token_urlsafe(48)
    event.publish_secret_hash = hashlib.sha256(raw_secret.encode()).hexdigest()
    event.secret_created_at = datetime.now(timezone.utc)
    audit(db, user=admin, action="event.regenerate_secret", resource_type="event",
          resource_id=event.id, request=request)
    db.commit()

    protection = protect_current_state("publisher-secret-rotation")
    if not protection.protected:
        event.publish_secret_hash = previous_hash
        event.secret_created_at = previous_created_at
        db.commit()
        request_ha_replication("publisher-secret-rotation-rollback")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "standby_protection_failed",
                "message": "The publisher token was not rotated because the standby did not accept the protected state.",
            },
        )

    return {"publish_secret": raw_secret}


# ---------------------------------------------------------------------------
# User endpoints
# ---------------------------------------------------------------------------

@router.post("/users", response_model=UserCreateResponse)
@limiter.limit("20/minute")
def create_user(
    request: Request,
    body: UserCreateIn,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Create a new user and return a one-time activation URL."""
    # Issuer scoping: force own event, block privilege escalation
    if _is_issuer_only(admin):
        body.event_id = admin.event_id
        if body.is_admin or body.is_issuer:
            raise HTTPException(status_code=403, detail="Issuers cannot grant admin or issuer roles")
        body.is_issuer = False

    if body.is_admin and not admin.is_root_admin:
        raise HTTPException(status_code=403, detail="Only root admin can grant admin role")
    if body.is_admin or body.is_issuer:
        ensure_recent_reauth(admin, db)

    # Only root can set is_issuer
    if body.is_issuer and not admin.is_root_admin:
        raise HTTPException(status_code=403, detail="Only root admin can grant issuer role")

    # Check username uniqueness
    existing = db.query(User).filter(User.username == body.username).first()
    if existing:
        raise HTTPException(status_code=409, detail="Username already taken")

    # Root may prepare an ordinary account before deciding its event. Other
    # operators remain event-scoped, and privileged roles must always have an
    # event so they cannot acquire ambiguous global access.
    if not body.event_id:
        if not admin.is_root_admin:
            raise HTTPException(status_code=422, detail="event_id is required")
        if body.is_admin or body.is_issuer:
            raise HTTPException(status_code=422, detail="Privileged users require an event")
    else:
        event = db.query(Event).filter(Event.id == body.event_id).first()
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

    user = User(
        username=body.username,
        display_name=body.display_name,
        email=str(body.email) if body.email else None,
        event_id=body.event_id,
        is_admin=body.is_admin,
        is_issuer=body.is_issuer,
        can_edit=body.can_edit,
        is_active=True,
        is_activated=False,
        tags=_normalise_tags(body.tags),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    audit(db, user=admin, action="user.create", resource_type="user",
          resource_id=user.id, request=request)

    # Create activation link
    raw_token, _link = create_activation_link(
        user_id=user.id,
        created_by_id=admin.id,
        db=db,
    )
    db.commit()

    activation_url = f"/activate#token={raw_token}"

    return UserCreateResponse(
        user=_user_out(user),
        activation_url=activation_url,
        expires_at=_ensure_aware_utc(_link.expires_at),
    )


@router.post("/users/bulk", response_model=BulkUserCreateResponse)
@limiter.limit("20/minute")
def bulk_create_users(
    request: Request,
    body: BulkUserCreateIn,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Create multiple ordinary users for one event, returning row errors."""

    event_id = body.event_id
    if _is_issuer_only(admin):
        event_id = admin.event_id

    if not event_id:
        raise HTTPException(status_code=422, detail="event_id is required")

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    created: list[UserOut] = []
    errors: list[BulkUserCreateError] = []
    seen_usernames: set[str] = set()
    existing_usernames = {
        username
        for (username,) in db.query(User.username)
        .filter(User.username.in_([row.username for row in body.users]))
        .all()
    }

    for index, row in enumerate(body.users):
        username = row.username.strip()
        display_name = row.display_name.strip()
        if not username:
            errors.append(BulkUserCreateError(
                index=index, username=row.username, field="username",
                message="Username is required",
            ))
            continue
        if not display_name:
            errors.append(BulkUserCreateError(
                index=index, username=username, field="display_name",
                message="Display name is required",
            ))
            continue
        if username in seen_usernames:
            errors.append(BulkUserCreateError(
                index=index, username=username, field="username",
                message="Username is duplicated in this batch",
            ))
            continue
        seen_usernames.add(username)
        if username in existing_usernames:
            errors.append(BulkUserCreateError(
                index=index, username=username, field="username",
                message="Username already taken",
            ))
            continue

        user = User(
            username=username,
            display_name=display_name,
            email=str(row.email) if row.email else None,
            event_id=event_id,
            is_admin=False,
            is_issuer=False,
            can_edit=row.can_edit,
            is_active=True,
            is_activated=False,
            tags=_normalise_tags(body.bulk_tags, row.tags),
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            errors.append(BulkUserCreateError(
                index=index, username=username, field="username",
                message="Username already taken",
            ))
            existing_usernames.add(username)
            continue
        db.refresh(user)
        existing_usernames.add(username)
        created.append(_user_out(user))

    if created:
        audit(
            db,
            user=admin,
            action="user.create_bulk",
            resource_type="user",
            detail=json.dumps({
                "event_id": event_id,
                "created_user_ids": [user.id for user in created],
                "error_count": len(errors),
            }),
            request=request,
        )
        db.commit()

    return BulkUserCreateResponse(created=created, errors=errors)


@router.get("/users", response_model=List[UserOut])
def list_users(
    event_id: Optional[int] = None,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """List users visible to the current admin or issuer."""

    query = db.query(User).filter(User.is_root_admin == False)
    # Issuer scoping: force filter to own event
    if _is_issuer_only(admin):
        query = query.filter(
            User.event_id == admin.event_id,
            User.is_admin == False,  # noqa: E712
            User.is_issuer == False,  # noqa: E712
        )
    elif event_id is not None:
        query = query.filter(User.event_id == event_id)
    users = query.order_by(User.created_at.desc()).all()
    link_meta = _activation_link_metadata([u.id for u in users], db)

    return [
        UserOut(
            id=u.id,
            username=u.username,
            display_name=u.display_name,
            email=u.email,
            is_root_admin=u.is_root_admin,
            is_admin=u.is_admin,
            is_issuer=u.is_issuer,
            can_edit=u.can_edit,
            is_active=u.is_active,
            is_activated=u.is_activated,
            has_activation_link=bool(link_meta.get(u.id, {}).get("has_activation_link")),
            last_activation_link_created_at=link_meta.get(u.id, {}).get("last_activation_link_created_at"),
            last_activation_at=link_meta.get(u.id, {}).get("last_activation_at"),
            activation_email_status=link_meta.get(u.id, {}).get("activation_email_status"),
            activation_email_attempted_at=link_meta.get(u.id, {}).get("activation_email_attempted_at"),
            activation_email_accepted_at=link_meta.get(u.id, {}).get("activation_email_accepted_at"),
            activation_email_error_code=link_meta.get(u.id, {}).get("activation_email_error_code"),
            activation_email_error_message=link_meta.get(u.id, {}).get("activation_email_error_message"),
            activation_email_purpose=link_meta.get(u.id, {}).get("activation_email_purpose"),
            has_valid_email=_has_valid_email(u.email),
            linked_person_id=u.linked_person_id,
            event_id=u.event_id,
            tags=u.tags or [],
            last_login_at=u.last_login_at,
            created_at=u.created_at,
            deletion_requested_at=u.deletion_requested_at,
        )
        for u in users
    ]


@router.put("/user-tags/actions", response_model=UserTagActionOut)
def apply_user_tag_action(
    body: UserTagActionIn,
    request: Request,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Apply one atomic tag action without issuing per-user requests."""

    query = db.query(User).filter(User.is_root_admin == False)  # noqa: E712
    if body.action in {"add", "remove"}:
        query = query.filter(User.id.in_(body.user_ids or []))
    else:
        event_id = admin.event_id if _is_issuer_only(admin) else body.event_id
        if _is_issuer_only(admin) and body.event_id != admin.event_id:
            raise HTTPException(status_code=403, detail="Issuers may only manage their own event")
        query = query.filter(User.event_id == event_id)

    targets = query.order_by(User.id.asc()).all()
    if body.action in {"add", "remove"} and len(targets) != len(body.user_ids or []):
        raise HTTPException(status_code=404, detail="One or more selected users were not found")
    for target in targets:
        require_user_management_access(target, admin)

    affected: list[int] = []
    for target in targets:
        current = _normalise_tags(target.tags or [])
        if body.action == "add":
            updated = _normalise_tags(current, [body.tag])
        elif body.action in {"remove", "delete"}:
            updated = [tag for tag in current if tag != body.tag]
        else:
            updated = _normalise_tags(
                [body.replacement if tag == body.tag else tag for tag in current]
            )
        if updated != current:
            target.tags = updated
            affected.append(target.id)

    audit(
        db,
        user=admin,
        action="user.tags_bulk_update",
        resource_type="user",
        detail=json.dumps({
            "operation": body.action,
            "event_id": body.event_id,
            "selected_count": len(body.user_ids or []),
            "affected_count": len(affected),
        }),
        request=request,
    )
    db.commit()
    return UserTagActionOut(
        action=body.action,
        affected_user_ids=affected,
        affected_count=len(affected),
    )


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdateIn,
    request: Request,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Update a user while enforcing issuer and event boundaries."""

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    require_user_management_access(user, admin)
    if not user.is_active:
        raise HTTPException(status_code=409, detail="Cannot modify a deactivated user")

    # Issuer scoping
    if _is_issuer_only(admin):
        # Block privilege escalation and event reassignment
        if body.is_admin is not None:
            raise HTTPException(status_code=403, detail="Issuers cannot change admin status")
        if body.is_issuer is not None:
            raise HTTPException(status_code=403, detail="Issuers cannot change issuer status")
        if "event_id" in body.model_fields_set and body.event_id != admin.event_id:
            raise HTTPException(status_code=403, detail="Issuers cannot reassign users to other events")

    # Only a recently re-authenticated root may change global roles.
    if body.is_admin is not None and not admin.is_root_admin:
        raise HTTPException(status_code=403, detail="Only root admin can change admin status")
    if body.is_issuer is not None and not admin.is_root_admin:
        raise HTTPException(status_code=403, detail="Only root admin can change issuer status")
    if body.is_admin is not None or body.is_issuer is not None:
        ensure_recent_reauth(admin, db)

    if body.is_active is not None and body.is_active != user.is_active:
        ensure_recent_reauth(admin, db)

    event_field_supplied = "event_id" in body.model_fields_set
    if event_field_supplied and body.event_id is None:
        if not admin.is_root_admin:
            raise HTTPException(status_code=403, detail="Only root admin can unassign users")
        if user.is_admin or user.is_issuer:
            raise HTTPException(status_code=409, detail="Privileged users require an event")
    if event_field_supplied and body.event_id is not None:
        event = db.query(Event).filter(Event.id == body.event_id).first()
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")

    event_changed = event_field_supplied and body.event_id != user.event_id
    if event_changed:
        ensure_recent_reauth(admin, db)
    effective_event_id = body.event_id if event_field_supplied else user.event_id
    linked_person = None
    if body.linked_person_id is not None:
        linked_person = (
            db.query(PublishedPerson)
            .filter(
                PublishedPerson.event_id == effective_event_id,
                PublishedPerson.external_person_id == body.linked_person_id,
            )
            .first()
        )
        if linked_person is None:
            raise HTTPException(status_code=404, detail="Person not found in this event")

    changed_fields = [
        field
        for field, value in body.model_dump(exclude_unset=True).items()
        if value != getattr(user, field)
    ]
    if (
        event_changed
        and "linked_person_id" not in body.model_fields_set
        and user.linked_person_id is not None
    ):
        changed_fields.append("linked_person_id")

    if body.display_name is not None:
        user.display_name = body.display_name
    if "email" in body.model_fields_set:
        user.email = str(body.email) if body.email else None
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.is_issuer is not None:
        user.is_issuer = body.is_issuer
    if body.can_edit is not None:
        user.can_edit = body.can_edit
    if body.is_active is not None:
        user.is_active = body.is_active
    if "linked_person_id" in body.model_fields_set:
        user.linked_person_id = body.linked_person_id
        if linked_person is not None:
            user.evidence_subject_id = linked_person.evidence_subject_id
    if event_field_supplied:
        user.event_id = body.event_id
        if event_changed and "linked_person_id" not in body.model_fields_set:
            user.linked_person_id = None
    if body.tags is not None:
        user.tags = [str(tag)[:100] for tag in body.tags[:100]]

    audit(
        db,
        user=admin,
        action="user.update",
        resource_type="user",
        resource_id=user.id,
        detail=json.dumps({"changed_fields": changed_fields}),
        request=request,
    )
    db.commit()
    db.refresh(user)

    if any(
        field in changed_fields
        for field in {"is_admin", "is_issuer", "can_edit", "is_active", "event_id"}
    ):
        revoke_all_user_sessions(user.id, db)

    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        is_root_admin=user.is_root_admin,
        is_admin=user.is_admin,
        is_issuer=user.is_issuer,
        can_edit=user.can_edit,
        is_active=user.is_active,
        is_activated=user.is_activated,
        has_valid_email=_has_valid_email(user.email),
        linked_person_id=user.linked_person_id,
        event_id=user.event_id,
        tags=user.tags or [],
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        deletion_requested_at=user.deletion_requested_at,
    )


@router.delete("/users/{user_id}")
@limiter.limit("10/minute")
def delete_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_recent_reauth),
    db: Session = Depends(get_db),
):
    """Remove an unused invitation; used accounts require signed erasure."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    require_user_management_access(user, admin)

    blockers = _direct_removal_blockers(db, user)
    if blockers:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SIGNED_DELETION_REQUIRED",
                "message": (
                    "This account has identity or operational history. Use the "
                    "signed deletion-evidence workflow instead."
                ),
                "blockers": blockers,
            },
        )

    # Invitation-only records carry no completed ceremony or operational act.
    db.query(AuthSession).filter(AuthSession.user_id == user_id).delete(synchronize_session=False)
    db.query(ExchangeCode).filter(ExchangeCode.user_id == user_id).delete(synchronize_session=False)
    db.query(PasskeyChallenge).filter(PasskeyChallenge.user_id == user_id).delete(synchronize_session=False)
    db.query(PasskeyCeremony).filter(PasskeyCeremony.user_id == user_id).delete(synchronize_session=False)
    db.query(WebAuthnCredential).filter(WebAuthnCredential.user_id == user_id).delete(synchronize_session=False)
    db.query(ActivationEmailDelivery).filter(
        ActivationEmailDelivery.user_id == user_id
    ).delete(synchronize_session=False)
    db.query(ActivationLink).filter(
        ActivationLink.created_by_id == user_id
    ).delete(synchronize_session=False)
    db.query(ActivationLink).filter(ActivationLink.user_id == user_id).delete(synchronize_session=False)

    # Older account-creation audit rows may name the invitation in free text.
    # Preserve the administrative fact without retaining the unused identity.
    db.query(AuditLog).filter(
        AuditLog.resource_type == "user", AuditLog.resource_id == user_id
    ).update(
        {
            "resource_id": None,
            "detail": json.dumps({"subject": "unused_invitation"}),
        },
        synchronize_session=False,
    )
    audit(
        db,
        user=admin,
        action="user.delete_unused_invitation",
        resource_type="user",
        resource_id=None,
        detail=json.dumps({"removal_mode": "direct_unused_invitation"}),
        request=request,
    )
    db.delete(user)
    db.commit()
    return {"status": "ok", "removal_mode": "direct_unused_invitation"}


class BatchActivationLinksIn(BaseModel):
    """Bounded selection for generating initial activation links."""

    event_id: Optional[int] = Field(None, gt=0)
    user_ids: Optional[List[int]] = Field(None, min_length=1, max_length=1000)

    @field_validator("user_ids")
    @classmethod
    def unique_optional_user_ids(cls, value: Optional[List[int]]) -> Optional[List[int]]:
        """Reject duplicate or non-positive explicit recipients."""

        if value is None:
            return value
        if len(set(value)) != len(value):
            raise ValueError("Each user may only be selected once")
        if any(user_id <= 0 for user_id in value):
            raise ValueError("User IDs must be positive")
        return value


class BatchActivationLinkSkipped(BaseModel):
    """Concrete reason why a selected user received no manual link."""

    user_id: int
    display_name: str
    error_code: str
    message: str


class BatchActivationLinkItem(BaseModel):
    """One generated manual link returned only to the requesting administrator."""

    user_id: int
    username: str
    display_name: str
    activation_url: str
    expires_at: datetime
    purpose: Literal["initial_setup"] = "initial_setup"


class BatchActivationLinksOut(BaseModel):
    """Generated manual links and concrete reasons for every exclusion."""

    links: List[BatchActivationLinkItem]
    count: int
    skipped: List[BatchActivationLinkSkipped]


class ActivationQrCodeItemIn(BaseModel):
    """One raw manual token paired with the user it was generated for."""

    user_id: int = Field(..., gt=0)
    token: str = Field(..., min_length=1, max_length=256)


class ActivationQrCodesIn(BaseModel):
    """Bounded explicit selection for canonical activation QR downloads."""

    items: List[ActivationQrCodeItemIn] = Field(..., min_length=1, max_length=50)

    @field_validator("items")
    @classmethod
    def unique_users(cls, value: List[ActivationQrCodeItemIn]):
        """Reject ambiguous duplicate users before rendering any artwork."""

        user_ids = [item.user_id for item in value]
        if len(set(user_ids)) != len(user_ids):
            raise ValueError("Each user may only be selected once")
        return value


class ActivationEmailIn(BaseModel):
    """Optional retry metadata for one explicit activation-email send."""

    retry_of_delivery_id: Optional[int] = Field(None, gt=0)
    purpose: Optional[ManagedPasskeyPurpose] = None


class ActivationLinkIn(BaseModel):
    """Optional operation for a manually distributed active-account link."""

    purpose: Optional[ManagedPasskeyPurpose] = None


class ActivationLinkOut(BaseModel):
    """One manually distributed link and its resolved registration purpose."""

    activation_url: str
    expires_at: datetime
    purpose: Literal["initial_setup", "additional_passkey", "credential_reset"]


class BatchActivationEmailsIn(BaseModel):
    """Explicit bounded selection for immediate activation-email delivery."""

    user_ids: List[int] = Field(..., min_length=1, max_length=50)

    @field_validator("user_ids")
    @classmethod
    def unique_user_ids(cls, value: List[int]) -> List[int]:
        """Reject ambiguous duplicate recipients rather than sending twice."""

        if len(set(value)) != len(value):
            raise ValueError("Each user may only be selected once")
        if any(user_id <= 0 for user_id in value):
            raise ValueError("User IDs must be positive")
        return value


class ActivationEmailResult(BaseModel):
    """Safe per-user result for an immediate SMTP attempt."""

    user_id: int
    display_name: str
    email: Optional[str] = None
    status: Literal[
        "sending", "accepted", "failed", "unknown", "skipped", "not_attempted"
    ]
    message: str
    delivery_id: Optional[int] = None
    error_code: Optional[str] = None
    expires_at: Optional[datetime] = None
    purpose: Literal["initial_setup", "additional_passkey", "credential_reset"]


class BatchActivationEmailsOut(BaseModel):
    """Per-recipient results and summary counts for a selected batch."""

    results: List[ActivationEmailResult]
    counts: dict[str, int]


class ActivationEmailDeliveryOut(BaseModel):
    """Non-secret activation-email delivery history entry."""

    id: int
    activation_link_id: Optional[int] = None
    retry_of_id: Optional[int] = None
    recipient_email: str
    status: Literal["sending", "accepted", "failed", "unknown", "not_attempted"]
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    includes_qr: bool
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    purpose: Literal["initial_setup", "additional_passkey", "credential_reset"]


def _event_name(user: User, db: Session) -> str | None:
    """Return the event name used in activation email body copy."""

    if user.event_id is None:
        return None
    event = db.query(Event.name).filter(Event.id == user.event_id).first()
    return event[0] if event else None


def _latest_retryable_delivery(
    user_id: int,
    db: Session,
    *,
    purpose: ActivationPurpose,
) -> ActivationEmailDelivery | None:
    """Return the latest unsuccessful delivery for one operation."""

    return (
        db.query(ActivationEmailDelivery)
        .filter(
            ActivationEmailDelivery.user_id == user_id,
            ActivationEmailDelivery.purpose == purpose,
            ActivationEmailDelivery.status.in_(("failed", "unknown", "not_attempted")),
        )
        .order_by(ActivationEmailDelivery.started_at.desc(), ActivationEmailDelivery.id.desc())
        .first()
    )


def _record_not_attempted(
    *,
    user: User,
    admin: User,
    error: ActivationMailError,
    purpose: ActivationPurpose,
    retry_of_id: int | None,
    request: Request,
    db: Session,
) -> ActivationEmailResult:
    """Persist a safe result when SMTP was unavailable before token creation."""

    delivery = ActivationEmailDelivery(
        user_id=user.id,
        requested_by_id=admin.id,
        retry_of_id=retry_of_id,
        recipient_email=user.email or "",
        purpose=purpose,
        status="not_attempted",
        error_code=error.code,
        error_message=error.safe_message,
        includes_qr=True,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(delivery)
    audit(
        db,
        user=admin,
        action="activation.email_not_attempted",
        resource_type="user",
        resource_id=user.id,
        detail=json.dumps({"purpose": purpose, "error_code": error.code}),
        request=request,
        outcome="error",
    )
    db.commit()
    db.refresh(delivery)
    return ActivationEmailResult(
        user_id=user.id,
        display_name=user.display_name,
        email=user.email,
        status="not_attempted",
        message=error.safe_message,
        delivery_id=delivery.id,
        error_code=error.code,
        purpose=purpose,
    )


def _send_user_activation_email(
    *,
    user: User,
    admin: User,
    mailer: ActivationMailer,
    request: Request,
    db: Session,
    purpose: ActivationPurpose,
    retry_of_id: int | None = None,
) -> ActivationEmailResult:
    """Issue, send and record one concurrency-safe activation email."""

    locked_user = (
        db.query(User)
        .filter(User.id == user.id)
        .with_for_update()
        .one()
    )
    user = locked_user
    recovered = recover_stale_deliveries(db, user_id=user.id)
    if recovered:
        db.flush()
    in_progress = (
        db.query(ActivationEmailDelivery)
        .filter(
            ActivationEmailDelivery.user_id == user.id,
            ActivationEmailDelivery.status == "sending",
        )
        .first()
    )
    if in_progress is not None:
        db.commit()
        return ActivationEmailResult(
            user_id=user.id,
            display_name=user.display_name,
            email=user.email,
            status="skipped",
            message="An activation email is already being sent to this user.",
            delivery_id=in_progress.id,
            error_code="delivery_in_progress",
            purpose=in_progress.purpose,
        )

    recipient = normalise_recipient(user.email)
    raw_token, link = create_activation_link(
        user_id=user.id,
        created_by_id=admin.id,
        db=db,
        purpose=purpose,
        delivery_pending=True,
        permit_email_delivery_start=True,
    )
    url = absolute_activation_url(raw_token)
    policy_identity = current_policy_identity(db)
    policy_url = (
        f"{settings.WEBAUTHN_ORIGIN.rstrip('/')}/api/v1/governance/public/versions/"
        f"{policy_identity[0]}/privacy.html"
        if policy_identity else None
    )
    try:
        message, message_id = build_activation_message(
            recipient=recipient,
            display_name=user.display_name,
            event_name=_event_name(user, db),
            url=url,
            expires_at=link.expires_at,
            purpose=purpose,
            policy_url=policy_url,
        )
    except Exception as exc:
        logger.error("Activation email rendering failed (%s)", type(exc).__name__)
        link.invalidated_at = datetime.now(timezone.utc)
        link.delivery_pending = False
        delivery = ActivationEmailDelivery(
            activation_link_id=link.id,
            user_id=user.id,
            requested_by_id=admin.id,
            retry_of_id=retry_of_id,
            recipient_email=recipient,
            purpose=purpose,
            status="failed",
            error_code="email_render_failed",
            error_message="The email could not be prepared. No activation link was left active.",
            includes_qr=True,
            completed_at=datetime.now(timezone.utc),
        )
        db.add(delivery)
        audit(
            db,
            user=admin,
            action="activation.email_failed",
            resource_type="user",
            resource_id=user.id,
            detail=json.dumps({
                "purpose": purpose,
                "error_code": "email_render_failed",
            }),
            request=request,
            outcome="error",
        )
        db.commit()
        db.refresh(delivery)
        return ActivationEmailResult(
            user_id=user.id,
            display_name=user.display_name,
            email=recipient,
            status="failed",
            message=delivery.error_message,
            delivery_id=delivery.id,
            error_code=delivery.error_code,
            purpose=purpose,
        )
    delivery = ActivationEmailDelivery(
        activation_link_id=link.id,
        user_id=user.id,
        requested_by_id=admin.id,
        retry_of_id=retry_of_id,
        recipient_email=recipient,
        purpose=purpose,
        message_id=message_id,
        status="sending",
        includes_qr=True,
    )
    db.add(delivery)
    audit(
        db,
        user=admin,
        action="activation.email_attempt",
        resource_type="user",
        resource_id=user.id,
        detail=json.dumps({"purpose": purpose, "retry_of_id": retry_of_id}),
        request=request,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        current = (
            db.query(ActivationEmailDelivery)
            .filter(
                ActivationEmailDelivery.user_id == user.id,
                ActivationEmailDelivery.status == "sending",
            )
            .first()
        )
        return ActivationEmailResult(
            user_id=user.id,
            display_name=user.display_name,
            email=recipient,
            status="skipped",
            message="An activation email is already being sent to this user.",
            delivery_id=current.id if current else None,
            error_code="delivery_in_progress",
            purpose=current.purpose if current else purpose,
        )
    db.refresh(delivery)

    failure: ActivationMailError | None = None
    try:
        mailer.send(message)
    except ActivationMailError as exc:
        failure = exc
    except Exception as exc:
        logger.error("Unexpected activation SMTP failure (%s)", type(exc).__name__)
        failure = ActivationMailError(
            "delivery_unknown",
            "Delivery could not be confirmed. The activation link was invalidated; send a fresh email.",
            unknown=True,
        )

    if failure is not None:
        delivery.status = "unknown" if failure.unknown else "failed"
        delivery.error_code = failure.code
        delivery.error_message = failure.safe_message
        delivery.completed_at = datetime.now(timezone.utc)
        link.invalidated_at = datetime.now(timezone.utc)
        link.delivery_pending = False
        audit(
            db,
            user=admin,
            action="activation.email_failed",
            resource_type="activation_email_delivery",
            resource_id=delivery.id,
            detail=json.dumps({
                "purpose": purpose,
                "status": delivery.status,
                "error_code": failure.code,
            }),
            request=request,
            outcome="error",
        )
        db.commit()
        return ActivationEmailResult(
            user_id=user.id,
            display_name=user.display_name,
            email=recipient,
            status=delivery.status,
            message=failure.safe_message,
            delivery_id=delivery.id,
            error_code=failure.code,
            purpose=purpose,
        )

    delivery.status = "accepted"
    delivery.completed_at = datetime.now(timezone.utc)
    link.delivery_pending = False
    audit(
        db,
        user=admin,
        action="activation.email_accepted",
        resource_type="activation_email_delivery",
        resource_id=delivery.id,
        detail=json.dumps({"purpose": purpose}),
        request=request,
    )
    db.commit()
    return ActivationEmailResult(
        user_id=user.id,
        display_name=user.display_name,
        email=recipient,
        status="accepted",
        message="Accepted by the mail server.",
        delivery_id=delivery.id,
        expires_at=_ensure_aware_utc(link.expires_at),
        purpose=purpose,
    )


@router.post("/batch-activation-links", response_model=BatchActivationLinksOut)
def batch_activation_links(
    body: BatchActivationLinksIn,
    request: Request,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Generate links for the exact eligible selection and report exclusions."""

    event_id = body.event_id
    user_ids = body.user_ids
    if _is_issuer_only(admin):
        if admin.event_id is None:
            raise HTTPException(status_code=403, detail="Issuer has no event access")
        event_id = admin.event_id

    skipped: list[BatchActivationLinkSkipped] = []
    if user_ids is not None:
        users = []
        for user_id in user_ids:
            user = db.query(User).filter(User.id == user_id).first()
            if user is None:
                skipped.append(BatchActivationLinkSkipped(
                    user_id=user_id,
                    display_name="Unknown user",
                    error_code="user_not_found",
                    message="User not found.",
                ))
                continue
            require_user_management_access(user, admin)
            if event_id is not None and user.event_id != event_id:
                skipped.append(BatchActivationLinkSkipped(
                    user_id=user.id,
                    display_name=user.display_name,
                    error_code="event_mismatch",
                    message="This user is not in the selected event.",
                ))
                continue
            if not user.is_active:
                skipped.append(BatchActivationLinkSkipped(
                    user_id=user.id,
                    display_name=user.display_name,
                    error_code="account_inactive",
                    message="This account is deactivated.",
                ))
                continue
            if user.is_activated:
                skipped.append(BatchActivationLinkSkipped(
                    user_id=user.id,
                    display_name=user.display_name,
                    error_code="already_activated",
                    message="This user is already activated.",
                ))
                continue
            users.append(user)
    else:
        query = db.query(User).filter(
            User.is_activated.is_(False),
            User.is_active.is_(True),
            User.is_root_admin.is_(False),
        )
        if not admin.is_root_admin:
            query = query.filter(
                User.is_admin.is_(False),
                User.is_issuer.is_(False),
            )
        if event_id is not None:
            query = query.filter(User.event_id == event_id)
        users = query.all()

    results = []
    for u in users:
        try:
            raw_token, _link = create_activation_link(
                user_id=u.id,
                created_by_id=admin.id,
                db=db,
                purpose="initial_setup",
            )
        except ActivationDeliveryInProgressError:
            skipped.append(BatchActivationLinkSkipped(
                user_id=u.id,
                display_name=u.display_name,
                error_code="delivery_in_progress",
                message="An activation email is currently being handed off.",
            ))
            continue
        results.append({
            "user_id": u.id,
            "username": u.username,
            "display_name": u.display_name,
            "activation_url": f"/activate#token={raw_token}",
            "expires_at": _ensure_aware_utc(_link.expires_at),
        })
    audit(
        db,
        user=admin,
        action="activation.create_batch",
        resource_type="user",
        detail=json.dumps({"user_ids": [user.id for user in users]}),
        request=request,
    )
    db.commit()
    return {
        "links": results,
        "count": len(results),
        "skipped": [item.model_dump() for item in skipped],
    }


def _activation_qr_filename(display_name: str, user_id: int) -> str:
    """Return a token-free collision-safe filename for one QR card."""

    ascii_name = (
        unicodedata.normalize("NFKD", display_name)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", ascii_name).strip("-_")
    return f"{safe_name or 'participant'}-{user_id}.png"


@router.post("/activation-qr-codes")
def download_activation_qr_codes(
    body: ActivationQrCodesIn,
    request: Request,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Return canonical QR PNGs for valid manual links in one ZIP archive.

    Raw tokens arrive only in the protected request body. They are validated
    against their stored hashes and are never included in logs, audit detail,
    filenames, database fields, or error responses.
    """

    resolved: list[tuple[ActivationQrCodeItemIn, User, ActivationLink]] = []
    for item in body.items:
        link = validate_activation_token(item.token, db)
        if link is None or link.user_id != item.user_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "One or more activation links are no longer available. "
                    "Generate fresh links and try again."
                ),
            )
        user = db.query(User).filter(User.id == item.user_id).first()
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=400,
                detail=(
                    "One or more activation links are no longer available. "
                    "Generate fresh links and try again."
                ),
            )
        require_user_management_access(user, admin)
        resolved.append((item, user, link))

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_DEFLATED) as output:
        for item, user, link in resolved:
            png = render_activation_qr_png(
                absolute_activation_url(item.token),
                user.display_name,
                link.purpose,
            )
            output.writestr(
                _activation_qr_filename(user.display_name, user.id),
                png,
            )

    audit(
        db,
        user=admin,
        action="activation.qr_download",
        resource_type="user",
        detail=json.dumps({"user_ids": [item.user_id for item in body.items]}),
        request=request,
    )
    db.commit()
    return Response(
        content=archive.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="activation-qr-codes.zip"',
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )


@router.get("/activation-delivery/settings")
def get_activation_delivery_settings(
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Return safe activation delivery capability and effective validity."""

    result = safe_mail_settings()
    result["expiry_hours"] = runtime_settings.get_int(
        "activation_link_expiry_hours",
        db,
    )
    return result


@router.post(
    "/users/{user_id}/activation-email",
    response_model=ActivationEmailResult,
)
@limiter.limit("10/minute")
def send_user_activation_email(
    user_id: int,
    request: Request,
    body: ActivationEmailIn | None = None,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Generate and immediately email a fresh activation link to one user."""

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    require_user_management_access(user, admin)
    requested_purpose = body.purpose if body else None
    try:
        purpose = resolve_activation_purpose(
            is_activated=user.is_activated,
            requested=requested_purpose,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not user.is_active:
        return ActivationEmailResult(
            user_id=user.id,
            display_name=user.display_name,
            email=user.email,
            status="skipped",
            message="This account is deactivated.",
            error_code="account_inactive",
            purpose=purpose,
        )
    if user.is_activated:
        ensure_recent_reauth(admin, db)

    retry_of_id = body.retry_of_delivery_id if body else None
    if retry_of_id is not None:
        retry = (
            db.query(ActivationEmailDelivery)
            .filter(
                ActivationEmailDelivery.id == retry_of_id,
                ActivationEmailDelivery.user_id == user.id,
            )
            .first()
        )
        if retry is None:
            raise HTTPException(status_code=404, detail="Delivery attempt not found")
        if retry.status not in {"failed", "unknown", "not_attempted"}:
            raise HTTPException(status_code=409, detail="Only unsuccessful deliveries can be retried")
        if requested_purpose is not None and retry.purpose != requested_purpose:
            raise HTTPException(
                status_code=409,
                detail="Retry purpose does not match the original delivery",
            )
        purpose = retry.purpose
    else:
        retry = _latest_retryable_delivery(user.id, db, purpose=purpose)
        retry_of_id = retry.id if retry else None

    try:
        normalise_recipient(user.email)
    except ActivationMailError as error:
        return ActivationEmailResult(
            user_id=user.id,
            display_name=user.display_name,
            email=user.email,
            status="skipped",
            message=error.safe_message,
            error_code=error.code,
            purpose=purpose,
        )

    try:
        with ActivationMailer() as mailer:
            return _send_user_activation_email(
                user=user,
                admin=admin,
                mailer=mailer,
                request=request,
                db=db,
                purpose=purpose,
                retry_of_id=retry_of_id,
            )
    except ActivationMailError as error:
        return _record_not_attempted(
            user=user,
            admin=admin,
            error=error,
            purpose=purpose,
            retry_of_id=retry_of_id,
            request=request,
            db=db,
        )


@router.post(
    "/batch-activation-emails",
    response_model=BatchActivationEmailsOut,
)
@limiter.limit("2/minute")
def send_batch_activation_emails(
    body: BatchActivationEmailsIn,
    request: Request,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Email activation links to an explicit selection of pending users."""

    results: list[ActivationEmailResult] = []
    eligible: list[User] = []
    for user_id in body.user_ids:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            results.append(ActivationEmailResult(
                user_id=user_id,
                display_name="Unknown user",
                status="skipped",
                message="User not found.",
                error_code="user_not_found",
                purpose=INITIAL_SETUP,
            ))
            continue
        require_user_management_access(user, admin)
        if not user.is_active:
            results.append(ActivationEmailResult(
                user_id=user.id,
                display_name=user.display_name,
                email=user.email,
                status="skipped",
                message="This account is deactivated.",
                error_code="account_inactive",
                purpose=INITIAL_SETUP,
            ))
            continue
        if user.is_activated:
            results.append(ActivationEmailResult(
                user_id=user.id,
                display_name=user.display_name,
                email=user.email,
                status="skipped",
                message="This user is already activated. Manage passkeys individually.",
                error_code="already_activated",
                purpose=INITIAL_SETUP,
            ))
            continue
        try:
            normalise_recipient(user.email)
        except ActivationMailError as error:
            results.append(ActivationEmailResult(
                user_id=user.id,
                display_name=user.display_name,
                email=user.email,
                status="skipped",
                message=error.safe_message,
                error_code=error.code,
                purpose=INITIAL_SETUP,
            ))
            continue
        eligible.append(user)

    if eligible:
        try:
            with ActivationMailer() as mailer:
                connection_lost = False
                for user in eligible:
                    retry = _latest_retryable_delivery(
                        user.id,
                        db,
                        purpose=INITIAL_SETUP,
                    )
                    if connection_lost:
                        result = _record_not_attempted(
                            user=user,
                            admin=admin,
                            error=ActivationMailError(
                                "smtp_connection_lost",
                                "The mail connection stopped before this user was attempted. Retry this email.",
                            ),
                            purpose=INITIAL_SETUP,
                            retry_of_id=retry.id if retry else None,
                            request=request,
                            db=db,
                        )
                    else:
                        result = _send_user_activation_email(
                            user=user,
                            admin=admin,
                            mailer=mailer,
                            request=request,
                            db=db,
                            purpose=INITIAL_SETUP,
                            retry_of_id=retry.id if retry else None,
                        )
                        connection_lost = result.status == "unknown"
                    results.append(result)
        except ActivationMailError as error:
            for user in eligible:
                retry = _latest_retryable_delivery(
                    user.id,
                    db,
                    purpose=INITIAL_SETUP,
                )
                results.append(_record_not_attempted(
                    user=user,
                    admin=admin,
                    error=error,
                    purpose=INITIAL_SETUP,
                    retry_of_id=retry.id if retry else None,
                    request=request,
                    db=db,
                ))

    order = {user_id: index for index, user_id in enumerate(body.user_ids)}
    results.sort(key=lambda result: order[result.user_id])
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return BatchActivationEmailsOut(results=results, counts=counts)


@router.get(
    "/users/{user_id}/activation-email-deliveries",
    response_model=List[ActivationEmailDeliveryOut],
)
def get_user_activation_email_deliveries(
    user_id: int,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Return non-secret activation-email history for one visible user."""

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    require_user_management_access(user, admin)
    deliveries = (
        db.query(ActivationEmailDelivery)
        .filter(ActivationEmailDelivery.user_id == user.id)
        .order_by(ActivationEmailDelivery.started_at.desc(), ActivationEmailDelivery.id.desc())
        .all()
    )
    return [ActivationEmailDeliveryOut(
        id=delivery.id,
        activation_link_id=delivery.activation_link_id,
        retry_of_id=delivery.retry_of_id,
        recipient_email=delivery.recipient_email,
        status=delivery.status,
        error_code=delivery.error_code,
        error_message=delivery.error_message,
        includes_qr=delivery.includes_qr,
        started_at=_ensure_aware_utc(delivery.started_at),
        completed_at=_ensure_aware_utc(delivery.completed_at),
        purpose=delivery.purpose,
    ) for delivery in deliveries]


@router.post("/users/{user_id}/activation-link", response_model=ActivationLinkOut)
def create_user_activation_link(
    user_id: int,
    request: Request,
    body: ActivationLinkIn | None = None,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Create a purpose-bound registration link and invalidate older links."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    require_user_management_access(user, admin)
    if not user.is_active:
        raise HTTPException(status_code=409, detail="Cannot create activation link for a deactivated user")
    try:
        purpose = resolve_activation_purpose(
            is_activated=user.is_activated,
            requested=body.purpose if body else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if user.is_activated:
        ensure_recent_reauth(admin, db)

    try:
        raw_token, _link = create_activation_link(
            user_id=user.id,
            created_by_id=admin.id,
            db=db,
            purpose=purpose,
        )
    except ActivationDeliveryInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail="Wait for the activation email hand-off to finish before creating a manual link",
        ) from exc
    audit(
        db,
        user=admin,
        action="activation.create",
        resource_type="user",
        resource_id=user.id,
        detail=json.dumps({"purpose": _link.purpose}),
        request=request,
    )
    db.commit()

    return ActivationLinkOut(
        activation_url=f"/activate#token={raw_token}",
        expires_at=_ensure_aware_utc(_link.expires_at),
        purpose=purpose,
    )


@router.get("/users/{user_id}/activation-links")
def get_user_activation_links(
    user_id: int,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Get activation link status for a user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    require_user_management_access(user, admin)

    links = (
        db.query(ActivationLink)
        .filter(ActivationLink.user_id == user_id)
        .order_by(ActivationLink.created_at.desc())
        .all()
    )

    now = datetime.now(timezone.utc)

    result = []
    for link in links:
        if link.used_at:
            status = "used"
        elif link.invalidated_at:
            status = "invalidated"
        elif link.delivery_pending:
            status = "delivery_pending"
        elif link.expires_at and _ensure_aware_utc(link.expires_at) < now:
            status = "expired"
        else:
            status = "active"

        result.append({
            "id": link.id,
            "purpose": link.purpose,
            "status": status,
            "created_at": link.created_at.isoformat() if link.created_at else None,
            "expires_at": link.expires_at.isoformat() if link.expires_at else None,
            "used_at": link.used_at.isoformat() if link.used_at else None,
        })

    return result


@router.delete("/users/{user_id}/activation-links/{link_id}")
def invalidate_activation_link(
    user_id: int,
    link_id: int,
    request: Request,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Invalidate a specific activation link."""
    from datetime import datetime, timezone

    # Issuer scoping: verify target user shares event
    target_user = db.query(User).filter(User.id == user_id).first()
    if target_user:
        require_user_management_access(target_user, admin)

    link = (
        db.query(ActivationLink)
        .filter(ActivationLink.id == link_id, ActivationLink.user_id == user_id)
        .first()
    )
    if not link:
        raise HTTPException(status_code=404, detail="Activation link not found")
    if link.delivery_pending:
        raise HTTPException(
            status_code=409,
            detail="Wait for the activation email hand-off to finish before invalidating this link",
        )

    link.invalidated_at = datetime.now(timezone.utc)
    audit(
        db,
        user=admin,
        action="activation.invalidate",
        resource_type="activation_link",
        resource_id=link.id,
        request=request,
    )
    db.commit()
    return {"status": "ok", "message": "Activation link invalidated"}


# ---------------------------------------------------------------------------
# Event deletion (cascade)
# ---------------------------------------------------------------------------

@router.delete("/events/{event_id}")
def delete_event(
    event_id: int,
    request: Request,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    """Reject direct deletion in favour of the accountable case workflow."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    raise HTTPException(
        status_code=409,
        detail={
            "code": "DELETION_CASE_REQUIRED",
            "message": (
                "Start an event deletion case at "
                f"/api/v1/admin/deletion-requests/events/{event_id}; "
                "direct deletion is disabled."
            ),
        },
    )


# ---------------------------------------------------------------------------
# Server Setup Import
# ---------------------------------------------------------------------------

class ImportUserIn(BaseModel):
    """Imported user record from a desktop setup export."""

    username: str = Field(..., max_length=64)
    display_name: str = Field(..., max_length=128)
    email: Optional[EmailStr] = None
    can_edit: bool = False
    person_id: Optional[int] = Field(None, gt=0)  # Desktop Person.id for auto-linking
    evidence_subject_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    )

    model_config = ConfigDict(extra="forbid")


class ImportEventIn(BaseModel):
    """Imported event metadata from a desktop setup export."""

    evidence_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    )
    name: str = Field(..., max_length=128)
    location: Optional[str] = Field(None, max_length=256)
    start_date: Optional[str] = Field(None, max_length=16)  # YYYY-MM-DD
    end_date: Optional[str] = Field(None, max_length=16)

    model_config = ConfigDict(extra="forbid")


class ImportSetupIn(BaseModel):
    """Bulk setup import payload containing one event and users."""

    event: ImportEventIn
    users: List[ImportUserIn] = Field(default_factory=list, max_length=1000)

    model_config = ConfigDict(extra="forbid")


class ImportUserOut(BaseModel):
    """Imported user response with activation URL."""

    user: UserOut
    activation_url: str


class ImportSetupResponse(BaseModel):
    """Import response with generated publish secret and user links."""

    event: EventOut
    publish_secret: str
    users: List[ImportUserOut]


@router.post("/import-setup", response_model=ImportSetupResponse)
@limiter.limit("5/minute")
def import_setup(
    request: Request,
    body: ImportSetupIn,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    """Import event + users from a JSON export. Server generates the publish secret."""
    # Create event
    raw_secret = secrets.token_urlsafe(48)
    secret_hash = hashlib.sha256(raw_secret.encode()).hexdigest()

    event = Event(
        evidence_id=body.event.evidence_id,
        name=body.event.name,
        location=body.event.location,
        start_date=datetime.strptime(body.event.start_date, "%Y-%m-%d").date() if body.event.start_date else None,
        end_date=datetime.strptime(body.event.end_date, "%Y-%m-%d").date() if body.event.end_date else None,
        status="draft",
        publish_secret_hash=secret_hash,
    )
    db.add(event)
    db.flush()  # Get event.id
    materialise_event_purge_deadline(event, db)

    # Create users
    user_results: List[ImportUserOut] = []
    for u_in in body.users:
        # Skip duplicates
        existing = db.query(User).filter(User.username == u_in.username).first()
        if existing:
            continue

        user = User(
            evidence_subject_id=u_in.evidence_subject_id,
            username=u_in.username,
            display_name=u_in.display_name,
            email=str(u_in.email) if u_in.email else None,
            event_id=event.id,
            can_edit=u_in.can_edit,
            is_active=True,
            is_activated=False,
            linked_person_id=u_in.person_id,  # Auto-link via desktop Person.id
        )
        db.add(user)
        db.flush()

        raw_token, _link = create_activation_link(
            user_id=user.id,
            created_by_id=admin.id,
            db=db,
        )

        user_results.append(ImportUserOut(
            user=UserOut(
                id=user.id,
                username=user.username,
                display_name=user.display_name,
                email=user.email,
                is_root_admin=False,
                is_admin=False,
                is_issuer=False,
                can_edit=user.can_edit,
                is_active=True,
                is_activated=False,
                linked_person_id=u_in.person_id,
                event_id=event.id,
                last_login_at=None,
                created_at=user.created_at,
            ),
            activation_url=f"/activate#token={raw_token}",
        ))

    # Auto-link if published person data already exists for this event
    _auto_link_event_users(event.id, db)

    audit(db, user=admin, action="event.import_setup", resource_type="event",
          resource_id=event.id, request=request)
    db.commit()

    protection = protect_current_state("publisher-secret-import")
    if not protection.protected:
        imported_user_ids = [result.user.id for result in user_results]
        if imported_user_ids:
            db.query(ActivationLink).filter(
                ActivationLink.user_id.in_(imported_user_ids)
            ).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_(imported_user_ids)).delete(
                synchronize_session=False
            )
        db.delete(event)
        db.commit()
        request_ha_replication("publisher-secret-import-rollback")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "standby_protection_failed",
                "message": "The setup was not imported because its publisher token could not be protected on the standby.",
            },
        )

    return ImportSetupResponse(
        event=EventOut(
            id=event.id,
            evidence_id=event.evidence_id,
            name=event.name,
            location=event.location,
            start_date=event.start_date.isoformat() if event.start_date else None,
            end_date=event.end_date.isoformat() if event.end_date else None,
            status=event.status,
            purge_grace_days=event.purge_grace_days,
            purge_due_at=event.purge_due_at,
            purge_case_request_id=event.purge_case_request_id,
            purge_started_at=event.purge_started_at,
            created_at=event.created_at,
            logo_color_1=None,
            logo_color_2=None,
        ),
        publish_secret=raw_secret,
        users=user_results,
    )


# ---------------------------------------------------------------------------
# User-Person linking (manual override)
# ---------------------------------------------------------------------------

class LinkPersonIn(BaseModel):
    """Manual user-to-person linking payload."""

    person_id: Optional[int] = None  # external_person_id; None to unlink


@router.put("/users/{user_id}/link-person")
def link_user_to_person(
    user_id: int,
    body: LinkPersonIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Manually link or unlink a user to a published person."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    require_user_management_access(user, admin)

    person = None
    if body.person_id is not None:
        # Validate person exists for user's event
        person = (
            db.query(PublishedPerson)
            .filter(
                PublishedPerson.event_id == user.event_id,
                PublishedPerson.external_person_id == body.person_id,
            )
            .first()
        )
        if not person:
            raise HTTPException(status_code=404, detail="Person not found in this event")

    user.linked_person_id = body.person_id
    if person is not None:
        user.evidence_subject_id = person.evidence_subject_id
    audit(
        db,
        user=admin,
        action="user.link_person",
        resource_type="user",
        resource_id=user.id,
        detail=json.dumps({"person_id": body.person_id}),
        request=request,
    )
    db.commit()
    return {"status": "ok", "linked_person_id": user.linked_person_id}


# ---------------------------------------------------------------------------
# Helper: auto-link users by email
# ---------------------------------------------------------------------------

def _auto_link_event_users(event_id: int, db: Session) -> None:
    """Match users to published persons by email within the same event."""
    users = (
        db.query(User)
        .filter(User.event_id == event_id, User.email.isnot(None), User.email != "")
        .all()
    )
    if not users:
        return

    persons = (
        db.query(PublishedPerson)
        .filter(PublishedPerson.event_id == event_id, PublishedPerson.email.isnot(None))
        .all()
    )
    email_to_person = {p.email.lower(): p for p in persons if p.email}

    for user in users:
        person = email_to_person.get(user.email.lower())
        if person is not None:
            user.linked_person_id = person.external_person_id
            user.evidence_subject_id = person.evidence_subject_id


# ---------------------------------------------------------------------------
# Security settings (runtime-configurable)
# ---------------------------------------------------------------------------

@router.get("/settings")
def get_security_settings(
    admin: User = Depends(require_root_admin),
    db: Session = Depends(get_db),
):
    """Return root-admin security settings with current effective values."""
    return runtime_settings.get_all(db)


@router.get("/retention/status")
def get_retention_status(
    admin: User = Depends(require_root_admin),
    db: Session = Depends(get_db),
):
    """Return bounded scheduler health and the complete retention inventory."""

    return retention_status(db)


class SettingsUpdateIn(BaseModel):
    """Runtime security settings update payload."""

    settings: dict  # {key: int_value, ...}


@router.put("/settings")
def update_security_settings(
    body: SettingsUpdateIn,
    request: Request,
    admin: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    """Update root-admin security settings after re-authentication."""
    updated = []
    errors = []
    for key, value in body.settings.items():
        try:
            runtime_settings.set_value(key, int(value), db)
            updated.append(key)
        except (KeyError, ValueError, TypeError) as exc:
            errors.append({"key": key, "error": str(exc)})
    if updated:
        import json
        audit(db, user=admin, action="settings.update", resource_type="settings",
              detail=json.dumps({"updated_fields": updated}), request=request)
        db.commit()
        if "ha_replication_interval_minutes" in updated and settings.HA_MODE == "ha":
            _request_ha_replication("settings-change")
    return {"updated": updated, "errors": errors}


# ---------------------------------------------------------------------------
# Symmetric HA replication (root only)
# ---------------------------------------------------------------------------

_HA_SAFE_STATUS_KEYS = {
    "mode", "node_id", "holder_node_id", "peer_node_id", "generation",
    "automatic_failover", "state", "job_id", "job_state", "started_at",
    "completed_at", "last_attempt_at", "last_success_at", "last_received_at",
    "last_bundle_id", "last_bundle_sha256", "potential_data_loss_seconds",
    "peer_reachable", "peer_compatible", "error_code", "message",
}

_HA_SAFE_CONTROL_KEYS = {
    "holder_node_id", "generation", "lease_expires_at", "observed_at",
    "routing_ready", "automatic_failover", "should_promote",
}
_HA_SAFE_NODE_KEYS = {
    "node_id", "healthy", "is_holder", "last_heartbeat_at", "heartbeat_age_seconds",
    "release_hash", "bundle_id", "bundle_generation", "bundle_created_at",
    "smtp_configured", "smtp_ready", "smtp_checked_at", "smtp_error_code",
    "smtp_config_fingerprint", "critical_pending",
}
_HA_SAFE_INCIDENT_KEYS = {
    "id", "kind", "state", "node_id", "from_node_id", "to_node_id",
    "generation", "started_at", "detected_at", "decision_at", "routing_ready_at",
    "resolved_at", "detection_seconds", "decision_seconds", "recovery_seconds",
}
_HA_SAFE_INCIDENT_GROUP_KEYS = {
    "id", "category", "state", "node_id", "from_node_id", "to_node_id",
    "generation", "service_impact", "started_at", "last_contact_at",
    "detected_at", "safety_boundary_at", "recovery_point_at", "decision_at",
    "routing_ready_at", "service_restored_at", "redundancy_restored_at",
    "resolved_at", "downtime_seconds", "event_count",
}
_HA_SAFE_DOWNTIME_AGGREGATE_KEYS = {
    "incident_count", "active_count", "total_downtime_seconds",
    "average_downtime_seconds",
}
_HA_SAFE_TRANSITION_KEYS = {
    "phase", "reason", "from_node_id", "to_node_id", "started_at",
    "last_contact_at", "detected_at", "decision_at", "routing_ready_at",
    "earliest_failover_at", "recovery_point_at",
}
_HA_SAFE_LAST_RECOVERY_KEYS = {"kind", "completed_at", "recovery_seconds"}
_HA_FAILOVER_DELAY_SECONDS = 120


class HADashboardOut(BaseModel):
    """Sanitised root-only operational view of the local HA installation."""

    format: Literal["mp-opt-ha-dashboard-v1"]
    observed_at: str
    mode: Literal["standalone", "ha"]
    cluster: dict
    transition: dict
    last_recovery: dict | None = None
    nodes: List[dict] = Field(default_factory=list)
    replication: dict
    recovery: dict
    incidents: List[dict] = Field(default_factory=list)
    incident_groups: List[dict] = Field(default_factory=list)
    incident_summary: dict = Field(default_factory=dict)


def _read_bounded_json(path: str | Path, maximum_bytes: int = 131_072) -> dict:
    """Read a small runtime JSON object without following malformed content."""

    candidate = Path(path)
    try:
        if not candidate.is_file() or candidate.stat().st_size > maximum_bytes:
            return {}
        document = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _ha_age_seconds(value: object, now: datetime) -> int | None:
    """Return a non-negative live age for a public ISO timestamp."""

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    parsed = _ensure_aware_utc(parsed)
    return max(0, int((now - parsed).total_seconds())) if parsed else None


def _read_ha_replication_status() -> dict:
    if settings.HA_MODE != "ha":
        return {"mode": "standalone", "state": "not-configured"}
    try:
        data = json.loads(Path(settings.HA_REPLICATION_STATUS_PATH).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        data = {"mode": "ha", "state": "unknown", "message": "No replication status is available yet."}
    if not isinstance(data, dict):
        data = {"mode": "ha", "state": "unknown"}
    safe = {key: value for key, value in data.items() if key in _HA_SAFE_STATUS_KEYS}
    safe["mode"] = "ha"
    safe.setdefault("node_id", settings.HA_NODE_ID)
    safe.setdefault("peer_node_id", settings.HA_PEER_NODE_ID)
    try:
        control = json.loads(Path(settings.HA_LEASE_STATE_PATH).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        control = {}
    if isinstance(control, dict):
        for source, target in (
            ("holder_node_id", "holder_node_id"),
            ("generation", "generation"),
            ("automatic_failover", "automatic_failover"),
        ):
            if source in control:
                safe[target] = control[source]
    try:
        received = json.loads((Path(settings.HA_LEASE_STATE_PATH).parent / "ha-receiver.json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        received = {}
    if isinstance(received, dict) and received.get("last_received_at"):
        safe["last_received_at"] = received["last_received_at"]
    return safe


def _read_ha_dashboard(db: Session) -> HADashboardOut:
    """Compose live replication, witness, node and recovery telemetry."""

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    replication = _read_ha_replication_status()
    replication["interval_minutes"] = runtime_settings.get_int(
        "ha_replication_interval_minutes", db
    )
    replication["potential_data_loss_seconds"] = _ha_age_seconds(
        replication.get("last_success_at"), now
    )

    control = _read_bounded_json(settings.HA_LEASE_STATE_PATH)
    snapshot = _read_bounded_json(settings.HA_SNAPSHOT_STATUS_PATH)
    safe_control = {
        key: value for key, value in control.items() if key in _HA_SAFE_CONTROL_KEYS
    }
    witness_age = _ha_age_seconds(control.get("observed_at"), now)
    safe_control.update({
        "cluster_id": settings.HA_CLUSTER_ID or None,
        "local_node_id": settings.HA_NODE_ID or None,
        "peer_node_id": settings.HA_PEER_NODE_ID or None,
        "witness_age_seconds": witness_age,
        "lease_remaining_seconds": None,
    })
    safe_control["failover_delay_seconds"] = _HA_FAILOVER_DELAY_SECONDS
    lease_age = _ha_age_seconds(control.get("lease_expires_at"), now)
    if isinstance(control.get("lease_expires_at"), str) and lease_age is not None:
        try:
            lease = datetime.fromisoformat(
                str(control["lease_expires_at"]).replace("Z", "+00:00")
            )
            lease = _ensure_aware_utc(lease)
            safe_control["lease_remaining_seconds"] = max(
                0, int((lease - now).total_seconds())
            ) if lease else None
        except ValueError:
            pass

    nodes: list[dict] = []
    raw_nodes = control.get("nodes")
    if isinstance(raw_nodes, list):
        for raw_node in raw_nodes[:2]:
            if not isinstance(raw_node, dict):
                continue
            node = {
                key: value for key, value in raw_node.items() if key in _HA_SAFE_NODE_KEYS
            }
            node["heartbeat_age_seconds"] = _ha_age_seconds(
                raw_node.get("last_heartbeat_at"), now
            )
            nodes.append(node)

    incidents: list[dict] = []
    raw_incidents = control.get("incidents")
    if isinstance(raw_incidents, list):
        for raw_incident in raw_incidents[:20]:
            if isinstance(raw_incident, dict):
                incidents.append({
                    key: value
                    for key, value in raw_incident.items()
                    if key in _HA_SAFE_INCIDENT_KEYS
                })

    incident_groups: list[dict] = []
    raw_groups = control.get("incident_groups")
    if isinstance(raw_groups, list):
        for raw_group in raw_groups[:100]:
            if isinstance(raw_group, dict):
                incident_groups.append({
                    key: value
                    for key, value in raw_group.items()
                    if key in _HA_SAFE_INCIDENT_GROUP_KEYS
                })

    incident_summary: dict = {"retention_days": 90}
    raw_summary = control.get("incident_summary")
    if isinstance(raw_summary, dict):
        retention_days = raw_summary.get("retention_days")
        if isinstance(retention_days, int) and 1 <= retention_days <= 365:
            incident_summary["retention_days"] = retention_days
        for category in (
            "overall", "planned_handoff", "automatic_failover", "primary_outage"
        ):
            aggregate = raw_summary.get(category)
            if isinstance(aggregate, dict):
                incident_summary[category] = {
                    key: value
                    for key, value in aggregate.items()
                    if key in _HA_SAFE_DOWNTIME_AGGREGATE_KEYS
                }

    raw_transition = control.get("transition")
    transition = (
        {
            key: value
            for key, value in raw_transition.items()
            if key in _HA_SAFE_TRANSITION_KEYS
        }
        if isinstance(raw_transition, dict)
        else {"phase": "stable", "reason": None}
    )
    transition.setdefault("phase", "stable")
    transition.setdefault("reason", None)
    raw_last_recovery = control.get("last_recovery")
    last_recovery = (
        {
            key: value
            for key, value in raw_last_recovery.items()
            if key in _HA_SAFE_LAST_RECOVERY_KEYS
        }
        if isinstance(raw_last_recovery, dict)
        else None
    )

    receipt_keys = {
        "name", "type", "created_at", "archive_sha256", "archive_size",
        "verification", "recovery_status", "verified_at", "recovery_key_id",
        "local_state", "off_server_state",
    }
    portable_keys = {
        "state", "snapshot", "confirmed_at", "required_at", "reason",
        "package_sha256", "package_size", "archive_sha256", "recovery_key_id",
    }
    safe_snapshot: dict = {}
    for key in ("latest_database", "latest_full"):
        value = snapshot.get(key)
        safe_snapshot[key] = (
            {field: item for field, item in value.items() if field in receipt_keys}
            if isinstance(value, dict) else None
        )
    portable_value = snapshot.get("portable_export")
    safe_snapshot["portable_export"] = (
        {field: item for field, item in portable_value.items() if field in portable_keys}
        if isinstance(portable_value, dict) else None
    )
    for key in ("observed_at", "local_snapshot_count", "deep_verified_count"):
        if key in snapshot:
            safe_snapshot[key] = snapshot[key]
    recovery = {
        "storage_mode": settings.HA_RECOVERY_STORAGE_MODE,
        "status_observed_at": safe_snapshot.pop("observed_at", None),
        **safe_snapshot,
    }
    if not snapshot:
        recovery["state"] = "awaiting-status"
    elif settings.HA_RECOVERY_STORAGE_MODE == "manual_portable":
        export = recovery.get("portable_export")
        recovery["state"] = (
            "protected" if isinstance(export, dict)
            and export.get("state") == "operator-sha256-confirmed"
            else "export-required"
        )
    else:
        latest_recovery_points = (
            recovery.get("latest_full"), recovery.get("latest_database")
        )
        recovery["state"] = (
            "protected"
            if any(
                isinstance(point, dict)
                and point.get("off_server_state") == "hash-verified"
                for point in latest_recovery_points
            )
            else "archive-required"
        )

    return HADashboardOut(
        format="mp-opt-ha-dashboard-v1",
        observed_at=now_iso,
        mode="ha" if settings.HA_MODE == "ha" else "standalone",
        cluster=safe_control,
        transition=transition,
        last_recovery=last_recovery,
        nodes=nodes,
        replication=replication,
        recovery=recovery,
        incidents=incidents,
        incident_groups=incident_groups,
        incident_summary=incident_summary,
    )


def _request_ha_replication(reason: str) -> str | None:
    return request_ha_replication(reason)


@router.get("/ha/status", response_model=HADashboardOut)
def get_ha_dashboard_status(
    admin: User = Depends(require_root_admin_read_only),
    db: Session = Depends(get_db),
):
    """Return the complete sanitised HA/recovery dashboard document."""

    return _read_ha_dashboard(db)


@router.get("/ha/replication")
def get_ha_replication_status(
    admin: User = Depends(require_root_admin),
    db: Session = Depends(get_db),
):
    result = _read_ha_replication_status()
    result["interval_minutes"] = runtime_settings.get_int("ha_replication_interval_minutes", db)
    return result


@router.post("/ha/replication", status_code=202)
def trigger_ha_replication(
    request: Request,
    admin: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    document = _read_ha_replication_status()
    if settings.HA_MODE != "ha":
        raise HTTPException(status_code=409, detail="High availability is not configured")
    if document.get("holder_node_id") != settings.HA_NODE_ID:
        raise HTTPException(status_code=503, detail="Only the current primary can replicate")
    if document.get("job_state") in {"queued", "capturing", "transferring", "verifying", "applying"}:
        raise HTTPException(status_code=409, detail="A replication is already running")
    job_id = _request_ha_replication("root-admin")
    if job_id is None:
        raise HTTPException(status_code=503, detail="The replication agent is unavailable")
    audit(db, user=admin, action="ha.replication_requested", resource_type="ha_cluster",
          detail=json.dumps({"job_id": job_id}), request=request)
    db.commit()
    return {"job_id": job_id, "status": "queued"}


@router.get("/ha/replication/{job_id}")
def get_ha_replication_job(job_id: str, admin: User = Depends(require_root_admin)):
    try:
        uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Replication job not found") from exc
    document = _read_ha_replication_status()
    if document.get("job_id") != job_id:
        raise HTTPException(status_code=404, detail="Replication job not found")
    return document


class TestEmailIn(BaseModel):
    """Validated recipient for a token-free SMTP configuration test."""

    recipient: EmailStr


@router.post("/settings/email/test")
@limiter.limit("3/minute")
def send_test_email(
    body: TestEmailIn,
    request: Request,
    admin: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    """Send a token-free test email after recent root re-authentication."""

    recipient = normalise_recipient(str(body.recipient))
    try:
        with ActivationMailer() as mailer:
            mailer.send(build_test_message(recipient))
    except ActivationMailError as error:
        audit(
            db,
            user=admin,
            action="settings.email_test",
            resource_type="settings",
            detail=json.dumps({"error_code": error.code}),
            request=request,
            outcome="error",
        )
        db.commit()
        raise HTTPException(status_code=503, detail=error.safe_message) from error
    audit(
        db,
        user=admin,
        action="settings.email_test",
        resource_type="settings",
        request=request,
    )
    db.commit()
    return {
        "status": "accepted",
        "message": "Test email accepted by the mail server.",
    }


class InvalidateAllActivationLinksIn(BaseModel):
    """Explicit confirmation for global activation-link invalidation."""

    confirm: bool = False


@router.post("/activation-links/invalidate-all")
@limiter.limit("2/minute")
def invalidate_all_activation_links(
    body: InvalidateAllActivationLinksIn,
    request: Request,
    admin: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    """Invalidate every currently active activation link after confirmation."""

    if not body.confirm:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required")
    now = datetime.now(timezone.utc)
    count = (
        db.query(ActivationLink)
        .filter(
            ActivationLink.used_at.is_(None),
            ActivationLink.invalidated_at.is_(None),
            ActivationLink.delivery_pending.is_(False),
            ActivationLink.expires_at > now,
        )
        .update({"invalidated_at": now}, synchronize_session="fetch")
    )
    audit(
        db,
        user=admin,
        action="activation.invalidate_all",
        resource_type="activation_link",
        detail=json.dumps({"count": count}),
        request=request,
    )
    db.commit()
    return {"status": "ok", "invalidated_count": count}


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class AuditLogEntry(BaseModel):
    """Single audit log entry returned to admins."""

    id: int
    timestamp: str
    user_id: Optional[int]
    actor_ref: Optional[str]
    action: str
    resource_type: Optional[str]
    resource_id: Optional[int]
    detail: Optional[str]
    outcome: str

    model_config = ConfigDict(from_attributes=True)


class AuditLogResponse(BaseModel):
    """Paginated audit log response."""

    total: int
    page: int
    per_page: int
    entries: List[AuditLogEntry]


@router.get("/audit-log", response_model=AuditLogResponse)
def get_audit_log(
    page: int = Query(1, ge=1, le=100000),
    per_page: int = Query(50, ge=1, le=200),
    action: Optional[str] = Query(None, max_length=64),
    user_id: Optional[int] = None,
    admin: User = Depends(require_root_admin),
    db: Session = Depends(get_db),
):
    """Query the global audit log (root/controller only)."""
    from app.models.audit import AuditLog

    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if user_id is not None:
        q = q.filter(AuditLog.user_id == user_id)

    total = q.count()
    entries = (
        q.order_by(AuditLog.timestamp.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return AuditLogResponse(
        total=total,
        page=page,
        per_page=per_page,
        entries=[
            AuditLogEntry(
                id=e.id,
                timestamp=e.timestamp.isoformat() if e.timestamp else "",
                user_id=e.user_id,
                actor_ref=e.actor_ref,
                action=e.action,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                detail=e.detail,
                outcome=e.outcome or "success",
            )
            for e in entries
        ],
    )
