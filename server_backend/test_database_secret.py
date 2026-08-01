from pathlib import Path
import os

import pytest

from deploy.management.database_secret import migrate


def test_legacy_database_credentials_migrate_atomically(tmp_path: Path):
    env_path = tmp_path / ".env"
    secret_path = tmp_path / "secrets" / "database_password"
    password = "safe%password-with-32-characters"
    env_path.write_text(
        "DOMAIN=mp-opt.net\n"
        f"DATABASE_URL=postgresql://masterplan:safe%25password-with-32-characters@db:5432/masterplan\n"
        f"POSTGRES_PASSWORD={password}\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)

    assert migrate(env_path, secret_path) == "migrated"

    assert secret_path.read_text(encoding="utf-8") == password
    if os.name != "nt":
        assert secret_path.stat().st_mode & 0o777 == 0o600
    assert env_path.read_text(encoding="utf-8") == "DOMAIN=mp-opt.net\n"


def test_database_secret_mismatch_preserves_both_sources(tmp_path: Path):
    env_path = tmp_path / ".env"
    secret_path = tmp_path / "secrets" / "database_password"
    secret_path.parent.mkdir()
    env_path.write_text(
        "POSTGRES_PASSWORD=a-different-password-of-safe-length\n",
        encoding="utf-8",
    )
    secret_path.write_text(
        "existing-database-password-safe-length",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="differ"):
        migrate(env_path, secret_path)

    assert "POSTGRES_PASSWORD=" in env_path.read_text(encoding="utf-8")
    assert (
        secret_path.read_text(encoding="utf-8")
        == "existing-database-password-safe-length"
    )


def test_database_secret_rejects_known_default_without_creating_file(
    tmp_path: Path,
):
    env_path = tmp_path / ".env"
    secret_path = tmp_path / "secrets" / "database_password"
    env_path.write_text("POSTGRES_PASSWORD=masterplan\n", encoding="utf-8")

    with pytest.raises(ValueError, match="known default"):
        migrate(env_path, secret_path)

    assert not secret_path.exists()
    assert env_path.read_text(encoding="utf-8") == "POSTGRES_PASSWORD=masterplan\n"


def test_backend_constructs_database_url_from_file_secret(monkeypatch):
    from app.core import config

    password = "safe password/with?reserved#characters"
    monkeypatch.setattr(
        config,
        "read_docker_secret",
        lambda name: password if name == "DATABASE_PASSWORD" else None,
    )

    resolved = config.Settings(_env_file=None, DATABASE_URL="")

    assert resolved.DATABASE_URL == (
        "postgresql://masterplan:"
        "safe%20password%2Fwith%3Freserved%23characters@db:5432/masterplan"
    )


def test_production_rejects_reused_application_and_ip_hmac_key(monkeypatch):
    from pydantic import ValidationError
    from app.core import config

    shared = "x" * 48
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(
        config,
        "read_docker_secret",
        lambda name: (
            "a-production-database-password-of-safe-length"
            if name == "DATABASE_PASSWORD"
            else None
        ),
    )

    with pytest.raises(ValidationError, match="must be separate"):
        config.Settings(
            _env_file=None,
            DATABASE_URL="",
            SECRET_KEY=shared,
            IP_HMAC_KEY=shared,
        )
