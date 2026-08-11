"""Contracts for locally controlled, immutable governance publications."""

import json
from copy import deepcopy
from pathlib import Path

from app.core import governance_rendering
from app.core.config import settings
from server_backend.conftest import _make_client, create_test_event, create_test_user


PROFILE = {
    "controller_type": "organisation",
    "controller_legal_name": "Example Association",
    "controller_postal_address": "Example Street 1, 8000 Zurich",
    "controller_country": "ch",
    "privacy_contact_email": "privacy@synthetic-controller.ch",
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


def test_optional_controller_details_do_not_block_a_truthful_minimum_notice(db):
    client, _root = _root_with_reauth(db)
    profile = deepcopy(PROFILE)
    for key in (
        "controller_postal_address", "controller_country",
        "supervisory_authority_name", "supervisory_authority_url",
        "processor_summary", "retention_summary", "rights_summary", "terms_summary",
    ):
        profile[key] = ""
    profile["structured"]["instance_name"] = ""
    profile["structured"]["jurisdiction_scope"] = ""
    profile["structured"]["incident_contact_email"] = None
    profile["structured"]["hosting_countries"] = []
    for purpose in profile["structured"]["processing_purposes"]:
        purpose["gdpr_legal_basis"] = None
        purpose["swiss_justification_or_basis"] = None

    saved = client.put("/api/v1/admin/governance", json=profile)
    assert saved.status_code == 200, saved.json()
    preflight = saved.json()["preflight"]
    assert preflight["ready"] is True
    statuses = {item["code"]: item["status"] for item in preflight["checks"]}
    assert statuses["controller_address"] == "optional"
    assert statuses["supervisory_authority"] == "optional"
    assert statuses["rights_summary"] == "optional"
    assert statuses["controller_basis_decisions"] == "externally_unverifiable"

    published = client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION)
    assert published.status_code == 200, published.json()
    rights = client.get("/api/v1/governance/public/rights.html")
    assert rights.status_code == 200
    assert "competent data-protection supervisory authority" in rights.text
    assert "mailto:privacy@synthetic-controller.ch" in rights.text


def test_retention_needs_periods_or_clear_controller_criteria(db):
    client, _root = _root_with_reauth(db)
    profile = deepcopy(PROFILE)
    profile["retention_summary"] = ""
    profile["structured"]["retention"]["live_retention_days"] = None
    profile["structured"]["retention"]["backup_retention_days"] = None
    profile["structured"]["retention"]["receipt_retention_days"] = None

    saved = client.put("/api/v1/admin/governance", json=profile)
    assert saved.status_code == 200, saved.json()
    check = next(item for item in saved.json()["preflight"]["checks"] if item["code"] == "retention_configuration")
    assert check["status"] == "missing"
    assert saved.json()["preflight"]["ready"] is False


def test_runtime_feature_declarations_are_authoritative_on_draft_save(db, monkeypatch):
    client, _root = _root_with_reauth(db)
    imported = deepcopy(PROFILE)
    imported["structured"]["optional_features"]["ha_enabled"] = False
    monkeypatch.setattr(
        governance_rendering,
        "runtime_feature_state",
        lambda: {
            "smtp_enabled": False,
            "push_enabled": False,
            "ha_enabled": True,
            "dns_mode": "dns_only",
        },
    )

    saved = client.put("/api/v1/admin/governance", json=imported)

    assert saved.status_code == 200, saved.json()
    assert saved.json()["draft"]["structured"]["optional_features"]["ha_enabled"] is True
    assert any(
        item["governance_field"] == "optional_features.ha_enabled"
        and item["previous"] is False
        and item["current"] is True
        for item in saved.json()["runtime_enforced_changes"]
    )
    ha_check = next(
        item for item in saved.json()["preflight"]["checks"] if item["code"] == "ha_enabled"
    )
    assert ha_check["status"] == "ready"


def test_runtime_retention_settings_are_authoritative_and_require_republication(db):
    client, _root = _root_with_reauth(db)
    imported = deepcopy(PROFILE)
    imported["structured"]["retention"]["event_grace_days"] = 2
    imported["structured"]["retention"]["audit_retention_days"] = 31
    imported["structured"]["retention"]["browser_cache_expiry_hours"] = 3

    saved = client.put("/api/v1/admin/governance", json=imported)
    assert saved.status_code == 200, saved.json()
    retained = saved.json()["draft"]["structured"]["retention"]
    assert retained["event_grace_days"] == settings.EVENT_PURGE_GRACE_DAYS
    assert retained["audit_retention_days"] == 90
    assert retained["browser_cache_expiry_hours"] == 24
    assert {item["governance_field"] for item in saved.json()["runtime_enforced_changes"]} == {
        "retention.event_grace_days", "retention.audit_retention_days",
        "retention.browser_cache_expiry_hours",
    }

    published = client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION)
    assert published.status_code == 200, published.json()
    original_public = client.get("/api/v1/governance/public").json()
    assert original_public["retention"]["audit_retention_days"] == 90

    changed = client.put("/api/v1/admin/settings", json={"settings": {"audit_log_retention_days": 120}})
    assert changed.status_code == 200, changed.json()
    assert changed.json()["governance_impact"]["draft_updated"] is True
    assert changed.json()["governance_impact"]["publication_required"] is True
    draft = client.get("/api/v1/admin/governance").json()
    assert draft["draft"]["structured"]["retention"]["audit_retention_days"] == 120
    assert draft["runtime_impact"]["publication_required"] is True
    assert client.get("/api/v1/governance/public").json()["retention"]["audit_retention_days"] == 90
    assert any(
        item["path"] == "retention.audit_retention_days"
        for item in client.get("/api/v1/admin/governance/preview").json()["diff"]["changes"]
    )

    republished = client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION)
    assert republished.status_code == 200, republished.json()
    assert republished.json()["version"] == 2
    assert client.get("/api/v1/governance/public").json()["retention"]["audit_retention_days"] == 120
    assert client.get("/api/v1/admin/governance").json()["runtime_impact"]["publication_required"] is False


def test_runtime_retention_reversion_clears_stale_governance_impact(db):
    from app.models.server_setting import ServerSetting

    client, _root = _root_with_reauth(db)
    saved = client.put("/api/v1/admin/governance", json=PROFILE)
    assert saved.status_code == 200, saved.json()
    published = client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION)
    assert published.status_code == 200, published.json()

    changed = client.put(
        "/api/v1/admin/settings",
        json={"settings": {"event_purge_grace_days": settings.EVENT_PURGE_GRACE_DAYS + 1}},
    )
    assert changed.status_code == 200, changed.json()
    assert changed.json()["governance_impact"]["publication_required"] is True

    restored = client.put(
        "/api/v1/admin/settings",
        json={"settings": {"event_purge_grace_days": settings.EVENT_PURGE_GRACE_DAYS}},
    )
    assert restored.status_code == 200, restored.json()
    assert restored.json()["governance_impact"] == {
        "draft_updated": False,
        "publication_required": False,
        "changed_at": None,
        "changes": [],
    }
    draft = client.get("/api/v1/admin/governance").json()
    assert draft["runtime_impact"]["publication_required"] is False
    assert draft["runtime_impact"]["changes"] == []
    preview = client.get("/api/v1/admin/governance/preview").json()
    assert preview["diff"]["changes"] == []
    assert (
        client.get("/api/v1/governance/public").json()["retention"]["event_grace_days"]
        == settings.EVENT_PURGE_GRACE_DAYS
    )

    # Deploying the fix must also hide a no-op marker already persisted by the
    # previous implementation; operators should not have to repeat the change.
    changed_fields = db.query(ServerSetting).filter(
        ServerSetting.key == "governance_runtime_changed_fields"
    ).one()
    changed_fields.value = json.dumps([{
        "setting": "event_purge_grace_days",
        "governance_field": "retention.event_grace_days",
        "label": "Event purge grace",
        "previous": settings.EVENT_PURGE_GRACE_DAYS,
        "current": settings.EVENT_PURGE_GRACE_DAYS,
    }])
    publication_required = db.query(ServerSetting).filter(
        ServerSetting.key == "governance_runtime_publication_required"
    ).one()
    publication_required.value = "true"
    db.commit()

    reconciled = client.get("/api/v1/admin/governance").json()["runtime_impact"]
    assert reconciled["draft_updated"] is False
    assert reconciled["publication_required"] is False
    assert reconciled["changed_at"] is None
    assert reconciled["changes"] == []


def test_preflight_reports_required_controller_trust_before_publication(monkeypatch, db):
    monkeypatch.setattr(settings, "KEY_SEPARATION_ENFORCED", True)
    client, _root = _root_with_reauth(db)

    saved = client.put("/api/v1/admin/governance", json=PROFILE)

    assert saved.status_code == 200
    trust_check = next(item for item in saved.json()["preflight"]["checks"] if item["code"] == "controller_trust")
    assert trust_check["status"] == "missing"
    assert saved.json()["preflight"]["ready"] is False
    preview = client.get("/api/v1/admin/governance/preview").json()
    assert preview["preflight"]["ready"] is False
    blocked = client.post("/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "controller_trust_required"


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


def test_immutable_privacy_and_rights_pages_are_styled_human_readable_notices(db):
    client, _root = _root_with_reauth(db)
    profile = {
        **PROFILE,
        "dpo_contact": "dpo@synthetic-controller.ch",
    }
    assert client.put("/api/v1/admin/governance", json=profile).status_code == 200
    published = client.post(
        "/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION
    ).json()

    privacy = client.get("/api/v1/governance/public/versions/1/privacy.html")
    rights = client.get("/api/v1/governance/public/versions/1/rights.html")

    for response in (privacy, rights):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert '<article class="notice-shell">' in response.text
        assert 'font-family:"Source Sans 3"' in response.text
        assert 'src="/logo_normal.png"' in response.text
        assert 'alt="Masterplan Optimiser"' in response.text
        assert f"SHA-256 {published['content_sha256']}" in response.text
        assert 'href="/api/v1/governance/public/versions/1/privacy.html"' in response.text
        assert 'href="/api/v1/governance/public/versions/1/rights.html"' in response.text
        assert '"controller_legal_name"' not in response.text
        assert "<pre>" not in response.text
        assert "<script>" not in response.text

    assert "Purpose and permitted information" in privacy.text
    assert "Cookies and browser storage" in privacy.text
    assert "Processors and service providers" in privacy.text
    assert 'href="tel:' not in privacy.text
    assert 'href="mailto:dpo@synthetic-controller.ch">dpo@synthetic-controller.ch</a>' in privacy.text
    assert 'href="rights.html">Read how to exercise your rights</a>' in privacy.text
    terms = client.get("/api/v1/governance/public/versions/1/terms.html")
    assert terms.status_code == 200
    assert 'href="data-policy.html">permitted-data boundary</a>' in terms.text
    assert "How to make a request" in rights.text
    assert "What happens next" in rights.text
    assert "Email the privacy contact" in rights.text
    assert "Access" in rights.text
    assert "Correction" in rights.text
    assert "Erasure" in rights.text
    assert "Restriction" in rights.text
    assert "Objection" in rights.text
    assert "Portability" in rights.text
    assert "Automated decisions" in rights.text
    assert "You can ask in ordinary language" in rights.text
    assert "GDPR Art. 15" in rights.text
    assert "FADP Art. 25" in rights.text
    assert "GDPR Articles 77 and 79" in rights.text
    assert "FADP Articles 32 and 49 onward" in rights.text


def test_governance_rejects_the_retired_phone_contact(db):
    client, _root = _root_with_reauth(db)
    response = client.put(
        "/api/v1/admin/governance",
        json={**PROFILE, "privacy_contact_phone": "+41 00 000 00 00"},
    )

    assert response.status_code == 422
    assert "privacy_contact_phone is retired" in response.text


def test_email_only_governance_migration_does_not_rewrite_publications():
    migration = (
        Path(__file__).resolve().parents[1]
        / "deploy"
        / "migrations"
        / "20260811_governance_email_only.sql"
    ).read_text(encoding="utf-8").lower()

    assert "drop column if exists privacy_contact_phone" in migration
    assert "governance_publications" not in migration


def test_every_public_governance_section_uses_the_shared_masterplan_shell(db):
    client, _root = _root_with_reauth(db)
    assert client.put("/api/v1/admin/governance", json=PROFILE).status_code == 200
    assert client.post(
        "/api/v1/admin/governance/publish", json=PUBLICATION_CONFIRMATION
    ).status_code == 200

    for section in ("privacy", "legal", "terms", "data-policy", "retention", "rights", "processors"):
        response = client.get(f"/api/v1/governance/public/versions/1/{section}.html")
        assert response.status_code == 200
        assert '<article class="notice-shell">' in response.text
        assert '<nav class="notice-nav"' in response.text
        assert '<footer class="notice-footer">' in response.text
        assert 'src="/logo_normal.png"' in response.text
        assert "Masterplan Optimiser self-hosted instance" in response.text


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
        assert (
            'path.join(process.cwd(), "legal-artifacts"' in source
            or "path.join(process.cwd(), 'legal-artifacts'" in source
        )
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
