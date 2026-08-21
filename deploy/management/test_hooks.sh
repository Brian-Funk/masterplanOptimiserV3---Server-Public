#!/usr/bin/env bash
# Test-policy-only commissioning interruption hooks.
#
# This file is sourced only by host-local management and HA scripts. It has no
# network route and no container mount. Production policy is rejected before any
# test state is created or inspected.

MP_SETUP_TEST_HOOK_DIR="${MP_SETUP_TEST_HOOK_DIR:-${MP_STATE}/setup-test-hooks}"
MP_SETUP_TEST_HOOK_ENABLED="${MP_SETUP_TEST_HOOK_ENABLED:-${MP_SETUP_TEST_HOOK_DIR}/enabled.json}"
MP_SETUP_TEST_HOOK_ARMED="${MP_SETUP_TEST_HOOK_ARMED:-${MP_SETUP_TEST_HOOK_DIR}/armed.json}"
MP_SETUP_TEST_HOOK_TRIGGERED="${MP_SETUP_TEST_HOOK_TRIGGERED:-${MP_SETUP_TEST_HOOK_DIR}/triggered.jsonl}"
MP_SETUP_TEST_HOOK_LOCK="${MP_SETUP_TEST_HOOK_LOCK:-${MP_SETUP_TEST_HOOK_DIR}/lock}"
MP_SETUP_TEST_HOOK_RECEIPTS="${MP_SETUP_TEST_HOOK_RECEIPTS:-${MP_SETUP_TEST_HOOK_DIR}/transition-receipts}"
MP_SETUP_TEST_HOOK_DRIVER_CHECKPOINTS="${MP_SETUP_TEST_HOOK_DRIVER_CHECKPOINTS:-${MP_SETUP_TEST_HOOK_DIR}/driver-checkpoints}"

# Keep the complete reviewed transition catalogue beside the executable
# subset.  A transition is advertised in `transitions` only after its named
# driver reaches the real side effect.  This prevents the private laboratory
# from confusing a planned adapter with executable interruption coverage.
readonly MP_SETUP_TEST_HOOK_TRANSITION_SPECS='[
  {"transition":"artifact.acquire","driver":"coordinator","wired":true},
  {"transition":"artifact.images-activate","driver":"server-checkpoint","wired":true},
  {"transition":"database.create","driver":"deployment-script","wired":true},
  {"transition":"database.migrate","driver":"deployment-script","wired":true},
  {"transition":"backend.activate","driver":"deployment-script","wired":true},
  {"transition":"caddy.activate","driver":"deployment-script","wired":true},
  {"transition":"witness.deploy-code","driver":"server-checkpoint","wired":true},
  {"transition":"witness.bind-secrets","driver":"server-checkpoint","wired":true},
  {"transition":"witness.register-primary","driver":"server-checkpoint","wired":true},
  {"transition":"dns.create","driver":"server-checkpoint","wired":true},
  {"transition":"dns.propagate","driver":"server-checkpoint","wired":true},
  {"transition":"peer.pair","driver":"server-checkpoint","wired":true},
  {"transition":"bundle.capture","driver":"ha-script","wired":true},
  {"transition":"bundle.transfer","driver":"ha-script","wired":true},
  {"transition":"bundle.restore","driver":"ha-script","wired":true},
  {"transition":"bundle.verify","driver":"ha-script","wired":true},
  {"transition":"bundle.acknowledge","driver":"server-checkpoint","wired":true},
  {"transition":"root.passkey-register","driver":"browser","wired":true},
  {"transition":"recovery.download","driver":"browser","wired":true},
  {"transition":"recovery.reselect","driver":"browser","wired":true},
  {"transition":"controller.download-or-import","driver":"browser","wired":true},
  {"transition":"controller.possession-proof","driver":"browser","wired":true},
  {"transition":"controller.root-authorise","driver":"browser","wired":true},
  {"transition":"governance.save","driver":"browser","wired":true},
  {"transition":"governance.preview","driver":"browser","wired":true},
  {"transition":"governance.publish","driver":"browser","wired":true},
  {"transition":"smtp.authenticate","driver":"server-checkpoint","wired":true},
  {"transition":"smtp.dns-verify","driver":"server-checkpoint","wired":true},
  {"transition":"smtp.deliver-and-receive","driver":"server-checkpoint","wired":true},
  {"transition":"commissioning.finalise","driver":"browser","wired":true},
  {"transition":"evidence.verify","driver":"server-checkpoint","wired":true}
]'
readonly MP_SETUP_TEST_HOOK_TRANSITIONS='[
  "artifact.acquire","artifact.images-activate","database.create","database.migrate",
  "backend.activate","caddy.activate","witness.deploy-code","witness.bind-secrets",
  "witness.register-primary","dns.create","dns.propagate",
  "peer.pair","bundle.capture","bundle.transfer","bundle.restore","bundle.verify",
  "bundle.acknowledge","root.passkey-register","recovery.download","recovery.reselect",
  "controller.download-or-import","controller.possession-proof",
  "controller.root-authorise","governance.save","governance.preview",
  "governance.publish","smtp.authenticate","smtp.dns-verify",
  "smtp.deliver-and-receive","commissioning.finalise",
  "evidence.verify"
]'
readonly MP_SETUP_TEST_HOOK_BOUNDARIES='[
  "before-side-effect","after-side-effect-before-receipt",
  "after-receipt-before-checkpoint","after-checkpoint-before-next-action"
]'
readonly MP_SETUP_TEST_HOOK_CHECKPOINT_MAP='[
  {"checkpoint":"application_deployed","transition":"artifact.images-activate"},
  {"checkpoint":"witness_bootstrap","transition":"witness.register-primary"},
  {"checkpoint":"public_routing_ready","transition":"dns.propagate"},
  {"checkpoint":"joined","transition":"peer.pair"},
  {"checkpoint":"paired","transition":"peer.pair"},
  {"checkpoint":"replicated","transition":"bundle.acknowledge"},
  {"checkpoint":"validated","transition":"evidence.verify"}
]'

mp_setup_test_hook_policy() {
    [ "$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)" = test ]
}

mp_setup_test_hook_uuid() {
    [[ "$1" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]]
}

mp_setup_test_hook_transition() {
    jq -en --arg value "$1" --argjson supported "$MP_SETUP_TEST_HOOK_TRANSITIONS" \
        '$supported | index($value) != null' >/dev/null
}

mp_setup_test_hook_boundary_name() {
    jq -en --arg value "$1" --argjson supported "$MP_SETUP_TEST_HOOK_BOUNDARIES" \
        '$supported | index($value) != null' >/dev/null
}

mp_setup_test_hook_fault_id() {
    local transition="$1" boundary="$2"
    printf '%s\0%s' "$transition" "$boundary" | sha256sum | awk '{print "fault-" substr($1,1,16)}'
}

mp_setup_test_hook_prepare() {
    local owner mode
    mp_setup_test_hook_policy || return 77
    [ -d "$MP_STATE" ] && [ ! -L "$MP_STATE" ] || return 77
    owner="$(stat -c '%u' "$MP_STATE" 2>/dev/null)" || return 77
    [ "$owner" = "$(id -u)" ] || return 77
    if [ -e "$MP_SETUP_TEST_HOOK_DIR" ] || [ -L "$MP_SETUP_TEST_HOOK_DIR" ]; then
        [ -d "$MP_SETUP_TEST_HOOK_DIR" ] && [ ! -L "$MP_SETUP_TEST_HOOK_DIR" ] || return 77
        [ "$(stat -c '%u' "$MP_SETUP_TEST_HOOK_DIR")" = "$owner" ] || return 77
        mode="$(stat -c '%a' "$MP_SETUP_TEST_HOOK_DIR")" || return 77
        [ "$mode" = 700 ] || return 77
    else
        mkdir -m 700 "$MP_SETUP_TEST_HOOK_DIR" || return 1
    fi
    if [ -e "$MP_SETUP_TEST_HOOK_RECEIPTS" ] || [ -L "$MP_SETUP_TEST_HOOK_RECEIPTS" ]; then
        [ -d "$MP_SETUP_TEST_HOOK_RECEIPTS" ] && [ ! -L "$MP_SETUP_TEST_HOOK_RECEIPTS" ] \
            && [ "$(stat -c '%u:%a' "$MP_SETUP_TEST_HOOK_RECEIPTS" 2>/dev/null)" = "$owner:700" ] \
            || return 77
    else
        mkdir -m 700 "$MP_SETUP_TEST_HOOK_RECEIPTS" || return 1
    fi
    if [ -e "$MP_SETUP_TEST_HOOK_DRIVER_CHECKPOINTS" ] \
        || [ -L "$MP_SETUP_TEST_HOOK_DRIVER_CHECKPOINTS" ]; then
        [ -d "$MP_SETUP_TEST_HOOK_DRIVER_CHECKPOINTS" ] \
            && [ ! -L "$MP_SETUP_TEST_HOOK_DRIVER_CHECKPOINTS" ] \
            && [ "$(stat -c '%u:%a' "$MP_SETUP_TEST_HOOK_DRIVER_CHECKPOINTS" 2>/dev/null)" = "$owner:700" ] \
            || return 77
    else
        mkdir -m 700 "$MP_SETUP_TEST_HOOK_DRIVER_CHECKPOINTS" || return 1
    fi
    if [ -e "$MP_SETUP_TEST_HOOK_LOCK" ] || [ -L "$MP_SETUP_TEST_HOOK_LOCK" ]; then
        [ -f "$MP_SETUP_TEST_HOOK_LOCK" ] && [ ! -L "$MP_SETUP_TEST_HOOK_LOCK" ] \
            && [ "$(stat -c '%u:%a' "$MP_SETUP_TEST_HOOK_LOCK")" = "$owner:600" ] || return 77
    else
        : > "$MP_SETUP_TEST_HOOK_LOCK" || return 1
        chmod 600 "$MP_SETUP_TEST_HOOK_LOCK" || return 1
    fi
    command -v flock >/dev/null 2>&1 || return 69
}

mp_setup_test_hook_validate_file() {
    local file="$1" owner
    [ ! -e "$file" ] && [ ! -L "$file" ] && return 0
    [ -f "$file" ] && [ ! -L "$file" ] || return 77
    owner="$(stat -c '%u' "$MP_STATE")" || return 77
    [ "$(stat -c '%u:%a' "$file")" = "$owner:600" ] || return 77
}

mp_setup_test_hook_write() {
    local destination="$1" document="$2" temporary
    temporary="$(mktemp "$MP_SETUP_TEST_HOOK_DIR/.state.XXXXXX")" || return 1
    printf '%s\n' "$document" > "$temporary" || { rm -f "$temporary"; return 1; }
    chmod 600 "$temporary" && sync -f "$temporary" 2>/dev/null \
        && mv "$temporary" "$destination" \
        && sync -f "$MP_SETUP_TEST_HOOK_DIR" 2>/dev/null \
        || { rm -f "$temporary"; return 1; }
}

mp_setup_test_hook_validate_enabled() {
    local run_id="${1:-}"
    mp_setup_test_hook_validate_file "$MP_SETUP_TEST_HOOK_ENABLED" || return $?
    [ -s "$MP_SETUP_TEST_HOOK_ENABLED" ] || return 66
    jq -e --arg run "$run_id" '
        type == "object"
        and ((keys | sort) == ["enabled","enabled_at","format","run_id"])
        and .format == "mp-opt-commissioning-test-hook-enable-v1"
        and .enabled == true
        and (.run_id | test("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
        and (.enabled_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))
        and ($run == "" or .run_id == $run)
    ' "$MP_SETUP_TEST_HOOK_ENABLED" >/dev/null 2>&1
}

mp_setup_test_hook_capabilities() {
    local enabled=false run_id=''
    mp_setup_test_hook_prepare || return $?
    mp_setup_test_hook_validate_file "$MP_SETUP_TEST_HOOK_ARMED" || return $?
    mp_setup_test_hook_validate_file "$MP_SETUP_TEST_HOOK_TRIGGERED" || return $?
    if [ -s "$MP_SETUP_TEST_HOOK_ENABLED" ]; then
        mp_setup_test_hook_validate_enabled "" || return 65
        enabled=true
        run_id="$(jq -r .run_id "$MP_SETUP_TEST_HOOK_ENABLED")"
    fi
    jq -cn --argjson transitions "$MP_SETUP_TEST_HOOK_TRANSITIONS" \
        --argjson transition_specs "$MP_SETUP_TEST_HOOK_TRANSITION_SPECS" \
        --argjson boundaries "$MP_SETUP_TEST_HOOK_BOUNDARIES" \
        --argjson checkpoint_map "$MP_SETUP_TEST_HOOK_CHECKPOINT_MAP" \
        --argjson enabled "$enabled" --arg run "$run_id" '
        {format:"mp-opt-commissioning-test-hooks-v1",enabled:$enabled,
         environment:"commissioning-candidate",scope:"host-local-machine-interface",
         active_run_id:(if $run == "" then null else $run end),
         one_shot:true,transitions:$transitions,
         declared_transitions:($transition_specs | map(.transition)),
         transition_specs:$transition_specs,boundaries:$boundaries,
         checkpoint_map:$checkpoint_map}
    '
}

mp_setup_test_hook_transition_for_checkpoint() {
    jq -ern --arg checkpoint "$1" --argjson mapping "$MP_SETUP_TEST_HOOK_CHECKPOINT_MAP" \
        '$mapping[] | select(.checkpoint == $checkpoint) | .transition' | head -1
}

# Return success only when the current checkpoint must pass through the fault
# wrapper: either its exact transition is armed, or a durable transition
# receipt proves that its side effect already returned successfully. Merely
# enabling a test run must not change unrelated commissioning semantics.
mp_setup_test_hook_should_wrap() {
    local transition="$1" checkpoint="$2" idempotency_key="$3" status=0 run_id
    mp_setup_test_hook_policy || return 1
    [ -s "$MP_SETUP_TEST_HOOK_ENABLED" ] || return 1
    mp_setup_test_hook_prepare || return $?
    mp_setup_test_hook_validate_enabled "" || return $?
    if mp_setup_test_hook_receipt_matches "$transition" "$checkpoint" "$idempotency_key"; then
        return 0
    else
        status=$?
        [ "$status" -eq 1 ] || return "$status"
    fi
    mp_setup_test_hook_validate_file "$MP_SETUP_TEST_HOOK_ARMED" || return $?
    [ -s "$MP_SETUP_TEST_HOOK_ARMED" ] || return 1
    run_id="$(jq -r .run_id "$MP_SETUP_TEST_HOOK_ENABLED")" || return 65
    jq -e --arg run "$run_id" --arg transition "$transition" '
        .run_id == $run and .transition == $transition and .state == "armed"
    ' "$MP_SETUP_TEST_HOOK_ARMED" >/dev/null 2>&1
}

# Invoke a boundary from inside the real commissioning process.  There is no
# output on the normal machine channel.  Exit 197 deliberately terminates the
# current one-transition process after the trigger receipt has been fsynced.
mp_setup_test_hook_reach_named() {
    local transition="$1" boundary="$2" run_id fault_id input status=0
    mp_setup_test_hook_policy || return 0
    [ -s "$MP_SETUP_TEST_HOOK_ENABLED" ] || return 0
    mp_setup_test_hook_validate_enabled "" || return $?
    run_id="$(jq -r .run_id "$MP_SETUP_TEST_HOOK_ENABLED")" || return 65
    fault_id="$(mp_setup_test_hook_fault_id "$transition" "$boundary")" || return 1
    input="$(mktemp "$MP_SETUP_TEST_HOOK_DIR/.boundary.XXXXXX")" || return 1
    jq -n --arg run "$run_id" --arg fault "$fault_id" \
        --arg transition "$transition" --arg boundary "$boundary" \
        '{format:"mp-opt-commissioning-fault-boundary-v1",run_id:$run,
          fault_id:$fault,transition:$transition,boundary:$boundary}' > "$input" \
        && chmod 600 "$input" || { rm -f "$input"; return 1; }
    mp_setup_test_hook_reach "$input" >/dev/null || status=$?
    rm -f "$input"
    if [ "$status" -eq 197 ]; then
        exit 197
    fi
    return "$status"
}

mp_setup_test_hook_receipt_path() {
    local run_id="$1" transition="$2" checkpoint="$3" idempotency_key="$4" digest
    digest="$(printf '%s\0%s\0%s\0%s' "$run_id" "$transition" "$checkpoint" \
        "$idempotency_key" | sha256sum | awk '{print $1}')" || return 1
    printf '%s/%s.json\n' "$MP_SETUP_TEST_HOOK_RECEIPTS" "$digest"
}

mp_setup_test_hook_receipt_matches() {
    local transition="$1" checkpoint="$2" idempotency_key="$3" run_id receipt owner
    mp_setup_test_hook_policy || return 1
    [ -s "$MP_SETUP_TEST_HOOK_ENABLED" ] || return 1
    mp_setup_test_hook_validate_enabled "" || return $?
    run_id="$(jq -r .run_id "$MP_SETUP_TEST_HOOK_ENABLED")" || return 65
    receipt="$(mp_setup_test_hook_receipt_path "$run_id" "$transition" \
        "$checkpoint" "$idempotency_key")" || return 1
    [ -f "$receipt" ] && [ ! -L "$receipt" ] || return 1
    owner="$(stat -c '%u' "$MP_STATE")" || return 77
    [ "$(stat -c '%u:%a' "$receipt" 2>/dev/null)" = "$owner:600" ] || return 77
    jq -e --arg run "$run_id" --arg transition "$transition" \
        --arg checkpoint "$checkpoint" --arg key_hash \
        "$(printf '%s' "$idempotency_key" | sha256sum | awk '{print $1}')" '
        .format == "mp-opt-commissioning-transition-receipt-v1"
        and .run_id == $run and .transition == $transition
        and .checkpoint == $checkpoint and .idempotency_key_sha256 == $key_hash
        and (.side_effect_completed_at | type == "string")
    ' "$receipt" >/dev/null 2>&1
}

# Record only that the bounded commissioning call returned success.  It
# contains no provider response, secret, personal data, or arbitrary output.
mp_setup_test_hook_record_transition_receipt() {
    local transition="$1" checkpoint="$2" idempotency_key="$3" run_id receipt document
    local temporary key_hash
    mp_setup_test_hook_prepare || return $?
    mp_setup_test_hook_validate_enabled "" || return $?
    if mp_setup_test_hook_receipt_matches "$transition" "$checkpoint" "$idempotency_key"; then
        return 0
    fi
    run_id="$(jq -r .run_id "$MP_SETUP_TEST_HOOK_ENABLED")" || return 65
    receipt="$(mp_setup_test_hook_receipt_path "$run_id" "$transition" \
        "$checkpoint" "$idempotency_key")" || return 1
    [ ! -e "$receipt" ] && [ ! -L "$receipt" ] || return 65
    key_hash="$(printf '%s' "$idempotency_key" | sha256sum | awk '{print $1}')" || return 1
    document="$(jq -cn --arg run "$run_id" --arg transition "$transition" \
        --arg checkpoint "$checkpoint" --arg key_hash "$key_hash" \
        --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
        {format:"mp-opt-commissioning-transition-receipt-v1",run_id:$run,
         transition:$transition,checkpoint:$checkpoint,idempotency_key_sha256:$key_hash,
         side_effect_completed_at:$at}')" || return 1
    temporary="$(mktemp "$MP_SETUP_TEST_HOOK_RECEIPTS/.receipt.XXXXXX")" || return 1
    printf '%s\n' "$document" > "$temporary" && chmod 600 "$temporary" \
        && sync -f "$temporary" 2>/dev/null && mv "$temporary" "$receipt" \
        && sync -f "$MP_SETUP_TEST_HOOK_RECEIPTS" 2>/dev/null \
        || { rm -f "$temporary"; return 1; }
}

mp_setup_test_hook_driver_checkpoint_path() {
    local run_id="$1" transition="$2" checkpoint="$3" idempotency_key="$4" digest
    digest="$(printf '%s\0%s\0%s\0%s' "$run_id" "$transition" "$checkpoint" \
        "$idempotency_key" | sha256sum | awk '{print $1}')" || return 1
    printf '%s/%s.json\n' "$MP_SETUP_TEST_HOOK_DRIVER_CHECKPOINTS" "$digest"
}

mp_setup_test_hook_driver_checkpoint_matches() {
    local transition="$1" checkpoint="$2" idempotency_key="$3" run_id path owner
    mp_setup_test_hook_validate_enabled "" || return $?
    run_id="$(jq -r .run_id "$MP_SETUP_TEST_HOOK_ENABLED")" || return 65
    path="$(mp_setup_test_hook_driver_checkpoint_path "$run_id" "$transition" \
        "$checkpoint" "$idempotency_key")" || return 1
    [ -f "$path" ] && [ ! -L "$path" ] || return 1
    owner="$(stat -c '%u' "$MP_STATE")" || return 77
    [ "$(stat -c '%u:%a' "$path" 2>/dev/null)" = "$owner:600" ] || return 77
    jq -e --arg run "$run_id" --arg transition "$transition" \
        --arg checkpoint "$checkpoint" --arg key_hash \
        "$(printf '%s' "$idempotency_key" | sha256sum | awk '{print $1}')" '
        .format == "mp-opt-commissioning-driver-checkpoint-v1"
        and .run_id == $run and .transition == $transition
        and .checkpoint == $checkpoint and .idempotency_key_sha256 == $key_hash
        and (.completed_at | type == "string")
    ' "$path" >/dev/null 2>&1
}

mp_setup_test_hook_record_driver_checkpoint() {
    local transition="$1" checkpoint="$2" idempotency_key="$3" run_id path document
    local temporary key_hash
    mp_setup_test_hook_prepare || return $?
    if mp_setup_test_hook_driver_checkpoint_matches \
        "$transition" "$checkpoint" "$idempotency_key"; then
        return 0
    fi
    run_id="$(jq -r .run_id "$MP_SETUP_TEST_HOOK_ENABLED")" || return 65
    path="$(mp_setup_test_hook_driver_checkpoint_path "$run_id" "$transition" \
        "$checkpoint" "$idempotency_key")" || return 1
    [ ! -e "$path" ] && [ ! -L "$path" ] || return 65
    key_hash="$(printf '%s' "$idempotency_key" | sha256sum | awk '{print $1}')" || return 1
    document="$(jq -cn --arg run "$run_id" --arg transition "$transition" \
        --arg checkpoint "$checkpoint" --arg key_hash "$key_hash" \
        --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
        {format:"mp-opt-commissioning-driver-checkpoint-v1",run_id:$run,
         transition:$transition,checkpoint:$checkpoint,idempotency_key_sha256:$key_hash,
         completed_at:$at}')" || return 1
    temporary="$(mktemp "$MP_SETUP_TEST_HOOK_DRIVER_CHECKPOINTS/.checkpoint.XXXXXX")" \
        || return 1
    printf '%s\n' "$document" > "$temporary" && chmod 600 "$temporary" \
        && sync -f "$temporary" 2>/dev/null && mv "$temporary" "$path" \
        && sync -f "$MP_SETUP_TEST_HOOK_DRIVER_CHECKPOINTS" 2>/dev/null \
        || { rm -f "$temporary"; return 1; }
}

# Wrap one deployment/HA/coordinator-owned real side effect. The exact command
# is passed as argv; no command string is evaluated. A durable side-effect
# receipt and a separate driver checkpoint make all four crash windows
# distinguishable without fabricating product state.
mp_setup_test_hook_run_driver_transition() {
    local transition="$1" checkpoint="$2" idempotency_key="$3" status=0
    shift 3
    [ "$#" -gt 0 ] || return 64
    if [ ! -s "$MP_SETUP_TEST_HOOK_ENABLED" ]; then
        "$@"
        return $?
    fi
    mp_setup_test_hook_transition "$transition" || return 64
    mp_setup_test_hook_should_wrap "$transition" "$checkpoint" \
        "$idempotency_key" || status=$?
    case "$status" in
        0) ;;
        1) "$@"; return $? ;;
        *) return "$status" ;;
    esac
    if mp_setup_test_hook_driver_checkpoint_matches \
        "$transition" "$checkpoint" "$idempotency_key"; then
        return 0
    fi
    if ! mp_setup_test_hook_receipt_matches \
        "$transition" "$checkpoint" "$idempotency_key"; then
        mp_setup_test_hook_reach_named "$transition" before-side-effect || return $?
        "$@" || return $?
        mp_setup_test_hook_reach_named "$transition" \
            after-side-effect-before-receipt || return $?
        mp_setup_test_hook_record_transition_receipt "$transition" \
            "$checkpoint" "$idempotency_key" || return $?
    fi
    mp_setup_test_hook_reach_named "$transition" \
        after-receipt-before-checkpoint || return $?
    mp_setup_test_hook_record_driver_checkpoint "$transition" \
        "$checkpoint" "$idempotency_key" || return $?
    mp_setup_test_hook_reach_named "$transition" \
        after-checkpoint-before-next-action
}

mp_setup_test_hook_enable() {
    local input="$1" run_id document existing
    mp_setup_test_hook_prepare || return $?
    jq -e '
        type == "object"
        and ((keys | sort) == ["enabled","format","run_id"])
        and .format == "mp-opt-commissioning-test-hook-enable-v1"
        and .enabled == true
        and (.run_id | test("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
    ' "$input" >/dev/null 2>&1 || return 64
    run_id="$(jq -r .run_id "$input")"
    exec 9>"$MP_SETUP_TEST_HOOK_LOCK"; flock -x 9 || return 75
    if [ -s "$MP_SETUP_TEST_HOOK_ENABLED" ]; then
        mp_setup_test_hook_validate_enabled "" || return 65
        existing="$(jq -r .run_id "$MP_SETUP_TEST_HOOK_ENABLED")"
        [ "$existing" = "$run_id" ] || return 75
    else
        [ ! -e "$MP_SETUP_TEST_HOOK_ARMED" ] && [ ! -L "$MP_SETUP_TEST_HOOK_ARMED" ] || return 65
        document="$(jq -cn --arg run "$run_id" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
            {format:"mp-opt-commissioning-test-hook-enable-v1",run_id:$run,
             enabled:true,enabled_at:$at}')" || return 1
        mp_setup_test_hook_write "$MP_SETUP_TEST_HOOK_ENABLED" "$document" || return $?
    fi
    jq -cn --arg run "$run_id" \
        '{format:"mp-opt-commissioning-test-hook-enable-result-v1",run_id:$run,state:"enabled"}'
}

mp_setup_test_hook_arm() {
    local input="$1" run_id fault_id transition boundary expected document
    mp_setup_test_hook_prepare || return $?
    jq -e '
        type == "object"
        and ((keys | sort) == ["boundary","fault_id","format","run_id","transition"])
        and .format == "mp-opt-commissioning-fault-v1"
        and (.run_id | test("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
        and (.fault_id | test("^fault-[0-9a-f]{16}$"))
        and (.transition | type == "string") and (.boundary | type == "string")
    ' "$input" >/dev/null 2>&1 || return 64
    run_id="$(jq -r .run_id "$input")"; fault_id="$(jq -r .fault_id "$input")"
    transition="$(jq -r .transition "$input")"; boundary="$(jq -r .boundary "$input")"
    mp_setup_test_hook_transition "$transition" && mp_setup_test_hook_boundary_name "$boundary" \
        || return 64
    expected="$(mp_setup_test_hook_fault_id "$transition" "$boundary")" || return 1
    [ "$fault_id" = "$expected" ] || return 64
    exec 9>"$MP_SETUP_TEST_HOOK_LOCK"; flock -x 9 || return 75
    mp_setup_test_hook_validate_enabled "$run_id" || return $?
    mp_setup_test_hook_validate_file "$MP_SETUP_TEST_HOOK_ARMED" || return $?
    if [ -s "$MP_SETUP_TEST_HOOK_ARMED" ]; then
        jq -e --arg run "$run_id" --arg fault "$fault_id" --arg transition "$transition" \
            --arg boundary "$boundary" '
            .run_id==$run and .fault_id==$fault and .transition==$transition
            and .boundary==$boundary and .state=="armed"
        ' "$MP_SETUP_TEST_HOOK_ARMED" >/dev/null 2>&1 || return 75
    else
        document="$(jq -cn --arg run "$run_id" --arg fault "$fault_id" \
            --arg transition "$transition" --arg boundary "$boundary" \
            --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
            {format:"mp-opt-commissioning-fault-v1",run_id:$run,fault_id:$fault,
             transition:$transition,boundary:$boundary,state:"armed",armed_at:$at}')" || return 1
        mp_setup_test_hook_write "$MP_SETUP_TEST_HOOK_ARMED" "$document" || return $?
    fi
    jq -cn --arg run "$run_id" --arg fault "$fault_id" --arg transition "$transition" \
        --arg boundary "$boundary" '
        {format:"mp-opt-commissioning-fault-result-v1",run_id:$run,fault_id:$fault,
         transition:$transition,boundary:$boundary,state:"armed"}'
}

mp_setup_test_hook_disarm() {
    local input="$1" run_id fault_id transition=null boundary=null
    mp_setup_test_hook_prepare || return $?
    jq -e '
        type == "object" and ((keys | sort) == ["fault_id","format","run_id"])
        and .format == "mp-opt-commissioning-fault-cancel-v1"
        and (.run_id | test("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
        and (.fault_id | test("^fault-[0-9a-f]{16}$"))
    ' "$input" >/dev/null 2>&1 || return 64
    run_id="$(jq -r .run_id "$input")"; fault_id="$(jq -r .fault_id "$input")"
    exec 9>"$MP_SETUP_TEST_HOOK_LOCK"; flock -x 9 || return 75
    mp_setup_test_hook_validate_enabled "$run_id" || return $?
    mp_setup_test_hook_validate_file "$MP_SETUP_TEST_HOOK_ARMED" || return $?
    if [ -s "$MP_SETUP_TEST_HOOK_ARMED" ]; then
        jq -e --arg run "$run_id" --arg fault "$fault_id" \
            '.run_id==$run and .fault_id==$fault and .state=="armed"' \
            "$MP_SETUP_TEST_HOOK_ARMED" >/dev/null 2>&1 || return 65
        transition="$(jq -r .transition "$MP_SETUP_TEST_HOOK_ARMED")"
        boundary="$(jq -r .boundary "$MP_SETUP_TEST_HOOK_ARMED")"
        rm -f "$MP_SETUP_TEST_HOOK_ARMED" || return 1
        sync -f "$MP_SETUP_TEST_HOOK_DIR" 2>/dev/null || return 1
    fi
    jq -cn --arg run "$run_id" --arg fault "$fault_id" --arg transition "$transition" \
        --arg boundary "$boundary" '
        {format:"mp-opt-commissioning-fault-result-v1",run_id:$run,fault_id:$fault,
         transition:(if $transition=="null" then null else $transition end),
         boundary:(if $boundary=="null" then null else $boundary end),state:"disarmed"}'
}

# Reach one controller-defined material boundary. A matching fault is consumed
# durably before exit 197 is returned, so resume cannot fire it twice.
mp_setup_test_hook_reach() {
    local input="$1" run_id fault_id transition boundary receipt temporary already=false
    mp_setup_test_hook_prepare || return $?
    jq -e '
        type == "object"
        and ((keys | sort) == ["boundary","fault_id","format","run_id","transition"])
        and .format == "mp-opt-commissioning-fault-boundary-v1"
        and (.run_id | test("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
        and (.fault_id | test("^fault-[0-9a-f]{16}$"))
    ' "$input" >/dev/null 2>&1 || return 64
    run_id="$(jq -r .run_id "$input")"; fault_id="$(jq -r .fault_id "$input")"
    transition="$(jq -r .transition "$input")"; boundary="$(jq -r .boundary "$input")"
    mp_setup_test_hook_transition "$transition" && mp_setup_test_hook_boundary_name "$boundary" \
        || return 64
    [ "$fault_id" = "$(mp_setup_test_hook_fault_id "$transition" "$boundary")" ] || return 64
    exec 9>"$MP_SETUP_TEST_HOOK_LOCK"; flock -x 9 || return 75
    mp_setup_test_hook_validate_enabled "$run_id" || return $?
    mp_setup_test_hook_validate_file "$MP_SETUP_TEST_HOOK_ARMED" || return $?
    mp_setup_test_hook_validate_file "$MP_SETUP_TEST_HOOK_TRIGGERED" || return $?
    if [ -s "$MP_SETUP_TEST_HOOK_TRIGGERED" ] && jq -e --arg run "$run_id" --arg fault "$fault_id" \
        'select(.run_id==$run and .fault_id==$fault and .state=="triggered")' \
        "$MP_SETUP_TEST_HOOK_TRIGGERED" >/dev/null 2>&1; then
        already=true
    fi
    if [ "$already" = true ]; then
        [ ! -e "$MP_SETUP_TEST_HOOK_ARMED" ] || rm -f "$MP_SETUP_TEST_HOOK_ARMED" || return 1
        jq -cn --arg run "$run_id" --arg fault "$fault_id" --arg transition "$transition" \
            --arg boundary "$boundary" '
            {format:"mp-opt-commissioning-fault-result-v1",run_id:$run,fault_id:$fault,
             transition:$transition,boundary:$boundary,state:"already-triggered"}'
        return 0
    fi
    [ -s "$MP_SETUP_TEST_HOOK_ARMED" ] || {
        jq -cn --arg run "$run_id" --arg fault "$fault_id" --arg transition "$transition" \
            --arg boundary "$boundary" '
            {format:"mp-opt-commissioning-fault-result-v1",run_id:$run,fault_id:$fault,
             transition:$transition,boundary:$boundary,state:"not-armed"}'
        return 0
    }
    jq -e --arg run "$run_id" --arg fault "$fault_id" --arg transition "$transition" \
        --arg boundary "$boundary" '
        .run_id==$run and .fault_id==$fault and .transition==$transition
        and .boundary==$boundary and .state=="armed"
    ' "$MP_SETUP_TEST_HOOK_ARMED" >/dev/null 2>&1 || {
        jq -cn --arg run "$run_id" --arg fault "$fault_id" --arg transition "$transition" \
            --arg boundary "$boundary" '
            {format:"mp-opt-commissioning-fault-result-v1",run_id:$run,fault_id:$fault,
             transition:$transition,boundary:$boundary,state:"not-armed"}'
        return 0
    }
    receipt="$(jq -cn --arg run "$run_id" --arg fault "$fault_id" \
        --arg transition "$transition" --arg boundary "$boundary" \
        --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
        {format:"mp-opt-commissioning-fault-result-v1",run_id:$run,fault_id:$fault,
         transition:$transition,boundary:$boundary,state:"triggered",triggered_at:$at}')" || return 1
    temporary="$(mktemp "$MP_SETUP_TEST_HOOK_DIR/.triggered.XXXXXX")" || return 1
    [ ! -s "$MP_SETUP_TEST_HOOK_TRIGGERED" ] || cat "$MP_SETUP_TEST_HOOK_TRIGGERED" > "$temporary" \
        || { rm -f "$temporary"; return 1; }
    printf '%s\n' "$receipt" >> "$temporary" || { rm -f "$temporary"; return 1; }
    chmod 600 "$temporary" && sync -f "$temporary" 2>/dev/null \
        && mv "$temporary" "$MP_SETUP_TEST_HOOK_TRIGGERED" \
        && rm -f "$MP_SETUP_TEST_HOOK_ARMED" \
        && sync -f "$MP_SETUP_TEST_HOOK_DIR" 2>/dev/null \
        || { rm -f "$temporary"; return 1; }
    printf '%s\n' "$receipt"
    return 197
}
