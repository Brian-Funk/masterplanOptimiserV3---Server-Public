#!/usr/bin/env python3
"""Create non-personal, instance-signed clean-backup bridge receipts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid


SHA256 = re.compile(r"^[0-9a-f]{64}$")
KEY_ID = re.compile(r"^rk-[0-9a-f]{16}$")
SNAPSHOT_NAME = re.compile(
    r"^[0-9]{8}T[0-9]{6}Z_(?:database|secrets|full)_[A-Za-z0-9._-]{1,64}$"
)
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
JOURNAL_FIELDS = {
    "format", "state", "resolution_id", "job_id", "selected_snapshot",
    "selected_package_id", "live_data_purged_at", "prepared_at", "resolved_at",
    "candidates",
}
JOURNAL_CANDIDATE_FIELDS = {
    "snapshot", "receipt_sha256", "archive_sha256", "created_at", "tombstone",
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


def snapshot_inventory(root: Path, selected_receipt: Path | None) -> list[dict]:
    """Return a strictly validated inventory without following substituted paths."""

    if root.is_symlink() or not root.is_dir():
        raise ValueError("The local snapshot inventory is unsafe")
    root_resolved = root.resolve()
    selected_directory = selected_receipt.parent if selected_receipt is not None else None
    if selected_directory is not None and (
        selected_directory.is_symlink()
        or selected_directory.parent.resolve() != root_resolved
        or not SNAPSHOT_NAME.fullmatch(selected_directory.name)
    ):
        raise ValueError("The selected snapshot is outside the local inventory")
    inventory: list[dict] = []
    for directory in sorted(root.iterdir(), key=lambda path: path.name):
        if directory.name.startswith("."):
            continue
        if (
            not SNAPSHOT_NAME.fullmatch(directory.name)
            or directory.is_symlink()
            or not directory.is_dir()
            or directory.parent.resolve() != root_resolved
        ):
            raise ValueError("The local snapshot inventory contains an unsafe entry")
        receipt_path = directory / "receipt.json"
        receipt = load_regular(receipt_path)
        if receipt.get("format") != "mp-opt-snapshot-receipt-v2":
            raise ValueError("The local snapshot inventory contains an unsupported receipt")
        archive_sha256 = receipt.get("archive_sha256")
        if not SHA256.fullmatch(str(archive_sha256 or "")):
            raise ValueError("The local snapshot inventory contains an invalid archive digest")
        inventory.append({
            "name": directory.name,
            "path": directory,
            "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            "archive_sha256": archive_sha256,
            "created_at": timestamp(receipt.get("created_at")),
        })
    if selected_directory is not None and not any(item["path"] == selected_directory for item in inventory):
        raise ValueError("The selected snapshot is unavailable")
    if len(inventory) > 256:
        raise ValueError("The local snapshot inventory is too large")
    return inventory


def fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_snapshot_tree(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("A superseded local snapshot path is unsafe")
    for current, directories, files in os.walk(path, followlinks=False):
        current_path = Path(current)
        for name in (*directories, *files):
            if (current_path / name).is_symlink():
                raise ValueError("A superseded local snapshot contains an unsafe link")


def private_atomic_write(path: Path, value: dict) -> None:
    raw = canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        os.write(descriptor, raw)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if not hasattr(os, "fchmod"):
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def prepare_resolution_journal(
    journals: Path,
    request: dict,
    facts: dict,
    inventory: list[dict],
) -> tuple[Path, dict]:
    path = journals / f"{request['job_id']}.json"
    candidates = [
        {
            "snapshot": item["name"],
            "receipt_sha256": item["receipt_sha256"],
            "archive_sha256": item["archive_sha256"],
            "created_at": item["created_at"].isoformat(),
            "tombstone": f".compliance-delete-{item['receipt_sha256']}",
        }
        for item in inventory
        if item["created_at"] < timestamp(request["live_data_purged_at"])
    ]
    if len(candidates) > 128:
        raise ValueError("Too many pre-deletion local snapshots require resolution")
    resolution_id = str(uuid.uuid5(
        uuid.UUID(request["job_id"]),
        f"mp-opt-local-snapshot-resolution-v1:{facts['package_id']}",
    ))
    if path.exists():
        journal = load_regular(path, 256 * 1024)
        if (
            set(journal) != JOURNAL_FIELDS
            or journal.get("format") != "mp-opt-local-snapshot-resolution-v1"
            or journal.get("job_id") != request["job_id"]
            or journal.get("resolution_id") != resolution_id
            or journal.get("selected_package_id") != facts["package_id"]
            or journal.get("selected_snapshot") != Path(facts["selected_snapshot"]).name
            or journal.get("live_data_purged_at") != request["live_data_purged_at"]
            or journal.get("state") not in {"prepared", "resolved"}
            or not isinstance(journal.get("candidates"), list)
            or len(journal["candidates"]) > 128
        ):
            raise ValueError("The local snapshot resolution journal does not match this request")
        timestamp(journal.get("prepared_at"))
        if journal["state"] == "resolved":
            timestamp(journal.get("resolved_at"))
        elif journal.get("resolved_at") is not None:
            raise ValueError("The local snapshot resolution journal state is invalid")
        return path, journal
    journal = {
        "format": "mp-opt-local-snapshot-resolution-v1",
        "state": "prepared",
        "resolution_id": resolution_id,
        "job_id": request["job_id"],
        "selected_snapshot": Path(facts["selected_snapshot"]).name,
        "selected_package_id": facts["package_id"],
        "live_data_purged_at": request["live_data_purged_at"],
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": None,
        "candidates": candidates,
    }
    private_atomic_write(path, journal)
    return path, journal


def resolve_local_snapshots(root: Path, journal_path: Path, journal: dict) -> dict:
    root_resolved = root.resolve()
    selected = root / journal["selected_snapshot"]
    if selected.is_symlink() or not selected.is_dir() or selected.parent.resolve() != root_resolved:
        raise ValueError("The clean replacement snapshot is unavailable")
    for candidate in journal.get("candidates", []):
        if not isinstance(candidate, dict) or set(candidate) != JOURNAL_CANDIDATE_FIELDS:
            raise ValueError("The local snapshot resolution journal is invalid")
        name = candidate.get("snapshot")
        tombstone_name = candidate.get("tombstone")
        if (
            not isinstance(name, str)
            or not SNAPSHOT_NAME.fullmatch(name)
            or not isinstance(tombstone_name, str)
            or tombstone_name != f".compliance-delete-{candidate.get('receipt_sha256')}"
            or not SHA256.fullmatch(str(candidate.get("receipt_sha256", "")))
            or not SHA256.fullmatch(str(candidate.get("archive_sha256", "")))
        ):
            raise ValueError("The local snapshot resolution journal is invalid")
        created_at = timestamp(candidate.get("created_at"))
        if created_at >= timestamp(journal["live_data_purged_at"]):
            raise ValueError("The local snapshot resolution journal contains a clean snapshot")
        source = root / name
        tombstone = root / tombstone_name
        if source == selected:
            raise ValueError("The clean replacement snapshot cannot be superseded")
        if source.exists():
            validate_snapshot_tree(source)
            receipt_path = source / "receipt.json"
            if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != candidate["receipt_sha256"]:
                raise ValueError("A superseded local snapshot changed after resolution was prepared")
            if tombstone.exists():
                raise ValueError("The local snapshot deletion tombstone is unsafe")
            os.replace(source, tombstone)
            fsync_directory(root)
        if tombstone.exists():
            validate_snapshot_tree(tombstone)
            shutil.rmtree(tombstone)
            fsync_directory(root)
    remaining = snapshot_inventory(root, selected / "receipt.json")
    cutoff = timestamp(journal["live_data_purged_at"])
    if any(item["created_at"] < cutoff for item in remaining):
        raise ValueError("A pre-deletion local snapshot remains after automatic resolution")
    if journal.get("state") != "resolved":
        journal = {
            **journal,
            "state": "resolved",
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        private_atomic_write(journal_path, journal)
    return {"journal": journal, "retained_count": len(remaining)}


def resolution_public_facts(journal: dict, retained_count: int) -> dict:
    if journal.get("state") != "resolved" or not journal.get("resolved_at"):
        raise ValueError("The local snapshot resolution is incomplete")
    timestamp(journal["resolved_at"])
    timestamp(journal["live_data_purged_at"])
    projection = {
        "format": "mp-opt-local-snapshot-resolution-v1",
        "resolution_id": journal["resolution_id"],
        "job_id": journal["job_id"],
        "selected_package_id": journal["selected_package_id"],
        "live_data_purged_at": journal["live_data_purged_at"],
        "resolved_at": journal["resolved_at"],
        "superseded_local_snapshots": [
            {
                "receipt_sha256": item["receipt_sha256"],
                "archive_sha256": item["archive_sha256"],
                "created_at": item["created_at"],
            }
            for item in journal["candidates"]
        ],
        "retained_local_snapshot_count": retained_count,
    }
    return {
        "local_resolution_id": journal["resolution_id"],
        "local_resolution_sha256": hashlib.sha256(canonical(projection)).hexdigest(),
        "superseded_local_snapshot_receipt_sha256s": [
            item["receipt_sha256"] for item in journal["candidates"]
        ],
        "retained_local_snapshot_count": retained_count,
    }


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
        fsync_directory(path.parent)
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


def verify_receipt_signature(content: bytes, signature: str, public_key_path: Path) -> None:
    if public_key_path.is_symlink() or not public_key_path.is_file():
        raise ValueError("The instance evidence public key is unavailable")
    public_key = " ".join(public_key_path.read_text(encoding="utf-8").split()[:2])
    if not public_key.startswith("ssh-ed25519 "):
        raise ValueError("The instance evidence public key is invalid")
    with tempfile.TemporaryDirectory(prefix="mp-opt-compliance-verify-") as temporary_name:
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
        raise ValueError("The peer snapshot-resolution signature is invalid")


def emit_peer_resolution(args: argparse.Namespace, target: Path) -> None:
    """Ask the current HA peer to resolve its own pre-purge local snapshots."""

    peer_target = target.with_name(f"{target.stem}.peer.json")
    if peer_target.exists():
        peer_signature_path = Path(str(peer_target) + ".sig")
        if not peer_signature_path.is_file():
            raise ValueError("The peer snapshot resolution receipt is incomplete")
        existing = load_regular(peer_target)
        verify_receipt_signature(
            canonical(existing), peer_signature_path.read_text(encoding="utf-8"),
            Path(str(args.instance_key) + ".pub"),
        )
        return
    receipt = load_regular(target)
    signature_path = Path(str(target) + ".sig")
    if not signature_path.is_file() or signature_path.stat().st_size > 16 * 1024:
        raise ValueError("The clean-backup receipt signature is unavailable")
    request = {
        "format": "mp-opt-peer-snapshot-resolution-request-v1",
        "source_node_id": args.ha_node_id,
        "target_node_id": args.ha_peer_node_id,
        "clean_receipt": receipt,
        "clean_signature": signature_path.read_text(encoding="utf-8"),
    }
    result = subprocess.run(
        [
            "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            args.ha_peer_ssh,
            "python3 /opt/masterplan/deploy/management/peer_snapshot_resolution.py receive",
        ],
        input=json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n",
        text=True,
        check=False,
        capture_output=True,
        timeout=180,
    )
    if result.returncode != 0:
        diagnostic = result.stderr.strip()[:256]
        raise ValueError(f"The HA peer could not resolve its pre-deletion snapshots: {diagnostic}")
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("The HA peer returned an invalid snapshot-resolution response") from exc
    if (
        not isinstance(response, dict)
        or set(response) != {"format", "receipt", "signature"}
        or response.get("format") != "mp-opt-peer-snapshot-resolution-response-v1"
    ):
        raise ValueError("The HA peer returned an invalid snapshot-resolution response")
    peer_receipt = response.get("receipt")
    if not isinstance(peer_receipt, dict) or (
        peer_receipt.get("job_id") != receipt.get("job_id")
        or peer_receipt.get("clean_receipt_sha256") != hashlib.sha256(canonical(receipt)).hexdigest()
        or peer_receipt.get("replacement_package_id") != receipt.get("package_id")
        or peer_receipt.get("replacement_package_sha256") != receipt.get("package_sha256")
        or peer_receipt.get("source_node_id") != args.ha_node_id
        or peer_receipt.get("target_node_id") != args.ha_peer_node_id
    ):
        raise ValueError("The HA peer snapshot-resolution receipt does not match the clean backup")
    peer_signature = response.get("signature")
    if not isinstance(peer_signature, str) or len(peer_signature) > 16 * 1024:
        raise ValueError("The HA peer snapshot-resolution signature is invalid")
    verify_receipt_signature(
        canonical(peer_receipt), peer_signature, Path(str(args.instance_key) + ".pub"),
    )
    atomic_write(peer_target, canonical(peer_receipt))
    atomic_write(Path(str(peer_target) + ".sig"), peer_signature.encode("utf-8"))


def emit(args: argparse.Namespace) -> None:
    snapshot_path = Path(args.snapshot_receipt)
    snapshot = load_regular(snapshot_path)
    snapshot["selected_snapshot"] = snapshot_path.parent.name
    snapshots = Path(args.snapshots)
    inventory = snapshot_inventory(snapshots, snapshot_path)
    requests = Path(args.requests)
    receipts = Path(args.receipts)
    journals = Path(args.resolution_journals)
    receipts.mkdir(parents=True, exist_ok=True, mode=0o755)
    os.chmod(receipts, 0o755)
    if args.reconcile_job_id:
        if args.ha_mode != "ha":
            raise ValueError("Peer snapshot reconciliation is available only in HA mode")
        canonical_uuid(args.reconcile_job_id)
        target = receipts / f"{args.reconcile_job_id}.json"
        if not target.is_file() or not Path(str(target) + ".sig").is_file():
            raise ValueError("The local clean-backup receipt is unavailable for reconciliation")
        emit_peer_resolution(args, target)
        print("PEER_RESOLVED")
        return
    eligible: list[tuple[Path, dict, dict, Path, dict]] = []
    for request_path in sorted(requests.glob("*.json")) if requests.is_dir() else []:
        request = validate_request(load_regular(request_path, 16 * 1024))
        if request_path.name != f"{request['job_id']}.json":
            raise ValueError("Compliance request filename does not match its job")
        target = receipts / f"{request['job_id']}.json"
        if target.exists():
            if not Path(str(target) + ".sig").is_file():
                raise ValueError("Compliance receipt is incomplete")
            if args.ha_mode == "ha":
                emit_peer_resolution(args, target)
            request_path.unlink(missing_ok=True)
            continue
        try:
            facts = snapshot_facts(snapshot, request)
        except ValueError:
            continue
        facts["selected_snapshot"] = snapshot_path.parent.name
        journal_path, journal = prepare_resolution_journal(journals, request, facts, inventory)
        eligible.append((request_path, request, facts, journal_path, journal))
    resolved: dict[str, dict] = {}
    removed_receipt_sha256s: set[str] = set()
    for _, request, _, journal_path, journal in eligible:
        resolved[request["job_id"]] = resolve_local_snapshots(snapshots, journal_path, journal)
        removed_receipt_sha256s.update(
            item["receipt_sha256"]
            for item in resolved[request["job_id"]]["journal"]["candidates"]
        )
    for request_path, request, facts, _, _ in eligible:
        resolution = resolved[request["job_id"]]
        target = receipts / f"{request['job_id']}.json"
        receipt = {
            "format": "mp-opt-clean-backup-receipt-v4",
            "receipt_id": str(uuid.uuid5(uuid.UUID(request["job_id"]), facts["package_id"])),
            **{key: request[key] for key in (
                "job_id", "instance_id", "workflow_type", "workflow_id", "event_ref",
                "subject_ref", "privacy_action_id", "privacy_action_sequence",
                "live_purge_receipt_sha256", "live_data_purged_at",
            )},
            **facts,
            **resolution_public_facts(
                resolution["journal"], resolution["retained_count"],
            ),
            "superseded_portable_packages": superseded_portable_packages(
                Path(args.portable_inventory), facts["package_id"], request,
            ),
        }
        receipt.pop("selected_snapshot", None)
        atomic_write(target, canonical(receipt))
        try:
            sign_receipt(target, Path(args.instance_key))
        except ValueError:
            target.unlink(missing_ok=True)
            raise
        os.chmod(str(target) + ".sig", 0o644)
        if args.ha_mode == "ha":
            emit_peer_resolution(args, target)
        request_path.unlink(missing_ok=True)
    if eligible:
        print(f"RESOLVED\t{len(removed_receipt_sha256s)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", required=True)
    parser.add_argument("--receipts", required=True)
    parser.add_argument("--snapshots", required=True)
    parser.add_argument("--portable-inventory", required=True)
    parser.add_argument("--resolution-journals", required=True)
    parser.add_argument("--snapshot-receipt", required=True)
    parser.add_argument("--instance-key", required=True)
    parser.add_argument("--ha-mode", choices=["standalone", "ha"], default="standalone")
    parser.add_argument("--ha-node-id")
    parser.add_argument("--ha-peer-node-id")
    parser.add_argument("--ha-peer-ssh")
    parser.add_argument("--reconcile-job-id")
    try:
        args = parser.parse_args()
        if args.ha_mode == "ha" and not all((args.ha_node_id, args.ha_peer_node_id, args.ha_peer_ssh)):
            raise ValueError("The HA peer snapshot-resolution configuration is incomplete")
        emit(args)
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(str(exc), file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
