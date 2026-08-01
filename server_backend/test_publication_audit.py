"""Tests for the fail-closed public-tree scanner."""

from pathlib import Path
import shutil
import subprocess

from deploy.security.publication_audit import (
    REQUIRED_PUBLIC_FILES,
    audit_history,
    audit_text,
    audit_tree,
    forbidden_path_reason,
    verify_scanner_fixture,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _fixture(root):
    _git(root, "init", "-q")
    for name in REQUIRED_PUBLIC_FILES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == "LICENSE":
            shutil.copyfile(PROJECT_ROOT / "LICENSE", target)
        else:
            target.write_text("public\n", encoding="utf-8")
    for relative in (
        "pyproject.toml",
        "web/package.json",
        "web/package-lock.json",
        "infra/cloudflare-ha-witness/package.json",
        "infra/cloudflare-ha-witness/package-lock.json",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT_ROOT / relative, target)
    legal = root / "web" / "legal-artifacts"
    legal.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / "LICENSE", legal / "LICENSE")
    shutil.copyfile(root / "THIRD-PARTY-NOTICES.md", legal / "THIRD-PARTY-NOTICES.md")
    _git(root, "add", ".")


def test_publication_audit_rejects_private_material(tmp_path):
    _fixture(tmp_path)
    secret = tmp_path / ".env"
    secret.write_text("SECRET_KEY=this-is-not-for-publication\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    failures = audit_tree(tmp_path)
    assert any("forbidden name: .env" in failure for failure in failures)


def test_publication_audit_accepts_controller_neutral_tree(tmp_path):
    _fixture(tmp_path)
    (tmp_path / ".env.example").write_text("SECRET_KEY=\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    assert audit_tree(tmp_path) == []


def test_publication_audit_rejects_untracked_publishable_secret(tmp_path):
    _fixture(tmp_path)
    (tmp_path / "accidental.pem").write_text("not a real key\n", encoding="utf-8")

    failures = audit_tree(tmp_path)

    assert "forbidden .pem artefact: accidental.pem" in failures


def test_history_audit_requires_clean_export_for_internal_notes(tmp_path):
    _fixture(tmp_path)
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "plan.md").write_text("internal\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "internal")
    (notes / "plan.md").unlink()
    _git(tmp_path, "add", "-u")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "remove")

    assert any("notes/plan.md" in failure for failure in audit_history(tmp_path))


def test_scanner_rejects_archives_and_sensitive_evidence_without_echoing_values():
    assert forbidden_path_reason("dist/release.zip") == "forbidden .zip artefact"
    secret = "-----BEGIN " + "PRIVATE KEY-----"
    assert audit_text("src/config.txt", secret) == ["secret-like private key: src/config.txt"]
    assert secret not in audit_text("src/config.txt", secret)[0]
    assert audit_text("evidence/records/1.json", '{"email":"person@example.org"}') == [
        "evidence contains email address: evidence/records/1.json"
    ]


def test_shared_phase_c_scanner_fixture(tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        """{
          "format": "masterplan-security-scanner-fixture-v1",
          "safe": [{"id":"safe-evidence","scope":"evidence","path":"evidence/records/1.json","content":"{\\"subject_ref\\":\\"sha256:synthetic\\"}"}],
          "unsafe": [
            {"id":"archive","scope":"artefact","path":"dist/export.zip","content":"","expected":"forbidden .zip artefact"},
            {"id":"history","scope":"history","path":"notes/private.md","content":"","expected":"forbidden historical path"},
            {"id":"pii","scope":"evidence","path":"evidence/records/2.json","content":"{\\"email\\":\\"person@example.org\\"}","expected":"evidence contains email address"}
          ]
        }""",
        encoding="utf-8",
    )
    assert verify_scanner_fixture(fixture) == []
