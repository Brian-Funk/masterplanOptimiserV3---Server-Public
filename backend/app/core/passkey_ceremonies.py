"""Database-backed lifecycle helpers for WebAuthn ceremonies."""
from datetime import datetime, timedelta, timezone
import secrets

from fastapi import HTTPException
from sqlalchemy.orm import Session
from webauthn.helpers import bytes_to_base64url

from app.core import runtime_settings
from app.models.user import PasskeyCeremony


BOOTSTRAP_REGISTRATION = "bootstrap_registration"
ACTIVATION_REGISTRATION = "activation_registration"
ACCOUNT_REGISTRATION = "account_registration"
AUTHENTICATION = "authentication"
REAUTHENTICATION = "reauthentication"
DELETION_APPROVAL = "deletion_approval"
TRUST_KEY_ACTIVATION = "trust_key_activation"


def _aware(value: datetime) -> datetime:
    """Return a database datetime as timezone-aware UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def cleanup_ceremonies(db: Session) -> int:
    """Remove ceremony records one day after their expiry."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    count = (
        db.query(PasskeyCeremony)
        .filter(PasskeyCeremony.expires_at < cutoff)
        .delete(synchronize_session=False)
    )
    if count:
        db.commit()
    return count


def create_ceremony(
    challenge: bytes,
    purpose: str,
    db: Session,
    *,
    user_id: int | None = None,
    session_id: int | None = None,
    activation_link_id: int | None = None,
    ttl_minutes: int | None = None,
    action_json: str | None = None,
    action_sha256: str | None = None,
) -> PasskeyCeremony:
    """Persist one independently scoped WebAuthn ceremony."""
    cleanup_ceremonies(db)
    if ttl_minutes is None:
        ttl_minutes = runtime_settings.get_int("challenge_ttl_minutes", db)
    ceremony = PasskeyCeremony(
        id=secrets.token_urlsafe(32),
        challenge=bytes_to_base64url(challenge),
        purpose=purpose,
        user_id=user_id,
        session_id=session_id,
        activation_link_id=activation_link_id,
        action_json=action_json,
        action_sha256=action_sha256,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
    )
    db.add(ceremony)
    db.commit()
    db.refresh(ceremony)
    return ceremony


def consume_ceremony(
    ceremony_id: str,
    purpose: str,
    db: Session,
    *,
    user_id: int | None = None,
    session_id: int | None = None,
    activation_link_id: int | None = None,
) -> PasskeyCeremony:
    """Atomically claim an exact, unexpired ceremony for one verification.

    The caller must commit on either verification success or failure so a
    claimed ceremony cannot be replayed.
    """
    if not ceremony_id or len(ceremony_id) > 128:
        raise HTTPException(status_code=400, detail="Invalid passkey ceremony")

    ceremony = db.query(PasskeyCeremony).filter(PasskeyCeremony.id == ceremony_id).first()
    if ceremony is None:
        raise HTTPException(status_code=400, detail="Passkey ceremony is unknown")
    if ceremony.purpose != purpose:
        raise HTTPException(status_code=400, detail="Passkey ceremony has the wrong purpose")
    if ceremony.user_id != user_id:
        raise HTTPException(status_code=400, detail="Passkey ceremony does not match this account")
    if ceremony.session_id != session_id:
        raise HTTPException(status_code=400, detail="Passkey ceremony does not match this session")
    if ceremony.activation_link_id != activation_link_id:
        raise HTTPException(status_code=400, detail="Passkey ceremony does not match this activation")

    now = datetime.now(timezone.utc)
    if now > _aware(ceremony.expires_at):
        raise HTTPException(status_code=400, detail="Passkey ceremony has expired")
    if ceremony.consumed_at is not None:
        raise HTTPException(status_code=400, detail="Passkey ceremony has already been used")

    updated = (
        db.query(PasskeyCeremony)
        .filter(
            PasskeyCeremony.id == ceremony.id,
            PasskeyCeremony.consumed_at.is_(None),
            PasskeyCeremony.expires_at > now,
        )
        .update({"consumed_at": now}, synchronize_session=False)
    )
    if updated != 1:
        db.rollback()
        raise HTTPException(status_code=400, detail="Passkey ceremony has already been used")
    ceremony.consumed_at = now
    return ceremony
