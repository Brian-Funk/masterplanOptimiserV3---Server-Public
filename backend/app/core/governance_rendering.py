"""Deterministic live-governance rendering from controller-supplied facts."""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import settings
from app.models.governance import InstanceGovernanceProfile


POLICY_TEMPLATE_VERSION = "2026-07-31"
MATERIAL_PATH_PREFIXES = (
    "controller_type",
    "controller_legal_name",
    "controller_postal_address",
    "controller_country",
    "dpo_",
    "eu_representative",
    "swiss_representative",
    "processing_purposes",
    "structured.processing_purposes",
    "data_categories",
    "structured.data_categories",
    "processors",
    "structured.processors",
    "hosting_countries",
    "structured.hosting_countries",
    "optional_features",
    "structured.optional_features",
    "retention.live_retention_days",
    "retention.event_grace_days",
    "retention.backup_retention_days",
    "jurisdiction_scope",
    "structured.jurisdiction_scope",
)
_PLACEHOLDER = re.compile(
    r"(?:\b(?:todo|tbd|placeholder|example\.(?:com|org)|change me|insert here)\b|<[^>]+>)",
    re.IGNORECASE,
)


def profile_structured(profile: InstanceGovernanceProfile) -> dict[str, Any]:
    """Decode the validated extension document without exposing parse failures."""

    try:
        value = json.loads(profile.structured_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def runtime_feature_state() -> dict[str, bool | str]:
    """Return non-secret deployment facts that policy declarations must match."""

    return {
        "smtp_enabled": all(
            (settings.SMTP_HOST, settings.SMTP_USERNAME, settings.SMTP_TOKEN, settings.SMTP_FROM_EMAIL)
        ),
        "push_enabled": bool(settings.VAPID_PRIVATE_KEY and settings.VAPID_CLAIMS_EMAIL),
        "ha_enabled": settings.HA_MODE == "ha",
        # The supported production architecture uses Cloudflare for DNS and
        # health selection, not TLS termination or application-content proxying.
        "dns_mode": "dns_only",
    }


def _public_processor(entry: dict[str, Any]) -> dict[str, Any]:
    """Drop root-only contract references and review notes from public output."""

    return {
        key: entry.get(key)
        for key in (
            "provider_code",
            "display_name",
            "service",
            "role",
            "purpose_codes",
            "data_categories",
            "hosting_countries",
            "support_access_countries",
            "transfer_mechanism",
            "public_notice_summary",
        )
    }


def _public_purpose(entry: dict[str, Any]) -> dict[str, Any]:
    """Expose the purpose, not the root-only legal assessment notes."""

    return {
        key: entry.get(key)
        for key in ("purpose_code", "description", "required_or_optional", "withdrawal_effect")
    }


def _public_category(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: entry.get(key)
        for key in (
            "category_code", "display_name", "required_or_optional", "visibility",
            "source", "purpose_codes", "retention_policy_code", "sensitive_data_supported",
        )
    }


def _feature_disclosures(structured: dict[str, Any]) -> list[dict[str, str]]:
    features = structured.get("optional_features") or {}
    disclosures: list[dict[str, str]] = []
    if features.get("smtp_enabled"):
        disclosures.append({
            "code": "activation_email",
            "text": (
                "Activation and recovery messages may be delivered through the configured SMTP provider. "
                "Messages contain the recipient address, access purpose, event name where applicable, "
                "one-time link and expiry."
            ),
        })
    else:
        disclosures.append({
            "code": "manual_activation",
            "text": "Activation links are distributed manually; this instance does not use SMTP for delivery.",
        })
    if features.get("push_enabled"):
        disclosures.append({
            "code": "push_notifications",
            "text": (
                "Participants may enable Web Push. The push service receives the minimum delivery metadata; "
                "notifications can be withdrawn in account settings."
            ),
        })
    if features.get("offline_schedule_enabled"):
        disclosures.append({
            "code": "offline_schedule",
            "text": (
                "Participants may explicitly store their own limited schedule in IndexedDB on this device "
                "until the displayed expiry or manual removal. Shared-device users should remove it after use."
            ),
        })
    if features.get("public_schedule_enabled"):
        disclosures.append({
            "code": "public_schedule",
            "text": (
                "An organiser may create expiring public schedule links. Possession of a bearer link grants "
                "the access described on that link until it expires or is revoked."
            ),
        })
    if features.get("external_support_enabled"):
        disclosures.append({
            "code": "external_support",
            "text": "Authorised external support may access only the data and period recorded by the controller.",
        })
    if features.get("ha_enabled"):
        disclosures.append({
            "code": "high_availability",
            "text": "Encrypted recovery and high-availability copies follow the controller-selected retention policy.",
        })
    if features.get("dns_mode") == "dns_only":
        disclosures.append({
            "code": "dns_only_routing",
            "text": (
                "The DNS and health-selection provider may process DNS and health-check metadata. "
                "Application HTTPS is established directly with the selected server; the supported setup "
                "does not terminate application TLS at that provider."
            ),
        })
    return disclosures


def build_publication_payload(profile: InstanceGovernanceProfile) -> dict[str, Any]:
    """Build the exact public and evidential snapshot for one draft."""

    structured = profile_structured(profile)
    purposes = [_public_purpose(item) for item in structured.get("processing_purposes", []) if item.get("enabled")]
    categories = [_public_category(item) for item in structured.get("data_categories", []) if item.get("enabled")]
    processors = [
        _public_processor(item)
        for item in structured.get("processors", [])
        if item.get("enabled")
    ]
    features = structured.get("optional_features") or {}
    retention = structured.get("retention") or {}
    storage: dict[str, Any] = {
        "tracking": False,
        "session_cookie": (
            f"{settings.SESSION_COOKIE_NAME}: strictly necessary authentication token, valid for up to "
            f"{settings.SESSION_TTL_HOURS} hours; only its one-way digest is stored by the server."
        ),
        "csrf_cookie": (
            f"{settings.CSRF_COOKIE_NAME}: strictly necessary request-integrity token, valid for up to "
            f"{settings.SESSION_TTL_HOURS} hours."
        ),
        "session_metadata": (
            "The server retains session start, last-activity and expiry times, coarse browser and operating-"
            "system family, a one-way browser fingerprint and a daily keyed IP pseudonym. Active sessions "
            f"can be reviewed and revoked. Expired rows are retained for up to "
            f"{settings.RETENTION_EXPIRED_SESSIONS_DAYS} day(s) and revoked rows for up to "
            f"{settings.RETENTION_REVOKED_SESSIONS_DAYS} day(s)."
        ),
        "application_shell": (
            "The Cache API stores only a versioned static application shell and static assets. "
            "The supported service worker never stores authenticated API responses."
        ),
        "preferences": (
            "localStorage may retain theme, installation-prompt and notice preferences. "
            "These preference keys contain no session token or schedule payload."
        ),
        "tab_state": (
            "sessionStorage and browser history may temporarily retain an activation or recovery route "
            "secret in the current tab until it is completed or definitively rejected."
        ),
    }
    if features.get("offline_schedule_enabled"):
        storage["offline_schedule"] = (
            "Optional IndexedDB offline calendar response data is stored only after explicit enablement. It contains "
            "at most the signed-in participant's linked identity and own published unavailability, excludes "
            "other participant identities and organiser-only history, carries the server-bounded expiry, and is removed on expiry, confirmed "
            "logout, authorisation failure or successful manual removal."
        )

    return {
        "template_version": POLICY_TEMPLATE_VERSION,
        "instance_id": profile.instance_id,
        "instance_name": structured.get("instance_name"),
        "controller_type": profile.controller_type,
        "controller_legal_name": profile.controller_legal_name,
        "controller_postal_address": profile.controller_postal_address,
        "controller_country": profile.controller_country,
        "privacy_contact_email": profile.privacy_contact_email,
        "dpo_name_or_role": structured.get("dpo_name_or_role"),
        "dpo_contact": profile.dpo_contact,
        "eu_representative": structured.get("eu_representative"),
        "swiss_representative": structured.get("swiss_representative"),
        "supervisory_authority_name": profile.supervisory_authority_name,
        "supervisory_authority_url": profile.supervisory_authority_url,
        "default_locale": profile.default_locale,
        "supported_locales": structured.get("supported_locales", [profile.default_locale]),
        "jurisdiction_scope": structured.get("jurisdiction_scope"),
        "processing_purposes": purposes,
        "data_categories": categories,
        "processors": processors,
        "hosting_countries": structured.get("hosting_countries", []),
        "retention": retention,
        "optional_features": features,
        "feature_disclosures": _feature_disclosures(structured),
        "processor_summary": profile.processor_summary,
        "retention_summary": profile.retention_summary,
        "rights_summary": profile.rights_summary,
        "rights_request_url": structured.get("rights_request_url"),
        "incident_contact_email": structured.get("incident_contact_email"),
        "terms_summary": profile.terms_summary,
        "permitted_data": {
            "purpose": "Operational event scheduling and access management",
            "allowed": [item.get("display_name", item.get("category_code")) for item in categories],
            "unsupported": [
                "health", "dietary", "safeguarding", "political", "religious",
                "identity documents", "disciplinary", "unrelated private information",
            ],
            "sensitive_data_supported": False,
        },
        "storage": storage,
        "authentication": (
            "Passkeys remain on the user's device. The server stores the credential identifier, public key, "
            "sign counter, optional transports and authenticator metadata; it receives neither biometric data "
            "nor a passkey private key."
        ),
        "controller_supplied_wording": {
            "processors": profile.processor_summary,
            "retention": profile.retention_summary,
            "rights": profile.rights_summary,
            "terms": profile.terms_summary,
        },
    }


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _walk_strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _walk_strings(child)]
    return []


def governance_preflight(profile: InstanceGovernanceProfile | None) -> dict[str, Any]:
    """Classify publish readiness without pretending to validate legal decisions."""

    checks: list[dict[str, str]] = []

    def add(code: str, status: str, message: str) -> None:
        checks.append({"code": code, "status": status, "message": message})

    def add_optional(code: str, value: Any, label: str) -> None:
        present = bool(value and (not isinstance(value, str) or value.strip()))
        add(code, "ready" if present else "optional", f"{label} recorded." if present else f"{label} is optional and has not been provided.")

    if profile is None:
        add("controller", "missing", "Controller configuration has not been saved.")
        return {"ready": False, "checks": checks}

    required = {
        "controller_identity": profile.controller_legal_name,
        "privacy_contact": profile.privacy_contact_email,
    }
    for code, value in required.items():
        add(code, "ready" if value and value.strip() else "missing", f"{code.replace('_', ' ').title()} recorded." if value and value.strip() else f"{code.replace('_', ' ').title()} is missing.")

    for code, value, label in (
        ("controller_address", profile.controller_postal_address, "Controller service address"),
        ("controller_country", profile.controller_country, "Controller country"),
        ("supervisory_authority", profile.supervisory_authority_name, "Specific supervisory authority"),
        ("processor_summary", profile.processor_summary, "Supplementary processor wording"),
        ("retention_summary", profile.retention_summary, "Supplementary retention wording"),
        ("rights_summary", profile.rights_summary, "Supplementary rights procedure"),
        ("instance_terms", profile.terms_summary, "Supplementary instance terms"),
    ):
        add_optional(code, value, label)

    structured = profile_structured(profile)
    for code, key, label in (
        ("instance_name", "instance_name", "Public instance name"),
        ("jurisdiction_scope", "jurisdiction_scope", "Jurisdiction explanation"),
        ("incident_contact", "incident_contact_email", "Separate incident contact"),
        ("hosting_countries", "hosting_countries", "Deployment-level hosting countries"),
        ("processor_register", "processors", "Processor register"),
    ):
        add_optional(code, structured.get(key), label)
    for code, key in (
        ("processing_purposes", "processing_purposes"),
        ("data_categories", "data_categories"),
    ):
        value = structured.get(key)
        add(code, "ready" if value else "missing", f"{code.replace('_', ' ').title()} recorded." if value else f"{code.replace('_', ' ').title()} is missing.")
    retention = structured.get("retention") or {}
    enforced_retention_ready = all(retention.get(key) is not None for key in (
        "event_grace_days", "audit_retention_days", "browser_cache_expiry_hours",
    ))
    controller_retention_ready = all(retention.get(key) is not None for key in (
        "live_retention_days", "backup_retention_days", "receipt_retention_days",
    )) or bool(profile.retention_summary and profile.retention_summary.strip())
    retention_ready = enforced_retention_ready and controller_retention_ready
    add(
        "retention_configuration", "ready" if retention_ready else "missing",
        "Server-enforced periods and controller retention periods or criteria are recorded."
        if retention_ready else
        "Record the Server-managed periods and either the controller-selected live, backup and receipt periods or clear retention criteria.",
    )

    enabled_purposes = [item for item in structured.get("processing_purposes", []) if item.get("enabled")]
    undecided = [item.get("purpose_code") for item in enabled_purposes if not (item.get("gdpr_legal_basis") or item.get("swiss_justification_or_basis"))]
    add(
        "controller_basis_decisions",
        "externally_unverifiable" if undecided else "ready",
        "A legal basis is conditionally required where GDPR applies. Masterplan cannot determine applicability for: "
        + ", ".join(filter(None, undecided)) if undecided else "Controller basis or justification decisions recorded.",
    )

    features = structured.get("optional_features") or {}
    runtime = runtime_feature_state()
    for key in ("smtp_enabled", "push_enabled", "ha_enabled"):
        if key not in features:
            add(key, "missing", f"{key.replace('_', ' ').title()} declaration is missing.")
        elif bool(features[key]) != bool(runtime[key]):
            add(key, "contradiction", f"Declared {key.replace('_', ' ')} does not match the running configuration.")
        else:
            add(key, "ready", f"Declared {key.replace('_', ' ')} matches the running configuration.")
    if features.get("dns_mode") != runtime["dns_mode"]:
        add("dns_mode", "contradiction", "The notice must describe the supported DNS-only direct-TLS architecture.")
    else:
        add("dns_mode", "ready", "DNS-only direct-TLS wording selected.")

    enabled_processors = {item.get("provider_code") for item in structured.get("processors", []) if item.get("enabled")}
    required_provider_codes = []
    if features.get("smtp_enabled"):
        required_provider_codes.append(features.get("smtp_provider_code"))
    if features.get("push_enabled"):
        required_provider_codes.extend(features.get("push_provider_codes") or [])
    missing_providers = [code for code in required_provider_codes if not code or code not in enabled_processors]
    add("enabled_feature_processors", "contradiction" if missing_providers else "ready", "Every enabled external feature has an enabled processor entry." if not missing_providers else "An enabled external feature is missing its processor entry.")

    source_strings = list(required.values()) + [
        profile.controller_postal_address, profile.controller_country,
        profile.supervisory_authority_name, profile.supervisory_authority_url,
        profile.processor_summary, profile.retention_summary,
        profile.rights_summary, profile.terms_summary,
    ] + _walk_strings(structured)
    if any(_PLACEHOLDER.search(value) for value in source_strings if value):
        add("placeholder_content", "missing", "Replace placeholder or example content before publication.")
    else:
        add("placeholder_content", "ready", "No recognised placeholder content detected.")

    for item in structured.get("processors", []):
        if item.get("enabled") and item.get("dpa_status") not in {"accepted", "not_required"}:
            add(
                f"processor_dpa_{item.get('provider_code', 'unknown')}",
                "externally_unverifiable",
                "The controller must verify the provider agreement outside Masterplan.",
            )

    blocking = {"missing", "contradiction", "requires_controller_decision"}
    return {"ready": not any(item["status"] in blocking for item in checks), "checks": checks}


def publication_diff(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic path-level diff and materiality classification."""

    if previous is None:
        return {"material_change": True, "changes": [{"path": "$", "kind": "initial_publication"}]}

    def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key in sorted(value):
                result.update(flatten(value[key], f"{prefix}.{key}" if prefix else key))
            return result
        if isinstance(value, list):
            return {prefix: value}
        return {prefix: value}

    before = flatten(previous)
    after = flatten(current)
    changes = []
    for path in sorted(set(before) | set(after)):
        if before.get(path) != after.get(path):
            changes.append({"path": path, "before": before.get(path), "after": after.get(path)})
    material = any(any(change["path"].startswith(prefix) for prefix in MATERIAL_PATH_PREFIXES) for change in changes)
    return {"material_change": material, "changes": changes}
