"""Migrate legacy database credentials into a protected password file.

The command never prints the credential. It validates all legacy sources,
requires them to agree, installs the secret atomically, then removes the
password-bearing settings from the dotenv file.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import tempfile
from urllib.parse import unquote, urlparse


KNOWN_DEFAULTS = {
    "masterplan",
    "password",
    "changeme",
    "CHANGE_ME",
    "CHANGE_ME_STRONG_PASSWORD",
}


def _read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("The configuration file is missing or unsafe")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    values: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.rstrip("\r\n").split("=", 1)
        if key in {"DATABASE_URL", "POSTGRES_PASSWORD"}:
            if key in values:
                raise ValueError(f"Duplicate legacy database setting: {key}")
            values[key] = value
    return lines, values


def _password_from_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg2"}:
        raise ValueError("Legacy DATABASE_URL is not PostgreSQL")
    if parsed.password is None:
        raise ValueError("Legacy DATABASE_URL does not contain a password")
    return unquote(parsed.password)


def _validate_password(value: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError("The database password contains a line break")
    if value in KNOWN_DEFAULTS or value.lower() in {
        "masterplan",
        "password",
        "changeme",
    }:
        raise ValueError("The database password is a known default")
    if not 24 <= len(value.encode("utf-8")) <= 512:
        raise ValueError("The database password must contain 24 to 512 bytes")


def migrate(env_path: Path, secret_path: Path) -> str:
    lines, legacy = _read_env(env_path)
    candidates: list[str] = []
    if "POSTGRES_PASSWORD" in legacy:
        candidates.append(legacy["POSTGRES_PASSWORD"])
    if "DATABASE_URL" in legacy:
        candidates.append(_password_from_url(legacy["DATABASE_URL"]))

    existing: str | None = None
    if secret_path.exists():
        if not secret_path.is_file() or secret_path.is_symlink():
            raise ValueError("The database password file is unsafe")
        existing = secret_path.read_text(encoding="utf-8")
        _validate_password(existing)
    if not candidates and existing is None:
        raise ValueError(
            "No protected database password or matching legacy credential was found"
        )
    for candidate in candidates:
        _validate_password(candidate)
        if existing is not None and candidate != existing:
            raise ValueError(
                "Legacy database credentials differ from the protected password file"
            )
    if len(set(candidates)) > 1:
        raise ValueError("Legacy database credentials do not match")

    created = existing is None
    if created:
        secret_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        password = candidates[0]
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".database_password.", dir=secret_path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as target:
                target.write(password)
                target.flush()
                os.fsync(target.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, secret_path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    if legacy:
        original = env_path.stat()
        retained = [
            line
            for line in lines
            if not line.startswith("DATABASE_URL=")
            and not line.startswith("POSTGRES_PASSWORD=")
        ]
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".env.database-migration.", dir=env_path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as target:
                target.writelines(retained)
                target.flush()
                os.fsync(target.fileno())
            os.chmod(temporary_name, stat.S_IMODE(original.st_mode))
            try:
                chown = os.chown
            except AttributeError:
                chown = None
            try:
                if chown is not None:
                    chown(temporary_name, original.st_uid, original.st_gid)
            except PermissionError:
                pass
            os.replace(temporary_name, env_path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    if legacy:
        return "migrated"
    return "created" if created else "ready"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True, type=Path)
    parser.add_argument("--secret", required=True, type=Path)
    args = parser.parse_args()
    result = migrate(args.env, args.secret)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
