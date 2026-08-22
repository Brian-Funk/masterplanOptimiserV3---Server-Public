"""Tests for authentication and role-based access control."""
import json

from server_backend.conftest import (
    create_test_event, create_test_user, inject_session, _make_client,
    _raw_client,
)
from app.models.audit import AuditLog


def test_ip_pseudonym_uses_canonical_daily_purpose_specific_hmac(monkeypatch):
    """Equivalent IP forms match, while day and address changes do not."""
    from datetime import datetime, timezone
    import hashlib
    import hmac

    from app.core import sessions

    key = "a-dedicated-test-ip-hmac-key-of-safe-length"
    monkeypatch.setattr(sessions.settings, "IP_HMAC_KEY", key)

    class FirstDay(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 30, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(sessions, "datetime", FirstDay)
    first = sessions._hash_ip("2001:0db8:0:0:0:0:0:1")
    canonical = sessions._hash_ip("2001:db8::1")
    other = sessions._hash_ip("2001:db8::2")

    expected = hmac.new(
        key.encode("utf-8"),
        b"mp-opt-ip-v1\x002026-07-30\x002001:db8::1",
        hashlib.sha256,
    ).hexdigest()[:32]
    assert first == canonical
    assert first == f"v1:{sessions.settings.ip_hmac_key_id}:{expected}"
    assert other != first
    assert sessions._hash_ip("not-an-ip") is None

    class NextDay(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 31, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(sessions, "datetime", NextDay)
    assert sessions._hash_ip("2001:db8::1") != first


def _add_exchange_code(db, user, raw_code: str = "exchange-code-with-enough-entropy"):
    """Persist the digest form of a valid short-lived exchange code."""
    import hashlib
    from datetime import datetime, timedelta, timezone
    from app.models.user import ExchangeCode

    row = ExchangeCode(
        code=hashlib.sha256(raw_code.encode()).hexdigest(),
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    db.add(row)
    db.commit()
    return raw_code, row


# ── /api/v1/auth/me ──


def test_me_returns_401_without_cookie(db):
    """GET /me without session cookie returns 401."""
    client = _raw_client()
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_me_returns_user_with_valid_session(db, root_client):
    """GET /me with valid session returns user data."""
    r = root_client.get("/api/v1/auth/me")
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == "root.admin"
    assert data["is_root_admin"] is True
    assert data["offline_access_ttl_hours"] == 24


def test_recovery_key_access_requires_root_and_recent_passkey(db, admin_client):
    """The recovery generator is root-only and requires fresh WebAuthn."""
    root = create_test_user(
        db,
        username="recovery.root",
        is_root_admin=True,
        is_admin=True,
    )
    root_without_reauth = _make_client(db, root)
    root_with_reauth = _make_client(db, root, reauth=True)

    assert admin_client.get("/api/v1/auth/root-access").status_code == 403
    assert root_without_reauth.get("/api/v1/auth/root-access").status_code == 200
    blocked = root_without_reauth.get("/api/v1/auth/recovery-key-access")
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "Re-authentication required"
    assert root_with_reauth.get("/api/v1/auth/recovery-key-access").status_code == 200


def test_me_returns_configured_offline_access_window(db, root_client):
    """GET /me returns the runtime offline calendar access window."""
    from app.core import runtime_settings

    runtime_settings.set_value("offline_access_ttl_hours", 6, db)

    r = root_client.get("/api/v1/auth/me")

    assert r.status_code == 200
    assert r.json()["offline_access_ttl_hours"] == 6


def test_me_returns_issuer_fields(db):
    """GET /me returns is_issuer field for issuer users."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(
        db, username="iss", display_name="Issuer",
        is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, user)
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 200
    data = r.json()
    assert data["is_issuer"] is True
    assert data["event_id"] == event.id


def test_me_returns_401_with_expired_session(db):
    """GET /me with expired session returns 401."""
    from datetime import datetime, timedelta, timezone
    from app.models.user import AuthSession
    from app.core.sessions import _hash_token
    import secrets

    event, _ = create_test_event(db, name="Exp")
    user = create_test_user(db, username="expired_user", event_id=event.id)

    raw_token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    session = AuthSession(
        user_id=user.id,
        session_token=_hash_token(raw_token),
        csrf_token=csrf,
        expires_at=now - timedelta(hours=1),  # already expired
        last_seen_at=now - timedelta(hours=2),
    )
    db.add(session)
    db.commit()

    client = _raw_client(
        cookies={"session_id": raw_token, "csrf_token": csrf},
    )
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


# ── require_admin ──


def test_admin_endpoint_blocks_regular_user(db):
    """Admin-only endpoint returns 403 for regular users."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(db, username="nonadmin", event_id=event.id)
    client = _make_client(db, user)
    r = client.get("/api/v1/admin/events")
    assert r.status_code == 403


def test_admin_endpoint_allows_admin(db, admin_client):
    """Admin-only endpoint returns 200 for admins."""
    r = admin_client.get("/api/v1/admin/events")
    assert r.status_code == 200


# ── require_admin_or_issuer ──


def test_admin_or_issuer_allows_issuer(db):
    """Endpoints with require_admin_or_issuer allow issuers."""
    event, _ = create_test_event(db, name="Evt")
    issuer = create_test_user(
        db, username="issuer1", display_name="Iss",
        is_issuer=True, event_id=event.id,
    )
    client = _make_client(db, issuer)
    r = client.get("/api/v1/admin/users")
    assert r.status_code == 200


def test_admin_or_issuer_blocks_regular_user(db):
    """Endpoints with require_admin_or_issuer block regular users."""
    event, _ = create_test_event(db, name="Evt")
    user = create_test_user(db, username="regular", event_id=event.id)
    client = _make_client(db, user)
    r = client.get("/api/v1/admin/users")
    assert r.status_code == 403


# ── _is_issuer_only ──


def test_is_issuer_only_true_for_pure_issuer():
    """_is_issuer_only returns True for issuer without admin."""
    from app.core.security import _is_issuer_only
    from unittest.mock import MagicMock
    user = MagicMock()
    user.is_issuer = True
    user.is_admin = False
    user.is_root_admin = False
    assert _is_issuer_only(user) is True


def test_is_issuer_only_false_for_admin_plus_issuer():
    """_is_issuer_only returns False when user is both admin and issuer."""
    from app.core.security import _is_issuer_only
    from unittest.mock import MagicMock
    user = MagicMock()
    user.is_issuer = True
    user.is_admin = True
    user.is_root_admin = False
    assert _is_issuer_only(user) is False


# ── require_same_event ──


def test_require_same_event_blocks_cross_event(db):
    """Issuer cannot access users from a different event."""
    event1, _ = create_test_event(db, name="Evt1")
    event2, _ = create_test_event(db, name="Evt2")
    issuer = create_test_user(
        db, username="iss_e1", display_name="Iss",
        is_issuer=True, event_id=event1.id,
    )
    target = create_test_user(
        db, username="target_e2", display_name="Target",
        event_id=event2.id,
    )
    from app.core.security import require_same_event
    from fastapi import HTTPException
    import pytest as pt
    with pt.raises(HTTPException) as exc_info:
        require_same_event(target, issuer)
    assert exc_info.value.status_code == 404


def test_require_same_event_allows_same_event(db):
    """Issuer can access users from the same event."""
    event, _ = create_test_event(db, name="Evt")
    issuer = create_test_user(
        db, username="iss", is_issuer=True, event_id=event.id,
    )
    target = create_test_user(
        db, username="tgt", event_id=event.id,
    )
    from app.core.security import require_same_event
    # Should not raise
    require_same_event(target, issuer)


def test_require_same_event_blocks_event_admin_cross_event(db):
    """Event administrators are not global administrators."""
    event1, _ = create_test_event(db, name="Evt1")
    event2, _ = create_test_event(db, name="Evt2")
    admin = create_test_user(
        db, username="adm", is_admin=True, event_id=event1.id,
    )
    target = create_test_user(
        db, username="tgt", event_id=event2.id,
    )
    from app.core.security import require_same_event
    from fastapi import HTTPException
    import pytest as pt
    with pt.raises(HTTPException) as exc_info:
        require_same_event(target, admin)
    assert exc_info.value.status_code == 404


def test_require_same_event_allows_root_cross_event(db):
    """Root remains explicitly outside the tenant isolation boundary."""
    event1, _ = create_test_event(db, name="Evt1")
    event2, _ = create_test_event(db, name="Evt2")
    root = create_test_user(
        db, username="root_global", is_root_admin=True,
    )
    target = create_test_user(db, username="tgt_global", event_id=event2.id)
    from app.core.security import require_same_event
    require_same_event(target, root)


# ── require_recent_reauth ──


def test_reauth_required_blocks_without_reauth(db, admin_client):
    """Destructive endpoint requires re-authentication."""
    # admin_client does not have reauth_at set
    # Try to delete an event that requires recent re-authentication.
    r = admin_client.delete("/api/v1/admin/events/9999")
    assert r.status_code == 403
    assert "Re-authentication required" in r.json().get("detail", "")


def test_reauth_required_allows_with_reauth(db, reauth_admin_client):
    """Destructive endpoint succeeds with fresh re-authentication."""
    # Event does not exist, so 404 proves the auth check passed.
    r = reauth_admin_client.delete("/api/v1/admin/events/9999")
    assert r.status_code == 404  # past the auth check


# ── Security settings ──


def test_root_settings_exposes_offline_access_window(db, root_client):
    """GET /admin/settings includes the offline calendar access window."""
    r = root_client.get("/api/v1/admin/settings")

    assert r.status_code == 200
    setting = r.json()["offline_access_ttl_hours"]
    assert setting["value"] == 24
    assert setting["default"] == 24
    assert setting["label"] == "Offline calendar access window"
    assert setting["unit"] == "hours"
    assert setting["min"] == 1
    assert setting["max"] == 24


def test_admin_settings_blocks_non_root_admin(db, admin_client):
    """GET /admin/settings is limited to root admins."""
    r = admin_client.get("/api/v1/admin/settings")

    assert r.status_code == 403
    assert r.json()["detail"] == "Root admin access required"


def test_root_settings_updates_offline_access_window_with_reauth(db):
    """Root admins can update offline access after recent re-authentication."""
    from app.core import runtime_settings

    root = create_test_user(
        db,
        username="settings.root",
        is_root_admin=True,
        is_admin=True,
    )
    client = _make_client(db, root, reauth=True)

    r = client.put(
        "/api/v1/admin/settings",
        json={"settings": {"offline_access_ttl_hours": 6}},
    )

    assert r.status_code == 200
    assert r.json()["updated"] == ["offline_access_ttl_hours"]
    assert runtime_settings.get_int("offline_access_ttl_hours", db) == 6


def test_root_settings_rejects_invalid_offline_access_window(db):
    """Root settings updates reject values outside the configured range."""
    root = create_test_user(
        db,
        username="settings.invalid",
        is_root_admin=True,
        is_admin=True,
    )
    client = _make_client(db, root, reauth=True)

    r = client.put(
        "/api/v1/admin/settings",
        json={"settings": {"offline_access_ttl_hours": 25}},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == []
    assert body["errors"][0]["key"] == "offline_access_ttl_hours"


def test_admin_settings_update_blocks_non_root_admin(db):
    """PUT /admin/settings is limited to root admins."""
    event, _ = create_test_event(db, name="Settings scope")
    admin = create_test_user(
        db, username="settings.admin", is_admin=True, event_id=event.id
    )
    client = _make_client(db, admin, reauth=True)

    r = client.put(
        "/api/v1/admin/settings",
        json={"settings": {"offline_access_ttl_hours": 6}},
    )

    assert r.status_code == 403
    assert r.json()["detail"] == "Root admin access required"


def test_root_configures_desktop_publish_rate_limits(db):
    """Root settings change both desktop push limit providers at runtime."""
    from app.core.rate_limit import runtime_limit

    root = create_test_user(
        db,
        username="settings.publish",
        is_root_admin=True,
        is_admin=True,
    )
    client = _make_client(db, root, reauth=True)

    before = client.get("/api/v1/admin/settings")
    assert before.status_code == 200
    for key in (
        "masterplan_pushes_per_minute",
        "public_schedule_pushes_per_minute",
    ):
        assert before.json()[key]["value"] == 5
        assert before.json()[key]["min"] == 1
        assert before.json()[key]["max"] == 120

    updated = client.put(
        "/api/v1/admin/settings",
        json={
            "settings": {
                "masterplan_pushes_per_minute": 12,
                "public_schedule_pushes_per_minute": 18,
            }
        },
    )

    assert updated.status_code == 200
    assert set(updated.json()["updated"]) == {
        "masterplan_pushes_per_minute",
        "public_schedule_pushes_per_minute",
    }
    assert runtime_limit("masterplan_pushes_per_minute")() == "12/minute"
    assert runtime_limit("public_schedule_pushes_per_minute")() == "18/minute"


def test_root_rejects_desktop_publish_rate_limits_outside_bounds(db):
    """Desktop push limits remain within the documented 1 to 120 range."""
    root = create_test_user(
        db,
        username="settings.publish.invalid",
        is_root_admin=True,
        is_admin=True,
    )
    client = _make_client(db, root, reauth=True)

    response = client.put(
        "/api/v1/admin/settings",
        json={
            "settings": {
                "masterplan_pushes_per_minute": 0,
                "public_schedule_pushes_per_minute": 121,
            }
        },
    )

    assert response.status_code == 200
    assert response.json()["updated"] == []
    assert {error["key"] for error in response.json()["errors"]} == {
        "masterplan_pushes_per_minute",
        "public_schedule_pushes_per_minute",
    }


# Exchange codes


def test_exchange_code_is_hashed_and_single_use(db):
    """A successful exchange creates one session and cannot be replayed."""
    from app.models.user import AuthSession

    event, _ = create_test_event(db, name="Exchange Event")
    user = create_test_user(db, username="exchange.user", event_id=event.id)
    raw_code, row = _add_exchange_code(db, user)
    client = _raw_client()

    first = client.post("/api/v1/auth/exchange", json={"code": raw_code})
    second = client.post("/api/v1/auth/exchange", json={"code": raw_code})

    assert row.code != raw_code
    assert first.status_code == 200
    assert "session_id=" in first.headers.get("set-cookie", "")
    assert second.status_code == 400
    session = db.query(AuthSession).filter(AuthSession.user_id == user.id).one()
    assert session.reauth_at is not None


def test_exchange_stores_only_coarse_user_agent(db):
    """Session metadata must discard versions and device-model detail."""
    from app.models.user import AuthSession

    user = create_test_user(db, username="coarse.device")
    raw_code, _ = _add_exchange_code(db, user)
    client = _raw_client(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/127.0.0.0 Safari/537.36"
            )
        }
    )

    response = client.post("/api/v1/auth/exchange", json={"code": raw_code})

    assert response.status_code == 200
    session = db.query(AuthSession).filter(AuthSession.user_id == user.id).one()
    assert session.user_agent == "Chrome on Windows"
    assert "127" not in session.user_agent


def test_exchange_rejects_inactive_account(db):
    """A valid one-time code cannot create a session for an inactive account."""
    from app.models.user import AuthSession

    user = create_test_user(db, username="exchange.inactive")
    user.is_active = False
    db.commit()
    raw_code, _ = _add_exchange_code(db, user)

    response = _raw_client().post(
        "/api/v1/auth/exchange",
        json={"code": raw_code},
    )

    assert response.status_code == 400
    assert db.query(AuthSession).filter(AuthSession.user_id == user.id).count() == 0


def test_issuer_receives_privileged_session_lifetime(db):
    """Issuer sessions use the short privileged session policy."""
    from datetime import datetime, timezone
    from app.core import runtime_settings
    from app.models.user import AuthSession

    runtime_settings.set_value("session_ttl_hours", 8, db)
    runtime_settings.set_value("session_ttl_hours_admin", 1, db)
    event, _ = create_test_event(db, name="Issuer Session")
    issuer = create_test_user(
        db,
        username="exchange.issuer",
        event_id=event.id,
        is_issuer=True,
    )
    raw_code, _ = _add_exchange_code(db, issuer)

    response = _raw_client().post(
        "/api/v1/auth/exchange",
        json={"code": raw_code},
    )

    assert response.status_code == 200
    session = db.query(AuthSession).filter(AuthSession.user_id == issuer.id).one()
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    remaining_seconds = (expires_at - datetime.now(timezone.utc)).total_seconds()
    assert 0 < remaining_seconds <= 3600


# Logout


def test_logout_clears_session(db, admin_client):
    """POST /logout clears session."""
    r = admin_client.post("/api/v1/auth/logout")
    assert r.status_code == 200
    # Subsequent /me should fail
    r2 = admin_client.get("/api/v1/auth/me")
    assert r2.status_code == 401


def test_logout_rejects_missing_csrf_token(db):
    """A cross-site logout request cannot revoke a browser session."""
    event, _ = create_test_event(db, name="Logout Event")
    user = create_test_user(db, username="logout.user", is_admin=True, event_id=event.id)
    raw_token, csrf_token = inject_session(db, user)
    client = _raw_client(cookies={"session_id": raw_token, "csrf_token": csrf_token})

    r = client.post("/api/v1/auth/logout")

    assert r.status_code == 403
    assert r.json()["detail"] == "CSRF token missing or invalid"
    assert client.get("/api/v1/auth/me").status_code == 200


def test_logout_audits_user_before_revoking_session(db):
    """Logout audit keeps the user id even though the session is revoked."""
    event, _ = create_test_event(db, name="Audit Event")
    user = create_test_user(db, username="audit.logout", is_admin=True, event_id=event.id)
    raw_token, csrf_token = inject_session(db, user)
    client = _raw_client(
        cookies={"session_id": raw_token, "csrf_token": csrf_token},
        headers={
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf_token,
        },
    )

    r = client.post("/api/v1/auth/logout", json={})

    assert r.status_code == 200
    entry = db.query(AuditLog).filter(AuditLog.action == "auth.logout").one()
    assert entry.user_id == user.id
    assert entry.username is None
    assert entry.actor_ref == user.evidence_subject_id


def test_user_can_list_and_revoke_only_owned_active_sessions(db):
    from app.models.user import AuthSession

    event, _ = create_test_event(db, name="Session scope")
    user = create_test_user(db, username="sessions.owner", event_id=event.id)
    client = _make_client(db, user)
    current = (
        db.query(AuthSession)
        .filter(AuthSession.user_id == user.id)
        .order_by(AuthSession.id.desc())
        .first()
    )
    current.user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/127.0.0.0"
    )
    inject_session(db, user)
    other = (
        db.query(AuthSession)
        .filter(AuthSession.user_id == user.id, AuthSession.id != current.id)
        .one()
    )
    other.user_agent = "Firefox on Linux"
    outsider = create_test_user(db, username="sessions.outsider", event_id=event.id)
    inject_session(db, outsider)
    outsider_session = (
        db.query(AuthSession).filter(AuthSession.user_id == outsider.id).one()
    )
    db.commit()

    listed = client.get("/api/v1/auth/sessions")

    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()} == {current.id, other.id}
    assert {item["device"] for item in listed.json()} == {
        "Chrome on Windows",
        "Firefox on Linux",
    }
    assert sum(item["current"] for item in listed.json()) == 1
    assert all("ip" not in item for item in listed.json())

    forbidden = client.delete(f"/api/v1/auth/sessions/{outsider_session.id}")
    revoked = client.delete(f"/api/v1/auth/sessions/{other.id}")

    assert forbidden.status_code == 404
    assert revoked.status_code == 200
    assert revoked.json() == {"revoked": True, "current": False}
    db.refresh(other)
    assert other.revoked_at is not None
    assert client.get("/api/v1/auth/me").status_code == 200
    entry = (
        db.query(AuditLog)
        .filter(AuditLog.action == "auth.session_revoke")
        .one()
    )
    assert json.loads(entry.detail) == {"schema_version": 1, "target": "other"}
    assert entry.resource_id is None


def test_revoking_current_session_clears_cookies_and_authentication(db):
    from app.models.user import AuthSession

    event, _ = create_test_event(db, name="Current session scope")
    user = create_test_user(db, username="sessions.current", event_id=event.id)
    client = _make_client(db, user)
    current = db.query(AuthSession).filter(AuthSession.user_id == user.id).one()

    response = client.delete(f"/api/v1/auth/sessions/{current.id}")

    assert response.status_code == 200
    assert response.json() == {"revoked": True, "current": True}
    assert "session_id=" in response.headers.get("set-cookie", "")
    assert client.get("/api/v1/auth/me").status_code == 401
