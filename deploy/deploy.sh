#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  deploy.sh  -  Pull, rebuild, restart (run ON the server)
#  Can be called directly or via update-server.bat from desktop
# ══════════════════════════════════════════════════════════════
set -euo pipefail
umask 077

PULL_LATEST=1
if [ "${1:-}" = "--no-pull" ]; then
    PULL_LATEST=0
elif [ "$#" -gt 0 ]; then
    echo "Usage: $0 [--no-pull]"
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"
export MP_ROOT="$REPO_DIR"
# shellcheck source=management/common.sh
source "$REPO_DIR/deploy/management/common.sh"

echo "════════════════════════════════════════════════════════"
echo "  MP-OPT Server  -  Deploy"
echo "════════════════════════════════════════════════════════"

# ── 1. Check .env ────────────────────────────────────────────
echo "[1/4] Checking prerequisites..."
if [ ! -f ".env" ]; then
    echo "  No .env file found  -  running interactive configuration..."
    echo ""
    bash "$REPO_DIR/configure-production.sh"
    if [ ! -f ".env" ]; then
        echo "  ERROR: .env still not created. Aborting."
        exit 1
    fi
fi
echo "       .env found, Docker available."

ensure_instance_id() {
    local instance_id
    instance_id="$(mp_env_get MP_INSTANCE_ID "$REPO_DIR/.env" 2>/dev/null || true)"
    if [ -z "$instance_id" ]; then
        if [ -r /proc/sys/kernel/random/uuid ]; then
            instance_id="$(tr '[:upper:]' '[:lower:]' < /proc/sys/kernel/random/uuid)"
        else
            instance_id="$(python3 -c 'import uuid; print(uuid.uuid4())')"
        fi
        mp_env_set MP_INSTANCE_ID "$instance_id" "$REPO_DIR/.env"
        echo "       Assigned stable instance evidence ID: $instance_id"
    elif ! [[ "$instance_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] \
            || [ "$instance_id" = "00000000-0000-0000-0000-000000000000" ]; then
        echo "  ERROR: MP_INSTANCE_ID must be a non-zero canonical lowercase UUID." >&2
        return 1
    fi
}

ensure_instance_id

ensure_secret_files() {
    mkdir -p secrets
    chmod 700 secrets

    mp_migrate_database_secret

    if [ ! -s "secrets/ip_hmac_key" ]; then
        IP_HMAC_KEY=$(openssl rand -base64 48 2>/dev/null || head -c 48 /dev/urandom | base64)
        printf "%s" "$IP_HMAC_KEY" > secrets/ip_hmac_key
        echo "       Created dedicated IP-pseudonymisation HMAC secret"
        unset IP_HMAC_KEY
    fi

    if [ ! -s "secrets/secret_key" ]; then
        SECRET_FROM_ENV=$(grep -m1 '^SECRET_KEY=' .env 2>/dev/null | cut -d= -f2- || true)
        if [ -z "$SECRET_FROM_ENV" ]; then
            SECRET_FROM_ENV=$(openssl rand -base64 48 2>/dev/null || head -c 48 /dev/urandom | base64)
        fi
        printf "%s" "$SECRET_FROM_ENV" > secrets/secret_key
        echo "       Created missing Docker secret: secrets/secret_key"
    fi

    if [ ! -s "secrets/vapid_private_key" ]; then
        VAPID_PRIVATE_KEY=$(openssl rand 32 2>/dev/null | base64 | tr '+/' '-_' | tr -d '=\n' || head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n')
        printf "%s" "$VAPID_PRIVATE_KEY" > secrets/vapid_private_key
        echo "       Created missing Docker secret: secrets/vapid_private_key"
    fi

    # An existing empty file intentionally keeps bootstrap disabled after setup.
    if [ ! -e "secrets/root_bootstrap_token" ]; then
        ROOT_BOOTSTRAP_TOKEN=$(grep -m1 '^ROOT_BOOTSTRAP_TOKEN=' .env 2>/dev/null | cut -d= -f2- || true)
        if [ "${#ROOT_BOOTSTRAP_TOKEN}" -lt 32 ] || [[ "$ROOT_BOOTSTRAP_TOKEN" == CHANGE_ME* ]]; then
            ROOT_BOOTSTRAP_TOKEN=$(openssl rand -base64 48 2>/dev/null || head -c 48 /dev/urandom | base64)
        fi
        printf "%s" "$ROOT_BOOTSTRAP_TOKEN" > secrets/root_bootstrap_token
        echo "       Created missing Docker secret: secrets/root_bootstrap_token"
        echo "       Read the bootstrap code with: cat $REPO_DIR/secrets/root_bootstrap_token"
    fi

    # SMTP is optional. The empty secret keeps Compose deployable until an
    # operator installs a provider-issued token.
    if [ ! -e "secrets/smtp_token" ]; then
        : > secrets/smtp_token
        echo "       Created empty optional Docker secret: secrets/smtp_token"
    fi

    # Automatic evidence archival is optional and disabled by default. Keep an
    # empty mount target so Compose remains valid without creating a token.
    if [ ! -e "secrets/evidence_github_fine_grained_token" ]; then
        : > secrets/evidence_github_fine_grained_token
        echo "       Created empty optional evidence Git token secret"
    fi

    if [ ! -s "secrets/evidence_signing_key" ]; then
        command -v ssh-keygen >/dev/null 2>&1 || {
            echo "  ERROR: ssh-keygen is required to initialise accountability evidence." >&2
            return 1
        }
        rm -f secrets/evidence_signing_key secrets/evidence_signing_key.pub
        ssh-keygen -q -t ed25519 -N '' -C 'mp-opt-evidence-v1' \
            -f secrets/evidence_signing_key
        echo "       Created dedicated instance evidence-signing key"
    elif [ ! -s "secrets/evidence_signing_key.pub" ]; then
        ssh-keygen -y -f secrets/evidence_signing_key \
            > secrets/evidence_signing_key.pub
    fi

    chmod 600 secrets/database_password secrets/ip_hmac_key \
        secrets/secret_key secrets/vapid_private_key secrets/root_bootstrap_token \
        secrets/smtp_token secrets/evidence_github_fine_grained_token \
        secrets/evidence_signing_key secrets/evidence_signing_key.pub
}

ensure_secret_files
mp_prepare_backend_secret_permissions || {
    echo "  ERROR: Backend secret permissions could not be prepared." >&2
    exit 1
}

# ── 2. Pull latest code ─────────────────────────────────────
if [ "$PULL_LATEST" -eq 1 ] && [ ! -f "$REPO_DIR/.release.env" ]; then
    echo "[2/5] Pulling latest code..."
    # Stash tracked local changes so a fast-forward pull can be applied safely.
    if ! git diff --quiet 2>/dev/null; then
        echo "       Stashing local changes..."
        git stash --quiet
        STASHED=1
    else
        STASHED=0
    fi

    if ! git pull --ff-only; then
        echo "  ERROR: Code update failed. Restoring local changes."
        if [ "$STASHED" -eq 1 ]; then
            git stash pop || true
        fi
        exit 1
    fi
    echo "       Code updated."

    if [ "$STASHED" -eq 1 ]; then
        echo "       Restoring local changes..."
        git stash pop --quiet || echo "  WARNING: stash pop had conflicts - check manually."
    fi
else
    echo "[2/5] Using the installed release/current checkout without pulling..."
fi

chmod 755 "$REPO_DIR/manage.sh" "$REPO_DIR/configure-production.sh" \
    "$REPO_DIR/deploy/deploy.sh" "$REPO_DIR/deploy/setup-server.sh" \
    "$REPO_DIR/deploy/management/"*.sh
chmod 755 "$REPO_DIR/deploy/ha/"*.sh
if sudo -n true >/dev/null 2>&1; then
    sudo ln -sf "$REPO_DIR/manage.sh" /usr/local/bin/mp-opt
    sudo install -d -o 10001 -g 10001 -m 0700 "$REPO_DIR/state/evidence"
    sudo install -d -o 10001 -g 10001 -m 0700 "$REPO_DIR/state/evidence/public"
    mp_publish_audit_head
else
    echo "  ERROR: Passwordless sudo is required to prepare the protected evidence store."
    exit 1
fi

if [ ! -f "$REPO_DIR/.release.env" ] && [ "$(mp_ha_role)" = "dynamic" ] \
    && { ! git diff --quiet --ignore-submodules -- \
        || ! git diff --cached --quiet --ignore-submodules --; }; then
    echo "  ERROR: HA nodes must run an unmodified Git commit. Tracked local changes were found."
    echo "  Commit or remove those changes before deploying so release matching remains trustworthy."
    exit 1
fi
mp_compose_init
mp_compose_validate
if [ "$(mp_ha_role)" = "dynamic" ] \
    && jq -e '.automatic_failover == true' "$REPO_DIR/runtime/ha-control.json" >/dev/null 2>&1; then
    echo "  ERROR: Disable automatic HA failover before deploying either node."
    echo "  This prevents ownership moving while schemas and containers are being replaced."
    exit 1
fi

# ── 3. Build frontend (static export) ───────────────────────
mp_prepare_frontend_csp_runtime
if [ -f "$REPO_DIR/.release.env" ]; then
    echo "[3/5] Using the verified frontend from $(grep -m1 '^MP_RELEASE_TAG=' .release.env | cut -d= -f2-)..."
    test -s "$REPO_DIR/web/out/index.html" \
        && test -s "$REPO_DIR/runtime/frontend-csp.caddy" \
        || { echo "  ERROR: Signed frontend assets are incomplete. Reinstall the release."; exit 1; }
else
    echo "[3/5] Building frontend..."
    docker run --rm \
        -v "$REPO_DIR/web:/app" \
        -w /app \
        node:22-alpine \
        sh -c "npm ci --no-audit && npm audit --omit=dev --audit-level=high && npm run lint && npm run build"
    python3 "$REPO_DIR/deploy/stamp_service_worker.py" "$REPO_DIR/web/out/sw.js" \
        "$(git -C "$REPO_DIR" rev-parse HEAD)"
    python3 "$REPO_DIR/deploy/generate_frontend_csp.py" "$REPO_DIR/web/out" \
        --output "$REPO_DIR/runtime/frontend-csp.caddy"
    echo "       Frontend built → web/out/"
fi

if sudo -n true >/dev/null 2>&1; then
    "$REPO_DIR/deploy/ha/install_services.sh" --install-only
    if [ "$(mp_ha_role)" = "dynamic" ]; then
        sudo -n systemctl stop mp-opt-ha-replication.timer \
            mp-opt-ha-replication.service mp-opt-ha-lease.service >/dev/null 2>&1 || true
    fi
fi

# ── 4. Build and start containers ───────────────────────────
echo "[4/5] Starting the database and applying schema updates..."
"${MP_COMPOSE[@]}" stop backend >/dev/null 2>&1 || true
if [ -f "$REPO_DIR/.release.env" ]; then
    echo "       Using digest-pinned, signature-verified production images..."
    "${MP_COMPOSE[@]}" pull db caddy backend
else
    echo "       Refreshing and building hardened service images..."
    # BuildKit avoids the legacy builder's large intermediate image commits.
    export DOCKER_BUILDKIT=1
    for service in db caddy backend; do
        "${MP_COMPOSE[@]}" build --pull "$service"
    done
fi
"${MP_COMPOSE[@]}" up -d db
if ! mp_wait_for_database 30; then
    echo "  ERROR: Database did not become ready."
    "${MP_COMPOSE[@]}" logs --tail 100 db >&2 || true
    exit 1
fi
if ! mp_ensure_base_schema; then
    echo "  ERROR: The base database schema could not be initialised. Public services were not started."
    exit 1
fi
if ! mp_apply_migrations; then
    echo "  ERROR: A database migration failed. Public services were not started."
    "${MP_COMPOSE[@]}" logs --tail 100 db backend >&2 || true
    exit 1
fi
echo "       Verifying the canonical database schema contract..."
if ! mp_verify_database_schema_contract; then
    echo "  ERROR: The canonical database schema contract is incomplete."
    echo "  Public services were not started; review the failed invariant above."
    exit 1
fi

echo "       Starting application containers..."
if [ -f "$REPO_DIR/.release.env" ]; then
    "${MP_COMPOSE[@]}" up -d --no-build --force-recreate --remove-orphans
else
    "${MP_COMPOSE[@]}" up -d --build --force-recreate --remove-orphans
fi
if sudo -n true >/dev/null 2>&1; then
    "$REPO_DIR/deploy/ha/install_services.sh"
fi
sleep 5
"${MP_COMPOSE[@]}" ps
mp_retire_root_bootstrap_secret

# ── 5. Health check ──────────────────────────────────────────
echo "[5/5] Health check..."
DOMAIN=$(grep -oP 'DOMAIN=\K.*' .env 2>/dev/null || echo "localhost")

for i in $(seq 1 15); do
    if { [ "$(mp_ha_role)" = "dynamic" ] \
        && "${MP_COMPOSE[@]}" exec -T backend python -c \
            'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3).read()' \
            >/dev/null 2>&1; } \
        || { [ "$(mp_ha_role)" != "dynamic" ] \
        && curl -sf --max-time 3 "https://${DOMAIN}/health" >/dev/null 2>&1; }; then
        echo "       Backend healthy!"
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo "  ERROR: Backend not responding. Check: docker compose logs backend"
        "${MP_COMPOSE[@]}" logs --tail 100 backend >&2 || true
        case "$(mp_caddy_mode)" in
            container) "${MP_COMPOSE[@]}" logs --tail 100 caddy >&2 || true ;;
            host) sudo journalctl -u caddy -n 100 --no-pager >&2 || true ;;
        esac
        exit 1
    fi
    sleep 3
done

echo ""
echo "════════════════════════════════════════════════════════"
echo "  Deployment complete: https://${DOMAIN}"
echo "════════════════════════════════════════════════════════"
