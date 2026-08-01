"""Tests for activation link endpoints."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.api.v1.admin as admin_module
from app.models.user import ActivationEmailDelivery, ActivationLink
from server_backend.conftest import (
    create_test_event, create_test_user, _make_client, inject_session,
)


def _request() -> Request:
    """Return a minimal request for direct activation administration tests."""

    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/admin/batch-activation-links",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "scheme": "https",
        "server": ("localhost", 443),
    })


# ── POST /admin/users/{id}/activation-link ──


def test_create_activation_link(db, admin_client):
    """Admin can create an activation link for a user."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(
        db, username="needs_link", event_id=event.id, is_activated=False,
    )

    r = admin_client.post(f"/api/v1/admin/users/{user.id}/activation-link")
    assert r.status_code == 200
    assert "/activate#token=" in r.json()["activation_url"]
    assert r.json()["purpose"] == "initial_setup"


def test_active_user_can_receive_non_destructive_additional_passkey_link(db):
    """A recently re-authenticated administrator can issue additive access."""

    event, _ = create_test_event(db, name="Additional passkey event")
    admin = create_test_user(
        db,
        username="additional.admin",
        is_admin=True,
        event_id=event.id,
    )
    user = create_test_user(
        db,
        username="additional.target",
        event_id=event.id,
        is_activated=True,
    )
    admin._auth_session = SimpleNamespace(reauth_at=datetime.now(timezone.utc))

    response = admin_module.create_user_activation_link(
        user_id=user.id,
        request=_request(),
        body=admin_module.ActivationLinkIn(purpose="additional_passkey"),
        admin=admin,
        db=db,
    )

    assert response.purpose == "additional_passkey"
    assert "/activate#token=" in response.activation_url
    link = db.query(ActivationLink).filter_by(user_id=user.id).one()
    assert link.purpose == "additional_passkey"


def test_pending_user_cannot_receive_credential_management_link(db):
    """Purpose cannot be used to bypass initial account setup semantics."""

    event, _ = create_test_event(db, name="Pending purpose event")
    user = create_test_user(
        db,
        username="pending.purpose",
        event_id=event.id,
        is_activated=False,
    )
    admin = create_test_user(
        db,
        username="pending.purpose.admin",
        event_id=event.id,
        is_admin=True,
    )

    with pytest.raises(HTTPException) as error:
        admin_module.create_user_activation_link(
            user_id=user.id,
            request=_request(),
            body=admin_module.ActivationLinkIn(purpose="additional_passkey"),
            admin=admin,
            db=db,
        )

    assert error.value.status_code == 409
    assert "activated account" in error.value.detail


def test_create_activation_link_not_found(db, admin_client):
    """Activation link for non-existent user → 404."""
    r = admin_client.post("/api/v1/admin/users/99999/activation-link")
    assert r.status_code == 404


def test_create_activation_link_issuer_scoped(db):
    """Issuer can create link for user in same event only."""
    event, _ = create_test_event(db, name="Evt")
    other_event, _ = create_test_event(db, name="Other")
    user_same = create_test_user(
        db, username="same_evt", event_id=event.id, is_activated=False,
    )
    user_other = create_test_user(
        db, username="other_evt", event_id=other_event.id, is_activated=False,
    )
    issuer = create_test_user(
        db, username="iss_link", is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer)

    # Same event → OK
    r1 = client.post(f"/api/v1/admin/users/{user_same.id}/activation-link")
    assert r1.status_code == 200

    # Different event → 403
    r2 = client.post(f"/api/v1/admin/users/{user_other.id}/activation-link")
    assert r2.status_code == 403


# ── GET /admin/users/{id}/activation-links ──


def test_get_activation_link_status(db, admin_client):
    """Admin can get activation link status for a user."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(
        db, username="link_status", event_id=event.id, is_activated=False,
    )
    # Create a link first
    admin_client.post(f"/api/v1/admin/users/{user.id}/activation-link")

    r = admin_client.get(f"/api/v1/admin/users/{user.id}/activation-links")
    assert r.status_code == 200
    links = r.json()
    assert len(links) >= 1
    assert links[0]["status"] == "active"


# ── POST /admin/batch-activation-links ──


def test_batch_activation_links(db, admin_client):
    """Admin can batch-generate activation links."""
    event, _ = create_test_event(db, name="Batch Evt")
    create_test_user(
        db, username="batch1", event_id=event.id, is_activated=False,
    )
    create_test_user(
        db, username="batch2", event_id=event.id, is_activated=False,
    )

    r = admin_client.post("/api/v1/admin/batch-activation-links", json={
        "event_id": event.id,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["count"] >= 2


def test_batch_activation_links_issuer_scoped(db):
    """Issuer batch-generates links for own event only."""
    event1, _ = create_test_event(db, name="Evt1")
    event2, _ = create_test_event(db, name="Evt2")
    create_test_user(
        db, username="batch_e1", event_id=event1.id, is_activated=False,
    )
    create_test_user(
        db, username="batch_e2", event_id=event2.id, is_activated=False,
    )

    issuer = create_test_user(
        db, username="iss_batch", is_issuer=True, event_id=event1.id,
    )
    client = _make_client(db, issuer)

    r = client.post("/api/v1/admin/batch-activation-links", json={
        "event_id": event2.id,  # tries other event
    })
    assert r.status_code == 200
    data = r.json()
    # Only event1 users should get links
    for link in data["links"]:
        assert link["username"] == "batch_e1"


def test_issuer_batch_honours_exact_explicit_selection(db):
    """Issuer scoping must not expand a selected subset to the entire event."""

    event, _ = create_test_event(db, name="Exact Evt")
    selected = create_test_user(
        db, username="exact_selected", event_id=event.id, is_activated=False,
    )
    unselected = create_test_user(
        db, username="exact_unselected", event_id=event.id, is_activated=False,
    )
    issuer = create_test_user(
        db, username="exact_issuer", is_issuer=True, event_id=event.id,
    )

    result = admin_module.batch_activation_links(
        body=admin_module.BatchActivationLinksIn(user_ids=[selected.id]),
        request=_request(),
        admin=issuer,
        db=db,
    )

    assert result["count"] == 1
    assert result["links"][0]["user_id"] == selected.id
    assert db.query(ActivationLink).filter_by(user_id=unselected.id).count() == 0


def test_manual_batch_reports_ineligible_users_without_erasing_history(db):
    """Manual generation reports exclusions and preserves email delivery records."""

    event, _ = create_test_event(db, name="History Evt")
    admin = create_test_user(
        db, username="history_admin", is_admin=True, event_id=event.id,
    )
    inactive = create_test_user(
        db,
        username="history_inactive",
        event_id=event.id,
        is_activated=False,
    )
    inactive.email = "inactive@example.com"
    inactive.is_active = False
    db.commit()
    delivery = ActivationEmailDelivery(
        user_id=inactive.id,
        requested_by_id=admin.id,
        recipient_email=inactive.email,
        status="failed",
        error_code="recipient_rejected",
    )
    db.add(delivery)
    db.commit()

    result = admin_module.batch_activation_links(
        body=admin_module.BatchActivationLinksIn(user_ids=[inactive.id]),
        request=_request(),
        admin=admin,
        db=db,
    )

    assert result["count"] == 0
    assert result["skipped"][0]["error_code"] == "account_inactive"
    assert db.query(ActivationEmailDelivery).filter_by(user_id=inactive.id).count() == 1
    assert db.query(ActivationLink).filter_by(user_id=inactive.id).count() == 0


# ── DELETE /admin/users/{id}/activation-links/{link_id} ──


def test_invalidate_activation_link(db, admin_client):
    """Admin can invalidate a specific activation link."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(
        db, username="inv_link", event_id=event.id, is_activated=False,
    )
    admin_client.post(f"/api/v1/admin/users/{user.id}/activation-link")

    # Get link ID
    links_r = admin_client.get(f"/api/v1/admin/users/{user.id}/activation-links")
    link_id = links_r.json()[0]["id"]

    r = admin_client.delete(
        f"/api/v1/admin/users/{user.id}/activation-links/{link_id}",
    )
    assert r.status_code == 200

    # Verify it's invalidated
    links_r2 = admin_client.get(f"/api/v1/admin/users/{user.id}/activation-links")
    assert links_r2.json()[0]["status"] == "invalidated"


# ── Activation flow: validate + complete ──


def test_activation_validate_token(db, admin_client):
    """Activation token can be validated via the activation endpoint."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(
        db, username="activate_me", event_id=event.id, is_activated=False,
    )
    r = admin_client.post(f"/api/v1/admin/users/{user.id}/activation-link")
    activation_url = r.json()["activation_url"]
    token = activation_url.split("token=")[1]

    # Validate
    r2 = admin_client.post(
        "/api/v1/activation/validate",
        json={"token": token},
    )
    # Should return user info (200) or the validation response.
    assert r2.status_code == 200


def test_credential_reset_link_requires_recent_reauthentication(db):
    """Creating a new passkey link for an active account is step-up protected."""
    event, _ = create_test_event(db, name="Reset Event")
    target = create_test_user(
        db,
        username="reset.target",
        event_id=event.id,
        is_activated=True,
    )
    admin = create_test_user(
        db,
        username="reset.admin",
        event_id=event.id,
        is_admin=True,
    )

    denied = _make_client(db, admin).post(
        f"/api/v1/admin/users/{target.id}/activation-link",
        json={},
    )
    allowed = _make_client(db, admin, reauth=True).post(
        f"/api/v1/admin/users/{target.id}/activation-link",
        json={},
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
