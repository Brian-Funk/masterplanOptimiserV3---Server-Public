"""Phase 3 retention scheduling and whole-event purge orchestration."""

from datetime import date, datetime, timedelta, timezone

from app.core import evidence, retention
from app.models.deletion import DeletionCase, DesktopDeletionWorkOrder
from app.models.event import Event
from app.models.audit import AuditLog
from app.models.published import PublishSnapshot
from app.models.retention import RetentionSchedulerState
from app.models.server_setting import ServerSetting
from app.models.user import (
    ActivationEmailDelivery,
    ActivationLink,
    AuthSession,
    ExchangeCode,
    PasskeyCeremony,
    PasskeyChallenge,
)
from app.db.database import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from server_backend.conftest import (
    create_test_event,
    create_test_user,
)
from server_backend.test_publish import _MINIMAL_PAYLOAD, _publish_client
import pytest


def _prepare_due_event(db, *, grace_days=2):
    evidence.initialise(db)
    db.add(ServerSetting(key="event_purge_grace_days", value=str(grace_days)))
    event, _secret = create_test_event(db, name="Synthetic retention event")
    event.end_date = date(2026, 7, 1)
    retention.materialise_event_purge_deadline(event, db, force=True)
    db.commit()
    return event


def test_event_purge_starts_at_exact_grace_boundary_without_bypassing_root(db):
    event = _prepare_due_event(db)
    expected_due = datetime(2026, 7, 4, tzinfo=timezone.utc)
    assert event.purge_due_at.replace(tzinfo=timezone.utc) == expected_due
    assert event.purge_grace_days == 2

    before = retention.run_retention_cycle(
        db, now=expected_due - timedelta(microseconds=1)
    )
    assert before["event_purge_cases_started"] == 0
    assert db.query(DeletionCase).count() == 0

    at_boundary = retention.run_retention_cycle(db, now=expected_due)
    assert at_boundary["event_purge_cases_started"] == 1
    case = db.query(DeletionCase).one()
    db.refresh(event)
    assert case.initiation_reason == "retention_schedule"
    assert case.event_purge_key == event.evidence_id
    assert case.state == "submitted"
    assert case.request_manifest_sha256 and len(case.request_manifest_sha256) == 64
    assert case.decision_at is None
    assert case.access_revoked_at is None
    assert case.privacy_action_id is None
    assert db.query(DesktopDeletionWorkOrder).count() == 0
    assert event.purge_case_request_id == case.request_id
    assert event.status == "purge_pending"


def test_restart_and_repeat_cycles_reuse_the_same_signed_event_case(db):
    event = _prepare_due_event(db, grace_days=1)
    due = datetime(2026, 7, 3, tzinfo=timezone.utc)

    first = retention.run_retention_cycle(db, now=due)
    first_request_id = event.purge_case_request_id
    second = retention.run_retention_cycle(db, now=due + timedelta(hours=6))

    assert first["event_purge_cases_started"] == 1
    assert second["event_purge_cases_started"] == 0
    assert db.query(DeletionCase).count() == 1
    db.refresh(event)
    assert event.purge_case_request_id == first_request_id
    state = db.get(RetentionSchedulerState, 1)
    assert state.cycle_count == 2
    assert state.last_result == "success"


def test_deadline_is_stable_when_the_global_grace_setting_changes(db):
    event = _prepare_due_event(db, grace_days=5)
    original_due = event.purge_due_at
    row = db.query(ServerSetting).filter_by(key="event_purge_grace_days").one()
    row.value = "30"
    db.commit()

    retention.materialise_event_purge_deadline(event, db)
    assert event.purge_grace_days == 5
    assert event.purge_due_at == original_due

    event.end_date = date(2026, 8, 1)
    retention.materialise_event_purge_deadline(event, db, force=True)
    assert event.purge_grace_days == 30
    assert event.purge_due_at.replace(tzinfo=timezone.utc) == datetime(
        2026, 9, 1, tzinfo=timezone.utc
    )


def test_retention_inventory_names_automated_and_controller_managed_classes(db):
    status = retention.retention_status(db)
    mechanisms = {
        row["record_class"]: row["mechanism"] for row in status["inventory"]
    }
    assert mechanisms == {
        "auth_sessions": "scheduled_database_delete",
        "passkey_challenges": "scheduled_database_delete",
        "evidence_key_challenges": "scheduled_database_delete",
        "passkey_ceremonies": "scheduled_database_delete",
        "exchange_codes": "scheduled_database_delete",
        "activation_links": "scheduled_database_delete",
        "activation_email_deliveries": "scheduled_database_delete",
        "audit_logs": "scheduled_database_delete",
        "publish_snapshots": "scheduled_count_prune",
        "events": "scheduled_signed_workflow",
        "recovery_packages": "controller_attested_workflow",
        "privacy_tombstones": "restore_replay_guard",
        "evidence_ledger": "controller_repository_policy",
    }


def test_non_holder_does_not_open_a_database_session(monkeypatch):
    monkeypatch.setattr(retention, "is_ha_enabled", lambda: True)
    monkeypatch.setattr(retention, "control_witness_ready", lambda: False)

    def forbidden_factory():
        raise AssertionError("non-holder opened a writable scheduler session")

    assert retention.run_retention_cycle_once(forbidden_factory) == {
        "non_holder_skipped": 1
    }


def test_cycle_failure_is_recorded_without_sensitive_error_text(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'failure-status.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(autoflush=False, bind=engine)

    def fail_cycle(_db, **_kwargs):
        raise RuntimeError("synthetic detail must not be persisted")

    monkeypatch.setattr(retention, "run_retention_cycle", fail_cycle)
    with pytest.raises(RuntimeError, match="synthetic detail"):
        retention.run_retention_cycle_once(factory)

    verification_db = factory()
    try:
        state = verification_db.get(RetentionSchedulerState, 1)
        assert state.last_result == "failed"
        assert state.last_error_code == "RuntimeError"
        assert "synthetic" not in (state.last_counts_json or "")
    finally:
        verification_db.close()
        engine.dispose()


def test_one_cycle_enforces_every_automated_database_retention_class(db):
    now = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    event, _secret = create_test_event(db, name="Synthetic cleanup event")
    user = create_test_user(db, username="synthetic.retention", event_id=event.id)
    db.add(ServerSetting(key="max_snapshots_per_event", value="5"))
    old = now - timedelta(days=100)
    link = ActivationLink(
        token_hash="a" * 64,
        user_id=user.id,
        expires_at=old,
        invalidated_at=old,
    )
    db.add(link)
    db.flush()
    db.add_all([
        AuthSession(
            user_id=user.id,
            session_token="expired-session",
            csrf_token="synthetic-csrf",
            expires_at=old,
        ),
        PasskeyChallenge(
            challenge="expired-challenge",
            user_id=user.id,
            challenge_type="authentication",
            expires_at=old,
        ),
        PasskeyCeremony(
            id="expired-ceremony",
            challenge="expired-ceremony-challenge",
            purpose="authentication",
            user_id=user.id,
            expires_at=old,
        ),
        ExchangeCode(
            code="expired-code",
            user_id=user.id,
            expires_at=old,
        ),
        ActivationEmailDelivery(
            activation_link_id=link.id,
            user_id=user.id,
            recipient_email="synthetic@example.test",
            purpose="initial_setup",
            status="failed",
            started_at=old,
        ),
        AuditLog(timestamp=old, action="synthetic.retention.test"),
    ])
    for version in range(1, 8):
        db.add(PublishSnapshot(
            event_id=event.id,
            version=version,
            snapshot_json="{}",
            content_hash=f"{version:064x}",
            frozen=version == 1,
        ))
    db.commit()

    counts = retention.run_retention_cycle(db, now=now)

    for key in (
        "auth_sessions",
        "passkey_challenges",
        "passkey_ceremonies",
        "exchange_codes",
        "activation_links",
        "activation_email_deliveries",
        "audit_logs",
        "publish_snapshots",
    ):
        assert counts[key] >= 1, (key, counts)
    assert db.query(AuthSession).count() == 0
    assert db.query(PasskeyChallenge).count() == 0
    assert db.query(PasskeyCeremony).count() == 0
    assert db.query(ExchangeCode).count() == 0
    assert db.query(ActivationLink).count() == 0
    assert db.query(ActivationEmailDelivery).count() == 0
    assert db.query(AuditLog).count() == 0
    assert db.query(PublishSnapshot).count() == 5
    assert db.query(PublishSnapshot).filter_by(version=1).one().frozen is True


def test_admin_event_contract_exposes_materialised_deadline_and_inventory(
    db, root_client
):
    response = root_client.post(
        "/api/v1/admin/events",
        json={
            "name": "Synthetic scheduled event",
            "end_date": "2026-08-01",
        },
    )
    assert response.status_code == 200, response.json()
    event = response.json()["event"]
    assert event["purge_grace_days"] == 90
    assert event["purge_due_at"].startswith("2026-10-31T00:00:00")
    assert event["purge_case_request_id"] is None

    status = root_client.get("/api/v1/admin/retention/status")
    assert status.status_code == 200
    body = status.json()
    assert body["format"] == "mp-opt-retention-status-v1"
    assert body["interval_seconds"] >= 60
    assert len(body["inventory"]) == len(retention.RETENTION_INVENTORY)


def test_imported_setup_materialises_and_returns_the_event_deadline(
    db, reauth_admin_client
):
    response = reauth_admin_client.post(
        "/api/v1/admin/import-setup",
        json={
            "event": {
                "evidence_id": "11111111-1111-4111-8111-111111111112",
                "name": "Synthetic imported event",
                "end_date": "2026-08-01",
            },
            "users": [],
        },
    )

    assert response.status_code == 200, response.json()
    event = response.json()["event"]
    assert event["purge_grace_days"] == 90
    assert event["purge_due_at"].startswith("2026-10-31T00:00:00")


def test_schedule_publish_is_blocked_after_event_purge_case_starts(db):
    event, secret = create_test_event(db, name="Synthetic purge pending")
    event.purge_case_request_id = "11111111-1111-4111-8111-111111111111"
    event.status = "purge_pending"
    db.commit()

    response = _publish_client(secret).post(
        "/api/v1/publish/publish", json=_MINIMAL_PAYLOAD
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "EVENT_PURGE_IN_PROGRESS"


def test_general_schedule_publish_reschedules_an_event_deadline(db):
    event, secret = create_test_event(db, name="Synthetic general schedule")
    event.end_date = date(2026, 8, 1)
    retention.materialise_event_purge_deadline(event, db, force=True)
    original_due = event.purge_due_at
    db.commit()

    response = _publish_client(secret).post(
        "/api/v1/publish/general-schedule",
        json={
            "event": {"end_date": "2026-08-10"},
            "fingerprint": "synthetic-retention-reschedule",
            "schedule_views": [],
            "items": [],
        },
    )

    assert response.status_code == 200, response.json()
    db.refresh(event)
    assert event.purge_due_at != original_due
    assert event.purge_due_at.replace(tzinfo=timezone.utc) == datetime(
        2026, 11, 9, tzinfo=timezone.utc
    )
