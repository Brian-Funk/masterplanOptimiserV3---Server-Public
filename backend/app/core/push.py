"""
Web Push helper  -  send push notifications to subscribed users.
Uses pywebpush with VAPID authentication.

VAPID_PRIVATE_KEY should be a base64url-encoded raw 32-byte EC private key
(the same format py-vapid and many VAPID generators output).
"""
import base64
import json
import logging
from typing import Optional

from pywebpush import webpush, WebPushException
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

# Cache the derived public key
_public_key_cache: Optional[str] = None


def _vapid_configured() -> bool:
    return bool(settings.VAPID_PRIVATE_KEY and settings.VAPID_CLAIMS_EMAIL)


def get_application_server_key() -> Optional[str]:
    """Return the VAPID public key in base64url format for the Push API."""
    global _public_key_cache
    if not _vapid_configured():
        return None
    if _public_key_cache:
        return _public_key_cache
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        # Decode raw 32-byte private scalar
        raw = base64.urlsafe_b64decode(settings.VAPID_PRIVATE_KEY + "==")
        private_key = ec.derive_private_key(
            int.from_bytes(raw, "big"),
            ec.SECP256R1(),
        )
        pub_bytes = private_key.public_key().public_bytes(
            encoding=Encoding.X962,
            format=PublicFormat.UncompressedPoint,
        )
        _public_key_cache = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode("ascii")
        return _public_key_cache
    except Exception as exc:
        logger.error("Failed to derive VAPID public key (%s)", type(exc).__name__)
        return None


def send_push(endpoint: str, p256dh: str, auth: str, payload: dict) -> bool | None:
    """Send one push, returning false only for an expired subscription."""
    if not _vapid_configured():
        return None

    try:
        # pywebpush accepts raw base64url private key string directly
        webpush(
            subscription_info={
                "endpoint": endpoint,
                "keys": {
                    "p256dh": p256dh,
                    "auth": auth,
                },
            },
            data=json.dumps(payload),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={
                "sub": settings.VAPID_CLAIMS_EMAIL
                if settings.VAPID_CLAIMS_EMAIL.startswith("mailto:")
                else f"mailto:{settings.VAPID_CLAIMS_EMAIL}"
            },
        )
        return True
    except WebPushException as exc:
        # 410 Gone or 404 = subscription expired, caller should delete
        if hasattr(exc, "response") and exc.response is not None:
            if exc.response.status_code in (404, 410):
                return False
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        logger.warning(
            "Push delivery failed (%s, status=%s)",
            type(exc).__name__,
            status_code,
        )
        return None
    except Exception as exc:
        logger.warning("Push delivery failed (%s)", type(exc).__name__)
        return None


def send_push_to_event(
    event_id: int, title: str, body: str, url: Optional[str], db: Session,
    notification_type: Optional[str] = None,
) -> int:
    """Send push to all subscribers of an event. Returns count of successful deliveries.
    Removes expired subscriptions (410/404).
    notification_type: "announcement" or "schedule" (used by SW to pick icon)."""
    from app.models.notification import PushSubscription

    if not _vapid_configured():
        logger.info("VAPID is not configured; push delivery skipped")
        return 0

    subs = db.query(PushSubscription).filter(PushSubscription.event_id == event_id).all()
    if not subs:
        return 0

    payload = {"title": title, "body": body}
    if url:
        payload["url"] = url
    if notification_type:
        payload["type"] = notification_type

    sent = 0
    expired_ids = []
    for sub in subs:
        ok = send_push(sub.endpoint, sub.p256dh, sub.auth, payload)
        if ok:
            sent += 1
        elif ok is False:
            expired_ids.append(sub.id)

    # Clean up expired subscriptions
    if expired_ids:
        db.query(PushSubscription).filter(PushSubscription.id.in_(expired_ids)).delete(synchronize_session=False)
        db.commit()

    logger.info(
        "Push delivery completed for event %s: %s/%s sent",
        event_id,
        sent,
        len(subs),
    )
    return sent
