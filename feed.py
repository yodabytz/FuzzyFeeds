#!/usr/bin/env python3
import os
import json
import feedparser
import time
from config import feeds_file, subscriptions_file, channels, last_links_file, default_interval

# Global data – only user‐added feeds are stored.
channel_feeds = {}  # { channel: { feed_name: feed_url, ... } }
subscriptions = {}  # User-specific subscriptions: { user: { feed_name: feed_url, ... } }
last_feed_links = set()  # Set of seen feed links

# Globals for scheduling channel feeds
channel_intervals = {}  # { channel: interval_in_seconds }
last_check_times = {}   # { channel: last_check_timestamp }

def load_feeds():
    """Load feeds from feeds_file; if missing/empty, leave channel_feeds empty."""
    global channel_feeds
    if os.path.exists(feeds_file):
        try:
            channel_feeds = json.load(open(feeds_file, "r"))
        except Exception as e:
            print(f"Error loading {feeds_file}: {e}")
            channel_feeds = {}
    else:
        channel_feeds = {}
    init_channel_times()
    print("[feed.py] Loaded channel feeds:", channel_feeds)
    load_subscriptions()  # Ensure subscriptions are loaded

def save_feeds():
    """Save the current channel_feeds dictionary to feeds_file."""
    try:
        with open(feeds_file, "w") as f:
            json.dump(channel_feeds, f, indent=4)
        print("[feed.py] Saved channel feeds:", channel_feeds)
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
    print("[feed.py] Loaded user subscriptions:", subscriptions)

def save_subscriptions():
    """Save the subscriptions dictionary to subscriptions_file."""
    try:
        with open(subscriptions_file, "w") as f:
            json.dump(subscriptions, f, indent=4)
        print("[feed.py] Saved user subscriptions:", subscriptions)
    except Exception as e:
        print(f"Error saving {subscriptions_file}: {e}")

def load_last_feed_links():
    """Load the set of seen feed links from last_links_file."""
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
    print("[feed.py] Loaded last feed links:", last_feed_links)

def save_last_feed_link(link):
    """Append a new feed link to last_links_file and update the in-memory set."""
    global last_feed_links
    last_feed_links.add(link)
    try:
        with open(last_links_file, "a") as f:
            f.write(f"{link}\n")
        print("[feed.py] Saved new feed link:", link)
    except Exception as e:
        print(f"Error saving to {last_links_file}: {e}")

def fetch_latest_feed(feed_url):
    """
    Parse the feed URL and return (title, link) for the latest entry
    if its link has not been seen; otherwise, return (None, None).
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

def check_feeds(send_message_func):
    """
    For each channel in channels, check for new feed entries using user‐added feeds.
    Only post articles that have been published after the last check time.
    Announce any new entries via send_message_func by sending two separate messages:
    one for the title and one for the link, then record their links.
    Returns a list of messages that were generated.
    """
    messages = []
    current_time = time.time()
    for chan in channels:
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
                        title_msg = f"New Feed from {feed_name}: {title}"
                        link_msg = f"Link: {link}"
                        send_message_func(chan, title_msg)
                        send_message_func(chan, link_msg)
                        messages.append(title_msg)
                        messages.append(link_msg)
                        save_last_feed_link(link)
            last_check_times[chan] = current_time
    return messages

if __name__ == "__main__":
    # For testing purposes only.
    load_feeds()
    load_last_feed_links()
