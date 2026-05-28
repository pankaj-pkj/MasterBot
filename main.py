"""
╔══════════════════════════════════════════════════════════════════════╗
║   MASTER HOSTING BOT  v4.0  —  Production Edition                   ║
║   python-telegram-bot 20.x  ·  Python 3.11  ·  Render.com           ║
║   Codian Studio 💎                                                   ║
╠══════════════════════════════════════════════════════════════════════╣
║  v4.0 Critical Fixes:                                                ║
║  • NAME COLLISION — bots stored as uid_botname (never overwrite)     ║
║  • WEB RUN FIX — restart_flag properly handled, bot stays alive      ║
║  • AUTO SELF-HEAL — common errors fixed automatically:               ║
║      - Missing package  → auto pip install + restart                 ║
║      - Conflict error   → delete webhook + restart                   ║
║      - Port in use      → find free port + patch                     ║
║      - Encoding error   → patch file encoding declaration            ║
║      - getUpdates flood → add sleep + restart                        ║
║  • Collision-safe deploy — zip name collision → uid prefix added     ║
║  • /users shows username + per-bot status                            ║
║  • All previous features retained                                    ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os, re, ast, sys, json, time, shutil, logging, asyncio, subprocess, socket
from zipfile import ZipFile, BadZipFile
from pathlib import Path

import aiohttp
from aiohttp import web

from telegram import (
    Update, BotCommand, LabeledPrice,
    KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, PreCheckoutQueryHandler, filters,
)

import editor as web_ide

# ══════════════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s │ %(levelname)-8s │ %(message)s",
    datefmt="%H:%M:%S", level=logging.INFO, stream=sys.stdout,
)
log = logging.getLogger("MasterBot")

# ══════════════════════════════════════════════════════════════════════
#  ENV
# ══════════════════════════════════════════════════════════════════════
BOT_TOKEN       = os.environ.get("BOT_TOKEN",       "")
GITHUB_PAT      = os.environ.get("GITHUB_PAT",      "")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "")
REPO_NAME       = os.environ.get("REPO_NAME",       "HostedBotsData")
RENDER_URL      = os.environ.get("RENDER_URL",      "").rstrip("/")
PORT            = int(os.environ.get("PORT",         8080))

if not BOT_TOKEN:
    log.critical("BOT_TOKEN not set — aborting."); sys.exit(1)

# ══════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════
ADMIN_IDS: set[int] = {6960252072}

FREE_SLOTS       = 3
MAX_FAST_CRASHES = 3
FAST_CRASH_SEC   = 30
MAX_HEAL_TRIES   = 2   # auto-heal attempts before giving up

PLANS = {
    "starter": {"stars": 15,  "slots": 3,  "label": "Starter ⭐",  "desc": "+3 slots"},
    "pro":     {"stars": 50,  "slots": 10, "label": "Pro 💎",      "desc": "+10 slots"},
    "elite":   {"stars": 100, "slots": 25, "label": "Elite 👑",    "desc": "+25 slots"},
}

STDLIB: set[str] = {
    "os","sys","re","io","json","time","math","copy","enum","abc","ast","csv",
    "gzip","hmac","html","http","uuid","glob","shutil","queue","array","struct",
    "socket","signal","string","random","hashlib","logging","pathlib","inspect",
    "functools","operator","itertools","contextlib","threading","subprocess",
    "traceback","datetime","calendar","textwrap","argparse","platform","tempfile",
    "unittest","dataclasses","collections","ftplib","smtplib","urllib","base64",
    "binascii","codecs","email","getpass","locale","weakref","warnings","typing",
    "types","builtins","gc","heapq","bisect","pprint","statistics","decimal",
    "fractions","zipfile","tarfile","configparser","pickle","shelve","sqlite3",
    "multiprocessing","concurrent","asyncio","ssl","select","selectors","mimetypes",
    "xml","timeit","dis","cmd","wave","colorsys","cProfile","numbers","difflib",
}

IMPORT_MAP = {
    "telegram":"python-telegram-bot","telebot":"pyTelegramBotAPI","aiogram":"aiogram",
    "pyrogram":"pyrogram","tgcrypto":"tgcrypto","telethon":"telethon",
    "requests":"requests","aiohttp":"aiohttp","httpx":"httpx","urllib3":"urllib3",
    "bs4":"beautifulsoup4","lxml":"lxml","selenium":"selenium","playwright":"playwright",
    "PIL":"Pillow","cv2":"opencv-python","numpy":"numpy","pandas":"pandas",
    "sklearn":"scikit-learn","scipy":"scipy","matplotlib":"matplotlib",
    "flask":"flask","fastapi":"fastapi","uvicorn":"uvicorn","django":"django",
    "starlette":"starlette","tornado":"tornado","sqlalchemy":"SQLAlchemy",
    "pymongo":"pymongo","motor":"motor","redis":"redis",
    "psycopg2":"psycopg2-binary","pymysql":"PyMySQL","aiosqlite":"aiosqlite",
    "aiofiles":"aiofiles","dotenv":"python-dotenv","yaml":"PyYAML","toml":"toml",
    "pydantic":"pydantic","click":"click","rich":"rich","loguru":"loguru",
    "tqdm":"tqdm","colorama":"colorama","apscheduler":"APScheduler",
    "schedule":"schedule","celery":"celery","cryptography":"cryptography",
    "jwt":"PyJWT","bcrypt":"bcrypt","qrcode":"qrcode","openai":"openai",
    "anthropic":"anthropic","langchain":"langchain","transformers":"transformers",
    "paramiko":"paramiko","tweepy":"tweepy","yt_dlp":"yt-dlp",
    "instaloader":"instaloader","discord":"discord.py",
}

_RKB = {
    "bots":  "🤖 My Bots",
    "dep":   "📦 Deploy",
    "slots": "💰 Buy Slots",
    "stats": "📊 Stats",
    "help":  "ℹ️ Help",
    "admin": "👑 Admin",
}

REPO_URL    = f"https://{GITHUB_PAT}@github.com/{GITHUB_USERNAME}/{REPO_NAME}.git"
HOSTED_DIR  = Path("hosted_bots")
DATA_DIR    = HOSTED_DIR / "_data"
DELETED_DIR = HOSTED_DIR / "_deleted"

RUNNING_BOTS: dict[str, dict]        = {}
DEPLOY_TIMES: dict[int, list[float]] = {}
USER_NAMES:   dict[str, str]         = {}
_APP = None

# ══════════════════════════════════════════════════════════════════════
#  BOT NAME SYSTEM  — uid-prefixed to prevent collision
# ══════════════════════════════════════════════════════════════════════

def make_bot_key(uid: int, raw_name: str) -> str:
    """
    Internal bot key = f"{uid}_{raw_name}".
    This guarantees two users with same zip name never collide.
    Display name shown to user is still raw_name.
    """
    clean = re.sub(r"[^a-zA-Z0-9_-]", "_", raw_name)[:40]
    return f"{uid}_{clean}"


def display_name(bot_key: str) -> str:
    """Strip uid prefix for user-facing display."""
    parts = bot_key.split("_", 1)
    if len(parts) == 2 and parts[0].isdigit():
        return parts[1]
    return bot_key


def is_owner_key(bot_key: str, uid: int) -> bool:
    return bot_key.startswith(f"{uid}_")


# ══════════════════════════════════════════════════════════════════════
#  SMART ERROR PARSER
# ══════════════════════════════════════════════════════════════════════

_ERR_PATTERNS = [
    (re.compile(r"ModuleNotFoundError: No module named '([^']+)'"),
     "📦 Package '{0}' missing. Auto-installing…"),
    (re.compile(r"ImportError: cannot import name '([^']+)' from '([^']+)'"),
     "⚠️ Wrong import '{0}' from '{1}'. Check library docs."),
    (re.compile(r"SyntaxError: (.+)"),
     "✏️ Syntax error: {0}. Check brackets/colons/quotes."),
    (re.compile(r"IndentationError: (.+)"),
     "↔️ Indentation error: {0}. Use 4 spaces consistently."),
    (re.compile(r"NameError: name '([^']+)' is not defined"),
     "❓ '{0}' not defined. Check spelling or define before use."),
    (re.compile(r"TypeError: (.+)"),
     "🔢 Type error: {0}. Check argument types."),
    (re.compile(r"AttributeError: '([^']+)' object has no attribute '([^']+)'"),
     "🔍 '{0}' has no attribute '{1}'. Check object type."),
    (re.compile(r"KeyError: '?([^'\n]+)'?"),
     "🗝️ Key '{0}' not found in dict. Check key name."),
    (re.compile(r"Conflict: terminated by other getUpdates"),
     "⚡ Conflict: another instance running. Auto-fixing…"),
    (re.compile(r"Unauthorized"),
     "🔑 Unauthorized. Check your bot token."),
    (re.compile(r"ConnectionRefusedError"),
     "🔌 Connection refused. Check host/port."),
    (re.compile(r"FileNotFoundError: .+: '([^']+)'"),
     "📁 File not found: '{0}'. Check the file path."),
    (re.compile(r"JSONDecodeError"),
     "📋 JSON decode error. Check your JSON format."),
    (re.compile(r"RecursionError"),
     "♾️ Infinite recursion. Add a base case."),
    (re.compile(r"ZeroDivisionError"),
     "➗ Division by zero. Check your divisor."),
    (re.compile(r"MemoryError"),
     "💾 Out of memory. Reduce data size."),
    (re.compile(r"OSError: \[Errno 98\]"),
     "🔌 Address already in use. Port conflict."),
    (re.compile(r"RuntimeError: (.+)"),
     "💥 RuntimeError: {0}."),
    (re.compile(r"ValueError: (.+)"),
     "⚡ ValueError: {0}. Check data being processed."),
]


def parse_error(text: str) -> dict:
    result = {
        "has_error":   False,
        "error_type":  "Unknown",
        "error_msg":   "",
        "file_name":   None,
        "line_no":     None,
        "code_snippet":None,
        "hint":        "Check the output panel.",
        "module":      None,
    }
    if not text: return result
    lines = text.splitlines()

    # Find last traceback
    tb_start = next((i for i in range(len(lines)-1,-1,-1)
                     if "Traceback (most recent call last)" in lines[i]), -1)
    if tb_start == -1:
        for line in reversed(lines[-30:]):
            for pat, hint in _ERR_PATTERNS:
                m = pat.search(line)
                if m:
                    result.update(has_error=True, error_msg=line.strip(),
                                  error_type=line.split(":")[0].strip(),
                                  hint=hint.format(*m.groups()) if m.groups() else hint)
                    _extract_module(result, line)
                    return result
        return result

    result["has_error"] = True
    tb = lines[tb_start:]

    for ln in reversed(tb):
        m = re.search(r'File "([^"]+)", line (\d+)', ln)
        if m:
            try: result["file_name"] = str(Path(m.group(1)).relative_to(Path.cwd()))
            except: result["file_name"] = m.group(1).split("/")[-1]
            result["line_no"] = int(m.group(2))
            break

    for i, ln in enumerate(tb):
        if re.search(r'File "([^"]+)", line (\d+)', ln):
            if i+1 < len(tb):
                s = tb[i+1].strip()
                if s and not s.startswith("File "): result["code_snippet"] = s

    for ln in reversed(tb):
        s = ln.strip()
        if s and not s.startswith("File ") and not s.startswith("Traceback") and not s.startswith(" "):
            result["error_msg"]  = s
            result["error_type"] = s.split(":")[0].strip()
            break

    tail = "\n".join(tb[-10:])
    for pat, hint in _ERR_PATTERNS:
        m = pat.search(tail)
        if m:
            result["hint"] = hint.format(*m.groups()) if m.groups() else hint
            _extract_module(result, tail)
            break
    return result


def _extract_module(r: dict, text: str):
    m = re.search(r"No module named '([^']+)'", text)
    if m: r["module"] = m.group(1)


def fmt_error_tg(name: str, parsed: dict, runtime: float) -> str:
    dname = display_name(name)
    if not parsed["has_error"]:
        return (f"⚠️ *{dname}* crashed after `{runtime:.1f}s`\n"
                f"No traceback found. Use /getlog to get the log file.")
    loc   = f"\n📍 `{parsed['file_name']}` → Line `{parsed['line_no']}`" if parsed["file_name"] and parsed["line_no"] else ""
    snip  = f"\n\n```python\n{parsed['code_snippet'][:200]}\n```" if parsed["code_snippet"] else ""
    return (
        f"🚨 *{dname}* — Auto-stopped\n"
        f"⏱ Runtime: `{runtime:.1f}s`\n\n"
        f"❌ `{parsed['error_type']}`\n"
        f"`{parsed['error_msg'][:180]}`"
        f"{loc}{snip}\n\n"
        f"💡 {parsed['hint']}\n\n"
        f"/getlog — download full log\n/ide — edit in Web IDE"
    )


def fmt_error_terminal(parsed: dict, runtime: float) -> list[str]:
    out = ["─"*55, f"💥 CRASHED  (runtime {runtime:.1f}s)", "─"*55]
    if parsed["has_error"]:
        out += [
            f"❌ {parsed['error_type']}: {parsed['error_msg'][:300]}",
            f"📍 {parsed['file_name']} → Line {parsed['line_no']}" if parsed["file_name"] else "",
            f"💻 {parsed['code_snippet']}" if parsed["code_snippet"] else "",
            "",
            f"💡 {parsed['hint']}",
        ]
    out.append("─"*55)
    return [l for l in out if l is not None]


# ══════════════════════════════════════════════════════════════════════
#  AUTO SELF-HEAL ENGINE
# ══════════════════════════════════════════════════════════════════════

async def try_heal(name: str, bot_dir: Path, parsed: dict, heal_count: int) -> bool:
    """
    Attempt to auto-fix a common error. Returns True if a fix was applied
    (caller should restart the bot). Returns False if unfixable.
    """
    if heal_count >= MAX_HEAL_TRIES:
        return False

    err_type = parsed.get("error_type", "")
    err_msg  = parsed.get("error_msg", "")
    module   = parsed.get("module")
    main_py  = bot_dir / "main.py"

    # ── Fix 1: Missing module → pip install ───────────────────────────
    if module and "ModuleNotFoundError" in err_type:
        pkg = IMPORT_MAP.get(module, module)
        log.info("AUTO-HEAL [%s]: installing %s", name, pkg)
        ret = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg, "-q"],
            timeout=120, capture_output=True
        )
        if ret.returncode == 0:
            _write_log(bot_dir, f"[AUTO-HEAL] Installed {pkg} — restarting…\n")
            return True
        _write_log(bot_dir, f"[AUTO-HEAL] pip install {pkg} failed\n")
        return False

    # ── Fix 2: Telegram Conflict → delete webhook ─────────────────────
    if "Conflict" in err_msg and ("getUpdates" in err_msg or "Webhook" in err_msg):
        log.info("AUTO-HEAL [%s]: deleting webhook", name)
        _write_log(bot_dir, "[AUTO-HEAL] Conflict detected — deleting webhook…\n")
        if main_py.exists():
            code = main_py.read_text(errors="replace")
            if "delete_webhook" not in code:
                # Patch: insert delete_webhook call before start_polling
                patched = re.sub(
                    r"(start_polling|run_polling|start\(\))",
                    r"bot.delete_webhook(drop_pending_updates=True)\n    \1",
                    code, count=1
                )
                if patched != code:
                    main_py.write_text(patched, encoding="utf-8")
                    _write_log(bot_dir, "[AUTO-HEAL] Patched: added delete_webhook — restarting…\n")
                    return True
        return False

    # ── Fix 3: Address already in use (port conflict) ─────────────────
    if "Errno 98" in err_msg or "Address already in use" in err_msg:
        log.info("AUTO-HEAL [%s]: port conflict", name)
        if main_py.exists():
            code = main_py.read_text(errors="replace")
            # Find a free port
            free_port = _find_free_port()
            patched   = re.sub(r"port\s*=\s*\d+", f"port={free_port}", code, flags=re.IGNORECASE)
            patched   = re.sub(r"PORT\s*=\s*\d+", f"PORT={free_port}", patched)
            if patched != code:
                main_py.write_text(patched, encoding="utf-8")
                _write_log(bot_dir, f"[AUTO-HEAL] Port patched to {free_port} — restarting…\n")
                return True
        return False

    # ── Fix 4: UnicodeDecodeError / encoding issues ───────────────────
    if "UnicodeDecodeError" in err_type or "codec can't decode" in err_msg:
        log.info("AUTO-HEAL [%s]: encoding fix", name)
        if main_py.exists():
            code = main_py.read_text(errors="replace")
            if "# -*- coding:" not in code:
                patched = "# -*- coding: utf-8 -*-\n" + code
                main_py.write_text(patched, encoding="utf-8")
                _write_log(bot_dir, "[AUTO-HEAL] Added UTF-8 encoding declaration — restarting…\n")
                return True
        return False

    # ── Fix 5: getUpdates flood / 429 too many requests ───────────────
    if "429" in err_msg or "Too Many Requests" in err_msg or "Flood" in err_msg:
        log.info("AUTO-HEAL [%s]: flood wait", name)
        _write_log(bot_dir, "[AUTO-HEAL] Flood control — waiting 30s before restart…\n")
        await asyncio.sleep(30)
        return True

    # ── Fix 6: SyntaxError — report only, can't auto-fix ─────────────
    if "SyntaxError" in err_type or "IndentationError" in err_type:
        _write_log(bot_dir, "[AUTO-HEAL] Syntax/Indentation error cannot be auto-fixed. Edit in /ide\n")
        return False

    return False


def _write_log(bot_dir: Path, msg: str):
    try:
        with open(bot_dir / "bot_output.log", "a", encoding="utf-8") as f:
            f.write(f"\n{msg}")
    except: pass


def _find_free_port(start=8100, end=9000) -> int:
    for p in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", p))
                return p
            except OSError:
                continue
    return start


# ══════════════════════════════════════════════════════════════════════
#  JSON PERSISTENCE
# ══════════════════════════════════════════════════════════════════════

def _jload(p: Path, d):
    try:    return json.loads(p.read_text(encoding="utf-8"))
    except: return d

def _jsave(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def load_users():    return _jload(DATA_DIR / "users.json", {})
def save_users(d):   _jsave(DATA_DIR / "users.json", d)
def load_registry(): return _jload(DATA_DIR / "registry.json", {})
def save_registry(d):_jsave(DATA_DIR / "registry.json", d)

def load_usernames():
    global USER_NAMES; USER_NAMES = _jload(DATA_DIR / "usernames.json", {})

def save_username(uid: int, name: str):
    if name: USER_NAMES[str(uid)] = name; _jsave(DATA_DIR / "usernames.json", USER_NAMES)

def get_user(uid: int) -> dict:
    data = load_users(); k = str(uid)
    if k not in data:
        data[k] = {"slots": FREE_SLOTS, "stars_spent": 0, "banned": False}
        save_users(data)
    return data[k]

def _upd_user(uid, patch):
    data = load_users(); k = str(uid)
    if k not in data: data[k] = {"slots": FREE_SLOTS, "stars_spent": 0, "banned": False}
    data[k].update(patch); save_users(data)

def set_user_slots(uid, s):   _upd_user(uid, {"slots": s})
def add_user_slots(uid, n):
    u = get_user(uid); new = u["slots"] + n; set_user_slots(uid, new); return new
def add_user_stars(uid, s):
    u = get_user(uid); _upd_user(uid, {"stars_spent": u.get("stars_spent", 0) + s})
def is_banned(uid): return bool(get_user(uid).get("banned", False))
def ban_user(uid):   _upd_user(uid, {"banned": True})
def unban_user(uid): _upd_user(uid, {"banned": False})

def register_bot(bot_key: str, uid: int):
    reg = load_registry()
    reg[bot_key] = {"owner_id": uid, "registered_at": time.time(),
                    "display_name": display_name(bot_key)}
    save_registry(reg)

def unregister_bot(bot_key: str):
    reg = load_registry(); reg.pop(bot_key, None); save_registry(reg)

def get_used_slots(uid: int) -> int:
    return sum(1 for v in load_registry().values() if str(v.get("owner_id")) == str(uid))

def get_user_bots(uid: int) -> list[str]:
    return [k for k, v in load_registry().items()
            if str(v.get("owner_id")) == str(uid)]

def soft_delete_bot(bot_key: str, deleted_by: int):
    src = HOSTED_DIR / bot_key
    reg = load_registry()
    owner_uid = str(reg.get(bot_key, {}).get("owner_id", deleted_by))
    if src.exists():
        ts   = int(time.time())
        dest = DELETED_DIR / owner_uid / f"{bot_key}_{ts}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        _jsave(dest / "_DELETED.json", {
            "bot_key": bot_key, "display_name": display_name(bot_key),
            "owner_uid": owner_uid, "deleted_by": deleted_by,
            "deleted_at": ts, "marker": "<deleted>",
        })
    unregister_bot(bot_key)


# ══════════════════════════════════════════════════════════════════════
#  GIT SYNC
# ══════════════════════════════════════════════════════════════════════

def _git(cmd): return os.system(cmd + " > /dev/null 2>&1")

def configure_git():
    _git('git config --global user.email "masterbot@render.com"')
    _git('git config --global user.name "MasterHostingBot"')

def sync_from_github():
    if not GITHUB_PAT:
        log.warning("GITHUB_PAT not set — local only."); HOSTED_DIR.mkdir(exist_ok=True); return
    if (HOSTED_DIR / ".git").exists():
        log.info("Pulling repo…")
        if _git(f'cd "{HOSTED_DIR}" && git pull') != 0:
            shutil.rmtree(HOSTED_DIR, ignore_errors=True); _clone()
    else:
        shutil.rmtree(HOSTED_DIR, ignore_errors=True); _clone()

def _clone():
    log.info("Cloning repo…")
    if _git(f'git clone "{REPO_URL}" "{HOSTED_DIR}"') != 0:
        log.error("Clone failed."); HOSTED_DIR.mkdir(exist_ok=True)

def push_to_github(msg="Update"):
    if not GITHUB_PAT: return
    _git(f'cd "{HOSTED_DIR}" && git add -A && '
         f'(git diff --cached --quiet || git commit -m "{msg}") && git push')


# ══════════════════════════════════════════════════════════════════════
#  WEB SERVER
# ══════════════════════════════════════════════════════════════════════

async def _web_root(req: web.Request) -> web.Response:
    ide  = f"{RENDER_URL}/editor" if RENDER_URL else "/editor"
    rows = "".join(
        f"<tr><td><b>{display_name(n)}</b></td><td>{d.get('status','?')}</td>"
        f"<td>{fmt_up(time.time()-d.get('start_time',time.time()))}</td>"
        f"<td>{d.get('restarts',0)}</td></tr>"
        for n, d in RUNNING_BOTS.items()
    )
    return web.Response(content_type="text/html", text=(
        "<html><head><title>Master Hosting Bot</title>"
        "<style>body{font-family:monospace;padding:20px;background:#1e1e1e;color:#d4d4d4}"
        "h2{color:#4fc3f7}.btn{display:inline-block;background:#0e639c;color:#fff;"
        "padding:8px 20px;border-radius:4px;text-decoration:none;margin:12px 0}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #3c3c3c;padding:8px}"
        "tr:nth-child(even){background:#252526}</style></head><body>"
        f"<h2>🤖 Master Hosting Bot v4.0</h2>"
        f"<a class='btn' href='{ide}'>💎 Web IDE</a><br><br>"
        f"<table><tr><th>Bot</th><th>Status</th><th>Uptime</th><th>Restarts</th></tr>"
        f"{rows}</table><p style='color:#555;margin-top:14px'><i>Codian Studio 💎</i></p></body></html>"
    ))

async def start_web_server():
    wa = web.Application(client_max_size=128 * 1024 * 1024)
    wa.router.add_get("/",       _web_root)
    wa.router.add_get("/health", lambda r: web.Response(text="OK"))
    web_ide.register_routes(wa)
    runner = web.AppRunner(wa); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Web + IDE on port %d", PORT)

async def keep_alive_loop():
    if not RENDER_URL: return
    await asyncio.sleep(90)
    async with aiohttp.ClientSession() as sess:
        while True:
            try:
                async with sess.get(f"{RENDER_URL}/health",
                                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                    log.info("Ping → %d", r.status)
            except Exception as e: log.warning("Ping: %s", e)
            await asyncio.sleep(840)


# ══════════════════════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════════════════════

def fmt_up(s):
    s = int(max(0, s)); h, r = divmod(s, 3600); m, sc = divmod(r, 60)
    return f"{h}h {m}m {sc}s"

def is_admin(uid): return uid in ADMIN_IDS
def is_banned(uid): return bool(get_user(uid).get("banned", False))

def speed_lbl(e):
    r = e.get("restarts", 0)
    return ("🚀 Excellent" if r == 0 else "⚡ Good" if r < 3
            else "🐢 Unstable" if r < 10 else "💀 Critical")

def _tail(p, n=120):
    try: return "\n".join(p.read_text(errors="replace").splitlines()[-n:])
    except: return ""

def check_rate(uid):
    if is_admin(uid): return True
    now = time.time(); ts = [t for t in DEPLOY_TIMES.get(uid, []) if now - t < 600]
    if len(ts) >= 3: return False
    ts.append(now); DEPLOY_TIMES[uid] = ts; return True

def detect_imports(code):
    names = []
    try:
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, ast.Import):
                for a in node.names: names.append(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module.split(".")[0])
    except SyntaxError:
        names = re.findall(r"^(?:import|from)\s+([a-zA-Z_]\w*)", code, re.MULTILINE)
    return list(set(names))

def smart_install(code) -> list[str]:
    to_install = []
    for imp in detect_imports(code):
        if imp in STDLIB: continue
        pkg = IMPORT_MAP.get(imp, imp)
        if subprocess.run([sys.executable, "-c", f"import {imp}"],
                          capture_output=True).returncode != 0:
            to_install.append(pkg)
    if to_install:
        log.info("Auto-installing: %s", to_install)
        subprocess.run([sys.executable, "-m", "pip", "install"] + to_install + ["-q"],
                       check=False, timeout=300)
    return to_install

def _install_reqs(req):
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req), "-q"],
                   check=False, timeout=300)

_CODE_RE = re.compile(
    r"(^\s*(import|from)\s+\w+|^\s*(def|async\s+def|class)\s+\w+|"
    r"if\s+__name__\s*==|^\s*@\w+)", re.MULTILINE)

def is_code(text): return len(text) > 20 and bool(_CODE_RE.search(text))

def smart_name(code, uid):
    lower = code.lower()
    if any(k in lower for k in ("telegram", "telebot", "aiogram", "pyrogram")): pf = "tgbot"
    elif any(k in lower for k in ("discord",)): pf = "dsbot"
    elif any(k in lower for k in ("flask", "fastapi", "django")): pf = "webapp"
    elif any(k in lower for k in ("scrape", "bs4", "selenium")): pf = "scraper"
    elif any(k in lower for k in ("schedule", "cron")): pf = "scheduler"
    elif any(k in lower for k in ("openai", "anthropic", "gpt")): pf = "aibot"
    else: pf = "script"
    m = re.search(r"class\s+([A-Za-z]\w+)", code)
    if m: raw = m.group(1).lower()[:20]
    else:
        m = re.search(r"#\s*([A-Za-z][A-Za-z0-9 _-]+)", code)
        raw = m.group(1).strip().lower()[:20].replace(" ", "_") if m else f"{pf}_{int(time.time())%9999}"
    return make_bot_key(uid, raw)

def _do_stop(bot_key: str):
    e = RUNNING_BOTS.get(bot_key, {})
    if e:
        e["active"] = False
        p = e.get("process")
        if p and p.poll() is None:
            p.terminate()
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill()
        e["status"] = "Stopped 🛑"

def _kill_remove(bot_key: str, deleted_by=0):
    _do_stop(bot_key); RUNNING_BOTS.pop(bot_key, None)
    soft_delete_bot(bot_key, deleted_by)
    shutil.rmtree(HOSTED_DIR / bot_key, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
#  CHILD-BOT RUNNER  v4.0
#  • Proper restart_flag handling — web IDE run button works correctly
#  • Auto self-heal before giving up
#  • Fast crash counter reset on heal
# ══════════════════════════════════════════════════════════════════════

async def run_bot(bot_key: str, bot_dir: Path, owner_id: int):
    log_path = bot_dir / "bot_output.log"

    RUNNING_BOTS[bot_key] = {
        "active":     True,
        "start_time": time.time(),
        "status":     "Starting ⏳",
        "process":    None,
        "restarts":   0,
        "heal_tries": 0,
        "last_error": None,
        "parsed_err": None,
        "owner_id":   owner_id,
    }
    fast_crashes = 0

    while RUNNING_BOTS.get(bot_key, {}).get("active"):
        # ── Clear restart flag if set by Web IDE ──────────────────────
        flag = bot_dir / ".restart_flag"
        if flag.exists():
            try: flag.unlink()
            except: pass

        t0 = time.time()
        RUNNING_BOTS[bot_key]["status"] = "Running 🟢"
        lf = None

        try:
            lf = open(log_path, "a", encoding="utf-8", errors="replace")
            lf.write(f"\n{'─'*55}\n"
                     f"[START] {display_name(bot_key)}  ·  {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                     f"{'─'*55}\n")
            lf.flush()
            proc = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd=str(bot_dir), stdout=lf, stderr=lf,
            )
        except Exception as exc:
            if lf:
                try: lf.close()
                except: pass
            RUNNING_BOTS[bot_key].update(
                status="Launch Error ❌", active=False, last_error=str(exc))
            await _notify(owner_id, f"❌ *{display_name(bot_key)}* failed to launch:\n`{exc}`")
            break

        RUNNING_BOTS[bot_key]["process"] = proc

        # ── Non-blocking wait; poll for restart_flag ───────────────────
        while proc.poll() is None:
            await asyncio.sleep(1.5)
            # Web IDE ▶ Run pressed → flag created → stop cleanly
            if (bot_dir / ".restart_flag").exists():
                log.info("Web IDE restart → %s", bot_key)
                try: (bot_dir / ".restart_flag").unlink()
                except: pass
                proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill()
                # Reset state for clean restart
                RUNNING_BOTS[bot_key]["status"]     = "Restarting ⏳"
                RUNNING_BOTS[bot_key]["start_time"] = time.time()
                fast_crashes = 0   # clean restart doesn't count as crash
                break

        try: lf.close()
        except: pass

        runtime = time.time() - t0
        tail    = _tail(log_path, 150)
        parsed  = parse_error(tail)
        RUNNING_BOTS[bot_key]["last_error"] = tail
        RUNNING_BOTS[bot_key]["parsed_err"] = parsed
        RUNNING_BOTS[bot_key]["restarts"]  += 1

        # Write error block to log (visible in IDE Output panel)
        if parsed["has_error"]:
            try:
                with open(log_path, "a", encoding="utf-8", errors="replace") as lf2:
                    lf2.write("\n")
                    for line in fmt_error_terminal(parsed, runtime):
                        lf2.write(line + "\n")
            except: pass

        # Check if we stopped intentionally
        if not RUNNING_BOTS.get(bot_key, {}).get("active"):
            RUNNING_BOTS[bot_key]["status"] = "Stopped 🛑"
            break

        log.warning("%s exited (runtime=%.1fs)", bot_key, runtime)

        # ── Fast crash detection ───────────────────────────────────────
        if runtime < FAST_CRASH_SEC:
            fast_crashes += 1

            # ── Auto-heal attempt ──────────────────────────────────────
            heal_count = RUNNING_BOTS[bot_key]["heal_tries"]
            if parsed["has_error"] and heal_count < MAX_HEAL_TRIES:
                RUNNING_BOTS[bot_key]["status"] = "Healing 🔧"
                healed = await try_heal(bot_key, bot_dir, parsed, heal_count)
                RUNNING_BOTS[bot_key]["heal_tries"] += 1
                if healed:
                    fast_crashes = 0   # reset — heal counts as a clean restart
                    RUNNING_BOTS[bot_key]["status"] = "Restarting ⏳"
                    await asyncio.sleep(3)
                    continue

            if fast_crashes >= MAX_FAST_CRASHES:
                RUNNING_BOTS[bot_key].update(active=False, status="Error ❌")
                await _notify(owner_id, fmt_error_tg(bot_key, parsed, runtime))
                break
        else:
            fast_crashes = 0
            RUNNING_BOTS[bot_key]["heal_tries"] = 0  # reset on successful long run

        RUNNING_BOTS[bot_key]["status"] = "Restarting ⏳"
        await asyncio.sleep(5)


async def _notify(owner_id, text):
    if not _APP or not owner_id: return
    try: await _APP.bot.send_message(owner_id, text, parse_mode="Markdown")
    except Exception as e: log.warning("Notify: %s", e)


# ══════════════════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════════════════

def reply_kb(admin=False):
    rows = [
        [KeyboardButton(_RKB["bots"]),  KeyboardButton(_RKB["dep"])],
        [KeyboardButton(_RKB["slots"]), KeyboardButton(_RKB["stats"])],
        [KeyboardButton(_RKB["help"])],
    ]
    if admin: rows.append([KeyboardButton(_RKB["admin"])])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)

def kb_home(admin=False):
    rows = [
        [InlineKeyboardButton("🤖 My Bots",  callback_data="my_bots"),
         InlineKeyboardButton("📦 Deploy",   callback_data="how_host")],
        [InlineKeyboardButton("💰 Buy Slots", callback_data="buy_plan"),
         InlineKeyboardButton("📊 Stats",    callback_data="my_stats")],
    ]
    if admin: rows.append([InlineKeyboardButton("👑 Admin", callback_data="admin_panel")])
    return InlineKeyboardMarkup(rows)

def kb_plans():
    rows = [[InlineKeyboardButton(
        f"{p['label']} — {p['stars']} ⭐ ({p['desc']})",
        callback_data=f"buy_{k}")] for k, p in PLANS.items()]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)

def kb_bot_card(bot_key, can):
    rows = []
    if can:
        dname = display_name(bot_key)
        rows.append([
            InlineKeyboardButton("🛑 Stop",    callback_data=f"stop|{bot_key}"),
            InlineKeyboardButton("🔄 Restart", callback_data=f"restart|{bot_key}"),
            InlineKeyboardButton("🗑 Delete",  callback_data=f"delete|{bot_key}"),
        ])
        rows.append([
            InlineKeyboardButton("📋 Logs",    callback_data=f"logs|{bot_key}"),
            InlineKeyboardButton("🔍 Error",   callback_data=f"errinfo|{bot_key}"),
            InlineKeyboardButton("⬇ Log",     callback_data=f"getlog|{bot_key}"),
        ])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="my_bots")])
    return InlineKeyboardMarkup(rows)

def kb_back(to="main_menu"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "🏠 Home" if to == "main_menu" else "🔙 Back", callback_data=to)]])


# ══════════════════════════════════════════════════════════════════════
#  DISPLAY
# ══════════════════════════════════════════════════════════════════════

def home_text(uid):
    u = get_user(uid); used = get_used_slots(uid)
    plan = "Admin 👑" if is_admin(uid) else "Free ✨"
    return (f"🤖 *Master Hosting Bot v4.0*\n\n"
            f"👤 Plan  : {plan}\n"
            f"📦 Slots : `{used}/{u['slots']}` used\n\n"
            f"Send `.zip`, `.py` or paste Python code!\n"
            f"💎 Web IDE: /ide\n\n_Codian Studio 💎_")

def mybots_card(uid):
    bots = get_user_bots(uid)
    if not bots:
        return ("📭 *No bots hosted yet.*\n\n"
                "Send a `.zip`/`.py` or paste Python code!"), kb_back()
    lines = ["🤖 *Your Hosted Bots*\n"]; rows = []
    for bot_key in bots:
        e     = RUNNING_BOTS.get(bot_key, {})
        dname = display_name(bot_key)
        st    = e.get("status", "Offline 🔴")
        up    = fmt_up(time.time() - e["start_time"]) if e.get("start_time") else "—"
        rs    = e.get("restarts", 0)
        hl    = e.get("heal_tries", 0)
        heal  = f"  🔧 Healed `{hl}×`" if hl > 0 else ""
        lines.append(
            f"🔹 *{dname}*\n"
            f"   {st}  ·  ⏱ `{up}`\n"
            f"   {speed_lbl(e) if e else '—'}  ·  🔄 `{rs}`{heal}\n"
        )
        rows.append([InlineKeyboardButton(f"⚙️  {dname}", callback_data=f"detail|{bot_key}")])
    rows.append([InlineKeyboardButton("🏠 Home", callback_data="main_menu")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


# ══════════════════════════════════════════════════════════════════════
#  SHARED DEPLOY FINALIZE
# ══════════════════════════════════════════════════════════════════════

async def _finalize(bot_key: str, bot_dir: Path, uid: int, msg, code_text=""):
    user = get_user(uid)
    code = code_text or (bot_dir / "main.py").read_text(errors="replace")
    req  = bot_dir / "requirements.txt"
    if req.exists():
        await msg.edit_text("⚙️ Installing requirements…", parse_mode="Markdown")
        _install_reqs(req)
    else:
        await msg.edit_text("🔍 Auto-detecting packages…", parse_mode="Markdown")
        installed = smart_install(code)
        if installed: log.info("Auto-installed: %s", installed)
    if bot_key in RUNNING_BOTS: _do_stop(bot_key); await asyncio.sleep(2)
    register_bot(bot_key, uid)
    push_to_github(f"Deploy uid={uid}: {display_name(bot_key)}")
    asyncio.create_task(run_bot(bot_key, bot_dir, uid))
    used = get_used_slots(uid)
    dname = display_name(bot_key)
    await msg.edit_text(
        f"✅ *{dname}* deployed!\n\n"
        f"📦 Slots: `{used}/{user['slots']}`\n"
        f"🔧 Auto-heal: enabled (fixes missing packages, conflicts)\n"
        f"💎 Edit in Web IDE: /ide\n\n"
        f"_Crash errors will be sent here with fix hints._",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🤖 My Bots", callback_data="my_bots"),
            InlineKeyboardButton("🏠 Home",    callback_data="main_menu"),
        ]]),
    )


# ══════════════════════════════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    name = update.effective_user.username or update.effective_user.first_name or ""
    save_username(uid, name); get_user(uid)
    if is_banned(uid): await update.message.reply_text("🚫 Banned."); return
    await update.message.reply_text(
        home_text(uid), parse_mode="Markdown", reply_markup=reply_kb(is_admin(uid)))
    await update.message.reply_text("👇 Use the menu:", reply_markup=kb_home(is_admin(uid)))


async def cmd_ide(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_banned(uid): return
    token = web_ide.get_user_token(uid)
    url   = (f"{RENDER_URL}/editor?token={token}"
             if RENDER_URL else f"http://localhost:{PORT}/editor?token={token}")
    await update.message.reply_text(
        f"💎 *Your Web IDE*\n\n"
        f"🔗 [Open IDE]({url})\n\n"
        f"🔑 Token:\n`{token}`\n\n"
        f"⚠️ Keep this private! /rotatetoken to get a new one.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🌐 Open Web IDE", url=url)
        ]]),
    )


async def cmd_rotatetoken(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    if is_banned(uid): return
    token = web_ide.rotate_user_token(uid)
    url   = f"{RENDER_URL}/editor?token={token}" if RENDER_URL else f"/editor?token={token}"
    await update.message.reply_text(
        f"🔐 *Token rotated!*\n\nNew token:\n`{token}`\n\n[Open IDE]({url})",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Open IDE", url=url)]]),
    )


async def cmd_mybots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_banned(uid): return
    text, kb = mybots_card(uid)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): await update.message.reply_text("❌ Admin only."); return
    if not RUNNING_BOTS: await update.message.reply_text("📭 No bots."); return
    reg   = load_registry(); lines = ["👑 *All Bots*\n"]
    for bot_key, e in RUNNING_BOTS.items():
        up  = fmt_up(time.time() - e.get("start_time", time.time()))
        own = reg.get(bot_key, {}).get("owner_id", "?")
        un  = USER_NAMES.get(str(own), "?")
        rs  = e.get("restarts", 0)
        hl  = e.get("heal_tries", 0)
        lines.append(
            f"🔹 *{display_name(bot_key)}*\n"
            f"   @{un} (`{own}`)  {e.get('status','?')}\n"
            f"   ⏱`{up}`  🔄`{rs}`  🔧`{hl}` heals  {speed_lbl(e)}\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): await update.message.reply_text("❌ Admin only."); return
    users = load_users(); reg = load_registry(); total = len(users)
    lines = [f"👥 *All Users ({total})*\n"]
    for k, v in list(users.items()):
        bots  = [bk for bk, rv in reg.items() if str(rv.get("owner_id")) == k]
        run   = sum(1 for bk in bots if RUNNING_BOTS.get(bk, {}).get("status") == "Running 🟢")
        bn    = " 🚫" if v.get("banned") else ""
        uname = USER_NAMES.get(k, "?")
        lines.append(
            f"• `{k}` @{uname}{bn}\n"
            f"  📦 `{len(bots)}/{v.get('slots',0)}` bots  🟢 `{run}` running  ⭐`{v.get('stars_spent',0)}`"
        )
        if len(lines) > 35: lines.append(f"_...and {total-35} more_"); break
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): await update.message.reply_text("❌ Admin only."); return
    try:    target = int(ctx.args[0])
    except: await update.message.reply_text("Usage: `/user <id>`", parse_mode="Markdown"); return
    u     = get_user(target); bots = get_user_bots(target)
    uname = USER_NAMES.get(str(target), "?")
    lines = [
        f"👤 *User: `{target}`*  @{uname}\n\n"
        f"📦 Slots: `{get_used_slots(target)}/{u['slots']}`\n"
        f"⭐ Stars: `{u.get('stars_spent',0)}`\n"
        f"🚫 Banned: `{u.get('banned',False)}`\n\n"
        f"🤖 *Bots ({len(bots)}):*\n"
    ]
    rows = []
    for bot_key in bots:
        e  = RUNNING_BOTS.get(bot_key, {})
        st = e.get("status", "Offline 🔴")
        up = fmt_up(time.time() - e["start_time"]) if e.get("start_time") else "—"
        lines.append(f"🔹 *{display_name(bot_key)}*  {st}  ⏱`{up}`")
        rows.append([InlineKeyboardButton(f"⚙️ {display_name(bot_key)}", callback_data=f"detail|{bot_key}")])
    if not bots: lines.append("_No bots_")
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(rows))


async def cmd_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    if not ctx.args:
        await update.message.reply_text(
            "Usage:\n`/msg Hello!` — broadcast\n`/msg 123456 Hi` — DM",
            parse_mode="Markdown"); return
    if ctx.args[0].isdigit():
        target = int(ctx.args[0]); message = " ".join(ctx.args[1:])
        if not message: await update.message.reply_text("Add message after ID."); return
        try:
            await ctx.bot.send_message(target, f"📢 *Admin:*\n\n{message}", parse_mode="Markdown")
            await update.message.reply_text(f"✅ Sent to `{target}`.", parse_mode="Markdown")
        except Exception as exc:
            await update.message.reply_text(f"❌ `{exc}`", parse_mode="Markdown")
        return
    message = " ".join(ctx.args); users = load_users(); ok = fail = 0
    sm = await update.message.reply_text(f"📡 Broadcasting to {len(users)}…")
    for uid_str in users:
        try:
            await ctx.bot.send_message(int(uid_str), f"📢 *Broadcast:*\n\n{message}",
                                       parse_mode="Markdown"); ok += 1
        except: fail += 1
        await asyncio.sleep(0.05)
    await sm.edit_text(f"📡 Done! ✅{ok}  ❌{fail}")


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    raw = " ".join(ctx.args).strip()
    if not raw: await update.message.reply_text("Usage: `/stop <bot name>`", parse_mode="Markdown"); return
    bot_key = _resolve_bot_key(uid, raw)
    if not bot_key:
        await update.message.reply_text(f"❌ Bot not found.", parse_mode="Markdown"); return
    _do_stop(bot_key)
    await update.message.reply_text(f"🛑 *{display_name(bot_key)}* stopped.", parse_mode="Markdown")


async def cmd_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; raw = " ".join(ctx.args).strip()
    if not raw: await update.message.reply_text("Usage: `/restart <bot name>`", parse_mode="Markdown"); return
    bot_key = _resolve_bot_key(uid, raw)
    if not bot_key:
        await update.message.reply_text("❌ Bot not found.", parse_mode="Markdown"); return
    bot_dir = HOSTED_DIR / bot_key
    if not (bot_dir / "main.py").exists():
        await update.message.reply_text("❌ Bot files missing — re-upload."); return
    _do_stop(bot_key); await asyncio.sleep(2)
    asyncio.create_task(run_bot(bot_key, bot_dir, uid))
    await update.message.reply_text(f"🔄 *{display_name(bot_key)}* restarted!", parse_mode="Markdown")


async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; raw = " ".join(ctx.args).strip()
    if not raw: await update.message.reply_text("Usage: `/delete <bot name>`", parse_mode="Markdown"); return
    bot_key = _resolve_bot_key(uid, raw)
    if not bot_key:
        await update.message.reply_text("❌ Bot not found.", parse_mode="Markdown"); return
    _kill_remove(bot_key, uid); push_to_github(f"Del uid={uid}: {display_name(bot_key)}")
    await update.message.reply_text(f"🗑 *{display_name(bot_key)}* deleted.", parse_mode="Markdown")


async def cmd_getlog(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; raw = " ".join(ctx.args).strip()
    if not raw: await update.message.reply_text("Usage: `/getlog <bot name>`", parse_mode="Markdown"); return
    bot_key = _resolve_bot_key(uid, raw)
    if not bot_key:
        await update.message.reply_text("❌ Not found or not yours.", parse_mode="Markdown"); return
    lp = HOSTED_DIR / bot_key / "bot_output.log"
    if not lp.exists(): await update.message.reply_text("❌ No log file."); return
    await update.message.reply_document(
        document=open(lp, "rb"), filename=f"{display_name(bot_key)}_log.txt",
        caption=f"📋 *{display_name(bot_key)}* log", parse_mode="Markdown")


async def cmd_pr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try: target = int(ctx.args[0]); slots = int(ctx.args[1])
    except: await update.message.reply_text("Usage: `/pr <id> <slots>`", parse_mode="Markdown"); return
    set_user_slots(target, slots); push_to_github(f"Slots: {target}→{slots}")
    await update.message.reply_text(f"✅ `{target}` → *{slots}* slots.", parse_mode="Markdown")


async def cmd_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try: target = int(ctx.args[0])
    except: await update.message.reply_text("Usage: `/ban <id>`", parse_mode="Markdown"); return
    ban_user(target); await update.message.reply_text(f"🚫 `{target}` banned.", parse_mode="Markdown")


async def cmd_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try: target = int(ctx.args[0])
    except: await update.message.reply_text("Usage: `/unban <id>`", parse_mode="Markdown"); return
    unban_user(target); await update.message.reply_text(f"✅ `{target}` unbanned.", parse_mode="Markdown")


async def cmd_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; u = get_user(uid)
    used = get_used_slots(uid); bots = get_user_bots(uid)
    run  = sum(1 for b in bots if RUNNING_BOTS.get(b, {}).get("status") == "Running 🟢")
    await update.message.reply_text(
        f"ℹ️ *Info*\n\n🆔 `{uid}`\n"
        f"📦 `{used}/{u['slots']}` slots\n"
        f"🤖 `{len(bots)}` bots  🟢 `{run}` running\n"
        f"⭐ `{u.get('stars_spent',0)}` stars",
        parse_mode="Markdown")


# Helper — resolve display name or bot_key to actual key
def _resolve_bot_key(uid: int, name_or_key: str) -> str | None:
    """Find bot_key from display name or full key. Admin sees all."""
    reg = load_registry()
    # Exact match first
    if name_or_key in reg:
        owner = str(reg[name_or_key].get("owner_id", ""))
        if is_admin(uid) or owner == str(uid): return name_or_key
    # Try display name match
    for bot_key, v in reg.items():
        if display_name(bot_key) == name_or_key:
            owner = str(v.get("owner_id", ""))
            if is_admin(uid) or owner == str(uid): return bot_key
    # Also check running bots (for bots without registry entry)
    for bot_key, e in RUNNING_BOTS.items():
        if display_name(bot_key) == name_or_key or bot_key == name_or_key:
            owner = str(e.get("owner_id", ""))
            if is_admin(uid) or owner == str(uid): return bot_key
    return None


# ══════════════════════════════════════════════════════════════════════
#  FILE UPLOAD HANDLER
# ══════════════════════════════════════════════════════════════════════

async def handle_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_banned(uid): return
    doc   = update.message.document; fname = doc.file_name or "upload"
    ext   = Path(fname).suffix.lower()
    if ext not in (".zip", ".py"):
        await update.message.reply_text("⚠️ Send `.zip` or `.py` file.", parse_mode="Markdown"); return
    if not check_rate(uid):
        await update.message.reply_text("⏳ Rate limit: 3 deploys/10 min."); return

    user     = get_user(uid)
    raw_name = Path(fname).stem
    bot_key  = make_bot_key(uid, raw_name)   # uid-prefixed → collision-safe

    reg      = load_registry()
    is_redeploy = bot_key in reg and str(reg[bot_key].get("owner_id", "")) == str(uid)

    if not is_redeploy and get_used_slots(uid) >= user["slots"]:
        await update.message.reply_text(
            f"❌ *Slot limit!* `{get_used_slots(uid)}/{user['slots']}`",
            parse_mode="Markdown", reply_markup=kb_plans()); return

    msg = await update.message.reply_text(
        f"⬇️ Downloading *{display_name(bot_key)}*…", parse_mode="Markdown")
    HOSTED_DIR.mkdir(parents=True, exist_ok=True)
    tg_file = await ctx.bot.get_file(doc.file_id)

    if ext == ".py":
        bot_dir = HOSTED_DIR / bot_key; bot_dir.mkdir(parents=True, exist_ok=True)
        await tg_file.download_to_drive(str(bot_dir / "main.py"))
    else:
        zip_path = HOSTED_DIR / f"{bot_key}.zip"
        await tg_file.download_to_drive(str(zip_path))
        bot_dir = HOSTED_DIR / bot_key
        if bot_dir.exists(): shutil.rmtree(bot_dir)
        bot_dir.mkdir(parents=True)
        try:
            with ZipFile(zip_path, "r") as zf:
                members = zf.namelist()
                tops    = {m.split("/")[0] for m in members if m.strip("/")}
                strip   = (list(tops)[0] + "/") if (
                    len(tops) == 1 and any("/" in m for m in members)) else ""
                for member in members:
                    rel = member[len(strip):] if strip else member
                    if not rel: continue
                    dest = bot_dir / rel
                    if member.endswith("/"): dest.mkdir(parents=True, exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(zf.read(member))
        except BadZipFile:
            zip_path.unlink(missing_ok=True); shutil.rmtree(bot_dir, ignore_errors=True)
            await msg.edit_text("❌ Invalid ZIP."); return
        finally: zip_path.unlink(missing_ok=True)

    main_py = bot_dir / "main.py"
    if not main_py.exists():
        found = list(bot_dir.rglob("main.py")) or list(bot_dir.rglob("*.py"))
        if found: shutil.copy(found[0], main_py)
        else:
            shutil.rmtree(bot_dir, ignore_errors=True)
            await msg.edit_text("❌ No `.py` file found.", parse_mode="Markdown"); return

    await _finalize(bot_key, bot_dir, uid, msg)


# ══════════════════════════════════════════════════════════════════════
#  TEXT HANDLER
# ══════════════════════════════════════════════════════════════════════

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = (update.message.text or "").strip()
    name = update.effective_user.username or update.effective_user.first_name or ""
    save_username(uid, name)
    if is_banned(uid): return

    if text == _RKB["bots"]:
        t, kb = mybots_card(uid)
        await update.message.reply_text(t, parse_mode="Markdown", reply_markup=kb); return
    if text == _RKB["dep"]:
        await update.message.reply_text(
            "📦 *Deploy*\n\n1️⃣ Send `.zip`\n2️⃣ Send `.py`\n3️⃣ Paste Python code\n4️⃣ /ide",
            parse_mode="Markdown"); return
    if text == _RKB["slots"]:
        await update.message.reply_text("💰 *Buy Slots*", parse_mode="Markdown",
                                        reply_markup=kb_plans()); return
    if text == _RKB["stats"]:
        u = get_user(uid); used = get_used_slots(uid); bots = get_user_bots(uid)
        run = sum(1 for b in bots if RUNNING_BOTS.get(b, {}).get("status") == "Running 🟢")
        await update.message.reply_text(
            f"📊 *Stats*\n\n📦 `{used}/{u['slots']}`\n"
            f"🤖 `{len(bots)}` bots  🟢 `{run}` running\n"
            f"⭐ `{u.get('stars_spent',0)}`", parse_mode="Markdown"); return
    if text == _RKB["help"]:
        await update.message.reply_text(
            "📦 *How to Deploy*\n\n"
            "1️⃣ ZIP → send `.zip`\n"
            "2️⃣ PY → send `.py`\n"
            "3️⃣ Code → paste Python directly\n"
            "4️⃣ IDE → /ide for Web IDE\n\n"
            "🔧 *Auto-Heal:* missing packages, conflict errors, port issues fixed automatically!\n\n"
            "⚠️ *Same-name safety:* two users can upload same-named bots — they never interfere.\n\n"
            "`/stop` `/restart` `/delete` `/getlog` `/info` `/rotatetoken`",
            parse_mode="Markdown"); return
    if text == _RKB["admin"] and is_admin(uid):
        total   = len(RUNNING_BOTS)
        running = sum(1 for e in RUNNING_BOTS.values() if e.get("status") == "Running 🟢")
        await update.message.reply_text(
            f"👑 *Admin Panel*\n\n"
            f"🤖 `{total}` bots  🟢 `{running}` running  👥 `{len(load_users())}` users\n\n"
            f"`/all` `/users` `/user <id>` `/msg`\n"
            f"`/pr <id> <slots>` `/ban` `/unban`",
            parse_mode="Markdown"); return

    if is_code(text):
        if not check_rate(uid):
            await update.message.reply_text("⏳ Rate limit: 3 deploys/10 min."); return
        user = get_user(uid)
        if get_used_slots(uid) >= user["slots"] and not is_admin(uid):
            await update.message.reply_text("❌ *Slot limit!*", parse_mode="Markdown",
                                            reply_markup=kb_plans()); return
        bot_key = smart_name(text, uid)
        msg = await update.message.reply_text(
            f"🔍 *Code detected!*\n📝 Name: *{display_name(bot_key)}*\n⚙️ Setting up…",
            parse_mode="Markdown")
        bot_dir = HOSTED_DIR / bot_key; bot_dir.mkdir(parents=True, exist_ok=True)
        (bot_dir / "main.py").write_text(text, encoding="utf-8")
        await _finalize(bot_key, bot_dir, uid, msg, code_text=text); return

    await update.message.reply_text(
        "💡 Send `.zip`/`.py` or paste Python code.\nType /start for help.")


# ══════════════════════════════════════════════════════════════════════
#  CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════════

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; uid = q.from_user.id; d = q.data or ""
    await q.answer()
    reg = load_registry()

    # IDE block/allow
    if d.startswith("ide_allow|"):
        if not is_admin(uid): await q.answer("❌ Admin only!", show_alert=True); return
        parts = d.split("|"); fp = parts[1]; ip = parts[2] if len(parts) > 2 else "?"
        web_ide._PENDING[fp] = "allow"
        await q.edit_message_text(f"✅ Session `{fp}` allowed.", parse_mode="Markdown"); return
    if d.startswith("ide_block|"):
        if not is_admin(uid): await q.answer("❌ Admin only!", show_alert=True); return
        parts = d.split("|"); fp = parts[1]; ip = parts[2] if len(parts) > 2 else "?"
        web_ide._block_ip(ip); web_ide._PENDING[fp] = "block"
        await q.edit_message_text(f"🚫 IP `{ip}` blocked.", parse_mode="Markdown"); return

    if d == "main_menu":
        await q.edit_message_text(home_text(uid), parse_mode="Markdown",
                                  reply_markup=kb_home(is_admin(uid)))

    elif d == "my_bots":
        text, kb = mybots_card(uid); await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

    elif d.startswith("detail|"):
        bot_key = d.split("|", 1)[1]; e = RUNNING_BOTS.get(bot_key, {})
        can = is_admin(uid) or str(reg.get(bot_key, {}).get("owner_id", "")) == str(uid)
        up  = fmt_up(time.time() - e["start_time"]) if e.get("start_time") else "—"
        err = (e.get("last_error") or "None").strip()[-300:]
        hl  = e.get("heal_tries", 0)
        await q.edit_message_text(
            f"⚙️ *{display_name(bot_key)}*\n\n"
            f"📌 {e.get('status','Offline 🔴')}\n"
            f"⏱ `{up}`  ·  {speed_lbl(e) if e else '—'}\n"
            f"🔄 `{e.get('restarts',0)}` restarts  🔧 `{hl}` heals\n\n"
            f"```\n{err}\n```",
            parse_mode="Markdown", reply_markup=kb_bot_card(bot_key, can))

    elif d.startswith("errinfo|"):
        bot_key = d.split("|", 1)[1]; e = RUNNING_BOTS.get(bot_key, {})
        parsed  = e.get("parsed_err")
        if not parsed or not parsed.get("has_error"):
            await q.answer("No error info yet.", show_alert=True); return
        await q.edit_message_text(fmt_error_tg(bot_key, parsed, 0), parse_mode="Markdown",
                                  reply_markup=kb_back(f"detail|{bot_key}"))

    elif d.startswith("stop|"):
        bot_key = d.split("|", 1)[1]
        if not (is_admin(uid) or str(reg.get(bot_key, {}).get("owner_id", "")) == str(uid)):
            await q.answer("❌ Not yours!", show_alert=True); return
        _do_stop(bot_key)
        await q.edit_message_text(f"🛑 *{display_name(bot_key)}* stopped.", parse_mode="Markdown",
                                  reply_markup=kb_back())

    elif d.startswith("restart|"):
        bot_key = d.split("|", 1)[1]
        if not (is_admin(uid) or str(reg.get(bot_key, {}).get("owner_id", "")) == str(uid)):
            await q.answer("❌ Not yours!", show_alert=True); return
        bot_dir = HOSTED_DIR / bot_key
        if not (bot_dir / "main.py").exists(): await q.answer("❌ Files missing!", show_alert=True); return
        _do_stop(bot_key); owner = reg.get(bot_key, {}).get("owner_id", uid)
        await asyncio.sleep(2); asyncio.create_task(run_bot(bot_key, bot_dir, owner))
        await q.edit_message_text(f"🔄 *{display_name(bot_key)}* restarted!", parse_mode="Markdown",
                                  reply_markup=kb_back())

    elif d.startswith("delete|"):
        bot_key = d.split("|", 1)[1]
        if not (is_admin(uid) or str(reg.get(bot_key, {}).get("owner_id", "")) == str(uid)):
            await q.answer("❌ Not yours!", show_alert=True); return
        _kill_remove(bot_key, uid); push_to_github(f"Del uid={uid}: {display_name(bot_key)}")
        await q.edit_message_text(f"🗑 *{display_name(bot_key)}* deleted.", parse_mode="Markdown",
                                  reply_markup=kb_back())

    elif d.startswith("logs|"):
        bot_key = d.split("|", 1)[1]; lp = HOSTED_DIR / bot_key / "bot_output.log"
        tail    = _tail(lp, 50) or "*(No logs yet.)*"
        if len(tail) > 3200: tail = "…\n" + tail[-3100:]
        await q.edit_message_text(
            f"📋 *{display_name(bot_key)}*\n\n```\n{tail}\n```",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh",  callback_data=f"logs|{bot_key}"),
                InlineKeyboardButton("🔍 Error",    callback_data=f"errinfo|{bot_key}"),
                InlineKeyboardButton("🔙 Back",     callback_data=f"detail|{bot_key}"),
            ]]))

    elif d.startswith("getlog|"):
        bot_key = d.split("|", 1)[1]; lp = HOSTED_DIR / bot_key / "bot_output.log"
        if not lp.exists(): await q.answer("No log file.", show_alert=True); return
        await q.message.reply_document(document=open(lp, "rb"),
            filename=f"{display_name(bot_key)}_log.txt",
            caption=f"📋 *{display_name(bot_key)}*", parse_mode="Markdown")

    elif d == "how_host":
        await q.edit_message_text(
            "📦 *Deploy Options*\n\n"
            "1️⃣ ZIP → send `.zip`\n2️⃣ PY → send `.py`\n"
            "3️⃣ Code → paste Python\n4️⃣ /ide → Web IDE\n\n"
            "🔧 Auto-heal: installs missing packages, fixes conflicts!\n"
            "✅ Same-name safety: each user's bots are isolated.",
            parse_mode="Markdown", reply_markup=kb_back())

    elif d == "my_stats":
        u = get_user(uid); used = get_used_slots(uid); bots = get_user_bots(uid)
        run = sum(1 for b in bots if RUNNING_BOTS.get(b, {}).get("status") == "Running 🟢")
        await q.edit_message_text(
            f"📊 *Stats*\n\n🆔`{uid}`\n📦`{used}/{u['slots']}`\n"
            f"🤖`{len(bots)}` / 🟢`{run}`\n⭐`{u.get('stars_spent',0)}`",
            parse_mode="Markdown", reply_markup=kb_back())

    elif d == "buy_plan":
        await q.edit_message_text("💰 *Buy Slots*\n\nPay with ⭐ Stars:",
                                  parse_mode="Markdown", reply_markup=kb_plans())

    elif d.startswith("buy_"):
        plan_key = d[4:]; plan = PLANS.get(plan_key)
        if not plan: await q.answer("Unknown plan.", show_alert=True); return
        await ctx.bot.send_invoice(chat_id=uid, title=plan["label"],
            description=f"{plan['slots']} extra slots — {plan['desc']}",
            payload=f"{plan_key}|{uid}", provider_token="", currency="XTR",
            prices=[LabeledPrice(plan["label"], plan["stars"])])

    elif d == "admin_panel":
        if not is_admin(uid): await q.answer("Admins only!", show_alert=True); return
        total   = len(RUNNING_BOTS)
        running = sum(1 for e in RUNNING_BOTS.values() if e.get("status") == "Running 🟢")
        await q.edit_message_text(
            f"👑 *Admin*\n\n🤖`{total}`  🟢`{running}`  👥`{len(load_users())}`\n\n"
            f"`/all` `/users` `/user <id>` `/msg`\n"
            f"`/pr <id> <slots>` `/ban` `/unban`",
            parse_mode="Markdown", reply_markup=kb_back())


# ══════════════════════════════════════════════════════════════════════
#  PAYMENTS
# ══════════════════════════════════════════════════════════════════════

async def pre_checkout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def payment_success(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    try:
        plan_key, uid_str = payment.invoice_payload.split("|")
        uid = int(uid_str); plan = PLANS[plan_key]
    except:
        await update.message.reply_text("⚠️ Payment received — contact admin."); return
    new_slots = add_user_slots(uid, plan["slots"]); add_user_stars(uid, plan["stars"])
    push_to_github(f"Stars: {plan_key} uid={uid}")
    await update.message.reply_text(
        f"🎉 *Payment OK!*\n\n📦 *{plan['label']}*\n"
        f"➕ +{plan['slots']} slots\n📊 Total: *{new_slots}*\n\n_Codian Studio 💎_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📦 Deploy Now", callback_data="how_host")
        ]]))


# ══════════════════════════════════════════════════════════════════════
#  AUTO-START PERSISTED BOTS
# ══════════════════════════════════════════════════════════════════════

async def autostart():
    reg = load_registry()
    if not HOSTED_DIR.exists(): return
    for item in HOSTED_DIR.iterdir():
        if not item.is_dir() or item.name.startswith(("_", ".")): continue
        if not (item / "main.py").exists(): continue
        req = item / "requirements.txt"
        if req.exists(): _install_reqs(req)
        owner = reg.get(item.name, {}).get("owner_id", 0)
        log.info("Auto-starting: %s (owner=%s)", item.name, owner)
        asyncio.create_task(run_bot(item.name, item, owner))


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════

async def main():
    global _APP
    log.info("════════════════════════════════════════════════════")
    log.info("   Master Hosting Bot v4.0  —  Codian Studio 💎    ")
    log.info("════════════════════════════════════════════════════")

    configure_git(); sync_from_github()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DELETED_DIR.mkdir(parents=True, exist_ok=True)
    load_usernames()

    web_ide.init_editor(RUNNING_BOTS, None, ADMIN_IDS)

    _APP = ApplicationBuilder().token(BOT_TOKEN).build()

    handlers = [
        CommandHandler("start",       cmd_start),
        CommandHandler("ide",         cmd_ide),
        CommandHandler("rotatetoken", cmd_rotatetoken),
        CommandHandler("mybots",      cmd_mybots),
        CommandHandler("all",         cmd_all),
        CommandHandler("users",       cmd_users),
        CommandHandler("user",        cmd_user),
        CommandHandler("msg",         cmd_msg),
        CommandHandler("stop",        cmd_stop),
        CommandHandler("restart",     cmd_restart),
        CommandHandler("delete",      cmd_delete),
        CommandHandler("pr",          cmd_pr),
        CommandHandler("ban",         cmd_ban),
        CommandHandler("unban",       cmd_unban),
        CommandHandler("getlog",      cmd_getlog),
        CommandHandler("info",        cmd_info),
        MessageHandler(filters.Document.ALL,            handle_upload),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
        CallbackQueryHandler(handle_callback),
        PreCheckoutQueryHandler(pre_checkout),
        MessageHandler(filters.SUCCESSFUL_PAYMENT,      payment_success),
    ]
    for h in handlers: _APP.add_handler(h)

    web_ide._APP_REF = _APP

    await start_web_server()
    asyncio.create_task(keep_alive_loop())
    await autostart()

    log.info("Clearing webhook…")
    await _APP.bot.delete_webhook(drop_pending_updates=True)
    await _APP.initialize()
    await _APP.start()
    await _APP.updater.start_polling(
        drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

    try:
        await _APP.bot.set_my_commands([
            BotCommand("start",       "🏠 Home"),
            BotCommand("ide",         "💎 Web IDE link"),
            BotCommand("mybots",      "🤖 My bots"),
            BotCommand("info",        "📊 My info"),
            BotCommand("stop",        "🛑 Stop bot"),
            BotCommand("restart",     "🔄 Restart bot"),
            BotCommand("delete",      "🗑 Delete bot"),
            BotCommand("getlog",      "📋 Download log"),
            BotCommand("rotatetoken", "🔐 New IDE token"),
            BotCommand("all",         "👑 All bots (admin)"),
            BotCommand("users",       "👑 All users (admin)"),
            BotCommand("user",        "👑 View user (admin)"),
            BotCommand("msg",         "👑 Broadcast (admin)"),
            BotCommand("pr",          "👑 Set slots (admin)"),
        ])
    except Exception: pass

    log.info("✅ v4.0 live! IDE: %s/editor", RENDER_URL or "localhost:"+str(PORT))
    await asyncio.Event().wait()


if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: log.info("Stopped.")
