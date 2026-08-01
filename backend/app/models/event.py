"""Event model  -  top-level container for a published masterplan."""
from sqlalchemy import CheckConstraint, Column, Integer, String, Date, DateTime, Text
from sqlalchemy.sql import func
from app.db.database import Base
import uuid


class Event(Base):
    """Server-side event that receives published schedules."""

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "purge_grace_days IS NULL OR purge_grace_days BETWEEN 1 AND 3650",
            name="ck_event_purge_grace_days",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    evidence_id = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()), index=True)
    name = Column(String, nullable=False)
    location = Column(String, nullable=True)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    status = Column(String, default="draft")  # draft | published | purge_pending
    # SHA-256 hash of the publish secret. Raw secret shown once on creation.
    publish_secret_hash = Column(String(64), nullable=False, unique=True, index=True)
    metadata_json = Column(Text, nullable=True)  # Arbitrary JSON metadata
    # Legacy compatibility fields. Logo colour customisation is no longer used.
    logo_color_1 = Column(String, nullable=True)
    logo_color_2 = Column(String, nullable=True)
    secret_created_at = Column(DateTime(timezone=True), nullable=True)

    # The controller-selected deadline is materialised so later setting changes
    # cannot silently move an existing event's deletion date. The case pointer
    # is assigned atomically with the signed request at the grace boundary.
    purge_grace_days = Column(Integer, nullable=True)
    purge_due_at = Column(DateTime(timezone=True), nullable=True, index=True)
    purge_case_request_id = Column(String(36), nullable=True, unique=True, index=True)
    purge_started_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
