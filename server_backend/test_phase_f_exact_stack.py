"""Connected Phase F current-format publication and access flow."""

from __future__ import annotations

import uuid

from app.models.deletion import DeletionCase
from server_backend.conftest import _make_client, _raw_client, create_test_event, create_test_user


def _publish_client(secret: str):
    return _raw_client(headers={
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
    })


def test_current_format_publication_participant_organiser_and_deletion_flow(db) -> None:
    event, publish_secret = create_test_event(db, name="Phase F synthetic event")
    subject_id = str(uuid.UUID(int=73, version=4))
    payload = {
        "contract_version": "2026-07-30",
        "event": {
            "name": event.name,
            "start_date": "2026-08-01",
            "end_date": "2026-08-02",
            "schedule_day_range": {"start_hour": 6, "end_hour": 30},
        },
        "tasks": [{
            "id": 31,
            "name": "Synthetic opening session",
            "start": "2026-08-01T09:00:00+00:00",
            "end": "2026-08-01T10:00:00+00:00",
            "attendees": [{"name": "Synthetic Participant", "person_id": 73}],
        }],
        "persons": [{
            "id": 73,
            "first_name": "Synthetic",
            "last_name": "Participant",
            "evidence_subject_id": subject_id,
        }],
        "unavailabilities": [],
        "publish_scope": "full",
    }

    published = _publish_client(publish_secret).post("/api/v1/publish/publish", json=payload)

    assert published.status_code == 200
    assert published.json()["tasks_created"] == 1
    assert published.json()["persons_created"] == 1

    participant = create_test_user(
        db,
        username="phase.f.participant",
        display_name="Synthetic Participant",
        event_id=event.id,
    )
    participant.linked_person_id = 73
    participant.evidence_subject_id = subject_id
    organiser = create_test_user(
        db,
        username="phase.f.organiser",
        display_name="Synthetic Organiser",
        event_id=event.id,
        is_issuer=True,
    )
    db.commit()

    participant_client = _make_client(db, participant, reauth=True)
    participant_calendar = participant_client.get(f"/api/v1/calendar/{event.id}")
    organiser_calendar = _make_client(db, organiser).get(f"/api/v1/calendar/{event.id}")

    assert participant_calendar.status_code == organiser_calendar.status_code == 200
    assert participant_calendar.json()["tasks"][0]["name"] == "Synthetic opening session"
    participant_person = participant_calendar.json()["persons"]
    assert len(participant_person) == 1
    assert participant_person[0]["external_person_id"] == 73
    assert participant_person[0]["first_name"] == "Synthetic"
    assert participant_person[0]["last_name"] == "Participant"
    assert organiser_calendar.json()["tasks"][0]["working_date"] == "2026-08-01"

    deletion = participant_client.post("/api/v1/user/deletion-requests")
    receipt = participant_client.get(
        f"/api/v1/user/deletion-requests/{deletion.json()['request_id']}/receipt"
    )

    assert deletion.status_code == receipt.status_code == 200
    assert deletion.json()["state"] == "submitted"
    assert receipt.json()["request_id"] == deletion.json()["request_id"]
    case = db.query(DeletionCase).filter_by(request_id=deletion.json()["request_id"]).one()
    assert case.subject_evidence_id == subject_id


def test_publish_endpoint_has_no_legacy_runtime_fallback(db) -> None:
    _event, publish_secret = create_test_event(db, name="Phase F current contract only")
    response = _publish_client(publish_secret).post(
        "/api/v1/publish/publish",
        json={"tasks": [], "persons": []},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == [{
        "type": "missing",
        "loc": ["body", "contract_version"],
        "msg": "Field required",
    }]
