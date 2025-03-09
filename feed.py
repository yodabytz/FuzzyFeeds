#!/usr/bin/env python3
import os
import json
import feedparser
import time
from config import feeds_file, subscriptions_file, channels, last_links_file, default_interval

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Global data – only user‐added feeds are stored.
channel_feeds = {}  # { channel: { feed_name: feed_url, ... } }
subscriptions = {}  # User-specific subscriptions: { user: { feed_name: feed_url, ... } }

# We used to keep a single set of last_feed_links, but now we do
# per-channel posted links to avoid repeated re-posting in that channel.
last_feed_links = set()  # DEPRECATED for global usage, but left for any older code references

# Globals for scheduling channel feeds
channel_intervals = {}  # { channel: interval_in_seconds }
last_check_times = {}   # { channel: last_check_timestamp }

# NEW: Per-channel posted links, stored in posted_links.json
posted_links_file = os.path.join(BASE_DIR, "posted_links.json")
posted_links = {}  # {channel: set_of_posted_links}


def load_posted_links():
    """
    Loads posted_links from posted_links.json as a dict:
       { "channel": ["link1", "link2", ...], ... }
    Internally we store them as sets for quick membership checks.
    """
    global posted_links
    if os.path.exists(posted_links_file):
        try:
            with open(posted_links_file, "r") as f:
                data = json.load(f)
            posted_links = {ch: set(links) for ch, links in data.items()}
        except Exception as e:
            print(f"Error loading {posted_links_file}: {e}")
            posted_links = {}
    else:
        posted_links = {}


def save_posted_links():
    """
    Saves posted_links back to posted_links.json,
    converting each set to a list so JSON can store them.
    """
    try:
        serializable = {ch: list(links) for ch, links in posted_links.items()}
        with open(posted_links_file, "w") as f:
            json.dump(serializable, f, indent=4)
    except Exception as e:
        print(f"Error saving {posted_links_file}: {e}")


def is_link_posted(channel, link):
    """
    Returns True if 'link' is already posted in 'channel'.
    """
    return link in posted_links.get(channel, set())


def mark_link_posted(channel, link):
    """
    Mark 'link' as posted in 'channel' and save to disk.
    """
    if channel not in posted_links:
        posted_links[channel] = set()
    posted_links[channel].add(link)
    save_posted_links()


def load_feeds():
    """Load feeds from feeds_file; if missing/empty, leave channel_feeds empty."""
    global channel_feeds
    if os.path.exists(feeds_file):
        try:
            channel_feeds = json.load(open(feeds_file, "r"))
        except Exception as e:
            print(f"Error loading {feeds_file}: {e}")
            channel_feeds = {}
            save_feeds()
    else:
        channel_feeds = {}
        save_feeds()
    init_channel_times()
    total_feeds = sum(len(feeds) for feeds in channel_feeds.values())
    print(f"[feed.py] Loaded {len(channel_feeds)} channels with {total_feeds} feeds.")

    # Ensure subscriptions are loaded
    load_subscriptions()

    # Also ensure per-channel posted links are loaded
    load_posted_links()


def save_feeds():
    """Save the current channel_feeds dictionary to feeds_file."""
    try:
        with open(feeds_file, "w") as f:
            json.dump(channel_feeds, f, indent=4)
        total_feeds = sum(len(feeds) for feeds in channel_feeds.values())
        print(f"[feed.py] Saved {len(channel_feeds)} channels with {total_feeds} feeds.")
    except Exception as e:
        print(f"Error saving {feeds_file}: {e}")


def init_channel_times():
    """Ensure each channel in channels has a default interval and last check time."""
    global channel_intervals, last_check_times
    current_time = time.time()
    for chan in channels:
        if chan not in channel_intervals:
            channel_intervals[chan] = default_interval
        if chan not in last_check_times or last_check_times.get(chan, 0) == 0:
            last_check_times[chan] = current_time


def load_subscriptions():
    """Load user subscriptions from subscriptions_file."""
    global subscriptions
    if os.path.exists(subscriptions_file):
        try:
            subscriptions = json.load(open(subscriptions_file, "r"))
        except Exception as e:
            print(f"Error loading {subscriptions_file}: {e}")
            subscriptions = {}
    else:
        subscriptions = {}
    save_subscriptions()
    total_subs = sum(len(subs) for subs in subscriptions.values())
    print(f"[feed.py] Loaded user subscriptions: {total_subs} subscriptions.")


def save_subscriptions():
    """Save the subscriptions dictionary to subscriptions_file."""
    try:
        with open(subscriptions_file, "w") as f:
            json.dump(subscriptions, f, indent=4)
        total_subs = sum(len(subs) for subs in subscriptions.values())
        print(f"[feed.py] Saved user subscriptions: {total_subs} subscriptions.")
    except Exception as e:
        print(f"Error saving {subscriptions_file}: {e}")


def load_last_feed_links():
    """Legacy function. Loads from last_links_file (text) into last_feed_links set."""
    global last_feed_links
    if os.path.exists(last_links_file):
        try:
            with open(last_links_file, "r") as f:
                last_feed_links = set(f.read().splitlines())
        except Exception as e:
            print(f"Error loading {last_links_file}: {e}")
            last_feed_links = set()
    else:
        last_feed_links = set()
    print(f"[feed.py] Loaded {len(last_feed_links)} last feed links.")


def save_last_feed_link(link):
    """Legacy function. Appends new link to last_links_file and last_feed_links set."""
    global last_feed_links
    last_feed_links.add(link)
    try:
        with open(last_links_file, "a") as f:
            f.write(f"{link}\n")
        print(f"[feed.py] Saved new feed link: {link}")
    except Exception as e:
        print(f"Error saving to {last_links_file}: {e}")


def fetch_latest_feed(feed_url):
    """
    Parse the feed URL and return (title, link) for the latest entry
    if its link has not been seen globally (legacy check); otherwise, return (None, None).
    """
    d = feedparser.parse(feed_url)
    if d.entries:
        entry = d.entries[0]
        title = entry.title.strip() if entry.title else "No Title"
        link = entry.link.strip() if entry.link else ""
        if link and link not in last_feed_links:
            return title, link
    return None, None


def fetch_latest_article(feed_url):
    """
    Always return the latest entry (title, link), regardless of seen status.
    """
    d = feedparser.parse(feed_url)
    if d.entries:
        entry = d.entries[0]
        title = entry.title.strip() if entry.title else "No Title"
        link = entry.link.strip() if entry.link else ""
        return title, link
    return None, None


def check_feeds(send_message_func, channels_to_check=None):
    """
    For each channel (or each channel in channels_to_check if provided),
    check for new feed entries using user‐added feeds.
    Only post articles that have been published after the last check time.
    Announce any new entries via send_message_func by sending messages,
    then record their links in last_feed_links (legacy).
    """
    current_time = time.time()
    channels_list = channels_to_check if channels_to_check is not None else channels
    for chan in channels_list:
        feeds_to_check = channel_feeds.get(chan, {})
        interval = channel_intervals.get(chan, default_interval)
        if current_time - last_check_times.get(chan, 0) >= interval:
            for feed_name, feed_url in feeds_to_check.items():
                d = feedparser.parse(feed_url)
                if d.entries:
                    entry = d.entries[0]
                    published_time = None
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        published_time = time.mktime(entry.published_parsed)
                    if published_time is not None and published_time <= last_check_times.get(chan, 0):
                        continue
                    title = entry.title.strip() if entry.title else "No Title"
                    link = entry.link.strip() if entry.link else ""
                    if link and link not in last_feed_links:
                        send_message_func(chan, f"New Feed from {feed_name}: {title}")
                        send_message_func(chan, f"Link: {link}")
                        save_last_feed_link(link)
            last_check_times[chan] = current_time
