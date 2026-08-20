"""Tests for calendar endpoints."""
import json
import hashlib
from datetime import datetime, timezone

from server_backend.conftest import (
    _make_client,
    _raw_client,
    create_test_event,
    create_test_user,
)
from app.models.published import (
    PublishedGeneralScheduleCategory,
    PublishedGeneralScheduleItem,
    PublishedTask,
    PublishedPerson,
    PublishedPersonUnavailability,
    TaskEdit,
)
from app.models.governance import GovernancePublication
from app.core.governance_rendering import POLICY_TEMPLATE_VERSION
from app.core.tenancy import assign_event_membership


def _publish_current_offline_policy(db) -> None:
    content = json.dumps(
        {
            "template_version": POLICY_TEMPLATE_VERSION,
            "optional_features": {"offline_calendar": True},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    db.add(GovernancePublication(
        version=1,
        content_json=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        source_json="{}",
        source_sha256="0" * 64,
        material_change=True,
        change_summary_json="[]",
    ))
    db.commit()


def _seed_published_data(db, event_id: int):
    """Insert published tasks and persons for testing."""
    person = PublishedPerson(
        event_id=event_id,
        external_person_id=1,
        first_name="Jane",
        last_name="Doe",
        email="jane@test.com",
    )
    db.add(person)

    task = PublishedTask(
        event_id=event_id,
        external_task_id=1,
        name="Workshop A",
        start_datetime=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
        attendees_json='[{"name": "Jane Doe", "person_id": 1}]',
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task, person


def test_get_calendar(db):
    """Authenticated user can get calendar data for their event."""
    event, _ = create_test_event(db, name="Cal Evt")
    _seed_published_data(db, event.id)

    user = create_test_user(
        db, username="cal_user", event_id=event.id, can_edit=True,
    )
    client = _make_client(db, user)

    r = client.get(f"/api/v1/calendar/{event.id}")
    assert r.status_code == 200
    data = r.json()
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["name"] == "Workshop A"


def test_participant_calendar_excludes_arbitrary_internal_task_dictionaries(db):
    event, _ = create_test_event(db, name="Minimised calendar")
    task, _person = _seed_published_data(db, event.id)
    task.field_values_json = json.dumps({"internal_notes": "not participant visible"})
    task.field_definitions_json = json.dumps([{"id": "internal_notes", "name": "Internal", "type": "text"}])
    task.additional_json = json.dumps({"source_record": "private"})
    db.commit()
    participant = create_test_user(db, username="minimal.participant", event_id=event.id)

    response = _make_client(db, participant).get(f"/api/v1/calendar/{event.id}")

    assert response.status_code == 200, response.text
    returned = response.json()["tasks"][0]
    assert returned["field_values"] is None
    assert returned["field_definitions"] is None
    assert returned["additional"] is None


def test_offline_calendar_keeps_old_scope_until_revised_policy_is_published(db):
    """The immutable old opt-in is never broadened by a software upgrade."""
    event, _ = create_test_event(db, name="Offline contract")
    task, _person = _seed_published_data(db, event.id)
    task.field_values_json = json.dumps({"internal_notes": "not for the device"})
    task.field_definitions_json = json.dumps([
        {
            "id": "internal_notes",
            "name": "Internal",
            "type": "text",
            "purpose": "operational_instruction",
            "visibility": "organiser",
        },
    ])
    task.additional_json = json.dumps({"source_record": "private"})
    db.commit()
    editor = create_test_user(
        db,
        username="offline.editor",
        event_id=event.id,
        can_edit=True,
    )
    client = _make_client(db, editor)

    live = client.get(f"/api/v1/calendar/{event.id}")
    offline = client.get(f"/api/v1/calendar/{event.id}/offline")

    assert live.status_code == 200
    assert live.json()["tasks"][0]["field_values"] == {
        "internal_notes": "not for the device",
    }
    assert offline.status_code == 200
    data = offline.json()
    assert set(data) == {
        "offline_contract_version", "controller_public_id",
        "controller_trust_entity_id", "event_ref", "membership_id", "linked_person_id",
        "event_id", "event_name", "start_date", "end_date", "day_aliases",
        "schedule_day_range", "tasks", "persons", "public_schedule_categories",
        "public_schedule_views", "public_schedule_items", "unavailabilities",
        "data_policy_version", "data_policy_sha256", "data_policy_acknowledged",
    }
    returned = data["tasks"][0]
    assert returned["field_values"] is None
    assert returned["field_definitions"] is None
    assert returned["additional"] is None
    assert returned["has_web_edit"] is False
    assert returned["web_edit_edited_by"] is None
    assert returned["web_edit_edited_by_user_id"] is None
    assert returned["web_edit_change_summary"] == []


def test_revised_offline_policy_includes_authenticated_allocations_but_not_directory(db):
    event, _ = create_test_event(db, name="Revised offline contract")
    db.add_all([
        PublishedPerson(
            event_id=event.id,
            external_person_id=person_id,
            first_name=first_name,
            last_name="Example",
        )
        for person_id, first_name in ((1, "Anna"), (2, "Ben"))
    ])
    task = PublishedTask(
        event_id=event.id,
        external_task_id=8,
        name="Briefing",
        start_datetime=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
        attendees_json=json.dumps([
            {"name": "Anna Example", "person_id": 1},
            {"name": "Ben Example", "person_id": 2},
        ]),
        field_assignments_json=json.dumps({
            "front": [{"name": "Anna Example", "person_id": 1}],
            "side": [{"name": "Ben Example", "person_id": 2}],
        }),
        field_values_json=json.dumps({"note": "Bring the documents"}),
        field_definitions_json=json.dumps([
            {"id": "front", "name": "Front-Orga", "type": "persons_list", "purpose": "assignment", "visibility": "participant"},
            {"id": "side", "name": "Side-Orga", "type": "persons_list", "purpose": "assignment", "visibility": "organiser"},
            {"id": "note", "name": "Note", "type": "text", "purpose": "operational_instruction", "visibility": "organiser"},
        ]),
    )
    db.add(task)
    db.commit()
    viewer = create_test_user(db, username="revised.offline", event_id=event.id)
    viewer.linked_person_id = 1
    assign_event_membership(db, viewer, event, linked_person_id=1)
    db.commit()
    _publish_current_offline_policy(db)

    response = _make_client(db, viewer).get(f"/api/v1/calendar/{event.id}/offline")

    assert response.status_code == 200, response.text
    data = response.json()
    assert [person["external_person_id"] for person in data["persons"]] == [1]
    returned = data["tasks"][0]
    assert list(returned["field_assignments"]) == ["front", "side"]
    assert returned["field_assignments"]["front"][0]["name"] == "Anna Example"
    assert returned["field_assignments"]["side"][0]["name"] == "Ben Example"
    assert returned["field_values"] == {"note": "Bring the documents"}


def test_all_authenticated_event_roles_receive_the_same_task_allocations(db):
    event, _ = create_test_event(db, name="One viewing class")
    db.add_all([
        PublishedPerson(event_id=event.id, external_person_id=1, first_name="Brian", last_name="Funk"),
        PublishedPerson(event_id=event.id, external_person_id=2, first_name="Anna", last_name="Example"),
    ])
    db.add(PublishedTask(
        event_id=event.id,
        external_task_id=9,
        name="Desk",
        start_datetime=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
        attendees_json=json.dumps([
            {"name": "Brian Funk", "person_id": 1},
            {"name": "Anna Example", "person_id": 2},
        ]),
        field_assignments_json=json.dumps({
            "front": [{"name": "Brian Funk", "person_id": 1}],
            "side": [{"name": "Anna Example", "person_id": 2}],
        }),
        field_values_json=json.dumps({"participant_note": "A", "legacy_note": "B"}),
        field_definitions_json=json.dumps([
            {"id": "front", "name": "Front-Orga", "type": "persons_list", "purpose": "assignment", "visibility": "participant"},
            {"id": "side", "name": "Side-Orga", "type": "persons_list", "purpose": "assignment", "visibility": "organiser"},
            {"id": "participant_note", "name": "Field_A", "type": "text", "purpose": "operational_instruction", "visibility": "participant"},
            {"id": "legacy_note", "name": "Field_B", "type": "text", "purpose": "operational_instruction", "visibility": "organiser"},
        ]),
    ))
    db.commit()
    users = [
        create_test_user(db, username="viewer", event_id=event.id),
        create_test_user(db, username="editor", event_id=event.id, can_edit=True),
        create_test_user(db, username="issuer", event_id=event.id, is_issuer=True),
        create_test_user(db, username="administrator", event_id=event.id, is_admin=True),
        create_test_user(db, username="root", is_root_admin=True, is_admin=True),
        create_test_user(db, username="unlinked", event_id=event.id),
    ]
    users[0].linked_person_id = 1
    db.commit()

    task_views = []
    for user in users:
        response = _make_client(db, user).get(f"/api/v1/calendar/{event.id}")
        assert response.status_code == 200
        returned = response.json()["tasks"][0]
        task_views.append({
            "attendees": returned["attendees"],
            "field_assignments": returned["field_assignments"],
            "field_values": returned["field_values"],
            "field_definitions": returned["field_definitions"],
        })
    assert all(view == task_views[0] for view in task_views[1:])


def test_offline_calendar_projects_live_audience_teams_to_bounded_shape(db):
    """The richer published team shape never escapes into device storage."""
    event, _ = create_test_event(db, name="Audience projection")
    category = PublishedGeneralScheduleCategory(
        event_id=event.id,
        external_category_id=21,
        name="Programme",
        sort_order=0,
    )
    db.add(category)
    db.flush()
    item = PublishedGeneralScheduleItem(
        event_id=event.id,
        external_session_element_id=301,
        title="Opening",
        date="2026-08-26",
        start_time="09:00",
        end_time="10:00",
        category_id=category.id,
        category_name=category.name,
        audience_teams_json=json.dumps([
            {
                "id": 7,
                "name": "Officials",
                "short_name": "OFF",
                "category_id": 2,
                "category_name": "Delegations",
                "colour": None,
            },
            {
                "id": 8,
                "name": "Chairs",
                "short_name": None,
                "category_id": 2,
                "category_name": "Delegations",
                "colour": "#123456",
            },
        ]),
    )
    db.add(item)
    db.commit()
    participant = create_test_user(
        db,
        username="audience.offline",
        event_id=event.id,
    )

    response = _make_client(db, participant).get(
        f"/api/v1/calendar/{event.id}/offline",
    )

    assert response.status_code == 200
    assert response.json()["public_schedule_items"][0]["audience_teams"] == [
        {"name": "Officials", "short_name": "OFF"},
        {"name": "Chairs", "short_name": None},
    ]


def test_calendar_returns_overnight_working_day_and_private_unavailability(db):
    """Authenticated calendars retain the overnight tail and exact missing-person detail."""
    event, _ = create_test_event(db, name="Night Calendar")
    event.metadata_json = json.dumps(
        {"schedule_day_range": {"start_hour": 6, "end_hour": 30}},
    )
    person = PublishedPerson(
        event_id=event.id,
        external_person_id=11,
        first_name="Night",
        last_name="Worker",
    )
    task = PublishedTask(
        event_id=event.id,
        external_task_id=11,
        name="Late Session",
        start_datetime=datetime(2026, 8, 2, 1, 0),
        end_datetime=datetime(2026, 8, 2, 2, 0),
        attendees_json="[]",
    )
    interval = PublishedPersonUnavailability(
        event_id=event.id,
        external_person_id=11,
        working_date="2026-08-01",
        start_datetime="2026-08-02T00:30:00",
        end_datetime="2026-08-02T01:30:00",
    )
    db.add_all([person, task, interval])
    db.commit()
    user = create_test_user(db, username="night_user", event_id=event.id)
    user.linked_person_id = 11
    db.commit()
    client = _make_client(db, user)

    response = client.get(f"/api/v1/calendar/{event.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["schedule_day_range"] == {"start_hour": 6, "end_hour": 30}
    assert data["tasks"][0]["working_date"] == "2026-08-01"
    assert data["unavailabilities"] == [
        {
            "person_id": 11,
            "working_date": "2026-08-01",
            "start": "2026-08-02T00:30:00",
            "end": "2026-08-02T01:30:00",
        },
    ]


def test_get_calendar_persons(db):
    """Can get published persons for an event."""
    event, _ = create_test_event(db, name="Pers Evt")
    _seed_published_data(db, event.id)

    user = create_test_user(
        db, username="pers_user", event_id=event.id,
    )
    user.linked_person_id = 1
    assign_event_membership(db, user, event, linked_person_id=1)
    db.commit()
    client = _make_client(db, user)

    r = client.get(f"/api/v1/calendar/{event.id}/persons")
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    assert data[0]["first_name"] == "Jane"


def test_get_calendar_returns_public_schedule_views(db):
    """Calendar payload includes published public schedule views."""
    event, _ = create_test_event(db, name="Schedule View Evt")
    db.add(
        PublishedGeneralScheduleCategory(
            event_id=event.id,
            external_category_id=10,
            name="Delegates",
            sort_order=0,
        )
    )
    db.add(
        PublishedGeneralScheduleItem(
            event_id=event.id,
            external_session_element_id=100,
            title="Opening Briefing",
            date="2026-08-01",
            start_time="09:00",
            end_time="10:00",
            category_id=10,
            category_name="Delegates",
        )
    )
    db.commit()
    user = create_test_user(db, username="schedule_user", event_id=event.id)
    client = _make_client(db, user)

    r = client.get(f"/api/v1/calendar/{event.id}")

    assert r.status_code == 200
    data = r.json()
    assert data["public_schedule_views"] == [
        {"id": 10, "name": "Delegates", "sort_order": 0.0},
    ]
    assert data["public_schedule_items"][0]["category_id"] == 10


def test_commit_preserves_structured_assignment_categories(db):
    """Web edits keep assignment fields separate instead of flattening them."""
    event, _ = create_test_event(db, name="Structured Assignments")
    db.add_all([
        PublishedPerson(
            event_id=event.id,
            external_person_id=person_id,
            first_name="Person",
            last_name=letter,
        )
        for person_id, letter in ((1, "A"), (2, "B"), (3, "C"))
    ])
    task = PublishedTask(
        event_id=event.id,
        external_task_id=5,
        name="Meal Transfer",
        start_datetime=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
        attendees_json=json.dumps([
            {"name": "Person A", "person_id": 1},
            {"name": "Person B", "person_id": 2},
        ]),
        field_assignments_json=json.dumps({
            "driver": [{"name": "Person A", "person_id": 1}],
            "cook": [{"name": "Person B", "person_id": 2}],
        }),
        field_definitions_json=json.dumps([
            {"id": "driver", "name": "Driver", "type": "persons_list", "purpose": "assignment", "visibility": "participant"},
            {"id": "cook", "name": "Cook", "type": "persons_list", "purpose": "assignment", "visibility": "participant"},
        ]),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    editor = create_test_user(
        db,
        username="structured.editor",
        event_id=event.id,
        can_edit=True,
    )
    client = _make_client(db, editor)

    added = client.post(
        f"/api/v1/calendar/{event.id}/tasks/commit",
        json={
            "edits": [
                {
                    "task_id": task.id,
                    "field_assignments": {
                        "cook": [
                            {"name": "Person B", "person_id": 2},
                            {"name": "Person C", "person_id": 3},
                        ],
                    },
                },
            ],
            "deletions": [],
            "creations": [],
        },
    )

    assert added.status_code == 200
    edit = db.query(TaskEdit).filter(TaskEdit.task_id == task.id).one()
    assert json.loads(edit.field_assignments_json) == {
        "driver": [{"name": "Person A", "person_id": 1}],
        "cook": [
            {"name": "Person B", "person_id": 2},
            {"name": "Person C", "person_id": 3},
        ],
    }
    assert json.loads(edit.attendees_json) == [
        {"name": "Person A", "person_id": 1},
        {"name": "Person B", "person_id": 2},
        {"name": "Person C", "person_id": 3},
    ]

    removed = client.post(
        f"/api/v1/calendar/{event.id}/tasks/commit",
        json={
            "edits": [
                {
                    "task_id": task.id,
                    "field_assignments": {
                        "cook": [{"name": "Person C", "person_id": 3}],
                    },
                },
            ],
            "deletions": [],
            "creations": [],
        },
    )

    assert removed.status_code == 200
    refreshed = client.get(f"/api/v1/calendar/{event.id}")
    assert refreshed.status_code == 200
    task_data = refreshed.json()["tasks"][0]
    assert task_data["field_assignments"] == {
        "driver": [{"name": "Person A", "person_id": 1}],
        "cook": [{"name": "Person C", "person_id": 3}],
    }
    assert task_data["attendees"] == [
        {"name": "Person A", "person_id": 1},
        {"name": "Person C", "person_id": 3},
    ]


def test_partial_assignment_edit_rejects_person_already_in_untouched_category(db):
    """A partial patch is checked against the complete effective allocation."""

    event, _ = create_test_event(db, name="Unique assignments")
    db.add_all([
        PublishedPerson(event_id=event.id, external_person_id=1, first_name="Person", last_name="A"),
        PublishedPerson(event_id=event.id, external_person_id=2, first_name="Person", last_name="B"),
    ])
    task = PublishedTask(
        event_id=event.id,
        external_task_id=50,
        name="Desk",
        start_datetime=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
        attendees_json=json.dumps([{"name": "Person A", "person_id": 1}]),
        field_assignments_json=json.dumps({
            "front": [{"name": "Person A", "person_id": 1}],
            "side": [],
        }),
        field_definitions_json=json.dumps([
            {"id": "front", "name": "Front-Orga", "type": "persons_list", "purpose": "assignment", "visibility": "participant"},
            {"id": "side", "name": "Side-Orga", "type": "persons_list", "purpose": "assignment", "visibility": "participant"},
        ]),
    )
    db.add(task)
    db.commit()
    editor = create_test_user(db, username="unique.editor", event_id=event.id, can_edit=True)

    response = _make_client(db, editor).post(
        f"/api/v1/calendar/{event.id}/tasks/commit",
        json={
            "edits": [{
                "task_id": task.id,
                "field_assignments": {
                    "side": [{"name": "ignored", "person_id": 1}],
                },
            }],
            "deletions": [],
            "creations": [],
        },
    )

    assert response.status_code == 422
    assert "only once" in response.json()["detail"]
    assert db.query(TaskEdit).filter(TaskEdit.task_id == task.id).first() is None


def test_get_calendar_unauthenticated(db):
    """Calendar endpoint requires authentication."""
    event, _ = create_test_event(db, name="Unauth Evt")
    client = _raw_client()
    r = client.get(f"/api/v1/calendar/{event.id}")
    assert r.status_code == 401


def test_calendar_rejects_cross_event_read(db):
    """Changing the event id cannot expose another event's schedule."""
    own_event, _ = create_test_event(db, name="Own Event")
    other_event, _ = create_test_event(db, name="Other Event")
    _seed_published_data(db, other_event.id)
    user = create_test_user(db, username="event.viewer", event_id=own_event.id)

    response = _make_client(db, user).get(
        f"/api/v1/calendar/{other_event.id}",
    )

    assert response.status_code == 404


def test_calendar_commit_rejects_user_without_edit_permission(db):
    """A viewer cannot submit a crafted task edit request."""
    event, _ = create_test_event(db, name="Read Only")
    task, _ = _seed_published_data(db, event.id)
    viewer = create_test_user(db, username="read.only", event_id=event.id)

    response = _make_client(db, viewer).post(
        f"/api/v1/calendar/{event.id}/tasks/commit",
        json={
            "edits": [{"task_id": task.id, "name": "Changed"}],
            "deletions": [],
            "creations": [],
        },
    )

    assert response.status_code == 403


def test_revert_cannot_delete_edit_from_another_event(db):
    """A task id from another event cannot be used to remove its edit."""
    own_event, _ = create_test_event(db, name="Own Edit Event")
    other_event, _ = create_test_event(db, name="Other Edit Event")
    task, _ = _seed_published_data(db, other_event.id)
    edit = TaskEdit(task_id=task.id, name="Protected edit")
    db.add(edit)
    db.commit()
    editor = create_test_user(
        db,
        username="scoped.editor",
        event_id=own_event.id,
        can_edit=True,
    )

    response = _make_client(db, editor).delete(
        f"/api/v1/calendar/{own_event.id}/tasks/{task.id}/edits",
    )

    assert response.status_code == 404
    assert db.query(TaskEdit).filter(TaskEdit.task_id == task.id).count() == 1
