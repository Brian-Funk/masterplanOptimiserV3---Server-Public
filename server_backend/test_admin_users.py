"""Tests for admin user management endpoints."""
from server_backend.conftest import (
    create_test_event, create_test_user, inject_session, _make_client,
)
from app.models.published import PublishedPerson
from app.models.user import User


# ── POST /admin/users (create user) ──


def test_create_user_happy_path(db, admin_client):
    """Admin can create a user with activation URL returned."""
    event, _ = create_test_event(db, name="Evt")
    r = admin_client.post("/api/v1/admin/users", json={
        "username": "new.user",
        "display_name": "New User",
        "email": "new@test.com",
        "event_id": event.id,
        "can_edit": True,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["user"]["username"] == "new.user"
    assert data["user"]["is_activated"] is False
    assert "/activate#token=" in data["activation_url"]


def test_create_user_duplicate_username(db, admin_client):
    """Creating a user with an existing username → 409."""
    event, _ = create_test_event(db, name="Evt")
    create_test_user(db, username="duplicate", event_id=event.id)
    r = admin_client.post("/api/v1/admin/users", json={
        "username": "duplicate",
        "display_name": "Dup",
        "event_id": event.id,
    })
    assert r.status_code == 409


def test_create_user_missing_event_id(db, admin_client):
    """Creating a user without event_id → 422."""
    r = admin_client.post("/api/v1/admin/users", json={
        "username": "noevt",
        "display_name": "No Event",
    })
    assert r.status_code == 422


def test_create_user_issuer_forces_own_event(db):
    """Issuer creating a user → event_id forced to issuer's event."""
    event, _ = create_test_event(db, name="IssuerEvt")
    other_event, _ = create_test_event(db, name="OtherEvt")
    issuer = create_test_user(
        db, username="iss_creator", is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer)

    r = client.post("/api/v1/admin/users", json={
        "username": "created_by_issuer",
        "display_name": "User By Issuer",
        "event_id": other_event.id,  # tries to set different event
    })
    assert r.status_code == 200
    data = r.json()
    # event_id forced to issuer's event
    assert data["user"]["event_id"] == event.id


def test_create_user_issuer_can_omit_event_id(db):
    """Issuer user creation may omit event_id because it is server-scoped."""
    event, _ = create_test_event(db, name="IssuerEvt")
    issuer = create_test_user(
        db, username="iss_creator_no_event", is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer)

    r = client.post("/api/v1/admin/users", json={
        "username": "created_without_event",
        "display_name": "User Without Event",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["user"]["event_id"] == event.id


def test_create_user_issuer_cannot_grant_admin(db):
    """Issuer cannot set is_admin on new users."""
    event, _ = create_test_event(db, name="Evt")
    issuer = create_test_user(
        db, username="iss_priv", is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer)

    r = client.post("/api/v1/admin/users", json={
        "username": "escalated",
        "display_name": "Escalated",
        "event_id": event.id,
        "is_admin": True,
    })
    assert r.status_code == 403


def test_create_user_issuer_cannot_grant_issuer(db):
    """Issuer cannot set is_issuer on new users."""
    event, _ = create_test_event(db, name="Evt")
    issuer = create_test_user(
        db, username="iss_priv2", is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer)

    r = client.post("/api/v1/admin/users", json={
        "username": "escalated2",
        "display_name": "Escalated2",
        "event_id": event.id,
        "is_issuer": True,
    })
    assert r.status_code == 403


def test_bulk_create_users_partial_success_and_tags(db, admin_client):
    """Bulk creation creates valid rows and reports invalid rows."""
    event, _ = create_test_event(db, name="BulkEvt")
    create_test_user(db, username="taken.user", event_id=event.id)

    r = admin_client.post("/api/v1/admin/users/bulk", json={
        "event_id": event.id,
        "bulk_tags": ["board", "event"],
        "users": [
            {
                "username": "alpha.tester",
                "display_name": "Alpha Tester",
                "email": "alpha@example.com",
                "can_edit": True,
                "tags": ["speaker"],
            },
            {
                "username": "taken.user",
                "display_name": "Taken User",
            },
            {
                "username": "alpha.tester",
                "display_name": "Duplicate Batch User",
            },
        ],
    })
    assert r.status_code == 200
    data = r.json()
    assert [u["username"] for u in data["created"]] == ["alpha.tester"]
    assert data["created"][0]["event_id"] == event.id
    assert data["created"][0]["can_edit"] is True
    assert data["created"][0]["tags"] == ["board", "event", "speaker"]
    assert {error["index"] for error in data["errors"]} == {1, 2}


def test_bulk_create_users_rejects_invalid_email(db, admin_client):
    """Malformed bulk email input is rejected before any row is created."""

    event, _ = create_test_event(db, name="BulkInvalidEmailEvt")

    response = admin_client.post("/api/v1/admin/users/bulk", json={
        "event_id": event.id,
        "users": [
            {
                "username": "invalid.email",
                "display_name": "Invalid Email",
                "email": "not-an-email",
            },
        ],
    })

    assert response.status_code == 422
    assert db.query(User).filter_by(username="invalid.email").first() is None


def test_bulk_create_users_issuer_forces_own_event(db):
    """Issuer bulk creation ignores requested event and uses own event."""
    event, _ = create_test_event(db, name="IssuerBulkEvt")
    other_event, _ = create_test_event(db, name="OtherBulkEvt")
    issuer = create_test_user(
        db, username="iss_bulk_creator", is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer)

    r = client.post("/api/v1/admin/users/bulk", json={
        "event_id": other_event.id,
        "bulk_tags": ["issuer"],
        "users": [
            {"username": "issuer.bulk.one", "display_name": "Issuer Bulk One"},
            {"username": "issuer.bulk.two", "display_name": "Issuer Bulk Two"},
        ],
    })
    assert r.status_code == 200
    data = r.json()
    assert len(data["created"]) == 2
    assert {u["event_id"] for u in data["created"]} == {event.id}
    assert all(not u["is_admin"] and not u["is_issuer"] for u in data["created"])
    assert data["created"][0]["tags"] == ["issuer"]


def test_only_root_can_set_issuer(db, admin_client):
    """Non-root admin cannot grant issuer role."""
    event, _ = create_test_event(db, name="Evt")
    r = admin_client.post("/api/v1/admin/users", json={
        "username": "want_issuer",
        "display_name": "Want Issuer",
        "event_id": event.id,
        "is_issuer": True,
    })
    assert r.status_code == 403


def test_root_role_grant_requires_reauthentication(db, root_client):
    """A root session without step-up authentication cannot grant roles."""
    event, _ = create_test_event(db, name="Evt")
    r = root_client.post("/api/v1/admin/users", json={
        "username": "new_issuer",
        "display_name": "New Issuer",
        "event_id": event.id,
        "is_issuer": True,
    })
    assert r.status_code == 403


def test_reauthenticated_root_can_set_issuer(db):
    """A recently re-authenticated root can grant issuer role."""
    event, _ = create_test_event(db, name="Issuer Grant Event")
    root = create_test_user(
        db,
        username="roles.root",
        is_root_admin=True,
        is_admin=True,
    )
    client = _make_client(db, root, reauth=True)

    response = client.post("/api/v1/admin/users", json={
        "username": "new_issuer",
        "display_name": "New Issuer",
        "event_id": event.id,
        "is_issuer": True,
    })

    assert response.status_code == 200
    assert response.json()["user"]["is_issuer"] is True


def test_root_can_create_an_unassigned_ordinary_user(db, root_client):
    response = root_client.post("/api/v1/admin/users", json={
        "username": "awaiting.assignment",
        "display_name": "Awaiting Assignment",
        "event_id": None,
    })

    assert response.status_code == 200
    assert response.json()["user"]["event_id"] is None


def test_non_root_admin_cannot_create_an_unassigned_user(db, admin_client):
    response = admin_client.post("/api/v1/admin/users", json={
        "username": "unscoped.account",
        "display_name": "Unscoped Account",
        "event_id": None,
    })

    assert response.status_code == 422


def test_reauthenticated_root_can_unassign_an_ordinary_user(db):
    event, _ = create_test_event(db, name="Assignment Event")
    root = create_test_user(db, username="assignment.root", is_root_admin=True, is_admin=True)
    user = create_test_user(db, username="assigned.user", event_id=event.id)
    client = _make_client(db, root, reauth=True)

    response = client.put(f"/api/v1/admin/users/{user.id}", json={"event_id": None})

    assert response.status_code == 200
    assert response.json()["event_id"] is None


def test_unassigning_a_user_requires_root_reauthentication(db, root_client):
    event, _ = create_test_event(db, name="Protected Assignment Event")
    user = create_test_user(db, username="protected.assignment", event_id=event.id)

    response = root_client.put(f"/api/v1/admin/users/{user.id}", json={"event_id": None})

    assert response.status_code == 403


# ── GET /admin/users ──


def test_list_users_admin_sees_all(db, root_client):
    """Admin can see all non-root users."""
    event, _ = create_test_event(db, name="Evt")
    create_test_user(db, username="u1", event_id=event.id)
    create_test_user(db, username="u2", event_id=event.id)

    r = root_client.get("/api/v1/admin/users")
    assert r.status_code == 200
    usernames = [u["username"] for u in r.json()]
    assert "u1" in usernames
    assert "u2" in usernames


def test_list_users_issuer_sees_own_event_only(db):
    """Issuer only sees users from their own event."""
    event1, _ = create_test_event(db, name="Evt1")
    event2, _ = create_test_event(db, name="Evt2")
    create_test_user(db, username="u_e1", event_id=event1.id)
    create_test_user(db, username="u_e2", event_id=event2.id)

    issuer = create_test_user(
        db, username="iss_list", is_issuer=True, event_id=event1.id,
    )
    client = _make_client(db, issuer)

    r = client.get("/api/v1/admin/users")
    assert r.status_code == 200
    usernames = [u["username"] for u in r.json()]
    assert "u_e1" in usernames
    assert "u_e2" not in usernames


# ── PUT /admin/users/{id} ──


def test_update_user_fields(db, admin_client):
    """Admin can update user fields."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(db, username="updatable", event_id=event.id)

    r = admin_client.put(f"/api/v1/admin/users/{user.id}", json={
        "display_name": "Updated Name",
        "can_edit": True,
        "tags": ["tag1", "tag2"],
    })
    assert r.status_code == 200
    data = r.json()
    assert data["display_name"] == "Updated Name"
    assert data["can_edit"] is True
    assert data["tags"] == ["tag1", "tag2"]


def test_update_user_issuer_blocked_from_privilege_escalation(db):
    """Issuer cannot change is_admin or is_issuer on users."""
    event, _ = create_test_event(db, name="Evt")
    target = create_test_user(db, username="target_esc", event_id=event.id)
    issuer = create_test_user(
        db, username="iss_esc", is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer)

    r = client.put(f"/api/v1/admin/users/{target.id}", json={
        "is_admin": True,
    })
    assert r.status_code == 403

    r2 = client.put(f"/api/v1/admin/users/{target.id}", json={
        "is_issuer": True,
    })
    assert r2.status_code == 403


def test_update_user_issuer_blocked_event_reassignment(db):
    """Issuer cannot reassign user to another event."""
    event1, _ = create_test_event(db, name="Evt1")
    event2, _ = create_test_event(db, name="Evt2")
    target = create_test_user(db, username="target_ev", event_id=event1.id)
    issuer = create_test_user(
        db, username="iss_ev", is_issuer=True, event_id=event1.id,
    )
    client = _make_client(db, issuer)

    r = client.put(f"/api/v1/admin/users/{target.id}", json={
        "event_id": event2.id,
    })
    assert r.status_code == 403


def test_update_user_cannot_modify_root(db, admin_client):
    """Cannot modify root admin user."""
    root = create_test_user(
        db, username="root.protected", is_root_admin=True, is_admin=True,
    )
    r = admin_client.put(f"/api/v1/admin/users/{root.id}", json={
        "display_name": "Hacked",
    })
    assert r.status_code == 403


def test_update_issuer_cross_event_blocked(db):
    """Issuer cannot update user from a different event."""
    event1, _ = create_test_event(db, name="Evt1")
    event2, _ = create_test_event(db, name="Evt2")
    target = create_test_user(db, username="other_evt", event_id=event2.id)
    issuer = create_test_user(
        db, username="iss_cross", is_issuer=True, event_id=event1.id,
    )
    client = _make_client(db, issuer)

    r = client.put(f"/api/v1/admin/users/{target.id}", json={
        "display_name": "Should Fail",
    })
    assert r.status_code == 403


def test_non_root_admin_cannot_modify_privileged_account(db):
    """Changing status cannot bypass root-only role management."""
    event, _ = create_test_event(db, name="Privilege Event")
    target = create_test_user(
        db,
        username="privileged.target",
        event_id=event.id,
        is_admin=True,
    )
    actor = create_test_user(
        db,
        username="privileged.actor",
        event_id=event.id,
        is_admin=True,
    )

    response = _make_client(db, actor).put(
        f"/api/v1/admin/users/{target.id}",
        json={"is_active": False},
    )

    assert response.status_code == 403
    db.refresh(target)
    assert target.is_active is True


def test_deactivating_user_requires_recent_reauthentication(db):
    """Account deactivation is denied until the admin steps up with a passkey."""
    event, _ = create_test_event(db, name="Deactivate Event")
    actor = create_test_user(db, username="deactivate.admin", is_admin=True)
    target = create_test_user(db, username="deactivate.target", event_id=event.id)

    denied = _make_client(db, actor).put(
        f"/api/v1/admin/users/{target.id}",
        json={"is_active": False},
    )
    allowed = _make_client(db, actor, reauth=True).put(
        f"/api/v1/admin/users/{target.id}",
        json={"is_active": False},
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["is_active"] is False


def test_update_user_rejects_person_from_another_event(db):
    """The generic update route cannot link a user to another event's person."""
    own_event, _ = create_test_event(db, name="Own Person Event")
    other_event, _ = create_test_event(db, name="Other Person Event")
    target = create_test_user(db, username="person.target", event_id=own_event.id)
    actor = create_test_user(db, username="person.admin", is_admin=True)
    db.add(PublishedPerson(
        event_id=other_event.id,
        external_person_id=91,
        first_name="Other",
        last_name="Person",
    ))
    db.commit()

    response = _make_client(db, actor).put(
        f"/api/v1/admin/users/{target.id}",
        json={"linked_person_id": 91},
    )

    assert response.status_code == 404
    db.refresh(target)
    assert target.linked_person_id is None


def test_event_reassignment_clears_stale_person_link(db):
    """Moving a user to another event cannot retain the old person identity."""
    first_event, _ = create_test_event(db, name="First Link Event")
    second_event, _ = create_test_event(db, name="Second Link Event")
    actor = create_test_user(db, username="move.admin", is_admin=True)
    target = create_test_user(db, username="move.target", event_id=first_event.id)
    target.linked_person_id = 42
    db.commit()

    response = _make_client(db, actor).put(
        f"/api/v1/admin/users/{target.id}",
        json={"event_id": second_event.id},
    )

    assert response.status_code == 200
    assert response.json()["event_id"] == second_event.id
    assert response.json()["linked_person_id"] is None


def test_issuer_cannot_delete_another_issuer(db):
    """Issuer-scoped deletion cannot remove a peer privileged account."""
    event, _ = create_test_event(db, name="Issuer Boundary")
    actor = create_test_user(
        db,
        username="issuer.actor",
        event_id=event.id,
        is_issuer=True,
    )
    target = create_test_user(
        db,
        username="issuer.target",
        event_id=event.id,
        is_issuer=True,
    )

    response = _make_client(db, actor, reauth=True).delete(
        f"/api/v1/admin/users/{target.id}",
    )

    assert response.status_code == 403


# ── DELETE /admin/users/{id} ──


def test_delete_user_requires_reauth(db, admin_client):
    """Deleting a user requires re-authentication."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(db, username="to_delete", event_id=event.id)
    r = admin_client.delete(f"/api/v1/admin/users/{user.id}")
    assert r.status_code == 403
    assert "Re-authentication required" in r.json().get("detail", "")


def test_delete_used_user_requires_signed_workflow(db, reauth_admin_client):
    """Re-authentication cannot bypass the evidence workflow for used accounts."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(db, username="to_delete2", event_id=event.id)
    r = reauth_admin_client.delete(f"/api/v1/admin/users/{user.id}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SIGNED_DELETION_REQUIRED"


def test_delete_unused_invitation_with_reauth(db, reauth_admin_client):
    """A never-activated, unlinked invitation can be removed directly."""
    event, _ = create_test_event(db, name="Unused Invitation Event")
    user = create_test_user(
        db,
        username="unused.invitation",
        event_id=event.id,
        is_activated=False,
    )

    r = reauth_admin_client.delete(f"/api/v1/admin/users/{user.id}")

    assert r.status_code == 200
    assert r.json()["removal_mode"] == "direct_unused_invitation"


def test_delete_root_admin_blocked(db, reauth_admin_client):
    """Cannot delete root admin."""
    from app.models.user import User
    root = db.query(User).filter(User.username == "root.admin").first()
    if not root:
        root = create_test_user(
            db, username="root.admin", is_root_admin=True, is_admin=True,
        )
    r = reauth_admin_client.delete(f"/api/v1/admin/users/{root.id}")
    assert r.status_code == 403



def test_list_users_includes_activation_campaign_metadata(db, root_client):
    """The Users endpoint exposes safe activation campaign metadata."""
    event, _ = create_test_event(db, name="Activation Metadata")
    user = create_test_user(
        db,
        username="activation.pending",
        event_id=event.id,
        is_activated=False,
    )

    link = root_client.post(f"/api/v1/admin/users/{user.id}/activation-link")
    assert link.status_code == 200
    r = root_client.get("/api/v1/admin/users")

    assert r.status_code == 200
    data = next(item for item in r.json() if item["id"] == user.id)
    assert data["has_activation_link"] is True
    assert data["last_activation_link_created_at"] is not None
    assert data["last_activation_at"] is None


def test_list_users_reports_last_activation_from_used_link(db, root_client):
    """Used activation links expose last activation time without exposing tokens."""
    from datetime import datetime, timezone
    from app.models.user import ActivationLink

    event, _ = create_test_event(db, name="Used Link")
    user = create_test_user(
        db,
        username="activation.used",
        event_id=event.id,
        is_activated=True,
    )
    db.add(
        ActivationLink(
            token_hash="x" * 64,
            user_id=user.id,
            purpose="initial_setup",
            expires_at=datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc),
            used_at=datetime(2026, 5, 21, 14, 35, tzinfo=timezone.utc),
            created_by_id=user.id,
        )
    )
    db.commit()

    r = root_client.get("/api/v1/admin/users")

    assert r.status_code == 200
    data = next(item for item in r.json() if item["id"] == user.id)
    assert data["has_activation_link"] is False
    assert data["last_activation_at"] is not None
    assert "token" not in data
