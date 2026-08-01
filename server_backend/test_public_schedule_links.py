"""Tests for managed token links that expose selected Public Schedule views."""

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from app.models.audit import AuditLog
from app.models.event import Event
from app.models.public_schedule_link import (
    PublicScheduleLink,
    PublicScheduleLinkView,
)
from app.models.published import (
    PublishedGeneralScheduleCategory,
    PublishedGeneralScheduleItem,
)
from server_backend.conftest import (
    _make_client,
    create_test_event,
    create_test_user,
)


def _future_iso(days: int = 7) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _seed_schedule(db, event_id: int) -> None:
    db.add_all(
        [
            PublishedGeneralScheduleCategory(
                event_id=event_id,
                external_category_id=10,
                name="Delegates",
                sort_order=0,
            ),
            PublishedGeneralScheduleCategory(
                event_id=event_id,
                external_category_id=11,
                name="Officials",
                sort_order=1,
            ),
            PublishedGeneralScheduleItem(
                event_id=event_id,
                external_session_element_id=100,
                title="Opening Briefing",
                date="2026-08-01",
                start_time="09:00",
                end_time="10:00",
                location_name="Room A",
                location_address="1 Parliament Square",
                responsible="Session president",
                audience_teams_json=json.dumps(
                    [
                        {
                            "id": 75,
                            "name": "Delegates",
                            "short_name": "DEL",
                            "colour": "#336699",
                            "category_id": 999,
                            "category_name": "Internal category",
                        }
                    ]
                ),
                description="Bring laptops.",
                category_id=10,
                category_name="Delegates",
                type_id=20,
                type_name="Briefing",
                copy_template_html="<p>Private template</p>",
                category="Internal category",
                colour="#336699",
            ),
            PublishedGeneralScheduleItem(
                event_id=event_id,
                external_session_element_id=101,
                title="Board Update",
                date="2026-08-01",
                start_time="11:00",
                end_time="12:00",
                category_id=11,
                category_name="Officials",
            ),
        ]
    )
    db.commit()


def _create_link(client, event_id: int, **overrides):
    payload = {
        "description": "Shared with the board",
        "expires_at": _future_iso(),
        "view_ids": [10, 11],
        **overrides,
    }
    return client.post(
        f"/api/v1/admin/events/{event_id}/public-schedule-links",
        json=payload,
    )


def _token_from_share_url(value: str) -> str:
    fragment = urlsplit(value).fragment
    return parse_qs(fragment)["token"][0]


def test_root_creates_hashed_one_time_public_schedule_link(db, root_client):
    event = db.query(Event).filter(Event.name == "Root Event").one()
    _seed_schedule(db, event.id)

    response = _create_link(root_client, event.id)

    assert response.status_code == 201
    data = response.json()
    token = _token_from_share_url(data["share_url"])
    row = db.query(PublicScheduleLink).one()
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row.token_hash
    assert data["status"] == "active"
    assert data["description"] == "Shared with the board"
    assert [view["id"] for view in data["views"]] == [10, 11]

    listed = root_client.get(
        f"/api/v1/admin/events/{event.id}/public-schedule-links"
    )
    assert listed.status_code == 200
    assert "share_url" not in listed.json()[0]
    assert "token" not in json.dumps(listed.json()).lower()


def test_public_link_management_is_root_or_own_event_issuer_only(
    db,
    admin_client,
    issuer_client,
    user_client,
):
    admin = db.query(Event).filter(Event.name == "Admin Event").one()
    _seed_schedule(db, admin.id)
    assert _create_link(admin_client, admin.id).status_code == 403

    ordinary_client, _ordinary_user, ordinary_event = user_client
    _seed_schedule(db, ordinary_event.id)
    assert _create_link(ordinary_client, ordinary_event.id).status_code == 403

    issuer_http, _issuer, issuer_event = issuer_client
    _seed_schedule(db, issuer_event.id)
    assert _create_link(issuer_http, issuer_event.id).status_code == 201

    other_event, _ = create_test_event(db, name="Other Event")
    _seed_schedule(db, other_event.id)
    assert _create_link(issuer_http, other_event.id).status_code == 403


def test_dual_role_issuer_remains_scoped_to_own_event(db):
    own_event, _ = create_test_event(db, name="Dual Role Own")
    other_event, _ = create_test_event(db, name="Dual Role Other")
    _seed_schedule(db, own_event.id)
    _seed_schedule(db, other_event.id)
    user = create_test_user(
        db,
        username="dual.role",
        event_id=own_event.id,
        is_admin=True,
        is_issuer=True,
    )
    client = _make_client(db, user)

    assert _create_link(client, own_event.id).status_code == 201
    assert _create_link(client, other_event.id).status_code == 403


def test_link_creation_validates_expiry_description_and_current_views(
    db,
    root_client,
):
    event = db.query(Event).filter(Event.name == "Root Event").one()
    _seed_schedule(db, event.id)

    assert _create_link(
        root_client,
        event.id,
        expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    ).status_code == 422
    assert _create_link(root_client, event.id, expires_at=_future_iso(366)).status_code == 422
    assert _create_link(root_client, event.id, description="   ").status_code == 422
    assert _create_link(root_client, event.id, view_ids=[10, 10]).status_code == 422
    assert _create_link(root_client, event.id, view_ids=[999]).status_code == 422
    assert db.query(PublicScheduleLink).count() == 0


def test_active_link_properties_change_without_changing_its_token(db, root_client):
    event = db.query(Event).filter(Event.name == "Root Event").one()
    _seed_schedule(db, event.id)
    created = _create_link(root_client, event.id)
    link_id = created.json()["id"]
    token = _token_from_share_url(created.json()["share_url"])

    updated = root_client.patch(
        f"/api/v1/admin/events/{event.id}/public-schedule-links/{link_id}",
        json={
            "description": "Shared with chairs",
            "expires_at": _future_iso(14),
            "view_ids": [11],
        },
    )

    assert updated.status_code == 200
    assert updated.json()["description"] == "Shared with chairs"
    assert [view["id"] for view in updated.json()["views"]] == [11]
    public = root_client.get(
        "/api/v1/public-schedule/shared",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert public.status_code == 200
    assert [view["id"] for view in public.json()["views"]] == [11]
    assert [item["title"] for item in public.json()["items"]] == ["Board Update"]


def test_expiry_and_invalidation_are_permanent(db, root_client):
    event = db.query(Event).filter(Event.name == "Root Event").one()
    _seed_schedule(db, event.id)
    created = _create_link(root_client, event.id)
    link_id = created.json()["id"]
    token = _token_from_share_url(created.json()["share_url"])
    link = db.get(PublicScheduleLink, link_id)
    link.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    patch_url = f"/api/v1/admin/events/{event.id}/public-schedule-links/{link_id}"
    assert root_client.patch(patch_url, json={"expires_at": _future_iso()}).status_code == 409
    assert root_client.get(
        "/api/v1/public-schedule/shared",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 404

    replacement = _create_link(root_client, event.id)
    replacement_id = replacement.json()["id"]
    replacement_token = _token_from_share_url(replacement.json()["share_url"])
    replacement_url = (
        f"/api/v1/admin/events/{event.id}/public-schedule-links/{replacement_id}"
    )
    invalidated = root_client.post(f"{replacement_url}/invalidate", json={})
    assert invalidated.status_code == 200
    assert invalidated.json()["status"] == "invalidated"
    assert root_client.patch(
        replacement_url,
        json={"description": "Cannot revive"},
    ).status_code == 409
    assert root_client.get(
        "/api/v1/public-schedule/shared",
        headers={"Authorization": f"Bearer {replacement_token}"},
    ).status_code == 404


def test_links_can_be_permanently_deleted_in_any_state(db, root_client):
    """Permanent deletion removes active and invalidated links from management."""
    event = db.query(Event).filter(Event.name == "Root Event").one()
    _seed_schedule(db, event.id)
    active = _create_link(root_client, event.id).json()
    invalidated = _create_link(root_client, event.id).json()
    base_url = f"/api/v1/admin/events/{event.id}/public-schedule-links"

    assert root_client.post(
        f"{base_url}/{invalidated['id']}/invalidate",
        json={},
    ).status_code == 200
    assert root_client.delete(f"{base_url}/{active['id']}").status_code == 204
    assert root_client.delete(f"{base_url}/{invalidated['id']}").status_code == 204

    listed = root_client.get(base_url)
    assert listed.status_code == 200
    assert listed.json() == []
    assert db.query(PublicScheduleLink).count() == 0
    assert db.query(PublicScheduleLinkView).count() == 0


def test_shared_response_contains_only_public_programme_fields(db, root_client):
    event = db.query(Event).filter(Event.name == "Root Event").one()
    event.start_date = date(2026, 8, 1)
    event.end_date = date(2026, 8, 3)
    event.metadata_json = json.dumps({
        "day_aliases": {"2026-08-01": "Day 1"},
        "schedule_day_range": {"start_hour": 6, "end_hour": 30},
    })
    db.commit()
    _seed_schedule(db, event.id)
    created = _create_link(root_client, event.id, view_ids=[10])
    token = _token_from_share_url(created.json()["share_url"])

    response = root_client.get(
        "/api/v1/public-schedule/shared",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["event"] == {
        "name": "Root Event",
        "start_date": "2026-08-01",
        "end_date": "2026-08-03",
        "day_aliases": {"2026-08-01": "Day 1"},
        "schedule_day_range": {"start_hour": 6, "end_hour": 30},
    }
    item = data["items"][0]
    assert item["title"] == "Opening Briefing"
    assert item["location_address"] == "1 Parliament Square"
    assert item["responsible"] == "Session president"
    assert item["working_date"] == "2026-08-01"
    assert item["audience_teams"] == [
        {"name": "Delegates", "short_name": "DEL", "colour": "#336699"}
    ]
    payload_text = json.dumps(data)
    for private_value in (
        "Shared with the board",
        "Private template",
        "Internal category",
        "external_session_element_id",
        "copy_template_html",
        "location_note",
        "token_hash",
        "created_by_id",
    ):
        assert private_value not in payload_text


def test_removed_views_are_hidden_and_can_make_link_unavailable(db, root_client):
    event = db.query(Event).filter(Event.name == "Root Event").one()
    _seed_schedule(db, event.id)
    created = _create_link(root_client, event.id)
    link_id = created.json()["id"]
    token = _token_from_share_url(created.json()["share_url"])

    db.query(PublishedGeneralScheduleCategory).filter(
        PublishedGeneralScheduleCategory.event_id == event.id,
        PublishedGeneralScheduleCategory.external_category_id == 10,
    ).delete()
    db.commit()
    available = root_client.get(
        "/api/v1/public-schedule/shared",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert available.status_code == 200
    assert [view["id"] for view in available.json()["views"]] == [11]

    listed = root_client.get(
        f"/api/v1/admin/events/{event.id}/public-schedule-links"
    ).json()[0]
    assert listed["status"] == "active"
    assert {view["id"]: view["available"] for view in listed["views"]} == {
        10: False,
        11: True,
    }

    db.query(PublishedGeneralScheduleCategory).filter(
        PublishedGeneralScheduleCategory.event_id == event.id,
    ).delete()
    db.commit()
    unavailable = root_client.get(
        "/api/v1/public-schedule/shared",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert unavailable.status_code == 404
    assert unavailable.json() == {"detail": "Shared schedule not available"}
    status_response = root_client.get(
        f"/api/v1/admin/events/{event.id}/public-schedule-links"
    ).json()[0]
    assert status_response["id"] == link_id
    assert status_response["status"] == "unavailable"


def test_invalid_tokens_share_one_generic_failure(db, root_client):
    event = db.query(Event).filter(Event.name == "Root Event").one()
    _seed_schedule(db, event.id)
    _create_link(root_client, event.id)

    responses = [
        root_client.get("/api/v1/public-schedule/shared"),
        root_client.get(
            "/api/v1/public-schedule/shared",
            headers={"Authorization": "Bearer definitely-not-a-valid-token"},
        ),
        root_client.get(
            "/api/v1/public-schedule/shared",
            headers={"Authorization": "Basic definitely-not-a-valid-token"},
        ),
    ]
    assert all(response.status_code == 404 for response in responses)
    assert {
        response.json()["detail"] for response in responses
    } == {"Shared schedule not available"}


def test_link_actions_are_audited_without_raw_tokens(db, root_client):
    event = db.query(Event).filter(Event.name == "Root Event").one()
    _seed_schedule(db, event.id)
    created = _create_link(root_client, event.id)
    link_id = created.json()["id"]
    token = _token_from_share_url(created.json()["share_url"])
    root_client.patch(
        f"/api/v1/admin/events/{event.id}/public-schedule-links/{link_id}",
        json={"description": "Updated internal description"},
    )
    link_url = f"/api/v1/admin/events/{event.id}/public-schedule-links/{link_id}"
    root_client.post(f"{link_url}/invalidate", json={})
    root_client.delete(link_url)

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.resource_type == "public_schedule_link")
        .order_by(AuditLog.id)
        .all()
    )
    assert [row.action for row in rows] == [
        "public_schedule_link.create",
        "public_schedule_link.update",
        "public_schedule_link.invalidate",
        "public_schedule_link.delete",
    ]
    assert token not in " ".join(row.detail or "" for row in rows)


def test_event_deletion_cascades_public_schedule_links(db, root_client):
    event = db.query(Event).filter(Event.name == "Root Event").one()
    _seed_schedule(db, event.id)
    _create_link(root_client, event.id)
    assert db.query(PublicScheduleLink).count() == 1
    assert db.query(PublicScheduleLinkView).count() == 2

    db.delete(event)
    db.commit()

    assert db.query(PublicScheduleLink).count() == 0
    assert db.query(PublicScheduleLinkView).count() == 0
