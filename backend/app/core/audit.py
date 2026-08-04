"""
Audit helper  -  single function to record security-relevant actions.

Usage::

    from app.core.audit import audit
    audit(db, user=current_user, action="event.create", resource_type="event",
          resource_id=new_event.id, request=request)

The entry is added to the session but NOT committed  -  the caller's
existing transaction will include it.
"""
import json
import re
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.user import User
from app.core.sessions import _hash_ip


AUDIT_ACTIONS = frozenset({
    "activation.create", "activation.create_batch", "activation.email_accepted",
    "activation.email_attempt", "activation.email_failed", "activation.email_not_attempted",
    "activation.invalidate", "activation.invalidate_all", "activation.qr_download",
    "announcement.create", "announcement.delete", "auth.login", "auth.logout",
    "auth.reauth", "auth.reauth_failed", "auth.session_revoke", "calendar.commit",
    "calendar.task_edit", "calendar.task_revert", "event.create", "event.import_setup",
    "commissioning.recovery_completed", "commissioning.completed",
    "event.regenerate_secret", "evidence.export", "evidence.initialise", "evidence.verify",
    "evidence.trust_key.challenge", "evidence.trust_key.proof_verified",
    "evidence.trust_key.root_authorised", "evidence.trust_key.register",
    "evidence.trust_key.rotate", "evidence.trust_key.revoke",
    "evidence.git_anchor.import", "evidence.archive_retry",
    "gdpr.accept_deletion", "gdpr.accept_event_erasure", "gdpr.clean_backup_receipt_applied",
    "gdpr.clean_backup_requested", "gdpr.create_checklist", "gdpr.create_event_erasure",
    "gdpr.advance_deletion", "gdpr.confirm_deletion_completion",
    "gdpr.desktop_absence_confirmed", "gdpr.no_controlled_backups_confirmed",
    "gdpr.delete", "gdpr.dismiss_deletion", "gdpr.export", "gdpr.finalise_deletion",
    "gdpr.peer_replication_confirmed", "gdpr.peer_replication_requested",
    "gdpr.purge_event_live_data", "gdpr.purge_live", "gdpr.request_deletion",
    "gdpr.resolve_backups", "gdpr.resolve_outstanding_actions", "gdpr.withdraw_deletion",
    "general_schedule.publish", "governance.data_policy_acknowledged",
    "governance.draft_saved", "governance.event_override_saved",
    "governance.published", "ha.replication_requested",
    "history.delete", "history.restore", "history.update", "passkey.auth_failed",
    "passkey.bootstrap", "passkey.bootstrap_failed", "passkey.delete",
    "passkey.duplicate_denied", "passkey.register", "passkey.registration_failed",
    "passkey.rename", "recovery.bootstrap_download_confirmed",
    "public_schedule_link.create", "public_schedule_link.delete",
    "public_schedule_link.invalidate", "public_schedule_link.update", "publish.data",
    "settings.email_test", "settings.update", "user.create", "user.create_bulk",
    "user.delete_unused_invitation", "user.link_person", "user.tags_bulk_update",
    "user.update", "web_edit.revert", "web_edit.revert_bulk",
})
AUDIT_RESOURCE_TYPES = frozenset({
    "activation_email_delivery", "activation_link", "announcement", "credential",
    "deletion_request", "event", "evidence", "governance_publication", "ha_cluster",
    "instance", "public_schedule_link", "publish_snapshot", "published_task",
    "settings", "user",
})
AUDIT_OUTCOMES = frozenset({"success", "denied", "error"})
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FORBIDDEN_FIELD_PARTS = frozenset({
    "body", "cookie", "email", "header", "name", "passkey", "password",
    "recipient", "secret", "token", "username",
})


def _validate_detail_value(value: Any, *, depth: int = 0) -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        if len(value) > 256 or "\r" in value or "\n" in value:
            raise ValueError("audit detail strings must be short single-line values")
        return
    if depth > 4:
        raise ValueError("audit detail nesting is too deep")
    if isinstance(value, list):
        if len(value) > 500:
            raise ValueError("audit detail lists are too large")
        for item in value:
            _validate_detail_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 64:
            raise ValueError("audit detail objects contain too many fields")
        for key, item in value.items():
            if not isinstance(key, str) or not _FIELD_NAME.fullmatch(key):
                raise ValueError("audit detail field names must be bounded identifiers")
            lowered = key.lower()
            if any(part in lowered for part in _FORBIDDEN_FIELD_PARTS):
                raise ValueError(f"audit detail field is not permitted: {key}")
            _validate_detail_value(item, depth=depth + 1)
        return
    raise ValueError("audit detail values must be JSON scalars, lists or objects")


def _canonical_detail(detail: Optional[str]) -> Optional[str]:
    if detail is None:
        return None
    try:
        value = json.loads(detail)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("audit detail must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("audit detail must be a JSON object")
    value.setdefault("schema_version", 1)
    if value["schema_version"] != 1:
        raise ValueError("unsupported audit detail schema version")
    _validate_detail_value(value)
    rendered = json.dumps(value, separators=(",", ":"), sort_keys=True)
    if len(rendered.encode("utf-8")) > 8192:
        raise ValueError("audit detail exceeds the bounded size")
    return rendered


def audit(
    db: Session,
    *,
    user: Optional[User],
    action: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[int] = None,
    detail: Optional[str] = None,
    request: Optional[Request] = None,
    outcome: str = "success",
) -> AuditLog:
    """Create one schema-bound, minimised audit entry (uncommitted)."""
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"unsupported audit action: {action}")
    if resource_type is not None and resource_type not in AUDIT_RESOURCE_TYPES:
        raise ValueError(f"unsupported audit resource type: {resource_type}")
    if outcome not in AUDIT_OUTCOMES:
        raise ValueError(f"unsupported audit outcome: {outcome}")
    ip_hash = None
    if request is not None:
        ip_hash = _hash_ip(request.client.host if request.client else None)

    entry = AuditLog(
        user_id=user.id if user else None,
        username=None,
        actor_ref=user.evidence_subject_id if user else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=_canonical_detail(detail),
        ip_hash=ip_hash,
        outcome=outcome,
    )
    db.add(entry)
    return entry
