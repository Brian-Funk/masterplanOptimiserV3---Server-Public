"""
Activation-link helpers  -  ported from MasterplanOptimiserV2 Server.
Tokens stored as SHA-256 hashes so a DB dump never reveals a usable token.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.user import ActivationEmailDelivery, ActivationLink, User
from app.core import runtime_settings


INITIAL_SETUP = "initial_setup"
ADDITIONAL_PASSKEY = "additional_passkey"
CREDENTIAL_RESET = "credential_reset"
ActivationPurpose = Literal[
    "initial_setup", "additional_passkey", "credential_reset"
]
ManagedPasskeyPurpose = Literal["additional_passkey", "credential_reset"]


class ActivationDeliveryInProgressError(RuntimeError):
    """Raised when manual link creation would disrupt an SMTP hand-off."""


def hash_token(token: str) -> str:
    """Return the hex-digest SHA-256 of *token*."""
    return hashlib.sha256(token.encode()).hexdigest()


def resolve_activation_purpose(
    *,
    is_activated: bool,
    requested: ManagedPasskeyPurpose | None,
) -> ActivationPurpose:
    """Resolve a safe link purpose from account state and an optional request.

    Pending accounts always use initial setup. Active accounts retain the
    historical reset default when an older client omits the purpose.
    """

    if not is_activated:
        if requested is not None:
            raise ValueError(
                "Credential-management links require an activated account"
            )
        return INITIAL_SETUP
    return requested or CREDENTIAL_RESET


def create_activation_link(
    user_id: int,
    created_by_id: int,
    db: Session,
    purpose: str = "initial_setup",
    expiry_hours: int | None = None,
    delivery_pending: bool = False,
    permit_email_delivery_start: bool = False,
) -> Tuple[str, ActivationLink]:
    """Create an activation link for a user.

    Automatically invalidates any previous active link for the same user.
    Email links may be held pending until SMTP acceptance is confirmed. Only
    the email delivery workflow may set ``permit_email_delivery_start`` so a
    manual link cannot invalidate a token while its email is being handed off.
    Returns (raw_token, link_row).
    """
    if expiry_hours is None:
        expiry_hours = runtime_settings.get_int("activation_link_expiry_hours", db)
    if purpose not in {INITIAL_SETUP, ADDITIONAL_PASSKEY, CREDENTIAL_RESET}:
        raise ValueError("Unsupported activation purpose")
    now = datetime.now(timezone.utc)

    # Serialise concurrent link creation for one user.
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if user is None:
        raise ValueError("User not found")
    if not permit_email_delivery_start and db.query(ActivationEmailDelivery).filter(
        ActivationEmailDelivery.user_id == user_id,
        ActivationEmailDelivery.status == "sending",
    ).first() is not None:
        raise ActivationDeliveryInProgressError(
            "An activation email is already being handed off for this user"
        )

    # Invalidate all previous active links for this user
    db.query(ActivationLink).filter(
        ActivationLink.user_id == user_id,
        ActivationLink.used_at.is_(None),
        ActivationLink.invalidated_at.is_(None),
    ).update({"invalidated_at": now}, synchronize_session="fetch")

    raw_token = secrets.token_urlsafe(48)
    link = ActivationLink(
        token_hash=hash_token(raw_token),
        user_id=user_id,
        purpose=purpose,
        expires_at=now + timedelta(hours=expiry_hours),
        delivery_pending=delivery_pending,
        created_by_id=created_by_id,
    )
    db.add(link)
    db.flush()

    return raw_token, link


def validate_activation_token(
    token: str,
    db: Session,
    *,
    for_update: bool = False,
) -> Optional[ActivationLink]:
    """Look up a token and return the link row if it is still valid."""
    from app.core.database_tenancy import authentication_service_context

    authentication_service_context(db)
    hashed = hash_token(token)
    query = db.query(ActivationLink).filter(ActivationLink.token_hash == hashed)
    if for_update:
        query = query.with_for_update()
    link = query.first()
    if link is None:
        return None

    now = datetime.now(timezone.utc)
    expires = link.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    if now > expires:
        return None
    if link.used_at is not None:
        return None
    if link.invalidated_at is not None:
        return None
    if link.delivery_pending:
        return None

    return link


def mark_link_used(link: ActivationLink, db: Session) -> None:
    """Mark an activation link as used."""
    if link.used_at is not None or link.invalidated_at is not None:
        raise ValueError("Activation link is no longer available")
    link.used_at = datetime.now(timezone.utc)
    db.commit()
