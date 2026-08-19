#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────
#  Install the systemd service so the bot survives reboots/disconnects.
#
#  Works for any user INCLUDING root (root's home is /root, not
#  /home/root — hardcoding /home/$USER breaks for root).
#
#  Usage:  ./deploy/install_service.sh
# ────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$PWD"
WHO="$(id -un)"

if [ ! -f "$DIR/.env" ]; then
    echo "❌ $DIR/.env not found. Run ./deploy/setup_vps.sh first, then add BOT_TOKEN."
    exit 1
fi
if ! grep -qE '^BOT_TOKEN=.+' "$DIR/.env"; then
    echo "❌ BOT_TOKEN is empty in $DIR/.env"
    echo "   Run:  nano .env    and fill in BOT_TOKEN=your_token"
    exit 1
fi
if [ ! -x "$DIR/venv/bin/python" ]; then
    echo "❌ venv missing. Run ./deploy/setup_vps.sh first."
    exit 1
fi

echo "── Installing service ──"
echo "   user: $WHO"
echo "   dir : $DIR"

sudo cp deploy/masterbot.service /etc/systemd/system/masterbot.service
sudo sed -i "s|__USER__|$WHO|g; s|__DIR__|$DIR|g" /etc/systemd/system/masterbot.service

sudo systemctl daemon-reload
sudo systemctl enable masterbot
sudo systemctl restart masterbot
sleep 3

if systemctl is-active --quiet masterbot; then
    echo ""
    echo "✅ Bot is running."
    echo "   Live logs:  journalctl -u masterbot -f"
else
    echo ""
    echo "❌ Bot failed to start. Last 30 log lines:"
    sudo journalctl -u masterbot -n 30 --no-pager
    exit 1
fi
