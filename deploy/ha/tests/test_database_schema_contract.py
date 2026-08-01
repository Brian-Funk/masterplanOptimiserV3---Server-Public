from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
MODEL = (ROOT / "backend/app/models/ha.py").read_text(encoding="utf-8")
MIGRATION = (
    ROOT / "deploy/migrations/20260719_ha_cluster_state_contract.sql"
).read_text(encoding="utf-8")
COMMON = (ROOT / "deploy/management/common.sh").read_text(encoding="utf-8")
ACTIONS = (ROOT / "deploy/management/actions.sh").read_text(encoding="utf-8")
DEPLOY = (ROOT / "deploy/deploy.sh").read_text(encoding="utf-8")
POSTGRES_INTEGRATION = (
    ROOT / "deploy/ha/tests/postgres_schema_contract.py"
).read_text(encoding="utf-8")


def shell_function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    remainder = source[start:]
    next_function = remainder.find("\nmp_", len(name) + 5)
    return remainder if next_function < 0 else remainder[:next_function]


class DatabaseSchemaContractTests(unittest.TestCase):
    def test_orm_metadata_models_the_singleton_generation_contract(self) -> None:
        self.assertIn(
            'CheckConstraint("id = 1", name="ha_cluster_state_id_check")',
            MODEL,
        )
        self.assertIn(
            'CheckConstraint("generation >= 1", '
            'name="ha_cluster_state_generation_check")',
            MODEL,
        )
        self.assertIn(
            "id = Column(Integer, primary_key=True, autoincrement=False)",
            MODEL,
        )
        self.assertIn('server_default=text("false")', MODEL)
        self.assertIn('server_default=text("CURRENT_TIMESTAMP")', MODEL)

    def test_reconciliation_migration_is_idempotent_and_data_preserving(self) -> None:
        self.assertIn("WHERE id <> 1 OR generation < 1", MIGRATION)
        self.assertIn("ALTER COLUMN id DROP DEFAULT", MIGRATION)
        self.assertIn("DROP SEQUENCE IF EXISTS public.ha_cluster_state_id_seq", MIGRATION)
        self.assertIn("ALTER COLUMN maintenance SET DEFAULT FALSE", MIGRATION)
        self.assertIn("ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP", MIGRATION)
        self.assertIn("ADD CONSTRAINT ha_cluster_state_id_check", MIGRATION)
        self.assertIn("ADD CONSTRAINT ha_cluster_state_generation_check", MIGRATION)
        for mutation in ("DELETE FROM", "UPDATE ha_cluster_state", "INSERT INTO"):
            self.assertNotIn(mutation, MIGRATION)

    def test_runtime_validator_names_every_ha_safety_invariant(self) -> None:
        report = shell_function(COMMON, "mp_database_schema_contract_report")
        validator = shell_function(COMMON, "mp_verify_database_schema_contract")
        for invariant in (
            "ha_cluster_state.primary_key_is_id",
            "ha_cluster_state.id_has_no_default",
            "ha_cluster_state.id_sequence_absent",
            "ha_cluster_state.singleton_check",
            "ha_cluster_state.generation_check",
            "ha_cluster_state.maintenance_defaults_false",
            "ha_cluster_state.updated_at_defaults_to_current_time",
        ):
            self.assertIn(invariant, report)
        self.assertIn("PASS   %s", validator)
        self.assertIn("FAIL   %s", validator)

    def test_deploy_and_wipe_fail_closed_before_public_service_start(self) -> None:
        wipe = shell_function(ACTIONS, "mp_wipe_database")
        self.assertLess(
            wipe.index("mp_apply_migrations"),
            wipe.index("mp_verify_database_schema_contract"),
        )
        self.assertLess(
            wipe.index("mp_verify_database_schema_contract"),
            wipe.index("mp_recreate_backend"),
        )
        self.assertIn("mp_guard_rollback", wipe)

        migration = DEPLOY.index("if ! mp_apply_migrations")
        verification = DEPLOY.index("if ! mp_verify_database_schema_contract", migration)
        application_start = DEPLOY.index(
            '"${MP_COMPOSE[@]}" up -d --build --force-recreate --remove-orphans',
            verification,
        )
        self.assertLess(migration, verification)
        self.assertLess(verification, application_start)

    def test_recovery_evidence_contains_the_named_contract_report(self) -> None:
        evidence = shell_function(ACTIONS, "mp_collect_recovery_evidence")
        self.assertIn('schema-contract.tsv', evidence)
        self.assertIn('mp_database_schema_contract_report', evidence)
        self.assertIn("schema.sha256", evidence)

    def test_postgres_database_administration_uses_explicit_autocommit(self) -> None:
        self.assertIn("ISOLATION_LEVEL_AUTOCOMMIT", POSTGRES_INTEGRATION)
        self.assertEqual(
            POSTGRES_INTEGRATION.count(
                "connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)"
            ),
            2,
        )
        self.assertNotIn(
            "with psycopg2.connect(admin_dsn) as connection",
            POSTGRES_INTEGRATION,
        )


if __name__ == "__main__":
    unittest.main()
