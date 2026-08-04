"""Contracts for resumable commissioning, DNS routing and signed releases."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import urllib.error
from unittest import mock

from deploy.ha import pairing
from deploy.release import install_release


ROOT = Path(__file__).resolve().parents[3]
SETUP = (ROOT / "deploy/management/setup_v2.sh").read_text(encoding="utf-8")
COMMON = (ROOT / "deploy/management/common.sh").read_text(encoding="utf-8")
ACTIONS = (ROOT / "deploy/management/actions.sh").read_text(encoding="utf-8")
DEPLOY = (ROOT / "deploy/deploy.sh").read_text(encoding="utf-8")
WORKER = (ROOT / "infra/cloudflare-ha-witness/src/index.ts").read_text(encoding="utf-8")
CADDY = (ROOT / "infra/Caddyfile.ha").read_text(encoding="utf-8")
CADDY_STANDALONE = (ROOT / "infra/Caddyfile").read_text(encoding="utf-8")
CADDY_IMAGE = (ROOT / "infra/Dockerfile.caddy").read_text(encoding="utf-8")
RELEASE = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
SCHEDULER = (ROOT / "deploy/ha/replication_scheduler.py").read_text(encoding="utf-8")
INSTALL_RELEASE = (ROOT / "deploy/release/install_release.py").read_text(encoding="utf-8")
BOOTSTRAP = (ROOT / "deploy/setup-server.sh").read_text(encoding="utf-8")
PORTABLE_RECOVERY_DOC = (ROOT / "docs/portable-snapshot-recovery.md").read_text(
    encoding="utf-8"
)


def shell_function(source: str, name: str) -> str:
    marker = f"{name}() {{"
    start = source.index(marker)
    next_function = source.find("\nmp_", start + len(marker))
    return source[start:next_function if next_function >= 0 else len(source)]


class PairingCodeTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX shell state contract")
    def test_unsigned_state_pins_first_pushed_head_and_ignores_moving_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            state = work / "state"
            fake_bin = work / "bin"
            state.mkdir()
            fake_bin.mkdir()
            head = work / "head"
            head.write_text("a" * 40, encoding="ascii")
            fake_git = fake_bin / "git"
            fake_git.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$*\" == *'rev-parse HEAD'* ]]; then cat \"$FAKE_HEAD\"; exit 0; fi\n"
                "if [[ \"$*\" == *'fetch --no-tags --force origin'* ]]; then exit \"${FAKE_FETCH_STATUS:-0}\"; fi\n"
                "if [[ \"$*\" == *'rev-parse FETCH_HEAD'* ]]; then cat \"$FAKE_HEAD\"; exit 0; fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_git.chmod(0o755)
            policy = work / "policy"
            policy.write_text("test\n", encoding="ascii")
            script = r'''
                export MP_ROOT="$1" MP_STATE="$2" MP_SETUP_V2_STATE="$2/setup.json"
                export MP_DEPLOYMENT_POLICY_FILE="$3" FAKE_HEAD="$4"
                export PATH="$5:$PATH"
                source "$6/deploy/management/setup_v2.sh"
                ui_error() { printf '%s\n' "$*" >&2; }
                mp_setup_state_begin standalone-new
                printf '%s\n' "$(jq -r .campaign_commit "$MP_SETUP_V2_STATE")"
                printf '%s' 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' > "$FAKE_HEAD"
                mp_setup_state_begin standalone-new
                printf '%s\n' "$(jq -r .campaign_commit "$MP_SETUP_V2_STATE")"
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script, "bash", str(work),
                 str(state), str(policy), str(head), str(fake_bin), str(ROOT)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ["a" * 40, "a" * 40])

    @unittest.skipIf(os.name == "nt", "POSIX shell state contract")
    def test_unsigned_state_rejects_unpushed_or_uppercase_head(self) -> None:
        state_begin = shell_function(SETUP, "mp_setup_state_begin")
        self.assertIn("Push the exact commit before commissioning", state_begin)
        self.assertIn(r"^[0-9a-f]{40}$", state_begin)
        self.assertLess(
            state_begin.index("fetch --no-tags --force origin"),
            state_begin.index('format:"mp-opt-setup-state-v2"'),
        )

    def test_operator_owned_values_have_neutral_examples_not_deployment_presets(self) -> None:
        guided = shell_function(ACTIONS, "mp_guided_initial_configuration")
        configure_smtp = shell_function(ACTIONS, "mp_configure_smtp")
        node_material = shell_function(SETUP, "mp_setup_prepare_node_material")
        standalone_dns = shell_function(SETUP, "mp_setup_verify_standalone_dns")
        smtp_dns = shell_function(SETUP, "mp_setup_verify_smtp_and_dns")

        self.assertNotIn("mp-opt.net", guided)
        self.assertNotIn("smtp.protonmail.ch", guided)
        self.assertNotIn("access@", guided)
        self.assertNotIn("smtp.protonmail.ch", configure_smtp)
        self.assertIn("schedule.example.org", guided)
        self.assertIn("admin@example.org", guided)
        self.assertIn("smtp.example.org", guided)
        self.assertIn("notifications@example.org", guided)
        self.assertIn("support@example.org", guided)

        for prompt in (node_material, standalone_dns):
            self.assertNotIn("api.ipify.org", prompt)
            self.assertNotIn("api64.ipify.org", prompt)
            self.assertIn("203.0.113.10", prompt)
            self.assertIn("2001:db8::10", prompt)
        self.assertNotIn('"default")', smtp_dns)
        self.assertIn("default or provider1", smtp_dns)

    def test_full_loss_runbook_links_the_current_destructive_gate(self) -> None:
        self.assertIn(
            "[destructive recovery drill completion gate]"
            "(recovery-drill.md#completion-gate)",
            PORTABLE_RECOVERY_DOC,
        )
        self.assertNotIn(
            "high-availability.md#5-destructive-verification-gate",
            PORTABLE_RECOVERY_DOC,
        )

    def test_round_trip_and_tamper_detection(self) -> None:
        document = {
            "format": "mp-opt-ha-join-v2",
            "cluster_id": "mp-opt-cluster-1234",
            "domain": "calendar.example.org",
            "witness_url": "https://witness.example.workers.dev",
            "pairing_secret": "a" * 48,
            "node_id": "node-b",
            "deployment_lane": "unsigned",
            "campaign_commit": "a" * 40,
        }
        encoded = pairing.encode_document(document)
        self.assertEqual(pairing.decode_code(encoded), document)
        replacement = "A" if encoded[-1] != "A" else "B"
        with self.assertRaises(ValueError):
            pairing.decode_code(encoded[:-1] + replacement)

    def test_cli_never_outputs_a_private_node_identity(self) -> None:
        self.assertNotIn("AGE-SECRET-KEY", SETUP)
        self.assertIn("pending-ha-join.json", SETUP)
        self.assertIn("expiresAt: now + 15 * 60 * 1000", WORKER)

    def test_fresh_modes_cannot_overwrite_an_existing_installation(self) -> None:
        self.assertIn("A live standalone configuration already exists", SETUP)
        self.assertIn("Join codes are accepted only on a fresh", SETUP)
        self.assertIn("full-loss recovery is only for a replacement VPS", SETUP)

    def test_replacement_pairing_targets_whichever_node_is_lost(self) -> None:
        self.assertIn('--arg target "$HA_PEER_NODE_ID"', SETUP)
        self.assertIn("node_id:$target", SETUP)
        self.assertIn('--arg node "$HA_NODE_ID"', SETUP)
        self.assertIn('--arg peer "$HA_PEER_NODE_ID"', SETUP)
        self.assertNotIn('printf \'{"node_id":"node-a"}', SETUP)

    def test_expired_pairing_code_can_be_reissued_safely(self) -> None:
        self.assertIn("const activePairing", WORKER)
        self.assertIn("The previous code expired", SETUP)

    def test_worker_commissioning_requests_are_exactly_retryable(self) -> None:
        self.assertIn("bootstrapHash", WORKER)
        self.assertIn("bootstrapDisposition(existing.bootstrapHash, bootstrapHash)", WORKER)
        self.assertIn("joinDisposition(pairing, pairSecretHash, materialHash", WORKER)
        self.assertIn("pairing.consumedAt = Date.now()", WORKER)
        self.assertIn("const exactPairingRetry", WORKER)
        self.assertNotIn("delete cluster.pairing", WORKER)

    def test_local_pending_receipts_cover_both_remote_commit_boundaries(self) -> None:
        self.assertIn("pending-witness-bootstrap.json", SETUP)
        self.assertIn("mp-opt-pending-witness-bootstrap-v1", SETUP)
        self.assertIn("pending-local-join.json", SETUP)
        self.assertIn("mp-opt-pending-local-join-v2", SETUP)
        self.assertIn("mp-opt-ha-join-v2", SETUP)
        self.assertIn("deployment_lane:$lane", SETUP)
        self.assertIn("campaign_commit:", SETUP)
        self.assertLess(
            SETUP.index('mv "$bootstrap_tmp" "$MP_SETUP_V2_PENDING_BOOTSTRAP"'),
            SETUP.index('mp_setup_witness_call bootstrap'),
        )
        self.assertLess(
            SETUP.index('mv "$pending" "$MP_SETUP_V2_PENDING_LOCAL_JOIN"'),
            SETUP.index('mp_setup_witness_call join'),
        )

    def test_full_loss_uses_the_exact_import_receipt_and_blank_restore(self) -> None:
        self.assertIn("setup-import-receipt.json", SETUP)
        self.assertIn('mp_snapshot_restore_full_loss "$imported_snapshot"', SETUP)
        self.assertNotIn("mp_snapshot_restore_interactive || return 1", SETUP)
        snapshots = (ROOT / "deploy/management/snapshots.sh").read_text(encoding="utf-8")
        self.assertIn("mp_snapshot_restore_full_loss()", snapshots)
        self.assertIn("Old HA identity, TLS topology", snapshots)
        self.assertIn('rm -f "$MP_ROOT/infra/docker-compose.override.yml"', snapshots)
        self.assertIn("resume_installed=true", snapshots)
        self.assertIn('cmp -s "$payload/config/.env" "$MP_ROOT/.env"', snapshots)
        self.assertIn("docker volume inspect masterplan_pgdata", snapshots)

    def test_commission_menu_is_contextual_at_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            state.mkdir()
            capture = root / "menu.txt"
            script = r'''
                export MP_ROOT="$1" MP_STATE="$2" MENU_CAPTURE="$3"
                export MP_SETUP_V2_STATE="$2/setup-state.json"
                source "$4/deploy/management/setup_v2.sh"
                ui_menu() { printf '%s\n' "$*" > "$MENU_CAPTURE"; printf 'cancel\n'; }
                ui_message() { :; }
                ui_error() { return 1; }
                mp_ha_role() { printf '%s\n' "${TEST_ROLE:-standalone}"; }
                mp_setup_v2
            '''
            def menu(env: dict[str, str]) -> str:
                result = subprocess.run(
                    ["bash", "-Eeuo", "pipefail", "-c", script, "bash",
                     str(root), str(state), str(capture), str(ROOT)],
                    env={**__import__("os").environ, **env}, text=True,
                    capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return capture.read_text(encoding="utf-8")

            blank = menu({"TEST_ROLE": "standalone"})
            self.assertIn("Fresh single-node server", blank)
            self.assertIn("Join an existing HA pair", blank)
            self.assertNotIn("Convert this existing", blank)

            (root / ".env").write_text("DOMAIN=example.test\n", encoding="utf-8")
            standalone = menu({"TEST_ROLE": "standalone"})
            self.assertIn("Add a blank second VPS", standalone)
            self.assertNotIn("Fresh single-node server", standalone)

            ha = menu({"TEST_ROLE": "dynamic"})
            self.assertIn("Replace the lost peer", ha)
            self.assertNotIn("Fresh two-node HA", ha)

    def test_cancelling_after_completed_commissioning_preserves_the_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            state.mkdir()
            checkpoint = state / "setup-state.json"
            checkpoint.write_text(
                json.dumps({
                    "format": "mp-opt-setup-state-v2",
                    "mode": "standalone-new",
                    "state": "complete",
                    "completed": ["validated", "smtp_verified"],
                }),
                encoding="utf-8",
            )
            (root / ".env").write_text("DOMAIN=example.test\n", encoding="utf-8")
            script = r'''
                export MP_ROOT="$1" MP_STATE="$2"
                export MP_SETUP_V2_STATE="$2/setup-state.json"
                source "$3/deploy/management/setup_v2.sh"
                ui_menu() { printf 'cancel\n'; }
                ui_message() { :; }
                ui_error() { return 1; }
                mp_ha_role() { printf 'standalone\n'; }
                mp_setup_v2
            '''
            result = subprocess.run(
                ["bash", "-Eeuo", "pipefail", "-c", script, "bash",
                 str(root), str(state), str(ROOT)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(checkpoint.is_file())
            self.assertEqual(
                json.loads(checkpoint.read_text(encoding="utf-8"))["state"],
                "complete",
            )

    def test_every_supported_setup_has_guarded_checkpoint_order(self) -> None:
        standalone = shell_function(SETUP, "mp_setup_standalone")
        for earlier, later in (
            ("mp_guided_initial_configuration", "mp_setup_verify_standalone_dns"),
            ("mp_setup_verify_standalone_dns", "mp_setup_deploy_application"),
            ("mp_setup_deploy_application", "mp_setup_register_root_passkey"),
            ("mp_setup_register_root_passkey", "mp_validate_installation"),
            ("mp_validate_installation", "mp_setup_verify_smtp_and_dns"),
        ):
            self.assertLess(standalone.index(earlier), standalone.index(later))

        primary_resume = shell_function(SETUP, "mp_setup_primary_resume")
        self.assertLess(
            primary_resume.index("mp_setup_deploy_application"),
            primary_resume.index("mp_setup_register_root_passkey"),
        )
        self.assertLess(
            primary_resume.index("mp_setup_register_root_passkey"),
            primary_resume.index("mp_ha_replicate_now"),
        )

        primary = shell_function(SETUP, "mp_setup_primary_create")
        self.assertLess(primary.index("migration_snapshot"), primary.index("witness_bootstrap"))
        self.assertLess(
            primary.index('mv "$bootstrap_tmp" "$MP_SETUP_V2_PENDING_BOOTSTRAP"'),
            primary.index("mp_setup_witness_call bootstrap"),
        )
        self.assertLess(
            primary.index("mp_setup_witness_call bootstrap"),
            primary.index('mv "$pending" "$MP_SETUP_V2_PENDING_JOIN"'),
        )

        joining = shell_function(SETUP, "mp_setup_join_node")
        self.assertLess(
            joining.index('mv "$pending" "$MP_SETUP_V2_PENDING_LOCAL_JOIN"'),
            joining.index("mp_setup_witness_call join"),
        )
        self.assertLess(
            joining.index("mp_setup_witness_call join"),
            joining.index("mp_setup_install_ha_identity"),
        )

        replacement = shell_function(SETUP, "mp_setup_replace_standby")
        self.assertLess(
            replacement.index('mv "$replacement_tmp" "$MP_SETUP_V2_PENDING_REPLACEMENT"'),
            replacement.index("mp_setup_witness_call pair-open"),
        )
        self.assertLess(
            replacement.index("mp_setup_witness_call pair-open"),
            replacement.index('mp_setup_state_begin replace-primary'),
        )

    def test_test_policy_restores_exact_management_checkout_after_signed_baseline(self) -> None:
        install = shell_function(SETUP, "mp_setup_install_signed_release")
        restore = shell_function(SETUP, "mp_setup_restore_test_management_checkout")
        installer = install.index("install_release.py")
        self.assertGreater(
            install.index("mp_setup_restore_test_management_checkout", installer),
            installer,
        )
        self.assertGreaterEqual(install.count("mp_setup_restore_test_management_checkout"), 2)
        self.assertIn('= test ] || return 0', restore)
        self.assertIn(".campaign_commit // empty", restore)
        self.assertIn('restore --source="$commit" --', restore)
        self.assertIn("deploy manage.sh configure-production.sh", restore)
        self.assertIn("diff --quiet --ignore-submodules", restore)

    def test_unsigned_setup_has_one_pinned_application_checkpoint(self) -> None:
        state = shell_function(SETUP, "mp_setup_state_begin")
        standalone = shell_function(SETUP, "mp_setup_standalone")
        reconcile = shell_function(SETUP, "mp_setup_reconcile_unsigned_application")
        self.assertIn('format:"mp-opt-setup-state-v2"', state)
        self.assertIn('deployment_lane:$lane', state)
        self.assertIn('campaign_commit:', state)
        self.assertIn('fetch --no-tags --force origin "$commit"', state)
        self.assertIn('rev-parse FETCH_HEAD', state)
        self.assertNotIn("test_commit_deployed", SETUP)
        self.assertNotIn("root_passkey_registered", SETUP)
        self.assertIn("application_deployed", standalone)
        self.assertIn("root_commissioning_complete", standalone)
        self.assertIn("Recovering exact deployment", reconcile)
        self.assertIn("mp_wait_for_health 45", reconcile)
        self.assertIn("Automatic fallback is prohibited", reconcile)

    def test_setup_failure_returns_to_menu_with_specific_resume_state(self) -> None:
        setup = shell_function(SETUP, "mp_setup_v2")
        self.assertIn("SETUP_ACTION_PAUSED", setup)
        self.assertIn("The exact lane and commit remain pinned", setup)
        self.assertIn('return 0', setup)

    def test_standalone_dns_wait_retries_at_thirty_second_intervals(self) -> None:
        script = r'''
            TEST_ROOT="$(mktemp -d)"
            trap 'rm -rf -- "$TEST_ROOT"' EXIT
            export MP_ROOT="$PWD" MP_STATE="$TEST_ROOT/state"
            mkdir -p "$MP_STATE"
            source deploy/management/setup_v2.sh
            export MP_DNS_POLL_INTERVAL_SECONDS=30
            TEST_ATTEMPTS="$TEST_ROOT/attempts"
            TEST_SLEEPS="$TEST_ROOT/sleeps"
            export TEST_ATTEMPTS TEST_SLEEPS
            dig() {
                local count=0
                [ -s "$TEST_ATTEMPTS" ] && count="$(cat "$TEST_ATTEMPTS")"
                count=$((count + 1))
                printf '%s\n' "$count" > "$TEST_ATTEMPTS"
                if [ "$count" -lt 3 ]; then
                    printf '203.0.113.9\n'
                else
                    printf '198.51.100.7\n'
                fi
            }
            sleep() { printf '%s\n' "$1" >> "$TEST_SLEEPS"; }
            mp_setup_wait_for_standalone_dns example.test 198.51.100.7
            printf 'attempts=%s\n' "$(cat "$TEST_ATTEMPTS")"
            printf 'sleeps=%s\n' "$(paste -sd, "$TEST_SLEEPS")"
        '''
        result = subprocess.run(
            ["bash", "-Eeuo", "pipefail", "-c", script], cwd=ROOT,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("attempts=3", result.stdout)
        self.assertIn("sleeps=30,30", result.stdout)
        self.assertIn("Public DNS now resolves", result.stdout)

    def test_launcher_resumes_before_showing_waiting_for_primary(self) -> None:
        launcher = (ROOT / "manage.sh").read_text(encoding="utf-8")
        self.assertLess(
            launcher.index('ui_confirm "Resume commissioning"'),
            launcher.index('ui_message "Waiting for the primary"'),
        )


class DnsOnlyHaTests(unittest.TestCase):
    def test_new_clusters_use_scoped_dns_and_exact_acme_name(self) -> None:
        self.assertIn('provider: "cloudflare-dns"', WORKER)
        self.assertIn('const expected = `_acme-challenge.${cluster.routing.hostname}`', WORKER)
        self.assertIn('return this.env.CLOUDFLARE_DNS_API_TOKEN || ""', WORKER)
        self.assertIn('if (type !== "TXT") record.proxied = false', WORKER)
        self.assertIn("DNS_TTL_SECONDS = 60", WORKER)

    def test_vps_caddy_has_no_cloudflare_credential(self) -> None:
        self.assertIn("dns mpopt_witness", CADDY)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", CADDY)
        self.assertNotIn("CLOUDFLARE_DNS_API_TOKEN", CADDY)
        self.assertIn("dns.providers.mpopt_witness", CADDY_IMAGE)
        self.assertNotIn("CLOUDFLARE_DNS_API_TOKEN", CADDY_IMAGE)

    def test_recovery_key_route_requires_root_and_fresh_passkey(self) -> None:
        for caddy in (CADDY_STANDALONE, CADDY):
            self.assertIn("@recoveryKey path /recovery-key", caddy)
            self.assertIn("forward_auth @recoveryKey backend:8000", caddy)
            self.assertIn("uri /api/v1/auth/root-access", caddy)
        page = (ROOT / "web/src/app/recovery-key/page.tsx").read_text(encoding="utf-8")
        self.assertIn("/api/v1/auth/recovery-key-access", page)
        self.assertIn("withReauth", page)

    def test_setup_hardcodes_nodes_and_guides_legacy_retirement(self) -> None:
        self.assertIn("node-a", SETUP)
        self.assertIn("node-b", SETUP)
        self.assertIn("legacy-load-balancer-retirement.json", SETUP)
        self.assertIn("date -u -d '+7 days'", SETUP)


class SignedReleaseTests(unittest.TestCase):
    @staticmethod
    def github_error(status: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            "https://api.github.com/repos/example/releases/latest",
            status,
            "simulated",
            {},
            None,
        )

    def test_release_discovery_retries_a_transient_404_then_succeeds(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"tag_name":"v3.8.0"}')
        opener = mock.Mock(side_effect=[self.github_error(404), response])
        sleeper = mock.Mock()

        self.assertEqual(
            install_release.latest_stable_tag(opener=opener, sleeper=sleeper),
            "v3.8.0",
        )
        self.assertEqual(opener.call_count, 2)
        sleeper.assert_called_once_with(1)

    def test_release_discovery_exhausts_bounded_retries_without_busy_looping(self) -> None:
        opener = mock.Mock(
            side_effect=[
                self.github_error(404)
                for _ in range(len(install_release.LATEST_RELEASE_RETRY_DELAYS) + 1)
            ]
        )
        sleeper = mock.Mock()

        with self.assertRaisesRegex(
            install_release.ReleaseDiscoveryError,
            r"failed after 4 attempt\(s\): HTTP 404",
        ):
            install_release.latest_stable_tag(opener=opener, sleeper=sleeper)

        self.assertEqual(opener.call_count, 4)
        self.assertEqual(
            [call.args[0] for call in sleeper.call_args_list],
            list(install_release.LATEST_RELEASE_RETRY_DELAYS),
        )

    def test_release_discovery_failure_does_not_create_installation_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(
                install_release,
                "latest_stable_tag",
                side_effect=install_release.ReleaseDiscoveryError("simulated"),
            ), mock.patch(
                "sys.argv",
                ["install_release.py", "--repo-root", str(root)],
            ):
                with self.assertRaises(install_release.ReleaseDiscoveryError):
                    install_release.main()

            self.assertEqual(list(root.iterdir()), [])

    def test_cosign_reads_private_host_files_as_their_owner_without_widening_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            manifest = work / "release-manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            work.chmod(0o700)
            manifest.chmod(0o600)

            with mock.patch.object(
                install_release,
                "host_container_user",
                return_value="1000:1000",
            ), mock.patch.object(subprocess, "run") as run:
                install_release.run_cosign(work, "verify-blob", "/work/release-manifest.json")

            command = run.call_args.args[0]
            self.assertEqual(command[:7], [
                "docker", "run", "--rm",
                "--user", "1000:1000",
                "--env", "HOME=/tmp",
            ])
            self.assertIn(f"{work}:/work:ro", command)
            if os.name == "posix":
                self.assertEqual(work.stat().st_mode & 0o777, 0o700)
                self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
            self.assertTrue(run.call_args.kwargs["check"])

    def test_release_contains_signed_operations_frontend_and_images(self) -> None:
        self.assertIn("operations.tar.gz", RELEASE)
        self.assertIn("cosign sign-blob", RELEASE)
        self.assertIn("cosign sign --yes", RELEASE)
        self.assertIn("--platform linux/amd64,linux/arm64", RELEASE)
        self.assertIn("Dockerfile.tools", RELEASE)
        self.assertIn("MP_TOOLS_IMAGE", INSTALL_RELEASE)
        self.assertIn('"$tools_image" deploy /worker/src/index.ts', SETUP)
        self.assertIn(".release.env", COMMON)
        self.assertIn("--no-build", DEPLOY)
        self.assertIn("mp-opt-setup.sh", RELEASE)
        self.assertRegex(
            install_release.COSIGN_IMAGE,
            r"^ghcr\.io/sigstore/cosign/cosign@sha256:[0-9a-f]{64}$",
        )

    def test_bootstrap_rejects_moving_refs_and_does_not_execute_remote_shell(self) -> None:
        self.assertNotIn("get.docker.com", BOOTSTRAP)
        self.assertNotIn("| sh", BOOTSTRAP)
        self.assertNotIn("| bash", BOOTSTRAP)
        self.assertNotIn('MP_REPOSITORY_REF:-main', BOOTSTRAP)
        self.assertIn("A verified stable release tag is required", BOOTSTRAP)
        self.assertIn('/usr/sbin/sshd -T', BOOTSTRAP)

    def test_one_previous_release_can_be_exchanged_for_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pairs = [
                (root / "deploy", root / ".deploy.previous"),
                (root / "infra", root / ".infra.previous"),
                (root / "web/out", root / "web/.out.previous"),
                (root / "runtime/frontend-csp.caddy", root / "runtime/.frontend-csp.previous"),
                (root / "manage.sh", root / ".manage.sh.previous"),
                (root / "configure-production.sh", root / ".configure-production.sh.previous"),
                (root / ".release.env", root / ".release.env.previous"),
            ]
            for current, previous in pairs:
                current.parent.mkdir(parents=True, exist_ok=True)
                previous.parent.mkdir(parents=True, exist_ok=True)
                if current.suffix or current.name in {"manage.sh", "configure-production.sh"}:
                    current.write_text("current", encoding="utf-8")
                    previous.write_text("previous", encoding="utf-8")
                else:
                    current.mkdir()
                    previous.mkdir()
                    (current / "marker").write_text("current", encoding="utf-8")
                    (previous / "marker").write_text("previous", encoding="utf-8")

            self.assertEqual(install_release.rollback(root), 0)
            for current, previous in pairs:
                if current.is_dir():
                    self.assertEqual((current / "marker").read_text(), "previous")
                    self.assertEqual((previous / "marker").read_text(), "current")
                else:
                    self.assertEqual(current.read_text(), "previous")
                    self.assertEqual(previous.read_text(), "current")

    def test_incomplete_release_exchange_restores_both_sides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / "current"
            previous = root / "previous"
            current.write_text("current", encoding="utf-8")
            previous.write_text("previous", encoding="utf-8")
            original_replace = Path.replace
            calls = 0

            def fail_final_exchange(path: Path, target: Path):
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise OSError("simulated final rename failure")
                return original_replace(path, target)

            with mock.patch.object(Path, "replace", fail_final_exchange):
                with self.assertRaises(OSError):
                    install_release.swap_with_previous(current, previous)
            self.assertEqual(current.read_text(encoding="utf-8"), "current")
            self.assertEqual(previous.read_text(encoding="utf-8"), "previous")

    def test_public_release_is_usable_without_github_credentials(self) -> None:
        self.assertIn("Verify public source distribution", RELEASE)
        self.assertIn("Verify anonymous public release", RELEASE)
        self.assertIn(".private == false", RELEASE)
        self.assertIn("docker logout ghcr.io", RELEASE)
        self.assertGreaterEqual(RELEASE.count("docker buildx imagetools inspect"), 2)
        self.assertIn("cosign verify-blob --bundle release-manifest.bundle", RELEASE)
        self.assertIn("verify_asset .sboms.source", RELEASE)
        self.assertIn('--certificate-identity "$identity"', RELEASE)
        self.assertNotIn("GH_TOKEN", BOOTSTRAP)
        self.assertNotIn("GITHUB_TOKEN", BOOTSTRAP)
        self.assertNotIn("Authorization", INSTALL_RELEASE)

    def test_safe_extract_rejects_traversal_and_unexpected_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                info = tarfile.TarInfo("../escape")
                info.size = 1
                output.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(RuntimeError):
                install_release.safe_extract(archive, root / "out", "operations")

            archive = root / "unexpected.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                info = tarfile.TarInfo("secrets/token")
                info.size = 1
                output.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(RuntimeError):
                install_release.safe_extract(archive, root / "out", "operations")

    def test_five_minute_verified_copy_is_the_default(self) -> None:
        self.assertIn("'),'5');", SCHEDULER)
        runtime = (ROOT / "backend/app/core/runtime_settings.py").read_text(encoding="utf-8")
        self.assertIn('"ha_replication_interval_minutes": {"default": 5', runtime)


if __name__ == "__main__":
    unittest.main()
