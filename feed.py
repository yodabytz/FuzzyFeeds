import feedparser
import time
import logging
import json
import ssl
from persistence import load_json, save_json

FEEDS_FILE = "feeds.json"
SUBSCRIPTIONS_FILE = "subscriptions.json"
CHANNELS_FILE = "channels.json"
NETWORKS_FILE = "networks.json"
POSTED_LINKS_FILE = "posted_links.json"

channels = []
channel_feeds = {}
channel_intervals = {}
last_check_times = {}
subscriptions = {}
posted_links = {}
default_interval = 300

def load_feeds():
    global channels, channel_feeds, channel_intervals, last_check_times
    channels_data = load_json(CHANNELS_FILE, default={"irc_channels": [], "discord_channels": [], "matrix_rooms": []})
    channels = channels_data.get("irc_channels", []) + channels_data.get("discord_channels", []) + channels_data.get("matrix_rooms", [])
    
    networks = load_json(NETWORKS_FILE, default={})
    # For each network, add composite keys to channels list.
    for network_name, net_info in networks.items():
        net_channels = net_info.get("Channels", [])
        for chan in net_channels:
            composite_key = f"{net_info['server']}|{chan}"
            if composite_key not in channels:
                channels.append(composite_key)
        logging.info(f"[feed.py] Loaded {len(net_channels)} channels from network {network_name}")

    channel_feeds = load_json(FEEDS_FILE, default={})
    total_feeds = sum(len(feeds) for feeds in channel_feeds.values())
    logging.info(f"[feed.py] Loaded {len(channel_feeds)} channels with {total_feeds} feeds.")
    
    # --- Migration Step ---
    # For IRC channels defined in secondary networks, if a plain channel key exists (e.g. "#buzzard")
    # merge its feeds into the composite key (e.g. "irc.collectiveirc.net|#buzzard") and remove the plain key.
    networks = load_json(NETWORKS_FILE, default={})
    migrated_keys = []
    for net_name, net_info in networks.items():
        server_name = net_info.get("server")
        for chan in net_info.get("Channels", []):
            if chan in channel_feeds:
                composite_key = f"{server_name}|{chan}"
                if composite_key not in channel_feeds:
                    channel_feeds[composite_key] = channel_feeds[chan]
                else:
                    # Merge the feeds from the plain key into the composite key.
                    channel_feeds[composite_key].update(channel_feeds[chan])
                migrated_keys.append(chan)
    for key in migrated_keys:
        if key in channel_feeds:
            del channel_feeds[key]
    if migrated_keys:
        save_feeds()
        logging.info(f"[feed.py] Migrated plain channel keys to composite keys: {migrated_keys}")
    # --- End Migration ---

    loaded_intervals = load_json("intervals.json", default={})
    for chan in channels:
        if chan not in loaded_intervals:
            channel_intervals[chan] = default_interval
        else:
            channel_intervals[chan] = loaded_intervals[chan]
        last_check_times[chan] = 0
    load_posted_links()

def load_subscriptions():
    global subscriptions
    subscriptions = load_json(SUBSCRIPTIONS_FILE, default={})
    logging.info(f"[feed.py] Loaded user subscriptions: {sum(len(subs) for subs in subscriptions.values())} subscriptions.")

def save_feeds():
    save_json(FEEDS_FILE, channel_feeds)

def save_subscriptions():
    save_json(SUBSCRIPTIONS_FILE, subscriptions)

def load_posted_links():
    global posted_links
    posted_links = load_json(POSTED_LINKS_FILE, default={})

def save_posted_links():
    save_json(POSTED_LINKS_FILE, posted_links)

def is_link_posted(channel, link):
    if channel not in posted_links:
        posted_links[channel] = []
    return link in posted_links[channel]

def mark_link_posted(channel, link):
    if channel not in posted_links:
        posted_links[channel] = []
    posted_links[channel].append(link)
    save_posted_links()

def fetch_latest_article(url):
    try:
        d = feedparser.parse(url)
        if d.entries:
            entry = d.entries[0]
            title = entry.title.strip() if entry.title else "No Title"
            link = entry.link.strip() if entry.link else ""
            return title, link
        return None, None
    except Exception as e:
        logging.error(f"[feed.py] Error fetching feed {url}: {e}")
        return None, None

def check_feeds(send_message_func, channels_to_check=None):
    try:
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
                        if link and not is_link_posted(chan, link):
                            send_message_func(chan, f"New Feed from {feed_name}: {title}")
                            send_message_func(chan, f"Link: {link}")
                            mark_link_posted(chan, link)
                last_check_times[chan] = current_time
    except Exception as e:
        logging.error(f"Error in check_feeds: {e}")

load_feeds()
load_subscriptions()
