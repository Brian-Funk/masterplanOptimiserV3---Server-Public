"""Evidence identity contracts shared by setup import and publishing."""

import uuid

import pytest
from pydantic import TypeAdapter, ValidationError

from app.api.v1.admin import EventCreateIn, ImportSetupIn
from app.api.v1.publish import PersonIn
from app.core import deletion_cases
from app.core.evidence_identity import CanonicalEvidenceIdentity
from app.models.event import Event
from app.models.user import User


UUID4 = "11111111-1111-4111-8111-111111111111"
UUID5_EVENT = str(uuid.uuid5(uuid.NAMESPACE_URL, "mp-opt:event:converted"))
UUID5_SUBJECT = str(uuid.uuid5(uuid.NAMESPACE_URL, "mp-opt:person:converted"))


@pytest.mark.parametrize("identity", [UUID4, UUID5_EVENT, UUID5_SUBJECT])
def test_evidence_identity_accepts_canonical_uuid4_and_uuid5(identity):
    assert TypeAdapter(CanonicalEvidenceIdentity).validate_python(identity) == identity


@pytest.mark.parametrize(
    "identity",
    [
        "00000000-0000-0000-0000-000000000000",
        "11111111-1111-1111-8111-111111111111",
        "11111111-1111-3111-8111-111111111111",
        "11111111-1111-4111-7111-111111111111",
        UUID5_EVENT.upper(),
        UUID5_EVENT.replace("-", ""),
        "not-a-uuid",
    ],
)
def test_evidence_identity_rejects_unsupported_or_noncanonical_values(identity):
    with pytest.raises(ValidationError):
        TypeAdapter(CanonicalEvidenceIdentity).validate_python(identity)


def test_setup_import_and_event_creation_accept_and_preserve_uuid5():
    parsed = ImportSetupIn.model_validate(
        {
            "event": {"evidence_id": UUID5_EVENT, "name": "Converted event"},
            "publish_secret": "p" * 48,
            "idempotency_key": UUID4,
            "users": [
                {
                    "username": "converted.person",
                    "display_name": "Converted Person",
                    "person_id": 7,
                    "evidence_subject_id": UUID5_SUBJECT,
                }
            ],
        }
    )
    created = EventCreateIn.model_validate(
        {
            "name": "Converted event",
            "evidence_id": UUID5_EVENT,
            "publish_secret": "s" * 48,
            "idempotency_key": UUID4,
        }
    )

    assert parsed.event.evidence_id == UUID5_EVENT
    assert parsed.users[0].evidence_subject_id == UUID5_SUBJECT
    assert created.evidence_id == UUID5_EVENT


def test_published_person_accepts_and_preserves_uuid5():
    person = PersonIn.model_validate(
        {
            "id": 7,
            "first_name": "Converted",
            "last_name": "Person",
            "evidence_subject_id": UUID5_SUBJECT,
        }
    )
    assert person.evidence_subject_id == UUID5_SUBJECT


def test_setup_import_endpoint_persists_uuid5_evidence_identities(
    db,
    reauth_admin_client,
):
    response = reauth_admin_client.post(
        "/api/v1/admin/import-setup",
        json={
            "event": {"evidence_id": UUID5_EVENT, "name": "Converted event"},
            "publish_secret": "p" * 48,
            "idempotency_key": UUID4,
            "users": [
                {
                    "username": "converted.import",
                    "display_name": "Converted Import",
                    "person_id": 7,
                    "evidence_subject_id": UUID5_SUBJECT,
                }
            ],
        },
    )

    assert response.status_code == 200
    event = db.query(Event).filter(Event.evidence_id == UUID5_EVENT).one()
    user = db.query(User).filter(User.username == "converted.import").one()
    assert response.json()["event"]["evidence_id"] == UUID5_EVENT
    assert event.evidence_id == UUID5_EVENT
    assert user.evidence_subject_id == UUID5_SUBJECT


def test_uuid5_identities_flow_unchanged_into_deletion_evidence(db, monkeypatch):
    event = Event(
        evidence_id=UUID5_EVENT,
        name="Converted event",
        status="draft",
        publish_secret_hash="a" * 64,
    )
    user = User(
        evidence_subject_id=UUID5_SUBJECT,
        username="converted.person",
        display_name="Converted Person",
        event_id=None,
        is_active=True,
    )
    db.add_all([event, user])
    db.flush()
    user.event_id = event.id
    recorded = {}

    def append_stub(_db, **kwargs):
        recorded.update(kwargs["payload"])
        return "b" * 64

    monkeypatch.setattr(deletion_cases, "append_record", append_stub)
    case = deletion_cases.create_event_erasure_case(
        db,
        event,
        initiation_reason="manual_root",
    )

    assert event.evidence_id == UUID5_EVENT
    assert user.evidence_subject_id == UUID5_SUBJECT
    assert case.event_evidence_id == UUID5_EVENT
    assert case.subject_evidence_id == UUID5_EVENT
    assert recorded["event_ref"] == UUID5_EVENT
