#!/usr/bin/env bash
# Structured host-local HA lifecycle adapter for the staging laboratory.

MP_MACHINE_HA_RECEIPTS="${MP_MACHINE_HA_RECEIPTS:-$MP_STATE/ha-machine-receipts}"

mp_machine_ha_prepare_receipts() {
    mp_create_private_owner_directory_chain "$MP_MACHINE_HA_RECEIPTS" \
        && chmod 700 "$MP_MACHINE_HA_RECEIPTS" \
        && [ -d "$MP_MACHINE_HA_RECEIPTS" ] \
        && [ ! -L "$MP_MACHINE_HA_RECEIPTS" ] \
        && [ "$(stat -c '%u:%a' "$MP_MACHINE_HA_RECEIPTS" 2>/dev/null)" \
            = "$(id -u):700" ] || return 77
}

mp_machine_ha_receipt_path() {
    local key="$1"
    printf '%s/%s.json\n' "$MP_MACHINE_HA_RECEIPTS" \
        "$(printf '%s' "$key" | sha256sum | awk '{print $1}')"
}

mp_machine_ha_status() {
    local control
    mp_machine_require_local_owner || return 77
    mp_load_ha_config || return 65
    [ "$HA_ROLE" = dynamic ] || return 65
    [ -s "$MP_ROOT/runtime/ha-control.json" ] \
        && [ -f "$MP_ROOT/runtime/ha-control.json" ] \
        && [ ! -L "$MP_ROOT/runtime/ha-control.json" ] || return 20
    control="$(cat "$MP_ROOT/runtime/ha-control.json")" || return 20
    jq -ce --arg node "$HA_NODE_ID" --arg peer "$HA_PEER_NODE_ID" '
      . as $control
      | ($control.nodes // []) as $nodes
      | select(($control.holder_node_id | type) == "string")
      | select(($control.generation | type) == "number" and $control.generation >= 1)
      | select($control.holder_node_id == $node or $control.holder_node_id == $peer)
      | select(($control.routing_ready | type) == "boolean")
      | select(($control.automatic_failover | type) == "boolean")
      | select(($nodes | type) == "array" and ($nodes | length) == 2)
      | select($nodes | all(
          (.node_id | type) == "string" and (.healthy | type) == "boolean"))
      | select(([$nodes[].node_id] | sort) == ([$node,$peer] | sort))
      | {format:"mp-opt-ha-machine-status-v1",mode:"ha",local_node_id:$node,
         peer_node_id:$peer,holder_node_id:$control.holder_node_id,
         generation:$control.generation,routing_ready:$control.routing_ready,
         automatic_failover:$control.automatic_failover,
         nodes:($nodes | map({node_id,healthy,bundle_generation,
           bundle_id:(.bundle_id // null),release_hash:(.release_hash // null),
           critical_pending:(.critical_pending // false)}) | sort_by(.node_id))}' \
        <<< "$control" || return 65
}

mp_machine_ha_runtime_status() {
    local lease_active=false backend_running=false
    mp_machine_require_local_owner || return 77
    mp_load_ha_config || return 65
    [ "$HA_ROLE" = dynamic ] || return 65
    mp_compose_init_existing_runtime ha || return 65
    systemctl is-active --quiet mp-opt-ha-lease.service 2>/dev/null \
        && lease_active=true
    "${MP_COMPOSE[@]}" ps --status running --services 2>/dev/null \
        | grep -Fxq backend && backend_running=true
    jq -cn --arg node "$HA_NODE_ID" \
        --argjson lease "$lease_active" --argjson backend "$backend_running" \
        '{format:"mp-opt-ha-runtime-status-v1",local_node_id:$node,
          lease_agent_active:$lease,backend_running:$backend}'
}

mp_machine_ha_read_input() {
    local target="$1" action="$2" bytes
    head -c 8193 > "$target" || return 1
    bytes="$(wc -c < "$target" | tr -d ' ')"
    [ "$bytes" -gt 0 ] && [ "$bytes" -le 8192 ] || return 64
    chmod 600 "$target"
    jq -e --arg action "$action" '
      type == "object"
      and ((keys | sort) == ["action","format","idempotency_key","run_id","values"])
      and .format == "mp-opt-ha-machine-request-v1"
      and .action == $action
      and (.run_id | type == "string" and test("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
      and (.idempotency_key | type == "string" and test("^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"))
      and (.values | type == "object")
      and (.values.expected_holder | type == "string" and test("^[a-z][a-z0-9-]{1,62}$"))
      and (.values.expected_generation | type == "number" and floor == . and . >= 1)
      and (if $action == "readiness" then
        ((.values | keys | sort) == ["expected_generation","expected_holder"])
      elif $action == "handover" then
        ((.values | keys | sort) == ["expected_generation","expected_holder","target_node_id"])
        and (.values.target_node_id | type == "string" and test("^[a-z][a-z0-9-]{1,62}$"))
      elif $action == "automatic" then
        ((.values | keys | sort) == ["expected_generation","expected_holder","state"])
        and (.values.state | IN("enabled","disabled"))
      elif $action == "fault" then
        ((.values | keys | sort) == ["expected_generation","expected_holder","state"])
        and (.values.state | IN("offline","online"))
      else false end)' "$target" >/dev/null 2>&1 || return 64
}

mp_machine_ha_record_receipt() {
    local receipt="$1" request_sha="$2" action="$3" run_id="$4" result="$5" temporary
    mp_machine_ha_prepare_receipts || return $?
    temporary="$(mktemp "$MP_MACHINE_HA_RECEIPTS/.receipt.XXXXXX")" || return 1
    jq -cn --arg request_sha "$request_sha" --arg action "$action" \
        --arg run_id "$run_id" --arg completed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --argjson result "$result" \
        '{format:"mp-opt-ha-machine-receipt-v1",request_sha256:$request_sha,
          action:$action,run_id:$run_id,completed_at:$completed_at,result:$result}' \
        > "$temporary" \
        && chmod 600 "$temporary" && sync -f "$temporary" 2>/dev/null \
        && mv "$temporary" "$receipt" \
        && sync -f "$MP_MACHINE_HA_RECEIPTS" 2>/dev/null \
        || { rm -f "$temporary"; return 1; }
}

mp_machine_ha_fault_result() {
    local state="$1" control="$2" runtime
    runtime="$(mp_machine_ha_runtime_status)" || return $?
    jq -cn --arg state "$state" --argjson control "$control" \
      --argjson runtime "$runtime" \
      '{format:"mp-opt-ha-runtime-fault-result-v1",state:$state,
        local_node_id:$runtime.local_node_id,
        holder_node_id:$control.holder_node_id,generation:$control.generation,
        routing_ready:$control.routing_ready,
        automatic_failover:$control.automatic_failover,
        lease_agent_active:$runtime.lease_agent_active,
        backend_running:$runtime.backend_running}'
}

mp_machine_ha_fault_offline() {
    local control="$1" runtime
    jq -e '.automatic_failover == true' <<< "$control" >/dev/null || return 20
    mp_compose_init_existing_runtime ha || return 65
    # Remove the current writer before withholding its heartbeat. This avoids
    # a window where the witness can promote the peer while the old backend is
    # still accepting traffic.
    "${MP_COMPOSE[@]}" stop backend >/dev/null || return 1
    sudo -n systemctl stop mp-opt-ha-lease.service >/dev/null || return 1
    runtime="$(mp_machine_ha_runtime_status)" || return $?
    jq -e '.lease_agent_active == false and .backend_running == false' \
        <<< "$runtime" >/dev/null || return 20
    mp_machine_ha_fault_result offline "$control"
}

mp_machine_ha_fault_online() {
    local expected_holder="$1" expected_generation="$2" peer_node_id="$3"
    local control="" runtime="" ready=false
    [ "$expected_holder" = "$peer_node_id" ] || return 20
    mp_compose_init_existing_runtime ha || return 65
    sudo -n systemctl start mp-opt-ha-lease.service >/dev/null || return 1
    for _ in $(seq 1 30); do
        if control="$(mp_machine_ha_status 2>/dev/null)" \
          && jq -e --arg holder "$expected_holder" \
            --argjson generation "$expected_generation" '
            .holder_node_id == $holder and .generation == $generation
            and .automatic_failover == true' <<< "$control" >/dev/null; then
            runtime="$(mp_machine_ha_runtime_status 2>/dev/null || true)"
            if jq -e '.lease_agent_active == true and .backend_running == false' \
                <<< "$runtime" >/dev/null; then
                ready=true
                break
            fi
        fi
        sleep 1
    done
    if [ "$ready" != true ]; then
        # Starting the returning lease agent is the only way to refresh its
        # deliberately stale local witness observation. If the exact peer
        # holder/generation cannot then be proven, restore the simulated-loss
        # state before returning so a stale request cannot leave a writer or
        # lease agent running unexpectedly.
        sudo -n systemctl stop mp-opt-ha-lease.service >/dev/null 2>&1 || true
        "${MP_COMPOSE[@]}" stop backend >/dev/null 2>&1 || true
        return 20
    fi
    mp_machine_ha_fault_result online "$control"
}

mp_machine_ha_action() {
    local action="$1" input request_sha idempotency run_id receipt current
    local expected_holder expected_generation result status=0 target next
    local local_node_id peer_node_id
    mp_machine_require_local_owner || return 77
    mp_setup_test_hook_policy || return 77
    mp_machine_ha_prepare_receipts || return $?
    input="$(mktemp "$MP_STATE/ha-machine-input.XXXXXX")" || return 1
    chmod 600 "$input"; MP_MACHINE_INPUT_FILE="$input"
    trap 'mp_secure_remove_file "${MP_MACHINE_INPUT_FILE:-}"; mp_setup_execution_release; mp_unlock' EXIT
    mp_machine_ha_read_input "$input" "$action" || return $?
    run_id="$(jq -er .run_id "$input")" || return 64
    idempotency="$(jq -er .idempotency_key "$input")" || return 64
    request_sha="$(sha256sum "$input" | awk '{print $1}')"
    receipt="$(mp_machine_ha_receipt_path "$idempotency")"
    if [ -e "$receipt" ] || [ -L "$receipt" ]; then
        mp_machine_validate_regular_file "$receipt" 600 || return 77
        jq -e --arg request_sha "$request_sha" --arg action "$action" \
          --arg run_id "$run_id" '
          .format == "mp-opt-ha-machine-receipt-v1"
          and .request_sha256 == $request_sha and .action == $action
          and .run_id == $run_id and (.result | type == "object")' \
          "$receipt" >/dev/null || return 65
        jq -c .result "$receipt"
        mp_secure_remove_file "$input"; MP_MACHINE_INPUT_FILE=""; trap - EXIT
        return 0
    fi
    mp_setup_execution_acquire "$run_id" "ha-$action" || return $?
    mp_lock || return 75
    export MP_MANAGEMENT_LOCK_HELD=1
    current="$(mp_machine_ha_status)" || return $?
    # Status is the validated authoritative identity projection for this
    # action.  It is evaluated in a subshell, so configuration variables loaded
    # there must not be read from the parent shell.
    local_node_id="$(jq -er .local_node_id <<< "$current")" || return 65
    peer_node_id="$(jq -er .peer_node_id <<< "$current")" || return 65
    expected_holder="$(jq -er .values.expected_holder "$input")"
    expected_generation="$(jq -er .values.expected_generation "$input")"
    if [ "$action" = handover ]; then
        target="$(jq -er .values.target_node_id "$input")" || return 64
        if [ "$expected_holder" = "$local_node_id" ] \
          && [ "$target" = "$peer_node_id" ] \
          && jq -e --arg target "$target" --argjson expected "$expected_generation" '
          .holder_node_id == $target and .generation == ($expected + 1)
          and .automatic_failover == false' <<< "$current" >/dev/null; then
            result="$(jq -cn --arg source "$expected_holder" --arg target "$target" \
              --argjson previous_generation "$expected_generation" \
              --argjson generation "$((expected_generation + 1))" \
              --argjson routing "$(jq -er .routing_ready <<< "$current")" \
              '{format:"mp-opt-ha-handover-result-v1",source_node_id:$source,
                target_node_id:$target,previous_generation:$previous_generation,
                generation:$generation,routing_ready:$routing,
                automatic_failover:false,reconciled:true}')" || return 65
            mp_machine_ha_record_receipt "$receipt" "$request_sha" "$action" "$run_id" \
                "$result" || return 1
            printf '%s\n' "$result"
            mp_secure_remove_file "$input"; MP_MACHINE_INPUT_FILE=""
            mp_unlock; mp_setup_execution_release; trap - EXIT
            return 0
        fi
    elif [ "$action" = automatic ]; then
        next="$(jq -er .values.state "$input")" || return 64
    elif [ "$action" = fault ]; then
        next="$(jq -er .values.state "$input")" || return 64
    fi
    if [ "$action" = fault ] && [ "$next" = online ]; then
        result="$(mp_machine_ha_fault_online "$expected_holder" \
            "$expected_generation" "$peer_node_id")" || status=$?
    else
        [ "$(jq -r .holder_node_id <<< "$current")" = "$expected_holder" ] \
            && [ "$(jq -r .generation <<< "$current")" = "$expected_generation" ] \
            || return 20
        case "$action" in
          readiness)
            [ "$expected_holder" = "$local_node_id" ] || return 20
            mp_ha_active_verification_readiness >/dev/null || status=20
            [ "$status" -ne 0 ] || result="$(jq -cn --arg holder "$expected_holder" \
              --argjson generation "$expected_generation" \
              '{format:"mp-opt-ha-readiness-result-v1",ready:true,
                holder_node_id:$holder,generation:$generation}')"
            ;;
          handover)
            [ "$expected_holder" = "$local_node_id" ] || return 20
            result="$(mp_ha_planned_switchover_apply "$target")" || status=$?
            ;;
          automatic)
            [ "$expected_holder" = "$local_node_id" ] || return 20
            result="$(mp_ha_automatic_failover_apply "$next")" || status=$?
            ;;
          fault)
            [ "$expected_holder" = "$local_node_id" ] || return 20
            [ "$next" = offline ] || return 64
            result="$(mp_machine_ha_fault_offline "$current")" || status=$?
            ;;
          *) return 64 ;;
        esac
    fi
    [ "$status" -eq 0 ] || return "$status"
    jq -e 'type == "object"' <<< "$result" >/dev/null || return 65
    mp_machine_ha_record_receipt "$receipt" "$request_sha" "$action" "$run_id" \
        "$result" || return 1
    printf '%s\n' "$result"
    mp_secure_remove_file "$input"; MP_MACHINE_INPUT_FILE=""
    mp_unlock; mp_setup_execution_release; trap - EXIT
}
