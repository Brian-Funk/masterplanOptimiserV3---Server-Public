"""Audit log model  -  immutable record of security-relevant actions."""
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.sql import func

from app.db.database import Base


class AuditLog(Base):
    """Persisted record of security-relevant user and admin actions."""

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Legacy deployments retain a nullable username column until the migration
    # clears it. New records never populate it.
    username = Column(String, nullable=True)
    actor_ref = Column(String(36), nullable=True, index=True)
    action = Column(String(64), nullable=False, index=True)
    resource_type = Column(String(64), nullable=True)
    resource_id = Column(Integer, nullable=True)
    detail = Column(Text, nullable=True)  # canonical bounded JSON object
    ip_hash = Column(String(32), nullable=True)
    outcome = Column(String(16), default="success")  # success | denied | error
