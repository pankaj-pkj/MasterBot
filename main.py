"""
╔══════════════════════════════════════════════════════════════════════╗
║   MASTER HOSTING BOT  v2.0  —  Advanced Edition                     ║
║   python-telegram-bot 20.x  ·  Python 3.11  ·  Render.com           ║
║   Codian Studio 💎                                                   ║
╠══════════════════════════════════════════════════════════════════════╣
║  Features:                                                           ║
║  • Per-user slot system (Free = 3 slots)                             ║
║  • Telegram Stars payment plans (15 / 50 / 100 ⭐)                  ║
║  • Admin: /pr <id> <slots>  to grant custom slots                    ║
║  • Full Inline Keyboard UI                                           ║
║  • Smart crash detection — stops after 3 fast crashes, shows error   ║
║  • Bot speed / stability rating                                      ║
║  • Live log viewer via button                                        ║
║  • User can stop / delete their own bots                             ║
║  • .zip  .py  nested-folder zips — all supported                     ║
║  • requirements.txt auto-detected and installed                      ║
║  • /stop fix — full name with spaces works correctly                 ║
║  • /all  — admin only                                                ║
║  • GitHub sync for Render ephemeral-fs persistence                   ║
╚══════════════════════════════════════════════════════════════════════╝

Required Render env-vars:
  BOT_TOKEN        — Master bot token from @BotFather
  GITHUB_PAT       — GitHub PAT with "repo" scope
  GITHUB_USERNAME  — Your GitHub username
  REPO_NAME        — Private repo name for persistence
  RENDER_URL       — https://your-app.onrender.com
  PORT             — Set automatically by Render (default 8080)
"""

# ── stdlib ────────────────────────────────────────────────────────────
import os, sys, time, asyncio, shutil, logging, subprocess, json
from zipfile import ZipFile, BadZipFile
from pathlib  import Path

# ── third-party ───────────────────────────────────────────────────────
import aiohttp
from aiohttp import web

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    LabeledPrice,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)

# ══════════════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format  = "%(asctime)s │ %(levelname)-8s │ %(message)s",
    datefmt = "%H:%M:%S",
    level   = logging.INFO,
    stream  = sys.stdout,
)
log = logging.getLogger("MasterBot")


# ══════════════════════════════════════════════════════════════════════
#  ENV CONFIG
# ══════════════════════════════════════════════════════════════════════
BOT_TOKEN       = os.environ.get("BOT_TOKEN",       "")
GITHUB_PAT      = os.environ.get("GITHUB_PAT",      "")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "")
REPO_NAME       = os.environ.get("REPO_NAME",       "HostedBotsData")
RENDER_URL      = os.environ.get("RENDER_URL",      "").rstrip("/")
PORT            = int(os.environ.get("PORT",         8080))

if not BOT_TOKEN:
    log.critical("BOT_TOKEN not set — aborting.")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════
ADMIN_IDS: set[int] = {6960252072}   # ← add more admin IDs here

FREE_SLOTS       = 3     # every new user starts with this many slots
MAX_FAST_CRASHES = 3     # give up auto-restart after this many quick crashes
FAST_CRASH_SEC   = 30    # crash within N seconds of launch = "fast crash"

PLANS: dict[str, dict] = {
    "starter": {"stars": 15,  "slots": 3,  "label": "Starter ⭐",  "desc": "+3 extra bot slots"},
    "pro":     {"stars": 50,  "slots": 10, "label": "Pro 💎",      "desc": "+10 extra bot slots"},
    "elite":   {"stars": 100, "slots": 25, "label": "Elite 👑",    "desc": "+25 extra bot slots"},
}

REPO_URL    = f"https://{GITHUB_PAT}@github.com/{GITHUB_USERNAME}/{REPO_NAME}.git"
HOSTED_DIR  = Path("hosted_bots")          # child-bot working root
DATA_DIR    = HOSTED_DIR / "_data"         # JSON persistence lives here

# Runtime state — rebuilt from disk every restart
RUNNING_BOTS: dict[str, dict] = {}

# Global PTB app reference (set inside main())
_APP = None


# ══════════════════════════════════════════════════════════════════════
#  JSON PERSISTENCE  (stored inside the GitHub repo)
# ══════════════════════════════════════════════════════════════════════

def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Users ──────────────────────────────────────────────────────────────
def load_users() -> dict:
    return _load_json(DATA_DIR / "users.json", {})


def save_users(d: dict) -> None:
    _save_json(DATA_DIR / "users.json", d)


def get_user(uid: int) -> dict:
    """Return user record; create with defaults if missing."""
    data = load_users()
    k    = str(uid)
    if k not in data:
        data[k] = {"slots": FREE_SLOTS, "stars_spent": 0}
        save_users(data)
    return data[k]


def set_user_slots(uid: int, slots: int) -> None:
    data    = load_users()
    k       = str(uid)
    data[k] = data.get(k, {"stars_spent": 0})
    data[k]["slots"] = slots
    save_users(data)


def add_user_slots(uid: int, extra: int) -> int:
    u   = get_user(uid)
    new = u["slots"] + extra
    set_user_slots(uid, new)
    return new


def add_user_stars(uid: int, stars: int) -> None:
    data    = load_users()
    k       = str(uid)
    data[k] = data.get(k, {"slots": FREE_SLOTS})
    data[k]["stars_spent"] = data[k].get("stars_spent", 0) + stars
    save_users(data)


# ── Bot Registry ───────────────────────────────────────────────────────
def load_registry() -> dict:
    return _load_json(DATA_DIR / "registry.json", {})


def save_registry(d: dict) -> None:
    _save_json(DATA_DIR / "registry.json", d)


def register_bot(name: str, uid: int) -> None:
    reg      = load_registry()
    reg[name] = {"owner_id": uid, "registered_at": time.time()}
    save_registry(reg)


def unregister_bot(name: str) -> None:
    reg = load_registry()
    reg.pop(name, None)
    save_registry(reg)


def get_used_slots(uid: int) -> int:
    return sum(
        1 for v in load_registry().values()
        if str(v.get("owner_id")) == str(uid)
    )


def get_user_bots(uid: int) -> list[str]:
    return [k for k, v in load_registry().items()
            if str(v.get("owner_id")) == str(uid)]


# ══════════════════════════════════════════════════════════════════════
#  GIT / GITHUB SYNC
# ══════════════════════════════════════════════════════════════════════

def _git(cmd: str) -> int:
    return os.system(cmd + " > /dev/null 2>&1")


def configure_git() -> None:
    _git('git config --global user.email "masterbot@render.com"')
    _git('git config --global user.name  "MasterHostingBot"')


def sync_from_github() -> None:
    if not GITHUB_PAT:
        log.warning("GITHUB_PAT not set — local storage only.")
        HOSTED_DIR.mkdir(exist_ok=True)
        return

    git_dir = HOSTED_DIR / ".git"
    if HOSTED_DIR.exists() and git_dir.exists():
        log.info("Pulling latest data from GitHub…")
        if _git(f'cd "{HOSTED_DIR}" && git pull') != 0:
            log.warning("git pull failed — re-cloning.")
            shutil.rmtree(HOSTED_DIR, ignore_errors=True)
            _clone()
    else:
        shutil.rmtree(HOSTED_DIR, ignore_errors=True)
        _clone()


def _clone() -> None:
    log.info("Cloning GitHub repo…")
    if _git(f'git clone "{REPO_URL}" "{HOSTED_DIR}"') != 0:
        log.error("Clone failed — using local directory only.")
        HOSTED_DIR.mkdir(exist_ok=True)


def push_to_github(msg: str = "Update") -> None:
    if not GITHUB_PAT:
        return
    _git(
        f'cd "{HOSTED_DIR}" && git add -A && '
        f'(git diff --cached --quiet || git commit -m "{msg}") && git push'
    )


# ══════════════════════════════════════════════════════════════════════
#  AIOHTTP WEB SERVER  (Render port-binding + status page)
# ══════════════════════════════════════════════════════════════════════

async def _web_root(req: web.Request) -> web.Response:
    rows = "".join(
        f"<tr><td><b>{n}</b></td>"
        f"<td>{d.get('status','?')}</td>"
        f"<td>{fmt_uptime(time.time()-d.get('start_time', time.time()))}</td>"
        f"<td>{d.get('restarts',0)}</td></tr>"
        for n, d in RUNNING_BOTS.items()
    )
    html = (
        "<html><head><title>Master Hosting Bot</title>"
        "<style>body{font-family:monospace;padding:20px}"
        "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:8px}"
        "</style></head><body>"
        "<h2>🤖 Master Hosting Bot — Live</h2>"
        f"<table><tr><th>Bot</th><th>Status</th><th>Uptime</th><th>Restarts</th></tr>{rows}</table>"
        "<p><i>Codian Studio 💎</i></p></body></html>"
    )
    return web.Response(text=html, content_type="text/html")


async def start_web_server() -> None:
    wa = web.Application()
    wa.router.add_get("/",       _web_root)
    wa.router.add_get("/health", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(wa)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Web server listening on port %d", PORT)


# ══════════════════════════════════════════════════════════════════════
#  ANTI-SLEEP SELF-PING  (every 14 minutes)
# ══════════════════════════════════════════════════════════════════════

async def keep_alive_loop() -> None:
    if not RENDER_URL:
        log.warning("RENDER_URL not set — anti-sleep disabled.")
        return
    await asyncio.sleep(90)                  # warm-up grace period
    async with aiohttp.ClientSession() as sess:
        while True:
            try:
                async with sess.get(
                    f"{RENDER_URL}/health",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    log.info("Anti-sleep ping → HTTP %d", resp.status)
            except Exception as exc:
                log.warning("Ping failed: %s", exc)
            await asyncio.sleep(840)         # 14 × 60 s


# ══════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════

def fmt_uptime(secs: float) -> str:
    s      = int(max(0, secs))
    h, r   = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h}h {m}m {sec}s"


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def speed_label(entry: dict) -> str:
    r = entry.get("restarts", 0)
    if r == 0:  return "🚀 Excellent"
    if r < 3:   return "⚡ Good"
    if r < 10:  return "🐢 Unstable"
    return              "💀 Critical"


def _tail(path: Path, n: int = 40) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return ""


def _install_reqs(req: Path) -> None:
    log.info("Installing %s", req)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req), "-q"],
        check=False, timeout=180,
    )


# ══════════════════════════════════════════════════════════════════════
#  CHILD-BOT RUNNER  (smart auto-restart + error capture + DM notify)
# ══════════════════════════════════════════════════════════════════════

async def run_bot(name: str, bot_dir: Path, owner_id: int) -> None:
    """
    Launch  python main.py  inside *bot_dir*.

    Auto-restarts on crash UNLESS:
      • Crashed < FAST_CRASH_SEC  ×  MAX_FAST_CRASHES times in a row
        → auto-stop and DM the owner with the captured error log.
      • RUNNING_BOTS[name]['active'] == False  (manual stop / delete).
    """
    log_path = bot_dir / "bot_output.log"

    RUNNING_BOTS[name] = {
        "active":     True,
        "start_time": time.time(),
        "status":     "Starting ⏳",
        "process":    None,
        "restarts":   0,
        "last_error": None,
        "owner_id":   owner_id,
    }

    fast_crashes = 0

    while RUNNING_BOTS.get(name, {}).get("active"):
        t0 = time.time()
        RUNNING_BOTS[name]["status"] = "Running 🟢"

        # ── Launch subprocess ────────────────────────────────────────
        try:
            lf   = open(log_path, "a", encoding="utf-8", errors="replace")
            proc = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd    = str(bot_dir),
                stdout = lf,
                stderr = lf,
            )
        except Exception as exc:
            RUNNING_BOTS[name].update(
                status    = "Launch Error ❌",
                active    = False,
                last_error= str(exc),
            )
            await _notify(owner_id, f"❌ *{name}* failed to launch:\n`{exc}`")
            break
        finally:
            try: lf.close()
            except Exception: pass

        RUNNING_BOTS[name]["process"] = proc

        # ── Wait for exit without blocking the event loop ────────────
        while proc.poll() is None:
            await asyncio.sleep(2)

        runtime = time.time() - t0
        tail    = _tail(log_path)
        RUNNING_BOTS[name]["last_error"] = tail
        RUNNING_BOTS[name]["restarts"]  += 1

        # Manual stop was requested
        if not RUNNING_BOTS.get(name, {}).get("active"):
            RUNNING_BOTS[name]["status"] = "Stopped 🛑"
            break

        log.warning("%s exited (code=%s, runtime=%.1fs)", name, proc.returncode, runtime)

        # ── Smart crash logic ────────────────────────────────────────
        if runtime < FAST_CRASH_SEC:
            fast_crashes += 1
            if fast_crashes >= MAX_FAST_CRASHES:
                RUNNING_BOTS[name].update(active=False, status="Error ❌ (Auto-stopped)")
                preview = (tail[-900:] if tail else "*(no output)*")
                await _notify(
                    owner_id,
                    f"⚠️ *{name}* crashed {MAX_FAST_CRASHES}× quickly — *auto-stopped*.\n\n"
                    f"🔍 *Error Output:*\n```\n{preview}\n```\n\n"
                    f"Fix the error then re-upload your bot.",
                )
                break
        else:
            fast_crashes = 0          # successful long run resets counter

        RUNNING_BOTS[name]["status"] = "Restarting ⏳"
        log.info("Auto-restarting %s in 5 s…", name)
        await asyncio.sleep(5)


async def _notify(owner_id: int, text: str) -> None:
    """DM the bot owner — best-effort, never raises."""
    if not _APP or not owner_id:
        return
    try:
        await _APP.bot.send_message(owner_id, text, parse_mode="Markdown")
    except Exception as exc:
        log.warning("Could not notify %d: %s", owner_id, exc)


# ══════════════════════════════════════════════════════════════════════
#  INLINE KEYBOARD BUILDERS
# ══════════════════════════════════════════════════════════════════════

def kb_home(admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🤖 My Bots",      callback_data="my_bots"),
            InlineKeyboardButton("📦 How to Host",  callback_data="how_host"),
        ],
        [
            InlineKeyboardButton("💰 Buy Slots",    callback_data="buy_plan"),
            InlineKeyboardButton("📊 My Stats",     callback_data="my_stats"),
        ],
    ]
    if admin:
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(rows)


def kb_plans() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            f"{p['label']} — {p['stars']} ⭐  ({p['desc']})",
            callback_data=f"buy_{k}",
        )]
        for k, p in PLANS.items()
    ]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


def kb_bot_card(name: str, can_ctrl: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_ctrl:
        rows.append([
            InlineKeyboardButton("🛑 Stop",      callback_data=f"stop|{name}"),
            InlineKeyboardButton("🗑 Delete",    callback_data=f"delete|{name}"),
        ])
        rows.append([InlineKeyboardButton("📋 Live Logs", callback_data=f"logs|{name}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="my_bots")])
    return InlineKeyboardMarkup(rows)


def kb_back(to: str = "main_menu") -> InlineKeyboardMarkup:
    lbl = "🏠 Home" if to == "main_menu" else "🔙 Back"
    return InlineKeyboardMarkup([[InlineKeyboardButton(lbl, callback_data=to)]])


# ══════════════════════════════════════════════════════════════════════
#  SHARED DISPLAY HELPERS
# ══════════════════════════════════════════════════════════════════════

def home_text(uid: int) -> str:
    u    = get_user(uid)
    used = get_used_slots(uid)
    plan = "Admin 👑" if is_admin(uid) else "Free ✨"
    return (
        f"🤖 *Master Hosting Bot*\n\n"
        f"👤 Plan   : {plan}\n"
        f"📦 Slots  : `{used} / {u['slots']}` used\n\n"
        f"Send a `.zip` archive or a `.py` file to deploy your bot!\n\n"
        f"_Codian Studio_ 💎"
    )


def mybots_card(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    bots = get_user_bots(uid)
    if not bots:
        return (
            "📭 *No bots hosted yet.*\n\nSend a `.zip` or `.py` file to deploy your first bot!",
            kb_back(),
        )
    lines = ["🤖 *Your Hosted Bots*\n"]
    rows  = []
    for name in bots:
        e   = RUNNING_BOTS.get(name, {})
        st  = e.get("status", "Offline 🔴")
        up  = fmt_uptime(time.time() - e["start_time"]) if e.get("start_time") else "—"
        rs  = e.get("restarts", 0)
        spd = speed_label(e) if e else "—"
        lines.append(f"🔹 *{name}*\n   {st}  ·  ⏱ `{up}`\n   {spd}  ·  🔄 `{rs}` restarts\n")
        rows.append([InlineKeyboardButton(f"⚙️  {name}", callback_data=f"detail|{name}")])
    rows.append([InlineKeyboardButton("🏠 Home", callback_data="main_menu")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    get_user(uid)                           # ensure record exists
    await update.message.reply_text(
        home_text(uid),
        parse_mode    = "Markdown",
        reply_markup  = kb_home(is_admin(uid)),
    )


async def cmd_mybots(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text, kb = mybots_card(update.effective_user.id)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin only — list every hosted bot across all users."""
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ This command is for admins only.")
        return
    if not RUNNING_BOTS:
        await update.message.reply_text("📭 No bots currently hosted.")
        return
    reg   = load_registry()
    lines = ["👑 *All Hosted Bots — Admin View*\n"]
    for name, e in RUNNING_BOTS.items():
        up  = fmt_uptime(time.time() - e.get("start_time", time.time()))
        own = reg.get(name, {}).get("owner_id", "?")
        rs  = e.get("restarts", 0)
        lines.append(
            f"🔹 *{name}*\n"
            f"   Owner: `{own}`  ·  {e.get('status','?')}\n"
            f"   ⏱ `{up}`  ·  🔄 `{rs}`  ·  {speed_label(e)}\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid      = update.effective_user.id
    bot_name = " ".join(ctx.args).strip()    # ← FIX: joins ALL args (handles spaces)

    if not bot_name:
        await update.message.reply_text("Usage: `/stop <bot name>`", parse_mode="Markdown")
        return

    reg   = load_registry()
    owner = str(reg.get(bot_name, {}).get("owner_id", ""))

    if not (is_admin(uid) or owner == str(uid)):
        await update.message.reply_text(
            f"❌ No bot named *{bot_name}* found (or you don't own it).",
            parse_mode="Markdown",
        )
        return

    e = RUNNING_BOTS.get(bot_name)
    if not e:
        await update.message.reply_text(
            f"⚠️ *{bot_name}* is registered but currently not running.",
            parse_mode="Markdown",
        )
        return

    _do_stop(bot_name)
    await update.message.reply_text(f"🛑 *{bot_name}* stopped.", parse_mode="Markdown")


async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid      = update.effective_user.id
    bot_name = " ".join(ctx.args).strip()

    if not bot_name:
        await update.message.reply_text("Usage: `/delete <bot name>`", parse_mode="Markdown")
        return

    reg   = load_registry()
    owner = str(reg.get(bot_name, {}).get("owner_id", ""))

    if not (is_admin(uid) or owner == str(uid)):
        await update.message.reply_text(
            f"❌ No bot named *{bot_name}* found (or you don't own it).",
            parse_mode="Markdown",
        )
        return

    _kill_and_remove(bot_name)
    push_to_github(f"Deleted: {bot_name}")
    await update.message.reply_text(f"🗑 *{bot_name}* deleted permanently.", parse_mode="Markdown")


async def cmd_pr(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: /pr <user_id> <total_slots>"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return
    try:
        target = int(ctx.args[0])
        slots  = int(ctx.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Usage: `/pr <user_id> <total_slots>`", parse_mode="Markdown"
        )
        return
    set_user_slots(target, slots)
    push_to_github(f"Admin grant: {target} → {slots} slots")
    await update.message.reply_text(
        f"✅ User `{target}` now has *{slots}* hosting slots.", parse_mode="Markdown"
    )


# ══════════════════════════════════════════════════════════════════════
#  FILE UPLOAD HANDLER  (.zip / .py)
# ══════════════════════════════════════════════════════════════════════

async def handle_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid   = update.effective_user.id
    doc   = update.message.document
    fname = doc.file_name or "upload"
    ext   = Path(fname).suffix.lower()

    if ext not in (".zip", ".py"):
        await update.message.reply_text(
            "⚠️ Send a `.zip` archive or a `.py` file.", parse_mode="Markdown"
        )
        return

    # ── Slot check ─────────────────────────────────────────────────
    user     = get_user(uid)
    used     = get_used_slots(uid)
    bot_name = Path(fname).stem
    reg      = load_registry()
    is_redeploy = (
        str(reg.get(bot_name, {}).get("owner_id", "")) == str(uid)
        or is_admin(uid)
    )

    if not is_redeploy and used >= user["slots"]:
        await update.message.reply_text(
            f"❌ *Slot limit reached!*  `{used}/{user['slots']}`\n\n"
            f"Buy more slots to host additional bots 👇",
            parse_mode   = "Markdown",
            reply_markup = kb_plans(),
        )
        return

    msg = await update.message.reply_text(
        f"⬇️ Downloading *{bot_name}*…", parse_mode="Markdown"
    )

    HOSTED_DIR.mkdir(parents=True, exist_ok=True)
    tg_file = await ctx.bot.get_file(doc.file_id)

    # ── Single .py file ────────────────────────────────────────────
    if ext == ".py":
        bot_dir = HOSTED_DIR / bot_name
        bot_dir.mkdir(parents=True, exist_ok=True)
        await tg_file.download_to_drive(str(bot_dir / "main.py"))

    # ── .zip archive ───────────────────────────────────────────────
    else:
        zip_path = HOSTED_DIR / fname
        await tg_file.download_to_drive(str(zip_path))

        bot_dir = HOSTED_DIR / bot_name
        if bot_dir.exists():
            shutil.rmtree(bot_dir)
        bot_dir.mkdir(parents=True)

        try:
            with ZipFile(zip_path, "r") as zf:
                members = zf.namelist()
                # Detect single top-level folder and strip it
                tops  = {m.split("/")[0] for m in members if m.strip("/")}
                strip = (list(tops)[0] + "/") if (
                    len(tops) == 1 and any("/" in m for m in members)
                ) else ""

                for member in members:
                    rel = member[len(strip):] if strip else member
                    if not rel:
                        continue
                    dest = bot_dir / rel
                    if member.endswith("/"):
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(zf.read(member))

        except BadZipFile:
            zip_path.unlink(missing_ok=True)
            shutil.rmtree(bot_dir, ignore_errors=True)
            await msg.edit_text("❌ Not a valid ZIP archive.")
            return
        finally:
            zip_path.unlink(missing_ok=True)

    # ── Resolve main.py (auto-search if missing at root) ──────────
    main_py = bot_dir / "main.py"
    if not main_py.exists():
        found = list(bot_dir.rglob("main.py")) or list(bot_dir.rglob("*.py"))
        if found:
            shutil.copy(found[0], main_py)
        else:
            shutil.rmtree(bot_dir, ignore_errors=True)
            await msg.edit_text(
                "❌ No `.py` file found inside the archive.\n"
                "Include `main.py` as the entry point.",
                parse_mode="Markdown",
            )
            return

    # ── Install requirements (auto) ────────────────────────────────
    req = bot_dir / "requirements.txt"
    if req.exists():
        await msg.edit_text(
            f"⚙️ Installing dependencies for *{bot_name}*…", parse_mode="Markdown"
        )
        _install_reqs(req)

    # ── Stop old instance if re-deploying ─────────────────────────
    if bot_name in RUNNING_BOTS:
        _do_stop(bot_name)
        await asyncio.sleep(2)

    # ── Register, sync, launch ────────────────────────────────────
    register_bot(bot_name, uid)
    push_to_github(f"Deployed: {bot_name}")
    asyncio.create_task(run_bot(bot_name, bot_dir, uid))

    new_used = get_used_slots(uid)
    await msg.edit_text(
        f"✅ *{bot_name}* deployed and running!\n\n"
        f"📦 Slots used: `{new_used}/{user['slots']}`\n"
        f"Use 🤖 My Bots to monitor & manage.",
        parse_mode   = "Markdown",
        reply_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🤖 My Bots", callback_data="my_bots"),
            InlineKeyboardButton("🏠 Home",    callback_data="main_menu"),
        ]]),
    )


# ══════════════════════════════════════════════════════════════════════
#  INLINE CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════════

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q   = update.callback_query
    uid = q.from_user.id
    d   = q.data or ""
    await q.answer()

    # ── Home ─────────────────────────────────────────────────────────
    if d == "main_menu":
        await q.edit_message_text(
            home_text(uid),
            parse_mode   = "Markdown",
            reply_markup = kb_home(is_admin(uid)),
        )

    # ── My Bots list ─────────────────────────────────────────────────
    elif d == "my_bots":
        text, kb = mybots_card(uid)
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

    # ── Bot detail card ──────────────────────────────────────────────
    elif d.startswith("detail|"):
        name = d.split("|", 1)[1]
        e    = RUNNING_BOTS.get(name, {})
        reg  = load_registry()
        can  = is_admin(uid) or str(reg.get(name, {}).get("owner_id", "")) == str(uid)
        up   = fmt_uptime(time.time() - e["start_time"]) if e.get("start_time") else "—"
        err  = (e.get("last_error") or "None").strip()[-400:]
        txt  = (
            f"⚙️ *{name}*\n\n"
            f"📌 Status   : {e.get('status','Offline 🔴')}\n"
            f"⏱ Uptime   : `{up}`\n"
            f"🚀 Speed    : {speed_label(e) if e else '—'}\n"
            f"🔄 Restarts : `{e.get('restarts',0)}`\n\n"
            f"📋 *Last Output:*\n```\n{err}\n```"
        )
        await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb_bot_card(name, can))

    # ── Stop via button ──────────────────────────────────────────────
    elif d.startswith("stop|"):
        name = d.split("|", 1)[1]
        reg  = load_registry()
        if not (is_admin(uid) or str(reg.get(name, {}).get("owner_id", "")) == str(uid)):
            await q.answer("❌ Not your bot!", show_alert=True)
            return
        _do_stop(name)
        await q.edit_message_text(
            f"🛑 *{name}* has been stopped.",
            parse_mode="Markdown", reply_markup=kb_back()
        )

    # ── Delete via button ────────────────────────────────────────────
    elif d.startswith("delete|"):
        name = d.split("|", 1)[1]
        reg  = load_registry()
        if not (is_admin(uid) or str(reg.get(name, {}).get("owner_id", "")) == str(uid)):
            await q.answer("❌ Not your bot!", show_alert=True)
            return
        _kill_and_remove(name)
        push_to_github(f"Deleted: {name}")
        await q.edit_message_text(
            f"🗑 *{name}* deleted permanently.",
            parse_mode="Markdown", reply_markup=kb_back()
        )

    # ── Live log viewer ──────────────────────────────────────────────
    elif d.startswith("logs|"):
        name     = d.split("|", 1)[1]
        log_path = HOSTED_DIR / name / "bot_output.log"
        tail     = _tail(log_path, 50) or "*(No logs yet.)*"
        if len(tail) > 3400:
            tail = "…(truncated)\n" + tail[-3300:]
        await q.edit_message_text(
            f"📋 *Logs — {name}*\n\n```\n{tail}\n```",
            parse_mode   = "Markdown",
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh",  callback_data=f"logs|{name}"),
                InlineKeyboardButton("🔙 Back",     callback_data=f"detail|{name}"),
            ]]),
        )

    # ── How to host guide ────────────────────────────────────────────
    elif d == "how_host":
        await q.edit_message_text(
            "📦 *How to Host Your Bot*\n\n"
            "1️⃣  Name your entry file *`main.py`*\n"
            "2️⃣  Add `requirements.txt` for packages *(optional)*\n"
            "3️⃣  Zip everything and send the `.zip` here\n"
            "   _Or just send the `.py` file directly!_\n\n"
            "✅ *We handle the rest automatically.*\n\n"
            "📌 *Notes:*\n"
            "• Nested folder zips are auto-extracted\n"
            "• Any `.py` file is accepted if `main.py` is absent\n"
            "• Bot crashes 3× fast → auto-stopped + error sent to you\n"
            "• Free plan = *3 slots*  |  Upgrades available below\n",
            parse_mode   = "Markdown",
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("💰 Buy More Slots", callback_data="buy_plan"),
                InlineKeyboardButton("🏠 Home",           callback_data="main_menu"),
            ]]),
        )

    # ── My stats ─────────────────────────────────────────────────────
    elif d == "my_stats":
        u    = get_user(uid)
        used = get_used_slots(uid)
        bots = get_user_bots(uid)
        run  = sum(1 for b in bots
                   if RUNNING_BOTS.get(b, {}).get("status") == "Running 🟢")
        await q.edit_message_text(
            f"📊 *Your Statistics*\n\n"
            f"🆔 User ID     : `{uid}`\n"
            f"📦 Slots       : `{used} / {u['slots']}`\n"
            f"🤖 Total Bots  : `{len(bots)}`\n"
            f"🟢 Running Now : `{run}`\n"
            f"⭐ Stars Spent : `{u.get('stars_spent', 0)}`\n",
            parse_mode   = "Markdown",
            reply_markup = kb_back(),
        )

    # ── Plan menu ────────────────────────────────────────────────────
    elif d == "buy_plan":
        await q.edit_message_text(
            "💰 *Upgrade Your Plan*\n\n"
            "Pay with Telegram ⭐ Stars — slots are added *instantly* and permanently.\n",
            parse_mode   = "Markdown",
            reply_markup = kb_plans(),
        )

    # ── Send Stars invoice ───────────────────────────────────────────
    elif d.startswith("buy_"):
        plan_key = d[4:]
        plan     = PLANS.get(plan_key)
        if not plan:
            await q.answer("Unknown plan.", show_alert=True)
            return
        await ctx.bot.send_invoice(
            chat_id       = uid,
            title         = plan["label"],
            description   = f"Get {plan['slots']} extra bot hosting slots. {plan['desc']}",
            payload       = f"{plan_key}|{uid}",
            provider_token= "",              # empty = Telegram Stars (XTR)
            currency      = "XTR",
            prices        = [LabeledPrice(plan["label"], plan["stars"])],
        )

    # ── Admin panel ──────────────────────────────────────────────────
    elif d == "admin_panel":
        if not is_admin(uid):
            await q.answer("Admins only!", show_alert=True)
            return
        total   = len(RUNNING_BOTS)
        running = sum(1 for e in RUNNING_BOTS.values()
                      if e.get("status") == "Running 🟢")
        users   = len(load_users())
        await q.edit_message_text(
            f"👑 *Admin Panel*\n\n"
            f"🤖 Total Bots  : `{total}`\n"
            f"🟢 Running     : `{running}`\n"
            f"👥 Total Users : `{users}`\n\n"
            f"*Commands:*\n"
            f"`/all`              — View every hosted bot\n"
            f"`/pr <id> <slots>`  — Set a user's slot count\n"
            f"`/stop <name>`      — Stop any bot\n"
            f"`/delete <name>`    — Delete any bot permanently\n",
            parse_mode   = "Markdown",
            reply_markup = kb_back(),
        )


# ══════════════════════════════════════════════════════════════════════
#  TELEGRAM STARS PAYMENT HANDLERS
# ══════════════════════════════════════════════════════════════════════

async def pre_checkout(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.pre_checkout_query.answer(ok=True)


async def payment_success(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    payment = update.message.successful_payment
    try:
        plan_key, uid_str = payment.invoice_payload.split("|")
        uid  = int(uid_str)
        plan = PLANS[plan_key]
    except Exception:
        await update.message.reply_text(
            "⚠️ Payment received but could not be processed. Contact admin."
        )
        return

    new_slots = add_user_slots(uid, plan["slots"])
    add_user_stars(uid, plan["stars"])
    push_to_github(f"Stars purchase: {plan_key} by {uid}")

    await update.message.reply_text(
        f"🎉 *Payment Successful!*\n\n"
        f"📦 Plan      : *{plan['label']}*\n"
        f"➕ Added     : +{plan['slots']} slots\n"
        f"📊 New Total : *{new_slots}* slots\n\n"
        f"Go host your bots! 🚀\n_Codian Studio_ 💎",
        parse_mode   = "Markdown",
        reply_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("📦 Host a Bot", callback_data="how_host"),
        ]]),
    )


# ══════════════════════════════════════════════════════════════════════
#  INTERNAL STOP / DELETE HELPERS
# ══════════════════════════════════════════════════════════════════════

def _do_stop(name: str) -> None:
    """Stop the process but keep the folder and registry entry."""
    e = RUNNING_BOTS.get(name, {})
    if e:
        e["active"] = False
        p = e.get("process")
        if p and p.poll() is None:
            p.terminate()
            try:   p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill()
        e["status"] = "Stopped 🛑"


def _kill_and_remove(name: str) -> None:
    """Stop the process, delete the folder, unregister the bot."""
    _do_stop(name)
    RUNNING_BOTS.pop(name, None)
    shutil.rmtree(HOSTED_DIR / name, ignore_errors=True)
    unregister_bot(name)


# ══════════════════════════════════════════════════════════════════════
#  AUTO-START PERSISTED BOTS  (after Render restart)
# ══════════════════════════════════════════════════════════════════════

async def autostart_persisted() -> None:
    reg = load_registry()
    if not HOSTED_DIR.exists():
        return
    for item in HOSTED_DIR.iterdir():
        if not item.is_dir() or item.name.startswith(("_", ".")):
            continue
        if not (item / "main.py").exists():
            continue
        req = item / "requirements.txt"
        if req.exists():
            _install_reqs(req)
        owner = reg.get(item.name, {}).get("owner_id", 0)
        log.info("Auto-starting persisted bot: %s (owner=%s)", item.name, owner)
        asyncio.create_task(run_bot(item.name, item, owner))


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════

async def main() -> None:
    global _APP

    log.info("═══════════════════════════════════════════════")
    log.info("   Master Hosting Bot  v2.0  —  Initialising  ")
    log.info("═══════════════════════════════════════════════")

    configure_git()
    sync_from_github()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Build PTB app ──────────────────────────────────────────────
    _APP = ApplicationBuilder().token(BOT_TOKEN).build()

    # ── Register handlers ──────────────────────────────────────────
    _APP.add_handler(CommandHandler("start",  cmd_start))
    _APP.add_handler(CommandHandler("mybots", cmd_mybots))
    _APP.add_handler(CommandHandler("all",    cmd_all))
    _APP.add_handler(CommandHandler("stop",   cmd_stop))
    _APP.add_handler(CommandHandler("delete", cmd_delete))
    _APP.add_handler(CommandHandler("pr",     cmd_pr))
    _APP.add_handler(MessageHandler(filters.Document.ALL, handle_upload))
    _APP.add_handler(CallbackQueryHandler(handle_callback))
    _APP.add_handler(PreCheckoutQueryHandler(pre_checkout))
    _APP.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, payment_success))

    # ── Bind web server (BEFORE polling — Render health check) ─────
    await start_web_server()

    # ── Background tasks ───────────────────────────────────────────
    asyncio.create_task(keep_alive_loop())

    # ── Re-launch previously hosted bots ──────────────────────────
    await autostart_persisted()

    # ── CRITICAL: kill rival Render instance ──────────────────────
    log.info("Deleting existing webhook and clearing updates…")
    await _APP.bot.delete_webhook(drop_pending_updates=True)

    # ── PTB 20.x lifecycle — strict order ─────────────────────────
    await _APP.initialize()
    await _APP.start()
    await _APP.updater.start_polling(
        drop_pending_updates = True,
        allowed_updates      = Update.ALL_TYPES,
    )

    # ── Bot command menu ───────────────────────────────────────────
    try:
        await _APP.bot.set_my_commands([
            BotCommand("start",  "🏠 Home & welcome"),
            BotCommand("mybots", "🤖 Manage your hosted bots"),
            BotCommand("stop",   "🛑 Stop a running bot"),
            BotCommand("delete", "🗑 Delete a hosted bot"),
            BotCommand("all",    "👑 Admin: view all bots"),
            BotCommand("pr",     "👑 Admin: set user slots"),
        ])
    except Exception:
        pass

    log.info("✅  All systems operational — Master Hosting Bot is live!")
    await asyncio.Event().wait()          # block without burning CPU


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted — shutting down.")
