#!/usr/bin/env python3
"""Export, verify and stage public MP-OPT accountability evidence."""

from __future__ import annotations

import argparse
import base64
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

import evidence_manifest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


FORMAT = "mp-opt-evidence-bundle-v2"
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
MAX_EVIDENCE_ZIP_BYTES = MAX_BUNDLE_BYTES + (2 * 1024 * 1024)
INCLUDED_ROOTS = {"ledger", "public", "anchors", "artifacts", "archive-trust"}
SIGNED_DESKTOP_EVIDENCE_PACKAGE_FORMAT = "mp-opt-signed-desktop-evidence-v1"
DESKTOP_EVIDENCE_NAMESPACE = "mp-opt-desktop-evidence-v1"
SIGNED_ARCHIVE_TRUST_PACKAGE_FORMAT = "mp-opt-signed-controller-archive-trust-v1"
ARCHIVE_TRUST_DOCUMENT_FORMAT = "mp-opt-controller-archive-trust-v1"
ARCHIVE_TRUST_SCOPE = "accountability_evidence_archive"
TRUST_NAMESPACE = "mp-opt-role-trust-v1"
BUNDLE_NAMESPACE = uuid.UUID("aa0c67fb-6a9c-4cf3-b712-c4cde822e7be")
PUBLIC_VERIFIER_URL = "https://brian-funk.github.io/masterplanOptimiserV3---Evidence-Public/verify-evidence/"
ZIP_MEMBERS = ("accountability.evidence", "accountability.evidence.sha256", "VERIFYING.txt")
LIMIT_TEXT = (
    "A valid signature proves that the identified key signed the exact statement shown. "
    "It does not prove physical deletion, absence of copies outside controlled systems, "
    "physical-world truth, or legal compliance."
)


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
    archive_trust = _verify_archive_trust(home, chain)
    files = evidence_files(home)
    payload = {
        path.relative_to(home).as_posix(): path.read_bytes()
        for path in files
    }
    rows = [
        {
            "path": path,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        }
        for path, raw in sorted(payload.items())
    ]
    latest = sorted((home / "ledger").glob("[0-9]" * 12 + "_*.json"))[-1]
    created_at = evidence_manifest.load_json_bytes(latest.read_bytes())["created_at"]
    identity = "|".join((
        archive_trust["controller_id"],
        chain["instance_id"],
        chain["chain_id"],
        chain["head_sha256"],
        archive_trust["statement_sha256"],
        hashlib.sha256((json.dumps({"files": rows}, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")).hexdigest(),
    ))
    document = {
        "format": FORMAT,
        "bundle_id": str(uuid.uuid5(BUNDLE_NAMESPACE, identity)),
        "created_at": created_at,
        "instance_id": chain["instance_id"],
        "controller_id": archive_trust["controller_id"],
        "controller_key_id": archive_trust["controller_key_id"],
        "controller_public_key_sha256": archive_trust["controller_public_key_sha256"],
        "instance_key_id": archive_trust["instance_key_id"],
        "archive_trust_sha256": archive_trust["statement_sha256"],
        "chain_id": chain["chain_id"],
        "chain_head_sha256": chain["head_sha256"],
        "record_count": chain["records"],
        "files": rows,
    }
    manifest = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = output.with_name(output.name + ".partial")
    try:
        with tarfile.open(temporary, "w", format=tarfile.GNU_FORMAT) as archive:
            _add_bytes(archive, "bundle.json", manifest)
            for path, raw in sorted(payload.items()):
                _add_bytes(archive, f"evidence/{path}", raw)
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


def _add_bytes(archive: tarfile.TarFile, name: str, raw: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(raw)
    info.mode = 0o600
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    archive.addfile(info, _BytesReader(raw))


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


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _document_binding(payload: dict, document: dict) -> tuple[str | None, str]:
    """Return the recorded and independently recalculated document digests.

    Policy acknowledgements use the signed-package canonical form. Deletion
    report and copy-resolution digests predate that package and intentionally
    use their domain canonical form without a trailing LF. The signature still
    covers the signed-package canonical document in every case.
    """

    fields = [
        field
        for field in ("document_sha256", "report_sha256", "copy_resolution_sha256")
        if field in payload
    ]
    if len(fields) != 1:
        return None, ""
    field = fields[0]
    if field == "document_sha256":
        rendered = _canonical(document)
    else:
        rendered = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    return payload[field], hashlib.sha256(rendered).hexdigest()


def _verify_archive_trust(root: Path, chain: dict) -> dict:
    """Verify controller authorisation of the exact instance evidence key."""

    directory = root / "archive-trust"
    if not directory.is_dir() or directory.is_symlink():
        raise BundleError("controller archive trust is missing or unsafe")
    paths = sorted(directory.glob("*.json"))
    if not paths or any(not re.fullmatch(r"[0-9a-f]{64}\.json", path.name) for path in paths):
        raise BundleError("controller archive trust files are invalid")
    instance_public = evidence_manifest.canonical_public_key(
        (root / "public" / "instance_signing_key.pub").read_text(encoding="ascii")
    )
    instance_key_id = evidence_manifest.key_id(instance_public)
    instance_fingerprint = hashlib.sha256(instance_public.encode("ascii")).hexdigest()
    ledger_bindings: list[str] = []
    for record_path in sorted((root / "ledger").glob("[0-9]" * 12 + "_*.json")):
        record = evidence_manifest.load_json_bytes(record_path.read_bytes())
        if record.get("record_type") == "evidence.archive_trust_bound":
            digest = record["payload"].get("statement_sha256")
            if isinstance(digest, str):
                ledger_bindings.append(digest)
    binding_set = set(ledger_bindings)
    verified: dict[str, dict] = {}
    for path in paths:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != path.stem or len(raw) > 128 * 1024:
            raise BundleError("controller archive trust digest does not match")
        try:
            package = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BundleError("controller archive trust package is invalid JSON") from exc
        if (
            raw != _canonical(package)
            or not isinstance(package, dict)
            or set(package) != {"format", "namespace", "document", "proof", "controller_public_key", "instance_public_key"}
            or package.get("format") != SIGNED_ARCHIVE_TRUST_PACKAGE_FORMAT
            or package.get("namespace") != TRUST_NAMESPACE
        ):
            raise BundleError("controller archive trust package schema is invalid")
        document = package["document"]
        proof = package["proof"]
        expected_document_fields = {
            "format", "instance_id", "controller_id", "controller_key_id",
            "controller_public_key_sha256", "instance_key_id",
            "instance_public_key_sha256", "scope", "signed_at",
        }
        if not isinstance(document, dict) or set(document) != expected_document_fields or not isinstance(proof, dict) or set(proof) != {"format", "key_id", "namespace", "signature"}:
            raise BundleError("controller archive trust document or proof is invalid")
        try:
            controller_public = evidence_manifest.canonical_public_key(package["controller_public_key"])
            package_instance_public = evidence_manifest.canonical_public_key(package["instance_public_key"])
            loaded = serialization.load_ssh_public_key(controller_public.encode("ascii"))
            signature = base64.b64decode(proof["signature"], validate=True)
        except (ValueError, TypeError) as exc:
            raise BundleError("controller archive trust key or signature is invalid") from exc
        controller_key_id = evidence_manifest.key_id(controller_public)
        controller_fingerprint = hashlib.sha256(controller_public.encode("ascii")).hexdigest()
        if (
            not isinstance(loaded, Ed25519PublicKey)
            or len(signature) != 64
            or proof["format"] != "mp-opt-ed25519-signature-v1"
            or proof["namespace"] != TRUST_NAMESPACE
            or proof["key_id"] != controller_key_id
            or document["format"] != ARCHIVE_TRUST_DOCUMENT_FORMAT
            or document["scope"] != ARCHIVE_TRUST_SCOPE
            or document["instance_id"] != chain["instance_id"]
            or document["controller_key_id"] != controller_key_id
            or document["controller_public_key_sha256"] != controller_fingerprint
            or document["instance_key_id"] != instance_key_id
            or document["instance_public_key_sha256"] != instance_fingerprint
            or package_instance_public != instance_public
            or not re.fullmatch(r"ctl-[a-z0-9]{8,48}", str(document["controller_id"]))
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", str(document["signed_at"]))
        ):
            raise BundleError("controller archive trust identity does not match the evidence chain")
        try:
            loaded.verify(signature, TRUST_NAMESPACE.encode("ascii") + b"\0" + _canonical(document))
        except Exception as exc:
            raise BundleError("controller archive trust signature does not verify") from exc
        if digest not in binding_set:
            raise BundleError("controller archive trust is not bound into the signed ledger")
        verified[digest] = {
            "controller_id": document["controller_id"],
            "controller_key_id": controller_key_id,
            "controller_public_key_sha256": controller_fingerprint,
            "instance_key_id": instance_key_id,
            "statement_sha256": digest,
        }
    if not ledger_bindings or set(verified) != binding_set:
        raise BundleError("controller archive trust history is incomplete")
    current = verified.get(ledger_bindings[-1])
    if current is None:
        raise BundleError("controller archive trust is unavailable")
    return current


def _verify_processor_artifacts(root: Path) -> int:
    """Verify every Desktop proof referenced by the signed instance ledger."""

    references: dict[str, dict] = {}
    for path in sorted((root / "ledger").glob("[0-9]" * 12 + "_*.json")):
        record = evidence_manifest.load_json_bytes(path.read_bytes())
        digest = record["payload"].get("evidence_package_sha256")
        if digest is not None:
            if digest in references:
                raise BundleError("an evidence artifact is referenced more than once")
            references[digest] = record["payload"]

    artifacts = root / "artifacts"
    present = set()
    if artifacts.exists():
        present = {path.stem for path in artifacts.glob("*.json") if path.is_file()}
        if any(path.suffix != ".json" or not path.is_file() for path in artifacts.iterdir()):
            raise BundleError("the Desktop evidence artifact directory is invalid")
    if present != set(references):
        raise BundleError("Desktop evidence artifacts do not exactly match the signed ledger")

    for digest, payload in references.items():
        path = artifacts / f"{digest}.json"
        raw = path.read_bytes()
        if len(raw) > 128 * 1024 or hashlib.sha256(raw).hexdigest() != digest:
            raise BundleError("a Desktop evidence artifact digest does not match")
        try:
            package = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BundleError("a Desktop evidence artifact is invalid JSON") from exc
        if (
            not isinstance(package, dict)
            or set(package) != {"format", "namespace", "document", "proof", "public_key"}
            or package.get("format") != SIGNED_DESKTOP_EVIDENCE_PACKAGE_FORMAT
            or package.get("namespace") != DESKTOP_EVIDENCE_NAMESPACE
            or raw != _canonical(package)
        ):
            raise BundleError("a Desktop evidence artifact schema is invalid")
        document = package["document"]
        proof = package["proof"]
        if not isinstance(document, dict) or not isinstance(proof, dict) or set(proof) != {
            "format", "key_id", "namespace", "signature",
        }:
            raise BundleError("a Desktop evidence proof envelope is invalid")
        try:
            public_key = evidence_manifest.canonical_public_key(package["public_key"])
            loaded = serialization.load_ssh_public_key(public_key.encode("ascii"))
            signature = base64.b64decode(proof["signature"], validate=True)
        except (ValueError, TypeError) as exc:
            raise BundleError("a Desktop evidence public key or signature is invalid") from exc
        if (
            not isinstance(loaded, Ed25519PublicKey)
            or len(signature) != 64
            or proof.get("format") != "mp-opt-ed25519-signature-v1"
            or proof.get("namespace") != DESKTOP_EVIDENCE_NAMESPACE
            or proof.get("key_id") != evidence_manifest.key_id(public_key)
        ):
            raise BundleError("a Desktop evidence signature identity is invalid")
        try:
            loaded.verify(
                signature,
                DESKTOP_EVIDENCE_NAMESPACE.encode("ascii") + b"\0" + _canonical(document),
            )
        except Exception as exc:
            raise BundleError("a Desktop evidence signature does not verify") from exc
        expected_document, document_digest = _document_binding(payload, document)
        expected_key = payload.get("key_id", payload.get("processor_key_id"))
        expected_fingerprint = payload.get("public_key_sha256", payload.get("completed_public_key_sha256"))
        if (
            expected_document != document_digest
            or payload.get("signature_sha256") != hashlib.sha256(_canonical(proof)).hexdigest()
            or expected_key != proof["key_id"]
            or expected_fingerprint != hashlib.sha256(public_key.encode("ascii")).hexdigest()
            or document.get("key_id") != proof["key_id"]
            or document.get("public_key_sha256") != expected_fingerprint
        ):
            raise BundleError("a Desktop evidence artifact does not match its signed ledger record")
    return len(references)


def verify_bundle(
    bundle: Path,
    *,
    expected_controller_id: str | None = None,
    expected_instance_id: str | None = None,
) -> dict:
    metadata = bundle.lstat()
    if not stat.S_ISREG(metadata.st_mode) or bundle.is_symlink() or metadata.st_size > MAX_BUNDLE_BYTES:
        raise BundleError("bundle is unavailable, unsafe or too large")
    with tempfile.TemporaryDirectory(prefix="mp-opt-evidence-bundle.") as directory_name:
        root = Path(directory_name)
        with tarfile.open(bundle, "r:") as archive:
            members = _safe_members(archive, metadata.st_size)
            manifest_raw = _read_member(archive, members["bundle.json"])
            try:
                document = json.loads(manifest_raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BundleError("bundle manifest is invalid") from exc
            required = {
                "format", "bundle_id", "created_at", "controller_id",
                "controller_key_id", "controller_public_key_sha256",
                "instance_id", "instance_key_id", "archive_trust_sha256",
                "chain_id", "chain_head_sha256", "record_count", "files",
            }
            if not isinstance(document, dict) or set(document) != required or document.get("format") != FORMAT:
                raise BundleError("bundle manifest schema is invalid")
            canonical_manifest = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            if manifest_raw != canonical_manifest:
                raise BundleError("bundle manifest is not canonical")
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
        archive_trust = _verify_archive_trust(root / "evidence", chain)
        if (
            chain["instance_id"] != document["instance_id"]
            or chain["chain_id"] != document["chain_id"]
            or chain["head_sha256"] != document["chain_head_sha256"]
            or chain["records"] != document["record_count"]
            or archive_trust["controller_id"] != document["controller_id"]
            or archive_trust["controller_key_id"] != document["controller_key_id"]
            or archive_trust["controller_public_key_sha256"] != document["controller_public_key_sha256"]
            or archive_trust["instance_key_id"] != document["instance_key_id"]
            or archive_trust["statement_sha256"] != document["archive_trust_sha256"]
        ):
            raise BundleError("bundle manifest does not match its signed chain")
        processor_artifacts = _verify_processor_artifacts(root / "evidence")
        identity = "|".join((
            document["controller_id"],
            document["instance_id"],
            document["chain_id"],
            document["chain_head_sha256"],
            document["archive_trust_sha256"],
            hashlib.sha256((json.dumps({"files": document["files"]}, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")).hexdigest(),
        ))
        if document["bundle_id"] != str(uuid.uuid5(BUNDLE_NAMESPACE, identity)):
            raise BundleError("bundle deterministic identity does not match")
        if expected_controller_id is not None and document["controller_id"] != expected_controller_id:
            raise BundleError("bundle controller identity does not match the configured archive")
        if expected_instance_id is not None and document["instance_id"] != expected_instance_id:
            raise BundleError("bundle instance identity does not match the configured archive")
        record_sha256s = [
            hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((root / "evidence" / "ledger").glob("[0-9]" * 12 + "_*.json"))
        ]
    return {
        "valid": True,
        "bundle_sha256": sha256_file(bundle),
        "bundle_id": document["bundle_id"],
        "controller_id": document["controller_id"],
        "controller_key_id": document["controller_key_id"],
        "controller_public_key_sha256": document["controller_public_key_sha256"],
        "instance_id": document["instance_id"],
        "instance_key_id": document["instance_key_id"],
        "archive_trust_sha256": document["archive_trust_sha256"],
        "chain_id": document["chain_id"],
        "chain_head_sha256": document["chain_head_sha256"],
        "record_count": document["record_count"],
        "record_sha256s": record_sha256s,
        "processor_artifact_count": processor_artifacts,
    }


def _zip_instructions(summary: dict, bundle_sha256: str) -> bytes:
    return (
        "Masterplan Optimiser accountability evidence\n"
        "==============================================\n\n"
        "This ZIP contains one canonical signed evidence bundle and its SHA-256 receipt.\n"
        "No private signing, passkey, recovery or application secret is included.\n\n"
        "Browser verification (the files stay on your device):\n"
        f"{PUBLIC_VERIFIER_URL}\n\n"
        "Offline command-line verification:\n"
        "1. Extract accountability.evidence.\n"
        "2. Copy evidence_bundle.py and evidence_manifest.py from a trusted Server source checkout.\n"
        "3. Run: python evidence_bundle.py verify --bundle accountability.evidence\n\n"
        f"Bundle SHA-256: {bundle_sha256}\n"
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


def create_evidence_zip(home: Path, output: Path) -> dict:
    """Create the same deterministic complete evidence ZIP for web and TUI."""

    if output.exists() or output.is_symlink():
        raise BundleError("evidence ZIP output already exists")
    with tempfile.TemporaryDirectory(prefix="mp-opt-complete-evidence.") as directory_name:
        bundle = Path(directory_name) / "accountability.evidence"
        summary = create_bundle(home, bundle)
        bundle_raw = bundle.read_bytes()
    bundle_sha256 = hashlib.sha256(bundle_raw).hexdigest()
    members = {
        "accountability.evidence": bundle_raw,
        "accountability.evidence.sha256": f"{bundle_sha256}  accountability.evidence\n".encode("ascii"),
        "VERIFYING.txt": _zip_instructions(summary, bundle_sha256),
    }
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = output.with_name(output.name + ".partial")
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


def verify_evidence_zip(path: Path) -> dict:
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_EVIDENCE_ZIP_BYTES:
        raise BundleError("evidence ZIP is unavailable, unsafe or too large")
    with zipfile.ZipFile(path, "r") as archive:
        members = archive.infolist()
        if tuple(member.filename for member in members) != ZIP_MEMBERS:
            raise BundleError("evidence ZIP must contain exactly the three documented files")
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
            raise BundleError("evidence ZIP contains an unsafe member")
        bundle_raw = archive.read("accountability.evidence")
        receipt = archive.read("accountability.evidence.sha256")
        instructions = archive.read("VERIFYING.txt")
    bundle_sha256 = hashlib.sha256(bundle_raw).hexdigest()
    if receipt != f"{bundle_sha256}  accountability.evidence\n".encode("ascii"):
        raise BundleError("evidence ZIP checksum receipt does not match")
    with tempfile.TemporaryDirectory(prefix="mp-opt-complete-evidence-verify.") as directory_name:
        bundle = Path(directory_name) / "accountability.evidence"
        bundle.write_bytes(bundle_raw)
        summary = verify_bundle(bundle)
    if instructions != _zip_instructions(summary, bundle_sha256):
        raise BundleError("evidence ZIP verification instructions were changed")
    return summary | {"bundle_sha256": bundle_sha256, "zip_sha256": sha256_file(path), "valid_zip": True}


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
    create_zip = commands.add_parser("create-zip")
    create_zip.add_argument("--evidence-home", required=True, type=Path)
    create_zip.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle", required=True, type=Path)
    verify_zip = commands.add_parser("verify-zip")
    verify_zip.add_argument("--zip", required=True, type=Path)
    git = commands.add_parser("stage-git")
    git.add_argument("--bundle", required=True, type=Path)
    git.add_argument("--archive", required=True, type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "create":
            result = create_bundle(args.evidence_home, args.output)
        elif args.command == "create-zip":
            result = create_evidence_zip(args.evidence_home, args.output)
        elif args.command == "verify":
            result = verify_bundle(args.bundle)
            result["bundle_sha256"] = sha256_file(args.bundle)
        elif args.command == "verify-zip":
            result = verify_evidence_zip(args.zip)
        else:
            result = stage_git(args.bundle, args.archive)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (
        BundleError,
        evidence_manifest.EvidenceError,
        OSError,
        tarfile.TarError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ) as exc:
        parser().exit(1, f"evidence bundle error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
