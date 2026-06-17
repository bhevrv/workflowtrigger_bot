import os
import discord
import sqlite3
import aiohttp  # Replaced requests with aiohttp
from discord import app_commands
from discord.ext import commands
from datetime import datetime

# === SQLite setup === #
db = sqlite3.connect("bot_data.db")
cursor = db.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS server_settings (
    server_id INTEGER PRIMARY KEY,
    owner TEXT DEFAULT '',
    repo TEXT DEFAULT 'mcserverstarter',
    workflow_file TEXT DEFAULT 'selenium.yml',
    github_token TEXT DEFAULT '',
    notify_channel INTEGER DEFAULT 0,
    max_uses_per_day INTEGER DEFAULT 3,
    cooldown_time INTEGER DEFAULT 600
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS user_usage (
    server_id INTEGER,
    user_id INTEGER,
    uses INTEGER DEFAULT 0,
    last_used REAL DEFAULT 0,
    PRIMARY KEY (server_id, user_id)
)''')
db.commit()

# === Bot Setup === #
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# === Helper Functions === #
def get_server_settings(server_id):
    cursor.execute("SELECT * FROM server_settings WHERE server_id = ?", (server_id,))
    return cursor.fetchone()

def update_server_settings(server_id, **kwargs):
    # Ensure a row exists first using UPSERT style logic
    cursor.execute("INSERT OR IGNORE INTO server_settings (server_id) VALUES (?)", (server_id,))
    
    for key, value in kwargs.items():
        if value is not None:  
            cursor.execute(f"UPDATE server_settings SET {key} = ? WHERE server_id = ?", (value, server_id))
    db.commit()

# === Slash Commands === #
@bot.tree.command(name="wssetup", description="Setup Github workflow settings")
@app_commands.describe(owner="GitHub username", repo="GitHub repository", workflow_file="GitHub workflow file", github_token="GitHub personal access token")
@app_commands.default_permissions(administrator=True) # Admin Only
async def wssetup(interaction: discord.Interaction, owner: str = None, repo: str = None, workflow_file: str = None, github_token: str = None):
    sid = interaction.guild_id
    if sid is None:
        return await interaction.response.send_message("🚫 Use this in a server.", ephemeral=True)
    
    update_server_settings(sid, owner=owner, repo=repo, workflow_file=workflow_file, github_token=github_token)
    await interaction.response.send_message("✅ GitHub workflow settings updated.", ephemeral=True)

@bot.tree.command(name="botsetup", description="Setup bot limits and channels")
@app_commands.describe(notify_channel="Notification channel", max_uses_per_day="Daily usage limit", cooldown_time="Cooldown in sec")
@app_commands.default_permissions(administrator=True) # Admin Only
async def botsetup(interaction: discord.Interaction, notify_channel: discord.TextChannel = None, max_uses_per_day: int = None, cooldown_time: int = None):
    sid = interaction.guild_id
    if sid is None:
        return await interaction.response.send_message("🚫 Use this in a server.", ephemeral=True)
    
    update_server_settings(sid, 
                           notify_channel=notify_channel.id if notify_channel else None, 
                           max_uses_per_day=max_uses_per_day, 
                           cooldown_time=cooldown_time)
    await interaction.response.send_message("✅ Bot settings updated.", ephemeral=True)

@bot.tree.command(name="run_mc", description="Trigger a GitHub Actions workflow to start the MC server")
async def run_mc(interaction: discord.Interaction):
    sid = interaction.guild_id
    uid = interaction.user.id
    if sid is None:
        return await interaction.response.send_message("🚫 Use this in a server.", ephemeral=True)

    settings = get_server_settings(sid)
    if not settings or not settings[1] or not settings[4]: # Check if owner and token exist
        return await interaction.response.send_message("⚠️ Server settings or GitHub token not fully configured.", ephemeral=True)

    _, owner, repo, workflow_file, token, notify_channel, max_uses, cooldown = settings

    cursor.execute("SELECT uses, last_used FROM user_usage WHERE server_id = ? AND user_id = ?", (sid, uid))
    row = cursor.fetchone()
    now = datetime.utcnow().timestamp()

    if row:
        uses, last_used = row
        
        # --- FIXED: Reset daily limits if 24 hours have passed since last use ---
        if now - last_used > 86400:
            uses = 0
        
        # Check Cooldown first
        if now - last_used < cooldown:
            remaining = int(cooldown - (now - last_used))
            return await interaction.response.send_message(f"⏳ Cooldown! Wait {remaining} sec.", ephemeral=True)
            
        # Check Max Uses
        if uses >= max_uses:
            return await interaction.response.send_message("🚫 Daily usage limit reached. Resets 24h after your last use.", ephemeral=True)
    else:
        uses, last_used = 0, 0

    # Trigger workflow asynchronously using aiohttp
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_file}/dispatches"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
    data = {"ref": "main"}

    await interaction.response.defer(ephemeral=True) # Give the bot time to make the API call

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=data, headers=headers) as response:
            if response.status == 204:
                # Update usage stats
                cursor.execute("""
                    INSERT INTO user_usage (server_id, user_id, uses, last_used) 
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(server_id, user_id) 
                    DO UPDATE SET uses = ?, last_used = ?
                """, (sid, uid, uses + 1, now, uses + 1, now))
                db.commit()

                await interaction.followup.send("✅ Workflow triggered successfully!", ephemeral=True)
                
                channel = bot.get_channel(notify_channel)
                if channel:
                    await channel.send(f"🚀 {interaction.user.mention} ran the MC workflow.")
            else:
                error_text = await response.text()
                await interaction.followup.send(f"❌ GitHub API Error: {response.status} - {error_text}", ephemeral=True)

@bot.tree.command(name="users_usage")
async def users_usage(interaction: discord.Interaction):
    sid = interaction.guild_id
    cursor.execute("SELECT user_id, uses FROM user_usage WHERE server_id = ?", (sid,))
    rows = cursor.fetchall()
    if not rows:
        return await interaction.response.send_message("No usage data.", ephemeral=True)
    result = "\n".join([f"<@{uid}>: {uses}" for uid, uses in rows])
    await interaction.response.send_message(f"📊 Usage Stats:\n{result}", ephemeral=True)

@bot.tree.command(name="reset_usage")
@app_commands.default_permissions(administrator=True)
async def reset_usage(interaction: discord.Interaction):
    sid = interaction.guild_id
    cursor.execute("DELETE FROM user_usage WHERE server_id = ?", (sid,))
    db.commit()
    await interaction.response.send_message("🔄 Usage stats reset.", ephemeral=True)

@bot.tree.command(name="show_settings")
@app_commands.default_permissions(administrator=True)
async def show_settings(interaction: discord.Interaction):
    sid = interaction.guild_id
    settings = get_server_settings(sid)
    if not settings:
        return await interaction.response.send_message("⚠️ Server not set up yet.", ephemeral=True)

    _, owner, repo, workflow_file, token, notify_channel, max_uses, cooldown = settings
    notify_channel_display = f"<#{notify_channel}>" if notify_channel else "`None`"
    
    # Secure token snippet preview
    token_preview = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else "Not Set"
    
    await interaction.response.send_message(
        f"**Current Server Settings**\n"
        f"Owner: `{owner}`\nRepo: `{repo}`\nWorkflow: `{workflow_file}`\n"
        f"Token: `{token_preview}`\n"
        f"Notify Channel: {notify_channel_display}\n"
        f"Max Uses/Day: `{max_uses}`\n⏱ Cooldown: `{cooldown} sec`",
        ephemeral=True
    )

# === Events === #
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user}")

bot.run(DISCORD_TOKEN)
