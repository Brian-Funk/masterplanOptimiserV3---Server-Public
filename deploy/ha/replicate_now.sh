#!/usr/bin/env bash
# Capture, encrypt and send one complete point-in-time application state.
set -Eeuo pipefail
umask 077

MP_ROOT="${MP_ROOT:-/opt/masterplan}"
MP_HA_HOME="${MP_HA_HOME:-/etc/mp-opt-ha}"
MP_HA_STATE="${MP_HA_STATE:-$HOME/.local/state/mp-opt-ha-replication}"
job_id="${1:-$(cat /proc/sys/kernel/random/uuid)}"
request_file="${2:-}"
[[ "$job_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || exit 1
[ -z "$request_file" ] || { [ -f "$request_file" ] && [ ! -L "$request_file" ]; } || exit 1

# shellcheck source=../management/common.sh
source "$MP_ROOT/deploy/management/common.sh"
mp_load_ha_config
[ "$HA_MODE" = "ha" ] || exit 1
[ "$(jq -r '.holder_node_id // empty' "$MP_ROOT/runtime/ha-control.json")" = "$HA_NODE_ID" ] || exit 1
generation="$(jq -r '.generation // 0' "$MP_ROOT/runtime/ha-control.json")"
[[ "$generation" =~ ^[1-9][0-9]*$ ]] || exit 1
[ -n "${HA_PEER_SSH:-}" ] && [ -n "${HA_PEER_NODE_ID:-}" ] || exit 1

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
stage="$(mktemp -d "$MP_HA_STATE/outgoing/.stage.XXXXXX")"
snapshot_input=""
snapshot_pid=""
cleanup() {
    local result="$?"
    set +e
    if [ -n "$snapshot_input" ]; then
        printf 'ROLLBACK;\n\\q\n' >"$snapshot_input" 2>/dev/null || true
    fi
    [ -z "$snapshot_pid" ] || wait "$snapshot_pid" 2>/dev/null || true
    rm -rf "$stage"
    exit "$result"
}
trap cleanup EXIT
mkdir -p "$stage/payload/database" "$stage/payload/config/secrets" \
    "$stage/payload/recovery" "$stage/payload/evidence"
chmod -R go-rwx "$stage"

mp_compose_init
"${MP_COMPOSE[@]}" up -d db >/dev/null
mp_retire_root_bootstrap_secret

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
    'SELECT pg_advisory_lock(5571046919607735876);' \
    'BEGIN ISOLATION LEVEL REPEATABLE READ;' \
    "SELECT 'SNAPSHOT:' || pg_export_snapshot();" >&"$snapshot_input"
snapshot_id=""
while IFS= read -r line <&"$snapshot_output"; do
    case "$line" in
        SNAPSHOT:*) snapshot_id="${line#SNAPSHOT:}"; break ;;
    esac
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
sudo -n cp -a "$MP_ROOT/state/evidence/." "$stage/payload/evidence/"
sudo -n chown -R "$(id -u):$(id -g)" "$stage/payload/evidence"
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

release="$(mp_release_hash)"
manifest_args=(create \
    --payload "$stage/payload" --cluster "$HA_CLUSTER_ID" \
    --source "$HA_NODE_ID" --target "$HA_PEER_NODE_ID" \
    --bundle "$job_id" --generation "$generation" --release "$release" \
    --output "$stage/manifest.json")
[ -z "$request_file" ] || manifest_args+=(--request "$request_file")
python3 "$MP_ROOT/deploy/ha/replication_bundle.py" "${manifest_args[@]}"

recipient="$(tr -d '\r\n' < "$MP_HA_HOME/peer-age-recipient")"
[[ "$recipient" =~ ^age1[0-9a-z]{58}$ ]] || { echo "Peer age recipient is invalid." >&2; exit 1; }
tar -C "$stage" -cf - manifest.json payload \
    | age -r "$recipient" -o "$stage/bundle.age"
archive_hash="$(sha256sum "$stage/bundle.age" | awk '{print $1}')"
set +e
response="$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$HA_PEER_SSH" \
    "/opt/masterplan/deploy/ha/receive_replication_bundle.sh '$job_id' '$archive_hash'" \
    < "$stage/bundle.age")"
ssh_status="$?"
set -e
if [ "$ssh_status" -eq 255 ]; then
    echo "The replication peer is unreachable." >&2
    exit 20
fi
if [ "$ssh_status" -eq 75 ]; then
    echo "The replication peer is completing a management operation." >&2
    exit 23
fi
if [ "$ssh_status" -ne 0 ]; then
    echo "The replication peer rejected the copy." >&2
    exit 21
fi
[ "$response" = "ACCEPTED:$job_id:$archive_hash" ] \
    || { echo "The replication acknowledgement was invalid." >&2; exit 22; }
printf '%s\n' "$response"
