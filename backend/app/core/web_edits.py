"""Web-edit confidence helpers for server operations visibility."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.published import PublishedTask, TaskEdit
from app.models.user import User


@dataclass(frozen=True)
class WebEditItem:
    """One committed web edit compared with the desktop-published baseline."""

    task_id: int
    task_name: str
    day: str | None
    start: datetime | None
    end: datetime | None
    location: str | None
    edited_at: datetime | None
    edited_by: str | None
    edited_by_user_id: int | None
    change_summary: list[str]
    original_summary: str
    current_summary: str


@dataclass(frozen=True)
class WebEditSummary:
    """Compact confidence summary for all web edits in one event."""

    level: str
    edited_task_count: int
    last_edited_at: datetime | None
    last_edited_by: str | None
    has_published_baseline: bool
    headline: str
    description: str
    items: list[WebEditItem]


def _parse_json(raw: str | None) -> Any:
    """Parse optional JSON from published or edited task fields."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _same_json(left: str | None, right: str | None) -> bool:
    """Compare JSON strings by value, falling back to raw strings if invalid."""
    left_parsed = _parse_json(left)
    right_parsed = _parse_json(right)
    if left_parsed is not None or right_parsed is not None:
        return left_parsed == right_parsed
    return (left or "") == (right or "")


def summarise_task_edit(task: PublishedTask, edit: TaskEdit | None) -> list[str]:
    """Return short human-readable changes made by one web edit."""
    if edit is None:
        if task.web_created:
            return ["Created on web"]
        return []

    changes: list[str] = []
    if edit.is_deleted:
        changes.append("Deleted from live schedule")
    if edit.name is not None and edit.name != task.name:
        changes.append("Name changed")
    if edit.start_datetime is not None or edit.end_datetime is not None:
        changes.append("Time changed")
    if edit.location_name is not None or edit.location_address is not None:
        changes.append("Location changed")
    if edit.attendees_json is not None and not _same_json(
        edit.attendees_json,
        task.attendees_json,
    ):
        changes.append("People changed")
    if edit.field_assignments_json is not None and not _same_json(
        edit.field_assignments_json,
        task.field_assignments_json,
    ):
        changes.append("Assignments changed")
    if edit.field_values_json is not None and not _same_json(
        edit.field_values_json,
        task.field_values_json,
    ):
        changes.append("Fields changed")
    if edit.attachments_json is not None and not _same_json(
        edit.attachments_json,
        task.attachments_json,
    ):
        changes.append("Attachments changed")
    if edit.summary is not None or edit.description is not None:
        changes.append("Text changed")
    if edit.color is not None and edit.color != task.color:
        changes.append("Colour changed")

    return changes or ["Edited on the web"]


def _format_time_range(start: datetime | None, end: datetime | None) -> str:
    """Format a task time range for compact review text."""
    if not start or not end:
        return "Time not available"
    return f"{start.strftime('%H:%M')} - {end.strftime('%H:%M')}"


def _attendee_names(raw: str | None) -> str:
    """Return a readable attendee summary from stored JSON."""
    attendees = _parse_json(raw)
    if not isinstance(attendees, list) or not attendees:
        return "No assigned people"
    names = [
        str(item.get("name"))
        for item in attendees
        if isinstance(item, dict) and item.get("name")
    ]
    return ", ".join(names) if names else "No assigned people"


def _state_summary(
    *,
    start: datetime | None,
    end: datetime | None,
    location: str | None,
    attendees_json: str | None,
) -> str:
    """Return the concise values shown before reverting an edit."""
    parts = [
        _format_time_range(start, end),
        location or "No location",
        _attendee_names(attendees_json),
    ]
    return " · ".join(parts)


def _original_summary(task: PublishedTask) -> str:
    """Summarise the desktop-published task baseline."""
    if task.web_created:
        return "No desktop-published version"
    return _state_summary(
        start=task.start_datetime,
        end=task.end_datetime,
        location=task.location_name,
        attendees_json=task.attendees_json,
    )


def _current_summary(task: PublishedTask, edit: TaskEdit | None) -> str:
    """Summarise the current live task after web edits are applied."""
    if edit and edit.is_deleted:
        return "Deleted from the live schedule"
    return _state_summary(
        start=edit.start_datetime if edit and edit.start_datetime else task.start_datetime,
        end=edit.end_datetime if edit and edit.end_datetime else task.end_datetime,
        location=(
            edit.location_name
            if edit and edit.location_name is not None
            else task.location_name
        ),
        attendees_json=(
            edit.attendees_json
            if edit and edit.attendees_json is not None
            else task.attendees_json
        ),
    )


def _editor_name(user: User | None) -> str | None:
    """Return the safest display name for an editor without exposing credentials."""
    if user is None:
        return None
    return user.display_name or user.username


def _load_editor_names(db: Session, edits: list[TaskEdit]) -> dict[int, str]:
    """Load editor display names for edit records in one query."""
    editor_ids = sorted(
        {
            edit.edited_by_user_id
            for edit in edits
            if edit.edited_by_user_id is not None
        }
    )
    if not editor_ids:
        return {}
    users = db.query(User).filter(User.id.in_(editor_ids)).all()
    return {user.id: _editor_name(user) or "Unknown editor" for user in users}


def build_web_edit_item(
    task: PublishedTask,
    edit: TaskEdit | None,
    editor_name: str | None = None,
) -> WebEditItem:
    """Build one review-list item for a web edit or web-created task."""
    effective_start = edit.start_datetime if edit and edit.start_datetime else task.start_datetime
    effective_end = edit.end_datetime if edit and edit.end_datetime else task.end_datetime
    effective_name = edit.name if edit and edit.name is not None else task.name
    effective_location = (
        edit.location_name
        if edit and edit.location_name is not None
        else task.location_name
    )
    edited_at = edit.edited_at if edit else task.created_at
    edited_by_user_id = edit.edited_by_user_id if edit else None

    return WebEditItem(
        task_id=task.id,
        task_name=effective_name,
        day=effective_start.date().isoformat() if effective_start else None,
        start=effective_start,
        end=effective_end,
        location=effective_location,
        edited_at=edited_at,
        edited_by=editor_name,
        edited_by_user_id=edited_by_user_id,
        change_summary=summarise_task_edit(task, edit),
        original_summary=_original_summary(task),
        current_summary=_current_summary(task, edit),
    )


def get_task_web_edit_metadata(
    task: PublishedTask,
    edit: TaskEdit | None,
    editor_name: str | None = None,
) -> dict[str, Any]:
    """Return calendar response metadata for one web-edited task."""
    if edit is None and not task.web_created:
        return {
            "has_web_edit": False,
            "web_edit_edited_at": None,
            "web_edit_edited_by": None,
            "web_edit_edited_by_user_id": None,
            "web_edit_change_summary": [],
        }

    item = build_web_edit_item(task, edit, editor_name=editor_name)
    return {
        "has_web_edit": True,
        "web_edit_edited_at": item.edited_at.isoformat() if item.edited_at else None,
        "web_edit_edited_by": item.edited_by,
        "web_edit_edited_by_user_id": item.edited_by_user_id,
        "web_edit_change_summary": item.change_summary,
    }


def derive_web_edit_summary(event_id: int, db: Session) -> WebEditSummary:
    """Derive the event-level web-edit confidence state for admins."""
    tasks = (
        db.query(PublishedTask)
        .filter(PublishedTask.event_id == event_id)
        .order_by(PublishedTask.start_datetime, PublishedTask.sort_order)
        .all()
    )
    has_published_baseline = any(not task.web_created for task in tasks)
    if not has_published_baseline:
        return WebEditSummary(
            level="unknown",
            edited_task_count=0,
            last_edited_at=None,
            last_edited_by=None,
            has_published_baseline=False,
            headline="Web edit state unknown",
            description="No published desktop baseline is available yet.",
            items=[],
        )

    task_ids = [task.id for task in tasks]
    edits = (
        db.query(TaskEdit).filter(TaskEdit.task_id.in_(task_ids)).all()
        if task_ids
        else []
    )
    edits_by_task = {edit.task_id: edit for edit in edits}
    editor_names = _load_editor_names(db, edits)

    items: list[WebEditItem] = []
    for task in tasks:
        edit = edits_by_task.get(task.id)
        if edit is None and not task.web_created:
            continue
        editor_name = (
            editor_names.get(edit.edited_by_user_id)
            if edit and edit.edited_by_user_id is not None
            else None
        )
        items.append(build_web_edit_item(task, edit, editor_name=editor_name))

    items.sort(
        key=lambda item: item.edited_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    last_item = items[0] if items else None

    if not items:
        return WebEditSummary(
            level="healthy",
            edited_task_count=0,
            last_edited_at=None,
            last_edited_by=None,
            has_published_baseline=True,
            headline="No web edits",
            description="Live schedule matches the published desktop source.",
            items=[],
        )

    return WebEditSummary(
        level="review",
        edited_task_count=len(items),
        last_edited_at=last_item.edited_at if last_item else None,
        last_edited_by=last_item.edited_by if last_item else None,
        has_published_baseline=True,
        headline="Review needed",
        description=f"{len(items)} web edit{'s' if len(items) != 1 else ''} since the last desktop publish.",
        items=items,
    )


def _edited_tasks_for_event(event_id: int, db: Session) -> list[PublishedTask]:
    """Return tasks that currently differ from the desktop-published baseline."""
    tasks = (
        db.query(PublishedTask)
        .filter(PublishedTask.event_id == event_id)
        .order_by(PublishedTask.start_datetime, PublishedTask.sort_order)
        .all()
    )
    task_ids = [task.id for task in tasks]
    edits = (
        db.query(TaskEdit).filter(TaskEdit.task_id.in_(task_ids)).all()
        if task_ids
        else []
    )
    edited_task_ids = {edit.task_id for edit in edits}
    return [
        task
        for task in tasks
        if task.web_created or task.id in edited_task_ids
    ]


def revert_web_edit(event_id: int, task_id: int, db: Session) -> tuple[str, int]:
    """Revert one committed web edit and return task name plus remaining count."""
    task = (
        db.query(PublishedTask)
        .filter(PublishedTask.id == task_id, PublishedTask.event_id == event_id)
        .first()
    )
    if task is None:
        raise LookupError("Task not found")

    edit = db.query(TaskEdit).filter(TaskEdit.task_id == task_id).first()
    if edit is None and not task.web_created:
        raise LookupError("No web edit found for this task")

    task_name = task.name
    if edit is not None:
        db.delete(edit)
    if task.web_created:
        db.delete(task)
    db.flush()

    remaining = len(_edited_tasks_for_event(event_id, db))
    return task_name, remaining


def revert_web_edits(
    event_id: int,
    db: Session,
    task_ids: list[int] | None = None,
    *,
    revert_all: bool = False,
) -> tuple[int, int]:
    """Revert multiple committed web edits and return counts."""
    if revert_all:
        targets = [task.id for task in _edited_tasks_for_event(event_id, db)]
    else:
        targets = task_ids or []
    if not targets:
        return 0, len(_edited_tasks_for_event(event_id, db))

    reverted = 0
    for task_id in targets:
        try:
            revert_web_edit(event_id, task_id, db)
            reverted += 1
        except LookupError:
            continue

    remaining = len(_edited_tasks_for_event(event_id, db))
    return reverted, remaining
