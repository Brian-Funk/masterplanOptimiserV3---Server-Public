"""Contract tests for the current deletion-case and desktop-report workflow."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.v1.admin import ImportSetupIn, _auto_link_event_users
from app.api.v1.publish import TaskIn
from app.core import deletion_cases, deletion_workflow
from app.models.deletion import (
    DeletionCase,
    DeletionChecklistApproval,
    DeletionRequiredProcessor,
    DeletionSubjectScope,
)
from app.models.evidence import BackupInventoryRecord, EvidenceKey, ProcessorIdentity
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


def _set_snapshot_count(monkeypatch, tmp_path, count: int) -> None:
    status = tmp_path / "snapshot-status.json"
    status.write_text(
        f'{{"format":"mp-opt-ha-snapshot-status-v1","local_snapshot_count":{count}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(deletion_cases.settings, "HA_SNAPSHOT_STATUS_PATH", str(status))


def _activate_processor(db, event, *, entity_id="prc-synthetic0001"):
    alternate = entity_id != "prc-synthetic0001"
    key = EvidenceKey(
        key_id="ek-fedcba0987654321" if alternate else "ek-1234567890abcdef",
        public_key="ssh-ed25519 " + ("B" if alternate else "A") * 44,
        public_key_sha256=("e" if alternate else "f") * 64,
        instance_id="11111111-1111-4111-8111-111111111111",
        entity_id=entity_id, role="processor", activated_at=datetime.now(timezone.utc),
    )
    identity = ProcessorIdentity(
        instance_id=key.instance_id, entity_id=entity_id, event_id=event.id,
        event_evidence_id=event.evidence_id, event_display_name=event.name,
        display_label="Synthetic workstation", status="active", active_key_id=key.key_id,
        activated_at=datetime.now(timezone.utc),
    )
    db.add_all([key, identity])
    db.flush()
    return identity, key


def test_processor_assignments_are_snapshotted_per_event(db):
    event, _ = create_test_event(db)
    _activate_processor(db, event)
    _activate_processor(db, event, entity_id="prc-synthetic0002")
    case = _case(db, event, case_type="event_erasure")

    work_orders = deletion_cases.ensure_desktop_work_order(db, case, event=event, subject_ref=None)

    assert {row.processor_entity_id for row in work_orders} == {"prc-synthetic0001", "prc-synthetic0002"}
    requirements = db.query(DeletionRequiredProcessor).filter_by(case_id=case.id).all()
    assert {row.processor_entity_id for row in requirements} == {"prc-synthetic0001", "prc-synthetic0002"}
    for row in requirements:
        assert row.state == "awaiting_desktop"
        assert row.snapshotted_key_id


def _report(work_order, *, outstanding=None):
    return {
        "format": "mp-opt-desktop-deletion-receipt-v2",
        "instance_id": "11111111-1111-4111-8111-111111111111",
        "entity_id": work_order.processor_entity_id,
        "key_id": work_order.processor_key_id,
        "role": "processor",
        "algorithm": "Ed25519",
        "public_key_sha256": "f" * 64,
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
    _activate_processor(db, event)
    subject_ref = "22222222-2222-4222-8222-222222222222"
    case = _case(db, event, subject_ref=subject_ref)
    work_order = deletion_cases.ensure_desktop_work_order(
        db, case, event=event, subject_ref=subject_ref,
    )[0]
    capability = deletion_cases.claim_work_order(work_order)
    report = _report(work_order)

    with pytest.raises(ValueError, match="claim"):
        deletion_cases.apply_desktop_report(
            db, case, work_order, claim_capability="wrong", report=report,
            signature_sha256="a" * 64, evidence_package_json="{}",
            evidence_package_sha256="c" * 64, completed_key_id=work_order.processor_key_id,
            completed_public_key_sha256="f" * 64,
        )

    digest = deletion_cases.apply_desktop_report(
        db, case, work_order, claim_capability=capability, report=report,
        signature_sha256="a" * 64, evidence_package_json="{}",
        evidence_package_sha256="c" * 64, completed_key_id=work_order.processor_key_id,
        completed_public_key_sha256="f" * 64,
    )
    assert case.state == "ready_for_live_purge"
    assert work_order.state == "report_received"
    assert work_order.claim_capability_sha256 is None
    assert deletion_cases.apply_desktop_report(
        db, case, work_order, claim_capability="", report=report,
        signature_sha256="a" * 64, evidence_package_json="{}",
        evidence_package_sha256="c" * 64, completed_key_id=work_order.processor_key_id,
        completed_public_key_sha256="f" * 64,
    ) == digest

    changed = dict(report, outcome="not_deleted")
    with pytest.raises(ValueError, match="different report"):
        deletion_cases.apply_desktop_report(
            db, case, work_order, claim_capability="", report=changed,
            signature_sha256="a" * 64, evidence_package_json="{}",
            evidence_package_sha256="c" * 64, completed_key_id=work_order.processor_key_id,
            completed_public_key_sha256="f" * 64,
        )


def test_expired_desktop_claim_can_be_reissued_without_replaying_old_capability(db):
    """An abandoned desktop claim may be retried, but its bearer token stays dead."""

    event, _ = create_test_event(db)
    _activate_processor(db, event)
    subject_ref = "77777777-7777-4777-8777-777777777777"
    case = _case(db, event, subject_ref=subject_ref)
    work_order = deletion_cases.ensure_desktop_work_order(
        db, case, event=event, subject_ref=subject_ref,
    )[0]
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
            signature_sha256="a" * 64,
            evidence_package_json="{}", evidence_package_sha256="c" * 64,
            completed_key_id=work_order.processor_key_id,
            completed_public_key_sha256="f" * 64,
        )
    deletion_cases.apply_desktop_report(
        db,
        case,
        work_order,
        claim_capability=replacement_capability,
        report=report,
        signature_sha256="a" * 64,
        evidence_package_json="{}", evidence_package_sha256="c" * 64,
        completed_key_id=work_order.processor_key_id,
        completed_public_key_sha256="f" * 64,
    )
    assert work_order.state == "report_received"


def test_unknown_report_fields_and_external_copies_fail_closed(db):
    """Unknown payloads are rejected and known external copies restrict the case."""

    event, _ = create_test_event(db)
    _activate_processor(db, event)
    case = _case(db, event, case_type="event_erasure")
    work_order = deletion_cases.ensure_desktop_work_order(
        db, case, event=event, subject_ref=None,
    )[0]
    report = _report(work_order)
    report["private_note"] = "must never be accepted"
    with pytest.raises(ValueError, match="unknown fields"):
        deletion_cases.validate_report_payload(work_order, report)

    report.pop("private_note")
    report["outstanding_actions"] = ["untracked_external_export"]
    capability = deletion_cases.claim_work_order(work_order)
    deletion_cases.apply_desktop_report(
        db, case, work_order, claim_capability=capability, report=report,
        signature_sha256="a" * 64, evidence_package_json="{}",
        evidence_package_sha256="c" * 64, completed_key_id=work_order.processor_key_id,
        completed_public_key_sha256="f" * 64,
    )
    assert case.state == "ready_for_live_purge"
    assert case.retention_reason_code == "external_desktop_copy_unresolved"
    deletion_cases.resolve_outstanding_actions(
        db, case, actions=["untracked_external_export"],
    )
    assert case.outstanding_actions_json == "[]"
    assert case.retention_reason_code is None


def test_root_cannot_substitute_for_an_event_processor(db):
    """A required Desktop receipt cannot be replaced by root confirmation."""

    event, _ = create_test_event(db)
    _activate_processor(db, event)
    case = _case(db, event, case_type="event_erasure")
    work_order = deletion_cases.ensure_desktop_work_order(
        db, case, event=event, subject_ref=None,
    )[0]
    deletion_cases.claim_work_order(work_order)

    with pytest.raises(ValueError, match="cannot replace required processor receipts"):
        deletion_cases.confirm_desktop_already_absent(db, case)
    assert case.state == "awaiting_desktop_report"
    assert work_order.state == "claimed"


def test_no_backup_path_requires_explicit_confirmation_and_empty_inventory(db):
    """An empty inventory is not treated as proof until the controller confirms it."""

    event, _ = create_test_event(db)
    case = _case(db, event, case_type="event_erasure")
    deletion_cases.ensure_case_scope(db, case, event=event, subject_ref=None)
    case.desktop_deletion_required = False
    case.live_purge_receipt_sha256 = "b" * 64
    case.outstanding_actions_json = "[]"

    assert "clean_backup_receipt" in deletion_cases.checklist_prerequisites(case, db)
    receipt = deletion_cases.confirm_no_controlled_backups(db, case)
    assert len(receipt) == 64
    assert case.state == "awaiting_checklist"
    checklist = deletion_cases.build_checklist(case, db)
    assert checklist["version"] == 3
    assert checklist["receipts"]["backup_not_applicable_sha256"] == receipt


def test_no_backup_confirmation_rejects_recorded_host_snapshots(db, monkeypatch, tmp_path):
    event, _ = create_test_event(db)
    case = _case(db, event, case_type="event_erasure")
    case.live_purge_receipt_sha256 = "b" * 64
    status = tmp_path / "snapshot-status.json"
    status.write_text(
        '{"format":"mp-opt-ha-snapshot-status-v1","local_snapshot_count":1}',
        encoding="utf-8",
    )
    monkeypatch.setattr(deletion_cases.settings, "HA_SNAPSHOT_STATUS_PATH", str(status))

    with pytest.raises(ValueError, match="snapshots are recorded"):
        deletion_cases.confirm_no_controlled_backups(db, case)


def test_checklist_rejects_superseded_local_snapshots(db, monkeypatch, tmp_path):
    event, _ = create_test_event(db)
    case = _case(db, event, case_type="event_erasure")
    deletion_cases.ensure_case_scope(db, case, event=event, subject_ref=None)
    case.desktop_deletion_required = False
    case.live_purge_receipt_sha256 = "b" * 64
    case.replacement_package_sha256 = "c" * 64
    case.outstanding_actions_json = "[]"
    _set_snapshot_count(monkeypatch, tmp_path, 2)

    assert "local_snapshot_resolution" in deletion_cases.checklist_prerequisites(case, db)
    with pytest.raises(ValueError, match="required actions remain"):
        deletion_cases.build_checklist(case, db)


def test_checklist_is_content_bound_and_requires_all_approvals(db, monkeypatch, tmp_path):
    """A frozen checklist cannot complete before its required passkey approvals."""

    event, _ = create_test_event(db)
    case = _case(db, event, subject_ref="33333333-3333-4333-8333-333333333333")
    case.desktop_deletion_required = False
    deletion_cases.ensure_case_scope(
        db, case, event=event, subject_ref=case.subject_evidence_id,
    )
    with pytest.raises(ValueError, match="required actions remain"):
        deletion_cases.build_checklist(case, db)
    case.live_purge_receipt_sha256 = "b" * 64
    case.replacement_package_sha256 = "c" * 64
    case.outstanding_actions_json = "[]"
    _set_snapshot_count(monkeypatch, tmp_path, 1)
    checklist = deletion_cases.build_checklist(case, db)
    first_hash = case.checklist_sha256
    assert deletion_cases.build_checklist(case, db) == checklist
    assert case.checklist_sha256 == first_hash

    deletion_cases.record_checklist_approval(
        db, case, role="executor", user_id=None, credential_sha256="d" * 64,
    )
    assert case.state == "ready_for_completion"
    assert deletion_cases.complete_case(case, db)
    assert case.state == "complete"
    assert case.event_display_name is None
    scope = db.query(DeletionSubjectScope).filter_by(case_id=case.id).one()
    assert scope.state == "complete"


def test_non_root_completion_approvals_are_rejected(db, monkeypatch, tmp_path):
    """Processor receipts are automatic; only root confirms Server closure."""

    event, _ = create_test_event(db)
    case = _case(db, event, subject_ref="88888888-8888-4888-8888-888888888888")
    case.desktop_deletion_required = False
    deletion_cases.ensure_case_scope(
        db, case, event=event, subject_ref=case.subject_evidence_id,
    )
    case.live_purge_receipt_sha256 = "b" * 64
    case.replacement_package_sha256 = "c" * 64
    case.outstanding_actions_json = "[]"
    _set_snapshot_count(monkeypatch, tmp_path, 1)
    deletion_cases.build_checklist(case, db)

    deletion_cases.record_checklist_approval(
        db, case, role="executor", user_id=None, credential_sha256="d" * 64,
    )
    assert case.state == "ready_for_completion"
    for role in ("controller", "processor"):
        with pytest.raises(ValueError, match="Only the root executor"):
            deletion_cases.record_checklist_approval(
                db, case, role=role, user_id=None, credential_sha256="e" * 64,
            )


def test_completion_revalidates_checklist_content_and_approval_rows(db, monkeypatch, tmp_path):
    """Finalisation fails closed if approved evidence or approval rows change."""

    event, _ = create_test_event(db)
    case = _case(db, event, subject_ref="99999999-9999-4999-8999-999999999999")
    case.desktop_deletion_required = False
    deletion_cases.ensure_case_scope(
        db, case, event=event, subject_ref=case.subject_evidence_id,
    )
    case.live_purge_receipt_sha256 = "b" * 64
    case.replacement_package_sha256 = "c" * 64
    case.outstanding_actions_json = "[]"
    _set_snapshot_count(monkeypatch, tmp_path, 1)
    deletion_cases.build_checklist(case, db)
    deletion_cases.record_checklist_approval(
        db, case, role="executor", user_id=None, credential_sha256="d" * 64,
    )

    case.replacement_package_sha256 = "f" * 64
    with pytest.raises(ValueError, match="no longer matches"):
        deletion_cases.complete_case(case, db)
    case.replacement_package_sha256 = "c" * 64

    executor_approval = db.query(DeletionChecklistApproval).filter_by(
        case_id=case.id,
        checklist_sha256=case.checklist_sha256,
        role="executor",
    ).one()
    db.delete(executor_approval)
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
    _activate_processor(db, event)
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
    )[0]
    assert work_order.subject_ref == person.evidence_subject_id


def test_setup_import_requires_and_preserves_current_evidence_identities():
    """The sole current import contract cannot silently replace desktop UUIDs."""

    event_ref = "55555555-5555-4555-8555-555555555555"
    subject_ref = "66666666-6666-4666-8666-666666666666"
    parsed = ImportSetupIn.model_validate({
        "event": {"evidence_id": event_ref, "name": "Current event"},
        "publish_secret": "p" * 48,
        "idempotency_key": "77777777-7777-4777-8777-777777777777",
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
            "publish_secret": "p" * 48,
            "idempotency_key": "88888888-8888-4888-8888-888888888888",
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


def test_clean_backup_registration_is_reusable_across_deletion_cases(db):
    """One post-deletion package can close several concurrent deletion cases."""

    package_id = "77777777-7777-4777-8777-777777777777"
    for _ in range(2):
        deletion_workflow.record_clean_backup(
            db,
            package_id=package_id,
            package_sha256="a" * 64,
            archive_sha256="b" * 64,
            recovery_key_id="rk-" + "c" * 16,
        )
    assert db.query(BackupInventoryRecord).filter_by(package_id=package_id).count() == 1
    source = Path(deletion_workflow.__file__).read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock" in source


def test_pre_deletion_portable_exports_require_explicit_resolution(db):
    old_package_id = "66666666-6666-4666-8666-666666666666"
    replacement_package_id = "77777777-7777-4777-8777-777777777777"
    deletion_workflow.record_superseded_portable_backups(
        db,
        packages=[{
            "package_id": old_package_id,
            "package_sha256": "a" * 64,
            "archive_sha256": "b" * 64,
            "recovery_key_id": "rk-" + "c" * 16,
            "snapshot_created_at": "2026-08-01T10:00:00+00:00",
            "portable_confirmed_at": "2026-08-01T10:05:00+00:00",
        }],
        replacement_package_id=replacement_package_id,
    )
    deletion_workflow.record_clean_backup(
        db,
        package_id=replacement_package_id,
        package_sha256="d" * 64,
        archive_sha256="e" * 64,
        recovery_key_id="rk-" + "f" * 16,
    )

    old = db.query(BackupInventoryRecord).filter_by(package_id=old_package_id).one()
    replacement = db.query(BackupInventoryRecord).filter_by(package_id=replacement_package_id).one()
    assert old.status == "superseded_pending_deletion"
    assert old.replacement_package_id == replacement_package_id
    assert replacement.status == "active"
    assert "backup_inventory_resolution" in deletion_cases.checklist_prerequisites(
        _case(db, create_test_event(db)[0], case_type="event_erasure"), db,
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
