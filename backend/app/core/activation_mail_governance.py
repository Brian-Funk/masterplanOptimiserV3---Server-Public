"""Published, deployment-specific governance facts for participant access mail."""

from __future__ import annotations

import json
from dataclasses import dataclass

from email_validator import EmailNotValidError, validate_email
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


class ActivationMailGovernanceError(RuntimeError):
    """Safe configuration failure raised before any participant token is issued."""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(frozen=True)
class ActivationMailGovernance:
    """Exact public facts used by one action-first access email."""

    brand: str
    controller_name: str
    privacy_contact: str
    smtp_provider_name: str
    smtp_processing_countries: tuple[str, ...]
    policy_version: int
    policy_sha256: str
    privacy_url: str
    rights_url: str
    event_privacy_url: str | None
    event_name: str | None


def _configuration_error() -> ActivationMailGovernanceError:
    return ActivationMailGovernanceError(
        "published_mail_governance_unavailable",
        (
            "Participant email delivery is waiting for a complete published "
            "controller and email-provider notice. Ask a root administrator to "
            "review and publish Governance, then try again."
        ),
    )


def _required_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise _configuration_error()
    return text


def _published_notice(db: Session) -> tuple[GovernancePublication, dict[str, object]]:
    publication = (
        db.query(GovernancePublication)
        .order_by(GovernancePublication.version.desc())
        .first()
    )
    if publication is None:
        raise _configuration_error()
    try:
        notice = json.loads(publication.content_json)
    except (TypeError, ValueError) as exc:
        raise _configuration_error() from exc
    if not isinstance(notice, dict):
        raise _configuration_error()
    return publication, notice


def resolve_activation_mail_governance(
    *,
    user: User,
    db: Session,
) -> ActivationMailGovernance:
    """Resolve only published facts applicable to the recipient's event."""

    if tenancy_mode(db) == TENANCY_HOSTED:
        return _resolve_hosted_activation_mail_governance(user=user, db=db)

    publication, notice = _published_notice(db)
    features = notice.get("optional_features")
    if not isinstance(features, dict) or features.get("smtp_enabled") is not True:
        raise _configuration_error()
    if not all(
        (
            settings.SMTP_HOST,
            settings.SMTP_USERNAME,
            settings.SMTP_TOKEN,
            settings.SMTP_FROM_EMAIL,
        )
    ):
        raise _configuration_error()

    provider_code = _required_text(features.get("smtp_provider_code"))
    processors = notice.get("processors")
    if not isinstance(processors, list):
        raise _configuration_error()
    provider = next(
        (
            item
            for item in processors
            if isinstance(item, dict) and item.get("provider_code") == provider_code
        ),
        None,
    )
    if provider is None:
        raise _configuration_error()
    if "activation_email" not in (provider.get("purpose_codes") or []):
        raise _configuration_error()
    provider_name = _required_text(provider.get("display_name"))
    countries = sorted(
        {
            str(country).strip().upper()
            for field in ("hosting_countries", "support_access_countries")
            for country in (provider.get(field) or [])
            if str(country).strip()
        }
    )
    if not countries:
        raise _configuration_error()

    brand = _required_text(notice.get("instance_name"))
    controller_name = _required_text(notice.get("controller_legal_name"))
    privacy_contact = _required_text(notice.get("privacy_contact_email"))
    try:
        privacy_contact = validate_email(
            privacy_contact,
            check_deliverability=False,
        ).normalized
    except EmailNotValidError as exc:
        raise _configuration_error() from exc
    event_name: str | None = None
    event_privacy_url: str | None = None

    if user.event_id is not None:
        event = db.get(Event, user.event_id)
        if event is None:
            raise _configuration_error()
        event_name = _required_text(event.name)
        override = db.get(EventGovernanceOverride, event.id)
        if override is not None:
            try:
                event_features = set(json.loads(override.enabled_optional_features_json or "[]"))
            except (TypeError, ValueError) as exc:
                raise _configuration_error() from exc
            if "activation_email" not in event_features:
                raise ActivationMailGovernanceError(
                    "event_activation_email_disabled",
                    "Email delivery is not enabled for this event's published governance settings.",
                )
            if override.controller_override_enabled:
                controller_name = _required_text(override.controller_identity_override)
                privacy_contact = _required_text(override.privacy_contact_override)
                try:
                    privacy_contact = validate_email(
                        privacy_contact,
                        check_deliverability=False,
                    ).normalized
                except EmailNotValidError as exc:
                    raise _configuration_error() from exc
                event_privacy_url = (
                    f"{settings.WEBAUTHN_ORIGIN.rstrip('/')}"
                    f"/api/v1/governance/public/events/{event.id}/privacy.html"
                )

    origin = settings.WEBAUTHN_ORIGIN.rstrip("/")
    version_base = f"{origin}/api/v1/governance/public/versions/{publication.version}"
    return ActivationMailGovernance(
        brand=brand,
        controller_name=controller_name,
        privacy_contact=privacy_contact,
        smtp_provider_name=provider_name,
        smtp_processing_countries=tuple(countries),
        policy_version=publication.version,
        policy_sha256=publication.content_sha256,
        privacy_url=f"{version_base}/privacy.html",
        rights_url=f"{version_base}/rights.html",
        event_privacy_url=event_privacy_url,
        event_name=event_name,
    )


def _resolve_hosted_activation_mail_governance(
    *, user: User, db: Session
) -> ActivationMailGovernance:
    if user.event_id is None:
        raise _configuration_error()
    event = db.get(Event, user.event_id)
    if event is None:
        raise _configuration_error()
    controller = db.get(Controller, event.controller_id)
    config = db.get(EventGovernanceConfiguration, event.id)
    if controller is None or config is None:
        raise _configuration_error()
    features = set(json.loads(config.enabled_optional_features_json or "[]"))
    if "smtp_activation" not in features:
        raise ActivationMailGovernanceError(
            "event_activation_email_disabled",
            "Email delivery is not enabled for this event's published governance settings.",
        )
    controller_publication = db.query(ControllerGovernancePublication).filter(
        ControllerGovernancePublication.controller_id == controller.id,
        ControllerGovernancePublication.version == config.controller_policy_version,
    ).first()
    operator_publication = db.query(OperatorPolicyPublication).filter(
        OperatorPolicyPublication.version == config.operator_policy_version
    ).first()
    if controller_publication is None or operator_publication is None:
        raise _configuration_error()
    try:
        controller_notice = json.loads(controller_publication.content_json)
        operator_notice = json.loads(operator_publication.content_json)
    except (TypeError, ValueError) as exc:
        raise _configuration_error() from exc
    if not isinstance(controller_notice, dict) or not isinstance(operator_notice, dict):
        raise _configuration_error()
    subprocessors = operator_notice.get("subprocessors")
    if not isinstance(subprocessors, list):
        raise _configuration_error()
    provider = next(
        (
            item for item in subprocessors
            if isinstance(item, dict)
            and "activation_email" in (item.get("purpose_codes") or [])
        ),
        None,
    )
    if provider is None:
        raise _configuration_error()
    countries = sorted(
        {
            str(country).strip().upper()
            for field in ("hosting_countries", "support_access_countries")
            for country in (provider.get(field) or [])
            if str(country).strip()
        }
    )
    if not countries or not all(
        (settings.SMTP_HOST, settings.SMTP_USERNAME, settings.SMTP_TOKEN, settings.SMTP_FROM_EMAIL)
    ):
        raise _configuration_error()
    origin = settings.WEBAUTHN_ORIGIN.rstrip("/")
    return ActivationMailGovernance(
        brand=_required_text(operator_notice.get("operator_legal_name")),
        controller_name=_required_text(controller_notice.get("legal_name")),
        privacy_contact=_required_text(controller_notice.get("privacy_contact_email")),
        smtp_provider_name=_required_text(provider.get("display_name")),
        smtp_processing_countries=tuple(countries),
        policy_version=controller_publication.version,
        policy_sha256=controller_publication.content_sha256,
        privacy_url=f"{origin}/api/v1/legal/events/{event.evidence_id}/privacy.html",
        rights_url=(
            f"{origin}/api/v1/legal/controllers/{controller.public_id}"
            f"/versions/{controller_publication.version}/rights.html"
        ),
        event_privacy_url=f"{origin}/api/v1/legal/events/{event.evidence_id}/privacy.html",
        event_name=event.name,
    )
