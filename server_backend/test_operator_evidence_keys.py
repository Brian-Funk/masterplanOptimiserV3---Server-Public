"""Synthetic Phase F four-domain key-separation contracts."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from webauthn.helpers import bytes_to_base64url

from app.api.v1 import evidence_keys as trust_api
from app.core.config import settings
from app.core.operator_evidence import TRUST_NAMESPACE, canonical_json, key_id, signature_envelope
from app.models.evidence import EvidenceKey, EvidenceKeyRegistrationChallenge, RootActionAuthorisation
from app.models.user import WebAuthnCredential
from deploy.management.instance_key import InstanceKeyError, commission, compare, verify
from server_backend.conftest import _make_client, create_test_user
from server_backend.test_governance import PROFILE, PUBLICATION_CONFIRMATION
from tools.controller_custody import generate as generate_controller, sign as controller_sign


BASE = "/api/v1/admin/evidence"


def _root(db):
    user = create_test_user(db, username=f"phasef.root.{uuid.uuid4().hex[:8]}", display_name="Synthetic Root", is_root_admin=True, is_admin=True)
    credential_id = f"root-passkey-{uuid.uuid4()}".encode()
    db.add(WebAuthnCredential(user_id=user.id, credential_id=credential_id, public_key=b"synthetic-public", sign_count=0))
    db.commit()
    return _make_client(db, user, reauth=True), user, credential_id


def _keypair() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode("ascii")
    return private, public


def _proof(private: Ed25519PrivateKey, document: dict) -> dict:
    public = private.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode("ascii")
    signature = private.sign(TRUST_NAMESPACE.encode("ascii") + b"\0" + canonical_json(document))
    return signature_envelope(key_identifier=key_id(public), signature=signature)


def _begin(client, public: str, role: str, entity_id: str, **rotation) -> dict:
    response = client.post(f"{BASE}/trust-keys/challenges", json={"public_key": public, "role": role, "entity_id": entity_id, **rotation})
    assert response.status_code == 200, response.text
    return response.json()["challenge"]


def _auth_body(credential_id: bytes, user_id: int, ceremony_id: str) -> dict:
    encoded = bytes_to_base64url(credential_id)
    return {"ceremony_id": ceremony_id, "credential": {"id": encoded, "rawId": encoded, "type": "public-key", "response": {"userHandle": bytes_to_base64url(str(user_id).encode())}}}


def _install_passkey_success(monkeypatch):
    monkeypatch.setattr(trust_api, "verify_authentication_response", lambda **kwargs: SimpleNamespace(new_sign_count=kwargs["credential_current_sign_count"] + 1))


def _activate(db, monkeypatch, *, role="controller", entity_id="ctl-synthetic0001", previous=None, reason=None):
    client, root, credential_id = _root(db)
    private, public = _keypair()
    rotation = {}
    if previous:
        rotation = {"supersedes_key_id": previous["key"]["key_id"], "reason": reason}
    challenge = _begin(client, public, role, entity_id, **rotation)
    body = {"challenge": challenge, "proof": _proof(private, challenge), "previous_proof": None}
    if previous and reason == "routine": body["previous_proof"] = _proof(previous["private"], challenge)
    proof = client.post(f"{BASE}/trust-keys/proofs", json=body)
    assert proof.status_code == 200, proof.text
    begin = client.post(f"{BASE}/trust-keys/{challenge['challenge_id']}/root-authorisation/begin", json={})
    assert begin.status_code == 200, begin.text
    _install_passkey_success(monkeypatch)
    complete = client.post(
        f"{BASE}/trust-keys/{challenge['challenge_id']}/root-authorisation/complete",
        json=_auth_body(credential_id, root.id, begin.json()["ceremony_id"]),
    )
    assert complete.status_code == 200, complete.text
    return {"client": client, "root": root, "private": private, "public": public, "challenge": challenge, "key": complete.json()["key"]}


def test_controller_and_processor_challenges_are_entity_instance_action_and_role_bound(db):
    client, _root_user, _credential = _root(db)
    _private, public = _keypair()
    controller = _begin(client, public, "controller", "ctl-synthetic0001")
    assert controller["role"] == "controller"
    assert controller["entity_id"] == "ctl-synthetic0001"
    assert controller["algorithm"] == "Ed25519"
    assert controller["action_sha256"] == hashlib.sha256(canonical_json({
        "format": "mp-opt-trust-action-v1", "action": controller["action"],
        "instance_id": controller["instance_id"], "entity_id": controller["entity_id"],
        "key_id": controller["key_id"], "role": controller["role"], "algorithm": controller["algorithm"],
        "public_key_sha256": controller["public_key_sha256"], "supersedes_key_id": None, "reason": None,
    })).hexdigest()
    invalid = client.post(f"{BASE}/trust-keys/challenges", json={"public_key": public, "role": "processor", "entity_id": "ctl-synthetic0001"})
    assert invalid.status_code == 409
    private_key_marker = "-----BEGIN " + "PRIVATE KEY-----"
    private_input = client.post(f"{BASE}/trust-keys/challenges", json={"public_key": public, "role": "controller", "entity_id": "ctl-synthetic0001", "private_key": private_key_marker})
    assert private_input.status_code == 422


def test_proof_precedes_single_use_exact_root_passkey_activation(db, monkeypatch):
    activated = _activate(db, monkeypatch)
    row = db.query(EvidenceKey).filter(EvidenceKey.key_id == activated["key"]["key_id"]).one()
    authorisation = db.query(RootActionAuthorisation).one()
    assert row.role == "controller" and row.entity_id == "ctl-synthetic0001"
    assert row.root_action_sha256 == authorisation.action_sha256
    assert authorisation.role == "root_passkey"
    assert activated["key"]["validity_status"] == "active"
    replay = activated["client"].post(
        f"{BASE}/trust-keys/{activated['challenge']['challenge_id']}/root-authorisation/begin", json={},
    )
    assert replay.status_code == 409
    assert "private" not in json.dumps(activated["key"]).lower()


def test_wrong_proof_expired_challenge_and_changed_instance_are_rejected(db):
    client, _root_user, _credential = _root(db)
    private, public = _keypair(); wrong, _ = _keypair()
    challenge = _begin(client, public, "processor", "prc-synthetic0001")
    denied = client.post(f"{BASE}/trust-keys/proofs", json={"challenge": challenge, "proof": _proof(wrong, challenge)})
    assert denied.status_code == 409
    changed = challenge | {"instance_id": str(uuid.uuid4())}
    changed_proof = client.post(f"{BASE}/trust-keys/proofs", json={"challenge": changed, "proof": _proof(private, changed)})
    assert changed_proof.status_code == 409
    stored = db.query(EvidenceKeyRegistrationChallenge).filter(EvidenceKeyRegistrationChallenge.challenge_id == challenge["challenge_id"]).one()
    stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1); db.commit()
    expired = client.post(f"{BASE}/trust-keys/proofs", json={"challenge": challenge, "proof": _proof(private, challenge)})
    assert expired.status_code == 409


def test_separate_organisations_and_same_organisation_use_separate_role_keys(db, monkeypatch):
    controller = _activate(db, monkeypatch, role="controller", entity_id="ctl-organisation01")
    processor = _activate(db, monkeypatch, role="processor", entity_id="prc-organisation02")
    same_org_processor = _activate(db, monkeypatch, role="processor", entity_id="prc-organisation01")
    assert len({controller["key"]["key_id"], processor["key"]["key_id"], same_org_processor["key"]["key_id"]}) == 3
    assert controller["key"]["role"] != same_org_processor["key"]["role"]


def test_routine_rotation_requires_old_and_new_proof_and_history_is_preserved(db, monkeypatch):
    previous = _activate(db, monkeypatch, role="processor", entity_id="prc-rotation0001")
    client, _root_user, _credential = _root(db); new_private, new_public = _keypair()
    challenge = _begin(client, new_public, "processor", "prc-rotation0001", supersedes_key_id=previous["key"]["key_id"], reason="routine")
    missing = client.post(f"{BASE}/trust-keys/proofs", json={"challenge": challenge, "proof": _proof(new_private, challenge)})
    assert missing.status_code == 409
    successor = _activate(db, monkeypatch, role="processor", entity_id="prc-rotation0001", previous=previous, reason="routine")
    historic = db.query(EvidenceKey).filter(EvidenceKey.key_id == previous["key"]["key_id"]).one()
    assert historic.revocation_reason == "retired"
    assert historic.superseded_by_key_id == successor["key"]["key_id"]
    assert historic.public_key == previous["public"]


def test_role_statement_rejects_cross_role_and_accepts_controller_declaration(db, monkeypatch):
    controller = _activate(db, monkeypatch, role="controller", entity_id="ctl-declaration01")
    assert controller["client"].put("/api/v1/admin/governance", json=PROFILE).status_code == 200
    monkeypatch.setattr(settings, "KEY_SEPARATION_ENFORCED", True)
    blocked = controller["client"].post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "controller_trust_required"
    document = {
        "format": "mp-opt-controller-trust-declaration-v1", "instance_id": controller["key"]["instance_id"],
        "entity_id": controller["key"]["entity_id"], "key_id": controller["key"]["key_id"],
        "role": "controller", "algorithm": "Ed25519", "public_key_sha256": controller["key"]["public_key_sha256"],
        "statement_type": "initial_trust_declaration", "statement_sha256": hashlib.sha256(b"synthetic controller trust").hexdigest(),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    accepted = controller["client"].post(f"{BASE}/trust-keys/{controller['key']['key_id']}/statements/import", json={"document": document, "proof": _proof(controller["private"], document)})
    assert accepted.status_code == 200, accepted.text
    published = controller["client"].post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION)
    assert published.status_code == 200, published.text
    wrong_role = document | {"role": "processor", "format": "mp-opt-processor-statement-v1", "statement_type": "receipt"}
    rejected = controller["client"].post(f"{BASE}/trust-keys/{controller['key']['key_id']}/statements/import", json={"document": wrong_role, "proof": _proof(controller["private"], wrong_role)})
    assert rejected.status_code == 409


def test_instance_key_commissioning_is_exactly_once_fail_closed_and_ha_consistent(tmp_path):
    instance_id = str(uuid.uuid4()); local = tmp_path / "local"; peer = tmp_path / "peer"
    first = commission(local, instance_id)
    assert verify(local, instance_id)["public_key_sha256"] == first["public_key_sha256"]
    try: commission(local, instance_id)
    except InstanceKeyError as exc: assert "already exists" in str(exc)
    else: raise AssertionError("commissioning regenerated an instance key")
    peer.mkdir();
    for source in local.iterdir():
        target = peer / source.name
        if source.name == "evidence_signing_key":
            os.link(source, target)
        else:
            target.write_bytes(source.read_bytes())
            os.chmod(target, 0o600)
    assert compare(local, peer)["consistent"] is True
    (peer / "evidence_signing_key.pub").write_text("ssh-ed25519 invalid\n", encoding="ascii")
    try: compare(local, peer)
    except InstanceKeyError: pass
    else: raise AssertionError("HA fingerprint mismatch did not fail closed")


def test_controller_utility_keeps_encrypted_private_key_outside_server_and_signs_only_controller(tmp_path, monkeypatch):
    passphrase = b"synthetic-passphrase-long"
    package = generate_controller("ctl-custody0001", tmp_path, passphrase=passphrase)
    private_path = Path(package["private_key_path"])
    encrypted_private_key_label = b"ENCRYPTED " + b"PRIVATE KEY"
    assert encrypted_private_key_label in private_path.read_bytes()
    assert "private" not in Path(package["public_package_path"]).read_text(encoding="utf-8").lower()
    document = {
        "format": "mp-opt-controller-trust-declaration-v1", "instance_id": str(uuid.uuid4()),
        "entity_id": "ctl-custody0001", "key_id": package["key_id"], "role": "controller",
        "algorithm": "Ed25519", "public_key_sha256": package["public_key_sha256"],
        "statement_type": "initial_trust_declaration", "statement_sha256": hashlib.sha256(b"synthetic").hexdigest(),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    signed = controller_sign(private_path, document, passphrase=passphrase)
    assert signed["proof"]["namespace"] == TRUST_NAMESPACE
    processor = document | {"format": "mp-opt-processor-statement-v1", "role": "processor", "entity_id": "prc-custody0001", "statement_type": "receipt"}
    try: controller_sign(private_path, processor, passphrase=passphrase)
    except ValueError: pass
    else: raise AssertionError("controller utility signed a processor statement")
