"""Database Configuration  -  PostgreSQL via SQLAlchemy."""
from sqlalchemy import create_engine, event
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings

engine = create_engine(settings.DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


@event.listens_for(SessionLocal.class_, "before_commit")
def _require_ha_write_permit_before_commit(session) -> None:
    """Require the external lease before committing ORM mutations."""

    if not (session.new or session.dirty or session.deleted):
        return
    from app.core.ha_witness import require_write_permit

    require_write_permit()


@event.listens_for(SessionLocal.class_, "do_orm_execute")
def _require_ha_write_permit_before_bulk_write(execute_state) -> None:
    """Cover bulk ORM and explicit SQL writes which bypass the identity map."""

    statement = execute_state.statement
    mutation = execute_state.is_insert or execute_state.is_update or execute_state.is_delete
    if isinstance(statement, TextClause):
        first_word = statement.text.lstrip().split(None, 1)[0].upper() if statement.text.strip() else ""
        mutation = mutation or first_word in {
            "INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE", "CREATE", "ALTER", "DROP",
            "WITH", "CALL", "GRANT", "REVOKE", "COMMENT", "VACUUM",
        }
    if not mutation:
        return
    from app.core.ha_witness import require_write_permit

    require_write_permit()


def get_db():
    """FastAPI dependency  -  yields a DB session, auto-closed after request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
