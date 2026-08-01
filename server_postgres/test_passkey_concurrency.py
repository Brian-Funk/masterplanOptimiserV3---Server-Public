"""PostgreSQL integration tests for simultaneous passkey ceremonies."""
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

import app.api.v1.passkey as passkey_api
from app.core.activation import create_activation_link
from app.core.sessions import _hash_token
from app.main import app
from app.models.user import AuthSession, User, WebAuthnCredential


def _client(
    *,
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> TestClient:
    """Create an isolated HTTP client backed by production DB dependencies."""
    client = TestClient(app, base_url="https://localhost")
    if cookies:
        client.cookies.update(cookies)
    if headers:
        client.headers.update(headers)
    return client


def _run_together(*calls: Callable[[], object]) -> list[object]:
    """Release all callables together and return results in input order."""
    barrier = threading.Barrier(len(calls))

    def run(call: Callable[[], object]) -> object:
        barrier.wait(timeout=10)
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        futures = [executor.submit(run, call) for call in calls]
        return [future.result(timeout=20) for future in futures]


def _user(
    db: Session,
    username: str,
    *,
    activated: bool = True,
    admin: bool = False,
) -> User:
    """Create an account used by a concurrency scenario."""
    user = User(
        username=username,
        display_name=username,
        is_active=True,
        is_activated=activated,
        is_admin=admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _credential(db: Session, user: User, credential_id: bytes) -> None:
    """Store a discoverable credential for authentication tests."""
    db.add(
        WebAuthnCredential(
            user_id=user.id,
            credential_id=credential_id,
            public_key=b"public-key",
            sign_count=0,
            friendly_name="Passkey",
        )
    )
    db.commit()


def _auth_body(credential_id: bytes, user_id: int, ceremony_id: str) -> dict:
    """Build the minimal discoverable-credential response used by mocks."""
    encoded_id = bytes_to_base64url(credential_id)
    return {
        "ceremony_id": ceremony_id,
        "credential": {
            "id": encoded_id,
            "rawId": encoded_id,
            "type": "public-key",
            "response": {"userHandle": bytes_to_base64url(str(user_id).encode())},
        },
    }


def _registration_body(credential_id: bytes, ceremony_id: str) -> dict:
    """Build the minimal registration response used by mocks."""
    encoded_id = bytes_to_base64url(credential_id)
    return {
        "ceremony_id": ceremony_id,
        "credential": {
            "id": encoded_id,
            "rawId": encoded_id,
            "type": "public-key",
            "response": {},
        },
    }


def _mock_authentication(monkeypatch, *, synchronise: bool = False) -> None:
    """Replace cryptographic verification while retaining transaction logic."""
    barrier = threading.Barrier(2) if synchronise else None

    def verify(**kwargs):
        if barrier is not None:
            barrier.wait(timeout=10)
        return SimpleNamespace(
            new_sign_count=kwargs["credential_current_sign_count"] + 1,
        )

    monkeypatch.setattr(passkey_api, "verify_authentication_response", verify)


def _mock_registration(
    monkeypatch,
    credential_id: bytes | None = None,
    *,
    synchronise: bool = False,
) -> None:
    """Replace cryptographic verification while retaining persistence logic."""
    barrier = threading.Barrier(2) if synchronise else None

    def verify(**kwargs):
        if barrier is not None:
            barrier.wait(timeout=10)
        response_id = base64url_to_bytes(kwargs["credential"]["rawId"])
        resolved_id = credential_id or response_id
        return SimpleNamespace(
            credential_id=resolved_id,
            credential_public_key=b"public-key-" + resolved_id,
            sign_count=0,
            aaguid=None,
        )

    monkeypatch.setattr(passkey_api, "verify_registration_response", verify)


def _activation_begin(token: str) -> str:
    """Start an activation registration and return its ceremony ID."""
    response = _client().post(
        "/api/v1/passkey/register/begin",
        headers={"X-Activation-Token": token},
    )
    assert response.status_code == 200, response.text
    return response.json()["ceremony_id"]


def _session(db: Session, user: User) -> tuple[str, str]:
    """Create a recently re-authenticated session and return raw credentials."""
    raw_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    db.add(
        AuthSession(
            user_id=user.id,
            session_token=_hash_token(raw_token),
            csrf_token=csrf_token,
            expires_at=now + timedelta(hours=1),
            last_seen_at=now,
            reauth_at=now,
        )
    )
    db.commit()
    return raw_token, csrf_token


def _session_client(raw_token: str, csrf_token: str) -> TestClient:
    """Create a client authenticated with the supplied account session."""
    return _client(
        cookies={"session_id": raw_token, "csrf_token": csrf_token},
        headers={
            "X-CSRF-Token": csrf_token,
            "Content-Type": "application/json",
        },
    )


def test_different_users_authenticate_simultaneously(db, monkeypatch):
    """Independent authentication ceremonies both issue usable exchange codes."""
    user_a = _user(db, "parallel.auth.a")
    user_b = _user(db, "parallel.auth.b")
    credential_a = b"parallel-auth-a"
    credential_b = b"parallel-auth-b"
    _credential(db, user_a, credential_a)
    _credential(db, user_b, credential_b)
    _mock_authentication(monkeypatch, synchronise=True)

    begin_response_a, begin_response_b = _run_together(
        lambda: _client().post("/api/v1/passkey/auth/begin"),
        lambda: _client().post("/api/v1/passkey/auth/begin"),
    )
    assert begin_response_a.status_code == 200, begin_response_a.text
    assert begin_response_b.status_code == 200, begin_response_b.text
    begin_a = begin_response_a.json()
    begin_b = begin_response_b.json()
    response_a, response_b = _run_together(
        lambda: _client().post(
            "/api/v1/passkey/auth/complete",
            json=_auth_body(credential_a, user_a.id, begin_a["ceremony_id"]),
        ),
        lambda: _client().post(
            "/api/v1/passkey/auth/complete",
            json=_auth_body(credential_b, user_b.id, begin_b["ceremony_id"]),
        ),
    )

    assert response_a.status_code == 200, response_a.text
    assert response_b.status_code == 200, response_b.text
    code_a = response_a.json()["exchange_code"]
    code_b = response_b.json()["exchange_code"]
    assert code_a != code_b
    assert _client().post("/api/v1/auth/exchange", json={"code": code_a}).status_code == 200
    assert _client().post("/api/v1/auth/exchange", json={"code": code_b}).status_code == 200


def test_different_activation_links_register_simultaneously(db, monkeypatch):
    """Separate activation rows do not block or consume one another."""
    issuer = _user(db, "parallel.activation.issuer", admin=True)
    user_a = _user(db, "parallel.activation.a", activated=False)
    user_b = _user(db, "parallel.activation.b", activated=False)
    token_a, _ = create_activation_link(user_a.id, issuer.id, db)
    token_b, _ = create_activation_link(user_b.id, issuer.id, db)
    db.commit()
    ceremony_a, ceremony_b = _run_together(
        lambda: _activation_begin(token_a),
        lambda: _activation_begin(token_b),
    )
    _mock_registration(monkeypatch, synchronise=True)

    response_a, response_b = _run_together(
        lambda: _client().post(
            "/api/v1/passkey/register/complete",
            headers={"X-Activation-Token": token_a},
            json=_registration_body(b"parallel-register-a", ceremony_a),
        ),
        lambda: _client().post(
            "/api/v1/passkey/register/complete",
            headers={"X-Activation-Token": token_b},
            json=_registration_body(b"parallel-register-b", ceremony_b),
        ),
    )

    assert response_a.status_code == 200, response_a.text
    assert response_b.status_code == 200, response_b.text
    db.expire_all()
    assert db.query(WebAuthnCredential).count() == 2
    assert db.get(User, user_a.id).is_activated is True
    assert db.get(User, user_b.id).is_activated is True


def test_distinct_account_sessions_register_simultaneously(db, monkeypatch):
    """Account passkey registration remains independent between administrators."""
    user_a = _user(db, "parallel.account.a", admin=True)
    user_b = _user(db, "parallel.account.b", admin=True)
    token_a, csrf_a = _session(db, user_a)
    token_b, csrf_b = _session(db, user_b)
    client_a = _session_client(token_a, csrf_a)
    client_b = _session_client(token_b, csrf_b)
    begin_response_a, begin_response_b = _run_together(
        lambda: client_a.post("/api/v1/passkey/register/begin", json={}),
        lambda: client_b.post("/api/v1/passkey/register/begin", json={}),
    )
    assert begin_response_a.status_code == 200, begin_response_a.text
    assert begin_response_b.status_code == 200, begin_response_b.text
    begin_a = begin_response_a.json()
    begin_b = begin_response_b.json()
    _mock_registration(monkeypatch, synchronise=True)

    response_a, response_b = _run_together(
        lambda: _session_client(token_a, csrf_a).post(
            "/api/v1/passkey/register/complete",
            json=_registration_body(b"parallel-account-a", begin_a["ceremony_id"]),
        ),
        lambda: _session_client(token_b, csrf_b).post(
            "/api/v1/passkey/register/complete",
            json=_registration_body(b"parallel-account-b", begin_b["ceremony_id"]),
        ),
    )

    assert response_a.status_code == 200, response_a.text
    assert response_b.status_code == 200, response_b.text
    db.expire_all()
    assert db.query(WebAuthnCredential).count() == 2


def test_one_authentication_ceremony_can_only_complete_once(db, monkeypatch):
    """Concurrent replay of one ceremony produces exactly one success."""
    user = _user(db, "parallel.replay")
    credential_id = b"parallel-replay"
    _credential(db, user, credential_id)
    _mock_authentication(monkeypatch)
    ceremony_id = _client().post("/api/v1/passkey/auth/begin").json()["ceremony_id"]
    body = _auth_body(credential_id, user.id, ceremony_id)

    responses = _run_together(
        lambda: _client().post("/api/v1/passkey/auth/complete", json=body),
        lambda: _client().post("/api/v1/passkey/auth/complete", json=body),
    )

    assert sorted(response.status_code for response in responses) == [200, 400]


def test_one_activation_link_can_only_register_once(db, monkeypatch):
    """Two ceremonies for one activation link cannot both activate it."""
    issuer = _user(db, "parallel.single.issuer", admin=True)
    user = _user(db, "parallel.single.user", activated=False)
    token, _ = create_activation_link(user.id, issuer.id, db)
    db.commit()
    ceremony_a, ceremony_b = _run_together(
        lambda: _activation_begin(token),
        lambda: _activation_begin(token),
    )
    _mock_registration(monkeypatch)

    responses = _run_together(
        lambda: _client().post(
            "/api/v1/passkey/register/complete",
            headers={"X-Activation-Token": token},
            json=_registration_body(b"single-link-a", ceremony_a),
        ),
        lambda: _client().post(
            "/api/v1/passkey/register/complete",
            headers={"X-Activation-Token": token},
            json=_registration_body(b"single-link-b", ceremony_b),
        ),
    )

    assert sorted(response.status_code for response in responses) == [200, 401]
    db.expire_all()
    assert db.query(WebAuthnCredential).count() == 1


def test_duplicate_credential_registration_is_atomic(db, monkeypatch):
    """Concurrent duplicate credentials produce one row and one conflict."""
    issuer = _user(db, "parallel.duplicate.issuer", admin=True)
    user_a = _user(db, "parallel.duplicate.a", activated=False)
    user_b = _user(db, "parallel.duplicate.b", activated=False)
    token_a, _ = create_activation_link(user_a.id, issuer.id, db)
    token_b, _ = create_activation_link(user_b.id, issuer.id, db)
    db.commit()
    ceremony_a, ceremony_b = _run_together(
        lambda: _activation_begin(token_a),
        lambda: _activation_begin(token_b),
    )
    duplicate_id = b"parallel-duplicate"
    _mock_registration(monkeypatch, duplicate_id, synchronise=True)

    responses = _run_together(
        lambda: _client().post(
            "/api/v1/passkey/register/complete",
            headers={"X-Activation-Token": token_a},
            json=_registration_body(duplicate_id, ceremony_a),
        ),
        lambda: _client().post(
            "/api/v1/passkey/register/complete",
            headers={"X-Activation-Token": token_b},
            json=_registration_body(duplicate_id, ceremony_b),
        ),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    db.expire_all()
    assert db.query(WebAuthnCredential).filter(
        WebAuthnCredential.credential_id == duplicate_id,
    ).count() == 1
