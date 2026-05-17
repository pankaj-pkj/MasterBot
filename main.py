"""
╔══════════════════════════════════════════════════════════════════════╗
║   MASTER HOSTING BOT  v3.0  —  Ultimate Edition                     ║
║   python-telegram-bot 20.x  ·  Python 3.11  ·  Render.com           ║
║   Codian Studio 💎                                                   ║
╠══════════════════════════════════════════════════════════════════════╣
║  NEW in v3.0:                                                        ║
║  • Soft-delete  — deleted bots backed up to GitHub _deleted/         ║
║  • /users       — admin: list all users + stats                      ║
║  • /msg         — broadcast OR DM a specific user                    ║
║  • /ban /unban  — admin user ban system                              ║
║  • /restart     — restart a stopped bot                              ║
║  • /getlog      — download bot log as file                           ║
║  • Persistent Reply Keyboard  +  Inline Keyboard                     ║
║  • Direct code deploy  — paste Python → named & launched             ║
║  • Smart auto-detect packages (no requirements.txt needed)           ║
║  • Rate limiting  (3 deploys / 10 min per user)                      ║
║  • Full /stop fix — names with spaces work                           ║
╚══════════════════════════════════════════════════════════════════════╝

Render env-vars required:
  BOT_TOKEN  GITHUB_PAT  GITHUB_USERNAME  REPO_NAME  RENDER_URL  PORT
"""

# ── stdlib ────────────────────────────────────────────────────────────
import os
import re
import ast
import sys
import json
import time
import shutil
import logging
import asyncio
import subprocess
from zipfile import ZipFile, BadZipFile
from pathlib import Path

# ── third-party ───────────────────────────────────────────────────────
import aiohttp
from aiohttp import web

from telegram import (
    Update,
    BotCommand,
    LabeledPrice,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
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
ADMIN_IDS: set[int] = {6960252072}   # ← add admin Telegram IDs here

FREE_SLOTS       = 3
MAX_FAST_CRASHES = 3
FAST_CRASH_SEC   = 30

PLANS: dict[str, dict] = {
    "starter": {"stars": 15,  "slots": 3,  "label": "Starter ⭐",  "desc": "+3 bot slots"},
    "pro":     {"stars": 50,  "slots": 10, "label": "Pro 💎",      "desc": "+10 bot slots"},
    "elite":   {"stars": 100, "slots": 25, "label": "Elite 👑",    "desc": "+25 bot slots"},
}

# Python stdlib modules (never try to pip-install these)
STDLIB: set[str] = {
    "os","sys","re","io","json","time","math","copy","enum","abc","ast",
    "csv","gzip","hmac","html","http","uuid","glob","shutil","queue",
    "array","struct","socket","signal","string","random","hashlib",
    "logging","pathlib","inspect","functools","operator","itertools",
    "contextlib","threading","subprocess","traceback","datetime",
    "calendar","textwrap","argparse","platform","tempfile","unittest",
    "dataclasses","collections","ftplib","smtplib","urllib","base64",
    "binascii","codecs","email","getpass","locale","weakref","warnings",
    "typing","types","builtins","gc","heapq","bisect","pprint",
    "statistics","decimal","fractions","zipfile","tarfile","configparser",
    "pickle","shelve","sqlite3","multiprocessing","concurrent","asyncio",
    "ssl","select","selectors","mimetypes","xml","pdb","timeit","dis",
    "cmd","wave","colorsys","imghdr","sndhdr","struct","cProfile",
    "numbers","abc","io","string","textwrap","difflib","readline",
}

# import-name → pip package name mapping
IMPORT_MAP: dict[str, str] = {
    "telegram":           "python-telegram-bot",
    "telebot":            "pyTelegramBotAPI",
    "aiogram":            "aiogram",
    "pyrogram":           "pyrogram",
    "tgcrypto":           "tgcrypto",
    "telethon":           "telethon",
    "requests":           "requests",
    "aiohttp":            "aiohttp",
    "httpx":              "httpx",
    "urllib3":            "urllib3",
    "bs4":                "beautifulsoup4",
    "lxml":               "lxml",
    "selenium":           "selenium",
    "playwright":         "playwright",
    "scrapy":             "scrapy",
    "PIL":                "Pillow",
    "cv2":                "opencv-python",
    "skimage":            "scikit-image",
    "imageio":            "imageio",
    "numpy":              "numpy",
    "pandas":             "pandas",
    "sklearn":            "scikit-learn",
    "scipy":              "scipy",
    "matplotlib":         "matplotlib",
    "seaborn":            "seaborn",
    "plotly":             "plotly",
    "flask":              "flask",
    "fastapi":            "fastapi",
    "uvicorn":            "uvicorn",
    "django":             "django",
    "starlette":          "starlette",
    "tornado":            "tornado",
    "sqlalchemy":         "SQLAlchemy",
    "pymongo":            "pymongo",
    "motor":              "motor",
    "redis":              "redis",
    "psycopg2":           "psycopg2-binary",
    "pymysql":            "PyMySQL",
    "aiomysql":           "aiomysql",
    "aiosqlite":          "aiosqlite",
    "aiofiles":           "aiofiles",
    "dotenv":             "python-dotenv",
    "yaml":               "PyYAML",
    "toml":               "toml",
    "pydantic":           "pydantic",
    "click":              "click",
    "rich":               "rich",
    "loguru":             "loguru",
    "tqdm":               "tqdm",
    "colorama":           "colorama",
    "apscheduler":        "APScheduler",
    "schedule":           "schedule",
    "celery":             "celery",
    "cryptography":       "cryptography",
    "jwt":                "PyJWT",
    "passlib":            "passlib",
    "bcrypt":             "bcrypt",
    "qrcode":             "qrcode",
    "gspread":            "gspread",
    "openai":             "openai",
    "anthropic":          "anthropic",
    "langchain":          "langchain",
    "transformers":       "transformers",
    "torch":              "torch",
    "tensorflow":         "tensorflow",
    "paramiko":           "paramiko",
    "tweepy":             "tweepy",
    "discord":            "discord.py",
    "pyautogui":          "pyautogui",
    "pynput":             "pynput",
    "pytube":             "pytube",
    "yt_dlp":             "yt-dlp",
    "instaloader":        "instaloader",
    "marshmallow":        "marshmallow",
    "attrs":              "attrs",
}

# Reply-keyboard button labels (exact strings users send when pressing)
_RKB_MY_BOTS = "🤖 My Bots"
_RKB_DEPLOY  = "📦 Deploy Bot"
_RKB_SLOTS   = "💰 Buy Slots"
_RKB_STATS   = "📊 My Stats"
_RKB_HELP    = "ℹ️ Help"
_RKB_ADMIN   = "👑 Admin"
_ALL_RKB     = {_RKB_MY_BOTS, _RKB_DEPLOY, _RKB_SLOTS, _RKB_STATS, _RKB_HELP, _RKB_ADMIN}

# ══════════════════════════════════════════════════════════════════════
#  PATHS & RUNTIME STATE
# ══════════════════════════════════════════════════════════════════════
REPO_URL     = f"https://{GITHUB_PAT}@github.com/{GITHUB_USERNAME}/{REPO_NAME}.git"
HOSTED_DIR   = Path("hosted_bots")
DATA_DIR     = HOSTED_DIR / "_data"
DELETED_DIR  = HOSTED_DIR / "_deleted"

RUNNING_BOTS: dict[str, dict]        = {}
DEPLOY_TIMES: dict[int, list[float]] = {}   # rate-limit tracker
_APP = None                                 # global PTB app ref

# ══════════════════════════════════════════════════════════════════════
#  JSON PERSISTENCE  (inside GitHub repo for Render persistence)
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
    data = load_users()
    k    = str(uid)
    if k not in data:
        data[k] = {"slots": FREE_SLOTS, "stars_spent": 0, "banned": False}
        save_users(data)
    return data[k]

def _update_user(uid: int, patch: dict) -> None:
    data = load_users()
    k    = str(uid)
    if k not in data:
        data[k] = {"slots": FREE_SLOTS, "stars_spent": 0, "banned": False}
    data[k].update(patch)
    save_users(data)

def set_user_slots(uid: int, slots: int) -> None:
    _update_user(uid, {"slots": slots})

def add_user_slots(uid: int, extra: int) -> int:
    u   = get_user(uid)
    new = u["slots"] + extra
    set_user_slots(uid, new)
    return new

def add_user_stars(uid: int, stars: int) -> None:
    u = get_user(uid)
    _update_user(uid, {"stars_spent": u.get("stars_spent", 0) + stars})

def is_banned(uid: int) -> bool:
    return bool(get_user(uid).get("banned", False))

def ban_user(uid: int) -> None:
    _update_user(uid, {"banned": True})

def unban_user(uid: int) -> None:
    _update_user(uid, {"banned": False})

# ── Bot Registry ───────────────────────────────────────────────────────
def load_registry() -> dict:
    return _load_json(DATA_DIR / "registry.json", {})

def save_registry(d: dict) -> None:
    _save_json(DATA_DIR / "registry.json", d)

def register_bot(name: str, uid: int) -> None:
    reg       = load_registry()
    reg[name] = {"owner_id": uid, "registered_at": time.time()}
    save_registry(reg)

def unregister_bot(name: str) -> None:
    reg = load_registry()
    reg.pop(name, None)
    save_registry(reg)

def get_used_slots(uid: int) -> int:
    return sum(1 for v in load_registry().values()
               if str(v.get("owner_id")) == str(uid))

def get_user_bots(uid: int) -> list[str]:
    return [k for k, v in load_registry().items()
            if str(v.get("owner_id")) == str(uid)]

def soft_delete_bot(name: str, deleted_by: int) -> None:
    """
    Back up the bot folder to _deleted/<name>_<ts>/ in the repo,
    write a <deleted> marker, then unregister from active registry.
    Files are committed to GitHub so admin can always recover them.
    """
    src = HOSTED_DIR / name
    if src.exists():
        ts   = int(time.time())
        dest = DELETED_DIR / f"{name}_{ts}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        # Write <deleted> marker
        _save_json(dest / "_DELETED.json", {
            "bot_name":   name,
            "deleted_by": deleted_by,
            "deleted_at": ts,
            "marker":     "<deleted>",
        })
    unregister_bot(name)

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
    if (HOSTED_DIR / ".git").exists():
        log.info("Pulling GitHub repo…")
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
        log.error("Clone failed — local dir only.")
        HOSTED_DIR.mkdir(exist_ok=True)

def push_to_github(msg: str = "Update") -> None:
    if not GITHUB_PAT:
        return
    _git(
        f'cd "{HOSTED_DIR}" && git add -A && '
        f'(git diff --cached --quiet || git commit -m "{msg}") && git push'
    )

# ══════════════════════════════════════════════════════════════════════
#  AIOHTTP WEB SERVER
# ══════════════════════════════════════════════════════════════════════

async def _web_root(req: web.Request) -> web.Response:
    rows = "".join(
        f"<tr><td><b>{n}</b></td>"
        f"<td>{d.get('status','?')}</td>"
        f"<td>{fmt_uptime(time.time()-d.get('start_time',time.time()))}</td>"
        f"<td>{d.get('restarts',0)}</td></tr>"
        for n, d in RUNNING_BOTS.items()
    )
    total   = len(RUNNING_BOTS)
    running = sum(1 for e in RUNNING_BOTS.values() if e.get("status") == "Running 🟢")
    html = (
        "<html><head><title>Master Hosting Bot</title>"
        "<style>body{font-family:monospace;padding:20px;background:#0d1117;color:#c9d1d9}"
        "h2{color:#58a6ff}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #30363d;padding:8px;text-align:left}"
        "tr:nth-child(even){background:#161b22}.badge{display:inline-block;"
        "padding:2px 8px;border-radius:4px;background:#238636;color:#fff}"
        "</style></head><body>"
        f"<h2>🤖 Master Hosting Bot — Live</h2>"
        f"<p>🟢 Running: <b>{running}</b> / Total: <b>{total}</b></p>"
        f"<table><tr><th>Bot</th><th>Status</th><th>Uptime</th><th>Restarts</th></tr>"
        f"{rows}</table><br>"
        f"<p><i>Codian Studio 💎</i></p></body></html>"
    )
    return web.Response(text=html, content_type="text/html")

async def start_web_server() -> None:
    wa = web.Application()
    wa.router.add_get("/",       _web_root)
    wa.router.add_get("/health", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(wa)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Web server on port %d", PORT)

# ══════════════════════════════════════════════════════════════════════
#  ANTI-SLEEP SELF-PING
# ══════════════════════════════════════════════════════════════════════

async def keep_alive_loop() -> None:
    if not RENDER_URL:
        return
    await asyncio.sleep(90)
    async with aiohttp.ClientSession() as sess:
        while True:
            try:
                async with sess.get(
                    f"{RENDER_URL}/health",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    log.info("Anti-sleep ping → HTTP %d", r.status)
            except Exception as exc:
                log.warning("Ping failed: %s", exc)
            await asyncio.sleep(840)

# ══════════════════════════════════════════════════════════════════════
#  UTILITY HELPERS
# ══════════════════════════════════════════════════════════════════════

def fmt_uptime(secs: float) -> str:
    s      = int(max(0, secs))
    h, r   = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h}h {m}m {sec}s"

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def speed_label(e: dict) -> str:
    r = e.get("restarts", 0)
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

def check_rate_limit(uid: int) -> bool:
    """Return True if user is allowed to deploy (max 3 per 10 min)."""
    if is_admin(uid):
        return True
    now   = time.time()
    times = [t for t in DEPLOY_TIMES.get(uid, []) if now - t < 600]
    if len(times) >= 3:
        return False
    times.append(now)
    DEPLOY_TIMES[uid] = times
    return True

# ── Smart requirements detection ───────────────────────────────────────
def detect_imports(code: str) -> list[str]:
    """Extract top-level import names using ast; regex fallback."""
    names: list[str] = []
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module.split(".")[0])
    except SyntaxError:
        names = re.findall(
            r"^(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
            code, re.MULTILINE,
        )
    return list(set(names))

def smart_install(code: str) -> list[str]:
    """
    Detect imports in *code*, filter stdlib & already-installed packages,
    then pip-install the rest. Returns list of packages actually installed.
    """
    imports    = detect_imports(code)
    to_install = []
    for imp in imports:
        if imp in STDLIB:
            continue
        pkg = IMPORT_MAP.get(imp, imp)
        # Test import silently
        check = subprocess.run(
            [sys.executable, "-c", f"import {imp}"],
            capture_output=True,
        )
        if check.returncode != 0:
            to_install.append(pkg)
    if to_install:
        log.info("Auto-installing: %s", to_install)
        subprocess.run(
            [sys.executable, "-m", "pip", "install"] + to_install + ["-q"],
            check=False, timeout=300,
        )
    return to_install

def _install_reqs(req: Path) -> None:
    log.info("Installing %s", req)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req), "-q"],
        check=False, timeout=300,
    )

# ── Code detection & smart naming ──────────────────────────────────────
_CODE_RE = re.compile(
    r"(^\s*(import|from)\s+\w+|"
    r"^\s*(def|async\s+def|class)\s+\w+|"
    r"if\s+__name__\s*==|"
    r"^\s*@\w+)",
    re.MULTILINE,
)

def is_python_code(text: str) -> bool:
    return len(text) > 20 and bool(_CODE_RE.search(text))

def smart_bot_name(code: str, uid: int) -> str:
    """Derive a meaningful bot name from raw Python source."""
    lower = code.lower()
    if any(k in lower for k in ("telegram", "telebot", "aiogram", "pyrogram", "telethon")):
        prefix = "tgbot"
    elif any(k in lower for k in ("discord", "nextcord", "disnake")):
        prefix = "dsbot"
    elif any(k in lower for k in ("flask", "fastapi", "django", "tornado", "uvicorn")):
        prefix = "webapp"
    elif any(k in lower for k in ("scrape", "bs4", "selenium", "playwright", "scrapy")):
        prefix = "scraper"
    elif any(k in lower for k in ("schedule", "cron", "apscheduler")):
        prefix = "scheduler"
    elif any(k in lower for k in ("openai", "anthropic", "langchain", "gpt", "ai")):
        prefix = "aibot"
    else:
        prefix = "script"

    # Try to find a class name
    m = re.search(r"class\s+([A-Za-z][A-Za-z0-9]+)", code)
    if m:
        return f"{m.group(1).lower()[:15]}_{uid % 9999}"

    # Try first comment
    m = re.search(r"#\s*([A-Za-z][A-Za-z0-9 _-]+)", code)
    if m:
        slug = m.group(1).strip().lower()[:18].replace(" ", "_")
        return f"{slug}_{uid % 9999}"

    return f"{prefix}_{uid}_{int(time.time()) % 99999}"

# ── Process stop helpers ───────────────────────────────────────────────
def _do_stop(name: str) -> None:
    """Stop process, keep folder & registry entry."""
    e = RUNNING_BOTS.get(name, {})
    if e:
        e["active"] = False
        p = e.get("process")
        if p and p.poll() is None:
            p.terminate()
            try:   p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill()
        e["status"] = "Stopped 🛑"

def _kill_and_remove(name: str, deleted_by: int = 0) -> None:
    """Stop process, soft-delete (backup) folder, unregister."""
    _do_stop(name)
    RUNNING_BOTS.pop(name, None)
    soft_delete_bot(name, deleted_by)       # backup to _deleted/ before removing
    shutil.rmtree(HOSTED_DIR / name, ignore_errors=True)

# ══════════════════════════════════════════════════════════════════════
#  CHILD-BOT RUNNER
# ══════════════════════════════════════════════════════════════════════

async def run_bot(name: str, bot_dir: Path, owner_id: int) -> None:
    """
    Launch  python main.py  inside *bot_dir*.
    • Auto-restarts on crash.
    • Stops auto-restarting after MAX_FAST_CRASHES consecutive fast crashes.
    • Sends crash error to owner via DM.
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

        lf = None
        try:
            lf   = open(log_path, "a", encoding="utf-8", errors="replace")
            proc = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd    = str(bot_dir),
                stdout = lf,
                stderr = lf,
            )
        except Exception as exc:
            if lf:
                try: lf.close()
                except Exception: pass
            RUNNING_BOTS[name].update(
                status="Launch Error ❌", active=False, last_error=str(exc)
            )
            await _notify(owner_id, f"❌ *{name}* failed to launch:\n`{exc}`")
            break

        RUNNING_BOTS[name]["process"] = proc

        # Non-blocking wait
        while proc.poll() is None:
            await asyncio.sleep(2)

        try: lf.close()
        except Exception: pass

        runtime = time.time() - t0
        tail    = _tail(log_path)
        RUNNING_BOTS[name]["last_error"] = tail
        RUNNING_BOTS[name]["restarts"]  += 1

        if not RUNNING_BOTS.get(name, {}).get("active"):
            RUNNING_BOTS[name]["status"] = "Stopped 🛑"
            break

        log.warning("%s exited (code=%s, runtime=%.1fs)", name, proc.returncode, runtime)

        if runtime < FAST_CRASH_SEC:
            fast_crashes += 1
            if fast_crashes >= MAX_FAST_CRASHES:
                RUNNING_BOTS[name].update(
                    active=False, status="Error ❌ (Auto-stopped)"
                )
                preview = tail[-900:] if tail else "*(no output)*"
                await _notify(
                    owner_id,
                    f"⚠️ *{name}* crashed {MAX_FAST_CRASHES}× quickly — *auto-stopped*.\n\n"
                    f"🔍 *Error Output:*\n```\n{preview}\n```\n\n"
                    f"Fix the error then re-upload your bot.",
                )
                break
        else:
            fast_crashes = 0

        RUNNING_BOTS[name]["status"] = "Restarting ⏳"
        await asyncio.sleep(5)


async def _notify(owner_id: int, text: str) -> None:
    if not _APP or not owner_id:
        return
    try:
        await _APP.bot.send_message(owner_id, text, parse_mode="Markdown")
    except Exception as exc:
        log.warning("Notify failed for %d: %s", owner_id, exc)

# ══════════════════════════════════════════════════════════════════════
#  KEYBOARD BUILDERS
# ══════════════════════════════════════════════════════════════════════

def reply_kb(admin: bool = False) -> ReplyKeyboardMarkup:
    """Persistent bottom menu."""
    rows = [
        [KeyboardButton(_RKB_MY_BOTS), KeyboardButton(_RKB_DEPLOY)],
        [KeyboardButton(_RKB_SLOTS),   KeyboardButton(_RKB_STATS)],
        [KeyboardButton(_RKB_HELP)],
    ]
    if admin:
        rows.append([KeyboardButton(_RKB_ADMIN)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)


def kb_home(admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🤖 My Bots",     callback_data="my_bots"),
            InlineKeyboardButton("📦 Deploy Bot",  callback_data="how_host"),
        ],
        [
            InlineKeyboardButton("💰 Buy Slots",   callback_data="buy_plan"),
            InlineKeyboardButton("📊 My Stats",    callback_data="my_stats"),
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
    rows: list[list[InlineKeyboardButton]] = []
    if can_ctrl:
        rows.append([
            InlineKeyboardButton("🛑 Stop",      callback_data=f"stop|{name}"),
            InlineKeyboardButton("🔄 Restart",   callback_data=f"restart|{name}"),
            InlineKeyboardButton("🗑 Delete",    callback_data=f"delete|{name}"),
        ])
        rows.append([
            InlineKeyboardButton("📋 Live Logs", callback_data=f"logs|{name}"),
            InlineKeyboardButton("⬇️ Download Log", callback_data=f"getlog|{name}"),
        ])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="my_bots")])
    return InlineKeyboardMarkup(rows)


def kb_back(to: str = "main_menu") -> InlineKeyboardMarkup:
    lbl = "🏠 Home" if to == "main_menu" else "🔙 Back"
    return InlineKeyboardMarkup([[InlineKeyboardButton(lbl, callback_data=to)]])

# ══════════════════════════════════════════════════════════════════════
#  DISPLAY TEXT BUILDERS
# ══════════════════════════════════════════════════════════════════════

def home_text(uid: int) -> str:
    u    = get_user(uid)
    used = get_used_slots(uid)
    plan = "Admin 👑" if is_admin(uid) else "Free ✨"
    return (
        f"🤖 *Master Hosting Bot*\n\n"
        f"👤 Plan   : {plan}\n"
        f"📦 Slots  : `{used} / {u['slots']}` used\n\n"
        f"Send a `.zip`, `.py` file *or paste Python code directly*!\n\n"
        f"_Codian Studio 💎_"
    )


def mybots_card(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    bots = get_user_bots(uid)
    if not bots:
        return (
            "📭 *No bots hosted yet.*\n\n"
            "Send a `.zip`, `.py` file or paste your Python code!",
            kb_back(),
        )
    lines = ["🤖 *Your Hosted Bots*\n"]
    rows: list[list[InlineKeyboardButton]] = []
    for name in bots:
        e   = RUNNING_BOTS.get(name, {})
        st  = e.get("status", "Offline 🔴")
        up  = fmt_uptime(time.time() - e["start_time"]) if e.get("start_time") else "—"
        rs  = e.get("restarts", 0)
        spd = speed_label(e) if e else "—"
        lines.append(
            f"🔹 *{name}*\n"
            f"   {st}  ·  ⏱ `{up}`\n"
            f"   {spd}  ·  🔄 `{rs}` restarts\n"
        )
        rows.append([InlineKeyboardButton(f"⚙️  {name}", callback_data=f"detail|{name}")])
    rows.append([InlineKeyboardButton("🏠 Home", callback_data="main_menu")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)

# ══════════════════════════════════════════════════════════════════════
#  SHARED DEPLOY LOGIC  (used by file upload & code paste)
# ══════════════════════════════════════════════════════════════════════

async def _finalize_deploy(
    bot_name: str,
    bot_dir: Path,
    uid: int,
    msg,                          # telegram Message to edit
    code_text: str = "",          # non-empty when deploying from text
) -> None:
    """Install deps, register, push, launch. *msg* is a Telegram message object."""

    user = get_user(uid)

    # Detect & install missing packages
    code = (bot_dir / "main.py").read_text(errors="replace") if not code_text else code_text
    req  = bot_dir / "requirements.txt"
    if req.exists():
        await msg.edit_text(f"⚙️ Installing requirements for *{bot_name}*…", parse_mode="Markdown")
        _install_reqs(req)
    else:
        await msg.edit_text(f"🔍 Auto-detecting packages for *{bot_name}*…", parse_mode="Markdown")
        installed = smart_install(code)
        if installed:
            log.info("Auto-installed for %s: %s", bot_name, installed)

    # Stop old instance if re-deploying
    if bot_name in RUNNING_BOTS:
        _do_stop(bot_name)
        await asyncio.sleep(2)

    register_bot(bot_name, uid)
    push_to_github(f"Deployed: {bot_name}")
    asyncio.create_task(run_bot(bot_name, bot_dir, uid))

    new_used = get_used_slots(uid)
    await msg.edit_text(
        f"✅ *{bot_name}* deployed and running!\n\n"
        f"📦 Slots: `{new_used}/{user['slots']}`\n"
        f"Use 🤖 My Bots to monitor & manage.",
        parse_mode   = "Markdown",
        reply_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🤖 My Bots", callback_data="my_bots"),
            InlineKeyboardButton("🏠 Home",    callback_data="main_menu"),
        ]]),
    )

# ══════════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    get_user(uid)
    if is_banned(uid):
        await update.message.reply_text("🚫 You are banned from using this service.")
        return
    await update.message.reply_text(
        home_text(uid),
        parse_mode   = "Markdown",
        reply_markup = reply_kb(is_admin(uid)),
    )
    await update.message.reply_text(
        "👇 Use the menu below or send a file to get started!",
        reply_markup = kb_home(is_admin(uid)),
    )


async def cmd_mybots(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if is_banned(uid):
        return
    text, kb = mybots_card(uid)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: list every hosted bot."""
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Admins only.")
        return
    if not RUNNING_BOTS:
        await update.message.reply_text("📭 No bots hosted yet.")
        return
    reg   = load_registry()
    lines = ["👑 *All Hosted Bots (Admin)*\n"]
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


async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: list all registered users."""
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Admins only.")
        return
    users = load_users()
    reg   = load_registry()
    total = len(users)
    lines = [f"👥 *Total Users: {total}*\n"]
    for k, v in list(users.items()):
        bots   = sum(1 for rv in reg.values() if str(rv.get("owner_id")) == k)
        banned = " 🚫" if v.get("banned") else ""
        lines.append(
            f"• `{k}`{banned}  —  slots:`{v.get('slots',0)}`  "
            f"bots:`{bots}`  ⭐:`{v.get('stars_spent',0)}`"
        )
        if len(lines) > 30:
            lines.append(f"_...and {total - 30} more_")
            break
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin:
      /msg <text>          — broadcast to ALL users
      /msg <user_id> <text> — DM specific user
    """
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Admins only.")
        return
    if not ctx.args:
        await update.message.reply_text(
            "Usage:\n"
            "`/msg Hello everyone!`  — broadcast\n"
            "`/msg 123456789 Hi!`    — DM to user",
            parse_mode="Markdown",
        )
        return

    # Detect: first arg all-digits → specific user DM
    if ctx.args[0].isdigit():
        target  = int(ctx.args[0])
        message = " ".join(ctx.args[1:])
        if not message:
            await update.message.reply_text("Please include a message after the user ID.")
            return
        try:
            await ctx.bot.send_message(
                target,
                f"📢 *Message from Admin:*\n\n{message}",
                parse_mode="Markdown",
            )
            await update.message.reply_text(f"✅ Message sent to `{target}`.", parse_mode="Markdown")
        except Exception as exc:
            await update.message.reply_text(f"❌ Failed: `{exc}`", parse_mode="Markdown")
        return

    # Broadcast to all users
    message = " ".join(ctx.args)
    users   = load_users()
    ok = fail = 0
    status_msg = await update.message.reply_text(
        f"📡 Broadcasting to {len(users)} users…"
    )
    for uid_str in users:
        try:
            await ctx.bot.send_message(
                int(uid_str),
                f"📢 *Broadcast from Admin:*\n\n{message}",
                parse_mode="Markdown",
            )
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)   # stay inside Telegram rate limits

    await status_msg.edit_text(
        f"📡 Broadcast complete!\n✅ Sent: {ok}  ·  ❌ Failed: {fail}"
    )


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid      = update.effective_user.id
    bot_name = " ".join(ctx.args).strip()        # FIX: joins all args (handles spaces)
    if not bot_name:
        await update.message.reply_text("Usage: `/stop <bot name>`", parse_mode="Markdown")
        return
    reg   = load_registry()
    owner = str(reg.get(bot_name, {}).get("owner_id", ""))
    if not (is_admin(uid) or owner == str(uid)):
        await update.message.reply_text(
            f"❌ No bot named *{bot_name}* found (or not yours).", parse_mode="Markdown"
        )
        return
    _do_stop(bot_name)
    await update.message.reply_text(f"🛑 *{bot_name}* stopped.", parse_mode="Markdown")


async def cmd_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid      = update.effective_user.id
    bot_name = " ".join(ctx.args).strip()
    if not bot_name:
        await update.message.reply_text("Usage: `/restart <bot name>`", parse_mode="Markdown")
        return
    reg   = load_registry()
    owner = str(reg.get(bot_name, {}).get("owner_id", ""))
    if not (is_admin(uid) or owner == str(uid)):
        await update.message.reply_text(
            f"❌ No bot named *{bot_name}* found (or not yours).", parse_mode="Markdown"
        )
        return
    bot_dir = HOSTED_DIR / bot_name
    if not (bot_dir / "main.py").exists():
        await update.message.reply_text("❌ Bot files not found — please re-upload.")
        return
    _do_stop(bot_name)
    await asyncio.sleep(2)
    asyncio.create_task(run_bot(bot_name, bot_dir, uid))
    await update.message.reply_text(f"🔄 *{bot_name}* restarted!", parse_mode="Markdown")


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
            f"❌ No bot named *{bot_name}* found (or not yours).", parse_mode="Markdown"
        )
        return
    _kill_and_remove(bot_name, deleted_by=uid)
    push_to_github(f"Deleted: {bot_name} by {uid}")
    await update.message.reply_text(
        f"🗑 *{bot_name}* deleted.\n"
        f"_(Files backed up in GitHub repo with `<deleted>` marker)_",
        parse_mode="Markdown",
    )


async def cmd_pr(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: /pr <user_id> <slots>"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admins only.")
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
    push_to_github(f"Admin grant slots: {target} → {slots}")
    await update.message.reply_text(
        f"✅ User `{target}` → *{slots}* slots.", parse_mode="Markdown"
    )


async def cmd_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: /ban <user_id>"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admins only.")
        return
    try:
        target = int(ctx.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/ban <user_id>`", parse_mode="Markdown")
        return
    ban_user(target)
    push_to_github(f"Banned: {target}")
    await update.message.reply_text(f"🚫 User `{target}` banned.", parse_mode="Markdown")


async def cmd_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: /unban <user_id>"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admins only.")
        return
    try:
        target = int(ctx.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/unban <user_id>`", parse_mode="Markdown")
        return
    unban_user(target)
    push_to_github(f"Unbanned: {target}")
    await update.message.reply_text(f"✅ User `{target}` unbanned.", parse_mode="Markdown")


async def cmd_getlog(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Download a bot's log file."""
    uid      = update.effective_user.id
    bot_name = " ".join(ctx.args).strip()
    if not bot_name:
        await update.message.reply_text("Usage: `/getlog <bot name>`", parse_mode="Markdown")
        return
    reg   = load_registry()
    owner = str(reg.get(bot_name, {}).get("owner_id", ""))
    if not (is_admin(uid) or owner == str(uid)):
        await update.message.reply_text("❌ Not found or not yours.", parse_mode="Markdown")
        return
    log_path = HOSTED_DIR / bot_name / "bot_output.log"
    if not log_path.exists():
        await update.message.reply_text("❌ No log file found for this bot.")
        return
    await update.message.reply_document(
        document = open(log_path, "rb"),
        filename = f"{bot_name}_log.txt",
        caption  = f"📋 Log for *{bot_name}*",
        parse_mode = "Markdown",
    )


async def cmd_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = update.effective_user.id
    u    = get_user(uid)
    used = get_used_slots(uid)
    bots = get_user_bots(uid)
    run  = sum(1 for b in bots
               if RUNNING_BOTS.get(b, {}).get("status") == "Running 🟢")
    await update.message.reply_text(
        f"ℹ️ *Your Info*\n\n"
        f"🆔 ID       : `{uid}`\n"
        f"📦 Slots    : `{used}/{u['slots']}`\n"
        f"🤖 Bots     : `{len(bots)}` total, `{run}` running\n"
        f"⭐ Stars    : `{u.get('stars_spent',0)}` spent\n"
        f"🚫 Banned   : `{u.get('banned',False)}`\n",
        parse_mode="Markdown",
    )

# ══════════════════════════════════════════════════════════════════════
#  FILE UPLOAD HANDLER  (.zip / .py)
# ══════════════════════════════════════════════════════════════════════

async def handle_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid   = update.effective_user.id
    if is_banned(uid):
        return
    doc   = update.message.document
    fname = doc.file_name or "upload"
    ext   = Path(fname).suffix.lower()

    if ext not in (".zip", ".py"):
        await update.message.reply_text(
            "⚠️ Send a `.zip` archive or a `.py` file.", parse_mode="Markdown"
        )
        return

    # ── Rate limit ─────────────────────────────────────────────────
    if not check_rate_limit(uid):
        await update.message.reply_text(
            "⏳ *Rate limit:* max 3 deployments per 10 minutes.", parse_mode="Markdown"
        )
        return

    user     = get_user(uid)
    bot_name = Path(fname).stem
    reg      = load_registry()
    is_redeploy = (
        str(reg.get(bot_name, {}).get("owner_id", "")) == str(uid)
        or is_admin(uid)
    )

    if not is_redeploy and get_used_slots(uid) >= user["slots"]:
        await update.message.reply_text(
            f"❌ *Slot limit reached!* `{get_used_slots(uid)}/{user['slots']}`\n\n"
            f"Upgrade to host more bots 👇",
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
                tops    = {m.split("/")[0] for m in members if m.strip("/")}
                strip   = (list(tops)[0] + "/") if (
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

    # ── Resolve main.py (auto-search) ──────────────────────────────
    main_py = bot_dir / "main.py"
    if not main_py.exists():
        found = list(bot_dir.rglob("main.py")) or list(bot_dir.rglob("*.py"))
        if found:
            shutil.copy(found[0], main_py)
        else:
            shutil.rmtree(bot_dir, ignore_errors=True)
            await msg.edit_text(
                "❌ No `.py` file found inside the archive.\n"
                "Include `main.py` as your entry point.",
                parse_mode="Markdown",
            )
            return

    await _finalize_deploy(bot_name, bot_dir, uid, msg)

# ══════════════════════════════════════════════════════════════════════
#  TEXT MESSAGE HANDLER  (Reply-KB buttons  +  Direct code deploy)
# ══════════════════════════════════════════════════════════════════════

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = update.effective_user.id
    text = (update.message.text or "").strip()

    if is_banned(uid):
        return

    # ── Reply keyboard buttons ────────────────────────────────────
    if text == _RKB_MY_BOTS:
        t, kb = mybots_card(uid)
        await update.message.reply_text(t, parse_mode="Markdown", reply_markup=kb)
        return

    if text == _RKB_DEPLOY:
        await update.message.reply_text(
            "📦 *Deploy Your Bot*\n\n"
            "Send a `.zip` archive, a `.py` file, or *paste your Python code* directly here!",
            parse_mode="Markdown",
        )
        return

    if text == _RKB_SLOTS:
        await update.message.reply_text(
            "💰 *Upgrade Your Plan*\n\nPay with Telegram ⭐ Stars:",
            parse_mode   = "Markdown",
            reply_markup = kb_plans(),
        )
        return

    if text == _RKB_STATS:
        u    = get_user(uid)
        used = get_used_slots(uid)
        bots = get_user_bots(uid)
        run  = sum(1 for b in bots
                   if RUNNING_BOTS.get(b, {}).get("status") == "Running 🟢")
        await update.message.reply_text(
            f"📊 *Your Stats*\n\n"
            f"📦 Slots   : `{used}/{u['slots']}`\n"
            f"🤖 Bots    : `{len(bots)}` total\n"
            f"🟢 Running : `{run}`\n"
            f"⭐ Stars   : `{u.get('stars_spent',0)}`",
            parse_mode="Markdown",
        )
        return

    if text == _RKB_HELP:
        await update.message.reply_text(
            "📦 *How to Host Your Bot*\n\n"
            "*Method 1 — ZIP Upload*\n"
            "Zip your bot folder and send it here.\n"
            "Entry point: `main.py`\n\n"
            "*Method 2 — Python File*\n"
            "Send a `.py` file directly.\n\n"
            "*Method 3 — Paste Code* 🆕\n"
            "Paste your Python code as a message.\n"
            "We auto-detect the bot type, name it, and run it!\n\n"
            "*Package Detection* 🆕\n"
            "`requirements.txt` is optional — we auto-detect what to install!\n\n"
            "*Commands*\n"
            "`/stop <name>`    — Stop a bot\n"
            "`/restart <name>` — Restart a bot\n"
            "`/delete <name>`  — Delete a bot _(backed up in GitHub)_\n"
            "`/getlog <name>`  — Download bot log\n"
            "`/info`           — Your account info",
            parse_mode="Markdown",
        )
        return

    if text == _RKB_ADMIN and is_admin(uid):
        total   = len(RUNNING_BOTS)
        running = sum(1 for e in RUNNING_BOTS.values()
                      if e.get("status") == "Running 🟢")
        users   = len(load_users())
        await update.message.reply_text(
            f"👑 *Admin Panel*\n\n"
            f"🤖 Total Bots  : `{total}`\n"
            f"🟢 Running     : `{running}`\n"
            f"👥 Total Users : `{users}`\n\n"
            f"*Admin Commands:*\n"
            f"`/all`              — View all hosted bots\n"
            f"`/users`            — List all users\n"
            f"`/msg <text>`       — Broadcast message\n"
            f"`/msg <id> <text>`  — DM specific user\n"
            f"`/pr <id> <slots>`  — Set user slots\n"
            f"`/ban <id>`         — Ban a user\n"
            f"`/unban <id>`       — Unban a user\n"
            f"`/stop <name>`      — Stop any bot\n"
            f"`/delete <name>`    — Delete any bot\n"
            f"`/getlog <name>`    — Get bot log file",
            parse_mode="Markdown",
        )
        return

    # ── Direct Python code deploy ─────────────────────────────────
    if is_python_code(text):
        if not check_rate_limit(uid):
            await update.message.reply_text(
                "⏳ Rate limit: max 3 deploys per 10 min.", parse_mode="Markdown"
            )
            return

        user = get_user(uid)
        used = get_used_slots(uid)
        if used >= user["slots"] and not is_admin(uid):
            await update.message.reply_text(
                f"❌ *Slot limit reached!* `{used}/{user['slots']}`",
                parse_mode   = "Markdown",
                reply_markup = kb_plans(),
            )
            return

        bot_name = smart_bot_name(text, uid)
        msg = await update.message.reply_text(
            f"🔍 *Code detected!*\n📝 Bot name: *{bot_name}*\n⚙️ Setting up…",
            parse_mode="Markdown",
        )

        bot_dir = HOSTED_DIR / bot_name
        bot_dir.mkdir(parents=True, exist_ok=True)
        (bot_dir / "main.py").write_text(text, encoding="utf-8")

        await _finalize_deploy(bot_name, bot_dir, uid, msg, code_text=text)
        return

    # ── Fallback ──────────────────────────────────────────────────
    await update.message.reply_text(
        "💡 Send a `.zip` / `.py` file, or paste Python code to deploy a bot.\n"
        "Type /start for help.",
    )

# ══════════════════════════════════════════════════════════════════════
#  INLINE CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════════

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q   = update.callback_query
    uid = q.from_user.id
    d   = q.data or ""
    await q.answer()

    if d == "main_menu":
        await q.edit_message_text(
            home_text(uid),
            parse_mode   = "Markdown",
            reply_markup = kb_home(is_admin(uid)),
        )

    elif d == "my_bots":
        text, kb = mybots_card(uid)
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

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

    elif d.startswith("stop|"):
        name = d.split("|", 1)[1]
        reg  = load_registry()
        if not (is_admin(uid) or str(reg.get(name, {}).get("owner_id", "")) == str(uid)):
            await q.answer("❌ Not your bot!", show_alert=True)
            return
        _do_stop(name)
        await q.edit_message_text(
            f"🛑 *{name}* stopped.", parse_mode="Markdown", reply_markup=kb_back()
        )

    elif d.startswith("restart|"):
        name = d.split("|", 1)[1]
        reg  = load_registry()
        if not (is_admin(uid) or str(reg.get(name, {}).get("owner_id", "")) == str(uid)):
            await q.answer("❌ Not your bot!", show_alert=True)
            return
        bot_dir = HOSTED_DIR / name
        if not (bot_dir / "main.py").exists():
            await q.answer("❌ Bot files not found!", show_alert=True)
            return
        _do_stop(name)
        owner = reg.get(name, {}).get("owner_id", uid)
        await asyncio.sleep(2)
        asyncio.create_task(run_bot(name, bot_dir, owner))
        await q.edit_message_text(
            f"🔄 *{name}* restarted!", parse_mode="Markdown", reply_markup=kb_back()
        )

    elif d.startswith("delete|"):
        name = d.split("|", 1)[1]
        reg  = load_registry()
        if not (is_admin(uid) or str(reg.get(name, {}).get("owner_id", "")) == str(uid)):
            await q.answer("❌ Not your bot!", show_alert=True)
            return
        _kill_and_remove(name, deleted_by=uid)
        push_to_github(f"Deleted: {name} by {uid}")
        await q.edit_message_text(
            f"🗑 *{name}* deleted.\n_(Backed up in GitHub with `<deleted>` marker)_",
            parse_mode="Markdown", reply_markup=kb_back(),
        )

    elif d.startswith("logs|"):
        name     = d.split("|", 1)[1]
        log_path = HOSTED_DIR / name / "bot_output.log"
        tail     = _tail(log_path, 50) or "*(No logs yet.)*"
        if len(tail) > 3300:
            tail = "…(truncated)\n" + tail[-3200:]
        await q.edit_message_text(
            f"📋 *Logs — {name}*\n\n```\n{tail}\n```",
            parse_mode   = "Markdown",
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data=f"logs|{name}"),
                InlineKeyboardButton("🔙 Back",    callback_data=f"detail|{name}"),
            ]]),
        )

    elif d.startswith("getlog|"):
        name     = d.split("|", 1)[1]
        log_path = HOSTED_DIR / name / "bot_output.log"
        if not log_path.exists():
            await q.answer("No log file found.", show_alert=True)
            return
        await q.message.reply_document(
            document   = open(log_path, "rb"),
            filename   = f"{name}_log.txt",
            caption    = f"📋 Log for *{name}*",
            parse_mode = "Markdown",
        )

    elif d == "how_host":
        await q.edit_message_text(
            "📦 *How to Deploy*\n\n"
            "*Option 1 — ZIP*\nZip your folder → send here.\n\n"
            "*Option 2 — .py file*\nSend `main.py` directly.\n\n"
            "*Option 3 — Paste Code* 🆕\n"
            "Paste Python code as a message — we name it automatically!\n\n"
            "• No `requirements.txt`? We auto-detect packages! 🆕\n"
            "• Nested folder zips are auto-extracted.\n"
            "• Free plan: *3 bot slots*",
            parse_mode   = "Markdown",
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("💰 Buy More Slots", callback_data="buy_plan"),
                InlineKeyboardButton("🏠 Home",           callback_data="main_menu"),
            ]]),
        )

    elif d == "my_stats":
        u    = get_user(uid)
        used = get_used_slots(uid)
        bots = get_user_bots(uid)
        run  = sum(1 for b in bots
                   if RUNNING_BOTS.get(b, {}).get("status") == "Running 🟢")
        await q.edit_message_text(
            f"📊 *Your Stats*\n\n"
            f"🆔 ID      : `{uid}`\n"
            f"📦 Slots   : `{used}/{u['slots']}`\n"
            f"🤖 Bots    : `{len(bots)}` total, `{run}` running\n"
            f"⭐ Stars   : `{u.get('stars_spent',0)}`",
            parse_mode   = "Markdown",
            reply_markup = kb_back(),
        )

    elif d == "buy_plan":
        await q.edit_message_text(
            "💰 *Upgrade Your Plan*\n\nPay with Telegram ⭐ Stars — instant activation.",
            parse_mode   = "Markdown",
            reply_markup = kb_plans(),
        )

    elif d.startswith("buy_"):
        plan_key = d[4:]
        plan     = PLANS.get(plan_key)
        if not plan:
            await q.answer("Unknown plan.", show_alert=True)
            return
        await ctx.bot.send_invoice(
            chat_id        = uid,
            title          = plan["label"],
            description    = f"{plan['slots']} extra hosting slots — {plan['desc']}",
            payload        = f"{plan_key}|{uid}",
            provider_token = "",       # empty = Telegram Stars (XTR)
            currency       = "XTR",
            prices         = [LabeledPrice(plan["label"], plan["stars"])],
        )

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
            f"`/all` `/users` `/msg` `/pr` `/ban` `/unban` `/getlog`",
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
        await update.message.reply_text("⚠️ Payment received — contact admin to activate.")
        return

    new_slots = add_user_slots(uid, plan["slots"])
    add_user_stars(uid, plan["stars"])
    push_to_github(f"Stars purchase: {plan_key} by {uid}")

    await update.message.reply_text(
        f"🎉 *Payment Successful!*\n\n"
        f"📦 Plan      : *{plan['label']}*\n"
        f"➕ Added     : +{plan['slots']} slots\n"
        f"📊 New Total : *{new_slots}* slots\n\n"
        f"Go host your bots! 🚀\n_Codian Studio 💎_",
        parse_mode   = "Markdown",
        reply_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("📦 Deploy Now", callback_data="how_host"),
        ]]),
    )

# ══════════════════════════════════════════════════════════════════════
#  AUTO-START PERSISTED BOTS  (after Render cold-start)
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
        log.info("Auto-starting: %s (owner=%s)", item.name, owner)
        asyncio.create_task(run_bot(item.name, item, owner))

# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════

async def main() -> None:
    global _APP

    log.info("═══════════════════════════════════════════════════")
    log.info("   Master Hosting Bot  v3.0  —  Codian Studio 💎  ")
    log.info("═══════════════════════════════════════════════════")

    configure_git()
    sync_from_github()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DELETED_DIR.mkdir(parents=True, exist_ok=True)

    # ── Build PTB application ──────────────────────────────────────
    _APP = ApplicationBuilder().token(BOT_TOKEN).build()

    # ── Register all handlers ──────────────────────────────────────
    _APP.add_handler(CommandHandler("start",   cmd_start))
    _APP.add_handler(CommandHandler("mybots",  cmd_mybots))
    _APP.add_handler(CommandHandler("all",     cmd_all))
    _APP.add_handler(CommandHandler("users",   cmd_users))
    _APP.add_handler(CommandHandler("msg",     cmd_msg))
    _APP.add_handler(CommandHandler("stop",    cmd_stop))
    _APP.add_handler(CommandHandler("restart", cmd_restart))
    _APP.add_handler(CommandHandler("delete",  cmd_delete))
    _APP.add_handler(CommandHandler("pr",      cmd_pr))
    _APP.add_handler(CommandHandler("ban",     cmd_ban))
    _APP.add_handler(CommandHandler("unban",   cmd_unban))
    _APP.add_handler(CommandHandler("getlog",  cmd_getlog))
    _APP.add_handler(CommandHandler("info",    cmd_info))

    # File upload — catch ALL documents (ext check is inside handler)
    _APP.add_handler(MessageHandler(filters.Document.ALL, handle_upload))

    # Text — reply keyboard buttons + direct code deploy (no commands)
    _APP.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_text
    ))

    _APP.add_handler(CallbackQueryHandler(handle_callback))
    _APP.add_handler(PreCheckoutQueryHandler(pre_checkout))
    _APP.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, payment_success))

    # ── Web server (bind port BEFORE polling for Render health check)
    await start_web_server()

    # ── Background tasks ───────────────────────────────────────────
    asyncio.create_task(keep_alive_loop())

    # ── Re-launch previously hosted bots ──────────────────────────
    await autostart_persisted()

    # ── CRITICAL: kill any rival Render instance ───────────────────
    log.info("Clearing webhook and pending updates…")
    await _APP.bot.delete_webhook(drop_pending_updates=True)

    # ── PTB 20.x lifecycle — exact order matters ───────────────────
    await _APP.initialize()
    await _APP.start()
    await _APP.updater.start_polling(
        drop_pending_updates = True,
        allowed_updates      = Update.ALL_TYPES,
    )

    # ── Register command menu ──────────────────────────────────────
    try:
        await _APP.bot.set_my_commands([
            BotCommand("start",   "🏠 Home"),
            BotCommand("mybots",  "🤖 My hosted bots"),
            BotCommand("info",    "📊 My account info"),
            BotCommand("stop",    "🛑 Stop a bot"),
            BotCommand("restart", "🔄 Restart a bot"),
            BotCommand("delete",  "🗑 Delete a bot"),
            BotCommand("getlog",  "📋 Download bot log"),
            BotCommand("all",     "👑 Admin: all bots"),
            BotCommand("users",   "👑 Admin: all users"),
            BotCommand("msg",     "👑 Admin: send message"),
            BotCommand("pr",      "👑 Admin: set slots"),
            BotCommand("ban",     "👑 Admin: ban user"),
            BotCommand("unban",   "👑 Admin: unban user"),
        ])
    except Exception:
        pass

    log.info("✅  All systems operational — bot is live!")
    await asyncio.Event().wait()        # block without burning CPU


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped by user.")

