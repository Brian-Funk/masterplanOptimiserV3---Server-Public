"""Key-value store for runtime-configurable server settings."""
from sqlalchemy import Column, Integer, String, DateTime, func

from app.db.database import Base


class ServerSetting(Base):
    """Runtime-configurable server setting stored in the database."""

    __tablename__ = "server_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, nullable=False, index=True)
    value = Column(String, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
