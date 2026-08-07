"""Restart-safe erasure executed by the server and paired desktop."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import uuid

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.evidence import (
    EvidenceUnavailable,
    append_record,
    lock_evidence_transaction,
)
from app.core.sessions import revoke_all_user_sessions
from app.core.ha_replication import HAProtectionResult
from app.models.audit import AuditLog
from app.models.deletion import (
    DeletionCase,
    DeletionSubjectScope,
    DesktopDeletionWorkOrder,
)
from app.models.evidence import BackupInventoryRecord, PrivacyActionReceipt
from app.models.event import Event
from app.models.notification import PushSubscription
from app.models.published import (
    PublishedPerson,
    PublishedPersonUnavailability,
    PublishedTask,
    PublishSnapshot,
    TaskEdit,
)
from app.models.user import (
    ActivationEmailDelivery,
    ActivationLink, AuthSession,
    ExchangeCode,
    PasskeyChallenge,
    PasskeyCeremony,
    User,
    WebAuthnCredential,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _topology() -> str:
    return "two_node_ha" if settings.HA_MODE == "ha" else "single_node"


def record_clean_backup(
    db: Session,
    *,
    package_id: str,
    package_sha256: str,
    archive_sha256: str | None = None,
    recovery_key_id: str | None = None,
    confirmed_at: datetime | None = None,
) -> None:
    """Maintain the complete local inventory when a clean package is verified."""

    now = utc_now()
    # Several deletion cases may consume the same freshly exported recovery
    # package concurrently. Serialise registration by package ID so every
    # transaction observes the first insert instead of racing the unique key.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:scope), hashtext(:package_id))"),
            {"scope": "mp-opt-backup-inventory", "package_id": package_id},
        )
    existing = db.query(BackupInventoryRecord).filter(
        BackupInventoryRecord.package_id == package_id,
    ).first()
    if existing is not None and existing.package_sha256 != package_sha256:
        raise ValueError("The recovery package UUID is already registered with another digest")
    for previous in db.query(BackupInventoryRecord).filter(
        BackupInventoryRecord.status == "active",
        BackupInventoryRecord.package_id != package_id,
    ):
        previous.status = "superseded_pending_deletion"
        previous.replacement_package_id = package_id
    if existing is None:
        existing = BackupInventoryRecord(
            package_id=package_id,
            package_sha256=package_sha256,
            archive_sha256=archive_sha256,
            recovery_key_id=recovery_key_id,
            status="active",
            verified_at=now,
            confirmed_at=confirmed_at or now,
        )
        db.add(existing)
    else:
        existing.status = "active"
        existing.verified_at = now
        existing.confirmed_at = confirmed_at or now
        existing.archive_sha256 = archive_sha256 or existing.archive_sha256
        existing.recovery_key_id = recovery_key_id or existing.recovery_key_id
    db.flush()


def record_superseded_portable_backups(
    db: Session, *, packages: list[dict], replacement_package_id: str,
) -> None:
    """Persist every known pre-deletion workstation package for explicit resolution."""

    for package in packages:
        existing = db.query(BackupInventoryRecord).filter(
            BackupInventoryRecord.package_id == package["package_id"],
        ).first()
        if existing is not None and existing.package_sha256 != package["package_sha256"]:
            raise ValueError("A superseded recovery package is registered with another digest")
        if existing is None:
            existing = BackupInventoryRecord(
                package_id=package["package_id"],
                package_sha256=package["package_sha256"],
                archive_sha256=package["archive_sha256"],
                recovery_key_id=package["recovery_key_id"],
                status="superseded_pending_deletion",
                created_at=datetime.fromisoformat(package["snapshot_created_at"]),
                confirmed_at=datetime.fromisoformat(package["portable_confirmed_at"]),
                replacement_package_id=replacement_package_id,
            )
            db.add(existing)
        elif existing.status != "confirmed_deleted":
            existing.status = "superseded_pending_deletion"
            existing.replacement_package_id = replacement_package_id
    db.flush()


def _redact_person_from_json(raw: str | None, external_person_id: int) -> str | None:
    if not raw:
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return raw

    def redact(item):
        if isinstance(item, list):
            return [redact(entry) for entry in item]
        if isinstance(item, dict):
            result = {key: redact(entry) for key, entry in item.items()}
            person_id = result.get("person_id")
            if person_id is None and any(key in result for key in ("first_name", "last_name", "email")):
                person_id = result.get("id")
            if str(person_id) == str(external_person_id):
                for key in ("name", "first_name", "last_name", "email", "display_name"):
                    if key in result:
                        result[key] = "Deleted participant" if key in {"name", "display_name"} else None
            return result
        return item

    return json.dumps(redact(value), ensure_ascii=False, separators=(",", ":"))


def _create_privacy_action(db: Session, job: DeletionCase) -> PrivacyActionReceipt:
    lock_evidence_transaction(db)
    if job.privacy_action_id:
        existing = db.query(PrivacyActionReceipt).filter(
            PrivacyActionReceipt.privacy_action_id == job.privacy_action_id,
        ).first()
        if existing is None:
            raise EvidenceUnavailable("The deletion privacy-action receipt is inconsistent")
        return existing
    sequence = (db.query(func.max(PrivacyActionReceipt.sequence)).scalar() or 0) + 1
    receipt = PrivacyActionReceipt(
        privacy_action_id=str(uuid.uuid4()),
        sequence=sequence,
        instance_id=job.instance_id,
        event_ref=job.event_evidence_id,
        subject_ref=(
            None if job.case_type == "event_erasure" else job.subject_evidence_id
        ),
        action_type=(
            "event_delete" if job.case_type == "event_erasure" else "subject_delete"
        ),
        retain_until=utc_now() + timedelta(days=settings.EVIDENCE_TOMBSTONE_RETENTION_DAYS),
    )
    db.add(receipt)
    db.flush()
    job.privacy_action_id = receipt.privacy_action_id
    job.privacy_action_sequence = receipt.sequence
    evidence_payload = {
        "request_id": job.request_id,
        "event_ref": job.event_evidence_id,
        "privacy_action_id": receipt.privacy_action_id,
        "privacy_action_sequence": receipt.sequence,
        "action_type": receipt.action_type,
        "retain_until": timestamp(receipt.retain_until),
        "status": "created",
    }
    if receipt.subject_ref is not None:
        evidence_payload["subject_ref"] = receipt.subject_ref
    append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=job.request_id,
        operation_type="privacy_action_created",
        record_type="privacy_action.created",
        payload=evidence_payload,
    )
    return receipt


def accept_event_request(db: Session, job: DeletionCase, event: Event) -> None:
    """Accept an event erasure and revoke all event-scoped sessions immediately."""

    if job.case_type != "event_erasure" or event.evidence_id != job.event_evidence_id:
        raise ValueError("The event erasure target does not match its case")
    if job.state not in {
        "submitted",
        "under_review",
        "accepted",
        "access_revoked",
        "awaiting_desktop_report",
    }:
        raise ValueError("The event erasure cannot be accepted from its current state")
    if job.state == "awaiting_desktop_report":
        return
    now = utc_now()
    job.verification_method = "root_passkey_reauthentication"
    job.decision_code = "accepted_event_erasure"
    job.decision_at = now
    job.acceptance_receipt_sha256 = append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=job.request_id,
        operation_type="accepted",
        record_type="deletion.event_accepted",
        payload={
            "case_id": job.request_id,
            "event_ref": job.event_evidence_id,
            "verification_method": job.verification_method,
            "status": "accepted",
        },
    )
    _create_privacy_action(db, job)
    user_ids = [
        row.id for row in db.query(User.id).filter(User.event_id == event.id)
    ]
    if user_ids:
        db.query(AuthSession).filter(
            AuthSession.user_id.in_(user_ids),
            AuthSession.revoked_at.is_(None),
        ).update({"revoked_at": now}, synchronize_session=False)
    job.access_revoked_at = now
    job.access_revocation_receipt_sha256 = append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=job.request_id,
        operation_type="access_revoked",
        record_type="deletion.event_access_revoked",
        payload={
            "case_id": job.request_id,
            "event_ref": job.event_evidence_id,
            "privacy_action_id": job.privacy_action_id,
            "status": "access_revoked",
        },
    )
    job.state = (
        "awaiting_desktop_report"
        if job.desktop_deletion_required
        else "ready_for_live_purge"
    )


def accept_subject_request(db: Session, job: DeletionCase, user: User) -> None:
    """Approve the request and immediately remove account access."""

    if job.state not in {
        "submitted", "under_review", "accepted", "access_revoked",
        "awaiting_desktop_report", "ready_for_live_purge",
    }:
        raise ValueError("The deletion request cannot be accepted from its current state")
    if job.state in {"access_revoked", "awaiting_desktop_report", "ready_for_live_purge"}:
        return
    now = utc_now()
    job.verification_method = job.verification_method or "recent_passkey_reauthentication"
    job.decision_code = "accepted_erasure_request"
    job.decision_at = now
    job.acceptance_receipt_sha256 = append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=job.request_id,
        operation_type="accepted",
        record_type="data_subject.deletion.accepted",
        payload={
            "request_id": job.request_id,
            "event_ref": job.event_evidence_id,
            "subject_ref": job.subject_evidence_id,
            "verification_method": job.verification_method,
            "decision_code": job.decision_code,
            "status": "accepted",
        },
    )
    job.state = "accepted"
    _create_privacy_action(db, job)

    revoke_all_user_sessions(user.id, db)
    db.query(AuthSession).filter(AuthSession.user_id == user.id).delete(synchronize_session=False)
    db.query(ExchangeCode).filter(ExchangeCode.user_id == user.id).delete(synchronize_session=False)
    db.query(ActivationLink).filter(ActivationLink.user_id == user.id).delete(synchronize_session=False)
    user.is_active = False
    user.deletion_requested_at = None
    job.access_revoked_at = now
    job.access_revocation_receipt_sha256 = append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=job.request_id,
        operation_type="access_revoked",
        record_type="data_subject.access_revoked",
        payload={
            "request_id": job.request_id,
            "event_ref": job.event_evidence_id,
            "subject_ref": job.subject_evidence_id,
            "privacy_action_id": job.privacy_action_id,
            "privacy_action_sequence": job.privacy_action_sequence,
            "status": "access_revoked",
        },
    )
    job.state = (
        "awaiting_desktop_report"
        if job.desktop_deletion_required
        else "ready_for_live_purge"
    )


def purge_subject_live_data(db: Session, job: DeletionCase, user: User) -> None:
    """Purge/anonymise all locally controlled copies of one subject."""

    if job.live_data_purged_at is not None:
        return
    if job.state not in {"ready_for_live_purge", "live_purge_in_progress"}:
        raise ValueError("The deletion request is not ready for live-data purge")
    if job.desktop_deletion_required and not (
        job.desktop_report_sha256 or job.desktop_absence_receipt_sha256
    ):
        raise ValueError("A verified desktop deletion report is required before live-data purge")
    job.state = "live_purge_in_progress"
    linked_person_id = user.linked_person_id
    linked_event_id = user.event_id
    revoke_all_user_sessions(user.id, db)
    db.query(AuthSession).filter(AuthSession.user_id == user.id).delete(synchronize_session=False)
    db.query(WebAuthnCredential).filter(WebAuthnCredential.user_id == user.id).delete(synchronize_session=False)
    db.query(PasskeyChallenge).filter(PasskeyChallenge.user_id == user.id).delete(synchronize_session=False)
    db.query(PasskeyCeremony).filter(PasskeyCeremony.user_id == user.id).delete(synchronize_session=False)
    db.query(ExchangeCode).filter(ExchangeCode.user_id == user.id).delete(synchronize_session=False)
    db.query(ActivationEmailDelivery).filter(ActivationEmailDelivery.user_id == user.id).delete(synchronize_session=False)
    db.query(ActivationLink).filter(ActivationLink.created_by_id == user.id).update(
        {"created_by_id": None}, synchronize_session=False,
    )
    db.query(ActivationEmailDelivery).filter(
        ActivationEmailDelivery.requested_by_id == user.id,
    ).update({"requested_by_id": None}, synchronize_session=False)
    db.query(ActivationLink).filter(ActivationLink.user_id == user.id).delete(synchronize_session=False)
    db.query(PushSubscription).filter(PushSubscription.user_id == user.id).delete(synchronize_session=False)
    db.query(TaskEdit).filter(TaskEdit.edited_by_user_id == user.id).update(
        {"edited_by_user_id": None}, synchronize_session=False,
    )

    published_targets = {
        (person.event_id, person.external_person_id)
        for person in db.query(PublishedPerson).filter(
            PublishedPerson.evidence_subject_id == job.subject_evidence_id,
        )
    }
    if linked_person_id is not None and linked_event_id is not None:
        published_targets.add((linked_event_id, linked_person_id))

    for target_event_id, target_person_id in sorted(published_targets):
        for person in db.query(PublishedPerson).filter(
            PublishedPerson.event_id == target_event_id,
            PublishedPerson.external_person_id == target_person_id,
        ):
            person.first_name = "Deleted"
            person.last_name = f"Participant {person.id}"
            person.email = None
        db.query(PublishedPersonUnavailability).filter(
            PublishedPersonUnavailability.event_id == target_event_id,
            PublishedPersonUnavailability.external_person_id == target_person_id,
        ).delete(synchronize_session=False)
        for task in db.query(PublishedTask).filter(PublishedTask.event_id == target_event_id):
            for field in (
                "attendees_json", "field_assignments_json", "field_values_json",
                "field_definitions_json", "additional_json", "attachments_json",
            ):
                setattr(task, field, _redact_person_from_json(getattr(task, field), target_person_id))
        for edit in db.query(TaskEdit).join(PublishedTask).filter(PublishedTask.event_id == target_event_id):
            for field in ("attendees_json", "field_assignments_json", "field_values_json", "attachments_json"):
                setattr(edit, field, _redact_person_from_json(getattr(edit, field), target_person_id))
        for snapshot in db.query(PublishSnapshot).filter(PublishSnapshot.event_id == target_event_id):
            snapshot.snapshot_json = _redact_person_from_json(snapshot.snapshot_json, target_person_id) or "{}"
            snapshot.content_hash = hashlib.sha256(snapshot.snapshot_json.encode("utf-8")).hexdigest()

    user.linked_person_id = None
    db.query(AuditLog).filter(AuditLog.user_id == user.id).update(
        {"user_id": None, "username": None, "actor_ref": None, "detail": None, "ip_hash": None},
        synchronize_session=False,
    )
    db.query(AuditLog).filter(
        AuditLog.resource_type == "user", AuditLog.resource_id == user.id,
    ).update(
        {"resource_id": None, "detail": None},
        synchronize_session=False,
    )
    user.username = f"deleted_{secrets.token_hex(12)}"
    user.display_name = "Deleted User"
    user.email = None
    user.tags = None
    user.event_id = None
    user.is_admin = False
    user.is_issuer = False
    user.is_active = False
    user.is_activated = False
    user.deletion_requested_at = None

    now = utc_now()
    receipt = db.query(PrivacyActionReceipt).filter(
        PrivacyActionReceipt.privacy_action_id == job.privacy_action_id,
    ).first()
    if receipt is None:
        receipt = _create_privacy_action(db, job)
    receipt.local_applied_at = now
    job.live_data_purged_at = now
    job.live_purge_receipt_sha256 = append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=job.request_id,
        operation_type="live_data_purged",
        record_type="data_subject.live_data_purged",
        payload={
            "request_id": job.request_id,
            "event_ref": job.event_evidence_id,
            "subject_ref": job.subject_evidence_id,
            "privacy_action_id": job.privacy_action_id,
            "privacy_action_sequence": job.privacy_action_sequence,
            "topology": _topology(),
            "completed_at": timestamp(now),
            "outcome": "verified",
            "status": "live_data_purged",
        },
    )
    job.user_id = None
    db.delete(user)
    db.flush()
    job.state = "peer_replication_pending" if settings.HA_MODE == "ha" else "awaiting_clean_backup"


def confirm_case_peer(
    db: Session,
    job: DeletionCase,
    protection: HAProtectionResult,
) -> None:
    if settings.HA_MODE != "ha":
        raise ValueError("Peer confirmation is not applicable to a single-node installation")
    if job.live_data_purged_at is None:
        raise ValueError("Live data must be purged before peer confirmation")
    if job.peer_confirmed_at is not None:
        return
    if not (
        protection.protected
        and protection.job_id
        and protection.bundle_id
        and protection.bundle_sha256
        and protection.generation
        and protection.accepted_at
    ):
        raise ValueError("The peer has not returned a complete accepted-bundle receipt")
    now = protection.accepted_at
    evidence_payload = {
        "case_id": job.request_id,
        "event_ref": job.event_evidence_id,
        "privacy_action_id": job.privacy_action_id,
        "privacy_action_sequence": job.privacy_action_sequence,
        "bundle_id": protection.bundle_id,
        "bundle_sha256": protection.bundle_sha256,
        "generation": protection.generation,
        "peer_confirmed_at": timestamp(now),
        "outcome": "verified",
        "status": "peer_replication_confirmed",
    }
    if job.case_type != "event_erasure":
        evidence_payload["subject_ref"] = job.subject_evidence_id
    job.peer_confirmation_sha256 = append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=job.request_id,
        operation_type="peer_confirmed",
        record_type="deletion.peer_confirmed",
        payload=evidence_payload,
    )
    job.peer_confirmed_at = now
    job.peer_replication_job_id = protection.job_id
    job.peer_bundle_id = protection.bundle_id
    job.peer_bundle_sha256 = protection.bundle_sha256
    job.peer_generation = protection.generation
    job.peer_accepted_at = now
    receipt = db.query(PrivacyActionReceipt).filter(
        PrivacyActionReceipt.privacy_action_id == job.privacy_action_id,
    ).first()
    if receipt:
        receipt.peer_confirmed_at = now
    job.state = "awaiting_clean_backup"


def confirm_case_clean_backup(
    db: Session,
    job: DeletionCase,
    *,
    receipt: dict,
) -> None:
    if job.live_data_purged_at is None:
        raise ValueError("Live data must be purged before creating a clean replacement backup")
    if settings.HA_MODE == "ha" and job.peer_confirmed_at is None:
        raise ValueError("The peer must confirm the privacy action before the clean backup")
    package_id = receipt["package_id"]
    package_sha256 = receipt["package_sha256"]
    record_superseded_portable_backups(
        db,
        packages=receipt["superseded_portable_packages"],
        replacement_package_id=package_id,
    )
    record_clean_backup(
        db,
        package_id=package_id,
        package_sha256=package_sha256,
        archive_sha256=receipt["archive_sha256"],
        recovery_key_id=receipt["recovery_key_id"],
        confirmed_at=datetime.fromisoformat(receipt["portable_confirmed_at"]),
    )
    job.replacement_package_id = package_id
    job.replacement_package_sha256 = package_sha256
    job.clean_backup_receipt_id = receipt["receipt_id"]
    evidence_payload = {
        "case_id": job.request_id,
        "event_ref": job.event_evidence_id,
        "replacement_package_id": package_id,
        "replacement_package_sha256": package_sha256,
        "receipt_sha256": receipt["receipt_sha256"],
        "local_snapshot_count": receipt["local_snapshot_count"],
        "superseded_portable_package_ids": [
            package["package_id"] for package in receipt["superseded_portable_packages"]
        ],
        "verified_at": timestamp(utc_now()),
        "outcome": "verified",
        "status": "clean_backup_verified",
    }
    if job.case_type != "event_erasure":
        evidence_payload["subject_ref"] = job.subject_evidence_id
    append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=job.request_id,
        operation_type="clean_backup_verified",
        record_type="deletion.clean_backup_verified",
        payload=evidence_payload,
    )
    job.state = "awaiting_backup_resolution"


def purge_event_live_data(db: Session, job: DeletionCase, event: Event) -> None:
    """Delete one event after every applicable Desktop receipt is verified."""

    if job.live_data_purged_at is not None:
        return
    if job.case_type != "event_erasure" or event.evidence_id != job.event_evidence_id:
        raise ValueError("The event erasure target no longer matches")
    if job.state not in {"ready_for_live_purge", "live_purge_in_progress"}:
        raise ValueError("The event erasure is not ready for live-data deletion")
    if job.desktop_deletion_required and not (
        job.desktop_report_sha256 or job.desktop_absence_receipt_sha256
    ):
        raise ValueError("A verified desktop deletion report is required before live-data purge")
    job.state = "live_purge_in_progress"
    event_users = db.query(User).filter(
        User.event_id == event.id,
        User.is_root_admin == False,  # noqa: E712
    ).all()
    user_ids = [user.id for user in event_users]
    if user_ids:
        db.query(AuditLog).filter(AuditLog.user_id.in_(user_ids)).update(
            {"user_id": None, "username": None, "actor_ref": None, "detail": None, "ip_hash": None},
            synchronize_session=False,
        )
        db.query(ActivationEmailDelivery).filter(
            ActivationEmailDelivery.requested_by_id.in_(user_ids),
        ).update({"requested_by_id": None}, synchronize_session=False)
        db.query(ActivationLink).filter(ActivationLink.created_by_id.in_(user_ids)).update(
            {"created_by_id": None}, synchronize_session=False,
        )
        for user in event_users:
            db.delete(user)
    db.query(AuditLog).filter(
        AuditLog.resource_type == "event",
        AuditLog.resource_id == event.id,
    ).update({"resource_id": None, "detail": None}, synchronize_session=False)
    now = utc_now()
    receipt = db.query(PrivacyActionReceipt).filter(
        PrivacyActionReceipt.privacy_action_id == job.privacy_action_id,
    ).first()
    if receipt is None:
        receipt = _create_privacy_action(db, job)
    receipt.local_applied_at = now
    for scope in db.query(DeletionSubjectScope).filter(
        DeletionSubjectScope.case_id == job.id,
    ):
        scope.event_id = None
        scope.state = "server_deleted"
        scope.completed_at = now
    for work_order in db.query(DesktopDeletionWorkOrder).filter(
        DesktopDeletionWorkOrder.case_id == job.id,
    ):
        work_order.event_id = None
    db.delete(event)
    db.flush()
    job.live_data_purged_at = now
    job.live_purge_receipt_sha256 = append_record(
        db,
        workflow_type="deletion_case",
        workflow_id=job.request_id,
        operation_type="live_data_purged",
        record_type="deletion.event_live_data_purged",
        payload={
            "case_id": job.request_id,
            "event_ref": job.event_evidence_id,
            "privacy_action_id": job.privacy_action_id,
            "topology": _topology(),
            "completed_at": timestamp(now),
            "outcome": "verified",
            "status": "live_data_purged",
        },
    )
    job.state = "peer_replication_pending" if settings.HA_MODE == "ha" else "awaiting_clean_backup"
