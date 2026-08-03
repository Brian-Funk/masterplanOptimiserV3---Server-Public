#!/usr/bin/env python3
"""Create and verify deterministic, self-contained accountability bundles.

The protected installed copy of this module is authoritative for ingestion.
Code carried inside a bundle is provided only for offline controller use and is
never executed by the integrated Server uploader.
"""

from __future__ import annotations

import argparse
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
import zipfile
from typing import Any

import evidence_git
import evidence_manifest


FORMAT = "mp-opt-portable-evidence-bundle-v1"
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
MAX_EVIDENCE_ZIP_BYTES = MAX_BUNDLE_BYTES + (2 * 1024 * 1024)
BUNDLE_NAMESPACE = uuid.UUID("8c36ce0a-ec6a-4b9b-981e-dfb7f891da70")
PUBLIC_VERIFIER_URL = "https://brian-funk.github.io/masterplanOptimiserV3---Evidence-Public/verify-evidence/"
ZIP_MEMBERS = ("accountability.evidence", "accountability.evidence.sha256", "VERIFYING.txt")
LIMIT_TEXT = (
    "A valid signature proves that the identified key signed the exact statement shown. "
    "It does not prove physical deletion, absence of copies outside controlled systems, "
    "physical-world truth, or legal compliance."
)
ROOT_MEMBERS = {"bundle.json", "bundle.sha256", "VERIFYING.md"}
OFFLINE_SOURCES = {
    "tools/portable_bundle.py": "portable_bundle.py",
    "tools/evidence_git.py": "evidence_git.py",
    "tools/evidence_manifest.py": "evidence_manifest.py",
}


class PortableBundleError(ValueError):
    """Raised when a portable bundle is unsafe or unverifiable."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(
        value
        and value == path.as_posix()
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in value
    )


def _regular_bytes(path: Path, *, limit: int = 2 * 1024 * 1024) -> bytes:
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
        raise PortableBundleError(f"Unsafe bundle source file: {path}")
    return path.read_bytes()


def _add_bytes(archive: tarfile.TarFile, name: str, raw: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(raw)
    info.mode = 0o600
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    archive.addfile(info, _BytesReader(raw))


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


def _payload_files(repository: Path, result: dict[str, Any], instance_id: str) -> dict[str, bytes]:
    controller = repository / "trust"
    instance = repository / "instances" / instance_id
    paths = [
        controller / "controller.json",
        controller / "controller.json.sig",
        controller / "controller.pub",
        instance / "trust" / "instance.json",
        instance / "trust" / "instance.json.sig",
        instance / "trust" / "instance.pub",
    ]
    for processor_id in result["instances"][instance_id]["trust"]["processor_ids"]:
        paths += [
            controller / "processors" / f"{processor_id}.json",
            controller / "processors" / f"{processor_id}.json.sig",
        ]
    for directory_name in ("ledger", "requests", "purges", "attestations", "backups", "anchors"):
        directory = instance / directory_name
        paths += [path for path in sorted(directory.glob("*")) if path.is_file() and path.name != ".append.lock"]
    payload: dict[str, bytes] = {}
    for path in paths:
        relative = path.relative_to(repository).as_posix()
        payload[f"evidence/{relative}"] = _regular_bytes(path)
    payload[f"summaries/{instance_id}/evidence-summary.md"] = evidence_git.render_markdown(result, instance_id).encode("utf-8")
    payload[f"summaries/{instance_id}/evidence-summary.html"] = evidence_git.render_html(result, instance_id).encode("utf-8")
    module_directory = Path(__file__).resolve().parent
    for destination, source_name in OFFLINE_SOURCES.items():
        payload[destination] = _regular_bytes(module_directory / source_name)
    payload["tools/verify_offline.py"] = (
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import portable_bundle\n"
        "raise SystemExit(portable_bundle.cli(['verify', '--bundle', str(Path(__file__).resolve().parents[1] / 'evidence.bundle')]))\n"
    ).encode("utf-8")
    return payload


def create_bundle(repository: Path, instance_id: str, output: Path) -> dict[str, Any]:
    """Create identical bytes for an unchanged verified chain and trust state."""

    repository = repository.resolve()
    digest_path = Path(str(output) + ".sha256")
    if output.exists() or output.is_symlink() or digest_path.exists() or digest_path.is_symlink():
        raise PortableBundleError("Bundle output or digest already exists")
    result = evidence_git.verify_repository(repository, check_summaries=False)
    if instance_id not in result["instances"]:
        raise PortableBundleError("Requested instance is not present in verified evidence")
    instance = result["instances"][instance_id]
    records = instance["records"]
    if not records:
        raise PortableBundleError("Portable bundle requires a non-empty signed chain")
    payload = _payload_files(repository, result, instance_id)
    rows = [
        {"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
        for path, raw in sorted(payload.items())
    ]
    identity = "|".join((
        result["controller"]["controller_id"],
        instance_id,
        instance["chain"]["head_sha256"],
        hashlib.sha256(evidence_git.canonical_json({"files": rows})).hexdigest(),
    ))
    bundle_id = str(uuid.uuid5(BUNDLE_NAMESPACE, identity))
    document = {
        "format": FORMAT,
        "bundle_id": bundle_id,
        "created_at": records[-1]["created_at"],
        "controller_id": result["controller"]["controller_id"],
        "instance_id": instance_id,
        "chain_id": records[-1]["chain_id"],
        "chain_head_sha256": instance["chain"]["head_sha256"],
        "record_count": instance["chain"]["records"],
        "instance_key_id": instance["trust"]["signing_key_id"],
        "processor_ids": instance["trust"]["processor_ids"],
        "verification_limits": LIMIT_TEXT,
        "files": rows,
    }
    manifest = evidence_git.canonical_json(document)
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    verifying = (
        "# Offline verification\n\n"
        "Copy `evidence.bundle` to a trusted offline workstation with Python 3.11 or later and OpenSSH. "
        "Review the bundled verifier source, then run `python tools/verify_offline.py`.\n\n"
        "The integrated Server uploader must ignore bundled verifier code and use only its protected installed verifier.\n\n"
        f"{LIMIT_TEXT}\n"
    ).encode("utf-8")
    members = {
        "bundle.json": manifest,
        "bundle.sha256": f"{manifest_digest}  bundle.json\n".encode("ascii"),
        "VERIFYING.md": verifying,
    }
    members.update({f"payload/{name}": raw for name, raw in payload.items()})
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=output.name + ".",
        suffix=".partial",
        dir=output.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with tarfile.open(temporary, "w", format=tarfile.GNU_FORMAT) as archive:
            for name, raw in sorted(members.items()):
                _add_bytes(archive, name, raw)
        os.chmod(temporary, 0o600)
        verified = verify_bundle(temporary)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    bundle_digest = sha256_file(output)
    digest_path.write_text(f"{bundle_digest}  {output.name}\n", encoding="ascii", newline="\n")
    return verified | {"bundle_sha256": bundle_digest, "path": str(output), "digest_path": str(digest_path)}


def stage_archive(bundle: Path, archive: Path) -> dict[str, Any]:
    """Verify and idempotently stage only a portable bundle and its digest."""

    summary = verify_bundle(bundle)
    destination = (
        archive / "instances" / summary["instance_id"] / "bundles" /
        summary["bundle_id"]
    )
    target = destination / "evidence.bundle"
    receipt = destination / "bundle.sha256"
    digest = summary["bundle_sha256"]
    expected_receipt = f"{digest}  evidence.bundle\n"
    if destination.exists():
        if (
            target.is_file() and not target.is_symlink()
            and receipt.is_file() and not receipt.is_symlink()
            and sha256_file(target) == digest
            and receipt.read_text(encoding="ascii") == expected_receipt
        ):
            return summary | {"status": "already_staged", "destination": str(destination)}
        raise PortableBundleError("Bundle identity is already staged with different content")
    destination.mkdir(parents=True, exist_ok=False)
    temporary = destination / "evidence.bundle.partial"
    try:
        shutil.copyfile(bundle, temporary)
        os.chmod(temporary, 0o600)
        if sha256_file(temporary) != digest:
            raise PortableBundleError("Bundle changed while it was being staged")
        os.replace(temporary, target)
        receipt.write_text(expected_receipt, encoding="ascii", newline="\n")
        os.chmod(receipt, 0o600)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return summary | {"status": "staged", "destination": str(destination)}


def create_from_local(
    evidence_home: Path,
    trust_repository: Path,
    instance_id: str,
    output: Path,
) -> dict[str, Any]:
    """Combine the authoritative local ledger with controller-approved trust."""

    if evidence_home.is_symlink() or trust_repository.is_symlink():
        raise PortableBundleError("Local evidence or trust source is unsafe")
    evidence_home = evidence_home.resolve()
    trust_repository = trust_repository.resolve()
    with tempfile.TemporaryDirectory(prefix="mp-opt-portable-source.") as directory_name:
        repository = Path(directory_name) / "repository"
        shutil.copytree(trust_repository / "trust", repository / "trust", symlinks=True)
        source_instance_trust = trust_repository / "instances" / instance_id / "trust"
        shutil.copytree(
            source_instance_trust,
            repository / "instances" / instance_id / "trust",
            symlinks=True,
        )
        shutil.copytree(
            evidence_home / "ledger",
            repository / "instances" / instance_id / "ledger",
            symlinks=True,
        )
        source_public = evidence_home / "public" / "instance_signing_key.pub"
        target_public = repository / "instances" / instance_id / "trust" / "instance.pub"
        if evidence_manifest.canonical_public_key(source_public.read_text(encoding="ascii")) != evidence_manifest.canonical_public_key(target_public.read_text(encoding="ascii")):
            raise PortableBundleError("Local ledger key does not match authorised instance trust")
        for name in ("requests", "purges", "attestations", "backups", "anchors", "summaries"):
            target = repository / "instances" / instance_id / name
            target.mkdir(parents=True)
        return create_bundle(repository, instance_id, output)


def _zip_instructions(summary: dict[str, Any], bundle_sha256: str) -> bytes:
    return (
        "Masterplan Optimiser accountability evidence\n"
        "==============================================\n\n"
        "This ZIP contains one canonical signed evidence bundle and its SHA-256 receipt.\n"
        "No private signing, passkey, recovery or application secret is included.\n\n"
        "Browser verification (the files stay on your device):\n"
        f"{PUBLIC_VERIFIER_URL}\n\n"
        "Offline command-line verification:\n"
        "1. Extract accountability.evidence.\n"
        "2. Copy the protected verifier from a trusted Server or Evidence-Public checkout.\n"
        "3. Run: python portable_bundle.py verify --bundle accountability.evidence\n\n"
        f"Bundle SHA-256: {bundle_sha256}\n"
        f"Controller ID: {summary['controller_id']}\n"
        f"Instance ID: {summary['instance_id']}\n"
        f"Chain ID: {summary['chain_id']}\n"
        f"Chain head SHA-256: {summary['chain_head_sha256']}\n"
        f"Record count: {summary['record_count']}\n\n"
        f"{LIMIT_TEXT}\n"
    ).encode("utf-8")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o600 << 16
    return info


def create_evidence_zip(bundle: Path, output: Path) -> dict[str, Any]:
    """Wrap one verified canonical bundle in a deterministic three-file ZIP."""

    if output.exists() or output.is_symlink():
        raise PortableBundleError("Evidence ZIP output already exists")
    summary = verify_bundle(bundle)
    bundle_raw = _regular_bytes(bundle, limit=MAX_BUNDLE_BYTES)
    bundle_sha256 = hashlib.sha256(bundle_raw).hexdigest()
    members = {
        "accountability.evidence": bundle_raw,
        "accountability.evidence.sha256": f"{bundle_sha256}  accountability.evidence\n".encode("ascii"),
        "VERIFYING.txt": _zip_instructions(summary, bundle_sha256),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=output.name + ".", suffix=".partial", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=False) as archive:
            for name in ZIP_MEMBERS:
                archive.writestr(_zip_info(name), members[name])
        os.chmod(temporary, 0o600)
        verified = verify_evidence_zip(temporary)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return verified | {"path": str(output)}


def create_zip_from_local(
    evidence_home: Path,
    trust_repository: Path,
    instance_id: str,
    output: Path,
) -> dict[str, Any]:
    """Verify the local chain, create its canonical bundle, then wrap it as a ZIP."""

    with tempfile.TemporaryDirectory(prefix="mp-opt-evidence-export.") as directory_name:
        bundle = Path(directory_name) / "accountability.evidence"
        create_from_local(evidence_home, trust_repository, instance_id, bundle)
        return create_evidence_zip(bundle, output)


def verify_evidence_zip(path: Path) -> dict[str, Any]:
    """Verify the exact three-file export without trusting carried instructions."""

    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_EVIDENCE_ZIP_BYTES:
        raise PortableBundleError("Evidence ZIP is unavailable, unsafe or too large")
    with zipfile.ZipFile(path, "r") as archive:
        members = archive.infolist()
        if tuple(member.filename for member in members) != ZIP_MEMBERS:
            raise PortableBundleError("Evidence ZIP must contain exactly the three documented files")
        if any(
            member.is_dir()
            or member.flag_bits & 0x1
            or member.compress_type != zipfile.ZIP_STORED
            or member.date_time != (1980, 1, 1, 0, 0, 0)
            or member.create_system != 3
            or (member.external_attr >> 16) & 0o777 != 0o600
            or member.file_size > MAX_BUNDLE_BYTES
            for member in members
        ):
            raise PortableBundleError("Evidence ZIP contains an unsafe member")
        bundle_raw = archive.read("accountability.evidence")
        receipt = archive.read("accountability.evidence.sha256")
        instructions = archive.read("VERIFYING.txt")
    bundle_sha256 = hashlib.sha256(bundle_raw).hexdigest()
    if receipt != f"{bundle_sha256}  accountability.evidence\n".encode("ascii"):
        raise PortableBundleError("Evidence ZIP checksum receipt does not match")
    with tempfile.TemporaryDirectory(prefix="mp-opt-evidence-zip-verify.") as directory_name:
        bundle = Path(directory_name) / "accountability.evidence"
        bundle.write_bytes(bundle_raw)
        summary = verify_bundle(bundle)
    if instructions != _zip_instructions(summary, bundle_sha256):
        raise PortableBundleError("Evidence ZIP verification instructions were changed")
    return summary | {"zip_sha256": sha256_file(path), "valid_zip": True}


def _members(archive: tarfile.TarFile, bundle_size: int) -> dict[str, tarfile.TarInfo]:
    result: dict[str, tarfile.TarInfo] = {}
    total = 0
    for member in archive.getmembers():
        if not _safe_relative(member.name) or member.name in result:
            raise PortableBundleError("Bundle contains an unsafe or duplicate path")
        if not member.isfile() or member.issym() or member.islnk() or member.isdev() or member.mode != 0o600:
            raise PortableBundleError("Bundle members must be private regular files")
        if member.name not in ROOT_MEMBERS and not member.name.startswith("payload/"):
            raise PortableBundleError("Bundle contains an unexpected member")
        total += member.size
        if total > bundle_size:
            raise PortableBundleError("Bundle is sparse or has unsafe expanded size")
        result[member.name] = member
    if not ROOT_MEMBERS.issubset(result):
        raise PortableBundleError("Bundle root members are incomplete")
    return result


def _read(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    handle = archive.extractfile(member)
    if handle is None:
        raise PortableBundleError("Bundle member is unreadable")
    raw = handle.read(member.size + 1)
    if len(raw) != member.size:
        raise PortableBundleError("Bundle member is truncated")
    return raw


def verify_bundle(bundle: Path, *, expected_controller_id: str | None = None, expected_instance_id: str | None = None) -> dict[str, Any]:
    """Verify using this installed module, never code carried by the bundle."""

    metadata = bundle.lstat()
    if bundle.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BUNDLE_BYTES:
        raise PortableBundleError("Bundle is unavailable, unsafe or too large")
    with tempfile.TemporaryDirectory(prefix="mp-opt-portable-evidence.") as directory_name:
        temporary = Path(directory_name)
        evidence_root = temporary / "evidence"
        with tarfile.open(bundle, "r:") as archive:
            members = _members(archive, metadata.st_size)
            manifest_raw = _read(archive, members["bundle.json"])
            try:
                document = evidence_manifest.load_json_bytes(manifest_raw)
            except evidence_manifest.EvidenceError as exc:
                raise PortableBundleError("Bundle manifest is invalid") from exc
            if manifest_raw != evidence_git.canonical_json(document):
                raise PortableBundleError("Bundle manifest is not canonical")
            required = {
                "format", "bundle_id", "created_at", "controller_id", "instance_id", "chain_id",
                "chain_head_sha256", "record_count", "instance_key_id", "processor_ids",
                "verification_limits", "files",
            }
            if set(document) != required or document.get("format") != FORMAT:
                raise PortableBundleError("Bundle manifest schema is invalid")
            if document.get("verification_limits") != LIMIT_TEXT:
                raise PortableBundleError("Bundle verification limits are missing or changed")
            expected_manifest_digest = f"{hashlib.sha256(manifest_raw).hexdigest()}  bundle.json\n".encode("ascii")
            if _read(archive, members["bundle.sha256"]) != expected_manifest_digest:
                raise PortableBundleError("Bundle manifest digest does not match")
            try:
                if str(uuid.UUID(document["bundle_id"])) != document["bundle_id"]:
                    raise ValueError
                if str(uuid.UUID(document["instance_id"])) != document["instance_id"]:
                    raise ValueError
                if str(uuid.UUID(document["chain_id"])) != document["chain_id"]:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise PortableBundleError("Bundle identifiers are invalid") from exc
            rows = document.get("files")
            if not isinstance(rows, list) or not rows:
                raise PortableBundleError("Bundle file manifest is empty")
            declared: set[str] = set()
            payload: dict[str, bytes] = {}
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"path", "sha256", "size"}:
                    raise PortableBundleError("Bundle file row is invalid")
                path = row.get("path")
                member_name = f"payload/{path}"
                if not isinstance(path, str) or not _safe_relative(path) or member_name in declared or member_name not in members:
                    raise PortableBundleError("Bundle file path is unsafe, duplicate or missing")
                raw = _read(archive, members[member_name])
                if row.get("size") != len(raw) or row.get("sha256") != hashlib.sha256(raw).hexdigest():
                    raise PortableBundleError("Bundle file hash or size does not match")
                payload[path] = raw
                declared.add(member_name)
            if declared != set(members) - ROOT_MEMBERS:
                raise PortableBundleError("Bundle contains undeclared payload")
        for path, raw in payload.items():
            if not path.startswith("evidence/"):
                continue
            destination = temporary / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        (evidence_root / "trust" / "processors").mkdir(parents=True, exist_ok=True)
        instance_directory = evidence_root / "instances" / document["instance_id"]
        for name in ("requests", "purges", "attestations", "backups", "anchors", "summaries"):
            (instance_directory / name).mkdir(parents=True, exist_ok=True)
        result = evidence_git.verify_repository(evidence_root, check_summaries=False)
        if expected_controller_id is not None and document["controller_id"] != expected_controller_id:
            raise PortableBundleError("Bundle controller does not match the upload credential")
        if expected_instance_id is not None and document["instance_id"] != expected_instance_id:
            raise PortableBundleError("Bundle instance does not match the upload credential")
        if document["controller_id"] != result["controller"]["controller_id"]:
            raise PortableBundleError("Bundle manifest controller does not match verified trust")
        instance = result["instances"].get(document["instance_id"])
        if instance is None:
            raise PortableBundleError("Bundle manifest instance is missing from verified evidence")
        records = instance["records"]
        if (
            not records
            or document["chain_id"] != records[-1]["chain_id"]
            or document["chain_head_sha256"] != instance["chain"]["head_sha256"]
            or document["record_count"] != instance["chain"]["records"]
            or document["instance_key_id"] != instance["trust"]["signing_key_id"]
            or document["processor_ids"] != instance["trust"]["processor_ids"]
        ):
            raise PortableBundleError("Bundle manifest does not match verified evidence")
        expected_markdown = evidence_git.render_markdown(result, document["instance_id"]).encode("utf-8")
        expected_html = evidence_git.render_html(result, document["instance_id"]).encode("utf-8")
        if payload.get(f"summaries/{document['instance_id']}/evidence-summary.md") != expected_markdown:
            raise PortableBundleError("Bundle Markdown summary is not derived from verified evidence")
        if payload.get(f"summaries/{document['instance_id']}/evidence-summary.html") != expected_html:
            raise PortableBundleError("Bundle HTML summary is not derived from verified evidence")
        for destination in OFFLINE_SOURCES:
            if destination not in payload:
                raise PortableBundleError("Bundle offline verifier is incomplete")
        if "tools/verify_offline.py" not in payload:
            raise PortableBundleError("Bundle offline verifier entry point is missing")
    return {
        "valid": True,
        "bundle_id": document["bundle_id"],
        "controller_id": document["controller_id"],
        "instance_id": document["instance_id"],
        "chain_id": document["chain_id"],
        "chain_head_sha256": document["chain_head_sha256"],
        "record_count": document["record_count"],
        "record_sha256s": [hashlib.sha256(evidence_git.canonical_json(record)).hexdigest() for record in records],
        "bundle_sha256": sha256_file(bundle),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--repository", required=True, type=Path)
    create.add_argument("--instance-id", required=True)
    create.add_argument("--output", required=True, type=Path)
    local = commands.add_parser("create-local")
    local.add_argument("--evidence-home", required=True, type=Path)
    local.add_argument("--trust-repository", required=True, type=Path)
    local.add_argument("--instance-id", required=True)
    local.add_argument("--output", required=True, type=Path)
    local_zip = commands.add_parser("create-local-zip")
    local_zip.add_argument("--evidence-home", required=True, type=Path)
    local_zip.add_argument("--trust-repository", required=True, type=Path)
    local_zip.add_argument("--instance-id", required=True)
    local_zip.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle", required=True, type=Path)
    verify.add_argument("--controller-id")
    verify.add_argument("--instance-id")
    verify_zip = commands.add_parser("verify-zip")
    verify_zip.add_argument("--zip", required=True, type=Path)
    stage = commands.add_parser("stage-archive")
    stage.add_argument("--bundle", required=True, type=Path)
    stage.add_argument("--archive", required=True, type=Path)
    return result


def cli(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "create":
            result = create_bundle(arguments.repository, arguments.instance_id, arguments.output)
        elif arguments.command == "create-local":
            result = create_from_local(
                arguments.evidence_home,
                arguments.trust_repository,
                arguments.instance_id,
                arguments.output,
            )
        elif arguments.command == "create-local-zip":
            result = create_zip_from_local(
                arguments.evidence_home,
                arguments.trust_repository,
                arguments.instance_id,
                arguments.output,
            )
        elif arguments.command == "verify":
            result = verify_bundle(
                arguments.bundle,
                expected_controller_id=arguments.controller_id,
                expected_instance_id=arguments.instance_id,
            )
        elif arguments.command == "verify-zip":
            result = verify_evidence_zip(arguments.zip)
        else:
            result = stage_archive(arguments.bundle, arguments.archive)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (
        PortableBundleError,
        evidence_git.EvidenceGitError,
        evidence_manifest.EvidenceError,
        OSError,
        tarfile.TarError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ) as exc:
        parser().exit(1, f"portable evidence bundle error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(cli())
