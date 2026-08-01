"""Contracts for fast exact-commit testing and immutable signed releases."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_repo_executable(path: Path) -> None:
    """Check the executable bit recorded by Git, including on Windows."""

    relative = path.relative_to(ROOT).as_posix()
    result = subprocess.run(
        ["git", "ls-files", "--stage", "--", relative],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.split(maxsplit=1)[0] == "100755"


def test_unsigned_lane_is_exact_commit_and_root_policy_gated() -> None:
    supervisor = ROOT / "deploy/test-deployment.sh"
    source = supervisor.read_text(encoding="utf-8")
    assert_repo_executable(supervisor)
    assert "require_test_policy" in source
    assert "deployment-policy" in text("deploy/management/common.sh")
    assert "exact 40-character commit" in source
    assert "git clone --filter=blob:none --no-checkout" in source
    assert "git -C \"$MP_TEST_SOURCE\" fetch --no-tags --force origin \"$commit\"" in source
    assert "MP-OPT UNSIGNED TEST BUILD DEPLOYED" in source


def test_signed_lane_is_exact_tag_peer_first_and_immutable() -> None:
    supervisor = ROOT / "deploy/signed-deployment.sh"
    source = supervisor.read_text(encoding="utf-8")
    workflow = text(".github/workflows/release.yml")
    assert_repo_executable(supervisor)
    assert "validate tag" in source
    assert "MP_SIGNED_PEER=1" in source
    assert "--tag \"$tag\"" in source
    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow.split("permissions:", 1)[0]
    assert "retired-tags.txt" in workflow
    assert 'git rev-parse origin/main' in workflow
    assert "inputs.release_tag" in workflow
    assert '[[ "$GITHUB_REF" == refs/heads/main ]]' in workflow
    assert '[[ "$(git rev-parse HEAD)" == "$tag_commit" ]]' in workflow
    assert 'git merge-base --is-ancestor "$tag_commit" origin/main' in workflow
    assert ".verification.verified == true" in workflow
    assert "pyproject.toml" in workflow
    assert "web/package.json" in workflow
    assert "web/package-lock.json" in workflow
    assert "sort_by(.id) | last" in workflow
    assert '.status // "missing"' in workflow
    assert "sort_by(.started_at)" not in workflow
    assert "gh release create \"$TAG\"" in workflow
    assert "gh release delete" not in workflow


def test_release_version_is_synchronised_and_documented() -> None:
    backend = tomllib.loads(text("pyproject.toml"))["project"]["version"]
    frontend = json.loads(text("web/package.json"))["version"]
    lock_document = json.loads(text("web/package-lock.json"))
    assert backend == frontend == lock_document["version"] == "3.8.0"
    assert lock_document["packages"][""]["version"] == "3.8.0"
    assert "signed-deployment.sh v3.8.0" in text("docs/deployment.md")
    assert "canonical signed `v3.8.0` release" in text("docs/publication-runbook.md")


def test_signed_release_preflight_binds_exact_latest_green_main() -> None:
    workflow = text(".github/workflows/release.yml")
    assert 'git rev-parse origin/main' in workflow
    assert '[[ "$GITHUB_REF" == refs/tags/v* ]]' in workflow
    assert '[[ "$tag_commit" == "$GITHUB_SHA" ]]' in workflow
    assert 'git merge-base --is-ancestor "$tag_commit" origin/main' in workflow
    assert 'RELEASE_COMMIT=$tag_commit' in workflow
    assert "pyproject.toml" in workflow
    assert "web/package.json" in workflow
    assert "web/package-lock.json" in workflow
    assert "sort_by(.id) | last" in workflow
    assert '.status // "missing"' in workflow
    assert "sort_by(.started_at)" not in workflow


def test_public_release_is_reverified_from_anonymous_downloads() -> None:
    workflow = text(".github/workflows/release.yml")
    assert '.private == false and .visibility == "public"' in workflow
    assert ".private == false &&" not in workflow
    assert 'releases/tags/${TAG}' in workflow
    assert "--certificate-identity \"$identity\"" in workflow
    assert "verify_asset .frontend" in workflow
    assert "verify_asset .operations" in workflow
    assert "verify_asset .bootstrap" in workflow
    assert "verify_asset .sboms.source" in workflow
    assert "verify_asset .sboms.tools" in workflow
    assert 'cosign verify \\' in workflow


def test_ci_is_draft_aware_and_uses_shared_component_classifier() -> None:
    workflow = text(".github/workflows/server-ci.yml")
    assert "github.event.pull_request.draft == false" in workflow
    assert "Draft PR: heavy CI intentionally deferred." in workflow
    assert "deploy/test_deployment.py classify" in workflow
    assert "server-ci-result" in workflow


def test_documentation_runs_when_a_draft_becomes_ready_without_deploying() -> None:
    workflow = text(".github/workflows/docs.yml")
    pull_request_trigger = workflow.split("pull_request:", 1)[1].split("push:", 1)[0]
    assert "types: [opened, reopened, synchronize, ready_for_review]" in workflow
    assert "paths:" not in pull_request_trigger
    assert "github.event.pull_request.draft == false" in workflow
    assert "if: github.event_name != 'pull_request'" in workflow


def test_retired_release_name_cannot_be_reused() -> None:
    retired = {
        line.strip()
        for line in text("deploy/release/retired-tags.txt").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "v3.4.0" in retired
