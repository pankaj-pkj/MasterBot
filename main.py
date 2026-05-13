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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
GITHUB_PAT = os.environ.get("GITHUB_PAT", "YOUR_GITHUB_PERSONAL_ACCESS_TOKEN")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "YourGithubUsername")
REPO_NAME = os.environ.get("REPO_NAME", "HostedBotsData")
RENDER_URL = os.environ.get("RENDER_URL", "https://your-app-name.onrender.com") # Apna render web URL daalein

REPO_URL = f"https://{GITHUB_PAT}@github.com/{GITHUB_USERNAME}/{REPO_NAME}.git"
HOSTED_DIR = "hosted_bots"
RUNNING_BOTS = {}

# Git config set karna zaroori hai commit ke liye
os.system('git config --global user.email "masterbot@example.com"')
os.system('git config --global user.name "MasterBot Admin"')

# --- 1. DUMMY WEB SERVER & KEEP ALIVE (Anti-Sleep) ---
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
    """Har 14 minute me khud ko ping karega taaki Render sleep na ho."""
    while True:
        try:
            requests.get(RENDER_URL)
            print("🔄 Self-ping successful. Render is awake.")
        except Exception as e:
            print(f"Ping failed: {e}")
        await asyncio.sleep(14 * 60) # 14 minutes

# --- 2. GITHUB SYNC & AUTO-STARTUP ---
def sync_from_github():
    """Restart hone par GitHub se purane bots wapas layega."""
    if os.path.exists(HOSTED_DIR):
        shutil.rmtree(HOSTED_DIR)
    print("📥 Syncing bots from GitHub...")
    os.system(f"git clone {REPO_URL} {HOSTED_DIR}")

def push_to_github(commit_msg="Added new bot"):
    """Naya bot aane par use GitHub par permanently save karega."""
    print("📤 Pushing new data to GitHub...")
    os.system(f"cd {HOSTED_DIR} && git add . && git commit -m '{commit_msg}' && git push")

# --- 3. AUTO-RESTART MONITOR ---
async def run_and_monitor_bot(bot_name, bot_folder, main_file):
    """Ye function bot ko run karega aur crash hone par auto-restart karega."""
    RUNNING_BOTS[bot_name] = {"active": True, "start_time": time.time(), "status": "Running 🟢"}
    
    while RUNNING_BOTS.get(bot_name, {}).get("active", False):
        print(f"🚀 Starting {bot_name}...")
        proc = subprocess.Popen(["python", "main.py"], cwd=bot_folder)
        RUNNING_BOTS[bot_name]["process"] = proc
        
        # Wait for process to exit
        while proc.poll() is None:
            await asyncio.sleep(2)
            
        # Agar user ne manual stop kiya hai
        if not RUNNING_BOTS.get(bot_name, {}).get("active", False):
            RUNNING_BOTS[bot_name]["status"] = "Stopped 🛑"
            break
            
        # Agar error/crash ki wajah se band hua hai
        RUNNING_BOTS[bot_name]["status"] = "Restarting ⏳"
        print(f"⚠️ {bot_name} crashed! Restarting in 5 seconds...")
        await asyncio.sleep(5)

# --- 4. TELEGRAM HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *All-Rounder Master Bot Is Live!*\n\n"
        "Send me a `.zip` file of your bot. I will host it permanently, auto-restart it if it crashes, and sync it to GitHub!\n\n"
        "Commands:\n"
        "👉 /all - Hosted bots ki list aur speed\n"
        "👉 /stop <bot_folder> - Bot ko band karein"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def receive_bot_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    file_name = document.file_name
    
    if not file_name.endswith('.zip'):
        await update.message.reply_text("❌ Sirf .zip file bhejiye jisme aapka `main.py` aur `requirements.txt` ho.")
        return

    msg = await update.message.reply_text("⏳ Download & Extracting...")
    
    bot_name = file_name.replace('.zip', '')
    bot_folder = os.path.join(HOSTED_DIR, bot_name)
    
    # Download zip
    file = await context.bot.get_file(document.file_id)
    zip_path = f"{bot_folder}.zip"
    await file.download_to_drive(zip_path)

    # Extract
    with ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(bot_folder)
    os.remove(zip_path) # Delete zip after extraction

    main_file = os.path.join(bot_folder, "main.py")
    if not os.path.exists(main_file):
        await msg.edit_text("❌ Error: ZIP ke andar `main.py` file hona zaroori hai!")
        return

    # Install requirements
    req_file = os.path.join(bot_folder, "requirements.txt")
    if os.path.exists(req_file):
        await msg.edit_text("⚙️ Installing Dependencies...")
        subprocess.run(["pip", "install", "-r", req_file])
    else:
        # Auto-create basic requirements if missing
        with open(req_file, "w") as f:
            f.write("python-telegram-bot")

    await msg.edit_text("☁️ Backing up to GitHub...")
    push_to_github(f"Added new bot: {bot_name}")

    await msg.edit_text(f"🚀 Starting {bot_name} in background with Auto-Restart...")
    asyncio.create_task(run_and_monitor_bot(bot_name, bot_folder, main_file))
    
    await msg.edit_text(f"✅ *{bot_name}* Successfully Hosted permanently!", parse_mode="Markdown")

async def list_bots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not RUNNING_BOTS:
        await update.message.reply_text("📂 Koi bot run nahi ho raha.")
        return

    text = "📊 *Server Status & Speed:*\n\n"
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
            await update.message.reply_text(f"🛑 {bot_name} band kar diya gaya hai. Auto-restart ruk gaya.")
        else:
            await update.message.reply_text("❌ Ye bot exist nahi karta.")
    except IndexError:
        await update.message.reply_text("Format: `/stop <bot_name>`")

# --- MAIN STARTUP ---
async def main():
    # Render Restart hote hi Github se sab wapas layega
    sync_from_github()
    
    # Purane bots ko auto-start karna
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

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("all", list_bots))
    app.add_handler(CommandHandler("stop", stop_bot))
    app.add_handler(MessageHandler(filters.Document.ZIP, receive_bot_zip))

    # Sab kuch ek sath start karna
    await asyncio.gather(
        start_web_server(),
        keep_alive_ping(),
        app.initialize(),
        app.start(),
        app.updater.start_polling()
    )
    await asyncio.Event().wait()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
