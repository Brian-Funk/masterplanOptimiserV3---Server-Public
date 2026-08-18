#!/usr/bin/env bash
# Migration-free, in-place activation of an already verified signed release.
# The new release must first be staged with install_release.py --blue-green.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MP_ROOT="${MP_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
export MP_ROOT
# shellcheck source=../management/common.sh
source "$MP_ROOT/deploy/management/common.sh"

[ "$MP_ROOT" = /opt/masterplan ] || [ "${MP_ALLOW_TEST_ROOT:-0}" = 1 ] || {
    printf 'Blue/green activation is restricted to /opt/masterplan.\n' >&2
    exit 2
}
[ "$(sed -n 's/^MP_RELEASE_BLUE_GREEN_STAGED=//p' "$MP_ROOT/.release.env" | head -1)" = true ] \
    || { printf 'No signed blue/green release is staged.\n' >&2; exit 2; }
[ "$(sed -n 's/^MP_RELEASE_MIGRATION_FREE=//p' "$MP_ROOT/.release.env" | head -1)" = true ] \
    || { printf 'The staged release is not migration-free.\n' >&2; exit 2; }
[ "$(sed -n 's/^MP_RELEASE_INFRASTRUCTURE_UNCHANGED=//p' "$MP_ROOT/.release.env" | head -1)" = true ] \
    || { printf 'The staged release changed an infrastructure contract.\n' >&2; exit 2; }

release_value() { sed -n "s/^$1=//p" "$2" | head -1; }
for image_key in MP_CADDY_IMAGE MP_POSTGRES_IMAGE; do
    [ "$(release_value "$image_key" "$MP_ROOT/.release.env")" = \
      "$(release_value "$image_key" "$MP_ROOT/.release.env.previous")" ] || {
        printf '%s changed; blue/green activation is forbidden.\n' "$image_key" >&2
        exit 2
    }
done
diff -qr "$MP_ROOT/deploy/migrations" "$MP_ROOT/.deploy.previous/migrations" >/dev/null || {
    printf 'Migration files differ from the active signed release.\n' >&2
    exit 2
}

owns_lock=false
if [ "${MP_RELEASE_LOCK_HELD:-0}" != 1 ]; then
    mp_lock
    owns_lock=true
fi
next_container=masterplan-backend-next
next_container_id=""
caddy_container=""
frontend_activated=false
finished=false

reload_caddy() {
    local config_path="$1"
    docker exec "$caddy_container" caddy validate \
        --config "$config_path" --adapter caddyfile >/dev/null
    docker exec "$caddy_container" caddy reload \
        --config "$config_path" --adapter caddyfile >/dev/null
}

wait_container_health() {
    local container="$1" deadline=$((SECONDS + 60))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if docker exec "$container" python -c \
            'import urllib.request; r=urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2); raise SystemExit(0 if r.status == 200 else 1)' \
            >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

rollback_upgrade() {
    local rc=$?
    [ "$finished" = true ] && return "$rc"
    set +e
    printf 'Blue/green acceptance failed; restoring the previous signed release.\n' >&2
    if [ "$frontend_activated" = true ]; then
        python3 "$MP_ROOT/deploy/release/activate_frontend.py" rollback --repo-root "$MP_ROOT"
    fi
    if [ -n "$next_container_id" ]; then
        docker rm -f "$next_container_id" >/dev/null 2>&1 || true
    fi
    python3 "$MP_ROOT/deploy/release/install_release.py" \
        --repo-root "$MP_ROOT" --rollback-staged
    mp_compose_init
    "${MP_COMPOSE[@]}" up -d --no-deps --no-build --pull never --force-recreate backend
    caddy_container="$("${MP_COMPOSE[@]}" ps -q caddy 2>/dev/null || true)"
    [ -z "$caddy_container" ] || reload_caddy /etc/caddy/Caddyfile
    rm -f "$MP_ROOT/runtime/blue-green.Caddyfile"
    [ "$owns_lock" != true ] || mp_unlock
    exit "$rc"
}
trap rollback_upgrade ERR INT TERM

mp_compose_init
caddy_container="$("${MP_COMPOSE[@]}" ps -q caddy)"
canonical_container="$("${MP_COMPOSE[@]}" ps -q backend)"
[ -n "$caddy_container" ] && [ -n "$canonical_container" ] || {
    printf 'Canonical Backend and Caddy must already be running.\n' >&2
    exit 1
}

previous_backend="$(release_value MP_BACKEND_IMAGE "$MP_ROOT/.release.env.previous")"
[ "$(docker inspect --format '{{.Config.Image}}' "$canonical_container")" = "$previous_backend" ] || {
    printf 'The canonical Backend does not match the retained signed release.\n' >&2
    exit 1
}
[ -z "$(docker ps -aq --filter "name=^/${next_container}$")" ] || {
    printf 'A container already occupies the temporary Backend name.\n' >&2
    exit 1
}
next_container_id="$(
    "${MP_COMPOSE[@]}" run -d --name "$next_container" --no-deps \
        -e BLUE_GREEN_STAGING=true backend
)"
[[ "$next_container_id" =~ ^[0-9a-f]{64}$ ]] || {
    printf 'Compose did not return the exact temporary Backend identity.\n' >&2
    exit 1
}
[ "$(docker inspect --format '{{.Name}}' "$next_container_id")" = "/$next_container" ] || {
    printf 'The temporary Backend identity is inconsistent.\n' >&2
    exit 1
}
wait_container_health "$next_container_id" || {
    printf 'The temporary Backend did not become healthy.\n' >&2
    exit 1
}

blue_green_config="$MP_ROOT/runtime/blue-green.Caddyfile"
[ ! -L "$blue_green_config" ] || {
    printf 'The temporary Caddy configuration path is unsafe.\n' >&2
    exit 1
}
rm -f "$blue_green_config"
docker exec "$caddy_container" cat /etc/caddy/Caddyfile > "$blue_green_config"
count="$(grep -o 'backend:8000' "$blue_green_config" | wc -l | tr -d ' ')"
[ "$count" -ge 1 ] || { printf 'The active Caddy route has no canonical Backend upstream.\n' >&2; exit 1; }
sed -i 's/backend:8000/masterplan-backend-next:8000/g' "$blue_green_config"
chmod 0644 "$blue_green_config"
reload_caddy /etc/caddy/runtime/blue-green.Caddyfile

# Public API traffic now uses the temporary backend while the canonical
# container is replaced. PostgreSQL and Caddy are deliberately untouched.
"${MP_COMPOSE[@]}" up -d --no-deps --no-build --pull never --force-recreate backend
canonical_container="$("${MP_COMPOSE[@]}" ps -q backend)"
wait_container_health "$canonical_container" || {
    printf 'The new canonical Backend did not become healthy.\n' >&2
    exit 1
}

python3 "$MP_ROOT/deploy/release/activate_frontend.py" activate --repo-root "$MP_ROOT"
frontend_activated=true
# This reload atomically returns API traffic to the healthy canonical Backend
# and loads the transitional CSP before the new HTML is publicly observed.
reload_caddy /etc/caddy/Caddyfile
mp_backend_health_once
mp_origin_tls_health_once

python3 "$MP_ROOT/deploy/release/activate_frontend.py" finalize --repo-root "$MP_ROOT"
reload_caddy /etc/caddy/Caddyfile
docker rm -f "$next_container_id" >/dev/null
next_container_id=""
rm -f "$blue_green_config"

environment_tmp="$MP_ROOT/.release.env.blue-green-complete"
sed 's/^MP_RELEASE_BLUE_GREEN_STAGED=true$/MP_RELEASE_BLUE_GREEN_STAGED=false/' \
    "$MP_ROOT/.release.env" > "$environment_tmp"
chmod 0600 "$environment_tmp"
mv -f "$environment_tmp" "$MP_ROOT/.release.env"
finished=true
trap - ERR INT TERM
[ "$owns_lock" != true ] || mp_unlock
printf 'SIGNED BLUE/GREEN RELEASE ACTIVATED\nTag: %s\nCommit: %s\n' \
    "$(release_value MP_RELEASE_TAG "$MP_ROOT/.release.env")" \
    "$(release_value MP_RELEASE_COMMIT "$MP_ROOT/.release.env")"
