"""Tests for GDPR endpoints - data export, deletion request, anonymisation."""
from server_backend.conftest import (
    create_test_event, create_test_user, _make_client, inject_session,
)


# ── GET /admin/users/{id}/export ──


def test_gdpr_export(db, reauth_admin_client):
    """A recently re-authenticated admin can export user data."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(db, username="exportme", event_id=event.id)

    r = reauth_admin_client.get(f"/api/v1/admin/users/{user.id}/export")
    assert r.status_code == 200
    data = r.json()
    assert data["user"]["username"] == "exportme"
    assert "sessions_count" in data
    assert "credentials_count" in data


def test_gdpr_export_not_found(db, reauth_admin_client):
    """Export for non-existent user → 404."""
    r = reauth_admin_client.get("/api/v1/admin/users/99999/export")
    assert r.status_code == 404


def test_gdpr_export_regular_user_blocked(db):
    """Regular users cannot access GDPR export."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(db, username="noexport", event_id=event.id)
    client = _make_client(db, user)

    r = client.get(f"/api/v1/admin/users/{user.id}/export")
    assert r.status_code == 403


def test_gdpr_export_requires_reauthentication(db, admin_client):
    """An ordinary admin session cannot export personal data without step-up."""
    event, _ = create_test_event(db, name="Export Reauth")
    user = create_test_user(db, username="export.reauth", event_id=event.id)

    response = admin_client.get(f"/api/v1/admin/users/{user.id}/export")

    assert response.status_code == 403
    assert "Re-authentication required" in response.json()["detail"]


# ── DELETE /admin/users/{id}/gdpr-delete (anonymise) ──


def test_gdpr_anonymise_server_only_user_is_ready_for_live_purge(db, reauth_admin_client):
    """A server-only account skips the inapplicable desktop work order."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(db, username="anonme", event_id=event.id)

    r = reauth_admin_client.delete(f"/api/v1/admin/users/{user.id}/gdpr-delete")
    assert r.status_code == 200
    assert r.json()["state"] == "ready_for_live_purge"
    assert "server-only account" in r.json()["message"].lower()

    from app.models.user import User
    from app.models.deletion import DesktopDeletionWorkOrder
    retained = db.query(User).filter(User.id == user.id).one()
    assert retained.username == "anonme"
    assert retained.is_active is False
    assert db.query(DesktopDeletionWorkOrder).filter_by(
        subject_ref=user.evidence_subject_id,
    ).first() is None


def test_guided_deletion_advances_machine_steps_and_stops_for_backup_policy(db):
    """The web workflow purges and prepares review without exposing internal buttons."""

    from app.models.user import User

    root = create_test_user(
        db, username="guided.root", display_name="Guided Root",
        is_root_admin=True, is_admin=True, event_id=None,
    )
    client = _make_client(db, root, reauth=True)
    target = create_test_user(
        db, username="guided.target", display_name="Guided Target", event_id=None,
    )
    target_id = target.id

    started = client.delete(f"/api/v1/admin/users/{target.id}/gdpr-delete")
    assert started.status_code == 200
    request_id = started.json()["request_id"]

    advanced = client.post(f"/api/v1/admin/deletion-requests/{request_id}/advance", json={})
    assert advanced.status_code == 200
    assert advanced.json()["advanced"] == ["live_data_purged"]
    assert advanced.json()["state"] == "awaiting_clean_backup"
    assert db.query(User).filter_by(id=target_id).first() is None

    no_backups = client.post(
        f"/api/v1/admin/deletion-requests/{request_id}/no-controlled-backups",
        json={},
    )
    assert no_backups.status_code == 200
    assert no_backups.json()["evidence"]["backup_not_applicable"]

    prepared = client.post(f"/api/v1/admin/deletion-requests/{request_id}/advance", json={})
    assert prepared.status_code == 200
    assert prepared.json()["advanced"] == ["completion_review_prepared"]
    assert prepared.json()["state"] == "awaiting_approvals"


def test_event_erasure_detail_includes_temporary_operator_label(db):
    """The root UI can identify an open event case without signing its name."""
    event, _ = create_test_event(db, name="Readable Deletion Event")
    root = create_test_user(
        db,
        username="event.label.root",
        is_root_admin=True,
        is_admin=True,
        event_id=None,
    )
    client = _make_client(db, root, reauth=True)

    response = client.post(
        f"/api/v1/admin/deletion-requests/events/{event.id}",
        json={"processor_approval_required": False},
    )

    assert response.status_code == 202
    assert response.json()["event_name"] == "Readable Deletion Event"


def test_gdpr_anonymise_activated_unassigned_account_uses_instance_scope(
    db, reauth_admin_client,
):
    """An activated account without an event can still enter signed erasure."""
    import uuid

    from app.models.deletion import DeletionCase, DesktopDeletionWorkOrder
    from app.models.user import User

    user = create_test_user(db, username="unassigned.used", event_id=None)

    response = reauth_admin_client.delete(
        f"/api/v1/admin/users/{user.id}/gdpr-delete"
    )

    assert response.status_code == 200
    assert response.json()["state"] == "ready_for_live_purge"
    case = db.query(DeletionCase).filter_by(
        request_id=response.json()["request_id"]
    ).one()
    assert uuid.UUID(case.event_evidence_id)
    assert case.event_evidence_id != case.subject_evidence_id
    assert case.desktop_deletion_required is False
    assert db.query(DesktopDeletionWorkOrder).filter_by(case_id=case.id).count() == 0
    retained = db.query(User).filter(User.id == user.id).one()
    assert retained.is_active is False


def test_unassigned_account_can_submit_own_signed_deletion_request(db):
    """Self-service deletion does not require an artificial event assignment."""
    user = create_test_user(db, username="unassigned.self", event_id=None)
    client = _make_client(db, user, reauth=True)

    response = client.post("/api/v1/user/deletion-requests")

    assert response.status_code == 200
    assert response.json()["state"] == "submitted"


def test_gdpr_anonymise_removes_event_linked_identity_and_audit_name(db, reauth_admin_client):
    """Deletion removes duplicated participant identity rather than only unlinking it."""
    import json
    from datetime import datetime
    from app.models.audit import AuditLog
    from app.models.published import (
        PublishedPerson,
        PublishedPersonUnavailability,
        PublishedTask,
        PublishSnapshot,
        TaskEdit,
    )

    event, _ = create_test_event(db, name="Deletion Event")
    person = PublishedPerson(
        event_id=event.id, external_person_id=77,
        first_name="Personal", last_name="Name", email="personal@example.test",
    )
    db.add(person)
    db.flush()
    user = create_test_user(db, username="remove.identity", event_id=event.id)
    user.linked_person_id = 77
    task = PublishedTask(
        event_id=event.id, external_task_id=1, name="Operational task",
        start_datetime=datetime.fromisoformat("2026-07-27T10:00:00+00:00"),
        end_datetime=datetime.fromisoformat("2026-07-27T11:00:00+00:00"),
        attendees_json=json.dumps([{"person_id": 77, "name": "Personal Name"}]),
        additional_json=json.dumps({"operator": {"person_id": 77, "name": "Personal Name"}}),
    )
    db.add(task)
    db.flush()
    db.add(TaskEdit(
        task_id=task.id,
        field_values_json=json.dumps({"lead": {"person_id": 77, "name": "Personal Name"}}),
    ))
    db.add(PublishSnapshot(
        event_id=event.id,
        version=1,
        snapshot_json=json.dumps({
            "persons": [{"id": 77, "first_name": "Personal", "last_name": "Name"}],
            "tasks": [{"id": 77, "name": "Unrelated task name"}],
        }),
        content_hash="a" * 64,
        task_count=1,
        person_count=1,
        edits_count=0,
    ))
    db.add(PublishedPersonUnavailability(
        event_id=event.id, external_person_id=77, working_date="2026-07-27",
        start_datetime="2026-07-27T12:00:00+00:00",
        end_datetime="2026-07-27T13:00:00+00:00",
    ))
    db.add(AuditLog(
        user_id=user.id, username=user.username, action="test.personal",
        detail="remove.identity",
    ))
    db.commit()

    response = reauth_admin_client.delete(f"/api/v1/admin/users/{user.id}/gdpr-delete")
    assert response.status_code == 200
    assert "remove.identity" not in response.text
    from app.core.deletion_cases import apply_desktop_report, claim_work_order
    from app.core.deletion_workflow import purge_subject_live_data
    from app.models.deletion import DeletionCase, DesktopDeletionWorkOrder
    case = db.query(DeletionCase).filter_by(request_id=response.json()["request_id"]).one()
    work_order = db.query(DesktopDeletionWorkOrder).filter_by(case_id=case.id).one()
    capability = claim_work_order(work_order)
    apply_desktop_report(
        db,
        case,
        work_order,
        claim_capability=capability,
        report={
            "version": 1,
            "work_order_id": work_order.work_order_id,
            "event_ref": event.evidence_id,
            "subject_ref": user.evidence_subject_id,
            "operation": "delete_subject",
            "outcome": "deleted",
            "deleted_counts": {
                "persons": 1,
                "assignments": 0,
                "capability_links": 0,
                "group_memberships": 0,
                "unavailability_intervals": 0,
                "task_references": 0,
                "optimisation_records": 0,
                "publish_records": 0,
                "cached_records": 0,
                "tracked_exports": 0,
                "integration_references": 0,
            },
            "outstanding_actions": [],
            "completed_at": "2026-07-28T10:30:00+00:00",
        },
    )
    purge_subject_live_data(db, case, user)
    db.commit()
    db.refresh(person)
    db.refresh(task)
    assert person.first_name == "Deleted"
    assert person.email is None
    assert "Personal Name" not in (task.attendees_json or "")
    assert "Personal Name" not in (task.additional_json or "")
    edit = db.query(TaskEdit).filter_by(task_id=task.id).one()
    assert "Personal Name" not in (edit.field_values_json or "")
    snapshot = db.query(PublishSnapshot).filter_by(event_id=event.id).one()
    assert "Personal" not in snapshot.snapshot_json
    assert "Unrelated task name" in snapshot.snapshot_json
    assert db.query(PublishedPersonUnavailability).filter_by(external_person_id=77).count() == 0
    old_audit = db.query(AuditLog).filter(AuditLog.action == "test.personal").one()
    assert old_audit.user_id is None
    assert old_audit.username is None
    assert old_audit.actor_ref is None
    assert old_audit.detail is None


def test_gdpr_anonymise_root_blocked(db, reauth_admin_client):
    """Cannot anonymise root admin."""
    from app.models.user import User
    root = db.query(User).filter(User.username == "root.admin").first()
    if not root:
        root = create_test_user(
            db, username="root.admin", is_root_admin=True, is_admin=True,
        )

    r = reauth_admin_client.delete(f"/api/v1/admin/users/{root.id}/gdpr-delete")
    assert r.status_code == 403


# ── POST /user/request-deletion ──


def test_user_request_deletion(db):
    """User can request their own deletion."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(db, username="deleteme", event_id=event.id)
    client = _make_client(db, user, reauth=True)

    r = client.post("/api/v1/user/request-deletion")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["state"] == "submitted"
    assert r.json()["request_id"]

    # Verify flag is set
    from app.models.user import User
    updated = db.query(User).filter(User.id == user.id).first()
    assert updated.deletion_requested_at is not None

    from app.models.deletion import DeletionCase
    job = db.query(DeletionCase).filter_by(user_id=user.id).one()
    assert job.request_id == r.json()["request_id"]
    assert job.instance_id != "00000000-0000-0000-0000-000000000000"
    assert job.event_evidence_id == event.evidence_id
    assert job.subject_evidence_id == user.evidence_subject_id


def test_current_deletion_request_returns_durable_receipt(db):
    event, _ = create_test_event(db, name="Deletion receipt")
    user = create_test_user(db, username="delete.receipt", event_id=event.id)
    client = _make_client(db, user, reauth=True)

    created = client.post("/api/v1/user/deletion-requests")
    current = client.get("/api/v1/user/deletion-requests/current")
    receipt = client.get(
        f"/api/v1/user/deletion-requests/{created.json()['request_id']}/receipt"
    )

    assert created.status_code == current.status_code == receipt.status_code == 200
    assert current.json()["request_id"] == created.json()["request_id"]
    assert receipt.json()["request_id"] == created.json()["request_id"]
    assert current.json()["submitted_at"]
    assert current.json()["normal_response_due_at"]


def test_deletion_receipt_is_scoped_to_authenticated_user(db):
    event, _ = create_test_event(db, name="Deletion receipt isolation")
    owner = create_test_user(db, username="delete.owner", event_id=event.id)
    other = create_test_user(db, username="delete.other", event_id=event.id)
    request_id = _make_client(db, owner, reauth=True).post(
        "/api/v1/user/deletion-requests"
    ).json()["request_id"]

    response = _make_client(db, other).get(
        f"/api/v1/user/deletion-requests/{request_id}/receipt"
    )

    assert response.status_code == 404


def test_user_deletion_request_requires_recent_passkey(db):
    event, _ = create_test_event(db, name="Deletion reauthentication")
    user = create_test_user(db, username="delete.reauth", event_id=event.id)

    response = _make_client(db, user).post("/api/v1/user/request-deletion")

    assert response.status_code == 403
    assert response.json()["detail"] == "Re-authentication required"


def test_duplicate_deletion_request_is_idempotent(db):
    event, _ = create_test_event(db, name="Deletion idempotency")
    user = create_test_user(db, username="delete.once", event_id=event.id)
    client = _make_client(db, user, reauth=True)

    first = client.post("/api/v1/user/request-deletion")
    second = client.post("/api/v1/user/request-deletion")

    assert first.status_code == second.status_code == 200
    assert second.json()["status"] == "pending"
    assert first.json()["request_id"] == second.json()["request_id"]


def test_gdpr_delete_keeps_case_link_until_server_only_purge(db, reauth_admin_client):
    from app.models.deletion import DeletionCase

    event, _ = create_test_event(db, name="Deletion evidence")
    user = create_test_user(db, username="delete.evidence", event_id=event.id)
    client = _make_client(db, user, reauth=True)
    request_id = client.post("/api/v1/user/request-deletion").json()["request_id"]

    response = reauth_admin_client.delete(f"/api/v1/admin/users/{user.id}/gdpr-delete")

    assert response.status_code == 200
    job = db.query(DeletionCase).filter_by(request_id=request_id).one()
    assert job.state == "ready_for_live_purge"
    assert job.desktop_deletion_required is False
    assert job.user_id == user.id
    assert job.completed_at is None


def test_root_cannot_request_self_deletion(db, root_client):
    """Root admin cannot request self-deletion."""
    r = root_client.post("/api/v1/user/request-deletion")
    assert r.status_code == 403


# ── DELETE /admin/users/{id}/deletion-request (dismiss) ──


def test_dismiss_deletion_request(db, reauth_admin_client):
    """Admin can dismiss a pending deletion request."""
    from datetime import datetime, timezone
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(db, username="dismiss_target", event_id=event.id)
    client = _make_client(db, user, reauth=True)
    request_id = client.post("/api/v1/user/request-deletion").json()["request_id"]

    r = reauth_admin_client.delete(f"/api/v1/admin/users/{user.id}/deletion-request")
    assert r.status_code == 200

    # Verify flag cleared
    from app.models.user import User
    updated = db.query(User).filter(User.id == user.id).first()
    assert updated.deletion_requested_at is None
    from app.models.deletion import DeletionCase
    job = db.query(DeletionCase).filter_by(request_id=request_id).one()
    assert job.state == "rejected"
    assert job.user_id is None


def test_dismiss_no_pending_request(db, reauth_admin_client):
    """Dismissing when no request is pending → 409."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(db, username="no_request", event_id=event.id)

    r = reauth_admin_client.delete(f"/api/v1/admin/users/{user.id}/deletion-request")
    assert r.status_code == 409


def test_dismiss_deletion_request_requires_reauthentication(db, admin_client):
    event, _ = create_test_event(db, name="Deletion decision reauthentication")
    user = create_test_user(db, username="dismiss.reauth", event_id=event.id)
    _make_client(db, user, reauth=True).post("/api/v1/user/request-deletion")

    response = admin_client.delete(f"/api/v1/admin/users/{user.id}/deletion-request")

    assert response.status_code == 403
    assert response.json()["detail"] == "Re-authentication required"
