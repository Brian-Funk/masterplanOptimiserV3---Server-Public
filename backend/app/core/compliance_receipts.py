"""One-use bridge between host recovery verification and web workflows."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.evidence import EvidenceUnavailable, _verify_detached_bytes
from app.models.evidence import EvidenceKey


SHA256 = re.compile(r"^[0-9a-f]{64}$")
RECOVERY_KEY_ID = re.compile(r"^rk-[0-9a-f]{16}$")
RECEIPT_COMMON_FIELDS = {
    "format", "receipt_id", "job_id", "instance_id", "workflow_type",
    "workflow_id", "event_ref", "subject_ref", "privacy_action_id",
    "privacy_action_sequence", "live_purge_receipt_sha256", "package_id",
    "live_data_purged_at",
    "package_sha256", "package_size", "archive_sha256", "recovery_key_id",
    "snapshot_created_at", "snapshot_evidence_head_sha256", "deep_verified_at",
    "portable_confirmed_at", "superseded_portable_packages",
}
RECEIPT_V3_FIELDS = RECEIPT_COMMON_FIELDS | {"local_snapshot_count"}
RECEIPT_V4_FIELDS = RECEIPT_COMMON_FIELDS | {
    "local_resolution_id", "local_resolution_sha256",
    "superseded_local_snapshot_receipt_sha256s",
    "retained_local_snapshot_count",
}
SUPERSEDED_PORTABLE_FIELDS = {
    "package_id", "package_sha256", "package_size", "archive_sha256",
    "recovery_key_id", "snapshot_created_at", "portable_confirmed_at",
}
PEER_RESOLUTION_FIELDS = {
    "format", "resolution_id", "job_id", "instance_id", "workflow_type", "workflow_id", "event_ref",
    "subject_ref", "privacy_action_id", "privacy_action_sequence",
    "live_purge_receipt_sha256", "live_data_purged_at", "clean_receipt_sha256",
    "replacement_package_id", "replacement_package_sha256", "source_node_id",
    "target_node_id", "resolved_at", "superseded_local_snapshot_receipt_sha256s",
    "retained_local_snapshot_count", "resolution_sha256",
}
NODE_ID = re.compile(r"^[a-z][a-z0-9-]{1,31}$")


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise EvidenceUnavailable("The compliance receipt timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceUnavailable("The compliance receipt timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed > datetime.now(timezone.utc):
        raise EvidenceUnavailable("The compliance receipt timestamp is invalid")
    return parsed.astimezone(timezone.utc)


def _uuid(value: object, label: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except ValueError as exc:
        raise EvidenceUnavailable(f"The compliance receipt {label} is invalid") from exc
    if str(parsed) != value:
        raise EvidenceUnavailable(f"The compliance receipt {label} is invalid")
    return str(parsed)


def _atomic_write(path: Path, raw: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, mode)
        os.write(descriptor, raw)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if not hasattr(os, "fchmod"):
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def queue_clean_backup_request(
    *,
    job_id: str,
    instance_id: str,
    workflow_type: str,
    workflow_id: str,
    event_ref: str,
    subject_ref: str | None,
    privacy_action_id: str,
    privacy_action_sequence: int,
    live_purge_receipt_sha256: str,
    live_data_purged_at: datetime,
) -> None:
    for value, label in (
        (job_id, "job_id"),
        (instance_id, "instance_id"),
        (workflow_id, "workflow_id"),
        (event_ref, "event_ref"),
        (privacy_action_id, "privacy_action_id"),
    ):
        _uuid(value, label)
    if subject_ref is not None:
        _uuid(subject_ref, "subject_ref")
    if workflow_type != "deletion_case":
        raise EvidenceUnavailable("The compliance workflow type is invalid")
    if (
        not isinstance(privacy_action_sequence, int)
        or isinstance(privacy_action_sequence, bool)
        or privacy_action_sequence < 1
    ):
        raise EvidenceUnavailable("The compliance privacy sequence is invalid")
    if not SHA256.fullmatch(live_purge_receipt_sha256):
        raise EvidenceUnavailable("The compliance purge receipt is invalid")
    if live_data_purged_at.tzinfo is None:
        raise EvidenceUnavailable("The privacy-action timestamp is invalid")
    document = {
        "format": "mp-opt-clean-backup-request-v1",
        "job_id": job_id,
        "instance_id": instance_id,
        "workflow_type": workflow_type,
        "workflow_id": workflow_id,
        "event_ref": event_ref,
        "subject_ref": subject_ref,
        "privacy_action_id": privacy_action_id,
        "privacy_action_sequence": privacy_action_sequence,
        "live_purge_receipt_sha256": live_purge_receipt_sha256,
        "live_data_purged_at": live_data_purged_at.astimezone(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    target = Path(settings.COMPLIANCE_REQUEST_DIR) / f"{job_id}.json"
    try:
        _atomic_write(target, raw)
    except OSError as exc:
        raise EvidenceUnavailable("The host compliance agent is unavailable") from exc


def cancel_pending_clean_backup_request(*, job_id: str) -> None:
    """Cancel only a request that has not produced any host receipt."""

    _uuid(job_id, "job_id")
    request_path = Path(settings.COMPLIANCE_REQUEST_DIR) / f"{job_id}.json"
    receipt_path = Path(settings.COMPLIANCE_RECEIPT_DIR) / f"{job_id}.json"
    signature_path = Path(str(receipt_path) + ".sig")
    try:
        if any(path.is_symlink() for path in (request_path, receipt_path, signature_path)):
            raise EvidenceUnavailable("The compliance path is unsafe")
        if receipt_path.exists() or signature_path.exists():
            raise EvidenceUnavailable("The recovery snapshot has already produced a receipt")
        if request_path.exists() and not request_path.is_file():
            raise EvidenceUnavailable("The compliance request path is unsafe")
        request_path.unlink(missing_ok=True)
    except OSError as exc:
        raise EvidenceUnavailable("The host compliance agent is unavailable") from exc


def verified_clean_backup_receipt(
    db: Session,
    *,
    job_id: str,
    expected: dict,
) -> dict:
    _uuid(job_id, "job_id")
    path = Path(settings.COMPLIANCE_RECEIPT_DIR) / f"{job_id}.json"
    signature_path = Path(str(path) + ".sig")
    try:
        if path.is_symlink() or signature_path.is_symlink():
            raise EvidenceUnavailable("The compliance receipt path is unsafe")
        if not path.exists() or not signature_path.exists():
            raise EvidenceUnavailable("The verified host receipt is not available yet")
        if (
            not path.is_file()
            or not signature_path.is_file()
            or signature_path.stat().st_size > 16 * 1024
        ):
            raise EvidenceUnavailable("The compliance receipt path is unsafe")
        raw = path.read_bytes()
        signature = signature_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvidenceUnavailable("The verified host receipt is not available yet") from exc
    if len(raw) > 64 * 1024:
        raise EvidenceUnavailable("The compliance receipt is too large")
    key = db.query(EvidenceKey).filter(
        EvidenceKey.role == "instance",
        EvidenceKey.revoked_at.is_(None),
    ).one_or_none()
    if key is None:
        raise EvidenceUnavailable("The instance evidence key is unavailable")
    _verify_detached_bytes(content=raw, signature=signature, public_key=key.public_key)
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvidenceUnavailable("The compliance receipt is invalid JSON") from exc
    canonical = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if raw != canonical or not isinstance(document, dict):
        raise EvidenceUnavailable("The compliance receipt schema is invalid")
    receipt_format = document.get("format")
    expected_fields = (
        RECEIPT_V3_FIELDS
        if receipt_format == "mp-opt-clean-backup-receipt-v3"
        else RECEIPT_V4_FIELDS
        if receipt_format == "mp-opt-clean-backup-receipt-v4"
        else None
    )
    if expected_fields is None:
        raise EvidenceUnavailable("The compliance receipt format is invalid")
    if set(document) != expected_fields:
        raise EvidenceUnavailable("The compliance receipt schema is invalid")
    for field in ("receipt_id", "job_id", "instance_id", "workflow_id", "event_ref", "privacy_action_id", "package_id"):
        _uuid(document.get(field), field)
    if document.get("subject_ref") is not None:
        _uuid(document.get("subject_ref"), "subject_ref")
    for field, value in expected.items():
        if document.get(field) != value:
            raise EvidenceUnavailable("The compliance receipt does not match this workflow")
    expected_receipt_id = str(
        uuid.uuid5(uuid.UUID(document["job_id"]), document["package_id"])
    )
    if document["receipt_id"] != expected_receipt_id:
        raise EvidenceUnavailable("The compliance receipt ID is inconsistent")
    if document.get("workflow_type") != "deletion_case":
        raise EvidenceUnavailable("The compliance receipt workflow is invalid")
    sequence = document.get("privacy_action_sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise EvidenceUnavailable("The compliance receipt privacy sequence is invalid")
    for field in ("live_purge_receipt_sha256", "package_sha256", "archive_sha256", "snapshot_evidence_head_sha256"):
        if not SHA256.fullmatch(str(document.get(field, ""))):
            raise EvidenceUnavailable("The compliance receipt contains an invalid digest")
    if not RECOVERY_KEY_ID.fullmatch(str(document.get("recovery_key_id", ""))):
        raise EvidenceUnavailable("The compliance receipt recovery key is invalid")
    size = document.get("package_size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        raise EvidenceUnavailable("The compliance receipt package size is invalid")
    if receipt_format == "mp-opt-clean-backup-receipt-v3":
        if document.get("local_snapshot_count") != 1:
            raise EvidenceUnavailable("Superseded local recovery snapshots remain")
        document["retained_local_snapshot_count"] = document["local_snapshot_count"]
        document["local_resolution_id"] = None
        document["local_resolution_sha256"] = None
        document["superseded_local_snapshot_receipt_sha256s"] = []
    else:
        _uuid(document.get("local_resolution_id"), "local_resolution_id")
        if not SHA256.fullmatch(str(document.get("local_resolution_sha256", ""))):
            raise EvidenceUnavailable("The local snapshot resolution digest is invalid")
        retained_count = document.get("retained_local_snapshot_count")
        if (
            not isinstance(retained_count, int)
            or isinstance(retained_count, bool)
            or retained_count < 1
        ):
            raise EvidenceUnavailable("The retained local snapshot count is invalid")
        local_receipts = document.get("superseded_local_snapshot_receipt_sha256s")
        if not isinstance(local_receipts, list) or len(local_receipts) > 128:
            raise EvidenceUnavailable("The superseded local snapshot inventory is invalid")
        if len(set(local_receipts)) != len(local_receipts) or any(
            not isinstance(digest, str) or not SHA256.fullmatch(digest)
            for digest in local_receipts
        ):
            raise EvidenceUnavailable("The superseded local snapshot inventory is invalid")
    purged_at = _timestamp(expected["live_data_purged_at"])
    created_at = _timestamp(document.get("snapshot_created_at"))
    verified_at = _timestamp(document.get("deep_verified_at"))
    confirmed_at = _timestamp(document.get("portable_confirmed_at"))
    if created_at < purged_at or verified_at < created_at or confirmed_at < verified_at:
        raise EvidenceUnavailable("The clean backup was not verified after the privacy action")
    superseded = document.get("superseded_portable_packages")
    if not isinstance(superseded, list) or len(superseded) > 128:
        raise EvidenceUnavailable("The superseded portable-package inventory is invalid")
    package_ids: set[str] = set()
    for package in superseded:
        if not isinstance(package, dict) or set(package) != SUPERSEDED_PORTABLE_FIELDS:
            raise EvidenceUnavailable("The superseded portable-package inventory is invalid")
        package_id = _uuid(package.get("package_id"), "superseded package_id")
        if package_id == document["package_id"] or package_id in package_ids:
            raise EvidenceUnavailable("The superseded portable-package inventory is inconsistent")
        package_ids.add(package_id)
        for field in ("package_sha256", "archive_sha256"):
            if not SHA256.fullmatch(str(package.get(field, ""))):
                raise EvidenceUnavailable("The superseded portable package contains an invalid digest")
        if not RECOVERY_KEY_ID.fullmatch(str(package.get("recovery_key_id", ""))):
            raise EvidenceUnavailable("The superseded portable package recovery key is invalid")
        package_size = package.get("package_size")
        if not isinstance(package_size, int) or isinstance(package_size, bool) or package_size < 1:
            raise EvidenceUnavailable("The superseded portable package size is invalid")
        package_created_at = _timestamp(package.get("snapshot_created_at"))
        package_confirmed_at = _timestamp(package.get("portable_confirmed_at"))
        if package_created_at >= purged_at or package_confirmed_at < package_created_at:
            raise EvidenceUnavailable("The superseded portable package timeline is invalid")
    document["receipt_sha256"] = hashlib.sha256(raw).hexdigest()
    return document


def verified_peer_snapshot_resolution_receipt(
    db: Session,
    *,
    job_id: str,
    expected: dict,
) -> dict:
    """Verify the current HA peer's signed, case-bound local resolution receipt."""

    _uuid(job_id, "job_id")
    path = Path(settings.COMPLIANCE_RECEIPT_DIR) / f"{job_id}.peer.json"
    signature_path = Path(str(path) + ".sig")
    try:
        if path.is_symlink() or signature_path.is_symlink():
            raise EvidenceUnavailable("The peer compliance receipt path is unsafe")
        if not path.is_file() or not signature_path.is_file():
            raise EvidenceUnavailable("The verified peer snapshot receipt is not available yet")
        if path.stat().st_size > 64 * 1024 or signature_path.stat().st_size > 16 * 1024:
            raise EvidenceUnavailable("The peer compliance receipt path is unsafe")
        raw = path.read_bytes()
        signature = signature_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvidenceUnavailable("The verified peer snapshot receipt is not available yet") from exc
    key = db.query(EvidenceKey).filter(
        EvidenceKey.role == "instance", EvidenceKey.revoked_at.is_(None),
    ).one_or_none()
    if key is None:
        raise EvidenceUnavailable("The instance evidence key is unavailable")
    _verify_detached_bytes(content=raw, signature=signature, public_key=key.public_key)
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvidenceUnavailable("The peer snapshot receipt is invalid JSON") from exc
    canonical = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if (
        raw != canonical or not isinstance(document, dict)
        or set(document) != PEER_RESOLUTION_FIELDS
        or document.get("format") != "mp-opt-peer-snapshot-resolution-v1"
    ):
        raise EvidenceUnavailable("The peer snapshot receipt schema is invalid")
    for field in (
        "resolution_id", "job_id", "instance_id", "workflow_id", "event_ref",
        "privacy_action_id", "replacement_package_id",
    ):
        _uuid(document.get(field), field)
    if document.get("subject_ref") is not None:
        _uuid(document.get("subject_ref"), "subject_ref")
    if document.get("job_id") != job_id:
        raise EvidenceUnavailable("The peer snapshot receipt does not match this job")
    for field, value in expected.items():
        if document.get(field) != value:
            raise EvidenceUnavailable("The peer snapshot receipt does not match this workflow")
    if (
        not NODE_ID.fullmatch(str(document.get("source_node_id", "")))
        or not NODE_ID.fullmatch(str(document.get("target_node_id", "")))
        or document.get("source_node_id") != settings.HA_NODE_ID
        or document.get("target_node_id") != settings.HA_PEER_NODE_ID
    ):
        raise EvidenceUnavailable("The peer snapshot receipt does not match this HA pair")
    sequence = document.get("privacy_action_sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise EvidenceUnavailable("The peer snapshot receipt privacy sequence is invalid")
    for field in (
        "live_purge_receipt_sha256", "clean_receipt_sha256",
        "replacement_package_sha256", "resolution_sha256",
    ):
        if not SHA256.fullmatch(str(document.get(field, ""))):
            raise EvidenceUnavailable("The peer snapshot receipt contains an invalid digest")
    purged_at = _timestamp(document.get("live_data_purged_at"))
    resolved_at = _timestamp(document.get("resolved_at"))
    if resolved_at < purged_at:
        raise EvidenceUnavailable("The peer snapshot resolution predates the privacy action")
    retained = document.get("retained_local_snapshot_count")
    if not isinstance(retained, int) or isinstance(retained, bool) or retained < 0:
        raise EvidenceUnavailable("The peer retained-snapshot count is invalid")
    removed = document.get("superseded_local_snapshot_receipt_sha256s")
    if not isinstance(removed, list) or len(removed) > 128 or len(set(removed)) != len(removed):
        raise EvidenceUnavailable("The peer superseded-snapshot inventory is invalid")
    if any(not isinstance(value, str) or not SHA256.fullmatch(value) for value in removed):
        raise EvidenceUnavailable("The peer superseded-snapshot inventory is invalid")
    document["receipt_sha256"] = hashlib.sha256(raw).hexdigest()
    return document
