"""Discord slash commands (/scan-uni, /create-cert, /status, /help).

The commands are registered in the server that owns DISCORD_CHANNEL_ID
(server commands show up instantly) and only obeyed in that channel.
"""
import logging

import discord
from discord import app_commands

from General_Items.connection_const import DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID
from General_Functions.daily_log import log
from General_Functions import commands
from General_Functions.discord_errors import ConnectionLog

CHANNEL_ID = int(DISCORD_CHANNEL_ID)  # connection_const has checked it's numeric
LIMIT = 2000  # Discord's per-message cap


def listen():
    client = discord.Client(intents=discord.Intents.default())
    tree = app_commands.CommandTree(client)
    synced = False
    connection_log = ConnectionLog()

    async def respond(interaction, name, handler, slow):
        if interaction.channel_id != CHANNEL_ID:
            await interaction.response.send_message(
                f"ThalesWatcher only takes commands in <#{CHANNEL_ID}>.", ephemeral=True)
            return
        log(f"Command /{name} from {interaction.user}")
        if slow:
            # Scans take longer than the 3 s Discord allows for an answer:
            # show "thinking...", then post the result as a follow-up.
            await interaction.response.defer(thinking=True)
        try:
            reply = await client.loop.run_in_executor(None, handler)
        except Exception as e:  # a command must never take the listener down
            reply = f"❌ /{name} failed: {type(e).__name__}: {e}"
        reply = reply[:LIMIT]
        if slow:
            await interaction.followup.send(reply)
        else:
            await interaction.response.send_message(reply)
        log(f"Command /{name} done")

    @tree.command(name="scan-uni", description="Scan eClass now and report new items")
    async def scan_uni(interaction: discord.Interaction):
        await respond(interaction, "scan-uni", commands.scan_uni, slow=True)

    @tree.command(name="create-cert", description="Rebuild the TLS certificate bundle")
    async def create_cert(interaction: discord.Interaction):
        await respond(interaction, "create-cert", commands.create_cert, slow=True)

    @tree.command(name="status", description="Last scan, next scheduled scan, certificate")
    async def status(interaction: discord.Interaction):
        await respond(interaction, "status", commands.status, slow=False)

    @tree.command(name="help", description="List the ThalesWatcher commands")
    async def help_(interaction: discord.Interaction):
        await respond(interaction, "help", commands.help_text, slow=False)

    @client.event
    async def on_ready():
        nonlocal synced
        _report_reconnect()
        if synced:  # on_ready fires again after every reconnect
            return
        try:
            channel = await client.fetch_channel(CHANNEL_ID)
        except discord.HTTPException as e:
            log(f"Discord: can't open channel {CHANNEL_ID} ({e}); commands not registered.")
            return
        tree.copy_global_to(guild=channel.guild)
        await tree.sync(guild=channel.guild)
        synced = True
        log(f"Discord: logged in as {client.user}; commands registered in "
            f"{channel.guild.name} (#{channel.name}).")

    @client.event
    async def on_resumed():  # reconnected and picked up the old session
        _report_reconnect()

    def _report_reconnect():
        summary = connection_log.back_online()
        if summary:
            log(summary)

    # discord.py logs connection problems itself; keep it to warnings and up,
    # and turn its network-error tracebacks into one readable line each.
    handler = logging.StreamHandler()  # sys.stderr, i.e. the daily log
    handler.addFilter(connection_log)
    client.run(DISCORD_BOT_TOKEN, log_handler=handler, log_level=logging.WARNING)
