#!/usr/bin/env bash
# Create bounded encrypted HA recovery points from the active node only.
set -Eeuo pipefail
umask 077

MP_ROOT="${MP_ROOT:-/opt/masterplan}"
export MP_ROOT MP_TUI=ansi MP_SNAPSHOT_SERVICE_MODE=1

# shellcheck source=../management/common.sh
source "$MP_ROOT/deploy/management/common.sh"
# shellcheck source=../management/snapshots.sh
source "$MP_ROOT/deploy/management/snapshots.sh"

# A persistent timer catch-up may begin while commissioning is still
# finishing. Share the setup lease before touching state so validation and
# snapshots can never stop or recreate the same services concurrently.
mp_setup_worker_lease_acquire "${MP_SETUP_WORKER_LEASE_WAIT_SECONDS:-300}" || {
    status=$?
    if [ "$status" -eq 75 ]; then
        printf 'Automatic snapshot deferred: commissioning still owns the setup lease.\n'
        exit 0
    fi
    printf 'Automatic snapshot stopped: the setup lease is unsafe.\n' >&2
    exit "$status"
}
trap 'mp_setup_worker_lease_release' EXIT

mp_initialise_paths
mp_load_ha_config
[ "$HA_MODE" = "ha" ] || exit 0
[ "$(jq -r '.holder_node_id // empty' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null)" = "$HA_NODE_ID" ] || exit 0
mp_snapshot_compose_init || {
    printf 'Automatic snapshot stopped: the installed runtime permission contract is unsafe.\n' >&2
    exit 1
}

# A scheduled snapshot may overlap a deliberate operator workflow such as a
# portable export or recovery-key rotation.  That contention is expected: do
# not invoke the interactive lock error path from this non-TTY service, and do
# not leave systemd reporting a failed unit.  The next timer run will retry.
exec 9>"$MP_LOCK_FILE"
if ! flock -n 9; then
    printf 'Automatic snapshot skipped: another management operation holds the lock.\n'
    exec 9>&-
    exit 0
fi
export MP_MANAGEMENT_LOCK_HELD=1
trap 'mp_unlock; mp_setup_worker_lease_release' EXIT

create_snapshot() {
    local type="$1" name="$2" path
    path="$(mp_snapshot_create "$type" "$name")" || return 1
    if [ "${HA_RECOVERY_STORAGE_MODE:-manual_portable}" != ssh_archive ]; then
        mp_audit "ha.snapshot-copy" "skipped" "$type:$name:manual-portable"
        return 0
    fi
    mp_snapshot_copy_off_server "$path" || {
        mp_audit "ha.snapshot-copy" "failed" "$type:$name"
        python3 "$MP_ROOT/deploy/ha/send_alert.py" \
            "Off-server snapshot copy failed" \
            "The encrypted ${type} snapshot ${name} remains local on ${HA_NODE_ID}, but its off-server SHA-256 verification failed." \
            || true
        return 0
    }
    mp_audit "ha.snapshot-copy" "success" "$type:$name"
}

prune_series() {
    local pattern="$1" retain="$2" index=0 path
    while IFS= read -r path; do
        index=$((index + 1))
        [ "$index" -le "$retain" ] || rm -rf -- "$path"
    done < <(find "$MP_SNAPSHOTS" -mindepth 1 -maxdepth 1 -type d -name "$pattern" -print | sort -r)
}

hour="$(date -u +%H)"
weekday="$(date -u +%u)"
create_snapshot database "ha-auto-hourly-$(date -u +%Y%m%dT%H)"
prune_series '*_database_ha-auto-hourly-*' 24

if [ "$hour" = "02" ]; then
    create_snapshot full "ha-auto-daily-$(date -u +%Y%m%d)"
    prune_series '*_full_ha-auto-daily-*' 14
fi

if [ "$weekday" = "7" ] && [ "$hour" = "03" ]; then
    create_snapshot full "ha-auto-weekly-$(date -u +%G-W%V)"
    prune_series '*_full_ha-auto-weekly-*' 8
fi

# Refresh counts after retention pruning as well as after creation.
mp_snapshot_publish_status || true
