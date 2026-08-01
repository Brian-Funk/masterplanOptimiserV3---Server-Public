"""Durable, non-identifying retention-scheduler status."""

from sqlalchemy import CheckConstraint, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.db.database import Base


class RetentionSchedulerState(Base):
    """Singleton status row for root monitoring and restart evidence."""

    __tablename__ = "retention_scheduler_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_retention_scheduler_singleton"),
    )

    id = Column(Integer, primary_key=True, autoincrement=False, default=1)
    cycle_count = Column(Integer, nullable=False, default=0)
    last_started_at = Column(DateTime(timezone=True), nullable=True)
    last_completed_at = Column(DateTime(timezone=True), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_result = Column(String(24), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    last_counts_json = Column(Text, nullable=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
