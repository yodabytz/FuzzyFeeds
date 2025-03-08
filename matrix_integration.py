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
    matrix_homeserver, matrix_user, matrix_password,
    admins, admin as config_admin, admin_file, start_time
)
import feed
import persistence
import users
from commands import search_feeds, get_help
from channels import load_channels

logging.basicConfig(level=logging.INFO)

GRACE_PERIOD = 5
POSTED_FILE = "matrix_posted.json"

# Global instance and event loop for Matrix integration.
matrix_bot_instance = None
matrix_event_loop = None

# --- Per-Room Posted Feeds Storage ---
def load_posted_articles():
    if os.path.exists(POSTED_FILE):
        try:
            with open(POSTED_FILE, "r") as f:
                data = json.load(f)
                return {room: set(links) for room, links in data.items()}
        except Exception as e:
            logging.error(f"Error loading {POSTED_FILE}: {e}")
            return {}
    return {}

def save_posted_articles(posted_dict):
    try:
        serializable = {room: list(links) for room, links in posted_dict.items()}
        with open(POSTED_FILE, "w") as f:
            json.dump(serializable, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving {POSTED_FILE}: {e}")
# --- End Per-Room Posted Feeds Storage ---

matrix_room_names = {}

# --- New: Matrix DM Cache and Functions ---
matrix_dm_rooms = {}  # Cache mapping Matrix user IDs to DM room IDs

async def get_dm_room(user):
    """
    Get or create a direct-message room with the specified Matrix user.
    """
    global matrix_dm_rooms
    if user in matrix_dm_rooms:
        return matrix_dm_rooms[user]
    try:
        response = await matrix_bot_instance.client.create_room(
            invite=[user],
            is_direct=True,
            preset="trusted_private_chat"
        )
        if hasattr(response, "room_id"):
            matrix_dm_rooms[user] = response.room_id
            logging.info(f"Created DM room for {user}: {response.room_id}")
            return response.room_id
        else:
            logging.error(f"Failed to create DM room for {user}: {response}")
            return None
    except Exception as e:
        logging.error(f"Exception creating DM room for {user}: {e}")
        return None

async def send_matrix_dm_async(user, message):
    """
    Asynchronously send a direct message to a Matrix user.
    """
    room_id = await get_dm_room(user)
    if room_id:
        await matrix_bot_instance.send_message(room_id, message)

def send_matrix_dm(user, message):
    """
    Synchronously schedule sending a DM to a Matrix user.
    """
    global matrix_bot_instance, matrix_event_loop
    if matrix_bot_instance is None or matrix_event_loop is None:
        logging.error("Matrix bot not properly initialized for DM sending.")
        return
    matrix_event_loop.call_soon_threadsafe(
        lambda: asyncio.ensure_future(send_matrix_dm_async(user, message), loop=matrix_event_loop)
    )
# --- End Matrix DM Functions ---

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

def get_localpart(matrix_id):
    if matrix_id.startswith("@"):
        return matrix_id.split(":", 1)[0].lstrip("@")
    return matrix_id

class MatrixBot:
    def __init__(self, homeserver, user, password):
        self.client = AsyncClient(homeserver, user)
        self.password = password
        self.start_time = 0
        self.processing_enabled = False
        self.posted_articles = load_posted_articles()  # Dict: room_id -> set(links)
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
        # Load matrix channels from channels.json.
        channels_data = load_channels()
        matrix_channels = channels_data.get("matrix_channels", [])
        for room in matrix_channels:
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

        if cmd == "!join":
            if get_localpart(sender).lower() not in ([a.lower() for a in admins] + [config_admin.lower()]):
                await self.send_message(room_key, "Only a bot admin can use !join.")
                return
            if len(parts) < 3:
                await self.send_message(room_key, "Usage: !join <#room_alias> <adminname>")
                return
            room_alias = parts[1].strip()
            join_admin = parts[2].strip()
            try:
                response = await self.client.join(room_alias)
                if hasattr(response, "room_id"):
                    try:
                        state = await self.client.room_get_state_event(response.room_id, "m.room.name", "")
                        display_name = state.get("name", room_alias)
                    except Exception as e:
                        logging.warning(f"Could not fetch display name for {room_alias}: {e}")
                        display_name = room_alias
                    matrix_room_names[response.room_id] = display_name
                    await self.send_message(response.room_id,
                        f"🤖 FuzzyFeeds Bot joined room '{display_name}' with admin {join_admin}")
                    logging.info(f"Joined Matrix room: {response.room_id} (Display name: {display_name})")
                    try:
                        if os.path.exists(admin_file):
                            with open(admin_file, "r") as f:
                                admin_mapping = json.load(f)
                        else:
                            admin_mapping = {}
                        admin_mapping[response.room_id] = join_admin
                        with open(admin_file, "w") as f:
                            json.dump(admin_mapping, f, indent=4)
                        logging.info(f"Updated admin mapping for room {response.room_id}: {join_admin}")
                    except Exception as e:
                        logging.error(f"Failed to update admin file: {e}")
                else:
                    logging.error(f"Error joining room {room_alias}: {response}")
                    await self.send_message(room_key, f"Error joining room: {response}")
            except Exception as e:
                logging.error(f"Exception joining room {room_alias}: {e}")
                await self.send_message(room_key, f"Exception during join: {e}")
            return

        elif cmd == "!part":
            if get_localpart(sender).lower() not in ([a.lower() for a in admins] + [config_admin.lower()]):
                await self.send_message(room_key, "Only a bot admin can use !part.")
                return
            try:
                response = await self.client.room_leave(room_key)
                if response:
                    if os.path.exists(admin_file):
                        try:
                            with open(admin_file, "r") as f:
                                admin_mapping = json.load(f)
                        except Exception as e:
                            logging.error(f"Error reading admin file: {e}")
                            admin_mapping = {}
                        if room_key in admin_mapping:
                            del admin_mapping[room_key]
                        with open(admin_file, "w") as f:
                            json.dump(admin_mapping, f, indent=4)
                    logging.info(f"Left room {room_key} on admin request by {sender}.")
                else:
                    await self.send_message(room_key, "Failed to leave room.")
            except Exception as e:
                logging.error(f"Exception during !part in room {room_key}: {e}")
                await self.send_message(room_key, f"Exception during part: {e}")
            return

        else:
            # For non-special commands, define our callbacks.
            def matrix_send(target, msg):
                asyncio.create_task(self.send_message(target, msg))
            # NEW: Instead of sending private messages to the current room,
            # use our DM function so the reply goes to the sender's DM room.
            def matrix_send_private(user_, msg):
                from matrix_integration import send_matrix_dm
                send_matrix_dm(sender, msg)
            def matrix_send_multiline(target, msg):
                asyncio.create_task(self.send_message(target, msg))
            is_op_flag = (get_localpart(sender).lower() in ([a.lower() for a in admins] + [config_admin.lower()]))
            from commands import handle_centralized_command
            handle_centralized_command("matrix", matrix_send, matrix_send_private, matrix_send_multiline, sender, room_key, command, is_op_flag)

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
        # For Matrix channels, we want the message to be exactly:
        # "Feedname: Title goes here" on one line, then the link on the next.
        # We check if the message contains a line starting with "Link:".
        link = None
        for line in message.splitlines():
            if line.startswith("Link:"):
                link = line[len("Link:"):].strip()
                break
        if link:
            if room_id not in self.posted_articles:
                self.posted_articles[room_id] = set()
            if link in self.posted_articles[room_id]:
                logging.info(f"Link already posted in {room_id}: {link}")
                return
            else:
                self.posted_articles[room_id].add(link)
                save_posted_articles(self.posted_articles)
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
    global matrix_bot_instance, matrix_event_loop
    if matrix_bot_instance is None:
        logging.error("Matrix bot instance not initialized.")
        return
    if matrix_event_loop is None:
        logging.error("Matrix event loop not available.")
        return
    matrix_event_loop.call_soon_threadsafe(
        lambda: asyncio.ensure_future(matrix_bot_instance.send_message(room, message), loop=matrix_event_loop)
    )

# Export send_message as send_matrix_message for legacy compatibility.
send_message = send_matrix_message

def start_matrix_bot():
    global matrix_bot_instance, matrix_event_loop
    loop = asyncio.new_event_loop()
    matrix_event_loop = loop
    asyncio.set_event_loop(loop)
    logging.info("Starting Matrix integration...")
    from channels import load_channels
    channels_data = load_channels()
    matrix_channels = channels_data.get("matrix_channels", [])
    bot_instance = MatrixBot(matrix_homeserver, matrix_user, matrix_password)
    matrix_bot_instance = bot_instance
    # Pre-load feeds for Matrix channels: ensure feed.channel_feeds has keys for each matrix channel.
    for room in matrix_channels:
        if room not in feed.channel_feeds:
            feed.channel_feeds[room] = {}  # Start with empty feeds
    try:
        loop.run_until_complete(bot_instance.run())
    except Exception as e:
        logging.error(f"Matrix integration error: {e}")

def disable_feed_loop():
    pass

if __name__ == "__main__":
    start_matrix_bot()
