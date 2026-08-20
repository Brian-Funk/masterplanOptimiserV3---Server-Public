"""
Notification endpoints  -  push subscription management and announcements.
"""
from typing import Optional, List
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.security import (
    get_current_user,
    require_admin_or_issuer,
    require_event_access,
    _is_issuer_only,
)
from app.core.push import get_application_server_key, send_push_to_event
from app.core.audit import audit
from app.core.governance import require_data_policy_acknowledgement
from app.core.features import require_event_feature
from app.core.rate_limit import limiter
from app.core import runtime_settings as rt
from app.models.notification import PushSubscription, Announcement, ScheduleChange
from app.models.user import User

router = APIRouter()

# ---------------------------------------------------------------------------
# Push endpoint validation  -  allow only known push service domains
# ---------------------------------------------------------------------------

_ALLOWED_PUSH_DOMAINS = {
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.services.mozilla.com",
    "wns.windows.com",
    "web.push.apple.com",
}


def _validate_push_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https":
        raise HTTPException(400, "Push endpoint must use HTTPS")
    domain = parsed.hostname or ""
    if not any(domain == d or domain.endswith("." + d) for d in _ALLOWED_PUSH_DOMAINS):
        raise HTTPException(400, "Push endpoint domain not recognised")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class VapidKeyResponse(BaseModel):
    """Public VAPID key response for browser push subscription."""

    public_key: str | None


class SubscribeRequest(BaseModel):
    """Browser push subscription payload."""

    event_id: int = Field(..., gt=0)
    endpoint: str = Field(..., max_length=2048)
    p256dh: str = Field(..., max_length=256)
    auth: str = Field(..., max_length=256)


class SubscribeResponse(BaseModel):
    """Push subscription mutation status."""

    status: str


class UnsubscribeRequest(BaseModel):
    """Push unsubscribe payload containing the browser endpoint."""

    endpoint: str = Field(..., max_length=2048)


class AnnouncementOut(BaseModel):
    """Announcement visible to event participants."""

    id: int
    event_id: int
    title: str
    body: str | None
    created_by: str | None
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class AnnouncementCreate(BaseModel):
    """Admin or issuer payload for creating an announcement."""

    event_id: int
    title: str = Field(..., max_length=256)
    body: str | None = Field(None, max_length=2000)
    push: bool = True  # Whether to also send a push notification


# ---------------------------------------------------------------------------
# VAPID public key  -  unauthenticated (needed before subscribe)
# ---------------------------------------------------------------------------

@router.get("/vapid-key", response_model=VapidKeyResponse)
def vapid_key():
    """Return the VAPID public key for push subscription."""
    return VapidKeyResponse(public_key=get_application_server_key())


# ---------------------------------------------------------------------------
# Subscribe / Unsubscribe
# ---------------------------------------------------------------------------

@router.post("/subscribe", response_model=SubscribeResponse)
@limiter.limit("10/minute")
def subscribe(
    req: SubscribeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save a push subscription for the current user + event."""
    require_event_access(req.event_id, current_user, db)
    require_event_feature(req.event_id, "push_notifications", db)
    _validate_push_endpoint(req.endpoint)
    if not req.p256dh or not req.auth:
        raise HTTPException(400, "p256dh and auth keys are required")

    # Check if already subscribed with this endpoint
    existing = (
        db.query(PushSubscription)
        .filter(
            PushSubscription.user_id == current_user.id,
            PushSubscription.endpoint == req.endpoint,
        )
        .first()
    )
    if existing:
        # Update keys (browser may rotate them)
        existing.p256dh = req.p256dh
        existing.auth = req.auth
        existing.event_id = req.event_id
    else:
        db.add(PushSubscription(
            user_id=current_user.id,
            event_id=req.event_id,
            endpoint=req.endpoint,
            p256dh=req.p256dh,
            auth=req.auth,
        ))
    db.commit()
    return SubscribeResponse(status="subscribed")


@router.delete("/subscribe", response_model=SubscribeResponse)
@limiter.limit("10/minute")
def unsubscribe(
    req: UnsubscribeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a push subscription."""
    deleted = (
        db.query(PushSubscription)
        .filter(
            PushSubscription.user_id == current_user.id,
            PushSubscription.endpoint == req.endpoint,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return SubscribeResponse(status="unsubscribed" if deleted else "not_found")


# ---------------------------------------------------------------------------
# Subscription status  -  check if current user is subscribed
# ---------------------------------------------------------------------------

class SubscriptionStatusResponse(BaseModel):
    """Current user's push subscription status for an event."""

    subscribed: bool


@router.get("/status/{event_id}", response_model=SubscriptionStatusResponse)
def subscription_status(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Check if the current user has any push subscription for this event."""
    require_event_access(event_id, current_user, db)
    require_event_feature(event_id, "push_notifications", db)
    exists = (
        db.query(PushSubscription)
        .filter(
            PushSubscription.user_id == current_user.id,
            PushSubscription.event_id == event_id,
        )
        .first()
    )
    return SubscriptionStatusResponse(subscribed=bool(exists))


# ---------------------------------------------------------------------------
# Announcements  -  read (authenticated users) / write (admins)
# ---------------------------------------------------------------------------

@router.get("/announcements/{event_id}", response_model=List[AnnouncementOut])
def list_announcements(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List announcements for an event (most recent first)."""
    require_event_access(event_id, current_user, db)
    rows = (
        db.query(Announcement, User.display_name)
        .outerjoin(User, Announcement.created_by_id == User.id)
        .filter(Announcement.event_id == event_id)
        .order_by(Announcement.created_at.desc())
        .limit(rt.get_int("announcements_per_event_limit", db))
        .all()
    )
    return [
        AnnouncementOut(
            id=ann.id,
            event_id=ann.event_id,
            title=ann.title,
            body=ann.body,
            created_by=display_name,
            created_at=ann.created_at.isoformat() if ann.created_at else "",
        )
        for ann, display_name in rows
    ]


@router.post("/announcements", response_model=AnnouncementOut, status_code=201)
def create_announcement(
    req: AnnouncementCreate,
    request: Request,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Create an announcement (admin or issuer). Optionally sends push notification."""
    # Every non-root role derives the event from its membership. A submitted
    # event identifier is never an authorization source.
    if not admin.is_root_admin:
        req.event_id = admin.event_id
    require_event_access(req.event_id, admin, db)
    require_data_policy_acknowledgement(admin, req.event_id, db)
    if req.push:
        require_event_feature(req.event_id, "push_notifications", db)
    ann = Announcement(
        event_id=req.event_id,
        title=req.title,
        body=req.body,
        created_by_id=admin.id,
    )
    db.add(ann)
    db.commit()
    db.refresh(ann)

    audit(db, user=admin, action="announcement.create", resource_type="announcement",
          resource_id=ann.id, request=request)
    db.commit()

    # Send push notification if requested
    if req.push:
        send_push_to_event(
            event_id=req.event_id,
            title=req.title,
            body=req.body or "",
            url=f"/calendar?event={req.event_id}",
            db=db,
            notification_type="announcement",
        )

    return AnnouncementOut(
        id=ann.id,
        event_id=ann.event_id,
        title=ann.title,
        body=ann.body,
        created_by=admin.display_name,
        created_at=ann.created_at.isoformat() if ann.created_at else "",
    )


@router.delete("/announcements/{announcement_id}", status_code=204)
def delete_announcement(
    announcement_id: int,
    request: Request,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Delete an announcement (admin or issuer)."""
    ann = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not ann:
        raise HTTPException(status_code=404, detail="Announcement not found")
    # Issuer scoping: verify announcement belongs to their event
    require_event_access(ann.event_id, admin, db)
    audit(db, user=admin, action="announcement.delete", resource_type="announcement",
          resource_id=announcement_id, request=request)
    db.delete(ann)
    db.commit()


# ---------------------------------------------------------------------------
# Schedule change notifications  -  per-person diffs from republishes
# ---------------------------------------------------------------------------

class ScheduleChangeOut(BaseModel):
    """Unread schedule change notification for a user."""

    id: int
    changes: dict
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class MarkChangesReadRequest(BaseModel):
    """Request to mark schedule changes as read for an event."""

    event_id: int = Field(..., gt=0)


@router.get("/changes/{event_id}", response_model=List[ScheduleChangeOut])
def list_pending_changes(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return unread schedule changes for the current user and event."""
    require_event_access(event_id, current_user, db)
    import json
    rows = (
        db.query(ScheduleChange)
        .filter(
            ScheduleChange.user_id == current_user.id,
            ScheduleChange.event_id == event_id,
            ScheduleChange.read_at.is_(None),
        )
        .order_by(ScheduleChange.created_at.desc())
        .all()
    )
    return [
        ScheduleChangeOut(
            id=row.id,
            changes=json.loads(row.changes_json) if row.changes_json else {},
            created_at=row.created_at.isoformat() if row.created_at else "",
        )
        for row in rows
    ]


@router.post("/changes/read")
def mark_changes_read(
    req: MarkChangesReadRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark all unread schedule changes as read for current user + event."""
    require_event_access(req.event_id, current_user, db)
    now = datetime.now(timezone.utc)
    count = (
        db.query(ScheduleChange)
        .filter(
            ScheduleChange.user_id == current_user.id,
            ScheduleChange.event_id == req.event_id,
            ScheduleChange.read_at.is_(None),
        )
        .update({"read_at": now}, synchronize_session=False)
    )
    db.commit()
    return {"status": "ok", "marked": count}
