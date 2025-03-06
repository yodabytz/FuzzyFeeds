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

# Global instance and event loop for Matrix integration.
matrix_bot_instance = None
matrix_event_loop = None

# --- Per-Room Posted Feeds Storage ---
def load_posted_articles():
    """Load a dictionary mapping room_id -> set(links) from POSTED_FILE."""
    if os.path.exists(POSTED_FILE):
        try:
            with open(POSTED_FILE, "r") as f:
                data = json.load(f)
                # Convert each list to a set.
                return {room: set(links) for room, links in data.items()}
        except Exception as e:
            logging.error(f"Error loading {POSTED_FILE}: {e}")
            return {}
    return {}

def save_posted_articles(posted_dict):
    """Save the dictionary mapping room_id -> set(links) to POSTED_FILE."""
    try:
        serializable = {room: list(links) for room, links in posted_dict.items()}
        with open(POSTED_FILE, "w") as f:
            json.dump(serializable, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving {POSTED_FILE}: {e}")
# --- End Per-Room Posted Feeds Storage ---

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

def get_localpart(matrix_id):
    """Extract the local part from a Matrix user ID (e.g. '@locoghost:matrix.org' -> 'locoghost')."""
    if matrix_id.startswith("@"):
        return matrix_id.split(":", 1)[0].lstrip("@")
    return matrix_id

class MatrixBot:
    def __init__(self, homeserver, user, password, rooms):
        self.client = AsyncClient(homeserver, user)
        self.password = password
        self.rooms = rooms  # List of Matrix room IDs from config.
        self.start_time = 0  # Set after initial sync.
        self.processing_enabled = False
        # Load per-room posted articles.
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
                    # Announce join in the room.
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
        # Ignore old messages.
        if hasattr(room, "origin_server_ts") and room.origin_server_ts < self.start_time:
            logging.info(f"Ignoring old message in {room_key}: {command}")
            return

        logging.info(f"Processing command `{cmd}` from `{sender}` in `{room_key}`.")

        # Handle admin-only commands locally.
        if cmd == "!join":
            # Only allow if sender's local part is in admins or equals primary admin.
            if get_localpart(sender).lower() not in ([a.lower() for a in admins] + [config_admin.lower()]):
                await self.send_message(room_key, "Only a bot admin can use !join.")
                return
            if len(parts) < 3:
                await self.send_message(room_key, "Usage: !join <#room_alias> <adminname>")
                return
            room_alias = parts[1].strip()
            join_admin = parts[2].strip()
            try:
                join_response = await self.client.join(room_alias)
                if hasattr(join_response, "room_id"):
                    try:
                        state = await self.client.room_get_state_event(join_response.room_id, "m.room.name", "")
                        display_name = state.get("name", room_alias)
                    except Exception as e:
                        logging.warning(f"Could not fetch display name for {room_alias}: {e}")
                        display_name = room_alias
                    matrix_room_names[join_response.room_id] = display_name
                    await self.send_message(join_response.room_id,
                        f"🤖 FuzzyFeeds Bot joined room '{display_name}' with admin {join_admin}")
                    logging.info(f"Joined Matrix room: {join_response.room_id} (Display name: {display_name})")
                    # Update admin.json with the new admin mapping.
                    try:
                        if os.path.exists(admin_file):
                            with open(admin_file, "r") as f:
                                admin_mapping = json.load(f)
                        else:
                            admin_mapping = {}
                        admin_mapping[join_response.room_id] = join_admin
                        with open(admin_file, "w") as f:
                            json.dump(admin_mapping, f, indent=4)
                        logging.info(f"Updated admin mapping for room {join_response.room_id}: {join_admin}")
                    except Exception as e:
                        logging.error(f"Failed to update admin file: {e}")
                else:
                    logging.error(f"Error joining room {room_alias}: {join_response}")
                    await self.send_message(room_key, f"Error joining room: {join_response}")
            except Exception as e:
                logging.error(f"Exception joining room {room_alias}: {e}")
                await self.send_message(room_key, f"Exception during join: {e}")
            return

        elif cmd == "!part":
            # !part: Admin-only command to leave the room.
            if get_localpart(sender).lower() not in ([a.lower() for a in admins] + [config_admin.lower()]):
                await self.send_message(room_key, "Only a bot admin can use !part.")
                return
            try:
                leave_response = await self.client.room_leave(room_key)
                if leave_response:
                    # Optionally update admin.json to remove this room.
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

        # Delegate any other commands to the centralized command handler.
        else:
            # Define wrapper functions for sending messages.
            def matrix_send(target, msg):
                asyncio.create_task(self.send_message(target, msg))
            def matrix_send_private(user_, msg):
                asyncio.create_task(self.send_message(room_key, msg))
            def matrix_send_multiline(target, msg):
                asyncio.create_task(self.send_message(target, msg))
            # Determine operator flag from sender's local part.
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
        # If this is a feed link announcement, check per-room posted links.
        if message.startswith("Link:"):
            link = message[len("Link:"):].strip()
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
    """
    Module-level function for sending a Matrix message.
    This is used by centralized_polling.
    """
    global matrix_bot_instance, matrix_event_loop
    if matrix_bot_instance is None:
        logging.error("Matrix bot instance not initialized.")
        return
    if matrix_event_loop is None:
        logging.error("Matrix event loop not available.")
        return
    # Schedule the send_message coroutine on the stored event loop.
    matrix_event_loop.call_soon_threadsafe(asyncio.create_task, matrix_bot_instance.send_message(room, message))

# Export send_message as an alias for backward compatibility.
send_message = send_matrix_message

def start_matrix_bot():
    global matrix_bot_instance, matrix_event_loop
    loop = asyncio.new_event_loop()
    matrix_event_loop = loop  # Save the event loop globally for use by send_message
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
