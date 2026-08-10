"""Published, event-aware processing consent for first account activation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.event import Event
from app.models.governance import EventGovernanceOverride, GovernancePublication
from app.models.user import User


STATEMENT_VERSION = "mp-opt-account-processing-consent-v1"


class ActivationConsentError(RuntimeError):
    """Safe refusal when an exact published consent disclosure is unavailable."""

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
        return {
            **self.document,
            "statement_sha256": self.statement_sha256,
        }


def _unavailable() -> ActivationConsentError:
    return ActivationConsentError(
        "published_activation_consent_unavailable",
        (
            "Account activation is waiting for a complete published controller notice. "
            "Ask an administrator to review Governance and send a fresh activation link."
        ),
    )


def _required(value: object) -> str:
    result = str(value or "").strip()
    if not result:
        raise _unavailable()
    return result


def _canonical(document: dict[str, object]) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def resolve_activation_consent(user: User, db: Session) -> ActivationConsentDisclosure:
    """Resolve the exact published processing disclosure for one account."""

    publication = (
        db.query(GovernancePublication)
        .order_by(GovernancePublication.version.desc())
        .first()
    )
    if publication is None:
        raise _unavailable()
    try:
        notice = json.loads(publication.content_json)
    except (TypeError, ValueError) as exc:
        raise _unavailable() from exc
    if not isinstance(notice, dict):
        raise _unavailable()

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
        f"I have read the privacy information and consent to {controller} processing "
        f"the described operational account and event data so I can use authenticated "
        f"{instance_name} access. I understand that I can withdraw this consent by "
        f"using Delete my data or by contacting {privacy_contact}; withdrawal does not "
        "affect processing already carried out."
    )
    document: dict[str, object] = {
        "format": STATEMENT_VERSION,
        "instance_id": instance_id,
        "user_subject_id": user.evidence_subject_id,
        "event_ref": event_ref,
        "event_name": event_name,
        "controller_identity": controller,
        "privacy_contact": privacy_contact,
        "instance_name": instance_name,
        "processing_purposes": purposes,
        "data_categories": categories,
        "authenticated_audience": "Authenticated Masterplan users for the assigned event",
        "policy_version": publication.version,
        "policy_sha256": publication.content_sha256,
        "privacy_url": f"{version_base}/privacy.html",
        "rights_url": f"{version_base}/rights.html",
        "event_privacy_url": event_privacy_url,
        "statement": statement,
    }
    rendered = _canonical(document)
    return ActivationConsentDisclosure(
        document=document,
        document_json=rendered,
        statement_sha256=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
    )
