#!/usr/bin/env python3
import asyncio
import logging
import time
import os
from nio import AsyncClient, RoomMessageText

from config import (
    matrix_homeserver,
    matrix_user,
    matrix_password,
    admins,
    admin as config_admin,
    start_time
)
import feed
from channels import load_channels
from commands import handle_centralized_command

logging.basicConfig(level=logging.INFO)

# Exposed for external use if needed
matrix_room_names = {}
matrix_bot_instance = None
matrix_event_loop = None

# If your main application doesn't want this bot to poll its own feeds,
# you can call disable_feed_loop() from main.py
feed_loop_enabled = True

def disable_feed_loop():
    global feed_loop_enabled
    feed_loop_enabled = False
    logging.info("Matrix feed loop disabled.")

class MatrixBot:
    def __init__(self, homeserver: str, user: str, password: str):
        self.client = AsyncClient(homeserver, user)
        self.password = password
        self.processing_enabled = False
        self.start_time_ms = 0  # to skip old events

    async def login(self):
        response = await self.client.login(self.password, device_name="FuzzyFeeds Bot")
        if hasattr(response, "access_token") and response.access_token:
            logging.info("Matrix login successful")
        else:
            logging.error("Matrix login failed: %s", response)
            raise Exception("Matrix login failed")

    async def join_rooms(self):
        global matrix_room_names
        channels_data = load_channels()
        matrix_channels = channels_data.get("matrix_channels", [])
        for room_id in matrix_channels:
            try:
                response = await self.client.join(room_id)
                if hasattr(response, "room_id"):
                    # Attempt to get display name
                    try:
                        state = await self.client.room_get_state_event(response.room_id, "m.room.name", "")
                        if hasattr(state, 'content') and isinstance(state.content, dict):
                            display_name = state.content.get("name", room_id)
                        else:
                            display_name = room_id
                    except Exception as e:
                        logging.warning(f"Could not fetch display name for {room_id}: {e}")
                        display_name = room_id

                    matrix_room_names[room_id] = display_name
                    logging.info(f"Joined Matrix room: {room_id} (Display name: {display_name})")
                    await self.send_message(room_id, f"🤖 FuzzyFeeds Bot is online! Type `!help` for commands. (Room: {display_name})")
                else:
                    logging.error(f"Error joining room {room_id}: {response}")
            except Exception as e:
                logging.error(f"Exception joining room {room_id}: {e}")

    async def initial_sync(self):
        logging.info("Performing initial sync...")
        await self.client.sync(timeout=30000)
        await asyncio.sleep(2)
        self.start_time_ms = int(time.time() * 1000)
        self.processing_enabled = True
        logging.info(f"Initial sync complete; start_time_ms set to {self.start_time_ms}")

    async def message_callback(self, room, event):
        if not self.processing_enabled:
            return
        if event.origin_server_ts < self.start_time_ms:
            return

        # If command starts with '!'
        if event.body.startswith("!"):
            await self.process_command(room, event.body, event.sender)

    async def send_message(self, room_id: str, message: str):
        try:
            await self.client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content={"msgtype": "m.text", "body": message}
            )
        except Exception as e:
            logging.error(f"Failed to send message to {room_id}: {e}")

    async def sync_forever(self):
        while True:
            await self.client.sync(timeout=30000)
            await asyncio.sleep(1)

    async def process_command(self, room, command_text, sender):
        is_op_flag = (sender.lower() == config_admin.lower() or sender.lower() in [a.lower() for a in admins])

        # We'll define async send functions that can be used by handle_centralized_command
        async def matrix_send(target, msg):
            await self.send_message(target, msg)

        async def matrix_send_private(user, msg):
            # For simplicity, also send to the public room or implement DM logic
            await self.send_message(room.room_id, msg)

        async def matrix_send_multiline(target, msg):
            lines = msg.splitlines()
            for line in lines:
                await self.send_message(target, line)

        handle_centralized_command(
            integration="matrix",
            send_message_fn=matrix_send,
            send_private_message_fn=matrix_send_private,
            send_multiline_message_fn=matrix_send_multiline,
            user=sender,
            target=room.room_id,
            message=command_text,
            is_op_flag=is_op_flag
        )

    async def run(self):
        # Login
        await self.login()
        # Join the known matrix channels
        await self.join_rooms()
        # Initial sync so we skip old events
        await self.initial_sync()

        # Register callback for text events
        self.client.add_event_callback(self.message_callback, RoomMessageText)

        # If feed_loop_enabled is True, you could do feed polling here, but typically
        # you do it in your centralized_polling. We'll skip any internal loop here.

        # Forever sync
        await self.sync_forever()

def start_matrix_bot():
    """
    The function your main.py can call to spawn & run the matrix bot
    in a blocking manner (forever).
    """
    global matrix_bot_instance, matrix_event_loop
    matrix_bot_instance = MatrixBot(matrix_homeserver, matrix_user, matrix_password)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(matrix_bot_instance.run())
    except KeyboardInterrupt:
        logging.info("MatrixBot: Keyboard interrupt, shutting down.")
    except Exception as e:
        logging.error(f"MatrixBot encountered an error: {e}")
    finally:
        loop.close()
        logging.info("Matrix event loop closed.")

def send_message(room_id, message):
    """
    Externally callable function for 'from matrix_integration import send_message'
    so that centralized_polling or main can do matrix_integration.send_message(...).
    """
    global matrix_bot_instance
    if matrix_bot_instance is None:
        logging.error("Matrix bot instance not initialized. Cannot send message.")
        return
    try:
        # Use the matrix_bot_instance's send_message method in an async-safe way
        asyncio.get_event_loop().run_until_complete(
            matrix_bot_instance.send_message(room_id, message)
        )
    except RuntimeError:
        # If there's already a running event loop, we schedule it differently
        logging.error("Cannot run_until_complete in the existing loop. Attempting create_task approach.")
        # Attempt a fallback approach: create a task in the existing loop
        asyncio.create_task(matrix_bot_instance.send_message(room_id, message))
    except Exception as e:
        logging.error(f"Error sending Matrix message: {e}")

if __name__ == "__main__":
    # Just for direct test runs, typically your main.py calls start_matrix_bot()
    start_matrix_bot()
