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

logging.basicConfig(level=logging.INFO)

class MatrixBot:
    def __init__(self, homeserver, user, password, rooms):
        self.client = AsyncClient(homeserver, user)
        self.password = password
        self.rooms = rooms
        self.start_time = 0
        self.processing_enabled = False  # Enabled after sync
        self.client.add_event_callback(self.message_callback, RoomMessageText)

    async def login(self):
        response = await self.client.login(self.password)
        if isinstance(response, LoginResponse):
            logging.info("Matrix login successful")
        else:
            logging.error("Matrix login failed: %s", response)
            raise Exception("Matrix login failed")

    async def join_rooms(self):
        for room in self.rooms:
            try:
                response = await self.client.join(room)
                if hasattr(response, "room_id"):
                    logging.info("Joined Matrix room: %s", room)
                else:
                    logging.error("Error joining room %s: %s", room, response)
            except Exception as e:
                logging.error("Exception joining room %s: %s", room, e)

    async def initial_sync(self):
        await self.client.sync(timeout=30000)
        await asyncio.sleep(5)
        self.start_time = int(time.time() * 1000)
        self.processing_enabled = True
        logging.info("Initial sync complete; start_time set to %s", self.start_time)

    async def check_feeds_for_updates(self):
        while True:
            logging.info("Checking feeds for Matrix updates...")
            new_articles = []
            
            def send_message_to_matrix(room, msg):
                new_articles.append((room, msg))
                
            feed.check_feeds(send_message_to_matrix)
            
            for room, msg in new_articles:
                await self.send_message(room, msg)
            
            await asyncio.sleep(300)

    async def send_message(self, room_id, message):
        await self.client.room_send(
            room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": message}
        )

    async def message_callback(self, room, event):
        if hasattr(event, "origin_server_ts"):
            if event.origin_server_ts < self.start_time:
                return
        if event.body.startswith("!"):
            logging.info("Matrix command received in room %s: %s", room.room_id, event.body)
            await self.process_command(room, event.body, event.sender)

    async def process_command(self, room, command, sender):
        room_key = room.room_id
        parts = command.strip().split(" ", 2)
        cmd = parts[0].lower()
        sender_local = sender[1:].split(":")[0].lower()
        
        if cmd == "!listfeeds":
            if room_key in feed.channel_feeds and feed.channel_feeds[room_key]:
                lines = [f"{name}: {url}" for name, url in feed.channel_feeds[room_key].items()]
                await self.send_message(room_key, "\n".join(lines))
            else:
                await self.send_message(room_key, "No feeds found in this room.")
        elif cmd == "!latest":
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
            title, link = feed.fetch_latest_article(feed.channel_feeds[room_key][matched])
            if title and link:
                await self.send_message(room_key, f"Latest from {matched}: {title}\n{link}")
            else:
                await self.send_message(room_key, f"No entry available for {matched}.")
        else:
            await self.send_message(room_key, "Unknown command. Use !help for a list.")

    async def sync_forever(self):
        while True:
            await self.client.sync(timeout=30000)
            await asyncio.sleep(1)

    async def run(self):
        await self.login()
        await self.join_rooms()
        await self.initial_sync()
        await asyncio.gather(
            self.check_feeds_for_updates(),
            self.sync_forever()
        )

def start_matrix_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    logging.info("Starting Matrix integration...")
    bot = MatrixBot(matrix_homeserver, matrix_user, matrix_password, matrix_rooms)
    try:
        loop.run_until_complete(bot.run())
    except Exception as e:
        logging.error("Matrix integration error: %s", e)

