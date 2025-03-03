#!/usr/bin/env python3
import asyncio
import logging
import time
import json
import fnmatch
import requests
import datetime
import feedparser

from nio import AsyncClient, LoginResponse, RoomMessageText
from config import matrix_homeserver, matrix_user, matrix_password, matrix_rooms, admins, admin as config_admin, admin_file, start_time
import feed
import persistence
import users
from commands import search_feeds, get_help

logging.basicConfig(level=logging.INFO)

GRACE_PERIOD = 5

def match_feed(feed_dict, pattern):
    if "*" in pattern or "?" in pattern:
        matches = [name for name in feed_dict.keys() if fnmatch.fnmatch(name, pattern)]
        return matches[0] if len(matches) == 1 else (matches if matches else None)
    return pattern if pattern in feed_dict else None

class MatrixBot:
    def __init__(self, homeserver, user, password, rooms):
        self.client = AsyncClient(homeserver, user)
        self.password = password
        self.rooms = rooms
        self.start_time = 0  # Set after initial sync
        self.processing_enabled = False
        self.client.add_event_callback(self.message_callback, RoomMessageText)

    async def login(self):
        response = await self.client.login(self.password, device_name="FuzzyFeeds Bot")
        if hasattr(response, "access_token") and response.access_token:
            logging.info("Matrix login successful")
        else:
            logging.error("Matrix login failed: %s", response)
            raise Exception("Matrix login failed")

    async def join_rooms(self):
        for room in self.rooms:
            try:
                response = await self.client.join(room)
                if hasattr(response, "room_id"):
                    logging.info(f"Joined Matrix room: {room}")
                    await self.send_message(response.room_id, "🤖 FuzzyFeeds Bot is online! Type `!help` for commands.")
                else:
                    logging.error(f"Error joining room {room}: {response}")
            except Exception as e:
                logging.error(f"Exception joining room {room}: {e}")

    async def initial_sync(self):
        logging.info("Performing initial sync...")
        await self.client.sync(timeout=30000)
        await asyncio.sleep(GRACE_PERIOD)
        self.start_time = int(time.time() * 1000)
        self.processing_enabled = True
        logging.info("Initial sync complete; start_time set to %s", self.start_time)

    async def check_feeds_loop(self):
        while True:
            logging.info("Matrix: Checking feeds for new articles...")
            current = time.time()
            # Iterate over Matrix rooms
            for room in self.rooms:
                feeds_to_check = feed.channel_feeds.get(room, {})
                interval = feed.channel_intervals.get(room, feed.default_interval)
                last_checked = feed.last_check_times.get(room, 0)
                if current - last_checked >= interval:
                    for feed_name, feed_url in feeds_to_check.items():
                        parsed = feedparser.parse(feed_url)
                        if parsed.entries:
                            # Collect all entries that are newer than last_checked
                            new_entries = []
                            for entry in parsed.entries:
                                published_time = None
                                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                                    published_time = time.mktime(entry.published_parsed)
                                if published_time and published_time > last_checked:
                                    new_entries.append((published_time, entry))
                            # Sort entries by publication time (oldest first)
                            new_entries.sort(key=lambda x: x[0])
                            for pub_time, entry in new_entries:
                                title = entry.title.strip() if entry.title else "No Title"
                                link = entry.link.strip() if entry.link else ""
                                if link and link not in feed.last_feed_links:
                                    await self.send_message(room, f"New Feed from {feed_name}: {title}\nLink: {link}")
                                    logging.info(f"Matrix: Article posted to {room}: {title}")
                                    feed.save_last_feed_link(link)
                    feed.last_check_times[room] = current
            await asyncio.sleep(300)  # Wait 5 minutes

    async def process_command(self, room, command, sender):
        room_key = room.room_id
        parts = command.strip().split(" ", 2)
        cmd = parts[0].lower()

        # Ignore messages older than sync start time.
        if hasattr(room, "origin_server_ts") and room.origin_server_ts < self.start_time:
            logging.info(f"Ignoring old message in {room_key}: {command}")
            return

        logging.info(f"Processing command `{cmd}` from `{sender}` in `{room_key}`.")

        if cmd == "!admin":
            try:
                with open(admin_file, "r") as f:
                    admin_mapping = json.load(f)
                if sender.lower() == config_admin.lower() or sender.lower() in [a.lower() for a in admins]:
                    irc_admins = {k: v for k, v in admin_mapping.items() if k.startswith("#")}
                    matrix_admins = {k: v for k, v in admin_mapping.items() if k.startswith("!")}
                    discord_admins = {k: v for k, v in admin_mapping.items() if k.isdigit() or k.lower() == "discord"}
                    output = "IRC:\n" + "\n".join([f"{chan}: {adm}" for chan, adm in irc_admins.items()]) + "\n"
                    output += "Matrix:\n" + "\n".join([f"{chan}: {adm}" for chan, adm in matrix_admins.items()]) + "\n"
                    output += "Discord:\n" + "\n".join([f"{chan}: {adm}" for chan, adm in discord_admins.items()])
                else:
                    output = f"Admin for {room_key}: {admin_mapping.get(room_key, 'Not set')}"
                await self.send_message(room_key, output)
            except Exception as e:
                await self.send_message(room_key, f"Error reading admin info: {e}")

        elif cmd == "!listfeeds":
            if room_key not in feed.channel_feeds or not feed.channel_feeds[room_key]:
                await self.send_message(room_key, "No feeds found in this room.")
            else:
                response = "\n".join([f"`{name}` - {url}" for name, url in feed.channel_feeds[room_key].items()])
                await self.send_message(room_key, f"**Feeds for this room:**\n{response}")

        elif cmd == "!latest":
            if len(parts) < 2:
                await self.send_message(room_key, "Usage: !latest <feed_name>")
            else:
                pattern = parts[1].strip()
                if room_key not in feed.channel_feeds:
                    await self.send_message(room_key, "No feeds found in this room.")
                else:
                    matched = match_feed(feed.channel_feeds[room_key], pattern)
                    if matched is None:
                        await self.send_message(room_key, f"No feed matches '{pattern}'.")
                    elif isinstance(matched, list):
                        await self.send_message(room_key, f"Multiple feeds match '{pattern}': {', '.join(matched)}. Please be more specific.")
                    else:
                        title, link = feed.fetch_latest_article(feed.channel_feeds[room_key][matched])
                        if title and link:
                            await self.send_message(room_key, f"Latest from {matched}: {title}\n{link}")
                        else:
                            await self.send_message(room_key, f"No entry available for {matched}.")

        elif cmd == "!stats":
            uptime_seconds = int(time.time() - self.start_time)
            uptime = str(datetime.timedelta(seconds=uptime_seconds))
            if sender.lower() == config_admin.lower() or sender.lower() in [a.lower() for a in admins]:
                matrix_feed_count = sum(len(feed.channel_feeds[k]) for k in feed.channel_feeds if k.startswith("!"))
                response = (f"Global Uptime: {uptime}\n"
                            f"Matrix Global Feeds: {matrix_feed_count} across {len(feed.channel_feeds)} rooms\n"
                            f"User Subscriptions: {sum(len(subs) for subs in feed.subscriptions.values())} total (from {len(feed.subscriptions)} users)")
            else:
                num_channel_feeds = len(feed.channel_feeds.get(room_key, {}))
                response = (f"Uptime: {uptime}\n"
                            f"Room Feeds: {num_channel_feeds}")
            await self.send_message(room_key, response)

        elif cmd == "!help":
            help_text = get_help(parts[1].strip()) if len(parts) == 2 else get_help()
            await self.send_message(room_key, help_text)

        elif cmd == "!search":
            if len(parts) < 2:
                await self.send_message(room_key, "Usage: !search <query>")
            else:
                query = parts[1].strip()
                results = search_feeds(query)
                if not results:
                    await self.send_message(room_key, f"No results found for `{query}`.")
                else:
                    response = "\n".join([f"`{title}` - {url}" for title, url in results])
                    await self.send_message(room_key, f"**Search results for `{query}`:**\n{response}")

        elif cmd == "!addfeed":
            if len(parts) < 3:
                await self.send_message(room_key, "Usage: !addfeed <feed_name> <URL>")
            else:
                feed_name = parts[1].strip()
                feed_url = parts[2].strip()
                if room_key not in feed.channel_feeds:
                    feed.channel_feeds[room_key] = {}
                feed.channel_feeds[room_key][feed_name] = feed_url
                feed.save_feeds()
                await self.send_message(room_key, f"Feed added: {feed_name} ({feed_url})")

        elif cmd == "!delfeed":
            if len(parts) < 2:
                await self.send_message(room_key, "Usage: !delfeed <feed_name>")
            else:
                feed_name = parts[1].strip()
                if room_key not in feed.channel_feeds or feed_name not in feed.channel_feeds[room_key]:
                    await self.send_message(room_key, f"No feed found with name `{feed_name}`.")
                else:
                    del feed.channel_feeds[room_key][feed_name]
                    feed.save_feeds()
                    await self.send_message(room_key, f"Feed `{feed_name}` removed successfully.")

        elif cmd == "!getfeed":
            if len(parts) < 2:
                await self.send_message(room_key, "Usage: !getfeed <query>")
            else:
                query = parts[1].strip()
                results = search_feeds(query)
                if not results:
                    await self.send_message(room_key, "No matching feed found.")
                else:
                    feed_title, feed_url = results[0]
                    title, link = feed.fetch_latest_article(feed_url)
                    if title and link:
                        await self.send_message(room_key, f"Latest from {feed_title}: {title}\n{link}")
                    else:
                        await self.send_message(room_key, f"No entry available for feed {feed_title}.")

        elif cmd == "!getadd":
            if len(parts) < 2:
                await self.send_message(room_key, "Usage: !getadd <query>")
            else:
                query = parts[1].strip()
                results = search_feeds(query)
                if not results:
                    await self.send_message(room_key, "No matching feed found.")
                else:
                    feed_title, feed_url = results[0]
                    if room_key not in feed.channel_feeds:
                        feed.channel_feeds[room_key] = {}
                    feed.channel_feeds[room_key][feed_title] = feed_url
                    feed.save_feeds()
                    await self.send_message(room_key, f"Feed '{feed_title}' added: {feed_url}")

        elif cmd == "!genfeed":
            if len(parts) < 2:
                await self.send_message(room_key, "Usage: !genfeed <website_url>")
            else:
                website_url = parts[1].strip()
                API_ENDPOINT = "https://api.rss.app/v1/generate"
                params = {"url": website_url}
                try:
                    api_response = requests.get(API_ENDPOINT, params=params, timeout=10)
                    if api_response.status_code == 200:
                        result = api_response.json()
                        feed_url = result.get("feed_url")
                        if feed_url:
                            await self.send_message(room_key, f"Generated feed for {website_url}: {feed_url}")
                        else:
                            await self.send_message(room_key, "Feed generation failed: no feed_url in response.")
                    else:
                        await self.send_message(room_key, f"Feed generation API error: {api_response.status_code}")
                except Exception as e:
                    await self.send_message(room_key, f"Error generating feed: {e}")

        elif cmd == "!setinterval":
            if len(parts) < 2:
                await self.send_message(room_key, "Usage: !setinterval <minutes>")
            else:
                try:
                    minutes = int(parts[1].strip())
                    feed.channel_intervals[room_key] = minutes * 60
                    await self.send_message(room_key, f"Feed check interval set to {minutes} minutes for {room_key}.")
                except ValueError:
                    await self.send_message(room_key, "Invalid number of minutes.")

        elif cmd == "!addsub":
            if len(parts) < 3:
                await self.send_message(room_key, "Usage: !addsub <feed_name> <URL>")
            else:
                feed_name = parts[1].strip()
                feed_url = parts[2].strip()
                if sender not in feed.subscriptions:
                    feed.subscriptions[sender] = {}
                feed.subscriptions[sender][feed_name] = feed_url
                feed.save_subscriptions()
                await self.send_message(room_key, f"Subscribed to feed: {feed_name} ({feed_url})")

        elif cmd == "!unsub":
            if len(parts) < 2:
                await self.send_message(room_key, "Usage: !unsub <feed_name>")
            else:
                feed_name = parts[1].strip()
                if sender in feed.subscriptions and feed_name in feed.subscriptions[sender]:
                    del feed.subscriptions[sender][feed_name]
                    feed.save_subscriptions()
                    await self.send_message(room_key, f"Unsubscribed from feed: {feed_name}")
                else:
                    await self.send_message(room_key, f"Not subscribed to feed '{feed_name}'.")

        elif cmd == "!mysubs":
            if sender in feed.subscriptions and feed.subscriptions[sender]:
                response = "\n".join([f"{name}: {url}" for name, url in feed.subscriptions[sender].items()])
                await self.send_message(room_key, response)
            else:
                await self.send_message(room_key, "No subscriptions found.")

        elif cmd == "!latestsub":
            if len(parts) < 2:
                await self.send_message(room_key, "Usage: !latestsub <feed_name>")
            else:
                feed_name = parts[1].strip()
                if sender in feed.subscriptions and feed_name in feed.subscriptions[sender]:
                    url = feed.subscriptions[sender][feed_name]
                    title, link = feed.fetch_latest_article(url)
                    if title and link:
                        await self.send_message(room_key, f"Latest from your subscription '{feed_name}': {title}\n{link}")
                    else:
                        await self.send_message(room_key, f"No entry available for {feed_name}.")
                else:
                    await self.send_message(room_key, f"You are not subscribed to feed '{feed_name}'.")

        elif cmd == "!setsetting":
            if len(parts) < 3:
                await self.send_message(room_key, "Usage: !setsetting <key> <value>")
            else:
                key = parts[1].strip()
                value = parts[2].strip()
                users.add_user(sender)
                user_data = users.get_user(sender)
                if "settings" not in user_data:
                    user_data["settings"] = {}
                user_data["settings"][key] = value
                users.save_users()
                await self.send_message(room_key, f"Setting '{key}' set to '{value}'.")

        elif cmd == "!getsetting":
            if len(parts) < 2:
                await self.send_message(room_key, "Usage: !getsetting <key>")
            else:
                key = parts[1].strip()
                users.add_user(sender)
                user_data = users.get_user(sender)
                if "settings" in user_data and key in user_data["settings"]:
                    await self.send_message(room_key, f"{key}: {user_data['settings'][key]}")
                else:
                    await self.send_message(room_key, f"No setting found for '{key}'.")

        elif cmd == "!settings":
            users.add_user(sender)
            user_data = users.get_user(sender)
            if "settings" in user_data and user_data["settings"]:
                response = "\n".join([f"{k}: {v}" for k, v in user_data["settings"].items()])
                await self.send_message(room_key, response)
            else:
                await self.send_message(room_key, "No settings found.")

        else:
            await self.send_message(room_key, "Unknown command. Use !help for a list.")

    async def message_callback(self, room, event):
        if not self.processing_enabled:
            return
        if hasattr(event, "origin_server_ts") and event.origin_server_ts < self.start_time:
            logging.info(f"Ignoring old message in {room.room_id}: {event.body}")
            return
        if event.body.startswith("!"):
            logging.info(f"Matrix command received in {room.room_id}: {event.body}")
            await self.process_command(room, event.body, event.sender)

    async def send_message(self, room_id, message):
        try:
            await self.client.room_send(
                room_id,
                message_type="m.room.message",
                content={"msgtype": "m.text", "body": message}
            )
            logging.info(f"Sent message to {room_id}: {message}")
        except Exception as e:
            logging.error(f"Failed to send message to {room_id}: {e}")

    async def sync_forever(self):
        while True:
            await self.client.sync(timeout=30000)
            await asyncio.sleep(1)

    async def run(self):
        feed.load_feeds()
        users.load_users()
        await self.login()
        await self.join_rooms()
        await self.initial_sync()
        # Set the feed check baseline for each Matrix room so only new feeds (after syncing) are processed.
        current = time.time()
        for room in self.rooms:
            feed.last_check_times[room] = current
        asyncio.create_task(self.check_feeds_loop())
        await self.sync_forever()

def start_matrix_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    logging.info("Starting Matrix integration...")
    bot = MatrixBot(matrix_homeserver, matrix_user, matrix_password, matrix_rooms)
    try:
        loop.run_until_complete(bot.run())
    except Exception as e:
        logging.error(f"Matrix integration error: {e}")

if __name__ == "__main__":
    start_matrix_bot()
