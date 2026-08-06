#!/usr/bin/env bash
# Receive and atomically accept one encrypted replication bundle.
set -Eeuo pipefail
umask 077

MP_ROOT="${MP_ROOT:-/opt/masterplan}"
MP_HA_HOME="${MP_HA_HOME:-/etc/mp-opt-ha}"
MP_HA_STATE="${MP_HA_STATE:-$HOME/.local/state/mp-opt-ha-replication}"
bundle_id="${1:?bundle id required}"
expected_hash="${2:?archive hash required}"
[[ "$bundle_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || exit 1
[[ "$expected_hash" =~ ^[0-9a-f]{64}$ ]] || exit 1

# shellcheck source=../management/common.sh
source "$MP_ROOT/deploy/management/common.sh"
# shellcheck source=../management/snapshots.sh
source "$MP_ROOT/deploy/management/snapshots.sh"
mp_load_ha_config
[ "$HA_MODE" = "ha" ] || exit 1
source_holder="$(jq -r '.holder_node_id // empty' "$MP_ROOT/runtime/ha-control.json")"
[ "$source_holder" = "$HA_PEER_NODE_ID" ] || { echo "Peer is not the current lease holder." >&2; exit 1; }

# Do not swap the peer database while its operator is testing recovery or
# changing node-local service state.
mkdir -p "$MP_STATE"
chmod 700 "$MP_STATE"
exec 8>"$MP_LOCK_FILE"
flock -n 8 || { echo "A management operation is running; the previous copy was retained." >&2; exit 75; }

mkdir -p "$MP_HA_STATE/incoming"
chmod 700 "$MP_HA_STATE" "$MP_HA_STATE/incoming"
exec 9>"$MP_HA_STATE/receiver.lock"
flock -n 9 || exit 75
stage="$(mktemp -d "$MP_HA_STATE/incoming/.stage.XXXXXX")"
stage_db=""
rollback_db=""
database_swap_started=false
receiver_state_existed=false
manual_export_state_existed=false
recovery_recipient_existed=false
snapshot_status_existed=false
evidence_state_existed=false
lease_service_active=false
backend_service_active=false
caddy_service_active=false
mp_compose_init
if "${MP_COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx backend; then
    backend_service_active=true
fi
if "${MP_COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx caddy; then
    caddy_service_active=true
fi
if systemctl is-active --quiet mp-opt-ha-lease.service; then
    lease_service_active=true
fi
cleanup() {
    local result="$?" previous_exists="" stage_exists="" rollback_source=""
    local -a restore_services=()
    set +e
    if [ "$result" -ne 0 ] && [ "$database_swap_started" = true ]; then
        mp_compose_init
        "${MP_COMPOSE[@]}" stop backend >/dev/null 2>&1 || true
        previous_exists="$("${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d postgres -Atqc \
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname='${rollback_db}')" 2>/dev/null || true)"
        if [ "$previous_exists" = "t" ]; then
            rollback_source="$rollback_db"
        else
            # A signal can arrive in the tiny interval after the verified old
            # database was renamed to masterplan_previous and before the swap
            # was marked complete. Only trust that name after the staging name
            # has disappeared; otherwise it may be an older retained copy.
            stage_exists="$("${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d postgres -Atqc \
                "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname='${stage_db}')" \
                2>/dev/null || true)"
            if [ "$stage_exists" = "f" ]; then
                previous_exists="$("${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d postgres -Atqc \
                    "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname='masterplan_previous')" \
                    2>/dev/null || true)"
                [ "$previous_exists" != "t" ] || rollback_source="masterplan_previous"
            fi
        fi
        if [ -n "$rollback_source" ]; then
            "${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U masterplan -d postgres \
                --set=rollback_db="$rollback_source" >/dev/null <<'SQL' || true
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
WHERE datname IN ('masterplan', :'rollback_db') AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS masterplan;
SELECT format('ALTER DATABASE %I RENAME TO masterplan', :'rollback_db') \gexec
SQL
        elif [ -n "$stage_db" ]; then
            "${MP_COMPOSE[@]}" exec -T db dropdb -U masterplan --if-exists "$stage_db" >/dev/null 2>&1 || true
        fi
        install -m 0600 "$stage/.env.previous" "$MP_ROOT/.env" || true
        for secret in secret_key ip_hmac_key vapid_private_key root_bootstrap_token smtp_token evidence_signing_key; do
            rm -f "$MP_ROOT/secrets/$secret"
            [ ! -f "$stage/secrets.previous/$secret" ] \
                || install -m 0600 "$stage/secrets.previous/$secret" "$MP_ROOT/secrets/$secret"
        done
        rm -f "$MP_ROOT/secrets/evidence_signing_key.pub"
        if [ -s "$MP_ROOT/secrets/evidence_signing_key" ]; then
            restored_evidence_public="$stage/evidence_signing_key.pub.restored"
            if ssh-keygen -y -f "$MP_ROOT/secrets/evidence_signing_key" \
                    > "$restored_evidence_public" 2>/dev/null; then
                install -m 0600 "$restored_evidence_public" \
                    "$MP_ROOT/secrets/evidence_signing_key.pub"
            fi
            rm -f "$restored_evidence_public"
        fi
        if [ "$lease_service_active" = true ]; then
            # Secret restoration replaces inodes. Refresh the hardened
            # service namespace so its file-level writable bind is current.
            sudo -n systemctl restart mp-opt-ha-lease.service >/dev/null 2>&1 || true
        fi
        if [ "$receiver_state_existed" = true ]; then
            install -m 0600 "$stage/receiver.previous" "$MP_ROOT/runtime/ha-receiver.json" || true
        else
            rm -f "$MP_ROOT/runtime/ha-receiver.json"
        fi
        if [ "$manual_export_state_existed" = true ]; then
            install -m 0600 "$stage/manual-export.previous" "$MP_MANUAL_EXPORT_STATE" || true
        else
            rm -f "$MP_MANUAL_EXPORT_STATE"
        fi
        if [ "$recovery_recipient_existed" = true ]; then
            install -m 0600 "$stage/recovery-recipient.previous" "$MP_RECIPIENT_FILE" || true
        else
            rm -f "$MP_RECIPIENT_FILE"
        fi
        if [ "$snapshot_status_existed" = true ]; then
            install -m 0644 "$stage/snapshot-status.previous" "$MP_HA_SNAPSHOT_STATUS" || true
        else
            rm -f "$MP_HA_SNAPSHOT_STATUS"
        fi
        if [ "$evidence_state_existed" = true ]; then
            sudo -n rm -rf "$MP_ROOT/state/evidence"
            sudo -n mv "$stage/evidence.previous" "$MP_ROOT/state/evidence"
            sudo -n chown -R 10001:10001 "$MP_ROOT/state/evidence"
        else
            sudo -n rm -rf "$MP_ROOT/state/evidence"
        fi
        if [ "$backend_service_active" = true ]; then
            restore_services+=(backend)
        else
            "${MP_COMPOSE[@]}" stop backend >/dev/null 2>&1 || true
        fi
        if [ "$caddy_service_active" = true ]; then
            restore_services+=(caddy)
        else
            "${MP_COMPOSE[@]}" stop caddy >/dev/null 2>&1 || true
        fi
        [ "${#restore_services[@]}" -eq 0 ] \
            || "${MP_COMPOSE[@]}" up -d --no-deps --force-recreate "${restore_services[@]}" >/dev/null 2>&1 \
            || true
    elif [ "$result" -ne 0 ] && [ -n "$stage_db" ]; then
        mp_compose_init
        "${MP_COMPOSE[@]}" exec -T db dropdb -U masterplan --if-exists "$stage_db" >/dev/null 2>&1 || true
    fi
    sudo -n rm -rf "$stage/evidence.new"
    rm -rf "$stage"
    exit "$result"
}
trap cleanup EXIT
if [ -f "$MP_ROOT/runtime/ha-receiver.json" ]; then
    cp -a "$MP_ROOT/runtime/ha-receiver.json" "$stage/receiver.previous"
    receiver_state_existed=true
fi
if [ -f "$MP_MANUAL_EXPORT_STATE" ]; then
    cp -a "$MP_MANUAL_EXPORT_STATE" "$stage/manual-export.previous"
    manual_export_state_existed=true
fi
if [ -f "$MP_RECIPIENT_FILE" ]; then
    cp -a "$MP_RECIPIENT_FILE" "$stage/recovery-recipient.previous"
    recovery_recipient_existed=true
fi
if [ -f "$MP_HA_SNAPSHOT_STATUS" ]; then
    cp -a "$MP_HA_SNAPSHOT_STATUS" "$stage/snapshot-status.previous"
    snapshot_status_existed=true
fi
if [ -d "$MP_ROOT/state/evidence" ] && [ ! -L "$MP_ROOT/state/evidence" ]; then
    sudo -n cp -a "$MP_ROOT/state/evidence" "$stage/evidence.previous"
    sudo -n chown -R "$(id -u):$(id -g)" "$stage/evidence.previous"
    evidence_state_existed=true
fi
archive="$stage/bundle.age"
max_bytes="${HA_MAX_REPLICATION_BUNDLE_BYTES:-2147483648}"
[[ "$max_bytes" =~ ^[1-9][0-9]*$ ]] \
    && [ "$max_bytes" -ge 1048576 ] && [ "$max_bytes" -le 10737418240 ] \
    || { echo "The replication bundle size limit is invalid." >&2; exit 1; }
head -c "$((max_bytes + 1))" > "$archive"
[ "$(stat -c '%s' "$archive")" -le "$max_bytes" ] || exit 1
[ "$(sha256sum "$archive" | awk '{print $1}')" = "$expected_hash" ] || exit 1
identity="$MP_HA_HOME/secrets/replication_age_identity"
[ -f "$identity" ] && [ ! -L "$identity" ] && [ "$(stat -c '%a' "$identity")" = 600 ] || exit 1
age -d -i "$identity" -o "$stage/bundle.tar" "$archive"
python3 "$MP_ROOT/deploy/ha/replication_bundle.py" validate-archive --archive "$stage/bundle.tar"
mkdir "$stage/extracted"
tar -xf "$stage/bundle.tar" -C "$stage/extracted" --no-same-owner --no-same-permissions
release="$(mp_release_hash)"
manifest="$(python3 "$MP_ROOT/deploy/ha/replication_bundle.py" validate \
    --extracted "$stage/extracted" --cluster "$HA_CLUSTER_ID" \
    --source "$HA_PEER_NODE_ID" --target "$HA_NODE_ID" --release "$release")"
[ "$(jq -r '.bundle_id' <<< "$manifest")" = "$bundle_id" ] || exit 1
manifest_generation="$(jq -r '.generation' <<< "$manifest")"
manifest_created_at="$(jq -r '.created_at' <<< "$manifest")"

# A receiver token proves this node asked the external lease authority whether
# the SSH sender is still the current writer.  This closes the race where an
# old primary continues a transfer after ownership has already moved.
python3 "$MP_ROOT/deploy/ha/witness_control.py" authorize-transfer \
    "$HA_PEER_NODE_ID" "$bundle_id" "$expected_hash" "$manifest_generation" >/dev/null

if [ -f "$MP_ROOT/runtime/ha-receiver.json" ]; then
    previous_generation="$(jq -r '.generation // 0' "$MP_ROOT/runtime/ha-receiver.json")"
    previous_created_at="$(jq -r '.bundle_created_at // empty' "$MP_ROOT/runtime/ha-receiver.json")"
    [ "$manifest_generation" -ge "$previous_generation" ] || { echo "Replication generation is older than the accepted copy." >&2; exit 1; }
    if [ "$manifest_generation" -eq "$previous_generation" ] && [ -n "$previous_created_at" ]; then
        [[ "$manifest_created_at" > "$previous_created_at" ]] \
            || { echo "Replication copy is not newer than the accepted copy." >&2; exit 1; }
    fi
fi

mp_compose_init
"${MP_COMPOSE[@]}" up -d db >/dev/null
mp_wait_for_database 30 || { echo "The replication peer database did not become ready." >&2; exit 1; }
stage_db="mp_stage_${bundle_id//[^A-Za-z0-9]/}"
stage_db="${stage_db:0:48}"
rollback_db="mp_rollback_${bundle_id//[^A-Za-z0-9]/}"
rollback_db="${rollback_db:0:48}"
"${MP_COMPOSE[@]}" exec -T db dropdb -U masterplan --if-exists "$stage_db"
"${MP_COMPOSE[@]}" exec -T db createdb -U masterplan -T template0 "$stage_db"
"${MP_COMPOSE[@]}" exec -T db pg_restore -U masterplan -d "$stage_db" \
    --no-owner --no-acl < "$stage/extracted/payload/database/masterplan.dump"
db_identity="$("${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d "$stage_db" -Atqc \
    "SELECT cluster_id || ':' || generation::text || ':' || active_node_id FROM ha_cluster_state WHERE id=1")"
[ "$db_identity" = "${HA_CLUSTER_ID}:${manifest_generation}:${HA_PEER_NODE_ID}" ] \
    || { echo "The database writer identity does not match the authorized bundle." >&2; exit 1; }
"${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d "$stage_db" -Atqc \
    "SELECT 1 FROM users LIMIT 1" >/dev/null

# A privacy confirmation is valid only when this exact staged database has the
# asserted applied action and signed live-purge receipt. The sender cannot turn
# an unrelated successful copy into deletion evidence.
privacy_workflow="$(jq -r '.privacy_assertion.workflow_type // empty' <<< "$manifest")"
if [ -n "$privacy_workflow" ]; then
    privacy_workflow_id="$(jq -r '.privacy_assertion.workflow_id' <<< "$manifest")"
    privacy_action_id="$(jq -r '.privacy_assertion.privacy_action_id' <<< "$manifest")"
    privacy_sequence="$(jq -r '.privacy_assertion.privacy_action_sequence' <<< "$manifest")"
    privacy_purge_digest="$(jq -r '.privacy_assertion.live_purge_receipt_sha256' <<< "$manifest")"
    [ "$privacy_workflow" = "deletion_case" ] \
        || { echo "The privacy assertion workflow is not current." >&2; exit 1; }
    privacy_verified="$("${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d "$stage_db" -At \
        --set=workflow_id="$privacy_workflow_id" --set=action_id="$privacy_action_id" \
        --set=action_sequence="$privacy_sequence" --set=purge_digest="$privacy_purge_digest" \
        -c "SELECT EXISTS (SELECT 1 FROM deletion_cases c JOIN privacy_action_receipts p ON p.privacy_action_id=c.privacy_action_id WHERE c.request_id=:'workflow_id' AND c.privacy_action_id=:'action_id' AND c.privacy_action_sequence=:'action_sequence'::integer AND c.live_purge_receipt_sha256=:'purge_digest' AND c.live_data_purged_at IS NOT NULL AND p.sequence=:'action_sequence'::integer AND p.local_applied_at IS NOT NULL)")"
    [ "$privacy_verified" = "t" ] \
        || { echo "The staged database does not prove the requested privacy action." >&2; exit 1; }
fi

python3 "$MP_ROOT/deploy/ha/replication_bundle.py" merge-env \
    --local "$MP_ROOT/.env" --shared "$stage/extracted/payload/config/shared.env" \
    --output "$stage/.env"
cp -a "$MP_ROOT/.env" "$stage/.env.previous"
mkdir "$stage/secrets.previous"
cp -a "$MP_ROOT/secrets/." "$stage/secrets.previous/"

"${MP_COMPOSE[@]}" stop backend >/dev/null 2>&1 || true
database_swap_started=true
"${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U masterplan -d postgres \
    --set=stage_db="$stage_db" --set=rollback_db="$rollback_db" >/dev/null <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
WHERE datname IN ('masterplan', 'masterplan_previous', :'rollback_db') AND pid <> pg_backend_pid();
SELECT format('DROP DATABASE IF EXISTS %I', :'rollback_db') \gexec
DROP DATABASE IF EXISTS masterplan_previous;
SELECT format('ALTER DATABASE masterplan RENAME TO %I', :'rollback_db') \gexec
SELECT format('ALTER DATABASE %I RENAME TO masterplan', :'stage_db') \gexec
SQL

install -m 0600 "$stage/.env" "$MP_ROOT/.env"
for secret in secret_key ip_hmac_key vapid_private_key root_bootstrap_token smtp_token evidence_signing_key; do
    source_file="$stage/extracted/payload/config/secrets/$secret"
    [ ! -f "$source_file" ] || install -m 0600 "$source_file" "$MP_ROOT/secrets/$secret"
done
if [ -s "$MP_ROOT/secrets/evidence_signing_key" ]; then
    ssh-keygen -y -f "$MP_ROOT/secrets/evidence_signing_key" \
        > "$MP_ROOT/secrets/evidence_signing_key.pub"
    chmod 600 "$MP_ROOT/secrets/evidence_signing_key.pub"
fi
mkdir -p "$(dirname "$MP_MANUAL_EXPORT_STATE")"
chmod 700 "$(dirname "$MP_MANUAL_EXPORT_STATE")"
install -m 0600 \
    "$stage/extracted/payload/recovery/manual-recovery-export.json" \
    "$MP_MANUAL_EXPORT_STATE"
mkdir -p "$(dirname "$MP_RECIPIENT_FILE")"
chmod 700 "$(dirname "$MP_RECIPIENT_FILE")"
install -m 0600 \
    "$stage/extracted/payload/recovery/recovery-recipient" \
    "$MP_RECIPIENT_FILE"
# Replace the signed ledger together with the accepted database. The backend
# remains stopped, and the source bundle validator has hash-checked every
# regular file and rejected links/special files.
sudo -n rm -rf "$stage/evidence.new"
sudo -n cp -a "$stage/extracted/payload/evidence" "$stage/evidence.new"
sudo -n chown -R 10001:10001 "$stage/evidence.new"
sudo -n chmod 700 "$stage/evidence.new"
[ ! -e "$MP_ROOT/state" ] || { [ -d "$MP_ROOT/state" ] && [ ! -L "$MP_ROOT/state" ]; } \
    || { echo "The evidence parent directory is unsafe." >&2; exit 1; }
sudo -n install -d -o root -g root -m 0755 "$MP_ROOT/state"
sudo -n rm -rf "$MP_ROOT/state/evidence"
sudo -n mv "$stage/evidence.new" "$MP_ROOT/state/evidence"
mp_snapshot_publish_status
mp_prepare_backend_secret_permissions
"${MP_COMPOSE[@]}" up -d --no-deps --force-recreate backend caddy >/dev/null
"${MP_COMPOSE[@]}" exec -T db pg_isready -U masterplan -d masterplan >/dev/null
"${MP_COMPOSE[@]}" exec -T caddy \
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
for _ in $(seq 1 30); do
    "${MP_COMPOSE[@]}" exec -T backend python -c \
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3).read()' \
        >/dev/null 2>&1 && break
    sleep 2
done
"${MP_COMPOSE[@]}" exec -T backend python -c \
    'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3).read()' \
    >/dev/null
python3 "$MP_ROOT/deploy/ha/smtp_probe.py" --root "$MP_ROOT" --node-id "$HA_NODE_ID" \
    --output "$MP_ROOT/runtime/ha-smtp-status.json" >/dev/null 2>&1 || true

received_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
jq -n --arg bundle "$bundle_id" --arg hash "$expected_hash" --arg received "$received_at" \
    --arg created "$manifest_created_at" --arg source "$HA_PEER_NODE_ID" --argjson generation "$manifest_generation" \
    --argjson privacy "$(jq '.privacy_assertion // null' <<< "$manifest")" \
    '{format:"mp-opt-receiver-state-v1",last_bundle_id:$bundle,last_bundle_sha256:$hash,last_received_at:$received,bundle_created_at:$created,source_node_id:$source,generation:$generation,privacy_assertion:$privacy}' \
    > "$stage/receiver.json"
install -m 0600 "$stage/receiver.json" "$MP_ROOT/runtime/ha-receiver.json"
# The old database is intentionally retained until the next successful copy;
# it is the immediate local rollback point for operator recovery.
"${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U masterplan -d postgres \
    --set=rollback_db="$rollback_db" >/dev/null <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
WHERE datname IN ('masterplan_previous', :'rollback_db') AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS masterplan_previous;
SELECT format('ALTER DATABASE %I RENAME TO masterplan_previous', :'rollback_db') \gexec
SQL
# The hardened lease service has a file-level ReadWritePaths bind for the root
# bootstrap token. Atomic secret replacement changes that inode, so refresh its
# mount namespace before acknowledging the copy. The transfer guard prevents a
# promotion from racing this receiver operation.
if [ "$lease_service_active" = true ]; then
    sudo -n systemctl restart mp-opt-ha-lease.service
fi
database_swap_started=false
# Clear the witness transfer guard. Failure is harmless: it expires after the
# failover delay and the fully applied, hash-verified copy remains valid.
python3 "$MP_ROOT/deploy/ha/witness_control.py" complete-transfer \
    "$bundle_id" "$expected_hash" >/dev/null 2>&1 || true
printf 'ACCEPTED:%s:%s\n' "$bundle_id" "$expected_hash"
