#!/usr/bin/env python3
"""
centralized_polling.py

This module implements centralized polling for RSS/Atom feeds for all integrations:
IRC, Matrix, and Discord. It uses the feed data from feed.py and, at configurable
intervals, checks each feed for new entries. When a new entry is found, it uses the
provided callback functions to send messages to the appropriate integration channel/room.
"""

import aiohttp
import asyncio
import heapq
import time
import logging
import feedparser
import datetime
import threading
from io import BytesIO

import feed
from config import default_interval, BATCH_SIZE, BATCH_DELAY

logging.basicConfig(level=logging.INFO)

# Global variable for initial setup
script_start_time = time.time()

async def fetch_feed_conditional(session, url, last_modified=None, etag=None):
    headers = {}
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    if etag:
        headers["If-None-Match"] = etag
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status == 200:
                content = await response.read()
                parsed = feedparser.parse(BytesIO(content))
                if parsed.bozo:
                    logging.warning(f"Error parsing feed at {url}: {parsed.bozo_exception}")
                    return None
                return {
                    "feed": parsed,
                    "last_modified": response.headers.get("Last-Modified"),
                    "etag": response.headers.get("ETag")
                }
            return None
    except Exception as e:
        logging.error(f"Error fetching {url}: {e}")
        return None

def send_to_platform(chan, msg, irc_send, matrix_send, discord_send):
    logging.info(f"Routing message to platform: {chan}")
    if chan.startswith("!"):
        logging.info(f"Sending to Matrix: {chan}")
        matrix_send(chan, msg)
    elif str(chan).isdigit():
        logging.info(f"Sending to Discord: {chan}")
        discord_send(chan, msg)
    else:
        logging.info(f"Sending to IRC: {chan}")
        irc_send(chan, msg)

class FeedScheduler:
    def __init__(self):
        self.queue = []  # (next_check_time, channel)
        self.lock = threading.Lock()

    def add_channel(self, channel, interval):
        with self.lock:
            next_check = time.time() + interval
            heapq.heappush(self.queue, (next_check, channel))

    def get_next(self):
        with self.lock:
            if not self.queue:
                return None, None
            return heapq.heappop(self.queue)

    def reschedule(self, channel, interval):
        self.add_channel(channel, interval)

async def process_channel(chan, feeds_to_check, irc_send, matrix_send, discord_send):
    feed.load_feeds()
    current_time = time.time()
    last_check = feed.last_check_times.get(chan, script_start_time)
    interval = feed.channel_intervals.get(chan, default_interval)
    if current_time - last_check < interval:
        logging.info(f"Skipping {chan}: too soon since last check")
        return 0

    async with aiohttp.ClientSession() as session:
        tasks = [fetch_feed_conditional(session, url, *feed.feed_metadata.get(url, {}).values()) 
                 for url in feeds_to_check.values()]
        results = await asyncio.gather(*tasks)

    updates = []
    for (feed_name, feed_url), result in zip(feeds_to_check.items(), results):
        if result and result["feed"]:
            entry = result["feed"].entries[0]
            published_time = None
            if entry.get("published_parsed"):
                published_time = time.mktime(entry.published_parsed)
            elif entry.get("updated_parsed"):
                published_time = time.mktime(entry.updated_parsed)
            if published_time and published_time > last_check:
                link = entry.get("link", "").strip()
                if link and not feed.is_link_posted(chan, link):
                    title = entry.get("title", "No Title").strip()
                    updates.append((feed_name, title, link))
                    feed.mark_link_posted(chan, link)
                    feed.feed_metadata[feed_url] = {"last_modified": result["last_modified"], "etag": result["etag"]}

    if not updates:
        logging.info(f"No new feeds found in {chan}.")
        feed.last_check_times[chan] = current_time
        return 0

    batch_size = feed.channel_settings.get(chan, {}).get("batch_size", BATCH_SIZE)
    if batch_size <= 0:
        batch_size = 1
    batches = [updates[i:i + batch_size] for i in range(0, len(updates), batch_size)]
    for i, batch in enumerate(batches):
        msg = "New Feeds" + (" (continued)" if i > 0 else "") + ":\n"
        for j, (feed_name, title, link) in enumerate(batch, 1 + i * batch_size):
            msg += f"{j}. {feed_name}: {title} - {link}\n"
        send_to_platform(chan, msg.strip(), irc_send, matrix_send, discord_send)
        if i < len(batches) - 1:
            await asyncio.sleep(BATCH_DELAY)

    feed.last_check_times[chan] = current_time
    logging.info(f"Posted {len(updates)} new feeds in {chan}.")
    return len(updates)

async def start_polling(irc_send, matrix_send, discord_send, poll_interval=default_interval):
    logging.info("Centralized async polling started.")
    scheduler = FeedScheduler()
    feed.load_feeds()  # Initial load for scheduler setup
    for chan in feed.channel_feeds.keys():
        if not hasattr(feed, 'last_check_times') or feed.last_check_times is None:
            feed.last_check_times = {}
        feed.last_check_times.setdefault(chan, script_start_time)
        scheduler.add_channel(chan, feed.channel_intervals.get(chan, poll_interval))

    while True:
        next_time, chan = scheduler.get_next()
        if not chan:
            await asyncio.sleep(1)
            continue
        current_time = time.time()
        if current_time < next_time:
            await asyncio.sleep(next_time - current_time)

        feeds_to_check = feed.channel_feeds.get(chan, {})
        if not feeds_to_check:
            logging.warning(f"No feed dictionary found for channel {chan}; skipping.")
            scheduler.reschedule(chan, feed.channel_intervals.get(chan, poll_interval))
            continue

        new_feed_count = await process_channel(chan, feeds_to_check, irc_send, matrix_send, discord_send)
        scheduler.reschedule(chan, feed.channel_intervals.get(chan, poll_interval))
        logging.info(f"Finished checking {chan}. Next check in {feed.channel_intervals.get(chan, poll_interval)} seconds.")

if __name__ == "__main__":
    def test_irc_send(channel, message):
        if "|" in channel:
            parts = channel.split("|", 1)
            actual_channel = parts[1]
            print(f"[Secondary IRC] Channel {actual_channel}:")
        else:
            print(f"[Primary IRC] Channel {channel}:")
        for line in message.split('\n'):
            print(line)

    def test_matrix_send(room, message):
        print(f"[Matrix] Room {room}: {message}")

    def test_discord_send(channel, message):
        print(f"[Discord] Channel {channel}: {message}")

    asyncio.run(start_polling(test_irc_send, test_matrix_send, test_discord_send, poll_interval=300))
