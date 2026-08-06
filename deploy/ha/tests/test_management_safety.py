from __future__ import annotations

from pathlib import Path
import importlib.util
import json
import os
import pty
import select
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
HA_SOURCE = (ROOT / "deploy/management/ha.sh").read_text(encoding="utf-8")
MENU_SOURCE = (ROOT / "manage.sh").read_text(encoding="utf-8")
COMMON_SOURCE = (ROOT / "deploy/management/common.sh").read_text(encoding="utf-8")
SNAPSHOT_SOURCE = (ROOT / "deploy/management/snapshots.sh").read_text(encoding="utf-8")
PORTABLE_SOURCE = (ROOT / "deploy/management/portable_snapshots.sh").read_text(encoding="utf-8")
ACTIONS_SOURCE = (ROOT / "deploy/management/actions.sh").read_text(encoding="utf-8")
ROTATION_SOURCE = (ROOT / "deploy/management/recovery_rotation.sh").read_text(encoding="utf-8")
INSTALL_SERVICES_SOURCE = (ROOT / "deploy/ha/install_services.sh").read_text(encoding="utf-8")
AUTOMATIC_SNAPSHOTS_SOURCE = (ROOT / "deploy/ha/automatic_snapshots.sh").read_text(encoding="utf-8")
RECOVERY_KEY_PATH = ROOT / "deploy/ha/recovery_key_setup.py"
PORTABLE_TOOL_PATH = ROOT / "deploy/management/portable_snapshot.py"
SPEC = importlib.util.spec_from_file_location("recovery_key_setup", RECOVERY_KEY_PATH)
assert SPEC and SPEC.loader
recovery_key_setup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recovery_key_setup
SPEC.loader.exec_module(recovery_key_setup)


def function_body(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    remainder = source[start:]
    next_function = remainder.find("\nmp_", len(name) + 5)
    return remainder if next_function < 0 else remainder[:next_function]


class CaddyConversionSafetyTests(unittest.TestCase):
    def test_conversion_is_prebootstrap_hash_backed_and_backend_preserving(self) -> None:
        body = function_body(HA_SOURCE, "mp_ha_convert_host_caddy")
        self.assertIn(".routing_ready == true", body)
        self.assertLess(body.index("mp_ha_validate_container_caddy"), body.index("systemctl disable --now caddy"))
        self.assertLess(body.index("mp_ha_host_caddy_backup"), body.index("systemctl disable --now caddy"))
        self.assertIn('up -d --no-deps caddy', body)
        self.assertNotIn("force-recreate backend", body)
        self.assertIn("mp_ha_restore_host_caddy_backup", body)

    def test_rollback_verifies_receipt_and_refuses_routing_ready_cluster(self) -> None:
        rollback = function_body(HA_SOURCE, "mp_ha_rollback_host_caddy")
        restore = function_body(HA_SOURCE, "mp_ha_restore_host_caddy_backup")
        self.assertIn(".routing_ready == true", rollback)
        self.assertLess(restore.index("sha256sum -c receipt.sha256"), restore.index("install -m 0600"))
        self.assertIn("caddy validate", restore)
        self.assertIn("/health", restore)

    def test_management_menu_routes_conversion_through_commissioning(self) -> None:
        # Host-Caddy conversion and rollback remain guarded implementation
        # primitives, but are no longer exposed as disconnected operator
        # actions. The resumable commissioning workflow invokes conversion at
        # the correct pre-routing checkpoint and owns rollback handling.
        self.assertNotIn('"caddy-convert" "Convert host Caddy', MENU_SOURCE)
        self.assertNotIn('"caddy-rollback" "Restore the verified', MENU_SOURCE)
        self.assertIn('"setup" "Commission, migrate, replace or recover a server"', MENU_SOURCE)
        setup_source = (ROOT / "deploy/management/setup_v2.sh").read_text(encoding="utf-8")
        self.assertIn("mp_ha_convert_host_caddy", setup_source)
        self.assertIn("mp_ha_restore_host_caddy_backup", HA_SOURCE)
        self.assertIn("mp_unlock()", COMMON_SOURCE)


class TerminalExitSafetyTests(unittest.TestCase):
    def test_every_management_exit_clears_the_operator_terminal(self) -> None:
        cleanup = function_body(MENU_SOURCE, "mp_cleanup")
        self.assertIn("clear </dev/tty >/dev/tty", cleanup)
        self.assertIn("trap mp_cleanup EXIT TERM", MENU_SOURCE)


class SnapshotServiceSafetyTests(unittest.TestCase):
    def test_deep_verify_retries_pending_deletion_recovery_receipts(self) -> None:
        verify = function_body(SNAPSHOT_SOURCE, "mp_snapshot_verify_interactive")
        export = function_body(PORTABLE_SOURCE, "mp_snapshot_export_portable_interactive")
        self.assertIn('mp_compliance_emit_backup_receipts "$selected"', verify)
        self.assertIn("Pending deletion recovery receipts were recorded", verify)
        self.assertIn("Export this verified snapshot", verify)
        self.assertIn('mp_compliance_emit_backup_receipts "$selected"', export)
        self.assertNotIn('mp_compliance_emit_backup_receipts "$selected" || true', export)
        self.assertIn("Deep-verify this snapshot now", export)

    def test_portable_exports_are_durably_inventoried_for_deletion_cases(self) -> None:
        record = function_body(PORTABLE_SOURCE, "mp_portable_record_confirmed_export")
        bridge = function_body(PORTABLE_SOURCE, "mp_compliance_emit_backup_receipts")
        self.assertIn("MP_PORTABLE_EXPORT_INVENTORY", PORTABLE_SOURCE)
        self.assertIn('"$MP_PORTABLE_EXPORT_INVENTORY/${package_id}.json"', record)
        self.assertIn('mp-opt-portable-export-inventory-v1', record)
        self.assertIn('--portable-inventory "$MP_PORTABLE_EXPORT_INVENTORY"', bridge)

    def test_backend_secret_contract_is_group_readable_without_broadening_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secrets = root / "secrets"
            secrets.mkdir(mode=0o700)
            names = (
                "database_password", "ip_hmac_key", "secret_key",
                "vapid_private_key", "root_bootstrap_token", "smtp_token",
                "evidence_signing_key", "evidence_github_fine_grained_token",
            )
            for name in names:
                path = secrets / name
                path.write_text("synthetic", encoding="utf-8")
                path.chmod(0o600)
            log = root / "sudo.log"
            command = f'''
                set -Eeuo pipefail
                export MP_ROOT={shlex.quote(str(root))}
                export TEST_SUDO_LOG={shlex.quote(str(log))}
                source {shlex.quote(str(ROOT / "deploy/management/common.sh"))}
                sudo() {{
                    [ "$1" != -n ] || shift
                    printf '%s\n' "$*" >> "$TEST_SUDO_LOG"
                }}
                mp_prepare_backend_secret_permissions
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", command],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(stat.S_IMODE(secrets.stat().st_mode), 0o700)
            self.assertTrue(all(
                stat.S_IMODE((secrets / name).stat().st_mode) == 0o640
                for name in names
            ))
            chowns = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(chowns), len(names))
            self.assertTrue(all(
                line.startswith("chown :10001 -- ")
                for line in chowns
            ))

    def test_installation_validation_uses_the_backend_secret_permission_contract(self) -> None:
        validation = function_body(ACTIONS_SOURCE, "mp_validate_installation")
        expected_mode = function_body(COMMON_SOURCE, "mp_expected_protected_file_mode")
        mode_validation = function_body(COMMON_SOURCE, "mp_validate_protected_file_modes")

        self.assertIn("mp_validate_protected_file_modes || failed=1", validation)
        self.assertNotIn('[ "$mode" != "600" ]', validation)
        for name in (
            "database_password", "ip_hmac_key", "secret_key",
            "vapid_private_key", "root_bootstrap_token", "smtp_token",
            "evidence_signing_key", "evidence_github_fine_grained_token",
        ):
            self.assertIn(f'"$MP_ROOT/secrets/{name}"', expected_mode)
        self.assertIn("printf '640\\n'", expected_mode)
        self.assertIn("printf '600\\n'", expected_mode)
        self.assertIn('expected="$(mp_expected_protected_file_mode "$file")"', mode_validation)

    def test_snapshot_payload_permissions_do_not_depend_on_operator_umask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.mkdir(mode=0o755)
            command = f'''
                set -Eeuo pipefail
                umask 002
                export MP_ROOT={shlex.quote(str(ROOT))}
                source "$MP_ROOT/deploy/management/snapshots.sh"
                mp_compose_init() {{ MP_COMPOSE=(fake_compose); }}
                fake_compose() {{
                    case "$*" in
                        "up -d db") return 0 ;;
                        "exec -T db pg_dump -U masterplan -d masterplan -Fc") printf dump ;;
                        "exec -T db pg_restore --list") cat >/dev/null ;;
                        *) printf 'Unexpected Compose call: %s\n' "$*" >&2; return 1 ;;
                    esac
                }}
                mp_snapshot_dump_database {shlex.quote(str(payload))}
                test "$(stat -c '%a' {shlex.quote(str(payload / 'database'))})" = 700
                test "$(stat -c '%a' {shlex.quote(str(payload / 'database/masterplan.dump'))})" = 600
                printf marker > {shlex.quote(str(payload / 'broad-marker'))}
                chmod 664 {shlex.quote(str(payload / 'broad-marker'))}
                mp_snapshot_normalise_payload_permissions {shlex.quote(str(payload))}
                test "$(stat -c '%a' {shlex.quote(str(payload))})" = 700
                test "$(stat -c '%a' {shlex.quote(str(payload / 'broad-marker'))})" = 600
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", command],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_scheduled_snapshot_lock_contention_is_a_clean_retryable_skip(self) -> None:
        lock = AUTOMATIC_SNAPSHOTS_SOURCE.index('exec 9>"$MP_LOCK_FILE"')
        create = AUTOMATIC_SNAPSHOTS_SOURCE.index("create_snapshot database")
        self.assertLess(lock, create)
        self.assertIn("if ! flock -n 9", AUTOMATIC_SNAPSHOTS_SOURCE)
        self.assertIn("Automatic snapshot skipped", AUTOMATIC_SNAPSHOTS_SOURCE)
        self.assertIn("export MP_MANAGEMENT_LOCK_HELD=1", AUTOMATIC_SNAPSHOTS_SOURCE)
        self.assertIn("trap 'mp_unlock' EXIT", AUTOMATIC_SNAPSHOTS_SOURCE)

    def test_installer_precreates_every_hardened_snapshot_write_path(self) -> None:
        for path in (
            '$HOME/.config/mp-opt-server',
            '$HOME/.local/state/mp-opt-server',
            '$HOME/masterplan-snapshots',
        ):
            self.assertIn(path, INSTALL_SERVICES_SOURCE)
        self.assertLess(
            INSTALL_SERVICES_SOURCE.index('$HOME/masterplan-snapshots'),
            INSTALL_SERVICES_SOURCE.index('units=('),
        )

    def test_snapshot_service_can_publish_only_to_the_shared_runtime_directory(self) -> None:
        service = (ROOT / "deploy/ha/mp-opt-ha-snapshots.service").read_text(encoding="utf-8")
        self.assertIn("-/opt/masterplan/runtime", service)
        self.assertIn("mp-opt-ha-snapshot-status-v1", SNAPSHOT_SOURCE)
        self.assertNotIn("AGE-SECRET-KEY", function_body(SNAPSHOT_SOURCE, "mp_snapshot_publish_status"))


class RecoveryKeyWorkflowTests(unittest.TestCase):
    RECIPIENT = "age1" + "a" * 58

    def test_holder_guard_loads_node_identity_in_the_current_shell(self) -> None:
        maintenance = function_body(COMMON_SOURCE, "mp_require_ha_maintenance_window")
        self.assertIn(".automatic_failover'", maintenance)
        self.assertNotIn("automatic_failover // true", maintenance)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "node.env"
            runtime = root / "runtime"
            runtime.mkdir()
            config.write_text(
                "HA_MODE=ha\n"
                "HA_ROLE=dynamic\n"
                "HA_NODE_ID=node-a\n"
                "HA_CLUSTER_ID=cluster-test\n",
                encoding="utf-8",
            )
            (runtime / "ha-control.json").write_text(json.dumps({
                "holder_node_id": "node-a",
                "generation": 7,
                "automatic_failover": False,
            }), encoding="utf-8")
            command = f'''
                set -Eeuo pipefail
                export MP_ROOT={shlex.quote(str(root))}
                export MP_HA_CONFIG={shlex.quote(str(config))}
                source {shlex.quote(str(ROOT / "deploy/management/common.sh"))}
                unset HA_MODE HA_ROLE HA_NODE_ID HA_CLUSTER_ID
                python3() {{ printf '%s\n' '{{"allowed":true,"holder_node_id":"node-a","generation":7}}'; }}
                jq() {{
                    case "$*" in
                        *holder_node_id*//*) printf '%s\n' node-a ;;
                        *generation*//*) printf '%s\n' 7 ;;
                        *automatic_failover*) printf '%s\n' false ;;
                        *) cat >/dev/null ;;
                    esac
                }}
                ui_error() {{ printf '%s\n' "$1" >&2; }}
                mp_require_active_or_standalone
                test "$HA_ROLE" = dynamic
                test "$HA_NODE_ID" = node-a
                mp_require_ha_maintenance_window
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", command],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def fake_age_keygen(self, directory: Path) -> Path:
        executable = directory / "age-keygen"
        executable.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-o\" ]; then\n"
            "  printf '%s\\n' 'AGE-SECRET-KEY-1TESTPRIVATEVALUE' > \"$2\"\n"
            "elif [ \"$1\" = \"-y\" ]; then\n"
            f"  printf '%s\\n' '{self.RECIPIENT}'\n"
            "else exit 2; fi\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        return executable

    def test_generation_writes_private_identity_and_public_only_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = self.fake_age_keygen(root)
            identity = root / "cluster-recovery.agekey"
            arguments = recovery_key_setup.parser().parse_args([
                "generate", "--output", str(identity), "--age-keygen", str(executable)
            ])
            self.assertEqual(recovery_key_setup.generate(arguments), 0)
            metadata = Path(str(identity) + ".recipient.json")
            document = json.loads(metadata.read_text(encoding="utf-8"))
            self.assertEqual(document["recipient"], self.RECIPIENT)
            self.assertEqual(document["recipient_sha256"], recovery_key_setup.fingerprint(self.RECIPIENT))
            self.assertNotIn("AGE-SECRET-KEY", metadata.read_text(encoding="utf-8"))
            self.assertIn("AGE-SECRET-KEY", identity.read_text(encoding="utf-8"))
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(identity.stat().st_mode), 0o600)

    def test_generation_refuses_repository_and_existing_destinations(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            recovery_key_setup.require_off_repository(ROOT / "forbidden.agekey")
        with tempfile.TemporaryDirectory() as temporary:
            existing = Path(temporary) / "existing.agekey"
            existing.write_text("do not overwrite", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
                recovery_key_setup.require_new_regular_path(existing)

    def test_identity_verification_never_prints_private_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = self.fake_age_keygen(root)
            identity = root / "identity.agekey"
            identity.write_text("AGE-SECRET-KEY-1TESTPRIVATEVALUE\n", encoding="utf-8")
            arguments = recovery_key_setup.parser().parse_args([
                "verify", "--identity", str(identity), "--recipient", self.RECIPIENT,
                "--age-keygen", str(executable),
            ])
            self.assertEqual(recovery_key_setup.verify(arguments), 0)

    def test_cluster_sync_is_holder_guarded_public_only_and_verified(self) -> None:
        body = function_body(HA_SOURCE, "mp_ha_sync_recovery_recipient")
        self.assertIn("mp_require_active_or_standalone", body)
        self.assertLess(body.index("mp_ha_peer_recovery_recipient stage"), body.index("mp_store_recovery_recipient_local"))
        self.assertIn('mp_ha_peer_recovery_recipient activate "$recipient" "$expected"', body)
        self.assertIn('peer_hash="$(mp_ha_peer_recovery_recipient hash', body)
        self.assertNotIn("AGE-SECRET-KEY", body)
        self.assertIn("recovery-recipient-sync", body)

    def test_overview_displays_matching_public_hashes_not_private_key(self) -> None:
        body = function_body(HA_SOURCE, "mp_ha_overview")
        self.assertIn("Local SHA-256", body)
        self.assertIn("Peer SHA-256", body)
        self.assertIn("MATCH", body)
        self.assertIn("must not be stored here", body)

    def test_snapshot_v2_binds_archive_to_public_key_generation(self) -> None:
        writer = function_body(SNAPSHOT_SOURCE, "mp_snapshot_write_manifest")
        verifier = function_body(SNAPSHOT_SOURCE, "mp_snapshot_verify_key_metadata")
        receipt = function_body(SNAPSHOT_SOURCE, "mp_snapshot_verify_path")
        self.assertIn('mp-opt-snapshot-v2', writer)
        self.assertIn('recipient_sha256', writer)
        self.assertIn('recovery_key_id', writer)
        self.assertIn('mp-opt-snapshot-v2', verifier)
        self.assertNotIn('mp-opt-snapshot-v1', verifier)
        self.assertIn('mp_identity_recipient', verifier)
        self.assertIn('mp-opt-snapshot-receipt-v2', receipt)
        self.assertNotIn('source_manifest_format', receipt)

    def test_portable_snapshot_menu_is_os_independent(self) -> None:
        self.assertIn('mp_snapshot_export_portable_interactive', MENU_SOURCE)
        self.assertIn('mp_snapshot_import_portable_interactive', MENU_SOURCE)
        for interface in ("windows-cmd", "windows-powershell", "linux", "macos", "generic"):
            self.assertIn(interface, PORTABLE_SOURCE)
        for checksum in ("Get-FileHash", "sha256sum -c", "shasum -a 256"):
            self.assertIn(checksum, PORTABLE_SOURCE)
        self.assertIn('mp-opt-portable-snapshot-2026-01', PORTABLE_TOOL_PATH.read_text(encoding="utf-8"))
        self.assertNotIn('AGE-SECRET-KEY-', PORTABLE_SOURCE)

    def test_portable_export_asks_only_for_alias_and_uses_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", r'''
                    export MP_ROOT="$1"
                    export MP_HOME="$2/home"
                    export MP_STATE="$2/state"
                    export MP_SNAPSHOTS="$2/snapshots"
                    calls="$2/prompts"
                    source "$1/deploy/management/common.sh"
                    source "$1/deploy/management/portable_snapshots.sh"
                    ui_input() {
                        printf '%s\n' "$2" >> "$calls"
                        case "$2" in
                            *"SSH host"*) printf '%s\n' primary.example ;;
                            *"Absolute workstation path"*) printf '%s\n' '/tmp/Recovery Files/example.mpopt-snapshot' ;;
                            *) return 1 ;;
                        esac
                    }
                    exported="$(mp_portable_transfer_inputs windows-cmd export ignored.mpopt-snapshot)"
                    [ "$exported" = $'primary.example\t.' ]
                    [ "$(wc -l < "$calls")" -eq 1 ]
                    : > "$calls"
                    imported="$(mp_portable_transfer_inputs linux import '')"
                    [ "$imported" = $'primary.example\t/tmp/Recovery Files/example.mpopt-snapshot' ]
                    [ "$(wc -l < "$calls")" -eq 2 ]
                ''', "bash", str(ROOT), temporary],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_portable_export_commands_download_and_verify_automatically(self) -> None:
        expected_hash = "a" * 64
        filename = "20260718T120000Z_full_post-key-rotation.mpopt-snapshot"
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", r'''
                    export MP_ROOT="$1"
                    export MP_HOME="$2/home"
                    export MP_STATE="$2/state"
                    export MP_SNAPSHOTS="$2/snapshots"
                    mkdir -p "$MP_STATE"
                    source "$1/deploy/management/common.sh"
                    source "$1/deploy/management/portable_snapshots.sh"
                    remote="/tmp/$3"
                    for style in windows-cmd windows-powershell linux macos; do
                        mp_portable_write_commands "$2/${style}.txt" "$style" export \
                            primary.example . "$remote" "$4"
                    done
                ''', "bash", str(ROOT), temporary, filename, expected_hash],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            reports = {
                style: (Path(temporary) / f"{style}.txt").read_text(encoding="utf-8")
                for style in ("windows-cmd", "windows-powershell", "linux", "macos")
            }
            for report in reports.values():
                self.assertIn(f'scp ', report)
                self.assertIn(f'/{filename}', report)
                self.assertIn(expected_hash, report)
                self.assertIn("MP-OPT SNAPSHOT VERIFIED", report)
                self.assertNotIn("AGE-SECRET-KEY-1", report)
            self.assertIn("Get-FileHash", reports["windows-cmd"])
            self.assertIn("Get-FileHash", reports["windows-powershell"])
            self.assertIn("sha256sum -c", reports["linux"])
            self.assertIn("shasum -a 256", reports["macos"])

    @unittest.skipUnless(shutil.which("sha256sum"), "sha256sum is required for the generated-command test")
    def test_linux_export_marker_requires_the_exact_downloaded_bytes(self) -> None:
        filename = "20260718T120000Z_full_marker-test.mpopt-snapshot"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mpopt-snapshot"
            source.write_bytes(b"verified portable package")
            expected_hash = __import__("hashlib").sha256(source.read_bytes()).hexdigest()
            report = root / "report.txt"
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", r'''
                    export MP_ROOT="$1"
                    export MP_HOME="$2/home"
                    export MP_STATE="$2/state"
                    export MP_SNAPSHOTS="$2/snapshots"
                    mkdir -p "$MP_STATE"
                    source "$1/deploy/management/common.sh"
                    source "$1/deploy/management/portable_snapshots.sh"
                    mp_portable_write_commands "$2/report.txt" linux export \
                        primary.example . "/tmp/$3" "$4"
                ''', "bash", str(ROOT), temporary, filename, expected_hash],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            command = next(
                line for line in report.read_text(encoding="utf-8").splitlines()
                if line.startswith("scp ")
            )
            fake_bin = root / "bin"
            destination = root / "download"
            fake_bin.mkdir()
            destination.mkdir()
            fake_scp = fake_bin / "scp"
            fake_scp.write_text(
                '#!/usr/bin/env sh\nset -eu\ncp "$FAKE_SCP_SOURCE" "./$FAKE_SCP_FILENAME"\n',
                encoding="utf-8",
            )
            fake_scp.chmod(0o700)
            environment = os.environ.copy()
            environment.update({
                "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
                "FAKE_SCP_SOURCE": str(source),
                "FAKE_SCP_FILENAME": filename,
            })
            verified = subprocess.run(
                ["bash", "-c", command], cwd=destination, env=environment,
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertEqual(verified.stdout.strip(), "MP-OPT SNAPSHOT VERIFIED")

            source.write_bytes(b"tampered portable package")
            rejected = subprocess.run(
                ["bash", "-c", command], cwd=destination, env=environment,
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertNotIn("MP-OPT SNAPSHOT VERIFIED", rejected.stdout)

    def test_portable_commands_are_shown_as_selectable_terminal_text(self) -> None:
        presenter = function_body(PORTABLE_SOURCE, "mp_portable_show_copyable_commands")
        selectable = function_body(COMMON_SOURCE, "ui_copyable_terminal_text")
        exporter = function_body(PORTABLE_SOURCE, "mp_snapshot_export_portable_interactive")
        importer = function_body(PORTABLE_SOURCE, "mp_snapshot_import_portable_interactive")
        self.assertIn("ui_copyable_terminal_text", presenter)
        self.assertIn('/dev/tty', selectable)
        self.assertIn(r"\033[2J\033[3J\033[H", selectable)
        self.assertLess(selectable.index("clear_sequence="), selectable.index("normal selectable terminal text"))
        self.assertIn("normal selectable terminal text", selectable)
        self.assertIn("printf '%s\\n' '----- COPY FROM HERE -----'", selectable)
        self.assertIn("'----- COPY FROM HERE -----' &&", selectable)
        self.assertNotIn("ui_text_file", presenter)
        self.assertIn("mp_portable_show_copyable_commands", exporter)
        self.assertIn("mp_portable_show_copyable_commands", importer)
        self.assertNotIn("ui_text_file", exporter)
        self.assertNotIn("ui_text_file", importer)
        self.assertIn("MP-OPT SNAPSHOT VERIFIED", exporter)

    def test_every_generated_bootstrap_code_uses_selectable_terminal_text(self) -> None:
        for function in (
            "mp_guided_initial_configuration",
            "mp_reset_root_admin",
            "mp_wipe_database",
            "mp_change_domain",
        ):
            body = function_body(ACTIONS_SOURCE, function)
            self.assertIn("ui_copyable_terminal_text", body, function)
            self.assertIn("bootstrap_view", body, function)
            self.assertNotIn('ui_message "Root recovery ready"', body, function)
            self.assertNotIn('ui_message "Database recreated"', body, function)
            self.assertNotIn('ui_message "Domain changed"', body, function)

    def test_initial_configuration_is_secret_silent_without_a_tty(self) -> None:
        guided = function_body(ACTIONS_SOURCE, "mp_guided_initial_configuration")
        self.assertIn("if mp_has_terminal; then", guided)
        self.assertIn("ui_copyable_terminal_text", guided)
        self.assertIn("Open an interactive management session", guided)
        self.assertNotIn('ui_message "Configuration complete" "$bootstrap_view"', guided)

    def test_copyable_terminal_text_is_secret_silent_without_a_tty(self) -> None:
        result = subprocess.run(
            ["bash", "-Eeuo", "pipefail", "-c", '''
                source "$1/deploy/management/common.sh"
                ui_copyable_terminal_text "Bootstrap" "DO-NOT-PRINT-THIS"
            ''', "bash", str(ROOT)],
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("DO-NOT-PRINT-THIS", result.stdout)
        self.assertNotIn("DO-NOT-PRINT-THIS", result.stderr)

    def test_copyable_terminal_text_round_trips_through_a_real_pty(self) -> None:
        master, slave = pty.openpty()
        tty_path = os.ttyname(slave)
        environment = os.environ.copy()
        environment["MP_COPYABLE_TTY"] = tty_path
        process = subprocess.Popen(
            ["bash", "-Eeuo", "pipefail", "-c", '''
                source "$1/deploy/management/common.sh"
                ui_copyable_terminal_text "Join code" "SELECTABLE-JOIN-CODE"
                printf 'RETURNED-TO-TUI\n'
            ''', "bash", str(ROOT)],
            stdin=slave, stdout=slave, stderr=slave, env=environment, close_fds=True,
        )
        os.close(slave)
        received = b""
        try:
            while b"END COPYABLE TEXT" not in received:
                ready, _, _ = select.select([master], [], [], 3)
                self.assertTrue(ready, received.decode(errors="replace"))
                received += os.read(master, 4096)
            os.write(master, b"\n")
            while b"RETURNED-TO-TUI" not in received:
                ready, _, _ = select.select([master], [], [], 3)
                self.assertTrue(ready, received.decode(errors="replace"))
                received += os.read(master, 4096)
            self.assertEqual(process.wait(timeout=3), 0)
        finally:
            os.close(master)
            if process.poll() is None:
                process.kill()
        rendered = received.decode(errors="replace")
        self.assertIn("SELECTABLE-JOIN-CODE", rendered)
        self.assertIn("RETURNED-TO-TUI", rendered)
        self.assertGreaterEqual(rendered.count("\x1b[2J\x1b[3J\x1b[H"), 2)

    def test_portable_transfer_paths_accept_spaces_and_reject_shell_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", '''
                    export MP_ROOT="$1"
                    export MP_HOME="$2/home"
                    export MP_STATE="$2/state"
                    export MP_SNAPSHOTS="$2/snapshots"
                    source "$1/deploy/management/common.sh"
                    source "$1/deploy/management/portable_snapshots.sh"
                    mp_portable_validate_host primary.example
                    mp_portable_validate_host deploy@test.example.net
                    mp_portable_validate_local_path windows-cmd 'C:\\Users\\Example User\\recovery.mpopt-snapshot'
                    mp_portable_validate_local_path windows-powershell 'C:\\Users\\Example User\\recovery.mpopt-snapshot'
                    mp_portable_validate_local_path linux '/home/brian/Recovery Files/recovery.mpopt-snapshot'
                    mp_portable_validate_local_path macos '/Users/brian/Recovery Files/recovery.mpopt-snapshot'
                    ! mp_portable_validate_local_path windows-cmd 'C:\\Recovery\\bad&whoami.mpopt-snapshot'
                    ! mp_portable_validate_host 'host;whoami'
                ''', "bash", str(ROOT), temporary],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_standalone_database_restore_revokes_bearer_access(self) -> None:
        body = function_body(SNAPSHOT_SOURCE, "mp_snapshot_revoke_restored_access")
        self.assertIn("UPDATE auth_sessions", body)
        self.assertIn("DELETE FROM passkey_ceremonies", body)
        self.assertIn("UPDATE activation_links", body)
        self.assertNotIn("UPDATE public_schedule_links", body)
        self.assertNotIn("publish_secret_hash", body)
        self.assertNotIn("DELETE FROM webauthn_credentials", body)
        self.assertIn('mp_prepare_backend_secret_permissions', body)
        self.assertNotIn('chmod 600 "$MP_ROOT/secrets/root_bootstrap_token"', body)

    def test_restore_refuses_snapshot_older_than_retained_privacy_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            extracted = root / "extracted"
            (extracted / "payload/database").mkdir(parents=True)
            (extracted / "payload/database/masterplan.dump").touch()
            manifest = extracted / "manifest.json"
            manifest.write_text(
                json.dumps({"created_at": "2026-01-01T00:00:00Z"}),
                encoding="utf-8",
            )
            command = f'''
                set -Eeuo pipefail
                source {shlex.quote(str(ROOT / "deploy/management/snapshots.sh"))}
                mp_compose_init() {{ MP_COMPOSE=(fake_compose); }}
                fake_compose() {{ printf '%s\n' '2026-02-01T00:00:00Z'; }}
                jq() {{
                    python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["created_at"])' "${{@: -1}}"
                }}
                ! mp_snapshot_guard_privacy_actions {shlex.quote(str(extracted))}
                printf '%s\n' '{{"created_at":"2026-03-01T00:00:00Z"}}' \
                    > {shlex.quote(str(manifest))}
                mp_snapshot_guard_privacy_actions {shlex.quote(str(extracted))}
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", command],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Restore blocked", result.stderr)

    def test_database_restore_requires_the_current_evidence_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            extracted = root / "extracted"
            installation = root / "installation"
            (extracted / "payload/database").mkdir(parents=True)
            (extracted / "payload/metadata").mkdir()
            (extracted / "payload/database/masterplan.dump").touch()
            (installation / "state/evidence/ledger").mkdir(parents=True)
            anchor = extracted / "payload/metadata/evidence-anchor.json"
            current = installation / "state/evidence/ledger/chain-head.json"
            anchor.write_text(json.dumps({"head_sha256": "a" * 64}), encoding="utf-8")
            current.write_text(json.dumps({"head_sha256": "b" * 64}), encoding="utf-8")
            command = f'''
                set -Eeuo pipefail
                export MP_ROOT={shlex.quote(str(installation))}
                source {shlex.quote(str(ROOT / "deploy/management/snapshots.sh"))}
                mp_env_get() {{ printf '%s\n' required; }}
                sudo() {{ [ "$1" != -n ] || shift; "$@"; }}
                jq() {{
                    python3 -c 'import json, pathlib, sys; p=pathlib.Path(sys.argv[1]); data=json.load(open(p)) if p.is_file() else json.load(sys.stdin); print(data.get("head_sha256", ""))' "${{@: -1}}"
                }}
                ! mp_snapshot_guard_evidence_head {shlex.quote(str(extracted))}
                cp {shlex.quote(str(anchor))} {shlex.quote(str(current))}
                mp_snapshot_guard_evidence_head {shlex.quote(str(extracted))}
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", command],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("different heads", result.stderr)

    def test_evidence_head_guard_reads_backend_owned_head_through_sudo(self) -> None:
        body = function_body(SNAPSHOT_SOURCE, "mp_snapshot_guard_evidence_head")
        self.assertIn('sudo -n test -s "$current"', body)
        self.assertIn('sudo -n test ! -L "$current"', body)
        self.assertIn('sudo -n cat "$current"', body)
        self.assertNotIn('jq -r \'.head_sha256 // empty\' "$current"', body)

    def test_snapshot_topology_is_resolved_and_checked_before_mutation(self) -> None:
        copy_body = function_body(SNAPSHOT_SOURCE, "mp_snapshot_copy_configuration")
        self.assertIn("container|host", copy_body)
        self.assertIn("active Caddy topology could not be resolved", copy_body)
        guard_body = function_body(SNAPSHOT_SOURCE, "mp_snapshot_guard_caddy_topology")
        self.assertIn('snapshot_mode="$(tr -d', guard_body)
        self.assertIn('current_mode="$(mp_caddy_mode)"', guard_body)
        apply_body = function_body(SNAPSHOT_SOURCE, "mp_snapshot_apply")
        self.assertLess(
            apply_body.index('mp_snapshot_guard_caddy_topology "$temporary"'),
            apply_body.index("MP_SNAPSHOT_APPLY_MUTATED=true"),
        )

    def test_restore_recreates_only_empty_optional_evidence_token_source(self) -> None:
        body = function_body(SNAPSHOT_SOURCE, "mp_snapshot_restore_configuration")
        self.assertIn('optional_evidence_token="$MP_ROOT/secrets/evidence_github_fine_grained_token"', body)
        self.assertIn('install -m 0600 /dev/null "$optional_evidence_token"', body)
        self.assertIn("intentionally excluded from snapshots", body)
        for stage in (
            "configuration-topology",
            "configuration-environment",
            "configuration-secrets",
            "configuration-secret-permissions",
            "configuration-compose-override",
            "configuration-host-caddy",
            "configuration-database-secret",
            "configuration-database-role",
            "configuration-backend-secret-permissions",
        ):
            self.assertIn(f'MP_SNAPSHOT_APPLY_STAGE="{stage}"', body)

    def test_database_snapshot_pauses_writes_and_records_evidence_anchor(self) -> None:
        body = function_body(SNAPSHOT_SOURCE, "mp_snapshot_create")
        self.assertLess(body.index('stop backend'), body.index('mp_snapshot_dump_database'))
        self.assertIn('mp_snapshot_write_evidence_anchor "$staging/payload"', body)
        self.assertIn('up -d --no-deps backend', body)

    def test_restore_uses_snapshot_key_and_current_rollback_key_separately(self) -> None:
        body = function_body(SNAPSHOT_SOURCE, "mp_snapshot_restore_interactive")
        self.assertIn('selected_recipient', body)
        self.assertIn('current_recipient', body)
        self.assertIn('rollback_identity', body)
        self.assertIn('mp_snapshot_verify_path "$pre_snapshot" "$rollback_identity"', body)
        self.assertIn('mp_snapshot_apply "$pre_snapshot" "$rollback_identity"', body)
        self.assertIn('MP_SNAPSHOT_APPLY_MUTATED', body)
        self.assertIn('No database, configuration, or service state was changed.', body)
        self.assertLess(body.index("ui_clear_terminal"), body.index("mp_snapshot_create full"))

    def test_snapshot_delete_uses_a_short_fixed_confirmation_phrase(self) -> None:
        body = function_body(SNAPSHOT_SOURCE, "mp_snapshot_delete_interactive")
        self.assertIn('"DELETE SNAPSHOT"', body)
        self.assertNotIn('"DELETE $label"', body)

    def test_restore_preflight_failure_does_not_attempt_rollback(self) -> None:
        body = function_body(SNAPSHOT_SOURCE, "mp_snapshot_restore_interactive")
        preflight_exit = body.index('if [ "${MP_SNAPSHOT_APPLY_MUTATED:-false}" != true ]')
        rollback_apply = body.index('mp_snapshot_apply "$pre_snapshot" "$rollback_identity"', preflight_exit)
        self.assertLess(preflight_exit, rollback_apply)
        apply_body = function_body(SNAPSHOT_SOURCE, "mp_snapshot_apply")
        self.assertLess(
            apply_body.index('MP_SNAPSHOT_APPLY_STAGE="evidence-head-preflight"'),
            apply_body.index('MP_SNAPSHOT_APPLY_MUTATED=true'),
        )

    def test_rotation_preserves_originals_until_verified_baseline(self) -> None:
        transform = function_body(ROTATION_SOURCE, "mp_snapshot_reencrypt_path")
        rotate = function_body(ROTATION_SOURCE, "mp_rotate_recovery_recipient")
        self.assertIn('mp_snapshot_verify_path "$destination" "$new_identity"', transform)
        self.assertLess(rotate.index('mp_snapshot_reencrypt_path'), rotate.index('mp_rotation_commit_copy'))
        self.assertIn('mp_rotation_rollback_committed', rotate)
        self.assertIn('mp_snapshot_copy_off_server "$baseline"', rotate)
        self.assertIn('awaiting-portable-export', ROTATION_SOURCE)
        self.assertIn('operator-sha256-confirmed', ROTATION_SOURCE)
        self.assertIn('mp_rotation_finalize_portable_export', ROTATION_SOURCE)
        self.assertIn('manual_portable', rotate)
        self.assertIn('ssh_archive', rotate)
        self.assertIn('ROTATE WITHOUT OLD KEY', rotate)
        self.assertIn('ROTATE RECOVERY KEY', rotate)
        self.assertIn('mp_rotation_reconcile_incomplete', rotate)
        self.assertIn('baseline-verified', ROTATION_SOURCE)
        self.assertIn('RECOVER ROTATION', ROTATION_SOURCE)
        self.assertNotIn('AGE-SECRET-KEY-', ROTATION_SOURCE)

    def test_manual_export_confirmation_is_durable_and_secret_free(self) -> None:
        recorder = function_body(PORTABLE_SOURCE, "mp_portable_record_confirmed_export")
        self.assertIn('mp-opt-manual-recovery-export-v1', recorder)
        self.assertIn('operator-sha256-confirmed', recorder)
        self.assertIn('package_sha256', recorder)
        self.assertIn('recovery_key_id', recorder)
        self.assertNotIn('local_path', recorder)
        self.assertNotIn('AGE-SECRET-KEY', recorder)
        exporter = function_body(PORTABLE_SOURCE, "mp_snapshot_export_portable_interactive")
        self.assertIn('mp_portable_record_confirmed_export', exporter)
        self.assertIn('mp_rotation_finalize_portable_export', exporter)

    def test_recovery_storage_supports_manual_and_ssh_modes(self) -> None:
        configure = function_body(HA_SOURCE, "mp_ha_configure_archive_target")
        self.assertIn('manual_portable', configure)
        self.assertIn('ssh_archive', configure)
        self.assertIn('Manual workstation export', configure)
        self.assertIn('HA_ARCHIVE_SSH_TARGET', configure)
        self.assertIn('Passwordless SSH verification failed', configure)
        self.assertIn('HA_RECOVERY_STORAGE_MODE', COMMON_SOURCE)

    @unittest.skipUnless(shutil.which("jq"), "jq is required for the shell integration test")
    def test_confirmed_portable_export_updates_receipt_and_public_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshots = root / "snapshots"
            state = root / "state"
            selected = snapshots / "20260718T120000Z_full_baseline"
            selected.mkdir(parents=True)
            state.mkdir()
            (state / "portable-export-inventory").mkdir()
            archive = selected / "snapshot.tar.age"
            archive.write_bytes(b"encrypted-test-archive")
            archive_hash = __import__("hashlib").sha256(archive.read_bytes()).hexdigest()
            (selected / "receipt.json").write_text(json.dumps({
                "format": "mp-opt-snapshot-receipt-v2",
                "created_at": "2026-07-18T12:00:00Z",
                "archive_sha256": archive_hash,
                "verification": "deep-verified",
                "encryption": {"recovery_key_id": "age-key-test"},
                "storage": {"local": "deep-verified"},
            }), encoding="utf-8")
            package_id = "12345678-1234-4123-8123-123456789abc"
            package_hash = "a" * 64
            command = f'''
                export MP_ROOT={shlex.quote(str(ROOT))}
                export MP_HOME={shlex.quote(str(root / "home"))}
                export MP_STATE={shlex.quote(str(state))}
                export MP_SNAPSHOTS={shlex.quote(str(snapshots))}
                source "$MP_ROOT/deploy/management/common.sh"
                source "$MP_ROOT/deploy/management/portable_snapshots.sh"
                mp_portable_record_confirmed_export {shlex.quote(str(selected))} {package_id} {package_hash} 12345
                jq -e '.storage.portable.state == "operator-sha256-confirmed"
                    and .storage.portable.package_id == "{package_id}"
                    and .storage.portable.package_sha256 == "{package_hash}"' {shlex.quote(str(selected / "receipt.json"))}
                jq -e '.format == "mp-opt-manual-recovery-export-v1"
                    and .snapshot == "{selected.name}"
                    and .package_id == "{package_id}"
                    and .package_sha256 == "{package_hash}"' "$MP_MANUAL_EXPORT_STATE"
                ! grep -Eq 'AGE-SECRET-KEY|workstation|local_path' "$MP_MANUAL_EXPORT_STATE"
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", command],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("jq"), "jq is required for the shell integration test")
    def test_manual_rotation_finalizes_only_matching_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_root = root / "state"
            snapshots = root / "snapshots"
            rotations = state_root / "recovery-rotations"
            baseline = snapshots / "20260718T120000Z_full_post-key-rotation-123456789abc"
            old_copy = snapshots / "20260718T110000Z_full_old"
            protected = snapshots / ".pre-rotation-12345678-1234-1234-1234-123456789abc-20260718T110000Z_full_old"
            rotations.mkdir(parents=True)
            baseline.mkdir(parents=True)
            old_copy.mkdir()
            protected.mkdir()
            job = "12345678-1234-1234-1234-123456789abc"
            recipient = "age1" + "c" * 58
            package_hash = "d" * 64
            (baseline / "receipt.json").write_text(json.dumps({
                "format": "mp-opt-snapshot-receipt-v2",
                "verification": "deep-verified",
                "encryption": {"recipient": recipient},
                "storage": {"portable": {
                    "state": "operator-sha256-confirmed",
                    "package_sha256": package_hash,
                }},
            }), encoding="utf-8")
            manual = state_root / "manual-recovery-export.json"
            manual.write_text(json.dumps({
                "format": "mp-opt-manual-recovery-export-v1",
                "state": "operator-sha256-confirmed",
                "snapshot": baseline.name,
                "package_sha256": package_hash,
            }), encoding="utf-8")
            state_file = rotations / f"{job}.state.json"
            state_file.write_text(json.dumps({
                "format": "mp-opt-recovery-rotation-state-v1",
                "job_id": job,
                "phase": "awaiting-portable-export",
                "rotation_kind": "planned",
                "recovery_storage_mode": "manual_portable",
                "old_recipient": "age1" + "b" * 58,
                "new_recipient": recipient,
                "baseline": baseline.name,
            }), encoding="utf-8")
            (rotations / f"{job}.copies.tsv").write_text(
                f"local\t-\t{snapshots}\t{old_copy.name}\n", encoding="utf-8"
            )
            (rotations / f"{job}.jsonl").touch()
            command = f'''
                export MP_ROOT={shlex.quote(str(ROOT))}
                export MP_HOME={shlex.quote(str(root / "home"))}
                export MP_STATE={shlex.quote(str(state_root))}
                export MP_SNAPSHOTS={shlex.quote(str(snapshots))}
                export MP_MANUAL_EXPORT_STATE={shlex.quote(str(manual))}
                source "$MP_ROOT/deploy/management/common.sh"
                source "$MP_ROOT/deploy/management/recovery_rotation.sh"
                mp_require_ha_maintenance_window() {{ return 0; }}
                mp_recovery_recipient() {{ printf '%s\n' {recipient}; }}
                mp_load_ha_config() {{ export HA_ROLE=standalone; }}
                mp_lock() {{ return 0; }}
                mp_unlock() {{ return 0; }}
                mp_audit() {{ return 0; }}
                ! mp_rotation_finalize_state {shlex.quote(str(state_file))} {shlex.quote(str(baseline))} {'e' * 64}
                test -d {shlex.quote(str(protected))}
                mp_rotation_finalize_state {shlex.quote(str(state_file))} {shlex.quote(str(baseline))} {package_hash}
                test ! -e {shlex.quote(str(protected))}
                jq -e '.phase == "complete"' {shlex.quote(str(state_file))}
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", command],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_off_server_copy_verifies_archive_and_receipt(self) -> None:
        body = function_body(SNAPSHOT_SOURCE, "mp_snapshot_copy_off_server")
        self.assertIn('snapshot.tar.age', body)
        self.assertIn('receipt.json', body)
        self.assertIn('off_server: "hash-verified"', body)
        self.assertIn('--delete', body)

    def test_cluster_sync_changes_both_public_recipients(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            peer = root / "peer-recipient"
            home.mkdir()
            previous = "age1" + "b" * 58
            replacement = "age1" + "c" * 58
            peer.write_text(previous + "\n", encoding="utf-8")
            script = f'''
                set -Eeuo pipefail
                export HOME={home!s}
                export MP_HOME="$HOME/config"
                export MP_STATE="$HOME/state"
                export MP_SNAPSHOTS="$HOME/snapshots"
                export MP_RECIPIENT_FILE="$MP_HOME/recovery-recipient"
                export MP_AUDIT_FILE="$MP_STATE/audit.log"
                export MP_LOCK_FILE="$MP_STATE/lock"
                source "{ROOT / "deploy/management/common.sh"}"
                source "{ROOT / "deploy/management/ha.sh"}"
                mp_initialise_paths
                mp_store_recovery_recipient_local {previous}
                mp_load_ha_config() {{
                    export HA_ROLE=dynamic HA_NODE_ID=node-a HA_PEER_NODE_ID=node-b HA_PEER_SSH=peer
                }}
                mp_require_active_or_standalone() {{ return 0; }}
                mp_ha_peer_recovery_recipient() {{
                    case "$1" in
                        read) cat {peer!s} ;;
                        stage) printf '%s\\n' "$2" > {peer!s}.pending ;;
                        activate)
                            test "$2" = {replacement}
                            test -n "$3"
                            mv {peer!s}.pending {peer!s}
                            ;;
                        restore) if [ -n "${{2:-}}" ]; then printf '%s\\n' "$2" > {peer!s}; else rm -f {peer!s}; fi ;;
                        hash) tr -d '\\r\\n' < {peer!s} | sha256sum | awk '{{print $1}}' ;;
                    esac
                }}
                mp_audit() {{ :; }}
                mp_ha_sync_recovery_recipient {replacement}
                test "$(mp_recovery_recipient)" = {replacement}
                test "$(tr -d '\\r\\n' < {peer!s})" = {replacement}
            '''
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
