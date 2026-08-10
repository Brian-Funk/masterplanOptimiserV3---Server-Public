"""Restricted, resumable root commissioning API."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import zipfile
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1 import evidence_keys, governance
from app.api.v1.passkey import CeremonyCompletion
from app.core.audit import audit
from app.core.commissioning import (
    active_controller_key,
    commissioning_stage,
    latest_governance,
    mark_commissioning_complete,
    root_passkey_ready,
    setting,
    set_setting,
    status_payload,
)
from app.core.evidence import (
    EvidenceUnavailable,
    append_record,
    initialise,
    verify_local_chain,
)
from app.core.governance import stable_instance_id
from app.core.security import (
    require_commissioning_root,
    require_commissioning_root_recent_reauth,
)
from app.db.database import get_db
from app.models.evidence import EvidenceChainState, EvidenceKey
from app.models.user import User


router = APIRouter()


class RecoveryCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: str = Field(pattern=r"^age1[023456789acdefghjklmnpqrstuvwxyz]{58}$")
    download_acknowledged: Literal[True]
    local_reimport_verified: Literal[True]


def _require_stage(db: Session, expected: str) -> None:
    actual = commissioning_stage(db)
    if actual != expected:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COMMISSIONING_STAGE_MISMATCH",
                "expected": expected,
                "current": actual,
                "message": "This setup action is not available at the current step.",
            },
        )


def _safe_failure(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})


@router.get("/status")
def setup_status(
    _root: User = Depends(require_commissioning_root),
    db: Session = Depends(get_db),
):
    """Return authoritative public progress without any private key material."""
    result = status_payload(db)
    result["final_readiness_checks"] = {
        "root_passkey": root_passkey_ready(db),
        "bootstrap_token_retired": setting(db, "root_bootstrap_disabled") == "true",
        "recovery_acknowledgement": bool(setting(db, "root_recovery_download_acknowledged_at")),
        "controller_trust": active_controller_key(db) is not None,
        "governance_v1": bool(latest_governance(db) and latest_governance(db).version == 1),
        "instance_evidence": db.get(EvidenceChainState, 1) is not None,
    }
    return result


@router.post("/recovery/complete")
def complete_recovery(
    body: RecoveryCompletion,
    request: Request,
    root: User = Depends(require_commissioning_root_recent_reauth),
    db: Session = Depends(get_db),
):
    """Record only public AGE recipient metadata after browser-local verification."""
    _require_stage(db, "recovery")
    recipient_digest = hashlib.sha256(body.recipient.encode("ascii")).hexdigest()
    now = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        evidence_digest = append_record(
            db,
            workflow_type="commissioning",
            workflow_id=stable_instance_id(db),
            operation_type="recovery_key_acknowledged",
            record_type="commissioning.recovery_key_acknowledged",
            payload={
                "instance_id": stable_instance_id(db),
                "recipient_sha256": recipient_digest,
                "download_acknowledged": True,
                "local_reimport_verified": True,
                "completed_at": now,
            },
            allow_missing_audit=True,
        )
    except EvidenceUnavailable as exc:
        db.rollback()
        raise _safe_failure("RECOVERY_EVIDENCE_UNAVAILABLE", str(exc)) from exc
    set_setting(db, "root_recovery_recipient_sha256", recipient_digest)
    set_setting(db, "root_recovery_recipient", body.recipient)
    set_setting(db, "root_recovery_download_acknowledged_at", now)
    set_setting(db, "root_recovery_evidence_sha256", evidence_digest or "")
    audit(db, user=root, action="commissioning.recovery_completed", resource_type="instance", request=request)
    db.commit()
    return status_payload(db)


@router.post("/controller/challenges")
def begin_controller_challenge(
    body: evidence_keys.BeginTrustKeyChallenge,
    request: Request,
    root: User = Depends(require_commissioning_root),
    db: Session = Depends(get_db),
):
    _require_stage(db, "controller")
    return evidence_keys.begin_trust_key_challenge(body=body, request=request, root=root, db=db)


@router.post("/controller/proofs")
def submit_controller_proof(
    body: evidence_keys.SubmitPossessionProof,
    request: Request,
    root: User = Depends(require_commissioning_root),
    db: Session = Depends(get_db),
):
    _require_stage(db, "controller")
    return evidence_keys.submit_possession_proof(body=body, request=request, root=root, db=db)


@router.post("/controller/{challenge_id}/root-authorisation/begin")
def begin_controller_root_authorisation(
    challenge_id: str,
    request: Request,
    root: User = Depends(require_commissioning_root),
    db: Session = Depends(get_db),
):
    _require_stage(db, "controller")
    return evidence_keys.begin_root_authorisation(challenge_id=challenge_id, request=request, root=root, db=db)


@router.post("/controller/{challenge_id}/root-authorisation/complete")
def complete_controller_root_authorisation(
    challenge_id: str,
    body: CeremonyCompletion,
    request: Request,
    root: User = Depends(require_commissioning_root),
    db: Session = Depends(get_db),
):
    _require_stage(db, "controller")
    result = evidence_keys.complete_root_authorisation(
        challenge_id=challenge_id, body=body, request=request, root=root, db=db
    )
    return {**result, "commissioning": status_payload(db)}


@router.get("/governance")
def get_governance_draft(
    root: User = Depends(require_commissioning_root),
    db: Session = Depends(get_db),
):
    _require_stage(db, "governance")
    return governance.governance_draft(_root=root, db=db)


@router.put("/governance")
def save_governance_draft(
    body: governance.GovernanceDraft,
    request: Request,
    root: User = Depends(require_commissioning_root),
    db: Session = Depends(get_db),
):
    """Save a private draft without a WebAuthn prompt."""
    _require_stage(db, "governance")
    return governance.save_governance_draft(body=body, request=request, root=root, db=db)


@router.get("/governance/preview")
def preview_governance(
    root: User = Depends(require_commissioning_root),
    db: Session = Depends(get_db),
):
    _require_stage(db, "governance")
    return governance.preview_governance(_root=root, db=db)


@router.get("/governance/preview/{section}.html")
def preview_governance_section(
    section: str,
    root: User = Depends(require_commissioning_root),
    db: Session = Depends(get_db),
):
    _require_stage(db, "governance")
    return governance.preview_governance_html(section=section, _root=root, db=db)


@router.post("/governance/publish")
def publish_governance_v1(
    body: governance.PublicationConfirmation,
    request: Request,
    root: User = Depends(require_commissioning_root_recent_reauth),
    db: Session = Depends(get_db),
):
    _require_stage(db, "governance")
    if latest_governance(db) is not None:
        raise _safe_failure("GOVERNANCE_V1_ALREADY_EXISTS", "Governance version 1 is already published.")
    return governance.publish_governance(body=body, request=request, root=root, db=db)


def _final_checks(db: Session) -> tuple[dict[str, bool], dict]:
    publication = latest_governance(db)
    controller = active_controller_key(db)
    db.execute(text("SELECT 1"))
    chain = verify_local_chain(db)
    public_document = governance.public_governance(db=db)
    privacy_notice = governance.public_governance_html(section="privacy", db=db)
    terms_notice = governance.public_governance_html(section="terms", db=db)
    public_digest_ok = bool(
        publication
        and public_document.get("configured") is True
        and public_document.get("version") == publication.version
        and public_document.get("content_sha256") == publication.content_sha256
        and hashlib.sha256(publication.content_json.encode("utf-8")).hexdigest()
        == publication.content_sha256
    )
    expected_version_marker = f"Policy version {publication.version}" if publication else ""
    expected_digest_marker = f"SHA-256 {publication.content_sha256}" if publication else ""
    public_notices_ok = bool(
        publication
        and privacy_notice.status_code == 200
        and terms_notice.status_code == 200
        and expected_version_marker.encode("utf-8") in privacy_notice.body
        and expected_version_marker.encode("utf-8") in terms_notice.body
        and expected_digest_marker.encode("utf-8") in privacy_notice.body
        and expected_digest_marker.encode("utf-8") in terms_notice.body
        and b"<h1>Privacy notice</h1>" in privacy_notice.body
        and b"<h1>Instance terms</h1>" in terms_notice.body
    )
    checks = {
        "root_passkey": root_passkey_ready(db),
        "bootstrap_token_retired": setting(db, "root_bootstrap_disabled") == "true",
        "recovery_acknowledgement": bool(setting(db, "root_recovery_download_acknowledged_at")),
        "controller_trust_establishment": bool(controller and controller.trust_establishment_sha256),
        "instance_evidence_key": db.query(EvidenceKey).filter(
            EvidenceKey.role == "instance", EvidenceKey.revoked_at.is_(None)
        ).first() is not None,
        "complete_evidence_chain": bool(chain.get("head_sha256")),
        "governance_version_1": bool(publication and publication.version == 1),
        "public_governance_digest": public_digest_ok,
        "public_privacy_and_terms": public_notices_ok,
        "application_health": True,
    }
    return checks, chain


@router.post("/finalise")
def finalise_commissioning(
    request: Request,
    root: User = Depends(require_commissioning_root),
    db: Session = Depends(get_db),
):
    """Idempotently verify all facts and append the commissioning receipt."""
    if commissioning_stage(db) == "complete":
        return status_payload(db)
    _require_stage(db, "governance")
    try:
        checks, before = _final_checks(db)
    except EvidenceUnavailable as exc:
        raise _safe_failure("COMMISSIONING_EVIDENCE_CHECK_FAILED", str(exc)) from exc
    if not all(checks.values()):
        raise HTTPException(
            status_code=409,
            detail={"code": "COMMISSIONING_NOT_READY", "checks": checks, "message": "One or more final checks failed."},
        )
    controller = active_controller_key(db)
    publication = latest_governance(db)
    # The immutable publication time makes a retry byte-for-byte idempotent even
    # if the evidence append succeeded immediately before a transient response
    # or database failure.
    completed_at = publication.published_at.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "instance_id": stable_instance_id(db),
        "completed_at": completed_at,
        "status": "completed",
        "entity_id": controller.entity_id,
        "controller_key_id": controller.key_id,
        "public_key_sha256": controller.public_key_sha256,
        "trust_establishment_sha256": controller.trust_establishment_sha256,
        "policy_version": publication.version,
        "policy_sha256": publication.content_sha256,
        "checks_sha256": hashlib.sha256(
            json.dumps(checks, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "previous_chain_head_sha256": before.get("head_sha256"),
    }
    try:
        receipt = append_record(
            db,
            workflow_type="commissioning",
            workflow_id=stable_instance_id(db),
            operation_type="complete",
            record_type="commissioning.completed",
            payload=payload,
            allow_missing_audit=True,
        )
    except EvidenceUnavailable as exc:
        db.rollback()
        raise _safe_failure("COMMISSIONING_RECEIPT_FAILED", str(exc)) from exc
    mark_commissioning_complete(db, receipt or "")
    audit(db, user=root, action="commissioning.completed", resource_type="instance", request=request)
    db.commit()
    return status_payload(db)


@router.get("/report.zip")
def commissioning_report(
    _root: User = Depends(require_commissioning_root),
    db: Session = Depends(get_db),
):
    """Download only public commissioning metadata and verification results."""
    if commissioning_stage(db) != "complete":
        raise _safe_failure("COMMISSIONING_NOT_COMPLETE", "Complete commissioning before downloading its report.")
    checks, chain = _final_checks(db)
    controller = active_controller_key(db)
    publication = latest_governance(db)
    status = status_payload(db)
    files = {
        "commissioning-summary.json": status,
        "controller-public-key.json": {
            "format": "mp-opt-controller-public-key-v1",
            "entity_id": controller.entity_id,
            "key_id": controller.key_id,
            "public_key": controller.public_key,
            "public_key_sha256": controller.public_key_sha256,
        },
        "governance-v1.json": {
            "version": publication.version,
            "content_sha256": publication.content_sha256,
            "published_content": json.loads(publication.content_json),
            "source_configuration": json.loads(publication.source_json),
        },
        "commissioning-receipt.json": {
            "record_sha256": setting(db, "root_commissioning_receipt_sha256"),
            "completed_at": setting(db, "root_commissioning_completed_at"),
        },
        "verification.json": {"checks": checks, "chain": chain},
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, document in files.items():
            archive.writestr(name, json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return Response(
        output.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="masterplan-commissioning-report.zip"'},
    )
