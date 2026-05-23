"""
╔══════════════════════════════════════════════════════════════════════╗
║   MASTER HOSTING BOT  —  Web IDE Module                             ║
║   editor.py  ·  Codian Studio 💎                                    ║
╠══════════════════════════════════════════════════════════════════════╣
║  Features:                                                           ║
║  • Monaco Editor (VS Code engine via CDN)                            ║
║  • Live File Tree — all bots + all their files                       ║
║  • Multi-tab editor                                                  ║
║  • ▶ Run button — hot-reload child bot instantly                     ║
║  • Upload files / folders (drag & drop)                              ║
║  • Create new project / new file / new folder                        ║
║  • Delete files / folders                                            ║
║  • Real-time log stream (WebSocket)                                  ║
║  • Password-protected (EDITOR_TOKEN env var)                         ║
║  • Dark VS Code theme                                                ║
╚══════════════════════════════════════════════════════════════════════╝

This module is imported by main.py.
It registers its routes on the SAME aiohttp app (same port = 8080).

Routes added:
  GET  /editor              — IDE page (requires ?token=xxx or cookie)
  GET  /editor/api/tree     — JSON file tree
  GET  /editor/api/file     — read a file
  POST /editor/api/file     — write / create a file
  DEL  /editor/api/file     — delete a file
  POST /editor/api/mkdir    — create folder
  POST /editor/api/run      — restart a bot
  POST /editor/api/upload   — upload files (multipart)
  POST /editor/api/newbot   — create new bot scaffold
  GET  /editor/api/logs     — last N log lines (JSON)
  GET  /editor/ws           — WebSocket real-time log stream
"""

import os
import sys
import json
import time
import shutil
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
#  Config (read from env, same as main.py)
# ──────────────────────────────────────────────────────────────────────
EDITOR_TOKEN = os.environ.get("EDITOR_TOKEN", "codianstudio")   # change this!
HOSTED_DIR   = Path("hosted_bots")
BLOCKED_NAMES= {"_data", "_deleted", ".git", "__pycache__"}

# Reference to main.py's RUNNING_BOTS dict — injected via init_editor()
_RUNNING_BOTS: dict  = {}
_APP_REF             = None   # PTB Application ref for restart

def init_editor(running_bots: dict, app_ref=None) -> None:
    """Call this from main.py to share runtime state."""
    global _RUNNING_BOTS, _APP_REF
    _RUNNING_BOTS = running_bots
    _APP_REF      = app_ref

# ──────────────────────────────────────────────────────────────────────
#  Auth helpers
# ──────────────────────────────────────────────────────────────────────
def _authed(request: web.Request) -> bool:
    token = (
        request.rel_url.query.get("token")
        or request.cookies.get("ide_token")
    )
    return token == EDITOR_TOKEN

def _auth_response() -> web.Response:
    return web.Response(
        text         = _LOGIN_HTML,
        content_type = "text/html",
        status       = 401,
    )

# ──────────────────────────────────────────────────────────────────────
#  File tree builder
# ──────────────────────────────────────────────────────────────────────
def _build_tree(root: Path, rel: str = "") -> list:
    items = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        return items

    for entry in entries:
        if entry.name.startswith(".") or entry.name == "__pycache__":
            continue
        node = {
            "name":  entry.name,
            "path":  (rel + "/" + entry.name).lstrip("/"),
            "type":  "dir" if entry.is_dir() else "file",
            "ext":   entry.suffix.lstrip(".").lower() if entry.is_file() else "",
            "size":  entry.stat().st_size if entry.is_file() else 0,
        }
        if entry.is_dir():
            node["children"] = _build_tree(entry, node["path"])
        items.append(node)
    return items

def _bot_tree() -> list:
    """Return list of {name, status, children} for every hosted bot dir."""
    if not HOSTED_DIR.exists():
        return []
    bots = []
    for item in sorted(HOSTED_DIR.iterdir()):
        if not item.is_dir() or item.name in BLOCKED_NAMES:
            continue
        e      = _RUNNING_BOTS.get(item.name, {})
        status = e.get("status", "Offline 🔴")
        bots.append({
            "name":     item.name,
            "path":     item.name,
            "type":     "bot",
            "status":   status,
            "restarts": e.get("restarts", 0),
            "uptime":   _fmt_uptime(time.time() - e["start_time"]) if e.get("start_time") else "—",
            "children": _build_tree(item, item.name),
        })
    return bots

def _fmt_uptime(s: float) -> str:
    s = int(max(0, s))
    h, r   = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h}h {m}m {sec}s"

def _safe_path(raw: str) -> Optional[Path]:
    """Resolve path safely — must stay inside HOSTED_DIR."""
    try:
        full = (HOSTED_DIR / raw).resolve()
        if HOSTED_DIR.resolve() in full.parents or full == HOSTED_DIR.resolve():
            return full
    except Exception:
        pass
    return None

# ──────────────────────────────────────────────────────────────────────
#  API handlers
# ──────────────────────────────────────────────────────────────────────

async def api_tree(req: web.Request) -> web.Response:
    if not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response(_bot_tree())


async def api_read_file(req: web.Request) -> web.Response:
    if not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    raw  = req.rel_url.query.get("path", "")
    path = _safe_path(raw)
    if not path or not path.exists() or not path.is_file():
        return web.json_response({"error": "File not found"}, status=404)

    # Read up to 512 KB
    try:
        content = path.read_text(errors="replace")[:524288]
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)

    mime = mimetypes.guess_type(str(path))[0] or "text/plain"
    return web.json_response({
        "path":    raw,
        "name":    path.name,
        "content": content,
        "mime":    mime,
        "size":    path.stat().st_size,
    })


async def api_write_file(req: web.Request) -> web.Response:
    if not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body    = await req.json()
        raw     = body.get("path", "")
        content = body.get("content", "")
        path    = _safe_path(raw)
        if not path:
            return web.json_response({"error": "Invalid path"}, status=400)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return web.json_response({"ok": True, "path": raw, "size": len(content.encode())})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def api_delete_file(req: web.Request) -> web.Response:
    if not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await req.json()
        raw  = body.get("path", "")
        path = _safe_path(raw)
        if not path or not path.exists():
            return web.json_response({"error": "Not found"}, status=404)

        # Don't allow deleting an entire top-level bot dir through this endpoint
        if path.parent.resolve() == HOSTED_DIR.resolve() and path.is_dir():
            return web.json_response(
                {"error": "Use the Delete Bot button to remove an entire bot."}, status=400
            )

        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return web.json_response({"ok": True})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def api_mkdir(req: web.Request) -> web.Response:
    if not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await req.json()
        raw  = body.get("path", "")
        path = _safe_path(raw)
        if not path:
            return web.json_response({"error": "Invalid path"}, status=400)
        path.mkdir(parents=True, exist_ok=True)
        return web.json_response({"ok": True, "path": raw})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def api_upload(req: web.Request) -> web.Response:
    """Upload one or more files via multipart/form-data.
    Form fields:
      dir   — destination directory (relative to HOSTED_DIR)
      files — one or more file parts
    """
    if not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        reader  = await req.multipart()
        dest_raw = ""
        saved    = []

        async for part in reader:
            if part.name == "dir":
                dest_raw = (await part.read()).decode().strip()
            elif part.name == "files" or part.name and part.filename:
                dest_dir = _safe_path(dest_raw) if dest_raw else HOSTED_DIR
                if not dest_dir:
                    continue
                dest_dir.mkdir(parents=True, exist_ok=True)
                fname = part.filename or f"upload_{int(time.time())}"
                # strip any leading path from browser (keep basename)
                fname = Path(fname).name
                out   = dest_dir / fname
                data  = await part.read()
                out.write_bytes(data)
                saved.append(str(out.relative_to(HOSTED_DIR)))

        return web.json_response({"ok": True, "saved": saved})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def api_newbot(req: web.Request) -> web.Response:
    """Create a new bot scaffold: /hosted_bots/<name>/main.py + requirements.txt."""
    if not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await req.json()
        name = body.get("name", "").strip().replace(" ", "_").replace("/", "")
        if not name:
            return web.json_response({"error": "Bot name required"}, status=400)

        bot_dir = HOSTED_DIR / name
        if bot_dir.exists():
            return web.json_response({"error": f"Bot '{name}' already exists"}, status=409)

        bot_dir.mkdir(parents=True)
        # Scaffold main.py
        (bot_dir / "main.py").write_text(
            f'# {name} — created by Codian Studio Web IDE\n'
            f'# Write your bot code here\n\n'
            f'print("Hello from {name}!")\n',
            encoding="utf-8",
        )
        (bot_dir / "requirements.txt").write_text(
            "# Add your dependencies here\n# python-telegram-bot==20.8\n",
            encoding="utf-8",
        )
        return web.json_response({
            "ok": True, "name": name, "path": name,
            "main": f"{name}/main.py",
        })
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def api_run(req: web.Request) -> web.Response:
    """Restart (or start) a bot process. Returns immediately."""
    if not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body     = await req.json()
        bot_name = body.get("bot", "").strip()
        if not bot_name:
            return web.json_response({"error": "Bot name required"}, status=400)

        bot_dir = HOSTED_DIR / bot_name
        if not (bot_dir / "main.py").exists():
            return web.json_response({"error": "main.py not found"}, status=404)

        # Signal main.py to restart via a simple flag file
        # main.py's run_bot loop checks for this and restarts
        flag = bot_dir / ".restart_flag"
        flag.write_text(str(time.time()))

        # Also stop existing process so run_bot loop picks it up
        e = _RUNNING_BOTS.get(bot_name)
        if e:
            e["active"] = False
            p = e.get("process")
            if p and p.poll() is None:
                p.terminate()

        return web.json_response({"ok": True, "bot": bot_name, "restarting": True})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def api_logs(req: web.Request) -> web.Response:
    if not _authed(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    bot_name = req.rel_url.query.get("bot", "")
    n        = int(req.rel_url.query.get("n", 100))
    log_path = HOSTED_DIR / bot_name / "bot_output.log"
    try:
        lines = log_path.read_text(errors="replace").splitlines()[-n:]
        return web.json_response({"bot": bot_name, "lines": lines})
    except Exception:
        return web.json_response({"bot": bot_name, "lines": []})


# ──────────────────────────────────────────────────────────────────────
#  WebSocket — real-time log tail
# ──────────────────────────────────────────────────────────────────────
async def ws_logs(req: web.Request) -> web.WebSocketResponse:
    token    = req.rel_url.query.get("token", "")
    bot_name = req.rel_url.query.get("bot",   "")
    if token != EDITOR_TOKEN:
        raise web.HTTPUnauthorized()

    ws       = web.WebSocketResponse()
    await ws.prepare(req)
    log_path = HOSTED_DIR / bot_name / "bot_output.log"
    pos      = 0

    # Send last 50 lines immediately
    try:
        text  = log_path.read_text(errors="replace")
        lines = text.splitlines()[-50:]
        for ln in lines:
            await ws.send_str(ln)
        pos = log_path.stat().st_size
    except Exception:
        pass

    # Stream new lines as they appear
    try:
        while not ws.closed:
            await asyncio.sleep(1)
            try:
                size = log_path.stat().st_size
                if size > pos:
                    with open(log_path, "r", errors="replace") as f:
                        f.seek(pos)
                        new_text = f.read()
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
#  IDE HTML page
# ──────────────────────────────────────────────────────────────────────
async def editor_page(req: web.Request) -> web.Response:
    if not _authed(req):
        # Accept token via query, set cookie, redirect
        token = req.rel_url.query.get("token", "")
        if token == EDITOR_TOKEN:
            resp = web.HTTPFound(location="/editor")
            resp.set_cookie("ide_token", token, max_age=86400 * 30, httponly=True)
            return resp
        return _auth_response()

    return web.Response(text=_IDE_HTML, content_type="text/html")


def register_routes(app: web.Application) -> None:
    """Call from main.py after creating the aiohttp app."""
    app.router.add_get ("/editor",            editor_page)
    app.router.add_get ("/editor/api/tree",   api_tree)
    app.router.add_get ("/editor/api/file",   api_read_file)
    app.router.add_post("/editor/api/file",   api_write_file)
    app.router.add_post("/editor/api/delete", api_delete_file)
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
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Codian Studio — Login</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:#0d1117;color:#c9d1d9;font-family:'Segoe UI',sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh}
  .card{background:#161b22;border:1px solid #30363d;border-radius:12px;
        padding:40px;width:340px;text-align:center;box-shadow:0 8px 32px #0008}
  .logo{font-size:2.4rem;margin-bottom:8px}
  h1{font-size:1.2rem;font-weight:600;margin-bottom:4px;color:#58a6ff}
  p{color:#8b949e;font-size:.85rem;margin-bottom:24px}
  input{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;
        padding:10px 14px;color:#c9d1d9;font-size:1rem;outline:none;margin-bottom:16px}
  input:focus{border-color:#58a6ff}
  button{width:100%;background:#238636;border:none;border-radius:6px;
         padding:11px;color:#fff;font-size:1rem;font-weight:600;cursor:pointer;
         transition:.2s}
  button:hover{background:#2ea043}
  .err{color:#f85149;font-size:.82rem;margin-top:8px;display:none}
</style>
</head>
<body>
<div class="card">
  <div class="logo">💎</div>
  <h1>Codian Studio</h1>
  <p>Master Hosting Bot — Web IDE</p>
  <input type="password" id="tok" placeholder="Enter access token" onkeydown="if(event.key==='Enter')login()">
  <button onclick="login()">🔐 Login</button>
  <div class="err" id="err">Invalid token. Try again.</div>
</div>
<script>
function login(){
  const t=document.getElementById('tok').value.trim();
  if(!t)return;
  window.location.href='/editor?token='+encodeURIComponent(t);
}
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════
#  FULL IDE HTML  (Monaco Editor + File Tree + Logs)
# ══════════════════════════════════════════════════════════════════════
_IDE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Codian Studio 💎 — Web IDE</title>
<style>
/* ── Reset ── */
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg0:#0d1117;--bg1:#161b22;--bg2:#1c2128;--bg3:#21262d;
  --border:#30363d;--accent:#58a6ff;--green:#3fb950;
  --red:#f85149;--yellow:#d29922;--purple:#bc8cff;
  --text:#c9d1d9;--text2:#8b949e;--text3:#6e7681;
  --sidebar:240px;--topbar:44px
}
html,body{height:100%;overflow:hidden;background:var(--bg0);color:var(--text);
          font-family:'Segoe UI',system-ui,sans-serif;font-size:13px}

/* ── Layout ── */
#layout{display:grid;height:100vh;
        grid-template-rows:var(--topbar) 1fr;
        grid-template-columns:var(--sidebar) 1fr;
        grid-template-areas:"topbar topbar" "sidebar main"}

/* ── Topbar ── */
#topbar{grid-area:topbar;background:var(--bg1);border-bottom:1px solid var(--border);
        display:flex;align-items:center;gap:8px;padding:0 14px;user-select:none}
#topbar .logo{font-size:1.1rem;font-weight:700;color:var(--accent);margin-right:12px}
.tbtn{display:flex;align-items:center;gap:5px;background:var(--bg3);
      border:1px solid var(--border);border-radius:6px;padding:4px 10px;
      cursor:pointer;font-size:12px;color:var(--text);transition:.15s}
.tbtn:hover{background:var(--accent);color:#fff;border-color:var(--accent)}
.tbtn.run{background:#1a4a1f;border-color:var(--green);color:var(--green)}
.tbtn.run:hover{background:var(--green);color:#fff}
.tbtn.danger{background:#3d1a1a;border-color:var(--red);color:var(--red)}
.tbtn.danger:hover{background:var(--red);color:#fff}
#active-bot{flex:1;text-align:center;font-weight:600;color:var(--purple);font-size:12px}
#status-pill{padding:3px 10px;border-radius:20px;font-size:11px;background:var(--bg3);
             border:1px solid var(--border)}

/* ── Sidebar ── */
#sidebar{grid-area:sidebar;background:var(--bg1);border-right:1px solid var(--border);
         display:flex;flex-direction:column;overflow:hidden}
#sidebar-head{padding:8px 10px;border-bottom:1px solid var(--border);
              display:flex;align-items:center;gap:6px}
#sidebar-head span{flex:1;font-weight:600;font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:.5px}
.icon-btn{background:none;border:none;color:var(--text2);cursor:pointer;
          font-size:15px;padding:2px 4px;border-radius:4px;line-height:1}
.icon-btn:hover{background:var(--bg3);color:var(--text)}
#tree{flex:1;overflow-y:auto;padding:4px 0}
#tree::-webkit-scrollbar{width:4px}
#tree::-webkit-scrollbar-track{background:transparent}
#tree::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
.tree-bot{margin-bottom:2px}
.bot-header{display:flex;align-items:center;gap:5px;padding:5px 8px;
            cursor:pointer;border-radius:4px;transition:.1s;position:relative}
.bot-header:hover{background:var(--bg3)}
.bot-header.active-bot{background:#1a2f3a;border-left:2px solid var(--accent)}
.bot-caret{font-size:10px;color:var(--text3);transition:transform .2s;flex-shrink:0}
.bot-caret.open{transform:rotate(90deg)}
.bot-icon{font-size:15px}
.bot-name{flex:1;font-weight:600;font-size:12px;overflow:hidden;
          text-overflow:ellipsis;white-space:nowrap}
.bot-status{font-size:9px;padding:1px 5px;border-radius:8px;white-space:nowrap}
.bot-status.running{background:#1a3d1f;color:var(--green)}
.bot-status.stopped{background:#3d1a1a;color:var(--red)}
.bot-status.other{background:var(--bg3);color:var(--text2)}
.bot-children{display:none;padding-left:14px}
.bot-children.open{display:block}
.tree-item{display:flex;align-items:center;gap:5px;padding:3px 8px;
           cursor:pointer;border-radius:4px;transition:.1s;
           user-select:none}
.tree-item:hover{background:var(--bg3)}
.tree-item.active{background:#1a2f3a;color:var(--accent)}
.tree-item.dir-item{color:var(--yellow)}
.tree-dir-children{display:none;padding-left:14px}
.tree-dir-children.open{display:block}
.file-icon{font-size:13px;flex-shrink:0}
.file-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* ── Main area ── */
#main{grid-area:main;display:flex;flex-direction:column;overflow:hidden}
#tabs{display:flex;background:var(--bg1);border-bottom:1px solid var(--border);
      overflow-x:auto;flex-shrink:0}
#tabs::-webkit-scrollbar{height:3px}
#tabs::-webkit-scrollbar-thumb{background:var(--border)}
.tab{display:flex;align-items:center;gap:6px;padding:0 14px;height:36px;
     cursor:pointer;border-right:1px solid var(--border);white-space:nowrap;
     font-size:12px;color:var(--text2);transition:.1s;flex-shrink:0;position:relative}
.tab:hover{background:var(--bg3);color:var(--text)}
.tab.active{background:var(--bg0);color:var(--text);border-bottom:2px solid var(--accent)}
.tab-close{opacity:.5;font-size:14px;line-height:1;border-radius:3px;padding:1px 3px}
.tab-close:hover{opacity:1;background:var(--red);color:#fff}
.tab-dirty::after{content:'●';color:var(--yellow);font-size:10px;margin-left:2px}
#editor-wrap{flex:1;position:relative;overflow:hidden}
#monaco-container{width:100%;height:100%}
.editor-placeholder{display:flex;flex-direction:column;align-items:center;
                    justify-content:center;height:100%;color:var(--text3);gap:10px}
.editor-placeholder .big{font-size:3rem}
.editor-placeholder p{font-size:13px}

/* ── Log Panel ── */
#log-panel{height:180px;background:var(--bg1);border-top:1px solid var(--border);
           display:flex;flex-direction:column;flex-shrink:0}
#log-head{display:flex;align-items:center;gap:8px;padding:4px 12px;
          border-bottom:1px solid var(--border);font-size:11px;
          color:var(--text2);text-transform:uppercase;letter-spacing:.5px}
#log-head span{flex:1}
#log-body{flex:1;overflow-y:auto;padding:6px 10px;font-family:'Cascadia Code','Fira Code',monospace;
          font-size:11px;line-height:1.6;color:#abb2bf}
#log-body::-webkit-scrollbar{width:4px}
#log-body::-webkit-scrollbar-thumb{background:var(--border)}
.log-line{white-space:pre-wrap;word-break:break-all}
.log-err{color:var(--red)}
.log-warn{color:var(--yellow)}
.log-info{color:var(--green)}

/* ── Modal ── */
.modal-bg{display:none;position:fixed;inset:0;background:#000a;
          align-items:center;justify-content:center;z-index:999}
.modal-bg.show{display:flex}
.modal{background:var(--bg1);border:1px solid var(--border);border-radius:10px;
       padding:24px;min-width:320px;max-width:460px;box-shadow:0 8px 32px #0008}
.modal h3{margin-bottom:16px;font-size:1rem;color:var(--accent)}
.modal input,.modal select{width:100%;background:var(--bg0);border:1px solid var(--border);
                            border-radius:6px;padding:8px 12px;color:var(--text);
                            font-size:13px;margin-bottom:12px;outline:none}
.modal input:focus{border-color:var(--accent)}
.modal-btns{display:flex;gap:8px;justify-content:flex-end;margin-top:4px}
.mbtn{padding:7px 16px;border-radius:6px;border:1px solid var(--border);
      cursor:pointer;font-size:13px;transition:.15s}
.mbtn.ok{background:var(--accent);border-color:var(--accent);color:#fff}
.mbtn.ok:hover{opacity:.85}
.mbtn.cancel{background:var(--bg3);color:var(--text)}
.mbtn.cancel:hover{background:var(--border)}

/* ── Drop zone overlay ── */
#dropzone{display:none;position:fixed;inset:0;background:#58a6ff22;
          border:3px dashed var(--accent);z-index:900;
          align-items:center;justify-content:center;
          font-size:1.5rem;color:var(--accent);pointer-events:none}
#dropzone.show{display:flex}

/* ── Toast ── */
#toast{position:fixed;bottom:20px;right:20px;background:var(--bg3);
       border:1px solid var(--border);border-radius:8px;padding:10px 16px;
       font-size:13px;opacity:0;transition:.3s;z-index:9999;max-width:300px}
#toast.show{opacity:1}
#toast.ok{border-color:var(--green);color:var(--green)}
#toast.err{border-color:var(--red);color:var(--red)}

/* ── Upload input hidden ── */
#file-input{display:none}
</style>
</head>
<body>

<!-- Upload input -->
<input type="file" id="file-input" multiple>

<!-- Drop zone -->
<div id="dropzone">📁 Drop files here to upload</div>

<!-- Toast -->
<div id="toast"></div>

<!-- Modals -->
<div class="modal-bg" id="modal-newbot">
  <div class="modal">
    <h3>🤖 Create New Bot</h3>
    <input id="nb-name" placeholder="Bot name (e.g. mybot)" maxlength="40">
    <div class="modal-btns">
      <button class="mbtn cancel" onclick="closeModal('modal-newbot')">Cancel</button>
      <button class="mbtn ok" onclick="doNewBot()">Create</button>
    </div>
  </div>
</div>

<div class="modal-bg" id="modal-newfile">
  <div class="modal">
    <h3>📄 New File</h3>
    <input id="nf-name" placeholder="filename.py">
    <div class="modal-btns">
      <button class="mbtn cancel" onclick="closeModal('modal-newfile')">Cancel</button>
      <button class="mbtn ok" onclick="doNewFile()">Create</button>
    </div>
  </div>
</div>

<div class="modal-bg" id="modal-newfolder">
  <div class="modal">
    <h3>📁 New Folder</h3>
    <input id="nfld-name" placeholder="folder-name">
    <div class="modal-btns">
      <button class="mbtn cancel" onclick="closeModal('modal-newfolder')">Cancel</button>
      <button class="mbtn ok" onclick="doNewFolder()">Create</button>
    </div>
  </div>
</div>

<div class="modal-bg" id="modal-upload">
  <div class="modal">
    <h3>⬆️ Upload Files</h3>
    <p style="color:var(--text2);margin-bottom:12px;font-size:12px">
      Files will be uploaded to: <b id="upload-dest-label">/</b>
    </p>
    <button class="mbtn ok" onclick="document.getElementById('file-input').click()" style="width:100%;margin-bottom:10px">
      📂 Choose Files
    </button>
    <div id="upload-drop-area" style="border:2px dashed var(--border);border-radius:8px;
         padding:20px;text-align:center;color:var(--text3);font-size:12px">
      or drag & drop files here
    </div>
    <div class="modal-btns" style="margin-top:12px">
      <button class="mbtn cancel" onclick="closeModal('modal-upload')">Close</button>
    </div>
  </div>
</div>

<!-- LAYOUT -->
<div id="layout">

  <!-- TOPBAR -->
  <div id="topbar">
    <div class="logo">💎 Codian Studio</div>
    <button class="tbtn" onclick="openModal('modal-newbot')">＋ New Bot</button>
    <button class="tbtn" onclick="showUpload()">⬆ Upload</button>
    <button class="tbtn" onclick="openModal('modal-newfile')" id="btn-newfile" style="display:none">📄 New File</button>
    <button class="tbtn" onclick="openModal('modal-newfolder')" id="btn-newfolder" style="display:none">📁 New Folder</button>
    <div id="active-bot">No bot selected</div>
    <div id="status-pill">—</div>
    <button class="tbtn" onclick="saveFile()" title="Ctrl+S">💾 Save</button>
    <button class="tbtn run" onclick="runBot()" id="btn-run">▶ Run</button>
    <button class="tbtn" onclick="toggleLog()">📋 Logs</button>
    <button class="tbtn danger" onclick="confirmDeleteFile()" id="btn-del">🗑 Delete</button>
  </div>

  <!-- SIDEBAR -->
  <div id="sidebar">
    <div id="sidebar-head">
      <span>Explorer</span>
      <button class="icon-btn" onclick="refreshTree()" title="Refresh">⟳</button>
    </div>
    <div id="tree">
      <div style="padding:20px;text-align:center;color:var(--text3)">Loading…</div>
    </div>
  </div>

  <!-- MAIN -->
  <div id="main">
    <div id="tabs"></div>
    <div id="editor-wrap">
      <div id="monaco-container"></div>
      <div class="editor-placeholder" id="placeholder">
        <div class="big">💎</div>
        <p>Select a file from the sidebar to start editing</p>
        <p style="font-size:11px;color:var(--text3)">Ctrl+S to save · ▶ Run to restart bot</p>
      </div>
    </div>
    <div id="log-panel">
      <div id="log-head">
        <span>Terminal / Logs</span>
        <button class="icon-btn" onclick="clearLog()">✕</button>
        <button class="icon-btn" onclick="scrollLogBottom()">⬇</button>
      </div>
      <div id="log-body"></div>
    </div>
  </div>

</div>

<!-- Monaco Editor via CDN -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.46.0/min/vs/loader.min.js"></script>
<script>
'use strict';

// ── State ───────────────────────────────────────────────────────────
const TOKEN   = document.cookie.match(/ide_token=([^;]+)/)?.[1]
              || new URLSearchParams(location.search).get('token')
              || '';
const API     = (p, opts={}) => fetch(p + (p.includes('?')?'&':'?') + 'token='+TOKEN, opts).then(r=>r.json());
const WS_BASE = (location.protocol==='https:'?'wss://':'ws://') + location.host;

let monacoEditor = null;
let tree         = [];
let tabs         = [];   // [{path, name, dirty, model}]
let activeTab    = null;
let activeBotName= '';
let activeFilePath = '';
let logWs        = null;
let uploadDest   = '';
let statusInterval = null;

// ── Monaco init ─────────────────────────────────────────────────────
require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.46.0/min/vs' } });
require(['vs/editor/editor.main'], () => {
  monacoEditor = monaco.editor.create(document.getElementById('monaco-container'), {
    value:           '',
    language:        'python',
    theme:           'vs-dark',
    automaticLayout: true,
    fontSize:        14,
    fontFamily:      "'Cascadia Code','Fira Code','Consolas',monospace",
    fontLigatures:   true,
    minimap:         { enabled: true },
    scrollBeyondLastLine: false,
    lineNumbers:     'on',
    roundedSelection: false,
    padding:         { top: 10 },
    smoothScrolling: true,
    cursorBlinking:  'smooth',
    bracketPairColorization: { enabled: true },
    suggest:         { showIcons: true },
  });

  // Ctrl+S → save
  monacoEditor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveFile);

  monacoEditor.onDidChangeModelContent(() => {
    if (activeTab) {
      activeTab.dirty = true;
      renderTabs();
    }
  });

  hidePlaceholder();
  refreshTree();
  setInterval(refreshTree, 30000);   // auto-refresh sidebar every 30s
});

// ── File tree ────────────────────────────────────────────────────────
async function refreshTree() {
  const data = await API('/editor/api/tree');
  tree = Array.isArray(data) ? data : [];
  renderTree();
  updateStatus();
}

function renderTree() {
  const el = document.getElementById('tree');
  if (!tree.length) {
    el.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text3)">No bots yet.<br>Click ＋ New Bot</div>';
    return;
  }
  el.innerHTML = tree.map(renderBotNode).join('');
}

function renderBotNode(bot) {
  const open   = bot.name === activeBotName;
  const active = bot.name === activeBotName ? 'active-bot' : '';
  const st     = (bot.status||'').toLowerCase();
  const pill   = st.includes('running') ? 'running' : st.includes('stop')||st.includes('error') ? 'stopped' : 'other';
  return `
  <div class="tree-bot" id="bot-${bot.name}">
    <div class="bot-header ${active}" onclick="toggleBot('${bot.name}',this)">
      <span class="bot-caret ${open?'open':''}">▶</span>
      <span class="bot-icon">🤖</span>
      <span class="bot-name">${esc(bot.name)}</span>
      <span class="bot-status ${pill}">${esc(bot.status||'—')}</span>
    </div>
    <div class="bot-children ${open?'open':''}" id="children-${bot.name}">
      ${(bot.children||[]).map(c=>renderFileNode(c, bot.name)).join('')}
    </div>
  </div>`;
}

function renderFileNode(node, botName) {
  const active = node.path === activeFilePath ? 'active' : '';
  if (node.type === 'dir') {
    return `
    <div>
      <div class="tree-item dir-item" onclick="toggleDir(this,'dir-${node.path.replace(/\//g,'_')}')">
        <span class="file-icon">📁</span>
        <span class="file-name">${esc(node.name)}</span>
      </div>
      <div class="tree-dir-children" id="dir-${node.path.replace(/\//g,'_')}">
        ${(node.children||[]).map(c=>renderFileNode(c, botName)).join('')}
      </div>
    </div>`;
  }
  return `<div class="tree-item ${active}" onclick="openFile('${node.path}','${esc(node.name)}','${botName}')">
    <span class="file-icon">${fileIcon(node.ext)}</span>
    <span class="file-name">${esc(node.name)}</span>
  </div>`;
}

function toggleBot(name, hdr) {
  const children = document.getElementById('children-'+name);
  const caret    = hdr.querySelector('.bot-caret');
  const isOpen   = children.classList.contains('open');
  children.classList.toggle('open', !isOpen);
  caret.classList.toggle('open', !isOpen);
  if (!isOpen) {
    selectBot(name);
  }
}

function selectBot(name) {
  activeBotName = name;
  document.getElementById('active-bot').textContent = '🤖 ' + name;
  document.getElementById('btn-newfile').style.display = '';
  document.getElementById('btn-newfolder').style.display = '';
  uploadDest = name;
  connectLogWs(name);
  updateStatus();
  // Re-render tree to show active
  renderTree();
}

function toggleDir(el, id) {
  const ch = document.getElementById(id);
  if (ch) ch.classList.toggle('open');
}

function updateStatus() {
  const pill = document.getElementById('status-pill');
  if (!activeBotName) { pill.textContent = '—'; return; }
  const bot = tree.find(b=>b.name===activeBotName);
  if (!bot) { pill.textContent = '—'; return; }
  pill.textContent = bot.status || '—';
  const st = (bot.status||'').toLowerCase();
  pill.style.color = st.includes('running')?'var(--green)':st.includes('error')?'var(--red)':'var(--text2)';
}

// ── File open / tabs ─────────────────────────────────────────────────
async function openFile(path, name, botName) {
  selectBot(botName);
  activeFilePath = path;

  // Already open?
  let tab = tabs.find(t=>t.path===path);
  if (tab) { activateTab(tab); return; }

  const res = await API(`/editor/api/file?path=${encodeURIComponent(path)}`);
  if (res.error) { toast('❌ '+res.error, 'err'); return; }

  const lang  = guessLang(name);
  const model = monaco.editor.createModel(res.content, lang);
  tab = { path, name, dirty: false, model, botName };
  tabs.push(tab);
  activateTab(tab);
  renderTree();
}

function activateTab(tab) {
  activeTab = tab;
  if (monacoEditor) {
    monacoEditor.setModel(tab.model);
    hidePlaceholder();
  }
  activeFilePath = tab.path;
  activeBotName  = activeBotName || tab.botName;
  renderTabs();
}

function closeTab(path, e) {
  e && e.stopPropagation();
  const idx = tabs.findIndex(t=>t.path===path);
  if (idx===-1) return;
  tabs[idx].model.dispose();
  tabs.splice(idx, 1);
  if (activeTab && activeTab.path===path) {
    activeTab = tabs[idx] || tabs[idx-1] || null;
    if (activeTab) { activateTab(activeTab); }
    else {
      if (monacoEditor) monacoEditor.setModel(null);
      showPlaceholder();
    }
  }
  renderTabs();
}

function renderTabs() {
  const el = document.getElementById('tabs');
  el.innerHTML = tabs.map(t=>`
    <div class="tab ${t===activeTab?'active':''} ${t.dirty?'tab-dirty':''}"
         onclick="activateTab(tabs.find(x=>x.path==='${t.path}'))">
      <span>${fileIcon(t.name.split('.').pop())} ${esc(t.name)}</span>
      <span class="tab-close" onclick="closeTab('${t.path}',event)">✕</span>
    </div>`).join('');
}

// ── Save ─────────────────────────────────────────────────────────────
async function saveFile() {
  if (!activeTab || !monacoEditor) return;
  const content = monacoEditor.getValue();
  const res = await API('/editor/api/file', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ path: activeTab.path, content }),
  });
  if (res.error) { toast('❌ Save failed: '+res.error,'err'); return; }
  activeTab.dirty = false;
  renderTabs();
  toast('💾 Saved — ' + activeTab.name, 'ok');
}

// ── Run bot ──────────────────────────────────────────────────────────
async function runBot() {
  if (!activeBotName) { toast('Select a bot first', 'err'); return; }
  // Auto-save current file first
  if (activeTab && activeTab.dirty) await saveFile();
  const res = await API('/editor/api/run', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ bot: activeBotName }),
  });
  if (res.error) { toast('❌ '+res.error,'err'); return; }
  toast('▶ Restarting ' + activeBotName + '…', 'ok');
  setTimeout(refreshTree, 3000);
}

// ── New bot ───────────────────────────────────────────────────────────
async function doNewBot() {
  const name = document.getElementById('nb-name').value.trim();
  if (!name) return;
  const res = await API('/editor/api/newbot', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ name }),
  });
  closeModal('modal-newbot');
  if (res.error) { toast('❌ '+res.error,'err'); return; }
  toast('🤖 Bot created: '+res.name,'ok');
  await refreshTree();
  openFile(res.main, 'main.py', res.name);
}

// ── New file ─────────────────────────────────────────────────────────
async function doNewFile() {
  const name = document.getElementById('nf-name').value.trim();
  if (!name || !activeBotName) return;
  const path = activeBotName + '/' + name;
  const res = await API('/editor/api/file', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ path, content: '' }),
  });
  closeModal('modal-newfile');
  if (res.error) { toast('❌ '+res.error,'err'); return; }
  await refreshTree();
  openFile(path, name, activeBotName);
}

// ── New folder ────────────────────────────────────────────────────────
async function doNewFolder() {
  const name = document.getElementById('nfld-name').value.trim();
  if (!name || !activeBotName) return;
  const path = activeBotName + '/' + name;
  const res = await API('/editor/api/mkdir', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ path }),
  });
  closeModal('modal-newfolder');
  if (res.error) { toast('❌ '+res.error,'err'); return; }
  toast('📁 Folder created','ok');
  refreshTree();
}

// ── Delete file ───────────────────────────────────────────────────────
async function confirmDeleteFile() {
  if (!activeFilePath) { toast('No file selected','err'); return; }
  if (!confirm('Delete ' + activeFilePath + '?')) return;
  const res = await API('/editor/api/delete', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ path: activeFilePath }),
  });
  if (res.error) { toast('❌ '+res.error,'err'); return; }
  closeTab(activeFilePath, null);
  activeFilePath = '';
  toast('🗑 Deleted','ok');
  refreshTree();
}

// ── Upload ────────────────────────────────────────────────────────────
function showUpload() {
  document.getElementById('upload-dest-label').textContent = uploadDest || '/';
  openModal('modal-upload');
}

document.getElementById('file-input').addEventListener('change', async e => {
  await uploadFiles(e.target.files);
  e.target.value = '';
});

async function uploadFiles(files) {
  if (!files.length) return;
  const fd = new FormData();
  fd.append('dir', uploadDest);
  for (const f of files) fd.append('files', f, f.name);
  const res = await fetch('/editor/api/upload?token='+TOKEN, { method:'POST', body:fd }).then(r=>r.json());
  closeModal('modal-upload');
  if (res.error) { toast('❌ '+res.error,'err'); return; }
  toast('⬆ Uploaded '+res.saved.length+' file(s)','ok');
  refreshTree();
}

// ── Drag & drop global ────────────────────────────────────────────────
document.addEventListener('dragenter', e => {
  if (e.dataTransfer.types.includes('Files')) {
    document.getElementById('dropzone').classList.add('show');
  }
});
document.addEventListener('dragover', e => e.preventDefault());
document.addEventListener('dragleave', e => {
  if (!e.relatedTarget) document.getElementById('dropzone').classList.remove('show');
});
document.addEventListener('drop', async e => {
  e.preventDefault();
  document.getElementById('dropzone').classList.remove('show');
  await uploadFiles(e.dataTransfer.files);
});

// Also wire the modal drop area
document.getElementById('upload-drop-area').addEventListener('dragover', e=>e.preventDefault());
document.getElementById('upload-drop-area').addEventListener('drop', async e=>{
  e.preventDefault();
  await uploadFiles(e.dataTransfer.files);
});

// ── WebSocket logs ────────────────────────────────────────────────────
function connectLogWs(botName) {
  if (logWs) { try{logWs.close()}catch(e){} }
  const lb = document.getElementById('log-body');
  lb.innerHTML = `<div class="log-info">── Connecting to ${botName} log stream… ──</div>`;
  logWs = new WebSocket(`${WS_BASE}/editor/ws?token=${TOKEN}&bot=${encodeURIComponent(botName)}`);
  logWs.onmessage = e => appendLog(e.data);
  logWs.onclose   = ()=> appendLog('── Stream closed ──');
  logWs.onerror   = ()=> appendLog('── WebSocket error ──');
}

function appendLog(text) {
  const lb = document.getElementById('log-body');
  const div = document.createElement('div');
  div.className = 'log-line' + (
    /error|exception|traceback/i.test(text) ? ' log-err'  :
    /warn/i.test(text)                      ? ' log-warn' :
    /info|ok|start/i.test(text)             ? ' log-info' : '');
  div.textContent = text;
  lb.appendChild(div);
  // auto-scroll if near bottom
  if (lb.scrollHeight - lb.scrollTop < lb.clientHeight + 80)
    lb.scrollTop = lb.scrollHeight;
  // Cap at 2000 lines
  while (lb.children.length > 2000) lb.removeChild(lb.firstChild);
}

function clearLog() { document.getElementById('log-body').innerHTML = ''; }
function scrollLogBottom() {
  const lb = document.getElementById('log-body');
  lb.scrollTop = lb.scrollHeight;
}

function toggleLog() {
  const p = document.getElementById('log-panel');
  p.style.display = p.style.display === 'none' ? '' : 'none';
  if (monacoEditor) monacoEditor.layout();
}

// ── Modal helpers ─────────────────────────────────────────────────────
function openModal(id) {
  document.getElementById(id).classList.add('show');
  const inp = document.getElementById(id).querySelector('input');
  if (inp) { inp.value=''; setTimeout(()=>inp.focus(),50); }
}
function closeModal(id) { document.getElementById(id).classList.remove('show'); }

// Close modal on backdrop click
document.querySelectorAll('.modal-bg').forEach(bg=>{
  bg.addEventListener('click', e=>{ if(e.target===bg) bg.classList.remove('show'); });
});

// Enter key in modals
document.getElementById('nb-name').addEventListener('keydown',   e=>e.key==='Enter'&&doNewBot());
document.getElementById('nf-name').addEventListener('keydown',   e=>e.key==='Enter'&&doNewFile());
document.getElementById('nfld-name').addEventListener('keydown', e=>e.key==='Enter'&&doNewFolder());

// ── Toast ─────────────────────────────────────────────────────────────
let _toastTimer;
function toast(msg, type='ok') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className   = 'show ' + type;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(()=>el.className='', 3000);
}

// ── Placeholder ───────────────────────────────────────────────────────
function showPlaceholder() { document.getElementById('placeholder').style.display=''; }
function hidePlaceholder() { document.getElementById('placeholder').style.display='none'; }

// ── Helpers ───────────────────────────────────────────────────────────
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function fileIcon(ext) {
  const m = {
    py:'🐍',js:'📜',json:'📋',txt:'📄',md:'📝',
    sh:'⚙️',yml:'⚙️',yaml:'⚙️',env:'🔑',
    html:'🌐',css:'🎨',png:'🖼',jpg:'🖼',gif:'🖼',
    zip:'📦',gz:'📦',log:'📋',cfg:'⚙️',ini:'⚙️',
    toml:'⚙️',requirements:'📋',
  };
  return m[ext] || '📄';
}

function guessLang(name) {
  const ext = name.split('.').pop().toLowerCase();
  const map  = {
    py:'python',js:'javascript',json:'json',
    html:'html',css:'css',sh:'shell',
    md:'markdown',yml:'yaml',yaml:'yaml',
    txt:'plaintext',toml:'ini',cfg:'ini',ini:'ini',
  };
  return map[ext] || 'plaintext';
}

// Keyboard shortcut hint
document.addEventListener('keydown', e=>{
  if ((e.ctrlKey||e.metaKey) && e.key==='s') { e.preventDefault(); saveFile(); }
});
</script>
</body>
</html>"""

