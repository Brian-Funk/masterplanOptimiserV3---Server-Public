"""Integration tests for destructive server-side deletion after desktop proof."""

from datetime import datetime, timedelta, timezone

from app.core import deletion_cases, deletion_workflow
from app.models.deletion import DeletionCase
from app.models.event import Event
from app.models.evidence import PrivacyActionReceipt
from app.models.user import User
from deploy.evidence.evidence_manifest import RECORD_TYPES, _validate_payload
from server_backend.conftest import create_test_event, create_test_user


HASH = "a" * 64


def _fake_evidence(monkeypatch):
    def append_stub(*args, **kwargs):
        assert kwargs["record_type"] in RECORD_TYPES
        _validate_payload(kwargs["payload"])
        return HASH

    monkeypatch.setattr(deletion_workflow, "append_record", append_stub)
    monkeypatch.setattr(deletion_cases, "append_record", append_stub)
    monkeypatch.setattr(deletion_workflow, "lock_evidence_transaction", lambda _db: None)


def _case(db, event, *, case_type, user=None):
    case = DeletionCase(
        case_type=case_type,
        instance_id="11111111-1111-4111-8111-111111111111",
        event_evidence_id=event.evidence_id,
        subject_evidence_id=(
            user.evidence_subject_id if user is not None else event.evidence_id
        ),
        user_id=user.id if user is not None else None,
        state="submitted",
        normal_response_due_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(case)
    db.flush()
    return case


def _apply_desktop_report(db, case, event):
    subject_ref = (
        None if case.case_type == "event_erasure" else case.subject_evidence_id
    )
    deletion_cases.ensure_case_scope(
        db, case, event=event, subject_ref=subject_ref,
    )
    work_order = deletion_cases.ensure_desktop_work_order(
        db, case, event=event, subject_ref=subject_ref,
    )
    capability = deletion_cases.claim_work_order(work_order)
    report = {
        "version": 1,
        "work_order_id": work_order.work_order_id,
        "event_ref": event.evidence_id,
        "subject_ref": subject_ref,
        "operation": work_order.operation,
        "outcome": "deleted",
        "deleted_counts": {
            "persons": 1,
            "assignments": 0,
            "capability_links": 0,
            "group_memberships": 0,
            "unavailability_intervals": 0,
            "task_references": 0,
            "optimisation_records": 0,
            "publish_records": 0,
            "cached_records": 0,
            "tracked_exports": 0,
            "integration_references": 0,
        },
        "outstanding_actions": [],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    deletion_cases.apply_desktop_report(
        db, case, work_order, claim_capability=capability, report=report,
    )


def test_subject_purge_deletes_account_after_verified_desktop_report(db, monkeypatch):
    _fake_evidence(monkeypatch)
    event, _ = create_test_event(db, name="Subject erasure")
    user = create_test_user(db, username="erase.me", event_id=event.id)
    user_id = user.id
    case = _case(db, event, case_type="personal_data_erasure", user=user)

    deletion_workflow.accept_subject_request(db, case, user)
    assert case.state == "awaiting_desktop_report"
    assert user.is_active is False
    assert db.query(PrivacyActionReceipt).filter_by(
        privacy_action_id=case.privacy_action_id,
    ).one()

    _apply_desktop_report(db, case, event)
    deletion_workflow.purge_subject_live_data(db, case, user)
    db.commit()

    assert db.query(User).filter_by(id=user_id).first() is None
    assert case.user_id is None
    assert case.live_data_purged_at is not None
    assert case.live_purge_receipt_sha256 == HASH


def test_server_only_account_purge_does_not_invent_a_desktop_requirement(db, monkeypatch):
    """An unlinked account still has an executable right-to-erasure path."""

    _fake_evidence(monkeypatch)
    event, _ = create_test_event(db, name="Server-only erasure")
    user = create_test_user(db, username="server.only", event_id=event.id)
    user_id = user.id
    case = _case(db, event, case_type="personal_data_erasure", user=user)
    case.desktop_deletion_required = False

    deletion_workflow.accept_subject_request(db, case, user)
    assert case.state == "ready_for_live_purge"
    assert case.desktop_report_sha256 is None
    deletion_workflow.purge_subject_live_data(db, case, user)
    db.commit()

    assert db.query(User).filter_by(id=user_id).first() is None
    assert case.live_purge_receipt_sha256 == HASH
    assert "desktop_report" not in deletion_cases.checklist_prerequisites(case, db)


def test_event_purge_deletes_complete_non_root_event_scope(db, monkeypatch):
    _fake_evidence(monkeypatch)
    event, _ = create_test_event(db, name="Event erasure")
    ordinary = create_test_user(db, username="event.user", event_id=event.id)
    event_id = event.id
    ordinary_id = ordinary.id
    case = _case(db, event, case_type="event_erasure")

    deletion_workflow.accept_event_request(db, case, event)
    _apply_desktop_report(db, case, event)
    deletion_workflow.purge_event_live_data(db, case, event)
    db.commit()

    assert db.query(Event).filter_by(id=event_id).first() is None
    assert db.query(User).filter_by(id=ordinary_id).first() is None
    assert case.live_data_purged_at is not None
    assert case.live_purge_receipt_sha256 == HASH


def test_event_purge_accepts_explicit_already_absent_desktop_confirmation(db, monkeypatch):
    """The event can continue when its Desktop copy was removed before the work order."""

    _fake_evidence(monkeypatch)
    event, _ = create_test_event(db, name="Already absent locally")
    event_id = event.id
    case = _case(db, event, case_type="event_erasure")
    deletion_workflow.accept_event_request(db, case, event)
    deletion_cases.ensure_desktop_work_order(db, case, event=event, subject_ref=None)
    deletion_cases.confirm_desktop_already_absent(db, case)

    deletion_workflow.purge_event_live_data(db, case, event)
    db.commit()

    assert db.query(Event).filter_by(id=event_id).first() is None
    assert case.live_purge_receipt_sha256 == HASH
