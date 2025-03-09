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

# Global variable: used for initial setup of last check times.
script_start_time = time.time()

def start_polling(irc_send, matrix_send, discord_send, poll_interval=300):
    logging.info("Centralized polling started.")
    feed.load_feeds()
    logging.info(f"Loaded channels: {list(feed.channel_feeds.keys())}")
    
    # Initialize last_check_times for any channel that doesn't have one.
    # The feed.py code already does some initialization, but we do this
    # again in case new channels appear after feed.load_feeds().
    if not hasattr(feed, 'last_check_times') or feed.last_check_times is None:
        feed.last_check_times = {}
    for chan in feed.channel_feeds.keys():
        feed.last_check_times.setdefault(chan, script_start_time)

    while True:
        current_time = time.time()
        logging.info(f"Polling loop iteration at {datetime.datetime.fromtimestamp(current_time)}")
        
        # Grab a static list of channels for this iteration
        channels_to_check = list(feed.channel_feeds.keys())
        logging.info(f"Checking {len(channels_to_check)} channels for new feeds...")

        for chan in channels_to_check:
            feeds_to_check = feed.channel_feeds.get(chan)
            if not feeds_to_check:
                logging.warning(f"No feed dictionary found for channel {chan}; skipping.")
                continue

            interval = feed.channel_intervals.get(chan, default_interval)
            last_check = feed.last_check_times.get(chan, script_start_time)

            logging.info(
                f"Channel {chan}: Last check at "
                f"{datetime.datetime.fromtimestamp(last_check)}, "
                f"current time {datetime.datetime.fromtimestamp(current_time)}, "
                f"interval {interval}s"
            )

            # Only proceed if enough time has passed for this channel
            if current_time - last_check >= interval:
                new_feed_count = 0

                for feed_name, feed_url in feeds_to_check.items():
                    try:
                        parsed_feed = feedparser.parse(feed_url)
                        if parsed_feed.bozo:
                            logging.warning(
                                f"Error parsing feed '{feed_name}' ({feed_url}): "
                                f"{parsed_feed.bozo_exception}"
                            )
                            continue

                        entries = parsed_feed.get("entries")
                        if not entries:
                            logging.info(f"No entries in feed '{feed_name}' ({feed_url}).")
                            continue

                        # We only look at the most recent entry
                        entry = entries[0]
                        published_time = None

                        # Use published or updated time
                        if entry.get("published_parsed"):
                            published_time = time.mktime(entry.published_parsed)
                        elif entry.get("updated_parsed"):
                            published_time = time.mktime(entry.updated_parsed)

                        # For all channels, skip if feed item is older than this channel's last check
                        if published_time is not None and published_time <= last_check:
                            logging.info(
                                f"Skipping entry from feed '{feed_name}' "
                                f"published at {datetime.datetime.fromtimestamp(published_time)} "
                                f"(last check was {datetime.datetime.fromtimestamp(last_check)})."
                            )
                            continue

                        title = entry.title.strip() if entry.get("title") else "No Title"
                        link = entry.link.strip() if entry.get("link") else ""

                        # Since we no longer do a global last_feed_links check,
                        # each channel will see the feed if it meets time criteria.
                        if link:
                            if chan.startswith("!"):  # Matrix channel
                                if matrix_send:
                                    matrix_send(chan, f"{feed_name}: {title}")
                                    matrix_send(chan, f"Link: {link}")
                            elif chan.startswith("#"):  # IRC channel
                                if irc_send:
                                    irc_send(chan, f"New Feed from {feed_name}: {title}")
                                    irc_send(chan, f"Link: {link}")
                            elif chan.isdigit():        # Discord channel
                                if discord_send:
                                    discord_send(chan, f"New Feed from {feed_name}: {title}")
                                    discord_send(chan, f"Link: {link}")
                            else:
                                # fallback or unknown
                                if irc_send:
                                    irc_send(chan, f"New Feed from {feed_name}: {title}")
                                if matrix_send:
                                    matrix_send(chan, f"{feed_name}: {title}\nLink: {link}")
                                if discord_send:
                                    discord_send(chan, f"New Feed from {feed_name}: {title}")

                            new_feed_count += 1

                    except Exception as e:
                        logging.error(f"Error checking feed '{feed_name}' at {feed_url}: {e}")

                # Done checking all feeds in this channel
                if new_feed_count > 0:
                    logging.info(f"Posted {new_feed_count} new feeds in {chan}.")
                else:
                    logging.info(f"No new feeds found in {chan}.")

                # Update last check time for this channel
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
