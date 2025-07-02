import discord
from discord.ext import commands, tasks
import json
import random
import os
import psutil

# --- Configuration Loading ---
with open('./botconfig/config.json', 'r') as f:
    config = json.load(f)

TOKEN = config['token']
PREFIX = config['prefix']
OWNER_ID = int(config['761635564835045387'])  # Replace with your actual owner ID

with open('./botconfig/radiostation.json', 'r') as f:
    radio_stations = json.load(f)

# --- Database Handling ---
def get_db():
    try:
        with open('./db/role.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_db(db):
    with open('./db/role.json', 'w') as f:
        json.dump(db, f, indent=4)

# --- Bot Setup ---
intents = discord.Intents.default()
intents.guilds = True
intents.guild_messages = True
intents.guild_members = True
intents.guild_voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# --- Bot Events ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    update_activity.start()

@tasks.loop(seconds=4)
async def update_activity():
    activities = [
        "WWELCOME TO VAYU 247 RADIO STATION 📻",
        "🎵 LISTENING TO MUSIC",
        "🎵 LISTENING 24/7 VAYU RADIO",
        "🔊 BROADCASTING NON-STOP TUNES",
        "🎶 ALWAYS LIVE WITH MUSIC",
        "📻 KEEPING THE BEATS ALIVE",
        "🎧 JOIN US FOR CONTINUOUS MUSIC",
        "🎙️ LIVE 24/7 WITH VAYU RADIO",
    ]
    random_activity = random.choice(activities)
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=random_activity))

# --- Bot Commands ---
@bot.command()
async def help(ctx):
    help_embed = discord.Embed(
        title="📻 Help menu",
        color=discord.Color.from_rgb(255, 255, 255)
    )
    help_embed.add_field(name=f"{PREFIX}radio", value="Play radio", inline=True)
    help_embed.add_field(name=f"{PREFIX}radiolist", value="List of popular radio stations", inline=True)
    help_embed.add_field(name=f"{PREFIX}stats", value="Stats of the bot", inline=True)
    help_embed.add_field(name=f"{PREFIX}setrole", value="Set a role to control the bot", inline=True)
    help_embed.add_field(name=f"{PREFIX}reset", value="Restart the bot", inline=True)
    help_embed.add_field(name=f"{PREFIX}dc", value="Disconnect the bot", inline=True)
    help_embed.set_thumbnail(url="https://cdn.discordapp.com/avatars/1127207370502705293/aa56a6dab22500c98c1ab8668b6044aa.png?size=2048")
    help_embed.set_footer(text=f"Requested By {ctx.author.name}", icon_url=ctx.author.avatar.url)
    help_embed.timestamp = discord.utils.utcnow()
    await ctx.reply(embed=help_embed)

@bot.command()
async def radiolist(ctx):
    try:
        with open('./botconfig/radioid.json', 'r', encoding='utf-8') as f:
            contents = f.read()
        radioid_embed = discord.Embed(
            title="Radio ID List",
            description=f"```json\n{contents}\n```",
            color=discord.Color.from_rgb(255, 255, 255)
        )
        radioid_embed.set_footer(text=f"Requested By {ctx.author.name}", icon_url=ctx.author.avatar.url)
        radioid_embed.timestamp = discord.utils.utcnow()
        await ctx.reply(embed=radioid_embed)
    except FileNotFoundError:
        await ctx.reply(":x: **Could not find the radio ID list file.**")

@bot.command()
async def setrole(ctx, role: discord.Role):
    if ctx.author.id != OWNER_ID:
        return await ctx.reply(":x: **You don't have permission to use this command!**")
    
    db = get_db()
    db['role'] = role.id
    db['Guildid'] = ctx.guild.id
    save_db(db)
    await ctx.reply(f"✅ **Role was set to {role.mention}**")

@setrole.error
async def setrole_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(":x: **You forgot to mention a role!**")
    elif isinstance(error, commands.RoleNotFound):
        await ctx.reply(":x: **Could not find that role.**")

@bot.command()
async def radio(ctx, voice_channel: discord.VoiceChannel, radio_id: int):
    db = get_db()
    role_id = db.get('role')
    
    if role_id:
        role = ctx.guild.get_role(role_id)
        if ctx.author.id != OWNER_ID and (not role or role not in ctx.author.roles):
            return await ctx.reply(f":x: **You don't have permission to use this command! You need the {role.mention} role.**")
    elif ctx.author.id != OWNER_ID:
        return await ctx.reply(":x: **You don't have permission to use this command!**")

    if not (1 <= radio_id <= len(radio_stations)):
         return await ctx.reply(f":x: **Invalid Radio ID! Please choose an ID between 1 and {len(radio_stations)}.**")
    
    try:
        voice_client = await voice_channel.connect()
    except discord.errors.ClientException:
        await ctx.guild.voice_client.disconnect()
        voice_client = await voice_channel.connect()

    audio_source = discord.FFmpegPCMAudio(radio_stations[str(radio_id)])
    voice_client.play(audio_source)
    
    await ctx.reply("📻 **Radio Started**")

@radio.error
async def radio_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(":x: **Usage:** `!radio [voice_channel_id] [radio_id]`\n**e.g.:** `!radio 879417192553271367 2`")
    elif isinstance(error, commands.ChannelNotFound):
        await ctx.reply(":x: **Could not find that voice channel.**")
    elif isinstance(error, commands.BadArgument):
        await ctx.reply(":x: **Invalid arguments provided.**\n**Usage:** `!radio [voice_channel_id] [radio_id]`")


@bot.command()
async def reset(ctx):
    if ctx.author.id != OWNER_ID:
        return await ctx.reply(":x: **You don't have permission to use this command!**")
    await ctx.reply("**Restarting Bot...**")
    await bot.close()
    # In a real-world scenario, you would use a process manager like systemd or pm2 to automatically restart the script.
    # For a simple restart, you might need to run the bot in a loop in your terminal.
    # This example will just close the connection.
    print("Bot is restarting...")
    # os.execv(sys.executable, ['python'] + sys.argv) # This would be a more direct way to restart

@bot.command()
async def dc(ctx):
    db = get_db()
    role_id = db.get('role')
    
    if role_id:
        role = ctx.guild.get_role(role_id)
        if ctx.author.id != OWNER_ID and (not role or role not in ctx.author.roles):
            return await ctx.reply(f":x: **You don't have permission to use this command! You need the {role.mention} role.**")
    elif ctx.author.id != OWNER_ID:
        return await ctx.reply(":x: **You don't have permission to use this command!**")
        
    if ctx.voice_client:
        await ctx.guild.voice_client.disconnect()
        await ctx.reply("✅ **Bot was successfully disconnected**")
    else:
        await ctx.reply(":x: **I'm not in a voice channel!**")

@bot.command()
async def stats(ctx):
    process = psutil.Process(os.getpid())
    memory_usage = process.memory_info().rss / 1024 / 1024

    try:
        with open('package.json', 'r') as f:
            package_data = json.load(f)
            version = package_data.get('version', 'N/A')
    except (FileNotFoundError, json.JSONDecodeError):
        version = 'N/A'

    stats_embed = discord.Embed(
        color=discord.Color.from_rgb(255, 255, 255)
    )
    stats_embed.add_field(name=":robot: Client", value="┕`🟢 Online!`", inline=True)
    stats_embed.add_field(name="⌛ Ping", value=f"┕`{round(bot.latency * 1000)}ms`", inline=True)
    stats_embed.add_field(name=":file_cabinet: Memory", value=f"┕`{memory_usage:.2f}mb`", inline=True)
    stats_embed.add_field(name=":robot: Version", value=f"┕`v{version}`", inline=True)
    stats_embed.add_field(name=":blue_book: Discord.py", value=f"┕`v{discord.__version__}`", inline=True)
    stats_embed.add_field(name=":green_book: Python", value=f"┕`{os.sys.version.split(' ')[0]}`", inline=True)
    stats_embed.set_footer(text=f"Requested By {ctx.author.name}", icon_url=ctx.author.avatar.url)
    stats_embed.timestamp = discord.utils.utcnow()

    await ctx.reply(embed=stats_embed)

# --- Run the Bot ---
bot.run(TOKEN)