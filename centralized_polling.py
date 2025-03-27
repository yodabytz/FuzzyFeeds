#!/usr/bin/env python3
"""
centralized_polling.py

This module implements centralized polling for RSS/Atom feeds for all integrations:
IRC, Matrix, and Discord. It uses the feed data from feed.py and, at configurable
intervals, checks each feed for new entries. When a new entry is found, it uses the
provided callback functions to send messages to the appropriate integration channel/room.
"""

import time
import logging
import feedparser
import datetime

import feed
from config import default_interval

logging.basicConfig(level=logging.INFO)

# Global variable for initial setup.
script_start_time = time.time()

def start_polling(irc_send, matrix_send, discord_send, poll_interval=300):
    logging.info("Centralized polling started.")
    while True:
        feed.load_feeds()
        current_time = time.time()
        channels_to_check = list(feed.channel_feeds.keys())
        logging.info(f"Checking {len(channels_to_check)} channels for new feeds: {channels_to_check}")
        
        # Ensure we track last check times
        if not hasattr(feed, 'last_check_times') or feed.last_check_times is None:
            feed.last_check_times = {}
        for chan in channels_to_check:
            feed.last_check_times.setdefault(chan, script_start_time)
            
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
                                f"Error parsing feed '{feed_name}' ({feed_url}): "
                                f"{parsed_feed.bozo_exception}"
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
                                f"Skipping entry from feed '{feed_name}' published at "
                                f"{datetime.datetime.fromtimestamp(published_time)} "
                                f"(last check was {datetime.datetime.fromtimestamp(last_check)})."
                            )
                            continue
                        
                        logging.info(
                            f"Feed '{feed_name}' data - Title: {entry.get('title')}, Link: {entry.get('link')}"
                        )
                        
                        title = entry.title.strip() if entry.get("title") else "No Title"
                        link = entry.link.strip() if entry.get("link") else ""
                        
                        # Check if we've already posted this link
                        if link and feed.is_link_posted(chan, link):
                            logging.info(f"Channel {chan} already has link: {link}")
                            continue

                        if link:
                            title_msg = f"{feed_name}: {title}"
                            link_msg  = f"Link: {link}"

                            # Title always sent first, link second
                            if chan.startswith("!"):
                                matrix_send(chan, title_msg)
                                matrix_send(chan, link_msg)
                            elif str(chan).isdigit():
                                discord_send(chan, title_msg)
                                discord_send(chan, link_msg)
                            else:
                                irc_send(chan, title_msg)
                                irc_send(chan, link_msg)

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
        # Simple test function
        print(f"[Primary IRC] {channel}: {message}")

    def test_matrix_send(room, message):
        print(f"[Matrix] {room}: {message}")

    def test_discord_send(channel, message):
        print(f"[Discord] {channel}: {message}")
        
    start_polling(test_irc_send, test_matrix_send, test_discord_send, poll_interval=300)

