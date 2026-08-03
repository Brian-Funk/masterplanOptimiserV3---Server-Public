"""End-to-end tests for the signed host/backend clean-backup bridge."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from app.core import compliance_receipts
from app.core.evidence import EvidenceUnavailable, public_key_id
from app.models.evidence import EvidenceKey


def test_missing_host_receipt_is_reported_as_pending(db, monkeypatch, tmp_path):
    """A queued job normally has no receipt until the TUI finishes its work."""
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    monkeypatch.setattr(
        compliance_receipts.settings, "COMPLIANCE_RECEIPT_DIR", str(receipts)
    )

    with pytest.raises(EvidenceUnavailable, match="not available yet"):
        compliance_receipts.verified_clean_backup_receipt(
            db, job_id=str(uuid.uuid4()), expected={}
        )


def test_non_file_host_receipt_path_remains_unsafe(db, monkeypatch, tmp_path):
    """Normal pending state must not weaken rejection of substituted paths."""
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    job_id = str(uuid.uuid4())
    (receipts / f"{job_id}.json").mkdir()
    (receipts / f"{job_id}.json.sig").write_text("signature", encoding="utf-8")
    monkeypatch.setattr(
        compliance_receipts.settings, "COMPLIANCE_RECEIPT_DIR", str(receipts)
    )

    with pytest.raises(EvidenceUnavailable, match="path is unsafe"):
        compliance_receipts.verified_clean_backup_receipt(
            db, job_id=job_id, expected={}
        )


def test_pending_clean_backup_request_can_be_cancelled_before_a_receipt(monkeypatch, tmp_path):
    requests = tmp_path / "requests"
    receipts = tmp_path / "receipts"
    requests.mkdir()
    receipts.mkdir()
    job_id = str(uuid.uuid4())
    request_path = requests / f"{job_id}.json"
    request_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(compliance_receipts.settings, "COMPLIANCE_REQUEST_DIR", str(requests))
    monkeypatch.setattr(compliance_receipts.settings, "COMPLIANCE_RECEIPT_DIR", str(receipts))

    compliance_receipts.cancel_pending_clean_backup_request(job_id=job_id)
    assert not request_path.exists()

    (receipts / f"{job_id}.json").write_text("{}", encoding="utf-8")
    with pytest.raises(EvidenceUnavailable, match="already produced"):
        compliance_receipts.cancel_pending_clean_backup_request(job_id=job_id)


def test_host_receipt_is_signed_scoped_and_tamper_evident(db, monkeypatch, tmp_path):
    requests = tmp_path / "requests"
    receipts = tmp_path / "receipts"
    snapshots = tmp_path / "snapshot"
    requests.mkdir()
    receipts.mkdir()
    snapshots.mkdir()
    monkeypatch.setattr(compliance_receipts.settings, "COMPLIANCE_REQUEST_DIR", str(requests))
    monkeypatch.setattr(compliance_receipts.settings, "COMPLIANCE_RECEIPT_DIR", str(receipts))

    ids = {name: str(uuid.uuid4()) for name in (
        "job_id", "instance_id", "workflow_id", "event_ref", "subject_ref",
        "privacy_action_id", "package_id",
    )}
    key = tmp_path / "instance-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
    )
    key.chmod(0o640)
    public = " ".join((tmp_path / "instance-key.pub").read_text().split()[:2])
    db.add(EvidenceKey(
        key_id=public_key_id(public),
        public_key=public,
        public_key_sha256=hashlib.sha256(public.encode("ascii")).hexdigest(),
        instance_id=ids["instance_id"],
        role="instance",
    ))
    db.commit()

    purged_at = datetime.now(timezone.utc) - timedelta(minutes=4)
    created_at = purged_at + timedelta(minutes=1)
    verified_at = created_at + timedelta(minutes=1)
    confirmed_at = verified_at + timedelta(minutes=1)
    purge_digest = "a" * 64
    compliance_receipts.queue_clean_backup_request(
        job_id=ids["job_id"],
        instance_id=ids["instance_id"],
        workflow_type="deletion_case",
        workflow_id=ids["workflow_id"],
        event_ref=ids["event_ref"],
        subject_ref=ids["subject_ref"],
        privacy_action_id=ids["privacy_action_id"],
        privacy_action_sequence=7,
        live_purge_receipt_sha256=purge_digest,
        live_data_purged_at=purged_at,
    )
    snapshot_receipt = {
        "format": "mp-opt-snapshot-receipt-v2",
        "type": "full",
        "created_at": created_at.isoformat(),
        "verification": "deep-verified",
        "verified_at": verified_at.isoformat(),
        "evidence": {"head_sha256": "b" * 64},
        "storage": {"portable": {
            "state": "operator-sha256-confirmed",
            "package_id": ids["package_id"],
            "package_sha256": "c" * 64,
            "package_size": 1234,
            "archive_sha256": "d" * 64,
            "recovery_key_id": "rk-" + "e" * 16,
            "confirmed_at": confirmed_at.isoformat(),
        }},
    }
    snapshot_path = snapshots / "receipt.json"
    snapshot_path.write_text(json.dumps(snapshot_receipt), encoding="utf-8")
    subprocess.run(
        [
            sys.executable, "deploy/management/compliance_receipts.py",
            "--requests", str(requests),
            "--receipts", str(receipts),
            "--snapshot-receipt", str(snapshot_path),
            "--instance-key", str(key),
        ],
        check=True,
    )
    assert not (requests / f"{ids['job_id']}.json").exists()

    expected = {
        "job_id": ids["job_id"],
        "instance_id": ids["instance_id"],
        "workflow_type": "deletion_case",
        "workflow_id": ids["workflow_id"],
        "event_ref": ids["event_ref"],
        "subject_ref": ids["subject_ref"],
        "privacy_action_id": ids["privacy_action_id"],
        "privacy_action_sequence": 7,
        "live_purge_receipt_sha256": purge_digest,
        "live_data_purged_at": purged_at.isoformat(),
    }
    verified = compliance_receipts.verified_clean_backup_receipt(
        db, job_id=ids["job_id"], expected=expected,
    )
    assert verified["package_id"] == ids["package_id"]
    assert len(verified["receipt_sha256"]) == 64

    receipt_path = receipts / f"{ids['job_id']}.json"
    receipt_path.write_bytes(receipt_path.read_bytes().replace(b'"package_size":1234', b'"package_size":1235'))
    with pytest.raises(EvidenceUnavailable, match="signature is invalid"):
        compliance_receipts.verified_clean_backup_receipt(
            db, job_id=ids["job_id"], expected=expected,
        )
