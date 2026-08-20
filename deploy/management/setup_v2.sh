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
MP_SETUP_V2_EVENTS="${MP_SETUP_V2_EVENTS:-$MP_STATE/setup-events-v1.jsonl}"
MP_SETUP_V2_EXECUTION_LOCK="${MP_SETUP_V2_EXECUTION_LOCK:-${MP_SETUP_EXECUTION_LOCK:-$MP_STATE/setup-execution.lock}}"
MP_SETUP_V2_EXECUTION_STATE="${MP_SETUP_V2_EXECUTION_STATE:-${MP_SETUP_EXECUTION_STATE:-$MP_STATE/setup-execution.json}}"
MP_SETUP_EXECUTION_LOCK="${MP_SETUP_EXECUTION_LOCK:-$MP_SETUP_V2_EXECUTION_LOCK}"
MP_SETUP_EXECUTION_STATE="${MP_SETUP_EXECUTION_STATE:-$MP_SETUP_V2_EXECUTION_STATE}"
MP_SETUP_V2_CANCEL_REQUEST="${MP_SETUP_V2_CANCEL_REQUEST:-$MP_STATE/setup-cancel-request.json}"
MP_SETUP_V2_ARTIFACTS="${MP_SETUP_V2_ARTIFACTS:-$MP_STATE/setup-artifacts}"
MP_SETUP_V2_FULL_LOSS_AUTH="${MP_SETUP_V2_FULL_LOSS_AUTH:-$MP_STATE/setup-full-loss-authorization.json}"
MP_SETUP_V2_PROVIDER_RESOURCE="${MP_SETUP_V2_PROVIDER_RESOURCE:-$MP_STATE/cloudflare-provider-resource.json}"

mp_setup_record_cloudflare_resource() {
    local cluster="$1" account="$2" worker="$3" witness="$4" zone="$5" domain="$6" temporary host
    [[ "$cluster" =~ ^mp-opt-[0-9a-f-]{36}$ ]] || return 65
    [[ "$account" =~ ^[0-9a-f]{32}$ ]] || return 65
    [[ "$worker" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || return 65
    [[ "$witness" =~ ^https://[^[:space:]]+\.workers\.dev/?$ ]] || return 65
    [[ "$zone" =~ ^[A-Za-z0-9_-]{8,128}$ ]] || return 65
    mp_validate_hostname "$domain" || return 65
    host="${witness#https://}"; host="${host%/}"
    [ "${host%%.*}" = "$worker" ] || return 65
    if [ -e "$MP_SETUP_V2_PROVIDER_RESOURCE" ] || [ -L "$MP_SETUP_V2_PROVIDER_RESOURCE" ]; then
        [ -f "$MP_SETUP_V2_PROVIDER_RESOURCE" ] && [ ! -L "$MP_SETUP_V2_PROVIDER_RESOURCE" ] \
            && [ "$(stat -c '%u:%a' "$MP_SETUP_V2_PROVIDER_RESOURCE" 2>/dev/null)" = "$(id -u):600" ] \
            || return 77
        jq -e --arg cluster "$cluster" --arg account "$account" --arg worker "$worker" --arg witness "${witness%/}" \
            --arg zone "$zone" --arg domain "$domain" '
            .format == "mp-opt-cloudflare-provider-resource-v1"
            and .cluster_id == $cluster and .account_id == $account and .worker_name == $worker
            and .witness_url == $witness and .zone_id == $zone and .domain == $domain
        ' "$MP_SETUP_V2_PROVIDER_RESOURCE" >/dev/null 2>&1 || return 65
        return 0
    fi
    temporary="$(mktemp "$MP_STATE/cloudflare-provider-resource.XXXXXX")" || return 1
    jq -n --arg cluster "$cluster" --arg account "$account" --arg worker "$worker" --arg witness "${witness%/}" \
        --arg zone "$zone" --arg domain "$domain" \
        --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
        {format:"mp-opt-cloudflare-provider-resource-v1",cluster_id:$cluster,account_id:$account,
         worker_name:$worker,witness_url:$witness,zone_id:$zone,domain:$domain,
         recorded_at:$at}
    ' > "$temporary" \
        && chmod 600 "$temporary" && sync -f "$temporary" 2>/dev/null \
        && mv "$temporary" "$MP_SETUP_V2_PROVIDER_RESOURCE" \
        && sync -f "$MP_STATE" 2>/dev/null \
        || { rm -f "$temporary"; return 1; }
}

mp_setup_load_cloudflare_resource() {
    local cluster="$1" witness="$2" domain="$3"
    [ -f "$MP_SETUP_V2_PROVIDER_RESOURCE" ] && [ ! -L "$MP_SETUP_V2_PROVIDER_RESOURCE" ] \
        && [ "$(stat -c '%u:%a' "$MP_SETUP_V2_PROVIDER_RESOURCE" 2>/dev/null)" = "$(id -u):600" ] \
        || return 65
    jq -e --arg cluster "$cluster" --arg witness "${witness%/}" --arg domain "$domain" '
        .format == "mp-opt-cloudflare-provider-resource-v1"
        and .cluster_id == $cluster and .witness_url == $witness and .domain == $domain
        and (.account_id | test("^[0-9a-f]{32}$"))
        and (.worker_name | test("^[a-z0-9][a-z0-9-]{0,62}$"))
        and (.zone_id | test("^[A-Za-z0-9_-]{8,128}$"))
        and (.recorded_at | type == "string" and length > 0)
    ' "$MP_SETUP_V2_PROVIDER_RESOURCE" >/dev/null 2>&1 || return 65
    [ "$(jq -r .worker_name "$MP_SETUP_V2_PROVIDER_RESOURCE")" \
        = "$(printf '%s' "${witness#https://}" | cut -d. -f1)" ] || return 65
}

mp_setup_validate_full_loss_restore_authorization() {
    local snapshot_path="$1" authorization_id="$2" required_state="${3:-any}"
    local receipt_hash recipient setup_started
    [ -f "$MP_SETUP_V2_FULL_LOSS_AUTH" ] && [ ! -L "$MP_SETUP_V2_FULL_LOSS_AUTH" ] \
        && [ "$(stat -c '%u:%a' "$MP_SETUP_V2_FULL_LOSS_AUTH" 2>/dev/null)" = "$(id -u):600" ] \
        || return 65
    jq -e '.mode == "full-restore" and .state == "in_progress"
        and ((.completed // []) | index("imported") != null)' \
        "$MP_SETUP_V2_STATE" >/dev/null 2>&1 || return 65
    [ -f "$snapshot_path/receipt.json" ] && [ ! -L "$snapshot_path/receipt.json" ] || return 65
    receipt_hash="$(sha256sum "$snapshot_path/receipt.json" | awk '{print $1}')" || return 1
    recipient="$(jq -er '.encryption.recipient | select(test("^age1[0-9a-z]+$"))' \
        "$snapshot_path/receipt.json")" || return 65
    setup_started="$(jq -r '.started_at // empty' "$MP_SETUP_V2_STATE")"
    jq -e --arg authorization "$authorization_id" --arg receipt "$receipt_hash" \
        --arg recipient "$recipient" --arg started "$setup_started" --arg required "$required_state" '
        .format == "mp-opt-full-loss-authorization-v1"
        and .authorization_id == $authorization
        and .setup_started_at == $started
        and .snapshot_receipt_sha256 == $receipt
        and .recovery_recipient == $recipient
        and (.state | IN("authorized","installing"))
        and (if $required == "installing" then .state == "installing" else true end)
    ' "$MP_SETUP_V2_FULL_LOSS_AUTH" >/dev/null 2>&1
}

mp_setup_prepare_full_loss_restore_authorization() {
    local snapshot_path="$1" receipt_hash recipient setup_started authorization temporary
    if [ -e "$MP_SETUP_V2_FULL_LOSS_AUTH" ] || [ -L "$MP_SETUP_V2_FULL_LOSS_AUTH" ]; then
        authorization="$(jq -r '.authorization_id // empty' "$MP_SETUP_V2_FULL_LOSS_AUTH" 2>/dev/null || true)"
        mp_setup_validate_full_loss_restore_authorization "$snapshot_path" "$authorization" any \
            || return 65
        printf '%s\n' "$authorization"
        return 0
    fi
    mp_snapshot_full_loss_host_is_blank || return 65
    receipt_hash="$(sha256sum "$snapshot_path/receipt.json" | awk '{print $1}')" || return 1
    recipient="$(jq -er '.encryption.recipient | select(test("^age1[0-9a-z]+$"))' \
        "$snapshot_path/receipt.json")" || return 65
    setup_started="$(jq -r '.started_at // empty' "$MP_SETUP_V2_STATE")"
    [ -n "$setup_started" ] || return 65
    authorization="$(cat /proc/sys/kernel/random/uuid)"
    temporary="$(mktemp "$MP_STATE/setup-full-loss-authorization.XXXXXX")" || return 1
    jq -n --arg authorization "$authorization" --arg receipt "$receipt_hash" \
        --arg recipient "$recipient" --arg started "$setup_started" \
        --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
        {format:"mp-opt-full-loss-authorization-v1",authorization_id:$authorization,
         setup_started_at:$started,snapshot_receipt_sha256:$receipt,
         recovery_recipient:$recipient,state:"authorized",blank_verified_at:$at}
    ' > "$temporary" \
        && chmod 600 "$temporary" && sync -f "$temporary" 2>/dev/null \
        && mv "$temporary" "$MP_SETUP_V2_FULL_LOSS_AUTH" \
        && sync -f "$MP_STATE" 2>/dev/null \
        || { rm -f "$temporary"; return 1; }
    printf '%s\n' "$authorization"
}

mp_setup_mark_full_loss_restore_started() {
    local snapshot_path="$1" authorization_id="$2" temporary
    mp_setup_validate_full_loss_restore_authorization \
        "$snapshot_path" "$authorization_id" any || return 65
    if [ "$(jq -r .state "$MP_SETUP_V2_FULL_LOSS_AUTH")" = installing ]; then
        mp_snapshot_full_loss_host_is_blank
        return $?
    fi
    mp_snapshot_full_loss_host_is_blank || return 65
    temporary="$(mktemp "$MP_STATE/setup-full-loss-authorization.XXXXXX")" || return 1
    jq --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '.state="installing" | .installation_started_at=$at' \
        "$MP_SETUP_V2_FULL_LOSS_AUTH" > "$temporary" \
        && chmod 600 "$temporary" && sync -f "$temporary" 2>/dev/null \
        && mv "$temporary" "$MP_SETUP_V2_FULL_LOSS_AUTH" \
        && sync -f "$MP_STATE" 2>/dev/null \
        || { rm -f "$temporary"; return 1; }
}

# One host-local lease serialises the graphical TUI and the private test
# coordinator. The metadata is diagnostic only; flock remains authoritative.
mp_setup_execution_acquire() {
    local run_id="${1:-tui-$$}" command_name="${2:-commissioning}" temporary
    [[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$ ]] || return 64
    [[ "$command_name" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]] || return 64
    mp_prepare_private_lock_file "$MP_SETUP_EXECUTION_LOCK" || return 1
    exec 8>"$MP_SETUP_V2_EXECUTION_LOCK" || return 1
    if ! flock -n 8; then
        exec 8>&-
        return 75
    fi
    temporary="$(mktemp "$MP_STATE/setup-execution.XXXXXX")" || {
        flock -u 8 >/dev/null 2>&1 || true; exec 8>&-; return 1;
    }
    jq -n --arg run "$run_id" --arg command "$command_name" \
        --arg started "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --argjson pid "$$" --argjson uid "$(id -u)" \
        '{format:"mp-opt-setup-execution-v1",run_id:$run,command:$command,
          pid:$pid,uid:$uid,started_at:$started}' > "$temporary" \
        && chmod 600 "$temporary" && mv "$temporary" "$MP_SETUP_V2_EXECUTION_STATE" \
        || { rm -f "$temporary"; flock -u 8 >/dev/null 2>&1 || true; exec 8>&-; return 1; }
    export MP_SETUP_RUN_ID="$run_id" MP_SETUP_EXECUTION_LEASE_HELD=1
}

mp_setup_execution_release() {
    if [ "${MP_SETUP_EXECUTION_LEASE_HELD:-0}" = 1 ]; then
        rm -f "$MP_SETUP_V2_EXECUTION_STATE"
        flock -u 8 >/dev/null 2>&1 || true
        exec 8>&- 2>/dev/null || true
        unset MP_SETUP_EXECUTION_LEASE_HELD
    fi
}

# Report execution metadata only while the kernel lease is actually held.
# A killed process releases flock automatically; the next observer then
# removes its stale diagnostic metadata before reporting the coordinator idle.
mp_setup_execution_observe() {
    local metadata=null
    mp_prepare_private_lock_file "$MP_SETUP_EXECUTION_LOCK" || return 1
    exec 9>"$MP_SETUP_V2_EXECUTION_LOCK" || return 1
    if flock -n 9; then
        rm -f "$MP_SETUP_V2_EXECUTION_STATE"
        flock -u 9 >/dev/null 2>&1 || true; exec 9>&-
        jq -cn '{active:false,metadata:null}'
        return 0
    fi
    exec 9>&-
    if [ -s "$MP_SETUP_V2_EXECUTION_STATE" ]; then
        metadata="$(jq -c 'select(.format=="mp-opt-setup-execution-v1")' \
            "$MP_SETUP_V2_EXECUTION_STATE" 2>/dev/null || printf null)"
    fi
    jq -cn --argjson metadata "$metadata" '{active:true,metadata:$metadata}'
}

mp_setup_cancellation_requested() {
    [ -s "$MP_SETUP_V2_CANCEL_REQUEST" ] || return 1
    [ -f "$MP_SETUP_V2_CANCEL_REQUEST" ] && [ ! -L "$MP_SETUP_V2_CANCEL_REQUEST" ] \
        && [ "$(stat -c '%u:%a' "$MP_SETUP_V2_CANCEL_REQUEST" 2>/dev/null)" = "$(id -u):600" ] \
        && jq -e '.format == "mp-opt-setup-cancel-v1" and (.requested_at | type == "string")' \
            "$MP_SETUP_V2_CANCEL_REQUEST" >/dev/null 2>&1
}

# Cancellation is cooperative: destructive or non-atomic host operations are
# never killed halfway through. Long-running wait loops consume the request at
# their next safe boundary and leave the resumable setup state intact.
mp_setup_consume_cancellation() {
    mp_setup_cancellation_requested || return 1
    rm -f "$MP_SETUP_V2_CANCEL_REQUEST" || return 1
    if [ -s "$MP_SETUP_V2_STATE" ]; then
        mp_setup_state_update \
            '.current_action="Commissioning paused by operator"
             | .current_action_code="SETUP_CANCELLED"
             | .current_checkpoint=null | .action_started_at=null
             | .last_failure={code:"SETUP_CANCELLED",
                 message:"The current automation run was cancelled at a safe boundary. Commissioning remains resumable.",
                 at:$now,action_code:"SETUP_CANCELLED",checkpoint:null}' \
            || return 1
        mp_setup_journal_event execution.cancelled || return 1
    fi
    return 0
}

# Append safe progress telemetry. The setup state and signed receipts remain
# authoritative; a missing journal entry after power loss is repaired by the
# next status snapshot rather than guessed.
mp_setup_journal_event() {
    mp_append_setup_event "$1" "$MP_SETUP_V2_STATE" "$MP_SETUP_V2_EVENTS" \
        "${MP_SETUP_RUN_ID:-tui-$$}"
}

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
    local mode="$1" temporary lane="" policy commit="" receipt="" pinned="" checkout="" previous=""
    local fast_forward=false
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
                git -C "$MP_ROOT" fetch --no-tags --force origin "$receipt" >/dev/null 2>&1 \
                    && [ "$(git -C "$MP_ROOT" rev-parse FETCH_HEAD 2>/dev/null || true)" = "$receipt" ] \
                    || { ui_error "The active exact deployment receipt is not available from origin. Push that exact commit before resuming commissioning."; return 1; }
                checkout="$(git -C "$MP_ROOT" rev-parse HEAD 2>/dev/null || true)"
                previous="$(jq -r '.previous_commit // empty' \
                    "$MP_STATE/test-deployments/current.json" 2>/dev/null || true)"
                if [[ "$pinned" =~ ^[0-9a-f]{40}$ ]] \
                    && git -C "$MP_ROOT" merge-base --is-ancestor "$pinned" "$receipt" >/dev/null 2>&1; then
                    fast_forward=true
                fi
                if { [ "$pinned" != "$checkout" ] && [ "$pinned" != "$previous" ] \
                        && [ "$fast_forward" != true ]; } \
                    || jq -e '.completed | index("witness_bootstrap") != null' \
                        "$MP_SETUP_V2_STATE" >/dev/null 2>&1 \
                    || [ -s "$MP_SETUP_V2_PENDING_JOIN" ]; then
                    ui_error "The unsigned commissioning pin does not match the active exact deployment receipt. Pairing has stopped rather than changing an established campaign target."
                    return 1
                fi
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
          current_action_code:"SIGNED_BASELINE_VERIFY",current_checkpoint:"signed_baseline_verified",
          action_started_at:$now,last_completed_action:null,
          last_failure:null,started_at:$now,updated_at:$now}' \
        > "$temporary" || { rm -f "$temporary"; return 1; }
    chmod 600 "$temporary"
    sync -f "$temporary" 2>/dev/null || { rm -f "$temporary"; return 1; }
    mv "$temporary" "$MP_SETUP_V2_STATE"
    sync -f "$(dirname "$MP_SETUP_V2_STATE")" 2>/dev/null || return 1
    rm -f "$MP_SETUP_V2_CANCEL_REQUEST"
    mp_setup_journal_event workflow.started
}

mp_setup_state_update() {
    local filter="$1" temporary
    shift
    temporary="$(mktemp "$MP_STATE/setup-state.XXXXXX")" || return 1
    jq "$@" --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "$filter | .updated_at=\$now" "$MP_SETUP_V2_STATE" > "$temporary" \
        || { rm -f "$temporary"; return 1; }
    chmod 600 "$temporary"
    sync -f "$temporary" 2>/dev/null || { rm -f "$temporary"; return 1; }
    mv "$temporary" "$MP_SETUP_V2_STATE"
    sync -f "$(dirname "$MP_SETUP_V2_STATE")" 2>/dev/null || return 1
}

mp_setup_state_action() {
    local action="$1" code="${2:-SETUP_ACTION}" checkpoint="${3:-}"
    mp_setup_state_update \
        '.current_action=$action | .current_action_code=$code
         | .current_checkpoint=(if $checkpoint == "" then null else $checkpoint end)
         | .action_started_at=$now | .last_failure=null' \
        --arg action "$action" --arg code "$code" --arg checkpoint "$checkpoint" \
        && mp_setup_journal_event checkpoint.started
}

mp_setup_state_failure() {
    local code="$1" message="${2:0:400}"
    mp_setup_state_update \
        '.last_failure={code:$code,message:$message,at:$now,
          action_code:(.current_action_code // "SETUP_ACTION"),
          checkpoint:(.current_checkpoint // null)}' \
        --arg code "$code" --arg message "$message" \
        && mp_setup_journal_event checkpoint.failed
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
        --arg tag "$tag" --arg commit "$commit" \
        && mp_setup_journal_event checkpoint.completed
}

mp_setup_state_has() {
    jq -e --arg step "$1" '.completed | index($step) != null' \
        "$MP_SETUP_V2_STATE" >/dev/null 2>&1
}

mp_setup_state_mark_now() {
    mp_setup_state_has "$1" && return 0
    if [ "${MP_SETUP_MACHINE_CHECKPOINT:-}" = "$1" ] \
        && [ -n "${MP_SETUP_MACHINE_IDEMPOTENCY_KEY:-}" ]; then
        mp_setup_state_update \
            'if .machine_transitions[$step].idempotency_key != $key then error("machine transition mismatch") else . end
             | .completed=((.completed + [$step]) | unique)
             | .last_completed_action={checkpoint:$step,
                 action_code:(.current_action_code // "SETUP_ACTION"),
                 label:(.current_action // "Commissioning step"),completed_at:$now}
             | .machine_transitions[$step].state="completed"
             | .machine_transitions[$step].completed_at=$now
             | .current_action="Reconciling the next commissioning step"
             | .current_action_code="SETUP_RECONCILING" | .current_checkpoint=null
             | .action_started_at=null | .last_failure=null' --arg step "$1" \
            --arg key "$MP_SETUP_MACHINE_IDEMPOTENCY_KEY" \
            && mp_setup_journal_event checkpoint.completed
    else
        mp_setup_state_update \
            '.completed=((.completed + [$step]) | unique)
             | .last_completed_action={checkpoint:$step,
                 action_code:(.current_action_code // "SETUP_ACTION"),
                 label:(.current_action // "Commissioning step"),completed_at:$now}
             | .current_action="Reconciling the next commissioning step"
             | .current_action_code="SETUP_RECONCILING" | .current_checkpoint=null
             | .action_started_at=null | .last_failure=null' --arg step "$1" \
            && mp_setup_journal_event checkpoint.completed
    fi
}

mp_setup_state_mark() {
    # The candidate-only fault harness defers only the current machine
    # checkpoint.  This lets the real side effect return before a durable test
    # receipt and the normal setup checkpoint are written in distinct windows.
    if [ -n "${MP_SETUP_TEST_DEFER_CHECKPOINT:-}" ] \
        && [ "$1" = "$MP_SETUP_TEST_DEFER_CHECKPOINT" ]; then
        MP_SETUP_TEST_MARK_REQUESTED=true
        return 0
    fi
    mp_setup_state_mark_now "$1"
}

mp_setup_state_complete() {
    mp_setup_state_update \
        '.state="complete" | .completed_at=$now
         | .current_action=null | .current_action_code=null
         | .current_checkpoint=null | .action_started_at=null
         | .last_failure=null' \
        && mp_setup_journal_event workflow.completed
}

mp_setup_state_clear_completed() {
    [ -f "$MP_SETUP_V2_STATE" ] || return 0
    [ "$(jq -r '.state // empty' "$MP_SETUP_V2_STATE")" = complete ] || return 0
    rm -f "$MP_SETUP_V2_STATE" "$MP_SETUP_V2_PENDING_JOIN" \
        "$MP_SETUP_V2_PENDING_BOOTSTRAP" "$MP_SETUP_V2_PENDING_LOCAL_JOIN" \
        "$MP_SETUP_V2_PENDING_REPLACEMENT" "$MP_SETUP_V2_IMPORT_RECEIPT" \
        "$MP_SETUP_V2_FULL_LOSS_AUTH"
}

mp_setup_machine_identity_file() {
    local identity="$1" temporary recipient identity_prefix suffix
    # Keep the private identity format out of all display-oriented setup
    # source and output. Construct the well-known prefix only at validation.
    identity_prefix="$(printf 'AGE-%s-KEY-1' SECRET)"
    suffix="${identity#"$identity_prefix"}"
    [ "${#identity}" -ge 40 ] && [ "${#identity}" -le 4096 ] \
        && [ "$identity" != "$suffix" ] && [[ "$suffix" =~ ^[0-9A-Z]+$ ]] || return 65
    temporary="$(mktemp "$MP_STATE/setup-recovery-identity.XXXXXX")" || return 1
    chmod 600 "$temporary" || { rm -f "$temporary"; return 1; }
    printf '%s\n' "$identity" > "$temporary" || { rm -f "$temporary"; return 1; }
    recipient="$(mp_identity_recipient "$temporary" 2>/dev/null || true)"
    [[ "$recipient" =~ ^age1[0-9a-z]+$ ]] \
        || { mp_secure_remove_file "$temporary"; return 65; }
    printf '%s\n' "$temporary"
}

# Create and deeply verify the conversion guard snapshot, then stage one
# encrypted portable package behind an opaque ticket. The private identity is
# used only from the bounded stdin document and is securely removed before the
# safe receipt is returned. Downloading the package is a separate raw-artifact
# operation; the conversion checkpoint is not completed until its digest is
# confirmed back through advance.
mp_setup_machine_stage_migration_snapshot() {
    local input_file="$1" existing ticket directory snapshot identity recipient
    local output package package_id package_hash package_size receipt temporary
    jq -e '.format == "mp-opt-migration-snapshot-input-v1"
        and ((keys | sort) == ["format","recovery_identity"])
        and (.recovery_identity | type == "string" and length >= 40 and length <= 4096)' \
        "$input_file" >/dev/null 2>&1 || return 64
    jq -e '.state == "in_progress" and .mode == "convert-ha"
        and ((.completed // []) | index("migration_snapshot") == null)' \
        "$MP_SETUP_V2_STATE" >/dev/null 2>&1 || return 65
    existing="$(jq -r '.pending_artifacts.migration_snapshot.ticket // empty' \
        "$MP_SETUP_V2_STATE")"
    if [[ "$existing" =~ ^[0-9a-f-]{36}$ ]] \
        && [ -s "$MP_SETUP_V2_ARTIFACTS/$existing/receipt.json" ]; then
        jq '{format:"mp-opt-migration-snapshot-stage-v1",ok:true,
            ticket,sha256:.package_sha256,size:.package_size,
            snapshot_receipt_sha256,resumed:true,exit_code:0}' \
            "$MP_SETUP_V2_ARTIFACTS/$existing/receipt.json"
        return 0
    fi
    identity="$(mp_setup_machine_identity_file "$(jq -r .recovery_identity "$input_file")")" \
        || return $?
    recipient="$(mp_identity_recipient "$identity")" || {
        mp_secure_remove_file "$identity"; return 65;
    }
    [ "$recipient" = "$(mp_recovery_recipient 2>/dev/null || true)" ] || {
        mp_secure_remove_file "$identity"; return 65;
    }
    snapshot="$(mp_snapshot_create full "single-to-ha-machine-$(date -u +%Y%m%dT%H%M%SZ)")" \
        || { mp_secure_remove_file "$identity"; return 1; }
    mp_snapshot_verify_path "$snapshot" "$identity" \
        || { mp_secure_remove_file "$identity"; return 1; }
    mp_secure_remove_file "$identity"
    ticket="$(cat /proc/sys/kernel/random/uuid)" || return 1
    directory="$MP_SETUP_V2_ARTIFACTS/$ticket"
    mkdir -p "$MP_SETUP_V2_ARTIFACTS" && chmod 700 "$MP_SETUP_V2_ARTIFACTS" \
        && mkdir -m 0700 "$directory" || return 1
    package="$directory/migration.mpopt-snapshot"
    output="$(python3 "$MP_PORTABLE_TOOL" export --snapshot "$snapshot" \
        --output "$package" --source-node "$(mp_ha_role 2>/dev/null || printf standalone)")" \
        || { rm -rf "$directory"; return 1; }
    package_id="$(jq -er .package_id <<< "$output")" || { rm -rf "$directory"; return 1; }
    package_hash="$(jq -er '.sha256 | select(test("^[0-9a-f]{64}$"))' <<< "$output")" \
        || { rm -rf "$directory"; return 1; }
    package_size="$(jq -er '.size | select(type == "number" and . >= 1)' <<< "$output")" \
        || { rm -rf "$directory"; return 1; }
    [ "$(sha256sum "$package" | awk '{print $1}')" = "$package_hash" ] \
        || { rm -rf "$directory"; return 1; }
    chmod 600 "$package"
    receipt="$directory/receipt.json"; temporary="$(mktemp "$directory/.receipt.XXXXXX")" || return 1
    jq -n --arg ticket "$ticket" --arg path "$package" --arg snapshot "$snapshot" \
        --arg package_id "$package_id" --arg package_hash "$package_hash" \
        --arg snapshot_hash "$(sha256sum "$snapshot/receipt.json" | awk '{print $1}')" \
        --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson size "$package_size" \
        '{format:"mp-opt-machine-artifact-v1",kind:"migration-snapshot",ticket:$ticket,
          package_path:$path,package_id:$package_id,package_sha256:$package_hash,
          package_size:$size,snapshot_path:$snapshot,snapshot_receipt_sha256:$snapshot_hash,
          created_at:$created_at}' > "$temporary" \
        && chmod 600 "$temporary" && mv "$temporary" "$receipt" \
        || { rm -f "$temporary"; rm -rf "$directory"; return 1; }
    sync -f "$package" 2>/dev/null && sync -f "$receipt" 2>/dev/null \
        && sync -f "$directory" 2>/dev/null || return 1
    mp_setup_state_update \
        '.pending_artifacts.migration_snapshot={ticket:$ticket,sha256:$sha,size:$size}' \
        --arg ticket "$ticket" --arg sha "$package_hash" --argjson size "$package_size" || return 1
    jq -cn --arg ticket "$ticket" --arg sha "$package_hash" --arg snap "$(sha256sum "$snapshot/receipt.json" | awk '{print $1}')" \
        --argjson size "$package_size" \
        '{format:"mp-opt-migration-snapshot-stage-v1",ok:true,ticket:$ticket,
          sha256:$sha,size:$size,snapshot_receipt_sha256:$snap,resumed:false,exit_code:0}'
}

mp_setup_machine_import_recovery_package() {
    local package="$1" expected_sha="$2" actual result status name package_hash existing_snapshot
    local imported_path state_tmp
    [[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || return 64
    jq -e '.state == "in_progress" and .mode == "full-restore"
        and ((.completed // []) | index("imported") == null)' \
        "$MP_SETUP_V2_STATE" >/dev/null 2>&1 || return 65
    if [ "$(jq -r '.pending_artifacts.recovery_import.sha256 // empty' \
            "$MP_SETUP_V2_STATE")" = "$expected_sha" ] \
        && jq -e --arg sha "$expected_sha" \
            '.format == "mp-opt-portable-import-receipt-v1"
             and .package_sha256 == $sha
             and (.snapshot_path | type == "string")' \
            "$MP_PORTABLE_LAST_IMPORT_STATE" >/dev/null 2>&1; then
        existing_snapshot="$(jq -r .snapshot_path "$MP_PORTABLE_LAST_IMPORT_STATE")"
        case "$(readlink -f "$existing_snapshot" 2>/dev/null || true)" in
            "$(readlink -f "$MP_SNAPSHOTS")"/*) ;;
            *) return 65 ;;
        esac
        [ -d "$existing_snapshot" ] && [ -f "$existing_snapshot/receipt.json" ] \
            && [ ! -L "$existing_snapshot/receipt.json" ] || return 65
        jq -cn --arg sha "$expected_sha" --arg snapshot_receipt_sha256 \
            "$(sha256sum "$existing_snapshot/receipt.json" | awk '{print $1}')" \
            '{format:"mp-opt-recovery-package-stage-v1",ok:true,sha256:$sha,
              snapshot_receipt_sha256:$snapshot_receipt_sha256,resumed:true,exit_code:0}'
        return 0
    fi
    actual="$(sha256sum "$package" | awk '{print $1}')" || return 1
    [ "$actual" = "$expected_sha" ] || return 65
    mp_portable_initialise || return 1
    result="$(python3 "$MP_PORTABLE_TOOL" import --package "$package" \
        --snapshots "$MP_SNAPSHOTS" --expected-sha256 "$expected_sha")" || return 1
    status="$(jq -er .status <<< "$result")" || return 1
    name="$(jq -er .snapshot_directory <<< "$result")" || return 1
    package_hash="$(jq -er .package_sha256 <<< "$result")" || return 1
    imported_path="$MP_SNAPSHOTS/$name"
    [ -d "$imported_path" ] && [ "$package_hash" = "$expected_sha" ] || return 1
    state_tmp="$(mktemp "$MP_STATE/portable-last-import.XXXXXX")" || return 1
    jq -n --arg snapshot "$name" --arg path "$imported_path" \
        --arg package_hash "$package_hash" --arg status "$status" \
        --arg imported_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '{format:"mp-opt-portable-import-receipt-v1",snapshot:$snapshot,
          snapshot_path:$path,package_sha256:$package_hash,status:$status,
          imported_at:$imported_at}' > "$state_tmp" \
        && chmod 600 "$state_tmp" && mv "$state_tmp" "$MP_PORTABLE_LAST_IMPORT_STATE" \
        || { rm -f "$state_tmp"; return 1; }
    mp_setup_state_update \
        '.pending_artifacts.recovery_import={sha256:$sha,snapshot:$snapshot}' \
        --arg sha "$package_hash" --arg snapshot "$name" || return 1
    jq -cn --arg sha "$package_hash" --arg snapshot_receipt_sha256 \
        "$(sha256sum "$imported_path/receipt.json" | awk '{print $1}')" \
        '{format:"mp-opt-recovery-package-stage-v1",ok:true,sha256:$sha,
          snapshot_receipt_sha256:$snapshot_receipt_sha256,resumed:false,exit_code:0}'
}

mp_setup_machine_open_replacement() {
    local pair body token response pending replacement_tmp lane campaign
    mp_load_ha_config || return 1
    [ "$HA_ROLE" = dynamic ] || return 65
    mp_require_active_or_standalone || return 65
    [ "$(jq -r '.automatic_failover // false' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null)" = false ] \
        || return 65
    if [ ! -s "$MP_SETUP_V2_PENDING_REPLACEMENT" ]; then
        replacement_tmp="$(mktemp "$MP_STATE/pending-replacement-request.XXXXXX")" || return 1
        jq -n --arg pair "$(mp_random_secret)" --arg target "$HA_PEER_NODE_ID" \
            '{format:"mp-opt-pending-replacement-v1",pairing_secret:$pair,target_node_id:$target}' \
            > "$replacement_tmp" \
            && chmod 600 "$replacement_tmp" \
            && mv "$replacement_tmp" "$MP_SETUP_V2_PENDING_REPLACEMENT" \
            || { rm -f "$replacement_tmp"; return 1; }
    fi
    jq -e --arg target "$HA_PEER_NODE_ID" \
        '.format == "mp-opt-pending-replacement-v1" and .target_node_id == $target' \
        "$MP_SETUP_V2_PENDING_REPLACEMENT" >/dev/null || return 65
    pair="$(jq -r .pairing_secret "$MP_SETUP_V2_PENDING_REPLACEMENT")"
    token="$(cat "$MP_HA_HOME/secrets/node_token")" || return 1
    body="$(mktemp "$MP_STATE/pair-open.XXXXXX")" || return 1
    jq -n --arg node "$HA_NODE_ID" --arg target "$HA_PEER_NODE_ID" --arg pair "$pair" \
        '{node_id:$node,target_node_id:$target,pairing_secret:$pair}' > "$body" || return 1
    response="$(mp_setup_witness_call pair-open "$HA_WITNESS_URL" "$HA_CLUSTER_ID" "$token" "$body")" \
        || { rm -f "$body"; unset pair token; return 1; }
    rm -f "$body"; unset token
    jq -e '.pairing_open == true' <<< "$response" >/dev/null 2>&1 || return 1
    lane="$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")"
    campaign="$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")"
    pending="$(mktemp "$MP_STATE/pending-ha-join.XXXXXX")" || return 1
    jq -n --arg cluster "$HA_CLUSTER_ID" --arg domain "$(mp_env_get DOMAIN)" \
        --arg witness "$HA_WITNESS_URL" --arg pair "$pair" \
        --arg target "$HA_PEER_NODE_ID" --arg lane "$lane" --arg commit "$campaign" \
        '{format:"mp-opt-ha-join-v2",cluster_id:$cluster,domain:$domain,witness_url:$witness,
          pairing_secret:$pair,node_id:$target,deployment_lane:$lane,
          campaign_commit:(if $commit == "" then null else $commit end)}' > "$pending" \
        && chmod 600 "$pending" && mv "$pending" "$MP_SETUP_V2_PENDING_JOIN" \
        || { rm -f "$pending"; return 1; }
    rm -f "$MP_SETUP_V2_PENDING_REPLACEMENT"; unset pair
    mp_setup_state_mark witness_bootstrap
    # Signed replacement reuses the already-qualified local release. An
    # unsigned replacement cannot claim this checkpoint until the exact
    # candidate identity has also been installed and verified on Node B after
    # pairing.
    [ "$lane" != signed ] \
        || mp_setup_state_has application_deployed \
        || mp_setup_state_mark application_deployed
    mp_setup_state_action "Waiting for replacement node join" PEER_JOIN_WAIT paired
}

# Return the durable checkpoint order used by both the TUI and the local
# automation adapter. This is a plan, not permission to execute a step.
mp_setup_checkpoint_plan_json() {
    local mode="$1" lane="${2:-signed}"
    case "$mode:$lane" in
        standalone-new:signed|standalone-new:unsigned)
            jq -cn '["signed_baseline_verified","configuration","public_dns",
                "application_deployed","public_routing_ready",
                "root_commissioning_complete","recovery_recipient","validated",
                "smtp_verified"]'
            ;;
        ha-primary-new:signed|ha-primary-new:unsigned)
            jq -cn '["signed_baseline_verified","configuration","witness_bootstrap",
                "paired","application_deployed","witness_ready","public_routing_ready",
                "root_commissioning_complete","recovery_recipient","replicated",
                "ha_services_activated","validated","smtp_verified",
                "automatic_failover_readiness"]'
            ;;
        convert-ha:signed)
            jq -cn '["signed_baseline_verified","configuration","recovery_recipient",
                "migration_snapshot","witness_bootstrap","paired","application_deployed",
                "witness_ready","public_routing_ready","replicated",
                "ha_services_activated","validated","smtp_verified",
                "automatic_failover_readiness"]'
            ;;
        convert-ha:unsigned)
            jq -cn '["signed_baseline_verified","configuration","recovery_recipient",
                "migration_snapshot","witness_bootstrap","paired","application_deployed",
                "witness_ready","public_routing_ready","replicated",
                "ha_services_activated","peer_exact_deployment","validated","smtp_verified",
                "automatic_failover_readiness"]'
            ;;
        ha-join:signed|replace-node:signed)
            jq -cn '["signed_baseline_verified","joined","application_deployed","replicated"]'
            ;;
        ha-join:unsigned|replace-node:unsigned)
            jq -cn '["signed_baseline_verified","joined","application_deployed","replicated",
                "peer_exact_deployment"]'
            ;;
        replace-primary:signed|replace-primary:unsigned)
            jq -cn '["signed_baseline_verified","witness_bootstrap","paired",
                "application_deployed","witness_ready","public_routing_ready","replicated",
                "ha_services_activated","validated","smtp_verified",
                "automatic_failover_readiness"]'
            ;;
        full-restore:signed|full-restore:unsigned)
            jq -cn '["signed_baseline_verified","imported","restored",
                "application_deployed","public_routing_ready","validated","smtp_verified"]'
            ;;
        *) return 64 ;;
    esac
}

mp_setup_validate_state_contract() {
    local state_file="${1:-$MP_SETUP_V2_STATE}" owner expected_owner mode lane plan
    [ -f "$state_file" ] && [ ! -L "$state_file" ] || return 66
    owner="$(stat -c '%u' "$state_file" 2>/dev/null)" || return 66
    expected_owner="$(stat -c '%u' "$MP_STATE" 2>/dev/null)" || return 77
    [ "$owner" = "$expected_owner" ] || return 77
    [ "$(stat -c '%a' "$state_file" 2>/dev/null)" = 600 ] || return 77
    jq -e '
        .format == "mp-opt-setup-state-v2"
        and (.mode | IN("standalone-new","ha-primary-new","ha-join","convert-ha",
            "replace-primary","replace-node","full-restore"))
        and (.state | IN("in_progress","complete"))
        and (.deployment_lane | IN("signed","unsigned"))
        and (.completed | type == "array")
        and all(.completed[]; type == "string" and test("^[a-z0-9_]{1,64}$"))
        and ((.campaign_commit == null)
            or (.campaign_commit | test("^[0-9a-f]{40}$")))
        and (if .deployment_lane == "signed" then .campaign_commit == null else true end)
        and ((.current_action == null) or (.current_action | type == "string" and length <= 400))
        and ((.current_action_code == null) or (.current_action_code | test("^[A-Z0-9_]{1,64}$")))
        and ((.current_checkpoint == null) or (.current_checkpoint | test("^[a-z0-9_]{1,64}$")))
        and ((.machine_transitions // {}) | type == "object")
        and (((.machine_transitions // {}) | to_entries) | all(.[].key;
            test("^[a-z0-9_]{1,64}$")))
        and (((.machine_transitions // {}) | to_entries) | all(.[].value;
            (.idempotency_key | type == "string"
                and test("^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"))
            and (.state | IN("started","completed"))))
    ' "$state_file" >/dev/null 2>&1 || return 65
    mode="$(jq -r .mode "$state_file")"; lane="$(jq -r .deployment_lane "$state_file")"
    plan="$(mp_setup_checkpoint_plan_json "$mode" "$lane")" || return 65
    jq -e --argjson plan "$plan" 'all(.completed[]; . as $step | $plan | index($step) != null)' \
        "$state_file" >/dev/null 2>&1 || return 65
}

mp_setup_machine_complete_if_plan_finished() {
    local plan="$1" remaining
    remaining="$(jq -r --argjson plan "$plan" '
        . as $setup
        | $plan | map(select(. as $step | ($setup.completed | index($step)) == null))
        | length
    ' "$MP_SETUP_V2_STATE")" || return 1
    [ "$remaining" -ne 0 ] || mp_setup_state_complete
}

mp_setup_upgrade_pending_witness_bootstrap() {
    local file="${1:-$MP_SETUP_V2_PENDING_BOOTSTRAP}" temporary
    [ -s "$file" ] || return 0
    [ -f "$file" ] && [ ! -L "$file" ] \
        && [ "$(stat -c '%u:%a' "$file" 2>/dev/null)" = "$(id -u):600" ] \
        || return 77
    jq -e '.format == "mp-opt-pending-witness-bootstrap-v1"' \
        "$file" >/dev/null 2>&1 || return 0
    # v1 was written only after the Worker deployment had succeeded. Preserve
    # its exact cluster, credentials, node material, URL and zone while adding
    # the explicit lifecycle state required by the resumable v2 contract.
    jq -e '
        (.cluster_id | test("^mp-opt-[0-9a-f-]{36}$"))
        and (.node_token | type == "string" and length >= 32 and length <= 4096)
        and (.pairing_secret | type == "string" and length >= 32 and length <= 4096)
        and (.admin_token | type == "string" and length >= 32 and length <= 4096)
        and (.zone_id | type == "string" and length >= 1)
        and (.witness_url | test("^https://[^[:space:]]+\\.workers\\.dev/?$"))
    ' "$file" >/dev/null 2>&1 || return 65
    temporary="$(mktemp "$MP_STATE/pending-witness-bootstrap.XXXXXX")" || return 1
    jq '.format="mp-opt-pending-witness-bootstrap-v2" | .state="deployed"' \
        "$file" > "$temporary" \
        && chmod 600 "$temporary" && sync -f "$temporary" 2>/dev/null \
        && mv "$temporary" "$file" && sync -f "$MP_STATE" 2>/dev/null \
        || { rm -f "$temporary"; return 1; }
}

mp_setup_validate_pending_witness_bootstrap() {
    local file="${1:-$MP_SETUP_V2_PENDING_BOOTSTRAP}"
    [ -f "$file" ] && [ ! -L "$file" ] \
        && [ "$(stat -c '%u:%a' "$file" 2>/dev/null)" = "$(id -u):600" ] \
        || return 77
    jq -e '
        .format == "mp-opt-pending-witness-bootstrap-v2"
        and (.state | IN("planned","deployed","registered"))
        and (.cluster_id | test("^mp-opt-[0-9a-f-]{36}$"))
        and (.domain | type == "string" and length >= 1 and length <= 253)
        and (.node_token | type == "string" and length >= 32 and length <= 4096)
        and (.pairing_secret | type == "string" and length >= 32 and length <= 4096)
        and (.admin_token | type == "string" and length >= 32 and length <= 4096)
        and (.node_a_ipv4 | type == "string" and length >= 1 and length <= 45)
        and (.node_a_ipv6 | type == "string" and length <= 45)
        and (.node_a_ssh_public_key | type == "string" and length >= 1)
        and (.node_a_ssh_host_key | type == "string" and length >= 1)
        and (.node_a_age_recipient | test("^age1[0-9a-z]+$"))
        and (if .state == "planned" then
            .zone_id == null and .witness_url == null
          else
            (.zone_id | type == "string" and length >= 1)
            and (.witness_url | test("^https://[^[:space:]]+\\.workers\\.dev/?$"))
          end)
    ' "$file" >/dev/null 2>&1
}

# Reconcile only facts which can be proved locally or from signed/accepted
# receipts. It never prompts, deploys, changes provider state, or invents a
# completed human acknowledgement.
mp_setup_machine_reconcile() {
    local state mode lane stage previous_bundle current_bundle changed=false
    [ -s "$MP_SETUP_V2_STATE" ] || return 10
    mp_setup_validate_state_contract "$MP_SETUP_V2_STATE" || return $?
    state="$(jq -r .state "$MP_SETUP_V2_STATE")"
    if [ "$state" = complete ]; then
        mode="$(jq -r .mode "$MP_SETUP_V2_STATE")"
        lane="$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")"
        # A historical candidate peer finaliser could set state=complete after
        # activation while omitting its already-proven replication and exact
        # deployment checkpoints.  Repair that contradiction from the guarded
        # receiver/current-deployment receipts before taking the generic
        # completed-state fast path.
        if [[ "$mode" =~ ^(ha-join|replace-node)$ ]] && [ "$lane" = unsigned ]; then
            mp_setup_reconcile_unsigned_join || return 1
        fi
        # Replication continues after commissioning completes.  If a newer
        # accepted bundle is durably acknowledged by both nodes, refresh the
        # primary setup snapshot before taking the completed-state fast path.
        if [[ "$mode" =~ ^(ha-primary-new|convert-ha|replace-primary)$ ]] \
            && mp_setup_state_has paired; then
            previous_bundle="$(jq -c '.first_verified_bundle // null' "$MP_SETUP_V2_STATE")"
            if mp_load_ha_config >/dev/null 2>&1 \
                && mp_setup_record_first_verified_bundle >/dev/null 2>&1; then
                current_bundle="$(jq -c '.first_verified_bundle // null' "$MP_SETUP_V2_STATE")"
                [ "$previous_bundle" = "$current_bundle" ] || changed=true
            fi
        fi
        if jq -e '.current_action != null or .current_action_code != null
            or .current_checkpoint != null or .action_started_at != null
            or .last_failure != null' "$MP_SETUP_V2_STATE" >/dev/null; then
            mp_setup_state_update '.current_action=null | .current_action_code=null
                | .current_checkpoint=null | .action_started_at=null | .last_failure=null' \
                || return 1
            changed=true
        fi
        [ "$changed" = false ] || mp_setup_journal_event state.reconciled || return 1
        return 0
    fi
    mode="$(jq -r .mode "$MP_SETUP_V2_STATE")"
    lane="$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")"
    if [[ "$mode" =~ ^(ha-join|replace-node)$ ]]; then
        if [ "$lane" = signed ]; then
            mp_reconcile_signed_join_setup || return 1
        else
            mp_setup_reconcile_unsigned_join || return 1
        fi
        [ "$(jq -r .state "$MP_SETUP_V2_STATE")" != complete ] || return 0
    fi
    if ! mp_setup_state_has application_deployed; then
        if [ "$lane" = signed ]; then
            if mp_setup_verify_signed_application >/dev/null 2>&1; then
                mp_setup_state_mark application_deployed || return 1
                changed=true
            fi
        else
            if [ "$(jq -r '.current_commit // empty' "$MP_STATE/test-deployments/current.json" 2>/dev/null || true)" \
                    = "$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")" ] \
                && mp_setup_verify_exact_environment \
                    "$(jq -r .campaign_commit "$MP_SETUP_V2_STATE")" >/dev/null 2>&1 \
                && mp_wait_for_stable_local_services 1 1 >/dev/null 2>&1; then
                mp_setup_state_mark application_deployed || return 1
                changed=true
            fi
        fi
    fi
    if [[ "$mode" =~ ^(ha-primary-new|convert-ha|replace-primary)$ ]] \
        && mp_setup_state_has paired; then
        if mp_load_ha_config >/dev/null 2>&1 \
            && mp_setup_record_first_verified_bundle >/dev/null 2>&1; then
            if ! mp_setup_state_has replicated; then
                mp_setup_state_mark replicated || return 1
            fi
            changed=true
        fi
    fi
    if [[ "$mode" =~ ^(standalone-new|ha-primary-new)$ ]] \
        && mp_setup_state_has application_deployed \
        && mp_root_bootstrap_is_disabled >/dev/null 2>&1; then
        # Browser commissioning disables bootstrap in the database before the
        # machine controller can observe its completion receipt. Retire the
        # host-side bearer value at that reconciliation boundary, just as the
        # interactive polling path does. Replication and snapshot workers are
        # deliberately validation-only, so advancing the setup checkpoint
        # while this file is still populated would leave the installation in
        # a state those workers must reject.
        mp_retire_root_bootstrap_secret || return 1
        mp_validate_retired_root_bootstrap_secret_existing_runtime || return 1
        stage="$(mp_setup_commissioning_stage 2>/dev/null || true)"
        if [ "$stage" = complete ]; then
            if ! mp_recovery_recipient >/dev/null 2>&1; then
                mp_setup_sync_commissioning_recipient || return 1
                changed=true
            fi
            if ! mp_setup_state_has root_commissioning_complete; then
                mp_setup_state_mark root_commissioning_complete || return 1
                changed=true
            fi
            if ! mp_setup_state_has recovery_recipient; then
                mp_setup_state_mark recovery_recipient || return 1
                changed=true
            fi
        fi
    fi
    [ "$changed" = false ] || mp_setup_journal_event state.reconciled || return 1
    return 10
}

# Readiness observations converge asynchronously: the peer can accept a
# verified bundle moments after the primary first asks the witness for its
# current view.  Treat that ordinary observation gap as a wait, not as a
# terminal commissioning failure.  Provider mutations below remain hard
# failures; only the read-only convergence gates return the machine waiting
# status.
mp_setup_verify_automatic_failover_readiness() {
    mp_ha_refresh_witness_observations || return 10
    mp_ha_active_verification_readiness || return 10
    # Commissioning proves that automatic failover could be enabled, but
    # activation remains a separate explicit action after recovery testing.
    python3 "$MP_ROOT/deploy/ha/witness_control.py" automatic disabled >/dev/null \
        || return 1
    mp_ha_set_config_value HA_AUTOMATIC_FAILOVER disabled || return 1
}

# Execute exactly one non-interactive transition. The input document has
# already been schema-validated by the machine adapter and names the expected
# next checkpoint, preventing a stale coordinator from advancing another step.
mp_setup_machine_advance_one() {
    local input_file="$1" mode lane plan checkpoint expected ipv4 ipv6 commit
    local idempotency_key recorded_key recorded_state remaining
    local smtp_enabled smtp_host smtp_port smtp_username smtp_token smtp_security
    local smtp_from_email smtp_from_name smtp_reply_to
    local cluster_id node_token pairing_secret body pending bootstrap_tmp response peer current
    local bootstrap_error bootstrap_ok=false repair_attempted=false attempt
    local requested_tag requested_commit fault_transition="" replay_test_receipt=false
    local fault_hook_active=false hook_status=0 step_status=0
    local -a install_args
    mp_setup_validate_state_contract "$MP_SETUP_V2_STATE" || return $?
    checkpoint="$(jq -r .checkpoint "$input_file")"
    idempotency_key="$(jq -r .idempotency_key "$input_file")"
    if [ "$(jq -r .state "$MP_SETUP_V2_STATE")" = complete ]; then
        recorded_key="$(jq -r --arg checkpoint "$checkpoint" \
            '.machine_transitions[$checkpoint].idempotency_key // empty' \
            "$MP_SETUP_V2_STATE")"
        recorded_state="$(jq -r --arg checkpoint "$checkpoint" \
            '.machine_transitions[$checkpoint].state // empty' \
            "$MP_SETUP_V2_STATE")"
        [ "$recorded_state" = completed ] && [ "$recorded_key" = "$idempotency_key" ]
        return $?
    fi
    mode="$(jq -r .mode "$MP_SETUP_V2_STATE")"
    lane="$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")"
    plan="$(mp_setup_checkpoint_plan_json "$mode" "$lane")" || return 65
    expected="$(jq -r --argjson plan "$plan" '
        . as $setup
        | $plan | map(select(. as $step | ($setup.completed | index($step)) == null))
        | .[0] // empty
    ' "$MP_SETUP_V2_STATE")"
    recorded_key="$(jq -r --arg checkpoint "$checkpoint" \
        '.machine_transitions[$checkpoint].idempotency_key // empty' \
        "$MP_SETUP_V2_STATE")"
    recorded_state="$(jq -r --arg checkpoint "$checkpoint" \
        '.machine_transitions[$checkpoint].state // empty' \
        "$MP_SETUP_V2_STATE")"
    if mp_setup_state_has "$checkpoint"; then
        [ "$recorded_key" = "$idempotency_key" ] || return 65
        if [ "$recorded_state" = started ]; then
            mp_setup_state_update \
                '.machine_transitions[$checkpoint].state="completed"
                 | .machine_transitions[$checkpoint].completed_at=$now' \
                --arg checkpoint "$checkpoint" || return 1
        else
            [ "$recorded_state" = completed ] || return 65
        fi
        mp_setup_machine_complete_if_plan_finished "$plan" || return $?
        return 0
    fi
    [ -n "$expected" ] && [ "$checkpoint" = "$expected" ] || return 65
    if [ -n "$recorded_key" ]; then
        [ "$recorded_key" = "$idempotency_key" ] || return 65
        if [ "$recorded_state" = completed ]; then
            # The side effect and its durable machine receipt completed, but a
            # crash or interruption may have happened before the setup
            # checkpoint was written. Reconcile that proven completion instead
            # of rejecting the exact idempotent retry forever.
            mp_setup_state_mark_now "$checkpoint" || return 1
            mp_setup_machine_complete_if_plan_finished "$plan" || return $?
            return 0
        fi
        [ "$recorded_state" = started ] || return 65
    else
        mp_setup_state_update \
            '.machine_transitions=((.machine_transitions // {}) + {
                ($checkpoint):{idempotency_key:$key,state:"started",started_at:$now}})' \
            --arg checkpoint "$checkpoint" --arg key "$idempotency_key" || return 1
    fi
    MP_SETUP_MACHINE_CHECKPOINT="$checkpoint"
    MP_SETUP_MACHINE_IDEMPOTENCY_KEY="$idempotency_key"
    if declare -F mp_setup_test_hook_transition_for_checkpoint >/dev/null; then
        fault_transition="$(mp_setup_test_hook_transition_for_checkpoint "$checkpoint")"
    fi
    if [ -n "$fault_transition" ] && [ -s "${MP_SETUP_TEST_HOOK_ENABLED:-/nonexistent}" ]; then
        hook_status=0
        mp_setup_test_hook_should_wrap "$fault_transition" "$checkpoint" \
            "$idempotency_key" || hook_status=$?
        case "$hook_status" in
            0) fault_hook_active=true ;;
            1) fault_hook_active=false ;;
            *) return "$hook_status" ;;
        esac
    fi
    if [ "$fault_hook_active" = true ]; then
        if mp_setup_test_hook_receipt_matches "$fault_transition" "$checkpoint" "$idempotency_key"; then
            replay_test_receipt=true
        else
            MP_SETUP_TEST_DEFER_CHECKPOINT="$checkpoint"
            MP_SETUP_TEST_MARK_REQUESTED=false
            mp_setup_test_hook_reach_named "$fault_transition" before-side-effect || return $?
        fi
    fi
    if [ "$replay_test_receipt" = false ]; then
      case "$checkpoint" in
        signed_baseline_verified)
            if [ "$(jq -r '.values | length' "$input_file")" -gt 0 ]; then
                requested_tag="$(jq -r .values.tag "$input_file")"
                requested_commit="$(jq -r .values.commit "$input_file")"
                install_args=(--repo-root "$MP_ROOT" --tag "$requested_tag")
                [ "$lane" != unsigned ] || install_args+=(--baseline-only)
                python3 "$MP_ROOT/deploy/release/install_release.py" "${install_args[@]}" || return 1
                [ "$(sed -n 's/^MP_RELEASE_TAG=//p' "$MP_ROOT/.release.env" | head -1)" = "$requested_tag" ] \
                    && [ "$(sed -n 's/^MP_RELEASE_COMMIT=//p' "$MP_ROOT/.release.env" | head -1)" = "$requested_commit" ] \
                    || return 65
                mp_setup_record_signed_baseline || return 1
            else
                mp_setup_install_signed_release
            fi
            ;;
        configuration)
            if [ "$mode" = convert-ha ]; then
                [ "$(jq -r '.values | length' "$input_file")" -eq 0 ] \
                    && [ -s "$MP_ROOT/.env" ] \
                    && mp_validate_hostname "$(mp_env_get DOMAIN 2>/dev/null || true)" \
                    || return 65
                mp_setup_state_mark configuration
            else
                smtp_enabled="$(jq -r '.values.smtp.enabled' "$input_file")"
                if [ "$smtp_enabled" = true ]; then
                smtp_host="$(jq -r .values.smtp.host "$input_file")"
                smtp_port="$(jq -r .values.smtp.port "$input_file")"
                smtp_username="$(jq -r .values.smtp.username "$input_file")"
                smtp_token="$(jq -r .values.smtp.token "$input_file")"
                smtp_security="$(jq -r .values.smtp.security "$input_file")"
                smtp_from_email="$(jq -r .values.smtp.from_email "$input_file")"
                smtp_from_name="$(jq -r .values.smtp.from_name "$input_file")"
                smtp_reply_to="$(jq -r '.values.smtp.reply_to // empty' "$input_file")"
                else
                    smtp_host=""; smtp_port=587; smtp_username=""; smtp_token=""
                    smtp_security=starttls; smtp_from_email=""; smtp_from_name="Masterplan Access"
                    smtp_reply_to=""
                fi
                mp_setup_state_action "Protected configuration" \
                    CONFIGURATION_WRITING configuration || return 1
                mp_apply_initial_configuration \
                "$(jq -r .values.domain "$input_file")" \
                "$(jq -r .values.application_name "$input_file")" \
                "$(jq -r .values.vapid_contact_email "$input_file")" \
                "$(openssl rand -hex 32)" \
                "$([ "$smtp_enabled" = true ] && printf yes || printf no)" \
                "$smtp_host" "$smtp_port" "$smtp_username" "$smtp_token" \
                "$smtp_security" "$smtp_from_email" "$smtp_from_name" "$smtp_reply_to" \
                    || return 1
                unset smtp_token MP_INITIAL_ROOT_TOKEN
                mp_setup_state_mark configuration
            fi
            ;;
        public_dns)
            ipv4="$(jq -r '.values.ipv4 // empty' "$input_file")"
            ipv6="$(jq -r '.values.ipv6 // empty' "$input_file")"
            python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "$ipv4" \
                >/dev/null 2>&1 || return 65
            [ -z "$ipv6" ] || python3 -c \
                'import ipaddress,sys; ipaddress.IPv6Address(sys.argv[1])' "$ipv6" \
                >/dev/null 2>&1 || return 65
            mp_setup_state_action "Verifying public DNS" \
                PUBLIC_DNS_VERIFY public_dns || return 1
            mp_setup_standalone_dns_matches "$(mp_env_get DOMAIN)" "$ipv4" "$ipv6" \
                || return 10
            mp_setup_state_update \
                '.public_routing={ipv4:$ipv4,ipv6:(if $ipv6 == "" then null else $ipv6 end)}' \
                --arg ipv4 "$ipv4" --arg ipv6 "$ipv6" || return 1
            mp_setup_state_mark public_dns
            ;;
        witness_bootstrap)
            if [ "$mode" = replace-primary ]; then
                [ "$(jq -r '.values.old_peer_powered_off // false' "$input_file")" = true ] \
                    || return 65
                mp_setup_state_action "Opening replacement-node pairing" \
                    WITNESS_REPLACEMENT_OPENING witness_bootstrap || return 1
                mp_setup_machine_open_replacement || return $?
            else
                [[ "$mode" =~ ^(ha-primary-new|convert-ha)$ ]] || return 10
                ipv4="$(jq -r .values.ipv4 "$input_file")"
                ipv6="$(jq -r '.values.ipv6 // empty' "$input_file")"
            mp_setup_state_action "Deploying HA witness" \
                WITNESS_DEPLOYING witness_bootstrap || return 1
            if [ ! -s "$MP_SETUP_V2_PENDING_BOOTSTRAP" ]; then
                mp_setup_prepare_node_material_machine node-a "$ipv4" "$ipv6" || return 1
                cluster_id="mp-opt-$(cat /proc/sys/kernel/random/uuid)"
                node_token="$(mp_random_secret)"; pairing_secret="$(mp_random_secret)"
                MP_SETUP_WITNESS_ADMIN_TOKEN="$(mp_random_secret)"
                bootstrap_tmp="$(mktemp "$MP_STATE/pending-witness-bootstrap.XXXXXX")" || return 1
                jq -n --arg cluster "$cluster_id" --arg token "$node_token" \
                    --arg pair "$pairing_secret" --arg domain "$(mp_env_get DOMAIN)" \
                    --arg admin "$MP_SETUP_WITNESS_ADMIN_TOKEN" \
                    --arg ipv4 "$MP_SETUP_NODE_IPV4" --arg ipv6 "$MP_SETUP_NODE_IPV6" \
                    --arg ssh "$MP_SETUP_NODE_SSH_PUBLIC" --arg host "$MP_SETUP_NODE_SSH_HOST" \
                    --arg age "$MP_SETUP_NODE_AGE_RECIPIENT" \
                    '{format:"mp-opt-pending-witness-bootstrap-v2",state:"planned",
                      cluster_id:$cluster,node_token:$token,pairing_secret:$pair,
                      zone_id:null,domain:$domain,witness_url:null,admin_token:$admin,
                      node_a_ipv4:$ipv4,node_a_ipv6:$ipv6,node_a_ssh_public_key:$ssh,
                      node_a_ssh_host_key:$host,node_a_age_recipient:$age}' > "$bootstrap_tmp" \
                    && chmod 600 "$bootstrap_tmp" && sync -f "$bootstrap_tmp" 2>/dev/null \
                    && mv "$bootstrap_tmp" "$MP_SETUP_V2_PENDING_BOOTSTRAP" \
                    && sync -f "$MP_STATE" 2>/dev/null \
                    || { rm -f "$bootstrap_tmp"; return 1; }
            fi
            mp_setup_upgrade_pending_witness_bootstrap || return $?
            mp_setup_validate_pending_witness_bootstrap || return $?
            cluster_id="$(jq -r .cluster_id "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
            node_token="$(jq -r .node_token "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
            pairing_secret="$(jq -r .pairing_secret "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
            MP_SETUP_WITNESS_ADMIN_TOKEN="$(jq -r .admin_token "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
            if [ "$(jq -r .state "$MP_SETUP_V2_PENDING_BOOTSTRAP")" = planned ]; then
                mp_setup_deploy_witness_machine "$(jq -r .domain "$MP_SETUP_V2_PENDING_BOOTSTRAP")" \
                    "$cluster_id" "$(jq -r .values.cloudflare_account_id "$input_file")" \
                    "$(jq -r .values.cloudflare_deploy_token "$input_file")" \
                    "$(jq -r .values.cloudflare_dns_token "$input_file")" \
                    "$MP_SETUP_WITNESS_ADMIN_TOKEN" || return 1
                bootstrap_tmp="$(mktemp "$MP_STATE/pending-witness-bootstrap.XXXXXX")" || return 1
                jq --arg witness "$MP_SETUP_WITNESS_URL" --arg zone "$MP_SETUP_ZONE_ID" \
                    '.state="deployed" | .witness_url=$witness | .zone_id=$zone' \
                    "$MP_SETUP_V2_PENDING_BOOTSTRAP" > "$bootstrap_tmp" \
                    && chmod 600 "$bootstrap_tmp" && sync -f "$bootstrap_tmp" 2>/dev/null \
                    && mv "$bootstrap_tmp" "$MP_SETUP_V2_PENDING_BOOTSTRAP" \
                    && sync -f "$MP_STATE" 2>/dev/null || return 1
            fi
            MP_SETUP_WITNESS_URL="$(jq -r .witness_url "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
            MP_SETUP_ZONE_ID="$(jq -r .zone_id "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
            MP_SETUP_NODE_IPV4="$(jq -r .node_a_ipv4 "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
            MP_SETUP_NODE_IPV6="$(jq -r .node_a_ipv6 "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
            MP_SETUP_NODE_SSH_PUBLIC="$(jq -r .node_a_ssh_public_key "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
            MP_SETUP_NODE_SSH_HOST="$(jq -r .node_a_ssh_host_key "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
            MP_SETUP_NODE_AGE_RECIPIENT="$(jq -r .node_a_age_recipient "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
            body="$(mktemp "$MP_STATE/witness-bootstrap.XXXXXX")" || return 1
            jq -n --arg cluster "$cluster_id" --arg token "$node_token" \
                --arg pair "$pairing_secret" --arg zone "$MP_SETUP_ZONE_ID" \
                --arg domain "$(mp_env_get DOMAIN)" --arg ipv4 "$MP_SETUP_NODE_IPV4" \
                --arg ipv6 "$MP_SETUP_NODE_IPV6" --arg ssh "$MP_SETUP_NODE_SSH_PUBLIC" \
                --arg host "$MP_SETUP_NODE_SSH_HOST" --arg age "$MP_SETUP_NODE_AGE_RECIPIENT" \
                '{cluster_id:$cluster,initial_holder:"node-a",node_a_token:$token,
                  pairing_secret:$pair,zone_id:$zone,hostname:$domain,node_a_ipv4:$ipv4,
                  node_a_ipv6:$ipv6,node_a_ssh_public_key:$ssh,node_a_ssh_host_key:$host,
                  node_a_age_recipient:$age}' > "$body" || return 1
            mp_setup_state_action "Registering Node A with HA witness" \
                WITNESS_REGISTERING witness_bootstrap || return 1
            if [ "$(jq -r .state "$MP_SETUP_V2_PENDING_BOOTSTRAP")" != registered ]; then
                bootstrap_error="$(mktemp "$MP_STATE/witness-bootstrap-error.XXXXXX")" \
                    || { rm -f "$body"; return 1; }
                for attempt in $(seq 1 20); do
                    : > "$bootstrap_error"
                    if mp_setup_witness_call bootstrap "$MP_SETUP_WITNESS_URL" "$cluster_id" \
                        "$MP_SETUP_WITNESS_ADMIN_TOKEN" "$body" \
                        >/dev/null 2> "$bootstrap_error"; then
                        bootstrap_ok=true
                        break
                    fi
                    if [ "$repair_attempted" = false ] \
                        && grep -Eq 'remote API returned HTTP 401([^0-9]|$)' "$bootstrap_error"; then
                        mp_setup_deploy_witness_machine \
                            "$(jq -r .domain "$MP_SETUP_V2_PENDING_BOOTSTRAP")" \
                            "$cluster_id" \
                            "$(jq -r .values.cloudflare_account_id "$input_file")" \
                            "$(jq -r .values.cloudflare_deploy_token "$input_file")" \
                            "$(jq -r .values.cloudflare_dns_token "$input_file")" \
                            "$MP_SETUP_WITNESS_ADMIN_TOKEN" \
                            || { rm -f "$body" "$bootstrap_error"; return 20; }
                        repair_attempted=true
                        continue
                    fi
                    if ! grep -Eq 'remote API returned HTTP (404|429|500|502|503|504)([^0-9]|$)|provider error 1042([^0-9]|$)' \
                        "$bootstrap_error"; then
                        break
                    fi
                    [ "$attempt" -eq 20 ] || sleep 3
                done
                if [ "$bootstrap_ok" != true ]; then
                    cat "$bootstrap_error" >&2
                    rm -f "$body" "$bootstrap_error"
                    return 20
                fi
                rm -f "$bootstrap_error"
                bootstrap_tmp="$(mktemp "$MP_STATE/pending-witness-bootstrap.XXXXXX")" || return 1
                jq '.state="registered"' "$MP_SETUP_V2_PENDING_BOOTSTRAP" > "$bootstrap_tmp" \
                    && chmod 600 "$bootstrap_tmp" && sync -f "$bootstrap_tmp" 2>/dev/null \
                    && mv "$bootstrap_tmp" "$MP_SETUP_V2_PENDING_BOOTSTRAP" \
                    && sync -f "$MP_STATE" 2>/dev/null || return 1
            fi
            rm -f "$body"
            mp_setup_install_ha_identity node-a node-b "$cluster_id" \
                "$MP_SETUP_WITNESS_URL" "$node_token" || return 1
            pending="$(mktemp "$MP_STATE/pending-ha-join.XXXXXX")" || return 1
            jq -n --arg cluster "$cluster_id" --arg domain "$(mp_env_get DOMAIN)" \
                --arg witness "$MP_SETUP_WITNESS_URL" --arg pair "$pairing_secret" \
                --arg lane "$lane" --arg commit "$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")" \
                '{format:"mp-opt-ha-join-v2",cluster_id:$cluster,domain:$domain,
                  witness_url:$witness,pairing_secret:$pair,node_id:"node-b",
                  deployment_lane:$lane,campaign_commit:(if $commit == "" then null else $commit end)}' \
                > "$pending" || return 1
            chmod 600 "$pending" && mv "$pending" "$MP_SETUP_V2_PENDING_JOIN" || return 1
            # Retain the protected intent through the checkpoint. It is safe
            # to remove only after pairing has moved to the next checkpoint.
            unset node_token pairing_secret MP_SETUP_WITNESS_ADMIN_TOKEN
            mp_setup_state_mark witness_bootstrap
                mp_setup_state_action "Waiting for Node B join" PEER_JOIN_WAIT paired
            fi
            ;;
        joined)
            [[ "$mode" =~ ^(ha-join|replace-node)$ ]] || return 10
            mp_setup_join_node_machine "$input_file"
            ;;
        paired)
            # The join code is already installed. A receipt from the witness,
            # rather than an input assertion, proves that Node B consumed it.
            mp_load_ha_config || return 10
            node_token="$(cat "$MP_HA_HOME/secrets/node_token")" || return 10
            body="$(mktemp "$MP_STATE/pair-state.XXXXXX")" || return 1
            jq -n --arg node "$HA_NODE_ID" '{node_id:$node}' > "$body"
            response="$(mp_setup_witness_call pair-state "$HA_WITNESS_URL" \
                "$HA_CLUSTER_ID" "$node_token" "$body")" || { rm -f "$body"; return 10; }
            rm -f "$body"; unset node_token
            jq -e '.paired == true' <<< "$response" >/dev/null 2>&1 || return 10
            peer="$(jq -c --arg peer "$HA_PEER_NODE_ID" '.nodes[] | select(.node_id == $peer)' <<< "$response")"
            [ -n "$peer" ] || return 10
            mp_setup_install_peer_trust "$(jq -r .ipv4 <<< "$peer")" \
                "$(jq -r .ssh_public_key <<< "$peer")" "$(jq -r .ssh_host_key <<< "$peer")" \
                "$(jq -r .age_recipient <<< "$peer")" || return 1
            current="$(jq -c --arg node "$HA_NODE_ID" \
                '.nodes[] | select(.node_id == $node)' <<< "$response")"
            if [ -n "$current" ]; then
                mp_setup_state_update \
                    '.public_routing={ipv4:$ipv4,ipv6:(if $ipv6 == "" then null else $ipv6 end)}' \
                    --arg ipv4 "$(jq -r '.ipv4 // empty' <<< "$current")" \
                    --arg ipv6 "$(jq -r '.ipv6 // empty' <<< "$current")" || return 1
            fi
            mp_setup_state_mark paired
            rm -f "$MP_SETUP_V2_PENDING_BOOTSTRAP"
            ;;
        application_deployed)
            if [ "$mode" = full-restore ]; then
                mp_wait_for_stable_local_services 2 1 >/dev/null 2>&1 || return 10
                mp_setup_state_mark application_deployed
            elif [ "$mode" = replace-primary ]; then
                if [ "$lane" = unsigned ]; then
                    commit="$(jq -r .campaign_commit "$MP_SETUP_V2_STATE")"
                    [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || return 65
                    jq -c '.values.registry' "$input_file" \
                        | "$MP_ROOT/deploy/test-deployment.sh" prepare-peer "$commit" \
                            --registry-credentials-stdin \
                        || return 1
                fi
                mp_setup_state_mark application_deployed
            elif [ "$mode" = convert-ha ] && [ "$lane" = unsigned ]; then
                mp_setup_activate_converted_unsigned_pair \
                    --registry-credentials-file "$input_file" || return 1
                mp_setup_state_mark application_deployed
            elif [ "$lane" = unsigned ] \
                && [ -s "$MP_STATE/test-deployments/candidate/receipt.json" ]; then
                [ "$(jq -r '.commit // empty' "$MP_STATE/test-deployments/candidate/receipt.json")" \
                    = "$(jq -r .campaign_commit "$MP_SETUP_V2_STATE")" ] || return 65
                if ! jq -c '.values.registry' "$input_file" \
                    | "$MP_ROOT/deploy/test-deployment.sh" apply-prebuilt \
                        "$(jq -r .campaign_commit "$MP_SETUP_V2_STATE")" \
                        --fresh-commissioning --registry-credentials-stdin; then
                    local failed_stage failure_code
                    failed_stage="$(tr -cd 'a-z0-9-' \
                        < "$MP_STATE/test-deployments/current-stage" 2>/dev/null \
                        | head -c 32)"
                    [ -n "$failed_stage" ] || failed_stage=unknown
                    failure_code="UNSIGNED_CANDIDATE_$(tr 'a-z-' 'A-Z_' <<< "$failed_stage")_FAILED"
                    mp_setup_state_failure "$failure_code" \
                        "The exact candidate deployment stopped during ${failed_stage}. Resume will reuse its idempotency key and authoritative facts." \
                        || true
                    return 1
                fi
            else
                mp_setup_deploy_application || return 1
            fi
            mp_setup_state_mark application_deployed
            ;;
        public_routing_ready)
            if [ "$mode" = full-restore ]; then
                mp_wait_for_public_health 45 || return 10
                mp_setup_state_mark public_routing_ready
            else
                ipv4="$(jq -r '.public_routing.ipv4 // empty' "$MP_SETUP_V2_STATE")"
                ipv6="$(jq -r '.public_routing.ipv6 // empty' "$MP_SETUP_V2_STATE")"
                [ -n "$ipv4" ] || return 65
                mp_setup_verify_public_routing "$(mp_env_get DOMAIN)" "$ipv4" "$ipv6"
            fi
            ;;
        replicated)
            if ! mp_setup_record_first_verified_bundle >/dev/null 2>&1; then
                if [ "$mode" = convert-ha ]; then
                    mp_setup_state_action "Verifying initial HA writer identity" \
                        HA_WRITER_ESTABLISHING replicated || return 1
                    mp_setup_establish_initial_writer_identity || return 1
                fi
                mp_setup_state_action "Replicating complete application state to Node B" \
                    FIRST_BUNDLE_TRANSFER replicated || return 1
                mp_setup_prepare_unsigned_replacement_peer_if_needed || {
                    mp_setup_state_failure PEER_CANDIDATE_PREPARATION_FAILED \
                        "Node B could not reinstall and verify the exact candidate runtime assets, so no replication bundle was sent." || true
                    return 1
                }
                mp_setup_verify_unsigned_peer_identity || {
                    mp_setup_state_failure PEER_CANDIDATE_IDENTITY_MISMATCH \
                        "Node B is not bound to the exact candidate identity, so no replication bundle was sent." || true
                    return 1
                }
                mp_ha_replicate_now || return 1
                mp_setup_record_first_verified_bundle >/dev/null 2>&1 || return 1
            fi
            mp_setup_finalize_fresh_unsigned_peer || return 1
            mp_setup_state_mark replicated
            ;;
        ha_services_activated)
            mp_setup_state_action "Activating verified HA services" \
                HA_SERVICES_ACTIVATING ha_services_activated || return 1
            "$MP_ROOT/deploy/ha/install_services.sh" || return 1
            mp_setup_state_mark ha_services_activated
            ;;
        peer_exact_deployment)
            [ "$lane" = unsigned ] || return 65
            commit="$(jq -r .campaign_commit "$MP_SETUP_V2_STATE")"
            mp_setup_state_action "Finalising Node B exact deployment" \
                PEER_EXACT_DEPLOYMENT_FINALISING peer_exact_deployment || return 1
            ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
                env MP_ROOT=/opt/masterplan MP_TEST_PEER=1 \
                /opt/masterplan/deploy/test-deployment.sh internal-finalize-peer "$commit" \
                || return 1
            mp_setup_state_mark peer_exact_deployment
            ;;
        validated)
            mp_setup_state_action "Validating the complete installation" \
                INSTALLATION_VALIDATING validated || return 1
            mp_validate_installation || return 1
            mp_setup_state_mark validated
            ;;
        smtp_verified)
            mp_setup_state_action "Verifying SMTP and DNS" \
                SMTP_VALIDATING smtp_verified || return 1
            mp_setup_verify_smtp_and_dns_machine \
                "$(jq -r '.values.dkim_selector // empty' "$input_file")" \
                "$(jq -r '.values.test_recipient // empty' "$input_file")" \
                "$(jq -r '.values.correlation_id // empty' "$input_file")" || return $?
            mp_setup_state_mark smtp_verified
            ;;
        automatic_failover_readiness)
            mp_setup_state_action "Verifying automatic failover readiness" \
                AUTOMATIC_FAILOVER_VALIDATING automatic_failover_readiness || return 1
            mp_setup_verify_automatic_failover_readiness || return $?
            mp_setup_state_mark automatic_failover_readiness
            ;;
        recovery_recipient)
            local recipient probe role
            recipient="$(jq -r .values.recipient "$input_file")"
            [[ "$recipient" =~ ^age1[0-9a-z]+$ ]] || return 65
            probe="$(mktemp "${TMPDIR:-/tmp}/mp-opt-age-probe.XXXXXX")" || return 1
            printf '' | age -r "$recipient" -o "$probe" >/dev/null 2>&1 \
                || { rm -f "$probe"; return 65; }
            rm -f "$probe"
            role="$(mp_ha_role)" || return 1
            if [ "$role" = dynamic ]; then
                mp_ha_sync_recovery_recipient "$recipient" || return 1
            else
                mp_store_recovery_recipient_local "$recipient" || return 1
            fi
            mp_setup_state_mark recovery_recipient
            ;;
        migration_snapshot)
            local artifact_ticket artifact_receipt package_hash package_size snapshot_path package_id
            artifact_ticket="$(jq -r .values.artifact_ticket "$input_file")"
            artifact_receipt="$MP_SETUP_V2_ARTIFACTS/$artifact_ticket/receipt.json"
            [ -f "$artifact_receipt" ] && [ ! -L "$artifact_receipt" ] \
                && [ "$(stat -c '%a' "$artifact_receipt" 2>/dev/null)" = 600 ] \
                || return 65
            package_hash="$(jq -r .package_sha256 "$artifact_receipt")"
            [ "$package_hash" = "$(jq -r .values.package_sha256 "$input_file")" ] \
                && [ "$package_hash" = "$(jq -r '.pending_artifacts.migration_snapshot.sha256 // empty' "$MP_SETUP_V2_STATE")" ] \
                || return 65
            [ "$(sha256sum "$(jq -r .package_path "$artifact_receipt")" | awk '{print $1}')" = "$package_hash" ] \
                || return 65
            snapshot_path="$(jq -r .snapshot_path "$artifact_receipt")"
            package_id="$(jq -r .package_id "$artifact_receipt")"
            package_size="$(jq -r .package_size "$artifact_receipt")"
            mp_portable_initialise || return 1
            mp_portable_record_confirmed_export "$snapshot_path" "$package_id" \
                "$package_hash" "$package_size" || return 1
            rm -rf "$MP_SETUP_V2_ARTIFACTS/$artifact_ticket"
            mp_setup_state_update 'del(.pending_artifacts.migration_snapshot)' || return 1
            mp_setup_state_mark migration_snapshot
            ;;
        imported)
            local import_hash
            import_hash="$(jq -r .values.package_sha256 "$input_file")"
            [ "$import_hash" = "$(jq -r '.pending_artifacts.recovery_import.sha256 // empty' "$MP_SETUP_V2_STATE")" ] \
                && [ "$import_hash" = "$(jq -r '.package_sha256 // empty' "$MP_PORTABLE_LAST_IMPORT_STATE" 2>/dev/null || true)" ] \
                || return 65
            jq -e '.format == "mp-opt-portable-import-receipt-v1"' \
                "$MP_PORTABLE_LAST_IMPORT_STATE" >/dev/null || return 65
            install -m 0600 "$MP_PORTABLE_LAST_IMPORT_STATE" "$MP_SETUP_V2_IMPORT_RECEIPT" || return 1
            mp_setup_state_update 'del(.pending_artifacts.recovery_import)' || return 1
            mp_setup_state_mark imported
            ;;
        restored)
            local restore_identity imported_snapshot restore_authorization commit
            imported_snapshot="$(jq -er '.snapshot_path | select(type == "string")' \
                "$MP_SETUP_V2_IMPORT_RECEIPT")" || return 65
            case "$(readlink -f "$imported_snapshot")" in
                "$(readlink -f "$MP_SNAPSHOTS")"/*) ;;
                *) return 65 ;;
            esac
            if [ "$lane" = unsigned ]; then
                commit="$(jq -r '.values.candidate_commit // empty' "$input_file")"
                [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || return 65
                jq -c '.values.registry' "$input_file" \
                    | "$MP_ROOT/deploy/test-deployment.sh" \
                        prepare-full-loss-restore-prebuilt "$commit" \
                        --registry-credentials-stdin \
                    || return 1
                # Only the independently verified staged bundle may retarget
                # a pending development recovery. Commit the new exact SHA
                # after runtime preparation and before restoring data.
                mp_setup_state_update '.campaign_commit = $commit' \
                    --arg commit "$commit" || return 1
            else
                jq -e '(.values | keys) == ["recovery_identity"]' \
                    "$input_file" >/dev/null || return 65
            fi
            restore_identity="$(mp_setup_machine_identity_file \
                "$(jq -r .values.recovery_identity "$input_file")")" || return $?
            restore_authorization="$(mp_setup_prepare_full_loss_restore_authorization \
                "$imported_snapshot")" || {
                    mp_secure_remove_file "$restore_identity"; return 65;
                }
            mp_snapshot_restore_full_loss "$imported_snapshot" "$restore_identity" \
                "$restore_authorization" || {
                mp_secure_remove_file "$restore_identity"; return 1;
            }
            mp_secure_remove_file "$restore_identity"
            mp_setup_state_mark restored
            ;;
        witness_ready)
            mp_setup_state_action "Activating public HA routing" \
                WITNESS_ROUTING witness_ready || return 1
            if mp_setup_activate_initial_witness_routing; then
                :
            else
                step_status=$?
                # Network/provider availability is an ordinary wait. Leave
                # the exact transition started so the machine controller and
                # TUI resume it without a false terminal failure.
                [ "$step_status" -eq 10 ] && return 10
                mp_setup_state_failure WITNESS_ROUTING_FAILED \
                    "The HA witness did not accept the routing-ready transition." || true
                return 1
            fi
            mp_setup_state_mark witness_ready
            ;;
        root_commissioning_complete)
            # These transitions need an explicit interactive ceremony, a
            # provider-specific input contract, or a browser/peer receipt.
            return 10
            ;;
        *) return 65 ;;
      esac
      step_status=$?
      [ "$step_status" -eq 0 ] || return "$step_status"
    fi
    if [ "$fault_hook_active" = true ]; then
        if [ "$replay_test_receipt" = false ]; then
            [ "${MP_SETUP_TEST_MARK_REQUESTED:-false}" = true ] || return 65
            mp_setup_test_hook_reach_named "$fault_transition" \
                after-side-effect-before-receipt || return $?
            mp_setup_test_hook_record_transition_receipt "$fault_transition" \
                "$checkpoint" "$idempotency_key" || return $?
        fi
        mp_setup_test_hook_reach_named "$fault_transition" \
            after-receipt-before-checkpoint || return $?
        unset MP_SETUP_TEST_DEFER_CHECKPOINT MP_SETUP_TEST_MARK_REQUESTED
        mp_setup_state_mark_now "$checkpoint" || return 1
        mp_setup_test_hook_reach_named "$fault_transition" \
            after-checkpoint-before-next-action || return $?
    fi
    mp_setup_state_update \
        '.machine_transitions[$checkpoint].state="completed"
         | .machine_transitions[$checkpoint].completed_at=$now' \
        --arg checkpoint "$checkpoint" || return 1
    unset MP_SETUP_MACHINE_CHECKPOINT MP_SETUP_MACHINE_IDEMPOTENCY_KEY
    mp_setup_machine_complete_if_plan_finished "$plan"
}

mp_setup_reconcile_unsigned_join() {
    local receiver="$MP_ROOT/runtime/ha-receiver.json" commit receipt
    [ -s "$MP_SETUP_V2_STATE" ] || return 0
    jq -e '
        .format == "mp-opt-setup-state-v2"
        and (.mode | IN("ha-join","replace-node"))
        and .deployment_lane == "unsigned"
        and (
            .state == "in_progress"
            or (
                .state == "complete"
                and (
                    ((.completed // []) | index("application_deployed") == null)
                    or ((.completed // []) | index("replicated") == null)
                    or ((.completed // []) | index("peer_exact_deployment") == null)
                )
            )
        )
        and ((.completed // []) | index("joined") != null)
    ' "$MP_SETUP_V2_STATE" >/dev/null 2>&1 || return 0
    [ -s "$receiver" ] || return 0
    jq -e '
        .format == "mp-opt-receiver-state-v2"
        and (.last_bundle_id | type == "string" and length > 0)
        and (.last_bundle_sha256 | test("^[0-9a-f]{64}$"))
        and (.generation | type == "number" and . >= 1)
    ' "$receiver" >/dev/null 2>&1 || return 1
    commit="$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")"
    receipt="$(jq -r '.current_commit // empty' "$MP_STATE/test-deployments/current.json" 2>/dev/null || true)"
    [ "$receipt" = "$commit" ] || return 0
    mp_setup_verify_exact_environment "$commit" >/dev/null 2>&1 || return 0
    mp_wait_for_stable_local_services 1 1 >/dev/null 2>&1 || return 0
    mp_setup_state_mark application_deployed || return 1
    mp_setup_state_mark replicated || return 1
    mp_setup_state_mark peer_exact_deployment || return 1
    mp_setup_state_complete
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

# Prove that host custody already matches the authoritative application value
# without requiring an active HA lease merely to repeat an identical write.
# A missing or different value still goes through the full standalone/HA sync
# path above, including the active-holder and peer transaction guards.
mp_setup_ensure_commissioning_recipient_current() {
    local recipient current
    mp_compose_init || return 1
    recipient="$("${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d masterplan -Atqc \
        "SELECT value FROM server_settings WHERE key='root_recovery_recipient'" 2>/dev/null || true)"
    [[ "$recipient" =~ ^age1[0-9a-z]+$ ]] || return 1
    current="$(mp_recovery_recipient 2>/dev/null || true)"
    [ "$current" = "$recipient" ] || mp_setup_sync_commissioning_recipient
}

mp_setup_wait_for_root_commissioning() (
    local interval attempt=1 stage label action retired=0
    interval="${MP_ROOT_PASSKEY_POLL_INTERVAL_SECONDS:-5}"
    [[ "$interval" =~ ^[0-9]+$ ]] || interval=5
    trap 'return 130' INT TERM PIPE
    while true; do
        mp_setup_consume_cancellation && return 130
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
    mp_setup_state_action "Root commissioning — Step 1 of 3" \
        ROOT_COMMISSIONING root_commissioning_complete || return 1
    mp_wait_for_public_health 45 || {
        ui_error "The pinned application is not publicly healthy, so root commissioning was not presented."
        return 1
    }
    mp_public_https_get /api/v1/passkey/bootstrap-status \
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
    local commit="$1" short key value candidate manifest_key expected
    short="${commit:0:12}"
    [ "$(sed -n 's/^MP_TEST_COMMIT=//p' "$MP_ROOT/.test-deployment.env" 2>/dev/null | head -1)" = "$commit" ] \
        || { ui_error "The unsigned environment does not match the pinned campaign commit."; return 1; }
    candidate="$MP_STATE/test-deployments/candidate/receipt.json"
    for key in MP_BACKEND_IMAGE MP_CADDY_IMAGE MP_POSTGRES_IMAGE MP_TOOLS_IMAGE; do
        value="$(sed -n "s/^${key}=//p" "$MP_ROOT/.test-deployment.env" | head -1)"
        if [ -s "$candidate" ] && [ "$(jq -r '.commit // empty' "$candidate")" = "$commit" ]; then
            case "$key" in MP_BACKEND_IMAGE) manifest_key=backend ;; MP_CADDY_IMAGE) manifest_key=caddy ;;
                MP_POSTGRES_IMAGE) manifest_key=postgres ;; MP_TOOLS_IMAGE) manifest_key=tools ;; esac
            expected="$(jq -r --arg key "$manifest_key" '.manifest.images[$key] // empty' "$candidate")"
            [ "$value" = "$expected" ] && [[ "$value" =~ @sha256:[0-9a-f]{64}$ ]] \
                || { ui_error "${key} does not match the staged candidate digest."; return 1; }
        else
            [[ "$value" =~ ^masterplan-(backend|caddy|postgres|tools):test-${short}$ ]] \
                || { ui_error "${key} is not pinned to test-${short}."; return 1; }
        fi
        docker image inspect "$value" >/dev/null 2>&1 \
            || { ui_error "Pinned image ${value} is missing."; return 1; }
    done
}

# Prove an already-running signed deployment from immutable local facts. This
# allows setup to recover the narrow window where deployment succeeded but SSH
# ended before the checkpoint was written.
mp_setup_verify_signed_application() {
    local baseline_tag baseline_commit release_tag release_commit service key expected container actual
    MP_SIGNED_DEPLOYMENT_FAILURE_CODE=SIGNED_DEPLOYMENT_FACTS_MISMATCH
    MP_SIGNED_DEPLOYMENT_FAILURE_MESSAGE="The installed signed-release identity does not match the commissioning baseline."
    baseline_tag="$(jq -r '.signed_baseline.tag // empty' "$MP_SETUP_V2_STATE" 2>/dev/null || true)"
    baseline_commit="$(jq -r '.signed_baseline.commit // empty' "$MP_SETUP_V2_STATE" 2>/dev/null || true)"
    release_tag="$(sed -n 's/^MP_RELEASE_TAG=//p' "$MP_ROOT/.release.env" 2>/dev/null | head -1)"
    release_commit="$(sed -n 's/^MP_RELEASE_COMMIT=//p' "$MP_ROOT/.release.env" 2>/dev/null | head -1)"
    [ -n "$baseline_tag" ] && [[ "$baseline_commit" =~ ^[0-9a-f]{40}$ ]] \
        && [ "$release_tag" = "$baseline_tag" ] && [ "$release_commit" = "$baseline_commit" ] \
        || return 1
    mp_compose_init >/dev/null 2>&1 && mp_compose_validate >/dev/null 2>&1 || return 1
    for service in db backend caddy; do
        case "$service" in
            db) key=MP_POSTGRES_IMAGE ;;
            backend) key=MP_BACKEND_IMAGE ;;
            caddy) key=MP_CADDY_IMAGE ;;
        esac
        expected="$(sed -n "s/^${key}=//p" "$MP_ROOT/.release.env" | head -1)"
        [[ "$expected" =~ ^ghcr\.io/brian-funk/masterplanoptimiserv3---server/(backend|caddy|postgres)@sha256:[0-9a-f]{64}$ ]] \
            || return 1
        docker image inspect "$expected" >/dev/null 2>&1 || return 1
        container="$("${MP_COMPOSE[@]}" ps -q "$service" 2>/dev/null)"
        [ -n "$container" ] || return 1
        [ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null)" = true ] || return 1
        actual="$(docker inspect --format '{{.Config.Image}}' "$container" 2>/dev/null)"
        [ "$actual" = "$expected" ] || return 1
    done
    expected="$(sed -n 's/^MP_TOOLS_IMAGE=//p' "$MP_ROOT/.release.env" | head -1)"
    [[ "$expected" =~ ^ghcr\.io/brian-funk/masterplanoptimiserv3---server/tools@sha256:[0-9a-f]{64}$ ]] \
        && docker image inspect "$expected" >/dev/null 2>&1 || return 1
    MP_SIGNED_DEPLOYMENT_FAILURE_CODE=LOCAL_DATABASE_UNHEALTHY
    MP_SIGNED_DEPLOYMENT_FAILURE_MESSAGE="The signed containers are present, but PostgreSQL or the canonical schema is not ready."
    mp_wait_for_database 1 >/dev/null 2>&1 \
        && mp_verify_database_schema_contract >/dev/null 2>&1 || return 2
    MP_SIGNED_DEPLOYMENT_FAILURE_CODE=LOCAL_BACKEND_UNHEALTHY
    MP_SIGNED_DEPLOYMENT_FAILURE_MESSAGE="The signed Backend container is running, but its local health endpoint is unavailable."
    mp_wait_for_backend_health 1 || return 3
    if ! mp_wait_for_caddy_validation 30 >/dev/null 2>&1; then
        MP_SIGNED_DEPLOYMENT_FAILURE_CODE="${MP_CADDY_FAILURE_CODE:-CADDY_VALIDATION_FAILED}"
        MP_SIGNED_DEPLOYMENT_FAILURE_MESSAGE="${MP_CADDY_FAILURE_MESSAGE:-The signed Caddy service could not be validated.}"
        return 4
    fi
    MP_SIGNED_DEPLOYMENT_FAILURE_CODE=ORIGIN_TLS_UNHEALTHY
    MP_SIGNED_DEPLOYMENT_FAILURE_MESSAGE="The local certificate-bound HTTPS origin is unavailable. Public DNS was not used for this check."
    mp_wait_for_origin_tls_health 1 || return 5
    MP_SIGNED_DEPLOYMENT_FAILURE_CODE=""
    MP_SIGNED_DEPLOYMENT_FAILURE_MESSAGE=""
}

mp_setup_record_signed_deployment_failure() {
    local code="${MP_SIGNED_DEPLOYMENT_FAILURE_CODE:-SIGNED_DEPLOYMENT_FACTS_MISMATCH}"
    local message="${MP_SIGNED_DEPLOYMENT_FAILURE_MESSAGE:-The signed deployment could not be verified.}"
    mp_setup_state_failure "$code" "$message" || true
    ui_error "$message"
    return 1
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
        mp_setup_state_action "Recovering exact deployment" \
            UNSIGNED_DEPLOYMENT_RECOVERY application_deployed || return 1
        mp_setup_verify_exact_environment "$commit" || return 1
        mp_compose_init || return 1
        "${MP_COMPOSE[@]}" up -d db backend caddy || return 1
        mp_wait_for_database 30 && mp_verify_database_schema_contract \
            && mp_wait_for_stable_local_services 45 3 \
            || { ui_error "The exact deployment receipt exists, but its database, schema, containers, or local TLS health could not be recovered."; return 1; }
        return 0
    fi
    mp_setup_state_action "Building pinned commit" \
        UNSIGNED_DEPLOYMENT_BUILD application_deployed || return 1
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
        && mp_setup_verify_exact_environment "$commit" \
        && mp_wait_for_stable_local_services 45 3
}

mp_setup_deploy_application() {
    local lane mode
    lane="$(jq -r '.deployment_lane // empty' "$MP_SETUP_V2_STATE")"
    mode="$(jq -r '.mode // empty' "$MP_SETUP_V2_STATE")"
    case "$lane" in
        unsigned) mp_setup_reconcile_unsigned_application ;;
        signed)
            [ -z "$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")" ] \
                || { ui_error "Signed commissioning must not contain a campaign commit."; return 1; }
            if mp_setup_verify_signed_application; then
                return 0
            fi
            if mp_setup_state_has application_deployed; then
                mp_setup_state_action "Recovering signed deployment" \
                    SIGNED_DEPLOYMENT_RECOVERY application_deployed || return 1
                mp_compose_init && "${MP_COMPOSE[@]}" up -d db backend caddy || return 1
            else
                mp_setup_state_action "Deploying signed release" \
                    SIGNED_DEPLOYMENT application_deployed || return 1
                case "$mode" in
                    standalone-new|ha-primary-new)
                        "$MP_ROOT/deploy/deploy.sh" --no-pull --fresh-commissioning
                        ;;
                    *) "$MP_ROOT/deploy/deploy.sh" --no-pull ;;
                esac
            fi
            if ! mp_setup_verify_signed_application; then
                mp_setup_record_signed_deployment_failure
            elif ! mp_wait_for_stable_local_services 30 3; then
                MP_SIGNED_DEPLOYMENT_FAILURE_CODE=LOCAL_SERVICES_UNSTABLE
                MP_SIGNED_DEPLOYMENT_FAILURE_MESSAGE="The signed services did not remain healthy across the commissioning observation window."
                mp_setup_record_signed_deployment_failure
            fi
            ;;
        *) ui_error "The setup deployment lane is invalid."; return 1 ;;
    esac
}

mp_setup_reconcile_primary_campaign_pin() {
    local receipt pinned temporary
    [ "$(jq -r '.deployment_lane // empty' "$MP_SETUP_V2_STATE")" = unsigned ] || return 0
    receipt="$(jq -r '.current_commit // empty' "$MP_STATE/test-deployments/current.json" 2>/dev/null || true)"
    pinned="$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")"
    [ "$receipt" != "$pinned" ] || return 0
    [[ "$receipt" =~ ^[0-9a-f]{40}$ ]] && [[ "$pinned" =~ ^[0-9a-f]{40}$ ]] \
        && ! mp_setup_state_has paired && ! mp_setup_state_has application_deployed \
        && git -C "$MP_ROOT" fetch --no-tags --force origin "$receipt" >/dev/null 2>&1 \
        && [ "$(git -C "$MP_ROOT" rev-parse FETCH_HEAD 2>/dev/null || true)" = "$receipt" ] \
        && git -C "$MP_ROOT" merge-base --is-ancestor "$pinned" "$receipt" >/dev/null 2>&1 \
        || { ui_error "The active unsigned receipt cannot safely fast-forward the unpaired campaign pin."; return 1; }
    mp_setup_state_update '.campaign_commit=$commit' --arg commit "$receipt" || return 1
    if [ -s "$MP_SETUP_V2_PENDING_JOIN" ]; then
        temporary="$(mktemp "$MP_STATE/pending-ha-join.XXXXXX")" || return 1
        jq --arg commit "$receipt" '.campaign_commit=$commit' \
            "$MP_SETUP_V2_PENDING_JOIN" > "$temporary" || { rm -f "$temporary"; return 1; }
        chmod 600 "$temporary"; mv "$temporary" "$MP_SETUP_V2_PENDING_JOIN"
    fi
}

mp_setup_establish_initial_writer_identity() {
    local generation holder
    mp_load_ha_config || return 1
    # Conversion creates the bounded HA node token before the long-running HA
    # services are installed. Establish the authoritative runtime contract
    # now so the one-shot lease agent can use the same validation-only
    # promotion path as the hardened service (notably node_token group-read
    # access for the fixed Backend identity).
    mp_prepare_runtime_permissions \
        || { ui_error "The HA runtime permission contract could not be established before writer activation."; return 1; }
    generation="$(jq -r '.generation // 0' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null || true)"
    holder="$(jq -r '.holder_node_id // empty' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null || true)"
    # A standalone-to-HA conversion installs the node identity before it
    # installs the long-running HA services.  There is therefore no lease
    # agent yet to materialise the witness's bootstrap decision locally.  Ask
    # the real lease agent for exactly one authenticated observation before
    # deciding whether promotion is authorised.  The helper writes the same
    # atomic control receipt used by the service and promotes only when the
    # witness still names this node as the initial holder.
    if ! [[ "$generation" =~ ^[1-9][0-9]*$ ]] || [ "$holder" != "$HA_NODE_ID" ]; then
        MP_ROOT="$MP_ROOT" MP_HA_HOME="$MP_HA_HOME" \
            python3 "$MP_ROOT/deploy/ha/lease_agent.py" --once \
            || { ui_error "The initial witness observation could not be established."; return 1; }
        generation="$(jq -r '.generation // 0' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null || true)"
        holder="$(jq -r '.holder_node_id // empty' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null || true)"
    fi
    [[ "$generation" =~ ^[1-9][0-9]*$ ]] && [ "$holder" = "$HA_NODE_ID" ] \
        || { ui_error "The witness did not authorise this node as the initial database writer."; return 1; }
    "$MP_ROOT/deploy/ha/promote_local.sh" "$generation" \
        || { ui_error "Node A could not establish the witness-authorised database writer identity."; return 1; }
}

mp_setup_activate_converted_unsigned_pair() {
    local registry_credentials="${1:-}" registry_input="${2:-}" commit
    if [ -n "$registry_credentials" ]; then
        [ "$registry_credentials" = --registry-credentials-file ] \
            && [ -n "$registry_input" ] && [ -f "$registry_input" ] \
            && [ ! -L "$registry_input" ] \
            && [ "$(stat -c %u "$registry_input")" = "$(id -u)" ] \
            && [ "$(stat -c %a "$registry_input")" = 600 ] \
            && jq -e '.values.registry | type == "object"
                and ((keys | sort) == ["token","username"])
                and (.username | type == "string" and length >= 1 and length <= 255)
                and (.token | type == "string" and length >= 1 and length <= 4096)' \
                "$registry_input" >/dev/null 2>&1 \
            || { ui_error "The candidate peer registry credentials are unavailable or unsafe."; return 1; }
    else
        [ -z "$registry_input" ] \
            || { ui_error "The candidate peer registry-credential mode is invalid."; return 1; }
    fi
    mp_setup_state_action "Preparing exact images for Node B" \
        PEER_IMAGES_PREPARING application_deployed || return 1
    commit="$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")"
    [ "$(jq -r '.current_commit // empty' "$MP_STATE/test-deployments/current.json" 2>/dev/null || true)" = "$commit" ] \
        || { ui_error "Node A's verified deployment receipt does not match the reconciled campaign pin."; return 1; }
    # A fresh standalone database deliberately has no HA ownership row.  Once
    # the witness has authorised Node A as the initial holder, establish that
    # same writer identity before capturing any first-copy bundle.  The
    # promotion helper is idempotent: a retry with the exact generation is a
    # no-op, while a conflicting witness/database identity is rejected.
    mp_setup_state_action "Establishing initial HA writer identity" \
        HA_WRITER_ESTABLISHING application_deployed || return 1
    mp_setup_establish_initial_writer_identity || return 1
    # Accept the exact image representation already verified for this campaign:
    # legacy local test tags or the current private, digest-pinned candidate
    # references.  The shared verifier binds every value to the staged
    # candidate receipt and proves that the local image exists before anything
    # is copied to Node B.
    mp_setup_verify_exact_environment "$commit" || return 1
    if [ "$registry_credentials" = --registry-credentials-file ]; then
        jq -c '.values.registry' "$registry_input" \
            | "$MP_ROOT/deploy/test-deployment.sh" prepare-peer "$commit" \
                --registry-credentials-stdin || return 1
    else
        "$MP_ROOT/deploy/test-deployment.sh" prepare-peer "$commit" || return 1
    fi
    mp_setup_state_action "Installing Node A HA services" \
        HA_SERVICES_INSTALLING application_deployed || return 1
    "$MP_ROOT/deploy/ha/install_services.sh" || return 1
    mp_setup_state_action "Activating HA routing" \
        WITNESS_ROUTING application_deployed || return 1
    python3 "$MP_ROOT/deploy/ha/witness_control.py" ready >/dev/null || return 1
    mp_prepare_backend_secret_permissions || return 1
    mp_compose_init || return 1
    mp_compose_validate || return 1
    "${MP_COMPOSE[@]}" up -d db backend caddy || return 1
    mp_setup_state_action "Verifying Node A HA health" \
        LOCAL_SERVICES_VALIDATING application_deployed || return 1
    mp_wait_for_database 30 && mp_verify_database_schema_contract && mp_wait_for_local_health 45 \
        || { ui_error "Node A could not activate the reconciled unsigned HA topology."; return 1; }
}

mp_setup_prepare_node_material() {
    local node_id="$1" identity ssh_key public_ip public_ipv6 confirmed observed_ipv4=""
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
    if [ -n "${SSH_CONNECTION:-}" ]; then
        observed_ipv4="$(awk '{print $3}' <<< "$SSH_CONNECTION")"
        python3 -c 'import ipaddress,sys; value=ipaddress.IPv4Address(sys.argv[1]); raise SystemExit(0 if value.is_global else 1)' \
            "$observed_ipv4" >/dev/null 2>&1 || observed_ipv4=""
    fi
    public_ip="$(ui_input "Public address" "Public IPv4 address for this VPS (for example 203.0.113.10)" "$observed_ipv4")" || return 1
    python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "$public_ip" \
        || { ui_error "Enter a valid public IPv4 address."; return 1; }
    [ -z "$observed_ipv4" ] || [ "$public_ip" = "$observed_ipv4" ] || {
        ui_error "The entered address does not match the public IPv4 endpoint of this SSH session (${observed_ipv4}). Reconnect through the address intended for HA, then resume setup."
        return 1
    }
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

# Non-interactive counterpart used by the local automation adapter. Public
# addresses come from the schema-validated stdin document, not prompts.
mp_setup_prepare_node_material_machine() {
    local node_id="$1" public_ip="$2" public_ipv6="${3:-}" identity ssh_key observed_ipv4=""
    mp_require_commands age age-keygen jq openssl ssh ssh-keygen || return 1
    python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "$public_ip" \
        >/dev/null 2>&1 || return 65
    [ -z "$public_ipv6" ] || python3 -c \
        'import ipaddress,sys; ipaddress.IPv6Address(sys.argv[1])' "$public_ipv6" \
        >/dev/null 2>&1 || return 65
    if [ -n "${SSH_CONNECTION:-}" ]; then
        observed_ipv4="$(awk '{print $3}' <<< "$SSH_CONNECTION")"
        python3 -c 'import ipaddress,sys; value=ipaddress.IPv4Address(sys.argv[1]); raise SystemExit(0 if value.is_global else 1)' \
            "$observed_ipv4" >/dev/null 2>&1 || observed_ipv4=""
    fi
    [ -z "$observed_ipv4" ] || [ "$public_ip" = "$observed_ipv4" ] || return 65
    sudo -n install -d -o "$USER" -g "$(id -gn)" -m 0700 \
        "$MP_HA_HOME" "$MP_HA_HOME/secrets" || return 1
    identity="$MP_HA_HOME/secrets/replication_age_identity"
    ssh_key="$HOME/.ssh/mp_opt_ha_peer"
    install -d -m 0700 "$HOME/.ssh"
    [ -s "$identity" ] || { age-keygen -o "$identity" >/dev/null 2>&1 && chmod 600 "$identity"; } \
        || return 1
    [ -s "$ssh_key" ] || ssh-keygen -q -t ed25519 -N '' -C "mp-opt-${node_id}" -f "$ssh_key" \
        || return 1
    chmod 600 "$ssh_key"; chmod 644 "$ssh_key.pub"
    MP_SETUP_NODE_IPV4="$public_ip"; MP_SETUP_NODE_IPV6="$public_ipv6"
    MP_SETUP_NODE_SSH_PUBLIC="$(cat "$ssh_key.pub")"
    MP_SETUP_NODE_SSH_HOST="$(sudo -n cat /etc/ssh/ssh_host_ed25519_key.pub)"
    MP_SETUP_NODE_AGE_RECIPIENT="$(age-keygen -y "$identity")"
    export MP_SETUP_NODE_IPV4 MP_SETUP_NODE_IPV6 MP_SETUP_NODE_SSH_PUBLIC \
        MP_SETUP_NODE_SSH_HOST MP_SETUP_NODE_AGE_RECIPIENT
}

mp_setup_install_peer_trust() {
    local peer_ip="$1" peer_public="$2" peer_host="$3" peer_recipient="$4" verification="${5:-required}"
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
    case "$verification" in
        required)
            ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer true \
                || { ui_error "The peer address did not present the registered host key or accept this node's generated HA key. Verify the peer's public IPv4 address before retrying."; return 1; }
            ;;
        deferred) ;;
        *) return 1 ;;
    esac
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

mp_setup_repair_witness_admin_secret_scoped() (
    local cluster_id="$1" admin_token="$2" deploy_token tools_image worker_name account_id secrets_file="" output=""
    cleanup() {
        mp_secure_remove_file "$secrets_file" || true
        [ -z "$output" ] || rm -f -- "$output"
        unset deploy_token
    }
    trap cleanup EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    deploy_token="$(ui_password "Cloudflare" "Temporary Worker deployment API token")" || return 1
    [ "${#deploy_token}" -ge 32 ] \
        || { ui_error "The Cloudflare token appears incomplete."; return 1; }
    tools_image="$(sed -n 's/^MP_TOOLS_IMAGE=//p' "$MP_ROOT/.release.env" | head -1)"
    [[ "$tools_image" =~ ^ghcr\.io/brian-funk/masterplanoptimiserv3---server/tools@sha256:[0-9a-f]{64}$ ]] \
        || { ui_error "The signed release does not contain the commissioning tools image."; return 1; }
    [ -f "$MP_SETUP_V2_PROVIDER_RESOURCE" ] && [ ! -L "$MP_SETUP_V2_PROVIDER_RESOURCE" ] \
        && [ "$(stat -c '%u:%a' "$MP_SETUP_V2_PROVIDER_RESOURCE" 2>/dev/null)" = "$(id -u):600" ] \
        && jq -e --arg cluster "$cluster_id" '
            .format == "mp-opt-cloudflare-provider-resource-v1"
            and .cluster_id == $cluster
            and (.account_id | test("^[0-9a-f]{32}$"))
            and (.worker_name | test("^[a-z0-9][a-z0-9-]{0,62}$"))
        ' "$MP_SETUP_V2_PROVIDER_RESOURCE" >/dev/null 2>&1 \
        || { ui_error "The exact Cloudflare Worker identity is unavailable; the administrator secret was not changed."; return 1; }
    worker_name="$(jq -r .worker_name "$MP_SETUP_V2_PROVIDER_RESOURCE")"
    account_id="$(jq -r .account_id "$MP_SETUP_V2_PROVIDER_RESOURCE")"
    secrets_file="$(mktemp "$MP_STATE/wrangler-secrets.XXXXXX")" || return 1
    output="$(mktemp "$MP_STATE/wrangler-repair.XXXXXX")" || return 1
    jq -n --arg admin "$admin_token" '{ADMIN_TOKEN:$admin}' > "$secrets_file" \
        && chmod 600 "$secrets_file" || return 1
    docker run --rm \
        -e CLOUDFLARE_API_TOKEN="$deploy_token" \
        -e CLOUDFLARE_ACCOUNT_ID="$account_id" \
        -v "$MP_ROOT/infra/cloudflare-ha-witness:/worker:ro" \
        -v "$secrets_file:/run/mp-opt-witness-secrets.json:ro" \
        "$tools_image" deploy /worker/src/index.ts --config /worker/wrangler.toml \
            --name "$worker_name" --secrets-file /run/mp-opt-witness-secrets.json \
        > "$output" 2>&1 \
        || { ui_text_file "Worker credential repair failed" "$output"; return 1; }
)

mp_setup_repair_witness_admin_secret() {
    local cluster_id="$1" admin_token="$2"
    ui_message "Repair HA witness access" \
        "The deployed witness rejected its protected administrator binding. Re-enter the temporary Worker deployment token to atomically rebind that one secret. The existing long-lived DNS secret is preserved."
    mp_setup_repair_witness_admin_secret_scoped "$cluster_id" "$admin_token"
}

mp_setup_deploy_witness_scoped() (
    local domain="$1" cluster_id="$2" admin_token="${3:-}" deploy_token dns_token worker_name tools_image
    local output="" witness zone_id account_id secrets_file=""
    cleanup() {
        mp_secure_remove_file "$secrets_file" || true
        [ -z "$output" ] || rm -f -- "$output"
        unset deploy_token dns_token admin_token
    }
    trap cleanup EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    deploy_token="$(ui_password "Cloudflare" "Temporary Worker deployment API token")" || return 1
    dns_token="$(ui_password "Cloudflare" "Long-lived zone-scoped DNS Edit + Zone Read API token")" || return 1
    account_id="$(ui_input "Cloudflare account" "Cloudflare account ID (32 lowercase hexadecimal characters)")" || return 1
    [ "${#deploy_token}" -ge 32 ] && [ "${#dns_token}" -ge 32 ] \
        || { ui_error "Both Cloudflare tokens appear incomplete."; return 1; }
    [[ "$account_id" =~ ^[0-9a-f]{32}$ ]] \
        || { ui_error "Enter the exact Cloudflare account ID."; return 1; }
    tools_image="$(sed -n 's/^MP_TOOLS_IMAGE=//p' "$MP_ROOT/.release.env" | head -1)"
    [[ "$tools_image" =~ ^ghcr\.io/brian-funk/masterplanoptimiserv3---server/tools@sha256:[0-9a-f]{64}$ ]] \
        || { unset deploy_token dns_token; ui_error "The signed release does not contain the commissioning tools image."; return 1; }
    zone_id="$(printf '%s' "$dns_token" \
        | python3 "$MP_ROOT/deploy/ha/commission_api.py" zone-id "$domain")" \
        || { unset deploy_token dns_token; ui_error "Cloudflare zone discovery failed. The DNS token needs Zone Read and DNS Edit for this zone."; return 1; }
    worker_name="mp-opt-ha-$(tr -cd 'a-z0-9' <<< "${cluster_id:0:12}")"
    [ -n "$admin_token" ] || admin_token="$(mp_random_secret)"
    secrets_file="$(mktemp "$MP_STATE/wrangler-secrets.XXXXXX")" || return 1
    output="$(mktemp "$MP_STATE/wrangler-deploy.XXXXXX")" || return 1
    jq -n --arg admin "$admin_token" --arg dns "$dns_token" \
        '{ADMIN_TOKEN:$admin,CLOUDFLARE_DNS_API_TOKEN:$dns}' > "$secrets_file" \
        && chmod 600 "$secrets_file" || return 1
    docker run --rm \
        -e CLOUDFLARE_API_TOKEN="$deploy_token" \
        -e CLOUDFLARE_ACCOUNT_ID="$account_id" \
        -v "$MP_ROOT/infra/cloudflare-ha-witness:/worker:ro" \
        -v "$secrets_file:/run/mp-opt-witness-secrets.json:ro" \
        "$tools_image" deploy /worker/src/index.ts --config /worker/wrangler.toml \
            --name "$worker_name" --secrets-file /run/mp-opt-witness-secrets.json \
        > "$output" 2>&1 \
        || { ui_text_file "Worker deployment failed" "$output"; return 1; }
    witness="$(grep -Eo 'https://[^[:space:]]+\.workers\.dev' "$output" | tail -1)"
    [ -n "$witness" ] || witness="$(ui_input "Cloudflare Worker" "Deployed Worker HTTPS URL")" || return 1
    [[ "$witness" =~ ^https://[^[:space:]]+\.workers\.dev/?$ ]] \
        || { ui_error "The deployed Worker URL is invalid."; return 1; }
    jq -cn --arg witness "${witness%/}" --arg zone "$zone_id" --arg admin "$admin_token" \
        --arg account "$account_id" \
        --arg worker "$worker_name" \
        '{witness_url:$witness,zone_id:$zone,admin_token:$admin,
          account_id:$account,worker_name:$worker}'
)

mp_setup_deploy_witness() {
    local domain="$1" cluster_id="$2" admin_token="${3:-}" result
    result="$(mp_setup_deploy_witness_scoped "$domain" "$cluster_id" "$admin_token")" || return 1
    jq -e '.witness_url | test("^https://[^[:space:]]+\\.workers\\.dev$")' \
        <<< "$result" >/dev/null || { unset result; return 1; }
    MP_SETUP_WITNESS_URL="$(jq -r .witness_url <<< "$result")"
    MP_SETUP_ZONE_ID="$(jq -r .zone_id <<< "$result")"
    MP_SETUP_WITNESS_ADMIN_TOKEN="$(jq -r .admin_token <<< "$result")"
    mp_setup_record_cloudflare_resource "$cluster_id" "$(jq -r .account_id <<< "$result")" \
        "$(jq -r .worker_name <<< "$result")" \
        "$MP_SETUP_WITNESS_URL" "$MP_SETUP_ZONE_ID" "$domain" || return 1
    export MP_SETUP_WITNESS_URL MP_SETUP_ZONE_ID MP_SETUP_WITNESS_ADMIN_TOKEN
    unset result
}

mp_setup_deploy_witness_machine() {
    local domain="$1" cluster_id="$2" account_id="$3" deploy_token="$4" dns_token="$5"
    local admin_token="${6:-}" worker_name tools_image output="" witness zone_id secrets_file=""
    cleanup() {
        mp_secure_remove_file "$secrets_file" || true
        [ -z "$output" ] || rm -f -- "$output"
        unset deploy_token dns_token admin_token
    }
    trap cleanup EXIT
    [[ "$account_id" =~ ^[0-9a-f]{32}$ ]] \
        && [ "${#deploy_token}" -ge 32 ] && [ "${#dns_token}" -ge 32 ] || return 65
    tools_image="$(sed -n 's/^MP_TOOLS_IMAGE=//p' "$MP_ROOT/.release.env" | head -1)"
    [[ "$tools_image" =~ ^ghcr\.io/brian-funk/masterplanoptimiserv3---server/tools@sha256:[0-9a-f]{64}$ ]] \
        || return 65
    zone_id="$(printf '%s' "$dns_token" | python3 "$MP_ROOT/deploy/ha/commission_api.py" zone-id "$domain")" \
        || return 1
    worker_name="mp-opt-ha-$(tr -cd 'a-z0-9' <<< "${cluster_id:0:12}")"
    [ -n "$admin_token" ] || admin_token="$(mp_random_secret)"
    secrets_file="$(mktemp "$MP_STATE/wrangler-secrets.XXXXXX")" || return 1
    output="$(mktemp "$MP_STATE/wrangler-deploy.XXXXXX")" || return 1
    jq -n --arg admin "$admin_token" --arg dns "$dns_token" \
        '{ADMIN_TOKEN:$admin,CLOUDFLARE_DNS_API_TOKEN:$dns}' > "$secrets_file" \
        && chmod 600 "$secrets_file" || return 1
    CLOUDFLARE_API_TOKEN="$deploy_token" docker run --rm -e CLOUDFLARE_API_TOKEN \
        -e CLOUDFLARE_ACCOUNT_ID="$account_id" \
        -v "$MP_ROOT/infra/cloudflare-ha-witness:/worker:ro" \
        -v "$secrets_file:/run/mp-opt-witness-secrets.json:ro" \
        "$tools_image" deploy /worker/src/index.ts --config /worker/wrangler.toml \
            --name "$worker_name" --secrets-file /run/mp-opt-witness-secrets.json \
        > "$output" 2>&1 || return 1
    witness="$(grep -Eo 'https://[^[:space:]]+\.workers\.dev' "$output" | tail -1)"
    [[ "$witness" =~ ^https://[^[:space:]]+\.workers\.dev/?$ ]] || return 1
    MP_SETUP_WITNESS_URL="${witness%/}"; MP_SETUP_ZONE_ID="$zone_id"
    MP_SETUP_WITNESS_ADMIN_TOKEN="$admin_token"
    mp_setup_record_cloudflare_resource "$cluster_id" "$account_id" "$worker_name" \
        "$MP_SETUP_WITNESS_URL" "$MP_SETUP_ZONE_ID" "$domain" || return 1
    export MP_SETUP_WITNESS_URL MP_SETUP_ZONE_ID MP_SETUP_WITNESS_ADMIN_TOKEN
    cleanup
    trap - EXIT
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
    spf="$(mp_public_dns_consensus "$domain" TXT | grep -m1 'v=spf1' || true)"
    dmarc="$(mp_public_dns_consensus "_dmarc.$domain" TXT | grep -m1 'v=DMARC1' || true)"
    dkim="$(mp_public_dns_consensus "${selector}._domainkey.$domain" TXT | grep -m1 'v=DKIM1' || true)"
    [ -n "$dkim" ] \
        || dkim="$(mp_public_dns_consensus "${selector}._domainkey.$domain" CNAME | grep -m1 '\.' || true)"
    [ -n "$spf" ] && [ -n "$dmarc" ] && [ -n "$dkim" ] || {
        ui_error "Email delivery authenticated, but public DNS is incomplete. Required records: SPF on ${domain}, DMARC on _dmarc.${domain}, and DKIM on ${selector}._domainkey.${domain}. Publish the exact values supplied by your mail provider, wait for DNS propagation, then resume this checkpoint."
        return 1
    }
    ui_message "Email verified" "SMTP delivery succeeded and public SPF, DKIM and DMARC records are visible. The TUI did not modify mail DNS."
}

# Machine equivalent of the SMTP checkpoint. All values arrive through the
# bounded 0600 commissioning-input file; none are persisted in setup state or
# events. Missing public DNS is a retryable wait, not an interactive prompt.
mp_setup_smtp_delivery_fingerprint() {
    local token_hash
    [ -f "$MP_ROOT/.env" ] && [ ! -L "$MP_ROOT/.env" ] \
        && [ -f "$MP_ROOT/secrets/smtp_token" ] && [ ! -L "$MP_ROOT/secrets/smtp_token" ] \
        || return 1
    token_hash="$(sha256sum "$MP_ROOT/secrets/smtp_token" | awk '{print $1}')" || return 1
    {
        grep -E '^SMTP_(HOST|PORT|USERNAME|SECURITY|FROM_EMAIL|FROM_NAME|REPLY_TO|TIMEOUT_SECONDS)=' \
            "$MP_ROOT/.env" || true
        printf 'SMTP_TOKEN_SHA256=%s\n' "$token_hash"
    } | sha256sum | awk '{print $1}'
}

mp_setup_smtp_delivery_receipt() {
    local idempotency_key="$1" recipient="$2" correlation_id="$3" operation="${4:-read}"
    local key_hash recipient_hash configuration_hash topology receipt temporary state
    [[ "$idempotency_key" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$ ]] || return 65
    [[ "$correlation_id" =~ ^[0-9a-f]{32}$ ]] || return 65
    key_hash="$(printf '%s' "$idempotency_key" | sha256sum | awk '{print $1}')" || return 1
    recipient_hash="$(printf '%s' "$recipient" | sha256sum | awk '{print $1}')" || return 1
    configuration_hash="$(mp_setup_smtp_delivery_fingerprint)" || return 1
    topology="$(mp_ha_role 2>/dev/null || printf standalone)"
    receipt="$MP_STATE/setup-smtp-delivery-${key_hash}.json"
    if [ -e "$receipt" ] || [ -L "$receipt" ]; then
        [ -f "$receipt" ] && [ ! -L "$receipt" ] \
            && [ "$(stat -c '%u:%a' "$receipt" 2>/dev/null)" = "$(id -u):600" ] \
            || return 77
        jq -e --arg key "$key_hash" --arg recipient "$recipient_hash" \
            --arg correlation "$correlation_id" --arg configuration "$configuration_hash" \
            --arg topology "$topology" '
            .format == "mp-opt-setup-smtp-delivery-receipt-v1"
            and .idempotency_key_sha256 == $key
            and .recipient_sha256 == $recipient
            and .correlation_id == $correlation
            and .configuration_sha256 == $configuration
            and .topology == $topology
            and (.state | IN("prepared","accepted"))
            and (.provider_accepted | type == "boolean")
            and (if .state == "accepted" then
                .provider_accepted == true
                and (.accepted_at | type == "string" and length > 0)
              else .provider_accepted == false and .accepted_at == null end)
        ' "$receipt" >/dev/null 2>&1 || return 65
        state="$(jq -r .state "$receipt")"
        case "$operation:$state" in
            read:accepted|accept:accepted) return 0 ;;
            read:prepared|prepare:prepared) return 20 ;;
            accept:prepared)
                temporary="$(mktemp "$MP_STATE/setup-smtp-delivery.XXXXXX")" || return 1
                jq --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
                    .state="accepted" | .provider_accepted=true | .accepted_at=$at
                ' "$receipt" > "$temporary" \
                    && chmod 600 "$temporary" && sync -f "$temporary" 2>/dev/null \
                    && mv "$temporary" "$receipt" && sync -f "$MP_STATE" 2>/dev/null \
                    || { rm -f "$temporary"; return 1; }
                return 0
                ;;
            *) return 65 ;;
        esac
    fi
    [ "$operation" = prepare ] || return 10
    temporary="$(mktemp "$MP_STATE/setup-smtp-delivery.XXXXXX")" || return 1
    jq -n --arg key "$key_hash" --arg recipient "$recipient_hash" \
        --arg correlation "$correlation_id" --arg configuration "$configuration_hash" \
        --arg topology "$topology" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
        {format:"mp-opt-setup-smtp-delivery-receipt-v1",
         idempotency_key_sha256:$key,recipient_sha256:$recipient,
         correlation_id:$correlation,configuration_sha256:$configuration,
         topology:$topology,state:"prepared",provider_accepted:false,
         prepared_at:$at,accepted_at:null}
    ' > "$temporary" \
        && chmod 600 "$temporary" && sync -f "$temporary" 2>/dev/null \
        && mv "$temporary" "$receipt" && sync -f "$MP_STATE" 2>/dev/null \
        || { rm -f "$temporary"; return 1; }
}

mp_setup_verify_smtp_and_dns_machine() {
    local selector="$1" recipient="$2" correlation_id="${3:-}" from_email domain spf dmarc dkim
    local receipt_status=0
    [ -n "$(mp_env_get SMTP_HOST 2>/dev/null || true)" ] || {
        [ -z "$selector" ] && [ -z "$recipient" ]
        return $?
    }
    mp_require_commands dig || return 1
    [[ "$selector" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$ ]] || return 65
    mp_validate_email_address "$recipient" || return 65
    [[ "$correlation_id" =~ ^[0-9a-f]{32}$ ]] || return 65
    mp_setup_smtp_delivery_receipt "$MP_SETUP_MACHINE_IDEMPOTENCY_KEY" \
        "$recipient" "$correlation_id" read || receipt_status=$?
    if [ "$receipt_status" -ne 0 ]; then
        # A prepared receipt means the process may have died after the provider
        # accepted the message but before local acknowledgement was durable.
        # Never resend automatically from that ambiguous boundary.
        [ "$receipt_status" -ne 20 ] || return 20
        [ "$receipt_status" -eq 10 ] || return "$receipt_status"
        mp_setup_smtp_delivery_receipt "$MP_SETUP_MACHINE_IDEMPOTENCY_KEY" \
            "$recipient" "$correlation_id" prepare || return 1
        if [ "$(mp_ha_role 2>/dev/null || printf standalone)" = dynamic ]; then
            mp_ha_verify_smtp_both_nodes required "$recipient" "$correlation_id" || return 1
        else
            mp_send_smtp_test_to "$recipient" "$correlation_id" || return 1
        fi
        mp_setup_smtp_delivery_receipt "$MP_SETUP_MACHINE_IDEMPOTENCY_KEY" \
            "$recipient" "$correlation_id" accept || return 1
    fi
    from_email="$(mp_env_get SMTP_FROM_EMAIL)" || return 1
    domain="${from_email##*@}"
    spf="$(mp_public_dns_consensus "$domain" TXT | grep -m1 'v=spf1' || true)"
    dmarc="$(mp_public_dns_consensus "_dmarc.$domain" TXT | grep -m1 'v=DMARC1' || true)"
    dkim="$(mp_public_dns_consensus "${selector}._domainkey.$domain" TXT | grep -m1 'v=DKIM1' || true)"
    [ -n "$dkim" ] \
        || dkim="$(mp_public_dns_consensus "${selector}._domainkey.$domain" CNAME | grep -m1 '\.' || true)"
    [ -n "$spf" ] && [ -n "$dmarc" ] && [ -n "$dkim" ] || return 10
}

mp_setup_standalone_dns_matches() {
    local domain="$1" public_ip="$2" public_ipv6="${3:-}"
    mp_public_dns_observe "$domain" A "$public_ip" || return $?
    if [ -n "$public_ipv6" ]; then
        mp_public_dns_observe "$domain" AAAA "$public_ipv6" || return $?
    else
        mp_public_dns_observe "$domain" AAAA __absent__ || return $?
    fi
}

mp_setup_wait_for_standalone_dns() (
    local domain="$1" public_ip="$2" public_ipv6="${3:-}" interval attempt=1 address_label=address
    local system_answer
    interval="${MP_DNS_POLL_INTERVAL_SECONDS:-30}"
    [[ "$interval" =~ ^[0-9]+$ ]] || interval=30
    trap 'return 130' INT TERM PIPE
    while ! mp_setup_standalone_dns_matches "$domain" "$public_ip" "$public_ipv6"; do
        mp_setup_consume_cancellation && return 130
        system_answer="$(timeout 3 getent ahostsv4 "$domain" 2>/dev/null | awk '{print $1}' | sort -u | paste -sd, - || true)"
        printf '[%s] Public DNS: %s (%s). Host resolver: %s. Check %d; retrying in %s seconds.\n' \
            "$(date -u +%H:%M:%SZ)" "${MP_PUBLIC_DNS_STATUS:-pending}" \
            "${MP_PUBLIC_DNS_DETAILS:-no public response}" "${system_answer:-no answer}" \
            "$attempt" "$interval"
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
    mp_setup_state_update '.public_routing={ipv4:$ipv4,ipv6:(if $ipv6 == "" then null else $ipv6 end)}' \
        --arg ipv4 "$public_ip" --arg ipv6 "$public_ipv6"
}

# Wait for the public resolver quorum and the exact expected origin addresses.
# This never consults the VPS resolver for a success decision.
mp_setup_wait_for_public_routing() (
    local domain="$1" public_ip="$2" public_ipv6="${3:-}" interval attempt=1
    local code message a_details a_status aaaa_details aaaa_status system_answer dns_result
    interval="${MP_DNS_POLL_INTERVAL_SECONDS:-30}"
    [[ "$interval" =~ ^[0-9]+$ ]] || interval=30
    trap 'return 130' INT TERM PIPE
    while true; do
        mp_setup_consume_cancellation && return 130
        code=""; message=""; dns_result=""
        if mp_public_dns_observe "$domain" A "$public_ip"; then
            a_status=ready
        else
            case "$?" in
                2) code=PUBLIC_DNS_QUORUM_UNAVAILABLE; message="Fewer than two independent public DNS resolvers answered." ;;
                3) code=PUBLIC_DNS_PENDING; message="Public DNS resolvers still disagree." ;;
                4) code=PUBLIC_DNS_PENDING; message="Public A-record propagation is still incomplete." ;;
                5) code=PUBLIC_DNS_MISMATCH; message="Public DNS agrees on an address other than the active Node A address." ;;
                *) code=PUBLIC_DNS_PENDING; message="Public DNS is not ready." ;;
            esac
            a_status="$MP_PUBLIC_DNS_STATUS"
        fi
        a_details="$MP_PUBLIC_DNS_DETAILS"
        if [ -z "$code" ]; then
            if [ -n "$public_ipv6" ]; then
                mp_public_dns_observe "$domain" AAAA "$public_ipv6" || dns_result=$?
            else
                mp_public_dns_observe "$domain" AAAA __absent__ || dns_result=$?
            fi
            aaaa_status="$MP_PUBLIC_DNS_STATUS"; aaaa_details="$MP_PUBLIC_DNS_DETAILS"
            if [ -n "${dns_result:-}" ]; then
                case "$dns_result" in
                    2) code=PUBLIC_DNS_QUORUM_UNAVAILABLE; message="Fewer than two independent public DNS resolvers answered for the AAAA record." ;;
                    3) code=PUBLIC_DNS_PENDING; message="Public DNS resolvers still disagree on the AAAA record." ;;
                    4) code=PUBLIC_DNS_PENDING; message="Public AAAA-record propagation is still incomplete." ;;
                    5) code=PUBLIC_DNS_MISMATCH; message="The public AAAA record does not match the configured Node A IPv6 state." ;;
                    *) code=PUBLIC_DNS_PENDING; message="The public AAAA record is not ready." ;;
                esac
            fi
        else
            aaaa_status=not-checked; aaaa_details=""
        fi
        if [ -z "$code" ] \
            && ! mp_curl_resolved_address "$domain" "$public_ip" /health >/dev/null 2>&1; then
            code=PUBLIC_ROUTE_UNHEALTHY
            message="Public DNS is correct, but the expected IPv4 origin did not pass certificate-bound HTTPS health."
        elif [ -z "$code" ] && [ -n "$public_ipv6" ] \
            && ! mp_curl_resolved_address "$domain" "$public_ipv6" /health >/dev/null 2>&1; then
            code=PUBLIC_ROUTE_UNHEALTHY
            message="Public DNS is correct, but the expected IPv6 origin did not pass certificate-bound HTTPS health."
        fi
        if [ -z "$code" ]; then
            printf '[%s] Public resolver quorum and HTTPS health are ready for %s.\n' \
                "$(date -u +%H:%M:%SZ)" "$domain"
            return 0
        fi
        mp_setup_state_failure "$code" "$message" || return 1
        system_answer="$(timeout 3 getent ahostsv4 "$domain" 2>/dev/null | awk '{print $1}' | sort -u | paste -sd, - || true)"
        printf '[%s] %s A=%s [%s] AAAA=%s [%s] Host resolver=%s. Check %d; retrying in %s seconds.\n' \
            "$(date -u +%H:%M:%SZ)" "$message" "$a_status" "$a_details" \
            "$aaaa_status" "$aaaa_details" "${system_answer:-no answer}" "$attempt" "$interval"
        case "$code" in
            PUBLIC_DNS_MISMATCH|PUBLIC_ROUTE_UNHEALTHY) return 20 ;;
        esac
        sleep "$interval" || return $?
        attempt=$((attempt + 1))
    done
)

mp_setup_verify_public_routing() {
    local domain="$1" public_ip="$2" public_ipv6="${3:-}" code message
    mp_setup_state_action "Waiting for public DNS and HTTPS" \
        PUBLIC_ROUTING_WAIT public_routing_ready || return 1
    ui_run_command "Waiting for public routing" \
        "Checking three independent public resolvers and the exact expected TLS origin every 30 seconds. The VPS resolver is shown only as a diagnostic. Press Ctrl+C or close SSH to pause safely." \
        mp_setup_wait_for_public_routing "$domain" "$public_ip" "$public_ipv6" || {
            code="$(jq -r '.last_failure.code // "PUBLIC_DNS_PENDING"' "$MP_SETUP_V2_STATE" 2>/dev/null || printf PUBLIC_DNS_PENDING)"
            message="$(jq -r '.last_failure.message // "Public routing is not ready."' "$MP_SETUP_V2_STATE" 2>/dev/null || printf 'Public routing is not ready.')"
            case "$code" in
                PUBLIC_DNS_MISMATCH)
                    ui_error "${message} Correct the application-domain DNS record, then resume commissioning. The verified deployment will not run again."
                    ;;
                PUBLIC_ROUTE_UNHEALTHY)
                    ui_error "${message} Verify the public firewall, provider routing and certificate path, then resume commissioning. The verified deployment will not run again."
                    ;;
                *)
                    ui_message "Public routing wait paused" "${code}: ${message}\n\nDeployment remains verified. Resume commissioning to continue DNS and HTTPS checks without redeploying the application."
                    ;;
            esac
            return 1
        }
    mp_setup_state_mark public_routing_ready
}

# Keep the primary's TUI attached to the witness pairing checkpoint.  No
# credential is passed on the command line or written to the display.  A lost
# SSH session simply stops this poller; setup-v2 retains the join checkpoint.
mp_setup_wait_for_peer_join() {
(
    local interval attempt=1 token body response expires_at now
    interval="${MP_SETUP_PEER_POLL_INTERVAL_SECONDS:-5}"
    [[ "$interval" =~ ^[0-9]+$ ]] || interval=5
    mp_load_ha_config || return 1
    token="$(cat "$MP_HA_HOME/secrets/node_token")" || return 1
    body="$(mktemp "$MP_STATE/pair-wait.XXXXXX")" || return 1
    trap 'rm -f "$body"; unset token response; return 130' INT TERM PIPE
    jq -n --arg node "$HA_NODE_ID" '{node_id:$node}' > "$body" || return 1
    while true; do
        mp_setup_consume_cancellation && return 130
        response="$(mp_setup_witness_call pair-state "$HA_WITNESS_URL" \
            "$HA_CLUSTER_ID" "$token" "$body" 2>/dev/null || true)"
        if jq -e '.paired == true' <<< "$response" >/dev/null 2>&1; then
            printf '[%s] Node B pairing is complete. Continuing commissioning.\n' \
                "$(date -u +%H:%M:%SZ)"
            rm -f "$body"; unset token response
            return 0
        fi
        expires_at="$(jq -r '.expires_at // empty' <<< "$response" 2>/dev/null || true)"
        if [ -n "$expires_at" ]; then
            now="$(date -u +%s)"
            if [ "$(date -u -d "$expires_at" +%s 2>/dev/null || printf 0)" -le "$now" ]; then
                printf '[%s] The join code expired before Node B completed pairing.\n' \
                    "$(date -u +%H:%M:%SZ)" >&2
                rm -f "$body"; unset token response
                return 2
            fi
        fi
        printf '[%s] Waiting for Node B (check %d). Retrying in %s seconds.\n' \
            "$(date -u +%H:%M:%SZ)" "$attempt" "$interval"
        sleep "$interval" || return $?
        attempt=$((attempt + 1))
    done
)
}

mp_setup_poll_peer_join_in_tui() {
    ui_run_command "Waiting for Node B" \
        "Leave this window open while Node B consumes the one-time code. MP-OPT checks automatically and continues as soon as pairing is verified. Press Ctrl+C or close SSH to pause safely." \
        mp_setup_wait_for_peer_join
}

mp_setup_primary_create() {
    local mode="$1" domain cluster_id node_token pairing_secret body pending join_code bootstrap_tmp
    local bootstrap_ok attempt bootstrap_error repair_attempted
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
        mp_setup_state_action "Protected configuration" \
            CONFIGURATION_WRITING configuration || return 1
        MP_SETUP_V2_ACTIVE=1 mp_guided_initial_configuration || return 1
        [ -f "$MP_ROOT/.env" ] || {
            ui_message "Commissioning paused" "The configuration review was cancelled. No configuration checkpoint was recorded; resume setup whenever you are ready."
            return 0
        }
        mp_setup_state_mark configuration
    fi
    domain="$(mp_env_get DOMAIN)" || return 1
    if [ "$mode" = convert-ha ] && ! mp_setup_state_has recovery_recipient; then
        mp_setup_state_action "Verifying recovery identity" \
            RECOVERY_IDENTITY_VERIFY recovery_recipient || return 1
        [ -s "$MP_RECIPIENT_FILE" ] || mp_configure_recovery_recipient || return 1
        mp_setup_state_mark recovery_recipient
    fi
    if [ "$mode" = convert-ha ] && ! mp_setup_state_has migration_snapshot; then
        mp_setup_state_action "Creating migration safety snapshot" \
            MIGRATION_SNAPSHOT_CREATE migration_snapshot || return 1
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
        mp_setup_state_action "Deploying HA witness" \
            WITNESS_DEPLOYING witness_bootstrap || return 1
        if [ ! -s "$MP_SETUP_V2_PENDING_BOOTSTRAP" ]; then
            mp_setup_prepare_node_material node-a || return 1
            cluster_id="mp-opt-$(cat /proc/sys/kernel/random/uuid)"
            node_token="$(mp_random_secret)"; pairing_secret="$(mp_random_secret)"
            MP_SETUP_WITNESS_ADMIN_TOKEN="$(mp_random_secret)"
            bootstrap_tmp="$(mktemp "$MP_STATE/pending-witness-bootstrap.XXXXXX")" || return 1
            jq -n --arg cluster "$cluster_id" --arg token "$node_token" \
                --arg pair "$pairing_secret" --arg domain "$domain" \
                --arg ipv4 "$MP_SETUP_NODE_IPV4" --arg ipv6 "$MP_SETUP_NODE_IPV6" \
                --arg ssh "$MP_SETUP_NODE_SSH_PUBLIC" --arg host "$MP_SETUP_NODE_SSH_HOST" \
                --arg age "$MP_SETUP_NODE_AGE_RECIPIENT" \
                --arg admin "$MP_SETUP_WITNESS_ADMIN_TOKEN" \
                '{format:"mp-opt-pending-witness-bootstrap-v2",state:"planned",
                  cluster_id:$cluster,node_token:$token,pairing_secret:$pair,
                  zone_id:null,domain:$domain,witness_url:null,admin_token:$admin,node_a_ipv4:$ipv4,
                  node_a_ipv6:$ipv6,node_a_ssh_public_key:$ssh,
                  node_a_ssh_host_key:$host,node_a_age_recipient:$age}' \
                > "$bootstrap_tmp" || { rm -f "$bootstrap_tmp"; return 1; }
            chmod 600 "$bootstrap_tmp" && sync -f "$bootstrap_tmp" 2>/dev/null \
                && mv "$bootstrap_tmp" "$MP_SETUP_V2_PENDING_BOOTSTRAP" \
                && sync -f "$MP_STATE" 2>/dev/null \
                || { rm -f "$bootstrap_tmp"; return 1; }
            unset node_token pairing_secret MP_SETUP_WITNESS_ADMIN_TOKEN
        fi
        mp_setup_upgrade_pending_witness_bootstrap || return $?
        mp_setup_validate_pending_witness_bootstrap || {
            ui_error "The protected pending Worker bootstrap receipt is invalid. It was retained for manual inspection."
            return 1
        }
        cluster_id="$(jq -r .cluster_id "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
        node_token="$(jq -r .node_token "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
        pairing_secret="$(jq -r .pairing_secret "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
        MP_SETUP_WITNESS_ADMIN_TOKEN="$(jq -r .admin_token "$MP_SETUP_V2_PENDING_BOOTSTRAP")"
        if [ "$(jq -r .state "$MP_SETUP_V2_PENDING_BOOTSTRAP")" = planned ]; then
            mp_setup_deploy_witness "$domain" "$cluster_id" \
                "$MP_SETUP_WITNESS_ADMIN_TOKEN" || return 1
            bootstrap_tmp="$(mktemp "$MP_STATE/pending-witness-bootstrap.XXXXXX")" || return 1
            jq --arg witness "$MP_SETUP_WITNESS_URL" --arg zone "$MP_SETUP_ZONE_ID" \
                --arg admin "$MP_SETUP_WITNESS_ADMIN_TOKEN" \
                '.state="deployed" | .witness_url=$witness | .zone_id=$zone
                 | .admin_token=$admin' "$MP_SETUP_V2_PENDING_BOOTSTRAP" > "$bootstrap_tmp" \
                && chmod 600 "$bootstrap_tmp" && sync -f "$bootstrap_tmp" 2>/dev/null \
                && mv "$bootstrap_tmp" "$MP_SETUP_V2_PENDING_BOOTSTRAP" \
                && sync -f "$MP_STATE" 2>/dev/null || return 1
        fi
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
        mp_setup_state_action "Registering Node A with HA witness" \
            WITNESS_REGISTERING witness_bootstrap || { rm -f "$body"; return 1; }
        bootstrap_ok=false
        repair_attempted=false
        bootstrap_error="$(mktemp "$MP_STATE/witness-bootstrap-error.XXXXXX")" \
            || { rm -f "$body"; return 1; }
        for attempt in $(seq 1 20); do
            if [ "$(jq -r .state "$MP_SETUP_V2_PENDING_BOOTSTRAP")" = registered ]; then
                bootstrap_ok=true
                break
            fi
            if mp_setup_witness_call bootstrap "$MP_SETUP_WITNESS_URL" "$cluster_id" \
                "$MP_SETUP_WITNESS_ADMIN_TOKEN" "$body" >/dev/null 2> "$bootstrap_error"; then
                bootstrap_ok=true
                break
            fi
            if [ "$repair_attempted" = false ] \
                && grep -Eq 'remote API returned HTTP 401([^0-9]|$)' "$bootstrap_error"; then
                mp_setup_repair_witness_admin_secret "$cluster_id" \
                    "$MP_SETUP_WITNESS_ADMIN_TOKEN" \
                    || { rm -f "$body" "$bootstrap_error"; \
                        unset node_token pairing_secret MP_SETUP_WITNESS_ADMIN_TOKEN; return 1; }
                repair_attempted=true
                continue
            fi
            if ! grep -Eq 'remote API returned HTTP (404|429|500|502|503|504)([^0-9]|$)|provider error 1042([^0-9]|$)' \
                "$bootstrap_error"; then
                break
            fi
            [ "$attempt" -eq 20 ] || sleep 3
        done
        [ "$bootstrap_ok" = true ] \
            || { ui_text_file "Worker registration failed" "$bootstrap_error"; \
                rm -f "$body" "$bootstrap_error"; \
                unset node_token pairing_secret MP_SETUP_WITNESS_ADMIN_TOKEN; return 1; }
        bootstrap_tmp="$(mktemp "$MP_STATE/pending-witness-bootstrap.XXXXXX")" || return 1
        jq '.state="registered"' "$MP_SETUP_V2_PENDING_BOOTSTRAP" > "$bootstrap_tmp" \
            && chmod 600 "$bootstrap_tmp" && sync -f "$bootstrap_tmp" 2>/dev/null \
            && mv "$bootstrap_tmp" "$MP_SETUP_V2_PENDING_BOOTSTRAP" \
            && sync -f "$MP_STATE" 2>/dev/null || return 1
        rm -f "$body" "$bootstrap_error"
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
        unset node_token pairing_secret MP_SETUP_WITNESS_ADMIN_TOKEN
    fi
    mp_setup_state_action "Waiting for Node B join" \
        PEER_JOIN_WAIT paired || return 1
    join_code="$(python3 "$MP_ROOT/deploy/ha/pairing.py" encode < "$MP_SETUP_V2_PENDING_JOIN")" || return 1
    ui_copyable_terminal_text "Node B join code" "$join_code" \
        "On the second VPS, start mp-opt, choose Join an existing HA pair with a one-time code, and paste this code within 15 minutes. Return to this window after copying; it will wait and continue automatically." || return 1
    mp_setup_poll_peer_join_in_tui || {
        ui_message "Pairing wait paused" "The protected join checkpoint remains valid. Run mp-opt to display the current code or create its replacement and resume automatic polling."
        return 1
    }
    mp_setup_primary_resume
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
        "$(jq -r .age_recipient <<< "$peer")" deferred || return 1
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
    # This node-local optional credential is deliberately excluded from the
    # first shared bundle, but Compose still requires a safe mount source.
    mp_prepare_node_local_optional_secret_mounts || return 1
    "$MP_ROOT/deploy/ha/install_services.sh" || return 1
    rm -f "$body" "$pair_body" "$MP_SETUP_V2_PENDING_LOCAL_JOIN"; unset pair node_token
    mp_setup_state_mark joined
    if [ "$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")" = unsigned ]; then
        mp_setup_state_action "Waiting for pinned images from Node A" \
            PEER_IMAGES_WAIT replicated
        ui_message "HA node joined" "The one-time code is consumed for ${node_id}. This node is pinned to $(jq -r .campaign_commit "$MP_SETUP_V2_STATE") and is waiting for Node A to transfer and activate those exact images."
    else
        mp_setup_state_action "Waiting for first verified copy from Node A" \
            FIRST_BUNDLE_WAIT replicated
        ui_message "HA node joined" "The one-time code is consumed for ${node_id}. Peer trust and replication-encryption material were installed. Keep this VPS available. The current holder will verify reciprocal SSH before sending the first protected application copy, and setup completes only after that copy is verified here."
    fi
}

# Join Node B using the same protected pending-receipt and receiver scaffold as
# the TUI, with the join code and public addresses supplied by validated stdin.
mp_setup_join_node_machine() {
    local input_file="$1" decoded cluster domain witness pair node_id peer_id
    local node_token body response pair_body peer db_password pending lane campaign policy
    [ ! -f "$MP_ROOT/.env" ] || return 65
    decoded="$(mktemp "$MP_STATE/ha-join.XXXXXX")" || return 1
    printf '%s' "$(jq -r .values.join_code "$input_file")" \
        | python3 "$MP_ROOT/deploy/ha/pairing.py" decode > "$decoded" \
        || { rm -f "$decoded"; return 65; }
    cluster="$(jq -r .cluster_id "$decoded")"; domain="$(jq -r .domain "$decoded")"
    witness="$(jq -r .witness_url "$decoded")"; pair="$(jq -r .pairing_secret "$decoded")"
    node_id="$(jq -r .node_id "$decoded")"; lane="$(jq -r .deployment_lane "$decoded")"
    campaign="$(jq -r '.campaign_commit // empty' "$decoded")"
    [ "$node_id" = node-b ] || { rm -f "$decoded"; return 65; }
    policy="$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)"
    [ "$policy:$lane" = production:signed ] || [ "$policy:$lane" = test:unsigned ] \
        || { rm -f "$decoded"; return 65; }
    mp_setup_state_update '.deployment_lane=$lane
        | .campaign_commit=(if $commit == "" then null else $commit end)' \
        --arg lane "$lane" --arg commit "$campaign" || return 1
    peer_id=node-a
    mp_setup_prepare_node_material_machine "$node_id" \
        "$(jq -r .values.ipv4 "$input_file")" \
        "$(jq -r '.values.ipv6 // empty' "$input_file")" || return 1
    node_token="$(mp_random_secret)"
    pending="$(mktemp "$MP_STATE/pending-local-join.XXXXXX")" || return 1
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
        || return 1
    rm -f "$decoded"
    body="$(mktemp "$MP_STATE/witness-join.XXXXXX")" || return 1
    jq -n --arg pair "$pair" --arg token "$node_token" \
        --arg ipv4 "$MP_SETUP_NODE_IPV4" --arg ipv6 "$MP_SETUP_NODE_IPV6" \
        --arg ssh "$MP_SETUP_NODE_SSH_PUBLIC" --arg host "$MP_SETUP_NODE_SSH_HOST" \
        --arg age "$MP_SETUP_NODE_AGE_RECIPIENT" --arg node "$node_id" \
        '{pairing_secret:$pair,node_id:$node,node_token:$token,ipv4:$ipv4,ipv6:$ipv6,
          ssh_public_key:$ssh,ssh_host_key:$host,age_recipient:$age}' > "$body"
    response="$(mp_setup_witness_call join "$witness" "$cluster" "$pair" "$body")" \
        || { rm -f "$body"; return 1; }
    rm -f "$body"
    jq -e '.joined == true' <<< "$response" >/dev/null || return 1
    mp_setup_install_ha_identity "$node_id" "$peer_id" "$cluster" "$witness" "$node_token" \
        || return 1
    pair_body="$(mktemp "$MP_STATE/pair-state.XXXXXX")" || return 1
    jq -n --arg node "$node_id" '{node_id:$node}' > "$pair_body"
    response="$(mp_setup_witness_call pair-state "$witness" "$cluster" "$node_token" "$pair_body")" \
        || return 1
    rm -f "$pair_body"
    peer="$(jq -c --arg peer "$peer_id" '.nodes[] | select(.node_id == $peer)' <<< "$response")"
    [ -n "$peer" ] || return 1
    mp_setup_install_peer_trust "$(jq -r .ipv4 <<< "$peer")" \
        "$(jq -r .ssh_public_key <<< "$peer")" "$(jq -r .ssh_host_key <<< "$peer")" \
        "$(jq -r .age_recipient <<< "$peer")" deferred || return 1
    db_password="$(openssl rand -hex 32)"
    printf 'DOMAIN=%s\n' "$domain" > "$MP_ROOT/.env" && chmod 600 "$MP_ROOT/.env"
    mkdir -p "$MP_ROOT/secrets" && chmod 700 "$MP_ROOT/secrets"
    printf '%s' "$db_password" > "$MP_ROOT/secrets/database_password"
    : > "$MP_ROOT/secrets/secret_key"; : > "$MP_ROOT/secrets/ip_hmac_key"
    : > "$MP_ROOT/secrets/vapid_private_key"; : > "$MP_ROOT/secrets/root_bootstrap_token"
    : > "$MP_ROOT/secrets/smtp_token"; : > "$MP_ROOT/secrets/evidence_signing_key"
    chmod 600 "$MP_ROOT/secrets/"*
    mp_prepare_node_local_optional_secret_mounts || return 1
    "$MP_ROOT/deploy/ha/install_services.sh" || return 1
    rm -f "$MP_SETUP_V2_PENDING_LOCAL_JOIN"; unset pair node_token db_password
    mp_setup_state_mark joined
    mp_setup_state_action "Waiting for first verified copy from Node A" \
        FIRST_BUNDLE_WAIT replicated
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
    [ "$lane" != signed ] || mp_setup_state_mark application_deployed
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
    local domain cluster witness node_token worker_name account_id deploy_token dns_token admin_token tools_image
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
    account_id="$(ui_input "Cloudflare migration" "Cloudflare account ID")" || return 1
    [ "${#deploy_token}" -ge 32 ] && [ "${#dns_token}" -ge 32 ] \
        || { ui_error "Both Cloudflare tokens appear incomplete."; return 1; }
    [[ "$account_id" =~ ^[0-9a-f]{32}$ ]] \
        || { ui_error "Enter the exact Cloudflare account ID."; return 1; }
    tools_image="$(sed -n 's/^MP_TOOLS_IMAGE=//p' "$MP_ROOT/.release.env" | head -1)"
    [[ "$tools_image" =~ ^ghcr\.io/brian-funk/masterplanoptimiserv3---server/tools@sha256:[0-9a-f]{64}$ ]] \
        || { unset deploy_token dns_token; ui_error "The signed release does not contain the commissioning tools image."; return 1; }
    admin_token="$(mp_random_secret)"
    docker run --rm -e CLOUDFLARE_API_TOKEN="$deploy_token" \
        -e CLOUDFLARE_ACCOUNT_ID="$account_id" \
        -v "$MP_ROOT/infra/cloudflare-ha-witness:/worker:ro" \
        "$tools_image" deploy /worker/src/index.ts --config /worker/wrangler.toml --name "$worker_name" \
        >/dev/null \
        || { unset deploy_token dns_token admin_token; ui_error "The Worker could not be upgraded safely."; return 1; }
    printf '%s' "$admin_token" \
        | docker run --rm -i -e CLOUDFLARE_API_TOKEN="$deploy_token" \
            -e CLOUDFLARE_ACCOUNT_ID="$account_id" \
            "$tools_image" secret put ADMIN_TOKEN --name "$worker_name" >/dev/null \
        || { unset deploy_token dns_token admin_token; ui_error "The Worker administrator secret could not be rotated."; return 1; }
    printf '%s' "$dns_token" \
        | docker run --rm -i -e CLOUDFLARE_API_TOKEN="$deploy_token" \
            -e CLOUDFLARE_ACCOUNT_ID="$account_id" \
            "$tools_image" secret put CLOUDFLARE_DNS_API_TOKEN --name "$worker_name" >/dev/null \
        || { unset deploy_token dns_token admin_token; ui_error "The Worker DNS secret could not be installed."; return 1; }
    zone_id="$(printf '%s' "$dns_token" | python3 "$MP_ROOT/deploy/ha/commission_api.py" zone-id "$domain")" \
        || { unset deploy_token dns_token admin_token; return 1; }
    mp_setup_record_cloudflare_resource "$cluster" "$account_id" "$worker_name" "$witness" \
        "$zone_id" "$domain" || { unset deploy_token dns_token admin_token; return 1; }
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
        -e CLOUDFLARE_ACCOUNT_ID="$account_id" \
        "$tools_image" secret list --name "$worker_name")" \
        || { rm -f "$body"; unset deploy_token dns_token admin_token; ui_error "The Worker secret inventory could not be verified."; return 1; }
    if grep -q 'CLOUDFLARE_API_TOKEN' <<< "$secret_list"; then
        printf 'y\n' \
            | docker run --rm -i -e CLOUDFLARE_API_TOKEN="$deploy_token" \
                -e CLOUDFLARE_ACCOUNT_ID="$account_id" \
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
            if mp_curl_resolved_address "$domain" "$origin" /health >/dev/null; then
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
    if ! mp_public_https_get /health "$domain" >/dev/null; then
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
mp_setup_decommission_witness_after_secret_rotation() {
    local witness="$1" cluster="$2" admin_token="$3" body="$4"
    local error attempt
    error="$(mktemp "$MP_STATE/decommission-witness-error.XXXXXX")" || return 1
    for attempt in $(seq 1 12); do
        if mp_setup_witness_call decommission "$witness" "$cluster" "$admin_token" "$body" \
            >/dev/null 2>"$error"; then
            rm -f "$error"
            return 0
        fi
        # A newly written Worker secret is eventually consistent. Retry only
        # the exact authentication-propagation response; every other provider
        # or witness error remains immediately visible to the caller.
        if ! grep -Eq 'remote API returned HTTP 401([^0-9]|$)' "$error"; then
            cat "$error" >&2
            rm -f "$error"
            return 1
        fi
        [ "$attempt" -eq 12 ] || sleep 5
    done
    cat "$error" >&2
    rm -f "$error"
    return 1
}

mp_setup_decommission_cloudflare() {
    local cluster witness account_id worker_name deploy_token admin_token tools_image body
    mp_load_ha_config || return 1
    [ "$HA_ROLE" = dynamic ] \
        || { ui_error "Cloudflare decommissioning is available only for an HA pair."; return 1; }
    mp_require_active_or_standalone || return 1
    ui_require_phrase "Retire HA Cloudflare resources" \
        "This removes the public A and AAAA records, ACME challenge records, Worker state and Worker. It does not erase either VPS." \
        "DECOMMISSION HA CLOUDFLARE" || return 0
    cluster="$HA_CLUSTER_ID"; witness="$HA_WITNESS_URL"
    mp_setup_load_cloudflare_resource "$cluster" "$witness" "$(mp_env_get DOMAIN)" \
        || { ui_error "The exact Cloudflare Worker identity is not recorded on this node. Refusing provider deletion; recover the protected provider-resource receipt first."; return 1; }
    worker_name="$(jq -r .worker_name "$MP_SETUP_V2_PROVIDER_RESOURCE")"
    account_id="$(jq -r .account_id "$MP_SETUP_V2_PROVIDER_RESOURCE")"
    deploy_token="$(ui_password "Cloudflare" "Temporary Worker deployment API token")" || return 1
    [ "${#deploy_token}" -ge 32 ] \
        || { unset deploy_token; ui_error "The Cloudflare token appears incomplete."; return 1; }
    tools_image="$(sed -n 's/^MP_TOOLS_IMAGE=//p' "$MP_ROOT/.release.env" | head -1)"
    [[ "$tools_image" =~ ^ghcr\.io/brian-funk/masterplanoptimiserv3---server/tools@sha256:[0-9a-f]{64}$ ]] \
        || { unset deploy_token; ui_error "The signed release does not contain the commissioning tools image."; return 1; }
    admin_token="$(mp_random_secret)"
    printf '%s' "$admin_token" \
        | docker run --rm -i -e CLOUDFLARE_API_TOKEN="$deploy_token" \
            -e CLOUDFLARE_ACCOUNT_ID="$account_id" \
            "$tools_image" secret put ADMIN_TOKEN --name "$worker_name" >/dev/null \
        || { unset deploy_token admin_token; ui_error "The Worker administrator secret could not be rotated."; return 1; }
    body="$(mktemp "$MP_STATE/decommission-cloudflare.XXXXXX")" || return 1
    jq -n --arg cluster "$cluster" '{confirm_cluster_id:$cluster}' > "$body"
    mp_setup_decommission_witness_after_secret_rotation \
        "$witness" "$cluster" "$admin_token" "$body" >/dev/null \
        || { rm -f "$body"; unset deploy_token admin_token; ui_error "The Worker did not confirm DNS and state deletion."; return 1; }
    rm -f "$body"; unset admin_token
    printf 'y\n' | docker run --rm -i -e CLOUDFLARE_API_TOKEN="$deploy_token" \
        -e CLOUDFLARE_ACCOUNT_ID="$account_id" \
        "$tools_image" delete --name "$worker_name" >/dev/null \
        || { unset deploy_token; ui_error "Worker state was erased, but the Worker deployment still needs manual deletion."; return 1; }
    unset deploy_token
    mp_audit "ha.cloudflare-decommission" "success" "dns-worker-state-removed"
    ui_message "Cloudflare resources retired" "The HA DNS records, Worker state and Worker were removed. The VPS installations were not changed."
}

mp_setup_decommission_cloudflare_machine() {
    local deploy_token="$1" expected_account="$2" expected_worker="$3" expected_zone="$4"
    local cluster witness account_id worker_name zone_id admin_token tools_image body receipt probe
    local worker_present=false reconciled=false temporary
    [ "$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)" = test ] || return 77
    mp_load_ha_config || return 1
    [ "$HA_ROLE" = dynamic ] || return 65
    [ "${#deploy_token}" -ge 32 ] || return 65
    cluster="$HA_CLUSTER_ID"; witness="$HA_WITNESS_URL"
    mp_setup_load_cloudflare_resource "$cluster" "$witness" "$(mp_env_get DOMAIN)" || return 65
    worker_name="$(jq -r .worker_name "$MP_SETUP_V2_PROVIDER_RESOURCE")"
    account_id="$(jq -r .account_id "$MP_SETUP_V2_PROVIDER_RESOURCE")"
    zone_id="$(jq -r .zone_id "$MP_SETUP_V2_PROVIDER_RESOURCE")"
    [ "$account_id" = "$expected_account" ] && [ "$worker_name" = "$expected_worker" ] \
        && [ "$zone_id" = "$expected_zone" ] \
        || return 65
    receipt="$MP_STATE/provider-cleanup-${cluster}.json"
    if [ -e "$receipt" ] || [ -L "$receipt" ]; then
        [ -f "$receipt" ] && [ ! -L "$receipt" ] \
            && [ "$(stat -c '%u:%a' "$receipt" 2>/dev/null)" = "$(id -u):600" ] \
            || return 77
        jq -e --arg cluster "$cluster" --arg account "$account_id" --arg worker "$worker_name" \
            --arg witness "$witness" --arg zone "$zone_id" '
            .format == "mp-opt-provider-cleanup-receipt-v1"
            and .cluster_id == $cluster and .account_id == $account and .worker_name == $worker
            and .witness_url == $witness and .zone_id == $zone
            and .witness_state_deleted == true
            and (.worker_deleted | type == "boolean")
            and (.witness_state_deleted_at | type == "string" and length > 0)
        ' "$receipt" >/dev/null 2>&1 || return 65
    fi
    tools_image="$(sed -n 's/^MP_TOOLS_IMAGE=//p' "$MP_ROOT/.release.env" | head -1)"
    [[ "$tools_image" =~ ^ghcr\.io/brian-funk/masterplanoptimiserv3---server/tools@sha256:[0-9a-f]{64}$ ]] \
        || return 65
    if [ ! -s "$receipt" ]; then
        admin_token="$(mp_random_secret)"
        printf '%s' "$admin_token" | CLOUDFLARE_API_TOKEN="$deploy_token" \
            docker run --rm -i -e CLOUDFLARE_API_TOKEN \
                -e CLOUDFLARE_ACCOUNT_ID="$account_id" "$tools_image" \
                secret put ADMIN_TOKEN --name "$worker_name" >/dev/null || return 1
        body="$(mktemp "$MP_STATE/decommission-cloudflare.XXXXXX")" || return 1
        jq -n --arg cluster "$cluster" '{confirm_cluster_id:$cluster}' > "$body"
        mp_setup_decommission_witness_after_secret_rotation \
            "$witness" "$cluster" "$admin_token" "$body" >/dev/null \
            || { rm -f "$body"; unset admin_token; return 1; }
        rm -f "$body"; unset admin_token
        temporary="$(mktemp "$MP_STATE/provider-cleanup-receipt.XXXXXX")" || return 1
        jq -n --arg cluster "$cluster" --arg account "$account_id" --arg worker "$worker_name" \
            --arg witness "$witness" --arg zone "$zone_id" \
            --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '{format:"mp-opt-provider-cleanup-receipt-v1",cluster_id:$cluster,account_id:$account,
              worker_name:$worker,witness_url:$witness,zone_id:$zone,
              witness_state_deleted:true,worker_deleted:false,
              witness_state_deleted_at:$at}' > "$temporary" \
            && chmod 600 "$temporary" && sync -f "$temporary" 2>/dev/null \
            && mv "$temporary" "$receipt" \
            || { rm -f "$temporary"; return 1; }
        sync -f "$receipt" 2>/dev/null || return 1
        sync -f "$MP_STATE" 2>/dev/null || return 1
    fi
    if ! jq -e '.worker_deleted == true' "$receipt" >/dev/null 2>&1; then
        # The witness-state receipt is durable before Worker deletion. A retry
        # observes the exact Worker first, so a lost acknowledgement can never
        # cause an unexamined second destructive request.
        probe="$(mktemp "$MP_STATE/provider-worker-probe.XXXXXX")" || return 1
        if printf '%s' "$deploy_token" \
            | python3 "$MP_ROOT/deploy/ha/cloudflare_worker_script.py" \
                observe "$account_id" "$worker_name" > /dev/null 2> "$probe"; then
            worker_present=true
        elif [ "${PIPESTATUS[1]}" -eq 4 ]; then
            reconciled=true
        else
            rm -f "$probe"
            return 1
        fi
        rm -f "$probe"
        if [ "$worker_present" = true ]; then
            printf '%s' "$deploy_token" \
                | python3 "$MP_ROOT/deploy/ha/cloudflare_worker_script.py" \
                    delete "$account_id" "$worker_name" >/dev/null \
                || return 1
        fi
        temporary="$(mktemp "$MP_STATE/provider-cleanup-receipt.XXXXXX")" || return 1
        jq --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson reconciled "$reconciled" \
            '.worker_deleted=true | .worker_deleted_at=$at
             | .worker_deletion_reconciled=$reconciled' "$receipt" > "$temporary" \
            && chmod 600 "$temporary" && sync -f "$temporary" 2>/dev/null \
            && mv "$temporary" "$receipt" || { rm -f "$temporary"; return 1; }
        sync -f "$receipt" 2>/dev/null || return 1
        sync -f "$MP_STATE" 2>/dev/null || return 1
    fi
    unset deploy_token
    jq '{format,cluster_id,account_id,worker_name,witness_url,zone_id,witness_state_deleted,worker_deleted,
         witness_state_deleted_at,worker_deleted_at,
         worker_deletion_reconciled:(.worker_deletion_reconciled // false)}' "$receipt"
}

# Bind Node A's commissioning checkpoint to the exact durable sender receipt
# and Node B's independently written receiver receipt. This makes an SSH loss
# after acknowledgement resumable without sending a second full copy.
mp_setup_record_first_verified_bundle() {
    local sender_file="$MP_ROOT/runtime/ha-last-accepted-bundle.json"
    local sender receiver bundle sha256 generation accepted_at release
    mp_load_ha_config || return 1
    release="$(mp_release_hash)" || return 1
    [ -s "$sender_file" ] && [ -f "$sender_file" ] && [ ! -L "$sender_file" ] \
        || return 1
    sender="$(cat "$sender_file")" || return 1
    receiver="$(ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
        -o ClearAllForwardings=yes "$HA_PEER_SSH" \
        'cat /opt/masterplan/runtime/ha-receiver.json' 2>/dev/null)" || return 1
    jq -e --arg source "$HA_NODE_ID" --arg target "$HA_PEER_NODE_ID" \
        --arg cluster "$HA_CLUSTER_ID" --arg release "$release" '
        .format == "mp-opt-ha-sender-acceptance-v1"
        and .source_node_id == $source and .target_node_id == $target
        and .cluster_id == $cluster and .release_hash == $release
        and (.bundle_id | type == "string" and length > 0)
        and (.sha256 | test("^[0-9a-f]{64}$"))
        and (.generation | type == "number" and . >= 1)
        and (.accepted_at | type == "string" and length > 0)
    ' <<< "$sender" >/dev/null || return 1
    jq -e --arg source "$HA_NODE_ID" --arg target "$HA_PEER_NODE_ID" \
        --arg cluster "$HA_CLUSTER_ID" --arg release "$release" '
        .format == "mp-opt-receiver-state-v2" and .source_node_id == $source
        and .target_node_id == $target
        and .cluster_id == $cluster and .release_hash == $release
        and (.last_bundle_id | type == "string" and length > 0)
        and (.last_bundle_sha256 | test("^[0-9a-f]{64}$"))
        and (.generation | type == "number" and . >= 1)
        and (.last_received_at | type == "string" and length > 0)
    ' <<< "$receiver" >/dev/null || return 1
    bundle="$(jq -r .bundle_id <<< "$sender")"
    sha256="$(jq -r .sha256 <<< "$sender")"
    generation="$(jq -r .generation <<< "$sender")"
    [ "$bundle" = "$(jq -r .last_bundle_id <<< "$receiver")" ] \
        && [ "$sha256" = "$(jq -r .last_bundle_sha256 <<< "$receiver")" ] \
        && [ "$generation" = "$(jq -r .generation <<< "$receiver")" ] \
        || return 1
    accepted_at="$(jq -r .last_received_at <<< "$receiver")"
    if jq -e --arg bundle "$bundle" --arg sha256 "$sha256" \
        --argjson generation "$generation" --arg accepted "$accepted_at" '
        .first_verified_bundle == {
          bundle_id:$bundle,sha256:$sha256,generation:$generation,accepted_at:$accepted
        }
    ' "$MP_SETUP_V2_STATE" >/dev/null; then
        return 0
    fi
    mp_setup_state_update \
        '.first_verified_bundle={bundle_id:$bundle,sha256:$sha256,
          generation:$generation,accepted_at:$accepted}' \
        --arg bundle "$bundle" --arg sha256 "$sha256" \
        --argjson generation "$generation" --arg accepted "$accepted_at"
}

# Initial deployment may take longer than the witness transition lease. Reuse
# the normal lease-agent iteration so routing activation is preceded by a
# fresh, authenticated health heartbeat and the local writer promotion. This
# preserves the Worker's expired-lease guard instead of bypassing it.
mp_setup_activate_initial_witness_routing() {
    mp_load_ha_config || return 1
    # Root commissioning can legitimately exceed the 90-second witness
    # freshness window. Start only the lease observer here; replication and
    # snapshot triggers remain disabled until ha_services_activated.
    "$MP_ROOT/deploy/ha/install_services.sh" --commissioning || return 1
    python3 "$MP_ROOT/deploy/ha/lease_agent.py" --once >/dev/null || return 1
    jq -e --arg node "$HA_NODE_ID" \
        '.holder_node_id == $node and .routing_ready == true' \
        "$MP_ROOT/runtime/ha-control.json" >/dev/null 2>&1
}

# Prove the receiver's active candidate identity immediately before an initial
# unsigned copy. The receiver must not infer this from a staged bundle alone:
# its release hash is derived from the installed deployment environment.
mp_setup_verify_unsigned_peer_identity() {
    local mode lane commit
    mode="$(jq -r '.mode // empty' "$MP_SETUP_V2_STATE")" || return 1
    lane="$(jq -r '.deployment_lane // empty' "$MP_SETUP_V2_STATE")" || return 1
    [[ "$mode" =~ ^(ha-primary-new|convert-ha|replace-primary)$ ]] \
        && [ "$lane" = unsigned ] || return 0
    commit="$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")" || return 1
    [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || return 1
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
        env MP_ROOT=/opt/masterplan MP_TEST_PEER=1 \
        /opt/masterplan/deploy/test-deployment.sh internal-verify-peer-identity "$commit"
}

# A retry may arrive after an older supervisor recorded application_deployed
# on the surviving node before the replacement peer had received its exact
# frontend and CSP assets. Repair that pre-copy state from the already verified
# local candidate. No registry access is needed because candidate activation
# has already proved and retained all four image digests on this node.
mp_setup_prepare_unsigned_replacement_peer_if_needed() {
    local mode lane commit
    mode="$(jq -r '.mode // empty' "$MP_SETUP_V2_STATE")" || return 1
    lane="$(jq -r '.deployment_lane // empty' "$MP_SETUP_V2_STATE")" || return 1
    [ "$mode" = replace-primary ] && [ "$lane" = unsigned ] || return 0
    if mp_setup_verify_unsigned_peer_identity >/dev/null 2>&1; then
        return 0
    fi
    commit="$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")" || return 1
    [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || return 1
    "$MP_ROOT/deploy/test-deployment.sh" prepare-peer "$commit"
}

# A fresh or replacement unsigned peer is deliberately staged before shared
# configuration is available. The first accepted replication bundle activates
# that staged candidate. Record the exact candidate receipt only after sender
# and receiver have proved that they accepted the same bundle.
mp_setup_finalize_fresh_unsigned_peer() {
    local mode lane commit
    mode="$(jq -r '.mode // empty' "$MP_SETUP_V2_STATE")" || return 1
    lane="$(jq -r '.deployment_lane // empty' "$MP_SETUP_V2_STATE")" || return 1
    [[ "$mode" =~ ^(ha-primary-new|replace-primary)$ ]] \
        && [ "$lane" = unsigned ] || return 0
    commit="$(jq -r '.campaign_commit // empty' "$MP_SETUP_V2_STATE")" || return 1
    [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || return 1
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
        env MP_ROOT=/opt/masterplan MP_TEST_PEER=1 \
        /opt/masterplan/deploy/test-deployment.sh internal-finalize-peer "$commit"
}

mp_setup_primary_resume() {
    local cluster witness token body response peer current mode pairing_expires pairing_secret join_code domain pending commit
    local public_ip public_ipv6
    [ -s "$MP_SETUP_V2_PENDING_JOIN" ] || { ui_error "The protected pending join record is missing."; return 1; }
    mp_setup_reconcile_primary_campaign_pin || return 1
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
                || {
                    rm -f "$body"
                    ui_message "Join code renewal pending" \
                        "The protected pending join value is incompatible and cannot be displayed. It expires at ${pairing_expires}. Resume setup after that time to replace it with a new URL-safe one-time code; no application data or routing changed."
                    return 0
                }
            rm -f "$body"
            ui_copyable_terminal_text "Node join code" "$join_code" \
                "This code remains valid until ${pairing_expires}. Paste it on the joining VPS, then return to this window; it will wait and continue automatically." || return 1
            mp_setup_poll_peer_join_in_tui || {
                ui_message "Pairing wait paused" "The protected join checkpoint remains valid. Run mp-opt to resume automatic polling or replace an expired code."
                return 1
            }
            mp_setup_primary_resume
            return $?
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
            "The previous code expired. Paste this new 15-minute one-time code on the joining VPS, then return to this window; it will wait and continue automatically." || return 1
        mp_setup_poll_peer_join_in_tui || {
            ui_message "Pairing wait paused" "The protected replacement checkpoint remains valid. Run mp-opt to resume automatic polling."
            return 1
        }
        mp_setup_primary_resume
        return $?
    fi
    peer="$(jq -c --arg peer "$HA_PEER_NODE_ID" '.nodes[] | select(.node_id == $peer)' <<< "$response")"
    [ -n "$peer" ] || { rm -f "$body"; ui_error "The witness did not return the expected peer metadata."; return 1; }
    current="$(jq -c --arg node "$HA_NODE_ID" '.nodes[] | select(.node_id == $node)' <<< "$response")"
    [ -n "$current" ] || { rm -f "$body"; ui_error "The witness did not return this node's routing metadata."; return 1; }
    public_ip="$(jq -r '.ipv4 // empty' <<< "$current")"
    public_ipv6="$(jq -r '.ipv6 // empty' <<< "$current")"
    python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "$public_ip" >/dev/null 2>&1 \
        || { rm -f "$body"; ui_error "The witness returned an invalid Node A IPv4 address."; return 1; }
    [ -z "$public_ipv6" ] \
        || python3 -c 'import ipaddress,sys; ipaddress.IPv6Address(sys.argv[1])' "$public_ipv6" >/dev/null 2>&1 \
        || { rm -f "$body"; ui_error "The witness returned an invalid Node A IPv6 address."; return 1; }
    mp_setup_install_peer_trust "$(jq -r .ipv4 <<< "$peer")" \
        "$(jq -r .ssh_public_key <<< "$peer")" "$(jq -r .ssh_host_key <<< "$peer")" \
        "$(jq -r .age_recipient <<< "$peer")" || return 1
    if [ "$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")" = unsigned ]; then
        commit="$(jq -r .campaign_commit "$MP_SETUP_V2_STATE")"
        ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
            env MP_ROOT=/opt/masterplan MP_TEST_PEER=1 \
            /opt/masterplan/deploy/test-deployment.sh internal-repin-setup "$commit" \
            || { ui_error "Node B could not record Node A's verified fast-forwarded campaign pin."; return 1; }
    fi
    mp_setup_state_has paired || mp_setup_state_mark paired
    rm -f "$MP_SETUP_V2_PENDING_BOOTSTRAP"
    if ! mp_setup_state_has application_deployed; then
        if [ "$mode" = convert-ha ] && [ -f "$MP_ROOT/infra/docker-compose.override.yml" ]; then
            mp_ha_convert_host_caddy || return 1
        fi
        if [ "$mode" = replace-primary ] \
            && [ "$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")" = unsigned ]; then
            prepare_initial_peer "$(jq -r .campaign_commit "$MP_SETUP_V2_STATE")" \
                || { ui_error "Node B could not install and verify the exact candidate identity."; return 1; }
        elif [ "$mode" = convert-ha ] \
            && [ "$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")" = unsigned ]; then
            mp_setup_activate_converted_unsigned_pair || return 1
        else
            mp_setup_deploy_application || return 1
        fi
        mp_setup_state_has application_deployed || mp_setup_state_mark application_deployed
    fi
    if ! mp_setup_state_has witness_ready; then
        mp_setup_state_action "Activating public HA routing" \
            WITNESS_ROUTING witness_ready || return 1
        if mp_setup_activate_initial_witness_routing; then
            :
        else
            status=$?
            [ "$status" -eq 10 ] && return 10
            mp_setup_state_failure WITNESS_ROUTING_FAILED \
                "The HA witness did not accept the routing-ready transition." || true
            return 1
        fi
        mp_setup_state_mark witness_ready
    fi
    if ! mp_setup_state_has public_routing_ready; then
        domain="$(mp_env_get DOMAIN)" || return 1
        mp_setup_state_update '.public_routing={ipv4:$ipv4,ipv6:(if $ipv6 == "" then null else $ipv6 end)}' \
            --arg ipv4 "$public_ip" --arg ipv6 "$public_ipv6" || return 1
        mp_setup_verify_public_routing "$domain" "$public_ip" "$public_ipv6" || return 1
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
        mp_setup_state_action "Replicating complete application state to Node B" \
            FIRST_BUNDLE_TRANSFER replicated || return 1
        if ! mp_setup_record_first_verified_bundle; then
            mp_setup_prepare_unsigned_replacement_peer_if_needed || {
                mp_setup_state_failure PEER_CANDIDATE_PREPARATION_FAILED \
                    "Node B could not reinstall and verify the exact candidate runtime assets, so no replication bundle was sent." || true
                return 1
            }
            mp_setup_verify_unsigned_peer_identity || {
                mp_setup_state_failure PEER_CANDIDATE_IDENTITY_MISMATCH \
                    "Node B is not bound to the exact candidate identity, so no replication bundle was sent." || true
                return 1
            }
            mp_ha_replicate_now || return 1
            mp_setup_record_first_verified_bundle || {
                mp_setup_state_failure FIRST_BUNDLE_RECEIPT_MISMATCH \
                    "Node B may have accepted a copy, but the sender and receiver receipts do not identify the same bundle." || true
                return 1
            }
        fi
        mp_setup_finalize_fresh_unsigned_peer || {
            ui_error "Node B accepted the first copy but could not record the exact candidate receipt."
            return 1
        }
        mp_setup_state_mark replicated
    fi
    if ! mp_setup_state_has ha_services_activated; then
        mp_setup_state_action "Activating verified HA services" \
            HA_SERVICES_ACTIVATING ha_services_activated || return 1
        "$MP_ROOT/deploy/ha/install_services.sh" || {
            mp_setup_state_failure HA_SERVICE_ACTIVATION_FAILED \
                "The first copy is safely accepted, but the local HA services did not activate." || true
            return 1
        }
        mp_setup_state_mark ha_services_activated
    fi
    if [ "$mode" = convert-ha ] \
        && [ "$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")" = unsigned ] \
        && ! mp_setup_state_has peer_exact_deployment; then
        commit="$(jq -r .campaign_commit "$MP_SETUP_V2_STATE")"
        mp_setup_state_action "Finalising Node B exact deployment" \
            PEER_EXACT_DEPLOYMENT_FINALISING peer_exact_deployment || return 1
        ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
            env MP_ROOT=/opt/masterplan MP_TEST_PEER=1 \
            /opt/masterplan/deploy/test-deployment.sh internal-finalize-peer "$commit" \
            || { ui_error "Node B accepted the replication bundle but could not record the exact deployment receipt."; return 1; }
        mp_setup_state_mark peer_exact_deployment
    fi
    if ! mp_setup_state_has validated; then
        mp_setup_state_action "Validating the complete installation" \
            INSTALLATION_VALIDATING validated || return 1
        mp_validate_installation || return 1
        mp_setup_state_mark validated
    fi
    if ! mp_setup_state_has smtp_verified; then
        mp_setup_state_action "Verifying SMTP and DNS after HA conversion" \
            SMTP_VALIDATING smtp_verified || return 1
        mp_setup_verify_smtp_and_dns || return 1
        mp_setup_state_mark smtp_verified
    fi
    if ! mp_setup_state_has automatic_failover_readiness; then
        mp_setup_state_action "Verifying automatic failover readiness" \
            AUTOMATIC_FAILOVER_VALIDATING automatic_failover_readiness || return 1
        mp_setup_verify_automatic_failover_readiness || return $?
        mp_setup_state_mark automatic_failover_readiness
    fi
    rm -f "$body" "$MP_SETUP_V2_PENDING_JOIN"; unset token
    mp_setup_state_complete
    if [ "$mode" = convert-ha ]; then
        ui_message "HA conversion complete" "${HA_NODE_ID} is primary. ${HA_PEER_NODE_ID} accepted the current complete encrypted copy and all readiness gates passed. Automatic failover remains disabled until it is explicitly enabled through the guarded High availability action after handover and failover testing. High availability changes the deployment facts. Review Policies & notices at https://$(mp_env_get DOMAIN)/admin/governance, save the authoritative HA state, review the exact diff and publish a new policy version."
    else
        ui_message "HA commissioning complete" "${HA_NODE_ID} is primary. ${HA_PEER_NODE_ID} accepted the current complete encrypted copy and all readiness gates passed. Automatic failover remains disabled until it is explicitly enabled through the guarded High availability action after handover and failover testing. Open https://$(mp_env_get DOMAIN)/admin/governance to publish the controller-specific legal centre."
    fi
}

mp_setup_standalone() {
    local public_ip public_ipv6
    if [ ! -f "$MP_SETUP_V2_STATE" ] && [ -f "$MP_ROOT/.env" ]; then
        ui_error "This server is already configured. Use the normal Configuration or Deploy menus instead of starting a fresh installation."
        return 1
    fi
    mp_setup_state_begin standalone-new || return 1
    mp_setup_state_has signed_baseline_verified || mp_setup_install_signed_release || return 1
    if ! mp_setup_state_has configuration; then
        mp_setup_state_action "Protected configuration" \
            CONFIGURATION_WRITING configuration || return 1
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
    if ! mp_setup_state_has public_routing_ready; then
        public_ip="$(jq -r '.public_routing.ipv4 // empty' "$MP_SETUP_V2_STATE")"
        public_ipv6="$(jq -r '.public_routing.ipv6 // empty' "$MP_SETUP_V2_STATE")"
        [ -n "$public_ip" ] || {
            mp_setup_state_failure PUBLIC_DNS_METADATA_MISSING \
                "The verified public address metadata is missing; a clean commissioning restart is required." || true
            return 1
        }
        mp_setup_verify_public_routing "$(mp_env_get DOMAIN)" "$public_ip" "$public_ipv6" || return 1
    fi
    if ! mp_setup_state_has root_commissioning_complete; then
        mp_setup_register_root_passkey || return 1
        mp_setup_state_mark root_commissioning_complete
    fi
    mp_setup_state_mark recovery_recipient
    if ! mp_setup_state_has validated; then
        mp_setup_state_action "Validating the complete installation" \
            INSTALLATION_VALIDATING validated || return 1
        mp_validate_installation || return 1
        mp_setup_state_mark validated
    fi
    if ! mp_setup_state_has smtp_verified; then
        mp_setup_state_action "Verifying SMTP and DNS" \
            SMTP_VALIDATING smtp_verified || return 1
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
    if ! mp_setup_state_has imported; then
        mp_setup_state_action "Importing verified recovery snapshot" \
            RECOVERY_IMPORTING imported || return 1
        ui_message "Full-loss recovery" "Import the latest encrypted portable snapshot. The restore flow verifies its receipt and requires the recovery identity held outside the VPS."
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
        mp_setup_state_action "Restoring the verified application snapshot" \
            RECOVERY_RESTORING restored || return 1
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
    if ! mp_setup_state_has public_routing_ready; then
        mp_setup_state_action "Verifying restored public routing" \
            PUBLIC_ROUTING_WAIT public_routing_ready || return 1
        mp_wait_for_public_health 45 || {
            mp_setup_state_failure PUBLIC_ROUTE_UNHEALTHY \
                "The restored application is locally healthy, but resolver-independent public HTTPS health is unavailable." || true
            return 1
        }
        mp_setup_state_mark public_routing_ready
    fi
    if ! mp_setup_state_has validated; then
        mp_setup_state_action "Validating the restored installation" \
            INSTALLATION_VALIDATING validated || return 1
        mp_validate_installation || return 1
        mp_setup_state_mark validated
    fi
    if ! mp_setup_state_has smtp_verified; then
        mp_setup_state_action "Verifying restored SMTP and DNS" \
            SMTP_VALIDATING smtp_verified || return 1
        mp_setup_verify_smtp_and_dns || return 1
        mp_setup_state_mark smtp_verified
    fi
    mp_setup_state_complete
    ui_message "Recovery complete" "The signed release is live and the restored application passed validation. Commission a new Node B from this server if HA protection is required."
}

mp_setup_v2() {
    local choice mode state role action failure_code failure_message status=0 completed_state=false
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
            failure_code="$(jq -r '.last_failure.code // empty' "$MP_SETUP_V2_STATE" 2>/dev/null || true)"
            failure_message="$(jq -r '.last_failure.message // empty' "$MP_SETUP_V2_STATE" 2>/dev/null || true)"
            ui_continue_message "Resuming commissioning" \
                "Current action: $(jq -r '.current_action // "Reconcile setup"' "$MP_SETUP_V2_STATE").$([ -z "$failure_code" ] || printf '\n\nLast verified status: %s — %s' "$failure_code" "$failure_message") Deployment lane: $(jq -r '.deployment_lane' "$MP_SETUP_V2_STATE"). The pinned target will not change."
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
            if mp_setup_state_has joined 2>/dev/null; then
                if [ "$(jq -r '.deployment_lane // empty' "$MP_SETUP_V2_STATE")" = unsigned ]; then
                    mp_setup_reconcile_unsigned_join || {
                        ui_error "Node B could not reconcile its pinned first copy. The protected waiting state was retained."
                        status=1
                    }
                    if [ "$(jq -r '.state // empty' "$MP_SETUP_V2_STATE")" = complete ]; then
                        ui_message "HA node ready" "Node B accepted and verified its pinned first application copy. Continue commissioning on Node A."
                    elif [ "$status" -eq 0 ]; then
                        ui_message "Waiting for pinned deployment" \
                            "Node B is paired and pinned to $(jq -r .campaign_commit "$MP_SETUP_V2_STATE"). Resume on Node A; it will transfer, verify and activate the exact images here."
                    fi
                else
                    mp_reconcile_signed_join_setup || {
                        ui_error "Node B could not reconcile its first verified copy. The protected waiting state was retained."
                        status=1
                    }
                    if [ "$(jq -r '.state // empty' "$MP_SETUP_V2_STATE")" = complete ]; then
                        ui_message "HA node ready" "Node B accepted and verified its first protected application copy. Continue commissioning on Node A."
                    elif [ "$status" -eq 0 ]; then
                        ui_message "Waiting for first verified copy" \
                            "Node B is paired and ready. Resume on Node A; this setup will complete only after the first protected application copy and local services are verified."
                    fi
                fi
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
    if [ "$status" -eq 10 ] && [ -s "$MP_SETUP_V2_STATE" ]; then
        action="$(jq -r '.current_action // "the current action"' "$MP_SETUP_V2_STATE" 2>/dev/null || printf 'the current action')"
        ui_message "Commissioning is waiting" \
            "${action} is waiting for a transient dependency to converge. No completed action will be repeated. Keep mp-opt open to retry from the menu, or run mp-opt again later to resume the same pinned setup."
        return 0
    fi
    if [ "$status" -ne 0 ] && [ -s "$MP_SETUP_V2_STATE" ]; then
        action="$(jq -r '.current_action // "the current action"' "$MP_SETUP_V2_STATE" 2>/dev/null || printf 'the current action')"
        jq -e '.last_failure != null' "$MP_SETUP_V2_STATE" >/dev/null 2>&1 \
            || mp_setup_state_failure "SETUP_ACTION_PAUSED" "${action} did not complete; resume will reconcile authoritative deployment facts before retrying." || true
        failure_code="$(jq -r '.last_failure.code // "SETUP_ACTION_PAUSED"' "$MP_SETUP_V2_STATE" 2>/dev/null || printf SETUP_ACTION_PAUSED)"
        failure_message="$(jq -r '.last_failure.message // "Resume commissioning to retry the verified checkpoint."' "$MP_SETUP_V2_STATE" 2>/dev/null || printf 'Resume commissioning to retry the verified checkpoint.')"
        ui_message "Commissioning paused" \
            "${failure_code}: ${failure_message}\n\nCurrent action: ${action}. The exact lane and commit remain pinned. Run mp-opt to resume; setup will not fall back to another deployment."
        return 0
    fi
    return "$status"
}
