"""
╔══════════════════════════════════════════════════════════════════╗
║           MASTER HOSTING BOT  —  main.py                        ║
║  Deploys & manages child Telegram bots on Render.com Free Tier   ║
║  Stack : python-telegram-bot 20.x  |  Python 3.11               ║
╚══════════════════════════════════════════════════════════════════╝

Environment variables required (set in Render dashboard):
  BOT_TOKEN        – Master bot token from @BotFather
  GITHUB_PAT       – GitHub Personal Access Token (repo scope)
  GITHUB_USERNAME  – GitHub account username
  REPO_NAME        – Private repo name used for persistence
  RENDER_URL       – Full URL of this service (https://xxx.onrender.com)
  PORT             – Automatically set by Render (default 8080)
"""

import os
import sys
import time
import asyncio
import shutil
import logging
import subprocess
from zipfile import ZipFile, BadZipFile
from pathlib import Path

import aiohttp
from aiohttp import web

from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("MasterBot")

# ─────────────────────────────────────────────
#  ENVIRONMENT CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN       = os.environ.get("BOT_TOKEN", "")
GITHUB_PAT      = os.environ.get("GITHUB_PAT", "")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "")
REPO_NAME       = os.environ.get("REPO_NAME", "HostedBotsData")
RENDER_URL      = os.environ.get("RENDER_URL", "").rstrip("/")
PORT            = int(os.environ.get("PORT", 8080))

if not BOT_TOKEN:
    log.critical("BOT_TOKEN is not set. Exiting.")
    sys.exit(1)

REPO_URL     = f"https://{GITHUB_PAT}@github.com/{GITHUB_USERNAME}/{REPO_NAME}.git"
HOSTED_DIR   = Path("hosted_bots")          # local working directory for child bots
RUNNING_BOTS: dict[str, dict] = {}          # name → {process, start_time, active, status}

# ─────────────────────────────────────────────
#  GIT ONE-TIME CONFIG
# ─────────────────────────────────────────────
def _configure_git() -> None:
    os.system('git config --global user.email "masterbot@render.com"')
    os.system('git config --global user.name  "MasterHostingBot"')

# ─────────────────────────────────────────────
#  1.  DUMMY WEB SERVER  (Render port binding)
# ─────────────────────────────────────────────
async def _handle_root(request: web.Request) -> web.Response:
    lines = ["<h2>🤖 Master Hosting Bot — Online</h2><ul>"]
    for name, data in RUNNING_BOTS.items():
        lines.append(f"<li><b>{name}</b> — {data.get('status', '?')}</li>")
    lines.append("</ul>")
    return web.Response(text="\n".join(lines), content_type="text/html")

async def _handle_health(request: web.Request) -> web.Response:
    return web.Response(text="OK")

async def start_web_server() -> None:
    """Bind aiohttp to 0.0.0.0:PORT so Render marks the service as healthy."""
    app = web.Application()
    app.router.add_get("/",       _handle_root)
    app.router.add_get("/health", _handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Web server listening on port %d", PORT)

# ─────────────────────────────────────────────
#  2.  ANTI-SLEEP SELF-PING  (every 14 min)
# ─────────────────────────────────────────────
async def keep_alive_loop() -> None:
    """Ping our own Render URL every 14 minutes to prevent cold-start sleep."""
    if not RENDER_URL:
        log.warning("RENDER_URL not set — anti-sleep ping disabled.")
        return

    await asyncio.sleep(30)                          # give server a moment to start
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(
                    f"{RENDER_URL}/health",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    log.info("Self-ping → %s %d", RENDER_URL, resp.status)
            except Exception as exc:
                log.warning("Self-ping failed: %s", exc)
            await asyncio.sleep(840)                 # 14 × 60 seconds

# ─────────────────────────────────────────────
#  3.  GITHUB SYNC
# ─────────────────────────────────────────────
def sync_from_github() -> None:
    """Clone (or pull) the persistence repo so previously hosted bots survive restarts."""
    if not GITHUB_PAT:
        log.warning("GITHUB_PAT not set — GitHub sync disabled.")
        HOSTED_DIR.mkdir(exist_ok=True)
        return

    if HOSTED_DIR.exists():
        log.info("Pulling latest data from GitHub…")
        ret = os.system(
            f'cd "{HOSTED_DIR}" && git pull 2>&1'
        )
        if ret != 0:
            log.warning("git pull failed — re-cloning…")
            shutil.rmtree(HOSTED_DIR, ignore_errors=True)
            os.system(f'git clone "{REPO_URL}" "{HOSTED_DIR}" 2>&1')
    else:
        log.info("Cloning GitHub repo…")
        ret = os.system(f'git clone "{REPO_URL}" "{HOSTED_DIR}" 2>&1')
        if ret != 0:
            log.error("git clone failed — creating empty local directory.")
            HOSTED_DIR.mkdir(exist_ok=True)

def push_to_github(commit_msg: str = "Bot update") -> None:
    """Stage, commit, and push all changes in HOSTED_DIR."""
    if not GITHUB_PAT:
        return
    log.info("Pushing to GitHub: %s", commit_msg)
    cmds = (
        f'cd "{HOSTED_DIR}" && '
        f'git add -A && '
        f'git diff --cached --quiet || '          # skip commit if nothing changed
        f'git commit -m "{commit_msg}" && '
        f'git push 2>&1'
    )
    ret = os.system(cmds)
    if ret != 0:
        log.warning("GitHub push finished with code %d", ret)

# ─────────────────────────────────────────────
#  4.  CHILD-BOT RUNNER WITH AUTO-RESTART
# ─────────────────────────────────────────────
async def _run_and_monitor(bot_name: str, bot_dir: Path) -> None:
    """
    Launch 'python main.py' inside *bot_dir* and restart it automatically
    whenever it crashes — until RUNNING_BOTS[bot_name]['active'] is False.
    """
    RUNNING_BOTS[bot_name] = {
        "active":     True,
        "start_time": time.time(),
        "status":     "Starting ⏳",
        "process":    None,
    }

    while RUNNING_BOTS.get(bot_name, {}).get("active"):
        log.info("Launching child bot: %s", bot_name)
        RUNNING_BOTS[bot_name]["status"]     = "Running 🟢"
        RUNNING_BOTS[bot_name]["start_time"] = time.time()

        try:
            proc = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd=str(bot_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except Exception as exc:
            log.error("Failed to start %s: %s", bot_name, exc)
            RUNNING_BOTS[bot_name]["status"] = "Error ❌"
            await asyncio.sleep(10)
            continue

        RUNNING_BOTS[bot_name]["process"] = proc

        # Wait for the process to end without blocking the event loop
        while proc.poll() is None:
            await asyncio.sleep(2)

        exit_code = proc.returncode
        log.warning("%s exited with code %d", bot_name, exit_code)

        if not RUNNING_BOTS.get(bot_name, {}).get("active"):
            RUNNING_BOTS[bot_name]["status"] = "Stopped 🛑"
            break

        RUNNING_BOTS[bot_name]["status"] = "Restarting ⏳"
        log.info("Auto-restarting %s in 5 s…", bot_name)
        await asyncio.sleep(5)

def _install_requirements(req_path: Path) -> None:
    log.info("Installing dependencies from %s", req_path)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req_path), "-q"],
        check=False,
    )

# ─────────────────────────────────────────────
#  5.  TELEGRAM COMMAND & MESSAGE HANDLERS
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🤖 *Master Hosting Bot is Live!*\n\n"
        "I can host your Telegram bots permanently on this server.\n\n"
        "📦 *How to deploy a bot:*\n"
        "Upload a `.zip` file containing `main.py` (and optionally "
        "`requirements.txt`).\n\n"
        "🔧 *Commands:*\n"
        "/all — List all hosted bots & their status\n"
        "/stop `<name>` — Stop a running bot\n"
        "/start — Show this message"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_list_bots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not RUNNING_BOTS:
        await update.message.reply_text("📭 No bots are currently hosted.")
        return

    lines = ["📊 *Hosted Bots Status:*\n"]
    for name, data in RUNNING_BOTS.items():
        elapsed = int(time.time() - data.get("start_time", time.time()))
        h, rem  = divmod(elapsed, 3600)
        m, s    = divmod(rem, 60)
        uptime  = f"`{h:02d}h {m:02d}m {s:02d}s`"
        status  = data.get("status", "Unknown")
        lines.append(f"🔹 *{name}*\n   Status : {status}\n   Uptime : {uptime}\n")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: `/stop <bot_name>`", parse_mode="Markdown"
        )
        return

    bot_name = context.args[0]
    entry    = RUNNING_BOTS.get(bot_name)

    if not entry:
        await update.message.reply_text(
            f"❌ No bot named *{bot_name}* found.", parse_mode="Markdown"
        )
        return

    entry["active"] = False
    proc = entry.get("process")
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    entry["status"] = "Stopped 🛑"
    await update.message.reply_text(
        f"🛑 *{bot_name}* has been stopped.", parse_mode="Markdown"
    )


async def handle_zip_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive a .zip file, extract it, install deps, and launch the child bot."""
    doc       = update.message.document
    file_name = doc.file_name or ""

    if not file_name.lower().endswith(".zip"):
        await update.message.reply_text(
            "⚠️ Please send a `.zip` file containing your bot.", parse_mode="Markdown"
        )
        return

    bot_name   = Path(file_name).stem
    status_msg = await update.message.reply_text(f"⏳ Receiving *{bot_name}*…", parse_mode="Markdown")

    # ── Download ──────────────────────────────
    HOSTED_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = HOSTED_DIR / file_name
    tg_file  = await context.bot.get_file(doc.file_id)
    await tg_file.download_to_drive(str(zip_path))

    # ── Extract ───────────────────────────────
    bot_dir = HOSTED_DIR / bot_name
    if bot_dir.exists():
        shutil.rmtree(bot_dir)
    bot_dir.mkdir(parents=True)

    try:
        with ZipFile(zip_path, "r") as zf:
            # Flatten one extra nesting level if zip has a single root folder
            members = zf.namelist()
            strip   = ""
            if all(m.startswith(members[0].split("/")[0] + "/") for m in members if "/" in m):
                strip = members[0].split("/")[0] + "/"
            for member in members:
                dest_rel = member[len(strip):] if strip else member
                if not dest_rel:
                    continue
                dest_path = bot_dir / dest_rel
                if member.endswith("/"):
                    dest_path.mkdir(parents=True, exist_ok=True)
                else:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    dest_path.write_bytes(zf.read(member))
    except BadZipFile:
        zip_path.unlink(missing_ok=True)
        await status_msg.edit_text("❌ The file is not a valid ZIP archive.")
        return
    finally:
        zip_path.unlink(missing_ok=True)

    # ── Validate ──────────────────────────────
    main_py = bot_dir / "main.py"
    if not main_py.exists():
        shutil.rmtree(bot_dir, ignore_errors=True)
        await status_msg.edit_text(
            "❌ `main.py` not found inside the ZIP.\n"
            "Make sure your bot's entry point is named `main.py`.",
            parse_mode="Markdown",
        )
        return

    # ── Install requirements ───────────────────
    req_file = bot_dir / "requirements.txt"
    if req_file.exists():
        await status_msg.edit_text(f"⚙️ Installing dependencies for *{bot_name}*…", parse_mode="Markdown")
        _install_requirements(req_file)

    # ── Stop old instance if re-deploying ─────
    if bot_name in RUNNING_BOTS:
        old = RUNNING_BOTS[bot_name]
        old["active"] = False
        p = old.get("process")
        if p and p.poll() is None:
            p.terminate()
        await asyncio.sleep(2)

    # ── GitHub sync ───────────────────────────
    push_to_github(f"Deployed bot: {bot_name}")

    # ── Launch ────────────────────────────────
    asyncio.create_task(_run_and_monitor(bot_name, bot_dir))
    await status_msg.edit_text(
        f"✅ *{bot_name}* deployed and running!\n\n"
        f"Use /all to see all hosted bots.",
        parse_mode="Markdown",
    )

# ─────────────────────────────────────────────
#  6.  AUTO-START PREVIOUSLY HOSTED BOTS
# ─────────────────────────────────────────────
async def autostart_existing_bots() -> None:
    """After a Render restart, re-launch every bot found in HOSTED_DIR."""
    if not HOSTED_DIR.exists():
        return
    for item in HOSTED_DIR.iterdir():
        if not item.is_dir() or item.name.startswith("."):
            continue
        main_py = item / "main.py"
        if not main_py.exists():
            continue
        req_file = item / "requirements.txt"
        if req_file.exists():
            _install_requirements(req_file)
        log.info("Auto-starting previously hosted bot: %s", item.name)
        asyncio.create_task(_run_and_monitor(item.name, item))

# ─────────────────────────────────────────────
#  7.  MAIN ENTRY-POINT
# ─────────────────────────────────────────────
async def main() -> None:
    log.info("═══════════════════════════════════════")
    log.info("     Master Hosting Bot  —  Starting   ")
    log.info("═══════════════════════════════════════")

    _configure_git()

    # Pull persisted bots from GitHub
    sync_from_github()

    # ── Build the Telegram application ────────
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("all",   cmd_list_bots))
    app.add_handler(CommandHandler("stop",  cmd_stop_bot))
    app.add_handler(MessageHandler(filters.Document.ZIP, handle_zip_upload))

    # ── Start aiohttp web server first ────────
    # Render pings the port immediately; bind before doing anything else.
    await start_web_server()

    # ── Anti-sleep ping loop ──────────────────
    asyncio.create_task(keep_alive_loop())

    # ── Re-launch previously hosted bots ──────
    await autostart_existing_bots()

    # ── CRITICAL: delete webhook & clear queue ─
    # Prevents "Conflict: terminated by other getUpdates request" on Render
    # which spins up two instances on cold-start.
    log.info("Clearing any existing webhook / pending updates…")
    await app.bot.delete_webhook(drop_pending_updates=True)

    # ── Proper PTB 20.x lifecycle ─────────────
    # Must follow: initialize → start → start_polling
    log.info("Initializing application…")
    await app.initialize()

    log.info("Starting application…")
    await app.start()

    log.info("Starting polling…")
    await app.updater.start_polling(
        drop_pending_updates=True,          # ignore queued-up messages
        allowed_updates=Update.ALL_TYPES,
    )

    # Set bot commands menu (cosmetic)
    try:
        await app.bot.set_my_commands([
            BotCommand("start", "Welcome & instructions"),
            BotCommand("all",   "List all hosted bots"),
            BotCommand("stop",  "Stop a hosted bot"),
        ])
    except Exception:
        pass

    log.info("✅ Master Hosting Bot is fully operational!")

    # ── Block forever ─────────────────────────
    # asyncio.Event().wait() keeps the loop alive without burning CPU.
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user — shutting down.")

