"""Strict deletion-case work orders, reports and signed checklists."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.evidence import append_record
from app.core.governance import stable_instance_id
from app.models.deletion import (
    DeletionChecklistApproval,
    DeletionCase,
    DeletionRequiredProcessor,
    DeletionSubjectScope,
    DesktopDeletionWorkOrder,
)
from app.models.evidence import BackupInventoryRecord, EvidenceKey, ProcessorIdentity
from app.models.event import Event


CHECKLIST_VERSION = 3
CLAIM_TTL_MINUTES = 30
STATUS_CAPABILITY_TTL_DAYS = 90
_REPORT_COUNTERS = {
    "persons",
    "assignments",
    "capability_links",
    "group_memberships",
    "unavailability_intervals",
    "task_references",
    "optimisation_records",
    "publish_records",
    "cached_records",
    "tracked_exports",
    "integration_references",
}


def create_event_erasure_case(
    db: Session,
    event: Event,
    *,
    initiation_reason: str,
    now: datetime | None = None,
) -> DeletionCase:
    """Create or return the sole signed whole-event deletion case.

    The unique event purge key is the database-level idempotency boundary used
    by both the authenticated root route and the automatic retention cycle.
    """

    existing = db.query(DeletionCase).filter(
        DeletionCase.event_purge_key == event.evidence_id,
    ).first()
    if existing is not None:
        return existing
    now = now or utc_now()
    job = DeletionCase(
        case_type="event_erasure",
        initiation_reason=initiation_reason,
        event_purge_key=event.evidence_id,
        instance_id=stable_instance_id(db),
        event_evidence_id=event.evidence_id,
        event_display_name=event.name,
        subject_evidence_id=event.evidence_id,
        user_id=None,
        state="submitted",
        normal_response_due_at=now + timedelta(days=30),
    )
    db.add(job)
    db.flush()
    job.request_manifest_sha256 = append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=job.request_id,
        operation_type="requested",
        record_type="deletion.event_requested",
        payload={
            "case_id": job.request_id,
            "event_ref": event.evidence_id,
            "case_type": "event_erasure",
            "initiation_reason": initiation_reason,
            "normal_response_due_at": job.normal_response_due_at.strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "status": "submitted",
        },
    )
    return job


def utc_now() -> datetime:
    """Return the current UTC time."""

    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    """Serialise one bounded evidence object deterministically."""

    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True)


def sha256_text(value: str) -> str:
    """Return the lower-case SHA-256 digest of UTF-8 text."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_capability(raw: str) -> str:
    """Hash a bearer capability before database storage."""

    return sha256_text(raw)


def issue_status_capability(case: DeletionCase) -> str:
    """Issue a one-time status capability before account access is revoked."""

    if case.status_capability_sha256:
        raise ValueError("A status capability has already been issued")
    raw = secrets.token_urlsafe(32)
    case.status_capability_sha256 = hash_capability(raw)
    case.status_capability_expires_at = utc_now() + timedelta(days=STATUS_CAPABILITY_TTL_DAYS)
    return raw


def verify_status_capability(case: DeletionCase, raw: str) -> bool:
    """Verify an unexpired case-status capability in constant time."""

    expires = case.status_capability_expires_at
    if not raw or not case.status_capability_sha256 or expires is None:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return utc_now() <= expires and secrets.compare_digest(
        case.status_capability_sha256,
        hash_capability(raw),
    )


def ensure_case_scope(
    db: Session,
    case: DeletionCase,
    *,
    event: Event,
    subject_ref: str | None,
) -> DeletionSubjectScope:
    """Create or return one exact pseudonymous case scope."""

    existing = db.query(DeletionSubjectScope).filter(
        DeletionSubjectScope.case_id == case.id,
        DeletionSubjectScope.event_ref == event.evidence_id,
        DeletionSubjectScope.subject_ref == subject_ref,
    ).first()
    if existing:
        return existing
    scope = DeletionSubjectScope(
        case_id=case.id,
        event_id=event.id,
        event_ref=event.evidence_id,
        subject_ref=subject_ref,
    )
    db.add(scope)
    db.flush()
    return scope


def ensure_desktop_work_order(
    db: Session,
    case: DeletionCase,
    *,
    event: Event,
    subject_ref: str | None,
) -> list[DesktopDeletionWorkOrder]:
    """Snapshot active event processors and create one work order per entity.

    A Desktop receipt is inapplicable only when this event has never had a
    processor identity.  Historical or pending identities prove that Desktop
    linking occurred, so an unavailable active key must block instead of being
    mistaken for proof that no Desktop copy exists.
    """

    ensure_case_scope(db, case, event=event, subject_ref=subject_ref)
    known_identities = db.query(ProcessorIdentity).filter(
        ProcessorIdentity.event_evidence_id == event.evidence_id,
    ).order_by(ProcessorIdentity.entity_id).all()
    identities = [identity for identity in known_identities if (
        identity.status == "active" and identity.active_key_id is not None
    )]
    if not known_identities:
        case.desktop_deletion_required = False
        return []
    if not identities:
        case.desktop_deletion_required = True
        raise ValueError(
            "This event was linked to Desktop, but no active processor key is "
            "available to complete its deletion receipt"
        )
    case.desktop_deletion_required = True
    result: list[DesktopDeletionWorkOrder] = []
    for identity in identities:
        active_key = db.query(EvidenceKey).filter(
            EvidenceKey.key_id == identity.active_key_id,
            EvidenceKey.entity_id == identity.entity_id,
            EvidenceKey.role == "processor",
            EvidenceKey.activated_at.isnot(None),
            EvidenceKey.revoked_at.is_(None),
        ).first()
        if active_key is None:
            raise ValueError("An active processor assignment has no verifiable public key")
        requirement = db.query(DeletionRequiredProcessor).filter(
            DeletionRequiredProcessor.case_id == case.id,
            DeletionRequiredProcessor.event_ref == event.evidence_id,
            DeletionRequiredProcessor.processor_entity_id == identity.entity_id,
        ).first()
        if requirement is None:
            requirement = DeletionRequiredProcessor(
                case_id=case.id, event_ref=event.evidence_id,
                processor_entity_id=identity.entity_id,
                snapshotted_key_id=str(identity.active_key_id),
                snapshotted_public_key_sha256=active_key.public_key_sha256,
            )
            db.add(requirement)
        work_order = db.query(DesktopDeletionWorkOrder).filter(
            DesktopDeletionWorkOrder.case_id == case.id,
            DesktopDeletionWorkOrder.event_ref == event.evidence_id,
            DesktopDeletionWorkOrder.subject_ref == subject_ref,
            DesktopDeletionWorkOrder.processor_entity_id == identity.entity_id,
        ).first()
        if work_order is None:
            work_order = DesktopDeletionWorkOrder(
                case_id=case.id, event_id=event.id, event_ref=event.evidence_id,
                subject_ref=subject_ref, processor_entity_id=identity.entity_id,
                processor_key_id=identity.active_key_id,
                operation="delete_event" if case.case_type == "event_erasure" else "delete_subject",
            )
            db.add(work_order)
            db.flush()
            payload = {
                "case_id": case.request_id, "work_order_id": work_order.work_order_id,
                "event_ref": work_order.event_ref, "processor_entity_id": identity.entity_id,
                "processor_key_id": identity.active_key_id,
                "operation": work_order.operation, "status": "open",
            }
            if work_order.subject_ref is not None: payload["subject_ref"] = work_order.subject_ref
            append_record(
                db, workflow_type="deletion_case", workflow_id=case.request_id,
                operation_type=f"desktop_work_order_created_{work_order.work_order_id}",
                record_type="deletion.desktop_work_order_created", payload=payload,
            )
        result.append(work_order)
    return result


def claim_work_order(work_order: DesktopDeletionWorkOrder) -> str:
    """Claim a work order and return its raw, short-lived report capability."""

    if work_order.state == "report_received":
        raise ValueError("The work order is already complete")
    now = utc_now()
    expires = work_order.claim_expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if work_order.state == "claimed" and expires and expires > now:
        raise ValueError("The work order already has an active claim")
    raw = secrets.token_urlsafe(32)
    work_order.claim_capability_sha256 = hash_capability(raw)
    work_order.claimed_at = now
    work_order.claim_expires_at = now + timedelta(minutes=CLAIM_TTL_MINUTES)
    work_order.state = "claimed"
    return raw


def validate_report_payload(work_order: DesktopDeletionWorkOrder, report: dict[str, Any]) -> None:
    """Validate a privacy-safe desktop deletion report."""

    if set(report) != {
        "format", "instance_id", "entity_id", "key_id", "role", "algorithm",
        "public_key_sha256",
        "work_order_id",
        "event_ref",
        "subject_ref",
        "operation",
        "outcome",
        "deleted_counts",
        "outstanding_actions",
        "completed_at",
    }:
        raise ValueError("The desktop report contains missing or unknown fields")
    if report["format"] != "mp-opt-desktop-deletion-receipt-v2":
        raise ValueError("Unsupported Desktop deletion receipt version")
    for key, expected in (
        ("work_order_id", work_order.work_order_id),
        ("event_ref", work_order.event_ref),
        ("subject_ref", work_order.subject_ref),
        ("operation", work_order.operation),
    ):
        if report[key] != expected:
            raise ValueError(f"The desktop report {key} does not match its work order")
    if report["outcome"] != "deleted":
        raise ValueError("The desktop report does not confirm deletion")
    counts = report["deleted_counts"]
    if not isinstance(counts, dict) or set(counts) != _REPORT_COUNTERS:
        raise ValueError("The desktop report counters do not match the current contract")
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise ValueError("Desktop deletion counters must be non-negative integers")
    outstanding = report["outstanding_actions"]
    if not isinstance(outstanding, list) or any(
        item not in {"untracked_external_export", "external_integration_copy"}
        for item in outstanding
    ):
        raise ValueError("The desktop report contains an unsupported outstanding action")
    try:
        completed = datetime.fromisoformat(report["completed_at"])
    except (TypeError, ValueError) as exc:
        raise ValueError("The desktop report completion time is invalid") from exc
    if completed.tzinfo is None:
        raise ValueError("The desktop report completion time must include a UTC offset")


def apply_desktop_report(
    db: Session,
    case: DeletionCase,
    work_order: DesktopDeletionWorkOrder,
    *,
    claim_capability: str,
    report: dict[str, Any],
    signature_sha256: str,
    evidence_package_json: str,
    evidence_package_sha256: str,
    completed_key_id: str,
    completed_public_key_sha256: str,
) -> str:
    """Apply an idempotent, capability-authorised desktop deletion report."""

    canonical = canonical_json(report)
    digest = sha256_text(canonical)
    if work_order.report_sha256:
        if (
            work_order.report_sha256 != digest
            or work_order.report_evidence_package_sha256 != evidence_package_sha256
        ):
            raise ValueError("A different report is already recorded for this work order")
        return digest
    expires = work_order.claim_expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if (
        work_order.state != "claimed"
        or not work_order.claim_capability_sha256
        or not expires
        or utc_now() > expires
        or not secrets.compare_digest(work_order.claim_capability_sha256, hash_capability(claim_capability))
    ):
        raise ValueError("The work-order claim is missing, expired or invalid")
    validate_report_payload(work_order, report)
    now = utc_now()
    work_order.report_json = canonical
    work_order.report_sha256 = digest
    work_order.report_signature_sha256 = signature_sha256
    work_order.report_evidence_package_json = evidence_package_json
    work_order.report_evidence_package_sha256 = evidence_package_sha256
    work_order.processor_key_id = completed_key_id
    work_order.reported_at = now
    work_order.claim_capability_sha256 = None
    work_order.claim_expires_at = None
    work_order.state = "report_received"
    scope = db.query(DeletionSubjectScope).filter(
        DeletionSubjectScope.case_id == case.id,
        DeletionSubjectScope.event_ref == work_order.event_ref,
        DeletionSubjectScope.subject_ref == work_order.subject_ref,
    ).one()
    scope.state = "desktop_deleted"
    case.desktop_report_sha256 = digest
    requirement = db.query(DeletionRequiredProcessor).filter(
        DeletionRequiredProcessor.case_id == case.id,
        DeletionRequiredProcessor.event_ref == work_order.event_ref,
        DeletionRequiredProcessor.processor_entity_id == work_order.processor_entity_id,
    ).one()
    requirement.deletion_receipt_sha256 = digest
    requirement.completed_key_id = completed_key_id
    requirement.completed_public_key_sha256 = completed_public_key_sha256
    requirement.state = "deletion_received"
    existing_actions = set(json.loads(case.outstanding_actions_json or "[]"))
    existing_actions.update(report["outstanding_actions"])
    case.outstanding_actions_json = canonical_json(sorted(existing_actions))
    db.flush()
    pending_deletions = db.query(DeletionRequiredProcessor).filter(
        DeletionRequiredProcessor.case_id == case.id,
        DeletionRequiredProcessor.deletion_receipt_sha256.is_(None),
    ).count()
    case.state = "awaiting_desktop_report" if pending_deletions else "ready_for_live_purge"
    if report["outstanding_actions"]:
        case.retention_reason_code = "external_desktop_copy_unresolved"
        case.retention_review_at = now + timedelta(days=30)
    evidence_payload = {
        "case_id": case.request_id,
        "work_order_id": work_order.work_order_id,
        "event_ref": work_order.event_ref,
        "report_sha256": digest,
        "signature_sha256": signature_sha256,
        "evidence_package_sha256": evidence_package_sha256,
        "processor_entity_id": work_order.processor_entity_id,
        "processor_key_id": completed_key_id,
        "completed_public_key_sha256": completed_public_key_sha256,
        "outstanding_actions": report["outstanding_actions"],
        "status": case.state,
    }
    if work_order.subject_ref is not None:
        evidence_payload["subject_ref"] = work_order.subject_ref
    append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=case.request_id,
        operation_type=f"desktop_report_received_{work_order.work_order_id}",
        record_type="deletion.desktop_report_received",
        payload=evidence_payload,
    )
    return digest


def resolve_outstanding_actions(
    db: Session,
    case: DeletionCase,
    *,
    actions: list[str],
) -> str:
    """Record completion of the exact external actions reported by desktop."""

    current = json.loads(case.outstanding_actions_json or "[]")
    if sorted(set(actions)) != sorted(set(current)) or not current:
        raise ValueError("The resolved actions must exactly match the outstanding desktop actions")
    case.outstanding_actions_json = "[]"
    case.retention_reason_code = None
    case.retention_review_at = None
    if case.live_data_purged_at is None:
        case.state = "ready_for_live_purge"
    elif not case.replacement_package_sha256:
        case.state = "awaiting_clean_backup"
    elif not _backup_inventory_resolved(db):
        case.state = "awaiting_backup_resolution"
    else:
        case.state = "awaiting_checklist"
    digest = append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=case.request_id,
        operation_type="desktop_actions_resolved",
        record_type="deletion.desktop_actions_resolved",
        payload={
            "case_id": case.request_id,
            "actions": sorted(current),
            "status": case.state,
        },
    )
    return digest


def apply_desktop_copy_resolution(
    db: Session,
    case: DeletionCase,
    work_order: DesktopDeletionWorkOrder,
    *,
    document: dict[str, Any],
    signature_sha256: str,
    evidence_package_json: str,
    evidence_package_sha256: str,
    completed_key_id: str,
    completed_public_key_sha256: str,
) -> str:
    """Record a typed processor statement about Desktop-local copies."""

    if not work_order.report_sha256:
        raise ValueError("Desktop data deletion must be recorded before local-copy resolution")
    canonical = canonical_json(document)
    digest = sha256_text(canonical)
    if work_order.copy_resolution_sha256:
        if (
            work_order.copy_resolution_sha256 != digest
            or work_order.copy_resolution_evidence_package_sha256 != evidence_package_sha256
        ):
            raise ValueError("a different local-copy resolution is already recorded")
        return digest
    work_order.copy_resolution_sha256 = digest
    work_order.copy_resolution_signature_sha256 = signature_sha256
    work_order.copy_resolution_evidence_package_json = evidence_package_json
    work_order.copy_resolution_evidence_package_sha256 = evidence_package_sha256
    requirement = db.query(DeletionRequiredProcessor).filter(
        DeletionRequiredProcessor.case_id == case.id,
        DeletionRequiredProcessor.event_ref == work_order.event_ref,
        DeletionRequiredProcessor.processor_entity_id == work_order.processor_entity_id,
    ).one()
    requirement.copy_resolution_sha256 = digest
    requirement.completed_key_id = completed_key_id
    requirement.completed_public_key_sha256 = completed_public_key_sha256
    requirement.state = "complete"
    requirement.completed_at = utc_now()
    append_record(
        db, workflow_type="deletion_case", workflow_id=case.request_id,
        operation_type=f"desktop_copy_resolution_{work_order.work_order_id}",
        record_type="deletion.desktop_copy_resolution",
        payload={
            "case_id": case.request_id, "work_order_id": work_order.work_order_id,
            "event_ref": work_order.event_ref,
            "processor_entity_id": work_order.processor_entity_id,
            "processor_key_id": completed_key_id,
            "copy_resolution_sha256": digest,
            "signature_sha256": signature_sha256,
            "evidence_package_sha256": evidence_package_sha256,
            "completed_public_key_sha256": completed_public_key_sha256,
            "disposition": document["disposition"], "status": "verified",
        },
    )
    return digest


def confirm_desktop_already_absent(db: Session, case: DeletionCase) -> str:
    """Reject controller substitution for event-scoped processor evidence."""

    del db, case
    raise ValueError("Root confirmation cannot replace required processor receipts")


def confirm_no_controlled_backups(db: Session, case: DeletionCase) -> str:
    """Record an explicit no-backup assertion; never infer it from the empty inventory."""

    if case.replacement_package_sha256:
        raise ValueError("A clean recovery snapshot is already recorded")
    if case.backup_not_applicable_sha256:
        return case.backup_not_applicable_sha256
    if not case.live_purge_receipt_sha256:
        raise ValueError("Live data must be purged first")
    if settings.HA_MODE == "ha" and not case.peer_confirmation_sha256:
        raise ValueError("Peer replication must be verified first")
    if db.query(BackupInventoryRecord).count() > 0:
        raise ValueError("Recovery packages are recorded; create a clean snapshot instead")
    snapshot_count = controlled_snapshot_count()
    if snapshot_count is not None and snapshot_count > 0:
        raise ValueError("Controlled recovery snapshots are recorded; create a clean snapshot instead")
    evidence_payload = {
        "case_id": case.request_id,
        "event_ref": case.event_evidence_id,
        "verification_method": "controller_confirmation",
        "reason_code": "no_controlled_backups",
        "outcome": "not_applicable",
        "status": "awaiting_checklist",
    }
    if case.case_type != "event_erasure":
        evidence_payload["subject_ref"] = case.subject_evidence_id
    case.backup_not_applicable_sha256 = append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=case.request_id,
        operation_type="backup_not_applicable",
        record_type="deletion.backup_not_applicable",
        payload=evidence_payload,
    )
    case.state = "awaiting_checklist"
    case.retention_reason_code = None
    case.retention_review_at = None
    return case.backup_not_applicable_sha256


def controlled_snapshot_count() -> int | None:
    """Return the bounded host snapshot count, or unknown when no status exists."""

    path = Path(settings.HA_SNAPSHOT_STATUS_PATH)
    try:
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
            return None
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    count = document.get("local_snapshot_count")
    if document.get("format") != "mp-opt-ha-snapshot-status-v1" or type(count) is not int or count < 0:
        return None
    return count


def _backup_inventory_resolved(db: Session) -> bool:
    return db.query(BackupInventoryRecord).filter(
        BackupInventoryRecord.status == "superseded_pending_deletion",
    ).first() is None


def checklist_prerequisites(case: DeletionCase, db: Session) -> list[str]:
    """Return machine-verifiable prerequisites still missing from a case."""

    missing: list[str] = []
    required_processors = db.query(DeletionRequiredProcessor).filter(
        DeletionRequiredProcessor.case_id == case.id,
    ).all()
    if case.desktop_deletion_required and (
        not required_processors or any(row.state != "complete" for row in required_processors)
    ):
        missing.append("desktop_processor_receipts")
    if not case.live_purge_receipt_sha256:
        missing.append("live_purge_receipt")
    if settings.HA_MODE == "ha" and not case.peer_confirmation_sha256:
        missing.append("peer_replication_receipt")
    if not (case.replacement_package_sha256 or case.backup_not_applicable_sha256):
        missing.append("clean_backup_receipt")
    if case.replacement_package_sha256 and controlled_snapshot_count() != 1:
        missing.append("local_snapshot_resolution")
    if not _backup_inventory_resolved(db):
        missing.append("backup_inventory_resolution")
    if case.outstanding_actions_json and json.loads(case.outstanding_actions_json):
        missing.append("outstanding_desktop_actions")
    return missing


def build_checklist(case: DeletionCase, db: Session) -> dict[str, Any]:
    """Freeze an immutable checklist after all machine prerequisites exist."""

    if case.checklist_json:
        return json.loads(case.checklist_json)
    missing = checklist_prerequisites(case, db)
    if missing:
        case.state = "restricted_retention"
        case.retention_reason_code = missing[0]
        case.retention_review_at = utc_now() + timedelta(days=30)
        raise ValueError("The deletion checklist cannot be created while required actions remain")
    scopes = db.query(DeletionSubjectScope).filter(
        DeletionSubjectScope.case_id == case.id,
    ).order_by(DeletionSubjectScope.scope_id).all()
    checklist = {
        "version": CHECKLIST_VERSION,
        "case_id": case.request_id,
        "case_type": case.case_type,
        "scopes": [
            {
                "scope_id": scope.scope_id,
                "event_ref": scope.event_ref,
                "subject_ref": scope.subject_ref,
            }
            for scope in scopes
        ],
        "receipts": {
            "live_purge_receipt_sha256": case.live_purge_receipt_sha256,
            "peer_confirmation_sha256": case.peer_confirmation_sha256,
            "clean_backup_sha256": case.replacement_package_sha256,
            "backup_not_applicable_sha256": case.backup_not_applicable_sha256,
        },
        "desktop_deletion_required": bool(case.desktop_deletion_required),
        "desktop_processors": [{
            "event_ref": row.event_ref,
            "entity_id": row.processor_entity_id,
            "snapshotted_key_id": row.snapshotted_key_id,
            "snapshotted_public_key_sha256": row.snapshotted_public_key_sha256,
            "completed_key_id": row.completed_key_id,
            "completed_public_key_sha256": row.completed_public_key_sha256,
            "deletion_receipt_sha256": row.deletion_receipt_sha256,
            "copy_resolution_sha256": row.copy_resolution_sha256,
        } for row in db.query(DeletionRequiredProcessor).filter(
            DeletionRequiredProcessor.case_id == case.id,
        ).order_by(DeletionRequiredProcessor.event_ref, DeletionRequiredProcessor.processor_entity_id)],
        "outstanding_actions": [],
    }
    canonical = canonical_json(checklist)
    case.checklist_version = CHECKLIST_VERSION
    case.checklist_json = canonical
    case.checklist_sha256 = sha256_text(canonical)
    case.checklist_created_at = utc_now()
    case.state = "awaiting_approvals"
    append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=case.request_id,
        operation_type="checklist_created",
        record_type="deletion.checklist_created",
        payload={
            "case_id": case.request_id,
            "checklist_version": CHECKLIST_VERSION,
            "checklist_sha256": case.checklist_sha256,
            "status": "awaiting_approvals",
        },
    )
    return checklist


def record_checklist_approval(
    db: Session,
    case: DeletionCase,
    *,
    role: str,
    user_id: int,
    credential_sha256: str,
) -> DeletionChecklistApproval:
    """Record one verified WebAuthn approval for the current checklist."""

    if role != "executor":
        raise ValueError("Only the root executor may approve Server completion")
    if not case.checklist_sha256 or case.state not in {"awaiting_approvals", "ready_for_completion"}:
        raise ValueError("The case has no checklist awaiting approval")
    existing = db.query(DeletionChecklistApproval).filter(
        DeletionChecklistApproval.case_id == case.id,
        DeletionChecklistApproval.checklist_sha256 == case.checklist_sha256,
        DeletionChecklistApproval.role == role,
    ).first()
    if existing:
        return existing
    now = utc_now()
    approval_payload = {
        "case_id": case.request_id,
        "checklist_sha256": case.checklist_sha256,
        "role": role,
        "user_id": user_id,
        "credential_sha256": credential_sha256,
        "approved_at": now.isoformat(),
    }
    approval_sha256 = sha256_text(canonical_json(approval_payload))
    approval = DeletionChecklistApproval(
        case_id=case.id,
        checklist_sha256=case.checklist_sha256,
        role=role,
        user_id=user_id,
        credential_sha256=credential_sha256,
        approval_sha256=approval_sha256,
        approved_at=now,
    )
    db.add(approval)
    db.flush()
    case.executor_approval_sha256 = approval_sha256
    required = {"executor"}
    present = {
        row.role
        for row in db.query(DeletionChecklistApproval).filter(
            DeletionChecklistApproval.case_id == case.id,
            DeletionChecklistApproval.checklist_sha256 == case.checklist_sha256,
        )
    }
    present.add(role)
    if required.issubset(present):
        case.state = "ready_for_completion"
    append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=case.request_id,
        operation_type=f"checklist_approved_{role}",
        record_type="deletion.checklist_approved",
        payload={
            "case_id": case.request_id,
            "checklist_sha256": case.checklist_sha256,
            "role": role,
            "approval_sha256": approval_sha256,
            "status": case.state,
        },
    )
    return approval


def complete_case(case: DeletionCase, db: Session) -> str:
    """Complete a case only after every strict prerequisite and approval."""

    if case.state != "ready_for_completion":
        raise ValueError("The deletion case is not ready for completion")
    if checklist_prerequisites(case, db):
        raise ValueError("A deletion prerequisite became unresolved")
    if not case.checklist_json or not case.checklist_sha256:
        raise ValueError("The deletion checklist is missing")
    try:
        checklist = json.loads(case.checklist_json)
    except (TypeError, ValueError) as exc:
        raise ValueError("The deletion checklist is invalid") from exc
    canonical_checklist = canonical_json(checklist)
    if (
        canonical_checklist != case.checklist_json
        or sha256_text(canonical_checklist) != case.checklist_sha256
    ):
        raise ValueError("The deletion checklist hash does not match its content")
    scopes = db.query(DeletionSubjectScope).filter(
        DeletionSubjectScope.case_id == case.id,
    ).order_by(DeletionSubjectScope.scope_id).all()
    expected_checklist = {
        "version": CHECKLIST_VERSION,
        "case_id": case.request_id,
        "case_type": case.case_type,
        "scopes": [
            {
                "scope_id": scope.scope_id,
                "event_ref": scope.event_ref,
                "subject_ref": scope.subject_ref,
            }
            for scope in scopes
        ],
        "receipts": {
            "live_purge_receipt_sha256": case.live_purge_receipt_sha256,
            "peer_confirmation_sha256": case.peer_confirmation_sha256,
            "clean_backup_sha256": case.replacement_package_sha256,
            "backup_not_applicable_sha256": case.backup_not_applicable_sha256,
        },
        "desktop_deletion_required": bool(case.desktop_deletion_required),
        "desktop_processors": [{
            "event_ref": row.event_ref,
            "entity_id": row.processor_entity_id,
            "snapshotted_key_id": row.snapshotted_key_id,
            "snapshotted_public_key_sha256": row.snapshotted_public_key_sha256,
            "completed_key_id": row.completed_key_id,
            "completed_public_key_sha256": row.completed_public_key_sha256,
            "deletion_receipt_sha256": row.deletion_receipt_sha256,
            "copy_resolution_sha256": row.copy_resolution_sha256,
        } for row in db.query(DeletionRequiredProcessor).filter(
            DeletionRequiredProcessor.case_id == case.id,
        ).order_by(DeletionRequiredProcessor.event_ref, DeletionRequiredProcessor.processor_entity_id)],
        "outstanding_actions": [],
    }
    if checklist != expected_checklist:
        raise ValueError("The deletion checklist no longer matches the case")
    approval_rows = {
        row.role: row
        for row in db.query(DeletionChecklistApproval).filter(
            DeletionChecklistApproval.case_id == case.id,
            DeletionChecklistApproval.checklist_sha256 == case.checklist_sha256,
        )
    }
    required_roles = {"executor"}
    stored_hashes = {
        "executor": case.executor_approval_sha256,
    }
    if any(
        role not in approval_rows
        or not stored_hashes[role]
        or approval_rows[role].approval_sha256 != stored_hashes[role]
        for role in required_roles
    ):
        raise ValueError("The deletion checklist approvals are incomplete or inconsistent")
    now = utc_now()
    evidence_payload = {
        "case_id": case.request_id,
        "case_type": case.case_type,
        "checklist_sha256": case.checklist_sha256,
        "executor_approval_sha256": case.executor_approval_sha256,
        "server_receipts": {
            key: value for key, value in checklist["receipts"].items() if value is not None
        },
        "desktop_processor_receipts": checklist["desktop_processors"],
        "completed_at": now.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "outcome": "verified",
        "status": "complete",
    }
    case.final_receipt_sha256 = append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=case.request_id,
        operation_type="completed",
        record_type="deletion.completed",
        payload=evidence_payload,
    )
    for scope in db.query(DeletionSubjectScope).filter(
        DeletionSubjectScope.case_id == case.id,
    ):
        scope.state = "complete"
        scope.completed_at = now
    case.completed_at = now
    case.state = "complete"
    case.event_display_name = None
    case.subject_display_name = None
    return case.state
