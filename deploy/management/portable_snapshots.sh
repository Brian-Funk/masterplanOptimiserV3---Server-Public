#!/usr/bin/env bash

# OS-independent transfer guidance for one-file encrypted snapshot packages.

MP_PORTABLE_TOOL="${MP_ROOT}/deploy/management/portable_snapshot.py"
MP_PORTABLE_EXPORTS="${MP_STATE}/portable-exports"
MP_PORTABLE_IMPORTS="${MP_STATE}/portable-imports"
MP_PORTABLE_EXPORT_INVENTORY="${MP_PORTABLE_EXPORT_INVENTORY:-$MP_STATE/portable-export-inventory}"
MP_PORTABLE_LAST_IMPORT_STATE="${MP_PORTABLE_LAST_IMPORT_STATE:-$MP_STATE/portable-last-import.json}"

mp_compliance_emit_backup_receipts() {
    local selected="$1"
    python3 "$MP_ROOT/deploy/management/compliance_receipts.py" \
        --requests "$MP_ROOT/runtime/compliance-requests" \
        --receipts "$MP_ROOT/runtime/compliance-receipts" \
        --snapshots "$MP_SNAPSHOTS" \
        --portable-inventory "$MP_PORTABLE_EXPORT_INVENTORY" \
        --snapshot-receipt "$selected/receipt.json" \
        --instance-key "$MP_ROOT/secrets/evidence_signing_key"
}

# Convert bounded receipt-emitter diagnostics into an actionable operator
# message without displaying raw paths or cryptographic material.
mp_compliance_error_message() {
    local error_file="$1" diagnostic
    diagnostic="$(head -c 512 "$error_file" 2>/dev/null || true)"
    case "$diagnostic" in
        "Superseded local snapshots remain."*)
            printf '%s\n' "Older local snapshots remain. Keep the newly verified replacement snapshot, delete every older local snapshot, then retry Deep verify."
            ;;
        "Compliance receipt signing failed"*)
            printf '%s\n' "The instance evidence key could not sign the recovery receipt. Check the protected evidence-key configuration, then retry Deep verify."
            ;;
        *)
            printf '%s\n' "The pending deletion recovery receipt could not be validated. Review the snapshot inventory and evidence configuration, then retry Deep verify."
            ;;
    esac
}

mp_portable_initialise() {
    mkdir -p "$MP_PORTABLE_EXPORTS" "$MP_PORTABLE_IMPORTS" "$MP_PORTABLE_EXPORT_INVENTORY" || return 1
    chmod 700 "$MP_PORTABLE_EXPORTS" "$MP_PORTABLE_IMPORTS" "$MP_PORTABLE_EXPORT_INVENTORY" || return 1
    find "$MP_PORTABLE_EXPORTS" "$MP_PORTABLE_IMPORTS" \
        -mindepth 1 -maxdepth 1 -type d -mmin +1440 -exec rm -rf -- {} + 2>/dev/null || true
}

mp_portable_transfer_style() {
    ui_menu "Workstation" "Which command style should MP-OPT generate?" \
        "windows-cmd" "Windows Command Prompt" \
        "windows-powershell" "Windows PowerShell" \
        "linux" "Linux shell" \
        "macos" "macOS shell" \
        "generic" "SFTP client or another operating system"
}

mp_portable_validate_host() {
    local value="$1"
    [ "${#value}" -ge 1 ] && [ "${#value}" -le 253 ] \
        && [[ "$value" =~ ^([A-Za-z0-9._-]+@)?[A-Za-z0-9][A-Za-z0-9._-]*$ ]]
}

mp_portable_validate_local_path() {
    local style="$1" value="$2"
    [ "${#value}" -ge 3 ] && [ "${#value}" -le 1024 ] || return 1
    [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || return 1
    case "$style" in
        windows-cmd)
            [[ "$value" =~ ^[A-Za-z]:[\\/].+ ]] \
                && [[ "$value" != *'"'* ]] \
                && [[ "$value" != *'%'* ]] \
                && [[ "$value" != *'!'* ]] \
                && [[ "$value" != *'&'* ]] \
                && [[ "$value" != *'|'* ]] \
                && [[ "$value" != *'<'* ]] \
                && [[ "$value" != *'>'* ]] \
                && [[ "$value" != *'^'* ]]
            ;;
        windows-powershell)
            [[ "$value" =~ ^[A-Za-z]:[\\/].+ ]] && [[ "$value" != *"'"* ]]
            ;;
        linux|macos)
            [[ "$value" == /* ]] && [[ "$value" != *"'"* ]]
            ;;
        generic) return 0 ;;
        *) return 1 ;;
    esac
}

mp_portable_transfer_inputs() {
    local style="$1" direction="$2"
    local host local_path prompt
    host="$(ui_input "Workstation transfer" "SSH host or alias used by this workstation")" || return 1
    mp_portable_validate_host "$host" || {
        ui_error "Use a plain SSH alias, hostname, or user@hostname without spaces or shell characters."
        return 1
    }
    if [ "$style" = generic ]; then
        local_path=""
    elif [ "$direction" = export ]; then
        # The package already has a collision-resistant, validated filename.
        # Download it into the workstation shell's current directory so the
        # operator never has to retype that long name or an absolute path.
        local_path="."
    else
        prompt="Absolute workstation path of the .mpopt-snapshot file"
        local_path="$(ui_input "Workstation transfer" "$prompt")" || return 1
        mp_portable_validate_local_path "$style" "$local_path" || {
            ui_error "Enter an absolute path valid for the selected shell. Shell metacharacters are not accepted."
            return 1
        }
    fi
    printf '%s\t%s\n' "$host" "$local_path"
}

mp_portable_write_commands() {
    local report="$1" style="$2" direction="$3" host="$4" local_path="$5" remote_path="$6" hash="${7:-}"
    local filename
    filename="$(basename "$remote_path")"
    if [ "$direction" = export ]; then
        [[ "$filename" =~ ^[0-9]{8}T[0-9]{6}Z_(database|secrets|full)_[A-Za-z0-9._-]{1,64}\.mpopt-snapshot$ ]] \
            || return 1
        [[ "$hash" =~ ^[0-9a-f]{64}$ ]] || return 1
    fi
    {
        printf 'MP-OPT portable snapshot transfer\n\n'
        printf 'This file contains public package metadata and an age-encrypted snapshot.\n'
        printf 'The private AGE-SECRET-KEY is not included.\n\n'
        if [ "$direction" = export ]; then
            printf 'Remote source: %s:%s\n' "$host" "$remote_path"
            printf 'Expected package SHA-256: %s\n\n' "$hash"
        else
            printf 'Remote upload destination: %s:%s\n\n' "$host" "$remote_path"
        fi
        case "$style:$direction" in
            windows-cmd:export)
                printf 'Open Windows Command Prompt in the destination folder, then paste this complete block:\n\n'
                printf 'scp "%s:%s" . && powershell.exe -NoProfile -Command "$Expected=\x27%s\x27; $Actual=(Get-FileHash -Algorithm SHA256 -LiteralPath \x27.\\%s\x27).Hash.ToLowerInvariant(); if ($Actual -ne $Expected) { Write-Error (\x27SHA-256 mismatch. Expected {0}, got {1}\x27 -f $Expected,$Actual); exit 1 }; Write-Host \x27MP-OPT SNAPSHOT VERIFIED\x27"\n' \
                    "$host" "$remote_path" "$hash" "$filename"
                ;;
            windows-cmd:import)
                printf 'Run in Windows Command Prompt:\n\nscp "%s" "%s:%s"\n' "$local_path" "$host" "$remote_path"
                ;;
            windows-powershell:export)
                printf 'Open Windows PowerShell in the destination folder, then paste this complete block:\n\n'
                printf "scp '%s:%s' .\n" "$host" "$remote_path"
                printf "if (\$LASTEXITCODE -ne 0) { throw 'SCP download failed.' }\n"
                printf "\$Expected = '%s'\n" "$hash"
                printf "\$Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath '.\\%s').Hash.ToLowerInvariant()\n" "$filename"
                printf 'if ($Actual -ne $Expected) { throw "SHA-256 mismatch. Expected $Expected, got $Actual" }\n'
                printf "Write-Host 'MP-OPT SNAPSHOT VERIFIED'\n"
                ;;
            windows-powershell:import)
                printf "Run in Windows PowerShell:\n\nscp '%s' '%s:%s'\n" "$local_path" "$host" "$remote_path"
                ;;
            linux:export)
                printf 'Open a Linux shell in the destination directory, then paste this complete block:\n\n'
                printf "scp '%s:%s' . && printf '%%s  %%s\\\\n' '%s' '%s' | sha256sum -c - >/dev/null && printf '%%s\\\\n' 'MP-OPT SNAPSHOT VERIFIED'\n" \
                    "$host" "$remote_path" "$hash" "$filename"
                ;;
            linux:import)
                printf "Run in the Linux shell:\n\nscp '%s' '%s:%s'\n" "$local_path" "$host" "$remote_path"
                ;;
            macos:export)
                printf 'Open a macOS shell in the destination directory, then paste this complete block:\n\n'
                printf "scp '%s:%s' . && actual=\"\$(shasum -a 256 '%s' | awk '{print \$1}')\" && if [ \"\$actual\" = '%s' ]; then printf '%%s\\\\n' 'MP-OPT SNAPSHOT VERIFIED'; else printf 'SHA-256 mismatch. Expected %%s, got %%s\\\\n' '%s' \"\$actual\" >&2; exit 1; fi\n" \
                    "$host" "$remote_path" "$filename" "$hash" "$hash"
                ;;
            macos:import)
                printf "Run in the macOS shell:\n\nscp '%s' '%s:%s'\n" "$local_path" "$host" "$remote_path"
                ;;
            generic:export)
                printf 'Use an SCP/SFTP client in binary mode to download the remote source.\n'
                printf 'Save it without changing its filename or contents, then calculate SHA-256 locally.\n'
                ;;
            generic:import)
                printf 'Use an SCP/SFTP client in binary mode to upload the portable file to the exact destination.\n'
                ;;
        esac
        if [ "$direction" = export ]; then
            if [ "$style" = generic ]; then
                printf '\nAccept the copy only when the complete 64-character local SHA-256 equals the expected value above.\n'
            else
                printf '\nThe command prints MP-OPT SNAPSHOT VERIFIED only after the download succeeds and the exact SHA-256 matches.\n'
            fi
        else
            printf '\nReturn to MP-OPT only after the upload has completed successfully.\n'
        fi
    } > "$report"
    chmod 600 "$report"
}

# Show public transfer commands in the ordinary SSH terminal rather than a
# dialog/whiptail textbox. This deliberately keeps the command selectable for
# copy/paste while MP-OPT waits. Snapshot data and private recovery identities
# are never written to this report.
mp_portable_show_copyable_commands() {
    local title="$1" report="$2" tty="${MP_PORTABLE_TTY:-/dev/tty}" body
    [ -f "$report" ] && [ ! -L "$report" ] || return 1
    body="$(sed -n '1,400p' "$report")" || return 1
    MP_COPYABLE_TTY="$tty" ui_copyable_terminal_text "$title" \
        "Open a second workstation terminal in the desired folder, copy the command block below, and run it there.

${body}" \
        "Return here after the workstation command finishes, then press Enter."
}

# Persist only public evidence that an operator downloaded one exact encrypted
# package and compared its workstation SHA-256 with the VPS value. The
# workstation path and private recovery identity are deliberately never kept.
mp_portable_record_confirmed_export() {
    local selected="$1" package_id="$2" package_hash="$3" package_size="$4"
    local snapshot_name snapshot_created_at archive_hash key_id receipt_tmp state_tmp inventory_tmp confirmed_at
    snapshot_name="$(basename "$selected")"
    [[ "$snapshot_name" =~ ^[0-9]{8}T[0-9]{6}Z_(database|secrets|full)_[A-Za-z0-9._-]{1,64}$ ]] || return 1
    [ "$(readlink -f "$(dirname "$selected")")" = "$(readlink -f "$MP_SNAPSHOTS")" ] || return 1
    [ -f "$selected/receipt.json" ] && [ ! -L "$selected/receipt.json" ] || return 1
    if declare -F mp_snapshot_receipt_is_v2 >/dev/null; then
        mp_snapshot_receipt_is_v2 "$selected" || return 1
    else
        jq -e '.format == "mp-opt-snapshot-receipt-v2"' "$selected/receipt.json" >/dev/null || return 1
    fi
    [[ "$package_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] || return 1
    [[ "$package_hash" =~ ^[0-9a-f]{64}$ ]] && [[ "$package_size" =~ ^[0-9]+$ ]] || return 1
    archive_hash="$(jq -er '.archive_sha256 | select(test("^[0-9a-f]{64}$"))' "$selected/receipt.json")" || return 1
    snapshot_created_at="$(jq -er '.created_at | select(type == "string")' "$selected/receipt.json")" || return 1
    key_id="$(jq -er '.encryption.recovery_key_id | select(type == "string" and length > 0)' "$selected/receipt.json")" || return 1
    [ "$(sha256sum "$selected/snapshot.tar.age" | awk '{print $1}')" = "$archive_hash" ] || return 1
    confirmed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    receipt_tmp="$(mktemp "${selected}/receipt.portable.XXXXXX")" || return 1
    jq --arg confirmed_at "$confirmed_at" --arg package_id "$package_id" --arg package_hash "$package_hash" \
        --arg archive_hash "$archive_hash" --arg key_id "$key_id" --argjson package_size "$package_size" '
        .storage = ((.storage // {}) + {
          portable: {
            state: "operator-sha256-confirmed",
            package_format: "mp-opt-portable-snapshot-2026-01",
            package_id: $package_id,
            confirmed_at: $confirmed_at,
            package_sha256: $package_hash,
            package_size: $package_size,
            archive_sha256: $archive_hash,
            recovery_key_id: $key_id
          }
        })
    ' "$selected/receipt.json" > "$receipt_tmp" || { rm -f "$receipt_tmp"; return 1; }
    chmod 600 "$receipt_tmp" && mv "$receipt_tmp" "$selected/receipt.json" || {
        rm -f "$receipt_tmp"
        return 1
    }

    mkdir -p "$MP_STATE" && chmod 700 "$MP_STATE" || return 1
    state_tmp="$(mktemp "${MP_MANUAL_EXPORT_STATE}.XXXXXX")" || return 1
    jq -n --arg snapshot "$snapshot_name" --arg confirmed_at "$confirmed_at" --arg package_id "$package_id" \
        --arg package_hash "$package_hash" --arg archive_hash "$archive_hash" --arg key_id "$key_id" \
        --argjson package_size "$package_size" '
        {
          format: "mp-opt-manual-recovery-export-v1",
          state: "operator-sha256-confirmed",
          snapshot: $snapshot,
          confirmed_at: $confirmed_at,
          package_format: "mp-opt-portable-snapshot-2026-01",
          package_id: $package_id,
          package_sha256: $package_hash,
          package_size: $package_size,
          archive_sha256: $archive_hash,
          recovery_key_id: $key_id
        }
    ' > "$state_tmp" || { rm -f "$state_tmp"; return 1; }
    chmod 600 "$state_tmp" && mv "$state_tmp" "$MP_MANUAL_EXPORT_STATE" || return 1
    inventory_tmp="$(mktemp "$MP_PORTABLE_EXPORT_INVENTORY/.${package_id}.XXXXXX")" || return 1
    jq -n --arg snapshot "$snapshot_name" --arg snapshot_created_at "$snapshot_created_at" \
        --arg confirmed_at "$confirmed_at" --arg package_id "$package_id" \
        --arg package_hash "$package_hash" --arg archive_hash "$archive_hash" \
        --arg key_id "$key_id" --argjson package_size "$package_size" '
        {
          format: "mp-opt-portable-export-inventory-v1",
          state: "operator-sha256-confirmed",
          snapshot: $snapshot,
          snapshot_created_at: $snapshot_created_at,
          confirmed_at: $confirmed_at,
          package_id: $package_id,
          package_sha256: $package_hash,
          package_size: $package_size,
          archive_sha256: $archive_hash,
          recovery_key_id: $key_id
        }
    ' > "$inventory_tmp" || { rm -f "$inventory_tmp"; return 1; }
    chmod 600 "$inventory_tmp" && mv "$inventory_tmp" "$MP_PORTABLE_EXPORT_INVENTORY/${package_id}.json" || {
        rm -f "$inventory_tmp"
        return 1
    }
    if declare -F mp_snapshot_publish_status >/dev/null; then
        mp_snapshot_publish_status || true
    fi
}

# Action-driven warning state. Manual mode intentionally has no periodic
# reminder, but a restore must not leave an older workstation copy looking
# like a fresh post-restore recovery point.
mp_portable_mark_export_required() {
    local reason="$1" temporary previous="{}"
    mp_validate_single_line "$reason" || return 1
    if [ -f "$MP_MANUAL_EXPORT_STATE" ]; then
        previous="$(jq -c 'if .state == "operator-sha256-confirmed" then {
            snapshot,confirmed_at,package_id,package_sha256,recovery_key_id
        } else (.previous_confirmed // {}) end' "$MP_MANUAL_EXPORT_STATE" 2>/dev/null || printf '{}')"
    fi
    temporary="$(mktemp "${MP_MANUAL_EXPORT_STATE}.XXXXXX")" || return 1
    jq -n --arg reason "$reason" --arg required_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        --argjson previous "$previous" '{
          format:"mp-opt-manual-recovery-export-v1",
          state:"fresh-export-required",
          reason:$reason,
          required_at:$required_at,
          previous_confirmed:$previous
        }' > "$temporary" || { rm -f "$temporary"; return 1; }
    chmod 600 "$temporary" && mv "$temporary" "$MP_MANUAL_EXPORT_STATE"
}

mp_snapshot_export_portable_interactive() {
    local selected style transfer host local_path ticket directory output result report package_id package_hash package_size
    local compliance_receipts="" compliance_note="" compliance_error_file compliance_error
    mp_require_commands python3 jq sha256sum || return 1
    mp_portable_initialise || return 1
    selected="$(mp_snapshot_select "Choose a v2 snapshot to export")" || return 1
    style="$(mp_portable_transfer_style)" || return 1
    transfer="$(mp_portable_transfer_inputs "$style" export "$(basename "$selected").mpopt-snapshot")" || return 1
    IFS=$'\t' read -r host local_path <<< "$transfer"
    ticket="$(cat /proc/sys/kernel/random/uuid)"
    directory="$MP_PORTABLE_EXPORTS/$ticket"
    mkdir -m 0700 "$directory" || return 1
    output="$directory/$(basename "$selected").mpopt-snapshot"
    mp_load_ha_config || { rm -rf "$directory"; return 1; }
    result="$(python3 "$MP_PORTABLE_TOOL" export --snapshot "$selected" --output "$output" \
        --source-node "${HA_NODE_ID:-standalone}")" || {
        rm -rf "$directory"
        ui_error "Portable export validation failed. No completed package was retained."
        return 1
    }
    package_id="$(jq -er '.package_id' <<< "$result")" || { rm -rf "$directory"; return 1; }
    package_hash="$(jq -er '.sha256' <<< "$result")" || { rm -rf "$directory"; return 1; }
    package_size="$(jq -er '.size' <<< "$result")" || { rm -rf "$directory"; return 1; }
    report="$(mktemp "$MP_STATE/portable-export.XXXXXX")" || { rm -rf "$directory"; return 1; }
    mp_portable_write_commands "$report" "$style" export "$host" "$local_path" "$output" "$package_hash" || {
        rm -f "$report"
        rm -rf "$directory"
        return 1
    }
    printf '\nPackage size: %s bytes\nTemporary export expires after 24 hours.\n' "$package_size" >> "$report"
    mp_portable_show_copyable_commands "Export portable snapshot" "$report" || {
        rm -f "$report"
        return 1
    }
    rm -f "$report"
    if { [ "$style" = generic ] \
        && ui_confirm "Portable snapshot" "Did the workstation transfer finish and does its complete SHA-256 exactly match?"; } \
        || { [ "$style" != generic ] \
        && ui_confirm "Portable snapshot" "Did the workstation command print exactly: MP-OPT SNAPSHOT VERIFIED?"; }; then
        if ! mp_portable_record_confirmed_export "$selected" "$package_id" "$package_hash" "$package_size"; then
            mp_audit "snapshot.portable-export" "failed" "$(basename "$selected"):confirmation-state"
            ui_error "The workstation copy exists, but MP-OPT could not record its protected confirmation receipt. The temporary VPS package was retained for retry."
            return 1
        fi
        rm -rf "$directory"
        mp_audit "snapshot.portable-export" "success" "$(basename "$selected"):sha256:${package_hash}"
        if declare -F mp_rotation_finalize_portable_export >/dev/null \
            && ! mp_rotation_finalize_portable_export "$selected" "$package_hash"; then
            ui_error "The workstation copy was recorded, but a matching pending recovery-key rotation could not be finalized. Open Configuration → Resume pending recovery-key rotation."
            return 1
        fi
        if declare -F mp_compliance_emit_backup_receipts >/dev/null; then
            compliance_error_file="$(mktemp "$MP_STATE/compliance-error.XXXXXX")" || return 1
            compliance_receipts="$(mp_compliance_emit_backup_receipts "$selected" 2>"$compliance_error_file")" || {
                compliance_error="$(mp_compliance_error_message "$compliance_error_file")"
                rm -f "$compliance_error_file"
                ui_error "The workstation export was verified, but pending deletion recovery receipts could not be recorded. The export remains valid.\n\n${compliance_error}"
                return 1
            }
            rm -f "$compliance_error_file"
            if [ -n "$compliance_receipts" ]; then
                compliance_note="\n\nPending deletion recovery receipts were recorded. The web page will detect them automatically."
            elif find "$MP_ROOT/runtime/compliance-requests" -maxdepth 1 -type f -name '*.json' -print -quit 2>/dev/null | grep -q .; then
                compliance_note="\n\nA deletion workflow is still waiting. Deep-verify this snapshot now; MP-OPT will then record the recovery receipt."
            fi
        fi
        ui_message "Portable snapshot exported" \
            "The workstation copy was operator-confirmed and its public recovery receipt was recorded.\n\nPackage ID: ${package_id}\nPackage SHA-256: ${package_hash}\n\nThe temporary VPS export was removed.${compliance_note}"
    else
        mp_audit "snapshot.portable-export" "pending" "$(basename "$selected"):sha256:${package_hash}"
        ui_message "Portable export retained" "The protected VPS copy remains for 24 hours so the transfer can be retried."
    fi
}

mp_snapshot_import_portable_interactive() {
    local style transfer host local_path ticket directory upload report expected result status name package_hash
    mp_require_commands python3 jq sha256sum || return 1
    mp_portable_initialise || return 1
    style="$(mp_portable_transfer_style)" || return 1
    transfer="$(mp_portable_transfer_inputs "$style" import "")" || return 1
    IFS=$'\t' read -r host local_path <<< "$transfer"
    ticket="$(cat /proc/sys/kernel/random/uuid)"
    directory="$MP_PORTABLE_IMPORTS/$ticket"
    mkdir -m 0700 "$directory" || return 1
    upload="$directory/upload.partial"
    report="$(mktemp "$MP_STATE/portable-import.XXXXXX")" || { rm -rf "$directory"; return 1; }
    mp_portable_write_commands "$report" "$style" import "$host" "$local_path" "$upload" "" || {
        rm -f "$report"
        rm -rf "$directory"
        return 1
    }
    printf '\nThe upload ticket expires after 24 hours.\n' >> "$report"
    mp_portable_show_copyable_commands "Import portable snapshot" "$report" || {
        rm -f "$report"
        rm -rf "$directory"
        return 1
    }
    rm -f "$report"
    if ! ui_confirm "Portable snapshot" "Has the workstation upload completed successfully?"; then
        rm -rf "$directory"
        ui_message "Import cancelled" "No package was imported. Run Import portable snapshot again when the workstation file is ready."
        return 75
    fi
    [ -f "$upload" ] && [ ! -L "$upload" ] || {
        rm -rf "$directory"
        ui_error "The uploaded portable file was not found as a regular file."
        return 1
    }
    chmod 600 "$upload" || { rm -rf "$directory"; return 1; }
    expected="$(ui_input "Portable snapshot" "Original 64-character package SHA-256; leave blank if unavailable")" || {
        rm -rf "$directory"; return 1;
    }
    expected="$(printf '%s' "$expected" | tr 'A-F' 'a-f' | tr -d '[:space:]')"
    if [ -n "$expected" ] && ! [[ "$expected" =~ ^[0-9a-f]{64}$ ]]; then
        rm -rf "$directory"
        ui_error "The expected SHA-256 must be blank or exactly 64 hexadecimal characters."
        return 1
    fi
    result="$(python3 "$MP_PORTABLE_TOOL" import --package "$upload" --snapshots "$MP_SNAPSHOTS" \
        --expected-sha256 "$expected")" || {
        rm -rf "$directory"
        mp_audit "snapshot.portable-import" "failed" "package-validation"
        ui_error "Portable import failed its safety or integrity checks. No snapshot was installed."
        return 1
    }
    status="$(jq -er '.status' <<< "$result")" || { rm -rf "$directory"; return 1; }
    name="$(jq -er '.snapshot_directory' <<< "$result")" || { rm -rf "$directory"; return 1; }
    package_hash="$(jq -er '.package_sha256' <<< "$result")" || { rm -rf "$directory"; return 1; }
    rm -rf "$directory"
    local imported_path state_tmp
    imported_path="$MP_SNAPSHOTS/$name"
    [ -d "$imported_path" ] || return 1
    state_tmp="$(mktemp "$MP_STATE/portable-last-import.XXXXXX")" || return 1
    jq -n --arg snapshot "$name" --arg path "$imported_path" \
        --arg package_hash "$package_hash" --arg status "$status" \
        --arg imported_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '{format:"mp-opt-portable-import-receipt-v1",snapshot:$snapshot,
          snapshot_path:$path,package_sha256:$package_hash,status:$status,
          imported_at:$imported_at}' > "$state_tmp" \
        || { rm -f "$state_tmp"; return 1; }
    chmod 600 "$state_tmp" && mv "$state_tmp" "$MP_PORTABLE_LAST_IMPORT_STATE" \
        || { rm -f "$state_tmp"; return 1; }
    mp_audit "snapshot.portable-import" "success" "${name}:sha256:${package_hash}:${status}"
    if declare -F mp_snapshot_publish_status >/dev/null; then
        mp_snapshot_publish_status || true
    fi
    ui_message "Portable snapshot imported" \
        "${name}\n\nPackage SHA-256:\n${package_hash}\n\nRun Deep verify with the matching recovery identity before restoring it."
}
