"""Contracts for locally controlled, immutable governance publications."""

import json
from pathlib import Path

from server_backend.conftest import _make_client, create_test_event, create_test_user


PROFILE = {
    "controller_type": "organisation",
    "controller_legal_name": "Example Association",
    "controller_postal_address": "Example Street 1, 8000 Zurich",
    "controller_country": "ch",
    "privacy_contact_email": "privacy@synthetic-controller.ch",
    "privacy_contact_phone": None,
    "dpo_contact": None,
    "supervisory_authority_name": "Federal Data Protection and Information Commissioner",
    "supervisory_authority_url": "https://www.edoeb.admin.ch/",
    "default_locale": "en",
    "processor_summary": "The controller uses its VPS, SMTP and DNS providers for the stated purposes.",
    "retention_summary": "Event data is removed according to the controller's documented event schedule.",
    "rights_summary": "Contact the controller to exercise applicable access, correction or deletion rights.",
    "terms_summary": "Use is limited to authorised operational event scheduling.",
    "structured": {
        "instance_name": "Synthetic governance test instance",
        "supported_locales": ["en"],
        "jurisdiction_scope": "The controller recorded Swiss and European data-protection scope for this synthetic test.",
        "processing_purposes": [
            {
                "purpose_code": "event_scheduling",
                "enabled": True,
                "description": "Create and publish operational event schedules.",
                "gdpr_legal_basis": "Controller-recorded contractual and legitimate-interest assessment",
                "swiss_justification_or_basis": "Controller-recorded operational necessity assessment",
                "required_or_optional": "required",
            },
            {
                "purpose_code": "offline_schedule",
                "enabled": True,
                "description": "Store a participant's limited schedule on their device when requested.",
                "gdpr_legal_basis": "Controller-recorded user-requested service assessment",
                "required_or_optional": "optional",
                "withdrawal_effect": "Removing offline access deletes the device copy.",
            },
        ],
        "data_categories": [
            {
                "category_code": "operational_identity",
                "display_name": "names and operational roles",
                "enabled": True,
                "required_or_optional": "required",
                "visibility": "participant",
                "source": "controller and participant",
                "purpose_codes": ["event_scheduling"],
                "retention_policy_code": "instance_default",
                "sensitive_data_supported": False,
            },
            {
                "category_code": "availability",
                "display_name": "availability and operational instructions",
                "enabled": True,
                "required_or_optional": "optional",
                "visibility": "organiser",
                "source": "participant",
                "purpose_codes": ["event_scheduling", "offline_schedule"],
                "retention_policy_code": "instance_default",
                "sensitive_data_supported": False,
            },
        ],
        "processors": [
            {
                "provider_code": "vps_primary",
                "display_name": "Synthetic Swiss VPS Provider",
                "service": "Application and database hosting",
                "role": "processor",
                "purpose_codes": ["event_scheduling"],
                "data_categories": ["operational_identity", "availability"],
                "hosting_countries": ["CH"],
                "support_access_countries": ["CH"],
                "dpa_status": "accepted",
                "dpa_version": "synthetic-2026-07",
                "transfer_mechanism": "No transfer outside Switzerland recorded",
                "public_notice_summary": "Hosts this synthetic test instance in Switzerland.",
                "enabled": True,
            }
        ],
        "hosting_countries": ["CH"],
        "retention": {
            "policy_code": "instance_default",
            "live_retention_days": 30,
            "event_grace_days": 7,
            "backup_retention_days": 30,
            "audit_retention_days": 90,
            "receipt_retention_days": 365,
            "browser_cache_expiry_hours": 24,
            "automatic_purge_enabled": True,
            "legal_hold_supported": True,
        },
        "optional_features": {
            "smtp_enabled": False,
            "push_enabled": False,
            "offline_schedule_enabled": True,
            "public_schedule_enabled": True,
            "external_support_enabled": False,
            "ha_enabled": False,
            "dns_mode": "dns_only",
            "backup_storage_mode": "manual_portable",
        },
        "rights_request_url": "https://synthetic-controller.ch/rights",
        "incident_contact_email": "incident@synthetic-controller.ch",
    },
}

PUBLICATION_CONFIRMATION = {
    "authorised_to_configure": True,
    "reviewed_generated_documents": True,
    "confirmed_permitted_data_policy": True,
    "understands_no_legal_certification": True,
}


def _root_with_reauth(db):
    root = create_test_user(
        db, username="governance.root", display_name="Governance Root",
        is_root_admin=True, is_admin=True,
    )
    return _make_client(db, root, reauth=True), root


def test_draft_is_private_and_publication_is_immutable(db):
    client, _root = _root_with_reauth(db)

    saved = client.put("/api/v1/admin/governance", json=PROFILE)
    assert saved.status_code == 200, saved.json()
    hidden = client.get("/api/v1/governance/public").json()
    assert hidden == {
        "configured": False,
        "message": "This self-hosted instance has not published its controller notice yet.",
    }

    publication = client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION)
    assert publication.status_code == 200
    assert publication.json()["version"] == 1
    public = client.get("/api/v1/governance/public").json()
    assert public["configured"] is True
    assert public["controller_legal_name"] == "Example Association"
    assert public["controller_country"] == "CH"
    assert len(public["content_sha256"]) == 64
    storage = public["storage"]
    assert "IndexedDB offline calendar response" in storage["offline_schedule"]
    assert "linked identity" in storage["offline_schedule"]
    assert "other participant identities" in storage["offline_schedule"]
    assert "server-bounded expiry" in storage["offline_schedule"]
    assert "never stores authenticated API responses" in storage["application_shell"]
    assert "localStorage" in storage["preferences"]
    assert "sessionStorage" in storage["tab_state"]
    assert "coarse browser" in storage["session_metadata"]
    assert "daily keyed IP pseudonym" in storage["session_metadata"]
    assert "reviewed and revoked" in storage["session_metadata"]
    assert public["optional_features"]["smtp_enabled"] is False
    assert not any(item["code"] == "activation_email" for item in public["feature_disclosures"])
    assert any(item["code"] == "manual_activation" for item in public["feature_disclosures"])
    assert any(item["code"] == "dns_only_routing" for item in public["feature_disclosures"])
    assert "internal_notes_reference" not in public["processors"][0]

    retry = client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION)
    assert retry.json()["status"] == "unchanged"
    assert retry.json()["version"] == 1


def test_public_legal_notice_is_readable_without_javascript_and_escapes_controller_text(db):
    client, _root = _root_with_reauth(db)
    client.put("/api/v1/admin/governance", json=PROFILE)
    client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION)
    profile = {**PROFILE, "controller_legal_name": "Synthetic <script>alert(1)</script> Association"}
    client.put("/api/v1/admin/governance", json=profile)
    blocked = client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION)
    assert blocked.status_code == 409

    response = client.get("/api/v1/governance/public/privacy.html")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<script>" not in response.text
    assert "Example Association" in response.text
    assert "fetch(" not in response.text
    assert 'href="/rights"' in response.text
    assert "IndexedDB offline calendar response" in response.text
    assert "linked identity" in response.text
    assert "sessionStorage" in response.text


def test_root_can_review_saved_draft_as_private_html_before_publication(db):
    client, _root = _root_with_reauth(db)
    profile = {**PROFILE, "controller_legal_name": "Draft <script>alert(1)</script> Controller"}
    assert client.put("/api/v1/admin/governance", json=profile).status_code == 200

    response = client.get("/api/v1/admin/governance/preview/privacy.html")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert "Private draft preview" in response.text
    assert "Draft &lt;script&gt;alert(1)&lt;/script&gt; Controller" in response.text
    assert "<script>" not in response.text
    assert "/api/v1/admin/governance/preview/rights.html" in response.text
    assert "Published policy version" not in response.text
    assert client.get("/api/v1/governance/public").json()["configured"] is False

    participant = create_test_user(db, username="governance.preview.denied")
    denied = _make_client(db, participant).get("/api/v1/admin/governance/preview/privacy.html")
    assert denied.status_code == 403


def test_preview_classifies_material_changes_and_export_contains_only_published_snapshot(db):
    client, _root = _root_with_reauth(db)
    saved = client.put("/api/v1/admin/governance", json=PROFILE)
    assert saved.status_code == 200, saved.json()
    assert client.get("/api/v1/admin/governance/preview").json()["diff"]["material_change"] is True
    published = client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION).json()

    spelling = {**PROFILE, "terms_summary": PROFILE["terms_summary"] + " Authorised users only."}
    assert client.put("/api/v1/admin/governance", json=spelling).status_code == 200
    preview = client.get("/api/v1/admin/governance/preview").json()
    assert preview["diff"]["material_change"] is False
    second = client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION).json()
    assert second["material_change"] is False
    assert second["version"] == 2

    exported = client.get("/api/v1/admin/governance/export/2")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    assert exported.json()["content_sha256"] == second["content_sha256"]
    assert exported.json()["supersedes_version"] == 1
    assert exported.json()["source_sha256"]
    assert exported.json()["source_configuration"]["structured"]["processing_purposes"][0]["gdpr_legal_basis"]


def test_event_controller_override_requires_root_review_and_complete_contact(db):
    client, _root = _root_with_reauth(db)
    client.put("/api/v1/admin/governance", json=PROFILE)
    client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION)
    event, _secret = create_test_event(db, name="Different Controller Event")

    incomplete = client.put(
        f"/api/v1/admin/governance/events/{event.id}",
        json={"controller_override_enabled": True, "controller_identity_override": "Event Controller"},
    )
    assert incomplete.status_code == 422

    disabled = client.put(
        f"/api/v1/admin/governance/events/{event.id}",
        json={"enabled_optional_features": ["activation_email"]},
    )
    assert disabled.status_code == 422
    assert disabled.json()["detail"] == {
        "code": "event_governance_feature_disabled",
        "disabled_features": ["activation_email"],
        "enabled_features": ["offline_schedule", "public_schedule"],
    }

    saved = client.put(
        f"/api/v1/admin/governance/events/{event.id}",
        json={
            "controller_override_enabled": True,
            "controller_identity_override": "Event Controller",
            "privacy_contact_override": "privacy@synthetic-event-controller.ch",
            "retention_override_days": 14,
            "enabled_optional_features": ["public_schedule"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["controller_identity"] == "Event Controller"
    assert saved.json()["retention_days"] == 14
    public = client.get(f"/api/v1/governance/public/events/{event.id}")
    assert public.status_code == 200
    assert public.json()["policy_version"] == 1
    assert public.json()["enabled_optional_features"] == ["public_schedule"]
    assert "event_name" not in public.json()
    private = client.get(f"/api/v1/admin/governance/events/{event.id}")
    assert private.status_code == 200
    assert private.json()["event_name"] == "Different Controller Event"


def test_frontend_legal_artifacts_are_self_contained_and_exact():
    root = Path(__file__).resolve().parents[1]
    for name in ("LICENSE", "THIRD-PARTY-NOTICES.md", "SECURITY.md"):
        assert (root / "web" / "legal-artifacts" / name).read_bytes() == (root / name).read_bytes()

    pages = {
        "LICENSE": root / "web/src/app/licence/page.tsx",
        "THIRD-PARTY-NOTICES.md": root / "web/src/app/third-party-notices/page.tsx",
        "SECURITY.md": root / "web/src/app/security/page.tsx",
    }
    for name, page in pages.items():
        source = page.read_text(encoding="utf-8")
        assert 'path.join(process.cwd(), "legal-artifacts"' in source
        assert name in source
        assert "SECURITY_REPORT.md" not in source


def test_root_only_basis_change_creates_material_version_without_public_disclosure(db):
    client, _root = _root_with_reauth(db)
    client.put("/api/v1/admin/governance", json=PROFILE)
    first = client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION).json()
    changed = json.loads(json.dumps(PROFILE))
    changed["structured"]["processing_purposes"][0]["gdpr_legal_basis"] = "Controller-recorded public-task assessment"
    client.put("/api/v1/admin/governance", json=changed)

    second = client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION).json()

    assert second["version"] == 2
    assert second["material_change"] is True
    assert second["content_sha256"] == first["content_sha256"]
    assert second["source_sha256"] != first["source_sha256"]
    public = client.get("/api/v1/governance/public").json()
    assert "gdpr_legal_basis" not in json.dumps(public)
    exported = client.get("/api/v1/admin/governance/export/2").json()
    assert exported["source_configuration"]["structured"]["processing_purposes"][0]["gdpr_legal_basis"] == "Controller-recorded public-task assessment"


def test_current_policy_must_be_acknowledged_before_event_edit(db):
    root_client, _root = _root_with_reauth(db)
    root_client.put("/api/v1/admin/governance", json=PROFILE)
    root_client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION)

    event, _ = create_test_event(db, name="Acknowledgement Event")
    editor = create_test_user(db, username="policy.editor", event_id=event.id, can_edit=True)
    editor_client = _make_client(db, editor)

    missing = editor_client.post(
        f"/api/v1/calendar/{event.id}/tasks/commit",
        json={"edits": [], "deletions": [], "new_tasks": []},
    )
    assert missing.status_code == 428
    assert missing.json()["detail"]["code"] == "data_policy_acknowledgement_required"

    accepted = editor_client.post(
        "/api/v1/user/data-policy/acknowledge",
        json={
            "event_id": event.id,
            "scope": "authorised_editor",
            "policy_version": 1,
            "policy_sha256": root_client.get("/api/v1/governance/public").json()["content_sha256"],
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["policy_version"] == 1

    committed = editor_client.post(
        f"/api/v1/calendar/{event.id}/tasks/commit",
        json={"edits": [], "deletions": [], "new_tasks": []},
    )
    assert committed.status_code == 200


def test_user_cannot_claim_a_more_privileged_policy_scope(db):
    root_client, _root = _root_with_reauth(db)
    root_client.put("/api/v1/admin/governance", json=PROFILE)
    root_client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION)
    event, _ = create_test_event(db, name="Scoped acknowledgement")
    editor = create_test_user(db, username="scope.editor", event_id=event.id, can_edit=True)

    response = _make_client(db, editor).post(
        "/api/v1/user/data-policy/acknowledge",
        json={
            "event_id": event.id,
            "scope": "field_visibility_administrator",
            "policy_version": 1,
            "policy_sha256": root_client.get("/api/v1/governance/public").json()["content_sha256"],
        },
    )

    assert response.status_code == 403
