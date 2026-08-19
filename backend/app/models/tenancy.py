"""First-class operator, controller and event-membership tenancy models."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.orm.attributes import NO_VALUE, NEVER_SET

from app.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _controller_trust_entity() -> str:
    """Return a public, non-secret trust identity for one controller."""

    return f"ctl-{uuid.uuid4().hex[:16]}"


class InstanceOperatorProfile(Base):
    """Singleton identity and service policy for the hosting operator."""

    __tablename__ = "instance_operator_profiles"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_instance_operator_profile_singleton"),
        CheckConstraint(
            "operator_type IN ('organisation', 'individual')",
            name="ck_instance_operator_type",
        ),
        CheckConstraint(
            "fixed_retention_days BETWEEN 1 AND 3650",
            name="ck_instance_operator_retention",
        ),
    )

    id = Column(Integer, primary_key=True, default=1)
    instance_id = Column(String(36), nullable=False, unique=True)
    operator_type = Column(String(24), nullable=False)
    operator_legal_name = Column(String(200), nullable=False)
    operator_postal_address = Column(String(500), nullable=False)
    operator_country = Column(String(2), nullable=False)
    privacy_contact_email = Column(String(320), nullable=False)
    service_description = Column(Text, nullable=False)
    security_summary = Column(Text, nullable=False)
    subprocessors_json = Column(Text, nullable=False, default="[]")
    hosting_regions_json = Column(Text, nullable=False, default="[]")
    fixed_retention_days = Column(Integer, nullable=False)
    dpa_url = Column(String(500), nullable=True)
    subprocessor_schedule_url = Column(String(500), nullable=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OperatorPolicyPublication(Base):
    """Immutable operator/service publication independently versioned from controllers."""

    __tablename__ = "operator_policy_publications"
    __table_args__ = (
        UniqueConstraint("version", name="uq_operator_policy_publication_version"),
    )

    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False)
    content_json = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False, unique=True)
    source_json = Column(Text, nullable=False, default="{}")
    source_sha256 = Column(String(64), nullable=False)
    published_by_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    published_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    supersedes_version = Column(Integer, nullable=True)


class Controller(Base):
    """One legal controller owning one or more events."""

    __tablename__ = "controllers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'suspended', 'retired')",
            name="ck_controller_status",
        ),
    )

    id = Column(Integer, primary_key=True)
    public_id = Column(String(36), nullable=False, unique=True, default=_uuid, index=True)
    trust_entity_id = Column(
        String(52), nullable=False, unique=True, default=_controller_trust_entity, index=True
    )
    code = Column(String(64), nullable=False, unique=True, index=True)
    display_name = Column(String(200), nullable=False)
    status = Column(String(16), nullable=False, default="draft", index=True)
    created_by_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_controller_created_by",
            use_alter=True,
        ),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ControllerGovernanceProfile(Base):
    """Editable legal/governance facts belonging to exactly one controller."""

    __tablename__ = "controller_governance_profiles"
    __table_args__ = (
        CheckConstraint(
            "controller_type IN ('organisation', 'individual')",
            name="ck_controller_governance_type",
        ),
    )

    controller_id = Column(
        Integer,
        ForeignKey("controllers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    controller_type = Column(String(24), nullable=False)
    legal_name = Column(String(200), nullable=False)
    postal_address = Column(String(500), nullable=False)
    country = Column(String(2), nullable=False)
    privacy_contact_email = Column(String(320), nullable=False)
    dpo_contact = Column(String(320), nullable=True)
    supervisory_authority_name = Column(String(200), nullable=False)
    supervisory_authority_url = Column(String(500), nullable=False)
    default_locale = Column(String(16), nullable=False, default="en")
    processor_summary = Column(Text, nullable=False)
    rights_summary = Column(Text, nullable=False)
    terms_summary = Column(Text, nullable=False)
    structured_json = Column(Text, nullable=False, default="{}")
    accepted_operator_policy_version = Column(Integer, nullable=True)
    accepted_operator_policy_sha256 = Column(String(64), nullable=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ControllerGovernancePublication(Base):
    """Immutable governance publication for one controller."""

    __tablename__ = "controller_governance_publications"
    __table_args__ = (
        UniqueConstraint(
            "controller_id", "version", name="uq_controller_governance_version"
        ),
        UniqueConstraint(
            "controller_id", "content_sha256", name="uq_controller_governance_content"
        ),
    )

    id = Column(Integer, primary_key=True)
    controller_id = Column(
        Integer,
        ForeignKey("controllers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(Integer, nullable=False)
    content_json = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    source_json = Column(Text, nullable=False, default="{}")
    source_sha256 = Column(String(64), nullable=False)
    controller_key_id = Column(
        Integer, ForeignKey("evidence_keys.id", ondelete="SET NULL"), nullable=True
    )
    technical_publisher_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    operator_policy_version = Column(Integer, nullable=False)
    operator_policy_sha256 = Column(String(64), nullable=False)
    external_authorisation_ref = Column(String(200), nullable=True)
    evidence_record_sha256 = Column(String(64), nullable=True)
    published_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    supersedes_version = Column(Integer, nullable=True)
    legacy_publication_id = Column(
        Integer,
        ForeignKey(
            "governance_publications.id",
            ondelete="SET NULL",
            name="fk_controller_governance_legacy_publication",
            use_alter=True,
        ),
        nullable=True,
        unique=True,
    )


class EventGovernanceConfiguration(Base):
    """Only the facts and optional features genuinely specific to one event."""

    __tablename__ = "event_governance_configurations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["event_id", "controller_id"],
            ["events.id", "events.controller_id"],
            name="fk_event_governance_event_controller",
            ondelete="CASCADE",
        ),
    )

    event_id = Column(Integer, primary_key=True)
    controller_id = Column(Integer, nullable=False, index=True)
    event_notice = Column(Text, nullable=True)
    enabled_optional_features_json = Column(Text, nullable=False, default="[]")
    contact_routing_json = Column(Text, nullable=False, default="{}")
    operator_policy_version = Column(Integer, nullable=False)
    controller_policy_version = Column(Integer, nullable=False)
    # Monotonic identity of the event-specific disclosure layer.  The mutable
    # row remains the current configuration, while every activation stores the
    # exact revision and digest it acknowledged in its immutable document.
    revision = Column(Integer, nullable=False, default=1)
    content_sha256 = Column(String(64), nullable=False)
    updated_by_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EventMembership(Base):
    """The single event authorization boundary for one non-root account."""

    __tablename__ = "event_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_event_membership_user"),
        UniqueConstraint("id", "event_id", name="uq_event_membership_id_event"),
        ForeignKeyConstraint(
            ["event_id", "controller_id"],
            ["events.id", "events.controller_id"],
            name="fk_event_membership_event_controller",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('active', 'suspended', 'revoked')",
            name="ck_event_membership_status",
        ),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    controller_id = Column(Integer, nullable=False, index=True)
    event_id = Column(Integer, nullable=False, index=True)
    is_event_admin = Column(Boolean, nullable=False, default=False)
    is_issuer = Column(Boolean, nullable=False, default=False)
    can_edit = Column(Boolean, nullable=False, default=False)
    is_privacy_delegate = Column(Boolean, nullable=False, default=False)
    linked_person_id = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False, default="active", index=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def _immutable_controller_identity(target, value, oldvalue, initiator):
    """Keep controller URLs and trust anchors stable after persistence."""

    del initiator
    if (
        target.id is not None
        and oldvalue not in (NO_VALUE, NEVER_SET, None)
        and value != oldvalue
    ):
        raise ValueError("Controller public trust identity is immutable")
    return value


for _identity_attribute in (Controller.public_id, Controller.trust_entity_id, Controller.code):
    sqlalchemy_event.listen(
        _identity_attribute,
        "set",
        _immutable_controller_identity,
        retval=True,
        active_history=True,
    )
