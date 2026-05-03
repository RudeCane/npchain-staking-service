#!/usr/bin/env bash
# NPChain Staking Service — first-time GCP VM provisioning
# Tested on Ubuntu 22.04. Run as root or via sudo.
#
# Usage:
#   ssh into the new VM, then:
#     git clone https://github.com/RudeCane/NPChain.git /tmp/npchain
#     sudo bash /tmp/npchain/staking/deploy/setup.sh
#
# This is a one-shot bootstrap. Re-running is safe (idempotent steps).

set -euo pipefail

INSTALL_DIR=/opt/npchain-staking
LOG_DIR=/var/log/npchain-staking
SERVICE_USER=npchain-staking

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
    python3.11 python3.11-venv python3.11-dev \
    postgresql postgresql-contrib \
    nginx \
    git curl jq \
    ufw

echo "==> Creating service user"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --shell /usr/sbin/nologin --home "$INSTALL_DIR" "$SERVICE_USER"
fi

echo "==> Setting up directories"
mkdir -p "$INSTALL_DIR" "$LOG_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" "$LOG_DIR"

echo "==> Configuring PostgreSQL"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = 'npchain_staking'" | grep -q 1 \
    || sudo -u postgres createdb npchain_staking
sudo -u postgres psql -tc "SELECT 1 FROM pg_user WHERE usename = 'npchain'" | grep -q 1 \
    || sudo -u postgres psql -c "CREATE USER npchain WITH PASSWORD 'CHANGE_ME_IN_ENV';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE npchain_staking TO npchain;"

echo "==> Configuring firewall"
ufw allow OpenSSH
ufw allow 'Nginx HTTP'
ufw --force enable

echo "==> Setup complete."
echo
echo "Next steps:"
echo "  1. Copy the staking service code into $INSTALL_DIR"
echo "  2. As $SERVICE_USER, create the venv and install requirements:"
echo "       sudo -u $SERVICE_USER bash"
echo "       cd $INSTALL_DIR"
echo "       python3.11 -m venv venv"
echo "       venv/bin/pip install -r requirements.txt"
echo "  3. Copy .env.example to .env and fill in production values"
echo "       (especially DB_URL, L1_RPC_URL, and any signing keys)"
echo "  4. Run alembic upgrade head"
echo "  5. Install systemd unit:"
echo "       cp deploy/systemd/npchain-staking.service /etc/systemd/system/"
echo "       systemctl daemon-reload"
echo "       systemctl enable --now npchain-staking"
echo "  6. Install nginx config:"
echo "       cp deploy/nginx/npchain-staking.conf /etc/nginx/sites-available/"
echo "       ln -s /etc/nginx/sites-available/npchain-staking /etc/nginx/sites-enabled/"
echo "       nginx -t && systemctl reload nginx"
echo "  7. Add stake.npchain.org A record in Cloudflare pointing to this VM IP"
echo "  8. Trigger initial snapshot:"
echo "       sudo -u $SERVICE_USER $INSTALL_DIR/venv/bin/python -m src.chain.snapshot"
