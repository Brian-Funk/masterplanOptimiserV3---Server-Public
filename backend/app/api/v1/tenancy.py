"""Operator, controller and event-governance tenancy endpoints."""

from __future__ import annotations

import hashlib
import html
import json
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.governance_rendering import POLICY_TEMPLATE_VERSION
from app.core.database_tenancy import (
    DatabaseTenantContext,
    apply_database_tenant_context,
    bounded_event_service_context,
)
from app.core.governance import stable_instance_id
from app.core.evidence import (
    EvidenceUnavailable,
    append_record,
    initialise_controller_chain,
)
from app.core.security import (
    require_root_admin_read_only,
    require_root_recent_reauth,
)
from app.core.tenancy import (
    TENANCY_HOSTED,
    event_governance_identity,
    hosted_mode_preflight,
    set_tenancy_mode,
    tenancy_mode,
)
from app.db.database import get_db
from app.models.event import Event
from app.models.evidence import EvidenceKey
from app.models.tenancy import (
    Controller,
    ControllerGovernanceProfile,
    ControllerGovernancePublication,
    EventGovernanceConfiguration,
    InstanceOperatorProfile,
    OperatorPolicyPublication,
)
from app.models.user import User


admin_router = APIRouter()
public_router = APIRouter()

CONTROLLER_CODE = re.compile(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")
OPTIONAL_FEATURES = frozenset(
    {
        "desktop_publishing",
        "offline_schedule",
        "public_schedule_links",
        "push_notifications",
        "smtp_activation",
    }
)


def _canonical(value: object) -> tuple[str, str]:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return rendered, hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _json_object(value: str, *, field: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(status_code=409, detail=f"Invalid {field}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=409, detail=f"Invalid {field}")
    return parsed


def _json_array(value: str, *, field: str) -> list[object]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(status_code=409, detail=f"Invalid {field}") from exc
    if not isinstance(parsed, list):
        raise HTTPException(status_code=409, detail=f"Invalid {field}")
    return parsed


class OperatorSubprocessorIn(BaseModel):
    """Bounded public description of one operator-selected subprocessor."""

    provider_code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,62}[a-z0-9]$")
    display_name: str = Field(min_length=1, max_length=200)
    purpose_codes: list[str] = Field(min_length=1, max_length=30)
    hosting_countries: list[str] = Field(min_length=1, max_length=50)
    support_access_countries: list[str] = Field(default_factory=list, max_length=50)
    privacy_url: str | None = Field(None, max_length=500)

    @field_validator("purpose_codes")
    @classmethod
    def valid_purposes(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Subprocessor purposes must be unique")
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", item) for item in value):
            raise ValueError("Subprocessor purpose codes are invalid")
        return sorted(value)

    @field_validator("hosting_countries", "support_access_countries")
    @classmethod
    def valid_countries(cls, value: list[str]) -> list[str]:
        normalised = [item.strip().upper() for item in value]
        if len(normalised) != len(set(normalised)):
            raise ValueError("Subprocessor countries must be unique")
        if any(not re.fullmatch(r"[A-Z]{2}", item) for item in normalised):
            raise ValueError("Subprocessor countries must use two-letter codes")
        return sorted(normalised)

    @field_validator("privacy_url")
    @classmethod
    def https_privacy_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("Subprocessor privacy URLs must use HTTPS")
        return value


class OperatorProfileIn(BaseModel):
    operator_type: Literal["organisation", "individual"]
    operator_legal_name: str = Field(min_length=1, max_length=200)
    operator_postal_address: str = Field(min_length=1, max_length=500)
    operator_country: str = Field(pattern=r"^[A-Z]{2}$")
    privacy_contact_email: EmailStr
    service_description: str = Field(min_length=1, max_length=5000)
    security_summary: str = Field(min_length=1, max_length=5000)
    subprocessors: list[OperatorSubprocessorIn] = Field(max_length=100)
    hosting_regions: list[str] = Field(min_length=1, max_length=50)
    fixed_retention_days: int = Field(ge=1, le=3650)
    dpa_url: str | None = Field(None, max_length=500)
    subprocessor_schedule_url: str | None = Field(None, max_length=500)

    @field_validator("hosting_regions")
    @classmethod
    def valid_regions(cls, value: list[str]) -> list[str]:
        normalised = [item.strip().upper() for item in value]
        if len(normalised) != len(set(normalised)):
            raise ValueError("Hosting regions must be unique")
        if any(not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,31}", item) for item in normalised):
            raise ValueError("Hosting region codes are invalid")
        return sorted(normalised)

    @field_validator("dpa_url", "subprocessor_schedule_url")
    @classmethod
    def https_operator_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("Operator legal URLs must use HTTPS")
        return value


class ControllerCreateIn(BaseModel):
    code: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=200)

    @field_validator("code")
    @classmethod
    def valid_code(cls, value: str) -> str:
        if not CONTROLLER_CODE.fullmatch(value):
            raise ValueError("Use lower-case letters, numbers and internal hyphens")
        return value


class ControllerUpdateIn(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=200)
    status: Literal["draft", "active", "suspended", "retired"] | None = None


class ControllerProfileIn(BaseModel):
    controller_type: Literal["organisation", "individual"]
    legal_name: str = Field(min_length=1, max_length=200)
    postal_address: str = Field(min_length=1, max_length=500)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    privacy_contact_email: EmailStr
    dpo_contact: str | None = Field(None, max_length=320)
    supervisory_authority_name: str = Field(min_length=1, max_length=200)
    supervisory_authority_url: str = Field(min_length=1, max_length=500)
    default_locale: str = Field(default="en", pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    processor_summary: str = Field(min_length=1, max_length=5000)
    rights_summary: str = Field(min_length=1, max_length=5000)
    terms_summary: str = Field(min_length=1, max_length=5000)
    governance: dict[str, object]
    accepted_operator_policy_version: int = Field(ge=1)
    accepted_operator_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ControllerPublicationIn(BaseModel):
    external_authorisation_ref: str | None = Field(None, max_length=200)


class EventGovernanceIn(BaseModel):
    event_notice: str | None = Field(None, max_length=5000)
    enabled_optional_features: list[str] = Field(max_length=20)
    contact_routing: dict[str, object] = Field(default_factory=dict)
    operator_policy_version: int = Field(ge=1)
    controller_policy_version: int = Field(ge=1)

    @field_validator("enabled_optional_features")
    @classmethod
    def valid_features(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Optional features must be unique")
        unsupported = sorted(set(value) - OPTIONAL_FEATURES)
        if unsupported:
            raise ValueError(f"Unsupported optional features: {', '.join(unsupported)}")
        return sorted(value)


class TenancyModeIn(BaseModel):
    mode: Literal["single-controller", "hosted-multi-controller"]


def _operator_out(profile: InstanceOperatorProfile) -> dict[str, object]:
    return {
        "instance_id": profile.instance_id,
        "operator_type": profile.operator_type,
        "operator_legal_name": profile.operator_legal_name,
        "operator_postal_address": profile.operator_postal_address,
        "operator_country": profile.operator_country,
        "privacy_contact_email": profile.privacy_contact_email,
        "service_description": profile.service_description,
        "security_summary": profile.security_summary,
        "subprocessors": _json_array(profile.subprocessors_json, field="subprocessors"),
        "hosting_regions": _json_array(profile.hosting_regions_json, field="hosting regions"),
        "fixed_retention_days": profile.fixed_retention_days,
        "dpa_url": profile.dpa_url,
        "subprocessor_schedule_url": profile.subprocessor_schedule_url,
        "updated_at": profile.updated_at,
    }


def _controller_out(controller: Controller, db: Session) -> dict[str, object]:
    profile = db.get(ControllerGovernanceProfile, controller.id)
    latest = (
        db.query(ControllerGovernancePublication)
        .filter(ControllerGovernancePublication.controller_id == controller.id)
        .order_by(ControllerGovernancePublication.version.desc())
        .first()
    )
    return {
        "public_id": controller.public_id,
        "trust_entity_id": controller.trust_entity_id,
        "code": controller.code,
        "display_name": controller.display_name,
        "status": controller.status,
        "has_governance_profile": profile is not None,
        "latest_governance_version": latest.version if latest else None,
        "latest_governance_sha256": latest.content_sha256 if latest else None,
        "event_count": db.query(Event).filter(Event.controller_id == controller.id).count(),
        "created_at": controller.created_at,
        "updated_at": controller.updated_at,
    }


def _controller_by_public_id(public_id: str, db: Session) -> Controller:
    controller = db.query(Controller).filter(Controller.public_id == public_id).first()
    if controller is None:
        raise HTTPException(status_code=404, detail="Controller not found")
    return controller


@admin_router.get("/tenancy")
def get_tenancy_status(
    root: User = Depends(require_root_admin_read_only),
    db: Session = Depends(get_db),
):
    del root
    result = hosted_mode_preflight(db)
    result["configured_mode"] = tenancy_mode(db)
    return result


@admin_router.put("/tenancy/mode")
def update_tenancy_mode(
    body: TenancyModeIn,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    try:
        preflight = set_tenancy_mode(db, body.mode)
    except ValueError as exc:
        try:
            detail: object = json.loads(str(exc))
        except json.JSONDecodeError:
            detail = str(exc)
        raise HTTPException(status_code=409, detail=detail) from exc
    if body.mode == TENANCY_HOSTED:
        controllers = db.query(Controller).order_by(Controller.id).all()
        append_record(
            db,
            workflow_type="operator_tenancy",
            workflow_id=stable_instance_id(db),
            operation_type="hosted_mode_enabled",
            record_type="operator.hosted_tenancy_enabled",
            payload={
                "mode": TENANCY_HOSTED,
                "controller_public_ids": sorted(row.public_id for row in controllers),
                "event_count": db.query(Event).count(),
                "status": "enabled",
            },
        )
        for controller in controllers:
            initialise_controller_chain(db, controller.id)
    audit(
        db,
        user=root,
        action="tenancy.mode_changed",
        resource_type="instance",
        detail=json.dumps({"mode": body.mode}),
    )
    db.commit()
    return {"mode": body.mode, "preflight": preflight}


@admin_router.get("/operator")
def get_operator(
    root: User = Depends(require_root_admin_read_only),
    db: Session = Depends(get_db),
):
    del root
    profile = db.get(InstanceOperatorProfile, 1)
    if profile is None:
        raise HTTPException(status_code=404, detail="Operator profile not configured")
    return _operator_out(profile)


@admin_router.put("/operator")
def save_operator(
    body: OperatorProfileIn,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    profile = db.get(InstanceOperatorProfile, 1)
    if profile is None:
        profile = InstanceOperatorProfile(id=1, instance_id=stable_instance_id(db))
        db.add(profile)
    values = body.model_dump()
    profile.subprocessors_json = _canonical(values.pop("subprocessors"))[0]
    profile.hosting_regions_json = _canonical(values.pop("hosting_regions"))[0]
    for field, value in values.items():
        setattr(profile, field, str(value) if field == "privacy_contact_email" else value)
    audit(db, user=root, action="operator.profile_saved", resource_type="operator")
    db.commit()
    db.refresh(profile)
    return _operator_out(profile)


@admin_router.post("/operator/publications", status_code=201)
def publish_operator(
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    profile = db.get(InstanceOperatorProfile, 1)
    if profile is None:
        raise HTTPException(status_code=409, detail="Operator profile is incomplete")
    content = _operator_out(profile)
    content.pop("updated_at", None)
    content_json, content_sha = _canonical(content)
    existing = (
        db.query(OperatorPolicyPublication)
        .filter(OperatorPolicyPublication.content_sha256 == content_sha)
        .first()
    )
    if existing is not None:
        return {"version": existing.version, "sha256": existing.content_sha256, "unchanged": True}
    latest = db.query(OperatorPolicyPublication).order_by(OperatorPolicyPublication.version.desc()).first()
    publication = OperatorPolicyPublication(
        version=(latest.version + 1 if latest else 1),
        content_json=content_json,
        content_sha256=content_sha,
        source_json=content_json,
        source_sha256=content_sha,
        published_by_id=root.id,
        supersedes_version=latest.version if latest else None,
    )
    db.add(publication)
    audit(db, user=root, action="operator.policy_published", resource_type="operator")
    db.commit()
    return {"version": publication.version, "sha256": publication.content_sha256, "unchanged": False}


@admin_router.get("/controllers")
def list_controllers(
    root: User = Depends(require_root_admin_read_only),
    db: Session = Depends(get_db),
):
    del root
    return [_controller_out(row, db) for row in db.query(Controller).order_by(Controller.display_name).all()]


@admin_router.post("/controllers", status_code=201)
def create_controller(
    body: ControllerCreateIn,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    controller = Controller(
        code=body.code,
        display_name=body.display_name,
        status="draft",
        created_by_id=root.id,
    )
    db.add(controller)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Controller code already exists") from exc
    audit(
        db,
        user=root,
        action="controller.create",
        resource_type="controller",
        resource_id=controller.id,
        controller_id=controller.id,
    )
    db.commit()
    db.refresh(controller)
    return _controller_out(controller, db)


@admin_router.patch("/controllers/{public_id}")
def update_controller(
    public_id: str,
    body: ControllerUpdateIn,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    controller = _controller_by_public_id(public_id, db)
    if body.status == "retired" and db.query(Event).filter(Event.controller_id == controller.id).count():
        raise HTTPException(status_code=409, detail="A controller with events cannot be retired")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(controller, field, value)
    audit(
        db,
        user=root,
        action="controller.update",
        resource_type="controller",
        resource_id=controller.id,
        controller_id=controller.id,
    )
    db.commit()
    return _controller_out(controller, db)


@admin_router.get("/controllers/{public_id}/governance")
def get_controller_governance(
    public_id: str,
    root: User = Depends(require_root_admin_read_only),
    db: Session = Depends(get_db),
):
    del root
    controller = _controller_by_public_id(public_id, db)
    profile = db.get(ControllerGovernanceProfile, controller.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Controller governance is not configured")
    return {
        "controller_public_id": controller.public_id,
        "controller_type": profile.controller_type,
        "legal_name": profile.legal_name,
        "postal_address": profile.postal_address,
        "country": profile.country,
        "privacy_contact_email": profile.privacy_contact_email,
        "dpo_contact": profile.dpo_contact,
        "supervisory_authority_name": profile.supervisory_authority_name,
        "supervisory_authority_url": profile.supervisory_authority_url,
        "default_locale": profile.default_locale,
        "processor_summary": profile.processor_summary,
        "rights_summary": profile.rights_summary,
        "terms_summary": profile.terms_summary,
        "governance": _json_object(profile.structured_json, field="controller governance"),
        "accepted_operator_policy_version": profile.accepted_operator_policy_version,
        "accepted_operator_policy_sha256": profile.accepted_operator_policy_sha256,
    }


@admin_router.put("/controllers/{public_id}/governance")
def save_controller_governance(
    public_id: str,
    body: ControllerProfileIn,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    controller = _controller_by_public_id(public_id, db)
    operator_publication = db.query(OperatorPolicyPublication).filter(
        OperatorPolicyPublication.version == body.accepted_operator_policy_version,
        OperatorPolicyPublication.content_sha256 == body.accepted_operator_policy_sha256,
    ).first()
    if operator_publication is None:
        raise HTTPException(status_code=409, detail="Operator policy identity does not exist")
    profile = db.get(ControllerGovernanceProfile, controller.id)
    if profile is None:
        profile = ControllerGovernanceProfile(controller_id=controller.id)
        db.add(profile)
    values = body.model_dump()
    profile.structured_json = _canonical(values.pop("governance"))[0]
    for field, value in values.items():
        setattr(profile, field, str(value) if field == "privacy_contact_email" else value)
    audit(
        db,
        user=root,
        action="controller.governance_saved",
        resource_type="controller",
        resource_id=controller.id,
        controller_id=controller.id,
    )
    db.commit()
    return get_controller_governance(public_id, root, db)


@admin_router.post("/controllers/{public_id}/governance/publications", status_code=201)
def publish_controller_governance(
    public_id: str,
    body: ControllerPublicationIn,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    controller = _controller_by_public_id(public_id, db)
    profile = db.get(ControllerGovernanceProfile, controller.id)
    if profile is None:
        raise HTTPException(status_code=409, detail="Controller governance is incomplete")
    operator_publication = db.query(OperatorPolicyPublication).filter(
        OperatorPolicyPublication.version == profile.accepted_operator_policy_version,
        OperatorPolicyPublication.content_sha256 == profile.accepted_operator_policy_sha256,
    ).first()
    if operator_publication is None:
        raise HTTPException(status_code=409, detail="Accepted operator policy is unavailable")
    controller_key = db.query(EvidenceKey).filter(
        EvidenceKey.controller_id == controller.id,
        EvidenceKey.role == "controller",
        EvidenceKey.revoked_at.is_(None),
        EvidenceKey.activated_at.isnot(None),
    ).order_by(EvidenceKey.activated_at.desc()).first()
    if controller_key is None:
        raise HTTPException(status_code=409, detail="An active controller trust key is required")
    content = {
        "format": "mp-opt-controller-governance-v1",
        "template_version": POLICY_TEMPLATE_VERSION,
        "controller_public_id": controller.public_id,
        "controller_type": profile.controller_type,
        "legal_name": profile.legal_name,
        "postal_address": profile.postal_address,
        "country": profile.country,
        "privacy_contact_email": profile.privacy_contact_email,
        "dpo_contact": profile.dpo_contact,
        "supervisory_authority_name": profile.supervisory_authority_name,
        "supervisory_authority_url": profile.supervisory_authority_url,
        "default_locale": profile.default_locale,
        "processor_summary": profile.processor_summary,
        "rights_summary": profile.rights_summary,
        "terms_summary": profile.terms_summary,
        "governance": _json_object(profile.structured_json, field="controller governance"),
        "operator_policy": {
            "version": operator_publication.version,
            "sha256": operator_publication.content_sha256,
        },
    }
    content_json, content_sha = _canonical(content)
    existing = db.query(ControllerGovernancePublication).filter(
        ControllerGovernancePublication.controller_id == controller.id,
        ControllerGovernancePublication.content_sha256 == content_sha,
    ).first()
    if existing is not None:
        return {
            "version": existing.version,
            "sha256": existing.content_sha256,
            "evidence_record_sha256": existing.evidence_record_sha256,
            "unchanged": True,
        }
    latest = db.query(ControllerGovernancePublication).filter(
        ControllerGovernancePublication.controller_id == controller.id
    ).order_by(ControllerGovernancePublication.version.desc()).first()
    publication = ControllerGovernancePublication(
        controller_id=controller.id,
        version=(latest.version + 1 if latest else 1),
        content_json=content_json,
        content_sha256=content_sha,
        source_json=profile.structured_json,
        source_sha256=hashlib.sha256(profile.structured_json.encode("utf-8")).hexdigest(),
        controller_key_id=controller_key.id,
        technical_publisher_id=root.id,
        operator_policy_version=operator_publication.version,
        operator_policy_sha256=operator_publication.content_sha256,
        external_authorisation_ref=body.external_authorisation_ref,
        supersedes_version=latest.version if latest else None,
    )
    db.add(publication)
    db.flush()
    try:
        publication.evidence_record_sha256 = append_record(
            db,
            workflow_type="controller_governance",
            workflow_id=str(
                uuid.uuid5(
                    uuid.UUID(controller.public_id),
                    f"governance-publication:{publication.version}",
                )
            ),
            operation_type="published",
            record_type="controller.governance_published",
            payload={
                "controller_public_id": controller.public_id,
                "controller_key_id": controller_key.key_id,
                "policy_version": publication.version,
                "policy_sha256": publication.content_sha256,
                "document_sha256": publication.content_sha256,
                "operator_policy_version": publication.operator_policy_version,
                "operator_policy_sha256": publication.operator_policy_sha256,
                "governance_authorisation": (
                    "external_reference_recorded"
                    if publication.external_authorisation_ref
                    else "controller_trust_binding"
                ),
            },
            controller_id=controller.id,
        )
    except EvidenceUnavailable as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail={
                "code": "CONTROLLER_EVIDENCE_UNAVAILABLE",
                "message": "The governance publication was not committed because its controller evidence could not be sealed.",
            },
        ) from exc
    controller.status = "active"
    audit(
        db,
        user=root,
        action="controller.governance_published",
        resource_type="governance_publication",
        controller_id=controller.id,
    )
    db.commit()
    return {
        "version": publication.version,
        "sha256": publication.content_sha256,
        "evidence_record_sha256": publication.evidence_record_sha256,
        "unchanged": False,
    }


@admin_router.put("/events/{event_id}/governance-configuration")
def save_event_governance(
    event_id: int,
    body: EventGovernanceIn,
    root: User = Depends(require_root_recent_reauth),
    db: Session = Depends(get_db),
):
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    operator_publication = db.query(OperatorPolicyPublication).filter(
        OperatorPolicyPublication.version == body.operator_policy_version
    ).first()
    controller_publication = db.query(ControllerGovernancePublication).filter(
        ControllerGovernancePublication.controller_id == event.controller_id,
        ControllerGovernancePublication.version == body.controller_policy_version,
    ).first()
    if operator_publication is None or controller_publication is None:
        raise HTTPException(status_code=409, detail="Referenced governance publication is unavailable")
    config = db.get(EventGovernanceConfiguration, event.id)
    features_json = _canonical(body.enabled_optional_features)[0]
    routing_json = _canonical(body.contact_routing)[0]
    content_sha256 = event_governance_identity(
        event_notice=body.event_notice,
        enabled_optional_features_json=features_json,
        contact_routing_json=routing_json,
        operator_policy_version=body.operator_policy_version,
        controller_policy_version=body.controller_policy_version,
    )
    if config is None:
        config = EventGovernanceConfiguration(
            event_id=event.id,
            controller_id=event.controller_id,
            revision=1,
            content_sha256=content_sha256,
        )
        db.add(config)
    elif config.content_sha256 != content_sha256:
        config.revision += 1
        config.content_sha256 = content_sha256
    config.event_notice = body.event_notice
    config.enabled_optional_features_json = features_json
    config.contact_routing_json = routing_json
    config.operator_policy_version = body.operator_policy_version
    config.controller_policy_version = body.controller_policy_version
    config.updated_by_id = root.id
    db.commit()
    return {
        "event_id": event.id,
        "controller_public_id": db.get(Controller, event.controller_id).public_id,
        **body.model_dump(),
    }


@admin_router.get("/events/{event_id}/governance-configuration")
def get_event_governance(
    event_id: int,
    root: User = Depends(require_root_admin_read_only),
    db: Session = Depends(get_db),
):
    del root
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    config = db.get(EventGovernanceConfiguration, event.id)
    if config is None:
        raise HTTPException(status_code=404, detail="Event governance is not configured")
    controller = db.get(Controller, event.controller_id)
    return {
        "event_id": event.id,
        "event_ref": event.evidence_id,
        "event_name": event.name,
        "controller_public_id": controller.public_id if controller else None,
        "event_notice": config.event_notice,
        "event_notice_revision": config.revision,
        "event_notice_sha256": config.content_sha256,
        "enabled_optional_features": _json_array(
            config.enabled_optional_features_json,
            field="event features",
        ),
        "contact_routing": _json_object(config.contact_routing_json, field="contact routing"),
        "operator_policy_version": config.operator_policy_version,
        "controller_policy_version": config.controller_policy_version,
        "updated_at": config.updated_at,
    }


def _latest_operator(db: Session) -> OperatorPolicyPublication:
    publication = db.query(OperatorPolicyPublication).order_by(OperatorPolicyPublication.version.desc()).first()
    if publication is None:
        raise HTTPException(status_code=404, detail="Operator policy not published")
    return publication


@public_router.get("/operator")
def public_operator(db: Session = Depends(get_db)):
    publication = _latest_operator(db)
    return {
        "format": "mp-opt-operator-policy-v1",
        "version": publication.version,
        "sha256": publication.content_sha256,
        "policy": _json_object(publication.content_json, field="operator policy"),
    }


@public_router.get("/operator/versions/{version}")
def public_operator_version(version: int, db: Session = Depends(get_db)):
    publication = db.query(OperatorPolicyPublication).filter(
        OperatorPolicyPublication.version == version
    ).first()
    if publication is None:
        raise HTTPException(status_code=404, detail="Operator policy version not found")
    return {
        "format": "mp-opt-operator-policy-v1",
        "version": publication.version,
        "sha256": publication.content_sha256,
        "policy": _json_object(publication.content_json, field="operator policy"),
    }


def _public_controller_publication(
    controller: Controller,
    db: Session,
    *,
    version: int | None = None,
) -> dict[str, object]:
    query = db.query(ControllerGovernancePublication).filter(
        ControllerGovernancePublication.controller_id == controller.id
    )
    if version is None:
        publication = query.order_by(
            ControllerGovernancePublication.version.desc()
        ).first()
    else:
        publication = query.filter(
            ControllerGovernancePublication.version == version
        ).first()
    if publication is None:
        raise HTTPException(status_code=404, detail="Controller policy not published")
    return {
        "format": "mp-opt-controller-policy-v1",
        "controller_public_id": controller.public_id,
        "version": publication.version,
        "sha256": publication.content_sha256,
        "policy": _json_object(publication.content_json, field="controller policy"),
    }


@public_router.get("/controllers/{public_id}")
def public_controller(public_id: str, db: Session = Depends(get_db)):
    apply_database_tenant_context(
        db, DatabaseTenantContext(scope="public_controller_lookup")
    )
    controller = _controller_by_public_id(public_id, db)
    apply_database_tenant_context(
        db,
        DatabaseTenantContext(
            scope="public_legal", controller_id=controller.id
        ),
    )
    return _public_controller_publication(controller, db)


@public_router.get("/controllers/{public_id}/versions/{version}")
def public_controller_version(
    public_id: str,
    version: int,
    db: Session = Depends(get_db),
):
    apply_database_tenant_context(
        db, DatabaseTenantContext(scope="public_controller_lookup")
    )
    controller = _controller_by_public_id(public_id, db)
    apply_database_tenant_context(
        db,
        DatabaseTenantContext(scope="public_legal", controller_id=controller.id),
    )
    return _public_controller_publication(controller, db, version=version)


@public_router.get("/events/{event_evidence_id}")
def public_event_legal(event_evidence_id: str, db: Session = Depends(get_db)):
    apply_database_tenant_context(
        db, DatabaseTenantContext(scope="public_event_lookup")
    )
    event = db.query(Event).filter(Event.evidence_id == event_evidence_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Event legal notice not found")
    bounded_event_service_context(
        db,
        scope="public_legal",
        event_id=event.id,
        controller_id=event.controller_id,
    )
    controller = db.get(Controller, event.controller_id)
    config = db.get(EventGovernanceConfiguration, event.id)
    if controller is None or config is None:
        raise HTTPException(status_code=404, detail="Event legal notice not published")
    operator = db.query(OperatorPolicyPublication).filter(
        OperatorPolicyPublication.version == config.operator_policy_version
    ).first()
    controller_publication = db.query(ControllerGovernancePublication).filter(
        ControllerGovernancePublication.controller_id == event.controller_id,
        ControllerGovernancePublication.version == config.controller_policy_version,
    ).first()
    if operator is None or controller_publication is None:
        raise HTTPException(status_code=404, detail="Event legal notice not published")
    return {
        "format": "mp-opt-event-legal-chain-v1",
        "event": {"public_id": event.evidence_id, "name": event.name, "notice": config.event_notice},
        "controller": {
            "public_id": controller.public_id,
            "version": controller_publication.version,
            "sha256": controller_publication.content_sha256,
            "policy": _json_object(controller_publication.content_json, field="controller policy"),
        },
        "operator": {
            "version": operator.version,
            "sha256": operator.content_sha256,
            "policy": _json_object(operator.content_json, field="operator policy"),
        },
        "enabled_optional_features": _json_array(config.enabled_optional_features_json, field="event features"),
        "event_notice_revision": config.revision,
        "event_notice_sha256": config.content_sha256,
    }


@public_router.get("/events/{event_evidence_id}/privacy.html", response_class=HTMLResponse)
def public_event_privacy_html(event_evidence_id: str, db: Session = Depends(get_db)):
    value = public_event_legal(event_evidence_id, db)
    event = value["event"]
    controller = value["controller"]["policy"]
    operator = value["operator"]["policy"]
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'><title>Privacy notice</title></head><body>"
        f"<h1>{html.escape(str(event['name']))}</h1>"
        f"<h2>Legal controller</h2><p>{html.escape(str(controller['legal_name']))}</p>"
        f"<p>{html.escape(str(controller['postal_address']))}</p>"
        f"<h2>Hosting operator / processor</h2><p>{html.escape(str(operator['operator_legal_name']))}</p>"
        "<p>The hosting operator has privileged technical access for provisioning, security, support and recovery. "
        "All authenticated event accounts can see event unavailability; other events and public visitors cannot.</p>"
        f"<p>{html.escape(str(event.get('notice') or ''))}</p></body></html>"
    )
