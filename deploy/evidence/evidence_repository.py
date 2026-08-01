#!/usr/bin/env python3
"""Create, stage and verify a controller-owned private evidence repository."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import uuid

import evidence_bundle


POLICY_FORMAT = "mp-opt-evidence-repository-policy-v1"
ANCHOR_FORMAT = "mp-opt-git-anchor-v2"
CONTROLLER_ROLE = "controller"
KEY_ID_RE = re.compile(r"^ek-[0-9a-f]{16}$")
GIT_COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
STATIC_FILES = {
    "README.md",
    "repository-policy.json",
    ".github/workflows/verify-evidence.yml",
    "scripts/evidence_manifest.py",
    "scripts/evidence_bundle.py",
    "scripts/evidence_repository.py",
    "scripts/evidence_repo.py",
}


class RepositoryError(ValueError):
    """Raised when an evidence repository violates its closed schema."""


def canonical_json(value: dict) -> bytes:
    """Return stable JSON bytes for repository metadata."""

    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise RepositoryError(f"{field} must be a UUID")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise RepositoryError(f"{field} must be a UUID") from exc
    if str(parsed) != value:
        raise RepositoryError(f"{field} must use canonical UUID form")
    return value


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_bytes(raw)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def load_policy(archive: Path) -> dict:
    """Load the exact repository policy and reject ambiguous fields."""

    path = archive / "repository-policy.json"
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise RepositoryError("repository policy is missing or invalid") from exc
    required = {"format", "repository_id", "state", "required_instance_ids"}
    if not isinstance(document, dict) or set(document) != required:
        raise RepositoryError("repository policy fields are invalid")
    if raw != canonical_json(document) or document["format"] != POLICY_FORMAT:
        raise RepositoryError("repository policy is not canonical or supported")
    _uuid(document["repository_id"], "repository_id")
    if document["state"] not in {"initialising", "active"}:
        raise RepositoryError("repository policy state is invalid")
    values = document["required_instance_ids"]
    if not isinstance(values, list) or values != sorted(set(values)):
        raise RepositoryError("required instance IDs must be sorted and unique")
    for value in values:
        _uuid(value, "required_instance_ids entry")
    if document["state"] == "initialising" and values:
        raise RepositoryError("an initialising repository cannot require instances")
    return document


def initialise_repository(destination: Path, repository_id: str | None = None) -> dict:
    """Create a closed, private-repository template without any evidence."""

    if destination.exists() and any(destination.iterdir()):
        raise RepositoryError("repository destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    repository_id = repository_id or str(uuid.uuid4())
    _uuid(repository_id, "repository_id")
    assets = Path(__file__).with_name("repository-template")
    for relative in ("README.md", ".github/workflows/verify-evidence.yml"):
        source = assets / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    scripts = destination / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("evidence_manifest.py", "evidence_bundle.py", "evidence_repository.py", "evidence_repo.py"):
        shutil.copyfile(Path(__file__).with_name(name), scripts / name)
    policy = {
        "format": POLICY_FORMAT,
        "repository_id": repository_id,
        "state": "initialising",
        "required_instance_ids": [],
    }
    _atomic_write(destination / "repository-policy.json", canonical_json(policy))
    verify_repository(destination)
    return policy


def _bundle_record_digests(bundle: Path) -> tuple[str, list[str]]:
    """Return chain ID and ordered signed-record digests without extraction."""

    digests: list[str] = []
    chain_id: str | None = None
    with tarfile.open(bundle, "r:") as archive:
        records = sorted((
            member for member in archive.getmembers()
            if re.fullmatch(r"evidence/ledger/[0-9]{12}_[0-9a-f-]{36}\.json", member.name)
        ), key=lambda member: member.name)
        for member in records:
            handle = archive.extractfile(member)
            if handle is None:
                raise RepositoryError("a staged ledger record is unreadable")
            raw = handle.read(member.size + 1)
            if len(raw) != member.size:
                raise RepositoryError("a staged ledger record is truncated")
            try:
                document = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RepositoryError("a staged ledger record is invalid") from exc
            current_chain = document.get("chain_id")
            if chain_id is None:
                chain_id = current_chain
            elif current_chain != chain_id:
                raise RepositoryError("a bundle mixes evidence chains")
            digests.append(hashlib.sha256(raw).hexdigest())
    if chain_id is None or not digests:
        raise RepositoryError("a staged bundle has no signed ledger records")
    return chain_id, digests


def _allowed_instance_file(relative: str) -> bool:
    return bool(re.fullmatch(
        r"instances/[0-9a-f-]{36}/bundles/[0-9a-f-]{36}/(?:evidence\.bundle|bundle\.sha256)",
        relative,
    ))


def verify_repository(archive: Path) -> dict:
    """Verify every declared bundle and reject all undeclared repository data."""

    if archive.is_symlink() or not archive.is_dir():
        raise RepositoryError("evidence repository is unavailable or unsafe")
    policy = load_policy(archive)
    for path in archive.rglob("*"):
        relative = path.relative_to(archive)
        if ".git" not in relative.parts and path.is_symlink():
            raise RepositoryError(f"repository symlinks are forbidden: {relative.as_posix()}")
    files = {
        path.relative_to(archive).as_posix()
        for path in archive.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(archive).parts
    }
    unexpected = sorted(
        relative for relative in files
        if relative not in STATIC_FILES and not _allowed_instance_file(relative)
    )
    if unexpected:
        raise RepositoryError(f"repository contains undeclared files: {', '.join(unexpected)}")
    missing_static = sorted(STATIC_FILES - files)
    if missing_static:
        raise RepositoryError(f"repository template files are missing: {', '.join(missing_static)}")

    instances: dict[str, list[tuple[int, str, str, set[str]]]] = {}
    for bundle in sorted(archive.glob("instances/*/bundles/*/evidence.bundle")):
        summary = evidence_bundle.verify_bundle(bundle)
        instance_id = bundle.parents[2].name
        bundle_id = bundle.parent.name
        if instance_id != summary["instance_id"] or bundle_id != summary["bundle_id"]:
            raise RepositoryError("bundle path does not match its signed identity")
        receipt = bundle.parent / "bundle.sha256"
        expected = f"{evidence_bundle.sha256_file(bundle)}  evidence.bundle\n"
        if receipt.read_text(encoding="ascii") != expected:
            raise RepositoryError("bundle checksum receipt does not match")
        chain_id, record_digests = _bundle_record_digests(bundle)
        instances.setdefault(instance_id, []).append((
            summary["record_count"], chain_id, summary["chain_head_sha256"], set(record_digests),
        ))

    required = set(policy["required_instance_ids"])
    present = set(instances)
    if policy["state"] == "initialising":
        if present:
            raise RepositoryError("an initialising repository cannot contain evidence bundles")
    elif not present or not required or not required.issubset(present):
        raise RepositoryError("active repository is missing required evidence")

    for instance_id, bundles in instances.items():
        ordered = sorted(bundles)
        chain_ids = {row[1] for row in ordered}
        if len(chain_ids) != 1:
            raise RepositoryError(f"instance {instance_id} contains a forked chain")
        for previous, current in zip(ordered, ordered[1:]):
            if previous[0] > current[0] or previous[2] not in current[3]:
                raise RepositoryError(f"instance {instance_id} contains a rollback or fork")
    return {
        "valid": True,
        "repository_id": policy["repository_id"],
        "state": policy["state"],
        "instances": len(instances),
        "bundles": sum(len(rows) for rows in instances.values()),
    }


def stage_bundle(bundle: Path, archive: Path) -> dict:
    """Verify and idempotently stage a bundle, then activate its policy."""

    policy = load_policy(archive)
    previous_policy = canonical_json(policy)
    summary = evidence_bundle.stage_git(bundle, archive)
    try:
        required = sorted(set(policy["required_instance_ids"]) | {summary["instance_id"]})
        policy["state"] = "active"
        policy["required_instance_ids"] = required
        _atomic_write(archive / "repository-policy.json", canonical_json(policy))
        verification = verify_repository(archive)
    except Exception:
        _atomic_write(archive / "repository-policy.json", previous_policy)
        if summary["status"] == "staged":
            destination = Path(summary["destination"])
            shutil.rmtree(destination)
            for parent in (destination.parent, destination.parent.parent):
                try:
                    parent.rmdir()
                except OSError:
                    break
        raise
    return summary | {"repository": verification}


def build_anchor_document(
    archive: Path,
    *,
    instance_id: str,
    bundle_id: str,
    git_commit_sha: str,
    controller_key_id: str,
    output: Path,
) -> dict:
    """Create an unsigned canonical anchor document for external controller signing."""

    policy = load_policy(archive)
    verify_repository(archive)
    _uuid(instance_id, "instance_id")
    _uuid(bundle_id, "bundle_id")
    if not GIT_COMMIT_RE.fullmatch(git_commit_sha):
        raise RepositoryError("git commit SHA is invalid")
    if not KEY_ID_RE.fullmatch(controller_key_id):
        raise RepositoryError("controller key identity is invalid")
    if output.exists() or output.is_symlink():
        raise RepositoryError("anchor output already exists")
    bundle = archive / "instances" / instance_id / "bundles" / bundle_id / "evidence.bundle"
    summary = evidence_bundle.verify_bundle(bundle)
    document = {
        "format": ANCHOR_FORMAT,
        "anchor_id": str(uuid.uuid4()),
        "instance_id": summary["instance_id"],
        "chain_id": summary["chain_id"],
        "bundle_id": summary["bundle_id"],
        "bundle_sha256": evidence_bundle.sha256_file(bundle),
        "chain_head_sha256": summary["chain_head_sha256"],
        "repository_id": policy["repository_id"],
        "git_commit_sha": git_commit_sha,
        "controller_key_id": controller_key_id,
        "controller_role": CONTROLLER_ROLE,
        "signed_at": datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _atomic_write(output, canonical_json(document))
    return document


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    initialise = commands.add_parser("init")
    initialise.add_argument("--archive", required=True, type=Path)
    initialise.add_argument("--repository-id")
    stage = commands.add_parser("stage")
    stage.add_argument("--bundle", required=True, type=Path)
    stage.add_argument("--archive", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--archive", required=True, type=Path)
    anchor = commands.add_parser("build-anchor")
    anchor.add_argument("--archive", required=True, type=Path)
    anchor.add_argument("--instance-id", required=True)
    anchor.add_argument("--bundle-id", required=True)
    anchor.add_argument("--git-commit-sha", required=True)
    anchor.add_argument("--controller-key-id", required=True)
    anchor.add_argument("--output", required=True, type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "init":
            result = initialise_repository(args.archive, args.repository_id)
        elif args.command == "stage":
            result = stage_bundle(args.bundle, args.archive)
        elif args.command == "verify":
            result = verify_repository(args.archive)
        else:
            result = build_anchor_document(
                args.archive,
                instance_id=args.instance_id,
                bundle_id=args.bundle_id,
                git_commit_sha=args.git_commit_sha,
                controller_key_id=args.controller_key_id,
                output=args.output,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (RepositoryError, evidence_bundle.BundleError, OSError, tarfile.TarError) as exc:
        parser().exit(1, f"evidence repository error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
