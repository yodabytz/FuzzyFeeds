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

    # Ensure each channel has a last_check_time.
    if not hasattr(feed, 'last_check_times') or feed.last_check_times is None:
        feed.last_check_times = {}
    for chan in feed.channel_feeds.keys():
        feed.last_check_times.setdefault(chan, script_start_time)

    while True:
        current_time = time.time()
        logging.info(f"Polling loop iteration at {datetime.datetime.fromtimestamp(current_time)}")

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

            if current_time - last_check >= interval:
                new_feed_count = 0

                for feed_name, feed_url in feeds_to_check.items():
                    try:
                        parsed_feed = feedparser.parse(feed_url)
                        if parsed_feed.bozo:
                            logging.warning(
                                f"Error parsing feed '{feed_name}' ({feed_url}): {parsed_feed.bozo_exception}"
                            )
                            continue

                        entries = parsed_feed.get("entries")
                        if not entries:
                            logging.info(f"No entries in feed '{feed_name}' ({feed_url}).")
                            continue

                        entry = entries[0]
                        published_time = None
                        if entry.get("published_parsed"):
                            published_time = time.mktime(entry.published_parsed)
                        elif entry.get("updated_parsed"):
                            published_time = time.mktime(entry.updated_parsed)

                        if published_time is not None and published_time <= last_check:
                            logging.info(
                                f"Skipping entry from feed '{feed_name}' "
                                f"published at {datetime.datetime.fromtimestamp(published_time)} "
                                f"(last check was {datetime.datetime.fromtimestamp(last_check)})."
                            )
                            continue

                        title = entry.title.strip() if entry.get("title") else "No Title"
                        link = entry.link.strip() if entry.get("link") else ""

                        if link and feed.is_link_posted(chan, link):
                            logging.info(f"Channel {chan} already has link: {link}")
                            continue

                        if link:
                            # Build separate messages for title and link.
                            title_msg = f"{feed_name}: {title}"
                            link_msg = f"Link: {link}"

                            # For IRC channels (regular or composite) send each line separately.
                            if chan.startswith("#") or ("|" in chan and chan.split("|", 1)[1].startswith("#")):
                                actual_channel = chan if chan.startswith("#") else chan.split("|", 1)[1]
                                if irc_send:
                                    irc_send(actual_channel, title_msg)
                                    # Small delay to help queue processing
                                    time.sleep(0.1)
                                    irc_send(actual_channel, link_msg)
                            elif chan.startswith("!"):
                                if matrix_send:
                                    matrix_send(chan, f"{title_msg}\n{link_msg}")
                            elif chan.isdigit():
                                if discord_send:
                                    discord_send(chan, f"{title_msg}\n{link_msg}")
                            else:
                                if irc_send:
                                    irc_send(chan, f"{title_msg}\n{link_msg}")
                                if matrix_send:
                                    matrix_send(chan, f"{title_msg}\n{link_msg}")
                                if discord_send:
                                    discord_send(chan, f"{title_msg}\n{link_msg}")

                            feed.mark_link_posted(chan, link)
                            new_feed_count += 1

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
