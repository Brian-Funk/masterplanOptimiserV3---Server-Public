#!/usr/bin/env python3
"""Workstation-only Git workflow for a private accountability archive.

This helper never receives Git credentials and never handles operator private
keys. It verifies and stages public evidence, makes a locally signed Git
commit through the user's configured Git signer, and prepares an unsigned
anchor document for the external controller custody helper to sign.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

import evidence_bundle
import evidence_repository


class WorkstationError(ValueError):
    """Raised when a Git action would cross the dedicated archive boundary."""


def _git(archive: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(archive), *arguments],
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _repository_root(archive: Path) -> Path:
    archive = archive.resolve(strict=True)
    result = _git(archive, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise WorkstationError("the archive is not a Git repository")
    root = Path(result.stdout.strip()).resolve(strict=True)
    if root != archive:
        raise WorkstationError("commands must target the dedicated Git repository root")
    return root


def _changed_paths(archive: Path) -> list[str]:
    result = _git(archive, "status", "--porcelain=v1", "-z")
    entries = result.stdout.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        if len(entry) < 4:
            raise WorkstationError("Git returned an ambiguous status entry")
        status = entry[:2]
        path = entry[3:]
        if "R" in status or "C" in status:
            if index >= len(entries) or not entries[index]:
                raise WorkstationError("Git returned an incomplete rename entry")
            path = entries[index]
            index += 1
        paths.append(path.replace("\\", "/"))
    return sorted(set(paths))


def _allowed_change(path: str) -> bool:
    return path in evidence_repository.STATIC_FILES or bool(re.fullmatch(
        r"instances/[0-9a-f-]{36}/bundles/[0-9a-f-]{36}/(?:evidence\.bundle|bundle\.sha256)",
        path,
    ))


def initialise(archive: Path, repository_id: str | None = None) -> dict:
    """Initialise the closed archive template and a local Git repository."""

    result = evidence_repository.initialise_repository(archive, repository_id)
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(archive)],
        check=True,
        capture_output=True,
        text=True,
    )
    return result


def import_bundle(bundle: Path, archive: Path) -> dict:
    """Verify and copy a public bundle without creating a commit."""

    root = _repository_root(archive)
    existing = _changed_paths(root)
    if existing:
        raise WorkstationError("the evidence repository has uncommitted changes")
    return evidence_repository.stage_bundle(bundle.resolve(strict=True), root)


def commit(archive: Path, message: str) -> dict:
    """Verify, stage only declared evidence files, and create a signed commit."""

    root = _repository_root(archive)
    verification = evidence_repository.verify_repository(root)
    paths = _changed_paths(root)
    if not paths:
        raise WorkstationError("there are no evidence changes to commit")
    unexpected = [path for path in paths if not _allowed_change(path)]
    if unexpected:
        raise WorkstationError(f"the working tree contains unrelated changes: {', '.join(unexpected)}")
    _git(root, "add", "--", *paths)
    _git(root, "commit", "-S", "-m", message)
    sha = _git(root, "rev-parse", "HEAD").stdout.strip()
    return {"commit_sha": sha, "signed": True, "files": paths, "repository": verification}


def push(archive: Path, remote: str) -> dict:
    """Verify a clean archive and push the current branch without force."""

    root = _repository_root(archive)
    verification = evidence_repository.verify_repository(root)
    if _changed_paths(root):
        raise WorkstationError("the evidence repository must be clean before push")
    branch = _git(root, "branch", "--show-current").stdout.strip()
    if not branch:
        raise WorkstationError("the evidence repository is in detached HEAD state")
    _git(root, "push", "--porcelain", "--set-upstream", remote, f"HEAD:{branch}")
    return {"remote": remote, "branch": branch, "commit_sha": _git(root, "rev-parse", "HEAD").stdout.strip(), "repository": verification}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    initialise_command = commands.add_parser("initialise")
    initialise_command.add_argument("--archive", required=True, type=Path)
    initialise_command.add_argument("--repository-id")
    verify_command = commands.add_parser("verify")
    verify_command.add_argument("--archive", required=True, type=Path)
    import_command = commands.add_parser("import-bundle")
    import_command.add_argument("--archive", required=True, type=Path)
    import_command.add_argument("--bundle", required=True, type=Path)
    commit_command = commands.add_parser("commit")
    commit_command.add_argument("--archive", required=True, type=Path)
    commit_command.add_argument("--message", required=True)
    push_command = commands.add_parser("push")
    push_command.add_argument("--archive", required=True, type=Path)
    push_command.add_argument("--remote", default="origin")
    anchor_command = commands.add_parser("create-anchor")
    anchor_command.add_argument("--archive", required=True, type=Path)
    anchor_command.add_argument("--instance-id", required=True)
    anchor_command.add_argument("--bundle-id", required=True)
    anchor_command.add_argument("--controller-key-id", required=True)
    anchor_command.add_argument("--output", required=True, type=Path)
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        if arguments.command == "initialise":
            result = initialise(arguments.archive, arguments.repository_id)
        elif arguments.command == "verify":
            result = evidence_repository.verify_repository(arguments.archive.resolve(strict=True))
        elif arguments.command == "import-bundle":
            result = import_bundle(arguments.bundle, arguments.archive)
        elif arguments.command == "commit":
            result = commit(arguments.archive, arguments.message)
        elif arguments.command == "push":
            result = push(arguments.archive, arguments.remote)
        else:
            root = _repository_root(arguments.archive)
            if _changed_paths(root):
                raise WorkstationError("the evidence repository must be clean before anchor creation")
            result = evidence_repository.build_anchor_document(
                root,
                instance_id=arguments.instance_id,
                bundle_id=arguments.bundle_id,
                git_commit_sha=_git(root, "rev-parse", "HEAD").stdout.strip(),
                controller_key_id=arguments.controller_key_id,
                output=arguments.output,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (
        WorkstationError,
        evidence_repository.RepositoryError,
        evidence_bundle.BundleError,
        OSError,
        subprocess.CalledProcessError,
    ) as exc:
        parser().exit(1, f"evidence Git helper error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
