"""Persistent high-availability generation record."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    String,
    func,
    text,
)

from app.db.database import Base


class HAClusterState(Base):
    """Singleton cluster identity and generation used to prevent stale writers."""

    __tablename__ = "ha_cluster_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ha_cluster_state_id_check"),
        CheckConstraint("generation >= 1", name="ha_cluster_state_generation_check"),
    )

    # This is a singleton safety record, not an entity collection. An
    # autoincrement sequence would allow a second, silently ignored row.
    id = Column(Integer, primary_key=True, autoincrement=False)
    cluster_id = Column(String(128), nullable=False)
    generation = Column(BigInteger, nullable=False)
    active_node_id = Column(String(128), nullable=False)
    maintenance = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=func.now(),
    )
