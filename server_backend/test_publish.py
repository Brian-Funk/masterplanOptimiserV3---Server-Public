"""Tests for the desktop-to-server publish endpoint."""
import uuid

from fastapi.testclient import TestClient

from server_backend.conftest import _raw_client, create_test_event, create_test_user
from app.models.published import (
    PublishedGeneralScheduleCategory,
    PublishedGeneralScheduleItem,
    PublishedPerson,
    PublishedPersonUnavailability,
    PublishedTask,
    TaskEdit,
)


def _publish_client(bearer_token: str) -> TestClient:
    """Create a client with Bearer token auth and no session cookies."""
    client = _raw_client(
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        },
    )
    original_post = client.post

    def contract_post(url, *args, **kwargs):
        if url == "/api/v1/publish/publish" and isinstance(kwargs.get("json"), dict):
            kwargs["json"] = {"contract_version": "2026-07-30", **kwargs["json"]}
        return original_post(url, *args, **kwargs)

    client.post = contract_post
    return client


def _subject_id(person_id: int) -> str:
    """Return a stable version-4 evidence UUID for one test person."""

    return str(uuid.UUID(int=person_id, version=4))


_MINIMAL_PAYLOAD = {
    "contract_version": "2026-07-30",
    "tasks": [
        {
            "id": 1,
            "name": "Opening Ceremony",
            "start": "2026-08-01T09:00:00+00:00",
            "end": "2026-08-01T10:00:00+00:00",
            "attendees": [{"name": "John Doe", "person_id": 1}],
        },
    ],
    "persons": [
        {
            "id": 1,
            "first_name": "John",
            "last_name": "Doe",
            "evidence_subject_id": _subject_id(1),
        },
    ],
}


def _task_payload(task_id: int, name: str, day: str) -> dict:
    return {
        "id": task_id,
        "name": name,
        "start": f"{day}T09:00:00+00:00",
        "end": f"{day}T10:00:00+00:00",
        "attendees": [],
    }


def _publish_days(client: TestClient, days: list[tuple[int, str, str]], **extra):
    payload = {
        "contract_version": "2026-07-30",
        "tasks": [_task_payload(task_id, name, day) for task_id, name, day in days],
        "persons": [
            {
                "id": 1,
                "first_name": "Anna",
                "last_name": "Muller",
                "evidence_subject_id": _subject_id(1),
            },
            {
                "id": 2,
                "first_name": "Ben",
                "last_name": "Rossi",
                "evidence_subject_id": _subject_id(2),
            },
        ],
        **extra,
    }
    return client.post("/api/v1/publish/publish", json=payload)


def test_publish_valid_token(db):
    """Publish with valid Bearer token returns 200."""
    event, secret = create_test_event(db, name="Pub Evt")
    client = _publish_client(secret)

    r = client.post("/api/v1/publish/publish", json=_MINIMAL_PAYLOAD)
    assert r.status_code == 200
    data = r.json()
    assert data["tasks_created"] == 1
    assert data["persons_created"] == 1


def test_publish_invalid_token(db):
    """Publish with invalid Bearer token returns 401."""
    create_test_event(db, name="Pub Evt")
    client = _publish_client("invalid-secret-token")

    r = client.post("/api/v1/publish/publish", json=_MINIMAL_PAYLOAD)
    assert r.status_code == 401


def test_publish_no_token(db):
    """Publish without Bearer token returns 401."""
    client = _raw_client(headers={"Content-Type": "application/json"})
    r = client.post("/api/v1/publish/publish", json=_MINIMAL_PAYLOAD)
    assert r.status_code == 401


def test_publish_creates_data(db):
    """Published data is stored and retrievable."""
    event, secret = create_test_event(db, name="Data Evt")
    client = _publish_client(secret)
    client.post("/api/v1/publish/publish", json=_MINIMAL_PAYLOAD)

    from app.models.published import PublishedPerson, PublishedTask

    tasks = db.query(PublishedTask).filter(
        PublishedTask.event_id == event.id,
    ).all()
    persons = db.query(PublishedPerson).filter(
        PublishedPerson.event_id == event.id,
    ).all()
    assert len(tasks) == 1
    assert tasks[0].name == "Opening Ceremony"
    assert len(persons) == 1
    assert persons[0].first_name == "John"


def test_publish_adds_range_and_unavailability_without_touching_registered_users(db):
    """Publishing adds schedule data while preserving existing server accounts."""
    event, secret = create_test_event(db, name="Overnight Evt")
    existing_user = create_test_user(
        db,
        username="existing.viewer",
        display_name="Existing Viewer",
        event_id=event.id,
    )
    client = _publish_client(secret)
    payload = {
        "event": {
            "name": event.name,
            "schedule_day_range": {"start_hour": 6, "end_hour": 30},
        },
        "tasks": [
            {
                "id": 7,
                "name": "Night Duty",
                "start": "2026-08-02T01:00:00",
                "end": "2026-08-02T02:00:00",
                "attendees": [],
            },
        ],
        "persons": [
            {
                "id": 1,
                "first_name": "Jane",
                "last_name": "Doe",
                "evidence_subject_id": _subject_id(1),
            },
        ],
        "unavailabilities": [
            {
                "person_id": 1,
                "working_date": "2026-08-01",
                "start": "2026-08-02T00:30:00",
                "end": "2026-08-02T01:30:00",
            },
        ],
    }

    response = client.post("/api/v1/publish/publish", json=payload)

    assert response.status_code == 200
    db.refresh(existing_user)
    assert existing_user.username == "existing.viewer"
    db.refresh(event)
    assert '"schedule_day_range": {"start_hour": 6, "end_hour": 30}' in event.metadata_json
    intervals = db.query(PublishedPersonUnavailability).filter_by(event_id=event.id).all()
    assert len(intervals) == 1
    assert intervals[0].working_date == "2026-08-01"


def test_publish_replaces_existing(db):
    """Re-publish wipes old data and inserts new data."""
    event, secret = create_test_event(db, name="Replace Evt")
    client = _publish_client(secret)

    client.post("/api/v1/publish/publish", json=_MINIMAL_PAYLOAD)

    new_payload = {
        "tasks": [
            {
                "id": 2,
                "name": "Closing Ceremony",
                "start": "2026-08-10T18:00:00+00:00",
                "end": "2026-08-10T19:00:00+00:00",
                "attendees": [],
            },
        ],
        "persons": [],
    }
    r = client.post("/api/v1/publish/publish", json=new_payload)
    assert r.status_code == 200
    assert r.json()["tasks_created"] == 1

    from app.models.published import PublishedTask

    tasks = db.query(PublishedTask).filter(
        PublishedTask.event_id == event.id,
    ).all()
    assert len(tasks) == 1
    assert tasks[0].name == "Closing Ceremony"


def test_publish_full_scope_replaces_existing(db):
    """Explicit full-scope publish keeps legacy full replacement behaviour."""
    event, secret = create_test_event(db, name="Full Scope Evt")
    client = _publish_client(secret)
    _publish_days(
        client,
        [
            (1, "Arrival Task", "2026-08-01"),
            (2, "Session Task", "2026-08-02"),
        ],
    )

    r = _publish_days(
        client,
        [(3, "Replacement Task", "2026-08-03")],
        publish_scope="full",
    )

    assert r.status_code == 200
    tasks = db.query(PublishedTask).filter(PublishedTask.event_id == event.id).all()
    assert [task.name for task in tasks] == ["Replacement Task"]


def test_publish_date_scope_replaces_only_requested_day(db):
    """Date-scoped publish overwrites the requested day and preserves other days."""
    event, secret = create_test_event(db, name="Partial Scope Evt")
    client = _publish_client(secret)
    _publish_days(
        client,
        [
            (1, "Arrival Task", "2026-08-01"),
            (2, "Session Task", "2026-08-02"),
            (3, "Departure Task", "2026-08-03"),
        ],
    )

    r = _publish_days(
        client,
        [(20, "Updated Session Task", "2026-08-02")],
        publish_scope="dates",
        dates=["2026-08-02"],
    )

    assert r.status_code == 200
    tasks = (
        db.query(PublishedTask)
        .filter(PublishedTask.event_id == event.id)
        .order_by(PublishedTask.start_datetime)
        .all()
    )
    assert [task.name for task in tasks] == [
        "Arrival Task",
        "Updated Session Task",
        "Departure Task",
    ]
    assert [task.external_task_id for task in tasks] == [1, 20, 3]


def test_publish_date_scope_adds_new_day_without_deleting_existing_days(db):
    """Date-scoped publish can add a new day to the existing live schedule."""
    event, secret = create_test_event(db, name="Add Day Evt")
    client = _publish_client(secret)
    _publish_days(client, [(1, "Arrival Task", "2026-08-01")])

    r = _publish_days(
        client,
        [(2, "Session Task", "2026-08-02")],
        publish_scope="dates",
        dates=["2026-08-02"],
    )

    assert r.status_code == 200
    tasks = (
        db.query(PublishedTask)
        .filter(PublishedTask.event_id == event.id)
        .order_by(PublishedTask.start_datetime)
        .all()
    )
    assert [task.name for task in tasks] == ["Arrival Task", "Session Task"]


def test_publish_date_scope_clears_only_replaced_day_web_edits(db):
    """Web edits on untouched days survive date-scoped publish."""
    event, secret = create_test_event(db, name="Edit Scope Evt")
    client = _publish_client(secret)
    _publish_days(
        client,
        [
            (1, "Arrival Task", "2026-08-01"),
            (2, "Session Task", "2026-08-02"),
        ],
    )
    tasks = {
        task.external_task_id: task
        for task in db.query(PublishedTask).filter(PublishedTask.event_id == event.id)
    }
    db.add(TaskEdit(task_id=tasks[1].id, name="Edited Arrival"))
    db.add(TaskEdit(task_id=tasks[2].id, name="Edited Session"))
    db.commit()

    r = _publish_days(
        client,
        [(20, "Updated Session Task", "2026-08-02")],
        publish_scope="dates",
        dates=["2026-08-02"],
    )

    assert r.status_code == 200
    remaining_edits = db.query(TaskEdit).all()
    assert len(remaining_edits) == 1
    remaining_task = db.query(PublishedTask).filter(
        PublishedTask.id == remaining_edits[0].task_id,
    ).one()
    assert remaining_task.external_task_id == 1


def test_publish_date_scope_upserts_people_without_deleting_existing_people(db):
    """Partial publish updates incoming people and keeps older event people."""
    event, secret = create_test_event(db, name="People Scope Evt")
    client = _publish_client(secret)
    _publish_days(client, [(1, "Arrival Task", "2026-08-01")])

    payload = {
        "publish_scope": "dates",
        "dates": ["2026-08-02"],
        "tasks": [_task_payload(2, "Session Task", "2026-08-02")],
        "persons": [
            {
                "id": 2,
                "first_name": "Benjamin",
                "last_name": "Rossi",
                "evidence_subject_id": _subject_id(2),
            },
            {
                "id": 3,
                "first_name": "Clara",
                "last_name": "Smith",
                "evidence_subject_id": _subject_id(3),
            },
        ],
    }
    r = client.post("/api/v1/publish/publish", json=payload)

    assert r.status_code == 200
    people = (
        db.query(PublishedPerson)
        .filter(PublishedPerson.event_id == event.id)
        .order_by(PublishedPerson.external_person_id)
        .all()
    )
    assert [(p.external_person_id, p.first_name) for p in people] == [
        (1, "Anna"),
        (2, "Benjamin"),
        (3, "Clara"),
    ]


def test_publish_date_scope_rejects_empty_or_mismatched_dates(db):
    """Scoped publish requires explicit matching dates to avoid accidental deletes."""
    _event, secret = create_test_event(db, name="Invalid Scope Evt")
    client = _publish_client(secret)

    empty = client.post(
        "/api/v1/publish/publish",
        json={"publish_scope": "dates", "dates": [], "tasks": [], "persons": []},
    )
    assert empty.status_code == 400

    mismatched = client.post(
        "/api/v1/publish/publish",
        json={
            "publish_scope": "dates",
            "dates": ["2026-08-02"],
            "tasks": [_task_payload(1, "Wrong Day", "2026-08-01")],
            "persons": [],
        },
    )
    assert mismatched.status_code == 400


def test_publish_ping_advertises_scoped_publish_support(db):
    """Desktop clients can refuse selected-day publishing against old servers."""
    _event, secret = create_test_event(db, name="Ping Scope Evt")
    client = _publish_client(secret)

    r = client.get("/api/v1/publish/ping")

    assert r.status_code == 200
    assert r.json()["supports_scoped_publish"] is True


def test_publish_updates_event_metadata(db):
    """Publish can update event name and dates."""
    event, secret = create_test_event(db, name="Old Name")
    client = _publish_client(secret)

    payload = {
        "event": {
            "name": "New Name",
            "start_date": "2026-08-01",
            "end_date": "2026-08-10",
        },
        "tasks": [],
        "persons": [],
    }
    r = client.post("/api/v1/publish/publish", json=payload)
    assert r.status_code == 200

    db.refresh(event)
    assert event.name == "New Name"
    assert event.status == "published"


def test_publish_rejects_retired_logo_theme_payload(db):
    """The clean-slate contract has no compatibility path for retired theme data."""
    _event, secret = create_test_event(db, name="Theme Evt")
    client = _publish_client(secret)

    payload = {
        **_MINIMAL_PAYLOAD,
        "theme": {
            "logo_color_1": "#ff0000",
            "logo_color_2": "#00ff00",
        },
    }

    r = client.post("/api/v1/publish/publish", json=payload)
    assert r.status_code == 422


def test_general_schedule_publish_uses_explicit_schedule_views(db):
    """Explicit Schedule Views publish one public item row per selected view."""
    event, secret = create_test_event(db, name="General Schedule Evt")
    client = _publish_client(secret)

    response = client.post(
        "/api/v1/publish/general-schedule",
        json={
            "event": {"name": "General Schedule Evt"},
            "fingerprint": "abc123",
            "schedule_views": [
                {"id": 10, "name": "Delegates", "sort_order": 0},
                {"id": 11, "name": "Officials", "sort_order": 1},
            ],
            "items": [
                {
                    "id": 100,
                    "title": "Opening Briefing",
                    "date": "2026-08-01",
                    "start_time": "09:00",
                    "end_time": "10:00",
                    "location_name": "Room A",
                    "location_address": "1 Parliament Square",
                    "responsible": "Session president",
                    "schedule_view_ids": [10, 11],
                    "schedule_view_names": ["Delegates", "Officials"],
                },
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["items_published"] == 1
    categories = (
        db.query(PublishedGeneralScheduleCategory)
        .filter(PublishedGeneralScheduleCategory.event_id == event.id)
        .order_by(PublishedGeneralScheduleCategory.sort_order)
        .all()
    )
    items = (
        db.query(PublishedGeneralScheduleItem)
        .filter(PublishedGeneralScheduleItem.event_id == event.id)
        .order_by(PublishedGeneralScheduleItem.category_id)
        .all()
    )
    assert [category.name for category in categories] == ["Delegates", "Officials"]
    assert [item.category_id for item in items] == [10, 11]
    assert [item.category_name for item in items] == ["Delegates", "Officials"]
    assert all(item.location_address == "1 Parliament Square" for item in items)
    assert all(item.responsible == "Session president" for item in items)


def test_general_schedule_publish_does_not_create_fallback_for_explicit_no_view_items(db):
    """Explicit Schedule View payloads do not publish no-view items."""
    event, secret = create_test_event(db, name="No Fallback Evt")
    client = _publish_client(secret)

    response = client.post(
        "/api/v1/publish/general-schedule",
        json={
            "fingerprint": "no-view",
            "schedule_views": [],
            "items": [
                {
                    "id": 101,
                    "title": "Unassigned",
                    "date": "2026-08-01",
                    "start_time": "09:00",
                    "end_time": "10:00",
                    "schedule_view_ids": [],
                    "schedule_view_names": [],
                },
            ],
        },
    )

    assert response.status_code == 200
    assert db.query(PublishedGeneralScheduleCategory).filter(
        PublishedGeneralScheduleCategory.event_id == event.id,
    ).count() == 0
    assert db.query(PublishedGeneralScheduleItem).filter(
        PublishedGeneralScheduleItem.event_id == event.id,
    ).count() == 0


def test_general_schedule_date_scope_replaces_only_requested_working_day(db):
    """A selected-day Public Schedule push preserves every other published day."""
    event, secret = create_test_event(db, name="Scoped General Schedule")
    client = _publish_client(secret)
    base_payload = {
        "fingerprint": "full-v1",
        "schedule_views": [{"id": 10, "name": "Public", "sort_order": 0}],
        "items": [
            {
                "id": 100,
                "title": "Day One",
                "date": "2026-08-01",
                "start_time": "09:00",
                "end_time": "10:00",
                "schedule_view_ids": [10],
                "schedule_view_names": ["Public"],
            },
            {
                "id": 200,
                "title": "Day Two",
                "date": "2026-08-02",
                "start_time": "09:00",
                "end_time": "10:00",
                "schedule_view_ids": [10],
                "schedule_view_names": ["Public"],
            },
        ],
    }
    assert client.post(
        "/api/v1/publish/general-schedule",
        json=base_payload,
    ).status_code == 200

    scoped = client.post(
        "/api/v1/publish/general-schedule",
        json={
            **base_payload,
            "fingerprint": "day-one-v2",
            "publish_scope": "dates",
            "dates": ["2026-08-01"],
            "items": [
                {
                    **base_payload["items"][0],
                    "id": 101,
                    "title": "Day One Updated",
                },
            ],
        },
    )

    assert scoped.status_code == 200
    rows = (
        db.query(PublishedGeneralScheduleItem)
        .filter(PublishedGeneralScheduleItem.event_id == event.id)
        .order_by(PublishedGeneralScheduleItem.date)
        .all()
    )
    assert [row.title for row in rows] == ["Day One Updated", "Day Two"]


def test_general_schedule_date_scope_validates_working_day_membership(db):
    """Items outside a requested working day cannot leak into a scoped push."""
    _event, secret = create_test_event(db, name="Scoped Validation")
    client = _publish_client(secret)

    response = client.post(
        "/api/v1/publish/general-schedule",
        json={
            "fingerprint": "invalid-scope",
            "publish_scope": "dates",
            "dates": ["2026-08-01"],
            "schedule_views": [{"id": 10, "name": "Public"}],
            "items": [
                {
                    "id": 200,
                    "title": "Wrong Day",
                    "date": "2026-08-02",
                    "start_time": "09:00",
                    "end_time": "10:00",
                    "schedule_view_ids": [10],
                    "schedule_view_names": ["Public"],
                }
            ],
        },
    )

    assert response.status_code == 400
    assert "outside the requested publish dates" in response.json()["detail"]


def test_general_schedule_date_scope_includes_after_midnight_working_day(db):
    """A post-midnight item is replaced with its configured preceding working day."""
    event, secret = create_test_event(db, name="Overnight General Schedule")
    client = _publish_client(secret)

    response = client.post(
        "/api/v1/publish/general-schedule",
        json={
            "fingerprint": "overnight",
            "publish_scope": "dates",
            "dates": ["2026-08-01"],
            "event": {
                "schedule_day_range": {"start_hour": 6, "end_hour": 30},
            },
            "schedule_views": [{"id": 10, "name": "Public"}],
            "items": [
                {
                    "id": 300,
                    "title": "Night Session",
                    "date": "2026-08-02",
                    "start_time": "01:00",
                    "end_time": "02:00",
                    "schedule_view_ids": [10],
                    "schedule_view_names": ["Public"],
                }
            ],
        },
    )

    assert response.status_code == 200
    row = db.query(PublishedGeneralScheduleItem).filter(
        PublishedGeneralScheduleItem.event_id == event.id,
    ).one()
    assert row.date == "2026-08-02"
    assert row.title == "Night Session"
