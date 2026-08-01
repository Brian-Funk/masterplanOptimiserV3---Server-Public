"""Controller evidence Git template, verifier, scanner and viewer tests."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "deploy" / "evidence"
sys.path.insert(0, str(EVIDENCE))

import evidence_git  # noqa: E402
import evidence_manifest  # noqa: E402
import portable_bundle  # noqa: E402


INSTANCE_ID = "11111111-1111-4111-8111-111111111111"
CHAIN_ID = "22222222-2222-4222-8222-222222222222"
CONTROLLER_ID = "ctl-controller000001"
PROCESSOR_ID = "proc-processor0000001"


def _keypair(directory: Path, name: str) -> tuple[Path, Path]:
    private = directory / name
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private)],
        check=True,
    )
    return private, private.with_suffix(".pub")


def _write_signed(path: Path, value: dict, private: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(evidence_git.canonical_json(value))
    signature = Path(str(path) + ".sig")
    if signature.exists():
        signature.unlink()
    evidence_manifest.sign_file(path, private)


def _repository(tmp_path: Path, *, records: int = 3, processor: bool = True) -> tuple[Path, dict]:
    repository = tmp_path / "repository"
    evidence_git.export_template(ROOT, repository)
    controller_private, controller_public = _keypair(tmp_path, "controller-key")
    instance_private, instance_public = _keypair(tmp_path, "instance-key")
    controller_public_text = evidence_manifest.canonical_public_key(controller_public.read_text(encoding="ascii"))
    instance_public_text = evidence_manifest.canonical_public_key(instance_public.read_text(encoding="ascii"))
    (repository / "trust" / "processors").mkdir(parents=True, exist_ok=True)
    controller = {
        "format": evidence_git.CONTROLLER_FORMAT,
        "controller_id": CONTROLLER_ID,
        "display_name": "Example Controller Organisation",
        "jurisdiction": "CH",
        "signing_key_id": evidence_manifest.key_id(controller_public_text),
        "signing_public_key": controller_public_text,
        "revoked_key_ids": [],
        "status": "active",
        "signed_at": "2026-01-01T00:00:00Z",
    }
    (repository / "trust" / "controller.pub").write_text(controller_public_text + "\n", encoding="ascii")
    _write_signed(repository / "trust" / "controller.json", controller, controller_private)
    processor_ids: list[str] = []
    if processor:
        processor_value = {
            "format": evidence_git.PROCESSOR_FORMAT,
            "processor_id": PROCESSOR_ID,
            "controller_id": CONTROLLER_ID,
            "display_name": "Example Hosting Processor",
            "service_categories": ["hosting"],
            "countries": ["CH"],
            "transfer_basis": "adequacy",
            "active_from": "2026-01-01T00:00:00Z",
            "active_until": None,
            "status": "active",
            "signed_at": "2026-01-01T00:00:00Z",
        }
        _write_signed(repository / "trust" / "processors" / f"{PROCESSOR_ID}.json", processor_value, controller_private)
        processor_ids.append(PROCESSOR_ID)
    instance_directory = repository / "instances" / INSTANCE_ID
    instance = {
        "format": evidence_git.INSTANCE_FORMAT,
        "instance_id": INSTANCE_ID,
        "controller_id": CONTROLLER_ID,
        "signing_key_id": evidence_manifest.key_id(instance_public_text),
        "signing_public_key": instance_public_text,
        "processor_ids": processor_ids,
        "status": "active",
        "signed_at": "2026-01-01T00:00:00Z",
    }
    (instance_directory / "trust").mkdir(parents=True)
    (instance_directory / "trust" / "instance.pub").write_text(instance_public_text + "\n", encoding="ascii")
    _write_signed(instance_directory / "trust" / "instance.json", instance, controller_private)
    ledger = instance_directory / "ledger"
    record_types = ["instance.initialised", "data_subject.live_data_purged", "deletion.clean_backup_verified"]
    payloads = [
        {"status": "initialised"},
        {"status": "completed", "receipt_sha256": "a" * 64},
        {"status": "verified", "receipt_sha256": "b" * 64},
    ]
    for index in range(records):
        evidence_manifest.append_record(
            ledger,
            instance_id=INSTANCE_ID,
            chain_id=CHAIN_ID,
            record_type=record_types[index],
            payload=payloads[index],
            private_key=instance_private,
            public_key=instance_directory / "trust" / "instance.pub",
            created_at=f"2026-01-01T00:00:0{index}Z",
            record_id=f"33333333-3333-4333-8333-33333333333{index}",
        )
    for name in ("requests", "purges", "attestations", "backups", "anchors", "summaries"):
        (instance_directory / name).mkdir()
    return repository, {
        "controller_private": controller_private,
        "controller": controller,
        "instance_private": instance_private,
        "instance": instance,
    }


def _git_commit(repository: Path, message: str) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Evidence Test"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "evidence-test@invalid.example"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repository, check=True)


def test_valid_chain_renders_deterministic_accessible_markdown_and_html_and_exports_exact_tools(tmp_path):
    repository, _keys = _repository(tmp_path)
    result = evidence_git.verify_repository(repository)
    written = evidence_git.write_summaries(repository, result)
    first = {path.name: path.read_bytes() for path in written}
    evidence_git.write_summaries(repository)
    second = {path.name: path.read_bytes() for path in written}

    assert first == second
    assert result["chain_health"] == "verified"
    assert "Controller ID" in first["evidence-summary.md"].decode()
    rendered_html = first["evidence-summary.html"].decode()
    assert '<html lang="en-GB">' in rendered_html
    assert "<main>" in rendered_html and '<th scope="col">' in rendered_html
    assert "does not prove physical deletion" in rendered_html
    assert evidence_git.verify_repository(repository)["valid"] is True

    exported = tmp_path / "exported"
    evidence_git.export_template(ROOT, exported)
    assert (exported / "tools" / "evidence_git.py").read_bytes() == (EVIDENCE / "evidence_git.py").read_bytes()
    assert (exported / "tools" / "verify_evidence_repo.py").read_bytes() == (ROOT / "tools" / "verify_evidence_repo.py").read_bytes()
    workflow = (exported / ".github" / "workflows" / "verify-evidence.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow and "contents: read" in workflow


def test_tampered_record_and_missing_middle_record_fail_closed(tmp_path):
    repository, _keys = _repository(tmp_path / "tamper")
    record = sorted((repository / "instances" / INSTANCE_ID / "ledger").glob("*.json"))[1]
    raw = record.read_bytes()
    record.write_bytes(raw.replace(b'"completed"', b'"verified"'))
    with pytest.raises(evidence_git.EvidenceGitError, match="chain verification failed"):
        evidence_git.verify_repository(repository)

    repository, _keys = _repository(tmp_path / "missing")
    ledger = repository / "instances" / INSTANCE_ID / "ledger"
    middle = sorted(ledger.glob("*.json"))[1]
    middle.unlink()
    Path(str(middle) + ".sig").unlink()
    with pytest.raises(evidence_git.EvidenceGitError, match="chain verification failed"):
        evidence_git.verify_repository(repository)


def test_wrong_controller_processor_conflict_and_revoked_instance_key_fail(tmp_path):
    repository, keys = _repository(tmp_path / "wrong-controller")
    instance = dict(keys["instance"])
    instance["controller_id"] = "ctl-othercontrol0001"
    _write_signed(repository / "instances" / INSTANCE_ID / "trust" / "instance.json", instance, keys["controller_private"])
    with pytest.raises(evidence_git.EvidenceGitError, match="wrong controller"):
        evidence_git.verify_repository(repository)

    repository, keys = _repository(tmp_path / "conflict")
    original = repository / "trust" / "processors" / f"{PROCESSOR_ID}.json"
    duplicate = repository / "trust" / "processors" / "proc-zzzzzzzzzzzzzzzz.json"
    shutil.copyfile(original, duplicate)
    shutil.copyfile(Path(str(original) + ".sig"), Path(str(duplicate) + ".sig"))
    with pytest.raises(evidence_git.EvidenceGitError, match="Conflicting processor ID"):
        evidence_git.verify_repository(repository)

    repository, keys = _repository(tmp_path / "revoked")
    controller = dict(keys["controller"])
    controller["revoked_key_ids"] = [keys["instance"]["signing_key_id"]]
    _write_signed(repository / "trust" / "controller.json", controller, keys["controller_private"])
    with pytest.raises(evidence_git.EvidenceGitError, match="revoked or inactive"):
        evidence_git.verify_repository(repository)


def test_secret_private_key_personal_email_and_controller_history_change_are_rejected(tmp_path):
    repository, keys = _repository(tmp_path / "unsafe")
    leak = repository / "instances" / INSTANCE_ID / "requests" / "leak.txt"
    leak.write_text(
        "api_key=1234567890abcdef\nperson@invalid.example\n"
        + "-----BEGIN " + "PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    findings = evidence_git.scan_repository(repository)
    assert any(item.startswith("secret:") for item in findings)
    assert any(item.startswith("personal-email:") for item in findings)
    assert any(item.startswith("private-key:") for item in findings)
    with pytest.raises(evidence_git.EvidenceGitError, match="safety scan failed"):
        evidence_git.verify_repository(repository)

    repository, keys = _repository(tmp_path / "history", processor=False)
    _git_commit(repository, "Initial controller")
    controller = dict(keys["controller"])
    controller["controller_id"] = "ctl-replacement00001"
    _write_signed(repository / "trust" / "controller.json", controller, keys["controller_private"])
    instance = dict(keys["instance"])
    instance["controller_id"] = controller["controller_id"]
    _write_signed(repository / "instances" / INSTANCE_ID / "trust" / "instance.json", instance, keys["controller_private"])
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Changed controller"], cwd=repository, check=True)
    with pytest.raises(evidence_git.EvidenceGitError, match="Controller identity changed"):
        evidence_git.verify_repository(repository)


def test_portable_bundle_is_deterministic_self_contained_and_offline_verifiable(tmp_path):
    repository, _keys = _repository(tmp_path)
    first = tmp_path / "first.bundle"
    second = tmp_path / "second.bundle"
    created = portable_bundle.create_bundle(repository, INSTANCE_ID, first)
    portable_bundle.create_bundle(repository, INSTANCE_ID, second)

    assert first.read_bytes() == second.read_bytes()
    assert created == portable_bundle.verify_bundle(first) | {
        "path": str(first),
        "digest_path": str(Path(str(first) + ".sha256")),
    }
    assert portable_bundle.verify_bundle(
        first,
        expected_controller_id=CONTROLLER_ID,
        expected_instance_id=INSTANCE_ID,
    )["valid"] is True
    assert Path(str(first) + ".sha256").read_text(encoding="ascii") == (
        f"{portable_bundle.sha256_file(first)}  first.bundle\n"
    )


def test_portable_bundle_tamper_binding_and_path_traversal_fail_closed(tmp_path):
    repository, _keys = _repository(tmp_path / "source")
    bundle = tmp_path / "evidence.bundle"
    portable_bundle.create_bundle(repository, INSTANCE_ID, bundle)

    with pytest.raises(portable_bundle.PortableBundleError, match="controller"):
        portable_bundle.verify_bundle(bundle, expected_controller_id="ctl-othercontrol0001")
    with pytest.raises(portable_bundle.PortableBundleError, match="instance"):
        portable_bundle.verify_bundle(bundle, expected_instance_id="99999999-9999-4999-8999-999999999999")

    tampered = tmp_path / "tampered.bundle"
    raw = bytearray(bundle.read_bytes())
    offset = raw.find(b"Example Controller Organisation")
    assert offset > 0
    raw[offset] ^= 1
    tampered.write_bytes(raw)
    with pytest.raises((portable_bundle.PortableBundleError, evidence_git.EvidenceGitError)):
        portable_bundle.verify_bundle(tampered)

    traversal = tmp_path / "traversal.bundle"
    with __import__("tarfile").open(traversal, "w") as archive:
        info = __import__("tarfile").TarInfo("../outside")
        info.mode = 0o600
        info.size = 1
        archive.addfile(info, portable_bundle._BytesReader(b"x"))
    with pytest.raises(portable_bundle.PortableBundleError, match="unsafe"):
        portable_bundle.verify_bundle(traversal)
