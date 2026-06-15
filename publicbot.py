

###########    DEPRECATED    ###########


import discord
import sqlite3
from discord import app_commands
from discord.ext import commands, tasks
from collections import defaultdict
from datetime import datetime, timedelta

# === SQLite setup === #
db = sqlite3.connect("bot_data.db")
cursor = db.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS server_settings (
    server_id INTEGER PRIMARY KEY,
    owner TEXT,
    repo TEXT,
    workflow_file TEXT,
    github_token TEXT,
    notify_channel INTEGER,
    max_uses_per_day INTEGER,
    cooldown_time INTEGER
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS user_usage (
    server_id INTEGER,
    user_id INTEGER,
    uses INTEGER,
    last_used REAL,
    PRIMARY KEY (server_id, user_id)
)''')

db.commit()

# === Bot Setup === #
DISCORD_TOKEN = "MTUxNTk2OTc5MzgwNTY1MjA4OQ.Grl57d.UKm4uvPYNgP7qxk5afUzaLMdogvLQG7270zuCg"
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# === Helper Functions === #
def get_server_settings(server_id):
    cursor.execute("SELECT * FROM server_settings WHERE server_id = ?", (server_id,))
    return cursor.fetchone()

def update_server_settings(server_id, **kwargs):
    current = get_server_settings(server_id)
    if current:
        for key, value in kwargs.items():
            if value is not None:  # Only update non-None values
                cursor.execute(f"UPDATE server_settings SET {key} = ? WHERE server_id = ?", (value, server_id))
    else:
        # Set defaults
        cursor.execute("""
            INSERT INTO server_settings (
                server_id, owner, repo, workflow_file, github_token, notify_channel, max_uses_per_day, cooldown_time
            ) VALUES (?, '', 'mcserverstarter', 'selenium.yml', '', 0, 3, 600)
        """, (server_id,))
        # Only update passed values, if any
        update_server_settings(server_id, **kwargs)
    db.commit()



# === Slash Commands === #
@bot.tree.command(name="wssetup", description="Setup Github workflow settings")
@app_commands.describe(owner="GitHub username", repo="GitHub repository(default=mcserverstarter)", workflow_file="GitHub workflow file(default=selenium.yml)", github_token="GitHub personal access token")
async def wssetup(interaction: discord.Interaction, owner: str = None, repo: str = None, workflow_file: str = None, github_token: str = None):
    sid = interaction.guild_id
    if sid is None:
        return await interaction.response.send_message("🚫 Use this in a server.", ephemeral=True)
    update_server_settings(sid,
        owner=owner,
        repo=repo,
        workflow_file=workflow_file,
        github_token=github_token
    )
    await interaction.response.send_message("✅ GitHub workflow settings updated.", ephemeral=True)

@bot.tree.command(name="botsetup",  description="Setup bot settings like notify channel, max uses, cooldown time")
@app_commands.describe(notify_channel="Notification channel", max_uses_per_day="Daily usage limit (default = 3)", cooldown_time="Cooldown in sec (default = 600)")
async def botsetup(interaction: discord.Interaction, notify_channel: discord.TextChannel = None, max_uses_per_day: int = None, cooldown_time: int = None):
    sid = interaction.guild_id
    if sid is None:
        return await interaction.response.send_message("🚫 Use this in a server.", ephemeral=True)
    update_server_settings(sid,
        notify_channel=notify_channel.id if notify_channel else None,
        max_uses_per_day=max_uses_per_day,
        cooldown_time=cooldown_time
    )
    await interaction.response.send_message("✅ Bot settings updated.", ephemeral=True)

@bot.tree.command(name="run_mc",  description="Trigger a GitHub Actions workflow to start the MC server")
async def run_mc(interaction: discord.Interaction):
    sid = interaction.guild_id
    uid = interaction.user.id
    if sid is None:
        return await interaction.response.send_message("🚫 Use this in a server.", ephemeral=True)

    settings = get_server_settings(sid)
    if not settings:
        return await interaction.response.send_message("⚠️ Server settings not configured.", ephemeral=True)

    _, owner, repo, workflow_file, token, notify_channel, max_uses, cooldown = settings

    cursor.execute("SELECT uses, last_used FROM user_usage WHERE server_id = ? AND user_id = ?", (sid, uid))
    row = cursor.fetchone()
    now = datetime.utcnow().timestamp()

    if row:
        uses, last_used = row
        if uses >= max_uses:
            return await interaction.response.send_message("🚫 Daily usage limit reached.", ephemeral=True)
        if now - last_used < cooldown:
            remaining = int(cooldown - (now - last_used))
            return await interaction.response.send_message(f"⏳ Cooldown! Wait {remaining} sec.", ephemeral=True)
    else:
        uses, last_used = 0, 0

    # Trigger workflow
    import requests
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_file}/dispatches"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
    data = {"ref": "main"}

    res = requests.post(url, json=data, headers=headers)
    if res.status_code == 204:
        if row:
            cursor.execute("UPDATE user_usage SET uses = ?, last_used = ? WHERE server_id = ? AND user_id = ?", (uses+1, now, sid, uid))
        else:
            cursor.execute("INSERT INTO user_usage (server_id, user_id, uses, last_used) VALUES (?, ?, ?, ?)", (sid, uid, 1, now))
        db.commit()

        await interaction.response.send_message("✅ Workflow triggered!", ephemeral=True)
        channel = bot.get_channel(notify_channel)
        if channel:
            await channel.send(f"🚀 {interaction.user.mention} ran the MC workflow.")
    else:
        await interaction.response.send_message(f"❌ Failed: {res.text}", ephemeral=True)

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
async def reset_usage(interaction: discord.Interaction):
    sid = interaction.guild_id
    cursor.execute("DELETE FROM user_usage WHERE server_id = ?", (sid,))
    db.commit()
    if cursor.rowcount == 0:
        print(f"No usage data ")
    else:
        await interaction.response.send_message("🔄 Usage stats reset.", ephemeral=True)

@bot.tree.command(name="show_settings")
async def show_settings(interaction: discord.Interaction):
    sid = interaction.guild_id
    settings = get_server_settings(sid)
    if not settings:
        return await interaction.response.send_message("⚠️ Server not set up yet.", ephemeral=True)

    _, owner, repo, workflow_file, token, notify_channel, max_uses, cooldown = settings
    notify_channel_display = f"<#{notify_channel}>" if notify_channel else "`None`"
    await interaction.response.send_message(
        f" **Current Server Settings**\n"
        f" Owner: `{owner}`\n Repo: `{repo}`\n Workflow: `{workflow_file}`\n"
        f" Token: `{token[:4]}...{token[-4:]}`\n"
        f" Notify Channel: {notify_channel_display}\n"
        f" Max Uses/Day: `{max_uses}`\n⏱ Cooldown: `{cooldown} sec`",
        ephemeral=True
    )
# === Events === #
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user}")

bot.run(DISCORD_TOKEN)
