"""
╔══════════════════════════════════════════════════════════════════════╗
║   MASTER HOSTING BOT  —  Web IDE  v3.0  (Replit-like)               ║
║   Codian Studio 💎                                                   ║
╠══════════════════════════════════════════════════════════════════════╣
║  • Per-user tokens — each user sees only their own bots              ║
║  • Admin token → sees all bots                                       ║
║  • Full token shown (browser needs it in URL)                        ║
║  • New-device alert → Telegram DM with Block/Allow buttons           ║
║  • Replit-like layout: sidebar | editor | terminal (separate)        ║
║  • Monaco Editor: zoom fixed, scroll-edit bug fixed                  ║
║  • Undo / Redo buttons in toolbar                                    ║
║  • Resizable sidebar & terminal pane (drag)                          ║
║  • Delete = silent (no "saved to GitHub" shown to user)              ║
║  • Ownership check on every API call                                 ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os, sys, json, time, uuid, shutil, hashlib, asyncio, logging, mimetypes
from pathlib import Path
from typing  import Optional

from aiohttp import web

log = logging.getLogger("WebIDE")

# ── Shared state injected by main.py ─────────────────────────────────
HOSTED_DIR    = Path("hosted_bots")
DATA_DIR      = HOSTED_DIR / "_data"
DELETED_DIR   = HOSTED_DIR / "_deleted"
BLOCKED_NAMES = {"_data", "_deleted", ".git", "__pycache__"}

_RUNNING_BOTS : dict      = {}
_APP_REF                  = None
_ADMIN_IDS   : set[int]   = set()
_PENDING     : dict       = {}   # fingerprint → "allow"|"block"|""

# Persisted file paths
_TOKENS_FILE      = DATA_DIR / "user_tokens.json"
_SESSIONS_FILE    = DATA_DIR / "ide_sessions.json"
_BLOCKED_IPS_FILE = DATA_DIR / "ide_blocked_ips.json"


def init_editor(running_bots: dict, app_ref=None, admin_ids: set = None):
    global _RUNNING_BOTS, _APP_REF, _ADMIN_IDS
    _RUNNING_BOTS = running_bots
    _APP_REF      = app_ref
    _ADMIN_IDS    = admin_ids or set()


# ══════════════════════════════════════════════════════════════════════
#  TOKEN SYSTEM  (per-user)
# ══════════════════════════════════════════════════════════════════════
def _load_json(p: Path, d):
    try:    return json.loads(p.read_text())
    except: return d

def _save_json(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))

def get_user_token(uid: int) -> str:
    """Return existing token for uid, or generate a new one."""
    tokens = _load_json(_TOKENS_FILE, {})
    for tok, stored_uid in tokens.items():
        if stored_uid == uid:
            return tok
    return _new_token(uid)

def rotate_user_token(uid: int) -> str:
    """Invalidate old token and generate new one."""
    tokens = _load_json(_TOKENS_FILE, {})
    tokens = {t: u for t, u in tokens.items() if u != uid}  # remove old
    _save_json(_TOKENS_FILE, tokens)
    return _new_token(uid)

def _new_token(uid: int) -> str:
    tok = uuid.uuid4().hex + uuid.uuid4().hex[:8]
    tokens = _load_json(_TOKENS_FILE, {})
    tokens[tok] = uid
    _save_json(_TOKENS_FILE, tokens)
    return tok

def _get_auth(req: web.Request) -> tuple[int, bool]:
    """Returns (uid, is_admin). uid=0 = not authenticated."""
    tok = req.rel_url.query.get("token") or req.cookies.get("ide_token", "")
    if not tok:
        return 0, False
    tokens = _load_json(_TOKENS_FILE, {})
    uid    = tokens.get(tok, 0)
    return uid, uid in _ADMIN_IDS

def _get_ip(req: web.Request) -> str:
    return req.headers.get("X-Forwarded-For", req.remote or "?").split(",")[0].strip()

def _fingerprint(req: web.Request) -> str:
    raw = _get_ip(req) + "|" + req.headers.get("User-Agent", "")
    return hashlib.sha256(raw.encode()).hexdigest()[:20]

def _is_ip_blocked(ip: str) -> bool:
    return ip in _load_json(_BLOCKED_IPS_FILE, [])

def _block_ip(ip: str):
    bl = _load_json(_BLOCKED_IPS_FILE, [])
    if ip not in bl: bl.append(ip)
    _save_json(_BLOCKED_IPS_FILE, bl)

def _unblock_ip(ip: str):
    bl = [b for b in _load_json(_BLOCKED_IPS_FILE, []) if b != ip]
    _save_json(_BLOCKED_IPS_FILE, bl)

def _register_session(fp, ip, ua) -> bool:
    sessions     = _load_json(_SESSIONS_FILE, {})
    is_new       = fp not in sessions
    sessions[fp] = {"ip": ip, "ua": ua[:120], "last_seen": time.time()}
    _save_json(_SESSIONS_FILE, sessions)
    return is_new

async def _alert_new_device(uid: int, fp: str, ip: str, ua: str):
    if not _APP_REF or not _ADMIN_IDS:
        return
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    _PENDING[fp] = ""
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Allow",    callback_data=f"ide_allow|{fp}|{ip}"),
        InlineKeyboardButton("🚫 Block IP", callback_data=f"ide_block|{fp}|{ip}"),
    ]])
    msg = (f"🔔 *New IDE Login*\n\n"
           f"👤 User: `{uid}`\n🌐 IP: `{ip}`\n"
           f"🖥 Browser: `{ua[:70]}`\n🔑 Session: `{fp}`\n\n"
           f"Is this you? Block if not.")
    for aid in _ADMIN_IDS:
        try: await _APP_REF.bot.send_message(aid, msg, parse_mode="Markdown", reply_markup=kb)
        except Exception as e: log.warning("Alert failed: %s", e)

# ══════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════
def _fmt_up(s: float) -> str:
    s = int(max(0, s)); h, r = divmod(s, 3600); m, sc = divmod(r, 60)
    return f"{h}h {m}m {sc}s"

def _load_registry() -> dict:
    try:    return json.loads((DATA_DIR / "registry.json").read_text())
    except: return {}

def _can_access(uid: int, is_admin: bool, bot_name: str) -> bool:
    if is_admin: return True
    reg = _load_registry()
    return str(reg.get(bot_name, {}).get("owner_id", "")) == str(uid)

def _user_bots(uid: int, is_admin: bool) -> list[str]:
    reg = _load_registry()
    if is_admin: return list(reg.keys())
    return [k for k, v in reg.items() if str(v.get("owner_id", "")) == str(uid)]

def _safe_path(raw: str) -> Optional[Path]:
    try:
        full = (HOSTED_DIR / raw).resolve()
        base = HOSTED_DIR.resolve()
        if full == base or base in full.parents: return full
    except: pass
    return None

def _build_tree(root: Path, rel: str = "") -> list:
    items = []
    try: entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except: return items
    for e in entries:
        if e.name.startswith(".") or e.name == "__pycache__": continue
        node = {"name": e.name, "path": (rel+"/"+e.name).lstrip("/"),
                "type": "dir" if e.is_dir() else "file",
                "ext":  e.suffix.lstrip(".").lower() if e.is_file() else "",
                "size": e.stat().st_size if e.is_file() else 0}
        if e.is_dir(): node["children"] = _build_tree(e, node["path"])
        items.append(node)
    return items

def _bot_tree(uid: int, is_admin: bool) -> list:
    if not HOSTED_DIR.exists(): return []
    allowed = set(_user_bots(uid, is_admin))
    bots    = []
    reg     = _load_registry()
    for item in sorted(HOSTED_DIR.iterdir()):
        if not item.is_dir() or item.name in BLOCKED_NAMES: continue
        if item.name not in allowed: continue
        e = _RUNNING_BOTS.get(item.name, {})
        owner = reg.get(item.name, {}).get("owner_id", "?")
        bots.append({"name": item.name, "path": item.name, "type": "bot",
                     "status":   e.get("status", "Offline 🔴"),
                     "owner":    owner,
                     "restarts": e.get("restarts", 0),
                     "uptime":   _fmt_up(time.time()-e["start_time"]) if e.get("start_time") else "—",
                     "children": _build_tree(item, item.name)})
    return bots

def _tail(path: Path, n=80) -> str:
    try: return "\n".join(path.read_text(errors="replace").splitlines()[-n:])
    except: return ""

# ══════════════════════════════════════════════════════════════════════
#  API HANDLERS
# ══════════════════════════════════════════════════════════════════════
async def api_tree(req: web.Request) -> web.Response:
    if _is_ip_blocked(_get_ip(req)): return web.json_response({"error":"blocked"},status=403)
    uid, is_admin = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    return web.json_response(_bot_tree(uid, is_admin))

async def api_read(req: web.Request) -> web.Response:
    uid, is_admin = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    raw  = req.rel_url.query.get("path","")
    bot  = raw.split("/")[0] if raw else ""
    if not _can_access(uid, is_admin, bot): return web.json_response({"error":"forbidden"},status=403)
    path = _safe_path(raw)
    if not path or not path.exists() or not path.is_file():
        return web.json_response({"error":"File not found"},status=404)
    try:    content = path.read_text(errors="replace")[:524288]
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)
    return web.json_response({"path":raw,"name":path.name,"content":content,
                              "mime":mimetypes.guess_type(str(path))[0] or "text/plain",
                              "size":path.stat().st_size})

async def api_write(req: web.Request) -> web.Response:
    uid, is_admin = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body = await req.json()
        raw  = body.get("path",""); bot = raw.split("/")[0]
        if not _can_access(uid, is_admin, bot): return web.json_response({"error":"forbidden"},status=403)
        path = _safe_path(raw)
        if not path: return web.json_response({"error":"Invalid path"},status=400)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.get("content",""), encoding="utf-8")
        return web.json_response({"ok":True})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_delete(req: web.Request) -> web.Response:
    uid, is_admin = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body = await req.json(); raw = body.get("path",""); bot = raw.split("/")[0]
        if not _can_access(uid, is_admin, bot): return web.json_response({"error":"forbidden"},status=403)
        path = _safe_path(raw)
        if not path or not path.exists(): return web.json_response({"error":"Not found"},status=404)
        if path.parent.resolve()==HOSTED_DIR.resolve() and path.is_dir():
            return web.json_response({"error":"Use the bot menu to delete an entire bot."},status=400)
        shutil.rmtree(path) if path.is_dir() else path.unlink()
        return web.json_response({"ok":True})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_mkdir(req: web.Request) -> web.Response:
    uid, is_admin = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body = await req.json(); raw = body.get("path",""); bot = raw.split("/")[0]
        if not _can_access(uid, is_admin, bot): return web.json_response({"error":"forbidden"},status=403)
        path = _safe_path(raw)
        if not path: return web.json_response({"error":"Invalid path"},status=400)
        path.mkdir(parents=True, exist_ok=True)
        return web.json_response({"ok":True})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_upload(req: web.Request) -> web.Response:
    uid, is_admin = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        reader = await req.multipart(); dest_raw=""; saved=[]
        async for part in reader:
            if part.name=="dir":
                dest_raw=(await part.read()).decode().strip()
            elif part.filename:
                bot=dest_raw.split("/")[0] if dest_raw else ""
                if bot and not _can_access(uid, is_admin, bot): continue
                dest_dir = _safe_path(dest_raw) if dest_raw else HOSTED_DIR
                if not dest_dir: continue
                dest_dir.mkdir(parents=True,exist_ok=True)
                fname=Path(part.filename).name
                (dest_dir/fname).write_bytes(await part.read())
                saved.append((dest_dir/fname).relative_to(HOSTED_DIR).as_posix())
        return web.json_response({"ok":True,"saved":saved})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_newbot(req: web.Request) -> web.Response:
    uid, is_admin = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body = await req.json()
        name = body.get("name","").strip().replace(" ","_").replace("/","")
        if not name: return web.json_response({"error":"Name required"},status=400)
        bot_dir = HOSTED_DIR/name
        if bot_dir.exists(): return web.json_response({"error":f"'{name}' already exists"},status=409)
        bot_dir.mkdir(parents=True)
        (bot_dir/"main.py").write_text(
            f'# {name}  —  Codian Studio 💎\n\nprint("Hello from {name}!")\n',encoding="utf-8")
        (bot_dir/"requirements.txt").write_text("# Dependencies\n",encoding="utf-8")
        # Register in registry so user can access it via IDE
        try:
            reg = _load_registry()
            reg[name] = {"owner_id": uid, "registered_at": time.time()}
            (DATA_DIR/"registry.json").write_text(json.dumps(reg,indent=2))
        except: pass
        return web.json_response({"ok":True,"name":name,"main":f"{name}/main.py"})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_run(req: web.Request) -> web.Response:
    uid, is_admin = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body     = await req.json(); bot_name = body.get("bot","").strip()
        if not bot_name: return web.json_response({"error":"Bot name required"},status=400)
        if not _can_access(uid, is_admin, bot_name):
            return web.json_response({"error":"forbidden"},status=403)
        bot_dir = HOSTED_DIR/bot_name
        if not (bot_dir/"main.py").exists():
            return web.json_response({"error":"main.py not found"},status=404)
        # Stop running process
        e = _RUNNING_BOTS.get(bot_name)
        if e:
            e["active"]=False; p=e.get("process")
            if p and p.poll() is None:
                p.terminate()
                try: await asyncio.get_event_loop().run_in_executor(None, p.wait, 5)
                except: 
                    try: p.kill()
                    except: pass
            e["status"]="Stopped 🛑"
        # Write restart flag for run_bot loop
        (bot_dir/".restart_flag").write_text(str(time.time()))
        return web.json_response({"ok":True,"bot":bot_name,"msg":"Restarting…"})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_logs(req: web.Request) -> web.Response:
    uid, is_admin = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    bot_name = req.rel_url.query.get("bot","")
    if not _can_access(uid, is_admin, bot_name):
        return web.json_response({"error":"forbidden"},status=403)
    n        = min(int(req.rel_url.query.get("n",150)), 500)
    log_path = HOSTED_DIR/bot_name/"bot_output.log"
    lines    = _tail(log_path, n).splitlines() if _tail(log_path, n) else []
    e        = _RUNNING_BOTS.get(bot_name, {})
    return web.json_response({"bot":bot_name,"lines":lines,
                              "status":e.get("status","Offline"),
                              "uptime":_fmt_up(time.time()-e["start_time"]) if e.get("start_time") else "—",
                              "restarts":e.get("restarts",0)})

# ── WebSocket real-time log ──────────────────────────────────────────
async def ws_logs(req: web.Request) -> web.WebSocketResponse:
    tok      = req.rel_url.query.get("token","")
    bot_name = req.rel_url.query.get("bot","")
    tokens   = _load_json(_TOKENS_FILE, {})
    uid      = tokens.get(tok, 0)
    if not uid: raise web.HTTPUnauthorized()
    is_admin = uid in _ADMIN_IDS
    if not _can_access(uid, is_admin, bot_name): raise web.HTTPForbidden()

    ws       = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(req)
    log_path = HOSTED_DIR/bot_name/"bot_output.log"
    pos      = 0
    try:
        text  = log_path.read_text(errors="replace")
        lines = text.splitlines()[-80:]
        for ln in lines: await ws.send_str(ln)
        pos = log_path.stat().st_size
    except: pass
    try:
        while not ws.closed:
            await asyncio.sleep(0.8)
            try:
                size = log_path.stat().st_size
                if size > pos:
                    with open(log_path,"r",errors="replace") as f:
                        f.seek(pos); chunk=f.read()
                    pos=size
                    for ln in chunk.splitlines():
                        if ln: await ws.send_str(ln)
            except: pass
    except asyncio.CancelledError: pass
    finally:
        try: await ws.close()
        except: pass
    return ws

# ── Editor page ──────────────────────────────────────────────────────
async def editor_page(req: web.Request) -> web.Response:
    if _is_ip_blocked(_get_ip(req)):
        return web.Response(text="403 — IP blocked", status=403)

    tok = req.rel_url.query.get("token","")
    tokens = _load_json(_TOKENS_FILE, {})
    uid    = tokens.get(tok, 0)

    # Token in URL → set cookie, redirect to clean URL
    if uid:
        resp = web.HTTPFound(location="/editor")
        resp.set_cookie("ide_token", tok, max_age=86400*30, httponly=True, samesite="Strict")
        return resp

    # Check cookie
    uid2, is_admin = _get_auth(req)
    if not uid2:
        return web.Response(text=_LOGIN_HTML, content_type="text/html", status=401)

    # New device fingerprint check
    fp = _fingerprint(req); ip = _get_ip(req); ua = req.headers.get("User-Agent","")
    if _register_session(fp, ip, ua):
        asyncio.create_task(_alert_new_device(uid2, fp, ip, ua))

    return web.Response(text=_IDE_HTML, content_type="text/html")

def register_routes(app: web.Application):
    app.router.add_get ("/editor",            editor_page)
    app.router.add_get ("/editor/api/tree",   api_tree)
    app.router.add_get ("/editor/api/file",   api_read)
    app.router.add_post("/editor/api/file",   api_write)
    app.router.add_post("/editor/api/delete", api_delete)
    app.router.add_post("/editor/api/mkdir",  api_mkdir)
    app.router.add_post("/editor/api/upload", api_upload)
    app.router.add_post("/editor/api/newbot", api_newbot)
    app.router.add_post("/editor/api/run",    api_run)
    app.router.add_get ("/editor/api/logs",   api_logs)
    app.router.add_get ("/editor/ws",         ws_logs)
    log.info("Web IDE routes ready at /editor")

# ══════════════════════════════════════════════════════════════════════
#  LOGIN PAGE
# ══════════════════════════════════════════════════════════════════════
_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Codian Studio — Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0e1525;color:#e1e4e8;font-family:'Segoe UI',sans-serif;
     display:flex;align-items:center;justify-content:center;height:100vh}
.card{background:#141d2b;border:1px solid #1c2b3a;border-radius:12px;
      padding:40px;width:360px;text-align:center;box-shadow:0 16px 48px #00000088}
.logo{font-size:3rem;margin-bottom:10px}
h1{color:#0096ff;font-size:1.1rem;margin-bottom:4px;font-weight:700}
p{color:#8892a4;font-size:.82rem;margin-bottom:28px}
input{width:100%;background:#0e1525;border:1px solid #1c2b3a;border-radius:7px;
      padding:12px 14px;color:#e1e4e8;font-size:13px;outline:none;font-family:monospace;
      letter-spacing:1px;margin-bottom:14px}
input:focus{border-color:#0096ff;box-shadow:0 0 0 2px #0096ff22}
button{width:100%;background:#0096ff;border:none;border-radius:7px;
       padding:12px;color:#fff;font-size:14px;font-weight:700;cursor:pointer;transition:.2s}
button:hover{background:#0080df}
.hint{color:#8892a4;font-size:.75rem;margin-top:14px;line-height:1.6}
</style></head><body>
<div class="card">
  <div class="logo">💎</div>
  <h1>Codian Studio</h1>
  <p>Master Hosting Bot — Web IDE</p>
  <input type="password" id="tok" placeholder="Paste your access token"
         onkeydown="if(event.key==='Enter')go()">
  <button onclick="go()">🔐 Open IDE</button>
  <p class="hint">Get your token from the bot:<br><code>/ide</code></p>
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
#  REPLIT-LIKE IDE HTML
# ══════════════════════════════════════════════════════════════════════
_IDE_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Codian Studio 💎</title>
<style>
/* ── Reset & Variables ────────────────────────────────────────────── */
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{
  --bg:    #0e1525;
  --bg1:   #141d2b;
  --bg2:   #1a2537;
  --bg3:   #1f2e42;
  --border:#1c2b3a;
  --blue:  #0096ff;
  --green: #4caf50;
  --red:   #f44336;
  --yel:   #ffb74d;
  --pur:   #9c5fff;
  --text:  #e1e4e8;
  --text2: #8892a4;
  --text3: #556070;
  --sb-w:  220px;
  --top-h: 46px;
  --term-h:200px;
}
html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--text);
          font-family:'Segoe UI',system-ui,sans-serif;font-size:13px;
          -webkit-font-smoothing:antialiased}

/* ── App Layout ───────────────────────────────────────────────────── */
#app{display:flex;flex-direction:column;height:100vh;overflow:hidden}

/* ── Topbar ───────────────────────────────────────────────────────── */
#topbar{height:var(--top-h);flex-shrink:0;background:var(--bg1);
        border-bottom:1px solid var(--border);display:flex;align-items:center;
        gap:5px;padding:0 10px;user-select:none;overflow-x:auto;overflow-y:hidden}
#topbar::-webkit-scrollbar{display:none}
.logo{font-size:1rem;font-weight:800;color:var(--blue);margin-right:6px;
      white-space:nowrap;flex-shrink:0}
.sep{width:1px;height:22px;background:var(--border);flex-shrink:0;margin:0 3px}
.spacer{flex:1;min-width:8px}
/* Topbar buttons */
.btn{display:inline-flex;align-items:center;gap:4px;border-radius:6px;
     padding:5px 10px;cursor:pointer;font-size:12px;border:1px solid var(--border);
     color:var(--text2);background:var(--bg2);white-space:nowrap;flex-shrink:0;
     transition:border-color .15s,color .15s,background .15s;user-select:none}
.btn:hover{border-color:var(--blue);color:var(--text);background:var(--bg3)}
.btn.active{border-color:var(--blue);color:var(--blue);background:var(--bg3)}
.btn.run{border-color:var(--green);color:var(--green);background:#0d1e10}
.btn.run:hover,.btn.run.loading{background:var(--green);color:#fff;border-color:var(--green)}
.btn.run.loading{pointer-events:none;opacity:.85}
.btn.danger{border-color:var(--red);color:var(--red);background:#1e0d0d}
.btn.danger:hover{background:var(--red);color:#fff}
.btn.undo,.btn.redo{padding:5px 8px;font-size:14px}
#bot-label{color:var(--pur);font-weight:700;font-size:12px;white-space:nowrap;
           flex-shrink:0;max-width:120px;overflow:hidden;text-overflow:ellipsis}
#font-sz{font-size:11px;color:var(--text2);min-width:26px;text-align:center;flex-shrink:0}
#status-dot{width:8px;height:8px;border-radius:50%;background:var(--text3);
            flex-shrink:0;transition:background .3s}
#status-dot.run{background:var(--green)}
#status-dot.err{background:var(--red)}
#status-dot.other{background:var(--yel)}

/* ── Workspace ────────────────────────────────────────────────────── */
#workspace{display:flex;flex:1;overflow:hidden;min-height:0}

/* ── Sidebar ──────────────────────────────────────────────────────── */
#sidebar{width:var(--sb-w);min-width:140px;max-width:400px;
         flex-shrink:0;display:flex;flex-direction:column;
         background:var(--bg1);border-right:1px solid var(--border);
         overflow:hidden;transition:width .18s}
#sidebar.hidden{width:0;min-width:0;border-right:none}
#sb-hdr{display:flex;align-items:center;gap:4px;padding:7px 10px;
        border-bottom:1px solid var(--border);flex-shrink:0}
#sb-hdr .title{flex:1;font-size:10px;font-weight:700;color:var(--text2);
               text-transform:uppercase;letter-spacing:.7px;white-space:nowrap}
.ib{background:none;border:none;color:var(--text2);cursor:pointer;
    padding:2px 5px;border-radius:3px;font-size:14px;line-height:1;transition:.15s}
.ib:hover{background:var(--bg3);color:var(--text)}
#tree{flex:1;overflow-y:auto;overflow-x:hidden;padding:3px 0;
      scrollbar-width:thin;scrollbar-color:var(--border) transparent}
#tree::-webkit-scrollbar{width:4px}
#tree::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}

/* ── Sidebar resizer ──────────────────────────────────────────────── */
#rsz-h{width:4px;flex-shrink:0;cursor:col-resize;background:transparent;
       transition:background .15s;position:relative;z-index:10}
#rsz-h:hover,#rsz-h.dragging{background:var(--blue)}

/* ── Editor pane ──────────────────────────────────────────────────── */
#editor-pane{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0;min-height:0}

/* ── Tabs ─────────────────────────────────────────────────────────── */
#tabs{height:36px;flex-shrink:0;display:flex;background:var(--bg1);
      border-bottom:1px solid var(--border);overflow-x:auto;overflow-y:hidden}
#tabs::-webkit-scrollbar{height:3px}
#tabs::-webkit-scrollbar-thumb{background:var(--border)}
.tab{display:inline-flex;align-items:center;gap:6px;padding:0 14px;
     cursor:pointer;border-right:1px solid var(--border);white-space:nowrap;
     font-size:12px;color:var(--text2);flex-shrink:0;height:100%;
     transition:color .1s,background .1s}
.tab:hover{color:var(--text);background:var(--bg2)}
.tab.act{background:var(--bg);color:var(--text);box-shadow:inset 0 -2px 0 var(--blue)}
.tab.dirty .tab-name::after{content:'•';margin-left:4px;color:var(--yel)}
.tab-x{opacity:.3;border-radius:3px;padding:0 2px;line-height:1;font-size:13px;
        transition:.15s}
.tab-x:hover{opacity:1;background:var(--red);color:#fff}

/* ── Monaco container  ──────────────────────────────────────────────
   CRITICAL: overflow:hidden + position:relative prevents scroll-edit bug
   The #monaco div is absolutely positioned inside to fill exactly
────────────────────────────────────────────────────────────────────── */
#ed-wrap{flex:1;position:relative;overflow:hidden;min-height:0;
         background:#1e1e1e /* Monaco bg fallback */}
#monaco-container{position:absolute;inset:0;overflow:hidden}

/* ── Terminal resizer ─────────────────────────────────────────────── */
#rsz-v{height:5px;flex-shrink:0;cursor:row-resize;background:var(--border);
       position:relative;z-index:10;transition:background .15s}
#rsz-v:hover,#rsz-v.dragging{background:var(--blue)}

/* ── Terminal pane ────────────────────────────────────────────────── */
#term-pane{height:var(--term-h);min-height:80px;max-height:60vh;
           flex-shrink:0;display:flex;flex-direction:column;
           background:var(--bg1);border-top:1px solid var(--border)}
#term-pane.hidden{height:0!important;min-height:0;border-top:none;overflow:hidden}
#term-hdr{display:flex;align-items:center;gap:6px;padding:5px 12px;
          border-bottom:1px solid var(--border);flex-shrink:0}
#term-hdr .title{font-size:10px;font-weight:700;color:var(--text2);
                 text-transform:uppercase;letter-spacing:.6px;flex:1}
#term-body{flex:1;overflow-y:auto;padding:6px 12px;
           font-family:'Cascadia Code','Fira Code',Consolas,monospace;
           font-size:12px;line-height:1.7;color:#abb2bf;
           scrollbar-width:thin;scrollbar-color:var(--border) transparent}
#term-body::-webkit-scrollbar{width:4px}
#term-body::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
.tl{white-space:pre-wrap;word-break:break-all}
.tl-err{color:#f44336}.tl-warn{color:#ffb74d}.tl-ok{color:#4caf50}
.tl-info{color:#42a5f5}

/* ── Tree nodes ───────────────────────────────────────────────────── */
.bot-row{display:flex;align-items:center;gap:5px;padding:5px 10px;
         cursor:pointer;border-left:2px solid transparent;transition:.12s}
.bot-row:hover{background:var(--bg2)}
.bot-row.sel{background:#0d2040;border-left-color:var(--blue)}
.caret{font-size:9px;color:var(--text3);transition:transform .15s;flex-shrink:0;width:10px}
.caret.op{transform:rotate(90deg)}
.b-name{flex:1;font-weight:700;font-size:12px;overflow:hidden;
        text-overflow:ellipsis;white-space:nowrap}
.bst{font-size:9px;padding:1px 5px;border-radius:8px;flex-shrink:0;white-space:nowrap;font-weight:600}
.bst-r{background:#0d1e10;color:var(--green)}
.bst-s{background:#1e0d0d;color:var(--red)}
.bst-o{background:var(--bg3);color:var(--text2)}
.bot-ch{display:none;padding-left:12px}
.bot-ch.op{display:block}
.f-row{display:flex;align-items:center;gap:5px;padding:3px 8px;
       cursor:pointer;border-radius:3px;transition:.1s}
.f-row:hover{background:var(--bg3)}
.f-row.sel{background:#0d2040;color:var(--blue)}
.f-dir-ch{display:none;padding-left:14px}
.f-dir-ch.op{display:block}
.f-ico{font-size:12px;flex-shrink:0;width:16px}
.f-nm{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.f-dir-nm{color:var(--yel)}

/* ── Placeholder ──────────────────────────────────────────────────── */
#placeholder{position:absolute;inset:0;display:flex;flex-direction:column;
             align-items:center;justify-content:center;color:var(--text3);
             gap:10px;pointer-events:none;z-index:1}
#placeholder.hide{display:none}
#placeholder .big{font-size:3.5rem}

/* ── Modals ───────────────────────────────────────────────────────── */
.moverlay{display:none;position:fixed;inset:0;background:#000000aa;
          align-items:center;justify-content:center;z-index:200}
.moverlay.show{display:flex}
.modal{background:var(--bg1);border:1px solid var(--border);border-radius:10px;
       padding:24px;min-width:300px;max-width:420px;box-shadow:0 12px 40px #00000099}
.modal h3{margin-bottom:16px;color:var(--blue);font-size:.95rem}
.modal input{width:100%;background:var(--bg);border:1px solid var(--border);
             border-radius:6px;padding:9px 12px;color:var(--text);font-size:13px;
             outline:none;margin-bottom:12px}
.modal input:focus{border-color:var(--blue)}
.modal-btns{display:flex;gap:8px;justify-content:flex-end}
.mok{padding:7px 16px;border-radius:6px;border:none;background:var(--blue);
     color:#fff;cursor:pointer;font-size:13px;font-weight:600}
.mok:hover{opacity:.85}
.mcancel{padding:7px 16px;border-radius:6px;border:1px solid var(--border);
         background:var(--bg2);color:var(--text);cursor:pointer;font-size:13px}

/* ── Toast ────────────────────────────────────────────────────────── */
#toast{position:fixed;bottom:18px;right:18px;background:var(--bg2);
       border-radius:8px;padding:10px 16px;font-size:13px;
       opacity:0;pointer-events:none;z-index:500;max-width:280px;
       transition:opacity .2s;border:1px solid var(--border)}
#toast.show{opacity:1}
#toast.ok{border-color:var(--green);color:var(--green)}
#toast.err{border-color:var(--red);color:var(--red)}
#toast.info{border-color:var(--blue);color:var(--blue)}

/* ── Drop overlay ─────────────────────────────────────────────────── */
#drop-ov{display:none;position:fixed;inset:0;background:#0096ff18;
         border:3px dashed var(--blue);z-index:400;align-items:center;
         justify-content:center;font-size:1.4rem;color:var(--blue);pointer-events:none}
#drop-ov.show{display:flex}
#finp{display:none}
</style>
</head>
<body>
<input type="file" id="finp" multiple>
<div id="drop-ov">📁 Drop files here</div>
<div id="toast"></div>

<!-- Modals -->
<div class="moverlay" id="m-newbot">
  <div class="modal"><h3>🤖 New Bot Project</h3>
    <input id="nb-n" placeholder="project-name">
    <div class="modal-btns">
      <button class="mcancel" onclick="cM('m-newbot')">Cancel</button>
      <button class="mok" onclick="doNewBot()">Create</button>
    </div></div></div>

<div class="moverlay" id="m-newfile">
  <div class="modal"><h3>📄 New File</h3>
    <input id="nf-n" placeholder="filename.py">
    <div class="modal-btns">
      <button class="mcancel" onclick="cM('m-newfile')">Cancel</button>
      <button class="mok" onclick="doNewFile()">Create</button>
    </div></div></div>

<div class="moverlay" id="m-newdir">
  <div class="modal"><h3>📁 New Folder</h3>
    <input id="nd-n" placeholder="folder-name">
    <div class="modal-btns">
      <button class="mcancel" onclick="cM('m-newdir')">Cancel</button>
      <button class="mok" onclick="doNewDir()">Create</button>
    </div></div></div>

<div class="moverlay" id="m-upload">
  <div class="modal"><h3>⬆️ Upload Files</h3>
    <p style="color:var(--text2);font-size:12px;margin-bottom:14px">
      To: <b id="ul-dest" style="color:var(--text)">/</b></p>
    <button class="mok" style="width:100%;margin-bottom:10px"
      onclick="document.getElementById('finp').click()">📂 Choose Files</button>
    <div id="ul-drop" style="border:2px dashed var(--border);border-radius:7px;
         padding:16px;text-align:center;color:var(--text3);font-size:12px;cursor:pointer"
         onclick="document.getElementById('finp').click()">
      or drag & drop files here</div>
    <div class="modal-btns" style="margin-top:14px">
      <button class="mcancel" onclick="cM('m-upload')">Close</button>
    </div></div></div>

<!-- APP -->
<div id="app">
  <!-- TOPBAR -->
  <div id="topbar">
    <span class="logo">💎 Codian</span>
    <div class="sep"></div>
    <button class="btn active" id="btn-sb"  onclick="toggleSB()"  title="Sidebar [B]">☰</button>
    <button class="btn" onclick="oM('m-newbot')">＋ Bot</button>
    <button class="btn" onclick="showUpload()">⬆ Upload</button>
    <button class="btn" id="btn-nf" style="display:none" onclick="oM('m-newfile')">📄 File</button>
    <button class="btn" id="btn-nd" style="display:none" onclick="oM('m-newdir')">📁 Dir</button>
    <div class="sep"></div>
    <div id="status-dot"></div>
    <div id="bot-label">Select a bot</div>
    <div class="spacer"></div>
    <button class="btn undo" onclick="doUndo()" title="Undo [Ctrl+Z]">↩</button>
    <button class="btn redo" onclick="doRedo()" title="Redo [Ctrl+Y]">↪</button>
    <div class="sep"></div>
    <button class="btn" onclick="zE(-1)" title="Zoom Out">A−</button>
    <span id="font-sz">14</span>
    <button class="btn" onclick="zE(+1)" title="Zoom In">A+</button>
    <div class="sep"></div>
    <button class="btn" onclick="saveFile()" title="Save [Ctrl+S]">💾 Save</button>
    <button class="btn run" id="btn-run" onclick="runBot()">▶ Run</button>
    <div class="sep"></div>
    <button class="btn active" id="btn-tm" onclick="toggleTerm()" title="Terminal [T]">⬛ Term</button>
    <button class="btn danger" onclick="confirmDel()">🗑</button>
  </div>

  <!-- WORKSPACE -->
  <div id="workspace">
    <!-- SIDEBAR -->
    <div id="sidebar">
      <div id="sb-hdr">
        <span class="title">Explorer</span>
        <button class="ib" onclick="refreshTree()" title="Refresh">⟳</button>
      </div>
      <div id="tree"><div style="padding:20px;text-align:center;color:var(--text3)">Loading…</div></div>
    </div>

    <!-- SIDEBAR RESIZER -->
    <div id="rsz-h"></div>

    <!-- EDITOR PANE -->
    <div id="editor-pane">
      <!-- TABS -->
      <div id="tabs"></div>

      <!-- MONACO -->
      <div id="ed-wrap">
        <div id="monaco-container"></div>
        <div id="placeholder">
          <div class="big">💎</div>
          <p style="font-size:13px">Select a file to start editing</p>
          <p style="font-size:11px;color:var(--text3)">Ctrl+S save  ·  B sidebar  ·  T terminal  ·  F fullscreen</p>
        </div>
      </div>

      <!-- TERMINAL RESIZER -->
      <div id="rsz-v"></div>

      <!-- TERMINAL — completely separate from editor -->
      <div id="term-pane">
        <div id="term-hdr">
          <span class="title">▶ Terminal / Bot Output</span>
          <button class="ib" onclick="clearTerm()">✕</button>
          <button class="ib" onclick="termBottom()">⬇</button>
        </div>
        <div id="term-body"></div>
      </div>
    </div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.46.0/min/vs/loader.min.js"></script>
<script>
'use strict';

// ── Token from cookie ────────────────────────────────────────────────
const TOK = (document.cookie.match(/ide_token=([^;]+)/)||[])[1]||'';
const WS  = (location.protocol==='https:'?'wss://':'ws://')+location.host;
const api = async(url,opts={})=>{
  const q=url.includes('?')?'&':'?';
  const r=await fetch(url+q+'token='+TOK,opts);
  return r.json().catch(()=>({error:'Parse error'}));
};

// ── State ────────────────────────────────────────────────────────────
let ed       = null;
let fsize    = 14;
let tree     = [];
let tabs     = [];
let aTab     = null;   // active tab {path,name,dirty,model,bot}
let curBot   = '';
let curFile  = '';
let logWs    = null;
let ulDest   = '';
let sbOpen   = true;
let tmOpen   = true;

// ══════════════════════════════════════════════════════════════════════
//  MONACO INIT
// ══════════════════════════════════════════════════════════════════════
require.config({paths:{vs:'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.46.0/min/vs'}});
require(['vs/editor/editor.main'], ()=>{
  ed = monaco.editor.create(document.getElementById('monaco-container'),{
    value:'', language:'python', theme:'vs-dark',
    fontSize: fsize,
    fontFamily:"'Cascadia Code','Fira Code',Consolas,monospace",
    fontLigatures: true,
    automaticLayout: true,
    minimap:{ enabled:true, scale:1 },
    scrollBeyondLastLine: false,
    lineNumbers: 'on',
    glyphMargin: true,
    folding: true,
    wordWrap: 'off',
    smoothScrolling: true,
    cursorBlinking: 'smooth',
    cursorSmoothCaretAnimation: 'on',
    renderLineHighlight: 'all',
    bracketPairColorization:{ enabled:true },
    padding:{ top:12, bottom:12 },
    // ── SCROLL FIX: Monaco handles its own scroll, no browser interference
    scrollbar:{
      vertical:'auto', horizontal:'auto',
      useShadows:false, verticalScrollbarSize:8, horizontalScrollbarSize:8,
      alwaysConsumeMouseWheel:true,
    },
    mouseWheelZoom: false,           // we handle zoom ourselves
    overviewRulerLanes: 0,           // cleaner look
    suggest:{ showIcons:true },
    quickSuggestions:{ other:true, comments:false, strings:false },
  });

  // Keyboard shortcuts inside Monaco
  ed.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.KeyS, saveFile);
  ed.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.Equal, ()=>zE(+1));
  ed.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.Minus, ()=>zE(-1));
  ed.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.KeyZ, doUndo);
  ed.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyMod.Shift|monaco.KeyCode.KeyZ, doRedo);

  ed.onDidChangeModelContent(()=>{
    if(aTab){ aTab.dirty=true; renderTabs(); }
  });

  hidePH();
  refreshTree();
  setInterval(refreshTree, 25000);
  setInterval(pollStatus, 4000);
});

// ── ZOOM: Ctrl+Scroll on editor container (NOT on Monaco div itself) ─
const edWrap = document.getElementById('ed-wrap');
edWrap.addEventListener('wheel', e=>{
  if(e.ctrlKey||e.metaKey){
    e.preventDefault(); e.stopImmediatePropagation();
    zE(e.deltaY<0?1:-1);
  }
},{passive:false, capture:true});

// ── Prevent page zoom on pinch (mobile) ─────────────────────────────
edWrap.addEventListener('touchstart', e=>{
  if(e.touches.length>1) e.preventDefault();
},{passive:false});

function zE(d){
  fsize=Math.max(8,Math.min(30,fsize+d));
  if(ed) ed.updateOptions({fontSize:fsize});
  document.getElementById('font-sz').textContent=fsize;
}

function doUndo(){ if(ed) ed.trigger('btn','undo',null); }
function doRedo(){ if(ed) ed.trigger('btn','redo',null); }

// ══════════════════════════════════════════════════════════════════════
//  FILE TREE
// ══════════════════════════════════════════════════════════════════════
async function refreshTree(){
  const data=await api('/editor/api/tree');
  tree=Array.isArray(data)?data:[];
  renderTree();
}

function renderTree(){
  const el=document.getElementById('tree');
  if(!tree.length){
    el.innerHTML='<div style="padding:18px;text-align:center;color:var(--text3)">No bots yet.<br>Click ＋ Bot to create one.</div>';
    return;
  }
  el.innerHTML=tree.map(b=>{
    const open=b.name===curBot;
    const st=(b.status||'').toLowerCase();
    const sc=st.includes('running')?'bst-r':st.includes('stop')||st.includes('error')?'bst-s':'bst-o';
    return `<div>
      <div class="bot-row ${open?'sel':''}" onclick="toggleBot('${b.name}',this)">
        <span class="caret ${open?'op':''}">▶</span>
        <span style="font-size:14px">🤖</span>
        <span class="b-name">${esc(b.name)}</span>
        <span class="bst ${sc}">${esc(b.status||'—')}</span>
      </div>
      <div class="bot-ch ${open?'op':''}" id="bc-${xid(b.name)}">
        ${(b.children||[]).map(c=>fNode(c,b.name)).join('')}
      </div>
    </div>`;
  }).join('');
}

function fNode(node,bot){
  const sel=node.path===curFile?'sel':'';
  if(node.type==='dir'){
    const did='fd-'+xid(node.path);
    return `<div>
      <div class="f-row f-dir-nm" onclick="toggleDir('${did}',this)">
        <span class="caret f-ico" id="c-${did}">▶</span>
        <span class="f-ico">📁</span>
        <span class="f-nm f-dir-nm">${esc(node.name)}</span>
      </div>
      <div class="f-dir-ch" id="${did}">
        ${(node.children||[]).map(c=>fNode(c,bot)).join('')}
      </div>
    </div>`;
  }
  return `<div class="f-row ${sel}"
    onclick="openFile('${node.path}','${esc(node.name)}','${esc(bot)}')">
    <span class="f-ico">${fIco(node.ext)}</span>
    <span class="f-nm">${esc(node.name)}</span>
  </div>`;
}

function toggleBot(name, hdr){
  const ch=document.getElementById('bc-'+xid(name));
  const cv=hdr.querySelector('.caret');
  const op=ch.classList.contains('op');
  if(!op) selBot(name);
  ch.classList.toggle('op',!op);
  cv.classList.toggle('op',!op);
}

function toggleDir(id,el){
  document.getElementById(id)?.classList.toggle('op');
  el.querySelector('.caret')?.classList.toggle('op');
}

function selBot(name){
  curBot=name;
  document.getElementById('bot-label').textContent='🤖 '+name;
  document.getElementById('btn-nf').style.display='';
  document.getElementById('btn-nd').style.display='';
  ulDest=name;
  connectWs(name);
  pollStatus();
  renderTree();
}

async function pollStatus(){
  if(!curBot) return;
  const b=tree.find(x=>x.name===curBot); if(!b) return;
  const dot=document.getElementById('status-dot');
  const st=(b.status||'').toLowerCase();
  dot.className='status-dot '+(st.includes('running')?'run':st.includes('error')?'err':'other');
  document.getElementById('status-dot').id='status-dot';
  dot.id='status-dot';
  if(st.includes('running')) dot.className='run',dot.style.background='var(--green)';
  else if(st.includes('error')||st.includes('stop')) dot.style.background='var(--red)';
  else dot.style.background='var(--yel)';
}

// ══════════════════════════════════════════════════════════════════════
//  FILE OPEN / TABS
// ══════════════════════════════════════════════════════════════════════
async function openFile(path,name,bot){
  if(curBot!==bot) selBot(bot);
  curFile=path;
  let tab=tabs.find(t=>t.path===path);
  if(tab){ actTab(tab); return; }
  const res=await api(`/editor/api/file?path=${encodeURIComponent(path)}`);
  if(res.error){ toast('❌ '+res.error,'err'); return; }
  const model=monaco.editor.createModel(res.content, gLang(name));
  tab={path,name,dirty:false,model,bot};
  tabs.push(tab); actTab(tab); renderTree();
}

function actTab(tab){
  aTab=tab;
  if(ed){ ed.setModel(tab.model); hidePH(); }
  curFile=tab.path;
  if(!curBot) selBot(tab.bot);
  renderTabs();
}

function closeTab(path,e){
  if(e) e.stopPropagation();
  const i=tabs.findIndex(t=>t.path===path); if(i<0) return;
  tabs[i].model.dispose(); tabs.splice(i,1);
  if(aTab?.path===path){
    aTab=tabs[i]||tabs[i-1]||null;
    if(aTab) actTab(aTab);
    else{ if(ed) ed.setModel(null); showPH(); }
  }
  renderTabs();
}

function renderTabs(){
  const el=document.getElementById('tabs');
  el.innerHTML=tabs.map(t=>`
    <div class="tab ${t===aTab?'act':''} ${t.dirty?'dirty':''}"
         onclick="actTab(tabs.find(x=>x.path==='${t.path}'))">
      <span class="f-ico" style="font-size:11px">${fIco(t.name.split('.').pop())}</span>
      <span class="tab-name">${esc(t.name)}</span>
      <span class="tab-x" onclick="closeTab('${t.path}',event)">✕</span>
    </div>`).join('');
}

// ══════════════════════════════════════════════════════════════════════
//  SAVE
// ══════════════════════════════════════════════════════════════════════
async function saveFile(){
  if(!aTab||!ed) return;
  const content=ed.getValue();
  const res=await api('/editor/api/file',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path:aTab.path,content}),
  });
  if(res.error){ toast('❌ '+res.error,'err'); return; }
  aTab.dirty=false; renderTabs(); toast('💾 Saved — '+aTab.name,'ok');
}

// ══════════════════════════════════════════════════════════════════════
//  RUN BOT  (stops old first)
// ══════════════════════════════════════════════════════════════════════
async function runBot(){
  if(!curBot){ toast('Select a bot first','err'); return; }
  if(aTab?.dirty) await saveFile();
  const btn=document.getElementById('btn-run');
  btn.classList.add('loading'); btn.textContent='⏳ Stopping…';
  const res=await api('/editor/api/run',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({bot:curBot}),
  });
  if(res.error){ toast('❌ '+res.error,'err'); btn.classList.remove('loading'); btn.textContent='▶ Run'; return; }
  toast('▶ '+curBot+' restarting…','info');
  setTimeout(()=>{ btn.classList.remove('loading'); btn.textContent='▶ Run'; refreshTree(); }, 3500);
}

// ══════════════════════════════════════════════════════════════════════
//  NEW BOT / FILE / DIR
// ══════════════════════════════════════════════════════════════════════
async function doNewBot(){
  const name=document.getElementById('nb-n').value.trim(); if(!name) return;
  const res=await api('/editor/api/newbot',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
  cM('m-newbot');
  if(res.error){ toast('❌ '+res.error,'err'); return; }
  toast('🤖 '+res.name+' created','ok'); await refreshTree(); openFile(res.main,'main.py',res.name);
}

async function doNewFile(){
  const name=document.getElementById('nf-n').value.trim(); if(!name||!curBot) return;
  const path=curBot+'/'+name;
  const res=await api('/editor/api/file',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({path,content:''})});
  cM('m-newfile');
  if(res.error){ toast('❌ '+res.error,'err'); return; }
  await refreshTree(); openFile(path,name,curBot);
}

async function doNewDir(){
  const name=document.getElementById('nd-n').value.trim(); if(!name||!curBot) return;
  const res=await api('/editor/api/mkdir',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({path:curBot+'/'+name})});
  cM('m-newdir');
  if(res.error){ toast('❌ '+res.error,'err'); return; }
  toast('📁 Folder created','ok'); refreshTree();
}

// ══════════════════════════════════════════════════════════════════════
//  DELETE FILE
// ══════════════════════════════════════════════════════════════════════
async function confirmDel(){
  if(!curFile){ toast('No file selected','err'); return; }
  if(!confirm('Delete '+curFile+'?\nThis cannot be undone.')) return;
  const res=await api('/editor/api/delete',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({path:curFile})});
  if(res.error){ toast('❌ '+res.error,'err'); return; }
  closeTab(curFile,null); curFile=''; toast('🗑 Deleted','ok'); refreshTree();
}

// ══════════════════════════════════════════════════════════════════════
//  UPLOAD
// ══════════════════════════════════════════════════════════════════════
function showUpload(){
  document.getElementById('ul-dest').textContent=ulDest||'/';
  oM('m-upload');
}
document.getElementById('finp').addEventListener('change',async e=>{
  await upFiles(e.target.files); e.target.value='';
});
document.getElementById('ul-drop').addEventListener('dragover',e=>e.preventDefault());
document.getElementById('ul-drop').addEventListener('drop',async e=>{
  e.preventDefault(); await upFiles(e.dataTransfer.files);
});
async function upFiles(files){
  if(!files.length) return;
  const fd=new FormData(); fd.append('dir',ulDest);
  for(const f of files) fd.append('files',f,f.name);
  const res=await fetch('/editor/api/upload?token='+TOK,{method:'POST',body:fd}).then(r=>r.json());
  cM('m-upload');
  if(res.error){ toast('❌ '+res.error,'err'); return; }
  toast('⬆ '+res.saved.length+' file(s) uploaded','ok'); refreshTree();
}

// Global drag-drop
const ov=document.getElementById('drop-ov');
document.addEventListener('dragenter',e=>{ if(e.dataTransfer.types.includes('Files')) ov.classList.add('show'); });
document.addEventListener('dragover',e=>e.preventDefault());
document.addEventListener('dragleave',e=>{ if(!e.relatedTarget) ov.classList.remove('show'); });
document.addEventListener('drop',async e=>{ e.preventDefault(); ov.classList.remove('show'); await upFiles(e.dataTransfer.files); });

// ══════════════════════════════════════════════════════════════════════
//  WEBSOCKET TERMINAL
// ══════════════════════════════════════════════════════════════════════
function connectWs(bot){
  if(logWs){ try{logWs.close()}catch(e){} }
  const tb=document.getElementById('term-body');
  tb.innerHTML=`<div class="tl tl-info">── Connecting to ${bot} ──</div>`;
  logWs=new WebSocket(`${WS}/editor/ws?token=${TOK}&bot=${encodeURIComponent(bot)}`);
  logWs.onmessage=e=>appendLog(e.data);
  logWs.onclose=()=>appendLog('── Stream closed ──');
  logWs.onerror=()=>appendLog('── Connection error ──');
}

function appendLog(txt){
  const tb=document.getElementById('term-body');
  const d=document.createElement('div');
  d.className='tl'+(
    /error|exception|traceback/i.test(txt)?' tl-err':
    /warn/i.test(txt)?' tl-warn':
    /\binfo\b|started|running|✅/i.test(txt)?' tl-info':
    /ok|success|done/i.test(txt)?' tl-ok':'');
  d.textContent=txt; tb.appendChild(d);
  if(tb.scrollHeight-tb.scrollTop<tb.clientHeight+60) tb.scrollTop=tb.scrollHeight;
  while(tb.children.length>2000) tb.removeChild(tb.firstChild);
}
function clearTerm(){ document.getElementById('term-body').innerHTML=''; }
function termBottom(){ const tb=document.getElementById('term-body'); tb.scrollTop=tb.scrollHeight; }

// ══════════════════════════════════════════════════════════════════════
//  PANEL TOGGLES + RESIZE
// ══════════════════════════════════════════════════════════════════════
function toggleSB(){
  sbOpen=!sbOpen;
  document.getElementById('sidebar').classList.toggle('hidden',!sbOpen);
  document.getElementById('btn-sb').classList.toggle('active',sbOpen);
  document.getElementById('rsz-h').style.display=sbOpen?'':'none';
  if(ed) ed.layout();
}

function toggleTerm(){
  tmOpen=!tmOpen;
  document.getElementById('term-pane').classList.toggle('hidden',!tmOpen);
  document.getElementById('rsz-v').style.display=tmOpen?'':'none';
  document.getElementById('btn-tm').classList.toggle('active',tmOpen);
  if(ed) ed.layout();
}

let _fs=false;
function toggleFullscreen(){
  _fs=!_fs;
  if(_fs){ if(sbOpen) toggleSB(); if(tmOpen) toggleTerm(); }
  else    { if(!sbOpen) toggleSB(); if(!tmOpen) toggleTerm(); }
}

// Sidebar drag-resize
(()=>{
  const rsz=document.getElementById('rsz-h');
  const sb=document.getElementById('sidebar');
  let dragging=false, startX=0, startW=0;
  rsz.addEventListener('mousedown',e=>{
    if(!sbOpen) return;
    dragging=true; startX=e.clientX; startW=sb.offsetWidth;
    rsz.classList.add('dragging'); document.body.style.cursor='col-resize';
    e.preventDefault();
  });
  document.addEventListener('mousemove',e=>{
    if(!dragging) return;
    const w=Math.max(140,Math.min(500,startW+(e.clientX-startX)));
    sb.style.width=w+'px'; document.documentElement.style.setProperty('--sb-w',w+'px');
    if(ed) ed.layout();
  });
  document.addEventListener('mouseup',()=>{
    dragging=false; rsz.classList.remove('dragging'); document.body.style.cursor='';
  });
})();

// Terminal drag-resize
(()=>{
  const rsz=document.getElementById('rsz-v');
  const tp=document.getElementById('term-pane');
  let dragging=false, startY=0, startH=0;
  rsz.addEventListener('mousedown',e=>{
    if(!tmOpen) return;
    dragging=true; startY=e.clientY; startH=tp.offsetHeight;
    rsz.classList.add('dragging'); document.body.style.cursor='row-resize';
    e.preventDefault();
  });
  document.addEventListener('mousemove',e=>{
    if(!dragging) return;
    const h=Math.max(80,Math.min(window.innerHeight*0.6, startH-(e.clientY-startY)));
    tp.style.height=h+'px';
    if(ed) ed.layout();
  });
  document.addEventListener('mouseup',()=>{
    dragging=false; rsz.classList.remove('dragging'); document.body.style.cursor='';
  });
})();

// ══════════════════════════════════════════════════════════════════════
//  KEYBOARD SHORTCUTS
// ══════════════════════════════════════════════════════════════════════
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA') return;
  const k=e.key.toLowerCase();
  if(k==='b') toggleSB();
  else if(k==='t') toggleTerm();
  else if(k==='f') toggleFullscreen();
  if((e.ctrlKey||e.metaKey)&&k==='s'){ e.preventDefault(); saveFile(); }
  if((e.ctrlKey||e.metaKey)&&k==='enter'){ e.preventDefault(); runBot(); }
});

// ══════════════════════════════════════════════════════════════════════
//  MODALS
// ══════════════════════════════════════════════════════════════════════
function oM(id){
  document.getElementById(id).classList.add('show');
  const inp=document.getElementById(id).querySelector('input');
  if(inp){ inp.value=''; setTimeout(()=>inp.focus(),40); }
}
function cM(id){ document.getElementById(id).classList.remove('show'); }
document.querySelectorAll('.moverlay').forEach(bg=>{
  bg.addEventListener('click',e=>{ if(e.target===bg) bg.classList.remove('show'); });
});
document.getElementById('nb-n').addEventListener('keydown',e=>e.key==='Enter'&&doNewBot());
document.getElementById('nf-n').addEventListener('keydown',e=>e.key==='Enter'&&doNewFile());
document.getElementById('nd-n').addEventListener('keydown',e=>e.key==='Enter'&&doNewDir());

// ══════════════════════════════════════════════════════════════════════
//  TOAST
// ══════════════════════════════════════════════════════════════════════
let _tt;
function toast(msg,type='ok'){
  const el=document.getElementById('toast');
  el.textContent=msg; el.className='show '+type;
  clearTimeout(_tt); _tt=setTimeout(()=>el.className='',3000);
}

// ══════════════════════════════════════════════════════════════════════
//  PLACEHOLDER
// ══════════════════════════════════════════════════════════════════════
function showPH(){ document.getElementById('placeholder').classList.remove('hide'); }
function hidePH(){ document.getElementById('placeholder').classList.add('hide'); }

// ══════════════════════════════════════════════════════════════════════
//  HELPERS
// ══════════════════════════════════════════════════════════════════════
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function xid(s){ return s.replace(/[^a-zA-Z0-9]/g,'_'); }
function fIco(e){
  return{py:'🐍',js:'📜',json:'🗂',txt:'📄',md:'📝',sh:'⚙️',
    yml:'⚙️',yaml:'⚙️',env:'🔑',html:'🌐',css:'🎨',
    png:'🖼',jpg:'🖼',gif:'🖼',zip:'📦',log:'📋',
    cfg:'⚙️',ini:'⚙️',toml:'⚙️',ts:'📜',sql:'🗄️'}[e]||'📄';
}
function gLang(n){
  const e=n.split('.').pop().toLowerCase();
  return{py:'python',js:'javascript',ts:'typescript',json:'json',
    html:'html',css:'css',sh:'shell',md:'markdown',
    yml:'yaml',yaml:'yaml',sql:'sql',txt:'plaintext',
    toml:'ini',cfg:'ini',ini:'ini'}[e]||'plaintext';
}
</script>
</body></html>"""

