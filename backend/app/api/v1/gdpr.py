"""
GDPR endpoints  -  data export, deletion request, and admin-initiated anonymisation.

Admin-only execution model: users can request deletion (flag), admin approves and runs.
"""
import json
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from webauthn import generate_authentication_options, verify_authentication_response
from webauthn.helpers import base64url_to_bytes, options_to_json
from webauthn.helpers.structs import UserVerificationRequirement

from app.core.audit import audit
from app.core.config import settings
from app.core.evidence import EvidenceUnavailable, append_record as append_evidence_record
from app.core.compliance_receipts import (
    queue_clean_backup_request,
    verified_clean_backup_receipt,
)
from app.core.deletion_workflow import (
    accept_event_request,
    accept_subject_request,
    confirm_case_clean_backup,
    confirm_case_peer,
    purge_event_live_data,
    purge_subject_live_data,
)
from app.core.deletion_cases import (
    build_checklist,
    complete_case,
    create_event_erasure_case,
    ensure_desktop_work_order,
    issue_status_capability,
    record_checklist_approval,
    resolve_outstanding_actions,
    verify_status_capability,
)
from app.core.passkey_ceremonies import (
    DELETION_APPROVAL,
    consume_ceremony,
    create_ceremony,
)
from app.core.governance import stable_instance_id
from app.core.ha_replication import observe_ha_replication, protect_current_state
from app.core.security import (
    ensure_recent_reauth,
    get_current_user,
    require_admin,
    require_admin_recent_reauth,
    require_user_management_access,
)
from app.db.database import get_db
from app.models.audit import AuditLog
from app.models.deletion import (
    DeletionApprovalChallenge,
    DeletionChecklistApproval,
    DeletionCase,
    DeletionSubjectScope,
    DesktopDeletionWorkOrder,
)
from app.models.evidence import BackupInventoryRecord
from app.models.event import Event
from app.models.notification import PushSubscription
from app.models.published import (
    PublishedPerson,
    TaskEdit,
)
from app.models.user import (
    AuthSession,
    User,
    WebAuthnCredential,
)
from app.api.v1.passkey import CeremonyCompletion, _credential_id, _verify_user_handle

admin_router = APIRouter()
user_router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DataExportResponse(BaseModel):
    """GDPR export payload for one server user."""

    user: dict
    sessions_count: int
    credentials_count: int
    push_subscriptions: List[dict]
    task_edits_count: int
    linked_persons: List[dict]
    audit_entries: List[dict]


class DeletionRequestResponse(BaseModel):
    """Status response for user deletion request actions."""

    status: str
    message: str
    request_id: str
    state: str
    submitted_at: datetime
    normal_response_due_at: datetime
    completed_at: Optional[datetime] = None
    outcome: Optional[str] = None
    limitations: List[str] = Field(default_factory=list)
    status_capability: Optional[str] = None


class DeletionFinaliseIn(BaseModel):
    """Strict completion has no exception or compatibility parameters."""

    model_config = ConfigDict(extra="forbid")


class BackupResolutionIn(BaseModel):
    """Exact recovery packages the controller confirms were deleted."""

    package_ids: List[str] = Field(min_length=1, max_length=128)


class OutstandingActionsResolutionIn(BaseModel):
    """Exact external actions that the controller confirms are complete."""

    actions: List[str] = Field(min_length=1, max_length=2)


class ChecklistApprovalBeginIn(BaseModel):
    """Approval role requested for the immutable checklist."""

    role: str = Field(pattern=r"^(executor|controller|processor)$")


class EventErasureRequestIn(BaseModel):
    """Root-authorised request to erase one complete event scope."""

    processor_approval_required: bool = False


_OPEN_DELETION_STATES = {
    "submitted",
    "under_review",
    "accepted",
    "access_revoked",
    "awaiting_desktop_report",
    "ready_for_live_purge",
    "live_purge_in_progress",
    "live_data_purged",
    "peer_replication_pending",
    "peer_replication_confirmed",
    "awaiting_clean_backup",
    "clean_backup_verified",
    "awaiting_backup_resolution",
    "restricted_retention",
    "awaiting_checklist",
    "awaiting_approvals",
    "ready_for_completion",
}


def _pending_deletion_job(db: Session, user_id: int) -> DeletionCase | None:
    return (
        db.query(DeletionCase)
        .filter(
            DeletionCase.user_id == user_id,
            DeletionCase.state.in_(_OPEN_DELETION_STATES),
        )
        .order_by(DeletionCase.id.desc())
        .first()
    )


def _deletion_response(job: DeletionCase, *, status: str, message: str) -> DeletionRequestResponse:
    return DeletionRequestResponse(
        status=status,
        message=message,
        request_id=job.request_id,
        state=job.state,
        submitted_at=job.submitted_at,
        normal_response_due_at=job.normal_response_due_at,
        completed_at=job.completed_at,
        outcome="verified" if job.state == "complete" else None,
        limitations=[],
        status_capability=getattr(job, "_issued_status_capability", None),
    )


def _admin_deletion_job(db: Session, request_id: str) -> DeletionCase:
    job = db.query(DeletionCase).filter(
        DeletionCase.request_id == request_id,
    ).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Deletion request not found")
    return job


def _job_detail(job: DeletionCase, db: Session | None = None) -> dict:
    detail = {
        "request_id": job.request_id,
        "case_type": job.case_type,
        "initiation_reason": job.initiation_reason,
        "state": job.state,
        "submitted_at": job.submitted_at,
        "normal_response_due_at": job.normal_response_due_at,
        "decision_at": job.decision_at,
        "access_revoked_at": job.access_revoked_at,
        "live_data_purged_at": job.live_data_purged_at,
        "completed_at": job.completed_at,
        "event_ref": job.event_evidence_id,
        "subject_ref": job.subject_evidence_id,
        "desktop_deletion_required": bool(job.desktop_deletion_required),
        "topology": "two_node_ha" if settings.HA_MODE == "ha" else "single_node",
        "peer_replication": {
            "job_id": job.peer_replication_job_id,
            "bundle_id": job.peer_bundle_id,
            "bundle_sha256": job.peer_bundle_sha256,
            "generation": job.peer_generation,
            "accepted_at": job.peer_accepted_at,
        },
        "clean_backup_bridge": {
            "job_id": job.clean_backup_job_id,
            "receipt_id": job.clean_backup_receipt_id,
        },
        "evidence": {
            "request": job.request_manifest_sha256,
            "acceptance": job.acceptance_receipt_sha256,
            "access_revocation": job.access_revocation_receipt_sha256,
            "desktop_report": job.desktop_report_sha256,
            "live_purge": job.live_purge_receipt_sha256,
            "peer": job.peer_confirmation_sha256,
            "clean_backup": job.replacement_package_sha256,
            "backup_resolution": job.backup_resolution_sha256,
            "executor_approval": job.executor_approval_sha256,
            "controller_approval": job.controller_approval_sha256,
            "processor_approval": job.processor_approval_sha256,
            "checklist": job.checklist_sha256,
            "final": job.final_receipt_sha256,
        },
        "retention": {
            "reason_code": job.retention_reason_code,
            "review_at": job.retention_review_at,
            "outstanding_actions": json.loads(job.outstanding_actions_json)
            if job.outstanding_actions_json else [],
        },
        "checklist": {
            "version": job.checklist_version,
            "sha256": job.checklist_sha256,
            "created_at": job.checklist_created_at,
            "processor_approval_required": job.processor_approval_required,
        },
    }
    if db is not None:
        detail["scopes"] = [
            {
                "scope_id": scope.scope_id,
                "event_ref": scope.event_ref,
                "subject_ref": scope.subject_ref,
                "state": scope.state,
            }
            for scope in db.query(DeletionSubjectScope).filter(
                DeletionSubjectScope.case_id == job.id,
            ).order_by(DeletionSubjectScope.id).all()
        ]
        detail["desktop_work_orders"] = [
            {
                "work_order_id": work_order.work_order_id,
                "event_ref": work_order.event_ref,
                "subject_ref": work_order.subject_ref,
                "operation": work_order.operation,
                "state": work_order.state,
                "report_sha256": work_order.report_sha256,
            }
            for work_order in db.query(DesktopDeletionWorkOrder).filter(
                DesktopDeletionWorkOrder.case_id == job.id,
            ).order_by(DesktopDeletionWorkOrder.id).all()
        ]
        detail["approvals"] = [
            {
                "role": approval.role,
                "approval_sha256": approval.approval_sha256,
                "approved_at": approval.approved_at,
            }
            for approval in db.query(DeletionChecklistApproval).filter(
                DeletionChecklistApproval.case_id == job.id,
            ).order_by(DeletionChecklistApproval.id).all()
        ]
    return detail


def _new_deletion_job(db: Session, user: User, *, state: str = "submitted") -> DeletionCase:
    instance_id = stable_instance_id(db)
    event = (
        db.query(Event).filter(Event.id == user.event_id).first()
        if user.event_id is not None
        else None
    )
    if user.event_id is not None and event is None:
        raise HTTPException(status_code=409, detail="The account's event data scope no longer exists")
    if event is None and user.linked_person_id is not None:
        raise HTTPException(
            status_code=409,
            detail="The account has a stale desktop person link. Clear it before deletion.",
        )
    published_person = None
    if user.linked_person_id is not None:
        published_person = db.query(PublishedPerson).filter(
            PublishedPerson.event_id == event.id,
            PublishedPerson.external_person_id == user.linked_person_id,
        ).first()
        if published_person is None:
            raise HTTPException(
                status_code=409,
                detail="The account's desktop person link is stale. Relink it before deletion.",
            )
        user.evidence_subject_id = published_person.evidence_subject_id
    now = datetime.now(timezone.utc)
    # Activated accounts can legitimately exist before an event assignment.
    # Give those server-only identities a stable, pseudonymous instance scope
    # so they can use the same signed evidence workflow without inventing an
    # event or creating an inapplicable Desktop work order.
    event_ref = (
        event.evidence_id
        if event is not None
        else str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"urn:masterplan:{instance_id}:server-only-accounts",
        ))
    )
    job = DeletionCase(
        instance_id=instance_id,
        event_evidence_id=event_ref,
        subject_evidence_id=user.evidence_subject_id,
        desktop_deletion_required=published_person is not None,
        user_id=user.id,
        state=state,
        normal_response_due_at=now + timedelta(days=30),
    )
    db.add(job)
    db.flush()
    job._issued_status_capability = issue_status_capability(job)
    try:
        job.request_manifest_sha256 = append_evidence_record(
            db,
            workflow_type="deletion_case",
            workflow_id=job.request_id,
            operation_type="requested",
            record_type="data_subject.deletion.requested",
            payload={
                "request_id": job.request_id,
                "event_ref": job.event_evidence_id,
                "subject_ref": job.subject_evidence_id,
                "request_type": job.request_type,
                "identity_verification": job.identity_verification,
                "submitted_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "normal_response_due_at": job.normal_response_due_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": state,
            },
        )
    except (EvidenceUnavailable, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "EVIDENCE_UNAVAILABLE",
                "message": "The deletion request could not be recorded safely. Try again later.",
            },
        ) from exc
    return job


# ---------------------------------------------------------------------------
# Admin-initiated data export
# ---------------------------------------------------------------------------

@admin_router.get("/users/{user_id}/export", response_model=DataExportResponse)
def export_user_data(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    """Export all personal data for a user (admin only). GDPR Article 20."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    require_user_management_access(user, admin)

    user_data = {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "is_admin": user.is_admin,
        "can_edit": user.can_edit,
        "event_id": user.event_id,
        "tags": user.tags,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }

    sessions_count = db.query(AuthSession).filter(AuthSession.user_id == user_id).count()
    credentials_count = db.query(WebAuthnCredential).filter(WebAuthnCredential.user_id == user_id).count()

    push_subs = db.query(PushSubscription).filter(PushSubscription.user_id == user_id).all()
    push_data = [
        {"event_id": s.event_id, "created_at": s.created_at.isoformat() if s.created_at else None}
        for s in push_subs
    ]

    task_edits_count = db.query(TaskEdit).filter(TaskEdit.edited_by_user_id == user_id).count()

    linked_persons = []
    if user.linked_person_id and user.event_id:
        persons = (
            db.query(PublishedPerson)
            .filter(
                PublishedPerson.event_id == user.event_id,
                PublishedPerson.external_person_id == user.linked_person_id,
            )
            .all()
        )
        linked_persons = [
            {"name": f"{p.first_name} {p.last_name}", "email": p.email, "event_id": p.event_id}
            for p in persons
        ]

    audit_entries = (
        db.query(AuditLog)
        .filter(AuditLog.user_id == user_id)
        .order_by(AuditLog.timestamp.desc())
        .limit(500)
        .all()
    )
    audit_data = [
        {
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "action": e.action,
            "resource_type": e.resource_type,
            "outcome": e.outcome,
        }
        for e in audit_entries
    ]

    audit(db, user=admin, action="gdpr.export", resource_type="user",
          resource_id=user_id, request=request)
    db.commit()

    return DataExportResponse(
        user=user_data,
        sessions_count=sessions_count,
        credentials_count=credentials_count,
        push_subscriptions=push_data,
        task_edits_count=task_edits_count,
        linked_persons=linked_persons,
        audit_entries=audit_data,
    )


# ---------------------------------------------------------------------------
# Admin-initiated GDPR deletion (anonymisation)
# ---------------------------------------------------------------------------

@admin_router.delete("/users/{user_id}/gdpr-delete")
def gdpr_delete_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    """Create and accept a strict erasure case for one managed user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    require_user_management_access(user, admin)

    deletion_job = _pending_deletion_job(db, user_id)
    if deletion_job is None:
        deletion_job = _new_deletion_job(db, user)
    try:
        accept_subject_request(db, deletion_job, user)
        if deletion_job.desktop_deletion_required:
            event = db.query(Event).filter(Event.id == user.event_id).one()
            ensure_desktop_work_order(
                db,
                deletion_job,
                event=event,
                subject_ref=deletion_job.subject_evidence_id,
            )
    except (EvidenceUnavailable, ValueError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "DELETION_WORKFLOW_REJECTED", "message": str(exc)},
        ) from exc

    audit(db, user=admin, action="gdpr.delete", resource_type="user",
          resource_id=None, detail=json.dumps({
              "result": (
                  "accepted_and_queued_for_desktop"
                  if deletion_job.desktop_deletion_required
                  else "accepted_server_only"
              ),
              "deletion_request_id": deletion_job.request_id,
          }), request=request)
    db.commit()

    return {
        "status": "accepted",
        "message": (
            "Access was revoked and the desktop deletion work order was created."
            if deletion_job.desktop_deletion_required
            else "Access was revoked; this server-only account is ready for live-data deletion."
        ),
        "request_id": deletion_job.request_id,
        "state": deletion_job.state,
    }


# ---------------------------------------------------------------------------
# Evidence-backed deletion workflow administration
# ---------------------------------------------------------------------------

@admin_router.get("/deletion-requests")
def list_deletion_requests(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List non-identifying deletion workflow receipts."""

    jobs = db.query(DeletionCase).order_by(DeletionCase.id.desc()).limit(500).all()
    return [_job_detail(job, db) for job in jobs]


@admin_router.post("/deletion-requests/events/{event_id}", status_code=202)
def create_event_erasure_request(
    event_id: int,
    body: EventErasureRequestIn,
    request: Request,
    root: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    """Create and accept a whole-event erasure case using the unified workflow."""

    if not root.is_root_admin:
        raise HTTPException(status_code=403, detail="Root admin access required")
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    existing = db.query(DeletionCase).filter(
        DeletionCase.case_type == "event_erasure",
        DeletionCase.event_evidence_id == event.evidence_id,
        DeletionCase.state.in_(_OPEN_DELETION_STATES),
    ).first()
    if existing is not None:
        return _job_detail(existing, db)
    try:
        job = create_event_erasure_case(
            db,
            event,
            processor_approval_required=body.processor_approval_required,
            initiation_reason="manual_root",
        )
        event.purge_case_request_id = job.request_id
        event.purge_started_at = job.submitted_at or datetime.now(timezone.utc)
        accept_event_request(db, job, event)
        ensure_desktop_work_order(db, job, event=event, subject_ref=None)
    except (EvidenceUnavailable, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(
        db,
        user=root,
        action="gdpr.create_event_erasure",
        resource_type="deletion_request",
        detail=json.dumps(
            {"deletion_request_id": job.request_id, "event_ref": event.evidence_id}
        ),
        request=request,
    )
    db.commit()
    return _job_detail(job, db)


@admin_router.get("/deletion-requests/{request_id}")
def get_deletion_request(
    request_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _job_detail(_admin_deletion_job(db, request_id), db)


@admin_router.post("/deletion-requests/{request_id}/accept")
def accept_deletion_request(
    request_id: str,
    request: Request,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    job = _admin_deletion_job(db, request_id)
    if job.case_type == "event_erasure":
        if not admin.is_root_admin:
            raise HTTPException(status_code=403, detail="Root admin access required")
        event = db.query(Event).filter(
            Event.evidence_id == job.event_evidence_id,
        ).first()
        if event is None:
            if job.live_data_purged_at is not None:
                return _job_detail(job, db)
            raise HTTPException(
                status_code=409,
                detail="The event erasure target no longer exists",
            )
        try:
            accept_event_request(db, job, event)
            ensure_desktop_work_order(db, job, event=event, subject_ref=None)
        except (EvidenceUnavailable, ValueError) as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit(
            db,
            user=admin,
            action="gdpr.accept_event_erasure",
            resource_type="deletion_request",
            detail=json.dumps({"deletion_request_id": job.request_id}),
            request=request,
        )
        db.commit()
        return _job_detail(job, db)
    if job.user_id is None:
        raise HTTPException(status_code=409, detail="The request is no longer linked to live account data")
    user = db.query(User).filter(User.id == job.user_id).first()
    if user is None:
        raise HTTPException(status_code=409, detail="The request's account no longer exists")
    require_user_management_access(user, admin)
    try:
        accept_subject_request(db, job, user)
        if job.desktop_deletion_required:
            event = db.query(Event).filter(Event.id == user.event_id).one()
            ensure_desktop_work_order(
                db,
                job,
                event=event,
                subject_ref=job.subject_evidence_id,
            )
    except (EvidenceUnavailable, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, user=admin, action="gdpr.accept_deletion", resource_type="deletion_request",
          detail=json.dumps({"deletion_request_id": job.request_id}), request=request)
    db.commit()
    return _job_detail(job, db)


@admin_router.post("/deletion-requests/{request_id}/purge-live")
def purge_deletion_request_live_data(
    request_id: str,
    request: Request,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    job = _admin_deletion_job(db, request_id)
    if job.case_type == "event_erasure":
        if not admin.is_root_admin:
            raise HTTPException(status_code=403, detail="Root admin access required")
        event = db.query(Event).filter(
            Event.evidence_id == job.event_evidence_id,
        ).first()
        if event is None:
            if job.live_data_purged_at is not None:
                return _job_detail(job, db)
            raise HTTPException(
                status_code=409,
                detail="The event erasure target no longer exists",
            )
        try:
            purge_event_live_data(db, job, event)
        except (EvidenceUnavailable, ValueError) as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit(
            db,
            user=admin,
            action="gdpr.purge_event_live_data",
            resource_type="deletion_request",
            detail=json.dumps({"deletion_request_id": job.request_id}),
            request=request,
        )
        db.commit()
        return _job_detail(job, db)
    if job.user_id is None:
        if job.live_data_purged_at is not None:
            return _job_detail(job, db)
        raise HTTPException(status_code=409, detail="The request has no live account target")
    user = db.query(User).filter(User.id == job.user_id).first()
    if user is None:
        raise HTTPException(status_code=409, detail="The request's account no longer exists")
    require_user_management_access(user, admin)
    try:
        purge_subject_live_data(db, job, user)
    except (EvidenceUnavailable, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, user=admin, action="gdpr.purge_live", resource_type="deletion_request",
          detail=json.dumps({"deletion_request_id": job.request_id}), request=request)
    db.commit()
    return _job_detail(job, db)


@admin_router.post("/deletion-requests/{request_id}/peer-replication")
def confirm_deletion_peer(
    request_id: str,
    request: Request,
    response: Response,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    job = _admin_deletion_job(db, request_id)
    if settings.HA_MODE != "ha":
        raise HTTPException(status_code=409, detail="Peer replication is not applicable")
    if not job.live_purge_receipt_sha256 or not job.privacy_action_id or not job.privacy_action_sequence:
        raise HTTPException(status_code=409, detail="Live data must be purged before peer replication")
    assertion = {
        "workflow_type": "deletion_case",
        "workflow_id": job.request_id,
        "privacy_action_id": job.privacy_action_id,
        "privacy_action_sequence": job.privacy_action_sequence,
        "live_purge_receipt_sha256": job.live_purge_receipt_sha256,
    }
    protection = (
        observe_ha_replication(
            job.peer_replication_job_id,
            privacy_assertion=assertion,
        )
        if job.peer_replication_job_id
        else None
    )
    if protection is None and job.peer_replication_job_id is None:
        protection = protect_current_state(
            "privacy-case-purge",
            critical=False,
            privacy_assertion=assertion,
        )
        job.peer_replication_job_id = protection.job_id
    try:
        if protection is not None and protection.protected:
            confirm_case_peer(db, job, protection)
    except (EvidenceUnavailable, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, user=admin, action=(
        "gdpr.peer_replication_confirmed"
        if job.peer_confirmation_sha256
        else "gdpr.peer_replication_requested"
    ), resource_type="deletion_request",
          detail=json.dumps({"deletion_request_id": job.request_id}), request=request)
    db.commit()
    if not job.peer_confirmation_sha256:
        response.status_code = 202
    return _job_detail(job, db)


@admin_router.post("/deletion-requests/{request_id}/clean-backup-request", status_code=202)
def request_deletion_clean_backup(
    request_id: str,
    request: Request,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    job = _admin_deletion_job(db, request_id)
    if not job.live_data_purged_at or not job.live_purge_receipt_sha256 or not job.privacy_action_id or not job.privacy_action_sequence:
        raise HTTPException(status_code=409, detail="Live data must be purged before creating a clean baseline")
    if settings.HA_MODE == "ha" and not job.peer_confirmation_sha256:
        raise HTTPException(status_code=409, detail="The peer must accept the privacy action first")
    job.clean_backup_job_id = job.clean_backup_job_id or str(uuid.uuid4())
    try:
        queue_clean_backup_request(
            job_id=job.clean_backup_job_id,
            instance_id=job.instance_id,
            workflow_type="deletion_case",
            workflow_id=job.request_id,
            event_ref=job.event_evidence_id,
            subject_ref=(
                None if job.case_type == "event_erasure" else job.subject_evidence_id
            ),
            privacy_action_id=job.privacy_action_id,
            privacy_action_sequence=job.privacy_action_sequence,
            live_purge_receipt_sha256=job.live_purge_receipt_sha256,
            live_data_purged_at=job.live_data_purged_at,
        )
    except (EvidenceUnavailable, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, user=admin, action="gdpr.clean_backup_requested", resource_type="deletion_request",
          detail=json.dumps({"deletion_request_id": job.request_id}), request=request)
    db.commit()
    return _job_detail(job, db)


@admin_router.post("/deletion-requests/{request_id}/apply-clean-backup-receipt")
def apply_deletion_clean_backup_receipt(
    request_id: str,
    request: Request,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    job = _admin_deletion_job(db, request_id)
    if job.clean_backup_receipt_id:
        return _job_detail(job, db)
    if not job.clean_backup_job_id or not job.live_data_purged_at:
        raise HTTPException(status_code=409, detail="Create the guided clean-backup request first")
    expected = {
        "job_id": job.clean_backup_job_id,
        "instance_id": job.instance_id,
        "workflow_type": "deletion_case",
        "workflow_id": job.request_id,
        "event_ref": job.event_evidence_id,
        "subject_ref": (
            None if job.case_type == "event_erasure" else job.subject_evidence_id
        ),
        "privacy_action_id": job.privacy_action_id,
        "privacy_action_sequence": job.privacy_action_sequence,
        "live_purge_receipt_sha256": job.live_purge_receipt_sha256,
        "live_data_purged_at": job.live_data_purged_at.astimezone(timezone.utc).isoformat(),
    }
    try:
        receipt = verified_clean_backup_receipt(
            db, job_id=job.clean_backup_job_id, expected=expected,
        )
        confirm_case_clean_backup(db, job, receipt=receipt)
    except (EvidenceUnavailable, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, user=admin, action="gdpr.clean_backup_receipt_applied",
          resource_type="deletion_request",
          detail=json.dumps({"deletion_request_id": job.request_id}), request=request)
    db.commit()
    return _job_detail(job, db)


@admin_router.post("/deletion-requests/{request_id}/finalise")
def finalise_deletion_request(
    request_id: str,
    body: DeletionFinaliseIn,
    request: Request,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    job = _admin_deletion_job(db, request_id)
    try:
        complete_case(job, db)
    except (EvidenceUnavailable, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, user=admin, action="gdpr.finalise_deletion", resource_type="deletion_request",
          detail=json.dumps({
              "deletion_request_id": job.request_id,
              "outcome": job.state,
          }), request=request)
    db.commit()
    return _job_detail(job, db)


@admin_router.post("/deletion-requests/{request_id}/resolve-backups")
def resolve_deletion_backups(
    request_id: str,
    body: BackupResolutionIn,
    request: Request,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    """Record root-authenticated deletion of exact superseded packages."""

    if not admin.is_root_admin:
        raise HTTPException(status_code=403, detail="Root admin access required")
    job = _admin_deletion_job(db, request_id)
    package_ids = sorted(set(body.package_ids))
    if any(len(value) != 36 for value in package_ids):
        raise HTTPException(status_code=422, detail="Every package ID must be a UUID")
    records = db.query(BackupInventoryRecord).filter(
        BackupInventoryRecord.package_id.in_(package_ids),
    ).all()
    if {record.package_id for record in records} != set(package_ids):
        raise HTTPException(status_code=409, detail="One or more packages are not in the inventory")
    if any(record.status != "superseded_pending_deletion" for record in records):
        raise HTTPException(status_code=409, detail="Only unresolved superseded packages may be resolved")
    payload = {
        "case_id": job.request_id,
        "package_ids": package_ids,
        "outcome": "operator_confirmed_deleted",
        "status": "backup_inventory_resolved",
    }
    digest = append_evidence_record(
        db,
        workflow_type="deletion_case",
        workflow_id=job.request_id,
        operation_type="backup_inventory_resolved",
        record_type="deletion.backup_inventory_resolved",
        payload=payload,
    )
    for record in records:
        record.status = "confirmed_deleted"
        record.deletion_resolution_sha256 = digest
    job.backup_resolution_sha256 = digest
    job.state = "awaiting_checklist"
    audit(
        db,
        user=admin,
        action="gdpr.resolve_backups",
        resource_type="deletion_request",
        detail=json.dumps({"deletion_request_id": job.request_id, "package_count": len(package_ids)}),
        request=request,
    )
    db.commit()
    return _job_detail(job, db)


@admin_router.post("/deletion-requests/{request_id}/resolve-outstanding-actions")
def resolve_deletion_outstanding_actions(
    request_id: str,
    body: OutstandingActionsResolutionIn,
    request: Request,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    """Confirm external copies were removed after the local desktop transaction."""

    if not admin.is_root_admin:
        raise HTTPException(status_code=403, detail="Root admin access required")
    job = _admin_deletion_job(db, request_id)
    try:
        receipt_sha256 = resolve_outstanding_actions(db, job, actions=body.actions)
    except (EvidenceUnavailable, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(
        db,
        user=admin,
        action="gdpr.resolve_outstanding_actions",
        resource_type="deletion_request",
        detail=json.dumps({"deletion_request_id": job.request_id}),
        request=request,
    )
    db.commit()
    return {"receipt_sha256": receipt_sha256, **_job_detail(job, db)}


@admin_router.post("/deletion-requests/{request_id}/checklist")
def create_deletion_checklist(
    request_id: str,
    request: Request,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    """Freeze the immutable checklist after machine prerequisites pass."""

    job = _admin_deletion_job(db, request_id)
    try:
        checklist = build_checklist(job, db)
    except (EvidenceUnavailable, ValueError) as exc:
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(
        db,
        user=admin,
        action="gdpr.create_checklist",
        resource_type="deletion_request",
        detail=json.dumps({"deletion_request_id": job.request_id}),
        request=request,
    )
    db.commit()
    return {
        "checklist": checklist,
        "checklist_sha256": job.checklist_sha256,
        "state": job.state,
    }


@admin_router.post("/deletion-requests/{request_id}/approvals/begin")
def begin_deletion_checklist_approval(
    request_id: str,
    body: ChecklistApprovalBeginIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Start a WebAuthn ceremony bound to one checklist and role."""

    job = _admin_deletion_job(db, request_id)
    if not job.checklist_sha256 or job.state not in {"awaiting_approvals", "ready_for_completion"}:
        raise HTTPException(status_code=409, detail="The case has no checklist awaiting approval")
    if body.role in {"controller", "processor"} and not admin.is_root_admin:
        raise HTTPException(status_code=403, detail="Root admin access required for this approval role")
    if body.role == "processor" and not job.processor_approval_required:
        raise HTTPException(status_code=409, detail="This case does not require processor approval")
    auth_session = getattr(admin, "_auth_session", None)
    if auth_session is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    nonce = secrets.token_bytes(32)
    challenge = hashlib.sha256(
        b"mp-opt-deletion-checklist-v1\0"
        + job.request_id.encode("ascii")
        + b"\0"
        + job.checklist_sha256.encode("ascii")
        + b"\0"
        + body.role.encode("ascii")
        + b"\0"
        + nonce
    ).digest()
    options = generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        challenge=challenge,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    ceremony = create_ceremony(
        challenge,
        DELETION_APPROVAL,
        db,
        user_id=admin.id,
        session_id=auth_session.id,
    )
    context = DeletionApprovalChallenge(
        ceremony_id=ceremony.id,
        case_id=job.id,
        checklist_sha256=job.checklist_sha256,
        role=body.role,
        user_id=admin.id,
    )
    db.add(context)
    db.commit()
    return {
        "options": options_to_json(options),
        "ceremony_id": ceremony.id,
        "checklist_sha256": job.checklist_sha256,
        "role": body.role,
    }


@admin_router.post("/deletion-requests/{request_id}/approvals/{role}/complete")
def complete_deletion_checklist_approval(
    request_id: str,
    role: str,
    body: CeremonyCompletion,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Verify and record a checklist-bound passkey approval."""

    job = _admin_deletion_job(db, request_id)
    auth_session = getattr(admin, "_auth_session", None)
    if auth_session is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    ceremony = consume_ceremony(
        body.ceremony_id,
        DELETION_APPROVAL,
        db,
        user_id=admin.id,
        session_id=auth_session.id,
    )
    context = db.query(DeletionApprovalChallenge).filter(
        DeletionApprovalChallenge.ceremony_id == ceremony.id,
        DeletionApprovalChallenge.case_id == job.id,
        DeletionApprovalChallenge.checklist_sha256 == job.checklist_sha256,
        DeletionApprovalChallenge.role == role,
        DeletionApprovalChallenge.user_id == admin.id,
        DeletionApprovalChallenge.consumed_at.is_(None),
    ).first()
    if context is None:
        db.rollback()
        raise HTTPException(status_code=400, detail="Approval ceremony does not match this checklist")
    try:
        credential_id = _credential_id(body.credential)
        stored = db.query(WebAuthnCredential).filter(
            WebAuthnCredential.credential_id == credential_id,
            WebAuthnCredential.user_id == admin.id,
        ).with_for_update().one()
        _verify_user_handle(body.credential, admin.id)
        verification = verify_authentication_response(
            credential=body.credential,
            expected_challenge=base64url_to_bytes(ceremony.challenge),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            credential_public_key=stored.public_key,
            credential_current_sign_count=stored.sign_count,
            require_user_verification=True,
        )
        stored.sign_count = verification.new_sign_count
        stored.last_used_at = datetime.now(timezone.utc)
        context.consumed_at = datetime.now(timezone.utc)
        approval = record_checklist_approval(
            db,
            job,
            role=role,
            user_id=admin.id,
            credential_sha256=hashlib.sha256(credential_id).hexdigest(),
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Checklist approval failed") from exc
    audit(
        db,
        user=admin,
        action=f"gdpr.approve_checklist.{role}",
        resource_type="deletion_request",
        detail=json.dumps({"deletion_request_id": job.request_id}),
        request=request,
    )
    db.commit()
    return {
        "approval_sha256": approval.approval_sha256,
        "role": approval.role,
        "state": job.state,
    }


# ---------------------------------------------------------------------------
# Dismiss (undo) a deletion request
# ---------------------------------------------------------------------------

@admin_router.delete("/users/{user_id}/deletion-request")
def dismiss_deletion_request(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin_recent_reauth),
    db: Session = Depends(get_db),
):
    """Clear a user's pending deletion request without taking action."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    require_user_management_access(user, admin)
    if not user.deletion_requested_at:
        raise HTTPException(status_code=409, detail="No pending deletion request")

    now = datetime.now(timezone.utc)
    deletion_job = _pending_deletion_job(db, user_id)
    if deletion_job is not None:
        try:
            deletion_job.decision_code = "controller_rejected_request"
            append_evidence_record(
                db,
                workflow_type="deletion_case",
                workflow_id=deletion_job.request_id,
                operation_type="rejected",
                record_type="data_subject.deletion.rejected",
                payload={
                    "request_id": deletion_job.request_id,
                    "event_ref": deletion_job.event_evidence_id,
                    "subject_ref": deletion_job.subject_evidence_id,
                    "decision_code": deletion_job.decision_code,
                    "status": "rejected",
                },
            )
        except EvidenceUnavailable as exc:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail={"code": "EVIDENCE_UNAVAILABLE", "message": str(exc)},
            ) from exc
        deletion_job.state = "rejected"
        deletion_job.decision_at = now
        deletion_job.user_id = None
    user.deletion_requested_at = None
    audit(db, user=admin, action="gdpr.dismiss_deletion", resource_type="user",
          resource_id=user_id,
          detail=json.dumps({
              "deletion_request_id": deletion_job.request_id if deletion_job else None,
              "result": "rejected",
          }),
          request=request)
    db.commit()

    return {"status": "ok", "message": "Deletion request dismissed"}


# ---------------------------------------------------------------------------
# User self-service deletion request
# ---------------------------------------------------------------------------

@user_router.post("/deletion-requests", response_model=DeletionRequestResponse)
@user_router.post("/request-deletion", response_model=DeletionRequestResponse)
def request_deletion(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Flag the current user's account for deletion. An admin will review."""
    if current_user.is_root_admin:
        raise HTTPException(status_code=403, detail="Root admin cannot request self-deletion")
    ensure_recent_reauth(current_user, db)

    existing = _pending_deletion_job(db, current_user.id)
    if existing is not None:
        return _deletion_response(
            existing,
            status="pending",
            message="Your deletion request is already pending.",
        )

    current_user.deletion_requested_at = datetime.now(timezone.utc)
    deletion_job = _new_deletion_job(db, current_user)
    audit(
        db,
        user=current_user,
        action="gdpr.request_deletion",
        resource_type="deletion_request",
        resource_id=None,
        detail=json.dumps({"deletion_request_id": deletion_job.request_id}),
        request=request,
    )
    db.commit()

    return _deletion_response(
        deletion_job,
        status="ok",
        message="Your deletion request has been submitted. An administrator will process it.",
    )


@user_router.get("/deletion-requests/current", response_model=DeletionRequestResponse)
def current_deletion_request(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the current durable deletion receipt for the signed-in user."""
    job = (
        db.query(DeletionCase)
        .filter(DeletionCase.user_id == current_user.id)
        .order_by(DeletionCase.id.desc())
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="No deletion request found")
    return _deletion_response(
        job,
        status="pending" if job.state in _OPEN_DELETION_STATES else job.state,
        message="Your deletion request receipt is available.",
    )


@user_router.get("/deletion-requests/{request_id}/status")
def deletion_request_status_with_capability(
    request_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return a minimised status after the requesting account is erased."""

    job = db.query(DeletionCase).filter(
        DeletionCase.request_id == request_id,
    ).first()
    capability = request.headers.get("x-deletion-status", "")
    if job is None or not verify_status_capability(job, capability):
        raise HTTPException(status_code=404, detail="Deletion request not found")
    return {
        "request_id": job.request_id,
        "state": job.state,
        "submitted_at": job.submitted_at,
        "normal_response_due_at": job.normal_response_due_at,
        "completed_at": job.completed_at,
        "outcome": "verified" if job.state == "complete" else None,
        "retention_reason_code": job.retention_reason_code,
        "retention_review_at": job.retention_review_at,
    }


@user_router.get(
    "/deletion-requests/{request_id}/receipt",
    response_model=DeletionRequestResponse,
)
def deletion_request_receipt(
    request_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return one deletion receipt without exposing another user's workflow."""
    job = (
        db.query(DeletionCase)
        .filter(
            DeletionCase.request_id == request_id,
            DeletionCase.user_id == current_user.id,
        )
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Deletion request not found")
    return _deletion_response(
        job,
        status="pending" if job.state in _OPEN_DELETION_STATES else job.state,
        message="Your deletion request receipt is available.",
    )


@user_router.post(
    "/deletion-requests/{request_id}/withdraw",
    response_model=DeletionRequestResponse,
)
def withdraw_deletion_request(
    request_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Record a signed withdrawal before live data has been purged."""

    ensure_recent_reauth(current_user, db)
    job = db.query(DeletionCase).filter(
        DeletionCase.request_id == request_id,
        DeletionCase.user_id == current_user.id,
    ).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Deletion request not found")
    if job.state in {
        "live_data_purged",
        "peer_replication_pending",
        "peer_replication_confirmed",
        "awaiting_clean_backup",
        "clean_backup_verified",
        "awaiting_backup_resolution",
        "restricted_retention",
        "awaiting_checklist",
        "awaiting_approvals",
        "ready_for_completion",
        "complete",
    }:
        raise HTTPException(
            status_code=409,
            detail="The request can no longer be withdrawn because live deletion has started.",
        )
    if job.state == "withdrawn":
        return _deletion_response(job, status="withdrawn", message="The deletion request is withdrawn.")
    if job.state == "rejected":
        raise HTTPException(status_code=409, detail="The request has already been rejected")

    try:
        append_evidence_record(
            db,
            workflow_type="deletion_case",
            workflow_id=job.request_id,
            operation_type="withdrawn",
            record_type="data_subject.deletion.withdrawn",
            payload={
                "request_id": job.request_id,
                "event_ref": job.event_evidence_id,
                "subject_ref": job.subject_evidence_id,
                "status": "withdrawn",
            },
        )
    except EvidenceUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "EVIDENCE_UNAVAILABLE"}) from exc
    job.state = "withdrawn"
    job.decision_at = datetime.now(timezone.utc)
    current_user.deletion_requested_at = None
    audit(
        db,
        user=current_user,
        action="gdpr.withdraw_deletion",
        resource_type="deletion_request",
        detail=json.dumps({"deletion_request_id": job.request_id}),
        request=request,
    )
    db.commit()
    return _deletion_response(job, status="withdrawn", message="The deletion request is withdrawn.")
