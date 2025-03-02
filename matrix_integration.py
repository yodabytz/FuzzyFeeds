#!/usr/bin/env python3
import asyncio
import logging
import time
from nio import AsyncClient, LoginResponse, RoomMessageText
from config import matrix_homeserver, matrix_user, matrix_password, matrix_rooms, admins
import feed
import persistence
import users
from commands import search_feeds, get_help
import fnmatch
import requests
import datetime
import config

logging.basicConfig(level=logging.INFO)

# GRACE_PERIOD (in seconds) after initial sync before processing commands.
GRACE_PERIOD = 5

def match_feed(feed_dict, pattern):
    """Matches a feed name with a pattern, supporting wildcards."""
    if "*" in pattern or "?" in pattern:
        matches = [name for name in feed_dict.keys() if fnmatch.fnmatch(name, pattern)]
        return matches[0] if len(matches) == 1 else (matches if matches else None)
    return pattern if pattern in feed_dict else None

class MatrixBot:
    def __init__(self, homeserver, user, password, rooms):
        self.client = AsyncClient(homeserver, user)
        self.password = password
        self.rooms = rooms
        self.start_time = 0  # Timestamp of when the bot fully syncs
        self.processing_enabled = False  # Will be enabled after full sync
        self.client.add_event_callback(self.message_callback, RoomMessageText)

    async def login(self):
        response = await self.client.login(self.password)
        if isinstance(response, LoginResponse):
            logging.info("Matrix login successful")
        else:
            logging.error("Matrix login failed: %s", response)
            raise Exception("Matrix login failed")

    async def join_rooms(self):
        """Ensures the bot joins all required Matrix rooms."""
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
        """Performs the initial sync and sets a timestamp for new messages."""
        logging.info("Performing initial sync...")
        await self.client.sync(timeout=30000)
        await asyncio.sleep(GRACE_PERIOD)  # Wait before processing messages
        self.start_time = int(time.time() * 1000)  # Set timestamp for new messages
        self.processing_enabled = True
        logging.info("Initial sync complete; start_time set to %s", self.start_time)

    async def check_feeds_loop(self):
        """Background loop to check for new feed articles for Matrix integration."""
        while True:
            def send_matrix_message(channel, msg):
                if channel in self.rooms:
                    asyncio.create_task(self.send_message(channel, msg))
            try:
                feed.check_feeds(send_matrix_message)
            except Exception as e:
                logging.error(f"Error in Matrix feed checker: {e}")
            await asyncio.sleep(300)  # Check every 5 minutes

    async def process_command(self, room, command, sender):
        """Processes Matrix bot commands."""
        room_key = room.room_id
        parts = command.strip().split(" ", 2)
        cmd = parts[0].lower()

        # Ignore messages before sync.
        if hasattr(room, "origin_server_ts"):
            if room.origin_server_ts < self.start_time:
                logging.info(f"Ignoring old message in {room_key}: {command}")
                return

        logging.info(f"Processing command `{cmd}` from `{sender}` in `{room_key}`.")

        if cmd == "!listfeeds":
            if room_key not in feed.channel_feeds or not feed.channel_feeds[room_key]:
                await self.send_message(room_key, "No feeds found in this room.")
                return
            response = "\n".join([f"`{name}` - {url}" for name, url in feed.channel_feeds[room_key].items()])
            await self.send_message(room_key, f"**Feeds for this room:**\n{response}")
            return

        if cmd == "!latest":
            if len(parts) < 2:
                await self.send_message(room_key, "Usage: !latest <feed_name>")
                return
            pattern = parts[1].strip()
            if room_key not in feed.channel_feeds:
                await self.send_message(room_key, "No feeds found in this room.")
                return
            matched = match_feed(feed.channel_feeds[room_key], pattern)
            if matched is None:
                await self.send_message(room_key, f"No feed matches '{pattern}'.")
                return
            if isinstance(matched, list):
                await self.send_message(room_key, f"Multiple feeds match '{pattern}': {', '.join(matched)}. Please be more specific.")
                return
            feed_name = matched
            title, link = feed.fetch_latest_article(feed.channel_feeds[room_key][feed_name])
            if title and link:
                await self.send_message(room_key, f"Latest from {feed_name}: {title}\n{link}")
            else:
                await self.send_message(room_key, f"No entry available for {feed_name}.")
            return

        if cmd == "!stats":
            uptime_seconds = int(time.time() - self.start_time)
            uptime = str(datetime.timedelta(seconds=uptime_seconds))
            if sender.lower() == config.admin.lower() or sender.lower() in [a.lower() for a in config.admins]:
                # Global stats for Matrix integration.
                matrix_feed_count = sum(len(feed.channel_feeds[k]) for k in feed.channel_feeds if k.startswith("!"))
                response = (f"Global Uptime: {uptime}\n"
                            f"Matrix Global Feeds: {matrix_feed_count} across {len(feed.channel_feeds)} rooms\n"
                            f"User Subscriptions: {sum(len(subs) for subs in feed.subscriptions.values())} total (from {len(feed.subscriptions)} users)")
            else:
                num_channel_feeds = len(feed.channel_feeds[room_key]) if room_key in feed.channel_feeds else 0
                response = (f"Uptime: {uptime}\n"
                            f"Room Feeds: {num_channel_feeds}")
            await self.send_message(room_key, response)
            return

        # (Other commands can be added here as needed.)

    async def message_callback(self, room, event):
        """Handles new messages and ensures commands are processed."""
        if not self.processing_enabled:
            return

        if hasattr(event, "origin_server_ts"):
            if event.origin_server_ts < self.start_time:
                logging.info(f"Ignoring old message in {room.room_id}: {event.body}")
                return

        if event.body.startswith("!"):
            logging.info(f"Matrix command received in {room.room_id}: {event.body}")
            await self.process_command(room, event.body, event.sender)

    async def send_message(self, room_id, message):
        """Sends a message to a Matrix room and logs errors if sending fails."""
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
        """Runs the Matrix sync loop indefinitely."""
        while True:
            await self.client.sync(timeout=30000)
            await asyncio.sleep(1)

    async def run(self):
        """Starts the Matrix bot, ensuring proper login, room joins, and feed checking."""
        feed.load_feeds()
        users.load_users()
        await self.login()
        await self.join_rooms()
        await self.initial_sync()
        asyncio.create_task(self.check_feeds_loop())
        await self.sync_forever()

def start_matrix_bot():
    """Starts the Matrix bot in an asyncio event loop."""
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

