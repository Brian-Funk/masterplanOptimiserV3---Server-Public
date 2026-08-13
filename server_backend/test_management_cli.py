"""SSH-only MP-OPT_SERVER management and recovery tooling tests."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess


def _server_root() -> Path:
    """Return the checked-out server repository used by integration tests."""

    return Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    """Read one management source file as UTF-8."""

    return (_server_root() / relative).read_text(encoding="utf-8")


def _bash_path(path: Path) -> str:
    """Translate a Windows path for the WSL Bash used by local shell tests."""

    resolved = path.resolve()
    if os.name != "nt":
        return str(resolved)
    drive = resolved.drive.rstrip(":").lower()
    relative = resolved.as_posix().split(":/", 1)[1]
    return f"/mnt/{drive}/{relative}"


def test_management_shell_sources_are_syntax_valid():
    """Every SSH management entry point must parse before deployment."""

    root = _server_root()
    scripts = [
        root / "manage.sh",
        root / "configure-production.sh",
        root / "deploy" / "deploy.sh",
        root / "deploy" / "setup-server.sh",
        *sorted((root / "deploy" / "management").glob("*.sh")),
    ]
    result = subprocess.run(
        ["bash", "-n", *map(str, scripts)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_management_entry_point_is_branded_menu_only():
    """The operator receives one branded graphical menu instead of raw commands."""

    entry = _read("manage.sh")
    common = _read("deploy/management/common.sh")

    assert "MP-OPT_SERVER" in entry
    assert "MP-OPT_SERVER" in common
    assert "Brian Funk" in common
    assert "Copyright © %s Brian Funk" in common
    assert 'MP_COPYRIGHT_YEAR="2026"' in common
    assert 'ui_menu "MP-OPT_SERVER"' in entry
    assert "Snapshots and recovery" in entry
    assert "Root administrator recovery" in entry
    assert "Database" in entry
    assert "Logs" in entry
    assert "Maintenance and diagnostics" in entry
    assert "High availability" in entry
    setup_dispatch = entry.index('if [ "${1:-}" = setup ]; then')
    tty_guard = entry.index("mp_require_interactive_terminal")
    assert setup_dispatch < entry.index('source "$MP_ROOT/deploy/management/common.sh"')
    assert setup_dispatch < tty_guard
    assert 'exec bash "$MP_ROOT/deploy/commissioning-machine.sh" "$@"' in entry
    assert 'if [ "${1:-}" =' in entry
    assert entry.count('if [ "${1:-}" =') == 1
    assert 'readlink -f "${BASH_SOURCE[0]}"' in entry
    assert 'MP_MENU_CANCEL_LABEL="Exit"' in entry
    assert 'ui_confirm "Exit MP-OPT_SERVER"' in entry


def test_management_launcher_resolves_repository_through_symlink(tmp_path: Path):
    """The installed /usr/local/bin-style symlink must resolve back to the repository."""

    root = _server_root()
    launcher = tmp_path / "bin" / "mp-opt"
    launcher.parent.mkdir()
    launcher.symlink_to(root / "manage.sh")
    command = r"""
        script_path="$(readlink -f "$1")"
        root_dir="$(cd "$(dirname "$script_path")" && pwd)"
        test "$root_dir" = "$2"
        test -f "$root_dir/deploy/management/common.sh"
    """
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command, "bash", str(launcher), str(root)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_setup_installs_graphical_encrypted_recovery_and_launcher():
    """Fresh VPS setup must install the TUI, encryption tools and friendly launcher."""

    setup = _read("deploy/setup-server.sh")
    deploy = _read("deploy/deploy.sh")

    for package in ("age", "jq", "dialog", "whiptail"):
        assert package in setup
    assert "/usr/local/bin/mp-opt" in setup
    assert "/usr/local/bin/mp-opt" in deploy
    assert '"$REPO_DIR/manage.sh"' in deploy


def test_tui_menu_uses_controlling_terminal_when_result_is_captured(tmp_path: Path):
    """Captured menu output must not make the full-screen backend disappear."""

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_dialog = fake_bin / "dialog"
    fake_dialog.write_text("#!/bin/sh\nprintf 'snapshots\\n'\n", encoding="utf-8")
    fake_dialog.chmod(0o755)
    command = r"""
        source deploy/management/common.sh
        selected="$(ui_menu 'MP-OPT_SERVER' 'Choose an area' snapshots 'Snapshots')"
        test "$selected" = snapshots
        test "$(mp_tui_backend)" = dialog
    """
    runner = tmp_path / "run-menu-test.sh"
    runner.write_text(command, encoding="utf-8")
    environment = os.environ.copy()
    environment.update({"PATH": f"{fake_bin}:{environment['PATH']}", "MP_TUI": "auto"})
    result = subprocess.run(
        [
            "script",
            "-qec",
            f"bash -Eeuo pipefail {shlex.quote(str(runner))}",
            "/dev/null",
        ],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_interface_geometry_profiles_are_terminal_aware(tmp_path: Path):
    """Large and maximum layouts must use the available terminal dimensions."""

    environment = os.environ.copy()
    environment.update(
        {
            "MP_ROOT": str(tmp_path),
            "MP_HOME": str(tmp_path / "home"),
            "MP_STATE": str(tmp_path / "state"),
            "MP_SNAPSHOTS": str(tmp_path / "snapshots"),
            "MP_TUI": "ansi",
        }
    )
    command = r"""
        source deploy/management/common.sh
        mp_terminal_dimensions() { printf '60 200\n'; }

        MP_UI_SIZE=compact
        test "$(mp_ui_geometry menu)" = '24 86 16'
        test "$(mp_ui_geometry view)" = '28 110 20'

        MP_UI_SIZE=standard
        test "$(mp_ui_geometry menu)" = '42 156 34'

        MP_UI_SIZE=large
        test "$(mp_ui_geometry menu)" = '51 180 43'
        test "$(mp_ui_geometry prompt)" = '22 180 14'

        MP_UI_SIZE=maximum
        test "$(mp_ui_geometry view)" = '58 196 50'

        mp_terminal_dimensions() { printf '10 30\n'; }
        test "$(mp_ui_geometry menu)" = '8 26 1'
    """
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_interface_size_setting_is_protected_and_applies_immediately(tmp_path: Path):
    """The selected layout must persist privately without changing deployment config."""

    environment = os.environ.copy()
    environment.update(
        {
            "MP_ROOT": str(tmp_path),
            "MP_HOME": str(tmp_path / "home"),
            "MP_STATE": str(tmp_path / "state"),
            "MP_SNAPSHOTS": str(tmp_path / "snapshots"),
            "MP_TUI": "ansi",
        }
    )
    command = r"""
        source deploy/management/common.sh
        mp_initialise_paths
        ui_menu() { printf 'maximum\n'; }
        ui_message() { :; }
        mp_configure_interface_size
        test "$(mp_ui_size_profile)" = maximum
        test "$(cat "$MP_UI_SIZE_FILE")" = maximum
        test "$(stat -c '%a' "$MP_UI_SIZE_FILE")" = 600
        grep -Fq '|interface.size|success|maximum|' "$MP_AUDIT_FILE"
        test ! -e "$MP_ROOT/.env"
    """
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_static_logs_are_sanitised_viewed_and_removed(tmp_path: Path):
    """Bounded logs remain visible in the viewer without retaining temporary files."""

    environment = os.environ.copy()
    environment.update(
        {
            "MP_ROOT": str(tmp_path),
            "MP_HOME": str(tmp_path / "home"),
            "MP_STATE": str(tmp_path / "state"),
            "MP_SNAPSHOTS": str(tmp_path / "snapshots"),
            "MP_TUI": "ansi",
        }
    )
    command = r"""
        source deploy/management/common.sh
        source deploy/management/actions.sh
        mp_initialise_paths
        mp_collect_logs() { printf '\033[31mVisible log\033[0m\n'; }
        ui_text_file() {
            stat -c '%a' "$2" > "$MP_STATE/view-mode"
            cp "$2" "$MP_STATE/viewed"
        }
        mp_show_static_logs backend recent 200
        grep -Fxq 'Visible log' "$MP_STATE/viewed"
        ! grep -q $'\033' "$MP_STATE/viewed"
        grep -Fxq '600' "$MP_STATE/view-mode"
        test -z "$(find "$MP_STATE" -maxdepth 1 -name 'logs.*' -print -quit)"
    """
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_failed_and_empty_logs_show_useful_viewer_content(tmp_path: Path):
    """Log command failures and empty selections must never vanish silently."""

    environment = os.environ.copy()
    environment.update(
        {
            "MP_ROOT": str(tmp_path),
            "MP_HOME": str(tmp_path / "home"),
            "MP_STATE": str(tmp_path / "state"),
            "MP_SNAPSHOTS": str(tmp_path / "snapshots"),
            "MP_TUI": "ansi",
        }
    )
    command = r"""
        source deploy/management/common.sh
        source deploy/management/actions.sh
        mp_initialise_paths
        ui_text_file() { printf '%s\n' "$1" > "$MP_STATE/title"; cp "$2" "$MP_STATE/viewed"; }
        mp_collect_logs() { return 7; }
        ! mp_show_static_logs caddy since 30m
        grep -Fq 'Logs failed' "$MP_STATE/title"
        grep -Fxq 'No log entries matched this selection.' "$MP_STATE/viewed"
    """
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_live_log_viewer_streams_stops_producer_and_cleans_file(tmp_path: Path):
    """Live lines must appear before exit and closing must leave no residue."""

    environment = os.environ.copy()
    environment.update(
        {
            "MP_ROOT": str(tmp_path),
            "MP_HOME": str(tmp_path / "home"),
            "MP_STATE": str(tmp_path / "state"),
            "MP_SNAPSHOTS": str(tmp_path / "snapshots"),
            "MP_TUI": "ansi",
        }
    )
    command = r"""
        source deploy/management/common.sh
        source deploy/management/actions.sh
        mp_initialise_paths
        mp_follow_logs() { while true; do printf 'tick\n'; sleep 0.05; done; }
        ui_live_text_file() {
            local attempt
            for attempt in $(seq 1 20); do
                if grep -Fxq 'tick' "$2"; then
                    touch "$MP_STATE/live-line-visible"
                    return 130
                fi
                sleep 0.025
            done
            return 9
        }
        mp_show_live_logs backend
        test -e "$MP_STATE/live-line-visible"
        test -z "$(find "$MP_STATE" -maxdepth 1 -name 'logs.live.*' -print -quit)"
        touch "$MP_STATE/menu-continued"
        test -e "$MP_STATE/menu-continued"
    """
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


def test_live_log_viewer_stops_nested_process_tree(tmp_path: Path):
    """Closing live logs must not orphan nested Compose-like subprocesses."""

    environment = os.environ.copy()
    environment.update(
        {
            "MP_ROOT": str(tmp_path),
            "MP_HOME": str(tmp_path / "home"),
            "MP_STATE": str(tmp_path / "state"),
            "MP_SNAPSHOTS": str(tmp_path / "snapshots"),
            "MP_TUI": "ansi",
        }
    )
    command = r"""
        source deploy/management/common.sh
        source deploy/management/actions.sh
        mp_initialise_paths
        parent_file="$MP_STATE/nested-parent"
        child_file="$MP_STATE/nested-child"
        cleanup_nested() {
            [ ! -s "$child_file" ] || kill -KILL "$(cat "$child_file")" 2>/dev/null || true
            [ ! -s "$parent_file" ] || kill -KILL "$(cat "$parent_file")" 2>/dev/null || true
        }
        trap cleanup_nested EXIT
        mp_follow_logs() {
            MP_TEST_PARENT_FILE="$parent_file" MP_TEST_CHILD_FILE="$child_file" bash -c '
                printf "%s\n" "$BASHPID" > "$MP_TEST_PARENT_FILE"
                bash -c '\''
                    printf "%s\n" "$BASHPID" > "$MP_TEST_CHILD_FILE"
                    while true; do sleep 1; done
                '\'' &
                wait
            '
        }
        ui_live_text_file() {
            local attempt
            for attempt in $(seq 1 40); do
                if [ -s "$parent_file" ] && [ -s "$child_file" ]; then
                    return 0
                fi
                sleep 0.025
            done
            return 9
        }
        mp_show_live_logs backend
        for attempt in $(seq 1 40); do
            if ! kill -0 "$(cat "$parent_file")" 2>/dev/null \
                && ! kill -0 "$(cat "$child_file")" 2>/dev/null; then
                trap - EXIT
                exit 0
            fi
            sleep 0.025
        done
        exit 8
    """
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


def test_live_log_viewer_reports_source_failure(tmp_path: Path):
    """A failed live source must explain its termination inside the viewer."""

    environment = os.environ.copy()
    environment.update(
        {
            "MP_ROOT": str(tmp_path),
            "MP_HOME": str(tmp_path / "home"),
            "MP_STATE": str(tmp_path / "state"),
            "MP_SNAPSHOTS": str(tmp_path / "snapshots"),
            "MP_TUI": "ansi",
        }
    )
    command = r"""
        source deploy/management/common.sh
        source deploy/management/actions.sh
        mp_initialise_paths
        mp_follow_logs() { printf 'Cannot connect to log source\n' >&2; return 7; }
        ui_live_text_file() {
            local attempt
            for attempt in $(seq 1 20); do
                if grep -Fq 'source stopped unexpectedly (exit status 7)' "$2"; then
                    cp "$2" "$MP_STATE/viewed-live-failure"
                    return 0
                fi
                sleep 0.025
            done
            return 9
        }
        mp_show_live_logs backend
        grep -Fxq 'Cannot connect to log source' "$MP_STATE/viewed-live-failure"
        grep -Fq 'source stopped unexpectedly (exit status 7)' "$MP_STATE/viewed-live-failure"
        test -z "$(find "$MP_STATE" -maxdepth 1 -name 'logs.live.*' -print -quit)"
    """
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


def test_live_log_sources_use_the_expected_topology_commands(tmp_path: Path):
    """Every live-log source must select its Compose or host service command."""

    environment = os.environ.copy()
    environment.update({"MP_ROOT": str(tmp_path), "MP_TUI": "ansi"})
    command = r"""
        source deploy/management/common.sh
        source deploy/management/actions.sh
        calls="$1"
        mp_compose_init() { MP_COMPOSE=(fake_compose); }
        fake_compose() { printf 'compose %s\n' "$*" >> "$calls"; }
        sudo() { printf 'sudo %s\n' "$*" >> "$calls"; }
        mp_follow_logs backend
        mp_follow_logs db
        mp_follow_logs all
        mp_caddy_mode() { printf 'container\n'; }
        mp_follow_logs caddy
        mp_caddy_mode() { printf 'host\n'; }
        mp_follow_logs caddy
    """
    calls = tmp_path / "live-log-calls"
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command, "bash", str(calls)],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "compose logs -f --tail 100 backend",
        "compose logs -f --tail 100 db",
        "compose logs -f --tail 100",
        "compose logs -f --tail 100 caddy",
        "sudo journalctl -u caddy -f -n 100",
    ]


def test_management_dashboard_refuses_non_interactive_execution():
    """The menu must fail clearly instead of attempting mutations without a TTY."""

    result = subprocess.run(
        ["bash", "manage.sh"],
        cwd=_server_root(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "requires an interactive terminal" in result.stderr


def test_command_output_window_cleans_success_and_failure_files(tmp_path: Path):
    """Long-running command reports must be sanitised and removed after viewing."""

    environment = os.environ.copy()
    environment.update(
        {
            "MP_ROOT": str(tmp_path),
            "MP_HOME": str(tmp_path / "home"),
            "MP_STATE": str(tmp_path / "state"),
            "MP_SNAPSHOTS": str(tmp_path / "snapshots"),
            "MP_TUI": "ansi",
        }
    )
    command = r"""
        source deploy/management/common.sh
        mp_initialise_paths
        ui_text_file() { test -f "$2"; }
        ui_run_command Test Running bash -c "printf '\033[31mcomplete\033[0m\n'"
        ! ui_run_command Test Running bash -c "printf 'failed\n'; exit 7"
        test -z "$(find "$MP_STATE" -maxdepth 1 -name 'command-output.*' -print -quit)"
    """
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_production_configurator_delegates_to_guarded_wizard():
    """The compatibility configurator must never retain its old overwrite implementation."""

    wrapper = _read("configure-production.sh")
    actions = _read("deploy/management/actions.sh")

    assert 'exec "$ROOT_DIR/manage.sh"' in wrapper
    assert "An existing .env was detected" in actions
    assert "Configure SMTP activation email now?" in actions
    assert "You can safely skip this" in actions
    assert 'printf \'%s\' "$smtp_token" > "$staging/secrets/smtp_token"' in actions
    assert ': > "$staging/secrets/evidence_github_fine_grained_token"' in actions
    assert "SMTP_TOKEN=" not in _read(".env.example")
    assert "SECRET_KEY=" not in _read(".env.example")
    assert "ROOT_BOOTSTRAP_TOKEN=" not in _read(".env.example")
    assert "VAPID_PRIVATE_KEY=" not in _read(".env.example")


def test_smtp_test_reports_the_safe_one_off_process_error():
    """A failed Docker exec must not direct the operator to unrelated backend logs."""

    actions = _read("deploy/management/actions.sh")

    assert "MP_SMTP_ERROR:{exc.code}:{exc}" in actions
    assert "The SMTP test process failed before returning a safe diagnostic." in actions
    assert "Review Configuration > SMTP and retry the guarded test." in actions
    assert "Review backend logs." not in actions


def test_redacted_configuration_hides_database_urls_and_secret_keys(tmp_path: Path):
    """Diagnostics must not reveal passwords embedded inside DATABASE_URL."""

    env_file = tmp_path / ".env"
    env_file.write_text(
        "DOMAIN=mp-opt.net\n"
        "DATABASE_URL=postgresql://masterplan:very-secret@db/masterplan\n"
        "POSTGRES_PASSWORD=very-secret\n"
        "SMTP_TOKEN=provider-secret\n",
        encoding="utf-8",
    )
    command = (
        'source deploy/management/common.sh; '
        f'mp_redacted_configuration "{env_file}"'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=_server_root(),
        check=True,
        capture_output=True,
        text=True,
    )

    assert "very-secret" not in result.stdout
    assert "provider-secret" not in result.stdout
    assert result.stdout.count("<redacted>") == 3
    assert "mp-opt.net" in result.stdout


def test_common_helpers_reject_traversal_and_hash_management_receipts(tmp_path: Path):
    """Snapshot names are path-safe and activity receipts form a SHA-256 chain."""

    environment = os.environ.copy()
    environment.update(
        {
            "MP_ROOT": str(tmp_path),
            "MP_HOME": str(tmp_path / "home"),
            "MP_STATE": str(tmp_path / "state"),
            "MP_SNAPSHOTS": str(tmp_path / "snapshots"),
            "MP_TUI": "ansi",
        }
    )
    command = """
        source deploy/management/common.sh
        mp_initialise_paths
        mp_validate_snapshot_name safe_name-1
        ! mp_validate_snapshot_name ../unsafe
        ! mp_validate_snapshot_name 'unsafe/name'
        mp_audit snapshot.create success database:safe_name-1
        mp_audit snapshot.verify success safe_name-1
        test "$(wc -l < "$MP_AUDIT_FILE")" -eq 2
        first=$(sed -n '1s/|.*//p' "$MP_AUDIT_FILE")
        second_previous=$(sed -n '2s/^[^|]*|[^|]*|[^|]*|[^|]*|[^|]*|[^|]*|//p' "$MP_AUDIT_FILE")
        test "$first" = "$second_previous"
    """
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_menu_actions_keep_strict_failure_handling_without_closing_menu(tmp_path: Path):
    """A failed command stops its action but remains recoverable by the dashboard."""

    environment = os.environ.copy()
    environment.update(
        {
            "MP_ROOT": str(tmp_path),
            "MP_HOME": str(tmp_path / "home"),
            "MP_STATE": str(tmp_path / "state"),
            "MP_SNAPSHOTS": str(tmp_path / "snapshots"),
            "MP_TUI": "ansi",
        }
    )
    command = r"""
        source deploy/management/common.sh
        mp_initialise_paths
        unsafe_action() {
            false
            touch "$MP_STATE/should-not-exist"
        }
        mp_action_permission_profile() {
            test "${1:-}" = unsafe_action || return 1
            printf 'observe\n'
        }
        mp_run_action unsafe_action
        test ! -e "$MP_STATE/should-not-exist"
        grep -Fq '|menu.action|failed|unsafe_action|' "$MP_AUDIT_FILE"
        touch "$MP_STATE/dashboard-continued"
        test -e "$MP_STATE/dashboard-continued"
    """
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_initial_wizard_can_skip_smtp_without_putting_secrets_in_env(tmp_path: Path):
    """First setup creates protected secret files while SMTP remains optional."""

    management = tmp_path / "deploy" / "management"
    management.mkdir(parents=True)
    (management / "instance_key.py").write_bytes(
        (_server_root() / "deploy" / "management" / "instance_key.py").read_bytes()
    )
    environment = os.environ.copy()
    environment.update(
        {
            "MP_ROOT": str(tmp_path),
            "MP_HOME": str(tmp_path / "home"),
            "MP_STATE": str(tmp_path / "state"),
            "MP_SNAPSHOTS": str(tmp_path / "snapshots"),
            "MP_TUI": "ansi",
        }
    )
    command = r"""
        source deploy/management/common.sh
        source deploy/management/actions.sh
        mp_initialise_paths
        mp_require_commands() { return 0; }
        mp_compose_validate() { return 0; }
        mp_audit() { return 0; }
        ui_message() { return 0; }
        ui_input() {
            case "$2" in
                "Public application domain (for example schedule.example.org)") printf '%s\n' 'schedule.example.org' ;;
                "Passkey application name") printf '%s\n' 'Masterplan Access' ;;
                "VAPID contact email (for example admin@example.org)") printf '%s\n' 'admin@example.org' ;;
                *) return 1 ;;
            esac
        }
        ui_confirm() {
            case "$1" in
                "Database"|"Permitted data"|"Review configuration"|"Commission instance signing identity") return 0 ;;
                "Optional activation email"|"Recovery encryption") return 1 ;;
                *) return 1 ;;
            esac
        }
        mp_guided_initial_configuration
        test -s "$MP_ROOT/.env"
        test -s "$MP_ROOT/secrets/database_password"
        test -s "$MP_ROOT/secrets/ip_hmac_key"
        test -s "$MP_ROOT/secrets/secret_key"
        test -s "$MP_ROOT/secrets/vapid_private_key"
        test -s "$MP_ROOT/secrets/root_bootstrap_token"
        test -s "$MP_ROOT/secrets/evidence_signing_key"
        test -s "$MP_ROOT/secrets/evidence_signing_key.pub"
        test -e "$MP_ROOT/secrets/smtp_token"
        test ! -s "$MP_ROOT/secrets/smtp_token"
        test -e "$MP_ROOT/secrets/evidence_github_fine_grained_token"
        test ! -s "$MP_ROOT/secrets/evidence_github_fine_grained_token"
        ! grep -Eq '^(DATABASE_URL|POSTGRES_PASSWORD|SECRET_KEY|IP_HMAC_KEY|ROOT_BOOTSTRAP_TOKEN|VAPID_PRIVATE_KEY|SMTP_TOKEN)=' "$MP_ROOT/.env"
        grep -Fxq 'SMTP_HOST=' "$MP_ROOT/.env"
        grep -Eq '^MP_INSTANCE_ID=[0-9a-f-]{36}$' "$MP_ROOT/.env"
        grep -Fxq 'GOVERNANCE_SETUP_ACK_VERSION=2026-07-27' "$MP_ROOT/.env"
        test "$(stat -c '%a' "$MP_ROOT/.env")" = 600
        test "$(stat -c '%a' "$MP_ROOT/secrets/secret_key")" = 600
    """
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_initial_wizard_refuses_orphaned_non_empty_secrets(tmp_path: Path):
    """Missing .env must not authorise overwriting existing secret material."""

    secrets = tmp_path / "secrets"
    secrets.mkdir()
    existing = secrets / "secret_key"
    existing.write_text("existing-secret", encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        {
            "MP_ROOT": str(tmp_path),
            "MP_HOME": str(tmp_path / "home"),
            "MP_STATE": str(tmp_path / "state"),
            "MP_SNAPSHOTS": str(tmp_path / "snapshots"),
            "MP_TUI": "ansi",
        }
    )
    command = r"""
        source deploy/management/common.sh
        source deploy/management/actions.sh
        mp_initialise_paths
        ui_error() { printf '%s\n' "$1" >&2; }
        ! mp_guided_initial_configuration
        test ! -e "$MP_ROOT/.env"
        test "$(cat "$MP_ROOT/secrets/secret_key")" = existing-secret
    """
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Nothing was overwritten" in result.stderr


def test_legacy_secret_migration_requires_exact_file_match(tmp_path: Path):
    """Legacy env secrets are removed only after byte-for-byte file comparison."""

    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (tmp_path / ".env").write_text(
        "DOMAIN=mp-opt.net\nSECRET_KEY=matching-secret\n",
        encoding="utf-8",
    )
    (secrets / "secret_key").write_text("matching-secret", encoding="utf-8")
    for name in ("vapid_private_key", "root_bootstrap_token", "smtp_token"):
        (secrets / name).write_text("", encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        {
            "MP_ROOT": str(tmp_path),
            "MP_HOME": str(tmp_path / "home"),
            "MP_STATE": str(tmp_path / "state"),
            "MP_SNAPSHOTS": str(tmp_path / "snapshots"),
            "MP_TUI": "ansi",
        }
    )
    command = r"""
        source deploy/management/common.sh
        source deploy/management/actions.sh
        mp_initialise_paths
        ui_confirm() { return 0; }
        ui_message() { return 0; }
        ui_error() { printf '%s\n' "$1" >&2; }
        mp_recreate_backend() { return 0; }
        mp_audit() { return 0; }
        before=$(sha256sum "$MP_ROOT/secrets/secret_key" | awk '{print $1}')
        mp_migrate_legacy_env_secrets
        after=$(sha256sum "$MP_ROOT/secrets/secret_key" | awk '{print $1}')
        test "$before" = "$after"
        ! grep -q '^SECRET_KEY=' "$MP_ROOT/.env"
        grep -Fxq 'DOMAIN=mp-opt.net' "$MP_ROOT/.env"
    """
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_snapshots_are_encrypted_versioned_and_hash_verified():
    """Recovery archives carry internal and external hashes and require age identities."""

    snapshots = _read("deploy/management/snapshots.sh")
    common = _read("deploy/management/common.sh")
    portable = _read("deploy/management/portable_snapshots.sh")
    rotation = _read("deploy/management/recovery_rotation.sh")
    ha = _read("deploy/management/ha.sh")

    assert "mp-opt-snapshot-v2" in snapshots
    assert "mp-opt-snapshot-receipt-v2" in snapshots
    assert "mp-opt-snapshot-v1" not in snapshots
    assert "mp-opt-snapshot-receipt-v1" not in snapshots
    assert 'age -r "$recipient"' in snapshots
    assert 'age -d -i "$identity_file"' in snapshots
    assert '| tar -tf - > "$members" || return 1' in snapshots
    assert '| tar -tvf - > "$member_types" || return 1' in snapshots
    assert "sort \"$list_file\" | uniq -d" in snapshots
    assert '| tar -C "$destination" -xf - || return 1' in snapshots
    assert "sha256sum" in snapshots
    assert "pg_restore --list" in snapshots
    assert 'verification: "encrypted"' in snapshots
    assert '.verification = "deep-verified"' in snapshots
    assert "/dev/shm" in common
    assert "AGE-SECRET-KEY-1" in common
    assert "HA_RECOVERY_STORAGE_MODE" in common
    assert "manual_portable" in common
    assert "ssh_archive" in common
    assert "mp-opt-manual-recovery-export-v1" in portable
    assert "operator-sha256-confirmed" in portable
    assert "package_sha256" in portable
    assert "mp_portable_record_confirmed_export" in portable
    assert "mp_rotation_finalize_portable_export" in portable
    assert "awaiting-portable-export" in rotation
    assert "ROTATE WITHOUT OLD KEY" in rotation
    assert "ROTATE RECOVERY KEY" in rotation
    assert 'mp_snapshot_copy_off_server "$baseline"' in rotation
    assert "Manual workstation export" in ha
    assert "Passwordless SSH verification failed" in ha
    assert "local_path" not in rotation
    assert "AGE-SECRET-KEY-" not in rotation


def test_destructive_actions_require_deep_snapshot_and_exact_phrase_first():
    """Root reset, wipe, domain and rotations cannot mutate before guarded recovery."""

    actions = _read("deploy/management/actions.sh")

    expectations = {
        "mp_reset_root_admin()": "RESET ROOT ADMIN",
        "mp_wipe_database()": "WIPE DATABASE",
        "mp_change_domain()": "CHANGE DOMAIN TO $new_domain",
        "mp_rotate_database_password()": "ROTATE DATABASE PASSWORD",
        "mp_rotate_application_secret()": "ROTATE APPLICATION SECRET",
        "mp_rotate_ip_hmac_key()": "ROTATE IP HMAC KEY",
        "mp_rotate_vapid()": "ROTATE VAPID",
    }
    for marker, phrase in expectations.items():
        start = actions.index(marker)
        next_function = actions.find("\n# ", start + len(marker))
        body = actions[start : next_function if next_function != -1 else None]
        assert "mp_prepare_guard_snapshot" in body
        assert phrase in body
        assert body.index("mp_prepare_guard_snapshot") < body.index(phrase)


def test_root_reset_is_authentication_scoped_and_domain_change_is_topology_aware():
    """Root recovery is scoped while RP changes preserve the active proxy topology."""

    actions = _read("deploy/management/actions.sh")
    root_start = actions.index("mp_reset_root_admin()")
    root_end = actions.index("mp_disable_root_bootstrap()")
    root_body = actions[root_start:root_end]
    domain_start = actions.index("mp_change_domain()")
    domain_end = actions.index("mp_rotate_database_password()")
    domain_body = actions[domain_start:domain_end]

    assert "DELETE FROM webauthn_credentials WHERE user_id = root_id" in root_body
    assert "DELETE FROM auth_sessions WHERE user_id = root_id" in root_body
    assert "DELETE FROM users" not in root_body
    assert "DELETE FROM webauthn_credentials;" in domain_body
    assert "UPDATE users SET is_activated = FALSE" in domain_body
    assert 'if [ "$caddy_mode" = "host" ]; then' in domain_body
    assert 'label == old " {" && !done {sub(old, new); done=1}' in domain_body
    assert '"$MP_HOST_CADDYFILE"' in domain_body
    assert "mp_caddy_reload" in domain_body
    assert "mp_caddy_validate" in domain_body


def test_database_wipe_recreates_schema_then_applies_committed_migrations():
    """A clean database must reach the same migrated schema as an upgraded one."""

    actions = _read("deploy/management/actions.sh")
    start = actions.index("mp_wipe_database()")
    end = actions.index("mp_change_domain()")
    body = actions[start:end]

    create_database = body.index("createdb -U masterplan masterplan")
    ensure_base_schema = body.index("mp_ensure_base_schema")
    apply_migrations = body.index("mp_apply_migrations")
    recreate_backend = body.index("mp_recreate_backend", apply_migrations)
    assert create_database < ensure_base_schema < apply_migrations < recreate_backend
    assert 'mp_guard_rollback "Fresh database schema migration failed."' in body

    common = _read("deploy/management/common.sh")
    helper_start = common.index("mp_ensure_base_schema()")
    helper_end = common.index("# Append a sanitised", helper_start)
    helper_body = common[helper_start:helper_end]
    assert "python -m app.tools.bootstrap_schema" in helper_body
    assert "up -d --no-deps --force-recreate backend" not in helper_body


def test_restore_verifies_then_creates_verified_rollback_before_apply():
    """Snapshot restore must prove both selected and rollback archives before mutation."""

    snapshots = _read("deploy/management/snapshots.sh")
    start = snapshots.index("mp_snapshot_restore_interactive()")
    end = snapshots.index("mp_snapshot_delete_interactive()")
    body = snapshots[start:end]

    first_verify = body.index('mp_snapshot_verify_path "$selected"')
    pre_create = body.index("mp_snapshot_create full")
    pre_verify = body.index('mp_snapshot_verify_path "$pre_snapshot"')
    apply_selected = body.index('mp_snapshot_apply "$selected"')
    assert first_verify < pre_create < pre_verify < apply_selected
    assert 'mp_snapshot_apply "$pre_snapshot"' in body
    assert body.index("ui_clear_terminal") < pre_create


def test_snapshot_deletion_uses_fixed_confirmation_phrase():
    """Deleting the selected snapshot must not require retyping its long generated name."""

    snapshots = _read("deploy/management/snapshots.sh")
    start = snapshots.index("mp_snapshot_delete_interactive()")
    end = snapshots.index("mp_snapshot_list_interactive()")
    body = snapshots[start:end]

    assert '"DELETE SNAPSHOT"' in body
    assert '"DELETE $label"' not in body


def test_management_audit_chain_verifier_detects_tampering(tmp_path: Path):
    """Audit verification must accept intact receipts and reject edited history."""
    common = _server_root() / "deploy" / "management" / "common.sh"
    audit_file = tmp_path / "management.log"
    command = f'''
        export MP_AUDIT_FILE="{_bash_path(audit_file)}"
        source "{_bash_path(common)}"
        : > "$MP_AUDIT_FILE"
        mp_audit "drill.start" "success" "baseline"
        mp_audit "drill.checkpoint" "success" "before-wipe"
        mp_verify_audit_chain
        sed -i 's/before-wipe/after-wipe/' "$MP_AUDIT_FILE"
        ! mp_verify_audit_chain
    '''

    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_management_audit_rejects_unstructured_or_delimiter_injected_fields(tmp_path: Path):
    """Audit fields must stay bounded and preserve the seven-column chain format."""
    common = _server_root() / "deploy" / "management" / "common.sh"
    audit_file = tmp_path / "management.log"
    command = f'''
        export MP_AUDIT_FILE="{_bash_path(audit_file)}"
        source "{_bash_path(common)}"
        mp_publish_audit_head() {{ :; }}
        : > "$MP_AUDIT_FILE"
        mp_audit "drill.start" "success" "baseline"
        ! mp_audit "Bad Action" "success" "baseline"
        ! mp_audit "drill.start" "unknown" "baseline"
        ! mp_audit "drill.start" "success" $'line\\nbreak'
        ! mp_audit "drill.start" "success" "field|injection"
        [ "$(wc -l < "$MP_AUDIT_FILE")" -eq 1 ]
        awk -F'|' 'NF != 7 {{ exit 1 }}' "$MP_AUDIT_FILE"
        mp_verify_audit_chain
    '''

    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", command],
        cwd=_server_root(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_recovery_evidence_is_redacted_hashed_and_available_from_menu():
    """Recovery checkpoints must expose hashes and metadata without raw values."""
    actions = _read("deploy/management/actions.sh")
    common = _read("deploy/management/common.sh")
    menu = _read("manage.sh")

    start = actions.index("mp_collect_recovery_evidence()")
    end = actions.index("mp_collect_recovery_evidence_interactive()")
    body = actions[start:end]
    assert "database-fingerprints.tsv" in body
    assert "protected-files.tsv" in body
    assert "schema.sha256" in body
    assert "snapshot-archives.sha256" in body
    assert "evidence.sha256" in body
    assert "sha256sum -c evidence.sha256" in body
    assert "credential_id, public_key" in body
    assert 'cat "$MP_ROOT/.env"' not in body
    assert 'cat "$file"' not in body
    assert "session_token" not in body
    assert "csrf_token" not in body
    assert "mp_verify_audit_chain()" in common
    assert '"recovery-evidence" "Create a hashed recovery-test checkpoint"' in menu
    assert '"audit-verify" "Verify the management audit hash chain"' in menu


def test_accountability_export_is_copyable_and_never_publishes_automatically():
    root = _server_root()
    evidence = (root / "deploy/management/evidence.sh").read_text(encoding="utf-8")
    menu = (root / "manage.sh").read_text(encoding="utf-8")
    assert "ui_copyable_terminal_text \"Complete accountability evidence exported\"" in evidence
    assert "sudo -n env PYTHONDONTWRITEBYTECODE=1 python3" in evidence
    assert "export-tui" in evidence
    assert "zip_sha256" in evidence
    assert "mounted evidence export changed after backend verification" in evidence
    assert "accountability.evidence.sha256" in evidence
    assert "VERIFYING.txt" in evidence
    assert "scp deploy@" in evidence
    assert "SHA-256:" in evidence
    assert "stage-git" in evidence
    assert "git push" in evidence
    assert "intentionally not created automatically" in evidence
    assert "gh repo create" not in evidence
    assert '"accountability" "Signed deletion accountability evidence"' in menu
