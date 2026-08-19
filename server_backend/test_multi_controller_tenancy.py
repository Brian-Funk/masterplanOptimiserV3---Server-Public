"""Cross-controller and cross-event authorization regression coverage."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

import pytest

from app.models.deletion import DeletionCase
from app.models.evidence import ControllerEvidenceChainState, EvidenceKey, EvidenceOperation
from app.models.server_setting import ServerSetting
from app.core import evidence
from app.core.database_tenancy import root_service_context
from app.core.config import settings
from app.models.published import PublishedPerson, PublishedPersonUnavailability
from app.models.tenancy import (
    Controller,
    EventGovernanceConfiguration,
    OperatorPolicyPublication,
)
from app.core.retention import materialise_event_purge_deadline
from server_backend.conftest import create_test_event, create_test_user, _make_client


def _controller(db, code: str, label: str) -> Controller:
    row = Controller(code=code, display_name=label, status="active")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _event_account(db, event, *, username: str, role: str = "user"):
    user = create_test_user(
        db,
        username=username,
        display_name=username.replace(".", " ").title(),
        event_id=event.id,
        is_admin=role == "admin",
        is_issuer=role == "issuer",
        can_edit=role == "editor",
    )
    return user, _make_client(db, user, reauth=role in {"admin", "issuer"})


def _seed_unavailability(db, event, *, person_id: int, first_name: str):
    db.add(PublishedPerson(
        event_id=event.id,
        external_person_id=person_id,
        first_name=first_name,
        last_name="Synthetic",
    ))
    db.add(PublishedPersonUnavailability(
        event_id=event.id,
        external_person_id=person_id,
        working_date="2027-07-20",
        start_datetime="2027-07-20T09:00:00+02:00",
        end_datetime="2027-07-20T10:00:00+02:00",
    ))
    db.commit()


def test_non_root_roles_cannot_enumerate_other_event_or_controller(db):
    controller_a = _controller(db, "controller-a", "Controller A")
    controller_b = _controller(db, "controller-b", "Controller B")
    event_a1, _ = create_test_event(db, "A1", controller_id=controller_a.id)
    event_a2, _ = create_test_event(db, "A2", controller_id=controller_a.id)
    event_b1, _ = create_test_event(db, "B1", controller_id=controller_b.id)

    for role in ("user", "editor", "issuer", "admin"):
        _, client = _event_account(db, event_a1, username=f"{role}.a1", role=role)
        assert client.get("/api/v1/calendar/%s" % event_a2.id).status_code == 404
        assert client.get("/api/v1/calendar/%s" % event_b1.id).status_code == 404
        assert client.get("/api/v1/notifications/announcements/%s" % event_a2.id).status_code == 404
        if role == "admin":
            events = client.get("/api/v1/admin/events")
            assert events.status_code == 200
            assert [item["id"] for item in events.json()] == [event_a1.id]
        if role in {"issuer", "admin"}:
            users = client.get("/api/v1/admin/users")
            assert users.status_code == 200
            assert {item["event_id"] for item in users.json()} == {event_a1.id}
        assert client.get("/api/v1/admin/controllers").status_code == 403


def test_every_authenticated_event_account_sees_all_event_unavailability_only(db):
    controller_a = _controller(db, "controller-a", "Controller A")
    controller_b = _controller(db, "controller-b", "Controller B")
    event_a, _ = create_test_event(db, "A", controller_id=controller_a.id)
    event_b, _ = create_test_event(db, "B", controller_id=controller_b.id)
    _seed_unavailability(db, event_a, person_id=101, first_name="Alex")
    _seed_unavailability(db, event_a, person_id=102, first_name="Blair")
    _seed_unavailability(db, event_b, person_id=201, first_name="Casey")

    for role in ("user", "editor", "issuer", "admin"):
        _, client = _event_account(db, event_a, username=f"availability.{role}", role=role)
        response = client.get(f"/api/v1/calendar/{event_a.id}")
        assert response.status_code == 200
        payload = response.json()
        assert {item["person_id"] for item in payload["unavailabilities"]} == {101, 102}
        assert {item["first_name"] for item in payload["persons"]} == {"Alex", "Blair"}
        assert 201 not in {item["person_id"] for item in payload["unavailabilities"]}


def test_event_admin_deletion_listing_is_exact_event_scoped(db):
    controller = _controller(db, "controller-a", "Controller A")
    event_a, _ = create_test_event(db, "A", controller_id=controller.id)
    event_b, _ = create_test_event(db, "B", controller_id=controller.id)
    _, client = _event_account(db, event_a, username="privacy.admin", role="admin")
    now = datetime.now(timezone.utc)
    own = DeletionCase(
        controller_id=controller.id,
        event_id=event_a.id,
        instance_id="11111111-1111-4111-8111-111111111111",
        event_evidence_id=event_a.evidence_id,
        subject_evidence_id=event_a.evidence_id,
        state="submitted",
        normal_response_due_at=now,
    )
    other = DeletionCase(
        controller_id=controller.id,
        event_id=event_b.id,
        instance_id="11111111-1111-4111-8111-111111111111",
        event_evidence_id=event_b.evidence_id,
        subject_evidence_id=event_b.evidence_id,
        state="submitted",
        normal_response_due_at=now,
    )
    db.add_all([own, other])
    db.commit()

    listing = client.get("/api/v1/admin/deletion-requests")
    assert listing.status_code == 200
    assert [item["request_id"] for item in listing.json()] == [own.request_id]
    assert client.get(f"/api/v1/admin/deletion-requests/{other.request_id}").status_code == 404


def test_root_remains_explicitly_global_without_support_grants(db, root_client):
    controller_a = _controller(db, "controller-a", "Controller A")
    controller_b = _controller(db, "controller-b", "Controller B")
    event_a, _ = create_test_event(db, "A", controller_id=controller_a.id)
    event_b, _ = create_test_event(db, "B", controller_id=controller_b.id)
    create_test_user(db, username="a.person", event_id=event_a.id)
    create_test_user(db, username="b.person", event_id=event_b.id)

    events = root_client.get("/api/v1/admin/events")
    assert events.status_code == 200
    event_ids = {item["id"] for item in events.json()}
    assert {event_a.id, event_b.id}.issubset(event_ids)
    users = root_client.get("/api/v1/admin/users")
    assert users.status_code == 200
    assert {event_a.id, event_b.id}.issubset({item["event_id"] for item in users.json()})
    assert root_client.get(f"/api/v1/calendar/{event_a.id}").status_code == 200
    assert root_client.get(f"/api/v1/calendar/{event_b.id}").status_code == 200


def test_username_identity_is_event_scoped_not_instance_global(db):
    controller = _controller(db, "controller-a", "Controller A")
    first, _ = create_test_event(db, "First", controller_id=controller.id)
    second, _ = create_test_event(db, "Second", controller_id=controller.id)
    one = create_test_user(db, username="same.login", event_id=first.id)
    two = create_test_user(db, username="same.login", event_id=second.id)
    assert one.id != two.id
    assert one.event_id != two.event_id


def test_event_ownership_and_controller_trust_identity_are_immutable(db):
    controller_a = _controller(db, "immutable-a", "Immutable A")
    controller_b = _controller(db, "immutable-b", "Immutable B")
    event, _ = create_test_event(db, "Immutable Event", controller_id=controller_a.id)

    with pytest.raises(ValueError, match="ownership is immutable"):
        event.controller_id = controller_b.id
    with pytest.raises(ValueError, match="trust identity is immutable"):
        controller_a.public_id = str(uuid.uuid4())
    with pytest.raises(ValueError, match="trust identity is immutable"):
        controller_a.trust_entity_id = "ctl-reassigned000001"
    with pytest.raises(ValueError, match="trust identity is immutable"):
        controller_a.code = "renamed-controller"


def test_hosted_retention_uses_event_bound_immutable_operator_policy(db):
    controller = _controller(db, "retention-controller", "Retention Controller")
    event, _ = create_test_event(db, "Retention Event", controller_id=controller.id)
    event.end_date = date(2027, 7, 24)
    content = json.dumps(
        {"fixed_retention_days": 137},
        sort_keys=True,
        separators=(",", ":"),
    )
    db.add(OperatorPolicyPublication(
        version=11,
        content_json=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        source_json="{}",
        source_sha256=hashlib.sha256(b"{}").hexdigest(),
    ))
    db.add(EventGovernanceConfiguration(
        event_id=event.id,
        controller_id=controller.id,
        event_notice="Synthetic retention notice",
        enabled_optional_features_json="[]",
        contact_routing_json="{}",
        operator_policy_version=11,
        controller_policy_version=1,
        revision=1,
        content_sha256="a" * 64,
    ))
    mode = db.query(ServerSetting).filter(ServerSetting.key == "tenancy_mode").one()
    mode.value = "hosted-multi-controller"
    db.commit()

    due = materialise_event_purge_deadline(event, db, force=True)

    assert event.purge_grace_days == 137
    assert due is not None
    assert due.date() == date(2027, 12, 9)


def test_hosted_smtp_activation_is_event_feature_gated(db):
    controller = _controller(db, "smtp-controller", "SMTP Controller")
    event, _ = create_test_event(db, "SMTP Disabled", controller_id=controller.id)
    db.add(EventGovernanceConfiguration(
        event_id=event.id,
        controller_id=controller.id,
        event_notice=None,
        enabled_optional_features_json="[]",
        contact_routing_json="{}",
        operator_policy_version=1,
        controller_policy_version=1,
        revision=1,
        content_sha256="b" * 64,
    ))
    mode = db.query(ServerSetting).filter(ServerSetting.key == "tenancy_mode").one()
    mode.value = "hosted-multi-controller"
    target = create_test_user(
        db,
        username="smtp.target",
        event_id=event.id,
    )
    target.email = "smtp.target@example.test"
    admin = create_test_user(
        db,
        username="smtp.admin",
        event_id=event.id,
        is_admin=True,
    )
    client = _make_client(db, admin, reauth=True)
    db.commit()

    response = client.post(f"/api/v1/admin/users/{target.id}/activation-email")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "event_feature_unavailable"


def test_privileged_non_root_route_matrix_hides_every_foreign_tenant(db):
    """Issuer/admin routes hide same-controller and other-controller events alike."""

    controller_a = _controller(db, "matrix-a", "Matrix A")
    controller_b = _controller(db, "matrix-b", "Matrix B")
    event_a1, _ = create_test_event(db, "Matrix A1", controller_id=controller_a.id)
    event_a2, _ = create_test_event(db, "Matrix A2", controller_id=controller_a.id)
    event_b1, _ = create_test_event(db, "Matrix B1", controller_id=controller_b.id)

    for role in ("issuer", "admin"):
        _, client = _event_account(
            db,
            event_a1,
            username=f"matrix.{role}",
            role=role,
        )
        for foreign in (event_a2, event_b1):
            assert client.get(
                f"/api/v1/admin/events/{foreign.id}/history"
            ).status_code == 404
            assert client.get(
                f"/api/v1/admin/events/{foreign.id}/web-edits"
            ).status_code == 404
            if role == "issuer":
                assert client.get(
                    f"/api/v1/admin/events/{foreign.id}/public-schedule-links"
                ).status_code == 404


def test_migration_contains_forced_rls_for_material_tenant_tables():
    migration = open(
        "deploy/migrations/20260819_multi_controller_tenancy.sql",
        encoding="utf-8",
    ).read()
    assert "ALTER TABLE users FORCE ROW LEVEL SECURITY" in migration
    assert "ALTER TABLE events FORCE ROW LEVEL SECURITY" in migration
    assert "ALTER TABLE audit_log FORCE ROW LEVEL SECURITY" in migration
    assert "ALTER TABLE deletion_cases FORCE ROW LEVEL SECURITY" in migration
    assert "'webauthn_credentials', 'exchange_codes', 'auth_sessions'" in migration
    assert "ARRAY['passkey_challenges', 'passkey_ceremonies']" in migration
    assert "authentication_service" in migration
    assert "processor_identities', 'processor_policy_acknowledgements'" in migration
    assert "mp_opt_rls_event_id()" in migration
    assert "mp_opt_rls_controller_id()" in migration


def test_root_can_configure_controller_enable_hosted_mode_and_create_event(db):
    root = create_test_user(
        db,
        username="host.root",
        display_name="Hosting Root",
        is_root_admin=True,
        is_admin=True,
    )
    client = _make_client(db, root, reauth=True)
    controller = db.get(Controller, 1)
    assert controller is not None

    operator_saved = client.put("/api/v1/admin/operator", json={
        "operator_type": "organisation",
        "operator_legal_name": "Synthetic Hosting Cooperative",
        "operator_postal_address": "1 Test Way, 8000 Zurich",
        "operator_country": "CH",
        "privacy_contact_email": "privacy@synthetic-host.example.com",
        "service_description": "Technical hosting, recovery and security operations.",
        "security_summary": "The operator has disclosed privileged infrastructure access.",
        "subprocessors": [{
            "provider_code": "synthetic_mail",
            "display_name": "Synthetic Mail",
            "purpose_codes": ["activation_email"],
            "hosting_countries": ["CH"],
            "support_access_countries": ["CH"],
            "privacy_url": "https://mail.example.test/privacy",
        }],
        "hosting_regions": ["CH"],
        "fixed_retention_days": 90,
        "dpa_url": "https://operator.example.test/dpa",
        "subprocessor_schedule_url": "https://operator.example.test/subprocessors",
    })
    assert operator_saved.status_code == 200, operator_saved.text
    operator_published = client.post("/api/v1/admin/operator/publications", json={})
    assert operator_published.status_code == 201, operator_published.text
    operator_identity = operator_published.json()

    activated = client.patch(
        f"/api/v1/admin/controllers/{controller.public_id}",
        json={"display_name": "Synthetic Controller", "status": "active"},
    )
    assert activated.status_code == 200, activated.text
    db.add(EvidenceKey(
        key_id="ek-1111111111111111",
        public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAISyntheticControllerKey",
        public_key_sha256="1" * 64,
        instance_id="11111111-1111-4111-8111-111111111111",
        entity_id=controller.trust_entity_id,
        controller_id=controller.id,
        role="controller",
        activated_at=datetime.now(timezone.utc),
    ))
    db.commit()

    controller_saved = client.put(
        f"/api/v1/admin/controllers/{controller.public_id}/governance",
        json={
            "controller_type": "organisation",
            "legal_name": "Synthetic Event Controller",
            "postal_address": "2 Controller Street, 8750 Glarus",
            "country": "CH",
            "privacy_contact_email": "privacy@synthetic-controller.example.com",
            "dpo_contact": None,
            "supervisory_authority_name": "Synthetic Data Authority",
            "supervisory_authority_url": "https://authority.example.test",
            "default_locale": "en",
            "processor_summary": "The hosting operator processes data on behalf of this controller.",
            "rights_summary": "Contact the controller to exercise applicable rights.",
            "terms_summary": "Operational event accounts are event-scoped.",
            "governance": {
                "permitted_data": {
                    "purpose": "Coordinate NSC Glarus",
                    "allowed": ["operational schedules"],
                    "unsupported": ["unrelated private information"],
                },
            },
            "accepted_operator_policy_version": operator_identity["version"],
            "accepted_operator_policy_sha256": operator_identity["sha256"],
        },
    )
    assert controller_saved.status_code == 200, controller_saved.text
    controller_published = client.post(
        f"/api/v1/admin/controllers/{controller.public_id}/governance/publications",
        json={"external_authorisation_ref": "synthetic-board-resolution"},
    )
    assert controller_published.status_code == 201, controller_published.text
    controller_identity = controller_published.json()
    assert controller_identity["evidence_record_sha256"]
    governance_operation = db.query(EvidenceOperation).filter_by(
        workflow_type="controller_governance",
        workflow_id=str(
            uuid.uuid5(
                uuid.UUID(controller.public_id),
                "governance-publication:1",
            )
        ),
        operation_type="published",
    ).one()
    # The compatibility controller must be publishable before hosted mode can
    # be enabled. That pre-hosted publication is sealed in the legacy operator
    # chain; the controller chain created during enablement anchors that head.
    assert governance_operation.chain_scope == "operator"
    assert governance_operation.controller_id is None
    assert governance_operation.record_sha256 == controller_identity["evidence_record_sha256"]

    preflight = client.get("/api/v1/admin/tenancy")
    assert preflight.status_code == 200
    assert preflight.json()["ready"] is True, preflight.text
    enabled = client.put(
        "/api/v1/admin/tenancy/mode",
        json={"mode": "hosted-multi-controller"},
    )
    assert enabled.status_code == 200, enabled.text
    controller_chain = db.get(ControllerEvidenceChainState, controller.id)
    assert controller_chain is not None
    assert controller_chain.legacy_chain_head_sha256 == evidence.verify_local_chain(db)["head_sha256"]

    created = client.post("/api/v1/admin/events", json={
        "name": "NSC Glarus",
        "controller_public_id": controller.public_id,
        "event_notice": "Synthetic event-specific scheduling notice.",
        "enabled_optional_features": ["desktop_publishing", "smtp_activation"],
        "contact_routing": {"privacy": "controller"},
        "location": "Glarus",
        "start_date": "2027-07-18",
        "end_date": "2027-07-24",
        "policy_version": controller_identity["version"],
        "policy_sha256": controller_identity["sha256"],
        "publish_secret": "p" * 48,
        "idempotency_key": "hosted-event-create-0001",
    })
    assert created.status_code == 200, created.text
    event = created.json()["event"]
    assert event["controller_public_id"] == controller.public_id
    assert event["controller_name"] == "Synthetic Controller"

    legal = client.get(f"/api/v1/legal/events/{event['evidence_id']}")
    assert legal.status_code == 200, legal.text
    body = legal.json()
    assert body["controller"]["public_id"] == controller.public_id
    assert body["controller"]["policy"]["legal_name"] == "Synthetic Event Controller"
    assert body["operator"]["policy"]["operator_legal_name"] == "Synthetic Hosting Cooperative"
    assert body["enabled_optional_features"] == ["desktop_publishing", "smtp_activation"]


def test_hosted_evidence_is_split_into_controller_chains(db):
    """Future tenant evidence never enters another controller's export chain."""

    operator_state = evidence.initialise(db)
    assert operator_state is not None
    controller_a = db.get(Controller, 1)
    controller_a.status = "active"
    controller_b = Controller(
        code="evidence-controller-b",
        display_name="Evidence Controller B",
        status="active",
    )
    db.add(controller_b)
    db.flush()
    event_a, _ = create_test_event(db, name="Evidence Event A", controller_id=controller_a.id)
    event_b, _ = create_test_event(db, name="Evidence Event B", controller_id=controller_b.id)
    mode = db.query(ServerSetting).filter(ServerSetting.key == "tenancy_mode").one()
    mode.value = "hosted-multi-controller"
    db.flush()
    root_service_context(db, scope="multi_controller_evidence_test")

    digest_a = evidence.append_record(
        db,
        workflow_type="account_consent",
        workflow_id="00000000-0000-4000-8000-00000000000a",
        operation_type="recorded",
        record_type="account.processing_consent_recorded",
        payload={"event_ref": event_a.evidence_id, "status": "recorded"},
    )
    digest_b = evidence.append_record(
        db,
        workflow_type="account_consent",
        workflow_id="00000000-0000-4000-8000-00000000000b",
        operation_type="recorded",
        record_type="account.processing_consent_recorded",
        payload={"event_ref": event_b.evidence_id, "status": "recorded"},
    )
    db.commit()

    assert digest_a != digest_b
    assert evidence.verify_local_chain(db)["records"] == 1
    assert evidence.verify_controller_chain(db, controller_a.id)["records"] == 2
    assert evidence.verify_controller_chain(db, controller_b.id)["records"] == 2
    assert db.query(ControllerEvidenceChainState).count() == 2
    operations_a = db.query(EvidenceOperation).filter(
        EvidenceOperation.controller_id == controller_a.id
    ).all()
    operations_b = db.query(EvidenceOperation).filter(
        EvidenceOperation.controller_id == controller_b.id
    ).all()
    assert digest_a in {row.record_sha256 for row in operations_a}
    assert digest_a not in {row.record_sha256 for row in operations_b}
    home = Path(settings.EVIDENCE_HOME) / "controllers"
    assert (home / controller_a.trust_entity_id / "ledger").is_dir()
    assert (home / controller_b.trust_entity_id / "ledger").is_dir()
