"""Durable background coordination for optional private evidence archival."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sys
import uuid
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.evidence import append_record, evidence_home
from app.core.ha import control_witness_ready, is_ha_enabled
from app.core.ha_witness import require_write_permit
from app.db.database import SessionLocal
from app.models.evidence import EvidenceArchiveSubmission, EvidenceChainState


LOGGER = logging.getLogger("evidence.archive")
TOOLS = Path("/app/evidence")
if not TOOLS.is_dir():
    TOOLS = Path(__file__).resolve().parents[3] / "deploy" / "evidence"
sys.path.insert(0, str(TOOLS))

import evidence_git_uploader  # noqa: E402
import github_token_client  # noqa: E402
import portable_bundle  # noqa: E402


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def policy() -> evidence_git_uploader.UploaderPolicy:
    return evidence_git_uploader.UploaderPolicy(
        enabled=settings.EVIDENCE_GIT_ARCHIVE_ENABLED,
        repository_id=settings.EVIDENCE_GIT_REPOSITORY_ID,
        controller_id=settings.EVIDENCE_CONTROLLER_ID,
        instance_id=settings.EVIDENCE_ALLOWED_INSTANCE_ID,
        branch_prefix=settings.EVIDENCE_GIT_BRANCH_PREFIX,
        retry_limit=settings.EVIDENCE_GIT_RETRY_LIMIT,
        check_poll_seconds=settings.EVIDENCE_GIT_CHECK_POLL_SECONDS,
        check_timeout_seconds=settings.EVIDENCE_GIT_CHECK_TIMEOUT_SECONDS,
    )


def client() -> github_token_client.GitHubTokenClient:
    return github_token_client.GitHubTokenClient(github_token_client.GitHubTokenConfiguration(
        api_base_url=settings.EVIDENCE_GIT_API_BASE_URL,
        owner=settings.EVIDENCE_GIT_REPOSITORY_OWNER,
        repository=settings.EVIDENCE_GIT_REPOSITORY_NAME,
        repository_id=settings.EVIDENCE_GIT_REPOSITORY_ID,
        default_branch=settings.EVIDENCE_GIT_DEFAULT_BRANCH,
        token_path=Path(settings.EVIDENCE_GITHUB_FINE_GRAINED_TOKEN_PATH),
    ))


def _latest_record() -> dict[str, Any] | None:
    paths = sorted((evidence_home() / "ledger").glob("[0-9]" * 12 + "_*.json"))
    if not paths:
        return None
    try:
        value = json.loads(paths[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _bundle_directory() -> Path:
    path = Path(settings.EVIDENCE_GIT_BUNDLE_DIR)
    if path.is_symlink():
        raise RuntimeError("evidence_archive_queue_path_is_symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("evidence_archive_queue_path_is_unsafe")
    os.chmod(path, 0o700)
    return path


def enqueue_current_chain(db: Session) -> EvidenceArchiveSubmission | None:
    """Create one durable idempotent row without blocking local ledger writes."""

    if not settings.EVIDENCE_GIT_ARCHIVE_ENABLED:
        return None
    state = db.get(EvidenceChainState, 1)
    if state is None or not state.head_sha256:
        return None
    latest = _latest_record()
    if latest and latest.get("record_type") == "evidence.git_archive_completed":
        return None
    existing = db.query(EvidenceArchiveSubmission).filter(
        EvidenceArchiveSubmission.repository_id == settings.EVIDENCE_GIT_REPOSITORY_ID,
        EvidenceArchiveSubmission.instance_id == state.instance_id,
        EvidenceArchiveSubmission.chain_head_sha256 == state.head_sha256,
    ).first()
    if existing is not None:
        return existing
    output = _bundle_directory() / f"{state.head_sha256}.evidence.bundle"
    if output.exists():
        summary = portable_bundle.verify_bundle(
            output,
            expected_controller_id=settings.EVIDENCE_CONTROLLER_ID,
            expected_instance_id=state.instance_id,
        )
    else:
        summary = portable_bundle.create_from_local(
            evidence_home(),
            Path(settings.EVIDENCE_TRUST_ROOT),
            state.instance_id,
            output,
        )
    if summary["chain_head_sha256"] != state.head_sha256:
        raise RuntimeError("bundle_chain_changed_during_creation")
    row = EvidenceArchiveSubmission(
        submission_id=evidence_git_uploader.submission_id(summary["bundle_sha256"]),
        repository_id=settings.EVIDENCE_GIT_REPOSITORY_ID,
        controller_id=settings.EVIDENCE_CONTROLLER_ID,
        instance_id=state.instance_id,
        bundle_id=summary["bundle_id"],
        bundle_sha256=summary["bundle_sha256"],
        chain_head_sha256=summary["chain_head_sha256"],
        bundle_path=str(output),
        state="pending",
        next_attempt_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _claim(db: Session, worker_id: str, now: datetime) -> EvidenceArchiveSubmission | None:
    query = db.query(EvidenceArchiveSubmission).filter(
        EvidenceArchiveSubmission.state.in_(evidence_git_uploader.ACTIVE_STATES),
        or_(
            EvidenceArchiveSubmission.next_attempt_at.is_(None),
            EvidenceArchiveSubmission.next_attempt_at <= now,
        ),
        or_(
            EvidenceArchiveSubmission.lease_expires_at.is_(None),
            EvidenceArchiveSubmission.lease_expires_at <= now,
            EvidenceArchiveSubmission.lease_owner == worker_id,
        ),
    ).order_by(EvidenceArchiveSubmission.id)
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    row = query.first()
    if row is None:
        return None
    row.lease_owner = worker_id
    row.lease_expires_at = now + timedelta(seconds=settings.EVIDENCE_GIT_LEASE_SECONDS)
    db.commit()
    db.refresh(row)
    return row


def _previous_archived_head(db: Session, row: EvidenceArchiveSubmission) -> str | None:
    previous = db.query(EvidenceArchiveSubmission).filter(
        EvidenceArchiveSubmission.repository_id == row.repository_id,
        EvidenceArchiveSubmission.instance_id == row.instance_id,
        EvidenceArchiveSubmission.state == "verified",
        EvidenceArchiveSubmission.id != row.id,
    ).order_by(EvidenceArchiveSubmission.completed_at.desc()).first()
    return previous.chain_head_sha256 if previous else None


def _append_archive_record(db: Session, row: EvidenceArchiveSubmission) -> None:
    if row.archive_record_sha256:
        return
    completed = _aware(row.completed_at) or utcnow()
    digest = append_record(
        db,
        workflow_type="evidence_git_archive",
        workflow_id=row.bundle_id,
        operation_type="archive_completed",
        record_type="evidence.git_archive_completed",
        payload={
            "submission_id": row.submission_id,
            "archive_repository_id": row.repository_id,
            "controller_id": row.controller_id,
            "bundle_id": row.bundle_id,
            "bundle_sha256": row.bundle_sha256,
            "chain_head_sha256": row.chain_head_sha256,
            "pull_request_number": row.pull_request_number,
            "pull_request_head_sha": row.pull_request_head_sha,
            "merge_commit_sha": row.merge_commit_sha,
            "completed_at": completed.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "archive_status": "verified",
        },
    )
    if not digest:
        raise RuntimeError("archive_record_append_failed")
    row.archive_record_sha256 = digest


def run_archive_cycle(
    *,
    session_factory=SessionLocal,
    provider: Any | None = None,
    now: datetime | None = None,
    worker_id: str | None = None,
    enqueue: bool = True,
) -> dict[str, int]:
    """Run one bounded step only on the current writer and persist all state."""

    if not settings.EVIDENCE_GIT_ARCHIVE_ENABLED:
        return {"disabled": 1}
    if is_ha_enabled() and not control_witness_ready():
        return {"non_holder_skipped": 1}
    require_write_permit(force_refresh=True)
    observed = now or utcnow()
    worker_id = worker_id or f"worker-{uuid.uuid4().hex[:16]}"
    db = session_factory()
    try:
        if enqueue:
            enqueue_current_chain(db)
            db.commit()
        row = _claim(db, worker_id, observed)
        if row is None:
            return {"idle": 1}
        previous = _previous_archived_head(db, row)
        state = evidence_git_uploader.advance_submission(
            row,
            policy=policy(),
            client=provider or client(),
            now=observed,
            previous_archived_chain_head=previous,
        )
        if state == "verified":
            _append_archive_record(db, row)
        row.lease_owner = None
        row.lease_expires_at = None
        db.commit()
        return {state: 1}
    except Exception as exc:
        db.rollback()
        LOGGER.error("Evidence archive cycle failed (%s)", type(exc).__name__)
        raise
    finally:
        db.close()


async def evidence_archive_worker_loop(stop: asyncio.Event) -> None:
    interval = settings.EVIDENCE_GIT_CHECK_POLL_SECONDS
    next_enqueue_at = utcnow()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            continue
        except asyncio.TimeoutError:
            pass
        try:
            observed = utcnow()
            enqueue_due = observed >= next_enqueue_at
            await asyncio.to_thread(
                run_archive_cycle,
                now=observed,
                enqueue=enqueue_due,
            )
            if enqueue_due:
                next_enqueue_at = observed + timedelta(
                    seconds=settings.EVIDENCE_GIT_UPLOAD_SCHEDULE_SECONDS,
                )
        except Exception as exc:
            LOGGER.error("Evidence archive worker step failed (%s)", type(exc).__name__)


def archive_status(db: Session) -> dict[str, Any]:
    state = db.get(EvidenceChainState, 1)
    latest = db.query(EvidenceArchiveSubmission).order_by(EvidenceArchiveSubmission.id.desc()).first()
    pending_count = db.query(EvidenceArchiveSubmission).filter(
        EvidenceArchiveSubmission.state.in_(evidence_git_uploader.ACTIVE_STATES),
    ).count()
    last_success = db.query(EvidenceArchiveSubmission).filter(
        EvidenceArchiveSubmission.state == "verified",
    ).order_by(EvidenceArchiveSubmission.completed_at.desc()).first()
    token_path = Path(settings.EVIDENCE_GITHUB_FINE_GRAINED_TOKEN_PATH)
    token_configured = False
    try:
        token_configured = token_path.is_file() and not token_path.is_symlink() and token_path.stat().st_size > 0
    except OSError:
        pass
    return {
        "format": "mp-opt-evidence-archive-status-v1",
        "enabled": settings.EVIDENCE_GIT_ARCHIVE_ENABLED,
        "authentication": "Fine-grained GitHub personal access token" if token_configured else "Disabled",
        "token_configured": token_configured,
        "token_fingerprint": settings.EVIDENCE_GITHUB_TOKEN_FINGERPRINT if token_configured else None,
        "repository": (
            f"{settings.EVIDENCE_GIT_REPOSITORY_OWNER}/{settings.EVIDENCE_GIT_REPOSITORY_NAME}"
            if settings.EVIDENCE_GIT_REPOSITORY_OWNER and settings.EVIDENCE_GIT_REPOSITORY_NAME else None
        ),
        "repository_id": settings.EVIDENCE_GIT_REPOSITORY_ID or None,
        "default_branch": settings.EVIDENCE_GIT_DEFAULT_BRANCH if settings.EVIDENCE_GIT_ARCHIVE_ENABLED else None,
        "latest_local_chain_head": state.head_sha256 if state else None,
        "latest_bundled_chain_head": latest.chain_head_sha256 if latest else None,
        "latest_archived_chain_head": last_success.chain_head_sha256 if last_success else None,
        "pending_submission_count": pending_count,
        "last_successful_archive_at": last_success.completed_at if last_success else None,
        "submission_id": latest.submission_id if latest else None,
        "state": evidence_git_uploader.DISPLAY_STATES.get(latest.state) if latest else None,
        "pull_request_number": latest.pull_request_number if latest else None,
        "pull_request_head_sha": latest.pull_request_head_sha if latest else None,
        "merge_commit_sha": latest.merge_commit_sha if latest else None,
        "failure_reason": latest.failure_reason if latest else None,
    }


SAFE_RETRY_REASONS = {
    "required_checks_failed", "required_checks_timed_out", "retry_limit_reached",
    "github_api_unavailable", "github_rate_limited", "protected_merge_failed",
    "github_repository_race_or_policy", "default_branch_changed",
}


def retry_submission(db: Session, submission_id: str) -> EvidenceArchiveSubmission:
    row = db.query(EvidenceArchiveSubmission).filter(
        EvidenceArchiveSubmission.submission_id == submission_id,
    ).first()
    if row is None:
        raise ValueError("submission_not_found")
    if row.failure_reason not in SAFE_RETRY_REASONS:
        raise ValueError("submission_requires_configuration_or_evidence_review")
    if row.merge_commit_sha:
        row.state = "awaiting_merge"
    elif row.pull_request_number:
        row.state = "awaiting_checks"
    else:
        row.state = "pending"
    row.failure_reason = None
    row.next_attempt_at = utcnow()
    row.lease_owner = None
    row.lease_expires_at = None
    return row


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage non-secret Evidence Git uploader state")
    commands = parser.add_subparsers(dest="command", required=True)
    retry = commands.add_parser("retry-failed")
    retry.add_argument("--submission-id", required=True)
    commands.add_parser("status")
    arguments = parser.parse_args(argv)
    db = SessionLocal()
    try:
        if arguments.command == "status":
            print(json.dumps(archive_status(db), sort_keys=True, default=str))
            return 0
        row = retry_submission(db, arguments.submission_id)
        db.commit()
        print(json.dumps({"submission_id": row.submission_id, "state": row.state}, sort_keys=True))
        return 0
    except ValueError as exc:
        db.rollback()
        parser.exit(1, f"Evidence archive retry failed: {exc}\n")
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(cli())
