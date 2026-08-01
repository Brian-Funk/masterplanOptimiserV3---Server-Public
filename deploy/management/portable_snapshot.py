#!/usr/bin/env python3
"""Create and import one safe, OS-independent MP-OPT snapshot package."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile
import uuid


PACKAGE_FORMAT = "mp-opt-portable-snapshot-2026-01"
SNAPSHOT_RECEIPT_FORMAT = "mp-opt-snapshot-receipt-v2"
MAX_PACKAGE_BYTES = 10 * 1024**3
MIN_FREE_RESERVE_BYTES = 1024**3
SNAPSHOT_NAME = re.compile(
    r"^[0-9]{8}T[0-9]{6}Z_(?P<type>database|secrets|full)_[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
)
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
AGE_RECIPIENT = re.compile(r"^age1[0-9a-z]+$")
KEY_ID = re.compile(r"^rk-[0-9a-f]{16}$")
PACKAGE_MEMBERS = (
    "portable.json",
    "snapshot/snapshot.tar.age",
    "snapshot/archive.sha256",
    "snapshot/receipt.json",
)
SNAPSHOT_FILES = ("snapshot.tar.age", "archive.sha256", "receipt.json")


class PackageError(ValueError):
    """A portable package is incomplete, unsafe or internally inconsistent."""


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def regular_file(path: Path, *, nonempty: bool = True) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PackageError(f"required file is unavailable: {path.name}") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise PackageError(f"required path is not a regular file: {path.name}")
    if nonempty and metadata.st_size <= 0:
        raise PackageError(f"required file is empty: {path.name}")
    return metadata


def read_json_bytes(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PackageError(f"{label} must be a JSON object")
    return value


def read_json_file(path: Path, label: str, maximum: int = 1024 * 1024) -> dict:
    metadata = regular_file(path)
    if metadata.st_size > maximum:
        raise PackageError(f"{label} is too large")
    return read_json_bytes(path.read_bytes(), label)


def validate_receipt(receipt: dict, archive_hash: str, archive_size: int) -> None:
    if receipt.get("format") != SNAPSHOT_RECEIPT_FORMAT:
        raise PackageError("only mp-opt-snapshot-receipt-v2 is supported")
    if receipt.get("source_manifest_format") not in {None, "mp-opt-snapshot-v2"}:
        raise PackageError("snapshot receipt identifies an unsupported encrypted manifest")
    if receipt.get("type") not in {"database", "secrets", "full"}:
        raise PackageError("snapshot receipt has an invalid type")
    if receipt.get("archive_sha256") != archive_hash:
        raise PackageError("snapshot receipt archive SHA-256 does not match")
    if receipt.get("archive_size") != archive_size:
        raise PackageError("snapshot receipt archive size does not match")
    encryption = receipt.get("encryption")
    if not isinstance(encryption, dict) or encryption.get("scheme") != "age-x25519":
        raise PackageError("snapshot receipt has invalid encryption metadata")
    recipient = encryption.get("recipient")
    fingerprint = encryption.get("recipient_sha256")
    key_id = encryption.get("recovery_key_id")
    if not isinstance(recipient, str) or not AGE_RECIPIENT.fullmatch(recipient):
        raise PackageError("snapshot receipt has an invalid age recipient")
    expected_fingerprint = hashlib.sha256(recipient.encode("ascii")).hexdigest()
    if fingerprint != expected_fingerprint:
        raise PackageError("snapshot recipient fingerprint does not match")
    if not isinstance(key_id, str) or not KEY_ID.fullmatch(key_id):
        raise PackageError("snapshot receipt has an invalid recovery key id")
    if key_id != f"rk-{expected_fingerprint[:16]}":
        raise PackageError("snapshot recovery key id does not match its recipient")


def validate_snapshot_directory(snapshot: Path, canonical_name: str | None = None) -> tuple[dict, list[dict]]:
    if not snapshot.is_dir() or snapshot.is_symlink():
        raise PackageError("snapshot path is not a regular directory")
    snapshot_name = canonical_name or snapshot.name
    name_match = SNAPSHOT_NAME.fullmatch(snapshot_name)
    if not name_match:
        raise PackageError("snapshot directory name is invalid")
    expected = set(SNAPSHOT_FILES)
    actual = {entry.name for entry in snapshot.iterdir()}
    if actual != expected:
        raise PackageError("snapshot directory must contain exactly three managed files")
    file_rows: list[dict] = []
    for filename in SNAPSHOT_FILES:
        path = snapshot / filename
        metadata = regular_file(path)
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise PackageError(f"snapshot file must have mode 0600: {filename}")
        file_rows.append({
            "path": f"snapshot/{filename}",
            "size": metadata.st_size,
            "mode": "0600",
            "sha256": sha256_path(path),
        })
    archive = snapshot / "snapshot.tar.age"
    archive_hash = next(row["sha256"] for row in file_rows if row["path"].endswith("snapshot.tar.age"))
    checksum_text = (snapshot / "archive.sha256").read_text(encoding="ascii")
    if checksum_text != f"{archive_hash}  snapshot.tar.age\n":
        raise PackageError("archive.sha256 is not the canonical archive checksum")
    receipt = read_json_file(snapshot / "receipt.json", "snapshot receipt")
    validate_receipt(receipt, archive_hash, archive.stat().st_size)
    if receipt["type"] != name_match.group("type"):
        raise PackageError("snapshot directory type does not match its receipt")
    return receipt, file_rows


def portable_document(snapshot: Path, receipt: dict, rows: list[dict], source_node: str) -> dict:
    if source_node and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", source_node):
        raise PackageError("source node identifier is invalid")
    encryption = receipt["encryption"]
    return {
        "format": PACKAGE_FORMAT,
        "package_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_directory": snapshot.name,
        "source_node_id": source_node or None,
        "snapshot": {
            "type": receipt["type"],
            "name": receipt.get("name", ""),
            "created_at": receipt.get("created_at", ""),
            "archive_sha256": receipt["archive_sha256"],
            "recipient_sha256": encryption["recipient_sha256"],
            "recovery_key_id": encryption["recovery_key_id"],
        },
        "files": rows,
    }


def tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = 0o600
    info.mtime = int(datetime.now(timezone.utc).timestamp())
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def create_package(snapshot: Path, output: Path, source_node: str = "") -> dict:
    receipt, rows = validate_snapshot_directory(snapshot)
    document = portable_document(snapshot, receipt, rows, source_node)
    portable = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.exists() or output.is_symlink():
        raise PackageError("portable output already exists")
    temporary = output.with_name(output.name + ".partial")
    if temporary.exists() or temporary.is_symlink():
        raise PackageError("portable output staging path already exists")
    try:
        with tarfile.open(temporary, "w", format=tarfile.PAX_FORMAT) as archive:
            archive.addfile(tar_info("portable.json", len(portable)), fileobj=_BytesReader(portable))
            for filename in SNAPSHOT_FILES:
                path = snapshot / filename
                with path.open("rb") as handle:
                    archive.addfile(tar_info(f"snapshot/{filename}", path.stat().st_size), handle)
        os.chmod(temporary, 0o600)
        validated = validate_package(temporary)
        os.replace(temporary, output)
        package_hash = sha256_path(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "format": PACKAGE_FORMAT,
        "status": "exported",
        "package_id": validated["document"]["package_id"],
        "snapshot_directory": snapshot.name,
        "path": str(output.resolve()),
        "size": output.stat().st_size,
        "sha256": package_hash,
    }


class _BytesReader:
    def __init__(self, value: bytes):
        self.value = value
        self.offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.value) - self.offset
        result = self.value[self.offset:self.offset + size]
        self.offset += len(result)
        return result


def safe_members(archive: tarfile.TarFile, package_size: int) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or member.name != path.as_posix():
            raise PackageError("portable package contains an unsafe member path")
        if member.name in members:
            raise PackageError("portable package contains a duplicate member")
        if member.name not in PACKAGE_MEMBERS:
            raise PackageError("portable package contains an unexpected member")
        if not member.isfile() or member.issym() or member.islnk() or member.isdev():
            raise PackageError("portable package members must be regular files")
        if member.mode != 0o600:
            raise PackageError("portable package members must have mode 0600")
        members[member.name] = member
    if set(members) != set(PACKAGE_MEMBERS):
        raise PackageError("portable package is incomplete")
    if members["snapshot/snapshot.tar.age"].size <= 0:
        raise PackageError("portable encrypted snapshot is empty")
    if sum(member.size for member in members.values()) > package_size:
        raise PackageError("portable package declares sparse or oversized member data")
    if members["portable.json"].size > 64 * 1024:
        raise PackageError("portable metadata is too large")
    if members["snapshot/receipt.json"].size > 1024 * 1024:
        raise PackageError("snapshot receipt is too large")
    if members["snapshot/archive.sha256"].size > 1024:
        raise PackageError("archive checksum is too large")
    return members


def read_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    handle = archive.extractfile(member)
    if handle is None:
        raise PackageError(f"portable package member is unreadable: {member.name}")
    value = handle.read(member.size + 1)
    if len(value) != member.size:
        raise PackageError(f"portable package member is truncated: {member.name}")
    return value


def sha256_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    handle = archive.extractfile(member)
    if handle is None:
        raise PackageError(f"portable package member is unreadable: {member.name}")
    digest = hashlib.sha256()
    read = 0
    while True:
        block = handle.read(1024 * 1024)
        if not block:
            break
        read += len(block)
        if read > member.size:
            raise PackageError(f"portable package member exceeds its declared size: {member.name}")
        digest.update(block)
    if read != member.size:
        raise PackageError(f"portable package member is truncated: {member.name}")
    return digest.hexdigest()


def validate_portable_document(document: dict, members: dict[str, tarfile.TarInfo]) -> None:
    if document.get("format") != PACKAGE_FORMAT:
        raise PackageError("portable package format is unsupported")
    try:
        uuid.UUID(str(document.get("package_id", "")))
    except ValueError as exc:
        raise PackageError("portable package id is invalid") from exc
    snapshot_name = document.get("snapshot_directory")
    if not isinstance(snapshot_name, str) or not SNAPSHOT_NAME.fullmatch(snapshot_name):
        raise PackageError("portable snapshot directory name is invalid")
    rows = document.get("files")
    if not isinstance(rows, list) or len(rows) != 3:
        raise PackageError("portable package file manifest is invalid")
    mapped: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("path") in mapped:
            raise PackageError("portable package file manifest contains duplicates")
        path = row.get("path")
        if path not in PACKAGE_MEMBERS[1:]:
            raise PackageError("portable package file manifest contains an unexpected path")
        if row.get("mode") != "0600" or row.get("size") != members[path].size:
            raise PackageError("portable package file metadata does not match")
        if not isinstance(row.get("sha256"), str) or not HEX_SHA256.fullmatch(row["sha256"]):
            raise PackageError("portable package file SHA-256 is invalid")
        mapped[path] = row
    if set(mapped) != set(PACKAGE_MEMBERS[1:]):
        raise PackageError("portable package file manifest is incomplete")


def validate_package(package: Path) -> dict:
    metadata = regular_file(package)
    if metadata.st_size > MAX_PACKAGE_BYTES:
        raise PackageError("portable package exceeds the 10 GiB limit")
    with tarfile.open(package, "r:") as archive:
        members = safe_members(archive, metadata.st_size)
        document = read_json_bytes(read_member(archive, members["portable.json"]), "portable metadata")
        validate_portable_document(document, members)
        rows = {row["path"]: row for row in document["files"]}
        for member_name in PACKAGE_MEMBERS[1:]:
            if sha256_member(archive, members[member_name]) != rows[member_name]["sha256"]:
                raise PackageError(f"portable package member hash does not match: {member_name}")
        receipt = read_json_bytes(read_member(archive, members["snapshot/receipt.json"]), "snapshot receipt")
        checksum = read_member(archive, members["snapshot/archive.sha256"]).decode("ascii")
        archive_row = next(row for row in document["files"] if row["path"] == "snapshot/snapshot.tar.age")
        expected_checksum = f"{archive_row['sha256']}  snapshot.tar.age\n"
        if checksum != expected_checksum:
            raise PackageError("portable archive checksum is not canonical")
        validate_receipt(receipt, archive_row["sha256"], archive_row["size"])
        summary = document.get("snapshot")
        directory_match = SNAPSHOT_NAME.fullmatch(document["snapshot_directory"])
        if not isinstance(summary, dict) or any((
            summary.get("type") != receipt.get("type"),
            directory_match is None or directory_match.group("type") != receipt.get("type"),
            summary.get("archive_sha256") != receipt.get("archive_sha256"),
            summary.get("recipient_sha256") != receipt["encryption"].get("recipient_sha256"),
            summary.get("recovery_key_id") != receipt["encryption"].get("recovery_key_id"),
        )):
            raise PackageError("portable snapshot summary does not match its receipt")
    return {"document": document, "receipt": receipt, "size": metadata.st_size, "sha256": sha256_path(package)}


def copy_member(archive: tarfile.TarFile, member: tarfile.TarInfo, target: Path, expected_hash: str) -> None:
    source = archive.extractfile(member)
    if source is None:
        raise PackageError(f"portable package member is unreadable: {member.name}")
    digest = hashlib.sha256()
    written = 0
    with target.open("xb") as destination:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            written += len(block)
            if written > member.size:
                raise PackageError(f"portable package member exceeds its declared size: {member.name}")
            digest.update(block)
            destination.write(block)
        destination.flush()
        os.fsync(destination.fileno())
    os.chmod(target, 0o600)
    if written != member.size or digest.hexdigest() != expected_hash:
        raise PackageError(f"portable package member hash does not match: {member.name}")


def same_snapshot(left: Path, right: Path) -> bool:
    return all(
        regular_file(left / name).st_size == regular_file(right / name).st_size
        and sha256_path(left / name) == sha256_path(right / name)
        for name in SNAPSHOT_FILES
    )


def import_package(package: Path, snapshots: Path, expected_sha256: str = "") -> dict:
    if expected_sha256 and not HEX_SHA256.fullmatch(expected_sha256):
        raise PackageError("expected package SHA-256 must contain 64 lowercase hexadecimal characters")
    validated = validate_package(package)
    if expected_sha256 and validated["sha256"] != expected_sha256:
        raise PackageError("portable package SHA-256 does not match the expected value")
    snapshots.mkdir(parents=True, exist_ok=True, mode=0o700)
    snapshot_root_metadata = snapshots.lstat()
    if not stat.S_ISDIR(snapshot_root_metadata.st_mode) or snapshots.is_symlink():
        raise PackageError("snapshot storage must be a real directory, not a symbolic link")
    os.chmod(snapshots, 0o700)
    free = shutil.disk_usage(snapshots).free
    required = validated["size"] * 2 + MIN_FREE_RESERVE_BYTES
    if free < required:
        raise PackageError("insufficient free space for guarded portable import")
    name = validated["document"]["snapshot_directory"]
    target = snapshots / name
    staging = Path(tempfile.mkdtemp(prefix=".portable-import.", dir=snapshots))
    os.chmod(staging, 0o700)
    try:
        rows = {row["path"]: row for row in validated["document"]["files"]}
        with tarfile.open(package, "r:") as archive:
            members = safe_members(archive, validated["size"])
            for member_name, filename in zip(PACKAGE_MEMBERS[1:], SNAPSHOT_FILES, strict=True):
                copy_member(archive, members[member_name], staging / filename, rows[member_name]["sha256"])
        validate_snapshot_directory(staging, name)
        if target.exists() or target.is_symlink():
            target_valid = False
            try:
                validate_snapshot_directory(target, name)
                target_valid = True
            except (OSError, PackageError, UnicodeError):
                pass
            if target_valid and same_snapshot(staging, target):
                shutil.rmtree(staging)
                return {
                    "format": PACKAGE_FORMAT,
                    "status": "already-present",
                    "package_id": validated["document"]["package_id"],
                    "snapshot_directory": name,
                    "package_sha256": validated["sha256"],
                }
            raise PackageError("a different snapshot already uses this directory name")
        os.replace(staging, target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {
        "format": PACKAGE_FORMAT,
        "status": "imported",
        "package_id": validated["document"]["package_id"],
        "snapshot_directory": name,
        "package_sha256": validated["sha256"],
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--snapshot", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--source-node", default="")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--package", required=True, type=Path)
    import_command = commands.add_parser("import")
    import_command.add_argument("--package", required=True, type=Path)
    import_command.add_argument("--snapshots", required=True, type=Path)
    import_command.add_argument("--expected-sha256", default="")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "export":
            result = create_package(args.snapshot, args.output, args.source_node)
        elif args.command == "inspect":
            validated = validate_package(args.package)
            result = {
                "format": PACKAGE_FORMAT,
                "status": "valid",
                "package_id": validated["document"]["package_id"],
                "snapshot_directory": validated["document"]["snapshot_directory"],
                "snapshot": validated["document"]["snapshot"],
                "size": validated["size"],
                "sha256": validated["sha256"],
            }
        else:
            result = import_package(args.package, args.snapshots, args.expected_sha256)
    except (OSError, tarfile.TarError, PackageError, UnicodeError) as exc:
        print(json.dumps({"format": PACKAGE_FORMAT, "status": "error", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
