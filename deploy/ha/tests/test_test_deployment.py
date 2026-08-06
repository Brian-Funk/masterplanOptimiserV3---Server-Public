"""Unit coverage for exact-commit deployment planning."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "test_deployment", ROOT / "deploy" / "test_deployment.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
SUPERVISOR = (ROOT / "deploy" / "test-deployment.sh").read_text(encoding="utf-8")


class TestDeploymentPlannerTests(unittest.TestCase):
    def test_snapshot_free_apply_is_confined_to_fresh_root_only_commissioning(self) -> None:
        self.assertIn("--fresh-commissioning", SUPERVISOR)
        self.assertIn("require_fresh_commissioning_database", SUPERVISOR)
        self.assertIn('.state == "in_progress"', SUPERVISOR)
        self.assertIn('.format == "mp-opt-setup-state-v2"', SUPERVISOR)
        self.assertIn('.deployment_lane == "unsigned"', SUPERVISOR)
        self.assertIn('.campaign_commit == $target', SUPERVISOR)
        self.assertIn('.mode == "standalone-new"', SUPERVISOR)
        self.assertIn('.mode == "ha-primary-new"', SUPERVISOR)
        self.assertIn("NOT EXISTS (SELECT 1 FROM events)", SUPERVISOR)
        self.assertIn("NOT is_root_admin", SUPERVISOR)
        self.assertIn("count(*) FROM users WHERE is_root_admin) <= 1", SUPERVISOR)
        self.assertIn("root_commissioning_completed_at", SUPERVISOR)
        self.assertIn("root_recovery_download_acknowledged_at", SUPERVISOR)

    def test_fresh_commissioning_builds_every_exact_runtime_before_activation(self) -> None:
        self.assertIn('["backend","frontend","caddy","database","tools","operations"]', SUPERVISOR)
        for component in ("backend", "caddy", "database", "tools"):
            self.assertIn(f'set_apply_stage "build-${{component}}"', SUPERVISOR)
        self.assertLess(
            SUPERVISOR.index('set_apply_stage stop-old-backend'),
            SUPERVISOR.index('set_apply_stage migrations'),
        )
        self.assertIn('compose_activate "$components" "$fresh_commissioning"', SUPERVISOR)

    def test_failure_is_staged_and_previous_exact_receipt_is_recovered(self) -> None:
        self.assertIn("mp-opt-test-deployment-failure-v1", SUPERVISOR)
        self.assertIn("record_apply_failure", SUPERVISOR)
        self.assertIn("restore_verified_previous_deployment", SUPERVISOR)
        self.assertIn("mp_wait_for_health 45", SUPERVISOR)

    def test_local_tls_health_is_retried_before_recording_the_receipt(self) -> None:
        self.assertIn("for attempt in $(seq 1 30)", SUPERVISOR)
        self.assertIn('"https://${domain}/health" >/dev/null 2>&1', SUPERVISOR)
        self.assertIn('sleep 1', SUPERVISOR)
        self.assertIn("after 30 attempts", SUPERVISOR)

    def test_ha_peer_receives_same_commit_and_writes_a_matching_receipt(self) -> None:
        self.assertIn('internal-activate "$target" "$components" "$fresh_commissioning"', SUPERVISOR)
        self.assertIn("Node B did not record the exact pinned deployment receipt", SUPERVISOR)
        self.assertIn('write_state "$target" "" "$plan" ""', SUPERVISOR)
        peer_activate = SUPERVISOR.split("internal_activate()", 1)[1].split(
            "deploy_witness()", 1
        )[0]
        self.assertIn('.campaign_commit=$target', peer_activate)
        self.assertIn('.campaign_commit != $previous', peer_activate)

    def test_peer_reexecutes_installed_exact_operations_before_activation(self) -> None:
        peer_activate = SUPERVISOR.split("internal_activate()", 1)[1].split(
            "deploy_witness()", 1
        )[0]
        self.assertIn("MP_TEST_INTERNAL_ACTIVATE_REEXEC", peer_activate)
        self.assertIn('exec env MP_ROOT="$MP_ROOT"', peer_activate)
        self.assertLess(peer_activate.index("sync_operations"), peer_activate.index("exec env"))
        self.assertLess(peer_activate.index("exec env"), peer_activate.index("compose_activate"))
        self.assertIn("does not match the exact target after re-entry", peer_activate)

    def test_successful_exact_update_advances_setup_pin_after_pair_readiness(self) -> None:
        self.assertIn("advance_setup_campaign_pin()", SUPERVISOR)
        apply = SUPERVISOR.split("apply_commit()", 1)[1].split("restore_signed()", 1)[0]
        self.assertLess(
            apply.index("mp_ha_active_verification_readiness"),
            apply.rindex('advance_setup_campaign_pin "$target" "$previous"'),
        )
        self.assertIn(
            '.campaign_commit != $previous and .campaign_commit != $target',
            SUPERVISOR,
        )

    def test_initial_peer_is_prepared_before_replication_and_finalised_afterward(self) -> None:
        self.assertIn("prepare_initial_peer()", SUPERVISOR)
        self.assertIn("internal_prepare_peer()", SUPERVISOR)
        self.assertIn("internal_finalize_peer()", SUPERVISOR)
        prepare = SUPERVISOR.split("internal_prepare_peer()", 1)[1].split(
            "internal_repin_setup()", 1
        )[0]
        self.assertIn("mp_compose_validate", prepare)
        self.assertNotIn('up -d', prepare)
        self.assertIn("internal-repin-setup", SUPERVISOR)
        self.assertIn("internal-finalize-peer", SUPERVISOR)
        self.assertIn("peer_copy_image", SUPERVISOR)
        repin = SUPERVISOR.split("internal_repin_setup()", 1)[1].split(
            "internal_finalize_peer()", 1
        )[0]
        finalize = SUPERVISOR.split("internal_finalize_peer()", 1)[1].split(
            "internal_activate()", 1
        )[0]
        for operation in (repin, finalize):
            self.assertIn('.state == "complete"', operation)
            self.assertIn('index("application_deployed") != null', operation)
            self.assertIn("MP_TEST_STATE_FILE", operation)
            self.assertIn("return 0", operation)
        initial = SUPERVISOR.split("prepare_initial_peer()", 1)[1].split(
            "internal_prepare_peer()", 1
        )[0]
        self.assertIn('[ -d "$MP_ROOT/web/out" ]', initial)
        self.assertIn('tar -C "$MP_ROOT" -czf - web/out runtime/frontend-csp.caddy', initial)
        self.assertNotIn('tar -C "$MP_TEST_SOURCE" -czf - web/out', initial)

    def test_paired_pre_activation_updates_are_operations_only(self) -> None:
        apply = SUPERVISOR.split("apply_commit()", 1)[1].split("restore_signed()", 1)[0]
        self.assertIn("pre_activation_pair=true", apply)
        self.assertIn('index("replicated") == null', apply)
        self.assertIn("before initial HA replication", apply)
        self.assertIn(
            'advance_setup_campaign_pin "$target" "$previous" "Preparing exact images for Node B"',
            apply,
        )

    def test_pre_pairing_ha_update_stays_local(self) -> None:
        self.assertIn("ha_pairing_complete()", SUPERVISOR)
        self.assertIn("ha_pair_transport_ready()", SUPERVISOR)
        self.assertIn('if ha_pairing_complete; then', SUPERVISOR)
        self.assertIn('[ "$peer_ready" != true ] || peer_copy_image "$image"', SUPERVISOR)
        self.assertIn('if [ "$peer_ready" = true ]; then\n        peer_activate', SUPERVISOR)
        self.assertIn('pre_pairing=true', SUPERVISOR)
        self.assertIn('if [ "$pre_pairing" = true ]; then', SUPERVISOR)
        self.assertIn(
            "Complete HA pairing before applying an unsigned runtime-component update.",
            SUPERVISOR,
        )
        pre_pairing = SUPERVISOR.split('if [ "$pre_pairing" = true ]; then', 1)[1]
        self.assertLess(
            pre_pairing.index("mp_prepare_backend_secret_permissions"),
            pre_pairing.index('else\n        compose_activate'),
        )
        self.assertIn(
            "HA pairing is recorded, but the verified peer transport is unavailable.",
            SUPERVISOR,
        )

    def test_frontend_build_uses_host_resolved_exact_source_identity(self) -> None:
        common = (ROOT / "deploy/management/common.sh").read_text(encoding="utf-8")
        self.assertIn("mp_build_frontend_container", SUPERVISOR)
        self.assertIn("remote get-url origin", common)
        self.assertIn("rev-parse HEAD", common)
        self.assertIn("MP_PUBLIC_SOURCE_REPOSITORY_URL=$repository", common)
        self.assertIn("MP_PUBLIC_SOURCE_REVISION=$revision", common)
        self.assertIn('[[ "$revision" =~ ^[0-9a-f]{40}$ ]]', common)
        self.assertIn("stat -c '%u:%g'", common)
        self.assertIn('docker run --rm --user "$owner" -e HOME=/tmp', common)

    def test_standalone_test_deployment_never_publishes_ha_witness(self) -> None:
        self.assertIn(
            'if [ "$role" = dynamic ] && grep -qw witness <<< "$components"',
            SUPERVISOR,
        )

    def test_migration_snapshot_reuses_the_deployment_management_lock(self) -> None:
        self.assertIn(
            'MP_MANAGEMENT_LOCK_HELD=1 mp_snapshot_create full "test-deploy-${target:0:12}"',
            SUPERVISOR,
        )

    def test_terminal_dimensions_have_a_non_tty_fallback(self) -> None:
        common = (ROOT / "deploy/management/common.sh").read_text(encoding="utf-8")
        dimensions = common.split("mp_terminal_dimensions()", 1)[1].split("}", 1)[0]
        self.assertIn("if mp_has_terminal", dimensions)
        self.assertNotIn("</dev/tty", dimensions)

    def test_snapshot_anchor_uses_the_accessible_copied_ledger(self) -> None:
        snapshots = (ROOT / "deploy/management/snapshots.sh").read_text(encoding="utf-8")
        anchor = snapshots.split("mp_snapshot_write_evidence_anchor()", 1)[1].split(
            "mp_snapshot_normalise_payload_permissions()", 1,
        )[0]
        self.assertIn('$payload/evidence/ledger/chain-head.json', anchor)
        self.assertIn('sudo -n cat "$head"', anchor)

    def test_accepts_only_exact_lowercase_commits_and_canonical_tags(self) -> None:
        commit = "a" * 40
        self.assertEqual(MODULE.require_commit(commit), commit)
        self.assertEqual(MODULE.require_tag("v3.4.1"), "v3.4.1")
        for invalid in ("a" * 39, "A" * 40, "main", "origin/main"):
            with self.assertRaises(ValueError):
                MODULE.require_commit(invalid)
        for invalid in ("V3.4.1", "v.3.4.1", "3.4.1", "v3.4"):
            with self.assertRaises(ValueError):
                MODULE.require_tag(invalid)

    def test_backend_only_plan_stays_fast(self) -> None:
        plan = MODULE.plan_from_files("a" * 40, "b" * 40, ["backend/app/main.py"])
        self.assertEqual(plan.components, ("backend",))
        self.assertFalse(plan.full)
        self.assertFalse(plan.migrations)

    def test_frontend_and_database_plans_require_full_review(self) -> None:
        plan = MODULE.plan_from_files(
            "a" * 40,
            "b" * 40,
            ["web/src/app/page.tsx", "deploy/migrations/20990101_example.sql"],
        )
        self.assertEqual(plan.components, ("frontend", "database", "operations"))
        self.assertTrue(plan.full)
        self.assertTrue(plan.migrations)

    def test_unknown_paths_fail_closed_to_every_component(self) -> None:
        plan = MODULE.plan_from_files("a" * 40, "b" * 40, ["unexpected.file"])
        self.assertEqual(plan.components, MODULE.COMPONENTS)
        self.assertTrue(plan.full)

    def test_atomic_json_is_private_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "state" / "receipt.json"
            MODULE.atomic_json(destination, {"commit": "a" * 40})
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertIn('"commit"', destination.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
