"""Focused tests for the integrated, fine-grained-token Evidence Git uploader."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import logging
import shutil
import subprocess
import sys
from urllib.error import HTTPError

import pytest


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "deploy" / "evidence"
sys.path.insert(0, str(EVIDENCE))

import evidence_archive_repository  # noqa: E402
import evidence_git  # noqa: E402
import evidence_git_uploader  # noqa: E402
import github_token_client  # noqa: E402


HEAD = "a" * 40
MERGE = "b" * 40
BUNDLE_SHA = "c" * 64
CHAIN_HEAD = "d" * 64
INSTANCE_ID = "11111111-1111-4111-8111-111111111111"
BUNDLE_ID = "22222222-2222-4222-8222-222222222222"


def synthetic_token(label: str = "synthetic") -> str:
    return "github_" + "pat_" + label + "_0123456789"


def write_token(path: Path, label: str = "synthetic") -> str:
    value = synthetic_token(label)
    path.write_text(value, encoding="ascii")
    path.chmod(0o600)
    return value


class Response:
    def __init__(self, document, *, status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self.raw = json.dumps(document).encode() if document is not None else b""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.raw


def configuration(token: Path, **overrides):
    values = {
        "api_base_url": "https://api.github.test",
        "owner": "Brian-Funk",
        "repository": "MasterplanOptimiserV3---Evidence",
        "repository_id": "42",
        "default_branch": "main",
        "token_path": token,
    }
    values.update(overrides)
    return github_token_client.GitHubTokenConfiguration(**values)


def test_fine_grained_token_readiness_is_private_bound_and_never_disclosed(tmp_path, monkeypatch, caplog):
    token = tmp_path / "token"
    secret = write_token(token, "NEVER_DISCLOSE")

    def fake_urlopen(request, timeout):
        assert timeout == 30
        assert secret not in request.full_url
        url = request.full_url
        if url.endswith("/repos/Brian-Funk/MasterplanOptimiserV3---Evidence"):
            return Response({"id": 42, "private": True, "fork": False,
                             "full_name": "Brian-Funk/MasterplanOptimiserV3---Evidence",
                             "default_branch": "main"}, headers={"GitHub-Authentication-Token-Expiration": "2027-01-01"})
        if url.endswith("/git/ref/heads/main"):
            return Response({"object": {"sha": HEAD}})
        if url.endswith("/branches/main"):
            return Response({"protected": True})
        raise AssertionError(url)

    monkeypatch.setattr(github_token_client, "urlopen", fake_urlopen)
    with caplog.at_level(logging.DEBUG):
        result = github_token_client.GitHubTokenClient(configuration(token)).readiness()
    assert result["ready"] is True
    assert result["repository_id"] == "42"
    assert result["protected"] is True
    assert secret not in caplog.text
    assert secret not in json.dumps(result)


@pytest.mark.parametrize("document,reason", [
    ({"id": 42, "private": False, "fork": False, "full_name": "Brian-Funk/MasterplanOptimiserV3---Evidence", "default_branch": "main"}, "repository_not_private"),
    ({"id": 42, "private": True, "fork": True, "full_name": "Brian-Funk/MasterplanOptimiserV3---Evidence", "default_branch": "main"}, "public_or_fork_repository_forbidden"),
    ({"id": 99, "private": True, "fork": False, "full_name": "Brian-Funk/Elsewhere", "default_branch": "main"}, "repository_identity_mismatch"),
])
def test_token_rejects_public_fork_and_wrong_repository(tmp_path, monkeypatch, document, reason):
    token = tmp_path / "token"
    write_token(token)
    monkeypatch.setattr(github_token_client, "urlopen", lambda *_args, **_kwargs: Response(document))
    with pytest.raises(github_token_client.GitHubArchiveError, match=reason):
        github_token_client.GitHubTokenClient(configuration(token)).repository()


def test_invalid_expired_and_insufficient_tokens_have_bounded_tracebacks(tmp_path, monkeypatch):
    token = tmp_path / "token"
    secret = write_token(token, "TRACEBACK_PROBE")
    for status, reason in ((401, "invalid_or_expired_token"), (403, "insufficient_token_permissions")):
        def fail(request, timeout, status=status):
            raise HTTPError(request.full_url, status, secret, {}, None)
        monkeypatch.setattr(github_token_client, "urlopen", fail)
        with pytest.raises(github_token_client.GitHubArchiveError) as raised:
            github_token_client.GitHubTokenClient(configuration(token)).repository()
        assert raised.value.reason_code == reason
        assert secret not in str(raised.value)


def test_evidence_public_is_never_an_automatic_target(tmp_path):
    token = tmp_path / "token"
    write_token(token)
    client = github_token_client.GitHubTokenClient(configuration(
        token, repository="MasterplanOptimiserV3---Evidence-Public",
    ))
    with pytest.raises(github_token_client.GitHubArchiveError, match="evidence_public_forbidden"):
        client.repository()


def test_check_monitoring_requires_both_named_checks_at_the_exact_head(tmp_path, monkeypatch):
    token = tmp_path / "token"
    write_token(token)
    client = github_token_client.GitHubTokenClient(configuration(token))
    documents = {
        "check-runs": {"check_runs": [
            {"name": "Evidence verification", "head_sha": HEAD, "status": "completed", "conclusion": "success"},
        ]},
        "status": {"state": "success"},
    }
    monkeypatch.setattr(client, "_request", lambda _method, path: documents[path.rsplit("/", 1)[1]])
    assert client.check_state(HEAD) == "pending"
    documents["check-runs"]["check_runs"].append(
        {"name": "Ingestion path validation", "head_sha": HEAD, "status": "completed", "conclusion": "success"},
    )
    assert client.check_state(HEAD) == "passed"
    documents["check-runs"]["check_runs"][1]["head_sha"] = MERGE
    with pytest.raises(github_token_client.GitHubArchiveError, match="check_sha_mismatch"):
        client.check_state(HEAD)


def test_client_reuses_only_an_exact_existing_archive_branch_and_pull_request(tmp_path, monkeypatch):
    token = tmp_path / "token"
    write_token(token)
    client = github_token_client.GitHubTokenClient(configuration(token))
    files = {
        f"instances/{INSTANCE_ID}/bundles/{BUNDLE_ID}/evidence.bundle": b"bundle",
        f"instances/{INSTANCE_ID}/bundles/{BUNDLE_ID}/bundle.sha256": b"digest",
    }

    def exact(_method, path, **kwargs):
        if "/git/ref/heads/" in path:
            return {"object": {"sha": HEAD}}
        if "/compare/" in path:
            return {
                "ahead_by": 1,
                "behind_by": 0,
                "files": [
                    {
                        "filename": name,
                        "status": "added",
                        "sha": client._git_blob_sha(content),
                    }
                    for name, content in files.items()
                ],
            }
        if "/pulls?" in path:
            assert kwargs.get("allow_list") is True
            return [{
                "number": 7,
                "head": {"sha": HEAD},
                "base": {"ref": "main"},
            }]
        raise AssertionError(path)

    monkeypatch.setattr(client, "_request", exact)
    assert client.create_archive_commit(branch="archive/instance/bundle", base_sha=MERGE, files=files) == HEAD
    assert client.open_pull_request(branch="archive/instance/bundle", head_sha=HEAD) == 7

    def conflicting(_method, path, **_kwargs):
        if "/git/ref/heads/" in path:
            return {"object": {"sha": HEAD}}
        if "/compare/" in path:
            return {"ahead_by": 2, "behind_by": 0, "files": []}
        raise AssertionError(path)

    monkeypatch.setattr(client, "_request", conflicting)
    with pytest.raises(github_token_client.GitHubArchiveError, match="automatic_branch_conflict"):
        client.create_archive_commit(branch="archive/instance/bundle", base_sha=MERGE, files=files)


def row(bundle: Path) -> SimpleNamespace:
    return SimpleNamespace(
        state="pending", failure_reason=None, next_attempt_at=None,
        lease_owner="worker", lease_expires_at=None, attempt_count=0,
        bundle_sha256=BUNDLE_SHA, bundle_id=BUNDLE_ID, bundle_path=str(bundle),
        chain_head_sha256=CHAIN_HEAD, controller_id="ctl-controller000001",
        instance_id=INSTANCE_ID, repository_id="42", branch_name=None,
        base_sha=None, pull_request_number=None, pull_request_head_sha=None,
        merge_commit_sha=None, checks_started_at=None, completed_at=None,
    )


class Provider:
    def __init__(self):
        self.check = "pending"
        self.deleted = []

    def readiness(self):
        return {"repository_id": "42", "private": True, "default_head_sha": HEAD}

    def default_head(self):
        return HEAD

    def create_archive_commit(self, *, branch, base_sha, files):
        assert base_sha == HEAD
        assert sorted(path.rsplit("/", 1)[1] for path in files) == ["bundle.sha256", "evidence.bundle"]
        return HEAD

    def open_pull_request(self, *, branch, head_sha):
        assert head_sha == HEAD
        return 7

    def pull_request_head(self, number):
        assert number == 7
        return HEAD

    def check_state(self, head_sha):
        assert head_sha == HEAD
        return self.check

    def merge_pull_request(self, *, number, expected_head_sha):
        assert number == 7 and expected_head_sha == HEAD
        return MERGE

    def delete_branch(self, branch):
        self.deleted.append(branch)


def test_durable_uploader_verifies_bundle_monitors_exact_sha_merges_and_cleans_branch(tmp_path, monkeypatch):
    bundle = tmp_path / "evidence.bundle"
    bundle.write_bytes(b"synthetic")
    monkeypatch.setattr(evidence_git_uploader.portable_bundle, "sha256_file", lambda _path: BUNDLE_SHA)
    queued = row(bundle)
    provider = Provider()
    policy = evidence_git_uploader.UploaderPolicy(True, "42", "ctl-controller000001", INSTANCE_ID)
    verifier = lambda *_args, **_kwargs: {
        "bundle_sha256": BUNDLE_SHA, "bundle_id": BUNDLE_ID,
        "chain_head_sha256": CHAIN_HEAD, "record_sha256s": [CHAIN_HEAD],
    }
    now = datetime(2026, 7, 31, tzinfo=timezone.utc)
    assert evidence_git_uploader.advance_submission(queued, policy=policy, client=provider, now=now, verifier=verifier) == "verifying"
    assert evidence_git_uploader.advance_submission(queued, policy=policy, client=provider, now=now, verifier=verifier) == "uploading"
    assert evidence_git_uploader.advance_submission(queued, policy=policy, client=provider, now=now, verifier=verifier) == "awaiting_checks"
    assert evidence_git_uploader.advance_submission(queued, policy=policy, client=provider, now=now, verifier=verifier) == "awaiting_checks"
    provider.check = "passed"
    assert evidence_git_uploader.advance_submission(queued, policy=policy, client=provider, now=now, verifier=verifier) == "awaiting_merge"
    assert evidence_git_uploader.advance_submission(queued, policy=policy, client=provider, now=now, verifier=verifier) == "awaiting_merge"
    assert queued.merge_commit_sha == MERGE
    assert evidence_git_uploader.advance_submission(queued, policy=policy, client=provider, now=now, verifier=verifier) == "verified"
    assert provider.deleted == [queued.branch_name]


def test_transient_merge_retry_preserves_pull_request_and_exact_head(tmp_path):
    queued = row(tmp_path / "bundle")
    queued.state = "awaiting_merge"
    queued.branch_name = "ingest/instance/bundle"
    queued.pull_request_number = 7
    queued.pull_request_head_sha = HEAD
    policy = evidence_git_uploader.UploaderPolicy(True, "42", "ctl-controller000001", INSTANCE_ID)

    class Failing(Provider):
        def merge_pull_request(self, **_kwargs):
            raise github_token_client.GitHubArchiveError("protected_merge_failed", retryable=True)

    result = evidence_git_uploader.advance_submission(queued, policy=policy, client=Failing())
    assert result == "awaiting_merge"
    assert queued.pull_request_number == 7
    assert queued.pull_request_head_sha == HEAD
    assert queued.branch_name == "ingest/instance/bundle"
    assert queued.next_attempt_at is not None


def test_transient_pull_request_failure_reuses_the_created_branch(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.write_bytes(b"synthetic")
    monkeypatch.setattr(evidence_git_uploader.portable_bundle, "sha256_file", lambda _path: BUNDLE_SHA)
    queued = row(bundle)
    queued.state = "uploading"
    queued.base_sha = HEAD
    policy = evidence_git_uploader.UploaderPolicy(
        True, "42", "ctl-controller000001", INSTANCE_ID,
    )

    class Ambiguous(Provider):
        def __init__(self):
            super().__init__()
            self.create_calls = 0
            self.open_calls = 0

        def create_archive_commit(self, **kwargs):
            self.create_calls += 1
            return super().create_archive_commit(**kwargs)

        def open_pull_request(self, **_kwargs):
            self.open_calls += 1
            if self.open_calls == 1:
                raise github_token_client.GitHubArchiveError(
                    "github_api_unavailable", retryable=True,
                )
            return 7

    provider = Ambiguous()
    assert evidence_git_uploader.advance_submission(
        queued, policy=policy, client=provider,
    ) == "uploading"
    assert queued.pull_request_head_sha == HEAD
    assert queued.branch_name is not None
    assert evidence_git_uploader.advance_submission(
        queued, policy=policy, client=provider,
    ) == "awaiting_checks"
    assert provider.create_calls == 1
    assert provider.open_calls == 2


def test_malformed_provider_response_is_bounded_and_retried(tmp_path):
    queued = row(tmp_path / "bundle")
    queued.state = "awaiting_checks"
    queued.pull_request_number = 7
    queued.pull_request_head_sha = HEAD
    policy = evidence_git_uploader.UploaderPolicy(
        True, "42", "ctl-controller000001", INSTANCE_ID,
    )

    class Malformed(Provider):
        def pull_request_head(self, _number):
            raise KeyError("synthetic-provider-field")

    result = evidence_git_uploader.advance_submission(
        queued, policy=policy, client=Malformed(),
    )
    assert result == "awaiting_checks"
    assert queued.failure_reason == "provider_invalid_response"
    assert queued.next_attempt_at is not None


def test_disabled_rollback_changed_head_and_check_timeout_fail_closed(tmp_path):
    now = datetime(2026, 7, 31, tzinfo=timezone.utc)
    disabled = row(tmp_path / "disabled")
    policy = evidence_git_uploader.UploaderPolicy(False, "42", "ctl-controller000001", INSTANCE_ID)
    assert evidence_git_uploader.advance_submission(disabled, policy=policy, client=Provider(), now=now) == "requires_controller_action"
    assert disabled.failure_reason == "automatic_archival_disabled"

    rollback = row(tmp_path / "rollback")
    rollback.state = "verifying"
    policy = evidence_git_uploader.UploaderPolicy(True, "42", "ctl-controller000001", INSTANCE_ID)
    verifier = lambda *_args, **_kwargs: {
        "bundle_sha256": BUNDLE_SHA, "bundle_id": BUNDLE_ID,
        "chain_head_sha256": CHAIN_HEAD, "record_sha256s": ["9" * 64],
    }
    assert evidence_git_uploader.advance_submission(
        rollback, policy=policy, client=Provider(), now=now,
        previous_archived_chain_head="8" * 64, verifier=verifier,
    ) == "blocked"
    assert rollback.failure_reason == "rollback_or_fork_detected"

    changed = row(tmp_path / "changed")
    changed.state = "awaiting_checks"
    changed.pull_request_number = 7
    changed.pull_request_head_sha = HEAD
    changed.checks_started_at = now
    provider = Provider()
    provider.pull_request_head = lambda _number: MERGE
    assert evidence_git_uploader.advance_submission(changed, policy=policy, client=provider, now=now) == "blocked"
    assert changed.failure_reason == "pull_request_head_changed"

    timed_out = row(tmp_path / "timeout")
    timed_out.state = "awaiting_checks"
    timed_out.pull_request_number = 7
    timed_out.pull_request_head_sha = HEAD
    timed_out.checks_started_at = now - __import__("datetime").timedelta(hours=1)
    short_policy = evidence_git_uploader.UploaderPolicy(
        True, "42", "ctl-controller000001", INSTANCE_ID, check_timeout_seconds=60,
    )
    assert evidence_git_uploader.advance_submission(timed_out, policy=short_policy, client=Provider(), now=now) == "failed"
    assert timed_out.failure_reason == "required_checks_timed_out"


def test_exported_archive_template_is_closed_and_has_required_checks(tmp_path):
    archive = tmp_path / "archive"
    evidence_git.export_template(ROOT, archive)
    assert evidence_archive_repository.verify_repository(archive) == {
        "valid": True, "controller_id": None, "instances": 0, "bundles": 0,
    }
    workflow = (archive / ".github" / "workflows" / "verify-evidence.yml").read_text(encoding="utf-8")
    assert "name: Evidence verification" in workflow
    assert "name: Ingestion path validation" in workflow
    assert "contents: read" in workflow


def test_ingestion_path_gate_allows_only_one_new_bundle_and_digest(tmp_path):
    archive = tmp_path / "archive"
    evidence_git.export_template(ROOT, archive)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=archive, check=True)
    subprocess.run(["git", "config", "user.name", "Synthetic Evidence"], cwd=archive, check=True)
    subprocess.run(["git", "config", "user.email", "synthetic@invalid.example"], cwd=archive, check=True)
    subprocess.run(["git", "add", "."], cwd=archive, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "template"], cwd=archive, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=archive, text=True).strip()
    destination = archive / "instances" / INSTANCE_ID / "bundles" / BUNDLE_ID
    destination.mkdir(parents=True)
    (destination / "evidence.bundle").write_bytes(b"synthetic")
    (destination / "bundle.sha256").write_text("0" * 64 + "  evidence.bundle\n", encoding="ascii")
    subprocess.run(["git", "add", "."], cwd=archive, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "bundle"], cwd=archive, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=archive, text=True).strip()
    result = evidence_archive_repository.validate_ingestion_paths(archive, base, head)
    assert [path.rsplit("/", 1)[1] for path in result["paths"]] == ["bundle.sha256", "evidence.bundle"]

    (archive / "README.md").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=archive, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "forbidden"], cwd=archive, check=True)
    forbidden_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=archive, text=True).strip()
    with pytest.raises(evidence_archive_repository.ArchiveRepositoryError, match="exactly two files"):
        evidence_archive_repository.validate_ingestion_paths(archive, head, forbidden_head)


def test_ingestion_path_gate_allows_exact_template_from_empty_base(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=archive, check=True)
    subprocess.run(["git", "config", "user.name", "Synthetic Evidence"], cwd=archive, check=True)
    subprocess.run(["git", "config", "user.email", "synthetic@invalid.example"], cwd=archive, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "empty root"], cwd=archive, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=archive, text=True).strip()

    template = tmp_path / "template"
    evidence_git.export_template(ROOT, template)
    shutil.copytree(template, archive, dirs_exist_ok=True)
    subprocess.run(["git", "add", "."], cwd=archive, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "template"], cwd=archive, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=archive, text=True).strip()

    result = evidence_archive_repository.validate_ingestion_paths(archive, base, head)
    assert result["mode"] == "template-bootstrap"
    assert set(result["paths"]) == evidence_archive_repository.STATIC_FILES


def test_tui_uses_masked_atomic_secret_storage_and_excludes_token_everywhere():
    tui = (ROOT / "deploy" / "management" / "evidence.sh").read_text(encoding="utf-8")
    snapshots = (ROOT / "deploy" / "management" / "snapshots.sh").read_text(encoding="utf-8")
    diagnostics = (ROOT / "deploy" / "management" / "actions.sh").read_text(encoding="utf-8")
    config = (ROOT / "backend" / "app" / "core" / "config.py").read_text(encoding="utf-8")
    assert tui.count("ui_password") >= 2
    assert "github_pat_*" in tui and "Classic tokens are not supported" in tui
    assert "mktemp" in tui and "mv -f" in tui and "chmod 600" in tui
    assert "--token-file \"$token_file\"" in tui
    assert "EVIDENCE_GIT_ARCHIVE_ENABLED: bool = False" in config
    assert "evidence_github_fine_grained_token" in snapshots and "rm -f" in snapshots
    assert "evidence_github_fine_grained_token" not in diagnostics
    assert "mp_permissions_report diagnostics" in diagnostics
    forbidden = ("GITHUB_APP", "installation-token", "app private key", "classic personal access token")
    combined = "\n".join((tui, config, (ROOT / "deploy" / "evidence" / "github_token_client.py").read_text(encoding="utf-8")))
    assert not any(value.casefold() in combined.casefold() for value in forbidden)
