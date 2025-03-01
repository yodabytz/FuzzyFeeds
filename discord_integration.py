#!/usr/bin/env python3
import discord
import logging
import json
import asyncio
from discord.ext import commands
from config import discord_token, discord_channel_id
from commands import search_feeds  # ✅ Import actual search function
import feed  # ✅ Import feed handling functions

logging.basicConfig(level=logging.INFO)

# Set up intents for message processing
intents = discord.Intents.default()
intents.message_content = True  # Needed for text commands like !help

# ✅ Define bot with prefix "!" and disable built-in help
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ✅ Load commands from help.json dynamically
def load_help_data():
    try:
        with open("help.json", "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error("Error loading help.json: %s", e)
        return {}

help_data = load_help_data()  # Load commands on startup

@bot.event
async def on_ready():
    logging.info(f"Discord bot is ready as {bot.user}")
    bot.loop.create_task(check_feeds_for_updates())  # ✅ Start the automatic feed update task

@bot.event
async def on_message(message):
    # Ignore messages sent by the bot itself
    if message.author == bot.user:
        return

    logging.info(f"Received message: {message.content}")  # ✅ Debugging line

    # Ensure bot still processes commands
    await bot.process_commands(message)

# ✅ Function to check feeds for updates and post new articles
async def check_feeds_for_updates():
    await bot.wait_until_ready()  # Ensure bot is connected before running loop
    while not bot.is_closed():
        logging.info("Checking feeds for new articles...")
        new_articles = feed.check_feeds(lambda channel, msg: msg)  # Get new articles

        if new_articles:
            channel = bot.get_channel(discord_channel_id)  # Get the channel where updates should be posted
            if channel:
                for msg in new_articles:
                    await channel.send(msg)
                    logging.info(f"Posted new article: {msg}")
            else:
                logging.error(f"Could not find Discord channel with ID {discord_channel_id}")

        await asyncio.sleep(300)  # ✅ Check for new articles every 5 minutes

# ✅ Register ALL Commands from `help.json` With Proper Functionality
def register_commands():
    for cmd, desc in help_data.items():
        if cmd.lower() == "help":  # ✅ Prevent "help" conflict
            continue

        @bot.command(name=cmd)
        async def dynamic_command(ctx, *args, cmd=cmd):
            """Handles commands dynamically"""
            full_command = f"{cmd} {' '.join(args)}".strip()  # Supports commands with arguments
            
            # ✅ If command is "search", run actual search logic
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

            # ✅ If command is "addfeed", add a feed
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

            # ✅ If command is "delfeed", delete a feed
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

            # ✅ If command is "latest", fetch latest article
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

            # ✅ If command is "listfeeds", list all feeds in the channel
            if cmd == "listfeeds":
                channel_id = str(ctx.channel.id)
                if channel_id not in feed.channel_feeds or not feed.channel_feeds[channel_id]:
                    await ctx.send("No feeds found for this channel.")
                    return
                response = "\n".join([f"`{name}` - {url}" for name, url in feed.channel_feeds[channel_id].items()])
                await ctx.send(f"**Feeds for this channel:**\n{response}")
                return

            # ✅ If command is "stats", display feed statistics
            if cmd == "stats":
                num_channel_feeds = sum(len(feeds) for feeds in feed.channel_feeds.values())
                num_channels = len(feed.channel_feeds)
                num_user_subscriptions = sum(len(subs) for subs in feed.subscriptions.values())
                await ctx.send(f"📊 **Bot Statistics:**\n- `{num_channel_feeds}` feeds across `{num_channels}` channels.\n- `{num_user_subscriptions}` user subscriptions.")
                return

            # ✅ Default: Return help text for unknown commands
            if cmd in help_data:
                await ctx.send(f"`!{cmd}` - {help_data[cmd]}")
            else:
                await ctx.send(f"Unknown command: `!{full_command}`")

register_commands()  # Call function to register all commands

# ✅ Custom Help Command: !help (Lists all loaded commands)
@bot.command(name="help")
async def help_command(ctx):
    """Displays the available commands"""
    help_text = "**Available Commands:**\n"
    for cmd, desc in help_data.items():
        help_text += f"`!{cmd}` - {desc}\n"
    await ctx.send(help_text)

# ✅ Debugging Command: !debug (Checks if the bot is working)
@bot.command(name="debug")
async def debug(ctx):
    """Checks if the bot is online"""
    await ctx.send(f"I am online and working! My user: {bot.user}")

def run_discord_bot():
    bot.run(discord_token)

if __name__ == "__main__":
    run_discord_bot()

