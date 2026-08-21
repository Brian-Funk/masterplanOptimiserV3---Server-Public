#!/usr/bin/env bash
# Test-policy-only structured snapshot operations for the private laboratory.

MP_MACHINE_SNAPSHOT_RECEIPTS="${MP_MACHINE_SNAPSHOT_RECEIPTS:-$MP_STATE/snapshot-machine-receipts}"

mp_machine_snapshot_prepare_receipt_directory() {
    mp_create_private_owner_directory_chain "$MP_MACHINE_SNAPSHOT_RECEIPTS" \
        && chmod 700 "$MP_MACHINE_SNAPSHOT_RECEIPTS" \
        && [ -d "$MP_MACHINE_SNAPSHOT_RECEIPTS" ] \
        && [ ! -L "$MP_MACHINE_SNAPSHOT_RECEIPTS" ] \
        && [ "$(stat -c '%u:%a' "$MP_MACHINE_SNAPSHOT_RECEIPTS" 2>/dev/null)" \
            = "$(id -u):700" ] || return 77
}

mp_machine_snapshot_receipt_path() {
    printf '%s/%s.json\n' "$MP_MACHINE_SNAPSHOT_RECEIPTS" \
        "$(printf '%s' "$1" | sha256sum | awk '{print $1}')"
}

mp_machine_snapshot_read_input() {
    local target="$1" action="$2" bytes
    head -c 8193 > "$target" || return 1
    bytes="$(wc -c < "$target" | tr -d ' ')"
    [ "$bytes" -gt 0 ] && [ "$bytes" -le 8192 ] || return 64
    chmod 600 "$target"
    jq -e --arg action "$action" '
      type == "object"
      and ((keys | sort) == ["action","format","idempotency_key","values"])
      and .format == "mp-opt-snapshot-machine-request-v1"
      and .action == $action
      and (.idempotency_key | type == "string"
        and test("^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"))
      and (.values | type == "object")
      and (if $action == "create" then
        ((.values | keys | sort) == ["name","type"])
        and (.values.type | IN("database","secrets","full"))
        and (.values.name | type == "string" and test("^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$"))
      elif $action == "verify" then
        ((.values | keys | sort) == ["recovery_identity","snapshot_ref"])
        and (.values.snapshot_ref | type == "string"
          and test("^[0-9]{8}T[0-9]{6}Z_(database|secrets|full)_lab-[0-9a-f]{12}-[A-Za-z0-9][A-Za-z0-9._-]{0,39}$"))
        and (.values.recovery_identity | type == "string" and length >= 40 and length <= 4096)
      else false end)' "$target" >/dev/null 2>&1 || return 64
}

mp_machine_snapshot_validate_directory() {
    local directory="$1" owner expected actual file
    case "$(readlink -f -- "$directory" 2>/dev/null)" in
      "$(readlink -f -- "$MP_SNAPSHOTS")"/*) ;;
      *) return 65 ;;
    esac
    owner="$(id -u)"
    [ -d "$directory" ] && [ ! -L "$directory" ] \
        && [ "$(stat -c '%u:%a' "$directory" 2>/dev/null)" = "$owner:700" ] \
        && mp_snapshot_receipt_is_v2 "$directory" \
        && [ -f "$directory/snapshot.tar.age" ] && [ ! -L "$directory/snapshot.tar.age" ] \
        && [ -f "$directory/archive.sha256" ] && [ ! -L "$directory/archive.sha256" ] \
        || return 65
    for file in receipt.json snapshot.tar.age archive.sha256; do
        [ "$(stat -c '%u:%a' "$directory/$file" 2>/dev/null)" = "$owner:600" ] \
            || return 65
    done
    expected="$(jq -er '.archive_sha256 | select(test("^[0-9a-f]{64}$"))' \
        "$directory/receipt.json")" || return 65
    actual="$(sha256sum "$directory/snapshot.tar.age" | awk '{print $1}')" || return 1
    [ "$expected" = "$actual" ] || return 65
}

mp_machine_snapshot_safe_result() {
    local directory="$1" reconciled="${2:-false}"
    mp_machine_snapshot_validate_directory "$directory" || return $?
    jq -c --arg ref "$(basename "$directory")" --argjson reconciled "$reconciled" '
      {format:"mp-opt-snapshot-machine-result-v1",state:"completed",snapshot_ref:$ref,
       type,name,created_at,archive_sha256,archive_size,verification,recovery_status,
       encryption:{scheme:.encryption.scheme,
         recipient_sha256:.encryption.recipient_sha256,
         recovery_key_id:.encryption.recovery_key_id},
       storage:{local:(.storage.local // "unknown"),
         off_server:(.storage.off_server // "not-copied")},reconciled:$reconciled}' \
      "$directory/receipt.json"
}

mp_machine_snapshot_find_exact() {
    local type="$1" name="$2" directory match="" count=0
    [ -d "$MP_SNAPSHOTS" ] && [ ! -L "$MP_SNAPSHOTS" ] || return 1
    while IFS= read -r directory; do
        [ -f "$directory/receipt.json" ] && [ ! -L "$directory/receipt.json" ] || continue
        if jq -e --arg type "$type" --arg name "$name" \
            '.format == "mp-opt-snapshot-receipt-v2" and .type == $type and .name == $name' \
            "$directory/receipt.json" >/dev/null 2>&1; then
            match="$directory"; count=$((count + 1))
        fi
    done < <(find -P "$MP_SNAPSHOTS" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -print | sort)
    [ "$count" -le 1 ] || return 65
    [ "$count" -eq 1 ] || return 1
    printf '%s\n' "$match"
}

mp_machine_snapshot_write_state() {
    local receipt="$1" request_sha="$2" action="$3" state="$4" result="${5:-null}" temporary
    temporary="$(mktemp "$MP_MACHINE_SNAPSHOT_RECEIPTS/.receipt.XXXXXX")" || return 1
    jq -cn --arg request_sha "$request_sha" --arg action "$action" --arg state "$state" \
        --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson result "$result" '
      {format:"mp-opt-snapshot-machine-receipt-v1",request_sha256:$request_sha,
       action:$action,state:$state,updated_at:$at,result:$result}' > "$temporary" \
      && chmod 600 "$temporary" && sync -f "$temporary" 2>/dev/null \
      && mv "$temporary" "$receipt" && sync -f "$MP_MACHINE_SNAPSHOT_RECEIPTS" 2>/dev/null \
      || { rm -f "$temporary"; return 1; }
}

mp_machine_snapshot_action() {
    local action="$1" run_id input key request_sha receipt receipt_state type requested_name
    local derived_name directory result identity_file="" status=0
    mp_machine_require_local_owner || return 77
    mp_setup_test_hook_policy || return 77
    run_id="snapshot-${action}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mp_setup_execution_acquire "$run_id" snapshot || return $?
    trap 'mp_secure_remove_file "${MP_MACHINE_INPUT_FILE:-}"; mp_secure_remove_file "${MP_MACHINE_IDENTITY_FILE:-}"; mp_setup_execution_release' EXIT
    mp_initialise_paths || return 77
    mp_machine_snapshot_prepare_receipt_directory || return $?
    input="$(mktemp "$MP_STATE/snapshot-machine-input.XXXXXX")" || return 1
    MP_MACHINE_INPUT_FILE="$input"; chmod 600 "$input"
    mp_machine_snapshot_read_input "$input" "$action" || return $?
    key="$(jq -er .idempotency_key "$input")" || return 64
    request_sha="$(sha256sum "$input" | awk '{print $1}')"
    receipt="$(mp_machine_snapshot_receipt_path "$key")"
    if [ -e "$receipt" ] || [ -L "$receipt" ]; then
        mp_machine_validate_regular_file "$receipt" 600 || return 77
        jq -e --arg request_sha "$request_sha" --arg action "$action" '
          .format == "mp-opt-snapshot-machine-receipt-v1"
          and .request_sha256 == $request_sha and .action == $action
          and (.state | IN("started","completed"))' "$receipt" >/dev/null || return 65
        receipt_state="$(jq -r .state "$receipt")"
        if [ "$receipt_state" = completed ]; then
            jq -c .result "$receipt"
            mp_secure_remove_file "$input"; MP_MACHINE_INPUT_FILE=""
            mp_setup_execution_release; trap - EXIT
            return 0
        fi
    else
        mp_machine_snapshot_write_state "$receipt" "$request_sha" "$action" started || return 1
    fi
    case "$action" in
      create)
        type="$(jq -er .values.type "$input")"; requested_name="$(jq -er .values.name "$input")"
        derived_name="lab-$(printf '%s' "$key" | sha256sum | awk '{print substr($1,1,12)}')-${requested_name}"
        if directory="$(mp_machine_snapshot_find_exact "$type" "$derived_name")"; then
            result="$(mp_machine_snapshot_safe_result "$directory" true)" || return $?
        else
            status=$?; [ "$status" -eq 1 ] || return "$status"
            directory="$(mp_snapshot_create "$type" "$derived_name")" || return 20
            result="$(mp_machine_snapshot_safe_result "$directory" false)" || return $?
        fi
        ;;
      verify)
        directory="$MP_SNAPSHOTS/$(jq -er .values.snapshot_ref "$input")"
        mp_machine_snapshot_validate_directory "$directory" || return $?
        identity_file="$(mp_setup_machine_identity_file \
            "$(jq -r .values.recovery_identity "$input")")" || return $?
        MP_MACHINE_IDENTITY_FILE="$identity_file"
        mp_snapshot_verify_path "$directory" "$identity_file" || return 20
        mp_secure_remove_file "$identity_file"; MP_MACHINE_IDENTITY_FILE=""
        result="$(mp_machine_snapshot_safe_result "$directory" false)" || return $?
        ;;
      *) return 64 ;;
    esac
    mp_machine_snapshot_write_state "$receipt" "$request_sha" "$action" completed "$result" || return 1
    printf '%s\n' "$result"
    mp_secure_remove_file "$input"; MP_MACHINE_INPUT_FILE=""
    mp_setup_execution_release; trap - EXIT
}

mp_machine_snapshot_list() {
    local directory results='[]'
    mp_machine_require_local_owner || return 77
    mp_setup_test_hook_policy || return 77
    [ -d "$MP_SNAPSHOTS" ] && [ ! -L "$MP_SNAPSHOTS" ] \
        && [ "$(stat -c '%u:%a' "$MP_SNAPSHOTS" 2>/dev/null)" = "$(id -u):700" ] \
        || return 77
    while IFS= read -r directory; do
        mp_machine_snapshot_validate_directory "$directory" || return $?
        results="$(jq -cn --argjson prior "$results" \
            --argjson current "$(mp_machine_snapshot_safe_result "$directory" false)" \
            '$prior + [$current]')" || return 1
        [ "$(jq 'length' <<< "$results")" -le 100 ] || return 65
    done < <(find -P "$MP_SNAPSHOTS" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -print | sort -r)
    jq -cn --argjson snapshots "$results" \
      '{format:"mp-opt-snapshot-machine-list-v1",snapshots:$snapshots,count:($snapshots|length)}'
}
