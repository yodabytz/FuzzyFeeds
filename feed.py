import feedparser
import requests
import time
import logging
import json
from persistence import load_json, save_json

# Optional: keep this line. It won't hurt, although the main fix
# is using requests before parsing the raw text.
feedparser.USER_AGENT = "FuzzyFeedsBot/1.0 (+https://github.com/YourUser/YourRepo)"

FEEDS_FILE = "feeds.json"
SUBSCRIPTIONS_FILE = "subscriptions.json"
CHANNELS_FILE = "channels.json"
NETWORKS_FILE = "networks.json"
POSTED_LINKS_FILE = "posted_links.json"

channels = []
channel_feeds = {}
channel_intervals = {}
last_check_times = {}
# Added initialization for subscription last-check records:
last_check_subs = {}
subscriptions = {}
posted_links = {}
default_interval = 300

def parse_with_custom_user_agent(url):
    headers = {
        "User-Agent": "FuzzyFeedsBot/1.0 (+https://github.com/YourUser/YourRepo)"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
    except Exception as e:
        logging.error(f"[feed.py] Error making HTTP request to {url}: {e}")
        return feedparser.FeedParserDict()

    if resp.status_code != 200:
        logging.error(f"[feed.py] Feed returned {resp.status_code}: {resp.text[:200]}")
        return feedparser.FeedParserDict()

    # Strip any leading whitespace or BOM from the response text
    text = resp.text.lstrip()
    return feedparser.parse(text)

def load_feeds():
    global channels, channel_feeds, channel_intervals, last_check_times
    channels_data = load_json(CHANNELS_FILE, default={"irc_channels": [], "discord_channels": [], "matrix_rooms": []})
    channels = channels_data.get("irc_channels", []) + channels_data.get("discord_channels", []) + channels_data.get("matrix_rooms", [])

    networks = load_json(NETWORKS_FILE, default={})
    for network_name, net_info in networks.items():
        net_channels = net_info.get("Channels", [])
        for chan in net_channels:
            composite_key = f"{net_info['server']}|{chan}"
            if composite_key not in channels:
                channels.append(composite_key)

    global channel_feeds
    channel_feeds = load_json(FEEDS_FILE, default={})
    migrate_plain_keys_to_composite()

    loaded_intervals = load_json("intervals.json", default={})
    for chan in channels:
        channel_intervals[chan] = loaded_intervals.get(chan, default_interval)
        last_check_times[chan] = 0

    load_posted_links()

def migrate_plain_keys_to_composite():
    networks = load_json(NETWORKS_FILE, default={})
    feeds_changed = False
    for net_info in networks.values():
        server_name = net_info.get("server")
        for chan in net_info.get("Channels", []):
            if chan in channel_feeds:
                composite_key = f"{server_name}|{chan}"
                if composite_key not in channel_feeds:
                    channel_feeds[composite_key] = channel_feeds[chan]
                else:
                    channel_feeds[composite_key].update(channel_feeds[chan])
                del channel_feeds[chan]
                feeds_changed = True
    if feeds_changed:
        save_json(FEEDS_FILE, channel_feeds)

    global posted_links
    posted_links = load_json(POSTED_LINKS_FILE, default={})
    links_changed = False
    for net_info in networks.values():
        server_name = net_info.get("server")
        for chan in net_info.get("Channels", []):
            if chan in posted_links:
                composite_key = f"{server_name}|{chan}"
                if composite_key not in posted_links:
                    posted_links[composite_key] = posted_links[chan]
                else:
                    posted_links[composite_key] = list(set(posted_links[composite_key] + posted_links[chan]))
                del posted_links[chan]
                links_changed = True
    if links_changed:
        save_json(POSTED_LINKS_FILE, posted_links)

def normalize_sub_key(key):
    return key.strip().lower()

def load_subscriptions():
    global subscriptions
    raw_subs = load_json(SUBSCRIPTIONS_FILE, default={})
    normalized = {}
    for user, subdict in raw_subs.items():
        normalized[user.lower()] = {normalize_sub_key(k): v for k, v in subdict.items()}
    subscriptions = normalized

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
        d = parse_with_custom_user_agent(url)
        if d.entries:
            entry = d.entries[0]
            title = entry.title.strip() if entry.get("title") else "No Title"
            link = entry.link.strip() if entry.get("link") else ""
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
                    d = parse_with_custom_user_agent(feed_url)
                    if d.entries:
                        entry = d.entries[0]
                        published_time = None
                        if entry.get("published_parsed"):
                            published_time = time.mktime(entry.published_parsed)
                        elif entry.get("updated_parsed"):
                            published_time = time.mktime(entry.updated_parsed)
                        if published_time is not None and published_time <= last_check_times.get(chan, 0):
                            continue
                        title = entry.title.strip() if entry.get("title") else "No Title"
                        link = entry.link.strip() if entry.get("link") else ""
                        if link and not is_link_posted(chan, link):
                            combined_message = f"New Feed from {feed_name}: {title}\nLink: {link}"
                            send_message_func(chan, combined_message)
                            mark_link_posted(chan, link)
                last_check_times[chan] = current_time
    except Exception as e:
        logging.error(f"Error in check_feeds: {e}")

# Automatically load feeds & subscriptions on import
load_feeds()
load_subscriptions()

