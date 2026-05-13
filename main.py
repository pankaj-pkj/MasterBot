import os
import subprocess
import asyncio
import time
import requests
import shutil
from zipfile import ZipFile
from aiohttp import web
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# --- Configuration Variables ---
PORT = int(os.environ.get("PORT", 8080))
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
GITHUB_PAT = os.environ.get("GITHUB_PAT", "")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "")
REPO_NAME = os.environ.get("REPO_NAME", "HostedBotsData")
RENDER_URL = os.environ.get("RENDER_URL", "")

REPO_URL = f"https://{GITHUB_PAT}@github.com/{GITHUB_USERNAME}/{REPO_NAME}.git"
HOSTED_DIR = "hosted_bots"
RUNNING_BOTS = {}

# GitHub config
os.system('git config --global user.email "masterbot@example.com"')
os.system('git config --global user.name "MasterBot Admin"')

# --- 1. DUMMY WEB SERVER & KEEP ALIVE ---
async def handle_ping(request):
    return web.Response(text="Master Bot is Awake and Running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"✅ Web server started on port {PORT}")

async def keep_alive_ping():
    while True:
        try:
            if RENDER_URL and "http" in RENDER_URL:
                requests.get(RENDER_URL)
                print("🔄 Ping successful. Render is awake.")
        except Exception as e:
            pass
        await asyncio.sleep(840) # 14 minutes

# --- 2. GITHUB SYNC ---
def sync_from_github():
    if not GITHUB_PAT: return
    if os.path.exists(HOSTED_DIR):
        shutil.rmtree(HOSTED_DIR)
    print("📥 Syncing bots from GitHub...")
    os.system(f"git clone {REPO_URL} {HOSTED_DIR}")

def push_to_github(commit_msg="Added new bot"):
    if not GITHUB_PAT: return
    print("📤 Pushing new data to GitHub...")
    os.system(f"cd {HOSTED_DIR} && git add . && git commit -m '{commit_msg}' && git push")

# --- 3. BOT RUNNER & MONITOR ---
async def run_and_monitor_bot(bot_name, bot_folder, main_file):
    RUNNING_BOTS[bot_name] = {"active": True, "start_time": time.time(), "status": "Running 🟢"}
    while RUNNING_BOTS.get(bot_name, {}).get("active", False):
        print(f"🚀 Starting {bot_name}...")
        proc = subprocess.Popen(["python", "main.py"], cwd=bot_folder)
        RUNNING_BOTS[bot_name]["process"] = proc
        
        while proc.poll() is None:
            await asyncio.sleep(2)
            
        if not RUNNING_BOTS.get(bot_name, {}).get("active", False):
            RUNNING_BOTS[bot_name]["status"] = "Stopped 🛑"
            break
            
        RUNNING_BOTS[bot_name]["status"] = "Restarting ⏳"
        await asyncio.sleep(5)

# --- 4. TELEGRAM HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *All-Rounder Master Bot Is Live!*\n\n"
        "Send me a `.zip` file of your bot to host it permanently.\n\n"
        "👉 /all - Check hosted bots\n"
        "👉 /stop <bot_name> - Stop a bot"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def receive_bot_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    file_name = document.file_name
    
    if not file_name.endswith('.zip'):
        await update.message.reply_text("❌ Please send a .zip file!")
        return

    msg = await update.message.reply_text("⏳ Download & Extracting...")
    bot_name = file_name.replace('.zip', '')
    bot_folder = os.path.join(HOSTED_DIR, bot_name)
    
    os.makedirs(HOSTED_DIR, exist_ok=True)
    file = await context.bot.get_file(document.file_id)
    zip_path = os.path.join(HOSTED_DIR, file_name)
    await file.download_to_drive(zip_path)

    with ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(bot_folder)
    os.remove(zip_path)

    main_file = os.path.join(bot_folder, "main.py")
    if not os.path.exists(main_file):
        await msg.edit_text("❌ Error: `main.py` not found in ZIP!")
        return

    req_file = os.path.join(bot_folder, "requirements.txt")
    if os.path.exists(req_file):
        await msg.edit_text("⚙️ Installing Dependencies...")
        subprocess.run(["pip", "install", "-r", req_file])

    push_to_github(f"Added new bot: {bot_name}")
    asyncio.create_task(run_and_monitor_bot(bot_name, bot_folder, main_file))
    await msg.edit_text(f"✅ *{bot_name}* Hosted successfully!", parse_mode="Markdown")

async def list_bots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not RUNNING_BOTS:
        await update.message.reply_text("📂 Koi bot run nahi ho raha.")
        return

    text = "📊 *Server Status:*\n\n"
    for name, data in RUNNING_BOTS.items():
        if data.get("active", False):
            uptime = int(time.time() - data.get("start_time", time.time()))
            m, s = divmod(uptime, 60)
            h, m = divmod(m, 60)
            text += f"🔹 *{name}*\nStatus: {data['status']}\nUptime: `{h}h {m}m {s}s`\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        bot_name = context.args[0]
        if bot_name in RUNNING_BOTS:
            RUNNING_BOTS[bot_name]["active"] = False
            RUNNING_BOTS[bot_name]["process"].terminate()
            await update.message.reply_text(f"🛑 {bot_name} stopped.")
        else:
            await update.message.reply_text("❌ Bot not found.")
    except IndexError:
        await update.message.reply_text("Format: `/stop <bot_name>`")

# --- MASTER LIFECYCLE (ERROR-FREE) ---
async def main():
    sync_from_github()
    
    # Auto-start existing bots
    if os.path.exists(HOSTED_DIR):
        for folder in os.listdir(HOSTED_DIR):
            bot_dir = os.path.join(HOSTED_DIR, folder)
            if os.path.isdir(bot_dir) and folder != ".git":
                req_file = os.path.join(bot_dir, "requirements.txt")
                if os.path.exists(req_file):
                    subprocess.run(["pip", "install", "-r", req_file])
                main_file = os.path.join(bot_dir, "main.py")
                if os.path.exists(main_file):
                    asyncio.create_task(run_and_monitor_bot(folder, bot_dir, main_file))

    # Initialize Master Bot
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("all", list_bots))
    app.add_handler(CommandHandler("stop", stop_bot))
    app.add_handler(MessageHandler(filters.Document.ZIP, receive_bot_zip))

    # Start Web Server First
    await start_web_server()
    asyncio.create_task(keep_alive_ping())

    # Start Bot Safely
    print("⏳ Initializing Bot...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    print("✅ Bot Started Successfully!")

    # Block forever
    await asyncio.Event().wait()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped by User.")
