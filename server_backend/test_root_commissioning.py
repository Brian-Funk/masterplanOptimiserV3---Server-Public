"""Authoritative root commissioning state, fencing, and safe report contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import app.core.security as security_module
from app.core.commissioning import commissioning_required, commissioning_stage, set_setting
from app.core.governance import stable_instance_id
from app.models.evidence import EvidenceKey
from app.models.governance import GovernancePublication
from app.models.user import WebAuthnCredential
from server_backend.conftest import _make_client, create_test_user


def _controller_key(db) -> EvidenceKey:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    ).decode("ascii")
    digest = hashlib.sha256(public.encode("ascii")).hexdigest()
    row = EvidenceKey(
        key_id=f"ek-{digest[:16]}",
        public_key=public,
        public_key_sha256=digest,
        instance_id=stable_instance_id(db),
        entity_id="ctl-commissioning01",
        controller_id=1,
        role="controller",
        activated_at=datetime.now(timezone.utc),
        trust_establishment_sha256="b" * 64,
    )
    db.add(row)
    db.commit()
    return row


def _governance_v1(db, root_id: int) -> GovernancePublication:
    content = {
        "controller_legal_name": "Synthetic Controller",
        "controller_postal_address": "Synthetic address",
        "controller_country": "CH",
        "privacy_contact_email": "privacy@example.invalid",
        "supervisory_authority_name": "Synthetic authority",
        "supervisory_authority_url": "https://example.invalid/authority",
        "processor_summary": "Synthetic processors.",
        "retention_summary": "Synthetic retention.",
        "rights_summary": "Synthetic rights procedure.",
        "terms_summary": "Synthetic terms.",
    }
    raw = json.dumps(content, sort_keys=True, separators=(",", ":"))
    row = GovernancePublication(
        version=1,
        content_json=raw,
        content_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        source_json="{}",
        source_sha256=hashlib.sha256(b"{}").hexdigest(),
        published_by_id=root_id,
        material_change=True,
        change_summary_json="[]",
    )
    db.add(row)
    db.commit()
    return row


def test_root_is_fenced_until_all_authoritative_steps_are_complete(db, monkeypatch):
    monkeypatch.setattr(security_module, "commissioning_required", commissioning_required)
    root = create_test_user(
        db,
        username="commissioning.root",
        display_name="Commissioning Root",
        is_root_admin=True,
        is_admin=True,
    )
    db.add(WebAuthnCredential(
        user_id=root.id,
        credential_id=b"commissioning-root-passkey",
        public_key=b"synthetic-public-key",
        sign_count=0,
    ))
    set_setting(db, "root_bootstrap_disabled", "true")
    db.commit()
    client = _make_client(db, root, reauth=True)

    status = client.get("/api/v1/setup/status")
    assert status.status_code == 200
    assert status.json()["current_step"] == "recovery"
    blocked = client.get("/api/v1/admin/events")
    assert blocked.status_code == 423
    assert blocked.json()["detail"] == {
        "code": "ROOT_COMMISSIONING_REQUIRED",
        "commissioning_stage": "recovery",
        "setup_url": "/setup",
        "message": "Complete root commissioning before using administration.",
    }

    recipient = "age1" + "q" * 58
    recovery = client.post("/api/v1/setup/recovery/complete", json={
        "recipient": recipient,
        "download_acknowledged": True,
        "local_reimport_verified": True,
    })
    assert recovery.status_code == 200, recovery.text
    assert recovery.json()["current_step"] == "controller"
    assert "AGE-SECRET-KEY" not in json.dumps(recovery.json())
    replay = client.post("/api/v1/setup/recovery/complete", json={
        "recipient": recipient,
        "download_acknowledged": True,
        "local_reimport_verified": True,
    })
    assert replay.status_code == 409

    controller = _controller_key(db)
    assert client.get("/api/v1/setup/status").json()["current_step"] == "governance"
    assert client.get("/api/v1/setup/report.zip").status_code == 409
    publication = _governance_v1(db, root.id)
    finalised = client.post("/api/v1/setup/finalise", json={})
    assert finalised.status_code == 200, finalised.text
    assert finalised.json()["commissioning"]["receipt_sha256"]
    retry = client.post("/api/v1/setup/finalise", json={})
    assert retry.status_code == 200
    assert retry.json()["commissioning"] == finalised.json()["commissioning"]

    complete = client.get("/api/v1/setup/status").json()
    assert complete["current_step"] == "complete"
    assert complete["can_enter_administration"] is True
    assert complete["controller"]["key_id"] == controller.key_id
    assert complete["governance"]["content_sha256"] == publication.content_sha256
    assert client.get("/api/v1/admin/events").status_code == 200

    report = client.get("/api/v1/setup/report.zip")
    assert report.status_code == 200
    with zipfile.ZipFile(io.BytesIO(report.content)) as archive:
        assert set(archive.namelist()) == {
            "commissioning-summary.json",
            "controller-public-key.json",
            "governance-v1.json",
            "commissioning-receipt.json",
            "verification.json",
        }
        combined = b"".join(archive.read(name) for name in archive.namelist())
    assert b"PRIVATE KEY" not in combined
    assert b"AGE-SECRET-KEY" not in combined

    set_setting(db, "root_commissioning_receipt_sha256", "not-a-receipt")
    db.commit()
    assert commissioning_stage(db) == "governance"
