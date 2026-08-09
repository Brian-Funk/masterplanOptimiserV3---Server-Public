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
from app.core import runtime_settings
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

NOTICE_STYLES = """
@font-face{font-family:'Source Sans 3';font-style:normal;font-display:swap;font-weight:400;src:url('/fonts/source-sans-3-latin-400-normal.woff2') format('woff2')}
@font-face{font-family:'Source Sans 3';font-style:italic;font-display:swap;font-weight:400;src:url('/fonts/source-sans-3-latin-400-italic.woff2') format('woff2')}
@font-face{font-family:'Source Sans 3';font-style:normal;font-display:swap;font-weight:600;src:url('/fonts/source-sans-3-latin-600-normal.woff2') format('woff2')}
@font-face{font-family:'Source Sans 3';font-style:normal;font-display:swap;font-weight:700;src:url('/fonts/source-sans-3-latin-700-normal.woff2') format('woff2')}
:root{color-scheme:light dark;--bg:#f7f8fa;--surface:#fff;--surface-soft:#f8fafc;--surface-muted:#f1f3f5;--text:#111827;--muted:#667085;--line:#e4e7ec;--blue:#2563eb;--blue-soft:#eff6ff;--violet:#7c3aed;--good:#047857;--good-soft:#ecfdf5;--warn:#b45309;--warn-soft:#fffbeb;--danger:#b42318;--danger-soft:#fff5f5;--shadow:0 1px 3px rgba(16,24,40,.08)}
*{box-sizing:border-box}html{background:var(--bg)}body{min-width:320px;margin:0;background:var(--bg);color:var(--text);font-family:"Source Sans 3",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;line-height:1.6;-webkit-font-smoothing:antialiased}
a{color:var(--blue);text-underline-offset:3px}a:hover{text-decoration-thickness:2px}a:focus-visible{outline:2px solid var(--blue);outline-offset:3px;border-radius:4px}main{width:min(1024px,calc(100% - 32px));margin:0 auto;padding:32px 0 64px}
.notice-shell{background:var(--surface);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);overflow:hidden}.notice-header{position:relative;padding:28px 36px 30px;border-top:4px solid transparent;border-bottom:1px solid var(--line);border-image:linear-gradient(90deg,var(--blue),var(--violet)) 1;background:var(--surface)}
.notice-brand{display:flex;align-items:center;gap:12px;margin-bottom:26px}.notice-logo{width:46px;height:46px;object-fit:contain}.notice-brand-copy{min-width:0}.notice-product{display:block;font-size:.98rem;font-weight:700;color:var(--text)}.notice-instance{display:block;overflow:hidden;color:var(--muted);font-size:.82rem;text-overflow:ellipsis;white-space:nowrap}
.notice-kicker{margin:0 0 6px;color:var(--blue);font-size:.74rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase}.notice-header h1{max-width:760px;margin:0;font-size:clamp(2rem,4vw,2.75rem);line-height:1.12;letter-spacing:-.03em}.notice-lead{max-width:720px;margin:12px 0 0;color:var(--muted);font-size:1.04rem}
.notice-meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:20px}.notice-meta span,.notice-meta code{display:inline-flex;align-items:center;min-height:29px;padding:4px 9px;border:1px solid var(--line);border-radius:8px;background:var(--surface-soft);color:var(--muted);font-size:.77rem}.notice-meta code{max-width:100%;overflow-wrap:anywhere;font-family:ui-monospace,"SFMono-Regular",Consolas,monospace}
.notice-nav{display:flex;flex-wrap:wrap;gap:5px;padding:12px 28px;border-bottom:1px solid var(--line);background:var(--surface-soft)}.notice-nav a{padding:7px 10px;border-radius:8px;color:var(--muted);text-decoration:none;font-size:.87rem;font-weight:600}.notice-nav a:hover{background:var(--surface);color:var(--text)}.notice-nav a[aria-current="page"]{background:var(--blue-soft);color:#1d4ed8}
.notice-content{padding:8px 36px 40px}.notice-content h2{margin:32px 0 10px;padding-top:28px;border-top:1px solid var(--line);font-size:1.25rem;line-height:1.3;letter-spacing:-.01em}.notice-content h2:first-child{border-top:0}.notice-content p{max-width:78ch;margin:8px 0;color:var(--muted)}.notice-content strong{color:var(--text)}
.notice-content ul,.notice-content ol{margin:12px 0;padding-left:22px}.notice-content li{margin:7px 0;color:var(--muted)}.notice-content li::marker{color:var(--blue)}
.notice-callout{margin:18px 0;padding:16px 18px;border:1px solid var(--line);border-radius:12px;background:var(--surface-soft)}.notice-callout p:first-child{margin-top:0}.notice-callout p:last-child{margin-bottom:0}.notice-callout--good{border-color:#a7f3d0;background:var(--good-soft)}.notice-callout--warn{border-color:#fde68a;background:var(--warn-soft)}
.rights-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:0!important;list-style:none}.rights-grid li{display:flex;min-height:154px;margin:0;padding:17px;border:1px solid var(--line);border-radius:12px;background:var(--surface)}.rights-card{display:flex;min-width:0;flex-direction:column}.rights-card>strong{display:block;margin-bottom:5px;font-size:1.02rem}.rights-card>span{color:var(--muted)}.rights-card__refs{display:flex;flex-wrap:wrap;gap:6px;margin-top:auto;padding-top:14px}.rights-card__refs a{padding:3px 7px;border-radius:6px;background:var(--blue-soft);font-size:.75rem;font-weight:700;text-decoration:none}.rights-note{font-size:.9rem}
.notice-actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}.notice-button{display:inline-flex;align-items:center;justify-content:center;min-height:40px;padding:8px 14px;border-radius:9px;background:var(--blue);color:#fff;text-decoration:none;font-weight:700}.notice-button:hover{color:#fff;filter:brightness(.96)}.notice-button--secondary{border:1px solid var(--line);background:var(--surface);color:var(--text)}.notice-button--secondary:hover{color:var(--text);background:var(--surface-soft)}
table{width:100%;margin:16px 0;border-collapse:separate;border-spacing:0;border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:.9rem}caption{padding:0 0 10px;text-align:left;font-weight:700;color:var(--text)}th,td{padding:11px 13px;border-top:1px solid var(--line);text-align:left;vertical-align:top}tr:first-child th,tr:first-child td{border-top:0}th{width:46%;background:var(--surface-soft);font-weight:650}td{color:var(--muted)}
.notice-footer{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;padding:18px 36px;border-top:1px solid var(--line);background:var(--surface-soft);color:var(--muted);font-size:.78rem}.notice-footer a{color:var(--muted)}
@media(max-width:640px){main{width:min(100% - 20px,1024px);padding:12px 0 34px}.notice-shell{border-radius:14px}.notice-header{padding:24px 20px}.notice-brand{margin-bottom:22px}.notice-content{padding:6px 20px 30px}.notice-nav{padding:10px 14px}.notice-footer{padding:16px 20px}.rights-grid{grid-template-columns:1fr}.rights-grid li{min-height:0}th{width:42%}}
@media(prefers-color-scheme:dark){:root{--bg:#22252a;--surface:#282c34;--surface-soft:#2d333d;--surface-muted:#343a45;--text:#e5e7eb;--muted:#abb2bf;--line:#3d434f;--blue:#93c5fd;--blue-soft:#243652;--violet:#c4b5fd;--good-soft:#183c31;--warn-soft:#44341e;--danger-soft:#452526;--shadow:none}.notice-nav a[aria-current="page"]{color:#bfdbfe}.rights-card__refs a{color:#bfdbfe}.notice-button{background:#3b82f6;color:#fff}}
"""


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
    controller_postal_address: str = Field(default="", max_length=500)
    controller_country: str = Field(default="", max_length=2)
    privacy_contact_email: EmailStr
    privacy_contact_phone: str | None = Field(default=None, max_length=64)
    dpo_contact: str | None = Field(default=None, max_length=320)
    supervisory_authority_name: str = Field(default="", max_length=200)
    supervisory_authority_url: HttpUrl | Literal[""] = ""
    default_locale: str = Field(default="en", pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    processor_summary: str = Field(default="", max_length=4000)
    retention_summary: str = Field(default="", max_length=4000)
    rights_summary: str = Field(default="", max_length=4000)
    terms_summary: str = Field(default="", max_length=4000)
    structured: GovernanceStructuredIn

    @field_validator("controller_country")
    @classmethod
    def normalise_country(cls, value: str) -> str:
        normalised = value.strip().upper()
        if normalised and (len(normalised) != 2 or not normalised.isalpha()):
            raise ValueError("country must be empty or use a two-letter code")
        return normalised


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
    content_sha256: str | None = None,
    preview: bool = False,
    navigation_base: str | None = None,
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
    leads = {
        "privacy": "How this deployment uses, protects, retains and shares operational information.",
        "legal": "The controller and contact details for this self-hosted deployment.",
        "terms": "The operating terms that apply to this deployment.",
        "data-policy": "The exact boundary between supported operational information and unsupported sensitive data.",
        "retention": "The controller-selected periods and deletion approach for this deployment.",
        "rights": "How to exercise data-protection rights with the controller of this deployment.",
        "processors": "The controller-declared providers and processing locations used by this deployment.",
    }
    instance_name = _text((notice or {}).get("instance_name") or "Self-hosted instance")
    meta: list[str] = []
    if preview:
        meta.append("<span>Private draft</span>")
    elif version is not None:
        meta.append(f"<span>Policy version {version}</span>")
    if published_at is not None:
        meta.append(f"<span>Published {_text(published_at.date())}</span>")
    if content_sha256:
        meta.append(f"<code>SHA-256 {_text(content_sha256)}</code>")
    header = (
        '<header class="notice-header">'
        '<div class="notice-brand">'
        '<picture><source media="(prefers-color-scheme: dark)" srcset="/logo_dark.svg">'
        '<img class="notice-logo" src="/logo_normal.png" width="46" height="46" '
        'alt="Masterplan Optimiser"></picture>'
        '<div class="notice-brand-copy"><span class="notice-product">Masterplan Optimiser</span>'
        f'<span class="notice-instance">{instance_name}</span></div></div>'
        '<p class="notice-kicker">Published governance</p>'
        f'<h1>{headings[section]}</h1>'
        f'<p class="notice-lead">{leads[section]}</p>'
        + (f'<div class="notice-meta">{"".join(meta)}</div>' if meta else "")
        + "</header>"
    )
    body: list[str] = []
    if preview:
        body.append(
            '<div class="notice-callout notice-callout--warn"><strong>Private draft preview &mdash; not published.</strong> '
            "Preview markers and publication metadata are not part of the final public notice.</div>"
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
                f"<p>Email: <a href=\"mailto:{_text(notice.get('privacy_contact_email'))}\">{_text(notice.get('privacy_contact_email'))}</a></p>",
            ])
            if notice.get("controller_postal_address"):
                body.append(_paragraphs(notice.get("controller_postal_address")))
            if notice.get("controller_country"):
                body.append(f"<p>Country: {_text(notice.get('controller_country'))}</p>")
            if notice.get("privacy_contact_phone"):
                body.append(f"<p>Telephone: {_text(notice['privacy_contact_phone'])}</p>")
            if notice.get("dpo_contact"):
                body.append(f"<p>Data-protection contact: {_text(notice['dpo_contact'])}</p>")
        if section in {"privacy", "data-policy"}:
            policy = notice.get("permitted_data") or {}
            body.extend([
                "<h2>Purpose and permitted information</h2>",
                f"<p>{_text(policy.get('purpose'))}. Optional data must be necessary for that purpose.</p>",
                f'<div class="notice-callout notice-callout--good"><strong>Normally permitted</strong><p>{_text(", ".join(policy.get("allowed") or []))}.</p></div>',
                f'<div class="notice-callout notice-callout--warn"><strong>Not supported</strong><p>{_text(", ".join(policy.get("unsupported") or []))}.</p></div>',
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
            body.append("<h2>Retention and deletion</h2>")
            if notice.get("retention_summary"):
                body.append(_paragraphs(notice.get("retention_summary")))
            retention = notice.get("retention") or {}
            if retention:
                body.extend([
                    '<table><caption>Controller-selected retention periods</caption><tbody>',
                    *[
                        f"<tr><th scope=\"row\">{_text(key.replace('_', ' ').title())}</th><td>{_text(value)}</td></tr>"
                        for key, value in retention.items() if value is not None
                    ],
                    "</tbody></table>",
                ])
        if section in {"privacy", "rights"}:
            body.extend([
                "<h2>Your rights</h2>",
                "<p>Whether you are a participant, organiser, administrator or another account holder, "
                "data-protection rights concern your own personal data. Depending on the law that applies, "
                "you may ask to access, correct, erase, restrict or object to processing, and to receive eligible data "
                "in a portable form. Contact the controller at "
                f"<a href=\"mailto:{_text(notice.get('privacy_contact_email'))}\">{_text(notice.get('privacy_contact_email'))}</a>; "
                "the controller will assess the applicable right, scope and proportionate identity verification.</p>",
            ])
            if section == "rights":
                gdpr_url = "https://eur-lex.europa.eu/eli/reg/2016/679/2016-05-04"
                fadp_url = "https://www.fedlex.admin.ch/eli/cc/2022/491/en"
                body.extend([
                    '<div class="notice-callout"><strong>You can ask in ordinary language.</strong>'
                    '<p>You do not need to identify the applicable law or quote an article. Describe what you want '
                    'to know or change, and the controller will assess the request. These rights do not give an organiser '
                    "access to another person's information or to all operational records.</p></div>",
                ])
                if notice.get("jurisdiction_scope"):
                    body.extend([
                        "<h2>Applicable scope</h2>",
                        f"<p>The controller recorded the following jurisdiction scope for this deployment: {_text(notice.get('jurisdiction_scope'))}</p>",
                        "<p>The Swiss FADP, the EU GDPR, or both may apply. The controller must assess the law for the particular request; "
                        "the software does not infer jurisdiction from hosting, network location or a person's account role.</p>",
                    ])
                body.extend([
                    "<h2>Rights you may exercise</h2>",
                    '<ul class="rights-grid">',
                    '<li><div class="rights-card"><strong>Access</strong><span>Ask whether personal data about you is processed and request the data and relevant processing information.</span>'
                    f'<div class="rights-card__refs"><a href="{gdpr_url}" rel="noopener noreferrer">GDPR Art. 15</a><a href="{fadp_url}" rel="noopener noreferrer">FADP Art. 25</a></div></div></li>',
                    '<li><div class="rights-card"><strong>Correction</strong><span>Ask for inaccurate personal data to be corrected and incomplete data to be completed where appropriate.</span>'
                    f'<div class="rights-card__refs"><a href="{gdpr_url}" rel="noopener noreferrer">GDPR Art. 16</a><a href="{fadp_url}" rel="noopener noreferrer">FADP Art. 32(1)</a></div></div></li>',
                    '<li><div class="rights-card"><strong>Erasure</strong><span>Ask for deletion or destruction where the applicable legal conditions are met. This right is not unconditional.</span>'
                    f'<div class="rights-card__refs"><a href="{gdpr_url}" rel="noopener noreferrer">GDPR Art. 17</a><a href="{fadp_url}" rel="noopener noreferrer">FADP Art. 32(2)(c)</a></div></div></li>',
                    '<li><div class="rights-card"><strong>Restriction</strong><span>Ask for disputed or potentially unlawful processing to be limited while the matter is assessed.</span>'
                    f'<div class="rights-card__refs"><a href="{gdpr_url}" rel="noopener noreferrer">GDPR Art. 18</a><a href="{fadp_url}" rel="noopener noreferrer">FADP Art. 32(2)(b)</a></div></div></li>',
                    '<li><div class="rights-card"><strong>Objection</strong><span>Object to processing of your personal data. The controller must assess whether it may lawfully continue.</span>'
                    f'<div class="rights-card__refs"><a href="{gdpr_url}" rel="noopener noreferrer">GDPR Art. 21</a><a href="{fadp_url}" rel="noopener noreferrer">FADP Arts. 30(2)(b), 32</a></div></div></li>',
                    '<li><div class="rights-card"><strong>Portability</strong><span>Ask for qualifying data in a commonly used, machine-readable format where the statutory conditions apply.</span>'
                    f'<div class="rights-card__refs"><a href="{gdpr_url}" rel="noopener noreferrer">GDPR Art. 20</a><a href="{fadp_url}" rel="noopener noreferrer">FADP Art. 28</a></div></div></li>',
                    '<li><div class="rights-card"><strong>Automated decisions</strong><span>Ask for the applicable safeguards or human review of a solely automated decision that significantly affects you.</span>'
                    f'<div class="rights-card__refs"><a href="{gdpr_url}" rel="noopener noreferrer">GDPR Art. 22</a><a href="{fadp_url}" rel="noopener noreferrer">FADP Art. 21</a></div></div></li>',
                    "</ul>",
                    '<p class="rights-note">The precise scope, exceptions and response depend on the applicable law, the processing purpose, '
                    "other people's rights, and any legally required retention.</p>",
                ])
            if notice.get("rights_summary"):
                body.append(_paragraphs(notice.get("rights_summary")))
            if section == "rights":
                body.extend([
                    "<h2>How to make a request</h2>",
                    '<div class="notice-callout"><p>Contact the controller using the privacy address below. You may write in ordinary language; '
                    "describe what you want to know or change. Do not email unnecessary identity documents or sensitive information.</p>"
                    f'<div class="notice-actions"><a class="notice-button" href="mailto:{_text(notice.get("privacy_contact_email"))}">Email the privacy contact</a></div></div>',
                    "<h2>What happens next</h2>",
                    "<ol><li>The controller confirms the scope of the request.</li>"
                    "<li>Only proportionate identity verification is requested where needed.</li>"
                    "<li>The controller assesses the applicable law, any limits, and the response or action required.</li></ol>",
                    f'<p>Under <a href="{gdpr_url}" rel="noopener noreferrer">GDPR Article 12</a>, a controller normally '
                    'responds without undue delay and within one month. Under the '
                    '<a href="https://www.fedlex.admin.ch/eli/cc/2022/568/en" rel="noopener noreferrer">Swiss Data Protection Ordinance</a>, '
                    "access information is normally provided within 30 days.</p>",
                ])
                if notice.get("rights_request_url"):
                    body.append(
                        f'<p><a class="notice-button" href="{_text(notice.get("rights_request_url"))}" '
                        'rel="noopener noreferrer">Open the rights-request form</a></p>'
                    )
        if section in {"privacy", "processors"}:
            body.append("<h2>Processors and service providers</h2>")
            if notice.get("processor_summary"):
                body.append(_paragraphs(notice.get("processor_summary")))
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
            body.extend([
                "<h2>Terms for this instance</h2>",
                "<p>Use is limited to authorised operational event scheduling and access management. "
                "Users must follow the permitted-data boundary and protect their own account access.</p>",
            ])
            if notice.get("terms_summary"):
                body.append(_paragraphs(notice.get("terms_summary")))
        if section == "rights":
            body.extend([
                "<h2>Complaint and legal remedy</h2>",
                '<p>If the controller does not handle a request properly, the applicable route may include a complaint to a supervisory authority '
                "or a legal remedy. See <a href=\"https://eur-lex.europa.eu/eli/reg/2016/679/2016-05-04\" rel=\"noopener noreferrer\">GDPR Articles 77 and 79</a> "
                'and <a href="https://www.fedlex.admin.ch/eli/cc/2022/491/en" rel="noopener noreferrer">FADP Articles 32 and 49 onward</a>.</p>',
                "<h2>Supervisory authority</h2>",
            ])
            if notice.get("supervisory_authority_name") and notice.get("supervisory_authority_url"):
                body.append(f"<p><a href=\"{_text(notice.get('supervisory_authority_url'))}\" rel=\"noopener noreferrer\">{_text(notice.get('supervisory_authority_name'))}</a></p>")
            elif notice.get("supervisory_authority_name"):
                body.append(f"<p>{_text(notice.get('supervisory_authority_name'))}</p>")
            else:
                body.append("<p>Where applicable, you may lodge a complaint with the competent data-protection supervisory authority.</p>")
        if preview:
            body.append("<p>Private draft preview. No policy version or publication time has been assigned.</p>")
    nav_labels = {
        "privacy": "Privacy",
        "legal": "Legal",
        "terms": "Terms",
        "data-policy": "Permitted data",
        "retention": "Retention",
        "rights": "Rights",
        "processors": "Processors",
    }
    def nav_link(href: str, item: str, label: str) -> str:
        current = ' aria-current="page"' if item == section else ""
        return f'<a href="{href}"{current}>{label}</a>'

    if preview:
        nav_base = "/api/v1/admin/governance/preview"
        nav_label = "Draft legal centre"
        nav_links = [nav_link(f"{nav_base}/{item}.html", item, label) for item, label in nav_labels.items()]
    elif navigation_base:
        nav_label = "Published policy sections"
        nav_links = [nav_link(f"{navigation_base}/{item}.html", item, label) for item, label in nav_labels.items()]
    else:
        nav_label = "Legal centre"
        nav_links = [nav_link(f"/{item}", item, label) for item, label in nav_labels.items()]
        nav_links.append('<a href="/licence">Licence</a>')
    navigation = f'<nav class="notice-nav" aria-label="{nav_label}">{"".join(nav_links)}</nav>'
    footer = (
        '<footer class="notice-footer"><span>Masterplan Optimiser self-hosted instance</span>'
        '<span>Controller-specific published governance</span></footer>'
    )
    document = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{headings[section]} | {instance_name}</title><style>{NOTICE_STYLES}</style></head>"
        f'<body><main><article class="notice-shell">{header}{navigation}'
        f'<div class="notice-content">{"".join(body)}</div>{footer}</article></main></body></html>'
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
        content_sha256=publication.content_sha256 if publication else None,
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
    response = _render_governance_html(
        section,
        notice=json.loads(publication.content_json),
        version=publication.version,
        published_at=publication.published_at,
        content_sha256=publication.content_sha256,
        navigation_base=f"/api/v1/governance/public/versions/{publication.version}",
    )
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


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
        "runtime_settings": runtime_settings.get_all(db),
        "runtime_impact": runtime_settings.governance_impact(db),
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
            EvidenceKey.trust_establishment_sha256.isnot(None),
        ).first()
        result["checks"].append({
            "code": "controller_trust",
            "status": "ready" if controller_key else "missing",
            "message": (
                "External controller trust is active."
                if controller_key
                else "Complete controller identity setup before publication."
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
    structured, runtime_changes = runtime_settings.apply_governance_runtime_values(
        body.structured.model_dump(mode="json"), db
    )
    structured_json = json.dumps(structured, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
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
    return {
        "status": "saved",
        "preflight": _preflight(profile, db),
        "draft": _draft_payload(profile),
        "runtime_enforced_changes": runtime_changes,
        "runtime_impact": runtime_settings.governance_impact(db),
    }


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
            EvidenceKey.trust_establishment_sha256.isnot(None),
        ).first()
        if controller_key is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "controller_trust_required",
                    "message": "Complete controller identity setup before publishing controller-specific governance or privacy notices.",
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
    runtime_settings.clear_governance_impact(db)
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


@public_router.get("/public/events/{event_id}/privacy.html", response_class=HTMLResponse)
def public_event_privacy_details(event_id: int, db: Session = Depends(get_db)):
    """Render the effective event controller layer without exposing private facts."""

    event = db.get(Event, event_id)
    publication = db.query(GovernancePublication).order_by(
        GovernancePublication.version.desc()
    ).first()
    if event is None or publication is None:
        raise HTTPException(status_code=404, detail="Published event governance not found")
    payload = _event_governance_payload(
        event,
        db.get(EventGovernanceOverride, event_id),
        publication,
    )
    version_base = f"/api/v1/governance/public/versions/{publication.version}"
    document = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Event privacy details</title></head><body><main>'
        '<h1>Event privacy details</h1>'
        '<p>This event uses the following reviewed controller and contact layer.</p>'
        f'<p><strong>Controller:</strong> {_text(payload.get("controller_identity"))}</p>'
        f'<p><strong>Privacy contact:</strong> <a href="mailto:{_text(payload.get("privacy_contact"))}">'
        f'{_text(payload.get("privacy_contact"))}</a></p>'
        f'<p>Underlying published policy: version {publication.version}; '
        f'SHA-256 {_text(publication.content_sha256)}.</p>'
        f'<nav aria-label="Published privacy information"><a href="{version_base}/privacy.html">Privacy notice</a> | '
        f'<a href="{version_base}/rights.html">Your rights</a> | '
        f'<a href="{version_base}/processors.html">Processors</a></nav>'
        '</main></body></html>'
    )
    return HTMLResponse(
        document,
        headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex, nofollow"},
    )


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
