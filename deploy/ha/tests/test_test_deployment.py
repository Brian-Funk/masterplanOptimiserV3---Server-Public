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
        self.assertIn('.mode == "standalone-new"', SUPERVISOR)
        self.assertIn('.mode == "ha-primary-new"', SUPERVISOR)
        self.assertIn("NOT EXISTS (SELECT 1 FROM events)", SUPERVISOR)
        self.assertIn("NOT is_root_admin", SUPERVISOR)
        self.assertIn("count(*) FROM webauthn_credentials) = 1", SUPERVISOR)

    def test_frontend_build_uses_host_resolved_exact_source_identity(self) -> None:
        common = (ROOT / "deploy/management/common.sh").read_text(encoding="utf-8")
        self.assertIn("mp_build_frontend_container", SUPERVISOR)
        self.assertIn("remote get-url origin", common)
        self.assertIn("rev-parse HEAD", common)
        self.assertIn("MP_PUBLIC_SOURCE_REPOSITORY_URL=$repository", common)
        self.assertIn("MP_PUBLIC_SOURCE_REVISION=$revision", common)
        self.assertIn('[[ "$revision" =~ ^[0-9a-f]{40}$ ]]', common)

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
