#!/usr/bin/env bash
# Activate a previously accepted point-in-time copy for a witness generation.
set -Eeuo pipefail
umask 077

MP_ROOT="${MP_ROOT:-/opt/masterplan}"
MP_HA_HOME="${MP_HA_HOME:-/etc/mp-opt-ha}"
generation="${1:?generation required}"
force_revoke="${2:-}"
[[ "$generation" =~ ^[1-9][0-9]*$ ]] || exit 1
[ -z "$force_revoke" ] || [ "$force_revoke" = "--force-revoke" ] || exit 1

# shellcheck source=../management/common.sh
source "$MP_ROOT/deploy/management/common.sh"
mp_load_ha_config
[ "$HA_MODE" = "ha" ] || exit 1
mkdir -p "$MP_STATE"
chmod 700 "$MP_STATE"
if [ "${MP_MANAGEMENT_LOCK_HELD:-0}" = "1" ]; then
    [ "$(readlink -f /proc/$$/fd/9 2>/dev/null || true)" = "$(readlink -f "$MP_LOCK_FILE")" ] \
        || { echo "The inherited management lock could not be verified." >&2; exit 1; }
else
    exec 7>"$MP_LOCK_FILE"
    flock -w 240 7 || { echo "A local management operation blocked promotion." >&2; exit 75; }
fi
control_generation="$(jq -r '.generation // 0' "$MP_ROOT/runtime/ha-control.json")"
holder="$(jq -r '.holder_node_id // empty' "$MP_ROOT/runtime/ha-control.json")"
[ "$control_generation" = "$generation" ] && [ "$holder" = "$HA_NODE_ID" ] || exit 1

mp_compose_init
"${MP_COMPOSE[@]}" up -d db >/dev/null
current_generation="$("${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U masterplan -d masterplan -Atqc \
    "SELECT generation::text || ':' || active_node_id FROM ha_cluster_state WHERE id = 1" 2>/dev/null || true)"

# Cloudflare routing can fail after the database promotion.  The lease agent
# retries that final step, so promotion must be safe to repeat without revoking
# freshly-created sessions or rotating publish credentials a second time.
if [ "$current_generation" != "${generation}:${HA_NODE_ID}" ] || [ "$force_revoke" = "--force-revoke" ]; then
    "${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U masterplan -d masterplan \
        --set=cluster_id="$HA_CLUSTER_ID" --set=generation="$generation" --set=node_id="$HA_NODE_ID" <<'SQL'
BEGIN;
INSERT INTO ha_cluster_state (id, cluster_id, generation, active_node_id, maintenance)
VALUES (1, :'cluster_id', :'generation', :'node_id', FALSE)
ON CONFLICT (id) DO UPDATE SET
    cluster_id = EXCLUDED.cluster_id,
    generation = EXCLUDED.generation,
    active_node_id = EXCLUDED.active_node_id,
    maintenance = FALSE,
    updated_at = CURRENT_TIMESTAMP;

UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE revoked_at IS NULL;
DELETE FROM exchange_codes;
DELETE FROM passkey_ceremonies;
DELETE FROM passkey_challenges;
UPDATE activation_links
SET invalidated_at = CURRENT_TIMESTAMP, delivery_pending = FALSE
WHERE invalidated_at IS NULL;
COMMIT;
SQL
fi

# A registered root passkey makes the bootstrap bearer unnecessary. Ensure a
# replicated or restored bootstrap code cannot survive an ownership change.
registered_root="$("${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 \
    -U masterplan -d masterplan -Atqc \
    'SELECT EXISTS (SELECT 1 FROM users u JOIN webauthn_credentials c ON c.user_id=u.id WHERE u.is_root_admin)' \
    2>/dev/null || true)"
if [ "$registered_root" = "t" ]; then
    : > "$MP_ROOT/secrets/root_bootstrap_token"
    chmod 600 "$MP_ROOT/secrets/root_bootstrap_token"
fi

"${MP_COMPOSE[@]}" up -d --no-deps --force-recreate backend >/dev/null
for _ in $(seq 1 30); do
    if "${MP_COMPOSE[@]}" exec -T backend python -c \
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3).read()' \
        >/dev/null 2>&1; then
        break
    fi
    sleep 2
done
"${MP_COMPOSE[@]}" exec -T backend python -c \
    'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3).read()' \
    >/dev/null
python3 "$MP_ROOT/deploy/ha/smtp_probe.py" --root "$MP_ROOT" --node-id "$HA_NODE_ID" \
    --output "$MP_ROOT/runtime/ha-smtp-status.json" >/dev/null 2>&1 || true
received_at="$(jq -r '.last_received_at // empty' "$MP_ROOT/runtime/ha-receiver.json" 2>/dev/null || true)"
jq -n --arg node "$HA_NODE_ID" --arg peer "$HA_PEER_NODE_ID" \
    --arg holder "$HA_NODE_ID" --arg received "$received_at" \
    --argjson generation "$generation" \
    '{mode:"ha",node_id:$node,peer_node_id:$peer,holder_node_id:$holder,generation:$generation,state:"degraded",job_state:"idle",last_received_at:($received | if length > 0 then . else null end),potential_data_loss_seconds:null,peer_reachable:null,peer_compatible:null,message:"This node has become primary; a new outbound peer copy has not completed yet."}' \
    > "$MP_ROOT/runtime/ha-replication.json.tmp"
chmod 644 "$MP_ROOT/runtime/ha-replication.json.tmp"
mv "$MP_ROOT/runtime/ha-replication.json.tmp" "$MP_ROOT/runtime/ha-replication.json"
