"""Authenticated General Schedule publish and status endpoints."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.publish import _authenticate_event, _require_publishing_allowed
from app.core.audit import audit
from app.core.rate_limit import limiter, runtime_limit
from app.core.retention import materialise_event_purge_deadline
from app.core.schedule_days import (
    event_schedule_day_range,
    merge_schedule_day_range,
    normalise_schedule_day_range,
    schedule_day_offset_hour,
)
from app.core.security import (
    require_admin_or_issuer,
    require_event_access,
)
from app.db.database import get_db
from app.models.event import Event
from app.models.published import (
    GeneralSchedulePublishState,
    PublishedGeneralScheduleCategory,
    PublishedGeneralScheduleItem,
)
from app.models.user import User

publish_router = APIRouter()
admin_router = APIRouter()


class AudienceTeamIn(BaseModel):
    id: Optional[int] = None
    name: str = Field(..., max_length=256)
    short_name: Optional[str] = Field(None, max_length=64)
    colour: Optional[str] = Field(None, max_length=32)
    category_id: Optional[int] = None
    category_name: Optional[str] = Field(None, max_length=256)


class GeneralScheduleCategoryIn(BaseModel):
    id: int = Field(..., gt=0)
    name: str = Field(..., max_length=256)
    sort_order: float = 0


class GeneralScheduleItemIn(BaseModel):
    id: int = Field(..., gt=0)
    title: str = Field(..., max_length=512)
    date: str = Field(..., max_length=16)
    start_time: str = Field(..., max_length=5)
    end_time: str = Field(..., max_length=5)
    location_name: Optional[str] = Field(None, max_length=512)
    location_address: Optional[str] = Field(None, max_length=1024)
    responsible: Optional[str] = Field(None, max_length=512)
    audience_teams: List[AudienceTeamIn] = Field(default_factory=list, max_length=100)
    description: Optional[str] = Field(None, max_length=10000)
    schedule_view_ids: List[int] = Field(default_factory=list, max_length=100)
    schedule_view_names: List[str] = Field(default_factory=list, max_length=100)
    category_ids: List[int] = Field(default_factory=list, max_length=100)
    category_names: List[str] = Field(default_factory=list, max_length=100)
    type_id: Optional[int] = None
    type_name: Optional[str] = Field(None, max_length=256)
    copy_template_html: Optional[str] = Field(None, max_length=50000)
    category: Optional[str] = Field(None, max_length=128)
    colour: Optional[str] = Field(None, max_length=32)
    sort_order: float = 0


class GeneralScheduleEventIn(BaseModel):
    name: Optional[str] = Field(None, max_length=256)
    start_date: Optional[str] = Field(None, max_length=16)
    end_date: Optional[str] = Field(None, max_length=16)
    day_aliases: Optional[Dict[str, str]] = None
    schedule_day_range: Optional[Dict[str, int]] = None


class GeneralSchedulePublishPayload(BaseModel):
    """Full or working-day-scoped Public Schedule publish payload."""

    event: Optional[GeneralScheduleEventIn] = None
    categories: List[GeneralScheduleCategoryIn] = Field(default_factory=list, max_length=100)
    schedule_views: List[GeneralScheduleCategoryIn] = Field(default_factory=list, max_length=100)
    items: List[GeneralScheduleItemIn] = Field(default_factory=list, max_length=10000)
    fingerprint: str = Field(..., max_length=128)
    published_at: Optional[str] = None
    publish_scope: Literal["full", "dates"] = "full"
    dates: Optional[List[str]] = Field(default=None, max_length=366)
    working_day_offset_hour: int = Field(default=0, ge=0, le=12)


class GeneralSchedulePublishResponse(BaseModel):
    status: str
    items_published: int


def _normalise_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {value}") from None


def _validate_time(value: str, field_name: str) -> None:
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {value}") from None


def _normalise_scope_dates(values: Optional[List[str]]) -> set[str]:
    """Validate working-day identifiers supplied for a scoped publish."""
    if not values:
        raise HTTPException(
            status_code=400,
            detail="Date-scoped publish requires at least one date.",
        )
    return {_normalise_date(value) for value in values}


def _working_day(date_value: str, start_time: str, offset_hour: int) -> str:
    """Return the displayed working day for one stored date and start time."""
    actual_date = datetime.strptime(_normalise_date(date_value), "%Y-%m-%d").date()
    start = datetime.strptime(start_time, "%H:%M")
    if offset_hour > 0 and start.hour < offset_hour:
        actual_date -= timedelta(days=1)
    return actual_date.isoformat()


def _parse_published_at(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _item_to_dict(item: PublishedGeneralScheduleItem) -> dict[str, Any]:
    teams = []
    if item.audience_teams_json:
        try:
            parsed = json.loads(item.audience_teams_json)
            teams = parsed if isinstance(parsed, list) else []
        except ValueError:
            teams = []
    return {
        "id": item.id,
        "external_session_element_id": item.external_session_element_id,
        "title": item.title,
        "date": item.date,
        "start_time": item.start_time,
        "end_time": item.end_time,
        "location_name": item.location_name,
        "location_address": item.location_address,
        "responsible": item.responsible,
        "audience_teams": teams,
        "description": item.description,
        "category_id": item.category_id,
        "category_name": item.category_name,
        "type_id": item.type_id,
        "type_name": item.type_name,
        "copy_template_html": item.copy_template_html,
        "category": item.category,
        "colour": item.colour,
        "sort_order": item.sort_order or 0,
    }


def _category_targets(
    incoming: GeneralScheduleItemIn,
    *,
    explicit_schedule_views: bool,
) -> list[tuple[Optional[int], Optional[str]]]:
    targets: list[tuple[Optional[int], Optional[str]]] = []
    seen: set[tuple[Optional[int], Optional[str]]] = set()

    if explicit_schedule_views:
        seen_view_ids: set[int] = set()
        for index, view_id in enumerate(incoming.schedule_view_ids):
            view_name = incoming.schedule_view_names[index] if index < len(incoming.schedule_view_names) else None
            if view_id not in seen_view_ids:
                seen_view_ids.add(view_id)
                targets.append((view_id, view_name))
        return targets

    if incoming.category_ids:
        for index, category_id in enumerate(incoming.category_ids):
            category_name = incoming.category_names[index] if index < len(incoming.category_names) else None
            key = (category_id, category_name)
            if key not in seen:
                seen.add(key)
                targets.append(key)

    for team in incoming.audience_teams:
        if team.category_id is None and not team.category_name:
            continue
        key = (team.category_id, team.category_name)
        if key not in seen:
            seen.add(key)
            targets.append(key)

    if not targets:
        fallback = incoming.category or "Public Schedule"
        targets.append((None, fallback))

    return targets


@publish_router.post("/general-schedule", response_model=GeneralSchedulePublishResponse)
@limiter.limit(runtime_limit("public_schedule_pushes_per_minute"))
def publish_general_schedule(
    payload: GeneralSchedulePublishPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    """Receive public General Schedule items through normal publish-secret auth."""
    event = _authenticate_event(request, db)
    _require_publishing_allowed(event)
    incoming_schedule_day_range = (
        payload.event.schedule_day_range
        if payload.event and payload.event.schedule_day_range is not None
        else event_schedule_day_range(event.metadata_json)
    )
    schedule_day_range = normalise_schedule_day_range(incoming_schedule_day_range)
    if (
        incoming_schedule_day_range is not None
        and schedule_day_range != incoming_schedule_day_range
    ):
        raise HTTPException(status_code=400, detail="Invalid schedule day range.")
    working_day_offset_hour = schedule_day_offset_hour(schedule_day_range)
    published_at = _parse_published_at(payload.published_at)
    scoped_dates = (
        _normalise_scope_dates(payload.dates)
        if payload.publish_scope == "dates"
        else None
    )

    prepared_items = []
    for incoming in payload.items:
        item_date = _normalise_date(incoming.date)
        _validate_time(incoming.start_time, "start_time")
        _validate_time(incoming.end_time, "end_time")
        item_working_day = _working_day(
            item_date,
            incoming.start_time,
            working_day_offset_hour,
        )
        if scoped_dates is not None and item_working_day not in scoped_dates:
            raise HTTPException(
                status_code=400,
                detail=f"Session Element {incoming.id} is outside the requested publish dates.",
            )
        prepared_items.append((incoming, item_date))
    explicit_schedule_views = (
        "schedule_views" in payload.model_fields_set
        or any("schedule_view_ids" in item.model_fields_set for item in payload.items)
    )

    if payload.event:
        if payload.event.name:
            event.name = payload.event.name
        if payload.event.start_date:
            event.start_date = datetime.strptime(payload.event.start_date, "%Y-%m-%d").date()
        if payload.event.end_date:
            new_end_date = datetime.strptime(payload.event.end_date, "%Y-%m-%d").date()
            end_date_changed = event.end_date != new_end_date
            event.end_date = new_end_date
            materialise_event_purge_deadline(
                event,
                db,
                force=end_date_changed,
            )
        if payload.event.day_aliases is not None:
            existing_meta = json.loads(event.metadata_json) if event.metadata_json else {}
            existing_meta["day_aliases"] = payload.event.day_aliases
            event.metadata_json = json.dumps(existing_meta)
        if payload.event.schedule_day_range is not None:
            event.metadata_json = merge_schedule_day_range(
                event.metadata_json,
                schedule_day_range,
            )
        event.status = "published"

    if scoped_dates is None:
        db.query(PublishedGeneralScheduleCategory).filter(
            PublishedGeneralScheduleCategory.event_id == event.id,
        ).delete(synchronize_session=False)
        db.query(PublishedGeneralScheduleItem).filter(
            PublishedGeneralScheduleItem.event_id == event.id,
        ).delete(synchronize_session=False)
    else:
        existing_items = (
            db.query(PublishedGeneralScheduleItem)
            .filter(PublishedGeneralScheduleItem.event_id == event.id)
            .all()
        )
        for existing_item in existing_items:
            if _working_day(
                existing_item.date,
                existing_item.start_time,
                working_day_offset_hour,
            ) in scoped_dates:
                db.delete(existing_item)

    category_rows = payload.schedule_views if explicit_schedule_views else payload.categories
    if explicit_schedule_views and not category_rows:
        category_rows = payload.categories

    existing_categories = {
        row.external_category_id: row
        for row in db.query(PublishedGeneralScheduleCategory)
        .filter(PublishedGeneralScheduleCategory.event_id == event.id)
        .all()
    }
    incoming_category_ids: set[int] = set()
    for category in category_rows:
        incoming_category_ids.add(category.id)
        category_row = existing_categories.get(category.id)
        if category_row is None:
            category_row = PublishedGeneralScheduleCategory(
                event_id=event.id,
                external_category_id=category.id,
            )
            db.add(category_row)
        category_row.name = category.name
        category_row.sort_order = category.sort_order
        category_row.published_at = published_at

    published_rows = 0
    for incoming, item_date in prepared_items:
        for category_id, category_name in _category_targets(
            incoming,
            explicit_schedule_views=explicit_schedule_views,
        ):
            db.add(
                PublishedGeneralScheduleItem(
                    event_id=event.id,
                    external_session_element_id=incoming.id,
                    title=incoming.title,
                    date=item_date,
                    start_time=incoming.start_time,
                    end_time=incoming.end_time,
                    location_name=incoming.location_name,
                    location_address=incoming.location_address,
                    responsible=incoming.responsible,
                    audience_teams_json=json.dumps(
                        [team.model_dump() for team in incoming.audience_teams],
                        ensure_ascii=False,
                    ) if incoming.audience_teams else None,
                    description=incoming.description,
                    category_id=category_id,
                    category_name=category_name,
                    type_id=incoming.type_id,
                    type_name=incoming.type_name,
                    copy_template_html=incoming.copy_template_html,
                    category=incoming.category,
                    colour=incoming.colour,
                    sort_order=incoming.sort_order,
                    published_at=published_at,
                )
            )
            published_rows += 1

    db.flush()
    if scoped_dates is not None:
        used_category_ids = {
            category_id
            for (category_id,) in db.query(PublishedGeneralScheduleItem.category_id)
            .filter(
                PublishedGeneralScheduleItem.event_id == event.id,
                PublishedGeneralScheduleItem.category_id.isnot(None),
            )
            .distinct()
            .all()
        }
        for category_id, category_row in existing_categories.items():
            if category_id not in incoming_category_ids and category_id not in used_category_ids:
                db.delete(category_row)

    state = (
        db.query(GeneralSchedulePublishState)
        .filter(GeneralSchedulePublishState.event_id == event.id)
        .first()
    )
    if state is None:
        state = GeneralSchedulePublishState(event_id=event.id)
        db.add(state)
    state.fingerprint = payload.fingerprint if scoped_dates is None else None
    state.published_at = published_at
    state.item_count = len(
        {
            external_id
            for (external_id,) in db.query(
                PublishedGeneralScheduleItem.external_session_element_id
            )
            .filter(PublishedGeneralScheduleItem.event_id == event.id)
            .distinct()
            .all()
        }
    )

    audit(
        db,
        user=None,
        action="general_schedule.publish",
        resource_type="event",
        resource_id=event.id,
        detail=json.dumps({
            "scope": payload.publish_scope,
            "dates": sorted(scoped_dates or []),
            "items": len(prepared_items),
            "category_rows": published_rows,
        }),
        request=request,
    )
    db.commit()
    return GeneralSchedulePublishResponse(status="ok", items_published=len(prepared_items))


@admin_router.get("/events/{event_id}/general-schedule")
def get_general_schedule_status(
    event_id: int,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    require_event_access(event_id, admin, db)

    state = (
        db.query(GeneralSchedulePublishState)
        .filter(GeneralSchedulePublishState.event_id == event_id)
        .first()
    )
    items = (
        db.query(PublishedGeneralScheduleItem)
        .filter(PublishedGeneralScheduleItem.event_id == event_id)
        .order_by(
            PublishedGeneralScheduleItem.date.asc(),
            PublishedGeneralScheduleItem.start_time.asc(),
            PublishedGeneralScheduleItem.sort_order.asc(),
        )
        .all()
    )
    categories = (
        db.query(PublishedGeneralScheduleCategory)
        .filter(PublishedGeneralScheduleCategory.event_id == event_id)
        .order_by(
            PublishedGeneralScheduleCategory.sort_order.asc(),
            PublishedGeneralScheduleCategory.name.asc(),
        )
        .all()
    )
    return {
        "event_id": event_id,
        "published_at": state.published_at.isoformat() if state and state.published_at else None,
        "fingerprint": state.fingerprint if state else None,
        "item_count": len(items),
        "categories": [
            {
                "id": category.external_category_id,
                "name": category.name,
                "sort_order": category.sort_order or 0,
            }
            for category in categories
        ],
        "schedule_views": [
            {
                "id": category.external_category_id,
                "name": category.name,
                "sort_order": category.sort_order or 0,
            }
            for category in categories
        ],
        "items": [_item_to_dict(item) for item in items],
    }
