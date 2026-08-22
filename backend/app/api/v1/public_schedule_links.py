"""Management and token-authenticated access for shared Public Schedules."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.config import settings
from app.core.ha_replication import (
    cancel_uncommitted_protection,
    create_protection_operation,
    find_protection_operation,
    queue_protection_operation,
    sync_protection_operation,
)
from app.core.ha_witness import HAWritePermitError
from app.core.rate_limit import limiter
from app.core.schedule_days import event_schedule_day_range, working_date_for_clock
from app.core.security import require_root_or_issuer
from app.core.features import require_event_feature
from app.core.database_tenancy import (
    DatabaseTenantContext,
    apply_database_tenant_context,
    bounded_event_id_context,
)
from app.db.database import get_db
from app.models.event import Event
from app.models.ha import HAProtectionOperation
from app.models.public_schedule_link import (
    PublicScheduleLink,
    PublicScheduleLinkView,
)
from app.models.published import (
    PublishedGeneralScheduleCategory,
    PublishedGeneralScheduleItem,
)
from app.models.user import User

admin_router = APIRouter()
public_router = APIRouter()

_MAX_EXPIRY = timedelta(days=365)
_TOKEN_MIN_LENGTH = 20
_TOKEN_MAX_LENGTH = 256
_UNAVAILABLE_DETAIL = "Shared schedule not available"


class PublicScheduleLinkCreate(BaseModel):
    """Fields required to create a Public Schedule sharing link."""

    description: str = Field(..., min_length=1, max_length=256)
    expires_at: datetime
    view_ids: list[int] = Field(..., min_length=1, max_length=100)
    token: str = Field(..., min_length=32, max_length=256)
    idempotency_key: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")

    @field_validator("description")
    @classmethod
    def normalise_description(cls, value: str) -> str:
        """Trim and reject descriptions containing only whitespace."""
        value = value.strip()
        if not value:
            raise ValueError("Description is required")
        return value

    @field_validator("view_ids")
    @classmethod
    def validate_view_ids(cls, value: list[int]) -> list[int]:
        """Require positive, unique Public Schedule view identifiers."""
        if any(view_id <= 0 for view_id in value):
            raise ValueError("View IDs must be positive")
        if len(value) != len(set(value)):
            raise ValueError("View IDs must be unique")
        return value


class PublicScheduleLinkUpdate(BaseModel):
    """Editable fields for an active Public Schedule sharing link."""

    description: Optional[str] = Field(None, min_length=1, max_length=256)
    expires_at: Optional[datetime] = None
    view_ids: Optional[list[int]] = Field(None, min_length=1, max_length=100)
    idempotency_key: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")

    @field_validator("description")
    @classmethod
    def normalise_description(cls, value: Optional[str]) -> Optional[str]:
        """Trim an optional description and reject whitespace-only values."""
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Description is required")
        return value

    @field_validator("view_ids")
    @classmethod
    def validate_view_ids(cls, value: Optional[list[int]]) -> Optional[list[int]]:
        """Require positive, unique view identifiers when permissions change."""
        if value is None:
            return None
        if any(view_id <= 0 for view_id in value):
            raise ValueError("View IDs must be positive")
        if len(value) != len(set(value)):
            raise ValueError("View IDs must be unique")
        return value


class PublicScheduleLinkViewOut(BaseModel):
    """One view permission shown in the private management interface."""

    id: int
    name: str
    available: bool


class PublicScheduleLinkOut(BaseModel):
    """Private management metadata for a Public Schedule sharing link."""

    id: int
    event_id: int
    description: str
    expires_at: datetime
    invalidated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by_id: Optional[int] = None
    status: str
    views: list[PublicScheduleLinkViewOut]
    protection_operation_id: Optional[str] = None
    protection_state: Optional[str] = None
    protection_stage: Optional[str] = None
    protection_error_code: Optional[str] = None


class PublicScheduleLinkCreatedOut(PublicScheduleLinkOut):
    """Creation response containing the sharing URL shown only once."""

    share_url: Optional[str] = None


class SharedScheduleViewOut(BaseModel):
    """A currently available Public Schedule view exposed by a token."""

    id: int
    name: str
    sort_order: float = 0


class SharedScheduleAudienceOut(BaseModel):
    """Public audience label attached to a Session Element."""

    name: Optional[str] = None
    short_name: Optional[str] = None
    colour: Optional[str] = None


class SharedScheduleItemOut(BaseModel):
    """Public programme fields for one shared Session Element occurrence."""

    id: int
    view_id: int
    title: str
    date: str
    start_time: str
    end_time: str
    working_date: str
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    responsible: Optional[str] = None
    audience_teams: list[SharedScheduleAudienceOut] = Field(default_factory=list)
    description: Optional[str] = None
    type_name: Optional[str] = None
    colour: Optional[str] = None
    sort_order: float = 0


class SharedScheduleEventOut(BaseModel):
    """Public event metadata needed to navigate a shared schedule."""

    name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    day_aliases: Optional[dict[str, str]] = None
    schedule_day_range: dict[str, int]


class SharedScheduleOut(BaseModel):
    """Complete privacy-filtered response for a valid sharing token."""

    event: SharedScheduleEventOut
    views: list[SharedScheduleViewOut]
    items: list[SharedScheduleItemOut]


def _aware_utc(value: datetime) -> datetime:
    """Return a database datetime as an aware UTC value."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _validate_expiry(value: datetime) -> datetime:
    """Validate and normalise a requested link expiry timestamp."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Expiry must include a timezone",
        )
    value = value.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if value <= now:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Expiry must be in the future",
        )
    if value > now + _MAX_EXPIRY:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Expiry cannot be more than one year away",
        )
    return value


def _require_event_access(event_id: int, user: User, db: Session) -> Event:
    """Apply the stricter root-or-own-event scope used by sharing links."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if user.is_root_admin:
        return event
    if user.is_issuer and user.event_id == event_id:
        return event
    raise HTTPException(status_code=404, detail="Event not found")


def _current_views(
    event_id: int,
    db: Session,
) -> dict[int, PublishedGeneralScheduleCategory]:
    """Return the event's current explicit Public Schedule views by ID."""
    rows = (
        db.query(PublishedGeneralScheduleCategory)
        .filter(PublishedGeneralScheduleCategory.event_id == event_id)
        .all()
    )
    return {row.external_category_id: row for row in rows}


def _link_view_rows(link_id: int, db: Session) -> list[PublicScheduleLinkView]:
    """Load permissions in their original view order."""
    return (
        db.query(PublicScheduleLinkView)
        .filter(PublicScheduleLinkView.link_id == link_id)
        .order_by(
            PublicScheduleLinkView.sort_order_snapshot.asc(),
            PublicScheduleLinkView.view_name_snapshot.asc(),
        )
        .all()
    )


def _link_operation(link_id: int, db: Session) -> HAProtectionOperation | None:
    if settings.HA_MODE != "ha":
        return None
    operation = (
        db.query(HAProtectionOperation)
        .filter(
            HAProtectionOperation.resource_type == "public_schedule_link",
            HAProtectionOperation.resource_id == str(link_id),
        )
        .order_by(HAProtectionOperation.mutation_sequence.desc())
        .first()
    )
    if operation is not None and operation.state in {"pending", "indeterminate"}:
        sync_protection_operation(db, operation)
    return operation


def _queue_link_operation(
    db: Session,
    operation: HAProtectionOperation | None,
    response: Response,
) -> None:
    if operation is None:
        return
    db.refresh(operation)
    queue_error = queue_protection_operation(operation)
    if queue_error is not None:
        operation.state = "indeterminate"
        operation.stage = "attention_required"
        operation.error_code = queue_error
        db.commit()
    response.status_code = status.HTTP_202_ACCEPTED


def _link_status(
    link: PublicScheduleLink,
    view_rows: list[PublicScheduleLinkView],
    available_view_ids: set[int],
) -> str:
    """Return the management status for a link and current schedule views."""
    if link.invalidated_at is not None:
        return "invalidated"
    if _aware_utc(link.expires_at) <= datetime.now(timezone.utc):
        return "expired"
    if not any(row.external_view_id in available_view_ids for row in view_rows):
        return "unavailable"
    return "active"


def _serialise_link(
    link: PublicScheduleLink,
    db: Session,
    *,
    share_url: Optional[str] = None,
) -> PublicScheduleLinkOut | PublicScheduleLinkCreatedOut:
    """Build private link metadata without ever returning the token hash."""
    current_views = _current_views(link.event_id, db)
    view_rows = _link_view_rows(link.id, db)
    operation = _link_operation(link.id, db)
    link_status = _link_status(link, view_rows, set(current_views))
    if operation is not None and operation.state in {"pending", "indeterminate"}:
        link_status = "securing"
    values: dict[str, Any] = {
        "id": link.id,
        "event_id": link.event_id,
        "description": link.description,
        "expires_at": _aware_utc(link.expires_at),
        "invalidated_at": (
            _aware_utc(link.invalidated_at) if link.invalidated_at else None
        ),
        "created_at": _aware_utc(link.created_at),
        "updated_at": _aware_utc(link.updated_at) if link.updated_at else None,
        "created_by_id": link.created_by_id,
        "status": link_status,
        "protection_operation_id": operation.id if operation else None,
        "protection_state": operation.state if operation else None,
        "protection_stage": operation.stage if operation else None,
        "protection_error_code": operation.error_code if operation else None,
        "views": [
            PublicScheduleLinkViewOut(
                id=row.external_view_id,
                name=(
                    current_views[row.external_view_id].name
                    if row.external_view_id in current_views
                    else row.view_name_snapshot
                ),
                available=row.external_view_id in current_views,
            )
            for row in view_rows
        ],
    }
    if share_url is not None:
        return PublicScheduleLinkCreatedOut(**values, share_url=share_url)
    return PublicScheduleLinkOut(**values)


def _load_managed_link(
    event_id: int,
    link_id: int,
    user: User,
    db: Session,
) -> PublicScheduleLink:
    """Load an event-scoped link after applying management access checks."""
    _require_event_access(event_id, user, db)
    link = (
        db.query(PublicScheduleLink)
        .filter(
            PublicScheduleLink.id == link_id,
            PublicScheduleLink.event_id == event_id,
        )
        .first()
    )
    if link is None:
        raise HTTPException(status_code=404, detail="Public schedule link not found")
    return link


def _require_active_link(link: PublicScheduleLink, db: Session) -> None:
    """Reject edits and invalidation after a link has permanently ended."""
    view_rows = _link_view_rows(link.id, db)
    current_ids = set(_current_views(link.event_id, db))
    status_value = _link_status(link, view_rows, current_ids)
    if status_value in {"expired", "invalidated"}:
        raise HTTPException(status_code=409, detail="Link is no longer active")


def _replace_view_permissions(
    link: PublicScheduleLink,
    view_ids: list[int],
    db: Session,
    *,
    allow_existing_unavailable: bool,
) -> None:
    """Replace permissions while preventing newly granted missing views."""
    current_views = _current_views(link.event_id, db)
    existing_rows = _link_view_rows(link.id, db)
    existing = {row.external_view_id: row for row in existing_rows}
    unavailable_additions = [
        view_id
        for view_id in view_ids
        if view_id not in current_views
        and (not allow_existing_unavailable or view_id not in existing)
    ]
    if unavailable_additions:
        raise HTTPException(
            status_code=422,
            detail="One or more Public Schedule views are unavailable",
        )

    selected = set(view_ids)
    for row in existing_rows:
        if row.external_view_id not in selected:
            db.delete(row)
    for view_id in view_ids:
        current = current_views.get(view_id)
        previous = existing.get(view_id)
        if previous is not None:
            if current is not None:
                previous.view_name_snapshot = current.name
                previous.sort_order_snapshot = current.sort_order or 0
            continue
        db.add(
            PublicScheduleLinkView(
                link_id=link.id,
                external_view_id=view_id,
                view_name_snapshot=current.name,
                sort_order_snapshot=current.sort_order or 0,
            )
        )
    db.flush()


def _validate_new_view_permissions(
    event_id: int,
    view_ids: list[int],
    db: Session,
) -> None:
    """Reject link creation before inserting rows when a view is unavailable."""
    current_view_ids = set(_current_views(event_id, db))
    if any(view_id not in current_view_ids for view_id in view_ids):
        raise HTTPException(
            status_code=422,
            detail="One or more Public Schedule views are unavailable",
        )


@admin_router.get(
    "/events/{event_id}/public-schedule-links",
    response_model=list[PublicScheduleLinkOut],
)
@limiter.limit("60/minute")
def list_public_schedule_links(
    event_id: int,
    request: Request,
    user: User = Depends(require_root_or_issuer),
    db: Session = Depends(get_db),
):
    """List retained sharing links for an accessible event."""
    _require_event_access(event_id, user, db)
    links = (
        db.query(PublicScheduleLink)
        .filter(PublicScheduleLink.event_id == event_id)
        .order_by(PublicScheduleLink.created_at.desc(), PublicScheduleLink.id.desc())
        .all()
    )
    values = [_serialise_link(link, db) for link in links]
    db.commit()
    return values


@admin_router.post(
    "/events/{event_id}/public-schedule-links",
    response_model=PublicScheduleLinkCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
def create_public_schedule_link(
    event_id: int,
    body: PublicScheduleLinkCreate,
    request: Request,
    response: Response,
    user: User = Depends(require_root_or_issuer),
    db: Session = Depends(get_db),
):
    """Create a sharing link and return its raw URL exactly once."""
    _require_event_access(event_id, user, db)
    require_event_feature(event_id, "public_schedule_links", db)
    expires_at = _validate_expiry(body.expires_at)
    _validate_new_view_permissions(event_id, body.view_ids, db)
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    if settings.HA_MODE == "ha":
        if body.idempotency_key is None:
            raise HTTPException(status_code=422, detail="Idempotency key is required in HA mode")
        existing_operation = find_protection_operation(db, body.idempotency_key)
        if existing_operation is not None:
            if existing_operation.operation_type != "public-link-create":
                raise HTTPException(status_code=409, detail="Idempotency key is already in use")
            link = db.query(PublicScheduleLink).filter(
                PublicScheduleLink.id == int(existing_operation.resource_id or 0)
            ).first()
            if link is None or link.token_hash != token_hash:
                raise HTTPException(status_code=409, detail="Idempotent link request does not match")
            sync_protection_operation(db, existing_operation)
            db.commit()
            response.status_code = status.HTTP_202_ACCEPTED
            return _serialise_link(link, db)
    link = PublicScheduleLink(
        event_id=event_id,
        token_hash=token_hash,
        description=body.description,
        expires_at=expires_at,
        created_by_id=user.id,
    )
    db.add(link)
    db.flush()
    _replace_view_permissions(
        link,
        body.view_ids,
        db,
        allow_existing_unavailable=False,
    )
    audit(
        db,
        user=user,
        action="public_schedule_link.create",
        resource_type="public_schedule_link",
        resource_id=link.id,
        detail=json.dumps(
            {"event_id": event_id, "view_ids": body.view_ids, "expires_at": expires_at.isoformat()}
        ),
        request=request,
    )
    protection: HAProtectionOperation | None = None
    try:
        protection = create_protection_operation(
            db, idempotency_key=body.idempotency_key,
            operation_type="public-link-create", resource_type="public_schedule_link",
            resource_id=str(link.id),
        )
        db.commit()
    except HAWritePermitError as exc:
        db.rollback()
        cancel_uncommitted_protection(protection)
        raise HTTPException(status_code=503, detail="The standby protection guard is unavailable") from exc
    except Exception:
        db.rollback()
        cancel_uncommitted_protection(protection)
        raise
    db.refresh(link)
    _queue_link_operation(db, protection, response)
    return _serialise_link(
        link,
        db,
        share_url=f"/shared-schedule#token={body.token}" if protection is None else None,
    )


@admin_router.patch(
    "/events/{event_id}/public-schedule-links/{link_id}",
    response_model=PublicScheduleLinkOut,
)
@limiter.limit("30/minute")
def update_public_schedule_link(
    event_id: int,
    link_id: int,
    body: PublicScheduleLinkUpdate,
    request: Request,
    response: Response,
    user: User = Depends(require_root_or_issuer),
    db: Session = Depends(get_db),
):
    """Update an active link without changing its token."""
    require_event_feature(event_id, "public_schedule_links", db)
    if not (body.model_fields_set - {"idempotency_key"}):
        raise HTTPException(status_code=422, detail="No changes supplied")
    link = _load_managed_link(event_id, link_id, user, db)
    if settings.HA_MODE == "ha":
        if body.idempotency_key is None:
            raise HTTPException(status_code=422, detail="Idempotency key is required in HA mode")
        existing_operation = find_protection_operation(db, body.idempotency_key)
        if existing_operation is not None:
            if existing_operation.operation_type != "public-link-update" or existing_operation.resource_id != str(link.id):
                raise HTTPException(status_code=409, detail="Idempotency key is already in use")
            sync_protection_operation(db, existing_operation)
            db.commit()
            response.status_code = status.HTTP_202_ACCEPTED
            return _serialise_link(link, db)
        pending = _link_operation(link.id, db)
        if pending is not None and pending.state in {"pending", "indeterminate"}:
            raise HTTPException(status_code=409, detail={"code": "protection_pending", "operation_id": pending.id})
    _require_active_link(link, db)

    changed_fields: list[str] = []
    if "description" in body.model_fields_set:
        if body.description is None:
            raise HTTPException(status_code=422, detail="Description is required")
        link.description = body.description
        changed_fields.append("description")
    if "expires_at" in body.model_fields_set:
        if body.expires_at is None:
            raise HTTPException(status_code=422, detail="Expiry is required")
        link.expires_at = _validate_expiry(body.expires_at)
        changed_fields.append("expires_at")
    if "view_ids" in body.model_fields_set:
        if body.view_ids is None:
            raise HTTPException(status_code=422, detail="At least one view is required")
        _replace_view_permissions(
            link,
            body.view_ids,
            db,
            allow_existing_unavailable=True,
        )
        changed_fields.append("view_ids")

    audit(
        db,
        user=user,
        action="public_schedule_link.update",
        resource_type="public_schedule_link",
        resource_id=link.id,
        detail=json.dumps({"event_id": event_id, "changed_fields": changed_fields}),
        request=request,
    )
    protection: HAProtectionOperation | None = None
    try:
        protection = create_protection_operation(
            db, idempotency_key=body.idempotency_key,
            operation_type="public-link-update", resource_type="public_schedule_link",
            resource_id=str(link.id),
        )
        db.commit()
    except HAWritePermitError as exc:
        db.rollback()
        cancel_uncommitted_protection(protection)
        raise HTTPException(status_code=503, detail="The standby protection guard is unavailable") from exc
    except Exception:
        db.rollback()
        cancel_uncommitted_protection(protection)
        raise
    db.refresh(link)
    _queue_link_operation(db, protection, response)
    return _serialise_link(link, db)


@admin_router.post(
    "/events/{event_id}/public-schedule-links/{link_id}/invalidate",
    response_model=PublicScheduleLinkOut,
)
@limiter.limit("30/minute")
def invalidate_public_schedule_link(
    event_id: int,
    link_id: int,
    request: Request,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"),
    user: User = Depends(require_root_or_issuer),
    db: Session = Depends(get_db),
):
    """Permanently invalidate an active Public Schedule sharing link."""
    link = _load_managed_link(event_id, link_id, user, db)
    if settings.HA_MODE == "ha":
        if idempotency_key is None:
            raise HTTPException(status_code=422, detail="Idempotency key is required in HA mode")
        existing_operation = find_protection_operation(db, idempotency_key)
        if existing_operation is not None:
            if existing_operation.operation_type != "public-link-invalidate" or existing_operation.resource_id != str(link.id):
                raise HTTPException(status_code=409, detail="Idempotency key is already in use")
            sync_protection_operation(db, existing_operation)
            db.commit()
            response.status_code = status.HTTP_202_ACCEPTED
            return _serialise_link(link, db)
        pending = _link_operation(link.id, db)
        if pending is not None and pending.state in {"pending", "indeterminate"}:
            raise HTTPException(status_code=409, detail={"code": "protection_pending", "operation_id": pending.id})
    _require_active_link(link, db)
    link.invalidated_at = datetime.now(timezone.utc)
    audit(
        db,
        user=user,
        action="public_schedule_link.invalidate",
        resource_type="public_schedule_link",
        resource_id=link.id,
        detail=json.dumps({"event_id": event_id}),
        request=request,
    )
    protection: HAProtectionOperation | None = None
    try:
        protection = create_protection_operation(
            db, idempotency_key=idempotency_key,
            operation_type="public-link-invalidate", resource_type="public_schedule_link",
            resource_id=str(link.id),
        )
        db.commit()
    except HAWritePermitError as exc:
        db.rollback()
        cancel_uncommitted_protection(protection)
        raise HTTPException(status_code=503, detail="The standby protection guard is unavailable") from exc
    except Exception:
        db.rollback()
        cancel_uncommitted_protection(protection)
        raise
    db.refresh(link)
    _queue_link_operation(db, protection, response)
    return _serialise_link(link, db)


@admin_router.delete(
    "/events/{event_id}/public-schedule-links/{link_id}",
)
@limiter.limit("30/minute")
def delete_public_schedule_link(
    event_id: int,
    link_id: int,
    request: Request,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"),
    user: User = Depends(require_root_or_issuer),
    db: Session = Depends(get_db),
):
    """Permanently delete a managed Public Schedule sharing link in any state."""
    if settings.HA_MODE == "ha":
        if idempotency_key is None:
            raise HTTPException(status_code=422, detail="Idempotency key is required in HA mode")
        existing_operation = find_protection_operation(db, idempotency_key)
        if existing_operation is not None:
            if existing_operation.operation_type != "public-link-delete" or existing_operation.resource_id != str(link_id):
                raise HTTPException(status_code=409, detail="Idempotency key is already in use")
            sync_protection_operation(db, existing_operation)
            db.commit()
            response.status_code = status.HTTP_202_ACCEPTED
            return {
                "protection_operation_id": existing_operation.id,
                "protection_state": existing_operation.state,
                "protection_stage": existing_operation.stage,
            }
    link = _load_managed_link(event_id, link_id, user, db)
    pending = _link_operation(link.id, db)
    if pending is not None and pending.state in {"pending", "indeterminate"}:
        raise HTTPException(status_code=409, detail={"code": "protection_pending", "operation_id": pending.id})
    previous_status = _serialise_link(link, db).status
    audit(
        db,
        user=user,
        action="public_schedule_link.delete",
        resource_type="public_schedule_link",
        resource_id=link.id,
        detail=json.dumps(
            {"event_id": event_id, "previous_status": previous_status}
        ),
        request=request,
    )
    db.delete(link)
    protection: HAProtectionOperation | None = None
    try:
        protection = create_protection_operation(
            db, idempotency_key=idempotency_key,
            operation_type="public-link-delete", resource_type="public_schedule_link",
            resource_id=str(link.id),
        )
        db.commit()
    except HAWritePermitError as exc:
        db.rollback()
        cancel_uncommitted_protection(protection)
        raise HTTPException(status_code=503, detail="The standby protection guard is unavailable") from exc
    except Exception:
        db.rollback()
        cancel_uncommitted_protection(protection)
        raise
    _queue_link_operation(db, protection, response)
    if protection is None:
        response.status_code = status.HTTP_204_NO_CONTENT
        return None
    return {
        "protection_operation_id": protection.id,
        "protection_state": protection.state,
        "protection_stage": protection.stage,
    }


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    """Return a bounded bearer token or ``None`` for malformed input."""
    if not authorization:
        return None
    scheme, separator, token = authorization.partition(" ")
    token = token.strip()
    if (
        separator != " "
        or scheme.lower() != "bearer"
        or not (_TOKEN_MIN_LENGTH <= len(token) <= _TOKEN_MAX_LENGTH)
    ):
        return None
    return token


def _public_audience(raw: Optional[str]) -> list[SharedScheduleAudienceOut]:
    """Parse audience JSON while discarding identifiers and category metadata."""
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(values, list):
        return []
    return [
        SharedScheduleAudienceOut(
            name=value.get("name") if isinstance(value.get("name"), str) else None,
            short_name=(
                value.get("short_name")
                if isinstance(value.get("short_name"), str)
                else None
            ),
            colour=(
                value.get("colour") if isinstance(value.get("colour"), str) else None
            ),
        )
        for value in values
        if isinstance(value, dict)
    ]


def _day_aliases(event: Event) -> Optional[dict[str, str]]:
    """Return validated day aliases from public event metadata."""
    if not event.metadata_json:
        return None
    try:
        metadata = json.loads(event.metadata_json)
    except (json.JSONDecodeError, TypeError):
        return None
    aliases = metadata.get("day_aliases") if isinstance(metadata, dict) else None
    if not isinstance(aliases, dict):
        return None
    return {
        key: value
        for key, value in aliases.items()
        if isinstance(key, str) and isinstance(value, str)
    }


@public_router.get("/shared", response_model=SharedScheduleOut)
@limiter.limit("120/minute")
def get_shared_public_schedule(
    request: Request,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Return public programme data for a valid bearer sharing token."""
    token = _extract_bearer_token(authorization)
    if token is None:
        raise HTTPException(status_code=404, detail=_UNAVAILABLE_DETAIL)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    apply_database_tenant_context(
        db, DatabaseTenantContext(scope="public_link_lookup")
    )
    link = (
        db.query(PublicScheduleLink)
        .filter(PublicScheduleLink.token_hash == token_hash)
        .first()
    )
    if link is None:
        raise HTTPException(status_code=404, detail=_UNAVAILABLE_DETAIL)
    if bounded_event_id_context(
        db, scope="public_schedule_link", event_id=link.event_id
    ) is None:
        raise HTTPException(status_code=404, detail=_UNAVAILABLE_DETAIL)
    try:
        require_event_feature(link.event_id, "public_schedule_links", db)
    except HTTPException:
        raise HTTPException(status_code=404, detail=_UNAVAILABLE_DETAIL) from None
    protection = _link_operation(link.id, db)
    if protection is not None and protection.state != "accepted":
        raise HTTPException(status_code=404, detail=_UNAVAILABLE_DETAIL)

    view_rows = _link_view_rows(link.id, db)
    current_views = _current_views(link.event_id, db)
    available_ids = {
        row.external_view_id
        for row in view_rows
        if row.external_view_id in current_views
    }
    if _link_status(link, view_rows, set(current_views)) != "active" or not available_ids:
        raise HTTPException(status_code=404, detail=_UNAVAILABLE_DETAIL)

    event = db.query(Event).filter(Event.id == link.event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail=_UNAVAILABLE_DETAIL)
    schedule_day_range = event_schedule_day_range(event.metadata_json)

    views = sorted(
        (current_views[view_id] for view_id in available_ids),
        key=lambda row: ((row.sort_order or 0), row.name),
    )
    items = (
        db.query(PublishedGeneralScheduleItem)
        .filter(
            PublishedGeneralScheduleItem.event_id == link.event_id,
            PublishedGeneralScheduleItem.category_id.in_(available_ids),
        )
        .order_by(
            PublishedGeneralScheduleItem.date.asc(),
            PublishedGeneralScheduleItem.start_time.asc(),
            PublishedGeneralScheduleItem.sort_order.asc(),
            PublishedGeneralScheduleItem.title.asc(),
        )
        .all()
    )
    return SharedScheduleOut(
        event=SharedScheduleEventOut(
            name=event.name,
            start_date=event.start_date.isoformat() if event.start_date else None,
            end_date=event.end_date.isoformat() if event.end_date else None,
            day_aliases=_day_aliases(event),
            schedule_day_range=schedule_day_range,
        ),
        views=[
            SharedScheduleViewOut(
                id=view.external_category_id,
                name=view.name,
                sort_order=view.sort_order or 0,
            )
            for view in views
        ],
        items=[
            SharedScheduleItemOut(
                id=index,
                view_id=item.category_id,
                title=item.title,
                date=item.date,
                start_time=item.start_time,
                end_time=item.end_time,
                working_date=working_date_for_clock(
                    item.date,
                    item.start_time,
                    schedule_day_range,
                ),
                location_name=item.location_name,
                location_address=item.location_address,
                responsible=item.responsible,
                audience_teams=_public_audience(item.audience_teams_json),
                description=item.description,
                type_name=item.type_name,
                colour=item.colour,
                sort_order=item.sort_order or 0,
            )
            for index, item in enumerate(items, start=1)
        ],
    )
