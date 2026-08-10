"""Regression tests for schema-bound, minimised audit metadata."""
import ast
import json
from pathlib import Path

import pytest

from app.core.audit import AUDIT_ACTIONS, audit
from app.core import sessions
from app.models.audit import AuditLog
from server_backend.conftest import create_test_user


def test_audit_uses_pseudonymous_actor_and_canonical_json(db):
    user = create_test_user(db, username="audit.actor")

    entry = audit(
        db,
        user=user,
        action="auth.login",
        resource_type="user",
        resource_id=user.id,
        detail=json.dumps({"method": "passkey"}),
    )

    assert entry.username is None
    assert entry.actor_ref == user.evidence_subject_id
    assert json.loads(entry.detail) == {"method": "passkey", "schema_version": 1}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action": "made.up.action"},
        {"action": "auth.login", "resource_type": "made_up_resource"},
        {"action": "auth.login", "outcome": "maybe"},
        {"action": "auth.login", "detail": "free form"},
        {"action": "auth.login", "detail": json.dumps({"raw_token": "forbidden"})},
        {"action": "auth.login", "detail": json.dumps({"schema_version": 2})},
    ],
)
def test_audit_rejects_unbounded_or_unknown_metadata(db, kwargs):
    with pytest.raises(ValueError):
        audit(db, user=None, **kwargs)


def test_global_audit_log_is_root_only(db, admin_client, root_client):
    audit(db, user=None, action="auth.login")
    db.commit()

    denied = admin_client.get("/api/v1/admin/audit-log")
    allowed = root_client.get("/api/v1/admin/audit-log")

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert "username" not in allowed.text
    assert "actor_ref" in allowed.json()["entries"][0]


def test_new_audit_rows_do_not_persist_denormalised_username(db):
    user = create_test_user(db, username="no.audit.name")
    audit(db, user=user, action="auth.logout")
    db.commit()

    row = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    assert row.username is None


def test_audit_ip_hash_column_accepts_the_versioned_hmac(monkeypatch):
    """Production IP pseudonyms must fit both fresh and upgraded schemas."""
    monkeypatch.setattr(
        sessions.settings,
        "IP_HMAC_KEY",
        "audit-ip-width-regression-key-with-sufficient-entropy",
    )
    pseudonym = sessions._hash_ip("203.0.113.10")
    assert pseudonym is not None
    assert len(pseudonym) <= AuditLog.__table__.c.ip_hash.type.length

    root = Path(__file__).resolve().parents[1]
    migration = (
        root / "deploy" / "migrations" / "20260801_audit_ip_hash_width.sql"
    ).read_text(encoding="utf-8")
    assert "ALTER COLUMN ip_hash TYPE VARCHAR(80)" in migration


def test_literal_audit_actions_used_by_backend_are_in_the_fixed_vocabulary():
    backend = Path(__file__).resolve().parents[1] / "backend" / "app"
    literals = set()
    for source_path in backend.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8-sig"), filename=str(source_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "audit":
                continue
            for keyword in node.keywords:
                if (
                    keyword.arg == "action"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    literals.add(keyword.value.value)

    assert literals
    assert literals <= AUDIT_ACTIONS
