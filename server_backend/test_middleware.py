"""Tests for CSRF enforcement and content-type middleware."""
from server_backend.conftest import (
    _raw_client,
    create_test_event,
    create_test_user,
    inject_session,
)


def test_csrf_required_on_write(db):
    """POST to cookie-authenticated endpoint without CSRF returns 403."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(
        db, username="csrf_user", is_admin=True, event_id=event.id,
    )
    raw_token, csrf = inject_session(db, user)

    client = _raw_client(
        cookies={"session_id": raw_token, "csrf_token": csrf},
        headers={"Content-Type": "application/json"},
    )

    r = client.post("/api/v1/admin/events", json={"name": "CSRF Test"})
    assert r.status_code == 403
    assert "CSRF" in r.json().get("detail", "")


def test_csrf_not_required_on_get(db, admin_client):
    """GET requests do not need CSRF tokens."""
    r = admin_client.get("/api/v1/admin/events")
    assert r.status_code == 200


def test_content_type_enforcement(db):
    """POST to API without application/json content-type returns 415."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(
        db, username="ct_user", is_admin=True, event_id=event.id,
    )
    raw_token, csrf = inject_session(db, user)

    client = _raw_client(
        cookies={"session_id": raw_token, "csrf_token": csrf},
        headers={
            "X-CSRF-Token": csrf,
            "Content-Type": "text/plain",
        },
    )

    r = client.post("/api/v1/admin/events", content='{"name": "CT Test"}')
    assert r.status_code == 415


def test_body_size_limit(db):
    """Request exceeding body size limit returns 413."""
    client = _raw_client(
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(10 * 1024 * 1024),
        },
    )

    r = client.post("/api/v1/admin/events", content=b"x" * 100)
    assert r.status_code == 413


def test_publish_exempt_from_csrf(db):
    """Publish endpoint uses Bearer token, not CSRF."""
    event, secret = create_test_event(db, name="Evt")

    client = _raw_client(
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
    )

    r = client.post("/api/v1/publish/publish", json={
        "contract_version": "2026-07-30",
        "tasks": [],
        "persons": [],
    })
    assert r.status_code == 200


def test_health_check(db):
    """Health endpoint returns ok."""
    client = _raw_client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] in ("ok", "degraded")
