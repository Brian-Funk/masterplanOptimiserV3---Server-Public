"""Shared tenant context, compatibility and hosted-mode safety checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.operator_evidence import TrustEvidenceError, validate_entity
from app.models.event import Event
from app.models.governance import EventGovernanceOverride, InstanceGovernanceProfile
from app.models.server_setting import ServerSetting
from app.models.tenancy import (
    Controller,
    ControllerGovernanceProfile,
    ControllerGovernancePublication,
    EventMembership,
    InstanceOperatorProfile,
    OperatorPolicyPublication,
    EventGovernanceConfiguration,
)
from app.models.deletion import DeletionCase
from app.models.evidence import EvidenceKey, ProcessorIdentity, ProcessorPolicyAcknowledgement
from app.models.user import User


TENANCY_SINGLE = "single-controller"
TENANCY_HOSTED = "hosted-multi-controller"
TENANCY_MODES = frozenset({TENANCY_SINGLE, TENANCY_HOSTED})


def event_governance_identity(
    *,
    event_notice: str | None,
    enabled_optional_features_json: str,
    contact_routing_json: str,
    operator_policy_version: int,
    controller_policy_version: int,
) -> str:
    """Digest the exact event-specific disclosure layer deterministically."""

    try:
        features = json.loads(enabled_optional_features_json)
        contact_routing = json.loads(contact_routing_json)
    except (TypeError, ValueError) as exc:
        raise ValueError("Event governance JSON is invalid") from exc
    if not isinstance(features, list) or not isinstance(contact_routing, dict):
        raise ValueError("Event governance JSON has an invalid shape")
    document = {
        "event_notice": event_notice,
        "enabled_optional_features": features,
        "contact_routing": contact_routing,
        "operator_policy_version": operator_policy_version,
        "controller_policy_version": controller_policy_version,
    }
    rendered = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


@dataclass(frozen=True)
class TenantContext:
    """Resolved authorization context for one authenticated account."""

    user_id: int
    is_root: bool
    controller_id: int | None
    event_id: int | None
    is_event_admin: bool = False
    is_issuer: bool = False
    can_edit: bool = False
    is_privacy_delegate: bool = False
    linked_person_id: int | None = None


def tenancy_mode(db: Session) -> str:
    """Return the explicitly configured tenancy mode, defaulting safely."""

    row = db.query(ServerSetting).filter(ServerSetting.key == "tenancy_mode").first()
    value = row.value if row is not None else TENANCY_SINGLE
    return value if value in TENANCY_MODES else TENANCY_SINGLE


def ensure_default_tenancy(db: Session, *, root_user_id: int | None = None) -> Controller:
    """Ensure the deterministic compatibility controller and mode exist."""

    controller = db.get(Controller, 1)
    if controller is None:
        legacy = db.get(InstanceGovernanceProfile, 1)
        controller = Controller(
            id=1,
            code="default",
            display_name=(
                legacy.controller_legal_name if legacy is not None else "Default controller"
            ),
            status="active" if legacy is not None else "draft",
            created_by_id=root_user_id,
        )
        db.add(controller)
        db.flush()

    mode = db.query(ServerSetting).filter(ServerSetting.key == "tenancy_mode").first()
    if mode is None:
        db.add(ServerSetting(key="tenancy_mode", value=TENANCY_SINGLE))
        db.flush()
    return controller


def assign_event_membership(
    db: Session,
    user: User,
    event: Event,
    *,
    is_event_admin: bool | None = None,
    is_issuer: bool | None = None,
    can_edit: bool | None = None,
    is_privacy_delegate: bool = False,
    linked_person_id: int | None = None,
) -> EventMembership:
    """Create or replace the one exact event membership for a non-root user."""

    if user.is_root_admin:
        raise ValueError("Root is outside event membership")
    membership = (
        db.query(EventMembership).filter(EventMembership.user_id == user.id).first()
    )
    if membership is None:
        membership = EventMembership(user_id=user.id)
        db.add(membership)
    membership.controller_id = event.controller_id
    membership.event_id = event.id
    membership.is_event_admin = (
        user.is_admin if is_event_admin is None else is_event_admin
    )
    membership.is_issuer = user.is_issuer if is_issuer is None else is_issuer
    membership.can_edit = user.can_edit if can_edit is None else can_edit
    membership.is_privacy_delegate = is_privacy_delegate
    membership.linked_person_id = (
        user.linked_person_id if linked_person_id is None else linked_person_id
    )
    membership.status = "active" if user.is_active else "suspended"

    # Keep the legacy columns as a compatibility projection while all callers
    # move to EventMembership. They are never an independent authority.
    user.event_id = event.id
    user.is_admin = membership.is_event_admin
    user.is_issuer = membership.is_issuer
    user.can_edit = membership.can_edit
    user.linked_person_id = membership.linked_person_id
    db.flush()
    return membership


def membership_for_user(db: Session, user: User) -> EventMembership | None:
    """Return the active exact membership; hosted mode never infers one."""

    if user.is_root_admin:
        return None
    membership = (
        db.query(EventMembership)
        .filter(
            EventMembership.user_id == user.id,
            EventMembership.status == "active",
        )
        .first()
    )
    return membership


def tenant_context(db: Session, user: User) -> TenantContext:
    """Resolve the one authorization context carried by an authenticated user."""

    if user.is_root_admin:
        return TenantContext(
            user_id=user.id,
            is_root=True,
            controller_id=None,
            event_id=None,
        )
    membership = membership_for_user(db, user)
    if membership is None:
        return TenantContext(
            user_id=user.id,
            is_root=False,
            controller_id=None,
            event_id=None,
        )
    return TenantContext(
        user_id=user.id,
        is_root=False,
        controller_id=membership.controller_id,
        event_id=membership.event_id,
        is_event_admin=membership.is_event_admin,
        is_issuer=membership.is_issuer,
        can_edit=membership.can_edit,
        is_privacy_delegate=membership.is_privacy_delegate,
        linked_person_id=membership.linked_person_id,
    )


def apply_membership_projection(db: Session, user: User) -> TenantContext:
    """Expose membership roles through legacy User attributes during migration."""

    context = tenant_context(db, user)
    if context.is_root:
        return context
    if context.event_id is None:
        # Fail closed for a non-root account without an active membership.
        user.event_id = None
        user.is_admin = False
        user.is_issuer = False
        user.can_edit = False
        user.linked_person_id = None
        return context
    user.event_id = context.event_id
    user.is_admin = context.is_event_admin
    user.is_issuer = context.is_issuer
    user.can_edit = context.can_edit
    user.linked_person_id = context.linked_person_id
    return context


def controller_for_event(db: Session, event_id: int) -> Controller | None:
    """Resolve a controller only through the event's immutable ownership FK."""

    return (
        db.query(Controller)
        .join(Event, Event.controller_id == Controller.id)
        .filter(Event.id == event_id)
        .first()
    )


def hosted_mode_preflight(db: Session) -> dict[str, object]:
    """Return bounded blockers; hosted mode may be enabled only when empty."""

    from app.core.features import hosted_feature_ceiling

    blockers: list[dict[str, object]] = []

    operator = db.get(InstanceOperatorProfile, 1)
    operator_publication = (
        db.query(OperatorPolicyPublication)
        .order_by(OperatorPolicyPublication.version.desc())
        .first()
    )
    if operator is None:
        blockers.append({"code": "operator_profile_missing"})
    if operator_publication is None:
        blockers.append({"code": "operator_policy_missing"})
    elif "legacy_combined_operator_controller" in operator_publication.content_json:
        blockers.append({"code": "operator_policy_requires_review"})
    elif not operator_publication.evidence_record_sha256:
        blockers.append({"code": "operator_policy_evidence_missing"})
    else:
        try:
            operator_document = json.loads(operator_publication.content_json)
        except (TypeError, ValueError):
            operator_document = None
        if not isinstance(operator_document, dict) or not isinstance(
            operator_document.get("supported_optional_features"), list
        ):
            blockers.append({"code": "operator_feature_policy_missing"})

    controllers = db.query(Controller).order_by(Controller.id).all()
    if not controllers:
        blockers.append({"code": "controller_missing"})
    for controller in controllers:
        try:
            validate_entity("controller", controller.trust_entity_id)
        except TrustEvidenceError:
            blockers.append({
                "code": "controller_trust_identity_invalid",
                "controller": controller.public_id,
            })
        if controller.status != "active":
            blockers.append({"code": "controller_not_active", "controller": controller.public_id})
        if db.get(ControllerGovernanceProfile, controller.id) is None:
            blockers.append({"code": "controller_profile_missing", "controller": controller.public_id})
        publication = (
            db.query(ControllerGovernancePublication)
            .filter(ControllerGovernancePublication.controller_id == controller.id)
            .order_by(ControllerGovernancePublication.version.desc())
            .first()
        )
        if publication is None:
            blockers.append({"code": "controller_policy_missing", "controller": controller.public_id})
        else:
            if not publication.evidence_record_sha256:
                blockers.append({
                    "code": "controller_policy_evidence_missing",
                    "controller": controller.public_id,
                })
            try:
                controller_document = json.loads(publication.content_json)
            except (TypeError, ValueError):
                controller_document = None
            if not isinstance(controller_document, dict) or not isinstance(
                controller_document.get("permitted_optional_features"), list
            ):
                blockers.append({
                    "code": "controller_feature_policy_missing",
                    "controller": controller.public_id,
                })

    unmapped_events = db.query(Event).filter(Event.controller_id.is_(None)).count()
    if unmapped_events:
        blockers.append({"code": "event_controller_missing", "count": unmapped_events})
    for event in db.query(Event).order_by(Event.id).all():
        config = db.get(EventGovernanceConfiguration, event.id)
        if config is None:
            blockers.append({"code": "event_governance_missing", "event_ref": event.evidence_id})
            continue
        if config.controller_id != event.controller_id:
            blockers.append({"code": "event_governance_controller_mismatch", "event_ref": event.evidence_id})
        operator_policy = db.query(OperatorPolicyPublication).filter(
            OperatorPolicyPublication.version == config.operator_policy_version
        ).first()
        controller_policy = db.query(ControllerGovernancePublication).filter(
            ControllerGovernancePublication.controller_id == event.controller_id,
            ControllerGovernancePublication.version == config.controller_policy_version,
        ).first()
        if operator_policy is None or controller_policy is None:
            blockers.append({"code": "event_governance_chain_incomplete", "event_ref": event.evidence_id})
            continue
        try:
            selected_features = set(json.loads(config.enabled_optional_features_json))
        except (TypeError, ValueError):
            selected_features = {"__invalid_feature_document__"}
        unavailable = sorted(
            selected_features - hosted_feature_ceiling(
                operator_policy,
                controller_policy,
            )
        )
        if unavailable:
            blockers.append({
                "code": "event_feature_policy_mismatch",
                "event_ref": event.evidence_id,
                "features": unavailable,
            })

    non_root_users = db.query(User).filter(User.is_root_admin.is_(False)).all()
    for user in non_root_users:
        membership = (
            db.query(EventMembership).filter(EventMembership.user_id == user.id).first()
        )
        if membership is None:
            blockers.append({"code": "user_membership_missing", "subject_ref": user.evidence_subject_id})
            continue
        event = db.get(Event, membership.event_id)
        if event is None or event.controller_id != membership.controller_id:
            blockers.append({"code": "user_membership_mismatch", "subject_ref": user.evidence_subject_id})
        elif user.event_id != membership.event_id:
            blockers.append({"code": "user_membership_projection_mismatch", "subject_ref": user.evidence_subject_id})

    for identity in db.query(ProcessorIdentity).filter(
        ProcessorIdentity.status.in_(("pending", "active"))
    ):
        event = db.get(Event, identity.event_id) if identity.event_id is not None else None
        if event is None or identity.controller_id != event.controller_id:
            blockers.append({"code": "processor_tenant_mismatch", "assignment_ref": identity.assignment_id})
    for key in db.query(EvidenceKey).filter(
        EvidenceKey.role == "processor",
        EvidenceKey.revoked_at.is_(None),
    ):
        event = db.get(Event, key.event_id) if key.event_id is not None else None
        if event is None or key.controller_id != event.controller_id:
            blockers.append({"code": "processor_key_tenant_mismatch", "key_id": key.key_id})
    for acknowledgement in db.query(ProcessorPolicyAcknowledgement):
        event = db.get(Event, acknowledgement.event_id) if acknowledgement.event_id is not None else None
        if event is None or acknowledgement.controller_id != event.controller_id:
            blockers.append({"code": "processor_policy_tenant_mismatch", "acknowledgement_ref": acknowledgement.acknowledgement_id})
    unscoped_open_deletions = db.query(DeletionCase).filter(
        DeletionCase.state.notin_(("complete", "rejected", "withdrawn")),
        DeletionCase.event_id.is_(None),
    ).count()
    if unscoped_open_deletions:
        blockers.append({"code": "deletion_case_event_scope_missing", "count": unscoped_open_deletions})

    legacy_overrides = db.query(EventGovernanceOverride).filter(
        (EventGovernanceOverride.controller_override_enabled.is_(True))
        | (EventGovernanceOverride.retention_override_days.isnot(None))
    ).count()
    if legacy_overrides:
        blockers.append({"code": "legacy_event_override_requires_review", "count": legacy_overrides})
    if settings.EVIDENCE_GIT_ARCHIVE_ENABLED:
        blockers.append({
            "code": "hosted_evidence_archive_mapping_required",
            "message": "Disable the legacy instance-wide archive until each controller has an explicit archive destination.",
        })

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        required_tables = {
            "users", "events", "audit_log", "controllers",
            "controller_governance_profiles", "controller_governance_publications",
            "event_memberships", "event_governance_configurations", "event_governance_overrides",
            "published_tasks", "published_persons", "published_person_unavailability",
            "publish_snapshots", "published_general_schedule_categories",
            "published_general_schedule_items", "general_schedule_publish_state",
            "announcements", "schedule_changes", "push_subscriptions",
            "public_schedule_links", "public_schedule_link_views", "task_edits",
            "processor_identities", "processor_policy_acknowledgements",
            "evidence_key_registration_challenges", "evidence_keys",
            "data_policy_acknowledgements", "account_processing_consents",
            "deletion_cases", "deletion_subject_scopes", "desktop_deletion_work_orders",
            "deletion_required_processors", "deletion_checklist_approvals",
            "deletion_approval_challenges",
            "controller_evidence_chain_states", "evidence_operations",
            "webauthn_credentials", "passkey_challenges", "passkey_ceremonies",
            "exchange_codes", "auth_sessions", "activation_links",
            "activation_email_deliveries",
        }
        rows = db.execute(
            text(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() AND c.relrowsecurity = TRUE "
                "AND c.relforcerowsecurity = TRUE"
            )
        ).scalars().all()
        missing = sorted(required_tables - set(rows))
        if missing:
            blockers.append({"code": "row_level_security_missing", "tables": missing})
        policy_tables = set(db.execute(text(
            "SELECT DISTINCT tablename FROM pg_policies WHERE schemaname = current_schema()"
        )).scalars().all())
        missing_policies = sorted(required_tables - policy_tables)
        if missing_policies:
            blockers.append({"code": "row_level_security_policy_missing", "tables": missing_policies})

    return {
        "format": "mp-opt-hosted-tenancy-preflight-v1",
        "ready": not blockers,
        "mode": tenancy_mode(db),
        "controller_count": len(controllers),
        "event_count": db.query(Event).count(),
        "non_root_account_count": len(non_root_users),
        "blockers": blockers,
    }


def set_tenancy_mode(db: Session, mode: str) -> dict[str, object]:
    """Set a mode only after the complete hosted preflight succeeds."""

    if mode not in TENANCY_MODES:
        raise ValueError("Unsupported tenancy mode")
    if tenancy_mode(db) == TENANCY_HOSTED and mode != TENANCY_HOSTED:
        raise ValueError(
            "Hosted tenancy cannot be downgraded in place; restore a pre-hosted snapshot instead"
        )
    preflight = hosted_mode_preflight(db)
    if mode == TENANCY_HOSTED and not preflight["ready"]:
        raise ValueError(json.dumps(preflight, sort_keys=True, separators=(",", ":")))
    row = db.query(ServerSetting).filter(ServerSetting.key == "tenancy_mode").first()
    if row is None:
        row = ServerSetting(key="tenancy_mode", value=mode)
        db.add(row)
    else:
        row.value = mode
    db.flush()
    return preflight
