"""
Publish endpoint  -  receives masterplan data from the desktop app.

Authentication: Bearer token matching an event's publish_secret_hash.
Strategy: full publish replaces the event schedule; date-scoped publish replaces only
the requested published days.
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.core.rate_limit import limiter, runtime_limit
from app.core.retention import materialise_event_purge_deadline
from app.core.audit import audit
from app.core.schedule_days import (
    event_schedule_day_range,
    merge_schedule_day_range,
    normalise_schedule_day_range,
    working_date_for_datetime,
)
from app.db.database import get_db
from app.models.event import Event
from app.models.published import (
    PublishedPerson,
    PublishedPersonUnavailability,
    PublishedTask,
    TaskEdit,
)
from app.models.user import User
from app.models.deletion import DeletionCase, DesktopDeletionWorkOrder
from app.core.deletion_cases import apply_desktop_report, claim_work_order
from app.core.publish_contract import (
    FieldPurpose,
    FieldType,
    FieldVisibility,
    PUBLISH_CONTRACT_VERSION,
    validate_published_field_value,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class AttendeeIn(BaseModel):
    """Published attendee received from the desktop app."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., max_length=256)
    person_id: int = Field(..., gt=0)


class PublishedFieldDefinitionIn(BaseModel):
    """Reviewed purpose and audience for one bounded published field."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str = Field(..., min_length=1, max_length=256)
    type: FieldType
    purpose: FieldPurpose
    visibility: FieldVisibility


class TaskIn(BaseModel):
    """Published task received from the desktop app."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., gt=0)
    name: str = Field(..., max_length=512)
    summary: Optional[str] = Field(None, max_length=2000)
    description: Optional[str] = Field(None, max_length=10000)
    start: str = Field(..., max_length=64)  # ISO datetime
    end: str = Field(..., max_length=64)    # ISO datetime
    location_name: Optional[str] = Field(None, max_length=512)
    location_address: Optional[str] = Field(None, max_length=1024)
    task_type_code: Optional[str] = Field(None, max_length=64)
    task_type_name: Optional[str] = Field(None, max_length=256)
    color: Optional[str] = Field(None, max_length=32)
    attendees: List[AttendeeIn] = Field(default_factory=list)
    field_assignments: Optional[Dict[str, List[AttendeeIn]]] = None
    field_values: Optional[Dict[str, Any]] = Field(None, max_length=100)
    field_definitions: Optional[List[PublishedFieldDefinitionIn]] = Field(None, max_length=100)
    sort_order: Optional[float] = 0

    @model_validator(mode="after")
    def reject_private_profiling(self):
        """Reject structured fields that the operational service must not hold."""

        _reject_prohibited_profile_fields(
            field_values=self.field_values,
            field_definitions=self.field_definitions,
            additional=None,
        )
        definitions = self.field_definitions or []
        definition_by_id = {definition.id: definition for definition in definitions}
        if len(definition_by_id) != len(definitions):
            raise ValueError("Published field identifiers must be unique")
        values = self.field_values or {}
        assignments = self.field_assignments or {}
        unknown = (set(values) | set(assignments)) - set(definition_by_id)
        if unknown:
            raise ValueError("Published values contain an unclassified field")
        for field_id, definition in definition_by_id.items():
            if definition.visibility == "never_publish" and (
                field_id in values or field_id in assignments
            ):
                raise ValueError("Fields marked never_publish must not cross the publish boundary")
            if field_id in values and not validate_published_field_value(
                definition.type, values[field_id]
            ):
                raise ValueError(f"Published field {field_id} does not match its declared type")
            if definition.type == "persons_list" and field_id in values:
                raise ValueError("persons_list data must use the structured assignment contract")
            if field_id in assignments and definition.type != "persons_list":
                raise ValueError("Only persons_list fields may contain published assignments")
        return self


class PersonIn(BaseModel):
    """Published person received from the desktop app."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., gt=0)
    first_name: str = Field(..., max_length=256)
    last_name: str = Field(..., max_length=256)
    email: Optional[str] = Field(None, max_length=512)
    evidence_subject_id: str = Field(
        ...,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    )


class EventMetaIn(BaseModel):
    """Published event metadata supplied by the desktop app."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, max_length=256)
    start_date: Optional[str] = Field(None, max_length=16)
    end_date: Optional[str] = Field(None, max_length=16)
    day_aliases: Optional[Dict[str, str]] = None  # {"2026-08-28": "Arrival Day"}
    schedule_day_range: Optional[Dict[str, int]] = None


class PersonUnavailabilityIn(BaseModel):
    """Published person-unavailability interval for one working day."""

    model_config = ConfigDict(extra="forbid")

    person_id: int = Field(..., gt=0)
    working_date: str = Field(..., max_length=10)
    start: str = Field(..., max_length=32)
    end: str = Field(..., max_length=32)


class PublishPayload(BaseModel):
    """Published schedule payload from the desktop app."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[PUBLISH_CONTRACT_VERSION]
    event: Optional[EventMetaIn] = None
    tasks: List[TaskIn]
    persons: List[PersonIn] = Field(default_factory=list)
    unavailabilities: List[PersonUnavailabilityIn] = Field(default_factory=list)
    publish_scope: Optional[Literal["full", "dates"]] = "full"
    dates: Optional[List[str]] = None


class PublishResponse(BaseModel):
    """Summary of rows created and edits cleared by a publish."""

    status: str
    tasks_created: int
    persons_created: int
    edits_cleared: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROHIBITED_PROFILE_TOKENS = {
    "allergies",
    "allergy",
    "criminal",
    "diagnosis",
    "diet",
    "dietary",
    "disciplinary",
    "disability",
    "ethnicity",
    "health",
    "medical",
    "political",
    "private_note",
    "private_profile",
    "religion",
    "religious",
    "safeguarding",
    "sexual_orientation",
    "trade_union",
}


def _normalised_field_tokens(value: str) -> set[str]:
    normalised = _normalised_field_name(value)
    return {token for token in normalised.split("_") if token}


def _normalised_field_name(value: str) -> str:
    return "".join(
        character.lower() if character.isalnum() else "_" for character in value
    )


def _prohibited_field_name(value: str) -> bool:
    normalised = _normalised_field_name(value)
    tokens = _normalised_field_tokens(value)
    return bool(tokens & _PROHIBITED_PROFILE_TOKENS) or any(
        phrase in normalised for phrase in _PROHIBITED_PROFILE_TOKENS if "_" in phrase
    )


def _walk_structured_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk_structured_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_structured_keys(child)


def _reject_prohibited_profile_fields(
    *,
    field_values: Optional[Dict[str, Any]],
    field_definitions: Optional[List[Dict[str, str]]],
    additional: Optional[Dict[str, Any]],
) -> None:
    candidates = list(_walk_structured_keys(field_values or {}))
    candidates.extend(_walk_structured_keys(additional or {}))
    for definition in field_definitions or []:
        if isinstance(definition, BaseModel):
            definition = definition.model_dump()
        for key in ("id", "key", "name", "label", "code"):
            value = definition.get(key)
            if value:
                candidates.append(value)
    if any(_prohibited_field_name(candidate) for candidate in candidates):
        raise ValueError(
            "Sensitive or unrelated private profiling fields are not supported"
        )

def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _ensure_aware_utc(value: datetime | None) -> datetime | None:
    """Return a timezone-aware UTC datetime for database values."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalise_scope_dates(dates: Optional[List[str]]) -> set[str]:
    """Validate and normalise date-scoped publish day ids."""
    if not dates:
        raise HTTPException(
            status_code=400,
            detail="Date-scoped publish requires at least one date.",
        )

    normalised: set[str] = set()
    for raw_date in dates:
        if not isinstance(raw_date, str):
            raise HTTPException(status_code=400, detail="Invalid publish date.")
        value = raw_date.strip()
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid publish date: {raw_date}",
            ) from None
        normalised.add(parsed.isoformat())
    return normalised


def _additional_date(additional_json: str | None) -> str | None:
    """Return the task date stored in additional_json if it is a valid ISO date."""
    if not additional_json:
        return None
    try:
        additional = json.loads(additional_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(additional, dict):
        return None
    raw_date = additional.get("date")
    if not isinstance(raw_date, str):
        return None
    try:
        return datetime.strptime(raw_date, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _published_task_day(
    task: PublishedTask,
    schedule_day_range: dict[str, int],
) -> str:
    """Resolve the published day for an existing task."""
    return working_date_for_datetime(task.start_datetime, schedule_day_range)


def _payload_task_day(task: TaskIn, schedule_day_range: dict[str, int]) -> str:
    """Resolve the published day for an incoming task payload."""
    try:
        return working_date_for_datetime(
            datetime.fromisoformat(task.start),
            schedule_day_range,
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid task start datetime for task {task.id}",
        ) from None


def _insert_person(
    person_in: PersonIn,
    event_id: int,
    db: Session,
) -> None:
    """Insert one published person."""
    db.add(PublishedPerson(
        event_id=event_id,
        external_person_id=person_in.id,
        evidence_subject_id=person_in.evidence_subject_id,
        first_name=person_in.first_name,
        last_name=person_in.last_name,
        email=person_in.email,
    ))


def _upsert_person(person_in: PersonIn, event_id: int, db: Session) -> None:
    """Insert or update one published person without deleting unrelated people."""
    existing = (
        db.query(PublishedPerson)
        .filter(
            PublishedPerson.event_id == event_id,
            PublishedPerson.external_person_id == person_in.id,
        )
        .first()
    )
    if existing is None:
        _insert_person(person_in, event_id, db)
        return
    if person_in.evidence_subject_id and person_in.evidence_subject_id != existing.evidence_subject_id:
        raise HTTPException(status_code=409, detail="A person's evidence identifier is immutable.")
    existing.first_name = person_in.first_name
    existing.last_name = person_in.last_name
    existing.email = person_in.email


def _insert_task(task_in: TaskIn, event_id: int, db: Session) -> None:
    """Insert one published task from a desktop publish payload."""
    attendees_data = [a.model_dump() for a in task_in.attendees]
    field_assignments_data = None
    if task_in.field_assignments:
        field_assignments_data = {
            k: [a.model_dump() for a in v]
            for k, v in task_in.field_assignments.items()
        }

    try:
        start_datetime = datetime.fromisoformat(task_in.start)
        end_datetime = datetime.fromisoformat(task_in.end)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid task datetime for task {task_in.id}",
        ) from None

    db.add(PublishedTask(
        event_id=event_id,
        external_task_id=task_in.id,
        name=task_in.name,
        summary=task_in.summary,
        description=task_in.description,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        location_name=task_in.location_name,
        location_address=task_in.location_address,
        task_type_code=task_in.task_type_code,
        task_type_name=task_in.task_type_name,
        color=task_in.color,
        attendees_json=json.dumps(attendees_data) if attendees_data else None,
        field_assignments_json=json.dumps(field_assignments_data) if field_assignments_data else None,
        field_values_json=json.dumps(task_in.field_values) if task_in.field_values else None,
        field_definitions_json=json.dumps([
            definition.model_dump() for definition in task_in.field_definitions
        ]) if task_in.field_definitions else None,
        additional_json=None,
        sort_order=task_in.sort_order,
    ))


def _authenticate_event(request: Request, db: Session) -> Event:
    """Authenticate via Bearer token matching an event's publish_secret_hash."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    secret = auth_header[7:].strip()
    if not secret:
        raise HTTPException(status_code=401, detail="Empty Bearer token")

    secret_hash = _hash_secret(secret)
    event = (
        db.query(Event)
        .filter(Event.publish_secret_hash == secret_hash)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=401, detail="Invalid publish secret")

    # Check secret rotation policy
    from app.core import runtime_settings
    max_age = runtime_settings.get_int("secret_max_age_days", db)
    if max_age > 0 and event.secret_created_at:
        created_at = _ensure_aware_utc(event.secret_created_at)
        age_days = (datetime.now(timezone.utc) - created_at).days
        if age_days > max_age:
            raise HTTPException(
                status_code=401,
                detail="Publish secret has expired - please regenerate in the admin panel",
            )

    return event


def _require_publishing_allowed(event: Event) -> None:
    """Keep the credential usable for deletion sync but block new live data."""

    if event.purge_case_request_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EVENT_PURGE_IN_PROGRESS",
                "message": "Publishing is disabled because the event deletion workflow has started.",
            },
        )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/publish", response_model=PublishResponse)
@limiter.limit(runtime_limit("masterplan_pushes_per_minute"))
def publish(
    payload: PublishPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    """Receive published masterplan data from the desktop app.

    Full publish replaces the event's published data.
    Date-scoped publish replaces only the requested published days.
    """
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
    publish_scope = payload.publish_scope or "full"
    scoped_dates = (
        _normalise_scope_dates(payload.dates)
        if publish_scope == "dates"
        else None
    )

    if scoped_dates is not None:
        for task_in in payload.tasks:
            if _payload_task_day(task_in, schedule_day_range) not in scoped_dates:
                raise HTTPException(
                    status_code=400,
                    detail=f"Task {task_in.id} is outside the requested publish dates.",
                )

    # Update event metadata if provided
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
            # Store day_aliases in event metadata_json
            existing_meta = json.loads(event.metadata_json) if event.metadata_json else {}
            existing_meta["day_aliases"] = payload.event.day_aliases
            event.metadata_json = json.dumps(existing_meta)
        if payload.event.schedule_day_range is not None:
            event.metadata_json = merge_schedule_day_range(
                event.metadata_json,
                schedule_day_range,
            )
        event.status = "published"

    # -----------------------------------------------------------------------
    # Capture old state for per-person diff (before delete-and-replace)
    # -----------------------------------------------------------------------
    old_tasks = (
        db.query(PublishedTask)
        .filter(PublishedTask.event_id == event.id)
        .all()
    )
    old_edits_map = {}
    if old_tasks:
        old_task_ids = [t.id for t in old_tasks]
        old_edits = db.query(TaskEdit).filter(TaskEdit.task_id.in_(old_task_ids)).all()
        old_edits_map = {e.task_id: e for e in old_edits}
        # Detach old tasks from session so they survive the delete below
        for t in old_tasks:
            db.expunge(t)
        for e in old_edits:
            db.expunge(e)

    # Delete existing published data + edits for this event/scope.
    existing_tasks_query = (
        db.query(PublishedTask)
        .filter(PublishedTask.event_id == event.id)
    )
    if scoped_dates is None:
        existing_task_ids = [task.id for task in existing_tasks_query.all()]
    else:
        existing_task_ids = [
            task.id
            for task in existing_tasks_query.all()
            if _published_task_day(task, schedule_day_range) in scoped_dates
        ]
    if existing_task_ids:
        edits_cleared = db.query(TaskEdit).filter(
            TaskEdit.task_id.in_(existing_task_ids),
        ).delete(synchronize_session=False)
    else:
        edits_cleared = 0

    if existing_task_ids:
        db.query(PublishedTask).filter(
            PublishedTask.id.in_(existing_task_ids),
        ).delete(synchronize_session=False)

    if scoped_dates is None:
        db.query(PublishedPerson).filter(
            PublishedPerson.event_id == event.id,
        ).delete(synchronize_session=False)

    # Insert persons
    for person_in in payload.persons:
        if scoped_dates is None:
            _insert_person(person_in, event.id, db)
        else:
            _upsert_person(person_in, event.id, db)

    availability_query = db.query(PublishedPersonUnavailability).filter(
        PublishedPersonUnavailability.event_id == event.id,
    )
    if scoped_dates is None:
        availability_query.delete(synchronize_session=False)
    else:
        availability_query.filter(
            PublishedPersonUnavailability.working_date.in_(scoped_dates),
        ).delete(synchronize_session=False)

    valid_person_ids = {person.id for person in payload.persons}
    seen_intervals: set[tuple[int, str, str, str]] = set()
    for interval in payload.unavailabilities:
        try:
            working_date = datetime.strptime(interval.working_date, "%Y-%m-%d").date().isoformat()
            start = datetime.fromisoformat(interval.start)
            end = datetime.fromisoformat(interval.end)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid unavailability interval.") from None
        if interval.person_id not in valid_person_ids:
            raise HTTPException(status_code=400, detail="Unavailable person is not part of this event.")
        if scoped_dates is not None and working_date not in scoped_dates:
            raise HTTPException(status_code=400, detail="Unavailability is outside the requested publish dates.")
        if end <= start:
            raise HTTPException(status_code=400, detail="Unavailability end must be after its start.")
        key = (interval.person_id, working_date, start.isoformat(), end.isoformat())
        if key in seen_intervals:
            continue
        seen_intervals.add(key)
        db.add(PublishedPersonUnavailability(
            event_id=event.id,
            external_person_id=interval.person_id,
            working_date=working_date,
            start_datetime=start.isoformat(),
            end_datetime=end.isoformat(),
        ))

    # Insert tasks
    for task_in in payload.tasks:
        _insert_task(task_in, event.id, db)

    db.flush()

    # Auto-link users to persons by matching email
    _auto_link_users_by_email(event.id, db)

    # -----------------------------------------------------------------------
    # Compute per-person diffs and store change records
    # -----------------------------------------------------------------------
    try:
        from app.core.diff import compute_per_person_diffs, store_schedule_changes
        new_tasks = (
            db.query(PublishedTask)
            .filter(PublishedTask.event_id == event.id)
            .all()
        )
        new_task_ids = [task.id for task in new_tasks]
        new_edits_map = {}
        if new_task_ids:
            new_edits = db.query(TaskEdit).filter(TaskEdit.task_id.in_(new_task_ids)).all()
            new_edits_map = {edit.task_id: edit for edit in new_edits}
        diffs = compute_per_person_diffs(
            old_tasks,
            old_edits_map,
            new_tasks,
            new_edits_map,
        )
        store_schedule_changes(event.id, diffs, db)
    except Exception as exc:
        logger.warning("Schedule diff generation failed (%s)", type(exc).__name__)

    # Snapshot the full published state after applying this publish.
    from app.core.snapshots import create_snapshot
    create_snapshot(event, db, source="Publish Secret")

    db.commit()

    audit(db, user=None, action="publish.data", resource_type="event",
          resource_id=event.id, detail=json.dumps({
              "scope": publish_scope,
              "tasks": len(payload.tasks),
          }), request=request)
    db.commit()

    # Send push notification to all subscribers of this event
    try:
        from app.core.push import send_push_to_event
        send_push_to_event(
            event_id=event.id,
            title="Schedule Updated",
            body=f"{event.name} schedule has been republished.",
            url=f"/calendar?event={event.id}",
            db=db,
            notification_type="schedule",
        )
    except Exception as exc:
        logger.warning("Publish push delivery failed (%s)", type(exc).__name__)

    return PublishResponse(
        status="ok",
        tasks_created=len(payload.tasks),
        persons_created=len(payload.persons),
        edits_cleared=edits_cleared,
    )


def _auto_link_users_by_email(event_id: int, db: Session) -> None:
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
    email_to_person = {p.email.lower(): p.external_person_id for p in persons if p.email}

    for user in users:
        person_id = email_to_person.get(user.email.lower())
        if person_id is not None:
            user.linked_person_id = person_id


# ---------------------------------------------------------------------------
# Ping endpoint
# ---------------------------------------------------------------------------

class PingResponse(BaseModel):
    """Publish credential health-check response."""

    status: str
    event_name: str
    event_id: int
    event_ref: str
    supports_scoped_publish: bool = True
    supports_deletion_work_orders: bool = True


@router.get("/ping", response_model=PingResponse)
@limiter.limit("20/minute")
def ping(
    request: Request,
    db: Session = Depends(get_db),
):
    """Health check for the desktop app. Validates the Bearer token."""
    event = _authenticate_event(request, db)
    return PingResponse(
        status="ok",
        event_name=event.name,
        event_id=event.id,
        event_ref=event.evidence_id,
        supports_scoped_publish=True,
        supports_deletion_work_orders=True,
    )


# ---------------------------------------------------------------------------
# Strict desktop deletion work orders
# ---------------------------------------------------------------------------

class DesktopDeletionCounts(BaseModel):
    """Bounded deletion counters that contain no personal values."""

    model_config = ConfigDict(extra="forbid")

    persons: int = Field(ge=0)
    assignments: int = Field(ge=0)
    capability_links: int = Field(ge=0)
    group_memberships: int = Field(ge=0)
    unavailability_intervals: int = Field(ge=0)
    task_references: int = Field(ge=0)
    optimisation_records: int = Field(ge=0)
    publish_records: int = Field(ge=0)
    cached_records: int = Field(ge=0)
    tracked_exports: int = Field(ge=0)
    integration_references: int = Field(ge=0)


class DesktopDeletionReportIn(BaseModel):
    """Current desktop deletion report contract."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    work_order_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    event_ref: str = Field(pattern=r"^[0-9a-f-]{36}$")
    subject_ref: Optional[str] = Field(None, pattern=r"^[0-9a-f-]{36}$")
    operation: Literal["delete_subject", "delete_event"]
    outcome: Literal["deleted"]
    deleted_counts: DesktopDeletionCounts
    outstanding_actions: List[
        Literal["untracked_external_export", "external_integration_copy"]
    ] = Field(default_factory=list)
    completed_at: str = Field(max_length=40)


def _desktop_work_order_detail(work_order: DesktopDeletionWorkOrder) -> dict:
    """Return the pseudonymous fields required by the paired desktop."""

    return {
        "version": 1,
        "work_order_id": work_order.work_order_id,
        "event_ref": work_order.event_ref,
        "subject_ref": work_order.subject_ref,
        "operation": work_order.operation,
        "state": work_order.state,
        "created_at": work_order.created_at,
        "claimed_at": work_order.claimed_at,
        "claim_expires_at": work_order.claim_expires_at,
        "reported_at": work_order.reported_at,
        "report_sha256": work_order.report_sha256,
    }


@router.get("/deletion-work-orders")
@limiter.limit("20/minute")
def list_desktop_deletion_work_orders(
    request: Request,
    db: Session = Depends(get_db),
):
    """List current deletion work orders for the authenticated event."""

    event = _authenticate_event(request, db)
    work_orders = db.query(DesktopDeletionWorkOrder).filter(
        DesktopDeletionWorkOrder.event_id == event.id,
        DesktopDeletionWorkOrder.state.in_({"open", "claimed", "report_received"}),
    ).order_by(DesktopDeletionWorkOrder.id).all()
    return [_desktop_work_order_detail(work_order) for work_order in work_orders]


@router.post("/deletion-work-orders/{work_order_id}/claim")
@limiter.limit("10/minute")
def claim_desktop_deletion_work_order(
    work_order_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Claim one work order and reveal a short-lived report capability once."""

    event = _authenticate_event(request, db)
    work_order = db.query(DesktopDeletionWorkOrder).filter(
        DesktopDeletionWorkOrder.work_order_id == work_order_id,
        DesktopDeletionWorkOrder.event_id == event.id,
    ).first()
    if work_order is None:
        raise HTTPException(status_code=404, detail="Deletion work order not found")
    try:
        capability = claim_work_order(work_order)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return {
        **_desktop_work_order_detail(work_order),
        "claim_capability": capability,
    }


@router.post("/deletion-work-orders/{work_order_id}/report")
@limiter.limit("20/minute")
def report_desktop_deletion_work_order(
    work_order_id: str,
    body: DesktopDeletionReportIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """Record an idempotent deletion report from the authenticated desktop."""

    event = _authenticate_event(request, db)
    work_order = db.query(DesktopDeletionWorkOrder).filter(
        DesktopDeletionWorkOrder.work_order_id == work_order_id,
        DesktopDeletionWorkOrder.event_id == event.id,
    ).first()
    if work_order is None:
        raise HTTPException(status_code=404, detail="Deletion work order not found")
    case = db.query(DeletionCase).filter(
        DeletionCase.id == work_order.case_id,
    ).first()
    if case is None:
        raise HTTPException(status_code=409, detail="Deletion case no longer exists")
    capability = request.headers.get("x-deletion-claim", "")
    try:
        digest = apply_desktop_report(
            db,
            case,
            work_order,
            claim_capability=capability,
            report=body.model_dump(mode="json"),
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return {
        "status": "recorded",
        "work_order_id": work_order.work_order_id,
        "report_sha256": digest,
        "case_state": case.state,
    }
