#!/usr/bin/env python3
"""
centralized_polling.py

This module implements centralized polling for RSS/Atom feeds for all integrations:
IRC, Matrix, and Discord. It uses the feed data from feed.py and, at configurable
intervals, checks each feed for new entries. When a new entry is found, it uses the
provided callback functions to send messages to the appropriate integration channel/room.

Usage:
    Import and start the polling loop by passing in three callback functions:
      - irc_send(channel, message): for sending messages via IRC.
      - matrix_send(room, message): for sending messages to a Matrix room.
      - discord_send(channel, message): for sending messages to a Discord channel.
      
    Optionally, set the poll_interval (default 300 seconds) between polling rounds.
"""

import time
import logging
import feedparser
import datetime

import feed
from config import default_interval

logging.basicConfig(level=logging.INFO)

# Global variable: only articles published after this time will be posted.
script_start_time = time.time()

def start_polling(irc_send, matrix_send, discord_send, poll_interval=300):
    logging.info("Centralized polling started.")
    feed.load_feeds()
    # Ensure global last_feed_links is set.
    if not hasattr(feed, 'last_feed_links') or feed.last_feed_links is None:
        feed.last_feed_links = set()
    current_time = time.time()
    
    # Initialize last_check_times for channels not yet set.
    if not hasattr(feed, 'last_check_times') or feed.last_check_times is None:
        feed.last_check_times = {}
    for chan in feed.channel_feeds.keys():
        if feed.channel_feeds[chan] is None:
            continue
        # Set last_check_time to script_start_time so that we ignore older entries.
        feed.last_check_times[chan] = script_start_time

    while True:
        current_time = time.time()
        channels_to_check = list(feed.channel_feeds.keys())
        logging.info(f"Checking {len(channels_to_check)} channels for new feeds...")

        for chan in channels_to_check:
            feeds_to_check = feed.channel_feeds.get(chan)
            if feeds_to_check is None:
                logging.warning(f"No feed dictionary found for channel {chan}; skipping.")
                continue
            interval = feed.channel_intervals.get(chan, default_interval)
            last_check = feed.last_check_times.get(chan, script_start_time)
            if current_time - last_check >= interval:
                new_feed_count = 0
                for feed_name, feed_url in feeds_to_check.items():
                    try:
                        parsed_feed = feedparser.parse(feed_url)
                        if parsed_feed.bozo:
                            logging.warning(f"Error parsing feed '{feed_name}' ({feed_url}): {parsed_feed.bozo_exception}")
                            continue
                        entries = parsed_feed.get("entries")
                        if not entries:
                            logging.info(f"No entries in feed '{feed_name}' ({feed_url}).")
                            continue
                        entry = entries[0]  # Process only the latest entry.
                        # Determine published time (or updated time).
                        published_time = None
                        if entry.get("published_parsed"):
                            published_time = time.mktime(entry.published_parsed)
                        elif entry.get("updated_parsed"):
                            published_time = time.mktime(entry.updated_parsed)
                        # For non-Discord channels, process only entries published after the last check.
                        if published_time is not None and not chan.isdigit() and published_time <= last_check:
                            logging.info(f"Skipping entry from feed '{feed_name}' published at {datetime.datetime.fromtimestamp(published_time)} because it is not newer than last check ({datetime.datetime.fromtimestamp(last_check)}).")
                            continue
                        title = entry.title.strip() if entry.get("title") else "No Title"
                        link = entry.link.strip() if entry.get("link") else ""
                        if link and link not in feed.last_feed_links:
                            # For Matrix channels, send two messages: one with title and one with link.
                            if chan.startswith("!"):
                                if matrix_send:
                                    matrix_send(chan, f"{feed_name}: {title}")
                                    matrix_send(chan, f"Link: {link}")
                            elif chan.startswith("#"):
                                if irc_send:
                                    irc_send(chan, f"New Feed from {feed_name}: {title}")
                                    irc_send(chan, f"Link: {link}")
                            elif chan.isdigit():
                                if discord_send:
                                    discord_send(chan, f"New Feed from {feed_name}: {title}")
                                    discord_send(chan, f"Link: {link}")
                            else:
                                if irc_send:
                                    irc_send(chan, f"New Feed from {feed_name}: {title}")
                                if matrix_send:
                                    matrix_send(chan, f"{feed_name}: {title}\nLink: {link}")
                                if discord_send:
                                    discord_send(chan, f"New Feed from {feed_name}: {title}")
                            new_feed_count += 1
                            feed.last_feed_links.add(link)
                            if hasattr(feed, "save_last_feed_link"):
                                feed.save_last_feed_link(link)
                        else:
                            logging.info(f"Feed link already posted in {chan}: {link}")
                    except Exception as e:
                        logging.error(f"Error checking feed '{feed_name}' at {feed_url}: {e}")
                if new_feed_count > 0:
                    logging.info(f"Posted {new_feed_count} new feeds in {chan}.")
                else:
                    logging.info(f"No new feeds found in {chan}.")
                feed.last_check_times[chan] = current_time
        logging.info(f"Finished checking feeds. Next check in {poll_interval} seconds.")
        time.sleep(poll_interval)

if __name__ == "__main__":
    def test_irc_send(channel, message):
        print(f"[IRC] Channel {channel}: {message}")
    def test_matrix_send(room, message):
        print(f"[Matrix] Room {room}: {message}")
    def test_discord_send(channel, message):
        print(f"[Discord] Channel {channel}: {message}")
    start_polling(test_irc_send, test_matrix_send, test_discord_send, poll_interval=300)
