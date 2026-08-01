"""Restart-safe bridge between workflow rows and signed evidence files."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.governance import stable_instance_id
from app.models.governance import InstanceGovernanceProfile
from app.models.server_setting import ServerSetting
from app.models.evidence import (
    EvidenceChainState,
    EvidenceKey,
    EvidenceOperation,
)


SHA256 = re.compile(r"^[0-9a-f]{64}$")
KEY_ID = re.compile(r"^ek-[0-9a-f]{16}$")
NAMESPACE = "mp-opt-evidence-v1"
EVIDENCE_TRANSACTION_LOCK = 5571046919607735876


class EvidenceUnavailable(RuntimeError):
    """Raised when required evidence cannot be written or verified."""


def lock_evidence_transaction(db: Session) -> None:
    """Serialize evidence mutations for the lifetime of the DB transaction."""

    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": EVIDENCE_TRANSACTION_LOCK},
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def evidence_home() -> Path:
    return Path(settings.EVIDENCE_HOME)


def evidence_tool() -> Path:
    configured = Path(settings.EVIDENCE_TOOL_PATH)
    if configured.is_file():
        return configured
    repository_copy = Path(__file__).resolve().parents[3] / "deploy" / "evidence" / "evidence_manifest.py"
    return repository_copy


def signing_key() -> Path:
    return Path(settings.EVIDENCE_SIGNING_KEY_PATH)


def _canonical_public_key(value: str) -> str:
    parts = value.strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise EvidenceUnavailable("The evidence signing key is not Ed25519")
    return f"{parts[0]} {parts[1]}"


def public_key_id(public_key: str) -> str:
    canonical = _canonical_public_key(public_key)
    return "ek-" + hashlib.sha256(canonical.encode("ascii")).hexdigest()[:16]


def _verify_detached_bytes(*, content: bytes, signature: str, public_key: str) -> None:
    """Verify a bounded receipt signed by the installation evidence key."""

    if len(content) > 64 * 1024 or len(signature.encode("utf-8")) > 32 * 1024:
        raise EvidenceUnavailable("The evidence proof exceeds its size limit")
    if "PRIVATE KEY" in signature:
        raise EvidenceUnavailable("Private keys may not be imported")
    canonical = _canonical_public_key(public_key)
    identifier = public_key_id(canonical)
    with tempfile.TemporaryDirectory(prefix="mp-opt-evidence-proof.") as directory_name:
        directory = Path(directory_name)
        allowed = directory / "allowed_signers"
        signature_path = directory / "proof.sig"
        _atomic_write(
            allowed,
            f'{identifier} namespaces="{NAMESPACE}" {canonical}\n'.encode("ascii"),
        )
        _atomic_write(signature_path, signature.encode("utf-8"))
        result = subprocess.run(
            [
                "ssh-keygen", "-Y", "verify", "-f", str(allowed),
                "-I", identifier, "-n", NAMESPACE, "-s", str(signature_path),
            ],
            input=content,
            check=False,
            capture_output=True,
        )
    if result.returncode != 0:
        raise EvidenceUnavailable("The evidence proof signature is invalid")


def _atomic_write(path: Path, raw: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or path.is_symlink():
        raise EvidenceUnavailable("Evidence paths may not be symbolic links")
    os.chmod(path.parent, 0o700)
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
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def ensure_directories() -> None:
    root = evidence_home()
    if root.is_symlink():
        raise EvidenceUnavailable("The evidence root may not be a symbolic link")
    for name in ("ledger", "public", "requests", "outbox", "exports", "anchors"):
        directory = root / name
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise EvidenceUnavailable("An evidence directory is unsafe")
        os.chmod(directory, 0o700)


def _derive_public_key() -> str:
    key = signing_key()
    if key.is_symlink() or not key.is_file():
        raise EvidenceUnavailable("The instance evidence signing key is unavailable")
    result = subprocess.run(
        ["ssh-keygen", "-y", "-f", str(key)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise EvidenceUnavailable("The instance evidence public key could not be derived")
    return _canonical_public_key(result.stdout)


def management_audit_tail(*, required: bool) -> str | None:
    path = evidence_home() / "public" / "management-audit-head.json"
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            raise EvidenceUnavailable("The management audit head is unsafe")
        document = json.loads(path.read_text(encoding="utf-8"))
        if set(document) != {"format", "tail_sha256", "verified_at"}:
            raise ValueError("unexpected audit head fields")
        digest = document["tail_sha256"]
        if document["format"] != "mp-opt-management-audit-head-v1" or not SHA256.fullmatch(digest):
            raise ValueError("invalid audit head")
        return digest
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        if required:
            raise EvidenceUnavailable("The verified management audit head is unavailable") from exc
        return None


def _mode() -> str:
    return settings.EVIDENCE_MODE


def verify_existing(db: Session) -> EvidenceChainState | None:
    """Verify an existing chain without creating files or database records."""

    state = db.get(EvidenceChainState, 1)
    if state is None:
        raise EvidenceUnavailable("The required evidence chain has not been initialised")
    try:
        ensure_directories()
        derived = _derive_public_key()
        stored = _canonical_public_key(
            (evidence_home() / "public" / "instance_signing_key.pub").read_text(
                encoding="ascii"
            )
        )
        if derived != stored:
            raise EvidenceUnavailable(
                "The instance evidence private and public keys do not match"
            )
        verify_local_chain(db)
    except (OSError, UnicodeError, EvidenceUnavailable) as exc:
        raise EvidenceUnavailable(
            "The existing evidence chain failed startup verification"
        ) from exc
    return state


def _ledger_records() -> list[tuple[Path, dict, str]]:
    records = []
    for path in sorted((evidence_home() / "ledger").glob("[0-9]" * 12 + "_*.json")):
        raw = path.read_bytes()
        records.append((path, json.loads(raw), hashlib.sha256(raw).hexdigest()))
    return records


def chain_contains_digest(digest: str) -> bool:
    """Return whether an exact signed record digest exists in the local chain."""

    if not SHA256.fullmatch(digest):
        return False
    return any(record_digest == digest for _path, _record, record_digest in _ledger_records())


def _payload_without_operation_metadata(payload: dict) -> dict:
    result = dict(payload)
    for field in (
        "evidence_operation_id",
        "evidence_workflow_type",
        "evidence_workflow_id",
        "evidence_operation_type",
    ):
        result.pop(field, None)
    return result


def _operation_payload(operation: EvidenceOperation) -> dict:
    payload = json.loads(operation.payload_json)
    payload.update({
        "evidence_operation_id": operation.operation_id,
        "evidence_workflow_type": operation.workflow_type,
        "evidence_workflow_id": operation.workflow_id,
        "evidence_operation_type": operation.operation_type,
    })
    return payload


def _quarantine_orphaned_tail(
    records: list[tuple[Path, dict, str]],
    retained: list[tuple[Path, dict, str]],
) -> None:
    """Remove a file-only transaction tail from the active signed chain.

    A ledger append happens just before the surrounding database commit. If
    that transaction rolls back, the signature proves only that an append was
    attempted, not that the asserted domain mutation committed. Keep those
    bytes for diagnosis, but never promote them back into database truth.
    """

    if not retained:
        raise EvidenceUnavailable("The evidence genesis is not committed by the database")
    quarantine = evidence_home() / "outbox" / "orphaned" / str(uuid.uuid4())
    quarantine.mkdir(mode=0o700, parents=True)
    for path, _record, _digest in records:
        path.replace(quarantine / path.name)
        signature = Path(str(path) + ".sig")
        if signature.is_file() and not signature.is_symlink():
            signature.replace(quarantine / signature.name)
        else:
            raise EvidenceUnavailable("An orphaned evidence signature is missing")
    _path, last_record, last_digest = retained[-1]
    _atomic_write(
        evidence_home() / "ledger" / "chain-head.json",
        (
            json.dumps(
                {
                    "chain_id": last_record["chain_id"],
                    "head_sha256": last_digest,
                    "instance_id": last_record["instance_id"],
                    "sequence": last_record["sequence"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8"),
    )


def _append_operation_file(
    db: Session,
    state: EvidenceChainState,
    operation: EvidenceOperation,
) -> str:
    if not operation.record_type:
        raise EvidenceUnavailable("The pending evidence operation has no record type")
    ensure_directories()
    payload_file = evidence_home() / "outbox" / f"{operation.operation_id}.json"
    canonical_payload = json.dumps(
        _operation_payload(operation), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    _atomic_write(payload_file, (canonical_payload + "\n").encode("utf-8"))
    command = [
        sys.executable, str(evidence_tool()), "append", str(evidence_home() / "ledger"),
        state.instance_id, state.chain_id, operation.record_type, str(payload_file),
        str(signing_key()), str(evidence_home() / "public" / "instance_signing_key.pub"),
        "--record-id", operation.record_id,
    ]
    if operation.management_audit_tail_sha256:
        command.extend([
            "--management-audit-tail-sha256",
            operation.management_audit_tail_sha256,
        ])
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    finally:
        payload_file.unlink(missing_ok=True)
    if result.returncode != 0:
        raise EvidenceUnavailable("The signed evidence record could not be appended")
    record_path = Path(result.stdout.strip())
    raw = record_path.read_bytes()
    record = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    operation.state = "complete"
    operation.record_sha256 = digest
    operation.error_code = None
    state.last_sequence = int(record["sequence"])
    state.head_sha256 = digest
    state.verified_at = datetime.now(timezone.utc)
    return digest


def reconcile_local_chain(db: Session) -> dict:
    """Recover provable interrupted appends, then strictly verify DB and ledger."""

    lock_evidence_transaction(db)
    state = db.get(EvidenceChainState, 1)
    if state is None:
        raise EvidenceUnavailable("Evidence has not been initialised")
    ensure_directories()
    derived = _derive_public_key()
    stored = _canonical_public_key(
        (evidence_home() / "public" / "instance_signing_key.pub").read_text(
            encoding="ascii"
        )
    )
    if derived != stored:
        raise EvidenceUnavailable(
            "The instance evidence private and public keys do not match"
        )
    # First verify signatures and chain linkage without trusting operation metadata.
    result = subprocess.run(
        [
            sys.executable, str(evidence_tool()), "verify-chain",
            str(evidence_home() / "ledger"),
            str(evidence_home() / "public" / "instance_signing_key.pub"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise EvidenceUnavailable("The local evidence chain is invalid")

    present: set[str] = set()
    retained: list[tuple[Path, dict, str]] = []
    orphaned: list[tuple[Path, dict, str]] = []
    for path, record, digest in _ledger_records():
        record_id = record.get("record_id")
        payload = record.get("payload")
        if not isinstance(record_id, str) or not isinstance(payload, dict):
            raise EvidenceUnavailable("The evidence ledger contains an invalid record")
        present.add(record_id)
        operation = db.query(EvidenceOperation).filter(
            EvidenceOperation.record_id == record_id,
        ).first()
        metadata = {
            "operation_id": payload.get("evidence_operation_id"),
            "workflow_type": payload.get("evidence_workflow_type"),
            "workflow_id": payload.get("evidence_workflow_id"),
            "operation_type": payload.get("evidence_operation_type"),
        }
        if operation is None:
            orphaned.append((path, record, digest))
            continue
        else:
            if orphaned:
                raise EvidenceUnavailable(
                    "A committed evidence record depends on an uncommitted ledger tail"
                )
            original_payload = json.loads(operation.payload_json)
            record_payload = (
                _payload_without_operation_metadata(payload)
                if metadata["operation_id"] is not None
                else payload
            )
            if (
                original_payload != record_payload
                or (operation.record_type and operation.record_type != record.get("record_type"))
                or (metadata["operation_id"] is not None and metadata != {
                    "operation_id": operation.operation_id,
                    "workflow_type": operation.workflow_type,
                    "workflow_id": operation.workflow_id,
                    "operation_type": operation.operation_type,
                })
            ):
                raise EvidenceUnavailable("Evidence operation content does not match the ledger")
            operation.record_type = record.get("record_type")
            operation.management_audit_tail_sha256 = record.get(
                "management_audit_tail_sha256"
            )
            operation.state = "complete"
            operation.record_sha256 = digest
            operation.error_code = None
            retained.append((path, record, digest))

    if orphaned:
        _quarantine_orphaned_tail(orphaned, retained)

    db.flush()
    for operation in db.query(EvidenceOperation).order_by(EvidenceOperation.id).all():
        if operation.record_id in present:
            continue
        if operation.state not in {"pending", "failed"}:
            raise EvidenceUnavailable("A committed evidence record is missing from the ledger")
        _append_operation_file(db, state, operation)

    return verify_local_chain(db)


def _recover_uncommitted_initialisation(db: Session) -> EvidenceChainState | None:
    ledger = evidence_home() / "ledger"
    if not ledger.is_dir() or not any(ledger.glob("[0-9]" * 12 + "_*.json")):
        return None
    public = _derive_public_key()
    public_path = evidence_home() / "public" / "instance_signing_key.pub"
    _atomic_write(public_path, (public + "\n").encode("ascii"))
    result = subprocess.run(
        [sys.executable, str(evidence_tool()), "verify-chain", str(ledger), str(public_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise EvidenceUnavailable("The interrupted evidence initialisation is invalid")
    chain = json.loads(result.stdout)
    _first_path, first, first_digest = _ledger_records()[0]
    if first.get("record_type") != "instance.initialised":
        raise EvidenceUnavailable("The evidence ledger has no valid initialisation record")
    recovered_instance_id = str(chain["instance_id"])
    configured_id = settings.MP_INSTANCE_ID or None
    profile = db.get(InstanceGovernanceProfile, 1)
    setting = db.query(ServerSetting).filter(ServerSetting.key == "instance_id").first()
    known_id = configured_id or (profile.instance_id if profile else None) or (setting.value if setting else None)
    if known_id is not None and known_id != recovered_instance_id:
        raise EvidenceUnavailable("The evidence ledger belongs to another instance")
    if setting is None and not configured_id and profile is None:
        db.add(ServerSetting(key="instance_id", value=recovered_instance_id))
    state = EvidenceChainState(
        id=1,
        instance_id=recovered_instance_id,
        chain_id=str(chain["chain_id"]),
        evidence_mode=_mode(),
    )
    db.add(state)
    digest = hashlib.sha256(public.encode("ascii")).hexdigest()
    db.add(EvidenceKey(
        key_id=public_key_id(public),
        public_key=public,
        public_key_sha256=digest,
        instance_id=recovered_instance_id,
        entity_id=None,
        algorithm="Ed25519",
        role="instance",
        activated_at=datetime.now(timezone.utc),
    ))
    payload = first.get("payload")
    if not isinstance(payload, dict):
        raise EvidenceUnavailable("The evidence initialisation payload is invalid")
    metadata = {
        "operation_id": payload.get("evidence_operation_id"),
        "workflow_type": payload.get("evidence_workflow_type"),
        "workflow_id": payload.get("evidence_workflow_id"),
        "operation_type": payload.get("evidence_operation_type"),
    }
    if not all(isinstance(value, str) and value for value in metadata.values()):
        raise EvidenceUnavailable("The interrupted initialisation has no recovery metadata")
    if _deterministic_record_id(
        state.chain_id,
        metadata["workflow_type"],
        metadata["workflow_id"],
        metadata["operation_type"],
    ) != first.get("record_id"):
        raise EvidenceUnavailable("The interrupted initialisation metadata is inconsistent")
    db.add(EvidenceOperation(
        operation_id=metadata["operation_id"],
        record_id=first["record_id"],
        workflow_type=metadata["workflow_type"],
        workflow_id=metadata["workflow_id"],
        operation_type=metadata["operation_type"],
        record_type=first["record_type"],
        payload_json=json.dumps(
            _payload_without_operation_metadata(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        management_audit_tail_sha256=first.get("management_audit_tail_sha256"),
        state="complete",
        record_sha256=first_digest,
    ))
    db.flush()
    reconcile_local_chain(db)
    return state


def initialise(db: Session) -> EvidenceChainState | None:
    """Initialise the mandatory instance evidence chain."""

    state = db.get(EvidenceChainState, 1)
    if state is not None:
        try:
            reconcile_local_chain(db)
        except (OSError, UnicodeError, EvidenceUnavailable) as exc:
            raise EvidenceUnavailable(
                "The existing evidence chain failed startup verification"
            ) from exc
        return state
    try:
        ensure_directories()
        public = _derive_public_key()
    except (OSError, EvidenceUnavailable):
        raise
    recovered = _recover_uncommitted_initialisation(db)
    if recovered is not None:
        return recovered
    instance_id = stable_instance_id(db)
    state = EvidenceChainState(
        id=1,
        instance_id=instance_id,
        chain_id=str(uuid.uuid4()),
        evidence_mode=_mode(),
    )
    db.add(state)
    digest = hashlib.sha256(public.encode("ascii")).hexdigest()
    key = EvidenceKey(
        key_id=public_key_id(public),
        public_key=public,
        public_key_sha256=digest,
        instance_id=instance_id,
        entity_id=None,
        algorithm="Ed25519",
        role="instance",
        activated_at=datetime.now(timezone.utc),
    )
    db.add(key)
    db.flush()
    _atomic_write(evidence_home() / "public" / "instance_signing_key.pub", (public + "\n").encode("ascii"))
    result = append_record(
        db,
        workflow_type="instance",
        workflow_id=instance_id,
        operation_type="initialised",
        record_type="instance.initialised",
        payload={"status": "initialised", "key_id": key.key_id, "public_key_sha256": digest},
        allow_missing_audit=True,
    )
    if result is None:
        raise EvidenceUnavailable("The required evidence chain could not be initialised")
    return state


def _deterministic_record_id(chain_id: str, workflow_type: str, workflow_id: str, operation_type: str) -> str:
    return str(uuid.uuid5(uuid.UUID(chain_id), f"{workflow_type}:{workflow_id}:{operation_type}"))


def append_record(
    db: Session,
    *,
    workflow_type: str,
    workflow_id: str,
    operation_type: str,
    record_type: str,
    payload: dict,
    allow_missing_audit: bool = False,
) -> str | None:
    """Append one idempotent instance-signed record and return its digest."""

    lock_evidence_transaction(db)
    state = db.get(EvidenceChainState, 1)
    if state is None:
        state = initialise(db)
    elif any((evidence_home() / "ledger").glob("[0-9]" * 12 + "_*.json")):
        reconcile_local_chain(db)
    if state is None:
        raise EvidenceUnavailable("Required evidence has not been initialised")
    existing = db.query(EvidenceOperation).filter(
        EvidenceOperation.workflow_type == workflow_type,
        EvidenceOperation.workflow_id == workflow_id,
        EvidenceOperation.operation_type == operation_type,
    ).first()
    canonical_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if existing is not None:
        if existing.payload_json != canonical_payload:
            raise EvidenceUnavailable("An evidence operation was replayed with different content")
        if existing.record_type and existing.record_type != record_type:
            raise EvidenceUnavailable("An evidence operation was replayed with another record type")
        if existing.state == "complete" and existing.record_sha256:
            return existing.record_sha256
        operation = existing
    else:
        operation = EvidenceOperation(
            operation_id=str(uuid.uuid4()),
            record_id=_deterministic_record_id(state.chain_id, workflow_type, workflow_id, operation_type),
            workflow_type=workflow_type,
            workflow_id=workflow_id,
            operation_type=operation_type,
            record_type=record_type,
            payload_json=canonical_payload,
            state="pending",
        )
        db.add(operation)
        db.flush()

    try:
        audit_tail = management_audit_tail(required=not allow_missing_audit)
        operation.record_type = record_type
        operation.management_audit_tail_sha256 = audit_tail
        return _append_operation_file(db, state, operation)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, EvidenceUnavailable) as exc:
        operation.state = "failed"
        operation.error_code = "evidence_append_failed"
        raise EvidenceUnavailable("Required evidence could not be appended") from exc


def verify_local_chain(db: Session) -> dict:
    """Run the strict local verifier and refresh the database cache."""

    state = db.get(EvidenceChainState, 1)
    if state is None:
        raise EvidenceUnavailable("Evidence has not been initialised")
    result = subprocess.run(
        [
            sys.executable, str(evidence_tool()), "verify-chain",
            str(evidence_home() / "ledger"),
            str(evidence_home() / "public" / "instance_signing_key.pub"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise EvidenceUnavailable("The local evidence chain is invalid")
    document = json.loads(result.stdout)
    if (
        document.get("instance_id") != state.instance_id
        or document.get("chain_id") != state.chain_id
    ):
        raise EvidenceUnavailable("The evidence ledger belongs to another database state")
    public = _canonical_public_key(
        (evidence_home() / "public" / "instance_signing_key.pub").read_text(
            encoding="ascii"
        )
    )
    public_digest = hashlib.sha256(public.encode("ascii")).hexdigest()
    instance_key = db.query(EvidenceKey).filter(
        EvidenceKey.role == "instance",
        EvidenceKey.key_id == public_key_id(public),
        EvidenceKey.public_key_sha256 == public_digest,
        EvidenceKey.revoked_at.is_(None),
    ).first()
    if instance_key is None:
        raise EvidenceUnavailable("The evidence ledger key is not registered by this database")
    records: dict[str, str] = {}
    for path in sorted((evidence_home() / "ledger").glob("[0-9]" * 12 + "_*.json")):
        raw = path.read_bytes()
        record = json.loads(raw)
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or record_id in records:
            raise EvidenceUnavailable("The evidence ledger has duplicate or invalid record IDs")
        records[record_id] = hashlib.sha256(raw).hexdigest()
    committed = {
        operation.record_id: operation.record_sha256
        for operation in db.query(EvidenceOperation).filter(
            EvidenceOperation.state == "complete",
        )
    }
    if records != committed:
        raise EvidenceUnavailable(
            "The evidence ledger and committed database operations do not match"
        )
    state.last_sequence = int(document["records"])
    state.head_sha256 = document["head_sha256"]
    state.verified_at = datetime.now(timezone.utc)
    return document
