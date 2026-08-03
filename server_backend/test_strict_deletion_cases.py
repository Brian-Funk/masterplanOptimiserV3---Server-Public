"""Contract tests for the current deletion-case and desktop-report workflow."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.v1.admin import ImportSetupIn, _auto_link_event_users
from app.api.v1.publish import TaskIn
from app.core import deletion_cases
from app.models.deletion import (
    DeletionCase,
    DeletionChecklistApproval,
    DeletionSubjectScope,
)
from app.models.published import PublishedPerson
from app.models.user import User
from deploy.evidence.evidence_manifest import RECORD_TYPES, _validate_payload
from server_backend.conftest import create_test_event


def _case(db, event, *, case_type="personal_data_erasure", subject_ref=None):
    case = DeletionCase(
        case_type=case_type,
        instance_id="11111111-1111-4111-8111-111111111111",
        event_evidence_id=event.evidence_id,
        event_display_name=event.name,
        subject_evidence_id=subject_ref or event.evidence_id,
        state="awaiting_desktop_report",
        normal_response_due_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    db.add(case)
    db.flush()
    return case


def _report(work_order, *, outstanding=None):
    return {
        "version": 1,
        "work_order_id": work_order.work_order_id,
        "event_ref": work_order.event_ref,
        "subject_ref": work_order.subject_ref,
        "operation": work_order.operation,
        "outcome": "deleted",
        "deleted_counts": {
            "persons": 1,
            "assignments": 2,
            "capability_links": 3,
            "group_memberships": 4,
            "unavailability_intervals": 5,
            "task_references": 6,
            "optimisation_records": 7,
            "publish_records": 8,
            "cached_records": 9,
            "tracked_exports": 10,
            "integration_references": 11,
        },
        "outstanding_actions": outstanding or [],
        "completed_at": "2026-07-28T10:30:00+00:00",
    }


@pytest.fixture(autouse=True)
def _evidence_stub(monkeypatch):
    def append_stub(_db, **kwargs):
        assert kwargs["record_type"] in RECORD_TYPES
        _validate_payload(kwargs["payload"])
        return deletion_cases.sha256_text(
            f"{kwargs['workflow_id']}:{kwargs['operation_type']}"
        )

    monkeypatch.setattr(
        deletion_cases,
        "append_record",
        append_stub,
    )


def test_desktop_report_is_capability_authorised_exact_and_idempotent(db):
    """Only the claimed current contract may advance a case."""

    event, _ = create_test_event(db)
    subject_ref = "22222222-2222-4222-8222-222222222222"
    case = _case(db, event, subject_ref=subject_ref)
    work_order = deletion_cases.ensure_desktop_work_order(
        db, case, event=event, subject_ref=subject_ref,
    )
    capability = deletion_cases.claim_work_order(work_order)
    report = _report(work_order)

    with pytest.raises(ValueError, match="claim"):
        deletion_cases.apply_desktop_report(
            db, case, work_order, claim_capability="wrong", report=report,
        )

    digest = deletion_cases.apply_desktop_report(
        db, case, work_order, claim_capability=capability, report=report,
    )
    assert case.state == "ready_for_live_purge"
    assert work_order.state == "report_received"
    assert work_order.claim_capability_sha256 is None
    assert deletion_cases.apply_desktop_report(
        db, case, work_order, claim_capability="", report=report,
    ) == digest

    changed = dict(report, outcome="not_deleted")
    with pytest.raises(ValueError, match="different report"):
        deletion_cases.apply_desktop_report(
            db, case, work_order, claim_capability="", report=changed,
        )


def test_expired_desktop_claim_can_be_reissued_without_replaying_old_capability(db):
    """An abandoned desktop claim may be retried, but its bearer token stays dead."""

    event, _ = create_test_event(db)
    subject_ref = "77777777-7777-4777-8777-777777777777"
    case = _case(db, event, subject_ref=subject_ref)
    work_order = deletion_cases.ensure_desktop_work_order(
        db, case, event=event, subject_ref=subject_ref,
    )
    expired_capability = deletion_cases.claim_work_order(work_order)
    with pytest.raises(ValueError, match="active claim"):
        deletion_cases.claim_work_order(work_order)

    work_order.claim_expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    replacement_capability = deletion_cases.claim_work_order(work_order)
    report = _report(work_order)

    with pytest.raises(ValueError, match="claim"):
        deletion_cases.apply_desktop_report(
            db,
            case,
            work_order,
            claim_capability=expired_capability,
            report=report,
        )
    deletion_cases.apply_desktop_report(
        db,
        case,
        work_order,
        claim_capability=replacement_capability,
        report=report,
    )
    assert work_order.state == "report_received"


def test_unknown_report_fields_and_external_copies_fail_closed(db):
    """Unknown payloads are rejected and known external copies restrict the case."""

    event, _ = create_test_event(db)
    case = _case(db, event, case_type="event_erasure")
    work_order = deletion_cases.ensure_desktop_work_order(
        db, case, event=event, subject_ref=None,
    )
    report = _report(work_order)
    report["private_note"] = "must never be accepted"
    with pytest.raises(ValueError, match="unknown fields"):
        deletion_cases.validate_report_payload(work_order, report)

    report.pop("private_note")
    report["outstanding_actions"] = ["untracked_external_export"]
    capability = deletion_cases.claim_work_order(work_order)
    deletion_cases.apply_desktop_report(
        db, case, work_order, claim_capability=capability, report=report,
    )
    assert case.state == "ready_for_live_purge"
    assert case.retention_reason_code == "external_desktop_copy_unresolved"
    deletion_cases.resolve_outstanding_actions(
        db, case, actions=["untracked_external_export"],
    )
    assert case.outstanding_actions_json == "[]"
    assert case.retention_reason_code is None


def test_checklist_is_content_bound_and_requires_all_approvals(db):
    """A frozen checklist cannot complete before its required passkey approvals."""

    event, _ = create_test_event(db)
    case = _case(db, event, subject_ref="33333333-3333-4333-8333-333333333333")
    deletion_cases.ensure_case_scope(
        db, case, event=event, subject_ref=case.subject_evidence_id,
    )
    with pytest.raises(ValueError, match="required actions remain"):
        deletion_cases.build_checklist(case, db)
    case.desktop_report_sha256 = "a" * 64
    case.live_purge_receipt_sha256 = "b" * 64
    case.replacement_package_sha256 = "c" * 64
    case.outstanding_actions_json = "[]"
    checklist = deletion_cases.build_checklist(case, db)
    first_hash = case.checklist_sha256
    assert deletion_cases.build_checklist(case, db) == checklist
    assert case.checklist_sha256 == first_hash

    deletion_cases.record_checklist_approval(
        db, case, role="executor", user_id=None, credential_sha256="d" * 64,
    )
    assert case.state == "awaiting_approvals"
    deletion_cases.record_checklist_approval(
        db, case, role="controller", user_id=None, credential_sha256="e" * 64,
    )
    assert case.state == "ready_for_completion"
    assert deletion_cases.complete_case(case, db)
    assert case.state == "complete"
    assert case.event_display_name is None
    scope = db.query(DeletionSubjectScope).filter_by(case_id=case.id).one()
    assert scope.state == "complete"


def test_processor_approval_is_required_only_when_declared(db):
    """Cases involving a processor cannot complete with controller approval alone."""

    event, _ = create_test_event(db)
    case = _case(db, event, subject_ref="88888888-8888-4888-8888-888888888888")
    deletion_cases.ensure_case_scope(
        db, case, event=event, subject_ref=case.subject_evidence_id,
    )
    case.desktop_report_sha256 = "a" * 64
    case.live_purge_receipt_sha256 = "b" * 64
    case.replacement_package_sha256 = "c" * 64
    case.outstanding_actions_json = "[]"
    case.processor_approval_required = True
    deletion_cases.build_checklist(case, db)

    deletion_cases.record_checklist_approval(
        db, case, role="executor", user_id=None, credential_sha256="d" * 64,
    )
    deletion_cases.record_checklist_approval(
        db, case, role="controller", user_id=None, credential_sha256="e" * 64,
    )
    assert case.state == "awaiting_approvals"
    deletion_cases.record_checklist_approval(
        db, case, role="processor", user_id=None, credential_sha256="f" * 64,
    )
    assert case.state == "ready_for_completion"


def test_completion_revalidates_checklist_content_and_approval_rows(db):
    """Finalisation fails closed if approved evidence or approval rows change."""

    event, _ = create_test_event(db)
    case = _case(db, event, subject_ref="99999999-9999-4999-8999-999999999999")
    deletion_cases.ensure_case_scope(
        db, case, event=event, subject_ref=case.subject_evidence_id,
    )
    case.desktop_report_sha256 = "a" * 64
    case.live_purge_receipt_sha256 = "b" * 64
    case.replacement_package_sha256 = "c" * 64
    case.outstanding_actions_json = "[]"
    deletion_cases.build_checklist(case, db)
    deletion_cases.record_checklist_approval(
        db, case, role="executor", user_id=None, credential_sha256="d" * 64,
    )
    deletion_cases.record_checklist_approval(
        db, case, role="controller", user_id=None, credential_sha256="e" * 64,
    )

    case.replacement_package_sha256 = "f" * 64
    with pytest.raises(ValueError, match="no longer matches"):
        deletion_cases.complete_case(case, db)
    case.replacement_package_sha256 = "c" * 64

    controller_approval = db.query(DeletionChecklistApproval).filter_by(
        case_id=case.id,
        checklist_sha256=case.checklist_sha256,
        role="controller",
    ).one()
    db.delete(controller_approval)
    db.flush()
    with pytest.raises(ValueError, match="approvals are incomplete"):
        deletion_cases.complete_case(case, db)


def test_status_capability_is_stored_only_as_a_hash(db):
    """Post-account-deletion status access uses an unguessable bearer capability."""

    event, _ = create_test_event(db)
    case = _case(db, event)
    raw = deletion_cases.issue_status_capability(case)
    assert raw != case.status_capability_sha256
    assert deletion_cases.verify_status_capability(case, raw)
    assert not deletion_cases.verify_status_capability(case, "wrong")
    with pytest.raises(ValueError, match="already been issued"):
        deletion_cases.issue_status_capability(case)
    case.status_capability_expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert not deletion_cases.verify_status_capability(case, raw)


def test_server_account_binds_to_the_desktop_subject_identity(db):
    """A linked account must produce a work order the desktop can resolve."""

    event, _ = create_test_event(db)
    subject_ref = "44444444-4444-4444-8444-444444444444"
    user = User(
        username="linked.person",
        display_name="Linked Person",
        email="linked@example.test",
        event_id=event.id,
    )
    person = PublishedPerson(
        evidence_subject_id=subject_ref,
        event_id=event.id,
        external_person_id=73,
        first_name="Linked",
        last_name="Person",
        email="linked@example.test",
    )
    db.add_all([user, person])
    db.flush()

    _auto_link_event_users(event.id, db)
    assert user.linked_person_id == 73
    assert user.evidence_subject_id == subject_ref

    case = _case(db, event, subject_ref=user.evidence_subject_id)
    work_order = deletion_cases.ensure_desktop_work_order(
        db, case, event=event, subject_ref=case.subject_evidence_id,
    )
    assert work_order.subject_ref == person.evidence_subject_id


def test_setup_import_requires_and_preserves_current_evidence_identities():
    """The sole current import contract cannot silently replace desktop UUIDs."""

    event_ref = "55555555-5555-4555-8555-555555555555"
    subject_ref = "66666666-6666-4666-8666-666666666666"
    parsed = ImportSetupIn.model_validate({
        "event": {"evidence_id": event_ref, "name": "Current event"},
        "users": [{
            "username": "current.person",
            "display_name": "Current Person",
            "person_id": 9,
            "evidence_subject_id": subject_ref,
        }],
    })
    assert parsed.event.evidence_id == event_ref
    assert parsed.users[0].evidence_subject_id == subject_ref

    with pytest.raises(ValidationError):
        ImportSetupIn.model_validate({
            "event": {"name": "Legacy event"},
            "users": [{"username": "legacy", "display_name": "Legacy"}],
        })


@pytest.mark.parametrize(
    "payload",
    [
        {"field_values": {"medical_notes": "unsupported"}},
        {"additional": {"private_profile": {"value": "unsupported"}}},
        {"field_definitions": [{"id": "dietary_requirements", "name": "Diet"}]},
    ],
)
def test_publish_contract_rejects_private_profiling_fields(payload):
    """Broad structured task payloads cannot be used for sensitive profiling."""

    with pytest.raises(ValidationError):
        TaskIn(
            id=1,
            name="Operational task",
            start="2026-08-01T09:00:00+00:00",
            end="2026-08-01T10:00:00+00:00",
            **payload,
        )


def test_admin_and_frontend_expose_only_the_current_deletion_case_workflow():
    """Retired event-purge and imported-attestation routes cannot return."""

    root = Path(__file__).resolve().parents[1]
    admin_api = (root / "backend/app/api/v1/admin.py").read_text(encoding="utf-8")
    evidence_ui = (
        root / "web/src/components/ComplianceEvidenceTab.tsx"
    ).read_text(encoding="utf-8")
    combined = admin_api + evidence_ui

    assert "/api/v1/admin/deletion-requests/events/" in combined
    assert "/api/v1/admin/evidence/event-purges" not in combined
    assert "/api/v1/admin/evidence/attestations" not in combined
    assert "complete_with_exceptions" not in combined
