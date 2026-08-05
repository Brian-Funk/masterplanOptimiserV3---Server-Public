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
PORTABLE_INVENTORY_FIELDS = {
    "format", "state", "snapshot", "snapshot_created_at", "confirmed_at",
    "package_id", "package_sha256", "package_size", "archive_sha256",
    "recovery_key_id",
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


def local_snapshot_count(root: Path, selected_receipt: Path) -> int:
    """Count completed local snapshots without following substituted paths."""

    if root.is_symlink() or not root.is_dir():
        raise ValueError("The local snapshot inventory is unsafe")
    selected_directory = selected_receipt.parent
    if selected_directory.is_symlink() or selected_directory.parent.resolve() != root.resolve():
        raise ValueError("The selected snapshot is outside the local inventory")
    count = 0
    for directory in root.iterdir():
        if directory.name.startswith("."):
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("The local snapshot inventory contains an unsafe entry")
        receipt = load_regular(directory / "receipt.json")
        if receipt.get("format") != "mp-opt-snapshot-receipt-v2":
            raise ValueError("The local snapshot inventory contains an unsupported receipt")
        count += 1
    if not selected_directory.exists():
        raise ValueError("The selected snapshot is unavailable")
    return count


def superseded_portable_packages(root: Path, selected_package_id: str, request: dict) -> list[dict]:
    """Return every known pre-deletion workstation package except the clean replacement."""

    if root.is_symlink() or not root.is_dir():
        raise ValueError("The portable export inventory is unsafe")
    purged_at = timestamp(request["live_data_purged_at"])
    packages: list[dict] = []
    for path in sorted(root.glob("*.json")):
        document = load_regular(path)
        if set(document) != PORTABLE_INVENTORY_FIELDS:
            raise ValueError("The portable export inventory schema is invalid")
        if (
            document.get("format") != "mp-opt-portable-export-inventory-v1"
            or document.get("state") != "operator-sha256-confirmed"
        ):
            raise ValueError("The portable export inventory state is invalid")
        package_id = canonical_uuid(document.get("package_id"))
        if path.name != f"{package_id}.json":
            raise ValueError("The portable export inventory filename is invalid")
        created_at = timestamp(document.get("snapshot_created_at"))
        confirmed_at = timestamp(document.get("confirmed_at"))
        if confirmed_at < created_at:
            raise ValueError("The portable export inventory timeline is invalid")
        if package_id == selected_package_id or created_at >= purged_at:
            continue
        for field in ("package_sha256", "archive_sha256"):
            if not SHA256.fullmatch(str(document.get(field, ""))):
                raise ValueError("The portable export inventory digest is invalid")
        key_id = document.get("recovery_key_id")
        if not isinstance(key_id, str) or not KEY_ID.fullmatch(key_id):
            raise ValueError("The portable export inventory recovery key is invalid")
        size = document.get("package_size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            raise ValueError("The portable export inventory size is invalid")
        packages.append({
            "package_id": package_id,
            "package_sha256": document["package_sha256"],
            "package_size": size,
            "archive_sha256": document["archive_sha256"],
            "recovery_key_id": key_id,
            "snapshot_created_at": created_at.isoformat(),
            "portable_confirmed_at": confirmed_at.isoformat(),
        })
    if len(packages) > 128:
        raise ValueError("The portable export inventory is too large")
    return packages


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
    snapshot_path = Path(args.snapshot_receipt)
    snapshot = load_regular(snapshot_path)
    snapshot_count = local_snapshot_count(Path(args.snapshots), snapshot_path)
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
        if snapshot_count != 1:
            raise ValueError(
                "Superseded local snapshots remain. Delete every older local snapshot "
                "before recording the clean recovery receipt"
            )
        receipt = {
            "format": "mp-opt-clean-backup-receipt-v3",
            "receipt_id": str(uuid.uuid5(uuid.UUID(request["job_id"]), facts["package_id"])),
            **{key: request[key] for key in (
                "job_id", "instance_id", "workflow_type", "workflow_id", "event_ref",
                "subject_ref", "privacy_action_id", "privacy_action_sequence",
                "live_purge_receipt_sha256", "live_data_purged_at",
            )},
            **facts,
            "local_snapshot_count": snapshot_count,
            "superseded_portable_packages": superseded_portable_packages(
                Path(args.portable_inventory), facts["package_id"], request,
            ),
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
    parser.add_argument("--snapshots", required=True)
    parser.add_argument("--portable-inventory", required=True)
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
