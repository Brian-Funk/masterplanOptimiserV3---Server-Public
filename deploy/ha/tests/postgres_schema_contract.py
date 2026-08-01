"""PostgreSQL integration proof for the HA singleton schema contract."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = (
    ROOT / "deploy/migrations/20260719_ha_cluster_state_contract.sql"
).read_text(encoding="utf-8")
DATABASES = (
    "mp_contract_model",
    "mp_contract_upgraded",
    "mp_contract_drifted",
)


def database_dsn(name: str) -> str:
    return make_url(os.environ["DATABASE_URL"]).set(database=name).render_as_string(
        hide_password=False
    )


def recreate_databases() -> None:
    admin_dsn = make_url(os.environ["DATABASE_URL"]).render_as_string(
        hide_password=False
    )
    connection = psycopg2.connect(admin_dsn)
    try:
        # CREATE/DROP DATABASE are forbidden in transaction blocks. Do not use
        # psycopg2's connection context manager for these administrative calls:
        # the context establishes transactional semantics even when autocommit
        # is assigned inside it.
        connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with connection.cursor() as cursor:
            for name in DATABASES:
                cursor.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(name)
                    )
                )
                cursor.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
                )
    finally:
        connection.close()


def drop_databases() -> None:
    admin_dsn = make_url(os.environ["DATABASE_URL"]).render_as_string(
        hide_password=False
    )
    connection = psycopg2.connect(admin_dsn)
    try:
        connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with connection.cursor() as cursor:
            for name in DATABASES:
                cursor.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(name)
                    )
                )
    finally:
        connection.close()


def create_model_schema() -> None:
    # Import only after DATABASE_URL is provided by the isolated CI database.
    from app.models.ha import HAClusterState

    engine = create_engine(database_dsn("mp_contract_model"))
    try:
        HAClusterState.__table__.create(engine)
    finally:
        engine.dispose()


def create_upgraded_schema() -> None:
    statement = """
        CREATE TABLE ha_cluster_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cluster_id VARCHAR(128) NOT NULL,
            generation BIGINT NOT NULL CHECK (generation >= 1),
            active_node_id VARCHAR(128) NOT NULL,
            maintenance BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """
    with psycopg2.connect(database_dsn("mp_contract_upgraded")) as connection:
        with connection.cursor() as cursor:
            cursor.execute(statement)
            cursor.execute(
                """
                INSERT INTO ha_cluster_state (
                    id, cluster_id, generation, active_node_id
                ) VALUES (1, 'cluster-test', 7, 'node-a')
                """
            )


def create_drifted_schema() -> None:
    statement = """
        CREATE TABLE ha_cluster_state (
            id SERIAL PRIMARY KEY,
            cluster_id VARCHAR(128) NOT NULL,
            generation BIGINT NOT NULL,
            active_node_id VARCHAR(128) NOT NULL,
            maintenance BOOLEAN NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """
    with psycopg2.connect(database_dsn("mp_contract_drifted")) as connection:
        with connection.cursor() as cursor:
            cursor.execute(statement)
            cursor.execute(
                """
                INSERT INTO ha_cluster_state (
                    id, cluster_id, generation, active_node_id, maintenance
                ) VALUES (1, 'cluster-test', 7, 'node-a', FALSE)
                """
            )


def apply_twice_and_verify(name: str) -> None:
    dsn = database_dsn(name)
    with psycopg2.connect(dsn) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(MIGRATION)
            cursor.execute(MIGRATION)
            cursor.execute(
                """
                SELECT column_default
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ha_cluster_state'
                  AND column_name = 'id'
                """
            )
            assert cursor.fetchone() == (None,)
            cursor.execute(
                "SELECT to_regclass('public.ha_cluster_state_id_seq') IS NULL"
            )
            assert cursor.fetchone() == (True,)
            cursor.execute(
                """
                SELECT lower(column_default)
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ha_cluster_state'
                  AND column_name = 'maintenance'
                """
            )
            assert cursor.fetchone()[0] in {"false", "false::boolean"}
            cursor.execute(
                """
                SELECT regexp_replace(
                           pg_get_expr(conbin, conrelid), '[[:space:]]', '', 'g'
                       )
                FROM pg_constraint
                WHERE conrelid = 'public.ha_cluster_state'::regclass
                  AND contype = 'c'
                ORDER BY 1
                """
            )
            expressions = {row[0] for row in cursor.fetchall()}
            assert expressions.intersection({"(id=1)", "id=1"})
            assert expressions.intersection({"(generation>=1)", "generation>=1"})
            cursor.execute("SELECT count(*) FROM ha_cluster_state")
            if cursor.fetchone() == (0,):
                cursor.execute(
                    """
                    INSERT INTO ha_cluster_state (
                        id, cluster_id, generation, active_node_id
                    ) VALUES (1, 'cluster-test', 7, 'node-a')
                    """
                )
            cursor.execute(
                """
                SELECT id, cluster_id, generation, active_node_id, maintenance
                FROM ha_cluster_state
                """
            )
            assert cursor.fetchone() == (1, "cluster-test", 7, "node-a", False)

            try:
                cursor.execute(
                    """
                    INSERT INTO ha_cluster_state (
                        id, cluster_id, generation, active_node_id
                    ) VALUES (2, 'invalid', 7, 'invalid')
                    """
                )
            except psycopg2.errors.CheckViolation:
                pass
            else:
                raise AssertionError(f"{name} accepted a second singleton row")

            try:
                cursor.execute(
                    "UPDATE ha_cluster_state SET generation = 0 WHERE id = 1"
                )
            except psycopg2.errors.CheckViolation:
                pass
            else:
                raise AssertionError(f"{name} accepted generation zero")


def main() -> None:
    os.environ.setdefault("ENVIRONMENT", "development")
    recreate_databases()
    try:
        create_model_schema()
        create_upgraded_schema()
        create_drifted_schema()
        for name in DATABASES:
            apply_twice_and_verify(name)
            print(f"PASS: {name}")
    finally:
        drop_databases()


if __name__ == "__main__":
    main()
