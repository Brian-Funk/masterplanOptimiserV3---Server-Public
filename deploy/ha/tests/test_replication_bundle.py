from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest


HA_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HA_DIR))

import replication_bundle  # noqa: E402


class ReplicationBundleTests(unittest.TestCase):
    def test_directly_invoked_ha_scripts_are_executable(self) -> None:
        for name in (
            "promote_local.sh",
            "receive_replication_bundle.sh",
            "replicate_now.sh",
        ):
            path = HA_DIR / name
            self.assertTrue(
                path.stat().st_mode & stat.S_IXUSR,
                f"{path} must be committed with its owner execute bit set",
            )

    def payload(self, root: Path) -> Path:
        payload = root / "payload"
        (payload / "database").mkdir(parents=True, exist_ok=True)
        (payload / "config" / "secrets").mkdir(parents=True, exist_ok=True)
        (payload / "recovery").mkdir(parents=True, exist_ok=True)
        (payload / "evidence" / "ledger").mkdir(parents=True, exist_ok=True)
        (payload / "evidence" / "public").mkdir(parents=True, exist_ok=True)
        for relative, value in (
            ("database/masterplan.dump", b"database"),
            ("config/shared.env", b"DOMAIN=example.test\n"),
            ("config/secrets/secret_key", b"shared-secret"),
            ("config/secrets/ip_hmac_key", b"shared-ip-hmac-secret"),
            ("config/secrets/vapid_private_key", b"vapid-secret"),
            ("config/secrets/root_bootstrap_token", b""),
            ("config/secrets/smtp_token", b""),
            ("config/secrets/evidence_signing_key", b"evidence-private-key"),
            ("evidence/ledger/chain-head.json", b'{"sequence":1}\n'),
            ("evidence/public/instance_signing_key.pub", b"ssh-ed25519 test\n"),
            (
                "recovery/manual-recovery-export.json",
                json.dumps({
                    "format": "mp-opt-manual-recovery-export-v1",
                    "state": "fresh-export-required",
                    "reason": "test-baseline",
                    "required_at": "2026-07-19T00:00:00+00:00",
                    "previous_confirmed": {},
                }).encode("utf-8"),
            ),
        ):
            path = payload / relative
            path.write_bytes(value)
            path.chmod(0o600)
        return payload

    def create_manifest(self, root: Path) -> tuple[Path, Path]:
        payload = self.payload(root)
        manifest = root / "manifest.json"
        replication_bundle.create(argparse.Namespace(
            payload=str(payload), cluster="cluster-test", source="node-a",
            target="node-b", bundle="bundle-1", generation=1,
            release="a" * 40, output=str(manifest),
        ))
        return payload, manifest

    def validate_args(self, root: Path) -> argparse.Namespace:
        return argparse.Namespace(
            extracted=str(root), cluster="cluster-test", source="node-a",
            target="node-b", release="a" * 40,
        )

    def test_manifest_validates_complete_mode_0600_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_manifest(root)
            replication_bundle.validate(self.validate_args(root))
            document = json.loads((root / "manifest.json").read_text())
            self.assertTrue(all(item["mode"] == "0600" for item in document["files"]))

    def test_tamper_and_unsafe_mode_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload, manifest = self.create_manifest(root)
            (payload / "database/masterplan.dump").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                replication_bundle.validate(self.validate_args(root))
            self.create_manifest(root)
            document = json.loads(manifest.read_text())
            document["files"][0]["mode"] = "0644"
            manifest.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "mode is unsafe"):
                replication_bundle.validate(self.validate_args(root))
            self.create_manifest(root)
            (payload / "config/shared.env").chmod(0o644)
            with self.assertRaisesRegex(ValueError, "mode is unsafe"):
                replication_bundle.validate(self.validate_args(root))

    def test_environment_merge_keeps_only_local_database_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "local.env"
            shared = root / "shared.env"
            output = root / "merged.env"
            local.write_text("DATABASE_URL=local\nPOSTGRES_PASSWORD=local-password\nHA_NODE_ID=node-b\nDOMAIN=old\n")
            shared.write_text("DOMAIN=new\nSMTP_HOST=mail.example\n")
            replication_bundle.merge_env(argparse.Namespace(
                local=str(local), shared=str(shared), output=str(output),
            ))
            self.assertEqual(
                output.read_text(),
                "DATABASE_URL=local\nPOSTGRES_PASSWORD=local-password\nHA_NODE_ID=node-b\nDOMAIN=new\nSMTP_HOST=mail.example\n",
            )
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_shared_environment_excludes_every_node_local_ha_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.env"
            output = root / "shared.env"
            source.write_text(
                "DOMAIN=example.test\nHA_NODE_ID=node-a\nHA_NODE_TOKEN=never-copy\n"
                "DATABASE_URL=local\nPOSTGRES_PASSWORD=local-password\n"
            )
            replication_bundle.filter_env(argparse.Namespace(source=str(source), output=str(output)))
            self.assertEqual(output.read_text(), "DOMAIN=example.test\n")

    def test_recovery_state_is_public_schema_bound_and_sanitised(self) -> None:
        confirmed = {
            "format": "mp-opt-manual-recovery-export-v1",
            "state": "operator-sha256-confirmed",
            "snapshot": "20260719T001950Z_full_post-portable-rebuild",
            "confirmed_at": "2026-07-19T00:20:31Z",
            "package_format": "mp-opt-portable-snapshot-2026-01",
            "package_sha256": "1" * 64,
            "package_size": 143360,
            "archive_sha256": "2" * 64,
            "recovery_key_id": "rk-" + "3" * 16,
        }
        self.assertEqual(
            replication_bundle.normalise_recovery_state(confirmed),
            confirmed,
        )
        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            replication_bundle.normalise_recovery_state({
                **confirmed,
                "ADMIN_TOKEN": "must-never-replicate",
            })

    def test_missing_recovery_state_becomes_explicit_export_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "manual-recovery-export.json"
            replication_bundle.prepare_recovery_state(argparse.Namespace(
                source=str(root / "missing.json"), output=str(output),
            ))
            document = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(document["state"], "fresh-export-required")
            self.assertEqual(document["reason"], "no-confirmed-workstation-export")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_fresh_export_required_preserves_only_valid_previous_receipt(self) -> None:
        document = {
            "format": "mp-opt-manual-recovery-export-v1",
            "state": "fresh-export-required",
            "reason": "snapshot-restore",
            "required_at": "2026-07-19T00:21:00Z",
            "previous_confirmed": {
                "snapshot": "20260719T001950Z_full_post-portable-rebuild",
                "confirmed_at": "2026-07-19T00:20:31Z",
                "package_sha256": "1" * 64,
                "recovery_key_id": "rk-" + "3" * 16,
            },
        }
        self.assertEqual(
            replication_bundle.normalise_recovery_state(document),
            document,
        )
        document["previous_confirmed"]["node_token"] = "forbidden"
        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            replication_bundle.normalise_recovery_state(document)

    def test_archive_member_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            members = Path(directory) / "members.txt"
            members.write_text("manifest.json\npayload/../secrets/node_token\n")
            with self.assertRaisesRegex(ValueError, "Unsafe archive member"):
                replication_bundle.validate_members(argparse.Namespace(list=str(members)))

    def test_archive_symbolic_link_is_rejected_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "bundle.tar"
            with tarfile.open(archive_path, "w") as archive:
                link = tarfile.TarInfo("payload/config/secrets/secret_key")
                link.type = tarfile.SYMTYPE
                link.linkname = "/etc/passwd"
                archive.addfile(link)
            with self.assertRaisesRegex(ValueError, "links and special files"):
                replication_bundle.validate_archive(argparse.Namespace(archive=str(archive_path)))

    def test_receiver_refreshes_lease_namespace_after_replacing_secrets(self) -> None:
        source = (HA_DIR / "receive_replication_bundle.sh").read_text(encoding="utf-8")
        secret_install = source.index(
            'install -m 0600 "$source_file" "$MP_ROOT/secrets/$secret"'
        )
        receiver_receipt = source.index(
            'install -m 0600 "$stage/receiver.json" "$MP_ROOT/runtime/ha-receiver.json"'
        )
        namespace_refresh = source.index(
            "sudo -n systemctl restart mp-opt-ha-lease.service",
            receiver_receipt,
        )
        accepted = source.index("printf 'ACCEPTED:%s:%s", namespace_refresh)

        self.assertLess(secret_install, receiver_receipt)
        self.assertLess(receiver_receipt, namespace_refresh)
        self.assertLess(namespace_refresh, accepted)
        self.assertIn(
            "sudo -n systemctl restart mp-opt-ha-lease.service >/dev/null 2>&1 || true",
            source,
        )

    def test_receiver_rebuilds_evidence_public_key_after_rollback(self) -> None:
        source = (HA_DIR / "receive_replication_bundle.sh").read_text(encoding="utf-8")
        restore_private = source.index(
            'install -m 0600 "$stage/secrets.previous/$secret" "$MP_ROOT/secrets/$secret"'
        )
        remove_public = source.index(
            'rm -f "$MP_ROOT/secrets/evidence_signing_key.pub"', restore_private
        )
        regenerate_public = source.index(
            'ssh-keygen -y -f "$MP_ROOT/secrets/evidence_signing_key"', remove_public
        )

        self.assertLess(restore_private, remove_public)
        self.assertLess(remove_public, regenerate_public)

    def test_replication_carries_recovery_receipt_and_publishes_dashboard_state(self) -> None:
        sender = (HA_DIR / "replicate_now.sh").read_text(encoding="utf-8")
        receiver = (HA_DIR / "receive_replication_bundle.sh").read_text(encoding="utf-8")

        self.assertIn("prepare-recovery-state", sender)
        self.assertIn('"$MP_MANUAL_EXPORT_STATE"', sender)
        receipt_install = receiver.index(
            '"$stage/extracted/payload/recovery/manual-recovery-export.json"'
        )
        status_publish = receiver.index("mp_snapshot_publish_status", receipt_install)
        accepted = receiver.index("printf 'ACCEPTED:%s:%s", status_publish)
        self.assertLess(receipt_install, status_publish)
        self.assertLess(status_publish, accepted)
        self.assertIn(
            'install -m 0600 "$stage/manual-export.previous" "$MP_MANUAL_EXPORT_STATE"',
            receiver,
        )
        self.assertIn(
            'install -m 0644 "$stage/snapshot-status.previous" "$MP_HA_SNAPSHOT_STATUS"',
            receiver,
        )


if __name__ == "__main__":
    unittest.main()
