#!/usr/bin/env bash

# Resumable, opinionated commissioning for standalone and two-node installs.
# The state file contains checkpoints and public metadata only; credentials are
# either installed as mode-0600 secrets or kept in memory for one operation.

MP_SETUP_V2_STATE="${MP_SETUP_V2_STATE:-$MP_STATE/setup-state-v2.json}"
MP_SETUP_V2_PENDING_JOIN="${MP_SETUP_V2_PENDING_JOIN:-$MP_STATE/pending-ha-join.json}"
MP_SETUP_V2_PENDING_BOOTSTRAP="${MP_SETUP_V2_PENDING_BOOTSTRAP:-$MP_STATE/pending-witness-bootstrap.json}"
MP_SETUP_V2_PENDING_LOCAL_JOIN="${MP_SETUP_V2_PENDING_LOCAL_JOIN:-$MP_STATE/pending-local-join.json}"
MP_SETUP_V2_PENDING_REPLACEMENT="${MP_SETUP_V2_PENDING_REPLACEMENT:-$MP_STATE/pending-replacement-request.json}"
MP_SETUP_V2_IMPORT_RECEIPT="${MP_SETUP_V2_IMPORT_RECEIPT:-$MP_STATE/setup-import-receipt.json}"

mp_setup_install_signed_release() {
    local lane
    lane="$(jq -r '.deployment_lane // "signed"' "$MP_SETUP_V2_STATE" 2>/dev/null || printf signed)"
    if [ -s "$MP_ROOT/.release.env" ] \
        && grep -Eq '^MP_TOOLS_IMAGE=ghcr\.io/brian-funk/masterplanoptimiserv3---server/tools@sha256:[0-9a-f]{64}$' \
            "$MP_ROOT/.release.env"; then
        [ "$(jq -r '.state // empty' "$MP_SETUP_V2_STATE" 2>/dev/null || true)" != in_progress ] \
            || mp_setup_record_signed_baseline
        return 0
    fi
    case "$lane" in
        unsigned)
            ui_run_command "Verify rollback baseline" \
                "Verifying the newest stable release and caching immutable images without activating signed application files" \
                python3 "$MP_ROOT/deploy/release/install_release.py" \
                    --repo-root "$MP_ROOT" --baseline-only \
                || { ui_error "The signed rollback baseline could not be verified. Check internet access and resume setup; the campaign checkout and application data were not changed."; return 1; }
            ;;
        signed)
            ui_run_command "Install production release" \
                "Verifying the newest stable release and downloading immutable images" \
                python3 "$MP_ROOT/deploy/release/install_release.py" --repo-root "$MP_ROOT" \
                || { ui_error "The signed production release could not be installed. Check internet access and resume setup; no application data was changed."; return 1; }
            ;;
        *)
            ui_error "The commissioning deployment lane is invalid. No release files were changed."
            return 1
            ;;
    esac
    [ "$(jq -r '.state // empty' "$MP_SETUP_V2_STATE" 2>/dev/null || true)" != in_progress ] \
        || mp_setup_record_signed_baseline
}

mp_setup_state_begin() {
    local mode="$1" temporary lane="" policy commit="" receipt="" pinned="" checkout=""
    if [ -f "$MP_SETUP_V2_STATE" ]; then
        jq -e '.format == "mp-opt-setup-state-v2" and .state == "in_progress"' \
            "$MP_SETUP_V2_STATE" >/dev/null || {
            ui_error "The commissioning checkpoint is not a valid v2 state. A clean reset is required; unsigned setup will not guess or translate old state."
            return 1
        }
        [ "$(jq -r '.mode // empty' "$MP_SETUP_V2_STATE")" = "$mode" ] || {
            ui_error "Another commissioning workflow is already in progress. Resume it and complete its guarded checkpoints before starting a different mode."
            return 1
        }
        if [ "$(jq -r '.deployment_lane // empty' "$MP_SETUP_V2_STATE")" = unsigned ] \
            && [ -f "$MP_ROOT/.env" ]; then
            receipt="$(jq -r '.current_commit // empty' \
                "$MP_STATE/test-deployments/current.json" 2>/dev/null || true)"
            [[ "$receipt" =~ ^[0-9a-f]{40}$ ]] || {
                ui_error "The active unsigned application has no valid exact deployment receipt. Commissioning will not infer a commit from the management checkout."
                return 1
            }
            pinned="$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")"
            if [ "$pinned" != "$receipt" ]; then
                checkout="$(git -C "$MP_ROOT" rev-parse HEAD 2>/dev/null || true)"
                if [ "$pinned" != "$checkout" ] \
                    || jq -e '.completed | index("witness_bootstrap") != null' \
                        "$MP_SETUP_V2_STATE" >/dev/null 2>&1 \
                    || [ -s "$MP_SETUP_V2_PENDING_JOIN" ]; then
                    ui_error "The unsigned commissioning pin does not match the active exact deployment receipt. Pairing has stopped rather than changing an established campaign target."
                    return 1
                fi
                git -C "$MP_ROOT" fetch --no-tags --force origin "$receipt" >/dev/null 2>&1 \
                    && [ "$(git -C "$MP_ROOT" rev-parse FETCH_HEAD 2>/dev/null || true)" = "$receipt" ] \
                    || { ui_error "The active exact deployment receipt is not available from origin. Push that exact commit before resuming commissioning."; return 1; }
                mp_setup_state_update '.campaign_commit=$commit' --arg commit "$receipt" || return 1
            fi
        fi
        return 0
    fi
    policy="$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)"
    case "$policy" in
        production) lane=signed ;;
        test)
            lane=unsigned
            if [ -f "$MP_ROOT/.env" ]; then
                commit="$(jq -r '.current_commit // empty' \
                    "$MP_STATE/test-deployments/current.json" 2>/dev/null || true)"
                [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || {
                    ui_error "A live unsigned installation requires an exact deployment receipt. Commissioning will not pin the management checkout."
                    return 1
                }
            else
                commit="$(git -C "$MP_ROOT" rev-parse HEAD 2>/dev/null || true)"
            fi
            [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || {
                ui_error "Unsigned commissioning requires a lowercase 40-character checkout HEAD."
                return 1
            }
            git -C "$MP_ROOT" fetch --no-tags --force origin "$commit" >/dev/null 2>&1 \
                || { ui_error "Checkout HEAD is not available from origin. Push the exact commit before commissioning."; return 1; }
            [ "$(git -C "$MP_ROOT" rev-parse FETCH_HEAD 2>/dev/null || true)" = "$commit" ] \
                || { ui_error "Origin did not return the exact checkout HEAD. Commissioning stopped before state was created."; return 1; }
            ;;
        *) ui_error "Unsupported deployment policy: $policy"; return 1 ;;
    esac
    temporary="$(mktemp "$MP_STATE/setup-state.XXXXXX")" || return 1
    jq -n --arg mode "$mode" --arg lane "$lane" --arg commit "$commit" \
        --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '{format:"mp-opt-setup-state-v2",mode:$mode,state:"in_progress",
          deployment_lane:$lane,campaign_commit:(if $commit == "" then null else $commit end),
          signed_baseline:null,completed:[],current_action:"Verifying signed rollback baseline",
          last_failure:null,started_at:$now,updated_at:$now}' \
        > "$temporary" || { rm -f "$temporary"; return 1; }
    chmod 600 "$temporary"
    mv "$temporary" "$MP_SETUP_V2_STATE"
}

mp_setup_state_update() {
    local filter="$1" temporary
    shift
    temporary="$(mktemp "$MP_STATE/setup-state.XXXXXX")" || return 1
    jq "$@" --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "$filter | .updated_at=\$now" "$MP_SETUP_V2_STATE" > "$temporary" \
        || { rm -f "$temporary"; return 1; }
    chmod 600 "$temporary"
    mv "$temporary" "$MP_SETUP_V2_STATE"
}

mp_setup_state_action() {
    mp_setup_state_update '.current_action=$action | .last_failure=null' --arg action "$1"
}

mp_setup_state_failure() {
    local code="$1" message="${2:0:400}"
    mp_setup_state_update \
        '.last_failure={code:$code,message:$message,at:$now}' \
        --arg code "$code" --arg message "$message"
}

mp_setup_record_signed_baseline() {
    local tag commit
    tag="$(sed -n 's/^MP_RELEASE_TAG=//p' "$MP_ROOT/.release.env" 2>/dev/null | head -1)"
    commit="$(sed -n 's/^MP_RELEASE_COMMIT=//p' "$MP_ROOT/.release.env" 2>/dev/null | head -1)"
    [ -n "$tag" ] && [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || {
        ui_error "The verified signed rollback baseline did not expose a valid tag and commit."
        return 1
    }
    mp_setup_state_update \
        '.signed_baseline={tag:$tag,commit:$commit} | .completed=((.completed+["signed_baseline_verified"])|unique)' \
        --arg tag "$tag" --arg commit "$commit"
}

mp_setup_state_has() {
    jq -e --arg step "$1" '.completed | index($step) != null' \
        "$MP_SETUP_V2_STATE" >/dev/null 2>&1
}

mp_setup_state_mark() {
    mp_setup_state_update '.completed=((.completed + [$step]) | unique) | .last_failure=null' --arg step "$1"
}

mp_setup_state_complete() {
    mp_setup_state_update \
        '.state="complete" | .completed_at=$now | .current_action="Complete" | .last_failure=null'
}

mp_setup_state_clear_completed() {
    [ -f "$MP_SETUP_V2_STATE" ] || return 0
    [ "$(jq -r '.state // empty' "$MP_SETUP_V2_STATE")" = complete ] || return 0
    rm -f "$MP_SETUP_V2_STATE" "$MP_SETUP_V2_PENDING_JOIN" \
        "$MP_SETUP_V2_PENDING_BOOTSTRAP" "$MP_SETUP_V2_PENDING_LOCAL_JOIN" \
        "$MP_SETUP_V2_PENDING_REPLACEMENT" "$MP_SETUP_V2_IMPORT_RECEIPT"
}

# Present the already-installed bootstrap value as a separately acknowledged
# checkpoint. If SSH drops while it is on screen, setup resumes here and shows
# the same value again instead of silently advancing past it.
mp_setup_present_bootstrap() {
    local domain token body
    domain="$(mp_env_get DOMAIN)" || return 1
    [ -s "$MP_ROOT/secrets/root_bootstrap_token" ] \
        || { ui_error "The protected root bootstrap code is missing or empty."; return 1; }
    token="$(cat "$MP_ROOT/secrets/root_bootstrap_token")" || return 1
    printf -v body \
        'Open https://%s/bootstrap now.\n\nRoot bootstrap code:\n%s\n\nRegister the root passkey in the browser before continuing setup. Store this code securely until registration succeeds; it is not written to the management log.' \
        "$domain" "$token"
    ui_copyable_terminal_text "Root bootstrap" "$body" \
        "Copy the code, open the URL and start root-passkey registration. Press Enter after the browser ceremony begins; setup will wait for successful completion." || {
        unset token body
        return 1
    }
    unset token body
}

mp_setup_commissioning_stage() {
    local stage
    mp_compose_init || return 1
    stage="$("${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d masterplan -Atqc \
        "SELECT CASE
            WHEN NOT EXISTS (SELECT 1 FROM server_settings WHERE key='root_recovery_download_acknowledged_at') THEN 'recovery'
            WHEN NOT EXISTS (
                SELECT 1 FROM evidence_keys
                WHERE role='controller' AND activated_at IS NOT NULL
                  AND revoked_at IS NULL AND trust_establishment_sha256 IS NOT NULL
            ) THEN 'controller'
            WHEN NOT EXISTS (SELECT 1 FROM governance_publications WHERE version=1) THEN 'governance'
            WHEN EXISTS (
                SELECT 1 FROM server_settings
                WHERE key='root_commissioning_completed_at' AND value <> ''
            ) AND EXISTS (
                SELECT 1 FROM server_settings
                WHERE key='root_commissioning_receipt_sha256' AND value ~ '^[0-9a-f]{64}$'
            ) THEN 'complete'
            ELSE 'governance'
        END" 2>/dev/null || true)"
    case "$stage" in recovery|controller|governance|complete) printf '%s\n' "$stage" ;; *) return 1 ;; esac
}

mp_setup_sync_commissioning_recipient() {
    local recipient
    mp_compose_init || return 1
    recipient="$("${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d masterplan -Atqc \
        "SELECT value FROM server_settings WHERE key='root_recovery_recipient'" 2>/dev/null || true)"
    [[ "$recipient" =~ ^age1[0-9a-z]+$ ]] || return 1
    if declare -F mp_ha_sync_recovery_recipient >/dev/null && [ -f "$MP_HA_CONFIG" ]; then
        mp_ha_sync_recovery_recipient "$recipient"
    else
        mp_store_recovery_recipient_local "$recipient"
    fi
}

mp_setup_wait_for_root_commissioning() (
    local interval attempt=1 stage label action retired=0
    interval="${MP_ROOT_PASSKEY_POLL_INTERVAL_SECONDS:-5}"
    [[ "$interval" =~ ^[0-9]+$ ]] || interval=5
    trap 'return 130' INT TERM PIPE
    while true; do
        if ! mp_root_bootstrap_is_disabled; then
            label="Root passkey registration"
            action="Open the bootstrap page and register the root passkey."
        else
            if [ "$retired" -eq 0 ]; then
                mp_retire_root_bootstrap_secret || return 1
                retired=1
            fi
            stage="$(mp_setup_commissioning_stage 2>/dev/null || true)"
            case "$stage" in
                recovery) label="Step 1 of 3 — Recovery key"; action="Generate, download, reselect and verify the recovery file in the browser." ;;
                controller) label="Step 2 of 3 — Controller identity"; action="Generate or import the controller private-key file, move it into protected custody, and approve its public identity." ;;
                governance) label="Step 3 of 3 — Governance baseline"; action="Complete the draft, preview it, publish version 1 and run final checks." ;;
                complete)
                    mp_setup_sync_commissioning_recipient || return 1
                    printf '[%s] Root commissioning is complete. The public recovery recipient is installed and the bootstrap secret is retired.\n' "$(date -u +%H:%M:%SZ)"
                    return 0 ;;
                *) label="Commissioning status"; action="The application is starting. The verified setup step will appear automatically." ;;
            esac
        fi
        printf '[%s] %s (check %d). %s Retrying in %s seconds.\n' \
            "$(date -u +%H:%M:%SZ)" "$label" "$attempt" "$action" "$interval"
        sleep "$interval" || return $?
        attempt=$((attempt + 1))
    done
)

mp_setup_register_root_passkey() {
    mp_setup_state_action "Root commissioning — Step 1 of 3" || return 1
    mp_wait_for_health 45 || {
        ui_error "The pinned application is not publicly healthy, so root commissioning was not presented."
        return 1
    }
    curl -fsS --max-time 10 "https://$(mp_env_get DOMAIN)/api/v1/passkey/bootstrap-status" \
        | jq -e 'has("needs_bootstrap") and has("stage")' >/dev/null || {
        ui_error "The pinned application health endpoint passed, but root bootstrap status is unavailable."
        return 1
    }
    mp_setup_present_bootstrap || return 1
    ui_run_command "Waiting for root commissioning" \
        "Complete all three browser steps. Setup reports the current verified step every 5 seconds. Press Ctrl+C or close SSH to pause safely; commissioning will resume at this checkpoint." \
        mp_setup_wait_for_root_commissioning \
        || { ui_message "Root commissioning paused" "Setup remains at its last verified browser step. Run mp-opt to resume the safe polling view."; return 1; }
}

mp_setup_verify_exact_environment() {
    local commit="$1" short key value
    short="${commit:0:12}"
    [ "$(sed -n 's/^MP_TEST_COMMIT=//p' "$MP_ROOT/.test-deployment.env" 2>/dev/null | head -1)" = "$commit" ] \
        || { ui_error "The unsigned environment does not match the pinned campaign commit."; return 1; }
    for key in MP_BACKEND_IMAGE MP_CADDY_IMAGE MP_POSTGRES_IMAGE MP_TOOLS_IMAGE; do
        value="$(sed -n "s/^${key}=//p" "$MP_ROOT/.test-deployment.env" | head -1)"
        [[ "$value" =~ ^masterplan-(backend|caddy|postgres|tools):test-${short}$ ]] \
            || { ui_error "${key} is not pinned to test-${short}."; return 1; }
        docker image inspect "$value" >/dev/null 2>&1 \
            || { ui_error "Pinned image ${value} is missing."; return 1; }
    done
}

mp_setup_reconcile_unsigned_application() {
    local commit receipt mode failure_stage
    local -a deploy_args
    commit="$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")"
    mode="$(jq -r '.mode // empty' "$MP_SETUP_V2_STATE")"
    [[ "$commit" =~ ^[0-9a-f]{40}$ ]] \
        || { ui_error "The v2 setup state has no valid pinned campaign commit."; return 1; }
    receipt="$(jq -r '.current_commit // empty' "$MP_STATE/test-deployments/current.json" 2>/dev/null || true)"
    if [ -n "$receipt" ] && [ "$receipt" != "$commit" ]; then
        ui_error "The unsigned deployment receipt is for ${receipt}; setup is pinned to ${commit}. Automatic fallback is prohibited."
        return 1
    fi
    if [ "$receipt" = "$commit" ]; then
        mp_setup_state_action "Recovering exact deployment" || return 1
        mp_setup_verify_exact_environment "$commit" || return 1
        mp_compose_init || return 1
        "${MP_COMPOSE[@]}" up -d db backend caddy || return 1
        mp_wait_for_database 30 && mp_verify_database_schema_contract && mp_wait_for_health 45 \
            || { ui_error "The exact deployment receipt exists, but its database, schema, containers, or public health could not be recovered."; return 1; }
        return 0
    fi
    mp_setup_state_action "Building pinned commit" || return 1
    deploy_args=(apply "$commit" --confirm-full --confirm-migrations)
    case "$mode" in standalone-new|ha-primary-new|ha-join) deploy_args+=(--fresh-commissioning) ;; esac
    ui_run_command "Install exact test-campaign build" \
        "Building and activating only commit ${commit}. The signed release remains a verified rollback baseline and is not started." \
        "$MP_ROOT/deploy/test-deployment.sh" "${deploy_args[@]}" \
        || {
            failure_stage="$(jq -r '.stage // "unknown"' "$MP_STATE/test-deployments/last-failure.json" 2>/dev/null || printf unknown)"
            mp_setup_state_failure "UNSIGNED_DEPLOYMENT_FAILED" \
                "Pinned deployment paused at ${failure_stage}; resume retries the same commit." || true
            ui_error "The pinned application build paused at ${failure_stage} before a verified receipt was written. Resume commissioning to retry the exact same commit."
            return 1
        }
    [ "$(jq -r '.current_commit // empty' "$MP_STATE/test-deployments/current.json" 2>/dev/null || true)" = "$commit" ] \
        && mp_setup_verify_exact_environment "$commit" && mp_wait_for_health 45
}

mp_setup_deploy_application() {
    local lane
    lane="$(jq -r '.deployment_lane // empty' "$MP_SETUP_V2_STATE")"
    case "$lane" in
        unsigned) mp_setup_reconcile_unsigned_application ;;
        signed)
            [ -z "$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")" ] \
                || { ui_error "Signed commissioning must not contain a campaign commit."; return 1; }
            if mp_setup_state_has application_deployed; then
                mp_setup_state_action "Recovering signed deployment" || return 1
                mp_compose_init && "${MP_COMPOSE[@]}" up -d db backend caddy && mp_wait_for_health 45
            else
                mp_setup_state_action "Deploying signed release" || return 1
                "$MP_ROOT/deploy/deploy.sh" --no-pull && mp_wait_for_health 45
            fi
            ;;
        *) ui_error "The setup deployment lane is invalid."; return 1 ;;
    esac
}

mp_setup_prepare_node_material() {
    local node_id="$1" identity ssh_key public_ip public_ipv6 confirmed
    mp_require_commands age age-keygen jq openssl ssh ssh-keygen || return 1
    sudo -n install -d -o "$USER" -g "$(id -gn)" -m 0700 "$MP_HA_HOME" "$MP_HA_HOME/secrets" || return 1
    identity="$MP_HA_HOME/secrets/replication_age_identity"
    ssh_key="$HOME/.ssh/mp_opt_ha_peer"
    install -d -m 0700 "$HOME/.ssh"
    if [ ! -s "$identity" ]; then
        age-keygen -o "$identity" >/dev/null 2>&1 || return 1
        chmod 600 "$identity"
    fi
    if [ ! -s "$ssh_key" ]; then
        ssh-keygen -q -t ed25519 -N '' -C "mp-opt-${node_id}" -f "$ssh_key" || return 1
    fi
    chmod 600 "$ssh_key"; chmod 644 "$ssh_key.pub"
    public_ip="$(ui_input "Public address" "Public IPv4 address for this VPS (for example 203.0.113.10)" "")" || return 1
    python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "$public_ip" \
        || { ui_error "Enter a valid public IPv4 address."; return 1; }
    confirmed="$(ui_input "Public address" "Type the public IPv4 address once more" "")" || return 1
    [ "$public_ip" = "$confirmed" ] || { ui_error "The public IPv4 addresses do not match."; return 1; }
    public_ipv6="$(ui_input "Public address" "Optional public IPv6 address for this VPS (for example 2001:db8::10)" "")" || return 1
    [ -z "$public_ipv6" ] \
        || python3 -c 'import ipaddress,sys; ipaddress.IPv6Address(sys.argv[1])' "$public_ipv6" \
        || { ui_error "Enter a valid public IPv6 address or leave it blank."; return 1; }
    MP_SETUP_NODE_IPV4="$public_ip"
    MP_SETUP_NODE_IPV6="$public_ipv6"
    MP_SETUP_NODE_SSH_PUBLIC="$(cat "$ssh_key.pub")"
    MP_SETUP_NODE_SSH_HOST="$(sudo -n cat /etc/ssh/ssh_host_ed25519_key.pub)"
    MP_SETUP_NODE_AGE_RECIPIENT="$(age-keygen -y "$identity")"
    export MP_SETUP_NODE_IPV4 MP_SETUP_NODE_IPV6 MP_SETUP_NODE_SSH_PUBLIC MP_SETUP_NODE_SSH_HOST MP_SETUP_NODE_AGE_RECIPIENT
}

mp_setup_install_peer_trust() {
    local peer_ip="$1" peer_public="$2" peer_host="$3" peer_recipient="$4"
    local config_include="$HOME/.ssh/mp-opt-ha.conf" temporary
    [[ "$peer_public" == ssh-ed25519\ * ]] && [[ "$peer_host" == ssh-ed25519\ * ]] \
        && [[ "$peer_recipient" =~ ^age1[0-9a-z]{58}$ ]] \
        || { ui_error "The witness returned invalid peer public material."; return 1; }
    install -d -m 0700 "$HOME/.ssh"
    touch "$HOME/.ssh/authorized_keys" "$HOME/.ssh/known_hosts" "$HOME/.ssh/config"
    chmod 600 "$HOME/.ssh/authorized_keys" "$HOME/.ssh/known_hosts" "$HOME/.ssh/config"
    grep -qxF "$peer_public" "$HOME/.ssh/authorized_keys" \
        || printf '%s\n' "$peer_public" >> "$HOME/.ssh/authorized_keys"
    ssh-keygen -R "$peer_ip" -f "$HOME/.ssh/known_hosts" >/dev/null 2>&1 || true
    printf '%s %s\n' "$peer_ip" "$(awk '{print $1" "$2}' <<< "$peer_host")" \
        >> "$HOME/.ssh/known_hosts"
    temporary="$(mktemp "$HOME/.ssh/mp-opt-ha.conf.XXXXXX")" || return 1
    {
        printf 'Host mp-opt-ha-peer\n'
        printf '    HostName %s\n    User %s\n' "$peer_ip" "$USER"
        printf '    IdentityFile ~/.ssh/mp_opt_ha_peer\n    IdentitiesOnly yes\n'
        printf '    StrictHostKeyChecking yes\n'
    } > "$temporary"
    chmod 600 "$temporary"; mv "$temporary" "$config_include"
    grep -qxF 'Include ~/.ssh/mp-opt-ha.conf' "$HOME/.ssh/config" \
        || { temporary="$(mktemp "$HOME/.ssh/config.XXXXXX")" || return 1; \
            { printf 'Include ~/.ssh/mp-opt-ha.conf\n'; cat "$HOME/.ssh/config"; } > "$temporary"; \
            chmod 600 "$temporary"; mv "$temporary" "$HOME/.ssh/config"; }
    printf '%s\n' "$peer_recipient" > "$MP_HA_HOME/peer-age-recipient"
    chmod 600 "$MP_HA_HOME/peer-age-recipient"
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer true \
        || { ui_error "The generated peer SSH trust could not be verified."; return 1; }
}

mp_setup_install_ha_identity() {
    local node_id="$1" peer_id="$2" cluster_id="$3" witness="$4" token="$5" staging
    printf '%s' "$token" > "$MP_HA_HOME/secrets/node_token"
    chmod 600 "$MP_HA_HOME/secrets/node_token"
    staging="$(mktemp "${TMPDIR:-/tmp}/mp-opt-ha.XXXXXX")" || return 1
    {
        printf 'HA_MODE=ha\nHA_ROLE=dynamic\nHA_NODE_ID=%s\n' "$node_id"
        printf 'HA_CLUSTER_ID=%s\nHA_GENERATION=1\n' "$cluster_id"
        printf 'HA_PEER_NODE_ID=%s\nHA_PEER_SSH=mp-opt-ha-peer\n' "$peer_id"
        printf 'HA_WITNESS_URL=%s\nHA_HEARTBEAT_INTERVAL_SECONDS=15\n' "$witness"
        printf 'HA_AUTOMATIC_FAILOVER=disabled\nHA_RECOVERY_STORAGE_MODE=manual_portable\n'
        printf 'HA_ARCHIVE_SSH_TARGET=\nHA_ALERT_EMAIL=\n'
    } > "$staging"
    chmod 600 "$staging"
    mp_ha_install_config "$staging"
    rm -f "$staging"
}

mp_setup_witness_call() {
    local action="$1" witness="$2" cluster="$3" token="$4" body_file="$5"
    { printf '%s\n' "$token"; cat "$body_file"; } \
        | python3 "$MP_ROOT/deploy/ha/commission_api.py" witness \
            "$action" "$witness" "$cluster"
}

mp_setup_deploy_witness() {
    local domain="$1" cluster_id="$2" deploy_token dns_token admin_token worker_name tools_image
    local output witness zone_id
    deploy_token="$(ui_password "Cloudflare" "Temporary Worker deployment API token")" || return 1
    dns_token="$(ui_password "Cloudflare" "Long-lived zone-scoped DNS Edit + Zone Read API token")" || return 1
    [ "${#deploy_token}" -ge 32 ] && [ "${#dns_token}" -ge 32 ] \
        || { ui_error "Both Cloudflare tokens appear incomplete."; return 1; }
    tools_image="$(sed -n 's/^MP_TOOLS_IMAGE=//p' "$MP_ROOT/.release.env" | head -1)"
    [[ "$tools_image" =~ ^ghcr\.io/brian-funk/masterplanoptimiserv3---server/tools@sha256:[0-9a-f]{64}$ ]] \
        || { unset deploy_token dns_token; ui_error "The signed release does not contain the commissioning tools image."; return 1; }
    worker_name="mp-opt-ha-$(tr -cd 'a-z0-9' <<< "${cluster_id:0:12}")"
    output="$(mktemp "$MP_STATE/wrangler-deploy.XXXXXX")" || return 1
    docker run --rm \
        -e CLOUDFLARE_API_TOKEN="$deploy_token" \
        -v "$MP_ROOT/infra/cloudflare-ha-witness:/worker:ro" \
        "$tools_image" deploy /worker/src/index.ts --config /worker/wrangler.toml --name "$worker_name" \
        > "$output" 2>&1 \
        || { ui_text_file "Worker deployment failed" "$output"; rm -f "$output"; return 1; }
    witness="$(grep -Eo 'https://[^[:space:]]+\.workers\.dev' "$output" | tail -1)"
    rm -f "$output"
    [ -n "$witness" ] || witness="$(ui_input "Cloudflare Worker" "Deployed Worker HTTPS URL")" || return 1
    [[ "$witness" =~ ^https://[^[:space:]]+\.workers\.dev/?$ ]] \
        || { ui_error "The deployed Worker URL is invalid."; return 1; }
    admin_token="$(mp_random_secret)"
    printf '%s' "$admin_token" \
        | docker run --rm -i -e CLOUDFLARE_API_TOKEN="$deploy_token" \
            "$tools_image" secret put ADMIN_TOKEN --name "$worker_name" >/dev/null \
        || { unset deploy_token dns_token admin_token; ui_error "Worker administrator secret could not be installed."; return 1; }
    printf '%s' "$dns_token" \
        | docker run --rm -i -e CLOUDFLARE_API_TOKEN="$deploy_token" \
            "$tools_image" secret put CLOUDFLARE_DNS_API_TOKEN --name "$worker_name" >/dev/null \
        || { unset deploy_token dns_token admin_token; ui_error "Worker DNS secret could not be installed."; return 1; }
    zone_id="$(printf '%s' "$dns_token" \
        | python3 "$MP_ROOT/deploy/ha/commission_api.py" zone-id "$domain")" \
        || { unset deploy_token dns_token admin_token; ui_error "Cloudflare zone discovery failed. The DNS token needs Zone Read and DNS Edit for this zone."; return 1; }
    MP_SETUP_WITNESS_URL="${witness%/}"
    MP_SETUP_ZONE_ID="$zone_id"
    MP_SETUP_WITNESS_ADMIN_TOKEN="$admin_token"
    export MP_SETUP_WITNESS_URL MP_SETUP_ZONE_ID MP_SETUP_WITNESS_ADMIN_TOKEN
    unset deploy_token dns_token
}

mp_setup_verify_smtp_and_dns() {
    local from_email domain selector spf dmarc dkim records
    [ -n "$(mp_env_get SMTP_HOST 2>/dev/null || true)" ] || return 0
    mp_require_commands dig || return 1
    if [ "$(mp_ha_role 2>/dev/null || printf standalone)" = dynamic ]; then
        mp_ha_verify_smtp_both_nodes required || return 1
    else
        mp_send_smtp_test || return 1
    fi
    from_email="$(mp_env_get SMTP_FROM_EMAIL)" || return 1
    domain="${from_email##*@}"
    selector="$(ui_input "Email DNS" "DKIM selector supplied by the SMTP provider (for example default or provider1; the part before ._domainkey)" "")" || return 1
    [[ "$selector" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$ ]] \
        || { ui_error "Enter the DKIM selector exactly as supplied by the mail provider."; return 1; }
    printf -v records \
        "SPF record:\n  Type: TXT\n  Name: %s\n  Value: use the exact SPF value supplied by the SMTP provider\n\nDMARC record:\n  Type: TXT\n  Name: _dmarc.%s\n  Value: use the organisation's approved DMARC policy\n\nDKIM record:\n  Type: TXT or CNAME, as specified by the provider\n  Name: %s._domainkey.%s\n  Value: use the exact DKIM value supplied by the SMTP provider" \
        "$domain" "$domain" "$selector" "$domain"
    ui_copyable_terminal_text "SMTP DNS records" "$records" \
        "Copy the record names needed at the DNS provider. Publish the provider-supplied values, wait for propagation, then press Enter to verify them." \
        || return 1
    spf="$(dig +short TXT "$domain" | tr -d '"' | grep -m1 'v=spf1' || true)"
    dmarc="$(dig +short TXT "_dmarc.$domain" | tr -d '"' | grep -m1 'v=DMARC1' || true)"
    dkim="$(dig +short TXT "${selector}._domainkey.$domain" | tr -d '"' | grep -m1 'v=DKIM1' || true)"
    [ -n "$dkim" ] \
        || dkim="$(dig +short CNAME "${selector}._domainkey.$domain" | grep -m1 '\.' || true)"
    [ -n "$spf" ] && [ -n "$dmarc" ] && [ -n "$dkim" ] || {
        ui_error "Email delivery authenticated, but public DNS is incomplete. Required records: SPF on ${domain}, DMARC on _dmarc.${domain}, and DKIM on ${selector}._domainkey.${domain}. Publish the exact values supplied by your mail provider, wait for DNS propagation, then resume this checkpoint."
        return 1
    }
    ui_message "Email verified" "SMTP delivery succeeded and public SPF, DKIM and DMARC records are visible. The TUI did not modify mail DNS."
}

mp_setup_standalone_dns_matches() {
    local domain="$1" public_ip="$2" public_ipv6="${3:-}" answer answer6
    answer="$(dig +short A "$domain" | grep -Fx "$public_ip" || true)"
    [ -n "$answer" ] || return 1
    if [ -n "$public_ipv6" ]; then
        answer6="$(dig +short AAAA "$domain" | python3 -c 'import ipaddress,sys; expected=ipaddress.IPv6Address(sys.argv[1]); raise SystemExit(0 if any(ipaddress.IPv6Address(line.strip()) == expected for line in sys.stdin if line.strip()) else 1)' "$public_ipv6" 2>/dev/null && printf matched || true)"
        [ "$answer6" = matched ] || return 1
    fi
}

mp_setup_wait_for_standalone_dns() (
    local domain="$1" public_ip="$2" public_ipv6="${3:-}" interval attempt=1 address_label=address
    interval="${MP_DNS_POLL_INTERVAL_SECONDS:-30}"
    [[ "$interval" =~ ^[0-9]+$ ]] || interval=30
    trap 'return 130' INT TERM PIPE
    while ! mp_setup_standalone_dns_matches "$domain" "$public_ip" "$public_ipv6"; do
        printf '[%s] Public DNS is not visible yet (check %d). Retrying in %s seconds.\n' \
            "$(date -u +%H:%M:%SZ)" "$attempt" "$interval"
        sleep "$interval" || return $?
        attempt=$((attempt + 1))
    done
    [ -z "$public_ipv6" ] || address_label=addresses
    printf '[%s] Public DNS now resolves to the configured %s.\n' \
        "$(date -u +%H:%M:%SZ)" "$address_label"
)

mp_setup_verify_standalone_dns() {
    local domain public_ip public_ipv6 records ipv6_record=""
    domain="$(mp_env_get DOMAIN)" || return 1
    public_ip="$(ui_input "Public DNS" "Public IPv4 address of this VPS (for example 203.0.113.10)" "")" || return 1
    python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "$public_ip" \
        || { ui_error "Enter a valid public IPv4 address."; return 1; }
    public_ipv6="$(ui_input "Public DNS" "Optional public IPv6 address of this VPS (for example 2001:db8::10)" "")" || return 1
    [ -z "$public_ipv6" ] \
        || python3 -c 'import ipaddress,sys; ipaddress.IPv6Address(sys.argv[1])' "$public_ipv6" \
        || { ui_error "Enter a valid public IPv6 address or leave it blank."; return 1; }
    if [ -n "$public_ipv6" ]; then
        printf -v ipv6_record \
            '\n\nAAAA record:\n  Type: AAAA\n  Name: %s\n  Value: %s\n  TTL: 60\n  Cloudflare proxy: DNS only (grey cloud)' \
            "$domain" "$public_ipv6"
    fi
    printf -v records \
        'Hostname: %s\n\nA record:\n  Type: A\n  Name: %s\n  Value: %s\n  TTL: 60\n  Cloudflare proxy: DNS only (grey cloud)%s' \
        "$domain" "$domain" "$public_ip" "$ipv6_record"
    ui_copyable_terminal_text "Public DNS checkpoint" "$records" \
        "Copy these values to the DNS provider, then press Enter. MP-OPT will check immediately and every 30 seconds until public DNS is ready." \
        || return 1
    ui_run_command "Waiting for public DNS" \
        "Checking immediately and every 30 seconds. Press Ctrl+C or close SSH to pause safely; commissioning will resume at this checkpoint." \
        mp_setup_wait_for_standalone_dns "$domain" "$public_ip" "$public_ipv6" \
        || { ui_message "DNS wait paused" "Public DNS is not ready yet. No deployment was started; resume commissioning whenever you want to continue the automatic checks."; return 1; }
}

mp_setup_primary_create() {
    local mode="$1" domain cluster_id node_token pairing_secret body pending join_code bootstrap_tmp
    local bootstrap_ok attempt
    if [ ! -f "$MP_SETUP_V2_STATE" ]; then
        if [ "$mode" = ha-primary-new ] && [ -f "$MP_ROOT/.env" ]; then
            ui_error "A live standalone configuration already exists. Choose Convert this existing standalone server to Node A so an off-VPS recovery copy is required."
            return 1
        fi
        if [ "$mode" = convert-ha ] && [ ! -f "$MP_ROOT/.env" ]; then
            ui_error "No existing standalone configuration was found. Choose Fresh HA pair instead."
            return 1
        fi
    fi
    mp_setup_state_begin "$mode" || return 1
    mp_setup_state_has signed_baseline_verified || mp_setup_install_signed_release || return 1
    if ! mp_setup_state_has configuration; then
        mp_setup_state_action "Protected configuration" || return 1
        MP_SETUP_V2_ACTIVE=1 mp_guided_initial_configuration || return 1
        [ -f "$MP_ROOT/.env" ] || {
            ui_message "Commissioning paused" "The configuration review was cancelled. No configuration checkpoint was recorded; resume setup whenever you are ready."
            return 0
        }
        mp_setup_state_mark configuration
    fi
    domain="$(mp_env_get DOMAIN)" || return 1
    if [ "$mode" = convert-ha ] && ! mp_setup_state_has recovery_recipient; then
        mp_setup_state_action "Verifying recovery identity" || return 1
        [ -s "$MP_RECIPIENT_FILE" ] || mp_configure_recovery_recipient || return 1
        mp_setup_state_mark recovery_recipient
    fi
    if [ "$mode" = convert-ha ] && ! mp_setup_state_has migration_snapshot; then
        mp_setup_state_action "Creating migration safety snapshot" || return 1
        mp_prepare_guard_snapshot "single-to-ha" || return 1
        if [ "$(mp_recovery_storage_mode)" = manual_portable ]; then
            mp_snapshot_export_portable_interactive || return 1
            [ "$(jq -r '.snapshot // empty' "$MP_MANUAL_EXPORT_STATE" 2>/dev/null)" = "$(basename "$MP_GUARD_SNAPSHOT")" ] \
                || { ui_error "The newly verified migration snapshot was not confirmed on the workstation. Export that exact newest snapshot before resuming."; return 1; }
        fi
        ui_message "Migration safety" "The existing installation has a deep-verified recovery snapshot and its off-VPS copy was hash-confirmed."
        mp_setup_state_mark migration_snapshot
    fi
    if ! mp_setup_state_has witness_bootstrap; then
        mp_setup_state_action "Deploying HA witness" || return 1
        if [ ! -s "$MP_SETUP_V2_PENDING_BOOTSTRAP" ]; then
            mp_setup_prepare_node_material node-a || return 1
            cluster_id="mp-opt-$(cat /proc/sys/kernel/random/uuid)"
            node_token="$(mp_random_secret)"; pairing_secret="$(mp_random_secret)"
            mp_setup_deploy_witness "$domain" "$cluster_id" || return 1
            bootstrap_tmp="$(mktemp "$MP_STATE/pending-witness-bootstrap.XXXXXX")" || return 1
            jq -n --arg cluster "$cluster_id" --arg token "$node_token" \
                --arg pair "$pairing_secret" --arg zone "$MP_SETUP_ZONE_ID" \
                --arg domain "$domain" --arg witness "$MP_SETUP_WITNESS_URL" \
                --arg admin "$MP_SETUP_WITNESS_ADMIN_TOKEN" \
                --arg ipv4 "$MP_SETUP_NODE_IPV4" --arg ipv6 "$MP_SETUP_NODE_IPV6" \
                --arg ssh "$MP_SETUP_NODE_SSH_PUBLIC" --arg host "$MP_SETUP_NODE_SSH_HOST" \
                --arg age "$MP_SETUP_NODE_AGE_RECIPIENT" \
                '{format:"mp-opt-pending-witness-bootstrap-v1",cluster_id:$cluster,
                  node_token:$token,pairing_secret:$pair,zone_id:$zone,domain:$domain,
                  witness_url:$witness,admin_token:$admin,node_a_ipv4:$ipv4,
                  node_a_ipv6:$ipv6,node_a_ssh_public_key:$ssh,
                  node_a_ssh_host_key:$host,node_a_age_recipient:$age}' \
                > "$bootstrap_tmp" || { rm -f "$bootstrap_tmp"; return 1; }
            chmod 600 "$bootstrap_tmp" && mv "$bootstrap_tmp" "$MP_SETUP_V2_PENDING_BOOTSTRAP" \
                || { rm -f "$bootstrap_tmp"; return 1; }
            unset node_token pairing_secret MP_SETUP_WITNESS_ADMIN_TOKEN
        fi
        jq -e '.format == "mp-opt-pending-witness-bootstrap-v1"' \
            "$MP_SETUP_V2_PENDING_BOOTSTRAP" >/dev/null || {
            ui_error "The protected pending Worker bootstrap receipt is invalid. It was retained for manual inspection."
            return 1
        }
        cluster_id="$(jq -r .cluster_id "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
        node_token="$(jq -r .node_token "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
        pairing_secret="$(jq -r .pairing_secret "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
        MP_SETUP_WITNESS_URL="$(jq -r .witness_url "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
        MP_SETUP_WITNESS_ADMIN_TOKEN="$(jq -r .admin_token "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
        body="$(mktemp "$MP_STATE/witness-bootstrap.XXXXXX")" || return 1
        jq -n --arg cluster "$cluster_id" --arg token "$node_token" \
            --arg pair "$pairing_secret" \
            --arg zone "$(jq -r .zone_id "$MP_SETUP_V2_PENDING_BOOTSTRAP")" \
            --arg domain "$(jq -r .domain "$MP_SETUP_V2_PENDING_BOOTSTRAP")" \
            --arg ipv4 "$(jq -r .node_a_ipv4 "$MP_SETUP_V2_PENDING_BOOTSTRAP")" \
            --arg ipv6 "$(jq -r .node_a_ipv6 "$MP_SETUP_V2_PENDING_BOOTSTRAP")" \
            --arg ssh "$(jq -r .node_a_ssh_public_key "$MP_SETUP_V2_PENDING_BOOTSTRAP")" \
            --arg host "$(jq -r .node_a_ssh_host_key "$MP_SETUP_V2_PENDING_BOOTSTRAP")" \
            --arg age "$(jq -r .node_a_age_recipient "$MP_SETUP_V2_PENDING_BOOTSTRAP")" \
            '{cluster_id:$cluster,initial_holder:"node-a",node_a_token:$token,pairing_secret:$pair,
              zone_id:$zone,hostname:$domain,node_a_ipv4:$ipv4,node_a_ipv6:$ipv6,
              node_a_ssh_public_key:$ssh,node_a_ssh_host_key:$host,node_a_age_recipient:$age}' \
            > "$body"
        mp_setup_state_action "Registering Node A with HA witness" || { rm -f "$body"; return 1; }
        bootstrap_ok=false
        for attempt in 1 2 3 4 5; do
            if mp_setup_witness_call bootstrap "$MP_SETUP_WITNESS_URL" "$cluster_id" \
                "$MP_SETUP_WITNESS_ADMIN_TOKEN" "$body" >/dev/null; then
                bootstrap_ok=true
                break
            fi
            [ "$attempt" -eq 5 ] || sleep 2
        done
        [ "$bootstrap_ok" = true ] \
            || { rm -f "$body"; unset node_token pairing_secret MP_SETUP_WITNESS_ADMIN_TOKEN; return 1; }
        rm -f "$body"
        mp_setup_install_ha_identity node-a node-b "$cluster_id" "$MP_SETUP_WITNESS_URL" "$node_token" || return 1
        pending="$(mktemp "$MP_STATE/pending-ha-join.XXXXXX")" || return 1
        jq -n --arg cluster "$cluster_id" --arg domain "$domain" \
            --arg witness "$MP_SETUP_WITNESS_URL" --arg pair "$pairing_secret" \
            --arg lane "$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")" \
            --arg commit "$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")" \
            '{format:"mp-opt-ha-join-v2",cluster_id:$cluster,domain:$domain,witness_url:$witness,
              pairing_secret:$pair,node_id:"node-b",deployment_lane:$lane,
              campaign_commit:(if $commit == "" then null else $commit end)}' \
            > "$pending"
        chmod 600 "$pending"; mv "$pending" "$MP_SETUP_V2_PENDING_JOIN"
        mp_setup_state_mark witness_bootstrap
        rm -f "$MP_SETUP_V2_PENDING_BOOTSTRAP"
        unset node_token pairing_secret MP_SETUP_WITNESS_ADMIN_TOKEN
    fi
    mp_setup_state_action "Waiting for Node B join" || return 1
    join_code="$(python3 "$MP_ROOT/deploy/ha/pairing.py" encode < "$MP_SETUP_V2_PENDING_JOIN")" || return 1
    ui_copyable_terminal_text "Node B join code" "$join_code" \
        "On the second VPS, start mp-opt, choose Join an existing HA pair with a one-time code, and paste this code within 15 minutes. Then return here and choose Resume setup." || return 1
    ui_message "Pairing paused" "No application data was placed in the join code. Setup is checkpointed and can be resumed after Node B joins."
}

mp_setup_join_node() {
    local workflow="${1:-ha-join}" code decoded cluster domain witness pair node_id peer_id node_token body response pair_body peer db_password pending join_error lane campaign policy
    if [ ! -f "$MP_SETUP_V2_STATE" ] && [ -f "$MP_ROOT/.env" ]; then
        ui_error "This VPS already contains an application configuration. Join codes are accepted only on a fresh or explicitly cleared replacement VPS."
        return 1
    fi
    mp_setup_state_begin "$workflow" || return 1
    if [ ! -s "$MP_SETUP_V2_PENDING_LOCAL_JOIN" ]; then
        code="$(ui_input "Join HA" "Paste the one-time join code from the current primary")" || return 1
        decoded="$(mktemp "$MP_STATE/ha-join.XXXXXX")" || return 1
        printf '%s' "$code" | python3 "$MP_ROOT/deploy/ha/pairing.py" decode > "$decoded" \
            || { rm -f "$decoded"; ui_error "The join code is invalid, damaged, or unsupported."; return 1; }
        cluster="$(jq -r .cluster_id "$decoded")"; domain="$(jq -r .domain "$decoded")"
        witness="$(jq -r .witness_url "$decoded")"; pair="$(jq -r .pairing_secret "$decoded")"
        node_id="$(jq -r .node_id "$decoded")"
        lane="$(jq -r '.deployment_lane // empty' "$decoded")"
        campaign="$(jq -r '.campaign_commit // empty' "$decoded")"
        policy="$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)"
        [ "$lane" = signed ] || [ "$lane" = unsigned ] \
            || { rm -f "$decoded"; ui_error "The join code has no supported deployment lane."; return 1; }
        [ "$policy:$lane" = production:signed ] || [ "$policy:$lane" = test:unsigned ] \
            || { rm -f "$decoded"; ui_error "Node B deployment policy does not match Node A."; return 1; }
        if [ "$lane" = unsigned ]; then
            [[ "$campaign" =~ ^[0-9a-f]{40}$ ]] \
                || { rm -f "$decoded"; ui_error "The join code has no valid pinned campaign commit."; return 1; }
            git -C "$MP_ROOT" fetch --no-tags --force origin "$campaign" >/dev/null 2>&1 \
                && [ "$(git -C "$MP_ROOT" rev-parse FETCH_HEAD 2>/dev/null || true)" = "$campaign" ] \
                || { rm -f "$decoded"; ui_error "Node B cannot fetch Node A's exact pinned campaign commit."; return 1; }
        else
            campaign=""
        fi
        mp_setup_state_update \
            '.deployment_lane=$lane | .campaign_commit=(if $commit == "" then null else $commit end)' \
            --arg lane "$lane" --arg commit "$campaign" || { rm -f "$decoded"; return 1; }
        case "$node_id" in node-a) peer_id=node-b ;; node-b) peer_id=node-a ;; *) rm -f "$decoded"; return 1 ;; esac
        mp_setup_prepare_node_material "$node_id" || { rm -f "$decoded"; return 1; }
        node_token="$(mp_random_secret)"
        pending="$(mktemp "$MP_STATE/pending-local-join.XXXXXX")" || { rm -f "$decoded"; return 1; }
        jq -n --arg cluster "$cluster" --arg domain "$domain" --arg witness "$witness" \
            --arg pair "$pair" --arg node "$node_id" --arg peer "$peer_id" \
            --arg token "$node_token" --arg ipv4 "$MP_SETUP_NODE_IPV4" \
            --arg ipv6 "$MP_SETUP_NODE_IPV6" --arg ssh "$MP_SETUP_NODE_SSH_PUBLIC" \
            --arg host "$MP_SETUP_NODE_SSH_HOST" --arg age "$MP_SETUP_NODE_AGE_RECIPIENT" \
            --arg lane "$lane" --arg commit "$campaign" \
            '{format:"mp-opt-pending-local-join-v2",cluster_id:$cluster,domain:$domain,
              witness_url:$witness,pairing_secret:$pair,node_id:$node,peer_id:$peer,
              node_token:$token,ipv4:$ipv4,ipv6:$ipv6,ssh_public_key:$ssh,
              ssh_host_key:$host,age_recipient:$age,deployment_lane:$lane,
              campaign_commit:(if $commit == "" then null else $commit end)}' > "$pending" \
            || { rm -f "$decoded" "$pending"; return 1; }
        chmod 600 "$pending" && mv "$pending" "$MP_SETUP_V2_PENDING_LOCAL_JOIN" \
            || { rm -f "$decoded" "$pending"; return 1; }
        rm -f "$decoded"
        unset pair node_token
    fi
    jq -e '.format == "mp-opt-pending-local-join-v2"' \
        "$MP_SETUP_V2_PENDING_LOCAL_JOIN" >/dev/null || {
        ui_error "The protected pending local join receipt is invalid. It was retained for manual inspection."
        return 1
    }
    mp_setup_state_has signed_baseline_verified || mp_setup_install_signed_release || return 1
    cluster="$(jq -r .cluster_id "$MP_SETUP_V2_PENDING_LOCAL_JOIN")"
    domain="$(jq -r .domain "$MP_SETUP_V2_PENDING_LOCAL_JOIN")"
    witness="$(jq -r .witness_url "$MP_SETUP_V2_PENDING_LOCAL_JOIN")"
    pair="$(jq -r .pairing_secret "$MP_SETUP_V2_PENDING_LOCAL_JOIN")"
    node_id="$(jq -r .node_id "$MP_SETUP_V2_PENDING_LOCAL_JOIN")"
    peer_id="$(jq -r .peer_id "$MP_SETUP_V2_PENDING_LOCAL_JOIN")"
    node_token="$(jq -r .node_token "$MP_SETUP_V2_PENDING_LOCAL_JOIN")"
    body="$(mktemp "$MP_STATE/witness-join.XXXXXX")" || return 1
    jq -n --arg pair "$pair" --arg token "$node_token" \
        --arg ipv4 "$(jq -r .ipv4 "$MP_SETUP_V2_PENDING_LOCAL_JOIN")" \
        --arg ipv6 "$(jq -r .ipv6 "$MP_SETUP_V2_PENDING_LOCAL_JOIN")" \
        --arg ssh "$(jq -r .ssh_public_key "$MP_SETUP_V2_PENDING_LOCAL_JOIN")" \
        --arg host "$(jq -r .ssh_host_key "$MP_SETUP_V2_PENDING_LOCAL_JOIN")" \
        --arg age "$(jq -r .age_recipient "$MP_SETUP_V2_PENDING_LOCAL_JOIN")" \
        --arg node "$node_id" \
        '{pairing_secret:$pair,node_id:$node,node_token:$token,ipv4:$ipv4,ipv6:$ipv6,
          ssh_public_key:$ssh,ssh_host_key:$host,age_recipient:$age}' > "$body"
    join_error="$(mktemp "$MP_STATE/witness-join-error.XXXXXX")" || { rm -f "$body"; return 1; }
    if ! response="$(mp_setup_witness_call join "$witness" "$cluster" "$pair" "$body" 2> "$join_error")"; then
        if grep -q 'HTTP 409' "$join_error"; then
            rm -f "$MP_SETUP_V2_PENDING_LOCAL_JOIN"
            ui_error "The join code expired, was replaced, or belongs to different node material. Ask the current primary to display a fresh code, then resume setup here."
        else
            ui_error "The HA witness could not be reached. The protected pending join was retained and will be retried exactly when setup resumes."
        fi
        rm -f "$body" "$join_error"; unset pair node_token
        return 1
    fi
    rm -f "$join_error"
    jq -e '.joined == true' <<< "$response" >/dev/null || { rm -f "$body"; return 1; }
    mp_setup_install_ha_identity "$node_id" "$peer_id" "$cluster" "$witness" "$node_token" || return 1
    pair_body="$(mktemp "$MP_STATE/pair-state.XXXXXX")" || return 1
    jq -n --arg node "$node_id" '{node_id:$node}' > "$pair_body"
    response="$(mp_setup_witness_call pair-state "$witness" "$cluster" "$node_token" "$pair_body")" || return 1
    peer="$(jq -c --arg peer "$peer_id" '.nodes[] | select(.node_id == $peer)' <<< "$response")"
    mp_setup_install_peer_trust "$(jq -r .ipv4 <<< "$peer")" \
        "$(jq -r .ssh_public_key <<< "$peer")" "$(jq -r .ssh_host_key <<< "$peer")" \
        "$(jq -r .age_recipient <<< "$peer")" || return 1
    # The first incoming copy deliberately excludes node-local database
    # credentials. Create only that local receiver scaffold; all shared
    # application configuration and secrets arrive in the verified bundle.
    if [ ! -f "$MP_ROOT/.env" ]; then
        db_password="$(openssl rand -hex 32)"
        {
            printf 'DOMAIN=%s\n' "$domain"
        } > "$MP_ROOT/.env"
        chmod 600 "$MP_ROOT/.env"
        mkdir -p "$MP_ROOT/secrets"
        chmod 700 "$MP_ROOT/secrets"
        printf '%s' "$db_password" > "$MP_ROOT/secrets/database_password"
        # Compose validates top-level secret sources even when the receiver is
        # starting only PostgreSQL. Empty placeholders carry no shared secret
        # and are atomically replaced by the first authorised bundle.
        : > "$MP_ROOT/secrets/secret_key"
        : > "$MP_ROOT/secrets/ip_hmac_key"
        : > "$MP_ROOT/secrets/vapid_private_key"
        : > "$MP_ROOT/secrets/root_bootstrap_token"
        : > "$MP_ROOT/secrets/smtp_token"
        : > "$MP_ROOT/secrets/evidence_signing_key"
        chmod 600 "$MP_ROOT/secrets/"*
        unset db_password
    fi
    "$MP_ROOT/deploy/ha/install_services.sh" || return 1
    rm -f "$body" "$pair_body" "$MP_SETUP_V2_PENDING_LOCAL_JOIN"; unset pair node_token
    mp_setup_state_mark joined
    if [ "$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")" = unsigned ]; then
        mp_setup_state_action "Waiting for pinned images from Node A"
        ui_message "HA node joined" "The one-time code is consumed for ${node_id}. This node is pinned to $(jq -r .campaign_commit "$MP_SETUP_V2_STATE") and is waiting for Node A to transfer and activate those exact images."
    else
        mp_setup_state_complete
        ui_message "HA node joined" "The one-time code is consumed for ${node_id}. Peer SSH and replication encryption were verified. Only a node-local database credential was created here; the current holder will now send the complete protected shared application state."
    fi
}

mp_setup_replace_standby() {
    local pair body token response join_code domain pending replacement_tmp lane campaign
    mp_load_ha_config || return 1
    [ "$HA_ROLE" = dynamic ] || { ui_error "This server is not an HA node."; return 1; }
    mp_require_active_or_standalone || return 1
    [ "$(jq -r '.automatic_failover // false' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null)" = false ] \
        || { ui_error "Disable automatic failover before replacing the standby."; return 1; }
    if [ ! -s "$MP_SETUP_V2_PENDING_REPLACEMENT" ]; then
        ui_require_phrase "Replace lost standby" \
            "The old standby must be powered off. Its witness credential will be revoked immediately and a join code will remain valid for 15 minutes." \
            "REPLACE STANDBY" || return 0
        replacement_tmp="$(mktemp "$MP_STATE/pending-replacement-request.XXXXXX")" || return 1
        jq -n --arg pair "$(mp_random_secret)" --arg target "$HA_PEER_NODE_ID" \
            '{format:"mp-opt-pending-replacement-v1",pairing_secret:$pair,target_node_id:$target}' \
            > "$replacement_tmp" || { rm -f "$replacement_tmp"; return 1; }
        chmod 600 "$replacement_tmp" && mv "$replacement_tmp" "$MP_SETUP_V2_PENDING_REPLACEMENT" \
            || { rm -f "$replacement_tmp"; return 1; }
    fi
    jq -e --arg target "$HA_PEER_NODE_ID" \
        '.format == "mp-opt-pending-replacement-v1" and .target_node_id == $target' \
        "$MP_SETUP_V2_PENDING_REPLACEMENT" >/dev/null \
        || { ui_error "The protected pending replacement request is invalid."; return 1; }
    pair="$(jq -r .pairing_secret "$MP_SETUP_V2_PENDING_REPLACEMENT")"
    token="$(cat "$MP_HA_HOME/secrets/node_token")"
    body="$(mktemp "$MP_STATE/pair-open.XXXXXX")" || return 1
    jq -n --arg node "$HA_NODE_ID" --arg target "$HA_PEER_NODE_ID" --arg pair "$pair" \
        '{node_id:$node,target_node_id:$target,pairing_secret:$pair}' > "$body"
    response="$(mp_setup_witness_call pair-open "$HA_WITNESS_URL" "$HA_CLUSTER_ID" "$token" "$body")" \
        || { rm -f "$body"; unset pair token; return 1; }
    jq -e '.pairing_open == true' <<< "$response" >/dev/null || return 1
    domain="$(mp_env_get DOMAIN)" || return 1
    if [ "$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)" = test ]; then
        lane=unsigned
        campaign="$(jq -r '.current_commit // empty' "$MP_STATE/test-deployments/current.json" 2>/dev/null || true)"
        [[ "$campaign" =~ ^[0-9a-f]{40}$ ]] \
            || { ui_error "The active unsigned deployment has no exact receipt."; return 1; }
    else
        lane=signed; campaign=""
    fi
    pending="$(mktemp "$MP_STATE/pending-ha-join.XXXXXX")" || return 1
    jq -n --arg cluster "$HA_CLUSTER_ID" --arg domain "$domain" \
        --arg witness "$HA_WITNESS_URL" --arg pair "$pair" \
        --arg target "$HA_PEER_NODE_ID" --arg lane "$lane" --arg commit "$campaign" \
        '{format:"mp-opt-ha-join-v2",cluster_id:$cluster,domain:$domain,witness_url:$witness,
          pairing_secret:$pair,node_id:$target,deployment_lane:$lane,
          campaign_commit:(if $commit == "" then null else $commit end)}' \
        > "$pending"
    chmod 600 "$pending"; mv "$pending" "$MP_SETUP_V2_PENDING_JOIN"
    rm -f "$body"; unset pair token
    rm -f "$MP_SETUP_V2_STATE"
    mp_setup_state_begin replace-primary || return 1
    mp_setup_state_update \
        '.deployment_lane=$lane | .campaign_commit=(if $commit == "" then null else $commit end)' \
        --arg lane "$lane" --arg commit "$campaign" || return 1
    mp_setup_state_mark witness_bootstrap
    mp_setup_state_mark application_deployed
    rm -f "$MP_SETUP_V2_PENDING_REPLACEMENT"
    join_code="$(python3 "$MP_ROOT/deploy/ha/pairing.py" encode < "$MP_SETUP_V2_PENDING_JOIN")" || return 1
    ui_copyable_terminal_text "Replacement node join code" "$join_code" \
        "Paste this into Commission server > Replace a lost standby on the replacement VPS. Then resume commissioning here." || return 1
}

# Move an already-running load-balancer based cluster to direct, DNS-only
# routing without retaining a Cloudflare credential on either VPS. The old
# load balancer is disabled by the operator and retained for seven days as a
# rollback aid; deletion is a separate, explicit checkpoint.
mp_setup_migrate_legacy_load_balancer() {
    local domain cluster witness node_token worker_name deploy_token dns_token admin_token tools_image
    local zone_id node_a_ip node_b_ip body retirement eligible direct_ready origin secret_list identifiers
    mp_load_ha_config || return 1
    [ "$HA_ROLE" = dynamic ] || { ui_error "This action requires an existing HA cluster."; return 1; }
    mp_require_active_or_standalone || return 1
    mp_lock || return 1
    mp_setup_install_signed_release || return 1
    [ "$(jq -r '.automatic_failover // false' "$MP_ROOT/runtime/ha-control.json")" = false ] \
        || { ui_error "Disable automatic failover before changing routing."; return 1; }
    domain="$(mp_env_get DOMAIN)" || return 1
    cluster="$HA_CLUSTER_ID"; witness="$HA_WITNESS_URL"
    worker_name="$(ui_input "Cloudflare migration" "Existing HA Worker name" "mp-opt-ha-witness")" || return 1
    [[ "$worker_name" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] \
        || { ui_error "Enter the exact Cloudflare Worker name."; return 1; }
    deploy_token="$(ui_password "Cloudflare migration" "Temporary Worker deployment API token")" || return 1
    dns_token="$(ui_password "Cloudflare migration" "Long-lived zone-scoped DNS Edit + Zone Read token")" || return 1
    [ "${#deploy_token}" -ge 32 ] && [ "${#dns_token}" -ge 32 ] \
        || { ui_error "Both Cloudflare tokens appear incomplete."; return 1; }
    tools_image="$(sed -n 's/^MP_TOOLS_IMAGE=//p' "$MP_ROOT/.release.env" | head -1)"
    [[ "$tools_image" =~ ^ghcr\.io/brian-funk/masterplanoptimiserv3---server/tools@sha256:[0-9a-f]{64}$ ]] \
        || { unset deploy_token dns_token; ui_error "The signed release does not contain the commissioning tools image."; return 1; }
    admin_token="$(mp_random_secret)"
    docker run --rm -e CLOUDFLARE_API_TOKEN="$deploy_token" \
        -v "$MP_ROOT/infra/cloudflare-ha-witness:/worker:ro" \
        "$tools_image" deploy /worker/src/index.ts --config /worker/wrangler.toml --name "$worker_name" \
        >/dev/null \
        || { unset deploy_token dns_token admin_token; ui_error "The Worker could not be upgraded safely."; return 1; }
    printf '%s' "$admin_token" \
        | docker run --rm -i -e CLOUDFLARE_API_TOKEN="$deploy_token" \
            "$tools_image" secret put ADMIN_TOKEN --name "$worker_name" >/dev/null \
        || { unset deploy_token dns_token admin_token; ui_error "The Worker administrator secret could not be rotated."; return 1; }
    printf '%s' "$dns_token" \
        | docker run --rm -i -e CLOUDFLARE_API_TOKEN="$deploy_token" \
            "$tools_image" secret put CLOUDFLARE_DNS_API_TOKEN --name "$worker_name" >/dev/null \
        || { unset deploy_token dns_token admin_token; ui_error "The Worker DNS secret could not be installed."; return 1; }
    zone_id="$(printf '%s' "$dns_token" | python3 "$MP_ROOT/deploy/ha/commission_api.py" zone-id "$domain")" \
        || { unset deploy_token dns_token admin_token; return 1; }
    node_a_ip="$(ui_input "Direct routing" "Node A public IPv4 address")" || return 1
    node_b_ip="$(ui_input "Direct routing" "Node B public IPv4 address")" || return 1
    python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1]); ipaddress.IPv4Address(sys.argv[2])' \
        "$node_a_ip" "$node_b_ip" || { ui_error "Both node addresses must be valid public IPv4 addresses."; return 1; }
    body="$(mktemp "$MP_STATE/configure-dns.XXXXXX")" || return 1
    jq -n --arg zone "$zone_id" --arg hostname "$domain" --arg a "$node_a_ip" --arg b "$node_b_ip" \
        '{zone_id:$zone,hostname:$hostname,node_a_ipv4:$a,node_b_ipv4:$b}' > "$body"
    # Teach the upgraded witness the direct-routing zone before either Caddy
    # starts. This enables DNS-01 issuance while the old load balancer still
    # carries production traffic; no public A record changes until `ready`.
    mp_setup_witness_call configure-dns "$witness" "$cluster" "$admin_token" "$body" >/dev/null \
        || { rm -f "$body"; unset admin_token; return 1; }
    secret_list="$(docker run --rm -e CLOUDFLARE_API_TOKEN="$deploy_token" \
        "$tools_image" secret list --name "$worker_name")" \
        || { rm -f "$body"; unset deploy_token dns_token admin_token; ui_error "The Worker secret inventory could not be verified."; return 1; }
    if grep -q 'CLOUDFLARE_API_TOKEN' <<< "$secret_list"; then
        printf 'y\n' \
            | docker run --rm -i -e CLOUDFLARE_API_TOKEN="$deploy_token" \
                "$tools_image" secret delete CLOUDFLARE_API_TOKEN --name "$worker_name" >/dev/null \
            || { rm -f "$body"; unset deploy_token dns_token admin_token secret_list; ui_error "The obsolete broad Cloudflare secret could not be removed from the Worker."; return 1; }
    fi
    rm -f "$body"; unset deploy_token dns_token admin_token secret_list
    ui_run_command "Upgrade Node B" "Installing the same signed release and direct-TLS routing on Node B" \
        ssh -T -o BatchMode=yes mp-opt-ha-peer \
        "python3 /opt/masterplan/deploy/release/install_release.py --repo-root /opt/masterplan && /opt/masterplan/deploy/deploy.sh --no-pull" \
        || { ui_error "Node B could not install the direct-routing release. Routing was not activated."; return 1; }
    ui_run_command "Upgrade Node A" "Installing the signed direct-routing release on Node A" \
        bash -Eeuo pipefail -c \
        'python3 "$1/deploy/release/install_release.py" --repo-root "$1" && "$1/deploy/deploy.sh" --no-pull' \
        mp-opt-release "$MP_ROOT" \
        || { ui_error "Node A could not install the direct-routing release. Routing was not activated."; return 1; }
    mp_ha_replicate_now || return 1

    for origin in "$node_a_ip" "$node_b_ip"; do
        direct_ready=false
        for _ in $(seq 1 18); do
            if curl -fsS --max-time 10 --resolve "${domain}:443:${origin}" "https://${domain}/health" >/dev/null; then
                direct_ready=true
                break
            fi
            sleep 5
        done
        [ "$direct_ready" = true ] \
            || { ui_error "Direct public TLS did not become ready on ${origin}. The old load balancer is still active; correct the origin before resuming."; return 1; }
    done

    printf -v identifiers \
        'Cloudflare hostname: %s\nLegacy Worker: %s\n\nDisable the legacy load balancer for this hostname. Do not delete the load balancer or its pools yet.' \
        "$domain" "$worker_name"
    ui_copyable_terminal_text "Disable legacy routing" "$identifiers" \
        "Copy the identifiers if needed, disable the load balancer in Cloudflare, then press Enter. The next checkpoint creates a DNS-only A record with TTL 60." \
        || return 1
    node_token="$(cat "$MP_HA_HOME/secrets/node_token")"
    body="$(mktemp "$MP_STATE/routing-ready.XXXXXX")" || return 1
    jq -n --arg node "$HA_NODE_ID" '{node_id:$node}' > "$body"
    mp_setup_witness_call ready "$witness" "$cluster" "$node_token" "$body" >/dev/null \
        || { rm -f "$body"; ui_error "Direct DNS routing was not activated. Confirm the legacy load balancer is disabled and retry."; return 1; }
    rm -f "$body"; unset node_token
    if ! curl -fsS --max-time 20 "https://${domain}/health" >/dev/null; then
        ui_message "DNS propagation" "Both origins passed direct TLS before cutover and the witness changed the DNS record. Public resolvers may still use the old answer for up to its previous TTL; keep the old load balancer disabled and check public health again after propagation."
    fi
    retirement="$MP_STATE/legacy-load-balancer-retirement.json"
    eligible="$(date -u -d '+7 days' +%Y-%m-%dT%H:%M:%SZ)"
    jq -n --arg domain "$domain" --arg disabled "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg eligible "$eligible" \
        '{format:"mp-opt-legacy-lb-retirement-v1",domain:$domain,disabled_at:$disabled,delete_after:$eligible}' \
        > "$retirement"
    chmod 600 "$retirement"
    mp_audit "ha.routing-migration" "success" "dns-only"
    mp_unlock
    ui_message "DNS-only routing active" "Both origins passed direct TLS health. Keep the disabled load balancer for rollback until ${eligible}; then use the cleanup checkpoint."
}

mp_setup_cleanup_legacy_load_balancer() {
    local retirement eligible now identifiers
    retirement="$MP_STATE/legacy-load-balancer-retirement.json"
    [ -s "$retirement" ] || { ui_error "No pending legacy load-balancer retirement was recorded."; return 1; }
    eligible="$(jq -r .delete_after "$retirement")"
    now="$(date -u +%s)"
    [ "$now" -ge "$(date -u -d "$eligible" +%s)" ] \
        || { ui_error "The rollback window remains open until ${eligible}. Do not delete the old load balancer yet."; return 1; }
    printf -v identifiers \
        'Hostname: %s\n\nDelete only the disabled legacy load balancer and its two legacy pools. Keep the DNS A/AAAA records and the HA Worker.' \
        "$(jq -r .domain "$retirement")"
    ui_copyable_terminal_text "Retire legacy routing" "$identifiers" \
        "Copy the hostname if needed, perform the deletion in Cloudflare, then press Enter to return for confirmation." \
        || return 1
    ui_require_phrase "Retire old load balancer" \
        "In Cloudflare, delete only the disabled legacy load balancer and its two legacy pools. Do not delete the DNS A record or the HA Worker. Confirm after Cloudflare shows the old objects are gone." \
        "LEGACY LOAD BALANCER DELETED" || return 0
    rm -f "$retirement"
    mp_audit "ha.routing-migration-cleanup" "success" "operator-confirmed"
    ui_message "Legacy routing retired" "The seven-day rollback objects are recorded as removed. DNS-only routing remains active."
}

# Remove the DNS-only routing records and Durable Object state before deleting
# the Worker itself. This action deliberately leaves the local servers and
# databases untouched so an operator can export or wipe them separately.
mp_setup_decommission_cloudflare() {
    local cluster witness worker_name deploy_token admin_token tools_image body
    mp_load_ha_config || return 1
    [ "$HA_ROLE" = dynamic ] \
        || { ui_error "Cloudflare decommissioning is available only for an HA pair."; return 1; }
    mp_require_active_or_standalone || return 1
    ui_require_phrase "Retire HA Cloudflare resources" \
        "This removes the public A and AAAA records, ACME challenge records, Worker state and Worker. It does not erase either VPS." \
        "DECOMMISSION HA CLOUDFLARE" || return 0
    cluster="$HA_CLUSTER_ID"; witness="$HA_WITNESS_URL"
    worker_name="mp-opt-ha-$(tr -cd 'a-z0-9' <<< "${cluster:0:12}")"
    deploy_token="$(ui_password "Cloudflare" "Temporary Worker deployment API token")" || return 1
    [ "${#deploy_token}" -ge 32 ] \
        || { unset deploy_token; ui_error "The Cloudflare token appears incomplete."; return 1; }
    tools_image="$(sed -n 's/^MP_TOOLS_IMAGE=//p' "$MP_ROOT/.release.env" | head -1)"
    [[ "$tools_image" =~ ^ghcr\.io/brian-funk/masterplanoptimiserv3---server/tools@sha256:[0-9a-f]{64}$ ]] \
        || { unset deploy_token; ui_error "The signed release does not contain the commissioning tools image."; return 1; }
    admin_token="$(mp_random_secret)"
    printf '%s' "$admin_token" \
        | docker run --rm -i -e CLOUDFLARE_API_TOKEN="$deploy_token" \
            "$tools_image" secret put ADMIN_TOKEN --name "$worker_name" >/dev/null \
        || { unset deploy_token admin_token; ui_error "The Worker administrator secret could not be rotated."; return 1; }
    body="$(mktemp "$MP_STATE/decommission-cloudflare.XXXXXX")" || return 1
    jq -n --arg cluster "$cluster" '{confirm_cluster_id:$cluster}' > "$body"
    mp_setup_witness_call decommission "$witness" "$cluster" "$admin_token" "$body" >/dev/null \
        || { rm -f "$body"; unset deploy_token admin_token; ui_error "The Worker did not confirm DNS and state deletion."; return 1; }
    rm -f "$body"; unset admin_token
    printf 'y\n' | docker run --rm -i -e CLOUDFLARE_API_TOKEN="$deploy_token" \
        "$tools_image" delete --name "$worker_name" >/dev/null \
        || { unset deploy_token; ui_error "Worker state was erased, but the Worker deployment still needs manual deletion."; return 1; }
    unset deploy_token
    mp_audit "ha.cloudflare-decommission" "success" "dns-worker-state-removed"
    ui_message "Cloudflare resources retired" "The HA DNS records, Worker state and Worker were removed. The VPS installations were not changed."
}

mp_setup_primary_resume() {
    local cluster witness token body response peer mode pairing_expires pairing_secret join_code domain pending
    [ -s "$MP_SETUP_V2_PENDING_JOIN" ] || { ui_error "The protected pending join record is missing."; return 1; }
    mp_load_ha_config || return 1
    mode="$(jq -r '.mode // empty' "$MP_SETUP_V2_STATE")" || return 1
    cluster="$HA_CLUSTER_ID"; witness="$HA_WITNESS_URL"
    token="$(cat "$MP_HA_HOME/secrets/node_token")"
    body="$(mktemp "$MP_STATE/pair-state.XXXXXX")" || return 1
    jq -n --arg node "$HA_NODE_ID" '{node_id:$node}' > "$body"
    response="$(mp_setup_witness_call pair-state "$witness" "$cluster" "$token" "$body")" \
        || { rm -f "$body"; return 1; }
    if ! jq -e '.paired == true' <<< "$response" >/dev/null; then
        pairing_expires="$(jq -r '.expires_at // empty' <<< "$response")"
        if [ -n "$pairing_expires" ] \
            && [ "$(date -u -d "$pairing_expires" +%s)" -gt "$(date -u +%s)" ]; then
            join_code="$(python3 "$MP_ROOT/deploy/ha/pairing.py" encode < "$MP_SETUP_V2_PENDING_JOIN")" \
                || { rm -f "$body"; return 1; }
            rm -f "$body"
            ui_copyable_terminal_text "Node join code" "$join_code" \
                "This code remains valid until ${pairing_expires}. Paste it on the joining VPS, then resume setup here." || return 1
            return 0
        fi
        pairing_secret="$(mp_random_secret)"
        jq -n --arg node "$HA_NODE_ID" --arg target "$HA_PEER_NODE_ID" --arg pair "$pairing_secret" \
            '{node_id:$node,target_node_id:$target,pairing_secret:$pair}' > "$body"
        response="$(mp_setup_witness_call pair-open "$witness" "$cluster" "$token" "$body")" \
            || { rm -f "$body"; unset pairing_secret; return 1; }
        jq -e '.pairing_open == true' <<< "$response" >/dev/null || return 1
        domain="$(mp_env_get DOMAIN)" || return 1
        pending="$(mktemp "$MP_STATE/pending-ha-join.XXXXXX")" || return 1
        jq -n --arg cluster "$cluster" --arg domain "$domain" --arg witness "$witness" \
            --arg pair "$pairing_secret" --arg target "$HA_PEER_NODE_ID" \
            --arg lane "$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")" \
            --arg commit "$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")" \
            '{format:"mp-opt-ha-join-v2",cluster_id:$cluster,domain:$domain,witness_url:$witness,
              pairing_secret:$pair,node_id:$target,deployment_lane:$lane,
              campaign_commit:(if $commit == "" then null else $commit end)}' \
            > "$pending"
        chmod 600 "$pending"; mv "$pending" "$MP_SETUP_V2_PENDING_JOIN"
        join_code="$(python3 "$MP_ROOT/deploy/ha/pairing.py" encode < "$MP_SETUP_V2_PENDING_JOIN")" || return 1
        rm -f "$body"; unset pairing_secret
        ui_copyable_terminal_text "Replacement join code" "$join_code" \
            "The previous code expired. Paste this new 15-minute one-time code on the joining VPS, then resume here." || return 1
        return 0
    fi
    peer="$(jq -c --arg peer "$HA_PEER_NODE_ID" '.nodes[] | select(.node_id == $peer)' <<< "$response")"
    [ -n "$peer" ] || { rm -f "$body"; ui_error "The witness did not return the expected peer metadata."; return 1; }
    mp_setup_install_peer_trust "$(jq -r .ipv4 <<< "$peer")" \
        "$(jq -r .ssh_public_key <<< "$peer")" "$(jq -r .ssh_host_key <<< "$peer")" \
        "$(jq -r .age_recipient <<< "$peer")" || return 1
    mp_setup_state_mark paired
    if [ "$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")" = unsigned ] \
        || ! mp_setup_state_has application_deployed; then
        if [ "$mode" = convert-ha ] && [ -f "$MP_ROOT/infra/docker-compose.override.yml" ]; then
            mp_ha_convert_host_caddy || return 1
        fi
        mp_setup_deploy_application || return 1
        python3 "$MP_ROOT/deploy/ha/witness_control.py" ready >/dev/null || return 1
        mp_setup_state_has application_deployed || mp_setup_state_mark application_deployed
    fi
    if [ "$mode" = ha-primary-new ] && ! mp_setup_state_has root_commissioning_complete; then
        mp_setup_register_root_passkey || return 1
        mp_setup_state_mark root_commissioning_complete
    fi
    if [ "$mode" != ha-primary-new ] && ! mp_setup_state_has recovery_recipient; then
        [ -s "$MP_RECIPIENT_FILE" ] || mp_configure_recovery_recipient || return 1
        mp_setup_state_mark recovery_recipient
    elif [ "$mode" = ha-primary-new ]; then
        mp_setup_state_mark recovery_recipient
    fi
    if ! mp_setup_state_has replicated; then
        mp_ha_replicate_now || return 1
        mp_setup_state_mark replicated
    fi
    if ! mp_setup_state_has validated; then
        mp_validate_installation || return 1
        mp_setup_state_mark validated
    fi
    if ! mp_setup_state_has smtp_verified; then
        mp_setup_verify_smtp_and_dns || return 1
        mp_setup_state_mark smtp_verified
    fi
    if ! mp_setup_state_has automatic_failover; then
        mp_ha_refresh_witness_observations || return 1
        mp_ha_active_verification_readiness || return 1
        python3 "$MP_ROOT/deploy/ha/witness_control.py" automatic enabled >/dev/null || return 1
        mp_ha_set_config_value HA_AUTOMATIC_FAILOVER enabled || return 1
        mp_setup_state_mark automatic_failover
    fi
    rm -f "$body" "$MP_SETUP_V2_PENDING_JOIN"; unset token
    mp_setup_state_complete
    if [ "$mode" = convert-ha ]; then
        ui_message "HA conversion complete" "${HA_NODE_ID} is primary. ${HA_PEER_NODE_ID} accepted the current complete encrypted copy, all readiness gates passed, and automatic failover is enabled with a two-minute loss threshold and five-minute copy target. High availability changes the deployment facts. Review Policies & notices at https://$(mp_env_get DOMAIN)/admin/governance, save the authoritative HA state, review the exact diff and publish a new policy version."
    else
        ui_message "HA commissioning complete" "${HA_NODE_ID} is primary. ${HA_PEER_NODE_ID} accepted the current complete encrypted copy, all readiness gates passed, and automatic failover is enabled with a two-minute loss threshold and five-minute copy target. Open https://$(mp_env_get DOMAIN)/admin/governance to publish the controller-specific legal centre."
    fi
}

mp_setup_standalone() {
    if [ ! -f "$MP_SETUP_V2_STATE" ] && [ -f "$MP_ROOT/.env" ]; then
        ui_error "This server is already configured. Use the normal Configuration or Deploy menus instead of starting a fresh installation."
        return 1
    fi
    mp_setup_state_begin standalone-new || return 1
    mp_setup_state_has signed_baseline_verified || mp_setup_install_signed_release || return 1
    if ! mp_setup_state_has configuration; then
        mp_setup_state_action "Protected configuration" || return 1
        MP_SETUP_V2_ACTIVE=1 mp_guided_initial_configuration || return 1
        [ -f "$MP_ROOT/.env" ] || {
            ui_message "Commissioning paused" "The configuration review was cancelled. No configuration checkpoint was recorded; resume setup whenever you are ready."
            return 0
        }
        mp_setup_state_mark configuration
    fi
    if ! mp_setup_state_has public_dns; then
        mp_setup_verify_standalone_dns || return 1
        mp_setup_state_mark public_dns
    fi
    if [ "$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")" = unsigned ] \
        || ! mp_setup_state_has application_deployed; then
        mp_setup_deploy_application || return 1
        mp_setup_state_has application_deployed || mp_setup_state_mark application_deployed
    fi
    if ! mp_setup_state_has root_commissioning_complete; then
        mp_setup_register_root_passkey || return 1
        mp_setup_state_mark root_commissioning_complete
    fi
    mp_setup_state_mark recovery_recipient
    if ! mp_setup_state_has validated; then
        mp_validate_installation || return 1
        mp_setup_state_mark validated
    fi
    if ! mp_setup_state_has smtp_verified; then
        mp_setup_verify_smtp_and_dns || return 1
        mp_setup_state_mark smtp_verified
    fi
    mp_setup_state_complete
    ui_message "Standalone commissioning complete" "The application is live, root commissioning is sealed, and administration is available. SMTP is separate; use Configuration > SMTP test if it was enabled."
}

mp_setup_restore_full_loss() {
    if [ ! -f "$MP_SETUP_V2_STATE" ] && [ -f "$MP_ROOT/.env" ]; then
        ui_error "A configured installation already exists. Use Snapshots and recovery for an in-place guarded restore; full-loss recovery is only for a replacement VPS."
        return 1
    fi
    mp_setup_state_begin full-restore || return 1
    mp_setup_state_has signed_baseline_verified || mp_setup_install_signed_release || return 1
    mp_setup_state_action "Importing verified recovery snapshot" || return 1
    ui_message "Full-loss recovery" "Import the latest encrypted portable snapshot. The restore flow verifies its receipt and requires the recovery identity held outside the VPS."
    if ! mp_setup_state_has imported; then
        mp_snapshot_import_portable_interactive || return 1
        [ -s "$MP_PORTABLE_LAST_IMPORT_STATE" ] \
            && jq -e '.format == "mp-opt-portable-import-receipt-v1"' \
                "$MP_PORTABLE_LAST_IMPORT_STATE" >/dev/null \
            || { ui_error "The portable import did not produce a protected success receipt."; return 1; }
        install -m 0600 "$MP_PORTABLE_LAST_IMPORT_STATE" "$MP_SETUP_V2_IMPORT_RECEIPT" || return 1
        mp_setup_state_mark imported
    fi
    if ! mp_setup_state_has restored; then
        local imported_snapshot
        imported_snapshot="$(jq -er '.snapshot_path | select(type == "string")' \
            "$MP_SETUP_V2_IMPORT_RECEIPT")" || return 1
        case "$(readlink -f "$imported_snapshot")" in
            "$(readlink -f "$MP_SNAPSHOTS")"/*) ;;
            *) ui_error "The protected import receipt points outside the snapshot directory."; return 1 ;;
        esac
        mp_snapshot_restore_full_loss "$imported_snapshot" || return 1
        mp_setup_state_mark restored
    fi
    mp_setup_state_has application_deployed || mp_setup_state_mark application_deployed
    if ! mp_setup_state_has validated; then
        mp_validate_installation || return 1
        mp_setup_state_mark validated
    fi
    if ! mp_setup_state_has smtp_verified; then
        mp_setup_verify_smtp_and_dns || return 1
        mp_setup_state_mark smtp_verified
    fi
    mp_setup_state_complete
    ui_message "Recovery complete" "The signed release is live and the restored application passed validation. Commission a new Node B from this server if HA protection is required."
}

mp_setup_v2() {
    local choice mode state role action status=0 completed_state=false
    if [ -f "$MP_SETUP_V2_STATE" ]; then
        state="$(jq -r '.state // "invalid"' "$MP_SETUP_V2_STATE" 2>/dev/null || printf invalid)"
        mode="$(jq -r '.mode // empty' "$MP_SETUP_V2_STATE" 2>/dev/null || true)"
        if [ "$state" = complete ]; then
            ui_message "Commissioning" "The previous ${mode} workflow completed successfully. Selecting another workflow will archive its checkpoint; cancelling will preserve it."
            completed_state=true
            mode=""
        elif [ "$state" != in_progress ]; then
            ui_error "The setup checkpoint is invalid. Inspect $MP_SETUP_V2_STATE before continuing."
            return 1
        else
            ui_continue_message "Resuming commissioning" \
                "Current action: $(jq -r '.current_action // "Reconcile setup"' "$MP_SETUP_V2_STATE"). Deployment lane: $(jq -r '.deployment_lane' "$MP_SETUP_V2_STATE"). The pinned target will not change."
        fi
    fi
    if [ -z "${mode:-}" ]; then
        role="$(mp_ha_role 2>/dev/null || printf standalone)"
        if [ "$role" = dynamic ]; then
            choice="$(ui_menu "Commission server" "This server is already a member of an HA pair" \
                "replace-peer" "Replace the lost peer with a blank VPS" \
                "cancel" "Return without changing anything")" || return 0
        elif [ -f "$MP_ROOT/.env" ]; then
            choice="$(ui_menu "Commission server" "This standalone server is already configured" \
                "convert-ha" "Add a blank second VPS and convert to two-node HA" \
                "cancel" "Return without changing anything")" || return 0
        else
            choice="$(ui_menu "Commission server" "Choose the desired final topology" \
                "standalone-new" "Fresh single-node server" \
                "ha-primary-new" "Fresh two-node HA: create Node A and a join code" \
                "ha-join" "Join an existing HA pair with a one-time code" \
                "full-restore" "Recover a standalone server from a portable full snapshot" \
                "cancel" "Return without changing anything")" || return 0
        fi
        mode="$choice"
    fi
    if [ "$completed_state" = true ]; then
        case "$mode" in
            cancel|"") return 0 ;;
        esac
        mp_setup_state_clear_completed || return 1
    fi
    case "$mode" in
        standalone-new) mp_setup_standalone || status=$? ;;
        ha-primary-new|convert-ha) 
            if mp_setup_state_has witness_bootstrap 2>/dev/null; then mp_setup_primary_resume || status=$?; else mp_setup_primary_create "$mode" || status=$?; fi ;;
        ha-join)
            if mp_setup_state_has joined 2>/dev/null \
                && [ "$(jq -r '.deployment_lane // empty' "$MP_SETUP_V2_STATE")" = unsigned ]; then
                ui_message "Waiting for pinned deployment" \
                    "Node B is paired and pinned to $(jq -r .campaign_commit "$MP_SETUP_V2_STATE"). Resume on Node A; it will transfer, verify and activate the exact images here."
            else
                mp_setup_join_node ha-join || status=$?
            fi
            ;;
        replace-primary) mp_setup_primary_resume || status=$? ;;
        replace-peer) mp_setup_replace_standby || status=$? ;;
        # Compatibility for a setup checkpoint created by the pre-contextual
        # menu. New replacement VPSes use the same idempotent HA join path.
        replace-node) mp_setup_join_node replace-node || status=$? ;;
        full-restore) mp_setup_restore_full_loss || status=$? ;;
        cancel|"") return 0 ;;
        *) ui_error "Unsupported setup checkpoint mode: $mode"; return 1 ;;
    esac
    if [ "$status" -ne 0 ] && [ -s "$MP_SETUP_V2_STATE" ]; then
        action="$(jq -r '.current_action // "the current action"' "$MP_SETUP_V2_STATE" 2>/dev/null || printf 'the current action')"
        jq -e '.last_failure != null' "$MP_SETUP_V2_STATE" >/dev/null 2>&1 \
            || mp_setup_state_failure "SETUP_ACTION_PAUSED" "${action} did not complete; resume will reconcile authoritative deployment facts before retrying." || true
        ui_message "Commissioning paused" \
            "Current action: ${action}. The exact lane and commit remain pinned. Run mp-opt to resume; setup will not fall back to another deployment."
        return 0
    fi
    return "$status"
}
