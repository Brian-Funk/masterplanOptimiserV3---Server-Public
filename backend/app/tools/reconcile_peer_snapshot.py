"""Guarded operator reconciliation for pre-contract completed HA deletion cases."""

from __future__ import annotations

import argparse
from datetime import timezone

from app.core.compliance_receipts import (
    verified_clean_backup_receipt,
    verified_peer_snapshot_resolution_receipt,
)
from app.core.deletion_workflow import reconcile_completed_case_peer_snapshot
from app.db.database import SessionLocal
from app.core.database_tenancy import root_service_context
from app.models.deletion import DeletionCase


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args()
    db = SessionLocal()
    root_service_context(db, scope="peer_snapshot_reconciliation")
    try:
        job = db.query(DeletionCase).filter(DeletionCase.request_id == args.request_id).one()
        if not job.clean_backup_job_id or not job.live_data_purged_at:
            raise ValueError("The completed case has no clean-backup workflow")
        expected = {
            "job_id": job.clean_backup_job_id,
            "instance_id": job.instance_id,
            "workflow_type": "deletion_case",
            "workflow_id": job.request_id,
            "event_ref": job.event_evidence_id,
            "subject_ref": None if job.case_type == "event_erasure" else job.subject_evidence_id,
            "privacy_action_id": job.privacy_action_id,
            "privacy_action_sequence": job.privacy_action_sequence,
            "live_purge_receipt_sha256": job.live_purge_receipt_sha256,
            "live_data_purged_at": job.live_data_purged_at.astimezone(timezone.utc).isoformat(),
        }
        clean = verified_clean_backup_receipt(
            db, job_id=job.clean_backup_job_id, expected=expected,
        )
        peer = verified_peer_snapshot_resolution_receipt(
            db,
            job_id=job.clean_backup_job_id,
            expected={
                **expected,
                "clean_receipt_sha256": clean["receipt_sha256"],
                "replacement_package_id": clean["package_id"],
                "replacement_package_sha256": clean["package_sha256"],
            },
        )
        digest = reconcile_completed_case_peer_snapshot(db, job, peer_receipt=peer)
        db.commit()
        print(f"RECONCILED:{job.request_id}:{digest}")
        return 0
    except Exception as exc:
        db.rollback()
        print(str(exc))
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
