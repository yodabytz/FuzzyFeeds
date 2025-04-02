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

from nio import AsyncClient, RoomMessageText

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

# Global instance and event loop for Matrix integration.
matrix_bot_instance = None
matrix_event_loop = None

matrix_room_names = {}
matrix_dm_rooms = {}

# ------------------ DM Helper Functions ------------------

async def send_matrix_dm_async(user, message):
    """Asynchronously send a direct message to the given user."""
    room_id = await get_dm_room(user)
    if room_id:
        try:
            await matrix_bot_instance.client.room_send(
                room_id,
                message_type="m.room.message",
                content={"msgtype": "m.text", "body": message}
            )
            logging.info(f"Sent DM to {user} in room {room_id}")
        except Exception as e:
            logging.error(f"Failed to send DM to {user} in room {room_id}: {e}")

def send_matrix_dm(user, message):
    """Schedule an asynchronous DM send."""
    global matrix_bot_instance, matrix_event_loop
    if matrix_bot_instance is None or matrix_event_loop is None:
        logging.error("Matrix bot not properly initialized for DM sending.")
        return
    matrix_event_loop.call_soon_threadsafe(
        lambda: asyncio.ensure_future(send_matrix_dm_async(user, message), loop=matrix_event_loop)
    )

async def update_direct_messages(room_id, user):
    try:
        # Use account_data_get (the proper method in matrix-nio 0.25.2) to retrieve DM mappings.
        dm_data = await matrix_bot_instance.client.account_data_get("m.direct")
        dm_content = dm_data.content if dm_data and hasattr(dm_data, "content") else {}
    except Exception as e:
        logging.error(f"Error retrieving m.direct for DM: {e}")
        dm_content = {}
    if user not in dm_content:
        dm_content[user] = []
    if room_id not in dm_content[user]:
        dm_content[user].append(room_id)
        try:
            await matrix_bot_instance.client.set_account_data("m.direct", dm_content)
            logging.info(f"Updated m.direct for {user} with room {room_id}")
        except Exception as e:
            logging.error(f"Error setting m.direct account data: {e}")

async def get_dm_room(user):
    global matrix_dm_rooms
    if user in matrix_dm_rooms:
        return matrix_dm_rooms[user]
    try:
        # Retrieve DM mapping using account_data_get.
        dm_data = await matrix_bot_instance.client.account_data_get("m.direct")
        if dm_data and hasattr(dm_data, "content"):
            content = dm_data.content
            if user in content and content[user]:
                room_id = content[user][0]
                matrix_dm_rooms[user] = room_id
                logging.info(f"Found existing DM room for {user}: {room_id}")
                return room_id
    except Exception as e:
        logging.error(f"Error retrieving m.direct for DM: {e}")
    
    try:
        # Create a new DM room using room_create.
        response = await matrix_bot_instance.client.room_create(
            invite=[user],
            is_direct=True,
            preset="trusted_private_chat"
        )
        # Extract the room_id from the response.
        room_id = response.room_id if hasattr(response, "room_id") else None
        if room_id and isinstance(room_id, str) and room_id.startswith("!"):
            matrix_dm_rooms[user] = room_id
            logging.info(f"Created DM room for {user}: {room_id}")
            try:
                await matrix_bot_instance.client.room_set_encryption(room_id, algorithm="m.megolm.v1.aes-sha2")
                logging.info(f"Enabled encryption in DM room {room_id}")
            except Exception as e:
                logging.error(f"Failed to enable encryption in DM room {room_id}: {e}")
            await update_direct_messages(room_id, user)
            return room_id
        else:
            logging.error(f"Failed to create DM room for {user}: {response}")
            return None
    except Exception as e:
        logging.error(f"Exception creating DM room for {user}: {e}")
        return None

# ------------------ End DM Helper Functions ------------------

class MatrixBot:
    def __init__(self, homeserver, user, password):
        self.client = AsyncClient(homeserver, user)
        self.password = password
        self.start_time = 0
        self.processing_enabled = False
        self.client.add_event_callback(self.message_callback, RoomMessageText)
        self.last_help_timestamp = {}  # For rate-limiting !help commands

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
        for room in matrix_channels:
            try:
                response = await self.client.join(room)
                if hasattr(response, "room_id"):
                    try:
                        state = await self.client.room_get_state_event(room, "m.room.name", "")
                        display_name = state.content.get("name", room) if hasattr(state, 'content') else room
                    except Exception as e:
                        logging.warning(f"Could not fetch display name for {room}: {e}")
                        display_name = room
                    matrix_room_names[room] = display_name
                    logging.info(f"Joined Matrix room: {room} (Display name: {display_name})")
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
        def matrix_send(target, msg):
            asyncio.create_task(self.send_message(target, msg))
        def matrix_send_private(user_, msg):
            asyncio.create_task(self.send_message(room_key, msg))
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
        logging.info("Starting Matrix sync loop...")
        while True:
            try:
                await self.client.sync(timeout=30000)
            except Exception as e:
                logging.error(f"Matrix sync error: {e}")
            await asyncio.sleep(1)

def get_localpart(matrix_id):
    if matrix_id.startswith("@"):
        return matrix_id.split(":", 1)[0].lstrip("@")
    return matrix_id

def start_matrix_bot():
    global matrix_bot_instance, matrix_event_loop
    matrix_event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(matrix_event_loop)
    matrix_bot_instance = MatrixBot(matrix_homeserver, matrix_user, matrix_password)
    try:
        matrix_event_loop.run_until_complete(matrix_bot_instance.login())
        matrix_event_loop.run_until_complete(matrix_bot_instance.join_rooms())
        matrix_event_loop.run_until_complete(matrix_bot_instance.initial_sync())
        logging.info("Matrix bot started successfully.")
        matrix_event_loop.create_task(matrix_bot_instance.sync_forever())
        matrix_event_loop.run_forever()
    except Exception as e:
        logging.error(f"Matrix bot failed to start: {e}")
        matrix_event_loop.stop()

# End of matrix_integration.py
