"""PostgreSQL fixtures for real server concurrency tests."""
import sys
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

_TEST_ROOT = Path(__file__).resolve().parent.parent
_SERVER_BACKEND = _TEST_ROOT / "backend"
if str(_SERVER_BACKEND) not in sys.path:
    sys.path.insert(0, str(_SERVER_BACKEND))

from app.core.rate_limit import limiter
from app.db.database import Base, SessionLocal, engine
from app.main import app  # noqa: F401
from app.models.server_setting import ServerSetting  # noqa: F401
from app.models.governance import DataPolicyAcknowledgement, GovernancePublication, InstanceGovernanceProfile  # noqa: F401
from app.models.deletion import DeletionCase  # noqa: F401


@pytest.fixture(autouse=True)
def postgres_schema() -> Generator[None, None, None]:
    """Recreate the production schema and reset limiter state for each test."""
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL concurrency tests require DATABASE_URL")

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    limiter.reset()
    try:
        yield
    finally:
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db() -> Generator[Session, None, None]:
    """Yield a setup and assertion session separate from request sessions."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
