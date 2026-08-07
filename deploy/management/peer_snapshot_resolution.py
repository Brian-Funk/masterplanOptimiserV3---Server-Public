#!/usr/bin/env python3
"""Resolve HA-peer snapshots covered by a signed clean-backup receipt."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

_COMPLIANCE_SPEC = importlib.util.spec_from_file_location(
    "mp_opt_host_compliance_receipts", Path(__file__).with_name("compliance_receipts.py")
)
if _COMPLIANCE_SPEC is None or _COMPLIANCE_SPEC.loader is None:
    raise RuntimeError("The host compliance module is unavailable")
_COMPLIANCE = importlib.util.module_from_spec(_COMPLIANCE_SPEC)
_COMPLIANCE_SPEC.loader.exec_module(_COMPLIANCE)
SHA256 = _COMPLIANCE.SHA256
SNAPSHOT_NAME = _COMPLIANCE.SNAPSHOT_NAME
atomic_write = _COMPLIANCE.atomic_write
canonical = _COMPLIANCE.canonical
fsync_directory = _COMPLIANCE.fsync_directory
load_regular = _COMPLIANCE.load_regular
private_atomic_write = _COMPLIANCE.private_atomic_write
sign_receipt = _COMPLIANCE.sign_receipt
snapshot_inventory = _COMPLIANCE.snapshot_inventory
timestamp = _COMPLIANCE.timestamp
validate_snapshot_tree = _COMPLIANCE.validate_snapshot_tree


NODE_ID = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
REQUEST_FORMAT = "mp-opt-peer-snapshot-resolution-request-v1"
RECEIPT_FORMAT = "mp-opt-peer-snapshot-resolution-v1"
MAX_HA_CONTROL_BYTES = 256 * 1024
REQUEST_FIELDS = {
    "format", "source_node_id", "target_node_id", "clean_receipt", "clean_signature",
}
RECEIPT_FIELDS = {
    "format", "resolution_id", "job_id", "instance_id", "workflow_type", "workflow_id", "event_ref",
    "subject_ref", "privacy_action_id", "privacy_action_sequence",
    "live_purge_receipt_sha256", "live_data_purged_at", "clean_receipt_sha256",
    "replacement_package_id", "replacement_package_sha256", "source_node_id",
    "target_node_id", "resolved_at", "superseded_local_snapshot_receipt_sha256s",
    "retained_local_snapshot_count", "resolution_sha256",
}
JOURNAL_FIELDS = {
    "format", "state", "resolution_id", "job_id", "source_node_id", "target_node_id",
    "clean_receipt_sha256", "replacement_package_id", "live_data_purged_at",
    "prepared_at", "resolved_at", "candidates",
}
CANDIDATE_FIELDS = {
    "snapshot", "receipt_sha256", "archive_sha256", "created_at", "tombstone",
}


def _uuid(value: object, label: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except ValueError as exc:
        raise ValueError(f"Peer resolution {label} is invalid") from exc
    if str(parsed) != value:
        raise ValueError(f"Peer resolution {label} is invalid")
    return str(parsed)


def _node(value: object, label: str) -> str:
    if not isinstance(value, str) or not NODE_ID.fullmatch(value):
        raise ValueError(f"Peer resolution {label} is invalid")
    return value


def _read_node_config(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("HA node configuration is unavailable")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _verify_signature(content: bytes, signature: str, public_key_path: Path) -> None:
    if public_key_path.is_symlink() or not public_key_path.is_file():
        raise ValueError("The instance evidence public key is unavailable")
    public_key = " ".join(public_key_path.read_text(encoding="utf-8").split()[:2])
    if not public_key.startswith("ssh-ed25519 "):
        raise ValueError("The instance evidence public key is invalid")
    with tempfile.TemporaryDirectory(prefix="mp-opt-peer-resolution-") as temporary_name:
        temporary = Path(temporary_name)
        allowed = temporary / "allowed_signers"
        sig = temporary / "receipt.sig"
        allowed.write_text(f"mp-opt-instance {public_key}\n", encoding="utf-8")
        sig.write_text(signature, encoding="utf-8")
        result = subprocess.run(
            [
                "ssh-keygen", "-Y", "verify", "-f", str(allowed),
                "-I", "mp-opt-instance", "-n", "mp-opt-evidence-v1", "-s", str(sig),
            ],
            input=content,
            check=False,
            capture_output=True,
        )
    if result.returncode != 0:
        raise ValueError("The clean-backup receipt signature is invalid")


def _validate_clean_receipt(value: object) -> dict:
    if not isinstance(value, dict) or value.get("format") != "mp-opt-clean-backup-receipt-v4":
        raise ValueError("The clean-backup receipt format is invalid")
    for field in (
        "job_id", "instance_id", "workflow_id", "event_ref", "privacy_action_id",
        "package_id", "local_resolution_id",
    ):
        _uuid(value.get(field), field)
    if value.get("subject_ref") is not None:
        _uuid(value.get("subject_ref"), "subject_ref")
    if value.get("workflow_type") != "deletion_case":
        raise ValueError("The clean-backup workflow is invalid")
    sequence = value.get("privacy_action_sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise ValueError("The clean-backup privacy sequence is invalid")
    for field in (
        "live_purge_receipt_sha256", "package_sha256", "archive_sha256",
        "snapshot_evidence_head_sha256", "local_resolution_sha256",
    ):
        if not SHA256.fullmatch(str(value.get(field, ""))):
            raise ValueError("The clean-backup receipt contains an invalid digest")
    timestamp(value.get("live_data_purged_at"))
    timestamp(value.get("snapshot_created_at"))
    timestamp(value.get("deep_verified_at"))
    timestamp(value.get("portable_confirmed_at"))
    return dict(value)


def _assert_peer_database(root: Path, receipt: dict) -> None:
    compose = ["docker", "compose", "--env-file", ".env"]
    if (root / ".release.env").is_file():
        compose.extend(["--env-file", ".release.env"])
    if (root / ".test-deployment.env").is_file():
        compose.extend(["--env-file", ".test-deployment.env"])
    compose.extend([
        "-f", "infra/docker-compose.yml", "-f", "infra/docker-compose.prod.yml",
        "-f", "infra/docker-compose.ha.yml", "exec", "-T", "db", "psql",
        "-U", "masterplan", "-d", "masterplan", "-At", "-v", "ON_ERROR_STOP=1",
        f"--set=workflow_id={receipt['workflow_id']}",
        f"--set=action_id={receipt['privacy_action_id']}",
        f"--set=action_sequence={receipt['privacy_action_sequence']}",
        f"--set=purge_digest={receipt['live_purge_receipt_sha256']}",
    ])
    sql = (
        "SELECT EXISTS (SELECT 1 FROM deletion_cases c JOIN privacy_action_receipts p "
        "ON p.privacy_action_id=c.privacy_action_id WHERE c.request_id=:'workflow_id' "
        "AND c.privacy_action_id=:'action_id' AND c.privacy_action_sequence="
        ":'action_sequence'::integer AND c.live_purge_receipt_sha256=:'purge_digest' "
        "AND c.live_data_purged_at IS NOT NULL AND p.local_applied_at IS NOT NULL);\n"
    )
    result = subprocess.run(
        compose, cwd=root, input=sql, text=True, check=False,
        capture_output=True, timeout=30,
    )
    if result.returncode != 0 or result.stdout.strip() != "t":
        raise ValueError("The peer database does not prove the privacy action")


def _prepare_journal(
    path: Path, receipt: dict, source_node_id: str, target_node_id: str,
    inventory: list[dict], clean_receipt_sha256: str,
) -> dict:
    resolution_id = str(uuid.uuid5(
        uuid.UUID(receipt["job_id"]),
        f"mp-opt-peer-snapshot-resolution-v1:{target_node_id}:{receipt['package_id']}",
    ))
    cutoff = timestamp(receipt["live_data_purged_at"])
    candidates = [
        {
            "snapshot": item["name"],
            "receipt_sha256": item["receipt_sha256"],
            "archive_sha256": item["archive_sha256"],
            "created_at": item["created_at"].isoformat(),
            "tombstone": f".compliance-delete-{item['receipt_sha256']}",
        }
        for item in inventory if item["created_at"] < cutoff
    ]
    if len(candidates) > 128:
        raise ValueError("Too many peer snapshots require resolution")
    if path.exists():
        journal = load_regular(path, 256 * 1024)
        expected = {
            "format": "mp-opt-peer-local-snapshot-resolution-v1",
            "resolution_id": resolution_id,
            "job_id": receipt["job_id"],
            "source_node_id": source_node_id,
            "target_node_id": target_node_id,
            "clean_receipt_sha256": clean_receipt_sha256,
            "replacement_package_id": receipt["package_id"],
            "live_data_purged_at": receipt["live_data_purged_at"],
        }
        if (
            set(journal) != JOURNAL_FIELDS
            or any(journal.get(key) != value for key, value in expected.items())
            or journal.get("state") not in {"prepared", "resolved"}
            or not isinstance(journal.get("candidates"), list)
        ):
            raise ValueError("The peer snapshot resolution journal is inconsistent")
        return journal
    journal = {
        "format": "mp-opt-peer-local-snapshot-resolution-v1",
        "state": "prepared",
        "resolution_id": resolution_id,
        "job_id": receipt["job_id"],
        "source_node_id": source_node_id,
        "target_node_id": target_node_id,
        "clean_receipt_sha256": clean_receipt_sha256,
        "replacement_package_id": receipt["package_id"],
        "live_data_purged_at": receipt["live_data_purged_at"],
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": None,
        "candidates": candidates,
    }
    private_atomic_write(path, journal)
    return journal


def _resolve(root: Path, journal_path: Path, journal: dict) -> tuple[dict, int]:
    root_resolved = root.resolve()
    cutoff = timestamp(journal["live_data_purged_at"])
    for candidate in journal["candidates"]:
        if not isinstance(candidate, dict) or set(candidate) != CANDIDATE_FIELDS:
            raise ValueError("The peer snapshot resolution journal is invalid")
        name = candidate.get("snapshot")
        receipt_sha256 = candidate.get("receipt_sha256")
        tombstone_name = candidate.get("tombstone")
        if (
            not isinstance(name, str) or not SNAPSHOT_NAME.fullmatch(name)
            or not SHA256.fullmatch(str(receipt_sha256 or ""))
            or not SHA256.fullmatch(str(candidate.get("archive_sha256", "")))
            or tombstone_name != f".compliance-delete-{receipt_sha256}"
            or timestamp(candidate.get("created_at")) >= cutoff
        ):
            raise ValueError("The peer snapshot resolution journal is invalid")
        source = root / name
        tombstone = root / str(tombstone_name)
        if source.exists():
            if source.parent.resolve() != root_resolved:
                raise ValueError("A peer snapshot path is unsafe")
            validate_snapshot_tree(source)
            if hashlib.sha256((source / "receipt.json").read_bytes()).hexdigest() != receipt_sha256:
                raise ValueError("A peer snapshot changed after resolution was prepared")
            if tombstone.exists():
                raise ValueError("A peer snapshot deletion tombstone is unsafe")
            os.replace(source, tombstone)
            fsync_directory(root)
        if tombstone.exists():
            validate_snapshot_tree(tombstone)
            shutil.rmtree(tombstone)
            fsync_directory(root)
    remaining = snapshot_inventory(root, None)
    if any(item["created_at"] < cutoff for item in remaining):
        raise ValueError("A pre-deletion peer snapshot remains")
    if journal["state"] != "resolved":
        journal = {**journal, "state": "resolved", "resolved_at": datetime.now(timezone.utc).isoformat()}
        private_atomic_write(journal_path, journal)
    return journal, len(remaining)


def receive(args: argparse.Namespace) -> None:
    request = json.load(os.sys.stdin)
    if not isinstance(request, dict) or set(request) != REQUEST_FIELDS or request.get("format") != REQUEST_FORMAT:
        raise ValueError("The peer resolution request is invalid")
    source_node_id = _node(request.get("source_node_id"), "source node")
    target_node_id = _node(request.get("target_node_id"), "target node")
    cfg = _read_node_config(Path(args.node_config))
    if (
        cfg.get("HA_MODE") != "ha" or cfg.get("HA_NODE_ID") != target_node_id
        or cfg.get("HA_PEER_NODE_ID") != source_node_id
    ):
        raise ValueError("The peer resolution request does not match this HA node")
    # Witness control includes bounded operational history and can legitimately
    # exceed 16 KiB during an extended HA campaign. Keep a strict limit aligned
    # with long-running witness state instead of rejecting a healthy pair.
    control = load_regular(Path(args.control), MAX_HA_CONTROL_BYTES)
    if control.get("holder_node_id") != source_node_id:
        raise ValueError("The peer resolution sender is not the current writer")
    receipt = _validate_clean_receipt(request.get("clean_receipt"))
    clean_raw = canonical(receipt)
    _verify_signature(clean_raw, str(request.get("clean_signature", "")), Path(args.instance_public_key))
    _assert_peer_database(Path(args.root), receipt)
    snapshots = Path(args.snapshots)
    inventory = snapshot_inventory(snapshots, None)
    clean_receipt_sha256 = hashlib.sha256(clean_raw).hexdigest()
    journals = Path(args.journals)
    journal_path = journals / f"{receipt['job_id']}.json"
    journal, retained_count = _resolve(
        snapshots,
        journal_path,
        _prepare_journal(
            journal_path, receipt, source_node_id, target_node_id,
            inventory, clean_receipt_sha256,
        ),
    )
    projection = {
        "format": "mp-opt-peer-local-snapshot-resolution-v1",
        "resolution_id": journal["resolution_id"],
        "job_id": receipt["job_id"],
        "source_node_id": source_node_id,
        "target_node_id": target_node_id,
        "clean_receipt_sha256": clean_receipt_sha256,
        "replacement_package_id": receipt["package_id"],
        "live_data_purged_at": receipt["live_data_purged_at"],
        "resolved_at": journal["resolved_at"],
        "superseded_local_snapshot_receipt_sha256s": [
            item["receipt_sha256"] for item in journal["candidates"]
        ],
        "retained_local_snapshot_count": retained_count,
    }
    resolution_receipt = {
        "format": RECEIPT_FORMAT,
        "resolution_id": journal["resolution_id"],
        **{key: receipt[key] for key in (
            "job_id", "instance_id", "workflow_type", "workflow_id", "event_ref", "subject_ref",
            "privacy_action_id", "privacy_action_sequence", "live_purge_receipt_sha256",
            "live_data_purged_at",
        )},
        "clean_receipt_sha256": clean_receipt_sha256,
        "replacement_package_id": receipt["package_id"],
        "replacement_package_sha256": receipt["package_sha256"],
        "source_node_id": source_node_id,
        "target_node_id": target_node_id,
        "resolved_at": journal["resolved_at"],
        "superseded_local_snapshot_receipt_sha256s": projection[
            "superseded_local_snapshot_receipt_sha256s"
        ],
        "retained_local_snapshot_count": retained_count,
        "resolution_sha256": hashlib.sha256(canonical(projection)).hexdigest(),
    }
    receipts = Path(args.receipts)
    target = receipts / f"{receipt['job_id']}.json"
    if not target.exists():
        atomic_write(target, canonical(resolution_receipt))
        sign_receipt(target, Path(args.instance_key))
        os.chmod(str(target) + ".sig", 0o644)
    else:
        existing = load_regular(target)
        if existing != resolution_receipt or not Path(str(target) + ".sig").is_file():
            raise ValueError("The peer resolution receipt is inconsistent")
    _verify_signature(
        canonical(resolution_receipt),
        Path(str(target) + ".sig").read_text(encoding="utf-8"),
        Path(args.instance_public_key),
    )
    response = {
        "format": "mp-opt-peer-snapshot-resolution-response-v1",
        "receipt": resolution_receipt,
        "signature": Path(str(target) + ".sig").read_text(encoding="utf-8"),
    }
    print(json.dumps(response, sort_keys=True, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["receive"])
    parser.add_argument("--root", default="/opt/masterplan")
    parser.add_argument("--snapshots", default=str(Path.home() / "masterplan-snapshots"))
    parser.add_argument("--journals", default=str(Path.home() / ".local/state/mp-opt/peer-compliance-resolutions"))
    parser.add_argument("--receipts", default=str(Path.home() / ".local/state/mp-opt/peer-compliance-receipts"))
    parser.add_argument("--instance-key", default="/opt/masterplan/secrets/evidence_signing_key")
    parser.add_argument("--instance-public-key", default="/opt/masterplan/secrets/evidence_signing_key.pub")
    parser.add_argument("--node-config", default="/etc/mp-opt-ha/node.env")
    parser.add_argument("--control", default="/opt/masterplan/runtime/ha-control.json")
    try:
        args = parser.parse_args()
        receive(args)
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
