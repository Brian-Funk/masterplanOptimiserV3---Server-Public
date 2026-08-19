"""Event-resolved activation disclosure and acknowledgement binding."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.tenancy import TENANCY_HOSTED, tenancy_mode
from app.models.event import Event
from app.models.governance import EventGovernanceOverride, GovernancePublication
from app.models.tenancy import (
    Controller,
    ControllerGovernancePublication,
    EventGovernanceConfiguration,
    OperatorPolicyPublication,
)
from app.models.user import User


STATEMENT_VERSION = "mp-opt-account-processing-disclosure-v2"


class ActivationConsentError(RuntimeError):
    """Safe refusal when an exact published activation disclosure is unavailable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True)
class ActivationConsentDisclosure:
    """Canonical disclosure shown and bound to one initial WebAuthn ceremony."""

    document: dict[str, object]
    document_json: str
    statement_sha256: str

    def public_payload(self) -> dict[str, object]:
        return {**self.document, "statement_sha256": self.statement_sha256}


def _unavailable() -> ActivationConsentError:
    return ActivationConsentError(
        "published_activation_disclosure_unavailable",
        (
            "Account activation is waiting for complete published event, controller "
            "and hosting-operator notices. Ask an administrator to review Governance "
            "and send a fresh activation link."
        ),
    )


def _required(value: object) -> str:
    result = str(value or "").strip()
    if not result:
        raise _unavailable()
    return result


def _object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise _unavailable() from exc
    if not isinstance(parsed, dict):
        raise _unavailable()
    return parsed


def _canonical(document: dict[str, object]) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _finish(document: dict[str, object]) -> ActivationConsentDisclosure:
    rendered = _canonical(document)
    return ActivationConsentDisclosure(
        document=document,
        document_json=rendered,
        statement_sha256=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
    )


def _hosted_disclosure(user: User, db: Session) -> ActivationConsentDisclosure:
    if user.event_id is None:
        raise _unavailable()
    event = db.get(Event, user.event_id)
    if event is None:
        raise _unavailable()
    controller = db.get(Controller, event.controller_id)
    config = db.get(EventGovernanceConfiguration, event.id)
    if controller is None or config is None:
        raise _unavailable()
    controller_publication = db.query(ControllerGovernancePublication).filter(
        ControllerGovernancePublication.controller_id == controller.id,
        ControllerGovernancePublication.version == config.controller_policy_version,
    ).first()
    operator_publication = db.query(OperatorPolicyPublication).filter(
        OperatorPolicyPublication.version == config.operator_policy_version
    ).first()
    if controller_publication is None or operator_publication is None:
        raise _unavailable()
    if (
        controller_publication.operator_policy_version != operator_publication.version
        or controller_publication.operator_policy_sha256 != operator_publication.content_sha256
    ):
        raise _unavailable()

    controller_notice = _object(controller_publication.content_json)
    operator_notice = _object(operator_publication.content_json)
    governance = controller_notice.get("governance")
    if not isinstance(governance, dict):
        raise _unavailable()
    legal_basis_code = _required(governance.get("account_access_legal_basis_code"))
    purposes_raw = governance.get("processing_purposes")
    categories_raw = governance.get("data_categories")
    if not isinstance(purposes_raw, list) or not isinstance(categories_raw, list):
        raise _unavailable()
    purposes = [
        _required(item.get("description"))
        for item in purposes_raw
        if isinstance(item, dict) and item.get("enabled", True)
    ]
    categories = [
        _required(item.get("display_name") or item.get("category_code"))
        for item in categories_raw
        if isinstance(item, dict) and item.get("enabled", True)
    ]
    if not purposes or not categories:
        raise _unavailable()

    controller_name = _required(controller_notice.get("legal_name"))
    privacy_contact = _required(controller_notice.get("privacy_contact_email"))
    operator_name = _required(operator_notice.get("operator_legal_name"))
    origin = settings.WEBAUTHN_ORIGIN.rstrip("/")
    statement = (
        f"I have read the processing information for {event.name}. I understand "
        f"that {controller_name} is the legal controller, that {operator_name} "
        "operates the hosted service as a technically privileged processor/service "
        "provider, and that my account is processed under the stated legal basis. "
        "This confirmation records receipt of the disclosure; it is not consent to "
        "processing that is required for authenticated event access."
    )
    return _finish(
        {
            "format": STATEMENT_VERSION,
            "confirmation_type": "disclosure_acknowledgement",
            "legal_basis_code": legal_basis_code,
            "instance_id": _required(operator_notice.get("instance_id")),
            "user_subject_id": user.evidence_subject_id,
            "event_ref": event.evidence_id,
            "event_name": event.name,
            "controller_public_id": controller.public_id,
            "controller_identity": controller_name,
            "privacy_contact": privacy_contact,
            "hosting_operator_identity": operator_name,
            "processing_purposes": purposes,
            "data_categories": categories,
            "authenticated_audience": "Authenticated accounts assigned to this event",
            "operator_policy_version": operator_publication.version,
            "operator_policy_sha256": operator_publication.content_sha256,
            "controller_policy_version": controller_publication.version,
            "controller_policy_sha256": controller_publication.content_sha256,
            "event_notice_revision": config.revision,
            "event_notice_sha256": config.content_sha256,
            "policy_version": controller_publication.version,
            "policy_sha256": controller_publication.content_sha256,
            "privacy_url": f"{origin}/api/v1/legal/events/{event.evidence_id}/privacy.html",
            "rights_url": (
                f"{origin}/api/v1/legal/controllers/{controller.public_id}"
                f"/versions/{controller_publication.version}"
            ),
            "event_privacy_url": f"{origin}/api/v1/legal/events/{event.evidence_id}/privacy.html",
            "event_notice": config.event_notice,
            "statement": statement,
        }
    )


def _legacy_disclosure(user: User, db: Session) -> ActivationConsentDisclosure:
    publication = db.query(GovernancePublication).order_by(
        GovernancePublication.version.desc()
    ).first()
    if publication is None:
        raise _unavailable()
    notice = _object(publication.content_json)
    controller = _required(notice.get("controller_legal_name"))
    privacy_contact = _required(notice.get("privacy_contact_email"))
    instance_name = _required(notice.get("instance_name"))
    instance_id = _required(notice.get("instance_id"))
    event_ref: str | None = None
    event_name: str | None = None
    event_privacy_url: str | None = None
    if user.event_id is not None:
        event = db.get(Event, user.event_id)
        if event is None:
            raise _unavailable()
        event_ref = event.evidence_id
        event_name = _required(event.name)
        override = db.get(EventGovernanceOverride, event.id)
        if override is not None and override.controller_override_enabled:
            controller = _required(override.controller_identity_override)
            privacy_contact = _required(override.privacy_contact_override)
            event_privacy_url = (
                f"{settings.WEBAUTHN_ORIGIN.rstrip('/')}"
                f"/api/v1/governance/public/events/{event.id}/privacy.html"
            )
    purposes = [
        _required(item.get("description"))
        for item in (notice.get("processing_purposes") or [])
        if isinstance(item, dict) and item.get("enabled", True)
    ]
    categories = [
        _required(item.get("display_name") or item.get("category_code"))
        for item in (notice.get("data_categories") or [])
        if isinstance(item, dict) and item.get("enabled", True)
    ]
    if not purposes or not categories:
        raise _unavailable()
    version_base = (
        f"{settings.WEBAUTHN_ORIGIN.rstrip('/')}"
        f"/api/v1/governance/public/versions/{publication.version}"
    )
    statement = (
        f"I have read the privacy information for {controller} and understand the "
        f"described processing required to use authenticated {instance_name} access. "
        f"Questions and rights requests can be sent to {privacy_contact}. This is a "
        "disclosure acknowledgement and does not assert consent as the legal basis."
    )
    return _finish(
        {
            "format": STATEMENT_VERSION,
            "confirmation_type": "disclosure_acknowledgement",
            "legal_basis_code": "legacy_controller_declared",
            "instance_id": instance_id,
            "user_subject_id": user.evidence_subject_id,
            "event_ref": event_ref,
            "event_name": event_name,
            "controller_identity": controller,
            "privacy_contact": privacy_contact,
            "instance_name": instance_name,
            "processing_purposes": purposes,
            "data_categories": categories,
            "authenticated_audience": "Authenticated accounts assigned to this event",
            "policy_version": publication.version,
            "policy_sha256": publication.content_sha256,
            "privacy_url": f"{version_base}/privacy.html",
            "rights_url": f"{version_base}/rights.html",
            "event_privacy_url": event_privacy_url,
            "statement": statement,
        }
    )


def resolve_activation_consent(user: User, db: Session) -> ActivationConsentDisclosure:
    """Resolve the exact event/controller/operator disclosure for one account."""

    if tenancy_mode(db) == TENANCY_HOSTED:
        return _hosted_disclosure(user, db)
    return _legacy_disclosure(user, db)
