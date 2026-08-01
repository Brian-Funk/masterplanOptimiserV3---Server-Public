"""Regression checks for the minimised, bounded operational logging policy."""
import json
import logging
from pathlib import Path
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]


def test_application_access_log_is_structured_and_omits_query(caplog, user_client):
    caplog.set_level(logging.INFO, logger="api.access")
    client, _user, _event = user_client

    response = client.get("/api/v1/auth/me?activation_token=must-not-be-logged")

    assert response.status_code == 200
    records = [record for record in caplog.records if record.name == "api.access"]
    assert records
    payload = json.loads(records[-1].getMessage())
    assert set(payload) == {
        "duration_ms",
        "event",
        "method",
        "path",
        "request_id",
        "status",
        "subject_ref",
    }
    assert payload["path"] == "/api/v1/auth/me"
    UUID(payload["subject_ref"])
    assert "must-not-be-logged" not in records[-1].getMessage()


def test_runtime_disables_raw_uvicorn_access_log_and_bounds_container_logs():
    dockerfile = (ROOT / "infra" / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")

    assert '"--no-access-log"' in dockerfile
    assert "x-bounded-logging: &bounded-logging" in compose
    assert 'max-size: "10m"' in compose
    assert 'max-file: "5"' in compose
    assert compose.count("logging: *bounded-logging") == 3


def test_caddy_access_logging_is_not_enabled_by_supported_configuration():
    for name in ("Caddyfile", "Caddyfile.local", "Caddyfile.ha"):
        content = (ROOT / "infra" / name).read_text(encoding="utf-8")
        directives = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert "log" not in directives
