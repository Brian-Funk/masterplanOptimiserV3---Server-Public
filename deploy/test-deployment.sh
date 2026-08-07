#!/usr/bin/env bash
# Reproducible, profile-gated deployment of an exact pushed commit.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MP_ROOT="${MP_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
export MP_ROOT

ui_error() { printf 'ERROR: %s\n' "$*" >&2; }
ui_message() { printf '%s\n' "$*"; }
ui_confirm() { return 1; }

if [ "${MP_TEST_APPLY_REEXEC:-0}" = 1 ]; then
    MP_TEST_OPERATIONS_ROOT="$SCRIPT_DIR"
else
    MP_TEST_OPERATIONS_ROOT="$MP_ROOT/deploy"
fi
# shellcheck source=management/common.sh
source "$MP_TEST_OPERATIONS_ROOT/management/common.sh"
# shellcheck source=management/snapshots.sh
source "$MP_TEST_OPERATIONS_ROOT/management/snapshots.sh"
# shellcheck source=management/ha.sh
source "$MP_TEST_OPERATIONS_ROOT/management/ha.sh"

MP_TEST_HOME="${MP_TEST_HOME:-$HOME/.local/share/mp-opt-test-deploy}"
MP_TEST_SOURCE="$MP_TEST_HOME/source"
MP_TEST_STATE_DIR="$MP_STATE/test-deployments"
MP_TEST_STATE_FILE="$MP_TEST_STATE_DIR/current.json"
MP_TEST_ENV="$MP_ROOT/.test-deployment.env"
MP_TEST_FAILURE_FILE="$MP_TEST_STATE_DIR/last-failure.json"
MP_TEST_STAGE_FILE="$MP_TEST_STATE_DIR/current-stage"

usage() {
    cat <<'EOF'
Usage:
  deploy/test-deployment.sh policy status|test|production
  deploy/test-deployment.sh plan COMMIT
  deploy/test-deployment.sh apply COMMIT [--confirm-full] [--confirm-migrations]
      [--fresh-commissioning]
      [--cloudflare-worker NAME --cloudflare-token-stdin]
  deploy/test-deployment.sh rollback
  deploy/test-deployment.sh restore-signed
  deploy/test-deployment.sh status

COMMIT must be an exact 40-character commit already available from origin.
Unsigned commands run only when the root-protected policy is "test".
EOF
}

policy_value() {
    cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf 'production\n'
}

set_apply_stage() {
    mkdir -p "$MP_TEST_STATE_DIR"
    printf '%s\n' "$1" > "$MP_TEST_STAGE_FILE"
    chmod 600 "$MP_TEST_STAGE_FILE"
}

record_apply_failure() {
    local target="$1" stage temporary
    stage="$(head -1 "$MP_TEST_STAGE_FILE" 2>/dev/null || printf unknown)"
    temporary="$(mktemp "$MP_TEST_STATE_DIR/failure.XXXXXX")" || return 0
    jq -n --arg target "$target" --arg stage "$stage" \
        --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '{format:"mp-opt-test-deployment-failure-v1",target:$target,stage:$stage,at:$at}' \
        > "$temporary" || { rm -f "$temporary"; return 0; }
    chmod 600 "$temporary"
    mv "$temporary" "$MP_TEST_FAILURE_FILE"
}

restore_verified_previous_deployment() {
    local verified_before="$1" previous_commit stage
    [ -n "$verified_before" ] || return 0
    stage="$(head -1 "$MP_TEST_STAGE_FILE" 2>/dev/null || printf unknown)"
    case "$stage" in preflight|build-*|peer-activation) ;; *) return 0 ;; esac
    [ -s "$MP_TEST_STATE_DIR/previous.env" ] || return 0
    previous_commit="$(sed -n 's/^MP_TEST_COMMIT=//p' "$MP_TEST_STATE_DIR/previous.env" | head -1)"
    [ "$previous_commit" = "$verified_before" ] || return 0
    install -m 0600 "$MP_TEST_STATE_DIR/previous.env" "$MP_TEST_ENV"
    mp_compose_init || return 1
    "${MP_COMPOSE[@]}" up -d db backend caddy || return 1
    mp_wait_for_health 45
}

require_test_policy() {
    [ "$(policy_value)" = test ] || {
        ui_error "Unsigned deployments are disabled. This server is production-policy."
        return 1
    }
}

ha_pairing_complete() {
    local setup_state="${MP_SETUP_V2_STATE:-$MP_STATE/setup-state-v2.json}"
    jq -e '(.completed // []) | index("paired") != null' "$setup_state" >/dev/null 2>&1
}

ha_pair_transport_ready() {
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer true >/dev/null 2>&1
}

require_fresh_commissioning_database() {
    local target="$1" setup_state="${MP_SETUP_V2_STATE:-$MP_STATE/setup-state-v2.json}"
    [ -s "$setup_state" ] || { ui_error "Fresh commissioning state is missing."; return 1; }
    jq -e --arg target "$target" \
        '.format == "mp-opt-setup-state-v2"
         and .state == "in_progress"
         and .deployment_lane == "unsigned"
         and .campaign_commit == $target
         and (.mode == "standalone-new" or .mode == "ha-primary-new" or .mode == "ha-join")
         and ((.completed | index("root_commissioning_complete")) == null)' \
        "$setup_state" >/dev/null \
        || { ui_error "Snapshot-free deployment requires an in-progress unsigned v2 setup pinned to this exact commit."; return 1; }
    [ ! -s "$MP_RECIPIENT_FILE" ] \
        || { ui_error "Fresh commissioning already has recovery material; use the normal snapshot-protected deployment path."; return 1; }
    [ ! -s "$MP_TEST_STATE_FILE" ] \
        || [ "$(jq -r '.current_commit // empty' "$MP_TEST_STATE_FILE" 2>/dev/null)" = "$target" ] \
        || { ui_error "A different verified unsigned deployment already exists."; return 1; }
}

assert_fresh_database_content() {
    local present safe
    mp_compose_init
    present="$("${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d masterplan -Atqc \
        "SELECT to_regclass('public.users') IS NOT NULL" 2>/dev/null || true)"
    [ "$present" = t ] || return 0
    safe="$("${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d masterplan -Atqc \
        "SELECT
            NOT EXISTS (SELECT 1 FROM events)
            AND NOT EXISTS (SELECT 1 FROM users WHERE NOT is_root_admin)
            AND (SELECT count(*) FROM users WHERE is_root_admin) <= 1
            AND NOT EXISTS (
              SELECT 1 FROM server_settings
              WHERE key IN ('root_commissioning_completed_at','root_recovery_download_acknowledged_at')
            )" 2>/dev/null || true)"
    [ "$safe" = t ] \
        || { ui_error "The database contains application data or completed commissioning facts; snapshot-free deployment is prohibited."; return 1; }
}

set_policy() {
    local value="$1" temporary
    case "$value" in
        status) policy_value ;;
        test)
            temporary="$(mktemp)"
            printf 'test\n' > "$temporary"
            sudo -n install -o root -g root -m 0644 "$temporary" "$MP_DEPLOYMENT_POLICY_FILE"
            rm -f "$temporary"
            printf 'Deployment policy: test (unsigned exact commits allowed)\n'
            ;;
        production)
            [ ! -f "$MP_TEST_ENV" ] || {
                ui_error "Restore the signed release before enabling production policy."
                return 1
            }
            temporary="$(mktemp)"
            printf 'production\n' > "$temporary"
            sudo -n install -o root -g root -m 0644 "$temporary" "$MP_DEPLOYMENT_POLICY_FILE"
            rm -f "$temporary"
            printf 'Deployment policy: production (signed releases only)\n'
            ;;
        *) usage; return 2 ;;
    esac
}

require_commit() {
    python3 "$MP_ROOT/deploy/test_deployment.py" validate commit "$1" >/dev/null
}

origin_url() {
    git -C "$MP_ROOT" remote get-url origin
}

prepare_source() {
    local commit="$1" remote
    require_commit "$commit"
    mkdir -p "$MP_TEST_HOME" "$MP_TEST_STATE_DIR"
    chmod 700 "$MP_TEST_HOME" "$MP_TEST_STATE_DIR"
    remote="$(origin_url)"
    if [ ! -d "$MP_TEST_SOURCE/.git" ]; then
        git clone --filter=blob:none --no-checkout "$remote" "$MP_TEST_SOURCE"
    fi
    git -C "$MP_TEST_SOURCE" remote set-url origin "$remote"
    git -C "$MP_TEST_SOURCE" fetch --no-tags --force origin "$commit"
    [ "$(git -C "$MP_TEST_SOURCE" rev-parse FETCH_HEAD)" = "$commit" ]
    [ "$(git -C "$MP_TEST_SOURCE" cat-file -t "$commit")" = commit ]
    git -C "$MP_TEST_SOURCE" checkout --detach --force "$commit"
    git -C "$MP_TEST_SOURCE" clean -ffdqx
}

baseline_commit() {
    local value=""
    if [ -s "$MP_TEST_STATE_FILE" ]; then
        value="$(jq -r '.current_commit // empty' "$MP_TEST_STATE_FILE")"
    fi
    if [ -z "$value" ] && [ -s "$MP_ROOT/.release.env" ]; then
        value="$(sed -n 's/^MP_RELEASE_COMMIT=//p' "$MP_ROOT/.release.env" | head -1)"
    fi
    if [ -z "$value" ]; then
        value="$(git -C "$MP_ROOT" rev-parse HEAD)"
    fi
    require_commit "$value"
    printf '%s\n' "$value"
}

create_plan() {
    local target="$1" base
    require_test_policy
    prepare_source "$target"
    base="$(baseline_commit)"
    git -C "$MP_TEST_SOURCE" fetch --no-tags --force origin "$base" >/dev/null 2>&1 || true
    python3 "$MP_TEST_SOURCE/deploy/test_deployment.py" classify \
        --repo "$MP_TEST_SOURCE" --base "$base" --target "$target"
}

env_set() {
    local file="$1" key="$2" value="$3" temporary
    temporary="$(mktemp "$MP_TEST_STATE_DIR/env.XXXXXX")"
    if [ -f "$file" ]; then
        awk -F= -v key="$key" '$1 != key {print}' "$file" > "$temporary"
    fi
    printf '%s=%s\n' "$key" "$value" >> "$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$file"
}

build_component() {
    local component="$1" target="$2" image dockerfile
    case "$component" in
        backend) image="masterplan-backend:test-${target:0:12}"; dockerfile=infra/Dockerfile ;;
        caddy) image="masterplan-caddy:test-${target:0:12}"; dockerfile=infra/Dockerfile.caddy ;;
        database) image="masterplan-postgres:test-${target:0:12}"; dockerfile=infra/Dockerfile.postgres ;;
        tools) image="masterplan-tools:test-${target:0:12}"; dockerfile=infra/Dockerfile.tools ;;
        *) return 2 ;;
    esac
    DOCKER_BUILDKIT=1 docker build -f "$MP_TEST_SOURCE/$dockerfile" -t "$image" "$MP_TEST_SOURCE" >&2
    printf '%s\n' "$image"
}

build_frontend() {
    mp_build_frontend_container "$MP_TEST_SOURCE"
    python3 "$MP_TEST_SOURCE/deploy/stamp_service_worker.py" \
        "$MP_TEST_SOURCE/web/out/sw.js" "$(git -C "$MP_TEST_SOURCE" rev-parse HEAD)"
    mkdir -p "$MP_TEST_SOURCE/runtime"
    python3 "$MP_TEST_SOURCE/deploy/generate_frontend_csp.py" "$MP_TEST_SOURCE/web/out" \
        --output "$MP_TEST_SOURCE/runtime/frontend-csp.caddy"
}

sync_operations() {
    local source="$1"
    rsync -a --delete "$source/deploy/" "$MP_ROOT/deploy/"
    rsync -a --delete "$source/infra/" "$MP_ROOT/infra/"
    install -m 0755 "$source/manage.sh" "$MP_ROOT/manage.sh"
    install -m 0755 "$source/configure-production.sh" "$MP_ROOT/configure-production.sh"
}

sync_frontend() {
    local source="$1"
    mkdir -p "$MP_ROOT/web" "$MP_ROOT/runtime"
    rm -rf "$MP_ROOT/web/.out.test-next"
    cp -a "$source/web/out" "$MP_ROOT/web/.out.test-next"
    rm -rf "$MP_ROOT/web/out"
    mv "$MP_ROOT/web/.out.test-next" "$MP_ROOT/web/out"
    install -m 0644 "$source/runtime/frontend-csp.caddy" "$MP_ROOT/runtime/frontend-csp.caddy"
}

prepare_runtime_from_installed_sources() {
    # Operations files are synchronised during this process. Run the helper in
    # a fresh shell so this deployment uses the just-installed common.sh, not
    # the function definitions that were loaded when the previous SHA started
    # the script.
    env MP_ROOT="$MP_ROOT" bash -c '
        set -Eeuo pipefail
        source "$MP_ROOT/deploy/management/common.sh"
        mp_prepare_frontend_csp_runtime
    '
}

ensure_optional_compose_secret_sources() {
    # Fresh unsigned commissioning intentionally bypasses deploy.sh, while
    # Compose bind mounts still require optional secret paths to exist. Never
    # overwrite a configured token; create only the absent disabled-state file.
    mkdir -p "$MP_ROOT/secrets"
    chmod 700 "$MP_ROOT/secrets"
    if [ ! -e "$MP_ROOT/secrets/evidence_github_fine_grained_token" ]; then
        install -m 0600 /dev/null \
            "$MP_ROOT/secrets/evidence_github_fine_grained_token"
    fi
}

compose_activate() {
    local components="$1" fresh_commissioning="${2:-false}" domain attempt
    prepare_runtime_from_installed_sources
    ensure_optional_compose_secret_sources
    mp_prepare_backend_secret_permissions
    mp_prepare_evidence_store
    mp_compose_init
    mp_compose_validate
    if grep -qw database <<< "$components"; then
        # Contract migrations must never run while an older backend is alive.
        set_apply_stage stop-old-backend
        "${MP_COMPOSE[@]}" stop backend >/dev/null 2>&1 || true
        set_apply_stage database
        "${MP_COMPOSE[@]}" up -d --no-deps --force-recreate db
        mp_wait_for_database 30
        [ "$fresh_commissioning" != true ] || assert_fresh_database_content
        set_apply_stage base-schema
        mp_ensure_base_schema
        set_apply_stage migrations
        mp_apply_migrations
        set_apply_stage schema-contract
        mp_verify_database_schema_contract
    fi
    set_apply_stage activation
    if grep -qw backend <<< "$components" || grep -qw database <<< "$components" \
        || grep -qw operations <<< "$components"; then
        "${MP_COMPOSE[@]}" up -d --no-deps --force-recreate backend
    fi
    if grep -qw caddy <<< "$components" || grep -qw frontend <<< "$components" \
        || grep -qw operations <<< "$components"; then
        "${MP_COMPOSE[@]}" up -d --no-deps --force-recreate caddy
    fi
    set_apply_stage public-health
    mp_wait_for_health 45
    domain="$(mp_env_get DOMAIN)"
    # Caddy may accept TLS a fraction of a second before a just-recreated
    # backend has bound its container address. Treat that bounded 502 window as
    # startup convergence, while still refusing to record a deployment receipt
    # unless the local TLS route becomes healthy.
    for attempt in $(seq 1 30); do
        if curl -fsS --max-time 10 --resolve "${domain}:443:127.0.0.1" \
            "https://${domain}/health" >/dev/null 2>&1; then
            break
        fi
        [ "$attempt" -lt 30 ] || {
            ui_error "The exact deployment is not healthy on this node's local TLS endpoint after 30 attempts."
            return 1
        }
        sleep 1
    done
    [ "$(stat -c %a "$MP_ROOT/runtime")" = 711 ] || {
        ui_error "Runtime traversal permissions were not preserved during activation."
        return 1
    }
}

write_state() {
    local target="$1" previous="$2" plan="$3" snapshot="${4:-}" temporary
    temporary="$(mktemp "$MP_TEST_STATE_DIR/state.XXXXXX")"
    jq -n --arg target "$target" --arg previous "$previous" \
        --arg baseline_tag "$(sed -n 's/^MP_RELEASE_TAG=//p' "$MP_ROOT/.release.env" 2>/dev/null | head -1)" \
        --arg baseline_commit "$(sed -n 's/^MP_RELEASE_COMMIT=//p' "$MP_ROOT/.release.env" 2>/dev/null | head -1)" \
        --arg snapshot "$snapshot" --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --argjson plan "$plan" \
        '{format:"mp-opt-test-state-v1",policy:"test",unsigned:true,label:"MP-OPT UNSIGNED TEST BUILD",
          current_commit:$target,previous_commit:$previous,
          signed_baseline:{tag:$baseline_tag,commit:$baseline_commit},applied_at:$now,
          snapshot:(if $snapshot == "" then null else $snapshot end),plan:$plan}' > "$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$MP_TEST_STATE_FILE"
}

advance_setup_campaign_pin() {
    local target="$1" previous="$2" action="${3:-}" setup_state temporary
    setup_state="${MP_SETUP_V2_STATE:-$MP_STATE/setup-state-v2.json}"
    [ -s "$setup_state" ] || return 0
    temporary="$(mktemp "$MP_STATE/setup-state.XXXXXX")"
    jq --arg target "$target" --arg previous "$previous" --arg action "$action" \
        --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
        if .format != "mp-opt-setup-state-v2" or .deployment_lane != "unsigned" then
          error("test deployment cannot advance a non-unsigned setup state")
        elif .campaign_commit != $previous and .campaign_commit != $target then
          error("campaign pin changed during exact deployment")
        else . end |
        .campaign_commit=$target | .updated_at=$now | .last_failure=null |
        if $action == "" then . else .current_action=$action end
    ' "$setup_state" > "$temporary" || { rm -f "$temporary"; return 1; }
    chmod 600 "$temporary"; mv "$temporary" "$setup_state"
}

reconcile_setup_campaign_pin_with_receipt() {
    local receipt="$1" role="$2" setup_state pinned peer_state
    setup_state="${MP_SETUP_V2_STATE:-$MP_STATE/setup-state-v2.json}"
    [ -s "$setup_state" ] || return 0
    [ "$(jq -r '.deployment_lane // empty' "$setup_state")" = unsigned ] || return 0
    pinned="$(jq -r '.campaign_commit // empty' "$setup_state")"
    [ "$pinned" != "$receipt" ] || return 0
    [[ "$pinned" =~ ^[0-9a-f]{40}$ ]] && [[ "$receipt" =~ ^[0-9a-f]{40}$ ]] \
        && [ "$(jq -r '.current_commit // empty' "$MP_TEST_STATE_FILE" 2>/dev/null || true)" = "$receipt" ] \
        && git -C "$MP_TEST_SOURCE" merge-base --is-ancestor "$pinned" "$receipt" \
        || { ui_error "The setup pin cannot be reconciled with the active exact deployment receipt."; return 1; }
    if [ "$role" = dynamic ] && ha_pairing_complete; then
        ha_pair_transport_ready || {
            ui_error "The paired setup pin cannot be reconciled while peer transport is unavailable."
            return 1
        }
        peer_state="$(ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
            env MP_ROOT=/opt/masterplan \
            /opt/masterplan/deploy/test-deployment.sh status 2>/dev/null)" \
            || { ui_error "The peer receipt could not be read while reconciling the setup pin."; return 1; }
        [ "$(jq -r '.current_commit // empty' <<< "$peer_state")" = "$receipt" ] \
            || { ui_error "The two active exact receipts differ; the setup pin was not changed."; return 1; }
    fi
    advance_setup_campaign_pin "$receipt" "$pinned"
}

peer_copy_image() {
    local image="$1" local_id peer_id
    docker save "$image" | gzip -1 | ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
        'gzip -d | docker load >/dev/null'
    local_id="$(docker image inspect -f '{{.Id}}' "$image")"
    peer_id="$(ssh -T -o BatchMode=yes mp-opt-ha-peer docker image inspect -f '{{.Id}}' "$image")"
    [ "$local_id" = "$peer_id" ]
}

peer_activate() {
    local target="$1" components="$2" fresh_commissioning="${3:-false}" peer_state peer_components
    # OpenSSH joins command arguments into a remote shell command without
    # preserving the caller's argument boundaries. A space-delimited component
    # list would therefore arrive as multiple arguments and the peer command
    # would exit before activation. Transport the validated list as one
    # comma-delimited token and decode it inside internal_activate().
    peer_components="${components// /,}"
    [[ "$peer_components" =~ ^(backend|frontend|caddy|database|tools|operations|witness)(,(backend|frontend|caddy|database|tools|operations|witness))*$ ]] \
        || { ui_error "The peer component set is invalid."; return 1; }
    scp -q "$MP_TEST_ENV" mp-opt-ha-peer:/tmp/mp-opt-test-deployment.env
    if grep -qw frontend <<< "$components"; then
        tar -C "$MP_TEST_SOURCE" -czf - web/out runtime/frontend-csp.caddy \
            | ssh -T -o BatchMode=yes mp-opt-ha-peer \
                "rm -rf '$MP_TEST_HOME/peer-assets' && mkdir -p '$MP_TEST_HOME/peer-assets' && tar -C '$MP_TEST_HOME/peer-assets' -xzf -"
    fi
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
        env MP_ROOT=/opt/masterplan MP_TEST_PEER=1 \
        /opt/masterplan/deploy/test-deployment.sh internal-activate "$target" "$peer_components" "$fresh_commissioning"
    peer_state="$(ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
        env MP_ROOT=/opt/masterplan \
        /opt/masterplan/deploy/test-deployment.sh status 2>/dev/null)" \
        || { ui_error "Node B's exact deployment receipt could not be read."; return 1; }
    [ "$(jq -r '.current_commit // empty' <<< "$peer_state")" = "$target" ] \
        || { ui_error "Node B did not record the exact pinned deployment receipt."; return 1; }
}

prepare_initial_peer() {
    local target="$1" key image
    require_test_policy
    [ "$(jq -r '.current_commit // empty' "$MP_TEST_STATE_FILE" 2>/dev/null || true)" = "$target" ] \
        || { ui_error "The local verified deployment receipt does not match the requested peer preparation."; return 1; }
    mp_load_ha_config
    [ "$HA_ROLE" = dynamic ] && ha_pairing_complete && ha_pair_transport_ready \
        || { ui_error "The verified HA peer transport is not ready for initial preparation."; return 1; }
    prepare_source "$target"
    for key in MP_BACKEND_IMAGE MP_CADDY_IMAGE MP_POSTGRES_IMAGE MP_TOOLS_IMAGE; do
        image="$(sed -n "s/^${key}=//p" "$MP_TEST_ENV" | head -1)"
        [[ "$image" =~ ^masterplan-(backend|caddy|postgres|tools):test-[0-9a-f]{12}$ ]] \
            && docker image inspect "$image" >/dev/null 2>&1 \
            || { ui_error "${key} is not a verified local test image."; return 1; }
        peer_copy_image "$image"
    done
    scp -q "$MP_TEST_ENV" mp-opt-ha-peer:/tmp/mp-opt-test-deployment.env
    # A conversion can advance through operations-only commits. Generated
    # frontend output is therefore not guaranteed to exist in the temporary
    # source checkout, while the verified active deployment always carries the
    # exact assets currently served by Node A. Seed Node B from that installed
    # deployment so initial HA preparation does not require an unrelated
    # frontend rebuild.
    [ -d "$MP_ROOT/web/out" ] && [ -s "$MP_ROOT/runtime/frontend-csp.caddy" ] \
        || { ui_error "The verified active frontend assets are unavailable for initial peer preparation."; return 1; }
    tar -C "$MP_ROOT" -czf - web/out runtime/frontend-csp.caddy \
        | ssh -T -o BatchMode=yes mp-opt-ha-peer \
            "rm -rf '$MP_TEST_HOME/peer-assets' && mkdir -p '$MP_TEST_HOME/peer-assets' && tar -C '$MP_TEST_HOME/peer-assets' -xzf -"
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
        env MP_ROOT=/opt/masterplan MP_TEST_PEER=1 \
        /opt/masterplan/deploy/test-deployment.sh internal-prepare-peer "$target"
}

internal_prepare_peer() {
    local target="$1" setup_state="${MP_SETUP_V2_STATE:-$MP_STATE/setup-state-v2.json}"
    require_test_policy
    jq -e --arg target "$target" \
        '.state == "in_progress" and .mode == "ha-join" and .deployment_lane == "unsigned"
         and ((.completed // []) | index("joined") != null)
         and ((.completed // []) | index("application_deployed") == null)
         and .campaign_commit == $target' "$setup_state" >/dev/null \
        || { ui_error "Node B is not in the exact joined pre-activation state."; return 1; }
    mp_lock
    trap 'mp_unlock' EXIT
    prepare_source "$target"
    install -m 0600 /tmp/mp-opt-test-deployment.env "$MP_TEST_ENV"
    rm -f /tmp/mp-opt-test-deployment.env
    sync_operations "$MP_TEST_SOURCE"
    sync_frontend "$MP_TEST_HOME/peer-assets"
    ensure_optional_compose_secret_sources
    mp_prepare_backend_secret_permissions
    mp_compose_init
    mp_compose_validate
    mp_unlock
    trap - EXIT
}

internal_repin_setup() {
    local target="$1" setup_state="${MP_SETUP_V2_STATE:-$MP_STATE/setup-state-v2.json}" pinned temporary
    require_test_policy
    [[ "$target" =~ ^[0-9a-f]{40}$ ]] || return 1
    if jq -e --arg target "$target" \
        '.state == "complete" and .mode == "ha-join" and .deployment_lane == "unsigned"
         and .campaign_commit == $target
         and ((.completed // []) | index("application_deployed") != null)' \
        "$setup_state" >/dev/null 2>&1 \
        && [ "$(jq -r '.current_commit // empty' "$MP_TEST_STATE_FILE" 2>/dev/null || true)" = "$target" ]; then
        return 0
    fi
    pinned="$(jq -r '.campaign_commit // empty' "$setup_state" 2>/dev/null || true)"
    jq -e '.state == "in_progress" and .mode == "ha-join" and .deployment_lane == "unsigned"
           and ((.completed // []) | index("joined") != null)
           and ((.completed // []) | index("application_deployed") == null)' \
        "$setup_state" >/dev/null || return 1
    git -C "$MP_ROOT" fetch --no-tags --force origin "$target" >/dev/null 2>&1 \
        && [ "$(git -C "$MP_ROOT" rev-parse FETCH_HEAD 2>/dev/null || true)" = "$target" ] \
        && git -C "$MP_ROOT" merge-base --is-ancestor "$pinned" "$target" >/dev/null 2>&1 \
        || return 1
    temporary="$(mktemp "$MP_STATE/setup-state.XXXXXX")"
    jq --arg commit "$target" --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '.campaign_commit=$commit | .updated_at=$now | .current_action="Waiting for pinned images from Node A"' \
        "$setup_state" > "$temporary"
    chmod 600 "$temporary"; mv "$temporary" "$setup_state"
}

internal_finalize_peer() {
    local target="$1" setup_state="${MP_SETUP_V2_STATE:-$MP_STATE/setup-state-v2.json}" plan temporary
    require_test_policy
    if jq -e --arg target "$target" \
        '.state == "complete" and .mode == "ha-join" and .campaign_commit == $target
         and ((.completed // []) | index("application_deployed") != null)' \
        "$setup_state" >/dev/null 2>&1 \
        && [ "$(jq -r '.current_commit // empty' "$MP_TEST_STATE_FILE" 2>/dev/null || true)" = "$target" ]; then
        mp_compose_init
        "${MP_COMPOSE[@]}" exec -T backend python -c \
            'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5).read()' >/dev/null
        return 0
    fi
    jq -e --arg target "$target" \
        '.state == "in_progress" and .mode == "ha-join" and .campaign_commit == $target
         and ((.completed // []) | index("joined") != null)' "$setup_state" >/dev/null || return 1
    mp_compose_init
    "${MP_COMPOSE[@]}" exec -T backend python -c \
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5).read()' >/dev/null
    plan="$(jq -n '{base:"",target:"",full:true,migrations:false,components:["backend","frontend","caddy","database","tools","operations"]}')"
    write_state "$target" "" "$plan" ""
    temporary="$(mktemp "$MP_STATE/setup-state.XXXXXX")"
    jq --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '.completed=((.completed+["application_deployed"])|unique) |
         .state="complete" | .completed_at=$now | .updated_at=$now |
         .current_action="Waiting for root administration on Node A"' \
        "$setup_state" > "$temporary"
    chmod 600 "$temporary"; mv "$temporary" "$setup_state"
}

internal_activate() {
    local target="$1" component_token="$2" fresh_commissioning="${3:-false}" plan setup_state temporary previous components
    [[ "$component_token" =~ ^(backend|frontend|caddy|database|tools|operations|witness)(,(backend|frontend|caddy|database|tools|operations|witness))*$ ]] \
        || { ui_error "The peer component set is invalid."; return 1; }
    components="${component_token//,/ }"
    require_test_policy
    [ "$fresh_commissioning" != true ] || require_fresh_commissioning_database "$target"
    mp_lock
    trap 'mp_unlock' EXIT
    if [ "${MP_TEST_INTERNAL_ACTIVATE_REEXEC:-0}" != 1 ]; then
        prepare_source "$target"
        install -m 0600 /tmp/mp-opt-test-deployment.env "$MP_TEST_ENV"
        rm -f /tmp/mp-opt-test-deployment.env
        if grep -qw operations <<< "$components" || grep -qw caddy <<< "$components"; then
            sync_operations "$MP_TEST_SOURCE"
        fi
        if grep -qw frontend <<< "$components"; then
            sync_frontend "$MP_TEST_HOME/peer-assets"
        fi
        # The caller entered through the previously deployed script. After an
        # operations update, continuing in this shell would retain its old
        # function definitions. Re-enter exactly once through the installed
        # target script before activating services.
        mp_unlock
        trap - EXIT
        exec env MP_ROOT="$MP_ROOT" MP_TEST_PEER=1 MP_TEST_INTERNAL_ACTIVATE_REEXEC=1 \
            "$MP_ROOT/deploy/test-deployment.sh" internal-activate \
            "$target" "$component_token" "$fresh_commissioning"
    fi
    [ "$(sed -n 's/^MP_TEST_COMMIT=//p' "$MP_TEST_ENV" | head -1)" = "$target" ] \
        || { ui_error "The peer activation environment does not match the exact target after re-entry."; return 1; }
    compose_activate "$components" "$fresh_commissioning"
    previous="$(jq -r '.current_commit // empty' "$MP_TEST_STATE_FILE" 2>/dev/null || true)"
    plan="$(jq -n --arg components "$components" \
        '{base:"",target:"",full:true,migrations:true,components:($components|split(" "))}')"
    write_state "$target" "" "$plan" ""
    setup_state="${MP_SETUP_V2_STATE:-$MP_STATE/setup-state-v2.json}"
    if [ -s "$setup_state" ]; then
        temporary="$(mktemp "$MP_STATE/setup-state.XXXXXX")"
        jq --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg target "$target" \
            --arg previous "$previous" '
             if .format != "mp-opt-setup-state-v2" or .deployment_lane != "unsigned"
                or (.campaign_commit != $previous and .campaign_commit != $target) then
               error("peer setup pin changed during exact deployment")
             else . end |
             .campaign_commit=$target |
             .completed=((.completed+["application_deployed"])|unique) |
             .state="complete" | .completed_at=$now |
             .current_action="Waiting for root commissioning on Node A" |
             .updated_at=$now | .last_failure=null' \
            "$setup_state" > "$temporary"
        chmod 600 "$temporary"; mv "$temporary" "$setup_state"
    fi
    mp_unlock
    trap - EXIT
}

deploy_witness() {
    local target="$1" tools_image worker before
    [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${MP_TEST_WORKER_NAME:-}" ] || {
        ui_error "Witness changes require CLOUDFLARE_API_TOKEN and MP_TEST_WORKER_NAME through the guarded TUI action."
        return 1
    }
    tools_image="$(sed -n 's/^MP_TOOLS_IMAGE=//p' "$MP_TEST_ENV" | head -1)"
    tools_image="${tools_image:-$(sed -n 's/^MP_TOOLS_IMAGE=//p' "$MP_ROOT/.release.env" | head -1)}"
    before="$(docker run --rm -e CLOUDFLARE_API_TOKEN "$tools_image" deployments status \
        --name "$MP_TEST_WORKER_NAME" --json 2>/dev/null || printf '{}')"
    printf '%s\n' "$before" > "$MP_TEST_STATE_DIR/witness-before.json"
    docker run --rm -e CLOUDFLARE_API_TOKEN \
        -v "$MP_TEST_SOURCE/infra/cloudflare-ha-witness:/worker:ro" "$tools_image" \
        deploy /worker/src/index.ts --config /worker/wrangler.toml \
        --name "$MP_TEST_WORKER_NAME" --tag "test-${target:0:12}" \
        --message "MP-OPT unsigned test ${target}"
}

apply_commit() {
    local target="$1" confirm_full="$2" confirm_migrations="$3" fresh_commissioning="$4"
    local plan previous components snapshot="" automatic=false role component image verified_before=""
    local peer_ready=false pre_pairing=false pre_activation_pair=false holder="" temporary
    local setup_state="${MP_SETUP_V2_STATE:-$MP_STATE/setup-state-v2.json}"
    local -a remote_args
    require_test_policy
    if [ "${MP_TEST_APPLY_REEXEC:-0}" != 1 ]; then
        # Fetch the immutable target with the currently trusted supervisor, then
        # start the deployment anew through that exact target. No locks or
        # service changes have occurred, so the re-entry is side-effect free.
        prepare_source "$target"
        remote_args=(apply "$target")
        [ "$confirm_full" != true ] || remote_args+=(--confirm-full)
        [ "$confirm_migrations" != true ] || remote_args+=(--confirm-migrations)
        [ "$fresh_commissioning" != true ] || remote_args+=(--fresh-commissioning)
        exec env MP_ROOT="$MP_ROOT" MP_TEST_APPLY_REEXEC=1 \
            "$MP_TEST_SOURCE/deploy/test-deployment.sh" "${remote_args[@]}"
    fi
    plan="$(create_plan "$target")"
    if [ "$fresh_commissioning" = true ]; then
        require_fresh_commissioning_database "$target"
        plan="$(jq '
          .full=true | .migrations=true |
          .components=((.components + ["backend","frontend","caddy","database","tools","operations"]) | unique)
        ' <<< "$plan")"
    fi
    previous="$(jq -r '.base' <<< "$plan")"
    components="$(jq -r '.components | join(" ")' <<< "$plan")"
    [ -n "$components" ] || { printf 'Commit already matches the deployed state.\n'; return 0; }
    [ "$(jq -r .full <<< "$plan")" != true ] || [ "$confirm_full" = true ] || {
        ui_error "This is a full test deployment. Re-run with --confirm-full after reviewing plan."
        jq . <<< "$plan"
        return 2
    }
    [ "$(jq -r .migrations <<< "$plan")" != true ] || [ "$confirm_migrations" = true ] || {
        ui_error "Database migrations require --confirm-migrations and an encrypted snapshot."
        return 2
    }
    mp_load_ha_config
    role="$HA_ROLE"
    reconcile_setup_campaign_pin_with_receipt "$previous" "$role"
    if [ "$role" = dynamic ] && [ "${MP_TEST_PEER:-0}" != 1 ]; then
        if ha_pairing_complete; then
            ha_pair_transport_ready || {
                ui_error "HA pairing is recorded, but the verified peer transport is unavailable. No node was updated."
                return 1
            }
            if jq -e --arg previous "$previous" \
                '.state == "in_progress"
                 and ((.completed // []) | index("paired") != null)
                 and ((.completed // []) | index("replicated") == null)
                 and .campaign_commit == $previous' \
                "$setup_state" >/dev/null 2>&1; then
                pre_activation_pair=true
                pre_pairing=true
                for component in backend frontend caddy database tools witness; do
                    if grep -qw "$component" <<< "$components"; then
                        ui_error "Only operations files may advance after pairing and before initial HA replication. No node was updated."
                        return 1
                    fi
                done
            else
                holder="$(jq -r '.holder_node_id // empty' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null || true)"
                if [ -z "$holder" ]; then
                    ui_error "HA pairing is complete, but no current lease observation is available. No node was updated."
                    return 1
                elif [ "$holder" != "$HA_NODE_ID" ]; then
                    remote_args=(apply "$target")
                    [ "$confirm_full" != true ] || remote_args+=(--confirm-full)
                    [ "$confirm_migrations" != true ] || remote_args+=(--confirm-migrations)
                    if grep -qw witness <<< "$components" && [ -n "${CLOUDFLARE_API_TOKEN:-}" ]; then
                        remote_args+=(--cloudflare-worker "${MP_TEST_WORKER_NAME:-}" --cloudflare-token-stdin)
                        printf '%s\n' "$CLOUDFLARE_API_TOKEN" | ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
                            env MP_ROOT=/opt/masterplan /opt/masterplan/deploy/test-deployment.sh "${remote_args[@]}"
                        return
                    fi
                    exec ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
                        env MP_ROOT=/opt/masterplan /opt/masterplan/deploy/test-deployment.sh "${remote_args[@]}"
                else
                    peer_ready=true
                fi
            fi
        else
            pre_pairing=true
            for component in backend frontend caddy database tools; do
                if grep -qw "$component" <<< "$components"; then
                        ui_error "Complete HA pairing before applying an unsigned runtime-component update. No service was changed."
                    return 1
                fi
            done
        fi
        if [ "$peer_ready" = true ] \
            && [ "$(jq -r '.automatic_failover // false' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null || printf false)" = true ]; then
            automatic=true
            python3 "$MP_ROOT/deploy/ha/witness_control.py" automatic disabled >/dev/null
            mp_ha_set_config_value HA_AUTOMATIC_FAILOVER disabled
        fi
    fi
    mp_lock
    trap 'mp_unlock' EXIT
    verified_before="$(jq -r '.current_commit // empty' "$MP_TEST_STATE_FILE" 2>/dev/null || true)"
    set_apply_stage preflight
    trap 'failure_status=$?; set +e; record_apply_failure "$target"; restore_verified_previous_deployment "$verified_before"; exit "$failure_status"' ERR
    if [ "$(jq -r .migrations <<< "$plan")" = true ]; then
        if [ "$fresh_commissioning" != true ]; then
            snapshot="$(MP_MANAGEMENT_LOCK_HELD=1 mp_snapshot_create full "test-deploy-${target:0:12}")"
            [ -n "$snapshot" ]
        fi
    fi
    cp -a "$MP_TEST_ENV" "$MP_TEST_STATE_DIR/previous.env" 2>/dev/null || : > "$MP_TEST_STATE_DIR/previous.env"
    chmod 600 "$MP_TEST_STATE_DIR/previous.env"
    cp -a "$MP_TEST_ENV" "$MP_TEST_STATE_DIR/next.env" 2>/dev/null || : > "$MP_TEST_STATE_DIR/next.env"
    env_set "$MP_TEST_STATE_DIR/next.env" MP_TEST_COMMIT "$target"
    for component in backend caddy database tools; do
        if grep -qw "$component" <<< "$components"; then
            set_apply_stage "build-${component}"
            image="$(build_component "$component" "$target")"
            case "$component" in
                backend) env_set "$MP_TEST_STATE_DIR/next.env" MP_BACKEND_IMAGE "$image" ;;
                caddy) env_set "$MP_TEST_STATE_DIR/next.env" MP_CADDY_IMAGE "$image" ;;
                database) env_set "$MP_TEST_STATE_DIR/next.env" MP_POSTGRES_IMAGE "$image" ;;
                tools) env_set "$MP_TEST_STATE_DIR/next.env" MP_TOOLS_IMAGE "$image" ;;
            esac
            [ "$peer_ready" != true ] || peer_copy_image "$image"
        fi
    done
    if grep -qw frontend <<< "$components"; then
        set_apply_stage build-frontend
        build_frontend
    fi
    set_apply_stage peer-activation
    install -m 0600 "$MP_TEST_STATE_DIR/next.env" "$MP_TEST_ENV"
    if [ "$peer_ready" = true ]; then
        peer_activate "$target" "$components" "$fresh_commissioning"
    fi
    set_apply_stage local-assets
    if grep -qw operations <<< "$components" || grep -qw caddy <<< "$components"; then
        sync_operations "$MP_TEST_SOURCE"
    fi
    if grep -qw frontend <<< "$components"; then
        sync_frontend "$MP_TEST_SOURCE"
    fi
    # A commit may contain HA witness changes while being validated on a
    # standalone server. Keep those sources in the exact checkout, but publish
    # the external Worker only for an active dynamic-HA topology.
    if [ "$role" = dynamic ] && grep -qw witness <<< "$components"; then
        deploy_witness "$target"
    fi
    if [ "$pre_pairing" = true ]; then
        # Node A deliberately keeps serving through its standalone topology
        # until Node B has exchanged trust and the witness has issued a lease.
        # Operations-only fixes are safe to install now, but recreating Caddy
        # would activate the fail-closed HA overlay and make the site return 503.
        env MP_ROOT="$MP_ROOT" bash -c '
            set -Eeuo pipefail
            source "$MP_ROOT/deploy/management/common.sh"
            mp_prepare_backend_secret_permissions
            mp_validate_protected_file_modes
        '
    else
        compose_activate "$components" "$fresh_commissioning"
    fi
    set_apply_stage deployment-receipt
    write_state "$target" "$previous" "$plan" "$snapshot"
    if [ "$pre_activation_pair" = true ]; then
        advance_setup_campaign_pin "$target" "$previous" "Preparing exact images for Node B"
    else
        # Once the exact receipt exists, late-stage failures deliberately keep
        # the healthy activation. Advance the setup pin in the same transaction
        # so replication/readiness can be resumed at this exact target.
        advance_setup_campaign_pin "$target" "$previous"
    fi
    if [ "$peer_ready" = true ]; then
        MP_MANAGEMENT_LOCK_HELD=1 mp_ha_replicate_now
        mp_ha_refresh_witness_observations
        mp_ha_active_verification_readiness
        if [ "$automatic" = true ]; then
            python3 "$MP_ROOT/deploy/ha/witness_control.py" automatic enabled >/dev/null
            mp_ha_set_config_value HA_AUTOMATIC_FAILOVER enabled
        fi
    fi
    mp_audit "deploy.test" "success" "$target"
    rm -f "$MP_TEST_FAILURE_FILE" "$MP_TEST_STAGE_FILE"
    trap - ERR
    printf 'MP-OPT UNSIGNED TEST BUILD DEPLOYED\nCommit: %s\nComponents: %s\nSigned baseline: %s\n' \
        "$target" "$components" "$(sed -n 's/^MP_RELEASE_TAG=//p' "$MP_ROOT/.release.env" | head -1)"
}

restore_signed() {
    local tag role automatic=false
    tag="$(sed -n 's/^MP_RELEASE_TAG=//p' "$MP_ROOT/.release.env" 2>/dev/null | head -1)"
    [ -n "$tag" ] || { ui_error "No signed baseline is recorded."; return 1; }
    mp_load_ha_config
    role="$HA_ROLE"
    if [ "$role" = dynamic ] && [ "${MP_TEST_PEER:-0}" != 1 ]; then
        if [ "$(jq -r '.holder_node_id // empty' "$MP_ROOT/runtime/ha-control.json")" != "$HA_NODE_ID" ]; then
            exec ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
                env MP_ROOT=/opt/masterplan /opt/masterplan/deploy/test-deployment.sh restore-signed
        fi
        if [ "$(jq -r '.automatic_failover // false' "$MP_ROOT/runtime/ha-control.json")" = true ]; then
            automatic=true
            python3 "$MP_ROOT/deploy/ha/witness_control.py" automatic disabled >/dev/null
            mp_ha_set_config_value HA_AUTOMATIC_FAILOVER disabled
        fi
        ssh -T -o BatchMode=yes mp-opt-ha-peer env MP_ROOT=/opt/masterplan MP_TEST_PEER=1 \
            /opt/masterplan/deploy/test-deployment.sh restore-signed || {
                ui_error "Peer baseline restore failed. Automatic failover remains disabled."
                return 1
            }
    fi
    mp_lock
    trap 'mp_unlock' EXIT
    rm -f "$MP_TEST_ENV"
    python3 "$MP_ROOT/deploy/release/install_release.py" --repo-root "$MP_ROOT" --tag "$tag"
    "$MP_ROOT/deploy/deploy.sh" --no-pull
    rm -f "$MP_TEST_STATE_FILE"
    mp_unlock
    trap - EXIT
    if [ "$role" = dynamic ] && [ "${MP_TEST_PEER:-0}" != 1 ]; then
        MP_MANAGEMENT_LOCK_HELD=1 mp_ha_replicate_now \
            || { ui_error "Baseline restored, but replication failed. Automatic failover remains disabled."; return 1; }
        mp_ha_refresh_witness_observations || { ui_error "Baseline restored, but witness refresh failed. Automatic failover remains disabled."; return 1; }
        mp_ha_active_verification_readiness || { ui_error "Baseline restored, but HA readiness did not converge. Automatic failover remains disabled."; return 1; }
        if [ "$automatic" = true ]; then
            python3 "$MP_ROOT/deploy/ha/witness_control.py" automatic enabled >/dev/null
            mp_ha_set_config_value HA_AUTOMATIC_FAILOVER enabled
        fi
    fi
    mp_audit "deploy.test.restore-signed" "success" "$tag"
}

rollback() {
    local previous
    [ -s "$MP_TEST_STATE_FILE" ] || { ui_error "No test deployment can be rolled back."; return 1; }
    previous="$(jq -r '.previous_commit // empty' "$MP_TEST_STATE_FILE")"
    if [ -n "$previous" ] && [ "$previous" != "$(jq -r '.signed_baseline.commit // empty' "$MP_TEST_STATE_FILE")" ]; then
        apply_commit "$previous" true true false
    else
        restore_signed
    fi
}

status() {
    if [ -s "$MP_TEST_STATE_FILE" ]; then
        jq . "$MP_TEST_STATE_FILE"
    else
        jq -n --arg policy "$(policy_value)" \
            --arg tag "$(sed -n 's/^MP_RELEASE_TAG=//p' "$MP_ROOT/.release.env" 2>/dev/null | head -1)" \
            '{format:"mp-opt-test-status-v1",policy:$policy,unsigned:false,signed_baseline:$tag}'
    fi
}

command="${1:-}"
shift || true
case "$command" in
    policy) set_policy "${1:-status}" ;;
    plan) [ "$#" -eq 1 ] || { usage; exit 2; }; create_plan "$1" | jq . ;;
    apply)
        [ "$#" -ge 1 ] || { usage; exit 2; }
        target="$1"; shift
        confirm_full=false; confirm_migrations=false
        cloudflare_worker=""; cloudflare_token_stdin=false; fresh_commissioning=false
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --confirm-full) confirm_full=true ;;
                --confirm-migrations) confirm_migrations=true ;;
                --fresh-commissioning) fresh_commissioning=true ;;
                --cloudflare-worker)
                    shift
                    [ "$#" -gt 0 ] || { usage; exit 2; }
                    cloudflare_worker="$1"
                    ;;
                --cloudflare-token-stdin) cloudflare_token_stdin=true ;;
                '') ;;
                *) usage; exit 2 ;;
            esac
            shift
        done
        if [ "$cloudflare_token_stdin" = true ]; then
            [[ "$cloudflare_worker" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || { ui_error "Invalid Cloudflare Worker name."; exit 2; }
            IFS= read -r CLOUDFLARE_API_TOKEN
            [ "${#CLOUDFLARE_API_TOKEN}" -ge 32 ] || { unset CLOUDFLARE_API_TOKEN; ui_error "Cloudflare token input was incomplete."; exit 2; }
            export CLOUDFLARE_API_TOKEN
            export MP_TEST_WORKER_NAME="$cloudflare_worker"
        elif [ -n "$cloudflare_worker" ]; then
            usage
            exit 2
        fi
        apply_commit "$target" "$confirm_full" "$confirm_migrations" "$fresh_commissioning"
        unset CLOUDFLARE_API_TOKEN MP_TEST_WORKER_NAME
        ;;
    rollback) [ "$#" -eq 0 ] || { usage; exit 2; }; rollback ;;
    restore-signed) [ "$#" -eq 0 ] || { usage; exit 2; }; restore_signed ;;
    status) [ "$#" -eq 0 ] || { usage; exit 2; }; status ;;
    prepare-peer) [ "$#" -eq 1 ] || exit 2; prepare_initial_peer "$1" ;;
    internal-prepare-peer) [ "$#" -eq 1 ] || exit 2; internal_prepare_peer "$1" ;;
    internal-repin-setup) [ "$#" -eq 1 ] || exit 2; internal_repin_setup "$1" ;;
    internal-finalize-peer) [ "$#" -eq 1 ] || exit 2; internal_finalize_peer "$1" ;;
    internal-activate) [ "$#" -eq 2 ] || [ "$#" -eq 3 ] || exit 2; internal_activate "$1" "$2" "${3:-false}" ;;
    help|-h|--help|'') usage ;;
    *) usage; exit 2 ;;
esac
