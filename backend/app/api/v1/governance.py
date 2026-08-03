"""Versioned, self-hosted governance configuration and public notices."""

import hashlib
import html
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.config import settings
from app.core.governance import (
    current_policy_identity,
    current_policy_version,
    require_current_policy_identity,
    stable_instance_id,
)
from app.core.governance_rendering import (
    POLICY_TEMPLATE_VERSION,
    build_publication_payload,
    governance_preflight,
    profile_structured,
    publication_diff,
    runtime_feature_state,
)
from app.core.evidence import lock_evidence_transaction
from app.core.security import get_current_user, require_root_admin_read_only, require_root_recent_reauth
from app.db.database import get_db
from app.models.governance import (
    DataPolicyAcknowledgement, EventGovernanceOverride, GovernancePublication,
    InstanceGovernanceProfile,
)
from app.models.event import Event
from app.models.evidence import EvidenceKey
from app.models.user import User

public_router = APIRouter()
admin_router = APIRouter()
user_router = APIRouter()

PUBLIC_SECTIONS = ("privacy", "legal", "terms", "data-policy", "retention", "rights", "processors")


PurposeCode = Literal[
    "event_scheduling", "account_authentication", "activation_email", "security_audit",
    "offline_schedule", "push_notifications", "public_schedule", "backup_and_recovery", "support",
]


class ProcessingPurposeIn(BaseModel):
    purpose_code: PurposeCode
    enabled: bool = True
    description: str = Field(min_length=1, max_length=500)
    gdpr_legal_basis: str | None = Field(default=None, max_length=200)
    swiss_justification_or_basis: str | None = Field(default=None, max_length=200)
    legitimate_interest_summary: str | None = Field(default=None, max_length=1000)
    required_or_optional: Literal["required", "optional"]
    withdrawal_effect: str | None = Field(default=None, max_length=500)


class DataCategoryIn(BaseModel):
    category_code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    required_or_optional: Literal["required", "optional"]
    visibility: Literal["root", "organiser", "participant", "public"]
    source: str = Field(min_length=1, max_length=200)
    purpose_codes: list[PurposeCode] = Field(min_length=1, max_length=9)
    retention_policy_code: str = Field(min_length=1, max_length=64)
    sensitive_data_supported: Literal[False] = False


class ProcessorEntryIn(BaseModel):
    provider_code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    display_name: str = Field(min_length=1, max_length=200)
    service: str = Field(min_length=1, max_length=200)
    role: Literal["processor", "independent_controller", "infrastructure_provider"]
    purpose_codes: list[PurposeCode] = Field(min_length=1, max_length=9)
    data_categories: list[str] = Field(min_length=1, max_length=32)
    hosting_countries: list[str] = Field(min_length=1, max_length=20)
    support_access_countries: list[str] = Field(default_factory=list, max_length=20)
    dpa_status: Literal["accepted", "pending", "not_required", "unknown"]
    dpa_version: str | None = Field(default=None, max_length=100)
    subprocessor_url: HttpUrl | None = None
    transfer_mechanism: str | None = Field(default=None, max_length=500)
    public_notice_summary: str = Field(min_length=1, max_length=1000)
    internal_notes_reference: str | None = Field(default=None, max_length=500)
    review_due_at: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    enabled: bool = True

    @field_validator("hosting_countries", "support_access_countries")
    @classmethod
    def normalise_countries(cls, values: list[str]) -> list[str]:
        normalised = [value.strip().upper() for value in values]
        if any(len(value) != 2 or not value.isalpha() for value in normalised):
            raise ValueError("countries must use two-letter codes")
        return sorted(set(normalised))


class RetentionConfigurationIn(BaseModel):
    policy_code: str = Field(default="instance_default", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    live_retention_days: int | None = Field(default=None, ge=1, le=3650)
    event_grace_days: int | None = Field(default=None, ge=1, le=3650)
    backup_retention_days: int | None = Field(default=None, ge=1, le=3650)
    audit_retention_days: int | None = Field(default=None, ge=1, le=3650)
    receipt_retention_days: int | None = Field(default=None, ge=1, le=3650)
    browser_cache_expiry_hours: int | None = Field(default=None, ge=1, le=168)
    automatic_purge_enabled: bool | None = None
    legal_hold_supported: bool | None = None


class OptionalFeaturesIn(BaseModel):
    smtp_enabled: bool
    smtp_provider_code: str | None = Field(default=None, max_length=64)
    push_enabled: bool
    push_provider_codes: list[str] = Field(default_factory=list, max_length=8)
    offline_schedule_enabled: bool
    public_schedule_enabled: bool
    external_support_enabled: bool
    ha_enabled: bool
    dns_mode: Literal["dns_only"] = "dns_only"
    backup_storage_mode: Literal["manual_portable", "ssh_archive", "controller_managed"]


class GovernanceStructuredIn(BaseModel):
    instance_name: str = Field(default="", max_length=200)
    dpo_name_or_role: str | None = Field(default=None, max_length=200)
    eu_representative: str | None = Field(default=None, max_length=500)
    swiss_representative: str | None = Field(default=None, max_length=500)
    supported_locales: list[str] = Field(default_factory=lambda: ["en"], min_length=1, max_length=10)
    jurisdiction_scope: str = Field(default="", max_length=1000)
    processing_purposes: list[ProcessingPurposeIn] = Field(default_factory=list, max_length=20)
    data_categories: list[DataCategoryIn] = Field(default_factory=list, max_length=64)
    processors: list[ProcessorEntryIn] = Field(default_factory=list, max_length=64)
    hosting_countries: list[str] = Field(default_factory=list, max_length=20)
    retention: RetentionConfigurationIn
    optional_features: OptionalFeaturesIn
    rights_request_url: HttpUrl | None = None
    incident_contact_email: EmailStr | None = None

    @field_validator("hosting_countries")
    @classmethod
    def normalise_hosting_countries(cls, values: list[str]) -> list[str]:
        normalised = [value.strip().upper() for value in values]
        if any(len(value) != 2 or not value.isalpha() for value in normalised):
            raise ValueError("hosting countries must use two-letter codes")
        return sorted(set(normalised))


class GovernanceDraft(BaseModel):
    """Controller facts needed to publish an instance-specific legal centre."""

    controller_type: Literal["organisation", "individual"]
    controller_legal_name: str = Field(min_length=1, max_length=200)
    controller_postal_address: str = Field(min_length=1, max_length=500)
    controller_country: str = Field(min_length=2, max_length=2)
    privacy_contact_email: EmailStr
    privacy_contact_phone: str | None = Field(default=None, max_length=64)
    dpo_contact: str | None = Field(default=None, max_length=320)
    supervisory_authority_name: str = Field(min_length=1, max_length=200)
    supervisory_authority_url: HttpUrl
    default_locale: str = Field(default="en", pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    processor_summary: str = Field(min_length=1, max_length=4000)
    retention_summary: str = Field(min_length=1, max_length=4000)
    rights_summary: str = Field(min_length=1, max_length=4000)
    terms_summary: str = Field(min_length=1, max_length=4000)
    structured: GovernanceStructuredIn

    @field_validator("controller_country")
    @classmethod
    def normalise_country(cls, value: str) -> str:
        return value.upper()


def _profile_payload(profile: InstanceGovernanceProfile) -> dict[str, object]:
    return build_publication_payload(profile)


@public_router.get("/public")
def public_governance(db: Session = Depends(get_db)):
    """Return only the latest published controller notice, never a draft."""

    publication = db.query(GovernancePublication).order_by(
        GovernancePublication.version.desc()
    ).first()
    if publication is None:
        return {
            "configured": False,
            "message": "This self-hosted instance has not published its controller notice yet.",
        }
    return {
        "configured": True,
        "version": publication.version,
        "published_at": publication.published_at,
        "content_sha256": publication.content_sha256,
        "supersedes_version": publication.supersedes_version,
        "material_change": publication.material_change,
        **json.loads(publication.content_json),
    }


def _text(value: object | None) -> str:
    return html.escape(str(value or ""), quote=True)


def _paragraphs(value: object | None) -> str:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    return "".join(f"<p>{_text(line)}</p>" for line in lines)


def _render_governance_html(
    section: str,
    *,
    notice: dict[str, object] | None,
    version: int | None = None,
    published_at: datetime | None = None,
    preview: bool = False,
) -> HTMLResponse:
    """Render one public or root-only preview legal-centre section."""

    if section not in PUBLIC_SECTIONS:
        raise HTTPException(status_code=404, detail="Legal-centre section not found")
    headings = {
        "privacy": "Privacy notice",
        "legal": "Controller and legal notice",
        "terms": "Instance terms",
        "data-policy": "Permitted-data policy",
        "retention": "Retention and deletion",
        "rights": "Your data-protection rights",
        "processors": "Processors and service providers",
    }
    body: list[str] = [f"<h1>{headings[section]}</h1>"]
    if preview:
        body.append(
            "<p><strong>Private draft preview — not published.</strong> "
            "Preview markers and publication metadata are not part of the final public notice.</p>"
        )
    if notice is None:
        body.extend([
            "<h2>Notice not configured</h2>",
            "<p>This self-hosted instance has not published its controller notice yet.</p>",
            "<p>Contact the instance operator. Generic project information is not the privacy notice for this deployment.</p>",
        ])
    else:
        if section in {"privacy", "legal"}:
            body.extend([
                "<h2>Controller</h2>",
                f"<p>{_text(notice.get('controller_legal_name'))}</p>",
                _paragraphs(notice.get("controller_postal_address")),
                f"<p>{_text(notice.get('controller_country'))}</p>",
                f"<p>Email: <a href=\"mailto:{_text(notice.get('privacy_contact_email'))}\">{_text(notice.get('privacy_contact_email'))}</a></p>",
            ])
            if notice.get("privacy_contact_phone"):
                body.append(f"<p>Telephone: {_text(notice['privacy_contact_phone'])}</p>")
            if notice.get("dpo_contact"):
                body.append(f"<p>Data-protection contact: {_text(notice['dpo_contact'])}</p>")
        if section in {"privacy", "data-policy"}:
            policy = notice.get("permitted_data") or {}
            body.extend([
                "<h2>Purpose and permitted information</h2>",
                f"<p>{_text(policy.get('purpose'))}. Optional data must be necessary for that purpose.</p>",
                f"<p><strong>Normally permitted:</strong> {_text(', '.join(policy.get('allowed') or []))}.</p>",
                f"<p><strong>Not supported:</strong> {_text(', '.join(policy.get('unsupported') or []))}.</p>",
            ])
        if section == "privacy":
            storage = notice.get("storage") or {}
            body.extend([
                "<h2>Passkey authentication</h2>",
                f"<p>{_text(notice.get('authentication'))}</p>",
                "<h2>Cookies and browser storage</h2>",
                "<p>No analytics, advertising or cross-site tracking is enabled by the supported release.</p>",
                "<ul>",
                f"<li>{_text(storage.get('session_cookie'))}</li>",
                f"<li>{_text(storage.get('csrf_cookie'))}</li>",
                f"<li>{_text(storage.get('session_metadata'))}</li>",
                f"<li>{_text(storage.get('application_shell'))}</li>",
                f"<li>{_text(storage.get('preferences'))}</li>",
                f"<li>{_text(storage.get('tab_state'))}</li>",
                "</ul>",
            ])
            if storage.get("offline_schedule"):
                body.insert(-1, f"<li>{_text(storage['offline_schedule'])}</li>")
            disclosures = notice.get("feature_disclosures") or []
            if disclosures:
                body.extend([
                    "<h2>Deployment features</h2><ul>",
                    *[f"<li>{_text(item.get('text'))}</li>" for item in disclosures],
                    "</ul>",
                ])
        if section in {"privacy", "retention"}:
            body.extend(["<h2>Retention and deletion</h2>", _paragraphs(notice.get("retention_summary"))])
            retention = notice.get("retention") or {}
            if retention:
                body.extend([
                    '<table><caption>Controller-selected retention periods</caption><tbody>',
                    *[
                        f"<tr><th scope=\"row\">{_text(key.replace('_', ' ').title())}</th><td>{_text(value)}</td></tr>"
                        for key, value in retention.items()
                    ],
                    "</tbody></table>",
                ])
        if section in {"privacy", "rights"}:
            body.extend(["<h2>Your rights</h2>", _paragraphs(notice.get("rights_summary"))])
        if section in {"privacy", "processors"}:
            body.extend(["<h2>Processors and service providers</h2>", _paragraphs(notice.get("processor_summary"))])
            processors = notice.get("processors") or []
            if processors:
                body.extend([
                    "<ul>",
                    *[
                        f"<li><strong>{_text(item.get('display_name'))}</strong>: "
                        f"{_text(item.get('service'))}; {_text(item.get('public_notice_summary'))}; "
                        f"countries {_text(', '.join(item.get('hosting_countries') or []))}.</li>"
                        for item in processors
                    ],
                    "</ul>",
                ])
        if section in {"legal", "terms"}:
            body.extend(["<h2>Terms for this instance</h2>", _paragraphs(notice.get("terms_summary"))])
        if section == "rights":
            body.extend([
                "<h2>Supervisory authority</h2>",
                f"<p><a href=\"{_text(notice.get('supervisory_authority_url'))}\" rel=\"noopener noreferrer\">{_text(notice.get('supervisory_authority_name'))}</a></p>",
            ])
        if preview:
            body.append("<p>Private draft preview. No policy version or publication time has been assigned.</p>")
        else:
            body.append(
                f"<p>Published policy version {version} on {_text(published_at.date() if published_at else '')}.</p>"
            )
    if preview:
        preview_base = "/api/v1/admin/governance/preview"
        body.append(
            f'<nav aria-label="Draft legal centre"><a href="{preview_base}/privacy.html">Privacy</a> | '
            f'<a href="{preview_base}/legal.html">Legal</a> | <a href="{preview_base}/terms.html">Terms</a> | '
            f'<a href="{preview_base}/data-policy.html">Permitted data</a> | '
            f'<a href="{preview_base}/retention.html">Retention</a> | <a href="{preview_base}/rights.html">Rights</a> | '
            f'<a href="{preview_base}/processors.html">Processors</a></nav>'
        )
    else:
        body.append(
            '<nav aria-label="Legal centre"><a href="/privacy">Privacy</a> | '
            '<a href="/legal">Legal</a> | <a href="/terms">Terms</a> | <a href="/data-policy">Permitted data</a> | '
            '<a href="/retention">Retention</a> | <a href="/rights">Rights</a> | '
            '<a href="/processors">Processors</a> | <a href="/licence">Licence</a></nav>'
        )
    document = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{headings[section]} | Masterplan Optimiser</title></head>"
        f"<body><main>{''.join(body)}</main></body></html>"
    )
    headers = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow"} if preview else {"Cache-Control": "no-cache"}
    return HTMLResponse(document, headers=headers)


@public_router.get("/public/{section}.html", response_class=HTMLResponse)
def public_governance_html(section: str, db: Session = Depends(get_db)):
    """Render the current controller notice without requiring JavaScript."""

    publication = db.query(GovernancePublication).order_by(
        GovernancePublication.version.desc()
    ).first()
    return _render_governance_html(
        section,
        notice=json.loads(publication.content_json) if publication else None,
        version=publication.version if publication else None,
        published_at=publication.published_at if publication else None,
    )


@public_router.get("/public/versions/{version}/{section}.html", response_class=HTMLResponse)
def versioned_public_governance_html(
    version: int,
    section: str,
    db: Session = Depends(get_db),
):
    """Render an immutable policy publication at a permanent exact URL."""

    if section not in PUBLIC_SECTIONS:
        raise HTTPException(status_code=404, detail="Legal-centre section not found")
    publication = db.query(GovernancePublication).filter(
        GovernancePublication.version == version
    ).first()
    if publication is None:
        raise HTTPException(status_code=404, detail="Policy version not found")
    notice = json.loads(publication.content_json)
    body = [
        f"<h1>{_text(section.replace('-', ' ').title())}</h1>",
        f"<p>Version {version}; SHA-256 {_text(publication.content_sha256)}</p>",
    ]
    if section == "data-policy":
        permitted = notice.get("permitted_data", {})
        body.extend([
            f"<h2>Purpose</h2><p>{_text(permitted.get('purpose'))}</p>",
            "<h2>Allowed data</h2><ul>"
            + "".join(f"<li>{_text(item)}</li>" for item in permitted.get("allowed", []))
            + "</ul>",
            "<h2>Unsupported data</h2><ul>"
            + "".join(f"<li>{_text(item)}</li>" for item in permitted.get("unsupported", []))
            + "</ul>",
        ])
    else:
        body.append(f"<pre>{_text(json.dumps(notice, indent=2, sort_keys=True))}</pre>")
    document = "<!doctype html><html lang=\"en\"><body><main>" + "".join(body) + "</main></body></html>"
    return HTMLResponse(document, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@admin_router.get("")
def governance_draft(
    _root: User = Depends(require_root_admin_read_only),
    db: Session = Depends(get_db),
):
    """Return the local draft and publication status to the root operator."""

    profile = db.get(InstanceGovernanceProfile, 1)
    return {
        "configured": profile is not None,
        "draft": _draft_payload(profile) if profile else None,
        "published_version": current_policy_version(db),
        "preflight": _preflight(profile, db),
        "runtime_features": runtime_feature_state(),
    }


def _draft_payload(profile: InstanceGovernanceProfile) -> dict[str, object]:
    return {
        "controller_type": profile.controller_type,
        "controller_legal_name": profile.controller_legal_name,
        "controller_postal_address": profile.controller_postal_address,
        "controller_country": profile.controller_country,
        "privacy_contact_email": profile.privacy_contact_email,
        "privacy_contact_phone": profile.privacy_contact_phone,
        "dpo_contact": profile.dpo_contact,
        "supervisory_authority_name": profile.supervisory_authority_name,
        "supervisory_authority_url": profile.supervisory_authority_url,
        "default_locale": profile.default_locale,
        "processor_summary": profile.processor_summary,
        "retention_summary": profile.retention_summary,
        "rights_summary": profile.rights_summary,
        "terms_summary": profile.terms_summary,
        "structured": profile_structured(profile),
    }


def _preflight(profile: InstanceGovernanceProfile | None, db: Session) -> dict[str, object]:
    result = governance_preflight(profile)
    if settings.KEY_SEPARATION_ENFORCED:
        controller_key = db.query(EvidenceKey).filter(
            EvidenceKey.role == "controller",
            EvidenceKey.activated_at.isnot(None),
            EvidenceKey.revoked_at.is_(None),
            EvidenceKey.trust_declaration_sha256.isnot(None),
        ).first()
        result["checks"].append({
            "code": "controller_trust",
            "status": "ready" if controller_key else "missing",
            "message": (
                "External controller trust is active."
                if controller_key
                else "Register and activate an external controller key, then import its signed initial trust declaration before publication."
            ),
        })
        if controller_key is None:
            result["ready"] = False
    return result


@admin_router.get("/preview")
def preview_governance(
    _root: User = Depends(require_root_admin_read_only),
    db: Session = Depends(get_db),
):
    """Preview the exact draft, readiness result and diff without publishing."""

    profile = db.get(InstanceGovernanceProfile, 1)
    if profile is None:
        raise HTTPException(status_code=404, detail="Governance draft not found")
    payload = _profile_payload(profile)
    previous = db.query(GovernancePublication).order_by(GovernancePublication.version.desc()).first()
    return {
        "preflight": _preflight(profile, db),
        "preview": payload,
        "diff": publication_diff(json.loads(previous.content_json) if previous else None, payload),
        "published_version": previous.version if previous else None,
    }


@admin_router.get("/preview/{section}.html", response_class=HTMLResponse)
def preview_governance_html(
    section: str,
    _root: User = Depends(require_root_admin_read_only),
    db: Session = Depends(get_db),
):
    """Render the saved private draft for root review without publishing it."""

    profile = db.get(InstanceGovernanceProfile, 1)
    if profile is None:
        raise HTTPException(status_code=404, detail="Governance draft not found")
    return _render_governance_html(section, notice=_profile_payload(profile), preview=True)


@admin_router.get("/versions")
def governance_versions(
    _root: User = Depends(require_root_admin_read_only),
    db: Session = Depends(get_db),
):
    """List immutable publication metadata without returning draft content."""

    rows = db.query(GovernancePublication).order_by(GovernancePublication.version.desc()).all()
    return [{
        "version": row.version,
        "content_sha256": row.content_sha256,
        "source_sha256": row.source_sha256,
        "published_at": row.published_at,
        "published_by_user_id": row.published_by_id,
        "supersedes_version": row.supersedes_version,
        "material_change": row.material_change,
        "changes": json.loads(row.change_summary_json or "[]"),
    } for row in rows]


@admin_router.get("/export/{version}")
def export_governance_version(
    version: int,
    _root: User = Depends(require_root_admin_read_only),
    db: Session = Depends(get_db),
):
    """Download a non-secret evidence bundle for one immutable publication."""

    row = db.query(GovernancePublication).filter(GovernancePublication.version == version).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Policy version not found")
    bundle = {
        "version": row.version,
        "content_sha256": row.content_sha256,
        "source_sha256": row.source_sha256,
        "published_at": row.published_at.isoformat(),
        "published_by_user_id": row.published_by_id,
        "supersedes_version": row.supersedes_version,
        "material_change": row.material_change,
        "changes": json.loads(row.change_summary_json or "[]"),
        "source_configuration": json.loads(row.source_json or "{}"),
        "published_content": json.loads(row.content_json),
    }
    return Response(
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="governance-policy-v{version}.json"'},
    )


@admin_router.put("")
def save_governance_draft(
    body: GovernanceDraft,
    request: Request,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    """Save controller facts locally without publishing the draft."""

    profile = db.get(InstanceGovernanceProfile, 1)
    scalar = body.model_dump(mode="json", exclude={"structured"})
    structured_json = json.dumps(
        body.structured.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    if profile is None:
        profile = InstanceGovernanceProfile(
            id=1,
            instance_id=stable_instance_id(db),
            **scalar,
            structured_json=structured_json,
        )
        db.add(profile)
    else:
        for key, value in scalar.items():
            setattr(profile, key, value)
        profile.structured_json = structured_json
    audit(db, user=root, action="governance.draft_saved", resource_type="instance", request=request)
    db.commit()
    return {"status": "saved", "preflight": _preflight(profile, db)}


class PublicationConfirmation(BaseModel):
    authorised_to_configure: Literal[True]
    reviewed_generated_documents: Literal[True]
    confirmed_permitted_data_policy: Literal[True]
    understands_no_legal_certification: Literal[True]


@admin_router.post("/publish")
def publish_governance(
    body: PublicationConfirmation,
    request: Request,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    """Publish an immutable, hash-addressed version of the current draft."""

    lock_evidence_transaction(db)
    if settings.KEY_SEPARATION_ENFORCED:
        controller_key = db.query(EvidenceKey).filter(
            EvidenceKey.role == "controller",
            EvidenceKey.activated_at.isnot(None),
            EvidenceKey.revoked_at.is_(None),
            EvidenceKey.trust_declaration_sha256.isnot(None),
        ).first()
        if controller_key is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "controller_trust_required",
                    "message": "Complete the external controller-key ceremony and import its signed initial trust declaration before publishing controller-specific governance or privacy notices.",
                },
            )
    profile = db.get(InstanceGovernanceProfile, 1)
    preflight = _preflight(profile, db)
    if not preflight["ready"] or profile is None:
        raise HTTPException(status_code=409, detail={"code": "governance_preflight_failed", **preflight})
    payload = _profile_payload(profile)
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    source_snapshot = _draft_payload(profile)
    source_canonical = json.dumps(
        source_snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    source_digest = hashlib.sha256(source_canonical.encode("utf-8")).hexdigest()
    previous = db.query(GovernancePublication).order_by(GovernancePublication.version.desc()).first()
    if previous and previous.content_sha256 == digest and previous.source_sha256 == source_digest:
        return {
            "status": "unchanged", "version": previous.version,
            "content_sha256": digest, "source_sha256": source_digest,
        }
    version = (previous.version if previous else 0) + 1
    public_diff = publication_diff(json.loads(previous.content_json) if previous else None, payload)
    source_diff = publication_diff(json.loads(previous.source_json) if previous else None, source_snapshot)
    diff = {
        "material_change": public_diff["material_change"] or source_diff["material_change"],
        "changes": public_diff["changes"] + [
            {**change, "path": f"source.{change['path']}"}
            for change in source_diff["changes"]
        ],
    }
    now = datetime.now(timezone.utc)
    if diff["material_change"]:
        db.query(DataPolicyAcknowledgement).filter(
            DataPolicyAcknowledgement.superseded_at.is_(None)
        ).update({"superseded_at": now}, synchronize_session=False)
    publication = GovernancePublication(
        version=version,
        content_json=canonical,
        content_sha256=digest,
        source_json=source_canonical,
        source_sha256=source_digest,
        published_by_id=root.id,
        published_at=now,
        supersedes_version=previous.version if previous else None,
        material_change=diff["material_change"],
        change_summary_json=json.dumps(diff["changes"], ensure_ascii=False, separators=(",", ":")),
    )
    db.add(publication)
    db.flush()
    db.add(DataPolicyAcknowledgement(
        user_id=root.id, event_id=None, policy_version=version,
        policy_sha256=digest, scope="instance_root",
    ))
    audit(db, user=root, action="governance.published", resource_type="governance_publication",
          resource_id=publication.id, detail=json.dumps({"version": version, "sha256": digest}), request=request)
    db.commit()
    return {
        "status": "published", "version": version, "content_sha256": digest,
        "source_sha256": source_digest,
        "material_change": diff["material_change"], "changes": diff["changes"],
    }


class EventGovernanceOverrideIn(BaseModel):
    controller_override_enabled: bool = False
    controller_identity_override: str | None = Field(default=None, max_length=200)
    privacy_contact_override: EmailStr | None = None
    retention_override_days: int | None = Field(default=None, ge=1, le=3650)
    enabled_optional_features: list[Literal[
        "activation_email", "push_notifications", "offline_schedule", "public_schedule", "external_support"
    ]] = Field(default_factory=list, max_length=5)


EVENT_OPTIONAL_FEATURES = frozenset({
    "activation_email", "push_notifications", "offline_schedule", "public_schedule", "external_support",
})


def _published_event_features(publication: GovernancePublication) -> set[str]:
    notice = json.loads(publication.content_json)
    return {
        item.get("code")
        for item in notice.get("feature_disclosures", [])
        if item.get("code") in EVENT_OPTIONAL_FEATURES
    }


def _event_governance_payload(
    event: Event,
    override: EventGovernanceOverride | None,
    publication: GovernancePublication,
    *,
    include_event_name: bool = False,
) -> dict[str, object]:
    notice = json.loads(publication.content_json)
    retention = notice.get("retention") or {}
    allowed_event_features = _published_event_features(publication)
    payload = {
        "event_id": event.id,
        "policy_version": publication.version,
        "policy_sha256": publication.content_sha256,
        "controller_identity": (
            override.controller_identity_override
            if override and override.controller_override_enabled
            else notice.get("controller_legal_name")
        ),
        "privacy_contact": (
            override.privacy_contact_override
            if override and override.controller_override_enabled
            else notice.get("privacy_contact_email")
        ),
        "retention_days": (
            override.retention_override_days
            if override and override.retention_override_days is not None
            else retention.get("event_grace_days")
        ),
        "enabled_optional_features": (
            sorted(set(json.loads(override.enabled_optional_features_json)) & allowed_event_features)
            if override else sorted(allowed_event_features)
        ),
        "controller_override_enabled": bool(override and override.controller_override_enabled),
        "data_policy_url": f"/api/v1/governance/public/versions/{publication.version}/data-policy.html",
        "privacy_url": f"/api/v1/governance/public/versions/{publication.version}/privacy.html",
    }
    if include_event_name:
        payload["event_name"] = event.name
    return payload


@public_router.get("/public/events/{event_id}")
def public_event_governance(event_id: int, db: Session = Depends(get_db)):
    """Return the exact policy layer applicable to one event."""

    event = db.get(Event, event_id)
    publication = db.query(GovernancePublication).order_by(GovernancePublication.version.desc()).first()
    if event is None or publication is None:
        raise HTTPException(status_code=404, detail="Published event governance not found")
    override = db.get(EventGovernanceOverride, event_id)
    return _event_governance_payload(event, override, publication)


@admin_router.get("/events/{event_id}")
def event_governance_override(
    event_id: int,
    _root: User = Depends(require_root_admin_read_only),
    db: Session = Depends(get_db),
):
    """Return the effective event layer and any root-reviewed override."""

    event = db.get(Event, event_id)
    publication = db.query(GovernancePublication).order_by(GovernancePublication.version.desc()).first()
    if event is None or publication is None:
        raise HTTPException(status_code=404, detail="Published event governance not found")
    return _event_governance_payload(
        event, db.get(EventGovernanceOverride, event_id), publication,
        include_event_name=True,
    )


@admin_router.put("/events/{event_id}")
def save_event_governance_override(
    event_id: int,
    body: EventGovernanceOverrideIn,
    request: Request,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    """Save a bounded event layer; a different controller always needs root review."""

    event = db.get(Event, event_id)
    identity = current_policy_identity(db)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if identity is None:
        raise HTTPException(status_code=409, detail="Publish instance governance before configuring an event layer")
    publication = db.query(GovernancePublication).filter(
        GovernancePublication.version == identity[0]
    ).first()
    if publication is None:
        raise HTTPException(status_code=409, detail="Published instance governance is unavailable")
    disabled_features = sorted(
        set(body.enabled_optional_features) - _published_event_features(publication)
    )
    if disabled_features:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "event_governance_feature_disabled",
                "disabled_features": disabled_features,
                "enabled_features": sorted(_published_event_features(publication)),
            },
        )
    if body.controller_override_enabled and not (
        body.controller_identity_override and body.privacy_contact_override
    ):
        raise HTTPException(
            status_code=422,
            detail="A different event controller requires its identity and privacy contact.",
        )
    row = db.get(EventGovernanceOverride, event_id)
    if row is None:
        row = EventGovernanceOverride(event_id=event_id, policy_version=identity[0])
        db.add(row)
    row.controller_override_enabled = body.controller_override_enabled
    row.controller_identity_override = body.controller_identity_override if body.controller_override_enabled else None
    row.privacy_contact_override = str(body.privacy_contact_override) if body.controller_override_enabled else None
    row.retention_override_days = body.retention_override_days
    row.enabled_optional_features_json = json.dumps(sorted(set(body.enabled_optional_features)))
    row.policy_version = identity[0]
    row.updated_by_id = root.id
    audit(
        db, user=root, action="governance.event_override_saved", resource_type="event",
        resource_id=event_id, detail=json.dumps({"policy_version": identity[0]}), request=request,
    )
    db.commit()
    return _event_governance_payload(event, row, publication, include_event_name=True)


class PolicyAcknowledgementIn(BaseModel):
    event_id: int
    scope: Literal["event_creator", "head_organiser", "authorised_editor", "field_visibility_administrator"]
    policy_version: int = Field(..., ge=1)
    policy_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")


@user_router.post("/data-policy/acknowledge")
def acknowledge_data_policy(
    body: PolicyAcknowledgementIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Record a user's current permitted-data acknowledgement locally."""

    if not user.is_root_admin and user.event_id != body.event_id:
        raise HTTPException(status_code=403, detail="No access to this event")
    permitted_scope = (
        user.is_root_admin
        or (body.scope == "head_organiser" and user.is_issuer)
        or (body.scope == "authorised_editor" and user.can_edit)
        or (body.scope == "field_visibility_administrator" and user.is_admin)
    )
    if not permitted_scope:
        raise HTTPException(status_code=403, detail="The acknowledgement scope does not match your role")
    version, digest = require_current_policy_identity(
        body.policy_version, body.policy_sha256, db
    )
    existing = db.query(DataPolicyAcknowledgement).filter(
        DataPolicyAcknowledgement.user_id == user.id,
        DataPolicyAcknowledgement.event_id == body.event_id,
        DataPolicyAcknowledgement.policy_version == version,
        DataPolicyAcknowledgement.policy_sha256 == digest,
        DataPolicyAcknowledgement.scope == body.scope,
    ).first()
    if existing is None:
        db.add(DataPolicyAcknowledgement(
            user_id=user.id, event_id=body.event_id, policy_version=version,
            policy_sha256=digest, scope=body.scope,
        ))
        audit(db, user=user, action="governance.data_policy_acknowledged",
              resource_type="event", resource_id=body.event_id,
              detail=json.dumps({"policy_version": version, "policy_sha256": digest, "scope": body.scope}), request=request)
        db.commit()
    return {"acknowledged": True, "policy_version": version, "policy_sha256": digest, "scope": body.scope}
