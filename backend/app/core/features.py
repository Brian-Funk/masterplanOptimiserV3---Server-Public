"""One authoritative effective-feature resolver for event operations."""

from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.tenancy import TENANCY_HOSTED, tenancy_mode
from app.models.event import Event
from app.models.governance import EventGovernanceOverride, GovernancePublication
from app.models.tenancy import EventGovernanceConfiguration
from app.models.tenancy import (
    ControllerGovernancePublication,
    OperatorPolicyPublication,
)


FEATURE_ALIASES = {
    "smtp_activation": "activation_email",
    "offline_schedule": "offline_calendar",
    "public_schedule_links": "public_links",
    "push_notifications": "push_notifications",
    "desktop_publishing": "desktop_publishing",
}


def _published_features(content_json: str, field: str) -> frozenset[str]:
    try:
        document = json.loads(content_json)
    except (TypeError, ValueError):
        return frozenset()
    if not isinstance(document, dict):
        return frozenset()
    values = document.get(field)
    if not isinstance(values, list):
        return frozenset()
    return frozenset(
        value
        for value in values
        if isinstance(value, str) and value in FEATURE_ALIASES
    )


def hosted_feature_ceiling(
    operator_publication: OperatorPolicyPublication,
    controller_publication: ControllerGovernancePublication,
) -> frozenset[str]:
    """Return the immutable operator/controller feature intersection."""

    if (
        controller_publication.operator_policy_version
        != operator_publication.version
        or controller_publication.operator_policy_sha256
        != operator_publication.content_sha256
    ):
        return frozenset()
    operator_features = _published_features(
        operator_publication.content_json,
        "supported_optional_features",
    )
    controller_features = _published_features(
        controller_publication.content_json,
        "permitted_optional_features",
    )
    return operator_features & controller_features


def validate_hosted_event_features(
    selected: list[str] | set[str] | frozenset[str],
    operator_publication: OperatorPolicyPublication,
    controller_publication: ControllerGovernancePublication,
) -> list[str]:
    """Return selected features or fail before storing an invalid contract."""

    unavailable = sorted(set(selected) - hosted_feature_ceiling(
        operator_publication,
        controller_publication,
    ))
    if unavailable:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "event_feature_not_permitted",
                "features": unavailable,
                "message": "One or more event features are outside the immutable operator/controller policy.",
            },
        )
    return sorted(set(selected))


def enabled_event_features(event_id: int, db: Session) -> frozenset[str]:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if tenancy_mode(db) == TENANCY_HOSTED:
        config = db.get(EventGovernanceConfiguration, event_id)
        if config is None:
            return frozenset()
        try:
            values = json.loads(config.enabled_optional_features_json or "[]")
        except (TypeError, ValueError):
            return frozenset()
        selected = frozenset(
            str(item)
            for item in values
            if isinstance(item, str) and item in FEATURE_ALIASES
        )
        operator_publication = db.query(OperatorPolicyPublication).filter(
            OperatorPolicyPublication.version == config.operator_policy_version
        ).first()
        controller_publication = db.query(ControllerGovernancePublication).filter(
            ControllerGovernancePublication.controller_id == event.controller_id,
            ControllerGovernancePublication.version == config.controller_policy_version,
        ).first()
        if operator_publication is None or controller_publication is None:
            return frozenset()
        return selected & hosted_feature_ceiling(
            operator_publication,
            controller_publication,
        )

    override = db.get(EventGovernanceOverride, event_id)
    if override is not None:
        try:
            legacy_values = set(json.loads(override.enabled_optional_features_json or "[]"))
        except (TypeError, ValueError):
            legacy_values = set()
        return frozenset(
            feature
            for feature, legacy in FEATURE_ALIASES.items()
            if legacy in legacy_values or feature in legacy_values
        )

    publication = db.query(GovernancePublication).order_by(
        GovernancePublication.version.desc()
    ).first()
    if publication is None:
        # Pre-governance single-controller compatibility. Hosted mode never
        # reaches this permissive branch.
        return frozenset(FEATURE_ALIASES)
    try:
        notice = json.loads(publication.content_json)
    except (TypeError, ValueError):
        return frozenset()
    optional = notice.get("optional_features") if isinstance(notice, dict) else None
    if not isinstance(optional, dict):
        return frozenset()
    result: set[str] = set()
    for feature, legacy in FEATURE_ALIASES.items():
        if optional.get(feature) is True or optional.get(legacy) is True:
            result.add(feature)
    return frozenset(result)


def require_event_feature(event_id: int, feature: str, db: Session) -> None:
    if feature not in FEATURE_ALIASES:
        raise ValueError("Unsupported event feature")
    if feature not in enabled_event_features(event_id, db):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "event_feature_unavailable",
                "feature": feature,
                "message": "This feature is not enabled for the event.",
            },
        )
