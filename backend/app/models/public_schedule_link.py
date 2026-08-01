"""Models for reusable token-based Public Schedule links."""

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.db.database import Base


class PublicScheduleLink(Base):
    """A reusable, expiring link to selected Public Schedule views."""

    __tablename__ = "public_schedule_links"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(
        Integer,
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    description = Column(String(256), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    invalidated_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class PublicScheduleLinkView(Base):
    """One Public Schedule view permitted by a sharing link."""

    __tablename__ = "public_schedule_link_views"
    __table_args__ = (
        UniqueConstraint(
            "link_id",
            "external_view_id",
            name="uq_public_schedule_link_view",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    link_id = Column(
        Integer,
        ForeignKey("public_schedule_links.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_view_id = Column(Integer, nullable=False, index=True)
    view_name_snapshot = Column(String(256), nullable=False)
    sort_order_snapshot = Column(Float, nullable=False, default=0)

