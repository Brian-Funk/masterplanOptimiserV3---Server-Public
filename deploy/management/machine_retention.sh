#!/usr/bin/env bash
# Test-policy-only retention-cycle adapter for the private staging laboratory.

MP_MACHINE_RETENTION_RECEIPTS="${MP_MACHINE_RETENTION_RECEIPTS:-$MP_STATE/retention-machine-receipts}"

mp_machine_retention_prepare_receipts() {
    mp_create_private_owner_directory_chain "$MP_MACHINE_RETENTION_RECEIPTS" \
        && chmod 700 "$MP_MACHINE_RETENTION_RECEIPTS" \
        && [ -d "$MP_MACHINE_RETENTION_RECEIPTS" ] \
        && [ ! -L "$MP_MACHINE_RETENTION_RECEIPTS" ] \
        && [ "$(stat -c '%u:%a' "$MP_MACHINE_RETENTION_RECEIPTS" 2>/dev/null)" \
            = "$(id -u):700" ] || return 77
}

mp_machine_retention_read_input() {
    local target="$1" bytes
    head -c 4097 > "$target" || return 1
    bytes="$(wc -c < "$target" | tr -d ' ')"
    [ "$bytes" -gt 0 ] && [ "$bytes" -le 4096 ] || return 64
    chmod 600 "$target"
    jq -e '
      type == "object"
      and ((keys | sort) == ["action","format","idempotency_key","values"])
      and .format == "mp-opt-retention-machine-request-v1"
      and .action == "run-as-of"
      and (.idempotency_key | type == "string"
        and test("^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"))
      and ((.values | keys) == ["as_of"])
      and (.values.as_of | type == "string"
        and test("^20[0-9]{2}-(0[1-9]|1[0-2])-([0-2][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"))
    ' "$target" >/dev/null 2>&1 || return 64
}

mp_machine_retention_receipt_path() {
    printf '%s/%s.json\n' "$MP_MACHINE_RETENTION_RECEIPTS" \
        "$(printf '%s' "$1" | sha256sum | awk '{print $1}')"
}

mp_machine_retention_write_receipt() {
    local destination="$1" request_sha="$2" state="$3" result="${4:-null}" temporary
    temporary="$(mktemp "$MP_MACHINE_RETENTION_RECEIPTS/.receipt.XXXXXX")" || return 1
    jq -cn --arg request_sha "$request_sha" --arg state "$state" \
        --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson result "$result" '
      {format:"mp-opt-retention-machine-receipt-v1",action:"run-as-of",
       request_sha256:$request_sha,state:$state,updated_at:$at,result:$result}
    ' > "$temporary" && chmod 600 "$temporary" \
        && sync -f "$temporary" 2>/dev/null && mv "$temporary" "$destination" \
        && sync -f "$MP_MACHINE_RETENTION_RECEIPTS" 2>/dev/null \
        || { rm -f "$temporary"; return 1; }
}

mp_machine_retention_execute() {
    local mode="$1" as_of="$2" program
    program='import contextlib, io, json, os, sys
from datetime import datetime, timezone
with contextlib.redirect_stdout(io.StringIO()):
    from app.core.retention import retention_status, run_retention_cycle_once
    from app.db.database import SessionLocal
    from app.core.database_tenancy import root_service_context

as_of = os.environ["MP_RETENTION_AS_OF"]
when = datetime.strptime(as_of, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
mode = sys.argv[1]
reconciled = False
counts = {}
state = "not-completed"
if mode == "probe":
    db = SessionLocal()
    try:
        root_service_context(db, scope="retention_worker")
        status = retention_status(db)
        started = status.get("last_started_at")
        completed = status.get("last_completed_at")
        normalise = lambda value: (value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None else value.astimezone(timezone.utc))
        if (started is not None and completed is not None
                and normalise(started) == when
                and normalise(completed) == when
                and status.get("last_result") == "success"):
            counts = status.get("last_counts") or {}
            state = "completed"
            reconciled = True
    finally:
        db.close()
elif mode == "run":
    with contextlib.redirect_stdout(io.StringIO()):
        counts = run_retention_cycle_once(now=when)
    state = "completed"
else:
    raise SystemExit(64)
print(json.dumps({"format":"mp-opt-retention-machine-result-v1",
    "state":state,"as_of":as_of,"counts":counts,"reconciled":reconciled},
    sort_keys=True,separators=(",", ":")))'
    MP_RETENTION_AS_OF="$as_of" "${MP_COMPOSE[@]}" exec -T \
        -e MP_RETENTION_AS_OF backend python -c "$program" "$mode"
}

mp_machine_retention_validate_result() {
    local document="$1" as_of="$2" expected_state="$3"
    jq -e --arg as_of "$as_of" --arg state "$expected_state" '
      type == "object"
      and ((keys | sort) == ["as_of","counts","format","reconciled","state"])
      and .format == "mp-opt-retention-machine-result-v1"
      and .state == $state and .as_of == $as_of
      and (.reconciled | type == "boolean")
      and (.counts | type == "object" and length <= 32)
      and ([.counts | to_entries[] |
        (.key | test("^[a-z][a-z0-9_]{0,63}$"))
        and (.value | type == "number" and floor == . and . >= 0)] | all)
    ' <<< "$document" >/dev/null 2>&1
}

mp_machine_retention_run_as_of() {
    local run_id input key as_of request_sha receipt receipt_state probe result
    mp_machine_require_local_owner || return 77
    mp_setup_test_hook_policy || return 77
    run_id="retention-as-of-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mp_setup_execution_acquire "$run_id" retention || return $?
    trap 'mp_secure_remove_file "${MP_MACHINE_INPUT_FILE:-}"; mp_setup_execution_release' EXIT
    mp_initialise_paths || return 77
    mp_machine_retention_prepare_receipts || return $?
    input="$(mktemp "$MP_STATE/retention-machine-input.XXXXXX")" || return 1
    MP_MACHINE_INPUT_FILE="$input"; chmod 600 "$input"
    mp_machine_retention_read_input "$input" || return $?
    key="$(jq -er .idempotency_key "$input")" || return 64
    as_of="$(jq -er .values.as_of "$input")" || return 64
    [ "$(date -u -d "$as_of" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" = "$as_of" ] \
        || return 64
    request_sha="$(sha256sum "$input" | awk '{print $1}')"
    receipt="$(mp_machine_retention_receipt_path "$key")"
    if [ -e "$receipt" ] || [ -L "$receipt" ]; then
        mp_machine_validate_regular_file "$receipt" 600 || return 77
        jq -e --arg request_sha "$request_sha" '
          .format == "mp-opt-retention-machine-receipt-v1"
          and .action == "run-as-of" and .request_sha256 == $request_sha
          and (.state | IN("started","completed"))' "$receipt" >/dev/null || return 65
        receipt_state="$(jq -r .state "$receipt")"
        if [ "$receipt_state" = completed ]; then
            jq -c .result "$receipt"
            mp_secure_remove_file "$input"; MP_MACHINE_INPUT_FILE=""
            mp_setup_execution_release; trap - EXIT
            return 0
        fi
        probe="$(mp_machine_retention_execute probe "$as_of")" || return 20
        mp_machine_retention_validate_result "$probe" "$as_of" not-completed || {
            mp_machine_retention_validate_result "$probe" "$as_of" completed || return 65
        }
        if [ "$(jq -r .state <<< "$probe")" = completed ]; then
            result="$probe"
        fi
    else
        mp_machine_retention_write_receipt "$receipt" "$request_sha" started || return 1
    fi
    if [ -z "${result:-}" ]; then
        result="$(mp_machine_retention_execute run "$as_of")" || return 20
        mp_machine_retention_validate_result "$result" "$as_of" completed || return 65
    fi
    mp_machine_retention_write_receipt "$receipt" "$request_sha" completed "$result" || return 1
    printf '%s\n' "$result"
    mp_secure_remove_file "$input"; MP_MACHINE_INPUT_FILE=""
    mp_setup_execution_release; trap - EXIT
}
