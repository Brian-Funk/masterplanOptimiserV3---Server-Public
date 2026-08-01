#!/usr/bin/env python3
"""Install the newest signed production release without compiling on the VPS."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

REPOSITORY = "Brian-Funk/masterplanOptimiserV3---Server-Public"
COSIGN_IMAGE = (
    "ghcr.io/sigstore/cosign/cosign@"
    "sha256:be924970ba7438c22e18067dec5637946d6566eac711f5bedd1584e7137008fb"
)
ISSUER = "https://token.actions.githubusercontent.com"
IDENTITY = (
    r"^https://github\.com/Brian-Funk/masterplanOptimiserV3---Server-Public/"
    r"\.github/workflows/release\.yml@refs/(?:tags/v[0-9]+\.[0-9]+\.[0-9]+|heads/main)$"
)
IMAGE = re.compile(r"^ghcr\.io/brian-funk/masterplanoptimiserv3---server/[a-z-]+@sha256:[0-9a-f]{64}$")
LATEST_RELEASE_RETRY_DELAYS = (1, 2, 4)
TRANSIENT_RELEASE_HTTP_STATUSES = {404, 408, 425, 429, 500, 502, 503, 504}


class ReleaseDiscoveryError(RuntimeError):
    """The stable release could not be resolved without weakening trust."""


def latest_stable_tag(
    repository: str = REPOSITORY,
    *,
    opener=urllib.request.urlopen,
    sleeper=time.sleep,
) -> str:
    """Resolve the latest stable tag with bounded retries for transient failures."""

    url = f"https://api.github.com/repos/{repository}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "mp-opt-release-installer/1",
        },
    )
    attempts = len(LATEST_RELEASE_RETRY_DELAYS) + 1
    for attempt in range(attempts):
        try:
            with opener(request, timeout=30) as response:
                payload = json.load(response)
            tag = payload.get("tag_name") if isinstance(payload, dict) else None
            if not isinstance(tag, str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
                raise ReleaseDiscoveryError(
                    "GitHub's latest release response has no stable semantic-version tag"
                )
            return tag
        except urllib.error.HTTPError as error:
            retryable = error.code in TRANSIENT_RELEASE_HTTP_STATUSES
            detail = f"HTTP {error.code}"
            cause = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            retryable = True
            detail = type(error).__name__
            cause = error

        if not retryable or attempt == attempts - 1:
            raise ReleaseDiscoveryError(
                f"GitHub latest-release discovery failed after {attempt + 1} attempt(s): {detail}"
            ) from cause
        sleeper(LATEST_RELEASE_RETRY_DELAYS[attempt])

    raise AssertionError("release discovery retry loop exited unexpectedly")


def host_container_user() -> str:
    """Return the POSIX owner used for private release-verification files."""

    if not hasattr(os, "getuid") or not hasattr(os, "getgid"):
        raise RuntimeError("signed release installation requires a POSIX host")
    return f"{os.getuid()}:{os.getgid()}"


def download(url: str, target: Path, limit: int) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "mp-opt-release-installer/1"})
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as output:
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise RuntimeError(f"release asset exceeds {limit} bytes")
            output.write(chunk)


def run_cosign(work: Path, *arguments: str) -> None:
    subprocess.run(
        [
            "docker", "run", "--rm",
            "--user", host_container_user(),
            "--env", "HOME=/tmp",
            "-v", f"{work}:/work:ro",
            COSIGN_IMAGE,
            *arguments,
        ],
        check=True,
    )


def safe_extract(archive: Path, target: Path, kind: str) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        total_size = 0
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise RuntimeError(f"{kind} release contains an unsafe archive member")
            total_size += member.size
            frontend_member = path.parts[:2] == ("web", "out") or path == Path("runtime/frontend-csp.caddy")
            operations_member = path in {Path("manage.sh"), Path("configure-production.sh")} \
                or (path.parts and path.parts[0] in {"deploy", "infra"})
            if (kind == "frontend" and not frontend_member) or (kind == "operations" and not operations_member):
                raise RuntimeError(f"unexpected {kind} release member: {member.name}")
        limit = 512 * 1024 * 1024 if kind == "frontend" else 64 * 1024 * 1024
        if total_size > limit:
            raise RuntimeError(f"{kind} release expands beyond its safety limit")
        # Every member was constrained above. Avoid extraction_filter here so
        # Ubuntu 22.04's Python 3.10 remains supported.
        tar.extractall(target, members=members)


def atomic_text(path: Path, contents: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(contents, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def signed_asset(manifest: dict, name: str) -> tuple[str, str]:
    value = manifest.get(name)
    asset = value.get("asset") if isinstance(value, dict) else None
    digest = value.get("sha256") if isinstance(value, dict) else None
    if not isinstance(asset, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,80}", asset):
        raise RuntimeError(f"signed release manifest has no valid {name} asset")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RuntimeError(f"signed release manifest has no valid {name} digest")
    return asset, digest


def swap_with_previous(current: Path, previous: Path) -> None:
    """Atomically exchange one installed path with its retained predecessor."""

    if not current.exists() or not previous.exists():
        raise RuntimeError(f"rollback state is incomplete for {current.name}")
    temporary = current.with_name(f".{current.name}.rollback-swap")
    if temporary.exists():
        raise RuntimeError(f"stale rollback swap exists for {current.name}")
    current.replace(temporary)
    previous_moved = False
    try:
        previous.replace(current)
        previous_moved = True
        temporary.replace(previous)
    except Exception:
        if previous_moved and current.exists() and not previous.exists():
            current.replace(previous)
        if not current.exists() and temporary.exists():
            temporary.replace(current)
        raise


def rollback(root: Path) -> int:
    """Exchange the active release with the one retained by the last install."""

    pairs = [
        (root / "deploy", root / ".deploy.previous"),
        (root / "infra", root / ".infra.previous"),
        (root / "web/out", root / "web/.out.previous"),
        (root / "runtime/frontend-csp.caddy", root / "runtime/.frontend-csp.previous"),
        (root / "manage.sh", root / ".manage.sh.previous"),
        (root / "configure-production.sh", root / ".configure-production.sh.previous"),
        (root / ".release.env", root / ".release.env.previous"),
    ]
    for current, previous in pairs:
        if not current.exists() or not previous.exists():
            raise RuntimeError("No complete previous signed release is available")
    completed: list[tuple[Path, Path]] = []
    try:
        for current, previous in pairs:
            swap_with_previous(current, previous)
            completed.append((current, previous))
    except Exception:
        for current, previous in reversed(completed):
            swap_with_previous(current, previous)
        raise
    print("Restored the retained previous signed release")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--tag", default="latest")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    root = args.repo_root.resolve()
    if args.rollback:
        return rollback(root)
    if args.tag == "latest":
        tag = latest_stable_tag()
    else:
        tag = args.tag
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise RuntimeError("latest GitHub release is not a stable semantic version")
    base = f"https://github.com/{REPOSITORY}/releases/download/{tag}"
    with tempfile.TemporaryDirectory(prefix="mp-opt-release-") as directory:
        work = Path(directory)
        manifest_path = work / "release-manifest.json"
        bundle_path = work / "release-manifest.bundle"
        frontend_path = work / "web-out.tar.gz"
        operations_path = work / "operations.tar.gz"
        download(f"{base}/release-manifest.json", manifest_path, 1024 * 1024)
        download(f"{base}/release-manifest.bundle", bundle_path, 4 * 1024 * 1024)
        run_cosign(
            work, "verify-blob", "--bundle", "/work/release-manifest.bundle",
            "--certificate-oidc-issuer", ISSUER,
            "--certificate-identity-regexp", IDENTITY,
            "/work/release-manifest.json",
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != "mp-opt-release-v1" or manifest.get("tag") != tag:
            raise RuntimeError("signed release manifest does not match the selected tag")
        commit = manifest.get("commit")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise RuntimeError("signed release manifest has an invalid source commit")
        images = manifest.get("images")
        if not isinstance(images, dict) or set(images) != {"backend", "caddy", "postgres", "tools"}:
            raise RuntimeError("signed release manifest has an invalid image set")
        for reference in images.values():
            if not isinstance(reference, str) or not IMAGE.fullmatch(reference):
                raise RuntimeError("signed release manifest contains an invalid image reference")
            run_cosign(
                work, "verify", "--certificate-oidc-issuer", ISSUER,
                "--certificate-identity-regexp", IDENTITY, reference,
            )
            subprocess.run(["docker", "pull", reference], check=True)
        frontend_asset, frontend_digest = signed_asset(manifest, "frontend")
        operations_asset, operations_digest = signed_asset(manifest, "operations")
        download(f"{base}/{frontend_asset}", frontend_path, 128 * 1024 * 1024)
        download(f"{base}/{operations_asset}", operations_path, 16 * 1024 * 1024)
        if sha256_file(frontend_path) != frontend_digest:
            raise RuntimeError("frontend release SHA-256 does not match the signed manifest")
        if sha256_file(operations_path) != operations_digest:
            raise RuntimeError("operations release SHA-256 does not match the signed manifest")
        extracted = work / "extracted"
        extracted.mkdir()
        safe_extract(frontend_path, extracted, "frontend")
        safe_extract(operations_path, extracted, "operations")
        destination = root / "web/out"
        previous = root / "web/.out.previous"
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700, exist_ok=True)
        policy = runtime / "frontend-csp.caddy"
        previous_policy = runtime / ".frontend-csp.previous"
        root_file_backups = work / "root-file-backups"
        root_file_backups.mkdir()
        for filename in ("manage.sh", "configure-production.sh"):
            shutil.copy2(root / filename, root_file_backups / filename)
        active_release_environment = work / "active.release.env"
        had_release_environment = (root / ".release.env").exists()
        if had_release_environment:
            shutil.copy2(root / ".release.env", active_release_environment)
        retained_paths = [
            root / ".deploy.previous",
            root / ".infra.previous",
            previous,
            previous_policy,
            root / ".manage.sh.previous",
            root / ".configure-production.sh.previous",
            root / ".release.env.previous",
        ]
        retained_backup = work / "retained-release"
        retained_backup.mkdir()
        for retained_path in retained_paths:
            if not retained_path.exists():
                continue
            backup = retained_backup / retained_path.relative_to(root)
            backup.parent.mkdir(parents=True, exist_ok=True)
            if retained_path.is_dir():
                shutil.copytree(retained_path, backup, symlinks=True)
            else:
                shutil.copy2(retained_path, backup)
        try:
            if previous.exists():
                shutil.rmtree(previous)
            if previous_policy.exists():
                previous_policy.unlink()
            if policy.exists():
                policy.replace(previous_policy)
            for filename in ("manage.sh", "configure-production.sh"):
                retained_file = root / f".{filename}.previous"
                if retained_file.exists():
                    retained_file.unlink()
                shutil.copy2(root / filename, retained_file)
            retained_release_environment = root / ".release.env.previous"
            if retained_release_environment.exists():
                retained_release_environment.unlink()
            if (root / ".release.env").exists():
                shutil.copy2(root / ".release.env", retained_release_environment)
            for directory_name in ("deploy", "infra"):
                current_directory = root / directory_name
                previous_directory = root / f".{directory_name}.previous"
                if previous_directory.exists():
                    shutil.rmtree(previous_directory)
                current_directory.replace(previous_directory)
                (extracted / directory_name).replace(current_directory)
            for filename in ("manage.sh", "configure-production.sh"):
                temporary_file = root / f".{filename}.next"
                shutil.copy2(extracted / filename, temporary_file)
                temporary_file.replace(root / filename)
            if destination.exists():
                destination.replace(previous)
            (extracted / "web/out").replace(destination)
            shutil.copy2(extracted / "runtime/frontend-csp.caddy", policy)
            atomic_text(
                root / ".release.env",
                "\n".join([
                    f"MP_RELEASE_TAG={tag}",
                    f"MP_RELEASE_COMMIT={commit}",
                    f"MP_BACKEND_IMAGE={images['backend']}",
                    f"MP_CADDY_IMAGE={images['caddy']}",
                    f"MP_POSTGRES_IMAGE={images['postgres']}",
                    f"MP_TOOLS_IMAGE={images['tools']}",
                    "",
                ]),
            )
            (root / ".test-deployment.env").unlink(missing_ok=True)
        except Exception:
            if destination.exists():
                shutil.rmtree(destination)
            if previous.exists():
                previous.replace(destination)
            if policy.exists():
                policy.unlink()
            if previous_policy.exists():
                previous_policy.replace(policy)
            for directory_name in ("deploy", "infra"):
                current_directory = root / directory_name
                previous_directory = root / f".{directory_name}.previous"
                if current_directory.exists():
                    shutil.rmtree(current_directory)
                if previous_directory.exists():
                    previous_directory.replace(current_directory)
            for filename in ("manage.sh", "configure-production.sh"):
                shutil.copy2(root_file_backups / filename, root / filename)
            if had_release_environment:
                shutil.copy2(active_release_environment, root / ".release.env")
            else:
                (root / ".release.env").unlink(missing_ok=True)
            # Restore the rollback set that predated this failed attempt.
            for retained_path in retained_paths:
                if retained_path.is_dir():
                    shutil.rmtree(retained_path)
                elif retained_path.exists():
                    retained_path.unlink()
                backup = retained_backup / retained_path.relative_to(root)
                if backup.is_dir():
                    shutil.copytree(backup, retained_path, symlinks=True)
                elif backup.exists():
                    retained_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, retained_path)
            raise
        # Keep one complete predecessor. A guarded rollback exchanges these
        # paths, so the just-replaced release remains available as the next
        # rollback target instead of being deleted.
    print(f"Installed signed release {tag}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseDiscoveryError as error:
        print(f"Release discovery failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
