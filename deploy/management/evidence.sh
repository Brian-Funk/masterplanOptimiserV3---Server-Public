#!/usr/bin/env bash
# Signed accountability evidence verification, export and copy guidance.

MP_EVIDENCE_BUNDLE_TOOL="${MP_ROOT}/deploy/evidence/portable_bundle.py"
MP_EVIDENCE_EXPORTS="${MP_STATE}/evidence-exports"
MP_EVIDENCE_GITHUB_CLIENT="${MP_ROOT}/deploy/evidence/github_token_client.py"
MP_EVIDENCE_GITHUB_TOKEN="${MP_ROOT}/secrets/evidence_github_fine_grained_token"

mp_instance_key_status() {
    local instance_id result body
    instance_id="$(mp_env_get MP_INSTANCE_ID)" || return 1
    if ! result="$(python3 "$MP_ROOT/deploy/management/instance_key.py" verify \
        --secret-dir "$MP_ROOT/secrets" --instance-id "$instance_id" 2>&1)"; then
        ui_error "Instance signing identity is missing, unreadable or mismatched. Server must fail closed. Do not generate a replacement. Use a verified encrypted recovery package or the guarded rotation workflow.\n\n${result}"
        return 1
    fi
    body="$(jq -r '"'"'"Instance ID: \(.instance_id)\nRole: \(.role)\nAlgorithm: \(.algorithm)\nStatus: \(.status)\nPublic fingerprint SHA-256: \(.public_key_sha256)"'"'"' <<< "$result")"
    if mp_load_ha_config >/dev/null 2>&1 && [ "${HA_ROLE:-standalone}" = dynamic ]; then
        body="${body}\nHA: shared instance key is transferred by the encrypted replication bundle. Confirm the same fingerprint in the peer status before failover."
    else
        body="${body}\nHA: standalone"
    fi
    ui_message "Instance signing identity" "$body"
}

mp_trust_key_guidance() {
    ui_message "Controller and processor key ceremonies" \
        "Open the root accountability screen in the Server web interface.\n\nController: generate and sign only with tools/controller_custody.py on a controller-controlled workstation.\n\nProcessor: generate and sign only in Masterplan Desktop using the declared processor ID.\n\nServer accepts public material and proof packages only. Root then authorises the exact activation with WebAuthn. The instance key signs the durable verification record. Controller and processor private keys must never enter this VPS."
}

mp_evidence_verify() {
    local result report
    report="$(mktemp "$MP_STATE/evidence-verify.XXXXXX")" || return 1
    if ! result="$(sudo -n python3 "$MP_ROOT/deploy/evidence/evidence_manifest.py" verify-chain \
        "$MP_EVIDENCE_HOME/ledger" \
        "$MP_EVIDENCE_HOME/public/instance_signing_key.pub" 2>&1)"; then
        printf 'SIGNED EVIDENCE: INVALID OR UNAVAILABLE\n\n%s\n' "$result" > "$report"
        chmod 600 "$report"
        ui_text_file "Accountability evidence" "$report"
        rm -f "$report"
        return 1
    fi
    {
        printf 'SIGNED EVIDENCE: VALID\n\n'
        jq . <<< "$result"
        printf '\nStore: %s\n' "$MP_EVIDENCE_HOME"
        printf 'Private signing key: not included in exports\n'
    } > "$report"
    chmod 600 "$report"
    mp_audit "evidence.verify" "success" "$(jq -r '.head_sha256' <<< "$result")"
    ui_text_file "Accountability evidence" "$report"
    rm -f "$report"
}

mp_evidence_export_bundle() {
    local timestamp partial output result hash host copy_text
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "$MP_EVIDENCE_EXPORTS" && chmod 700 "$MP_EVIDENCE_EXPORTS" || return 1
    output="$MP_EVIDENCE_EXPORTS/${timestamp}_accountability-evidence.zip"
    partial="${output}.root"
    [ ! -e "$output" ] && [ ! -e "$partial" ] || return 1
    if ! result="$(sudo -n python3 "$MP_EVIDENCE_BUNDLE_TOOL" create-local-zip \
        --evidence-home "$MP_EVIDENCE_HOME" \
        --trust-repository "$MP_EVIDENCE_HOME/controller-trust" \
        --instance-id "$(mp_env_get MP_INSTANCE_ID)" \
        --output "$partial" 2>&1)"; then
        sudo -n rm -f "$partial"
        ui_error "Signed evidence verification or ZIP creation failed. Nothing was exported.\n\n${result}"
        return 1
    fi
    sudo -n chown "$(id -u):$(id -g)" "$partial" || return 1
    chmod 600 "$partial" || return 1
    mv "$partial" "$output" || return 1
    hash="$(sha256sum "$output" | awk '{print $1}')"
    python3 "$MP_EVIDENCE_BUNDLE_TOOL" verify-zip --zip "$output" >/dev/null || return 1
    host="$(hostname -f 2>/dev/null || hostname)"
    copy_text="Complete evidence ZIP: $output
ZIP SHA-256: $hash

Run from a workstation terminal:

scp deploy@${host}:$(printf '%q' "$output") .
sha256sum $(printf '%q' "$(basename "$output")")

The displayed ZIP SHA-256 must match exactly. The ZIP contains
accountability.evidence, accountability.evidence.sha256 and VERIFYING.txt.
It contains public verification material but no private signing, passkey,
recovery or application secret."
    mp_audit "evidence.zip-export" "success" "$(basename "$output"):sha256:${hash}"
    ui_copyable_terminal_text "Complete accountability evidence exported" "$copy_text" \
        "Copy and run the workstation commands. Press Enter here to return to MP-OPT_SERVER."
}

mp_evidence_git_guidance() {
    local export_zip body
    mkdir -p "$MP_EVIDENCE_EXPORTS" && chmod 700 "$MP_EVIDENCE_EXPORTS" || return 1
    export_zip="$(find "$MP_EVIDENCE_EXPORTS" -maxdepth 1 -type f -name '*accountability-evidence.zip' -print | sort -r | head -n1)"
    if [ -z "$export_zip" ]; then
        ui_error "Export and verify a complete accountability evidence ZIP first."
        return 1
    fi
    body="The Git archive repository is intentionally not created automatically.
Create an empty private repository only after the local evidence tests pass.

After cloning that repository on a trusted workstation, stage the verified bundle with:

unzip $(printf '%q' "$(basename "$export_zip")") accountability.evidence accountability.evidence.sha256 VERIFYING.txt
python3 /absolute/path/to/trusted-server-source/deploy/evidence/portable_bundle.py stage-archive \\
  --bundle /absolute/path/to/copied/accountability.evidence \\
  --archive /absolute/path/to/cloned-evidence-repository

The command uses the protected installed verifier and stages only the bundle
and its digest. Then inspect and publish explicitly:

cd /absolute/path/to/cloned-evidence-repository
git status --short
git add instances
git commit -S -m \"Archive verified MP-OPT accountability evidence\"
git push

The staging command is idempotent and rejects a reused bundle ID with changed
content. Git is an additional off-server accountability copy, not proof that
an external storage provider physically erased data."
    ui_copyable_terminal_text "Git evidence archive" "$body" \
        "Copy these commands if needed. Press Enter here to return to MP-OPT_SERVER."
}

mp_evidence_git_status() {
    local configured="no" enabled="false" owner repository branch fingerprint body runtime_status
    [ -s "$MP_EVIDENCE_GITHUB_TOKEN" ] && configured="yes"
    enabled="$(mp_env_get EVIDENCE_GIT_ARCHIVE_ENABLED 2>/dev/null || printf false)"
    owner="$(mp_env_get EVIDENCE_GIT_REPOSITORY_OWNER 2>/dev/null || printf not-configured)"
    repository="$(mp_env_get EVIDENCE_GIT_REPOSITORY_NAME 2>/dev/null || printf not-configured)"
    branch="$(mp_env_get EVIDENCE_GIT_DEFAULT_BRANCH 2>/dev/null || printf main)"
    fingerprint="$(mp_env_get EVIDENCE_GITHUB_TOKEN_FINGERPRINT 2>/dev/null || printf unavailable)"
    mp_compose_init
    runtime_status="$("${MP_COMPOSE[@]}" exec -T backend python -m app.services.evidence_archive status 2>/dev/null || printf '{"state":"Unavailable"}')"
    body="Automatic archival enabled: ${enabled}
Fine-grained GitHub personal access token configured: ${configured}
Token fingerprint: ${fingerprint}
Private repository: ${owner}/${repository}
Protected default branch: ${branch}

Durable uploader status:
$(jq . <<< "$runtime_status" 2>/dev/null || printf 'Unavailable')

No token value, token path or bundle content is shown. The local ledger and
manual bundle export continue when automatic archival is disabled or unavailable."
    ui_message "Evidence Git archive status" "$body"
}

mp_evidence_git_test_file() {
    local token_file="$1"
    local owner="$2"
    local repository="$3"
    local branch="$4"
    local repository_id="${5:-}"
    python3 "$MP_EVIDENCE_GITHUB_CLIENT" \
        --owner "$owner" \
        --repository "$repository" \
        --repository-id "$repository_id" \
        --default-branch "$branch" \
        --token-file "$token_file"
}

mp_evidence_git_configure() {
    local token repeat owner repository branch controller_id instance_id schedule
    local staged result repository_id fingerprint preflight_dir preflight bundle_controller_id
    owner="$(ui_input "Evidence Git archive" "Private Evidence repository owner")" || return 1
    repository="$(ui_input "Evidence Git archive" "Private Evidence repository name")" || return 1
    branch="$(ui_input "Evidence Git archive" "Protected default branch" "main")" || return 1
    controller_id="$(ui_input "Evidence Git archive" "Stable controller ID in the form ctl- plus 16 lowercase letters or numbers")" || return 1
    instance_id="$(mp_env_get MP_INSTANCE_ID 2>/dev/null || true)"
    schedule="$(ui_input "Evidence Git archive" "Upload schedule in seconds, from 60 to 86400" "900")" || return 1
    [[ "$owner" =~ ^[A-Za-z0-9._-]{1,100}$ ]] \
        && [[ "$repository" =~ ^[A-Za-z0-9._-]{1,100}$ ]] \
        && [[ "$branch" =~ ^[A-Za-z0-9._/-]{1,100}$ ]] \
        && [[ "$controller_id" =~ ^ctl-[a-z0-9]{16}$ ]] \
        && [[ "$instance_id" =~ ^[0-9a-f-]{36}$ ]] \
        && [[ "$schedule" =~ ^[0-9]+$ ]] && [ "$schedule" -ge 60 ] && [ "$schedule" -le 86400 ] \
        || { ui_error "Repository, controller, instance, branch or schedule input is invalid."; return 1; }
    [ "${repository,,}" != "masterplanoptimiserv3---evidence-public" ] \
        || { ui_error "Automatic archival must not target Evidence-Public."; return 1; }
    preflight_dir="$(mktemp -d "$MP_STATE/evidence-archive-preflight.XXXXXX")" || return 1
    preflight="$preflight_dir/preflight.evidence.bundle"
    if ! result="$(sudo -n python3 "$MP_EVIDENCE_BUNDLE_TOOL" create-local \
        --evidence-home "$MP_EVIDENCE_HOME" \
        --trust-repository "$MP_EVIDENCE_HOME/controller-trust" \
        --instance-id "$instance_id" \
        --output "$preflight" 2>&1)"; then
        sudo -n rm -f "$preflight" "${preflight}.sha256"
        sudo -n rmdir "$preflight_dir" 2>/dev/null || true
        ui_error "The local ledger and controller-approved trust declarations could not produce a verified portable bundle. Automatic archival remains disabled.\n\n${result}"
        return 1
    fi
    bundle_controller_id="$(jq -er '.controller_id' <<< "$result" 2>/dev/null || true)"
    sudo -n rm -f "$preflight" "${preflight}.sha256"
    sudo -n rmdir "$preflight_dir" || return 1
    [ -n "$bundle_controller_id" ] || { ui_error "Portable bundle verification returned no controller ID."; return 1; }
    [ "$bundle_controller_id" = "$controller_id" ] \
        || { ui_error "The entered controller ID does not match the verified portable bundle."; return 1; }
    token="$(ui_password "Evidence Git archive" "Fine-grained GitHub personal access token")" || return 1
    repeat="$(ui_password "Evidence Git archive" "Repeat the Fine-grained GitHub personal access token")" || return 1
    if [ "$token" != "$repeat" ] || [[ "$token" != github_pat_* ]] || [ "${#token}" -lt 20 ]; then
        token=""; repeat=""
        ui_error "The entries do not match or are not a Fine-grained GitHub personal access token. Classic tokens are not supported."
        return 1
    fi
    mkdir -p "$MP_ROOT/secrets" && chmod 700 "$MP_ROOT/secrets" || return 1
    staged="$(mktemp "$MP_ROOT/secrets/.evidence-github-token.XXXXXX")" || return 1
    chmod 600 "$staged" || { rm -f "$staged"; return 1; }
    printf '%s' "$token" > "$staged" || { rm -f "$staged"; return 1; }
    token=""; repeat=""
    if ! result="$(mp_evidence_git_test_file "$staged" "$owner" "$repository" "$branch" 2>&1)"; then
        rm -f "$staged"
        ui_error "$result"
        return 1
    fi
    repository_id="$(jq -er '.repository_id' <<< "$result")" || { rm -f "$staged"; return 1; }
    if ! ui_confirm "Enable automatic evidence archival" \
        "The repository is private and the selected branch reports protection. Confirm separately that protected main requires pull requests, evidence verification, ingestion-path validation, up-to-date branches, blocked force pushes, blocked deletion and no token-owner bypass. A compromised VPS may obtain this repository-scoped token."; then
        rm -f "$staged"
        return 1
    fi
    fingerprint="fgp-$(sha256sum "$staged" | awk '{print substr($1,1,16)}')"
    mv -f "$staged" "$MP_EVIDENCE_GITHUB_TOKEN" || return 1
    chmod 600 "$MP_EVIDENCE_GITHUB_TOKEN" || return 1
    mp_env_set EVIDENCE_GIT_REPOSITORY_OWNER "$owner"
    mp_env_set EVIDENCE_GIT_REPOSITORY_NAME "$repository"
    mp_env_set EVIDENCE_GIT_REPOSITORY_ID "$repository_id"
    mp_env_set EVIDENCE_GIT_DEFAULT_BRANCH "$branch"
    mp_env_set EVIDENCE_CONTROLLER_ID "$controller_id"
    mp_env_set EVIDENCE_ALLOWED_INSTANCE_ID "$instance_id"
    mp_env_set EVIDENCE_GIT_UPLOAD_SCHEDULE_SECONDS "$schedule"
    mp_env_set EVIDENCE_GITHUB_TOKEN_FINGERPRINT "$fingerprint"
    mp_env_set EVIDENCE_GIT_PROTECTION_ACK_VERSION "1"
    mp_env_set EVIDENCE_GIT_ARCHIVE_ENABLED "true"
    mp_audit "evidence.git-token.configure" "success" "${owner}/${repository}:${fingerprint}"
    ui_message "Evidence Git archive" "Access verified and the Fine-grained GitHub personal access token was stored with owner-only permissions. Restart the backend to enable the durable uploader."
}

mp_evidence_git_test_saved() {
    local owner repository branch repository_id result
    [ -s "$MP_EVIDENCE_GITHUB_TOKEN" ] || { ui_error "No Fine-grained GitHub personal access token is configured."; return 1; }
    owner="$(mp_env_get EVIDENCE_GIT_REPOSITORY_OWNER)" || return 1
    repository="$(mp_env_get EVIDENCE_GIT_REPOSITORY_NAME)" || return 1
    repository_id="$(mp_env_get EVIDENCE_GIT_REPOSITORY_ID)" || return 1
    branch="$(mp_env_get EVIDENCE_GIT_DEFAULT_BRANCH)" || return 1
    result="$(mp_evidence_git_test_file "$MP_EVIDENCE_GITHUB_TOKEN" "$owner" "$repository" "$branch" "$repository_id")" || return 1
    ui_message "Evidence repository readiness" "$(jq . <<< "$result")"
}

mp_evidence_git_disable() {
    local staged
    ui_require_phrase "Disable automatic evidence archival" \
        "This deletes the stored Fine-grained GitHub personal access token. The local ledger and manual export remain available." \
        "DISABLE EVIDENCE ARCHIVAL" || return 1
    staged="$(mktemp "$MP_ROOT/secrets/.evidence-github-token.XXXXXX")" || return 1
    : > "$staged" && chmod 600 "$staged" && mv -f "$staged" "$MP_EVIDENCE_GITHUB_TOKEN" || return 1
    mp_env_set EVIDENCE_GIT_ARCHIVE_ENABLED "false"
    mp_env_set EVIDENCE_GITHUB_TOKEN_FINGERPRINT "unconfigured"
    mp_audit "evidence.git-token.delete" "success" "automatic-archive-disabled"
    ui_message "Evidence Git archive" "The token was deleted and automatic archival is disabled."
}

mp_evidence_git_retry() {
    local submission_id
    submission_id="$(ui_input "Retry evidence archival" "Submission ID, for example sub- followed by 32 hexadecimal characters")" || return 1
    [[ "$submission_id" =~ ^sub-[0-9a-f]{32}$ ]] \
        || { ui_error "The submission ID is invalid."; return 1; }
    mp_compose_init
    "${MP_COMPOSE[@]}" exec -T backend python -m app.services.evidence_archive \
        retry-failed --submission-id "$submission_id"
}
