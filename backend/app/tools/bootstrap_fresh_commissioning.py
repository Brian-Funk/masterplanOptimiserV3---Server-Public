"""Initialise the narrow root/evidence state for a fresh deployment.

This command is intentionally separate from FastAPI startup.  The host-side
deployer exposes it only from a verified v2 fresh-commissioning checkpoint and
runs it with HA writes disabled inside the one-shot container.  The database
checks below independently refuse an existing application installation.
"""

from __future__ import annotations

import os
import re

from sqlalchemy import text

from app.core.config import settings
from app.core.evidence import initialise as initialise_evidence
from app.core.evidence import verify_existing
from app.core.security import create_default_admin
from app.db.database import SessionLocal
from app.tools.bootstrap_schema import load_model_registry


_MUST_BE_EMPTY = (
    "events",
    "published_tasks",
    "published_persons",
    "webauthn_credentials",
    "auth_sessions",
    "activation_links",
    "governance_publications",
    "deletion_cases",
    "privacy_action_receipts",
    "public_schedule_links",
    "ha_protection_operations",
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")


def _count(db, table: str) -> int:
    return int(db.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one())


def _assert_narrow_fresh_state(db) -> None:
    populated = [table for table in _MUST_BE_EMPTY if _count(db, table)]
    if populated:
        raise RuntimeError("Fresh commissioning refused a populated application database")

    roots = db.execute(
        text(
            "SELECT username, is_root_admin, is_admin, is_activated "
            "FROM users ORDER BY id"
        )
    ).all()
    if roots and roots != [("root.admin", True, True, True)]:
        raise RuntimeError("Fresh commissioning found an unexpected account state")

    evidence_states = _count(db, "evidence_chain_state")
    evidence_operations = _count(db, "evidence_operations")
    non_instance_keys = int(
        db.execute(
            text("SELECT count(*) FROM evidence_keys WHERE role <> 'instance'")
        ).scalar_one()
    )
    if evidence_states > 1 or evidence_operations > 1 or non_instance_keys:
        raise RuntimeError("Fresh commissioning found non-genesis evidence state")


def _initialise_ha_bootstrap_state(db) -> None:
    """Persist the initial writer generation before the public backend starts."""

    mode = os.getenv("MP_FRESH_DEPLOYMENT_MODE", "")
    if mode == "standalone-new":
        existing = db.execute(
            text(
                "SELECT cluster_id, generation, active_node_id, maintenance "
                "FROM ha_cluster_state WHERE id = 1"
            )
        ).one_or_none()
        if existing is not None:
            raise RuntimeError("Fresh standalone commissioning found HA ownership state")
        return
    if mode != "ha-primary-new":
        raise RuntimeError("Fresh commissioning deployment mode is invalid")

    cluster_id = os.getenv("MP_FRESH_HA_CLUSTER_ID", "")
    node_id = os.getenv("MP_FRESH_HA_NODE_ID", "")
    generation_text = os.getenv("MP_FRESH_HA_GENERATION", "")
    if not _IDENTIFIER.fullmatch(cluster_id):
        raise RuntimeError("Fresh HA commissioning cluster identity is invalid")
    if node_id != "node-a":
        raise RuntimeError("Fresh HA commissioning must initialise Node A")
    if generation_text != "1":
        raise RuntimeError("Fresh HA commissioning must initialise generation 1")

    expected = (cluster_id, 1, node_id, False)
    existing = db.execute(
        text(
            "SELECT cluster_id, generation, active_node_id, maintenance "
            "FROM ha_cluster_state WHERE id = 1 FOR UPDATE"
        )
    ).one_or_none()
    if existing is None:
        db.execute(
            text(
                "INSERT INTO ha_cluster_state "
                "(id, cluster_id, generation, active_node_id, maintenance) "
                "VALUES (1, :cluster_id, 1, :node_id, FALSE)"
            ),
            {"cluster_id": cluster_id, "node_id": node_id},
        )
        return
    if tuple(existing) != expected:
        raise RuntimeError("Fresh HA commissioning found conflicting ownership state")


def main() -> int:
    if os.getenv("MP_FRESH_COMMISSIONING") != "1":
        raise RuntimeError("Fresh commissioning acknowledgement is missing")
    if settings.HA_MODE != "standalone":
        raise RuntimeError("Fresh commissioning must run in its isolated unfenced container")

    load_model_registry()
    db = SessionLocal()
    try:
        _assert_narrow_fresh_state(db)
        create_default_admin(db)
        initialise_evidence(db)
        _initialise_ha_bootstrap_state(db)
        db.commit()
        verify_existing(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print("Fresh root bootstrap and evidence genesis are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
