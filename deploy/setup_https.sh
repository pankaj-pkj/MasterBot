#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────
#  HTTPS setup — Caddy reverse proxy with automatic Let's Encrypt SSL
#
#  BEFORE running this, point your subdomain at this server:
#    In your DNS panel (where codianstudio.org is managed) add:
#        Type: A     Name: bot     Value: <THIS SERVER'S PUBLIC IP>
#    Wait ~2 minutes, then verify:
#        dig +short bot.codianstudio.org
#    It must print this server's IP before Caddy can get a certificate.
#
#  Usage:
#    ./deploy/setup_https.sh bot.codianstudio.org
# ────────────────────────────────────────────────────────────────────
set -euo pipefail

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
    echo "Usage: $0 <your.subdomain.com>"
    echo "Example: $0 bot.codianstudio.org"
    exit 1
fi

cd "$(dirname "$0")/.."

echo "── Checking DNS for $DOMAIN ──"
SERVER_IP="$(curl -fsS https://api.ipify.org || echo '')"
DNS_IP="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || echo '')"
echo "   This server : ${SERVER_IP:-unknown}"
echo "   DNS resolves: ${DNS_IP:-NOT SET}"
if [ -z "$DNS_IP" ]; then
    echo ""
    echo "❌ $DOMAIN does not resolve yet."
    echo "   Add an A record pointing 'bot' to $SERVER_IP, wait ~2 min, retry."
    exit 1
fi
if [ -n "$SERVER_IP" ] && [ "$DNS_IP" != "$SERVER_IP" ]; then
    echo ""
    echo "⚠️  DNS points to $DNS_IP but this server is $SERVER_IP."
    echo "   If you use Cloudflare, set the record to 'DNS only' (grey cloud)"
    echo "   so Caddy can complete the certificate challenge."
    read -r -p "   Continue anyway? [y/N] " ok
    [ "$ok" = "y" ] || exit 1
fi

echo "── Installing Caddy ──"
if ! command -v caddy >/dev/null 2>&1; then
    sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    sudo apt update -y
    sudo apt install -y caddy
else
    echo "caddy already installed"
fi

echo "── Writing Caddyfile for $DOMAIN ──"
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i "s|bot\.codianstudio\.org|$DOMAIN|g" /etc/caddy/Caddyfile

echo "── Firewall: open 80/443, close direct access to 8080 ──"
if command -v ufw >/dev/null 2>&1; then
    sudo ufw allow 80/tcp
    sudo ufw allow 443/tcp
    # 8080 must NOT be public — traffic should only arrive via HTTPS.
    sudo ufw delete allow 8080/tcp 2>/dev/null || true
fi

echo "── Reloading Caddy (it will fetch the certificate now) ──"
sudo systemctl enable caddy
sudo systemctl restart caddy
sleep 5
sudo systemctl --no-pager status caddy | head -12

echo ""
echo "── Updating .env ──"
if grep -q '^RENDER_URL=' .env 2>/dev/null; then
    sed -i "s|^RENDER_URL=.*|RENDER_URL=https://$DOMAIN|" .env
else
    echo "RENDER_URL=https://$DOMAIN" >> .env
fi
echo "   RENDER_URL=https://$DOMAIN"

echo ""
echo "✅ HTTPS ready:  https://$DOMAIN"
echo ""
echo "Restart the bot so /ide uses the new URL:"
echo "    sudo systemctl restart masterbot"
echo "Then send /ide in Telegram — the link will be https://$DOMAIN/editor?token=..."
