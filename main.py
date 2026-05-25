"""
╔══════════════════════════════════════════════════════════════════════╗
║   MASTER HOSTING BOT  v3.3  —  Complete Edition                     ║
║   python-telegram-bot 20.x  ·  Python 3.11  ·  Render.com           ║
║   Codian Studio 💎                                                   ║
╠══════════════════════════════════════════════════════════════════════╣
║  v3.3 New:                                                           ║
║  • Smart error parser — exact line, type, fix hint in terminal       ║
║  • /ide sends full token (browser needs it)                          ║
║  • /users shows username + bot count                                 ║
║  • Per-user IDE tokens (each user sees only their bots)              ║
║  • All v3.2 features retained                                        ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os, re, ast, sys, json, time, shutil, logging, asyncio, subprocess, traceback
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
#  ENV CONFIG
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

PLANS: dict[str, dict] = {
    "starter": {"stars": 15,  "slots": 3,  "label": "Starter ⭐",  "desc": "+3 slots"},
    "pro":     {"stars": 50,  "slots": 10, "label": "Pro 💎",      "desc": "+10 slots"},
    "elite":   {"stars": 100, "slots": 25, "label": "Elite 👑",    "desc": "+25 slots"},
}

STDLIB: set[str] = {
    "os","sys","re","io","json","time","math","copy","enum","abc","ast",
    "csv","gzip","hmac","html","http","uuid","glob","shutil","queue","array",
    "struct","socket","signal","string","random","hashlib","logging","pathlib",
    "inspect","functools","operator","itertools","contextlib","threading",
    "subprocess","traceback","datetime","calendar","textwrap","argparse",
    "platform","tempfile","unittest","dataclasses","collections","ftplib",
    "smtplib","urllib","base64","binascii","codecs","email","getpass","locale",
    "weakref","warnings","typing","types","builtins","gc","heapq","bisect",
    "pprint","statistics","decimal","fractions","zipfile","tarfile",
    "configparser","pickle","shelve","sqlite3","multiprocessing","concurrent",
    "asyncio","ssl","select","selectors","mimetypes","xml","timeit","dis",
    "cmd","wave","colorsys","cProfile","numbers","difflib","readline",
}

IMPORT_MAP: dict[str, str] = {
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

_RKB_MY_BOTS = "🤖 My Bots"
_RKB_DEPLOY  = "📦 Deploy Bot"
_RKB_SLOTS   = "💰 Buy Slots"
_RKB_STATS   = "📊 My Stats"
_RKB_HELP    = "ℹ️ Help"
_RKB_ADMIN   = "👑 Admin"

REPO_URL    = f"https://{GITHUB_PAT}@github.com/{GITHUB_USERNAME}/{REPO_NAME}.git"
HOSTED_DIR  = Path("hosted_bots")
DATA_DIR    = HOSTED_DIR / "_data"
DELETED_DIR = HOSTED_DIR / "_deleted"

RUNNING_BOTS: dict[str, dict]        = {}
DEPLOY_TIMES: dict[int, list[float]] = {}
# Store telegram usernames: uid_str -> username
USER_NAMES: dict[str, str]           = {}
_APP = None

# ══════════════════════════════════════════════════════════════════════
#  SMART ERROR PARSER
# ══════════════════════════════════════════════════════════════════════

# Known error patterns with human-readable fix hints
_ERROR_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"ModuleNotFoundError: No module named '([^']+)'"),
     "📦 Missing package. Add '{0}' to requirements.txt or bot will auto-install on next deploy."),
    (re.compile(r"ImportError: cannot import name '([^']+)' from '([^']+)'"),
     "⚠️ Wrong import name '{0}' from '{1}'. Check the library docs."),
    (re.compile(r"SyntaxError: (.+)"),
     "✏️ Syntax error: {0}. Check missing brackets, colons, or quotes."),
    (re.compile(r"IndentationError: (.+)"),
     "↔️ Indentation error: {0}. Use consistent spaces (4 spaces recommended)."),
    (re.compile(r"NameError: name '([^']+)' is not defined"),
     "❓ Variable/function '{0}' is not defined. Check spelling or define it first."),
    (re.compile(r"TypeError: (.+)"),
     "🔢 Type error: {0}. Check argument types."),
    (re.compile(r"AttributeError: '([^']+)' object has no attribute '([^']+)'"),
     "🔍 '{0}' has no attribute '{1}'. Check object type or typo."),
    (re.compile(r"KeyError: '?([^'\n]+)'?"),
     "🗝️ Key '{0}' not found in dict. Check key name."),
    (re.compile(r"ValueError: (.+)"),
     "⚡ Value error: {0}. Check the data being passed."),
    (re.compile(r"ConnectionRefusedError"),
     "🔌 Connection refused. Check host, port, or if the service is running."),
    (re.compile(r"Unauthorized"),
     "🔑 Unauthorized. Check your API token / bot token."),
    (re.compile(r"Conflict: terminated by other getUpdates"),
     "⚡ Conflict: Another bot instance is running. Delete webhook and restart."),
    (re.compile(r"RuntimeError: (.+)"),
     "💥 RuntimeError: {0}. Check the traceback above."),
    (re.compile(r"PermissionError: (.+)"),
     "🔒 Permission denied: {0}. Check file/folder permissions."),
    (re.compile(r"FileNotFoundError: (.+)"),
     "📁 File not found: {0}. Check the file path."),
    (re.compile(r"JSONDecodeError: (.+)"),
     "📋 JSON decode error: {0}. Check your JSON format."),
    (re.compile(r"TimeoutError"),
     "⏰ Timeout. Network or service too slow. Retry or increase timeout."),
    (re.compile(r"RecursionError"),
     "♾️ Recursion error. Infinite loop detected. Add a base case."),
    (re.compile(r"MemoryError"),
     "💾 Out of memory. Reduce data size or optimize."),
    (re.compile(r"ZeroDivisionError"),
     "➗ Division by zero. Check your divisor."),
    (re.compile(r"StopIteration"),
     "🔄 StopIteration. Iterator is empty. Check loop logic."),
    (re.compile(r"AssertionError: (.+)"),
     "⛔ Assertion failed: {0}. Check your assert conditions."),
]


def parse_error(log_text: str) -> dict:
    """
    Parse bot output log and extract:
    - error_type: e.g. 'ModuleNotFoundError'
    - error_msg: full error message
    - file_name: which file caused the error
    - line_no: int or None
    - code_snippet: the line of code
    - hint: human-readable fix suggestion
    - traceback_lines: list of relevant traceback lines
    """
    result = {
        "error_type":      "Unknown Error",
        "error_msg":       "",
        "file_name":       None,
        "line_no":         None,
        "code_snippet":    None,
        "hint":            "Check the terminal output for details.",
        "traceback_lines": [],
        "has_error":       False,
    }

    if not log_text:
        return result

    lines = log_text.splitlines()

    # Find the last traceback block
    tb_start = -1
    for i in range(len(lines) - 1, -1, -1):
        if "Traceback (most recent call last)" in lines[i]:
            tb_start = i
            break

    if tb_start == -1:
        # No traceback — check for simple error lines
        for line in reversed(lines[-30:]):
            for pat, hint_tpl in _ERROR_HINTS:
                m = pat.search(line)
                if m:
                    result["has_error"]   = True
                    result["error_msg"]   = line.strip()
                    result["error_type"]  = line.split(":")[0].strip()
                    result["hint"]        = hint_tpl.format(*m.groups()) if m.groups() else hint_tpl
                    return result
        return result

    result["has_error"]       = True
    tb_lines                  = lines[tb_start:]
    result["traceback_lines"] = tb_lines[:40]

    # Extract File + Line number from traceback
    for ln in reversed(tb_lines):
        m = re.search(r'File "([^"]+)", line (\d+)', ln)
        if m:
            fpath              = m.group(1)
            result["line_no"]  = int(m.group(2))
            # Clean path: show relative to bot dir
            try:
                fpath = str(Path(fpath).relative_to(Path.cwd()))
            except Exception:
                pass
            result["file_name"] = fpath
            break

    # Extract code snippet (line after File/line info)
    for i, ln in enumerate(tb_lines):
        if re.search(r'File "([^"]+)", line (\d+)', ln):
            if i + 1 < len(tb_lines):
                snippet = tb_lines[i + 1].strip()
                if snippet and not snippet.startswith("File "):
                    result["code_snippet"] = snippet

    # Extract error type and message from last non-empty line
    for ln in reversed(tb_lines):
        ln = ln.strip()
        if ln and not ln.startswith("File ") and not ln.startswith("Traceback"):
            result["error_msg"] = ln
            parts = ln.split(":", 1)
            if parts:
                result["error_type"] = parts[0].strip()
            break

    # Match hint
    for pat, hint_tpl in _ERROR_HINTS:
        m = pat.search("\n".join(tb_lines[-10:]))
        if m:
            result["hint"] = hint_tpl.format(*m.groups()) if m.groups() else hint_tpl
            break

    return result


def format_error_report(name: str, parsed: dict, runtime: float) -> str:
    """Format a rich error report for Telegram message."""
    if not parsed["has_error"]:
        return (f"⚠️ *{name}* crashed after `{runtime:.1f}s` but no error found in logs.\n"
                f"Check /getlog {name} for details.")

    loc = ""
    if parsed["file_name"] and parsed["line_no"]:
        loc = f"\n📍 *Location* : `{parsed['file_name']}` → Line `{parsed['line_no']}`"

    snippet = ""
    if parsed["code_snippet"]:
        snippet = f"\n\n💻 *Code:*\n```python\n{parsed['code_snippet']}\n```"

    return (
        f"🚨 *{name}* — Auto-stopped after `{MAX_FAST_CRASHES}` crashes\n"
        f"⏱ Runtime: `{runtime:.1f}s`\n\n"
        f"❌ *Error Type* : `{parsed['error_type']}`\n"
        f"📋 *Message*   : `{parsed['error_msg'][:200]}`"
        f"{loc}"
        f"{snippet}\n\n"
        f"💡 *Fix Hint:*\n{parsed['hint']}\n\n"
        f"🛠 Edit in Web IDE: /ide\n"
        f"📥 Download log: /getlog `{name}`"
    )


def format_error_for_terminal(parsed: dict, runtime: float) -> list[str]:
    """Format error as terminal lines for WebSocket stream."""
    lines = []
    lines.append("─" * 60)
    lines.append(f"💥 BOT CRASHED  (ran for {runtime:.1f}s)")
    lines.append("─" * 60)
    if parsed["has_error"]:
        lines.append(f"❌ Error Type : {parsed['error_type']}")
        lines.append(f"📋 Message    : {parsed['error_msg'][:300]}")
        if parsed["file_name"] and parsed["line_no"]:
            lines.append(f"📍 Location   : {parsed['file_name']}  →  Line {parsed['line_no']}")
        if parsed["code_snippet"]:
            lines.append(f"💻 Code       : {parsed['code_snippet']}")
        lines.append("")
        lines.append(f"💡 Fix Hint   : {parsed['hint']}")
    lines.append("─" * 60)
    return lines


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

def set_user_slots(uid, s):     _upd_user(uid, {"slots": s})
def add_user_slots(uid, extra):
    u = get_user(uid); n = u["slots"] + extra; set_user_slots(uid, n); return n
def add_user_stars(uid, stars):
    u = get_user(uid); _upd_user(uid, {"stars_spent": u.get("stars_spent", 0) + stars})
def is_banned(uid): return bool(get_user(uid).get("banned", False))
def ban_user(uid):   _upd_user(uid, {"banned": True})
def unban_user(uid): _upd_user(uid, {"banned": False})

def save_username(uid: int, username: str):
    if username:
        USER_NAMES[str(uid)] = username
        _jsave(DATA_DIR / "usernames.json", USER_NAMES)

def load_usernames():
    global USER_NAMES
    USER_NAMES = _jload(DATA_DIR / "usernames.json", {})

def register_bot(name, uid):
    reg = load_registry()
    reg[name] = {"owner_id": uid, "registered_at": time.time()}
    save_registry(reg)

def unregister_bot(name):
    reg = load_registry(); reg.pop(name, None); save_registry(reg)

def get_used_slots(uid):
    return sum(1 for v in load_registry().values() if str(v.get("owner_id")) == str(uid))

def get_user_bots(uid):
    return [k for k, v in load_registry().items() if str(v.get("owner_id")) == str(uid)]

def soft_delete_bot(name: str, deleted_by: int):
    src = HOSTED_DIR / name
    reg = load_registry()
    owner_uid = str(reg.get(name, {}).get("owner_id", deleted_by))
    if src.exists():
        ts   = int(time.time())
        dest = DELETED_DIR / owner_uid / f"{name}_{ts}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        _jsave(dest / "_DELETED.json", {
            "bot_name": name, "owner_uid": owner_uid,
            "deleted_by": deleted_by, "deleted_at": ts,
            "marker": "<deleted>",
        })
    unregister_bot(name)

# ══════════════════════════════════════════════════════════════════════
#  GIT SYNC
# ══════════════════════════════════════════════════════════════════════
def _git(cmd): return os.system(cmd + " > /dev/null 2>&1")

def configure_git():
    _git('git config --global user.email "masterbot@render.com"')
    _git('git config --global user.name  "MasterHostingBot"')

def sync_from_github():
    if not GITHUB_PAT:
        log.warning("GITHUB_PAT not set."); HOSTED_DIR.mkdir(exist_ok=True); return
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
        f"<tr><td><b>{n}</b></td><td>{d.get('status','?')}</td>"
        f"<td>{fmt_up(time.time()-d.get('start_time',time.time()))}</td>"
        f"<td>{d.get('restarts',0)}</td></tr>"
        for n, d in RUNNING_BOTS.items()
    )
    return web.Response(content_type="text/html", text=(
        "<html><head><title>Master Hosting Bot</title>"
        "<style>body{font-family:monospace;padding:20px;background:#0e1525;color:#e1e4e8}"
        "h2{color:#0096ff}.btn{display:inline-block;background:#0096ff;color:#fff;"
        "padding:8px 20px;border-radius:7px;text-decoration:none;margin:12px 0;font-weight:700}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #1c2b3a;padding:9px}"
        "tr:nth-child(even){background:#141d2b}</style></head><body>"
        f"<h2>🤖 Master Hosting Bot — Live</h2>"
        f"<a class='btn' href='{ide}'>💎 Open Web IDE</a><br><br>"
        f"<table><tr><th>Bot</th><th>Status</th><th>Uptime</th><th>Restarts</th></tr>"
        f"{rows}</table><p style='color:#556070;margin-top:16px'><i>Codian Studio 💎</i></p></body></html>"
    ))

async def start_web_server():
    wa = web.Application(client_max_size=128 * 1024 * 1024)
    wa.router.add_get("/",       _web_root)
    wa.router.add_get("/health", lambda r: web.Response(text="OK"))
    web_ide.register_routes(wa)
    runner = web.AppRunner(wa); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Web + IDE on port %d", PORT)

# ══════════════════════════════════════════════════════════════════════
#  ANTI-SLEEP
# ══════════════════════════════════════════════════════════════════════
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

def speed_lbl(e):
    r = e.get("restarts", 0)
    return ("🚀 Excellent" if r == 0 else "⚡ Good" if r < 3
            else "🐢 Unstable" if r < 10 else "💀 Critical")

def _tail(p, n=100):
    try: return "\n".join(p.read_text(errors="replace").splitlines()[-n:])
    except: return ""

def check_rate(uid):
    if is_admin(uid): return True
    now = time.time(); times = [t for t in DEPLOY_TIMES.get(uid, []) if now - t < 600]
    if len(times) >= 3: return False
    times.append(now); DEPLOY_TIMES[uid] = times; return True

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
    elif any(k in lower for k in ("discord", "nextcord")): pf = "dsbot"
    elif any(k in lower for k in ("flask", "fastapi", "django", "tornado")): pf = "webapp"
    elif any(k in lower for k in ("scrape", "bs4", "selenium", "playwright")): pf = "scraper"
    elif any(k in lower for k in ("schedule", "cron", "apscheduler")): pf = "scheduler"
    elif any(k in lower for k in ("openai", "anthropic", "gpt")): pf = "aibot"
    else: pf = "script"
    m = re.search(r"class\s+([A-Za-z]\w+)", code)
    if m: return f"{m.group(1).lower()[:15]}_{uid % 9999}"
    m = re.search(r"#\s*([A-Za-z][A-Za-z0-9 _-]+)", code)
    if m: return f"{m.group(1).strip().lower()[:18].replace(' ', '_')}_{uid % 9999}"
    return f"{pf}_{uid}_{int(time.time()) % 99999}"

def _do_stop(name):
    e = RUNNING_BOTS.get(name, {})
    if e:
        e["active"] = False
        p = e.get("process")
        if p and p.poll() is None:
            p.terminate()
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill()
        e["status"] = "Stopped 🛑"

def _kill_remove(name, deleted_by=0):
    _do_stop(name); RUNNING_BOTS.pop(name, None)
    soft_delete_bot(name, deleted_by)
    shutil.rmtree(HOSTED_DIR / name, ignore_errors=True)

# ══════════════════════════════════════════════════════════════════════
#  CHILD-BOT RUNNER  (with smart error parsing)
# ══════════════════════════════════════════════════════════════════════
async def run_bot(name: str, bot_dir: Path, owner_id: int):
    log_path = bot_dir / "bot_output.log"

    RUNNING_BOTS[name] = {
        "active":     True,
        "start_time": time.time(),
        "status":     "Starting ⏳",
        "process":    None,
        "restarts":   0,
        "last_error": None,
        "parsed_err": None,
        "owner_id":   owner_id,
    }
    fast_crashes = 0

    while RUNNING_BOTS.get(name, {}).get("active"):
        # Check for Web IDE restart flag
        flag = bot_dir / ".restart_flag"
        if flag.exists():
            try: flag.unlink()
            except: pass

        t0 = time.time()
        RUNNING_BOTS[name]["status"] = "Running 🟢"
        lf = None

        try:
            lf   = open(log_path, "a", encoding="utf-8", errors="replace")
            # Write startup separator to log (visible in IDE terminal)
            lf.write(f"\n{'─'*60}\n")
            lf.write(f"[START] {name}  ·  {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            lf.write(f"{'─'*60}\n")
            lf.flush()
            proc = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd=str(bot_dir), stdout=lf, stderr=lf,
            )
        except Exception as exc:
            if lf:
                try: lf.close()
                except: pass
            RUNNING_BOTS[name].update(
                status="Launch Error ❌", active=False, last_error=str(exc))
            await _notify(owner_id, f"❌ *{name}* failed to launch:\n`{exc}`")
            break

        RUNNING_BOTS[name]["process"] = proc

        # Non-blocking wait; also check for IDE restart flag
        while proc.poll() is None:
            await asyncio.sleep(1.5)
            if (bot_dir / ".restart_flag").exists():
                log.info("Restart flag → %s", name)
                try: (bot_dir / ".restart_flag").unlink()
                except: pass
                proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill()
                break

        try: lf.close()
        except: pass

        runtime = time.time() - t0
        tail    = _tail(log_path, 150)
        parsed  = parse_error(tail)

        RUNNING_BOTS[name]["last_error"] = tail
        RUNNING_BOTS[name]["parsed_err"] = parsed
        RUNNING_BOTS[name]["restarts"]  += 1

        # Write parsed error to log so IDE terminal shows it
        if parsed["has_error"]:
            try:
                with open(log_path, "a", encoding="utf-8", errors="replace") as lf2:
                    lf2.write("\n")
                    for line in format_error_for_terminal(parsed, runtime):
                        lf2.write(line + "\n")
            except: pass

        if not RUNNING_BOTS.get(name, {}).get("active"):
            RUNNING_BOTS[name]["status"] = "Stopped 🛑"; break

        log.warning("%s exited (runtime=%.1fs)", name, runtime)

        if runtime < FAST_CRASH_SEC:
            fast_crashes += 1
            if fast_crashes >= MAX_FAST_CRASHES:
                RUNNING_BOTS[name].update(
                    active=False, status="Error ❌ (Auto-stopped)")
                report = format_error_report(name, parsed, runtime)
                await _notify(owner_id, report)
                break
        else:
            fast_crashes = 0

        RUNNING_BOTS[name]["status"] = "Restarting ⏳"
        log.info("Restarting %s in 5s…", name)
        await asyncio.sleep(5)


async def _notify(owner_id, text):
    if not _APP or not owner_id: return
    try:
        await _APP.bot.send_message(owner_id, text, parse_mode="Markdown")
    except Exception as e:
        log.warning("Notify failed: %s", e)

# ══════════════════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════════════════
def reply_kb(admin=False):
    rows = [
        [KeyboardButton(_RKB_MY_BOTS), KeyboardButton(_RKB_DEPLOY)],
        [KeyboardButton(_RKB_SLOTS),   KeyboardButton(_RKB_STATS)],
        [KeyboardButton(_RKB_HELP)],
    ]
    if admin: rows.append([KeyboardButton(_RKB_ADMIN)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)

def kb_home(admin=False):
    rows = [
        [InlineKeyboardButton("🤖 My Bots",  callback_data="my_bots"),
         InlineKeyboardButton("📦 Deploy",   callback_data="how_host")],
        [InlineKeyboardButton("💰 Buy Slots", callback_data="buy_plan"),
         InlineKeyboardButton("📊 My Stats", callback_data="my_stats")],
    ]
    if admin: rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(rows)

def kb_plans():
    rows = [[InlineKeyboardButton(
        f"{p['label']} — {p['stars']} ⭐  ({p['desc']})",
        callback_data=f"buy_{k}")] for k, p in PLANS.items()]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)

def kb_bot_card(name, can):
    rows = []
    if can:
        rows.append([
            InlineKeyboardButton("🛑 Stop",    callback_data=f"stop|{name}"),
            InlineKeyboardButton("🔄 Restart", callback_data=f"restart|{name}"),
            InlineKeyboardButton("🗑 Delete",  callback_data=f"delete|{name}"),
        ])
        rows.append([
            InlineKeyboardButton("📋 Logs",       callback_data=f"logs|{name}"),
            InlineKeyboardButton("🔍 Error Info", callback_data=f"errinfo|{name}"),
            InlineKeyboardButton("⬇ Log File",   callback_data=f"getlog|{name}"),
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
    u    = get_user(uid); used = get_used_slots(uid)
    plan = "Admin 👑" if is_admin(uid) else "Free ✨"
    return (f"🤖 *Master Hosting Bot*\n\n"
            f"👤 Plan  : {plan}\n"
            f"📦 Slots : `{used}/{u['slots']}` used\n\n"
            f"Send `.zip`, `.py` or paste Python code!\n"
            f"💎 Web IDE: /ide\n\n_Codian Studio 💎_")

def mybots_card(uid):
    bots = get_user_bots(uid)
    if not bots:
        return ("📭 *No bots hosted yet.*\n\n"
                "Send a `.zip` / `.py` file or paste Python code!"), kb_back()
    lines = ["🤖 *Your Hosted Bots*\n"]; rows = []
    for name in bots:
        e   = RUNNING_BOTS.get(name, {})
        st  = e.get("status", "Offline 🔴")
        up  = fmt_up(time.time() - e["start_time"]) if e.get("start_time") else "—"
        rs  = e.get("restarts", 0)
        lines.append(
            f"🔹 *{name}*\n"
            f"   {st}  ·  ⏱ `{up}`\n"
            f"   {speed_lbl(e) if e else '—'}  ·  🔄 `{rs}` restarts\n"
        )
        rows.append([InlineKeyboardButton(f"⚙️  {name}", callback_data=f"detail|{name}")])
    rows.append([InlineKeyboardButton("🏠 Home", callback_data="main_menu")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)

# ══════════════════════════════════════════════════════════════════════
#  SHARED DEPLOY FINALIZE
# ══════════════════════════════════════════════════════════════════════
async def _finalize(bot_name, bot_dir, uid, msg, code_text=""):
    user = get_user(uid)
    code = code_text or (bot_dir / "main.py").read_text(errors="replace")
    req  = bot_dir / "requirements.txt"
    if req.exists():
        await msg.edit_text(
            f"⚙️ Installing requirements for *{bot_name}*…", parse_mode="Markdown")
        _install_reqs(req)
    else:
        await msg.edit_text(
            f"🔍 Auto-detecting packages for *{bot_name}*…", parse_mode="Markdown")
        installed = smart_install(code)
        if installed:
            log.info("Auto-installed for %s: %s", bot_name, installed)

    if bot_name in RUNNING_BOTS:
        _do_stop(bot_name); await asyncio.sleep(2)

    register_bot(bot_name, uid)
    push_to_github(f"Deploy uid={uid}: {bot_name}")
    asyncio.create_task(run_bot(bot_name, bot_dir, uid))

    used = get_used_slots(uid)
    await msg.edit_text(
        f"✅ *{bot_name}* deployed!\n\n"
        f"📦 Slots: `{used}/{user['slots']}`\n"
        f"💎 Edit live in Web IDE: /ide\n\n"
        f"_If bot crashes, you'll get a detailed error report here._",
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
    save_username(uid, name)
    get_user(uid)
    if is_banned(uid): await update.message.reply_text("🚫 Banned."); return
    await update.message.reply_text(
        home_text(uid), parse_mode="Markdown", reply_markup=reply_kb(is_admin(uid)))
    await update.message.reply_text("👇 Use the menu:", reply_markup=kb_home(is_admin(uid)))


async def cmd_ide(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Send full IDE link with token — per user."""
    uid = update.effective_user.id
    if is_banned(uid): return
    # Every user gets their own token
    token = web_ide.get_user_token(uid)
    url   = f"{RENDER_URL}/editor?token={token}" if RENDER_URL else f"http://localhost:{PORT}/editor?token={token}"
    await update.message.reply_text(
        f"💎 *Your Web IDE Link*\n\n"
        f"🔗 `{url}`\n\n"
        f"🔑 Token: `{token}`\n\n"
        f"⚠️ *Keep this private* — anyone with this link can edit your bots!\n"
        f"Use /rotatetoken to generate a new token.",
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
        f"🔐 *Token Rotated!*\n\n"
        f"Old token is now invalid.\n"
        f"New token: `{token}`\n\n"
        f"[Open IDE]({url})",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Open IDE", url=url)]]),
    )


async def cmd_sessions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): await update.message.reply_text("❌ Admin only."); return
    sessions = web_ide._load_json(web_ide._SESSIONS_FILE, {})
    blocked  = web_ide._load_json(web_ide._BLOCKED_IPS_FILE, [])
    if not sessions: await update.message.reply_text("📭 No IDE sessions."); return
    lines = ["🔐 *IDE Sessions*\n"]
    for fp, s in list(sessions.items())[-15:]:
        ip  = s.get("ip", "?"); ua = s.get("ua", "?")[:50]
        ago = int(time.time() - s.get("last_seen", 0))
        bl  = " 🚫" if ip in blocked else ""
        lines.append(f"• `{fp}` — `{ip}`{bl}\n  _{ua}_\n  _{ago}s ago_\n")
    if blocked:
        lines.append(f"\n🚫 *Blocked IPs:* {', '.join(f'`{i}`' for i in blocked)}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): await update.message.reply_text("❌ Admin only."); return
    try:    target = int(ctx.args[0])
    except: await update.message.reply_text("Usage: `/user <user_id>`", parse_mode="Markdown"); return
    u    = get_user(target); bots = get_user_bots(target)
    uname = USER_NAMES.get(str(target), "unknown")
    lines = [
        f"👤 *User: `{target}`*\n"
        f"   @{uname}\n\n"
        f"📦 Slots: `{get_used_slots(target)}/{u['slots']}`\n"
        f"⭐ Stars: `{u.get('stars_spent', 0)}`\n"
        f"🚫 Banned: `{u.get('banned', False)}`\n\n"
        f"🤖 *Bots ({len(bots)}):*\n"
    ]
    rows = []
    for name in bots:
        e  = RUNNING_BOTS.get(name, {})
        st = e.get("status", "Offline 🔴")
        up = fmt_up(time.time() - e["start_time"]) if e.get("start_time") else "—"
        lines.append(f"🔹 *{name}*  {st}  ⏱`{up}`")
        rows.append([InlineKeyboardButton(f"⚙️ {name}", callback_data=f"detail|{name}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    if not bots: lines.append("_No bots_")
    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows) if rows else None)


async def cmd_mybots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_banned(uid): return
    text, kb = mybots_card(uid)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): await update.message.reply_text("❌ Admin only."); return
    if not RUNNING_BOTS: await update.message.reply_text("📭 No bots."); return
    reg   = load_registry(); lines = ["👑 *All Bots (Admin)*\n"]
    for name, e in RUNNING_BOTS.items():
        up  = fmt_up(time.time() - e.get("start_time", time.time()))
        own = reg.get(name, {}).get("owner_id", "?")
        uname = USER_NAMES.get(str(own), "?")
        rs  = e.get("restarts", 0)
        lines.append(
            f"🔹 *{name}*\n"
            f"   @{uname} (`{own}`)  {e.get('status', '?')}\n"
            f"   ⏱`{up}`  🔄`{rs}`  {speed_lbl(e)}\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): await update.message.reply_text("❌ Admin only."); return
    users = load_users(); reg = load_registry(); total = len(users)
    lines = [f"👥 *Total Users: {total}*\n"]
    for k, v in list(users.items()):
        bots   = sum(1 for rv in reg.values() if str(rv.get("owner_id")) == k)
        banned = " 🚫" if v.get("banned") else ""
        uname  = USER_NAMES.get(k, "?")
        lines.append(
            f"• `{k}` @{uname}{banned}\n"
            f"  slots:`{v.get('slots', 0)}`  bots:`{bots}`  ⭐:`{v.get('stars_spent', 0)}`"
        )
        if len(lines) > 30: lines.append(f"_...and {total - 30} more_"); break
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid): return
    if not ctx.args:
        await update.message.reply_text(
            "Usage:\n`/msg Hello!`  — broadcast\n`/msg 123456 Hi`  — DM",
            parse_mode="Markdown"); return
    if ctx.args[0].isdigit():
        target  = int(ctx.args[0]); message = " ".join(ctx.args[1:])
        if not message: await update.message.reply_text("Include message after ID."); return
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
            await ctx.bot.send_message(
                int(uid_str), f"📢 *Broadcast:*\n\n{message}", parse_mode="Markdown"); ok += 1
        except: fail += 1
        await asyncio.sleep(0.05)
    await sm.edit_text(f"📡 Done! ✅{ok}  ❌{fail}")


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id; name = " ".join(ctx.args).strip()
    if not name: await update.message.reply_text("Usage: `/stop <name>`", parse_mode="Markdown"); return
    reg  = load_registry(); owner = str(reg.get(name, {}).get("owner_id", ""))
    if not (is_admin(uid) or owner == str(uid)):
        await update.message.reply_text(f"❌ *{name}* not found or not yours.", parse_mode="Markdown"); return
    _do_stop(name)
    await update.message.reply_text(f"🛑 *{name}* stopped.", parse_mode="Markdown")


async def cmd_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id; name = " ".join(ctx.args).strip()
    if not name: await update.message.reply_text("Usage: `/restart <name>`", parse_mode="Markdown"); return
    reg  = load_registry(); owner = str(reg.get(name, {}).get("owner_id", ""))
    if not (is_admin(uid) or owner == str(uid)):
        await update.message.reply_text("❌ Not found or not yours.", parse_mode="Markdown"); return
    bot_dir = HOSTED_DIR / name
    if not (bot_dir / "main.py").exists():
        await update.message.reply_text("❌ Files missing — re-upload."); return
    _do_stop(name); await asyncio.sleep(2)
    asyncio.create_task(run_bot(name, bot_dir, uid))
    await update.message.reply_text(f"🔄 *{name}* restarted!", parse_mode="Markdown")


async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id; name = " ".join(ctx.args).strip()
    if not name: await update.message.reply_text("Usage: `/delete <name>`", parse_mode="Markdown"); return
    reg  = load_registry(); owner = str(reg.get(name, {}).get("owner_id", ""))
    if not (is_admin(uid) or owner == str(uid)):
        await update.message.reply_text("❌ Not found or not yours.", parse_mode="Markdown"); return
    _kill_remove(name, uid)
    push_to_github(f"Del uid={uid}: {name}")
    # No mention of GitHub backup — silent
    await update.message.reply_text(f"🗑 *{name}* has been deleted.", parse_mode="Markdown")


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
    ban_user(target)
    await update.message.reply_text(f"🚫 `{target}` banned.", parse_mode="Markdown")


async def cmd_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try: target = int(ctx.args[0])
    except: await update.message.reply_text("Usage: `/unban <id>`", parse_mode="Markdown"); return
    unban_user(target)
    await update.message.reply_text(f"✅ `{target}` unbanned.", parse_mode="Markdown")


async def cmd_getlog(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id; name = " ".join(ctx.args).strip()
    if not name: await update.message.reply_text("Usage: `/getlog <name>`", parse_mode="Markdown"); return
    reg  = load_registry(); owner = str(reg.get(name, {}).get("owner_id", ""))
    if not (is_admin(uid) or owner == str(uid)):
        await update.message.reply_text("❌ Not found or not yours.", parse_mode="Markdown"); return
    lp = HOSTED_DIR / name / "bot_output.log"
    if not lp.exists(): await update.message.reply_text("❌ No log file."); return
    await update.message.reply_document(
        document=open(lp, "rb"), filename=f"{name}_log.txt",
        caption=f"📋 *{name}* log", parse_mode="Markdown")


async def cmd_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id; u = get_user(uid)
    used = get_used_slots(uid); bots = get_user_bots(uid)
    run  = sum(1 for b in bots if RUNNING_BOTS.get(b, {}).get("status") == "Running 🟢")
    await update.message.reply_text(
        f"ℹ️ *Info*\n\n🆔 `{uid}`\n"
        f"📦 `{used}/{u['slots']}` slots\n"
        f"🤖 `{len(bots)}` bots  🟢 `{run}` running\n"
        f"⭐ `{u.get('stars_spent', 0)}` stars",
        parse_mode="Markdown")

# ══════════════════════════════════════════════════════════════════════
#  FILE UPLOAD HANDLER
# ══════════════════════════════════════════════════════════════════════
async def handle_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    if is_banned(uid): return
    doc  = update.message.document
    fname = doc.file_name or "upload"; ext = Path(fname).suffix.lower()
    if ext not in (".zip", ".py"):
        await update.message.reply_text("⚠️ Send `.zip` or `.py` file.", parse_mode="Markdown"); return
    if not check_rate(uid):
        await update.message.reply_text("⏳ Rate limit: 3 deploys/10 min.", parse_mode="Markdown"); return

    user     = get_user(uid); bot_name = Path(fname).stem
    reg      = load_registry()
    is_redeploy = (str(reg.get(bot_name, {}).get("owner_id", "")) == str(uid) or is_admin(uid))
    if not is_redeploy and get_used_slots(uid) >= user["slots"]:
        await update.message.reply_text(
            f"❌ *Slot limit!* `{get_used_slots(uid)}/{user['slots']}`",
            parse_mode="Markdown", reply_markup=kb_plans()); return

    msg = await update.message.reply_text(f"⬇️ Downloading *{bot_name}*…", parse_mode="Markdown")
    HOSTED_DIR.mkdir(parents=True, exist_ok=True)
    tg_file = await ctx.bot.get_file(doc.file_id)

    if ext == ".py":
        bot_dir = HOSTED_DIR / bot_name; bot_dir.mkdir(parents=True, exist_ok=True)
        await tg_file.download_to_drive(str(bot_dir / "main.py"))
    else:
        zip_path = HOSTED_DIR / fname
        await tg_file.download_to_drive(str(zip_path))
        bot_dir = HOSTED_DIR / bot_name
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
            await msg.edit_text("❌ Invalid ZIP file."); return
        finally: zip_path.unlink(missing_ok=True)

    main_py = bot_dir / "main.py"
    if not main_py.exists():
        found = list(bot_dir.rglob("main.py")) or list(bot_dir.rglob("*.py"))
        if found: shutil.copy(found[0], main_py)
        else:
            shutil.rmtree(bot_dir, ignore_errors=True)
            await msg.edit_text("❌ No `.py` file found inside archive.", parse_mode="Markdown"); return

    await _finalize(bot_name, bot_dir, uid, msg)

# ══════════════════════════════════════════════════════════════════════
#  TEXT HANDLER
# ══════════════════════════════════════════════════════════════════════
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id; text = (update.message.text or "").strip()
    name = update.effective_user.username or update.effective_user.first_name or ""
    save_username(uid, name)
    if is_banned(uid): return

    if text == _RKB_MY_BOTS:
        t, kb = mybots_card(uid)
        await update.message.reply_text(t, parse_mode="Markdown", reply_markup=kb); return
    if text == _RKB_DEPLOY:
        await update.message.reply_text(
            "📦 *Deploy Options*\n\n1️⃣ Send `.zip` archive\n2️⃣ Send `.py` file\n"
            "3️⃣ Paste Python code directly\n4️⃣ Use Web IDE: /ide",
            parse_mode="Markdown"); return
    if text == _RKB_SLOTS:
        await update.message.reply_text("💰 *Buy Slots*", parse_mode="Markdown",
                                        reply_markup=kb_plans()); return
    if text == _RKB_STATS:
        u    = get_user(uid); used = get_used_slots(uid); bots = get_user_bots(uid)
        run  = sum(1 for b in bots if RUNNING_BOTS.get(b, {}).get("status") == "Running 🟢")
        await update.message.reply_text(
            f"📊 *Your Stats*\n\n"
            f"📦 Slots  : `{used}/{u['slots']}`\n"
            f"🤖 Bots   : `{len(bots)}` total\n"
            f"🟢 Running: `{run}`\n"
            f"⭐ Stars  : `{u.get('stars_spent', 0)}`",
            parse_mode="Markdown"); return
    if text == _RKB_HELP:
        await update.message.reply_text(
            "📦 *How to Deploy*\n\n"
            "1️⃣ *ZIP* — zip your bot folder → send here\n"
            "2️⃣ *PY* — send `main.py` directly\n"
            "3️⃣ *Code* — paste Python code → auto-named & deployed!\n"
            "4️⃣ *IDE* — `/ide` for browser code editor\n\n"
            "*Package Detection*\n"
            "No `requirements.txt`? We auto-detect what to install!\n\n"
            "*Commands*\n"
            "`/stop` `/restart` `/delete` `/getlog` `/info` `/ide`",
            parse_mode="Markdown"); return
    if text == _RKB_ADMIN and is_admin(uid):
        total   = len(RUNNING_BOTS)
        running = sum(1 for e in RUNNING_BOTS.values() if e.get("status") == "Running 🟢")
        await update.message.reply_text(
            f"👑 *Admin Panel*\n\n"
            f"🤖 `{total}` bots  🟢 `{running}` running  👥 `{len(load_users())}` users\n\n"
            f"`/all` `/users` `/user <id>` `/msg`\n"
            f"`/pr <id> <slots>` `/ban` `/unban`\n"
            f"`/ide` `/rotatetoken` `/sessions`",
            parse_mode="Markdown"); return

    # Direct Python code deploy
    if is_code(text):
        if not check_rate(uid):
            await update.message.reply_text("⏳ Rate limit: 3 deploys/10 min."); return
        user = get_user(uid)
        if get_used_slots(uid) >= user["slots"] and not is_admin(uid):
            await update.message.reply_text(
                "❌ *Slot limit!*", parse_mode="Markdown", reply_markup=kb_plans()); return
        bot_name = smart_name(text, uid)
        msg = await update.message.reply_text(
            f"🔍 *Code detected!*\n📝 Bot name: *{bot_name}*\n⚙️ Setting up…",
            parse_mode="Markdown")
        bot_dir = HOSTED_DIR / bot_name; bot_dir.mkdir(parents=True, exist_ok=True)
        (bot_dir / "main.py").write_text(text, encoding="utf-8")
        await _finalize(bot_name, bot_dir, uid, msg, code_text=text); return

    await update.message.reply_text(
        "💡 Send `.zip`/`.py` or paste Python code.\nType /start for help.")

# ══════════════════════════════════════════════════════════════════════
#  CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════════
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query; uid = q.from_user.id; d = q.data or ""
    await q.answer()

    # ── IDE device alerts ──────────────────────────────────────────────
    if d.startswith("ide_allow|"):
        if not is_admin(uid): await q.answer("❌ Admin only!", show_alert=True); return
        parts = d.split("|"); fp = parts[1]; ip = parts[2] if len(parts) > 2 else "?"
        web_ide._PENDING[fp] = "allow"
        await q.edit_message_text(f"✅ Session `{fp}` (IP:`{ip}`) allowed.", parse_mode="Markdown")
        return

    if d.startswith("ide_block|"):
        if not is_admin(uid): await q.answer("❌ Admin only!", show_alert=True); return
        parts = d.split("|"); fp = parts[1]; ip = parts[2] if len(parts) > 2 else "?"
        web_ide._block_ip(ip); web_ide._PENDING[fp] = "block"
        await q.edit_message_text(f"🚫 IP `{ip}` blocked.", parse_mode="Markdown")
        return

    # ── Standard ───────────────────────────────────────────────────────
    reg = load_registry()

    if d == "main_menu":
        await q.edit_message_text(home_text(uid), parse_mode="Markdown",
                                  reply_markup=kb_home(is_admin(uid)))

    elif d == "my_bots":
        text, kb = mybots_card(uid)
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

    elif d.startswith("detail|"):
        name = d.split("|", 1)[1]; e = RUNNING_BOTS.get(name, {})
        can  = is_admin(uid) or str(reg.get(name, {}).get("owner_id", "")) == str(uid)
        up   = fmt_up(time.time() - e["start_time"]) if e.get("start_time") else "—"
        err  = (e.get("last_error") or "None").strip()[-300:]
        await q.edit_message_text(
            f"⚙️ *{name}*\n\n"
            f"📌 {e.get('status', 'Offline 🔴')}\n"
            f"⏱ `{up}`  ·  {speed_lbl(e) if e else '—'}\n"
            f"🔄 `{e.get('restarts', 0)}` restarts\n\n"
            f"```\n{err}\n```",
            parse_mode="Markdown", reply_markup=kb_bot_card(name, can))

    elif d.startswith("errinfo|"):
        name   = d.split("|", 1)[1]; e = RUNNING_BOTS.get(name, {})
        parsed = e.get("parsed_err")
        if not parsed or not parsed.get("has_error"):
            await q.answer("No error info available.", show_alert=True); return
        runtime = 0.0
        report  = format_error_report(name, parsed, runtime)
        await q.edit_message_text(report, parse_mode="Markdown",
                                  reply_markup=kb_back(f"detail|{name}"))

    elif d.startswith("stop|"):
        name = d.split("|", 1)[1]
        if not (is_admin(uid) or str(reg.get(name, {}).get("owner_id", "")) == str(uid)):
            await q.answer("❌ Not yours!", show_alert=True); return
        _do_stop(name)
        await q.edit_message_text(f"🛑 *{name}* stopped.", parse_mode="Markdown",
                                  reply_markup=kb_back())

    elif d.startswith("restart|"):
        name = d.split("|", 1)[1]
        if not (is_admin(uid) or str(reg.get(name, {}).get("owner_id", "")) == str(uid)):
            await q.answer("❌ Not yours!", show_alert=True); return
        bot_dir = HOSTED_DIR / name
        if not (bot_dir / "main.py").exists():
            await q.answer("❌ Files missing!", show_alert=True); return
        _do_stop(name); owner = reg.get(name, {}).get("owner_id", uid)
        await asyncio.sleep(2); asyncio.create_task(run_bot(name, bot_dir, owner))
        await q.edit_message_text(f"🔄 *{name}* restarted!", parse_mode="Markdown",
                                  reply_markup=kb_back())

    elif d.startswith("delete|"):
        name = d.split("|", 1)[1]
        if not (is_admin(uid) or str(reg.get(name, {}).get("owner_id", "")) == str(uid)):
            await q.answer("❌ Not yours!", show_alert=True); return
        _kill_remove(name, uid); push_to_github(f"Del uid={uid}: {name}")
        await q.edit_message_text(f"🗑 *{name}* deleted.", parse_mode="Markdown",
                                  reply_markup=kb_back())

    elif d.startswith("logs|"):
        name = d.split("|", 1)[1]; lp = HOSTED_DIR / name / "bot_output.log"
        tail = _tail(lp, 50) or "*(No logs yet.)*"
        if len(tail) > 3200: tail = "…\n" + tail[-3100:]
        await q.edit_message_text(
            f"📋 *{name}* — Output\n\n```\n{tail}\n```",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh",  callback_data=f"logs|{name}"),
                InlineKeyboardButton("🔍 Error",    callback_data=f"errinfo|{name}"),
                InlineKeyboardButton("🔙 Back",     callback_data=f"detail|{name}"),
            ]]))

    elif d.startswith("getlog|"):
        name = d.split("|", 1)[1]; lp = HOSTED_DIR / name / "bot_output.log"
        if not lp.exists(): await q.answer("No log file.", show_alert=True); return
        await q.message.reply_document(document=open(lp, "rb"),
            filename=f"{name}_log.txt", caption=f"📋 *{name}*", parse_mode="Markdown")

    elif d == "how_host":
        await q.edit_message_text(
            "📦 *Deploy Options*\n\n"
            "1️⃣ *ZIP* → send `.zip` archive\n"
            "2️⃣ *PY* → send `.py` file\n"
            "3️⃣ *Code* → paste Python code\n"
            "4️⃣ *IDE* → `/ide` for Web IDE\n\n"
            "🔍 Auto package detection — no `requirements.txt` needed!\n"
            "🚨 Crashes auto-detected with line-level error reporting",
            parse_mode="Markdown", reply_markup=kb_back())

    elif d == "my_stats":
        u    = get_user(uid); used = get_used_slots(uid); bots = get_user_bots(uid)
        run  = sum(1 for b in bots if RUNNING_BOTS.get(b, {}).get("status") == "Running 🟢")
        await q.edit_message_text(
            f"📊 *Stats*\n\n🆔`{uid}`\n📦`{used}/{u['slots']}`\n"
            f"🤖`{len(bots)}` / 🟢`{run}`\n⭐`{u.get('stars_spent', 0)}`",
            parse_mode="Markdown", reply_markup=kb_back())

    elif d == "buy_plan":
        await q.edit_message_text("💰 *Buy Slots*\n\nPay with Telegram ⭐ Stars:",
                                  parse_mode="Markdown", reply_markup=kb_plans())

    elif d.startswith("buy_"):
        plan_key = d[4:]; plan = PLANS.get(plan_key)
        if not plan: await q.answer("Unknown plan.", show_alert=True); return
        await ctx.bot.send_invoice(
            chat_id=uid, title=plan["label"],
            description=f"{plan['slots']} extra slots — {plan['desc']}",
            payload=f"{plan_key}|{uid}", provider_token="", currency="XTR",
            prices=[LabeledPrice(plan["label"], plan["stars"])])

    elif d == "admin_panel":
        if not is_admin(uid): await q.answer("Admins only!", show_alert=True); return
        total   = len(RUNNING_BOTS)
        running = sum(1 for e in RUNNING_BOTS.values() if e.get("status") == "Running 🟢")
        await q.edit_message_text(
            f"👑 *Admin Panel*\n\n"
            f"🤖`{total}`  🟢`{running}`  👥`{len(load_users())}`\n\n"
            f"`/all` `/users` `/user <id>` `/msg`\n"
            f"`/pr <id> <slots>` `/ban` `/unban`\n"
            f"`/ide` `/rotatetoken` `/sessions`",
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
        uid  = int(uid_str); plan = PLANS[plan_key]
    except:
        await update.message.reply_text("⚠️ Payment ok — contact admin."); return
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
        log.info("Auto-starting: %s", item.name)
        asyncio.create_task(run_bot(item.name, item, owner))

# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════
async def main():
    global _APP
    log.info("═══════════════════════════════════════════════════")
    log.info("   Master Hosting Bot v3.3  —  Codian Studio 💎   ")
    log.info("═══════════════════════════════════════════════════")

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
        CommandHandler("sessions",    cmd_sessions),
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
            BotCommand("sessions",    "👑 IDE sessions (admin)"),
        ])
    except Exception: pass

    log.info("✅ v3.3 live! IDE: %s/editor", RENDER_URL or "localhost:"+str(PORT))
    await asyncio.Event().wait()


if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: log.info("Stopped.")

