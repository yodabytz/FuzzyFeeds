#!/usr/bin/env python3
"""
centralized_polling.py

This module polls RSS/Atom feeds for IRC, Matrix, and Discord integrations at fixed intervals.
For each feed URL, it checks for the newest entry whose link has not been posted before (as recorded
in posted_links, loaded from posted_links.json via feed.py). If found, it sends the update (Title then Link)
to the appropriate integration and marks the link as posted.
"""

import aiohttp
import asyncio
import heapq
import time
import logging
import feedparser
from io import BytesIO
import threading

import feed
from config import default_interval, BATCH_SIZE, BATCH_DELAY

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# Global startup time – we load feed data (including posted_links) only once.
script_start_time = time.time()

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
            elif response.status == 429:
                logging.warning(f"Got 429 (ratelimited) for feed: {url}")
                return {"ratelimited": True}
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
            else:
                logging.error(f"Unexpected status {response.status} for feed: {url}")
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

def normalize_channel_key(chan):
    """For IRC composite keys of the form 'server|#Channel', lowercase the channel part."""
    if "|" in chan:
        server, channel = chan.split("|", 1)
        return f"{server}|{channel.lower()}"
    return chan

async def process_channel(chan, feeds_to_check, irc_send, matrix_send, discord_send):
    current_time = time.time()
    norm_chan = normalize_channel_key(chan)
    # We no longer use last_check for filtering new entries; instead we rely on posted_links.
    updates = []
    async with aiohttp.ClientSession() as session:
        tasks = [
            fetch_feed_conditional(session, url, *feed.feed_metadata.get(url, {}).values())
            for url in feeds_to_check.values()
        ]
        results = await asyncio.gather(*tasks)
    # For each feed URL, post only the newest unposted entry.
    for (feed_name, feed_url), result in zip(feeds_to_check.items(), results):
        if result is None:
            continue
        if result.get("ratelimited"):
            logging.warning(f"Skipping feed '{feed_name}' due to rate limiting.")
            continue
        if "feed" in result:
            # Assume entries are in descending order (newest first) – most feeds do this.
            for entry in result["feed"].entries:
                link = entry.get("link", "").strip()
                if link and not feed.is_link_posted(norm_chan, link):
                    title = entry.get("title", "No Title").strip()
                    updates.append((feed_name, title, link))
                    feed.mark_link_posted(norm_chan, link)
                    # Only post one new update per feed URL per poll.
                    break
    if not updates:
        logging.info(f"No new feeds found in {norm_chan}.")
        # Update last_check to avoid reprocessing the same entries.
        feed.last_check_times[norm_chan] = current_time
        return 0

    # Post updates (Title then Link) with a slight delay.
    batch_size = feed.channel_settings.get(norm_chan, {}).get("batch_size", BATCH_SIZE)
    if batch_size <= 0:
        batch_size = 1
    batches = [updates[i:i + batch_size] for i in range(0, len(updates), batch_size)]
    for batch in batches:
        for feed_name, title, link in batch:
            send_to_platform(norm_chan, f"New Feed from {feed_name}: {title}", irc_send, matrix_send, discord_send)
            await asyncio.sleep(0.5)
            send_to_platform(norm_chan, f"Link: {link}", irc_send, matrix_send, discord_send)
            await asyncio.sleep(0.5)
    feed.last_check_times[norm_chan] = current_time
    logging.info(f"Posted {len(updates)} new feed entr{'y' if len(updates)==1 else 'ies'} in {norm_chan}.")
    return len(updates)

async def start_polling(irc_send, matrix_send, discord_send, poll_interval=default_interval):
    logging.info("Centralized async polling started.")
    scheduler = FeedScheduler()
    # Do not reload feeds here to preserve posted_links.
    for chan in feed.channel_feeds.keys():
        norm_chan = normalize_channel_key(chan)
        feed.last_check_times.setdefault(norm_chan, script_start_time)
        scheduler.add_channel(norm_chan, feed.channel_intervals.get(norm_chan, poll_interval))
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
