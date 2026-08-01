"""
Snapshot helpers  -  create / deduplicate / prune publish snapshots.

Used by publish.py (after data insertion) and history.py (rollback).
"""
import hashlib
import json
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func as sa_func

from app.models.event import Event
from app.models.published import (
    PublishedPerson, PublishedPersonUnavailability, PublishedTask,
    PublishSnapshot, TaskEdit,
)
from app.api.v1.calendar import _parse_json, _task_to_out
from app.core.schedule_days import event_schedule_day_range
from app.core import runtime_settings as rt


def _serialize_raw_task(task: PublishedTask) -> dict:
    """Serialize a PublishedTask to a dict that can be re-inserted on rollback."""
    return {
        "external_task_id": task.external_task_id,
        "name": task.name,
        "summary": task.summary,
        "description": task.description,
        "start_datetime": task.start_datetime.isoformat() if task.start_datetime else None,
        "end_datetime": task.end_datetime.isoformat() if task.end_datetime else None,
        "location_name": task.location_name,
        "location_address": task.location_address,
        "task_type_code": task.task_type_code,
        "task_type_name": task.task_type_name,
        "color": task.color,
        "attendees_json": task.attendees_json,
        "field_assignments_json": task.field_assignments_json,
        "field_values_json": task.field_values_json,
        "field_definitions_json": task.field_definitions_json,
        "additional_json": task.additional_json,
        "sort_order": task.sort_order,
        "web_created": task.web_created,
    }


def _serialize_raw_person(person: PublishedPerson) -> dict:
    """Serialize a PublishedPerson to a dict that can be re-inserted on rollback."""
    return {
        "external_person_id": person.external_person_id,
        "first_name": person.first_name,
        "last_name": person.last_name,
        "email": person.email,
    }


def create_snapshot(
    event: Event,
    db: Session,
    source: str = "desktop",
) -> Optional[PublishSnapshot]:
    """Snapshot the current published state for an event.

    Returns the new snapshot, or None if:
    - There are no existing tasks (nothing to archive)
    - Any existing snapshot already has the same content hash (dedup)
    """
    tasks = (
        db.query(PublishedTask)
        .filter(PublishedTask.event_id == event.id)
        .order_by(PublishedTask.sort_order, PublishedTask.start_datetime)
        .all()
    )
    if not tasks:
        return None

    persons = (
        db.query(PublishedPerson)
        .filter(PublishedPerson.event_id == event.id)
        .order_by(PublishedPerson.last_name, PublishedPerson.first_name)
        .all()
    )
    unavailabilities = (
        db.query(PublishedPersonUnavailability)
        .filter(PublishedPersonUnavailability.event_id == event.id)
        .order_by(
            PublishedPersonUnavailability.working_date,
            PublishedPersonUnavailability.start_datetime,
            PublishedPersonUnavailability.external_person_id,
        )
        .all()
    )

    # Load edits
    task_ids = [t.id for t in tasks]
    edits: List[TaskEdit] = []
    if task_ids:
        edits = db.query(TaskEdit).filter(TaskEdit.task_id.in_(task_ids)).all()
    edits_map: Dict[int, TaskEdit] = {e.task_id: e for e in edits}

    # Build resolved tasks (what users saw) for display
    resolved_tasks = []
    for t in tasks:
        out = _task_to_out(
            t,
            edits_map.get(t.id),
            schedule_day_range=event_schedule_day_range(event.metadata_json),
        )
        if out is not None:
            resolved_tasks.append(out.model_dump())

    # Build raw tasks (original columns) for rollback re-insertion
    raw_tasks = [_serialize_raw_task(t) for t in tasks]
    raw_persons = [_serialize_raw_person(p) for p in persons]
    raw_unavailabilities = [
        {
            "external_person_id": row.external_person_id,
            "working_date": row.working_date,
            "start_datetime": row.start_datetime,
            "end_datetime": row.end_datetime,
        }
        for row in unavailabilities
    ]

    # Edits summary
    edited_task_ids = [e.task_id for e in edits if not e.is_deleted]
    deleted_task_ids = [e.task_id for e in edits if e.is_deleted]

    # Event metadata at this point
    event_meta = {
        "name": event.name,
        "start_date": event.start_date.isoformat() if event.start_date else None,
        "end_date": event.end_date.isoformat() if event.end_date else None,
        "metadata_json": event.metadata_json,
    }

    snapshot_data = {
        "tasks": resolved_tasks,
        "raw_tasks": raw_tasks,
        "persons": raw_persons,
        "unavailabilities": raw_unavailabilities,
        "edits_summary": {
            "edited_task_ids": edited_task_ids,
            "deleted_task_ids": deleted_task_ids,
            "total": len(edits),
        },
        "event_meta": event_meta,
    }

    snapshot_json = json.dumps(snapshot_data, sort_keys=True, default=str)

    # Hash only stable content (raw data + event meta) for dedup.
    # resolved_tasks and edits_summary contain DB auto-increment IDs
    # that change on every delete-and-reinsert cycle.
    hash_data = {
        "raw_tasks": raw_tasks,
        "persons": raw_persons,
        "unavailabilities": raw_unavailabilities,
        "event_meta": event_meta,
    }
    content_hash = hashlib.sha256(
        json.dumps(hash_data, sort_keys=True, default=str).encode()
    ).hexdigest()

    # Deduplication: skip if any existing snapshot has the same content
    existing = (
        db.query(PublishSnapshot.id)
        .filter(
            PublishSnapshot.event_id == event.id,
            PublishSnapshot.content_hash == content_hash,
        )
        .first()
    )
    if existing is not None:
        return None

    # Guard: if all slots are frozen, we can't make room for a new snapshot
    frozen_count = (
        db.query(sa_func.count(PublishSnapshot.id))
        .filter(
            PublishSnapshot.event_id == event.id,
            PublishSnapshot.frozen == True,  # noqa: E712
        )
        .scalar()
    ) or 0
    max_snaps = rt.get_int("max_snapshots_per_event", db)
    if frozen_count >= max_snaps:
        return None

    # Compute next version
    max_version = (
        db.query(sa_func.max(PublishSnapshot.version))
        .filter(PublishSnapshot.event_id == event.id)
        .scalar()
    ) or 0
    next_version = max_version + 1

    snapshot = PublishSnapshot(
        event_id=event.id,
        version=next_version,
        snapshot_json=snapshot_json,
        content_hash=content_hash,
        task_count=len(tasks),
        person_count=len(persons),
        edits_count=len(edits),
        source=source,
    )
    db.add(snapshot)

    # Retention: prune oldest if over limit
    _prune_old_snapshots(event.id, db)

    return snapshot


def _prune_old_snapshots(event_id: int, db: Session) -> None:
    """Delete oldest *unfrozen* snapshots if the event exceeds the configured limit."""
    max_snaps = rt.get_int("max_snapshots_per_event", db)
    count = (
        db.query(sa_func.count(PublishSnapshot.id))
        .filter(PublishSnapshot.event_id == event_id)
        .scalar()
    ) or 0

    excess = count - max_snaps
    if excess <= 0:
        return

    oldest_ids = [
        row.id for row in
        db.query(PublishSnapshot.id)
        .filter(
            PublishSnapshot.event_id == event_id,
            PublishSnapshot.frozen == False,  # noqa: E712
        )
        .order_by(PublishSnapshot.version.asc())
        .limit(excess)
        .all()
    ]
    if oldest_ids:
        db.query(PublishSnapshot).filter(PublishSnapshot.id.in_(oldest_ids)).delete(
            synchronize_session=False,
        )
