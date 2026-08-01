"""Phase D deterministic session, CSRF and browser-storage contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import json

import pytest
from fastapi import HTTPException
from starlette.responses import Response

from app.api.v1.auth import _clear_session_cookies, _set_session_cookie
from app.core import runtime_settings, security, sessions
from app.core.config import settings
from app.core.permissions import (
    ALWAYS_ALLOWED_WRITE_PATHS,
    ALWAYS_ALLOWED_WRITE_PREFIXES,
    WRITE_METHODS,
)
from server_backend.conftest import create_test_user


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "deploy" / "security" / "session_storage_contract.json"


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_controller_selected_lifetimes_match_runtime_defaults_and_bounds():
    contract = _contract()
    assert contract["format"] == "masterplan-session-storage-contract-v1"
    assert contract["controller_confirmation_required"] is True
    for selection in contract["controller_selections"]:
        metadata = runtime_settings.TUNEABLE_SETTINGS[selection["runtime_key"]]
        assert metadata["default"] == selection["default"]
        assert metadata["min"] == selection["minimum"]
        assert metadata["max"] == selection["maximum"]
        assert metadata["unit"] == selection["unit"]


def test_generated_production_cookie_profile_has_host_prefix_and_exact_attributes(monkeypatch):
    contract = _contract()
    cookie_contract = {item["id"]: item for item in contract["production_cookie_profile"]}
    monkeypatch.setattr(settings, "SESSION_COOKIE_NAME", cookie_contract["session"]["generated_name"])
    monkeypatch.setattr(settings, "CSRF_COOKIE_NAME", cookie_contract["csrf"]["generated_name"])
    monkeypatch.setattr(settings, "COOKIE_SECURE", True)
    response = Response()
    _set_session_cookie(
        response,
        "opaque-session",
        "opaque-csrf",
        datetime.now(timezone.utc) + timedelta(hours=1),
    )

    raw = [value.decode("latin-1") for name, value in response.raw_headers if name.lower() == b"set-cookie"]
    assert len(raw) == 2
    session_header = next(value for value in raw if value.startswith("__Host-mp_session="))
    csrf_header = next(value for value in raw if value.startswith("__Host-mp_csrf="))
    for header in (session_header, csrf_header):
        assert "Path=/" in header
        assert "SameSite=lax" in header
        assert "Secure" in header
        assert "Max-Age=" in header
        assert "Domain=" not in header
    max_ages = [
        int(header.split("Max-Age=", 1)[1].split(";", 1)[0])
        for header in (session_header, csrf_header)
    ]
    assert max_ages[0] == max_ages[1]
    assert 3500 <= max_ages[0] <= 3600
    assert "HttpOnly" in session_header
    assert "HttpOnly" not in csrf_header

    cleared = Response()
    _clear_session_cookies(cleared)
    cleared_headers = [
        value.decode("latin-1")
        for name, value in cleared.raw_headers
        if name.lower() == b"set-cookie"
    ]
    assert len(cleared_headers) == 2
    assert all("Max-Age=0" in header and "Path=/" in header for header in cleared_headers)


def test_csrf_policy_matches_the_enforced_write_boundary():
    csrf = _contract()["csrf"]
    assert WRITE_METHODS == set(csrf["write_methods"])
    assert ALWAYS_ALLOWED_WRITE_PATHS == set(csrf["exact_exempt_paths"])
    assert ALWAYS_ALLOWED_WRITE_PREFIXES == csrf["exempt_prefixes"]
    assert csrf["header"] == "X-CSRF-Token"
    assert csrf["comparison"] == "constant_time_exact_match"


def test_absolute_inactivity_and_revocation_transitions_deny_access(db):
    now = datetime.now(timezone.utc)
    user = create_test_user(db, username="phase.d.lifecycle")
    runtime_settings.set_value("session_ttl_hours", 8, db)
    runtime_settings.set_value("session_inactivity_minutes", 30, db)
    auth_session = sessions.create_session(user.id, db)
    token = auth_session._raw_token
    assert sessions.validate_session(token, db, update_last_seen=False) is not None

    auth_session.expires_at = now - timedelta(seconds=1)
    db.commit()
    assert sessions.validate_session(token, db, update_last_seen=False) is None

    auth_session.expires_at = now + timedelta(hours=1)
    auth_session.last_seen_at = now - timedelta(minutes=31)
    db.commit()
    assert sessions.validate_session(token, db, update_last_seen=False) is None

    auth_session.last_seen_at = now
    db.commit()
    assert sessions.revoke_session(token, db) is True
    assert sessions.validate_session(token, db, update_last_seen=False) is None


def test_recent_reauthentication_window_is_inclusive_then_fails_closed(db, monkeypatch):
    now = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(security, "datetime", FrozenDateTime)
    runtime_settings.set_value("reauth_window_minutes", 5, db)
    user = create_test_user(db, username="phase.d.reauth", is_admin=True)
    user._auth_session = SimpleNamespace(reauth_at=now - timedelta(minutes=5))
    assert security.ensure_recent_reauth(user, db) is user

    user._auth_session.reauth_at = now - timedelta(minutes=5, microseconds=1)
    with pytest.raises(HTTPException) as rejected:
        security.ensure_recent_reauth(user, db)
    assert rejected.value.status_code == 403
    assert rejected.value.detail == "Re-authentication required"


def test_every_browser_store_has_source_and_test_evidence():
    contract = _contract()
    stores = {item["id"] for item in contract["browser_stores"]}
    assert stores == {
        "cookies",
        "localStorage",
        "sessionStorage",
        "browser_history",
        "IndexedDB",
        "Cache API",
    }
    sources = {
        "localStorage": ROOT / "web" / "src" / "lib" / "offlineAccess.ts",
        "sessionStorage": ROOT / "web" / "src" / "lib" / "routeSecret.ts",
        "browser_history": ROOT / "web" / "src" / "lib" / "routeSecret.ts",
        "IndexedDB": ROOT / "web" / "src" / "lib" / "offlineCalendarCache.ts",
        "Cache API": ROOT / "web" / "public" / "sw.js",
    }
    for store, path in sources.items():
        assert path.is_file(), store
    service_worker = sources["Cache API"].read_text(encoding="utf-8")
    assert "mp-opt-app-__MP_OPT_RELEASE__" in service_worker
    assert 'url.pathname.startsWith("/api/")' not in service_worker
    assert "cache.put(event.request" not in service_worker
    assert "mp-opt-offline-" in service_worker
    assert (ROOT / "web" / "tests" / "offlineCalendarCache.test.ts").is_file()
    assert (ROOT / "web" / "tests" / "routeSecret.test.ts").is_file()
    assert (ROOT / "web" / "tests" / "serviceWorker.test.ts").is_file()
