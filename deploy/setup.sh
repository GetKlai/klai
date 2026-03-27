#!/usr/bin/env bash
# core-01 initial setup — run as root directly after first SSH login
#
# Usage:
#   bash setup.sh
#
# Requires config to be loaded first. From the deploy directory, decrypt config:
#   sops --decrypt config.sops.env > config.env
#   source config.env
set -euo pipefail

# Load config from same directory if not already in environment
CONFIG_DIR="$(dirname "$0")"
if [ -z "${SERVER_USER:-}" ]; then
    CONFIG_PLAIN="$CONFIG_DIR/config.env"
    CONFIG_SOPS="$CONFIG_DIR/config.sops.env"
    if [ -f "$CONFIG_PLAIN" ]; then
        # shellcheck source=/dev/null
        source "$CONFIG_PLAIN"
    elif [ -f "$CONFIG_SOPS" ] && command -v sops &>/dev/null; then
        eval "$(SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}" \
            sops --decrypt --output-type dotenv "$CONFIG_SOPS")"
    else
        echo "ERROR: No config found. Decrypt config.sops.env first:"
        echo "  sops --decrypt config.sops.env > config.env && source config.env"
        exit 1
    fi
fi

echo "=== [1/7] System update ==="
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq curl git htop unzip ufw fail2ban

echo "=== [2/7] Hostname, timezone and NTP ==="
hostnamectl set-hostname "$SERVER_HOST"
timedatectl set-timezone Europe/Helsinki
echo "$SERVER_HOST" > /etc/hostname

echo "Enabling NTP synchronisation (systemd-timesyncd)..."
timedatectl set-ntp true
sleep 3
timedatectl timesync-status || true
echo "NTP status logged above (A.8.17 evidence)."

echo "=== [3/7] Create deploy user ==="
if ! id "$SERVER_USER" &>/dev/null; then
    useradd -m -s /bin/bash -G sudo "$SERVER_USER"
fi
mkdir -p /home/$SERVER_USER/.ssh
echo "$SSH_PUBKEY" > /home/$SERVER_USER/.ssh/authorized_keys
chmod 700 /home/$SERVER_USER/.ssh
chmod 600 /home/$SERVER_USER/.ssh/authorized_keys
chown -R $SERVER_USER:$SERVER_USER /home/$SERVER_USER/.ssh

# Passwordless sudo for deploy user
echo "$SERVER_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/$SERVER_USER
chmod 440 /etc/sudoers.d/$SERVER_USER

echo "=== [4/7] Harden SSH ==="
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config
systemctl reload ssh

echo "=== [5/7] UFW firewall ==="
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "SSH"
ufw allow 80/tcp comment "HTTP (Caddy ACME)"
ufw allow 443/tcp comment "HTTPS"
ufw --force enable

echo "=== [6/7] Install Docker ==="
curl -fsSL https://get.docker.com | sh
usermod -aG docker $SERVER_USER
systemctl enable docker
systemctl start docker

echo "=== [7/7] Create directory structure ==="
mkdir -p /opt/klai/{caddy/{data,config},zitadel,mongodb/data,postgres/data,redis/data,litellm,meilisearch/data,librechat/_template,alloy,secrets,portal-dist}
chown -R $SERVER_USER:$SERVER_USER /opt/klai

echo ""
echo "========================================"
echo " core-01 setup complete!"
echo " Verify access: ssh $SERVER_USER@$SERVER_IP"
echo " Root login is now disabled."
echo "========================================"
