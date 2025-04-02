#!/usr/bin/env python3
import discord
import logging
import json
import asyncio
from discord.ext import commands
from config import discord_token, discord_channel_id, admin, admins
from commands import handle_centralized_command, search_feeds
import feed
import time
import datetime
import config

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Global flag to disable internal feed loop since centralized polling is used
feed_loop_enabled = False

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

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.content.startswith("!"):
        sender = str(message.author)
        target = str(message.channel.id)
        message_text = message.content

        def send_msg(tgt, msg):
            asyncio.create_task(message.channel.send(msg))

        def send_priv(user, msg):
            asyncio.create_task(message.author.send(msg))

        def send_multiline(tgt, msg):
            for line in msg.split("\n"):
                asyncio.create_task(message.channel.send(line))

        is_op_flag = sender.lower() in [a.lower() for a in admins] or sender.lower() == admin.lower()

        handle_centralized_command(
            "discord",
            send_msg,
            send_priv,
            send_multiline,
            sender,
            target,
            message_text,
            is_op_flag
        )

    await bot.process_commands(message)

def register_commands():
    for cmd, desc in help_data.items():
        if cmd.lower() == "help":
            continue

        @bot.command(name=cmd)
        async def dynamic_command(ctx, *args, cmd=cmd):
            full_command = f"{cmd} {' '.join(args)}".strip()

            if cmd == "search":
                if not args:
                    await ctx.send("Usage: `!search <query>` - Search for feeds matching a query.")
                    return
                query = " ".join(args)
                results = search_feeds(query)
                if not results:
                    await ctx.send(f"No results found for `{query}`.")
                else:
                    response = "\n".join([f"`{title}` - {url}" for title, url in results])
                    await ctx.send(f"**Search results for `{query}`:**\n{response}")
                return

            if cmd == "addfeed":
                if len(args) < 2:
                    await ctx.send("Usage: `!addfeed <feed_name> <URL>`")
                    return
                feed_name, feed_url = args[0], args[1]
                channel_id = str(ctx.channel.id)
                if channel_id not in feed.channel_feeds:
                    feed.channel_feeds[channel_id] = {}
                feed.channel_feeds[channel_id][feed_name] = feed_url
                feed.save_feeds()
                await ctx.send(f"Feed added: `{feed_name}` - {feed_url}")
                return

            if cmd == "delfeed":
                if len(args) < 1:
                    await ctx.send("Usage: `!delfeed <feed_name>`")
                    return
                feed_name = args[0]
                channel_id = str(ctx.channel.id)
                if channel_id not in feed.channel_feeds or feed_name not in feed.channel_feeds[channel_id]:
                    await ctx.send(f"No feed found with name `{feed_name}`.")
                    return
                del feed.channel_feeds[channel_id][feed_name]
                feed.save_feeds()
                await ctx.send(f"Feed `{feed_name}` removed successfully.")
                return

def send_discord_message(channel_id, message):
    channel = bot.get_channel(int(channel_id))
    if channel:
        asyncio.run_coroutine_threadsafe(channel.send(message), bot.loop)
    else:
        logging.warning(f"Discord channel {channel_id} not found for message: {message}")

def run_discord_bot():
    feed.load_feeds()
    register_commands()
    bot.run(discord_token)
