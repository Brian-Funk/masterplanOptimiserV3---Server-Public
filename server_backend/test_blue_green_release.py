"""Migration-free signed blue/green release contract tests."""

from pathlib import Path
import tempfile

import pytest

from deploy.release import activate_frontend, install_release


ROOT = Path(__file__).resolve().parents[1]
BLUE_GREEN = (ROOT / "deploy/release/blue_green_upgrade.sh").read_text(encoding="utf-8")
SIGNED_DEPLOYMENT = (ROOT / "deploy/signed-deployment.sh").read_text(encoding="utf-8")
RELEASE_WORKFLOW = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
MAIN = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")


OLD_CSP = (
    'Content-Security-Policy "default-src \'self\'; '
    "script-src 'self' 'sha256-b2xk'; object-src 'none'\"\n"
)
NEW_CSP = (
    'Content-Security-Policy "default-src \'self\'; '
    "script-src 'self' 'sha256-bmV3'; object-src 'none'\"\n"
)


def _write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def test_frontend_activation_retains_old_hashes_and_switches_documents_atomically():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write(root / "web/out/index.html", "old html")
        _write(root / "web/out/_next/static/old.js", "old asset")
        _write(root / "web/.out.next/index.html", "new html")
        _write(root / "web/.out.next/_next/static/new.js", "new asset")
        _write(root / "runtime/frontend-csp.caddy", OLD_CSP)
        _write(root / "runtime/.frontend-csp.next", NEW_CSP)

        activate_frontend.activate(root)

        assert (root / "web/out/index.html").read_text() == "new html"
        assert (root / "web/out/_next/static/old.js").read_text() == "old asset"
        assert (root / "web/out/_next/static/new.js").read_text() == "new asset"
        policy = (root / "runtime/frontend-csp.caddy").read_text()
        assert "'sha256-b2xk'" in policy
        assert "'sha256-bmV3'" in policy

        activate_frontend.rollback(root)
        assert (root / "web/out/index.html").read_text() == "old html"
        assert (root / "runtime/frontend-csp.caddy").read_text() == OLD_CSP


def test_frontend_finalization_uses_exact_new_policy_and_removes_only_staging():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write(root / "web/.out.next/index.html", "new html")
        _write(root / "runtime/.frontend-csp.next", NEW_CSP)
        _write(root / "runtime/frontend-csp.caddy", OLD_CSP)

        activate_frontend.finalize(root)

        assert (root / "runtime/frontend-csp.caddy").read_text() == NEW_CSP
        assert not (root / "web/.out.next").exists()
        assert not (root / "runtime/.frontend-csp.next").exists()


def test_frontend_activation_rejects_nested_symlinks_before_changing_active_state(tmp_path):
    root = tmp_path
    _write(root / "web/out/index.html", "old html")
    _write(root / "web/.out.next/index.html", "new html")
    _write(root / "runtime/frontend-csp.caddy", OLD_CSP)
    _write(root / "runtime/.frontend-csp.next", NEW_CSP)
    try:
        (root / "web/.out.next/unsafe.js").symlink_to(root / "web/out/index.html")
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")

    with pytest.raises(RuntimeError, match="symlink"):
        activate_frontend.activate(root)

    assert (root / "web/out/index.html").read_text() == "old html"
    assert (root / "runtime/frontend-csp.caddy").read_text() == OLD_CSP
    assert not (root / "web/.out.previous").exists()


def test_release_environment_records_signed_blue_green_authorisation():
    images = {
        name: f"ghcr.io/example/{name}@sha256:{number * 64}"
        for name, number in zip(("backend", "caddy", "postgres", "tools"), "1234")
    }
    deployment = {
        "previous_tag": "v3.9.15",
        "migration_free": True,
        "infrastructure_unchanged": True,
        "blue_green_eligible": True,
    }

    environment = install_release.release_environment(
        "v3.9.16", "a" * 40, images, deployment, blue_green_staged=True,
    )

    assert "MP_RELEASE_MIGRATION_FREE=true" in environment
    assert "MP_RELEASE_INFRASTRUCTURE_UNCHANGED=true" in environment
    assert "MP_RELEASE_BLUE_GREEN_ELIGIBLE=true" in environment
    assert "MP_RELEASE_BLUE_GREEN_STAGED=true" in environment


def test_blue_green_installer_binds_to_exact_active_predecessor():
    source = (ROOT / "deploy/release/install_release.py").read_text(encoding="utf-8")

    assert 'active_tags != [deployment["previous_tag"]]' in source
    assert "does not name the exact active predecessor" in source


def test_staged_rollback_rejects_unsafe_frontend_before_exchanging_operations(
    tmp_path,
):
    root = tmp_path
    for current, previous in (
        ("deploy", ".deploy.previous"),
        ("infra", ".infra.previous"),
    ):
        _write(root / current / "marker", "new")
        _write(root / previous / "marker", "old")
    for current, previous in (
        ("manage.sh", ".manage.sh.previous"),
        ("configure-production.sh", ".configure-production.sh.previous"),
        (".release.env", ".release.env.previous"),
    ):
        contents = "MP_RELEASE_BLUE_GREEN_STAGED=true\n" if current == ".release.env" else "new"
        _write(root / current, contents)
        _write(root / previous, "old")
    (root / "web").mkdir(exist_ok=True)
    try:
        (root / "web/.out.next").symlink_to(root / "deploy", target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")

    try:
        install_release.rollback_staged(root)
    except RuntimeError as error:
        assert "unsafe" in str(error)
    else:
        raise AssertionError("unsafe staged frontend was accepted")

    assert (root / "deploy/marker").read_text() == "new"
    assert (root / ".deploy.previous/marker").read_text() == "old"


def test_blue_green_path_never_recreates_postgres_or_caddy():
    assert 'up -d --no-deps --no-build --pull never --force-recreate backend' in BLUE_GREEN
    assert "force-recreate db" not in BLUE_GREEN
    assert "force-recreate caddy" not in BLUE_GREEN
    assert "BLUE_GREEN_STAGING=true" in BLUE_GREEN
    assert "masterplan-backend-next:8000" in BLUE_GREEN
    assert 'docker rm -f "$next_container"' not in BLUE_GREEN
    assert 'docker rm -f "$next_container_id"' in BLUE_GREEN
    assert 'name=^/${next_container}$' in BLUE_GREEN
    assert "caddy reload" in BLUE_GREEN
    assert "--blue-green" in SIGNED_DEPLOYMENT
    assert "if not settings.BLUE_GREEN_STAGING" in MAIN
    assert "without housekeeping" in MAIN


def test_release_manifest_fails_closed_on_migrations_or_infrastructure_change():
    assert 'format:"mp-opt-deployment-contract-v1"' in RELEASE_WORKFLOW
    assert 'migration_free:$migration_free' in RELEASE_WORKFLOW
    assert 'infrastructure_unchanged:$infrastructure_unchanged' in RELEASE_WORKFLOW
    assert 'blue_green_eligible:$blue_green_eligible' in RELEASE_WORKFLOW
    assert "deploy/migrations backend/app/models backend/app/db/database.py" in RELEASE_WORKFLOW


def test_release_can_bind_a_skipped_release_to_the_exact_active_predecessor():
    assert "active_predecessor_tag:" in RELEASE_WORKFLOW
    assert '[[ "$ACTIVE_PREDECESSOR_TAG" != "$TAG" ]]' in RELEASE_WORKFLOW
    assert 'git merge-base --is-ancestor "$previous_commit" "$RELEASE_COMMIT"' in RELEASE_WORKFLOW
    assert '[[ "$(git rev-parse "$previous_tag^{commit}")" == "$previous_commit" ]]' in RELEASE_WORKFLOW


def test_unchanged_caddy_and_postgres_are_reused_even_for_a_full_plan():
    assert 'INFRASTRUCTURE_UNCHANGED: ${{ steps.plan.outputs.infrastructure_unchanged }}' in RELEASE_WORKFLOW
    assert '( "$service" == caddy || "$service" == postgres )' in RELEASE_WORKFLOW
    assert "changed=false" in RELEASE_WORKFLOW
