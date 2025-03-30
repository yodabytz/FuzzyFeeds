#!/usr/bin/env python3
"""
centralized_polling.py

This module implements centralized polling for RSS/Atom feeds for all integrations:
IRC, Matrix, and Discord. It uses the feed data from feed.py and, at configurable
intervals, checks each feed for new entries. When a new entry is found, it uses the
provided callback functions to send messages to the appropriate integration channel/room.
It also checks subscription feeds (stored in feed.subscriptions) and sends any new entries privately.
"""

import time
import logging
import feedparser
import datetime

import feed
from config import default_interval

logging.basicConfig(level=logging.INFO)

# Global variable for initial setup time.
script_start_time = time.time()

def start_polling(irc_send, matrix_send, discord_send, private_send, poll_interval=300):
    """
    This function runs an infinite loop checking:
      1. Channel feeds (in feed.channel_feeds) – posting Title then Link to the appropriate integration.
      2. Subscription feeds (in feed.subscriptions) – for each user, if a new entry is found, it is sent privately.
    
    The private_send callback should take two arguments: (user, message).
    """
    # Ensure the subscriptions last-check dictionary exists
    if not hasattr(feed, 'last_check_subs'):
        feed.last_check_subs = {}
        
    logging.info("Centralized polling started.")
    while True:
        feed.load_feeds()
        current_time = time.time()
        channels_to_check = list(feed.channel_feeds.keys())
        logging.info(f"Checking {len(channels_to_check)} channels for new feeds: {channels_to_check}")
        
        # Process channel feeds
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
                f"Channel {chan}: Last check at {datetime.datetime.fromtimestamp(last_check)}, "
                f"current time {datetime.datetime.fromtimestamp(current_time)}, interval {interval}s"
            )
            
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
                        entry = entries[0]
                        published_time = None
                        if entry.get("published_parsed"):
                            published_time = time.mktime(entry.published_parsed)
                        elif entry.get("updated_parsed"):
                            published_time = time.mktime(entry.updated_parsed)
                        if published_time is not None and published_time <= last_check:
                            logging.info(f"Skipping entry from feed '{feed_name}' published at {datetime.datetime.fromtimestamp(published_time)} (last check was {datetime.datetime.fromtimestamp(last_check)}).")
                            continue
                        
                        logging.info(f"Feed '{feed_name}' data - Title: {entry.get('title')}, Link: {entry.get('link')}")
                        
                        title = entry.title.strip() if entry.get("title") else "No Title"
                        link = entry.link.strip() if entry.get("link") else ""
                        if link and feed.is_link_posted(chan, link):
                            logging.info(f"Channel {chan} already has link: {link}")
                            continue

                        if link:
                            title_msg = f"{feed_name}: {title}"
                            link_msg  = f"Link: {link}"
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

        # Process subscription feeds
        for user, subs in feed.subscriptions.items():
            # Ensure each user's last check record is a dictionary.
            if not isinstance(feed.last_check_subs.get(user), dict):
                feed.last_check_subs[user] = {}
            for sub_name, sub_url in subs.items():
                # Default last check for this subscription
                last_check_sub = feed.last_check_subs[user].get(sub_name, script_start_time)
                try:
                    parsed_sub = feedparser.parse(sub_url)
                    if parsed_sub.bozo:
                        logging.warning(f"Error parsing subscription feed '{sub_name}' for {user}: {parsed_sub.bozo_exception}")
                        continue
                    entries = parsed_sub.get("entries")
                    if not entries:
                        logging.info(f"No entries in subscription feed '{sub_name}' for {user}.")
                        continue
                    entry = entries[0]
                    published_time = None
                    if entry.get("published_parsed"):
                        published_time = time.mktime(entry.published_parsed)
                    elif entry.get("updated_parsed"):
                        published_time = time.mktime(entry.updated_parsed)
                    if published_time is not None and published_time <= last_check_sub:
                        continue
                    title = entry.title.strip() if entry.get("title") else "No Title"
                    link = entry.link.strip() if entry.get("link") else ""
                    if link and not feed.is_link_posted(user, link):
                        message_text = f"New Subscription Feed from {sub_name}: {title}\nLink: {link}"
                        private_send(user, message_text)
                        feed.mark_link_posted(user, link)
                    # Update the last check time for this subscription.
                    feed.last_check_subs[user][sub_name] = published_time
                except Exception as e:
                    logging.error(f"Error checking subscription feed '{sub_name}' for {user}: {e}")

        logging.info(f"Finished checking feeds. Next check in {poll_interval} seconds.")
        time.sleep(poll_interval)

if __name__ == "__main__":
    # Simple test functions for local debugging
    def test_irc_send(channel, message):
        print(f"[IRC] {channel}: {message}")

    def test_matrix_send(room, message):
        print(f"[MATRIX] {room}: {message}")

    def test_discord_send(channel, message):
        print(f"[DISCORD] {channel}: {message}")

    def test_private_send(user, message):
        print(f"[PRIVATE] {user}: {message}")

    start_polling(test_irc_send, test_matrix_send, test_discord_send, test_private_send, poll_interval=300)
