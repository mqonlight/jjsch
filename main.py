import os
import re
import sqlite3
import asyncio
import threading
import datetime
import aiohttp
import discord
from discord.ext import commands
from flask import Flask

# --- 1. FLASK KEEP-ALIVE WEBSERVER ---
app = Flask("")

@app.route("/")
def home():
    return "Unified Discord Bot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

# --- 2. DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=["+", "?"], intents=intents)

# Configuration Constants
BANNER_URL = "https://i.imgur.com/0yNuy7m.gif"
VERIFIED_ROLE_ID = 1512157707384393781
TRYOUT_CHANNEL_ID = 1512169195905749132
TRYOUT_COOLDOWN_ROLE_ID = 1546520792022646824
BOOSTER_CHANNEL_ID = 1545402811179860088
BOOSTER_IMAGE_URL = "https://i.imgur.com/0yNuy7m.gif"

# --- PERMISSION ROLE IDS ---
ALLOWED_MUTE_ROLES = [1512168816275361882, 1512168817705357492, 1512168803578937354]
ALLOWED_WARN_ROLES = [1512168816275361882, 1512168817705357492, 1512168803578937354]
ALLOWED_BAN_ROLES = [1512168798315221132, 222222222222222222, 333333333333333333]
ALLOWED_ROLE_COMMAND_ROLES = [2222222222222, 222222222222222222, 333333333333333333]

# Achievement Role Map
ACHIEVEMENT_ROLES = {
    1512168949410697257: {
        "title": "Special Grade 1!",
        "rarity": "1%",
        "description": "Receive the Second Best Grade",
        "image_url": "https://preview.redd.it/couldnt-sukuna-use-his-flames-in-other-ways-v0-wemf6a96yu6e1.jpeg?auto=webp&s=fee938a51d9544a98bd1a2f3663a97ca3f40add2"
    }
}

ROLE_MAP = {
    # Grades & Aliases
    "special grade 1": 1512168949410697257,
    "spg1": 1512168949410697257,
    "grade 1": 1548096759048306788,
    "g1": 1548096759048306788,
    "semi grade 1": 1548096803575169076,
    "semi-grade 1": 1548096803575169076,
    "semi g1": 1548096803575169076,
    "sg1": 1548096803575169076,
    "grade 2": 1548096782611906560,
    "g2": 1548096782611906560,
    "grade 3": 1548096794142183524,
    "g3": 1548096794142183524,
    "grade 4": 1548096800643088394,
    "g4": 1548096800643088394,
    # Tiers
    "high": 1512168957799301131,
    "mid": 1512168959938527357,
    "low": 1512168961398145024,
    # Stability
    "strong": 1512168962509770945,
    "stable": 1512168963575119962,
    "weak": 1512168964422107341,
}
ALL_TRYOUT_ROLE_IDS = set(ROLE_MAP.values())

# Global In-Memory Data Caches
sniped_messages = {}
edited_messages = {}

# --- 3. DATABASE INITIALIZATION ---
conn_db = sqlite3.connect("database.db")
cursor_db = conn_db.cursor()
cursor_db.execute("""
CREATE TABLE IF NOT EXISTS players (
    discord_id INTEGER PRIMARY KEY,
    roblox_username TEXT,
    grade TEXT DEFAULT 'Ungraded',
    region TEXT DEFAULT 'Global',
    country TEXT DEFAULT 'Unknown',
    clan_name TEXT DEFAULT 'None'
)
""")
cursor_db.execute("""
CREATE TABLE IF NOT EXISTS clans (
    clan_name TEXT PRIMARY KEY,
    leader_id INTEGER,
    region TEXT,
    clan_points INTEGER DEFAULT 0,
    logo_url TEXT
)
""")
conn_db.commit()

conn_lb = sqlite3.connect("leaderboard.db")
cursor_lb = conn_lb.cursor()
cursor_lb.execute("""
CREATE TABLE IF NOT EXISTS leaderboard (
    position INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT,
    discord_id INTEGER,
    roblox_username TEXT,
    region TEXT,
    grade TEXT
)
""")
conn_lb.commit()

conn_cd = sqlite3.connect("cooldowns.db")
cursor_cd = conn_cd.cursor()
cursor_cd.execute("""
CREATE TABLE IF NOT EXISTS cooldowns (
    user_id INTEGER PRIMARY KEY,
    guild_id INTEGER,
    expiry_timestamp REAL
)
""")
conn_cd.commit()

# --- 4. HELPER FUNCTIONS ---
def check_has_roles(ctx, role_ids):
    if ctx.author.guild_permissions.administrator:
        return True
    return any(r.id in role_ids for r in ctx.author.roles)

def parse_time(time_str: str) -> int:
    match = re.match(r"^(\d+)([smhd])?$", time_str.lower())
    if not match:
        return None
    val, unit = match.groups()
    val = int(val)
    if unit == "s" or not unit:
        return val
    elif unit == "m":
        return val * 60
    elif unit == "h":
        return val * 3600
    elif unit == "d":
        return val * 86400
    return None

async def get_roblox_data(username: str):
    async with aiohttp.ClientSession() as session:
        user_url = "https://users.roblox.com/v1/usernames/users"
        async with session.post(user_url, json={"usernames": [username]}) as resp:
            if resp.status != 200:
                return None, None
            u_data = await resp.json()
            if not u_data.get("data"):
                return None, None
            user_id = u_data["data"][0]["id"]

        thumb_url = f"https://thumbnails.roblox.com/v1/users/avatar-bust?userIds={user_id}&size=420x420&format=Png"
        async with session.get(thumb_url) as resp:
            avatar_url = None
            if resp.status == 200:
                t_data = await resp.json()
                if t_data.get("data"):
                    avatar_url = t_data["data"][0].get("imageUrl")
            
        return avatar_url, user_id

def reindex_leaderboard():
    cursor_lb.execute("SELECT rowid, * FROM leaderboard ORDER BY position ASC")
    rows = cursor_lb.fetchall()
    for index, row in enumerate(rows, start=1):
        cursor_lb.execute("UPDATE leaderboard SET position = ? WHERE rowid = ?", (index, row[0]))
    conn_lb.commit()

async def schedule_cooldown_removal(guild_id: int, user_id: int, delay_seconds: float):
    await asyncio.sleep(delay_seconds)
    guild = bot.get_guild(guild_id)
    if guild:
        member = guild.get_member(user_id)
        if member:
            cooldown_role = guild.get_role(TRYOUT_COOLDOWN_ROLE_ID)
            if cooldown_role and cooldown_role in member.roles:
                try:
                    await member.remove_roles(cooldown_role)
                except Exception as e:
                    print(f"Failed to remove cooldown role: {e}")
    cursor_cd.execute("DELETE FROM cooldowns WHERE user_id = ?", (user_id,))
    conn_cd.commit()

async def restore_pending_cooldowns():
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    cursor_cd.execute("SELECT user_id, guild_id, expiry_timestamp FROM cooldowns")
    for user_id, guild_id, expiry in cursor_cd.fetchall():
        remaining = expiry - now
        bot.loop.create_task(schedule_cooldown_removal(guild_id, user_id, max(0, remaining)))

# --- 5. BOT EVENTS ---
@bot.event
async def on_ready():
    print(f"Master Utility & Leaderboard Bot ready as {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")
    await restore_pending_cooldowns()

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    added_roles = set(after.roles) - set(before.roles)
    for role in added_roles:
        if role.id in ACHIEVEMENT_ROLES:
            ach = ACHIEVEMENT_ROLES[role.id]
            try:
                dm_embed = discord.Embed(
                    title=f"GG {after.display_name}, you just unlocked the achievement: {ach['title']} ({after.guild.name})",
                    color=discord.Color.blue()
                )
                dm_embed.add_field(name="ACHIEVEMENT UNLOCKED!", value=f"**{ach['title']}**\n`{ach['rarity']}` • {ach['description']}", inline=False)
                if ach.get("image_url"):
                    dm_embed.set_image(url=ach["image_url"])
                await after.send(content=f"GG {after.mention}, you just unlocked the achievement: **{ach['title']}**!", embed=dm_embed)
            except Exception as e:
                print(f"Could not DM user {after}: {e}")

@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.content:
        return
    sniped_messages[message.channel.id] = {
        "content": message.content,
        "author": message.author,
        "time": message.created_at
    }

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or before.content == after.content:
        return
    edited_messages[before.channel.id] = {
        "before": before.content,
        "after": after.content,
        "author": before.author,
        "time": datetime.datetime.now(datetime.timezone.utc)
    }

@bot.event
async def on_message(message: discord.Message):
    if message.type in [
        discord.MessageType.premium_guild_subscription,
        discord.MessageType.premium_guild_tier_1,
        discord.MessageType.premium_guild_tier_2,
        discord.MessageType.premium_guild_tier_3,
    ]:
        booster_channel = message.guild.get_channel(BOOSTER_CHANNEL_ID)
        if booster_channel:
            c = "<:connector:1545187663273926686>"
            embed = discord.Embed(
                title="Thanks for boosting the server! 💎",
                description=(
                    f"{c} **Booster:** {message.author.mention} (`@{message.author.name}`)\n"
                    f"{c} **Picture Perms:** Post images & media in text channels\n"
                    f"{c} **Custom Role:** Claim your own custom role & color\n"
                    f"{c} **Nickname:** Change your own server nickname\n"
                    f"{c} **Private Tryout:** Schedule your Tryout for a time that works for you\n"
                    f"{c} **Giveaways:** 2x multiplier on all server giveaways\n\n"
                    f"*Open a ticket to claim your custom role and perks!*"
                ),
                color=discord.Color.from_rgb(255, 255, 255)
            )
            embed.set_image(url=BANNER_URL)
            await booster_channel.send(embed=embed)

    if message.author.bot:
        return

    if message.channel.id == TRYOUT_CHANNEL_ID:
        content = message.content.strip()
        is_private = False

        if re.match(r"^PRIVATE\s+TRYOUT\b", content, re.IGNORECASE):
            is_private = True
            content = re.sub(r"^PRIVATE\s+TRYOUT\s*", "", content, flags=re.IGNORECASE)

        pattern = r"^<@!?(\d+)>\s+(special grade \d|semi-grade \d|semi grade \d|semi g1|grade \d|spg1|sg1|g1|g2|g3|g4)\s+(high|mid|low)\s+(strong|stable|weak)(?:\s+(.*))?$"
        match = re.match(pattern, content, re.IGNORECASE)

        if match:
            user_id, grade, tier, stability, _ = match.groups()
            member = message.guild.get_member(int(user_id))

            if member:
                roles_to_remove = [r for r in member.roles if r.id in ALL_TRYOUT_ROLE_IDS]
                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove)

                roles_to_add = []
                for item in [grade, tier, stability]:
                    role_id = ROLE_MAP.get(item.lower())
                    if role_id:
                        role = message.guild.get_role(role_id)
                        if role and role not in roles_to_add:
                            roles_to_add.append(role)

                cooldown_role = message.guild.get_role(TRYOUT_COOLDOWN_ROLE_ID)
                if cooldown_role:
                    roles_to_add.append(cooldown_role)

                if roles_to_add:
                    await member.add_roles(*roles_to_add)
                    await message.add_reaction("✅")

                cooldown_days = 2 if is_private else 3
                duration_seconds = cooldown_days * 86400
                expiry_timestamp = datetime.datetime.now(datetime.timezone.utc).timestamp() + duration_seconds

                cursor_cd.execute(
                    "INSERT INTO cooldowns (user_id, guild_id, expiry_timestamp) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET guild_id=excluded.guild_id, expiry_timestamp=excluded.expiry_timestamp",
                    (member.id, message.guild.id, expiry_timestamp)
                )
                conn_cd.commit()
                bot.loop.create_task(schedule_cooldown_removal(message.guild.id, member.id, duration_seconds))

    await bot.process_commands(message)

# --- 6. MODERATION & ROLE PREFIX COMMANDS ---
@bot.command(name="mute")
async def mute(ctx: commands.Context, target: str = None, limit: str = None, *, reason: str = "No reason provided"):
    if not check_has_roles(ctx, ALLOWED_MUTE_ROLES):
        await ctx.send("❌ You don't have permission to use this command.")
        return

    if target is None:
        embed = discord.Embed(color=discord.Color.blue())
        embed.description = (
            "**Command: /mute**\n\n"
            "**Description:** Mute a member so they cannot type.\n"
            "**Cooldown:** 3 seconds\n"
            "**Usage:**\n"
            "/mute [user/id] [limit] [reason]\n"
            "**Example:**\n"
            "/mute @NoobLance 10 Shitposting\n"
            "/mute 123456789012345678 10m spamming\n"
            "/mute NoobLance 1d Too Cool\n"
            "/mute NoobLance 5h He asked for it"
        )
        await ctx.send(embed=embed)
        return

    try:
        member = await commands.MemberConverter().convert(ctx, target)
    except commands.BadArgument:
        await ctx.send("❌ Member not found in this server.")
        return

    parsed_duration = parse_time(limit) if limit else None
    if parsed_duration:
        await member.timeout(datetime.timedelta(seconds=parsed_duration), reason=reason)
        await ctx.send(f"🔇 **{member.display_name}** has been muted for {limit}. Reason: {reason}")
    else:
        full_reason = f"{limit} {reason}".strip() if limit else reason
        await member.timeout(datetime.timedelta(days=28), reason=full_reason)
        await ctx.send(f"🔇 **{member.display_name}** has been muted. Reason: {full_reason}")

@bot.command(name="warn")
async def warn(ctx: commands.Context, target: str = None, *, reason: str = "No reason provided"):
    if not check_has_roles(ctx, ALLOWED_WARN_ROLES):
        await ctx.send("❌ You don't have permission to use this command.")
        return

    if target is None:
        embed = discord.Embed(color=discord.Color.blue())
        embed.description = (
            "**Command: /warn**\n\n"
            "**Description:** Warn a member\n"
            "**Cooldown:** 3 seconds\n"
            "**Usage:**\n"
            "/warn [user/id] (reason)\n"
            "**Example:**\n"
            "/warn @NoobLance Stop posting lewd images\n"
            "/warn 123456789012345678 Stop spamming"
        )
        await ctx.send(embed=embed)
        return

    try:
        member = await commands.MemberConverter().convert(ctx, target)
    except commands.BadArgument:
        await ctx.send("❌ Member not found in this server.")
        return

    try:
        await member.send(f"⚠️ You have been warned in **{ctx.guild.name}**. Reason: {reason}")
    except Exception:
        pass
    await ctx.send(f"⚠️ **{member.display_name}** has been warned. Reason: {reason}")

@bot.command(name="ban")
async def ban(ctx: commands.Context, target: str = None, *, reason: str = "No reason provided"):
    if not check_has_roles(ctx, ALLOWED_BAN_ROLES):
        await ctx.send("❌ You don't have permission to use this command.")
        return

    if target is None:
        embed = discord.Embed(color=discord.Color.blue())
        embed.description = (
            "**Command: /ban**\n\n"
            "**Description:** Ban a member by ID or Mention\n"
            "**Cooldown:** 3 seconds\n"
            "**Usage:**\n"
            "/ban [user/id] [reason]\n"
            "**Example:**\n"
            "/ban @NoobLance making bugs\n"
            "/ban 123456789012345678 spamming toxic links"
        )
        await ctx.send(embed=embed)
        return

    try:
        user = await commands.UserConverter().convert(ctx, target)
    except commands.BadArgument:
        await ctx.send("❌ Invalid user ID or mention provided.")
        return

    await ctx.guild.ban(user, reason=reason)
    await ctx.send(f"🔨 **{user.name}** (`{user.id}`) has been banned. Reason: {reason}")

@bot.command(name="role")
async def role_cmd(ctx: commands.Context, target: str = None, role: discord.Role = None):
    if not check_has_roles(ctx, ALLOWED_ROLE_COMMAND_ROLES):
        await ctx.send("❌ You don't have permission to use this command.")
        return

    if target is None or role is None:
        await ctx.send("Usage: `?role <user_id_or_mention> <role_id_or_mention>`")
        return

    try:
        member = await commands.MemberConverter().convert(ctx, target)
    except commands.BadArgument:
        await ctx.send("❌ Member not found in this server.")
        return

    if role in member.roles:
        await member.remove_roles(role)
        action = "Removed"
    else:
        await member.add_roles(role)
        action = "Added"

    embed = discord.Embed(
        description=f"✅ {action} role {role.mention} from **{member.name}**",
        color=discord.Color.dark_gray()
    )
    await ctx.send(embed=embed)

# --- 7. UTILITY PREFIX COMMANDS ---
@bot.command(name="snipe")
async def snipe(ctx: commands.Context):
    sniped = sniped_messages.get(ctx.channel.id)
    if not sniped:
        await ctx.send("There's nothing to snipe!")
        return
    embed = discord.Embed(description=sniped["content"], color=discord.Color.red(), timestamp=sniped["time"])
    embed.set_author(name=f"Deleted by {sniped['author'].display_name}", icon_url=sniped['author'].display_avatar.url)
    await ctx.send(embed=embed)

@bot.command(name="editsnipe")
async def editsnipe(ctx: commands.Context):
    edited = edited_messages.get(ctx.channel.id)
    if not edited:
        await ctx.send("No recently edited messages found!")
        return
    embed = discord.Embed(color=discord.Color.orange(), timestamp=edited["time"])
    embed.set_author(name=f"Edited by {edited['author'].display_name}", icon_url=edited['author'].display_avatar.url)
    embed.add_field(name="Before", value=edited["before"], inline=False)
    embed.add_field(name="After", value=edited["after"], inline=False)
    await ctx.send(embed=embed)

@bot.command(name="pfp")
async def pfp(ctx: commands.Context, target: str = None):
    if target:
        try:
            member = await commands.MemberConverter().convert(ctx, target)
        except commands.BadArgument:
            await ctx.send("❌ Member not found.")
            return
    else:
        member = ctx.author

    embed = discord.Embed(title=f"{member.display_name}'s Avatar", color=discord.Color.blue())
    embed.set_image(url=member.display_avatar.url)
    await ctx.send(embed=embed)

@bot.command(name="banner")
async def banner(ctx: commands.Context, target: str = None):
    if target:
        try:
            member = await commands.MemberConverter().convert(ctx, target)
        except commands.BadArgument:
            await ctx.send("❌ Member not found.")
            return
    else:
        member = ctx.author

    user = await bot.fetch_user(member.id)
    if not user.banner:
        await ctx.send(f"{member.display_name} doesn't have a banner!")
        return
    embed = discord.Embed(title=f"{member.display_name}'s Banner", color=discord.Color.blue())
    embed.set_image(url=user.banner.url)
    await ctx.send(embed=embed)

@bot.command(name="lock")
@commands.has_permissions(administrator=True)
async def lock(ctx: commands.Context):
    role = ctx.guild.get_role(VERIFIED_ROLE_ID)
    if not role:
        await ctx.send("❌ Verified role not found!")
        return
    await ctx.channel.set_permissions(role, send_messages=False)
    await ctx.send("🔒 Channel locked for Verified members.")

@bot.command(name="unlock")
@commands.has_permissions(administrator=True)
async def unlock(ctx: commands.Context):
    role = ctx.guild.get_role(VERIFIED_ROLE_ID)
    if not role:
        await ctx.send("❌ Verified role not found!")
        return
    await ctx.channel.set_permissions(role, send_messages=True)
    await ctx.send("🔓 Channel unlocked for Verified members.")

@bot.command(name="embed")
@commands.has_permissions(administrator=True)
async def embed(ctx: commands.Context, title: str, *, message: str):
    embed_msg = discord.Embed(title=title, description=message, color=discord.Color.blue())
    await ctx.message.delete()
    await ctx.send(embed_msg)

# --- 8. INDEX & LEADERBOARD SLASH COMMANDS ---
@bot.tree.command(name="register", description="Register your Roblox profile")
async def register(interaction: discord.Interaction, roblox_username: str, region: str, country: str, clan: str = "None"):
    await interaction.response.defer()
    avatar_url, roblox_id = await get_roblox_data(roblox_username)
    if not roblox_id:
        await interaction.followup.send("❌ Invalid Roblox username.", ephemeral=True)
        return

    cursor_db.execute(
        "INSERT INTO players (discord_id, roblox_username, region, country, clan_name) VALUES (?, ?, ?, ?, ?) ON CONFLICT(discord_id) DO UPDATE SET roblox_username=excluded.roblox_username, region=excluded.region, country=excluded.country, clan_name=excluded.clan_name",
        (interaction.user.id, roblox_username, region, country, clan)
    )
    conn_db.commit()

    embed = discord.Embed(title="✅ Registration Successful", color=discord.Color.green())
    embed.add_field(name="Profile Linked", value=f"**{roblox_username}**", inline=False)
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="view_player", description="View a player's profile card")
async def view_player(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer()
    cursor_db.execute("SELECT roblox_username, grade, region, country, clan_name FROM players WHERE discord_id = ?", (user.id,))
    row = cursor_db.fetchone()
    if not row:
        await interaction.followup.send("❌ User not registered.", ephemeral=True)
        return

    r_user, grade, region, country, clan_name = row
    avatar_url, _ = await get_roblox_data(r_user)
    embed = discord.Embed(title=f"**{r_user}**", color=discord.Color.dark_theme())
    embed.add_field(name="Discord", value=user.mention, inline=False)
    embed.add_field(name="Grade", value=grade, inline=False)
    embed.add_field(name="Region", value=f"🌐 {region}", inline=False)
    embed.add_field(name="Country", value=country, inline=False)
    embed.add_field(name="Clan", value=clan_name, inline=False)
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="leaderboard", description="Display the current leaderboard (Top 20)")
async def show_leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    # Query ranks 1 through 20 specifically
    cursor_lb.execute("SELECT position, display_name, discord_id, roblox_username, region, grade FROM leaderboard WHERE position BETWEEN 1 AND 20 ORDER BY position ASC")
    players = cursor_lb.fetchall()
    if not players:
        await interaction.followup.send("Leaderboard is currently empty.")
        return

    for pos, name, d_id, r_user, region, grade in players:
        avatar_url, roblox_id = await get_roblox_data(r_user)
        profile_url = f"https://www.roblox.com/users/{roblox_id}/profile" if roblox_id else None

        embed = discord.Embed(
            title=f"{name} | {pos}.",
            url=profile_url,
            description=f"« « | <@{d_id}> (`@{r_user}`) | » »\n**Region:** — {region}\n**Grade:** — {grade}",
            color=discord.Color.dark_theme()
        )
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        if BANNER_URL:
            embed.set_image(url=BANNER_URL)
        await interaction.channel.send(embed=embed)

    await interaction.followup.send("Leaderboard (1-20) posted successfully!", ephemeral=True)

@bot.tree.command(name="add_player", description="Admin: Add a player to the leaderboard")
@commands.has_permissions(administrator=True)
async def add_player(interaction: discord.Interaction, display_name: str, user: discord.Member, roblox_username: str, region: str, grade: str):
    await interaction.response.defer(ephemeral=True)
    cursor_lb.execute("INSERT INTO leaderboard (display_name, discord_id, roblox_username, region, grade) VALUES (?, ?, ?, ?, ?)", (display_name, user.id, roblox_username, region, grade))
    conn_lb.commit()
    reindex_leaderboard()
    await interaction.followup.send(f"Added **{display_name}** (`{roblox_username}`) to the leaderboard.")

@bot.tree.command(name="remove_player", description="Admin: Remove a player from the leaderboard")
@commands.has_permissions(administrator=True)
async def remove_player(interaction: discord.Interaction, position: int):
    cursor_lb.execute("DELETE FROM leaderboard WHERE position = ?", (position,))
    conn_lb.commit()
    reindex_leaderboard()
    await interaction.response.send_message(f"Removed player at rank #{position}.", ephemeral=True)

# --- 9. START BOT ---
keep_alive()
token = os.environ.get("DISCORD_TOKEN") or os.environ.get("UTILITY_TOKEN")
bot.run(token)
