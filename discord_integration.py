#!/usr/bin/env python3
import discord
import logging
import json
import asyncio
from discord.ext import commands
from config import discord_token, admin, admins
from commands import handle_centralized_command
import feed

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Disable internal feed loop
feed_loop_enabled = False

# Global queue for sending messages with enforced delay
message_queue = asyncio.Queue()

def disable_feed_loop():
    global feed_loop_enabled
    feed_loop_enabled = False

def load_help_data():
    try:
        with open("help.json", "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error("Error loading help.json: %s", e)
        return {}

help_data = load_help_data()

@bot.event
async def on_ready():
    logging.info(f"Discord bot is ready as {bot.user}")
    bot.loop.create_task(message_sender_loop())

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content.startswith("!"):
        sender = str(message.author)
        target = str(message.channel.id)
        message_text = message.content

        async def send_msg(tgt, msg):
            await message_queue.put((message.channel, msg))

        async def send_priv(user, msg):
            try:
                await message_queue.put((message.author, msg))
            except:
                await message_queue.put((message.channel, "(Unable to send DM.)"))

        async def send_multiline(tgt, msg):
            MAX_CHARS = 1900
            lines = [line for line in msg.split("\n") if line.strip()]
            buffer = "```"
            for line in lines:
                if len(buffer) + len(line) + 1 > MAX_CHARS:
                    buffer += "```"
                    await message_queue.put((message.channel, buffer))
                    buffer = "```" + "\n" + line
                else:
                    buffer += "\n" + line
            buffer += "```"
            if buffer.strip() != "```":
                await message_queue.put((message.channel, buffer))

        is_op_flag = sender.lower() in [a.lower() for a in admins] or sender.lower() == admin.lower()

        handle_centralized_command(
            "discord",
            lambda tgt, msg: asyncio.create_task(send_msg(tgt, msg)),
            lambda usr, msg: asyncio.create_task(send_priv(usr, msg)),
            lambda tgt, msg: asyncio.create_task(send_multiline(tgt, msg)),
            sender,
            target,
            message_text,
            is_op_flag
        )

async def message_sender_loop():
    while True:
        target, msg = await message_queue.get()
        try:
            await target.send(msg)
            await asyncio.sleep(1.5)  # spacing between messages to avoid 429
        except Exception as e:
            logging.error(f"Error sending message: {e}")
        message_queue.task_done()

def register_commands():
    for cmd, desc in help_data.get("USER", {}).items():
        if cmd.lower() in ("help", "listfeeds", "stats", "search"):
            continue

        @bot.command(name=cmd)
        async def dynamic_command(ctx, *args, cmd=cmd):
            full_command = f"{cmd} {' '.join(args)}".strip()
            await message_queue.put((ctx.channel, f"Command `{full_command}` received (placeholder logic)."))

def send_discord_message(channel_id, message):
    channel = bot.get_channel(int(channel_id))
    if channel:
        asyncio.run_coroutine_threadsafe(message_queue.put((channel, message)), bot.loop)
    else:
        logging.warning(f"Discord channel {channel_id} not found for message: {message}")

def run_discord_bot():
    feed.load_feeds()
    register_commands()
    bot.run(discord_token)

