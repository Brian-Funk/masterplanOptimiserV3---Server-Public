#!/usr/bin/env bash

# Guarded, multi-generation snapshot recovery-key rotation.

mp_rotation_cleanup_transients() {
    [ -z "${MP_ROTATION_WORK:-}" ] || rm -rf "$MP_ROTATION_WORK"
    mp_remove_identity_file "${MP_ROTATION_NEW_IDENTITY:-}"
    mp_remove_identity_file "${MP_ROTATION_OLD_IDENTITY:-}"
}

mp_rotation_journal_event() {
    local journal="$1" event="$2" scope="${3:-none}" detail="${4:-none}"
    jq -cn --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg event "$event" \
        --arg scope "$scope" --arg detail "$detail" \
        '{format:"mp-opt-recovery-rotation-event-v1",at:$at,event:$event,scope:$scope,detail:$detail}' \
        >> "$journal" || return 1
    chmod 600 "$journal"
}

mp_rotation_state_write() {
    local state="$1" job_id="$2" phase="$3" old_recipient="$4" new_recipient="$5" baseline="${6:-}"
    local temporary rotation_kind storage_mode
    rotation_kind="${MP_ROTATION_KIND:-$(jq -r '.rotation_kind // "planned"' "$state" 2>/dev/null || printf planned)}"
    storage_mode="${MP_ROTATION_STORAGE_MODE:-$(jq -r '.recovery_storage_mode // "ssh_archive"' "$state" 2>/dev/null || printf ssh_archive)}"
    case "$rotation_kind" in planned|emergency) ;; *) return 1 ;; esac
    case "$storage_mode" in manual_portable|ssh_archive) ;; *) return 1 ;; esac
    temporary="$(mktemp "${state}.XXXXXX")" || return 1
    jq -n --arg job_id "$job_id" --arg phase "$phase" --arg old "$old_recipient" \
        --arg new "$new_recipient" --arg baseline "$baseline" --arg rotation_kind "$rotation_kind" \
        --arg recovery_storage_mode "$storage_mode" --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
        {format:"mp-opt-recovery-rotation-state-v1",job_id:$job_id,phase:$phase,
         rotation_kind:$rotation_kind,recovery_storage_mode:$recovery_storage_mode,
         old_recipient:$old,new_recipient:$new,baseline:$baseline,updated_at:$updated_at}
    ' > "$temporary" || { rm -f "$temporary"; return 1; }
    chmod 600 "$temporary" && mv "$temporary" "$state"
}

# Complete one manual-mode rotation only after the exact deeply verified
# baseline has an operator-confirmed workstation package receipt.
mp_rotation_finalize_state() {
    local state="$1" selected="$2" package_hash="$3"
    local phase job baseline new_recipient rotation_kind storage_mode registry journal row scope host parent base
    mp_require_ha_maintenance_window || return 1
    phase="$(jq -r '.phase // empty' "$state")" || return 1
    [ "$phase" = awaiting-portable-export ] || return 1
    job="$(jq -r '.job_id' "$state")"
    baseline="$(jq -r '.baseline' "$state")"
    new_recipient="$(jq -r '.new_recipient' "$state")"
    rotation_kind="$(jq -r '.rotation_kind' "$state")"
    storage_mode="$(jq -r '.recovery_storage_mode' "$state")"
    [[ "$job" =~ ^[0-9a-f-]{36}$ ]] && [ "$storage_mode" = manual_portable ] \
        && [ "$(basename "$selected")" = "$baseline" ] && [[ "$package_hash" =~ ^[0-9a-f]{64}$ ]] || return 1
    [ -d "$selected" ] && [ ! -L "$selected" ] \
        && [ "$(readlink -f "$selected")" = "$(readlink -f "$MP_SNAPSHOTS/$baseline")" ] || return 1
    [ "$(mp_recovery_recipient)" = "$new_recipient" ] || return 1
    mp_load_ha_config || return 1
    if [ "$HA_ROLE" = dynamic ]; then
        [ "$(mp_ha_peer_recovery_recipient read 2>/dev/null || true)" = "$new_recipient" ] || return 1
    fi
    jq -e --arg package_hash "$package_hash" --arg recipient "$new_recipient" '
        .format == "mp-opt-snapshot-receipt-v2"
        and .verification == "deep-verified"
        and .encryption.recipient == $recipient
        and .storage.portable.state == "operator-sha256-confirmed"
        and .storage.portable.package_sha256 == $package_hash
    ' "$selected/receipt.json" >/dev/null || return 1
    jq -e --arg baseline "$baseline" --arg package_hash "$package_hash" '
        .format == "mp-opt-manual-recovery-export-v1"
        and .state == "operator-sha256-confirmed"
        and .snapshot == $baseline
        and .package_sha256 == $package_hash
    ' "$MP_MANUAL_EXPORT_STATE" >/dev/null || return 1

    registry="${state%.state.json}.copies.tsv"
    journal="${state%.state.json}.jsonl"
    mp_lock || return 1
    if [ "$rotation_kind" = planned ] && [ -f "$registry" ]; then
        while IFS=$'\t' read -r scope host parent base; do
            if ! mp_rotation_remove_backup "$scope" "$host" "$parent" "$base" "$job"; then
                mp_rotation_journal_event "$journal" recovery-required cleanup "$scope:$base" || true
                mp_unlock
                return 1
            fi
        done < "$registry"
    fi
    MP_ROTATION_KIND="$rotation_kind" MP_ROTATION_STORAGE_MODE="$storage_mode" \
        mp_rotation_state_write "$state" "$job" complete \
        "$(jq -r '.old_recipient' "$state")" "$new_recipient" "$baseline" || {
            mp_unlock
            return 1
        }
    mp_rotation_journal_event "$journal" completed "$rotation_kind" \
        "$(mp_recovery_key_id "$new_recipient"):portable:${package_hash}" || {
            mp_unlock
            return 1
        }
    mp_audit "recovery-key.rotate" "success" \
        "${rotation_kind}:manual-portable:$(mp_recovery_key_id "$new_recipient")"
    mp_unlock
}

# Called by portable export. Unrelated exports succeed without changing any
# rotation; an exact pending baseline is finalized transactionally.
mp_rotation_finalize_portable_export() {
    local selected="$1" package_hash="$2" state matched=0
    [ -d "$MP_STATE/recovery-rotations" ] || return 0
    while IFS= read -r state; do
        jq -e --arg baseline "$(basename "$selected")" '
            .format == "mp-opt-recovery-rotation-state-v1"
            and .phase == "awaiting-portable-export"
            and .recovery_storage_mode == "manual_portable"
            and .baseline == $baseline
        ' "$state" >/dev/null 2>&1 || continue
        matched=$((matched + 1))
        [ "$matched" -eq 1 ] || return 1
        mp_rotation_finalize_state "$state" "$selected" "$package_hash" || return 1
    done < <(find "$MP_STATE/recovery-rotations" -maxdepth 1 -type f -name '*.state.json' | sort)
}

mp_rotation_resume_pending() {
    local state baseline package_hash pending=0
    [ -d "$MP_STATE/recovery-rotations" ] || {
        ui_message "Recovery-key rotation" "No pending manual rotation exists."
        return 0
    }
    while IFS= read -r state; do
        [ "$(jq -r '.phase // empty' "$state" 2>/dev/null)" = awaiting-portable-export ] || continue
        pending=$((pending + 1))
        [ "$pending" -eq 1 ] || { ui_error "More than one pending rotation was found; manual inspection is required."; return 1; }
        baseline="$(jq -r '.baseline' "$state")"
        if ! jq -e --arg baseline "$baseline" '
            .format == "mp-opt-manual-recovery-export-v1"
            and .state == "operator-sha256-confirmed"
            and .snapshot == $baseline
        ' "$MP_MANUAL_EXPORT_STATE" >/dev/null 2>&1; then
            ui_error "Rotation is waiting for ${baseline}. Open Snapshots and recovery → Export one portable snapshot, select that exact baseline, transfer it to the workstation, and confirm the matching SHA-256."
            return 1
        fi
        package_hash="$(jq -r '.package_sha256' "$MP_MANUAL_EXPORT_STATE")"
        if mp_rotation_finalize_state "$state" "$MP_SNAPSHOTS/$baseline" "$package_hash"; then
            ui_message "Recovery key rotation complete" \
                "The exact deeply verified baseline has a confirmed workstation copy. Protected old working copies were retired safely."
            return 0
        fi
        ui_error "The pending rotation evidence did not match the current recipients or baseline receipt. Nothing was cleaned up."
        return 1
    done < <(find "$MP_STATE/recovery-rotations" -maxdepth 1 -type f -name '*.state.json' | sort)
    ui_message "Recovery-key rotation" "No pending manual rotation exists."
}

mp_rotation_remove_baseline_copies() {
    local baseline="$1"
    [ -n "$baseline" ] || return 0
    [[ "$baseline" =~ ^[0-9]{8}T[0-9]{6}Z_full_post-(emergency-)?key-rotation-[0-9a-f]{12}$ ]] || return 1
    rm -rf "$MP_SNAPSHOTS/$baseline"
    if [ "${MP_ROTATION_STORAGE_MODE:-${HA_RECOVERY_STORAGE_MODE:-manual_portable}}" = ssh_archive ] \
        && [ -n "${HA_ARCHIVE_SSH_TARGET:-}" ]; then
        ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ClearAllForwardings=yes "$HA_ARCHIVE_SSH_TARGET" \
            "rm -rf 'masterplan-ha-archives/${HA_CLUSTER_ID:-standalone}/${HA_NODE_ID:-standalone}/$baseline'"
    fi
}

# Reconcile a process/host interruption before allowing another rotation. A
# verified baseline is finished forward; every earlier phase is rolled back.
mp_rotation_reconcile_incomplete() {
    local state phase job old_recipient new_recipient baseline registry row scope host parent base role
    [ -d "$MP_STATE/recovery-rotations" ] || return 0
    mp_load_ha_config || return 1
    role="$HA_ROLE"
    while IFS= read -r state; do
        phase="$(jq -r '.phase // "invalid"' "$state" 2>/dev/null)" || return 1
        case "$phase" in complete|rolled-back) continue ;; esac
        jq -e '.format == "mp-opt-recovery-rotation-state-v1"' "$state" >/dev/null || return 1
        job="$(jq -r '.job_id' "$state")"
        old_recipient="$(jq -r '.old_recipient' "$state")"
        new_recipient="$(jq -r '.new_recipient' "$state")"
        baseline="$(jq -r '.baseline // empty' "$state")"
        [[ "$job" =~ ^[0-9a-f-]{36}$ ]] \
            && [[ "$old_recipient" =~ ^age1[0-9a-z]+$ ]] \
            && [[ "$new_recipient" =~ ^age1[0-9a-z]+$ ]] || return 1
        if [ -z "$baseline" ]; then
            baseline="$(find "$MP_SNAPSHOTS" -mindepth 1 -maxdepth 1 -type d \
                \( -name "*_full_post-key-rotation-${job:0:12}" \
                   -o -name "*_full_post-emergency-key-rotation-${job:0:12}" \) \
                -printf '%f\n' | head -n 1)"
        fi
        registry="${state%.state.json}.copies.tsv"
        if [ "$phase" = baseline-verified ] \
            && [ "$(jq -r '.recovery_storage_mode // "ssh_archive"' "$state")" = manual_portable ]; then
            MP_ROTATION_KIND="$(jq -r '.rotation_kind // "planned"' "$state")" \
            MP_ROTATION_STORAGE_MODE=manual_portable \
                mp_rotation_state_write "$state" "$job" awaiting-portable-export \
                "$old_recipient" "$new_recipient" "$baseline" || return 1
            phase=awaiting-portable-export
        fi
        if [ "$phase" = awaiting-portable-export ]; then
            if jq -e --arg baseline "$baseline" '
                .format == "mp-opt-manual-recovery-export-v1"
                and .state == "operator-sha256-confirmed"
                and .snapshot == $baseline
            ' "$MP_MANUAL_EXPORT_STATE" >/dev/null 2>&1; then
                if mp_rotation_finalize_state "$state" "$MP_SNAPSHOTS/$baseline" \
                    "$(jq -r '.package_sha256' "$MP_MANUAL_EXPORT_STATE")"; then
                    continue
                fi
                ui_error "The interrupted rotation has portable evidence, but final validation failed. No old protected copies were removed."
                return 1
            fi
            ui_error "Rotation ${job} is safely paused. Export the exact baseline ${baseline} to a workstation, confirm its SHA-256, then choose Resume pending recovery-key rotation."
            return 1
        fi
        if ! ui_require_phrase "Interrupted recovery-key rotation" \
            "Rotation ${job} stopped in phase ${phase}. MP-OPT_SERVER must reconcile it before another recovery-key change." \
            "RECOVER ROTATION $job"; then
            return 1
        fi
        mp_lock || return 1
        export MP_MANAGEMENT_LOCK_HELD=1
        if [ "$phase" = baseline-verified ]; then
            mp_rotation_restore_recipient "$role" "$new_recipient" || { mp_unlock; return 1; }
            if [ -f "$registry" ]; then
                while IFS=$'\t' read -r scope host parent base; do
                    mp_rotation_remove_backup "$scope" "$host" "$parent" "$base" "$job" || { mp_unlock; return 1; }
                done < "$registry"
            fi
            mp_rotation_state_write "$state" "$job" complete "$old_recipient" "$new_recipient" "$baseline"
        else
            mp_rotation_restore_recipient "$role" "$old_recipient" || { mp_unlock; return 1; }
            if [ -f "$registry" ]; then
                while IFS=$'\t' read -r scope host parent base; do
                    mp_rotation_rollback_copy "$scope" "$host" "$parent" "$base" "$job" || { mp_unlock; return 1; }
                done < "$registry"
            fi
            mp_rotation_remove_baseline_copies "$baseline" || { mp_unlock; return 1; }
            mp_rotation_state_write "$state" "$job" rolled-back "$old_recipient" "$new_recipient" "$baseline"
        fi
        mp_unlock
    done < <(find "$MP_STATE/recovery-rotations" -maxdepth 1 -type f -name '*.state.json' | sort)
}

# Re-encrypt into a separate directory and deep-verify with the new identity.
# Nothing in the source directory is modified.
mp_snapshot_reencrypt_path() {
    local source="$1" destination="$2" old_identity="$3" new_identity="$4" new_recipient="$5" job_id="$6"
    local extracted archive_hash archive_size recipient_hash key_id
    [ -d "$source" ] && [ ! -e "$destination" ] || return 1
    extracted="$(mktemp -d "${MP_SNAPSHOTS}/.rotation-extract.XXXXXX")" || return 1
    chmod 700 "$extracted" || { rm -rf "$extracted"; return 1; }
    if ! mp_snapshot_extract "$source" "$old_identity" "$extracted" \
        || ! mp_snapshot_verify_extracted "$extracted" \
        || ! mp_snapshot_verify_key_metadata "$extracted" "$old_identity"; then
        rm -rf "$extracted"
        return 1
    fi
    recipient_hash="$(mp_recovery_recipient_fingerprint "$new_recipient")" || { rm -rf "$extracted"; return 1; }
    key_id="$(mp_recovery_key_id "$new_recipient")" || { rm -rf "$extracted"; return 1; }
    jq --arg recipient "$new_recipient" --arg recipient_hash "$recipient_hash" \
        --arg key_id "$key_id" --arg job_id "$job_id" --arg rotated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
        .format = "mp-opt-snapshot-v2"
        | .encryption = {scheme:"age-x25519",recipient:$recipient,recipient_sha256:$recipient_hash,recovery_key_id:$key_id}
        | .rotation = {job_id:$job_id,rotated_at:$rotated_at}
    ' "$extracted/manifest.json" > "$extracted/manifest.next" || { rm -rf "$extracted"; return 1; }
    mv "$extracted/manifest.next" "$extracted/manifest.json"
    chmod 600 "$extracted/manifest.json"

    mkdir -m 0700 "$destination" || { rm -rf "$extracted"; return 1; }
    if ! tar -C "$extracted" -cf - manifest.json payload \
        | age -r "$new_recipient" -o "$destination/snapshot.tar.age"; then
        rm -rf "$extracted" "$destination"
        return 1
    fi
    archive_hash="$(sha256sum "$destination/snapshot.tar.age" | awk '{print $1}')" || { rm -rf "$extracted" "$destination"; return 1; }
    archive_size="$(stat -c '%s' "$destination/snapshot.tar.age")" || { rm -rf "$extracted" "$destination"; return 1; }
    printf '%s  snapshot.tar.age\n' "$archive_hash" > "$destination/archive.sha256"
    jq --arg archive_hash "$archive_hash" --argjson archive_size "$archive_size" \
        --arg recipient "$new_recipient" --arg recipient_hash "$recipient_hash" --arg key_id "$key_id" \
        --arg job_id "$job_id" --arg rotated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
        .format = "mp-opt-snapshot-receipt-v2"
        | .archive_sha256 = $archive_hash | .archive_size = $archive_size
        | .verification = "encrypted" | del(.verified_at)
        | .recovery_status = "key-required"
        | .encryption = {scheme:"age-x25519",recipient:$recipient,recipient_sha256:$recipient_hash,recovery_key_id:$key_id}
        | .rotation = {job_id:$job_id,rotated_at:$rotated_at}
        | .storage = {local:"hash-verified",off_server:"pending-rotation"}
    ' "$source/receipt.json" > "$destination/receipt.json" || { rm -rf "$extracted" "$destination"; return 1; }
    chmod 600 "$destination/"*
    rm -rf "$extracted"
    mp_snapshot_verify_path "$destination" "$new_identity"
}

# Inventory managed copies. Remote paths are relative to the SSH account home.
mp_rotation_inventory_remote_directory() {
    local scope="$1" host="$2" directory="$3" base
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
        -o ClearAllForwardings=yes "$host" \
        "if [ -d '$directory' ]; then find '$directory' -mindepth 1 -maxdepth 1 -type d ! -name '.*' -printf '%f\\n' | sort; fi" \
        | while IFS= read -r base; do
            [[ "$base" =~ ^[0-9]{8}T[0-9]{6}Z_(database|secrets|full)_[A-Za-z0-9._-]{1,64}$ ]] || return 1
            printf '%s\t%s\t%s\t%s\n' "$scope" "$host" "$directory" "$base"
        done
}

mp_rotation_inventory() {
    local directory node
    while IFS= read -r directory; do
        [ -f "$directory/snapshot.tar.age" ] && [ -f "$directory/archive.sha256" ] \
            && [ -f "$directory/receipt.json" ] || return 1
        [[ "$(basename "$directory")" =~ ^[0-9]{8}T[0-9]{6}Z_(database|secrets|full)_[A-Za-z0-9._-]{1,64}$ ]] || return 1
        printf 'local\t-\t%s\t%s\n' "$MP_SNAPSHOTS" "$(basename "$directory")"
    done < <(find "$MP_SNAPSHOTS" -mindepth 1 -maxdepth 1 -type d ! -name '.*' | sort)

    if [ "${HA_ROLE:-standalone}" = dynamic ]; then
        [ -n "${HA_PEER_SSH:-}" ] || return 1
        mp_rotation_inventory_remote_directory peer "$HA_PEER_SSH" masterplan-snapshots || return 1
    fi
    if [ "${MP_ROTATION_STORAGE_MODE:-${HA_RECOVERY_STORAGE_MODE:-manual_portable}}" = ssh_archive ] \
        && [ -n "${HA_ARCHIVE_SSH_TARGET:-}" ]; then
        if [ "${HA_ROLE:-standalone}" = dynamic ]; then
            for node in "$HA_NODE_ID" "$HA_PEER_NODE_ID"; do
                [[ "$node" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || return 1
                mp_rotation_inventory_remote_directory archive "$HA_ARCHIVE_SSH_TARGET" \
                    "masterplan-ha-archives/${HA_CLUSTER_ID}/${node}" || return 1
            done
        else
            mp_rotation_inventory_remote_directory archive "$HA_ARCHIVE_SSH_TARGET" \
                masterplan-ha-archives/standalone/standalone || return 1
        fi
    fi
}

# Require conservative staging space before decrypting anything. Three times
# the total encrypted size covers fetched remote originals, replacements and
# tar/age working overhead, plus a fixed one-GiB safety margin.
mp_rotation_require_staging_space() {
    local inventory="$1" scope host parent base size total=0 available required
    while IFS=$'\t' read -r scope host parent base; do
        if [ "$scope" = local ]; then
            size="$(stat -c '%s' "$parent/$base/snapshot.tar.age")" || return 1
        else
            size="$(ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ClearAllForwardings=yes "$host" \
                "stat -c '%s' '$parent/$base/snapshot.tar.age'")" || return 1
        fi
        [[ "$size" =~ ^[0-9]+$ ]] || return 1
        total=$((total + size))
    done < "$inventory"
    available="$(df -B1 --output=avail "$MP_SNAPSHOTS" | awk 'NR==2 {print $1}')" || return 1
    required=$((total * 3 + 1073741824))
    if [ "$available" -lt "$required" ]; then
        printf 'Recovery-key rotation needs at least %s bytes free; only %s are available.\n' "$required" "$available" >&2
        return 1
    fi
}

mp_rotation_commit_copy() {
    local scope="$1" host="$2" parent="$3" base="$4" replacement="$5" job_id="$6"
    local expected_archive expected_receipt remote_hashes
    if [ "$scope" = local ]; then
        mv "$parent/$base" "$parent/.pre-rotation-${job_id}-${base}" \
            && mv "$replacement" "$parent/$base"
        return
    fi
    rsync -a --delete --chmod=F600,D700 "$replacement/" "$host:$parent/.rotation-${job_id}-${base}/" || return 1
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ClearAllForwardings=yes "$host" \
        bash -s -- "$parent" "$base" "$job_id" <<'REMOTE'
set -Eeuo pipefail
parent="$1"; base="$2"; job="$3"
[[ "$parent" =~ ^[A-Za-z0-9._/-]+$ ]]
[[ "$base" =~ ^[0-9]{8}T[0-9]{6}Z_(database|secrets|full)_[A-Za-z0-9._-]{1,64}$ ]]
[[ "$job" =~ ^[0-9a-f-]{36}$ ]]
[ -d "$parent/$base" ] && [ -d "$parent/.rotation-$job-$base" ]
mv "$parent/$base" "$parent/.pre-rotation-$job-$base"
mv "$parent/.rotation-$job-$base" "$parent/$base"
REMOTE
    expected_archive="$(sha256sum "$replacement/snapshot.tar.age" | awk '{print $1}')" || return 1
    expected_receipt="$(sha256sum "$replacement/receipt.json" | awk '{print $1}')" || return 1
    remote_hashes="$(ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ClearAllForwardings=yes "$host" \
        "sha256sum '$parent/$base/snapshot.tar.age' '$parent/$base/receipt.json'" | awk '{print $1}')" || return 1
    [ "$remote_hashes" = "${expected_archive}"$'\n'"${expected_receipt}" ]
}

mp_rotation_rollback_copy() {
    local scope="$1" host="$2" parent="$3" base="$4" job_id="$5"
    if [ "$scope" = local ]; then
        if [ -d "$parent/.pre-rotation-${job_id}-${base}" ]; then
            rm -rf "$parent/$base"
            mv "$parent/.pre-rotation-${job_id}-${base}" "$parent/$base"
        fi
        return
    fi
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ClearAllForwardings=yes "$host" \
        bash -s -- "$parent" "$base" "$job_id" <<'REMOTE'
set -Eeuo pipefail
parent="$1"; base="$2"; job="$3"
[[ "$parent" =~ ^[A-Za-z0-9._/-]+$ ]]
[[ "$base" =~ ^[0-9]{8}T[0-9]{6}Z_(database|secrets|full)_[A-Za-z0-9._-]{1,64}$ ]]
[[ "$job" =~ ^[0-9a-f-]{36}$ ]]
if [ -d "$parent/.pre-rotation-$job-$base" ]; then
    rm -rf "$parent/$base"
    mv "$parent/.pre-rotation-$job-$base" "$parent/$base"
fi
rm -rf "$parent/.rotation-$job-$base"
REMOTE
}

mp_rotation_remove_backup() {
    local scope="$1" host="$2" parent="$3" base="$4" job_id="$5"
    if [ "$scope" = local ]; then
        rm -rf "$parent/.pre-rotation-${job_id}-${base}"
    else
        ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ClearAllForwardings=yes "$host" \
            "rm -rf '$parent/.pre-rotation-${job_id}-${base}' '$parent/.rotation-${job_id}-${base}'"
    fi
}

mp_rotation_copy_key() {
    printf '%s\0%s\0%s\0%s' "$1" "$2" "$3" "$4" | sha256sum | awk '{print substr($1,1,20)}'
}

mp_rotation_prepare_receipt_scope() {
    local replacement="$1" scope="$2" temporary
    temporary="$(mktemp "${replacement}/receipt.scope.XXXXXX")" || return 1
    jq --arg scope "$scope" '
        .storage = if $scope == "archive" then {local:"not-applicable",off_server:"deep-verified"}
          elif $scope == "peer" then {local:"deep-verified-on-peer",off_server:"pending-rotation"}
          else {local:"deep-verified",off_server:"pending-rotation"} end
    ' "$replacement/receipt.json" > "$temporary" || { rm -f "$temporary"; return 1; }
    chmod 600 "$temporary" && mv "$temporary" "$replacement/receipt.json"
}

mp_rotation_restore_recipient() {
    local role="$1" recipient="$2"
    if [ "$role" = dynamic ]; then
        mp_ha_sync_recovery_recipient "$recipient"
    else
        mp_store_recovery_recipient_local "$recipient"
    fi
}

mp_rotation_rollback_committed() {
    local job_id="$1" row scope host parent base status=0
    shift
    for row in "$@"; do
        IFS=$'\t' read -r scope host parent base <<< "$row"
        mp_rotation_rollback_copy "$scope" "$host" "$parent" "$base" "$job_id" || status=1
    done
    return "$status"
}

mp_snapshot_mark_unavailable_path() {
    local snapshot_path="$1" job_id="$2" old_key_id="$3" temporary
    temporary="$(mktemp "${snapshot_path}/receipt.unavailable.XXXXXX")" || return 1
    jq --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg job_id "$job_id" --arg key_id "$old_key_id" '
        .recovery_status = "unavailable-old-key"
        | .unavailable = {marked_at:$at,rotation_job_id:$job_id,required_recovery_key_id:$key_id}
    ' "$snapshot_path/receipt.json" > "$temporary" || { rm -f "$temporary"; return 1; }
    chmod 600 "$temporary" && mv "$temporary" "$snapshot_path/receipt.json"
}

mp_rotation_mark_unavailable_copy() {
    local scope="$1" host="$2" parent="$3" base="$4" job_id="$5" old_key_id="$6"
    if [ "$scope" = local ]; then
        mp_snapshot_mark_unavailable_path "$parent/$base" "$job_id" "$old_key_id"
        return
    fi
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ClearAllForwardings=yes "$host" \
        bash -s -- "$parent" "$base" "$job_id" "$old_key_id" <<'REMOTE'
set -Eeuo pipefail
umask 077
parent="$1"; base="$2"; job="$3"; key_id="$4"
[[ "$parent" =~ ^[A-Za-z0-9._/-]+$ ]]
[[ "$base" =~ ^[0-9]{8}T[0-9]{6}Z_(database|secrets|full)_[A-Za-z0-9._-]{1,64}$ ]]
[[ "$job" =~ ^[0-9a-f-]{36}$ ]]
[[ "$key_id" =~ ^[A-Za-z0-9._:-]{1,128}$ ]]
receipt="$parent/$base/receipt.json"
[ -f "$receipt" ] && [ ! -L "$receipt" ]
temporary="$(mktemp "${receipt}.unavailable.XXXXXX")"
jq --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg job_id "$job" --arg key_id "$key_id" '
  .recovery_status = "unavailable-old-key"
  | .unavailable = {marked_at:$at,rotation_job_id:$job_id,required_recovery_key_id:$key_id}
' "$receipt" > "$temporary"
chmod 600 "$temporary"
mv "$temporary" "$receipt"
REMOTE
}

# Explicitly rotate the long-lived recovery key. The normal path consolidates
# every managed copy; the emergency path never claims old ciphertext is usable.
mp_rotate_recovery_recipient() {
    local new_recipient="$1" old_recipient new_identity old_identity role storage_mode rotation_kind job_id work inventory journal state registry
    local old_key_id new_key_id scope host parent base source replacement copy_key baseline baseline_name row rollback_ok
    local baseline_ready managed_scope
    local -a committed_rows=()
    mp_require_ha_maintenance_window || return 1
    mp_rotation_reconcile_incomplete || return 1
    mp_require_commands age age-keygen jq rsync ssh sha256sum tar docker || return 1
    old_recipient="$(mp_recovery_recipient)" || return 1
    [ "$old_recipient" != "$new_recipient" ] || {
        ui_message "Recovery key" "That recovery recipient is already active."
        return 0
    }
    new_identity="$(mp_prompt_identity_for_recipient "$new_recipient" "the new recovery recipient")" || return 1
    MP_ROTATION_NEW_IDENTITY="$new_identity"
    export MP_ROTATION_NEW_IDENTITY
    trap mp_rotation_cleanup_transients EXIT
    trap 'exit 143' TERM
    old_key_id="$(mp_recovery_key_id "$old_recipient")"
    new_key_id="$(mp_recovery_key_id "$new_recipient")"
    old_identity="$(mp_prompt_optional_identity_for_recipient "$old_recipient" "existing snapshots")" || {
        mp_remove_identity_file "$new_identity"
        return 1
    }
    MP_ROTATION_OLD_IDENTITY="$old_identity"
    export MP_ROTATION_OLD_IDENTITY
    mp_load_ha_config || {
        mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
        return 1
    }
    role="$HA_ROLE"
    storage_mode="$(mp_recovery_storage_mode)" || {
        mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
        ui_error "The recovery storage mode is invalid. Configure it under High availability → Recovery storage."
        return 1
    }
    if [ "$storage_mode" = ssh_archive ] \
        && ! [[ "${HA_ARCHIVE_SSH_TARGET:-}" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$ ]]; then
        mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
        ui_error "SSH archive mode requires a verified user@host destination."
        return 1
    fi
    if [ "$role" = dynamic ]; then
        [[ "${HA_PEER_SSH:-}" =~ ^([A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+$ ]] \
            && [[ "$HA_CLUSTER_ID" =~ ^[A-Za-z0-9._-]{1,128}$ ]] \
            && [[ "$HA_NODE_ID" =~ ^[A-Za-z0-9._-]{1,128}$ ]] \
            && [[ "$HA_PEER_NODE_ID" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || {
                mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
                ui_error "HA peer or cluster identifiers are unsafe; rotation was stopped."
                return 1
            }
    fi

    job_id="$(cat /proc/sys/kernel/random/uuid)"
    if [ -z "$old_identity" ]; then rotation_kind=emergency; else rotation_kind=planned; fi
    MP_ROTATION_KIND="$rotation_kind"
    MP_ROTATION_STORAGE_MODE="$storage_mode"
    export MP_ROTATION_KIND MP_ROTATION_STORAGE_MODE
    work="$(mktemp -d "${MP_SNAPSHOTS}/.rotation-${job_id}.XXXXXX")" || return 1
    MP_ROTATION_WORK="$work"
    export MP_ROTATION_WORK
    chmod 700 "$work"
    inventory="$work/inventory.tsv"
    mkdir -p "$MP_STATE/recovery-rotations" && chmod 700 "$MP_STATE/recovery-rotations"
    journal="$MP_STATE/recovery-rotations/${job_id}.jsonl"
    state="$MP_STATE/recovery-rotations/${job_id}.state.json"
    registry="$MP_STATE/recovery-rotations/${job_id}.copies.tsv"
    : > "$journal" && chmod 600 "$journal"
    : > "$registry" && chmod 600 "$registry"
    mp_rotation_state_write "$state" "$job_id" staging "$old_recipient" "$new_recipient"
    mp_rotation_journal_event "$journal" started cluster "${old_key_id}->${new_key_id}"
    if ! mp_rotation_inventory > "$inventory"; then
        mp_rotation_journal_event "$journal" aborted inventory-failed
        mp_rotation_state_write "$state" "$job_id" rolled-back "$old_recipient" "$new_recipient"
        rm -rf "$work"; mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
        ui_error "The complete local, peer and off-server inventory could not be read. Nothing was changed."
        return 1
    fi
    if [ -z "$old_identity" ]; then
        if ! ui_require_phrase "Rotate without old recovery key" \
            "${old_key_id} was not supplied. Existing ciphertext will remain untouched and catalogued as unavailable. Only a new ${new_key_id} baseline will be usable unless the old identity is found later." \
            "ROTATE WITHOUT OLD KEY"; then
            mp_rotation_state_write "$state" "$job_id" rolled-back "$old_recipient" "$new_recipient"
            rm -rf "$work"; mp_remove_identity_file "$new_identity"
            return 0
        fi
        mp_lock || { rm -rf "$work"; mp_remove_identity_file "$new_identity"; return 1; }
        export MP_MANAGEMENT_LOCK_HELD=1
        if ! mp_rotation_restore_recipient "$role" "$new_recipient"; then
            mp_unlock; rm -rf "$work"; mp_remove_identity_file "$new_identity"
            ui_error "The new public recipient could not be installed consistently. Nothing was changed."
            return 1
        fi
        mp_rotation_state_write "$state" "$job_id" recipient-synced "$old_recipient" "$new_recipient"
        baseline_name="post-emergency-key-rotation-${job_id:0:12}"
        baseline="$(mp_snapshot_create full "$baseline_name" || true)"
        [ -z "$baseline" ] || mp_rotation_state_write "$state" "$job_id" baseline-creating \
            "$old_recipient" "$new_recipient" "$(basename "$baseline")"
        baseline_ready=true
        if [ -z "$baseline" ] || ! mp_snapshot_verify_path "$baseline" "$new_identity"; then
            baseline_ready=false
        elif [ "$storage_mode" = ssh_archive ] && ! mp_snapshot_copy_off_server "$baseline"; then
            baseline_ready=false
        fi
        if [ "$baseline_ready" != true ]; then
            rollback_ok=true
            [ -z "$baseline" ] || mp_rotation_remove_baseline_copies "$(basename "$baseline")" || rollback_ok=false
            mp_rotation_restore_recipient "$role" "$old_recipient" >/dev/null 2>&1 || rollback_ok=false
            if [ "$rollback_ok" = true ]; then
                mp_rotation_journal_event "$journal" rolled-back baseline-failed
                mp_rotation_state_write "$state" "$job_id" rolled-back "$old_recipient" "$new_recipient" \
                    "$([ -n "$baseline" ] && basename "$baseline" || true)"
            else
                mp_rotation_journal_event "$journal" recovery-required baseline-failed
            fi
            mp_unlock; rm -rf "$work"; mp_remove_identity_file "$new_identity"
            ui_error "The new deeply verified recovery baseline failed. $([ "$rollback_ok" = true ] && printf 'The old public recipient was restored.' || printf 'Recovery is incomplete; run this action again and reconcile the recorded rotation job.')"
            return 1
        fi
        mp_rotation_state_write "$state" "$job_id" baseline-verified "$old_recipient" "$new_recipient" "$(basename "$baseline")"
        while IFS=$'\t' read -r scope host parent base; do
            mp_rotation_journal_event "$journal" unavailable "$scope" "$base:$old_key_id"
            mp_rotation_mark_unavailable_copy "$scope" "$host" "$parent" "$base" "$job_id" "$old_key_id" || {
                mp_rotation_journal_event "$journal" warning unavailable-receipt "$scope:$base"
            }
        done < "$inventory"
        if [ "$storage_mode" = manual_portable ]; then
            mp_rotation_journal_event "$journal" awaiting-portable-export emergency "$(basename "$baseline"):$new_key_id"
            mp_rotation_state_write "$state" "$job_id" awaiting-portable-export \
                "$old_recipient" "$new_recipient" "$(basename "$baseline")"
            mp_audit "recovery-key.rotate" "pending" "emergency:manual-portable:${old_key_id}->${new_key_id}"
            mp_unlock; rm -rf "$work"; mp_remove_identity_file "$new_identity"
            ui_message "Recovery key pending workstation export" \
                "The new ${new_key_id} recipient and deeply verified baseline are active. Existing ${old_key_id} archives are catalogued as unavailable.\n\nExport this exact baseline to the workstation and confirm its SHA-256:\n$(basename "$baseline")\n\nThe rotation completes only after that confirmation."
            return 0
        fi
        mp_rotation_journal_event "$journal" completed emergency "$new_key_id"
        mp_rotation_state_write "$state" "$job_id" complete "$old_recipient" "$new_recipient" "$(basename "$baseline")"
        mp_audit "recovery-key.rotate" "success" "emergency:${old_key_id}->${new_key_id}"
        mp_unlock; rm -rf "$work"; mp_remove_identity_file "$new_identity"
        ui_message "Recovery key rotated" "A new local and SSH-archived baseline is deeply verified with ${new_key_id}. Existing ${old_key_id} archives remain encrypted and are catalogued as unavailable until their identity is found."
        return 0
    fi

    if ! mp_rotation_require_staging_space "$inventory"; then
        mp_rotation_journal_event "$journal" aborted insufficient-staging-space
        mp_rotation_state_write "$state" "$job_id" rolled-back "$old_recipient" "$new_recipient"
        rm -rf "$work"; mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
        ui_error "Snapshot sizes or safe staging capacity could not be verified. Nothing was changed."
        return 1
    fi

    if [ "$storage_mode" = ssh_archive ]; then
        managed_scope="local, peer and SSH-archive"
    else
        managed_scope="local and peer"
    fi
    if ! ui_require_phrase "Consolidate snapshot recovery key" \
        "Every managed ${managed_scope} snapshot will be decrypted with ${old_key_id}, re-encrypted with ${new_key_id}, and deeply verified. Originals remain until every replacement, the new baseline and the required independent copy are verified." \
        "ROTATE RECOVERY KEY"; then
        mp_rotation_state_write "$state" "$job_id" rolled-back "$old_recipient" "$new_recipient"
        rm -rf "$work"; mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
        return 0
    fi

    while IFS=$'\t' read -r scope host parent base; do
        copy_key="$(mp_rotation_copy_key "$scope" "$host" "$parent" "$base")"
        source="$parent/$base"
        if [ "$scope" != local ]; then
            source="$work/source-${copy_key}"
            mkdir -m 0700 "$source"
            if ! rsync -a --delete --chmod=F600,D700 "$host:$parent/$base/" "$source/"; then
                mp_rotation_journal_event "$journal" aborted fetch "$scope:$base"
                mp_rotation_state_write "$state" "$job_id" rolled-back "$old_recipient" "$new_recipient"
                rm -rf "$work"; mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
                ui_error "Could not fetch ${scope} copy ${base}. No archive was replaced."
                return 1
            fi
        fi
        replacement="$work/replacement-${copy_key}"
        if ! mp_snapshot_reencrypt_path "$source" "$replacement" "$old_identity" "$new_identity" "$new_recipient" "$job_id"; then
            mp_rotation_journal_event "$journal" aborted transform "$scope:$base"
            mp_rotation_state_write "$state" "$job_id" rolled-back "$old_recipient" "$new_recipient"
            rm -rf "$work"; mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
            ui_error "${scope} snapshot ${base} could not be decrypted, validated, re-encrypted and re-validated. All originals remain active."
            return 1
        fi
        mp_rotation_prepare_receipt_scope "$replacement" "$scope" || {
            mp_rotation_journal_event "$journal" aborted receipt-scope "$scope:$base"
            mp_rotation_state_write "$state" "$job_id" rolled-back "$old_recipient" "$new_recipient"
            rm -rf "$work"; mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
            ui_error "The verified replacement receipt for ${scope} snapshot ${base} could not be finalised. No archive was replaced."
            return 1
        }
        mp_rotation_journal_event "$journal" staged "$scope" "$base"
    done < "$inventory"

    mp_lock || {
        rm -rf "$work"; mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
        return 1
    }
    export MP_MANAGEMENT_LOCK_HELD=1
    mp_rotation_state_write "$state" "$job_id" committing "$old_recipient" "$new_recipient"
    while IFS=$'\t' read -r scope host parent base; do
        copy_key="$(mp_rotation_copy_key "$scope" "$host" "$parent" "$base")"
        replacement="$work/replacement-${copy_key}"
        printf '%s\t%s\t%s\t%s\n' "$scope" "$host" "$parent" "$base" >> "$registry"
        chmod 600 "$registry"
        if ! mp_rotation_commit_copy "$scope" "$host" "$parent" "$base" "$replacement" "$job_id"; then
            rollback_ok=true
            mp_rotation_rollback_copy "$scope" "$host" "$parent" "$base" "$job_id" || rollback_ok=false
            mp_rotation_rollback_committed "$job_id" "${committed_rows[@]}" || rollback_ok=false
            if [ "$rollback_ok" = true ]; then
                mp_rotation_journal_event "$journal" rolled-back commit-failed "$scope:$base"
                mp_rotation_state_write "$state" "$job_id" rolled-back "$old_recipient" "$new_recipient"
            else
                mp_rotation_journal_event "$journal" recovery-required commit-failed "$scope:$base"
            fi
            mp_unlock; rm -rf "$work"; mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
            ui_error "Installing one verified replacement failed. $([ "$rollback_ok" = true ] && printf 'Every installed copy was rolled back.' || printf 'Automatic rollback was incomplete; run the rotation action again and reconcile the recorded job before any other recovery operation.')"
            return 1
        fi
        committed_rows+=("$scope"$'\t'"$host"$'\t'"$parent"$'\t'"$base")
        mp_rotation_journal_event "$journal" committed "$scope" "$base"
    done < "$inventory"

    if ! mp_rotation_restore_recipient "$role" "$new_recipient"; then
        rollback_ok=true
        mp_rotation_rollback_committed "$job_id" "${committed_rows[@]}" || rollback_ok=false
        if [ "$rollback_ok" = true ]; then
            mp_rotation_journal_event "$journal" rolled-back recipient-sync
            mp_rotation_state_write "$state" "$job_id" rolled-back "$old_recipient" "$new_recipient"
        else
            mp_rotation_journal_event "$journal" recovery-required recipient-sync
        fi
        mp_unlock; rm -rf "$work"; mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
        ui_error "The public recipient could not be installed consistently. $([ "$rollback_ok" = true ] && printf 'Original archives were restored.' || printf 'Archive rollback is incomplete; rerun this action to reconcile the recorded job.')"
        return 1
    fi
    mp_rotation_state_write "$state" "$job_id" recipient-synced "$old_recipient" "$new_recipient"
    baseline_name="post-key-rotation-${job_id:0:12}"
    baseline="$(mp_snapshot_create full "$baseline_name" || true)"
    [ -z "$baseline" ] || mp_rotation_state_write "$state" "$job_id" baseline-creating \
        "$old_recipient" "$new_recipient" "$(basename "$baseline")"
    baseline_ready=true
    if [ -z "$baseline" ] || ! mp_snapshot_verify_path "$baseline" "$new_identity"; then
        baseline_ready=false
    elif [ "$storage_mode" = ssh_archive ] && ! mp_snapshot_copy_off_server "$baseline"; then
        baseline_ready=false
    fi
    if [ "$baseline_ready" != true ]; then
        rollback_ok=true
        [ -z "$baseline" ] || mp_rotation_remove_baseline_copies "$(basename "$baseline")" || rollback_ok=false
        mp_rotation_restore_recipient "$role" "$old_recipient" >/dev/null 2>&1 || rollback_ok=false
        mp_rotation_rollback_committed "$job_id" "${committed_rows[@]}" || rollback_ok=false
        if [ "$rollback_ok" = true ]; then
            mp_rotation_journal_event "$journal" rolled-back baseline-failed
            mp_rotation_state_write "$state" "$job_id" rolled-back "$old_recipient" "$new_recipient" \
                "$([ -n "$baseline" ] && basename "$baseline" || true)"
        else
            mp_rotation_journal_event "$journal" recovery-required baseline-failed
        fi
        mp_unlock; rm -rf "$work"; mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
        ui_error "The required new recovery baseline failed verification. $([ "$rollback_ok" = true ] && printf 'The old recipient and original archives were restored.' || printf 'Recovery is incomplete; run this action again and reconcile the recorded rotation job.')"
        return 1
    fi
    mp_rotation_state_write "$state" "$job_id" baseline-verified "$old_recipient" "$new_recipient" "$(basename "$baseline")"

    if [ "$storage_mode" = manual_portable ]; then
        mp_rotation_journal_event "$journal" awaiting-portable-export planned "$(basename "$baseline"):$new_key_id"
        mp_rotation_state_write "$state" "$job_id" awaiting-portable-export \
            "$old_recipient" "$new_recipient" "$(basename "$baseline")"
        mp_audit "recovery-key.rotate" "pending" "planned:manual-portable:${old_key_id}->${new_key_id}"
        mp_unlock; rm -rf "$work"; mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
        ui_message "Recovery key pending workstation export" \
            "Every managed snapshot has been re-encrypted and deeply verified with ${new_key_id}. Protected ${old_key_id} originals remain in place.\n\nExport this exact baseline to the workstation and confirm its SHA-256:\n$(basename "$baseline")\n\nOnly then will MP-OPT retire the protected old working copies and complete the rotation."
        return 0
    fi

    rollback_ok=true
    for row in "${committed_rows[@]}"; do
        IFS=$'\t' read -r scope host parent base <<< "$row"
        mp_rotation_remove_backup "$scope" "$host" "$parent" "$base" "$job_id" || {
            mp_rotation_journal_event "$journal" warning cleanup "$scope:$base"
            rollback_ok=false
        }
    done
    if [ "$rollback_ok" != true ]; then
        mp_rotation_journal_event "$journal" recovery-required cleanup
        mp_unlock; rm -rf "$work"; mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
        ui_error "The new key and verified baseline are active, but one protected old-copy cleanup failed. Run the rotation action again and complete the recorded recovery job before retiring the old key."
        return 1
    fi
    mp_rotation_journal_event "$journal" completed consolidated "$new_key_id"
    mp_rotation_state_write "$state" "$job_id" complete "$old_recipient" "$new_recipient" "$(basename "$baseline")"
    mp_audit "recovery-key.rotate" "success" "consolidated:${old_key_id}->${new_key_id}"
    mp_unlock; rm -rf "$work"; mp_remove_identity_file "$new_identity"; mp_remove_identity_file "$old_identity"
    ui_message "Recovery key consolidated" "Every managed snapshot and the new local/SSH-archived baseline is verified with ${new_key_id}. The previous ${old_key_id} identity is no longer needed for managed archives."
}
