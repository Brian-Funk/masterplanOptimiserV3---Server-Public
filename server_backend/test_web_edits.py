"""Tests for server web-edit visibility."""
from datetime import datetime, timezone

from app.models.published import PublishedTask, TaskEdit
from server_backend.conftest import (
    _make_client,
    create_test_event,
    create_test_user,
)


def _add_task(db, event_id: int, name: str = "Opening Briefing") -> PublishedTask:
    """Insert one desktop-published task."""
    task = PublishedTask(
        event_id=event_id,
        external_task_id=101,
        name=name,
        start_datetime=datetime(2026, 5, 21, 9, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc),
        location_name="Room A",
        attendees_json='[{"name":"Anna Smith","person_id":1}]',
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_web_edits_summary_unknown_without_published_baseline(db, root_client):
    """Events without a desktop-published baseline report unknown state."""
    event, _ = create_test_event(db, name="No Baseline")

    r = root_client.get(f"/api/v1/admin/events/{event.id}/web-edits")

    assert r.status_code == 200
    data = r.json()
    assert data["level"] == "unknown"
    assert data["has_published_baseline"] is False
    assert data["edited_task_count"] == 0


def test_web_edits_summary_healthy_without_edits(db, root_client):
    """A baseline with no committed web edits is healthy."""
    event, _ = create_test_event(db, name="Healthy")
    _add_task(db, event.id)

    r = root_client.get(f"/api/v1/admin/events/{event.id}/web-edits")

    assert r.status_code == 200
    data = r.json()
    assert data["level"] == "healthy"
    assert data["headline"] == "No web edits"
    assert data["edited_task_count"] == 0


def test_web_edits_summary_lists_editor_timestamp_and_changes(db, root_client):
    """The review list includes who changed a task, when, and what changed."""
    event, _ = create_test_event(db, name="Edited")
    editor = create_test_user(
        db,
        username="anna.editor",
        display_name="Anna Editor",
        event_id=event.id,
        can_edit=True,
    )
    task = _add_task(db, event.id)
    edit = TaskEdit(
        task_id=task.id,
        edited_by_user_id=editor.id,
        start_datetime=datetime(2026, 5, 21, 9, 30, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc),
        location_name="Room B",
        edited_at=datetime(2026, 5, 21, 12, 20, tzinfo=timezone.utc),
    )
    db.add(edit)
    db.commit()

    r = root_client.get(f"/api/v1/admin/events/{event.id}/web-edits")

    assert r.status_code == 200
    data = r.json()
    assert data["level"] == "review"
    assert data["edited_task_count"] == 1
    assert data["last_edited_by"] == "Anna Editor"
    assert data["items"][0]["task_name"] == "Opening Briefing"
    assert data["items"][0]["edited_by"] == "Anna Editor"
    assert "Time changed" in data["items"][0]["change_summary"]
    assert "Location changed" in data["items"][0]["change_summary"]


def test_issuer_can_only_view_own_event_web_edits(db, issuer_client):
    """Issuers can inspect only the event they are assigned to."""
    client, _issuer, own_event = issuer_client
    other_event, _ = create_test_event(db, name="Other")

    own = client.get(f"/api/v1/admin/events/{own_event.id}/web-edits")
    other = client.get(f"/api/v1/admin/events/{other_event.id}/web-edits")

    assert own.status_code == 200
    assert other.status_code == 403


def test_calendar_response_includes_web_edit_metadata_for_editors(db):
    """Editors receive web-edit metadata for task markers."""
    event, _ = create_test_event(db, name="Calendar")
    editor = create_test_user(
        db,
        username="ben.editor",
        display_name="Ben Editor",
        event_id=event.id,
        can_edit=True,
    )
    task = _add_task(db, event.id)
    db.add(
        TaskEdit(
            task_id=task.id,
            edited_by_user_id=editor.id,
            location_name="Room C",
            edited_at=datetime(2026, 5, 21, 14, 20, tzinfo=timezone.utc),
        )
    )
    db.commit()
    client = _make_client(db, editor)

    r = client.get(f"/api/v1/calendar/{event.id}")

    assert r.status_code == 200
    task_data = r.json()["tasks"][0]
    assert task_data["has_web_edit"] is True
    assert task_data["web_edit_edited_by"] == "Ben Editor"
    assert task_data["web_edit_edited_by_user_id"] == editor.id
    assert task_data["web_edit_change_summary"] == ["Location changed"]


def test_calendar_response_hides_web_edit_details_from_participants(db):
    """Participants see edited markers without admin-level review details."""
    event, _ = create_test_event(db, name="Participant View")
    editor = create_test_user(
        db,
        username="editor.user",
        display_name="Editor User",
        event_id=event.id,
        can_edit=True,
    )
    participant = create_test_user(
        db,
        username="participant.user",
        display_name="Participant User",
        event_id=event.id,
        can_edit=False,
    )
    task = _add_task(db, event.id)
    db.add(
        TaskEdit(
            task_id=task.id,
            edited_by_user_id=editor.id,
            location_name="Room C",
            edited_at=datetime(2026, 5, 21, 14, 20, tzinfo=timezone.utc),
        )
    )
    db.commit()
    client = _make_client(db, participant)

    r = client.get(f"/api/v1/calendar/{event.id}")

    assert r.status_code == 200
    task_data = r.json()["tasks"][0]
    assert task_data["has_web_edit"] is True
    assert task_data["web_edit_edited_by"] is None
    assert task_data["web_edit_edited_by_user_id"] is None
    assert task_data["web_edit_change_summary"] == []



def test_revert_single_web_edit_updates_summary_and_audit(db, root_client):
    """Admins can revert one committed web edit from the central review flow."""
    from app.models.audit import AuditLog

    event, _ = create_test_event(db, name="Single Revert")
    editor = create_test_user(
        db,
        username="single.editor",
        display_name="Single Editor",
        event_id=event.id,
        can_edit=True,
    )
    task = _add_task(db, event.id)
    db.add(
        TaskEdit(
            task_id=task.id,
            edited_by_user_id=editor.id,
            location_name="Room B",
            edited_at=datetime(2026, 5, 21, 14, 20, tzinfo=timezone.utc),
        )
    )
    db.commit()

    r = root_client.post(f"/api/v1/admin/events/{event.id}/web-edits/{task.id}/revert")

    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["remaining_web_edit_count"] == 0
    assert db.query(TaskEdit).filter(TaskEdit.task_id == task.id).first() is None
    audit_row = (
        db.query(AuditLog)
        .filter(AuditLog.action == "web_edit.revert")
        .first()
    )
    assert audit_row is not None


def test_revert_all_web_edits_reverts_multiple_tasks(db, root_client):
    """Admins can revert every committed web edit for one event."""
    event, _ = create_test_event(db, name="Bulk Revert")
    editor = create_test_user(
        db,
        username="bulk.editor",
        display_name="Bulk Editor",
        event_id=event.id,
        can_edit=True,
    )
    first = _add_task(db, event.id, name="Opening")
    second = _add_task(db, event.id, name="Closing")
    second.external_task_id = 102
    db.add_all(
        [
            TaskEdit(
                task_id=first.id,
                edited_by_user_id=editor.id,
                location_name="Room B",
            ),
            TaskEdit(
                task_id=second.id,
                edited_by_user_id=editor.id,
                location_name="Room C",
            ),
        ]
    )
    db.commit()

    r = root_client.post(
        f"/api/v1/admin/events/{event.id}/web-edits/revert",
        json={"revert_all": True},
    )

    assert r.status_code == 200
    data = r.json()
    assert data["reverted_count"] == 2
    assert data["remaining_web_edit_count"] == 0
    assert db.query(TaskEdit).count() == 0


def test_revert_selected_web_edits_leaves_unselected_edits(db, root_client):
    """Selected bulk revert keeps unrelated web edits for further review."""
    event, _ = create_test_event(db, name="Selected Revert")
    editor = create_test_user(
        db,
        username="selected.editor",
        display_name="Selected Editor",
        event_id=event.id,
        can_edit=True,
    )
    first = _add_task(db, event.id, name="Opening")
    second = _add_task(db, event.id, name="Closing")
    second.external_task_id = 102
    db.add_all(
        [
            TaskEdit(
                task_id=first.id,
                edited_by_user_id=editor.id,
                location_name="Room B",
            ),
            TaskEdit(
                task_id=second.id,
                edited_by_user_id=editor.id,
                location_name="Room C",
            ),
        ]
    )
    db.commit()

    r = root_client.post(
        f"/api/v1/admin/events/{event.id}/web-edits/revert",
        json={"task_ids": [first.id]},
    )

    assert r.status_code == 200
    data = r.json()
    assert data["reverted_count"] == 1
    assert data["remaining_web_edit_count"] == 1
    assert db.query(TaskEdit).filter(TaskEdit.task_id == first.id).first() is None
    assert db.query(TaskEdit).filter(TaskEdit.task_id == second.id).first() is not None


def test_revert_web_created_task_deletes_the_task(db, root_client):
    """Web-created tasks are removed when reverted to the desktop baseline."""
    event, _ = create_test_event(db, name="Web Created")
    task = PublishedTask(
        event_id=event.id,
        external_task_id=999,
        name="Ad-hoc Web Task",
        start_datetime=datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc),
        location_name="Room D",
        attendees_json="[]",
        web_created=True,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    r = root_client.post(f"/api/v1/admin/events/{event.id}/web-edits/{task.id}/revert")

    assert r.status_code == 200
    assert r.json()["remaining_web_edit_count"] == 0
    assert db.query(PublishedTask).filter(PublishedTask.id == task.id).first() is None
