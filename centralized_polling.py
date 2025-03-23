#!/usr/bin/env python3
"""
centralized_polling.py

This module implements centralized polling for RSS/Atom feeds for all integrations:
IRC, Matrix, and Discord. It uses the feed data from feed.py and, at configurable
intervals, checks each feed for new entries. When new entries are found, it sends
the Title and Link to the appropriate integration.
"""

import aiohttp
import asyncio
import heapq
import time
import logging
import feedparser
import datetime
from io import BytesIO
import threading

import feed
from config import default_interval, BATCH_SIZE, BATCH_DELAY

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# Global variable for initial setup – only loaded once.
script_start_time = time.time()

# Helper function to get the entry timestamp.
def get_entry_time(entry):
    if entry.get("published_parsed"):
        return time.mktime(entry.published_parsed)
    elif entry.get("updated_parsed"):
        return time.mktime(entry.updated_parsed)
    else:
        return None

async def fetch_feed_conditional(session, url, last_modified=None, etag=None):
    headers = {}
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    if etag:
        headers["If-None-Match"] = etag
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status == 304:
                return None
            elif response.status == 200:
                content = await response.read()
                parsed = feedparser.parse(BytesIO(content))
                if parsed.bozo:
                    logging.warning(f"Error parsing feed at {url}")
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
    if chan.startswith("!"):
        matrix_send(chan, msg)
    elif str(chan).isdigit():
        discord_send(chan, msg)
    else:
        irc_send(chan, msg)

class FeedScheduler:
    def __init__(self):
        self.queue = []  # Each item: (next_check_time, channel)
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
    current_time = time.time()
    last_check = feed.last_check_times.get(chan, script_start_time)
    interval = feed.channel_intervals.get(chan, default_interval)
    if current_time - last_check < interval:
        return 0

    async with aiohttp.ClientSession() as session:
        tasks = [
            fetch_feed_conditional(session, url, *feed.feed_metadata.get(url, {}).values())
            for url in feeds_to_check.values()
        ]
        results = await asyncio.gather(*tasks)

    updates = []
    for (feed_name, feed_url), result in zip(feeds_to_check.items(), results):
        if result and result["feed"]:
            entries = result["feed"].entries
            valid_entries = [e for e in entries if get_entry_time(e) is not None]
            sorted_entries = sorted(valid_entries, key=get_entry_time)
            for entry in sorted_entries:
                entry_time = get_entry_time(entry)
                if entry_time and entry_time > last_check:
                    link = entry.get("link", "").strip()
                    if link and not feed.is_link_posted(chan, link):
                        title = entry.get("title", "No Title").strip()
                        updates.append((feed_name, title, link))
                        feed.mark_link_posted(chan, link)
                        feed.feed_metadata[feed_url] = {
                            "last_modified": result["last_modified"],
                            "etag": result["etag"]
                        }
    if not updates:
        logging.info(f"No new feeds found in {chan}.")
        feed.last_check_times[chan] = current_time
        return 0

    # Post updates in batches.
    batch_size = feed.channel_settings.get(chan, {}).get("batch_size", BATCH_SIZE)
    if batch_size <= 0:
        batch_size = 1
    batches = [updates[i:i + batch_size] for i in range(0, len(updates), batch_size)]
    for batch in batches:
        for feed_name, title, link in batch:
            send_to_platform(chan, f"New Feed from {feed_name}: {title}", irc_send, matrix_send, discord_send)
            await asyncio.sleep(0.5)
            send_to_platform(chan, f"Link: {link}", irc_send, matrix_send, discord_send)
            await asyncio.sleep(0.5)
    feed.last_check_times[chan] = current_time
    logging.info(f"Posted {len(updates)} new feed entr{'y' if len(updates)==1 else 'ies'} in {chan}.")
    return len(updates)

async def start_polling(irc_send, matrix_send, discord_send, poll_interval=default_interval):
    logging.info("Centralized async polling started.")
    scheduler = FeedScheduler()
    # Do not reload feeds here so that posted_links remains intact.
    for chan in feed.channel_feeds.keys():
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
        await process_channel(chan, feeds_to_check, irc_send, matrix_send, discord_send)
        scheduler.reschedule(chan, feed.channel_intervals.get(chan, poll_interval))
        logging.info(f"Finished checking {chan}. Next check in {feed.channel_intervals.get(chan, poll_interval)} seconds.")

if __name__ == "__main__":
    def test_irc_send(channel, message):
        print(f"[Test IRC] Channel {channel}: {message}")

    def test_matrix_send(room, message):
        print(f"[Test Matrix] Room {room}: {message}")

    def test_discord_send(channel, message):
        print(f"[Test Discord] Channel {channel}: {message}")

    asyncio.run(start_polling(test_irc_send, test_matrix_send, test_discord_send, poll_interval=300))
