#!/usr/bin/env python3
"""
centralized_polling.py

Centralized RSS/Atom feed polling for IRC, Matrix, and Discord.
Posts new feed entries with 'Title:' and 'Link:' to the correct channels.
"""

import aiohttp
import asyncio
import heapq
import time
import logging
import feedparser
import threading
from io import BytesIO

import feed
from config import default_interval, BATCH_SIZE, BATCH_DELAY

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

script_start_time = time.time()

async def fetch_feed(session, url, last_modified=None, etag=None):
    headers = {"If-Modified-Since": last_modified, "If-None-Match": etag} if last_modified or etag else {}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status == 304:
                return None
            elif response.status == 200:
                content = await response.read()
                parsed = feedparser.parse(BytesIO(content))
                if parsed.bozo:
                    logging.warning(f"Feed parse error at {url}: {parsed.bozo_exception}")
                    return None
                return {
                    "feed": parsed,
                    "last_modified": response.headers.get("Last-Modified"),
                    "etag": response.headers.get("ETag")
                }
            logging.debug(f"Feed fetch status {response.status} for {url}")
            return None
    except Exception as e:
        logging.error(f"Fetch error for {url}: {e}")
        return None

def send_to_platform(channel, message, irc_send, matrix_send, discord_send):
    logging.info(f"Routing to {channel}: {message}")
    try:
        if channel.startswith("!"):
            matrix_send(channel, message)
        elif str(channel).isdigit():
            discord_send(channel, message)
        else:
            irc_send(channel, message)
    except Exception as e:
        logging.error(f"Send error for {channel}: {e}")

class FeedScheduler:
    def __init__(self):
        self.queue = []  # (next_check_time, channel)
        self.lock = threading.Lock()

    def add_channel(self, channel, interval):
        with self.lock:
            heapq.heappush(self.queue, (time.time() + interval, channel))

    def get_next(self):
        with self.lock:
            return heapq.heappop(self.queue) if self.queue else (None, None)

    def reschedule(self, channel, interval):
        self.add_channel(channel, interval)

async def process_channel(channel, feeds, irc_send, matrix_send, discord_send):
    current_time = time.time()
    last_check = feed.last_check_times.get(channel, script_start_time)
    interval = feed.channel_intervals.get(channel, default_interval)
    if current_time - last_check < interval:
        logging.debug(f"Skipping {channel}: too soon")
        return 0

    logging.info(f"Polling {channel}: {feeds}")
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_feed(session, url, *feed.feed_metadata.get(url, {}).values()) for url in feeds.values()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    updates = []
    for (name, url), result in zip(feeds.items(), results):
        if isinstance(result, Exception):
            logging.error(f"Feed fetch failed for {url}: {result}")
            continue
        if result and result["feed"] and result["feed"].entries:
            entry = result["feed"].entries[0]
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            published_time = time.mktime(published) if published else None
            if published_time and published_time > last_check:
                link = entry.get("link", "").strip()
                if link and not feed.is_link_posted(channel, link):
                    title = entry.get("title", "No Title").strip()
                    updates.append((name, title, link))
                    feed.mark_link_posted(channel, link)
                    feed.feed_metadata[url] = {"last_modified": result["last_modified"], "etag": result["etag"]}

    if not updates:
        logging.info(f"No new feeds for {channel}")
        feed.last_check_times[channel] = current_time
        return 0

    batch_size = feed.channel_settings.get(channel, {}).get("batch_size", BATCH_SIZE)
    if batch_size <= 0:
        batch_size = 1
    batches = [updates[i:i + batch_size] for i in range(0, len(updates), batch_size)]
    for i, batch in enumerate(batches):
        for name, title, link in batch:
            title_msg = f"Title: New Feed from {name}: {title}"
            link_msg = f"Link: {link}"
            send_to_platform(channel, title_msg, irc_send, matrix_send, discord_send)
            send_to_platform(channel, link_msg, irc_send, matrix_send, discord_send)
        if i < len(batches) - 1:
            await asyncio.sleep(BATCH_DELAY)

    feed.last_check_times[channel] = current_time
    logging.info(f"Posted {len(updates)} updates to {channel}")
    return len(updates)

async def start_polling(irc_send, matrix_send, discord_send, poll_interval=default_interval):
    logging.info("Starting feed polling")
    scheduler = FeedScheduler()
    feed.load_feeds()
    logging.info(f"Polling channels: {list(feed.channel_feeds.keys())}")
    for channel in feed.channel_feeds:
        feed.last_check_times.setdefault(channel, script_start_time)
        scheduler.add_channel(channel, feed.channel_intervals.get(channel, poll_interval))

    while True:
        next_time, channel = scheduler.get_next()
        if not channel:
            await asyncio.sleep(1)
            continue
        await asyncio.sleep(max(0, next_time - time.time()))
        feeds = feed.channel_feeds.get(channel, {})
        if not feeds:
            logging.warning(f"No feeds for {channel}")
            scheduler.reschedule(channel, feed.channel_intervals.get(channel, poll_interval))
            continue
        await process_channel(channel, feeds, irc_send, matrix_send, discord_send)
        scheduler.reschedule(channel, feed.channel_intervals.get(channel, poll_interval))

if __name__ == "__main__":
    def test_irc_send(channel, message):
        print(f"[IRC] {channel}: {message}")
    def test_matrix_send(channel, message):
        print(f"[Matrix] {channel}: {message}")
    def test_discord_send(channel, message):
        print(f"[Discord] {channel}: {message}")
    asyncio.run(start_polling(test_irc_send, test_matrix_send, test_discord_send, poll_interval=60))
