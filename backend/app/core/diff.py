"""
Schedule diff helpers - compute per-person change summaries between publishes.
"""
import json
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.published import PublishedTask, TaskEdit
from app.models.user import User


# Fields compared when detecting task modifications
_DIFF_FIELDS = ("name", "start", "end", "location_name", "location_address")


def _resolve_task(task: PublishedTask, edit: Optional[TaskEdit]) -> Optional[dict]:
    """Resolve a task with its edit overlay into a comparable dict.
    Returns None if the task is soft-deleted."""
    if edit and edit.is_deleted:
        return None

    attendees = json.loads(task.attendees_json) if task.attendees_json else []

    name = task.name
    start = task.start_datetime.isoformat() if task.start_datetime else None
    end = task.end_datetime.isoformat() if task.end_datetime else None
    location_name = task.location_name
    location_address = task.location_address

    if edit:
        if edit.name is not None:
            name = edit.name
        if edit.start_datetime:
            start = edit.start_datetime.isoformat()
        if edit.end_datetime:
            end = edit.end_datetime.isoformat()
        if edit.location_name is not None:
            location_name = edit.location_name
        if edit.location_address is not None:
            location_address = edit.location_address
        if edit.attendees_json:
            parsed = json.loads(edit.attendees_json)
            if parsed is not None:
                attendees = parsed

    return {
        "external_task_id": task.external_task_id,
        "name": name,
        "start": start,
        "end": end,
        "location_name": location_name,
        "location_address": location_address,
        "attendees": attendees,
    }


def _build_person_task_map(
    tasks: List[PublishedTask],
    edits_map: Dict[int, TaskEdit],
) -> Dict[int, Dict[int, dict]]:
    """Build {person_id: {external_task_id: task_dict}} from resolved tasks."""
    result: Dict[int, Dict[int, dict]] = {}
    for task in tasks:
        resolved = _resolve_task(task, edits_map.get(task.id))
        if resolved is None:
            continue
        for att in resolved["attendees"]:
            pid = att.get("person_id")
            if pid is None:
                continue
            result.setdefault(pid, {})[resolved["external_task_id"]] = resolved
    return result


def _task_summary(t: dict) -> dict:
    """Extract display-friendly summary from a task dict."""
    return {
        "name": t["name"],
        "start": t["start"],
        "end": t["end"],
        "location": t.get("location_name") or "",
    }


def compute_per_person_diffs(
    old_tasks: List[PublishedTask],
    old_edits_map: Dict[int, TaskEdit],
    new_tasks: List[PublishedTask],
    new_edits_map: Optional[Dict[int, TaskEdit]] = None,
) -> Dict[int, dict]:
    """Compare old resolved tasks against new live tasks per person.

    Returns {person_id: changes_dict} for persons with actual changes.
    changes_dict has keys: type, summary, added, removed, modified.
    """
    old_map = _build_person_task_map(old_tasks, old_edits_map)
    new_map = _build_person_task_map(new_tasks, new_edits_map or {})

    all_person_ids = set(old_map.keys()) | set(new_map.keys())
    is_initial = len(old_tasks) == 0

    diffs: Dict[int, dict] = {}

    for pid in all_person_ids:
        old_person_tasks = old_map.get(pid, {})
        new_person_tasks = new_map.get(pid, {})

        old_ids = set(old_person_tasks.keys())
        new_ids = set(new_person_tasks.keys())

        added = [_task_summary(new_person_tasks[tid]) for tid in sorted(new_ids - old_ids)]
        removed = [_task_summary(old_person_tasks[tid]) for tid in sorted(old_ids - new_ids)]

        modified = []
        for tid in sorted(old_ids & new_ids):
            old_t = old_person_tasks[tid]
            new_t = new_person_tasks[tid]
            changes = {}
            for field in _DIFF_FIELDS:
                old_val = old_t.get(field)
                new_val = new_t.get(field)
                if old_val != new_val:
                    changes[field] = {"old": old_val, "new": new_val}
            # Check attendee list change (just names for readability)
            old_names = sorted(a.get("name", "") for a in old_t.get("attendees", []))
            new_names = sorted(a.get("name", "") for a in new_t.get("attendees", []))
            if old_names != new_names:
                changes["attendees"] = {
                    "old": ", ".join(old_names),
                    "new": ", ".join(new_names),
                }
            if changes:
                modified.append({
                    "name": new_t["name"],
                    "changes": changes,
                })

        if not added and not removed and not modified:
            continue

        total = len(added) + len(removed) + len(modified)
        change_type = "initial" if is_initial else "republish"
        summary = (
            f"Your schedule has been published with {total} task{'s' if total != 1 else ''}"
            if is_initial
            else f"{total} change{'s' if total != 1 else ''} to your schedule"
        )

        diffs[pid] = {
            "type": change_type,
            "summary": summary,
            "added": added,
            "removed": removed,
            "modified": modified,
        }

    return diffs


def store_schedule_changes(
    event_id: int,
    diffs: Dict[int, dict],
    db: Session,
) -> int:
    """Store per-person diffs as ScheduleChange records for linked users.
    Returns number of records created."""
    if not diffs:
        return 0

    from app.models.notification import ScheduleChange

    person_ids = list(diffs.keys())
    # Find users linked to these persons for this event
    linked_users = (
        db.query(User)
        .filter(
            User.event_id == event_id,
            User.linked_person_id.in_(person_ids),
        )
        .all()
    )

    count = 0
    for user in linked_users:
        person_diff = diffs.get(user.linked_person_id)
        if person_diff is None:
            continue
        db.add(ScheduleChange(
            user_id=user.id,
            event_id=event_id,
            changes_json=json.dumps(person_diff, default=str),
        ))
        count += 1

    return count
