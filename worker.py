"""
╔══════════════════════════════════════════════════════════════════════╗
║   WORKER NODE  —  for Master Hosting Bot                             ║
║   Codian Studio 💎                                                   ║
╠══════════════════════════════════════════════════════════════════════╣
║  Run this on a SEPARATE Render account (or any host). It receives    ║
║  bots from the master and runs them. It does NOT run a Telegram bot  ║
║  and needs NO BOT_TOKEN.                                             ║
║                                                                      ║
║  ── Render setup ───────────────────────────────────────────────    ║
║  • New Web Service → same repo                                       ║
║  • Start command:  python worker.py                                 ║
║  • Environment variables:                                           ║
║        NODE_SECRET   (REQUIRED) — any random string. Use the SAME   ║
║                       value when you run  /addnode <url> <secret>    ║
║                       on the master.                                ║
║        RENDER_URL    (optional) — this worker's own https URL, e.g. ║
║                       https://worker1.onrender.com  (enables the    ║
║                       anti-sleep self-ping so Render won't idle it). ║
║        PORT          — Render sets this automatically.              ║
║                                                                      ║
║  ⚠️  DO NOT copy the master's BOT_TOKEN / GITHUB_PAT here. The       ║
║      worker doesn't need them, and reusing BOT_TOKEN would clash    ║
║      with the master (Telegram allows only one poller per token).   ║
║      Each hosted child bot brings its OWN .env inside its files, so ║
║      its secrets travel with it — nothing else is shared.           ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os, re, sys, io, time, json, zipfile, shutil, logging, asyncio, subprocess, socket
from pathlib import Path

import aiohttp
from aiohttp import web

# ── LOGGING ───────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s │ %(levelname)-8s │ %(message)s",
    datefmt="%H:%M:%S", level=logging.INFO, stream=sys.stdout,
)
log = logging.getLogger("Worker")

# ── ENV ───────────────────────────────────────────────────────────────

def _load_dotenv(path: Path = None):
    """Load .env next to this script. Real env vars always take priority."""
    path = path or (Path(__file__).resolve().parent / ".env")
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in raw.splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if not key or key in os.environ:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        os.environ[key] = val

_load_dotenv()

NODE_SECRET = os.environ.get("NODE_SECRET", "")
RENDER_URL  = os.environ.get("RENDER_URL", "").rstrip("/")
PORT        = int(os.environ.get("PORT", 8080))

if not NODE_SECRET:
    log.critical("NODE_SECRET not set — aborting. Set it to the same value "
                 "you use in /addnode on the master.")
    sys.exit(1)

# ── CONSTANTS ─────────────────────────────────────────────────────────
HOSTED_DIR        = Path("hosted_bots")
MAX_FAST_CRASHES  = 3
FAST_CRASH_SEC    = 30
MAX_HEAL_TRIES    = 2
STARTUP_DELAY_SEC = 5

# Secrets that must NEVER leak into a child bot process.
_WORKER_ENV_KEYS = {"NODE_SECRET", "RENDER_URL", "IS_WORKER", "PORT"}

# import-name → pip-package, used by auto-heal when a module is missing.
IMPORT_MAP = {
    "telegram":"python-telegram-bot","telebot":"pyTelegramBotAPI","aiogram":"aiogram",
    "pyrogram":"pyrogram","tgcrypto":"tgcrypto","telethon":"telethon",
    "requests":"requests","aiohttp":"aiohttp","httpx":"httpx","bs4":"beautifulsoup4",
    "lxml":"lxml","PIL":"Pillow","cv2":"opencv-python","numpy":"numpy","pandas":"pandas",
    "flask":"flask","fastapi":"fastapi","uvicorn":"uvicorn","dotenv":"python-dotenv",
    "yaml":"PyYAML","pydantic":"pydantic","openai":"openai","anthropic":"anthropic",
    "discord":"discord.py","yt_dlp":"yt-dlp","redis":"redis","pymongo":"pymongo",
    "motor":"motor","sqlalchemy":"SQLAlchemy","apscheduler":"APScheduler",
}

RUNNING_BOTS: dict[str, dict] = {}


# ══════════════════════════════════════════════════════════════════════
#  ENV ISOLATION
# ══════════════════════════════════════════════════════════════════════
def _make_child_env(bot_dir: Path) -> dict:
    """Clean env for a child bot: strip worker secrets, load the bot's .env."""
    env = {k: v for k, v in os.environ.items() if k not in _WORKER_ENV_KEYS}
    env_file = bot_dir / ".env"
    if env_file.exists():
        try:
            for raw in env_file.read_text(errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip(); v = v.strip().strip('"').strip("'")
                if k: env[k] = v
        except Exception as e:
            log.warning("Failed reading .env for %s: %s", bot_dir.name, e)
    if "PATH" not in env:
        env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    return env


def _free_port(start=8100, end=9000) -> int:
    for p in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try: s.bind(("", p)); return p
            except OSError: continue
    return start


def _install_reqs(req: Path):
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req), "-q"],
                   check=False, timeout=300)


def _wlog(bot_dir: Path, msg: str):
    try:
        with open(bot_dir / "bot_output.log", "a", encoding="utf-8") as f:
            f.write(f"\n{msg}")
    except Exception: pass


# ══════════════════════════════════════════════════════════════════════
#  AUTO-HEAL  (missing module / conflict / port / flood)
# ══════════════════════════════════════════════════════════════════════
async def try_heal(name: str, bot_dir: Path, tail: str, heal_count: int) -> bool:
    if heal_count >= MAX_HEAL_TRIES:
        return False
    main_py = bot_dir / "main.py"

    m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", tail)
    if m:
        mod = m.group(1).split(".")[0]
        pkg = IMPORT_MAP.get(mod, mod)
        log.info("HEAL [%s]: pip install %s", name, pkg)
        ret = await asyncio.to_thread(
            subprocess.run, [sys.executable, "-m", "pip", "install", pkg, "-q"],
            timeout=120, capture_output=True)
        if ret.returncode == 0:
            _wlog(bot_dir, f"[HEAL] Installed {pkg} — restarting…\n"); return True
        return False

    if "Conflict" in tail and "getUpdates" in tail and main_py.exists():
        code = main_py.read_text(errors="replace")
        if "delete_webhook" not in code:
            patched = re.sub(r"(start_polling|run_polling|start\(\))",
                             r"bot.delete_webhook(drop_pending_updates=True)\n    \1",
                             code, count=1)
            if patched != code:
                main_py.write_text(patched, encoding="utf-8")
                _wlog(bot_dir, "[HEAL] Patched delete_webhook — restarting…\n"); return True
        return False

    if ("Errno 98" in tail or "Address already in use" in tail) and main_py.exists():
        code = main_py.read_text(errors="replace"); fp = _free_port()
        pat = re.sub(r"port\s*=\s*\d+", f"port={fp}", code, flags=re.IGNORECASE)
        pat = re.sub(r"PORT\s*=\s*\d+", f"PORT={fp}", pat)
        if pat != code:
            main_py.write_text(pat, encoding="utf-8")
            _wlog(bot_dir, f"[HEAL] Port → {fp} — restarting…\n"); return True
        return False

    if "429" in tail or "Too Many Requests" in tail or "Flood" in tail:
        _wlog(bot_dir, "[HEAL] Flood control — waiting 30s…\n")
        await asyncio.sleep(30); return True

    return False


# ══════════════════════════════════════════════════════════════════════
#  CHILD-BOT RUNNER
# ══════════════════════════════════════════════════════════════════════
async def run_bot(bot_key: str, bot_dir: Path, owner_id: int):
    log_path = bot_dir / "bot_output.log"
    RUNNING_BOTS[bot_key] = {
        "active": True, "start_time": time.time(), "status": "Starting ⏳",
        "process": None, "restarts": 0, "heal_tries": 0, "owner_id": owner_id,
    }
    fast_crashes = 0

    while RUNNING_BOTS.get(bot_key, {}).get("active"):
        flag = bot_dir / ".restart_flag"
        if flag.exists():
            try: flag.unlink()
            except Exception: pass

        t0 = time.time(); RUNNING_BOTS[bot_key]["status"] = "Running 🟢"
        lf = None
        try:
            lf = open(log_path, "a", encoding="utf-8", errors="replace")
            lf.write(f"\n{'─'*55}\n[START] {bot_key}  ·  {time.strftime('%Y-%m-%d %H:%M:%S')}\n{'─'*55}\n")
            lf.flush()
            proc = subprocess.Popen([sys.executable, "main.py"], cwd=str(bot_dir),
                                    stdout=lf, stderr=lf, env=_make_child_env(bot_dir))
        except Exception as exc:
            if lf:
                try: lf.close()
                except Exception: pass
            RUNNING_BOTS[bot_key].update(status="Launch Error ❌", active=False)
            log.warning("Launch error %s: %s", bot_key, exc); break

        RUNNING_BOTS[bot_key]["process"] = proc

        while proc.poll() is None:
            await asyncio.sleep(1.5)
            if (bot_dir / ".restart_flag").exists():
                try: (bot_dir / ".restart_flag").unlink()
                except Exception: pass
                proc.terminate()
                for _ in range(6):
                    await asyncio.sleep(0.5)
                    if proc.poll() is not None: break
                else:
                    try: proc.kill()
                    except Exception: pass
                if RUNNING_BOTS.get(bot_key, {}).get("active"):
                    RUNNING_BOTS[bot_key]["status"] = "Restarting ⏳"
                    RUNNING_BOTS[bot_key]["start_time"] = time.time()
                fast_crashes = 0
                break

        try: lf.close()
        except Exception: pass

        runtime = time.time() - t0
        RUNNING_BOTS[bot_key]["restarts"] += 1

        if not RUNNING_BOTS.get(bot_key, {}).get("active"):
            RUNNING_BOTS[bot_key]["status"] = "Stopped 🛑"; break

        log.warning("%s exited (runtime=%.1fs)", bot_key, runtime)

        try: tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-150:])
        except Exception: tail = ""

        if runtime < FAST_CRASH_SEC:
            fast_crashes += 1
            heal_count = RUNNING_BOTS[bot_key]["heal_tries"]
            if heal_count < MAX_HEAL_TRIES:
                RUNNING_BOTS[bot_key]["status"] = "Healing 🔧"
                healed = await try_heal(bot_key, bot_dir, tail, heal_count)
                RUNNING_BOTS[bot_key]["heal_tries"] += 1
                if healed:
                    fast_crashes = 0
                    RUNNING_BOTS[bot_key]["status"] = "Restarting ⏳"
                    await asyncio.sleep(3); continue
            if fast_crashes >= MAX_FAST_CRASHES:
                RUNNING_BOTS[bot_key].update(active=False, status="Error ❌"); break
        else:
            fast_crashes = 0; RUNNING_BOTS[bot_key]["heal_tries"] = 0

        RUNNING_BOTS[bot_key]["status"] = "Restarting ⏳"
        await asyncio.sleep(5)


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


# ══════════════════════════════════════════════════════════════════════
#  HTTP API  (master ↔ worker)
# ══════════════════════════════════════════════════════════════════════
def _check_secret(req: web.Request) -> bool:
    return bool(NODE_SECRET) and req.headers.get("X-Node-Secret", "") == NODE_SECRET


async def health(req): return web.Response(text="OK")


async def root(req):
    # No bot data exposed publicly.
    return web.Response(content_type="text/html",
        text="<html><body style='font-family:sans-serif;background:#0d1117;"
             "color:#8b949e;text-align:center;padding:40px'>"
             "<h2 style='color:#58a6ff'>💎 Codian Worker Node</h2>"
             "<p>Online. This is a private worker for the Master Hosting Bot.</p>"
             "</body></html>")


async def worker_status(req):
    if not _check_secret(req):
        return web.json_response({"error": "forbidden"}, status=403)
    return web.json_response({
        "online": True,
        "bot_count": sum(1 for e in RUNNING_BOTS.values() if e.get("active")),
        "render_url": RENDER_URL,
        "is_worker": True,
        "uptime": time.time(),
    })


async def worker_deploy(req):
    if not _check_secret(req):
        return web.json_response({"error": "forbidden"}, status=403)
    try:
        reader = await req.multipart()
        bot_key = None; owner_id = 0; zip_data = b""
        async for part in reader:
            if part.name == "bot_key":    bot_key  = (await part.read()).decode()
            elif part.name == "owner_id": owner_id = int((await part.read()).decode() or 0)
            elif part.name == "bot_zip":  zip_data = await part.read()
        if not bot_key or not zip_data:
            return web.json_response({"error": "missing data"}, status=400)

        # Sanitise the key so it can never escape hosted_bots/
        safe_key = re.sub(r"[^a-zA-Z0-9_-]", "_", bot_key)[:80]
        bot_dir  = HOSTED_DIR / safe_key
        if bot_dir.exists():
            _do_stop(safe_key)
            shutil.rmtree(bot_dir, ignore_errors=True)
        bot_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for member in zf.namelist():
                # Block path traversal inside the zip
                dest = (bot_dir / member).resolve()
                if bot_dir.resolve() not in dest.parents and dest != bot_dir.resolve():
                    continue
                if member.endswith("/"):
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(member))

        if not (bot_dir / "main.py").exists():
            return web.json_response({"error": "no main.py in bot"}, status=400)

        req_file = bot_dir / "requirements.txt"
        if req_file.exists():
            await asyncio.to_thread(_install_reqs, req_file)

        _save_owner(safe_key, owner_id)
        asyncio.create_task(run_bot(safe_key, bot_dir, owner_id))
        log.info("Deployed + started: %s (owner %s)", safe_key, owner_id)
        return web.json_response({"ok": True, "bot_key": safe_key})
    except Exception as ex:
        log.warning("Deploy failed: %s", ex)
        return web.json_response({"error": str(ex)}, status=500)


# ── lightweight owner registry (so we can restart bots after a reboot) ──
_OWNERS_FILE = HOSTED_DIR / "_worker_owners.json"

def _save_owner(bot_key: str, owner_id: int):
    try:
        data = json.loads(_OWNERS_FILE.read_text()) if _OWNERS_FILE.exists() else {}
    except Exception:
        data = {}
    data[bot_key] = owner_id
    _OWNERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OWNERS_FILE.write_text(json.dumps(data))

def _load_owners() -> dict:
    try: return json.loads(_OWNERS_FILE.read_text())
    except Exception: return {}


# ══════════════════════════════════════════════════════════════════════
#  KEEP-ALIVE + GRADUAL RESTART ON BOOT
# ══════════════════════════════════════════════════════════════════════
async def keep_alive_loop():
    if not RENDER_URL: return
    await asyncio.sleep(90)
    async with aiohttp.ClientSession() as sess:
        while True:
            try:
                async with sess.get(f"{RENDER_URL}/health",
                                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                    log.info("Keep-alive ping → %d", r.status)
            except Exception as e:
                log.warning("Ping: %s", e)
            await asyncio.sleep(840)


async def restart_persisted_bots():
    """After a worker reboot, bring its assigned bots back one-by-one."""
    if not HOSTED_DIR.exists(): return
    owners = _load_owners()
    started = 0
    for item in sorted(HOSTED_DIR.iterdir()):
        if not item.is_dir() or item.name.startswith(("_", ".")): continue
        if not (item / "main.py").exists(): continue
        owner = owners.get(item.name, 0)
        asyncio.create_task(run_bot(item.name, item, owner))
        started += 1
        await asyncio.sleep(STARTUP_DELAY_SEC)   # gradual — no RAM spike
    if started:
        log.info("Restarted %d persisted bot(s) gradually", started)


async def main():
    log.info("════════════════════════════════════════════════════")
    log.info("   Codian Worker Node  —  ready to receive bots       ")
    log.info("════════════════════════════════════════════════════")
    HOSTED_DIR.mkdir(parents=True, exist_ok=True)

    app = web.Application(client_max_size=512 * 1024 * 1024)  # 512MB uploads
    app.router.add_get("/",               root)
    app.router.add_get("/health",         health)
    app.router.add_get("/worker/status",  worker_status)
    app.router.add_post("/worker/deploy", worker_deploy)

    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Worker API on port %d  ·  keep-alive=%s", PORT, bool(RENDER_URL))

    asyncio.create_task(keep_alive_loop())
    asyncio.create_task(restart_persisted_bots())

    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        import uvloop
        uvloop.install()
        log.info("⚡ uvloop active — faster event loop")
    except ImportError:
        log.info("uvloop not installed — using default asyncio loop")
    try: asyncio.run(main())
    except KeyboardInterrupt: log.info("Worker stopped.")
