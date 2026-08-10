from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = ROOT / "deploy/management/portable_snapshot.py"
SPEC = importlib.util.spec_from_file_location("portable_snapshot", TOOL_PATH)
assert SPEC and SPEC.loader
portable_snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(portable_snapshot)


class PortableSnapshotTests(unittest.TestCase):
    RECIPIENT = "age1" + "a" * 58

    def create_snapshot(self, root: Path, receipt_format: str = "mp-opt-snapshot-receipt-v2") -> Path:
        snapshot = root / "20260718T120000Z_full_portable-test"
        snapshot.mkdir(mode=0o700)
        archive = snapshot / "snapshot.tar.age"
        archive.write_bytes(b"age-encrypted-ciphertext-for-test")
        archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        (snapshot / "archive.sha256").write_text(
            f"{archive_hash}  snapshot.tar.age\n", encoding="ascii"
        )
        fingerprint = hashlib.sha256(self.RECIPIENT.encode("ascii")).hexdigest()
        receipt = {
            "format": receipt_format,
            "type": "full",
            "name": "portable-test",
            "created_at": "2026-07-18T12:00:00Z",
            "archive_sha256": archive_hash,
            "archive_size": archive.stat().st_size,
            "verification": "deep-verified",
            "recovery_status": "recoverable",
            "encryption": {
                "scheme": "age-x25519",
                "recipient": self.RECIPIENT,
                "recipient_sha256": fingerprint,
                "recovery_key_id": f"rk-{fingerprint[:16]}",
            },
            "storage": {"local": "deep-verified", "off_server": "not-copied"},
        }
        (snapshot / "receipt.json").write_text(
            json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8"
        )
        for path in snapshot.iterdir():
            path.chmod(0o600)
        return snapshot

    def test_one_file_export_import_round_trip_is_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.create_snapshot(root)
            package = root / "recovery.mpopt-snapshot"
            exported = portable_snapshot.create_package(source, package, "node-a")
            self.assertEqual(exported["status"], "exported")
            self.assertEqual(exported["sha256"], portable_snapshot.sha256_path(package))
            self.assertEqual(package.stat().st_mode & 0o777, 0o600)
            with tarfile.open(package, "r:") as archive:
                self.assertEqual(set(archive.getnames()), set(portable_snapshot.PACKAGE_MEMBERS))
                self.assertNotIn(b"AGE-SECRET-KEY", package.read_bytes())

            destination = root / "imported"
            result = portable_snapshot.import_package(package, destination, exported["sha256"])
            self.assertEqual(result["status"], "imported")
            imported = destination / source.name
            for filename in portable_snapshot.SNAPSHOT_FILES:
                self.assertEqual((source / filename).read_bytes(), (imported / filename).read_bytes())

            repeated = portable_snapshot.import_package(package, destination, exported["sha256"])
            self.assertEqual(repeated["status"], "already-present")

    def test_v1_receipt_is_rejected_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.create_snapshot(root, "mp-opt-snapshot-receipt-v1")
            package = root / "legacy.mpopt-snapshot"
            with self.assertRaisesRegex(portable_snapshot.PackageError, "only .*v2"):
                portable_snapshot.create_package(source, package)
            self.assertFalse(package.exists())

    def test_v2_receipt_wrapping_a_legacy_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.create_snapshot(root)
            receipt_path = source / "receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["source_manifest_format"] = "mp-opt-snapshot-v1"
            receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
            receipt_path.chmod(0o600)
            with self.assertRaisesRegex(portable_snapshot.PackageError, "unsupported encrypted manifest"):
                portable_snapshot.create_package(source, root / "legacy.mpopt-snapshot")

    def test_wrong_transport_hash_and_conflicting_name_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.create_snapshot(root)
            package = root / "recovery.mpopt-snapshot"
            portable_snapshot.create_package(source, package)
            destination = root / "imported"
            with self.assertRaisesRegex(portable_snapshot.PackageError, "does not match"):
                portable_snapshot.import_package(package, destination, "0" * 64)
            self.assertFalse(destination.exists())

            portable_snapshot.import_package(package, destination)
            receipt = destination / source.name / "receipt.json"
            receipt.write_bytes(receipt.read_bytes() + b" ")
            with self.assertRaisesRegex(portable_snapshot.PackageError, "different snapshot"):
                portable_snapshot.import_package(package, destination)

    def test_encrypted_transport_file_does_not_require_posix_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.create_snapshot(root)
            package = root / "recovery.mpopt-snapshot"
            exported = portable_snapshot.create_package(source, package)
            package.chmod(0o644)

            inspected = portable_snapshot.validate_package(package)

            self.assertEqual(inspected["sha256"], exported["sha256"])
            self.assertEqual(inspected["document"]["snapshot_directory"], source.name)

    def test_symbolic_link_transport_file_is_rejected(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.create_snapshot(root)
            package = root / "recovery.mpopt-snapshot"
            portable_snapshot.create_package(source, package)
            linked = root / "linked.mpopt-snapshot"
            try:
                linked.symlink_to(package)
            except OSError:
                self.skipTest("symbolic links are not permitted in this environment")

            with self.assertRaisesRegex(portable_snapshot.PackageError, "regular file"):
                portable_snapshot.validate_package(linked)

    def test_unsafe_archive_member_is_rejected_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "unsafe.mpopt-snapshot"
            with tarfile.open(package, "w") as archive:
                data = b"escape"
                member = tarfile.TarInfo("../escape")
                member.size = len(data)
                member.mode = 0o600
                archive.addfile(member, io.BytesIO(data))
            package.chmod(0o600)
            with self.assertRaisesRegex(portable_snapshot.PackageError, "unsafe member path"):
                portable_snapshot.validate_package(package)
            self.assertFalse((root / "escape").exists())

    def test_changed_tar_framing_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.create_snapshot(root)
            package = root / "recovery.mpopt-snapshot"
            portable_snapshot.create_package(source, package)
            original = package.read_bytes()

            package.write_bytes(original[:-1])
            with self.assertRaisesRegex(portable_snapshot.PackageError, "non-canonical TAR length"):
                portable_snapshot.validate_package(package)

            package.write_bytes(original + (b"\0" * tarfile.RECORDSIZE))
            with self.assertRaisesRegex(portable_snapshot.PackageError, "non-canonical TAR length"):
                portable_snapshot.validate_package(package)

            package.write_bytes(original[:-1] + b"\1")
            with self.assertRaisesRegex(portable_snapshot.PackageError, "non-zero trailing TAR padding"):
                portable_snapshot.validate_package(package)

    def test_symbolic_link_snapshot_member_is_rejected(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.create_snapshot(root)
            receipt = source / "receipt.json"
            receipt.unlink()
            try:
                receipt.symlink_to(source / "archive.sha256")
            except OSError:
                self.skipTest("symbolic links are not permitted in this environment")
            with self.assertRaisesRegex(portable_snapshot.PackageError, "regular file"):
                portable_snapshot.create_package(source, root / "bad.mpopt-snapshot")


if __name__ == "__main__":
    unittest.main()
