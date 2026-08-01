"""Restart-safe, HA-fenced retention and event-purge scheduling."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
import json
import logging
from typing import Any, Callable

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core import runtime_settings
from app.core.activation_email import recover_stale_deliveries
from app.core.config import settings
from app.core.deletion_cases import create_event_erasure_case
from app.core.ha import control_witness_ready, is_ha_enabled
from app.core.ha_witness import require_write_permit
from app.core.sessions import cleanup_expired_sessions
from app.core.snapshots import _prune_old_snapshots
from app.db.database import SessionLocal
from app.models.audit import AuditLog
from app.models.deletion import DeletionCase
from app.models.event import Event
from app.models.evidence import EvidenceKeyRegistrationChallenge
from app.models.published import PublishSnapshot
from app.models.retention import RetentionSchedulerState
from app.models.user import (
    ActivationEmailDelivery,
    ActivationLink,
    ExchangeCode,
    PasskeyCeremony,
    PasskeyChallenge,
)


logger = logging.getLogger("retention.scheduler")
_POSTGRES_LOCK_KEY = 708_317_303_001

# Every retained class has one named owner and mechanism. Records which require
# human/provider action are deliberately not represented as automatic deletion.
RETENTION_INVENTORY: tuple[dict[str, Any], ...] = (
    {"record_class": "auth_sessions", "mechanism": "scheduled_database_delete", "settings": ["retention_expired_sessions_days", "retention_revoked_sessions_days"]},
    {"record_class": "passkey_challenges", "mechanism": "scheduled_database_delete", "settings": ["challenge_ttl_minutes"]},
    {"record_class": "evidence_key_challenges", "mechanism": "scheduled_database_delete", "settings": []},
    {"record_class": "passkey_ceremonies", "mechanism": "scheduled_database_delete", "settings": ["challenge_ttl_minutes"]},
    {"record_class": "exchange_codes", "mechanism": "scheduled_database_delete", "settings": ["exchange_code_ttl_seconds"]},
    {"record_class": "activation_links", "mechanism": "scheduled_database_delete", "settings": ["retention_used_activation_links_days"]},
    {"record_class": "activation_email_deliveries", "mechanism": "scheduled_database_delete", "settings": ["retention_used_activation_links_days"]},
    {"record_class": "audit_logs", "mechanism": "scheduled_database_delete", "settings": ["audit_log_retention_days"]},
    {"record_class": "publish_snapshots", "mechanism": "scheduled_count_prune", "settings": ["max_snapshots_per_event"]},
    {"record_class": "events", "mechanism": "scheduled_signed_workflow", "settings": ["event_purge_grace_days"]},
    {"record_class": "recovery_packages", "mechanism": "controller_attested_workflow", "settings": []},
    {"record_class": "privacy_tombstones", "mechanism": "restore_replay_guard", "settings": ["EVIDENCE_TOMBSTONE_RETENTION_DAYS"]},
    {"record_class": "evidence_ledger", "mechanism": "controller_repository_policy", "settings": []},
)


def _aware_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def event_purge_due_at(end_date: date, grace_days: int) -> datetime:
    """Return midnight UTC after the inclusive end date and full grace days."""

    if not 1 <= grace_days <= 3650:
        raise ValueError("Event purge grace days must be between 1 and 3650")
    return datetime.combine(
        end_date + timedelta(days=grace_days + 1),
        time.min,
        tzinfo=timezone.utc,
    )


def materialise_event_purge_deadline(
    event: Event,
    db: Session,
    *,
    force: bool = False,
) -> datetime | None:
    """Persist one explicit controller-selected deadline for an event."""

    if event.purge_case_request_id:
        return event.purge_due_at
    if event.end_date is None:
        event.purge_grace_days = None
        event.purge_due_at = None
        return None
    if event.purge_due_at is not None and not force:
        return event.purge_due_at
    grace_days = runtime_settings.get_int("event_purge_grace_days", db)
    event.purge_grace_days = grace_days
    event.purge_due_at = event_purge_due_at(event.end_date, grace_days)
    return event.purge_due_at


def _try_cycle_lock(db: Session) -> bool:
    if db.get_bind().dialect.name != "postgresql":
        return True
    return bool(
        db.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
            {"lock_key": _POSTGRES_LOCK_KEY},
        ).scalar()
    )


def _prune_snapshots(db: Session) -> int:
    before = db.query(func.count(PublishSnapshot.id)).scalar() or 0
    event_ids = [row[0] for row in db.query(PublishSnapshot.event_id).distinct().all()]
    for event_id in event_ids:
        _prune_old_snapshots(event_id, db)
    after = db.query(func.count(PublishSnapshot.id)).scalar() or 0
    return before - after


def _schedule_event_purges(db: Session, now: datetime) -> tuple[int, int]:
    query = db.query(Event).filter(Event.end_date.isnot(None)).order_by(Event.id)
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    scheduled = 0
    started = 0
    for event in query.all():
        if event.purge_case_request_id:
            continue
        if event.purge_due_at is None:
            materialise_event_purge_deadline(event, db)
            scheduled += 1
        if event.purge_due_at is None or _aware_utc(event.purge_due_at) > now:
            continue
        existing = db.query(DeletionCase).filter(
            DeletionCase.event_purge_key == event.evidence_id,
        ).first()
        job = existing or create_event_erasure_case(
            db,
            event,
            processor_approval_required=False,
            initiation_reason="retention_schedule",
            now=now,
        )
        event.purge_case_request_id = job.request_id
        event.purge_started_at = _aware_utc(job.submitted_at) if job.submitted_at else now
        event.status = "purge_pending"
        started += 1
    return scheduled, started


def run_retention_cycle(
    db: Session,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> dict[str, int]:
    """Run one atomic retention cycle and return non-identifying counts."""

    if is_ha_enabled() and not control_witness_ready():
        return {"non_holder_skipped": 1}
    require_write_permit(force_refresh=True)
    now = _aware_utc(now or datetime.now(timezone.utc))
    if not _try_cycle_lock(db):
        return {"lock_skipped": 1}
    state = db.get(RetentionSchedulerState, 1)
    if state is None:
        state = RetentionSchedulerState(id=1)
        db.add(state)
    state.last_started_at = now
    counts: dict[str, int] = {}
    counts["auth_sessions"] = cleanup_expired_sessions(
        db, now=now, commit=False
    )
    counts["activation_deliveries_recovered"] = recover_stale_deliveries(
        db, force=False, now=now
    )
    counts["passkey_challenges"] = db.query(PasskeyChallenge).filter(
        PasskeyChallenge.expires_at <= now
    ).delete(synchronize_session=False)
    counts["evidence_key_challenges"] = db.query(EvidenceKeyRegistrationChallenge).filter(
        (EvidenceKeyRegistrationChallenge.expires_at <= now)
        | EvidenceKeyRegistrationChallenge.used_at.isnot(None)
    ).delete(synchronize_session=False)
    counts["passkey_ceremonies"] = db.query(PasskeyCeremony).filter(
        PasskeyCeremony.expires_at <= now - timedelta(days=1)
    ).delete(synchronize_session=False)
    counts["exchange_codes"] = db.query(ExchangeCode).filter(
        ExchangeCode.expires_at <= now
    ).delete(synchronize_session=False)
    link_cutoff = now - timedelta(
        days=runtime_settings.get_int("retention_used_activation_links_days", db)
    )
    counts["activation_links"] = db.query(ActivationLink).filter(
        (ActivationLink.used_at.isnot(None) & (ActivationLink.used_at < link_cutoff))
        | (ActivationLink.invalidated_at.isnot(None) & (ActivationLink.invalidated_at < link_cutoff))
        | (ActivationLink.expires_at < link_cutoff)
    ).delete(synchronize_session=False)
    counts["activation_email_deliveries"] = db.query(ActivationEmailDelivery).filter(
        ActivationEmailDelivery.started_at < link_cutoff
    ).delete(synchronize_session=False)
    audit_cutoff = now - timedelta(
        days=runtime_settings.get_int("audit_log_retention_days", db)
    )
    counts["audit_logs"] = db.query(AuditLog).filter(
        AuditLog.timestamp < audit_cutoff
    ).delete(synchronize_session=False)
    counts["publish_snapshots"] = _prune_snapshots(db)
    scheduled, started = _schedule_event_purges(db, now)
    counts["event_deadlines_materialised"] = scheduled
    counts["event_purge_cases_started"] = started
    state.cycle_count = (state.cycle_count or 0) + 1
    state.last_completed_at = now
    state.last_success_at = now
    state.last_result = "success"
    state.last_error_code = None
    state.last_counts_json = json.dumps(counts, sort_keys=True, separators=(",", ":"))
    if commit:
        db.commit()
    else:
        db.flush()
    return counts


def run_retention_cycle_once(
    session_factory: Callable[[], Session] = SessionLocal,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Run only on the current writer and require the same online HA permit."""

    if is_ha_enabled() and not control_witness_ready():
        return {"non_holder_skipped": 1}
    require_write_permit(force_refresh=True)
    db = session_factory()
    try:
        return run_retention_cycle(db, now=now)
    except Exception as exc:
        db.rollback()
        try:
            failure_time = _aware_utc(now or datetime.now(timezone.utc))
            failure_state = db.get(RetentionSchedulerState, 1)
            if failure_state is None:
                failure_state = RetentionSchedulerState(id=1)
                db.add(failure_state)
            failure_state.cycle_count = (failure_state.cycle_count or 0) + 1
            failure_state.last_started_at = failure_time
            failure_state.last_completed_at = failure_time
            failure_state.last_result = "failed"
            failure_state.last_error_code = type(exc).__name__[:64]
            failure_state.last_counts_json = "{}"
            db.commit()
        except Exception:
            db.rollback()
        raise
    finally:
        db.close()


async def retention_scheduler_loop(stop: asyncio.Event) -> None:
    """Run bounded cycles until FastAPI shuts down."""

    interval = settings.RETENTION_SCHEDULER_INTERVAL_SECONDS
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            continue
        except asyncio.TimeoutError:
            pass
        try:
            await asyncio.to_thread(run_retention_cycle_once)
        except Exception as exc:
            logger.error(
                "Retention cycle failed (%s)", type(exc).__name__
            )


def retention_status(db: Session) -> dict[str, Any]:
    """Return a bounded root-facing status and the complete class inventory."""

    state = db.get(RetentionSchedulerState, 1)
    counts = {}
    if state and state.last_counts_json:
        try:
            counts = json.loads(state.last_counts_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            counts = {}
    return {
        "format": "mp-opt-retention-status-v1",
        "interval_seconds": settings.RETENTION_SCHEDULER_INTERVAL_SECONDS,
        "cycle_count": state.cycle_count if state else 0,
        "last_started_at": state.last_started_at if state else None,
        "last_completed_at": state.last_completed_at if state else None,
        "last_success_at": state.last_success_at if state else None,
        "last_result": state.last_result if state else None,
        "last_error_code": state.last_error_code if state else None,
        "last_counts": counts,
        "inventory": [dict(item) for item in RETENTION_INVENTORY],
    }
