"""Tests for configurable, flow-scoped passkey rate limiting."""
from starlette.requests import Request

from app.core import runtime_settings
from app.core.activation import create_activation_link
from app.core.rate_limit import (
    PASSKEY_COARSE_IP_LIMIT,
    passkey_registration_rate_key,
    passkey_session_rate_key,
    runtime_limit,
)
from server_backend.conftest import _make_client, _raw_client, create_test_user


def _request(
    *,
    activation_token: str | None = None,
    session_token: str | None = None,
) -> Request:
    """Build a request containing the credentials used by limiter key helpers."""
    headers: list[tuple[bytes, bytes]] = []
    if activation_token:
        headers.append((b"x-activation-token", activation_token.encode()))
    if session_token:
        headers.append((b"cookie", f"session_id={session_token}".encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": headers,
            "client": ("203.0.113.10", 12345),
            "server": ("localhost", 443),
            "scheme": "https",
        }
    )


def test_passkey_rate_keys_hash_secrets_and_separate_flows():
    """Limiter keys never expose raw activation or session credentials."""
    activation_a = "activation-secret-a"
    activation_b = "activation-secret-b"
    session = "session-secret"

    activation_key_a = passkey_registration_rate_key(
        _request(activation_token=activation_a),
    )
    activation_key_b = passkey_registration_rate_key(
        _request(activation_token=activation_b),
    )
    session_key = passkey_session_rate_key(_request(session_token=session))

    assert activation_key_a != activation_key_b
    assert activation_key_a.startswith("passkey-activation:")
    assert session_key.startswith("passkey-session:")
    assert activation_a not in activation_key_a
    assert session not in session_key
    assert PASSKEY_COARSE_IP_LIMIT == "300/minute"


def test_root_configures_passkey_rate_limit(db):
    """Root settings expose and update the passkey request limit."""
    root = create_test_user(
        db,
        username="settings.passkeys",
        is_root_admin=True,
        is_admin=True,
    )
    client = _make_client(db, root, reauth=True)

    before = client.get("/api/v1/admin/settings")
    assert before.status_code == 200
    setting = before.json()["passkey_requests_per_minute"]
    assert setting == {
        "value": 60,
        "default": 60,
        "label": "Passkey requests per minute",
        "unit": "requests/minute",
        "min": 5,
        "max": 600,
    }

    updated = client.put(
        "/api/v1/admin/settings",
        json={"settings": {"passkey_requests_per_minute": 90}},
    )

    assert updated.status_code == 200
    assert updated.json()["updated"] == ["passkey_requests_per_minute"]
    assert runtime_limit("passkey_requests_per_minute")() == "90/minute"


def test_root_rejects_passkey_rate_limit_outside_bounds(db):
    """Passkey throughput remains within the documented 5 to 600 range."""
    root = create_test_user(
        db,
        username="settings.passkeys.invalid",
        is_root_admin=True,
        is_admin=True,
    )
    client = _make_client(db, root, reauth=True)

    below = client.put(
        "/api/v1/admin/settings",
        json={"settings": {"passkey_requests_per_minute": 4}},
    )
    above = client.put(
        "/api/v1/admin/settings",
        json={"settings": {"passkey_requests_per_minute": 601}},
    )

    assert below.status_code == 200
    assert below.json()["updated"] == []
    assert below.json()["errors"][0]["key"] == "passkey_requests_per_minute"
    assert above.status_code == 200
    assert above.json()["updated"] == []
    assert above.json()["errors"][0]["key"] == "passkey_requests_per_minute"


def test_public_authentication_uses_configurable_ip_limit(db):
    """Public authentication remains bounded by the configured client quota."""
    runtime_settings.set_value("passkey_requests_per_minute", 5, db)
    client = _raw_client()

    responses = [
        client.post("/api/v1/passkey/auth/begin")
        for _ in range(6)
    ]

    assert [response.status_code for response in responses[:5]] == [200] * 5
    assert responses[5].status_code == 429
    assert "rate limit" in responses[5].json()["error"].lower()


def test_activation_registrations_on_one_ip_have_independent_limits(db):
    """Separate activation links do not consume each other's request quota."""
    runtime_settings.set_value("passkey_requests_per_minute", 5, db)
    issuer = create_test_user(db, username="limit.issuer", is_admin=True)
    user_a = create_test_user(
        db,
        username="limit.activation.a",
        is_activated=False,
    )
    user_b = create_test_user(
        db,
        username="limit.activation.b",
        is_activated=False,
    )
    token_a, _ = create_activation_link(user_a.id, issuer.id, db)
    token_b, _ = create_activation_link(user_b.id, issuer.id, db)
    db.commit()
    client = _raw_client()

    for _ in range(5):
        assert client.post(
            "/api/v1/passkey/register/begin",
            headers={"X-Activation-Token": token_a},
        ).status_code == 200
        assert client.post(
            "/api/v1/passkey/register/begin",
            headers={"X-Activation-Token": token_b},
        ).status_code == 200

    limited = client.post(
        "/api/v1/passkey/register/begin",
        headers={"X-Activation-Token": token_a},
    )
    assert limited.status_code == 429


def test_reauthentication_sessions_on_one_ip_have_independent_limits(db):
    """Separate admin sessions do not consume each other's re-auth quota."""
    runtime_settings.set_value("passkey_requests_per_minute", 5, db)
    user_a = create_test_user(db, username="limit.session.a", is_admin=True)
    user_b = create_test_user(db, username="limit.session.b", is_admin=True)
    client_a = _make_client(db, user_a, reauth=True)
    client_b = _make_client(db, user_b, reauth=True)

    for _ in range(5):
        assert client_a.post(
            "/api/v1/admin/reauth/begin",
            json={},
        ).status_code == 200
        assert client_b.post(
            "/api/v1/admin/reauth/begin",
            json={},
        ).status_code == 200

    limited = client_a.post("/api/v1/admin/reauth/begin", json={})
    assert limited.status_code == 429
