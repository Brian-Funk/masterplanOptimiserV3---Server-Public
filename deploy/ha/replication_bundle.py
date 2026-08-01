#!/usr/bin/env python3
"""Build and validate versioned point-in-time replication manifests."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import uuid


FORMAT = "mp-opt-replication-v1"
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
LOCAL_ENV_KEYS = {"DATABASE_URL", "POSTGRES_PASSWORD"}
ALLOWED_SECRET_FILES = {
    "secret_key", "vapid_private_key", "root_bootstrap_token", "smtp_token",
    "evidence_signing_key", "ip_hmac_key",
}
RECOVERY_STATE_FORMAT = "mp-opt-manual-recovery-export-v1"
RECOVERY_STATE_PATH = "recovery/manual-recovery-export.json"
SNAPSHOT_NAME = re.compile(
    r"^[0-9]{8}T[0-9]{6}Z_(?:database|secrets|full)_[A-Za-z0-9._-]{1,64}$"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
RECOVERY_KEY_ID = re.compile(r"^rk-[0-9a-f]{16}$")


def normalise_privacy_assertion(value: object) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "workflow_type",
        "workflow_id",
        "privacy_action_id",
        "privacy_action_sequence",
        "live_purge_receipt_sha256",
    }:
        raise ValueError("Privacy assertion fields are invalid")
    if value.get("workflow_type") != "deletion_case":
        raise ValueError("Privacy assertion workflow type is invalid")
    for field in ("workflow_id", "privacy_action_id"):
        parsed = uuid.UUID(str(value.get(field, "")))
        if str(parsed) != value.get(field):
            raise ValueError(f"Privacy assertion {field} is invalid")
    sequence = value.get("privacy_action_sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise ValueError("Privacy assertion sequence is invalid")
    digest = value.get("live_purge_receipt_sha256")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise ValueError("Privacy assertion purge receipt is invalid")
    return dict(value)


def is_local_env_key(key: str) -> bool:
    return key in LOCAL_ENV_KEYS or key.startswith("HA_")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value and not path.is_absolute() and ".." not in path.parts and "" not in path.parts)


def timestamp(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Recovery state {label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Recovery state {label} is invalid") from exc
    if parsed.tzinfo is None or parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError(f"Recovery state {label} is invalid")
    return value


def previous_export(value: object) -> dict:
    if value == {}:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Recovery state previous confirmation is invalid")
    allowed = {"snapshot", "confirmed_at", "package_id", "package_sha256", "recovery_key_id"}
    if set(value) not in {frozenset(allowed), frozenset(allowed - {"package_id"})}:
        raise ValueError("Recovery state previous confirmation contains unexpected fields")
    snapshot = value.get("snapshot")
    package_hash = value.get("package_sha256")
    key_id = value.get("recovery_key_id")
    package_id = value.get("package_id")
    if not isinstance(snapshot, str) or not SNAPSHOT_NAME.fullmatch(snapshot):
        raise ValueError("Recovery state previous snapshot is invalid")
    if not isinstance(package_hash, str) or not SHA256.fullmatch(package_hash):
        raise ValueError("Recovery state previous package hash is invalid")
    if not isinstance(key_id, str) or not RECOVERY_KEY_ID.fullmatch(key_id):
        raise ValueError("Recovery state previous key id is invalid")
    if package_id is not None:
        parsed_package_id = uuid.UUID(str(package_id))
        if str(parsed_package_id) != package_id:
            raise ValueError("Recovery state previous package id is invalid")
    result = {
        "snapshot": snapshot,
        "confirmed_at": timestamp(value.get("confirmed_at"), "previous confirmed_at"),
        "package_sha256": package_hash,
        "recovery_key_id": key_id,
    }
    if package_id is not None:
        result["package_id"] = package_id
    return result


def normalise_recovery_state(document: object) -> dict:
    """Return only the public, schema-bound workstation-export receipt."""

    if not isinstance(document, dict) or document.get("format") != RECOVERY_STATE_FORMAT:
        raise ValueError("Recovery state format is invalid")
    state = document.get("state")
    if state == "operator-sha256-confirmed":
        allowed = {
            "format", "state", "snapshot", "confirmed_at", "package_format",
            "package_id", "package_sha256", "package_size", "archive_sha256", "recovery_key_id",
        }
        if set(document) not in {frozenset(allowed), frozenset(allowed - {"package_id"})}:
            raise ValueError("Confirmed recovery state contains unexpected fields")
        snapshot = document.get("snapshot")
        package_hash = document.get("package_sha256")
        archive_hash = document.get("archive_sha256")
        key_id = document.get("recovery_key_id")
        package_size = document.get("package_size")
        package_id = document.get("package_id")
        if not isinstance(snapshot, str) or not SNAPSHOT_NAME.fullmatch(snapshot):
            raise ValueError("Confirmed recovery snapshot is invalid")
        if document.get("package_format") != "mp-opt-portable-snapshot-2026-01":
            raise ValueError("Confirmed recovery package format is invalid")
        if not isinstance(package_hash, str) or not SHA256.fullmatch(package_hash):
            raise ValueError("Confirmed recovery package hash is invalid")
        if not isinstance(archive_hash, str) or not SHA256.fullmatch(archive_hash):
            raise ValueError("Confirmed recovery archive hash is invalid")
        if not isinstance(key_id, str) or not RECOVERY_KEY_ID.fullmatch(key_id):
            raise ValueError("Confirmed recovery key id is invalid")
        if not isinstance(package_size, int) or isinstance(package_size, bool) or package_size < 1:
            raise ValueError("Confirmed recovery package size is invalid")
        if package_id is not None:
            parsed_package_id = uuid.UUID(str(package_id))
            if str(parsed_package_id) != package_id:
                raise ValueError("Confirmed recovery package id is invalid")
        result = {
            "format": RECOVERY_STATE_FORMAT,
            "state": state,
            "snapshot": snapshot,
            "confirmed_at": timestamp(document.get("confirmed_at"), "confirmed_at"),
            "package_format": "mp-opt-portable-snapshot-2026-01",
            "package_sha256": package_hash,
            "package_size": package_size,
            "archive_sha256": archive_hash,
            "recovery_key_id": key_id,
        }
        if package_id is not None:
            result["package_id"] = package_id
        return result
    if state == "fresh-export-required":
        allowed = {"format", "state", "reason", "required_at", "previous_confirmed"}
        if set(document) != allowed:
            raise ValueError("Required recovery state contains unexpected fields")
        reason = document.get("reason")
        if not isinstance(reason, str) or not (1 <= len(reason) <= 128) \
                or any(character in reason for character in "\r\n\t"):
            raise ValueError("Required recovery reason is invalid")
        return {
            "format": RECOVERY_STATE_FORMAT,
            "state": state,
            "reason": reason,
            "required_at": timestamp(document.get("required_at"), "required_at"),
            "previous_confirmed": previous_export(document.get("previous_confirmed")),
        }
    raise ValueError("Recovery state value is invalid")


def prepare_recovery_state(args: argparse.Namespace) -> None:
    source = Path(args.source)
    if source.is_symlink():
        raise ValueError("Recovery state source may not be a symbolic link")
    if source.exists():
        if not source.is_file():
            raise ValueError("Recovery state source is not a regular file")
        document = json.loads(source.read_text(encoding="utf-8"))
    else:
        document = {
            "format": RECOVERY_STATE_FORMAT,
            "state": "fresh-export-required",
            "reason": "no-confirmed-workstation-export",
            "required_at": datetime.now(timezone.utc).isoformat(),
            "previous_confirmed": {},
        }
    output = Path(args.output)
    output.write_text(
        json.dumps(normalise_recovery_state(document), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(output, 0o600)


def files_for(payload: Path) -> list[dict]:
    result = []
    for path in sorted(item for item in payload.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise ValueError("Replication payloads may not contain symbolic links")
        relative = path.relative_to(payload).as_posix()
        if not safe_relative(relative):
            raise ValueError("Unsafe replication payload path")
        result.append({
            "path": relative,
            "sha256": sha256(path),
            "size": path.stat().st_size,
            "mode": format(stat.S_IMODE(path.stat().st_mode), "04o"),
        })
    return result


def create(args: argparse.Namespace) -> None:
    for value in (args.cluster, args.source, args.target, args.bundle):
        if not IDENTIFIER.fullmatch(value):
            raise ValueError("Invalid replication identifier")
    payload = Path(args.payload).resolve()
    document = {
        "format": FORMAT,
        "bundle_id": args.bundle,
        "cluster_id": args.cluster,
        "source_node_id": args.source,
        "target_node_id": args.target,
        "generation": args.generation,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "release_hash": args.release,
        "files": files_for(payload),
    }
    request_path = getattr(args, "request", None)
    if request_path:
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        assertion = normalise_privacy_assertion(request.get("privacy_assertion"))
        if assertion is None:
            raise ValueError("The replication request has no privacy assertion")
        document["privacy_assertion"] = assertion
    Path(args.output).write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(args.output, 0o600)


def validate(args: argparse.Namespace) -> None:
    root = Path(args.extracted).resolve()
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "format": FORMAT,
        "cluster_id": args.cluster,
        "source_node_id": args.source,
        "target_node_id": args.target,
        "release_hash": args.release,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"Replication manifest {key} mismatch")
    normalise_privacy_assertion(manifest.get("privacy_assertion"))
    if not IDENTIFIER.fullmatch(str(manifest.get("bundle_id", ""))):
        raise ValueError("Invalid bundle identifier")
    if not isinstance(manifest.get("generation"), int) or manifest["generation"] < 1:
        raise ValueError("Invalid replication generation")
    try:
        created_at = datetime.fromisoformat(str(manifest.get("created_at", "")).replace("Z", "+00:00"))
        if created_at.tzinfo is None or created_at > (datetime.now(timezone.utc) + timedelta(minutes=5)).astimezone(created_at.tzinfo):
            raise ValueError("Invalid replication creation time")
    except ValueError as exc:
        raise ValueError("Invalid replication creation time") from exc
    declared = manifest.get("files")
    if not isinstance(declared, list) or not declared:
        raise ValueError("Replication manifest has no files")
    seen: set[str] = set()
    for item in declared:
        relative = str(item.get("path", ""))
        if not safe_relative(relative) or relative in seen:
            raise ValueError("Unsafe or duplicate manifest path")
        seen.add(relative)
        path = root / "payload" / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError("Replication payload member is missing or unsafe")
        if item.get("mode") != "0600" or stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise ValueError("Replication payload mode is unsafe")
        if path.stat().st_size != int(item.get("size", -1)) or sha256(path) != item.get("sha256"):
            raise ValueError("Replication payload hash mismatch")
    actual = {path.relative_to(root / "payload").as_posix() for path in (root / "payload").rglob("*") if path.is_file()}
    if actual != seen:
        raise ValueError("Replication payload contains undeclared files")
    required = {
        "database/masterplan.dump",
        "config/shared.env",
        RECOVERY_STATE_PATH,
        "evidence/ledger/chain-head.json",
        "evidence/public/instance_signing_key.pub",
        *(f"config/secrets/{name}" for name in ALLOWED_SECRET_FILES),
    }
    if not required.issubset(actual):
        raise ValueError("Replication payload is incomplete")
    secrets = {value.removeprefix("config/secrets/") for value in actual if value.startswith("config/secrets/")}
    if not secrets.issubset(ALLOWED_SECRET_FILES):
        raise ValueError("Replication payload contains a non-shared secret")
    recovery_document = json.loads(
        (root / "payload" / RECOVERY_STATE_PATH).read_text(encoding="utf-8")
    )
    normalise_recovery_state(recovery_document)
    print(json.dumps(manifest, sort_keys=True))


def filter_env(args: argparse.Namespace) -> None:
    source = Path(args.source).read_text(encoding="utf-8").splitlines()
    output = []
    for line in source:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if not is_local_env_key(key):
            output.append(line)
    Path(args.output).write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.chmod(args.output, 0o600)


def merge_env(args: argparse.Namespace) -> None:
    local_lines = Path(args.local).read_text(encoding="utf-8").splitlines()
    shared_lines = Path(args.shared).read_text(encoding="utf-8").splitlines()
    preserved = [
        line for line in local_lines
        if "=" in line and is_local_env_key(line.split("=", 1)[0].strip())
    ]
    Path(args.output).write_text("\n".join(preserved + shared_lines).rstrip() + "\n", encoding="utf-8")
    os.chmod(args.output, 0o600)


def validate_members(args: argparse.Namespace) -> None:
    members = Path(args.list).read_text(encoding="utf-8").splitlines()
    if not members or len(members) != len(set(members)):
        raise ValueError("Archive member list is empty or duplicated")
    for member in members:
        if not safe_relative(member.rstrip("/")):
            raise ValueError("Unsafe archive member")
        if not (member == "manifest.json" or member.startswith("payload/")):
            raise ValueError("Unexpected archive member")


def validate_archive(args: argparse.Namespace) -> None:
    """Reject links, devices, traversal and duplicate paths before extraction."""

    seen: set[str] = set()
    with tarfile.open(args.archive, mode="r:") as archive:
        members = archive.getmembers()
        if not members:
            raise ValueError("Archive is empty")
        for member in members:
            name = member.name.rstrip("/")
            if not safe_relative(name) or name in seen:
                raise ValueError("Unsafe or duplicate archive member")
            seen.add(name)
            if not (member.isfile() or member.isdir()):
                raise ValueError("Archive links and special files are forbidden")
            if not (name == "manifest.json" or name == "payload" or name.startswith("payload/")):
                raise ValueError("Unexpected archive member")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="command", required=True)
    create_parser = commands.add_parser("create")
    for option in ("payload", "cluster", "source", "target", "bundle", "release", "output"):
        create_parser.add_argument(f"--{option}", required=True)
    create_parser.add_argument("--generation", type=int, required=True)
    create_parser.add_argument("--request")
    create_parser.set_defaults(function=create)
    validate_parser = commands.add_parser("validate")
    for option in ("extracted", "cluster", "source", "target", "release"):
        validate_parser.add_argument(f"--{option}", required=True)
    validate_parser.set_defaults(function=validate)
    for command, function in (("filter-env", filter_env), ("merge-env", merge_env)):
        sub = commands.add_parser(command)
        if command == "filter-env":
            sub.add_argument("--source", required=True)
        else:
            sub.add_argument("--local", required=True)
            sub.add_argument("--shared", required=True)
        sub.add_argument("--output", required=True)
        sub.set_defaults(function=function)
    members = commands.add_parser("validate-members")
    members.add_argument("--list", required=True)
    members.set_defaults(function=validate_members)
    archive = commands.add_parser("validate-archive")
    archive.add_argument("--archive", required=True)
    archive.set_defaults(function=validate_archive)
    recovery = commands.add_parser("prepare-recovery-state")
    recovery.add_argument("--source", required=True)
    recovery.add_argument("--output", required=True)
    recovery.set_defaults(function=prepare_recovery_state)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        args.function(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
