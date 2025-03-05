#!/usr/bin/env python3
import os
import json
import feedparser
import time
import logging
from config import feeds_file, subscriptions_file, last_links_file, default_interval

# Global Data
channel_feeds = {}  # { channel: { feed_name: feed_url, ... } }
subscriptions = {}  # User-specific subscriptions: { user: { feed_name: feed_url, ... } }
last_feed_links = set()  # Set of seen feed links
channel_intervals = {}  # { channel: interval_in_seconds }
last_check_times = {}   # { channel: last_check_timestamp }

# Set up logging
logging.basicConfig(level=logging.INFO)

def load_feeds():
    """Load feeds from feeds.json, and log only the number of feeds per channel."""
    global channel_feeds
    if os.path.exists(feeds_file):
        try:
            with open(feeds_file, "r") as f:
                channel_feeds = json.load(f)
            logging.info(f"[feed.py] Loaded feeds for {len(channel_feeds)} channels, total feeds: {sum(len(feeds) for feeds in channel_feeds.values())}")
        except Exception as e:
            logging.error(f"[feed.py] Error loading {feeds_file}: {e}")
            channel_feeds = {}
    else:
        channel_feeds = {}

    init_channel_times()
    load_subscriptions()

def save_feeds():
    """Save the current channel_feeds dictionary to feeds.json."""
    try:
        with open(feeds_file, "w") as f:
            json.dump(channel_feeds, f, indent=4)
        logging.info(f"[feed.py] Feeds saved for {len(channel_feeds)} channels.")
    except Exception as e:
        logging.error(f"[feed.py] Error saving {feeds_file}: {e}")

def init_channel_times():
    """Ensure each channel has a default interval and last check time."""
    global channel_intervals, last_check_times
    current_time = time.time()
    for chan in channel_feeds:
        if chan not in channel_intervals:
            channel_intervals[chan] = default_interval
        if chan not in last_check_times:
            last_check_times[chan] = current_time

def load_subscriptions():
    """Load user subscriptions and log counts instead of full details."""
    global subscriptions
    if os.path.exists(subscriptions_file):
        try:
            with open(subscriptions_file, "r") as f:
                subscriptions = json.load(f)
            logging.info(f"[feed.py] Loaded {len(subscriptions)} user subscriptions, total feeds: {sum(len(feeds) for feeds in subscriptions.values())}")
        except Exception as e:
            logging.error(f"[feed.py] Error loading {subscriptions_file}: {e}")
            subscriptions = {}
    else:
        subscriptions = {}

def save_subscriptions():
    """Save subscriptions and log the number of users saved."""
    try:
        with open(subscriptions_file, "w") as f:
            json.dump(subscriptions, f, indent=4)
        logging.info(f"[feed.py] Subscriptions saved for {len(subscriptions)} users.")
    except Exception as e:
        logging.error(f"[feed.py] Error saving {subscriptions_file}: {e}")

def load_last_feed_links():
    """Load previously seen feed links."""
    global last_feed_links
    if os.path.exists(last_links_file):
        try:
            with open(last_links_file, "r") as f:
                last_feed_links = set(f.read().splitlines())
            logging.info(f"[feed.py] Loaded {len(last_feed_links)} past feed links.")
        except Exception as e:
            logging.error(f"[feed.py] Error loading {last_links_file}: {e}")
            last_feed_links = set()
    else:
        last_feed_links = set()

def save_last_feed_link(link):
    """Save a new feed link to prevent reposting."""
    global last_feed_links
    last_feed_links.add(link)
    try:
        with open(last_links_file, "a") as f:
            f.write(f"{link}\n")
        logging.info(f"[feed.py] Added new feed link to history.")
    except Exception as e:
        logging.error(f"[feed.py] Error saving to {last_links_file}: {e}")

def fetch_latest_feed(feed_url):
    """Fetch the latest feed entry if it hasn't been seen before."""
    d = feedparser.parse(feed_url)
    if d.entries:
        entry = d.entries[0]
        title = entry.title.strip() if entry.title else "No Title"
        link = entry.link.strip() if entry.link else ""
        if link and link not in last_feed_links:
            return title, link
    return None, None

def fetch_latest_article(feed_url):
    """Always return the latest entry, even if it was already seen."""
    d = feedparser.parse(feed_url)
    if d.entries:
        entry = d.entries[0]
        title = entry.title.strip() if entry.title else "No Title"
        link = entry.link.strip() if entry.link else ""
        return title, link
    return None, None

def check_feeds(send_message_func, channels_to_check=None):
    """Check feeds for all channels and user subscriptions, logging only feed counts."""
    current_time = time.time()
    channels_list = channels_to_check if channels_to_check is not None else list(channel_feeds.keys())

    # Include user subscriptions in feed checks
    for user, subs in subscriptions.items():
        channels_list.append(user)

    logging.info(f"[feed.py] Checking feeds for {len(channels_list)} targets...")

    for chan in channels_list:
        feeds_to_check = channel_feeds.get(chan, {}) if chan in channel_feeds else subscriptions.get(chan, {})
        interval = channel_intervals.get(chan, default_interval)

        if current_time - last_check_times.get(chan, 0) >= interval:
            logging.info(f"[feed.py] Checking {len(feeds_to_check)} feeds in {chan}...")

            for feed_name, feed_url in feeds_to_check.items():
                title, link = fetch_latest_feed(feed_url)
                if title and link:
                    send_message_func(chan, f"New Feed from {feed_name}: {title}")
                    send_message_func(chan, f"Link: {link}")
                    save_last_feed_link(link)

            last_check_times[chan] = current_time
