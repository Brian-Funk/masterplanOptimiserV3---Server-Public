"""Runtime consistency gates for signed accountability evidence."""

from pathlib import Path
import subprocess

import pytest

from app.core import evidence
from app.core.config import settings
from app.models.evidence import EvidenceOperation


def _runtime(tmp_path: Path, monkeypatch):
    private_key = tmp_path / "instance-evidence-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private_key)],
        check=True,
    )
    monkeypatch.setattr(settings, "EVIDENCE_MODE", "required")
    monkeypatch.setattr(settings, "EVIDENCE_HOME", str(tmp_path / "evidence"))
    monkeypatch.setattr(settings, "EVIDENCE_SIGNING_KEY_PATH", str(private_key))
    monkeypatch.setattr(
        settings,
        "EVIDENCE_TOOL_PATH",
        str(Path(__file__).resolve().parents[1] / "deploy" / "evidence" / "evidence_manifest.py"),
    )


def test_runtime_verification_requires_database_and_ledger_to_match(db, tmp_path, monkeypatch):
    _runtime(tmp_path, monkeypatch)
    state = evidence.initialise(db)
    assert state is not None
    db.commit()
    assert evidence.verify_local_chain(db)["records"] == 1

    db.query(EvidenceOperation).delete(synchronize_session=False)
    db.flush()
    with pytest.raises(evidence.EvidenceUnavailable, match="do not match"):
        evidence.verify_local_chain(db)


def test_existing_chain_rejects_a_different_private_key(db, tmp_path, monkeypatch):
    _runtime(tmp_path, monkeypatch)
    assert evidence.initialise(db) is not None
    db.commit()
    replacement = tmp_path / "replacement-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(replacement)],
        check=True,
    )
    monkeypatch.setattr(settings, "EVIDENCE_SIGNING_KEY_PATH", str(replacement))
    with pytest.raises(evidence.EvidenceUnavailable, match="startup verification"):
        evidence.initialise(db)


def test_required_read_only_verification_rejects_missing_chain(db, tmp_path, monkeypatch):
    _runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "EVIDENCE_MODE", "required")
    with pytest.raises(evidence.EvidenceUnavailable, match="has not been initialised"):
        evidence.verify_existing(db)


def test_required_initialisation_rejects_unavailable_storage(db, monkeypatch):
    monkeypatch.setattr(settings, "EVIDENCE_MODE", "required")

    def unavailable_storage() -> None:
        raise PermissionError("evidence storage is unavailable")

    monkeypatch.setattr(evidence, "ensure_directories", unavailable_storage)

    with pytest.raises(PermissionError, match="storage is unavailable"):
        evidence.initialise(db)


def test_non_holder_startup_verifies_evidence_before_early_return():
    source = (Path(__file__).resolve().parents[1] / "backend/app/main.py").read_text(
        encoding="utf-8"
    )
    branch = source[source.index("if is_ha_enabled() and not control_witness_ready():") :]
    branch = branch[: branch.index("# create_all")]
    assert "verify_existing(verification_db)" in branch
    assert branch.index("verify_existing(verification_db)") < branch.index("return")


def test_reconciliation_quarantines_signed_record_after_database_rollback(db, tmp_path, monkeypatch):
    _runtime(tmp_path, monkeypatch)
    state = evidence.initialise(db)
    assert state is not None
    db.commit()

    digest = evidence.append_record(
        db,
        workflow_type="instance",
        workflow_id=state.instance_id,
        operation_type="reconciliation_test",
        record_type="evidence.bundle_exported",
        payload={"status": "verified"},
        allow_missing_audit=True,
    )
    assert digest
    db.rollback()
    assert db.query(EvidenceOperation).count() == 1

    result = evidence.reconcile_local_chain(db)
    db.commit()
    assert result["records"] == 1
    assert db.query(EvidenceOperation).filter(
        EvidenceOperation.operation_type == "reconciliation_test",
    ).first() is None
    orphaned = list((tmp_path / "evidence/outbox/orphaned").glob("*/*.json"))
    assert len(orphaned) == 1
    assert evidence.verify_local_chain(db)["head_sha256"] != digest


def test_initialisation_recovers_if_ledger_won_the_crash_race(db, tmp_path, monkeypatch):
    _runtime(tmp_path, monkeypatch)
    original = evidence.initialise(db)
    assert original is not None
    instance_id = original.instance_id
    chain_id = original.chain_id
    db.rollback()

    recovered = evidence.initialise(db)
    db.commit()
    assert recovered is not None
    assert recovered.instance_id == instance_id
    assert recovered.chain_id == chain_id
    assert evidence.verify_local_chain(db)["records"] == 1
