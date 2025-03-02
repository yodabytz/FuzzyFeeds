#!/usr/bin/env python3
import discord
import logging
import json
import asyncio
from discord.ext import commands
from config import discord_token, discord_channel_id, admin, admins, admin_file
from commands import search_feeds
import feed
import time
import datetime
import config

logging.basicConfig(level=logging.INFO)

# Set up intents for message content
intents = discord.Intents.default()
intents.message_content = True

# Create the bot with the desired command prefix and disable built-in help
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

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
    # Start the feed-checking task
    bot.loop.create_task(check_feeds_for_updates())

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    logging.info(f"Received message: {message.content}")
    await bot.process_commands(message)

async def check_feeds_for_updates():
    await bot.wait_until_ready()
    channel = bot.get_channel(int(discord_channel_id))
    if channel is None:
        logging.error(f"Could not find Discord channel with ID {discord_channel_id}")
        return
    while not bot.is_closed():
        logging.info("Checking feeds for new articles...")
        # Define a callback that sends messages to the Discord channel
        def send_discord_message(feed_channel, message):
            # Since Discord uses a single channel (configured via discord_channel_id),
            # we ignore feed_channel and post every new feed entry to the same channel.
            asyncio.create_task(channel.send(message))
        # Call the shared feed checker with our callback.
        feed.check_feeds(send_discord_message)
        await asyncio.sleep(300)

def register_commands():
    for cmd, desc in help_data.items():
        if cmd.lower() == "help":
            continue

        @bot.command(name=cmd)
        async def dynamic_command(ctx, *args, cmd=cmd):
            full_command = f"{cmd} {' '.join(args)}".strip()
            
            if cmd.lower() == "search":
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

            if cmd.lower() == "addfeed":
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

            if cmd.lower() == "delfeed":
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

            if cmd.lower() == "latest":
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

            if cmd.lower() == "listfeeds":
                channel_id = str(ctx.channel.id)
                if channel_id not in feed.channel_feeds or not feed.channel_feeds[channel_id]:
                    await ctx.send("No feeds found for this channel.")
                    return
                response = "\n".join([f"`{name}` - {url}" for name, url in feed.channel_feeds[channel_id].items()])
                await ctx.send(f"**Feeds for this channel:**\n{response}")
                return

            if cmd.lower() == "stats":
                uptime_seconds = int(time.time() - config.start_time)
                uptime = str(datetime.timedelta(seconds=uptime_seconds))
                if ctx.author.name.lower() == config.admin.lower() or ctx.author.name.lower() in [a.lower() for a in config.admins]:
                    irc_keys = [k for k in feed.channel_feeds if k.startswith("#")]
                    discord_keys = [k for k in feed.channel_feeds if k.isdigit() or k.lower() == "discord"]
                    matrix_keys = [k for k in feed.channel_feeds if k.startswith("!")]
                    irc_feed_count = sum(len(feed.channel_feeds[k]) for k in irc_keys)
                    discord_feed_count = sum(len(feed.channel_feeds[k]) for k in discord_keys)
                    matrix_feed_count = sum(len(feed.channel_feeds[k]) for k in matrix_keys)
                    response = (f"📊 **Global Bot Statistics:**\n"
                                f"- Global Uptime: {uptime}\n"
                                f"- IRC Global Feeds: {irc_feed_count} across {len(irc_keys)} channels\n"
                                f"- Discord Global Feeds: {discord_feed_count} across {len(discord_keys)} channels\n"
                                f"- Matrix Global Feeds: {matrix_feed_count} across {len(matrix_keys)} rooms\n"
                                f"- User Subscriptions: {sum(len(subs) for subs in feed.subscriptions.values())} total (from {len(feed.subscriptions)} users)")
                else:
                    channel_id = str(ctx.channel.id)
                    num_channel_feeds = len(feed.channel_feeds[channel_id]) if channel_id in feed.channel_feeds else 0
                    response = (f"📊 **Server Statistics for {ctx.channel.name}:**\n"
                                f"- Uptime: {uptime}\n"
                                f"- {num_channel_feeds} feeds.")
                await ctx.send(response)
                return

            if cmd.lower() == "admin":
                try:
                    with open(admin_file, "r") as f:
                        admin_mapping = json.load(f)
                    if ctx.author.name.lower() == config.admin.lower() or ctx.author.name.lower() in [a.lower() for a in config.admins]:
                        irc_admins = {k: v for k, v in admin_mapping.items() if k.startswith("#")}
                        matrix_admins = {k: v for k, v in admin_mapping.items() if k.startswith("!")}
                        discord_admins = {k: v for k, v in admin_mapping.items() if k.isdigit() or k.lower() == "discord"}
                        response = "IRC:\n" + "\n".join([f"{chan}: {adm}" for chan, adm in irc_admins.items()]) + "\n"
                        response += "Matrix:\n" + "\n".join([f"{chan}: {adm}" for chan, adm in matrix_admins.items()]) + "\n"
                        response += "Discord:\n" + "\n".join([f"{chan}: {adm}" for chan, adm in discord_admins.items()])
                    else:
                        channel_id = str(ctx.channel.id)
                        if channel_id in admin_mapping:
                            response = f"Admin for this channel: {admin_mapping[channel_id]}"
                        else:
                            response = "No admin info available for this channel."
                    await ctx.send(response)
                except Exception as e:
                    await ctx.send(f"Error reading admin info: {e}")
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

def run_discord_bot():
    bot.run(discord_token)

if __name__ == "__main__":
    run_discord_bot()

