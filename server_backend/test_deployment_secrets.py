"""Production deployment and secret provisioning tests."""
import base64
import hashlib
from pathlib import Path
import subprocess
import sys


def _server_root() -> Path:
    """Return the checked-out server repository root used by external tests."""
    return Path(__file__).resolve().parents[1]


def test_deploy_provisions_required_root_bootstrap_secret():
    """Deploy must create Compose's bootstrap bind source when it is absent."""
    deploy_script = (_server_root() / "deploy" / "deploy.sh").read_text(
        encoding="utf-8",
    )

    assert 'if [ ! -e "secrets/root_bootstrap_token" ]; then' in deploy_script
    assert "'^ROOT_BOOTSTRAP_TOKEN='" in deploy_script
    assert '[[ "$ROOT_BOOTSTRAP_TOKEN" == CHANGE_ME* ]]' in deploy_script
    assert (
        'printf "%s" "$ROOT_BOOTSTRAP_TOKEN" > secrets/root_bootstrap_token'
        in deploy_script
    )
    assert "chmod 600 secrets/database_password secrets/ip_hmac_key" in deploy_script
    assert "secrets/secret_key secrets/vapid_private_key" in deploy_script
    for protected_name in (
        "secrets/root_bootstrap_token",
        "secrets/smtp_token",
        "secrets/evidence_signing_key",
    ):
        assert protected_name in deploy_script


def test_deploy_preserves_an_empty_bootstrap_secret():
    """An existing empty secret must continue to mean bootstrap is disabled."""
    deploy_script = (_server_root() / "deploy" / "deploy.sh").read_text(
        encoding="utf-8",
    )

    assert 'if [ ! -e "secrets/root_bootstrap_token" ]; then' in deploy_script
    assert 'if [ ! -s "secrets/root_bootstrap_token" ]; then' not in deploy_script


def test_completed_root_bootstrap_is_removed_from_replication_and_snapshots():
    root = _server_root()
    common = (root / "deploy" / "management" / "common.sh").read_text(
        encoding="utf-8"
    )
    deploy_script = (root / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    replication = (root / "deploy" / "ha" / "replicate_now.sh").read_text(
        encoding="utf-8"
    )
    snapshots = (root / "deploy" / "management" / "snapshots.sh").read_text(
        encoding="utf-8"
    )

    assert "mp_root_bootstrap_is_disabled()" in common
    assert "key='root_bootstrap_disabled'" in common
    assert "WHERE u.is_root_admin" in common
    assert "mp_retire_root_bootstrap_secret" in deploy_script
    assert replication.index("mp_retire_root_bootstrap_secret") < replication.index(
        "pg_dump"
    )
    assert "mp_root_bootstrap_is_disabled" in snapshots
    assert ': > "$payload/config/secrets/root_bootstrap_token"' in snapshots


def test_deploy_assigns_one_stable_instance_id_to_legacy_configuration():
    """A pre-governance deployment must not share a zero evidence identifier."""
    deploy_script = (_server_root() / "deploy" / "deploy.sh").read_text(
        encoding="utf-8",
    )

    assert "ensure_instance_id()" in deploy_script
    assert 'mp_env_get MP_INSTANCE_ID "$REPO_DIR/.env"' in deploy_script
    assert 'mp_env_set MP_INSTANCE_ID "$instance_id" "$REPO_DIR/.env"' in deploy_script
    assert "canonical lowercase UUID" in deploy_script


def test_caddy_serves_instance_notices_without_frontend_javascript():
    """Public legal routes must be backed by the current database publication."""
    root = _server_root()
    for caddy_name in ("Caddyfile", "Caddyfile.local"):
        caddy = (root / "infra" / caddy_name).read_text(encoding="utf-8")
        assert "@governanceNotice path /privacy /legal /data-policy /retention /rights /processors" in caddy
        assert "rewrite * /api/v1/governance/public{path}.html" in caddy
        assert caddy.index("handle @governanceNotice") < caddy.index("# ── Everything else")


def test_smtp_token_is_provisioned_as_an_optional_docker_secret():
    """SMTP credentials must be mounted from a protected file, never `.env`."""

    root = _server_root()
    deploy_script = (root / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    compose = (root / "infra" / "docker-compose.prod.yml").read_text(
        encoding="utf-8",
    )
    example_env = (root / ".env.example").read_text(encoding="utf-8")

    assert ': > secrets/smtp_token' in deploy_script
    assert "secrets/smtp_token" in deploy_script
    assert "smtp_token:" in compose
    assert "file: ../secrets/smtp_token" in compose
    assert "SMTP_TOKEN=" not in example_env


def test_activation_email_brand_and_qr_assets_are_packaged_predictably():
    """Production must use the approved mail identity and deterministic artwork."""

    root = _server_root()
    example_env = (root / ".env.example").read_text(encoding="utf-8")
    management_actions = (root / "deploy" / "management" / "actions.sh").read_text(
        encoding="utf-8",
    )
    dockerfile = (root / "infra" / "Dockerfile").read_text(encoding="utf-8")

    assert "SMTP_FROM_NAME=Masterplan Access" in example_env
    assert 'smtp_from_name="Masterplan Access"' in management_actions
    assert "FROM python:3.14-alpine" in dockerfile
    assert "apk add --no-cache font-dejavu" in dockerfile
    assert "fonts-dejavu-core" not in dockerfile
    assert (
        "COPY web/public/logo_normal.png /app/app/assets/logo_normal.png"
        in dockerfile
    )


def test_backend_runs_unprivileged_and_database_examples_fail_closed():
    root = _server_root()
    dockerfile = (root / "infra" / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    production_compose = (root / "infra" / "docker-compose.prod.yml").read_text(
        encoding="utf-8"
    )
    common = (root / "deploy" / "management" / "common.sh").read_text(
        encoding="utf-8"
    )
    example_env = (root / ".env.example").read_text(encoding="utf-8")
    config = (root / "backend" / "app" / "core" / "config.py").read_text(
        encoding="utf-8"
    )

    assert "USER 10001:10001" in dockerfile
    assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile
    assert "postgresql://masterplan:masterplan@" not in compose
    assert "postgresql://masterplan:masterplan@" not in example_env
    assert "POSTGRES_PASSWORD=CHANGE_ME" not in example_env
    assert 'DATABASE_URL: str = ""' in config
    assert "DATABASE_URL uses a known placeholder password" in config
    assert "POSTGRES_PASSWORD_FILE: /run/secrets/database_password" in compose
    assert 'DATABASE_URL: ""' in compose
    assert "file: ../secrets/database_password" in compose
    assert "file: ../secrets/ip_hmac_key" in compose
    assert "POSTGRES_PASSWORD:" not in compose
    assert "POSTGRES_PASSWORD:" not in production_compose
    assert "postgresql://masterplan:${" not in production_compose
    assert "DATABASE_URL=" not in example_env
    assert "POSTGRES_PASSWORD=" not in example_env
    assert "read_only: true" in production_compose
    assert "cap_drop:\n      - ALL" in production_compose
    assert "no-new-privileges:true" in production_compose
    assert 'chmod 1733 "$request_dir"' in common
    assert 'chmod 0711 "$runtime_dir"' in common


def test_unsigned_activation_uses_newly_installed_runtime_permission_helper():
    """An operations update must take effect during its own exact-SHA deployment."""
    script = (_server_root() / "deploy" / "test-deployment.sh").read_text(
        encoding="utf-8"
    )

    assert "prepare_runtime_from_installed_sources" in script
    assert "source \"$MP_ROOT/deploy/management/common.sh\"" in script
    assert 'stat -c %a "$MP_ROOT/runtime"' in script
    assert "Runtime traversal permissions were not preserved" in script


def test_deletion_case_label_migration_is_bounded_to_open_cases():
    """Operator labels are backfilled for open cases and removed at completion."""
    migration = (
        _server_root()
        / "deploy"
        / "migrations"
        / "20260803_deletion_case_event_labels.sql"
    ).read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS event_display_name VARCHAR(128)" in migration
    assert "deletion_case.state <> 'complete'" in migration
    assert "SET event_display_name = NULL" in migration


def test_database_and_ip_secrets_have_guarded_lifecycle_paths():
    root = _server_root()
    deploy_script = (root / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    actions = (root / "deploy" / "management" / "actions.sh").read_text(
        encoding="utf-8"
    )
    snapshots = (root / "deploy" / "management" / "snapshots.sh").read_text(
        encoding="utf-8"
    )
    replication = (root / "deploy" / "ha" / "replicate_now.sh").read_text(
        encoding="utf-8"
    )

    assert "mp_migrate_database_secret" in deploy_script
    assert 'if [ ! -s "secrets/ip_hmac_key" ]; then' in deploy_script
    assert '"$staging/secrets/database_password"' in actions
    assert '"$staging/secrets/ip_hmac_key"' in actions
    assert 'mv -f "$staged" "$MP_ROOT/secrets/database_password"' in actions
    assert 'mv -f "$staged" "$MP_ROOT/secrets/ip_hmac_key"' in actions
    assert '"secret.ip-hmac-rotate" "success" "key-id:${key_id}"' in actions
    assert 'cat "$MP_ROOT/secrets/database_password"' in snapshots
    assert "ip_hmac_key" in replication


def test_backend_image_uses_the_reviewed_dependency_resolution():
    root = _server_root()
    dockerfile = (root / "infra" / "Dockerfile").read_text(encoding="utf-8")
    lock = (root / "backend" / "requirements.lock.txt").read_text(
        encoding="utf-8"
    )

    assert "COPY backend/requirements.lock.txt /app/requirements.lock.txt" in dockerfile
    assert "pip install --constraint requirements.lock.txt -r requirements.txt" in dockerfile
    for line in lock.splitlines():
        if line and not line.startswith("#"):
            assert "==" in line


def test_secret_material_is_excluded_from_git_and_docker_context():
    """Local credentials and backups must not enter commits or build layers."""

    root = _server_root()
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")

    for ignored in (".env.*", "secrets/"):
        assert ignored in gitignore
        assert ignored in dockerignore


def test_blank_database_base_schema_runs_before_dynamic_migrations():
    """Deployment must initialise a blank schema before ordered migrations."""

    root = _server_root()
    deploy_script = (root / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    common = (root / "deploy" / "management" / "common.sh").read_text(
        encoding="utf-8",
    )
    build_loop = "for service in db caddy backend; do"
    service_build = '"${MP_COMPOSE[@]}" build --pull "$service"'
    database_start = '"${MP_COMPOSE[@]}" up -d db'
    base_schema = "mp_ensure_base_schema"
    migrations = "mp_apply_migrations"
    schema_contract = "mp_verify_database_schema_contract"
    application_start = (
        '"${MP_COMPOSE[@]}" up -d --build --force-recreate --remove-orphans'
    )

    build_loop_index = deploy_script.index(build_loop)
    service_build_index = deploy_script.index(service_build, build_loop_index)
    database_start_index = deploy_script.index(database_start, service_build_index)
    base_schema_index = deploy_script.index(base_schema, database_start_index)
    migrations_index = deploy_script.index(migrations, base_schema_index)
    schema_contract_index = deploy_script.index(schema_contract, migrations_index)
    application_start_index = deploy_script.index(
        application_start,
        schema_contract_index,
    )

    assert build_loop_index < service_build_index
    assert service_build_index < database_start_index
    assert database_start_index < base_schema_index
    assert base_schema_index < migrations_index
    assert migrations_index < schema_contract_index
    assert schema_contract_index < application_start_index
    assert "deploy/migrations/*.sql" in common
    assert "basename \"$migration\"" in common
    assert "20260714_activation_email_delivery.sql" not in deploy_script
    assert "20260715_additional_passkey.sql" not in deploy_script
    windows_launcher = (root / "deploy" / "update-server.bat").read_text(
        encoding="utf-8",
    )
    assert "bash deploy/deploy.sh" in windows_launcher
    assert "deploy/migrations/202607" not in windows_launcher

    migration = "20260714_activation_email_delivery.sql"
    migration_sql = (root / "deploy" / "migrations" / migration).read_text(
        encoding="utf-8",
    )
    assert "ADD COLUMN IF NOT EXISTS delivery_pending" in migration_sql
    assert "SET value = '24'" in migration_sql

    purpose_sql = (root / "deploy" / "migrations" / "20260715_additional_passkey.sql").read_text(
        encoding="utf-8",
    )
    assert "ADD COLUMN IF NOT EXISTS purpose" in purpose_sql
    assert "additional_passkey" in purpose_sql
    assert "ALTER COLUMN purpose SET NOT NULL" in purpose_sql


def test_frontend_csp_allows_only_hashed_exported_inline_scripts(tmp_path: Path):
    """The static Next.js runtime must hydrate without enabling arbitrary scripts."""
    root = _server_root()
    frontend = tmp_path / "out"
    nested = frontend / "bootstrap"
    nested.mkdir(parents=True)
    scripts = ["self.__next_f=[]", "self.__next_f.push([0])"]
    (frontend / "index.html").write_text(
        f'<script>{scripts[0]}</script><script src="/external.js"></script>',
        encoding="utf-8",
    )
    (nested / "index.html").write_text(
        f"<script>{scripts[1]}</script><script>{scripts[0]}</script>",
        encoding="utf-8",
    )
    output = frontend / ".csp-header.caddy"

    result = subprocess.run(
        [
            sys.executable,
            str(root / "deploy" / "generate_frontend_csp.py"),
            str(frontend),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    header = output.read_text(encoding="utf-8")
    expected = {
        base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        for script in scripts
    }
    assert all(f"'sha256-{digest}'" in header for digest in expected)
    assert header.count("'sha256-") == len(expected)
    script_policy = header.split("script-src ", 1)[1].split(";", 1)[0]
    assert "'self'" in script_policy
    assert "'unsafe-inline'" not in script_policy


def test_frontend_build_generates_and_reloads_build_specific_csp():
    """Full deploys and menu rebuilds must activate the new hashed policy."""
    root = _server_root()
    deploy_script = (root / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    actions = (root / "deploy" / "management" / "actions.sh").read_text(
        encoding="utf-8",
    )
    common = (root / "deploy" / "management" / "common.sh").read_text(
        encoding="utf-8",
    )

    generator = "deploy/generate_frontend_csp.py"
    assert generator in deploy_script
    assert generator in actions
    assert "npm run build" in common
    assert deploy_script.index("mp_build_frontend_container") < deploy_script.index(generator)
    rebuild = actions[actions.index("mp_rebuild_frontend()") :]
    rebuild = rebuild[: rebuild.index("# Print one bounded service log selection")]
    assert rebuild.index("mp_build_frontend_container") < rebuild.index(generator)
    assert rebuild.index(generator) < rebuild.index("mp_caddy_reload")

    for caddy_name in ("Caddyfile", "Caddyfile.local"):
        caddy = (root / "infra" / caddy_name).read_text(encoding="utf-8")
        assert "import /etc/caddy/runtime/frontend-csp.caddy" in caddy
        script_lines = [line for line in caddy.splitlines() if "script-src" in line]
        assert not script_lines

    compose = (root / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    assert (
        "../runtime:/etc/caddy/runtime:ro"
        in compose
    )
    assert "runtime/" in (root / ".gitignore").read_text(encoding="utf-8")
