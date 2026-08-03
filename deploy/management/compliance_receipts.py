#!/usr/bin/env python3
"""Create non-personal, instance-signed clean-backup bridge receipts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid


SHA256 = re.compile(r"^[0-9a-f]{64}$")
KEY_ID = re.compile(r"^rk-[0-9a-f]{16}$")
REQUEST_FIELDS = {
    "format", "job_id", "instance_id", "workflow_type", "workflow_id",
    "event_ref", "subject_ref", "privacy_action_id", "privacy_action_sequence",
    "live_purge_receipt_sha256", "live_data_purged_at", "created_at",
}


def timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Receipt timestamp is invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed > datetime.now(timezone.utc):
        raise ValueError("Receipt timestamp is invalid")
    return parsed.astimezone(timezone.utc)


def canonical(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def load_regular(path: Path, limit: int = 64 * 1024) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Compliance input is unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Compliance input must be an object")
    return value


def canonical_uuid(value: object) -> str:
    parsed = uuid.UUID(str(value))
    if str(parsed) != value:
        raise ValueError("Compliance identifier is invalid")
    return str(parsed)


def validate_request(value: dict) -> dict:
    if set(value) != REQUEST_FIELDS or value.get("format") != "mp-opt-clean-backup-request-v1":
        raise ValueError("Compliance request schema is invalid")
    for field in ("job_id", "instance_id", "workflow_id", "event_ref", "privacy_action_id"):
        canonical_uuid(value.get(field))
    if value.get("subject_ref") is not None:
        canonical_uuid(value.get("subject_ref"))
    if value.get("workflow_type") != "deletion_case":
        raise ValueError("Compliance workflow type is invalid")
    sequence = value.get("privacy_action_sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise ValueError("Compliance privacy sequence is invalid")
    if not SHA256.fullmatch(str(value.get("live_purge_receipt_sha256", ""))):
        raise ValueError("Compliance purge receipt is invalid")
    timestamp(value.get("created_at"))
    timestamp(value.get("live_data_purged_at"))
    return value


def snapshot_facts(receipt: dict, request: dict) -> dict:
    portable = (receipt.get("storage") or {}).get("portable") or {}
    evidence = receipt.get("evidence") or {}
    if receipt.get("format") != "mp-opt-snapshot-receipt-v2" or receipt.get("type") != "full":
        raise ValueError("A clean baseline requires a full v2 snapshot")
    if receipt.get("verification") != "deep-verified" or portable.get("state") != "operator-sha256-confirmed":
        raise ValueError("The snapshot is not deeply and portably verified")
    created_at = timestamp(receipt.get("created_at"))
    verified_at = timestamp(receipt.get("verified_at"))
    confirmed_at = timestamp(portable.get("confirmed_at"))
    if created_at < timestamp(request["live_data_purged_at"]):
        raise ValueError("The snapshot predates the privacy action")
    if verified_at < created_at or confirmed_at < verified_at:
        raise ValueError("The snapshot verification timeline is invalid")
    package_id = canonical_uuid(portable.get("package_id"))
    for field in ("package_sha256", "archive_sha256"):
        if not SHA256.fullmatch(str(portable.get(field, ""))):
            raise ValueError("Portable snapshot digest is invalid")
    head = evidence.get("head_sha256")
    if not SHA256.fullmatch(str(head or "")):
        raise ValueError("The snapshot has no verified evidence anchor")
    key_id = portable.get("recovery_key_id")
    if not isinstance(key_id, str) or not KEY_ID.fullmatch(key_id):
        raise ValueError("The snapshot recovery key ID is invalid")
    size = portable.get("package_size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        raise ValueError("The snapshot package size is invalid")
    return {
        "package_id": package_id,
        "package_sha256": portable["package_sha256"],
        "package_size": size,
        "archive_sha256": portable["archive_sha256"],
        "recovery_key_id": key_id,
        "snapshot_created_at": created_at.isoformat(),
        "snapshot_evidence_head_sha256": head,
        "deep_verified_at": verified_at.isoformat(),
        "portable_confirmed_at": confirmed_at.isoformat(),
    }


def atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o644)
        os.write(descriptor, raw)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if not hasattr(os, "fchmod"):
            os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def sign_receipt(path: Path, instance_key: Path) -> None:
    """Sign without changing the shared source key or weakening key permissions."""

    if instance_key.is_symlink() or not instance_key.is_file() or instance_key.stat().st_size > 64 * 1024:
        raise ValueError("Compliance signing key is unsafe")
    if not hasattr(os, "fchmod"):
        # Windows OpenSSH protects generated keys with ACLs that chmod cannot
        # reproduce on a copied file. The management runtime is POSIX-only,
        # but its qualification tests can safely sign with the protected
        # source key without changing it.
        result = subprocess.run(
            ["ssh-keygen", "-Y", "sign", "-f", str(instance_key), "-n", "mp-opt-evidence-v1", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError("Compliance receipt signing failed")
        return
    descriptor, name = tempfile.mkstemp(prefix="mp-opt-compliance-signing-key-")
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        key_bytes = instance_key.read_bytes()
        os.write(descriptor, key_bytes)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        result = subprocess.run(
            ["ssh-keygen", "-Y", "sign", "-f", str(temporary), "-n", "mp-opt-evidence-v1", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError("Compliance receipt signing failed")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def emit(args: argparse.Namespace) -> None:
    snapshot = load_regular(Path(args.snapshot_receipt))
    requests = Path(args.requests)
    receipts = Path(args.receipts)
    receipts.mkdir(parents=True, exist_ok=True, mode=0o755)
    os.chmod(receipts, 0o755)
    for request_path in sorted(requests.glob("*.json")) if requests.is_dir() else []:
        request = validate_request(load_regular(request_path, 16 * 1024))
        if request_path.name != f"{request['job_id']}.json":
            raise ValueError("Compliance request filename does not match its job")
        target = receipts / f"{request['job_id']}.json"
        if target.exists():
            if not Path(str(target) + ".sig").is_file():
                raise ValueError("Compliance receipt is incomplete")
            request_path.unlink(missing_ok=True)
            continue
        try:
            facts = snapshot_facts(snapshot, request)
        except ValueError:
            continue
        receipt = {
            "format": "mp-opt-clean-backup-receipt-v1",
            "receipt_id": str(uuid.uuid5(uuid.UUID(request["job_id"]), facts["package_id"])),
            **{key: request[key] for key in (
                "job_id", "instance_id", "workflow_type", "workflow_id", "event_ref",
                "subject_ref", "privacy_action_id", "privacy_action_sequence",
                "live_purge_receipt_sha256", "live_data_purged_at",
            )},
            **facts,
        }
        atomic_write(target, canonical(receipt))
        try:
            sign_receipt(target, Path(args.instance_key))
        except ValueError:
            target.unlink(missing_ok=True)
            raise
        os.chmod(str(target) + ".sig", 0o644)
        request_path.unlink(missing_ok=True)
        print(request["job_id"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", required=True)
    parser.add_argument("--receipts", required=True)
    parser.add_argument("--snapshot-receipt", required=True)
    parser.add_argument("--instance-key", required=True)
    try:
        emit(parser.parse_args())
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(str(exc), file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
