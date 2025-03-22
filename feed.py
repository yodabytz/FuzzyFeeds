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
feed_metadata = {}  # {feed_url: {"last_modified": str, "etag": str}}
channel_settings = {}
default_interval = 300
subscriptions_loaded = False
feeds_loaded = False

def load_feeds():
    global channels, channel_feeds, channel_intervals, last_check_times, feeds_loaded
    if feeds_loaded:
        logging.info("[feed.py] Feeds already loaded this cycle, skipping reload.")
        return
    channels_data = load_json(CHANNELS_FILE, default={"irc_channels": [], "discord_channels": [], "matrix_channels": []})
    channels = channels_data.get("irc_channels", []) + channels_data.get("discord_channels", []) + channels_data.get("matrix_channels", [])
    
    networks = load_json(NETWORKS_FILE, default={})
    for network_name, net_info in networks.items():
        net_channels = net_info.get("Channels", [])
        for chan in net_channels:
            composite_key = f"{net_info['server']}|{chan}"
            if composite_key not in channels:
                channels.append(composite_key)
        logging.info(f"[feed.py] Loaded {len(net_channels)} channels from network {network_name}")

    channel_feeds = load_json(FEEDS_FILE, default={})
    feed_metadata.update(load_json("feed_metadata.json", default={}))
    channel_settings.update(load_json("channel_settings.json", default={}))
    total_feeds = sum(len(feeds) for feeds in channel_feeds.values())
    logging.info(f"[feed.py] Loaded {len(channel_feeds)} channels with {total_feeds} feeds.")
    
    loaded_intervals = load_json("intervals.json", default={})
    for chan in channels:
        if chan not in loaded_intervals:
            channel_intervals[chan] = default_interval
        else:
            channel_intervals[chan] = loaded_intervals[chan]
        last_check_times[chan] = 0
    load_posted_links()
    feeds_loaded = True

def load_subscriptions():
    global subscriptions, subscriptions_loaded
    if subscriptions_loaded:
        logging.info("[feed.py] Subscriptions already loaded, skipping reload.")
        return
    subscriptions = load_json(SUBSCRIPTIONS_FILE, default={})
    subscriptions_loaded = True
    logging.info(f"[feed.py] Loaded user subscriptions: {sum(len(subs) for subs in subscriptions.values())} subscriptions.")

def save_feeds():
    global feeds_loaded
    save_json(FEEDS_FILE, channel_feeds)
    save_json("feed_metadata.json", feed_metadata)
    feeds_loaded = False  # Reset after save to allow reload

def save_subscriptions():
    save_json(SUBSCRIPTIONS_FILE, subscriptions)

def load_posted_links():
    global posted_links
    data = load_json(POSTED_LINKS_FILE, default={})
    posted_links = {chan: set(links) for chan, links in data.items()}

def save_posted_links():
    save_json(POSTED_LINKS_FILE, posted_links)

def save_channel_settings():
    save_json("channel_settings.json", channel_settings)

def is_link_posted(channel, link):
    if channel not in posted_links:
        posted_links[channel] = set()
    return link in posted_links[channel]

def mark_link_posted(channel, link):
    if channel not in posted_links:
        posted_links[channel] = set()
    posted_links[channel].add(link)
    if len(posted_links[channel]) > 1000:
        posted_links[channel] = set(list(posted_links[channel])[-1000:])
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

load_subscriptions()
