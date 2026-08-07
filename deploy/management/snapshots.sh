#!/usr/bin/env bash

# Encrypted infrastructure snapshot creation, verification and recovery.

# Publish a deliberately non-secret snapshot summary for the root dashboard.
# Receipts are already public metadata; neither private identities nor local
# workstation/VPS paths are copied into the runtime document.
mp_snapshot_publish_status() {
    local temporary directory receipt snapshot_type
    local latest_database="null" latest_full="null" portable="null"
    local count=0 verified=0 storage_mode="manual_portable"
    mkdir -p "$(dirname "$MP_HA_SNAPSHOT_STATUS")" || return 1
    while IFS= read -r directory; do
        [ -f "$directory/receipt.json" ] && [ ! -L "$directory/receipt.json" ] || continue
        jq -e '.format == "mp-opt-snapshot-receipt-v2"' "$directory/receipt.json" >/dev/null 2>&1 || continue
        count=$((count + 1))
        [ "$(jq -r '.verification // empty' "$directory/receipt.json")" = deep-verified ] \
            && verified=$((verified + 1))
        snapshot_type="$(jq -r '.type // empty' "$directory/receipt.json")"
        receipt="$(jq -c '{
            name, type, created_at, archive_sha256, archive_size, verification,
            recovery_status, verified_at,
            recovery_key_id: .encryption.recovery_key_id,
            local_state: .storage.local,
            off_server_state: .storage.off_server,
            portable: (.storage.portable // null)
        }' "$directory/receipt.json")" || return 1
        if [ "$snapshot_type" = database ] && [ "$latest_database" = null ]; then
            latest_database="$receipt"
        elif [ "$snapshot_type" = full ] && [ "$latest_full" = null ]; then
            latest_full="$receipt"
        fi
    done < <(find "$MP_SNAPSHOTS" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -print | sort -r)

    if [ -f "$MP_MANUAL_EXPORT_STATE" ] && [ ! -L "$MP_MANUAL_EXPORT_STATE" ]; then
        portable="$(jq -c '{
            state, snapshot, confirmed_at, required_at, reason,
            package_id, package_sha256, package_size, archive_sha256, recovery_key_id
        }' "$MP_MANUAL_EXPORT_STATE" 2>/dev/null || printf null)"
    fi
    storage_mode="$(mp_recovery_storage_mode 2>/dev/null || printf manual_portable)"
    temporary="$(mktemp "${MP_HA_SNAPSHOT_STATUS}.XXXXXX")" || return 1
    jq -n \
        --arg format "mp-opt-ha-snapshot-status-v1" \
        --arg observed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg storage_mode "$storage_mode" \
        --argjson latest_database "$latest_database" \
        --argjson latest_full "$latest_full" \
        --argjson portable_export "$portable" \
        --argjson local_snapshot_count "$count" \
        --argjson deep_verified_count "$verified" \
        '{format:$format, observed_at:$observed_at, storage_mode:$storage_mode,
          latest_database:$latest_database, latest_full:$latest_full,
          portable_export:$portable_export, local_snapshot_count:$local_snapshot_count,
          deep_verified_count:$deep_verified_count}' > "$temporary" \
        || { rm -f "$temporary"; return 1; }
    chmod 644 "$temporary" && mv "$temporary" "$MP_HA_SNAPSHOT_STATUS"
}

# Copy protected configuration into a snapshot payload without exposing values.
mp_snapshot_copy_configuration() {
    local payload="$1"
    local include_caddy="$2"
    local caddy_mode
    mkdir -p "$payload/config" "$payload/config/secrets" "$payload/metadata" || return 1
    cp -a "$MP_ROOT/.env" "$payload/config/.env" || return 1
    if [ -d "$MP_ROOT/secrets" ]; then
        cp -a "$MP_ROOT/secrets/." "$payload/config/secrets/" || return 1
        # The optional Git credential is deliberately excluded from every
        # application/configuration backup. Restore leaves archival disabled
        # until the controller enters a fresh token through the masked TUI.
        rm -f "$payload/config/secrets/evidence_github_fine_grained_token" || return 1
    fi
    if [ -e "$payload/config/secrets/root_bootstrap_token" ] \
        && mp_root_bootstrap_is_disabled; then
        : > "$payload/config/secrets/root_bootstrap_token" || return 1
    fi
    if [ -f "$MP_ROOT/infra/docker-compose.override.yml" ]; then
        cp -a "$MP_ROOT/infra/docker-compose.override.yml" "$payload/config/docker-compose.override.yml" || return 1
        : > "$payload/metadata/compose-override-present" || return 1
    else
        : > "$payload/metadata/compose-override-absent" || return 1
    fi
    caddy_mode="$(mp_caddy_mode)"
    case "$caddy_mode" in
        container|host) ;;
        *)
            printf '%s\n' 'Snapshot creation stopped: the active Caddy topology could not be resolved.' >&2
            return 1
            ;;
    esac
    printf '%s\n' "$caddy_mode" > "$payload/metadata/caddy-topology" || return 1
    if [ "$include_caddy" = "yes" ] \
        && [ "$caddy_mode" = "host" ] \
        && [ -f "$MP_HOST_CADDYFILE" ]; then
        install -m 0600 "$MP_HOST_CADDYFILE" "$payload/config/Caddyfile" || return 1
        : > "$payload/metadata/host-caddy-present" || return 1
    fi
    if [ "$include_caddy" = "yes" ]; then
        if [ -d "$MP_ROOT/state/evidence" ] && [ ! -L "$MP_ROOT/state/evidence" ]; then
            mkdir -m 0700 "$payload/evidence" || return 1
            sudo -n cp -a "$MP_ROOT/state/evidence/." "$payload/evidence/" || return 1
            sudo -n chown -R "$(id -u):$(id -g)" "$payload/evidence" || return 1
            [ -s "$payload/evidence/ledger/chain-head.json" ] \
                && [ -s "$payload/evidence/public/instance_signing_key.pub" ] || return 1
        else
            return 1
        fi
    fi
    return 0
}

# Create and validate a PostgreSQL custom-format dump in a payload.
mp_snapshot_dump_database() {
    local payload="$1"
    mkdir -p "$payload/database" || return 1
    chmod 700 "$payload/database" || return 1
    mp_compose_init
    "${MP_COMPOSE[@]}" up -d db >/dev/null || return 1
    "${MP_COMPOSE[@]}" exec -T db pg_dump \
        -U masterplan -d masterplan -Fc > "$payload/database/masterplan.dump" || return 1
    chmod 600 "$payload/database/masterplan.dump" || return 1
    [ -s "$payload/database/masterplan.dump" ] || return 1
    "${MP_COMPOSE[@]}" exec -T db pg_restore --list \
        < "$payload/database/masterplan.dump" >/dev/null || return 1
}

# Bind a database-bearing snapshot to the exact signed ledger state captured
# while application writes are paused. This contains no personal data.
mp_snapshot_write_evidence_anchor() {
    local payload="$1" head copied_head
    mkdir -p "$payload/metadata" || return 1
    head="$MP_ROOT/state/evidence/ledger/chain-head.json"
    copied_head="$payload/evidence/ledger/chain-head.json"
    if [ -s "$copied_head" ] && [ ! -L "$copied_head" ]; then
        jq -e '{format:"mp-opt-snapshot-evidence-anchor-v1", instance_id, chain_id,
            records, head_sha256}' "$copied_head" > "$payload/metadata/evidence-anchor.json" \
            || return 1
    elif sudo -n test -s "$head" && sudo -n test ! -L "$head"; then
        sudo -n cat "$head" \
            | jq -e '{format:"mp-opt-snapshot-evidence-anchor-v1", instance_id, chain_id,
                records, head_sha256}' > "$payload/metadata/evidence-anchor.json" \
            || return 1
    else
        return 1
    fi
    chmod 600 "$payload/metadata/evidence-anchor.json" || return 1
}

# Make encrypted payload permissions independent of the invoking shell's
# umask. GNU tar applies a restrictive extraction umask, so recording broader
# source modes would otherwise make an intact archive fail deep verification.
mp_snapshot_normalise_payload_permissions() {
    local payload="$1"
    [ -d "$payload" ] && [ ! -L "$payload" ] || return 1
    if find "$payload" -type l -print -quit | grep -q .; then
        return 1
    fi
    if find "$payload" ! -type f ! -type d -print -quit | grep -q .; then
        return 1
    fi
    find "$payload" -type d -exec chmod 700 {} + || return 1
    find "$payload" -type f -exec chmod 600 {} + || return 1
}

# Write the encrypted internal SHA-256 manifest for a snapshot payload.
mp_snapshot_write_manifest() {
    local staging="$1"
    local snapshot_type="$2"
    local snapshot_name="$3"
    local recipient="$4"
    local recipient_sha256 recovery_key_id
    local files_json="$staging/files.json"
    local list_file="$staging/files.tsv"
    local relative hash size mode
    : > "$list_file" || return 1
    while IFS= read -r -d '' file; do
        relative="${file#"$staging/"}"
        hash="$(sha256sum "$file" | awk '{print $1}')" || return 1
        size="$(stat -c '%s' "$file")" || return 1
        mode="$(stat -c '%a' "$file")" || return 1
        printf '%s\t%s\t%s\t%s\n' "$relative" "$hash" "$size" "$mode" >> "$list_file" || return 1
    done < <(find "$staging/payload" -type f -print0 | sort -z)

    jq -Rn '[inputs | split("\t") | {
        path: .[0], sha256: .[1], size: (.[2] | tonumber), mode: .[3]
    }]' < "$list_file" > "$files_json" || return 1

    recipient_sha256="$(mp_recovery_recipient_fingerprint "$recipient")" || return 1
    recovery_key_id="$(mp_recovery_key_id "$recipient")" || return 1
    jq -n \
        --arg format "mp-opt-snapshot-v2" \
        --arg type "$snapshot_type" \
        --arg name "$snapshot_name" \
        --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg hostname "$(hostname -f 2>/dev/null || hostname)" \
        --arg commit "$(git -C "$MP_ROOT" rev-parse HEAD 2>/dev/null || printf unknown)" \
        --arg backend_image "$(docker inspect -f '{{.Image}}' masterplan-backend-1 2>/dev/null || printf unavailable)" \
        --arg encryption_recipient "$recipient" \
        --arg encryption_recipient_sha256 "$recipient_sha256" \
        --arg recovery_key_id "$recovery_key_id" \
        --argjson files "$(cat "$files_json")" \
        '{
            format: $format,
            type: $type,
            name: $name,
            created_at: $created_at,
            hostname: $hostname,
            commit: $commit,
            backend_image: $backend_image,
            encryption: {
                scheme: "age-x25519",
                recipient: $encryption_recipient,
                recipient_sha256: $encryption_recipient_sha256,
                recovery_key_id: $recovery_key_id
            },
            files: $files
        }' > "$staging/manifest.json" || return 1
    chmod 600 "$staging/manifest.json" || return 1
}

# Create one encrypted database, secrets or complete recovery snapshot.
mp_snapshot_create() {
    local snapshot_type="$1"
    local snapshot_name="$2"
    local recipient recipient_sha256 recovery_key_id staging timestamp final temporary_final archive_hash archive_size owns_lock=false
    local backend_was_running=false backend_container="" capture_ok=true

    mp_require_commands age jq sha256sum tar docker || return 1
    recipient="$(mp_recovery_recipient)" || {
        ui_error "Configure a public age recovery recipient before creating snapshots."
        return 1
    }
    recipient_sha256="$(mp_recovery_recipient_fingerprint "$recipient")" || return 1
    recovery_key_id="$(mp_recovery_key_id "$recipient")" || return 1
    mp_validate_snapshot_name "$snapshot_name" || {
        ui_error "Snapshot names may contain 1-64 letters, numbers, dots, underscores or hyphens."
        return 1
    }
    case "$snapshot_type" in
        database|secrets|full) ;;
        *) ui_error "Unknown snapshot type."; return 1 ;;
    esac

    if [ "${MP_MANAGEMENT_LOCK_HELD:-0}" != 1 ]; then
        mp_lock || return 1
        owns_lock=true
        # RETURN is scoped to this function (functrace is not enabled). It
        # closes the management descriptor on every error branch, including
        # failures added later that do not remember to call mp_unlock.
        trap 'if [ "${owns_lock:-false}" = true ]; then mp_unlock; fi' RETURN
    fi
    staging="$(mktemp -d "${MP_SNAPSHOTS}/.staging.XXXXXX")" || return 1
    chmod 700 "$staging" || { rm -rf "$staging"; return 1; }
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    final="${MP_SNAPSHOTS}/${timestamp}_${snapshot_type}_${snapshot_name}"
    temporary_final="${final}.partial"
    if [ -e "$final" ] || [ -e "$temporary_final" ]; then
        rm -rf "$staging"
        ui_error "A snapshot with that generated identifier already exists."
        return 1
    fi

    mkdir -p "$staging/payload" || { rm -rf "$staging"; return 1; }
    chmod 700 "$staging/payload" || { rm -rf "$staging"; return 1; }
    if [ "$snapshot_type" = database ] || [ "$snapshot_type" = full ]; then
        mp_compose_init
        backend_container="$("${MP_COMPOSE[@]}" ps -q backend 2>/dev/null || true)"
        if [ -n "$backend_container" ] \
            && [ "$(docker inspect -f '{{.State.Running}}' "$backend_container" 2>/dev/null || true)" = true ]; then
            "${MP_COMPOSE[@]}" stop backend >/dev/null || {
                rm -rf "$staging"
                [ "$owns_lock" != true ] || mp_unlock
                return 1
            }
            backend_was_running=true
        fi
    fi
    case "$snapshot_type" in
        database)
            mp_snapshot_dump_database "$staging/payload" \
                && mp_snapshot_write_evidence_anchor "$staging/payload" \
                || capture_ok=false
            ;;
        secrets)
            mp_snapshot_copy_configuration "$staging/payload" "no" || capture_ok=false
            ;;
        full)
            mp_snapshot_copy_configuration "$staging/payload" "yes" \
                && mp_snapshot_dump_database "$staging/payload" \
                && mp_snapshot_write_evidence_anchor "$staging/payload" \
                || capture_ok=false
            ;;
    esac
    if [ "$backend_was_running" = true ]; then
        "${MP_COMPOSE[@]}" up -d --no-deps backend >/dev/null || capture_ok=false
    fi
    if [ "$capture_ok" != true ]; then
        rm -rf "$staging"
        [ "$owns_lock" != true ] || mp_unlock
        return 1
    fi

    if ! mp_snapshot_normalise_payload_permissions "$staging/payload"; then
        rm -rf "$staging"
        ui_error "Snapshot payloads may contain only protected regular files and directories."
        return 1
    fi

    if ! mp_snapshot_write_manifest "$staging" "$snapshot_type" "$snapshot_name" "$recipient"; then
        rm -rf "$staging"
        return 1
    fi
    mkdir -p "$temporary_final" || { rm -rf "$staging"; return 1; }
    chmod 700 "$temporary_final" || { rm -rf "$staging" "$temporary_final"; return 1; }
    if ! tar -C "$staging" -cf - manifest.json payload \
        | age -r "$recipient" -o "$temporary_final/snapshot.tar.age"; then
        rm -rf "$staging" "$temporary_final"
        return 1
    fi
    [ -s "$temporary_final/snapshot.tar.age" ] || {
        rm -rf "$staging" "$temporary_final"
        return 1
    }
    archive_hash="$(sha256sum "$temporary_final/snapshot.tar.age" | awk '{print $1}')" || {
        rm -rf "$staging" "$temporary_final"; return 1;
    }
    archive_size="$(stat -c '%s' "$temporary_final/snapshot.tar.age")" || {
        rm -rf "$staging" "$temporary_final"; return 1;
    }
    printf '%s  snapshot.tar.age\n' "$archive_hash" > "$temporary_final/archive.sha256" || {
        rm -rf "$staging" "$temporary_final"; return 1;
    }
    jq -n \
        --arg format "mp-opt-snapshot-receipt-v2" \
        --arg type "$snapshot_type" \
        --arg name "$snapshot_name" \
        --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg archive_sha256 "$archive_hash" \
        --arg encryption_recipient "$recipient" \
        --arg encryption_recipient_sha256 "$recipient_sha256" \
        --arg recovery_key_id "$recovery_key_id" \
        --argjson archive_size "$archive_size" \
        '{
            format: $format,
            type: $type,
            name: $name,
            created_at: $created_at,
            archive_sha256: $archive_sha256,
            archive_size: $archive_size,
            verification: "encrypted",
            recovery_status: "key-required",
            encryption: {
                scheme: "age-x25519",
                recipient: $encryption_recipient,
                recipient_sha256: $encryption_recipient_sha256,
                recovery_key_id: $recovery_key_id
            },
            storage: {local: "hash-verified", off_server: "not-copied"}
        }' > "$temporary_final/receipt.json" || {
            rm -rf "$staging" "$temporary_final"
            return 1
        }
    chmod 600 "$temporary_final/"* || { rm -rf "$staging" "$temporary_final"; return 1; }
    mv "$temporary_final" "$final" || { rm -rf "$staging" "$temporary_final"; return 1; }
    rm -rf "$staging"
    mp_audit "snapshot.create" "success" "${snapshot_type}:${snapshot_name}"
    mp_snapshot_publish_status || true
    [ "$owns_lock" != true ] || mp_unlock
    trap - RETURN
    printf '%s\n' "$final"
}

# Copy one encrypted snapshot to the independently configured SSH archive and
# prove the archive, checksum and public receipt arrived byte-for-byte. No
# private recovery key is involved in this operation.
mp_snapshot_copy_off_server() {
    local snapshot_path="$1" destination="${2:-${HA_ARCHIVE_SSH_TARGET:-}}"
    local cluster_id="${HA_CLUSTER_ID:-standalone}" node_id="${HA_NODE_ID:-standalone}"
    local remote_dir archive_hash receipt_hash remote_values receipt_tmp
    [ -n "$destination" ] || return 2
    [[ "$destination" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$ ]] || return 1
    [[ "$cluster_id" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || return 1
    [[ "$node_id" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || return 1
    [[ "$(basename "$snapshot_path")" =~ ^[0-9]{8}T[0-9]{6}Z_(database|secrets|full)_[A-Za-z0-9._-]{1,64}$ ]] || return 1
    remote_dir="masterplan-ha-archives/${cluster_id}/${node_id}/$(basename "$snapshot_path")"
    archive_hash="$(sha256sum "$snapshot_path/snapshot.tar.age" | awk '{print $1}')" || return 1
    receipt_hash="$(sha256sum "$snapshot_path/receipt.json" | awk '{print $1}')" || return 1
    ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ClearAllForwardings=yes "$destination" \
        "mkdir -p '$remote_dir' && chmod 700 '$remote_dir'" || return 1
    rsync -a --delete --chmod=F600,D700 "$snapshot_path/" "$destination:$remote_dir/" || return 1
    remote_values="$(ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ClearAllForwardings=yes "$destination" \
        "sha256sum '$remote_dir/snapshot.tar.age' '$remote_dir/receipt.json'" \
        | awk '{print $1}')" || return 1
    [ "$remote_values" = "${archive_hash}"$'\n'"${receipt_hash}" ] || return 1

    receipt_tmp="$(mktemp "${snapshot_path}/receipt.off-server.XXXXXX")" || return 1
    jq --arg copied_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg target_hash "$(printf '%s' "$destination" | sha256sum | awk '{print $1}')" '
        .storage = ((.storage // {}) + {
            off_server: "hash-verified", off_server_verified_at: $copied_at,
            off_server_target_sha256: $target_hash
        })' "$snapshot_path/receipt.json" > "$receipt_tmp" || { rm -f "$receipt_tmp"; return 1; }
    chmod 600 "$receipt_tmp" && mv "$receipt_tmp" "$snapshot_path/receipt.json" || { rm -f "$receipt_tmp"; return 1; }
    receipt_hash="$(sha256sum "$snapshot_path/receipt.json" | awk '{print $1}')" || return 1
    rsync -a --chmod=F600 "$snapshot_path/receipt.json" "$destination:$remote_dir/receipt.json" || return 1
    [ "$(ssh -T -o BatchMode=yes -o ConnectTimeout=10 -o ClearAllForwardings=yes "$destination" \
        "sha256sum '$remote_dir/receipt.json'" | awk '{print $1}')" = "$receipt_hash" ]
}

# Reject archive members capable of escaping the protected verification directory.
mp_snapshot_validate_members() {
    local list_file="$1"
    local member
    [ "$(grep -Fxc 'manifest.json' "$list_file")" -eq 1 ] || return 1
    if sort "$list_file" | uniq -d | grep -q .; then
        return 1
    fi
    while IFS= read -r member; do
        case "$member" in
            /*|../*|*/../*|*/..|..)
                return 1
                ;;
            manifest.json|payload|payload/*) ;;
            *) return 1 ;;
        esac
    done < "$list_file"
}

# Decrypt a snapshot into a protected directory after checking its outer hash.
mp_snapshot_extract() {
    local snapshot_path="$1"
    local identity_file="$2"
    local destination="$3"
    local expected actual members member_types
    [ -f "$snapshot_path/archive.sha256" ] || return 1
    expected="$(awk 'NR == 1 {print $1}' "$snapshot_path/archive.sha256")" || return 1
    actual="$(sha256sum "$snapshot_path/snapshot.tar.age" | awk '{print $1}')" || return 1
    [ -n "$expected" ] && [ "$expected" = "$actual" ] || return 1
    members="$destination/members.txt"
    age -d -i "$identity_file" "$snapshot_path/snapshot.tar.age" \
        | tar -tf - > "$members" || return 1
    mp_snapshot_validate_members "$members" || return 1
    member_types="$destination/member-types.txt"
    age -d -i "$identity_file" "$snapshot_path/snapshot.tar.age" \
        | tar -tvf - > "$member_types" || return 1
    awk 'substr($0, 1, 1) != "-" && substr($0, 1, 1) != "d" {exit 1}' \
        "$member_types" || return 1
    age -d -i "$identity_file" "$snapshot_path/snapshot.tar.age" \
        | tar -C "$destination" -xf - || return 1
    jq -e '.format == "mp-opt-snapshot-v2" and (.files | type == "array")' \
        "$destination/manifest.json" >/dev/null || return 1
}

# Validate the key-generation metadata inside a decrypted v2 archive.
mp_snapshot_verify_key_metadata() {
    local directory="$1" identity_file="$2" identity_recipient expected_hash expected_id
    identity_recipient="$(mp_identity_recipient "$identity_file")" || return 1
    expected_hash="$(mp_recovery_recipient_fingerprint "$identity_recipient")" || return 1
    expected_id="$(mp_recovery_key_id "$identity_recipient")" || return 1
    jq -e --arg recipient "$identity_recipient" --arg hash "$expected_hash" --arg key_id "$expected_id" '
        .format == "mp-opt-snapshot-v2"
        and .encryption.scheme == "age-x25519"
        and .encryption.recipient == $recipient
        and .encryption.recipient_sha256 == $hash
        and .encryption.recovery_key_id == $key_id
    ' "$directory/manifest.json" >/dev/null
}

# Compare every decrypted payload file with the encrypted internal manifest.
mp_snapshot_verify_extracted_files() {
    local directory="$1"
    local relative expected_hash expected_size expected_mode file actual_hash
    local manifest_count actual_count
    if find "$directory/payload" -type l -print -quit | grep -q .; then
        return 1
    fi
    if find "$directory/payload" ! -type f ! -type d -print -quit | grep -q .; then
        return 1
    fi
    manifest_count="$(jq '.files | length' "$directory/manifest.json")" || return 1
    actual_count="$(find "$directory/payload" -type f | wc -l)" || return 1
    [ "$manifest_count" = "$actual_count" ] || return 1
    while IFS=$'\t' read -r relative expected_hash expected_size expected_mode; do
        case "$relative" in
            payload/*) ;;
            *) return 1 ;;
        esac
        file="$directory/$relative"
        [ -f "$file" ] || return 1
        actual_hash="$(sha256sum "$file" | awk '{print $1}')" || return 1
        [ "$actual_hash" = "$expected_hash" ] || return 1
        [ "$(stat -c '%s' "$file")" = "$expected_size" ] || return 1
        [ "$(stat -c '%a' "$file")" = "$expected_mode" ] || return 1
    done < <(jq -r '.files[] | [.path, .sha256, (.size | tostring), .mode] | @tsv' "$directory/manifest.json")

}

# Validate file integrity first, then ask PostgreSQL to parse a database
# catalogue when the payload contains one.
mp_snapshot_verify_extracted() {
    local directory="$1"
    mp_snapshot_verify_extracted_files "$directory" || return 1
    if [ -f "$directory/payload/database/masterplan.dump" ]; then
        mp_compose_init
        "${MP_COMPOSE[@]}" up -d db >/dev/null || return 1
        "${MP_COMPOSE[@]}" exec -T db pg_restore --list \
            < "$directory/payload/database/masterplan.dump" >/dev/null || return 1
    fi
}

# Recover a completely blank VPS from one exact imported full snapshot. This
# deliberately ignores node-local HA, Compose override and host-Caddy files:
# the recovered server starts as a safe standalone node and can commission a
# new peer only after public health has passed.
mp_snapshot_restore_full_loss() {
    local snapshot_path="$1" expected_recipient identity temporary payload
    local installed=false resume_installed=false
    mp_require_commands age jq sha256sum diff docker || return 1
    if [ -e "$MP_ROOT/.env" ]; then
        resume_installed=true
    else
        if [ -d "$MP_ROOT/secrets" ] \
            && find "$MP_ROOT/secrets" -maxdepth 1 -type f -size +0c -print -quit | grep -q .; then
            ui_error "Full-loss recovery found protected secrets without a configuration. Resolve that partial installation manually before recovery."
            return 1
        fi
        if docker volume inspect masterplan_pgdata >/dev/null 2>&1; then
            ui_error "Full-loss recovery found an existing masterplan database volume. It will not guess that the volume is disposable; recover or remove it explicitly first."
            return 1
        fi
    fi
    [ "$(mp_ha_role 2>/dev/null || printf standalone)" = standalone ] \
        || { ui_error "This VPS still has a node-local HA identity. Clear or complete that HA workflow before full-loss recovery."; return 1; }
    [ -d "$snapshot_path" ] && mp_snapshot_receipt_is_v2 "$snapshot_path" \
        || { ui_error "The recorded imported snapshot is missing or unsupported."; return 1; }
    [ "$(jq -r '.type // empty' "$snapshot_path/receipt.json")" = full ] \
        || { ui_error "Full-loss recovery requires a full snapshot, not a database-only or secrets-only snapshot."; return 1; }
    expected_recipient="$(jq -er '.encryption.recipient | select(test("^age1[0-9a-z]+$"))' \
        "$snapshot_path/receipt.json")" \
        || { ui_error "The snapshot receipt has no valid public recovery recipient."; return 1; }
    identity="$(mp_prompt_identity_for_recipient "$expected_recipient" "the imported full snapshot")" \
        || return 1
    temporary="$(mktemp -d "$MP_SNAPSHOTS/.full-loss.XXXXXX")" || {
        mp_remove_identity_file "$identity"; return 1;
    }
    chmod 700 "$temporary"
    if ! mp_snapshot_extract "$snapshot_path" "$identity" "$temporary" \
        || ! mp_snapshot_verify_extracted_files "$temporary" \
        || ! mp_snapshot_verify_key_metadata "$temporary" "$identity"; then
        mp_remove_identity_file "$identity"; rm -rf "$temporary"
        ui_error "The recovery identity, encrypted archive, or internal file hashes did not verify. Nothing was restored."
        return 1
    fi
    mp_remove_identity_file "$identity"
    payload="$temporary/payload"
    [ -f "$payload/config/.env" ] && [ -d "$payload/config/secrets" ] \
        && [ -f "$payload/database/masterplan.dump" ] || {
        rm -rf "$temporary"
        ui_error "The full snapshot does not contain environment, secrets, and database payloads. Nothing was restored."
        return 1
    }
    if [ "$resume_installed" = true ]; then
        if ! cmp -s "$payload/config/.env" "$MP_ROOT/.env" \
            || ! diff -qr "$payload/config/secrets" "$MP_ROOT/secrets" >/dev/null \
            || [ "$(mp_recovery_recipient 2>/dev/null || true)" != "$expected_recipient" ]; then
            rm -rf "$temporary"
            ui_error "The partial local configuration does not exactly match the recorded recovery snapshot. Nothing was overwritten."
            return 1
        fi
        installed=true
        ui_message "Resume recovery" "The protected shared configuration matches the imported snapshot exactly. Database restoration will restart from its clean restore boundary."
    else
        if ! ui_require_phrase "Recover blank server" \
            "This installs the verified shared configuration and database as a standalone server. Old HA identity, TLS topology, sessions, and one-time ceremonies are not restored." \
            "RECOVER BLANK SERVER"; then
            rm -rf "$temporary"
            return 1
        fi

        # Install only shared configuration. Node-local topology is
        # intentionally rebuilt later rather than replayed from the old VPS.
        cp -a "$payload/config/.env" "$MP_ROOT/.env" || { rm -rf "$temporary"; return 1; }
        chmod 600 "$MP_ROOT/.env"
        mkdir -m 0700 "$MP_ROOT/secrets" || { rm -f "$MP_ROOT/.env"; rm -rf "$temporary"; return 1; }
        cp -a "$payload/config/secrets/." "$MP_ROOT/secrets/" \
            && find "$MP_ROOT/secrets" -maxdepth 1 -type f -exec chmod 600 {} + \
            && mp_store_recovery_recipient_local "$expected_recipient" \
            || { rm -f "$MP_ROOT/.env"; rm -rf "$MP_ROOT/secrets" "$temporary"; return 1; }
        installed=true
    fi
    rm -f "$MP_ROOT/infra/docker-compose.override.yml"
    if "$MP_ROOT/deploy/deploy.sh" --no-pull \
        && mp_snapshot_restore_database "$payload/database/masterplan.dump" \
        && mp_apply_migrations \
        && mp_snapshot_restore_evidence "$payload" \
        && mp_snapshot_revoke_restored_access; then
        mp_compose_init
        if "${MP_COMPOSE[@]}" up -d --force-recreate >/dev/null \
            && mp_caddy_validate && mp_wait_for_health 30; then
            rm -rf "$temporary"
            mp_audit "snapshot.full-loss-restore" "success" "$(basename "$snapshot_path")"
            return 0
        fi
    fi
    if [ "$installed" = true ]; then
        mp_compose_init 2>/dev/null || true
        "${MP_COMPOSE[@]}" down -v >/dev/null 2>&1 || true
        rm -f "$MP_ROOT/.env" "$MP_RECIPIENT_FILE"
        rm -rf "$MP_ROOT/secrets"
    fi
    rm -rf "$temporary"
    mp_audit "snapshot.full-loss-restore" "failed" "$(basename "$snapshot_path")"
    ui_error "Blank-server recovery failed. Generated configuration and the new database volume were removed; the imported encrypted snapshot was retained for retry."
    return 1
}

# Perform a deep recovery-key and payload-hash verification.
mp_snapshot_verify_path() {
    local snapshot_path="$1"
    local identity_file="$2"
    local temporary receipt_tmp identity_recipient recipient_hash recovery_key_id evidence_head
    [ -d "$snapshot_path" ] || return 1
    [ -f "$snapshot_path/snapshot.tar.age" ] || return 1
    mp_snapshot_receipt_is_v2 "$snapshot_path" || return 1
    temporary="$(mktemp -d "${MP_SNAPSHOTS}/.verify.XXXXXX")" || return 1
    chmod 700 "$temporary" || { rm -rf "$temporary"; return 1; }
    if ! mp_snapshot_extract "$snapshot_path" "$identity_file" "$temporary" \
        || ! mp_snapshot_verify_extracted "$temporary" \
        || ! mp_snapshot_verify_key_metadata "$temporary" "$identity_file"; then
        rm -rf "$temporary"
        mp_audit "snapshot.verify" "failed" "$(basename "$snapshot_path")"
        return 1
    fi
    receipt_tmp="$(mktemp "${snapshot_path}/receipt.tmp.XXXXXX")" || {
        rm -rf "$temporary"
        return 1
    }
    identity_recipient="$(mp_identity_recipient "$identity_file")" || { rm -rf "$temporary"; return 1; }
    recipient_hash="$(mp_recovery_recipient_fingerprint "$identity_recipient")" || { rm -rf "$temporary"; return 1; }
    recovery_key_id="$(mp_recovery_key_id "$identity_recipient")" || { rm -rf "$temporary"; return 1; }
    evidence_head="$(jq -er '.head_sha256 | select(test("^[0-9a-f]{64}$"))' \
        "$temporary/payload/metadata/evidence-anchor.json" 2>/dev/null || true)"
    jq --arg verified_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --arg recipient "$identity_recipient" --arg recipient_hash "$recipient_hash" \
        --arg recovery_key_id "$recovery_key_id" --arg evidence_head "$evidence_head" '
        .format = "mp-opt-snapshot-receipt-v2"
        | .verification = "deep-verified"
        | .verified_at = $verified_at
        | .recovery_status = "recoverable"
        | .encryption = {
            scheme: "age-x25519", recipient: $recipient,
            recipient_sha256: $recipient_hash, recovery_key_id: $recovery_key_id
          }
        | .storage = ((.storage // {}) + {local: "deep-verified"})
        | .evidence = (if $evidence_head == "" then null else {
            format: "mp-opt-snapshot-evidence-anchor-v1",
            head_sha256: $evidence_head
          } end)' \
        "$snapshot_path/receipt.json" > "$receipt_tmp" || {
            rm -rf "$temporary"
            rm -f "$receipt_tmp"
            return 1
        }
    chmod 600 "$receipt_tmp" || { rm -rf "$temporary"; rm -f "$receipt_tmp"; return 1; }
    mv "$receipt_tmp" "$snapshot_path/receipt.json" || {
        rm -rf "$temporary"
        rm -f "$receipt_tmp"
        return 1
    }
    rm -rf "$temporary"
    mp_audit "snapshot.verify" "success" "$(basename "$snapshot_path")"
    mp_snapshot_publish_status || true
}

# Return success only for the currently supported public snapshot receipt.
mp_snapshot_receipt_is_v2() {
    local directory="$1"
    [ -f "$directory/receipt.json" ] && [ ! -L "$directory/receipt.json" ] \
        && jq -e '.format == "mp-opt-snapshot-receipt-v2"
            and (.source_manifest_format == null or .source_manifest_format == "mp-opt-snapshot-v2")' \
            "$directory/receipt.json" >/dev/null 2>&1
}

# Present supported snapshots, or every receipt when deletion explicitly asks
# for unsupported test-era archives as well.
mp_snapshot_select() {
    local prompt="$1"
    local selection_scope="${2:-supported}"
    local -a choices=()
    local directory label metadata receipt_format
    while IFS= read -r directory; do
        [ -f "$directory/receipt.json" ] || continue
        label="$(basename "$directory")"
        receipt_format="$(jq -r '.format // "unreadable"' "$directory/receipt.json" 2>/dev/null || printf unreadable)"
        if ! mp_snapshot_receipt_is_v2 "$directory"; then
            [ "$selection_scope" = any ] || continue
            metadata="UNSUPPORTED ${receipt_format} or legacy manifest | deletion only"
        else
            metadata="$(jq -r '.type + " | " + .verification + " | " + .encryption.recovery_key_id + " | " + ((.archive_size / 1048576 * 100 | round) / 100 | tostring) + " MiB"' "$directory/receipt.json" 2>/dev/null || printf unreadable)"
        fi
        choices+=("$label" "$metadata")
    done < <(find "$MP_SNAPSHOTS" -mindepth 1 -maxdepth 1 -type d ! -name '.*' | sort -r)
    if [ "${#choices[@]}" -eq 0 ]; then
        ui_error "No snapshots are available."
        return 1
    fi
    local selected
    selected="$(ui_menu "Snapshots" "$prompt" "${choices[@]}")" || return 1
    [ -n "$selected" ] || return 1
    [ -d "$MP_SNAPSHOTS/$selected" ] || return 1
    printf '%s\n' "$MP_SNAPSHOTS/$selected"
}

# Ask for a name and create a selected snapshot type from the menu.
mp_snapshot_create_interactive() {
    local snapshot_type="$1"
    local name result
    name="$(ui_input "Create ${snapshot_type} snapshot" "Choose a short recovery name")" || return 1
    result="$(mp_snapshot_create "$snapshot_type" "$name")" || {
        ui_error "Snapshot creation failed. No completed archive was installed."
        return 1
    }
    mp_load_ha_config || return 1
    if [ "${HA_RECOVERY_STORAGE_MODE:-manual_portable}" = ssh_archive ]; then
        if ! mp_snapshot_copy_off_server "$result"; then
            ui_error "The snapshot is complete locally, but the independent off-server hash verification failed: $(basename "$result")"
            return 1
        fi
        ui_message "Snapshot created" "$(basename "$result")\n\nThe encrypted local and off-server copies match by archive and receipt SHA-256. Use Deep verify to prove recovery-key access and all internal hashes."
    else
        ui_message "Snapshot created" \
            "$(basename "$result")\n\nThe archive is encrypted and currently exists on this VPS. Deep-verify it, then use Export portable snapshot and compare the workstation SHA-256 to create the independent manual recovery copy."
    fi
}

# Deep-verify a selected snapshot using a transient private age identity.
mp_snapshot_verify_interactive() {
    local selected identity expected_recipient compliance_receipts="" compliance_note=""
    local compliance_error_file compliance_error compliance_removed_count="0"
    selected="$(mp_snapshot_select "Choose a snapshot to verify")" || return 1
    expected_recipient="$(jq -r '.encryption.recipient // empty' "$selected/receipt.json" 2>/dev/null || true)"
    if [ -n "$expected_recipient" ]; then
        identity="$(mp_prompt_identity_for_recipient "$expected_recipient" "this snapshot")" || return 1
    else
        identity="$(mp_prompt_identity_file)" || return 1
    fi
    if mp_snapshot_verify_path "$selected" "$identity"; then
        mp_remove_identity_file "$identity"
        if declare -F mp_compliance_emit_backup_receipts >/dev/null; then
            compliance_error_file="$(mktemp "$MP_STATE/compliance-error.XXXXXX")" || return 1
            compliance_receipts="$(mp_compliance_emit_backup_receipts "$selected" 2>"$compliance_error_file")" || {
                compliance_error="$(mp_compliance_error_message "$compliance_error_file")"
                rm -f "$compliance_error_file"
                ui_error "The snapshot was verified, but pending deletion recovery receipts could not be recorded. The verified snapshot was retained.\n\n${compliance_error}"
                return 1
            }
            rm -f "$compliance_error_file"
            if [ -n "$compliance_receipts" ]; then
                compliance_removed_count="$(awk -F '\t' '$1 == "RESOLVED" { print $2 }' <<< "$compliance_receipts" | tail -n 1)"
                [[ "$compliance_removed_count" =~ ^[0-9]+$ ]] || compliance_removed_count="0"
                compliance_note="\n\nPending deletion recovery receipts were recorded. ${compliance_removed_count} pre-deletion local snapshot(s) were removed automatically. External workstation copies remain separately accountable. The web page will detect the receipts automatically."
            elif find "$MP_ROOT/runtime/compliance-requests" -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null | grep -q .; then
                compliance_note="\n\nA deletion workflow is still waiting. Export this verified snapshot to the workstation and confirm its SHA-256; MP-OPT will then record the recovery receipt."
            fi
        fi
        ui_message "Snapshot verified" "The recovery identity, archive hash, payload hashes, file sizes and database catalogue all match.${compliance_note}"
    else
        mp_remove_identity_file "$identity"
        ui_error "Deep verification failed. The snapshot was not modified or restored."
        return 1
    fi
}

# Restore a PostgreSQL custom dump into a clean application database.
mp_snapshot_restore_database() {
    local dump="$1"
    mp_compose_init
    "${MP_COMPOSE[@]}" stop backend >/dev/null 2>&1 || true
    "${MP_COMPOSE[@]}" up -d db >/dev/null || return 1
    "${MP_COMPOSE[@]}" exec -T db dropdb --if-exists --force -U masterplan masterplan || return 1
    "${MP_COMPOSE[@]}" exec -T db createdb -U masterplan masterplan || return 1
    "${MP_COMPOSE[@]}" exec -T db pg_restore \
        -U masterplan -d masterplan --no-owner --no-privileges < "$dump" || return 1
}

# Restore protected env, Docker secrets, override and host Caddy configuration.
mp_snapshot_restore_configuration() {
    local payload="$1"
    local target_password escaped_password secrets_stage secrets_old secret_file
    local snapshot_caddy_mode current_caddy_mode optional_evidence_token
    [ -f "$payload/config/.env" ] || return 0
    MP_SNAPSHOT_APPLY_STAGE="configuration-topology"
    if [ -f "$payload/metadata/caddy-topology" ]; then
        snapshot_caddy_mode="$(tr -d '\r\n' < "$payload/metadata/caddy-topology")"
        current_caddy_mode="$(mp_caddy_mode)"
        if [ "$snapshot_caddy_mode" != "$current_caddy_mode" ]; then
            printf 'Snapshot Caddy topology is %s, but this installation uses %s. Restore was stopped.\n' \
                "$snapshot_caddy_mode" "$current_caddy_mode" >&2
            return 1
        fi
    fi
    if [ -f "$payload/config/Caddyfile" ]; then
        command -v caddy >/dev/null 2>&1 \
            && systemctl cat caddy >/dev/null 2>&1 \
            || { printf 'This snapshot requires host Caddy, but host Caddy is unavailable.\n' >&2; return 1; }
        sudo caddy validate --config "$payload/config/Caddyfile" --adapter caddyfile >/dev/null || return 1
    fi
    MP_SNAPSHOT_APPLY_STAGE="configuration-environment"
    cp -a "$payload/config/.env" "$MP_ROOT/.env" || return 1
    chmod 600 "$MP_ROOT/.env" || return 1
    cmp -s "$payload/config/.env" "$MP_ROOT/.env" || return 1
    MP_SNAPSHOT_APPLY_STAGE="configuration-secrets"
    if [ -d "$payload/config/secrets" ]; then
        secrets_stage="$MP_ROOT/.secrets.restore.$$"
        secrets_old="$MP_ROOT/.secrets.restore-old.$$"
        [ ! -e "$secrets_stage" ] && [ ! -e "$secrets_old" ] || return 1
        mkdir -m 0700 "$secrets_stage" || return 1
        cp -a "$payload/config/secrets/." "$secrets_stage/" || {
            rm -rf "$secrets_stage"
            return 1
        }
        find "$secrets_stage" -maxdepth 1 -type f -exec chmod 600 {} + || {
            rm -rf "$secrets_stage"
            return 1
        }
        while IFS= read -r -d '' secret_file; do
            cmp -s "$secret_file" "$secrets_stage/$(basename "$secret_file")" || {
                rm -rf "$secrets_stage"
                return 1
            }
        done < <(find "$payload/config/secrets" -maxdepth 1 -type f -print0)
        if [ -d "$MP_ROOT/secrets" ]; then
            mv "$MP_ROOT/secrets" "$secrets_old" || { rm -rf "$secrets_stage"; return 1; }
        fi
        if ! mv "$secrets_stage" "$MP_ROOT/secrets"; then
            [ ! -d "$secrets_old" ] || mv "$secrets_old" "$MP_ROOT/secrets" || true
            return 1
        fi
        rm -rf "$secrets_old" || return 1
    else
        mkdir -p "$MP_ROOT/secrets" || return 1
    fi
    MP_SNAPSHOT_APPLY_STAGE="configuration-secret-permissions"
    chmod 700 "$MP_ROOT/secrets" || return 1
    find "$MP_ROOT/secrets" -maxdepth 1 -type f -exec chmod 600 {} + || return 1
    optional_evidence_token="$MP_ROOT/secrets/evidence_github_fine_grained_token"
    if [ -e "$optional_evidence_token" ] \
        && { [ -L "$optional_evidence_token" ] || [ ! -f "$optional_evidence_token" ]; }; then
        printf '%s\n' 'The optional Evidence archive credential path is unsafe.' >&2
        return 1
    fi
    # The credential is intentionally excluded from snapshots. Compose still
    # requires a regular bind source, so restore recreates only the empty,
    # disabled-state placeholder and never invents or recovers a token.
    if [ ! -e "$optional_evidence_token" ]; then
        install -m 0600 /dev/null "$optional_evidence_token" || return 1
    fi
    MP_SNAPSHOT_APPLY_STAGE="configuration-compose-override"
    if [ -f "$payload/config/docker-compose.override.yml" ]; then
        cp -a "$payload/config/docker-compose.override.yml" "$MP_ROOT/infra/docker-compose.override.yml" || return 1
        chmod 600 "$MP_ROOT/infra/docker-compose.override.yml" || return 1
        cmp -s "$payload/config/docker-compose.override.yml" "$MP_ROOT/infra/docker-compose.override.yml" || return 1
    elif [ -f "$payload/metadata/compose-override-absent" ]; then
        rm -f "$MP_ROOT/infra/docker-compose.override.yml" || return 1
    fi
    MP_SNAPSHOT_APPLY_STAGE="configuration-host-caddy"
    if [ -f "$payload/config/Caddyfile" ]; then
        sudo install -o root -g root -m 0644 "$payload/config/Caddyfile" "$MP_HOST_CADDYFILE" || return 1
        cmp -s "$payload/config/Caddyfile" "$MP_HOST_CADDYFILE" || return 1
    fi

    MP_SNAPSHOT_APPLY_STAGE="configuration-database-secret"
    mp_migrate_database_secret || return 1
    target_password="$(cat "$MP_ROOT/secrets/database_password")" || return 1
    escaped_password="$(printf '%s' "$target_password" | sed "s/'/''/g")" || return 1
    mp_compose_init
    MP_SNAPSHOT_APPLY_STAGE="configuration-database-role"
    printf "ALTER ROLE masterplan PASSWORD '%s';\n" "$escaped_password" \
        | "${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U masterplan -d postgres >/dev/null || return 1
    unset target_password escaped_password
    MP_SNAPSHOT_APPLY_STAGE="configuration-backend-secret-permissions"
    mp_prepare_backend_secret_permissions || return 1
    return 0
}

# Atomically restore the signed accountability ledger owned by the backend
# service account. Full snapshots always carry this beside their database.
mp_snapshot_restore_evidence() {
    local payload="$1" stage old
    if [ ! -d "$payload/evidence" ]; then
        sudo -n rm -rf "$MP_ROOT/state/evidence" || return 1
        sudo -n install -d -o 10001 -g 10001 -m 0700 "$MP_ROOT/state/evidence" || return 1
        return 0
    fi
    [ ! -L "$payload/evidence" ] \
        && [ -s "$payload/evidence/ledger/chain-head.json" ] \
        && [ -s "$payload/evidence/public/instance_signing_key.pub" ] || return 1
    stage="$MP_ROOT/state/.evidence.restore.$$"
    old="$MP_ROOT/state/.evidence.restore-old.$$"
    [ ! -e "$stage" ] && [ ! -e "$old" ] || return 1
    mkdir -m 0700 "$stage" || return 1
    cp -a "$payload/evidence/." "$stage/" || { rm -rf "$stage"; return 1; }
    find "$stage" -type l -print -quit | grep -q . && { rm -rf "$stage"; return 1; }
    find "$stage" ! -type f ! -type d -print -quit | grep -q . \
        && { rm -rf "$stage"; return 1; }
    find "$stage" -type d -exec chmod 700 {} + \
        && find "$stage" -type f -exec chmod 600 {} + || { rm -rf "$stage"; return 1; }
    sudo -n chown -R 10001:10001 "$stage" || { sudo -n rm -rf "$stage"; return 1; }
    if [ -d "$MP_ROOT/state/evidence" ]; then
        sudo -n mv "$MP_ROOT/state/evidence" "$old" \
            || { sudo -n rm -rf "$stage"; return 1; }
    fi
    if ! sudo -n mv "$stage" "$MP_ROOT/state/evidence"; then
        [ ! -d "$old" ] || sudo -n mv "$old" "$MP_ROOT/state/evidence" || true
        return 1
    fi
    sudo -n rm -rf "$old"
}

# Refuse to revive data covered by a newer privacy-action tombstone. The
# deletion workflow requires a clean replacement backup; restoring anything
# older would contradict that retained accountability state.
mp_snapshot_guard_privacy_actions() {
    local extracted="$1" latest_action snapshot_created
    [ -f "$extracted/payload/database/masterplan.dump" ] || return 0
    mp_compose_init
    latest_action="$("${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d masterplan -Atqc \
        "SELECT COALESCE(to_char(max(created_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), '') FROM privacy_action_receipts" \
        2>/dev/null || true)"
    [ -n "$latest_action" ] || return 0
    snapshot_created="$(jq -er '.created_at | select(type == "string")' "$extracted/manifest.json")" \
        || return 1
    if [[ "$snapshot_created" < "$latest_action" || "$snapshot_created" = "$latest_action" ]]; then
        printf 'Restore blocked: this snapshot predates retained deletion action %s. Use the clean replacement snapshot recorded by the deletion workflow.\n' \
            "$latest_action" >&2
        return 1
    fi
}

# A normal in-place restore deliberately retains the append-only evidence
# store. Therefore its database must describe the exact same ledger head.
# Reject incompatibility before replacing any live database.
mp_snapshot_guard_evidence_head() {
    local extracted="$1" anchor current anchor_head current_head
    [ -f "$extracted/payload/database/masterplan.dump" ] || return 0
    anchor="$extracted/payload/metadata/evidence-anchor.json"
    current="$MP_ROOT/state/evidence/ledger/chain-head.json"
    if [ ! -s "$anchor" ] || [ -L "$anchor" ] \
        || ! sudo -n test -s "$current" || ! sudo -n test ! -L "$current"; then
        printf '%s\n' 'Restore blocked: required signed-evidence anchor is missing.' >&2
        return 1
    fi
    anchor_head="$(jq -er '.head_sha256 | select(type == "string" and test("^[0-9a-f]{64}$"))' "$anchor")" \
        || { printf '%s\n' 'Restore blocked: snapshot evidence anchor is invalid.' >&2; return 1; }
    current_head="$(sudo -n cat "$current" 2>/dev/null \
        | jq -er '.head_sha256 | select(type == "string" and test("^[0-9a-f]{64}$"))')" \
        || { printf '%s\n' 'Restore blocked: current protected evidence head is unreadable or invalid.' >&2; return 1; }
    if [ "$anchor_head" != "$current_head" ]; then
        printf '%s\n' 'Restore blocked: snapshot database and current signed-evidence ledger have different heads. Choose a compatible newer snapshot; the ledger will not be rewound.' >&2
        return 1
    fi
}

# Reject a cross-topology or unresolved configuration snapshot before stopping
# services or replacing the database. The same check remains in the
# configuration-restore function as defence in depth.
mp_snapshot_guard_caddy_topology() {
    local extracted="$1" marker snapshot_mode current_mode
    [ -f "$extracted/payload/config/.env" ] || return 0
    marker="$extracted/payload/metadata/caddy-topology"
    if [ ! -s "$marker" ] || [ -L "$marker" ]; then
        printf '%s\n' 'Restore blocked: snapshot Caddy topology is missing.' >&2
        return 1
    fi
    snapshot_mode="$(tr -d '\r\n' < "$marker")"
    current_mode="$(mp_caddy_mode)"
    case "$snapshot_mode" in container|host) ;; *)
        printf 'Restore blocked: snapshot Caddy topology is invalid: %s.\n' "$snapshot_mode" >&2
        return 1
        ;;
    esac
    case "$current_mode" in container|host) ;; *)
        printf '%s\n' 'Restore blocked: current Caddy topology is unavailable.' >&2
        return 1
        ;;
    esac
    if [ "$snapshot_mode" != "$current_mode" ]; then
        printf 'Restore blocked: snapshot Caddy topology is %s, but this installation uses %s.\n' \
            "$snapshot_mode" "$current_mode" >&2
        return 1
    fi
}

# Ensure replaying an older standalone database cannot revive bearer access or
# publishing credentials. Registered passkeys are deliberately preserved.
mp_snapshot_revoke_restored_access() {
    local registered_root
    mp_compose_init
    "${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U masterplan -d masterplan <<'SQL'
BEGIN;
UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE revoked_at IS NULL;
DELETE FROM exchange_codes;
DELETE FROM passkey_ceremonies;
DELETE FROM passkey_challenges;
UPDATE activation_links
SET invalidated_at = CURRENT_TIMESTAMP, delivery_pending = FALSE
WHERE invalidated_at IS NULL;
COMMIT;
SQL
    registered_root="$("${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 \
        -U masterplan -d masterplan -Atqc \
        'SELECT EXISTS (SELECT 1 FROM users u JOIN webauthn_credentials c ON c.user_id=u.id WHERE u.is_root_admin)' \
        2>/dev/null || true)"
    if [ "$registered_root" = t ]; then
        : > "$MP_ROOT/secrets/root_bootstrap_token" || return 1
        # Truncating the retired token must not undo the backend-readable
        # ownership and mode established during configuration restore.
        mp_prepare_backend_secret_permissions || return 1
    fi
}

# Apply one already verified snapshot payload and restart affected services.
mp_snapshot_apply() {
    local snapshot_path="$1"
    local identity_file="$2"
    local temporary payload domain restored_database=false
    MP_SNAPSHOT_APPLY_MUTATED=false
    MP_SNAPSHOT_APPLY_STAGE="extract-and-verify"
    temporary="$(mktemp -d "${MP_SNAPSHOTS}/.restore.XXXXXX")" || return 1
    chmod 700 "$temporary" || { rm -rf "$temporary"; return 1; }
    if ! mp_snapshot_extract "$snapshot_path" "$identity_file" "$temporary" \
        || ! mp_snapshot_verify_extracted "$temporary" \
        || ! mp_snapshot_verify_key_metadata "$temporary" "$identity_file"; then
        rm -rf "$temporary"
        return 1
    fi
    payload="$temporary/payload"
    MP_SNAPSHOT_APPLY_STAGE="evidence-head-preflight"
    mp_snapshot_guard_evidence_head "$temporary" || {
        rm -rf "$temporary"
        return 1
    }
    MP_SNAPSHOT_APPLY_STAGE="privacy-action-preflight"
    mp_snapshot_guard_privacy_actions "$temporary" || {
        rm -rf "$temporary"
        return 1
    }
    MP_SNAPSHOT_APPLY_STAGE="caddy-topology-preflight"
    mp_snapshot_guard_caddy_topology "$temporary" || {
        rm -rf "$temporary"
        return 1
    }
    mp_compose_init
    MP_SNAPSHOT_APPLY_STAGE="stop-backend"
    MP_SNAPSHOT_APPLY_MUTATED=true
    "${MP_COMPOSE[@]}" stop backend >/dev/null 2>&1 || true
    if [ -f "$payload/database/masterplan.dump" ]; then
        restored_database=true
        MP_SNAPSHOT_APPLY_STAGE="database-restore"
        mp_snapshot_restore_database "$payload/database/masterplan.dump" || {
            rm -rf "$temporary"
            return 1
        }
        MP_SNAPSHOT_APPLY_STAGE="database-migrations"
        mp_apply_migrations || {
            rm -rf "$temporary"
            return 1
        }
    fi
    MP_SNAPSHOT_APPLY_STAGE="configuration-restore"
    mp_snapshot_restore_configuration "$payload" || {
        rm -rf "$temporary"
        return 1
    }
    MP_SNAPSHOT_APPLY_STAGE="compose-validation"
    mp_compose_validate || {
        rm -rf "$temporary"
        return 1
    }
    mp_compose_init
    if [ "$(mp_ha_role)" = "dynamic" ]; then
        local witness_generation
        witness_generation="$(jq -r '.generation // 0' "$MP_ROOT/runtime/ha-control.json")"
        MP_MANAGEMENT_LOCK_HELD=1 "$MP_ROOT/deploy/ha/promote_local.sh" "$witness_generation" --force-revoke || {
            rm -rf "$temporary"
            return 1
        }
    elif [ "$restored_database" = true ]; then
        MP_SNAPSHOT_APPLY_STAGE="access-revocation"
        mp_snapshot_revoke_restored_access || {
            rm -rf "$temporary"
            return 1
        }
    fi
    MP_SNAPSHOT_APPLY_STAGE="service-start"
    "${MP_COMPOSE[@]}" up -d --force-recreate >/dev/null || {
        rm -rf "$temporary"
        return 1
    }
    if [ "$(mp_caddy_mode)" = "host" ]; then
        MP_SNAPSHOT_APPLY_STAGE="host-caddy-reload"
        mp_caddy_reload || {
            rm -rf "$temporary"
            return 1
        }
    fi
    MP_SNAPSHOT_APPLY_STAGE="caddy-validation"
    mp_caddy_validate || {
        rm -rf "$temporary"
        return 1
    }
    domain="$(mp_env_get DOMAIN)" || { rm -rf "$temporary"; return 1; }
    MP_SNAPSHOT_APPLY_STAGE="public-health"
    if ! mp_wait_for_health 30; then
        rm -rf "$temporary"
        return 1
    fi
    rm -rf "$temporary"
    MP_SNAPSHOT_APPLY_STAGE="complete"
    printf '%s\n' "$domain" >/dev/null
}

# Restore a selected snapshot with a newly deep-verified full rollback point.
mp_snapshot_restore_interactive() {
    mp_require_ha_maintenance_window || return 1
    local selected identity rollback_identity="" pre_snapshot pre_name selected_recipient current_recipient
    local failed_stage rollback_stage
    selected="$(mp_snapshot_select "Choose a snapshot to restore")" || return 1
    selected_recipient="$(jq -r '.encryption.recipient // empty' "$selected/receipt.json" 2>/dev/null || true)"
    current_recipient="$(mp_recovery_recipient)" || {
        ui_error "No current public recovery recipient is configured."
        return 1
    }
    if [ -n "$selected_recipient" ]; then
        identity="$(mp_prompt_identity_for_recipient "$selected_recipient" "the selected snapshot")" || return 1
    else
        identity="$(mp_prompt_identity_file)" || return 1
        selected_recipient="$(mp_identity_recipient "$identity")" || { mp_remove_identity_file "$identity"; return 1; }
    fi
    if ! mp_snapshot_verify_path "$selected" "$identity"; then
        mp_remove_identity_file "$identity"
        ui_error "The selected snapshot or recovery identity failed verification."
        return 1
    fi
    if [ "$selected_recipient" = "$current_recipient" ]; then
        rollback_identity="$identity"
    else
        rollback_identity="$(mp_prompt_identity_for_recipient "$current_recipient" "the mandatory rollback snapshot")" || {
            mp_remove_identity_file "$identity"
            return 1
        }
    fi
    if ! ui_require_phrase "Restore snapshot" \
        "Restoring can replace the database, environment, secrets and proxy configuration." \
        "RESTORE SNAPSHOT"; then
        mp_remove_identity_file "$identity"
        [ "$rollback_identity" = "$identity" ] || mp_remove_identity_file "$rollback_identity"
        return 1
    fi
    ui_clear_terminal
    pre_name="pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
    pre_snapshot="$(mp_snapshot_create full "$pre_name")" || {
        mp_remove_identity_file "$identity"
        [ "$rollback_identity" = "$identity" ] || mp_remove_identity_file "$rollback_identity"
        ui_error "The mandatory pre-restore snapshot could not be created."
        return 1
    }
    if ! mp_snapshot_verify_path "$pre_snapshot" "$rollback_identity"; then
        mp_remove_identity_file "$identity"
        [ "$rollback_identity" = "$identity" ] || mp_remove_identity_file "$rollback_identity"
        ui_error "The mandatory rollback snapshot could not be deeply verified."
        return 1
    fi
    mp_load_ha_config || {
        mp_remove_identity_file "$identity"
        [ "$rollback_identity" = "$identity" ] || mp_remove_identity_file "$rollback_identity"
        return 1
    }
    if [ "${HA_RECOVERY_STORAGE_MODE:-manual_portable}" = ssh_archive ] \
        && ! mp_snapshot_copy_off_server "$pre_snapshot"; then
        mp_remove_identity_file "$identity"
        [ "$rollback_identity" = "$identity" ] || mp_remove_identity_file "$rollback_identity"
        ui_error "The mandatory rollback snapshot is valid locally, but its configured off-server copy failed hash verification. Restore was stopped."
        return 1
    fi
    mp_lock || {
        mp_remove_identity_file "$identity"
        [ "$rollback_identity" = "$identity" ] || mp_remove_identity_file "$rollback_identity"
        return 1
    }
    if mp_snapshot_apply "$selected" "$identity"; then
        mp_remove_identity_file "$identity"
        [ "$rollback_identity" = "$identity" ] || mp_remove_identity_file "$rollback_identity"
        mp_audit "snapshot.restore" "success" "$(basename "$selected")"
        mp_load_ha_config || return 1
        if [ "${HA_RECOVERY_STORAGE_MODE:-manual_portable}" = manual_portable ] \
            && declare -F mp_portable_mark_export_required >/dev/null; then
            mp_portable_mark_export_required "snapshot-restore" || return 1
        fi
        if [ "$(mp_ha_role)" = "dynamic" ]; then
            mkdir -p "$MP_ROOT/runtime/ha-requests"
            printf '{"format":"mp-opt-replication-request-v1","job_id":"%s","reason":"snapshot-restore","created_at":"%s"}\n' \
                "$(cat /proc/sys/kernel/random/uuid)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
                > "$MP_ROOT/runtime/ha-requests/restore-$(date -u +%s).json"
            chmod 600 "$MP_ROOT/runtime/ha-requests/"*.json
            ui_message "Restore complete" \
                "The selected snapshot was restored, bearer access was revoked, and a fresh peer copy was queued. Passkeys remain registered.$([ "${HA_RECOVERY_STORAGE_MODE:-manual_portable}" = manual_portable ] && printf '\n\nManual recovery action required: create, deep-verify and export a fresh full workstation snapshot.' || true)"
        else
            ui_message "Restore complete" \
                "The selected snapshot was restored and public health passed. Bearer access and one-time activation ceremonies were revoked; registered passkeys, public schedule links, and publisher credentials remain valid.$([ "${HA_RECOVERY_STORAGE_MODE:-manual_portable}" = manual_portable ] && printf '\n\nManual recovery action required: create, deep-verify and export a fresh full workstation snapshot.' || true)"
        fi
        mp_snapshot_publish_status || true
        return 0
    fi

    failed_stage="${MP_SNAPSHOT_APPLY_STAGE:-unknown}"
    mp_audit "snapshot.restore" "failed" "$(basename "$selected"):${failed_stage}"
    if [ "${MP_SNAPSHOT_APPLY_MUTATED:-false}" != true ]; then
        mp_remove_identity_file "$identity"
        [ "$rollback_identity" = "$identity" ] || mp_remove_identity_file "$rollback_identity"
        ui_error "Restore was rejected during protected preflight (${failed_stage}). No database, configuration, or service state was changed."
        return 1
    fi
    if mp_snapshot_apply "$pre_snapshot" "$rollback_identity"; then
        mp_remove_identity_file "$identity"
        [ "$rollback_identity" = "$identity" ] || mp_remove_identity_file "$rollback_identity"
        ui_error "Restore verification failed. The automatic rollback snapshot was restored successfully."
    else
        rollback_stage="${MP_SNAPSHOT_APPLY_STAGE:-unknown}"
        mp_remove_identity_file "$identity"
        [ "$rollback_identity" = "$identity" ] || mp_remove_identity_file "$rollback_identity"
        if mp_wait_for_health 1; then
            ui_error "Restore failed during ${failed_stage}; automatic rollback also reported failure during ${rollback_stage}, but public health is currently available. Do not retry until the retained state has been verified. Use the verified pre-restore snapshot if recovery is required: $(basename "$pre_snapshot")"
        else
            ui_error "Restore failed during ${failed_stage} and automatic rollback failed during ${rollback_stage}. Application health is unavailable. Use the verified pre-restore snapshot: $(basename "$pre_snapshot")"
        fi
    fi
    return 1
}

# Delete only an explicitly selected completed snapshot.
mp_snapshot_delete_interactive() {
    local selected label
    selected="$(mp_snapshot_select "Choose a snapshot to delete permanently" any)" || return 1
    label="$(basename "$selected")"
    if ! ui_require_phrase "Delete snapshot" \
        "This permanently deletes $label, including its encrypted archive and receipt." \
        "DELETE SNAPSHOT"; then
        return 1
    fi
    rm -rf -- "$selected"
    mp_audit "snapshot.delete" "success" "$label"
    mp_snapshot_publish_status || true
    ui_message "Snapshot deleted" "$label was deleted."
}

# Display receipt metadata without decrypting snapshot content.
mp_snapshot_list_interactive() {
    local report directory receipt_format
    report="$(mktemp "${MP_STATE}/snapshot-list.XXXXXX")"
    printf 'Stored encrypted snapshots\n\n' > "$report"
    while IFS= read -r directory; do
        [ -f "$directory/receipt.json" ] || continue
        receipt_format="$(jq -r '.format // "unreadable"' "$directory/receipt.json" 2>/dev/null || printf unreadable)"
        if mp_snapshot_receipt_is_v2 "$directory"; then
            jq -r '"Directory: " + $directory + "\nName: " + .name + "\nType: " + .type + "\nCreated: " + .created_at + "\nVerification: " + .verification + "\nRecovery status: " + .recovery_status + "\nRecovery key: " + .encryption.recovery_key_id + "\nLocal copy: " + (.storage.local // "unknown") + "\nSSH archive: " + (.storage.off_server // "not-configured") + "\nPortable workstation copy: " + (.storage.portable.state // "not-confirmed") + "\nPortable confirmed: " + (.storage.portable.confirmed_at // "not-confirmed") + "\nPortable package SHA-256: " + (.storage.portable.package_sha256 // "not-confirmed") + "\nArchive SHA-256: " + .archive_sha256 + "\n"' \
                --arg directory "$(basename "$directory")" "$directory/receipt.json" >> "$report"
        else
            printf 'Directory: %s\nStatus: UNSUPPORTED %s or legacy manifest - deletion only\n\n' \
                "$(basename "$directory")" "$receipt_format" >> "$report"
        fi
    done < <(find "$MP_SNAPSHOTS" -mindepth 1 -maxdepth 1 -type d ! -name '.*' | sort -r)
    ui_text_file "Snapshots" "$report"
    rm -f "$report"
}

# Verify every encrypted archive against its external SHA-256 receipt.
mp_snapshot_verify_outer_all() {
    local report directory expected actual status failures=0
    report="$(mktemp "${MP_STATE}/snapshot-hashes.XXXXXX")"
    printf 'Encrypted archive SHA-256 verification\n\n' > "$report"
    while IFS= read -r directory; do
        [ -f "$directory/snapshot.tar.age" ] && [ -f "$directory/archive.sha256" ] || continue
        expected="$(awk 'NR == 1 {print $1}' "$directory/archive.sha256")"
        actual="$(sha256sum "$directory/snapshot.tar.age" | awk '{print $1}')"
        if [ -n "$expected" ] && [ "$expected" = "$actual" ]; then
            status="MATCH"
        else
            status="MISMATCH"
            failures=$((failures + 1))
        fi
        printf '%-10s %s\n' "$status" "$(basename "$directory")" >> "$report"
    done < <(find "$MP_SNAPSHOTS" -mindepth 1 -maxdepth 1 -type d ! -name '.*' | sort -r)
    ui_text_file "Snapshot archive hashes" "$report"
    rm -f "$report"
    if [ "$failures" -gt 0 ]; then
        mp_audit "snapshot.outer-verify" "failed" "mismatches:${failures}"
        return 1
    fi
    mp_audit "snapshot.outer-verify" "success" "all-archives"
}
