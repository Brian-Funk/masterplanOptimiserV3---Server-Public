#!/usr/bin/env python3
"""Verify a closed, bundle-only private Evidence archive repository."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import portable_bundle
import evidence_git


ARCHIVE_PATH_RE = re.compile(
    r"^instances/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/"
    r"bundles/([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/"
    r"(evidence\.bundle|bundle\.sha256)$"
)
STATIC_FILES = {
    ".github/workflows/verify-evidence.yml",
    "CODEOWNERS",
    "README.md",
    "instances/README.md",
    "tools/evidence_archive_repository.py",
    "tools/evidence_git.py",
    "tools/evidence_manifest.py",
    "tools/portable_bundle.py",
    "tools/scan_evidence_repo.py",
    "tools/validate_ingestion_paths.py",
    "tools/verify_evidence_repo.py",
}


class ArchiveRepositoryError(ValueError):
    """Raised when the private archive violates its closed schema."""


def _files(root: Path) -> set[str]:
    result: set[str] = set()
    for path in root.rglob("*"):
        relative_path = path.relative_to(root)
        if ".git" in relative_path.parts or "__pycache__" in relative_path.parts:
            continue
        if path.is_symlink():
            raise ArchiveRepositoryError(f"Repository links are forbidden: {relative_path.as_posix()}")
        if path.is_file():
            result.add(relative_path.as_posix())
    return result


def verify_repository(root: Path) -> dict[str, Any]:
    """Verify every portable bundle, digest, identity and chain relationship."""

    if root.is_symlink() or not root.is_dir():
        raise ArchiveRepositoryError("Evidence archive is unavailable or unsafe")
    root = root.resolve()
    files = _files(root)
    unexpected = sorted(path for path in files if path not in STATIC_FILES and not ARCHIVE_PATH_RE.fullmatch(path))
    if unexpected:
        raise ArchiveRepositoryError("Repository contains undeclared files: " + ", ".join(unexpected))
    missing = sorted(STATIC_FILES - files)
    if missing:
        raise ArchiveRepositoryError("Repository verifier files are missing: " + ", ".join(missing))

    controllers: set[str] = set()
    instances: dict[str, list[dict[str, Any]]] = {}
    bundle_paths = sorted(root.glob("instances/*/bundles/*/evidence.bundle"))
    for bundle in bundle_paths:
        relative = bundle.relative_to(root).as_posix()
        match = ARCHIVE_PATH_RE.fullmatch(relative)
        if match is None:
            raise ArchiveRepositoryError(f"Bundle path is invalid: {relative}")
        summary = portable_bundle.verify_bundle(bundle)
        instance_id, bundle_id = match.group(1), match.group(2)
        if summary["instance_id"] != instance_id or summary["bundle_id"] != bundle_id:
            raise ArchiveRepositoryError("Bundle path does not match its verified identity")
        receipt = bundle.with_name("bundle.sha256")
        expected = f"{summary['bundle_sha256']}  evidence.bundle\n"
        try:
            actual = receipt.read_text(encoding="ascii")
        except (OSError, UnicodeError) as exc:
            raise ArchiveRepositoryError("Bundle digest is missing or invalid") from exc
        if actual != expected:
            raise ArchiveRepositoryError("Bundle digest does not match")
        controllers.add(summary["controller_id"])
        instances.setdefault(instance_id, []).append(summary)

    if len(controllers) > 1:
        raise ArchiveRepositoryError("Repository contains more than one controller identity")
    for instance_id, summaries in instances.items():
        ordered = sorted(summaries, key=lambda item: (item["record_count"], item["bundle_id"]))
        seen_counts: set[int] = set()
        for summary in ordered:
            if summary["record_count"] in seen_counts:
                raise ArchiveRepositoryError(f"Instance {instance_id} contains competing bundles")
            seen_counts.add(summary["record_count"])
        for previous, current in zip(ordered, ordered[1:]):
            if (
                previous["record_count"] >= current["record_count"]
                or previous["chain_head_sha256"] not in current["record_sha256s"]
            ):
                raise ArchiveRepositoryError(f"Instance {instance_id} contains a rollback or fork")
    return {
        "valid": True,
        "controller_id": next(iter(controllers), None),
        "instances": len(instances),
        "bundles": len(bundle_paths),
    }


def _revision_files(root: Path, revision: str) -> set[str]:
    completed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", revision],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ArchiveRepositoryError("Git revision tree could not be inspected")
    return {line for line in completed.stdout.splitlines() if line}


def validate_ingestion_paths(root: Path, base: str, head: str) -> dict[str, Any]:
    """Allow the exact first template or one new portable bundle directory."""

    if not all(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) for value in (base, head)):
        raise ArchiveRepositoryError("Git ingestion revisions must be full object IDs")
    completed = subprocess.run(
        ["git", "diff", "--name-status", "--no-renames", base, head],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ArchiveRepositoryError("Git ingestion diff could not be inspected")
    rows = [line.split("\t") for line in completed.stdout.splitlines() if line]
    if not _revision_files(root, base):
        changed = {row[1] for row in rows if len(row) == 2 and row[0] == "A"}
        if len(changed) == len(rows) and changed == STATIC_FILES and _revision_files(root, head) == STATIC_FILES:
            return {"valid": True, "mode": "template-bootstrap", "paths": sorted(changed)}
    if len(rows) != 2 or any(len(row) != 2 or row[0] != "A" for row in rows):
        raise ArchiveRepositoryError("An ingestion pull request must add exactly two files")
    matches = [ARCHIVE_PATH_RE.fullmatch(row[1]) for row in rows]
    if any(match is None for match in matches):
        raise ArchiveRepositoryError("An ingestion pull request changed a forbidden path")
    parents = {row[1].rsplit("/", 1)[0] for row in rows}
    names = {row[1].rsplit("/", 1)[1] for row in rows}
    if len(parents) != 1 or names != {"evidence.bundle", "bundle.sha256"}:
        raise ArchiveRepositoryError("The bundle and digest must share one new identity directory")
    return {"valid": True, "mode": "bundle-ingestion", "paths": sorted(row[1] for row in rows)}


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("repository", nargs="?", type=Path, default=Path.cwd())
    paths = commands.add_parser("validate-paths")
    paths.add_argument("repository", nargs="?", type=Path, default=Path.cwd())
    paths.add_argument("--base", required=True)
    paths.add_argument("--head", required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "verify":
            result = verify_repository(arguments.repository)
        else:
            result = validate_ingestion_paths(arguments.repository, arguments.base, arguments.head)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (
        ArchiveRepositoryError, portable_bundle.PortableBundleError,
        evidence_git.EvidenceGitError, OSError,
    ) as exc:
        print(f"Evidence archive verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
