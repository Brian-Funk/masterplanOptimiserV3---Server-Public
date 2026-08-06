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
    UniqueConstraint,
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


class HAProtectionOperation(Base):
    """Durable state for one mutation that must be accepted by the standby."""

    __tablename__ = "ha_protection_operations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ha_protection_idempotency_key"),
        UniqueConstraint("mutation_sequence", name="uq_ha_protection_mutation_sequence"),
        CheckConstraint(
            "state IN ('pending','accepted','indeterminate','failed','cancelled')",
            name="ck_ha_protection_state",
        ),
    )

    id = Column(String(36), primary_key=True)
    idempotency_key = Column(String(128), nullable=False)
    operation_type = Column(String(64), nullable=False, index=True)
    resource_type = Column(String(64), nullable=False, index=True)
    resource_id = Column(String(128), nullable=True, index=True)
    mutation_sequence = Column(
        BigInteger,
        nullable=False,
    )
    state = Column(String(16), nullable=False, server_default=text("'pending'"), index=True)
    stage = Column(String(24), nullable=False, server_default=text("'queued'"))
    accepted_bundle_id = Column(String(128), nullable=True)
    accepted_bundle_sha256 = Column(String(64), nullable=True)
    accepted_generation = Column(BigInteger, nullable=True)
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
