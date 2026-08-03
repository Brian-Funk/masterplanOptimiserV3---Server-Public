#!/usr/bin/env bash
# Install or remove node-local systemd services according to the configured role.
set -Eeuo pipefail

install_only=false
if [ "${1:-}" = "--install-only" ]; then
    install_only=true
elif [ "$#" -gt 0 ]; then
    echo "Usage: $0 [--install-only]" >&2
    exit 2
fi

MP_ROOT="${MP_ROOT:-/opt/masterplan}"
export MP_ROOT
# shellcheck source=../management/common.sh
source "$MP_ROOT/deploy/management/common.sh"
mp_load_ha_config
install -d -m 0711 "$MP_ROOT/runtime"
install -d -m 0700 "$MP_ROOT/runtime/ha-requests" \
    "$MP_ROOT/runtime/ha-deferred-requests" "$MP_ROOT/runtime/ha-jobs"
install -d -m 0700 "$HOME/.config" "$HOME/.config/mp-opt-server" \
    "$HOME/masterplan-snapshots"
install -d -m 0700 "$HOME/.local/state/mp-opt-ha-replication" \
    "$HOME/.local/state/mp-opt-ha-replication/incoming" \
    "$HOME/.local/state/mp-opt-ha-replication/outgoing" \
    "$HOME/.local/state/mp-opt-server"
touch "$HOME/.local/state/mp-opt-server/management.lock"
chmod 600 "$HOME/.local/state/mp-opt-server/management.lock"

units=(
    mp-opt-ha-lease.service
    mp-opt-ha-replication.service
    mp-opt-ha-replication.path
    mp-opt-ha-replication.timer
    mp-opt-ha-snapshots.service
    mp-opt-ha-snapshots.timer
)

if [ "$HA_MODE" != "ha" ]; then
    for unit in "${units[@]}"; do
        sudo -n systemctl disable --now "$unit" >/dev/null 2>&1 || true
    done
    sudo -n systemctl disable --now mp-opt-ha-control.service >/dev/null 2>&1 || true
    exit 0
fi

for unit in "${units[@]}"; do
    sudo -n install -o root -g root -m 0644 \
        "$MP_ROOT/deploy/ha/$unit" "/etc/systemd/system/$unit"
done
sudo -n systemctl daemon-reload
sudo -n systemctl disable --now mp-opt-ha-control.service >/dev/null 2>&1 || true
[ "$install_only" = false ] || exit 0
sudo -n systemctl enable --now mp-opt-ha-lease.service
sudo -n systemctl enable --now mp-opt-ha-replication.timer
sudo -n systemctl enable --now mp-opt-ha-replication.path
sudo -n systemctl enable --now mp-opt-ha-snapshots.timer
