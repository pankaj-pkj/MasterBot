#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────
#  One-shot VPS setup — Master Hosting Bot
#  Tuned for: 2 vCPU / 4GB RAM / 40GB NVMe, Ubuntu Server
#
#  Usage:
#    chmod +x deploy/setup_vps.sh
#    ./deploy/setup_vps.sh
#
#  Then edit .env with your BOT_TOKEN etc., then:
#    sudo cp deploy/masterbot.service /etc/systemd/system/
#    sudo sed -i "s/__USER__/$USER/g" /etc/systemd/system/masterbot.service
#    sudo systemctl daemon-reload
#    sudo systemctl enable --now masterbot
# ────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

echo "── System packages ──"
sudo apt update -y
sudo apt install -y python3.11 python3.11-venv python3-pip git

echo "── 2GB swap (safety net — 4GB RAM alone can still OOM under a burst) ──"
if [ ! -f /swapfile ]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    # Prefer real RAM; only dip into swap under real pressure.
    sudo sysctl -w vm.swappiness=10
    echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
else
    echo "swap already present, skipping"
fi

echo "── Python venv + dependencies (incl. uvloop speed boost) ──"
python3.11 -m venv venv
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -r requirements.txt -q

echo "── .env template ──"
if [ ! -f .env ]; then
    cat > .env <<'EOF'
BOT_TOKEN=
GITHUB_PAT=
GITHUB_USERNAME=
REPO_NAME=HostedBotsData
RENDER_URL=
NODE_SECRET=
# 4GB RAM is generous — start all persisted bots immediately instead of
# the gradual /st flow (that flow exists for Render's 512MB tier).
AUTOSTART=true
EOF
    echo "→ Wrote .env — fill in BOT_TOKEN (and GITHUB_PAT if you use GitHub sync), then re-run."
else
    echo ".env already exists, leaving it alone"
fi

echo ""
echo "✅ Setup done."
echo "Next:"
echo "  1) nano .env               # fill in BOT_TOKEN"
echo "  2) sudo cp deploy/masterbot.service /etc/systemd/system/"
echo "  3) sudo sed -i \"s/__USER__/\$USER/g\" /etc/systemd/system/masterbot.service"
echo "  4) sudo systemctl daemon-reload && sudo systemctl enable --now masterbot"
echo "  5) journalctl -u masterbot -f     # watch it come up"
