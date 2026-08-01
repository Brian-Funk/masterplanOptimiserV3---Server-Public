"""Phase 2 fail-closed publish, participant and policy identity contracts."""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.api.v1.publish import PublishPayload
from app.models.published import (
    PublishedPerson,
    PublishedPersonUnavailability,
    PublishedTask,
)
from server_backend.conftest import _make_client, create_test_event, create_test_user
from server_backend.test_governance import PROFILE, _root_with_reauth


PUBLICATION_CONFIRMATIONS = {
    "authorised_to_configure": True,
    "reviewed_generated_documents": True,
    "confirmed_permitted_data_policy": True,
    "understands_no_legal_certification": True,
}


def _task(**updates):
    task = {
        "id": 1,
        "name": "Operational task",
        "start": "2026-08-01T09:00:00+00:00",
        "end": "2026-08-01T10:00:00+00:00",
        "attendees": [],
    }
    task.update(updates)
    return task


def test_publish_contract_rejects_missing_unknown_and_never_publish_fields():
    with pytest.raises(ValidationError):
        PublishPayload.model_validate({"tasks": [], "persons": []})

    with pytest.raises(ValidationError, match="unclassified"):
        PublishPayload.model_validate({
            "contract_version": "2026-07-30",
            "tasks": [_task(field_values={"legacy": "value"})],
            "persons": [],
        })

    with pytest.raises(ValidationError, match="never_publish"):
        PublishPayload.model_validate({
            "contract_version": "2026-07-30",
            "tasks": [_task(
                field_values={"notes": "private"},
                field_definitions=[{
                    "id": "notes",
                    "name": "Notes",
                    "type": "text",
                    "purpose": "operational_instruction",
                    "visibility": "never_publish",
                }],
            )],
            "persons": [],
        })


def test_publish_contract_accepts_reviewed_bounded_field():
    payload = PublishPayload.model_validate({
        "contract_version": "2026-07-30",
        "tasks": [_task(
            field_values={"brief": "Bring the room key"},
            field_definitions=[{
                "id": "brief",
                "name": "Operational brief",
                "type": "text",
                "purpose": "operational_instruction",
                "visibility": "organiser",
            }],
        )],
        "persons": [],
    })
    assert payload.contract_version == "2026-07-30"


def test_publish_contract_rejects_person_assignments_in_generic_values():
    with pytest.raises(ValidationError, match="structured assignment contract"):
        PublishPayload.model_validate({
            "contract_version": "2026-07-30",
            "tasks": [_task(
                field_values={"crew": [{"name": "Ada", "person_id": 1}]},
                field_definitions=[{
                    "id": "crew",
                    "name": "Assigned crew",
                    "type": "persons_list",
                    "purpose": "assignment",
                    "visibility": "participant",
                }],
            )],
            "persons": [],
        })


def test_participant_and_offline_contracts_expose_only_linked_identity(db):
    event, _secret = create_test_event(db, name="Scoped participant")
    db.add_all([
        PublishedPerson(event_id=event.id, external_person_id=1, first_name="Linked", last_name="Person"),
        PublishedPerson(event_id=event.id, external_person_id=2, first_name="Other", last_name="Person"),
        PublishedPersonUnavailability(
            event_id=event.id, external_person_id=1, working_date="2026-08-01",
            start_datetime="2026-08-01T08:00:00", end_datetime="2026-08-01T09:00:00",
        ),
        PublishedPersonUnavailability(
            event_id=event.id, external_person_id=2, working_date="2026-08-01",
            start_datetime="2026-08-01T10:00:00", end_datetime="2026-08-01T11:00:00",
        ),
        PublishedTask(
            event_id=event.id, external_task_id=1, name="Shared task",
            start_datetime=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
            end_datetime=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
            attendees_json='[{"name":"Linked Person","person_id":1},{"name":"Other Person","person_id":2}]',
            field_assignments_json='{"crew":[{"name":"Linked Person","person_id":1},{"name":"Other Person","person_id":2}]}',
            field_values_json=json.dumps({
                "participant_instruction": "Meet at the participant desk",
                "organiser_note": "Internal room key code",
            }),
            field_definitions_json=json.dumps([
                {
                    "id": "crew", "name": "Assigned crew", "type": "persons_list",
                    "purpose": "assignment", "visibility": "participant",
                },
                {
                    "id": "participant_instruction", "name": "Participant-visible instruction",
                    "type": "text", "purpose": "operational_instruction",
                    "visibility": "participant",
                },
                {
                    "id": "organiser_note", "name": "Internal organiser operational note",
                    "type": "text", "purpose": "operational_instruction",
                    "visibility": "organiser",
                },
            ]),
        ),
    ])
    db.commit()
    participant = create_test_user(db, username="linked.person", event_id=event.id)
    participant.linked_person_id = 1
    db.commit()
    client = _make_client(db, participant)

    for suffix in ("", "/offline"):
        response = client.get(f"/api/v1/calendar/{event.id}{suffix}")
        assert response.status_code == 200
        body = response.json()
        assert [person["external_person_id"] for person in body["persons"]] == [1]
        assert [row["person_id"] for row in body["unavailabilities"]] == [1]
        assert [row["person_id"] for row in body["tasks"][0]["attendees"]] == [1]
        assert [row["person_id"] for row in body["tasks"][0]["field_assignments"]["crew"]] == [1]
        assert body["tasks"][0]["field_values"] == {
            "participant_instruction": "Meet at the participant desk",
        }
        assert {
            definition["id"] for definition in body["tasks"][0]["field_definitions"]
        } == {"crew", "participant_instruction"}

    editor = create_test_user(
        db, username="scoped.editor", event_id=event.id, can_edit=True
    )
    organiser_body = _make_client(db, editor).get(f"/api/v1/calendar/{event.id}").json()
    assert organiser_body["tasks"][0]["field_values"] == {
        "participant_instruction": "Meet at the participant desk",
        "organiser_note": "Internal room key code",
    }


def test_acknowledgement_is_bound_to_exact_current_policy_digest(db):
    root_client, _root = _root_with_reauth(db)
    root_client.put("/api/v1/admin/governance", json=PROFILE)
    publication = root_client.post(
        "/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATIONS
    ).json()
    event, _secret = create_test_event(db, name="Digest-bound acknowledgement")
    editor = create_test_user(db, username="digest.editor", event_id=event.id, can_edit=True)
    client = _make_client(db, editor)

    wrong = client.post("/api/v1/user/data-policy/acknowledge", json={
        "event_id": event.id,
        "scope": "authorised_editor",
        "policy_version": publication["version"],
        "policy_sha256": "0" * 64,
    })
    assert wrong.status_code == 409

    accepted = client.post("/api/v1/user/data-policy/acknowledge", json={
        "event_id": event.id,
        "scope": "authorised_editor",
        "policy_version": publication["version"],
        "policy_sha256": publication["content_sha256"],
    })
    assert accepted.status_code == 200
    assert accepted.json()["policy_sha256"] == publication["content_sha256"]


def test_announcement_requires_current_policy_acknowledgement(db):
    root_client, _root = _root_with_reauth(db)
    assert root_client.put("/api/v1/admin/governance", json=PROFILE).status_code == 200
    publication = root_client.post(
        "/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATIONS
    ).json()
    event, _secret = create_test_event(db, name="Announcement policy boundary")
    administrator = create_test_user(
        db,
        username="announcement.policy.admin",
        event_id=event.id,
        is_admin=True,
    )
    client = _make_client(db, administrator)
    payload = {
        "event_id": event.id,
        "title": "Participant-visible operational update",
        "body": "Meet at the participant desk.",
        "push": False,
    }

    missing = client.post("/api/v1/notifications/announcements", json=payload)
    assert missing.status_code == 428
    assert missing.json()["detail"]["code"] == "data_policy_acknowledgement_required"

    accepted = client.post("/api/v1/user/data-policy/acknowledge", json={
        "event_id": event.id,
        "scope": "field_visibility_administrator",
        "policy_version": publication["version"],
        "policy_sha256": publication["content_sha256"],
    })
    assert accepted.status_code == 200

    created = client.post("/api/v1/notifications/announcements", json=payload)
    assert created.status_code == 201
    assert created.json()["title"] == payload["title"]
