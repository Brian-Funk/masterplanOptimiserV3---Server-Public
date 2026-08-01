"""Locally controlled, versioned governance configuration and acknowledgements."""

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.db.database import Base


class InstanceGovernanceProfile(Base):
    """Singleton draft containing controller-supplied deployment facts."""

    __tablename__ = "instance_governance_profile"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_instance_governance_singleton"),
        CheckConstraint(
            "controller_type IN ('organisation', 'individual')",
            name="ck_governance_controller_type",
        ),
    )

    id = Column(Integer, primary_key=True, default=1)
    instance_id = Column(String(36), nullable=False, unique=True)
    controller_type = Column(String(24), nullable=False)
    controller_legal_name = Column(String(200), nullable=False)
    controller_postal_address = Column(String(500), nullable=False)
    controller_country = Column(String(2), nullable=False)
    privacy_contact_email = Column(String(320), nullable=False)
    privacy_contact_phone = Column(String(64), nullable=True)
    dpo_contact = Column(String(320), nullable=True)
    supervisory_authority_name = Column(String(200), nullable=False)
    supervisory_authority_url = Column(String(500), nullable=False)
    default_locale = Column(String(16), nullable=False, default="en")
    processor_summary = Column(Text, nullable=False)
    retention_summary = Column(Text, nullable=False)
    rights_summary = Column(Text, nullable=False)
    terms_summary = Column(Text, nullable=False)
    # Validated JSON for purposes, data categories, processors, retention,
    # deployment features and public contact configuration. Keeping the
    # extension in one document lets existing installations migrate without
    # duplicating controller contact data across loosely coupled tables.
    structured_json = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GovernancePublication(Base):
    """Immutable copy of exactly one published governance configuration."""

    __tablename__ = "governance_publications"
    __table_args__ = (
        UniqueConstraint("version", name="uq_governance_publication_version"),
    )

    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False)
    content_json = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    source_json = Column(Text, nullable=False, default="{}")
    source_sha256 = Column(String(64), nullable=False, default="0" * 64)
    published_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    supersedes_version = Column(Integer, nullable=True)
    material_change = Column(Boolean, nullable=False, default=True)
    change_summary_json = Column(Text, nullable=False, default="[]")


class EventGovernanceOverride(Base):
    """Root-reviewed event layer for a genuinely different controller."""

    __tablename__ = "event_governance_overrides"
    __table_args__ = (
        CheckConstraint(
            "retention_override_days IS NULL OR retention_override_days BETWEEN 1 AND 3650",
            name="ck_event_governance_retention_days",
        ),
    )

    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)
    controller_override_enabled = Column(Boolean, nullable=False, default=False)
    controller_identity_override = Column(String(200), nullable=True)
    privacy_contact_override = Column(String(320), nullable=True)
    retention_override_days = Column(Integer, nullable=True)
    enabled_optional_features_json = Column(Text, nullable=False, default="[]")
    policy_version = Column(Integer, nullable=False)
    updated_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DataPolicyAcknowledgement(Base):
    """Local acknowledgement of the permitted-data policy, never consent."""

    __tablename__ = "data_policy_acknowledgements"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('instance_root', 'event_creator', 'head_organiser', "
            "'authorised_editor', 'field_visibility_administrator')",
            name="ck_data_policy_ack_scope",
        ),
        UniqueConstraint(
            "user_id", "event_id", "policy_version", "scope",
            name="uq_data_policy_acknowledgement",
        ),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=True, index=True)
    policy_version = Column(Integer, nullable=False)
    policy_sha256 = Column(String(64), nullable=False)
    scope = Column(String(48), nullable=False)
    acknowledged_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    superseded_at = Column(DateTime(timezone=True), nullable=True)
