"""Tests for admin event endpoints."""
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.models.event import Event
from app.models.ha import HAProtectionOperation
from server_backend.conftest import (
    create_test_event, create_test_user, _make_client,
)

PUBLISH_SECRET = "p" * 48


def protected_event_body(**values):
    return {
        "publish_secret": PUBLISH_SECRET,
        "idempotency_key": values.pop(
            "idempotency_key", "11111111-1111-4111-8111-111111111111"
        ),
        **values,
    }


# ── POST /admin/events ──


def test_create_event(db, admin_client):
    """Admin can create an event; publish secret returned once."""
    r = admin_client.post("/api/v1/admin/events", json=protected_event_body(
        name="New Event", location="Zurich",
        start_date="2026-08-01", end_date="2026-08-10",
    ))
    assert r.status_code == 200
    data = r.json()
    assert data["event"]["name"] == "New Event"
    assert data["event"]["evidence_id"]
    assert "publish_secret" in data
    assert data["publish_secret"] == PUBLISH_SECRET


def test_create_event_minimal(db, admin_client):
    """Event can be created with just a name."""
    r = admin_client.post("/api/v1/admin/events", json=protected_event_body(name="Minimal Event"))
    assert r.status_code == 200
    assert r.json()["event"]["name"] == "Minimal Event"


def test_create_event_rejects_end_date_before_start_date(db, admin_client):
    response = admin_client.post("/api/v1/admin/events", json=protected_event_body(
        name="Invalid date range", start_date="2031-08-20", end_date="2031-08-12",
    ))

    assert response.status_code == 422
    assert "End date must be on or after start date" in response.text


def test_create_event_allows_same_day_range(db, admin_client):
    response = admin_client.post("/api/v1/admin/events", json=protected_event_body(
        name="Same-day event", start_date="2031-08-20", end_date="2031-08-20",
    ))

    assert response.status_code == 200
    assert response.json()["event"]["end_date"] == "2031-08-20"


def test_ha_event_creation_is_durable_idempotent_and_returns_only_after_polling(
    db, root_client, monkeypatch, tmp_path,
):
    import app.main as main_module
    from app.core import ha_replication

    request_dir = tmp_path / "ha-requests"
    request_dir.mkdir()
    monkeypatch.setattr(settings, "HA_MODE", "ha")
    monkeypatch.setattr(settings, "HA_CLUSTER_ID", "cluster-test")
    monkeypatch.setattr(settings, "HA_NODE_ID", "node-a")
    monkeypatch.setattr(settings, "HA_REPLICATION_REQUEST_DIR", str(request_dir))
    monkeypatch.setattr(main_module, "control_witness_ready", lambda: True)
    monkeypatch.setattr(main_module, "assess_readiness", lambda _db: SimpleNamespace(ready=True))
    monkeypatch.setattr(main_module, "require_write_permit", lambda **_kwargs: None)
    monkeypatch.setattr(ha_replication, "witness_post", lambda *_args, **_kwargs: {})
    body = protected_event_body(
        name="Standby protected event",
        idempotency_key="99999999-9999-4999-8999-999999999999",
    )

    first = root_client.post("/api/v1/admin/events", json=body)
    assert first.status_code == 202
    assert first.json()["publish_secret"] is None
    operation_id = first.json()["protection_operation_id"]
    assert operation_id
    assert db.query(Event).filter(Event.name == "Standby protected event").count() == 1
    operation = db.get(HAProtectionOperation, operation_id)
    assert operation.state == "pending"
    assert (request_dir / f"{operation_id}.json").is_file()

    repeated = root_client.post("/api/v1/admin/events", json=body)
    assert repeated.status_code == 202
    assert repeated.json()["protection_operation_id"] == operation_id
    assert db.query(Event).filter(Event.name == "Standby protected event").count() == 1


def test_ha_queue_failure_rejects_event_before_commit(
    db, root_client, monkeypatch, tmp_path,
):
    import app.main as main_module

    request_dir = tmp_path / "missing-ha-requests"
    monkeypatch.setattr(settings, "HA_MODE", "ha")
    monkeypatch.setattr(settings, "HA_CLUSTER_ID", "cluster-test")
    monkeypatch.setattr(settings, "HA_NODE_ID", "node-a")
    monkeypatch.setattr(settings, "HA_REPLICATION_REQUEST_DIR", str(request_dir))
    monkeypatch.setattr(main_module, "control_witness_ready", lambda: True)
    monkeypatch.setattr(main_module, "assess_readiness", lambda _db: SimpleNamespace(ready=True))
    monkeypatch.setattr(main_module, "require_write_permit", lambda **_kwargs: None)

    response = root_client.post(
        "/api/v1/admin/events",
        json=protected_event_body(
            name="Must not commit",
            idempotency_key="88888888-8888-4888-8888-888888888888",
        ),
    )

    assert response.status_code == 503
    assert response.json()["code"] == "HA_PROTECTION_UNAVAILABLE"
    assert response.json()["reason"] == "replication_queue_missing"
    assert db.query(Event).filter(Event.name == "Must not commit").count() == 0
    assert (
        db.query(HAProtectionOperation)
        .filter(HAProtectionOperation.idempotency_key == "88888888-8888-4888-8888-888888888888")
        .count()
        == 0
    )


@pytest.mark.parametrize(
    "retryable_error",
    ["replication_queue_not_writable", "capture_failed", "runtime_contract_invalid"],
)
def test_root_retry_reuses_the_indeterminate_operation_without_duplicates(
    db, monkeypatch, tmp_path, retryable_error,
):
    import app.main as main_module
    from app.core import ha_replication

    request_dir = tmp_path / "ha-requests"
    request_dir.mkdir()
    monkeypatch.setattr(settings, "HA_MODE", "ha")
    monkeypatch.setattr(settings, "HA_CLUSTER_ID", "cluster-test")
    monkeypatch.setattr(settings, "HA_NODE_ID", "node-a")
    monkeypatch.setattr(settings, "HA_REPLICATION_REQUEST_DIR", str(request_dir))
    monkeypatch.setattr(main_module, "control_witness_ready", lambda: True)
    monkeypatch.setattr(main_module, "assess_readiness", lambda _db: SimpleNamespace(ready=True))
    monkeypatch.setattr(main_module, "require_write_permit", lambda **_kwargs: None)
    monkeypatch.setattr(ha_replication, "witness_post", lambda *_args, **_kwargs: {})
    event, _secret = create_test_event(db, name="Retry protected event")
    operation = ha_replication.create_protection_operation(
        db,
        idempotency_key="retry-operation-00000001",
        operation_type="publisher-secret-create",
        resource_type="event",
        resource_id=str(event.id),
    )
    operation.state = "indeterminate"
    operation.stage = "attention_required"
    operation.error_code = retryable_error
    db.commit()
    root = create_test_user(
        db,
        username="retry.root",
        is_root_admin=True,
        is_admin=True,
    )
    client = _make_client(db, root, reauth=True)

    response = client.post(
        f"/api/v1/admin/ha-protection-operations/{operation.id}/retry",
        json={},
    )

    assert response.status_code == 202
    assert response.json()["state"] == "pending"
    assert response.json()["stage"] == "queued"
    assert (request_dir / f"{operation.id}.json").is_file()
    assert db.query(Event).filter(Event.name == "Retry protected event").count() == 1
    assert db.query(HAProtectionOperation).filter_by(id=operation.id).count() == 1


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

    r = client.post("/api/v1/admin/events", json=protected_event_body(name="Should Fail"))
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
    new_value = "n" * 48
    r = reauth_admin_client.post(
        f"/api/v1/admin/events/{event.id}/regenerate-secret",
        json={
            "publish_secret": new_value,
            "idempotency_key": "22222222-2222-4222-8222-222222222222",
        },
    )
    assert r.status_code == 200
    new_secret = r.json()["publish_secret"]
    assert new_secret != old_secret
    assert new_secret == new_value
