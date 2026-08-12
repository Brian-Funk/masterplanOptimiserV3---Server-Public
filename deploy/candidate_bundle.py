#!/usr/bin/env python3
"""Validate and extract the private harness candidate-bundle contract."""

from __future__ import annotations

import argparse, hashlib, json, re, tarfile, zipfile
from pathlib import Path, PurePosixPath

IMAGE = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
FILES = {"candidate-bundle-index.json", "candidate-manifest.json",
         "frontend.tar.gz", "operations.tar.gz", "bootstrap.sh"}
LIMITS = {"candidate-bundle-index.json": 64 * 1024,
          "candidate-manifest.json": 64 * 1024, "bootstrap.sh": 4 * 1024 * 1024,
          "operations.tar.gz": 128 * 1024 * 1024, "frontend.tar.gz": 256 * 1024 * 1024}


def sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()


def object_with(value: object, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("candidate object shape is invalid")
    return value


def asset(value: object, name: str, payload: bytes) -> None:
    item = object_with(value, {"name", "sha256"})
    if item["name"] != name or not isinstance(item["sha256"], str) \
            or not SHA.fullmatch(item["sha256"]) or sha(payload) != item["sha256"]:
        raise ValueError("candidate asset identity mismatch")


def load(path: Path, commit: str) -> tuple[dict, dict[str, bytes]]:
    if not COMMIT.fullmatch(commit): raise ValueError("invalid commit")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if {item.filename for item in infos} != FILES or len(infos) != len(FILES):
            raise ValueError("candidate bundle has unexpected entries")
        for item in infos:
            if item.is_dir() or item.file_size > LIMITS[item.filename] or item.flag_bits & 1:
                raise ValueError("candidate bundle entry is unsafe")
        data = {name: archive.read(name) for name in FILES}
    index = object_with(json.loads(data["candidate-bundle-index.json"]),
                        {"format", "commit", "release_eligible", "manifest_sha256", "assets"})
    if index["format"] != "mp-opt-commissioning-candidate-bundle-v1" \
            or index["commit"] != commit or index["release_eligible"] is not False \
            or index["manifest_sha256"] != sha(data["candidate-manifest.json"]):
        raise ValueError("candidate index identity mismatch")
    index_assets = object_with(index["assets"], {"frontend", "operations", "bootstrap"})
    for key, filename in (("frontend", "frontend.tar.gz"),
                          ("operations", "operations.tar.gz"), ("bootstrap", "bootstrap.sh")):
        item = object_with(index_assets[key], {"path", "sha256"})
        if item["path"] != filename or item["sha256"] != sha(data[filename]):
            raise ValueError("candidate index asset mismatch")
    manifest = object_with(json.loads(data["candidate-manifest.json"]),
                           {"format", "commit", "release_eligible", "images",
                            "frontend", "operations", "bootstrap"})
    if manifest["format"] != "mp-opt-commissioning-candidate-v1" \
            or manifest["commit"] != commit or manifest["release_eligible"] is not False:
        raise ValueError("candidate manifest identity mismatch")
    images = object_with(manifest["images"], {"backend", "caddy", "postgres", "tools"})
    if not all(isinstance(value, str) and IMAGE.fullmatch(value) for value in images.values()):
        raise ValueError("candidate image is not digest-pinned")
    asset(manifest["frontend"], "frontend.tar.gz", data["frontend.tar.gz"])
    asset(manifest["operations"], "operations.tar.gz", data["operations.tar.gz"])
    asset(manifest["bootstrap"], "bootstrap.sh", data["bootstrap.sh"])
    return manifest, data


def extract_tar(data: bytes, destination: Path, allowed_dirs: tuple[str, ...],
                allowed_files: tuple[str, ...], max_total: int) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    temporary = destination / ".payload.tar.gz"; temporary.write_bytes(data)
    try:
        with tarfile.open(temporary, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > 20_000 or sum(member.size for member in members) > max_total:
                raise ValueError("candidate archive exceeds extraction bounds")
            seen: set[str] = set()
            for member in members:
                name = PurePosixPath(member.name)
                text = name.as_posix().lstrip("./")
                if text in seen: raise ValueError("candidate archive contains duplicate paths")
                seen.add(text)
                if name.is_absolute() or ".." in name.parts \
                        or not (member.isfile() or member.isdir()) \
                        or not (text in allowed_files
                                or any(text == prefix.rstrip("/") or text.startswith(prefix)
                                       for prefix in allowed_dirs)):
                    raise ValueError("candidate archive entry is unsafe")
            archive.extractall(destination, filter="data")
    finally: temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=("validate", "extract"))
    parser.add_argument("--bundle", type=Path, required=True); parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path); args = parser.parse_args()
    manifest, data = load(args.bundle, args.commit)
    if args.command == "extract":
        if not args.output: raise ValueError("extract requires output")
        extract_tar(data["operations.tar.gz"], args.output / "operations",
                    ("deploy/", "infra/"), ("manage.sh", "configure-production.sh"), 512 * 1024 * 1024)
        extract_tar(data["frontend.tar.gz"], args.output / "frontend",
                    ("web/out/",), ("runtime/frontend-csp.caddy",), 1024 * 1024 * 1024)
        bootstrap = data["bootstrap.sh"]
        if len(bootstrap) > 4 * 1024 * 1024 or not bootstrap.startswith(b"#!") or b"\0" in bootstrap:
            raise ValueError("candidate bootstrap is not a bounded script")
        path = args.output / "bootstrap.sh"; path.write_bytes(bootstrap); path.chmod(0o700)
    print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__": main()
