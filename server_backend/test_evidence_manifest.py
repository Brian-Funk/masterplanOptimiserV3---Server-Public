"""Tests for canonical, signed, hash-chained accountability evidence."""

import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from deploy.evidence.evidence_manifest import (
    EvidenceError,
    append_record,
    canonical_json,
    key_id,
    load_json_bytes,
    verify_chain,
)


def _keypair(tmp_path):
    private_key = tmp_path / "instance-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "ignored-comment", "-f", str(private_key)],
        check=True,
    )
    return private_key, private_key.with_suffix(".pub")


def test_canonical_json_rejects_duplicates_floats_and_control_characters():
    with pytest.raises(EvidenceError, match="duplicate"):
        load_json_bytes(b'{"format":"one","format":"two"}\n')
    with pytest.raises(EvidenceError, match="floating-point"):
        load_json_bytes(b'{"value":1.2}\n')
    with pytest.raises(EvidenceError, match="control"):
        canonical_json({"value": "line\nbreak"})


def test_key_id_ignores_public_key_comment(tmp_path):
    _private, public = _keypair(tmp_path)
    raw = public.read_text(encoding="ascii")

    assert key_id(raw) == key_id(" ".join(raw.split()[:2]) + " another-comment")


def test_signed_chain_round_trip_and_tamper_detection(tmp_path):
    private, public = _keypair(tmp_path)
    ledger = tmp_path / "ledger"
    instance_id = str(uuid.uuid4())
    chain_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    event_ref = str(uuid.uuid4())
    subject_ref = str(uuid.uuid4())

    first = append_record(
        ledger,
        instance_id=instance_id,
        chain_id=chain_id,
        record_type="instance.initialised",
        payload={},
        private_key=private,
        public_key=public,
    )
    append_record(
        ledger,
        instance_id=instance_id,
        chain_id=chain_id,
        record_type="data_subject.deletion.requested",
        payload={
            "request_id": request_id,
            "event_ref": event_ref,
            "subject_ref": subject_ref,
            "request_type": "full_erasure",
            "identity_verification": "recent_passkey_reauthentication",
            "status": "submitted",
        },
        private_key=private,
        public_key=public,
    )

    result = verify_chain(ledger, public)
    assert result["valid"] is True
    assert result["records"] == 2
    if os.name != "nt":
        assert stat_mode(first) == 0o600
        assert stat_mode(first.with_suffix(first.suffix + ".sig")) == 0o600

    record = json.loads(first.read_text(encoding="utf-8"))
    record["record_type"] = "key.rotated"
    first.write_bytes(canonical_json(record))
    with pytest.raises(EvidenceError, match="signature"):
        verify_chain(ledger, public)


def test_payload_rejects_unknown_or_free_text_fields(tmp_path):
    private, public = _keypair(tmp_path)
    common = {
        "ledger": tmp_path / "ledger",
        "instance_id": str(uuid.uuid4()),
        "chain_id": str(uuid.uuid4()),
        "record_type": "deletion.completed",
        "private_key": private,
        "public_key": public,
    }
    with pytest.raises(EvidenceError, match="unknown"):
        append_record(payload={"person_name": "Alice"}, **common)
    with pytest.raises(EvidenceError, match="enumerated"):
        append_record(payload={"error_code": "Alice said this failed"}, **common)


def test_current_deletion_records_are_signed_and_attestation_cli_is_absent(tmp_path):
    private, public = _keypair(tmp_path)
    tool = Path(__file__).resolve().parents[1] / "deploy" / "evidence" / "evidence_manifest.py"
    ledger = tmp_path / "ledger"
    instance_id = str(uuid.uuid4())
    chain_id = str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    work_order_id = str(uuid.uuid4())
    event_ref = str(uuid.uuid4())
    subject_ref = str(uuid.uuid4())

    append_record(
        ledger,
        instance_id=instance_id,
        chain_id=chain_id,
        record_type="deletion.desktop_report_received",
        payload={
            "case_id": case_id,
            "work_order_id": work_order_id,
            "event_ref": event_ref,
            "subject_ref": subject_ref,
            "report_sha256": "a" * 64,
            "outstanding_actions": [],
            "status": "ready_for_live_purge",
        },
        private_key=private,
        public_key=public,
    )
    append_record(
        ledger,
        instance_id=instance_id,
        chain_id=chain_id,
        record_type="deletion.checklist_approved",
        payload={
            "case_id": case_id,
            "checklist_sha256": "b" * 64,
            "role": "controller",
            "approval_sha256": "c" * 64,
            "status": "ready_for_completion",
        },
        private_key=private,
        public_key=public,
    )
    assert verify_chain(ledger, public)["records"] == 2

    result = subprocess.run(
        [sys.executable, str(tool), "validate-attestation"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_clean_backup_evidence_accepts_bounded_snapshot_and_portable_inventory(tmp_path):
    private, public = _keypair(tmp_path)
    ledger = tmp_path / "ledger"
    instance_id = str(uuid.uuid4())
    chain_id = str(uuid.uuid4())
    case_id = str(uuid.uuid4())
    event_ref = str(uuid.uuid4())
    replacement_package_id = str(uuid.uuid4())
    superseded_package_id = str(uuid.uuid4())
    payload = {
        "case_id": case_id,
        "event_ref": event_ref,
        "replacement_package_id": replacement_package_id,
        "replacement_package_sha256": "a" * 64,
        "receipt_sha256": "b" * 64,
        "local_snapshot_count": 1,
        "superseded_portable_package_ids": [superseded_package_id],
        "verified_at": "2026-08-05T15:00:00Z",
        "outcome": "verified",
        "status": "clean_backup_verified",
    }

    record = append_record(
        ledger,
        instance_id=instance_id,
        chain_id=chain_id,
        record_type="deletion.clean_backup_verified",
        payload=payload,
        private_key=private,
        public_key=public,
    )

    assert record.is_file()
    assert verify_chain(ledger, public)["records"] == 1

    with pytest.raises(EvidenceError, match="exactly one"):
        append_record(
            ledger,
            instance_id=instance_id,
            chain_id=chain_id,
            record_type="deletion.clean_backup_verified",
            payload={**payload, "local_snapshot_count": 2},
            private_key=private,
            public_key=public,
        )

    with pytest.raises(EvidenceError, match="UUID"):
        append_record(
            ledger,
            instance_id=instance_id,
            chain_id=chain_id,
            record_type="deletion.clean_backup_verified",
            payload={**payload, "superseded_portable_package_ids": ["not-a-package-id"]},
            private_key=private,
            public_key=public,
        )


def test_archive_completion_receipt_is_bounded_and_signed(tmp_path):
    private, public = _keypair(tmp_path)
    ledger = tmp_path / "ledger"
    record = append_record(
        ledger,
        instance_id=str(uuid.uuid4()),
        chain_id=str(uuid.uuid4()),
        record_type="evidence.git_archive_completed",
        payload={
            "submission_id": "sub-" + "a" * 32,
            "archive_repository_id": "42",
            "controller_id": "ctl-controller000001",
            "bundle_id": str(uuid.uuid4()),
            "bundle_sha256": "b" * 64,
            "chain_head_sha256": "c" * 64,
            "pull_request_number": 7,
            "pull_request_head_sha": "d" * 40,
            "merge_commit_sha": "e" * 40,
            "completed_at": "2026-07-31T12:00:00Z",
            "archive_status": "verified",
        },
        private_key=private,
        public_key=public,
    )
    assert record.is_file()
    assert verify_chain(ledger, public)["records"] == 1
    with pytest.raises(EvidenceError, match="Git object digest"):
        append_record(
            ledger,
            instance_id=json.loads(record.read_text(encoding="utf-8"))["instance_id"],
            chain_id=json.loads(record.read_text(encoding="utf-8"))["chain_id"],
            record_type="evidence.git_archive_completed",
            payload={"merge_commit_sha": "not-a-sha"},
            private_key=private,
            public_key=public,
        )


def stat_mode(path):
    return os.stat(path).st_mode & 0o777
