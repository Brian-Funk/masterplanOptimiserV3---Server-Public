#!/usr/bin/env bash
# MP-OPT_SERVER graphical management entry point for SSH operators.
set -Eeuo pipefail
umask 077

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
ROOT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
export MP_ROOT="${MP_ROOT:-$ROOT_DIR}"

# shellcheck source=deploy/management/common.sh
source "$MP_ROOT/deploy/management/common.sh"
# shellcheck source=deploy/management/snapshots.sh
source "$MP_ROOT/deploy/management/snapshots.sh"
# shellcheck source=deploy/management/portable_snapshots.sh
source "$MP_ROOT/deploy/management/portable_snapshots.sh"
# shellcheck source=deploy/management/recovery_rotation.sh
source "$MP_ROOT/deploy/management/recovery_rotation.sh"
# shellcheck source=deploy/management/ha.sh
source "$MP_ROOT/deploy/management/ha.sh"
# shellcheck source=deploy/management/actions.sh
source "$MP_ROOT/deploy/management/actions.sh"
# shellcheck source=deploy/management/evidence.sh
source "$MP_ROOT/deploy/management/evidence.sh"
# shellcheck source=deploy/management/setup_v2.sh
source "$MP_ROOT/deploy/management/setup_v2.sh"

mp_require_interactive_terminal
mp_initialise_paths

# Remove any transient private recovery identity when the menu exits unexpectedly.
mp_cleanup() {
    mp_remove_identity_file "${MP_GUARD_IDENTITY:-}"
    # Leave no full-screen menu or command output behind in the operator's
    # terminal after any normal, failed or interrupted management session.
    if mp_has_terminal; then
        clear </dev/tty >/dev/tty 2>/dev/null || true
    fi
}
trap mp_cleanup EXIT TERM
trap ':' INT

# Open deployment and service controls.
menu_services() {
    local choice
    while true; do
        choice="$(ui_menu "Deploy and services" "Choose an operation" \
            "status" "Show service and public health status" \
            "deploy" "Pull and deploy the latest release" \
            "test-status" "Show signed baseline and unsigned test receipt" \
            "test-deploy" "Deploy an exact pushed commit (test policy only)" \
            "test-rollback" "Roll back the current unsigned test build" \
            "test-restore" "Return both nodes to the signed baseline" \
            "start" "Start application services" \
            "restart" "Restart all application services" \
            "backend" "Recreate only the backend" \
            "frontend" "Rebuild the static frontend" \
            "stop" "Stop application services" \
            "back" "Return to the main menu")" || return 0
        case "$choice" in
            status) mp_run_action mp_service_status ;;
            deploy) mp_run_action mp_deploy_latest ;;
            test-status) mp_run_action mp_test_deployment_status ;;
            test-deploy) mp_run_action mp_test_deployment_apply ;;
            test-rollback) mp_run_action mp_test_deployment_rollback ;;
            test-restore) mp_run_action mp_test_deployment_restore_signed ;;
            start|restart|backend|stop) mp_run_action mp_service_action "$choice" ;;
            frontend) mp_run_action mp_rebuild_frontend ;;
            back|"") return 0 ;;
        esac
    done
}

# Open safe static and runtime configuration controls.
menu_configuration() {
    local choice
    while true; do
        choice="$(ui_menu "Configuration" "Choose a setting group" \
            "show" "View redacted configuration and file metadata" \
            "crypto-inventory" "View non-secret key and credential inventory status" \
            "storage-checklist" "Review provider and workstation storage controls" \
            "deployment-policy" "Choose signed-production or disposable-test policy" \
            "migrate-secrets" "Remove verified legacy secrets from .env" \
            "smtp" "Configure or update SMTP" \
            "smtp-test" "Send a token-free SMTP test email" \
            "smtp-disable" "Disable SMTP activation email" \
            "app-name" "Change the passkey application display name" \
            "runtime" "Manage bounded runtime security settings" \
            "recovery-key" "Configure or safely rotate snapshot recovery encryption" \
            "recovery-resume" "Resume a pending manual recovery-key rotation" \
            "db-password" "Rotate the internal database password" \
            "app-secret" "Rotate the application secret" \
            "ip-hmac-key" "Rotate the IP-pseudonymisation HMAC key" \
            "vapid" "Rotate VAPID and clear push subscriptions" \
            "domain" "Advanced application domain change" \
            "back" "Return to the main menu")" || return 0
        case "$choice" in
            show) mp_run_action mp_show_configuration ;;
            crypto-inventory) mp_run_action mp_cryptographic_inventory ;;
            storage-checklist) mp_run_action mp_storage_security_checklist ;;
            deployment-policy) mp_run_action mp_manage_deployment_policy ;;
            migrate-secrets) mp_run_action mp_migrate_legacy_env_secrets ;;
            smtp) mp_run_action mp_configure_smtp ;;
            smtp-test) mp_run_action mp_send_smtp_test ;;
            smtp-disable) mp_run_action mp_disable_smtp ;;
            app-name) mp_run_action mp_change_application_name ;;
            runtime) mp_run_action mp_manage_runtime_settings ;;
            recovery-key) mp_run_action mp_configure_recovery_recipient ;;
            recovery-resume) mp_run_action mp_rotation_resume_pending ;;
            db-password) mp_run_action mp_rotate_database_password ;;
            app-secret) mp_run_action mp_rotate_application_secret ;;
            ip-hmac-key) mp_run_action mp_rotate_ip_hmac_key ;;
            vapid) mp_run_action mp_rotate_vapid ;;
            domain) mp_run_action mp_change_domain ;;
            back|"") return 0 ;;
        esac
    done
}

# Open encrypted snapshot creation and recovery controls.
menu_snapshots() {
    local choice
    while true; do
        choice="$(ui_menu "Snapshots and recovery" "All archives are encrypted with the configured age recipient" \
            "create-db" "Create a named database snapshot" \
            "create-secrets" "Create a named configuration and secrets snapshot" \
            "create-full" "Create a named complete recovery snapshot" \
            "list" "List snapshot receipts and archive hashes" \
            "verify" "Deep-verify a snapshot with the operator-held identity" \
            "export" "Export one portable snapshot to a workstation" \
            "import" "Import one portable snapshot from a workstation" \
            "restore" "Restore a verified snapshot with rollback protection" \
            "delete" "Permanently delete a selected snapshot" \
            "back" "Return to the main menu")" || return 0
        case "$choice" in
            create-db) mp_run_action mp_snapshot_create_interactive database ;;
            create-secrets) mp_run_action mp_snapshot_create_interactive secrets ;;
            create-full) mp_run_action mp_snapshot_create_interactive full ;;
            list) mp_run_action mp_snapshot_list_interactive ;;
            verify) mp_run_action mp_snapshot_verify_interactive ;;
            export) mp_run_action mp_snapshot_export_portable_interactive ;;
            import) mp_run_action mp_snapshot_import_portable_interactive ;;
            restore) mp_run_action mp_snapshot_restore_interactive ;;
            delete) mp_run_action mp_snapshot_delete_interactive ;;
            back|"") return 0 ;;
        esac
    done
}

# Open root-only authentication recovery controls.
menu_root_recovery() {
    local choice
    while true; do
        choice="$(ui_menu "Root administrator recovery" "These actions preserve application data" \
            "reset" "Reset root passkeys and sessions" \
            "disable" "Disable bootstrap after registering a root passkey" \
            "status" "Show public bootstrap status" \
            "back" "Return to the main menu")" || return 0
        case "$choice" in
            reset) mp_run_action mp_reset_root_admin ;;
            disable) mp_run_action mp_disable_root_bootstrap ;;
            status) mp_run_action mp_show_report "Bootstrap status" curl -fsS --max-time 10 "https://$(mp_env_get DOMAIN)/api/v1/passkey/bootstrap-status" ;;
            back|"") return 0 ;;
        esac
    done
}

# Open database inspection, snapshot and destructive reset controls.
menu_database() {
    local choice
    while true; do
        choice="$(ui_menu "Database" "Choose an operation" \
            "status" "Show database size and table statistics" \
            "snapshot" "Create an encrypted database snapshot" \
            "restore" "Restore a database or full snapshot" \
            "wipe" "Completely wipe and recreate the database" \
            "back" "Return to the main menu")" || return 0
        case "$choice" in
            status) mp_run_action mp_database_status ;;
            snapshot) mp_run_action mp_snapshot_create_interactive database ;;
            restore) mp_run_action mp_snapshot_restore_interactive ;;
            wipe) mp_run_action mp_wipe_database ;;
            back|"") return 0 ;;
        esac
    done
}

# Open validation, diagnostics and safe storage maintenance controls.
menu_maintenance() {
    local choice
    while true; do
        choice="$(ui_menu "Maintenance" "Choose a maintenance action" \
            "interface-size" "Change the terminal interface size" \
            "validate" "Validate Compose, Caddy, health and permissions" \
            "diagnostics" "Create a redacted diagnostics report" \
            "recovery-evidence" "Create a hashed recovery-test checkpoint" \
            "audit" "View the hash-chained management activity log" \
            "audit-verify" "Verify the management audit hash chain" \
            "snapshot-hashes" "Verify every encrypted archive SHA-256" \
            "docker" "Show Docker disk usage" \
            "prune" "Prune unused Docker build cache" \
            "back" "Return to the main menu")" || return 0
        case "$choice" in
            interface-size) mp_run_action mp_configure_interface_size ;;
            validate) mp_run_action mp_validate_installation ;;
            diagnostics) mp_run_action mp_diagnostics ;;
            recovery-evidence) mp_run_action mp_collect_recovery_evidence_interactive ;;
            audit) mp_run_action ui_text_file "Management activity" "$MP_AUDIT_FILE" ;;
            audit-verify)
                if mp_verify_audit_chain; then
                    ui_message "Management audit" "The complete hash chain is valid."
                else
                    ui_error "The management audit hash chain is invalid."
                fi
                ;;
            snapshot-hashes) mp_run_action mp_snapshot_verify_outer_all ;;
            docker) mp_run_action mp_show_report "Docker disk usage" docker system df ;;
            prune) mp_run_action mp_prune_build_cache ;;
            back|"") return 0 ;;
        esac
    done
}

# Verify and export non-identifying signed deletion accountability evidence.
menu_accountability() {
    local choice
    while true; do
        choice="$(ui_menu "Accountability evidence" "Signed proof of controlled deletion steps and declared limitations" \
            "instance-key" "Inspect and verify the instance signing fingerprint" \
            "trust-keys" "Show controller and processor registration boundaries" \
            "verify" "Verify the complete local signed chain" \
            "export" "Create a self-contained portable bundle and show copy commands" \
            "git-status" "Show non-secret automatic archive status" \
            "git-configure" "Configure or rotate a Fine-grained GitHub personal access token" \
            "git-test" "Test private repository access and readiness" \
            "git-disable" "Delete the token and disable automatic archival" \
            "git-retry" "Retry safe failed archive submissions" \
            "git-manual" "Show manual workstation Git archive commands" \
            "back" "Return to the main menu")" || return 0
        case "$choice" in
            instance-key) mp_run_action mp_instance_key_status ;;
            trust-keys) mp_run_action mp_trust_key_guidance ;;
            verify) mp_run_action mp_evidence_verify ;;
            export) mp_run_action mp_evidence_export_bundle ;;
            git-status) mp_run_action mp_evidence_git_status ;;
            git-configure) mp_run_action mp_evidence_git_configure ;;
            git-test) mp_run_action mp_evidence_git_test_saved ;;
            git-disable) mp_run_action mp_evidence_git_disable ;;
            git-retry) mp_run_action mp_evidence_git_retry ;;
            git-manual) mp_run_action mp_evidence_git_guidance ;;
            back|"") return 0 ;;
        esac
    done
}

# Open symmetric cluster configuration and guarded recovery controls.
menu_high_availability() {
    local choice
    while true; do
        choice="$(ui_menu "High availability" "Symmetric provider-neutral lease and point-in-time replication." \
            "overview" "Show lease, peer and replication state" \
            "verification" "Active HA verification readiness" \
            "selftests" "Run isolated bundle and write-fencing tests" \
            "replicate" "Send a complete verified copy to the peer now" \
            "peer-key" "Change the peer age encryption recipient" \
            "replace" "Replace a lost standby with a one-time join code" \
            "dns-migrate" "Migrate a legacy Cloudflare load balancer to DNS-only routing" \
            "dns-cleanup" "Delete legacy routing after its seven-day rollback window" \
            "cloudflare-retire" "Remove HA DNS records and Worker state" \
            "archive" "Choose manual workstation or automatic SSH recovery storage" \
            "alerts" "Configure operational SMTP alerts" \
            "smtp-verify" "Verify SMTP authentication and delivery from both nodes" \
            "switchover" "Replicate and hand ownership to the peer" \
            "automatic" "Enable or disable gated automatic failover" \
            "back" "Return to the main menu")" || return 0
        case "$choice" in
            overview) mp_run_action mp_ha_overview ;;
            verification) mp_run_action mp_ha_active_verification_readiness ;;
            selftests) mp_run_action mp_ha_run_selftests ;;
            replicate) mp_run_action mp_ha_replicate_now ;;
            peer-key) mp_run_action mp_ha_configure_peer_recipient ;;
            replace) mp_run_action mp_setup_replace_standby ;;
            dns-migrate) mp_run_action mp_setup_migrate_legacy_load_balancer ;;
            dns-cleanup) mp_run_action mp_setup_cleanup_legacy_load_balancer ;;
            cloudflare-retire) mp_run_action mp_setup_decommission_cloudflare ;;
            archive) mp_run_action mp_ha_configure_archive_target ;;
            alerts) mp_run_action mp_ha_configure_alert_recipient ;;
            smtp-verify) mp_run_action mp_ha_verify_smtp_both_nodes ;;
            switchover) mp_run_action mp_ha_planned_switchover ;;
            automatic) mp_run_action mp_ha_automatic_failover ;;
            back|"") return 0 ;;
        esac
    done
}

# Launch first-run setup or the persistent management dashboard.
main() {
    local resume_attempted=false
    mp_banner
    mp_run_action mp_offer_dependency_install
    if [ -f "$MP_SETUP_V2_STATE" ] \
        && [ "$(jq -r '.state // empty' "$MP_SETUP_V2_STATE" 2>/dev/null || true)" = in_progress ]; then
        if ui_confirm "Resume commissioning" "An incomplete setup workflow was found. Resume from its last verified checkpoint now?"; then
            resume_attempted=true
            mp_run_action mp_setup_v2
        else
            ui_message "Commissioning paused" "No checkpoint was changed. Run mp-opt again when you are ready to resume."
            return 0
        fi
    fi
    if [ ! -f "$MP_ROOT/.env" ] && [ -f "$MP_HA_CONFIG" ]; then
        ui_message "Waiting for the primary" "This VPS joined the HA pair. Its receiver is ready for the first complete encrypted copy; application data intentionally does not exist until the current primary sends that copy."
        printf 'This HA node is waiting for its first verified replication.\n'
        return 0
    elif [ ! -f "$MP_ROOT/.env" ]; then
        [ "$resume_attempted" = false ] || return 0
        mp_banner
        local setup_status
        set +e
        (
            set -Eeuo pipefail
            mp_setup_v2
        )
        setup_status=$?
        set -e
        [ "$setup_status" -eq 0 ] || exit 1
    fi
    if [ -f "$MP_HA_CONFIG" ] && [ ! -s "$MP_ROOT/runtime/ha-receiver.json" ] \
        && [ "$(jq -r '.state // empty' "$MP_SETUP_V2_STATE" 2>/dev/null || true)" = complete ] \
        && [[ "$(jq -r '.mode // empty' "$MP_SETUP_V2_STATE" 2>/dev/null || true)" =~ ^(ha-join|replace-node)$ ]]; then
        ui_message "Waiting for the primary" "Pairing is complete. Leave this VPS powered on; the current primary will send and verify the first encrypted application copy. Reopen mp-opt after the primary reports that replication succeeded."
        printf 'This HA node is waiting for its first verified replication.\n'
        return 0
    fi
    while true; do
        mp_banner
        local choice
        if ! choice="$(MP_MENU_CANCEL_LABEL="Exit" ui_menu "MP-OPT_SERVER" "Choose an area" \
            "overview" "System overview" \
            "setup" "Commission, migrate, replace or recover a server" \
            "services" "Deploy and services" \
            "configuration" "Configuration" \
            "snapshots" "Snapshots and recovery" \
            "root" "Root administrator recovery" \
            "database" "Database" \
            "ha" "High availability" \
            "accountability" "Signed deletion accountability evidence" \
            "logs" "Logs" \
            "maintenance" "Maintenance and diagnostics" \
            "exit" "Exit")"; then
            ui_confirm "Exit MP-OPT_SERVER" "Close the management interface?" && break
            continue
        fi
        case "$choice" in
            overview) mp_run_action mp_system_overview ;;
            setup) mp_run_action mp_setup_v2 ;;
            services) menu_services ;;
            configuration) menu_configuration ;;
            snapshots) menu_snapshots ;;
            root) menu_root_recovery ;;
            database) menu_database ;;
            ha) menu_high_availability ;;
            accountability) menu_accountability ;;
            logs) mp_run_action mp_logs ;;
            maintenance) menu_maintenance ;;
            exit|"") break ;;
        esac
    done
    printf 'MP-OPT_SERVER closed.\n'
}

main "$@"
