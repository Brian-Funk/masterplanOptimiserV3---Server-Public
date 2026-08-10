#!/usr/bin/env bash

# Provider-neutral, symmetric two-node availability management.

mp_ha_caddy_compose() {
    MP_HA_CADDY_COMPOSE=(
        docker compose
        --env-file "$MP_ROOT/.env"
    )
    [ ! -f "$MP_ROOT/.release.env" ] || MP_HA_CADDY_COMPOSE+=(--env-file "$MP_ROOT/.release.env")
    if [ -f "$MP_ROOT/.test-deployment.env" ]; then
        [ "$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || true)" = test ] || {
            printf 'Unsigned deployment override exists on a non-test installation.\n' >&2
            return 1
        }
        MP_HA_CADDY_COMPOSE+=(--env-file "$MP_ROOT/.test-deployment.env")
    fi
    MP_HA_CADDY_COMPOSE+=(
        -f "$MP_ROOT/infra/docker-compose.yml"
        -f "$MP_ROOT/infra/docker-compose.prod.yml"
        -f "$MP_ROOT/infra/docker-compose.ha.yml"
    )
}

mp_ha_validate_container_caddy() {
    local domain caddy_image
    domain="$(mp_env_get DOMAIN)" || return 1
    mp_load_ha_config || return 1
    [ -s "$MP_HA_HOME/secrets/node_token" ] || return 1
    [ -f "$MP_ROOT/runtime/frontend-csp.caddy" ] || {
        printf 'The generated frontend CSP policy is missing. Deploy the current release first.\n' >&2
        return 1
    }
    mp_ha_caddy_compose
    "${MP_HA_CADDY_COMPOSE[@]}" config --quiet || return 1
    caddy_image="$(sed -n 's/^MP_CADDY_IMAGE=//p' "$MP_ROOT/.release.env" 2>/dev/null | head -1)"
    caddy_image="${caddy_image:-masterplan-caddy:2-hardened}"
    docker run --rm --network none \
        -e "DOMAIN=$domain" -e "HA_NODE_ID=$HA_NODE_ID" \
        -e "HA_CLUSTER_ID=$HA_CLUSTER_ID" -e "HA_WITNESS_URL=$HA_WITNESS_URL" \
        -v "$MP_ROOT/infra/Caddyfile.ha:/etc/caddy/Caddyfile:ro" \
        -v "$MP_ROOT/runtime:/etc/caddy/runtime:ro" \
        -v "$MP_HA_HOME/secrets/node_token:/run/secrets/ha_node_token:ro" \
        "$caddy_image" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile \
        >/dev/null
}

mp_ha_host_caddy_backup() {
    local backup_root backup timestamp override caddyfile active enabled
    backup_root="$MP_STATE/ha-caddy-topology"
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    backup="$backup_root/$timestamp"
    override="$MP_ROOT/infra/docker-compose.override.yml"
    caddyfile="$MP_HOST_CADDYFILE"
    mkdir -p "$backup_root"
    chmod 700 "$backup_root"
    [ ! -e "$backup" ] || return 1
    mkdir -m 0700 "$backup"
    cp -a "$override" "$backup/docker-compose.override.yml"
    cp -a "$caddyfile" "$backup/Caddyfile"
    active="$(systemctl is-active caddy 2>/dev/null || true)"
    enabled="$(systemctl is-enabled caddy 2>/dev/null || true)"
    printf '%s\n' "$active" > "$backup/host-caddy-active"
    printf '%s\n' "$enabled" > "$backup/host-caddy-enabled"
    chmod 600 "$backup/"*
    (
        cd "$backup"
        sha256sum Caddyfile docker-compose.override.yml host-caddy-active host-caddy-enabled \
            > receipt.sha256
        sha256sum -c receipt.sha256 >/dev/null
    ) || return 1
    printf '%s\n' "$backup"
}

mp_ha_restore_host_caddy_backup() {
    local backup="$1" override active enabled domain
    override="$MP_ROOT/infra/docker-compose.override.yml"
    [ -d "$backup" ] && [ ! -L "$backup" ] || return 1
    (
        cd "$backup"
        sha256sum -c receipt.sha256 >/dev/null
    ) || return 1
    mp_ha_caddy_compose
    "${MP_HA_CADDY_COMPOSE[@]}" stop caddy >/dev/null 2>&1 || true
    install -m 0600 "$backup/docker-compose.override.yml" "$override" || return 1
    sudo -n install -o root -g root -m 0644 "$backup/Caddyfile" "$MP_HOST_CADDYFILE" || return 1
    sudo -n caddy validate --config "$MP_HOST_CADDYFILE" --adapter caddyfile >/dev/null || return 1
    active="$(cat "$backup/host-caddy-active")"
    enabled="$(cat "$backup/host-caddy-enabled")"
    if [ "$enabled" = enabled ]; then
        sudo -n systemctl enable caddy >/dev/null || return 1
    else
        sudo -n systemctl disable caddy >/dev/null 2>&1 || true
    fi
    if [ "$active" = active ]; then
        sudo -n systemctl restart caddy || return 1
        domain="$(mp_env_get DOMAIN)" || return 1
        for _ in $(seq 1 20); do
            if curl -fsS --max-time 5 --resolve "$domain:443:127.0.0.1" \
                "https://$domain/health" >/dev/null 2>&1; then
                return 0
            fi
            sleep 1
        done
        return 1
    else
        sudo -n systemctl stop caddy >/dev/null 2>&1 || true
    fi
}

# Convert the legacy host-Caddy topology immediately before direct HA routing.
# The backend is deliberately not recreated here, so a failed conversion can
# return to the still-running standalone application without restoring data.
mp_ha_convert_host_caddy() {
    local override backup domain status active_file
    override="$MP_ROOT/infra/docker-compose.override.yml"
    active_file="$MP_STATE/ha-caddy-topology/active-backup"
    mp_load_ha_config || return 1
    [ "$HA_ROLE" = dynamic ] || { ui_error "Configure this HA node before converting Caddy."; return 1; }
    [ ! -f "$MP_ROOT/runtime/ha-control.json" ] \
        || ! jq -e '.routing_ready == true' "$MP_ROOT/runtime/ha-control.json" >/dev/null 2>&1 \
        || { ui_error "This cluster is already routing-ready. Pre-bootstrap Caddy conversion is no longer available."; return 1; }
    [ -f "$override" ] && [ ! -L "$override" ] \
        || { ui_message "Caddy topology" "No local Compose override is present. The container topology needs no conversion."; return 0; }
    [ -f "$MP_HOST_CADDYFILE" ] && [ ! -L "$MP_HOST_CADDYFILE" ] \
        || { ui_error "The host Caddyfile is missing or is a symbolic link."; return 1; }
    systemctl is-active --quiet caddy \
        || { ui_error "Host Caddy is not active; conversion was stopped."; return 1; }
    grep -Eq '127\.0\.0\.1:8000:8000' "$override" \
        && grep -Eq '^[[:space:]]*-[[:space:]]*disabled[[:space:]]*$' "$override" \
        || { ui_error "The local override is not the recognised host-Caddy topology."; return 1; }
    mp_ha_validate_container_caddy \
        || { ui_error "The HA container Caddy configuration failed its isolated validation."; return 1; }
    ui_require_phrase "Convert production Caddy topology" \
        "This pre-bootstrap action will stop host Caddy, preserve it with SHA-256 receipts, and start container Caddy without recreating the backend." \
        "CONVERT CADDY TO HA" || return 0
    mp_lock || return 1
    backup="$(mp_ha_host_caddy_backup)" || { mp_unlock; ui_error "The host Caddy backup could not be verified."; return 1; }
    mkdir -p "$(dirname "$active_file")"
    chmod 700 "$(dirname "$active_file")"
    printf '%s\n' "$backup" > "$active_file"
    chmod 600 "$active_file"
    sudo -n systemctl disable --now caddy >/dev/null || {
        rm -f "$active_file"
        mp_unlock
        ui_error "Host Caddy could not be stopped. Nothing else was changed."
        return 1
    }
    if ! mv "$override" "$backup/docker-compose.override.yml.installed"; then
        mp_ha_restore_host_caddy_backup "$backup" || true
        rm -f "$active_file"
        mp_unlock
        mp_audit "ha.caddy-convert" "failed" "override-move"
        ui_error "The local Compose override could not be staged. The verified host topology was restored."
        return 1
    fi
    mp_ha_caddy_compose
    status=0
    "${MP_HA_CADDY_COMPOSE[@]}" up -d --no-deps caddy >/dev/null || status=$?
    domain="$(mp_env_get DOMAIN)"
    if [ "$status" -eq 0 ]; then
        for _ in $(seq 1 20); do
            if curl -fsS --max-time 5 \
                --resolve "$domain:443:127.0.0.1" "https://$domain/health" >/dev/null 2>&1; then
                break
            fi
            sleep 1
        done
        curl -fsS --max-time 5 \
            --resolve "$domain:443:127.0.0.1" "https://$domain/health" >/dev/null 2>&1 \
            || status=1
    fi
    if [ "$status" -ne 0 ]; then
        mp_ha_restore_host_caddy_backup "$backup" || true
        rm -f "$active_file"
        mp_unlock
        mp_audit "ha.caddy-convert" "failed" "automatic-rollback"
        ui_error "Container Caddy did not become healthy. The verified host topology was restored."
        return 1
    fi
    mp_unlock
    mp_audit "ha.caddy-convert" "success" "$(basename "$backup")"
    ui_message "Caddy topology converted" \
        "Container Caddy serves the unchanged backend with a public certificate obtained through the scoped witness challenge.\n\nVerified rollback backup:\n$backup"
}

mp_ha_rollback_host_caddy() {
    local active_file backup
    active_file="$MP_STATE/ha-caddy-topology/active-backup"
    [ -f "$active_file" ] && [ ! -L "$active_file" ] \
        || { ui_error "No active pre-bootstrap Caddy conversion can be rolled back."; return 1; }
    [ ! -f "$MP_ROOT/runtime/ha-control.json" ] \
        || ! jq -e '.routing_ready == true' "$MP_ROOT/runtime/ha-control.json" >/dev/null 2>&1 \
        || { ui_error "The cluster is routing-ready. Use the full verified recovery procedure instead of topology-only rollback."; return 1; }
    backup="$(readlink -f "$(cat "$active_file")" 2>/dev/null || true)"
    [[ "$backup" == "$(readlink -f "$MP_STATE/ha-caddy-topology")/"* ]] \
        || { ui_error "The rollback receipt path is invalid."; return 1; }
    ui_require_phrase "Restore host Caddy topology" \
        "This is available only before witness bootstrap. Container Caddy will stop and the exact verified host configuration will return." \
        "RESTORE HOST CADDY" || return 0
    mp_lock || return 1
    if ! mp_ha_restore_host_caddy_backup "$backup"; then
        mp_unlock
        mp_audit "ha.caddy-rollback" "failed" "$(basename "$backup")"
        ui_error "Host Caddy restoration failed. Inspect both Caddy services before continuing."
        return 1
    fi
    rm -f "$active_file"
    mp_unlock
    mp_audit "ha.caddy-rollback" "success" "$(basename "$backup")"
    ui_message "Host Caddy restored" "The verified pre-conversion topology is active again. The standalone backend was never recreated."
}

mp_ha_install_config() {
    local source="$1"
    sudo -n install -d -o "$USER" -g "$(id -gn)" -m 0700 "$MP_HA_HOME" || return 1
    sudo -n install -o "$USER" -g "$(id -gn)" -m 0600 "$source" "$MP_HA_CONFIG" || return 1
    mp_load_ha_config
}

mp_ha_set_config_value() {
    local key="$1" value="$2" staging
    [ -f "$MP_HA_CONFIG" ] || return 1
    staging="$(mktemp "${TMPDIR:-/tmp}/mp-opt-ha.XXXXXX")" || return 1
    cp -a "$MP_HA_CONFIG" "$staging" || { rm -f "$staging"; return 1; }
    mp_env_set "$key" "$value" "$staging" || { rm -f "$staging"; return 1; }
    mp_ha_install_config "$staging"
    rm -f "$staging"
}

mp_configure_ha() {
    local mode node_id cluster_id peer_id peer_ssh witness peer_recipient staging token identity recipient
    mode="$(ui_menu "Availability mode" "Choose how this server participates" \
        "standalone" "Standalone server" \
        "ha" "Symmetric two-node HA cluster")" || return 1
    [ "$mode" = "ha" ] || { ui_message "Availability mode" "Standalone mode remains unchanged."; return 0; }
    [ ! -e "$MP_HA_CONFIG" ] || { ui_error "This node already has an HA identity. Reconfiguration in place is blocked."; return 1; }
    mp_require_commands age age-keygen jq openssl ssh || return 1
    node_id="$(ui_input "HA node" "Unique node id" "$(hostname -s)")" || return 1
    peer_id="$(ui_input "HA peer" "Unique peer node id")" || return 1
    cluster_id="$(ui_input "HA cluster" "Shared cluster id" "$(cat /proc/sys/kernel/random/uuid)")" || return 1
    peer_ssh="$(ui_input "HA peer" "Peer SSH host or alias")" || return 1
    witness="$(ui_input "Lease authority" "Cloudflare Worker HTTPS URL")" || return 1
    peer_recipient="$(ui_input "Replication encryption" "Peer age recipient (may be left blank until both nodes are configured)")" || return 1
    [[ "$node_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] \
        && [[ "$peer_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] \
        && [ "$node_id" != "$peer_id" ] || { ui_error "Node ids are invalid or identical."; return 1; }
    [[ "$cluster_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$ ]] \
        || { ui_error "The cluster id is invalid."; return 1; }
    [[ "$witness" =~ ^https://[^[:space:]]+$ ]] \
        || { ui_error "The lease authority must be HTTPS."; return 1; }
    [ -z "$peer_recipient" ] || [[ "$peer_recipient" =~ ^age1[0-9a-z]{58}$ ]] \
        || { ui_error "The peer age recipient is invalid."; return 1; }
    [[ "$peer_ssh" =~ ^([A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+$ ]] \
        || { ui_error "The peer SSH destination is invalid."; return 1; }

    sudo -n install -d -o "$USER" -g "$(id -gn)" -m 0700 "$MP_HA_HOME/secrets" || return 1
    identity="$MP_HA_HOME/secrets/replication_age_identity"
    age-keygen -o "$identity" >/dev/null 2>&1 || return 1
    chmod 600 "$identity"
    recipient="$(age-keygen -y "$identity")" || return 1
    printf '%s\n' "$peer_recipient" > "$MP_HA_HOME/peer-age-recipient"
    chmod 600 "$MP_HA_HOME/peer-age-recipient"
    token="$(openssl rand -hex 32)" || return 1
    printf '%s' "$token" > "$MP_HA_HOME/secrets/node_token"
    chmod 600 "$MP_HA_HOME/secrets/node_token"
    staging="$(mktemp "${TMPDIR:-/tmp}/mp-opt-ha.XXXXXX")" || return 1
    chmod 600 "$staging"
    {
        printf 'HA_MODE=ha\nHA_ROLE=dynamic\nHA_NODE_ID=%s\n' "$node_id"
        printf 'HA_CLUSTER_ID=%s\nHA_GENERATION=1\n' "$cluster_id"
        printf 'HA_PEER_NODE_ID=%s\nHA_PEER_SSH=%s\n' "$peer_id" "$peer_ssh"
        printf 'HA_WITNESS_URL=%s\nHA_HEARTBEAT_INTERVAL_SECONDS=15\n' "$witness"
        printf 'HA_AUTOMATIC_FAILOVER=disabled\nHA_RECOVERY_STORAGE_MODE=manual_portable\nHA_ARCHIVE_SSH_TARGET=\nHA_ALERT_EMAIL=\n'
    } > "$staging"
    mp_ha_install_config "$staging" || { rm -f "$staging"; return 1; }
    rm -f "$staging"
    "$MP_ROOT/deploy/ha/install_services.sh" || return 1
    mp_audit "ha.configure" "success" "dynamic:$node_id"
    ui_message "Symmetric HA configured" \
        "Node: ${node_id}\nCluster: ${cluster_id}\nPeer: ${peer_id}\n\nThis node age recipient:\n${recipient}\n\nThis node witness token (shown once):\n${token}\n\nStore both nodes' values securely for the one-time witness bootstrap."
    unset token
}

mp_ha_configure_peer_recipient() {
    local recipient temporary
    recipient="$(ui_input "Replication encryption" "Peer age recipient (age1...)" "$(cat "$MP_HA_HOME/peer-age-recipient" 2>/dev/null || true)")" || return 1
    [[ "$recipient" =~ ^age1[0-9a-z]{58}$ ]] || { ui_error "The peer age recipient is invalid."; return 1; }
    temporary="$(mktemp "${TMPDIR:-/tmp}/peer-age.XXXXXX")" || return 1
    printf '%s\n' "$recipient" > "$temporary"
    install -m 0600 "$temporary" "$MP_HA_HOME/peer-age-recipient"
    rm -f "$temporary"
    mp_audit "ha.peer-recipient" "success" "updated"
    ui_message "Replication encryption" "The peer recipient was installed. No private key was transferred."
}

mp_ha_configure_archive_target() {
    local mode target role temporary
    role="$(mp_ha_role)" || return 1
    mode="$(ui_menu "Recovery storage" "Choose how encrypted disaster-recovery snapshots leave this VPS" \
        "manual_portable" "Manual workstation export (no additional server)" \
        "ssh_archive" "Automatic passwordless SSH archive")" || return 1
    if [ "$mode" = manual_portable ]; then
        if [ "$role" = dynamic ]; then
            mp_ha_set_config_value HA_RECOVERY_STORAGE_MODE manual_portable || return 1
            mp_ha_set_config_value HA_ARCHIVE_SSH_TARGET "" || return 1
            rm -f "$MP_ARCHIVE_TARGET_FILE"
        else
            rm -f "$MP_ARCHIVE_TARGET_FILE"
            temporary="$(mktemp "${MP_HOME}/recovery-storage-mode.XXXXXX")" || return 1
            printf '%s\n' manual_portable > "$temporary"
            chmod 600 "$temporary"
            mv "$temporary" "$MP_RECOVERY_STORAGE_MODE_FILE"
        fi
        mp_audit "ha.recovery-storage" "success" "manual_portable"
        ui_message "Recovery storage" \
            "Automatic peer replication continues normally. Independent disaster-recovery copies must be exported manually to a workstation and confirmed by SHA-256. No recurring export timer is enabled."
        return 0
    fi
    target="$(ui_input "SSH recovery archive" "SSH destination as user@host" "${HA_ARCHIVE_SSH_TARGET:-}")" || return 1
    [[ "$target" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$ ]] \
        || { ui_error "Use an SSH destination such as deploy@backup.example.net."; return 1; }
    ssh -o BatchMode=yes -o ConnectTimeout=10 "$target" true \
        || { ui_error "Passwordless SSH verification failed. Nothing was changed."; return 1; }
    if [ "$role" = dynamic ]; then
        mp_ha_set_config_value HA_ARCHIVE_SSH_TARGET "$target" || return 1
        mp_ha_set_config_value HA_RECOVERY_STORAGE_MODE ssh_archive || return 1
        rm -f "$MP_ARCHIVE_TARGET_FILE"
    else
        temporary="$(mktemp "${MP_HOME}/archive-ssh-target.XXXXXX")" || return 1
        printf '%s\n' "$target" > "$temporary"
        chmod 600 "$temporary"
        mv "$temporary" "$MP_ARCHIVE_TARGET_FILE"
        temporary="$(mktemp "${MP_HOME}/recovery-storage-mode.XXXXXX")" || return 1
        printf '%s\n' ssh_archive > "$temporary"
        chmod 600 "$temporary"
        mv "$temporary" "$MP_RECOVERY_STORAGE_MODE_FILE"
    fi
    mp_audit "ha.recovery-storage" "success" "ssh_archive:ssh-verified"
    ui_message "Recovery storage" "Encrypted snapshots will be copied automatically to the verified SSH destination and compared by SHA-256."
}

mp_ha_configure_alert_recipient() {
    local recipient
    recipient="$(ui_input "HA alerts" "Operational alert email; leave blank to disable" "${HA_ALERT_EMAIL:-}")" || return 1
    [ -z "$recipient" ] || mp_validate_email_address "$recipient" \
        || { ui_error "Enter a valid alert email address."; return 1; }
    mp_ha_set_config_value HA_ALERT_EMAIL "$recipient" || return 1
    mp_audit "ha.alert-recipient" "success" "$([ -n "$recipient" ] && printf configured || printf disabled)"
    ui_message "HA alerts" "The alert recipient was updated. SMTP must also be configured on this node."
}

mp_ha_send_alert() {
    local subject="$1" message="$2"
    python3 "$MP_ROOT/deploy/ha/send_alert.py" "$subject" "$message" \
        && mp_audit "ha.alert" "success" "smtp-accepted"
}

mp_ha_replicate_now() {
    mp_load_ha_config || return 1
    [ "$HA_ROLE" = "dynamic" ] || { ui_error "Symmetric HA is not configured."; return 1; }
    ui_run_command "Replicate now" "Capturing, encrypting and verifying the complete application state" \
        "$MP_ROOT/deploy/ha/replicate_now.sh" || return 1
    ui_message "Replication complete" "The peer accepted the complete hash-verified application state."
}

# Authenticate to the configured SMTP provider from both HA origins. Reports
# contain only a digest of the effective configuration; credentials are never
# copied into the terminal, audit log, or peer command line.
mp_ha_verify_smtp_both_nodes() {
    local local_report peer_report local_ready peer_ready local_fingerprint peer_fingerprint
    local recipient send_message local_node peer_node require_delivery="${1:-optional}" delivery_sent=false
    mp_load_ha_config || return 1
    [ "$HA_ROLE" = "dynamic" ] || { ui_error "Symmetric HA is not configured."; return 1; }
    [[ "$HA_NODE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] \
        && [[ "$HA_PEER_NODE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] \
        || { ui_error "The configured HA node identities are invalid."; return 1; }
    local_report="$(mktemp "${TMPDIR:-/tmp}/mp-opt-smtp-local.XXXXXX")" || return 1
    peer_report="$(mktemp "${TMPDIR:-/tmp}/mp-opt-smtp-peer.XXXXXX")" || { rm -f "$local_report"; return 1; }
    chmod 600 "$local_report" "$peer_report"

    python3 "$MP_ROOT/deploy/ha/smtp_probe.py" \
        --root "$MP_ROOT" --node-id "$HA_NODE_ID" \
        --output "$MP_ROOT/runtime/ha-smtp-status.json" > "$local_report" 2>/dev/null || true
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
        -o ClearAllForwardings=yes "$HA_PEER_SSH" \
        python3 /opt/masterplan/deploy/ha/smtp_probe.py \
        --root /opt/masterplan --node-id "$HA_PEER_NODE_ID" \
        --output /opt/masterplan/runtime/ha-smtp-status.json > "$peer_report" 2>/dev/null || true

    if ! jq -e 'type == "object" and has("ready") and has("error_code")' "$local_report" >/dev/null 2>&1; then
        printf '{"node_id":"%s","ready":false,"error_code":"probe_unavailable"}\n' "$HA_NODE_ID" > "$local_report"
    fi
    if ! jq -e 'type == "object" and has("ready") and has("error_code")' "$peer_report" >/dev/null 2>&1; then
        printf '{"node_id":"%s","ready":false,"error_code":"peer_unreachable"}\n' "$HA_PEER_NODE_ID" > "$peer_report"
    fi
    local_ready="$(jq -r '.ready // false' "$local_report")"
    peer_ready="$(jq -r '.ready // false' "$peer_report")"
    local_fingerprint="$(jq -r '.config_fingerprint // ""' "$local_report")"
    peer_fingerprint="$(jq -r '.config_fingerprint // ""' "$peer_report")"
    local_node="$(jq -r '.node_id // "local"' "$local_report")"
    peer_node="$(jq -r '.node_id // "peer"' "$peer_report")"

    send_message="No test message was sent."
    if [ "$local_ready" = true ] && [ "$peer_ready" = true ] \
        && [ -n "$local_fingerprint" ] && [ "$local_fingerprint" = "$peer_fingerprint" ] \
        && { [ "$require_delivery" = required ] \
            || ui_confirm "SMTP delivery" "Both origins authenticate with the same protected SMTP configuration. Send one token-free delivery test from each origin?"; }; then
        recipient="$(ui_input "SMTP delivery" "Test recipient email")" || recipient=""
        if [ -n "$recipient" ]; then
            mp_validate_email_address "$recipient" || {
                rm -f "$local_report" "$peer_report"
                ui_error "Enter a valid recipient email."
                return 1
            }
            python3 "$MP_ROOT/deploy/ha/smtp_probe.py" \
                --root "$MP_ROOT" --node-id "$HA_NODE_ID" \
                --output "$MP_ROOT/runtime/ha-smtp-status.json" --send-to "$recipient" >/dev/null \
                || local_ready=false
            ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
                -o ClearAllForwardings=yes "$HA_PEER_SSH" \
                python3 /opt/masterplan/deploy/ha/smtp_probe.py \
                --root /opt/masterplan --node-id "$HA_PEER_NODE_ID" \
                --output /opt/masterplan/runtime/ha-smtp-status.json --send-to "$recipient" >/dev/null \
                || peer_ready=false
            if [ "$local_ready" = true ] && [ "$peer_ready" = true ]; then
                send_message="Both origins sent and the provider accepted a token-free test message."
                delivery_sent=true
            else
                send_message="At least one origin could not send the delivery test."
            fi
        fi
    fi

    mp_audit "ha.smtp-verification" \
        "$([ "$local_ready" = true ] && [ "$peer_ready" = true ] && [ -n "$local_fingerprint" ] && [ "$local_fingerprint" = "$peer_fingerprint" ] && printf success || printf failed)" \
        "local:${local_ready}:peer:${peer_ready}:configuration-match:$([ -n "$local_fingerprint" ] && [ "$local_fingerprint" = "$peer_fingerprint" ] && printf yes || printf no)"
    ui_message "HA SMTP verification" \
        "${local_node}: $([ "$local_ready" = true ] && printf authenticated || printf '%s' "$(jq -r '.error_code // "probe_failed"' "$local_report")")\n${peer_node}: $([ "$peer_ready" = true ] && printf authenticated || printf '%s' "$(jq -r '.error_code // "probe_failed"' "$peer_report")")\nConfiguration match: $([ -n "$local_fingerprint" ] && [ "$local_fingerprint" = "$peer_fingerprint" ] && printf yes || printf no)\n\n${send_message}"
    rm -f "$local_report" "$peer_report"
    [ "$local_ready" = true ] && [ "$peer_ready" = true ] \
        && [ -n "$local_fingerprint" ] && [ "$local_fingerprint" = "$peer_fingerprint" ] \
        && { [ "$require_delivery" != required ] || [ "$delivery_sent" = true ]; }
}

# Run one narrowly scoped public-recipient operation on the HA peer. The
# private recovery identity is never accepted by this path.
mp_ha_peer_recovery_recipient() {
    local operation="$1" recipient="${2:-}" expected="${3:-}"
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
        -o ClearAllForwardings=yes "$HA_PEER_SSH" \
        bash -s -- "$operation" "$recipient" "$expected" <<'REMOTE'
set -Eeuo pipefail
umask 077
operation="$1"
recipient="${2:-}"
expected="${3:-}"
directory="$HOME/.config/mp-opt-server"
target="$directory/recovery-recipient"
pending="$directory/recovery-recipient.pending"
valid() { [[ "$1" =~ ^age1[0-9a-z]+$ ]]; }
digest() { printf '%s' "$1" | sha256sum | awk '{print $1}'; }
case "$operation" in
    read)
        [ ! -L "$target" ] || exit 1
        [ ! -s "$target" ] || { value="$(tr -d '\r\n' < "$target")"; valid "$value"; printf '%s\n' "$value"; }
        ;;
    stage)
        valid "$recipient"
        [ "$(digest "$recipient")" = "$expected" ]
        install -d -m 0700 "$directory"
        [ ! -L "$target" ] && [ ! -L "$pending" ]
        printf '%s\n' "$recipient" > "$pending"
        chmod 600 "$pending"
        [ "$(digest "$(tr -d '\r\n' < "$pending")")" = "$expected" ]
        ;;
    activate)
        [ -f "$pending" ] && [ ! -L "$pending" ]
        value="$(tr -d '\r\n' < "$pending")"
        valid "$value"
        [ "$(digest "$value")" = "$expected" ]
        mv -f "$pending" "$target"
        chmod 600 "$target"
        ;;
    restore)
        install -d -m 0700 "$directory"
        rm -f "$pending"
        if [ -n "$recipient" ]; then
            valid "$recipient"
            temporary="$(mktemp "$directory/recovery-recipient.restore.XXXXXX")"
            printf '%s\n' "$recipient" > "$temporary"
            chmod 600 "$temporary"
            mv -f "$temporary" "$target"
        else
            rm -f "$target"
        fi
        ;;
    hash)
        [ -f "$target" ] && [ ! -L "$target" ]
        value="$(tr -d '\r\n' < "$target")"
        valid "$value"
        digest "$value"
        ;;
    *) exit 1 ;;
esac
REMOTE
}

# Install one public snapshot recipient as a two-node transaction. The active
# lease holder is the only node allowed to initiate the change. A failed peer
# operation restores the previous local and peer values whenever reachable.
mp_ha_sync_recovery_recipient() {
    local recipient="$1" expected local_previous peer_previous local_hash peer_hash
    mp_load_ha_config || return 1
    [ "$HA_ROLE" = dynamic ] || return 1
    mp_require_active_or_standalone || return 1
    [[ "$recipient" =~ ^age1[0-9a-z]+$ ]] || return 1
    [ -n "${HA_PEER_SSH:-}" ] || return 1
    expected="$(mp_recovery_recipient_fingerprint "$recipient")" || return 1
    local_previous="$(mp_recovery_recipient 2>/dev/null || true)"
    peer_previous="$(mp_ha_peer_recovery_recipient read 2>/dev/null)" || {
        printf 'The HA peer could not be reached or its existing recovery recipient is invalid.\n' >&2
        return 1
    }
    [ -z "$peer_previous" ] || [[ "$peer_previous" =~ ^age1[0-9a-z]+$ ]] || return 1
    mp_ha_peer_recovery_recipient stage "$recipient" "$expected" >/dev/null || return 1
    if ! mp_store_recovery_recipient_local "$recipient"; then
        mp_ha_peer_recovery_recipient restore "$peer_previous" >/dev/null 2>&1 || true
        return 1
    fi
    # SSH joins command arguments into a remote command string and does not
    # preserve an empty middle argument reliably.  Pass the already validated
    # public recipient here even though the activate operation only needs its
    # expected digest, so the digest remains remote positional parameter $3.
    if ! mp_ha_peer_recovery_recipient activate "$recipient" "$expected" >/dev/null; then
        if [ -n "$local_previous" ]; then
            mp_store_recovery_recipient_local "$local_previous" || true
        else
            rm -f "$MP_RECIPIENT_FILE"
        fi
        mp_ha_peer_recovery_recipient restore "$peer_previous" >/dev/null 2>&1 || true
        return 1
    fi
    local_hash="$(mp_recovery_recipient_fingerprint 2>/dev/null || true)"
    peer_hash="$(mp_ha_peer_recovery_recipient hash 2>/dev/null || true)"
    if [ "$local_hash" != "$expected" ] || [ "$peer_hash" != "$expected" ]; then
        if [ -n "$local_previous" ]; then
            mp_store_recovery_recipient_local "$local_previous" || true
        else
            rm -f "$MP_RECIPIENT_FILE"
        fi
        mp_ha_peer_recovery_recipient restore "$peer_previous" >/dev/null 2>&1 || true
        return 1
    fi
    mp_audit "ha.recovery-recipient-sync" "success" "sha256:$expected"
}

mp_ha_overview() {
    local report local_recipient local_hash peer_recipient peer_hash recovery_status peer_reachable storage_mode
    report="$(mktemp "${MP_STATE}/ha-overview.XXXXXX")" || return 1
    mp_load_ha_config || { rm -f "$report"; return 1; }
    local_recipient="$(mp_recovery_recipient 2>/dev/null || true)"
    local_hash="$(mp_recovery_recipient_fingerprint "$local_recipient" 2>/dev/null || true)"
    if peer_recipient="$(mp_ha_peer_recovery_recipient read 2>/dev/null)"; then
        peer_reachable=true
    else
        peer_reachable=false
        peer_recipient=""
    fi
    peer_hash="$(mp_recovery_recipient_fingerprint "$peer_recipient" 2>/dev/null || true)"
    storage_mode="${HA_RECOVERY_STORAGE_MODE:-manual_portable}"
    if [ "$peer_reachable" != true ]; then
        recovery_status="PEER UNREACHABLE - consistency could not be proved"
    elif [ -n "$local_hash" ] && [ "$local_hash" = "$peer_hash" ]; then
        recovery_status="MATCH"
    elif [ -z "$local_hash" ] || [ -z "$peer_hash" ]; then
        recovery_status="MISSING - configure from the active node"
    else
        recovery_status="MISMATCH - snapshots are not consistently recoverable"
    fi
    {
        printf 'MP-OPT_SERVER symmetric high availability\n\n'
        printf 'Node: %s\nPeer: %s\nCluster: %s\n' "$HA_NODE_ID" "${HA_PEER_NODE_ID:-not-configured}" "${HA_CLUSTER_ID:-not-configured}"
        printf '\nSnapshot recovery public recipient\n'
        printf 'Status: %s\nLocal SHA-256: %s\nPeer SHA-256: %s\n' \
            "$recovery_status" "${local_hash:-not-configured}" "${peer_hash:-not-configured}"
        printf 'Private identity on VPS: never required and must not be stored here\n'
        printf '\nDisaster-recovery storage\nMode: %s\n' "$storage_mode"
        if [ "$storage_mode" = manual_portable ]; then
            printf 'Schedule: manual - no recurring export timer\n'
            if [ -f "$MP_MANUAL_EXPORT_STATE" ]; then
                jq -r 'if .state == "operator-sha256-confirmed" then
                    "Last confirmed workstation copy: " + .confirmed_at
                    + "\nSnapshot: " + .snapshot
                    + "\nRecovery key: " + .recovery_key_id
                    + "\nPackage SHA-256: " + .package_sha256
                  else
                    "ACTION REQUIRED: fresh workstation export\nReason: " + (.reason // "operator-action")
                    + "\nRequired since: " + (.required_at // "unknown")
                  end' "$MP_MANUAL_EXPORT_STATE" 2>/dev/null \
                    || printf 'Manual export state: INVALID\n'
            else
                printf 'Last confirmed workstation copy: none recorded\n'
            fi
        else
            printf 'SSH archive target: configured and redacted\n'
        fi
        printf 'Automatic failover: %s\n\nLease authority\n' \
            "$(jq -r 'if .automatic_failover == true then "enabled" else "disabled" end' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null || printf unknown)"
        jq . "$MP_ROOT/runtime/ha-control.json" 2>/dev/null || printf 'No lease observation is available.\n'
        printf '\nReplication\n'
        jq . "$MP_ROOT/runtime/ha-replication.json" 2>/dev/null || printf 'No replication attempt is available.\n'
        printf '\nLast received copy\n'
        jq . "$MP_ROOT/runtime/ha-receiver.json" 2>/dev/null || printf 'No complete copy has been accepted.\n'
    } > "$report"
    ui_text_file "High availability" "$report"
    rm -f "$report"
}

mp_ha_selftest_root() {
    local test_root="$MP_ROOT" expected_commit="" receipt_commit="" source_commit="" source_dirty=""
    if [ "$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)" = test ]; then
        test_root="$HOME/.local/share/mp-opt-test-deploy/source"
        expected_commit="$(sed -n 's/^MP_TEST_COMMIT=//p' "$MP_ROOT/.test-deployment.env" 2>/dev/null | head -1)"
        receipt_commit="$(jq -r '.current_commit // empty' \
            "$MP_STATE/test-deployments/current.json" 2>/dev/null || true)"
        source_commit="$(git -C "$test_root" rev-parse HEAD 2>/dev/null || true)"
        source_dirty="$(git -C "$test_root" status --porcelain --untracked-files=all 2>/dev/null || printf unreadable)"
        [[ "$expected_commit" =~ ^[0-9a-f]{40}$ ]] \
            && [ "$receipt_commit" = "$expected_commit" ] \
            && [ "$source_commit" = "$expected_commit" ] \
            && [ -z "$source_dirty" ] \
            || {
                ui_error "The exact unsigned self-test source does not match the active deployment receipt. Reapply the qualified exact SHA before running HA self-tests."
                return 1
            }
    fi
    [ -d "$test_root/deploy/ha/tests" ] \
        || { ui_error "The isolated HA self-test source is unavailable."; return 1; }
    printf '%s\n' "$test_root"
}

mp_ha_run_selftests() {
    local test_root
    test_root="$(mp_ha_selftest_root)" || return 1
    ui_run_command "Replication self-tests" "Checking manifest, encryption boundaries and write fencing" \
        env MP_HA_SELFTEST_ROOT="$test_root" bash -c \
        'cd "$MP_HA_SELFTEST_ROOT" && exec python3 -m unittest discover -s "$MP_HA_SELFTEST_ROOT/deploy/ha/tests" -p "test_*.py"' \
        || return 1
}

mp_ha_refresh_witness_observations() {
    mp_load_ha_config || return 1
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 "$HA_PEER_SSH" \
        env MP_ROOT=/opt/masterplan MP_HA_HOME=/etc/mp-opt-ha \
        python3 /opt/masterplan/deploy/ha/lease_agent.py --once >/dev/null || return 1
    MP_ROOT="$MP_ROOT" MP_HA_HOME="$MP_HA_HOME" \
        python3 "$MP_ROOT/deploy/ha/lease_agent.py" --once >/dev/null || return 1
}

# Prove the concrete gates the witness also enforces before automatic failover
# is enabled. This is intentionally non-destructive: it validates the current
# copy, releases, recovery recipient, SMTP parity and public service only.
mp_ha_active_verification_readiness() {
    local control local_hash peer_hash domain
    mp_load_ha_config || return 1
    [ "$HA_ROLE" = dynamic ] || { ui_error "This installation is not an HA pair."; return 1; }
    [ -s "$MP_ROOT/runtime/ha-control.json" ] || { ui_error "No current witness observation is available."; return 1; }
    control="$(cat "$MP_ROOT/runtime/ha-control.json")"
    jq -e --arg holder "$HA_NODE_ID" '
      .routing_ready == true and .holder_node_id == $holder and
      ([.nodes[] | select(.healthy == true)] | length) == 2 and
      ([.nodes[].release_hash] | length == 2 and .[0] != "" and .[0] == .[1]) and
      ([.nodes[] | select(.node_id != $holder) |
          select(.bundle_generation == ($root.generation) and .bundle_id != "")] | length) == 1 and
      ([.nodes[] | select(.critical_pending == true)] | length) == 0 and
      (([.nodes[].smtp_configured] | unique | length) == 1) and
      (([.nodes[].smtp_configured] | all(. == false)) or
       (([.nodes[].smtp_ready] | all(. == true)) and
        ([.nodes[].smtp_config_fingerprint] | length == 2 and .[0] != "" and .[0] == .[1])))
    ' --argjson root "$control" <<< "$control" >/dev/null \
        || { ui_error "HA is not ready: both nodes must be healthy on the same release, the standby must hold the current verified copy, no critical copy may be pending, and SMTP must match on both nodes."; return 1; }
    local_hash="$(mp_recovery_recipient_fingerprint 2>/dev/null || true)"
    peer_hash="$(mp_ha_peer_recovery_recipient hash 2>/dev/null || true)"
    [ -n "$local_hash" ] && [ "$local_hash" = "$peer_hash" ] \
        || { ui_error "The snapshot recovery recipient is missing or differs between nodes."; return 1; }
    domain="$(mp_env_get DOMAIN)" || return 1
    curl -fsS --max-time 10 "https://${domain}/health" >/dev/null \
        || { ui_error "Public HTTPS health is not ready."; return 1; }
    ui_message "HA readiness" "Both nodes, the current verified copy, release identity, recovery encryption, SMTP parity and public health are ready for automatic failover."
}

mp_ha_automatic_failover() {
    local next current
    mp_load_ha_config || return 1
    mp_require_active_or_standalone || return 1
    current="$(jq -r 'if .automatic_failover == true then "enabled" else "disabled" end' \
        "$MP_ROOT/runtime/ha-control.json")" || return 1
    if [ "$current" = "enabled" ]; then next=disabled; else next=enabled; fi
    if [ "$next" = "enabled" ]; then
        ui_require_phrase "Enable automatic failover" \
            "Enable only after active recovery tests, both failover directions and a fresh peer-accepted replication have passed on a disposable pair. Accept that writes after the latest verified copy may be lost." \
            "ENABLE AUTOMATIC FAILOVER" || return 0
    else
        ui_confirm "Automatic failover" "Disable automatic failover now?" || return 0
    fi
    python3 "$MP_ROOT/deploy/ha/witness_control.py" automatic "$next" >/dev/null || return 1
    mp_ha_set_config_value HA_AUTOMATIC_FAILOVER "$next" || return 1
    mp_audit "ha.automatic" "success" "$next"
    ui_message "Automatic failover" "Automatic failover is ${next}."
}

mp_ha_planned_switchover() {
    local handed_off=false
    mp_load_ha_config || return 1
    mp_require_active_or_standalone || return 1
    ui_require_phrase "Planned switchover" \
        "A fresh complete copy will be accepted before ownership moves to ${HA_PEER_NODE_ID}. Existing bearer access will be invalidated; passkeys remain registered." \
        "HAND OFF TO $HA_PEER_NODE_ID" || return 0
    "$MP_ROOT/deploy/ha/replicate_now.sh" || return 1
    for _ in $(seq 1 15); do
        if python3 "$MP_ROOT/deploy/ha/witness_control.py" handoff "$HA_PEER_NODE_ID" >/dev/null 2>&1; then
            handed_off=true
            break
        fi
        sleep 1
    done
    [ "$handed_off" = true ] \
        || { ui_error "The witness did not accept the handoff. Ownership was not changed."; return 1; }
    mp_audit "ha.switchover" "success" "$HA_PEER_NODE_ID"
    ui_message "Ownership handed off" "The peer owns the next generation. Cloudflare routes traffic only after its local promotion checks pass."
}
