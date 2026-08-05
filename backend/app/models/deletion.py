"""Strict, non-identifying deletion cases and desktop work orders."""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.db.database import Base


class DeletionCase(Base):
    """One current-schema case retained independently of live personal data."""

    __tablename__ = "deletion_cases"
    __table_args__ = (
        CheckConstraint(
            "state IN ('submitted','under_review','accepted','rejected','withdrawn',"
            "'access_revoked','awaiting_desktop_report','ready_for_live_purge',"
            "'live_purge_in_progress','live_data_purged','peer_replication_pending',"
            "'peer_replication_confirmed','awaiting_clean_backup','clean_backup_verified',"
            "'awaiting_backup_resolution','restricted_retention','awaiting_checklist',"
            "'awaiting_approvals','ready_for_completion','complete','failed')",
            name="ck_deletion_case_state",
        ),
        CheckConstraint(
            "case_type IN ('personal_data_erasure','event_erasure')",
            name="ck_deletion_case_type",
        ),
        CheckConstraint(
            "initiation_reason IN ('authenticated_request','external_controller',"
            "'manual_root','retention_schedule')",
            name="ck_deletion_case_initiation_reason",
        ),
    )

    id = Column(Integer, primary_key=True)
    request_id = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()), index=True)
    case_type = Column(String(32), nullable=False, default="personal_data_erasure", index=True)
    initiation_reason = Column(String(32), nullable=False, default="authenticated_request")
    # A unique non-identifying key prevents duplicate whole-event cases across
    # concurrent scheduler workers and survives deletion of the live event row.
    event_purge_key = Column(String(36), nullable=True, unique=True, index=True)
    instance_id = Column(String(36), nullable=False, index=True)
    event_evidence_id = Column(String(36), nullable=False, index=True)
    # Operator convenience only. It is excluded from signed evidence and
    # cleared when the case completes.
    event_display_name = Column(String(128), nullable=True)
    subject_display_name = Column(String(128), nullable=True)
    subject_evidence_id = Column(String(36), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    request_type = Column(String(32), nullable=False, default="full_erasure")
    identity_verification = Column(String(48), nullable=False, default="recent_passkey_reauthentication")
    verification_method = Column(String(48), nullable=True)
    state = Column(String(48), nullable=False, default="submitted", index=True)
    submitted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    normal_response_due_at = Column(DateTime(timezone=True), nullable=False)
    decision_at = Column(DateTime(timezone=True), nullable=True)
    response_sent_at = Column(DateTime(timezone=True), nullable=True)
    decision_code = Column(String(64), nullable=True)
    access_revoked_at = Column(DateTime(timezone=True), nullable=True)
    request_manifest_sha256 = Column(String(64), nullable=True)
    acceptance_receipt_sha256 = Column(String(64), nullable=True)
    access_revocation_receipt_sha256 = Column(String(64), nullable=True)
    live_data_purged_at = Column(DateTime(timezone=True), nullable=True)
    live_purge_receipt_sha256 = Column(String(64), nullable=True)
    privacy_action_id = Column(String(36), nullable=True, unique=True)
    privacy_action_sequence = Column(Integer, nullable=True)
    peer_confirmation_sha256 = Column(String(64), nullable=True)
    peer_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    peer_replication_job_id = Column(String(128), nullable=True)
    peer_bundle_id = Column(String(128), nullable=True)
    peer_bundle_sha256 = Column(String(64), nullable=True)
    peer_generation = Column(Integer, nullable=True)
    peer_accepted_at = Column(DateTime(timezone=True), nullable=True)
    replacement_package_id = Column(String(36), nullable=True)
    replacement_package_sha256 = Column(String(64), nullable=True)
    clean_backup_job_id = Column(String(36), nullable=True)
    clean_backup_receipt_id = Column(String(36), nullable=True)
    backup_resolution_sha256 = Column(String(64), nullable=True)
    desktop_report_sha256 = Column(String(64), nullable=True)
    desktop_absence_receipt_sha256 = Column(String(64), nullable=True)
    desktop_deletion_required = Column(Boolean, nullable=False, default=True)
    backup_not_applicable_sha256 = Column(String(64), nullable=True)
    checklist_version = Column(Integer, nullable=True)
    checklist_json = Column(Text, nullable=True)
    checklist_sha256 = Column(String(64), nullable=True, unique=True)
    checklist_created_at = Column(DateTime(timezone=True), nullable=True)
    executor_approval_sha256 = Column(String(64), nullable=True)
    status_capability_sha256 = Column(String(64), nullable=True, unique=True)
    status_capability_expires_at = Column(DateTime(timezone=True), nullable=True)
    retention_reason_code = Column(String(64), nullable=True)
    retention_review_at = Column(DateTime(timezone=True), nullable=True)
    outstanding_actions_json = Column(Text, nullable=True)
    final_receipt_sha256 = Column(String(64), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DeletionSubjectScope(Base):
    """One pseudonymous event or subject scope covered by a deletion case."""

    __tablename__ = "deletion_subject_scopes"
    __table_args__ = (
        UniqueConstraint("case_id", "event_ref", "subject_ref", name="uq_deletion_scope"),
        CheckConstraint(
            "state IN ('pending','desktop_deleted','server_deleted','complete','failed')",
            name="ck_deletion_scope_state",
        ),
    )

    id = Column(Integer, primary_key=True)
    scope_id = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(
        Integer,
        ForeignKey("deletion_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_ref = Column(String(36), nullable=False, index=True)
    subject_ref = Column(String(36), nullable=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True)
    state = Column(String(24), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class DesktopDeletionWorkOrder(Base):
    """Event-scoped deletion instruction claimed by one paired desktop."""

    __tablename__ = "desktop_deletion_work_orders"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "event_ref", "subject_ref", "processor_entity_id",
            name="uq_desktop_work_order_scope",
        ),
        CheckConstraint(
            "state IN ('open','claimed','report_received','cancelled','failed')",
            name="ck_desktop_work_order_state",
        ),
        CheckConstraint(
            "operation IN ('delete_subject','delete_event')",
            name="ck_desktop_work_order_operation",
        ),
    )

    id = Column(Integer, primary_key=True)
    work_order_id = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()), index=True)
    case_id = Column(
        Integer,
        ForeignKey("deletion_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_id = Column(Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True)
    event_ref = Column(String(36), nullable=False, index=True)
    subject_ref = Column(String(36), nullable=True, index=True)
    processor_entity_id = Column(String(64), nullable=False, index=True)
    processor_key_id = Column(String(19), nullable=False, index=True)
    operation = Column(String(24), nullable=False)
    state = Column(String(24), nullable=False, default="open", index=True)
    claim_capability_sha256 = Column(String(64), nullable=True, unique=True)
    claim_expires_at = Column(DateTime(timezone=True), nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    report_json = Column(Text, nullable=True)
    report_sha256 = Column(String(64), nullable=True, unique=True)
    report_signature_sha256 = Column(String(64), nullable=True, unique=True)
    report_evidence_package_json = Column(Text, nullable=True)
    report_evidence_package_sha256 = Column(String(64), nullable=True, unique=True)
    copy_resolution_sha256 = Column(String(64), nullable=True, unique=True)
    copy_resolution_signature_sha256 = Column(String(64), nullable=True, unique=True)
    copy_resolution_evidence_package_json = Column(Text, nullable=True)
    copy_resolution_evidence_package_sha256 = Column(String(64), nullable=True, unique=True)
    reported_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DeletionRequiredProcessor(Base):
    """Immutable processor assignment captured when a case is accepted."""

    __tablename__ = "deletion_required_processors"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "event_ref", "processor_entity_id",
            name="uq_deletion_required_processor",
        ),
        CheckConstraint(
            "state IN ('awaiting_desktop','deletion_received','complete','blocked')",
            name="ck_deletion_required_processor_state",
        ),
    )

    id = Column(Integer, primary_key=True)
    requirement_id = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(Integer, ForeignKey("deletion_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    event_ref = Column(String(36), nullable=False, index=True)
    processor_entity_id = Column(String(64), nullable=False, index=True)
    snapshotted_key_id = Column(String(19), nullable=False)
    snapshotted_public_key_sha256 = Column(String(64), nullable=False)
    deletion_receipt_sha256 = Column(String(64), nullable=True)
    copy_resolution_sha256 = Column(String(64), nullable=True)
    completed_key_id = Column(String(19), nullable=True)
    completed_public_key_sha256 = Column(String(64), nullable=True)
    state = Column(String(24), nullable=False, default="awaiting_desktop", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class DeletionChecklistApproval(Base):
    """A passkey-verified human approval bound to an immutable checklist."""

    __tablename__ = "deletion_checklist_approvals"
    __table_args__ = (
        UniqueConstraint("case_id", "checklist_sha256", "role", name="uq_deletion_approval_role"),
        CheckConstraint(
            "role IN ('executor','controller','processor')",
            name="ck_deletion_approval_role",
        ),
    )

    id = Column(Integer, primary_key=True)
    approval_id = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    case_id = Column(
        Integer,
        ForeignKey("deletion_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    checklist_sha256 = Column(String(64), nullable=False, index=True)
    role = Column(String(24), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    credential_sha256 = Column(String(64), nullable=False)
    approval_sha256 = Column(String(64), nullable=False, unique=True)
    approved_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DeletionApprovalChallenge(Base):
    """Single-use WebAuthn ceremony context for one checklist role."""

    __tablename__ = "deletion_approval_challenges"
    __table_args__ = (
        CheckConstraint(
            "role IN ('executor','controller','processor')",
            name="ck_deletion_approval_challenge_role",
        ),
    )

    id = Column(Integer, primary_key=True)
    challenge_id = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    ceremony_id = Column(
        String(64),
        ForeignKey("passkey_ceremonies.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    case_id = Column(
        Integer,
        ForeignKey("deletion_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    checklist_sha256 = Column(String(64), nullable=False)
    role = Column(String(24), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
