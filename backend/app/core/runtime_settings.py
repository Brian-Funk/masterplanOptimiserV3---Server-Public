"""
Runtime-configurable security settings.

Reads overrides from the ``server_settings`` DB table and falls back to the
static values in ``config.py`` / hard-coded defaults.  Every public getter
accepts an **optional** ``db`` session so callers that already have one can
avoid opening a second connection.
"""
from datetime import datetime, timezone
import json
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.config import settings

# ---------------------------------------------------------------------------
# Registry of tuneable keys with (env default, type, description, unit)
# ---------------------------------------------------------------------------
TUNEABLE_SETTINGS: Dict[str, dict] = {
    "session_ttl_hours":                {"default": settings.SESSION_TTL_HOURS,                 "type": int,   "label": "Session lifetime (regular)", "unit": "hours", "min": 1, "max": 720},
    "session_ttl_hours_admin":          {"default": settings.SESSION_TTL_HOURS_ADMIN,           "type": int,   "label": "Session lifetime (admin)",   "unit": "hours", "min": 1, "max": 24},
    "session_inactivity_minutes":       {"default": settings.SESSION_INACTIVITY_MINUTES,        "type": int,   "label": "Inactivity timeout",         "unit": "minutes", "min": 5, "max": 1440},
    "offline_access_ttl_hours":         {"default": 24,                                         "type": int,   "label": "Offline calendar access window", "unit": "hours", "min": 1, "max": 24},
    "reauth_window_minutes":            {"default": 5,                                          "type": int,   "label": "Re-auth validity window",    "unit": "minutes", "min": 1, "max": 30},
    "activation_link_expiry_hours":     {"default": 24,                                         "type": int,   "label": "Activation link validity",   "unit": "hours", "min": 1, "max": 168},
    "retention_revoked_sessions_days":  {"default": settings.RETENTION_REVOKED_SESSIONS_DAYS,   "type": int,   "label": "Revoked session retention",  "unit": "days", "min": 1, "max": 365},
    "retention_expired_sessions_days":  {"default": settings.RETENTION_EXPIRED_SESSIONS_DAYS,   "type": int,   "label": "Expired session retention",  "unit": "days", "min": 1, "max": 365},
    "retention_used_activation_links_days": {"default": settings.RETENTION_USED_ACTIVATION_LINKS_DAYS, "type": int, "label": "Used activation link retention", "unit": "days", "min": 1, "max": 365},
    "audit_log_retention_days":             {"default": 90,                                             "type": int, "label": "Audit log retention",             "unit": "days", "min": 30, "max": 730},
    "event_purge_grace_days":               {"default": settings.EVENT_PURGE_GRACE_DAYS,                "type": int, "label": "Event purge grace period",       "unit": "days", "min": 1, "max": 3650},
    "secret_max_age_days":                  {"default": 90,                                             "type": int, "label": "Publish secret max age",       "unit": "days", "min": 0, "max": 365},
    "max_snapshots_per_event":              {"default": 20,                                             "type": int, "label": "Max snapshots per event",     "unit": "snapshots", "min": 5, "max": 100},
    "challenge_ttl_minutes":                {"default": 5,                                              "type": int, "label": "Passkey challenge lifetime",  "unit": "minutes", "min": 1, "max": 30},
    "exchange_code_ttl_seconds":            {"default": 30,                                             "type": int, "label": "Exchange code lifetime",      "unit": "seconds", "min": 10, "max": 300},
    "reauth_challenge_ttl_minutes":         {"default": 5,                                              "type": int, "label": "Re-auth challenge lifetime",  "unit": "minutes", "min": 1, "max": 30},
    "passkey_requests_per_minute":           {"default": 60,                                             "type": int, "label": "Passkey requests per minute", "unit": "requests/minute", "min": 5, "max": 600},
    "announcements_per_event_limit":        {"default": 50,                                             "type": int, "label": "Announcements per event",     "unit": "announcements", "min": 10, "max": 500},
    "masterplan_pushes_per_minute":         {"default": 5,                                              "type": int, "label": "Masterplan pushes per minute", "unit": "pushes/minute", "min": 1, "max": 120},
    "public_schedule_pushes_per_minute":    {"default": 5,                                              "type": int, "label": "Public Schedule pushes per minute", "unit": "pushes/minute", "min": 1, "max": 120},
    "ha_replication_interval_minutes": {"default": 5, "type": int, "label": "Replication frequency", "unit": "minutes", "min": 5, "max": 1440},
}

GOVERNANCE_RUNTIME_FIELDS: dict[str, tuple[str, str]] = {
    "event_purge_grace_days": ("event_grace_days", "Event purge grace"),
    "audit_log_retention_days": ("audit_retention_days", "Audit-log retention"),
    "offline_access_ttl_hours": ("browser_cache_expiry_hours", "Offline/browser access lifetime"),
}


# ---------------------------------------------------------------------------
# DB helpers (lazy import to avoid circular deps at module level)
# ---------------------------------------------------------------------------

def _get_overrides(db: Session) -> Dict[str, str]:
    """Fetch all overrides from the DB in one query."""
    from app.models.server_setting import ServerSetting
    rows = db.query(ServerSetting.key, ServerSetting.value).all()
    return {r.key: r.value for r in rows}


def get_all(db: Session) -> Dict[str, dict]:
    """Return every tuneable setting with its current effective value and metadata."""
    overrides = _get_overrides(db)
    result = {}
    for key, meta in TUNEABLE_SETTINGS.items():
        raw = overrides.get(key)
        if raw is not None:
            try:
                value = meta["type"](raw)
                if not meta["min"] <= value <= meta["max"]:
                    value = meta["default"]
            except (ValueError, TypeError):
                value = meta["default"]
        else:
            value = meta["default"]
        item = {
            "value": value,
            "default": meta["default"],
            "label": meta["label"],
            "unit": meta["unit"],
            "min": meta["min"],
            "max": meta["max"],
        }
        if key in GOVERNANCE_RUNTIME_FIELDS:
            item["governance_managed"] = True
            item["governance_field"] = GOVERNANCE_RUNTIME_FIELDS[key][0]
        result[key] = item
    return result


def get_int(key: str, db: Session) -> int:
    """Return the effective integer value for *key*."""
    meta = TUNEABLE_SETTINGS.get(key)
    if meta is None:
        raise KeyError(f"Unknown setting: {key}")
    from app.models.server_setting import ServerSetting
    row = db.query(ServerSetting).filter(ServerSetting.key == key).first()
    if row is not None:
        try:
            v = int(row.value)
            if meta["min"] <= v <= meta["max"]:
                return v
        except (ValueError, TypeError):
            pass
    return meta["default"]


def _set_internal(db: Session, key: str, value: str) -> None:
    from app.models.server_setting import ServerSetting
    row = db.query(ServerSetting).filter(ServerSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(ServerSetting(key=key, value=value))


def apply_governance_runtime_values(structured: dict[str, Any], db: Session) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Overlay only settings the Server actually enforces onto a draft."""
    result = json.loads(json.dumps(structured))
    retention = result.setdefault("retention", {})
    changes: list[dict[str, Any]] = []
    for setting_key, (field, label) in GOVERNANCE_RUNTIME_FIELDS.items():
        effective = get_int(setting_key, db)
        previous = retention.get(field)
        retention[field] = effective
        if previous != effective:
            changes.append({
                "setting": setting_key,
                "governance_field": f"retention.{field}",
                "label": label,
                "previous": previous,
                "current": effective,
            })
    return result, changes


def _sync_governance_draft(db: Session) -> dict[str, Any]:
    from app.models.governance import GovernancePublication, InstanceGovernanceProfile

    profile = db.get(InstanceGovernanceProfile, 1)
    if profile is None:
        return {"draft_updated": False, "publication_required": False, "changes": []}
    try:
        structured = json.loads(profile.structured_json or "{}")
    except json.JSONDecodeError:
        structured = {}
    updated, changes = apply_governance_runtime_values(structured, db)
    if not changes:
        return governance_impact(db) | {"draft_updated": False, "changes": []}
    profile.structured_json = json.dumps(updated, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    existing_raw = _get_overrides(db).get("governance_runtime_changed_fields", "[]")
    try:
        existing = json.loads(existing_raw)
    except json.JSONDecodeError:
        existing = []
    by_field = {
        item.get("governance_field"): item
        for item in existing
        if isinstance(item, dict) and item.get("current") != item.get("previous")
    }
    for change in changes:
        original = by_field.get(change["governance_field"])
        if original and "previous" in original:
            change["previous"] = original["previous"]
        if change["current"] == change["previous"]:
            by_field.pop(change["governance_field"], None)
        else:
            by_field[change["governance_field"]] = change
    outstanding = list(by_field.values())
    changed_at = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        if outstanding else None
    )
    publication_required = bool(outstanding) and db.query(GovernancePublication.id).first() is not None
    _set_internal(db, "governance_runtime_draft_changed_at", changed_at or "")
    _set_internal(db, "governance_runtime_changed_fields", json.dumps(outstanding, separators=(",", ":"), sort_keys=True))
    _set_internal(db, "governance_runtime_publication_required", "true" if publication_required else "false")
    return {
        "draft_updated": bool(outstanding),
        "publication_required": publication_required,
        "changed_at": changed_at,
        "changes": outstanding,
    }


def governance_impact(db: Session) -> dict[str, Any]:
    values = _get_overrides(db)
    try:
        changes = json.loads(values.get("governance_runtime_changed_fields", "[]"))
    except json.JSONDecodeError:
        changes = []
    changes = [
        item for item in changes
        if isinstance(item, dict) and item.get("current") != item.get("previous")
    ]
    return {
        "draft_updated": bool(changes),
        "publication_required": bool(changes) and values.get("governance_runtime_publication_required") == "true",
        "changed_at": values.get("governance_runtime_draft_changed_at") or None if changes else None,
        "changes": changes,
    }


def clear_governance_impact(db: Session) -> None:
    _set_internal(db, "governance_runtime_changed_fields", "[]")
    _set_internal(db, "governance_runtime_publication_required", "false")


def set_value(key: str, value: int, db: Session) -> dict[str, Any]:
    """Persist a runtime override (upsert)."""
    meta = TUNEABLE_SETTINGS.get(key)
    if meta is None:
        raise KeyError(f"Unknown setting: {key}")
    if not (meta["min"] <= value <= meta["max"]):
        raise ValueError(f"{key} must be between {meta['min']} and {meta['max']}")
    from app.models.server_setting import ServerSetting
    row = db.query(ServerSetting).filter(ServerSetting.key == key).first()
    if row:
        row.value = str(value)
    else:
        db.add(ServerSetting(key=key, value=str(value)))
    # The governance overlay must see this exact value even when the caller's
    # session has not otherwise issued a flushing query yet.
    db.flush()
    impact = _sync_governance_draft(db) if key in GOVERNANCE_RUNTIME_FIELDS else governance_impact(db)
    db.commit()
    return impact
