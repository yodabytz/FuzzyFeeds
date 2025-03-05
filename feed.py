#!/usr/bin/env python3
import os
import json
import feedparser
import time
from config import feeds_file, subscriptions_file, channels, last_links_file, default_interval

# Global cache for loaded feeds and subscriptions
channel_feeds = {}  # { channel: { feed_name: feed_url, ... } }
subscriptions = {}  # { user: { feed_name: feed_url, ... } }
last_feed_links = set()  # Set of seen feed links
feeds_loaded = False  # Track if feeds have already been loaded
subscriptions_loaded = False  # Track if subscriptions have been loaded

# Globals for scheduling channel feeds
channel_intervals = {}  # { channel: interval_in_seconds }
last_check_times = {}   # { channel: last_check_timestamp }

def load_feeds(force_reload=False):
    """Load feeds from feeds_file only once unless forced."""
    global channel_feeds, feeds_loaded
    if feeds_loaded and not force_reload:
        return  # Skip reloading if feeds are already loaded
    
    if os.path.exists(feeds_file):
        try:
            with open(feeds_file, "r") as f:
                channel_feeds = json.load(f)
        except Exception as e:
            print(f"Error loading {feeds_file}: {e}")
            channel_feeds = {}
    else:
        channel_feeds = {}
    
    init_channel_times()
    total_feeds = sum(len(feeds) for feeds in channel_feeds.values())
    print(f"[feed.py] Loaded {len(channel_feeds)} channels with {total_feeds} feeds.")
    
    feeds_loaded = True  # Mark feeds as loaded
    load_subscriptions()  # Ensure subscriptions are loaded

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
    """Ensure each channel has a default interval and last check time."""
    global channel_intervals, last_check_times
    current_time = time.time()
    for chan in channels:
        if chan not in channel_intervals:
            channel_intervals[chan] = default_interval
        if chan not in last_check_times or last_check_times.get(chan, 0) == 0:
            last_check_times[chan] = current_time

def load_subscriptions(force_reload=False):
    """Load user subscriptions only once unless forced."""
    global subscriptions, subscriptions_loaded
    if subscriptions_loaded and not force_reload:
        return  # Skip reloading if subscriptions are already loaded
    
    if os.path.exists(subscriptions_file):
        try:
            with open(subscriptions_file, "r") as f:
                subscriptions = json.load(f)
        except Exception as e:
            print(f"Error loading {subscriptions_file}: {e}")
            subscriptions = {}
    else:
        subscriptions = {}
    
    save_subscriptions()
    total_subs = sum(len(subs) for subs in subscriptions.values())
    print(f"[feed.py] Loaded user subscriptions: {total_subs} subscriptions.")

    subscriptions_loaded = True  # Mark subscriptions as loaded

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
    print(f"[feed.py] Loaded {len(last_feed_links)} last feed links.")

def save_last_feed_link(link):
    """Append a new feed link to last_links_file and update the in-memory set."""
    global last_feed_links
    last_feed_links.add(link)
    try:
        with open(last_links_file, "a") as f:
            f.write(f"{link}\n")
        print(f"[feed.py] Saved new feed link: {link}")
    except Exception as e:
        print(f"Error saving to {last_links_file}: {e}")
