#!/usr/bin/env bash
# Install one immutable signed release on a standalone server or both HA nodes.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MP_ROOT="${MP_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
export MP_ROOT

ui_error() { printf 'ERROR: %s\n' "$*" >&2; }
ui_message() { printf '%s\n' "$*"; }
ui_confirm() { return 1; }

# shellcheck source=management/common.sh
source "$MP_ROOT/deploy/management/common.sh"
# shellcheck source=management/ha.sh
source "$MP_ROOT/deploy/management/ha.sh"

usage() {
    printf 'Usage: deploy/signed-deployment.sh vMAJOR.MINOR.PATCH|--rollback\n' >&2
}

tag="${1:-}"
[ "$#" -eq 1 ] || { usage; exit 2; }
rollback=false
if [ "$tag" = --rollback ]; then
    rollback=true
else
    python3 "$MP_ROOT/deploy/test_deployment.py" validate tag "$tag" >/dev/null
fi
[ "$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || printf production)" = production ] || {
    ui_error "Signed production deployment requires production policy. Restore the signed baseline through the TUI first."
    exit 1
}
[ ! -f "$MP_ROOT/.test-deployment.env" ] || {
    ui_error "Unsigned image overrides are still active. Restore the signed baseline before deploying a release."
    exit 1
}

mp_load_ha_config
role="$HA_ROLE"
automatic=false
if [ "$role" = dynamic ] && [ "${MP_SIGNED_PEER:-0}" != 1 ]; then
    if [ "$(jq -r '.holder_node_id // empty' "$MP_ROOT/runtime/ha-control.json")" != "$HA_NODE_ID" ]; then
        exec ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
            env MP_ROOT=/opt/masterplan /opt/masterplan/deploy/signed-deployment.sh "$tag"
    fi
    if [ "$(jq -r '.automatic_failover // false' "$MP_ROOT/runtime/ha-control.json")" = true ]; then
        automatic=true
        python3 "$MP_ROOT/deploy/ha/witness_control.py" automatic disabled >/dev/null
        mp_ha_set_config_value HA_AUTOMATIC_FAILOVER disabled
    fi
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 mp-opt-ha-peer \
        env MP_ROOT=/opt/masterplan MP_SIGNED_PEER=1 \
        /opt/masterplan/deploy/signed-deployment.sh "$tag" || {
            ui_error "Peer release deployment failed. Automatic failover remains disabled."
            exit 1
        }
fi

mp_lock
trap 'mp_unlock' EXIT
if [ "$rollback" = true ]; then
    python3 "$MP_ROOT/deploy/release/install_release.py" --repo-root "$MP_ROOT" --rollback
elif [ -f "$MP_ROOT/.release.env" ]; then
    python3 "$MP_ROOT/deploy/release/install_release.py" \
        --repo-root "$MP_ROOT" --tag "$tag" --blue-green
    MP_RELEASE_LOCK_HELD=1 \
        "$MP_ROOT/deploy/release/blue_green_upgrade.sh"
else
    python3 "$MP_ROOT/deploy/release/install_release.py" --repo-root "$MP_ROOT" --tag "$tag"
    "$MP_ROOT/deploy/deploy.sh" --no-pull
fi
mp_unlock
trap - EXIT

if [ "$role" = dynamic ] && [ "${MP_SIGNED_PEER:-0}" != 1 ]; then
    mp_ha_replicate_now || { ui_error "Release installed, but peer replication failed. Automatic failover remains disabled."; exit 1; }
    mp_ha_refresh_witness_observations || { ui_error "Release installed, but witness refresh failed. Automatic failover remains disabled."; exit 1; }
    mp_ha_active_verification_readiness || { ui_error "Release installed, but HA readiness did not converge. Automatic failover remains disabled."; exit 1; }
    if [ "$automatic" = true ]; then
        python3 "$MP_ROOT/deploy/ha/witness_control.py" automatic enabled >/dev/null
        mp_ha_set_config_value HA_AUTOMATIC_FAILOVER enabled
    fi
fi

if [ "$rollback" = true ]; then
    mp_audit "deploy.signed-rollback" "success" "$(sed -n 's/^MP_RELEASE_TAG=//p' "$MP_ROOT/.release.env" | head -1)"
else
    mp_audit "deploy.signed" "success" "$tag"
fi
printf 'SIGNED RELEASE DEPLOYED\nTag: %s\nCommit: %s\n' \
    "$(sed -n 's/^MP_RELEASE_TAG=//p' "$MP_ROOT/.release.env" | head -1)" \
    "$(sed -n 's/^MP_RELEASE_COMMIT=//p' "$MP_ROOT/.release.env" | head -1)"
