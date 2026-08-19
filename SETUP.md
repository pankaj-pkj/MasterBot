# Master Hosting Bot — Complete Setup (Termux → VPS → HTTPS)

Zero se full setup. Har step order me karo — DNS pehle, kyunki wo propagate
hone me time leta hai.

---

## Aapko ye 4 cheezein chahiye

| Cheez | Kahan se |
|---|---|
| VPS ka **public IP** | Dost se (jisne VPS diya) |
| VPS ka **username + password** | Dost se (usually `root` ya `ubuntu`) |
| **BOT_TOKEN** | Telegram pe [@BotFather](https://t.me/BotFather) → `/newbot` |
| **Domain access** | Cloudflare dashboard (codianstudio.org) |

---

## STEP 1 — Cloudflare me subdomain banao

Cloudflare dashboard → `codianstudio.org` → **DNS** → **Add record**

| Field | Value |
|---|---|
| Type | `A` |
| Name | `bot` |
| IPv4 address | *(VPS ka public IP)* |
| Proxy status | **DNS only (grey cloud ☁️)** |
| TTL | Auto |

> ### ⚠️ Grey cloud CRITICAL hai
> Orange cloud (proxied) rakhoge to Caddy ko SSL certificate **nahi milega**
> aur IDE ka WebSocket (live logs) bhi toot sakta hai. Record banane ke baad
> cloud icon pe click karke **grey** kar do.

Isse ban jayega: **`bot.codianstudio.org`**

---

## STEP 2 — Termux setup (phone pe)

Termux khol kar:

```bash
pkg update -y && pkg upgrade -y
pkg install openssh -y
```

DNS check karo (2 min wait karne ke baad):
```bash
nslookup bot.codianstudio.org
```
VPS ka IP dikhna chahiye. Na dikhe to thoda aur wait karo.

---

## STEP 3 — VPS se connect

```bash
ssh username@VPS_IP
```
Password maange to daal do (typing dikhegi nahi — normal hai).

Ab aap VPS ke andar ho. **Aage ke saare commands VPS ke andar chalenge.**

---

## STEP 4 — Bot install

```bash
git clone https://github.com/pankaj-pkj/MasterBot.git
cd MasterBot
git checkout claude/hosting-bot-ui-perf-20cygb
chmod +x deploy/*.sh
./deploy/setup_vps.sh
```

Ye khud install karega: Python, venv, dependencies, 2GB swap.

---

## STEP 5 — Token daalo

```bash
nano .env
```

`BOT_TOKEN=` ke aage BotFather wala token paste karo:
```
BOT_TOKEN=1234567890:AAbbCCddEEff...
```

Save: `Ctrl+O` → `Enter` → `Ctrl+X`

> **Admin ID:** `main.py` me `ADMIN_IDS` set hai. Apna Telegram ID daalna ho to
> [@userinfobot](https://t.me/userinfobot) se ID lo aur `main.py` line ~78 me
> badal do.

---

## STEP 6 — HTTPS + domain

```bash
./deploy/setup_https.sh bot.codianstudio.org
```

Ye khud karega:
- DNS verify (galat hoga to pehle hi batayega)
- Caddy install + **free auto-renewing SSL**
- Port 80/443 open, **8080 public band**
- `.env` me `RENDER_URL=https://bot.codianstudio.org` likhega

---

## STEP 7 — Bot ko permanent chalu karo

```bash
sudo cp deploy/masterbot.service /etc/systemd/system/
sudo sed -i "s/__USER__/$USER/g" /etc/systemd/system/masterbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now masterbot
```

Check karo:
```bash
systemctl status masterbot
journalctl -u masterbot -f     # live logs — Ctrl+C se bahar
```

---

## STEP 8 — Test

1. Telegram me apne bot ko `/start` bhejo → menu aana chahiye
2. `/ide` bhejo → link aayega: `https://bot.codianstudio.org/editor?token=...`
3. Link kholo → IDE khulega (file banao, edit karo, ▶ Run)

---

## Ho gaya — ab Termux band kar sakte ho

`systemctl enable` ka matlab:
- VPS reboot ho → bot **auto-start**
- Bot crash ho → **auto-restart**
- SSH/Termux band karo → **bot chalta rahega**

Termux delete bhi kar sakte ho, lekin **rakhna better hai** — future me fix
ya update ke liye SSH chahiye hoga.

---

## Update kaise karein (baad me)

```bash
ssh username@VPS_IP
cd MasterBot
git pull
./venv/bin/pip install -r requirements.txt -q
sudo systemctl restart masterbot
```

---

## Kuch galat ho to

| Problem | Fix |
|---|---|
| Bot start nahi ho raha | `journalctl -u masterbot -n 50` — error dekho |
| SSL nahi mila | Cloudflare me **grey cloud** confirm karo, phir `sudo systemctl restart caddy` |
| `/ide` localhost dikha raha | `.env` me `RENDER_URL` check karo, phir bot restart |
| Site nahi khul rahi | `sudo systemctl status caddy` aur `sudo ufw status` |
| RAM full | `/ram` (admin) ya `/usage` bhejo bot me |

---

## Limits (badalne ho to `main.py` top me)

| | Free | Starter 15⭐ | Pro 50⭐ | Elite 100⭐ |
|---|---|---|---|---|
| Bots | 2 | +3 | +10 | +25 |
| RAM | 200MB | +512MB | +1GB | +2GB |

Limit **RAM pe hai** — user ka bot jitne bhi sub-process banaye, sab uske
quota me count honge.

---

## Admin commands

| Command | Kaam |
|---|---|
| `/st` | Idle bots ek-ek karke start karo |
| `/ram` | Server ki total memory |
| `/usage` | Apni RAM usage (sab users ke liye) |
| `/setram <id> <mb>` | Kisi user ka RAM quota set karo |
| `/pr <id> <slots>` | Bot slots set karo |
| `/nodes` | Worker nodes (delete button ke saath) |
| `/all` `/users` | Sab bots / users |
| `/msg` | Broadcast |
