"""Tests for admin event endpoints."""
from app.api.v1 import admin as admin_api
from app.core.ha_replication import HAProtectionResult
from app.models.event import Event
from server_backend.conftest import (
    create_test_event, create_test_user, _make_client,
)


# ── POST /admin/events ──


def test_create_event(db, admin_client):
    """Admin can create an event; publish secret returned once."""
    r = admin_client.post("/api/v1/admin/events", json={
        "name": "New Event",
        "location": "Zurich",
        "start_date": "2026-08-01",
        "end_date": "2026-08-10",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["event"]["name"] == "New Event"
    assert data["event"]["evidence_id"]
    assert "publish_secret" in data
    assert len(data["publish_secret"]) > 20


def test_create_event_minimal(db, admin_client):
    """Event can be created with just a name."""
    r = admin_client.post("/api/v1/admin/events", json={
        "name": "Minimal Event",
    })
    assert r.status_code == 200
    assert r.json()["event"]["name"] == "Minimal Event"


def test_create_event_handles_concurrent_protection_rollback(db, admin_client, monkeypatch):
    def fail_after_removal(_reason: str) -> HAProtectionResult:
        event = db.query(Event).filter(Event.name == "Rolled back event").one()
        db.delete(event)
        db.commit()
        return HAProtectionResult(False, error_code="synthetic_capture_failure")

    monkeypatch.setattr(admin_api, "protect_current_state", fail_after_removal)
    response = admin_client.post("/api/v1/admin/events", json={"name": "Rolled back event"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "standby_protection_failed"
    assert db.query(Event).filter(Event.name == "Rolled back event").first() is None


def test_create_event_rejects_end_date_before_start_date(db, admin_client):
    response = admin_client.post("/api/v1/admin/events", json={
        "name": "Invalid date range",
        "start_date": "2031-08-20",
        "end_date": "2031-08-12",
    })

    assert response.status_code == 422
    assert "End date must be on or after start date" in response.text


def test_create_event_allows_same_day_range(db, admin_client):
    response = admin_client.post("/api/v1/admin/events", json={
        "name": "Same-day event",
        "start_date": "2031-08-20",
        "end_date": "2031-08-20",
    })

    assert response.status_code == 200
    assert response.json()["event"]["end_date"] == "2031-08-20"


# ── GET /admin/events ──


def test_list_events(db, admin_client):
    """Admin can list all events."""
    event_a, _ = create_test_event(db, name="Event A")
    event_b, _ = create_test_event(db, name="Event B")

    r = admin_client.get("/api/v1/admin/events")
    assert r.status_code == 200
    events = r.json()
    names = [e["name"] for e in events]
    assert "Event A" in names
    assert "Event B" in names
    evidence_ids = {e["evidence_id"] for e in events}
    assert event_a.evidence_id in evidence_ids
    assert event_b.evidence_id in evidence_ids


# ── Issuer cannot access event endpoints ──


def test_issuer_cannot_create_event(db):
    """Issuers are blocked from event creation (require_admin)."""
    event, _ = create_test_event(db, name="Evt")
    issuer = create_test_user(
        db, username="iss_evt", is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer)

    r = client.post("/api/v1/admin/events", json={
        "name": "Should Fail",
    })
    assert r.status_code == 403


def test_issuer_cannot_list_events(db):
    """Issuers are blocked from listing events."""
    event, _ = create_test_event(db, name="Evt")
    issuer = create_test_user(
        db, username="iss_list_evt", is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer)

    r = client.get("/api/v1/admin/events")
    assert r.status_code == 403


# ── DELETE /admin/events/{id} ──


def test_delete_event_requires_reauth(db, admin_client):
    """Event deletion requires re-authentication."""
    event, _ = create_test_event(db, name="To Delete")
    r = admin_client.delete(f"/api/v1/admin/events/{event.id}")
    assert r.status_code == 403


def test_delete_event_requires_accountable_case(db, reauth_admin_client):
    """Direct event deletion cannot bypass the accountable erasure workflow."""
    event, _ = create_test_event(db, name="Cascade Evt")
    user = create_test_user(db, username="evt_user", event_id=event.id)
    user_id = user.id

    r = reauth_admin_client.delete(f"/api/v1/admin/events/{event.id}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "DELETION_CASE_REQUIRED"
    assert (
        f"/api/v1/admin/deletion-requests/events/{event.id}"
        in r.json()["detail"]["message"]
    )

    from app.models.user import User
    remaining = db.query(User).filter(User.id == user_id).first()
    assert remaining is not None
    assert remaining.event_id == event.id


def test_rejected_direct_delete_preserves_privileged_accounts(db, reauth_admin_client):
    """A rejected shortcut leaves privileged accounts and sessions unchanged."""
    event, _ = create_test_event(db, name="Privileged Event")
    privileged = create_test_user(
        db,
        username="event.issuer",
        event_id=event.id,
        is_issuer=True,
    )
    privileged_client = _make_client(db, privileged)

    response = reauth_admin_client.delete(f"/api/v1/admin/events/{event.id}")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DELETION_CASE_REQUIRED"
    db.refresh(privileged)
    assert privileged.event_id == event.id
    assert privileged.is_issuer is True
    assert privileged_client.get("/api/v1/auth/me").status_code == 200


def test_delete_event_not_found(db, reauth_admin_client):
    """Deleting non-existent event → 404."""
    r = reauth_admin_client.delete("/api/v1/admin/events/99999")
    assert r.status_code == 404


# ── POST /admin/events/{id}/regenerate-secret ──


def test_regenerate_secret(db, reauth_admin_client):
    """Admin with reauth can regenerate an event's publish secret."""
    event, old_secret = create_test_event(db, name="Regen Evt")
    r = reauth_admin_client.post(f"/api/v1/admin/events/{event.id}/regenerate-secret")
    assert r.status_code == 200
    new_secret = r.json()["publish_secret"]
    assert new_secret != old_secret
    assert len(new_secret) > 20


def test_regenerate_secret_handles_event_removed_during_protection(
    db, reauth_admin_client, monkeypatch,
):
    event, _old_secret = create_test_event(db, name="Concurrent removal")

    def fail_after_removal(_reason: str) -> HAProtectionResult:
        current = db.query(Event).filter(Event.id == event.id).one()
        db.delete(current)
        db.commit()
        return HAProtectionResult(False, error_code="synthetic_capture_failure")

    monkeypatch.setattr(admin_api, "protect_current_state", fail_after_removal)
    response = reauth_admin_client.post(
        f"/api/v1/admin/events/{event.id}/regenerate-secret"
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "standby_protection_failed"
