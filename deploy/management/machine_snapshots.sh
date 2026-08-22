#!/usr/bin/env bash
# Test-policy-only structured snapshot operations for the private laboratory.

MP_MACHINE_SNAPSHOT_RECEIPTS="${MP_MACHINE_SNAPSHOT_RECEIPTS:-$MP_STATE/snapshot-machine-receipts}"

mp_machine_snapshot_validate_artifact_root() {
    [ -d "$MP_SETUP_V2_ARTIFACTS" ] && [ ! -L "$MP_SETUP_V2_ARTIFACTS" ] \
        && [ "$(stat -c '%u:%a' "$MP_SETUP_V2_ARTIFACTS" 2>/dev/null)" \
            = "$(id -u):700" ]
}

mp_machine_snapshot_prepare_artifact_root() {
    mkdir -p "$MP_SETUP_V2_ARTIFACTS" && chmod 700 "$MP_SETUP_V2_ARTIFACTS" \
        && mp_machine_snapshot_validate_artifact_root
}

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
      elif $action == "export" then
        ((.values | keys) == ["snapshot_ref"])
        and (.values.snapshot_ref | type == "string"
          and test("^[0-9]{8}T[0-9]{6}Z_(database|secrets|full)_lab-[0-9a-f]{12}-[A-Za-z0-9][A-Za-z0-9._-]{0,39}$"))
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

mp_machine_snapshot_artifact_ticket() {
    local digest
    digest="$(printf '%s' "$1" | sha256sum | awk '{print $1}')" || return 1
    printf '%s-%s-4%s-8%s-%s\n' \
        "${digest:0:8}" "${digest:8:4}" "${digest:13:3}" \
        "${digest:17:3}" "${digest:20:12}"
}

mp_machine_snapshot_export_result() {
    local receipt="$1" reconciled="${2:-false}" directory package expected
    [ -f "$receipt" ] && [ ! -L "$receipt" ] \
        && [ "$(stat -c '%u:%a' "$receipt" 2>/dev/null)" = "$(id -u):600" ] \
        || return 65
    jq -e '
      .format == "mp-opt-machine-artifact-v1" and .kind == "portable-snapshot"
      and (.ticket | type == "string") and (.snapshot_ref | type == "string")
      and (.package_sha256 | test("^[0-9a-f]{64}$"))
      and (.package_size | type == "number" and floor == . and . >= 1 and . <= 4294967296)
      and (.snapshot_receipt_sha256 | test("^[0-9a-f]{64}$"))
    ' "$receipt" >/dev/null 2>&1 || return 65
    directory="$(dirname "$receipt")"
    mp_machine_snapshot_validate_artifact_root \
        && [ "$(readlink -f "$(dirname "$directory")" 2>/dev/null)" \
            = "$(readlink -f "$MP_SETUP_V2_ARTIFACTS" 2>/dev/null)" ] \
        && [ -d "$directory" ] && [ ! -L "$directory" ] \
        && [ "$(stat -c '%u:%a' "$directory" 2>/dev/null)" = "$(id -u):700" ] \
        || return 65
    package="$(jq -er .package_path "$receipt")" \
        || return 65
    [ "$(readlink -f "$package" 2>/dev/null)" \
        = "$(readlink -f "$directory" 2>/dev/null)/portable.mpopt-snapshot" ] \
        && [ -f "$package" ] && [ ! -L "$package" ] \
        && [ "$(stat -c '%u:%a' "$package" 2>/dev/null)" = "$(id -u):600" ] \
        && [ "$(stat -c %s "$package")" = "$(jq -r .package_size "$receipt")" ] \
        || return 65
    expected="$(jq -r .package_sha256 "$receipt")"
    [ "$(sha256sum "$package" | awk '{print $1}')" = "$expected" ] || return 65
    jq -c --argjson reconciled "$reconciled" '
      {format:"mp-opt-snapshot-export-result-v1",state:"completed",
       snapshot_ref,ticket,package_sha256,package_size,snapshot_receipt_sha256,
       reconciled:$reconciled}' "$receipt"
}

mp_machine_snapshot_export_artifact() {
    local directory="$1" key="$2" snapshot_ref ticket artifact package partial
    local receipt output package_id package_hash package_size temporary reconciled=false
    snapshot_ref="$(basename "$directory")"
    ticket="$(mp_machine_snapshot_artifact_ticket "$key")" || return 1
    artifact="$MP_SETUP_V2_ARTIFACTS/$ticket"
    package="$artifact/portable.mpopt-snapshot"
    partial="$package.partial"
    receipt="$artifact/receipt.json"
    mp_machine_snapshot_prepare_artifact_root || return 77
    if [ -e "$artifact" ] || [ -L "$artifact" ]; then
        [ -d "$artifact" ] && [ ! -L "$artifact" ] \
            && [ "$(stat -c '%u:%a' "$artifact" 2>/dev/null)" = "$(id -u):700" ] \
            || return 65
    else
        mkdir -m 0700 "$artifact" || return 1
    fi
    if [ -e "$partial" ] || [ -L "$partial" ]; then
        [ -f "$partial" ] && [ ! -L "$partial" ] \
            && [ "$(stat -c '%u:%a' "$partial" 2>/dev/null)" = "$(id -u):600" ] \
            || return 65
        rm -f -- "$partial" || return 1
    fi
    if [ ! -f "$package" ]; then
        [ ! -e "$package" ] && [ ! -L "$package" ] || return 65
        output="$(python3 "$MP_PORTABLE_TOOL" export --snapshot "$directory" \
            --output "$package" --source-node "$(mp_ha_role 2>/dev/null || printf standalone)")" \
            || return 20
    else
        reconciled=true
        [ ! -L "$package" ] \
            && [ "$(stat -c '%u:%a' "$package" 2>/dev/null)" = "$(id -u):600" ] \
            || return 65
        output="$(python3 "$MP_PORTABLE_TOOL" inspect --package "$package")" || return 65
    fi
    jq -e --arg snapshot_ref "$snapshot_ref" '
      .format == "mp-opt-portable-snapshot-2026-01"
      and (.status | IN("exported","valid"))
      and .snapshot_directory == $snapshot_ref
    ' <<< "$output" >/dev/null 2>&1 || return 65
    package_id="$(jq -er '.package_id | select(test("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))' <<< "$output")" \
        || return 65
    package_hash="$(jq -er '.sha256 | select(test("^[0-9a-f]{64}$"))' <<< "$output")" \
        || return 65
    package_size="$(stat -c %s "$package")" || return 1
    [ "$package_size" -ge 1 ] && [ "$package_size" -le 4294967296 ] \
        && [ "$(sha256sum "$package" | awk '{print $1}')" = "$package_hash" ] \
        || return 65
    if [ -e "$receipt" ] || [ -L "$receipt" ]; then
        mp_machine_snapshot_export_result "$receipt" true >/dev/null || return $?
        jq -e --arg ticket "$ticket" --arg ref "$snapshot_ref" --arg hash "$package_hash" \
            '.ticket == $ticket and .snapshot_ref == $ref and .package_sha256 == $hash' \
            "$receipt" >/dev/null || return 65
    else
        temporary="$(mktemp "$artifact/.receipt.XXXXXX")" || return 1
        jq -n --arg ticket "$ticket" --arg path "$package" --arg snapshot_ref "$snapshot_ref" \
            --arg package_id "$package_id" --arg package_hash "$package_hash" \
            --arg snapshot_hash "$(sha256sum "$directory/receipt.json" | awk '{print $1}')" \
            --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson size "$package_size" '
          {format:"mp-opt-machine-artifact-v1",kind:"portable-snapshot",ticket:$ticket,
           package_path:$path,package_id:$package_id,package_sha256:$package_hash,
           package_size:$size,snapshot_ref:$snapshot_ref,
           snapshot_receipt_sha256:$snapshot_hash,created_at:$created_at}' > "$temporary" \
            && chmod 600 "$temporary" && sync -f "$package" 2>/dev/null \
            && sync -f "$temporary" 2>/dev/null && mv "$temporary" "$receipt" \
            && sync -f "$artifact" 2>/dev/null \
            || { rm -f "$temporary"; return 1; }
    fi
    mp_machine_snapshot_export_result "$receipt" "$reconciled"
}

mp_machine_snapshot_import_result() {
    local receipt="$1" expected_sha="$2" snapshot_ref
    [ -f "$receipt" ] && [ ! -L "$receipt" ] \
        && [ "$(stat -c '%u:%a' "$receipt" 2>/dev/null)" = "$(id -u):600" ] \
        || return 65
    jq -e --arg sha "$expected_sha" '
      .format == "mp-opt-snapshot-machine-receipt-v1"
      and .action == "import" and .state == "completed"
      and (.result | type == "object"
        and ((keys | sort) == ["format","import_status","package_sha256",
          "package_size","reconciled","snapshot_receipt_sha256","snapshot_ref","state"])
        and .format == "mp-opt-snapshot-import-result-v1"
        and .state == "completed" and .package_sha256 == $sha
        and (.package_size | type == "number" and floor == . and . >= 1 and . <= 4294967296)
        and (.snapshot_receipt_sha256 | test("^[0-9a-f]{64}$"))
        and (.snapshot_ref | type == "string")
        and (.import_status | IN("imported","already-present"))
        and (.reconciled | type == "boolean"))
    ' "$receipt" >/dev/null 2>&1 || return 65
    snapshot_ref="$(jq -er .result.snapshot_ref "$receipt")" || return 65
    mp_machine_snapshot_validate_directory "$MP_SNAPSHOTS/$snapshot_ref" || return $?
    jq -c .result "$receipt"
}

mp_machine_snapshot_import_package() {
    local expected_sha="$1" key="$2" run_id package bytes actual request_sha receipt
    local receipt_state output status snapshot_ref directory state_tmp result
    [[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || return 64
    [[ "$key" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$ ]] || return 64
    mp_machine_require_local_owner || return 77
    mp_setup_test_hook_policy || return 77
    run_id="snapshot-import-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mp_setup_execution_acquire "$run_id" snapshot || return $?
    trap 'mp_secure_remove_file "${MP_MACHINE_INPUT_FILE:-}"; mp_setup_execution_release' EXIT
    mp_initialise_paths || return 77
    mp_machine_snapshot_prepare_receipt_directory || return $?
    package="$(mktemp "$MP_STATE/snapshot-machine-import.XXXXXX")" || return 1
    MP_MACHINE_INPUT_FILE="$package"; chmod 600 "$package"
    dd bs=1048576 count=4097 status=none > "$package" || return 1
    bytes="$(stat -c %s "$package")" || return 1
    [ "$bytes" -ge 1 ] && [ "$bytes" -le 4294967296 ] || return 64
    actual="$(sha256sum "$package" | awk '{print $1}')" || return 1
    [ "$actual" = "$expected_sha" ] || return 65
    request_sha="$(printf 'import:%s:%s' "$key" "$expected_sha" | sha256sum | awk '{print $1}')"
    receipt="$(mp_machine_snapshot_receipt_path "$key")"
    if [ -e "$receipt" ] || [ -L "$receipt" ]; then
        mp_machine_validate_regular_file "$receipt" 600 || return 77
        jq -e --arg request_sha "$request_sha" '
          .format == "mp-opt-snapshot-machine-receipt-v1"
          and .request_sha256 == $request_sha and .action == "import"
          and (.state | IN("started","completed"))' "$receipt" >/dev/null || return 65
        receipt_state="$(jq -r .state "$receipt")"
        if [ "$receipt_state" = completed ]; then
            mp_machine_snapshot_import_result "$receipt" "$expected_sha" || return $?
            mp_secure_remove_file "$package"; MP_MACHINE_INPUT_FILE=""
            mp_setup_execution_release; trap - EXIT
            return 0
        fi
    else
        mp_machine_snapshot_write_state "$receipt" "$request_sha" import started || return 1
    fi
    mp_portable_initialise || return 1
    output="$(python3 "$MP_PORTABLE_TOOL" import --package "$package" \
        --snapshots "$MP_SNAPSHOTS" --expected-sha256 "$expected_sha")" || return 20
    jq -e --arg sha "$expected_sha" '
      .format == "mp-opt-portable-snapshot-2026-01"
      and (.status | IN("imported","already-present"))
      and .package_sha256 == $sha
      and (.snapshot_directory | type == "string"
        and test("^[0-9]{8}T[0-9]{6}Z_(database|secrets|full)_[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"))
    ' <<< "$output" >/dev/null 2>&1 || return 65
    status="$(jq -er .status <<< "$output")"; snapshot_ref="$(jq -er .snapshot_directory <<< "$output")"
    directory="$MP_SNAPSHOTS/$snapshot_ref"
    mp_machine_snapshot_validate_directory "$directory" || return $?
    if [ -e "$MP_PORTABLE_LAST_IMPORT_STATE" ] || [ -L "$MP_PORTABLE_LAST_IMPORT_STATE" ]; then
        mp_machine_validate_regular_file "$MP_PORTABLE_LAST_IMPORT_STATE" 600 || return 77
        jq -e '.format == "mp-opt-portable-import-receipt-v1"' \
            "$MP_PORTABLE_LAST_IMPORT_STATE" >/dev/null 2>&1 || return 65
    fi
    state_tmp="$(mktemp "$MP_STATE/portable-last-import.XXXXXX")" || return 1
    jq -n --arg snapshot "$snapshot_ref" --arg path "$directory" \
        --arg package_hash "$expected_sha" --arg status "$status" \
        --arg imported_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
      {format:"mp-opt-portable-import-receipt-v1",snapshot:$snapshot,
       snapshot_path:$path,package_sha256:$package_hash,status:$status,
       imported_at:$imported_at}' > "$state_tmp" \
        && chmod 600 "$state_tmp" && sync -f "$state_tmp" 2>/dev/null \
        && mv "$state_tmp" "$MP_PORTABLE_LAST_IMPORT_STATE" \
        && sync -f "$MP_STATE" 2>/dev/null \
        || { rm -f "$state_tmp"; return 1; }
    result="$(jq -cn --arg snapshot_ref "$snapshot_ref" --arg sha "$expected_sha" \
        --arg status "$status" --arg receipt_sha \
        "$(sha256sum "$directory/receipt.json" | awk '{print $1}')" \
        --argjson size "$bytes" '
      {format:"mp-opt-snapshot-import-result-v1",state:"completed",
       snapshot_ref:$snapshot_ref,package_sha256:$sha,package_size:$size,
       snapshot_receipt_sha256:$receipt_sha,import_status:$status,
       reconciled:($status == "already-present")}')" || return 1
    mp_machine_snapshot_write_state "$receipt" "$request_sha" import completed "$result" || return 1
    printf '%s\n' "$result"
    mp_secure_remove_file "$package"; MP_MACHINE_INPUT_FILE=""
    mp_setup_execution_release; trap - EXIT
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
            if [ "$action" = export ]; then
                local artifact_ticket artifact_receipt authoritative
                artifact_ticket="$(jq -er '.result.ticket | select(type == "string")' "$receipt")" \
                    || return 65
                artifact_receipt="$MP_SETUP_V2_ARTIFACTS/$artifact_ticket/receipt.json"
                authoritative="$(mp_machine_snapshot_export_result "$artifact_receipt" true)" \
                    || return $?
                jq -e --argjson expected "$(jq -c '.result | del(.reconciled)' "$receipt")" '
                  (del(.reconciled) == $expected)' <<< "$authoritative" >/dev/null || return 65
            fi
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
      export)
        directory="$MP_SNAPSHOTS/$(jq -er .values.snapshot_ref "$input")"
        mp_machine_snapshot_validate_directory "$directory" || return $?
        result="$(mp_machine_snapshot_export_artifact "$directory" "$key")" || return $?
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
