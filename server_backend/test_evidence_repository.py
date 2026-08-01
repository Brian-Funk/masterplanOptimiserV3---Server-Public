"""Closed-schema private evidence repository and workstation-helper tests."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "deploy" / "evidence"
sys.path.insert(0, str(EVIDENCE))

import evidence_bundle  # noqa: E402
import evidence_manifest  # noqa: E402
import evidence_repo  # noqa: E402
import evidence_repository  # noqa: E402


def _keypair(tmp_path: Path, name: str = "instance") -> tuple[Path, Path]:
    private = tmp_path / name
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private)],
        check=True,
    )
    return private, private.with_suffix(".pub")


def _evidence_home(
    tmp_path: Path,
    *,
    instance_id: str = "11111111-1111-4111-8111-111111111111",
    chain_id: str = "22222222-2222-4222-8222-222222222222",
    name: str = "source",
) -> tuple[Path, Path, Path]:
    home = tmp_path / name
    private, generated_public = _keypair(tmp_path, f"{name}-key")
    public = home / "public" / "instance_signing_key.pub"
    public.parent.mkdir(parents=True)
    public.write_text(generated_public.read_text(encoding="ascii"), encoding="ascii")
    evidence_manifest.append_record(
        home / "ledger",
        instance_id=instance_id,
        chain_id=chain_id,
        record_type="instance.initialised",
        payload={"status": "initialised"},
        private_key=private,
        public_key=public,
    )
    return home, private, public


def _bundle(home: Path, destination: Path) -> dict:
    return evidence_bundle.create_bundle(home, destination)


def test_initialised_template_is_closed_and_ci_verifies_push_and_pull_request(tmp_path):
    archive = tmp_path / "archive"
    policy = evidence_repository.initialise_repository(
        archive,
        "33333333-3333-4333-8333-333333333333",
    )
    result = evidence_repository.verify_repository(archive)
    workflow = (archive / ".github" / "workflows" / "verify-evidence.yml").read_text(encoding="utf-8")

    assert policy["state"] == "initialising"
    assert result == {
        "valid": True,
        "repository_id": policy["repository_id"],
        "state": "initialising",
        "instances": 0,
        "bundles": 0,
    }
    assert "push:" in workflow
    assert "pull_request:" in workflow
    assert "evidence_repository.py verify --archive ." in workflow
    assert (archive / "scripts" / "evidence_repo.py").is_file()


def test_staged_complete_chain_round_trip_then_modified_truncated_and_missing_data_fail(tmp_path):
    archive = tmp_path / "archive"
    evidence_repository.initialise_repository(archive)
    home, private, public = _evidence_home(tmp_path)
    first_bundle = tmp_path / "first.bundle"
    first_summary = _bundle(home, first_bundle)
    evidence_repository.stage_bundle(first_bundle, archive)

    evidence_manifest.append_record(
        home / "ledger",
        instance_id=first_summary["instance_id"],
        chain_id=first_summary["chain_id"],
        record_type="trust_key.registered",
        payload={
            "key_id": "ek-0123456789abcdef",
            "role": "controller",
            "instance_id": first_summary["instance_id"],
            "entity_id": "ctl-synthetic-controller",
            "algorithm": "ed25519",
            "public_key_sha256": "a" * 64,
            "challenge_sha256": "b" * 64,
            "proof_sha256": "c" * 64,
            "status": "registered",
        },
        private_key=private,
        public_key=public,
    )
    second_bundle = tmp_path / "second.bundle"
    _bundle(home, second_bundle)
    evidence_repository.stage_bundle(second_bundle, archive)
    assert evidence_repository.verify_repository(archive)["bundles"] == 2

    staged = next(archive.glob("instances/*/bundles/*/evidence.bundle"))
    original = staged.read_bytes()
    staged.write_bytes(original[:-32])
    with pytest.raises((evidence_repository.RepositoryError, evidence_bundle.BundleError, OSError)):
        evidence_repository.verify_repository(archive)
    staged.write_bytes(original)

    receipt = staged.parent / "bundle.sha256"
    receipt.unlink()
    with pytest.raises((evidence_repository.RepositoryError, OSError)):
        evidence_repository.verify_repository(archive)


def test_undeclared_personal_or_private_data_and_forked_chain_are_rejected(tmp_path):
    archive = tmp_path / "archive"
    evidence_repository.initialise_repository(archive)
    private_key_marker = "-----BEGIN " + "PRIVATE KEY-----"
    (archive / "operator-notes.txt").write_text(
        f"alice@example.org\n{private_key_marker}\n",
        encoding="utf-8",
    )
    with pytest.raises(evidence_repository.RepositoryError, match="undeclared"):
        evidence_repository.verify_repository(archive)
    (archive / "operator-notes.txt").unlink()

    readme = archive / "README.md"
    readme.unlink()
    try:
        readme.symlink_to(Path(__file__))
    except OSError:
        pass
    else:
        with pytest.raises(evidence_repository.RepositoryError, match="symlinks"):
            evidence_repository.verify_repository(archive)
        readme.unlink()
    readme.write_text("restored test readme\n", encoding="utf-8")

    instance = "11111111-1111-4111-8111-111111111111"
    first, _private, _public = _evidence_home(tmp_path, instance_id=instance, name="chain-one")
    second, _other_private, _other_public = _evidence_home(
        tmp_path,
        instance_id=instance,
        chain_id="44444444-4444-4444-8444-444444444444",
        name="chain-two",
    )
    one = tmp_path / "one.bundle"
    two = tmp_path / "two.bundle"
    _bundle(first, one)
    _bundle(second, two)
    evidence_repository.stage_bundle(one, archive)
    with pytest.raises(evidence_repository.RepositoryError, match="forked"):
        evidence_repository.stage_bundle(two, archive)


def test_anchor_binds_exact_git_commit_bundle_head_repository_and_authorised_role(tmp_path):
    archive = tmp_path / "archive"
    evidence_repository.initialise_repository(
        archive,
        "33333333-3333-4333-8333-333333333333",
    )
    home, _private, _public = _evidence_home(tmp_path)
    bundle = tmp_path / "evidence.bundle"
    summary = _bundle(home, bundle)
    evidence_repository.stage_bundle(bundle, archive)
    output = tmp_path / "anchor.json"
    document = evidence_repository.build_anchor_document(
        archive,
        instance_id=summary["instance_id"],
        bundle_id=summary["bundle_id"],
        git_commit_sha="f" * 40,
        controller_key_id="ek-0123456789abcdef",
        output=output,
    )

    assert output.read_bytes() == evidence_repository.canonical_json(document)
    assert document["repository_id"] == "33333333-3333-4333-8333-333333333333"
    assert document["git_commit_sha"] == "f" * 40
    assert document["bundle_sha256"] == evidence_bundle.sha256_file(bundle)
    assert document["chain_head_sha256"] == summary["chain_head_sha256"]

    with pytest.raises(evidence_repository.RepositoryError, match="controller key identity"):
        evidence_repository.build_anchor_document(
            archive,
            instance_id=summary["instance_id"],
            bundle_id=summary["bundle_id"],
            git_commit_sha="e" * 40,
            controller_key_id="not-a-controller-key",
            output=tmp_path / "bad-anchor.json",
        )


def test_workstation_helper_refuses_non_root_and_dirty_import_and_never_embeds_credentials(tmp_path):
    archive = tmp_path / "archive"
    evidence_repo.initialise(archive)
    nested = archive / "nested"
    nested.mkdir()
    with pytest.raises(evidence_repo.WorkstationError, match="root"):
        evidence_repo._repository_root(nested)

    home, _private, _public = _evidence_home(tmp_path)
    bundle = tmp_path / "evidence.bundle"
    _bundle(home, bundle)
    with pytest.raises(evidence_repo.WorkstationError, match="uncommitted"):
        evidence_repo.import_bundle(bundle, archive)

    source = (EVIDENCE / "evidence_repo.py").read_text(encoding="utf-8")
    assert '"commit", "-S"' in source
    assert '"push", "--porcelain"' in source
    assert "password" not in source.lower()
    assert "token" not in source.lower()
