#!/usr/bin/env python3
import asyncio
import logging
import time
from nio import AsyncClient, RoomMessageText

from config import (
    matrix_homeserver,
    matrix_user,
    matrix_password,
    admins,
    admin as config_admin,
    start_time
)
from channels import load_channels
from commands import handle_centralized_command

logging.basicConfig(level=logging.INFO)

###############################################################################
# Variables
###############################################################################
# We store { room_id -> display_name } so the dashboard can show real names
# but we still send messages using the actual !room_id
matrix_room_display_names = {}

# In some parts of your code, you might see "matrix_room_names". We'll keep
# that or rename it to matrix_room_display_names for clarity. 
# If your code references matrix_room_names, you can just alias it:
matrix_room_names = matrix_room_display_names

# The main bot instance
matrix_bot_instance = None

# By default, we won't do internal feed polling if you have a centralized polling approach
feed_loop_enabled = True

###############################################################################
# Functions to match your existing usage
###############################################################################

def disable_feed_loop():
    """If the main code wants to skip an internal feed loop, we set this to False."""
    global feed_loop_enabled
    feed_loop_enabled = False
    logging.info("Matrix feed loop disabled.")

def start_matrix_bot():
    """
    Called by your main.py to run the MatrixBot in a blocking manner.
    """
    global matrix_bot_instance
    bot = MatrixBot(matrix_homeserver, matrix_user, matrix_password)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(bot.run())
    except KeyboardInterrupt:
        logging.info("MatrixBot shutting down (keyboard interrupt).")
    except Exception as e:
        logging.error(f"MatrixBot encountered an error: {e}")
    finally:
        loop.close()
        logging.info("Matrix event loop closed.")
    matrix_bot_instance = None

def send_message(room_id: str, message: str):
    """
    Called by centralized_polling or main.py to send a message to a Matrix room.
    We queue the operation on the bot’s event loop, so we don't get 'no current event loop' errors.
    """
    global matrix_bot_instance
    if not matrix_bot_instance:
        logging.error("Matrix bot instance not initialized. Cannot send message.")
        return

    loop = matrix_bot_instance.loop
    if not loop:
        logging.error("Matrix bot event loop not found. Cannot queue message.")
        return

    def schedule_send():
        asyncio.create_task(matrix_bot_instance.send_message(room_id, message))

    loop.call_soon_threadsafe(schedule_send)

###############################################################################
# MatrixBot class
###############################################################################
class MatrixBot:
    def __init__(self, homeserver, user, password):
        self.client = AsyncClient(homeserver, user)
        self.password = password
        self.processing_enabled = False
        self.start_time_ms = 0
        self.loop = None  # We'll store the event loop that runs this bot

    async def login(self):
        resp = await self.client.login(self.password, device_name="FuzzyFeeds Bot")
        if hasattr(resp, "access_token") and resp.access_token:
            logging.info("Matrix login successful")
        else:
            logging.error(f"Matrix login failed: {resp}")
            raise Exception("Matrix login failed")

    async def join_rooms(self):
        """
        Joins all Matrix channels from channels.json (under 'matrix_channels').
        We store the real room name in matrix_room_display_names for dashboard usage.
        """
        global matrix_room_display_names
        channels_data = load_channels()
        matrix_channels = channels_data.get("matrix_channels", [])
        for room_id in matrix_channels:
            try:
                resp = await self.client.join(room_id)
                if hasattr(resp, "room_id"):
                    # Attempt to fetch a display name
                    display_name = room_id  # fallback if we can't fetch
                    try:
                        state = await self.client.room_get_state_event(resp.room_id, "m.room.name", "")
                        if hasattr(state, 'content') and isinstance(state.content, dict):
                            # If there's a name in the content, use it
                            fetched_name = state.content.get("name")
                            if fetched_name:
                                display_name = fetched_name
                    except Exception as e:
                        logging.warning(f"Could not fetch display name for {room_id}: {e}")

                    matrix_room_display_names[room_id] = display_name
                    logging.info(f"Joined Matrix room: {room_id} (Display name: {display_name})")

                    # Announce presence
                    await self.send_message(
                        room_id,
                        f"🤖 FuzzyFeeds Bot is online! Type `!help` for commands. (Room: {display_name})"
                    )
                else:
                    logging.error(f"Error joining room {room_id}: {resp}")
            except Exception as e:
                logging.error(f"Exception joining room {room_id}: {e}")

    async def initial_sync(self):
        """
        Perform an initial sync so we ignore old messages.
        """
        logging.info("Performing initial sync...")
        await self.client.sync(timeout=30000)
        # small delay
        await asyncio.sleep(2)
        self.start_time_ms = int(time.time() * 1000)
        self.processing_enabled = True
        logging.info(f"Initial sync complete; start_time_ms={self.start_time_ms}")

    async def send_message(self, room_id: str, message: str):
        """
        Actually sends a message to the matrix room. 
        If called from outside, use the top-level send_message(...) function
        so we queue it onto the correct event loop.
        """
        try:
            await self.client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content={"msgtype": "m.text", "body": message}
            )
        except Exception as e:
            logging.error(f"Failed to send message to {room_id}: {e}")

    async def message_callback(self, room, event):
        """
        Called for each new message in a room. If we see an exclamation command, we handle it.
        """
        if not self.processing_enabled:
            return
        if event.origin_server_ts < self.start_time_ms:
            return

        # If it's a command
        if event.body.startswith("!"):
            await self.process_command(room, event.body, event.sender)

    async def process_command(self, room, cmd_text, sender):
        """
        If user typed something like "!help", we pass it to the centralized command handler.
        """
        is_op_flag = (sender.lower() == config_admin.lower() or sender.lower() in [a.lower() for a in admins])

        async def matrix_send(target, msg):
            await self.send_message(target, msg)

        async def matrix_send_private(user, msg):
            await self.send_message(room.room_id, msg)

        async def matrix_send_multiline(target, msg):
            for line in msg.splitlines():
                await self.send_message(target, line)

        handle_centralized_command(
            integration="matrix",
            send_message_fn=matrix_send,
            send_private_message_fn=matrix_send_private,
            send_multiline_message_fn=matrix_send_multiline,
            user=sender,
            target=room.room_id,
            message=cmd_text,
            is_op_flag=is_op_flag
        )

    async def sync_forever(self):
        """Continuously sync so the bot remains responsive."""
        while True:
            await self.client.sync(timeout=30000)
            await asyncio.sleep(1)

    async def run(self):
        """
        Main lifecycle: set up the event loop reference, log in, join rooms,
        do an initial sync, register callbacks, and loop forever.
        """
        # store the loop so we can queue tasks from other threads
        self.loop = asyncio.get_event_loop()

        await self.login()
        await self.join_rooms()
        await self.initial_sync()

        self.client.add_event_callback(self.message_callback, RoomMessageText)

        # If feed_loop_enabled, we could poll feeds here—but typically you do that externally
        await self.sync_forever()

###############################################################################
# End of file
###############################################################################
