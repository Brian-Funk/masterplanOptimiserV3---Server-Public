"""
Calendar endpoints  -  serve published tasks with optional edit overlay.
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_event_access
from app.core.web_edits import get_task_web_edit_metadata
from app.core.rate_limit import limiter
from app.core.audit import audit
from app.core.governance import (
    current_policy_identity,
    current_policy_template_version,
    has_data_policy_acknowledgement,
    require_data_policy_acknowledgement,
)
from app.core.governance_rendering import POLICY_TEMPLATE_VERSION
from app.core.schedule_days import (
    event_schedule_day_range,
    working_date_for_clock,
    working_date_for_datetime,
)
from app.db.database import get_db
from app.models.event import Event
from app.models.published import (
    GeneralSchedulePublishState,
    PublishedGeneralScheduleCategory,
    PublishedGeneralScheduleItem,
    PublishedTask,
    PublishedPerson,
    PublishedPersonUnavailability,
    TaskEdit,
)
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class AttendeeOut(BaseModel):
    """Calendar attendee shown on a published task."""

    name: str = Field(..., min_length=1, max_length=256)
    person_id: int = Field(..., gt=0)


class TaskOut(BaseModel):
    """Published task representation returned to calendar clients."""

    id: int
    external_task_id: int
    name: str
    summary: Optional[str] = None
    description: Optional[str] = None
    start: str  # ISO datetime
    end: str    # ISO datetime
    working_date: str
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    task_type_code: Optional[str] = None
    task_type_name: Optional[str] = None
    color: Optional[str] = None
    attendees: List[AttendeeOut] = []
    field_assignments: Optional[Dict[str, List[AttendeeOut]]] = None
    field_values: Optional[Dict[str, Any]] = None
    field_definitions: Optional[List[Dict[str, str]]] = None
    additional: Optional[Dict[str, Any]] = None
    sort_order: float = 0
    has_web_edit: bool = False
    web_edit_edited_at: Optional[str] = None
    web_edit_edited_by: Optional[str] = None
    web_edit_edited_by_user_id: Optional[int] = None
    web_edit_change_summary: List[str] = []


class PersonOut(BaseModel):
    """Published person visible in the event calendar."""

    id: int
    external_person_id: int
    first_name: str
    last_name: str


class PublicScheduleItemOut(BaseModel):
    """Published public General Schedule item shown in the calendar."""

    id: int
    external_session_element_id: int
    title: str
    date: str
    start_time: str
    end_time: str
    working_date: str
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    responsible: Optional[str] = None
    audience_teams: List[Dict[str, Any]] = []
    description: Optional[str] = None
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    type_id: Optional[int] = None
    type_name: Optional[str] = None
    copy_template_html: Optional[str] = None
    category: Optional[str] = None
    colour: Optional[str] = None
    sort_order: float = 0


class PublicScheduleCategoryOut(BaseModel):
    """Published General Schedule audience category shown as a calendar view."""

    id: int
    name: str
    sort_order: float = 0


class GeneralScheduleStateOut(BaseModel):
    """Latest General Schedule publish state for the calendar."""

    published_at: Optional[str] = None
    fingerprint: Optional[str] = None
    item_count: int = 0


class PersonUnavailabilityOut(BaseModel):
    """Published person unavailability returned to authenticated calendars."""

    person_id: int
    working_date: str
    start: str
    end: str


class CalendarResponse(BaseModel):
    """Calendar payload for one event and user role."""

    event_id: int
    event_name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    logo_color_1: Optional[str] = None
    logo_color_2: Optional[str] = None
    day_aliases: Optional[Dict[str, str]] = None
    schedule_day_range: Dict[str, int]
    tasks: List[TaskOut]
    persons: List[PersonOut]
    public_schedule_categories: List[PublicScheduleCategoryOut] = []
    public_schedule_views: List[PublicScheduleCategoryOut] = []
    public_schedule_items: List[PublicScheduleItemOut] = []
    general_schedule_state: Optional[GeneralScheduleStateOut] = None
    unavailabilities: List[PersonUnavailabilityOut] = []
    data_policy_version: Optional[int] = None
    data_policy_sha256: Optional[str] = None
    data_policy_acknowledged: bool = True


class OfflineTaskOut(BaseModel):
    """Authenticated-event task contract approved for browser persistence."""

    id: int
    external_task_id: int
    name: str
    summary: Optional[str] = None
    description: Optional[str] = None
    start: str
    end: str
    working_date: str
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    task_type_code: Optional[str] = None
    task_type_name: Optional[str] = None
    color: Optional[str] = None
    attendees: List[AttendeeOut] = []
    field_assignments: Optional[Dict[str, List[AttendeeOut]]] = None
    field_values: Optional[Dict[str, Any]] = None
    field_definitions: Optional[List[Dict[str, str]]] = None
    additional: None = None
    sort_order: float = 0
    has_web_edit: bool = False
    web_edit_edited_at: None = None
    web_edit_edited_by: None = None
    web_edit_edited_by_user_id: None = None
    web_edit_change_summary: List[str] = []


class OfflineAudienceTeamOut(BaseModel):
    """The deliberately small audience identity retained on a device."""

    name: str
    short_name: Optional[str] = None


class OfflinePublicScheduleItemOut(BaseModel):
    """Public programme fields approved for authenticated offline storage."""

    id: int
    external_session_element_id: int
    title: str
    date: str
    working_date: str
    start_time: str
    end_time: str
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    responsible: Optional[str] = None
    audience_teams: List[OfflineAudienceTeamOut] = []
    description: Optional[str] = None
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    type_name: Optional[str] = None
    colour: Optional[str] = None
    sort_order: float = 0


class OfflineCalendarResponse(BaseModel):
    """Fail-closed calendar contract that may be retained in IndexedDB."""

    event_id: int
    event_name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    day_aliases: Optional[Dict[str, str]] = None
    schedule_day_range: Dict[str, int]
    tasks: List[OfflineTaskOut]
    persons: List[PersonOut]
    public_schedule_categories: List[PublicScheduleCategoryOut] = []
    public_schedule_views: List[PublicScheduleCategoryOut] = []
    public_schedule_items: List[OfflinePublicScheduleItemOut] = []
    unavailabilities: List[PersonUnavailabilityOut] = []
    data_policy_version: Optional[int] = None
    data_policy_sha256: Optional[str] = None
    data_policy_acknowledged: bool = True


class TaskEditIn(BaseModel):
    """Single draft edit payload for an existing task."""

    start: Optional[str] = None   # ISO datetime
    end: Optional[str] = None     # ISO datetime
    attendees: Optional[List[AttendeeOut]] = Field(None, max_length=500)
    field_assignments: Optional[Dict[str, List[AttendeeOut]]] = Field(
        None,
        max_length=100,
    )


class BatchEditItem(BaseModel):
    """Batch item containing one task edit."""

    task_id: int = Field(..., gt=0)
    name: Optional[str] = Field(None, max_length=512)
    summary: Optional[str] = Field(None, max_length=2000)
    description: Optional[str] = Field(None, max_length=10000)
    start: Optional[str] = Field(None, max_length=64)
    end: Optional[str] = Field(None, max_length=64)
    location_name: Optional[str] = Field(None, max_length=512)
    location_address: Optional[str] = Field(None, max_length=1024)
    color: Optional[str] = Field(None, max_length=32)
    attendees: Optional[List[AttendeeOut]] = Field(None, max_length=500)
    field_assignments: Optional[Dict[str, List[AttendeeOut]]] = Field(
        None,
        max_length=100,
    )
    field_values: Optional[Dict[str, Any]] = Field(None, max_length=100)


class BatchCreateItem(BaseModel):
    """Batch item containing one new draft task."""

    name: str = Field(..., min_length=1, max_length=512)
    summary: Optional[str] = Field(None, max_length=2000)
    description: Optional[str] = Field(None, max_length=10000)
    start: str = Field(..., max_length=64)
    end: str = Field(..., max_length=64)
    location_name: Optional[str] = Field(None, max_length=512)
    location_address: Optional[str] = Field(None, max_length=1024)
    color: Optional[str] = Field(None, max_length=32)
    attendees: Optional[List[AttendeeOut]] = Field(None, max_length=500)
    field_assignments: Optional[Dict[str, List[AttendeeOut]]] = Field(
        None,
        max_length=100,
    )
    field_values: Optional[Dict[str, Any]] = Field(None, max_length=100)


class BatchCommitRequest(BaseModel):
    """Request body for committing draft edits and created tasks."""

    edits: List[BatchEditItem] = Field(default_factory=list, max_length=500)
    deletions: List[int] = Field(default_factory=list, max_length=500)
    creations: List[BatchCreateItem] = Field(default_factory=list, max_length=500)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json(raw: Optional[str]) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _serialise_field_assignments(
    assignments: Dict[str, List[AttendeeOut]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Convert structured assignment buckets into JSON-safe dictionaries."""

    return {
        field_id: [attendee.model_dump() for attendee in attendees]
        for field_id, attendees in assignments.items()
    }


def _flatten_field_assignments(
    assignments: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Flatten already-validated assignment buckets in their published order."""

    flattened: List[Dict[str, Any]] = []
    for raw_attendees in assignments.values():
        if not isinstance(raw_attendees, list):
            continue
        for raw_attendee in raw_attendees:
            if isinstance(raw_attendee, AttendeeOut):
                attendee = raw_attendee
            elif isinstance(raw_attendee, dict):
                candidate = dict(raw_attendee)
                if "person_id" not in candidate and "id" in candidate:
                    candidate["person_id"] = candidate["id"]
                try:
                    attendee = AttendeeOut.model_validate(candidate)
                except (TypeError, ValueError):
                    continue
            else:
                continue
            flattened.append(attendee.model_dump())
    return flattened


def _require_unique_assignment_people(assignments: Dict[str, Any]) -> None:
    """Enforce one allocation category per person for an effective task edit."""

    assigned_people: set[int] = set()
    for raw_attendees in assignments.values():
        if not isinstance(raw_attendees, list):
            raise HTTPException(status_code=422, detail="A task assignment field is invalid")
        for raw_attendee in raw_attendees:
            if isinstance(raw_attendee, AttendeeOut):
                person_id = raw_attendee.person_id
            elif isinstance(raw_attendee, dict):
                person_id = raw_attendee.get("person_id", raw_attendee.get("id"))
            else:
                raise HTTPException(status_code=422, detail="A task assignment is invalid")
            if not isinstance(person_id, int):
                raise HTTPException(status_code=422, detail="A task assignment is invalid")
            if person_id in assigned_people:
                raise HTTPException(
                    status_code=422,
                    detail="A person may be allocated only once across a task's assignment fields",
                )
            assigned_people.add(person_id)


_HEX_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _canonical_attendees(
    event_id: int,
    attendees: List[AttendeeOut],
    db: Session,
) -> List[AttendeeOut]:
    """Resolve submitted person IDs to canonical event-scoped names."""
    person_ids = [attendee.person_id for attendee in attendees]
    if len(person_ids) != len(set(person_ids)):
        raise HTTPException(status_code=422, detail="Duplicate assigned person")
    people = (
        db.query(PublishedPerson)
        .filter(
            PublishedPerson.event_id == event_id,
            PublishedPerson.external_person_id.in_(person_ids),
        )
        .all()
        if person_ids
        else []
    )
    by_id = {person.external_person_id: person for person in people}
    if len(by_id) != len(person_ids):
        raise HTTPException(status_code=422, detail="Assigned person is not part of this event")
    return [
        AttendeeOut(
            person_id=person_id,
            name=f"{by_id[person_id].first_name} {by_id[person_id].last_name}".strip(),
        )
        for person_id in person_ids
    ]


def _effective_json(task: PublishedTask, edit: TaskEdit | None, field: str) -> Any:
    """Return an edited JSON field when present, otherwise its published value."""
    edited_raw = getattr(edit, field, None) if edit is not None else None
    return _parse_json(edited_raw if edited_raw is not None else getattr(task, field))


def _assignment_field_ids(task: PublishedTask) -> List[str]:
    """Return structured assignment field IDs in their published order."""
    definitions = _parse_json(task.field_definitions_json)
    if isinstance(definitions, list):
        ids = [
            str(definition.get("id"))
            for definition in definitions
            if isinstance(definition, dict)
            and definition.get("id") is not None
            and definition.get("type") == "persons_list"
        ]
        if ids:
            return ids
    existing = _parse_json(task.field_assignments_json)
    return list(existing) if isinstance(existing, dict) else []


def _merge_field_assignments(
    event_id: int,
    task: PublishedTask,
    edit: TaskEdit | None,
    incoming: Dict[str, List[AttendeeOut]],
    db: Session,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Patch known assignment buckets while preserving every untouched bucket."""
    allowed = _assignment_field_ids(task)
    unknown = set(incoming) - set(allowed)
    if unknown or not allowed:
        raise HTTPException(status_code=422, detail="Unknown assignment field")
    current = _effective_json(task, edit, "field_assignments_json")
    merged = dict(current) if isinstance(current, dict) else {}
    for field_id, attendees in incoming.items():
        merged[field_id] = [
            attendee.model_dump()
            for attendee in _canonical_attendees(event_id, attendees, db)
        ]

    effective = {}
    effective.update(merged)
    for field_id in allowed:
        if field_id not in effective:
            merged.setdefault(field_id, [])
            effective[field_id] = merged[field_id]
    _require_unique_assignment_people(effective)
    return merged, effective


def _validate_link_value(value: Any) -> Any:
    """Validate one editable task-template link value."""
    if isinstance(value, str):
        url = value.strip()
        text = ""
    elif isinstance(value, dict):
        url = str(value.get("url", "")).strip()
        text = str(value.get("text", ""))[:512]
    else:
        raise HTTPException(status_code=422, detail="Invalid link field")
    if not url:
        return {"url": "", "text": text}
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or len(url) > 2048:
        raise HTTPException(status_code=422, detail="Link fields require an HTTP or HTTPS URL")
    return {"url": url, "text": text}


def _merge_field_values(
    task: PublishedTask,
    edit: TaskEdit | None,
    incoming: Dict[str, Any],
) -> Dict[str, Any]:
    """Allow edits to declared link fields while preserving hidden field values."""
    definitions = _parse_json(task.field_definitions_json)
    definition_types = {
        str(definition.get("id")): definition.get("type")
        for definition in definitions or []
        if isinstance(definition, dict) and definition.get("id") is not None
    }
    current = _effective_json(task, edit, "field_values_json")
    merged = dict(current) if isinstance(current, dict) else {}
    for field_id, value in incoming.items():
        field_type = definition_types.get(field_id)
        if field_type is None:
            raise HTTPException(status_code=422, detail="Unknown task field")
        if field_type != "link":
            if value != merged.get(field_id):
                raise HTTPException(status_code=422, detail="Task field is not editable on the web")
            continue
        merged[field_id] = _validate_link_value(value)
    return merged


def _validate_effective_task_values(
    *,
    start: str | None,
    end: str | None,
    fallback_start: datetime,
    fallback_end: datetime,
    colour: str | None = None,
) -> tuple[datetime, datetime]:
    """Parse task times, enforce ordering, and validate an optional colour."""
    try:
        parsed_start = datetime.fromisoformat(start) if start is not None else fallback_start
        parsed_end = datetime.fromisoformat(end) if end is not None else fallback_end
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid task datetime") from exc
    if (parsed_start.utcoffset() is None) != (parsed_end.utcoffset() is None):
        raise HTTPException(
            status_code=422,
            detail="Task datetimes must use the same timezone format",
        )
    if parsed_end <= parsed_start:
        raise HTTPException(status_code=422, detail="Task end must be after its start")
    if colour is not None and not _HEX_COLOUR.fullmatch(colour):
        raise HTTPException(status_code=422, detail="Invalid task colour")
    return parsed_start, parsed_end


def _task_to_out(
    task: PublishedTask,
    edit: Optional[TaskEdit] = None,
    editor_name: Optional[str] = None,
    include_web_edit_details: bool = True,
    include_management_details: bool = True,
    include_all_authenticated_fields: bool = True,
    schedule_day_range: Optional[Dict[str, int]] = None,
) -> Optional[TaskOut]:
    """Convert a PublishedTask (+ optional edit overlay) to the API response.
    Returns None if the task is marked as deleted via edit."""
    if edit and edit.is_deleted:
        return None

    attendees = _parse_json(task.attendees_json) or []
    field_assignments = _parse_json(task.field_assignments_json)
    field_values = _parse_json(task.field_values_json)
    field_definitions = _parse_json(task.field_definitions_json)
    additional = _parse_json(task.additional_json)

    name = task.name
    summary = task.summary
    description = task.description
    start = task.start_datetime.isoformat()
    end = task.end_datetime.isoformat()
    location_name = task.location_name
    location_address = task.location_address
    color = task.color
    web_edit_metadata = get_task_web_edit_metadata(
        task,
        edit,
        editor_name=editor_name,
    )
    if not include_web_edit_details and web_edit_metadata["has_web_edit"]:
        web_edit_metadata.update(
            {
                "web_edit_edited_at": None,
                "web_edit_edited_by": None,
                "web_edit_edited_by_user_id": None,
                "web_edit_change_summary": [],
            }
        )

    # Layer web edits on top
    if edit:
        if edit.name is not None:
            name = edit.name
        if edit.summary is not None:
            summary = edit.summary
        if edit.description is not None:
            description = edit.description
        if edit.start_datetime:
            start = edit.start_datetime.isoformat()
        if edit.end_datetime:
            end = edit.end_datetime.isoformat()
        if edit.location_name is not None:
            location_name = edit.location_name
        if edit.location_address is not None:
            location_address = edit.location_address
        if edit.color is not None:
            color = edit.color
        if edit.attendees_json:
            parsed_attendees = _parse_json(edit.attendees_json)
            if parsed_attendees is not None:
                attendees = parsed_attendees
        if edit.field_assignments_json:
            parsed_field_assignments = _parse_json(edit.field_assignments_json)
            if parsed_field_assignments is not None:
                field_assignments = parsed_field_assignments
        if edit.field_values_json:
            parsed_field_values = _parse_json(edit.field_values_json)
            if parsed_field_values is not None:
                field_values = parsed_field_values

    # Both historical authenticated visibility values now describe the same
    # event-member audience. ``public`` remains readable to authenticated users;
    # unauthenticated schedules use their separate public contract.
    allowed_visibilities = (
        {"participant", "organiser", "public"}
        if include_all_authenticated_fields
        else {"participant"}
    )
    visible_definitions: list[dict[str, str]] = []
    for definition in field_definitions or []:
        if not isinstance(definition, dict) or not isinstance(definition.get("id"), str):
            continue
        normalised = dict(definition)
        if normalised.get("visibility") in allowed_visibilities:
            visible_definitions.append(normalised)
    visible_field_ids = {definition["id"] for definition in visible_definitions}
    visible_values = (
        {
            field_id: value
            for field_id, value in field_values.items()
            if field_id in visible_field_ids
        }
        if isinstance(field_values, dict)
        else {}
    )
    visible_assignments = (
        {
            field_id: values
            for field_id, values in field_assignments.items()
            if field_id in visible_field_ids and isinstance(values, list)
        }
        if isinstance(field_assignments, dict)
        else {}
    )

    return TaskOut(
        id=task.id,
        external_task_id=task.external_task_id,
        name=name,
        summary=summary,
        description=description,
        start=start,
        end=end,
        working_date=working_date_for_datetime(
            datetime.fromisoformat(start),
            schedule_day_range or {"start_hour": 6, "end_hour": 24},
        ),
        location_name=location_name,
        location_address=location_address,
        task_type_code=task.task_type_code,
        task_type_name=task.task_type_name,
        color=color,
        attendees=[AttendeeOut(**a) for a in attendees],
        field_assignments={
            k: [AttendeeOut(**a) for a in v]
            for k, v in visible_assignments.items()
        } if visible_assignments else None,
        field_values=visible_values or None,
        field_definitions=visible_definitions or None,
        additional=additional if include_management_details else None,
        sort_order=task.sort_order or 0,
        **web_edit_metadata,
    )



def _general_schedule_item_to_out(
    item: PublishedGeneralScheduleItem,
    schedule_day_range: Dict[str, int],
) -> PublicScheduleItemOut:
    teams: list[dict[str, Any]] = []
    if item.audience_teams_json:
        try:
            parsed = json.loads(item.audience_teams_json)
            teams = parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            teams = []
    return PublicScheduleItemOut(
        id=item.id,
        external_session_element_id=item.external_session_element_id,
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
        audience_teams=teams,
        description=item.description,
        category_id=item.category_id,
        category_name=item.category_name,
        type_id=item.type_id,
        type_name=item.type_name,
        copy_template_html=item.copy_template_html,
        category=item.category,
        colour=item.colour,
        sort_order=item.sort_order or 0,
    )
def _check_event_access(event_id: int, user: User, db: Session) -> Event:
    """Verify user has access to the event."""
    return require_event_access(event_id, user, db)


# ---------------------------------------------------------------------------
# Calendar response builders
# ---------------------------------------------------------------------------

def _build_calendar_response(
    event_id: int,
    current_user: User,
    db: Session,
    *,
    restrict_identity_directory: bool = False,
    restrict_task_assignments: bool = False,
) -> CalendarResponse:
    """Build an authenticated calendar without conflating content and permissions."""
    event = _check_event_access(event_id, current_user, db)
    schedule_day_range = event_schedule_day_range(event.metadata_json)
    include_web_edit_details = not restrict_identity_directory and (
        current_user.can_edit
        or current_user.is_admin
        or current_user.is_root_admin
        or current_user.is_issuer
    )
    include_management_details = include_web_edit_details

    tasks = (
        db.query(PublishedTask)
        .filter(PublishedTask.event_id == event_id)
        .order_by(PublishedTask.sort_order, PublishedTask.start_datetime)
        .all()
    )

    # Load all edits for these tasks in one query
    task_ids = [t.id for t in tasks]
    edits_map: Dict[int, TaskEdit] = {}
    editor_names: Dict[int, str] = {}
    if task_ids:
        edits = db.query(TaskEdit).filter(TaskEdit.task_id.in_(task_ids)).all()
        edits_map = {e.task_id: e for e in edits}
        editor_ids = sorted({
            e.edited_by_user_id
            for e in edits
            if e.edited_by_user_id is not None
        })
        if editor_ids:
            editors = db.query(User).filter(User.id.in_(editor_ids)).all()
            editor_names = {
                editor.id: editor.display_name or editor.username
                for editor in editors
            }

    persons = (
        db.query(PublishedPerson)
        .filter(PublishedPerson.event_id == event_id)
        .order_by(PublishedPerson.last_name, PublishedPerson.first_name)
        .all()
    )

    # Extract day_aliases from event metadata
    day_aliases = None
    if event.metadata_json:
        try:
            meta = json.loads(event.metadata_json)
            day_aliases = meta.get("day_aliases")
        except (json.JSONDecodeError, TypeError):
            pass

    public_schedule_items = (
        db.query(PublishedGeneralScheduleItem)
        .filter(PublishedGeneralScheduleItem.event_id == event_id)
        .order_by(
            PublishedGeneralScheduleItem.date,
            PublishedGeneralScheduleItem.start_time,
            PublishedGeneralScheduleItem.sort_order,
            PublishedGeneralScheduleItem.title,
        )
        .all()
    )
    public_schedule_categories = (
        db.query(PublishedGeneralScheduleCategory)
        .filter(PublishedGeneralScheduleCategory.event_id == event_id)
        .order_by(
            PublishedGeneralScheduleCategory.sort_order,
            PublishedGeneralScheduleCategory.name,
        )
        .all()
    )
    general_schedule_state = (
        db.query(GeneralSchedulePublishState)
        .filter(GeneralSchedulePublishState.event_id == event_id)
        .first()
    )
    unavailabilities = (
        db.query(PublishedPersonUnavailability)
        .filter(PublishedPersonUnavailability.event_id == event_id)
        .order_by(
            PublishedPersonUnavailability.working_date,
            PublishedPersonUnavailability.start_datetime,
            PublishedPersonUnavailability.external_person_id,
        )
        .all()
    )

    task_outputs = [
        out for task in tasks
        if (out := _task_to_out(
            task,
            edits_map.get(task.id),
            editor_names.get(edits_map[task.id].edited_by_user_id)
            if task.id in edits_map and edits_map[task.id].edited_by_user_id is not None
            else None,
            include_web_edit_details=include_web_edit_details,
            include_management_details=include_management_details,
            include_all_authenticated_fields=not restrict_task_assignments,
            schedule_day_range=schedule_day_range,
        )) is not None
    ]
    if restrict_identity_directory:
        linked_person_id = current_user.linked_person_id
        persons = [
            person for person in persons
            if linked_person_id is not None
            and person.external_person_id == linked_person_id
        ]
        unavailabilities = [
            row for row in unavailabilities
            if linked_person_id is not None
            and row.external_person_id == linked_person_id
        ]
        if restrict_task_assignments:
            safe_tasks: list[TaskOut] = []
            for task in task_outputs:
                attendees = [
                    attendee for attendee in task.attendees
                    if attendee.person_id == linked_person_id
                ] if linked_person_id is not None else []
                assignments = None
                if task.field_assignments and linked_person_id is not None:
                    assignments = {
                        field_id: matching
                        for field_id, field_attendees in task.field_assignments.items()
                        if (matching := [
                            attendee for attendee in field_attendees
                            if attendee.person_id == linked_person_id
                        ])
                    } or None
                safe_tasks.append(task.model_copy(update={
                    "attendees": attendees,
                    "field_assignments": assignments,
                }))
            task_outputs = safe_tasks

    policy_identity = current_policy_identity(db)
    return CalendarResponse(
        event_id=event.id,
        event_name=event.name,
        start_date=event.start_date.isoformat() if event.start_date else None,
        end_date=event.end_date.isoformat() if event.end_date else None,
        logo_color_1=None,
        logo_color_2=None,
        day_aliases=day_aliases,
        schedule_day_range=schedule_day_range,
        tasks=task_outputs,
        public_schedule_items=[
            _general_schedule_item_to_out(item, schedule_day_range)
            for item in public_schedule_items
        ],
        public_schedule_categories=[
            PublicScheduleCategoryOut(
                id=category.external_category_id,
                name=category.name,
                sort_order=category.sort_order or 0,
            )
            for category in public_schedule_categories
        ],
        public_schedule_views=[
            PublicScheduleCategoryOut(
                id=category.external_category_id,
                name=category.name,
                sort_order=category.sort_order or 0,
            )
            for category in public_schedule_categories
        ],
        general_schedule_state=GeneralScheduleStateOut(
            published_at=general_schedule_state.published_at.isoformat() if general_schedule_state and general_schedule_state.published_at else None,
            fingerprint=general_schedule_state.fingerprint if general_schedule_state else None,
            item_count=general_schedule_state.item_count if general_schedule_state else 0,
        ) if general_schedule_state else None,
        persons=[
            PersonOut(
                id=p.id,
                external_person_id=p.external_person_id,
                first_name=p.first_name,
                last_name=p.last_name,
            )
            for p in persons
        ],
        unavailabilities=[
            PersonUnavailabilityOut(
                person_id=row.external_person_id,
                working_date=row.working_date,
                start=row.start_datetime,
                end=row.end_datetime,
            )
            for row in unavailabilities
        ],
        data_policy_version=policy_identity[0] if policy_identity else None,
        data_policy_sha256=policy_identity[1] if policy_identity else None,
        data_policy_acknowledged=has_data_policy_acknowledgement(current_user, event_id, db),
    )


def _offline_calendar_response(calendar: CalendarResponse) -> OfflineCalendarResponse:
    """Select the current bounded calendar fields approved for device storage."""
    return OfflineCalendarResponse(
        event_id=calendar.event_id,
        event_name=calendar.event_name,
        start_date=calendar.start_date,
        end_date=calendar.end_date,
        day_aliases=calendar.day_aliases,
        schedule_day_range=calendar.schedule_day_range,
        tasks=[
            OfflineTaskOut(
                id=task.id,
                external_task_id=task.external_task_id,
                name=task.name,
                summary=task.summary,
                description=task.description,
                start=task.start,
                end=task.end,
                working_date=task.working_date,
                location_name=task.location_name,
                location_address=task.location_address,
                task_type_code=task.task_type_code,
                task_type_name=task.task_type_name,
                color=task.color,
                attendees=task.attendees,
                field_assignments=task.field_assignments,
                field_values=task.field_values,
                field_definitions=task.field_definitions,
                additional=None,
                sort_order=task.sort_order,
                has_web_edit=False,
                web_edit_edited_at=None,
                web_edit_edited_by=None,
                web_edit_edited_by_user_id=None,
                web_edit_change_summary=[],
            )
            for task in calendar.tasks
        ],
        persons=calendar.persons,
        public_schedule_categories=calendar.public_schedule_categories,
        public_schedule_views=calendar.public_schedule_views,
        public_schedule_items=[
            OfflinePublicScheduleItemOut(
                id=item.id,
                external_session_element_id=item.external_session_element_id,
                title=item.title,
                date=item.date,
                working_date=item.working_date,
                start_time=item.start_time,
                end_time=item.end_time,
                location_name=item.location_name,
                location_address=item.location_address,
                responsible=item.responsible,
                audience_teams=[
                    OfflineAudienceTeamOut(
                        name=team["name"],
                        short_name=team.get("short_name"),
                    )
                    for team in item.audience_teams
                    if isinstance(team, dict)
                    and isinstance(team.get("name"), str)
                    and team["name"]
                    and (
                        team.get("short_name") is None
                        or isinstance(team.get("short_name"), str)
                    )
                ],
                description=item.description,
                category_id=item.category_id,
                category_name=item.category_name,
                type_name=item.type_name,
                colour=item.colour,
                sort_order=item.sort_order,
            )
            for item in calendar.public_schedule_items
        ],
        unavailabilities=calendar.unavailabilities,
        data_policy_version=calendar.data_policy_version,
        data_policy_sha256=calendar.data_policy_sha256,
        data_policy_acknowledged=calendar.data_policy_acknowledged,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{event_id}", response_model=CalendarResponse)
@limiter.limit("60/minute")
def get_calendar(
    event_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get identical published task content for every authenticated event member."""
    restrict_identity_directory = not (
        current_user.can_edit
        or current_user.is_admin
        or current_user.is_root_admin
        or current_user.is_issuer
    )
    return _build_calendar_response(
        event_id,
        current_user,
        db,
        restrict_identity_directory=restrict_identity_directory,
    )


@router.get("/{event_id}/offline", response_model=OfflineCalendarResponse)
@limiter.limit("60/minute")
def get_offline_calendar(
    event_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the bounded calendar contract approved for optional device storage."""
    policy_allows_allocation_names = (
        current_policy_template_version(db) == POLICY_TEMPLATE_VERSION
    )
    calendar = _build_calendar_response(
        event_id,
        current_user,
        db,
        restrict_identity_directory=True,
        restrict_task_assignments=not policy_allows_allocation_names,
    )
    return _offline_calendar_response(calendar)


@router.get("/{event_id}/persons", response_model=List[PersonOut])
def get_persons(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get person list for filter dropdown."""
    _check_event_access(event_id, current_user, db)

    persons = (
        db.query(PublishedPerson)
        .filter(PublishedPerson.event_id == event_id)
        .order_by(PublishedPerson.last_name, PublishedPerson.first_name)
        .all()
    )

    if not (
        current_user.can_edit
        or current_user.is_admin
        or current_user.is_root_admin
        or current_user.is_issuer
    ):
        persons = [
            person for person in persons
            if current_user.linked_person_id is not None
            and person.external_person_id == current_user.linked_person_id
        ]

    return [
        PersonOut(
            id=p.id,
            external_person_id=p.external_person_id,
            first_name=p.first_name,
            last_name=p.last_name,
        )
        for p in persons
    ]


@router.put("/{event_id}/tasks/{task_id}")
def edit_task(
    event_id: int,
    task_id: int,
    body: TaskEditIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save a web-only edit for a task. Requires can_edit permission."""
    if not current_user.can_edit and not current_user.is_admin and not current_user.is_root_admin:
        raise HTTPException(status_code=403, detail="Edit permission required")

    _check_event_access(event_id, current_user, db)
    require_data_policy_acknowledgement(current_user, event_id, db)

    # Serialize all edits for one published task. This makes the cross-category
    # uniqueness check authoritative even when two editors submit partial
    # category changes at the same time.
    task = (
        db.query(PublishedTask)
        .filter(
            PublishedTask.id == task_id,
            PublishedTask.event_id == event_id,
        )
        .with_for_update()
        .first()
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # Upsert task_edit
    edit = db.query(TaskEdit).filter(TaskEdit.task_id == task_id).first()
    if edit is None:
        edit = TaskEdit(task_id=task_id, edited_by_user_id=current_user.id)
        db.add(edit)

    parsed_start, parsed_end = _validate_effective_task_values(
        start=body.start,
        end=body.end,
        fallback_start=edit.start_datetime or task.start_datetime,
        fallback_end=edit.end_datetime or task.end_datetime,
    )
    if body.start is not None:
        edit.start_datetime = parsed_start
    if body.end is not None:
        edit.end_datetime = parsed_end
    if body.field_assignments is not None:
        field_assignments_data, effective_assignments = _merge_field_assignments(
            event_id, task, edit, body.field_assignments, db
        )
        edit.field_assignments_json = json.dumps(field_assignments_data)
        edit.attendees_json = json.dumps(
            _flatten_field_assignments(effective_assignments)
        )
    elif body.attendees is not None:
        if _assignment_field_ids(task):
            raise HTTPException(
                status_code=422,
                detail="Structured assignments must be edited by category",
            )
        edit.attendees_json = json.dumps([
            attendee.model_dump()
            for attendee in _canonical_attendees(event_id, body.attendees, db)
        ])

    edit.edited_by_user_id = current_user.id
    audit(
        db,
        user=current_user,
        action="calendar.task_edit",
        resource_type="published_task",
        resource_id=task.id,
        detail=json.dumps({"event_id": event_id}),
        request=request,
    )
    db.commit()

    return {"status": "ok", "message": "Edit saved"}


@router.delete("/{event_id}/tasks/{task_id}/edits")
def revert_task_edit(
    event_id: int,
    task_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Revert a task to its published version (delete web edit)."""
    if not current_user.can_edit and not current_user.is_admin and not current_user.is_root_admin:
        raise HTTPException(status_code=403, detail="Edit permission required")

    _check_event_access(event_id, current_user, db)

    task = db.query(PublishedTask).filter(
        PublishedTask.id == task_id,
        PublishedTask.event_id == event_id,
    ).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    deleted = db.query(TaskEdit).filter(TaskEdit.task_id == task.id).delete()

    if not deleted:
        raise HTTPException(status_code=404, detail="No edit found for this task")

    audit(
        db,
        user=current_user,
        action="calendar.task_revert",
        resource_type="published_task",
        resource_id=task.id,
        detail=json.dumps({"event_id": event_id}),
        request=request,
    )
    db.commit()
    return {"status": "ok", "message": "Edit reverted"}


@router.post("/{event_id}/tasks/commit")
@limiter.limit("20/minute")
def batch_commit(
    event_id: int,
    request: Request,
    body: BatchCommitRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Batch-commit edits, deletions, and new tasks in a single transaction.
    Sends a push notification after successful commit."""
    if not current_user.can_edit and not current_user.is_admin and not current_user.is_root_admin:
        raise HTTPException(status_code=403, detail="Edit permission required")

    event = _check_event_access(event_id, current_user, db)
    require_data_policy_acknowledgement(current_user, event_id, db)

    # Lock every affected source task once, in deterministic order. Besides
    # preventing lost partial edits, the ordering prevents two batch requests
    # from deadlocking when they touch the same tasks in a different order.
    affected_task_ids = sorted({
        *[item.task_id for item in body.edits],
        *body.deletions,
    })
    locked_tasks = (
        db.query(PublishedTask)
        .filter(
            PublishedTask.event_id == event_id,
            PublishedTask.id.in_(affected_task_ids),
        )
        .order_by(PublishedTask.id)
        .with_for_update()
        .all()
        if affected_task_ids
        else []
    )
    tasks_by_id = {task.id: task for task in locked_tasks}
    missing_task_ids = set(affected_task_ids) - set(tasks_by_id)
    if missing_task_ids:
        missing = min(missing_task_ids)
        raise HTTPException(status_code=404, detail=f"Task {missing} not found")

    # --- Edits ---
    for item in body.edits:
        task = tasks_by_id[item.task_id]

        edit = db.query(TaskEdit).filter(TaskEdit.task_id == item.task_id).first()
        if edit is None:
            edit = TaskEdit(task_id=item.task_id, edited_by_user_id=current_user.id)
            db.add(edit)

        if item.name is not None:
            edit.name = item.name
        if item.summary is not None:
            edit.summary = item.summary
        if item.description is not None:
            edit.description = item.description
        parsed_start, parsed_end = _validate_effective_task_values(
            start=item.start,
            end=item.end,
            fallback_start=edit.start_datetime or task.start_datetime,
            fallback_end=edit.end_datetime or task.end_datetime,
            colour=item.color,
        )
        if item.start is not None:
            edit.start_datetime = parsed_start
        if item.end is not None:
            edit.end_datetime = parsed_end
        if item.location_name is not None:
            edit.location_name = item.location_name
        if item.location_address is not None:
            edit.location_address = item.location_address
        if item.color is not None:
            edit.color = item.color
        if item.field_assignments is not None:
            field_assignments_data, effective_assignments = _merge_field_assignments(
                event_id, task, edit, item.field_assignments, db
            )
            edit.field_assignments_json = json.dumps(field_assignments_data)
            edit.attendees_json = json.dumps(
                _flatten_field_assignments(effective_assignments)
            )
        elif item.attendees is not None:
            if _assignment_field_ids(task):
                raise HTTPException(
                    status_code=422,
                    detail="Structured assignments must be edited by category",
                )
            edit.attendees_json = json.dumps([
                attendee.model_dump()
                for attendee in _canonical_attendees(event_id, item.attendees, db)
            ])
        if item.field_values is not None:
            edit.field_values_json = json.dumps(
                _merge_field_values(task, edit, item.field_values)
            )
        edit.edited_by_user_id = current_user.id

    # --- Deletions ---
    for task_id in body.deletions:
        task = tasks_by_id[task_id]

        # For web-created tasks, hard-delete instead of soft-delete
        if task.web_created:
            db.query(TaskEdit).filter(TaskEdit.task_id == task_id).delete()
            db.delete(task)
        else:
            edit = db.query(TaskEdit).filter(TaskEdit.task_id == task_id).first()
            if edit is None:
                edit = TaskEdit(task_id=task_id, edited_by_user_id=current_user.id)
                db.add(edit)
            edit.is_deleted = True
            edit.edited_by_user_id = current_user.id

    # --- Creations ---
    for new_task in body.creations:
        if new_task.field_assignments:
            raise HTTPException(
                status_code=422,
                detail="New web tasks cannot define hidden assignment fields",
            )
        if new_task.field_values:
            raise HTTPException(
                status_code=422,
                detail="New web tasks cannot define hidden task fields",
            )
        parsed_start, parsed_end = _validate_effective_task_values(
            start=new_task.start,
            end=new_task.end,
            fallback_start=datetime.now(timezone.utc),
            fallback_end=datetime.now(timezone.utc),
            colour=new_task.color,
        )
        attendees_data = [
            attendee.model_dump()
            for attendee in _canonical_attendees(
                event_id,
                new_task.attendees or [],
                db,
            )
        ]
        db.add(PublishedTask(
            event_id=event_id,
            external_task_id=0,
            name=new_task.name,
            summary=new_task.summary,
            description=new_task.description,
            start_datetime=parsed_start,
            end_datetime=parsed_end,
            location_name=new_task.location_name,
            location_address=new_task.location_address,
            color=new_task.color,
            attendees_json=json.dumps(attendees_data) if attendees_data else None,
            field_assignments_json=None,
            field_values_json=None,
            web_created=True,
        ))

    audit(
        db,
        user=current_user,
        action="calendar.commit",
        resource_type="event",
        resource_id=event_id,
        detail=json.dumps(
            {
                "edited_task_ids": [item.task_id for item in body.edits],
                "deleted_task_ids": body.deletions,
                "created_count": len(body.creations),
            }
        ),
        request=request,
    )
    db.commit()

    # Send push notification
    notification_sent = False
    try:
        from app.core.push import send_push_to_event
        count = send_push_to_event(
            event_id=event_id,
            title="Masterplan Changed",
            body="The masterplan has been changed manually.",
            url=f"/calendar?event={event_id}",
            db=db,
            notification_type="schedule",
        )
        notification_sent = count > 0
    except Exception as exc:
        logger.warning(
            "Calendar commit push delivery failed for event %s (%s)",
            event_id,
            type(exc).__name__,
        )

    return {
        "status": "ok",
        "edits_applied": len(body.edits),
        "tasks_deleted": len(body.deletions),
        "tasks_created": len(body.creations),
        "notification_sent": notification_sent,
    }

