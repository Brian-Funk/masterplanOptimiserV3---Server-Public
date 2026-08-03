"""Root-only status, export and verification for the required evidence chain."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from app.core.audit import audit
from app.core.evidence import EvidenceUnavailable, initialise, verify_local_chain
from app.core.security import require_root_admin, require_root_recent_reauth
from app.db.database import get_db
from app.models.evidence import BackupInventoryRecord, EvidenceChainState, EvidenceKey
from app.models.user import User
from app.services.evidence_archive import archive_status, retry_submission
from app.services.evidence_export import create_complete_evidence_export, remove_complete_evidence_export


router = APIRouter()


def _unavailable(exc: EvidenceUnavailable) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "EVIDENCE_OPERATION_REJECTED", "message": str(exc)},
    )


@router.get("")
def evidence_status(
    root: User = Depends(require_root_admin),
    db: Session = Depends(get_db),
):
    """Return non-secret chain and public-key status."""

    state = db.get(EvidenceChainState, 1)
    keys = db.query(EvidenceKey).order_by(EvidenceKey.registered_at).all()
    return {
        "initialised": state is not None,
        "mode": state.evidence_mode if state else None,
        "instance_id": state.instance_id if state else None,
        "chain_id": state.chain_id if state else None,
        "last_sequence": state.last_sequence if state else 0,
        "head_sha256": state.head_sha256 if state else None,
        "keys": [
            {
                "key_id": key.key_id,
                "role": key.role,
                "valid_from": key.valid_from,
                "expires_at": key.expires_at,
                "revoked_at": key.revoked_at,
            }
            for key in keys
        ],
    }


@router.get("/backups")
def list_backup_inventory(
    root: User = Depends(require_root_admin),
    db: Session = Depends(get_db),
):
    """List current package identifiers, digests, and lifecycle state."""

    rows = db.query(BackupInventoryRecord).order_by(
        BackupInventoryRecord.created_at.desc(),
    ).all()
    return [
        {
            "package_id": row.package_id,
            "package_sha256": row.package_sha256,
            "status": row.status,
            "replacement_package_id": row.replacement_package_id,
            "verified_at": row.verified_at,
            "confirmed_at": row.confirmed_at,
        }
        for row in rows
    ]


@router.get("/archive")
def evidence_archive_status(
    root: User = Depends(require_root_admin),
    db: Session = Depends(get_db),
):
    """Return a root-only, non-secret view of optional private Git archival."""

    return archive_status(db)


@router.post("/archive/{submission_id}/retry")
def retry_evidence_archive(
    submission_id: str,
    request: Request,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    """Retry a safe failed submission without accepting a credential via HTTP."""

    try:
        row = retry_submission(db, submission_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(
        db,
        user=root,
        action="evidence.archive_retry",
        resource_type="evidence_archive_submission",
        resource_id=row.submission_id,
        request=request,
    )
    db.commit()
    return {"submission_id": row.submission_id, "state": row.state}


@router.post("/initialise")
def initialise_evidence(
    request: Request,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    """Initialise the mandatory local evidence chain."""

    try:
        state = initialise(db)
    except (EvidenceUnavailable, ValueError) as exc:
        raise _unavailable(EvidenceUnavailable(str(exc))) from exc
    if state is None:
        raise HTTPException(status_code=409, detail="Evidence chain is unavailable")
    audit(db, user=root, action="evidence.initialise", resource_type="evidence", request=request)
    db.commit()
    return {"instance_id": state.instance_id, "chain_id": state.chain_id, "mode": state.evidence_mode}


@router.post("/verify")
def verify_evidence(
    request: Request,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    """Verify that database state and the signed filesystem chain agree."""

    try:
        result = verify_local_chain(db)
    except EvidenceUnavailable as exc:
        raise _unavailable(exc) from exc
    audit(db, user=root, action="evidence.verify", resource_type="evidence", request=request)
    db.commit()
    return result


@router.post("/export")
def export_evidence(
    request: Request,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    """Verify the exact chain and return a short-lived complete evidence ZIP."""

    output = None
    try:
        verified = verify_local_chain(db)
        state = db.get(EvidenceChainState, 1)
        if state is None:
            raise EvidenceUnavailable("Evidence has not been initialised")
        output, metadata = create_complete_evidence_export(state.instance_id)
        if metadata.get("chain_head_sha256") != verified.get("head_sha256"):
            remove_complete_evidence_export(output)
            raise EvidenceUnavailable("The evidence chain changed while the export was created")
    except EvidenceUnavailable as exc:
        if output is not None:
            remove_complete_evidence_export(output)
        raise _unavailable(exc) from exc
    try:
        audit(
            db,
            user=root,
            action="evidence.export",
            resource_type="evidence",
            detail=json.dumps(
                {
                    "bundle_id": metadata["bundle_id"],
                    "chain_head_sha256": metadata["chain_head_sha256"],
                    "record_count": metadata["record_count"],
                    "zip_sha256": metadata["zip_sha256"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            request=request,
        )
        db.commit()
    except Exception:
        remove_complete_evidence_export(output)
        raise
    filename = f"accountability-evidence-{metadata['chain_head_sha256'][:12]}.zip"
    return FileResponse(
        output,
        media_type="application/zip",
        filename=filename,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-Evidence-Chain-Head": metadata["chain_head_sha256"],
            "X-Evidence-Zip-SHA256": metadata["zip_sha256"],
        },
        background=BackgroundTask(remove_complete_evidence_export, output),
    )
