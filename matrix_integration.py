#!/usr/bin/env python3
import asyncio
import logging
import time
import json
import fnmatch
import requests
import datetime
import feedparser
import os

from nio import AsyncClient, LoginResponse, RoomMessageText
from config import (
    matrix_homeserver, matrix_user, matrix_password, matrix_rooms,
    admins, admin as config_admin, admin_file, start_time
)
import feed
import persistence
import users
from commands import search_feeds, get_help

logging.basicConfig(level=logging.INFO)

GRACE_PERIOD = 5
POSTED_FILE = "matrix_posted.json"

# Global instance for external use.
matrix_bot_instance = None

def load_posted_articles():
    if os.path.exists(POSTED_FILE):
        try:
            with open(POSTED_FILE, "r") as f:
                data = json.load(f)
                return set(data)
        except Exception as e:
            logging.error(f"Error loading {POSTED_FILE}: {e}")
            return set()
    return set()

def save_posted_articles(posted_set):
    try:
        with open(POSTED_FILE, "w") as f:
            json.dump(list(posted_set), f, indent=4)
    except Exception as e:
        logging.error(f"Error saving {POSTED_FILE}: {e}")

matrix_room_names = {}

def match_feed(feed_dict, pattern):
    if "*" in pattern or "?" in pattern:
        matches = [name for name in feed_dict.keys() if fnmatch.fnmatch(name, pattern)]
        return matches[0] if len(matches) == 1 else (matches if matches else None)
    return pattern if pattern in feed_dict else None

def get_feeds_for_room(room):
    feeds = feed.channel_feeds.get(room)
    if feeds is not None:
        return feeds
    norm = room.lstrip("#!").lower()
    for key, val in feed.channel_feeds.items():
        if key.lstrip("#!").lower() == norm:
            return val
    return {}

class MatrixBot:
    def __init__(self, homeserver, user, password, rooms):
        self.client = AsyncClient(homeserver, user)
        self.password = password
        self.rooms = rooms  # List of Matrix room IDs from config
        self.start_time = 0  # Set after initial sync
        self.processing_enabled = False
        self.posted_articles = load_posted_articles()
        self.client.add_event_callback(self.message_callback, RoomMessageText)

    async def login(self):
        response = await self.client.login(self.password, device_name="FuzzyFeeds Bot")
        if hasattr(response, "access_token") and response.access_token:
            logging.info("Matrix login successful")
        else:
            logging.error("Matrix login failed: %s", response)
            raise Exception("Matrix login failed")

    async def join_rooms(self):
        global matrix_room_names
        for room in self.rooms:
            try:
                response = await self.client.join(room)
                if hasattr(response, "room_id"):
                    try:
                        state = await self.client.room_get_state_event(room, "m.room.name", "")
                        display_name = state.get("name", room)
                    except Exception as e:
                        logging.warning(f"Could not fetch display name for {room}: {e}")
                        display_name = room
                    matrix_room_names[room] = display_name
                    logging.info(f"Joined Matrix room: {room} (Display name: {display_name})")
                    await self.send_message(room, f"🤖 FuzzyFeeds Bot is online! Type `!help` for commands. (Room: {display_name})")
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

    async def process_command(self, room, command, sender):
        room_key = room.room_id
        parts = command.strip().split(" ", 2)
        cmd = parts[0].lower()
        if hasattr(room, "origin_server_ts") and room.origin_server_ts < self.start_time:
            logging.info(f"Ignoring old message in {room_key}: {command}")
            return
        logging.info(f"Processing command `{cmd}` from `{sender}` in `{room_key}`.")
        if cmd == "!listfeeds":
            feeds = get_feeds_for_room(room_key)
            if not feeds:
                await self.send_message(room_key, "No feeds found in this room.")
            else:
                response = "\n".join([f"`{name}` - {url}" for name, url in feeds.items()])
                await self.send_message(room_key, f"**Feeds for this room:**\n{response}")
        elif cmd == "!latest":
            if len(parts) < 2:
                await self.send_message(room_key, "Usage: !latest <feed_name>")
                return
            pattern = parts[1].strip()
            feeds = get_feeds_for_room(room_key)
            if not feeds:
                await self.send_message(room_key, "No feeds found in this room.")
                return
            matched = match_feed(feeds, pattern)
            if matched is None:
                await self.send_message(room_key, f"No feed matches '{pattern}'.")
                return
            if isinstance(matched, list):
                await self.send_message(room_key, f"Multiple feeds match '{pattern}': {', '.join(matched)}. Please be more specific.")
                return
            title, link = feed.fetch_latest_article(feeds[matched])
            if title and link:
                await self.send_message(room_key, f"Latest from {matched}: {title}\n{link}")
            else:
                await self.send_message(room_key, f"No entry available for {matched}.")
        elif cmd == "!stats":
            uptime_seconds = int(time.time() - self.start_time)
            uptime = str(datetime.timedelta(seconds=uptime_seconds))
            await self.send_message(room_key, f"Uptime: {uptime}")
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
        logging.info(f"Loaded feeds: {feed.channel_feeds}")
        await self.login()
        await self.join_rooms()
        await self.initial_sync()
        await self.sync_forever()

def send_matrix_message(room, message):
    """
    Module-level function for sending a message.
    This is used by centralized_polling.
    """
    global matrix_bot_instance
    if matrix_bot_instance is None:
        logging.error("Matrix bot instance not initialized.")
        return
    asyncio.run_coroutine_threadsafe(matrix_bot_instance.send_message(room, message), matrix_bot_instance.client.loop)

def start_matrix_bot():
    global matrix_bot_instance
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    logging.info("Starting Matrix integration...")
    bot_instance = MatrixBot(matrix_homeserver, matrix_user, matrix_password, matrix_rooms)
    matrix_bot_instance = bot_instance
    try:
        loop.run_until_complete(bot_instance.run())
    except Exception as e:
        logging.error(f"Matrix integration error: {e}")

def disable_feed_loop():
    # Internal feed checking loop is disabled in favor of centralized_polling.
    pass

if __name__ == "__main__":
    start_matrix_bot()

