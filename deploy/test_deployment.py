#!/usr/bin/env python3
"""Plan and record reproducible unsigned test deployments.

This module deliberately contains no Docker or SSH code.  It provides the
strict input validation and conservative change classification shared by the
VPS supervisor and GitHub Actions.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys


COMMIT = re.compile(r"^[0-9a-f]{40}$")
TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
COMPONENTS = (
    "backend",
    "frontend",
    "database",
    "caddy",
    "tools",
    "witness",
    "operations",
)


@dataclass(frozen=True)
class ChangePlan:
    base: str
    target: str
    files: tuple[str, ...]
    components: tuple[str, ...]
    migrations: bool
    full: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "format": "mp-opt-test-plan-v1",
            "base": self.base,
            "target": self.target,
            "files": list(self.files),
            "components": list(self.components),
            "migrations": self.migrations,
            "full": self.full,
        }


def require_commit(value: str) -> str:
    if not COMMIT.fullmatch(value):
        raise ValueError("commit must be exactly 40 lowercase hexadecimal characters")
    return value


def require_tag(value: str) -> str:
    if not TAG.fullmatch(value):
        raise ValueError("release tag must use canonical form vMAJOR.MINOR.PATCH")
    return value


def classify_path(path: str) -> set[str]:
    """Return every affected component; unknown files force operations/full."""

    result: set[str] = set()
    if path.startswith("backend/") or path == "infra/Dockerfile":
        result.add("backend")
    if path.startswith("server_backend/") or path in {"conftest.py", "pyproject.toml"}:
        result.add("backend")
    if path == "web/public/logo_normal.png":
        result.add("backend")
    if path.startswith("web/"):
        result.add("frontend")
    if path.startswith("server_postgres/"):
        result.update({"backend", "database"})
    if path.startswith("deploy/migrations/") or path.startswith("backend/app/models/") or path in {
        "infra/Dockerfile.postgres",
        "backend/app/db/database.py",
    }:
        result.add("database")
    if path in {"infra/Caddyfile", "infra/Caddyfile.ha", "infra/Caddyfile.local"} \
            or path == "infra/Dockerfile.caddy" \
            or path.startswith("infra/caddy-dns-witness/"):
        result.add("caddy")
    if path == "infra/Dockerfile.tools":
        result.add("tools")
    if path.startswith("infra/cloudflare-ha-witness/"):
        result.add("witness")
    if path in {"manage.sh", "configure-production.sh", ".env.example"} \
            or path.startswith("deploy/") \
            or path.startswith("infra/docker-compose"):
        result.add("operations")
    if path.startswith("docs/"):
        result.add("operations")
    if path.startswith(".github/workflows/"):
        result.add("operations")
    if path.endswith(".md"):
        result.add("operations")
    if path == ".dockerignore":
        result.update(COMPONENTS)
    if not result:
        result.update(COMPONENTS)
    return result


def plan_from_files(base: str, target: str, files: list[str]) -> ChangePlan:
    require_commit(base)
    require_commit(target)
    clean_files = tuple(sorted({item.strip() for item in files if item.strip()}))
    components: set[str] = set()
    for path in clean_files:
        components.update(classify_path(path))
    migrations = any(path.startswith("deploy/migrations/") for path in clean_files)
    full = migrations or bool(components - {"backend"})
    return ChangePlan(
        base=base,
        target=target,
        files=clean_files,
        components=tuple(item for item in COMPONENTS if item in components),
        migrations=migrations,
        full=full,
    )


def git_plan(repo: Path, base: str, target: str) -> ChangePlan:
    require_commit(base)
    require_commit(target)
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", base, target, "--"],
        check=True,
        capture_output=True,
        text=True,
    )
    return plan_from_files(base, target, result.stdout.splitlines())


def atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    classify = subparsers.add_parser("classify")
    classify.add_argument("--repo", type=Path, required=True)
    classify.add_argument("--base", required=True)
    classify.add_argument("--target", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("kind", choices=("commit", "tag"))
    validate.add_argument("value")
    args = parser.parse_args()
    try:
        if args.command == "classify":
            print(json.dumps(git_plan(args.repo, args.base, args.target).as_dict(), sort_keys=True))
        elif args.kind == "commit":
            print(require_commit(args.value))
        else:
            print(require_tag(args.value))
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
