"""Request and observe host-managed HA replication without exposing SSH to the API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import uuid

from app.core.config import settings
from app.core.ha_witness import HAWritePermitError, witness_post
from app.models.ha import HAProtectionOperation
from sqlalchemy import func, text
from sqlalchemy.orm import Session


_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FINAL_STATES = {"accepted", "failed", "cancelled"}
_LOGGER = logging.getLogger("ha.protection")


class HAProtectionQueueError(RuntimeError):
    """A bounded infrastructure error raised before a protected commit."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class HAProtectionResult:
    protected: bool
    job_id: str | None = None
    error_code: str | None = None
    bundle_id: str | None = None
    bundle_sha256: str | None = None
    generation: int | None = None
    accepted_at: datetime | None = None


def protection_marker(operation: HAProtectionOperation) -> dict:
    """Return the bounded database marker embedded in a replication request."""

    marker = {
        "operation_id": operation.id,
        "mutation_sequence": int(operation.mutation_sequence),
        "operation_type": operation.operation_type,
        "resource_type": operation.resource_type,
        "resource_id": operation.resource_id,
    }
    canonical = json.dumps(marker, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {**marker, "marker_sha256": hashlib.sha256(canonical).hexdigest()}


def find_protection_operation(db: Session, idempotency_key: str) -> HAProtectionOperation | None:
    if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise ValueError("invalid_idempotency_key")
    return (
        db.query(HAProtectionOperation)
        .filter(HAProtectionOperation.idempotency_key == idempotency_key)
        .first()
    )


def protection_queue_error() -> str | None:
    """Return a safe error code for the backend-visible HA request queue."""

    if settings.HA_MODE != "ha":
        return None
    request_dir = Path(settings.HA_REPLICATION_REQUEST_DIR)
    try:
        if request_dir.is_symlink():
            return "replication_queue_unsafe"
        if not request_dir.exists():
            return "replication_queue_missing"
        if not request_dir.is_dir():
            return "replication_queue_unsafe"
        if not os.access(request_dir, os.W_OK | os.X_OK):
            return "replication_queue_not_writable"
    except OSError:
        return "replication_queue_unsafe"
    return None


def require_protection_queue_ready() -> None:
    """Prove an atomic write before opening a witness guard or transaction."""

    code = protection_queue_error()
    if code is not None:
        raise HAProtectionQueueError(code)
    request_dir = Path(settings.HA_REPLICATION_REQUEST_DIR)
    probe_id = str(uuid.uuid4())
    temporary = request_dir / f".{probe_id}.permission-probe.tmp"
    target = request_dir / f".{probe_id}.permission-probe"
    try:
        temporary.write_text("{}\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(target)
        target.unlink()
    except PermissionError as exc:
        raise HAProtectionQueueError("replication_queue_not_writable") from exc
    except FileNotFoundError as exc:
        raise HAProtectionQueueError("replication_queue_missing") from exc
    except OSError as exc:
        raise HAProtectionQueueError("replication_queue_atomic_write_failed") from exc
    finally:
        for candidate in (temporary, target):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass


def create_protection_operation(
    db: Session,
    *,
    idempotency_key: str,
    operation_type: str,
    resource_type: str,
    resource_id: str | None,
) -> HAProtectionOperation | None:
    """Create a pending marker in the caller's mutation transaction."""

    if settings.HA_MODE != "ha":
        return None
    existing = find_protection_operation(db, idempotency_key)
    if existing is not None:
        return existing
    require_protection_queue_ready()
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,63}", operation_type):
        raise ValueError("invalid_operation_type")
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,63}", resource_type):
        raise ValueError("invalid_resource_type")
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        mutation_sequence = int(
            db.execute(text("SELECT nextval('ha_protection_mutation_sequence')")).scalar_one()
        )
    else:
        # SQLite is used by the isolated API tests and has no sequence support.
        # Production remains concurrency-safe through the PostgreSQL sequence.
        mutation_sequence = int(
            db.query(func.coalesce(func.max(HAProtectionOperation.mutation_sequence), 0)).scalar()
        ) + 1
    operation = HAProtectionOperation(
        id=str(uuid.uuid4()),
        idempotency_key=idempotency_key,
        operation_type=operation_type,
        resource_type=resource_type,
        resource_id=str(resource_id)[:128] if resource_id is not None else None,
        mutation_sequence=mutation_sequence,
        state="pending",
        stage="queued",
    )
    db.add(operation)
    db.flush()
    witness_post(
        f"/v1/clusters/{settings.HA_CLUSTER_ID}/critical-begin",
        {
            "node_id": settings.HA_NODE_ID,
            "operation_id": operation.id,
            "mutation_sequence": int(operation.mutation_sequence),
        },
    )
    return operation


def cancel_uncommitted_protection(operation: HAProtectionOperation | None) -> None:
    """Close a witness guard only when the caller proves its transaction did not commit."""

    if operation is None or settings.HA_MODE != "ha":
        return
    try:
        witness_post(
            f"/v1/clusters/{settings.HA_CLUSTER_ID}/critical-cancel",
            {"node_id": settings.HA_NODE_ID, "operation_id": operation.id},
        )
    except HAWritePermitError:
        pass


def queue_protection_operation(
    operation: HAProtectionOperation | None,
    *,
    privacy_assertion: dict | None = None,
) -> str | None:
    """Atomically queue a committed operation marker for host replication."""

    if operation is None:
        return None
    marker = protection_marker(operation)
    request_dir = Path(settings.HA_REPLICATION_REQUEST_DIR)
    temporary: Path | None = None
    try:
        require_protection_queue_ready()
        temporary = request_dir / f".{operation.id}.{uuid.uuid4()}.tmp"
        target = request_dir / f"{operation.id}.json"
        document = {
            "format": "mp-opt-replication-request-v2",
            "job_id": operation.id,
            "reason": operation.operation_type,
            "critical": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "operation": marker,
        }
        if privacy_assertion is not None:
            document["privacy_assertion"] = privacy_assertion
        temporary.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
        temporary.chmod(0o644)
        temporary.replace(target)
        return None
    except HAProtectionQueueError as exc:
        code = exc.code
    except PermissionError:
        code = "replication_queue_not_writable"
    except FileNotFoundError:
        code = "replication_queue_missing"
    except OSError:
        code = "replication_queue_atomic_write_failed"
    if temporary is not None:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    _LOGGER.error(json.dumps({
        "event": "ha.protection.queue_failed",
        "operation_id": operation.id,
        "error_code": code,
    }, sort_keys=True))
    return code


def sync_protection_operation(db: Session, operation: HAProtectionOperation) -> HAProtectionOperation:
    """Apply a schema-bound public result receipt to the authoritative row."""

    if operation.state in _FINAL_STATES or settings.HA_MODE != "ha":
        return operation
    receipt = (
        Path(settings.HA_REPLICATION_REQUEST_DIR).parent
        / "ha-operation-results"
        / f"{operation.id}.json"
    )
    try:
        document = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return operation
    allowed = {
        "format", "operation_id", "state", "stage", "mutation_sequence",
        "bundle_id", "bundle_sha256", "generation", "error_code",
        "updated_at", "accepted_at",
    }
    required = {
        "format", "operation_id", "state", "stage", "mutation_sequence",
        "updated_at",
    }
    if (
        set(document) - allowed
        or not required.issubset(document)
        or document.get("format") != "mp-opt-ha-operation-result-v1"
    ):
        return operation
    if document.get("operation_id") != operation.id:
        return operation
    if document.get("mutation_sequence") != int(operation.mutation_sequence):
        return operation
    state = document.get("state")
    stage = document.get("stage")
    if state not in {"pending", "accepted", "indeterminate", "failed"}:
        return operation
    if stage not in {
        "queued", "capturing", "transferring", "verifying", "accepted", "attention_required",
    }:
        return operation
    error_code = document.get("error_code")
    if error_code is not None and (
        not isinstance(error_code, str) or not _SAFE_CODE.fullmatch(error_code)
    ):
        return operation
    accepted_values: tuple[str, str, int, datetime] | None = None
    if state == "accepted":
        bundle_id = document.get("bundle_id")
        bundle_hash = document.get("bundle_sha256")
        generation = document.get("generation")
        if not isinstance(bundle_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", bundle_id):
            return operation
        if not isinstance(bundle_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", bundle_hash):
            return operation
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            return operation
        try:
            accepted_at = datetime.fromisoformat(
                str(document.get("accepted_at", "")).replace("Z", "+00:00")
            )
        except ValueError:
            return operation
        if accepted_at.tzinfo is None:
            return operation
        accepted_values = (bundle_id, bundle_hash, generation, accepted_at)
    operation.state = state
    operation.stage = stage
    operation.error_code = error_code
    if accepted_values is not None:
        (
            operation.accepted_bundle_id,
            operation.accepted_bundle_sha256,
            operation.accepted_generation,
            operation.accepted_at,
        ) = accepted_values
        if operation.operation_type == "publisher-secret-import" and operation.resource_id:
            # Imported activation credentials are created in the same local
            # transaction but remain unusable until the exact event bundle is
            # accepted. This unlock is idempotent and contains no raw token.
            from app.models.user import ActivationLink, User

            links = (
                db.query(ActivationLink)
                .join(User, User.id == ActivationLink.user_id)
                .filter(
                    User.event_id == int(operation.resource_id),
                    ActivationLink.delivery_pending.is_(True),
                )
                .all()
            )
            for link in links:
                link.delivery_pending = False
    operation.updated_at = datetime.now(timezone.utc)
    db.flush()
    return operation


def request_ha_replication(
    reason: str,
    *,
    critical: bool = False,
    privacy_assertion: dict | None = None,
) -> str | None:
    if settings.HA_MODE != "ha":
        return None
    job_id = str(uuid.uuid4())
    request_dir = Path(settings.HA_REPLICATION_REQUEST_DIR)
    try:
        require_protection_queue_ready()
        temporary = request_dir / f".{job_id}.{uuid.uuid4()}.tmp"
        target = request_dir / f"{job_id}.json"
        document = {
            "format": "mp-opt-replication-request-v1",
            "job_id": job_id,
            "reason": reason,
            "critical": critical,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if privacy_assertion is not None:
            document["privacy_assertion"] = privacy_assertion
        temporary.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
        temporary.chmod(0o644)
        temporary.replace(target)
    except (HAProtectionQueueError, OSError):
        return None
    return job_id
