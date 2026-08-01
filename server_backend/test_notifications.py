"""Tests for notification and announcement endpoints."""
from server_backend.conftest import (
    create_test_event, create_test_user, _make_client,
)


# ── POST /notifications/announcements ──


def test_create_announcement_admin(db, admin_client):
    """Admin can create an announcement."""
    event, _ = create_test_event(db, name="Ann Evt")
    r = admin_client.post("/api/v1/notifications/announcements", json={
        "event_id": event.id,
        "title": "Test Announcement",
        "body": "This is a test.",
        "push": False,
    })
    assert r.status_code == 201
    data = r.json()
    assert data["title"] == "Test Announcement"
    assert data["event_id"] == event.id


def test_create_announcement_issuer_forced_event(db):
    """Issuer creating announcement → event_id forced to own event."""
    event, _ = create_test_event(db, name="Issuer Evt")
    other_event, _ = create_test_event(db, name="Other Evt")
    issuer = create_test_user(
        db, username="iss_ann", is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer)

    r = client.post("/api/v1/notifications/announcements", json={
        "event_id": other_event.id,  # tries to post to other event
        "title": "Issuer Ann",
        "push": False,
    })
    assert r.status_code == 201
    # Event forced to issuer's own event
    assert r.json()["event_id"] == event.id


def test_create_announcement_regular_user_blocked(db):
    """Regular user cannot create announcements."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(db, username="noann", event_id=event.id)
    client = _make_client(db, user)

    r = client.post("/api/v1/notifications/announcements", json={
        "event_id": event.id,
        "title": "Should Fail",
        "push": False,
    })
    assert r.status_code == 403


# ── GET /notifications/announcements/{event_id} ──


def test_list_announcements(db):
    """Authenticated user can list announcements for their event."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(db, username="reader", event_id=event.id)
    admin = create_test_user(
        db, username="ann_admin", is_admin=True, event_id=event.id,
    )

    # Create announcement as admin
    admin_client = _make_client(db, admin)
    admin_client.post("/api/v1/notifications/announcements", json={
        "event_id": event.id,
        "title": "Hello Everyone",
        "push": False,
    })

    # Read as regular user
    client = _make_client(db, user)
    r = client.get(f"/api/v1/notifications/announcements/{event.id}")
    assert r.status_code == 200
    titles = [a["title"] for a in r.json()]
    assert "Hello Everyone" in titles


def test_list_announcements_cross_event_blocked(db):
    """Changing an event id cannot reveal another event's announcements."""
    own_event, _ = create_test_event(db, name="Own Announcements")
    other_event, _ = create_test_event(db, name="Other Announcements")
    user = create_test_user(
        db,
        username="announcement.viewer",
        event_id=own_event.id,
    )

    response = _make_client(db, user).get(
        f"/api/v1/notifications/announcements/{other_event.id}",
    )

    assert response.status_code == 403


# ── DELETE /notifications/announcements/{id} ──


def test_delete_announcement_admin(db):
    """Admin can delete an announcement."""
    event, _ = create_test_event(db, name="Evt")
    admin = create_test_user(
        db, username="del_admin", is_admin=True, event_id=event.id,
    )
    client = _make_client(db, admin)

    # Create
    r1 = client.post("/api/v1/notifications/announcements", json={
        "event_id": event.id,
        "title": "To Delete",
        "push": False,
    })
    ann_id = r1.json()["id"]

    # Delete
    r2 = client.delete(f"/api/v1/notifications/announcements/{ann_id}")
    assert r2.status_code == 204


def test_delete_announcement_issuer_own_event(db):
    """Issuer can delete announcements from their own event."""
    event, _ = create_test_event(db, name="Evt")
    admin = create_test_user(
        db, username="creator_admin", is_admin=True, event_id=event.id,
    )
    issuer = create_test_user(
        db, username="iss_del", is_issuer=True, event_id=event.id,
    )

    # Create as admin
    admin_client = _make_client(db, admin)
    r1 = admin_client.post("/api/v1/notifications/announcements", json={
        "event_id": event.id,
        "title": "Deletable",
        "push": False,
    })
    ann_id = r1.json()["id"]

    # Delete as issuer (same event)
    issuer_client = _make_client(db, issuer)
    r2 = issuer_client.delete(f"/api/v1/notifications/announcements/{ann_id}")
    assert r2.status_code == 204


def test_delete_announcement_issuer_cross_event_blocked(db):
    """Issuer cannot delete announcements from other events."""
    event1, _ = create_test_event(db, name="Evt1")
    event2, _ = create_test_event(db, name="Evt2")
    admin = create_test_user(
        db, username="cross_admin", is_admin=True, event_id=event2.id,
    )
    issuer = create_test_user(
        db, username="iss_cross_del", is_issuer=True, event_id=event1.id,
    )

    # Create announcement in event2
    admin_client = _make_client(db, admin)
    r1 = admin_client.post("/api/v1/notifications/announcements", json={
        "event_id": event2.id,
        "title": "Other Event Ann",
        "push": False,
    })
    ann_id = r1.json()["id"]

    # Issuer (event1) tries to delete
    issuer_client = _make_client(db, issuer)
    r2 = issuer_client.delete(f"/api/v1/notifications/announcements/{ann_id}")
    assert r2.status_code == 403
