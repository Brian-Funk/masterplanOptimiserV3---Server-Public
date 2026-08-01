"""Current non-identifying compliance evidence and recovery inventory."""

import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class EvidenceKey(Base):
    """A typed public trust key. Private controller/processor bytes never enter Server."""

    __tablename__ = "evidence_keys"
    __table_args__ = (
        CheckConstraint(
            "role IN ('instance','controller','processor')",
            name="ck_evidence_key_role",
        ),
        CheckConstraint(
            "revocation_reason IS NULL OR revocation_reason IN "
            "('retired','lost','compromised','role_changed')",
            name="ck_evidence_key_revocation_reason",
        ),
    )

    id = Column(Integer, primary_key=True)
    key_id = Column(String(19), nullable=False, unique=True, index=True)
    public_key = Column(Text, nullable=False)
    public_key_sha256 = Column(String(64), nullable=False, unique=True)
    instance_id = Column(String(36), nullable=False, index=True)
    entity_id = Column(String(64), nullable=True, index=True)
    algorithm = Column(String(16), nullable=False, default="Ed25519")
    role = Column(String(32), nullable=False, default="instance")
    valid_from = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revocation_reason = Column(String(32), nullable=True)
    supersedes_key_id = Column(String(19), nullable=True, index=True)
    superseded_by_key_id = Column(String(19), nullable=True)
    registration_proof_sha256 = Column(String(64), nullable=True)
    root_credential_id_sha256 = Column(String(64), nullable=True)
    root_action_sha256 = Column(String(64), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    trust_declaration_sha256 = Column(String(64), nullable=True)
    registered_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EvidenceKeyRegistrationChallenge(Base):
    """Short-lived entity and action-bound proof-of-possession challenge."""

    __tablename__ = "evidence_key_registration_challenges"
    __table_args__ = (
        CheckConstraint("purpose IN ('register','rotate')", name="ck_evidence_key_challenge_purpose"),
        CheckConstraint(
            "role IN ('controller','processor')",
            name="ck_evidence_key_challenge_role",
        ),
        CheckConstraint(
            "rotation_reason IS NULL OR rotation_reason IN ('routine','lost','compromised')",
            name="ck_evidence_key_challenge_rotation_reason",
        ),
    )

    id = Column(Integer, primary_key=True)
    challenge_id = Column(String(36), nullable=False, unique=True, default=_uuid)
    purpose = Column(String(16), nullable=False)
    instance_id = Column(String(36), nullable=False, index=True)
    entity_id = Column(String(64), nullable=False, index=True)
    public_key = Column(Text, nullable=False)
    public_key_sha256 = Column(String(64), nullable=False)
    key_id = Column(String(19), nullable=False, index=True)
    role = Column(String(32), nullable=False)
    supersedes_key_id = Column(String(19), nullable=True)
    rotation_reason = Column(String(32), nullable=True)
    challenge_json = Column(Text, nullable=False)
    challenge_sha256 = Column(String(64), nullable=False, unique=True)
    action_sha256 = Column(String(64), nullable=False, index=True)
    possession_proof_sha256 = Column(String(64), nullable=True)
    previous_proof_sha256 = Column(String(64), nullable=True)
    root_ceremony_id = Column(String(64), nullable=True, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RootActionAuthorisation(Base):
    """Permanent public record of one exact root-passkey-authorised action."""

    __tablename__ = "root_action_authorisations"
    __table_args__ = (
        CheckConstraint("role = 'root_passkey'", name="ck_root_action_role"),
    )

    id = Column(Integer, primary_key=True)
    authorisation_id = Column(String(36), nullable=False, unique=True, default=_uuid)
    instance_id = Column(String(36), nullable=False, index=True)
    root_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    credential_id_sha256 = Column(String(64), nullable=False, index=True)
    role = Column(String(32), nullable=False, default="root_passkey")
    algorithm = Column(String(32), nullable=False, default="WebAuthn")
    action_sha256 = Column(String(64), nullable=False, unique=True, index=True)
    action_json = Column(Text, nullable=False)
    server_verified_at = Column(DateTime(timezone=True), nullable=False)
    instance_record_sha256 = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EvidenceChainState(Base):
    """Singleton cache of the authoritative signed filesystem chain."""

    __tablename__ = "evidence_chain_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_evidence_chain_singleton"),
        CheckConstraint("evidence_mode = 'required'", name="ck_evidence_mode"),
    )

    id = Column(Integer, primary_key=True, default=1)
    instance_id = Column(String(36), nullable=False, unique=True)
    chain_id = Column(String(36), nullable=False, unique=True, default=_uuid)
    evidence_mode = Column(String(16), nullable=False, default="required")
    last_sequence = Column(Integer, nullable=False, default=0)
    head_sha256 = Column(String(64), nullable=True)
    initialised_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    verified_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class EvidenceOperation(Base):
    """Restart-safe outbox entry coordinating DB state and signed files."""

    __tablename__ = "evidence_operations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending','appended','complete','failed')",
            name="ck_evidence_operation_state",
        ),
        UniqueConstraint(
            "workflow_type", "workflow_id", "operation_type",
            name="uq_evidence_operation",
        ),
    )

    id = Column(Integer, primary_key=True)
    operation_id = Column(String(36), nullable=False, unique=True, default=_uuid)
    record_id = Column(String(36), nullable=False, unique=True, default=_uuid)
    workflow_type = Column(String(32), nullable=False)
    workflow_id = Column(String(36), nullable=False, index=True)
    operation_type = Column(String(64), nullable=False)
    record_type = Column(String(64), nullable=True)
    payload_json = Column(Text, nullable=False)
    management_audit_tail_sha256 = Column(String(64), nullable=True)
    state = Column(String(16), nullable=False, default="pending", index=True)
    record_sha256 = Column(String(64), nullable=True)
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BackupInventoryRecord(Base):
    """Complete non-secret inventory of known portable recovery packages."""

    __tablename__ = "backup_inventory_records"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','superseded_pending_deletion','confirmed_deleted','expired')",
            name="ck_backup_inventory_status",
        ),
    )

    id = Column(Integer, primary_key=True)
    package_id = Column(String(36), nullable=False, unique=True, default=_uuid, index=True)
    package_sha256 = Column(String(64), nullable=False)
    archive_sha256 = Column(String(64), nullable=True)
    recovery_key_id = Column(String(19), nullable=True)
    status = Column(String(40), nullable=False, default="active", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    verified_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    delete_after = Column(DateTime(timezone=True), nullable=True)
    replacement_package_id = Column(String(36), nullable=True)
    deletion_resolution_sha256 = Column(String(64), nullable=True)


class PrivacyActionReceipt(Base):
    """Minimal durable tombstone preventing stale restoration."""

    __tablename__ = "privacy_action_receipts"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('subject_delete','event_delete')",
            name="ck_privacy_action_type",
        ),
    )

    id = Column(Integer, primary_key=True)
    privacy_action_id = Column(String(36), nullable=False, unique=True, default=_uuid)
    sequence = Column(Integer, nullable=False, unique=True)
    instance_id = Column(String(36), nullable=False, index=True)
    event_ref = Column(String(36), nullable=False, index=True)
    subject_ref = Column(String(36), nullable=True, index=True)
    action_type = Column(String(24), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    local_applied_at = Column(DateTime(timezone=True), nullable=True)
    peer_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    retain_until = Column(DateTime(timezone=True), nullable=False)
    witness_receipt_sha256 = Column(String(64), nullable=True)


class EvidenceArchiveSubmission(Base):
    """Durable, non-sensitive state for optional private Git archival."""

    __tablename__ = "evidence_archive_submissions"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending','verifying','uploading','awaiting_checks',"
            "'awaiting_merge','verified','failed','blocked','requires_controller_action')",
            name="ck_evidence_archive_state",
        ),
        UniqueConstraint(
            "repository_id", "instance_id", "chain_head_sha256",
            name="uq_evidence_archive_chain_head",
        ),
    )

    id = Column(Integer, primary_key=True)
    submission_id = Column(String(40), nullable=False, unique=True, index=True)
    repository_id = Column(String(128), nullable=False, index=True)
    controller_id = Column(String(20), nullable=False, index=True)
    instance_id = Column(String(36), nullable=False, index=True)
    bundle_id = Column(String(36), nullable=False, index=True)
    bundle_sha256 = Column(String(64), nullable=False, unique=True, index=True)
    chain_head_sha256 = Column(String(64), nullable=False, index=True)
    bundle_path = Column(Text, nullable=False)
    state = Column(String(32), nullable=False, default="pending", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)
    lease_owner = Column(String(64), nullable=True, index=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    failure_reason = Column(String(64), nullable=True)
    branch_name = Column(String(180), nullable=True)
    base_sha = Column(String(64), nullable=True)
    pull_request_number = Column(Integer, nullable=True)
    pull_request_head_sha = Column(String(64), nullable=True)
    merge_commit_sha = Column(String(64), nullable=True)
    archive_record_sha256 = Column(String(64), nullable=True)
    checks_started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
