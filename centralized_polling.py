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

import feed
from config import default_interval

logging.basicConfig(level=logging.INFO)

def start_polling(irc_send, matrix_send, discord_send, poll_interval=300):
    logging.info("Centralized polling started.")
    feed.load_feeds()
    # Load last feed links from file if available.
    if hasattr(feed, "load_last_feed_links"):
        feed.last_feed_links = feed.load_last_feed_links()
    if not hasattr(feed, 'last_feed_links'):
        feed.last_feed_links = set()
    current_time = time.time()
    
    # Initialize last_check_times for channels not yet set.
    for chan in feed.channel_feeds.keys():
        if chan not in feed.last_check_times:
            feed.last_check_times[chan] = current_time

    while True:
        current_time = time.time()
        channels_to_check = list(feed.channel_feeds.keys())
        logging.info(f"Checking {len(channels_to_check)} channels for new feeds...")

        for chan in channels_to_check:
            feeds_to_check = feed.channel_feeds.get(chan, {})
            interval = feed.channel_intervals.get(chan, default_interval)
            last_check = feed.last_check_times.get(chan, 0)
            if current_time - last_check >= interval:
                new_feed_count = 0
                for feed_name, feed_url in feeds_to_check.items():
                    try:
                        parsed_feed = feedparser.parse(feed_url)
                        if parsed_feed.bozo:
                            logging.warning(f"Error parsing feed '{feed_name}' ({feed_url}): {parsed_feed.bozo_exception}")
                            continue
                        if parsed_feed.entries:
                            entry = parsed_feed.entries[0]  # Only process the latest entry.
                            title = entry.title.strip() if entry.title else "No Title"
                            link = entry.link.strip() if entry.link else ""
                            if link and link not in feed.last_feed_links:
                                # For Matrix channels: send one combined message.
                                if chan.startswith("!"):
                                    if matrix_send:
                                        # Format: "Feedname: Title" on first line, then the link on second.
                                        combined_msg = f"{feed_name}: {title}\n{link}"
                                        matrix_send(chan, combined_msg)
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
                                        matrix_send(chan, f"{feed_name}: {title}\n{link}")
                                    if discord_send:
                                        discord_send(chan, f"New Feed from {feed_name}: {title}")
                                new_feed_count += 1
                                feed.last_feed_links.add(link)
                                if hasattr(feed, "save_last_feed_link"):
                                    feed.save_last_feed_link(link)
                            # Else, skip if already posted.
                        else:
                            logging.info(f"No entries in feed '{feed_name}' ({feed_url}).")
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
    # Test callback implementations.
    def test_irc_send(channel, message):
        print(f"[IRC] Channel {channel}: {message}")

    def test_matrix_send(room, message):
        try:
            from matrix_integration import send_message as send_matrix_message
            send_matrix_message(room, message)
        except Exception as e:
            logging.error(f"Error sending Matrix message: {e}")

    def test_discord_send(channel, message):
        print(f"[Discord] Channel {channel}: {message}")

    start_polling(test_irc_send, test_matrix_send, test_discord_send, poll_interval=60)
