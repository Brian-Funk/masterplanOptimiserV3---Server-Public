#!/usr/bin/env bash
# Host-local, machine-readable adapter for deterministic commissioning.
set -Eeuo pipefail
umask 077

if [ -n "${MP_ROOT:-}" ]; then
    # The test harness sources the function prefix into a non-interactive
    # `bash -c` process, where BASH_SOURCE legitimately has no element zero.
    # Production callers may also pin the installed checkout explicitly.  In
    # both cases resolve that already-authoritative root without consulting a
    # source-stack entry that does not exist.
    ROOT_DIR="$(readlink -f -- "$MP_ROOT")"
else
    MACHINE_SOURCE="${BASH_SOURCE[0]:-}"
    [ -n "$MACHINE_SOURCE" ] || {
        printf '%s\n' 'The commissioning adapter source path is unavailable.' >&2
        exit 65
    }
    SCRIPT_PATH="$(readlink -f -- "$MACHINE_SOURCE")"
    ROOT_DIR="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
fi
[ -d "$ROOT_DIR/deploy/management" ] || {
    printf '%s\n' 'The commissioning adapter repository root is invalid.' >&2
    exit 65
}
export MP_ROOT="$ROOT_DIR"

# shellcheck source=management/common.sh
source "$MP_ROOT/deploy/management/common.sh"
# shellcheck source=management/snapshots.sh
source "$MP_ROOT/deploy/management/snapshots.sh"
# shellcheck source=management/portable_snapshots.sh
source "$MP_ROOT/deploy/management/portable_snapshots.sh"
# shellcheck source=management/recovery_rotation.sh
source "$MP_ROOT/deploy/management/recovery_rotation.sh"
# shellcheck source=management/ha.sh
source "$MP_ROOT/deploy/management/ha.sh"
# shellcheck source=management/actions.sh
source "$MP_ROOT/deploy/management/actions.sh"
# shellcheck source=management/evidence.sh
source "$MP_ROOT/deploy/management/evidence.sh"
# shellcheck source=management/setup_v2.sh
source "$MP_ROOT/deploy/management/setup_v2.sh"
# shellcheck source=management/test_hooks.sh
source "$MP_ROOT/deploy/management/test_hooks.sh"

readonly MP_MACHINE_OK=0
readonly MP_MACHINE_WAITING=10
readonly MP_MACHINE_ATTENTION=20
readonly MP_MACHINE_BUSY=30
readonly MP_MACHINE_INVALID=40
readonly MP_MACHINE_UNAUTHORISED=50

# Never open a dialog or read operator input from this adapter.
ui_error() { printf '%s\n' "$*" >&2; }
ui_message() { printf '%s: %s\n' "${1:-Commissioning}" "${2:-}" >&2; }
ui_run_command() { shift 2; "$@"; }
ui_input() { return 64; }
ui_password() { return 64; }
ui_confirm() { return 64; }
ui_require_phrase() { return 64; }
ui_copyable_terminal_text() { return 64; }
ui_continue_message() { return 64; }

mp_machine_error() {
    local code="$1" message="$2" exit_code="${3:-$MP_MACHINE_INVALID}"
    jq -cn --arg code "$code" --arg message "$message" \
        --argjson exit_code "$exit_code" \
        '{format:"mp-opt-commissioning-error-v1",ok:false,error:{code:$code,message:$message},exit_code:$exit_code}'
    exit "$exit_code"
}

mp_machine_require_local_owner() {
    local owner
    [ -d "$MP_STATE" ] || return 0
    owner="$(stat -c '%u' "$MP_STATE" 2>/dev/null)" || return 1
    [ "$(id -u)" = "$owner" ]
}

mp_machine_validate_regular_file() {
    local path="$1" expected_mode="${2:-600}" expected_group="${3:-}" owner
    [ ! -e "$path" ] && [ ! -L "$path" ] && return 0
    [ -f "$path" ] && [ ! -L "$path" ] || return 1
    [ -d "$MP_STATE" ] || return 1
    owner="$(stat -c '%u' "$MP_STATE" 2>/dev/null)" || return 1
    [ "$(stat -c '%u' "$path" 2>/dev/null)" = "$owner" ] || return 1
    [ "$(stat -c '%a' "$path" 2>/dev/null)" = "$expected_mode" ] || return 1
    [ -z "$expected_group" ] \
        || [ "$(stat -c '%g' "$path" 2>/dev/null)" = "$expected_group" ]
}

mp_machine_validate() {
    local policy state_status=absent events_count=0
    policy="$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)"
    case "$policy" in production|test) ;; *) return 65 ;; esac
    if [ -e "$MP_SETUP_V2_STATE" ] || [ -L "$MP_SETUP_V2_STATE" ]; then
        mp_setup_validate_state_contract "$MP_SETUP_V2_STATE" || return $?
        state_status="$(jq -r .state "$MP_SETUP_V2_STATE")"
    fi
    mp_machine_validate_regular_file "$MP_SETUP_V2_EVENTS" 600 || return 77
    mp_machine_validate_regular_file "$MP_SETUP_V2_EXECUTION_STATE" 600 || return 77
    mp_machine_validate_regular_file "$MP_SETUP_V2_CANCEL_REQUEST" 600 || return 77
    mp_machine_validate_regular_file "$MP_SETUP_V2_EXECUTION_LOCK" 600 || return 77
    mp_machine_validate_regular_file "$MP_STATE/test-deployments/candidate/receipt.json" 600 || return 77
    if [ -e "$MP_SETUP_TEST_HOOK_DIR" ] || [ -L "$MP_SETUP_TEST_HOOK_DIR" ]; then
        [ "$policy" = test ] && [ -d "$MP_SETUP_TEST_HOOK_DIR" ] \
            && [ ! -L "$MP_SETUP_TEST_HOOK_DIR" ] \
            && [ "$(stat -c '%u:%a' "$MP_SETUP_TEST_HOOK_DIR" 2>/dev/null)" = "$(id -u):700" ] \
            || return 77
        mp_machine_validate_regular_file "$MP_SETUP_TEST_HOOK_ENABLED" 600 || return 77
        mp_machine_validate_regular_file "$MP_SETUP_TEST_HOOK_ARMED" 600 || return 77
        mp_machine_validate_regular_file "$MP_SETUP_TEST_HOOK_TRIGGERED" 600 || return 77
        mp_machine_validate_regular_file "$MP_SETUP_TEST_HOOK_LOCK" 600 || return 77
        if [ -e "$MP_SETUP_TEST_HOOK_RECEIPTS" ] || [ -L "$MP_SETUP_TEST_HOOK_RECEIPTS" ]; then
            [ -d "$MP_SETUP_TEST_HOOK_RECEIPTS" ] \
                && [ ! -L "$MP_SETUP_TEST_HOOK_RECEIPTS" ] \
                && [ "$(stat -c '%u:%a' "$MP_SETUP_TEST_HOOK_RECEIPTS" 2>/dev/null)" = "$(id -u):700" ] \
                || return 77
            while IFS= read -r -d '' receipt; do
                [[ "$(basename "$receipt")" =~ ^[0-9a-f]{64}\.json$ ]] || return 77
                mp_machine_validate_regular_file "$receipt" 600 || return 77
                jq -e '.format == "mp-opt-commissioning-transition-receipt-v1"
                    and (.run_id | test("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
                    and (.transition | type == "string")
                    and (.checkpoint | test("^[a-z0-9_]{1,64}$"))
                    and (.idempotency_key_sha256 | test("^[0-9a-f]{64}$"))
                    and (.side_effect_completed_at | type == "string")' \
                    "$receipt" >/dev/null 2>&1 || return 65
            done < <(find -P "$MP_SETUP_TEST_HOOK_RECEIPTS" -maxdepth 1 -mindepth 1 -print0)
        fi
    fi
    if [ -s "$MP_SETUP_V2_EVENTS" ]; then
        jq -s -e '
            all(.[];
                .format == "mp-opt-setup-event-v1"
                and (.sequence | type == "number" and . >= 1)
                and (.event_id | test("^[0-9a-f-]{36}$"))
                and (.type | test("^[a-z][a-z0-9._-]{0,63}$"))
                and (.run_id | test("^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")))
            and ([.[].sequence] == ([.[].sequence] | sort | unique))
        ' "$MP_SETUP_V2_EVENTS" >/dev/null 2>&1 || return 65
        events_count="$(wc -l < "$MP_SETUP_V2_EVENTS" | tr -d ' ')"
    fi
    jq -cn --arg state "$state_status" --arg policy "$policy" \
        --argjson events "$events_count" \
        '{format:"mp-opt-commissioning-validation-v1",ok:true,state:$state,
          deployment_policy:$policy,event_count:$events,exit_code:0}'
}

mp_machine_plan() {
    local mode="$1" lane="$2" checkpoints
    checkpoints="$(mp_setup_checkpoint_plan_json "$mode" "$lane")" || return 64
    jq -cn --arg mode "$mode" --arg lane "$lane" --argjson checkpoints "$checkpoints" \
        --argjson fault_transitions "$MP_SETUP_TEST_HOOK_TRANSITIONS" \
        --argjson fault_boundaries "$MP_SETUP_TEST_HOOK_BOUNDARIES" \
        --argjson fault_checkpoints "$MP_SETUP_TEST_HOOK_CHECKPOINT_MAP" '
        {format:"mp-opt-commissioning-plan-v1",ok:true,mode:$mode,
         deployment_lane:$lane,checkpoints:($checkpoints | to_entries | map({
            number:(.key + 1),id:.value,
            execution:(if (.value | IN("root_commissioning_complete")) then "browser-receipt"
                else "machine-or-reconcilable" end)})),
         machine_coverage:{fresh_standalone:true,fresh_ha:true,node_b_join:true,
            candidate_prebuilt:true,age_recovery_recipient:true,
            standalone_to_ha:true,replacement_peer:true,full_loss_restore:true,
            portable_migration_snapshot:true,portable_import_restore:true,
            signed_upgrade:true,signed_rollback:true,candidate_advance:true,
            candidate_exact_rollback:true,
            test_fault_hooks:true,
            fault_injection:{transitions:$fault_transitions,boundaries:$fault_boundaries,
                checkpoint_map:$fault_checkpoints},
            packaged_tui_pty:false,
            requested_mode_machine_complete:true,
            replacement_requires_surviving_active_holder:true,
            unsupported_modes:[],
            externally_completed_checkpoints:["root_commissioning_complete"],
            tui_only_checkpoints:[]},
         automatic_failover:{readiness_verified_during_commissioning:true,
            enabled_during_commissioning:false},exit_code:0}'
}

mp_machine_status() {
    local state='{}' plan='[]' execution='null' receiver='null' candidate='null' policy event_cursor=0
    local run_state=not_started next_checkpoint='null' progress=0 total=0 recommended=$MP_MACHINE_WAITING
    mp_machine_validate >/dev/null || return $?
    policy="$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)"
    if [ -s "$MP_SETUP_V2_STATE" ]; then
        state="$(cat "$MP_SETUP_V2_STATE")"
        plan="$(mp_setup_checkpoint_plan_json "$(jq -r .mode <<< "$state")" \
            "$(jq -r .deployment_lane <<< "$state")")" || return 65
        total="$(jq 'length' <<< "$plan")"
        progress="$(jq --argjson plan "$plan" \
            '[.completed[] as $step | select($plan | index($step) != null)] | length' \
            <<< "$state")"
        next_checkpoint="$(mp_machine_next_checkpoint_json)" || return $?
        if [ "$(jq -r .state <<< "$state")" = complete ]; then
            run_state=complete; recommended=$MP_MACHINE_OK
        elif jq -e '.last_failure != null and .last_failure.code != "SETUP_CANCELLED"' \
            <<< "$state" >/dev/null; then
            run_state=attention; recommended=$MP_MACHINE_ATTENTION
        elif [ "$(jq -r '.current_action_code // empty' <<< "$state")" = SETUP_CANCELLED ]; then
            run_state=paused; recommended=$MP_MACHINE_WAITING
        else
            run_state=waiting; recommended=$MP_MACHINE_WAITING
        fi
    fi
    execution="$(mp_setup_execution_observe)" || return 1
    if [ -s "$MP_ROOT/runtime/ha-receiver.json" ]; then
        receiver="$(jq '{bundle_id:(.last_bundle_id // null),
            sha256:(.last_bundle_sha256 // null),generation:(.generation // null),
            accepted_at:(.last_received_at // null)}' "$MP_ROOT/runtime/ha-receiver.json" 2>/dev/null || printf null)"
    fi
    if [ -s "$MP_STATE/test-deployments/candidate/receipt.json" ]; then
        candidate="$(jq '{format,commit,bundle_sha256,staged_at,
            image_digests:(.manifest.images|with_entries(.value |= split("@sha256:")[1]))}' \
            "$MP_STATE/test-deployments/candidate/receipt.json" 2>/dev/null || printf null)"
    fi
    [ ! -s "$MP_SETUP_V2_EVENTS" ] \
        || event_cursor="$(tail -n 1 "$MP_SETUP_V2_EVENTS" | jq -r '.sequence // 0')"
    mp_load_ha_config >/dev/null 2>&1 || {
        HA_ROLE=unknown; HA_NODE_ID=unknown; HA_GENERATION=0; HA_AUTOMATIC_FAILOVER=unknown;
    }
    jq -cn --arg run_state "$run_state" --arg policy "$policy" \
        --arg ha_role "${HA_ROLE:-standalone}" --arg node "${HA_NODE_ID:-standalone}" \
        --arg automatic "${HA_AUTOMATIC_FAILOVER:-disabled}" \
        --argjson setup "$state" --argjson plan "$plan" --argjson execution "$execution" \
        --argjson receiver "$receiver" --argjson candidate "$candidate" --argjson next "$next_checkpoint" \
        --argjson complete_count "$progress" --argjson total "$total" \
        --argjson cursor "$event_cursor" --argjson recommended "$recommended" \
        --argjson fault_transitions "$MP_SETUP_TEST_HOOK_TRANSITIONS" \
        --argjson fault_boundaries "$MP_SETUP_TEST_HOOK_BOUNDARIES" \
        --argjson fault_checkpoints "$MP_SETUP_TEST_HOOK_CHECKPOINT_MAP" \
        --arg cancel "$([ -s "$MP_SETUP_V2_CANCEL_REQUEST" ] && printf true || printf false)" '
        {format:"mp-opt-commissioning-status-v1",ok:true,run_state:$run_state,
         deployment_policy:$policy,
         setup:(if $setup == {} then null else {
            format:$setup.format,mode:$setup.mode,state:$setup.state,
            deployment_lane:$setup.deployment_lane,campaign_commit:$setup.campaign_commit,
            signed_baseline:$setup.signed_baseline,started_at:$setup.started_at,
            updated_at:$setup.updated_at,completed_at:($setup.completed_at // null),
            completed:$setup.completed,current_action:$setup.current_action,
            current_action_code:$setup.current_action_code,
            current_checkpoint:$setup.current_checkpoint,
            last_completed_action:($setup.last_completed_action // null),
            last_failure:$setup.last_failure,
            first_verified_bundle:($setup.first_verified_bundle // null)} end),
         progress:{completed:$complete_count,total:$total,
            percent:(if $total == 0 then 0 else (($complete_count * 100 / $total) | floor) end),
            next_checkpoint:$next},
         machine_coverage:{fresh_standalone:true,fresh_ha:true,node_b_join:true,
            candidate_prebuilt:true,age_recovery_recipient:true,
            standalone_to_ha:true,replacement_peer:true,full_loss_restore:true,
            portable_migration_snapshot:true,portable_import_restore:true,
            signed_upgrade:true,signed_rollback:true,candidate_advance:true,
            candidate_exact_rollback:true,
            test_fault_hooks:true,
            fault_injection:{transitions:$fault_transitions,boundaries:$fault_boundaries,
                checkpoint_map:$fault_checkpoints},
            packaged_tui_pty:false,
            requested_mode_machine_complete:(if $setup == {} then null else true end),
            replacement_requires_surviving_active_holder:true,
            unsupported_modes:[],
            externally_completed_checkpoints:["root_commissioning_complete"],
            tui_only_checkpoints:[]},
         execution:$execution,cancellation_requested:($cancel == "true"),
         candidate:$candidate,
         ha:{role:$ha_role,node_id:$node,automatic_failover:$automatic,
            automatic_failover_enabled:($automatic == "enabled"),receiver:$receiver},
         event_cursor:$cursor,recommended_exit_code:$recommended}'
}

mp_machine_events() {
    local after="$1" output_format="${2:-json}"
    [[ "$after" =~ ^[0-9]+$ ]] || return 64
    mp_machine_validate >/dev/null || return $?
    if [ -s "$MP_SETUP_V2_EVENTS" ] && [ "$output_format" = jsonl ]; then
        jq -c --argjson after "$after" 'select(.sequence > $after)' \
            "$MP_SETUP_V2_EVENTS"
    elif [ -s "$MP_SETUP_V2_EVENTS" ]; then
        jq -sc --argjson after "$after" '
            {format:"mp-opt-commissioning-events-v1",ok:true,after:$after,
             events:map(select(.sequence > $after)),
             cursor:(map(.sequence) | max // $after),exit_code:0}' \
            "$MP_SETUP_V2_EVENTS"
    else
        jq -cn --argjson after "$after" \
            '{format:"mp-opt-commissioning-events-v1",ok:true,after:$after,
              events:[],cursor:$after,exit_code:0}'
    fi
}

mp_machine_handoff_error() {
    printf '%s\n' "$2" >&2
    exit "${3:-$MP_MACHINE_INVALID}"
}

# Derive the next actionable checkpoint from the immutable plan and completed
# receipts.  Secret handoffs must not depend on mutable presentation fields
# such as current_action/current_checkpoint: those fields are intentionally
# cleared as soon as the preceding checkpoint is durably completed.
mp_machine_next_checkpoint_json() {
    local state plan
    state="$(cat "$MP_SETUP_V2_STATE")" || return 1
    plan="$(mp_setup_checkpoint_plan_json "$(jq -r .mode <<< "$state")" \
        "$(jq -r .deployment_lane <<< "$state")")" || return 65
    jq -cn --argjson plan "$plan" --argjson setup "$state" \
        '$plan | map(select(. as $step | ($setup.completed | index($step)) == null)) | (.[0] // null)'
}

# Emit one existing bounded setup secret and nothing else on stdout. This is
# for a local coordinator which immediately passes the value to a browser or
# peer. Status, validation, events and journals never call this function.
mp_machine_handoff() {
    local kind="$1" status value path run_id next_checkpoint
    mp_machine_require_local_owner || return 77
    [ -s "$MP_SETUP_V2_STATE" ] || return 65
    mp_setup_validate_state_contract "$MP_SETUP_V2_STATE" || return $?
    run_id="handoff-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    if mp_setup_execution_acquire "$run_id" "handoff-${kind}"; then
        :
    else
        status=$?
        return "$status"
    fi
    trap 'unset value; mp_setup_execution_release' EXIT
    next_checkpoint="$(mp_machine_next_checkpoint_json)" || return $?
    case "$kind" in
        root-bootstrap)
            jq -e --argjson next "$next_checkpoint" '
                .state == "in_progress"
                and (.mode | IN("standalone-new","ha-primary-new"))
                and $next == "root_commissioning_complete"
                and ((.completed // []) | index("application_deployed") != null)
                and ((.completed // []) | index("public_routing_ready") != null)
                and ((.completed // []) | index("root_commissioning_complete") == null)
            ' "$MP_SETUP_V2_STATE" >/dev/null 2>&1 || return 65
            path="$MP_ROOT/secrets/root_bootstrap_token"
            mp_machine_validate_regular_file "$path" 640 10001 || return 77
            [ -s "$path" ] || return 66
            value="$(cat "$path")" || return 1
            [[ "$value" =~ ^[A-Za-z0-9_-]{64}$ ]] || return 65
            ;;
        ha-join)
            jq -e --argjson next "$next_checkpoint" '
                .state == "in_progress"
                and (.mode | IN("ha-primary-new","convert-ha","replace-primary"))
                and $next == "paired"
                and ((.completed // []) | index("paired") == null)
            ' "$MP_SETUP_V2_STATE" >/dev/null 2>&1 || return 65
            path="$MP_SETUP_V2_PENDING_JOIN"
            mp_machine_validate_regular_file "$path" 600 || return 77
            [ -s "$path" ] || return 66
            value="$(python3 "$MP_ROOT/deploy/ha/pairing.py" encode < "$path")" \
                || return 65
            [[ "$value" =~ ^MPHA2-[A-Za-z0-9_-]+-[0-9A-F]{10}$ ]] || return 65
            ;;
        *) return 64 ;;
    esac
    printf '%s\n' "$value"
    unset value
    mp_setup_execution_release
    trap - EXIT
}

mp_machine_request_cancel() {
    local temporary request_id owner group
    [ -s "$MP_SETUP_V2_STATE" ] || {
        jq -cn '{format:"mp-opt-commissioning-cancel-result-v1",ok:true,
            accepted:false,reason:"no_active_setup",exit_code:0}'
        return 0
    }
    [ "$(jq -r .state "$MP_SETUP_V2_STATE")" = in_progress ] || {
        jq -cn '{format:"mp-opt-commissioning-cancel-result-v1",ok:true,
            accepted:false,reason:"setup_complete",exit_code:0}'
        return 0
    }
    mp_machine_require_local_owner || return 77
    request_id="$(cat /proc/sys/kernel/random/uuid)"
    temporary="$(mktemp "$MP_STATE/setup-cancel-request.XXXXXX")" || return 1
    jq -n --arg request "$request_id" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --argjson uid "$(id -u)" \
        '{format:"mp-opt-setup-cancel-v1",request_id:$request,requested_at:$at,
          requester_uid:$uid,scope:"current_execution"}' > "$temporary" \
        || { rm -f "$temporary"; return 1; }
    chmod 600 "$temporary" && mv "$temporary" "$MP_SETUP_V2_CANCEL_REQUEST" \
        || { rm -f "$temporary"; return 1; }
    jq -cn --arg request "$request_id" \
        '{format:"mp-opt-commissioning-cancel-result-v1",ok:true,
          accepted:true,request_id:$request,scope:"current_execution",
          resumable:true,exit_code:0}'
}

mp_machine_with_lease() {
    local command="$1" run_id status log
    mp_machine_require_local_owner || return 77
    run_id="machine-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    if mp_setup_execution_acquire "$run_id" "$command"; then
        :
    else
        status=$?
        [ "$status" -ne 75 ] || return 75
        return "$status"
    fi
    trap 'mp_setup_execution_release' EXIT
    mp_setup_journal_event execution.started || return 1
    status=0
    mkdir -p "$MP_STATE/setup-machine-logs"; chmod 700 "$MP_STATE/setup-machine-logs"
    log="$MP_STATE/setup-machine-logs/${run_id}.log"; : > "$log"; chmod 600 "$log"
    mp_setup_machine_reconcile >"$log" 2>&1 || status=$?
    if [ "$status" -eq 0 ]; then
        mp_setup_journal_event execution.completed || return 1
    elif [ "$status" -eq 10 ]; then
        mp_setup_journal_event execution.waiting || return 1
    else
        mp_setup_journal_event execution.failed || true
    fi
    mp_machine_status
    return "$status"
}

mp_machine_start() {
    local mode="$1" lane="$2" policy expected_lane run_id status
    case "$mode" in standalone-new|ha-primary-new|ha-join|convert-ha|replace-primary|replace-node|full-restore) ;; *) return 64 ;; esac
    case "$lane" in signed|unsigned) ;; *) return 64 ;; esac
    policy="$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)"
    case "$policy" in production) expected_lane=signed ;; test) expected_lane=unsigned ;; *) return 65 ;; esac
    [ "$lane" = "$expected_lane" ] || return 65
    mp_machine_require_local_owner || return 77
    mp_initialise_paths || return 77
    if [ -s "$MP_SETUP_V2_STATE" ]; then
        mp_setup_validate_state_contract "$MP_SETUP_V2_STATE" || return $?
        if [ "$(jq -r .state "$MP_SETUP_V2_STATE")" = complete ] \
            && [[ "$mode" =~ ^(convert-ha|replace-primary)$ ]]; then
            case "$mode" in
                convert-ha) [ -f "$MP_ROOT/.env" ] \
                    && [ "$(mp_ha_role 2>/dev/null || printf standalone)" = standalone ] || return 65 ;;
                replace-primary) [ "$(mp_ha_role 2>/dev/null || printf standalone)" = dynamic ] \
                    && mp_require_active_or_standalone >/dev/null 2>&1 || return 65 ;;
            esac
            mp_setup_state_clear_completed || return 1
        fi
    fi
    if [ -s "$MP_SETUP_V2_STATE" ]; then
        [ "$(jq -r .state "$MP_SETUP_V2_STATE")" = in_progress ] \
            && [ "$(jq -r .mode "$MP_SETUP_V2_STATE")" = "$mode" ] \
            && [ "$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")" = "$lane" ] \
            || return 65
        return 0
    fi
    run_id="start-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    if mp_setup_execution_acquire "$run_id" start; then :; else status=$?; return "$status"; fi
    trap 'mp_setup_execution_release' EXIT
    if [ "$mode" = full-restore ] && ! mp_snapshot_full_loss_host_is_blank; then
        mp_setup_execution_release
        trap - EXIT
        return 65
    fi
    mp_setup_state_begin "$mode"
    status=$?
    mp_setup_execution_release
    trap - EXIT
    return "$status"
}

mp_machine_read_advance_input() {
    local target="$1" bytes
    # Read one bounded document from stdin; nothing is echoed or journalled.
    head -c 65537 > "$target" || return 1
    bytes="$(wc -c < "$target" | tr -d ' ')"
    [ "$bytes" -gt 0 ] && [ "$bytes" -le 65536 ] || return 64
    chmod 600 "$target"
    jq -e '
        type == "object"
        and ((keys - ["format","checkpoint","idempotency_key","values"]) | length == 0)
        and .format == "mp-opt-commissioning-input-v1"
        and (.checkpoint | type == "string" and test("^[a-z0-9_]{1,64}$"))
        and (.idempotency_key | type == "string"
             and test("^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"))
        and (.values | type == "object")
        and (if .checkpoint == "signed_baseline_verified" then
            ((.values | length == 0)
             or (((.values | keys | sort) == ["commit","tag"])
                 and (.values.tag | type == "string"
                      and test("^v(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$"))
                 and (.values.commit | type == "string" and test("^[0-9a-f]{40}$"))))
          elif .checkpoint == "configuration" then
            ((.values | length == 0)
             or (((.values | keys - ["application_name","domain","instance_key_authorized",
                "permitted_data_acknowledged","smtp","vapid_contact_email"]) | length == 0)
            and (.values.domain | type == "string" and length <= 253)
            and (.values.application_name | type == "string" and length >= 1 and length <= 120)
            and (.values.vapid_contact_email | type == "string" and length <= 320)
            and .values.permitted_data_acknowledged == true
            and .values.instance_key_authorized == true
            and (.values.smtp | type == "object")
            and ((.values.smtp | keys - ["enabled","from_email","from_name","host",
                "port","reply_to","security","token","username"]) | length == 0)
            and (.values.smtp.enabled | type == "boolean")
            and (if .values.smtp.enabled then
                (.values.smtp.host | type == "string" and length <= 253)
                and (.values.smtp.port | type == "number" and floor == . and . >= 1 and . <= 65535)
                and (.values.smtp.username | type == "string" and length <= 320)
                and (.values.smtp.token | type == "string" and length >= 1 and length <= 4096)
                and (.values.smtp.security | IN("starttls","tls"))
                and (.values.smtp.from_email | type == "string" and length <= 320)
                and (.values.smtp.from_name | type == "string" and length >= 1 and length <= 120)
                and ((.values.smtp.reply_to == null)
                    or (.values.smtp.reply_to | type == "string" and length <= 320))
              else true end)))
          elif .checkpoint == "public_dns" then
            ((.values | keys - ["ipv4","ipv6"]) | length == 0)
            and (.values.ipv4 | type == "string" and length <= 45)
            and ((.values.ipv6 == null) or (.values.ipv6 | type == "string" and length <= 45))
          elif .checkpoint == "joined" then
            ((.values | keys - ["join_code","ipv4","ipv6"]) | length == 0)
            and (.values.join_code | type == "string" and test("^MPHA2-[A-Za-z0-9_-]+-[0-9A-F]{10}$"))
            and (.values.ipv4 | type == "string" and length <= 45)
            and ((.values.ipv6 == null) or (.values.ipv6 | type == "string" and length <= 45))
          elif .checkpoint == "witness_bootstrap" then
            (((.values | keys) == ["old_peer_powered_off"] and .values.old_peer_powered_off == true)
             or (((.values | keys - ["cloudflare_account_id","cloudflare_deploy_token","cloudflare_dns_token","ipv4","ipv6"]) | length == 0)
                  and (.values.cloudflare_account_id | type == "string" and test("^[0-9a-f]{32}$"))
                 and (.values.cloudflare_deploy_token | type == "string" and length >= 32 and length <= 4096)
                 and (.values.cloudflare_dns_token | type == "string" and length >= 32 and length <= 4096)
                 and (.values.ipv4 | type == "string" and length <= 45)
                 and ((.values.ipv6 == null) or (.values.ipv6 | type == "string" and length <= 45))))
          elif .checkpoint == "application_deployed" then
            ((.values | length == 0)
             or (((.values | keys) == ["registry"])
                 and (.values.registry | type == "object")
                 and ((.values.registry | keys | sort) == ["token","username"])
                 and (.values.registry.username | type == "string" and length >= 1 and length <= 255)
                 and (.values.registry.token | type == "string" and length >= 1 and length <= 4096)))
          elif .checkpoint == "smtp_verified" then
            ((.values | length == 0)
             or (((.values | keys - ["correlation_id","dkim_selector","test_recipient"]) | length == 0)
                 and (.values.dkim_selector | type == "string"
                      and test("^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$"))
                  and (.values.test_recipient | type == "string"
                       and length >= 3 and length <= 320)
                  and (.values.correlation_id | type == "string" and test("^[0-9a-f]{32}$"))))
          elif .checkpoint == "recovery_recipient" then
            ((.values | keys) == ["recipient"])
            and (.values.recipient | type == "string" and test("^age1[0-9a-z]+$"))
          elif .checkpoint == "migration_snapshot" then
            ((.values | keys | sort) == ["artifact_ticket","package_sha256"])
            and (.values.artifact_ticket | type == "string"
                 and test("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
            and (.values.package_sha256 | type == "string" and test("^[0-9a-f]{64}$"))
          elif .checkpoint == "imported" then
            ((.values | keys) == ["package_sha256"])
            and (.values.package_sha256 | type == "string" and test("^[0-9a-f]{64}$"))
          elif .checkpoint == "restored" then
            ((.values | keys) == ["recovery_identity"])
            and (.values.recovery_identity | type == "string" and length >= 40 and length <= 4096)
          else (.values | length == 0) end)
    ' "$target" >/dev/null 2>&1 || return 64
}

mp_machine_stage_candidate() {
    local commit="$1" sha256="$2" run_id status=0 log output
    [ -s "$MP_SETUP_V2_STATE" ] || return 65
    mp_setup_validate_state_contract "$MP_SETUP_V2_STATE" || return $?
    jq -e --arg commit "$commit" '
        .deployment_lane == "unsigned"
        and (
          (.state == "in_progress" and .campaign_commit == $commit
           and ((.completed // []) | index("application_deployed") == null))
          or
          (.state == "in_progress" and .campaign_commit != $commit
           and (.mode | IN("standalone-new","ha-primary-new","ha-join"))
           and ((.completed // []) | index("application_deployed") == null)
           and ((.completed // []) | index("root_commissioning_complete") == null))
          or
          (((.completed // []) | index("application_deployed") != null)
           and .campaign_commit != $commit)
        )
    ' "$MP_SETUP_V2_STATE" >/dev/null || return 65
    [[ "$sha256" =~ ^[0-9a-f]{64}$ ]] || return 64
    run_id="stage-candidate-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mp_setup_execution_acquire "$run_id" stage-candidate || return $?
    trap 'mp_setup_execution_release' EXIT
    mkdir -p "$MP_STATE/setup-machine-logs"; chmod 700 "$MP_STATE/setup-machine-logs"
    log="$MP_STATE/setup-machine-logs/${run_id}.log"; : > "$log"; chmod 600 "$log"
    output="$("$MP_ROOT/deploy/test-deployment.sh" stage-candidate "$commit" "$sha256" 2>"$log")" \
        || status=$?
    mp_setup_execution_release; trap - EXIT
    [ "$status" -eq 0 ] || return "$status"
    printf '%s\n' "$output"
}

mp_machine_stage_migration_snapshot() {
    local input run_id log output status=0
    [ -s "$MP_SETUP_V2_STATE" ] || return 65
    mp_machine_require_local_owner || return 77
    input="$(mktemp "$MP_STATE/setup-machine-input.XXXXXX")" || return 1
    MP_MACHINE_INPUT_FILE="$input"; chmod 600 "$input"
    trap 'mp_secure_remove_file "${MP_MACHINE_INPUT_FILE:-}"; mp_setup_execution_release' EXIT
    head -c 8193 > "$input" || return 1
    [ "$(stat -c %s "$input")" -le 8192 ] || return 64
    run_id="stage-migration-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mp_setup_execution_acquire "$run_id" stage-migration || return $?
    mkdir -p "$MP_STATE/setup-machine-logs"; chmod 700 "$MP_STATE/setup-machine-logs"
    log="$MP_STATE/setup-machine-logs/${run_id}.log"; : > "$log"; chmod 600 "$log"
    output="$(mp_setup_machine_stage_migration_snapshot "$input" 2>"$log")" || status=$?
    mp_secure_remove_file "$input"; MP_MACHINE_INPUT_FILE=""
    mp_setup_execution_release; trap - EXIT
    [ "$status" -eq 0 ] || return "$status"
    printf '%s\n' "$output"
}

mp_machine_read_artifact() {
    local ticket="$1" directory receipt package expected owner run_id status
    [[ "$ticket" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] \
        || return 64
    mp_machine_require_local_owner || return 77
    directory="$MP_SETUP_V2_ARTIFACTS/$ticket"; receipt="$directory/receipt.json"
    [ -d "$directory" ] && [ ! -L "$directory" ] \
        && [ "$(stat -c '%a' "$directory" 2>/dev/null)" = 700 ] \
        && [ -f "$receipt" ] && [ ! -L "$receipt" ] \
        && [ "$(stat -c '%a' "$receipt" 2>/dev/null)" = 600 ] || return 65
    owner="$(stat -c '%u' "$MP_STATE")"
    [ "$(stat -c '%u' "$directory")" = "$owner" ] \
        && [ "$(stat -c '%u' "$receipt")" = "$owner" ] || return 77
    jq -e --arg ticket "$ticket" '.format == "mp-opt-machine-artifact-v1"
        and .kind == "migration-snapshot" and .ticket == $ticket
        and (.package_sha256 | test("^[0-9a-f]{64}$"))
        and (.package_size | type == "number" and . >= 1 and . <= 4294967296)' \
        "$receipt" >/dev/null 2>&1 || return 65
    package="$(jq -r .package_path "$receipt")"
    [ "$(readlink -f "$package")" = "$(readlink -f "$directory")/migration.mpopt-snapshot" ] \
        && [ -f "$package" ] && [ ! -L "$package" ] \
        && [ "$(stat -c '%u:%a' "$package")" = "$owner:600" ] || return 65
    [ "$(stat -c %s "$package")" = "$(jq -r .package_size "$receipt")" ] || return 65
    expected="$(jq -r .package_sha256 "$receipt")"
    [ "$(sha256sum "$package" | awk '{print $1}')" = "$expected" ] || return 65
    run_id="artifact-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mp_setup_execution_acquire "$run_id" artifact || return $?
    trap 'mp_setup_execution_release' EXIT
    cat "$package" || status=$?
    mp_setup_execution_release; trap - EXIT
    return "${status:-0}"
}

mp_machine_stage_recovery_package() {
    local sha256="$1" package run_id log output status=0 bytes
    [[ "$sha256" =~ ^[0-9a-f]{64}$ ]] || return 64
    [ -s "$MP_SETUP_V2_STATE" ] || return 65
    mp_machine_require_local_owner || return 77
    package="$(mktemp "$MP_STATE/setup-recovery-package.XXXXXX")" || return 1
    MP_MACHINE_INPUT_FILE="$package"; chmod 600 "$package"
    trap 'mp_secure_remove_file "${MP_MACHINE_INPUT_FILE:-}"; mp_setup_execution_release' EXIT
    dd bs=1048576 count=4097 status=none > "$package" || return 1
    bytes="$(stat -c %s "$package")"
    [ "$bytes" -ge 1 ] && [ "$bytes" -le 4294967296 ] || return 64
    run_id="stage-recovery-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mp_setup_execution_acquire "$run_id" stage-recovery || return $?
    mkdir -p "$MP_STATE/setup-machine-logs"; chmod 700 "$MP_STATE/setup-machine-logs"
    log="$MP_STATE/setup-machine-logs/${run_id}.log"; : > "$log"; chmod 600 "$log"
    output="$(mp_setup_machine_import_recovery_package "$package" "$sha256" 2>"$log")" \
        || status=$?
    mp_secure_remove_file "$package"; MP_MACHINE_INPUT_FILE=""
    mp_setup_execution_release; trap - EXIT
    [ "$status" -eq 0 ] || return "$status"
    printf '%s\n' "$output"
}

mp_machine_deployment_action() {
    local input run_id action tag commit status=0 log receipt_file idempotency_key temporary
    local recovery_identity="" policy
    [ -s "$MP_SETUP_V2_STATE" ] || return 65
    mp_setup_validate_state_contract "$MP_SETUP_V2_STATE" || return $?
    mp_machine_require_local_owner || return 77
    input="$(mktemp "$MP_STATE/setup-machine-input.XXXXXX")" || return 1
    MP_MACHINE_INPUT_FILE="$input"; chmod 600 "$input"
    trap 'mp_secure_remove_file "${MP_MACHINE_INPUT_FILE:-}"; mp_setup_execution_release' EXIT
    head -c 16385 > "$input" || return 1
    [ "$(stat -c %s "$input")" -le 16384 ] \
        && jq -e 'type == "object"
          and ((keys | sort) == ["action","commit","format","idempotency_key","tag","values"])
          and .format == "mp-opt-deployment-lifecycle-input-v1"
          and (.action | IN("signed-upgrade","signed-rollback","candidate-precommission-retry","candidate-advance","candidate-rollback"))
          and (.commit | type == "string" and test("^[0-9a-f]{40}$"))
          and (.idempotency_key | type == "string"
               and test("^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"))
          and (if (.action|startswith("signed-")) then
                 (.tag | type == "string"
                   and test("^v(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$"))
                 and .values == {}
               elif .action == "candidate-precommission-retry" then
                 .tag == null
                 and ((.values|keys|sort)==["registry"])
                 and ((.values.registry|keys|sort)==["token","username"])
                 and (.values.registry.username|type=="string" and length>=1 and length<=255)
                 and (.values.registry.token|type=="string" and length>=1 and length<=4096)
               else
                 .tag == null
                 and ((.values|keys|sort)==["recovery_identity","registry"])
                 and (.values.recovery_identity|type=="string" and length>=40 and length<=4096)
                 and ((.values.registry|keys|sort)==["token","username"])
                 and (.values.registry.username|type=="string" and length>=1 and length<=255)
                 and (.values.registry.token|type=="string" and length>=1 and length<=4096)
               end)' \
            "$input" >/dev/null 2>&1 || return 64
    action="$(jq -r .action "$input")"; tag="$(jq -r '.tag // empty' "$input")"; commit="$(jq -r .commit "$input")"
    idempotency_key="$(jq -r .idempotency_key "$input")"
    if [ "$action" != candidate-precommission-retry ]; then
        jq -e '((.completed // []) | index("application_deployed") != null)' \
            "$MP_SETUP_V2_STATE" >/dev/null || return 65
    fi
    receipt_file="$MP_STATE/setup-deployment-lifecycle.jsonl"
    mp_machine_validate_regular_file "$receipt_file" 600 || return 77
    if [ -s "$receipt_file" ]; then
        jq -s -e 'all(.[];
            .format == "mp-opt-deployment-lifecycle-receipt-v1"
            and (.action | IN("signed-upgrade","signed-rollback","candidate-precommission-retry","candidate-advance","candidate-rollback"))
            and ((.tag == null) or (.tag | test("^v[0-9]+\\.[0-9]+\\.[0-9]+$")))
            and (.commit | test("^[0-9a-f]{40}$"))
            and (.idempotency_key | test("^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"))
            and .state == "completed")' "$receipt_file" >/dev/null 2>&1 || return 65
        if jq -e --arg key "$idempotency_key" \
            --arg action "$action" --arg tag "$tag" --arg commit "$commit" \
            'select(.idempotency_key==$key
              and (.action!=$action or (.tag // "")!=$tag or .commit!=$commit))' \
            "$receipt_file" >/dev/null 2>&1; then
            return 65
        fi
    fi
    if [ -s "$receipt_file" ] && jq -e --arg key "$idempotency_key" \
        --arg action "$action" --arg tag "$tag" --arg commit "$commit" \
        'select(.idempotency_key==$key and .action==$action and (.tag // "")==$tag
          and .commit==$commit and .state=="completed")' "$receipt_file" >/dev/null 2>&1; then
        jq -sc --arg key "$idempotency_key" \
            'map(select(.idempotency_key==$key)) | last
             | {format:"mp-opt-deployment-lifecycle-result-v1",ok:true,
                action,tag,commit,resumed:true,completed_at,exit_code:0}' "$receipt_file"
        mp_secure_remove_file "$input"; MP_MACHINE_INPUT_FILE=""; trap - EXIT
        return 0
    fi
    run_id="deployment-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mp_setup_execution_acquire "$run_id" deployment || return $?
    mkdir -p "$MP_STATE/setup-machine-logs"; chmod 700 "$MP_STATE/setup-machine-logs"
    log="$MP_STATE/setup-machine-logs/${run_id}.log"; : > "$log"; chmod 600 "$log"
    policy="$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)"
    case "$action" in
        signed-upgrade) mp_deploy_signed_exact "$tag" "$commit" >"$log" 2>&1 || status=$? ;;
        signed-rollback) mp_rollback_signed_exact "$tag" "$commit" >"$log" 2>&1 || status=$? ;;
        candidate-precommission-retry|candidate-advance|candidate-rollback)
            [ "$policy" = test ] || status=65
            if [ "$status" -eq 0 ] && [ "$action" = candidate-advance ]; then
                # Browser recovery stores the public AGE recipient in the
                # database before root commissioning is complete.  A targeted
                # debug candidate at a later browser step still needs the
                # normal snapshot-backed lifecycle, so synchronise that public
                # value into host custody before matching the supplied private
                # identity.  No private material leaves the machine input.
                mp_setup_sync_commissioning_recipient || status=$?
            fi
            if [ "$status" -eq 0 ] && [ "$action" != candidate-precommission-retry ]; then
                recovery_identity="$(mp_setup_machine_identity_file \
                    "$(jq -r .values.recovery_identity "$input")")" || status=$?
            fi
            if [ "$status" -eq 0 ]; then
                if [ "$action" = candidate-precommission-retry ]; then
                    jq -c .values.registry "$input" \
                        | "$MP_ROOT/deploy/test-deployment.sh" apply-prebuilt-precommission \
                            "$commit" --registry-credentials-stdin >"$log" 2>&1 || status=$?
                elif [ "$action" = candidate-advance ]; then
                    jq -c .values.registry "$input" \
                        | MP_TEST_RECOVERY_IDENTITY_FILE="$recovery_identity" \
                            "$MP_ROOT/deploy/test-deployment.sh" apply-prebuilt-established \
                                "$commit" --registry-credentials-stdin >"$log" 2>&1 || status=$?
                else
                    jq -c .values.registry "$input" \
                        | MP_TEST_RECOVERY_IDENTITY_FILE="$recovery_identity" \
                            "$MP_ROOT/deploy/test-deployment.sh" rollback-prebuilt \
                                "$commit" --registry-credentials-stdin >"$log" 2>&1 || status=$?
                fi
            fi
            [ -z "$recovery_identity" ] || mp_secure_remove_file "$recovery_identity"
            ;;
    esac
    if [ "$status" -eq 0 ]; then
        temporary="$(mktemp "$MP_STATE/setup-deployment-lifecycle.XXXXXX")" || status=1
    fi
    if [ "$status" -eq 0 ]; then
        [ ! -s "$receipt_file" ] || cat "$receipt_file" > "$temporary" || status=1
    fi
    if [ "$status" -eq 0 ]; then
        jq -cn --arg action "$action" --arg tag "$tag" --arg commit "$commit" \
            --arg key "$idempotency_key" \
            --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
            '{format:"mp-opt-deployment-lifecycle-receipt-v1",action:$action,
              tag:(if $tag=="" then null else $tag end),commit:$commit,
              idempotency_key:$key,state:"completed",completed_at:$at}' \
            >> "$temporary" || status=1
    fi
    if [ "$status" -eq 0 ]; then
        chmod 600 "$temporary" && sync -f "$temporary" 2>/dev/null \
            && mv "$temporary" "$receipt_file" \
            && sync -f "$MP_STATE" 2>/dev/null || status=1
    fi
    [ "$status" -eq 0 ] || { [ -z "${temporary:-}" ] || rm -f "$temporary"; }
    mp_secure_remove_file "$input"; MP_MACHINE_INPUT_FILE=""
    mp_setup_execution_release; trap - EXIT
    [ "$status" -eq 0 ] || return "$status"
    jq -cn --arg action "$action" --arg tag "$tag" --arg commit "$commit" \
        --arg completed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '{format:"mp-opt-deployment-lifecycle-result-v1",ok:true,action:$action,
          tag:(if $tag=="" then null else $tag end),commit:$commit,
          resumed:false,completed_at:$completed_at,exit_code:0}'
}

mp_machine_cleanup_provider() {
    local input run_id log output status=0 token account worker zone
    [ "$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)" = test ] || return 77
    input="$(mktemp "$MP_STATE/setup-machine-input.XXXXXX")" || return 1
    MP_MACHINE_INPUT_FILE="$input"; chmod 600 "$input"
    trap 'mp_secure_remove_file "${MP_MACHINE_INPUT_FILE:-}"; mp_setup_execution_release' EXIT
    head -c 8193 > "$input" || return 1
    [ "$(stat -c %s "$input")" -le 8192 ] \
        && jq -e 'type=="object" and (keys|sort)==["account_id","deploy_token","format","worker_name","zone_id"]
          and .format=="mp-opt-provider-cleanup-input-v1"
          and (.account_id|type=="string" and test("^[0-9a-f]{32}$"))
          and (.deploy_token|type=="string" and length>=32 and length<=4096)
          and (.worker_name|type=="string" and test("^[a-z0-9][a-z0-9-]{0,62}$"))
          and (.zone_id|type=="string" and test("^[A-Za-z0-9_-]{8,128}$"))' \
            "$input" >/dev/null 2>&1 || return 64
    token="$(jq -r .deploy_token "$input")"
    account="$(jq -r .account_id "$input")"
    worker="$(jq -r .worker_name "$input")"
    zone="$(jq -r .zone_id "$input")"
    run_id="cleanup-provider-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mp_setup_execution_acquire "$run_id" cleanup-provider || return $?
    mkdir -p "$MP_STATE/setup-machine-logs"; chmod 700 "$MP_STATE/setup-machine-logs"
    log="$MP_STATE/setup-machine-logs/${run_id}.log"; : > "$log"; chmod 600 "$log"
    output="$(mp_setup_decommission_cloudflare_machine "$token" "$account" "$worker" "$zone" \
        2>"$log")" || status=$?
    unset token; mp_secure_remove_file "$input"; MP_MACHINE_INPUT_FILE=""
    mp_setup_execution_release; trap - EXIT
    [ "$status" -eq 0 ] || return "$status"
    printf '%s\n' "$output"
}

mp_machine_advance_command() {
    local input_file run_id status=0 log
    mp_machine_require_local_owner || return 77
    mp_initialise_paths || return 77
    [ -s "$MP_SETUP_V2_STATE" ] || return 65
    input_file="$(mktemp "$MP_STATE/setup-machine-input.XXXXXX")" || return 1
    chmod 600 "$input_file"
    MP_MACHINE_INPUT_FILE="$input_file"
    trap 'mp_secure_remove_file "${MP_MACHINE_INPUT_FILE:-}"; mp_setup_execution_release' EXIT
    mp_machine_read_advance_input "$input_file" || return $?
    run_id="advance-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    if mp_setup_execution_acquire "$run_id" advance; then :; else status=$?; return "$status"; fi
    mp_setup_journal_event execution.started || return 1
    mkdir -p "$MP_STATE/setup-machine-logs"; chmod 700 "$MP_STATE/setup-machine-logs"
    log="$MP_STATE/setup-machine-logs/${run_id}.log"; : > "$log"; chmod 600 "$log"
    mp_setup_machine_advance_one "$input_file" >"$log" 2>&1 || status=$?
    mp_secure_remove_file "$input_file"
    MP_MACHINE_INPUT_FILE=""
    if [ "$status" -eq 0 ]; then
        mp_setup_journal_event execution.completed || return 1
    elif [ "$status" -eq 10 ]; then
        mp_setup_journal_event execution.waiting || return 1
    else
        mp_setup_journal_event execution.failed || true
    fi
    mp_setup_execution_release
    trap - EXIT
    mp_machine_status
    return "$status"
}

mp_machine_read_test_hook_input() {
    local target="$1" bytes
    head -c 8193 > "$target" || return 1
    bytes="$(wc -c < "$target" | tr -d ' ')"
    [ "$bytes" -gt 0 ] && [ "$bytes" -le 8192 ] || return 64
    chmod 600 "$target"
    jq -e 'type == "object"' "$target" >/dev/null 2>&1 || return 64
}

mp_machine_test_hook_input_action() {
    local action="$1" input status=0 output
    mp_machine_require_local_owner || return 77
    input="$(mktemp "$MP_STATE/setup-machine-input.XXXXXX")" || return 1
    MP_MACHINE_INPUT_FILE="$input"; chmod 600 "$input"
    trap 'mp_secure_remove_file "${MP_MACHINE_INPUT_FILE:-}"' EXIT
    mp_machine_read_test_hook_input "$input" || return $?
    case "$action" in
        enable) output="$(mp_setup_test_hook_enable "$input")" || status=$? ;;
        arm) output="$(mp_setup_test_hook_arm "$input")" || status=$? ;;
        disarm) output="$(mp_setup_test_hook_disarm "$input")" || status=$? ;;
        boundary) output="$(mp_setup_test_hook_reach "$input")" || status=$? ;;
        *) status=64 ;;
    esac
    mp_secure_remove_file "$input"; MP_MACHINE_INPUT_FILE=""; trap - EXIT
    [ -z "$output" ] || printf '%s\n' "$output"
    return "$status"
}

command_name="${1:-}"
[ -n "$command_name" ] || mp_machine_error INVALID_ARGUMENT \
    "Usage: mp-opt setup validate|plan|start|stage-candidate|stage-migration|stage-recovery|artifact|deployment|advance|status|events|reconcile|cancel|handoff|cleanup-provider|test-hook"
shift || true

case "$command_name" in
    test-hook)
        hook_action="${1:-}"; [ -n "$hook_action" ] || mp_machine_error INVALID_ARGUMENT \
            "test-hook requires capabilities, enable, arm, disarm or boundary"
        shift || true
        case "$hook_action" in
            capabilities)
                [ "${1:-}" != --json ] || shift
                [ "$#" -eq 0 ] || mp_machine_error INVALID_ARGUMENT \
                    "test-hook capabilities accepts only --json"
                status=0; output="$(mp_setup_test_hook_capabilities)" || status=$?
                ;;
            enable|arm|disarm|boundary)
                input_stdin=false
                while [ "$#" -gt 0 ]; do
                    case "$1" in --input-stdin) input_stdin=true; shift ;; --json) shift ;;
                        *) mp_machine_error INVALID_ARGUMENT "Unsupported test-hook argument: $1" ;;
                    esac
                done
                [ "$input_stdin" = true ] || mp_machine_error INVALID_ARGUMENT \
                    "test-hook ${hook_action} requires --input-stdin"
                status=0; output="$(mp_machine_test_hook_input_action "$hook_action")" || status=$?
                ;;
            *) mp_machine_error INVALID_ARGUMENT \
                "test-hook requires capabilities, enable, arm, disarm or boundary" ;;
        esac
        case "$status" in
            0) [ -z "${output:-}" ] || printf '%s\n' "$output" ;;
            197) [ -z "${output:-}" ] || printf '%s\n' "$output"; exit 197 ;;
            75) mp_machine_error TEST_HOOK_BUSY "Another exact test fault is active." "$MP_MACHINE_BUSY" ;;
            77) mp_machine_error TEST_POLICY_REQUIRED \
                "Commissioning fault hooks are unavailable outside an explicitly enabled test-policy run." \
                "$MP_MACHINE_UNAUTHORISED" ;;
            64|65|66) mp_machine_error INVALID_TEST_HOOK \
                "The test-hook request or protected state is invalid." "$MP_MACHINE_INVALID" ;;
            *) mp_machine_error TEST_HOOK_FAILED \
                "The test-only commissioning hook failed closed." "$MP_MACHINE_ATTENTION" ;;
        esac
        ;;
    stage-migration)
        input_stdin=false
        while [ "$#" -gt 0 ]; do
            case "$1" in --input-stdin) input_stdin=true; shift ;; --json) shift ;;
                *) mp_machine_error INVALID_ARGUMENT "Unsupported stage-migration argument: $1" ;;
            esac
        done
        [ "$input_stdin" = true ] || mp_machine_error INVALID_ARGUMENT \
            "stage-migration requires --input-stdin"
        status=0; output="$(mp_machine_stage_migration_snapshot)" || status=$?
        case "$status" in 0) printf '%s\n' "$output" ;;
            75) mp_machine_error EXECUTION_BUSY "Another coordinator holds the lease." "$MP_MACHINE_BUSY" ;;
            77) mp_machine_error LOCAL_OWNER_REQUIRED "Protected setup custody failed." "$MP_MACHINE_UNAUTHORISED" ;;
            64|65) mp_machine_error INVALID_INPUT "Migration snapshot input or state is invalid." "$MP_MACHINE_INVALID" ;;
            *) mp_machine_error MIGRATION_SNAPSHOT_FAILED "The migration package was not staged safely." "$MP_MACHINE_ATTENTION" ;;
        esac
        ;;
    stage-recovery)
        sha256=""; input_stdin=false
        while [ "$#" -gt 0 ]; do
            case "$1" in --sha256) sha256="${2:-}"; shift 2 ;;
                --input-stdin) input_stdin=true; shift ;; --json) shift ;;
                *) mp_machine_error INVALID_ARGUMENT "Unsupported stage-recovery argument: $1" ;;
            esac
        done
        [ "$input_stdin" = true ] && [ -n "$sha256" ] || mp_machine_error INVALID_ARGUMENT \
            "stage-recovery requires --sha256 and --input-stdin"
        status=0; output="$(mp_machine_stage_recovery_package "$sha256")" || status=$?
        case "$status" in 0) printf '%s\n' "$output" ;;
            75) mp_machine_error EXECUTION_BUSY "Another coordinator holds the lease." "$MP_MACHINE_BUSY" ;;
            77) mp_machine_error LOCAL_OWNER_REQUIRED "Protected recovery custody failed." "$MP_MACHINE_UNAUTHORISED" ;;
            64|65) mp_machine_error INVALID_INPUT "Recovery package or setup state is invalid." "$MP_MACHINE_INVALID" ;;
            *) mp_machine_error RECOVERY_STAGE_FAILED "The recovery package failed protected import validation." "$MP_MACHINE_ATTENTION" ;;
        esac
        ;;
    artifact)
        ticket=""
        while [ "$#" -gt 0 ]; do
            case "$1" in --ticket) ticket="${2:-}"; shift 2 ;;
                *) mp_machine_handoff_error INVALID_ARGUMENT "artifact accepts only --ticket" ;;
            esac
        done
        [ -n "$ticket" ] || mp_machine_handoff_error INVALID_ARGUMENT "artifact requires --ticket"
        status=0; mp_machine_read_artifact "$ticket" || status=$?
        case "$status" in 0) ;;
            75) mp_machine_handoff_error EXECUTION_BUSY "Another coordinator holds the lease." "$MP_MACHINE_BUSY" ;;
            77) mp_machine_handoff_error LOCAL_OWNER_REQUIRED "Artifact custody failed." "$MP_MACHINE_UNAUTHORISED" ;;
            *) mp_machine_handoff_error ARTIFACT_UNAVAILABLE "The requested artifact is unavailable or failed integrity validation." "$MP_MACHINE_INVALID" ;;
        esac
        ;;
    cleanup-provider)
        input_stdin=false
        while [ "$#" -gt 0 ]; do
            case "$1" in --input-stdin) input_stdin=true; shift ;; --json) shift ;;
                *) mp_machine_error INVALID_ARGUMENT "Unsupported cleanup-provider argument: $1" ;;
            esac
        done
        [ "$input_stdin" = true ] || mp_machine_error INVALID_ARGUMENT \
            "cleanup-provider requires --input-stdin"
        status=0; output="$(mp_machine_cleanup_provider)" || status=$?
        case "$status" in 0) printf '%s\n' "$output" ;;
            75) mp_machine_error EXECUTION_BUSY "Another coordinator holds the lease." "$MP_MACHINE_BUSY" ;;
            77) mp_machine_error TEST_POLICY_REQUIRED "Provider cleanup automation is available only in test policy." "$MP_MACHINE_UNAUTHORISED" ;;
            64|65) mp_machine_error INVALID_INPUT "Provider cleanup input or HA state is invalid." "$MP_MACHINE_INVALID" ;;
            *) mp_machine_error PROVIDER_CLEANUP_FAILED "Provider cleanup remains safely resumable." "$MP_MACHINE_ATTENTION" ;;
        esac
        ;;
    deployment)
        input_stdin=false
        while [ "$#" -gt 0 ]; do
            case "$1" in --input-stdin) input_stdin=true; shift ;; --json) shift ;;
                *) mp_machine_error INVALID_ARGUMENT "Unsupported deployment argument: $1" ;;
            esac
        done
        [ "$input_stdin" = true ] || mp_machine_error INVALID_ARGUMENT \
            "deployment requires --input-stdin"
        status=0; output="$(mp_machine_deployment_action)" || status=$?
        case "$status" in 0) printf '%s\n' "$output" ;;
            75) mp_machine_error EXECUTION_BUSY "Another coordinator holds the lease." "$MP_MACHINE_BUSY" ;;
            77) mp_machine_error LOCAL_OWNER_REQUIRED "Deployment lifecycle custody failed." "$MP_MACHINE_UNAUTHORISED" ;;
            64|65) mp_machine_error INVALID_INPUT "Deployment lifecycle input, state, or idempotency key is invalid." "$MP_MACHINE_INVALID" ;;
            *) mp_machine_error DEPLOYMENT_ACTION_FAILED "The exact deployment action failed and remains safely retryable." "$MP_MACHINE_ATTENTION" ;;
        esac
        ;;
    stage-candidate)
        commit=""; sha256=""; input_stdin=false
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --commit) commit="${2:-}"; shift 2 ;;
                --sha256) sha256="${2:-}"; shift 2 ;;
                --input-stdin) input_stdin=true; shift ;;
                --json) shift ;;
                *) mp_machine_error INVALID_ARGUMENT "Unsupported stage-candidate argument: $1" ;;
            esac
        done
        [ "$input_stdin" = true ] && [ -n "$commit" ] && [ -n "$sha256" ] \
            || mp_machine_error INVALID_ARGUMENT \
                "stage-candidate requires --commit, --sha256 and --input-stdin"
        status=0; output="$(mp_machine_stage_candidate "$commit" "$sha256")" || status=$?
        case "$status" in
            0) printf '%s\n' "$output" ;;
            75) mp_machine_error EXECUTION_BUSY "Another commissioning coordinator holds the execution lease." "$MP_MACHINE_BUSY" ;;
            64|65) mp_machine_error INVALID_INPUT "The candidate does not match the pinned unsigned setup." "$MP_MACHINE_INVALID" ;;
            *) mp_machine_error CANDIDATE_STAGE_FAILED "The candidate bundle failed bounded validation." "$MP_MACHINE_ATTENTION" ;;
        esac
        ;;
    start)
        mode=""; lane=""
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --mode) mode="${2:-}"; shift 2 ;;
                --lane) lane="${2:-}"; shift 2 ;;
                --json) shift ;;
                *) mp_machine_error INVALID_ARGUMENT "Unsupported start argument: $1" ;;
            esac
        done
        [ -n "$mode" ] && [ -n "$lane" ] \
            || mp_machine_error INVALID_ARGUMENT "start requires --mode and --lane"
        status=0
        mp_machine_start "$mode" "$lane" || status=$?
        case "$status" in
            0) output="$(mp_machine_status)"; printf '%s\n' "$output"; exit "$([ "$(jq -r .run_state <<< "$output")" = complete ] && printf 0 || printf 10)" ;;
            75) mp_machine_error EXECUTION_BUSY "Another commissioning coordinator holds the execution lease." "$MP_MACHINE_BUSY" ;;
            77) mp_machine_error LOCAL_OWNER_REQUIRED "Run start as the protected state-directory owner." "$MP_MACHINE_UNAUTHORISED" ;;
            *) mp_machine_error START_REJECTED "The requested commissioning mode or lane cannot be started from current state." "$MP_MACHINE_INVALID" ;;
        esac
        ;;
    validate)
        [ "${1:-}" != --json ] || shift
        [ "$#" -eq 0 ] || mp_machine_error INVALID_ARGUMENT "validate accepts only --json"
        if output="$(mp_machine_validate)"; then printf '%s\n' "$output"; else
            status=$?; mp_machine_error INVALID_STATE "Commissioning state or local metadata is invalid." \
                "$([ "$status" -eq 77 ] && printf %s "$MP_MACHINE_UNAUTHORISED" || printf %s "$MP_MACHINE_INVALID")"
        fi
        ;;
    plan)
        mode=""; lane=""
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --mode) mode="${2:-}"; shift 2 ;;
                --lane) lane="${2:-}"; shift 2 ;;
                --json) shift ;;
                *) mp_machine_error INVALID_ARGUMENT "Unsupported plan argument: $1" ;;
            esac
        done
        if [ -s "$MP_SETUP_V2_STATE" ]; then
            [ -n "$mode" ] || mode="$(jq -r .mode "$MP_SETUP_V2_STATE")"
            [ -n "$lane" ] || lane="$(jq -r .deployment_lane "$MP_SETUP_V2_STATE")"
        fi
        [ -n "$mode" ] && [ -n "$lane" ] \
            || mp_machine_error INVALID_ARGUMENT "plan requires --mode and --lane when setup has not started"
        output="$(mp_machine_plan "$mode" "$lane")" \
            || mp_machine_error INVALID_PLAN "The requested commissioning mode or lane is unsupported."
        printf '%s\n' "$output"
        ;;
    status)
        [ "${1:-}" != --json ] || shift
        [ "$#" -eq 0 ] || mp_machine_error INVALID_ARGUMENT "status accepts only --json"
        output="$(mp_machine_status)" || {
            status=$?; mp_machine_error INVALID_STATE "Commissioning status could not be read safely." \
                "$([ "$status" -eq 77 ] && printf %s "$MP_MACHINE_UNAUTHORISED" || printf %s "$MP_MACHINE_INVALID")";
        }
        printf '%s\n' "$output"
        exit "$(jq -r .recommended_exit_code <<< "$output")"
        ;;
    events)
        after=0; events_format=json
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --after) after="${2:-}"; shift 2 ;;
                --json) events_format=json; shift ;;
                --jsonl) events_format=jsonl; shift ;;
                *) mp_machine_error INVALID_ARGUMENT "Unsupported events argument: $1" ;;
            esac
        done
        output="$(mp_machine_events "$after" "$events_format")" \
            || mp_machine_error INVALID_STATE "The commissioning event journal is invalid."
        [ -z "$output" ] || printf '%s\n' "$output"
        ;;
    handoff)
        kind=""
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --kind) kind="${2:-}"; shift 2 ;;
                *) mp_machine_handoff_error INVALID_ARGUMENT "handoff accepts only --kind root-bootstrap or --kind ha-join" ;;
            esac
        done
        case "$kind" in root-bootstrap|ha-join) ;; *)
            mp_machine_handoff_error INVALID_ARGUMENT "handoff requires --kind root-bootstrap or --kind ha-join" ;;
        esac
        status=0
        output="$(mp_machine_handoff "$kind")" || status=$?
        case "$status" in
            0) printf '%s\n' "$output" ;;
            75) mp_machine_handoff_error EXECUTION_BUSY \
                    "Another commissioning coordinator holds the execution lease." "$MP_MACHINE_BUSY" ;;
            77) mp_machine_handoff_error LOCAL_OWNER_REQUIRED \
                    "The protected handoff files do not satisfy their ownership or mode contract." \
                    "$MP_MACHINE_UNAUTHORISED" ;;
            *) mp_machine_handoff_error HANDOFF_UNAVAILABLE \
                    "The requested handoff is not available at the current verified checkpoint." \
                    "$MP_MACHINE_INVALID" ;;
        esac
        ;;
    cancel)
        [ "${1:-}" != --json ] || shift
        [ "$#" -eq 0 ] || mp_machine_error INVALID_ARGUMENT "cancel accepts only --json"
        output="$(mp_machine_request_cancel)" || {
            status=$?; mp_machine_error CANCEL_REJECTED \
                "The cancellation request could not be recorded safely." \
                "$([ "$status" -eq 77 ] && printf %s "$MP_MACHINE_UNAUTHORISED" || printf %s "$MP_MACHINE_ATTENTION")";
        }
        printf '%s\n' "$output"
        ;;
    reconcile)
        [ "${1:-}" != --json ] || shift
        [ "$#" -eq 0 ] || mp_machine_error INVALID_ARGUMENT "$command_name accepts only --json"
        status=0
        output="$(mp_machine_with_lease "$command_name")" || status=$?
        if [ "$status" -eq 75 ]; then
            mp_machine_error EXECUTION_BUSY "Another commissioning coordinator holds the execution lease." "$MP_MACHINE_BUSY"
        elif [ "$status" -eq 77 ]; then
            mp_machine_error LOCAL_OWNER_REQUIRED \
                "Run mutating commissioning commands as the protected state-directory owner." \
                "$MP_MACHINE_UNAUTHORISED"
        elif [ "$status" -ne 0 ] && [ "$status" -ne 10 ]; then
            mp_machine_error RECONCILIATION_FAILED \
                "Authoritative commissioning facts could not be reconciled safely." "$MP_MACHINE_ATTENTION"
        fi
        printf '%s\n' "$output"
        exit "$status"
        ;;
    advance)
        input_stdin=false
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --input-stdin) input_stdin=true; shift ;;
                --json) shift ;;
                *) mp_machine_error INVALID_ARGUMENT "Unsupported advance argument: $1" ;;
            esac
        done
        [ "$input_stdin" = true ] \
            || mp_machine_error INVALID_ARGUMENT "advance requires --input-stdin; input is never accepted on argv"
        status=0
        output="$(mp_machine_advance_command)" || status=$?
        case "$status" in
            0|10) [ -z "$output" ] || printf '%s\n' "$output"; exit "$status" ;;
            197) exit 197 ;;
            64|65) mp_machine_error INVALID_INPUT \
                "The input schema or expected checkpoint does not match current state." "$MP_MACHINE_INVALID" ;;
            75) mp_machine_error EXECUTION_BUSY "Another commissioning coordinator holds the execution lease." "$MP_MACHINE_BUSY" ;;
            77) mp_machine_error LOCAL_OWNER_REQUIRED "Run advance as the protected state-directory owner." "$MP_MACHINE_UNAUTHORISED" ;;
            *) mp_machine_error ADVANCE_FAILED "The bounded commissioning transition failed and remains resumable." "$MP_MACHINE_ATTENTION" ;;
        esac
        ;;
    *) mp_machine_error INVALID_ARGUMENT "Unsupported commissioning command: $command_name" ;;
esac
