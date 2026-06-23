"""
╔══════════════════════════════════════════════════════════════════════╗
║   MASTER HOSTING BOT  —  Web IDE  v6.0  (Replit Exact)             ║
║   Codian Studio 💎                                                  ║
╠══════════════════════════════════════════════════════════════════════╣
║  Layout (exactly like Replit screenshot):                           ║
║  • Top bar: logo + file tabs (like Replit)                          ║
║  • Left: narrow activity bar + collapsible file tree               ║
║  • Center: Monaco editor                                            ║
║  • Bottom panel: Output / Problems / Terminal TABS                  ║
║  • Status bar at very bottom                                        ║
║                                                                     ║
║  Security: per-user tokens, ownership check on every API call       ║
║  Mobile zoom: 3-method fix (gesture + doubletap + ctrlwheel)       ║
╚══════════════════════════════════════════════════════════════════════╝
"""
import os, json, time, uuid, shutil, asyncio, logging, mimetypes
from pathlib import Path
from typing  import Optional
from aiohttp import web

log = logging.getLogger("WebIDE")

HOSTED_DIR    = Path("hosted_bots")
DATA_DIR      = HOSTED_DIR / "_data"
BLOCKED_NAMES = {"_data", "_deleted", ".git", "__pycache__"}

_RUNNING_BOTS: dict   = {}
_APP_REF              = None
_ADMIN_IDS: set[int]  = set()
_PENDING: dict        = {}

_TOKENS_FILE      = DATA_DIR / "user_tokens.json"
_BLOCKED_IPS_FILE = DATA_DIR / "ide_blocked_ips.json"


_RUN_BOT_FN = None   # set by main.py so IDE can start stopped bots


def init_editor(running_bots, app_ref=None, admin_ids=None, run_bot_fn=None):
    global _RUNNING_BOTS, _APP_REF, _ADMIN_IDS, _RUN_BOT_FN
    _RUNNING_BOTS = running_bots
    _APP_REF      = app_ref
    _ADMIN_IDS    = admin_ids or set()
    _RUN_BOT_FN   = run_bot_fn


# ── Token / Auth ──────────────────────────────────────────────────────
def _lj(p, d):
    try:    return json.loads(p.read_text())
    except: return d

def _sj(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))

def get_user_token(uid: int) -> str:
    tokens = _lj(_TOKENS_FILE, {})
    for tok, stored in tokens.items():
        if stored == uid: return tok
    return _new_tok(uid)

def rotate_user_token(uid: int) -> str:
    tokens = {t: u for t, u in _lj(_TOKENS_FILE, {}).items() if u != uid}
    _sj(_TOKENS_FILE, tokens)
    return _new_tok(uid)

def _new_tok(uid: int) -> str:
    tok = uuid.uuid4().hex + uuid.uuid4().hex[:8]
    tokens = _lj(_TOKENS_FILE, {}); tokens[tok] = uid; _sj(_TOKENS_FILE, tokens)
    return tok

def _auth(req: web.Request) -> tuple[int, bool]:
    tok = req.rel_url.query.get("token") or req.cookies.get("ide_token", "")
    if not tok: return 0, False
    uid = _lj(_TOKENS_FILE, {}).get(tok, 0)
    return uid, uid in _ADMIN_IDS

def _ip(req): return req.headers.get("X-Forwarded-For", req.remote or "?").split(",")[0].strip()
def _blocked(ip): return ip in _lj(_BLOCKED_IPS_FILE, [])
def _block_ip(ip):
    bl = _lj(_BLOCKED_IPS_FILE, [])
    if ip not in bl: bl.append(ip)
    _sj(_BLOCKED_IPS_FILE, bl)


# ── Helpers ───────────────────────────────────────────────────────────
def _fup(s):
    s=int(max(0,s)); h,r=divmod(s,3600); m,sc=divmod(r,60)
    return f"{h}h {m}m {sc}s"

def _reg():
    try:    return json.loads((DATA_DIR/"registry.json").read_text())
    except: return {}

def _disp(k):
    p=k.split("_",1)
    if len(p)==2 and p[0].isdigit(): return p[1]
    return k

def _can(uid, ia, bot):
    if ia: return True
    r=_reg()
    if str(r.get(bot,{}).get("owner_id",""))==str(uid): return True
    if str(_RUNNING_BOTS.get(bot,{}).get("owner_id",""))==str(uid): return True
    return False

def _ubots(uid, ia):
    r=_reg(); seen=set(); bots=[]
    src=list(r.keys()) if ia else [k for k,v in r.items() if str(v.get("owner_id",""))==str(uid)]
    for k in src: bots.append(k); seen.add(k)
    for n,e in _RUNNING_BOTS.items():
        if n not in seen and (ia or str(e.get("owner_id",""))==str(uid)): bots.append(n)
    return bots

def _sp(raw):
    try:
        full=(HOSTED_DIR/raw).resolve(); base=HOSTED_DIR.resolve()
        if full==base or base in full.parents: return full
    except: pass
    return None

def _tree(root, rel=""):
    items=[]
    try: entries=sorted(root.iterdir(),key=lambda p:(p.is_file(),p.name.lower()))
    except: return items
    for e in entries:
        if e.name.startswith(".") or e.name=="__pycache__": continue
        n={"name":e.name,"path":(rel+"/"+e.name).lstrip("/"),
           "type":"dir" if e.is_dir() else "file",
           "ext":e.suffix.lstrip(".").lower() if e.is_file() else "",
           "size":e.stat().st_size if e.is_file() else 0}
        if e.is_dir(): n["children"]=_tree(e,n["path"])
        items.append(n)
    return items

def _bots(uid, ia):
    if not HOSTED_DIR.exists(): return []
    allowed=set(_ubots(uid,ia)); bots=[]; seen=set()
    for item in sorted(HOSTED_DIR.iterdir()):
        if not item.is_dir() or item.name in BLOCKED_NAMES or item.name not in allowed: continue
        seen.add(item.name); e=_RUNNING_BOTS.get(item.name,{})
        bots.append({"name":item.name,"display":_disp(item.name),"path":item.name,
                     "type":"bot","status":e.get("status","Offline 🔴"),
                     "restarts":e.get("restarts",0),"heals":e.get("heal_tries",0),
                     "uptime":_fup(time.time()-e["start_time"]) if e.get("start_time") else "—",
                     "has_files":(item/"main.py").exists(),"children":_tree(item,item.name)})
    for n in allowed:
        if n in seen: continue
        e=_RUNNING_BOTS.get(n,{})
        if e.get("active"):
            bots.append({"name":n,"display":_disp(n),"path":n,"type":"bot",
                         "status":e.get("status","Running 🟢"),"restarts":e.get("restarts",0),
                         "heals":e.get("heal_tries",0),
                         "uptime":_fup(time.time()-e["start_time"]) if e.get("start_time") else "—",
                         "has_files":False,"children":[]})
    return bots

def _tail(path, n=80):
    try: return "\n".join(path.read_text(errors="replace").splitlines()[-n:])
    except: return ""


# ── API Handlers ──────────────────────────────────────────────────────
async def api_tree(req):
    if _blocked(_ip(req)): return web.json_response({"error":"blocked"},status=403)
    uid,ia=_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    return web.json_response(_bots(uid,ia))

async def api_read(req):
    uid,ia=_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    raw=req.rel_url.query.get("path",""); bot=raw.split("/")[0]
    if not _can(uid,ia,bot): return web.json_response({"error":"forbidden"},status=403)
    path=_sp(raw)
    if not path or not path.exists() or not path.is_file():
        return web.json_response({"error":"Not found"},status=404)
    try: content=path.read_text(errors="replace")[:524288]
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)
    return web.json_response({"path":raw,"name":path.name,"content":content,
                              "mime":mimetypes.guess_type(str(path))[0] or "text/plain"})

async def api_write(req):
    uid,ia=_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body=await req.json(); raw=body.get("path",""); bot=raw.split("/")[0]
        if not _can(uid,ia,bot): return web.json_response({"error":"forbidden"},status=403)
        path=_sp(raw)
        if not path: return web.json_response({"error":"Invalid path"},status=400)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(body.get("content",""),encoding="utf-8")
        return web.json_response({"ok":True})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_delete(req):
    uid,ia=_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body=await req.json(); raw=body.get("path",""); bot=raw.split("/")[0]
        if not _can(uid,ia,bot): return web.json_response({"error":"forbidden"},status=403)
        path=_sp(raw)
        if not path or not path.exists(): return web.json_response({"error":"Not found"},status=404)
        if path.parent.resolve()==HOSTED_DIR.resolve() and path.is_dir():
            return web.json_response({"error":"Use bot delete button."},status=400)
        shutil.rmtree(path) if path.is_dir() else path.unlink()
        return web.json_response({"ok":True})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_mkdir(req):
    uid,ia=_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body=await req.json(); raw=body.get("path",""); bot=raw.split("/")[0]
        if not _can(uid,ia,bot): return web.json_response({"error":"forbidden"},status=403)
        path=_sp(raw)
        if not path: return web.json_response({"error":"Invalid path"},status=400)
        path.mkdir(parents=True,exist_ok=True)
        return web.json_response({"ok":True})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_upload(req):
    uid,ia=_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        reader=await req.multipart(); dest_raw=""; saved=[]
        async for part in reader:
            if part.name=="dir": dest_raw=(await part.read()).decode().strip()
            elif part.filename:
                # Uploads MUST target a bot the user owns — never the root.
                bot=dest_raw.split("/")[0] if dest_raw else ""
                if not bot or not _can(uid,ia,bot): continue
                dest=_sp(dest_raw)
                if not dest: continue
                dest.mkdir(parents=True,exist_ok=True)
                fname=Path(part.filename).name
                (dest/fname).write_bytes(await part.read())
                saved.append((dest/fname).relative_to(HOSTED_DIR).as_posix())
        return web.json_response({"ok":True,"saved":saved})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_newbot(req):
    uid,ia=_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        import re as _re
        body=await req.json(); raw=body.get("name","").strip().replace(" ","_").replace("/","")
        if not raw: return web.json_response({"error":"Name required"},status=400)
        clean=_re.sub(r"[^a-zA-Z0-9_-]","_",raw)[:40]
        bot_key=f"{uid}_{clean}"; bot_dir=HOSTED_DIR/bot_key
        if bot_dir.exists(): return web.json_response({"error":f"'{clean}' exists"},status=409)
        bot_dir.mkdir(parents=True)
        (bot_dir/"main.py").write_text(
            f'# {clean}  —  Codian Studio 💎\n'
            '# Put your BOT_TOKEN in .env file (never hardcode)\n\n'
            'import os\nfrom dotenv import load_dotenv\nload_dotenv()\n\n'
            'BOT_TOKEN = os.getenv("BOT_TOKEN")\n'
            'if not BOT_TOKEN:\n'
            '    raise ValueError("BOT_TOKEN not set in .env!")\n\n'
            f'print("Starting {clean}...")\n', encoding="utf-8")
        (bot_dir/"requirements.txt").write_text("python-dotenv\n", encoding="utf-8")
        (bot_dir/".env").write_text(
            "# Fill in your values\nBOT_TOKEN=your_token_here\n", encoding="utf-8")
        try:
            r=_reg(); r[bot_key]={"owner_id":uid,"registered_at":time.time(),"display_name":clean}
            (DATA_DIR/"registry.json").write_text(json.dumps(r,indent=2))
        except: pass
        return web.json_response({"ok":True,"name":bot_key,"display":clean,"main":f"{bot_key}/main.py"})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_run(req):
    uid,ia=_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body=await req.json(); bot=body.get("bot","").strip()
        if not bot: return web.json_response({"error":"bot required"},status=400)
        if not _can(uid,ia,bot): return web.json_response({"error":"forbidden"},status=403)
        bot_dir=HOSTED_DIR/bot
        if not (bot_dir/"main.py").exists():
            return web.json_response({"error":"main.py not found"},status=404)

        e=_RUNNING_BOTS.get(bot)

        if e and e.get("active"):
            # Bot IS running — write flag + kill process.
            # DO NOT set active=False — that kills the run_bot loop permanently.
            # The loop checks restart_flag mid-run and restarts cleanly.
            e["status"]="Restarting ⏳"
            (bot_dir/".restart_flag").write_text(str(time.time()))
            p=e.get("process")
            if p and p.poll() is None:
                p.terminate()
                # Don't wait here — loop handles it
        else:
            # Bot is NOT running — start it fresh via run_bot_fn callback
            reg_owner = _reg().get(bot,{}).get("owner_id", uid)
            if _RUN_BOT_FN:
                asyncio.create_task(_RUN_BOT_FN(bot, bot_dir, reg_owner, stagger=False))
            else:
                # Fallback: write flag, if loop exists it'll pick it up
                (bot_dir/".restart_flag").write_text(str(time.time()))

        return web.json_response({"ok":True,"bot":bot,"msg":"Restarting…"})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_stop(req):
    uid,ia=_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    try:
        body=await req.json(); bot=body.get("bot","").strip()
        if not bot: return web.json_response({"error":"bot required"},status=400)
        if not _can(uid,ia,bot): return web.json_response({"error":"forbidden"},status=403)
        e=_RUNNING_BOTS.get(bot)
        if e:
            # active=False ends the run_bot loop so it stays stopped
            e["active"]=False
            p=e.get("process")
            if p and p.poll() is None:
                try: p.terminate()
                except Exception: pass
            e["status"]="Stopped 🛑"
        return web.json_response({"ok":True,"bot":bot,"msg":"Stopped"})
    except Exception as ex: return web.json_response({"error":str(ex)},status=500)

async def api_logs(req):
    uid,ia=_auth(req)
    if not uid: return web.json_response({"error":"unauthorized"},status=401)
    bot=req.rel_url.query.get("bot","")
    if not _can(uid,ia,bot): return web.json_response({"error":"forbidden"},status=403)
    n=min(int(req.rel_url.query.get("n",150)),500)
    lp=HOSTED_DIR/bot/"bot_output.log"
    lines=_tail(lp,n).splitlines() if _tail(lp,n) else []
    e=_RUNNING_BOTS.get(bot,{})
    return web.json_response({"bot":bot,"lines":lines,"status":e.get("status","Offline"),
                              "uptime":_fup(time.time()-e["start_time"]) if e.get("start_time") else "—"})

async def ws_logs(req):
    # Token may come from the query OR the httponly cookie (browsers send the
    # cookie on the WS handshake). Cookie-first keeps the token out of URLs/logs.
    tok=req.rel_url.query.get("token") or req.cookies.get("ide_token","")
    bot=req.rel_url.query.get("bot","")
    uid=_lj(_TOKENS_FILE,{}).get(tok,0)
    if not uid: raise web.HTTPUnauthorized()
    if not _can(uid,uid in _ADMIN_IDS,bot): raise web.HTTPForbidden()
    ws=web.WebSocketResponse(heartbeat=20); await ws.prepare(req)
    lp=HOSTED_DIR/bot/"bot_output.log"; pos=0
    try:
        txt=lp.read_text(errors="replace"); lines=txt.splitlines()[-80:]
        for ln in lines: await ws.send_str(ln)
        pos=lp.stat().st_size
    except: pass
    try:
        while not ws.closed:
            await asyncio.sleep(0.8)
            try:
                size=lp.stat().st_size
                if size>pos:
                    with open(lp,"r",errors="replace") as f: f.seek(pos); chunk=f.read()
                    pos=size
                    for ln in chunk.splitlines():
                        if ln: await ws.send_str(ln)
            except: pass
    except asyncio.CancelledError: pass
    finally:
        try: await ws.close()
        except: pass
    return ws

async def editor_page(req):
    if _blocked(_ip(req)): return web.Response(text="403 Blocked",status=403)
    tok=req.rel_url.query.get("token","")
    uid=_lj(_TOKENS_FILE,{}).get(tok,0)
    if uid:
        resp=web.HTTPFound(location="/editor")
        resp.set_cookie("ide_token",tok,max_age=86400*30,httponly=True,samesite="Strict")
        return resp
    uid2,_=_auth(req)
    if not uid2: return web.Response(text=_LOGIN,content_type="text/html",status=401)
    return web.Response(text=_IDE,content_type="text/html")

def register_routes(app):
    app.router.add_get("/editor",             editor_page)
    app.router.add_get("/editor/api/tree",    api_tree)
    app.router.add_get("/editor/api/file",    api_read)
    app.router.add_post("/editor/api/file",   api_write)
    app.router.add_post("/editor/api/delete", api_delete)
    app.router.add_post("/editor/api/mkdir",  api_mkdir)
    app.router.add_post("/editor/api/upload", api_upload)
    app.router.add_post("/editor/api/newbot", api_newbot)
    app.router.add_post("/editor/api/run",    api_run)
    app.router.add_post("/editor/api/stop",   api_stop)
    app.router.add_get("/editor/api/logs",    api_logs)
    app.router.add_get("/editor/ws",          ws_logs)
    log.info("Web IDE v6.0 ready at /editor")


# ══════════════════════════════════════════════════════════════════════
#  LOGIN PAGE
# ══════════════════════════════════════════════════════════════════════
_LOGIN = """<!DOCTYPE html><html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Codian Studio</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{background:#0e1117;color:#e6edf3;font-family:'Segoe UI',sans-serif;
     display:flex;align-items:center;justify-content:center;height:100svh;padding:20px}
.c{background:#161b22;border:1px solid #30363d;border-radius:12px;
   padding:36px 28px;width:100%;max-width:360px;text-align:center;box-shadow:0 16px 48px #0005}
.logo{font-size:2.8rem;margin-bottom:10px}
h1{color:#58a6ff;font-size:1.05rem;font-weight:700;margin-bottom:4px}
p{color:#8b949e;font-size:.82rem;margin-bottom:24px}
input{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;
      padding:11px 14px;color:#e6edf3;font-size:15px;outline:none;margin-bottom:14px;
      font-family:monospace;letter-spacing:1px;-webkit-appearance:none}
input:focus{border-color:#58a6ff;box-shadow:0 0 0 3px #58a6ff22}
button{width:100%;background:#238636;border:none;border-radius:6px;padding:12px;
       color:#fff;font-size:14px;font-weight:700;cursor:pointer;touch-action:manipulation}
button:hover{background:#2ea043}
.hint{color:#8b949e;font-size:.75rem;margin-top:14px;line-height:1.6}
code{background:#30363d;padding:2px 6px;border-radius:4px;color:#79c0ff}
</style></head><body>
<div class="c"><div class="logo">💎</div><h1>Codian Studio IDE</h1>
<p>Master Hosting Bot</p>
<input type="password" id="t" placeholder="Paste your access token"
       autocomplete="off" autocorrect="off" autocapitalize="off"
       onkeydown="if(event.key==='Enter')go()">
<button onclick="go()">Sign In →</button>
<p class="hint">Get token from bot: <code>/ide</code></p></div>
<script>function go(){var t=document.getElementById('t').value.trim();
if(!t)return;window.location.href='/editor?token='+encodeURIComponent(t);}
</script></body></html>"""


# ══════════════════════════════════════════════════════════════════════
#  IDE — Replit-exact layout  v6.0
# ══════════════════════════════════════════════════════════════════════
_IDE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,minimum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Codian Studio 💎</title>
<style>
/* ── ZOOM KILL ── */
html{touch-action:none;-webkit-text-size-adjust:100%;overflow:hidden;height:100%}
body{height:100svh;overflow:hidden;touch-action:none}
*{-webkit-tap-highlight-color:transparent;touch-action:manipulation;box-sizing:border-box;margin:0;padding:0}

/* ── THEME (GitHub Dark + Replit accent) ── */
:root{
  --bg:    #0d1117;  --bg1: #161b22;  --bg2: #21262d;  --bg3: #30363d;
  --bdr:   #30363d;
  --blue:  #58a6ff;  --blue2:#1f6feb; --green:#3fb950;  --green2:#56d364;
  --red:   #f85149;  --yel:  #d29922; --pur:  #bc8cff;  --orange:#ffa657;
  --tx:    #e6edf3;  --tx2:  #8b949e; --tx3:  #484f58;
  --act:   46px;     --sb:   240px;   --top:  44px;     --panel:200px; --stat:22px;
}
#root{display:flex;flex-direction:column;height:100svh;background:var(--bg);
      color:var(--tx);font-family:'Segoe UI',system-ui,sans-serif;font-size:13px;
      -webkit-font-smoothing:antialiased}

/* ══ TOP BAR (Replit style) ══ */
#topbar{
  height:var(--top);flex-shrink:0;
  background:var(--bg1);border-bottom:1px solid var(--bdr);
  display:flex;align-items:center;padding:0 0 0 0;
  user-select:none;overflow:hidden;
}
/* Left section of topbar */
#tb-left{display:flex;align-items:center;gap:0;height:100%;flex-shrink:0}
.tb-logo{display:flex;align-items:center;gap:8px;padding:0 14px;height:100%;
         border-right:1px solid var(--bdr);font-weight:800;color:var(--blue);font-size:.9rem}
/* File tabs (Replit style — in topbar) */
#file-tabs{display:flex;align-items:stretch;height:100%;overflow-x:auto;flex:1;min-width:0}
#file-tabs::-webkit-scrollbar{height:2px}
#file-tabs::-webkit-scrollbar-thumb{background:var(--bdr)}
.ftab{
  display:inline-flex;align-items:center;gap:6px;
  padding:0 14px;height:100%;flex-shrink:0;
  cursor:pointer;font-size:12.5px;color:var(--tx2);
  border-right:1px solid var(--bdr);white-space:nowrap;
  position:relative;touch-action:manipulation;
  transition:background .1s,color .1s;
}
.ftab:hover{background:var(--bg2);color:var(--tx)}
.ftab.act{background:var(--bg);color:var(--tx)}
/* Active tab indicator — line at bottom like Replit */
.ftab.act::after{content:'';position:absolute;bottom:0;left:0;right:0;
                  height:2px;background:var(--blue)}
.ftab.dirty .ftn::after{content:'●';color:var(--yel);margin-left:4px;font-size:9px}
.ftab-x{opacity:0;font-size:12px;border-radius:3px;padding:0 2px;
         line-height:1.3;touch-action:manipulation;transition:opacity .1s}
.ftab:hover .ftab-x{opacity:.5}
.ftab-x:hover{opacity:1!important;background:var(--red)!important;color:#fff!important}
/* Right section of topbar */
#tb-right{display:flex;align-items:center;gap:4px;padding:0 10px;flex-shrink:0;
           border-left:1px solid var(--bdr);height:100%}
.tbtn{display:inline-flex;align-items:center;gap:4px;border-radius:6px;
      padding:5px 10px;cursor:pointer;font-size:12px;border:1px solid var(--bdr);
      color:var(--tx2);background:var(--bg2);white-space:nowrap;
      touch-action:manipulation;transition:.12s;user-select:none}
.tbtn:hover{border-color:var(--blue);color:var(--tx)}
.tbtn.run{border-color:var(--green);color:var(--green);background:#0d2014}
.tbtn.run:hover,.tbtn.run.busy{background:var(--green);color:#fff;pointer-events:none}
.tbtn.stop{border-color:var(--red);color:var(--red);background:#20100d}
.tbtn.stop:hover{background:var(--red);color:#fff}
.tbtn.on{border-color:var(--blue);color:var(--blue)}
#bot-lbl{font-size:12px;color:var(--pur);font-weight:700;max-width:100px;
         overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* ══ WORKSPACE ══ */
#workspace{display:flex;flex:1;overflow:hidden;min-height:0}

/* ══ ACTIVITY BAR (Replit left strip) ══ */
#actbar{
  width:var(--act);flex-shrink:0;
  background:var(--bg1);border-right:1px solid var(--bdr);
  display:flex;flex-direction:column;align-items:center;padding:6px 0;gap:2px;
}
.ab{width:34px;height:34px;border-radius:6px;display:flex;align-items:center;
    justify-content:center;cursor:pointer;font-size:17px;color:var(--tx3);
    border:1px solid transparent;touch-action:manipulation;transition:.1s;position:relative}
.ab:hover{color:var(--tx);background:var(--bg2)}
.ab.on{color:var(--blue);border-color:var(--blue2);background:var(--bg2)}
.ab.on::before{content:'';position:absolute;left:-1px;top:6px;bottom:6px;
               width:2px;background:var(--blue);border-radius:0 2px 2px 0}
.ab-sp{flex:1}

/* ══ SIDEBAR (file tree) ══ */
#sidebar{
  width:var(--sb);min-width:150px;max-width:400px;flex-shrink:0;
  display:flex;flex-direction:column;
  background:var(--bg1);border-right:1px solid var(--bdr);overflow:hidden;
}
#sidebar.hide{display:none}
.sb-head{height:36px;display:flex;align-items:center;padding:0 12px;
         border-bottom:1px solid var(--bdr);flex-shrink:0;user-select:none}
.sb-title{flex:1;font-size:11px;font-weight:700;text-transform:uppercase;
          letter-spacing:.6px;color:var(--tx2)}
.sb-acts{display:flex;gap:2px}
.ib{background:none;border:none;color:var(--tx2);cursor:pointer;
    padding:3px 5px;border-radius:4px;font-size:13px;line-height:1;touch-action:manipulation}
.ib:hover{background:var(--bg2);color:var(--tx)}
#tree{flex:1;overflow-y:auto;overflow-x:hidden;
      scrollbar-width:thin;scrollbar-color:var(--bdr) transparent;touch-action:pan-y}
#tree::-webkit-scrollbar{width:4px}
#tree::-webkit-scrollbar-thumb{background:var(--bdr);border-radius:2px}

/* Sidebar resize handle */
#rsb{width:4px;flex-shrink:0;cursor:col-resize;background:transparent;transition:background .12s}
#rsb.on,#rsb:hover{background:var(--blue)}

/* ══ EDITOR PANE ══ */
#ed-pane{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0;min-height:0}

/* Monaco editor wrapper — ZOOM FIX: touch-action:none */
#ed-wrap{flex:1;position:relative;overflow:hidden;min-height:0;
         background:#0d1117;touch-action:none}
#mc{position:absolute;inset:0;touch-action:none;overflow:hidden}

/* Welcome screen */
#welcome{position:absolute;inset:0;display:flex;flex-direction:column;
         align-items:center;justify-content:center;color:var(--tx3);gap:12px;
         background:#0d1117}
#welcome.hide{display:none}
#welcome .wi{font-size:3rem}
#welcome h2{font-size:1rem;color:var(--tx2);font-weight:400}
#welcome p{font-size:12px}
#welcome kbd{background:var(--bg2);border:1px solid var(--bdr);border-radius:4px;
             padding:1px 6px;font-family:monospace;font-size:11px;color:var(--tx)}

/* Panel resize handle */
#rpanel{height:5px;flex-shrink:0;cursor:row-resize;background:var(--bdr);
        transition:background .12s;touch-action:none}
#rpanel.on,#rpanel:hover{background:var(--blue)}

/* ══ BOTTOM PANEL (Output/Problems/Terminal tabs — like Replit) ══ */
#panel{
  height:var(--panel);min-height:80px;max-height:60vh;flex-shrink:0;
  display:flex;flex-direction:column;
  background:var(--bg1);border-top:1px solid var(--bdr);
}
#panel.hide{height:0!important;min-height:0;border-top:none;overflow:hidden}

/* Panel tab strip */
#panel-tabstrip{
  height:36px;flex-shrink:0;
  background:var(--bg2);border-bottom:1px solid var(--bdr);
  display:flex;align-items:center;padding:0 8px;gap:0;
}
.ptab{
  height:100%;display:inline-flex;align-items:center;gap:5px;
  padding:0 14px;font-size:12px;color:var(--tx2);cursor:pointer;
  border-bottom:2px solid transparent;white-space:nowrap;touch-action:manipulation;
  transition:color .1s;position:relative;
}
.ptab:hover{color:var(--tx)}
.ptab.act{color:var(--tx);border-bottom-color:var(--blue)}
/* Dot indicator for problems */
.ptab .dot{width:6px;height:6px;border-radius:50%;background:var(--red);
            display:none;flex-shrink:0}
.ptab .dot.show{display:block}
.pt-sp{flex:1}
.pt-btn{padding:3px 7px;font-size:12px;color:var(--tx2);cursor:pointer;
        border-radius:4px;touch-action:manipulation}
.pt-btn:hover{background:var(--bg3);color:var(--tx)}

/* Panel content views */
#panel-body{flex:1;position:relative;overflow:hidden}
.pview{
  position:absolute;inset:0;overflow-y:auto;
  padding:5px 12px;
  font-family:'Cascadia Code','Fira Code',Consolas,monospace;
  font-size:12px;line-height:1.7;color:#e6edf3;
  scrollbar-width:thin;scrollbar-color:var(--bdr) transparent;
  touch-action:pan-y;display:none;
}
.pview.act{display:block}
.pview::-webkit-scrollbar{width:4px}
.pview::-webkit-scrollbar-thumb{background:var(--bdr);border-radius:2px}

/* Log line colors */
.ll{white-space:pre-wrap;word-break:break-all}
.le{color:#f85149}.lw{color:#d29922}.lo{color:#3fb950}.li{color:#58a6ff}.ls{color:#484f58}

/* Problems view items */
.prob{display:flex;align-items:flex-start;gap:8px;padding:5px 6px;
      border-radius:5px;margin-bottom:3px;cursor:pointer}
.prob:hover{background:var(--bg2)}
.prob-ico{flex-shrink:0;font-size:14px;margin-top:1px}
.prob-body{flex:1}
.prob-msg{font-size:12.5px;font-family:'Segoe UI',sans-serif}
.prob-loc{font-size:11px;color:var(--tx2);margin-top:1px;font-family:monospace}
.prob-hint{margin-top:5px;padding:6px 10px;background:var(--bg);border-left:2px solid var(--blue2);
           border-radius:0 5px 5px 0;font-size:11px;color:#79c0ff;font-family:'Segoe UI',sans-serif}

/* Terminal placeholder */
#pv-term{color:var(--tx2);font-family:'Segoe UI',sans-serif}
.term-cmd{font-family:monospace;color:var(--green2)}

/* ══ STATUS BAR (Replit bottom bar) ══ */
#statusbar{
  height:var(--stat);flex-shrink:0;
  background:var(--blue2);
  display:flex;align-items:center;padding:0 12px;gap:14px;
  font-size:11.5px;color:#fff;user-select:none;
}
#sb-bot{font-weight:700;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#sb-st{opacity:.85}
#sb-ln{font-family:monospace;opacity:.8}
.err-badge{background:#f85149;color:#fff;padding:0 7px;border-radius:10px;
           font-size:10px;font-weight:700;display:none}

/* ══ TREE NODES (exactly like Replit file tree) ══ */
.bot-item{padding:1px 0}
.bot-row{display:flex;align-items:center;gap:4px;padding:5px 12px;cursor:pointer;
         border-left:2px solid transparent;touch-action:manipulation;user-select:none}
.bot-row:hover{background:var(--bg2)}
.bot-row.sel{background:#1f3557;border-left-color:var(--blue)}
.caret{font-size:9px;color:var(--tx3);flex-shrink:0;width:10px;
       transition:transform .15s;display:inline-block}
.caret.op{transform:rotate(90deg)}
.bname{flex:1;font-weight:600;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bst{font-size:9px;padding:1px 6px;border-radius:8px;flex-shrink:0;white-space:nowrap;font-weight:600}
.bst-r{background:#0d2014;color:var(--green)}.bst-s{background:#250d0d;color:var(--red)}
.bst-o{background:var(--bg2);color:var(--tx2)}
.bot-children{display:none;padding-left:12px}.bot-children.op{display:block}
.frow{display:flex;align-items:center;gap:5px;padding:2px 12px;cursor:pointer;
      touch-action:manipulation;user-select:none}
.frow:hover{background:var(--bg2)}.frow.sel{background:#1f3557}
.dch{display:none;padding-left:14px}.dch.op{display:block}
.fic{font-size:12px;flex-shrink:0;width:16px}
.fname{font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fname-dir{color:#d29922}

/* ══ MODALS ══ */
.mov{display:none;position:fixed;inset:0;background:#000000cc;
     align-items:center;justify-content:center;z-index:300;padding:16px;touch-action:none}
.mov.show{display:flex}
.modal{background:var(--bg1);border:1px solid var(--bdr);border-radius:10px;
       padding:22px;width:100%;max-width:380px;box-shadow:0 12px 40px #0008}
.modal h3{margin-bottom:14px;color:var(--blue);font-size:.95rem;font-weight:700}
.modal input{width:100%;background:var(--bg);border:1px solid var(--bdr);border-radius:6px;
             padding:9px 12px;color:var(--tx);font-size:14px;outline:none;
             margin-bottom:12px;-webkit-appearance:none}
.modal input:focus{border-color:var(--blue);box-shadow:0 0 0 3px #58a6ff22}
.mbtns{display:flex;gap:8px;justify-content:flex-end}
.mok{padding:8px 16px;border-radius:6px;border:none;background:var(--blue2);
     color:#fff;cursor:pointer;font-size:13px;font-weight:600;touch-action:manipulation}
.mok:hover{background:#388bfd}
.mcancel{padding:8px 16px;border-radius:6px;border:1px solid var(--bdr);
         background:var(--bg2);color:var(--tx);cursor:pointer;font-size:13px;touch-action:manipulation}

/* ══ TOAST ══ */
#toast{position:fixed;bottom:28px;right:16px;background:var(--bg1);border-radius:8px;
       padding:10px 16px;font-size:12.5px;opacity:0;pointer-events:none;z-index:500;
       max-width:280px;border:1px solid var(--bdr);transition:opacity .2s}
#toast.show{opacity:1}
#toast.ok{border-color:var(--green);color:var(--green2)}
#toast.err{border-color:var(--red);color:var(--red)}
#toast.info{border-color:var(--blue2);color:var(--blue)}

/* Drop overlay */
#dov{display:none;position:fixed;inset:0;background:#58a6ff15;border:2px dashed var(--blue);
     z-index:400;align-items:center;justify-content:center;font-size:1.3rem;
     color:var(--blue);pointer-events:none}
#dov.show{display:flex}
#finp{display:none}

@media(max-width:600px){
  :root{--sb:200px;--act:40px;--panel:160px}
  .tbtn span{display:none}
}
</style></head><body>
<input type="file" id="finp" multiple>
<div id="dov">📁 Drop files to upload</div>
<div id="toast"></div>

<!-- Modals -->
<div class="mov" id="m-nb"><div class="modal"><h3>🤖 New Bot Project</h3>
  <input id="nb-n" placeholder="project-name" autocorrect="off" autocapitalize="off">
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
<div class="mov" id="m-ul"><div class="modal"><h3>⬆️ Upload Files</h3>
  <p style="color:var(--tx2);font-size:12px;margin-bottom:12px">To: <b id="ul-d" style="color:var(--tx)">/</b></p>
  <button class="mok" style="width:100%;margin-bottom:10px"
    onclick="document.getElementById('finp').click()">📂 Choose Files</button>
  <div style="border:1px dashed var(--bdr);border-radius:6px;padding:14px;
       text-align:center;color:var(--tx3);font-size:12px">or drag & drop anywhere</div>
  <div class="mbtns" style="margin-top:12px"><button class="mcancel" onclick="cM('m-ul')">Close</button>
  </div></div></div>

<!-- ════ APP ════ -->
<div id="root">

  <!-- TOP BAR (Replit style: logo | file tabs | right buttons) -->
  <div id="topbar">
    <div id="tb-left">
      <div class="tb-logo">💎 <span style="font-size:.8rem;font-weight:600">Codian</span></div>
    </div>
    <!-- File tabs live here, between logo and right buttons -->
    <div id="file-tabs">
      <div class="ftab act" id="ft-welcome" style="color:var(--tx2)">
        <span>Welcome</span>
      </div>
    </div>
    <div id="tb-right">
      <span id="bot-lbl">No bot</span>
      <button class="tbtn" onclick="showUL()" title="Upload">⬆</button>
      <button class="tbtn" onclick="save()" title="Save [Ctrl+S]">💾 <span>Save</span></button>
      <button class="tbtn run" id="btn-run" onclick="run()">▶ <span>Run</span></button>
      <button class="tbtn stop" id="btn-stop" onclick="stop()" title="Stop">⏹ <span>Stop</span></button>
      <button class="tbtn on" id="btn-sb" onclick="tSB()" title="Files [B]">☰</button>
      <button class="tbtn on" id="btn-panel" onclick="tPanel()" title="Console [T]">⬛</button>
    </div>
  </div>

  <!-- WORKSPACE -->
  <div id="workspace">

    <!-- ACTIVITY BAR -->
    <div id="actbar">
      <div class="ab on" id="ab-e" onclick="tSB(true)" title="Explorer [B]">📁</div>
      <div class="ab" onclick="toast('Search — coming soon','info')" title="Search">🔍</div>
      <div class="ab-sp"></div>
      <div class="ab" onclick="run()" title="Run [Ctrl+Enter]">▶</div>
      <div class="ab" onclick="showUL()" title="Upload">⬆</div>
      <div class="ab" id="ab-nb" onclick="oM('m-nb')" title="New Bot">＋</div>
    </div>

    <!-- SIDEBAR (file tree) -->
    <div id="sidebar">
      <div class="sb-head">
        <span class="sb-title">Explorer</span>
        <div class="sb-acts">
          <button class="ib" onclick="rf()" title="Refresh">⟳</button>
          <button class="ib" id="sb-nf" style="display:none" onclick="oM('m-nf')" title="New File">📄</button>
          <button class="ib" id="sb-nd" style="display:none" onclick="oM('m-nd')" title="New Folder">📁</button>
        </div>
      </div>
      <div id="tree">
        <div style="padding:18px;text-align:center;color:var(--tx3);font-size:12px">Loading…</div>
      </div>
    </div>
    <div id="rsb"></div>

    <!-- EDITOR PANE -->
    <div id="ed-pane">

      <!-- Monaco editor -->
      <div id="ed-wrap">
        <div id="mc"></div>
        <div id="welcome">
          <div class="wi">💎</div>
          <h2>Codian Studio</h2>
          <p style="margin-top:6px">Select a file to start editing</p>
          <p style="margin-top:8px;font-size:11px">
            <kbd>B</kbd> sidebar &nbsp;
            <kbd>T</kbd> console &nbsp;
            <kbd>Ctrl+S</kbd> save &nbsp;
            <kbd>Ctrl+Enter</kbd> run</p>
        </div>
      </div>

      <!-- Panel resize -->
      <div id="rpanel"></div>

      <!-- BOTTOM PANEL — tabs like Replit -->
      <div id="panel">
        <div id="panel-tabstrip">
          <div class="ptab act" id="pt-out" onclick="showP('out')">
            Output
          </div>
          <div class="ptab" id="pt-prob" onclick="showP('prob')">
            Problems <div class="dot" id="prob-dot"></div>
          </div>
          <div class="ptab" id="pt-term" onclick="showP('term')">
            Terminal
          </div>
          <div class="pt-sp"></div>
          <span class="pt-btn" onclick="clrPanel()">✕ Clear</span>
          <span class="pt-btn" onclick="panelBot()">⬇</span>
          <span class="pt-btn" onclick="tPanel()">⊟</span>
        </div>
        <div id="panel-body">
          <div class="pview act" id="pv-out"></div>
          <div class="pview" id="pv-prob">
            <div style="padding:10px 0;color:var(--tx2);font-size:12px">
              No problems detected. Run your bot to check for errors.
            </div>
          </div>
          <div class="pview" id="pv-term">
            <div style="padding:12px 0">
              <p style="color:var(--tx2);font-size:12px;margin-bottom:10px">
                Interactive shell — coming soon. Use <b>Output</b> tab for bot logs.
              </p>
              <p class="term-cmd" style="font-size:12px">$ python main.py</p>
              <p style="color:var(--tx3);font-size:11px;margin-top:4px">
                ↑ Running via ▶ Run button
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- STATUS BAR (Replit bottom bar) -->
  <div id="statusbar">
    <span class="err-badge" id="err-badge">0</span>
    <span id="sb-bot">💎 Codian Studio</span>
    <span id="sb-st">Ready</span>
    <span id="sb-ln">Ln 1, Col 1</span>
  </div>
</div>

<!-- Monaco from CDN -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.46.0/min/vs/loader.min.js"></script>
<script>
'use strict';

// ── ZOOM KILL (3 independent methods) ────────────────────────────────
document.addEventListener('gesturestart', e=>e.preventDefault(),{passive:false});
document.addEventListener('gesturechange',e=>e.preventDefault(),{passive:false});
document.addEventListener('gestureend',  e=>e.preventDefault(),{passive:false});
let _lt=0;
document.addEventListener('touchend',e=>{const n=Date.now();if(n-_lt<280)e.preventDefault();_lt=n;},{passive:false});
document.addEventListener('wheel',e=>{if(e.ctrlKey||e.metaKey)e.preventDefault();},{passive:false});
document.addEventListener('touchstart',e=>{if(e.touches.length>1)e.preventDefault();},{passive:false});

// ── STATE ─────────────────────────────────────────────────────────────
const TOK=(document.cookie.match(/ide_token=([^;]+)/)||[])[1]||'';
const WS=(location.protocol==='https:'?'wss://':'ws://')+location.host;
const api=async(u,o={})=>{
  try{const q=u.includes('?')?'&':'?';return(await fetch(u+q+'token='+TOK,o)).json();}
  catch(e){return{error:String(e)};}
};

let ed=null, fsize=14, tdata=[], tabs=[], aTab=null;
let curBot='', curFile='', logWs=null, ulDest='';
let sbVis=true, panelVis=true, curP='out', probs=[];

// ── MONACO ────────────────────────────────────────────────────────────
require.config({paths:{vs:'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.46.0/min/vs'}});
require(['vs/editor/editor.main'],()=>{
  ed=monaco.editor.create(document.getElementById('mc'),{
    value:'',language:'python',theme:'vs-dark',fontSize:fsize,
    fontFamily:"'Cascadia Code','Fira Code',Consolas,monospace",
    fontLigatures:true,automaticLayout:true,
    minimap:{enabled:false},
    scrollBeyondLastLine:false,lineNumbers:'on',
    glyphMargin:true,folding:true,wordWrap:'off',
    smoothScrolling:true,cursorBlinking:'smooth',cursorSmoothCaretAnimation:'on',
    renderLineHighlight:'line',bracketPairColorization:{enabled:true},
    padding:{top:10,bottom:10},
    scrollbar:{vertical:'auto',horizontal:'auto',useShadows:false,
               verticalScrollbarSize:6,horizontalScrollbarSize:6,alwaysConsumeMouseWheel:true},
    mouseWheelZoom:false,
    quickSuggestions:{other:true,comments:false,strings:false},
    suggest:{showIcons:true},
  });

  // Cursor pos → status bar
  ed.onDidChangeCursorPosition(e=>{
    const p=ed.getPosition();
    if(p) document.getElementById('sb-ln').textContent=`Ln ${p.lineNumber}, Col ${p.column}`;
  });

  // Mark tab dirty on edit
  ed.onDidChangeModelContent(()=>{
    if(aTab&&aTab.type==='f'){aTab.dirty=true;renderTabs();}
  });

  // Keybindings
  ed.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.KeyS, save);
  ed.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.Enter, run);
  ed.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.Equal, ()=>zE(1));
  ed.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.Minus, ()=>zE(-1));

  rf();
  setInterval(rf, 25000);
  setInterval(syncSt, 5000);
});

// Editor-area Ctrl+Scroll → font size (NOT page zoom)
document.getElementById('ed-wrap').addEventListener('wheel',e=>{
  if(e.ctrlKey||e.metaKey){e.preventDefault();e.stopImmediatePropagation();zE(e.deltaY<0?1:-1);}
},{passive:false,capture:true});

function zE(d){fsize=Math.max(8,Math.min(28,fsize+d));if(ed)ed.updateOptions({fontSize:fsize});}
function doUndo(){if(ed)ed.trigger('b','undo',null);}
function doRedo(){if(ed)ed.trigger('b','redo',null);}

// ── TREE ──────────────────────────────────────────────────────────────
async function rf(){
  const d=await api('/editor/api/tree');
  if(!d||d.error) return;
  tdata=Array.isArray(d)?d:[];
  renderTree();
}

function renderTree(){
  const el=document.getElementById('tree');
  if(!tdata.length){
    el.innerHTML='<div style="padding:18px;text-align:center;color:var(--tx3);font-size:12px">No bots.<br>Click ＋ to create one.</div>';
    return;
  }
  el.innerHTML=tdata.map(b=>{
    const op=b.name===curBot;
    const st=(b.status||'').toLowerCase();
    const sc=st.includes('running')?'bst-r':st.includes('stop')||st.includes('error')?'bst-s':'bst-o';
    return `<div class="bot-item">
      <div class="bot-row ${op?'sel':''}" onclick="tBot('${x(b.name)}','${x(b.display||b.name)}',this)">
        <span class="caret ${op?'op':''}">▶</span>
        <span style="font-size:14px;margin:0 2px">🤖</span>
        <span class="bname">${x(b.display||b.name)}</span>
        <span class="bst ${sc}">${x(b.status||'—')}</span>
      </div>
      <div class="bot-children ${op?'op':''}" id="bc-${ID(b.name)}">
        ${!b.has_files
          ? '<div style="padding:6px 14px;color:var(--tx3);font-size:11px">Running in memory — files not found</div>'
          : (b.children||[]).map(c=>fNode(c,b.name)).join('')}
      </div>
    </div>`;
  }).join('');
}

function fNode(n, bot){
  const sel=n.path===curFile?'sel':'';
  if(n.type==='dir'){
    const did='d-'+ID(n.path);
    return `<div>
      <div class="frow fname-dir" onclick="tDir('${did}',this)">
        <span class="fic caret" id="c-${did}">▶</span>
        <span class="fic">📁</span>
        <span class="fname fname-dir">${x(n.name)}</span>
      </div>
      <div class="dch" id="${did}">${(n.children||[]).map(c=>fNode(c,bot)).join('')}</div>
    </div>`;
  }
  return `<div class="frow ${sel}" onclick="openFile('${x(n.path)}','${x(n.name)}','${x(bot)}')">
    <span class="fic">${fIco(n.ext)}</span>
    <span class="fname">${x(n.name)}</span>
  </div>`;
}

function tBot(name,disp,hdr){
  const ch=document.getElementById('bc-'+ID(name));
  const cv=hdr.querySelector('.caret');
  const op=ch.classList.contains('op');
  if(!op) selBot(name,disp);
  ch.classList.toggle('op',!op);
  cv.classList.toggle('op',!op);
}
function tDir(id,el){
  document.getElementById(id)?.classList.toggle('op');
  el.querySelector('.caret')?.classList.toggle('op');
}

function selBot(name,disp){
  curBot=name;
  const d=disp||name;
  document.getElementById('bot-lbl').textContent='🤖 '+d;
  document.getElementById('sb-bot').textContent='🤖 '+d;
  document.getElementById('sb-nf').style.display='';
  document.getElementById('sb-nd').style.display='';
  ulDest=name;
  connectWs(name);
  syncSt();
  renderTree();
}

function syncSt(){
  if(!curBot) return;
  const b=tdata.find(t=>t.name===curBot); if(!b) return;
  const st=(b.status||'').toLowerCase();
  const col=st.includes('running')?'#3fb950':st.includes('error')?'#f85149':'#d29922';
  const ss=document.getElementById('sb-st');
  ss.textContent=b.status||'—'; ss.style.color=col;
}

// ── FILE TABS (in top bar like Replit) ───────────────────────────────
function renderTabs(){
  const el=document.getElementById('file-tabs');
  // Keep welcome tab
  el.querySelectorAll('.ftab[data-p]').forEach(e=>e.remove());
  const wt=document.getElementById('ft-welcome');
  const hasFiles=tabs.filter(t=>t.type==='f').length>0;
  if(wt) wt.style.display=hasFiles?'none':'';
  tabs.filter(t=>t.type==='f').forEach(t=>{
    const d=document.createElement('div');
    d.className='ftab'+(t===aTab?' act':'')+(t.dirty?' dirty':'');
    d.dataset.p=t.path;
    d.innerHTML=`<span>${fIco(t.name.split('.').pop())}</span>
      <span class="ftn">${x(t.name)}</span>
      <span class="ftab-x" onclick="cTab('${x(t.path)}',event)">✕</span>`;
    d.addEventListener('click',e=>{if(!e.target.classList.contains('ftab-x'))actTab(t);});
    el.appendChild(d);
  });
}

async function openFile(path,name,bot){
  if(curBot&&curBot!==bot) selBot(bot,bot);
  curFile=path;
  let tab=tabs.find(t=>t.path===path&&t.type==='f');
  if(tab){actTab(tab);return;}
  const res=await api('/editor/api/file?path='+encodeURIComponent(path));
  if(res.error){toast('❌ '+res.error,'err');return;}
  const model=monaco.editor.createModel(res.content,gLang(name));
  tab={path,name,dirty:false,model,bot,type:'f'};
  tabs.push(tab); actTab(tab); renderTree();
}

function actTab(tab){
  aTab=tab;
  if(ed&&tab.type==='f'){
    ed.setModel(tab.model);
    document.getElementById('welcome').classList.add('hide');
    document.getElementById('mc').style.display='';
  }
  curFile=tab.path;
  if(!curBot&&tab.bot) selBot(tab.bot,tab.bot);
  renderTabs();
}

function cTab(path,e){
  if(e) e.stopPropagation();
  const i=tabs.findIndex(t=>t.path===path&&t.type==='f'); if(i<0) return;
  tabs[i].model?.dispose(); tabs.splice(i,1);
  if(aTab?.path===path){
    aTab=tabs.filter(t=>t.type==='f').slice(-1)[0]||null;
    if(aTab) actTab(aTab);
    else{
      if(ed) ed.setModel(null);
      document.getElementById('welcome').classList.remove('hide');
    }
  }
  renderTabs();
}

// ── SAVE ──────────────────────────────────────────────────────────────
async function save(){
  if(!ed) return;
  if(!aTab||aTab.type!=='f'){toast('No file open to save','err');return;}
  const content=ed.getValue();
  if(!content&&!confirm('Save empty file?')) return;
  const res=await api('/editor/api/file',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path:aTab.path,content})
  });
  if(res.error){toast('❌ Save failed: '+res.error,'err');return;}
  aTab.dirty=false;
  renderTabs();
  // Update status bar briefly
  const sb=document.getElementById('sb-st');
  const prev=sb.textContent;
  sb.textContent='💾 Saved'; sb.style.color='#3fb950';
  setTimeout(()=>{sb.style.color='';syncSt();},2000);
  toast('💾 '+aTab.name+' saved','ok');
}

// ── RUN ───────────────────────────────────────────────────────────────
async function run(){
  if(!curBot){toast('Select a bot first','err');return;}
  // Auto-save first
  if(aTab&&aTab.dirty) await save();
  const btn=document.getElementById('btn-run');
  btn.classList.add('busy'); btn.innerHTML='⏳ <span>Stopping…</span>';
  // Switch to Output tab and show progress
  showP('out');
  addLog('─────────────────────────────────','s');
  addLog('▶ Saving & restarting '+curBot+'…','i');
  const res=await api('/editor/api/run',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({bot:curBot})
  });
  if(res.error){
    toast('❌ '+res.error,'err');
    addLog('❌ Run failed: '+res.error,'e');
    btn.classList.remove('busy'); btn.innerHTML='▶ <span>Run</span>';
    return;
  }
  addLog('⏳ Process stopping…','i');
  // Reconnect WebSocket after short delay so we get fresh output
  setTimeout(()=>{
    addLog('▶ Starting '+curBot+'…','i');
    connectWs(curBot);   // reconnect to get fresh log stream
    btn.classList.remove('busy'); btn.innerHTML='▶ <span>Run</span>';
    rf();
    toast('▶ Bot restarted!','ok');
  }, 2500);
}

// ── STOP ──────────────────────────────────────────────────────────────
async function stop(){
  if(!curBot){toast('Select a bot first','err');return;}
  showP('out');
  addLog('⏹ Stopping '+curBot+'…','w');
  const res=await api('/editor/api/stop',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({bot:curBot})
  });
  if(res.error){toast('❌ '+res.error,'err');addLog('❌ Stop failed: '+res.error,'e');return;}
  addLog('🛑 Stopped.','w'); toast('🛑 Bot stopped','ok'); rf();
}

// ── NEW BOT / FILE / DIR / DEL ────────────────────────────────────────
async function doNB(){
  const n=document.getElementById('nb-n').value.trim(); if(!n) return;
  const r=await api('/editor/api/newbot',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n})});
  cM('m-nb');
  if(r.error){toast('❌ '+r.error,'err');return;}
  toast('🤖 '+r.display+' created','ok');
  await rf(); openFile(r.main,'main.py',r.name);
}
async function doNF(){
  const n=document.getElementById('nf-n').value.trim(); if(!n||!curBot) return;
  const path=curBot+'/'+n;
  const r=await api('/editor/api/file',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({path,content:''})});
  cM('m-nf');
  if(r.error){toast('❌ '+r.error,'err');return;}
  await rf(); openFile(path,n,curBot);
}
async function doND(){
  const n=document.getElementById('nd-n').value.trim(); if(!n||!curBot) return;
  const r=await api('/editor/api/mkdir',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({path:curBot+'/'+n})});
  cM('m-nd');
  if(r.error){toast('❌ '+r.error,'err');return;}
  toast('📁 Created','ok'); rf();
}
async function delFile(){
  if(!curFile){toast('No file selected','err');return;}
  if(!confirm('Delete '+curFile+'?')) return;
  const r=await api('/editor/api/delete',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({path:curFile})});
  if(r.error){toast('❌ '+r.error,'err');return;}
  cTab(curFile,null); curFile=''; toast('🗑 Deleted','ok'); rf();
}

// ── UPLOAD ────────────────────────────────────────────────────────────
function showUL(){document.getElementById('ul-d').textContent=ulDest||'/';oM('m-ul');}
document.getElementById('finp').addEventListener('change',async e=>{
  await upFiles(e.target.files); e.target.value='';
});
async function upFiles(files){
  if(!files.length) return;
  const fd=new FormData(); fd.append('dir',ulDest);
  for(const f of files) fd.append('files',f,f.name);
  const r=await fetch('/editor/api/upload?token='+TOK,{method:'POST',body:fd}).then(r=>r.json());
  cM('m-ul');
  if(r.error){toast('❌ '+r.error,'err');return;}
  toast('⬆ '+r.saved.length+' file(s) uploaded','ok'); rf();
}
const dov=document.getElementById('dov');
document.addEventListener('dragenter',e=>{if(e.dataTransfer.types.includes('Files'))dov.classList.add('show');});
document.addEventListener('dragover',e=>e.preventDefault());
document.addEventListener('dragleave',e=>{if(!e.relatedTarget)dov.classList.remove('show');});
document.addEventListener('drop',async e=>{e.preventDefault();dov.classList.remove('show');await upFiles(e.dataTransfer.files);});

// ── WEBSOCKET — Output panel ──────────────────────────────────────────
function connectWs(bot){
  if(logWs){try{logWs.close();}catch(e){}}
  const pv=document.getElementById('pv-out');
  pv.innerHTML=`<div class="ll li">── Connecting to ${bot} ──</div>`;
  logWs=new WebSocket(`${WS}/editor/ws?token=${TOK}&bot=${encodeURIComponent(bot)}`);
  logWs.onmessage=e=>addLog(e.data);
  logWs.onclose=()=>addLog('── Stream closed ──','i');
  logWs.onerror=()=>addLog('── WebSocket error ──','e');
}

let _pbuf=[];
function addLog(txt,fc){
  const pv=document.getElementById('pv-out');
  const d=document.createElement('div');
  // Classify line
  const c=fc||(/error|exception|traceback/i.test(txt)?'e':
    /warn/i.test(txt)?'w':
    /▶|\[start\]|started|running|✅|🟢/i.test(txt)?'i':
    /ok|success|done|💡|\[heal\]/i.test(txt)?'o':
    /^─+$/.test(txt.trim())?'s':'');
  d.className='ll'+(c?' l'+c:'');
  // Highlight print() output from bot (plain text with no prefix)
  if(!c && txt.trim() && !txt.startsWith('[') && !txt.startsWith('─')){
    d.style.color='#e6edf3';  // bright white for bot print output
  }
  d.textContent=txt; pv.appendChild(d);
  if(pv.scrollHeight-pv.scrollTop<pv.clientHeight+80) pv.scrollTop=pv.scrollHeight;
  while(pv.children.length>3000) pv.removeChild(pv.firstChild);
  // Problems detection
  _pbuf.push(txt); if(_pbuf.length>80) _pbuf.shift();
  if(/^(ModuleNotFoundError|SyntaxError|NameError|TypeError|AttributeError|ValueError|ImportError|RuntimeError|IndentationError|KeyError|FileNotFoundError)/.test(txt.trim())){
    buildProb(_pbuf.join('\n')); _pbuf=[];
  }
}

// ── PROBLEMS TAB ──────────────────────────────────────────────────────
const HINTS=[
  [/ModuleNotFoundError: No module named '([^']+)'/,g=>`Install '${g[1]}' — add to requirements.txt`],
  [/SyntaxError: (.+)/,g=>`Syntax: ${g[1]} — check brackets/colons/quotes`],
  [/IndentationError: (.+)/,g=>`Indentation: ${g[1]} — use 4 spaces`],
  [/NameError: name '([^']+)'/,g=>`'${g[1]}' not defined — check spelling`],
  [/TypeError: (.+)/,g=>`Type error: ${g[1]}`],
  [/AttributeError: '([^']+)' object has no attribute '([^']+)'/,g=>`'${g[1]}' has no '${g[2]}'`],
  [/KeyError: '?([^'\n]+)'?/,g=>`Key '${g[1]}' not in dict`],
  [/ImportError: cannot import name '([^']+)' from '([^']+)'/,g=>`Wrong import '${g[1]}' from '${g[2]}'`],
  [/FileNotFoundError/,g=>'File not found — check path'],
  [/Unauthorized/,g=>'Unauthorized — check bot token'],
  [/Conflict/,g=>'Another bot instance running — auto-fixing'],
  [/ZeroDivisionError/,g=>'Division by zero'],
  [/RecursionError/,g=>'Infinite recursion — add a base case'],
];

function buildProb(text){
  let et='Error',em='',fn=null,ln=null,hint='Check Output panel.';
  for(const l of text.split('\n')){
    const m=l.match(/File "([^"]+)", line (\d+)/);
    if(m){fn=l.match(/([^/\\]+)\.py/)?.[0]||m[1].split('/').pop();ln=parseInt(m[2]);}
  }
  for(let i=text.split('\n').length-1;i>=0;i--){
    const l=text.split('\n')[i].trim();
    if(l&&!l.startsWith('File ')&&!l.startsWith('Traceback')&&!l.startsWith(' ')){
      em=l; et=l.split(':')[0].trim(); break;
    }
  }
  for(const[p,h]of HINTS){const m=text.match(p);if(m){hint=h(m);break;}}
  if(!em) return;
  probs.unshift({et,em,fn,ln,hint,ts:Date.now()});
  if(probs.length>50) probs.pop();
  renderProbs(); showP('prob');
}

function renderProbs(){
  const el=document.getElementById('pv-prob');
  const dot=document.getElementById('prob-dot');
  const badge=document.getElementById('err-badge');
  if(!probs.length){
    el.innerHTML='<div style="padding:10px 0;color:var(--tx2);font-size:12px">No problems detected. Run your bot to check.</div>';
    dot.classList.remove('show'); badge.style.display='none'; return;
  }
  dot.classList.add('show'); badge.textContent=String(probs.length); badge.style.display='';
  el.innerHTML=probs.map(p=>`
    <div class="prob">
      <span class="prob-ico">${p.et.includes('Syntax')||p.et.includes('Indent')?'✏️':'❌'}</span>
      <div class="prob-body">
        <div class="prob-msg"><b>${es(p.et)}:</b> ${es(p.em.slice(0,160))}</div>
        ${p.fn&&p.ln?`<div class="prob-loc">📍 ${es(p.fn)} — Line ${p.ln}</div>`:''}
        <div class="prob-hint">💡 ${es(p.hint)}</div>
      </div>
    </div>`).join('');
}

function clrPanel(){
  document.getElementById('pv-out').innerHTML='';
  probs=[]; renderProbs();
}
function panelBot(){
  const pv=document.getElementById('pv-'+curP);
  if(pv) pv.scrollTop=pv.scrollHeight;
}

// ── PANEL TAB SWITCH ──────────────────────────────────────────────────
function showP(id){
  curP=id;
  ['out','prob','term'].forEach(p=>{
    document.getElementById('pt-'+p)?.classList.toggle('act',p===id);
    document.getElementById('pv-'+p)?.classList.toggle('act',p===id);
  });
  if(!panelVis){panelVis=true;document.getElementById('panel').classList.remove('hide');
    document.getElementById('rpanel').style.display='';if(ed)ed.layout();}
}

// ── SIDEBAR / PANEL TOGGLE ────────────────────────────────────────────
function tSB(on){
  sbVis=on!==undefined?on:!sbVis;
  document.getElementById('sidebar').classList.toggle('hide',!sbVis);
  document.getElementById('rsb').style.display=sbVis?'':'none';
  document.getElementById('ab-e').classList.toggle('on',sbVis);
  document.getElementById('btn-sb').classList.toggle('on',sbVis);
  if(ed) ed.layout();
}
function tPanel(){
  panelVis=!panelVis;
  document.getElementById('panel').classList.toggle('hide',!panelVis);
  document.getElementById('rpanel').style.display=panelVis?'':'none';
  document.getElementById('btn-panel').classList.toggle('on',panelVis);
  if(ed) ed.layout();
}

// ── RESIZE SIDEBAR ────────────────────────────────────────────────────
(()=>{
  const rsz=document.getElementById('rsb'),sb=document.getElementById('sidebar');
  let dr=false,sx=0,sw=0;
  const st=e=>{if(!sbVis)return;dr=true;sx=e.clientX||(e.touches?.[0]?.clientX||0);sw=sb.offsetWidth;rsz.classList.add('on');e.preventDefault();};
  const mv=e=>{if(!dr)return;const cx=e.clientX||(e.touches?.[0]?.clientX||0);const w=Math.max(150,Math.min(400,sw+(cx-sx)));sb.style.width=w+'px';if(ed)ed.layout();};
  const en=()=>{dr=false;rsz.classList.remove('on');};
  rsz.addEventListener('mousedown',st);rsz.addEventListener('touchstart',st,{passive:false});
  document.addEventListener('mousemove',mv);document.addEventListener('touchmove',mv,{passive:true});
  document.addEventListener('mouseup',en);document.addEventListener('touchend',en);
})();

// ── RESIZE PANEL ──────────────────────────────────────────────────────
(()=>{
  const rsz=document.getElementById('rpanel'),panel=document.getElementById('panel');
  let dr=false,sy=0,sh=0;
  const st=e=>{if(!panelVis)return;dr=true;sy=e.clientY||(e.touches?.[0]?.clientY||0);sh=panel.offsetHeight;rsz.classList.add('on');e.preventDefault();};
  const mv=e=>{if(!dr)return;const cy=e.clientY||(e.touches?.[0]?.clientY||0);const h=Math.max(80,Math.min(window.innerHeight*0.65,sh-(cy-sy)));panel.style.height=h+'px';if(ed)ed.layout();};
  const en=()=>{dr=false;rsz.classList.remove('on');};
  rsz.addEventListener('mousedown',st);rsz.addEventListener('touchstart',st,{passive:false});
  document.addEventListener('mousemove',mv);document.addEventListener('touchmove',mv,{passive:true});
  document.addEventListener('mouseup',en);document.addEventListener('touchend',en);
})();

// ── KEYBOARD SHORTCUTS ────────────────────────────────────────────────
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA') return;
  const k=e.key.toLowerCase();
  if(!e.ctrlKey&&!e.metaKey&&k==='b') tSB();
  if(!e.ctrlKey&&!e.metaKey&&k==='t') tPanel();
  if(!e.ctrlKey&&!e.metaKey&&k==='`') showP('out');
  if((e.ctrlKey||e.metaKey)&&k==='s'){e.preventDefault();save();}
  if((e.ctrlKey||e.metaKey)&&k==='enter'){e.preventDefault();run();}
  if((e.ctrlKey||e.metaKey)&&k==='z') doUndo();
  if((e.ctrlKey||e.metaKey)&&(e.shiftKey&&k==='z'||k==='y')) doRedo();
  if((e.ctrlKey||e.metaKey)&&k==='n'){e.preventDefault();oM('m-nb');}
});

// ── MODALS ────────────────────────────────────────────────────────────
function oM(id){
  document.getElementById(id).classList.add('show');
  const inp=document.getElementById(id).querySelector('input');
  if(inp){inp.value='';setTimeout(()=>inp.focus(),50);}
}
function cM(id){document.getElementById(id).classList.remove('show');}
document.querySelectorAll('.mov').forEach(bg=>{
  bg.addEventListener('click',e=>{if(e.target===bg)bg.classList.remove('show');});
});
document.getElementById('nb-n').addEventListener('keydown',e=>e.key==='Enter'&&doNB());
document.getElementById('nf-n').addEventListener('keydown',e=>e.key==='Enter'&&doNF());
document.getElementById('nd-n').addEventListener('keydown',e=>e.key==='Enter'&&doND());

// ── TOAST ─────────────────────────────────────────────────────────────
let _tt;
function toast(msg,type='ok'){
  const el=document.getElementById('toast');
  el.textContent=msg; el.className='show '+type;
  clearTimeout(_tt); _tt=setTimeout(()=>el.className='',3000);
}

// ── HELPERS ───────────────────────────────────────────────────────────
function es(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function x(s){return es(s);}
function ID(s){return String(s).replace(/[^a-zA-Z0-9]/g,'_');}
function fIco(e){
  return{py:'🐍',js:'📜',ts:'📜',json:'{}',txt:'📄',md:'📝',sh:'⚙',
    yml:'⚙',yaml:'⚙',env:'🔑',html:'🌐',css:'🎨',png:'🖼',jpg:'🖼',
    gif:'🖼',zip:'📦',log:'📋',cfg:'⚙',ini:'⚙',toml:'⚙',sql:'🗄',db:'🗄'}[e]||'📄';
}
function gLang(n){
  const e=n.split('.').pop().toLowerCase();
  return{py:'python',js:'javascript',ts:'typescript',json:'json',html:'html',
    css:'css',sh:'shell',md:'markdown',yml:'yaml',yaml:'yaml',
    sql:'sql',txt:'plaintext',toml:'ini',cfg:'ini',ini:'ini'}[e]||'plaintext';
}
</script>
</body></html>"""

