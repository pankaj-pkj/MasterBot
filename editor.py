"""
╔══════════════════════════════════════════════════════════════════════╗
║   MASTER HOSTING BOT  —  Web IDE  v2.0                              ║
║   editor.py  ·  Codian Studio 💎                                    ║
╠══════════════════════════════════════════════════════════════════════╣
║  Security:                                                           ║
║  • Auto-generated strong random token (UUID4) stored in _data/      ║
║  • No default "codianstudio" — unique per deployment                 ║
║  • New device/IP alert → Telegram DM to admin with Block/Accept      ║
║  • Session fingerprinting (IP + UA hash)                             ║
║  • IP blocklist                                                      ║
║  UX Fixes:                                                           ║
║  • Monaco zoom via Ctrl+Scroll (no browser page zoom on click)       ║
║  • Sidebar fully collapsible (button + shortcut B)                   ║
║  • Log panel collapsible (button + shortcut L)                       ║
║  • Run button: STOP old process first → wait → start new             ║
║  • Full-screen editor mode (shortcut F)                              ║
║  • Drag-and-drop anywhere on page                                    ║
║  • Mobile touch-action fixed                                         ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os
import sys
import json
import time
import uuid
import shutil
import hashlib
import asyncio
import logging
import mimetypes
import subprocess
from pathlib import Path
from typing  import Optional

from aiohttp import web
import aiohttp

log = logging.getLogger("WebIDE")

# ──────────────────────────────────────────────────────────────────────
#  Shared state (injected by main.py)
# ──────────────────────────────────────────────────────────────────────
HOSTED_DIR    = Path("hosted_bots")
DATA_DIR      = HOSTED_DIR / "_data"
DELETED_DIR   = HOSTED_DIR / "_deleted"
BLOCKED_NAMES = {"_data", "_deleted", ".git", "__pycache__"}

_RUNNING_BOTS: dict = {}
_APP_REF             = None      # PTB Application — set by main.py
_ADMIN_IDS: set[int] = set()


def init_editor(running_bots: dict, app_ref=None, admin_ids: set = None) -> None:
    global _RUNNING_BOTS, _APP_REF, _ADMIN_IDS
    _RUNNING_BOTS = running_bots
    _APP_REF      = app_ref
    _ADMIN_IDS    = admin_ids or set()


# ──────────────────────────────────────────────────────────────────────
#  Token management  (auto-generated, stored on disk)
# ──────────────────────────────────────────────────────────────────────
_TOKEN_FILE = DATA_DIR / "editor_token.txt"


def get_token() -> str:
    """Return the current IDE token, generating one if it doesn't exist."""
    try:
        t = _TOKEN_FILE.read_text().strip()
        if len(t) >= 16:
            return t
    except Exception:
        pass
    return rotate_token()


def rotate_token() -> str:
    """Generate a new strong random token and persist it."""
    new_token = uuid.uuid4().hex + uuid.uuid4().hex[:8]   # 40 chars
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(new_token)
    log.info("IDE token rotated.")
    return new_token


# ──────────────────────────────────────────────────────────────────────
#  Session / device fingerprinting
# ──────────────────────────────────────────────────────────────────────
_SESSIONS_FILE     = DATA_DIR / "ide_sessions.json"
_BLOCKED_IPS_FILE  = DATA_DIR / "ide_blocked_ips.json"
# In-memory pending alerts: fingerprint → asyncio.Event (waiting for admin decision)
_PENDING: dict[str, str] = {}    # fingerprint → "allow" | "block" | "" (pending)


def _load_json_file(path: Path, default):
    try:    return json.loads(path.read_text())
    except: return default


def _save_json_file(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _fingerprint(req: web.Request) -> str:
    ip = req.headers.get("X-Forwarded-For", req.remote or "").split(",")[0].strip()
    ua = req.headers.get("User-Agent", "")
    raw = f"{ip}|{ua}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _get_ip(req: web.Request) -> str:
    return req.headers.get("X-Forwarded-For", req.remote or "?").split(",")[0].strip()


def _is_ip_blocked(ip: str) -> bool:
    blocked = _load_json_file(_BLOCKED_IPS_FILE, [])
    return ip in blocked


def _block_ip(ip: str):
    blocked = _load_json_file(_BLOCKED_IPS_FILE, [])
    if ip not in blocked:
        blocked.append(ip)
    _save_json_file(_BLOCKED_IPS_FILE, blocked)


def _unblock_ip(ip: str):
    blocked = _load_json_file(_BLOCKED_IPS_FILE, [])
    blocked = [b for b in blocked if b != ip]
    _save_json_file(_BLOCKED_IPS_FILE, blocked)


def _register_session(fp: str, ip: str, ua: str) -> bool:
    """Return True if this is a NEW (unseen) fingerprint."""
    sessions = _load_json_file(_SESSIONS_FILE, {})
    is_new   = fp not in sessions
    sessions[fp] = {"ip": ip, "ua": ua[:120], "last_seen": time.time()}
    _save_json_file(_SESSIONS_FILE, sessions)
    return is_new


async def _alert_admins_new_device(fp: str, ip: str, ua: str):
    """Send a Telegram alert to all admins about a new IDE login device."""
    if not _APP_REF or not _ADMIN_IDS:
        return
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    _PENDING[fp] = ""    # pending decision
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Allow",    callback_data=f"ide_allow|{fp}|{ip}"),
        InlineKeyboardButton("🚫 Block IP", callback_data=f"ide_block|{fp}|{ip}"),
    ]])
    msg = (
        f"🔔 *New Device — Web IDE Login*\n\n"
        f"🌐 IP      : `{ip}`\n"
        f"🖥 Browser : `{ua[:80]}`\n"
        f"🔑 Session : `{fp}`\n\n"
        f"Is this you? If not, block immediately."
    )
    for admin_id in _ADMIN_IDS:
        try:
            await _APP_REF.bot.send_message(admin_id, msg,
                                            parse_mode="Markdown", reply_markup=kb)
        except Exception as exc:
            log.warning("Could not alert admin %d: %s", admin_id, exc)


# ──────────────────────────────────────────────────────────────────────
#  Auth
# ──────────────────────────────────────────────────────────────────────
def _authed(req: web.Request) -> bool:
    token    = get_token()
    provided = (req.rel_url.query.get("token")
                or req.cookies.get("ide_token", ""))
    return provided == token


def _check_blocked(req: web.Request) -> bool:
    return _is_ip_blocked(_get_ip(req))


# ──────────────────────────────────────────────────────────────────────
#  Utility
# ──────────────────────────────────────────────────────────────────────
def _fmt_uptime(s: float) -> str:
    s = int(max(0, s)); h, r = divmod(s, 3600); m, sc = divmod(r, 60)
    return f"{h}h {m}m {sc}s"


def _tail(path: Path, n: int = 60) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return ""


def _safe_path(raw: str) -> Optional[Path]:
    try:
        full = (HOSTED_DIR / raw).resolve()
        base = HOSTED_DIR.resolve()
        if full == base or base in full.parents:
            return full
    except Exception:
        pass
    return None


def _build_tree(root: Path, rel: str = "") -> list:
    items = []
    try:
        entries = sorted(root.iterdir(),
                         key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        return items
    for entry in entries:
        if entry.name.startswith(".") or entry.name == "__pycache__":
            continue
        node = {
            "name": entry.name,
            "path": (rel + "/" + entry.name).lstrip("/"),
            "type": "dir" if entry.is_dir() else "file",
            "ext":  entry.suffix.lstrip(".").lower() if entry.is_file() else "",
            "size": entry.stat().st_size if entry.is_file() else 0,
        }
        if entry.is_dir():
            node["children"] = _build_tree(entry, node["path"])
        items.append(node)
    return items


def _bot_tree() -> list:
    if not HOSTED_DIR.exists():
        return []
    bots = []
    for item in sorted(HOSTED_DIR.iterdir()):
        if not item.is_dir() or item.name in BLOCKED_NAMES:
            continue
        e = _RUNNING_BOTS.get(item.name, {})
        bots.append({
            "name":     item.name,
            "path":     item.name,
            "type":     "bot",
            "status":   e.get("status", "Offline 🔴"),
            "owner":    e.get("owner_id", "?"),
            "restarts": e.get("restarts", 0),
            "uptime":   _fmt_uptime(time.time() - e["start_time"]) if e.get("start_time") else "—",
            "children": _build_tree(item, item.name),
        })
    return bots


# ──────────────────────────────────────────────────────────────────────
#  API  — tree
# ──────────────────────────────────────────────────────────────────────
async def api_tree(req: web.Request) -> web.Response:
    if _check_blocked(req):
        return web.json_response({"error": "blocked"}, status=403)
    if not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(_bot_tree())


# ──────────────────────────────────────────────────────────────────────
#  API  — read file
# ──────────────────────────────────────────────────────────────────────
async def api_read_file(req: web.Request) -> web.Response:
    if _check_blocked(req) or not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    raw  = req.rel_url.query.get("path", "")
    path = _safe_path(raw)
    if not path or not path.exists() or not path.is_file():
        return web.json_response({"error": "File not found"}, status=404)
    try:
        content = path.read_text(errors="replace")[:524288]
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)
    return web.json_response({
        "path": raw, "name": path.name, "content": content,
        "mime": mimetypes.guess_type(str(path))[0] or "text/plain",
        "size": path.stat().st_size,
    })


# ──────────────────────────────────────────────────────────────────────
#  API  — write file
# ──────────────────────────────────────────────────────────────────────
async def api_write_file(req: web.Request) -> web.Response:
    if _check_blocked(req) or not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body    = await req.json()
        path    = _safe_path(body.get("path", ""))
        content = body.get("content", "")
        if not path:
            return web.json_response({"error": "Invalid path"}, status=400)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return web.json_response({"ok": True, "size": len(content.encode())})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


# ──────────────────────────────────────────────────────────────────────
#  API  — delete
# ──────────────────────────────────────────────────────────────────────
async def api_delete(req: web.Request) -> web.Response:
    if _check_blocked(req) or not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await req.json()
        path = _safe_path(body.get("path", ""))
        if not path or not path.exists():
            return web.json_response({"error": "Not found"}, status=404)
        if path.parent.resolve() == HOSTED_DIR.resolve() and path.is_dir():
            return web.json_response(
                {"error": "Use Delete Bot button to remove an entire bot."}, status=400)
        shutil.rmtree(path) if path.is_dir() else path.unlink()
        return web.json_response({"ok": True})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


# ──────────────────────────────────────────────────────────────────────
#  API  — mkdir
# ──────────────────────────────────────────────────────────────────────
async def api_mkdir(req: web.Request) -> web.Response:
    if _check_blocked(req) or not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await req.json()
        path = _safe_path(body.get("path", ""))
        if not path:
            return web.json_response({"error": "Invalid path"}, status=400)
        path.mkdir(parents=True, exist_ok=True)
        return web.json_response({"ok": True})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


# ──────────────────────────────────────────────────────────────────────
#  API  — upload (multipart)
# ──────────────────────────────────────────────────────────────────────
async def api_upload(req: web.Request) -> web.Response:
    if _check_blocked(req) or not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        reader   = await req.multipart()
        dest_raw = ""
        saved    = []
        async for part in reader:
            if part.name == "dir":
                dest_raw = (await part.read()).decode().strip()
            elif part.filename:
                dest_dir = _safe_path(dest_raw) if dest_raw else HOSTED_DIR
                if not dest_dir:
                    continue
                dest_dir.mkdir(parents=True, exist_ok=True)
                fname = Path(part.filename).name
                out   = dest_dir / fname
                out.write_bytes(await part.read())
                saved.append(out.relative_to(HOSTED_DIR).as_posix())
        return web.json_response({"ok": True, "saved": saved})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


# ──────────────────────────────────────────────────────────────────────
#  API  — new bot scaffold
# ──────────────────────────────────────────────────────────────────────
async def api_newbot(req: web.Request) -> web.Response:
    if _check_blocked(req) or not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await req.json()
        name = body.get("name", "").strip().replace(" ", "_").replace("/", "")
        if not name:
            return web.json_response({"error": "Name required"}, status=400)
        bot_dir = HOSTED_DIR / name
        if bot_dir.exists():
            return web.json_response({"error": f"'{name}' already exists"}, status=409)
        bot_dir.mkdir(parents=True)
        (bot_dir / "main.py").write_text(
            f'# {name}  —  Codian Studio 💎\n\nprint("Hello from {name}!")\n',
            encoding="utf-8")
        (bot_dir / "requirements.txt").write_text(
            "# Add dependencies here\n# python-telegram-bot==20.8\n",
            encoding="utf-8")
        return web.json_response({"ok": True, "name": name, "main": f"{name}/main.py"})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


# ──────────────────────────────────────────────────────────────────────
#  API  — run  (STOP first, then start)
# ──────────────────────────────────────────────────────────────────────
async def api_run(req: web.Request) -> web.Response:
    if _check_blocked(req) or not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body     = await req.json()
        bot_name = body.get("bot", "").strip()
        if not bot_name:
            return web.json_response({"error": "Bot name required"}, status=400)
        bot_dir = HOSTED_DIR / bot_name
        if not (bot_dir / "main.py").exists():
            return web.json_response({"error": "main.py not found"}, status=404)

        # Step 1: Stop existing process cleanly
        e = _RUNNING_BOTS.get(bot_name)
        if e:
            e["active"] = False
            p = e.get("process")
            if p and p.poll() is None:
                p.terminate()
                try:
                    await asyncio.get_event_loop().run_in_executor(None, p.wait, 5)
                except Exception:
                    try: p.kill()
                    except Exception: pass
            e["status"] = "Stopped 🛑"

        # Step 2: Write restart flag (run_bot loop picks this up)
        (bot_dir / ".restart_flag").write_text(str(time.time()))
        return web.json_response({"ok": True, "bot": bot_name, "msg": "Stopping → restarting…"})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


# ──────────────────────────────────────────────────────────────────────
#  API  — logs (JSON, last N lines)
# ──────────────────────────────────────────────────────────────────────
async def api_logs(req: web.Request) -> web.Response:
    if _check_blocked(req) or not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    bot_name = req.rel_url.query.get("bot", "")
    n        = int(req.rel_url.query.get("n", 100))
    log_path = HOSTED_DIR / bot_name / "bot_output.log"
    try:
        lines = log_path.read_text(errors="replace").splitlines()[-n:]
    except Exception:
        lines = []
    e = _RUNNING_BOTS.get(bot_name, {})
    return web.json_response({
        "bot": bot_name, "lines": lines,
        "status":   e.get("status", "Offline"),
        "restarts": e.get("restarts", 0),
        "uptime":   _fmt_uptime(time.time() - e["start_time"]) if e.get("start_time") else "—",
    })


# ──────────────────────────────────────────────────────────────────────
#  WebSocket  — real-time log tail
# ──────────────────────────────────────────────────────────────────────
async def ws_logs(req: web.Request) -> web.WebSocketResponse:
    token    = req.rel_url.query.get("token", "")
    bot_name = req.rel_url.query.get("bot",   "")
    if token != get_token():
        raise web.HTTPUnauthorized()

    ws       = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(req)
    log_path = HOSTED_DIR / bot_name / "bot_output.log"
    pos      = 0

    # Send recent history
    try:
        text  = log_path.read_text(errors="replace")
        lines = text.splitlines()[-60:]
        for ln in lines:
            await ws.send_str(ln)
        pos = log_path.stat().st_size
    except Exception:
        pass

    try:
        while not ws.closed:
            await asyncio.sleep(1)
            try:
                size = log_path.stat().st_size
                if size > pos:
                    with open(log_path, "r", errors="replace") as f:
                        f.seek(pos); new_text = f.read()
                    pos = size
                    for ln in new_text.splitlines():
                        if ln:
                            await ws.send_str(ln)
            except Exception:
                pass
    except asyncio.CancelledError:
        pass
    finally:
        await ws.close()
    return ws


# ──────────────────────────────────────────────────────────────────────
#  Editor page  (auth + session fingerprinting)
# ──────────────────────────────────────────────────────────────────────
async def editor_page(req: web.Request) -> web.Response:
    if _check_blocked(req):
        return web.Response(text="403 Forbidden — IP blocked", status=403)

    token    = get_token()
    provided = req.rel_url.query.get("token", "")

    # Token from URL → set cookie and redirect cleanly (hides token from URL bar)
    if provided == token:
        resp = web.HTTPFound(location="/editor")
        resp.set_cookie("ide_token", token, max_age=86400 * 30, httponly=True, samesite="Strict")
        return resp

    if not _authed(req):
        return web.Response(text=_LOGIN_HTML, content_type="text/html", status=401)

    # Session fingerprinting
    fp = _fingerprint(req)
    ip = _get_ip(req)
    ua = req.headers.get("User-Agent", "")
    is_new = _register_session(fp, ip, ua)
    if is_new:
        asyncio.create_task(_alert_admins_new_device(fp, ip, ua))

    return web.Response(text=_IDE_HTML, content_type="text/html")


def register_routes(app: web.Application) -> None:
    app.router.add_get ("/editor",            editor_page)
    app.router.add_get ("/editor/api/tree",   api_tree)
    app.router.add_get ("/editor/api/file",   api_read_file)
    app.router.add_post("/editor/api/file",   api_write_file)
    app.router.add_post("/editor/api/delete", api_delete)
    app.router.add_post("/editor/api/mkdir",  api_mkdir)
    app.router.add_post("/editor/api/upload", api_upload)
    app.router.add_post("/editor/api/newbot", api_newbot)
    app.router.add_post("/editor/api/run",    api_run)
    app.router.add_get ("/editor/api/logs",   api_logs)
    app.router.add_get ("/editor/ws",         ws_logs)
    log.info("Web IDE routes registered at /editor")


# ══════════════════════════════════════════════════════════════════════
#  LOGIN PAGE
# ══════════════════════════════════════════════════════════════════════
_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Codian Studio — Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#c9d1d9;font-family:'Segoe UI',sans-serif;
     display:flex;align-items:center;justify-content:center;height:100vh}
.card{background:#161b22;border:1px solid #30363d;border-radius:12px;
      padding:40px;width:360px;text-align:center;box-shadow:0 8px 32px #0008}
.logo{font-size:2.8rem;margin-bottom:8px}
h1{color:#58a6ff;font-size:1.15rem;margin-bottom:4px}
p{color:#8b949e;font-size:.83rem;margin-bottom:24px}
input{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;
      padding:11px 14px;color:#c9d1d9;font-size:1rem;outline:none;margin-bottom:14px;
      font-family:monospace}
input:focus{border-color:#58a6ff}
button{width:100%;background:#238636;border:none;border-radius:6px;
       padding:12px;color:#fff;font-size:1rem;font-weight:600;cursor:pointer}
button:hover{background:#2ea043}
.hint{color:#8b949e;font-size:.75rem;margin-top:12px}
</style>
</head><body>
<div class="card">
  <div class="logo">💎</div>
  <h1>Codian Studio</h1>
  <p>Master Hosting Bot — Web IDE</p>
  <input type="password" id="tok" placeholder="Enter access token"
         onkeydown="if(event.key==='Enter')go()">
  <button onclick="go()">🔐 Login</button>
  <p class="hint">Get your token from the bot: /ide</p>
</div>
<script>
function go(){
  const t=document.getElementById('tok').value.trim();
  if(!t)return;
  window.location.href='/editor?token='+encodeURIComponent(t);
}
</script>
</body></html>"""


# ══════════════════════════════════════════════════════════════════════
#  FULL IDE HTML
# ══════════════════════════════════════════════════════════════════════
_IDE_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Codian Studio 💎</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg0:#0d1117;--bg1:#161b22;--bg2:#1c2128;--bg3:#21262d;
  --border:#30363d;--accent:#58a6ff;--green:#3fb950;
  --red:#f85149;--yellow:#d29922;--purple:#bc8cff;--orange:#ffa657;
  --text:#c9d1d9;--text2:#8b949e;--text3:#6e7681;
  --sidebar-w:240px;--topbar-h:44px;--log-h:180px;
}
html,body{height:100%;overflow:hidden;background:var(--bg0);color:var(--text);
          font-family:'Segoe UI',system-ui,sans-serif;font-size:13px}

/* ── Layout ── */
#root{display:flex;flex-direction:column;height:100vh}
#topbar{height:var(--topbar-h);flex-shrink:0;background:var(--bg1);
        border-bottom:1px solid var(--border);display:flex;align-items:center;
        gap:6px;padding:0 10px;user-select:none;overflow-x:auto}
#topbar::-webkit-scrollbar{height:0}
#workspace{display:flex;flex:1;overflow:hidden}
#sidebar{width:var(--sidebar-w);flex-shrink:0;background:var(--bg1);
         border-right:1px solid var(--border);display:flex;flex-direction:column;
         overflow:hidden;transition:width .2s}
#sidebar.collapsed{width:0}
#main-area{flex:1;display:flex;flex-direction:column;overflow:hidden}
#tabs-bar{height:36px;flex-shrink:0;background:var(--bg1);
          border-bottom:1px solid var(--border);display:flex;overflow-x:auto}
#tabs-bar::-webkit-scrollbar{height:3px}
#tabs-bar::-webkit-scrollbar-thumb{background:var(--border)}
#editor-wrap{flex:1;position:relative;overflow:hidden}
#monaco{width:100%;height:100%;touch-action:none}
#placeholder{position:absolute;inset:0;display:flex;flex-direction:column;
             align-items:center;justify-content:center;color:var(--text3);gap:8px;
             pointer-events:none}
#placeholder .big{font-size:3rem}
#log-panel{height:var(--log-h);flex-shrink:0;background:var(--bg1);
           border-top:1px solid var(--border);display:flex;flex-direction:column;
           transition:height .2s}
#log-panel.collapsed{height:0}

/* ── Topbar buttons ── */
.tbtn{display:flex;align-items:center;gap:4px;background:var(--bg3);
      border:1px solid var(--border);border-radius:5px;padding:4px 9px;
      cursor:pointer;font-size:12px;color:var(--text);white-space:nowrap;
      flex-shrink:0;transition:.15s;user-select:none}
.tbtn:hover{border-color:var(--accent);color:var(--accent)}
.tbtn.run{border-color:var(--green);color:var(--green);background:#0f2a15}
.tbtn.run:hover{background:var(--green);color:#fff}
.tbtn.run.loading{border-color:var(--yellow);color:var(--yellow);background:#2a220f;pointer-events:none}
.tbtn.danger{border-color:var(--red);color:var(--red);background:#2a0f0f}
.tbtn.danger:hover{background:var(--red);color:#fff}
.tbtn.tog{border-color:var(--border)}
.tbtn.tog.active{border-color:var(--accent);color:var(--accent)}
.sep{width:1px;height:20px;background:var(--border);flex-shrink:0;margin:0 2px}
#cur-bot-label{color:var(--purple);font-weight:700;font-size:12px;
               flex-shrink:0;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#status-badge{padding:2px 8px;border-radius:10px;font-size:11px;
              background:var(--bg3);border:1px solid var(--border);flex-shrink:0}
.spacer{flex:1}

/* ── Sidebar ── */
#sb-head{display:flex;align-items:center;gap:5px;padding:6px 8px;
         border-bottom:1px solid var(--border);flex-shrink:0}
#sb-head .title{flex:1;font-size:10px;font-weight:700;color:var(--text2);
                text-transform:uppercase;letter-spacing:.6px;white-space:nowrap}
.iBtn{background:none;border:none;color:var(--text2);cursor:pointer;
      padding:2px 5px;border-radius:3px;font-size:14px;line-height:1}
.iBtn:hover{background:var(--bg3);color:var(--text)}
#tree-wrap{flex:1;overflow-y:auto;padding:2px 0}
#tree-wrap::-webkit-scrollbar{width:3px}
#tree-wrap::-webkit-scrollbar-thumb{background:var(--border)}

/* ── Tree nodes ── */
.bot-hdr{display:flex;align-items:center;gap:5px;padding:5px 8px;cursor:pointer;
         border-radius:4px;transition:.1s;border-left:2px solid transparent}
.bot-hdr:hover{background:var(--bg3)}
.bot-hdr.sel{background:#162030;border-left-color:var(--accent)}
.caret{font-size:9px;color:var(--text3);transition:transform .18s;flex-shrink:0}
.caret.open{transform:rotate(90deg)}
.bot-name-lbl{flex:1;font-weight:600;font-size:12px;overflow:hidden;
              text-overflow:ellipsis;white-space:nowrap}
.bst{font-size:9px;padding:1px 5px;border-radius:8px;flex-shrink:0;white-space:nowrap}
.bst-run{background:#0f2a15;color:var(--green)}
.bst-stop{background:#2a0f0f;color:var(--red)}
.bst-other{background:var(--bg3);color:var(--text2)}
.bot-children{display:none;padding-left:12px}
.bot-children.open{display:block}
.f-item{display:flex;align-items:center;gap:5px;padding:3px 8px;cursor:pointer;
        border-radius:3px;transition:.1s;user-select:none}
.f-item:hover{background:var(--bg3)}
.f-item.sel{background:#162030;color:var(--accent)}
.f-dir{color:var(--yellow)}
.f-sub{display:none;padding-left:14px}
.f-sub.open{display:block}
.f-icon{font-size:12px;flex-shrink:0}
.f-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}

/* ── Tabs ── */
.tab{display:flex;align-items:center;gap:5px;padding:0 12px;
     cursor:pointer;border-right:1px solid var(--border);white-space:nowrap;
     font-size:12px;color:var(--text2);flex-shrink:0;position:relative;
     transition:.1s;height:100%}
.tab:hover{color:var(--text);background:var(--bg3)}
.tab.act{background:var(--bg0);color:var(--text);box-shadow:inset 0 -2px 0 var(--accent)}
.tab-x{opacity:.4;font-size:13px;border-radius:3px;padding:0 2px;line-height:1.2}
.tab-x:hover{opacity:1;background:var(--red);color:#fff}
.tab.dirty::after{content:'●';color:var(--yellow);font-size:9px;margin-left:2px}

/* ── Log panel ── */
#log-head{display:flex;align-items:center;gap:6px;padding:4px 10px;
          border-bottom:1px solid var(--border);flex-shrink:0}
#log-head .lbl{font-size:10px;font-weight:700;text-transform:uppercase;
               letter-spacing:.5px;color:var(--text2);flex:1}
#log-body{flex:1;overflow-y:auto;padding:5px 10px;
          font-family:'Cascadia Code','Fira Code',Consolas,monospace;
          font-size:11.5px;line-height:1.65;color:#abb2bf}
#log-body::-webkit-scrollbar{width:3px}
#log-body::-webkit-scrollbar-thumb{background:var(--border)}
.ll{white-space:pre-wrap;word-break:break-all}
.ll-err{color:var(--red)}.ll-warn{color:var(--yellow)}.ll-ok{color:var(--green)}

/* ── Modals ── */
.mb{display:none;position:fixed;inset:0;background:#000b;
    align-items:center;justify-content:center;z-index:999}
.mb.show{display:flex}
.mc{background:var(--bg1);border:1px solid var(--border);border-radius:10px;
    padding:22px;min-width:310px;max-width:440px;box-shadow:0 8px 24px #0009}
.mc h3{margin-bottom:14px;color:var(--accent);font-size:.95rem}
.mc input,.mc select{width:100%;background:var(--bg0);border:1px solid var(--border);
  border-radius:5px;padding:8px 11px;color:var(--text);font-size:13px;
  margin-bottom:11px;outline:none}
.mc input:focus{border-color:var(--accent)}
.mbtns{display:flex;gap:7px;justify-content:flex-end;margin-top:4px}
.mok{padding:7px 15px;border-radius:5px;border:none;background:var(--accent);
     color:#fff;cursor:pointer;font-size:13px}
.mok:hover{opacity:.85}
.mcancel{padding:7px 15px;border-radius:5px;border:1px solid var(--border);
         background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px}

/* ── Toast ── */
#toast{position:fixed;bottom:18px;right:18px;background:var(--bg3);
       border:1px solid var(--border);border-radius:8px;padding:9px 15px;
       font-size:13px;opacity:0;pointer-events:none;z-index:9999;
       max-width:300px;transition:opacity .25s}
#toast.show{opacity:1}
#toast.ok{border-color:var(--green);color:var(--green)}
#toast.err{border-color:var(--red);color:var(--red)}
#toast.info{border-color:var(--accent);color:var(--accent)}

/* ── Drop overlay ── */
#drop-overlay{display:none;position:fixed;inset:0;background:#58a6ff18;
              border:3px dashed var(--accent);z-index:888;pointer-events:none;
              align-items:center;justify-content:center;font-size:1.3rem;color:var(--accent)}
#drop-overlay.show{display:flex}
#file-inp{display:none}

/* ── Zoom buttons ── */
#zoom-bar{display:flex;gap:3px;align-items:center;flex-shrink:0}
#font-size-lbl{font-size:11px;color:var(--text2);min-width:28px;text-align:center}
</style>
</head><body>

<input type="file" id="file-inp" multiple>
<div id="drop-overlay">📁 Drop to upload</div>
<div id="toast"></div>

<!-- Modals -->
<div class="mb" id="m-newbot">
  <div class="mc"><h3>🤖 New Bot</h3>
    <input id="nb-name" placeholder="bot-name (letters, numbers, _)">
    <div class="mbtns">
      <button class="mcancel" onclick="closeM('m-newbot')">Cancel</button>
      <button class="mok" onclick="doNewBot()">Create</button>
    </div></div></div>

<div class="mb" id="m-newfile">
  <div class="mc"><h3>📄 New File</h3>
    <input id="nf-name" placeholder="filename.py">
    <div class="mbtns">
      <button class="mcancel" onclick="closeM('m-newfile')">Cancel</button>
      <button class="mok" onclick="doNewFile()">Create</button>
    </div></div></div>

<div class="mb" id="m-newdir">
  <div class="mc"><h3>📁 New Folder</h3>
    <input id="nd-name" placeholder="folder-name">
    <div class="mbtns">
      <button class="mcancel" onclick="closeM('m-newdir')">Cancel</button>
      <button class="mok" onclick="doNewDir()">Create</button>
    </div></div></div>

<div class="mb" id="m-upload">
  <div class="mc"><h3>⬆️ Upload Files</h3>
    <p style="color:var(--text2);font-size:12px;margin-bottom:12px">
      Destination: <b id="ul-dest">/</b></p>
    <button class="mok" style="width:100%;margin-bottom:10px"
      onclick="document.getElementById('file-inp').click()">📂 Choose Files</button>
    <div id="ul-drop" style="border:2px dashed var(--border);border-radius:7px;
         padding:18px;text-align:center;color:var(--text3);font-size:12px">
      or drag & drop here</div>
    <div class="mbtns" style="margin-top:10px">
      <button class="mcancel" onclick="closeM('m-upload')">Close</button>
    </div></div></div>

<!-- ROOT -->
<div id="root">

  <!-- TOPBAR -->
  <div id="topbar">
    <button class="tbtn tog" id="btn-sb" onclick="toggleSidebar()" title="Toggle sidebar [B]">☰</button>
    <div class="sep"></div>
    <button class="tbtn" onclick="openM('m-newbot')">＋ Bot</button>
    <button class="tbtn" onclick="showUpload()">⬆ Upload</button>
    <button class="tbtn" id="btn-nf" style="display:none" onclick="openM('m-newfile')">📄 File</button>
    <button class="tbtn" id="btn-nd" style="display:none" onclick="openM('m-newdir')">📁 Dir</button>
    <div class="sep"></div>
    <div id="cur-bot-label">No bot selected</div>
    <div id="status-badge">—</div>
    <div class="spacer"></div>
    <div id="zoom-bar">
      <button class="tbtn" onclick="zoomEditor(-1)" title="Decrease font size">A−</button>
      <span id="font-size-lbl">14</span>
      <button class="tbtn" onclick="zoomEditor(+1)" title="Increase font size">A+</button>
    </div>
    <div class="sep"></div>
    <button class="tbtn" onclick="saveFile()" title="Ctrl+S">💾 Save</button>
    <button class="tbtn run" id="btn-run" onclick="runBot()">▶ Run</button>
    <div class="sep"></div>
    <button class="tbtn" id="btn-log" onclick="toggleLog()" title="Toggle logs [L]">📋 Log</button>
    <button class="tbtn danger" onclick="confirmDel()">🗑 Del</button>
  </div>

  <!-- WORKSPACE -->
  <div id="workspace">

    <!-- SIDEBAR -->
    <div id="sidebar">
      <div id="sb-head">
        <span class="title">Explorer</span>
        <button class="iBtn" onclick="refreshTree()" title="Refresh">⟳</button>
      </div>
      <div id="tree-wrap">
        <div style="padding:18px;text-align:center;color:var(--text3)">Loading…</div>
      </div>
    </div>

    <!-- MAIN -->
    <div id="main-area">
      <div id="tabs-bar"></div>
      <div id="editor-wrap">
        <div id="monaco"></div>
        <div id="placeholder">
          <div class="big">💎</div>
          <p>Select a file to start editing</p>
          <p style="font-size:11px">Ctrl+S save · B sidebar · L logs · F fullscreen</p>
        </div>
      </div>
      <div id="log-panel">
        <div id="log-head">
          <span class="lbl">Terminal / Bot Logs</span>
          <button class="iBtn" onclick="clearLog()">✕ Clear</button>
          <button class="iBtn" onclick="logBottom()">⬇</button>
        </div>
        <div id="log-body"></div>
      </div>
    </div>

  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.46.0/min/vs/loader.min.js"></script>
<script>
'use strict';
// ── Cookie token ────────────────────────────────────────────────────
const TOKEN = document.cookie.match(/ide_token=([^;]+)/)?.[1] || '';
const API   = async (url, opts={}) => {
  const sep = url.includes('?') ? '&' : '?';
  const r   = await fetch(url + sep + 'token=' + TOKEN, opts);
  return r.json();
};
const WS = (location.protocol==='https:'?'wss://':'ws://')+location.host;

// ── State ────────────────────────────────────────────────────────────
let ed         = null;    // Monaco editor instance
let fontSize   = 14;
let tree       = [];
let tabs       = [];      // [{path,name,dirty,model,botName}]
let activeTab  = null;
let curBot     = '';
let curFile    = '';
let logWs      = null;
let uploadDest = '';
let sidebarOpen= true;
let logOpen    = true;

// ── Monaco init ──────────────────────────────────────────────────────
require.config({paths:{vs:'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.46.0/min/vs'}});
require(['vs/editor/editor.main'], () => {
  ed = monaco.editor.create(document.getElementById('monaco'), {
    value: '', language: 'python', theme: 'vs-dark',
    fontSize, fontFamily: "'Cascadia Code','Fira Code',Consolas,monospace",
    fontLigatures: true, automaticLayout: true,
    minimap: { enabled: true, scale: 1 },
    scrollBeyondLastLine: false, lineNumbers: 'on',
    mouseWheelZoom: false,     // we handle zoom ourselves
    smoothScrolling: true, cursorBlinking: 'smooth',
    bracketPairColorization: { enabled: true },
    wordWrap: 'off', padding: { top: 10 },
  });

  // Ctrl+S  → save
  ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveFile);
  // Ctrl+= / Ctrl+-  → zoom font
  ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Equal,  () => zoomEditor(+1));
  ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Minus,  () => zoomEditor(-1));

  ed.onDidChangeModelContent(() => {
    if (activeTab) { activeTab.dirty = true; renderTabs(); }
  });

  hidePH();
  refreshTree();
  setInterval(refreshTree, 30000);
  setInterval(pollStatus, 5000);
});

// Prevent Ctrl+Scroll from zooming the PAGE (we only zoom the editor font)
document.getElementById('monaco').addEventListener('wheel', e => {
  if (e.ctrlKey) { e.preventDefault(); zoomEditor(e.deltaY < 0 ? 1 : -1); }
}, { passive: false });

// Prevent pinch-zoom on mobile inside editor
document.getElementById('monaco').addEventListener('touchstart', e => {
  if (e.touches.length > 1) e.preventDefault();
}, { passive: false });

function zoomEditor(delta) {
  fontSize = Math.max(8, Math.min(28, fontSize + delta));
  if (ed) ed.updateOptions({ fontSize });
  document.getElementById('font-size-lbl').textContent = fontSize;
}

// ── File tree ────────────────────────────────────────────────────────
async function refreshTree() {
  const data = await API('/editor/api/tree');
  tree = Array.isArray(data) ? data : [];
  renderTree();
}

function renderTree() {
  const el = document.getElementById('tree-wrap');
  if (!tree.length) {
    el.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text3)">No bots yet.<br>Click ＋ Bot</div>';
    return;
  }
  el.innerHTML = tree.map(b => {
    const open = b.name === curBot;
    const sel  = open ? 'sel' : '';
    const st   = (b.status||'').toLowerCase();
    const sc   = st.includes('running') ? 'bst-run' : st.includes('stop')||st.includes('error') ? 'bst-stop' : 'bst-other';
    return `
    <div>
      <div class="bot-hdr ${sel}" onclick="toggleBot('${b.name}',this)">
        <span class="caret ${open?'open':''}">▶</span>
        <span>🤖</span>
        <span class="bot-name-lbl">${esc(b.name)}</span>
        <span class="bst ${sc}">${esc(b.status||'—')}</span>
      </div>
      <div class="bot-children ${open?'open':''}" id="bc-${eid(b.name)}">
        ${(b.children||[]).map(c=>renderNode(c,b.name)).join('')}
      </div>
    </div>`;
  }).join('');
}

function renderNode(node, bot) {
  const sel = node.path === curFile ? 'sel' : '';
  if (node.type==='dir') {
    const did = 'fd-'+eid(node.path);
    return `
    <div>
      <div class="f-item f-dir" onclick="toggleDir('${did}',this)">
        <span class="f-icon caret" id="c-${did}">▶</span><span>📁</span>
        <span class="f-name">${esc(node.name)}</span>
      </div>
      <div class="f-sub" id="${did}">
        ${(node.children||[]).map(c=>renderNode(c,bot)).join('')}
      </div>
    </div>`;
  }
  return `<div class="f-item ${sel}"
    onclick="openFile('${node.path}','${esc(node.name)}','${esc(bot)}')">
    <span class="f-icon">${fIcon(node.ext)}</span>
    <span class="f-name">${esc(node.name)}</span>
  </div>`;
}

function toggleBot(name, hdr) {
  const ch    = document.getElementById('bc-'+eid(name));
  const caret = hdr.querySelector('.caret');
  const isOpen= ch.classList.contains('open');
  if (!isOpen) selectBot(name);
  ch.classList.toggle('open', !isOpen);
  caret.classList.toggle('open', !isOpen);
}

function toggleDir(id, el) {
  const ch = document.getElementById(id);
  const cr = document.getElementById('c-'+id);
  if (ch) ch.classList.toggle('open');
  if (cr) cr.classList.toggle('open');
}

function selectBot(name) {
  curBot = name;
  document.getElementById('cur-bot-label').textContent = '🤖 '+name;
  document.getElementById('btn-nf').style.display = '';
  document.getElementById('btn-nd').style.display = '';
  uploadDest = name;
  connectWs(name);
  pollStatus();
  renderTree();
}

async function pollStatus() {
  if (!curBot) return;
  const b = tree.find(x=>x.name===curBot);
  if (!b) return;
  const badge = document.getElementById('status-badge');
  badge.textContent = b.status || '—';
  const st = (b.status||'').toLowerCase();
  badge.style.color = st.includes('running')?'var(--green)':st.includes('error')?'var(--red)':'var(--text2)';
}

// ── File open / tabs ─────────────────────────────────────────────────
async function openFile(path, name, bot) {
  if (!curBot || curBot !== bot) selectBot(bot);
  curFile = path;
  let tab = tabs.find(t=>t.path===path);
  if (tab) { activate(tab); return; }
  const res = await API(`/editor/api/file?path=${encodeURIComponent(path)}`);
  if (res.error) { toast('❌ '+res.error,'err'); return; }
  const model = monaco.editor.createModel(res.content, gLang(name));
  tab = { path, name, dirty:false, model, botName:bot };
  tabs.push(tab); activate(tab); renderTree();
}

function activate(tab) {
  activeTab = tab;
  if (ed) { ed.setModel(tab.model); hidePH(); }
  curFile = tab.path;
  if (!curBot) selectBot(tab.botName);
  renderTabs();
}

function closeTab(path, e) {
  if (e) e.stopPropagation();
  const i = tabs.findIndex(t=>t.path===path);
  if (i===-1) return;
  tabs[i].model.dispose(); tabs.splice(i,1);
  if (activeTab?.path===path) {
    activeTab = tabs[i] || tabs[i-1] || null;
    if (activeTab) activate(activeTab);
    else { if(ed)ed.setModel(null); showPH(); }
  }
  renderTabs();
}

function renderTabs() {
  const el = document.getElementById('tabs-bar');
  el.innerHTML = tabs.map(t=>`
    <div class="tab ${t===activeTab?'act':''} ${t.dirty?'dirty':''}"
         onclick="activate(tabs.find(x=>x.path==='${t.path}'))">
      ${fIcon(t.name.split('.').pop())} ${esc(t.name)}
      <span class="tab-x" onclick="closeTab('${t.path}',event)">✕</span>
    </div>`).join('');
}

// ── Save ─────────────────────────────────────────────────────────────
async function saveFile() {
  if (!activeTab||!ed) return;
  const content = ed.getValue();
  const res = await API('/editor/api/file',{
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({path:activeTab.path, content}),
  });
  if (res.error) { toast('❌ '+res.error,'err'); return; }
  activeTab.dirty = false; renderTabs(); toast('💾 Saved','ok');
}

// ── Run ──────────────────────────────────────────────────────────────
async function runBot() {
  if (!curBot) { toast('Select a bot first','err'); return; }
  if (activeTab?.dirty) await saveFile();
  const btn = document.getElementById('btn-run');
  btn.className = 'tbtn run loading'; btn.textContent = '⏳ Stopping…';
  const res = await API('/editor/api/run',{
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({bot:curBot}),
  });
  if (res.error) { toast('❌ '+res.error,'err'); btn.className='tbtn run'; btn.textContent='▶ Run'; return; }
  toast('▶ '+curBot+' restarting…','info');
  setTimeout(()=>{ btn.className='tbtn run'; btn.textContent='▶ Run'; refreshTree(); }, 4000);
}

// ── New bot ───────────────────────────────────────────────────────────
async function doNewBot() {
  const name=document.getElementById('nb-name').value.trim(); if(!name)return;
  const res=await API('/editor/api/newbot',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
  closeM('m-newbot');
  if(res.error){toast('❌ '+res.error,'err');return;}
  toast('🤖 '+res.name+' created','ok'); await refreshTree(); openFile(res.main,'main.py',res.name);
}

// ── New file ─────────────────────────────────────────────────────────
async function doNewFile() {
  const name=document.getElementById('nf-name').value.trim(); if(!name||!curBot)return;
  const path=curBot+'/'+name;
  const res=await API('/editor/api/file',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,content:''})});
  closeM('m-newfile');
  if(res.error){toast('❌ '+res.error,'err');return;}
  await refreshTree(); openFile(path,name,curBot);
}

// ── New dir ───────────────────────────────────────────────────────────
async function doNewDir() {
  const name=document.getElementById('nd-name').value.trim(); if(!name||!curBot)return;
  const path=curBot+'/'+name;
  const res=await API('/editor/api/mkdir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
  closeM('m-newdir'); if(res.error){toast('❌ '+res.error,'err');return;}
  toast('📁 Created','ok'); refreshTree();
}

// ── Delete ────────────────────────────────────────────────────────────
async function confirmDel() {
  if(!curFile){toast('No file selected','err');return;}
  if(!confirm('Delete '+curFile+'?'))return;
  const res=await API('/editor/api/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:curFile})});
  if(res.error){toast('❌ '+res.error,'err');return;}
  closeTab(curFile,null); curFile=''; toast('🗑 Deleted','ok'); refreshTree();
}

// ── Upload ────────────────────────────────────────────────────────────
function showUpload() {
  document.getElementById('ul-dest').textContent = uploadDest||'/';
  openM('m-upload');
}
document.getElementById('file-inp').addEventListener('change', async e=>{
  await uploadFiles(e.target.files); e.target.value='';
});
document.getElementById('ul-drop').addEventListener('dragover',e=>e.preventDefault());
document.getElementById('ul-drop').addEventListener('drop',async e=>{e.preventDefault();await uploadFiles(e.dataTransfer.files);});

async function uploadFiles(files) {
  if(!files.length)return;
  const fd=new FormData(); fd.append('dir',uploadDest);
  for(const f of files) fd.append('files',f,f.name);
  const res=await fetch('/editor/api/upload?token='+TOKEN,{method:'POST',body:fd}).then(r=>r.json());
  closeM('m-upload');
  if(res.error){toast('❌ '+res.error,'err');return;}
  toast('⬆ '+res.saved.length+' file(s) uploaded','ok'); refreshTree();
}

// ── Global drag-drop ─────────────────────────────────────────────────
const ov=document.getElementById('drop-overlay');
document.addEventListener('dragenter',e=>{if(e.dataTransfer.types.includes('Files'))ov.classList.add('show');});
document.addEventListener('dragover',e=>e.preventDefault());
document.addEventListener('dragleave',e=>{if(!e.relatedTarget)ov.classList.remove('show');});
document.addEventListener('drop',async e=>{e.preventDefault();ov.classList.remove('show');await uploadFiles(e.dataTransfer.files);});

// ── WebSocket log ─────────────────────────────────────────────────────
function connectWs(bot) {
  if(logWs){try{logWs.close()}catch(e){}}
  const lb=document.getElementById('log-body');
  lb.innerHTML=`<div class="ll ll-ok">── Connecting to ${bot} ──</div>`;
  logWs=new WebSocket(`${WS}/editor/ws?token=${TOKEN}&bot=${encodeURIComponent(bot)}`);
  logWs.onmessage=e=>appendLog(e.data);
  logWs.onclose=()=>appendLog('── Stream closed ──');
  logWs.onerror=()=>appendLog('── WS error ──');
}

function appendLog(txt) {
  const lb=document.getElementById('log-body');
  const d=document.createElement('div');
  d.className='ll'+(
    /error|exception|traceback/i.test(txt)?' ll-err':
    /warn/i.test(txt)?' ll-warn':
    /info|started|running|ok/i.test(txt)?' ll-ok':'');
  d.textContent=txt; lb.appendChild(d);
  if(lb.scrollHeight-lb.scrollTop<lb.clientHeight+80) lb.scrollTop=lb.scrollHeight;
  while(lb.children.length>3000) lb.removeChild(lb.firstChild);
}
function clearLog(){document.getElementById('log-body').innerHTML='';}
function logBottom(){const lb=document.getElementById('log-body');lb.scrollTop=lb.scrollHeight;}

// ── Panel toggles ─────────────────────────────────────────────────────
function toggleSidebar() {
  sidebarOpen=!sidebarOpen;
  document.getElementById('sidebar').classList.toggle('collapsed',!sidebarOpen);
  document.getElementById('btn-sb').classList.toggle('active',sidebarOpen);
  if(ed) ed.layout();
}
function toggleLog() {
  logOpen=!logOpen;
  document.getElementById('log-panel').classList.toggle('collapsed',!logOpen);
  document.getElementById('btn-log').classList.toggle('active',logOpen);
  if(ed) ed.layout();
}

// Fullscreen editor (hide sidebar + log)
let _fsMode=false;
function toggleFullscreen() {
  _fsMode=!_fsMode;
  document.getElementById('sidebar').classList.toggle('collapsed',_fsMode||!sidebarOpen);
  document.getElementById('log-panel').classList.toggle('collapsed',_fsMode||!logOpen);
  if(ed) ed.layout();
}

// ── Keyboard shortcuts ─────────────────────────────────────────────────
document.addEventListener('keydown', e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA') return;
  if(e.key==='b'||e.key==='B') toggleSidebar();
  if(e.key==='l'||e.key==='L') toggleLog();
  if(e.key==='f'||e.key==='F') toggleFullscreen();
  if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();saveFile();}
});

// ── Modals ────────────────────────────────────────────────────────────
function openM(id){
  document.getElementById(id).classList.add('show');
  const inp=document.getElementById(id).querySelector('input');
  if(inp){inp.value='';setTimeout(()=>inp.focus(),40);}
}
function closeM(id){document.getElementById(id).classList.remove('show');}
document.querySelectorAll('.mb').forEach(bg=>{
  bg.addEventListener('click',e=>{if(e.target===bg)bg.classList.remove('show');});
});
document.getElementById('nb-name').addEventListener('keydown',e=>e.key==='Enter'&&doNewBot());
document.getElementById('nf-name').addEventListener('keydown',e=>e.key==='Enter'&&doNewFile());
document.getElementById('nd-name').addEventListener('keydown',e=>e.key==='Enter'&&doNewDir());

// ── Toast ─────────────────────────────────────────────────────────────
let _tt;
function toast(msg,type='ok'){
  const el=document.getElementById('toast');
  el.textContent=msg; el.className='show '+type;
  clearTimeout(_tt); _tt=setTimeout(()=>el.className='',3000);
}

// ── Placeholder ───────────────────────────────────────────────────────
function showPH(){document.getElementById('placeholder').style.display='';}
function hidePH(){document.getElementById('placeholder').style.display='none';}

// ── Helpers ───────────────────────────────────────────────────────────
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function eid(s){return s.replace(/[^a-zA-Z0-9]/g,'_');}

function fIcon(e){
  return {py:'🐍',js:'📜',json:'📋',txt:'📄',md:'📝',sh:'⚙️',
    yml:'⚙️',yaml:'⚙️',env:'🔑',html:'🌐',css:'🎨',
    png:'🖼',jpg:'🖼',gif:'🖼',zip:'📦',log:'📋',
    cfg:'⚙️',ini:'⚙️',toml:'⚙️'}[e]||'📄';
}
function gLang(n){
  const e=n.split('.').pop().toLowerCase();
  return {py:'python',js:'javascript',json:'json',html:'html',css:'css',
    sh:'shell',md:'markdown',yml:'yaml',yaml:'yaml',
    toml:'ini',cfg:'ini',ini:'ini',txt:'plaintext'}[e]||'plaintext';
}
</script>
</body></html>"""
