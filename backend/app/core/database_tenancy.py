"""Transaction-local PostgreSQL tenant context used by row-level security."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class DatabaseTenantContext:
    """Bounded database authorization context for one transaction."""

    scope: str = "deny"
    user_id: int | None = None
    event_id: int | None = None
    controller_id: int | None = None
    is_root: bool = False


DENY_CONTEXT = DatabaseTenantContext()


def _values(context: DatabaseTenantContext) -> dict[str, str]:
    return {
        "scope": context.scope,
        "user_id": str(context.user_id or ""),
        "event_id": str(context.event_id or ""),
        "controller_id": str(context.controller_id or ""),
        "is_root": "true" if context.is_root else "false",
    }


def apply_database_tenant_context(
    db: Session, context: DatabaseTenantContext
) -> None:
    """Persist context on the Session and apply it to the current transaction."""

    db.info["mp_opt_tenant_context"] = context
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    values = _values(context)
    db.execute(
        text(
            "SELECT "
            "set_config('mp_opt.scope', :scope, true), "
            "set_config('mp_opt.user_id', :user_id, true), "
            "set_config('mp_opt.event_id', :event_id, true), "
            "set_config('mp_opt.controller_id', :controller_id, true), "
            "set_config('mp_opt.is_root', :is_root, true)"
        ),
        values,
    )


def database_tenant_context(db: Session) -> DatabaseTenantContext:
    value = db.info.get("mp_opt_tenant_context", DENY_CONTEXT)
    return value if isinstance(value, DatabaseTenantContext) else DENY_CONTEXT


def authenticated_subject_context(db: Session, user_id: int) -> None:
    """Permit authentication to load only the user owning the validated session."""

    apply_database_tenant_context(
        db, DatabaseTenantContext(scope="authenticated", user_id=user_id)
    )


def authentication_service_context(db: Session) -> None:
    """Permit only the bounded, pre-authentication credential ceremonies.

    Opaque tokens and credential identifiers must be resolved before a user or
    event is known. Keeping this as an explicit scope prevents ordinary API
    code from inheriting broad access to authentication tables.
    """

    apply_database_tenant_context(
        db, DatabaseTenantContext(scope="authentication_service")
    )


def authenticated_user_context(
    db: Session,
    *,
    user_id: int,
    event_id: int | None,
    controller_id: int | None,
    is_root: bool,
) -> None:
    apply_database_tenant_context(
        db,
        DatabaseTenantContext(
            scope="authenticated",
            user_id=user_id,
            event_id=event_id,
            controller_id=controller_id,
            is_root=is_root,
        ),
    )


def bounded_event_service_context(
    db: Session,
    *,
    scope: str,
    event_id: int,
    controller_id: int,
) -> None:
    """Authorize one publisher/public/worker operation for exactly one event."""

    apply_database_tenant_context(
        db,
        DatabaseTenantContext(
            scope=scope,
            event_id=event_id,
            controller_id=controller_id,
        ),
    )


def bounded_event_id_context(
    db: Session, *, scope: str, event_id: int
):
    """Resolve one event without ever opening another tenant's content rows."""

    from app.models.event import Event

    apply_database_tenant_context(
        db, DatabaseTenantContext(scope=scope, event_id=event_id)
    )
    event = db.get(Event, event_id)
    if event is None:
        return None
    bounded_event_service_context(
        db,
        scope=scope,
        event_id=event.id,
        controller_id=event.controller_id,
    )
    return event


def root_service_context(db: Session, *, scope: str) -> None:
    """Authorize an explicit trusted host worker across the complete instance."""

    apply_database_tenant_context(
        db, DatabaseTenantContext(scope=scope, is_root=True)
    )
