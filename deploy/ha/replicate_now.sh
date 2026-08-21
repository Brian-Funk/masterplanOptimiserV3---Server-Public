#!/usr/bin/env bash
# Capture, encrypt and send one complete point-in-time application state.
set -Eeuo pipefail
umask 077

MP_ROOT="${MP_ROOT:-/opt/masterplan}"
MP_HA_HOME="${MP_HA_HOME:-/etc/mp-opt-ha}"
MP_HA_STATE="${MP_HA_STATE:-$HOME/.local/state/mp-opt-ha-replication}"
job_id="${1:-$(cat /proc/sys/kernel/random/uuid)}"
request_file="${2:-}"
sender_started_ms="$(date +%s%3N)"
[[ "$job_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || exit 1
[ -z "$request_file" ] || { [ -f "$request_file" ] && [ ! -L "$request_file" ]; } || exit 1

update_operation_stage() {
    local stage_name="$1" state_name="${2:-pending}" operation_id="" operation_sequence=""
    [ -n "$request_file" ] || return 0
    [ "$(jq -r '.format // empty' "$request_file" 2>/dev/null)" = "mp-opt-replication-batch-v2" ] || return 0
    while IFS=$'\t' read -r operation_id operation_sequence; do
        [ -n "$operation_id" ] || continue
        jq -n --arg operation "$operation_id" --arg state "$state_name" \
            --arg stage "$stage_name" --arg bundle "$job_id" \
            --arg updated "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
            --argjson sequence "$operation_sequence" \
            '{format:"mp-opt-ha-operation-result-v1",operation_id:$operation,state:$state,stage:$stage,mutation_sequence:$sequence,bundle_id:$bundle,bundle_sha256:null,generation:null,error_code:null,updated_at:$updated,accepted_at:null}' \
            > "$MP_ROOT/runtime/ha-operation-results/.${operation_id}.tmp"
        chmod 0644 "$MP_ROOT/runtime/ha-operation-results/.${operation_id}.tmp"
        mv "$MP_ROOT/runtime/ha-operation-results/.${operation_id}.tmp" \
            "$MP_ROOT/runtime/ha-operation-results/${operation_id}.json"
    done < <(jq -r '.operations[]? | [.marker.operation_id,.marker.mutation_sequence] | @tsv' "$request_file")
}

peer_confirms_bundle() {
    local receiver="" expected_operations="" confirmed_operations=""
    receiver="$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$HA_PEER_SSH" \
        "cat /opt/masterplan/runtime/ha-receiver.json" 2>/dev/null || true)"
    [ "$(jq -r '.last_bundle_id // empty' <<< "$receiver" 2>/dev/null)" = "$job_id" ] || return 1
    [ "$(jq -r '.last_bundle_sha256 // empty' <<< "$receiver" 2>/dev/null)" = "$archive_hash" ] || return 1
    if [ -n "$request_file" ] && [ "$(jq -r '.format // empty' "$request_file")" = "mp-opt-replication-batch-v2" ]; then
        expected_operations="$(jq -c '[.operations[]?.marker | {operation_id,mutation_sequence}] | sort_by(.mutation_sequence)' "$request_file")"
        confirmed_operations="$(jq -c '[.protection_operations[]?] | sort_by(.mutation_sequence)' <<< "$receiver" 2>/dev/null)"
        [ "$expected_operations" = "$confirmed_operations" ] || return 1
    fi
    # The peer may have accepted and fsynced the receiver receipt immediately
    # before an injected crash window or a lost SSH acknowledgement. Reconcile
    # the expiring witness transfer guard here as the receiver would have done;
    # failure remains harmless because the guard has a bounded expiry.
    python3 "$MP_ROOT/deploy/ha/witness_control.py" complete-transfer \
        "$job_id" "$archive_hash" >/dev/null 2>&1 || true
    return 0
}

reconcile_peer_join_state() {
    local output attempt
    for attempt in 1 2 3; do
        output="$(ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
            -o ClearAllForwardings=yes "$HA_PEER_SSH" \
            '/usr/local/bin/mp-opt setup reconcile --json' 2>/dev/null)" || {
            sleep 1
            continue
        }
        [ "$(jq -r '.run_state // empty' <<< "$output" 2>/dev/null)" = complete ] \
            && return 0
        sleep 1
    done
    return 1
}

assert_current_holder() {
    local control holder current_generation routing_ready
    control="$(cat "$MP_ROOT/runtime/ha-control.json" 2>/dev/null || true)"
    holder="$(jq -r '.holder_node_id // empty' <<< "$control" 2>/dev/null || true)"
    current_generation="$(jq -r '.generation // 0' <<< "$control" 2>/dev/null || true)"
    routing_ready="$(jq -r '.routing_ready // false' <<< "$control" 2>/dev/null || true)"
    if [ "$holder" != "$HA_NODE_ID" ] \
        || [ "$current_generation" != "$generation" ] \
        || [ "$routing_ready" != true ]; then
        echo "Replication stopped because this node no longer holds the original writer lease." >&2
        exit 24
    fi
}

# shellcheck source=../management/common.sh
source "$MP_ROOT/deploy/management/common.sh"
# shellcheck source=../management/test_hooks.sh
source "$MP_ROOT/deploy/management/test_hooks.sh"
if ! mp_compose_init_existing_runtime \
    || ! mp_validate_action_profile_permissions evidence; then
    echo "The installed runtime permission contract is unsafe; replication did not start." >&2
    exit 25
fi
for required_service in db backend; do
    "${MP_COMPOSE[@]}" ps --status running --services 2>/dev/null \
        | grep -qx "$required_service" || {
        echo "The required local service is not running: $required_service" >&2
        exit 25
    }
done
if ! mp_validate_retired_root_bootstrap_secret_existing_runtime; then
    echo "The retired root-bootstrap contract is not valid; replication did not start." >&2
    exit 26
fi
[ "$HA_MODE" = "ha" ] || exit 1
[ "$(jq -r '.holder_node_id // empty' "$MP_ROOT/runtime/ha-control.json")" = "$HA_NODE_ID" ] || exit 1
generation="$(jq -r '.generation // 0' "$MP_ROOT/runtime/ha-control.json")"
[[ "$generation" =~ ^[1-9][0-9]*$ ]] || exit 1
[ -n "${HA_PEER_SSH:-}" ] && [ -n "${HA_PEER_NODE_ID:-}" ] || exit 1
assert_current_holder

# Capture database and shared files while no CLI recovery/configuration action
# can change their relationship.
mkdir -p "$MP_STATE"
chmod 700 "$MP_STATE"
if [ "${MP_MANAGEMENT_LOCK_HELD:-0}" != 1 ]; then
    exec 8>"$MP_LOCK_FILE"
    flock -n 8 || { echo "A management operation is running; replication was deferred." >&2; exit 74; }
fi

mkdir -p "$MP_HA_STATE/outgoing"
chmod 700 "$MP_HA_STATE" "$MP_HA_STATE/outgoing"
exec 9>"$MP_HA_STATE/replication.lock"
flock -n 9 || { echo "Replication is already running." >&2; exit 75; }
hook_checkpoint="${MP_SETUP_MACHINE_CHECKPOINT:-replicated}"
hook_key="${MP_SETUP_MACHINE_IDEMPOTENCY_KEY:-$job_id}"
hook_stage=false
if mp_setup_test_hook_policy && [ -s "$MP_SETUP_TEST_HOOK_ENABLED" ]; then
    mp_setup_test_hook_prepare || exit $?
    mp_setup_test_hook_validate_enabled "" || exit $?
    hook_stage=true
fi
if [ "$hook_stage" = true ]; then
    hook_stage_digest="$(printf '%s\0%s' "$job_id" "$hook_key" \
        | sha256sum | awk '{print substr($1,1,40)}')"
    stage="$MP_HA_STATE/outgoing/.setup-$hook_stage_digest"
    if [ -e "$stage" ] || [ -L "$stage" ]; then
        [ -d "$stage" ] && [ ! -L "$stage" ] \
            && [ "$(stat -c '%u:%a' "$stage")" = "$(id -u):700" ] \
            || { echo "The resumable replication stage is unsafe." >&2; exit 77; }
    else
        mkdir -m 700 "$stage"
        sync -f "$MP_HA_STATE/outgoing" 2>/dev/null || exit 1
    fi
else
    stage="$(mktemp -d "$MP_HA_STATE/outgoing/.stage.XXXXXX")"
fi
snapshot_input=""
snapshot_pid=""
accepted_receipt=""
cleanup() {
    local result="$?"
    set +e
    if [ -n "$snapshot_input" ]; then
        printf 'ROLLBACK;\n\\q\n' 2>/dev/null >"$snapshot_input" || true
    fi
    [ -z "$snapshot_pid" ] || wait "$snapshot_pid" 2>/dev/null || true
    [ -z "$accepted_receipt" ] || rm -f -- "$accepted_receipt"
    if [ "$result" -ne 197 ] || [ "$hook_stage" != true ]; then
        rm -rf "$stage"
    fi
    exit "$result"
}
trap cleanup EXIT

load_captured_bundle() {
    local receipt="$stage/capture.json" expected_release
    [ -f "$receipt" ] && [ ! -L "$receipt" ] \
        && [ "$(stat -c '%u:%a' "$receipt")" = "$(id -u):600" ] || return 1
    expected_release="$(mp_release_hash)" || return 1
    jq -e --arg bundle "$job_id" --arg cluster "$HA_CLUSTER_ID" \
        --arg source "$HA_NODE_ID" --arg target "$HA_PEER_NODE_ID" \
        --arg release "$expected_release" --argjson generation "$generation" '
        type == "object"
        and ((keys | sort) == ["bundle_id","captured_at","cluster_id","format","generation","release_hash","sha256","source_node_id","target_node_id"])
        and .format == "mp-opt-ha-captured-bundle-v1"
        and .bundle_id == $bundle and .cluster_id == $cluster
        and .source_node_id == $source and .target_node_id == $target
        and .release_hash == $release and .generation == $generation
        and (.sha256 | test("^[0-9a-f]{64}$"))
        and (.captured_at | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))
    ' "$receipt" >/dev/null 2>&1 || return 65
    [ -f "$stage/bundle.age" ] && [ ! -L "$stage/bundle.age" ] \
        && [ "$(stat -c '%u:%a' "$stage/bundle.age")" = "$(id -u):600" ] \
        || return 65
    archive_hash="$(jq -r .sha256 "$receipt")"
    [ "$(sha256sum "$stage/bundle.age" | awk '{print $1}')" = "$archive_hash" ] \
        || return 65
    release="$expected_release"
    capture_completed_ms="$(date +%s%3N)"
}

capture_bundle() {
    local capture_receipt="$stage/capture.json" temporary
    if [ -e "$capture_receipt" ] || [ -L "$capture_receipt" ]; then
        load_captured_bundle
        return $?
    fi
    [ -z "$(find "$stage" -mindepth 1 -maxdepth 1 -print -quit)" ] \
        || { echo "The incomplete resumable replication stage is not empty." >&2; return 65; }
    mkdir -p "$stage/payload/database" "$stage/payload/config/secrets" \
        "$stage/payload/recovery" "$stage/payload/evidence"
    chmod -R go-rwx "$stage"

mp_wait_for_database 30 \
    || { echo "The final local database process did not become ready for replication." >&2; exit 1; }

# Hold the same PostgreSQL advisory lock used by every evidence mutation and
# keep an exported snapshot alive until both the dump and ledger copy have
# been captured. Ordinary application writes continue; evidence-backed writes
# wait briefly so the database and signed files cannot cross in flight.
coproc SNAPSHOT_SESSION {
    "${MP_COMPOSE[@]}" exec -T db psql -XAtq -v ON_ERROR_STOP=1 \
        -U masterplan -d masterplan
}
snapshot_input="${SNAPSHOT_SESSION[1]}"
snapshot_output="${SNAPSHOT_SESSION[0]}"
snapshot_pid="$SNAPSHOT_SESSION_PID"
printf '%s\n' \
    "SET lock_timeout TO '30s';" \
    'SELECT pg_advisory_lock(5571046919607735876);' \
    'BEGIN ISOLATION LEVEL REPEATABLE READ;' \
    "SELECT 'SNAPSHOT:' || pg_export_snapshot();" >&"$snapshot_input"
snapshot_id=""
snapshot_deadline=$((SECONDS + 30))
while [ "$SECONDS" -lt "$snapshot_deadline" ]; do
    snapshot_wait_seconds=$((snapshot_deadline - SECONDS))
    if IFS= read -r -t "$snapshot_wait_seconds" line <&"$snapshot_output"; then
        case "$line" in
            SNAPSHOT:*) snapshot_id="${line#SNAPSHOT:}"; break ;;
        esac
    else
        break
    fi
done
[[ "$snapshot_id" =~ ^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{8}-[0-9]+$ ]] \
    || { echo "A consistent database snapshot could not be exported." >&2; exit 1; }

"${MP_COMPOSE[@]}" exec -T db pg_dump -U masterplan -d masterplan -Fc \
    --snapshot="$snapshot_id" \
    > "$stage/payload/database/masterplan.dump"
"${MP_COMPOSE[@]}" exec -T db pg_restore --list \
    < "$stage/payload/database/masterplan.dump" >/dev/null

[ -f "$MP_ROOT/.env" ] && [ ! -L "$MP_ROOT/.env" ] \
    && [ "$(stat -c '%a' "$MP_ROOT/.env")" = 600 ] \
    || { echo "The shared environment file is missing or unsafe." >&2; exit 1; }
python3 "$MP_ROOT/deploy/ha/replication_bundle.py" filter-env \
    --source "$MP_ROOT/.env" --output "$stage/payload/config/shared.env"
python3 "$MP_ROOT/deploy/ha/replication_bundle.py" prepare-recovery-state \
    --source "$MP_MANUAL_EXPORT_STATE" \
    --output "$stage/payload/recovery/manual-recovery-export.json"
[ -f "$MP_RECIPIENT_FILE" ] && [ ! -L "$MP_RECIPIENT_FILE" ] \
    && [ "$(stat -c '%a' "$MP_RECIPIENT_FILE")" = 600 ] \
    || { echo "The snapshot recovery recipient is missing or unsafe." >&2; exit 1; }
recovery_recipient="$(tr -d '\r\n' < "$MP_RECIPIENT_FILE")"
[[ "$recovery_recipient" =~ ^age1[0-9a-z]{58}$ ]] \
    || { echo "The snapshot recovery recipient is invalid." >&2; exit 1; }
install -m 0600 "$MP_RECIPIENT_FILE" "$stage/payload/recovery/recovery-recipient"
for secret in secret_key ip_hmac_key vapid_private_key root_bootstrap_token smtp_token evidence_signing_key; do
    source_secret="$MP_ROOT/secrets/$secret"
    expected_mode="$(mp_expected_protected_file_mode "$source_secret")"
    [ -f "$source_secret" ] && [ ! -L "$source_secret" ] \
        && [ "$(stat -c '%a' "$source_secret")" = "$expected_mode" ] \
        || { echo "Shared secret is missing or unsafe: $secret" >&2; exit 1; }
    install -m 0600 "$source_secret" "$stage/payload/config/secrets/$secret"
done

# The database outbox and signed filesystem ledger are one logical state.
# The exported snapshot and advisory lock above make this copy atomic with the
# database view, and the receiver independently verifies the pair again.
[ -d "$MP_ROOT/state/evidence" ] && [ ! -L "$MP_ROOT/state/evidence" ] \
    || { echo "The evidence store is missing or unsafe." >&2; exit 1; }
# The backend already owns the mode-0700 evidence store. Stream a read-only
# archive through that unprivileged container so the hardened replication
# service does not need sudo or broader host filesystem permissions.
"${MP_COMPOSE[@]}" exec -T backend sh -ec \
    '[ "$(id -u)" = 10001 ] && test -x /evidence && test -r /evidence' \
    || { echo "The Backend identity cannot read the protected evidence store." >&2; exit 1; }
"${MP_COMPOSE[@]}" exec -T backend tar -C /evidence -cf - . \
    | tar --no-same-owner -C "$stage/payload/evidence" -xf - \
    || { echo "The protected evidence store could not be captured for replication." >&2; exit 1; }
find "$stage/payload/evidence" -type d -exec chmod 700 {} +
find "$stage/payload/evidence" -type f -exec chmod 600 {} +
[ -s "$stage/payload/evidence/ledger/chain-head.json" ] \
    && [ -s "$stage/payload/evidence/public/instance_signing_key.pub" ] \
    || { echo "The evidence chain has not been initialized." >&2; exit 1; }
db_evidence="$("${MP_COMPOSE[@]}" exec -T db psql -XAtq -v ON_ERROR_STOP=1 \
    -U masterplan -d masterplan -c \
    "BEGIN ISOLATION LEVEL REPEATABLE READ; SET TRANSACTION SNAPSHOT '$snapshot_id'; SELECT last_sequence::text || ':' || COALESCE(head_sha256, '') || ':' || (SELECT count(*)::text FROM evidence_operations WHERE state <> 'complete') FROM evidence_chain_state WHERE id=1; COMMIT;")"
db_evidence="$(printf '%s\n' "$db_evidence" | grep -E '^[0-9]+:[0-9a-f]{64}:[0-9]+$' | tail -n 1)"
file_evidence="$(jq -r '.sequence|tostring' "$stage/payload/evidence/ledger/chain-head.json"):$(jq -r '.head_sha256' "$stage/payload/evidence/ledger/chain-head.json"):0"
[ "$db_evidence" = "$file_evidence" ] \
    || { echo "The database and evidence ledger are not one recoverable point." >&2; exit 1; }
printf 'COMMIT;\n\\q\n' >&"$snapshot_input"
snapshot_input=""
wait "$snapshot_pid"
snapshot_pid=""

# Capture can overlap a witness decision. Never construct or send a bundle
# after the source has lost the exact generation it started with. The receiver
# independently enforces the same holder relationship at acceptance time.
assert_current_holder

release="$(mp_release_hash)"
manifest_args=(create \
    --payload "$stage/payload" --cluster "$HA_CLUSTER_ID" \
    --source "$HA_NODE_ID" --target "$HA_PEER_NODE_ID" \
    --bundle "$job_id" --generation "$generation" --release "$release" \
    --output "$stage/manifest.json")
[ -z "$request_file" ] || manifest_args+=(--request "$request_file")
python3 "$MP_ROOT/deploy/ha/replication_bundle.py" "${manifest_args[@]}"
update_operation_stage transferring

recipient="$(tr -d '\r\n' < "$MP_HA_HOME/peer-age-recipient")"
[[ "$recipient" =~ ^age1[0-9a-z]{58}$ ]] || { echo "Peer age recipient is invalid." >&2; exit 1; }
tar -C "$stage" -cf - manifest.json payload \
    | age -r "$recipient" -o "$stage/bundle.age"
archive_hash="$(sha256sum "$stage/bundle.age" | awk '{print $1}')"
capture_completed_ms="$(date +%s%3N)"
rm -rf "$stage/manifest.json" "$stage/payload"
temporary="$(mktemp "$stage/.capture.XXXXXX")" || return 1
jq -n --arg bundle "$job_id" --arg sha256 "$archive_hash" \
    --arg source "$HA_NODE_ID" --arg target "$HA_PEER_NODE_ID" \
    --arg cluster "$HA_CLUSTER_ID" \
    --arg release "$release" --arg captured "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --argjson generation "$generation" \
    '{format:"mp-opt-ha-captured-bundle-v1",bundle_id:$bundle,sha256:$sha256,cluster_id:$cluster,
      source_node_id:$source,target_node_id:$target,release_hash:$release,
      generation:$generation,captured_at:$captured}' > "$temporary" \
    && chmod 600 "$temporary" && sync -f "$stage/bundle.age" 2>/dev/null \
    && sync -f "$temporary" 2>/dev/null && mv "$temporary" "$capture_receipt" \
    && sync -f "$stage" 2>/dev/null \
    || { rm -f "$temporary"; return 1; }
}

mp_setup_test_hook_run_driver_transition bundle.capture "$hook_checkpoint.capture" \
    "$hook_key" capture_bundle || exit $?
load_captured_bundle || { echo "The captured replication bundle receipt is invalid." >&2; exit 1; }
transfer_bundle() {
    if peer_confirms_bundle; then
        response="ACCEPTED:$job_id:$archive_hash"
        return 0
    fi
    assert_current_holder
    set +e
    response="$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$HA_PEER_SSH" \
        "/opt/masterplan/deploy/ha/receive_replication_bundle.sh '$job_id' '$archive_hash'" \
        < "$stage/bundle.age")"
    ssh_status="$?"
    set -e
    # A peer-side adjacent hook deliberately exits 197. Preserve that exact
    # result before lost-ack reconciliation so the controller observes the
    # requested crash window. The protected encrypted stage is retained and a
    # retry first reconciles the authoritative peer acceptance receipt.
    [ "$ssh_status" -ne 197 ] || return 197
    if [ "$ssh_status" -ne 0 ] || [ "$response" != "ACCEPTED:$job_id:$archive_hash" ]; then
        if peer_confirms_bundle; then
            response="ACCEPTED:$job_id:$archive_hash"
            ssh_status=0
        fi
    fi
    if [ "$ssh_status" -eq 255 ]; then
        echo "The replication peer is unreachable." >&2
        return 20
    fi
    if [ "$ssh_status" -eq 75 ]; then
        echo "The replication peer is completing a management operation." >&2
        return 23
    fi
    if [ "$ssh_status" -ne 0 ]; then
        echo "The replication peer rejected the copy." >&2
        return 21
    fi
    [ "$response" = "ACCEPTED:$job_id:$archive_hash" ] \
        || { echo "The replication acknowledgement was invalid." >&2; return 22; }
}

mp_setup_test_hook_run_driver_transition bundle.transfer "$hook_checkpoint.transfer" \
    "$hook_key" transfer_bundle || exit $?
response="ACCEPTED:$job_id:$archive_hash"
transfer_completed_ms="$(date +%s%3N)"
update_operation_stage verifying
accepted_receipt="$(mktemp "$MP_ROOT/runtime/ha-last-accepted-bundle.XXXXXX")"
jq -n --arg bundle "$job_id" --arg sha256 "$archive_hash" \
    --arg source "$HA_NODE_ID" --arg target "$HA_PEER_NODE_ID" \
    --arg cluster "$HA_CLUSTER_ID" --arg release "$release" \
    --arg accepted "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --argjson generation "$generation" \
    '{format:"mp-opt-ha-sender-acceptance-v1",bundle_id:$bundle,sha256:$sha256,
      source_node_id:$source,target_node_id:$target,cluster_id:$cluster,
      release_hash:$release,generation:$generation,
      accepted_at:$accepted}' > "$accepted_receipt"
chmod 0600 "$accepted_receipt"
sync -f "$accepted_receipt" 2>/dev/null || { rm -f "$accepted_receipt"; exit 1; }
mv "$accepted_receipt" "$MP_ROOT/runtime/ha-last-accepted-bundle.json"
accepted_receipt=""
sync -f "$MP_ROOT/runtime" 2>/dev/null || exit 1
if ! reconcile_peer_join_state; then
    # The exact bundle remains accepted. A later replication or an explicit
    # setup reconciliation can finish the peer's presentation state without
    # transferring or restoring the database again.
    echo "The peer accepted the copy but retained its resumable setup state." >&2
fi
printf 'MP_SENDER_TIMING capture_ms=%s transfer_round_trip_ms=%s\n' \
    "$((capture_completed_ms - sender_started_ms))" \
    "$((transfer_completed_ms - capture_completed_ms))" >&2
printf '%s\n' "$response"
