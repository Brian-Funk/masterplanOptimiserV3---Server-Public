"""Helpers for instance identity and published permitted-data enforcement."""

import hashlib
import json
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.governance import (
    DataPolicyAcknowledgement,
    GovernancePublication,
    InstanceGovernanceProfile,
)
from app.models.server_setting import ServerSetting
from app.models.event import Event
from app.models.tenancy import (
    ControllerGovernancePublication,
    EventGovernanceConfiguration,
)
from app.models.user import User


def stable_instance_id(db: Session) -> str:
    """Return one durable UUID for legacy and newly configured instances."""

    if settings.MP_INSTANCE_ID:
        return settings.MP_INSTANCE_ID
    profile = db.get(InstanceGovernanceProfile, 1)
    if profile is not None:
        return profile.instance_id
    row = db.query(ServerSetting).filter(ServerSetting.key == "instance_id").first()
    if row is None:
        row = ServerSetting(key="instance_id", value=str(uuid.uuid4()))
        db.add(row)
        db.flush()
    return row.value


def current_policy_version(db: Session) -> int | None:
    """Return the latest immutable policy version, if the controller published one."""

    publication = db.query(GovernancePublication).order_by(
        GovernancePublication.version.desc()
    ).first()
    return publication.version if publication else None


def current_policy_identity(
    db: Session,
    *,
    event_id: int | None = None,
    controller_id: int | None = None,
) -> tuple[int, str] | None:
    """Return the exact controller policy applicable to one tenant context."""

    mode = db.query(ServerSetting).filter(ServerSetting.key == "tenancy_mode").first()
    if mode is not None and mode.value == "hosted-multi-controller":
        if event_id is not None:
            event = db.get(Event, event_id)
            config = db.get(EventGovernanceConfiguration, event_id)
            if event is None or config is None or config.controller_id != event.controller_id:
                return None
            publication = db.query(ControllerGovernancePublication).filter(
                ControllerGovernancePublication.controller_id == event.controller_id,
                ControllerGovernancePublication.version == config.controller_policy_version,
            ).first()
        elif controller_id is not None:
            publication = db.query(ControllerGovernancePublication).filter(
                ControllerGovernancePublication.controller_id == controller_id,
            ).order_by(ControllerGovernancePublication.version.desc()).first()
        else:
            return None
        if publication is None:
            return None
        return publication.version, publication.content_sha256

    publication = db.query(GovernancePublication).order_by(
        GovernancePublication.version.desc()
    ).first()
    if publication is None:
        return None
    return publication.version, publication.content_sha256


def current_policy_template_version(
    db: Session,
    *,
    event_id: int | None = None,
    controller_id: int | None = None,
) -> str | None:
    """Return the renderer version for one exact controller policy."""

    mode = db.query(ServerSetting).filter(ServerSetting.key == "tenancy_mode").first()
    if mode is not None and mode.value == "hosted-multi-controller":
        if event_id is not None:
            event = db.get(Event, event_id)
            config = db.get(EventGovernanceConfiguration, event_id)
            if event is None or config is None or config.controller_id != event.controller_id:
                return None
            publication = db.query(ControllerGovernancePublication).filter(
                ControllerGovernancePublication.controller_id == event.controller_id,
                ControllerGovernancePublication.version == config.controller_policy_version,
            ).first()
        elif controller_id is not None:
            publication = db.query(ControllerGovernancePublication).filter(
                ControllerGovernancePublication.controller_id == controller_id,
            ).order_by(ControllerGovernancePublication.version.desc()).first()
        else:
            return None
    else:
        publication = db.query(GovernancePublication).order_by(
            GovernancePublication.version.desc()
        ).first()
    if publication is None:
        return None
    try:
        content = json.loads(publication.content_json)
    except (json.JSONDecodeError, TypeError):
        return None
    version = content.get("template_version") if isinstance(content, dict) else None
    if version is None and isinstance(content, dict):
        governance = content.get("governance")
        version = governance.get("template_version") if isinstance(governance, dict) else None
    return version if isinstance(version, str) else None


def require_current_policy_identity(
    policy_version: int,
    policy_sha256: str,
    db: Session,
    *,
    event_id: int | None = None,
    controller_id: int | None = None,
) -> tuple[int, str]:
    """Fail closed unless a submitted acknowledgement names the current policy."""

    identity = current_policy_identity(
        db, event_id=event_id, controller_id=controller_id
    )
    if identity is None:
        raise HTTPException(status_code=409, detail="No permitted-data policy is published")
    if identity != (policy_version, policy_sha256.lower()):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "data_policy_identity_mismatch",
                "policy_version": identity[0],
                "policy_sha256": identity[1],
                "message": "The permitted-data policy changed. Review the current exact version before continuing.",
            },
        )
    return identity


BOOTSTRAP_POLICY_VERSION = "2026-07-30"
BOOTSTRAP_POLICY_TEXT = (
    "Masterplan supports operational event scheduling and access management. "
    "Permitted information is limited to necessary names, business contact details, "
    "event roles, availability and operational instructions. Do not enter health, "
    "dietary, safeguarding, political, religious, disciplinary or unrelated private "
    "information. The self-hosting controller remains responsible for legal basis, "
    "transparency, providers, access, retention, data-subject requests and incidents."
)
BOOTSTRAP_POLICY_SHA256 = hashlib.sha256(
    json.dumps(
        {"version": BOOTSTRAP_POLICY_VERSION, "text": BOOTSTRAP_POLICY_TEXT},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


def require_data_policy_acknowledgement(user: User, event_id: int, db: Session) -> None:
    """Require the current event policy before a user writes broad event data."""

    identity = current_policy_identity(db, event_id=event_id)
    if identity is None:
        return
    version, digest = identity
    acknowledged = db.query(DataPolicyAcknowledgement).filter(
        DataPolicyAcknowledgement.user_id == user.id,
        DataPolicyAcknowledgement.event_id == event_id,
        DataPolicyAcknowledgement.policy_version == version,
        DataPolicyAcknowledgement.policy_sha256 == digest,
        DataPolicyAcknowledgement.superseded_at.is_(None),
    ).first()
    if acknowledged is None:
        raise HTTPException(
            status_code=428,
            detail={
                "code": "data_policy_acknowledgement_required",
                "policy_version": version,
                "message": "Acknowledge the current permitted-data policy before editing event content.",
            },
        )


def has_data_policy_acknowledgement(user: User, event_id: int, db: Session) -> bool:
    """Return whether the user has acknowledged the current event policy."""

    identity = current_policy_identity(db, event_id=event_id)
    if identity is None:
        return True
    version, digest = identity
    return db.query(DataPolicyAcknowledgement).filter(
        DataPolicyAcknowledgement.user_id == user.id,
        DataPolicyAcknowledgement.event_id == event_id,
        DataPolicyAcknowledgement.policy_version == version,
        DataPolicyAcknowledgement.policy_sha256 == digest,
        DataPolicyAcknowledgement.superseded_at.is_(None),
    ).first() is not None
