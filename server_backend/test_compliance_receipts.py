"""End-to-end tests for the signed host/backend clean-backup bridge."""

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from app.core import compliance_receipts
from app.core.evidence import EvidenceUnavailable, public_key_id
from app.models.evidence import EvidenceKey


HOST_TOOL_PATH = Path("deploy/management/compliance_receipts.py")
HOST_TOOL_SPEC = importlib.util.spec_from_file_location("host_compliance_receipts", HOST_TOOL_PATH)
assert HOST_TOOL_SPEC and HOST_TOOL_SPEC.loader
host_compliance_receipts = importlib.util.module_from_spec(HOST_TOOL_SPEC)
HOST_TOOL_SPEC.loader.exec_module(host_compliance_receipts)
PEER_TOOL_PATH = Path("deploy/management/peer_snapshot_resolution.py")
PEER_TOOL_SPEC = importlib.util.spec_from_file_location("host_peer_snapshot_resolution", PEER_TOOL_PATH)
assert PEER_TOOL_SPEC and PEER_TOOL_SPEC.loader
host_peer_snapshot_resolution = importlib.util.module_from_spec(PEER_TOOL_SPEC)
PEER_TOOL_SPEC.loader.exec_module(host_peer_snapshot_resolution)


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


def test_host_inventory_rejects_substituted_snapshot_paths(tmp_path):
    snapshots = tmp_path / "snapshots"
    selected = snapshots / "20260807T084531Z_full_selected"
    outside = tmp_path / "outside"
    selected.mkdir(parents=True)
    outside.mkdir()
    receipt = {
        "format": "mp-opt-snapshot-receipt-v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "archive_sha256": "a" * 64,
    }
    selected_receipt = selected / "receipt.json"
    selected_receipt.write_text(json.dumps(receipt), encoding="utf-8")
    substituted = snapshots / "20260807T081311Z_full_substituted"
    try:
        substituted.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this test host")

    with pytest.raises(ValueError, match="unsafe entry"):
        host_compliance_receipts.snapshot_inventory(snapshots, selected_receipt)


def test_peer_resolution_removes_only_pre_purge_snapshots(tmp_path):
    snapshots = tmp_path / "snapshots"
    journals = tmp_path / "journals"
    snapshots.mkdir()
    purged_at = datetime.now(timezone.utc) - timedelta(hours=1)
    receipt = {
        "job_id": str(uuid.uuid4()),
        "package_id": str(uuid.uuid4()),
        "live_data_purged_at": purged_at.isoformat(),
    }
    hashes = {}
    for name, created_at, archive in (
        ("20260807T081311Z_full_old", purged_at - timedelta(minutes=1), "1" * 64),
        ("20260807T084700Z_full_new", purged_at + timedelta(minutes=1), "2" * 64),
    ):
        directory = snapshots / name
        directory.mkdir()
        receipt_path = directory / "receipt.json"
        receipt_path.write_text(json.dumps({
            "format": "mp-opt-snapshot-receipt-v2",
            "created_at": created_at.isoformat(),
            "archive_sha256": archive,
        }), encoding="utf-8")
        hashes[name] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    inventory = host_compliance_receipts.snapshot_inventory(snapshots, None)
    clean_sha = "3" * 64
    journal_path = journals / f"{receipt['job_id']}.json"
    journal = host_peer_snapshot_resolution._prepare_journal(
        journal_path, receipt, "node-b", "node-a", inventory, clean_sha,
    )
    resolved, retained = host_peer_snapshot_resolution._resolve(
        snapshots, journal_path, journal,
    )
    assert resolved["state"] == "resolved"
    assert retained == 1
    assert not (snapshots / "20260807T081311Z_full_old").exists()
    assert (snapshots / "20260807T084700Z_full_new").is_dir()
    assert resolved["candidates"][0]["receipt_sha256"] == hashes["20260807T081311Z_full_old"]


def test_backend_verifies_peer_snapshot_resolution_receipt(db, monkeypatch, tmp_path):
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    monkeypatch.setattr(compliance_receipts.settings, "COMPLIANCE_RECEIPT_DIR", str(receipts))
    monkeypatch.setattr(compliance_receipts.settings, "HA_NODE_ID", "node-b")
    monkeypatch.setattr(compliance_receipts.settings, "HA_PEER_NODE_ID", "node-a")
    ids = {name: str(uuid.uuid4()) for name in (
        "resolution_id", "job_id", "instance_id", "workflow_id", "event_ref",
        "privacy_action_id", "package_id",
    )}
    key = tmp_path / "instance-key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    public = " ".join((tmp_path / "instance-key.pub").read_text().split()[:2])
    db.add(EvidenceKey(
        key_id=public_key_id(public), public_key=public,
        public_key_sha256=hashlib.sha256(public.encode("ascii")).hexdigest(),
        instance_id=ids["instance_id"], role="instance",
    ))
    db.commit()
    now = datetime.now(timezone.utc)
    document = {
        "format": "mp-opt-peer-snapshot-resolution-v1",
        "resolution_id": ids["resolution_id"],
        "job_id": ids["job_id"],
        "instance_id": ids["instance_id"],
        "workflow_type": "deletion_case",
        "workflow_id": ids["workflow_id"],
        "event_ref": ids["event_ref"],
        "subject_ref": None,
        "privacy_action_id": ids["privacy_action_id"],
        "privacy_action_sequence": 3,
        "live_purge_receipt_sha256": "4" * 64,
        "live_data_purged_at": (now - timedelta(minutes=2)).isoformat(),
        "clean_receipt_sha256": "5" * 64,
        "replacement_package_id": ids["package_id"],
        "replacement_package_sha256": "6" * 64,
        "source_node_id": "node-b",
        "target_node_id": "node-a",
        "resolved_at": now.isoformat(),
        "superseded_local_snapshot_receipt_sha256s": ["7" * 64],
        "retained_local_snapshot_count": 0,
        "resolution_sha256": "8" * 64,
    }
    target = receipts / f"{ids['job_id']}.peer.json"
    target.write_bytes(host_compliance_receipts.canonical(document))
    host_compliance_receipts.sign_receipt(target, key)
    expected = {
        key: document[key] for key in (
            "job_id", "instance_id", "workflow_type", "workflow_id", "event_ref",
            "subject_ref", "privacy_action_id", "privacy_action_sequence",
            "live_purge_receipt_sha256", "live_data_purged_at", "clean_receipt_sha256",
            "replacement_package_id", "replacement_package_sha256",
        )
    }
    verified = compliance_receipts.verified_peer_snapshot_resolution_receipt(
        db, job_id=ids["job_id"], expected=expected,
    )
    assert verified["receipt_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    document["target_node_id"] = "node-c"
    target.write_bytes(host_compliance_receipts.canonical(document))
    with pytest.raises(EvidenceUnavailable, match="signature is invalid"):
        compliance_receipts.verified_peer_snapshot_resolution_receipt(
            db, job_id=ids["job_id"], expected=expected,
        )


def test_host_receipt_is_signed_scoped_and_tamper_evident(db, monkeypatch, tmp_path):
    requests = tmp_path / "requests"
    receipts = tmp_path / "receipts"
    snapshots = tmp_path / "snapshots"
    selected = snapshots / "20260807T084531Z_full_selected"
    portable_inventory = tmp_path / "portable-inventory"
    resolution_journals = tmp_path / "resolution-journals"
    requests.mkdir()
    receipts.mkdir()
    selected.mkdir(parents=True)
    portable_inventory.mkdir()
    monkeypatch.setattr(compliance_receipts.settings, "COMPLIANCE_REQUEST_DIR", str(requests))
    monkeypatch.setattr(compliance_receipts.settings, "COMPLIANCE_RECEIPT_DIR", str(receipts))

    ids = {name: str(uuid.uuid4()) for name in (
        "job_id", "job_id_2", "job_id_3", "instance_id", "workflow_id",
        "workflow_id_2", "workflow_id_3", "event_ref", "subject_ref",
        "privacy_action_id", "privacy_action_id_2", "privacy_action_id_3",
        "package_id", "old_package_id",
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
    compliance_receipts.queue_clean_backup_request(
        job_id=ids["job_id_2"],
        instance_id=ids["instance_id"],
        workflow_type="deletion_case",
        workflow_id=ids["workflow_id_2"],
        event_ref=ids["event_ref"],
        subject_ref=None,
        privacy_action_id=ids["privacy_action_id_2"],
        privacy_action_sequence=8,
        live_purge_receipt_sha256="f" * 64,
        live_data_purged_at=purged_at + timedelta(seconds=30),
    )
    snapshot_receipt = {
        "format": "mp-opt-snapshot-receipt-v2",
        "type": "full",
        "archive_sha256": "d" * 64,
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
    snapshot_path = selected / "receipt.json"
    snapshot_path.write_text(json.dumps(snapshot_receipt), encoding="utf-8")
    compliance_receipts.queue_clean_backup_request(
        job_id=ids["job_id_3"],
        instance_id=ids["instance_id"],
        workflow_type="deletion_case",
        workflow_id=ids["workflow_id_3"],
        event_ref=ids["event_ref"],
        subject_ref=None,
        privacy_action_id=ids["privacy_action_id_3"],
        privacy_action_sequence=9,
        live_purge_receipt_sha256="9" * 64,
        live_data_purged_at=created_at + timedelta(seconds=30),
    )
    old_created_at = purged_at - timedelta(days=1)
    old_confirmed_at = old_created_at + timedelta(minutes=1)
    (portable_inventory / f"{ids['old_package_id']}.json").write_text(json.dumps({
        "format": "mp-opt-portable-export-inventory-v1",
        "state": "operator-sha256-confirmed",
        "snapshot": "old-full-snapshot",
        "snapshot_created_at": old_created_at.isoformat(),
        "confirmed_at": old_confirmed_at.isoformat(),
        "package_id": ids["old_package_id"],
        "package_sha256": "1" * 64,
        "package_size": 5678,
        "archive_sha256": "2" * 64,
        "recovery_key_id": "rk-" + "3" * 16,
    }), encoding="utf-8")
    stale = snapshots / "20260807T081311Z_full_stale"
    stale.mkdir()
    stale_receipt = {
        **snapshot_receipt,
        "created_at": old_created_at.isoformat(),
        "verified_at": old_confirmed_at.isoformat(),
        "archive_sha256": "4" * 64,
    }
    stale_receipt_path = stale / "receipt.json"
    stale_receipt_path.write_text(json.dumps(stale_receipt), encoding="utf-8")
    stale_receipt_sha256 = hashlib.sha256(stale_receipt_path.read_bytes()).hexdigest()
    later = snapshots / "20260807T084700Z_database_scheduled"
    later.mkdir()
    later_receipt = {
        **snapshot_receipt,
        "type": "database",
        "created_at": (created_at + timedelta(minutes=2)).isoformat(),
        "verified_at": (verified_at + timedelta(minutes=2)).isoformat(),
        "archive_sha256": "5" * 64,
    }
    (later / "receipt.json").write_text(json.dumps(later_receipt), encoding="utf-8")
    invalid_key = tmp_path / "invalid-key"
    invalid_key.write_text("not a signing key", encoding="utf-8")
    invalid_key.chmod(0o640)
    interrupted = subprocess.run(
        [
            sys.executable, "deploy/management/compliance_receipts.py",
            "--requests", str(requests),
            "--receipts", str(receipts),
            "--snapshots", str(snapshots),
            "--portable-inventory", str(portable_inventory),
            "--resolution-journals", str(resolution_journals),
            "--snapshot-receipt", str(snapshot_path),
            "--instance-key", str(invalid_key),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert interrupted.returncode == 1
    assert "Compliance receipt signing failed" in interrupted.stderr
    assert (requests / f"{ids['job_id']}.json").exists()
    assert (requests / f"{ids['job_id_2']}.json").exists()
    assert not stale.exists()
    assert selected.exists()
    assert later.exists()
    journal = json.loads((resolution_journals / f"{ids['job_id']}.json").read_text())
    assert journal["state"] == "resolved"
    assert journal["candidates"][0]["receipt_sha256"] == stale_receipt_sha256
    second_journal = json.loads(
        (resolution_journals / f"{ids['job_id_2']}.json").read_text()
    )
    assert second_journal["state"] == "resolved"
    assert second_journal["candidates"][0]["receipt_sha256"] == stale_receipt_sha256
    subprocess.run(
        [
            sys.executable, "deploy/management/compliance_receipts.py",
            "--requests", str(requests),
            "--receipts", str(receipts),
            "--snapshots", str(snapshots),
            "--portable-inventory", str(portable_inventory),
            "--resolution-journals", str(resolution_journals),
            "--snapshot-receipt", str(snapshot_path),
            "--instance-key", str(key),
        ],
        check=True,
    )
    assert not (requests / f"{ids['job_id']}.json").exists()
    assert not (requests / f"{ids['job_id_2']}.json").exists()
    assert (requests / f"{ids['job_id_3']}.json").exists()
    assert not (resolution_journals / f"{ids['job_id_3']}.json").exists()
    assert not (receipts / f"{ids['job_id_3']}.json").exists()
    host_receipt = json.loads((receipts / f"{ids['job_id']}.json").read_text())
    assert host_receipt["format"] == "mp-opt-clean-backup-receipt-v4"
    assert host_receipt["retained_local_snapshot_count"] == 2
    assert host_receipt["superseded_local_snapshot_receipt_sha256s"] == [
        stale_receipt_sha256
    ]
    assert len(host_receipt["local_resolution_sha256"]) == 64
    second_host_receipt = json.loads(
        (receipts / f"{ids['job_id_2']}.json").read_text()
    )
    assert second_host_receipt["retained_local_snapshot_count"] == 2
    assert second_host_receipt["superseded_local_snapshot_receipt_sha256s"] == [
        stale_receipt_sha256
    ]
    assert [item["package_id"] for item in host_receipt["superseded_portable_packages"]] == [
        ids["old_package_id"]
    ]

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

    legacy_job_id = str(uuid.uuid4())
    legacy_receipt = {
        field: value
        for field, value in host_receipt.items()
        if field not in {
            "local_resolution_id", "local_resolution_sha256",
            "superseded_local_snapshot_receipt_sha256s",
            "retained_local_snapshot_count",
        }
    }
    legacy_receipt.update({
        "format": "mp-opt-clean-backup-receipt-v3",
        "job_id": legacy_job_id,
        "receipt_id": str(uuid.uuid5(uuid.UUID(legacy_job_id), ids["package_id"])),
        "local_snapshot_count": 1,
    })
    legacy_path = receipts / f"{legacy_job_id}.json"
    legacy_path.write_bytes(host_compliance_receipts.canonical(legacy_receipt))
    host_compliance_receipts.sign_receipt(legacy_path, key)
    legacy_expected = {**expected, "job_id": legacy_job_id}
    legacy_verified = compliance_receipts.verified_clean_backup_receipt(
        db, job_id=legacy_job_id, expected=legacy_expected,
    )
    assert legacy_verified["retained_local_snapshot_count"] == 1
    assert legacy_verified["local_resolution_sha256"] is None

    receipt_path = receipts / f"{ids['job_id']}.json"
    receipt_path.write_bytes(receipt_path.read_bytes().replace(b'"package_size":1234', b'"package_size":1235'))
    with pytest.raises(EvidenceUnavailable, match="signature is invalid"):
        compliance_receipts.verified_clean_backup_receipt(
            db, job_id=ids["job_id"], expected=expected,
        )


def test_peer_resolution_accepts_bounded_long_running_ha_control(tmp_path):
    control_path = tmp_path / "ha-control.json"
    control_path.write_text(
        json.dumps({"holder_node_id": "node-a", "history": ["x" * 1024] * 20}),
        encoding="utf-8",
    )

    control = host_peer_snapshot_resolution.load_regular(
        control_path, host_peer_snapshot_resolution.MAX_HA_CONTROL_BYTES,
    )

    assert control["holder_node_id"] == "node-a"
    assert control_path.stat().st_size > 16 * 1024
