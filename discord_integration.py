#!/usr/bin/env python3
import discord
import logging
import json
import asyncio
from discord.ext import commands
from config import discord_token, discord_channel_id, admin, admins
from commands import search_feeds
import feed
import time
import datetime
import config

logging.basicConfig(level=logging.INFO)

# Set up intents for message content.
intents = discord.Intents.default()
intents.message_content = True

# Create the bot with the desired command prefix and disable built-in help.
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

def load_help_data():
    try:
        with open("help.json", "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error("Error loading help.json: %s", e)
        return {}

help_data = load_help_data()

# Global flag to disable internal feed checking loop.
feed_loop_enabled = False

@bot.event
async def on_ready():
    logging.info(f"Discord bot is ready as {bot.user}")
    # Do not start the internal feed checking loop since centralized_polling is handling feed updates.
    # (Internal loop disabled.)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    logging.info(f"Received message: {message.content}")
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

            if cmd == "latest":
                if len(args) < 1:
                    await ctx.send("Usage: `!latest <feed_name>`")
                    return
                feed_name = args[0]
                channel_id = str(ctx.channel.id)
                if channel_id not in feed.channel_feeds or feed_name not in feed.channel_feeds[channel_id]:
                    await ctx.send(f"No feed found with name `{feed_name}`.")
                    return
                title, link = feed.fetch_latest_article(feed.channel_feeds[channel_id][feed_name])
                if title and link:
                    await ctx.send(f"Latest from `{feed_name}`: {title}\n{link}")
                else:
                    await ctx.send(f"No new entries available for `{feed_name}`.")
                return

            if cmd == "listfeeds":
                channel_id = str(ctx.channel.id)
                if channel_id not in feed.channel_feeds or not feed.channel_feeds[channel_id]:
                    await ctx.send("No feeds found for this channel.")
                    return
                response = "\n".join([f"`{name}` - {url}" for name, url in feed.channel_feeds[channel_id].items()])
                await ctx.send(f"**Feeds for this channel:**\n{response}")
                return

            if cmd == "stats":
                uptime_seconds = int(time.time() - config.start_time)
                uptime = str(datetime.timedelta(seconds=uptime_seconds))
                is_admin_flag = (ctx.author.name.lower() == config.admin.lower() or ctx.author.name.lower() in [a.lower() for a in config.admins])
                if is_admin_flag:
                    response = "Admin stats not implemented for Discord yet."
                else:
                    channel_id = str(ctx.channel.id)
                    num_channel_feeds = len(feed.channel_feeds[channel_id]) if channel_id in feed.channel_feeds else 0
                    response = f"Uptime: {uptime}\nChannel '{ctx.channel.name}' Feeds: {num_channel_feeds}"
                await ctx.send(response)
                return

            if cmd in help_data:
                await ctx.send(f"`!{cmd}` - {help_data[cmd]}")
            else:
                await ctx.send(f"Unknown command: `!{full_command}`")

register_commands()

@bot.command(name="help")
async def help_command(ctx):
    help_text = "**Available Commands:**\n"
    for cmd, desc in help_data.items():
        help_text += f"`!{cmd}` - {desc}\n"
    await ctx.send(help_text)

@bot.command(name="debug")
async def debug(ctx):
    await ctx.send(f"I am online and working! My user: {bot.user}")

def send_discord_message(channel, message):
    # This function can be used by centralized_polling to send a message.
    async def _send():
        chan = bot.get_channel(int(channel))
        if chan:
            await chan.send(message)
        else:
            logging.error(f"Discord: Could not find channel with ID {channel}")
    asyncio.run_coroutine_threadsafe(_send(), bot.loop)

def disable_feed_loop():
    global feed_loop_enabled
    feed_loop_enabled = False

def run_discord_bot():
    bot.run(discord_token)

if __name__ == "__main__":
    run_discord_bot()

