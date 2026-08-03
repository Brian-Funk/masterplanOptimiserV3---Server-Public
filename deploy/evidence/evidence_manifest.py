#!/usr/bin/env python3
"""Create and verify minimal, signed MP-OPT accountability evidence.

This module intentionally has no network client. Evidence stays under the
self-hosting controller's control and OpenSSH performs all signing operations.
Signatures attest to a statement and its integrity; they do not prove physical
deletion of storage media.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import uuid

try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - exercised on Windows
    fcntl = None
    import msvcrt
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

FORMAT = "mp-opt-evidence-record-v1"
NAMESPACE = "mp-opt-evidence-v1"


def _lock_exclusive(descriptor: int) -> None:
    if fcntl is not None:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return
    if os.fstat(descriptor).st_size == 0:
        os.write(descriptor, b"\0")
        os.fsync(descriptor)
    os.lseek(descriptor, 0, os.SEEK_SET)
    msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)


def _unlock(descriptor: int) -> None:
    if fcntl is not None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return
    os.lseek(descriptor, 0, os.SEEK_SET)
    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
MAX_MANIFEST_BYTES = 64 * 1024
GENESIS = "GENESIS"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KEY_ID_RE = re.compile(r"^ek-[0-9a-f]{16}$")
SAFE_ENUM_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")

RECORD_TYPES = frozenset(
    {
        "instance.initialised",
        "key.rotated",
        "trust_key.registered",
        "trust_key.rotated",
        "trust_key.revoked",
        "trust.role_statement_imported",
        "data_subject.deletion.requested",
        "data_subject.deletion.withdrawn",
        "data_subject.deletion.accepted",
        "data_subject.deletion.rejected",
        "data_subject.access_revoked",
        "data_subject.live_data_purged",
        "deletion.peer_confirmed",
        "deletion.clean_backup_verified",
        "deletion.event_requested",
        "deletion.event_accepted",
        "deletion.event_access_revoked",
        "deletion.desktop_work_order_created",
        "deletion.desktop_report_received",
        "deletion.desktop_absence_confirmed",
        "deletion.desktop_actions_resolved",
        "deletion.event_live_data_purged",
        "deletion.backup_inventory_resolved",
        "deletion.backup_not_applicable",
        "deletion.checklist_created",
        "deletion.checklist_approved",
        "deletion.completed",
        "backup.export_confirmed",
        "backup.superseded",
        "evidence.bundle_exported",
        "evidence.git_anchor_imported",
        "evidence.git_archive_completed",
        "privacy_action.created",
    }
)

TOP_LEVEL_FIELDS = frozenset(
    {
        "format",
        "instance_id",
        "chain_id",
        "sequence",
        "record_id",
        "record_type",
        "created_at",
        "signer",
        "previous_record_sha256",
        "management_audit_tail_sha256",
        "payload",
    }
)
SIGNER_FIELDS = frozenset({"key_id", "role"})
SIGNER_ROLES = frozenset({"instance"})

# Payload fields are intentionally machine-oriented. Free-form explanations,
# names, e-mail addresses, host paths and task content are not accepted.
PAYLOAD_FIELDS = frozenset(
    {
        "request_id",
        "case_id",
        "case_type",
        "event_ref",
        "subject_ref",
        "work_order_id",
        "operation",
        "request_type",
        "identity_verification",
        "initiation_reason",
        "verification_method",
        "normal_response_due_at",
        "submitted_at",
        "policy_response_due_at",
        "access_revocation_due_at",
        "status",
        "outcome",
        "topology",
        "decision_code",
        "receipt_sha256",
        "package_id",
        "package_sha256",
        "archive_sha256",
        "replacement_package_id",
        "replacement_package_sha256",
        "verified_at",
        "completed_at",
        "retain_until",
        "peer_confirmed_at",
        "privacy_action_id",
        "privacy_action_sequence",
        "action_type",
        "key_id",
        "key_role",
        "previous_key_id",
        "new_key_id",
        "public_key_sha256",
        "chain_head_sha256",
        "bundle_sha256",
        "bundle_id",
        "generation",
        "error_code",
        "report_sha256",
        "checklist_sha256",
        "approval_sha256",
        "executor_approval_sha256",
        "processor_approval_sha256",
        "outstanding_backup_ids",
        "outstanding_actions",
        "actions",
        "outstanding_live_stores",
        "request_manifest_sha256",
        "acceptance_receipt_sha256",
        "access_revocation_receipt_sha256",
        "live_purge_receipt_sha256",
        "privacy_action_sha256",
        "peer_confirmation_sha256",
        "replacement_backup_receipt_sha256",
        "controller_approval_sha256",
        "package_ids",
        "checklist_version",
        "role",
        "challenge_sha256",
        "proof_sha256",
        "previous_proof_sha256",
        "signature_sha256",
        "reason_code",
        "anchor_id",
        "repository_id",
        "git_commit_sha",
        "controller_key_id",
        "controller_role",
        "instance_id",
        "entity_id",
        "algorithm",
        "root_credential_id_sha256",
        "root_action_sha256",
        "root_authorisation",
        "server_verification",
        "ledger_signer_role",
        "statement_sha256",
        "signed_at",
        "submission_id",
        "controller_id",
        "pull_request_number",
        "pull_request_head_sha",
        "merge_commit_sha",
        "archive_status",
        "archive_repository_id",
        "evidence_operation_id",
        "evidence_workflow_type",
        "evidence_workflow_id",
        "evidence_operation_type",
    }
)
UUID_FIELDS = frozenset(
    {
        "request_id",
        "case_id",
        "event_ref",
        "subject_ref",
        "work_order_id",
        "package_id",
        "bundle_id",
        "replacement_package_id",
        "privacy_action_id",
        "evidence_operation_id",
        "anchor_id",
        "repository_id",
        "instance_id",
    }
)
HASH_FIELDS = frozenset(
    {
        "receipt_sha256",
        "package_sha256",
        "archive_sha256",
        "replacement_package_sha256",
        "report_sha256",
        "checklist_sha256",
        "approval_sha256",
        "executor_approval_sha256",
        "processor_approval_sha256",
        "public_key_sha256",
        "chain_head_sha256",
        "bundle_sha256",
        "request_manifest_sha256",
        "acceptance_receipt_sha256",
        "access_revocation_receipt_sha256",
        "live_purge_receipt_sha256",
        "privacy_action_sha256",
        "peer_confirmation_sha256",
        "replacement_backup_receipt_sha256",
        "controller_approval_sha256",
        "challenge_sha256",
        "proof_sha256",
        "previous_proof_sha256",
        "signature_sha256",
        "root_credential_id_sha256",
        "root_action_sha256",
        "statement_sha256",
    }
)
TIMESTAMP_FIELDS = frozenset(
    {
        "normal_response_due_at",
        "submitted_at",
        "policy_response_due_at",
        "access_revocation_due_at",
        "verified_at",
        "completed_at",
        "retain_until",
        "peer_confirmed_at",
        "signed_at",
    }
)


class EvidenceError(ValueError):
    """Raised when evidence is unsafe, malformed, or unverifiable."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON property: {key}")
        result[key] = value
    return result


def _reject_float(value: str) -> None:
    raise EvidenceError(f"floating-point numbers are forbidden: {value}")


def load_json_bytes(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise EvidenceError("manifest exceeds 64 KiB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError("manifest must be UTF-8") from exc
    if "\r" in text:
        raise EvidenceError("manifest must use LF line endings")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=_reject_float,
            parse_constant=_reject_float,
        )
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise EvidenceError("manifest must be a JSON object")
    return value


def canonical_json(value: dict[str, Any]) -> bytes:
    _reject_values(value)
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise EvidenceError("manifest exceeds 64 KiB")
    return encoded


def _reject_values(value: Any) -> None:
    if isinstance(value, float):
        raise EvidenceError("floating-point numbers are forbidden")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        if any(ord(character) < 0x20 for character in value):
            raise EvidenceError("control characters are forbidden")
        if len(value) > 512:
            raise EvidenceError("string value exceeds 512 characters")
        return
    if isinstance(value, list):
        if len(value) > 256:
            raise EvidenceError("array exceeds 256 entries")
        for item in value:
            _reject_values(item)
        return
    if isinstance(value, dict):
        if len(value) > 128:
            raise EvidenceError("object exceeds 128 properties")
        for key, item in value.items():
            if not isinstance(key, str):
                raise EvidenceError("object keys must be strings")
            _reject_values(key)
            _reject_values(item)
        return
    raise EvidenceError(f"unsupported JSON value: {type(value).__name__}")


def _canonical_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise EvidenceError(f"{field} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise EvidenceError(f"{field} must be a UUID") from exc
    if str(parsed) != value:
        raise EvidenceError(f"{field} must use canonical lower-case UUID form")
    return value


def _timestamp(value: Any, field: str, *, reject_future: bool = True) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise EvidenceError(f"{field} must be a UTC RFC 3339 timestamp ending in Z")
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if reject_future and parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise EvidenceError(f"{field} is more than five minutes in the future")
    return parsed


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_public_key(public_key: str) -> str:
    parts = public_key.strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise EvidenceError("evidence keys must be OpenSSH Ed25519 public keys")
    return f"{parts[0]} {parts[1]}"


def key_id(public_key: str) -> str:
    canonical = canonical_public_key(public_key)
    return "ek-" + hashlib.sha256(canonical.encode("ascii")).hexdigest()[:16]


def _validate_payload(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, dict):
        unknown = set(value) - PAYLOAD_FIELDS
        if unknown:
            raise EvidenceError(f"unknown {path} properties: {', '.join(sorted(unknown))}")
        for field, item in value.items():
            child_path = f"{path}.{field}"
            if field in UUID_FIELDS:
                _canonical_uuid(item, child_path)
            elif field == "evidence_workflow_id":
                if not isinstance(item, str):
                    raise EvidenceError(f"{child_path} must be a bounded identifier")
                try:
                    _canonical_uuid(item, child_path)
                except EvidenceError:
                    if not KEY_ID_RE.fullmatch(item):
                        raise EvidenceError(
                            f"{child_path} must be a UUID or evidence key ID"
                        )
            elif field in HASH_FIELDS:
                if not isinstance(item, str) or not SHA256_RE.fullmatch(item):
                    raise EvidenceError(f"{child_path} must be a lower-case SHA-256 digest")
            elif field in TIMESTAMP_FIELDS:
                # Deadlines and retention times may legitimately be in the future.
                _timestamp(item, child_path, reject_future=False)
            elif field == "git_commit_sha":
                if not isinstance(item, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", item):
                    raise EvidenceError(f"{child_path} must be a Git object digest")
            elif field in {"pull_request_head_sha", "merge_commit_sha"}:
                if not isinstance(item, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", item):
                    raise EvidenceError(f"{child_path} must be a Git object digest")
            elif field == "archive_repository_id":
                if not isinstance(item, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", item):
                    raise EvidenceError(f"{child_path} must be a numeric repository ID")
            elif field == "submission_id":
                if not isinstance(item, str) or not re.fullmatch(r"sub-[0-9a-f]{32}", item):
                    raise EvidenceError(f"{child_path} must be an archive submission ID")
            elif field == "controller_id":
                if not isinstance(item, str) or not re.fullmatch(r"ctl-[a-z0-9]{16}", item):
                    raise EvidenceError(f"{child_path} must be a controller ID")
            elif field == "pull_request_number":
                if not isinstance(item, int) or isinstance(item, bool) or not 1 <= item <= 2147483647:
                    raise EvidenceError(f"{child_path} must be a positive pull request number")
            elif isinstance(item, dict):
                _validate_payload(item, path=child_path)
            elif isinstance(item, list):
                if len(item) > 256:
                    raise EvidenceError(f"{child_path} has too many entries")
                for index, entry in enumerate(item):
                    if isinstance(entry, dict):
                        _validate_payload(entry, path=f"{child_path}[{index}]")
                    elif field in {"package_ids", "outstanding_backup_ids"}:
                        _canonical_uuid(entry, f"{child_path}[{index}]")
                    elif not isinstance(entry, str) or not SAFE_ENUM_RE.fullmatch(entry):
                        raise EvidenceError(f"{child_path} entries must be bounded enums")
            elif isinstance(item, str):
                if field == "error_code" and not SAFE_ENUM_RE.fullmatch(item):
                    raise EvidenceError(f"{child_path} must be an enumerated error code")
                if field not in UUID_FIELDS | HASH_FIELDS | TIMESTAMP_FIELDS and not SAFE_ENUM_RE.fullmatch(item):
                    raise EvidenceError(f"{child_path} must be a bounded enum, not free text")
            elif not isinstance(item, (bool, int)) or isinstance(item, float):
                raise EvidenceError(f"{child_path} contains an unsupported value")
    else:
        raise EvidenceError("payload must be an object")


def validate_record(record: dict[str, Any]) -> None:
    unknown = set(record) - TOP_LEVEL_FIELDS
    missing = TOP_LEVEL_FIELDS - set(record)
    if unknown or missing:
        raise EvidenceError(
            f"invalid record fields; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    if record["format"] != FORMAT:
        raise EvidenceError("unsupported evidence format")
    _canonical_uuid(record["instance_id"], "instance_id")
    _canonical_uuid(record["chain_id"], "chain_id")
    _canonical_uuid(record["record_id"], "record_id")
    if not isinstance(record["sequence"], int) or isinstance(record["sequence"], bool) or record["sequence"] < 1:
        raise EvidenceError("sequence must be a positive integer")
    if record["record_type"] not in RECORD_TYPES:
        raise EvidenceError("unknown evidence record type")
    _timestamp(record["created_at"], "created_at")
    signer = record["signer"]
    if not isinstance(signer, dict) or set(signer) != SIGNER_FIELDS:
        raise EvidenceError("signer must contain exactly key_id and role")
    if not isinstance(signer["key_id"], str) or not KEY_ID_RE.fullmatch(signer["key_id"]):
        raise EvidenceError("invalid signer key ID")
    if signer["role"] not in SIGNER_ROLES:
        raise EvidenceError("invalid signer role")
    previous = record["previous_record_sha256"]
    if previous != GENESIS and (not isinstance(previous, str) or not SHA256_RE.fullmatch(previous)):
        raise EvidenceError("invalid previous record digest")
    audit_tail = record["management_audit_tail_sha256"]
    if audit_tail is not None and (not isinstance(audit_tail, str) or not SHA256_RE.fullmatch(audit_tail)):
        raise EvidenceError("invalid management audit tail digest")
    _validate_payload(record["payload"])
    canonical_json(record)


def _safe_regular_file(path: Path, *, max_bytes: int = MAX_MANIFEST_BYTES) -> bytes:
    if path.is_symlink():
        raise EvidenceError(f"symlinks are forbidden: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise EvidenceError(f"not a regular file: {path}")
        if info.st_size > max_bytes:
            raise EvidenceError(f"file exceeds size limit: {path}")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > max_bytes:
            raise EvidenceError(f"file exceeds size limit: {path}")
        return raw
    finally:
        os.close(descriptor)


def load_record(path: Path, *, require_canonical: bool = True) -> tuple[dict[str, Any], bytes]:
    raw = _safe_regular_file(path)
    record = load_json_bytes(raw)
    validate_record(record)
    canonical = canonical_json(record)
    if require_canonical and raw != canonical:
        raise EvidenceError(f"record is not canonical JSON: {path}")
    return record, canonical


def sign_file(path: Path, private_key: Path) -> Path:
    _safe_regular_file(path)
    _safe_regular_file(private_key, max_bytes=32 * 1024)
    result = subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-f", str(private_key), "-n", NAMESPACE, str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise EvidenceError("OpenSSH evidence signing failed")
    signature = Path(str(path) + ".sig")
    _safe_regular_file(signature, max_bytes=32 * 1024)
    os.chmod(signature, 0o600)
    return signature


def verify_file(path: Path, signature: Path, public_key: Path) -> None:
    raw = _safe_regular_file(path)
    _safe_regular_file(signature, max_bytes=32 * 1024)
    public = canonical_public_key(_safe_regular_file(public_key, max_bytes=32 * 1024).decode("ascii"))
    expected_key_id = key_id(public)
    with tempfile.TemporaryDirectory(prefix="mp-opt-evidence-verify.") as directory_name:
        allowed = Path(directory_name) / "allowed_signers"
        allowed.write_text(
            f"{expected_key_id} namespaces=\"{NAMESPACE}\" {public}\n",
            encoding="ascii",
        )
        os.chmod(allowed, 0o600)
        result = subprocess.run(
            [
                "ssh-keygen",
                "-Y",
                "verify",
                "-f",
                str(allowed),
                "-I",
                expected_key_id,
                "-n",
                NAMESPACE,
                "-s",
                str(signature),
            ],
            input=raw,
            check=False,
            capture_output=True,
        )
    if result.returncode != 0:
        raise EvidenceError("OpenSSH evidence signature verification failed")


def _atomic_write(path: Path, raw: bytes, mode: int = 0o600) -> None:
    if path.exists() and path.is_symlink():
        raise EvidenceError(f"refusing to replace symlink: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
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
        if hasattr(os, "O_DIRECTORY"):
            directory_descriptor = os.open(
                path.parent, os.O_RDONLY | os.O_DIRECTORY,
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def append_record(
    ledger: Path,
    *,
    instance_id: str,
    chain_id: str,
    record_type: str,
    payload: dict[str, Any],
    private_key: Path,
    public_key: Path,
    management_audit_tail_sha256: str | None = None,
    record_id: str | None = None,
    created_at: str | None = None,
) -> Path:
    """Atomically append one signed record while holding the ledger lock."""

    _canonical_uuid(instance_id, "instance_id")
    _canonical_uuid(chain_id, "chain_id")
    if record_id is not None:
        _canonical_uuid(record_id, "record_id")
    if created_at is not None:
        _timestamp(created_at, "created_at")
    ledger.mkdir(mode=0o700, parents=True, exist_ok=True)
    if ledger.is_symlink() or not ledger.is_dir():
        raise EvidenceError("ledger must be a real directory")
    os.chmod(ledger, 0o700)
    lock_path = ledger / ".append.lock"
    lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    lock_acquired = False
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(lock_descriptor, 0o600)
        else:
            os.chmod(lock_path, 0o600)
        _lock_exclusive(lock_descriptor)
        lock_acquired = True
        records = sorted(ledger.glob("[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_*.json"))
        if record_id is not None:
            existing = list(ledger.glob(f"[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_{record_id}.json"))
            if existing:
                current, _raw = load_record(existing[0])
                if (
                    current["instance_id"] != instance_id
                    or current["chain_id"] != chain_id
                    or current["record_type"] != record_type
                    or current["payload"] != payload
                    or current["management_audit_tail_sha256"] != management_audit_tail_sha256
                ):
                    raise EvidenceError("record ID was already used for different evidence")
                verify_file(existing[0], Path(str(existing[0]) + ".sig"), public_key)
                return existing[0]
        sequence = len(records) + 1
        previous = GENESIS
        if records:
            previous_record, previous_raw = load_record(records[-1])
            if previous_record["sequence"] != len(records):
                raise EvidenceError("existing evidence chain sequence is inconsistent")
            previous = sha256_bytes(previous_raw)
        public = canonical_public_key(_safe_regular_file(public_key, max_bytes=32 * 1024).decode("ascii"))
        record_id = record_id or str(uuid.uuid4())
        record = {
            "format": FORMAT,
            "instance_id": instance_id,
            "chain_id": chain_id,
            "sequence": sequence,
            "record_id": record_id,
            "record_type": record_type,
            "created_at": created_at or utc_now(),
            "signer": {"key_id": key_id(public), "role": "instance"},
            "previous_record_sha256": previous,
            "management_audit_tail_sha256": management_audit_tail_sha256,
            "payload": payload,
        }
        validate_record(record)
        destination = ledger / f"{sequence:012d}_{record_id}.json"
        _atomic_write(destination, canonical_json(record))
        try:
            signature = sign_file(destination, private_key)
            signature_raw = _safe_regular_file(signature, max_bytes=32 * 1024)
            signature.unlink()
            _atomic_write(Path(str(destination) + ".sig"), signature_raw)
            verify_file(destination, Path(str(destination) + ".sig"), public_key)
            _atomic_write(
                ledger / "chain-head.json",
                canonical_json(
                    {
                        "chain_id": chain_id,
                        "head_sha256": sha256_bytes(canonical_json(record)),
                        "instance_id": instance_id,
                        "sequence": sequence,
                    }
                ),
            )
        except Exception:
            destination.unlink(missing_ok=True)
            Path(str(destination) + ".sig").unlink(missing_ok=True)
            raise
        return destination
    finally:
        if lock_acquired:
            _unlock(lock_descriptor)
        os.close(lock_descriptor)


def verify_chain(ledger: Path, public_key: Path) -> dict[str, Any]:
    if ledger.is_symlink() or not ledger.is_dir():
        raise EvidenceError("ledger must be a real directory")
    records = sorted(ledger.glob("[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_*.json"))
    if not records:
        raise EvidenceError("evidence ledger is empty")
    expected_previous = GENESIS
    chain_id: str | None = None
    instance_id: str | None = None
    for sequence, path in enumerate(records, start=1):
        if path.name.split("_", 1)[0] != f"{sequence:012d}":
            raise EvidenceError("evidence filenames are not contiguous")
        record, raw = load_record(path)
        if record["sequence"] != sequence:
            raise EvidenceError("record sequence does not match filename")
        if record["previous_record_sha256"] != expected_previous:
            raise EvidenceError("evidence chain digest mismatch")
        if chain_id is None:
            chain_id = record["chain_id"]
            instance_id = record["instance_id"]
        elif record["chain_id"] != chain_id or record["instance_id"] != instance_id:
            raise EvidenceError("record belongs to another instance or chain")
        signature = Path(str(path) + ".sig")
        verify_file(path, signature, public_key)
        expected_previous = sha256_bytes(raw)
    return {
        "valid": True,
        "records": len(records),
        "instance_id": instance_id,
        "chain_id": chain_id,
        "head_sha256": expected_previous,
    }


def _load_payload(path: Path) -> dict[str, Any]:
    value = load_json_bytes(_safe_regular_file(path))
    _validate_payload(value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("manifest", type=Path)

    digest = subparsers.add_parser("sha256")
    digest.add_argument("manifest", type=Path)

    sign = subparsers.add_parser("sign")
    sign.add_argument("manifest", type=Path)
    sign.add_argument("private_key", type=Path)

    verify = subparsers.add_parser("verify")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("signature", type=Path)
    verify.add_argument("public_key", type=Path)

    append = subparsers.add_parser("append")
    append.add_argument("ledger", type=Path)
    append.add_argument("instance_id")
    append.add_argument("chain_id")
    append.add_argument("record_type", choices=sorted(RECORD_TYPES))
    append.add_argument("payload", type=Path)
    append.add_argument("private_key", type=Path)
    append.add_argument("public_key", type=Path)
    append.add_argument("--management-audit-tail-sha256")
    append.add_argument("--record-id")
    append.add_argument("--created-at")

    chain = subparsers.add_parser("verify-chain")
    chain.add_argument("ledger", type=Path)
    chain.add_argument("public_key", type=Path)

    arguments = parser.parse_args()
    try:
        if arguments.command in {"validate", "sha256"}:
            _record, raw = load_record(arguments.manifest)
            print(sha256_bytes(raw) if arguments.command == "sha256" else "valid")
        elif arguments.command == "sign":
            load_record(arguments.manifest)
            print(sign_file(arguments.manifest, arguments.private_key))
        elif arguments.command == "verify":
            load_record(arguments.manifest)
            verify_file(arguments.manifest, arguments.signature, arguments.public_key)
            print("valid")
        elif arguments.command == "append":
            path = append_record(
                arguments.ledger,
                instance_id=arguments.instance_id,
                chain_id=arguments.chain_id,
                record_type=arguments.record_type,
                payload=_load_payload(arguments.payload),
                private_key=arguments.private_key,
                public_key=arguments.public_key,
                management_audit_tail_sha256=arguments.management_audit_tail_sha256,
                record_id=arguments.record_id,
                created_at=arguments.created_at,
            )
            print(path)
        elif arguments.command == "verify-chain":
            print(json.dumps(verify_chain(arguments.ledger, arguments.public_key), sort_keys=True))
    except (EvidenceError, OSError) as exc:
        parser.exit(1, f"evidence error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
