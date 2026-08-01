#!/usr/bin/env python3
"""Offline controller-custody utility for a trusted controller workstation.

This tool is not imported by or packaged into the Server runtime. It creates an
encrypted Ed25519 private key locally and exports only public packages and
detached signatures for transfer to a Masterplan deployment.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


NAMESPACE = "mp-opt-role-trust-v1"
CONTROLLER_RE = re.compile(r"^ctl-[a-z0-9]{8,48}$")
KEY_RE = re.compile(r"^ek-[0-9a-f]{16}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class CustodyError(ValueError):
    pass


def canonical_json(value: dict[str, Any]) -> bytes:
    if not isinstance(value, dict) or len(value) > 32:
        raise CustodyError("document must be one bounded JSON object")
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(raw) > 64 * 1024 or b"PRIVATE KEY" in raw:
        raise CustodyError("document is oversized or contains private-key material")
    return raw


def public_text(private: Ed25519PrivateKey) -> str:
    return private.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode("ascii")


def key_id(public: str) -> str:
    return "ek-" + hashlib.sha256(public.encode("ascii")).hexdigest()[:16]


def _passphrase(confirm: bool = False) -> bytes:
    first = getpass.getpass("Controller key passphrase: ").encode("utf-8")
    if len(first) < 16: raise CustodyError("use a controller-held passphrase of at least 16 characters")
    if confirm and first != getpass.getpass("Repeat passphrase: ").encode("utf-8"):
        raise CustodyError("passphrases do not match")
    return first


def _write_new(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try: os.write(descriptor, data); os.fsync(descriptor)
    finally: os.close(descriptor)


def generate(controller_id: str, output_dir: Path, *, passphrase: bytes, supersedes: str | None = None) -> dict[str, Any]:
    if not CONTROLLER_RE.fullmatch(controller_id): raise CustodyError("controller ID is invalid")
    if supersedes is not None and not KEY_RE.fullmatch(supersedes): raise CustodyError("superseded key ID is invalid")
    private = Ed25519PrivateKey.generate(); public = public_text(private); identifier = key_id(public)
    private_path = output_dir / f"{identifier}.controller.ed25519.pem"
    public_path = output_dir / f"{identifier}.controller.public.json"
    encrypted = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(passphrase),
    )
    created = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    package = {
        "format": "mp-opt-controller-public-key-v1", "instance_id": None,
        "entity_id": controller_id, "key_id": identifier, "role": "controller",
        "algorithm": "Ed25519", "public_key": public,
        "public_key_sha256": hashlib.sha256(public.encode("ascii")).hexdigest(),
        "supersedes_key_id": supersedes, "created_at": created,
        "signature_namespace": NAMESPACE,
    }
    _write_new(private_path, encrypted)
    try: _write_new(public_path, canonical_json(package))
    except Exception:
        private_path.unlink(missing_ok=True); raise
    return {"private_key_path": str(private_path), "public_package_path": str(public_path), **package}


def load_private(path: Path, passphrase: bytes) -> Ed25519PrivateKey:
    if not path.is_file() or path.is_symlink(): raise CustodyError("controller private-key path is unsafe or missing")
    try: key = serialization.load_pem_private_key(path.read_bytes(), password=passphrase)
    except (ValueError, TypeError) as exc: raise CustodyError("controller private key or passphrase is invalid") from exc
    if not isinstance(key, Ed25519PrivateKey): raise CustodyError("controller key must use Ed25519")
    return key


def validate_controller_document(document: dict[str, Any], private: Ed25519PrivateKey) -> None:
    role = document.get("role"); entity = document.get("entity_id")
    if role != "controller" or not isinstance(entity, str) or not CONTROLLER_RE.fullmatch(entity):
        raise CustodyError("the document is not a controller action")
    public = public_text(private)
    if document.get("key_id") != key_id(public) or document.get("public_key_sha256") != hashlib.sha256(public.encode("ascii")).hexdigest():
        raise CustodyError("the document targets another controller key")
    if document.get("format") == "mp-opt-controller-trust-declaration-v1":
        if document.get("statement_type") != "initial_trust_declaration" or not SHA_RE.fullmatch(str(document.get("statement_sha256", ""))):
            raise CustodyError("controller trust declaration is invalid")
    elif document.get("format") == "mp-opt-trust-key-registration-v1":
        if document.get("action") not in {"register", "rotate"}:
            raise CustodyError("controller registration action is invalid")
    else:
        raise CustodyError("unsupported controller document format")
    canonical_json(document)


def sign(path: Path, document: dict[str, Any], *, passphrase: bytes) -> dict[str, Any]:
    private = load_private(path, passphrase); validate_controller_document(document, private)
    signature = private.sign(NAMESPACE.encode("ascii") + b"\0" + canonical_json(document))
    return {
        "document": document,
        "proof": {"format": "mp-opt-ed25519-signature-v1", "key_id": key_id(public_text(private)), "namespace": NAMESPACE, "signature": base64.b64encode(signature).decode("ascii")},
    }


def verify_recovery(path: Path, public_package: dict[str, Any], *, passphrase: bytes) -> dict[str, Any]:
    private = load_private(path, passphrase); public = public_text(private)
    if public_package.get("role") != "controller" or public_package.get("public_key") != public or public_package.get("key_id") != key_id(public):
        raise CustodyError("recovery key does not match the controller public package")
    return {"verified": True, "entity_id": public_package["entity_id"], "key_id": key_id(public), "public_key_sha256": hashlib.sha256(public.encode("ascii")).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run only on a controller-controlled trusted workstation")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("generate"); create.add_argument("--controller-id", required=True); create.add_argument("--output-dir", type=Path, required=True); create.add_argument("--supersedes-key-id")
    signer = commands.add_parser("sign"); signer.add_argument("--private-key", type=Path, required=True); signer.add_argument("--document", type=Path, required=True); signer.add_argument("--output", type=Path, required=True)
    recovery = commands.add_parser("verify-recovery"); recovery.add_argument("--private-key", type=Path, required=True); recovery.add_argument("--public-package", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "generate": result = generate(args.controller_id, args.output_dir, passphrase=_passphrase(True), supersedes=args.supersedes_key_id)
    elif args.command == "sign":
        document = json.loads(args.document.read_text(encoding="utf-8")); result = sign(args.private_key, document, passphrase=_passphrase()); _write_new(args.output, canonical_json(result))
    else:
        package = json.loads(args.public_package.read_text(encoding="utf-8")); result = verify_recovery(args.private_key, package, passphrase=_passphrase())
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (CustodyError, OSError, json.JSONDecodeError) as exc: print(f"Blocked: {exc}", file=__import__("sys").stderr); raise SystemExit(2)
