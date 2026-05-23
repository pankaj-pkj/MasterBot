"""
╔══════════════════════════════════════════════════════════════════════╗
║   MASTER HOSTING BOT  v3.1  —  Ultimate + Web IDE Edition           ║
║   python-telegram-bot 20.x  ·  Python 3.11  ·  Render.com           ║
║   Codian Studio 💎                                                   ║
╠══════════════════════════════════════════════════════════════════════╣
║  v3.1 New:                                                           ║
║  • Integrated Web IDE (editor.py) on same port 8080                 ║
║  • /ide  — sends IDE link to admin                                   ║
║  • All v3.0 features retained                                        ║
╚══════════════════════════════════════════════════════════════════════╝

Render env-vars:
  BOT_TOKEN  GITHUB_PAT  GITHUB_USERNAME  REPO_NAME
  RENDER_URL  PORT  EDITOR_TOKEN
"""

import os, re, ast, sys, json, time, shutil, logging, asyncio, subprocess
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

# ── Web IDE module
import editor as web_ide

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
EDITOR_TOKEN    = os.environ.get("EDITOR_TOKEN",     "codianstudio")

if not BOT_TOKEN:
    log.critical("BOT_TOKEN not set — aborting.")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════
ADMIN_IDS: set[int] = {6960252072}

FREE_SLOTS       = 3
MAX_FAST_CRASHES = 3
FAST_CRASH_SEC   = 30

PLANS: dict[str, dict] = {
    "starter": {"stars": 15,  "slots": 3,  "label": "Starter ⭐",  "desc": "+3 bot slots"},
    "pro":     {"stars": 50,  "slots": 10, "label": "Pro 💎",      "desc": "+10 bot slots"},
    "elite":   {"stars": 100, "slots": 25, "label": "Elite 👑",    "desc": "+25 bot slots"},
}

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
    "cmd","wave","colorsys","struct","cProfile","numbers","io","string",
    "textwrap","difflib","readline","asyncio",
}

IMPORT_MAP: dict[str, str] = {
    "telegram":"python-telegram-bot","telebot":"pyTelegramBotAPI",
    "aiogram":"aiogram","pyrogram":"pyrogram","tgcrypto":"tgcrypto",
    "telethon":"telethon","requests":"requests","aiohttp":"aiohttp",
    "httpx":"httpx","urllib3":"urllib3","bs4":"beautifulsoup4",
    "lxml":"lxml","selenium":"selenium","playwright":"playwright",
    "PIL":"Pillow","cv2":"opencv-python","numpy":"numpy","pandas":"pandas",
    "sklearn":"scikit-learn","scipy":"scipy","matplotlib":"matplotlib",
    "flask":"flask","fastapi":"fastapi","uvicorn":"uvicorn","django":"django",
    "starlette":"starlette","tornado":"tornado","sqlalchemy":"SQLAlchemy",
    "pymongo":"pymongo","motor":"motor","redis":"redis",
    "psycopg2":"psycopg2-binary","pymysql":"PyMySQL","aiosqlite":"aiosqlite",
    "aiofiles":"aiofiles","dotenv":"python-dotenv","yaml":"PyYAML",
    "toml":"toml","pydantic":"pydantic","click":"click","rich":"rich",
    "loguru":"loguru","tqdm":"tqdm","colorama":"colorama",
    "apscheduler":"APScheduler","schedule":"schedule","celery":"celery",
    "cryptography":"cryptography","jwt":"PyJWT","bcrypt":"bcrypt",
    "qrcode":"qrcode","openai":"openai","anthropic":"anthropic",
    "langchain":"langchain","transformers":"transformers",
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
_APP = None

# ══════════════════════════════════════════════════════════════════════
#  JSON PERSISTENCE
# ══════════════════════════════════════════════════════════════════════
def _load_json(path: Path, default):
    try:    return json.loads(path.read_text(encoding="utf-8"))
    except: return default

def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def load_users() -> dict:  return _load_json(DATA_DIR / "users.json", {})
def save_users(d: dict):    _save_json(DATA_DIR / "users.json", d)

def get_user(uid: int) -> dict:
    data = load_users(); k = str(uid)
    if k not in data:
        data[k] = {"slots": FREE_SLOTS, "stars_spent": 0, "banned": False}
        save_users(data)
    return data[k]

def _update_user(uid: int, patch: dict):
    data = load_users(); k = str(uid)
    if k not in data: data[k] = {"slots": FREE_SLOTS, "stars_spent": 0, "banned": False}
    data[k].update(patch); save_users(data)

def set_user_slots(uid, slots): _update_user(uid, {"slots": slots})
def add_user_slots(uid, extra):
    u = get_user(uid); new = u["slots"]+extra; set_user_slots(uid, new); return new
def add_user_stars(uid, stars):
    u = get_user(uid); _update_user(uid, {"stars_spent": u.get("stars_spent",0)+stars})
def is_banned(uid): return bool(get_user(uid).get("banned", False))
def ban_user(uid):   _update_user(uid, {"banned": True})
def unban_user(uid): _update_user(uid, {"banned": False})

def load_registry() -> dict:  return _load_json(DATA_DIR / "registry.json", {})
def save_registry(d: dict):    _save_json(DATA_DIR / "registry.json", d)

def register_bot(name, uid):
    reg = load_registry(); reg[name] = {"owner_id": uid, "registered_at": time.time()}; save_registry(reg)
def unregister_bot(name):
    reg = load_registry(); reg.pop(name, None); save_registry(reg)
def get_used_slots(uid):
    return sum(1 for v in load_registry().values() if str(v.get("owner_id"))==str(uid))
def get_user_bots(uid):
    return [k for k,v in load_registry().items() if str(v.get("owner_id"))==str(uid)]

def soft_delete_bot(name: str, deleted_by: int):
    src = HOSTED_DIR / name
    if src.exists():
        dest = DELETED_DIR / f"{name}_{int(time.time())}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        _save_json(dest / "_DELETED.json", {
            "bot_name": name, "deleted_by": deleted_by,
            "deleted_at": int(time.time()), "marker": "<deleted>",
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
        log.warning("GITHUB_PAT not set — local only."); HOSTED_DIR.mkdir(exist_ok=True); return
    if (HOSTED_DIR/".git").exists():
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
#  AIOHTTP WEB SERVER  (also hosts the Web IDE on /editor)
# ══════════════════════════════════════════════════════════════════════
async def _web_root(req: web.Request) -> web.Response:
    rows = "".join(
        f"<tr><td><b>{n}</b></td><td>{d.get('status','?')}</td>"
        f"<td>{fmt_uptime(time.time()-d.get('start_time',time.time()))}</td>"
        f"<td>{d.get('restarts',0)}</td></tr>"
        for n, d in RUNNING_BOTS.items()
    )
    ide_link = f"{RENDER_URL}/editor" if RENDER_URL else "/editor"
    html = (
        "<html><head><title>Master Hosting Bot</title>"
        "<style>body{font-family:monospace;padding:20px;background:#0d1117;color:#c9d1d9}"
        "h2{color:#58a6ff}a.btn{display:inline-block;background:#238636;color:#fff;"
        "padding:8px 18px;border-radius:6px;text-decoration:none;margin:10px 0;font-weight:600}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #30363d;padding:8px}"
        "</style></head><body>"
        f"<h2>🤖 Master Hosting Bot — Live</h2>"
        f"<a class='btn' href='{ide_link}'>💎 Open Web IDE</a><br>"
        f"<table><tr><th>Bot</th><th>Status</th><th>Uptime</th><th>Restarts</th></tr>"
        f"{rows}</table><p><i>Codian Studio 💎</i></p></body></html>"
    )
    return web.Response(text=html, content_type="text/html")

async def start_web_server() -> None:
    wa = web.Application(client_max_size=64*1024*1024)   # 64 MB upload limit
    wa.router.add_get("/",       _web_root)
    wa.router.add_get("/health", lambda r: web.Response(text="OK"))
    # ── Register Web IDE routes on same app ──
    web_ide.register_routes(wa)
    runner = web.AppRunner(wa)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Web server + IDE on port %d", PORT)

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
                    log.info("Anti-sleep ping → %d", r.status)
            except Exception as exc:
                log.warning("Ping failed: %s", exc)
            await asyncio.sleep(840)

# ══════════════════════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════════════════════
def fmt_uptime(s):
    s=int(max(0,s)); h,r=divmod(s,3600); m,sc=divmod(r,60); return f"{h}h {m}m {sc}s"
def is_admin(uid): return uid in ADMIN_IDS
def speed_label(e):
    r=e.get("restarts",0)
    return "🚀 Excellent" if r==0 else "⚡ Good" if r<3 else "🐢 Unstable" if r<10 else "💀 Critical"
def _tail(path, n=40):
    try: lines=path.read_text(errors="replace").splitlines(); return "\n".join(lines[-n:])
    except: return ""
def check_rate_limit(uid):
    if is_admin(uid): return True
    now=time.time(); times=[t for t in DEPLOY_TIMES.get(uid,[]) if now-t<600]
    if len(times)>=3: return False
    times.append(now); DEPLOY_TIMES[uid]=times; return True

def detect_imports(code):
    names=[]
    try:
        tree=ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                for a in node.names: names.append(a.name.split(".")[0])
            elif isinstance(node,ast.ImportFrom) and node.module:
                names.append(node.module.split(".")[0])
    except SyntaxError:
        names=re.findall(r"^(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)",code,re.MULTILINE)
    return list(set(names))

def smart_install(code):
    imports=detect_imports(code); to_install=[]
    for imp in imports:
        if imp in STDLIB: continue
        pkg=IMPORT_MAP.get(imp,imp)
        if subprocess.run([sys.executable,"-c",f"import {imp}"],capture_output=True).returncode!=0:
            to_install.append(pkg)
    if to_install:
        log.info("Auto-installing: %s", to_install)
        subprocess.run([sys.executable,"-m","pip","install"]+to_install+["-q"],check=False,timeout=300)
    return to_install

def _install_reqs(req):
    log.info("Installing %s", req)
    subprocess.run([sys.executable,"-m","pip","install","-r",str(req),"-q"],check=False,timeout=300)

_CODE_RE = re.compile(
    r"(^\s*(import|from)\s+\w+|^\s*(def|async\s+def|class)\s+\w+|"
    r"if\s+__name__\s*==|^\s*@\w+)", re.MULTILINE)
def is_python_code(text): return len(text)>20 and bool(_CODE_RE.search(text))

def smart_bot_name(code, uid):
    lower=code.lower()
    if any(k in lower for k in ("telegram","telebot","aiogram","pyrogram")): prefix="tgbot"
    elif any(k in lower for k in ("discord","nextcord")): prefix="dsbot"
    elif any(k in lower for k in ("flask","fastapi","django","tornado")): prefix="webapp"
    elif any(k in lower for k in ("scrape","bs4","selenium","playwright")): prefix="scraper"
    elif any(k in lower for k in ("schedule","cron","apscheduler")): prefix="scheduler"
    elif any(k in lower for k in ("openai","anthropic","gpt","ai")): prefix="aibot"
    else: prefix="script"
    m=re.search(r"class\s+([A-Za-z][A-Za-z0-9]+)",code)
    if m: return f"{m.group(1).lower()[:15]}_{uid%9999}"
    m=re.search(r"#\s*([A-Za-z][A-Za-z0-9 _-]+)",code)
    if m: return f"{m.group(1).strip().lower()[:18].replace(' ','_')}_{uid%9999}"
    return f"{prefix}_{uid}_{int(time.time())%99999}"

def _do_stop(name):
    e=RUNNING_BOTS.get(name,{})
    if e:
        e["active"]=False
        p=e.get("process")
        if p and p.poll() is None:
            p.terminate()
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill()
        e["status"]="Stopped 🛑"

def _kill_and_remove(name, deleted_by=0):
    _do_stop(name); RUNNING_BOTS.pop(name,None)
    soft_delete_bot(name, deleted_by)
    shutil.rmtree(HOSTED_DIR/name, ignore_errors=True)

# ══════════════════════════════════════════════════════════════════════
#  CHILD-BOT RUNNER
# ══════════════════════════════════════════════════════════════════════
async def run_bot(name: str, bot_dir: Path, owner_id: int) -> None:
    log_path=bot_dir/"bot_output.log"
    RUNNING_BOTS[name]={"active":True,"start_time":time.time(),"status":"Starting ⏳",
                        "process":None,"restarts":0,"last_error":None,"owner_id":owner_id}
    fast_crashes=0

    while RUNNING_BOTS.get(name,{}).get("active"):
        # Check for restart flag from Web IDE
        flag=bot_dir/".restart_flag"
        if flag.exists():
            try: flag.unlink()
            except: pass

        t0=time.time(); RUNNING_BOTS[name]["status"]="Running 🟢"
        lf=None
        try:
            lf=open(log_path,"a",encoding="utf-8",errors="replace")
            proc=subprocess.Popen([sys.executable,"main.py"],cwd=str(bot_dir),stdout=lf,stderr=lf)
        except Exception as exc:
            if lf:
                try: lf.close()
                except: pass
            RUNNING_BOTS[name].update(status="Launch Error ❌",active=False,last_error=str(exc))
            await _notify(owner_id,f"❌ *{name}* failed:\n`{exc}`"); break

        RUNNING_BOTS[name]["process"]=proc
        while proc.poll() is None:
            await asyncio.sleep(2)
            # Check restart flag mid-run (from Web IDE Run button)
            if (bot_dir/".restart_flag").exists():
                log.info("Web IDE restart flag detected for %s", name)
                try: (bot_dir/".restart_flag").unlink()
                except: pass
                proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill()
                break

        try: lf.close()
        except: pass
        runtime=time.time()-t0
        tail=_tail(log_path); RUNNING_BOTS[name]["last_error"]=tail; RUNNING_BOTS[name]["restarts"]+=1

        if not RUNNING_BOTS.get(name,{}).get("active"):
            RUNNING_BOTS[name]["status"]="Stopped 🛑"; break

        log.warning("%s exited (code=%s, runtime=%.1fs)", name, proc.returncode, runtime)
        if runtime<FAST_CRASH_SEC:
            fast_crashes+=1
            if fast_crashes>=MAX_FAST_CRASHES:
                RUNNING_BOTS[name].update(active=False,status="Error ❌ (Auto-stopped)")
                preview=tail[-900:] if tail else "*(no output)*"
                await _notify(owner_id,
                    f"⚠️ *{name}* crashed {MAX_FAST_CRASHES}× quickly — *auto-stopped*.\n\n"
                    f"🔍 *Error:*\n```\n{preview}\n```\n\nFix & re-upload or use Web IDE."); break
        else:
            fast_crashes=0
        RUNNING_BOTS[name]["status"]="Restarting ⏳"
        await asyncio.sleep(5)

async def _notify(owner_id, text):
    if not _APP or not owner_id: return
    try: await _APP.bot.send_message(owner_id, text, parse_mode="Markdown")
    except Exception as exc: log.warning("Notify failed: %s", exc)

# ══════════════════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════════════════
def reply_kb(admin=False):
    rows=[[KeyboardButton(_RKB_MY_BOTS),KeyboardButton(_RKB_DEPLOY)],
          [KeyboardButton(_RKB_SLOTS),  KeyboardButton(_RKB_STATS)],
          [KeyboardButton(_RKB_HELP)]]
    if admin: rows.append([KeyboardButton(_RKB_ADMIN)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)

def kb_home(admin=False):
    rows=[[InlineKeyboardButton("🤖 My Bots",callback_data="my_bots"),
           InlineKeyboardButton("📦 Deploy", callback_data="how_host")],
          [InlineKeyboardButton("💰 Buy Slots",callback_data="buy_plan"),
           InlineKeyboardButton("📊 My Stats",callback_data="my_stats")]]
    if admin: rows.append([InlineKeyboardButton("👑 Admin Panel",callback_data="admin_panel")])
    return InlineKeyboardMarkup(rows)

def kb_plans():
    rows=[[InlineKeyboardButton(f"{p['label']} — {p['stars']} ⭐  ({p['desc']})",
                                callback_data=f"buy_{k}")] for k,p in PLANS.items()]
    rows.append([InlineKeyboardButton("🔙 Back",callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)

def kb_bot_card(name,can_ctrl):
    rows=[]
    if can_ctrl:
        rows.append([InlineKeyboardButton("🛑 Stop",callback_data=f"stop|{name}"),
                     InlineKeyboardButton("🔄 Restart",callback_data=f"restart|{name}"),
                     InlineKeyboardButton("🗑 Delete",callback_data=f"delete|{name}")])
        rows.append([InlineKeyboardButton("📋 Logs",callback_data=f"logs|{name}"),
                     InlineKeyboardButton("⬇ Download Log",callback_data=f"getlog|{name}")])
    rows.append([InlineKeyboardButton("🔙 Back",callback_data="my_bots")])
    return InlineKeyboardMarkup(rows)

def kb_back(to="main_menu"):
    lbl="🏠 Home" if to=="main_menu" else "🔙 Back"
    return InlineKeyboardMarkup([[InlineKeyboardButton(lbl,callback_data=to)]])

# ══════════════════════════════════════════════════════════════════════
#  DISPLAY HELPERS
# ══════════════════════════════════════════════════════════════════════
def home_text(uid):
    u=get_user(uid); used=get_used_slots(uid); plan="Admin 👑" if is_admin(uid) else "Free ✨"
    return (f"🤖 *Master Hosting Bot*\n\n"
            f"👤 Plan   : {plan}\n"
            f"📦 Slots  : `{used}/{u['slots']}` used\n\n"
            f"Send `.zip`, `.py` or paste Python code to deploy!\n"
            f"💎 Web IDE available — `/ide` for link\n\n"
            f"_Codian Studio 💎_")

def mybots_card(uid):
    bots=get_user_bots(uid)
    if not bots:
        return "📭 *No bots hosted yet.*\n\nSend a `.zip` / `.py` or paste code!", kb_back()
    lines=["🤖 *Your Hosted Bots*\n"]; rows=[]
    for name in bots:
        e=RUNNING_BOTS.get(name,{}); st=e.get("status","Offline 🔴")
        up=fmt_uptime(time.time()-e["start_time"]) if e.get("start_time") else "—"
        rs=e.get("restarts",0); spd=speed_label(e) if e else "—"
        lines.append(f"🔹 *{name}*\n   {st}  ·  ⏱ `{up}`\n   {spd}  ·  🔄 `{rs}` restarts\n")
        rows.append([InlineKeyboardButton(f"⚙️  {name}",callback_data=f"detail|{name}")])
    rows.append([InlineKeyboardButton("🏠 Home",callback_data="main_menu")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)

# ══════════════════════════════════════════════════════════════════════
#  SHARED DEPLOY FINALIZE
# ══════════════════════════════════════════════════════════════════════
async def _finalize_deploy(bot_name, bot_dir, uid, msg, code_text=""):
    user=get_user(uid)
    code=code_text or (bot_dir/"main.py").read_text(errors="replace")
    req=bot_dir/"requirements.txt"
    if req.exists():
        await msg.edit_text(f"⚙️ Installing requirements for *{bot_name}*…",parse_mode="Markdown")
        _install_reqs(req)
    else:
        await msg.edit_text(f"🔍 Auto-detecting packages for *{bot_name}*…",parse_mode="Markdown")
        installed=smart_install(code)
        if installed: log.info("Auto-installed for %s: %s", bot_name, installed)
    if bot_name in RUNNING_BOTS:
        _do_stop(bot_name); await asyncio.sleep(2)
    register_bot(bot_name, uid); push_to_github(f"Deployed: {bot_name}")
    asyncio.create_task(run_bot(bot_name, bot_dir, uid))
    new_used=get_used_slots(uid)
    await msg.edit_text(
        f"✅ *{bot_name}* deployed!\n\n"
        f"📦 Slots: `{new_used}/{user['slots']}`\n"
        f"💎 Edit live in Web IDE: /ide",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🤖 My Bots",callback_data="my_bots"),
            InlineKeyboardButton("🏠 Home",   callback_data="main_menu"),
        ]]),
    )

# ══════════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; get_user(uid)
    if is_banned(uid): await update.message.reply_text("🚫 You are banned."); return
    await update.message.reply_text(home_text(uid),parse_mode="Markdown",reply_markup=reply_kb(is_admin(uid)))
    await update.message.reply_text("👇 Use the menu below:",reply_markup=kb_home(is_admin(uid)))

async def cmd_ide(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_admin(uid): await update.message.reply_text("❌ Admin only."); return
    url=f"{RENDER_URL}/editor?token={EDITOR_TOKEN}" if RENDER_URL else f"http://localhost:{PORT}/editor?token={EDITOR_TOKEN}"
    await update.message.reply_text(
        f"💎 *Web IDE Link*\n\n"
        f"🔗 `{url}`\n\n"
        f"🔑 Token: `{EDITOR_TOKEN}`\n\n"
        f"⚠️ Keep this link private — full file access!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Open Web IDE",url=url)]])
    )

async def cmd_mybots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if is_banned(uid): return
    text,kb=mybots_card(uid)
    await update.message.reply_text(text,parse_mode="Markdown",reply_markup=kb)

async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_admin(uid): await update.message.reply_text("❌ Admins only."); return
    if not RUNNING_BOTS: await update.message.reply_text("📭 No bots hosted."); return
    reg=load_registry(); lines=["👑 *All Hosted Bots*\n"]
    for name,e in RUNNING_BOTS.items():
        up=fmt_uptime(time.time()-e.get("start_time",time.time()))
        own=reg.get(name,{}).get("owner_id","?"); rs=e.get("restarts",0)
        lines.append(f"🔹 *{name}*\n   Owner:`{own}` · {e.get('status','?')}\n   ⏱`{up}` · 🔄`{rs}` · {speed_label(e)}\n")
    await update.message.reply_text("\n".join(lines),parse_mode="Markdown")

async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_admin(uid): await update.message.reply_text("❌ Admins only."); return
    users=load_users(); reg=load_registry(); total=len(users); lines=[f"👥 *Total Users: {total}*\n"]
    for k,v in list(users.items()):
        bots=sum(1 for rv in reg.values() if str(rv.get("owner_id"))==k)
        banned=" 🚫" if v.get("banned") else ""
        lines.append(f"• `{k}`{banned}  slots:`{v.get('slots',0)}`  bots:`{bots}`  ⭐:`{v.get('stars_spent',0)}`")
        if len(lines)>30: lines.append(f"_...and {total-30} more_"); break
    await update.message.reply_text("\n".join(lines),parse_mode="Markdown")

async def cmd_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not is_admin(uid): await update.message.reply_text("❌ Admins only."); return
    if not ctx.args:
        await update.message.reply_text(
            "Usage:\n`/msg Hello!`  — broadcast\n`/msg 123456 Hi`  — DM",parse_mode="Markdown"); return
    if ctx.args[0].isdigit():
        target=int(ctx.args[0]); message=" ".join(ctx.args[1:])
        if not message: await update.message.reply_text("Include a message after user ID."); return
        try:
            await ctx.bot.send_message(target,f"📢 *From Admin:*\n\n{message}",parse_mode="Markdown")
            await update.message.reply_text(f"✅ Sent to `{target}`.",parse_mode="Markdown")
        except Exception as exc:
            await update.message.reply_text(f"❌ Failed: `{exc}`",parse_mode="Markdown")
        return
    message=" ".join(ctx.args); users=load_users(); ok=fail=0
    status_msg=await update.message.reply_text(f"📡 Broadcasting to {len(users)} users…")
    for uid_str in users:
        try:
            await ctx.bot.send_message(int(uid_str),f"📢 *Broadcast:*\n\n{message}",parse_mode="Markdown")
            ok+=1
        except: fail+=1
        await asyncio.sleep(0.05)
    await status_msg.edit_text(f"📡 Done! ✅{ok}  ❌{fail}")

async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; bot_name=" ".join(ctx.args).strip()
    if not bot_name: await update.message.reply_text("Usage: `/stop <name>`",parse_mode="Markdown"); return
    reg=load_registry(); owner=str(reg.get(bot_name,{}).get("owner_id",""))
    if not (is_admin(uid) or owner==str(uid)):
        await update.message.reply_text(f"❌ *{bot_name}* not found or not yours.",parse_mode="Markdown"); return
    _do_stop(bot_name)
    await update.message.reply_text(f"🛑 *{bot_name}* stopped.",parse_mode="Markdown")

async def cmd_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; bot_name=" ".join(ctx.args).strip()
    if not bot_name: await update.message.reply_text("Usage: `/restart <name>`",parse_mode="Markdown"); return
    reg=load_registry(); owner=str(reg.get(bot_name,{}).get("owner_id",""))
    if not (is_admin(uid) or owner==str(uid)):
        await update.message.reply_text(f"❌ Not found or not yours.",parse_mode="Markdown"); return
    bot_dir=HOSTED_DIR/bot_name
    if not (bot_dir/"main.py").exists():
        await update.message.reply_text("❌ Bot files missing — re-upload."); return
    _do_stop(bot_name); await asyncio.sleep(2)
    asyncio.create_task(run_bot(bot_name, bot_dir, uid))
    await update.message.reply_text(f"🔄 *{bot_name}* restarted!",parse_mode="Markdown")

async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; bot_name=" ".join(ctx.args).strip()
    if not bot_name: await update.message.reply_text("Usage: `/delete <name>`",parse_mode="Markdown"); return
    reg=load_registry(); owner=str(reg.get(bot_name,{}).get("owner_id",""))
    if not (is_admin(uid) or owner==str(uid)):
        await update.message.reply_text(f"❌ Not found or not yours.",parse_mode="Markdown"); return
    _kill_and_remove(bot_name, deleted_by=uid); push_to_github(f"Deleted: {bot_name}")
    await update.message.reply_text(
        f"🗑 *{bot_name}* deleted. _(Backed up in GitHub)_",parse_mode="Markdown")

async def cmd_pr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): await update.message.reply_text("❌ Admin only."); return
    try: target=int(ctx.args[0]); slots=int(ctx.args[1])
    except: await update.message.reply_text("Usage: `/pr <id> <slots>`",parse_mode="Markdown"); return
    set_user_slots(target,slots); push_to_github(f"Slots: {target}→{slots}")
    await update.message.reply_text(f"✅ `{target}` → *{slots}* slots.",parse_mode="Markdown")

async def cmd_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try: target=int(ctx.args[0])
    except: await update.message.reply_text("Usage: `/ban <id>`",parse_mode="Markdown"); return
    ban_user(target); await update.message.reply_text(f"🚫 `{target}` banned.",parse_mode="Markdown")

async def cmd_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try: target=int(ctx.args[0])
    except: await update.message.reply_text("Usage: `/unban <id>`",parse_mode="Markdown"); return
    unban_user(target); await update.message.reply_text(f"✅ `{target}` unbanned.",parse_mode="Markdown")

async def cmd_getlog(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; bot_name=" ".join(ctx.args).strip()
    if not bot_name: await update.message.reply_text("Usage: `/getlog <name>`",parse_mode="Markdown"); return
    reg=load_registry(); owner=str(reg.get(bot_name,{}).get("owner_id",""))
    if not (is_admin(uid) or owner==str(uid)):
        await update.message.reply_text("❌ Not found or not yours.",parse_mode="Markdown"); return
    log_path=HOSTED_DIR/bot_name/"bot_output.log"
    if not log_path.exists(): await update.message.reply_text("❌ No log file found."); return
    await update.message.reply_document(document=open(log_path,"rb"),
        filename=f"{bot_name}_log.txt",caption=f"📋 *{bot_name}* log",parse_mode="Markdown")

async def cmd_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; u=get_user(uid)
    used=get_used_slots(uid); bots=get_user_bots(uid)
    run=sum(1 for b in bots if RUNNING_BOTS.get(b,{}).get("status")=="Running 🟢")
    await update.message.reply_text(
        f"ℹ️ *Your Info*\n\n🆔 `{uid}`\n📦 `{used}/{u['slots']}`\n"
        f"🤖 `{len(bots)}` bots, `{run}` running\n⭐ `{u.get('stars_spent',0)}`",
        parse_mode="Markdown")

# ══════════════════════════════════════════════════════════════════════
#  FILE UPLOAD HANDLER
# ══════════════════════════════════════════════════════════════════════
async def handle_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if is_banned(uid): return
    doc=update.message.document; fname=doc.file_name or "upload"; ext=Path(fname).suffix.lower()
    if ext not in (".zip",".py"):
        await update.message.reply_text("⚠️ Send `.zip` or `.py` file.",parse_mode="Markdown"); return
    if not check_rate_limit(uid):
        await update.message.reply_text("⏳ Rate limit: 3 deploys/10 min.",parse_mode="Markdown"); return
    user=get_user(uid); bot_name=Path(fname).stem; reg=load_registry()
    is_redeploy=str(reg.get(bot_name,{}).get("owner_id",""))==str(uid) or is_admin(uid)
    if not is_redeploy and get_used_slots(uid)>=user["slots"]:
        await update.message.reply_text(
            f"❌ *Slot limit!* `{get_used_slots(uid)}/{user['slots']}`\nUpgrade 👇",
            parse_mode="Markdown",reply_markup=kb_plans()); return
    msg=await update.message.reply_text(f"⬇️ Downloading *{bot_name}*…",parse_mode="Markdown")
    HOSTED_DIR.mkdir(parents=True,exist_ok=True)
    tg_file=await ctx.bot.get_file(doc.file_id)
    if ext==".py":
        bot_dir=HOSTED_DIR/bot_name; bot_dir.mkdir(parents=True,exist_ok=True)
        await tg_file.download_to_drive(str(bot_dir/"main.py"))
    else:
        zip_path=HOSTED_DIR/fname
        await tg_file.download_to_drive(str(zip_path))
        bot_dir=HOSTED_DIR/bot_name
        if bot_dir.exists(): shutil.rmtree(bot_dir)
        bot_dir.mkdir(parents=True)
        try:
            with ZipFile(zip_path,"r") as zf:
                members=zf.namelist()
                tops={m.split("/")[0] for m in members if m.strip("/")}
                strip=(list(tops)[0]+"/") if len(tops)==1 and any("/" in m for m in members) else ""
                for member in members:
                    rel=member[len(strip):] if strip else member
                    if not rel: continue
                    dest=bot_dir/rel
                    if member.endswith("/"): dest.mkdir(parents=True,exist_ok=True)
                    else: dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(zf.read(member))
        except BadZipFile:
            zip_path.unlink(missing_ok=True); shutil.rmtree(bot_dir,ignore_errors=True)
            await msg.edit_text("❌ Invalid ZIP."); return
        finally: zip_path.unlink(missing_ok=True)
    main_py=bot_dir/"main.py"
    if not main_py.exists():
        found=list(bot_dir.rglob("main.py")) or list(bot_dir.rglob("*.py"))
        if found: shutil.copy(found[0],main_py)
        else:
            shutil.rmtree(bot_dir,ignore_errors=True)
            await msg.edit_text("❌ No `.py` found in archive.",parse_mode="Markdown"); return
    await _finalize_deploy(bot_name, bot_dir, uid, msg)

# ══════════════════════════════════════════════════════════════════════
#  TEXT HANDLER  (reply keyboard + direct code deploy)
# ══════════════════════════════════════════════════════════════════════
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; text=(update.message.text or "").strip()
    if is_banned(uid): return

    if text==_RKB_MY_BOTS:
        t,kb=mybots_card(uid); await update.message.reply_text(t,parse_mode="Markdown",reply_markup=kb); return
    if text==_RKB_DEPLOY:
        await update.message.reply_text(
            "📦 *Deploy Your Bot*\n\nSend `.zip`, `.py` or paste Python code!",parse_mode="Markdown"); return
    if text==_RKB_SLOTS:
        await update.message.reply_text("💰 *Buy Slots*",parse_mode="Markdown",reply_markup=kb_plans()); return
    if text==_RKB_STATS:
        u=get_user(uid); used=get_used_slots(uid); bots=get_user_bots(uid)
        run=sum(1 for b in bots if RUNNING_BOTS.get(b,{}).get("status")=="Running 🟢")
        await update.message.reply_text(
            f"📊 *Stats*\n\n📦 `{used}/{u['slots']}`\n🤖 `{len(bots)}` total\n🟢 `{run}` running\n⭐ `{u.get('stars_spent',0)}`",
            parse_mode="Markdown"); return
    if text==_RKB_HELP:
        await update.message.reply_text(
            "📦 *How to Deploy*\n\n"
            "1️⃣ ZIP → send here\n2️⃣ `.py` → send here\n"
            "3️⃣ Paste Python code → auto-deploy!\n\n"
            "💎 Web IDE: `/ide` (admin)\n\n"
            "`/stop` `/restart` `/delete` `/getlog` `/info`",
            parse_mode="Markdown"); return
    if text==_RKB_ADMIN and is_admin(uid):
        total=len(RUNNING_BOTS); running=sum(1 for e in RUNNING_BOTS.values() if e.get("status")=="Running 🟢")
        await update.message.reply_text(
            f"👑 *Admin*\n\n🤖`{total}` · 🟢`{running}` · 👥`{len(load_users())}`\n\n"
            f"`/all` `/users` `/msg` `/pr` `/ban` `/unban` `/ide`",parse_mode="Markdown"); return
    if is_python_code(text):
        if not check_rate_limit(uid):
            await update.message.reply_text("⏳ Rate limit.",parse_mode="Markdown"); return
        user=get_user(uid)
        if get_used_slots(uid)>=user["slots"] and not is_admin(uid):
            await update.message.reply_text("❌ *Slot limit!*",parse_mode="Markdown",reply_markup=kb_plans()); return
        bot_name=smart_bot_name(text,uid)
        msg=await update.message.reply_text(
            f"🔍 *Code detected!*\n📝 Name: *{bot_name}*\n⚙️ Setting up…",parse_mode="Markdown")
        bot_dir=HOSTED_DIR/bot_name; bot_dir.mkdir(parents=True,exist_ok=True)
        (bot_dir/"main.py").write_text(text,encoding="utf-8")
        await _finalize_deploy(bot_name,bot_dir,uid,msg,code_text=text); return
    await update.message.reply_text("💡 Send `.zip`/`.py` or paste code. Type /start for help.")

# ══════════════════════════════════════════════════════════════════════
#  CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════════
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; uid=q.from_user.id; d=q.data or ""; await q.answer()
    if d=="main_menu":
        await q.edit_message_text(home_text(uid),parse_mode="Markdown",reply_markup=kb_home(is_admin(uid)))
    elif d=="my_bots":
        text,kb=mybots_card(uid); await q.edit_message_text(text,parse_mode="Markdown",reply_markup=kb)
    elif d.startswith("detail|"):
        name=d.split("|",1)[1]; e=RUNNING_BOTS.get(name,{}); reg=load_registry()
        can=is_admin(uid) or str(reg.get(name,{}).get("owner_id",""))==str(uid)
        up=fmt_uptime(time.time()-e["start_time"]) if e.get("start_time") else "—"
        err=(e.get("last_error") or "None").strip()[-400:]
        await q.edit_message_text(
            f"⚙️ *{name}*\n\n📌 {e.get('status','Offline 🔴')}\n⏱ `{up}`\n"
            f"🚀 {speed_label(e) if e else '—'}\n🔄 `{e.get('restarts',0)}`\n\n```\n{err}\n```",
            parse_mode="Markdown",reply_markup=kb_bot_card(name,can))
    elif d.startswith("stop|"):
        name=d.split("|",1)[1]; reg=load_registry()
        if not (is_admin(uid) or str(reg.get(name,{}).get("owner_id",""))==str(uid)):
            await q.answer("❌ Not yours!",show_alert=True); return
        _do_stop(name)
        await q.edit_message_text(f"🛑 *{name}* stopped.",parse_mode="Markdown",reply_markup=kb_back())
    elif d.startswith("restart|"):
        name=d.split("|",1)[1]; reg=load_registry()
        if not (is_admin(uid) or str(reg.get(name,{}).get("owner_id",""))==str(uid)):
            await q.answer("❌ Not yours!",show_alert=True); return
        bot_dir=HOSTED_DIR/name
        if not (bot_dir/"main.py").exists(): await q.answer("❌ Files missing!",show_alert=True); return
        _do_stop(name); owner=reg.get(name,{}).get("owner_id",uid)
        await asyncio.sleep(2); asyncio.create_task(run_bot(name,bot_dir,owner))
        await q.edit_message_text(f"🔄 *{name}* restarted!",parse_mode="Markdown",reply_markup=kb_back())
    elif d.startswith("delete|"):
        name=d.split("|",1)[1]; reg=load_registry()
        if not (is_admin(uid) or str(reg.get(name,{}).get("owner_id",""))==str(uid)):
            await q.answer("❌ Not yours!",show_alert=True); return
        _kill_and_remove(name,deleted_by=uid); push_to_github(f"Deleted: {name}")
        await q.edit_message_text(f"🗑 *{name}* deleted. _(Backed up)_",
                                  parse_mode="Markdown",reply_markup=kb_back())
    elif d.startswith("logs|"):
        name=d.split("|",1)[1]; log_path=HOSTED_DIR/name/"bot_output.log"
        tail=_tail(log_path,50) or "*(No logs.)*"
        if len(tail)>3300: tail="…\n"+tail[-3200:]
        await q.edit_message_text(f"📋 *{name}*\n\n```\n{tail}\n```",parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh",callback_data=f"logs|{name}"),
                InlineKeyboardButton("🔙 Back",   callback_data=f"detail|{name}"),
            ]]))
    elif d.startswith("getlog|"):
        name=d.split("|",1)[1]; log_path=HOSTED_DIR/name/"bot_output.log"
        if not log_path.exists(): await q.answer("No log file.",show_alert=True); return
        await q.message.reply_document(document=open(log_path,"rb"),
            filename=f"{name}_log.txt",caption=f"📋 *{name}*",parse_mode="Markdown")
    elif d=="how_host":
        await q.edit_message_text(
            "📦 *Deploy Options*\n\n1️⃣ ZIP\n2️⃣ .py file\n3️⃣ Paste code\n4️⃣ 💎 Web IDE: /ide\n\n"
            "Auto package detection — no requirements.txt needed!",
            parse_mode="Markdown",reply_markup=kb_back())
    elif d=="my_stats":
        u=get_user(uid); used=get_used_slots(uid); bots=get_user_bots(uid)
        run=sum(1 for b in bots if RUNNING_BOTS.get(b,{}).get("status")=="Running 🟢")
        await q.edit_message_text(
            f"📊 *Stats*\n\n🆔`{uid}`\n📦`{used}/{u['slots']}`\n🤖`{len(bots)}` / 🟢`{run}`\n⭐`{u.get('stars_spent',0)}`",
            parse_mode="Markdown",reply_markup=kb_back())
    elif d=="buy_plan":
        await q.edit_message_text("💰 *Upgrade*\n\nPay with ⭐ Stars:",
                                  parse_mode="Markdown",reply_markup=kb_plans())
    elif d.startswith("buy_"):
        plan_key=d[4:]; plan=PLANS.get(plan_key)
        if not plan: await q.answer("Unknown.",show_alert=True); return
        await ctx.bot.send_invoice(chat_id=uid,title=plan["label"],
            description=f"{plan['slots']} extra slots — {plan['desc']}",
            payload=f"{plan_key}|{uid}",provider_token="",currency="XTR",
            prices=[LabeledPrice(plan["label"],plan["stars"])])
    elif d=="admin_panel":
        if not is_admin(uid): await q.answer("Admins only!",show_alert=True); return
        total=len(RUNNING_BOTS); running=sum(1 for e in RUNNING_BOTS.values() if e.get("status")=="Running 🟢")
        await q.edit_message_text(
            f"👑 *Admin*\n\n🤖`{total}` · 🟢`{running}` · 👥`{len(load_users())}`\n\n"
            f"`/all` `/users` `/msg` `/pr` `/ban` `/unban` `/ide`",
            parse_mode="Markdown",reply_markup=kb_back())

# ══════════════════════════════════════════════════════════════════════
#  PAYMENTS
# ══════════════════════════════════════════════════════════════════════
async def pre_checkout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def payment_success(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    payment=update.message.successful_payment
    try:
        plan_key,uid_str=payment.invoice_payload.split("|"); uid=int(uid_str); plan=PLANS[plan_key]
    except:
        await update.message.reply_text("⚠️ Payment received — contact admin."); return
    new_slots=add_user_slots(uid,plan["slots"]); add_user_stars(uid,plan["stars"])
    push_to_github(f"Stars: {plan_key} by {uid}")
    await update.message.reply_text(
        f"🎉 *Payment OK!*\n\n📦 *{plan['label']}*\n➕ +{plan['slots']} slots\n📊 Now: *{new_slots}*\n\n_Codian Studio 💎_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📦 Deploy Now",callback_data="how_host")]]))

# ══════════════════════════════════════════════════════════════════════
#  AUTO-START PERSISTED BOTS
# ══════════════════════════════════════════════════════════════════════
async def autostart_persisted():
    reg=load_registry()
    if not HOSTED_DIR.exists(): return
    for item in HOSTED_DIR.iterdir():
        if not item.is_dir() or item.name.startswith(("_",".")): continue
        if not (item/"main.py").exists(): continue
        req=item/"requirements.txt"
        if req.exists(): _install_reqs(req)
        owner=reg.get(item.name,{}).get("owner_id",0)
        log.info("Auto-starting: %s", item.name)
        asyncio.create_task(run_bot(item.name, item, owner))

# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════
async def main():
    global _APP
    log.info("═══════════════════════════════════════════════════")
    log.info("   Master Hosting Bot v3.1 + Web IDE  —  Starting ")
    log.info("═══════════════════════════════════════════════════")

    configure_git(); sync_from_github()
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    DELETED_DIR.mkdir(parents=True,exist_ok=True)

    # Share runtime state with Web IDE module
    web_ide.init_editor(RUNNING_BOTS, None)

    _APP=ApplicationBuilder().token(BOT_TOKEN).build()

    _APP.add_handler(CommandHandler("start",   cmd_start))
    _APP.add_handler(CommandHandler("ide",     cmd_ide))
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
    _APP.add_handler(MessageHandler(filters.Document.ALL, handle_upload))
    _APP.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    _APP.add_handler(CallbackQueryHandler(handle_callback))
    _APP.add_handler(PreCheckoutQueryHandler(pre_checkout))
    _APP.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, payment_success))

    # Update IDE ref after _APP is built
    web_ide._APP_REF = _APP

    await start_web_server()            # also registers /editor routes
    asyncio.create_task(keep_alive_loop())
    await autostart_persisted()

    log.info("Clearing webhook…")
    await _APP.bot.delete_webhook(drop_pending_updates=True)
    await _APP.initialize()
    await _APP.start()
    await _APP.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

    try:
        await _APP.bot.set_my_commands([
            BotCommand("start",   "🏠 Home"),
            BotCommand("ide",     "💎 Web IDE link (admin)"),
            BotCommand("mybots",  "🤖 My bots"),
            BotCommand("info",    "📊 My info"),
            BotCommand("stop",    "🛑 Stop bot"),
            BotCommand("restart", "🔄 Restart bot"),
            BotCommand("delete",  "🗑 Delete bot"),
            BotCommand("getlog",  "📋 Download log"),
            BotCommand("all",     "👑 All bots (admin)"),
            BotCommand("users",   "👑 All users (admin)"),
            BotCommand("msg",     "👑 Broadcast (admin)"),
            BotCommand("pr",      "👑 Set slots (admin)"),
        ])
    except Exception: pass

    log.info("✅ Master Hosting Bot v3.1 is live! IDE → %s/editor", RENDER_URL or "localhost")
    await asyncio.Event().wait()

if __name__=="__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: log.info("Stopped.")

