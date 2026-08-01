#!/usr/bin/env python3
"""Export, verify and stage public MP-OPT accountability evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import tempfile
import uuid

import evidence_manifest


FORMAT = "mp-opt-evidence-bundle-v1"
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
INCLUDED_ROOTS = {"ledger", "public", "anchors"}


class BundleError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value and not path.is_absolute() and ".." not in path.parts)


def evidence_files(home: Path) -> list[Path]:
    if home.is_symlink() or not home.is_dir():
        raise BundleError("evidence home is unavailable or unsafe")
    result: list[Path] = []
    for root_name in sorted(INCLUDED_ROOTS):
        root = home / root_name
        if not root.exists():
            continue
        if root.is_symlink() or not root.is_dir():
            raise BundleError(f"unsafe evidence directory: {root_name}")
        for path in sorted(root.rglob("*")):
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
                raise BundleError("evidence contains a link or special file")
            if stat.S_ISREG(metadata.st_mode):
                result.append(path)
    return result


def create_bundle(home: Path, output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise BundleError("output already exists")
    public_key = home / "public" / "instance_signing_key.pub"
    chain = evidence_manifest.verify_chain(home / "ledger", public_key)
    files = evidence_files(home)
    rows = [
        {
            "path": path.relative_to(home).as_posix(),
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
        for path in files
    ]
    document = {
        "format": FORMAT,
        "bundle_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "instance_id": chain["instance_id"],
        "chain_id": chain["chain_id"],
        "chain_head_sha256": chain["head_sha256"],
        "record_count": chain["records"],
        "files": rows,
    }
    manifest = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = output.with_name(output.name + ".partial")
    try:
        with tarfile.open(temporary, "w", format=tarfile.PAX_FORMAT) as archive:
            info = tarfile.TarInfo("bundle.json")
            info.size = len(manifest)
            info.mode = 0o600
            info.mtime = int(datetime.now(timezone.utc).timestamp())
            archive.addfile(info, _BytesReader(manifest))
            for path in files:
                relative = f"evidence/{path.relative_to(home).as_posix()}"
                info = archive.gettarinfo(str(path), arcname=relative)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o600
                with path.open("rb") as source:
                    archive.addfile(info, source)
        os.chmod(temporary, 0o600)
        summary = verify_bundle(temporary)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    summary["bundle_sha256"] = sha256_file(output)
    summary["path"] = str(output)
    return summary


class _BytesReader:
    def __init__(self, value: bytes):
        self.value = value
        self.position = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.value) - self.position
        result = self.value[self.position:self.position + size]
        self.position += len(result)
        return result


def _safe_members(archive: tarfile.TarFile, bundle_size: int) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    total = 0
    for member in archive.getmembers():
        name = member.name
        if not safe_relative(name) or name in members:
            raise BundleError("bundle contains an unsafe or duplicate path")
        if not member.isfile() or member.issym() or member.islnk() or member.isdev():
            raise BundleError("bundle members must be regular files")
        if name != "bundle.json" and not name.startswith("evidence/"):
            raise BundleError("bundle contains an unexpected file")
        if member.mode != 0o600 or member.size < 0:
            raise BundleError("bundle member metadata is unsafe")
        total += member.size
        members[name] = member
    if "bundle.json" not in members or total > bundle_size:
        raise BundleError("bundle is incomplete or sparse")
    return members


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    handle = archive.extractfile(member)
    if handle is None:
        raise BundleError("bundle member is unreadable")
    raw = handle.read(member.size + 1)
    if len(raw) != member.size:
        raise BundleError("bundle member is truncated")
    return raw


def verify_bundle(bundle: Path) -> dict:
    metadata = bundle.lstat()
    if not stat.S_ISREG(metadata.st_mode) or bundle.is_symlink() or metadata.st_size > MAX_BUNDLE_BYTES:
        raise BundleError("bundle is unavailable, unsafe or too large")
    with tempfile.TemporaryDirectory(prefix="mp-opt-evidence-bundle.") as directory_name:
        root = Path(directory_name)
        with tarfile.open(bundle, "r:") as archive:
            members = _safe_members(archive, metadata.st_size)
            try:
                document = json.loads(_read_member(archive, members["bundle.json"]).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BundleError("bundle manifest is invalid") from exc
            required = {
                "format", "bundle_id", "created_at", "instance_id", "chain_id",
                "chain_head_sha256", "record_count", "files",
            }
            if not isinstance(document, dict) or set(document) != required or document.get("format") != FORMAT:
                raise BundleError("bundle manifest schema is invalid")
            try:
                uuid.UUID(document["bundle_id"])
                uuid.UUID(document["instance_id"])
                uuid.UUID(document["chain_id"])
            except (ValueError, TypeError) as exc:
                raise BundleError("bundle identifiers are invalid") from exc
            rows = document.get("files")
            if not isinstance(rows, list) or not rows:
                raise BundleError("bundle file manifest is empty")
            declared: set[str] = set()
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"path", "sha256", "size"}:
                    raise BundleError("bundle file row is invalid")
                relative = row["path"]
                member_name = f"evidence/{relative}"
                if not safe_relative(relative) or relative.split("/", 1)[0] not in INCLUDED_ROOTS:
                    raise BundleError("bundle file path is invalid")
                if member_name in declared or member_name not in members:
                    raise BundleError("bundle file is duplicate or missing")
                raw = _read_member(archive, members[member_name])
                if row["size"] != len(raw) or row["sha256"] != hashlib.sha256(raw).hexdigest():
                    raise BundleError("bundle file hash or size does not match")
                destination = root / member_name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
                declared.add(member_name)
            if declared != set(members) - {"bundle.json"}:
                raise BundleError("bundle contains undeclared evidence")
        chain = evidence_manifest.verify_chain(
            root / "evidence" / "ledger",
            root / "evidence" / "public" / "instance_signing_key.pub",
        )
        if (
            chain["instance_id"] != document["instance_id"]
            or chain["chain_id"] != document["chain_id"]
            or chain["head_sha256"] != document["chain_head_sha256"]
            or chain["records"] != document["record_count"]
        ):
            raise BundleError("bundle manifest does not match its signed chain")
    return {
        "valid": True,
        "bundle_id": document["bundle_id"],
        "instance_id": document["instance_id"],
        "chain_id": document["chain_id"],
        "chain_head_sha256": document["chain_head_sha256"],
        "record_count": document["record_count"],
    }


def stage_git(bundle: Path, archive: Path) -> dict:
    summary = verify_bundle(bundle)
    destination = archive / "instances" / summary["instance_id"] / "bundles" / summary["bundle_id"]
    archive.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        receipt = destination / "bundle.sha256"
        if receipt.read_text(encoding="ascii") != f"{sha256_file(bundle)}  evidence.bundle\n":
            raise BundleError("bundle ID already exists with different content")
        return summary | {"destination": str(destination), "status": "already_staged"}
    temporary = destination.with_name(destination.name + ".partial")
    temporary.mkdir(parents=True)
    shutil.copyfile(bundle, temporary / "evidence.bundle")
    os.chmod(temporary / "evidence.bundle", 0o600)
    (temporary / "bundle.sha256").write_text(
        f"{sha256_file(temporary / 'evidence.bundle')}  evidence.bundle\n", encoding="ascii",
    )
    os.chmod(temporary / "bundle.sha256", 0o600)
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temporary, destination)
    return summary | {"destination": str(destination), "status": "staged"}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--evidence-home", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle", required=True, type=Path)
    git = commands.add_parser("stage-git")
    git.add_argument("--bundle", required=True, type=Path)
    git.add_argument("--archive", required=True, type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "create":
            result = create_bundle(args.evidence_home, args.output)
        elif args.command == "verify":
            result = verify_bundle(args.bundle)
            result["bundle_sha256"] = sha256_file(args.bundle)
        else:
            result = stage_git(args.bundle, args.archive)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (BundleError, evidence_manifest.EvidenceError, OSError, tarfile.TarError) as exc:
        parser().exit(1, f"evidence bundle error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
