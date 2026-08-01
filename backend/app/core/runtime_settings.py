"""
Runtime-configurable security settings.

Reads overrides from the ``server_settings`` DB table and falls back to the
static values in ``config.py`` / hard-coded defaults.  Every public getter
accepts an **optional** ``db`` session so callers that already have one can
avoid opening a second connection.
"""
from typing import Dict, Optional

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
        result[key] = {
            "value": value,
            "default": meta["default"],
            "label": meta["label"],
            "unit": meta["unit"],
            "min": meta["min"],
            "max": meta["max"],
        }
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


def set_value(key: str, value: int, db: Session) -> None:
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
    db.commit()
