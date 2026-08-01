"""Tests for publish snapshot history endpoints."""
import json

from server_backend.conftest import (
    create_test_event, create_test_user, _make_client,
)
from app.models.published import PublishSnapshot
from app.models.audit import AuditLog


def _create_snapshot(db, event_id: int, version: int = 1, frozen: bool = False):
    """Helper to insert a snapshot directly."""
    snap = PublishSnapshot(
        event_id=event_id,
        version=version,
        snapshot_json=json.dumps({"tasks": [], "persons": []}),
        content_hash="abc123" + str(version),
        task_count=0,
        person_count=0,
        edits_count=0,
        source="test",
        frozen=frozen,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


# ── GET /admin/events/{event_id}/history ──


def test_list_snapshots(db, root_client):
    """Admin can list snapshots for an event."""
    event, _ = create_test_event(db, name="Hist Evt")
    _create_snapshot(db, event.id, version=1)
    _create_snapshot(db, event.id, version=2)

    r = root_client.get(f"/api/v1/admin/events/{event.id}/history")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert data[0]["version"] == 2  # newest first


def test_list_snapshots_issuer(db):
    """Issuer can list snapshots for their own event."""
    event, _ = create_test_event(db, name="Iss Hist Evt")
    _create_snapshot(db, event.id, version=1)
    issuer = create_test_user(
        db, username="iss_hist", is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer, reauth=True)

    r = client.get(f"/api/v1/admin/events/{event.id}/history")
    assert r.status_code == 200


# ── GET /admin/events/{event_id}/history/{version} ──


def test_get_snapshot_detail(db, root_client):
    """Admin can get full snapshot detail."""
    event, _ = create_test_event(db, name="Evt")
    _create_snapshot(db, event.id, version=1)

    r = root_client.get(f"/api/v1/admin/events/{event.id}/history/1")
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == 1
    assert "snapshot" in data


def test_get_snapshot_not_found(db, root_client):
    """Getting non-existent snapshot version returns 404."""
    event, _ = create_test_event(db, name="Evt")
    r = root_client.get(f"/api/v1/admin/events/{event.id}/history/999")
    assert r.status_code == 404


# ── PATCH /admin/events/{event_id}/history/{version} ──


def test_patch_snapshot_label(db, root_client):
    """Admin can set a label on a snapshot."""
    event, _ = create_test_event(db, name="Evt")
    _create_snapshot(db, event.id, version=1)

    r = root_client.patch(
        f"/api/v1/admin/events/{event.id}/history/1",
        json={"label": "Release 1.0"},
    )
    assert r.status_code == 200
    assert r.json()["label"] == "Release 1.0"
    entry = db.query(AuditLog).filter(AuditLog.action == "history.update").one()
    assert json.loads(entry.detail)["changed_fields"] == ["label"]


def test_patch_snapshot_freeze(db, root_client):
    """Admin can freeze a snapshot."""
    event, _ = create_test_event(db, name="Evt")
    _create_snapshot(db, event.id, version=1)

    r = root_client.patch(
        f"/api/v1/admin/events/{event.id}/history/1",
        json={"frozen": True},
    )
    assert r.status_code == 200
    assert r.json()["frozen"] is True


def test_patch_snapshot_issuer_can_label(db):
    """Issuer can label snapshots in their own event."""
    event, _ = create_test_event(db, name="Evt")
    _create_snapshot(db, event.id, version=1)
    issuer = create_test_user(
        db, username="iss_label", is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer, reauth=True)

    r = client.patch(
        f"/api/v1/admin/events/{event.id}/history/1",
        json={"label": "Issuer Label"},
    )
    assert r.status_code == 200


# ── DELETE /admin/events/{event_id}/history/{version} ──


def test_delete_snapshot_admin_only(db):
    """A re-authenticated admin can delete a snapshot."""
    event, _ = create_test_event(db, name="Evt")
    _create_snapshot(db, event.id, version=1)
    admin = create_test_user(db, username="delete.snapshot", is_admin=True)
    client = _make_client(db, admin, reauth=True)

    r = client.delete(f"/api/v1/admin/events/{event.id}/history/1")
    assert r.status_code == 200


def test_delete_snapshot_frozen_blocked(db):
    """Cannot delete a frozen snapshot."""
    event, _ = create_test_event(db, name="Evt")
    _create_snapshot(db, event.id, version=1, frozen=True)
    admin = create_test_user(db, username="delete.frozen", is_admin=True)
    client = _make_client(db, admin, reauth=True)

    r = client.delete(f"/api/v1/admin/events/{event.id}/history/1")
    assert r.status_code == 409


def test_delete_snapshot_issuer_can_delete(db):
    """Issuer can delete snapshots for their own event."""
    event, _ = create_test_event(db, name="Evt")
    _create_snapshot(db, event.id, version=1)
    issuer = create_test_user(
        db, username="iss_del", is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer, reauth=True)

    r = client.delete(f"/api/v1/admin/events/{event.id}/history/1")
    assert r.status_code == 200


# ── POST /admin/events/{event_id}/history/{version}/restore ──


def test_restore_snapshot_admin_only(db):
    """Admin can restore a snapshot."""
    event, _ = create_test_event(db, name="Evt")
    _create_snapshot(db, event.id, version=1)
    admin = create_test_user(
        db, username="restore_admin", is_admin=True, event_id=event.id,
    )
    client = _make_client(db, admin, reauth=True)

    r = client.post(f"/api/v1/admin/events/{event.id}/history/1/restore")
    assert r.status_code == 200
    assert r.json()["restored_version"] == 1
    assert db.query(AuditLog).filter(AuditLog.action == "history.restore").count() == 1


def test_restore_snapshot_ignores_legacy_logo_colours(db):
    """Restoring old snapshots does not reapply legacy logo colours."""
    event, _ = create_test_event(db, name="Evt")
    event.logo_color_1 = "#111111"
    event.logo_color_2 = "#222222"
    snapshot = PublishSnapshot(
        event_id=event.id,
        version=1,
        snapshot_json=json.dumps({
            "event_meta": {
                "name": "Restored",
                "logo_color_1": "#ff0000",
                "logo_color_2": "#00ff00",
            },
            "tasks": [],
            "persons": [],
        }),
        content_hash="legacy-logo",
        task_count=0,
        person_count=0,
        edits_count=0,
        source="test",
    )
    db.add(snapshot)
    db.commit()
    admin = create_test_user(
        db, username="restore_logo_admin", is_admin=True, event_id=event.id,
    )
    client = _make_client(db, admin, reauth=True)

    r = client.post(f"/api/v1/admin/events/{event.id}/history/1/restore")
    assert r.status_code == 200

    db.refresh(event)
    assert event.name == "Restored"
    assert event.logo_color_1 == "#111111"
    assert event.logo_color_2 == "#222222"


def test_restore_snapshot_issuer_can_restore(db):
    """Issuer can restore snapshots for their own event."""
    event, _ = create_test_event(db, name="Evt")
    _create_snapshot(db, event.id, version=1)
    issuer = create_test_user(
        db, username="iss_restore", is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer, reauth=True)

    r = client.post(f"/api/v1/admin/events/{event.id}/history/1/restore")
    assert r.status_code == 200
