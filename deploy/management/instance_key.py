#!/usr/bin/env python3
"""Guard the one Server instance signing identity used by the evidence chain.

This utility never creates a key during verification. Commissioning is an
explicit, exactly-once action. Recovery installs only a private key that
matches the previously trusted public fingerprint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


INSTANCE_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class InstanceKeyError(RuntimeError):
    pass


def _run(*args: str, input_text: str | None = None) -> str:
    result = subprocess.run(args, input=input_text, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise InstanceKeyError("instance key operation failed")
    return result.stdout.strip()


def canonical_public(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise InstanceKeyError("instance signing key is missing or unsafe")
    return _run("ssh-keygen", "-y", "-f", str(path))


def fingerprint(public: str) -> str:
    return hashlib.sha256(public.encode("ascii")).hexdigest()


def _paths(secret_dir: Path) -> tuple[Path, Path, Path]:
    return (
        secret_dir / "evidence_signing_key",
        secret_dir / "evidence_signing_key.pub",
        secret_dir / "instance_signing_trust.json",
    )


def commission(secret_dir: Path, instance_id: str) -> dict[str, str]:
    """Atomically create the first and only initial instance key."""
    if not INSTANCE_RE.fullmatch(instance_id):
        raise InstanceKeyError("instance ID is invalid")
    secret_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    private, public_path, trust_path = _paths(secret_dir)
    if any(path.exists() for path in (private, public_path, trust_path)):
        raise InstanceKeyError("instance signing identity already exists; use recovery or guarded rotation")
    with tempfile.TemporaryDirectory(prefix=".instance-key-", dir=secret_dir) as raw_stage:
        stage = Path(raw_stage)
        staged_private = stage / "key"
        _run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "mp-opt-instance-evidence-v1", "-f", str(staged_private))
        generated_public = canonical_public(staged_private)
        document = {
            "format": "mp-opt-instance-trust-v1",
            "instance_id": instance_id,
            "role": "instance",
            "algorithm": "Ed25519",
            "public_key": generated_public,
            "public_key_sha256": fingerprint(generated_public),
            "status": "active",
        }
        staged_public = stage / "key.pub.canonical"
        staged_trust = stage / "trust.json"
        staged_public.write_text(generated_public + "\n", encoding="ascii", newline="\n")
        staged_trust.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
        os.chmod(staged_private, 0o600); os.chmod(staged_public, 0o600); os.chmod(staged_trust, 0o600)
        os.replace(staged_private, private); os.replace(staged_public, public_path); os.replace(staged_trust, trust_path)
    return document


def verify(secret_dir: Path, instance_id: str | None = None) -> dict[str, str]:
    """Fail closed when the key, public record, or expected identity differs."""
    private, public_path, trust_path = _paths(secret_dir)
    if not public_path.is_file() or not trust_path.is_file() or public_path.is_symlink() or trust_path.is_symlink():
        raise InstanceKeyError("instance signing trust is incomplete; never regenerate automatically")
    expected_public = public_path.read_text(encoding="ascii").strip()
    actual_public = canonical_public(private)
    try: trust = json.loads(trust_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc: raise InstanceKeyError("instance trust record is unreadable") from exc
    expected_fields = {"format", "instance_id", "role", "algorithm", "public_key", "public_key_sha256", "status"}
    if set(trust) != expected_fields or trust.get("format") != "mp-opt-instance-trust-v1" or trust.get("role") != "instance" or trust.get("algorithm") != "Ed25519":
        raise InstanceKeyError("instance trust record is invalid")
    if instance_id is not None and trust.get("instance_id") != instance_id:
        raise InstanceKeyError("instance signing key belongs to another instance")
    actual_fingerprint = fingerprint(actual_public)
    if expected_public != actual_public or trust.get("public_key") != actual_public or trust.get("public_key_sha256") != actual_fingerprint:
        raise InstanceKeyError("instance signing fingerprint mismatch; use guarded recovery or rotation")
    return trust


def compare(local_dir: Path, peer_dir: Path) -> dict[str, str | bool]:
    local = verify(local_dir); peer = verify(peer_dir, local["instance_id"])
    same = local["public_key_sha256"] == peer["public_key_sha256"]
    if not same: raise InstanceKeyError("HA nodes have competing instance signing identities")
    return {"consistent": True, "instance_id": local["instance_id"], "public_key_sha256": local["public_key_sha256"]}


def recover(secret_dir: Path, recovered_private: Path) -> dict[str, str]:
    """Install a recovery key only when it matches the retained trust record."""
    private, public_path, trust_path = _paths(secret_dir)
    if private.exists(): raise InstanceKeyError("recovery refuses to overwrite an existing instance key")
    if not public_path.is_file() or not trust_path.is_file(): raise InstanceKeyError("recovery requires the retained public trust record")
    recovered_public = canonical_public(recovered_private)
    expected_public = public_path.read_text(encoding="ascii").strip()
    if recovered_public != expected_public: raise InstanceKeyError("recovery key fingerprint does not match the trusted instance identity")
    staged = secret_dir / ".evidence_signing_key.recovery"
    data = recovered_private.read_bytes()
    descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, data); os.fsync(descriptor)
    finally: os.close(descriptor)
    os.replace(staged, private)
    return verify(secret_dir)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("commission", "verify"):
        command = commands.add_parser(name); command.add_argument("--secret-dir", type=Path, required=True); command.add_argument("--instance-id")
    compare_command = commands.add_parser("compare-ha"); compare_command.add_argument("--local-secret-dir", type=Path, required=True); compare_command.add_argument("--peer-secret-dir", type=Path, required=True)
    recovery = commands.add_parser("recover"); recovery.add_argument("--secret-dir", type=Path, required=True); recovery.add_argument("--recovered-private-key", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "commission": result = commission(args.secret_dir, args.instance_id or "")
    elif args.command == "verify": result = verify(args.secret_dir, args.instance_id)
    elif args.command == "compare-ha": result = compare(args.local_secret_dir, args.peer_secret_dir)
    else: result = recover(args.secret_dir, args.recovered_private_key)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except InstanceKeyError as exc: print(f"Blocked: {exc}", file=__import__("sys").stderr); raise SystemExit(2)
