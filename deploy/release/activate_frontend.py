#!/usr/bin/env python3
"""Atomically activate or roll back staged static assets without remounting Caddy."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import tempfile


HASH_SOURCE = re.compile(r"'sha256-[A-Za-z0-9+/]+={0,2}'")


def require_directory(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"{label} is not a safe directory")
    return path


def require_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"{label} is not a safe file")
    return path


def atomic_copy(source: Path, destination: Path, mode: int = 0o644) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        temporary.chmod(mode)
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def merged_csp(active: Path, staged: Path, output: Path) -> None:
    active_text = require_file(active, "active CSP").read_text(encoding="utf-8")
    staged_text = require_file(staged, "staged CSP").read_text(encoding="utf-8")
    sources = sorted(set(HASH_SOURCE.findall(active_text)) | set(HASH_SOURCE.findall(staged_text)))
    if not sources or "script-src 'self'" not in staged_text:
        raise RuntimeError("CSP staging data is invalid")
    merged = re.sub(
        r"script-src 'self'(?: 'sha256-[A-Za-z0-9+/]+={0,2}')*;",
        f"script-src 'self' {' '.join(sources)};",
        staged_text,
        count=1,
    )
    descriptor, temporary_name = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(merged)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o644)
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def ordered_files(root: Path) -> list[Path]:
    files = [path for path in root.rglob("*") if path.is_file() and not path.is_symlink()]
    if any(path.is_symlink() for path in root.rglob("*")):
        raise RuntimeError("staged frontend contains a symlink")
    def priority(path: Path) -> tuple[int, str]:
        relative = path.relative_to(root).as_posix()
        if relative == "sw.js":
            return (4, relative)
        if relative == "index.html":
            return (3, relative)
        if path.suffix == ".html":
            return (2, relative)
        return (1, relative)
    return sorted(files, key=priority)


def activate(root: Path) -> None:
    active = require_directory(root / "web/out", "active frontend")
    staged = require_directory(root / "web/.out.next", "staged frontend")
    policy = require_file(root / "runtime/frontend-csp.caddy", "active CSP")
    staged_policy = require_file(root / "runtime/.frontend-csp.next", "staged CSP")
    previous = root / "web/.out.previous"
    previous_policy = root / "runtime/.frontend-csp.previous"
    if previous.is_symlink() or previous_policy.is_symlink():
        raise RuntimeError("frontend rollback target is unsafe")
    # Validate both trees before changing the rollback set or active policy.
    # copytree() would otherwise preserve a hostile nested symlink from an
    # unexpectedly substituted active tree.
    ordered_files(active)
    staged_files = ordered_files(staged)
    if previous.exists():
        shutil.rmtree(previous)
    shutil.copytree(active, previous)
    atomic_copy(policy, previous_policy)

    # First load a policy that accepts both generations. Hashed assets are then
    # installed before route documents, with the root document and worker last.
    merged_csp(policy, staged_policy, policy)
    for source in staged_files:
        atomic_copy(source, active / source.relative_to(staged))


def rollback(root: Path) -> None:
    active = require_directory(root / "web/out", "active frontend")
    previous = require_directory(root / "web/.out.previous", "previous frontend")
    previous_policy = require_file(root / "runtime/.frontend-csp.previous", "previous CSP")
    previous_files = ordered_files(previous)
    for child in list(active.iterdir()):
        if child.is_symlink():
            raise RuntimeError("active frontend contains a symlink")
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    for source in previous_files:
        atomic_copy(source, active / source.relative_to(previous))
    atomic_copy(previous_policy, root / "runtime/frontend-csp.caddy")


def finalize(root: Path) -> None:
    staged_policy = require_file(root / "runtime/.frontend-csp.next", "staged CSP")
    staged = require_directory(root / "web/.out.next", "staged frontend")
    ordered_files(staged)
    atomic_copy(staged_policy, root / "runtime/frontend-csp.caddy")
    shutil.rmtree(staged)
    staged_policy.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("activate", "rollback", "finalize"))
    parser.add_argument("--repo-root", required=True, type=Path)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    if root != Path("/opt/masterplan") and os.environ.get("MP_ALLOW_TEST_ROOT") != "1":
        raise RuntimeError("frontend activation is restricted to /opt/masterplan")
    {"activate": activate, "rollback": rollback, "finalize": finalize}[args.action](root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
