"""Portable public evidence bundle and Git staging tests."""

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "deploy" / "evidence"
sys.path.insert(0, str(EVIDENCE))

import evidence_bundle  # noqa: E402
import evidence_manifest  # noqa: E402


def _ledger(tmp_path: Path) -> Path:
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
