import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import random
import os
import psutil
import sys
import asyncio
import aiohttp
import time
from datetime import datetime, timedelta
import logging

# Enhanced logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Try to import Gemini AI (optional)
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logger.warning("Gemini AI not available. Install 'google-generativeai' for AI features.")

# --- Configuration Loading ---
try:
    with open('./botconfig/config.json', 'r') as f:
        config = json.load(f)
    TOKEN = config['token']
    PREFIX = config['prefix']
    OWNER_ID = int(config['ownerid'])
    GEMINI_API_KEY = config.get('gemini_api_key', '')
    
    # Configure Gemini AI if available
    if GEMINI_AVAILABLE and GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        ai_model = genai.GenerativeModel('gemini-pro')
    else:
        ai_model = None

    with open('./botconfig/radiostation.json', 'r') as f:
        radio_stations = json.load(f)
        
except FileNotFoundError as e:
    logger.error(f"Configuration error: {e}")
    print(f"Error: {e}. Make sure your 'botconfig' directory and all .json files are set up correctly.")
    sys.exit()

# --- Database Handling ---
def get_db():
    try:
        with open('./db/role.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_db(db):
    os.makedirs('db', exist_ok=True)
    with open('./db/role.json', 'w') as f:
        json.dump(db, f, indent=4)

def get_guild_config(guild_id):
    try:
        with open(f'./db/guild_{guild_id}.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            'auto_reconnect': True,
            'disconnect_when_alone': True,
            'now_playing_channel': None,
            'current_station': None,
            'volume': 0.5
        }

def save_guild_config(guild_id, config):
    os.makedirs('db', exist_ok=True)
    with open(f'./db/guild_{guild_id}.json', 'w') as f:
        json.dump(config, f, indent=4)

def get_stats():
    try:
        with open('./db/stats.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            'commands_used': 0,
            'total_playtime': 0,
            'errors': 0,
            'start_time': time.time()
        }

def save_stats(stats):
    os.makedirs('db', exist_ok=True)
    with open('./db/stats.json', 'w') as f:
        json.dump(stats, f, indent=4)

# --- Bot Setup ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# Global variables
current_stations = {}  # guild_id: station_info
control_messages = {}  # guild_id: message_id for control buttons
stats = get_stats()

# --- Music Control View ---
class MusicControlView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="⏸️ Pause", style=discord.ButtonStyle.secondary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_permissions(interaction):
            await interaction.response.send_message("❌ **You need DJ role or be the owner to use this!**", ephemeral=True)
            return
            
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_playing():
            voice_client.pause()
            button.label = "▶️ Resume"
            button.style = discord.ButtonStyle.success
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("⏸️ **Music paused!**", ephemeral=True)
        elif voice_client and voice_client.is_paused():
            voice_client.resume()
            button.label = "⏸️ Pause"
            button.style = discord.ButtonStyle.secondary
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("▶️ **Music resumed!**", ephemeral=True)
        else:
            await interaction.response.send_message("❌ **No music is playing!**", ephemeral=True)

    @discord.ui.button(label="⏹️ Stop", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_permissions(interaction):
            await interaction.response.send_message("❌ **You need DJ role or be the owner to use this!**", ephemeral=True)
            return
            
        voice_client = interaction.guild.voice_client
        if voice_client:
            voice_client.stop()
            await voice_client.disconnect()
            if self.guild_id in current_stations:
                del current_stations[self.guild_id]
            await interaction.response.send_message("⏹️ **Music stopped and disconnected!**")
        else:
            await interaction.response.send_message("❌ **Not connected to any voice channel!**", ephemeral=True)

    @discord.ui.button(label="🔄 Reconnect", style=discord.ButtonStyle.primary)
    async def reconnect_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_permissions(interaction):
            await interaction.response.send_message("❌ **You need DJ role or be the owner to use this!**", ephemeral=True)
            return
            
        if self.guild_id in current_stations:
            station_info = current_stations[self.guild_id]
            voice_channel = bot.get_channel(station_info['channel_id'])
            if voice_channel:
                try:
                    if interaction.guild.voice_client:
                        await interaction.guild.voice_client.disconnect()
                    
                    voice_client = await voice_channel.connect()
                    audio_source = discord.FFmpegPCMAudio(station_info['url'])
                    voice_client.play(audio_source)
                    
                    await interaction.response.send_message("🔄 **Successfully reconnected and resumed music!**")
                except Exception as e:
                    await interaction.response.send_message(f"❌ **Reconnection failed: {e}**", ephemeral=True)
            else:
                await interaction.response.send_message("❌ **Original voice channel not found!**", ephemeral=True)
        else:
            await interaction.response.send_message("❌ **No previous station to reconnect to!**", ephemeral=True)

    def check_permissions(self, interaction):
        if interaction.user.id == OWNER_ID:
            return True
        db = get_db()
        role_id = db.get('role')
        if not role_id:
            return False
        role = interaction.guild.get_role(role_id)
        return role and role in interaction.user.roles

# --- Bot Events ---
@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user.name} ({bot.user.id})')
    print(f'🎵 {bot.user.name} is now online!')
    
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")
    
    # Start background tasks
    update_activity.start()
    check_voice_connections.start()
    update_now_playing.start()

@bot.event
async def on_voice_state_update(member, before, after):
    """Handle voice state changes for auto-disconnect"""
    if member == bot.user:
        return
    
    # Check if bot should disconnect when alone
    for guild in bot.guilds:
        if guild.voice_client:
            guild_config = get_guild_config(guild.id)
            if guild_config.get('disconnect_when_alone', True):
                voice_channel = guild.voice_client.channel
                members = [m for m in voice_channel.members if not m.bot]
                
                if len(members) == 0:
                    await asyncio.sleep(30)  # Wait 30 seconds
                    # Check again
                    members = [m for m in voice_channel.members if not m.bot]
                    if len(members) == 0:
                        await guild.voice_client.disconnect()
                        logger.info(f"Auto-disconnected from {guild.name} - no members in voice channel")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    # AI assistance for confused users
    if ai_model and bot.user.mentioned_in(message) and len(message.content.split()) > 3:
        try:
            prompt = f"User is asking about a Discord music bot. Their message: '{message.content}'. Give a helpful, short response about bot commands or features."
            response = ai_model.generate_content(prompt)
            
            embed = discord.Embed(
                title="🤖 AI Assistant",
                description=response.text,
                color=discord.Color.blue()
            )
            embed.set_footer(text="Powered by Gemini AI")
            await message.reply(embed=embed)
        except Exception as e:
            logger.error(f"AI response error: {e}")
    
    await bot.process_commands(message)

# --- Background Tasks ---
@tasks.loop(seconds=15)
async def update_activity():
    activities = [
        "🎵 24/7 VAYU RADIO STATION 📻",
        "🎶 LISTENING TO MUSIC",
        "📻 BROADCASTING NON-STOP TUNES",
        "🎧 ALWAYS LIVE WITH MUSIC",
        "🎙️ LIVE 24/7 WITH VAYU RADIO",
        "🔊 KEEPING THE BEATS ALIVE",
        f"🎵 {len(bot.guilds)} SERVERS CONNECTED",
        f"📊 {len([g for g in bot.guilds if g.voice_client])} RADIOS PLAYING"
    ]
    activity = random.choice(activities)
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=activity))

@tasks.loop(minutes=1)
async def check_voice_connections():
    """Check and auto-reconnect voice connections if needed"""
    for guild_id, station_info in current_stations.copy().items():
        guild = bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            guild_config = get_guild_config(guild_id)
            if guild_config.get('auto_reconnect', True):
                try:
                    voice_channel = bot.get_channel(station_info['channel_id'])
                    if voice_channel:
                        voice_client = await voice_channel.connect()
                        audio_source = discord.FFmpegPCMAudio(station_info['url'])
                        voice_client.play(audio_source)
                        logger.info(f"Auto-reconnected to {guild.name}")
                except Exception as e:
                    logger.error(f"Auto-reconnect failed for {guild.name}: {e}")

@tasks.loop(minutes=5)
async def update_now_playing():
    """Update now playing messages in guilds"""
    for guild_id, station_info in current_stations.items():
        guild = bot.get_guild(guild_id)
        if guild and guild.voice_client and guild.voice_client.is_playing():
            guild_config = get_guild_config(guild_id)
            np_channel_id = guild_config.get('now_playing_channel')
            
            if np_channel_id:
                channel = bot.get_channel(np_channel_id)
                if channel:
                    try:
                        embed = discord.Embed(
                            title="🎵 Now Playing",
                            description=f"**{station_info['name']}**",
                            color=discord.Color.green(),
                            timestamp=datetime.utcnow()
                        )
                        embed.add_field(name="📻 Station", value=station_info['name'], inline=True)
                        embed.add_field(name="👥 Listeners", value=len(guild.voice_client.channel.members), inline=True)
                        embed.add_field(name="⏱️ Uptime", value=station_info.get('uptime', 'Unknown'), inline=True)
                        embed.set_thumbnail(url="https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif")
                        
                        await channel.send(embed=embed)
                    except Exception as e:
                        logger.error(f"Failed to update now playing in {guild.name}: {e}")

# --- Helper Functions ---
def is_dj_or_owner():
    def predicate(ctx):
        if ctx.author.id == OWNER_ID:
            return True
        db = get_db()
        role_id = db.get('role')
        if not role_id:
            return False
        role = ctx.guild.get_role(role_id)
        return role and role in ctx.author.roles
    return commands.check(predicate)

def log_command_usage(command_name, user_id, guild_id):
    global stats
    stats['commands_used'] += 1
    save_stats(stats)

# --- Slash Commands ---
@bot.tree.command(name="radio", description="Play radio in a voice channel")
@app_commands.describe(
    channel="Voice channel to join",
    radio_id="Radio station ID from radiolist"
)
async def slash_radio(interaction: discord.Interaction, channel: discord.VoiceChannel, radio_id: int):
    # Check permissions
    if interaction.user.id != OWNER_ID:
        db = get_db()
        role_id = db.get('role')
        if not role_id:
            await interaction.response.send_message("❌ **No DJ role set! Ask an admin to set one.**", ephemeral=True)
            return
        role = interaction.guild.get_role(role_id)
        if not role or role not in interaction.user.roles:
            await interaction.response.send_message("❌ **You need DJ role or be the owner to use this!**", ephemeral=True)
            return
    
    radio_key = str(radio_id)
    if radio_key not in radio_stations:
        await interaction.response.send_message(f"❌ **Invalid Radio ID! Use `/radiolist` to see valid IDs.**", ephemeral=True)
        return
    
    try:
        await interaction.response.defer()
        
        # Connect to voice channel
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.move_to(channel)
            voice_client = interaction.guild.voice_client
        else:
            voice_client = await channel.connect()
        
        # Stop current audio and play new
        if voice_client.is_playing():
            voice_client.stop()
        
        audio_source = discord.FFmpegPCMAudio(radio_stations[radio_key])
        voice_client.play(audio_source)
        
        # Store station info
        station_name = f"Radio Station {radio_id}"
        current_stations[interaction.guild.id] = {
            'url': radio_stations[radio_key],
            'name': station_name,
            'channel_id': channel.id,
            'start_time': time.time()
        }
        
        # Create control embed
        embed = discord.Embed(
            title="🎵 Radio Started!",
            description=f"Now playing **{station_name}** in {channel.mention}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="📻 Station", value=station_name, inline=True)
        embed.add_field(name="🔊 Channel", value=channel.name, inline=True)
        embed.add_field(name="👥 Listeners", value=len(channel.members), inline=True)
        embed.set_thumbnail(url="https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif")
        
        view = MusicControlView(interaction.guild.id)
        message = await interaction.followup.send(embed=embed, view=view)
        control_messages[interaction.guild.id] = message.id
        
        log_command_usage("radio", interaction.user.id, interaction.guild.id)
        
    except Exception as e:
        await interaction.followup.send(f"❌ **Error: {e}**", ephemeral=True)
        global stats
        stats['errors'] += 1
        save_stats(stats)

@bot.tree.command(name="radiolist", description="Show available radio stations")
async def slash_radiolist(interaction: discord.Interaction):
    try:
        with open('./botconfig/radioid.json', 'r', encoding='utf-8') as f:
            contents = f.read()
        
        embed = discord.Embed(
            title="📻 Available Radio Stations",
            description=f"```json\n{contents}\n```",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
        
        await interaction.response.send_message(embed=embed)
        log_command_usage("radiolist", interaction.user.id, interaction.guild.id)
        
    except FileNotFoundError:
        await interaction.response.send_message("❌ **Radio ID list not found!**", ephemeral=True)

@bot.tree.command(name="stats", description="Show detailed bot statistics")
async def slash_stats(interaction: discord.Interaction):
    process = psutil.Process(os.getpid())
    memory_usage = process.memory_info().rss / 1024 / 1024
    cpu_usage = process.cpu_percent()
    
    # System stats
    disk_usage = psutil.disk_usage('/').percent
    uptime = time.time() - stats.get('start_time', time.time())
    uptime_str = str(timedelta(seconds=int(uptime)))
    
    embed = discord.Embed(
        title="📊 Advanced Bot Statistics",
        color=discord.Color.gold(),
        timestamp=datetime.utcnow()
    )
    
    # Bot Stats
    embed.add_field(name="🤖 Bot Status", value="🟢 Online", inline=True)
    embed.add_field(name="⌛ Ping", value=f"{round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="⏰ Uptime", value=uptime_str, inline=True)
    
    # System Stats
    embed.add_field(name="💾 Memory", value=f"{memory_usage:.1f} MB", inline=True)
    embed.add_field(name="⚡ CPU", value=f"{cpu_usage}%", inline=True)
    embed.add_field(name="💿 Disk", value=f"{disk_usage}%", inline=True)
    
    # Usage Stats
    embed.add_field(name="🏠 Servers", value=len(bot.guilds), inline=True)
    embed.add_field(name="👥 Users", value=len(bot.users), inline=True)
    embed.add_field(name="🎵 Active Radios", value=len([g for g in bot.guilds if g.voice_client]), inline=True)
    
    # Command Stats
    embed.add_field(name="📝 Commands Used", value=stats.get('commands_used', 0), inline=True)
    embed.add_field(name="❌ Errors", value=stats.get('errors', 0), inline=True)
    embed.add_field(name="🐍 Python", value=f"{sys.version.split()[0]}", inline=True)
    
    embed.set_thumbnail(url="https://media.giphy.com/media/3oKIPEqDGUULpEU0aQ/giphy.gif")
    embed.set_footer(text=f"Requested by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
    
    await interaction.response.send_message(embed=embed)
    log_command_usage("stats", interaction.user.id, interaction.guild.id)

@bot.tree.command(name="setup", description="Server setup guide")
async def slash_setup(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **You need Administrator permission to use setup!**", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🛠️ Server Setup Guide",
        description="Follow these steps to configure the radio bot:",
        color=discord.Color.purple(),
        timestamp=datetime.utcnow()
    )
    
    setup_steps = [
        "1️⃣ Set DJ Role: `/setrole @YourDJRole`",
        "2️⃣ Test Radio: `/radio #voice-channel 1`",
        "3️⃣ Set Now Playing Channel: `/nowplaying #channel`",
        "4️⃣ Configure Auto Settings: `/autoreconnect on`",
        "5️⃣ View Available Stations: `/radiolist`"
    ]
    
    embed.add_field(name="📋 Setup Steps", value="\n".join(setup_steps), inline=False)
    embed.add_field(name="🎯 Quick Start", value="Most users just need steps 1 and 2!", inline=False)
    embed.add_field(name="❓ Need Help?", value="Mention the bot with your question for AI assistance!", inline=False)
    
    embed.set_thumbnail(url="https://media.giphy.com/media/3o7TKTDn976rzVgky4/giphy.gif")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="disconnect", description="Disconnect bot from voice channel")
async def slash_disconnect(interaction: discord.Interaction):
    # Check permissions
    if interaction.user.id != OWNER_ID:
        db = get_db()
        role_id = db.get('role')
        if not role_id:
            await interaction.response.send_message("❌ **No DJ role set!**", ephemeral=True)
            return
        role = interaction.guild.get_role(role_id)
        if not role or role not in interaction.user.roles:
            await interaction.response.send_message("❌ **You need DJ role or be the owner!**", ephemeral=True)
            return
    
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        if interaction.guild.id in current_stations:
            del current_stations[interaction.guild.id]
        await interaction.response.send_message("✅ **Successfully disconnected from voice channel!**")
    else:
        await interaction.response.send_message("❌ **Not connected to any voice channel!**", ephemeral=True)

# --- Prefix Commands (Backward Compatibility) ---
@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="📻 VAYU Radio Bot - Help Menu",
        description="🎵 Advanced 24/7 Radio Bot with AI Support",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    
    # Slash Commands
    slash_commands = [
        "`/radio` - Play radio station",
        "`/radiolist` - View available stations", 
        "`/stats` - Detailed bot statistics",
        "`/setup` - Server setup guide",
        "`/disconnect` - Disconnect from voice"
    ]
    
    # Prefix Commands
    prefix_commands = [
        f"`{PREFIX}radio` - Play radio (legacy)",
        f"`{PREFIX}radiolist` - Radio list (legacy)",
        f"`{PREFIX}stats` - Statistics (legacy)",
        f"`{PREFIX}setrole` - Set DJ role",
        f"`{PREFIX}reset` - Restart bot (owner)"
    ]
    
    embed.add_field(name="⚡ Slash Commands (Recommended)", value="\n".join(slash_commands), inline=False)
    embed.add_field(name="📝 Prefix Commands", value="\n".join(prefix_commands), inline=False)
    embed.add_field(name="🤖 AI Features", value="Mention me with questions for AI help!", inline=False)
    embed.add_field(name="🎛️ Control Features", value="Use buttons to pause, stop, and reconnect!", inline=False)
    
    embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user.avatar else None)
    embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
    
    await ctx.reply(embed=embed)

@bot.command()
async def radiolist(ctx):
    try:
        with open('./botconfig/radioid.json', 'r', encoding='utf-8') as f:
            contents = f.read()
        embed = discord.Embed(
            title="📻 Radio Station List",
            description=f"```json\n{contents}\n```",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
        await ctx.reply(embed=embed)
        log_command_usage("radiolist", ctx.author.id, ctx.guild.id)
    except FileNotFoundError:
        await ctx.reply("❌ **Radio ID list file not found!**")

@bot.command()
@commands.is_owner()
async def setrole(ctx, role: discord.Role):
    db = get_db()
    db['role'] = role.id
    db['Guildid'] = ctx.guild.id
    save_db(db)
    
    embed = discord.Embed(
        title="✅ DJ Role Set Successfully",
        description=f"DJ role has been set to {role.mention}",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    await ctx.reply(embed=embed)

@setrole.error
async def setrole_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("❌ **Usage:** `!setrole @rolename`")
    elif isinstance(error, commands.RoleNotFound):
        await ctx.reply("❌ **Role not found!**")
    elif isinstance(error, commands.NotOwner):
        await ctx.reply("❌ **Only the bot owner can set DJ roles!**")

@bot.command()
@is_dj_or_owner()
async def radio(ctx, voice_channel: discord.VoiceChannel, radio_id: int):
    radio_key = str(radio_id)
    if radio_key not in radio_stations:
        return await ctx.reply(f"❌ **Invalid Radio ID! Use `{PREFIX}radiolist` for valid IDs.**")
    
    try:
        # Connect logic
        if ctx.voice_client:
            await ctx.voice_client.move_to(voice_channel)
            voice_client = ctx.voice_client
        else:
            voice_client = await voice_channel.connect()

        if voice_client.is_playing():
            voice_client.stop()
        
        audio_source = discord.FFmpegPCMAudio(radio_stations[radio_key])
        voice_client.play(audio_source)
        
        # Store station info
        station_name = f"Radio Station {radio_id}"
        current_stations[ctx.guild.id] = {
            'url': radio_stations[radio_key],
            'name': station_name,
            'channel_id': voice_channel.id,
            'start_time': time.time()
        }
        
        embed = discord.Embed(
            title="🎵 Radio Started!",
            description=f"Playing **{station_name}** in {voice_channel.mention}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url="https://media.giphy.com/media/3oEjI6SIIHBdRxXI40/giphy.gif")
        
        view = MusicControlView(ctx.guild.id)
        message = await ctx.reply(embed=embed, view=view)
        control_messages[ctx.guild.id] = message.id
        
        log_command_usage("radio", ctx.author.id, ctx.guild.id)

    except Exception as e:
        await ctx.reply(f"❌ **Error: {e}**")
        global stats
        stats['errors'] += 1
        save_stats(stats)

@radio.error
async def radio_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.reply("❌ **You need DJ role or be the owner to use this command!**")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"❌ **Usage:** `{PREFIX}radio [voice_channel] [radio_id]`\n**Example:** `{PREFIX}radio General 2`")
    elif isinstance(error, commands.ChannelNotFound):
        await ctx.reply("❌ **Voice channel not found!**")
    elif isinstance(error, commands.BadArgument):
        await ctx.reply("❌ **Invalid arguments! Check channel name and radio ID.**")

@bot.command()
@commands.is_owner()
async def reset(ctx):
    embed = discord.Embed(
        title="🔄 Restarting Bot",
        description="Bot is restarting... Please wait.",
        color=discord.Color.orange(),
        timestamp=datetime.utcnow()
    )
    await ctx.reply(embed=embed)
    
    # Save restart data
    restart_data = {
        'channel_id': ctx.channel.id,
        'user_id': ctx.author.id,
        'timestamp': time.time()
    }
    with open('./db/restart_data.json', 'w') as f:
        json.dump(restart_data, f)
    
    await bot.close()

@bot.command()
@is_dj_or_owner()
async def dc(ctx):
    if ctx.voice_client:
        await ctx.guild.voice_client.disconnect()
        if ctx.guild.id in current_stations:
            del current_stations[ctx.guild.id]
        
        embed = discord.Embed(
            title="✅ Disconnected Successfully",
            description="Bot has been disconnected from voice channel.",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        await ctx.reply(embed=embed)
    else:
        await ctx.reply("❌ **Not connected to any voice channel!**")

@dc.error
async def dc_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.reply("❌ **You need DJ role or be the owner to disconnect the bot!**")

@bot.command()
async def stats(ctx):
    process = psutil.Process(os.getpid())
    memory_usage = process.memory_info().rss / 1024 / 1024
    cpu_usage = process.cpu_percent()
    
    embed = discord.Embed(
        title="📊 Bot Statistics",
        color=discord.Color.gold(),
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(name="🤖 Status", value="🟢 Online", inline=True)
    embed.add_field(name="⌛ Ping", value=f"{round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="💾 Memory", value=f"{memory_usage:.1f} MB", inline=True)
    embed.add_field(name="⚡ CPU", value=f"{cpu_usage}%", inline=True)
    embed.add_field(name="🏠 Servers", value=len(bot.guilds), inline=True)
    embed.add_field(name="🎵 Active Radios", value=len([g for g in bot.guilds if g.voice_client]), inline=True)
    embed.add_field(name="🐍 Python", value=f"{sys.version.split()[0]}", inline=True)
    embed.add_field(name="📚 Discord.py", value=f"v{discord.__version__}", inline=True)
    embed.add_field(name="📝 Commands Used", value=stats.get('commands_used', 0), inline=True)
    
    embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
    await ctx.reply(embed=embed)
    log_command_usage("stats", ctx.author.id, ctx.guild.id)

# --- Advanced Configuration Commands ---
@bot.tree.command(name="setrole", description="Set DJ role (Owner only)")
@app_commands.describe(role="The role to set as DJ role")
async def slash_setrole(interaction: discord.Interaction, role: discord.Role):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ **Only the bot owner can set DJ roles!**", ephemeral=True)
        return
    
    db = get_db()
    db['role'] = role.id
    db['Guildid'] = interaction.guild.id
    save_db(db)
    
    embed = discord.Embed(
        title="✅ DJ Role Updated",
        description=f"DJ role has been set to {role.mention}",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="autoreconnect", description="Toggle auto-reconnect feature")
@app_commands.describe(enabled="Enable or disable auto-reconnect")
@app_commands.choices(enabled=[
    app_commands.Choice(name="Enable", value="on"),
    app_commands.Choice(name="Disable", value="off")
])
async def slash_autoreconnect(interaction: discord.Interaction, enabled: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **You need Administrator permission!**", ephemeral=True)
        return
    
    guild_config = get_guild_config(interaction.guild.id)
    guild_config['auto_reconnect'] = (enabled == "on")
    save_guild_config(interaction.guild.id, guild_config)
    
    status = "✅ Enabled" if enabled == "on" else "❌ Disabled"
    embed = discord.Embed(
        title="🔄 Auto-Reconnect Settings",
        description=f"Auto-reconnect has been **{status}**",
        color=discord.Color.green() if enabled == "on" else discord.Color.red(),
        timestamp=datetime.utcnow()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="nowplaying", description="Set channel for now playing updates")
@app_commands.describe(channel="Channel for now playing messages")
async def slash_nowplaying(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ **You need Administrator permission!**", ephemeral=True)
        return
    
    guild_config = get_guild_config(interaction.guild.id)
    guild_config['now_playing_channel'] = channel.id
    save_guild_config(interaction.guild.id, guild_config)
    
    embed = discord.Embed(
        title="🎵 Now Playing Channel Set",
        description=f"Now playing updates will be sent to {channel.mention}",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="monitor", description="Show detailed monitoring information")
async def slash_monitor(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ **Only the bot owner can view monitoring info!**", ephemeral=True)
        return
    
    process = psutil.Process(os.getpid())
    
    # System Information
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    embed = discord.Embed(
        title="🔍 System Monitoring",
        description="Detailed system and bot performance metrics",
        color=discord.Color.purple(),
        timestamp=datetime.utcnow()
    )
    
    # Bot Performance
    embed.add_field(
        name="🤖 Bot Performance",
        value=f"**Memory:** {process.memory_info().rss / 1024 / 1024:.1f} MB\n"
              f"**CPU:** {process.cpu_percent():.1f}%\n"
              f"**Threads:** {process.num_threads()}\n"
              f"**Uptime:** {str(timedelta(seconds=int(time.time() - stats.get('start_time', time.time()))))}",
        inline=True
    )
    
    # System Resources
    embed.add_field(
        name="💻 System Resources",
        value=f"**CPU Usage:** {cpu_percent}%\n"
              f"**RAM Usage:** {memory.percent}%\n"
              f"**Disk Usage:** {disk.percent}%\n"
              f"**Available RAM:** {memory.available / 1024 / 1024 / 1024:.1f} GB",
        inline=True
    )
    
    # Bot Statistics
    active_radios = len([g for g in bot.guilds if g.voice_client])
    embed.add_field(
        name="📊 Bot Statistics",
        value=f"**Servers:** {len(bot.guilds)}\n"
              f"**Active Radios:** {active_radios}\n"
              f"**Commands Used:** {stats.get('commands_used', 0)}\n"
              f"**Errors:** {stats.get('errors', 0)}",
        inline=True
    )
    
    # Connection Status
    connection_status = []
    for guild in bot.guilds:
        if guild.voice_client:
            connection_status.append(f"🟢 {guild.name}")
        else:
            connection_status.append(f"⚫ {guild.name}")
    
    if connection_status:
        embed.add_field(
            name="🔗 Voice Connections",
            value="\n".join(connection_status[:10]),  # Limit to 10 servers
            inline=False
        )
    
    embed.set_thumbnail(url="https://media.giphy.com/media/3oKIPEqDGUULpEU0aQ/giphy.gif")
    await interaction.response.send_message(embed=embed)

# --- Error Handling ---
@bot.event
async def on_command_error(ctx, error):
    """Global error handler"""
    if isinstance(error, commands.CommandNotFound):
        return  # Ignore unknown commands
    
    global stats
    stats['errors'] += 1
    save_stats(stats)
    
    error_embed = discord.Embed(
        title="❌ Command Error",
        description="An error occurred while executing the command.",
        color=discord.Color.red(),
        timestamp=datetime.utcnow()
    )
    
    if isinstance(error, commands.MissingRequiredArgument):
        error_embed.add_field(name="Error", value="Missing required argument", inline=False)
    elif isinstance(error, commands.BadArgument):
        error_embed.add_field(name="Error", value="Invalid argument provided", inline=False)
    elif isinstance(error, commands.CheckFailure):
        error_embed.add_field(name="Error", value="You don't have permission to use this command", inline=False)
    elif isinstance(error, commands.CommandOnCooldown):
        error_embed.add_field(name="Error", value=f"Command on cooldown. Try again in {error.retry_after:.1f}s", inline=False)
    else:
        error_embed.add_field(name="Error", value=str(error), inline=False)
    
    try:
        await ctx.reply(embed=error_embed, ephemeral=True)
    except:
        pass  # Ignore if can't send error message
    
    logger.error(f"Command error in {ctx.guild.name if ctx.guild else 'DM'}: {error}")

@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Global slash command error handler"""
    global stats
    stats['errors'] += 1
    save_stats(stats)
    
    error_embed = discord.Embed(
        title="❌ Command Error",
        description="An error occurred while executing the slash command.",
        color=discord.Color.red(),
        timestamp=datetime.utcnow()
    )
    
    if isinstance(error, app_commands.MissingPermissions):
        error_embed.add_field(name="Error", value="Missing required permissions", inline=False)
    elif isinstance(error, app_commands.CommandOnCooldown):
        error_embed.add_field(name="Error", value=f"Command on cooldown. Try again in {error.retry_after:.1f}s", inline=False)
    else:
        error_embed.add_field(name="Error", value=str(error), inline=False)
    
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=error_embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
    except:
        pass
    
    logger.error(f"Slash command error in {interaction.guild.name if interaction.guild else 'DM'}: {error}")

# --- Startup Recovery ---
@bot.event
async def on_ready():
    # Check for restart data
    try:
        with open('./db/restart_data.json', 'r') as f:
            restart_data = json.load(f)
        
        channel = bot.get_channel(restart_data['channel_id'])
        if channel:
            embed = discord.Embed(
                title="✅ Bot Restarted Successfully",
                description="Bot has been restarted and is now online!",
                color=discord.Color.green(),
                timestamp=datetime.utcnow()
            )
            await channel.send(embed=embed)
        
        # Clean up restart data
        os.remove('./db/restart_data.json')
    except FileNotFoundError:
        pass  # No restart data found
    except Exception as e:
        logger.error(f"Error handling restart data: {e}")

# --- Additional Utility Commands ---
@bot.tree.command(name="ping", description="Check bot latency")
async def slash_ping(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Bot latency: **{round(bot.latency * 1000)}ms**",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="invite", description="Get bot invite link")
async def slash_invite(interaction: discord.Interaction):
    invite_url = discord.utils.oauth_url(bot.user.id, permissions=discord.Permissions(
        connect=True,
        speak=True,
        send_messages=True,
        embed_links=True,
        read_message_history=True,
        add_reactions=True,
        use_slash_commands=True
    ))
    
    embed = discord.Embed(
        title="🔗 Invite VAYU Radio Bot",
        description=f"[Click here to invite the bot to your server]({invite_url})",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Required Permissions", value="Connect, Speak, Send Messages, Embed Links", inline=False)
    embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user.avatar else None)
    
    await interaction.response.send_message(embed=embed)

# --- Run the Bot ---
if __name__ == "__main__":
    if not TOKEN:
        logger.error("Bot token is missing. Please check your config.json file.")
        print("❌ Error: Bot token is missing. Please check your config.json file.")
    else:
        try:
            logger.info("Starting VAYU Radio Bot...")
            print("🎵 Starting VAYU Radio Bot...")
            bot.run(TOKEN)
        except Exception as e:
            logger.error(f"Failed to start bot: {e}")
            print(f"❌ Failed to start bot: {e}")