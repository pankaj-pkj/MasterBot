"""
╔══════════════════════════════════════════════════════════════════════╗
║   MASTER HOSTING BOT  —  Web IDE  v5.0                              ║
║   Codian Studio 💎  ·  VS Code Dark Theme                           ║
╠══════════════════════════════════════════════════════════════════════╣
║  • NO device notifications (removed)                                 ║
║  • Per-user token — strict isolation, only owner's bots shown        ║
║  • Ownership validated on EVERY API call                             ║
║  • Web Run button: stop old → wait → start new (fixed)              ║
║  • Mobile zoom completely fixed (3 methods)                          ║
║  • VS Code / Replit layout: actbar + sidebar + editor + panel        ║
║  • Panel tabs: Output / Problems / (Terminal placeholder)            ║
║  • Problems tab: auto-populated from crash logs with fix hints       ║
║  • Drag-resize sidebar and panel                                     ║
║  • Editor tabs with dirty indicator                                  ║
║  • Status bar with bot name, cursor position, status                 ║
╚══════════════════════════════════════════════════════════════════════╝
"""
import os, json, time, uuid, shutil, asyncio, logging, mimetypes
from pathlib import Path
from typing  import Optional
from aiohttp import web

log = logging.getLogger("WebIDE")

HOSTED_DIR    = Path("hosted_bots")
DATA_DIR      = HOSTED_DIR / "_data"
DELETED_DIR   = HOSTED_DIR / "_deleted"
BLOCKED_NAMES = {"_data", "_deleted", ".git", "__pycache__"}

_RUNNING_BOTS : dict    = {}
_APP_REF               = None
_ADMIN_IDS: set[int]   = set()
_PENDING: dict         = {}

_TOKENS_FILE      = DATA_DIR / "user_tokens.json"
_BLOCKED_IPS_FILE = DATA_DIR / "ide_blocked_ips.json"


def init_editor(running_bots, app_ref=None, admin_ids=None):
    global _RUNNING_BOTS, _APP_REF, _ADMIN_IDS
    _RUNNING_BOTS = running_bots
    _APP_REF      = app_ref
    _ADMIN_IDS    = admin_ids or set()


# ══════════════════════════════════════════════════════════════════════
#  TOKEN / AUTH
# ══════════════════════════════════════════════════════════════════════
def _load_json(p: Path, d):
    try:    return json.loads(p.read_text())
    except: return d

def _save_json(p: Path, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))

def get_user_token(uid: int) -> str:
    tokens = _load_json(_TOKENS_FILE, {})
    for tok, stored in tokens.items():
        if stored == uid: return tok
    return _new_token(uid)

def rotate_user_token(uid: int) -> str:
    tokens = {t: u for t, u in _load_json(_TOKENS_FILE, {}).items() if u != uid}
    _save_json(_TOKENS_FILE, tokens)
    return _new_token(uid)

def _new_token(uid: int) -> str:
    tok    = uuid.uuid4().hex + uuid.uuid4().hex[:8]
    tokens = _load_json(_TOKENS_FILE, {})
    tokens[tok] = uid
    _save_json(_TOKENS_FILE, tokens)
    return tok

def _get_auth(req: web.Request) -> tuple[int, bool]:
    tok = req.rel_url.query.get("token") or req.cookies.get("ide_token", "")
    if not tok: return 0, False
    uid = _load_json(_TOKENS_FILE, {}).get(tok, 0)
    return uid, uid in _ADMIN_IDS

def _get_ip(req: web.Request) -> str:
    return req.headers.get("X-Forwarded-For", req.remote or "?").split(",")[0].strip()

def _is_blocked(ip: str) -> bool:
    return ip in _load_json(_BLOCKED_IPS_FILE, [])

def _block_ip(ip: str):
    bl = _load_json(_BLOCKED_IPS_FILE, [])
    if ip not in bl: bl.append(ip)
    _save_json(_BLOCKED_IPS_FILE, bl)


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
    if str(reg.get(bot_name, {}).get("owner_id", "")) == str(uid): return True
    if str(_RUNNING_BOTS.get(bot_name, {}).get("owner_id", "")) == str(uid): return True
    return False

def _user_bots(uid: int, is_admin: bool) -> list[str]:
    reg  = _load_registry()
    seen = set()
    bots = []
    src  = list(reg.keys()) if is_admin else [k for k,v in reg.items() if str(v.get("owner_id",""))==str(uid)]
    for k in src: bots.append(k); seen.add(k)
    for name, e in _RUNNING_BOTS.items():
        if name not in seen:
            if is_admin or str(e.get("owner_id",""))==str(uid):
                bots.append(name)
    return bots

def _safe_path(raw: str) -> Optional[Path]:
    try:
        full = (HOSTED_DIR / raw).resolve(); base = HOSTED_DIR.resolve()
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

def _display(bot_key: str) -> str:
    """Strip uid_ prefix for display."""
    parts = bot_key.split("_", 1)
    if len(parts) == 2 and parts[0].isdigit(): return parts[1]
    return bot_key

def _bot_tree(uid: int, is_admin: bool) -> list:
    if not HOSTED_DIR.exists(): return []
    allowed = set(_user_bots(uid, is_admin))
    bots    = []
    seen    = set()
    for item in sorted(HOSTED_DIR.iterdir()):
        if not item.is_dir() or item.name in BLOCKED_NAMES: continue
        if item.name not in allowed: continue
        seen.add(item.name)
        e = _RUNNING_BOTS.get(item.name, {})
        bots.append({"name": item.name,
                     "display": _display(item.name),
                     "path": item.name, "type": "bot",
                     "status":   e.get("status", "Offline 🔴"),
                     "restarts": e.get("restarts", 0),
                     "heals":    e.get("heal_tries", 0),
                     "uptime":   _fmt_up(time.time()-e["start_time"]) if e.get("start_time") else "—",
                     "has_files": (item / "main.py").exists(),
                     "children": _build_tree(item, item.name)})
    for name in allowed:
        if name in seen: continue
        e = _RUNNING_BOTS.get(name, {})
        if e.get("active"):
            bots.append({"name": name, "display": _display(name),
                         "path": name, "type": "bot",
                         "status": e.get("status","Running 🟢"),
                         "restarts": e.get("restarts",0),
                         "heals": e.get("heal_tries",0),
                         "uptime": _fmt_up(time.time()-e["start_time"]) if e.get("start_time") else "—",
                         "has_files": False, "children": []})
    return bots

def _tail(path: Path, n=120) -> str:
    try: return "\n".join(path.read_text(errors="replace").splitlines()[-n:])
    except: return ""


# ══════════════════════════════════════════════════════════════════════
#  API HANDLERS
# ══════════════════════════════════════════════════════════════════════

async def api_tree(req: web.Request) -> web.Response:
    if _is_blocked(_get_ip(req)): return web.json_response({"error":"blocked"},status=403)
    uid, ia = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    return web.json_response(_bot_tree(uid, ia))

async def api_read(req: web.Request) -> web.Response:
    uid, ia = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    raw = req.rel_url.query.get("path",""); bot = raw.split("/")[0]
    if not _can_access(uid, ia, bot): return web.json_response({"error":"forbidden"},status=403)
    path = _safe_path(raw)
    if not path or not path.exists() or not path.is_file():
        return web.json_response({"error":"File not found"},status=404)
    try:    content = path.read_text(errors="replace")[:524288]
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)
    return web.json_response({"path":raw,"name":path.name,"content":content,
                              "mime":mimetypes.guess_type(str(path))[0] or "text/plain"})

async def api_write(req: web.Request) -> web.Response:
    uid, ia = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body = await req.json(); raw = body.get("path",""); bot = raw.split("/")[0]
        if not _can_access(uid, ia, bot): return web.json_response({"error":"forbidden"},status=403)
        path = _safe_path(raw)
        if not path: return web.json_response({"error":"Invalid path"},status=400)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.get("content",""), encoding="utf-8")
        return web.json_response({"ok":True})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_delete(req: web.Request) -> web.Response:
    uid, ia = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body = await req.json(); raw = body.get("path",""); bot = raw.split("/")[0]
        if not _can_access(uid, ia, bot): return web.json_response({"error":"forbidden"},status=403)
        path = _safe_path(raw)
        if not path or not path.exists(): return web.json_response({"error":"Not found"},status=404)
        if path.parent.resolve()==HOSTED_DIR.resolve() and path.is_dir():
            return web.json_response({"error":"Use bot delete button."},status=400)
        shutil.rmtree(path) if path.is_dir() else path.unlink()
        return web.json_response({"ok":True})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_mkdir(req: web.Request) -> web.Response:
    uid, ia = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body = await req.json(); raw = body.get("path",""); bot = raw.split("/")[0]
        if not _can_access(uid, ia, bot): return web.json_response({"error":"forbidden"},status=403)
        path = _safe_path(raw)
        if not path: return web.json_response({"error":"Invalid path"},status=400)
        path.mkdir(parents=True, exist_ok=True)
        return web.json_response({"ok":True})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_upload(req: web.Request) -> web.Response:
    uid, ia = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        reader = await req.multipart(); dest_raw=""; saved=[]
        async for part in reader:
            if part.name=="dir": dest_raw=(await part.read()).decode().strip()
            elif part.filename:
                bot = dest_raw.split("/")[0] if dest_raw else ""
                if bot and not _can_access(uid, ia, bot): continue
                dest = _safe_path(dest_raw) if dest_raw else HOSTED_DIR
                if not dest: continue
                dest.mkdir(parents=True, exist_ok=True)
                fname = Path(part.filename).name
                (dest/fname).write_bytes(await part.read())
                saved.append((dest/fname).relative_to(HOSTED_DIR).as_posix())
        return web.json_response({"ok":True,"saved":saved})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_newbot(req: web.Request) -> web.Response:
    uid, ia = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body = await req.json()
        raw  = body.get("name","").strip().replace(" ","_").replace("/","")
        if not raw: return web.json_response({"error":"Name required"},status=400)
        # Always uid-prefix to avoid collision
        import re
        clean   = re.sub(r"[^a-zA-Z0-9_-]","_",raw)[:40]
        bot_key = f"{uid}_{clean}"
        bot_dir = HOSTED_DIR / bot_key
        if bot_dir.exists(): return web.json_response({"error":f"'{clean}' already exists"},status=409)
        bot_dir.mkdir(parents=True)
        (bot_dir/"main.py").write_text(
            f'# {clean}  —  Codian Studio 💎\n\nprint("Hello from {clean}!")\n', encoding="utf-8")
        (bot_dir/"requirements.txt").write_text("# Add dependencies here\n", encoding="utf-8")
        try:
            reg = _load_registry()
            reg[bot_key] = {"owner_id": uid, "registered_at": time.time(),
                            "display_name": clean}
            (DATA_DIR/"registry.json").write_text(json.dumps(reg, indent=2))
        except: pass
        return web.json_response({"ok":True,"name":bot_key,"display":clean,"main":f"{bot_key}/main.py"})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_run(req: web.Request) -> web.Response:
    """
    Run button fix: stop old process cleanly, write restart_flag,
    run_bot loop picks it up and starts fresh without freezing.
    """
    uid, ia = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body     = await req.json(); bot_name = body.get("bot","").strip()
        if not bot_name: return web.json_response({"error":"Bot name required"},status=400)
        if not _can_access(uid, ia, bot_name):
            return web.json_response({"error":"forbidden"},status=403)
        bot_dir = HOSTED_DIR / bot_name
        if not (bot_dir/"main.py").exists():
            return web.json_response({"error":"main.py not found"},status=404)
        # Step 1: stop existing process
        e = _RUNNING_BOTS.get(bot_name)
        if e:
            e["active"] = False
            p = e.get("process")
            if p and p.poll() is None:
                p.terminate()
                # Wait max 4s without blocking event loop
                for _ in range(8):
                    await asyncio.sleep(0.5)
                    if p.poll() is not None: break
                else:
                    try: p.kill()
                    except: pass
            e["status"] = "Stopped 🛑"
        # Step 2: write restart flag — run_bot picks this up
        # Also re-activate the entry so run_bot loop continues
        if bot_name in _RUNNING_BOTS:
            _RUNNING_BOTS[bot_name]["active"] = True
            _RUNNING_BOTS[bot_name]["status"] = "Restarting ⏳"
        (bot_dir/".restart_flag").write_text(str(time.time()))
        return web.json_response({"ok":True,"bot":bot_name,"msg":"Restarting…"})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_logs(req: web.Request) -> web.Response:
    uid, ia = _get_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    bot_name = req.rel_url.query.get("bot","")
    if not _can_access(uid, ia, bot_name):
        return web.json_response({"error":"forbidden"},status=403)
    n  = min(int(req.rel_url.query.get("n",150)), 500)
    lp = HOSTED_DIR / bot_name / "bot_output.log"
    lines = _tail(lp, n).splitlines() if _tail(lp, n) else []
    e = _RUNNING_BOTS.get(bot_name, {})
    return web.json_response({"bot":bot_name,"lines":lines,
                              "status":e.get("status","Offline"),
                              "uptime":_fmt_up(time.time()-e["start_time"]) if e.get("start_time") else "—",
                              "restarts":e.get("restarts",0),
                              "heals":e.get("heal_tries",0)})

async def ws_logs(req: web.Request) -> web.WebSocketResponse:
    tok = req.rel_url.query.get("token",""); bot_name = req.rel_url.query.get("bot","")
    uid = _load_json(_TOKENS_FILE,{}).get(tok,0)
    if not uid: raise web.HTTPUnauthorized()
    if not _can_access(uid, uid in _ADMIN_IDS, bot_name): raise web.HTTPForbidden()
    ws = web.WebSocketResponse(heartbeat=20); await ws.prepare(req)
    lp = HOSTED_DIR/bot_name/"bot_output.log"; pos=0
    try:
        text  = lp.read_text(errors="replace"); lines = text.splitlines()[-80:]
        for ln in lines: await ws.send_str(ln)
        pos = lp.stat().st_size
    except: pass
    try:
        while not ws.closed:
            await asyncio.sleep(0.8)
            try:
                size = lp.stat().st_size
                if size > pos:
                    with open(lp,"r",errors="replace") as f:
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

async def editor_page(req: web.Request) -> web.Response:
    if _is_blocked(_get_ip(req)): return web.Response(text="403 Blocked",status=403)
    tok = req.rel_url.query.get("token","")
    uid = _load_json(_TOKENS_FILE,{}).get(tok,0)
    if uid:
        resp = web.HTTPFound(location="/editor")
        resp.set_cookie("ide_token",tok,max_age=86400*30,httponly=True,samesite="Strict")
        return resp
    uid2,_ = _get_auth(req)
    if not uid2: return web.Response(text=_LOGIN_HTML,content_type="text/html",status=401)
    return web.Response(text=_IDE_HTML,content_type="text/html")

def register_routes(app: web.Application):
    app.router.add_get("/editor",             editor_page)
    app.router.add_get("/editor/api/tree",    api_tree)
    app.router.add_get("/editor/api/file",    api_read)
    app.router.add_post("/editor/api/file",   api_write)
    app.router.add_post("/editor/api/delete", api_delete)
    app.router.add_post("/editor/api/mkdir",  api_mkdir)
    app.router.add_post("/editor/api/upload", api_upload)
    app.router.add_post("/editor/api/newbot", api_newbot)
    app.router.add_post("/editor/api/run",    api_run)
    app.router.add_get("/editor/api/logs",    api_logs)
    app.router.add_get("/editor/ws",          ws_logs)
    log.info("Web IDE v5.0 routes ready")


# ══════════════════════════════════════════════════════════════════════
#  LOGIN PAGE
# ══════════════════════════════════════════════════════════════════════
_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Codian Studio</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{background:#1e1e1e;color:#d4d4d4;font-family:'Segoe UI',system-ui,sans-serif;
     display:flex;align-items:center;justify-content:center;height:100svh;padding:20px}
.card{background:#252526;border:1px solid #3c3c3c;border-radius:6px;
      padding:34px 26px;width:100%;max-width:340px;text-align:center}
.logo{font-size:2.6rem;margin-bottom:10px}
h1{color:#4fc3f7;font-size:.98rem;margin-bottom:4px;font-weight:600}
p{color:#858585;font-size:.8rem;margin-bottom:22px}
input{width:100%;background:#3c3c3c;border:1px solid #555;border-radius:4px;
      padding:10px 12px;color:#d4d4d4;font-size:15px;outline:none;
      margin-bottom:12px;font-family:monospace;-webkit-appearance:none}
input:focus{border-color:#0e639c}
button{width:100%;background:#0e639c;border:none;border-radius:4px;
       padding:10px;color:#fff;font-size:14px;font-weight:600;cursor:pointer;
       touch-action:manipulation}
button:hover{background:#1177bb}
.hint{color:#858585;font-size:.74rem;margin-top:12px;line-height:1.6}
code{background:#3c3c3c;padding:2px 5px;border-radius:3px;color:#9cdcfe}
</style></head><body>
<div class="card">
  <div class="logo">💎</div>
  <h1>Codian Studio IDE</h1>
  <p>Master Hosting Bot — Web Editor</p>
  <input type="password" id="t" placeholder="Paste your access token"
         autocomplete="off" autocorrect="off" autocapitalize="off"
         onkeydown="if(event.key==='Enter')go()">
  <button onclick="go()">Sign In →</button>
  <p class="hint">Get your token from the bot: <code>/ide</code></p>
</div>
<script>
function go(){var t=document.getElementById('t').value.trim();
  if(!t)return;window.location.href='/editor?token='+encodeURIComponent(t);}
</script></body></html>"""


# ══════════════════════════════════════════════════════════════════════
#  IDE HTML — VS Code / Replit exact layout
# ══════════════════════════════════════════════════════════════════════
_IDE_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,minimum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Codian Studio 💎</title>
<style>
/* ZOOM KILL — all methods */
html{touch-action:none;-webkit-text-size-adjust:100%;text-size-adjust:100%;overflow:hidden;height:100%}
body{height:100svh;overflow:hidden;touch-action:none}
*{-webkit-tap-highlight-color:transparent;touch-action:manipulation;box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#1e1e1e;--bg1:#252526;--bg2:#2d2d2d;--bg3:#3c3c3c;
  --border:#3c3c3c;--blue:#0e639c;--blue2:#4fc3f7;
  --green:#4ec9b0;--green2:#89d185;--red:#f44747;
  --yel:#dcdcaa;--pur:#c586c0;--text:#d4d4d4;--text2:#858585;--text3:#555;
  --act:46px;--sb:220px;--bar:22px;--tab:35px;--panel:200px;--status:22px;
}
#root{display:flex;flex-direction:column;height:100svh;background:var(--bg);
      color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;font-size:13px;
      -webkit-font-smoothing:antialiased}
/* ─ Menu bar ─ */
#mbar{height:var(--bar);background:#323233;border-bottom:1px solid #111;
      display:flex;align-items:center;padding:0 10px;gap:8px;flex-shrink:0;user-select:none}
.logo{font-weight:800;color:var(--blue2);font-size:.85rem;margin-right:6px}
.mitem{font-size:12px;color:var(--text2);padding:2px 7px;border-radius:3px;cursor:pointer;
       touch-action:manipulation;white-space:nowrap}
.mitem:hover{background:var(--bg3);color:var(--text)}
.msep{width:1px;height:14px;background:var(--border)}
.mspace{flex:1}
#mbar-bot{font-size:11px;color:var(--pur);font-weight:600;max-width:140px;
          overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* ─ Middle ─ */
#mid{display:flex;flex:1;overflow:hidden;min-height:0}
/* ─ Activity bar ─ */
#act{width:var(--act);flex-shrink:0;background:#333;border-right:1px solid #111;
     display:flex;flex-direction:column;align-items:center;padding:4px 0;gap:2px}
.ab{width:38px;height:38px;border-radius:6px;display:flex;align-items:center;
    justify-content:center;cursor:pointer;font-size:18px;color:var(--text3);
    border:1px solid transparent;touch-action:manipulation;transition:.1s;position:relative}
.ab:hover{color:var(--text);background:#3d3d3d}
.ab.on{color:var(--text);border-color:var(--blue)}
.ab.on::before{content:'';position:absolute;left:-1px;top:8px;bottom:8px;
               width:2px;background:var(--blue2);border-radius:0 2px 2px 0}
.ab-sp{flex:1}
/* ─ Sidebar ─ */
#sb{width:var(--sb);min-width:120px;max-width:480px;flex-shrink:0;
    display:flex;flex-direction:column;background:var(--bg1);border-right:1px solid #111;overflow:hidden}
#sb.hide{display:none}
.sbsec{height:22px;display:flex;align-items:center;padding:0 10px;
       font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;
       color:var(--text2);flex-shrink:0;user-select:none}
.sbsec .acts{display:flex;gap:2px;margin-left:auto}
.ib{background:none;border:none;color:var(--text2);cursor:pointer;
    padding:2px 4px;border-radius:3px;font-size:13px;line-height:1;touch-action:manipulation}
.ib:hover{background:var(--bg3);color:var(--text)}
#tree{flex:1;overflow-y:auto;overflow-x:hidden;scrollbar-width:thin;
      scrollbar-color:#555 transparent;touch-action:pan-y}
#tree::-webkit-scrollbar{width:4px}
#tree::-webkit-scrollbar-thumb{background:#555}
/* ─ SB resize ─ */
#rsb{width:4px;flex-shrink:0;cursor:col-resize;background:transparent;transition:background .12s}
#rsb.on,#rsb:hover{background:var(--blue)}
/* ─ Editor area ─ */
#eda{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0;min-height:0}
/* ─ Editor tab strip ─ */
#etabs{height:var(--tab);flex-shrink:0;background:var(--bg1);border-bottom:1px solid #111;
       display:flex;overflow-x:auto;overflow-y:hidden;touch-action:pan-x}
#etabs::-webkit-scrollbar{height:2px}
#etabs::-webkit-scrollbar-thumb{background:var(--border)}
.etab{display:inline-flex;align-items:center;gap:5px;padding:0 12px;height:100%;
      flex-shrink:0;cursor:pointer;font-size:12.5px;color:var(--text2);
      border-right:1px solid #111;white-space:nowrap;touch-action:manipulation;position:relative}
.etab:hover{color:var(--text);background:#2a2a2a}
.etab.act{background:var(--bg);color:var(--text);border-top:1px solid var(--blue)}
.etab.dirty .en::after{content:'●';color:var(--yel);margin-left:4px;font-size:9px}
.etab-x{opacity:0;font-size:12px;border-radius:3px;padding:1px 3px;
         line-height:1;touch-action:manipulation;transition:.1s}
.etab:hover .etab-x{opacity:.5}
.etab-x:hover{opacity:1!important;background:var(--red);color:#fff}
.wtab{background:#252526;color:var(--text2)}
/* ─ Monaco wrap — THE FIX ─ */
#ew{flex:1;position:relative;overflow:hidden;min-height:0;background:#1e1e1e;touch-action:none}
#mc{position:absolute;inset:0;touch-action:none;overflow:hidden}
/* ─ Welcome ─ */
#wlc{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
     justify-content:center;color:var(--text3);gap:14px;background:#1e1e1e}
#wlc.hide{display:none}
#wlc .wi{font-size:3.5rem}
#wlc h2{font-size:1rem;color:var(--text2);font-weight:400}
#wlc kbd{background:var(--bg3);border:1px solid #555;border-radius:3px;
          padding:1px 6px;font-family:monospace;font-size:11px;color:var(--text)}
/* ─ Panel resize ─ */
#rp{height:4px;flex-shrink:0;cursor:row-resize;background:var(--border);
    transition:background .12s;touch-action:none}
#rp.on,#rp:hover{background:var(--blue)}
/* ─ Bottom panel ─ */
#panel{height:var(--panel);min-height:60px;max-height:65vh;flex-shrink:0;
       display:flex;flex-direction:column;background:var(--bg1);border-top:1px solid #111}
#panel.hide{height:0!important;min-height:0;border-top:none;overflow:hidden}
#ptabs{height:35px;flex-shrink:0;background:var(--bg2);border-bottom:1px solid #111;
       display:flex;align-items:center;padding:0 6px;gap:0}
.ptab{height:100%;display:inline-flex;align-items:center;padding:0 14px;
      font-size:12px;color:var(--text2);cursor:pointer;border-bottom:1px solid transparent;
      touch-action:manipulation}
.ptab:hover{color:var(--text)}
.ptab.act{color:var(--text);border-bottom-color:var(--blue2)}
.ptsp{flex:1}
.pbtn{padding:2px 6px;font-size:12px;color:var(--text2);cursor:pointer;
      border-radius:3px;touch-action:manipulation}
.pbtn:hover{background:var(--bg3);color:var(--text)}
#pbody{flex:1;position:relative;overflow:hidden}
.pv{position:absolute;inset:0;overflow-y:auto;padding:5px 10px;
    font-family:'Cascadia Code','Fira Code',Consolas,monospace;font-size:12px;
    line-height:1.7;color:#d4d4d4;scrollbar-width:thin;scrollbar-color:#555 transparent;
    touch-action:pan-y;display:none}
.pv.act{display:block}
.pv::-webkit-scrollbar{width:4px}
.pv::-webkit-scrollbar-thumb{background:#555}
/* Log lines */
.ll{white-space:pre-wrap;word-break:break-all}
.le{color:#f44747}.lw{color:#dcdcaa}.lo{color:#4ec9b0}.li{color:#4fc3f7}.ls{color:#555}
/* Problems */
.pi{display:flex;align-items:flex-start;gap:8px;padding:4px 6px;border-radius:3px;
    margin-bottom:2px;font-size:12px}
.pi:hover{background:var(--bg3)}
.pico{flex-shrink:0;font-size:13px}
.ploc{color:var(--text2);font-size:11px;margin-top:1px}
.pmsg{flex:1}
.phint{margin-top:4px;padding:6px 8px;background:#2d2d2d;border-left:2px solid var(--blue);
       border-radius:0 3px 3px 0;font-size:11px;color:#9cdcfe}
#pcnt{display:inline-block;background:var(--bg3);border-radius:10px;
      padding:1px 6px;font-size:10px;color:var(--text2);margin-left:4px}
/* ─ Status bar ─ */
#sbar{height:var(--status);flex-shrink:0;background:var(--blue);display:flex;
      align-items:center;padding:0 10px;gap:12px;font-size:11.5px;color:#fff;user-select:none}
#sb-bot{font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#sb-ln{opacity:.8;font-family:monospace}
#sb-st{opacity:.85}
.ecnt{background:#f44747;color:#fff;padding:0 6px;border-radius:10px;
      font-size:10px;font-weight:700;display:none}
/* ─ Tree ─ */
.bi{padding:1px 0}
.bh{display:flex;align-items:center;gap:4px;padding:4px 10px;cursor:pointer;
    border-left:2px solid transparent;font-size:13px;touch-action:manipulation}
.bh:hover{background:#2a2a2a}
.bh.sel{background:#094771;border-left-color:var(--blue2)}
.car{font-size:9px;color:var(--text3);flex-shrink:0;width:10px;
     display:inline-block;transition:transform .15s}
.car.op{transform:rotate(90deg)}
.bn{flex:1;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bst{font-size:9px;padding:1px 5px;border-radius:10px;flex-shrink:0;white-space:nowrap}
.br{background:#143014;color:#4ec9b0}.bs{background:#3a1414;color:#f44747}.bo{background:#333;color:var(--text2)}
.bc{display:none;padding-left:10px}.bc.op{display:block}
.fi{display:flex;align-items:center;gap:5px;padding:2px 8px;cursor:pointer;
    font-size:12.5px;touch-action:manipulation}
.fi:hover{background:#2a2a2a}
.fi.sel{background:#094771}
.dc{display:none;padding-left:12px}.dc.op{display:block}
.fic{font-size:12px;flex-shrink:0;width:15px}
.fn{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fd{color:#dcdcaa}
/* ─ Modals ─ */
.mov{display:none;position:fixed;inset:0;background:#000000cc;align-items:center;
     justify-content:center;z-index:300;padding:16px;touch-action:none}
.mov.show{display:flex}
.modal{background:#252526;border:1px solid #555;border-radius:6px;padding:20px;
       width:100%;max-width:380px}
.modal h3{margin-bottom:14px;color:var(--blue2);font-size:.95rem;font-weight:600}
.modal input{width:100%;background:#3c3c3c;border:1px solid #555;border-radius:4px;
             padding:9px 11px;color:var(--text);font-size:14px;outline:none;
             margin-bottom:11px;-webkit-appearance:none}
.modal input:focus{border-color:var(--blue)}
.mbtns{display:flex;gap:8px;justify-content:flex-end}
.mok{padding:7px 16px;border-radius:4px;border:none;background:var(--blue);
     color:#fff;cursor:pointer;font-size:13px;touch-action:manipulation}
.mok:hover{background:#1177bb}
.mcancel{padding:7px 16px;border-radius:4px;border:1px solid #555;
         background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px;touch-action:manipulation}
/* ─ Toast ─ */
#toast{position:fixed;bottom:30px;right:16px;background:#252526;border-radius:4px;
       padding:9px 14px;font-size:12.5px;opacity:0;pointer-events:none;z-index:500;
       max-width:260px;border:1px solid var(--border);transition:opacity .18s}
#toast.show{opacity:1}
#toast.ok{border-color:var(--green);color:var(--green2)}
#toast.err{border-color:var(--red);color:var(--red)}
#toast.info{border-color:var(--blue);color:var(--blue2)}
#finp{display:none}
#dov{display:none;position:fixed;inset:0;background:#0e639c22;border:2px dashed var(--blue);
     z-index:400;align-items:center;justify-content:center;font-size:1.2rem;
     color:var(--blue2);pointer-events:none}
#dov.show{display:flex}
@media(max-width:600px){:root{--sb:190px;--act:42px;--panel:160px}.mitem{display:none}}
</style></head><body>
<input type="file" id="finp" multiple>
<div id="dov">📁 Drop to upload</div>
<div id="toast"></div>
<!-- Modals -->
<div class="mov" id="m-nb"><div class="modal"><h3>🤖 New Bot</h3>
  <input id="nb-n" placeholder="bot-name" autocorrect="off" autocapitalize="off">
  <div class="mbtns"><button class="mcancel" onclick="cM('m-nb')">Cancel</button>
    <button class="mok" onclick="doNB()">Create</button></div></div></div>
<div class="mov" id="m-nf"><div class="modal"><h3>📄 New File</h3>
  <input id="nf-n" placeholder="filename.py" autocorrect="off" autocapitalize="off">
  <div class="mbtns"><button class="mcancel" onclick="cM('m-nf')">Cancel</button>
    <button class="mok" onclick="doNF()">Create</button></div></div></div>
<div class="mov" id="m-nd"><div class="modal"><h3>📁 New Folder</h3>
  <input id="nd-n" placeholder="folder-name" autocorrect="off" autocapitalize="off">
  <div class="mbtns"><button class="mcancel" onclick="cM('m-nd')">Cancel</button>
    <button class="mok" onclick="doND()">Create</button></div></div></div>
<div class="mov" id="m-ul"><div class="modal"><h3>⬆️ Upload</h3>
  <p style="color:var(--text2);font-size:12px;margin-bottom:12px">To: <b id="ul-d">/</b></p>
  <button class="mok" style="width:100%;margin-bottom:10px"
    onclick="document.getElementById('finp').click()">Choose Files…</button>
  <div style="border:1px dashed #555;border-radius:4px;padding:14px;text-align:center;
       color:var(--text3);font-size:12px">or drag & drop files anywhere</div>
  <div class="mbtns" style="margin-top:12px">
    <button class="mcancel" onclick="cM('m-ul')">Close</button></div></div></div>
<!-- APP -->
<div id="root">
  <div id="mbar">
    <span class="logo">💎</span><div class="msep"></div>
    <span class="mitem" onclick="oM('m-nb')">＋ Bot</span>
    <span class="mitem" id="mb-nf" style="display:none" onclick="oM('m-nf')">New File</span>
    <span class="mitem" onclick="showUL()">Upload</span>
    <span class="mitem" onclick="save()">Save</span>
    <span class="mitem" onclick="run()">▶ Run</span>
    <div class="mspace"></div>
    <span class="mitem" id="mbar-bot">No bot selected</span>
  </div>
  <div id="mid">
    <div id="act">
      <div class="ab on" id="ab-e" onclick="tSB(true)" title="Explorer [B]">📁</div>
      <div class="ab" onclick="toast('Search — coming soon','info')">🔍</div>
      <div class="ab-sp"></div>
      <div class="ab" onclick="run()" title="Run [Ctrl+Enter]">▶</div>
      <div class="ab" onclick="showUL()" title="Upload">⬆</div>
    </div>
    <div id="sb">
      <div class="sbsec">Explorer<div class="acts">
        <button class="ib" onclick="refresh()" title="Refresh">⟳</button>
        <button class="ib" onclick="oM('m-nb')" title="New Bot">＋</button>
        <button class="ib" id="sb-nf" style="display:none" onclick="oM('m-nf')">📄</button>
        <button class="ib" id="sb-nd" style="display:none" onclick="oM('m-nd')">📁</button>
      </div></div>
      <div id="tree"><div style="padding:20px;text-align:center;color:var(--text3);font-size:12px">
        Loading…</div></div>
    </div>
    <div id="rsb"></div>
    <div id="eda">
      <div id="etabs">
        <div class="etab wtab act" id="tw">Welcome</div>
      </div>
      <div id="ew">
        <div id="mc"></div>
        <div id="wlc">
          <div class="wi">💎</div>
          <h2>Codian Studio</h2>
          <p style="font-size:12px;margin-top:6px">Select a file from Explorer</p>
          <p style="font-size:11px;margin-top:8px">
            <kbd>B</kbd> sidebar &nbsp;<kbd>T</kbd> panel &nbsp;
            <kbd>Ctrl+S</kbd> save &nbsp;<kbd>Ctrl+Enter</kbd> run</p>
        </div>
      </div>
      <div id="rp"></div>
      <div id="panel">
        <div id="ptabs">
          <div class="ptab act" id="pt-o" onclick="showP('o')">Output</div>
          <div class="ptab" id="pt-p" onclick="showP('p')">
            Problems<span id="pcnt">0</span></div>
          <div class="ptab" id="pt-t" onclick="showP('t')">Terminal</div>
          <div class="ptsp"></div>
          <span class="pbtn" onclick="clrP()">✕</span>
          <span class="pbtn" onclick="pBot()">⬇</span>
          <span class="pbtn" onclick="tPanel()">⊟</span>
        </div>
        <div id="pbody">
          <div class="pv act" id="pv-o"></div>
          <div class="pv" id="pv-p"><div style="padding:10px;color:var(--text2);font-size:12px">
            No problems. Run your bot to check for errors.</div></div>
          <div class="pv" id="pv-t"><div style="padding:10px;color:var(--text2);font-size:12px">
            Shell terminal — coming in next update.</div></div>
        </div>
      </div>
    </div>
  </div>
  <div id="sbar">
    <span class="ecnt" id="ecnt">0</span>
    <span id="sb-bot">💎 Codian Studio</span>
    <span id="sb-st">Ready</span>
    <span id="sb-ln">Ln 1, Col 1</span>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.46.0/min/vs/loader.min.js"></script>
<script>
'use strict';
// ── ANTI-ZOOM (3 methods) ────────────────────────────────
document.addEventListener('gesturestart', e=>e.preventDefault(),{passive:false});
document.addEventListener('gesturechange',e=>e.preventDefault(),{passive:false});
document.addEventListener('gestureend',   e=>e.preventDefault(),{passive:false});
let _lt=0;
document.addEventListener('touchend',e=>{const n=Date.now();if(n-_lt<280)e.preventDefault();_lt=n;},{passive:false});
document.addEventListener('wheel',e=>{if(e.ctrlKey||e.metaKey)e.preventDefault();},{passive:false});
document.addEventListener('touchstart',e=>{if(e.touches.length>1)e.preventDefault();},{passive:false});
// ── State ────────────────────────────────────────────────
const TOK=(document.cookie.match(/ide_token=([^;]+)/)||[])[1]||'';
const WS=(location.protocol==='https:'?'wss://':'ws://')+location.host;
const api=async(u,o={})=>{try{const q=u.includes('?')?'&':'?';return(await fetch(u+q+'token='+TOK,o)).json();}catch(e){return{error:String(e)};}};
let ed=null,fsize=14,tdata=[],tabs=[],aTab=null,curBot='',curFile='',logWs=null,ulDest='',sbVis=true,pVis=true,curP='o',probs=[];
// ── Monaco ───────────────────────────────────────────────
require.config({paths:{vs:'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.46.0/min/vs'}});
require(['vs/editor/editor.main'],()=>{
  ed=monaco.editor.create(document.getElementById('mc'),{
    value:'',language:'python',theme:'vs-dark',fontSize:fsize,
    fontFamily:"'Cascadia Code','Fira Code',Consolas,monospace",
    fontLigatures:true,automaticLayout:true,minimap:{enabled:false},
    scrollBeyondLastLine:false,lineNumbers:'on',glyphMargin:true,folding:true,
    wordWrap:'off',smoothScrolling:true,cursorBlinking:'smooth',
    cursorSmoothCaretAnimation:'on',renderLineHighlight:'line',
    bracketPairColorization:{enabled:true},padding:{top:8,bottom:8},
    scrollbar:{vertical:'auto',horizontal:'auto',useShadows:false,
               verticalScrollbarSize:6,horizontalScrollbarSize:6,alwaysConsumeMouseWheel:true},
    mouseWheelZoom:false,
    quickSuggestions:{other:true,comments:false,strings:false},
  });
  ed.onDidChangeCursorPosition(e=>{const p=ed.getPosition();if(p)document.getElementById('sb-ln').textContent=`Ln ${p.lineNumber}, Col ${p.column}`;});
  ed.onDidChangeModelContent(()=>{if(aTab&&aTab.type==='f'){aTab.dirty=true;rTabs();}});
  ed.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.KeyS,save);
  ed.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.Enter,run);
  ed.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.Equal,()=>zE(1));
  ed.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.Minus,()=>zE(-1));
  refresh(); setInterval(refresh,25000); setInterval(syncSt,4500);
});
document.getElementById('ew').addEventListener('wheel',e=>{if(e.ctrlKey||e.metaKey){e.preventDefault();e.stopImmediatePropagation();zE(e.deltaY<0?1:-1);}},{passive:false,capture:true});
function zE(d){fsize=Math.max(8,Math.min(28,fsize+d));if(ed)ed.updateOptions({fontSize:fsize});}
function undo(){if(ed)ed.trigger('b','undo',null);}
function redo(){if(ed)ed.trigger('b','redo',null);}
// ── Tree ─────────────────────────────────────────────────
async function refresh(){const d=await api('/editor/api/tree');if(d.error)return;tdata=Array.isArray(d)?d:[];rTree();}
function rTree(){
  const el=document.getElementById('tree');
  if(!tdata.length){el.innerHTML='<div style="padding:18px;text-align:center;color:var(--text3);font-size:12px">No bots.<br>Click ＋ to create one.</div>';return;}
  el.innerHTML=tdata.map(b=>{
    const op=b.name===curBot;
    const st=(b.status||'').toLowerCase();
    const sc=st.includes('running')?'br':st.includes('stop')||st.includes('error')?'bs':'bo';
    return `<div class="bi"><div class="bh ${op?'sel':''}" onclick="tBot('${x(b.name)}','${x(b.display||b.name)}',this)">
      <span class="car ${op?'op':''}">▶</span><span style="font-size:14px;margin:0 3px">🤖</span>
      <span class="bn">${x(b.display||b.name)}</span>
      <span class="bst ${sc}">${x(b.status||'—')}</span>
    </div>
    <div class="bc ${op?'op':''}" id="bc-${i(b.name)}">
      ${!b.has_files?'<div style="padding:6px 14px;color:var(--text3);font-size:11px">Running in memory — files not found locally</div>'
        :(b.children||[]).map(c=>fN(c,b.name)).join('')}
    </div></div>`;
  }).join('');
}
function fN(n,bot){
  const sel=n.path===curFile?'sel':'';
  if(n.type==='dir'){const did='d-'+i(n.path);return `<div><div class="fi fd" onclick="tDir('${did}',this)">
    <span class="fic car" id="c-${did}">▶</span><span class="fic">📁</span>
    <span class="fn fd">${x(n.name)}</span></div>
    <div class="dc" id="${did}">${(n.children||[]).map(c=>fN(c,bot)).join('')}</div></div>`;}
  return `<div class="fi ${sel}" onclick="openF('${x(n.path)}','${x(n.name)}','${x(bot)}')">
    <span class="fic">${fi(n.ext)}</span><span class="fn">${x(n.name)}</span></div>`;}
function tBot(name,disp,hdr){const ch=document.getElementById('bc-'+i(name));const cv=hdr.querySelector('.car');const op=ch.classList.contains('op');if(!op)selBot(name,disp);ch.classList.toggle('op',!op);cv.classList.toggle('op',!op);}
function tDir(id,el){document.getElementById(id)?.classList.toggle('op');el.querySelector('.car')?.classList.toggle('op');}
function selBot(name,disp){curBot=name;const d=disp||name;document.getElementById('mbar-bot').textContent='🤖 '+d;document.getElementById('sb-bot').textContent='🤖 '+d;document.getElementById('mb-nf').style.display='';document.getElementById('sb-nf').style.display='';document.getElementById('sb-nd').style.display='';ulDest=name;connectWs(name);syncSt();rTree();}
function syncSt(){if(!curBot)return;const b=tdata.find(t=>t.name===curBot);if(!b)return;const st=(b.status||'').toLowerCase();const col=st.includes('running')?'#4ec9b0':st.includes('error')?'#f44747':'#dcdcaa';const ss=document.getElementById('sb-st');ss.textContent=b.status||'—';ss.style.color=col;}
// ── Tabs ──────────────────────────────────────────────────
function rTabs(){const el=document.getElementById('etabs');el.querySelectorAll('.etab[data-p]').forEach(e=>e.remove());const tw=document.getElementById('tw');const hf=tabs.filter(t=>t.type==='f').length>0;if(tw)tw.style.display=hf?'none':'';tabs.filter(t=>t.type==='f').forEach(t=>{const d=document.createElement('div');d.className='etab'+(t===aTab?' act':'')+(t.dirty?' dirty':'');d.dataset.p=t.path;d.innerHTML=`<span>${fi(t.name.split('.').pop())}</span><span class="en">${x(t.name)}</span><span class="etab-x" onclick="cTab('${x(t.path)}',event)">✕</span>`;d.addEventListener('click',e=>{if(!e.target.classList.contains('etab-x'))actT(t);});el.appendChild(d);});}
async function openF(path,name,bot){if(curBot&&curBot!==bot)selBot(bot,bot);curFile=path;let tab=tabs.find(t=>t.path===path&&t.type==='f');if(tab){actT(tab);return;}const res=await api('/editor/api/file?path='+encodeURIComponent(path));if(res.error){toast('❌ '+res.error,'err');return;}const model=monaco.editor.createModel(res.content,gL(name));tab={path,name,dirty:false,model,bot,type:'f'};tabs.push(tab);actT(tab);rTree();}
function actT(tab){aTab=tab;if(ed&&tab.type==='f'){ed.setModel(tab.model);document.getElementById('wlc').classList.add('hide');document.getElementById('mc').style.display='';}curFile=tab.path;if(!curBot&&tab.bot)selBot(tab.bot,tab.bot);rTabs();}
function cTab(path,e){if(e)e.stopPropagation();const idx=tabs.findIndex(t=>t.path===path&&t.type==='f');if(idx<0)return;tabs[idx].model?.dispose();tabs.splice(idx,1);if(aTab?.path===path){aTab=tabs.filter(t=>t.type==='f').slice(-1)[0]||null;if(aTab)actT(aTab);else{if(ed)ed.setModel(null);document.getElementById('wlc').classList.remove('hide');}}rTabs();}
// ── Save ─────────────────────────────────────────────────
async function save(){if(!aTab||!ed||aTab.type!=='f')return;const res=await api('/editor/api/file',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:aTab.path,content:ed.getValue()})});if(res.error){toast('❌ '+res.error,'err');return;}aTab.dirty=false;rTabs();toast('💾 Saved','ok');}
// ── Run ──────────────────────────────────────────────────
async function run(){if(!curBot){toast('Select a bot first','err');return;}if(aTab?.dirty)await save();addLog('▶ Stopping '+curBot+'…','i');const res=await api('/editor/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bot:curBot})});if(res.error){toast('❌ '+res.error,'err');addLog('❌ Run error: '+res.error,'e');return;}addLog('▶ Restarting '+curBot+'…','i');toast('▶ Restarting…','info');setTimeout(()=>refresh(),3500);}
// ── New bot/file/dir/del ──────────────────────────────────
async function doNB(){const n=document.getElementById('nb-n').value.trim();if(!n)return;const r=await api('/editor/api/newbot',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n})});cM('m-nb');if(r.error){toast('❌ '+r.error,'err');return;}toast('🤖 '+r.display+' created','ok');await refresh();openF(r.main,'main.py',r.name);}
async function doNF(){const n=document.getElementById('nf-n').value.trim();if(!n||!curBot)return;const path=curBot+'/'+n;const r=await api('/editor/api/file',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,content:''})});cM('m-nf');if(r.error){toast('❌ '+r.error,'err');return;}await refresh();openF(path,n,curBot);}
async function doND(){const n=document.getElementById('nd-n').value.trim();if(!n||!curBot)return;const r=await api('/editor/api/mkdir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:curBot+'/'+n})});cM('m-nd');if(r.error){toast('❌ '+r.error,'err');return;}toast('📁 Created','ok');refresh();}
async function delCur(){if(!curFile){toast('No file selected','err');return;}if(!confirm('Delete '+curFile+'?'))return;const r=await api('/editor/api/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:curFile})});if(r.error){toast('❌ '+r.error,'err');return;}cTab(curFile,null);curFile='';toast('🗑 Deleted','ok');refresh();}
// ── Upload ────────────────────────────────────────────────
function showUL(){document.getElementById('ul-d').textContent=ulDest||'/';oM('m-ul');}
document.getElementById('finp').addEventListener('change',async e=>{await upF(e.target.files);e.target.value='';});
async function upF(files){if(!files.length)return;const fd=new FormData();fd.append('dir',ulDest);for(const f of files)fd.append('files',f,f.name);const r=await fetch('/editor/api/upload?token='+TOK,{method:'POST',body:fd}).then(r=>r.json());cM('m-ul');if(r.error){toast('❌ '+r.error,'err');return;}toast('⬆ '+r.saved.length+' file(s)','ok');refresh();}
const dov=document.getElementById('dov');
document.addEventListener('dragenter',e=>{if(e.dataTransfer.types.includes('Files'))dov.classList.add('show');});
document.addEventListener('dragover',e=>e.preventDefault());
document.addEventListener('dragleave',e=>{if(!e.relatedTarget)dov.classList.remove('show');});
document.addEventListener('drop',async e=>{e.preventDefault();dov.classList.remove('show');await upF(e.dataTransfer.files);});
// ── WebSocket output ─────────────────────────────────────
function connectWs(bot){if(logWs){try{logWs.close();}catch(e){}}document.getElementById('pv-o').innerHTML='<div class="ll li">── Connecting to '+bot+' ──</div>';logWs=new WebSocket(`${WS}/editor/ws?token=${TOK}&bot=${encodeURIComponent(bot)}`);logWs.onmessage=e=>addLog(e.data);logWs.onclose=()=>addLog('── Stream closed ──','i');logWs.onerror=()=>addLog('── WS error ──','e');}
let _pbuf=[];
function addLog(t,fc){const pv=document.getElementById('pv-o');const d=document.createElement('div');const c=fc||((/error|exception|traceback/i.test(t))?'e':(/warn/i.test(t))?'w':(/▶|info|started|running/i.test(t))?'i':(/ok|success|done/i.test(t))?'o':(/^─+$/.test(t.trim()))?'s':'');d.className='ll'+(c?' l'+c:'');d.textContent=t;pv.appendChild(d);if(pv.scrollHeight-pv.scrollTop<pv.clientHeight+80)pv.scrollTop=pv.scrollHeight;while(pv.children.length>3000)pv.removeChild(pv.firstChild);_pbuf.push(t);if(_pbuf.length>80)_pbuf.shift();const P=/^(ModuleNotFoundError|SyntaxError|NameError|TypeError|AttributeError|ValueError|ImportError|RuntimeError|IndentationError|KeyError|FileNotFoundError|JSONDecodeError|TimeoutError|RecursionError|ZeroDivisionError|MemoryError)/;if(P.test(t.trim())){buildProb(_pbuf.join('\n'));_pbuf=[];}}
const HINTS=[[/ModuleNotFoundError: No module named '([^']+)'/,g=>`Install '${g[1]}' — add to requirements.txt or re-deploy`],[/SyntaxError: (.+)/,g=>`Syntax error: ${g[1]} — check brackets/colons/quotes`],[/IndentationError: (.+)/,g=>`Indentation: ${g[1]} — use 4 spaces`],[/NameError: name '([^']+)'/,g=>`'${g[1]}' not defined — check spelling`],[/TypeError: (.+)/,g=>`Type error: ${g[1]} — check argument types`],[/AttributeError: '([^']+)' object has no attribute '([^']+)'/,g=>`'${g[1]}' has no '${g[2]}'`],[/KeyError: '?([^'\n]+)'?/,g=>`Key '${g[1]}' not in dict`],[/ImportError: cannot import name '([^']+)' from '([^']+)'/,g=>`Wrong import '${g[1]}' from '${g[2]}'`],[/FileNotFoundError/,g=>`File not found — check path`],[/ConnectionRefusedError/,g=>`Connection refused — check host/port`],[/Unauthorized/,g=>`Unauthorized — check bot token`],[/ZeroDivisionError/,g=>`Division by zero`],[/RecursionError/,g=>`Infinite recursion — add base case`],];
function buildProb(text){let et='Error',em='',fn=null,ln=null,hint='Check Output panel.';for(const l of text.split('\n')){const m=l.match(/File "([^"]+)", line (\d+)/);if(m){fn=l.match(/([^/\\]+)\.py/)?.[0]||m[1].split('/').pop();ln=parseInt(m[2]);}}for(let i=text.split('\n').length-1;i>=0;i--){const l=text.split('\n')[i].trim();if(l&&!l.startsWith('File ')&&!l.startsWith('Traceback')&&!l.startsWith(' ')){em=l;et=l.split(':')[0].trim();break;}}for(const[p,h]of HINTS){const m=text.match(p);if(m){hint=h(m);break;}}if(!em)return;probs.unshift({et,em,fn,ln,hint,ts:Date.now()});if(probs.length>50)probs.pop();rProbs();showP('p');}
function rProbs(){const el=document.getElementById('pv-p');const cnt=document.getElementById('pcnt');const ec=document.getElementById('ecnt');if(!probs.length){el.innerHTML='<div style="padding:10px;color:var(--text2);font-size:12px">No problems detected.</div>';cnt.textContent='0';ec.style.display='none';return;}cnt.textContent=String(probs.length);ec.textContent=String(probs.length);ec.style.display='';el.innerHTML=probs.map(p=>`<div class="pi"><span class="pico">${p.et.includes('Syntax')||p.et.includes('Indent')?'✏️':'❌'}</span><div style="flex:1"><div class="pmsg"><b>${es(p.et)}:</b> ${es(p.em.slice(0,150))}</div>${p.fn&&p.ln?`<div class="ploc">📍 ${es(p.fn)} — Line ${p.ln}</div>`:''}<div class="phint">💡 ${es(p.hint)}</div></div></div>`).join('');}
function clrP(){document.getElementById('pv-o').innerHTML='';probs=[];rProbs();}
function pBot(){const pv=document.getElementById('pv-'+curP);if(pv)pv.scrollTop=pv.scrollHeight;}
// ── Panel tabs ────────────────────────────────────────────
function showP(id){curP=id;['o','p','t'].forEach(p=>{document.getElementById('pt-'+p)?.classList.toggle('act',p===id);document.getElementById('pv-'+p)?.classList.toggle('act',p===id);});if(!pVis){pVis=true;document.getElementById('panel').classList.remove('hide');document.getElementById('rp').style.display='';if(ed)ed.layout();}}
// ── Panel / sidebar toggle ───────────────────────────────
function tSB(on){sbVis=on!==undefined?on:!sbVis;document.getElementById('sb').classList.toggle('hide',!sbVis);document.getElementById('rsb').style.display=sbVis?'':'none';document.getElementById('ab-e').classList.toggle('on',sbVis);if(ed)ed.layout();}
function tPanel(){pVis=!pVis;document.getElementById('panel').classList.toggle('hide',!pVis);document.getElementById('rp').style.display=pVis?'':'none';if(ed)ed.layout();}
// ── Resize ────────────────────────────────────────────────
(()=>{const rsz=document.getElementById('rsb'),sb=document.getElementById('sb');let dr=false,sx=0,sw=0;const st=e=>{if(!sbVis)return;dr=true;sx=e.clientX||(e.touches?.[0]?.clientX||0);sw=sb.offsetWidth;rsz.classList.add('on');e.preventDefault();};const mv=e=>{if(!dr)return;const cx=e.clientX||(e.touches?.[0]?.clientX||0);const w=Math.max(120,Math.min(480,sw+(cx-sx)));sb.style.width=w+'px';if(ed)ed.layout();};const en=()=>{dr=false;rsz.classList.remove('on');};rsz.addEventListener('mousedown',st);rsz.addEventListener('touchstart',st,{passive:false});document.addEventListener('mousemove',mv);document.addEventListener('touchmove',mv,{passive:true});document.addEventListener('mouseup',en);document.addEventListener('touchend',en);})();
(()=>{const rsz=document.getElementById('rp'),p=document.getElementById('panel');let dr=false,sy=0,sh=0;const st=e=>{if(!pVis)return;dr=true;sy=e.clientY||(e.touches?.[0]?.clientY||0);sh=p.offsetHeight;rsz.classList.add('on');e.preventDefault();};const mv=e=>{if(!dr)return;const cy=e.clientY||(e.touches?.[0]?.clientY||0);const h=Math.max(60,Math.min(window.innerHeight*0.65,sh-(cy-sy)));p.style.height=h+'px';if(ed)ed.layout();};const en=()=>{dr=false;rsz.classList.remove('on');};rsz.addEventListener('mousedown',st);rsz.addEventListener('touchstart',st,{passive:false});document.addEventListener('mousemove',mv);document.addEventListener('touchmove',mv,{passive:true});document.addEventListener('mouseup',en);document.addEventListener('touchend',en);})();
// ── Keyboard ─────────────────────────────────────────────
document.addEventListener('keydown',e=>{if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')return;const k=e.key.toLowerCase();if(!e.ctrlKey&&!e.metaKey&&k==='b')tSB();if(!e.ctrlKey&&!e.metaKey&&k==='t')tPanel();if((e.ctrlKey||e.metaKey)&&k==='s'){e.preventDefault();save();}if((e.ctrlKey||e.metaKey)&&k==='enter'){e.preventDefault();run();}if((e.ctrlKey||e.metaKey)&&k==='z')undo();if((e.ctrlKey||e.metaKey)&&(e.shiftKey&&k==='z'||k==='y'))redo();});
// ── Modals ────────────────────────────────────────────────
function oM(id){document.getElementById(id).classList.add('show');const inp=document.getElementById(id).querySelector('input');if(inp){inp.value='';setTimeout(()=>inp.focus(),50);}}
function cM(id){document.getElementById(id).classList.remove('show');}
document.querySelectorAll('.mov').forEach(bg=>{bg.addEventListener('click',e=>{if(e.target===bg)bg.classList.remove('show');});});
document.getElementById('nb-n').addEventListener('keydown',e=>e.key==='Enter'&&doNB());
document.getElementById('nf-n').addEventListener('keydown',e=>e.key==='Enter'&&doNF());
document.getElementById('nd-n').addEventListener('keydown',e=>e.key==='Enter'&&doND());
// ── Toast ─────────────────────────────────────────────────
let _tt;function toast(msg,type='ok'){const el=document.getElementById('toast');el.textContent=msg;el.className='show '+type;clearTimeout(_tt);_tt=setTimeout(()=>el.className='',3000);}
// ── Helpers ───────────────────────────────────────────────
function es(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function x(s){return es(s);}
function i(s){return String(s).replace(/[^a-zA-Z0-9]/g,'_');}
function fi(e){return{py:'🐍',js:'📜',ts:'📜',json:'{}',txt:'📄',md:'📝',sh:'⚙',yml:'⚙',yaml:'⚙',env:'🔑',html:'🌐',css:'🎨',png:'🖼',jpg:'🖼',gif:'🖼',zip:'📦',log:'📋',cfg:'⚙',ini:'⚙',toml:'⚙',sql:'🗄',requirements:'📋'}[e]||'📄';}
function gL(n){const e=n.split('.').pop().toLowerCase();return{py:'python',js:'javascript',ts:'typescript',json:'json',html:'html',css:'css',sh:'shell',md:'markdown',yml:'yaml',yaml:'yaml',sql:'sql',txt:'plaintext',toml:'ini',cfg:'ini',ini:'ini'}[e]||'plaintext';}
</script></body></html>"""

