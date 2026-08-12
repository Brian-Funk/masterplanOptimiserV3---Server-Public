"""Tests for durable hybrid-HA mutation protection state."""

import json

from app.core import ha_replication
from app.core.config import settings
from app.models.ha import HAProtectionOperation


def test_operation_marker_is_transactional_idempotent_and_reconcilable(
    db, monkeypatch, tmp_path,
):
    requests = tmp_path / "ha-requests"
    results = tmp_path / "ha-operation-results"
    calls = []
    monkeypatch.setattr(settings, "HA_MODE", "ha")
    monkeypatch.setattr(settings, "HA_CLUSTER_ID", "cluster-test")
    monkeypatch.setattr(settings, "HA_NODE_ID", "node-a")
    monkeypatch.setattr(settings, "HA_REPLICATION_REQUEST_DIR", str(requests))
    requests.mkdir()
    monkeypatch.setattr(
        ha_replication,
        "witness_post",
        lambda path, body: calls.append((path, body)) or {"ok": True},
    )

    operation = ha_replication.create_protection_operation(
        db,
        idempotency_key="test-operation-00000001",
        operation_type="publisher-secret-create",
        resource_type="event",
        resource_id="12",
    )
    assert operation is not None
    assert operation.mutation_sequence == 1
    db.commit()

    repeated = ha_replication.create_protection_operation(
        db,
        idempotency_key="test-operation-00000001",
        operation_type="publisher-secret-create",
        resource_type="event",
        resource_id="12",
    )
    assert repeated is not None
    assert repeated.id == operation.id
    assert len(calls) == 1

    assert ha_replication.queue_protection_operation(operation) is None
    queued = json.loads((requests / f"{operation.id}.json").read_text(encoding="utf-8"))
    assert queued["format"] == "mp-opt-replication-request-v2"
    assert queued["operation"]["operation_id"] == operation.id
    assert queued["operation"]["mutation_sequence"] == 1

    results.mkdir()
    (results / f"{operation.id}.json").write_text(
        json.dumps({
            "format": "mp-opt-ha-operation-result-v1",
            "operation_id": operation.id,
            "mutation_sequence": 1,
            "state": "accepted",
            "stage": "accepted",
            "bundle_id": "bundle-1",
            "bundle_sha256": "a" * 64,
            "generation": 4,
            "error_code": None,
            "updated_at": "2026-08-06T12:00:00+00:00",
            "accepted_at": "2026-08-06T12:00:00+00:00",
        }),
        encoding="utf-8",
    )
    assert ha_replication.sync_protection_operation(db, operation)
    db.commit()
    stored = db.get(HAProtectionOperation, operation.id)
    assert stored.state == "accepted"
    assert stored.accepted_bundle_id == "bundle-1"
    assert stored.accepted_generation == 4


def test_result_receipt_must_match_the_exact_database_marker(db, monkeypatch, tmp_path):
    requests = tmp_path / "ha-requests"
    results = tmp_path / "ha-operation-results"
    monkeypatch.setattr(settings, "HA_MODE", "ha")
    monkeypatch.setattr(settings, "HA_CLUSTER_ID", "cluster-test")
    monkeypatch.setattr(settings, "HA_NODE_ID", "node-a")
    monkeypatch.setattr(settings, "HA_REPLICATION_REQUEST_DIR", str(requests))
    requests.mkdir()
    monkeypatch.setattr(ha_replication, "witness_post", lambda *_args, **_kwargs: {})
    operation = ha_replication.create_protection_operation(
        db,
        idempotency_key="test-operation-00000002",
        operation_type="publisher-secret-rotation",
        resource_type="event",
        resource_id="14",
    )
    db.commit()
    results.mkdir()
    (results / f"{operation.id}.json").write_text(
        json.dumps({
            "format": "mp-opt-ha-operation-result-v1",
            "operation_id": operation.id,
            "mutation_sequence": 999,
            "state": "accepted",
            "stage": "accepted",
            "bundle_id": "substituted",
            "bundle_sha256": "b" * 64,
            "generation": 4,
            "error_code": None,
            "updated_at": "2026-08-06T12:00:00+00:00",
            "accepted_at": "2026-08-06T12:00:00+00:00",
        }),
        encoding="utf-8",
    )
    ha_replication.sync_protection_operation(db, operation)
    assert operation.state == "pending"


def test_queue_preflight_returns_bounded_permission_errors(monkeypatch, tmp_path):
    requests = tmp_path / "ha-requests"
    monkeypatch.setattr(settings, "HA_MODE", "ha")
    monkeypatch.setattr(settings, "HA_REPLICATION_REQUEST_DIR", str(requests))

    assert ha_replication.protection_queue_error() == "replication_queue_missing"
    requests.mkdir()
    monkeypatch.setattr(ha_replication.os, "access", lambda *_args: False)
    assert ha_replication.protection_queue_error() == "replication_queue_not_writable"


def test_queue_atomic_write_failure_has_a_bounded_error(db, monkeypatch, tmp_path):
    requests = tmp_path / "ha-requests"
    requests.mkdir()
    monkeypatch.setattr(settings, "HA_MODE", "ha")
    monkeypatch.setattr(settings, "HA_CLUSTER_ID", "cluster-test")
    monkeypatch.setattr(settings, "HA_NODE_ID", "node-a")
    monkeypatch.setattr(settings, "HA_REPLICATION_REQUEST_DIR", str(requests))
    monkeypatch.setattr(ha_replication, "witness_post", lambda *_args, **_kwargs: {})
    operation = ha_replication.create_protection_operation(
        db,
        idempotency_key="test-operation-atomic-failure",
        operation_type="publisher-secret-create",
        resource_type="event",
        resource_id="15",
    )
    db.commit()
    monkeypatch.setattr(ha_replication.Path, "replace", lambda *_args: (_ for _ in ()).throw(OSError()))

    assert (
        ha_replication.queue_protection_operation(operation)
        == "replication_queue_atomic_write_failed"
    )
    assert not (requests / f"{operation.id}.json").exists()
