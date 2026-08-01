"""User and authentication models  -  ported from MasterplanOptimiserV2 Server."""
from sqlalchemy import (
    CheckConstraint, Column, Integer, String, ForeignKey, Boolean, DateTime,
    Index, JSON, LargeBinary, text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.database import Base
import uuid


class User(Base):
    """Login accounts. Passkey-only authentication."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    evidence_subject_id = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()), index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    display_name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    is_root_admin = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)      # Can manage users/events
    is_issuer = Column(Boolean, default=False)     # Scoped admin: users/announcements/history for own event
    can_edit = Column(Boolean, default=False)       # Can make web-only task edits
    is_active = Column(Boolean, default=True)
    is_activated = Column(Boolean, default=False)   # True once passkey registered via activation link
    # Nullable FK: links user to a published person (by external_person_id)
    linked_person_id = Column(Integer, nullable=True)
    # Scoped to an event (nullable for root admins who see all events)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True)
    tags = Column(JSON, nullable=True, default=list)  # List of string tags
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    deletion_requested_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class WebAuthnCredential(Base):
    """Stored passkey credentials for WebAuthn authentication."""
    __tablename__ = "webauthn_credentials"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    credential_id = Column(LargeBinary, unique=True, nullable=False, index=True)
    public_key = Column(LargeBinary, nullable=False)
    sign_count = Column(Integer, nullable=False, default=0)
    transports = Column(JSON, nullable=True)
    aaguid = Column(String, nullable=True)
    friendly_name = Column(String, nullable=True, default="Passkey")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", foreign_keys=[user_id])


class PasskeyChallenge(Base):
    """Legacy WebAuthn challenge rows kept for upgrade cleanup only."""
    __tablename__ = "passkey_challenges"

    id = Column(Integer, primary_key=True, index=True)
    challenge = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    challenge_type = Column(String, nullable=False)  # "registration" or "authentication"
    expires_at = Column(DateTime(timezone=True), nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PasskeyCeremony(Base):
    """One scoped, expiring, single-use WebAuthn ceremony.

    A separate table avoids an in-place schema patch for existing deployments.
    Ceremony IDs and challenges are independently random; neither is logged.
    """

    __tablename__ = "passkey_ceremonies"

    id = Column(String(64), primary_key=True)
    challenge = Column(String(256), unique=True, nullable=False, index=True)
    purpose = Column(String(64), nullable=False, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    session_id = Column(
        Integer,
        ForeignKey("auth_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    activation_link_id = Column(
        Integer,
        ForeignKey("activation_links.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    action_json = Column(String, nullable=True)
    action_sha256 = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ExchangeCode(Base):
    """Short-lived one-time codes exchanged for a session after passkey auth."""
    __tablename__ = "exchange_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AuthSession(Base):
    """Active login sessions (server-side session store)."""
    __tablename__ = "auth_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_token = Column(String, unique=True, nullable=False, index=True)
    csrf_token = Column(String, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True))
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    fingerprint = Column(String(64), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    reauth_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", foreign_keys=[user_id])


class ActivationLink(Base):
    """One-time activation links for passkey registration.

    Only ONE active link per user at a time (enforced in application logic).
    Token is stored as a SHA-256 hash.
    """
    __tablename__ = "activation_links"

    id = Column(Integer, primary_key=True, index=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    purpose = Column(String, nullable=False, default="initial_setup")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    invalidated_at = Column(DateTime(timezone=True), nullable=True)
    delivery_pending = Column(Boolean, nullable=False, default=False)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    created_by = relationship("User", foreign_keys=[created_by_id])


class ActivationEmailDelivery(Base):
    """Non-secret record of an administrator-triggered activation email."""

    __tablename__ = "activation_email_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('sending', 'accepted', 'failed', 'unknown', 'not_attempted')",
            name="ck_activation_email_delivery_status",
        ),
        CheckConstraint(
            "purpose IN ('initial_setup', 'additional_passkey', 'credential_reset')",
            name="ck_activation_email_delivery_purpose",
        ),
        Index(
            "uq_activation_email_delivery_sending_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'sending'"),
            sqlite_where=text("status = 'sending'"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    activation_link_id = Column(
        Integer,
        ForeignKey("activation_links.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    retry_of_id = Column(
        Integer,
        ForeignKey("activation_email_deliveries.id", ondelete="SET NULL"),
        nullable=True,
    )
    recipient_email = Column(String(320), nullable=False)
    purpose = Column(String(32), nullable=False, default="initial_setup")
    message_id = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="sending", index=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(String(255), nullable=True)
    includes_qr = Column(Boolean, nullable=False, default=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    activation_link = relationship("ActivationLink", foreign_keys=[activation_link_id])
    user = relationship("User", foreign_keys=[user_id])
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    retry_of = relationship(
        "ActivationEmailDelivery",
        remote_side=[id],
        foreign_keys=[retry_of_id],
    )
