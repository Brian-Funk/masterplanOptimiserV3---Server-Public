"""
History endpoints  -  publish snapshot list, detail, delete, and rollback.
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from sqlalchemy import func as sa_func

from app.core.security import (
    require_admin_or_issuer,
    require_event_access,
    require_recent_reauth,
)
from app.core.snapshots import create_snapshot
from app.core.audit import audit
from app.core import runtime_settings as rt
from app.db.database import get_db
from app.models.event import Event
from app.models.published import (
    PublishedPerson, PublishedPersonUnavailability, PublishedTask,
    PublishSnapshot, TaskEdit,
)
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SnapshotSummary(BaseModel):
    """Compact snapshot metadata for history listings."""

    id: int
    version: int
    task_count: int
    person_count: int
    edits_count: int
    source: Optional[str] = None
    label: Optional[str] = None
    frozen: bool = False
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class SnapshotPatch(BaseModel):
    """Editable snapshot metadata."""

    label: Optional[str] = None
    frozen: Optional[bool] = None


class SnapshotDetail(BaseModel):
    """Full snapshot payload including stored schedule data."""

    id: int
    version: int
    task_count: int
    person_count: int
    edits_count: int
    source: Optional[str] = None
    created_at: Optional[datetime] = None
    snapshot: Dict[str, Any]


class RestoreResponse(BaseModel):
    """Result returned after restoring a publish snapshot."""

    status: str
    restored_version: int
    tasks_created: int
    persons_created: int
    edits_cleared: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_event_or_404(event_id: int, user: User, db: Session) -> Event:
    """Load event with access check (root sees all, admin/issuer sees own)."""
    return require_event_access(event_id, user, db)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/events/{event_id}/history",
    response_model=List[SnapshotSummary],
)
def list_snapshots(
    event_id: int,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """List all publish snapshots for an event (newest first)."""
    _get_event_or_404(event_id, admin, db)

    rows = (
        db.query(PublishSnapshot)
        .filter(PublishSnapshot.event_id == event_id)
        .order_by(PublishSnapshot.version.desc())
        .all()
    )
    return [
        SnapshotSummary(
            id=r.id,
            version=r.version,
            task_count=r.task_count,
            person_count=r.person_count,
            edits_count=r.edits_count,
            source=r.source,
            label=r.label,
            frozen=r.frozen if r.frozen is not None else False,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get(
    "/events/{event_id}/history/{version}",
    response_model=SnapshotDetail,
)
def get_snapshot(
    event_id: int,
    version: int,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Get full snapshot detail for a specific version."""
    _get_event_or_404(event_id, admin, db)

    snap = (
        db.query(PublishSnapshot)
        .filter(
            PublishSnapshot.event_id == event_id,
            PublishSnapshot.version == version,
        )
        .first()
    )
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot version not found")

    return SnapshotDetail(
        id=snap.id,
        version=snap.version,
        task_count=snap.task_count,
        person_count=snap.person_count,
        edits_count=snap.edits_count,
        source=snap.source,
        created_at=snap.created_at,
        snapshot=json.loads(snap.snapshot_json),
    )


@router.patch(
    "/events/{event_id}/history/{version}",
    response_model=SnapshotSummary,
)
def patch_snapshot(
    event_id: int,
    version: int,
    body: SnapshotPatch,
    request: Request,
    admin: User = Depends(require_admin_or_issuer),
    db: Session = Depends(get_db),
):
    """Update label and/or frozen status of a snapshot."""
    _get_event_or_404(event_id, admin, db)

    snap = (
        db.query(PublishSnapshot)
        .filter(
            PublishSnapshot.event_id == event_id,
            PublishSnapshot.version == version,
        )
        .first()
    )
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot version not found")

    if body.label is not None:
        snap.label = body.label.strip()[:100] or None

    if body.frozen is not None and body.frozen != snap.frozen:
        if body.frozen:
            frozen_count = (
                db.query(sa_func.count(PublishSnapshot.id))
                .filter(
                    PublishSnapshot.event_id == event_id,
                    PublishSnapshot.frozen == True,  # noqa: E712
                )
                .scalar()
            ) or 0
            if frozen_count >= rt.get_int("max_snapshots_per_event", db):
                raise HTTPException(
                    status_code=409,
                    detail="All snapshot slots are frozen",
                )
        snap.frozen = body.frozen

    audit(
        db,
        user=admin,
        action="history.update",
        resource_type="publish_snapshot",
        resource_id=snap.id,
        detail=json.dumps(
            {
                "event_id": event_id,
                "version": version,
                "changed_fields": sorted(body.model_fields_set),
            }
        ),
        request=request,
    )
    db.commit()
    db.refresh(snap)
    return SnapshotSummary(
        id=snap.id,
        version=snap.version,
        task_count=snap.task_count,
        person_count=snap.person_count,
        edits_count=snap.edits_count,
        source=snap.source,
        label=snap.label,
        frozen=snap.frozen if snap.frozen is not None else False,
        created_at=snap.created_at,
    )


@router.delete(
    "/events/{event_id}/history/{version}",
)
def delete_snapshot(
    event_id: int,
    version: int,
    request: Request,
    admin: User = Depends(require_recent_reauth),
    db: Session = Depends(get_db),
):
    """Delete an accessible snapshot after recent passkey verification."""
    _get_event_or_404(event_id, admin, db)

    snap = (
        db.query(PublishSnapshot)
        .filter(
            PublishSnapshot.event_id == event_id,
            PublishSnapshot.version == version,
        )
        .first()
    )
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot version not found")
    if snap.frozen:
        raise HTTPException(status_code=409, detail="Unfreeze snapshot before deleting")

    db.delete(snap)
    audit(
        db,
        user=admin,
        action="history.delete",
        resource_type="publish_snapshot",
        resource_id=snap.id,
        detail=json.dumps({"event_id": event_id, "version": version}),
        request=request,
    )
    db.commit()
    return {"status": "ok", "deleted_version": version}


@router.post(
    "/events/{event_id}/history/{version}/restore",
    response_model=RestoreResponse,
)
def restore_snapshot(
    event_id: int,
    version: int,
    request: Request,
    admin: User = Depends(require_recent_reauth),
    db: Session = Depends(get_db),
):
    """Restore a snapshot as the live schedule.

    1. Snapshots the current live state first (so rollback is reversible).
    2. Wipes current tasks + edits + persons.
    3. Re-inserts tasks and persons from the snapshot's raw_tasks / persons.
    4. Re-links users by email.
    5. Sends push notification.
    """
    event = _get_event_or_404(event_id, admin, db)

    # Load the target snapshot
    snap = (
        db.query(PublishSnapshot)
        .filter(
            PublishSnapshot.event_id == event_id,
            PublishSnapshot.version == version,
        )
        .first()
    )
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot version not found")

    # 1. Snapshot current live state first (makes this rollback reversible)
    create_snapshot(event, db, source=f"pre-rollback to v{version} by {admin.display_name}")

    # 2. Wipe current data
    existing_task_ids = [
        t.id for t in
        db.query(PublishedTask.id).filter(PublishedTask.event_id == event.id).all()
    ]
    if existing_task_ids:
        db.query(TaskEdit).filter(TaskEdit.task_id.in_(existing_task_ids)).delete(
            synchronize_session=False,
        )
    edits_cleared = len(existing_task_ids)

    db.query(PublishedTask).filter(PublishedTask.event_id == event.id).delete(
        synchronize_session=False,
    )
    db.query(PublishedPerson).filter(PublishedPerson.event_id == event.id).delete(
        synchronize_session=False,
    )
    db.query(PublishedPersonUnavailability).filter(
        PublishedPersonUnavailability.event_id == event.id,
    ).delete(synchronize_session=False)

    # 3. Parse snapshot and re-insert
    data = json.loads(snap.snapshot_json)
    raw_tasks = data.get("raw_tasks", [])
    persons = data.get("persons", [])
    unavailabilities = data.get("unavailabilities", [])

    # Restore event metadata from snapshot
    event_meta = data.get("event_meta")
    if event_meta:
        if event_meta.get("name"):
            event.name = event_meta["name"]
        if event_meta.get("start_date"):
            event.start_date = datetime.strptime(event_meta["start_date"], "%Y-%m-%d").date()
        if event_meta.get("end_date"):
            event.end_date = datetime.strptime(event_meta["end_date"], "%Y-%m-%d").date()
        if event_meta.get("metadata_json") is not None:
            event.metadata_json = event_meta["metadata_json"]

    for p in persons:
        db.add(PublishedPerson(
            event_id=event.id,
            external_person_id=p["external_person_id"],
            first_name=p["first_name"],
            last_name=p["last_name"],
            email=p.get("email"),
        ))

    for interval in unavailabilities:
        db.add(PublishedPersonUnavailability(
            event_id=event.id,
            external_person_id=interval["external_person_id"],
            working_date=interval["working_date"],
            start_datetime=interval["start_datetime"],
            end_datetime=interval["end_datetime"],
        ))

    for t in raw_tasks:
        db.add(PublishedTask(
            event_id=event.id,
            external_task_id=t["external_task_id"],
            name=t["name"],
            summary=t.get("summary"),
            description=t.get("description"),
            start_datetime=datetime.fromisoformat(t["start_datetime"]),
            end_datetime=datetime.fromisoformat(t["end_datetime"]),
            location_name=t.get("location_name"),
            location_address=t.get("location_address"),
            task_type_code=t.get("task_type_code"),
            task_type_name=t.get("task_type_name"),
            color=t.get("color"),
            attendees_json=t.get("attendees_json"),
            field_assignments_json=t.get("field_assignments_json"),
            field_values_json=t.get("field_values_json"),
            field_definitions_json=t.get("field_definitions_json"),
            additional_json=t.get("additional_json"),
            sort_order=t.get("sort_order"),
            web_created=t.get("web_created", False),
        ))

    db.flush()

    # 4. Re-link users by email
    from app.api.v1.publish import _auto_link_users_by_email
    _auto_link_users_by_email(event.id, db)

    audit(
        db,
        user=admin,
        action="history.restore",
        resource_type="publish_snapshot",
        resource_id=snap.id,
        detail=json.dumps({"event_id": event_id, "version": version}),
        request=request,
    )
    db.commit()

    # 5. Push notification
    try:
        from app.core.push import send_push_to_event
        send_push_to_event(
            event_id=event.id,
            title="Schedule Restored",
            body=f"{event.name} schedule restored to version {version}.",
            url=f"/calendar?event={event.id}",
            db=db,
            notification_type="schedule",
        )
    except Exception as exc:
        logger.warning("History restore push delivery failed (%s)", type(exc).__name__)

    return RestoreResponse(
        status="ok",
        restored_version=version,
        tasks_created=len(raw_tasks),
        persons_created=len(persons),
        edits_cleared=edits_cleared,
    )
