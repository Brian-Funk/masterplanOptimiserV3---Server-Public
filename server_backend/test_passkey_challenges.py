"""Security regressions for scoped, single-use WebAuthn ceremonies."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from webauthn.helpers import bytes_to_base64url

from app.api.v1 import passkey as passkey_api
from app.core.activation import ADDITIONAL_PASSKEY, create_activation_link
from app.core.passkey_ceremonies import (
    ACCOUNT_REGISTRATION,
    AUTHENTICATION,
)
from app.models.audit import AuditLog
from app.models.governance import AccountProcessingConsent
from app.models.user import PasskeyCeremony, WebAuthnCredential
from app.core.evidence import EvidenceUnavailable
from server_backend.conftest import (
    _make_client,
    _raw_client,
    create_test_governance_publication,
    create_test_user,
)


def _credential_id(value: str) -> bytes:
    return value.encode("utf-8")


def _raw_id(value: str) -> str:
    return bytes_to_base64url(_credential_id(value))


def _auth_body(
    credential_name: str,
    user_id: int,
    ceremony_id: str,
    *,
    user_handle: int | None = None,
) -> dict:
    """Build a discoverable-passkey completion payload."""
    handle_id = user_id if user_handle is None else user_handle
    return {
        "ceremony_id": ceremony_id,
        "credential": {
            "id": _raw_id(credential_name),
            "rawId": _raw_id(credential_name),
            "type": "public-key",
            "response": {
                "userHandle": bytes_to_base64url(str(handle_id).encode()),
            },
        },
    }


def _registration_body(ceremony_id: str) -> dict:
    """Build a registration completion payload for a mocked verifier."""
    return {
        "ceremony_id": ceremony_id,
        "credential": {
            "id": "new-credential",
            "rawId": "new-credential",
            "type": "public-key",
            "response": {},
        },
    }


def _processing_consent(client, token: str) -> dict:
    response = client.post("/api/v1/activation/validate", json={"token": token})
    assert response.status_code == 200, response.text
    disclosure = response.json()["processing_consent"]
    return {
        "confirmed": True,
        "statement_version": disclosure["format"],
        "statement_sha256": disclosure["statement_sha256"],
        "policy_version": disclosure["policy_version"],
        "policy_sha256": disclosure["policy_sha256"],
    }


def _install_auth_success(monkeypatch):
    def fake_verify_authentication_response(**kwargs):
        assert kwargs["require_user_verification"] is True
        return SimpleNamespace(
            new_sign_count=kwargs["credential_current_sign_count"] + 1,
        )

    monkeypatch.setattr(
        passkey_api,
        "verify_authentication_response",
        fake_verify_authentication_response,
    )


def _install_registration_success(monkeypatch, credential_id: bytes):
    def fake_verify_registration_response(**kwargs):
        assert kwargs["require_user_verification"] is True
        return SimpleNamespace(
            credential_id=credential_id,
            credential_public_key=b"public-key",
            sign_count=0,
            aaguid=None,
        )

    monkeypatch.setattr(
        passkey_api,
        "verify_registration_response",
        fake_verify_registration_response,
    )
    monkeypatch.setattr(passkey_api, "append_record", lambda *args, **kwargs: "a" * 64)


def _add_credential(db, user, credential_name: str) -> WebAuthnCredential:
    credential = WebAuthnCredential(
        user_id=user.id,
        credential_id=_credential_id(credential_name),
        public_key=b"public-key",
        sign_count=0,
    )
    db.add(credential)
    db.commit()
    return credential


def _ceremony(db, ceremony_id: str) -> PasskeyCeremony:
    return db.query(PasskeyCeremony).filter(PasskeyCeremony.id == ceremony_id).one()


def test_root_bootstrap_commits_credential_and_audit_with_production_ip_hash(
    db,
    monkeypatch,
):
    """A valid first passkey must not roll back while writing its audit row."""
    from app.core.config import settings
    from app.core.commissioning import commissioning_required
    import app.core.security as security_module
    from app.core.governance import BOOTSTRAP_POLICY_SHA256, BOOTSTRAP_POLICY_VERSION
    from app.models.server_setting import ServerSetting

    monkeypatch.setattr(security_module, "commissioning_required", commissioning_required)

    token = "root-bootstrap-regression-token-with-enough-entropy"
    root = create_test_user(
        db,
        username="bootstrap.root",
        is_root_admin=True,
        is_admin=True,
        is_activated=False,
    )
    monkeypatch.setattr(settings, "ROOT_BOOTSTRAP_TOKEN", token)
    monkeypatch.setattr(
        settings,
        "IP_HMAC_KEY",
        "bootstrap-audit-ip-key-with-sufficient-entropy",
    )
    _install_registration_success(monkeypatch, b"root-bootstrap-credential")
    client = _raw_client()

    begin = client.post(
        "/api/v1/passkey/bootstrap/begin",
        headers={"X-Bootstrap-Token": token},
    )
    assert begin.status_code == 200
    complete = client.post(
        "/api/v1/passkey/bootstrap/complete",
        headers={"X-Bootstrap-Token": token},
        json={
            **_registration_body(begin.json()["ceremony_id"]),
            "policy_version": BOOTSTRAP_POLICY_VERSION,
            "policy_sha256": BOOTSTRAP_POLICY_SHA256,
        },
    )

    assert complete.status_code == 200, complete.text
    assert db.query(WebAuthnCredential).filter_by(user_id=root.id).count() == 1
    assert db.query(AuditLog).filter_by(action="passkey.bootstrap").count() == 1
    db.refresh(root)
    assert root.is_activated is True
    assert db.query(ServerSetting).filter_by(
        key="root_bootstrap_disabled",
        value="true",
    ).count() == 1
    status = client.get("/api/v1/passkey/bootstrap-status")
    assert status.json()["stage"] == "setup"
    assert status.json()["needs_bootstrap"] is False
    _install_auth_success(monkeypatch)
    restricted_begin = client.post("/api/v1/passkey/auth/begin").json()
    restricted_login = client.post(
        "/api/v1/passkey/auth/complete",
        json=_auth_body(
            "root-bootstrap-credential", root.id,
            restricted_begin["ceremony_id"],
        ),
    )
    assert restricted_login.status_code == 200
    exchange = client.post(
        "/api/v1/auth/exchange",
        json={"code": restricted_login.json()["exchange_code"]},
    )
    assert exchange.status_code == 200, exchange.text
    assert exchange.json()["commissioning_required"] is True
    assert exchange.json()["commissioning_stage"] == "recovery"
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["commissioning_required"] is True
    assert me.json()["commissioning_stage"] == "recovery"
    assert client.get("/api/v1/auth/root-access").status_code == 423

    bootstrap_token_only = _raw_client().post(
        "/api/v1/setup/recovery/complete",
        headers={"X-Bootstrap-Token": token},
        json={
            "recipient": "age1" + "q" * 58,
            "download_acknowledged": True,
            "local_reimport_verified": True,
        },
    )
    assert bootstrap_token_only.status_code == 401

    recipient = "age1" + "q" * 58
    csrf = client.cookies.get(settings.CSRF_COOKIE_NAME)
    assert csrf
    final = client.post(
        "/api/v1/setup/recovery/complete",
        headers={"X-CSRF-Token": csrf},
        json={"recipient": recipient, "download_acknowledged": True, "local_reimport_verified": True},
    )
    assert final.status_code == 200, final.text
    db.refresh(root)
    assert root.is_activated is True
    assert db.query(ServerSetting).filter_by(
        key="root_bootstrap_disabled", value="true",
    ).count() == 1
    assert db.query(ServerSetting).filter_by(
        key="root_recovery_recipient_sha256",
        value=hashlib.sha256(recipient.encode("ascii")).hexdigest(),
    ).count() == 1
    assert db.query(AuditLog).filter_by(action="commissioning.recovery_completed").count() == 1
    assert client.get("/api/v1/auth/root-access").status_code == 423
    allowed_begin = client.post("/api/v1/passkey/auth/begin").json()
    allowed_login = client.post(
        "/api/v1/passkey/auth/complete",
        json=_auth_body(
            "root-bootstrap-credential", root.id,
            allowed_begin["ceremony_id"],
        ),
    )
    assert allowed_login.status_code == 200
    allowed_exchange = client.post(
        "/api/v1/auth/exchange",
        json={"code": allowed_login.json()["exchange_code"]},
    )
    assert allowed_exchange.json()["commissioning_required"] is True
    assert allowed_exchange.json()["commissioning_stage"] == "controller"


def test_root_recovery_completion_rejects_missing_download_acknowledgement(db, monkeypatch):
    from app.core.config import settings

    root = create_test_user(
        db, username="recovery.pending", is_root_admin=True,
        is_admin=True, is_activated=True,
    )
    _add_credential(db, root, "pending-recovery-credential")
    _install_auth_success(monkeypatch)
    client = _raw_client()
    begin = client.post("/api/v1/passkey/auth/begin").json()
    login = client.post(
        "/api/v1/passkey/auth/complete",
        json=_auth_body(
            "pending-recovery-credential", root.id, begin["ceremony_id"],
        ),
    )
    exchange = client.post(
        "/api/v1/auth/exchange",
        json={"code": login.json()["exchange_code"]},
    )
    assert exchange.status_code == 200
    csrf = client.cookies.get(settings.CSRF_COOKIE_NAME)
    assert csrf

    response = client.post(
        "/api/v1/setup/recovery/complete",
        headers={"X-CSRF-Token": csrf},
        json={"recipient": "age1" + "q" * 58, "download_acknowledged": False, "local_reimport_verified": True},
    )

    assert response.status_code == 422
    db.refresh(root)
    assert root.is_activated is True


def test_verified_reset_replaces_all_previous_passkeys(db):
    """A successful reset leaves only the newly verified credential usable."""

    user = create_test_user(db, username="replace.passkeys", is_activated=True)
    _add_credential(db, user, "old-credential-one")
    _add_credential(db, user, "old-credential-two")
    replacement = WebAuthnCredential(
        user_id=user.id,
        credential_id=_credential_id("replacement-credential"),
        public_key=b"replacement-public-key",
        sign_count=0,
    )
    db.add(replacement)
    db.flush()

    replaced = passkey_api._replace_previous_credentials(
        user_id=user.id,
        new_credential=replacement,
        db=db,
    )
    db.commit()

    credentials = db.query(WebAuthnCredential).filter_by(user_id=user.id).all()
    assert replaced == 2
    assert [credential.id for credential in credentials] == [replacement.id]
    assert credentials[0].friendly_name == "Replacement passkey"


def test_additional_passkey_policy_preserves_credentials_and_sessions(db):
    """Additive invitations retain every old passkey and signed-in session."""

    user = create_test_user(db, username="add.passkey", is_activated=True)
    old_credential = _add_credential(db, user, "old-additive-credential")
    added = WebAuthnCredential(
        user_id=user.id,
        credential_id=_credential_id("new-additive-credential"),
        public_key=b"new-public-key",
        sign_count=0,
    )
    db.add(added)
    db.flush()

    replaced = passkey_api._apply_activation_credential_policy(
        user_id=user.id,
        new_credential=added,
        activation_purpose=ADDITIONAL_PASSKEY,
        db=db,
    )
    db.commit()

    credential_ids = {
        credential.id
        for credential in db.query(WebAuthnCredential).filter_by(user_id=user.id)
    }
    assert replaced == 0
    assert credential_ids == {old_credential.id, added.id}
    assert added.friendly_name == "Additional passkey"
    assert passkey_api._registration_preserves_sessions(ADDITIONAL_PASSKEY) is True
    assert passkey_api._registration_preserves_sessions("credential_reset") is False


def test_concurrent_login_ceremonies_verify_independently(db, monkeypatch):
    """Completing one login attempt does not consume another attempt."""
    user_a = create_test_user(db, username="login.a")
    user_b = create_test_user(db, username="login.b")
    _add_credential(db, user_a, "cred-a")
    _add_credential(db, user_b, "cred-b")
    _install_auth_success(monkeypatch)

    client = _raw_client()
    begin_a = client.post("/api/v1/passkey/auth/begin").json()
    begin_b = client.post("/api/v1/passkey/auth/begin").json()

    assert begin_a["ceremony_id"] != begin_b["ceremony_id"]
    assert db.query(PasskeyCeremony).filter(
        PasskeyCeremony.purpose == AUTHENTICATION,
    ).count() == 2

    first = client.post(
        "/api/v1/passkey/auth/complete",
        json=_auth_body("cred-a", user_a.id, begin_a["ceremony_id"]),
    )
    assert first.status_code == 200, first.text
    assert _ceremony(db, begin_a["ceremony_id"]).consumed_at is not None
    assert _ceremony(db, begin_b["ceremony_id"]).consumed_at is None

    second = client.post(
        "/api/v1/passkey/auth/complete",
        json=_auth_body("cred-b", user_b.id, begin_b["ceremony_id"]),
    )
    assert second.status_code == 200
    assert _ceremony(db, begin_b["ceremony_id"]).consumed_at is not None


def test_auth_completion_requires_ceremony_id(db):
    """Legacy clientData fallback cannot bypass exact attempt selection."""
    user = create_test_user(db, username="no.ceremony")
    _add_credential(db, user, "no-ceremony-cred")

    response = _raw_client().post(
        "/api/v1/passkey/auth/complete",
        json={
            "credential": {
                "id": _raw_id("no-ceremony-cred"),
                "rawId": _raw_id("no-ceremony-cred"),
                "type": "public-key",
                "response": {},
            }
        },
    )

    assert response.status_code == 422


def test_unknown_ceremony_does_not_consume_valid_attempt(db, monkeypatch):
    user = create_test_user(db, username="unknown.login")
    _add_credential(db, user, "unknown-cred")
    _install_auth_success(monkeypatch)
    client = _raw_client()
    begin = client.post("/api/v1/passkey/auth/begin").json()

    response = client.post(
        "/api/v1/passkey/auth/complete",
        json=_auth_body(
            "unknown-cred",
            user.id,
            "x" * 43,
        ),
    )

    assert response.status_code == 400
    assert "unknown" in response.json()["detail"].lower()
    assert _ceremony(db, begin["ceremony_id"]).consumed_at is None


def test_expired_authentication_ceremony_fails_cleanly(db, monkeypatch):
    user = create_test_user(db, username="expired.login")
    _add_credential(db, user, "expired-cred")
    _install_auth_success(monkeypatch)
    client = _raw_client()
    begin = client.post("/api/v1/passkey/auth/begin").json()
    ceremony = _ceremony(db, begin["ceremony_id"])
    ceremony.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    response = client.post(
        "/api/v1/passkey/auth/complete",
        json=_auth_body("expired-cred", user.id, begin["ceremony_id"]),
    )

    assert response.status_code == 400
    assert "expired" in response.json()["detail"].lower()


def test_used_authentication_ceremony_cannot_be_replayed(db, monkeypatch):
    user = create_test_user(db, username="replay.login")
    _add_credential(db, user, "replay-cred")
    _install_auth_success(monkeypatch)
    client = _raw_client()
    begin = client.post("/api/v1/passkey/auth/begin").json()
    body = _auth_body("replay-cred", user.id, begin["ceremony_id"])

    assert client.post("/api/v1/passkey/auth/complete", json=body).status_code == 200
    replay = client.post("/api/v1/passkey/auth/complete", json=body)

    assert replay.status_code == 400
    assert "already been used" in replay.json()["detail"]


def test_wrong_user_handle_fails_and_consumes_attempt(db, monkeypatch):
    user = create_test_user(db, username="handle.login")
    other = create_test_user(db, username="handle.other")
    _add_credential(db, user, "handle-cred")
    _install_auth_success(monkeypatch)
    client = _raw_client()
    begin = client.post("/api/v1/passkey/auth/begin").json()

    response = client.post(
        "/api/v1/passkey/auth/complete",
        json=_auth_body(
            "handle-cred",
            user.id,
            begin["ceremony_id"],
            user_handle=other.id,
        ),
    )

    assert response.status_code == 400
    assert _ceremony(db, begin["ceremony_id"]).consumed_at is not None


def test_inactive_user_cannot_authenticate(db, monkeypatch):
    user = create_test_user(db, username="inactive.login")
    _add_credential(db, user, "inactive-cred")
    user.is_active = False
    db.commit()
    _install_auth_success(monkeypatch)
    client = _raw_client()
    begin = client.post("/api/v1/passkey/auth/begin").json()

    response = client.post(
        "/api/v1/passkey/auth/complete",
        json=_auth_body("inactive-cred", user.id, begin["ceremony_id"]),
    )

    assert response.status_code == 401
    assert _ceremony(db, begin["ceremony_id"]).consumed_at is not None
    assert db.query(AuditLog).filter(
        AuditLog.action == "passkey.auth_failed",
        AuditLog.user_id == user.id,
    ).count() == 1


def test_ceremony_for_wrong_purpose_is_rejected(db, monkeypatch):
    user = create_test_user(db, username="purpose.admin", is_admin=True)
    _add_credential(db, user, "purpose-cred")
    client = _make_client(db, user, reauth=True)
    registration = client.post("/api/v1/passkey/register/begin").json()
    _install_auth_success(monkeypatch)

    response = _raw_client().post(
        "/api/v1/passkey/auth/complete",
        json=_auth_body("purpose-cred", user.id, registration["ceremony_id"]),
    )

    assert response.status_code == 400
    assert "wrong purpose" in response.json()["detail"]
    assert _ceremony(db, registration["ceremony_id"]).consumed_at is None


def test_registration_cannot_use_another_users_ceremony(db, monkeypatch):
    user_a = create_test_user(db, username="register.a", is_admin=True)
    user_b = create_test_user(db, username="register.b", is_admin=True)
    client_a = _make_client(db, user_a, reauth=True)
    client_b = _make_client(db, user_b, reauth=True)
    begin_b = client_b.post("/api/v1/passkey/register/begin").json()
    _install_registration_success(monkeypatch, b"wrong-user-cred")

    response = client_a.post(
        "/api/v1/passkey/register/complete",
        json=_registration_body(begin_b["ceremony_id"]),
    )

    assert response.status_code == 400
    assert "account" in response.json()["detail"]
    assert _ceremony(db, begin_b["ceremony_id"]).consumed_at is None


def test_account_registration_attempts_have_independent_records(db, monkeypatch):
    """Starting a second registration does not overwrite the first challenge."""
    user = create_test_user(db, username="register.admin", is_admin=True)
    client = _make_client(db, user, reauth=True)
    first = client.post("/api/v1/passkey/register/begin").json()
    second = client.post("/api/v1/passkey/register/begin").json()
    _install_registration_success(monkeypatch, b"registered-cred")

    response = client.post(
        "/api/v1/passkey/register/complete",
        json=_registration_body(first["ceremony_id"]),
    )

    assert response.status_code == 200
    assert first["ceremony_id"] != second["ceremony_id"]
    assert _ceremony(db, first["ceremony_id"]).consumed_at is not None
    assert _ceremony(db, second["ceremony_id"]).consumed_at is None


def test_two_activation_devices_complete_independently(db, monkeypatch):
    """Separate activation attempts cannot invalidate each other's challenge."""
    issuer = create_test_user(db, username="activation.issuer", is_admin=True)
    user_a = create_test_user(db, username="activation.a", is_activated=False)
    user_b = create_test_user(db, username="activation.b", is_activated=False)
    token_a, _ = create_activation_link(user_a.id, issuer.id, db)
    token_b, _ = create_activation_link(user_b.id, issuer.id, db)
    create_test_governance_publication(db)
    db.commit()
    client = _raw_client()
    consent_a = _processing_consent(client, token_a)
    consent_b = _processing_consent(client, token_b)

    begin_a_response = client.post(
        "/api/v1/passkey/register/begin",
        headers={"X-Activation-Token": token_a},
        json=consent_a,
    )
    begin_b_response = client.post(
        "/api/v1/passkey/register/begin",
        headers={"X-Activation-Token": token_b},
        json=consent_b,
    )
    assert begin_a_response.status_code == 200, begin_a_response.text
    assert begin_b_response.status_code == 200, begin_b_response.text
    begin_a = begin_a_response.json()
    begin_b = begin_b_response.json()
    assert begin_a["ceremony_id"] != begin_b["ceremony_id"]

    _install_registration_success(monkeypatch, b"activation-cred-a")
    first = client.post(
        "/api/v1/passkey/register/complete",
        headers={"X-Activation-Token": token_a},
        json=_registration_body(begin_a["ceremony_id"]),
    )
    assert first.status_code == 200, first.text
    assert _ceremony(db, begin_b["ceremony_id"]).consumed_at is None

    _install_registration_success(monkeypatch, b"activation-cred-b")
    second = client.post(
        "/api/v1/passkey/register/complete",
        headers={"X-Activation-Token": token_b},
        json=_registration_body(begin_b["ceremony_id"]),
    )
    assert second.status_code == 200


def test_activation_token_is_single_use(db, monkeypatch):
    issuer = create_test_user(db, username="single.issuer", is_admin=True)
    user = create_test_user(db, username="single.activation", is_activated=False)
    token, _ = create_activation_link(user.id, issuer.id, db)
    create_test_governance_publication(db)
    db.commit()
    client = _raw_client()
    consent = _processing_consent(client, token)
    first_begin_response = client.post(
        "/api/v1/passkey/register/begin",
        headers={"X-Activation-Token": token},
        json=consent,
    )
    second_begin_response = client.post(
        "/api/v1/passkey/register/begin",
        headers={"X-Activation-Token": token},
        json=consent,
    )
    assert first_begin_response.status_code == 200, first_begin_response.text
    assert second_begin_response.status_code == 200, second_begin_response.text
    first_begin = first_begin_response.json()
    second_begin = second_begin_response.json()
    _install_registration_success(monkeypatch, b"single-activation-cred")

    first = client.post(
        "/api/v1/passkey/register/complete",
        headers={"X-Activation-Token": token},
        json=_registration_body(first_begin["ceremony_id"]),
    )
    second = client.post(
        "/api/v1/passkey/register/complete",
        headers={"X-Activation-Token": token},
        json=_registration_body(second_begin["ceremony_id"]),
    )

    assert first.status_code == 200
    assert second.status_code == 401


def test_duplicate_credential_id_is_rejected_across_users(db, monkeypatch):
    issuer = create_test_user(db, username="duplicate.issuer", is_admin=True)
    owner = create_test_user(db, username="duplicate.owner")
    target = create_test_user(db, username="duplicate.target", is_activated=False)
    _add_credential(db, owner, "shared-credential")
    token, _ = create_activation_link(target.id, issuer.id, db)
    create_test_governance_publication(db)
    db.commit()
    client = _raw_client()
    consent = _processing_consent(client, token)
    begin_response = client.post(
        "/api/v1/passkey/register/begin",
        headers={"X-Activation-Token": token},
        json=consent,
    )
    assert begin_response.status_code == 200, begin_response.text
    begin = begin_response.json()
    _install_registration_success(monkeypatch, b"shared-credential")

    response = client.post(
        "/api/v1/passkey/register/complete",
        headers={"X-Activation-Token": token},
        json=_registration_body(begin["ceremony_id"]),
    )

    assert response.status_code == 409
    assert _ceremony(db, begin["ceremony_id"]).consumed_at is not None


def test_initial_activation_requires_exact_processing_consent(db):
    issuer = create_test_user(db, username="consent.issuer", is_admin=True)
    user = create_test_user(db, username="consent.subject", is_activated=False)
    token, _ = create_activation_link(user.id, issuer.id, db)
    create_test_governance_publication(db)
    db.commit()
    client = _raw_client()
    consent = _processing_consent(client, token)

    absent = client.post(
        "/api/v1/passkey/register/begin",
        headers={"X-Activation-Token": token},
    )
    unchecked = client.post(
        "/api/v1/passkey/register/begin",
        headers={"X-Activation-Token": token},
        json={**consent, "confirmed": False},
    )
    stale = client.post(
        "/api/v1/passkey/register/begin",
        headers={"X-Activation-Token": token},
        json={**consent, "policy_sha256": "f" * 64},
    )

    assert absent.status_code == 428
    assert absent.json()["detail"]["code"] == "processing_consent_required"
    assert unchecked.status_code == 422
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "processing_consent_identity_mismatch"


def test_initial_activation_records_one_atomic_consent(db, monkeypatch):
    issuer = create_test_user(db, username="atomic.issuer", is_admin=True)
    user = create_test_user(db, username="atomic.subject", is_activated=False)
    token, link = create_activation_link(user.id, issuer.id, db)
    publication = create_test_governance_publication(db)
    db.commit()
    client = _raw_client()
    consent = _processing_consent(client, token)
    captured = {}

    _install_registration_success(monkeypatch, b"atomic-consent-credential")

    def record_consent(*args, **kwargs):
        captured.update(kwargs)
        return "b" * 64

    monkeypatch.setattr(passkey_api, "append_record", record_consent)
    begin = client.post(
        "/api/v1/passkey/register/begin",
        headers={"X-Activation-Token": token},
        json=consent,
    )
    assert begin.status_code == 200, begin.text

    complete = client.post(
        "/api/v1/passkey/register/complete",
        headers={"X-Activation-Token": token},
        json=_registration_body(begin.json()["ceremony_id"]),
    )

    assert complete.status_code == 200, complete.text
    db.expire_all()
    stored = db.query(AccountProcessingConsent).one()
    assert stored.user_subject_id == user.evidence_subject_id
    assert stored.activation_link_id == link.id
    assert stored.policy_version == publication.version
    assert stored.policy_sha256 == publication.content_sha256
    assert stored.statement_sha256 == consent["statement_sha256"]
    assert stored.instance_record_sha256 == "b" * 64
    assert captured["record_type"] == "account.processing_consent_recorded"
    assert db.query(WebAuthnCredential).filter_by(user_id=user.id).count() == 1
    assert db.get(type(link), link.id).used_at is not None
    assert db.get(type(user), user.id).is_activated is True


def test_evidence_failure_rolls_back_activation_and_consent(db, monkeypatch):
    issuer = create_test_user(db, username="evidence.issuer", is_admin=True)
    user = create_test_user(db, username="evidence.subject", is_activated=False)
    token, link = create_activation_link(user.id, issuer.id, db)
    create_test_governance_publication(db)
    db.commit()
    client = _raw_client()
    consent = _processing_consent(client, token)
    _install_registration_success(monkeypatch, b"unsealed-consent-credential")

    def unavailable(*args, **kwargs):
        raise EvidenceUnavailable("synthetic unavailable evidence")

    monkeypatch.setattr(passkey_api, "append_record", unavailable)
    begin = client.post(
        "/api/v1/passkey/register/begin",
        headers={"X-Activation-Token": token},
        json=consent,
    )
    response = client.post(
        "/api/v1/passkey/register/complete",
        headers={"X-Activation-Token": token},
        json=_registration_body(begin.json()["ceremony_id"]),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "processing_consent_evidence_unavailable"
    db.expire_all()
    assert db.query(AccountProcessingConsent).count() == 0
    assert db.query(WebAuthnCredential).filter_by(user_id=user.id).count() == 0
    assert db.get(type(link), link.id).used_at is None
    assert db.get(type(user), user.id).is_activated is False


def test_bootstrap_requires_operator_secret_and_discoverable_passkey(db, monkeypatch):
    create_test_user(
        db,
        username="bootstrap.root",
        is_root_admin=True,
        is_admin=True,
        is_activated=False,
    )
    monkeypatch.setattr(passkey_api.settings, "ROOT_BOOTSTRAP_TOKEN", "b" * 32)
    client = _raw_client()

    denied = client.post("/api/v1/passkey/bootstrap/begin")
    allowed = client.post(
        "/api/v1/passkey/bootstrap/begin",
        headers={"X-Bootstrap-Token": "b" * 32},
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    options = json.loads(allowed.json()["options"])
    assert options["authenticatorSelection"]["residentKey"] == "required"
    assert options["authenticatorSelection"]["userVerification"] == "required"


def test_registration_requires_recent_reauthentication(db):
    user = create_test_user(db, username="register.reauth", is_admin=True)
    client = _make_client(db, user)

    response = client.post("/api/v1/passkey/register/begin")

    assert response.status_code == 403
    assert "Re-authentication required" in response.json()["detail"]


def test_account_registration_purpose_is_session_scoped(db):
    user = create_test_user(db, username="register.scope", is_admin=True)
    client = _make_client(db, user, reauth=True)

    begin = client.post("/api/v1/passkey/register/begin").json()
    ceremony = _ceremony(db, begin["ceremony_id"])

    assert ceremony.purpose == ACCOUNT_REGISTRATION
    assert ceremony.user_id == user.id
    assert ceremony.session_id is not None
