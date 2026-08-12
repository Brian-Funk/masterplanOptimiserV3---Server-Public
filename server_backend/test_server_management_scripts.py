"""Regression tests for production provisioning and management scripts."""

from pathlib import Path
import hashlib
import os
import stat
import subprocess


def _server_root() -> Path:
    """Return the checked-out server repository used by external tests."""
    return Path(__file__).resolve().parents[1]


def _run_bash(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run a non-interactive management helper with a controlled environment."""
    return subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )


def test_operator_scripts_are_executable_and_existing_users_gain_docker_access():
    """Fresh clones must be directly runnable and provision existing operators."""
    root = _server_root()
    scripts = [
        root / "manage.sh",
        root / "configure-production.sh",
        root / "deploy" / "deploy.sh",
        root / "deploy" / "setup-server.sh",
        *(root / "deploy" / "management").glob("*.sh"),
    ]
    relative_scripts = [script_path.relative_to(root).as_posix() for script_path in scripts]
    staged = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--stage", "--", *relative_scripts],
        check=True,
        capture_output=True,
        text=True,
    )
    staged_modes = {
        path: mode
        for line in staged.stdout.splitlines()
        for mode, _object_id, _stage, path in [line.split(maxsplit=3)]
    }
    for relative_path, script_path in zip(relative_scripts, scripts, strict=True):
        assert staged_modes.get(relative_path) == "100755", script_path
        if os.name != "nt":
            assert script_path.stat().st_mode & stat.S_IXUSR, script_path

    setup = (root / "deploy" / "setup-server.sh").read_text(encoding="utf-8")
    assert "usermod -aG docker deploy" in setup
    assert "Docker group membership verified" in setup
    assert "Reconnect through SSH as deploy" in setup


def test_caddy_topology_detects_container_host_and_unavailable(tmp_path: Path):
    """The shared topology helper must distinguish all supported proxy states."""
    root = _server_root()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "docker").write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"${FAKE_CONTAINER_CADDY:-0}\" = 1 ]; then printf 'db\\nbackend\\ncaddy\\n'; "
        "else printf 'db\\nbackend\\n'; fi\n",
        encoding="utf-8",
    )
    (fake_bin / "systemctl").write_text(
        "#!/usr/bin/env bash\nexit \"${FAKE_SYSTEMCTL_STATUS:-1}\"\n",
        encoding="utf-8",
    )
    for executable in fake_bin.iterdir():
        executable.chmod(0o700)

    host_caddy = tmp_path / "Caddyfile"
    host_caddy.write_text("example.test { respond ok }\n", encoding="utf-8")
    common = root / "deploy" / "management" / "common.sh"
    command = f'source "{common}"; mp_caddy_mode'
    base_env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "MP_ROOT": str(root),
        "MP_HOST_CADDYFILE": str(host_caddy),
    }

    container = _run_bash(command, {**base_env, "FAKE_CONTAINER_CADDY": "1"})
    assert container.returncode == 0
    assert container.stdout.strip() == "container"

    host = _run_bash(
        command,
        {
            **base_env,
            "FAKE_CONTAINER_CADDY": "0",
            "FAKE_SYSTEMCTL_STATUS": "0",
        },
    )
    assert host.returncode == 0
    assert host.stdout.strip() == "host"

    unavailable = _run_bash(
        command,
        {
            **base_env,
            "FAKE_CONTAINER_CADDY": "0",
            "FAKE_SYSTEMCTL_STATUS": "1",
        },
    )
    assert unavailable.returncode == 0
    assert unavailable.stdout.strip() == "unavailable"


def test_snapshot_only_captures_host_caddy_for_host_topology(tmp_path: Path):
    """Container snapshots must not accidentally capture host proxy state."""
    root = _server_root()
    installation = tmp_path / "installation"
    (installation / "secrets").mkdir(parents=True)
    (installation / "infra").mkdir()
    (installation / ".env").write_text("DOMAIN=example.test\n", encoding="utf-8")
    (installation / "secrets" / "secret_key").write_text("secret", encoding="utf-8")
    evidence = installation / "state" / "evidence"
    (evidence / "ledger").mkdir(parents=True)
    (evidence / "public").mkdir()
    (evidence / "ledger" / "chain-head.json").write_text(
        '{"head_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}\n',
        encoding="utf-8",
    )
    (evidence / "public" / "instance_signing_key.pub").write_text(
        "ssh-ed25519 synthetic-test-key\n",
        encoding="utf-8",
    )
    host_caddy = tmp_path / "Caddyfile"
    host_caddy.write_text("example.test { respond ok }\n", encoding="utf-8")
    common = root / "deploy" / "management" / "common.sh"
    snapshots = root / "deploy" / "management" / "snapshots.sh"

    for topology, expected in (("container", False), ("host", True)):
        payload = tmp_path / f"payload-{topology}"
        command = (
            f'source "{common}"; source "{snapshots}"; '
            f'mp_caddy_mode() {{ printf "{topology}\\n"; }}; '
            'sudo() { [ "$1" != "-n" ] || shift; '
            '[ "$1" != "chown" ] || return 0; "$@"; }; '
            f'mp_snapshot_copy_configuration "{payload}" yes'
        )
        result = _run_bash(
            command,
            {
                "MP_ROOT": str(installation),
                "MP_HOST_CADDYFILE": str(host_caddy),
            },
        )
        assert result.returncode == 0, result.stderr
        assert (payload / "config" / "Caddyfile").exists() is expected
        assert (payload / "metadata" / "caddy-topology").read_text(
            encoding="utf-8",
        ).strip() == topology
        assert (payload / "evidence" / "ledger" / "chain-head.json").is_file()
        assert (
            payload / "evidence" / "public" / "instance_signing_key.pub"
        ).is_file()


def test_base_schema_bootstrap_skips_existing_and_uses_one_shot_for_blank_database(
    tmp_path: Path,
):
    """Base-schema initialisation must not start the public backend."""
    root = _server_root()
    common = root / "deploy" / "management" / "common.sh"
    compose_log = tmp_path / "compose.log"
    script = f'''
source "{common}"
mp_compose_init() {{ MP_COMPOSE=(fake_compose); }}
fake_compose() {{ printf '%s\n' "$*" >> "{compose_log}"; [ "$1" != ps ] || printf 'backend\n'; }}
mp_database_has_base_schema() {{ calls=$((calls + 1)); [ "$calls" -ge "$schema_ready_call" ]; }}
calls=0
schema_ready_call=1
mp_ensure_base_schema
[ ! -s "{compose_log}" ]
schema_ready_call=2
calls=0
mp_ensure_base_schema
grep -Fq 'run -T --rm --no-deps backend python -m app.tools.bootstrap_schema' "{compose_log}"
! grep -Fq 'up -d' "{compose_log}"
! grep -Fq 'stop backend' "{compose_log}"
'''
    result = _run_bash(script, {"MP_ROOT": str(root)})
    assert result.returncode == 0, result.stderr


def test_dynamic_migrations_run_in_filename_order(tmp_path: Path):
    """The shared migration runner must discover future files automatically."""
    root = _server_root()
    installation = tmp_path / "installation"
    migrations = installation / "deploy" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "20990102_second.sql").write_text("second\n", encoding="utf-8")
    (migrations / "20990101_first.sql").write_text("first\n", encoding="utf-8")
    migration_log = tmp_path / "migrations.log"
    common = root / "deploy" / "management" / "common.sh"
    script = f'''
source "{common}"
mp_compose_init() {{ MP_COMPOSE=(fake_compose); }}
fake_compose() {{ cat >> "{migration_log}"; }}
mp_apply_migrations
'''
    result = _run_bash(script, {"MP_ROOT": str(installation)})
    assert result.returncode == 0, result.stderr
    assert migration_log.read_text(encoding="utf-8") == "first\nsecond\n"
    assert result.stdout.index("20990101_first.sql") < result.stdout.index("20990102_second.sql")


def test_dynamic_migrations_stop_at_first_sql_failure(tmp_path: Path):
    """A later successful script must never mask an earlier migration failure."""
    root = _server_root()
    installation = tmp_path / "installation"
    migrations = installation / "deploy" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "20990101_first.sql").write_text("first\n", encoding="utf-8")
    (migrations / "20990102_second.sql").write_text("second\n", encoding="utf-8")
    migration_log = tmp_path / "migrations.log"
    common = root / "deploy" / "management" / "common.sh"
    script = f'''\nsource "{common}"\nmp_compose_init() {{ MP_COMPOSE=(fake_compose); }}\nfake_compose() {{\n    cat >> "{migration_log}"\n    [ "$(wc -l < "{migration_log}")" -ne 1 ]\n}}\n! mp_apply_migrations\n'''
    result = _run_bash(script, {"MP_ROOT": str(installation)})
    assert result.returncode == 0, result.stderr
    assert migration_log.read_text(encoding="utf-8") == "first\n"
    assert "Migration failed: 20990101_first.sql" in result.stderr


def test_dynamic_migrations_skip_matching_ledger_entry(tmp_path: Path):
    """An unchanged recorded migration must not execute a second time."""
    root = _server_root()
    installation = tmp_path / "installation"
    migrations = installation / "deploy" / "migrations"
    migrations.mkdir(parents=True)
    migration = migrations / "20990101_first.sql"
    migration.write_text("first\n", encoding="utf-8")
    digest = hashlib.sha256(migration.read_bytes()).hexdigest()
    migration_log = tmp_path / "migrations.log"
    common = root / "deploy" / "management" / "common.sh"
    script = f'''\nsource "{common}"\nmp_compose_init() {{ MP_COMPOSE=(fake_compose); }}\nfake_compose() {{\n    case "$*" in\n        *"SELECT sha256 FROM mp_schema_migrations"*) printf '%s\\n' "{digest}" ;;\n        *) cat >> "{migration_log}" ;;\n    esac\n}}\nmp_apply_migrations\n'''
    result = _run_bash(script, {"MP_ROOT": str(installation)})
    assert result.returncode == 0, result.stderr
    assert not migration_log.exists() or not migration_log.read_text(encoding="utf-8")
    assert "Migration already applied: 20990101_first.sql" in result.stdout


def test_caddy_validation_and_logs_follow_active_topology(tmp_path: Path):
    """Validation and bounded logs must use Compose or systemd consistently."""
    root = _server_root()
    common = root / "deploy" / "management" / "common.sh"
    actions = root / "deploy" / "management" / "actions.sh"
    command_log = tmp_path / "commands.log"
    script = f'''
source "{common}"
source "{actions}"
mp_compose_init() {{ MP_COMPOSE=(fake_compose); }}
fake_compose() {{
    printf 'compose:%s\n' "$*" >> "{command_log}"
    case " $* " in
        *" ps -q caddy "*) printf 'fake-caddy-container\n' ;;
    esac
}}
docker() {{
    printf 'docker:%s\n' "$*" >> "{command_log}"
    case " $* " in
        *" inspect --format "*) printf 'true\n' ;;
    esac
}}
systemctl() {{
    printf 'systemctl:%s\n' "$*" >> "{command_log}"
    case " $* " in
        *" is-active --quiet caddy "*) return 0 ;;
    esac
}}
sudo() {{ printf 'sudo:%s\n' "$*" >> "{command_log}"; }}
mp_caddy_mode() {{ printf 'container\n'; }}
mp_caddy_validate
mp_collect_logs caddy recent 25
mp_caddy_mode() {{ printf 'host\n'; }}
mp_caddy_validate
mp_collect_logs caddy since 30m
mp_caddy_mode() {{ printf 'unavailable\n'; }}
! mp_caddy_validate
! mp_collect_logs caddy recent 25
'''
    result = _run_bash(script, {"MP_ROOT": str(root)})
    assert result.returncode == 0, result.stderr
    commands = command_log.read_text(encoding="utf-8")
    assert (
        "compose:exec -T caddy caddy validate --config "
        "/etc/caddy/Caddyfile --adapter caddyfile"
    ) in commands
    assert "compose:logs --tail 25 caddy" in commands
    assert "sudo:-n caddy validate --config" in commands
    assert "sudo:journalctl -u caddy --since -30m --no-pager" in commands


def test_frontend_csp_runtime_repairs_only_empty_legacy_mount(tmp_path: Path):
    """Policy preparation must repair Docker's empty directory without data loss."""
    root = _server_root()
    common = root / "deploy" / "management" / "common.sh"
    runtime = tmp_path / "runtime"
    mistaken_mount = runtime / "frontend-csp.caddy"
    mistaken_mount.mkdir(parents=True)

    repaired = _run_bash(
        f'source "{common}"; mp_prepare_frontend_csp_runtime',
        {"MP_ROOT": str(tmp_path)},
    )

    assert repaired.returncode == 0, repaired.stderr
    assert runtime.is_dir()
    assert not mistaken_mount.exists()
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o711
    assert stat.S_IMODE((runtime / "compliance-requests").stat().st_mode) == 0o1733
    assert stat.S_IMODE((runtime / "compliance-receipts").stat().st_mode) == 0o755

    mistaken_mount.mkdir()
    marker = mistaken_mount / "preserve.txt"
    marker.write_text("operator data", encoding="utf-8")
    refused = _run_bash(
        f'source "{common}"; mp_prepare_frontend_csp_runtime',
        {"MP_ROOT": str(tmp_path)},
    )

    assert refused.returncode != 0
    assert marker.read_text(encoding="utf-8") == "operator data"
    assert "Refusing to replace non-empty CSP path" in refused.stderr
