#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  setup-server.sh  -  One-time MP-OPT VPS provisioning
#  Run as root on a fresh Ubuntu 22.04 / 24.04 VPS
# ══════════════════════════════════════════════════════════════
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run this bootstrap as root." >&2; exit 1; }
REPOSITORY_URL="${MP_REPOSITORY_URL:-https://github.com/Brian-Funk/MasterplanOptimiserV3---Server.git}"
REPOSITORY_REF="${MP_REPOSITORY_REF:-}"
LAUNCH_TUI=1
while [ "$#" -gt 0 ]; do
    case "$1" in
        --repository-url) REPOSITORY_URL="${2:?Missing repository URL}"; shift 2 ;;
        --ref) REPOSITORY_REF="${2:?Missing repository ref}"; shift 2 ;;
        --no-launch) LAUNCH_TUI=0; shift ;;
        *) echo "Usage: $0 [--repository-url URL] --ref vMAJOR.MINOR.PATCH|COMMIT [--no-launch]" >&2; exit 2 ;;
    esac
done
[ -n "$REPOSITORY_REF" ] || {
    echo "A verified stable release tag is required (for example --ref v1.2.3)." >&2
    exit 2
}
[[ "$REPOSITORY_REF" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || [[ "$REPOSITORY_REF" =~ ^[0-9a-f]{40}$ ]] \
    || { echo "--ref must be a stable release tag or exact lowercase commit." >&2; exit 2; }

# Keep the supported host surface deliberately small and predictable.
# shellcheck disable=SC1091
source /etc/os-release
[ "${ID:-}" = ubuntu ] && { [ "${VERSION_ID:-}" = 22.04 ] || [ "${VERSION_ID:-}" = 24.04 ]; } \
    || { echo "MP-OPT supports fresh Ubuntu 22.04 or 24.04 VPSs only." >&2; exit 1; }

echo "════════════════════════════════════════════════════════"
echo "  MP-OPT Server  -  Setup"
echo "════════════════════════════════════════════════════════"
echo ""

# ── 1. System updates ─────────────────────────────────────────
echo "[1/5] Updating system packages..."
apt update && apt upgrade -y

# ── 2. Essential packages ────────────────────────────────────
echo "[2/5] Installing essentials..."
apt install -y curl git sudo ufw fail2ban ca-certificates gnupg age jq dialog whiptail rsync openssh-client dnsutils

# ── 3. Firewall ──────────────────────────────────────────────
echo "[3/5] Configuring firewall (UFW)..."
ufw default deny incoming
ufw default allow outgoing
mapfile -t ssh_ports < <(/usr/sbin/sshd -T | awk '$1 == "port" {print $2}' | sort -un)
[ "${#ssh_ports[@]}" -gt 0 ] || { echo "Could not determine the effective SSH port." >&2; exit 1; }
for ssh_port in "${ssh_ports[@]}"; do
    [[ "$ssh_port" =~ ^[0-9]+$ ]] && [ "$ssh_port" -ge 1 ] && [ "$ssh_port" -le 65535 ] \
        || { echo "Invalid effective SSH port: $ssh_port" >&2; exit 1; }
    ufw allow "${ssh_port}/tcp" comment 'SSH'
done
ufw allow 80/tcp   comment 'HTTP -> Caddy redirect'
ufw allow 443/tcp  comment 'HTTPS -> Caddy'
ufw --force enable
ufw status verbose

# ── 4. Docker ────────────────────────────────────────────────
echo "[4/5] Installing Docker..."
if ! command -v docker &>/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl --fail --silent --show-error --location \
        https://download.docker.com/linux/ubuntu/gpg \
        --output /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    docker_arch="$(dpkg --print-architecture)"
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' \
        "$docker_arch" "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.list
    apt update
    apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker
    systemctl start docker
else
    echo "       Docker already installed: $(docker --version)"
fi

docker compose version || {
    echo "Installing docker-compose-plugin..."
    apt install -y docker-compose-plugin
}

# ── 5. Deploy user ──────────────────────────────────────────
echo "[5/5] Creating 'deploy' user..."
deploy_user_created=0
deploy_had_docker=0
if ! id -u deploy &>/dev/null; then
    adduser --disabled-password --gecos "Deploy" deploy
    deploy_user_created=1
    mkdir -p /home/deploy/.ssh
    if [ -f /root/.ssh/authorized_keys ]; then
        cp /root/.ssh/authorized_keys /home/deploy/.ssh/
    fi
    chown -R deploy:deploy /home/deploy/.ssh
    chmod 700 /home/deploy/.ssh
    chmod 600 /home/deploy/.ssh/authorized_keys 2>/dev/null || true
    echo "       User 'deploy' created."
else
    echo "       User 'deploy' already exists."
    if id -nG deploy | tr ' ' '\n' | grep -Fxq docker; then
        deploy_had_docker=1
    fi
fi

# Existing operators need the same Docker access as newly created accounts.
usermod -aG docker deploy
if ! id -nG deploy | tr ' ' '\n' | grep -Fxq docker; then
    echo "ERROR: User 'deploy' was not added to the docker group." >&2
    exit 1
fi
echo "       Docker group membership verified for 'deploy'."

# Docker administration is already root-equivalent. Give the dedicated
# operator account non-interactive sudo as well so guarded systemd, firewall,
# and protected HA-file operations cannot stall on a password prompt.
printf 'deploy ALL=(root) NOPASSWD: ALL\n' > /etc/sudoers.d/mp-opt-deploy
chmod 440 /etc/sudoers.d/mp-opt-deploy
visudo -cf /etc/sudoers.d/mp-opt-deploy >/dev/null

# Unsigned exact-commit deployment is an explicit test-lab capability. Fresh
# installations are production-policy until an operator deliberately changes
# this through the guarded management interface.
if [ ! -e /etc/mp-opt/deployment-policy ]; then
    install -d -o root -g root -m 0755 /etc/mp-opt
    printf 'production\n' > /etc/mp-opt/deployment-policy
    chmod 0644 /etc/mp-opt/deployment-policy
fi

# Fetch the small management checkout. Application containers and frontend
# assets are subsequently installed from the newest signed stable release.
if [ ! -f /opt/masterplan/manage.sh ]; then
    if [ -d /opt/masterplan ] && find /opt/masterplan -mindepth 1 -print -quit | grep -q .; then
        echo "ERROR: /opt/masterplan is not empty and is not an MP-OPT checkout." >&2
        exit 1
    fi
    rm -rf /opt/masterplan
    if [[ "$REPOSITORY_REF" =~ ^[0-9a-f]{40}$ ]]; then
        git init /opt/masterplan
        git -C /opt/masterplan remote add origin "$REPOSITORY_URL"
        git -C /opt/masterplan fetch --depth 1 origin "$REPOSITORY_REF"
        git -C /opt/masterplan checkout --detach "$REPOSITORY_REF"
        [ "$(git -C /opt/masterplan rev-parse HEAD)" = "$REPOSITORY_REF" ] || {
            echo "ERROR: The management checkout does not match the verified commit." >&2
            exit 1
        }
    else
        git clone --depth 1 --branch "$REPOSITORY_REF" "$REPOSITORY_URL" /opt/masterplan
    fi
fi
chown -R deploy:deploy /opt/masterplan

# Install the friendly launcher when the repository is already present.
if [ -f /opt/masterplan/manage.sh ]; then
    chmod 755 /opt/masterplan/manage.sh /opt/masterplan/configure-production.sh \
        /opt/masterplan/deploy/deploy.sh /opt/masterplan/deploy/setup-server.sh \
        /opt/masterplan/deploy/management/*.sh
    chmod 755 /opt/masterplan/deploy/ha/*.sh
    ln -sf /opt/masterplan/manage.sh /usr/local/bin/mp-opt
    echo "       Installed the 'mp-opt' management launcher."
fi

echo ""
echo "════════════════════════════════════════════════════════"
echo "  Server setup complete!"
echo ""
if [ "$deploy_user_created" -eq 1 ] || [ "$deploy_had_docker" -eq 0 ]; then
    echo "  Reconnect through SSH as deploy before running Docker or mp-opt."
    echo "  Group membership is only applied to a new login session."
    echo ""
fi
echo "  Management checkout: /opt/masterplan"
echo "  Production software will be installed from a signed stable release."
echo "════════════════════════════════════════════════════════"

if [ -f /var/run/reboot-required ]; then
    echo "A host reboot is required before commissioning. Reboot through your"
    echo "provider or with 'sudo systemctl reboot', reconnect as deploy, then run mp-opt."
    echo "The setup script will not reboot an active SSH session automatically."
    exit 0
fi

if [ "$LAUNCH_TUI" -eq 1 ]; then
    echo "Launching the commissioning TUI as deploy..."
    exec sudo -iu deploy /opt/masterplan/manage.sh
fi
