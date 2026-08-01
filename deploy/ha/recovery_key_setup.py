#!/usr/bin/env python3
"""Create and inspect an off-server age identity for snapshot recovery.

The private identity is deliberately never printed or copied into the
repository.  Only its public recipient and a reproducible SHA-256 fingerprint
are written to the companion metadata file.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys


FORMAT = "mp-opt-recovery-key-v1"
RECIPIENT = re.compile(r"^age1[0-9a-z]+$")
ROOT = Path(__file__).resolve().parents[2]


def fingerprint(recipient: str) -> str:
    return hashlib.sha256(recipient.encode("ascii")).hexdigest()


def resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def require_off_repository(path: Path) -> None:
    if is_within(path, ROOT):
        raise ValueError(
            "The private recovery identity must be created outside the repository. "
            "Choose a protected password-manager or offline-backup location."
        )


def require_new_regular_path(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"Refusing to overwrite existing path: {path}")
    if not path.parent.is_dir():
        raise ValueError(f"The destination directory does not exist: {path.parent}")


def locate_age_keygen(value: str | None) -> str:
    candidate = value or shutil.which("age-keygen")
    if not candidate:
        raise ValueError(
            "age-keygen is not available on PATH. Install the official age tools "
            "on this workstation, then rerun this command."
        )
    return candidate


def public_recipient(age_keygen: str, identity: Path) -> str:
    process = subprocess.run(
        [age_keygen, "-y", str(identity)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    recipient = process.stdout.strip()
    if not RECIPIENT.fullmatch(recipient):
        raise ValueError("age-keygen returned an invalid public recipient")
    return recipient


def identity_is_private(identity: Path) -> None:
    if not identity.is_file() or identity.is_symlink():
        raise ValueError("The recovery identity must be a regular, non-symbolic-link file")
    contents = identity.read_text(encoding="utf-8")
    if "AGE-SECRET-KEY-1" not in contents:
        raise ValueError("The generated file does not contain an age private identity")


def harden_identity(identity: Path) -> None:
    if os.name != "nt":
        os.chmod(identity, stat.S_IRUSR | stat.S_IWUSR)
        return
    username = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    if not username:
        raise ValueError("The Windows account name is unavailable; private-file ACL was not changed")
    account = f"{domain}\\{username}" if domain else username
    subprocess.run(
        ["icacls", str(identity), "/inheritance:r", "/grant:r", f"{account}:(F)"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def write_metadata(path: Path, recipient: str, identity_name: str) -> None:
    document = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "format": FORMAT,
        "identity_filename": identity_name,
        "recipient": recipient,
        "recipient_sha256": fingerprint(recipient),
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            json.dump(document, target, indent=2, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def print_summary(identity: Path, metadata: Path, recipient: str) -> None:
    print("MP-OPT snapshot recovery identity created and verified.")
    print(f"Private identity: {identity}")
    print(f"Public metadata:  {metadata}")
    print(f"Public recipient: {recipient}")
    print(f"Recipient SHA-256: {fingerprint(recipient)}")
    print()
    print("Required next steps:")
    print("1. Import the private identity into a protected password manager.")
    print("2. Make a second encrypted/offline backup and verify it can be read.")
    print("3. Configure only the public age1... recipient on the active MP-OPT node.")
    print("4. Never copy the AGE-SECRET-KEY value to either VPS, Git, email, or chat.")


def generate(args: argparse.Namespace) -> int:
    identity = resolved(Path(args.output))
    metadata = resolved(Path(args.metadata)) if args.metadata else Path(str(identity) + ".recipient.json")
    require_off_repository(identity)
    require_off_repository(metadata)
    if identity == metadata:
        raise ValueError("The private identity and public metadata paths must be different")
    require_new_regular_path(identity)
    require_new_regular_path(metadata)
    age_keygen = locate_age_keygen(args.age_keygen)
    try:
        subprocess.run(
            [age_keygen, "-o", str(identity)],
            check=True,
            stdin=subprocess.DEVNULL,
        )
        harden_identity(identity)
        identity_is_private(identity)
        recipient = public_recipient(age_keygen, identity)
        write_metadata(metadata, recipient, identity.name)
    except BaseException:
        identity.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)
        raise
    print_summary(identity, metadata, recipient)
    return 0


def inspect(args: argparse.Namespace) -> int:
    identity = resolved(Path(args.identity))
    require_off_repository(identity)
    identity_is_private(identity)
    recipient = public_recipient(locate_age_keygen(args.age_keygen), identity)
    print(f"Public recipient: {recipient}")
    print(f"Recipient SHA-256: {fingerprint(recipient)}")
    return 0


def verify(args: argparse.Namespace) -> int:
    identity = resolved(Path(args.identity))
    require_off_repository(identity)
    identity_is_private(identity)
    expected = args.recipient.strip()
    if not RECIPIENT.fullmatch(expected):
        raise ValueError("The expected public recipient is invalid")
    actual = public_recipient(locate_age_keygen(args.age_keygen), identity)
    print(f"Identity recipient SHA-256: {fingerprint(actual)}")
    print(f"Expected recipient SHA-256: {fingerprint(expected)}")
    if actual != expected:
        print("Result: MISMATCH", file=sys.stderr)
        return 1
    print("Result: MATCH")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Create or verify an off-server MP-OPT snapshot recovery identity."
    )
    commands = result.add_subparsers(dest="command", required=True)
    create = commands.add_parser("generate", help="create a new private identity and public metadata")
    create.add_argument("--output", required=True, help="new private identity path outside the repository")
    create.add_argument("--metadata", help="optional new public metadata path")
    create.add_argument("--age-keygen", help=argparse.SUPPRESS)
    create.set_defaults(function=generate)
    show = commands.add_parser("inspect", help="derive only the public recipient and fingerprint")
    show.add_argument("--identity", required=True)
    show.add_argument("--age-keygen", help=argparse.SUPPRESS)
    show.set_defaults(function=inspect)
    check = commands.add_parser("verify", help="prove an identity belongs to a public recipient")
    check.add_argument("--identity", required=True)
    check.add_argument("--recipient", required=True)
    check.add_argument("--age-keygen", help=argparse.SUPPRESS)
    check.set_defaults(function=verify)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        return int(args.function(args))
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
