"""Portable public evidence bundle and Git staging tests."""

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "deploy" / "evidence"
sys.path.insert(0, str(EVIDENCE))

import evidence_bundle  # noqa: E402
import evidence_manifest  # noqa: E402


def _ledger(tmp_path: Path, *, with_processor_artifact: bool = False) -> Path:
    home = tmp_path / "evidence"
    private = tmp_path / "key"
    public = home / "public" / "instance_signing_key.pub"
    public.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private)],
        check=True,
    )
    public.write_text(private.with_suffix(".pub").read_text(encoding="ascii"), encoding="ascii")
    evidence_manifest.append_record(
        home / "ledger",
        instance_id="11111111-1111-4111-8111-111111111111",
        chain_id="22222222-2222-4222-8222-222222222222",
        record_type="instance.initialised",
        payload={"status": "initialised"},
        private_key=private,
        public_key=public,
    )
    if with_processor_artifact:
        processor_private = Ed25519PrivateKey.generate()
        processor_public = processor_private.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
        ).decode("ascii")
        processor_key_id = evidence_manifest.key_id(processor_public)
        fingerprint = hashlib.sha256(processor_public.encode("ascii")).hexdigest()
        document = {
            "format": "mp-opt-desktop-policy-acknowledgement-v1",
            "instance_id": "11111111-1111-4111-8111-111111111111",
            "event_ref": "33333333-3333-4333-8333-333333333333",
            "entity_id": "prc-synthetic0001",
            "key_id": processor_key_id,
            "role": "processor",
            "algorithm": "Ed25519",
            "public_key_sha256": fingerprint,
            "policy_version": 1,
            "policy_sha256": "a" * 64,
            "acknowledged_at": evidence_manifest.utc_now(),
        }
        canonical_document = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        signature = processor_private.sign(b"mp-opt-desktop-evidence-v1\0" + canonical_document)
        proof = {
            "format": "mp-opt-ed25519-signature-v1",
            "key_id": processor_key_id,
            "namespace": "mp-opt-desktop-evidence-v1",
            "signature": base64.b64encode(signature).decode("ascii"),
        }
        package = {
            "format": "mp-opt-signed-desktop-evidence-v1",
            "namespace": "mp-opt-desktop-evidence-v1",
            "document": document,
            "proof": proof,
            "public_key": processor_public,
        }
        raw = json.dumps(package, sort_keys=True, separators=(",", ":")).encode()
        package_digest = hashlib.sha256(raw).hexdigest()
        artifacts = home / "artifacts"
        artifacts.mkdir()
        (artifacts / f"{package_digest}.json").write_bytes(raw)
        evidence_manifest.append_record(
            home / "ledger",
            instance_id="11111111-1111-4111-8111-111111111111",
            chain_id="22222222-2222-4222-8222-222222222222",
            record_type="desktop.policy_acknowledged",
            payload={
                "event_ref": document["event_ref"],
                "entity_id": document["entity_id"],
                "key_id": processor_key_id,
                "policy_version": 1,
                "policy_sha256": document["policy_sha256"],
                "document_sha256": hashlib.sha256(canonical_document).hexdigest(),
                "signature_sha256": hashlib.sha256(
                    json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "evidence_package_sha256": package_digest,
                "public_key_sha256": fingerprint,
                "status": "verified",
            },
            private_key=private,
            public_key=public,
        )
    return home


def test_bundle_round_trip_and_idempotent_git_staging(tmp_path):
    home = _ledger(tmp_path)
    bundle = tmp_path / "evidence.bundle"

    created = evidence_bundle.create_bundle(home, bundle)
    verified = evidence_bundle.verify_bundle(bundle)
    first = evidence_bundle.stage_git(bundle, tmp_path / "archive")
    second = evidence_bundle.stage_git(bundle, tmp_path / "archive")

    assert created["bundle_sha256"] == evidence_bundle.sha256_file(bundle)
    assert verified["valid"] is True
    assert verified["record_count"] == 1
    assert first["status"] == "staged"
    assert second["status"] == "already_staged"


def test_complete_zip_is_deterministic_exact_and_verified(tmp_path):
    home = _ledger(tmp_path)
    first_bundle = tmp_path / "first.evidence"
    second_bundle = tmp_path / "second.evidence"
    first_zip = tmp_path / "first.zip"
    second_zip = tmp_path / "second.zip"

    evidence_bundle.create_bundle(home, first_bundle)
    evidence_bundle.create_bundle(home, second_bundle)
    evidence_bundle.create_evidence_zip(home, first_zip)
    evidence_bundle.create_evidence_zip(home, second_zip)

    assert first_bundle.read_bytes() == second_bundle.read_bytes()
    assert first_zip.read_bytes() == second_zip.read_bytes()
    result = evidence_bundle.verify_evidence_zip(first_zip)
    assert result["valid"] is True
    assert result["valid_zip"] is True
    with zipfile.ZipFile(first_zip) as archive:
        assert tuple(archive.namelist()) == evidence_bundle.ZIP_MEMBERS
        assert evidence_bundle.PUBLIC_VERIFIER_URL.encode("ascii") in archive.read("VERIFYING.txt")


def test_bundle_tampering_is_rejected(tmp_path):
    home = _ledger(tmp_path)
    bundle = tmp_path / "evidence.bundle"
    evidence_bundle.create_bundle(home, bundle)
    raw = bytearray(bundle.read_bytes())
    offset = raw.find(b"initialised")
    assert offset > 0
    raw[offset] ^= 1
    bundle.write_bytes(raw)

    try:
        evidence_bundle.verify_bundle(bundle)
    except (evidence_bundle.BundleError, evidence_manifest.EvidenceError):
        pass
    else:
        raise AssertionError("tampered evidence bundle was accepted")


def test_bundle_verifies_referenced_desktop_signature_and_rejects_tampering(tmp_path):
    home = _ledger(tmp_path, with_processor_artifact=True)
    bundle = tmp_path / "processor.evidence"
    evidence_bundle.create_bundle(home, bundle)
    assert evidence_bundle.verify_bundle(bundle)["processor_artifact_count"] == 1

    artifact = next((home / "artifacts").glob("*.json"))
    document = json.loads(artifact.read_text(encoding="utf-8"))
    document["document"]["policy_version"] = 2
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    artifact.write_bytes(raw)
    tampered = tmp_path / "tampered.evidence"
    try:
        evidence_bundle.create_bundle(home, tampered)
    except evidence_bundle.BundleError:
        pass
    else:
        raise AssertionError("tampered Desktop evidence artifact was accepted")
