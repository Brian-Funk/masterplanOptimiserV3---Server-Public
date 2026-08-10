#!/usr/bin/env bash
: "${BASH_VERSION:?This management interface requires Bash.}"

# Shared terminal, validation and service helpers for MP-OPT_SERVER.

MP_ROOT="${MP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
MP_HOME="${MP_HOME:-${HOME}/.config/mp-opt-server}"
MP_STATE="${MP_STATE:-${HOME}/.local/state/mp-opt-server}"
MP_SNAPSHOTS="${MP_SNAPSHOTS:-${HOME}/masterplan-snapshots}"
MP_RECIPIENT_FILE="${MP_RECIPIENT_FILE:-${MP_HOME}/recovery-recipient}"
MP_ARCHIVE_TARGET_FILE="${MP_ARCHIVE_TARGET_FILE:-${MP_HOME}/archive-ssh-target}"
MP_RECOVERY_STORAGE_MODE_FILE="${MP_RECOVERY_STORAGE_MODE_FILE:-${MP_HOME}/recovery-storage-mode}"
MP_MANUAL_EXPORT_STATE="${MP_MANUAL_EXPORT_STATE:-${MP_STATE}/manual-recovery-export.json}"
MP_HA_SNAPSHOT_STATUS="${MP_HA_SNAPSHOT_STATUS:-${MP_ROOT}/runtime/ha-snapshot-status.json}"
MP_AUDIT_FILE="${MP_AUDIT_FILE:-${MP_STATE}/management.log}"
MP_STORAGE_CHECKLIST_FILE="${MP_STORAGE_CHECKLIST_FILE:-${MP_HOME}/storage-security-checklist.json}"
MP_LOCK_FILE="${MP_LOCK_FILE:-${MP_STATE}/management.lock}"
MP_EVIDENCE_HOME="${MP_EVIDENCE_HOME:-${MP_ROOT}/state/evidence}"
MP_UI_SIZE_FILE="${MP_UI_SIZE_FILE:-${MP_HOME}/interface-size}"
MP_DEPLOYMENT_POLICY_FILE="${MP_DEPLOYMENT_POLICY_FILE:-/etc/mp-opt/deployment-policy}"
MP_TEST_DEPLOYMENT_ENV="${MP_TEST_DEPLOYMENT_ENV:-${MP_ROOT}/.test-deployment.env}"
MP_HOST_CADDYFILE="${MP_HOST_CADDYFILE:-/etc/caddy/Caddyfile}"
MP_HA_HOME="${MP_HA_HOME:-/etc/mp-opt-ha}"
MP_HA_CONFIG="${MP_HA_CONFIG:-${MP_HA_HOME}/node.env}"
MP_COPYRIGHT_YEAR="2026"
MP_TUI="${MP_TUI:-auto}"
MP_UI_SIZE="${MP_UI_SIZE:-}"
MP_TUI_BACKTITLE="MP-OPT_SERVER | Brian Funk | Copyright © ${MP_COPYRIGHT_YEAR} Brian Funk"

declare -a MP_COMPOSE=()

# Load only the documented, non-secret HA keys without evaluating shell code.
mp_load_ha_config() {
    local key value
    export HA_MODE=standalone HA_ROLE=standalone HA_NODE_ID=standalone
    export HA_CLUSTER_ID= HA_GENERATION=0 HA_HEARTBEAT_INTERVAL_SECONDS=15
    export HA_PEER_NODE_ID= HA_PEER_SSH= HA_ARCHIVE_SSH_TARGET= HA_ALERT_EMAIL=
    export HA_RECOVERY_STORAGE_MODE=
    export HA_AUTOMATIC_FAILOVER=disabled HA_WITNESS_URL=
    export HA_MAX_REPLICATION_BUNDLE_BYTES=2147483648
    if [ -f "$MP_HA_CONFIG" ]; then
        while IFS='=' read -r key value || [ -n "$key" ]; do
            case "$key" in
                HA_MODE|HA_ROLE|HA_NODE_ID|HA_CLUSTER_ID|HA_GENERATION|HA_HEARTBEAT_INTERVAL_SECONDS|HA_PEER_NODE_ID|HA_PEER_SSH|HA_ARCHIVE_SSH_TARGET|HA_RECOVERY_STORAGE_MODE|HA_ALERT_EMAIL|HA_AUTOMATIC_FAILOVER|HA_WITNESS_URL|HA_MAX_REPLICATION_BUNDLE_BYTES)
                    mp_validate_single_line "$value" || return 1
                    printf -v "$key" '%s' "$value"
                    export "$key"
                    ;;
                ''|'#'*) ;;
                *) printf 'Unsupported HA configuration key: %s\n' "$key" >&2; return 1 ;;
            esac
        done < "$MP_HA_CONFIG"
    fi
    if [ -z "$HA_ARCHIVE_SSH_TARGET" ] && [ -s "$MP_ARCHIVE_TARGET_FILE" ]; then
        value="$(tr -d '\r\n' < "$MP_ARCHIVE_TARGET_FILE")"
        [[ "$value" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$ ]] || return 1
        HA_ARCHIVE_SSH_TARGET="$value"
        export HA_ARCHIVE_SSH_TARGET
    fi
    if [ -z "$HA_RECOVERY_STORAGE_MODE" ] && [ -s "$MP_RECOVERY_STORAGE_MODE_FILE" ]; then
        value="$(tr -d '\r\n' < "$MP_RECOVERY_STORAGE_MODE_FILE")"
        case "$value" in manual_portable|ssh_archive) ;; *) return 1 ;; esac
        HA_RECOVERY_STORAGE_MODE="$value"
    fi
    if [ -z "$HA_RECOVERY_STORAGE_MODE" ]; then
        if [ -n "$HA_ARCHIVE_SSH_TARGET" ]; then
            HA_RECOVERY_STORAGE_MODE=ssh_archive
        else
            HA_RECOVERY_STORAGE_MODE=manual_portable
        fi
    fi
    case "$HA_RECOVERY_STORAGE_MODE" in
        manual_portable) ;;
        ssh_archive) [ -n "$HA_ARCHIVE_SSH_TARGET" ] || return 1 ;;
        *) return 1 ;;
    esac
    export HA_RECOVERY_STORAGE_MODE
}

# Return the explicit recovery-copy policy. Legacy installations are inferred
# safely by mp_load_ha_config and become explicit the next time they are saved.
mp_recovery_storage_mode() {
    mp_load_ha_config >/dev/null || return 1
    printf '%s\n' "$HA_RECOVERY_STORAGE_MODE"
}

# Print the current node role, defaulting safely to standalone.
mp_ha_role() {
    mp_load_ha_config >/dev/null || return 1
    printf '%s\n' "${HA_ROLE:-standalone}"
}

# Return the immutable application release identity used by HA compatibility
# checks. Signed production releases record their source commit explicitly;
# source checkouts retain the Git commit as a development fallback.
mp_release_hash() {
    local value=""
    if [ -f "$MP_TEST_DEPLOYMENT_ENV" ]; then
        [ "$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || true)" = test ] \
            || { printf 'Unsigned deployment override exists on a non-test installation.\n' >&2; return 1; }
        value="$(sed -n 's/^MP_TEST_COMMIT=//p' "$MP_TEST_DEPLOYMENT_ENV" | head -1)"
    elif [ -f "$MP_ROOT/.release.env" ]; then
        value="$(sed -n 's/^MP_RELEASE_COMMIT=//p' "$MP_ROOT/.release.env" | head -1)"
    else
        value="$(git -C "$MP_ROOT" rev-parse HEAD 2>/dev/null || true)"
    fi
    [[ "$value" =~ ^[0-9a-f]{40}$|^[0-9a-f]{64}$ ]] || return 1
    printf '%s\n' "$value"
}

# Refuse an operation which must never run on a replica.
mp_require_active_or_standalone() {
    local role permit generation
    # Load into this shell. Calling mp_ha_role through command substitution
    # loads HA_NODE_ID only in a subshell, causing a real lease holder to be
    # compared against an empty/stale node id.
    mp_load_ha_config >/dev/null || return 1
    role="${HA_ROLE:-standalone}"
    if [ "$role" = "dynamic" ]; then
        [ "$(jq -r '.holder_node_id // empty' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null)" = "$HA_NODE_ID" ] \
            || { ui_error "This operation is available only on the current lease holder."; return 1; }
        generation="$(jq -r '.generation // 0' "$MP_ROOT/runtime/ha-control.json")"
        permit="$(python3 "$MP_ROOT/deploy/ha/witness_control.py" permit 2>/dev/null)" \
            || { ui_error "The external writer lease cannot be verified. The operation was stopped."; return 1; }
        jq -e --arg node "$HA_NODE_ID" --argjson generation "$generation" \
            '.allowed == true and .holder_node_id == $node and .generation == $generation' \
            <<< "$permit" >/dev/null \
            || { ui_error "The external writer lease generation changed. The operation was stopped."; return 1; }
    fi
}

# Long-running recovery operations stop or replace the local database. They
# are safe only while the witness is explicitly prevented from moving the
# lease to the peer mid-operation.
mp_require_ha_maintenance_window() {
    mp_require_active_or_standalone || return 1
    if [ "${HA_ROLE:-standalone}" = "dynamic" ] \
        && [ "$(jq -r '.automatic_failover' "$MP_ROOT/runtime/ha-control.json" 2>/dev/null)" != "false" ]; then
        ui_error "Disable automatic failover before this recovery operation, then try again."
        return 1
    fi
}

# Queue one complete copy after an operator mutation of shared state. The
# scheduler safely ignores the request unless this node still holds the lease.
mp_queue_ha_replication() {
    local reason="${1:-operator-change}" job temporary
    [ "$(mp_ha_role 2>/dev/null || printf standalone)" = "dynamic" ] || return 0
    [[ "$reason" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || return 1
    job="$(cat /proc/sys/kernel/random/uuid)" || return 1
    mkdir -p "$MP_ROOT/runtime/ha-requests" || return 1
    temporary="$MP_ROOT/runtime/ha-requests/.${job}.tmp"
    printf '{"format":"mp-opt-replication-request-v1","job_id":"%s","reason":"%s","created_at":"%s"}\n' \
        "$job" "$reason" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$temporary" || return 1
    chmod 644 "$temporary"
    mv "$temporary" "$MP_ROOT/runtime/ha-requests/${job}.json"
}

# Initialise protected operator-owned working directories.
mp_initialise_paths() {
    umask 077
    mkdir -p "$MP_HOME" "$MP_STATE" "$MP_SNAPSHOTS"
    chmod 700 "$MP_HOME" "$MP_STATE" "$MP_SNAPSHOTS"
    touch "$MP_AUDIT_FILE" "$MP_LOCK_FILE"
    chmod 600 "$MP_AUDIT_FILE" "$MP_LOCK_FILE"
}

# Return whether this process still has an interactive controlling terminal.
mp_has_terminal() {
    [ -t 0 ] || [ -t 1 ] || [ -t 2 ]
}

# Print the selected full-screen interface backend.
mp_tui_backend() {
    if [ "$MP_TUI" = "ansi" ] || ! mp_has_terminal; then
        printf 'ansi\n'
    elif [ "$MP_TUI" = "dialog" ] && command -v dialog >/dev/null 2>&1; then
        printf 'dialog\n'
    elif [ "$MP_TUI" = "whiptail" ] && command -v whiptail >/dev/null 2>&1; then
        printf 'whiptail\n'
    elif [ "$MP_TUI" = "auto" ] && command -v dialog >/dev/null 2>&1; then
        printf 'dialog\n'
    elif command -v whiptail >/dev/null 2>&1; then
        printf 'whiptail\n'
    else
        printf 'ansi\n'
    fi
}

# Return whether a full-screen interface can use the controlling terminal.
mp_has_tui() {
    [ "$(mp_tui_backend)" != "ansi" ]
}

# Refuse to run the management dashboard without an interactive terminal.
mp_require_interactive_terminal() {
    if ! mp_has_terminal; then
        printf 'MP-OPT_SERVER requires an interactive terminal. Connect through SSH and run mp-opt directly.\n' >&2
        return 1
    fi
}

# Print the validated interface-size profile, defaulting to a spacious layout.
mp_ui_size_profile() {
    local profile="$MP_UI_SIZE"
    if [ -z "$profile" ] && [ -f "$MP_UI_SIZE_FILE" ]; then
        IFS= read -r profile < "$MP_UI_SIZE_FILE" || true
    fi
    case "$profile" in
        compact|standard|large|maximum) printf '%s\n' "$profile" ;;
        *) printf 'large\n' ;;
    esac
}

# Print the current terminal height and width with safe non-TTY fallbacks.
mp_terminal_dimensions() {
    local dimensions rows columns
    dimensions=""
    if mp_has_terminal; then
        dimensions="$(stty size 2>/dev/null || true)"
    fi
    read -r rows columns <<< "$dimensions"
    [[ "$rows" =~ ^[0-9]+$ ]] && [ "$rows" -gt 0 ] || rows="${LINES:-30}"
    [[ "$columns" =~ ^[0-9]+$ ]] && [ "$columns" -gt 0 ] || columns="${COLUMNS:-120}"
    [[ "$rows" =~ ^[0-9]+$ ]] && [ "$rows" -ge 8 ] || rows=30
    [[ "$columns" =~ ^[0-9]+$ ]] && [ "$columns" -ge 20 ] || columns=120
    printf '%s %s\n' "$rows" "$columns"
}

# Print height, width and menu-height values for one type of interface window.
mp_ui_geometry() {
    local role="$1"
    local terminal_rows terminal_columns profile height width menu_height
    local max_height max_width min_height min_width
    read -r terminal_rows terminal_columns < <(mp_terminal_dimensions)
    profile="$(mp_ui_size_profile)"
    max_height=$((terminal_rows - 2))
    max_width=$((terminal_columns - 4))
    min_height=10
    min_width=46
    [ "$min_height" -le "$max_height" ] || min_height="$max_height"
    [ "$min_width" -le "$max_width" ] || min_width="$max_width"

    case "$profile" in
        compact)
            case "$role" in
                menu) height=24; width=86 ;;
                view) height=28; width=110 ;;
                prompt) height=18; width=78 ;;
                input) height=12; width=78 ;;
                info) height=10; width=72 ;;
                *) height=24; width=86 ;;
            esac
            ;;
        standard)
            height=$((terminal_rows * 70 / 100))
            width=$((terminal_columns * 78 / 100))
            ;;
        large)
            height=$((terminal_rows * 86 / 100))
            width=$((terminal_columns * 90 / 100))
            ;;
        maximum)
            height="$max_height"
            width="$max_width"
            ;;
    esac

    if [ "$profile" != "compact" ]; then
        case "$role" in
            prompt) [ "$height" -le 22 ] || height=22 ;;
            input) [ "$height" -le 14 ] || height=14 ;;
            info) [ "$height" -le 12 ] || height=12 ;;
        esac
    fi
    [ "$height" -le "$max_height" ] || height="$max_height"
    [ "$width" -le "$max_width" ] || width="$max_width"
    [ "$height" -ge "$min_height" ] || height="$min_height"
    [ "$width" -ge "$min_width" ] || width="$min_width"
    menu_height=$((height - 8))
    [ "$menu_height" -ge 1 ] || menu_height=1
    printf '%s %s %s\n' "$height" "$width" "$menu_height"
}

# Persist the operator's preferred interface-size profile.
mp_configure_interface_size() {
    local current selected temporary
    current="$(mp_ui_size_profile)"
    selected="$(ui_menu "Interface size" "Current profile: ${current}. Choose a new window size." \
        "compact" "Compact - fixed smaller windows" \
        "standard" "Standard - approximately three quarters of the terminal" \
        "large" "Large - spacious and terminal-aware (recommended)" \
        "maximum" "Maximum - almost the entire terminal" \
        "back" "Keep the current profile")" || return 0
    [ "$selected" != "back" ] && [ -n "$selected" ] || return 0
    case "$selected" in
        compact|standard|large|maximum) ;;
        *) ui_error "That interface-size profile is not available."; return 1 ;;
    esac
    temporary="$(mktemp "${MP_HOME}/interface-size.XXXXXX")" || return 1
    chmod 600 "$temporary" || { rm -f "$temporary"; return 1; }
    printf '%s\n' "$selected" > "$temporary"
    mv "$temporary" "$MP_UI_SIZE_FILE"
    chmod 600 "$MP_UI_SIZE_FILE"
    mp_audit "interface.size" "success" "$selected"
    ui_message "Interface size" "The ${selected} profile is active. New windows will use it immediately."
}

# Display a calm informational message.
ui_message() {
    local title="$1"
    local message="$2"
    local height width unused
    read -r height width unused < <(mp_ui_geometry prompt)
    case "$(mp_tui_backend)" in
        dialog)
            dialog --backtitle "$MP_TUI_BACKTITLE" --title "$title" \
                --ok-label "Return" --msgbox "$message" "$height" "$width" \
                </dev/tty >/dev/tty 2>/dev/tty
            ;;
        whiptail)
            whiptail --backtitle "$MP_TUI_BACKTITLE" --title "$title" \
                --ok-button "Return" --msgbox "$message" "$height" "$width" \
                </dev/tty >/dev/tty 2>/dev/tty
            ;;
        *) printf '\n[%s]\n%s\n' "$title" "$message" >&2 ;;
    esac
}

# Present an informational checkpoint whose acknowledgement advances the
# current workflow. Keep this distinct from ui_message, whose Return label is
# appropriate when an action is finished and control goes back to a menu.
ui_continue_message() {
    local title="$1"
    local message="$2"
    local height width unused
    read -r height width unused < <(mp_ui_geometry prompt)
    case "$(mp_tui_backend)" in
        dialog)
            dialog --backtitle "$MP_TUI_BACKTITLE" --title "$title" \
                --ok-label "Continue" --msgbox "$message" "$height" "$width" \
                </dev/tty >/dev/tty 2>/dev/tty
            ;;
        whiptail)
            whiptail --backtitle "$MP_TUI_BACKTITLE" --title "$title" \
                --ok-button "Continue" --msgbox "$message" "$height" "$width" \
                </dev/tty >/dev/tty 2>/dev/tty
            ;;
        *) printf '\n[%s]\n%s\n' "$title" "$message" >&2 ;;
    esac
}

# Show selectable ordinary terminal text outside dialog/whiptail. This is used
# for short-lived values that an operator must copy without persisting them in
# a report file. Clear before presentation and again on return so the terminal
# never mixes full-screen TUI remnants with the selectable content.
ui_copyable_terminal_text() {
    local title="$1"
    local body="$2"
    local guidance="${3:-Copy the text you need, then press Enter to return.}"
    local tty="${MP_COPYABLE_TTY:-/dev/tty}"
    local ignored status=0 tty_fd clear_sequence
    clear_sequence=$'\033[2J\033[3J\033[H'
    # Management entry points require an interactive terminal, but unit tests
    # and guarded automation may invoke an individual action without one.
    # Refuse to acknowledge the handoff in that case: callers must not mark a
    # copy checkpoint complete when nobody could have seen it. The value is
    # deliberately never redirected to stdout or stderr.
    if [ "$tty" = /dev/tty ] && ! mp_has_terminal; then
        return 1
    fi
    exec {tty_fd}<>"$tty" || return 1
    {
        printf '%s' "$clear_sequence" &&
        printf '\n============================================================\n' &&
        printf '%s\n' "$title" &&
        printf '============================================================\n\n' &&
        printf 'This is normal selectable terminal text.\n\n' &&
        printf '%s\n' '----- COPY FROM HERE -----' &&
        printf '%s\n' "$body" &&
        printf '%s\n' '----- END COPYABLE TEXT -----' &&
        printf '\n============================================================\n' &&
        printf '%s\n' "$guidance"
    } >&"$tty_fd" || status=1
    if [ "$status" -eq 0 ]; then
        IFS= read -r ignored <&"$tty_fd" || status=1
    fi
    # CSI 3 J asks supporting terminals to remove scrollback as well as the
    # visible page, so a bootstrap or join code does not remain behind after
    # the operator returns to the full-screen interface.
    printf '%s' "$clear_sequence" >&"$tty_fd" || status=1
    exec {tty_fd}>&-
    return "$status"
}

# Display an error without exposing command output or secret values.
ui_error() {
    ui_message "Operation stopped" "$1"
}

# Ask a yes/no question, returning success only for yes.
ui_confirm() {
    local title="$1"
    local message="$2"
    local answer
    local height width unused
    read -r height width unused < <(mp_ui_geometry prompt)
    case "$(mp_tui_backend)" in
        dialog)
            dialog --backtitle "$MP_TUI_BACKTITLE" --title "$title" \
                --yes-label "Yes" --no-label "No" --yesno "$message" "$height" "$width" \
                </dev/tty >/dev/tty 2>/dev/tty
            ;;
        whiptail)
            whiptail --backtitle "$MP_TUI_BACKTITLE" --title "$title" \
                --yes-button "Yes" --no-button "No" --yesno "$message" "$height" "$width" \
                </dev/tty >/dev/tty 2>/dev/tty
            ;;
        *)
            printf '\n[%s]\n%s [y/N]: ' "$title" "$message" >&2
            read -r answer </dev/tty
            [[ "$answer" =~ ^[Yy]$ ]]
            ;;
    esac
}

# Read one visible value and print only the entered result to stdout.
ui_input() {
    local title="$1"
    local prompt="$2"
    local default_value="${3:-}"
    local value
    local height width unused
    read -r height width unused < <(mp_ui_geometry input)
    case "$(mp_tui_backend)" in
        dialog)
            dialog --backtitle "$MP_TUI_BACKTITLE" --title "$title" --stdout \
                --inputbox "$prompt" "$height" "$width" "$default_value" \
                </dev/tty 2>/dev/tty
            ;;
        whiptail)
            whiptail --backtitle "$MP_TUI_BACKTITLE" --title "$title" \
                --inputbox "$prompt" "$height" "$width" "$default_value" \
                3>&1 1>/dev/tty 2>&3 </dev/tty
            ;;
        *)
            printf '\n[%s]\n%s' "$title" "$prompt" >&2
            if [ -n "$default_value" ]; then
                printf ' [%s]' "$default_value" >&2
            fi
            printf ': ' >&2
            read -r value </dev/tty
            printf '%s\n' "${value:-$default_value}"
            ;;
    esac
}

# Read one secret value without terminal echo and print it to stdout.
ui_password() {
    local title="$1"
    local prompt="$2"
    local value
    local height width unused
    read -r height width unused < <(mp_ui_geometry input)
    case "$(mp_tui_backend)" in
        dialog)
            dialog --backtitle "$MP_TUI_BACKTITLE" --title "$title" --stdout \
                --insecure --passwordbox "$prompt" "$height" "$width" </dev/tty 2>/dev/tty
            ;;
        whiptail)
            whiptail --backtitle "$MP_TUI_BACKTITLE" --title "$title" \
                --passwordbox "$prompt" "$height" "$width" 3>&1 1>/dev/tty 2>&3 </dev/tty
            ;;
        *)
            printf '\n[%s]\n%s: ' "$title" "$prompt" >&2
            read -r -s value </dev/tty
            printf '\n' >&2
            printf '%s\n' "$value"
            ;;
    esac
}

# Display a menu from tag/description pairs and print the selected tag.
ui_menu() {
    local title="$1"
    local prompt="$2"
    shift 2
    local -a values=("$@")
    local index=0
    local selected
    local cancel_label="${MP_MENU_CANCEL_LABEL:-Back}"
    local height width menu_height
    read -r height width menu_height < <(mp_ui_geometry menu)
    case "$(mp_tui_backend)" in
        dialog)
            dialog --backtitle "$MP_TUI_BACKTITLE" --title "$title" --stdout \
                --cancel-label "$cancel_label" --menu "$prompt" "$height" "$width" "$menu_height" "$@" \
                </dev/tty 2>/dev/tty
            ;;
        whiptail)
            whiptail --backtitle "$MP_TUI_BACKTITLE" --title "$title" \
                --cancel-button "$cancel_label" --menu "$prompt" "$height" "$width" "$menu_height" "$@" \
                3>&1 1>/dev/tty 2>&3 </dev/tty
            ;;
        *)
            printf '\n[%s]\n%s\n\n' "$title" "$prompt" >&2
            while [ "$index" -lt "${#values[@]}" ]; do
                printf '  %s) %s\n' "${values[$index]}" "${values[$((index + 1))]}" >&2
                index=$((index + 2))
            done
            printf '\nSelection: ' >&2
            read -r selected </dev/tty
            printf '%s\n' "$selected"
            ;;
    esac
}

# Show a text file in the graphical viewer or a terminal pager.
ui_text_file() {
    local title="$1"
    local file="$2"
    local height width unused
    read -r height width unused < <(mp_ui_geometry view)
    case "$(mp_tui_backend)" in
        dialog)
            dialog --backtitle "$MP_TUI_BACKTITLE" --title "$title" \
                --exit-label "Return" --scrollbar --textbox "$file" "$height" "$width" \
                </dev/tty >/dev/tty 2>/dev/tty
            ;;
        whiptail)
            whiptail --backtitle "$MP_TUI_BACKTITLE" --title "$title" \
                --ok-button "Return" --scrolltext --textbox "$file" "$height" "$width" \
                </dev/tty >/dev/tty 2>/dev/tty
            ;;
        *)
            if mp_has_terminal && command -v less >/dev/null 2>&1; then
                less -R "$file" </dev/tty >/dev/tty
            else
                sed -n '1,400p' "$file"
            fi
            ;;
    esac
}

# Display a file while it grows, returning cleanly when the viewer is closed.
ui_live_text_file() {
    local title="$1"
    local file="$2"
    local height width unused
    read -r height width unused < <(mp_ui_geometry view)
    case "$(mp_tui_backend)" in
        dialog)
            dialog --backtitle "$MP_TUI_BACKTITLE" --title "$title" \
                --exit-label "Return" --tailbox "$file" "$height" "$width" \
                </dev/tty >/dev/tty 2>/dev/tty
            ;;
        *)
            printf '\n%s\nPress Ctrl+C to return.\n\n' "$title" >/dev/tty
            tail -n +1 -f "$file" </dev/tty >/dev/tty 2>/dev/tty
            ;;
    esac
}

# Remove terminal control characters without buffering live command output.
mp_sanitise_terminal_stream() {
    stdbuf -oL sed -E $'s/\033\\[[0-9;?]*[ -/]*[@-~]//g' \
        | stdbuf -oL tr -d '\000-\010\013\014\016-\037\177'
}

# Run a long command inside the preferred TUI output window.
ui_clear_terminal() {
    if mp_has_terminal; then
        printf '\033[2J\033[3J\033[H' >/dev/tty 2>/dev/null || true
    else
        clear 2>/dev/null || true
    fi
}

ui_run_command() {
    local title="$1"
    local message="$2"
    shift 2
    local report status
    local height width unused info_height info_width
    read -r height width unused < <(mp_ui_geometry view)
    read -r info_height info_width unused < <(mp_ui_geometry info)
    report="$(mktemp "${MP_STATE}/command-output.XXXXXX")" || return 1
    chmod 600 "$report" || { rm -f "$report"; return 1; }
    ui_clear_terminal
    status=0
    case "$(mp_tui_backend)" in
        dialog)
            set +e
            "$@" 2>&1 \
                | mp_sanitise_terminal_stream \
                | tee "$report" \
                | dialog --backtitle "$MP_TUI_BACKTITLE" --title "$title" \
                    --exit-label "Return" --programbox "$message" "$height" "$width" \
                    >/dev/tty 2>/dev/tty
            status="${PIPESTATUS[0]}"
            set -e
            ;;
        whiptail)
            whiptail --backtitle "$MP_TUI_BACKTITLE" --title "$title" \
                --infobox "$message\n\nPlease wait..." "$info_height" "$info_width" \
                </dev/tty >/dev/tty 2>/dev/tty || true
            "$@" > "$report" 2>&1 || status=$?
            local safe
            safe="$(mktemp "${MP_STATE}/command-output.safe.XXXXXX")" || {
                rm -f "$report"
                return 1
            }
            chmod 600 "$safe" || { rm -f "$report" "$safe"; return 1; }
            mp_sanitise_terminal_stream < "$report" > "$safe" || {
                rm -f "$report" "$safe"
                return 1
            }
            mv "$safe" "$report" || { rm -f "$report" "$safe"; return 1; }
            ui_text_file "$title" "$report" || true
            ;;
        *)
            set +e
            "$@" 2>&1 | mp_sanitise_terminal_stream | tee "$report"
            status="${PIPESTATUS[0]}"
            set -e
            ;;
    esac
    if [ "$status" -ne 0 ]; then
        [ -s "$report" ] || printf 'The command failed without producing output.\n' > "$report"
        ui_text_file "${title} - failed" "$report" || true
    fi
    rm -f "$report"
    return "$status"
}

# Require an exact confirmation phrase for a destructive operation.
ui_require_phrase() {
    local title="$1"
    local warning="$2"
    local phrase="$3"
    local entered
    entered="$(ui_input "$title" "$warning\n\nType exactly: $phrase")" || return 1
    [ "$entered" = "$phrase" ]
}

# Print the branded launch screen and non-sensitive runtime context.
mp_banner() {
    local domain="not configured"
    local commit="unknown"
    local health="not checked"
    [ -f "$MP_ROOT/.env" ] && domain="$(mp_env_get DOMAIN "$MP_ROOT/.env" || true)"
    commit="$(git -C "$MP_ROOT" rev-parse --short HEAD 2>/dev/null || printf unknown)"
    if [ -n "$domain" ] && curl -fsS --max-time 2 "https://${domain}/health" >/dev/null 2>&1; then
        health="healthy"
    fi
    MP_TUI_BACKTITLE="MP-OPT_SERVER | Brian Funk | Copyright © ${MP_COPYRIGHT_YEAR} Brian Funk | ${domain:-not configured} | ${health}"
    export MP_TUI_BACKTITLE
    if mp_has_tui; then
        return 0
    fi
    clear 2>/dev/null || true
    printf '\033[38;5;39m'
    printf '  +--------------------------------------------------------------+\n'
    printf '  |                       MP-OPT_SERVER                          |\n'
    printf '  |             Masterplan Optimiser Server Management          |\n'
    printf '  +--------------------------------------------------------------+\n'
    printf '\033[0m'
    printf '  Brian Funk | Copyright © %s Brian Funk\n' "$MP_COPYRIGHT_YEAR"
    printf '  Host: %-20s Domain: %-24s\n' "$(hostname -s 2>/dev/null || hostname)" "${domain:-not configured}"
    printf '  Commit: %-18s Public health: %s\n\n' "$commit" "$health"
}

# Read an exact key from an env file without evaluating shell content.
mp_env_get() {
    local key="$1"
    local file="${2:-$MP_ROOT/.env}"
    [ -f "$file" ] || return 1
    awk -v key="$key" 'index($0, key "=") == 1 {print substr($0, length(key) + 2); found=1} END {if (!found) exit 1}' "$file"
}

# Atomically set one existing or new env value while preserving permissions.
mp_env_set() {
    local key="$1"
    local value="$2"
    local file="${3:-$MP_ROOT/.env}"
    local tmp count
    tmp="$(mktemp "${file}.tmp.XXXXXX")"
    count="$(grep -c "^${key}=" "$file" 2>/dev/null || true)"
    if [ "$count" -gt 1 ]; then
        rm -f "$tmp"
        printf 'Duplicate setting: %s\n' "$key" >&2
        return 1
    fi
    awk -v key="$key" -v value="$value" '
        index($0, key "=") == 1 {print key "=" value; replaced=1; next}
        {print}
        END {if (!replaced) print key "=" value}
    ' "$file" > "$tmp"
    chmod --reference="$file" "$tmp" 2>/dev/null || chmod 600 "$tmp"
    chown --reference="$file" "$tmp" 2>/dev/null || true
    mv "$tmp" "$file"
}

# Move legacy password-bearing database settings into the protected file used
# by both PostgreSQL and the backend. The helper never prints the credential.
mp_migrate_database_secret() {
    local result
    result="$(python3 "$MP_ROOT/deploy/management/database_secret.py" \
        --env "$MP_ROOT/.env" \
        --secret "$MP_ROOT/secrets/database_password")" || return 1
    case "$result" in
        migrated) printf '       Migrated the database credential into its protected file.\n' ;;
        created) printf '       Created the protected database credential file.\n' ;;
        ready) ;;
        *) return 1 ;;
    esac
}

# Reject unsafe values before they can reach an env file or email header.
mp_validate_single_line() {
    local value="$1"
    [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]]
}

# Validate an unquoted dotenv value while still allowing calm display-name spaces.
mp_validate_env_value() {
    local value="$1"
    mp_validate_single_line "$value" \
        && [[ "$value" != *'#'* ]] \
        && [[ "$value" != *'"'* ]] \
        && [[ "$value" != *"'"* ]] \
        && [[ "$value" != *'\\'* ]] \
        && [[ "$value" != [[:space:]]* ]] \
        && [[ "$value" != *[[:space:]] ]]
}

# Validate a DNS hostname suitable for DOMAIN and WebAuthn RP ID.
mp_validate_hostname() {
    local value="$1"
    [ "${#value}" -le 253 ] && [[ "$value" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] && [[ "$value" == *.* ]]
}

# Validate an operator-selected snapshot name without permitting traversal.
mp_validate_snapshot_name() {
    local value="$1"
    [ "${#value}" -ge 1 ] && [ "${#value}" -le 64 ] && [[ "$value" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]
}

# Give the fixed unprivileged backend identity read-only access to the exact
# secrets mounted by Compose. The operator remains the owner so guarded TUI
# rotations can replace them; the mode-0700 parent directory prevents the
# runtime group from traversing the canonical host secret store.
mp_prepare_backend_secret_permissions() {
    local file
    local -a files=(
        "$MP_ROOT/secrets/database_password"
        "$MP_ROOT/secrets/ip_hmac_key"
        "$MP_ROOT/secrets/secret_key"
        "$MP_ROOT/secrets/vapid_private_key"
        "$MP_ROOT/secrets/root_bootstrap_token"
        "$MP_ROOT/secrets/smtp_token"
        "$MP_ROOT/secrets/evidence_signing_key"
        "$MP_ROOT/secrets/evidence_github_fine_grained_token"
        "$MP_HA_HOME/secrets/node_token"
    )
    for file in "${files[@]}"; do
        [ -e "$file" ] || continue
        [ -f "$file" ] && [ ! -L "$file" ] || {
            printf 'Refusing unsafe backend secret path: %s\n' "$file" >&2
            return 1
        }
        sudo -n chown ":10001" -- "$file" || return 1
        chmod 0640 -- "$file" || return 1
    done
}

# Prepare the append-only evidence bind source for the fixed unprivileged
# backend identity. Refuse symlinks so a privileged ownership change cannot be
# redirected outside the installation.
mp_prepare_evidence_store() {
    local evidence="$MP_ROOT/state/evidence"
    if [ -e "$evidence" ] && { [ ! -d "$evidence" ] || [ -L "$evidence" ]; }; then
        printf 'Refusing unsafe evidence store path: %s\n' "$evidence" >&2
        return 1
    fi
    sudo -n install -d -o 10001 -g 10001 -m 0700 "$evidence" || return 1
    sudo -n install -d -o 10001 -g 10001 -m 0700 "$evidence/public"
}

# Return the expected mode for a protected operator file. Runtime secrets are
# deliberately group-readable by the fixed, unprivileged backend identity;
# every other protected file remains owner-only.
mp_expected_protected_file_mode() {
    local file="$1"
    case "$file" in
        "$MP_ROOT/secrets/database_password"|"$MP_ROOT/secrets/ip_hmac_key"|"$MP_ROOT/secrets/secret_key"|"$MP_ROOT/secrets/vapid_private_key"|"$MP_ROOT/secrets/root_bootstrap_token"|"$MP_ROOT/secrets/smtp_token"|"$MP_ROOT/secrets/evidence_signing_key"|"$MP_ROOT/secrets/evidence_github_fine_grained_token"|"$MP_HA_HOME/secrets/node_token")
            printf '640\n'
            ;;
        *)
            printf '600\n'
            ;;
    esac
}

# Validate protected file modes against the deployment permission contract.
mp_validate_protected_file_modes() {
    local file mode expected failed=0
    while IFS= read -r file; do
        mode="$(stat -c '%a' "$file")" || { failed=1; continue; }
        expected="$(mp_expected_protected_file_mode "$file")"
        if [ "$mode" != "$expected" ]; then
            printf 'UNSAFE MODE: %s is %s (expected %s)\n' "$file" "$mode" "$expected"
            failed=1
        fi
    done < <(
        find "$MP_ROOT/secrets" -maxdepth 1 -type f -print
        printf '%s\n' "$MP_ROOT/.env"
        [ ! -f "$MP_HA_HOME/secrets/node_token" ] || printf '%s\n' "$MP_HA_HOME/secrets/node_token"
    )
    return "$failed"
}

# Build the exact production Compose command, including the local override.
mp_compose_init() {
    mp_load_ha_config || return 1
    MP_COMPOSE=(
        docker compose
        --env-file "$MP_ROOT/.env"
    )
    if [ -f "$MP_ROOT/.release.env" ]; then
        MP_COMPOSE+=(--env-file "$MP_ROOT/.release.env")
    fi
    if [ -f "$MP_TEST_DEPLOYMENT_ENV" ]; then
        [ "$(cat "$MP_DEPLOYMENT_POLICY_FILE" 2>/dev/null || true)" = test ] \
            || { printf 'Unsigned deployment override exists on a non-test installation.\n' >&2; return 1; }
        MP_COMPOSE+=(--env-file "$MP_TEST_DEPLOYMENT_ENV")
    fi
    MP_COMPOSE+=(
        -f "$MP_ROOT/infra/docker-compose.yml"
        -f "$MP_ROOT/infra/docker-compose.prod.yml"
    )
    case "${HA_ROLE:-standalone}" in
        dynamic) MP_COMPOSE+=(-f "$MP_ROOT/infra/docker-compose.ha.yml") ;;
        standalone) ;;
        *) printf 'Invalid HA role: %s\n' "$HA_ROLE" >&2; return 1 ;;
    esac
    if [ -f "$MP_ROOT/infra/docker-compose.override.yml" ]; then
        MP_COMPOSE+=(-f "$MP_ROOT/infra/docker-compose.override.yml")
    fi
}

# Validate the complete production Compose model without displaying secrets.
mp_compose_validate() {
    mp_compose_init
    "${MP_COMPOSE[@]}" config --quiet
}

# Return success only after the database proves that root bootstrap completed.
# This lets copies exclude the obsolete bearer secret even before an operator
# explicitly opens the root-recovery menu to clear the host file.
mp_root_bootstrap_is_disabled() {
    local disabled
    [ -f "$MP_ROOT/.env" ] || return 1
    mp_compose_init || return 1
    "${MP_COMPOSE[@]}" up -d db >/dev/null 2>&1 || return 1
    disabled="$("${MP_COMPOSE[@]}" exec -T db psql -U masterplan -d masterplan -Atqc \
        "SELECT
            EXISTS (
                SELECT 1 FROM server_settings
                WHERE key='root_bootstrap_disabled' AND value='true'
            )
            AND EXISTS (
                SELECT 1 FROM users u
                JOIN webauthn_credentials c ON c.user_id=u.id
                WHERE u.is_root_admin
            )" 2>/dev/null || true)"
    [ "$disabled" = t ]
}

mp_retire_root_bootstrap_secret() {
    if mp_root_bootstrap_is_disabled; then
        local token="$MP_ROOT/secrets/root_bootstrap_token"
        [ -f "$token" ] && [ ! -L "$token" ] || return 1
        # Normal post-commissioning operations must not rewrite an already
        # retired token. This keeps read-only workers read-only while still
        # allowing a guarded operator action to retire stale material.
        if [ -s "$token" ]; then
            : > "$token" || return 1
            chmod 600 "$token" || return 1
            mp_prepare_backend_secret_permissions || return 1
        fi
    fi
}

# Prepare generated frontend-policy storage and safely repair the empty
# directory created by Docker's legacy missing-file bind behaviour.
mp_prepare_frontend_csp_runtime() {
    local runtime_dir="$MP_ROOT/runtime"
    local policy_path="$runtime_dir/frontend-csp.caddy"
    local request_dir="$runtime_dir/ha-requests"
    local operation_result_dir="$runtime_dir/ha-operation-results"
    local compliance_request_dir="$runtime_dir/compliance-requests"
    local compliance_receipt_dir="$runtime_dir/compliance-receipts"
    local owner directory

    if [ -d "$policy_path" ]; then
        if find "$policy_path" -mindepth 1 -print -quit | grep -q .; then
            printf 'Refusing to replace non-empty CSP path: %s\n' "$policy_path" >&2
            return 1
        fi
        rmdir "$policy_path" 2>/dev/null \
            || sudo -n rmdir "$policy_path" \
            || return 1
    fi
    if [ -e "$policy_path" ] && [ ! -f "$policy_path" ]; then
        printf 'CSP output path is not a regular file: %s\n' "$policy_path" >&2
        return 1
    fi

    owner="$(id -u):$(id -g)"
    mkdir -p "$runtime_dir" 2>/dev/null || true
    [ -d "$runtime_dir" ] || return 1
    if [ ! -w "$runtime_dir" ]; then
        sudo -n chown "$owner" "$runtime_dir" || return 1
    fi
    [ -w "$runtime_dir" ]
    # Containers run as dedicated non-host UIDs. Permit traversal to the
    # deliberately mounted child paths without allowing directory listing.
    chmod 0711 "$runtime_dir" || return 1
    for directory in "$request_dir" "$compliance_request_dir" "$compliance_receipt_dir"; do
        if [ -e "$directory" ] && { [ ! -d "$directory" ] || [ -L "$directory" ]; }; then
            printf 'Refusing unsafe runtime request path: %s\n' "$directory" >&2
            return 1
        fi
        mkdir -p "$directory" 2>/dev/null || true
        [ -d "$directory" ] || return 1
        [ "$(stat -c '%u:%g' "$directory")" = "$owner" ] \
            || sudo -n chown "$owner" "$directory" \
            || return 1
    done
    # The unprivileged API may enqueue opaque replication jobs without being
    # able to list or replace requests created by another process.
    chmod 1733 "$request_dir" || return 1
    if [ -e "$operation_result_dir" ] && { [ ! -d "$operation_result_dir" ] || [ -L "$operation_result_dir" ]; }; then
        printf 'Refusing unsafe HA operation-result path: %s\n' "$operation_result_dir" >&2
        return 1
    fi
    mkdir -p "$operation_result_dir" 2>/dev/null || true
    [ -d "$operation_result_dir" ] || return 1
    [ "$(stat -c '%u:%g' "$operation_result_dir")" = "$owner" ] \
        || sudo -n chown "$owner" "$operation_result_dir" \
        || return 1
    # UUID filenames are known only to the authenticated caller. The backend
    # may traverse and read a named 0644 result but cannot list the directory.
    chmod 0711 "$operation_result_dir" || return 1
    chmod 1733 "$compliance_request_dir" || return 1
    # Receipts contain only UUIDs, timestamps and public digests. The host TUI
    # writes them and the unprivileged backend reads them through a read-only
    # bind mount; signatures prevent a readable file from being trusted after
    # modification.
    chmod 0755 "$compliance_receipt_dir" || return 1
}

# Build the static frontend in the pinned Node container while preserving an
# exact corresponding-source identity. The deliberately small Node image does
# not contain git and mounts only web/, so resolve the public repository and
# immutable revision on the host and pass them into Next.js explicitly.
mp_build_frontend_container() {
    local repository_root="${1:-$MP_ROOT}" repository revision source_url owner
    local -a source_environment
    repository="${MP_PUBLIC_SOURCE_REPOSITORY_URL:-}"
    revision="${MP_PUBLIC_SOURCE_REVISION:-}"
    source_url="${MP_PUBLIC_SOURCE_URL:-}"
    [ -n "$repository" ] \
        || repository="$(git -C "$repository_root" remote get-url origin)" \
        || return 1
    [ -n "$revision" ] \
        || revision="$(git -C "$repository_root" rev-parse HEAD)" \
        || return 1
    [[ "$revision" =~ ^[0-9a-f]{40}$ ]] \
        || { printf 'Frontend source revision is not an exact commit SHA.\n' >&2; return 1; }
    source_environment=(
        -e "MP_PUBLIC_SOURCE_REPOSITORY_URL=$repository"
        -e "MP_PUBLIC_SOURCE_REVISION=$revision"
    )
    if [ -n "$source_url" ]; then
        source_environment+=(-e "MP_PUBLIC_SOURCE_URL=$source_url")
    fi
    owner="$(stat -c '%u:%g' "$repository_root/web")" || return 1
    [[ "$owner" =~ ^[0-9]+:[0-9]+$ ]] \
        || { printf 'Frontend source ownership could not be determined.\n' >&2; return 1; }
    docker run --rm --user "$owner" -e HOME=/tmp "${source_environment[@]}" \
        -v "$repository_root/web:/app" -w /app node:22-alpine \
        sh -c 'npm ci --no-audit && npm audit --omit=dev --audit-level=high && npm run lint && npm run build'
}

# Print the active reverse-proxy topology without changing service state.
mp_caddy_mode() {
    mp_compose_init
    if "${MP_COMPOSE[@]}" config --services 2>/dev/null | grep -Fxq caddy; then
        printf 'container\n'
    elif command -v systemctl >/dev/null 2>&1 \
        && systemctl cat caddy >/dev/null 2>&1 \
        && [ -f "$MP_HOST_CADDYFILE" ]; then
        printf 'host\n'
    else
        printf 'unavailable\n'
    fi
}

# Validate the configured Caddy instance for the active topology.
mp_caddy_validate() {
    local mode
    mode="$(mp_caddy_mode)"
    case "$mode" in
        container)
            mp_compose_init
            "${MP_COMPOSE[@]}" exec -T caddy \
                caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
            ;;
        host)
            sudo caddy validate --config "$MP_HOST_CADDYFILE" --adapter caddyfile >/dev/null
            ;;
        *)
            printf 'No managed Caddy topology is available.\n' >&2
            return 1
            ;;
    esac
}

# Reload or recreate Caddy according to the active topology.
mp_caddy_reload() {
    local mode
    mode="$(mp_caddy_mode)"
    case "$mode" in
        container)
            mp_compose_init
            "${MP_COMPOSE[@]}" up -d --no-deps --force-recreate caddy >/dev/null
            ;;
        host) sudo systemctl reload caddy ;;
        *)
            printf 'No managed Caddy topology is available.\n' >&2
            return 1
            ;;
    esac
}

# Print a concise status line for the active Caddy topology.
mp_caddy_status() {
    local mode
    mode="$(mp_caddy_mode)"
    case "$mode" in
        container)
            mp_compose_init
            printf 'container: '
            "${MP_COMPOSE[@]}" ps --status running --services | grep -Fxq caddy \
                && printf 'active\n' || printf 'inactive\n'
            ;;
        host)
            printf 'host: '
            systemctl is-active caddy 2>&1 || true
            ;;
        *) printf 'unavailable\n' ;;
    esac
}

# Wait for both database-backed health and the public HTTPS endpoint.
mp_wait_for_health() {
    local domain attempts="${1:-30}"
    domain="$(mp_env_get DOMAIN)"
    for _ in $(seq 1 "$attempts"); do
        if curl -fsS --max-time 5 "https://${domain}/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    return 1
}

# Wait until PostgreSQL accepts connections for the application role.
mp_wait_for_database() {
    local attempts="${1:-30}"
    mp_compose_init
    for _ in $(seq 1 "$attempts"); do
        if "${MP_COMPOSE[@]}" exec -T db pg_isready -U masterplan -d masterplan >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    return 1
}

# Return whether SQLAlchemy's base schema is already present.
mp_database_has_base_schema() {
    local present
    mp_compose_init
    present="$("${MP_COMPOSE[@]}" exec -T db psql \
        -v ON_ERROR_STOP=1 -U masterplan -d masterplan -Atqc \
        "SELECT to_regclass('public.users') IS NOT NULL AND to_regclass('public.activation_links') IS NOT NULL;")" \
        || return 1
    [ "$present" = "t" ]
}

# Report the canonical database invariants that make fresh installs, upgraded
# installs and destructive rebuilds equivalent. The output contains metadata
# only; it never includes application rows or protected values.
mp_database_schema_contract_report() {
    mp_compose_init
    "${MP_COMPOSE[@]}" exec -T db psql \
        -v ON_ERROR_STOP=1 -U masterplan -d masterplan -At -F $'\t' <<'SQL'
WITH contract(ordinal, invariant, satisfied) AS (
    VALUES
        (1, 'base.users_table_present',
            to_regclass('public.users') IS NOT NULL),
        (2, 'base.activation_links_table_present',
            to_regclass('public.activation_links') IS NOT NULL),
        (3, 'ha_cluster_state.table_present',
            to_regclass('public.ha_cluster_state') IS NOT NULL),
        (4, 'ha_cluster_state.primary_key_is_id',
            EXISTS (
                SELECT 1
                FROM pg_constraint AS constraint_record
                WHERE constraint_record.conrelid = to_regclass('public.ha_cluster_state')
                  AND constraint_record.contype = 'p'
                  AND pg_get_constraintdef(constraint_record.oid) = 'PRIMARY KEY (id)'
            )),
        (5, 'ha_cluster_state.required_columns_not_null',
            COALESCE((
                SELECT count(*) = 6
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ha_cluster_state'
                  AND column_name IN (
                      'id', 'cluster_id', 'generation', 'active_node_id',
                      'maintenance', 'updated_at'
                  )
                  AND is_nullable = 'NO'
            ), FALSE)),
        (6, 'ha_cluster_state.id_has_no_default',
            COALESCE((
                SELECT column_default IS NULL
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ha_cluster_state'
                  AND column_name = 'id'
            ), FALSE)),
        (7, 'ha_cluster_state.id_sequence_absent',
            to_regclass('public.ha_cluster_state_id_seq') IS NULL),
        (8, 'ha_cluster_state.singleton_check',
            EXISTS (
                SELECT 1
                FROM pg_constraint AS constraint_record
                WHERE constraint_record.conrelid = to_regclass('public.ha_cluster_state')
                  AND constraint_record.contype = 'c'
                  AND regexp_replace(
                        pg_get_expr(constraint_record.conbin, constraint_record.conrelid),
                        '[[:space:]]', '', 'g'
                      ) IN ('(id=1)', 'id=1')
            )),
        (9, 'ha_cluster_state.generation_check',
            EXISTS (
                SELECT 1
                FROM pg_constraint AS constraint_record
                WHERE constraint_record.conrelid = to_regclass('public.ha_cluster_state')
                  AND constraint_record.contype = 'c'
                  AND regexp_replace(
                        pg_get_expr(constraint_record.conbin, constraint_record.conrelid),
                        '[[:space:]]', '', 'g'
                      ) IN ('(generation>=1)', 'generation>=1')
            )),
        (10, 'ha_cluster_state.maintenance_defaults_false',
            COALESCE((
                SELECT lower(column_default) IN ('false', 'false::boolean')
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ha_cluster_state'
                  AND column_name = 'maintenance'
            ), FALSE)),
        (11, 'ha_cluster_state.updated_at_defaults_to_current_time',
            COALESCE((
                SELECT lower(column_default) IN ('current_timestamp', 'now()')
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'ha_cluster_state'
                  AND column_name = 'updated_at'
            ), FALSE)),
        (12, 'audit_log.ip_hash_accepts_versioned_hmac',
            COALESCE((
                SELECT character_maximum_length >= 80
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'audit_log'
                  AND column_name = 'ip_hash'
            ), FALSE))
)
SELECT invariant, CASE WHEN satisfied THEN 'pass' ELSE 'fail' END
FROM contract
ORDER BY ordinal;
SQL
}

# Print every canonical invariant and fail closed if even one is absent. This
# is called after migrations by deployment and database-wipe workflows.
mp_verify_database_schema_contract() {
    local report invariant status failures=0
    report="$(mp_database_schema_contract_report)" || {
        printf '       FAIL   database.schema_contract_query\n' >&2
        return 1
    }
    while IFS=$'\t' read -r invariant status; do
        [ -n "$invariant" ] || continue
        if [ "$status" = pass ]; then
            printf '       PASS   %s\n' "$invariant"
        else
            printf '       FAIL   %s\n' "$invariant" >&2
            failures=$((failures + 1))
        fi
    done <<< "$report"
    [ "$failures" -eq 0 ]
}

# Bootstrap a blank database through the application's canonical model metadata.
mp_ensure_base_schema() {
    mp_database_has_base_schema && return 0
    printf '       Empty database detected. Initialising the base schema...\n'
    mp_compose_init
    "${MP_COMPOSE[@]}" run -T --rm --no-deps backend \
        python -m app.tools.bootstrap_schema >/dev/null || return 1
    mp_database_has_base_schema || return 1
    printf '       Base schema initialised without starting public services.\n'
}

# Initialise only the root bootstrap record and instance evidence genesis.  The
# Python command independently proves that no application state exists; this
# host-side guard additionally binds the exception to an active v2 setup.
mp_initialise_fresh_commissioning_state() {
    local setup_state="${MP_STATE}/setup-state-v2.json"
    jq -e '
        .format == "mp-opt-setup-state-v2"
        and .state == "in_progress"
        and (.mode == "standalone-new" or .mode == "ha-primary-new")
        and (.completed | index("application_deployed") == null)
    ' "$setup_state" >/dev/null 2>&1 || {
        printf 'Fresh application initialisation requires an active setup-v2 checkpoint.\n' >&2
        return 1
    }
    mp_compose_init
    "${MP_COMPOSE[@]}" run -T --rm --no-deps \
        -e MP_FRESH_COMMISSIONING=1 \
        -e HA_MODE=standalone -e HA_ROLE=standalone \
        -e HA_NODE_ID=standalone -e HA_CONTROL_WITNESS_REQUIRED=false \
        backend python -m app.tools.bootstrap_fresh_commissioning
}

# Append a sanitised hash-chained management receipt.
mp_audit() {
    local action="$1"
    local outcome="$2"
    local detail="${3:-none}"
    local previous="GENESIS"
    local timestamp line digest operator
    [[ "$action" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]] \
        || { printf 'Invalid management audit action.\n' >&2; return 1; }
    case "$outcome" in success|failed|pending|skipped) ;; *)
        printf 'Invalid management audit outcome.\n' >&2
        return 1
        ;;
    esac
    mp_validate_single_line "$detail" || return 1
    [ "${#detail}" -le 256 ] && [[ "$detail" != *'|'* ]] \
        || { printf 'Invalid management audit detail.\n' >&2; return 1; }
    operator="${USER:-unknown}"
    [[ "$operator" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || operator=unknown
    timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if [ -s "$MP_AUDIT_FILE" ]; then
        previous="$(tail -n 1 "$MP_AUDIT_FILE" | awk -F'|' '{print $1}')"
    fi
    line="${timestamp}|${operator}|${action}|${outcome}|${detail}|${previous}"
    digest="$(printf '%s' "$line" | sha256sum | awk '{print $1}')"
    printf '%s|%s\n' "$digest" "$line" >> "$MP_AUDIT_FILE"
    chmod 600 "$MP_AUDIT_FILE"
    mp_publish_audit_head >/dev/null 2>&1 || true
}

# Verify every management receipt digest and its link to the previous receipt.
mp_verify_audit_chain() {
    local expected_previous="GENESIS"
    local line digest body actual previous

    [ -f "$MP_AUDIT_FILE" ] || return 1
    while IFS= read -r line || [ -n "$line" ]; do
        digest="${line%%|*}"
        body="${line#*|}"
        [ "$body" != "$line" ] || return 1
        [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || return 1
        actual="$(printf '%s' "$body" | sha256sum | awk '{print $1}')" || return 1
        [ "$digest" = "$actual" ] || return 1
        previous="${body##*|}"
        [ "$previous" = "$expected_previous" ] || return 1
        expected_previous="$digest"
    done < "$MP_AUDIT_FILE"
}

# Print the current digest only after validating the complete management log.
mp_audit_tail_sha256() {
    mp_verify_audit_chain || return 1
    [ -s "$MP_AUDIT_FILE" ] || return 1
    tail -n 1 "$MP_AUDIT_FILE" | awk -F'|' '{print $1}'
}

# Publish only the validated tail for the read-only backend's evidence bridge.
mp_publish_audit_head() {
    local digest temporary target
    digest="$(mp_audit_tail_sha256)" || return 1
    temporary="$(mktemp "${MP_STATE}/audit-head.XXXXXX")" || return 1
    chmod 600 "$temporary"
    jq -n --arg digest "$digest" --arg verified "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '{format:"mp-opt-management-audit-head-v1",tail_sha256:$digest,verified_at:$verified}' \
        > "$temporary" || { rm -f "$temporary"; return 1; }
    target="$MP_EVIDENCE_HOME/public/management-audit-head.json"
    if [ -w "$(dirname "$target")" ]; then
        install -m 0600 "$temporary" "$target" || { rm -f "$temporary"; return 1; }
    elif sudo -n true >/dev/null 2>&1; then
        sudo install -o 10001 -g 10001 -m 0600 "$temporary" "$target" \
            || { rm -f "$temporary"; return 1; }
    else
        rm -f "$temporary"
        return 1
    fi
    rm -f "$temporary"
}

# Prevent concurrent mutating management sessions.
mp_lock() {
    exec 9>"$MP_LOCK_FILE"
    if ! flock -n 9; then
        ui_error "Another MP-OPT_SERVER management operation is already running."
        return 1
    fi
}

# Release a management lock before a long-lived menu message or guarded
# rollback returns. Closing the descriptor also makes repeated helper use safe.
mp_unlock() {
    flock -u 9 >/dev/null 2>&1 || true
    exec 9>&-
}

# Require all named executables before beginning a workflow.
mp_require_commands() {
    local missing=()
    local command_name
    for command_name in "$@"; do
        command -v "$command_name" >/dev/null 2>&1 || missing+=("$command_name")
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        ui_error "Missing required tools: ${missing[*]}"
        return 1
    fi
}

# Run one menu action with strict error handling without closing the dashboard.
mp_run_action() {
    local status
    set +e
    (
        set -Eeuo pipefail
        trap ':' INT
        "$@"
    )
    status=$?
    set -e
    if [ "$status" -ne 0 ]; then
        mp_audit "menu.action" "failed" "$1"
    fi
    return 0
}

# Return the configured age recipient after validating its public format.
mp_recovery_recipient() {
    local recipient
    [ -s "$MP_RECIPIENT_FILE" ] || return 1
    recipient="$(tr -d '\r\n' < "$MP_RECIPIENT_FILE")"
    [[ "$recipient" =~ ^age1[0-9a-z]+$ ]] || return 1
    printf '%s\n' "$recipient"
}

# Fingerprint only the public snapshot recipient. This value is safe to show
# in diagnostics and lets operators prove that both nodes use the same key.
mp_recovery_recipient_fingerprint() {
    local recipient="${1:-}"
    [ -n "$recipient" ] || recipient="$(mp_recovery_recipient)" || return 1
    [[ "$recipient" =~ ^age1[0-9a-z]+$ ]] || return 1
    printf '%s' "$recipient" | sha256sum | awk '{print $1}'
}

# Return a short, stable public identifier for a recovery key generation.
# This is deliberately derived only from the public recipient and is safe to
# record in receipts, journals and operator reports.
mp_recovery_key_id() {
    local recipient="${1:-}" fingerprint
    [ -n "$recipient" ] || recipient="$(mp_recovery_recipient)" || return 1
    fingerprint="$(mp_recovery_recipient_fingerprint "$recipient")" || return 1
    printf 'rk-%s\n' "${fingerprint:0:16}"
}

# Derive the public recipient from a transient private age identity.
mp_identity_recipient() {
    local identity_file="$1" recipient
    [ -f "$identity_file" ] && [ ! -L "$identity_file" ] || return 1
    recipient="$(age-keygen -y "$identity_file" 2>/dev/null | tr -d '\r\n')" || return 1
    [[ "$recipient" =~ ^age1[0-9a-z]+$ ]] || return 1
    printf '%s\n' "$recipient"
}

# Prove that a private identity belongs to one exact public recipient.
mp_identity_matches_recipient() {
    local identity_file="$1" expected="$2" actual
    actual="$(mp_identity_recipient "$identity_file")" || return 1
    [ "$actual" = "$expected" ]
}

# Atomically install one validated public recipient on this node.
mp_store_recovery_recipient_local() {
    local recipient="$1" temporary
    [[ "$recipient" =~ ^age1[0-9a-z]+$ ]] || return 1
    [ ! -L "$MP_RECIPIENT_FILE" ] || return 1
    mkdir -p "$MP_HOME"
    chmod 700 "$MP_HOME"
    temporary="$(mktemp "${MP_HOME}/recovery-recipient.XXXXXX")" || return 1
    printf '%s\n' "$recipient" > "$temporary"
    chmod 600 "$temporary"
    mv -f "$temporary" "$MP_RECIPIENT_FILE"
}

# Store only an age public recipient, never a private recovery identity.
mp_configure_recovery_recipient() {
    local recipient current probe fingerprint old_fingerprint role scope_message domain handoff
    domain="$(mp_env_get DOMAIN 2>/dev/null || true)"
    if [ -n "$domain" ]; then
        printf -v handoff 'Sign in with the registered root passkey:\nhttps://%s/login?next=/recovery-key\n\nRoot-only recovery-key generator URL:\nhttps://%s/recovery-key' "$domain" "$domain"
        ui_copyable_terminal_text "Recovery encryption" "$handoff" \
            "Sign in with the root passkey, create and save the private identity in two protected places on the workstation, then press Enter to return and paste only the public age1... recipient." \
            || return 1
    fi
    recipient="$(ui_input "Recovery encryption" "Paste the public age recipient beginning with age1")" || return 1
    recipient="$(printf '%s' "$recipient" | tr -d '\r\n')"
    if ! [[ "$recipient" =~ ^age1[0-9a-z]+$ ]]; then
        ui_error "That is not a valid age public recipient."
        return 1
    fi
    mp_require_commands age || return 1
    probe="$(mktemp "${TMPDIR:-/tmp}/mp-opt-age-probe.XXXXXX")"
    if ! printf '' | age -r "$recipient" -o "$probe"; then
        rm -f "$probe"
        ui_error "age rejected that public recipient."
        return 1
    fi
    rm -f "$probe"
    current="$(mp_recovery_recipient 2>/dev/null || true)"
    if [ -n "$current" ] && [ "$current" != "$recipient" ]; then
        if declare -F mp_rotate_recovery_recipient >/dev/null; then
            mp_rotate_recovery_recipient "$recipient"
            return $?
        fi
        ui_error "Use the guarded recovery-key rotation workflow. A configured key may not be replaced without accounting for existing snapshots."
        return 1
    fi
    role="$(mp_ha_role)" || return 1
    mp_lock || return 1
    if [ "$role" = "dynamic" ]; then
        if ! declare -F mp_ha_sync_recovery_recipient >/dev/null \
            || ! mp_ha_sync_recovery_recipient "$recipient"; then
            mp_unlock
            ui_error "The public recipient was not installed consistently on both HA nodes. No successful cluster change was recorded."
            return 1
        fi
    elif ! mp_store_recovery_recipient_local "$recipient"; then
        mp_unlock
        ui_error "The public recovery recipient could not be stored safely."
        return 1
    fi
    fingerprint="$(mp_recovery_recipient_fingerprint "$recipient")"
    if [ "$role" = dynamic ]; then
        scope_message=" identically on both HA nodes"
    else
        scope_message=" on this standalone node"
    fi
    mp_audit "recovery-recipient.configure" "success" "sha256:$fingerprint"
    mp_unlock
    printf -v handoff 'Public recipient SHA-256:\n%s' "$fingerprint"
    ui_copyable_terminal_text "Recovery encryption configured" "$handoff" \
        "Copy the public fingerprint if you keep an external recovery record, then press Enter to return. The AGE-SECRET-KEY private identity must remain off every VPS." \
        || return 1
    ui_message "Recovery encryption" \
        "The public recipient is configured${scope_message}. Create and deep-verify a fresh full snapshot."
}

# Place a pasted age private identity temporarily in memory-backed storage.
mp_prompt_identity_file() {
    local identity runtime_dir
    identity="$(ui_password "Recovery identity" "Paste the AGE-SECRET-KEY identity. It will not be stored")" || return 1
    if ! [[ "$identity" =~ ^AGE-SECRET-KEY-1[0-9A-Z]+$ ]]; then
        ui_error "The supplied recovery identity has an invalid format."
        return 1
    fi
    runtime_dir="/dev/shm"
    [ -d "$runtime_dir" ] && [ -w "$runtime_dir" ] || runtime_dir="${TMPDIR:-/tmp}"
    local file
    file="$(mktemp "${runtime_dir}/mp-opt-age.XXXXXX")"
    chmod 600 "$file"
    printf '%s\n' "$identity" > "$file"
    unset identity
    printf '%s\n' "$file"
}

# Ask for an identity and reject it immediately unless its derived public
# recipient equals the expected snapshot/key generation.
mp_prompt_identity_for_recipient() {
    local expected="$1" purpose="${2:-Recovery identity}" identity expected_id actual_id
    expected_id="$(mp_recovery_key_id "$expected")" || return 1
    identity="$(mp_prompt_identity_file)" || return 1
    if ! mp_identity_matches_recipient "$identity" "$expected"; then
        actual_id="$(mp_identity_recipient "$identity" 2>/dev/null \
            | { read -r value; [ -n "$value" ] && mp_recovery_key_id "$value" || true; })"
        mp_remove_identity_file "$identity"
        ui_error "The supplied identity is ${actual_id:-unrecognised}; ${purpose} requires ${expected_id}. No snapshot was changed."
        return 1
    fi
    printf '%s\n' "$identity"
}

# Optionally accept an identity for an older generation. Blank input selects
# the explicit emergency path; a non-blank value must match exactly.
mp_prompt_optional_identity_for_recipient() {
    local expected="$1" purpose="${2:-existing snapshots}" identity runtime_dir file expected_id
    expected_id="$(mp_recovery_key_id "$expected")" || return 1
    identity="$(ui_password "Existing recovery identity (optional)" \
        "Paste ${expected_id} to migrate all old snapshots. Leave blank only if this key is permanently unavailable")" || return 1
    [ -n "$identity" ] || { printf '\n'; return 0; }
    [[ "$identity" =~ ^AGE-SECRET-KEY-1[0-9A-Z]+$ ]] || {
        ui_error "The supplied recovery identity has an invalid format."
        return 1
    }
    runtime_dir=/dev/shm
    [ -d "$runtime_dir" ] && [ -w "$runtime_dir" ] || runtime_dir="${TMPDIR:-/tmp}"
    file="$(mktemp "${runtime_dir}/mp-opt-age.XXXXXX")" || return 1
    chmod 600 "$file" && printf '%s\n' "$identity" > "$file"
    unset identity
    if ! mp_identity_matches_recipient "$file" "$expected"; then
        mp_remove_identity_file "$file"
        ui_error "That identity does not belong to ${expected_id}. No snapshot was changed."
        return 1
    fi
    printf '%s\n' "$file"
}

# Remove a temporary identity file and any shell reference to its contents.
mp_remove_identity_file() {
    local file="${1:-}"
    [ -n "$file" ] || return 0
    chmod 600 "$file" 2>/dev/null || true
    : > "$file" 2>/dev/null || true
    rm -f "$file"
}

# Create a secure random URL-safe operator secret.
mp_random_secret() {
    openssl rand -base64 48 | tr '+/' '-_' | tr -d '=\n'
}

# Return a redacted configuration report suitable for terminal display.
mp_redacted_configuration() {
    local file="${1:-$MP_ROOT/.env}"
    awk -F= '
        /^[[:space:]]*#/ || /^[[:space:]]*$/ {next}
        {
            key=$1
            value=substr($0, length(key) + 2)
            if (key == "DATABASE_URL" || key ~ /(PASSWORD|SECRET|TOKEN|PRIVATE_KEY)/) value="<redacted>"
            printf "%-36s %s\n", key, value
        }
    ' "$file"
}

# Verify protected local files without changing them.
mp_permissions_report() {
    local file mode="${1:-full}"
    for file in "$MP_ROOT/.env" "$MP_ROOT/secrets/secret_key" \
        "$MP_ROOT/secrets/database_password" "$MP_ROOT/secrets/ip_hmac_key" \
        "$MP_ROOT/secrets/vapid_private_key" "$MP_ROOT/secrets/root_bootstrap_token" \
        "$MP_ROOT/secrets/smtp_token" "$MP_ROOT/secrets/evidence_signing_key" \
        "$MP_ROOT/secrets/evidence_github_fine_grained_token"; do
        if [ "$mode" = diagnostics ] && [ "$(basename "$file")" = evidence_github_fine_grained_token ]; then
            continue
        fi
        if [ -e "$file" ]; then
            stat -c '%n | mode=%a | owner=%U:%G | size=%s bytes' "$file"
        else
            printf '%s | MISSING\n' "$file"
        fi
    done
}

# Apply each immutable SQL migration once in filename order. Historical
# installations predate the ledger, so their first ledger-aware deployment
# safely reconciles the idempotent scripts and records their exact hashes.
mp_apply_migrations() {
    local migration name hash recorded
    mp_compose_init
    "${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 \
        -U masterplan -d masterplan -c \
        "CREATE TABLE IF NOT EXISTS mp_schema_migrations (
            name VARCHAR(255) PRIMARY KEY,
            sha256 VARCHAR(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );" >/dev/null || return 1
    for migration in "$MP_ROOT"/deploy/migrations/*.sql; do
        [ -f "$migration" ] || continue
        name="$(basename "$migration")"
        [[ "$name" =~ ^[0-9]{8}_[a-z0-9_]+\.sql$ ]] \
            || { printf '       Invalid migration filename: %s\n' "$name" >&2; return 1; }
        hash="$(sha256sum "$migration" | awk '{print $1}')" || return 1
        recorded="$("${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 \
            -U masterplan -d masterplan -Atqc \
            "SELECT sha256 FROM mp_schema_migrations WHERE name = '$name';")" \
            || return 1
        if [ -n "$recorded" ]; then
            [ "$recorded" = "$hash" ] \
                || { printf '       Applied migration hash changed: %s\n' "$name" >&2; return 1; }
            printf '       Migration already applied: %s\n' "$name"
            continue
        fi
        printf '       Applying migration: %s\n' "$name"
        "${MP_COMPOSE[@]}" exec -T db psql \
            -v ON_ERROR_STOP=1 -U masterplan -d masterplan < "$migration" >/dev/null \
            || { printf '       Migration failed: %s\n' "$name" >&2; return 1; }
        "${MP_COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 \
            -U masterplan -d masterplan -c \
            "INSERT INTO mp_schema_migrations (name, sha256) VALUES ('$name', '$hash');" \
            >/dev/null \
            || { printf '       Migration ledger update failed: %s\n' "$name" >&2; return 1; }
    done
}
