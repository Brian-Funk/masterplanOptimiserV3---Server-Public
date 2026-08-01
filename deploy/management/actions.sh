#!/usr/bin/env bash

# Guided configuration, service operations and guarded recovery actions.

MP_GUARD_SNAPSHOT=""
MP_GUARD_IDENTITY=""

# Show command output in a protected temporary terminal report.
mp_show_report() {
    local title="$1"
    shift
    local report
    report="$(mktemp "${MP_STATE}/report.XXXXXX")"
    if "$@" > "$report" 2>&1; then
        ui_text_file "$title" "$report"
    else
        ui_text_file "$title - failed" "$report"
        rm -f "$report"
        return 1
    fi
    rm -f "$report"
}

# Build a redacted operational overview for the dashboard.
mp_system_overview() {
    local report domain
    report="$(mktemp "${MP_STATE}/overview.XXXXXX")"
    domain="$(mp_env_get DOMAIN 2>/dev/null || printf 'not configured')"
    {
        printf 'MP-OPT_SERVER overview\n\n'
        printf 'Host: %s\n' "$(hostname -f 2>/dev/null || hostname)"
        printf 'Domain: %s\n' "$domain"
        printf 'HA role: %s\n' "$(mp_ha_role 2>/dev/null || printf invalid)"
        printf 'Repository: %s\n' "$MP_ROOT"
        printf 'Branch: %s\n' "$(git -C "$MP_ROOT" branch --show-current 2>/dev/null || printf unknown)"
        printf 'Commit: %s\n' "$(git -C "$MP_ROOT" rev-parse --short HEAD 2>/dev/null || printf unknown)"
        printf 'Recovery recipient: '
        if mp_recovery_recipient >/dev/null 2>&1; then printf 'configured\n'; else printf 'not configured\n'; fi
        printf 'Recovery storage: %s\n' "$(mp_recovery_storage_mode 2>/dev/null || printf invalid)"
        if [ -f "$MP_MANUAL_EXPORT_STATE" ]; then
            printf 'Last manual workstation copy: %s\n' \
                "$(jq -r 'if .state == "operator-sha256-confirmed" then .confirmed_at + " | " + .snapshot + " | " + .package_sha256 else "ACTION REQUIRED since " + (.required_at // "unknown") + " | " + (.reason // "operator-action") end' "$MP_MANUAL_EXPORT_STATE" 2>/dev/null || printf INVALID)"
        fi
        printf 'Public health: '
        if [ "$domain" != "not configured" ] && curl -fsS --max-time 5 "https://${domain}/health"; then
            printf '\n'
        else
            printf 'unavailable\n'
        fi
        printf '\nContainers\n'
        if [ -f "$MP_ROOT/.env" ]; then
            mp_compose_init
            "${MP_COMPOSE[@]}" ps 2>&1 || true
        fi
        printf '\nDisk usage\n'
        df -h "$MP_ROOT" "$MP_SNAPSHOTS" 2>/dev/null || true
        printf '\nDocker usage\n'
        docker system df 2>/dev/null || true
        printf '\nLatest snapshots\n'
        find "$MP_SNAPSHOTS" -mindepth 1 -maxdepth 1 -type d ! -name '.*' \
            -printf '%TY-%Tm-%Td %TH:%TM  %f\n' 2>/dev/null | sort -r | head -5
    } > "$report"
    ui_text_file "System overview" "$report"
    rm -f "$report"
}

# Generate a VAPID private key in the raw base64url form expected by the backend.
mp_generate_vapid_private_key() {
    openssl rand 32 | base64 | tr '+/' '-_' | tr -d '=\n'
}

# Validate a simple mailbox address without performing network delivery checks.
mp_validate_email_address() {
    local value="$1"
    mp_validate_single_line "$value" && [[ "$value" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]
}

# Run the first-production configuration wizard without overwriting an installation.
mp_guided_initial_configuration() {
    if [ -f "$MP_ROOT/.env" ]; then
        ui_message "Production configuration" "An existing .env was detected. Use the Configuration menu to edit it safely."
        return 0
    fi
    if [ -d "$MP_ROOT/secrets" ] \
        && find "$MP_ROOT/secrets" -maxdepth 1 -type f -size +0c -print -quit | grep -q .; then
        ui_error "Protected secret files already exist while .env is missing. Restore the matching .env or verify the installation manually before configuration. Nothing was overwritten."
        return 1
    fi
    mp_require_commands openssl ssh-keygen docker || return 1
    local domain app_name db_password db_repeat vapid claims_email root_token bootstrap_view instance_id
    local configure_smtp smtp_host smtp_port smtp_username smtp_token smtp_repeat
    local smtp_security smtp_from_email smtp_from_name smtp_reply_to staging

    domain="$(ui_input "Site identity" "Public application domain (for example schedule.example.org)" "")" || return 1
    mp_validate_hostname "$domain" || { ui_error "Enter a valid DNS hostname."; return 1; }
    app_name="$(ui_input "Site identity" "Passkey application name" "Masterplan Access")" || return 1
    mp_validate_env_value "$app_name" || { ui_error "The application name contains unsupported configuration characters."; return 1; }

    if ui_confirm "Database" "Generate a strong internal database password automatically?"; then
        db_password="$(openssl rand -hex 32)"
    else
        db_password="$(ui_password "Database" "Enter a 24-128 character password using letters, numbers, dots, underscores, tildes or hyphens")" || return 1
        db_repeat="$(ui_password "Database" "Repeat the database password")" || return 1
        [ "$db_password" = "$db_repeat" ] || { ui_error "The database passwords do not match."; return 1; }
        [ "${#db_password}" -ge 24 ] && [ "${#db_password}" -le 128 ] \
            && [[ "$db_password" =~ ^[A-Za-z0-9._~-]+$ ]] \
            || { ui_error "The database password does not meet the required format."; return 1; }
    fi

    claims_email="$(ui_input "Web push" "VAPID contact email (for example admin@example.org)" "")" || return 1
    mp_validate_email_address "$claims_email" || { ui_error "Enter a valid VAPID contact email."; return 1; }
    vapid="$(mp_generate_vapid_private_key)"
    root_token="$(mp_random_secret)"
    instance_id="$(cat /proc/sys/kernel/random/uuid)" || return 1

    configure_smtp="no"
    smtp_host=""; smtp_port="587"; smtp_username=""; smtp_token=""
    smtp_security="starttls"; smtp_from_email=""; smtp_from_name="Masterplan Access"; smtp_reply_to=""
    if ui_confirm "Optional activation email" "Configure SMTP activation email now? You can safely skip this and configure it later."; then
        configure_smtp="yes"
        smtp_host="$(ui_input "SMTP" "SMTP hostname (for example smtp.example.org)" "")" || return 1
        mp_validate_hostname "$smtp_host" || { ui_error "Enter a valid SMTP hostname."; return 1; }
        smtp_port="$(ui_input "SMTP" "SMTP port" "587")" || return 1
        [[ "$smtp_port" =~ ^[0-9]+$ ]] && [ "$smtp_port" -ge 1 ] && [ "$smtp_port" -le 65535 ] \
            || { ui_error "SMTP port must be between 1 and 65535."; return 1; }
        smtp_security="$(ui_menu "SMTP" "Connection security" "starttls" "STARTTLS, usually port 587" "tls" "Implicit TLS, usually port 465")" || return 1
        smtp_username="$(ui_input "SMTP" "SMTP username (for example notifications@example.org)")" || return 1
        mp_validate_env_value "$smtp_username" || { ui_error "The SMTP username contains unsupported configuration characters."; return 1; }
        smtp_token="$(ui_password "SMTP" "Provider-issued SMTP token")" || return 1
        smtp_repeat="$(ui_password "SMTP" "Repeat the SMTP token")" || return 1
        [ -n "$smtp_token" ] && [ "$smtp_token" = "$smtp_repeat" ] || { ui_error "The SMTP tokens do not match."; return 1; }
        smtp_from_email="$(ui_input "SMTP" "Sender email (for example notifications@example.org)" "")" || return 1
        mp_validate_email_address "$smtp_from_email" || { ui_error "Enter a valid sender email."; return 1; }
        smtp_from_name="$(ui_input "SMTP" "Sender display name" "Masterplan Access")" || return 1
        mp_validate_env_value "$smtp_from_name" || { ui_error "The sender name contains unsupported configuration characters."; return 1; }
        smtp_reply_to="$(ui_input "SMTP" "Optional reply-to email (for example support@example.org)")" || return 1
        if [ -n "$smtp_reply_to" ] && ! mp_validate_email_address "$smtp_reply_to"; then
            ui_error "Enter a valid reply-to email or leave it blank."
            return 1
        fi
    fi

    ui_message "Permitted data for this instance" \
        "Masterplan supports operational event scheduling and access management.\n\nNormally permitted: names, necessary business contact details, event roles, availability and operational instructions. Optional fields must be necessary for the controller's stated purpose.\n\nUnsupported: health, dietary, safeguarding, political, religious, disciplinary or unrelated private information. Do not use broad text fields for sensitive data.\n\nThe self-hosting controller remains responsible for legal basis, transparency, providers, access, retention, data-subject requests and incidents. Nothing is sent to the software maintainer."
    if ! ui_confirm "Permitted data" \
        "I understand the permitted-data boundary and the controller's responsibility. Record this local setup acknowledgement?"; then
        ui_message "Configuration paused" "No configuration was written. Resume when the controller is ready to acknowledge the permitted-data boundary."
        return 0
    fi

    if ! ui_confirm "Review configuration" \
        "Domain: ${domain}\nApplication: ${app_name}\nDatabase password: generated or hidden\nVAPID: generated\nSMTP: ${configure_smtp}\n\nWrite the protected production configuration?"; then
        return 0
    fi
    if ! ui_confirm "Commission instance signing identity" \
        "Generate the deployment's Ed25519 instance signing key exactly once?\n\nThe private key remains in the protected Server secret area. It is not a root, controller or processor key. Later startup must fail closed if this key is missing or its trusted fingerprint changes; recovery or rotation is always explicit."; then
        ui_error "Initial commissioning was cancelled before any protected configuration was written."
        return 0
    fi

    staging="$(mktemp -d "${TMPDIR:-/tmp}/mp-opt-config.XXXXXX")"
    chmod 700 "$staging"
    mkdir -p "$staging/secrets"
    chmod 700 "$staging/secrets"
    {
        printf '# MP-OPT_SERVER production configuration\n'
        printf '# Generated %s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'CORS_ORIGINS=["https://%s"]\n' "$domain"
        printf 'WEBAUTHN_RP_ID=%s\n' "$domain"
        printf 'WEBAUTHN_RP_NAME=%s\n' "$app_name"
        printf 'WEBAUTHN_ORIGIN=https://%s\n' "$domain"
        printf 'COOKIE_SECURE=true\n'
        printf 'SESSION_COOKIE_NAME=__Host-mp_session\n'
        printf 'CSRF_COOKIE_NAME=__Host-mp_csrf\n'
        printf 'DOMAIN=%s\n' "$domain"
        printf 'MP_INSTANCE_ID=%s\n' "$instance_id"
        printf 'GOVERNANCE_SETUP_ACK_VERSION=2026-07-27\n'
        printf 'EVIDENCE_MODE=required\n'
        printf 'EVIDENCE_TOMBSTONE_RETENTION_DAYS=1095\n'
        printf 'EVENT_PURGE_GRACE_DAYS=90\n'
        printf 'RETENTION_SCHEDULER_INTERVAL_SECONDS=300\n'
        printf 'SESSION_TTL_HOURS=8\nSESSION_TTL_HOURS_ADMIN=1\nSESSION_INACTIVITY_MINUTES=30\n'
        printf 'VAPID_CLAIMS_EMAIL=mailto:%s\n' "$claims_email"
        printf 'SMTP_HOST=%s\nSMTP_PORT=%s\nSMTP_USERNAME=%s\n' "$smtp_host" "$smtp_port" "$smtp_username"
        printf 'SMTP_SECURITY=%s\nSMTP_FROM_EMAIL=%s\n' "$smtp_security" "$smtp_from_email"
        printf 'SMTP_FROM_NAME=%s\nSMTP_REPLY_TO=%s\nSMTP_TIMEOUT_SECONDS=15\n' "$smtp_from_name" "$smtp_reply_to"
    } > "$staging/.env"
    printf '%s' "$db_password" > "$staging/secrets/database_password"
    printf '%s' "$(mp_random_secret)" > "$staging/secrets/ip_hmac_key"
    printf '%s' "$(mp_random_secret)" > "$staging/secrets/secret_key"
    printf '%s' "$vapid" > "$staging/secrets/vapid_private_key"
    printf '%s' "$root_token" > "$staging/secrets/root_bootstrap_token"
    printf '%s' "$smtp_token" > "$staging/secrets/smtp_token"
    python3 "$MP_ROOT/deploy/management/instance_key.py" commission \
        --secret-dir "$staging/secrets" --instance-id "$instance_id" >/dev/null \
        || { rm -rf "$staging"; return 1; }
    chmod 600 "$staging/.env" "$staging/secrets/"*

    if ! cp -a "$staging/.env" "$MP_ROOT/.env" \
        || ! mkdir -p "$MP_ROOT/secrets" \
        || ! chmod 700 "$MP_ROOT/secrets" \
        || ! cp -a "$staging/secrets/." "$MP_ROOT/secrets/" \
        || ! chmod 600 "$MP_ROOT/.env" "$MP_ROOT/secrets/"*; then
        rm -f "$MP_ROOT/.env" "$MP_ROOT/secrets/secret_key" \
            "$MP_ROOT/secrets/database_password" \
            "$MP_ROOT/secrets/ip_hmac_key" \
            "$MP_ROOT/secrets/vapid_private_key" \
            "$MP_ROOT/secrets/root_bootstrap_token" \
            "$MP_ROOT/secrets/smtp_token" \
            "$MP_ROOT/secrets/evidence_signing_key" \
            "$MP_ROOT/secrets/evidence_signing_key.pub"
        rm -rf "$staging"
        ui_error "The protected configuration could not be installed. Partial generated files were removed."
        return 1
    fi
    rm -rf "$staging"
    unset db_password db_repeat smtp_token smtp_repeat vapid instance_id
    if ! mp_compose_validate; then
        rm -f "$MP_ROOT/.env" "$MP_ROOT/secrets/secret_key" \
            "$MP_ROOT/secrets/database_password" \
            "$MP_ROOT/secrets/ip_hmac_key" \
            "$MP_ROOT/secrets/vapid_private_key" \
            "$MP_ROOT/secrets/root_bootstrap_token" \
            "$MP_ROOT/secrets/smtp_token" \
            "$MP_ROOT/secrets/evidence_signing_key" \
            "$MP_ROOT/secrets/evidence_signing_key.pub"
        ui_error "Compose rejected the generated configuration. Generated files were removed without changing an existing installation."
        return 1
    fi
    mp_audit "configuration.initial" "success" "smtp:${configure_smtp};permitted-data:2026-07-27"
    if [ "${MP_SETUP_V2_ACTIVE:-0}" != 1 ]; then
        printf -v bootstrap_view \
            'Production configuration is ready.\n\nOpen https://%s/bootstrap\n\nRoot bootstrap code:\n%s\n\nStore this code securely. It is not written to the management log.' \
            "$domain" "$root_token"
        if mp_has_terminal; then
            ui_copyable_terminal_text "Configuration complete" "$bootstrap_view" \
                "Copy and store the bootstrap code securely, then press Enter to continue." || {
                unset root_token bootstrap_view
                return 1
            }
        else
            # The token is already installed in its protected secret file.
            # Automation must complete without printing that token to captured
            # stdout/stderr; an interactive management session can present it.
            ui_message "Configuration complete" \
                "Production configuration is ready. Open an interactive management session to display and copy the protected root bootstrap code."
        fi
    fi
    unset root_token bootstrap_view
    if [ "${MP_SETUP_V2_ACTIVE:-0}" != 1 ]; then
        if ui_confirm "Recovery encryption" "Configure the public age recovery recipient now?"; then
            mp_configure_recovery_recipient || true
        fi
        if ui_confirm "High availability" "Choose standalone or symmetric two-node mode now? You can safely skip this and configure it later."; then
            mp_configure_ha || true
        fi
    fi
}

# Recreate only the backend and confirm public health after a static setting change.
mp_recreate_backend() {
    mp_require_active_or_standalone || return 1
    mp_prepare_backend_secret_permissions || return 1
    mp_compose_validate || return 1
    mp_compose_init
    "${MP_COMPOSE[@]}" up -d --no-deps --force-recreate backend >/dev/null || return 1
    mp_wait_for_health 30 || return 1
}

# Apply SMTP settings transactionally, including provider authentication.
mp_configure_smtp() {
    local host port username security from_email from_name reply_to token token_repeat
    local staging old_env old_token status
    mp_require_active_or_standalone || return 1
    host="$(ui_input "SMTP" "SMTP hostname (for example smtp.example.org)" "$(mp_env_get SMTP_HOST 2>/dev/null || true)")" || return 1
    mp_validate_hostname "$host" || { ui_error "Enter a valid SMTP hostname."; return 1; }
    port="$(ui_input "SMTP" "SMTP port" "$(mp_env_get SMTP_PORT 2>/dev/null || printf 587)")" || return 1
    [[ "$port" =~ ^[0-9]+$ ]] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || { ui_error "Invalid SMTP port."; return 1; }
    security="$(ui_menu "SMTP" "Connection security" "starttls" "STARTTLS" "tls" "Implicit TLS")" || return 1
    username="$(ui_input "SMTP" "SMTP username (for example notifications@example.org)" "$(mp_env_get SMTP_USERNAME 2>/dev/null || true)")" || return 1
    mp_validate_env_value "$username" || { ui_error "The SMTP username contains unsupported configuration characters."; return 1; }
    from_email="$(ui_input "SMTP" "Sender email (for example notifications@example.org)" "$(mp_env_get SMTP_FROM_EMAIL 2>/dev/null || true)")" || return 1
    mp_validate_email_address "$from_email" || { ui_error "Enter a valid sender email."; return 1; }
    from_name="$(ui_input "SMTP" "Sender display name" "$(mp_env_get SMTP_FROM_NAME 2>/dev/null || printf 'Masterplan Access')")" || return 1
    mp_validate_env_value "$from_name" || { ui_error "The sender name contains unsupported configuration characters."; return 1; }
    reply_to="$(ui_input "SMTP" "Optional reply-to email (for example support@example.org)" "$(mp_env_get SMTP_REPLY_TO 2>/dev/null || true)")" || return 1
    [ -z "$reply_to" ] || mp_validate_email_address "$reply_to" || { ui_error "Invalid reply-to email."; return 1; }
    token="$(ui_password "SMTP" "Provider-issued SMTP token")" || return 1
    token_repeat="$(ui_password "SMTP" "Repeat the SMTP token")" || return 1
    [ -n "$token" ] && [ "$token" = "$token_repeat" ] || { ui_error "The SMTP tokens do not match."; return 1; }
    ui_confirm "SMTP" "Install these SMTP settings and verify provider authentication?" || return 0

    mp_lock || return 1
    staging="$(mktemp -d /dev/shm/mp-opt-smtp.XXXXXX 2>/dev/null || mktemp -d)" || return 1
    chmod 700 "$staging" || { rm -rf "$staging"; return 1; }
    cp -a "$MP_ROOT/.env" "$staging/.env" || { rm -rf "$staging"; return 1; }
    cp -a "$MP_ROOT/secrets/smtp_token" "$staging/smtp_token" || { rm -rf "$staging"; return 1; }
    old_env="$staging/.env"; old_token="$staging/smtp_token"

    if ! mp_env_set SMTP_HOST "$host" \
        || ! mp_env_set SMTP_PORT "$port" \
        || ! mp_env_set SMTP_USERNAME "$username" \
        || ! mp_env_set SMTP_SECURITY "$security" \
        || ! mp_env_set SMTP_FROM_EMAIL "$from_email" \
        || ! mp_env_set SMTP_FROM_NAME "$from_name" \
        || ! mp_env_set SMTP_REPLY_TO "$reply_to" \
        || ! printf '%s' "$token" > "$MP_ROOT/secrets/smtp_token" \
        || ! chmod 600 "$MP_ROOT/secrets/smtp_token"; then
        cp -a "$old_env" "$MP_ROOT/.env" || true
        cp -a "$old_token" "$MP_ROOT/secrets/smtp_token" || true
        rm -rf "$staging"
        unset token token_repeat
        ui_error "SMTP settings could not be installed. The previous files were restored."
        return 1
    fi
    unset token token_repeat

    status=0
    if ! mp_recreate_backend; then
        status=1
    else
        mp_compose_init
        if ! "${MP_COMPOSE[@]}" exec -T backend python -c \
            'from app.core.activation_email import ActivationMailer as M; m=M(); m.__enter__(); m.__exit__(None,None,None)' >/dev/null; then
            status=1
        fi
    fi
    if [ "$status" -ne 0 ]; then
        if ! cp -a "$old_env" "$MP_ROOT/.env" \
            || ! cp -a "$old_token" "$MP_ROOT/secrets/smtp_token" \
            || ! cmp -s "$old_env" "$MP_ROOT/.env" \
            || ! cmp -s "$old_token" "$MP_ROOT/secrets/smtp_token"; then
            rm -rf "$staging"
            mp_audit "smtp.configure" "failed" "rollback-file-error"
            ui_error "SMTP verification failed and the previous files could not be confirmed. Do not restart services until configuration is recovered."
            return 1
        fi
        mp_recreate_backend || true
        rm -rf "$staging"
        mp_audit "smtp.configure" "failed" "rolled-back"
        ui_error "SMTP verification failed. The previous configuration was restored."
        return 1
    fi
    rm -rf "$staging"
    mp_audit "smtp.configure" "success" "${host}:${port}:${security}"
    if [ "$(mp_ha_role 2>/dev/null || printf standalone)" = dynamic ]; then
        python3 "$MP_ROOT/deploy/ha/smtp_probe.py" --root "$MP_ROOT" \
            --node-id "$HA_NODE_ID" --output "$MP_ROOT/runtime/ha-smtp-status.json" \
            >/dev/null 2>&1 || true
    fi
    mp_queue_ha_replication "smtp-configuration" || true
    ui_message "SMTP ready" "Provider authentication and public health passed. No email was sent. In HA mode, the protected configuration is queued for the peer; verify both origins under High availability before relying on failover delivery."
}

# Disable SMTP without retaining an unencrypted token rollback file on disk.
mp_disable_smtp() {
    local staging
    mp_require_active_or_standalone || return 1
    ui_require_phrase "Disable activation email" \
        "Activation email sending will become unavailable until configured again." \
        "DISABLE SMTP" || return 1
    mp_lock || return 1
    staging="$(mktemp -d /dev/shm/mp-opt-smtp.XXXXXX 2>/dev/null || mktemp -d)" || return 1
    chmod 700 "$staging" || { rm -rf "$staging"; return 1; }
    cp -a "$MP_ROOT/.env" "$staging/.env" || { rm -rf "$staging"; return 1; }
    cp -a "$MP_ROOT/secrets/smtp_token" "$staging/smtp_token" || { rm -rf "$staging"; return 1; }
    if ! mp_env_set SMTP_HOST "" \
        || ! mp_env_set SMTP_USERNAME "" \
        || ! mp_env_set SMTP_FROM_EMAIL "" \
        || ! mp_env_set SMTP_REPLY_TO "" \
        || ! : > "$MP_ROOT/secrets/smtp_token" \
        || ! chmod 600 "$MP_ROOT/secrets/smtp_token"; then
        cp -a "$staging/.env" "$MP_ROOT/.env" || true
        cp -a "$staging/smtp_token" "$MP_ROOT/secrets/smtp_token" || true
        rm -rf "$staging"
        ui_error "SMTP could not be disabled. Previous files were restored."
        return 1
    fi
    if ! mp_recreate_backend; then
        if ! cp -a "$staging/.env" "$MP_ROOT/.env" \
            || ! cp -a "$staging/smtp_token" "$MP_ROOT/secrets/smtp_token" \
            || ! cmp -s "$staging/.env" "$MP_ROOT/.env" \
            || ! cmp -s "$staging/smtp_token" "$MP_ROOT/secrets/smtp_token"; then
            rm -rf "$staging"
            mp_audit "smtp.disable" "failed" "rollback-file-error"
            ui_error "SMTP disable failed and the previous files could not be confirmed. Recover configuration before restarting."
            return 1
        fi
        mp_recreate_backend || true
        rm -rf "$staging"
        mp_audit "smtp.disable" "failed" "rolled-back"
        return 1
    fi
    rm -rf "$staging"
    mp_audit "smtp.disable" "success" "disabled"
    rm -f "$MP_ROOT/runtime/ha-smtp-status.json"
    mp_queue_ha_replication "smtp-disabled" || true
    ui_message "SMTP disabled" "Activation email is disabled. Existing users, links and passkeys were not changed."
}

# Send one token-free SMTP test message from the running backend.
mp_send_smtp_test() {
    local recipient
    mp_require_active_or_standalone || return 1
    recipient="$(ui_input "SMTP test" "Test recipient email")" || return 1
    mp_validate_email_address "$recipient" || { ui_error "Enter a valid recipient email."; return 1; }
    mp_compose_init
    if "${MP_COMPOSE[@]}" exec -T -e MP_TEST_RECIPIENT="$recipient" backend python -c \
        'import os; from app.core.activation_email import ActivationMailer, build_test_message; m=ActivationMailer(); m.__enter__(); m.send(build_test_message(os.environ["MP_TEST_RECIPIENT"])); m.__exit__(None,None,None)' >/dev/null; then
        mp_audit "smtp.test" "success" "recipient-supplied"
        ui_message "Test accepted" "The mail server accepted the token-free test message."
    else
        mp_audit "smtp.test" "failed" "provider-error"
        ui_error "The mail server did not accept the test message. Review backend logs."
        return 1
    fi
}

# Prepare a new full rollback snapshot and deep-verify it with the recovery key.
mp_prepare_guard_snapshot() {
    local label="$1"
    MP_GUARD_IDENTITY="$(mp_prompt_identity_file)" || return 1
    MP_GUARD_SNAPSHOT="$(mp_snapshot_create full "${label}-$(date -u +%Y%m%dT%H%M%SZ)")" || {
        mp_remove_identity_file "$MP_GUARD_IDENTITY"
        MP_GUARD_IDENTITY=""
        return 1
    }
    if ! mp_snapshot_verify_path "$MP_GUARD_SNAPSHOT" "$MP_GUARD_IDENTITY"; then
        mp_remove_identity_file "$MP_GUARD_IDENTITY"
        MP_GUARD_IDENTITY=""
        MP_GUARD_SNAPSHOT=""
        return 1
    fi
    mp_load_ha_config || {
        mp_remove_identity_file "$MP_GUARD_IDENTITY"
        MP_GUARD_IDENTITY=""
        return 1
    }
    if [ "${HA_RECOVERY_STORAGE_MODE:-manual_portable}" = ssh_archive ] \
        && ! mp_snapshot_copy_off_server "$MP_GUARD_SNAPSHOT"; then
        mp_remove_identity_file "$MP_GUARD_IDENTITY"
        MP_GUARD_IDENTITY=""
        ui_error "The rollback snapshot is valid locally, but its configured off-server copy failed hash verification. The guarded operation was stopped."
        return 1
    fi
}

# Remove legacy secret values from .env only when Docker secret files match.
mp_migrate_legacy_env_secrets() {
    local staging key file env_value file_value changed=0
    staging="$(mktemp -d /dev/shm/mp-opt-env-migration.XXXXXX 2>/dev/null || mktemp -d)"
    chmod 700 "$staging"
    cp -a "$MP_ROOT/.env" "$staging/.env"
    cp -a "$MP_ROOT/.env" "$staging/original.env"
    for key in SECRET_KEY VAPID_PRIVATE_KEY ROOT_BOOTSTRAP_TOKEN SMTP_TOKEN; do
        case "$key" in
            SECRET_KEY) file="$MP_ROOT/secrets/secret_key" ;;
            VAPID_PRIVATE_KEY) file="$MP_ROOT/secrets/vapid_private_key" ;;
            ROOT_BOOTSTRAP_TOKEN) file="$MP_ROOT/secrets/root_bootstrap_token" ;;
            SMTP_TOKEN) file="$MP_ROOT/secrets/smtp_token" ;;
        esac
        if grep -q "^${key}=" "$staging/.env"; then
            [ "$(grep -c "^${key}=" "$staging/.env")" -eq 1 ] || {
                rm -rf "$staging"; ui_error "Duplicate legacy setting: $key"; return 1;
            }
            env_value="$(mp_env_get "$key" "$staging/.env")"
            file_value="$(cat "$file" 2>/dev/null || true)"
            [ "$env_value" = "$file_value" ] || {
                rm -rf "$staging"
                unset env_value file_value
                ui_error "$key differs from its Docker secret file. Nothing was changed."
                return 1
            }
            awk -v key="$key" 'index($0, key "=") != 1 {print}' "$staging/.env" > "$staging/next"
            mv "$staging/next" "$staging/.env"
            changed=1
            unset env_value file_value
        fi
    done
    if [ "$changed" -eq 0 ]; then
        rm -rf "$staging"
        ui_message "Secret migration" "No legacy secret values are stored in .env."
        return 0
    fi
    ui_confirm "Secret migration" \
        "Matching legacy secret values will be removed from .env. Docker secret files remain unchanged." \
        || { rm -rf "$staging"; return 0; }
    cp -a "$staging/.env" "$MP_ROOT/.env"
    chmod 600 "$MP_ROOT/.env"
    if ! mp_recreate_backend; then
        cp -a "$staging/original.env" "$MP_ROOT/.env"
        mp_recreate_backend || true
        rm -rf "$staging"
        ui_error "Backend verification failed. The original .env was restored."
        return 1
    fi
    rm -rf "$staging"
    mp_audit "configuration.secret-migration" "success" "env-redacted"
    ui_message "Secret migration" "Legacy values were removed from .env after exact comparison with Docker secret files."
}

# Clear transient recovery identity state after a guarded operation.
mp_clear_guard_snapshot() {
    mp_remove_identity_file "$MP_GUARD_IDENTITY"
    MP_GUARD_IDENTITY=""
    MP_GUARD_SNAPSHOT=""
}

# Roll back a failed guarded operation from its mandatory full snapshot.
mp_guard_rollback() {
    local message="$1"
    if [ -n "$MP_GUARD_SNAPSHOT" ] && [ -n "$MP_GUARD_IDENTITY" ] \
        && mp_snapshot_apply "$MP_GUARD_SNAPSHOT" "$MP_GUARD_IDENTITY"; then
        mp_audit "guard.rollback" "success" "$message"
        mp_clear_guard_snapshot
        ui_error "$message The verified rollback snapshot was restored."
    else
        mp_audit "guard.rollback" "failed" "$message"
        local snapshot_name="${MP_GUARD_SNAPSHOT:-unavailable}"
        mp_clear_guard_snapshot
        ui_error "$message Automatic rollback failed. Keep services stopped and recover from: $snapshot_name"
    fi
    return 1
}

# Reset only root authentication state and prepare one-time bootstrap recovery.
mp_reset_root_admin() {
    local root_count new_token bootstrap_view
    mp_require_ha_maintenance_window || return 1
    mp_prepare_guard_snapshot "pre-root-reset" || { ui_error "A deeply verified recovery snapshot is required."; return 1; }
    if ! ui_require_phrase "Reset root administrator" \
        "Root passkeys and root sessions will be removed. All other users and application data remain unchanged." \
        "RESET ROOT ADMIN"; then
        mp_clear_guard_snapshot
        return 0
    fi
    mp_lock || { mp_clear_guard_snapshot; return 1; }
    mp_compose_init
    root_count="$("${MP_COMPOSE[@]}" exec -T db psql -At -U masterplan -d masterplan \
        -c 'SELECT count(*) FROM users WHERE is_root_admin IS TRUE;')"
    [ "$root_count" = "1" ] || { mp_clear_guard_snapshot; ui_error "Expected exactly one root administrator."; return 1; }
    new_token="$(mp_random_secret)"
    if ! "${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U masterplan -d masterplan >/dev/null <<'SQL'
BEGIN;
DO $reset$
DECLARE root_id INTEGER;
BEGIN
    SELECT id INTO STRICT root_id FROM users WHERE is_root_admin IS TRUE;
    DELETE FROM passkey_ceremonies WHERE user_id = root_id;
    DELETE FROM exchange_codes WHERE user_id = root_id;
    DELETE FROM auth_sessions WHERE user_id = root_id;
    DELETE FROM activation_links WHERE user_id = root_id;
    DELETE FROM webauthn_credentials WHERE user_id = root_id;
    DELETE FROM server_settings WHERE key = 'root_bootstrap_disabled';
    UPDATE users SET is_active = TRUE, is_activated = FALSE WHERE id = root_id;
END $reset$;
COMMIT;
SQL
    then
        unset new_token
        mp_guard_rollback "Root reset failed."
        return 1
    fi
    if ! printf '%s' "$new_token" > "$MP_ROOT/secrets/root_bootstrap_token" \
        || ! chmod 600 "$MP_ROOT/secrets/root_bootstrap_token" \
        || ! mp_recreate_backend; then
        unset new_token
        mp_guard_rollback "Root reset health verification failed."
        return 1
    fi
    mp_audit "root.reset" "success" "authentication-only"
    mp_queue_ha_replication "root-reset" || true
    mp_clear_guard_snapshot
    printf -v bootstrap_view \
        'Open https://%s/bootstrap\n\nBootstrap code:\n%s\n\nStore it securely. Disable bootstrap from this menu after registration.' \
        "$(mp_env_get DOMAIN)" "$new_token"
    ui_copyable_terminal_text "Root recovery ready" "$bootstrap_view" \
        "Copy and store the bootstrap code securely, then press Enter to return." || {
        unset new_token bootstrap_view
        return 1
    }
    unset new_token bootstrap_view
}

# Disable root bootstrap only after a replacement root passkey exists.
mp_disable_root_bootstrap() {
    local credentials
    mp_require_active_or_standalone || return 1
    mp_compose_init
    credentials="$("${MP_COMPOSE[@]}" exec -T db psql -At -U masterplan -d masterplan \
        -c 'SELECT count(*) FROM webauthn_credentials c JOIN users u ON u.id=c.user_id WHERE u.is_root_admin IS TRUE;')"
    [ "$credentials" -gt 0 ] || { ui_error "Register a root passkey before disabling bootstrap."; return 1; }
    ui_confirm "Disable bootstrap" "Clear the server bootstrap code now?" || return 0
    "${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U masterplan -d masterplan \
        -c "INSERT INTO server_settings (key, value) VALUES ('root_bootstrap_disabled', 'true') ON CONFLICT (key) DO UPDATE SET value='true';" \
        >/dev/null || { ui_error "The durable bootstrap-disabled marker could not be written."; return 1; }
    : > "$MP_ROOT/secrets/root_bootstrap_token"
    chmod 600 "$MP_ROOT/secrets/root_bootstrap_token"
    mp_recreate_backend
    mp_audit "root.bootstrap-disable" "success" "empty-secret"
    mp_queue_ha_replication "root-bootstrap-disabled" || true
    ui_message "Bootstrap disabled" "The bootstrap code was cleared. Root passkeys remain valid."
}

# Completely recreate the application database while preserving configuration.
mp_wipe_database() {
    mp_require_ha_maintenance_window || return 1
    local new_token bootstrap_view
    mp_prepare_guard_snapshot "pre-database-wipe" || { ui_error "A deeply verified recovery snapshot is required."; return 1; }
    if ! ui_require_phrase "Wipe database" \
        "Every event, user, passkey, schedule, audit entry and setting will be permanently removed." \
        "WIPE DATABASE"; then
        mp_clear_guard_snapshot
        return 0
    fi
    mp_lock || { mp_clear_guard_snapshot; return 1; }
    new_token="$(mp_random_secret)"
    mp_compose_init
    "${MP_COMPOSE[@]}" stop backend >/dev/null 2>&1 || true
    if ! "${MP_COMPOSE[@]}" exec -T db dropdb --if-exists --force -U masterplan masterplan \
        || ! "${MP_COMPOSE[@]}" exec -T db createdb -U masterplan masterplan; then
        unset new_token
        mp_guard_rollback "Database recreation failed."
        return 1
    fi
    if ! printf '%s' "$new_token" > "$MP_ROOT/secrets/root_bootstrap_token" \
        || ! chmod 600 "$MP_ROOT/secrets/root_bootstrap_token"; then
        unset new_token
        mp_guard_rollback "Fresh database bootstrap secret installation failed."
        return 1
    fi
    if ! mp_ensure_base_schema; then
        unset new_token
        mp_guard_rollback "Fresh database base-schema initialisation failed."
        return 1
    fi
    if ! mp_apply_migrations; then
        unset new_token
        mp_guard_rollback "Fresh database schema migration failed."
        return 1
    fi
    if ! mp_verify_database_schema_contract; then
        unset new_token
        mp_guard_rollback "Fresh database schema contract verification failed."
        return 1
    fi
    if ! mp_recreate_backend; then
        unset new_token
        mp_guard_rollback "Fresh database service recreation failed."
        return 1
    fi
    if [ "$(mp_ha_role)" = "dynamic" ]; then
        MP_MANAGEMENT_LOCK_HELD=1 "$MP_ROOT/deploy/ha/promote_local.sh" \
            "$(jq -r '.generation' "$MP_ROOT/runtime/ha-control.json")" --force-revoke \
            || { unset new_token; mp_guard_rollback "Fresh HA writer identity failed."; return 1; }
    fi
    mp_audit "database.wipe" "success" "fresh-schema"
    mp_queue_ha_replication "database-wipe" || true
    mp_clear_guard_snapshot
    printf -v bootstrap_view \
        'A clean database and root account are ready.\n\nOpen https://%s/bootstrap\n\nBootstrap code:\n%s' \
        "$(mp_env_get DOMAIN)" "$new_token"
    ui_copyable_terminal_text "Database recreated" "$bootstrap_view" \
        "Copy and store the bootstrap code securely, then press Enter to return." || {
        unset new_token bootstrap_view
        return 1
    }
    unset new_token bootstrap_view
}

# Change the main application domain and reset RP-bound passkey state safely.
mp_change_domain() {
    mp_require_ha_maintenance_window || return 1
    local old_domain new_domain old_addresses new_addresses staged_env staged_caddy new_token caddy_mode bootstrap_view
    old_domain="$(mp_env_get DOMAIN)"
    new_domain="$(ui_input "Advanced domain change" "New application hostname" "$old_domain")" || return 1
    [ "$new_domain" != "$old_domain" ] || { ui_message "Domain" "The domain is unchanged."; return 0; }
    mp_validate_hostname "$new_domain" || { ui_error "Enter a valid hostname."; return 1; }
    old_addresses="$(getent ahostsv4 "$old_domain" 2>/dev/null | awk '{print $1}' | sort -u)"
    new_addresses="$(getent ahostsv4 "$new_domain" 2>/dev/null | awk '{print $1}' | sort -u)"
    [ -n "$old_addresses" ] || { ui_error "The current application hostname does not resolve in DNS. Domain change was stopped."; return 1; }
    [ -n "$new_addresses" ] || { ui_error "The new hostname does not resolve in DNS."; return 1; }
    if ! grep -Fxf <(printf '%s\n' "$old_addresses") <(printf '%s\n' "$new_addresses") >/dev/null 2>&1; then
        ui_error "The new hostname does not resolve to the current application endpoint. Update DNS first."
        return 1
    fi
    caddy_mode="$(mp_caddy_mode)"
    [ "$caddy_mode" != "unavailable" ] \
        || { ui_error "No managed Caddy topology is available. Domain change was stopped."; return 1; }
    if [ "$caddy_mode" = "host" ]; then
        [ "$(grep -Ec "^${old_domain//./\\.}[[:space:]]*\\{" "$MP_HOST_CADDYFILE")" -eq 1 ] \
            || { ui_error "Expected one exact application host block in $MP_HOST_CADDYFILE."; return 1; }
    fi

    mp_prepare_guard_snapshot "pre-domain-change" || { ui_error "A deeply verified recovery snapshot is required."; return 1; }
    if ! ui_require_phrase "Change application domain" \
        "All current passkeys become unusable when the WebAuthn relying party changes. Every user will need fresh activation. The active ${caddy_mode} Caddy configuration will be preserved." \
        "CHANGE DOMAIN TO $new_domain"; then
        mp_clear_guard_snapshot
        return 0
    fi
    mp_lock || { mp_clear_guard_snapshot; return 1; }
    staged_env="$(mktemp /dev/shm/mp-opt-env.XXXXXX 2>/dev/null || mktemp)"
    staged_caddy=""
    cp -a "$MP_ROOT/.env" "$staged_env"
    mp_env_set DOMAIN "$new_domain" "$staged_env"
    mp_env_set WEBAUTHN_RP_ID "$new_domain" "$staged_env"
    mp_env_set WEBAUTHN_ORIGIN "https://${new_domain}" "$staged_env"
    mp_env_set CORS_ORIGINS "[\"https://${new_domain}\"]" "$staged_env"
    if [ "$caddy_mode" = "host" ]; then
        staged_caddy="$(mktemp /dev/shm/mp-opt-caddy.XXXXXX 2>/dev/null || mktemp)"
        cp -a "$MP_HOST_CADDYFILE" "$staged_caddy"
        awk -v old="$old_domain" -v new="$new_domain" '
            {
                label=$0
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", label)
            }
            label == old " {" && !done {sub(old, new); done=1}
            {print}
            END {if (!done) exit 1}
        ' "$MP_HOST_CADDYFILE" > "$staged_caddy"
        sudo caddy validate --config "$staged_caddy" --adapter caddyfile >/dev/null || {
            rm -f "$staged_env" "$staged_caddy"
            mp_clear_guard_snapshot
            return 1
        }
    fi
    new_token="$(mp_random_secret)"
    mp_compose_init
    "${MP_COMPOSE[@]}" stop backend >/dev/null 2>&1 || true
    if ! "${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U masterplan -d masterplan >/dev/null <<'SQL'
BEGIN;
DELETE FROM passkey_ceremonies;
DELETE FROM exchange_codes;
DELETE FROM auth_sessions;
UPDATE activation_links SET invalidated_at = CURRENT_TIMESTAMP, delivery_pending = FALSE
WHERE used_at IS NULL AND invalidated_at IS NULL;
DELETE FROM webauthn_credentials;
UPDATE users SET is_activated = FALSE;
COMMIT;
SQL
    then
        rm -f "$staged_env"
        [ -z "$staged_caddy" ] || rm -f "$staged_caddy"
        unset new_token
        mp_guard_rollback "Domain passkey-state preparation failed."
        return 1
    fi
    if ! cp -a "$staged_env" "$MP_ROOT/.env" \
        || ! printf '%s' "$new_token" > "$MP_ROOT/secrets/root_bootstrap_token" \
        || ! chmod 600 "$MP_ROOT/secrets/root_bootstrap_token" \
        || ! mp_compose_validate; then
        rm -f "$staged_env"
        [ -z "$staged_caddy" ] || rm -f "$staged_caddy"
        unset new_token
        mp_guard_rollback "The new Compose configuration failed validation."
        return 1
    fi
    if [ "$caddy_mode" = "host" ] \
        && ! sudo install -o root -g root -m 0644 "$staged_caddy" "$MP_HOST_CADDYFILE"; then
        rm -f "$staged_env" "$staged_caddy"
        unset new_token
        mp_guard_rollback "The host Caddy configuration could not be installed."
        return 1
    fi
    rm -f "$staged_env"
    [ -z "$staged_caddy" ] || rm -f "$staged_caddy"
    mp_compose_init
    if ! "${MP_COMPOSE[@]}" up -d --no-deps --force-recreate backend >/dev/null \
        || ! mp_caddy_reload \
        || ! mp_caddy_validate \
        || ! mp_wait_for_health 45; then
        unset new_token
        mp_guard_rollback "The new domain did not become healthy."
        return 1
    fi
    mp_audit "domain.change" "success" "${old_domain}->${new_domain}"
    mp_queue_ha_replication "domain-change" || true
    mp_clear_guard_snapshot
    printf -v bootstrap_view \
        'Application: https://%s\nCaddy: %s\n\nOpen https://%s/bootstrap\n\nRoot bootstrap code:\n%s\n\nAll non-root users require fresh activation.' \
        "$new_domain" "$caddy_mode" "$new_domain" "$new_token"
    ui_copyable_terminal_text "Domain changed" "$bootstrap_view" \
        "Copy and store the bootstrap code securely, then press Enter to return." || {
        unset new_token bootstrap_view
        return 1
    }
    unset new_token bootstrap_view
}

# Rotate the internal PostgreSQL role password with full rollback protection.
mp_rotate_database_password() {
    mp_require_ha_maintenance_window || return 1
    local password repeat staged escaped
    if ui_confirm "Database password" "Generate a new password automatically?"; then
        password="$(openssl rand -hex 32)"
    else
        password="$(ui_password "Database password" "Enter 24-128 safe characters")" || return 1
        repeat="$(ui_password "Database password" "Repeat the new password")" || return 1
        [ "$password" = "$repeat" ] || { ui_error "Passwords do not match."; return 1; }
        [ "${#password}" -ge 24 ] && [ "${#password}" -le 128 ] && [[ "$password" =~ ^[A-Za-z0-9._~-]+$ ]] \
            || { ui_error "Use only letters, numbers, dots, underscores, tildes or hyphens."; return 1; }
    fi
    mp_prepare_guard_snapshot "pre-db-password" || return 1
    ui_require_phrase "Rotate database password" \
        "The database role and container configuration will change together." \
        "ROTATE DATABASE PASSWORD" || { mp_clear_guard_snapshot; return 0; }
    mp_lock || { mp_clear_guard_snapshot; return 1; }
    staged="$(mktemp "$MP_ROOT/secrets/.database_password.XXXXXX")"
    printf '%s' "$password" > "$staged"
    chmod 600 "$staged"
    escaped="$(printf '%s' "$password" | sed "s/'/''/g")"
    mp_compose_init
    if ! printf "ALTER ROLE masterplan PASSWORD '%s';\n" "$escaped" \
        | "${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U masterplan -d postgres >/dev/null; then
        rm -f "$staged"; unset password repeat escaped
        mp_guard_rollback "Database role update failed."
        return 1
    fi
    if ! mv -f "$staged" "$MP_ROOT/secrets/database_password"; then
        rm -f "$staged"; unset password repeat escaped
        mp_guard_rollback "Database configuration installation failed."
        return 1
    fi
    rm -f "$staged"; unset password repeat escaped
    mp_prepare_backend_secret_permissions || { mp_guard_rollback "Database secret permissions could not be prepared."; return 1; }
    if ! mp_compose_validate; then mp_guard_rollback "Database configuration validation failed."; return 1; fi
    mp_compose_init
    if ! "${MP_COMPOSE[@]}" up -d --force-recreate db backend >/dev/null; then
        mp_guard_rollback "Database container recreation failed."
        return 1
    fi
    if ! mp_wait_for_health 30; then mp_guard_rollback "Database password health verification failed."; return 1; fi
    mp_audit "database.password-rotate" "success" "credentials-updated"
    mp_clear_guard_snapshot
    ui_message "Database password rotated" "The role, protected password file and running containers agree, and public health passed."
}

# Rotate the application secret and revoke all sessions derived from old state.
mp_rotate_application_secret() {
    local secret
    mp_require_ha_maintenance_window || return 1
    mp_prepare_guard_snapshot "pre-app-secret" || return 1
    ui_require_phrase "Rotate application secret" \
        "Every signed-in session will be revoked." \
        "ROTATE APPLICATION SECRET" || { mp_clear_guard_snapshot; return 0; }
    mp_lock || { mp_clear_guard_snapshot; return 1; }
    secret="$(mp_random_secret)"
    if ! printf '%s' "$secret" > "$MP_ROOT/secrets/secret_key" \
        || ! chmod 600 "$MP_ROOT/secrets/secret_key"; then
        unset secret
        mp_guard_rollback "Application secret installation failed."
        return 1
    fi
    unset secret
    mp_compose_init
    if ! "${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U masterplan -d masterplan \
        -c 'TRUNCATE auth_sessions, exchange_codes, passkey_ceremonies;' >/dev/null \
        || ! mp_recreate_backend; then
        mp_guard_rollback "Application secret rotation failed."
        return 1
    fi
    mp_audit "secret.application-rotate" "success" "sessions-revoked"
    mp_queue_ha_replication "application-secret-rotation" || true
    mp_clear_guard_snapshot
    ui_message "Application secret rotated" "All existing sessions were revoked and public health passed."
}

# Rotate only the daily IP-pseudonymisation key. Existing pseudonyms remain
# bounded by session and audit retention but are intentionally not linkable to
# values produced with the new key.
mp_rotate_ip_hmac_key() {
    local secret key_id staged
    mp_require_ha_maintenance_window || return 1
    mp_prepare_guard_snapshot "pre-ip-hmac-key" || return 1
    ui_require_phrase "Rotate IP HMAC key" \
        "Daily IP pseudonyms created with the old key will no longer match new pseudonyms. Existing sessions are not revoked." \
        "ROTATE IP HMAC KEY" || { mp_clear_guard_snapshot; return 0; }
    mp_lock || { mp_clear_guard_snapshot; return 1; }
    secret="$(mp_random_secret)"
    key_id="iphmac-$(printf '%s' "$secret" | sha256sum | cut -c1-16)"
    staged="$(mktemp "$MP_ROOT/secrets/.ip_hmac_key.XXXXXX")"
    if ! printf '%s' "$secret" > "$staged" \
        || ! chmod 600 "$staged" \
        || ! mv -f "$staged" "$MP_ROOT/secrets/ip_hmac_key"; then
        rm -f "$staged"; unset secret
        mp_guard_rollback "IP HMAC key installation failed."
        return 1
    fi
    unset secret
    if ! mp_recreate_backend; then
        mp_guard_rollback "IP HMAC key rotation failed."
        return 1
    fi
    mp_audit "secret.ip-hmac-rotate" "success" "key-id:${key_id}"
    mp_queue_ha_replication "ip-hmac-key-rotation" || true
    mp_clear_guard_snapshot
    ui_message "IP HMAC key rotated" \
        "The backend is healthy with key ID ${key_id}. Daily IP-pseudonym continuity with the retired key has ended."
}

# Rotate VAPID and remove subscriptions encrypted for the previous public key.
mp_rotate_vapid() {
    local vapid claims
    mp_require_ha_maintenance_window || return 1
    claims="$(ui_input "VAPID" "Contact email" "$(mp_env_get VAPID_CLAIMS_EMAIL 2>/dev/null | sed 's/^mailto://' || true)")" || return 1
    mp_validate_email_address "$claims" || { ui_error "Enter a valid contact email."; return 1; }
    mp_prepare_guard_snapshot "pre-vapid" || return 1
    ui_require_phrase "Rotate VAPID" \
        "Existing browser push subscriptions will be cleared and must be recreated." \
        "ROTATE VAPID" || { mp_clear_guard_snapshot; return 0; }
    mp_lock || { mp_clear_guard_snapshot; return 1; }
    vapid="$(mp_generate_vapid_private_key)"
    if ! printf '%s' "$vapid" > "$MP_ROOT/secrets/vapid_private_key" \
        || ! chmod 600 "$MP_ROOT/secrets/vapid_private_key"; then
        unset vapid
        mp_guard_rollback "VAPID secret installation failed."
        return 1
    fi
    unset vapid
    if ! mp_env_set VAPID_CLAIMS_EMAIL "mailto:${claims}"; then
        mp_guard_rollback "VAPID configuration installation failed."
        return 1
    fi
    mp_compose_init
    if ! "${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U masterplan -d masterplan \
        -c 'TRUNCATE push_subscriptions;' >/dev/null \
        || ! mp_recreate_backend; then
        mp_guard_rollback "VAPID rotation failed."
        return 1
    fi
    mp_audit "secret.vapid-rotate" "success" "subscriptions-cleared"
    mp_queue_ha_replication "vapid-rotation" || true
    mp_clear_guard_snapshot
    ui_message "VAPID rotated" "Push subscriptions were cleared and public health passed."
}

# Select and edit one runtime setting using backend-owned bounds and validation.
mp_manage_runtime_settings() {
    local json selected action current minimum maximum value
    local -a choices=()
    mp_require_active_or_standalone || return 1
    mp_compose_init
    json="$("${MP_COMPOSE[@]}" exec -T backend python -c \
        'import json; from app.db.database import SessionLocal; from app.core.runtime_settings import get_all; d=SessionLocal(); print(json.dumps(get_all(d))); d.close()')" || return 1
    while IFS=$'\t' read -r key label; do choices+=("$key" "$label"); done \
        < <(printf '%s' "$json" | jq -r 'to_entries[] | [.key, (.value.label + " | " + (.value.value|tostring) + " " + .value.unit)] | @tsv')
    selected="$(ui_menu "Runtime security settings" "Choose a setting" "${choices[@]}")" || return 1
    action="$(ui_menu "Runtime security settings" "Update or reset ${selected}" "update" "Set a validated value" "reset" "Remove the database override")" || return 1
    if [ "$action" = "reset" ]; then
        "${MP_COMPOSE[@]}" exec -T -e MP_SETTING_KEY="$selected" backend python -c \
            'import os; from app.db.database import SessionLocal; from app.models.server_setting import ServerSetting; d=SessionLocal(); d.query(ServerSetting).filter(ServerSetting.key==os.environ["MP_SETTING_KEY"]).delete(); d.commit(); d.close()'
        mp_audit "runtime-setting.reset" "success" "$selected"
        mp_queue_ha_replication "runtime-setting-reset" || true
        ui_message "Runtime setting" "$selected now uses its configured default."
        return 0
    fi
    current="$(printf '%s' "$json" | jq -r --arg key "$selected" '.[$key].value')"
    minimum="$(printf '%s' "$json" | jq -r --arg key "$selected" '.[$key].min')"
    maximum="$(printf '%s' "$json" | jq -r --arg key "$selected" '.[$key].max')"
    value="$(ui_input "Runtime setting" "Value between ${minimum} and ${maximum}" "$current")" || return 1
    [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -ge "$minimum" ] && [ "$value" -le "$maximum" ] \
        || { ui_error "The value is outside the allowed range."; return 1; }
    "${MP_COMPOSE[@]}" exec -T -e MP_SETTING_KEY="$selected" -e MP_SETTING_VALUE="$value" backend python -c \
        'import os; from app.db.database import SessionLocal; from app.core.runtime_settings import set_value; d=SessionLocal(); set_value(os.environ["MP_SETTING_KEY"], int(os.environ["MP_SETTING_VALUE"]), d); d.close()'
    mp_audit "runtime-setting.update" "success" "$selected"
    mp_queue_ha_replication "runtime-setting-update" || true
    ui_message "Runtime setting" "$selected was updated to $value."
}

# Display a fully redacted environment and protected-file metadata report.
mp_show_configuration() {
    local report
    report="$(mktemp "${MP_STATE}/configuration.XXXXXX")"
    {
        printf 'Redacted production configuration\n\n'
        mp_redacted_configuration
        printf '\nProtected file metadata\n\n'
        mp_permissions_report diagnostics
    } > "$report"
    ui_text_file "Configuration" "$report"
    rm -f "$report"
}

# Show Compose status and public health.
mp_service_status() {
    local report domain
    report="$(mktemp "${MP_STATE}/service-status.XXXXXX")"
    domain="$(mp_env_get DOMAIN)"
    mp_compose_init
    {
        "${MP_COMPOSE[@]}" ps
        printf '\nCaddy topology\n'
        mp_caddy_status
        printf '\nPublic health\n'
        curl -fsS --max-time 10 "https://${domain}/health"
        printf '\n'
    } > "$report" 2>&1 || true
    ui_text_file "Service status" "$report"
    rm -f "$report"
}

# Install the complete graphical and encrypted-recovery dependency set.
mp_install_management_dependencies() {
    sudo apt-get update \
        && sudo apt-get install -y age jq dialog whiptail
}

# Offer to install optional graphical and encrypted-recovery dependencies.
mp_offer_dependency_install() {
    local missing=()
    local item
    for item in age jq dialog whiptail; do
        command -v "$item" >/dev/null 2>&1 || missing+=("$item")
    done
    [ "${#missing[@]}" -gt 0 ] || return 0
    if ui_confirm "Management dependencies" \
        "Optional tools are missing: ${missing[*]}\n\nInstall them now for full-screen menus, logs and encrypted recovery?"; then
        if ! ui_run_command "Install management dependencies" \
            "Installing dialog, recovery and configuration tools" \
            mp_install_management_dependencies; then
            mp_audit "dependencies.install" "failed" "package-manager"
            ui_error "Management dependencies could not be installed. The available fallback interface will remain active."
            return 1
        fi
        mp_audit "dependencies.install" "success" "age,jq,dialog,whiptail"
    fi
}

# Start, stop or restart the requested Compose scope.
mp_service_action() {
    local action="$1"
    mp_lock || return 1
    mp_compose_init
    case "$action" in
        start)
            ui_run_command "Start services" "Starting application containers" \
                "${MP_COMPOSE[@]}" up -d \
                || { ui_error "Application services could not be started."; return 1; }
            mp_wait_for_health 30 \
                || { ui_error "Services started, but public health did not become ready."; return 1; }
            ;;
        stop)
            ui_confirm "Stop server" "Stop all application containers?" || return 0
            ui_run_command "Stop services" "Stopping application containers" \
                "${MP_COMPOSE[@]}" stop \
                || { ui_error "Application services could not be stopped cleanly."; return 1; }
            ;;
        restart)
            ui_run_command "Restart services" "Restarting application containers" \
                "${MP_COMPOSE[@]}" restart \
                || { ui_error "Application services could not be restarted."; return 1; }
            mp_wait_for_health 30 \
                || { ui_error "Services restarted, but public health did not become ready."; return 1; }
            ;;
        backend)
            ui_run_command "Recreate backend" "Recreating the application API container" \
                "${MP_COMPOSE[@]}" up -d --no-deps --force-recreate backend \
                || { ui_error "The backend container could not be recreated."; return 1; }
            mp_wait_for_health 30 \
                || { ui_error "The backend was recreated, but public health did not become ready."; return 1; }
            ;;
        *) return 1 ;;
    esac
    mp_audit "service.${action}" "success" "compose"
    ui_message "Services" "The ${action} operation completed."
}

# Run the canonical deployment script after explicit confirmation.
mp_deploy_latest() {
    local role automatic_was_enabled=false
    ui_confirm "Deploy" "Download, verify and deploy the latest signed stable release now?" || return 0
    mp_lock || return 1
    mp_load_ha_config || return 1
    role="$HA_ROLE"
    if [ "$role" = dynamic ]; then
        mp_require_active_or_standalone || return 1
        if [ "$(jq -r '.automatic_failover // false' "$MP_ROOT/runtime/ha-control.json")" = true ]; then
            automatic_was_enabled=true
            python3 "$MP_ROOT/deploy/ha/witness_control.py" automatic disabled >/dev/null || return 1
            mp_ha_set_config_value HA_AUTOMATIC_FAILOVER disabled || return 1
        fi
        ui_run_command "Deploy Node B" \
            "Installing and verifying the signed release on the peer first" \
            ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
            "for i in \$(seq 1 30); do [ \"\$(jq -r '.automatic_failover // true' /opt/masterplan/runtime/ha-control.json 2>/dev/null)\" = false ] && break; sleep 1; done; [ \"\$(jq -r '.automatic_failover // true' /opt/masterplan/runtime/ha-control.json)\" = false ] && python3 /opt/masterplan/deploy/release/install_release.py --repo-root /opt/masterplan && /opt/masterplan/deploy/deploy.sh --no-pull" \
            || { ui_error "Node B deployment failed. Automatic failover remains disabled."; return 1; }
    fi
    if ui_run_command "Deploy latest release" \
        "Verifying release signatures, installing immutable images and checking health" \
        bash -Eeuo pipefail -c \
        'python3 "$1/deploy/release/install_release.py" --repo-root "$1" && "$1/deploy/deploy.sh" --no-pull' \
        mp-opt-release "$MP_ROOT"; then
        mp_audit "deploy.latest" "success" "$(grep -m1 '^MP_RELEASE_TAG=' "$MP_ROOT/.release.env" | cut -d= -f2-)"
    else
        mp_audit "deploy.latest" "failed" "deploy-script"
        ui_error "Deployment stopped. Review the displayed output and logs."
        return 1
    fi
    mp_unlock
    if [ "$role" = dynamic ]; then
        mp_ha_replicate_now || { ui_error "Both nodes run the release, but the fresh peer copy failed. Automatic failover remains disabled."; return 1; }
        mp_ha_refresh_witness_observations || { ui_error "The release is installed, but current peer observations could not be refreshed. Automatic failover remains disabled."; return 1; }
        mp_ha_active_verification_readiness || { ui_error "The release is installed, but HA readiness has not converged. Automatic failover remains disabled."; return 1; }
        if [ "$automatic_was_enabled" = true ]; then
            python3 "$MP_ROOT/deploy/ha/witness_control.py" automatic enabled >/dev/null || return 1
            mp_ha_set_config_value HA_AUTOMATIC_FAILOVER enabled || return 1
        fi
    fi
    ui_message "Deploy" "Deployment and public health verification completed.$([ "$role" = dynamic ] && printf ' Both nodes match and a fresh verified copy was accepted.' || true)"
}

# Select the server-side trust boundary for unsigned exact-commit deployments.
mp_manage_deployment_policy() {
    local current choice role
    current="$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)"
    choice="$(ui_menu "Deployment policy" "Current policy: ${current}" \
        "production" "Signed production releases only" \
        "test" "Disposable test data; allow unsigned pushed commits" \
        "back" "Keep the current policy")" || return 0
    [ "$choice" != back ] && [ -n "$choice" ] || return 0
    [ "$choice" != "$current" ] || return 0
    mp_load_ha_config || return 1
    role="$HA_ROLE"
    if [ "$choice" = test ]; then
        ui_require_phrase "Enable test deployment policy" \
            "Unsigned commits may execute application, proxy, database and HA code. Use this only with disposable data. Production profiles remain signed-release only." \
            "ENABLE UNSIGNED TEST DEPLOYMENTS" || return 0
        if [ "$role" = dynamic ]; then
            ssh -T -o BatchMode=yes mp-opt-ha-peer \
                env MP_ROOT=/opt/masterplan /opt/masterplan/deploy/test-deployment.sh policy test || return 1
        fi
        "$MP_ROOT/deploy/test-deployment.sh" policy test || return 1
    else
        if [ -f "$MP_ROOT/.test-deployment.env" ]; then
            ui_confirm "Restore signed release" \
                "The exact signed baseline must be restored on both nodes before production policy can be enabled." || return 0
            ui_run_command "Restore signed release" "Removing unsigned artefacts and verifying the signed baseline" \
                "$MP_ROOT/deploy/test-deployment.sh" restore-signed || return 1
        fi
        if [ "$role" = dynamic ]; then
            ssh -T -o BatchMode=yes mp-opt-ha-peer \
                env MP_ROOT=/opt/masterplan /opt/masterplan/deploy/test-deployment.sh policy production || return 1
        fi
        "$MP_ROOT/deploy/test-deployment.sh" policy production || return 1
    fi
    mp_audit "deploy.policy" "success" "$choice"
    ui_message "Deployment policy" "Both nodes now use the ${choice} deployment policy."
}

mp_test_deployment_status() {
    local report
    report="$(mktemp "$MP_STATE/test-deployment-status.XXXXXX")" || return 1
    "$MP_ROOT/deploy/test-deployment.sh" status > "$report" || { rm -f "$report"; return 1; }
    ui_text_file "Test deployment status" "$report"
    rm -f "$report"
}

mp_test_deployment_apply() {
    local commit plan_file full migrations components worker token
    local -a arguments
    [ "$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)" = test ] \
        || { ui_error "Change Configuration > Deployment policy to test first. Production-policy servers accept signed releases only."; return 1; }
    commit="$(ui_input "Unsigned test deployment" "Exact pushed 40-character Git commit")" || return 1
    [[ "$commit" =~ ^[0-9a-f]{40}$ ]] \
        || { ui_error "Enter the exact lowercase 40-character commit shown by git rev-parse HEAD."; return 1; }
    plan_file="$(mktemp "$MP_STATE/test-deployment-plan.XXXXXX")" || return 1
    if ! "$MP_ROOT/deploy/test-deployment.sh" plan "$commit" > "$plan_file"; then
        rm -f "$plan_file"
        ui_error "The pushed commit could not be fetched and planned."
        return 1
    fi
    ui_text_file "Unsigned deployment plan" "$plan_file"
    full="$(jq -r .full "$plan_file")"
    migrations="$(jq -r .migrations "$plan_file")"
    components="$(jq -r '.components | join(" ")' "$plan_file")"
    rm -f "$plan_file"
    arguments=(apply "$commit")
    if [ "$full" = true ]; then
        ui_require_phrase "Full unsigned deployment" \
            "Affected components: ${components}. Automatic failover will be disabled while both nodes are staged and verified." \
            "DEPLOY FULL TEST BUILD" || return 0
        arguments+=(--confirm-full)
    else
        ui_confirm "Backend test deployment" "Build and roll this exact backend commit across the test installation?" || return 0
    fi
    if [ "$migrations" = true ]; then
        ui_require_phrase "Unsigned database migration" \
            "A verified encrypted full snapshot will be created before applying the migration to both disposable test databases." \
            "MIGRATE DISPOSABLE TEST DATA" || return 0
        arguments+=(--confirm-migrations)
    fi
    if grep -qw witness <<< "$components"; then
        worker="$(ui_input "Cloudflare test Worker" "Existing test Worker name")" || return 1
        [[ "$worker" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || { ui_error "Invalid Worker name."; return 1; }
        token="$(ui_password "Cloudflare test Worker" "Temporary Worker deployment API token")" || return 1
        [ "${#token}" -ge 32 ] || { unset token; ui_error "The token appears incomplete."; return 1; }
        export CLOUDFLARE_API_TOKEN="$token"
        export MP_TEST_WORKER_NAME="$worker"
    fi
    ui_run_command "Deploy unsigned test build" "Building affected components and verifying every selected node" \
        "$MP_ROOT/deploy/test-deployment.sh" "${arguments[@]}" || {
            unset token CLOUDFLARE_API_TOKEN MP_TEST_WORKER_NAME
            ui_error "The test deployment did not complete. Automatic failover remains disabled unless consistency was proved."
            return 1
        }
    unset token CLOUDFLARE_API_TOKEN MP_TEST_WORKER_NAME
    ui_message "Unsigned test deployment" "Commit ${commit} passed deployment health checks. Use Test deployment status for the copyable receipt."
}

mp_test_deployment_rollback() {
    local components worker token
    ui_require_phrase "Rollback unsigned deployment" \
        "Restore the preceding test commit, or the signed baseline when this was the first unsigned deployment." \
        "ROLL BACK TEST BUILD" || return 0
    components="$(jq -r '.plan.components // [] | join(" ")' "$MP_STATE/test-deployments/current.json" 2>/dev/null || true)"
    if grep -qw witness <<< "$components"; then
        worker="$(ui_input "Cloudflare test Worker" "Existing test Worker name")" || return 1
        [[ "$worker" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || { ui_error "Invalid Worker name."; return 1; }
        token="$(ui_password "Cloudflare test Worker" "Temporary Worker rollback API token")" || return 1
        [ "${#token}" -ge 32 ] || { unset token; ui_error "The token appears incomplete."; return 1; }
        export CLOUDFLARE_API_TOKEN="$token"
        export MP_TEST_WORKER_NAME="$worker"
    fi
    ui_run_command "Rollback test deployment" "Restoring and health-checking the preceding component set" \
        "$MP_ROOT/deploy/test-deployment.sh" rollback || {
            unset token CLOUDFLARE_API_TOKEN MP_TEST_WORKER_NAME
            return 1
        }
    unset token CLOUDFLARE_API_TOKEN MP_TEST_WORKER_NAME
    ui_message "Test deployment rollback" "The preceding deployment passed health verification."
}

mp_test_deployment_restore_signed() {
    ui_require_phrase "Restore signed baseline" \
        "Remove unsigned overrides and reinstall the exact signed baseline recorded before testing." \
        "RESTORE SIGNED BASELINE" || return 0
    ui_run_command "Restore signed baseline" "Verifying signed assets and restoring both nodes" \
        "$MP_ROOT/deploy/test-deployment.sh" restore-signed || return 1
    ui_message "Signed baseline restored" "Unsigned overrides were removed and signed public health passed."
}

# Change the WebAuthn display name without changing RP identity or credentials.
mp_change_application_name() {
    local current name backup
    mp_require_active_or_standalone || return 1
    current="$(mp_env_get WEBAUTHN_RP_NAME 2>/dev/null || printf 'Masterplan Access')"
    name="$(ui_input "Application name" "Passkey application display name" "$current")" || return 1
    [ -n "$name" ] && [ "${#name}" -le 80 ] && mp_validate_env_value "$name" \
        || { ui_error "Use a non-empty single-line name of at most 80 characters."; return 1; }
    [ "$name" != "$current" ] || return 0
    backup="$(mktemp /dev/shm/mp-opt-app-name.XXXXXX 2>/dev/null || mktemp)" || return 1
    cp -a "$MP_ROOT/.env" "$backup" || { rm -f "$backup"; return 1; }
    cmp -s "$MP_ROOT/.env" "$backup" || { rm -f "$backup"; return 1; }
    mp_env_set WEBAUTHN_RP_NAME "$name" || { rm -f "$backup"; return 1; }
    if ! mp_recreate_backend; then
        cp -a "$backup" "$MP_ROOT/.env" || {
            rm -f "$backup"
            return 1
        }
        cmp -s "$backup" "$MP_ROOT/.env" || { rm -f "$backup"; return 1; }
        mp_recreate_backend || true
        rm -f "$backup"
        mp_audit "application-name.change" "failed" "rolled-back"
        ui_error "The new application name failed validation. The previous value was restored."
        return 1
    fi
    rm -f "$backup"
    mp_audit "application-name.change" "success" "display-only"
    mp_queue_ha_replication "application-name" || true
    ui_message "Application name" "The passkey display name is now: $name"
}

# Build only the exported web frontend through the pinned Node container.
mp_rebuild_frontend() {
    mp_lock || return 1
    if ! mp_prepare_frontend_csp_runtime; then
        mp_audit "frontend.rebuild" "failed" "csp-runtime"
        ui_error "The protected frontend policy directory could not be prepared."
        return 1
    fi
    if ! ui_run_command "Rebuild frontend" \
        "Installing pinned packages and building the static frontend" \
        docker run --rm -v "$MP_ROOT/web:/app" -w /app node:22-alpine \
        sh -c 'npm ci --no-audit && npm audit --omit=dev --audit-level=high && npm run lint && npm run build'; then
        mp_audit "frontend.rebuild" "failed" "build"
        return 1
    fi
    if ! python3 "$MP_ROOT/deploy/stamp_service_worker.py" "$MP_ROOT/web/out/sw.js" \
        "$(git -C "$MP_ROOT" rev-parse HEAD)"; then
        ui_error "The service-worker release could not be stamped."
        return 1
    fi
    if ! python3 "$MP_ROOT/deploy/generate_frontend_csp.py" "$MP_ROOT/web/out" \
        --output "$MP_ROOT/runtime/frontend-csp.caddy"; then
        mp_audit "frontend.rebuild" "failed" "csp-generation"
        ui_error "The frontend was built, but its security policy could not be generated."
        return 1
    fi
    mp_caddy_reload || {
        mp_audit "frontend.rebuild" "failed" "caddy-reload"
        ui_error "The frontend was built, but Caddy could not be reloaded."
        return 1
    }
    mp_caddy_validate || {
        mp_audit "frontend.rebuild" "failed" "caddy-validation"
        ui_error "The frontend was built, but Caddy validation failed."
        return 1
    }
    mp_wait_for_health 15 || {
        mp_audit "frontend.rebuild" "failed" "public-health"
        ui_error "The frontend was built, but public health did not recover."
        return 1
    }
    mp_audit "frontend.rebuild" "success" "static-export"
    ui_message "Frontend" "The static frontend was rebuilt and the active Caddy topology was verified."
}

# Print one bounded service log selection to stdout.
mp_collect_logs() {
    local service="$1"
    local mode="$2"
    local value="$3"
    mp_compose_init
    if [ "$service" = "caddy" ]; then
        case "$(mp_caddy_mode)" in
            container)
                if [ "$mode" = "recent" ]; then
                    "${MP_COMPOSE[@]}" logs --tail "$value" caddy
                else
                    "${MP_COMPOSE[@]}" logs --since "$value" caddy
                fi
                ;;
            host)
                if [ "$mode" = "recent" ]; then
                    sudo journalctl -u caddy -n "$value" --no-pager
                else
                    sudo journalctl -u caddy --since "-${value}" --no-pager
                fi
                ;;
            *) printf 'No managed Caddy log source is available.\n' >&2; return 1 ;;
        esac
    elif [ "$service" = "all" ]; then
        if [ "$mode" = "recent" ]; then
            "${MP_COMPOSE[@]}" logs --tail "$value"
        else
            "${MP_COMPOSE[@]}" logs --since "$value"
        fi
    elif [ "$mode" = "recent" ]; then
        "${MP_COMPOSE[@]}" logs --tail "$value" "$service"
    else
        "${MP_COMPOSE[@]}" logs --since "$value" "$service"
    fi
}

# Follow one service log source until the caller terminates this producer.
mp_follow_logs() {
    local service="$1"
    mp_compose_init
    if [ "$service" = "caddy" ]; then
        case "$(mp_caddy_mode)" in
            container) "${MP_COMPOSE[@]}" logs -f --tail 100 caddy ;;
            host) sudo journalctl -u caddy -f -n 100 ;;
            *) printf 'No managed Caddy log source is available.\n' >&2; return 1 ;;
        esac
    elif [ "$service" = "all" ]; then
        "${MP_COMPOSE[@]}" logs -f --tail 100
    else
        "${MP_COMPOSE[@]}" logs -f --tail 100 "$service"
    fi
}

# Print a process tree below one parent in leaves-first termination order.
mp_log_process_descendants() {
    local parent="$1"
    local child
    while IFS= read -r child; do
        [ -n "$child" ] || continue
        mp_log_process_descendants "$child"
        printf '%s\n' "$child"
    done < <(pgrep -P "$parent" 2>/dev/null || true)
}

# Stop a background log producer and its complete recorded process tree.
mp_stop_log_producer() {
    local producer="$1"
    local process attempt running
    local -a processes=()
    mapfile -t processes < <(mp_log_process_descendants "$producer")
    processes+=("$producer")

    for process in "${processes[@]}"; do
        kill -TERM "$process" 2>/dev/null || true
    done
    for attempt in 1 2 3 4 5; do
        running=0
        for process in "${processes[@]}"; do
            if kill -0 "$process" 2>/dev/null; then
                running=1
                break
            fi
        done
        [ "$running" -eq 0 ] && break
        sleep 0.1
    done
    for process in "${processes[@]}"; do
        kill -KILL "$process" 2>/dev/null || true
    done
    wait "$producer" 2>/dev/null || true
}

# Capture and display a static log selection in the protected text viewer.
mp_show_static_logs() {
    local service="$1"
    local mode="$2"
    local value="$3"
    local raw safe status title
    raw="$(mktemp "${MP_STATE}/logs.raw.XXXXXX")" || return 1
    safe="$(mktemp "${MP_STATE}/logs.safe.XXXXXX")" || { rm -f "$raw"; return 1; }
    chmod 600 "$raw" "$safe" || { rm -f "$raw" "$safe"; return 1; }
    status=0
    mp_collect_logs "$service" "$mode" "$value" > "$raw" 2>&1 || status=$?
    mp_sanitise_terminal_stream < "$raw" > "$safe" || {
        rm -f "$raw" "$safe"
        return 1
    }
    if [ ! -s "$safe" ]; then
        printf 'No log entries matched this selection.\n' > "$safe"
    fi
    title="Logs | ${service} | ${mode}"
    [ "$status" -eq 0 ] || title="Logs failed | ${service} | ${mode}"
    ui_text_file "$title" "$safe" || true
    rm -f "$raw" "$safe"
    [ "$status" -eq 0 ]
}

# Display live logs without allowing the producer or Ctrl+C to close the menu.
mp_show_live_logs() {
    local service="$1"
    local report producer viewer_status
    local -a stream_statuses
    report="$(mktemp "${MP_STATE}/logs.live.XXXXXX")" || return 1
    chmod 600 "$report" || { rm -f "$report"; return 1; }
    printf 'Connecting to %s logs...\n' "$service" > "$report"
    (
        set -o pipefail
        set +e
        mp_follow_logs "$service" 2>&1 | mp_sanitise_terminal_stream
        stream_statuses=("${PIPESTATUS[@]}")
        if [ "${stream_statuses[1]}" -ne 0 ]; then
            printf '\nThe live log sanitiser stopped unexpectedly (exit status %s).\n' \
                "${stream_statuses[1]}"
        elif [ "${stream_statuses[0]}" -ne 0 ]; then
            printf '\nThe live log source stopped unexpectedly (exit status %s). Check the service status or recent logs for details.\n' \
                "${stream_statuses[0]}"
        else
            printf '\nThe live log stream ended. Press Return to choose another view.\n'
        fi
    ) >> "$report" &
    producer=$!
    viewer_status=0
    set +e
    ui_live_text_file "Live logs | ${service}" "$report"
    viewer_status=$?
    set -e
    mp_stop_log_producer "$producer"
    rm -f "$report"
    [ "$viewer_status" -eq 0 ] || [ "$viewer_status" -eq 1 ] || [ "$viewer_status" -eq 130 ]
}

# Open the persistent log source and viewing-mode menus.
mp_logs() {
    local service mode value
    while true; do
        service="$(ui_menu "Logs" "Select a source" \
            "backend" "Application API" \
            "db" "PostgreSQL" \
            "caddy" "Caddy ($(mp_caddy_mode))" \
            "all" "All application containers" \
            "back" "Return to the main menu")" || return 0
        [ "$service" != "back" ] && [ -n "$service" ] || return 0
        mode="$(ui_menu "Logs" "Choose how to view ${service}" \
            "recent" "Recent lines in a scrollable window" \
            "since" "Entries from a duration such as 30m" \
            "follow" "Live full-screen view" \
            "back" "Choose another source")" || continue
        case "$mode" in
            recent)
                value="$(ui_input "Logs" "Number of lines, from 1 to 5000" "200")" || continue
                [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -ge 1 ] && [ "$value" -le 5000 ] \
                    || { ui_error "Choose a number from 1 to 5000."; continue; }
                mp_show_static_logs "$service" recent "$value" || true
                ;;
            since)
                value="$(ui_input "Logs" "Duration, for example 30m, 2h or 1d" "30m")" || continue
                [[ "$value" =~ ^[0-9]+[mhd]$ ]] \
                    || { ui_error "Use a duration such as 30m, 2h or 1d."; continue; }
                mp_show_static_logs "$service" since "$value" || true
                ;;
            follow) mp_show_live_logs "$service" || true ;;
            back|"") continue ;;
        esac
    done
}

# Create a non-secret diagnostics report for support and local inspection.
mp_diagnostics() {
    local report domain
    report="${MP_STATE}/diagnostics-$(date -u +%Y%m%dT%H%M%SZ).txt"
    domain="$(mp_env_get DOMAIN 2>/dev/null || printf unavailable)"
    {
        printf 'MP-OPT_SERVER redacted diagnostics\n'
        printf 'Created: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'Host: %s\nDomain: %s\n' "$(hostname -f 2>/dev/null || hostname)" "$domain"
        printf 'HA role: %s\n' "$(mp_ha_role 2>/dev/null || printf invalid)"
        printf 'Commit: %s\n\n' "$(git -C "$MP_ROOT" rev-parse HEAD 2>/dev/null || printf unknown)"
        mp_permissions_report
        printf '\nCompose validation: '
        if mp_compose_validate; then printf 'valid\n'; else printf 'invalid\n'; fi
        printf '\nContainers\n'
        mp_compose_init; "${MP_COMPOSE[@]}" ps 2>&1 || true
        printf '\nDocker usage\n'; docker system df 2>&1 || true
        printf '\nDisk usage\n'; df -h "$MP_ROOT" "$MP_SNAPSHOTS" 2>&1 || true
        printf '\nCaddy status\n'; mp_caddy_status
        printf '\nHealth\n'; curl -fsS --max-time 5 "https://${domain}/health" 2>&1 || true
        if [ "$(mp_ha_role 2>/dev/null || printf standalone)" != "standalone" ]; then
            printf '\nHA lease observation\n'
            jq . "$MP_ROOT/runtime/ha-control.json" 2>/dev/null || printf 'unavailable\n'
            printf '\nHA replication\n'
            jq . "$MP_ROOT/runtime/ha-replication.json" 2>/dev/null || printf 'unavailable\n'
            printf '\nHA configuration\n'
            mp_load_ha_config
            printf 'node=%s role=%s cluster=%s generation=%s automatic=%s\n' \
                "$HA_NODE_ID" "$HA_ROLE" "$HA_CLUSTER_ID" \
                "$(jq -r '.generation // "unknown"' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null || printf unknown)" \
                "${HA_AUTOMATIC_FAILOVER:-disabled}"
            printf 'lease witness: '
            jq -e '.holder_node_id and .generation' "$MP_ROOT/runtime/ha-control.json" >/dev/null 2>&1 \
                && printf 'observed\n' || printf 'unavailable\n'
        fi
    } > "$report"
    chmod 600 "$report"
    mp_audit "diagnostics.create" "success" "$(basename "$report")"
    ui_message "Diagnostics created" "$report\n\nThe report contains metadata but no environment values or secret contents."
}

# Capture a redacted, hash-verifiable recovery checkpoint without changing data.
mp_collect_recovery_evidence() {
    local label="$1"
    local timestamp evidence_root evidence_dir temporary_dir domain audit_status
    local table result
    local -a durable_tables=(
        events
        published_persons
        published_person_unavailability
        published_tasks
        task_edits
        publish_snapshots
        published_general_schedule_categories
        published_general_schedule_items
        general_schedule_publish_state
        announcements
        public_schedule_links
        public_schedule_link_views
        server_settings
    )

    mp_validate_snapshot_name "$label" || {
        ui_error "Evidence labels may contain 1-64 letters, numbers, dots, underscores or hyphens."
        return 1
    }
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    evidence_root="$MP_STATE/recovery-evidence"
    evidence_dir="$evidence_root/${timestamp}_${label}"
    temporary_dir="${evidence_dir}.partial"
    mkdir -p "$evidence_root" || return 1
    chmod 700 "$evidence_root" || return 1
    [ ! -e "$evidence_dir" ] && [ ! -e "$temporary_dir" ] || return 1
    mkdir -m 0700 "$temporary_dir" || return 1
    domain="$(mp_env_get DOMAIN)" || return 1
    mp_compose_init

    if mp_verify_audit_chain; then audit_status="valid"; else audit_status="INVALID"; fi
    [ "$audit_status" = "valid" ] || {
        rm -rf "$temporary_dir"
        ui_error "The management audit chain is invalid. Evidence collection was stopped."
        return 1
    }
    [ -z "$(git -C "$MP_ROOT" status --short 2>/dev/null)" ] || {
        rm -rf "$temporary_dir"
        ui_error "The repository worktree is changed. Commit or restore it before collecting evidence."
        return 1
    }
    curl -fsS --max-time 10 "https://${domain}/health" >/dev/null || {
        rm -rf "$temporary_dir"
        ui_error "Public health is unavailable. Evidence collection was stopped."
        return 1
    }
    {
        printf 'MP-OPT_SERVER recovery evidence\n'
        printf 'Created: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'Label: %s\n' "$label"
        printf 'Host: %s\n' "$(hostname -f 2>/dev/null || hostname)"
        printf 'Commit: %s\n' "$(git -C "$MP_ROOT" rev-parse HEAD 2>/dev/null || printf unknown)"
        if [ -z "$(git -C "$MP_ROOT" status --short 2>/dev/null)" ]; then
            printf 'Worktree: clean\n'
        else
            printf 'Worktree: CHANGED\n'
        fi
        printf 'Audit chain: %s\n' "$audit_status"
        printf 'Public health: '
        curl -fsS --max-time 10 "https://${domain}/health" || printf 'UNAVAILABLE'
        printf '\n'
    } > "$temporary_dir/summary.txt"

    {
        printf 'service\tstate\thealth\tstatus\n'
        "${MP_COMPOSE[@]}" ps --format json \
            | jq -r '[.Service, .State, (.Health // ""), .Status] | @tsv'
    } > "$temporary_dir/services.tsv"

    {
        printf 'path\tmode\towner\tsha256\n'
        while IFS= read -r -d '' file; do
            printf '%s\t%s\t%s:%s\t%s\n' \
                "${file#"$MP_ROOT/"}" \
                "$(stat -c '%a' "$file")" \
                "$(stat -c '%U' "$file")" \
                "$(stat -c '%G' "$file")" \
                "$(sha256sum "$file" | awk '{print $1}')"
        done < <(
            {
                find "$MP_ROOT/secrets" -maxdepth 1 -type f -print0
                printf '%s\0' "$MP_ROOT/.env"
                [ ! -f "$MP_ROOT/infra/docker-compose.override.yml" ] \
                    || printf '%s\0' "$MP_ROOT/infra/docker-compose.override.yml"
                [ ! -f "$MP_ROOT/runtime/frontend-csp.caddy" ] \
                    || printf '%s\0' "$MP_ROOT/runtime/frontend-csp.caddy"
            } | sort -z
        )
    } > "$temporary_dir/protected-files.tsv"

    {
        printf 'table\trows\tcontent_md5\n'
        for table in "${durable_tables[@]}"; do
            result="$("${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 \
                -U masterplan -d masterplan -At -F $'\t' -c \
                "SELECT count(*), md5(COALESCE(string_agg(to_jsonb(t)::text, E'\\n' ORDER BY to_jsonb(t)::text), '')) FROM ${table} AS t;")" \
                || return 1
            printf '%s\t%s\n' "$table" "$result"
        done
        result="$("${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 \
            -U masterplan -d masterplan -At -F $'\t' -c \
            "SELECT count(*), md5(COALESCE(string_agg(to_jsonb(t)::text, E'\\n' ORDER BY to_jsonb(t)::text), '')) FROM (SELECT id, username, display_name, email, is_root_admin, is_admin, is_issuer, can_edit, is_active, is_activated, linked_person_id, event_id, tags, deletion_requested_at, created_at FROM users) AS t;")" \
            || return 1
        printf 'users_stable\t%s\n' "$result"
        result="$("${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 \
            -U masterplan -d masterplan -At -F $'\t' -c \
            "SELECT count(*), md5(COALESCE(string_agg(to_jsonb(t)::text, E'\\n' ORDER BY to_jsonb(t)::text), '')) FROM (SELECT id, user_id, credential_id, public_key, transports, aaguid, friendly_name, created_at FROM webauthn_credentials) AS t;")" \
            || return 1
        printf 'webauthn_credentials_stable\t%s\n' "$result"
    } > "$temporary_dir/database-fingerprints.tsv"

    "${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U masterplan -d masterplan -At -F $'\t' -c \
        "SELECT c.table_name, c.ordinal_position, c.column_name, c.data_type, c.is_nullable, COALESCE(c.column_default, '') FROM information_schema.columns c WHERE c.table_schema='public' ORDER BY c.table_name, c.ordinal_position;" \
        | sha256sum | awk '{print $1 "  information-schema"}' \
        > "$temporary_dir/schema.sha256"

    {
        printf 'invariant\tstatus\n'
        mp_database_schema_contract_report
    } > "$temporary_dir/schema-contract.tsv"

    {
        find "$MP_SNAPSHOTS" -mindepth 2 -maxdepth 2 -type f -name snapshot.tar.age -print0 2>/dev/null \
            | sort -z | xargs -0 -r sha256sum
    } > "$temporary_dir/snapshot-archives.sha256"

    if [ "$(mp_ha_role 2>/dev/null || printf standalone)" != "standalone" ]; then
        mp_load_ha_config || return 1
        {
            printf 'node_id=%s\nrole=%s\ncluster_id=%s\ngeneration=%s\nautomatic=%s\n' \
                "$HA_NODE_ID" "$HA_ROLE" "$HA_CLUSTER_ID" \
                "$(jq -r '.generation // "unknown"' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null || printf unknown)" \
                "${HA_AUTOMATIC_FAILOVER:-disabled}"
            printf 'lease_witness=%s\n' "$(jq -r 'if .holder_node_id and .generation then "observed" else "unavailable" end' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null || printf unavailable)"
            printf 'node_config_sha256=%s\n' "$(sha256sum "$MP_HA_CONFIG" | awk '{print $1}')"
            printf 'shared_env_sha256=%s\n' "$(sha256sum "$MP_ROOT/.env" | awk '{print $1}')"
            printf 'shared_secrets_sha256=%s\n' "$(find "$MP_ROOT/secrets" -maxdepth 1 -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')"
            printf 'database_state='
            "${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d masterplan -At -F, -c \
                "SELECT pg_is_in_recovery(), current_setting('transaction_read_only'), CASE WHEN pg_is_in_recovery() THEN pg_last_wal_replay_lsn()::text ELSE pg_current_wal_lsn()::text END;" \
                2>/dev/null || printf 'unavailable'
            printf '\n'
        } > "$temporary_dir/high-availability.txt"
        if [ -f "$MP_ROOT/runtime/ha-control.json" ]; then
            jq '{node_id, holder_node_id, generation, observed_at, routing_ready, automatic_failover, error_type}' \
                "$MP_ROOT/runtime/ha-control.json" > "$temporary_dir/ha-control-witness.json"
        fi
        [ ! -f "$MP_ROOT/runtime/ha-replication.json" ] \
            || cp "$MP_ROOT/runtime/ha-replication.json" "$temporary_dir/ha-replication.json"
    fi

    (
        cd "$temporary_dir"
        find . -maxdepth 1 -type f ! -name evidence.sha256 -print0 \
            | sort -z | xargs -0 sha256sum > evidence.sha256
        sha256sum -c evidence.sha256 >/dev/null
    ) || return 1
    chmod 600 "$temporary_dir/"*
    mv "$temporary_dir" "$evidence_dir" || return 1
    mp_audit "recovery-evidence.create" "success" "$label"
    printf '%s\n' "$evidence_dir"
}

# Ask for a checkpoint label and display the verified evidence location.
mp_collect_recovery_evidence_interactive() {
    local label evidence_dir
    label="$(ui_input "Recovery evidence" "Choose a short checkpoint label")" || return 1
    evidence_dir="$(mp_collect_recovery_evidence "$label")" || {
        ui_error "Recovery evidence collection failed. No checkpoint was accepted."
        return 1
    }
    ui_message "Recovery evidence created" \
        "$evidence_dir\n\nThe bundle contains hashes and metadata, but no secret values or database rows."
}

# Validate Compose, Caddy, health and protected file permissions.
mp_validate_installation() {
    local report failed=0 mode
    report="$(mktemp "${MP_STATE}/validation.XXXXXX")"
    {
        printf 'MP-OPT_SERVER installation validation\n\n'
        printf 'Compose: '
        if mp_compose_validate; then printf 'valid\n'; else printf 'INVALID\n'; failed=1; fi
        printf 'Caddy: '
        if mp_caddy_validate >/dev/null 2>&1; then printf '%s: valid\n' "$(mp_caddy_mode)"; else printf '%s: INVALID\n' "$(mp_caddy_mode)"; failed=1; fi
        printf 'Public health: '
        if curl -fsS --max-time 5 "https://$(mp_env_get DOMAIN)/health" >/dev/null; then printf 'healthy\n'; else printf 'UNAVAILABLE\n'; failed=1; fi
        printf '\nProtected files\n'
        mp_permissions_report
        while IFS= read -r file; do
            mode="$(stat -c '%a' "$file")"
            if [ "$mode" != "600" ]; then printf 'UNSAFE MODE: %s is %s\n' "$file" "$mode"; failed=1; fi
        done < <(find "$MP_ROOT/secrets" -maxdepth 1 -type f -print; printf '%s\n' "$MP_ROOT/.env")
    } > "$report"
    ui_text_file "Installation validation" "$report"
    rm -f "$report"
    return "$failed"
}

# Render deployment-specific inventory status without exposing secret values.
mp_cryptographic_inventory() {
    local report
    report="$(mktemp "${MP_STATE}/cryptographic-inventory.XXXXXX")" || return 1
    if ! python3 "$MP_ROOT/deploy/security/cryptographic_inventory.py" report \
        --root "$MP_ROOT" \
        --home "$HOME" \
        --recovery-recipient "$MP_RECIPIENT_FILE" > "$report"; then
        rm -f "$report"
        ui_error "The cryptographic inventory is incomplete or invalid."
        return 1
    fi
    chmod 600 "$report"
    ui_text_file "Cryptographic inventory" "$report"
    rm -f "$report"
}

# View or update the protected provider/workstation security decision record.
mp_storage_security_checklist() {
    local action selected status value_format value reference report
    local -a choices=()
    if [ ! -f "$MP_STORAGE_CHECKLIST_FILE" ]; then
        python3 "$MP_ROOT/deploy/security/storage_security_checklist.py" initialise \
            --output "$MP_STORAGE_CHECKLIST_FILE" || return 1
    fi
    action="$(ui_menu "Storage security" \
        "Record non-secret decisions and evidence references. This does not inspect protected data or provider accounts." \
        "view" "View readiness and outstanding controls" \
        "update" "Update one provider or workstation control")" || return 1
    if [ "$action" = "view" ]; then
        report="$(mktemp "${MP_STATE}/storage-security.XXXXXX")" || return 1
        python3 "$MP_ROOT/deploy/security/storage_security_checklist.py" report \
            --file "$MP_STORAGE_CHECKLIST_FILE" > "$report" \
            || { rm -f "$report"; ui_error "The storage security checklist is invalid."; return 1; }
        chmod 600 "$report"
        ui_text_file "Storage security" "$report"
        rm -f "$report"
        return 0
    fi
    while IFS=$'\t' read -r control label format; do
        choices+=("$control" "$label")
    done < <(python3 "$MP_ROOT/deploy/security/storage_security_checklist.py" list \
        --file "$MP_STORAGE_CHECKLIST_FILE") || return 1
    selected="$(ui_menu "Storage security" "Choose one control" "${choices[@]}")" || return 1
    status="$(ui_menu "Storage security" "Record the reviewed state for ${selected}" \
        "pass" "Requirement confirmed" \
        "fail" "Requirement not satisfied" \
        "not_checked" "Clear the current decision" \
        "not_applicable" "Not applicable where the template permits it")" || return 1
    value="-"
    reference="-"
    if [ "$status" != "not_checked" ]; then
        value_format="$(python3 "$MP_ROOT/deploy/security/storage_security_checklist.py" list \
            --file "$MP_STORAGE_CHECKLIST_FILE" | awk -F'\t' -v id="$selected" '$1 == id { print $3 }')"
        case "$value_format" in
            country_code)
                value="$(ui_input "Storage security" "Two-letter data-centre country code, for example CH" "")" || return 1
                ;;
            snapshot_policy)
                value="$(ui_menu "Storage security" "Provider snapshot policy" \
                    "disabled" "Provider snapshots are disabled" \
                    "used" "Provider snapshots are used and separately controlled")" || return 1
                ;;
            disk_encryption)
                value="$(ui_menu "Storage security" "Workstation disk encryption" \
                    "bitlocker" "BitLocker" "filevault" "FileVault" \
                    "luks" "LUKS" "equivalent" "Equivalent reviewed control")" || return 1
                ;;
            cloud_sync)
                value="$(ui_menu "Storage security" "Workstation cloud-sync policy" \
                    "disabled" "Sensitive locations are not synchronised" \
                    "approved-processor" "Synchronisation provider is approved")" || return 1
                ;;
            none) ;;
            *) ui_error "The checklist value format is invalid."; return 1 ;;
        esac
        [ "$status" != "not_applicable" ] || value="-"
        reference="$(ui_input "Storage security" \
            "Non-secret evidence reference using letters, numbers, dot, slash, colon, underscore or hyphen" "")" || return 1
    fi
    if ! python3 "$MP_ROOT/deploy/security/storage_security_checklist.py" set \
        --file "$MP_STORAGE_CHECKLIST_FILE" \
        --control "$selected" \
        --status "$status" \
        --value "$value" \
        --evidence-reference "$reference"; then
        ui_error "The checklist update was rejected. No checklist change was published."
        return 1
    fi
    mp_audit "storage-checklist.update" "success" "${selected}:${status}"
    ui_message "Storage security" "The protected local checklist was updated. Use View to see remaining blockers."
}

# Show database size and key table counts without exposing row contents.
mp_database_status() {
    mp_compose_init
    mp_show_report "Database status" "${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d masterplan -P pager=off -c \
        "SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size; SELECT relname AS table_name, n_live_tup AS estimated_rows FROM pg_stat_user_tables ORDER BY relname;"
}

# Prune only unused Docker build cache after exact confirmation.
mp_prune_build_cache() {
    ui_require_phrase "Prune build cache" \
        "Unused Docker build cache will be removed. Running images, containers and volumes are preserved." \
        "PRUNE BUILD CACHE" || return 0
    ui_run_command "Prune build cache" "Removing unused Docker build cache" \
        docker builder prune -f \
        || { ui_error "Docker build-cache pruning failed."; return 1; }
    mp_audit "maintenance.prune-build-cache" "success" "unused-cache"
    ui_message "Docker maintenance" "Unused build cache was removed."
}
